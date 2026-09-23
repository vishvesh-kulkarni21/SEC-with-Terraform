# Design decisions

Each entry: what was chosen, what else was considered, and the one-line reason to give in an interview.

## Phase 1: Ground truth

### D1. Ground truth = "as most recently reported"
- **Chose:** for each fiscal year, use the value from the latest-filed 10-K that reports that year.
- **Alternatives:** as originally filed (the value in that year's own 10-K).
- **Why:** agents read the latest 10-K text, so that's the figure they should reproduce. An older value then counts as a "stale or restated" error, which is its own class in the taxonomy.
- **Seen in data:** J&J 2021 revenue is $78,740M as restated for the Kenvue separation. The original filing said $93,775M.

### D2. Pick annual facts by period dates, never by the `fy` field
- **Chose:** a 10-K or 10-K/A fact whose duration is 350–380 days.
- **Alternatives:** filter on `fy` and `fp == "FY"`.
- **Why:** `fy` is the fiscal year of the *filing*, not the period. A FY2024 10-K tags its FY2022 comparative as `fy=2024`, and it also contains Q4-only facts marked `fp=FY`. The 350–380 day window covers 52/53-week years (364 or 371 days).

### D3. Revenue tag: latest filing wins across tags, and priority only breaks ties
- **Chose:** gather candidates from every tag in the list and keep the latest-filed one for each period. If one filing reports the same period under two tags, the tag earlier in the list wins (`Revenues` first).
- **Alternatives:** walk the tags in priority order and take the first tag that has the period.
- **Why:** companies switch tags over time (Apple went from `SalesRevenueNet` to `Revenues` to the ASC 606 tag). Strict tag priority can pull an old value under an old tag, which breaks D1. `Revenues` wins ties because it usually means total revenue, while the ASC 606 tag can be a subset.

### D4. Fiscal-year label = the calendar year the period ends in, unless it ends Jan 1–7
- **Chose:** `end.year`, but a year ending in the first week of January counts as the previous year.
- **Alternatives:** the `fy` field (wrong for comparatives, see D2), or looking up each company's naming convention.
- **Why:** 52/53-week filers like J&J can end their "2022" on 2023-01-01. Walmart's year ends Jan 31 and it names the year after that end, which the rule handles. Retailers that end in early February (e.g. Target) would need a per-company override. They aren't in the company set.

### D5. The EDGAR cache never expires
- **Chose:** a cached response stays valid until someone calls with `refresh=True`.
- **Alternatives:** expire entries after a TTL.
- **Why:** the cache is a frozen data snapshot, so eval runs can be reproduced exactly.

### D6. Every value carries its source
- **Chose:** the `Fact` dataclass holds the tag, unit, accession number, form, filed date and period start/end.
- **Why:** design rule 2, every number must be traceable. The critic later checks claims against `Fact.source`.

### D7. Phase 1 tickers: AAPL, MSFT, JNJ
- **Why:** three different fiscal year-ends (September, June, and a 52/53-week December year) in three different sectors. J&J adds a real restatement.

## Phase 2: Deterministic tools

### D8. Every calculator returns a `Calculation` that keeps its inputs
- **Chose:** tools return `Calculation(name, value, unit, formula, inputs, fiscal_year, assumptions)`. The inputs are Facts or earlier Calculations, and `.facts()` walks back to the XBRL facts.
- **Alternatives:** return plain floats.
- **Why:** design rule 2. A DCF value per share can be traced to the exact cash flow, cash, debt and share-count facts behind it, so the critic can verify numbers produced by tools too.

### D9. Calculators validate their inputs and raise instead of guessing
- **Chose:** each tool checks for the same fiscal year, consecutive years for YoY growth, the expected line item (the margin denominator must be `revenue`), matching units, and non-zero denominators.
- **Why:** these checks mirror the error taxonomy (wrong period, wrong unit, wrong line item). If an agent passes the wrong inputs, it gets an error it can react to, not a plausible-looking wrong number.

### D10. Rates are ratios, never percents
- **Chose:** 0.2397, not 23.97.
- **Why:** mixing percents and ratios is one of the taxonomy's error classes ("wrong scale or unit"). One internal convention removes it, and formatting happens only at presentation.

### D11. Balance-sheet facts are accepted only at fiscal year-end dates
- **Chose:** fiscal year ends come from the revenue durations, and instant facts must fall on one of those dates.
- **Why:** a 10-K also carries balances at other dates, such as opening equity in the equity statement. Without this filter those could be mistaken for a year-end balance.

### D12. Metric definitions (simplifications stated up front)
- **Debt:** `long_term_debt` = `LongTermDebt`, which includes current maturities. Commercial paper and short-term borrowings are excluded because the tags are inconsistent across companies.
- **Equity:** `StockholdersEquity`, with a fallback to the tag that includes noncontrolling interest (J&J uses only that one).
- **ROE:** net income / average of opening and closing equity. Average equity is standard and doesn't flatter companies that shrink equity through buybacks within the year.
- **Missing metrics are reported as missing.** J&J has no operating income tag since 2014, so the tool raises `MissingMetricError` and the agent must say "not reported". It never substitutes pretax income.

### D13. Valuation = two-stage DCF on free cash flow, with no P/E
- **Chose:** FCF = operating cash flow − capex. Grow it for N years, add a Gordon terminal value, add cash, subtract debt, then divide by diluted shares.
- **Alternatives:** P/E or EV/EBITDA multiples.
- **Why:** multiples need a market price, and price isn't in XBRL. A price feed would add a dependency and a second ground-truth source. The agent picks the DCF assumptions (a judgment call) and the tool does the math. The assumptions are recorded in `Calculation.assumptions` so the critic can check they're reasonable.

### D14. Per-share figures are excluded from the evaluation
- **Why:** per-share figures shift across stock splits (Apple split 4:1 in 2020). EPS and share counts are extracted and usable, but the eval scores only company-level figures. That avoids a whole class of false "errors".

## Phase 3: Retrieval

### D15. Parse 10-K HTML into text and table blocks, each tagged with its Item
- **Chose:** BeautifulSoup + lxml. Hidden inline-XBRL content (`ix:header`, `display:none`) is dropped. Tables are rendered row by row as `cell | cell`, with SEC's split cells (`$` | `1,234`, `(321` | `)`) glued back together. The section is the most recent "Item N." heading.
- **Why:** a naive `get_text()` flattens tables into an unreadable stream of numbers, which is the exact failure the table-aware chunker exists to fix. The parser has to preserve table structure before any chunker can use it.

### D16. The three chunkers differ only in *where* they cut, not in chunk size
- **fixed:** a 350-word window with 50-word overlap over the whole document. The baseline.
- **section:** the same windows, restarted at each Item boundary. Tables can still be split.
- **table:** Item boundaries, whole paragraphs packed up to 350 words, and each table as its own chunk with its **caption** (the short text just above it, e.g. "(dollars in millions)"). Tables over 1,200 words are split between rows with the header row repeated.
- **Why:** holding size constant makes the comparison a controlled experiment. The caption matters because units and the statement name live *outside* the `<table>`, and without them a number's scale is ambiguous. That's the "wrong scale or unit" error class.
- **Size in words, not tokens:** no tokenizer dependency. At roughly 1.3 tokens per word, 350 words is about 450 tokens.

### D17. Embeddings: Gemini `gemini-embedding-001`, 768 dimensions, behind an `Embedder` protocol
- **Alternatives:** BM25 (keyword), or a local sentence-transformers model.
- **Why:** semantic retrieval stays on the same provider stack as the agents. The `Embedder` protocol keeps agent and retrieval code free of any SDK import (design rule 6). Document and query embeddings use different task types (`RETRIEVAL_DOCUMENT` vs `RETRIEVAL_QUERY`). Vectors are re-normalised because truncated Gemini embeddings aren't unit length.
- **One client, two backends:** the google-genai SDK reaches AI Studio (API key, local development) or Vertex AI (service account, Cloud Run) with a config change only.

### D18. No vector database: brute-force numpy cosine search, persisted to `.cache/indexes/`
- **Why:** one 10-K yields a few hundred chunks, so exact search takes microseconds. A vector DB would add a dependency and infrastructure for no gain at this size. Each 10-K is embedded once and cached, which also matters for free-tier rate limits.

### D19. Retrieval questions use "needles" to measure retrieval precision
- **Chose:** each question in `eval/retrieval_questions.json` has a needle string (e.g. `"416,161"`) that was verified to exist in the 10-K. A strategy scores a hit when any top-k passage contains it.
- **Why:** it's a cheap, objective retrieval-precision metric that needs no LLM judge. Numeric needles test exactly what the project cares about: did the chunk that carries the figure get retrieved?

### D20. Model access through Vertex AI, not an AI Studio key
- **Chose:** Vertex AI on the project's GCP account, authenticated with gcloud ADC locally and a service account on Cloud Run. `VERTEX_PROJECT` in `.env` switches the backend.
- **Why:** the AI Studio key landed on the paid prepay tier (it was created in a billing-enabled project), which returned 402. Vertex AI uses the GCP free-trial credits, has no daily free-tier cap to slow the eval down, and is the same path production uses. The code didn't change, only config (D17).

### D21. First retrieval check: don't tune on it
- **Observed (k=3, 14 questions):** fixed 14/14, section 13/14, table 12/14. At k=5 it was 14, 14 and 13.
- **Why the table chunker missed:** a pure number table embeds weakly. The Greater China table chunk was intact and captioned but ranked 4th, behind narrative text. Fixed and section windows mix a table with its surrounding explanation, which helps their similarity scores.
- **Decision:** leave the chunkers as designed. 14 questions is too few to conclude anything, and changing a chunker because of these questions would be tuning on the test set. Phase 6 measures the effect on the agents' actual numeric error rate, which is the metric that matters. If it holds up, "table-preserving chunks retrieve *worse* under pure embedding search" is itself a finding.

## Phase 4: One agent

### D22. ReAct through native function calling, with our own loop running the tools
- **Chose:** the model returns structured function calls. `run_agent` executes them, appends the results and loops until the model answers or hits the 12-step budget. Gemini's automatic function calling is turned off.
- **Alternatives:** text ReAct (parsing "Thought/Action/Observation" lines), or letting the SDK run the tools automatically.
- **Why:** native calls remove parsing errors. Running the loop ourselves means every call is traced, errors are caught, and a step budget stops runaway loops. `run_agent` is generic, so bull, bear and critic reuse it in Phase 5.

### D23. The evidence ledger: every tool output gets an ID the agent must cite
- **Chose:** facts are `F#`, calculations `C#`, passages `P#`. The agent writes "$391,035 million [F1]". Asking for the same fact twice returns the same ID.
- **Why:** design rules 2 and 3. A citation resolves to an XBRL fact (tag, accession number, period) or a filing chunk, so the critic in Phase 5 checks claims mechanically instead of trusting the model.

### D24. Python formats the numbers and the model copies the display string
- **Chose:** tools return a raw `value` plus a `display` string ("$391,035 million", "26.92%"), and the prompt says to quote `display` verbatim.
- **Why:** turning 391035000000 into "$391 billion", or 0.2692 into 26.92%, is arithmetic, and scale mistakes are an error class in the taxonomy. Python does the conversion once, correctly.

### D25. Tool errors go back to the model, not up the stack
- **Why:** "no revenue for FY2019; years available: 2024–2025" lets the agent recover or state the limitation. That's the self-correction behaviour ReAct is meant to show. A crash would lose the run.

### D26. Tracing: a contextvar conversation ID, with JSON lines on stderr plus `runs/<id>.jsonl`
- **Chose:** `with conversation():` binds an ID. `agent_start`, `llm_call` (tokens, latency), `tool_call` (args, result, latency) and `agent_end` all carry it. The fields `severity` and `message` follow Cloud Logging's structured-log format.
- **Why:** one ID filters an entire run, locally or in Cloud Logging (Phase 7), with no tracing dependency. Per-call token counts feed the cost metric in Phase 6.

### D27. Temperature 0 and a pinned model ID (`gemini-2.5-flash`)
- **Why:** it reduces run-to-run variance, which matters for a controlled comparison. It doesn't remove it, so Phase 6 still repeats every configuration.

### Open for Phase 6: the XBRL tools can bypass retrieval
- **Issue:** if the agent gets every figure from `get_fact`, the chunking strategy can't affect numeric accuracy. The first live run already showed the agent quoting net income from a passage, so both paths are in use.
- **Plan:** the chunker comparison uses questions whose answers live only in filing text (segment, product and geographic figures that aren't among the 16 XBRL metrics), plus a "text-only" mode where fact tools are disabled. The critic still checks against XBRL.

## Phase 5: Multi-agent

### D28. Bull and bear get no tools, only the researcher's evidence
- **Why:** the spec says they argue "using only what the researcher gathered". Without tools they can't fetch or invent new numbers, so every figure they use already has a ledger ID the critic can check. They return **structured claims** (JSON schema): the text, plus `figures[]` (each display string with its evidence ID) and `evidence_ids[]` for passages.

### D29. The critic has two layers: Python for numbers, the LLM for support
- **Deterministic (Python):** each figure is checked against the cited fact or calculation value, or must literally appear in the cited passage. Also checked: numbers in the text that aren't declared as figures, evidence IDs that don't exist, a fiscal year in the text that doesn't match the evidence's year, and stale periods.
- **LLM:** does the cited evidence support what the claim *concludes* (causes, risks, judgments)? The LLM is told to take numbers as given.
- **Why:** checking a number against ground truth is arithmetic (design rule 1) and must never depend on a model's judgment. Support for qualitative claims genuinely needs language understanding. Splitting them also shows *which* layer caught each error, which Phase 6 reports.

### D30. Tolerance = display rounding or 0.5% relative, whichever is looser
- **Why:** "26.9%" for 0.26916 is a correct rounding and must pass. "$416,161 billion" must fail. A correct number attributed to the wrong fiscal year fails too (`wrong_period`), as CLAUDE.md requires.

### D31. Block, then revise, then remove
- **Chose:** failed claims go back to the author with the critic's reasons, for up to 2 revision rounds. Anything still failing is removed and counted in the report ("N claims removed by the critic"). A missing LLM verdict counts as a failure, never a pass.
- **Why:** design rule 3, unverified claims never pass silently. Revision gives the author a chance to fix honest mistakes. Removal guarantees the output contains only verified claims.

### D32. Planted errors: perturb (+7%), scale (million↔billion, or ÷10), period (shift the year)
- **Why:** each kind maps to a taxonomy class (fabricated or wrong value, wrong scale, wrong period). Without planted errors, a high pass rate could just mean the critic passes everything. Live result: 6 of 6 caught across AAPL and MSFT, one plant per side for each kind.

### D33. Found in a live run: the model's sense of "now" overrode the tool
- **Observed:** `list_metrics` returned `latest_fiscal_year: 2026` for MSFT, and the researcher still analysed FY2022–2023. The critic passed the output because every number was correct *for those years*.
- **Fix, in code rather than prompts:** the orchestrator reads the latest fiscal year from the data and pins "FY2026 vs FY2025" in the task. The critic gets an `allowed_years` set, and figures outside it fail as `stale_period`.
- **Lesson for the write-up:** a stale period is invisible to value-level checking. It has to be enforced as a policy.

### D34. Calibrating the critic's LLM layer (two iterations)
- **First version:** it only checked claims that cited passages, so "the DCF shows the stock is attractive" (with no price available) slipped through.
- **Second version:** it checked everything but was too strict. It rejected "a lower current ratio means less liquidity", and was shown only an 80-character passage preview (a bug).
- **Final version:** explicit SUPPORTED/UNSUPPORTED rules. Definitional interpretations are allowed. Unstated causes, forecasts, valuation calls without a price, and comparisons missing a cited figure are rejected. The critic gets the full passage text and each calculation's formula.
- **Why it matters:** critic strictness is a precision/recall trade-off. Too lax lets unsupported claims through, and too strict removes valid analysis. Phase 6 measures it.

## Phase 6: Evaluation

### D35. The chunker comparison runs the agent in text-only mode, with an XBRL-tools baseline
- **Chose:** four conditions: `fixed`, `section` and `table` (the agent has only `search_filing` plus `submit_answer`), and `xbrl_tools` (the full toolset).
- **Why:** with XBRL tools available, the agent never needs retrieval for a reported figure, so the chunker couldn't affect numeric accuracy (the open issue logged in Phase 4). Text-only mode forces figures to come from chunks, so the chunker is the only variable. The tools baseline answers the bigger design question: how much does grounding numbers in structured data beat reading them from text?

### D36. Questions are generated from XBRL, so every one has ground truth
- **Chose:** 6 metrics (revenue, net income, operating income, operating cash flow, total assets, equity) × the 2 latest fiscal years × 8 companies, giving **90 questions**. Metrics a company doesn't report are skipped.
- **Why:** it's objective and needs no hand labelling. Everything can be answered from the latest 10-K (which shows 2 balance-sheet years and 3 income-statement years). The set was frozen in `eval/eval_config.json` before any run.

### D37. Structured answer capture through a `submit_answer` tool
- **Why:** the agent submits `{display, evidence_id, fiscal_year}` as a tool call. The answer is captured deterministically, with no second model call to extract it (which could add its own errors).

### D38. Metrics, as computed
- **Accuracy:** correct answers / runs. **Numeric error rate:** wrong / answered, since abstentions ("not found") are counted separately and aren't errors. It's reported with a Wilson 95% CI.
- **Unsupported rate:** answered runs where the cited evidence doesn't contain the figure.
- **Critic catch rate (natural errors):** the share of wrong answers the critic's deterministic layer would flag. **Critic catch rate (planted):** from the debate eval, by kind of planted error.
- **Retrieval hit:** did *any* passage the agent retrieved contain the ground-truth figure as the filing prints it (e.g. "416,161")? This separates retrieval failures from reading failures.
- **Latency:** p50 seconds per run. **Cost:** tokens × list price (in the config, to be verified), reported per run and **per correct answer**.

### D39. The taxonomy is checked in a fixed order, most specific first
- **Order:** scale, then stale/restated (matches an older reported copy of the same period), then wrong period, then wrong line item, then fabricated. "Not found" counts as an abstention.
- **Why:** one wrong number can match several explanations, and a fixed order makes the classification deterministic and reproducible. Every class is unit tested with J&J's real restatement.

### D40. Company set: 8 across sectors, with XOM swapped for CVX before any run
- **Why:** the XOM ticker now maps to a new SEC registrant (CIK 2115436) with no 10-K history, and swapping before any experiment isn't cherry-picking. Two fallback tags were added before any run (CAT net income, KO debt), and each only applies when the main tag is absent. Banks are excluded because their statements don't fit the metric set.

### D41. Repeats and parallelism
- **Chose:** 3 repeats at temperature 0, 8 worker threads, resumable JSONL rows, and indexes built before threads start.
- **Why:** temperature 0 still varies run to run, and repeats measure that variance. Building indexes first keeps embedding time out of latency and avoids races.
