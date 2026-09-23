"""Aggregate raw eval rows into the results tables (markdown)."""

import math
import statistics
from collections import defaultdict

from equity_research.evaluation.scoring import ERROR_CLASSES


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "-"


def _ci95(n: int, d: int) -> str:
    """Wilson 95% interval, so small differences are not over-read."""
    if not d:
        return "-"
    p, z = n / d, 1.96
    centre = (p + z * z / (2 * d)) / (1 + z * z / d)
    half = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / (1 + z * z / d)
    return f"{100 * max(0, centre - half):.0f}-{100 * min(1, centre + half):.0f}%"


def _money(x: float) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:.4f}"


def qa_table(rows: list[dict], group_by: tuple[str, ...]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "qa" and not r.get("exception"):
            groups[tuple(r[g] for g in group_by)].append(r)

    head = (list(group_by) + ["runs", "accuracy", "alt. definition", "numeric error rate (95% CI)", "abstained",
                              "unsupported", "critic catch", "retrieval hit", "p50 s", "cost/run",
                              "cost/correct"])
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for key in sorted(groups):
        g = groups[key]
        n = len(g)
        answered = [r for r in g if r["outcome"] != "abstained"]
        errors = [r for r in g if r["is_error"]]
        correct = sum(r["correct"] for r in g)
        caught = sum(bool(r["critic_issues"]) for r in errors)
        hits = [r["retrieval_hit"] for r in g if r["retrieval_hit"] is not None]
        costs = [r["cost_usd"] for r in g if not math.isnan(r["cost_usd"])]
        total_cost = sum(costs) if costs else float("nan")
        lines.append("| " + " | ".join([
            *[str(k) for k in key], str(n),
            _pct(correct, n),
            _pct(sum(r["outcome"] == "alt_definition" for r in g), n),
            f"{_pct(len(errors), len(answered))} ({_ci95(len(errors), len(answered))})",
            _pct(n - len(answered), n),
            _pct(sum(not r["supported"] for r in answered), len(answered)),
            _pct(caught, len(errors)),
            _pct(sum(hits), len(hits)) if hits else "-",
            f"{statistics.median(r['seconds'] for r in g):.1f}",
            _money(total_cost / n if costs else float("nan")),
            _money(total_cost / correct if costs and correct else float("nan")),
        ]) + " |")
    return "\n".join(lines)


def taxonomy_table(rows: list[dict], group_by: tuple[str, ...]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "qa" and not r.get("exception") and r["is_error"]:
            groups[tuple(r[g] for g in group_by)].append(r)
    classes = list(ERROR_CLASSES) + ["unparseable"]
    head = list(group_by) + ["errors"] + classes
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for key in sorted(groups):
        g = groups[key]
        counts = [sum(r["outcome"] == c for r in g) for c in classes]
        lines.append("| " + " | ".join([*map(str, key), str(len(g)), *map(str, counts)]) + " |")
    return "\n".join(lines)


def debate_table(rows: list[dict]) -> str:
    by_kind: dict[str, list[dict]] = defaultdict(list)
    draft_claims = draft_failed = draft_unsupported = kept = removed = 0
    seconds, costs = [], []
    for r in rows:
        if r.get("kind") != "debate" or r.get("exception"):
            continue
        seconds.append(r["seconds"])
        costs.append(r["cost_usd"])
        for s in r["sides"].values():
            if s["plant_caught"] is not None:
                by_kind[r["plant_kind"]].append(s)
            draft_claims += s["draft_claims"]
            draft_failed += s["draft_failed"]
            draft_unsupported += s["draft_unsupported"]
            kept += s["kept"]
            removed += s["removed"]
    lines = ["| planted error | plants | caught | catch rate (95% CI) | caught by |", "|---|---|---|---|---|"]
    total = caught_total = 0
    for kind in sorted(by_kind):
        g = by_kind[kind]
        caught = sum(s["plant_caught"] for s in g)
        total, caught_total = total + len(g), caught_total + caught
        codes = defaultdict(int)
        for s in g:
            for c in set(s["caught_by_code"]):
                codes[c] += 1
        lines.append(f"| {kind} | {len(g)} | {caught} | {_pct(caught, len(g))} ({_ci95(caught, len(g))}) | "
                     + ", ".join(f"{c} {n}" for c, n in sorted(codes.items())) + " |")
    lines.append(f"| **all** | {total} | {caught_total} | {_pct(caught_total, total)} "
                 f"({_ci95(caught_total, total)}) | |")
    if seconds:
        lines += ["", f"Draft claims: {draft_claims}; failed first review: {_pct(draft_failed, draft_claims)} "
                      f"(unsupported by evidence: {_pct(draft_unsupported, draft_claims)}); "
                      f"final kept {kept}, removed {removed}.",
                  f"Debate latency p50 {statistics.median(seconds):.0f}s; "
                  f"cost per debate {_money(statistics.mean(costs))}."]
    return "\n".join(lines)
