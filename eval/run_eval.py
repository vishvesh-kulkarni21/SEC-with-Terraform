"""Evaluation harness CLI.

  python eval/run_eval.py stage1                     # chunkers (+ XBRL-tools baseline) on one model
  python eval/run_eval.py stage2 --strategy table    # models on the best chunker
  python eval/run_eval.py grid                       # chunker x model on a few companies
  python eval/run_eval.py debate --strategy table    # critic catch rate on planted errors
  python eval/run_eval.py report                     # write eval/results/RESULTS.md

Add --limit N --tag smoke to try a small slice first. Rows go to eval/results/*.jsonl;
reruns resume where they stopped.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equity_research.config import load_settings  # noqa: E402
from equity_research.data.edgar_client import EdgarClient  # noqa: E402
from equity_research.data.financials import CompanyFinancials  # noqa: E402
from equity_research.evaluation import report  # noqa: E402
from equity_research.evaluation.questions import build_questions  # noqa: E402
from equity_research.evaluation.runner import (  # noqa: E402
    EvalContext, QAConfig, ResultsFile, run_debate_grid, run_qa_grid)
from equity_research.llm.factory import make_chat_model, make_embedder  # noqa: E402
from equity_research.retrieval.chunkers import STRATEGIES  # noqa: E402
from equity_research.retrieval.index import Retriever  # noqa: E402

RESULTS = ROOT / "eval" / "results"
QA_KEY = ("qid", "condition", "model", "repeat")
DEBATE_KEY = ("ticker", "plant_kind", "condition", "model", "repeat")


def load_config():
    cfg = json.loads((ROOT / "eval" / "eval_config.json").read_text(encoding="utf-8"))
    companies = json.loads((ROOT / "eval" / "companies.json").read_text(encoding="utf-8"))["companies"]
    return cfg, [c["ticker"] for c in companies]


def make_context(cfg, model_ids):
    settings = load_settings()
    client = EdgarClient(settings.sec_user_agent, settings.cache_dir)
    retriever = Retriever(client, make_embedder(settings), settings.cache_dir)
    models = {m: make_chat_model(settings, m) for m in model_ids}
    return EvalContext(lambda t: CompanyFinancials.load(client, t), retriever, models,
                       cfg["prices_usd_per_million_tokens"], cfg["top_k"], RESULTS / "traces")


def questions_for(ctx, cfg, tickers, limit):
    qs = [q for t in tickers for q in build_questions(ctx.fin(t), cfg["question_metrics"], cfg["years_back"])]
    return qs[:limit] if limit else qs


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["stage1", "stage2", "grid", "debate", "report", "questions"])
    p.add_argument("--strategy", choices=STRATEGIES, help="chunker for stage2/debate (best from stage1)")
    p.add_argument("--limit", type=int, help="only the first N questions (or companies for debate)")
    p.add_argument("--repeats", type=int, help="override repeats")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--tag", default="", help="suffix for results files, e.g. smoke")
    a = p.parse_args()
    cfg, tickers = load_config()
    sfx = f"_{a.tag}" if a.tag else ""

    if a.stage == "report":
        write_report(sfx)
        return
    if a.stage in ("stage2", "debate") and not a.strategy:
        p.error(f"{a.stage} needs --strategy (the best chunker from stage1)")

    if a.stage in ("stage1", "debate", "questions"):
        model_ids = [cfg["stage1"]["model"]]
    else:
        model_ids = cfg["stage2"]["models"] if a.stage == "stage2" else cfg["grid"]["models"]
    ctx = make_context(cfg, model_ids)
    run_tickers = cfg["grid"]["companies"] if a.stage == "grid" else tickers

    if a.stage == "questions":
        for q in questions_for(ctx, cfg, run_tickers, a.limit):
            print(q.qid, "|", q.text, "|", f"{q.truth.value:,.0f}")
        return

    strategies = list(STRATEGIES) if a.stage in ("stage1", "grid") else [a.strategy]
    print(f"Warming up data and indexes: {len(run_tickers)} companies x {strategies}", flush=True)
    ctx.warm_up(run_tickers, strategies)

    if a.stage == "debate":
        d = cfg["debate"]
        results = ResultsFile(RESULTS / f"debate{sfx}.jsonl", DEBATE_KEY)
        debate_tickers = run_tickers[:a.limit] if a.limit else run_tickers
        run_debate_grid(ctx, debate_tickers, d["plant_kinds"], a.strategy, model_ids[0],
                        a.repeats or d["repeats"], results, workers=min(a.workers, 4))
        return

    qs = questions_for(ctx, cfg, run_tickers, a.limit)
    if a.stage == "stage1":
        repeats = a.repeats or cfg["repeats"]
        configs = [QAConfig(c, model_ids[0], r) for c in cfg["stage1"]["conditions"] for r in range(repeats)]
    elif a.stage == "stage2":
        repeats = a.repeats or cfg["repeats"]
        configs = [QAConfig(a.strategy, m, r) for m in model_ids for r in range(repeats)]
    else:  # grid
        repeats = a.repeats or cfg["grid"]["repeats"]
        configs = [QAConfig(c, m, r) for c in STRATEGIES for m in model_ids for r in range(repeats)]
    results = ResultsFile(RESULTS / f"{a.stage}{sfx}.jsonl", QA_KEY)
    print(f"{len(qs)} questions x {len(configs)} configs = {len(qs) * len(configs)} runs "
          f"({len(results.done)} already done)", flush=True)
    run_qa_grid(ctx, qs, configs, results, workers=a.workers)


def write_report(sfx):
    def rows(name, key=QA_KEY):
        return ResultsFile(RESULTS / f"{name}{sfx}.jsonl", key).rows()

    s1, s2, grid, debate = rows("stage1"), rows("stage2"), rows("grid"), rows("debate", DEBATE_KEY)
    out = ["# Evaluation results", "",
           "Generated by `python eval/run_eval.py report`. Metric definitions: docs/DECISIONS.md (Phase 6).", ""]
    if s1:
        out += ["## Stage 1: chunking strategy (fixed model)", "", report.qa_table(s1, ("condition",)), "",
                "### Error taxonomy by condition", "", report.taxonomy_table(s1, ("condition",)), "",
                "### By company and condition", "", report.qa_table(s1, ("ticker", "condition")), ""]
    if s2:
        out += ["## Stage 2: model (best chunker)", "", report.qa_table(s2, ("model",)), "",
                "### Error taxonomy by model", "", report.taxonomy_table(s2, ("model",)), ""]
    if grid:
        out += ["## Interaction check: chunker x model", "", report.qa_table(grid, ("condition", "model")), ""]
    if debate:
        out += ["## Critic catch rate on planted errors (full pipeline)", "", report.debate_table(debate), ""]
    failed = sum(1 for r in s1 + s2 + grid + debate if r.get("exception"))
    out.append(f"_Runs that raised an exception (excluded from tables, retried on rerun): {failed}_")
    path = RESULTS / f"RESULTS{sfx}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
