"""Deterministic financial calculators. The model never does arithmetic; it calls these.

Every function takes Facts (or earlier Calculations) and returns a Calculation that keeps
its inputs, so any output number can be traced back to XBRL facts.

Conventions:
- Rates are plain ratios (0.2397), never percents. Formatting is a presentation concern.
- Inputs are validated: same fiscal year where the formula requires it, expected
  units, no division by zero. A bad input raises CalculationError instead of
  returning a quietly wrong number.
"""

from dataclasses import dataclass, field

from equity_research.data.xbrl import Fact


class CalculationError(ValueError):
    pass


@dataclass(frozen=True)
class Calculation:
    name: str
    value: float
    unit: str  # "ratio", "USD", "USD/shares"
    formula: str
    inputs: tuple["Fact | Calculation", ...]
    fiscal_year: int | None = None
    assumptions: dict[str, float] = field(default_factory=dict)

    def facts(self) -> list[Fact]:
        """All underlying XBRL facts, recursively."""
        out: list[Fact] = []
        for item in self.inputs:
            out.extend(item.facts() if isinstance(item, Calculation) else [item])
        return out


# --- validation helpers -------------------------------------------------------

def _require_metric(fact: Fact, metric: str) -> None:
    if fact.metric != metric:
        raise CalculationError(f"Expected {metric}, got {fact.metric}")


def _require_unit(item: "Fact | Calculation", unit: str) -> None:
    if item.unit != unit:
        raise CalculationError(f"{_label(item)} has unit {item.unit}, expected {unit}")


def _require_same_year(*items: "Fact | Calculation") -> int:
    years = {i.fiscal_year for i in items}
    if len(years) != 1:
        raise CalculationError(f"Inputs span fiscal years {sorted(years)}; they must match")
    return years.pop()


def _require_nonzero(item: "Fact | Calculation") -> None:
    if item.value == 0:
        raise CalculationError(f"{_label(item)} is zero; cannot divide by it")


def _label(item: "Fact | Calculation") -> str:
    name = item.metric if isinstance(item, Fact) else item.name
    return f"{name} FY{item.fiscal_year}"


# --- growth -------------------------------------------------------------------

def yoy_growth(current: Fact, prior: Fact) -> Calculation:
    """(current - prior) / |prior| for the same metric in consecutive fiscal years."""
    if current.metric != prior.metric:
        raise CalculationError(f"Cannot compare {current.metric} with {prior.metric}")
    _require_unit(prior, current.unit)
    if current.fiscal_year - prior.fiscal_year != 1:
        raise CalculationError(
            f"YoY growth needs consecutive years, got FY{prior.fiscal_year} -> FY{current.fiscal_year}")
    _require_nonzero(prior)
    return Calculation(
        name=f"{current.metric}_yoy_growth",
        value=(current.value - prior.value) / abs(prior.value),
        unit="ratio",
        formula=f"({current.metric} FY{current.fiscal_year} - FY{prior.fiscal_year}) / |FY{prior.fiscal_year}|",
        inputs=(current, prior),
        fiscal_year=current.fiscal_year,
    )


def cagr(first: Fact, last: Fact) -> Calculation:
    """Compound annual growth rate between two fiscal years of the same metric."""
    if first.metric != last.metric:
        raise CalculationError(f"Cannot compare {first.metric} with {last.metric}")
    _require_unit(first, last.unit)
    years = last.fiscal_year - first.fiscal_year
    if years <= 0:
        raise CalculationError("CAGR needs the last year after the first year")
    if first.value <= 0 or last.value <= 0:
        raise CalculationError("CAGR is undefined when either endpoint is zero or negative")
    return Calculation(
        name=f"{last.metric}_cagr",
        value=(last.value / first.value) ** (1 / years) - 1,
        unit="ratio",
        formula=f"({last.metric} FY{last.fiscal_year} / FY{first.fiscal_year}) ^ (1/{years}) - 1",
        inputs=(first, last),
        fiscal_year=last.fiscal_year,
    )


# --- margins ------------------------------------------------------------------

def margin(numerator: Fact, revenue: Fact) -> Calculation:
    """numerator / revenue for the same fiscal year, e.g. net_income -> net margin."""
    _require_metric(revenue, "revenue")
    _require_unit(numerator, "USD")
    _require_unit(revenue, "USD")
    year = _require_same_year(numerator, revenue)
    _require_nonzero(revenue)
    return Calculation(
        name=f"{numerator.metric}_margin",
        value=numerator.value / revenue.value,
        unit="ratio",
        formula=f"{numerator.metric} / revenue (FY{year})",
        inputs=(numerator, revenue),
        fiscal_year=year,
    )


# --- ratios -------------------------------------------------------------------

def _simple_ratio(name: str, num: Fact, num_metric: str, den: Fact, den_metric: str) -> Calculation:
    _require_metric(num, num_metric)
    _require_metric(den, den_metric)
    _require_unit(num, den.unit)
    year = _require_same_year(num, den)
    _require_nonzero(den)
    return Calculation(
        name=name,
        value=num.value / den.value,
        unit="ratio",
        formula=f"{num_metric} / {den_metric} (FY{year})",
        inputs=(num, den),
        fiscal_year=year,
    )


def current_ratio(current_assets: Fact, current_liabilities: Fact) -> Calculation:
    return _simple_ratio("current_ratio", current_assets, "current_assets",
                         current_liabilities, "current_liabilities")


def debt_to_equity(long_term_debt: Fact, equity: Fact) -> Calculation:
    return _simple_ratio("debt_to_equity", long_term_debt, "long_term_debt",
                         equity, "shareholders_equity")


def return_on_equity(net_income: Fact, equity_end: Fact, equity_begin: Fact) -> Calculation:
    """Net income / average of opening and closing shareholders' equity."""
    _require_metric(net_income, "net_income")
    _require_metric(equity_end, "shareholders_equity")
    _require_metric(equity_begin, "shareholders_equity")
    year = _require_same_year(net_income, equity_end)
    if equity_begin.fiscal_year != year - 1:
        raise CalculationError("Opening equity must be the prior fiscal year's closing balance")
    average = (equity_end.value + equity_begin.value) / 2
    if average <= 0:
        raise CalculationError("ROE is not meaningful with zero or negative average equity")
    return Calculation(
        name="return_on_equity",
        value=net_income.value / average,
        unit="ratio",
        formula=f"net_income FY{year} / avg(shareholders_equity FY{year - 1}, FY{year})",
        inputs=(net_income, equity_end, equity_begin),
        fiscal_year=year,
    )


# --- cash flow and valuation --------------------------------------------------

def free_cash_flow(operating_cash_flow: Fact, capex: Fact) -> Calculation:
    """Operating cash flow minus capital expenditure (capex is reported as a positive payment)."""
    _require_metric(operating_cash_flow, "operating_cash_flow")
    _require_metric(capex, "capex")
    year = _require_same_year(operating_cash_flow, capex)
    return Calculation(
        name="free_cash_flow",
        value=operating_cash_flow.value - capex.value,
        unit="USD",
        formula=f"operating_cash_flow - capex (FY{year})",
        inputs=(operating_cash_flow, capex),
        fiscal_year=year,
    )


def dcf_value_per_share(
    fcf: Calculation,
    cash: Fact,
    debt: Fact,
    diluted_shares: Fact,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    years: int = 5,
) -> Calculation:
    """Two-stage DCF on free cash flow, returning equity value per diluted share.

    Stage 1: FCF grows at growth_rate for `years` years, discounted at discount_rate.
    Stage 2: Gordon terminal value at terminal_growth, discounted back.
    Equity value = enterprise value + cash - debt.

    The agent chooses the assumptions (a judgment call); this function does the math.
    """
    if fcf.name != "free_cash_flow":
        raise CalculationError("dcf_value_per_share expects a free_cash_flow Calculation")
    _require_metric(cash, "cash")
    _require_metric(debt, "long_term_debt")
    _require_metric(diluted_shares, "diluted_shares")
    year = _require_same_year(fcf, cash, debt, diluted_shares)
    if discount_rate <= terminal_growth:
        raise CalculationError("discount_rate must exceed terminal_growth")
    if years < 1:
        raise CalculationError("years must be at least 1")
    _require_nonzero(diluted_shares)

    pv_stage1 = sum(
        fcf.value * (1 + growth_rate) ** t / (1 + discount_rate) ** t for t in range(1, years + 1)
    )
    final_fcf = fcf.value * (1 + growth_rate) ** years
    terminal = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal / (1 + discount_rate) ** years
    equity_value = pv_stage1 + pv_terminal + cash.value - debt.value

    return Calculation(
        name="dcf_value_per_share",
        value=equity_value / diluted_shares.value,
        unit="USD/shares",
        formula=(f"(PV of {years}y FCF at g={growth_rate} + PV terminal at g={terminal_growth}, "
                 f"r={discount_rate}) + cash - debt, / diluted_shares (base FY{year})"),
        inputs=(fcf, cash, debt, diluted_shares),
        fiscal_year=year,
        assumptions={"growth_rate": growth_rate, "discount_rate": discount_rate,
                     "terminal_growth": terminal_growth, "years": years},
    )
