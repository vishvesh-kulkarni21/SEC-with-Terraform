"""All annual facts for one company, keyed by metric and fiscal year."""

from equity_research.data.edgar_client import EdgarClient
from equity_research.data.sec_api import company_facts
from equity_research.data.xbrl import METRICS, Fact, MissingMetricError, annual_facts


class CompanyFinancials:
    def __init__(self, ticker: str, companyfacts: dict):
        self.ticker = ticker.upper()
        self.name = companyfacts.get("entityName", self.ticker)
        self._facts: dict[str, dict[int, Fact]] = {}

        # Fiscal year ends come from revenue durations; balance sheet facts are
        # only accepted at those dates.
        revenue = annual_facts(companyfacts, "revenue")
        self._facts["revenue"] = revenue
        year_ends = {f.end for f in revenue.values()}

        for metric, spec in METRICS.items():
            if metric == "revenue":
                continue
            ends = year_ends if spec.period_type == "instant" else None
            try:
                self._facts[metric] = annual_facts(companyfacts, metric, period_ends=ends)
            except MissingMetricError:
                self._facts[metric] = {}

    @classmethod
    def load(cls, client: EdgarClient, ticker: str) -> "CompanyFinancials":
        return cls(ticker, company_facts(client, ticker))

    def get(self, metric: str, fiscal_year: int) -> Fact:
        if metric not in METRICS:
            raise KeyError(f"Unknown metric {metric!r}. Known: {sorted(METRICS)}")
        try:
            return self._facts[metric][fiscal_year]
        except KeyError:
            available = sorted(self._facts[metric])
            raise MissingMetricError(
                f"{self.ticker} has no {metric} for FY{fiscal_year}. Years available: {available or 'none'}"
            ) from None

    def years(self, metric: str = "revenue") -> list[int]:
        return sorted(self._facts[metric])

    @property
    def latest_year(self) -> int:
        return self.years()[-1]
