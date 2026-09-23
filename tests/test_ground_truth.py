"""Phase 1 acceptance: five years x three tickers match the figures printed in the 10-Ks.

Values are USD millions as shown in each company's most recent 10-K income statement
(ground truth = as most recently reported). Uses the EDGAR cache, so only the first
run touches the network.
"""

import pytest

from equity_research.config import ConfigError, load_settings
from equity_research.data.edgar_client import EdgarClient
from equity_research.data.sec_api import company_facts
from equity_research.data.xbrl import annual_facts

pytestmark = pytest.mark.integration

EXPECTED = {
    "AAPL": {  # fiscal year ends late September
        "revenue":    {2021: 365_817, 2022: 394_328, 2023: 383_285, 2024: 391_035, 2025: 416_161},
        "net_income": {2021: 94_680, 2022: 99_803, 2023: 96_995, 2024: 93_736, 2025: 112_010},
    },
    "MSFT": {  # fiscal year ends June 30
        "revenue":    {2021: 168_088, 2022: 198_270, 2023: 211_915, 2024: 245_122, 2025: 281_724},
        "net_income": {2021: 61_271, 2022: 72_738, 2023: 72_361, 2024: 88_136, 2025: 101_832},
    },
    "JNJ": {  # 52/53-week year ending around Dec 31
        # 2021 and 2022 revenue are restated for the Kenvue separation (originally
        # filed as 93,775 and 94,943). Net income includes discontinued ops, so it is unchanged.
        "revenue":    {2021: 78_740, 2022: 79_990, 2023: 85_159, 2024: 88_821, 2025: 94_193},
        "net_income": {2021: 20_878, 2022: 17_941, 2023: 35_153, 2024: 14_066, 2025: 26_804},
    },
}


@pytest.fixture(scope="module")
def client():
    try:
        settings = load_settings()
    except ConfigError as e:
        pytest.skip(str(e))
    return EdgarClient(settings.sec_user_agent, settings.cache_dir)


@pytest.mark.parametrize("ticker", EXPECTED)
def test_matches_10k(client, ticker):
    cf = company_facts(client, ticker)
    for metric, by_year in EXPECTED[ticker].items():
        facts = annual_facts(cf, metric)
        for year, millions in by_year.items():
            assert facts[year].value == millions * 1_000_000, f"{ticker} {metric} FY{year}: {facts[year].source}"
