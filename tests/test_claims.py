import random
from datetime import date

import pytest

from equity_research.agents.claims import Claim, Figure, check_claim, numbers_in_text, parse_figure
from equity_research.agents.evidence import EvidenceLedger
from equity_research.agents.planting import plant_error
from equity_research.data.xbrl import Fact
from equity_research.retrieval.chunkers import Chunk
from equity_research.retrieval.index import Passage
from equity_research.tools.calculators import margin


def fact(metric, fy, value):
    return Fact(metric, fy, value, "USD", "Tag", None, date(fy, 9, 27), "accn", "10-K", date(2025, 10, 31))


@pytest.fixture
def ledger():
    led = EvidenceLedger()
    led.add_fact("AAPL", fact("revenue", 2025, 416_161e6))                                  # F1
    led.add_calculation("AAPL", margin(fact("net_income", 2025, 112_010e6),
                                       fact("revenue", 2025, 416_161e6)))                   # C1 26.92%
    led.add_passage(Passage(Chunk("table-0001", "table", "Services net sales were $109,158 and grew 14% "
                                  "due to advertising and cloud services.", "Item 7", "table"),
                            0.9, "AAPL", "accn", "2025-09-27", "url"))                      # P1
    return led


@pytest.mark.parametrize("display,value,kind", [
    ("$416,161 million", 416_161e6, "USD"),
    ("$1.2 billion", 1.2e9, "USD"),
    ("26.92%", 0.2692, "ratio"),
    ("(4)%", -0.04, "ratio"),
    ("$6.08 per share", 6.08, "USD/shares"),
])
def test_parse_figure(display, value, kind):
    v, _, k = parse_figure(display)
    assert v == pytest.approx(value) and k == kind


def test_numbers_in_text_skips_years_and_items():
    assert numbers_in_text("In fiscal 2025 (Item 7 of the 10-K) revenue was $416,161 million, up 6.43%.") \
        == ["$416,161 million", "6.43%"]


def test_correct_claim_passes(ledger):
    c = Claim("Revenue reached $416,161 million in fiscal 2025 with a 26.92% net margin.",
              [Figure("$416,161 million", "F1"), Figure("26.92%", "C1")])
    assert check_claim(c, ledger) == []


def test_rounding_within_display_precision_passes(ledger):
    c = Claim("Net margin was 26.9% in fiscal 2025.", [Figure("26.9%", "C1")])
    assert check_claim(c, ledger) == []


@pytest.mark.parametrize("text,figure,code", [
    ("Revenue reached $445,292 million in fiscal 2025.", Figure("$445,292 million", "F1"), "value_mismatch"),
    ("Revenue reached $416,161 billion in fiscal 2025.", Figure("$416,161 billion", "F1"), "value_mismatch"),
    ("Revenue reached $416,161 million in fiscal 2024.", Figure("$416,161 million", "F1"), "wrong_period"),
    ("Revenue reached $416,161 million.", Figure("$416,161 million", "F9"), "no_evidence"),
])
def test_bad_figures_fail(ledger, text, figure, code):
    assert code in [i.code for i in check_claim(Claim(text, [figure]), ledger)]


def test_undeclared_number_fails(ledger):
    c = Claim("Revenue reached $416,161 million, and margins hit 30%.", [Figure("$416,161 million", "F1")])
    assert [i.code for i in check_claim(c, ledger)] == ["undeclared_number"]


def test_figure_from_passage_must_appear_in_it(ledger):
    ok = Claim("Services grew 14% in fiscal 2025.", [Figure("14%", "P1")], ["P1"])
    bad = Claim("Services grew 41% in fiscal 2025.", [Figure("41%", "P1")], ["P1"])
    assert check_claim(ok, ledger) == []
    assert [i.code for i in check_claim(bad, ledger)] == ["not_in_passage"]


@pytest.mark.parametrize("kind", ["perturb", "scale", "period"])
def test_every_planted_error_kind_is_caught(ledger, kind):
    claims = [Claim("Revenue reached $416,161 million in fiscal 2025.", [Figure("$416,161 million", "F1")])]
    assert check_claim(claims[0], ledger) == []
    record = plant_error(claims, kind, random.Random(0))
    assert record is not None and record.planted != record.original
    assert check_claim(claims[0], ledger) != []


def test_llm_layer_rejects_unsupported_and_missing_verdicts(ledger):
    from equity_research.agents.critic import Critic
    from tests.fakes import ScriptedChatModel

    claims = [
        Claim("Services growth was driven by advertising.", [], ["P1"]),
        Claim("Services growth was driven by the Vision Pro.", [], ["P1"]),
        Claim("Services growth benefited from cloud services.", [], ["P1"]),
    ]
    critic = Critic(ScriptedChatModel([{"verdicts": [
        {"index": 0, "supported": True, "reason": "stated"},
        {"index": 1, "supported": False, "reason": "passage never mentions Vision Pro"},
        # no verdict for index 2
    ]}]))
    verdicts = critic.review(claims, ledger, "bull")
    assert [v.passed for v in verdicts] == [True, False, False]
    assert verdicts[1].issues[0].code == "unsupported"


def test_stale_period_fails_when_years_are_pinned(ledger):
    ledger.add_fact("AAPL", fact("revenue", 2022, 394_328e6))  # F2
    c = Claim("Revenue was $394,328 million in fiscal 2022.", [Figure("$394,328 million", "F2")])
    assert check_claim(c, ledger) == []
    assert [i.code for i in check_claim(c, ledger, allowed_years={2024, 2025})] == ["stale_period"]
