"""Extract annual ground-truth facts from EDGAR companyfacts JSON.

Rules (see docs/DECISIONS.md):
- Annual = a 10-K / 10-K/A duration fact spanning 350-380 days (covers 52/53-week years).
  The `fy` field is never used to pick periods: it is the fiscal year of the *filing*,
  so a 10-K for FY2024 labels its FY2022 comparative as fy=2024 too.
- Ground truth = as most recently reported: for each period end date, the fact from the
  latest-filed 10-K wins, whichever tag in the metric's list it uses. Tag priority only
  breaks ties between facts in the same filing.
- Only USD facts are used for monetary metrics; the unit travels with every value.
"""

from dataclasses import dataclass
from datetime import date

ANNUAL_FORMS = {"10-K", "10-K/A"}
MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS = 350, 380

# Metric name -> (period type, tags in priority order).
# "duration" facts cover a fiscal year (income / cash flow statement);
# "instant" facts are balances at the fiscal year end (balance sheet).
METRICS: dict[str, tuple[str, list[str]]] = {
    "revenue": ("duration", [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ]),
    "net_income": ("duration", ["NetIncomeLoss"]),
}


@dataclass(frozen=True)
class Fact:
    """One annual figure plus everything needed to trace it back to the filing."""

    metric: str
    fiscal_year: int
    value: float
    unit: str
    tag: str
    start: date | None  # None for instant (balance sheet) facts
    end: date
    accn: str  # accession number of the filing this copy came from
    form: str
    filed: date

    @property
    def source(self) -> str:
        return f"{self.tag} [{self.unit}] {self.form} accn {self.accn} filed {self.filed}, period ending {self.end}"


class MissingMetricError(LookupError):
    pass


def fiscal_year_of(end: date) -> int:
    """Label a fiscal year by the calendar year it ends in.

    52/53-week years can end in the first days of January; those belong to the
    previous year's label.
    """
    return end.year - 1 if end.month == 1 and end.day <= 7 else end.year


def annual_facts(companyfacts: dict, metric: str, unit: str = "USD") -> dict[int, Fact]:
    """Return {fiscal_year: Fact} for every fiscal year the company reported this metric."""
    period_type, tags = METRICS[metric]
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})

    best: dict[date, tuple[tuple, Fact]] = {}  # period end -> (sort key, fact)
    for priority, tag in enumerate(tags):
        for raw in gaap.get(tag, {}).get("units", {}).get(unit, []):
            fact = _to_fact(metric, tag, unit, raw, period_type)
            if fact is None:
                continue
            # Latest filing wins; within one filing, the higher-priority tag wins.
            key = (fact.filed, fact.accn, -priority)
            current = best.get(fact.end)
            if current is None or key > current[0]:
                best[fact.end] = (key, fact)

    if not best:
        raise MissingMetricError(f"No annual {unit} facts for {metric!r} under tags {tags}")

    result: dict[int, Fact] = {}
    for _, fact in best.values():
        if fact.fiscal_year in result:
            raise ValueError(f"Two {metric} periods map to fiscal year {fact.fiscal_year}")
        result[fact.fiscal_year] = fact
    return dict(sorted(result.items()))


def _to_fact(metric: str, tag: str, unit: str, raw: dict, period_type: str) -> Fact | None:
    """Convert one raw companyfacts entry to a Fact, or None if it is not an annual 10-K fact."""
    if raw.get("form") not in ANNUAL_FORMS:
        return None
    end = date.fromisoformat(raw["end"])
    start = date.fromisoformat(raw["start"]) if "start" in raw else None

    if period_type == "duration":
        if start is None or not MIN_ANNUAL_DAYS <= (end - start).days <= MAX_ANNUAL_DAYS:
            return None
    elif start is not None:
        return None

    return Fact(
        metric=metric,
        fiscal_year=fiscal_year_of(end),
        value=float(raw["val"]),
        unit=unit,
        tag=tag,
        start=start,
        end=end,
        accn=raw["accn"],
        form=raw["form"],
        filed=date.fromisoformat(raw["filed"]),
    )
