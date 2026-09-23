import json

from equity_research.agents.debate import render_markdown, run_debate
from equity_research.agents.tools import ResearchTools
from equity_research.data.financials import CompanyFinancials
from equity_research.tracing import conversation
from tests.fakes import ScriptedChatModel
from tests.test_researcher import companyfacts

GOOD = {"claims": [
    {"text": "Revenue reached $416,161 million in fiscal 2025.",
     "figures": [{"display": "$416,161 million", "evidence_id": "F1"}], "evidence_ids": []},
    {"text": "Net margin was 26.92% in fiscal 2025.",
     "figures": [{"display": "26.92%", "evidence_id": "C1"}], "evidence_ids": []},
]}


def researcher_turns():
    return [
        [("get_fact", {"ticker": "AAPL", "metric": "revenue", "fiscal_year": 2025}),
         ("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2025})],
        "Brief: revenue $416,161 million [F1], net margin 26.92% [C1].",
    ]


def make_tools():
    return ResearchTools(lambda t: CompanyFinancials(t, companyfacts()), retriever=None, strategy="table")


def run(author_turns, plant_kind=None, max_revisions=2):
    researcher = ScriptedChatModel(researcher_turns())
    author = ScriptedChatModel(author_turns)
    with conversation(echo=False):
        report = run_debate("AAPL", make_tools(), researcher, author, critic_model=None,
                            max_revisions=max_revisions, plant_kind=plant_kind, seed=1)
    return report, author


def test_clean_drafts_pass_first_round():
    report, _ = run([GOOD, GOOD])
    for side in ("bull", "bear"):
        assert len(report.sides[side].rounds) == 1
        assert len(report.sides[side].claims) == 2 and not report.sides[side].removed


def test_planted_error_is_caught_and_sent_back_for_revision():
    # Round 0 is clean, then gets a planted error; the author's revision (round 1) is clean again.
    report, author = run([GOOD, GOOD, GOOD, GOOD], plant_kind="perturb")
    for side in ("bull", "bear"):
        r = report.sides[side]
        assert r.plant is not None
        assert r.plant_caught is True
        assert len(r.rounds) == 2  # sent back once
        assert all(v.passed for v in r.rounds[1])
    # the author was told exactly what failed
    feedback = author.calls[1]["messages"][-1].text
    assert "value_mismatch" in feedback and "rejected" in feedback


def test_claim_still_failing_after_revisions_is_removed():
    bad = {"claims": GOOD["claims"] + [
        {"text": "Revenue will reach $999,999 million in fiscal 2025.",
         "figures": [{"display": "$999,999 million", "evidence_id": "F1"}], "evidence_ids": []}]}
    report, _ = run([bad, bad, bad, GOOD], max_revisions=2)
    bull = report.sides["bull"]
    assert len(bull.rounds) == 3
    assert len(bull.claims) == 2 and len(bull.removed) == 1
    md = render_markdown(report)
    assert "999,999" not in md and "$416,161 million" in md and "removed by the critic" in md



def test_researcher_task_pins_latest_fiscal_year():
    researcher = ScriptedChatModel(researcher_turns())
    with conversation(echo=False):
        run_debate("AAPL", make_tools(), researcher, ScriptedChatModel([GOOD, GOOD]), critic_model=None)
    task = researcher.calls[0]["messages"][0].text
    assert "FY2025 versus FY2024" in task
