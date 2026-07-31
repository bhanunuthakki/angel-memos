# research_memo.md Template — BVP-style, hard-tech adapted

Section structure for the deep-research memo. Derived from BVP's published
memos (Rocket Lab, Velo3D, Twilio at bvp.com/memos) merged with hard-tech
diligence frameworks: Prime Movers Lab's technical-diligence pillars
(Validity / Requirements / Maturity / Defensibility), Engine Ventures'
four risk dimensions (technical, market, scale, regulatory), CTVC's FOAK
bankability criteria, and TEA/tornado-sensitivity practice.

BVP's own memos omit risks sections and exit math — those gaps are fixed
here deliberately. For software-only deals, mark §5/§8/§10's hard-tech
subparts "N/A (software)" rather than forcing them.

Every section: claims carry inline source citations
`[source: <url or description>, primary|secondary]` — the source type is
part of the citation, matching the skill's evidence contract, not an
optional flourish. Claims that failed adversarial verification appear with
`⚠ DISPUTED:` and the refuting evidence. No placeholder text ("TBD",
"$XX", "could not verify" without naming what was searched).

---

## 1. Deal Snapshot & Recommendation

One block, terms stated precisely (Velo3D style: "$8M of a $15M round at
$40.4M post fully diluted, 10% option pool"): round, instrument,
pre/post-money, allocation, fees/carry, lead + co-investors (with DB
grades), and the recommendation up front — one of GO / GO IF <specific> /
PASS / PASS UNLESS <specific> — plus the rubric score (quick + deep tier).

## 2. Company & Thesis in Three Sentences

What they make, for whom, and the one breakthrough that makes it possible
now. Must answer: "how does a breakthrough invention enable this startup?"

## 3. Market & Why Now

Quantified demand with a bottoms-up supply/demand gap (Rocket Lab counted
945 stranded satellites — that concreteness). Explicit why-now: what
changed in physics, cost curves, regulation, or supply chains — and why
this wasn't possible 5 years ago / won't be commoditized 5 years from now.
Porter 5-forces summary: buyer power, supplier power, entry barriers,
substitutes, rivalry — one paragraph each, verdict per force.

## 4. Technology: How It Works & What's Genuinely New

Lay explainer of the mechanism (Velo3D's "How 3D Metal Printing Works"
section is the model). Then the Prime Movers Lab validity check: does the
engineering plan respect scientific laws — state the theoretical limit
and how close to it the company must operate to hit its claimed
economics. Distinguish shipped capability from claimed capability, from
public evidence: docs, demos, repos, papers, patents, job postings.

## 5. Technical Risk Register + TRL (hard-tech core)

Current TRL (NASA 1-9) with the evidence supporting that level. Table of
remaining technical risks, each tagged:
  - type: science / engineering / manufacturing-scale-up
  - severity: kill-shot / margin-compression / delay
  - retirement: the experiment or milestone that retires it, its cost,
    and expected date
Key discipline: "solved" ≠ "demonstrated at relevant scale". The memo
must say which risks are RETIRED vs OPEN. If science risk is open, say so
in the recommendation — PML invests when the question is "how big?" not
"will it work?".

## 6. Traction & Demand Validation

Named customers graded by contractual bindingness:
LOI < paid pilot < recurring contract < offtake with fixed price AND
fixed volume (the FOAK bankability bar). Sanity-check arithmetic: stated
ARR / customer count / pricing must cohere. For FOAK-bound companies:
blue-chip validation present? (customers, insurers, EPCs).

## 7. Competition, IP & Moat

Competitor map including the incumbent-response path: why doesn't the
best-capitalized incumbent replicate this in 18 months once the market is
proven? IP substance, not IP count — what exactly is patented and what
does it block (Velo3D's process-signature patents are the model). Who in
the value chain profits from this company's success, and who resists it.
Data/switching-cost moat reality check for AI products: proprietary
capability vs model-API assembly.

## 8. Capex & Scaling Economics — mini-TEA (hard-tech core)

Unit economics at pilot vs FOAK vs NOAK: CapEx/OpEx per unit, cost-down
drivers, learning-rate assumption, and what makes plant/unit N cheaper
than unit 1 (modularity, standardization — or hope). Tornado-style
sensitivity: the 2-3 parameters that dominate viability, with RANGES not
point estimates, and the evidence for those ranges at commercial (not
pilot) scale. Total capital to first cash-generating asset. Act I → Act
II financing test: are assets standardizable/collateralizable, are cash
flows contracted, can ~50% of the capital plan be non-dilutive (grants,
project finance, prepayments)?

## 9. Team

Technical depth "inside the problem" (papers, national labs, major
programs), commercial/manufacturing scar tissue for scale-up, and the
gaps this round's hires must fill. Cite pedigree tiers from the cached
founder profiles; add anything the module research surfaced beyond them.

## 10. Investment Shape: Staged De-Risking Map (decision core)

The milestone ladder. For each milestone:
  (a) which named risk from §5 it retires
  (b) what structural advantage or valuation inflection it unlocks
      (Engine: "if you hit those milestones, you will unlock value")
  (c) cost and time to reach it
Then the round-sufficiency test: does THIS round's size, with margin,
reach the next inflection — or does it strand the company mid-milestone
(the deep-tech bridge-round trap)? State explicitly what the NEXT round's
investors must believe.

## 11. Exit Math & Dilution-Adjusted Returns (directional)

Scenario fan (probabilities sum to 1) anchored to named comparables, with
cumulative dilution modeled across the multi-round capital plan from §8
(deep tech: 20-30% per round is typical) so returns are on FINAL
ownership. This section is directional input for /angel-decide — the
committed scenario math still lives in decision.md + exit_math.xlsx.

## 12. Risks & Adversarial Case (pre-mortem)

Bold-header risk categories across Engine's four dimensions — technical,
market, scale, regulatory — plus financing risk. Steelman the PASS case
in its strongest form. List expert-validation steps that remain open
(Rocket Lab's thesis was "pending expert technical confirmation" — name
the equivalent here). Include every claim the verification round disputed.

## 13. Conclusion: Recommendation & What Would Change Our Mind

Restate the verdict, the 2-3 load-bearing assumptions, and explicit
tripwires: kill criteria and upgrade criteria tied to the §10 milestones.

---

## Adversarial questions the verify round must pressure-test

1. Is the remaining risk science risk or engineering risk — has the
   science been retired? (PML)
2. Does the engineering plan respect physical limits — how close to the
   theoretical limit must they operate to hit the economics? (PML)
3. What exactly does this round de-risk, and does the check reach that
   inflection — or is this underwriting a future bridge? (Engine/Allied)
4. Who has committed contractually — offtakes with fixed price and
   volume, or LOIs? (CTVC FOAK)
5. Are they preparing for Act II during Act I — standardizable,
   collateralizable assets and contracted cash flows? (Not Boring)
6. Can they stack ~50% non-dilutive capital, and what can they avoid
   owning? (Phil Morle / Main Sequence)
7. Which 2-3 TEA parameters dominate the outcome, and what's the
   evidence for their ranges at commercial scale? (Scenarionist)
8. Why now — and why won't an incumbent with 10x the capital do it once
   the market is proven? (PML / Lux timing lens)
9. What makes unit N cheaper than unit 1 — modularity, standardization,
   or hope? (CTVC/Sifted)
10. After full multi-round dilution, what do angels actually own at exit
    — and does the scenario math still clear venture returns on that
    stake? (Scenarionist)
