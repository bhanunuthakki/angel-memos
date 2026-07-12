# Angel Memos — Project Instructions

Layers on top of the global `~/.gemini/AGENTS.md`. This repo generates two-mode adversarial investment memos (private + masked public) for angel-stage deals, plus a stage-appropriate exit-math sheet, from materials dropped into a company folder on Google Drive.

## Architecture

The full pipeline: `extension capture → ingest → quick screen (diligence + score) → [deep research] → decide → memo`.

1. **Python CLI `angel-memos`** (`src/angel_memos/cli.py`):
   - `diligence <company>` → writes `diligence_topics.html` into the company folder.
   - `score <company>` → rubric scorecard (`scoring.py`): deterministic weighted factors (team pedigree, co-investor grades, terms-vs-comps) + LLM-judge subscores (market, traction/tech) with self-consistency and an adversarial critique pass → `score_report.json` + `score_report.md`. Auto-upgrades to tier=deep when `research_memo.md` exists. ADVISORY — never writes `decision.md`.
   - `memo <company>` → reads `decision.md` + materials, writes `memo_private.md`, `memo_public.md`, `exit_math.xlsx`, and appends to two Google Docs.
   - `ingest` / `watch` (`ingest.py`) → move Chrome-extension drops from `~/Downloads/angel-memos/<Company>/` into `Evaluation/<Company>/`; `watch` is the daemon that also auto-runs the quick tier (diligence + score) when the drop's `job.json` says `tier: quick`. The extension writes `job.json` LAST; it is the drop-completeness marker.
   - `investors backfill|export` (`investors.py`) → persistent cross-deal investor DB. SQLite at `~/.angel-memos/investors.db` — deliberately OUTSIDE Drive (Drive sync corrupts sqlite); readable view exported to the Drive root as `investors.md`. Records stale after 180 days; re-researched on next lookup.
2. **Project-local skills** (`skill/`):
   - `angel-decide` — conversational Q&A producing a schema-validated `decision.md`. Reads `score_report.json` and `research_memo.md` as advisory inputs when present. Runs *between* the quick screen and `memo`.
   - `angel-research` — deep-tier autonomous research (~30-60 min, launched deliberately from a Claude Code session). Fans out 4 parallel module subagents (tech diligence, techno-economic, Porter 5 forces, moat/incumbent-response), adversarially verifies load-bearing claims with skeptic agents, synthesizes `research_memo.md` per `skill/angel-research/memo_template.md` (BVP-style, hard-tech adapted: TRL risk register, mini-TEA, investment-shape milestone ladder), then re-runs `angel-memos score` at deep tier.
3. **Five xlsx templates** (`templates/`) — one per valuation method. `memo` populates the appropriate one based on `decision.valuation_method`.
4. **Chrome extension** (`extension/`, MV3, load-unpacked) — one-click capture on AngelList deal pages: prints the page to PDF as `angellist - <Company>.pdf`, downloads attachment links, writes `job.json` last into `Downloads/angel-memos/<Company>/`.

The CLI never asks questions. The skills never write the post-decision memos. The score never decides. Each tool lives in its native medium.

## Folder contract per company

```
<Evaluation|Portfolio>/<Company>/
  angellist*.pdf             # required input — terms + narrative; source of `stage`
  deck*.pdf                  # input — pitch deck (passed multimodal to Claude)
  *.md, *.txt                # input — your call notes, public-link lists
  diligence_topics.html      # Phase A output (quick screen)
  score_report.json          # `score` output — rubric scorecard, consumed by /angel-decide
  score_report.md            # `score` output — readable scorecard
  research_memo.md           # /angel-research output (deep tier; optional)
  decision.md                # /angel-decide output (YAML frontmatter + prose body)
  decision_review.md         # `review` output — adversarial pressure-test
  memo_private.md            # Phase B output
  memo_public.md             # Phase B output (masked)
  exit_math.xlsx             # Phase B output (omitted iff verdict==pass OR method==custom)
```

On invest, `mv Evaluation/<Company> Portfolio/<Company>`. No deeper nesting in `Portfolio/`.

Cross-deal state (outside the folder contract): `~/.angel-memos/investors.db` (sqlite, local-only) and `investors.md` exported next to the Evaluation/Portfolio roots.

## Domain rules

- **Stage** is extracted from the AngelList memo's TERMS table (`Round` field), never from `decision.md` or inferred from materials.
- **Valuation method** is per-deal, proposed by `/angel-decide` (Claude reasoning) and confirmed by you. One of: `arr_multiple`, `revenue_ebitda`, `revenue_pe`, `gmv_take`, `seed_outcome`, `custom`.
- **Benchmarks are required** for every decision except `verdict == pass` or `valuation_method == custom`. They anchor terminal-metric reach AND exit multiple to named comparables — no scenario priors without a real-world reference.
- **Scenario probabilities sum to 1.0** (validator-enforced; tolerance 1e-6).
- **Public memo** is mechanically derived from the private memo by string substitution of mask terms (company name, founder names from AL memo, check size, post-money valuation, any `private_only_terms` Claude tags during generation).

## Claude calls — subscription billing

Use the subscription wrapper per the root `CLAUDE.md` → "Calling Claude from Python" (mechanics + Windows gotchas live there, not here). This repo pins **`model="claude-opus-4-7"`** and wraps once in `src/angel_memos/claude.py`; downstream code calls that wrapper, not `claude_cli` directly. Never import `anthropic` / `claude_agent_sdk`.

> Note: the root `CLAUDE.md` lists Opus 4.8 as the current Opus-class ID. The `4-7` pin here is intentional only if you want memo-output stability across the model bump — otherwise update it.

## Testing

- Strict TDD per global `AGENTS.md`. Schemas have their tests; orchestration tests mock the Claude call.
- Golden fixtures live in `tests/fixtures/`:
  - `SpotAI_AL_Details.pdf` — AngelList parser fixture
  - `SpotAI_Exit_Math.xlsx` — `arr_multiple` template golden output
- Never assert on Claude prompt wording or memo copy. Test structural properties: schema validates, output non-empty, mask-list applied, expected files written.

## Pre-push checklist

1. `uv sync --extra dev` (plain `uv sync` PRUNES pytest/ruff/pyright from the venv)
2. `ruff format .`
3. `ruff check .`
4. `pyright`
5. `basedpyright`
6. `pytest`
