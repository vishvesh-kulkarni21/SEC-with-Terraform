"""The evidence ledger: every number or passage a tool hands an agent gets an id.

Agents must cite ids like [F1] (fact), [C2] (calculation), [P3] (passage) next to each
claim. The critic later resolves those ids here, which is what makes every number in
the final output traceable to an XBRL fact or a filing passage.

Formatting lives here, in Python: agents copy the `display` string verbatim, so the
model never converts units or scales numbers itself (that would be arithmetic).
"""

from dataclasses import dataclass, field

from equity_research.data.xbrl import Fact
from equity_research.retrieval.index import Passage
from equity_research.tools.calculators import Calculation


def format_value(value: float, unit: str) -> str:
    if unit == "USD":
        return f"${value / 1e6:,.0f} million" if abs(value) >= 1e6 else f"${value:,.0f}"
    if unit == "ratio":
        return f"{value * 100:.2f}%"
    if unit == "USD/shares":
        return f"${value:,.2f} per share"
    if unit == "shares":
        return f"{value / 1e6:,.0f} million shares"
    return f"{value:,.4g} {unit}"


@dataclass
class Evidence:
    evidence_id: str
    kind: str  # "fact", "calculation", "passage"
    ticker: str
    label: str  # e.g. "net_income FY2024" or "Item 7 passage"
    display: str  # what the agent should quote
    source: str
    value: float | None = None
    unit: str | None = None
    fiscal_year: int | None = None
    facts: list[Fact] = field(default_factory=list)  # underlying XBRL facts
    text: str | None = None  # passage text

    def for_model(self) -> dict:
        """The view the model sees in a tool result."""
        out = {"evidence_id": self.evidence_id, "label": self.label, "display": self.display,
               "source": self.source}
        if self.value is not None:
            out["value"] = self.value
            out["unit"] = self.unit
        if self.text is not None:
            out["text"] = self.text
        return out


class EvidenceLedger:
    def __init__(self):
        self.items: dict[str, Evidence] = {}
        self._counts = {"F": 0, "C": 0, "P": 0}

    def _next_id(self, prefix: str) -> str:
        self._counts[prefix] += 1
        return f"{prefix}{self._counts[prefix]}"

    def add_fact(self, ticker: str, fact: Fact) -> Evidence:
        # The same fact fetched twice keeps its first id, so citations stay stable.
        for e in self.items.values():
            if e.kind == "fact" and e.facts == [fact]:
                return e
        ev = Evidence(self._next_id("F"), "fact", ticker, f"{fact.metric} FY{fact.fiscal_year}",
                      format_value(fact.value, fact.unit), fact.source, fact.value, fact.unit,
                      fact.fiscal_year, [fact])
        self.items[ev.evidence_id] = ev
        return ev

    def add_calculation(self, ticker: str, calc: Calculation) -> Evidence:
        label = f"{calc.name} FY{calc.fiscal_year}"
        source = f"{calc.formula}; inputs: " + "; ".join(f.source for f in calc.facts())
        ev = Evidence(self._next_id("C"), "calculation", ticker, label,
                      format_value(calc.value, calc.unit), source, calc.value, calc.unit,
                      calc.fiscal_year, calc.facts())
        self.items[ev.evidence_id] = ev
        return ev

    def add_passage(self, passage: Passage) -> Evidence:
        for e in self.items.values():
            if e.kind == "passage" and e.source == passage.source:
                return e
        ev = Evidence(self._next_id("P"), "passage", passage.ticker,
                      f"{passage.chunk.section} passage", passage.chunk.text[:80] + "...",
                      passage.source, text=passage.chunk.text)
        self.items[ev.evidence_id] = ev
        return ev

    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)
