# Angel Memos ingest contract

This document owns the durable behavior of `angel-memos ingest` and `angel-memos watch`.
`src/angel_memos/ingest.py` is executable authority; `extension/README.md` owns capture and watcher
operator instructions.

## Outcome

Move only complete extension drops from `~/Downloads/angel-memos/<Company>/` into the correct deal
folder without losing distinct source material, duplicating identical bytes, or combining rounds
known to be different. `watch` additionally runs the quick tier when the completed drop's
`job.json` requests `tier: quick`.

## Completeness and cleanup

- The extension writes `job.json` last. Its presence is the drop-completeness marker.
- A drop without `job.json` is incomplete, not litter; leave it and its files untouched.
- After an ingest pass, remove an inbox directory only when it is empty. Never prune a directory
  that still contains source files.

## Content identity

- Deduplicate by SHA-256 content, never by filename. If incoming bytes already exist in the target
  company folder, discard the duplicate capture instead of retaining a renamed copy.
- A same-named file with different bytes is distinct evidence and must be retained with a safe
  suffix. Never infer duplication from names alone.

## Round routing

The same company may return in later rounds, and each known-different round is a distinct deal.
Parse the round deterministically from the incoming AngelList memo's TERMS table. Compare its
canonical `Stage` with the destination's `.angellist_cache.json` stage when available, otherwise
with the destination AngelList memo.

- If positive evidence shows a different canonical stage, create a suffixed sibling such as
  `Acme (Series B)`.
- Canonically equivalent labels such as `Series C` and `Series C+` remain one round.
- If either round is unreadable, merge into the existing company folder. Do not split a deal on
  missing or ambiguous evidence; byte-level deduplication still protects identical files.

## State boundaries

Ingest writes company-folder material and inbox state. It does not decide the deal or write
`decision.md`, research synthesis, or post-decision memo artifacts. A watcher is an owned daemon:
report it as running rather than complete, and stop or explicitly hand it off when the task ends.

## Company-folder artifact and routing contract

```
<Evaluation|Portfolio|Passed>/<Company>/
  angellist*.pdf             # required input — terms + narrative; source of `stage`
  deck*.pdf                  # input — pitch deck
  *.md, *.txt                # input — your call notes, public-link lists
  diligence_topics.html      # Phase A output (quick screen)
  score_report.json          # `score` output — rubric scorecard, consumed by /angel-decide
  score_report.md            # `score` output — readable scorecard
  research_memo.md           # /angel-research output (deep tier; optional)
  decision.md                # /angel-decide output (YAML frontmatter + prose body)
  decision_review.md         # `review` output — adversarial pressure-test
  memo_private.md            # Phase B output
  private_entry.json         # structured private memo entry
  memo_public.md             # masked output (buy and strong_buy only)
  public_entry.json          # structured public entry (buys only)
  exit_math.xlsx             # output for buy, strong_buy, or hold; omitted for pass or custom
```

On `buy` or `strong_buy`, move `Evaluation/<Company>` to `Portfolio/<Company>`; on `pass`, move it to `Passed/<Company>`. No deeper nesting in those roots; a `hold` move remains an owner decision.

Cross-deal state (outside the folder contract): `~/.angel-memos/investors.db` (sqlite, local-only) and `investors.md` exported to the configured Drive root.
