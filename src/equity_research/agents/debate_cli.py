"""Run the full bull/bear/critic pipeline for a ticker.

  python -m equity_research.agents.debate_cli AAPL
  python -m equity_research.agents.debate_cli AAPL --plant perturb   # inject a wrong number

Writes the verified report to runs/<conversation_id>.md and the trace to runs/<conversation_id>.jsonl.
"""

import argparse

from equity_research.agents.debate import render_markdown, run_debate
from equity_research.agents.planting import PLANT_KINDS
from equity_research.agents.research_cli import build_tools
from equity_research.config import PROJECT_ROOT, load_settings
from equity_research.llm.factory import make_chat_model
from equity_research.retrieval.chunkers import STRATEGIES
from equity_research.tracing import conversation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("--strategy", choices=STRATEGIES, default="table")
    parser.add_argument("--model", help="override the configured chat model id")
    parser.add_argument("--plant", choices=PLANT_KINDS, help="plant one wrong number per side")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verbose", action="store_true", help="echo the JSON trace to stderr")
    args = parser.parse_args()

    settings = load_settings()
    model = make_chat_model(settings, args.model)
    runs = PROJECT_ROOT / "runs"
    with conversation(run_dir=runs, echo=args.verbose) as cid:
        report = run_debate(args.ticker, build_tools(settings, args.strategy), model, model, model,
                            plant_kind=args.plant, seed=args.seed)

    md = render_markdown(report)
    (runs / f"{cid}.md").write_text(md, encoding="utf-8")
    print(md)
    print("\n=== Critic log ===")
    for side, r in report.sides.items():
        if r.plant:
            print(f"{side}: planted ({r.plant.kind}): {r.plant.planted}\n"
                  f"      caught: {r.plant_caught}")
        for n, verdicts in enumerate(r.rounds):
            failed = [v for v in verdicts if not v.passed]
            print(f"{side} round {n}: {len(verdicts) - len(failed)} passed, {len(failed)} failed")
            for v in failed:
                print(f"   x {v.claim.text}\n     " + "; ".join(f"{i.code}: {i.detail}" for i in v.issues))
    print(f"\n{report.seconds}s, {report.usage.input_tokens}+{report.usage.output_tokens} tokens, "
          f"trace: runs/{cid}.jsonl")


if __name__ == "__main__":
    main()
