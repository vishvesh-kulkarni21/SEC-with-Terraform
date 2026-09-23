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
