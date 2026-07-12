# Definitions

Canonical terminology for this repo. Use these terms verbatim in code (variables, types, functions, file names), comments, commit messages, and conversation. Propose additions before introducing a new domain term.

## Workflow

- **Phase A** — the `angel-memos diligence` command. Reads materials in a company folder, outputs `diligence_topics.md` with structured gaps to investigate.
- **Decide step** — the `/angel-decide` Claude Code skill. Conducts conversational Q&A about the user's thinking, writes `decision.md` to the company folder.
- **Phase B** — the `angel-memos memo` command. Reads materials + `decision.md`, outputs `memo_private.md`, `memo_public.md`, optionally `exit_math.xlsx`, and appends both Google Docs.

## Inputs

- **AngelList memo** — the PDF supplied by AngelList containing a TERMS table (round, pre-money, fees, carry, allocation, lead's investment) and narrative sections (problem, solution, traction, team, competitors, risks). Identified by filename containing `angellist` (case-insensitive).
- **Pitch deck** — the company's slide deck. Image-rich; passed multimodally to Claude rather than text-extracted.
- **Call notes** — free-form Markdown or text files capturing founder/reference call observations.

## Outputs

- **Diligence topics** — structured gaps and questions to investigate, one section per memo-section, with suggested sources for answers.
- **Decision** — YAML-frontmatter Markdown file capturing verdict, conviction, check size, post-money, valuation method, scenarios, benchmarks, future dilution, top reasons, top risks, raw reasoning.
- **Private memo** — full 9-section adversarial memo with identifiers intact and personal context (fund-size reasoning, position-size rationale) included.
- **Public memo** — string-substituted derivative of the private memo with mask terms redacted.
- **Exit math** — xlsx file populated from a method-specific template (or omitted iff `verdict == pass` or `valuation_method == custom`).

## Domain enums

- **Stage** — `pre_seed`, `seed`, `series_a`, `series_b`, `series_c`, `growth`. Extracted from the AngelList memo's `Round` field.
- **Verdict** — `strong_buy`, `buy`, `hold`, `pass`. The user's call.
- **Conviction** — `low`, `medium`, `high`. The user's confidence in the verdict.
- **Valuation method** — the formula used to derive exit value from terminal metrics. One of:
  - `arr_multiple` — terminal ARR × exit multiple (SaaS/software)
  - `revenue_ebitda` — terminal revenue × EBITDA margin × EV/EBITDA (mature operations-heavy)
  - `revenue_pe` — terminal revenue × net margin × P/E (mature profitable, public-comp benchmarked)
  - `gmv_take` — terminal GMV × take rate × revenue multiple (marketplaces)
  - `seed_outcome` — fixed dollar exit value per scenario (seed/pre-seed; no growth metric extrapolation)
  - `custom` — bespoke model; tool skips xlsx generation, memo gets a "model TBD" stub

## Domain entities

- **Scenario** — one row in the exit-math model. Fields vary by valuation method (e.g., `arr_multiple` scenarios have `cagr` and `exit_multiple`; `seed_outcome` scenarios have a fixed `exit_value_usd`). All scenarios share `name`, `probability`, and (except `seed_outcome`) `future_dilution`.
- **Benchmark** — one row in the comparables table that anchors a scenario. Fields vary by valuation method (e.g., `arr_multiple` benchmarks have `terminal_arr_usd` and `exit_multiple`). All benchmarks share `rank_label` (e.g., "Top 1", "Top 5", "Top 20"), `comparable` (the named company), and `exit_valuation_usd`. Required for every decision except `verdict == pass` or `valuation_method == custom`.
- **Category** — the market the company competes in (from AL memo `Markets` field). Used to scope benchmark selection.

## Out of scope

- ETFs, public-market positions — those live in the `earnings-summary` repo's portfolio tracker.
- Crypto, real estate, debt instruments.
- LP/GP fund analytics — this is for direct-investment memos only.
