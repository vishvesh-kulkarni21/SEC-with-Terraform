"""Deliberately plant wrong numbers in a draft, to measure whether the critic catches them.

Without planted errors, a high catch rate means nothing: the critic might pass
everything. Each kind mirrors a class in the error taxonomy.
"""

import random
import re
from dataclasses import dataclass

from equity_research.agents.claims import Claim, Figure

PLANT_KINDS = ("perturb", "scale", "period")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class PlantRecord:
    kind: str
    claim_index: int
    original: str
    planted: str


def _reformat(number: str, factor: float) -> str:
    decimals = len(number.split(".")[1]) if "." in number else 0
    value = float(number.replace(",", "")) * factor
    return f"{value:,.{decimals}f}" if "," in number or value >= 1000 else f"{value:.{decimals}f}"


def plant_error(claims: list[Claim], kind: str, rng: random.Random) -> PlantRecord | None:
    """Mutate one numeric claim in place (a figure citing a fact or calculation)."""
    candidates = [(i, f) for i, c in enumerate(claims) for f in c.figures if f.evidence_id[:1] in ("F", "C")]
    if not candidates:
        return None
    index, fig = rng.choice(candidates)
    claim = claims[index]
    original = claim.text

    if kind == "perturb":  # wrong or fabricated value
        num = _NUM_RE.search(fig.display).group(0)
        new_display = fig.display.replace(num, _reformat(num, 1.07), 1)
    elif kind == "scale":  # wrong scale or unit
        if "million" in fig.display:
            new_display = fig.display.replace("million", "billion")
        else:
            num = _NUM_RE.search(fig.display).group(0)
            new_display = fig.display.replace(num, _reformat(num, 0.1), 1)
    elif kind == "period":  # right number, wrong fiscal year
        years = re.findall(r"\b(?:19|20)\d{2}\b", claim.text)
        if years:
            y = years[-1]
            claim.text = claim.text.replace(y, str(int(y) - 1))
        else:
            claim.text = claim.text.rstrip(".") + f" in fiscal {rng.choice([2019, 2020])}."
        return PlantRecord(kind, index, original, claim.text)
    else:
        raise ValueError(f"unknown plant kind {kind!r}")

    claim.text = claim.text.replace(fig.display, new_display) if fig.display in claim.text \
        else claim.text.rstrip(".") + f" ({new_display})."
    claim.figures = [Figure(new_display, f.evidence_id) if f is fig else f for f in claim.figures]
    return PlantRecord(kind, index, original, claim.text)
