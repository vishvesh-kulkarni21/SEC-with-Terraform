"""Critic: verifies every claim and returns a structured verdict per claim.

Two layers:
1. Deterministic (Python): every figure against the cited XBRL fact, calculation or
   passage text; undeclared numbers; missing evidence ids; wrong fiscal year.
2. Model judgment: does the cited evidence support what each claim concludes (causes,
   risks, judgments such as "undervalued")? Only this part uses the LLM, and it takes
   the figures as given: it never judges numbers. A guard checks the model's reasons:
   a number that appears in neither the claim nor its evidence was computed by the
   critic (design rule 1), so that verdict is discarded and the claim fails closed.

A claim passes only if both layers pass. Failing claims are sent back for revision,
and anything still failing after the last round is removed (design rule 3).
"""

import json
import re
from dataclasses import dataclass, field

from equity_research.agents.claims import Claim, Issue, check_claim, numbers_in_text
from equity_research.agents.evidence import EvidenceLedger
from equity_research.llm.base import ChatModel, Message, Usage
from equity_research.tracing import log_event, timed

SUPPORT_PROMPT = """You are a strict fact-checking critic for equity research.
For each claim, decide whether its cited evidence supports everything it asserts beyond the raw numbers: direction of change, causes, risks, business descriptions, and judgments.
- Assume every number in the claim is correct; numbers are verified separately. Never reject a claim over a number.
- Never calculate. Do not multiply, divide, subtract or estimate any number, and do not write any number in your reason that is not in the claim or its evidence.
- SUPPORTED: reporting the cited figures and the direction between them ("rose from X to Y", "declined from X to Y"); such a claim needs no passage. Also standard interpretations that follow by definition (a lower current ratio means less short-term liquidity; lower free cash flow means weaker cash generation; a lower margin means pressure on profitability; a DCF value is derived from the free cash flow it is based on).
- UNSUPPORTED: magnitude words that need arithmetic to check ("doubled", "tripled", "halved", "more than twice") unless a cited calculation states that figure; causes, drivers, risks or business facts that the cited passages do not state; predictions about the future; comparisons that need a figure the claim does not cite (e.g. "grew" when only one year is cited); valuation judgments ("undervalued", "attractive", "overvalued"), because no market price is available.
Return JSON only."""

SUPPORT_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"},
                       "reason": {"type": "string"}},
        "required": ["index", "supported", "reason"]}}},
    "required": ["verdicts"],
}


@dataclass
class Verdict:
    claim: Claim
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {"claim": self.claim.to_dict(), "passed": self.passed,
                "issues": [i.__dict__ for i in self.issues]}


class Critic:
    def __init__(self, model: ChatModel | None, allowed_years: set[int] | None = None):
        """model=None runs the deterministic layer only."""
        self.model = model
        self.allowed_years = allowed_years
        self.usage = Usage()

    def review(self, claims: list[Claim], ledger: EvidenceLedger, side: str) -> list[Verdict]:
        verdicts = [Verdict(c, check_claim(c, ledger, self.allowed_years)) for c in claims]
        if self.model is not None:
            self._check_support(verdicts, ledger, side)
        for i, v in enumerate(verdicts):
            log_event("critic_verdict", agent="critic", side=side, claim_index=i, passed=v.passed,
                      claim=v.claim.text, issues=[f"{x.code}: {x.detail}" for x in v.issues])
        return verdicts

    def _check_support(self, verdicts: list[Verdict], ledger: EvidenceLedger, side: str) -> None:
        to_check = list(enumerate(verdicts))
        if not to_check:
            return
        items = []
        for i, v in to_check:
            cited = list(dict.fromkeys(v.claim.evidence_ids + [f.evidence_id for f in v.claim.figures]))
            passages = [{"id": e, "text": ev.text} for e in cited
                        if (ev := ledger.get(e)) is not None and ev.kind == "passage"]
            figures = [{"id": f.evidence_id, "label": ev.label, "value": ev.display,
                        "definition": ev.source.split("; inputs:")[0] if ev.kind == "calculation" else "reported in 10-K"}
                       for f in v.claim.figures
                       if (ev := ledger.get(f.evidence_id)) is not None and ev.kind != "passage"]
            items.append({"index": i, "claim": v.claim.text, "figures": figures, "passages": passages})
        prompt = "Claims to check:\n" + json.dumps(items, indent=1)

        with timed("llm_call", agent="critic", side=side, model=self.model.model_id) as ev:
            response = self.model.generate(SUPPORT_PROMPT, [Message("user", prompt)], [],
                                           temperature=0.0, json_schema=SUPPORT_SCHEMA)
            ev.update(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
        self.usage = self.usage + response.usage

        try:
            results = {r["index"]: r for r in json.loads(response.message.text)["verdicts"]}
        except (json.JSONDecodeError, KeyError, TypeError):
            results = {}
        for i, v in to_check:
            r = results.get(i)
            if r is None:  # no verdict is not a pass: unverified claims never pass silently
                v.issues.append(Issue("unsupported", "critic returned no verdict for this claim"))
            elif computed := _computed_numbers(r.get("reason", ""), v.claim, ledger):
                log_event("critic_arithmetic", severity="WARNING", agent="critic", side=side, claim_index=i,
                          numbers=computed, reason=r.get("reason", ""))
                v.issues.append(Issue("critic_arithmetic",
                                      f"the critic's verdict relied on numbers it computed ({', '.join(computed)}); "
                                      "if the claim asserts a magnitude such as 'doubled', cite a calculation "
                                      "for it or remove the wording"))
            elif not r.get("supported"):
                v.issues.append(Issue("unsupported", r.get("reason", "passages do not support the claim")))


_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Conventional thresholds a critic may name without computing anything ("below 100%", "under 1.0").
_REFERENCE_POINTS = {"0", "1", "100"}


def _core(number: str) -> str:
    """'$14,066 million' -> '14066'; '26.90%' -> '26.9': digits only, so formatting never matters."""
    m = _TOKEN_RE.search(number)
    digits = m.group(0).replace(",", "") if m else ""
    return digits.rstrip("0").rstrip(".") if "." in digits else digits


def _computed_numbers(reason: str, claim: Claim, ledger: EvidenceLedger) -> list[str]:
    """Numbers in the critic's reason that appear in neither the claim nor its evidence."""
    sources = [claim.text] + [f.display for f in claim.figures]
    for eid in claim.evidence_ids + [f.evidence_id for f in claim.figures]:
        if (ev := ledger.get(eid)) is not None:
            sources += [ev.display or "", ev.text or ""]
    known = _REFERENCE_POINTS | {_core(t) for src in sources for t in _TOKEN_RE.findall(src)}
    return [n for n in numbers_in_text(reason) if _core(n) not in known]
