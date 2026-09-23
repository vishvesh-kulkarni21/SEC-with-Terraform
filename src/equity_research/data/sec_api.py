"""SEC endpoints: ticker -> CIK lookup and XBRL companyfacts."""

from equity_research.data.edgar_client import EdgarClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


class UnknownTickerError(LookupError):
    pass


def cik_for_ticker(client: EdgarClient, ticker: str) -> int:
    """Look up a company's CIK from SEC's ticker list (cached like any other response)."""
    ticker = ticker.upper()
    for row in client.get_json(TICKERS_URL).values():
        if row["ticker"].upper() == ticker:
            return int(row["cik_str"])
    raise UnknownTickerError(f"Ticker {ticker!r} not found in SEC ticker list")


def company_facts(client: EdgarClient, ticker: str) -> dict:
    """Return the raw companyfacts JSON for a ticker."""
    return client.get_json(COMPANYFACTS_URL.format(cik=cik_for_ticker(client, ticker)))
