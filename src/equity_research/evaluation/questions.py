"""Question set for the retrieval evaluation, generated from XBRL so every question has
a ground-truth answer. Fixed before any experiment runs (see eval/eval_config.json)."""

from dataclasses import dataclass

from equity_research.data.financials import CompanyFinancials
from equity_research.data.xbrl import Fact

TEMPLATES = {
    "revenue": "What was {name}'s total revenue for fiscal year {year}?",
    "net_income": "What was {name}'s net income attributable to the company for fiscal year {year}?",
    "operating_income": "What was {name}'s operating income for fiscal year {year}?",
    "operating_cash_flow": "What was {name}'s net cash provided by operating activities in fiscal year {year}?",
    "total_assets": "What were {name}'s total assets at the end of fiscal year {year}?",
    "shareholders_equity": "What was {name}'s total shareholders' equity at the end of fiscal year {year}?",
}


@dataclass(frozen=True)
class Question:
    qid: str
    ticker: str
    metric: str
    fiscal_year: int
    text: str
    truth: Fact


def build_questions(fin: CompanyFinancials, metrics: list[str], years_back: int = 2) -> list[Question]:
    """One question per (metric, year) for the latest `years_back` fiscal years.
    Metrics the company does not report are skipped, never guessed."""
    out = []
    latest = fin.latest_year
    for metric in metrics:
        for year in range(latest, latest - years_back, -1):
            if year not in fin.years(metric):
                continue
            text = TEMPLATES[metric].format(name=f"{fin.name} ({fin.ticker})", year=year)
            out.append(Question(f"{fin.ticker}-{metric}-{year}", fin.ticker, metric, year, text,
                                fin.get(metric, year)))
    return out
