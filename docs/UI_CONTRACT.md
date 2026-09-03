# Angel Memos interface contract

This contract owns the local dashboard and capture-overlay experience. The shared frontend
procedure supplies general composition and accessibility guidance; this file supplies the product
objective, state language, and exact executable authorities.

## Product outcome

The dashboard is a local, single-deal orchestration surface. It should let the owner see the next
valid pipeline action, inspect every generated artifact, and distinguish local generation from an
external publication. It is not an investment-recommendation dashboard and must not imply that an
advisory score decided the deal.

The extension overlay has one job: reliably capture the AngelList memo and optional deck into a
complete drop. It should stay visually subordinate to the host page and make single-flight,
progress, missing-deck, success, and failure states unmistakable.

## Executable authority

- `src/angel_memos/dashboard.py` owns dashboard HTML, styles, status labels, actions, and localhost
  behavior.
- `extension/content.js` owns the injected capture panel and its visible states.
- `extension/background.js` owns capture lifecycle and download routing, not presentation.
- `extension/README.md` owns installation and manual capture verification.

Keep presentation changes in those owners; do not copy a second set of interface rules into the
CLI, skills, or company-folder artifacts.

## Interaction contract

- Preserve the pipeline order: capture, quick brief, diligence and decision, memo and publication.
  Show an action as blocked, ready, running, partial, successful, or failed based on real artifacts
  and process state.
- Label local memo generation separately from Google Docs publication. Public publication is
  external and must never appear complete until privacy checks and exact-output human approval pass.
- Verdict-dependent artifacts must be truthful: public memo only for `buy` or `strong_buy`; exit
  math for non-`pass`, non-`custom` decisions, including `hold`.
- Keep one primary action per stage and retain the run log until the user can inspect success or
  failure. Do not hide a failed action behind a stale artifact state.
- Artifact links, deal names, filenames, and rendered Markdown are untrusted data. Escape them and
  preserve the top-level-file traversal boundary.
- The extension remains single-flight. Keep the panel out of captured PDFs, confirm the proposed
  company name, write `job.json` last, and give a specific recovery path when a deck cannot land.

## Required evidence

For a material dashboard change, render the deal list and one deal at 1440 × 900, exercise the
affected action through its terminal state, inspect its run log, and verify blocked and partial
states. For an extension change, load the unpacked extension on a supported AngelList page and
exercise Save only plus the affected success or failure path. Run the deterministic dashboard test
and JavaScript syntax gates named in `AGENTS.md`, and report any signed-in browser path that could
not be exercised.
