# Private Doc Style Guide — `[Private] Angel Investing`

You are generating an entry for Bhanu's **private** investing log. The
existing entries (Zeno Moto in Portfolio; [Passed] Anode, [Passed] Rumin8,
[Passed] Standard Metrics, [Passed] David Energy, [Passed] Enerflo,
[Passed] Rewbi, [Passed] Vesta/Planetary Tech, [Passed] Sami,
[Passed] GridSight in Passed Deals) are the style anchor. Match them.

## When to use this style

Always when publishing to the private doc — every decision (buy / hold /
pass) gets a private entry. The private doc is Bhanu's working ledger.

## Voice characteristics

1. **Terse and factual.** Bullets are one line each. Most are observations
   or facts, not paragraphs. The doc reads like a working journal.
2. **Mix of deal facts and substantive observations.** Bullets jump from
   `"$34M Pre"` to `"Pretty Rich"` to `"Co-Investors: Eclipse"` to a market
   observation in adjacent lines. This is intentional shorthand.
3. **No formal prose.** No "What does it do?", no "Bull Case" headers, no
   verdict line. The bullets ARE the analysis.
4. **First-person shorthand allowed.** "I just don't know the space super
   well." "we want a seed stage team based macro bets." "okay to leave some
   upside on the table." Honest, casual register.
5. **Specific dollar/multiple shorthand.** `$XXM Pre`, `$XXM Post`,
   `$XM ARR`, `7-8x LTM ARR multiple`, `>10x NTM revenue`. Real numbers,
   not bucketed ranges — this is the private doc.

## Mandatory structure

### Portfolio entries (verdict = buy / strong_buy)

```
### <Company>
#### Rationale
* <bullet>
* <bullet>
* ...
#### Risks
* <bullet>
* <bullet>
```

H3 = `<Company>` (no prefix).
H4 = `Rationale` and `Risks` (literal labels — not "Why Passing?", not "Bull Case").

### Passed-deal entries (verdict = pass / hold)

```
### [Passed] <Company>
#### Rationale
* <bullet>
* <bullet>
#### Why Passing?
* <bullet>
* <bullet>
```

H3 = `[Passed] <Company>` (prefix is literal, including the brackets).
H4 = `Rationale` then `Why Passing?` (not "Risks").

## What goes in each section

### `Rationale` (for both Portfolio and Passed)

Mix of:
- **Deal facts**: `$XXM Pre`, `$XXM ARR / $XXXM Post Money`, valuation multiple
- **Co-investors**: `Co-Investors: Congruent, Lowercarbon (led seed, super pro-rata at A)`. Name them. Note the lead.
- **Traction signals**: `NDR / GM / churn / customer count` numbers when known
- **Market or product observation**: `Massive Market`, `Effectively "virtual" energy provider`, `Software for utilities`, `Interesting business with network effects`
- **Team facts**: `East Africa experience + co-founder of ZoomCar (SPAC IPO, now penny stock) and Tesla charging lead in Africa`
- **Why it's interesting**: 1-2 bullets on the unfair angle

Each bullet stands alone — don't write transition prose between them.
A reader skimming should get the deal shape from 60 seconds of scanning.

### `Risks` (Portfolio only)

Failure modes you accepted by committing. Short. Examples from Bhanu's
Zeno Moto entry: just `Execution`. From other portfolio entries you'd see
2-4 bullets like `Capital intensity`, `Frontier model risk`, `Hyperscaler
bundling`. Not paragraphs.

### `Why Passing?` (Passed only)

The reasons for the no. Each bullet is one specific concern. Mix:
- **Valuation observations**: `Steep valuation`, `Rich valuation - Q1 2025 run-rate $21.5M → $240M post (11x multiple)`, `Pretty Rich`
- **Stage/process concerns**: `Outside ideal stage range - we want a seed stage team based macro bets`, `Already long this through Peak and Pila`
- **Specific operational concerns**: `Awful Glassdoor review which might be because of the shut down / layoffs`, `Limited info on GTM, progress, manufacturing advantage`
- **Honest self-assessment**: `Lots of unknown unknowns - I just don't know the space super well`, `Limited bandwidth to dig into MRV and exit pathways`
- **Strategic logic**: `Effectively okay to leave some upside on the table for more proof points`

## Phrasings to use (lifted from existing entries)

- "Co-Investors: <names>" (always lead with this when known)
- "Pretty Rich" / "Steep valuation" / "Rich valuation"
- "Effectively <metaphor>" — e.g., "Effectively 'virtual' energy provider"
- "Interesting business with <unique aspect>"
- "Outside ideal stage range — we want <X>"
- "Limited info on <specific gap>"
- "<X> at <Y> multiple, not unreasonable" / "not crazy, but rich"
- "Likely possible to capture upside with significantly more information when it becomes public company"

## Phrasings to avoid

- Full memo-style sections (no "Bull Case", no "Anti-Thesis", no "Market & Opportunity")
- Long prose paragraphs (1-2 sentences max per bullet; usually a fragment)
- Boilerplate verdict lines like "Conviction: medium · Check: $5,000"
- Generic risk labels without specificity (`competition`, `execution risk` alone — say what KIND)
- Buzzword filler (`paradigm-shifting`, `disruptive`, `revolutionary`)

## What NOT to anonymize (this is the private doc)

Everything is real. Real company names, real founder names if you have
them, real valuation, real check size, real co-investor names, real
customer references. The private doc is the working record — full fidelity.

## Layering your analysis on Bhanu's inputs

Bhanu provides via `decision.md`: verdict, top_reasons (3), top_risks (3),
raw_reasoning, scenarios + benchmarks (buys), conviction, check, post-money.

For the private entry, you produce:
1. **Rationale bullets**: combine the deal facts (from AngelListMetadata
   — pre-money, post, co-investors, allocation, founders, fees, carry,
   prior capital) with the substantive observations from `top_reasons`
   and `raw_reasoning`. The order is approximately: valuation/co-investors
   first, then market/product observation, then team, then 1-2 substantive
   bullets capturing the angle Bhanu sees.
2. **Risks bullets (Portfolio)** OR **Why Passing? bullets (Passed)**:
   condense `top_risks` (and `raw_reasoning` for the pass logic) into
   one-line observations. If Bhanu wrote a 3-sentence top_risk, distill
   it into a single phrase.

Where Bhanu's input is generic, sharpen it with the deck/AL specifics
you have — but keep the SHORTHAND register. Add `[NEEDS BHANU REVIEW: ...]`
for inferences that need confirmation.

## Annotated example: Portfolio entry

Source decision.md (Zeno Moto, hypothetical buy):
```
verdict: buy
check_usd: 5000
post_money_usd: 64000000
top_reasons:
  - "East Africa expertise + Tesla charging leadership"
  - "Energy network gross-margin positive at low scale"
  - "Massive market with structural cost parity"
top_risks:
  - "Execution risk on station rollout"
raw_reasoning: "Co-Investors are Congruent, Lowercarbon..."
```

Private Portfolio entry:
```
### Zeno Moto
#### Rationale
* Co-Investors: Congruent, Lowercarbon (led seed, and super pro-rata at Series A)
* Positive GP at relatively low scale for the battery swapping business
* Massive Market
* Founders / Team
* East Africa experience + co-founder of ZoomCar (SPAC IPO, now penny stock) and Tesla charging lead in Africa + Bolt + Ola
* Lots of battle scars with execution
* Combination of market + fintech (debt secured for charging and showing sophistication around financing of upfront purchase) + supply chain + chemistry/EV experience
* Approach exhibits a sophistication around localization + PMF with core users (ride hail drivers) + deep TEA/unit economics thinking + multi-phase plan
* Valuation: $19M round / $64M Post
#### Risks
* Execution
```

Note: Rationale leads with co-investors. Mix of one-line observations and
slightly longer combination bullets. Risks section is one word.

## Annotated example: Passed entry

Source decision.md (Anode-style pass):
```
verdict: pass
post_money_usd: 34000000
raw_reasoning: "Co-invested by Eclipse. Founders are CEO and VP Product
from Moxion (which shut down). Awful Glassdoor reviews including poor
management commentary. Valuation feels rich for the stage and team
baggage."
top_risks: ["Management quality concerns", "Valuation", "Same business as
failed predecessor"]
```

Private Passed entry:
```
### [Passed] Anode
#### Rationale
* $34M Pre
* Pretty Rich
* Co-Investors: Eclipse
* Same use case as Moxion basically, replacing Diesel Generators
#### Why Passing?
* Founding team were CEO and VP, Product at Moxion
* Awful Glassdoor review which might be because of the shut down / layoffs
* Lots of poor management reviews + people at Peak that note that Moxion was financially irresponsible — doesn't inspire confidence
* Rich valuation
```

Note: Each bullet stands alone. Honest commentary ("Awful Glassdoor review
which might be because of...") is preserved — don't sanitize Bhanu's voice
into neutral analyst-speak.
