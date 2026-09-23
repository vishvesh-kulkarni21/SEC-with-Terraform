"""CompanyFinancials against Apple's FY2024 10-K balance sheet, income and cash flow statements."""

import pytest

from equity_research.config import ConfigError, load_settings
from equity_research.data.edgar_client import EdgarClient
from equity_research.data.financials import CompanyFinancials
from equity_research.data.xbrl import MissingMetricError

pytestmark = pytest.mark.integration

AAPL_FY2024_MILLIONS = {
    "revenue": 391_035, "gross_profit": 180_683, "operating_income": 123_216,
    "net_income": 93_736, "operating_cash_flow": 118_254, "capex": 9_447,
    "total_assets": 364_980, "total_liabilities": 308_030,
    "current_assets": 152_987, "current_liabilities": 176_392,
    "cash": 29_943, "long_term_debt": 96_662, "shareholders_equity": 56_950,
}


@pytest.fixture(scope="module")
def client():
    try:
        settings = load_settings()
    except ConfigError as e:
        pytest.skip(str(e))
    return EdgarClient(settings.sec_user_agent, settings.cache_dir)


def test_apple_fy2024(client):
    aapl = CompanyFinancials.load(client, "AAPL")
    for metric, millions in AAPL_FY2024_MILLIONS.items():
        assert aapl.get(metric, 2024).value == millions * 1_000_000, metric
    assert aapl.get("eps_diluted", 2024).value == 6.08
    assert aapl.get("diluted_shares", 2024).unit == "shares"


def test_balance_sheet_dated_at_fiscal_year_end(client):
    aapl = CompanyFinancials.load(client, "AAPL")
    assert aapl.get("total_assets", 2024).end == aapl.get("revenue", 2024).end


def test_unreported_metric_says_so(client):
    # J&J has not reported operating income since 2014.
    jnj = CompanyFinancials.load(client, "JNJ")
    with pytest.raises(MissingMetricError, match="operating_income"):
        jnj.get("operating_income", 2024)
