"""Calculator tests against figures from Apple's FY2024 and Microsoft's FY2024 10-Ks.

Expected ratios are the percentages the companies print in their own MD&A
(e.g. Apple: "Total gross margin percentage 46.2%"), so they are checked
against the filing, not against our own arithmetic.
"""

from datetime import date

import pytest

from equity_research.data.xbrl import Fact
from equity_research.tools.calculators import (
    CalculationError,
    cagr,
    current_ratio,
    dcf_value_per_share,
    debt_to_equity,
    free_cash_flow,
    margin,
    return_on_equity,
    yoy_growth,
)

M = 1_000_000


def fact(metric, fy, value, unit="USD"):
    end = date(fy, 9, 28)
    return Fact(metric=metric, fiscal_year=fy, value=value, unit=unit, tag="Tag", start=None,
                end=end, accn=f"accn-{fy}", form="10-K", filed=date(fy, 11, 1))


# Apple 10-K, fiscal 2024 (USD millions)
AAPL = {
    ("revenue", 2024): 391_035, ("revenue", 2023): 383_285,
    ("gross_profit", 2024): 180_683, ("gross_profit", 2023): 169_148,
    ("operating_income", 2024): 123_216,
    ("net_income", 2024): 93_736,
    ("current_assets", 2024): 152_987, ("current_liabilities", 2024): 176_392,
    ("shareholders_equity", 2024): 56_950, ("shareholders_equity", 2023): 62_146,
    ("long_term_debt", 2024): 96_662,
    ("operating_cash_flow", 2024): 118_254, ("capex", 2024): 9_447,
}


def aapl(metric, fy):
    return fact(metric, fy, AAPL[(metric, fy)] * M)


def test_gross_margin_matches_apple_mdna():
    assert margin(aapl("gross_profit", 2024), aapl("revenue", 2024)).value == pytest.approx(0.462, abs=5e-4)
    assert margin(aapl("gross_profit", 2023), aapl("revenue", 2023)).value == pytest.approx(0.441, abs=5e-4)


def test_revenue_growth_matches_apple_mdna():
    # Apple MD&A: total net sales increased 2% in 2024.
    g = yoy_growth(aapl("revenue", 2024), aapl("revenue", 2023))
    assert g.value == pytest.approx(0.02, abs=5e-3)
    assert g.unit == "ratio"


def test_microsoft_fy2024_growth_matches_mdna():
    # Microsoft MD&A: revenue increased 16%, operating income increased 24%.
    rev = yoy_growth(fact("revenue", 2024, 245_122 * M), fact("revenue", 2023, 211_915 * M))
    oi = yoy_growth(fact("operating_income", 2024, 109_433 * M), fact("operating_income", 2023, 88_523 * M))
    assert rev.value == pytest.approx(0.16, abs=5e-3)
    assert oi.value == pytest.approx(0.24, abs=5e-3)


def test_net_and_operating_margin():
    assert margin(aapl("net_income", 2024), aapl("revenue", 2024)).value == pytest.approx(93_736 / 391_035)
    assert margin(aapl("operating_income", 2024), aapl("revenue", 2024)).value == pytest.approx(0.3151, abs=1e-4)


def test_current_ratio_and_debt_to_equity():
    assert current_ratio(aapl("current_assets", 2024), aapl("current_liabilities", 2024)).value == pytest.approx(0.8673, abs=1e-4)
    assert debt_to_equity(aapl("long_term_debt", 2024), aapl("shareholders_equity", 2024)).value == pytest.approx(1.6973, abs=1e-4)


def test_roe_uses_average_equity():
    roe = return_on_equity(aapl("net_income", 2024), aapl("shareholders_equity", 2024), aapl("shareholders_equity", 2023))
    assert roe.value == pytest.approx(93_736 / 59_548)  # avg(56,950, 62,146) = 59,548


def test_free_cash_flow():
    fcf = free_cash_flow(aapl("operating_cash_flow", 2024), aapl("capex", 2024))
    assert fcf.value == 108_807 * M
    assert fcf.unit == "USD"


def test_cagr():
    c = cagr(fact("revenue", 2021, 100.0), fact("revenue", 2024, 133.1))
    assert c.value == pytest.approx(0.10)


def test_dcf_zero_growth_reduces_to_perpetuity():
    # With g = terminal g = 0, equity value = FCF / r + cash - debt = 100/0.1 + 50 - 30 = 1020.
    fcf = free_cash_flow(fact("operating_cash_flow", 2024, 110), fact("capex", 2024, 10))
    v = dcf_value_per_share(fcf, fact("cash", 2024, 50), fact("long_term_debt", 2024, 30),
                            fact("diluted_shares", 2024, 10, unit="shares"),
                            growth_rate=0.0, discount_rate=0.10, terminal_growth=0.0, years=5)
    assert v.value == pytest.approx(102.0)
    assert v.assumptions["discount_rate"] == 0.10


# --- guards: these are the error classes the critic must catch in agents --------

def test_rejects_mismatched_years():
    with pytest.raises(CalculationError, match="fiscal years"):
        margin(aapl("net_income", 2024), aapl("revenue", 2023))


def test_rejects_non_consecutive_growth():
    with pytest.raises(CalculationError, match="consecutive"):
        yoy_growth(fact("revenue", 2024, 1), fact("revenue", 2022, 1))


def test_rejects_wrong_line_item():
    with pytest.raises(CalculationError):
        margin(aapl("net_income", 2024), aapl("gross_profit", 2024))


def test_rejects_unit_mismatch():
    with pytest.raises(CalculationError, match="unit"):
        margin(fact("eps_diluted", 2024, 6.08, unit="USD/shares"), aapl("revenue", 2024))


def test_rejects_bad_dcf_rates():
    fcf = free_cash_flow(fact("operating_cash_flow", 2024, 110), fact("capex", 2024, 10))
    with pytest.raises(CalculationError):
        dcf_value_per_share(fcf, fact("cash", 2024, 1), fact("long_term_debt", 2024, 1),
                            fact("diluted_shares", 2024, 1, unit="shares"),
                            growth_rate=0.05, discount_rate=0.03, terminal_growth=0.03)


def test_calculation_traces_to_facts():
    fcf = free_cash_flow(aapl("operating_cash_flow", 2024), aapl("capex", 2024))
    v = dcf_value_per_share(fcf, fact("cash", 2024, 1), fact("long_term_debt", 2024, 1),
                            fact("diluted_shares", 2024, 1, unit="shares"),
                            growth_rate=0.05, discount_rate=0.09, terminal_growth=0.02)
    assert {f.metric for f in v.facts()} == {"operating_cash_flow", "capex", "cash", "long_term_debt", "diluted_shares"}
