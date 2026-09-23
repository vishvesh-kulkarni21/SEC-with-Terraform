import pytest

from equity_research.data.financials import CompanyFinancials
from equity_research.evaluation.questions import build_questions
from equity_research.evaluation.report import debate_table, qa_table, taxonomy_table
from equity_research.evaluation.scoring import classify, retrieval_hit


def dur(val, fy, filed="2026-02-11", accn="new"):
    return {"val": val, "start": f"{fy}-01-01", "end": f"{fy}-12-31", "filed": filed, "accn": accn,
            "form": "10-K", "fy": 2025, "fp": "FY"}


@pytest.fixture
def fin():
    cf = {"entityName": "Johnson & Johnson", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            dur(93_775e6, 2021, "2022-02-17", "orig"),      # as originally filed
            dur(78_740e6, 2021, "2024-02-16", "restated"),  # restated (ground truth)
            dur(88_821e6, 2024), dur(94_193e6, 2025)]}},
        "NetIncomeLoss": {"units": {"USD": [dur(14_066e6, 2024), dur(26_804e6, 2025)]}},
    }}}
    return CompanyFinancials("JNJ", cf)


@pytest.fixture
def q_rev_2025(fin):
    return next(q for q in build_questions(fin, ["revenue"], years_back=2) if q.fiscal_year == 2025)


@pytest.mark.parametrize("answer,expected", [
    ("$94,193 million", "correct"),
    ("$94.2 billion", "correct"),             # rounding within display precision
    ("94,193", "wrong_scale"),                # unit dropped: read as $94,193
    ("$94,193 billion", "wrong_scale"),
    ("$88,821 million", "wrong_period"),      # prior-year column
    ("$26,804 million", "wrong_line_item"),   # net income, not revenue
    ("$51,000 million", "fabricated"),
    ("not found", "abstained"),
])
def test_taxonomy(fin, q_rev_2025, answer, expected):
    assert classify(answer, q_rev_2025, fin) == expected


def test_stale_restated(fin):
    q = next(q for q in build_questions(fin, ["revenue"], years_back=5) if q.fiscal_year == 2021)
    assert q.truth.value == 78_740e6
    assert classify("$93,775 million", q, fin) == "stale_restated"


def test_questions_skip_unreported_metrics(fin):
    qs = build_questions(fin, ["revenue", "operating_income"], years_back=2)
    assert {q.metric for q in qs} == {"revenue"}


def test_retrieval_hit_matches_filing_format(q_rev_2025):
    assert retrieval_hit(q_rev_2025, ["Total sales | 94,193 | 88,821"])
    assert not retrieval_hit(q_rev_2025, ["Total sales | 194,193"])


def test_report_tables_render():
    qa = [{"kind": "qa", "condition": "table", "outcome": o, "correct": o == "correct",
           "is_error": o not in ("correct", "abstained"), "supported": o == "correct",
           "critic_issues": [] if o == "correct" else ["not_in_passage"], "retrieval_hit": True,
           "seconds": 5.0, "cost_usd": 0.01} for o in ("correct", "correct", "wrong_period", "abstained")]
    t = qa_table(qa, ("condition",))
    assert "| table | 4 | 50.0% | 33.3%" in t
    assert "wrong_period" in taxonomy_table(qa, ("condition",))
    debate = [{"kind": "debate", "plant_kind": "scale", "seconds": 90, "cost_usd": 0.05, "sides": {
        "bull": {"plant_caught": True, "caught_by_code": ["value_mismatch"], "draft_claims": 6,
                 "draft_failed": 1, "draft_unsupported": 0, "kept": 6, "removed": 0}}}]
    assert "| scale | 1 | 1 | 100.0%" in debate_table(debate)
