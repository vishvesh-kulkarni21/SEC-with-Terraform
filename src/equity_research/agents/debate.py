"""Multi-agent pipeline: researcher -> bull and bear -> critic -> revise or remove.

1. The researcher gathers an evidence brief with tools (every item gets an evidence id).
2. Bull and bear each argue one side using ONLY that evidence: they get no tools, so
   they cannot introduce numbers the researcher did not fetch.
3. The critic verifies every claim. Failed claims go back to their author with the
   critic's reasons; after `max_revisions` rounds anything still failing is removed.
"""

import json
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from equity_research.agents.claims import CLAIMS_SCHEMA, Claim
from equity_research.agents.critic import Critic, Verdict
from equity_research.agents.evidence import EvidenceLedger
from equity_research.agents.planting import PlantRecord, plant_error
from equity_research.agents.researcher import RESEARCHER_PROMPT, AgentRun, run_agent
from equity_research.agents.tools import ResearchTools
from equity_research.llm.base import ChatModel, Message, Usage
from equity_research.tracing import current_conversation_id, log_event, timed

BRIEF_TASK = """Company: {ticker}
The latest reported fiscal year is FY{latest}. Analyse FY{latest} versus FY{prior}. Use exactly these fiscal years; do not rely on your own sense of the current date.
Build an evidence brief for an investment debate. Gather, for FY{latest} and FY{prior}:
- revenue and net income, revenue growth, gross/operating/net margins
- free cash flow, current ratio, debt to equity, return on equity
- one DCF valuation with assumptions you justify
- from the 10-K: main growth drivers, main risks, and segment performance
Then summarise the evidence in a short brief, citing evidence ids."""

SIDE_PROMPT = """You are the {side} analyst in an equity research debate about {ticker}.
Argue the {stance} case in 4 to 6 claims, using ONLY the evidence provided. You have no tools.

Rules:
- Each claim is one sentence.
- Copy every number exactly as its evidence "display" shows it (no quotation marks), and list it in "figures" with its evidence_id.
- Only draw conclusions the evidence supports. There is no market price, so do not call the stock cheap or expensive; a DCF value can be reported, not judged against a price.
- Every number in the text must appear in "figures". Do not compute, round, or rescale numbers.
- Qualitative statements must cite the supporting passage ids in "evidence_ids".
- State fiscal years explicitly (e.g. "in fiscal 2025"). Only use figures from the two fiscal years in the brief.
Return JSON only."""

STANCE = {"bull": "long (buy)", "bear": "short (sell)"}


@dataclass
class SideResult:
    side: str
    claims: list[Claim] = field(default_factory=list)  # verified, kept
    removed: list[Verdict] = field(default_factory=list)  # still failing after revisions
    rounds: list[list[Verdict]] = field(default_factory=list)  # critic verdicts per round
    plant: PlantRecord | None = None

    @property
    def plant_caught(self) -> bool | None:
        """Did the critic fail the planted claim in the round it was planted?"""
        if self.plant is None:
            return None
        return not self.rounds[0][self.plant.claim_index].passed


@dataclass
class DebateReport:
    ticker: str
    conversation_id: str | None
    brief: AgentRun
    sides: dict[str, SideResult]
    ledger: EvidenceLedger
    usage: Usage
    seconds: float


def _evidence_context(ledger: EvidenceLedger, passage_chars: int = 1500) -> str:
    lines = []
    for ev in ledger.items.values():
        if ev.kind == "passage":
            lines.append(f"[{ev.evidence_id}] passage from {ev.source}:\n{(ev.text or '')[:passage_chars]}")
        else:
            lines.append(f"[{ev.evidence_id}] {ev.label}: display=\"{ev.display}\"")
    return "\n\n".join(lines)


def _parse_claims(text: str) -> list[Claim]:
    try:
        return [Claim.from_dict(c) for c in json.loads(text)["claims"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _feedback(verdicts: list[Verdict]) -> str:
    failed = [{"index": i, "claim": v.claim.text, "problems": [f"{x.code}: {x.detail}" for x in v.issues]}
              for i, v in enumerate(verdicts) if not v.passed]
    return ("The critic rejected these claims:\n" + json.dumps(failed, indent=1) +
            "\n\nReturn the full revised list of claims. Fix each rejected claim using the evidence, "
            "or drop it if the evidence cannot support it. Keep the claims that passed unchanged.")


def argue_side(model: ChatModel, critic: Critic, side: str, ticker: str, brief: str,
               ledger: EvidenceLedger, max_revisions: int = 2,
               plant: Callable[[list[Claim]], PlantRecord | None] | None = None) -> tuple[SideResult, Usage]:
    system = SIDE_PROMPT.format(side=side, ticker=ticker, stance=STANCE[side])
    messages = [Message("user", f"Research brief:\n{brief}\n\nEvidence:\n{_evidence_context(ledger)}")]
    result, usage = SideResult(side), Usage()

    for round_no in range(max_revisions + 1):
        with timed("llm_call", agent=side, round=round_no, model=model.model_id) as ev:
            response = model.generate(system, messages, [], temperature=0.0, json_schema=CLAIMS_SCHEMA)
            ev.update(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
        usage = usage + response.usage
        claims = _parse_claims(response.message.text)

        if round_no == 0 and plant is not None:
            result.plant = plant(claims)
            if result.plant:
                log_event("planted_error", agent=side, **result.plant.__dict__)

        # The author sees its draft as the critic saw it (including any planted error).
        messages.append(Message("assistant", json.dumps({"claims": [c.to_dict() for c in claims]})))
        verdicts = critic.review(claims, ledger, side)
        result.rounds.append(verdicts)
        if all(v.passed for v in verdicts):
            break
        if round_no < max_revisions:
            messages.append(Message("user", _feedback(verdicts)))

    result.claims = [v.claim for v in result.rounds[-1] if v.passed]
    result.removed = [v for v in result.rounds[-1] if not v.passed]
    log_event("side_end", agent=side, rounds=len(result.rounds), kept=len(result.claims),
              removed=len(result.removed), plant_caught=result.plant_caught)
    return result, usage


def run_debate(ticker: str, tools: ResearchTools, researcher_model: ChatModel, author_model: ChatModel,
               critic_model: ChatModel | None, max_revisions: int = 2, plant_kind: str | None = None,
               seed: int = 0, researcher_steps: int = 15) -> DebateReport:
    start = time.perf_counter()
    ticker = ticker.upper()
    log_event("debate_start", ticker=ticker, plant_kind=plant_kind)

    # Pinned in code, not left to the model: a model's sense of "latest" comes from its
    # training data and can be years stale.
    latest = tools.fin(ticker).latest_year
    brief = run_agent(researcher_model, tools, RESEARCHER_PROMPT,
                      BRIEF_TASK.format(ticker=ticker, latest=latest, prior=latest - 1),
                      "researcher", max_steps=researcher_steps)
    critic = Critic(critic_model, allowed_years={latest, latest - 1})
    rng = random.Random(seed)
    sides, usage = {}, brief.usage
    for side in ("bull", "bear"):
        plant = (lambda claims: plant_error(claims, plant_kind, rng)) if plant_kind else None
        sides[side], side_usage = argue_side(author_model, critic, side, ticker, brief.answer,
                                             tools.ledger, max_revisions, plant)
        usage = usage + side_usage
    usage = usage + critic.usage

    report = DebateReport(ticker, current_conversation_id(), brief, sides, tools.ledger, usage,
                          round(time.perf_counter() - start, 3))
    log_event("debate_end", ticker=ticker, seconds=report.seconds, input_tokens=usage.input_tokens,
              output_tokens=usage.output_tokens,
              kept={s: len(r.claims) for s, r in sides.items()},
              removed={s: len(r.removed) for s, r in sides.items()},
              plant_caught={s: r.plant_caught for s, r in sides.items()})
    return report


def render_markdown(report: DebateReport) -> str:
    out = [f"# {report.ticker}: bull vs bear (verified)", ""]
    cited: list[str] = []
    for side, result in report.sides.items():
        out.append(f"## {side.title()} case")
        for c in result.claims:
            ids = [f.evidence_id for f in c.figures] + c.evidence_ids
            cited = list(dict.fromkeys(cited + ids))
            out.append(f"- {c.text} " + " ".join(f"[{i}]" for i in dict.fromkeys(ids)))
        if result.removed:
            out.append(f"\n_{len(result.removed)} claim(s) removed by the critic after "
                       f"{len(result.rounds) - 1} revision round(s)._")
        out.append("")
    out.append("## Sources")
    for eid in cited:
        ev = report.ledger.get(eid)
        if ev:
            out.append(f"- [{eid}] {ev.label}: {ev.display if ev.kind != 'passage' else ''} {ev.source}")
    out.append(f"\n_conversation_id: {report.conversation_id}_")
    return "\n".join(out)
