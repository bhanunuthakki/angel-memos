---
name: angel-decide
description: Conversational decision capture for an angel-stage deal. Loads materials and diligence topics from the company folder, conducts a structured Q&A with the user about their investment thinking, and writes a schema-validated `decision.md` to the folder. Run BETWEEN `angel-memos diligence` (Phase A) and `angel-memos memo` (Phase B).
---

# angel-decide

Conduct a structured Q&A to capture the user's investment decision and write a
schema-validated `decision.md` to the company folder.

## When to use

Invoke after the user has:
1. Run `angel-memos diligence <Company>` (Phase A)
2. Done their own diligence (calls, references, research)
3. Formed a recommendation but hasn't yet written it up

This skill produces the `decision.md` file that `angel-memos memo` (Phase B)
consumes.

## Inputs you should load

Resolve the company folder using the same logic as the CLI:
1. `Portfolio/<Company>/` if it exists (committed deals)
2. `Evaluation/<Company>/` otherwise

Default Drive roots (override from `~/.config/angel-memos/config.toml`):
- `G:\My Drive\Personal Finances\Angel Investing\Evaluation`
- `G:\My Drive\Personal Finances\Angel Investing\Portfolio`

Read for context:
- The AngelList memo PDF — surface stage, round size, pre-money, fees, carry,
  founders, co-investors
- `diligence_topics.html` if present — the gaps and questions from Phase A
  inform what to probe in Q&A
- `score_report.json` if present — the rubric scorecard (factor subscores,
  band, red flags). ADVISORY input: open the Q&A by presenting the score,
  its weakest factor, and its red flags, and pre-fill your proposed verdict
  from the band (strong_candidate → buy-leaning, consider → neutral,
  borderline/pass → pass-leaning). The user decides; if their verdict
  contradicts the band, probe once for the reason and capture it in
  `raw_reasoning` — never argue past that.
- `research_memo.md` if present — the deep-research memo. Use §10
  (Investment Shape) and §11 (directional exit math) to propose scenario
  probabilities and benchmarks, and §13's tripwires as candidate top_risks.

## Q&A flow

Walk down each branch of the design tree, settling dependent decisions in
order. 3-5 sharp questions per round, ordered so later answers depend on
earlier ones.

1. **Verdict**: `strong_buy` / `buy` / `hold` / `pass`
2. **Check size** (USD) + **post-money valuation** (USD)
3. **Conviction**: `low` / `medium` / `high`
4. **Valuation method** — propose one based on the business model:
   - SaaS / software with ARR -> `arr_multiple`
   - Hardware / ops-heavy with EBITDA path -> `revenue_ebitda`
   - Mature profitable / public-comp benchmarked -> `revenue_pe`
   - Marketplace -> `gmv_take`
   - Seed / pre-seed -> `seed_outcome` (fixed dollar exit values per scenario)
   - Bespoke / can't decompose cleanly -> `custom` (skips exit math)
   User accepts or overrides.
5. **Current base metric** (USD) — current ARR / Revenue / GMV. Required for
   growth methods; `null` for `seed_outcome` and `custom`.
6. **Five scenarios** — call them out by the standard names where useful:
   - Growth methods (any of arr_multiple, revenue_ebitda, revenue_pe, gmv_take):
     ask for `probability`, method-specific growth/multiple drivers, and
     `future_dilution` per scenario.
     - `arr_multiple` -> `cagr`, `exit_multiple`
     - `revenue_ebitda` -> `revenue_cagr`, `terminal_ebitda_margin`, `ev_ebitda`
     - `revenue_pe` -> `revenue_cagr`, `terminal_net_margin`, `pe_ratio`
     - `gmv_take` -> `gmv_cagr`, `take_rate`, `revenue_multiple`
   - `seed_outcome`: 5 named scenarios (`zero` / `acqui_hire` / `modest` /
     `breakout` / `generational`), each with `probability`, `future_dilution`,
     and a fixed `exit_value_usd`.
   Probabilities must sum to 1.0 (within 1e-3 tolerance).
7. **Benchmarks** — required unless `verdict == pass` or
   `valuation_method == custom`. 3-5 named comparable companies that anchor
   the scenarios:
   - `arr_multiple` benchmarks: `rank_label` ("Top 1" / "Top 5" / "Top 20"),
     `comparable`, `terminal_arr_usd`, `exit_multiple`, `exit_valuation_usd`
   - `revenue_ebitda`: `terminal_revenue_usd`, `ebitda_margin`, `ev_ebitda`
   - `revenue_pe`: `terminal_revenue_usd`, `net_margin`, `pe_ratio`
   - `gmv_take`: `terminal_gmv_usd`, `take_rate`, `revenue_multiple`
   - `seed_outcome`: just `rank_label`, `comparable`, `exit_valuation_usd`
8. **Top 3 reasons** for the call (exactly 3)
9. **Top 3 risks** being accepted (exactly 3)
10. **Free-form reasoning** — the user's raw thinking, multi-paragraph OK

For `pass` verdicts: skip scenarios + benchmarks + current_base_metric_usd
(set to `null`). Capture top_reasons and top_risks as the reasons for passing.
For `custom` valuation method: same — exit math gets skipped, memo gets a
"model TBD" stub.

## Output format

Write to `<folder>/decision.md` with YAML frontmatter that exactly matches the
`Decision` Pydantic schema in `angel_memos.models`. Any prose body after the
closing `---` is preserved but ignored by `angel-memos memo`.

Example:

```markdown
---
company: HiCap
verdict: buy
conviction: high
check_usd: 25000
post_money_usd: 30000000
valuation_method: seed_outcome
current_base_metric_usd: null
future_dilution: null
scenarios:
  - name: zero
    probability: 0.5
    future_dilution: 0.4
    exit_value_usd: 0
  - name: acqui_hire
    probability: 0.2
    future_dilution: 0.4
    exit_value_usd: 30000000
  - name: modest
    probability: 0.15
    future_dilution: 0.55
    exit_value_usd: 300000000
  - name: breakout
    probability: 0.1
    future_dilution: 0.65
    exit_value_usd: 3000000000
  - name: generational
    probability: 0.05
    future_dilution: 0.7
    exit_value_usd: 30000000000
benchmarks:
  - rank_label: "Top 1"
    comparable: ExampleCo
    exit_valuation_usd: 20000000000
  - rank_label: "Top 5"
    comparable: AnotherCo
    exit_valuation_usd: 800000000
  - rank_label: "Top 20"
    comparable: ThirdCo
    exit_valuation_usd: 60000000
top_reasons:
  - "Reason one"
  - "Reason two"
  - "Reason three"
top_risks:
  - "Risk one"
  - "Risk two"
  - "Risk three"
raw_reasoning: |
  Multi-paragraph reasoning here. Whatever helped form the call.
---
```

## Validation

Before writing the file, validate by constructing
`angel_memos.models.Decision.model_validate(parsed_yaml)` and surfacing any
ValidationError to the user with the field that failed. The memo command will
also revalidate at consumption time.
