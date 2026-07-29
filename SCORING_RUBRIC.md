# Angel Memos Scoring Rubric

## Default contract: v2.2

The score is an advisory quick-screen signal. It answers whether the company
deserves further diligence; it does not decide whether to invest.

| Factor | Weight | What earns credit |
|---|---:|---|
| Team / founder-market fit | 20% | Relevant problem, buyer, technical, regulatory, and operating experience; pedigree is only half of this factor |
| Market / buyer structure | 15% | Venture-scale bottom-up opportunity, identifiable budget owner, credible timing, and a workable wedge |
| Commercial evidence | 20% | Stage-appropriate paid use, repeat behavior, retention, deployments, or contracts with reconciled arithmetic |
| Defensibility | 15% | An owned, compounding advantage appropriate to the archetype rather than roadmap claims or generic competence |
| Execution / capital scaling | 15% | Credible unit economics, dependencies, milestones, and capital plan through the next value-inflecting event |
| Co-investors | 15% | Relevant investment track record and demonstrated access; especially useful as a quality signal when the diligence file is thin |

Weights sum to 100%.

## Comparable valuations are not scored

V2.2 does not research, require, or weight comparable financing valuations.
This is deliberate: private-round comps are difficult to back-populate
consistently, often stale, and especially weak across unlike stages and
archetypes. Missing comps therefore cannot lower confidence, reduce score
coverage, or cap an otherwise Strong score.

Terms still matter to the investment decision. They move to `/angel-decide`
and the exit-math workflow, where the user confirms:

- check size and post-money valuation;
- valuation method;
- dilution and scenario probabilities;
- named operating and exit benchmarks when the decision schema requires them;
- fees, carry, and net return economics.

Separating the screen from return underwriting prevents an unavailable comp
set from masquerading as company-quality evidence without permitting an
investment decision that ignores price.

## Archetype anchors

Every company uses the same factors and weights. The evidence anchors change
by archetype:

- **AI software:** paid production usage, retention, workflow embed, data
  rights, proprietary evaluations, inference margin, implementation burden,
  and model-provider dependency.
- **Enterprise software:** production deployment, retention, switching cost,
  integration depth, gross margin, CAC payback, and enterprise sales capacity.
- **Marketplace:** completed transactions, repeat cohorts, liquidity density,
  net revenue, contribution margin, incentives, fraud, and disintermediation.
- **Hardware product:** paid field performance, duty-cycle validation,
  manufacturing process, certification, BOM, yield, warranty, service, and
  working capital.
- **Deep-tech infrastructure:** third-party benchmarks, qualification,
  physics/TRL evidence, freedom to operate, capex, facilities, yield, and
  financing sequence.
- **Regulated biotech:** preclinical/clinical evidence, endpoints, IP,
  regulatory path, manufacturing, reimbursement, burn, and cash to readout.
- **Hybrid:** both the software/data advantage and the physical deployment
  economics must work; strength in one cannot conceal weakness in the other.

## Evidence rules and gates

- Commercial arithmetic must reconcile. A material conflict caps Commercial
  Evidence at 50 until resolved.
- Two or more low-confidence core factors, or one critical missing core proof
  point, cap the effective band at Consider.
- A missing pitch deck makes the score provisional and caps the effective band
  at Consider.
- Raw and effective bands remain separate and visible.

Bands:

- Strong Candidate: 70 or higher
- Consider: 55 to 69.9
- Borderline: 40 to 54.9
- Pass: below 40

## Version history and rollback

- `v2.2` — current default; comp-free six-factor rubric with 15% co-investor weight.
- `v2.1` — historical comp-free contract with 5% co-investor weight.
- `v2` — historical archetype-aware contract with a 15% terms/return factor.
- `v1` — original five-factor rubric with a 20% terms/valuation factor.

Use `angel-memos score <company> --rubric-version v2.1`, `v2`, or `v1`
only to reproduce a historical score. New screens use v2.2.
