"""Extract annual ground-truth facts from EDGAR companyfacts JSON.

Rules (see docs/DECISIONS.md):
- Annual = a 10-K / 10-K/A duration fact spanning 350-380 days (covers 52/53-week years).
  The `fy` field is never used to pick periods: it is the fiscal year of the *filing*,
  so a 10-K for FY2024 labels its FY2022 comparative as fy=2024 too.
- Ground truth = as most recently reported: for each period end date, the fact from the
  latest-filed 10-K wins, whichever tag in the metric's list it uses. Tag priority only
  breaks ties between facts in the same filing.
- Each metric has one fixed unit (USD, shares, USD/shares); the unit travels with every value.
"""

from dataclasses import dataclass
from datetime import date

ANNUAL_FORMS = {"10-K", "10-K/A"}
MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS = 350, 380

@dataclass(frozen=True)
class MetricSpec:
    period_type: str  # "duration" (income / cash flow statement) or "instant" (balance sheet)
    unit: str
    tags: tuple[str, ...]  # priority order; only breaks ties within one filing


METRICS: dict[str, MetricSpec] = {
    # Income statement
    "revenue": MetricSpec("duration", "USD", (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    )),
    "gross_profit": MetricSpec("duration", "USD", ("GrossProfit",)),
    "operating_income": MetricSpec("duration", "USD", ("OperatingIncomeLoss",)),
    "pretax_income": MetricSpec("duration", "USD", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    )),
    # Fallbacks only apply when NetIncomeLoss is absent (ties within a filing go to the
    # first tag); Caterpillar reports only the "available to common" variant.
    "net_income": MetricSpec("duration", "USD", (
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    )),
    "eps_diluted": MetricSpec("duration", "USD/shares", ("EarningsPerShareDiluted",)),
    "diluted_shares": MetricSpec("duration", "shares", ("WeightedAverageNumberOfDilutedSharesOutstanding",)),
    # Cash flow statement
    "operating_cash_flow": MetricSpec("duration", "USD", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capex": MetricSpec("duration", "USD", ("PaymentsToAcquirePropertyPlantAndEquipment",)),
    # Balance sheet
    "total_assets": MetricSpec("instant", "USD", ("Assets",)),
    "total_liabilities": MetricSpec("instant", "USD", ("Liabilities",)),
    "current_assets": MetricSpec("instant", "USD", ("AssetsCurrent",)),
    "current_liabilities": MetricSpec("instant", "USD", ("LiabilitiesCurrent",)),
    "cash": MetricSpec("instant", "USD", ("CashAndCashEquivalentsAtCarryingValue",)),
    "long_term_debt": MetricSpec("instant", "USD", (  # includes current maturities where reported
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "LongTermDebtNoncurrent",
    )),
    "shareholders_equity": MetricSpec("instant", "USD", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )),
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


def annual_facts(
    companyfacts: dict, metric: str, period_ends: set[date] | None = None
) -> dict[int, Fact]:
    """Return {fiscal_year: Fact} for every fiscal year the company reported this metric.

    period_ends: if given, keep only facts ending on these dates. Balance-sheet (instant)
    facts need this: a 10-K also carries balances at dates that are not fiscal year ends.
    """
    spec = METRICS[metric]
    tags, unit = spec.tags, spec.unit
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})

    best: dict[date, tuple[tuple, Fact]] = {}  # period end -> (sort key, fact)
    for priority, tag in enumerate(tags):
        for raw in gaap.get(tag, {}).get("units", {}).get(unit, []):
            fact = _to_fact(metric, tag, unit, raw, spec.period_type)
            if fact is None or (period_ends is not None and fact.end not in period_ends):
                continue
            # Latest filing wins; within one filing, the higher-priority tag wins.
            key = (fact.filed, fact.accn, -priority)
            current = best.get(fact.end)
            if current is None or key > current[0]:
                best[fact.end] = (key, fact)

    if not best:
        raise MissingMetricError(f"No annual {unit} facts for {metric!r} under tags {list(tags)}")

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


def reported_facts(companyfacts: dict, metric: str, end: date) -> list[Fact]:
    """Every 10-K copy of this metric for this period end, under any of its tags.

    Used by the error taxonomy to tell apart an older, since-revised copy (stale or
    restated) from an alternative definition in the same filing (e.g. equity with or
    without noncontrolling interest).
    """
    spec = METRICS[metric]
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out = []
    for tag in spec.tags:
        for raw in gaap.get(tag, {}).get("units", {}).get(spec.unit, []):
            fact = _to_fact(metric, tag, spec.unit, raw, spec.period_type)
            if fact is not None and fact.end == end:
                out.append(fact)
    return out
