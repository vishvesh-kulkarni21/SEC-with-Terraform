"""HTTP API for Cloud Run.

  GET  /health
  POST /research  {"ticker": "AAPL", "question": "...", "strategy": "fixed"}
  POST /debate    {"ticker": "AAPL", "strategy": "fixed", "plant": null, "format": "json" | "pdf"}

Every response carries the run's conversation_id; every log line of the run carries it
too (plus Cloud Logging's trace field), so one request can be followed end to end:
  jsonPayload.conversation_id="<id>"
"""

import os
import re
import tempfile
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from equity_research.agents.debate import render_markdown, run_debate
from equity_research.agents.pdf_report import render_pdf
from equity_research.agents.planting import PLANT_KINDS
from equity_research.agents.research_cli import build_tools
from equity_research.agents.researcher import research
from equity_research.config import load_settings
from equity_research.data.sec_api import UnknownTickerError
from equity_research.llm.factory import make_chat_model
from equity_research.tracing import conversation, log_event

app = FastAPI(title="Agentic Equity Research", version="0.1.0",
              description="Bull/bear equity research from SEC filings; every number verified against XBRL.")

Strategy = Literal["fixed", "section", "table"]
TICKER_RE = r"^[A-Za-z.\-]{1,10}$"


class ResearchRequest(BaseModel):
    ticker: str = Field(pattern=TICKER_RE, examples=["AAPL"])
    question: str = Field(min_length=5, max_length=500)
    strategy: Strategy = "fixed"  # lowest numeric error rate in the evaluation (D43)


class DebateRequest(BaseModel):
    ticker: str = Field(pattern=TICKER_RE, examples=["AAPL"])
    strategy: Strategy = "fixed"
    plant: Literal[PLANT_KINDS] | None = Field(None, description="Inject a wrong number to demo the critic")
    format: Literal["json", "pdf"] = Field("json", description="pdf returns the verified report as a PDF file")


@lru_cache(maxsize=1)
def _settings():
    return load_settings()


def _trace_fields(request: Request) -> dict:
    """Map Cloud Run's X-Cloud-Trace-Context header onto Cloud Logging's trace field."""
    header = request.headers.get("x-cloud-trace-context", "")
    project = _settings().vertex_project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    m = re.match(r"([0-9a-f]+)", header)
    if not (m and project):
        return {}
    return {"logging.googleapis.com/trace": f"projects/{project}/traces/{m.group(1)}"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research")
def research_endpoint(body: ResearchRequest, request: Request):
    settings = _settings()
    with conversation(extra=_trace_fields(request)) as cid:
        log_event("request", endpoint="/research", ticker=body.ticker, strategy=body.strategy)
        try:
            run = research(make_chat_model(settings), build_tools(settings, body.strategy),
                           body.ticker, body.question)
        except UnknownTickerError as e:
            raise HTTPException(404, str(e))
    return {
        "conversation_id": cid, "answer": run.answer, "unverified_numbers": run.unverified_numbers,
        "model": run.model_id, "steps": run.steps,
        "seconds": run.seconds, "tokens": {"input": run.usage.input_tokens, "output": run.usage.output_tokens},
        "evidence": [{"id": e.evidence_id, "label": e.label, "display": e.display, "source": e.source}
                     for e in run.ledger.items.values()],
    }


@app.post("/debate")
def debate_endpoint(body: DebateRequest, request: Request):
    settings = _settings()
    model = make_chat_model(settings)
    with conversation(extra=_trace_fields(request)) as cid:
        log_event("request", endpoint="/debate", ticker=body.ticker, strategy=body.strategy, plant=body.plant)
        try:
            tools = build_tools(settings, body.strategy)
            latest = tools.fin(body.ticker.upper()).latest_year
            report = run_debate(body.ticker, tools, model, model, model, plant_kind=body.plant)
        except UnknownTickerError as e:
            raise HTTPException(404, str(e))
    if body.format == "pdf":
        with tempfile.TemporaryDirectory() as tmp:
            path = render_pdf(report, tmp + "/report.pdf", body.strategy, model.model_id, (latest - 1, latest))
            content = path.read_bytes()
        return Response(content, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{report.ticker}_{cid}.pdf"',
            "X-Conversation-Id": cid})
    return {
        "conversation_id": cid, "report_markdown": render_markdown(report), "seconds": report.seconds,
        "tokens": {"input": report.usage.input_tokens, "output": report.usage.output_tokens},
        "sides": {side: {"claims": [c.to_dict() for c in r.claims],
                         "removed": [v.to_dict() for v in r.removed],
                         "revision_rounds": len(r.rounds) - 1,
                         "planted": r.plant.planted if r.plant else None,
                         "plant_caught": r.plant_caught}
                  for side, r in report.sides.items()},
    }
