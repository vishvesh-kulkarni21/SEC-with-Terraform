"""Structured claims and the deterministic numeric check the critic runs on them.

A claim is one sentence of argument. Every number in it must be declared as a Figure
that names the evidence id it came from. Verification is pure Python: comparing a
number with ground truth is arithmetic, and the model never does arithmetic.

Tolerance: a figure matches when it equals the evidence value up to display rounding
(half a unit of the last shown digit) or within 0.5% relative, whichever is looser.
A right number attached to the wrong fiscal year is a failure.
"""

import re
from dataclasses import dataclass, field

from equity_research.agents.evidence import Evidence, EvidenceLedger

RELATIVE_TOLERANCE = 0.005

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "One sentence of argument."},
                    "figures": {
                        "type": "array",
                        "description": "Every number in the text, copied exactly, with the evidence id it came from.",
                        "items": {
                            "type": "object",
                            "properties": {"display": {"type": "string"}, "evidence_id": {"type": "string"}},
                            "required": ["display", "evidence_id"],
                        },
                    },
                    "evidence_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Passage ids ([P#]) supporting any qualitative statement in the claim.",
                    },
                },
                "required": ["text", "figures", "evidence_ids"],
            },
        }
    },
    "required": ["claims"],
}


@dataclass
class Figure:
    display: str
    evidence_id: str


@dataclass
class Claim:
    text: str
    figures: list[Figure] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        return cls(d.get("text", ""), [Figure(f.get("display", ""), f.get("evidence_id", ""))
                                       for f in d.get("figures", [])], list(d.get("evidence_ids", [])))

    def to_dict(self) -> dict:
        return {"text": self.text, "figures": [f.__dict__ for f in self.figures],
                "evidence_ids": self.evidence_ids}


@dataclass
class Issue:
    code: str  # no_evidence, value_mismatch, wrong_period, stale_period, not_in_passage, undeclared_number, unparseable, unsupported, critic_arithmetic
    detail: str


# --- parsing numbers as written ------------------------------------------------

_NUM = r"\(?-?\$?\s?\d[\d,]*(?:\.\d+)?\)?"
_FIGURE_RE = re.compile(
    rf"(?P<num>[+]?{_NUM})\s*(?P<unit>percentage points?|%|percent|million|billion|thousand|per share)?", re.I)
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
# Money, percents, and anything with a thousands separator or decimals count as figures.
# Bare integers (years, "Item 7", "10-K") do not.
_TEXT_NUMBER_RE = re.compile(
    r"\(?-?\$\s?\d[\d,]*(?:\.\d+)?\)?(?:\s*(?:million|billion|thousand))?"
    r"|\(?-?\d[\d,]*(?:\.\d+)?\)?\s*(?:%|percentage points?|percent)"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b(?:\s*(?:million|billion|thousand))?"
    r"|\b\d+\.\d+\b(?:\s*(?:million|billion|thousand|x))?", re.I)
_YEAR_RE = re.compile(r"\b(?:FY\s?|fiscal (?:year )?)?((?:19|20)\d{2})\b", re.I)


def parse_figure(display: str) -> tuple[float, float, str] | None:
    """'$416,161 million' -> (416161e6, abs_tolerance, 'USD'); '26.92%' -> (0.2692, 5e-5, 'ratio')."""
    m = _FIGURE_RE.search(display)
    if not m:
        return None
    raw, unit = m.group("num"), (m.group("unit") or "").lower()
    negative = raw.startswith("(") and raw.endswith(")") or "-" in raw
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits or digits == ".":
        return None
    value = float(digits)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    half_unit = 0.5 * 10 ** -decimals
    if unit in ("%", "percent") or unit.startswith("percentage point"):
        value, half_unit, kind = value / 100, half_unit / 100, "ratio"
    elif unit in _SCALE:
        value, half_unit, kind = value * _SCALE[unit], half_unit * _SCALE[unit], "USD"
    elif unit == "per share":
        kind = "USD/shares"
    else:
        kind = "USD" if "$" in raw else "number"
    return (-value if negative else value), half_unit, kind


def numbers_in_text(text: str) -> list[str]:
    return [m.group(0).strip() for m in _TEXT_NUMBER_RE.finditer(text)]


def _normalise(s: str) -> str:
    return re.sub(r"[\s$]", "", s).lower()


# --- verification --------------------------------------------------------------

def check_claim(claim: Claim, ledger: EvidenceLedger, allowed_years: set[int] | None = None) -> list[Issue]:
    """Deterministic checks. Returns [] when every figure is verified.

    allowed_years: if given, figures from any other fiscal year fail as stale_period.
    """
    issues: list[Issue] = []
    declared = {_normalise(f.display) for f in claim.figures}

    for n in numbers_in_text(claim.text):
        if not any(_normalise(n) in d or d in _normalise(n) for d in declared):
            issues.append(Issue("undeclared_number", f"'{n}' appears in the text but has no cited figure"))

    years_in_text = {int(y) for y in _YEAR_RE.findall(claim.text)}
    for fig in claim.figures:
        ev = ledger.get(fig.evidence_id)
        if ev is None:
            issues.append(Issue("no_evidence", f"{fig.display}: evidence id {fig.evidence_id!r} does not exist"))
        elif ev.kind == "passage":
            issues += _check_against_passage(fig, ev)
        else:
            issues += _check_against_value(fig, ev, years_in_text)
            if allowed_years and ev.fiscal_year is not None and ev.fiscal_year not in allowed_years:
                issues.append(Issue("stale_period", f"[{ev.evidence_id}] is FY{ev.fiscal_year}; this analysis "
                                                    f"covers FY{min(allowed_years)}-FY{max(allowed_years)}"))

    for eid in claim.evidence_ids:
        if ledger.get(eid) is None:
            issues.append(Issue("no_evidence", f"evidence id {eid!r} does not exist"))
    if not claim.figures and not claim.evidence_ids:
        issues.append(Issue("no_evidence", "claim cites no evidence"))
    return issues


def _check_against_value(fig: Figure, ev: Evidence, years_in_text: set[int]) -> list[Issue]:
    parsed = parse_figure(fig.display)
    if parsed is None:
        return [Issue("unparseable", f"cannot read a number in {fig.display!r}")]
    value, half_unit, _ = parsed
    tolerance = max(half_unit, RELATIVE_TOLERANCE * abs(ev.value))
    issues = []
    if abs(value - ev.value) > tolerance:
        issues.append(Issue("value_mismatch",
                            f"{fig.display} does not match [{ev.evidence_id}] {ev.label} = {ev.display}"))
    if years_in_text and ev.fiscal_year is not None and ev.fiscal_year not in years_in_text:
        issues.append(Issue("wrong_period",
                            f"text refers to {sorted(years_in_text)} but [{ev.evidence_id}] is FY{ev.fiscal_year}"))
    return issues


def _check_against_passage(fig: Figure, ev: Evidence) -> list[Issue]:
    """A figure cited to a passage must literally appear in it (filings print numbers in
    their own units, e.g. "112,010" in a table stated in millions)."""
    m = _FIGURE_RE.search(fig.display)
    if m is None:
        return [Issue("unparseable", f"cannot read a number in {fig.display!r}")]
    core = re.sub(r"[()$\s-]", "", m.group("num"))
    unit = (m.group("unit") or "").lower()
    # Keep word boundaries: stripping all whitespace turns "In 2025, $14.7 billion" into
    # "2025,14.7", which reads as one thousands-separated number.
    passage = " ".join(re.sub(r"\$", " ", ev.text or "").split()).lower()
    # Filing tables print "%" only on a column's first row ("25.8% | ... | 22.1 | 17.7"),
    # so a percent figure matches the bare number too.
    pattern = re.escape(core) + (r"(?:\s*\)?\s*%)?" if unit in ("%", "percent") else "")
    if not re.search(rf"(?<![\d.])(?<!\d,){pattern}(?![\d]|[.,]\d)", passage):
        return [Issue("not_in_passage", f"{fig.display} not found in [{ev.evidence_id}] ({ev.source})")]
    return []
