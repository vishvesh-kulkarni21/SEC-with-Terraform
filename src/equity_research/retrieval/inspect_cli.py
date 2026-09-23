"""Inspect what each chunking strategy retrieves.

  python -m equity_research.retrieval.inspect_cli AAPL "What were Apple's total net sales in fiscal 2025?"
  python -m equity_research.retrieval.inspect_cli --questions eval/retrieval_questions.json
"""

import argparse
import json
from pathlib import Path

from equity_research.config import load_settings
from equity_research.data.edgar_client import EdgarClient
from equity_research.llm.factory import make_embedder
from equity_research.retrieval.chunkers import STRATEGIES
from equity_research.retrieval.index import Retriever


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", nargs="?")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--questions", type=Path, help="JSON question set; prints hit@k per strategy")
    parser.add_argument("-k", type=int, default=3)
    parser.add_argument("--chars", type=int, default=500, help="characters of each passage to show")
    args = parser.parse_args()

    settings = load_settings()
    retriever = Retriever(EdgarClient(settings.sec_user_agent, settings.cache_dir),
                          make_embedder(settings), settings.cache_dir)

    if args.questions:
        questions = json.loads(args.questions.read_text(encoding="utf-8"))["questions"]
        hits = {s: 0 for s in STRATEGIES}
        for q in questions:
            row = []
            for s in STRATEGIES:
                passages = retriever.search(q["ticker"], q["question"], s, k=args.k)
                hit = any(q["needle"].lower() in p.chunk.text.lower() for p in passages)
                hits[s] += hit
                row.append(f"{s}={'HIT ' if hit else 'miss'}")
            print(f"{q['ticker']:<5} {'  '.join(row)}  {q['question']}")
        print(f"\nhit@{args.k}: " + "  ".join(f"{s} {hits[s]}/{len(questions)}" for s in STRATEGIES))
        return

    if not (args.ticker and args.question):
        parser.error("give TICKER QUESTION, or --questions FILE")
    for s in STRATEGIES:
        print(f"\n===== {s} =====")
        for p in retriever.search(args.ticker, args.question, s, k=args.k):
            print(f"--- score {p.score:.3f} | {p.source}")
            print(p.chunk.text[:args.chars])


if __name__ == "__main__":
    main()
