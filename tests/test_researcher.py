import json
from datetime import date

from equity_research.agents.researcher import research
from equity_research.agents.tools import ResearchTools
from equity_research.data.financials import CompanyFinancials
from equity_research.tracing import conversation
from tests.fakes import ScriptedChatModel


def companyfacts():
    def dur(val, fy):
        return {"val": val, "start": f"{fy - 1}-09-29", "end": f"{fy}-09-27", "filed": "2025-10-31",
                "accn": "0000320193-25-000079", "form": "10-K", "fy": 2025, "fp": "FY"}
    return {"entityName": "Apple Inc.", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [dur(391_035e6, 2024), dur(416_161e6, 2025)]}},
        "NetIncomeLoss": {"units": {"USD": [dur(93_736e6, 2024), dur(112_010e6, 2025)]}},
    }}}


def make_tools():
    return ResearchTools(lambda t: CompanyFinancials(t, companyfacts()), retriever=None, strategy="table")


def test_loop_executes_tools_and_returns_answer(tmp_path):
    model = ScriptedChatModel([
        [("list_metrics", {"ticker": "AAPL"})],
        [("get_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025}),
         ("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2025})],
        "Revenue was $416,161 million [F1]; net margin 26.92% [C1].",
    ])
    tools = make_tools()
    with conversation(run_dir=tmp_path, echo=False) as cid:
        run = research(model, tools, "AAPL", "Revenue and net margin in 2025?")

    assert run.answer.startswith("Revenue was $416,161 million [F1]")
    assert run.steps == 3 and run.stopped_reason == "answered"
    assert [c["tool"] for c in run.tool_calls] == ["list_metrics", "get_fact", "margin"]
    assert run.ledger.get("F1").display == "$416,161 million"
    assert run.ledger.get("C1").display == "26.92%"
    assert run.usage.input_tokens == 300

    # search_filing is hidden when no retriever is configured
    assert "search_filing" not in model.calls[0]["tools"]

    # the tool result the model saw on step 3 carries the evidence id and display string
    tool_msg = model.calls[2]["messages"][-1]
    assert tool_msg.tool_results[0].content["evidence_id"] == "F1"

    events = [json.loads(l) for l in (tmp_path / f"{cid}.jsonl").read_text().splitlines()]
    assert {e["conversation_id"] for e in events} == {cid}
    assert [e["event"] for e in events].count("tool_call") == 3
    assert events[0]["event"] == "agent_start" and events[-1]["event"] == "agent_end"


def test_tool_errors_are_returned_to_the_model():
    model = ScriptedChatModel([
        [("get_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2019})],
        "Revenue for FY2019 is not available.",
    ])
    with conversation(echo=False):
        run = research(model, make_tools(), "AAPL", "Revenue in 2019?")
    err = model.calls[1]["messages"][-1].tool_results[0].content["error"]
    assert "FY2019" in err and "2024" in err  # says which years exist
    assert run.tool_calls[0]["error"]


def test_step_budget_stops_runaway_loops():
    model = ScriptedChatModel([[("list_metrics", {"ticker": "AAPL"})]] * 3)
    with conversation(echo=False):
        run = research(model, make_tools(), "AAPL", "?", max_steps=3)
    assert run.stopped_reason == "max_steps" and run.steps == 3


def test_same_fact_keeps_same_id():
    tools = make_tools()
    a = tools.call("get_fact", {"ticker": "aapl", "metric": "revenue", "fiscal_year": 2025})
    b = tools.call("get_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025})
    assert a["evidence_id"] == b["evidence_id"] == "F1"
