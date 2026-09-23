"""SEC endpoints: ticker -> CIK lookup, XBRL companyfacts, and 10-K filing documents."""

from dataclasses import dataclass
from datetime import date

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


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"


@dataclass(frozen=True)
class Filing:
    ticker: str
    cik: int
    form: str
    accn: str
    report_date: date  # period end the filing covers
    filed: date
    url: str


def ten_k_filings(client: EdgarClient, ticker: str) -> list[Filing]:
    """Original 10-K filings (no amendments), newest first, from the submissions API.

    Only the "recent" block is read; it covers roughly the last 1,000 filings, which
    reaches well past five years of 10-Ks for large companies.
    """
    cik = cik_for_ticker(client, ticker)
    recent = client.get_json(SUBMISSIONS_URL.format(cik=cik))["filings"]["recent"]
    filings = []
    for form, accn, doc, report, filed in zip(
        recent["form"], recent["accessionNumber"], recent["primaryDocument"],
        recent["reportDate"], recent["filingDate"],
    ):
        if form != "10-K":
            continue
        filings.append(Filing(
            ticker=ticker.upper(), cik=cik, form=form, accn=accn,
            report_date=date.fromisoformat(report), filed=date.fromisoformat(filed),
            url=ARCHIVE_URL.format(cik=cik, accn=accn.replace("-", ""), doc=doc),
        ))
    return sorted(filings, key=lambda f: f.filed, reverse=True)


def ten_k_html(client: EdgarClient, filing: Filing) -> str:
    return client.get_text(filing.url)
