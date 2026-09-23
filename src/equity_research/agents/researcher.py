"""Researcher agent: a ReAct loop over the research tools.

Reason -> act (tool call) -> observe (tool result) -> repeat, until the model answers
without calling a tool or the step budget runs out. Uses native function calling
rather than parsing "Action:" lines from text, which is more reliable, and our own
loop executes every call so each one is traced.
"""

import json
import time
from dataclasses import dataclass, field

from equity_research.agents.evidence import EvidenceLedger
from equity_research.agents.tools import ResearchTools
from equity_research.llm.base import ChatModel, Message, ToolResult, Usage
from equity_research.tracing import log_event, timed

RESEARCHER_PROMPT = """You are an equity research analyst. You answer questions about a public company using ONLY the tools provided.

Rules:
1. Never do arithmetic yourself. Every figure, growth rate, margin, ratio or valuation must come from a tool result. If you need a number no tool gives you, say it is not available.
2. Quote numbers exactly as the tool's "display" field shows them, and cite the evidence_id in square brackets right after, e.g. "revenue was $391,035 million [F1]".
3. Qualitative statements from the 10-K must cite the passage id, e.g. [P2].
4. Reported figures come from get_fact and the calculators. Use search_filing for context and explanations.
5. Be precise about fiscal years. Call list_metrics first to learn which fiscal years exist.
6. If a tool returns an error, adjust (different year, different metric) or state the limitation. Never guess.
7. When done, write a concise final answer. Do not call more tools after you have what you need."""


@dataclass
class AgentRun:
    """Outcome of one agent loop."""

    answer: str
    ledger: EvidenceLedger
    steps: int
    tool_calls: list[dict] = field(default_factory=list)
    usage: Usage = Usage()
    seconds: float = 0.0
    model_id: str = ""
    stopped_reason: str = "answered"  # or "max_steps"


def run_agent(model: ChatModel, tools: ResearchTools, system: str, task: str,
              agent_name: str, max_steps: int = 12, temperature: float = 0.0) -> AgentRun:
    """Generic tool-using loop, shared by the researcher and (later) the other agents."""
    messages = [Message("user", task)]
    run = AgentRun(answer="", ledger=tools.ledger, steps=0, model_id=model.model_id)
    start = time.perf_counter()
    log_event("agent_start", agent=agent_name, model=model.model_id, task=task)

    for step in range(1, max_steps + 1):
        run.steps = step
        with timed("llm_call", agent=agent_name, step=step, model=model.model_id) as ev:
            response = model.generate(system, messages, tools.specs, temperature)
            ev.update(input_tokens=response.usage.input_tokens,
                      output_tokens=response.usage.output_tokens,
                      tool_calls=[c.name for c in response.message.tool_calls],
                      text=response.message.text[:500])
        run.usage = run.usage + response.usage
        messages.append(response.message)

        if not response.message.tool_calls:
            run.answer = response.message.text
            break

        results = []
        for call in response.message.tool_calls:
            with timed("tool_call", agent=agent_name, step=step, tool=call.name, args=call.args) as ev:
                result = tools.call(call.name, call.args)
                ev.update(result=_summarise(result))
            run.tool_calls.append({"step": step, "tool": call.name, "args": call.args,
                                   "error": result.get("error")})
            results.append(ToolResult(call.call_id, call.name, result))
        messages.append(Message("tool", tool_results=results))
        if tools.submitted is not None:
            run.answer = json.dumps(tools.submitted)
            run.stopped_reason = "submitted"
            break
    else:
        run.stopped_reason = "max_steps"
        run.answer = messages[-2].text if len(messages) > 1 and messages[-2].role == "assistant" else ""

    run.seconds = round(time.perf_counter() - start, 3)
    log_event("agent_end", agent=agent_name, steps=run.steps, stopped_reason=run.stopped_reason,
              seconds=run.seconds, input_tokens=run.usage.input_tokens,
              output_tokens=run.usage.output_tokens, answer=run.answer[:2000])
    return run


def research(model: ChatModel, tools: ResearchTools, ticker: str, question: str,
             max_steps: int = 12) -> AgentRun:
    task = f"Company: {ticker.upper()}\nQuestion: {question}"
    return run_agent(model, tools, RESEARCHER_PROMPT, task, "researcher", max_steps)


def _summarise(result: dict) -> str:
    """Short form of a tool result for the trace (passages are long)."""
    if "passages" in result:
        return json.dumps([{"id": p["evidence_id"], "source": p["source"]} for p in result["passages"]])
    return json.dumps(result, default=str)[:600]
