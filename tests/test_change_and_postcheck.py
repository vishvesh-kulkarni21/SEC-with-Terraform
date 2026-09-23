import pytest

from equity_research.agents.researcher import research
from equity_research.tools.calculators import CalculationError, change, margin
from equity_research.tracing import conversation
from tests.fakes import ScriptedChatModel
from tests.test_calculators import M, aapl, fact
from tests.test_researcher import make_tools


def test_change_in_percentage_points():
    m25 = margin(fact("net_income", 2025, 112_010 * M), fact("revenue", 2025, 416_161 * M))
    m24 = margin(aapl("net_income", 2024), aapl("revenue", 2024))
    c = change(m25, m24)
    assert c.unit == "pp" and c.value == pytest.approx(0.2692 - 0.2397, abs=1e-4)
    with pytest.raises(CalculationError):
        change(m24, m25)  # earlier year passed first


def test_numbers_the_model_computed_are_flagged():
    model = ScriptedChatModel([
        [("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2025}),
         ("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2024})],
        "Net margin rose to 26.92% [C1] from 23.97% [C2], up 2.95 percentage points.",
    ])
    with conversation(echo=False):
        run = research(model, make_tools(), "AAPL", "net margin change?")
    assert run.unverified_numbers == ["2.95 percentage points"]


def test_change_tool_output_counts_as_verified():
    model = ScriptedChatModel([
        [("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2025}),
         ("margin", {"ticker": "AAPL", "metric": "net_income", "fiscal_year": 2024})],
        [("change", {"later_id": "C1", "earlier_id": "C2"})],
        # 26.916% - 23.971% = 2.945 -> +2.94; subtracting the rounded displays would give 2.95
        "Net margin rose to 26.92% [C1] from 23.97% [C2], +2.94 percentage points [C3].",
    ])
    with conversation(echo=False):
        run = research(model, make_tools(), "AAPL", "net margin change?")
    assert run.ledger.get("C3").display == "+2.94 percentage points"
    assert run.unverified_numbers == []
