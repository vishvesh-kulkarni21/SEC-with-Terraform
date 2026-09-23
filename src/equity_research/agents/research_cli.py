"""Ask the researcher agent a question.

  python -m equity_research.agents.research_cli AAPL "How did Apple's net margin change in fiscal 2025?"

The JSON trace streams to stderr and is saved to runs/<conversation_id>.jsonl.
"""

import argparse

from equity_research.agents.researcher import research
from equity_research.agents.tools import ResearchTools
from equity_research.config import PROJECT_ROOT, load_settings
from equity_research.data.edgar_client import EdgarClient
from equity_research.data.financials import CompanyFinancials
from equity_research.llm.factory import make_chat_model, make_embedder
from equity_research.retrieval.chunkers import STRATEGIES
from equity_research.retrieval.index import Retriever
from equity_research.tracing import conversation


def build_tools(settings, strategy: str, k: int = 5) -> ResearchTools:
    client = EdgarClient(settings.sec_user_agent, settings.cache_dir)
    retriever = Retriever(client, make_embedder(settings), settings.cache_dir)
    return ResearchTools(lambda t: CompanyFinancials.load(client, t), retriever, strategy, k)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("question")
    parser.add_argument("--strategy", choices=STRATEGIES, default="table")
    parser.add_argument("--model", help="override the configured chat model id")
    parser.add_argument("--quiet", action="store_true", help="don't echo the trace to stderr")
    args = parser.parse_args()

    settings = load_settings()
    tools = build_tools(settings, args.strategy)
    model = make_chat_model(settings, args.model)

    with conversation(run_dir=PROJECT_ROOT / "runs", echo=not args.quiet) as cid:
        run = research(model, tools, args.ticker, args.question)

    print(f"\n=== Answer ({run.model_id}, {run.steps} steps, {run.seconds}s, "
          f"{run.usage.input_tokens}+{run.usage.output_tokens} tokens) ===\n{run.answer}\n")
    print("=== Evidence ===")
    for ev in run.ledger.items.values():
        print(f"[{ev.evidence_id}] {ev.label}: {ev.display}\n      {ev.source}")
    print(f"\nconversation_id: {cid}  trace: runs/{cid}.jsonl")


if __name__ == "__main__":
    main()
