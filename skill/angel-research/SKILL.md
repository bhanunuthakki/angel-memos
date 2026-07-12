---
name: angel-research
description: Deep-tier autonomous research on an angel deal. Fans out parallel subagents (tech diligence with browser clicking, techno-economic analysis, Porter 5 forces + market structure, moat & incumbent-response), adversarially verifies their top claims, and synthesizes a BVP-style hard-tech research_memo.md into the company folder. Run AFTER the quick screen (diligence + score), BEFORE /angel-decide. ~30-60 min, launched deliberately.
---

# angel-research

Produce `research_memo.md` — the pre-decision deep-research memo — for one
company folder, using parallel subagents and an adversarial verify round.

## When to use

The quick tier (`angel-memos diligence` + `angel-memos score`) said the deal
is worth real hours (band `consider` or better, or you have a specific
reason). This skill is the deliberate 30-60 minute deep pass. It does NOT
write decision.md (that's /angel-decide) and does NOT replace the post-
decision memos (that's `angel-memos memo`).

## Inputs

Resolve the company folder like the CLI: `Portfolio/<Company>/` first, then
`Evaluation/<Company>/` (default roots in `~/.config/angel-memos/config.toml`,
falling back to `G:\My Drive\Personal Finances\Angel Investing\...`).

Read before spawning anything:
- `.angellist_cache.json`, `.deck_content_cache.json`,
  `.founder_profiles_cache.json`, `.comparable_deals_cache.json` — the
  cached parses. If missing, tell the user to run
  `angel-memos diligence <Company>` first and stop.
- `diligence_topics.html` — the quick screen's kill conditions and open
  questions seed the module prompts.
- `score_report.json` — quick-tier scorecard (cite it in §1 of the memo).
- Any `*.md` / `*.txt` notes in the folder.

Build a shared CONTEXT BLOCK (~1-2K words) from these: company, stage,
terms, product, ICP, traction claims, founder tiers, comps, kill
conditions. Every subagent prompt embeds this block so agents don't
re-derive basics.

## Constraints (bind every subagent)

- **No founder access.** $2.5-5K syndicate check: no data room, no
  reference calls. Everything must come from public web + the materials.
  Rephrase inaccessible asks into checkable public proxies.
- **Sources or it didn't happen.** Every claim carries
  `[source: <url or description>]`. No "TBD", no "$XX", no "cannot
  verify" without naming what was searched and what came back.
- **Verdicts take sides.** GO / GO IF X / PASS / PASS UNLESS X / WATCH
  WHEN X. "Mixed signals" is an auto-fail.

## Orchestration

Use the Agent tool. Launch all four module agents IN ONE MESSAGE so they
run concurrently. General-purpose agents with WebSearch/WebFetch; the tech
diligence agent may also use the browser tools for clicking into docs,
demos, and sandboxes.

### Phase 1 — four parallel module agents (~10-20 min)

Each agent gets: the CONTEXT BLOCK, its module charter below, the
constraints above, and this output contract: *"Return structured markdown:
one `## Claim` block per major finding with `evidence:` (cited),
`confidence: high|medium|low`, and a final `## Module verdict` section.
Your final message is consumed programmatically — no preamble."*

1. **tech-diligence** — Click into the product: public docs, demo videos,
   sandbox/trial if self-serve, GitHub/npm/PyPI presence, changelog
   cadence, API reference depth, security/compliance pages, engineering
   job postings (stack + seniority signals), founder talks/papers/patents.
   Verdict: shipped depth vs vaporware; build vs wrapper risk; what is
   DEMONSTRATED vs CLAIMED. For hard tech: TRL estimate with evidence
   (per the template's §5 discipline).
2. **techno-economic** — Reconstruct unit economics from public evidence:
   COGS drivers (token costs for AI products, BOM/energy for hardware),
   pricing page vs claimed margins, cost at pilot vs FOAK vs NOAK scale,
   learning-rate assumptions, capital to first cash-generating asset,
   non-dilutive stackability (grants, project finance). Tornado: the 2-3
   parameters that dominate viability, with ranges and evidence.
3. **porter-market** — Porter 5 forces with a verdict per force, plus a
   bottoms-up TAM rebuild (buyer count × plausible ACV) tested against
   the deck's top-down claim. Why-now: what changed, why it wasn't
   possible 5 years ago, what commoditizes it in 5.
4. **moat-incumbent** — Red-team: why doesn't the best-capitalized
   incumbent (or a frontier lab, for AI) replicate this in 18 months once
   the market is proven? Switching costs, data-moat reality, IP substance
   (what's patented, what it blocks), value-chain allies vs resistors.

### Phase 2 — adversarial verify round (~5-10 min)

Collect each module's high/medium-confidence claims that are LOAD-BEARING
(would change the verdict if false). Spawn one skeptic agent per module
(4 in parallel), prompted to REFUTE: *"Attack these claims with fresh web
research. For each: CONFIRMED / DISPUTED (with refuting evidence) /
UNVERIFIABLE (what you searched). Default skeptical."* Also give each
skeptic the template's "Adversarial questions" list (memo_template.md) —
the ones relevant to its module.

Claims that come back DISPUTED are kept in the memo with `⚠ DISPUTED:` and
the refuting evidence — never silently dropped or silently believed.

### Phase 3 — synthesis (you, not a subagent)

Write `research_memo.md` in the company folder following
[memo_template.md](memo_template.md) — all 13 sections, hard-tech sections
marked "N/A (software)" where genuinely inapplicable. The Investment
Shape section (§10) is the decision core: milestone ladder, risk retired
per milestone, round-sufficiency test.

### Phase 4 — refresh the scorecard

Run `angel-memos score <Company>` (Bash). The score command detects
`research_memo.md` and re-judges at tier=deep with the memo as evidence.
Report the before/after score to the user, the memo's recommendation, and
the 2-3 tripwires from §13.

## Budget

Default: 8 subagents (4 modules + 4 skeptics). If the user asks for an
exhaustive pass, add a completeness-critic agent ("what's missing —
modality not searched, claim unverified?") and act on its output once.
