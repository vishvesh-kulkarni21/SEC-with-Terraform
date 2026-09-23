# Agentic Equity Research: Project Brief

This file tells Claude Code what this project is, how I want to work, and the rules the code must follow. Read it fully before doing anything.

---

## What this project is

A multi-agent system that researches a public company from its SEC filings and produces a bull case and a bear case, where every number is verified against the company's filed financial data before the output is accepted.

The question the project exists to answer: **how often do financial research agents get numbers wrong, and which retrieval strategy reduces it?** The finished repo is the evidence. A written report on the measured results is the deliverable.

This is an upgrade of my existing project at github.com/vishvesh-kulkarni21 (Agentic Equity Research and Risk Assessment System). It is a personal project and must stay completely separate from my employer's work, clients, and domain. No compliance, model risk, AML, fraud, or document-review topics.

---

## How I want to work with you

**Mode changed 2026-09-23: build fast, then prep for interviews.** Time matters for the job application, so Claude builds the project end to end now. Interview prep comes after the build is done.

- **Claude writes the code, including core logic.** Keep moving phase by phase. Each phase still needs passing tests before the next one starts.
- **Record every design decision in `docs/DECISIONS.md` as you go.** Give each one what was chosen, the alternatives, and the one-sentence reason. That file becomes the interview prep material.
- **Only stop to ask when the decision is genuinely mine**, for example spending money, adding accounts or keys, or scope cuts. Otherwise pick the sensible default and log it.
- **Keep responses short.** Say what was built, what the tests show, and what's next.
- **After the build:** help me get interview ready by walking through the decisions log and the code, with explain-back questions.

---

## Non-negotiable design rules

These apply to every line of code in the project. Flag any violation you see, including in code I write.

1. **The model never does arithmetic.** Every ratio, growth rate, margin, and valuation figure comes from a deterministic Python function. Agents call those functions as tools and reason about the results. An LLM computing a number itself is a bug.
2. **Every number is traceable.** Any figure in the final output must carry a reference to where it came from: the XBRL fact, the filing, the period. No untraceable numbers reach the user.
3. **The critic can block output.** If the critic agent cannot verify a claim, that claim is removed or the case is sent back for revision. Unverified claims never pass silently.
4. **EDGAR etiquette.** Every request to SEC endpoints carries a `User-Agent` header with my name and email. Stay under 10 requests per second. Cache responses locally so we are not re-fetching the same data.
5. **No secrets in the repo.** API keys come from environment variables. `.env` is gitignored. Never hardcode a key, even temporarily.
6. **The model provider sits behind an interface.** No agent code imports a specific provider SDK directly. Swapping models must be a config change, because comparing models is part of the evaluation.

---

## Architecture

```
ticker
  |
  v
[Data layer]  EDGAR companyfacts (XBRL) + filing text, cached locally
  |
  v
[Tools]       deterministic calculators: growth, margins, ratios, valuation
  |
  v
[Retrieval]   10-K text, chunked three ways, indexed
  |
  v
[Researcher agent]  ReAct loop over tools and retrieval
  |
  +--> [Bull agent]  builds the long case
  +--> [Bear agent]  builds the short case
          |
          v
[Critic agent]  checks every claim against XBRL ground truth
          |
          v
   verified report  +  trace log (one conversation id end to end)
```

### Components

**Data layer.** Pulls structured financial facts from EDGAR XBRL, and filing text for retrieval. Normalizes tag variants, removes duplicates, and caches to disk.

**Tools.** Pure Python functions with typed inputs and outputs. Each one is unit tested against hand-checked numbers from a real 10-K.

**Retrieval.** Three chunking strategies over the same 10-K text, so they can be compared:
- *Fixed:* fixed token windows with overlap. The baseline.
- *Section-aware:* split on 10-K item boundaries (Item 1, 1A, 7, 7A, 8).
- *Table-aware:* keeps financial tables intact as single chunks, never split mid-table.

**Agents.**
- *Researcher:* gathers facts using tools and retrieval, ReAct style.
- *Bull and Bear:* each argue one side using only what the researcher gathered.
- *Critic:* verifies every numeric claim against ground truth and every qualitative claim against a retrieved passage. Returns a structured verdict per claim.

**Tracing.** A single conversation id follows each run through every agent step and tool call, logged in structured JSON.

---

## Phases

Each phase must work on its own, with tests, before the next starts. Do not skip ahead.

### Phase 1: Ground truth
Pull annual revenue and net income for any ticker from EDGAR.
**Done when:** five years of figures for three different tickers match each company's actual 10-K.

### Phase 2: Deterministic tools
Growth, margins, key ratios, a simple valuation.
**Done when:** every tool has unit tests against numbers checked by hand from a real filing.

### Phase 3: Retrieval
Fetch 10-K text, implement all three chunkers, build an index.
**Done when:** for a set of questions, each chunker retrieves passages, and I can inspect what each one returned.

### Phase 4: One agent
The researcher agent working alone with tools and retrieval.
**Done when:** it answers a question about a company correctly, and the trace shows every tool call it made.

### Phase 5: Multi-agent
Add bull, bear, and critic.
**Done when:** the critic demonstrably catches a planted wrong number and sends it back.

### Phase 6: Evaluation
The measurement harness. This is the point of the whole project.
**Done when:** it produces a results table comparing the three chunkers across a set of companies.

### Phase 7: Deploy
Deploy on GCP using my existing `agent-infra` Terraform project.
**Done when:** the system runs on Cloud Run and a request can be traced end to end in Cloud Logging.

### Phase 8: Write-up
A report on the measured results.
**Done when:** it states a clear finding, backed by the numbers from Phase 6.

---

## Known gotchas

Warn me about these when they become relevant, not all at once.

**XBRL tag variants.** Revenue alone appears as `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, and others depending on the company and year. Try a priority list of tags and record which one matched.

**Duplicate facts.** A single year's figure appears in that year's 10-K, again as a comparison in later 10-Ks, and in 10-Qs. Filter by `form`, `fp`, and `fy`, and decide deliberately which filing wins.

**Fiscal year is not calendar year.** Apple's fiscal year ends in September. Never assume December.

**Restatements and amendments.** A `10-K/A` can revise earlier numbers. Decide whether ground truth means "as originally filed" or "as most recently reported", and apply that rule consistently.

**Units and scale.** Facts carry units. Check them before any comparison.

**Share counts and splits.** Per-share figures shift across stock splits. Handle this explicitly or exclude per-share metrics from evaluation.

**Tables in filing text.** Raw 10-K HTML tables flatten into unreadable text if parsed naively. This is exactly why the table-aware chunker exists.

---

## Evaluation design

**Ground truth:** XBRL facts from EDGAR, which is the data the company legally filed.

**Metrics, per chunking strategy:**

| Metric | What it measures |
|---|---|
| Numeric error rate | Share of numeric claims that do not match ground truth within tolerance |
| Unsupported claim rate | Share of claims with no supporting source |
| Critic catch rate | Share of planted errors the critic flagged |
| Retrieval precision | Whether the retrieved chunk actually contained the needed figure |
| Latency | Seconds per full research run |
| Cost | Model cost per run |

**Tolerance:** define it explicitly. Rounding differences of 0.5% should not count as errors. A wrong fiscal year should.

**Planted errors:** deliberately inject wrong numbers into drafts to measure whether the critic catches them. Without this, a high catch rate means nothing.

**Company set:** a fixed list across sectors, chosen before running any experiment so results cannot be cherry-picked. Record the list in the repo.

**Error taxonomy (required).** Do not report a single error rate. Classify every numeric error, then report which chunker (and model) reduces which class:
- Wrong period (wrong fiscal year or quarter)
- Wrong scale or unit (thousands vs millions, percent vs ratio)
- Wrong line item (e.g., operating income reported as net income)
- Stale or restated figure (as-filed vs as-restated mismatch)
- Fabricated (no source supports the number at all)

The taxonomy is fixed before running experiments, like the company set.

**Model comparison: two-stage design.**
- *Stage 1:* compare the three chunkers on one fixed model.
- *Stage 2:* take the best chunker from stage 1 and compare models (e.g., Gemini Flash vs Pro).
- *Interaction check:* a small full grid (every chunker x every model) on 2 to 3 companies, to see whether chunker and model interact. If they do, that is a finding.

Fairness rules: same prompts, tools, questions, and temperature across models; only the model changes. Pin exact model IDs in config. Repeat each configuration several times, because single runs cannot separate signal from noise. Cost is computed as token counts x list price (the free tier costs $0) and reported as cost per correct answer.

---

## Stack

- Python 3.12
- `pytest` for tests
- `requests` for EDGAR
- A local vector store to start
- Model access: Gemini through a free Google AI Studio key, behind the provider interface so other models can be added for comparison
- Deployment: GCP Cloud Run through my `agent-infra` Terraform project

Keep dependencies minimal. Ask before adding a new one, and tell me why it is needed.

---

## Suggested layout

```
equity-research-agent/
  CLAUDE.md
  README.md
  pyproject.toml
  .env.example
  src/
    data/         EDGAR client, XBRL normalization, cache
    tools/        deterministic calculators
    retrieval/    fetch, chunkers, index
    agents/       researcher, bull, bear, critic
    llm/          provider interface and implementations
    tracing/      conversation id, structured logging
  eval/
    companies.yaml   fixed company set
    run_eval.py
    results/
  tests/
  infra/          pointer to or copy of agent-infra
```

---

## Progress

Update this as phases complete, so a new session knows where we are.

- [x] Phase 1: Ground truth (2026-09-23; `data/xbrl.py`, tests/test_ground_truth.py)
- [x] Phase 2: Deterministic tools (2026-09-23; `tools/calculators.py`, `data/financials.py`)
- [x] Phase 3: Retrieval (2026-09-23; `retrieval/`, inspect with `python -m equity_research.retrieval.inspect_cli`)
- [x] Phase 4: One agent (2026-09-23; `agents/researcher.py`, run with `python -m equity_research.agents.research_cli`)
- [ ] Phase 5: Multi-agent
- [ ] Phase 6: Evaluation
- [ ] Phase 7: Deploy
- [ ] Phase 8: Write-up

**Current phase:** 5 (design decisions are logged in docs/DECISIONS.md)
**Last decision made:** (2026-09-23) See docs/DECISIONS.md, the running log (D1–D27 cover Phases 1–4).
**Open questions:**
- `agent-infra` does not exist yet. GCP account created 2026-09-20; Terraform must be written from scratch in Phase 7. gcloud (SDK 585.0.0) and Terraform (1.16.2) installed 2026-09-21. gcloud and ADC authenticated; project `project-b3a2f493-623c-418f-818` (billing enabled, $20 budget alert). No default region set yet; plan is us-central1.
- Gemini is accessed through Vertex AI (VERTEX_PROJECT in .env; aiplatform API enabled 2026-09-23), billed to the $300 GCP credits. The AI Studio key returned 402 (paid prepay tier) and is unused.
- Phase 1 tickers: AAPL (Sept FY), MSFT (June FY), JNJ (Dec FY), chosen for three different fiscal year-ends and sectors. WMT (Jan FY) is a later stress test.
