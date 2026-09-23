"""Score one answer against XBRL ground truth and classify errors.

Taxonomy (fixed before experiments; CLAUDE.md):
  wrong_scale        right digits, wrong unit or scale (thousands vs millions, % vs ratio)
  stale_restated     matches a copy of the same period from an EARLIER filing that was since revised
  wrong_period       matches the same metric for a different fiscal year
  wrong_line_item    matches a different metric, or copies a real figure from a retrieved
                     passage that is the wrong row (e.g. a segment instead of the total)
  fabricated         no source supports the number: not reported, not in any retrieved passage
Checked in that order, so an answer is assigned the most specific explanation.

Not errors, counted separately:
  abstained          "not found"
  alt_definition     matches another accepted tag for the same metric in the SAME filing as
                     ground truth (e.g. total equity incl. noncontrolling interest vs parent
                     equity; Chevron "sales and other operating revenues" vs "total revenues
                     and other income"). The question admits both readings.
"""

import re

from equity_research.agents.claims import RELATIVE_TOLERANCE, parse_figure
from equity_research.data.financials import CompanyFinancials
from equity_research.data.xbrl import METRICS, reported_facts
from equity_research.evaluation.questions import Question

ERROR_CLASSES = ("wrong_scale", "stale_restated", "wrong_period", "wrong_line_item", "fabricated")
SCALE_FACTORS = (1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9)


def matches(value: float, truth: float, half_unit: float = 0.0) -> bool:
    return abs(value - truth) <= max(half_unit, RELATIVE_TOLERANCE * abs(truth))


def is_abstention(display: str | None) -> bool:
    return not display or not re.search(r"\d", display) or "not found" in display.lower()


def classify(display: str, q: Question, fin: CompanyFinancials, passages: list[str] | None = None) -> str:
    """Return "correct", "alt_definition", "abstained", "unparseable", or one of ERROR_CLASSES.

    passages: text the agent retrieved; a wrong number found there was misread, not invented.
    """
    if is_abstention(display):
        return "abstained"
    parsed = parse_figure(display)
    if parsed is None:
        return "unparseable"
    value, half_unit, _ = parsed
    truth = q.truth.value
    if matches(value, truth, half_unit):
        return "correct"
    if any(matches(value, truth * f) for f in SCALE_FACTORS):
        return "wrong_scale"
    copies = reported_facts(fin.companyfacts, q.metric, q.truth.end)
    if any(f.accn == q.truth.accn and f.value != truth and matches(value, f.value) for f in copies):
        return "alt_definition"
    if any(f.filed < q.truth.filed and f.value != truth and matches(value, f.value) for f in copies):
        return "stale_restated"
    for year in fin.years(q.metric):
        if year != q.fiscal_year and matches(value, fin.get(q.metric, year).value):
            return "wrong_period"
    for metric, spec in METRICS.items():
        if metric == q.metric or spec.unit != "USD":
            continue
        for year in (q.fiscal_year, q.fiscal_year - 1):
            if year in fin.years(metric) and matches(value, fin.get(metric, year).value):
                return "wrong_line_item"
    if passages and _appears_in(display, passages):
        return "wrong_line_item"
    return "fabricated"


def _appears_in(display: str, passages: list[str]) -> bool:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", display)
    if not m:
        return False
    pattern = re.compile(rf"(?<![\d,.]){re.escape(m.group(0))}(?![\d,])")
    return any(pattern.search(p) for p in passages)


def millions_string(value: float) -> str:
    """How a 10-K stated in millions prints the figure: 416161000000 -> '416,161'."""
    return f"{abs(value) / 1e6:,.0f}"


def retrieval_hit(q: Question, passages: list[str]) -> bool:
    """Did any retrieved passage contain the ground-truth figure as the filing prints it?"""
    needle = millions_string(q.truth.value)
    pattern = re.compile(rf"(?<![\d,.]){re.escape(needle)}(?![\d,])")
    return any(pattern.search(p) for p in passages)
