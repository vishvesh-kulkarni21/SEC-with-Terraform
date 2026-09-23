# Agentic Equity Research

A multi-agent system that researches a public company from its SEC filings and produces a **bull case and a bear case in which every number is verified against the company's filed XBRL data** before the output is accepted. The critic can block claims.

The project exists to answer one question with measurements:

> **How often do financial research agents get numbers wrong, and which retrieval strategy reduces it?**

The findings are in [`docs/REPORT.md`](docs/REPORT.md). Every design decision and its reasoning is in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Architecture

```
ticker
  │
  ▼
Data layer ─── EDGAR XBRL companyfacts (ground truth) + 10-K HTML, cached as a frozen snapshot
  │
  ▼
Tools ──────── deterministic calculators (growth, margins, ratios, DCF, change); the model never does arithmetic
  │
  ▼
Retrieval ──── 10-K parsed into text/table blocks → 3 chunkers (fixed / section / table-aware) → Gemini embeddings
  │
  ▼
Researcher ─── ReAct loop (native function calling); every tool result gets an evidence id [F1] [C2] [P3]
  │
  ├──▶ Bull ─┐   argue from the researcher's evidence only (no tools), as structured claims
  ├──▶ Bear ─┤
  │          ▼
  └──── Critic ── Python checks every number against XBRL/passages; the LLM checks qualitative support
                  → failed claims go back for revision → still failing = removed
                  │
                  ▼
        verified report + JSON trace (one conversation_id end to end)
```

## Design rules (enforced in code, not prompts)

1. **The model never does arithmetic.** Ratios, growth, margins, DCF and year-over-year changes come from Python tools. Numbers in an answer that match no evidence are flagged (`unverified_numbers`).
2. **Every number is traceable** to an XBRL fact (tag, accession, period) or a filing passage (accession, Item, chunk).
3. **The critic can block output.** Unverified claims are revised or removed, never passed silently.
4. **EDGAR etiquette:** a User-Agent header, under 10 requests/second, and a disk cache.
5. **No secrets in the repo.** On GCP, the model is reached through Vertex AI with a service account, so there are no API keys at all.
6. **The model provider sits behind an interface.** Swapping models is a config change.

## Headline results (Stage 1, Gemini 2.5 Flash, 90 questions × 8 companies × 3 repeats)

| condition | numeric error rate (95% CI) | alt. definition | accuracy |
|---|---|---|---|
| XBRL tools (structured data) | **0.0%** (0–1%) | 0.0% | 95.9% |
| fixed-window chunks | 0.8% (0–3%) | 4.4% | 93.3% |
| section-aware chunks | 2.7% (1–5%) | 3.7% | 91.1% |
| table-aware chunks | 4.5% (3–8%) | 2.6% | 91.9% |

The full tables, the error taxonomy, the model comparison and the critic catch rates are in [`eval/results/RESULTS.md`](eval/results/RESULTS.md).

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows; use .venv/bin on macOS/Linux
cp .env.example .env        # set SEC_USER_AGENT; VERTEX_PROJECT (gcloud ADC) or GEMINI_API_KEY
pytest                      # 94 tests, no network needed except the integration tests (cached)

python -m equity_research.agents.research_cli AAPL "How did Apple's net margin change in fiscal 2025?"
python -m equity_research.agents.debate_cli MSFT --plant scale      # plant a wrong number; watch the critic
python -m equity_research.retrieval.inspect_cli AAPL "Greater China net sales 2025"   # compare chunkers

python eval/run_eval.py stage1          # chunker comparison (resumable)
python eval/run_eval.py report          # → eval/results/RESULTS.md
```

## Deploy (GCP)

`infra/` is Terraform for Cloud Run: a private service (IAM invokers only), least-privilege runtime and build service accounts, Artifact Registry, and Vertex AI with no keys.

```bash
cd infra && cp terraform.tfvars.example terraform.tfvars   # fill in
terraform init && terraform apply -var deploy_service=false  # registry + service accounts
cd .. && gcloud builds submit --config cloudbuild.yaml --substitutions _TAG=$(git rev-parse --short HEAD) \
  --service-account projects/PROJECT/serviceAccounts/equity-research-build@PROJECT.iam.gserviceaccount.com
cd infra && terraform apply -var image_tag=$(git rev-parse --short HEAD)

curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H "Content-Type: application/json" \
  -d '{"ticker":"MSFT","question":"What was the net margin in fiscal 2026?"}' $URL/research
# Trace it: Cloud Logging → jsonPayload.conversation_id="<id from the response>"
```

## Layout

```
src/equity_research/
  data/        EDGAR client (cache, throttle), XBRL extraction, filings
  tools/       deterministic calculators
  retrieval/   10-K parser, three chunkers, vector index, inspection CLI
  agents/      researcher (ReAct), bull/bear, critic, evidence ledger, error planting
  llm/         provider interface + Gemini (AI Studio or Vertex AI)
  tracing/     conversation-id JSON logging (Cloud Logging format)
  evaluation/  questions, scoring + error taxonomy, parallel runner, reports
  api.py       FastAPI service for Cloud Run
eval/          frozen company set and config, run_eval.py, results
infra/         Terraform
docs/          DECISIONS.md (every design decision), REPORT.md (findings)
```
