"""Run evaluation grids: QA runs (chunker x model x question x repeat) and debate runs.

Each finished run appends one JSON row to a results file; rerunning skips rows that
already exist, so an interrupted eval resumes where it stopped. Runs execute in a
thread pool; each run gets its own conversation id and trace file.
"""

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from equity_research.agents.claims import Claim, Figure, check_claim
from equity_research.agents.debate import run_debate
from equity_research.agents.researcher import RESEARCHER_PROMPT, run_agent
from equity_research.agents.tools import ResearchTools
from equity_research.data.financials import CompanyFinancials
from equity_research.evaluation.questions import Question
from equity_research.evaluation.scoring import ERROR_CLASSES, classify, retrieval_hit
from equity_research.llm.base import ChatModel, Usage
from equity_research.retrieval.index import Retriever
from equity_research.tracing import conversation

TEXT_QA_PROMPT = """You answer one factual question about a company's financial statements, using ONLY search_filing over its latest 10-K.

- Search for the figure; financial statement tables are the best source. Try a different query if the first results do not contain it.
- Tables state their units in a caption such as "(In millions)". Report the figure with its unit, e.g. "$416,161 million". Do not do any arithmetic or rescaling beyond attaching the stated unit.
- Tables show several fiscal years side by side: make sure you read the column for the fiscal year asked.
- Then call submit_answer with the figure, the evidence_id of the passage it came from, and the fiscal year.
- If you cannot find the figure after a few searches, call submit_answer with display "not found"."""

TOOLS_QA_PROMPT = RESEARCHER_PROMPT + """

This is a single factual question. When you have the figure, call submit_answer with its display string exactly as the tool returned it, its evidence_id, and the fiscal year."""

XBRL_TOOLS = "xbrl_tools"  # condition name for the tool-grounded baseline


@dataclass(frozen=True)
class QAConfig:
    condition: str  # a chunking strategy ("fixed", "section", "table") or XBRL_TOOLS
    model_id: str
    repeat: int


def cost_usd(usage: Usage, model_id: str, prices: dict) -> float:
    p = prices.get(model_id)
    if not p:
        return float("nan")
    return (usage.input_tokens * p["input"] + usage.output_tokens * p["output"]) / 1e6


class ResultsFile:
    """Append-only JSONL with resume support; safe to write from several threads."""

    def __init__(self, path: Path, key_fields: tuple[str, ...]):
        self.path, self.key_fields = path, key_fields
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.done = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if not row.get("exception"):
                    self.done.add(self.key(row))

    def key(self, row: dict) -> tuple:
        return tuple(row[k] for k in self.key_fields)

    def append(self, row: dict) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
            if not row.get("exception"):
                self.done.add(self.key(row))

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines()]


class EvalContext:
    """Shared, read-mostly state: financials and retrieval indexes are built once."""

    def __init__(self, load_financials: Callable[[str], CompanyFinancials], retriever: Retriever,
                 models: dict[str, ChatModel], prices: dict, k: int, trace_dir: Path):
        self.load_financials, self.retriever, self.models = load_financials, retriever, models
        self.prices, self.k, self.trace_dir = prices, k, trace_dir
        self.financials: dict[str, CompanyFinancials] = {}

    def fin(self, ticker: str) -> CompanyFinancials:
        if ticker not in self.financials:
            self.financials[ticker] = self.load_financials(ticker)
        return self.financials[ticker]

    def warm_up(self, tickers: Iterable[str], strategies: Iterable[str]) -> None:
        """Load data and build every index before threads start (avoids races and
        keeps index-building time out of the latency measurements)."""
        for t in tickers:
            self.fin(t)
            for s in strategies:
                self.retriever.index(t, s)

    def tools(self, strategy: str, mode: str) -> ResearchTools:
        return ResearchTools(self.load_financials, self.retriever, strategy, self.k, mode,
                             financials_cache=self.financials)


# --- QA runs --------------------------------------------------------------------------

def run_qa(ctx: EvalContext, q: Question, cfg: QAConfig) -> dict:
    text_mode = cfg.condition != XBRL_TOOLS
    tools = ctx.tools(cfg.condition if text_mode else "table", "text" if text_mode else "qa")
    model = ctx.models[cfg.model_id]
    row = {"kind": "qa", "qid": q.qid, "ticker": q.ticker, "metric": q.metric, "fiscal_year": q.fiscal_year,
           "condition": cfg.condition, "model": cfg.model_id, "repeat": cfg.repeat,
           "truth": q.truth.value, "truth_source": q.truth.source}
    with conversation(run_dir=ctx.trace_dir, echo=False) as cid:
        run = run_agent(model, tools, TEXT_QA_PROMPT if text_mode else TOOLS_QA_PROMPT,
                        f"Company: {q.ticker}\nQuestion: {q.text}", "qa", max_steps=8)
    answer = tools.submitted or {}
    display, eid = answer.get("display"), answer.get("evidence_id", "")
    outcome = classify(display or "", q, ctx.fin(q.ticker), tools.retrieved)

    # What the critic's deterministic layer would say about this answer as a claim.
    critic_issues = []
    if outcome not in ("abstained",):
        claim = Claim(f"{q.metric} in fiscal {q.fiscal_year} was {display}.", [Figure(display or "", eid)])
        critic_issues = [i.code for i in check_claim(claim, tools.ledger)]

    row.update({
        "conversation_id": cid, "answer": display, "evidence_id": eid,
        "answer_fiscal_year": answer.get("fiscal_year"),
        "outcome": outcome, "correct": outcome == "correct",
        "is_error": outcome in ERROR_CLASSES or outcome == "unparseable",
        "supported": outcome != "abstained" and not critic_issues,
        "critic_issues": critic_issues,
        "retrieval_hit": retrieval_hit(q, tools.retrieved) if text_mode else None,
        "retrieved_sources": [e.source for e in tools.ledger.items.values() if e.kind == "passage"],
        "searches": sum(1 for c in run.tool_calls if c["tool"] == "search_filing"),
        "steps": run.steps, "stopped_reason": run.stopped_reason, "seconds": run.seconds,
        "input_tokens": run.usage.input_tokens, "output_tokens": run.usage.output_tokens,
        "cost_usd": cost_usd(run.usage, cfg.model_id, ctx.prices),
    })
    return row


def run_qa_grid(ctx: EvalContext, questions: list[Question], configs: list[QAConfig],
                results: ResultsFile, workers: int = 6, progress: bool = True) -> None:
    tasks = []
    for cfg in configs:
        for q in questions:
            key = (q.qid, cfg.condition, cfg.model_id, cfg.repeat)
            if key not in results.done:
                tasks.append((q, cfg))
    _run_parallel(tasks, lambda t: run_qa(ctx, *t), results, workers, progress,
                  lambda t: {"kind": "qa", "qid": t[0].qid, "ticker": t[0].ticker, "condition": t[1].condition,
                             "model": t[1].model_id, "repeat": t[1].repeat})


# --- debate runs ------------------------------------------------------------------------

def run_debate_case(ctx: EvalContext, ticker: str, plant_kind: str, strategy: str, model_id: str,
                    repeat: int) -> dict:
    model = ctx.models[model_id]
    tools = ctx.tools(strategy, "full")
    with conversation(run_dir=ctx.trace_dir, echo=False) as cid:
        report = run_debate(ticker, tools, model, model, model, plant_kind=plant_kind, seed=repeat)
    sides = {}
    for side, r in report.sides.items():
        first = r.rounds[0]
        planted_codes = [i.code for i in first[r.plant.claim_index].issues] if r.plant else []
        sides[side] = {
            "planted": r.plant.planted if r.plant else None,
            "plant_caught": r.plant_caught,
            "caught_by_code": planted_codes,
            "draft_claims": len(first),
            "draft_failed": sum(not v.passed for v in first),
            "draft_unsupported": sum(any(i.code in ("unsupported", "critic_arithmetic") for i in v.issues)
                                     for v in first),
            "rounds": len(r.rounds), "kept": len(r.claims), "removed": len(r.removed),
        }
    return {"kind": "debate", "ticker": ticker, "plant_kind": plant_kind, "condition": strategy,
            "model": model_id, "repeat": repeat, "conversation_id": cid, "sides": sides,
            "seconds": report.seconds, "input_tokens": report.usage.input_tokens,
            "output_tokens": report.usage.output_tokens,
            "cost_usd": cost_usd(report.usage, model_id, ctx.prices)}


def run_debate_grid(ctx: EvalContext, tickers: list[str], plant_kinds: list[str], strategy: str,
                    model_id: str, repeats: int, results: ResultsFile, workers: int = 3,
                    progress: bool = True) -> None:
    tasks = [(t, k, r) for t in tickers for k in plant_kinds for r in range(repeats)
             if (t, k, strategy, model_id, r) not in results.done]
    _run_parallel(tasks, lambda t: run_debate_case(ctx, t[0], t[1], strategy, model_id, t[2]),
                  results, workers, progress,
                  lambda t: {"kind": "debate", "ticker": t[0], "plant_kind": t[1], "condition": strategy,
                             "model": model_id, "repeat": t[2]})


def _run_parallel(tasks, fn, results: ResultsFile, workers: int, progress: bool, describe) -> None:
    if not tasks:
        print("Nothing to run: all rows already present.")
        return
    start, done = time.perf_counter(), 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, t): t for t in tasks}
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception as e:  # keep going; the row records the failure and is retried next time
                row = {**describe(futures[fut]), "exception": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc(limit=3)}
            results.append(row)
            done += 1
            if progress and (done % 10 == 0 or done == len(tasks)):
                rate = done / (time.perf_counter() - start)
                print(f"  {done}/{len(tasks)} runs, {rate * 60:.1f}/min, "
                      f"eta {(len(tasks) - done) / rate / 60:.1f} min", flush=True)
