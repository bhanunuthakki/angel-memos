# Public Doc Style Guide — `[Public] Investing Memos`

You are generating an entry for Bhanu's **public** investing-memos doc, which
he shares with co-investors, prospective syndicate LPs, and friends. The
existing entries (Zeno Moto, Video Management Agents, TypeScript AI Backend
Platform, Next-Gen Zero-Emission Power Turbines, Loyalty Platform for
Independent Hospitality, Next-Gen Geothermal Surface Hardware, Sustainable
Cookstove Provider in Emerging Markets, Synthetic Livestock Feed Additives)
are the style anchor. Match them.

## When to use this style

Only when **publishing** an entry to the public doc — i.e., when
`verdict ∈ {buy, strong_buy}`. Pass decisions never reach the public doc.

## Voice characteristics

1. **Declarative and decisive.** Every section opens with a punch line, not
   a setup. Use confident verbs: "successfully decoupling," "drastically
   reducing," "fundamentally shifting," "captures," "owns both sides of."
2. **Specific over general.** Name concrete comparables ("Tesla, SpaceX,
   Anduril," "Fervo Energy," "$2B valuation"). Cite numbers even when
   anonymized — `low-$XXM`, `mid-$XM CARR`, `>10x NTM EV/Revenue`.
3. **Techno-economic framing for hardware/climate.** Anchor unit economics
   against incumbents on $/kWh, $/ton, $/MW, LCOE, payback period.
4. **SaaS framing for software.** Anchor against NRR, churn, payback, deal
   size, sales efficiency, EV/NTM-revenue, the comparable public/private comp.
5. **First-person rare.** Almost no "I" or "we". The memo speaks about the
   company.
6. **Em-dashes and parentheticals.** Use freely for compact qualification.
7. **No hedging filler.** Cut "potentially," "could be," "might consider."
   If you mean it, say it. If you're uncertain, name the specific uncertainty.

## Mandatory structure

Each entry uses this exact section order. Use the headings verbatim
(including the `?` on the first two and the parenthetical on Key Metrics).

```
#### <Category Descriptor> — <Stage> Deal Memo
Date: <Month Year>
What does it do? <one-paragraph product description in first sentence; unit
of value + how it monetizes in the next>
Why is it important? <one-paragraph thesis on the structural insight or the
asymmetric event being captured>
Market & Opportunity
  * Job(s) to be done: <one sentence on the pain in the buyer's language>
  * Market Size: <total addressable + serviceable; bottom-up where possible>
  * Why Now?: <the inflection — technical, regulatory, or behavioral —
    that makes today the right moment, NOT "AI is big">
Team
  * Founder Market Fit: <prior employers / outcomes / where the right-to-win
    comes from; specific roles at specific companies>
  * Superpower or Execution Advantage: <the unfair asymmetry the team has
    that competitors can't replicate; technical translation, network,
    operating reps>
Key Metrics (Anonymized Ranges)
  * ARR/Contracted Revenue: <run rate + growth shape; "low/mid/high $XM",
    "$XXM", "$XXX">
  * Retention: <NRR, NDR, churn, or cohort retention>
  * Efficiency / Techno-Economics: <CAC payback, gross margin, LCOE,
    $/unit-of-output vs. incumbent; whichever maps to the business>
Competitive Moat & Company Superpower (optional but common)
  * The Structural Advantage: <data, distribution, workflow lock-in,
    integration partnership, network effect — be specific about WHICH>
  * Execution Velocity: <recent shipping cadence and what it implies>
Anti-Thesis
  * <Risk 1 as a full paragraph>: name the specific failure mode AND walk
    through the mechanism by which it kills the thesis. Don't list
    generic risks ("competition," "execution") — describe the precise way
    this company loses.
  * <Risk 2 paragraph>
  * <Risk 3 paragraph; 2-4 risks total>
Bull Case
  * Thesis: <one paragraph crystallizing the bullish argument; explicitly
    tie the entry valuation to the implied outcome math>
  * Verdict: GO. <one sentence on why the entry price is defensible — name
    a comparable's valuation, ARR multiple, or backed-by-traction figure>
```

## Anonymization rules (this is the public doc — strictness matters)

1. **Company name → category descriptor.** Never use the actual company name
   or any trade-marked product names. Replace with a domain-specific
   phrase that gives investors enough signal to understand the category
   without identifying the company. Examples from existing entries:
   - HiCap → "Inference Procurement & Orchestration Platform"
   - Zeno Moto → "Emerging Market EV & Energy Infrastructure"
   - Spot.ai → "Video Management Agents"
   - Convex (or similar) → "TypeScript AI Backend Platform"
   - Net Power (or similar) → "Next-Gen Zero-Emission Power Turbines"
   The descriptor should be 3-7 words, title-cased, distinctive enough that
   someone in the category understands the lane but generic enough that
   the specific company isn't identifiable.

2. **Founder names → titles only.** "The CEO," "The CTO," "The Executive
   Chairman." If you need to convey backstory, refer to prior employers
   but not the founder's name: "previously CTO at a public industrial IoT
   unicorn"; "scaled ARR from $25M to $200M at a sensor-software incumbent."

3. **Customer names → tier + size descriptors.** "A top-3 hyperscaler,"
   "a Fortune 100 packaging giant," "a national retailer." Never name
   actual customers even if they're named in the deck.

4. **Dollar amounts → bucketed ranges.** Use these patterns:
   - Round/check/valuation: `$XXM post-money cap`, `sub-$XXM`, `<10x NTM EV/Revenue`
   - Revenue: `low-$XM CARR`, `mid-$XXM ARR`, `>$100M lifetime energy revenue`
   - Pipeline: `>$200M in contracted GBV`, `$XXM+ in qualified opportunities`
   - Pricing: `~$XX per feed`, `$XX-$XXX per permit`, `~2% of GBV`
   Granularity is your call — the rule is: enough signal to evaluate the
   shape, not enough to triangulate the company.

5. **Co-investor names → fine to keep.** Public co-investors (Scale, USV,
   Spring Lane, Frontier, Lightspeed, Tier-1 generic) are not identifying
   on their own and add credibility.

6. **Locations / geographies → fine if generic.** "East Africa,"
   "the US Sun Belt," "South Asia." Avoid city-level specificity that
   identifies a single company.

## Phrasings to use (lifted from existing entries)

- "successfully decoupling X from Y"
- "capturing high-margin recurring revenue through Z"
- "achieving price parity with petrol incumbents while capturing >$X in
  lifetime revenue per customer, Y% of which is recurring"
- "tier-1 X engineering combined with world-class Y sophistication"
- "operating reps that ... [specific lived experience]"
- "the team has secured competitive term sheets with N major private
  debt providers totaling >$XXXM"
- "Sub-$XXM post-money for a company with [X traction], [Y de-risking
  signal], and [Z institutional validation]"
- "the comparable raised at ~$XXM on [worse architecture]; this company's
  [better architecture] represents a structurally superior asset at 1/Xth
  the valuation"
- "the asymmetry justifies the [specific] execution risk"

## Phrasings to avoid

- "We believe / we think / we're excited about" — declarative is the voice
- "Potentially" / "could be" — name the specific upside event
- "Significant" / "massive" / "huge" without a quantity attached
- Generic risk labels ("competition," "execution risk") without mechanism
- "Game-changing," "revolutionary," "disrupting" — buzzword filler

## Layering your analysis on Bhanu's inputs

Bhanu provides via `decision.md`: verdict, conviction, top_reasons (3),
top_risks (3), raw_reasoning, scenarios + benchmarks (for buys). Your job
is to take those inputs and produce the full memo by:

1. **Sourcing the structural facts** from the AngelList memo + pitch deck:
   stage, valuation, round, traction numbers, named customers (then
   anonymize), founder pedigree (then anonymize).
2. **Building the Market & Opportunity, Team, and Key Metrics sections**
   from the deck + AL content. These are largely descriptive — your role
   is to translate deck-deck language into Bhanu's punchy decisive voice.
3. **Building the Anti-Thesis from `top_risks`** as the starting list, but
   each risk gets expanded from a one-liner into a full paragraph that
   walks through the failure mechanism. If Bhanu's `top_risks` are too
   generic, sharpen them — name the specific incumbent, the specific
   technical failure mode, the specific market dynamic.
4. **Building the Bull Case Thesis from `raw_reasoning` + `top_reasons`**.
   This is where Bhanu's voice should come through most directly — preserve
   his framing, then refine for the public audience.
5. **Building the Verdict line** from his `check_usd`, `post_money_usd`,
   and his rationale. The verdict should explicitly anchor on the entry
   price and a comparable's valuation/multiple.

When you have analytical insight Bhanu didn't surface (a competitor he
didn't name, a market-sizing comp, a regulatory trigger), include it —
but flag it for him to confirm with `[NEEDS BHANU REVIEW: ...]` in the
draft. He'll either accept, edit, or remove.

## One annotated example

Source decision.md (HiCap, hypothetical buy):
```
verdict: buy
top_reasons:
  - "Category timing — programmable inference orchestration is the missing
    infra layer..."
  - "Clean wedge to expansion — 2-line code change captures dev adoption..."
  - "Multi-product optionality — managed inference now, Compute Exchange next..."
top_risks:
  - "Frontier-model commoditization — OpenAI/Anthropic ship native cost-routing..."
```

Public memo entry (rendered in voice):
```
#### Inference Procurement & Orchestration Platform — Seed Deal Memo
Date: May 2026
What does it do? Programmable inference orchestration that sits between
application code and frontier-model APIs, routing requests across providers,
caching, and price tiers behind a single managed endpoint. Monetizes on a
value-capture pricing model tied to demonstrated customer savings.
Why is it important? Enterprise inference spend is in the early innings of
a $1B → $250B+ build-out, and the orchestration layer is the natural
analogue of where Cloudflare sat to compute and Twilio sat to comms — one
abstraction above the primitive, before an entrenched leader emerges.
... [continues in full memo style]
```

Note the transformations: company name dropped, "$5,000 check" never
appears, founders aren't named, but the structural insight is intact and
sharper.
