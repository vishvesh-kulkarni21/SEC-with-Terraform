"""The research toolset exposed to agents: XBRL lookups, calculators, and filing search.

Each tool returns JSON-able dicts. Results are registered in the evidence ledger and the
model sees their evidence ids. Tool failures (missing data, invalid inputs) come back
as {"error": ...} so the agent can adapt instead of crashing the run.
"""

from typing import Callable

from equity_research.agents.evidence import EvidenceLedger
from equity_research.data.financials import CompanyFinancials
from equity_research.data.xbrl import METRICS, MissingMetricError
from equity_research.llm.base import ToolSpec
from equity_research.retrieval.index import Retriever
from equity_research.tools import calculators as calc

MARGIN_METRICS = ["gross_profit", "operating_income", "pretax_income", "net_income"]
RATIOS = ["current_ratio", "debt_to_equity", "return_on_equity"]

TICKER = {"type": "string", "description": "Stock ticker, e.g. AAPL"}
YEAR = {"type": "integer", "description": "Fiscal year as labelled by the company, e.g. 2025"}
METRIC = {"type": "string", "enum": sorted(METRICS)}


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


TOOL_SPECS = [
    ToolSpec("list_metrics",
             "List the financial metrics available for a company and the fiscal years each covers. "
             "Call this first to learn the latest fiscal year.",
             _obj({"ticker": TICKER}, ["ticker"])),
    ToolSpec("get_fact",
             "Get one reported annual figure from the company's XBRL filings (ground truth).",
             _obj({"ticker": TICKER, "metric": METRIC, "fiscal_year": YEAR},
                  ["ticker", "metric", "fiscal_year"])),
    ToolSpec("growth",
             "Year-over-year growth of a metric: fiscal_year versus the year before.",
             _obj({"ticker": TICKER, "metric": METRIC, "fiscal_year": YEAR},
                  ["ticker", "metric", "fiscal_year"])),
    ToolSpec("cagr",
             "Compound annual growth rate of a metric between two fiscal years.",
             _obj({"ticker": TICKER, "metric": METRIC, "start_year": YEAR, "end_year": YEAR},
                  ["ticker", "metric", "start_year", "end_year"])),
    ToolSpec("margin",
             "A metric as a share of revenue in the same fiscal year (e.g. net_income -> net margin).",
             _obj({"ticker": TICKER, "metric": {"type": "string", "enum": MARGIN_METRICS},
                   "fiscal_year": YEAR}, ["ticker", "metric", "fiscal_year"])),
    ToolSpec("ratio",
             "Balance sheet ratio for a fiscal year: current_ratio, debt_to_equity, or "
             "return_on_equity (net income / average equity).",
             _obj({"ticker": TICKER, "name": {"type": "string", "enum": RATIOS}, "fiscal_year": YEAR},
                  ["ticker", "name", "fiscal_year"])),
    ToolSpec("free_cash_flow",
             "Free cash flow (operating cash flow minus capital expenditure) for a fiscal year.",
             _obj({"ticker": TICKER, "fiscal_year": YEAR}, ["ticker", "fiscal_year"])),
    ToolSpec("dcf_valuation",
             "Two-stage DCF equity value per diluted share, based on free cash flow of fiscal_year. "
             "You choose the assumptions; state and justify them.",
             _obj({"ticker": TICKER, "fiscal_year": YEAR,
                   "growth_rate": {"type": "number", "description": "Annual FCF growth for the forecast years, e.g. 0.06"},
                   "discount_rate": {"type": "number", "description": "e.g. 0.09"},
                   "terminal_growth": {"type": "number", "description": "e.g. 0.025; must be below discount_rate"},
                   "years": {"type": "integer", "description": "Forecast years, default 5"}},
                  ["ticker", "fiscal_year", "growth_rate", "discount_rate", "terminal_growth"])),
    ToolSpec("search_filing",
             "Semantic search over the company's latest 10-K text. Use for qualitative context "
             "(strategy, risks, segment commentary), not as the source of reported figures.",
             _obj({"ticker": TICKER, "query": {"type": "string"}}, ["ticker", "query"])),
]


class ResearchTools:
    def __init__(self, load_financials: Callable[[str], CompanyFinancials],
                 retriever: Retriever | None, strategy: str, k: int = 5):
        self._load = load_financials
        self._financials: dict[str, CompanyFinancials] = {}
        self.retriever, self.strategy, self.k = retriever, strategy, k
        self.ledger = EvidenceLedger()

    @property
    def specs(self) -> list[ToolSpec]:
        return TOOL_SPECS if self.retriever else [t for t in TOOL_SPECS if t.name != "search_filing"]

    def call(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool {name!r}"}
        try:
            return handler(**args)
        except (MissingMetricError, calc.CalculationError, LookupError, ValueError, TypeError) as e:
            return {"error": str(e)}

    # --- helpers ---

    def fin(self, ticker: str) -> CompanyFinancials:
        ticker = ticker.upper()
        if ticker not in self._financials:
            self._financials[ticker] = self._load(ticker)
        return self._financials[ticker]

    def _fact(self, ticker, metric, year):
        return self.fin(ticker).get(metric, int(year))

    def _calc_result(self, ticker, calculation) -> dict:
        return self.ledger.add_calculation(ticker.upper(), calculation).for_model()

    # --- tools ---

    def _tool_list_metrics(self, ticker):
        f = self.fin(ticker)
        return {"company": f.name, "latest_fiscal_year": f.latest_year,
                "metrics": {m: {"years": f"{ys[0]}-{ys[-1]}"} if (ys := f.years(m)) else "not reported"
                            for m in METRICS}}

    def _tool_get_fact(self, ticker, metric, fiscal_year):
        return self.ledger.add_fact(ticker.upper(), self._fact(ticker, metric, fiscal_year)).for_model()

    def _tool_growth(self, ticker, metric, fiscal_year):
        y = int(fiscal_year)
        return self._calc_result(ticker, calc.yoy_growth(self._fact(ticker, metric, y),
                                                         self._fact(ticker, metric, y - 1)))

    def _tool_cagr(self, ticker, metric, start_year, end_year):
        return self._calc_result(ticker, calc.cagr(self._fact(ticker, metric, start_year),
                                                   self._fact(ticker, metric, end_year)))

    def _tool_margin(self, ticker, metric, fiscal_year):
        if metric not in MARGIN_METRICS:
            raise ValueError(f"margin metric must be one of {MARGIN_METRICS}")
        return self._calc_result(ticker, calc.margin(self._fact(ticker, metric, fiscal_year),
                                                     self._fact(ticker, "revenue", fiscal_year)))

    def _tool_ratio(self, ticker, name, fiscal_year):
        y = int(fiscal_year)
        f = lambda m, yr=y: self._fact(ticker, m, yr)  # noqa: E731
        if name == "current_ratio":
            result = calc.current_ratio(f("current_assets"), f("current_liabilities"))
        elif name == "debt_to_equity":
            result = calc.debt_to_equity(f("long_term_debt"), f("shareholders_equity"))
        elif name == "return_on_equity":
            result = calc.return_on_equity(f("net_income"), f("shareholders_equity"),
                                           f("shareholders_equity", y - 1))
        else:
            raise ValueError(f"ratio name must be one of {RATIOS}")
        return self._calc_result(ticker, result)

    def _tool_free_cash_flow(self, ticker, fiscal_year):
        return self._calc_result(ticker, calc.free_cash_flow(
            self._fact(ticker, "operating_cash_flow", fiscal_year), self._fact(ticker, "capex", fiscal_year)))

    def _tool_dcf_valuation(self, ticker, fiscal_year, growth_rate, discount_rate, terminal_growth, years=5):
        fcf = calc.free_cash_flow(self._fact(ticker, "operating_cash_flow", fiscal_year),
                                  self._fact(ticker, "capex", fiscal_year))
        result = calc.dcf_value_per_share(
            fcf, self._fact(ticker, "cash", fiscal_year), self._fact(ticker, "long_term_debt", fiscal_year),
            self._fact(ticker, "diluted_shares", fiscal_year),
            float(growth_rate), float(discount_rate), float(terminal_growth), int(years))
        out = self._calc_result(ticker, result)
        out["assumptions"] = result.assumptions
        return out

    def _tool_search_filing(self, ticker, query):
        if self.retriever is None:
            raise ValueError("search_filing is disabled in this run")
        passages = self.retriever.search(ticker.upper(), query, self.strategy, k=self.k)
        return {"passages": [self.ledger.add_passage(p).for_model() for p in passages]}
