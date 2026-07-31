"""Phase A: surface diligence topics from a company folder.

Pipeline:
  1. Load materials (AL memo PDF, deck PDF, notes).
  2. Parse AL terms + founders from the AL/deck PDFs (focused vision calls
     in `angellist.py`).
  3. Parse structured deck content (product, ICP, traction, etc.) in a
     focused vision call (`deck.py`).
  4. Run the diligence synthesis as a text-heavy call: parsed AL metadata
     + parsed deck content + notes go in as DENSE TEXT. The deck images
     stay attached as a fallback, but Claude doesn't have to re-read them
     to know what the company does — the text is already there.

This text-heavy synthesis path is the key fix for the "Claude said nothing
about the product" failure mode: previously the diligence call had 30+
images + a 9-section schema, and Claude skimmed. Now it has dense
pre-parsed text and can spend its budget on synthesis instead of reading.
"""

import math
import tempfile
from html import escape
from pathlib import Path

from angel_memos.claude import Purpose, extract_structured
from angel_memos.materials import (
    Materials,
    load_materials,
    load_or_parse_angellist,
    load_or_parse_deck,
    read_text,
)
from angel_memos.models import (
    AngelListMetadata,
    DeckContent,
    DiligenceSection,
    DiligenceTopics,
    ScenarioAnalysisSection,
    ScenarioOutcome,
)
from angel_memos.pdf_utils import rasterize_pdf
from angel_memos.research import (
    ComparableDeals,
    FounderProfile,
    RecentEvents,
    load_or_find_comps,
    load_or_find_events,
    load_or_profile_founders,
    render_comparable_deals_text,
    render_founder_profiles_text,
    render_recent_events_text,
)

_SYSTEM_PROMPT = """You produce ADVERSARIAL, DENSE diligence on an angel-
stage private company. Output structure is opinionated by design: every
section has BULL points facing BEAR points, an explicit VERDICT line that
names a side, and falsifiable KILL CONDITIONS.

This is not a descriptive analysis. It is a debate that takes positions.

============================================================================
HARD RULES (auto-fail if violated)
============================================================================

1. ONE LINE PER BULLET. Maximum ~25 words. No multi-clause prose. No
   parenthetical asides. If a thought needs two sentences, split into
   two bullets.

2. EVERY BULL POINT MUST HAVE A BEAR COUNTER. If you list "wedge is
   clean", the bear column must contain the specific way the wedge gets
   eaten. Symmetric debate, not one-sided narration.

3. VERDICT TAKES A SIDE. One of:
     "GO." / "GO IF <specific>." / "PASS." / "PASS UNLESS <specific>."
     / "WATCH — REVISIT WHEN <specific>."
   Banned: "Mixed signals", "Cautiously interesting", "Could be a fit
   depending", "Worth probing further". These are all auto-fails.

4. KILL CONDITIONS ARE FALSIFIABLE. Each one is a specific
   measurable event ("gross margin <30%", "Insight ships native cost-
   routing in 2026"). Banned: vague items like "if execution is poor"
   or "if the moat doesn't hold".

5. CONCRETE OVER ADJECTIVAL. Cite numbers, names, dates. Banned
   adjectives without quantity: "significant", "massive", "rich", "deep",
   "strong" — UNLESS attached to a specific number/name in the same line.

============================================================================
BANNED PHRASINGS — auto-fail
============================================================================
  - "Worth probing"
  - "Defensible if X holds, otherwise rich"
  - "Mixed signals"
  - "The evidence is incomplete"
  - "This is normal early but"
  - "Cautiously interesting"
  - "$XX", "$XXM" placeholder dollars
  - "TBD", "TO VERIFY", "to be researched"
  - "Cannot verify" without naming what you'd need
  - Multi-clause sentences strung together with semicolons or em-dashes
    where a split into two bullets is cleaner

============================================================================
CHECK SIZE CONTEXT — diligence must be ANSWERABLE without founder access
============================================================================

Bhanu invests $2,500-$5,000 via syndicate. He does NOT get:
  - Founder reference calls or Q&A
  - Data room access (no signed MSAs, no monthly P&L, no cap table)
  - Stripe / billing dashboard
  - Customer reference calls
  - Cohort retention tables or weighted pipeline detail
  - Anything requiring founder or lead-investor cooperation

Kill conditions, top_priorities, and open lines of inquiry MUST be
answerable from:
  - The deck + AL memo content (already in this prompt)
  - The pre-researched FOUNDER PROFILES block (web research already done)
  - The pre-researched COMPARABLE DEALS block (web research already done)
  - Additional public web search (LinkedIn, Crunchbase, news, Glassdoor
    of founders' PRIOR companies, regulatory filings, court records,
    state procurement portals, public funding coverage)
  - Sanity-check math on deck-stated numbers (does ARR / customers /
    months line up? Is the pipeline number consistent with deal count?)
  - Stage / category base rates from publicly-known comparables

BANNED diligence asks (auto-fails):
  - "Reference call with <customer / founder / prior co-founder>"
  - "Data room: signed MSA / cap table / monthly P&L / billing dashboard"
  - "Founder Q&A on X"
  - "Realized ARR vs. contracted" — not knowable from outside
  - "Verified gross margin breakdown" — not knowable from outside
  - "Confirm via cap table"
  - "Indemnification language in signed contracts"
  - Anything requiring founder, lead, or customer cooperation

REPHRASE such asks into checkable things:
  - "Does deck's stated ARR math line up with the customer count?"
  - "What does lead investor's public commentary say about traction?"
  - "What does <named competitor>'s pricing page reveal about take-rate
    economics?"
  - "Glassdoor sentiment on founders' prior employer?"
  - "Has press / regulator covered any challenge to the product?"
  - "What does the AL memo's 'Total prior capital' figure imply about
    burn vs. progress?"

`top_priorities` (3-5 items) = NEXT PUBLIC/WEB-RESEARCH ACTIONS Bhanu
can take in 30 minutes. NOT a wish list of inaccessible data.

============================================================================
HOW TO USE THE PRE-RESEARCHED TEXT IN THE USER PROMPT
============================================================================

The user prompt includes blocks produced by earlier focused web-research
calls:

  - `FOUNDER PROFILES (web-researched):` — one entry per founder with
    pedigree tier (S/A/B/C/D) and sources. THIS IS THE INPUT FOR THE
    TEAM SECTION. Cite the tier inline in bull/bear points.

  - `COMPARABLE DEALS (web-researched):` — 2-3 funded comparables with
    round size, valuation, ARR (when known). THIS IS THE INPUT FOR THE
    COMPETITIVE LANDSCAPE AND FINANCIALS SECTIONS. Name comps inline in
    bull/bear points to anchor valuation discussion.

  - `RECENT EVENTS (web-swept ...):` — material events from the last ~18
    months: executive quotes on maturity, buyer insourcing, competitor
    M&A/funding, customer wins/losses. AN EVENT OUTWEIGHS A DECK CLAIM
    ON THE SAME TOPIC (a customer CEO saying "pilot stage" beats the
    deck's "production scale"). Work each material event into the
    relevant section's bear or bull column; never leave one unused.

If pre-research returned nothing useful, that's a kill condition or a
top-priority — don't paper over with a placeholder.

EVIDENCE UNIVERSE — state it, don't imply completeness. The final
verdict_and_open_questions section must end with one line naming what
this brief's evidence covered: "Evidence universe: deal materials +
founder/comps/events web passes; no expert calls, no data room." A
reader must never mistake a quick screen for deep diligence.

MATERIALITY RULE for verification asks: a private company doing <1-2%
of a megacap customer's revenue will NEVER appear in that customer's
10-K/10-Q — its absence from filings is not evidence, and "confirm via
<megacap> filings" is a BANNED ask. Route the same question through
executive interviews, trade press, or the customer's own newsroom.

============================================================================
THE 9 STANDARD SECTIONS — each gets bull / bear / verdict / kill
============================================================================

  1. business_overview — atomic unit of value, unit economics, contract
     structure. Bull: why the wedge is durable. Bear: how it gets eaten.
  2. strategy — articulated direction. Bull: where it's internally
     consistent. Bear: where strategy and observed behavior diverge.
  3. value_chain — buyers, switching costs, platform position. Bull: why
     it's defensible. Bear: who squeezes from above or below.
  4. competitive_landscape — name comps from the COMPARABLE DEALS block
     inline. Bull: differentiated how (structural vs. executional). Bear:
     replicable in 18 months by which incumbent.
  5. opportunities_and_risks — bull = growth vectors + tailwinds. Bear =
     specific failure mechanisms (NOT "competition" / "execution"). Each
     bear must name a concrete mechanism.
  6. team — cite pedigree tier from FOUNDER PROFILES block. Bull: why
     they execute. Bear: specific gap (commercial / technical / domain).
  7. financials — anchor valuation against comps. Bull: why entry price
     is defensible. Bear: specific multiple/ratio that's offside.
     MANDATORY bullet (bull or bear, wherever it lands): BREAK-EVEN
     GROWTH — revenue needed at the named comp multiple to justify entry
     post-money, vs current revenue ("needs 5.0x revenue growth just to
     return 1x at Symbotic's 10.3x").
  8. scenario_analysis — bull = upside math + probability. Bear = downside
     math + probability. Verdict = probability-weighted call. ALSO populate
     `scenarios`: 4-5 named outcomes obeying the SCENARIO MATH CONTRACT
     below. Probabilities must sum to ~1.0. Spread across Zero/Slow
     Grind/Base/Bull/<regime-change tail> — must show realistic tail risk
     AND upside, not a polite Bear/Base/Bull cluster.
  9. verdict_and_open_questions — overall call. Verdict is the final
     position. Kill conditions = the 2-3 highest-leverage diligence items.

For EVERY section, populate ALL FOUR fields:
  - bull_points: 2-4 one-line bull arguments (concrete, fact-anchored)
  - bear_points: 2-4 one-line bear counters (specific failure mechanisms)
  - verdict: 1 sentence opinionated call ("GO", "GO IF X", "PASS",
    "PASS UNLESS X", "WATCH WHEN X")
  - kill_conditions: 1-3 falsifiable measurable events that would flip
    the verdict

============================================================================
SCENARIO MATH CONTRACT — micro-economics only (auto-fail if violated)
============================================================================

Every scenario's return_mom is DERIVED, never asserted. The chain is:
  exit revenue x named comp multiple = exit EV; / entry post-money;
  x dilution retention (rounds implied by burn vs runway vs exit year).
Write that chain in `derivation` and set `exit_value_usd`.

1. MULTIPLES COME FROM NAMED COMPS — a company in the COMPARABLE DEALS
   block or a named public comp with its current multiple. Banned:
   naked multiples ("10x is fair for this space").
2. TAM BIND: state which cells the sourced TAM constrains. A base case
   requiring ≥50% of the category TAM is a bear point, not a footnote.
3. PROBABILITIES FROM EXECUTION GATES: each weight names the company-
   specific gate that sets it (booked vs pipeline revenue, deployment
   stage, contract status, next-round dependency). Banned: weights
   copied from population base rates or asserted without a gate. Base
   rates appear ONCE, as a closing sanity line in the section verdict
   ("tail weights sit inside the 4-6% observed >10x rate").
4. THE TAIL CELL IS A REGIME CHANGE, NOT A BIGGER BASE CASE. Build it
   first-principles: what economics regime could this company's asset
   unlock (e.g. absorbing the construction LABOR line, not the layout-
   tooling line)? Price it as units x displaced cost x plausible
   capture. Weight it honestly — 1-3% or argued to zero — but it must
   be constructed and reasoned; comp-anchored ceilings silently
   truncate exactly the power-law tail angel investing exists for. It
   REPLACES a generic "Generational" cell; never keep both.
5. DOWNSIDE CELLS RESPECT THE PREFERENCE STACK: state total preferred
   sitting ahead of this SPV. Sub-1x cells must reflect seniority — a
   senior position over a small stack floors near 1x at modest exits; a
   junior position under a large stack zeros even above total raised.
6. If the terms summary carries a TERMS PARSE INCOMPLETE warning, the
   financials verdict MUST open with "NET MATH UNRELIABLE —" and name
   the missing fields as a kill condition.

============================================================================

`top_priorities` on the top-level DiligenceTopics: 3-5 highest-leverage
diligence actions across all sections — what to do next, not what to think.

Optional `supplementary` sections: deal-specific topics that don't fit
the 9 buckets (regulatory risk, token economics). Use sparingly. Same
4-field shape as the standard sections."""


def run_diligence(folder: Path) -> DiligenceTopics:
    """Run Phase A on a company folder. Returns the typed DiligenceTopics.

    Pipeline:
      1. Focused vision passes (cached): parse AL metadata, parse deck.
      2. Focused web-research passes (cached): profile each founder, find
         comparable deals. Outputs are dense text fed into the synthesis.
      3. Synthesis pass: heavy text-input call producing the 9-section
         DiligenceTopics. The pre-researched text is what makes the Team
         and Competitive Landscape sections concrete instead of templated.
    """
    materials = load_materials(folder)

    # Focused vision passes — cached on disk to avoid redundant Claude calls.
    angellist = load_or_parse_angellist(folder, materials)
    deck_content = load_or_parse_deck(folder, materials)

    # Focused web-research passes — cached too. Each produces structured
    # text that the synthesis prompt consumes directly.
    founder_profiles = load_or_profile_founders(folder, angellist.founders, angellist.company)
    product_summary = deck_content.product_description if deck_content is not None else ""
    category_keywords = list(angellist.markets)
    if deck_content is not None and deck_content.icp:
        category_keywords.append(deck_content.icp)
    comps = load_or_find_comps(
        folder,
        angellist.company,
        category_keywords,
        angellist.stage,
        product_summary,
    )
    # Event discovery: exec quotes, buyer insourcing, competitor M&A — the
    # facts the deck doesn't volunteer and a claims-level debate can't see.
    events = load_or_find_events(
        folder,
        angellist.company,
        category_keywords,
        anchor_names=angellist.co_investors + _named_anchors(deck_content),
    )

    # Heavy synthesis pass — text-heavy with deck images as fallback.
    with tempfile.TemporaryDirectory(prefix="angel_memos_diligence_") as tmp_str:
        tmp_dir = Path(tmp_str)
        al_pngs = rasterize_pdf(materials.angellist.path, tmp_dir / "al")
        deck_pngs: list[Path] = []
        if materials.deck is not None:
            deck_pngs = rasterize_pdf(materials.deck.path, tmp_dir / "deck")
        prompt = _format_prompt(
            al_pngs,
            deck_pngs,
            materials,
            angellist,
            deck_content,
            founder_profiles,
            comps,
            events,
        )
        return extract_structured(
            prompt,
            DiligenceTopics,
            purpose=Purpose.DILIGENCE_TOPICS,
            system_prompt=_SYSTEM_PROMPT,
            additional_dirs=[str(tmp_dir)],
            image_paths=al_pngs + deck_pngs,
        )


def run_diligence_phase(folder: Path) -> Path:
    """End-to-end Phase A: run analysis, render HTML, write file.
    Returns the path of the written `diligence_topics.html`."""
    topics = run_diligence(folder)
    # The terms-gap banner is rendered deterministically from the parsed
    # metadata (cache-hit here — run_diligence already parsed it), not
    # entrusted to the LLM output: a wrong net-return figure must be flagged
    # even if the synthesis pass ignored contract rule 6.
    materials = load_materials(folder)
    gaps = load_or_parse_angellist(folder, materials).terms_gaps()
    html = render_diligence_html(topics, terms_gaps=gaps)
    out_path = folder / "diligence_topics.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


_SECTION_ORDER: list[tuple[str, str]] = [
    ("business_overview", "1. Business Overview"),
    ("strategy", "2. Strategy & Stated Direction"),
    ("value_chain", "3. Value Chain & Market Position"),
    ("competitive_landscape", "4. Competitive Landscape & Differentiation"),
    ("opportunities_and_risks", "5. Opportunities & Risks"),
    ("team", "6. Team Assessment"),
    ("financials", "7. Financials, Funding & Valuation"),
    ("scenario_analysis", "8. Scenario Analysis"),
    ("verdict_and_open_questions", "9. Verdict & Open Questions"),
]


def render_diligence_html(topics: DiligenceTopics, terms_gaps: list[str] | None = None) -> str:
    """Render `DiligenceTopics` as a standalone HTML document with inline CSS.
    The output is readable in any browser and self-contained (no external
    dependencies). A non-empty `terms_gaps` renders a blocking data-gap
    banner above everything else — net-return math downstream of an
    incomplete TERMS parse is wrong, not merely uncertain."""
    company_html = escape(topics.company)
    banner = ""
    if terms_gaps:
        gap_list = ", ".join(escape(g) for g in terms_gaps)
        banner = (
            '<section class="datagap">\n'
            "<strong>TERMS PARSE INCOMPLETE</strong> — "
            f"{gap_list} parsed as 0.0. All net-return and fee math in this "
            "report is unreliable until the TERMS table is re-captured.\n"
            "</section>"
        )
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{company_html} - Diligence Topics</title>",
        _STYLES,
        "</head>",
        "<body>",
        f"<h1>{company_html} &mdash; Diligence Topics</h1>",
        banner,
        _render_top_priorities(topics.top_priorities),
    ]
    for attr, heading in _SECTION_ORDER:
        section = getattr(topics, attr)
        parts.append(_render_section(heading, section))
    if topics.supplementary:
        parts.append("<h2>Supplementary</h2>")
        for supp in topics.supplementary:
            parts.append(_render_section(f"&Dagger; {escape(supp.title)}", supp))
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)


def _render_top_priorities(items: list[str]) -> str:
    if not items:
        return ""
    lis = "\n".join(f"  <li>{escape(it)}</li>" for it in items)
    return f'<section class="priorities">\n<h2>Do Next</h2>\n<ol>\n{lis}\n</ol>\n</section>'


def _render_section(heading: str, section: DiligenceSection) -> str:
    """Two-column bull/bear with prominent verdict + kill conditions.

    Empty bull/bear columns are still rendered (shows the asymmetry) so a
    section with only bear points reads as a unanimous bear case. Empty
    kill_conditions are omitted. Scenario analysis sections get an extra
    outcome-chart block prepended."""
    bull_li = (
        "\n".join(f"      <li>{escape(p)}</li>" for p in section.bull_points)
        or '      <li class="empty">—</li>'
    )
    bear_li = (
        "\n".join(f"      <li>{escape(p)}</li>" for p in section.bear_points)
        or '      <li class="empty">—</li>'
    )
    verdict_class = _verdict_css_class(section.verdict)
    kill_html = ""
    if section.kill_conditions:
        kill_li = "\n".join(f"      <li>{escape(c)}</li>" for c in section.kill_conditions)
        kill_html = (
            f'  <div class="kill">\n'
            f'    <span class="kill-label">Kill</span>\n'
            f"    <ul>\n{kill_li}\n    </ul>\n"
            f"  </div>"
        )
    chart_html = ""
    if isinstance(section, ScenarioAnalysisSection):
        chart_html = _render_scenario_chart(section.scenarios) + "\n"
    return (
        f'<section class="section">\n'
        f"<h2>{heading}</h2>\n"
        f"{chart_html}"
        f'  <div class="bullbear">\n'
        f'    <div class="bull">\n'
        f'      <span class="col-label">Bull</span>\n'
        f"      <ul>\n{bull_li}\n      </ul>\n"
        f"    </div>\n"
        f'    <div class="bear">\n'
        f'      <span class="col-label">Bear</span>\n'
        f"      <ul>\n{bear_li}\n      </ul>\n"
        f"    </div>\n"
        f"  </div>\n"
        f'  <p class="verdict {verdict_class}">'
        f'<span class="verdict-label">Verdict</span> '
        f"{escape(section.verdict)}</p>\n"
        f"{kill_html}\n"
        f"</section>"
    )


def _verdict_css_class(verdict: str) -> str:
    """Pick a color class from the verdict's leading verb.

    `GO` → green, `PASS` → red, `WATCH` → amber, else neutral."""
    head = verdict.lstrip().upper()
    if head.startswith("GO"):
        return "go"
    if head.startswith("PASS"):
        return "pass"
    if head.startswith("WATCH"):
        return "watch"
    return "neutral"


# ---------------------------------------------------------------------------
# Scenario chart — SVG dot plot of outcome distribution.
# ---------------------------------------------------------------------------

_CHART_WIDTH = 720
_CHART_HEIGHT = 130
_AXIS_LEFT = 40
_AXIS_RIGHT = 700
_AXIS_Y = 90
_AXIS_TICKS_MOM: tuple[float, ...] = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0)


def _render_scenario_chart(scenarios: list[ScenarioOutcome]) -> str:
    """Render an SVG dot plot of `scenarios` plus a stats line and a
    compact scenarios table.

    X-axis: log-scaled gross MoM at exit (0.1x → 100x).
    Dot radius ∝ √(probability) — areas roughly proportional to mass.
    Color: red < 1x (loss), neutral 1-3x, green ≥ 3x.

    Right of the chart: E[MoM], P(>3x), P(zero/loss), asymmetry ratio.
    Below: small table with one row per scenario.
    """
    expected = _expected_mom(scenarios)
    upside = sum(s.probability * max(0.0, s.return_mom - 1.0) for s in scenarios)
    downside = sum(s.probability * max(0.0, 1.0 - s.return_mom) for s in scenarios)
    asymmetry = upside / downside if downside > 0 else math.inf
    p_3x = sum(s.probability for s in scenarios if s.return_mom >= 3.0)
    p_loss = sum(s.probability for s in scenarios if s.return_mom < 1.0)

    # Axis ticks
    tick_marks: list[str] = []
    for tick in _AXIS_TICKS_MOM:
        x = _mom_to_x(tick)
        tick_marks.append(
            f'<line x1="{x}" y1="{_AXIS_Y - 4}" x2="{x}" y2="{_AXIS_Y + 4}" '
            f'stroke="#9ca3af" stroke-width="1" />'
        )
        tick_marks.append(
            f'<text x="{x}" y="{_AXIS_Y + 18}" text-anchor="middle" '
            f'font-size="10" fill="#6b7280">{_format_mom(tick)}</text>'
        )
    # Break-even reference (1x).
    breakeven_x = _mom_to_x(1.0)
    breakeven_line = (
        f'<line x1="{breakeven_x}" y1="20" x2="{breakeven_x}" '
        f'y2="{_AXIS_Y}" stroke="#9ca3af" stroke-width="1" '
        f'stroke-dasharray="2,3" />'
    )
    breakeven_label = (
        f'<text x="{breakeven_x}" y="14" text-anchor="middle" '
        f'font-size="9" fill="#6b7280">break-even</text>'
    )

    # E[MoM] reference line.
    expected_x = _mom_to_x(max(0.05, expected))  # clamp so E[MoM] stays on chart
    expected_line = (
        f'<line x1="{expected_x}" y1="20" x2="{expected_x}" '
        f'y2="{_AXIS_Y}" stroke="#0369a1" stroke-width="1.5" />'
    )
    expected_label = (
        f'<text x="{expected_x}" y="14" text-anchor="middle" '
        f'font-size="10" font-weight="600" fill="#0369a1">'
        f"E[MoM] = {expected:.1f}x</text>"
    )

    # Dots — one per scenario.
    dots: list[str] = []
    for s in scenarios:
        x = _mom_to_x(max(0.05, s.return_mom))
        r = max(4, 28 * math.sqrt(s.probability))  # area ∝ probability
        color = _scenario_color(s.return_mom)
        dots.append(
            f'<circle cx="{x:.1f}" cy="{_AXIS_Y}" r="{r:.1f}" '
            f'fill="{color}" fill-opacity="0.55" stroke="{color}" '
            f'stroke-width="1.5" />'
        )
        dots.append(
            f'<text x="{x:.1f}" y="{_AXIS_Y - r - 4:.1f}" '
            f'text-anchor="middle" font-size="10" fill="#374151">'
            f"{escape(s.name)} · {s.probability:.0%}</text>"
        )

    # Axis baseline.
    axis = (
        f'<line x1="{_AXIS_LEFT}" y1="{_AXIS_Y}" x2="{_AXIS_RIGHT}" '
        f'y2="{_AXIS_Y}" stroke="#9ca3af" stroke-width="1" />'
    )

    svg = (
        f'<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" '
        f'class="scenariochart" role="img" '
        f'aria-label="Scenario outcome distribution">\n'
        f"  {axis}\n  " + "\n  ".join(tick_marks) + f"\n  {breakeven_line}\n  {breakeven_label}\n"
        f"  {expected_line}\n  {expected_label}\n  " + "\n  ".join(dots) + "\n</svg>"
    )

    asym_display = "∞" if math.isinf(asymmetry) else f"{asymmetry:.1f}"
    stats = (
        f'<div class="scenariostats">'
        f"<span><strong>E[MoM]</strong> {expected:.2f}x</span>"
        f"<span><strong>P(≥3x)</strong> {p_3x:.0%}</span>"
        f"<span><strong>P(loss)</strong> {p_loss:.0%}</span>"
        f"<span><strong>Asymmetry</strong> {asym_display}</span>"
        f"</div>"
    )

    rows: list[str] = []
    for s in scenarios:
        exit_ev = f"${s.exit_value_usd / 1e6:,.0f}M" if s.exit_value_usd else "—"
        rows.append(
            f"      <tr>"
            f"<td>{escape(s.name)}</td>"
            f'<td class="num">{s.probability:.0%}</td>'
            f'<td class="num">{_format_mom(s.return_mom)}</td>'
            f'<td class="num">{exit_ev}</td>'
            f"<td>{escape(s.derivation)}</td>"
            f"<td>{escape(s.key_assumption)}</td>"
            f"</tr>"
        )
    table = (
        '  <table class="scenarios">\n'
        "    <thead>\n"
        "      <tr><th>Scenario</th><th>P</th><th>MoM</th><th>Exit EV</th>"
        "<th>Derivation</th><th>Key assumption</th></tr>\n"
        "    </thead>\n    <tbody>\n" + "\n".join(rows) + "\n    </tbody>\n  </table>"
    )

    return f'  <div class="scenariofan">\n    {svg}\n    {stats}\n{table}\n  </div>'


def _expected_mom(scenarios: list[ScenarioOutcome]) -> float:
    """Probability-weighted expected MoM."""
    return sum(s.probability * s.return_mom for s in scenarios)


def _mom_to_x(mom: float) -> float:
    """Map a MoM value to x-position via log10. Min MoM clamped to 0.05x
    so total-loss scenarios still land on chart at the left edge."""
    min_log = math.log10(0.05)
    max_log = math.log10(_AXIS_TICKS_MOM[-1])
    clamped = max(0.05, mom)
    frac = (math.log10(clamped) - min_log) / (max_log - min_log)
    frac = max(0.0, min(1.0, frac))
    return _AXIS_LEFT + frac * (_AXIS_RIGHT - _AXIS_LEFT)


def _scenario_color(mom: float) -> str:
    """Color a scenario dot by its return: red (loss), neutral (1-3x),
    green (>=3x)."""
    if mom < 1.0:
        return "#b91c1c"
    if mom < 3.0:
        return "#64748b"
    return "#047857"


def _format_mom(mom: float) -> str:
    """Format a MoM number for display. Sub-1x shows as `0.5x`, integers
    show as `3x`, others as `1.5x`."""
    if mom < 1.0:
        return f"{mom:.2g}x"
    if mom >= 10 and mom == int(mom):
        return f"{int(mom)}x"
    if mom == int(mom):
        return f"{int(mom)}x"
    return f"{mom:.1f}x"


def _named_anchors(deck_content: DeckContent | None) -> list[str]:
    """Competitor/customer names worth sweeping for events, from the deck."""
    if deck_content is None:
        return []
    return list(deck_content.competitors_mentioned)[:4]


def _format_prompt(
    al_pngs: list[Path],
    deck_pngs: list[Path],
    materials: Materials,
    angellist: AngelListMetadata,
    deck_content: DeckContent | None,
    founder_profiles: list[FounderProfile],
    comps: ComparableDeals | None,
    events: RecentEvents | None = None,
) -> str:
    al_refs = "\n".join(f"@{p.as_posix()}" for p in al_pngs)
    deck_refs = "\n".join(f"@{p.as_posix()}" for p in deck_pngs)
    notes_text = "\n\n".join(f"--- {n.path.name} ---\n{read_text(n)}" for n in materials.notes)

    al_summary = (
        f"AngelList metadata (pre-parsed):\n"
        f"  Company: {angellist.company}\n"
        f"  Round: {angellist.round_label} (stage: {angellist.stage.value})\n"
        f"  Markets: {', '.join(angellist.markets)}\n"
        f"  Pre-money: ${angellist.pre_money_usd:,.0f}\n"
        f"  Round size: ${angellist.estimated_round_size_usd:,.0f}\n"
        f"  Post-money: ${angellist.post_money_usd:,.0f}\n"
        f"  Estimated expenses: {angellist.estimated_expenses_pct:.1%}\n"
        f"  Gross carry: {angellist.gross_carry_pct:.1%}\n"
        f"  Lead investment: ${angellist.leads_investment_usd:,.0f}\n"
        f"  Founders: {', '.join(angellist.founders)}\n"
        f"  Co-investors: "
        f"{', '.join(angellist.co_investors) if angellist.co_investors else 'none disclosed'}\n"
    )
    if angellist.total_prior_capital_usd is not None:
        al_summary += f"  Total prior capital: ${angellist.total_prior_capital_usd:,.0f}\n"
    gaps = angellist.terms_gaps()
    if gaps:
        al_summary += (
            f"  *** TERMS PARSE INCOMPLETE — {', '.join(gaps)} read as 0.0, "
            "which real AL syndicate deals never are. Treat fee/net-return "
            "math as unreliable and follow Scenario Math Contract rule 6. ***\n"
        )

    deck_summary = ""
    if deck_content is not None:
        deck_summary = (
            "Pitch-deck content (pre-parsed):\n"
            f"  Product: {deck_content.product_description}\n"
            f"  ICP: {deck_content.icp}\n"
            f"  Primary use case: {deck_content.primary_use_case}\n"
            f"  Pricing: {deck_content.pricing_model or '(not disclosed in deck)'}\n"
            f"  Traction: {deck_content.traction or '(not disclosed in deck)'}\n"
            f"  Competitors mentioned: "
            f"{', '.join(deck_content.competitors_mentioned) if deck_content.competitors_mentioned else '(none)'}\n"
            f"  Market claims: {deck_content.market_claims or '(none)'}\n"
            f"  GTM motion: {deck_content.gtm_motion or '(not disclosed)'}\n"
            f"  Roadmap: {deck_content.roadmap or '(not disclosed)'}\n"
            f"  Differentiation: {deck_content.differentiation or '(not disclosed)'}\n"
        )
        if deck_content.notable_metrics:
            deck_summary += "  Notable metrics:\n" + "".join(
                f"    - {m}\n" for m in deck_content.notable_metrics
            )

    sections: list[str] = [
        al_summary,
    ]
    if deck_summary:
        sections.append(deck_summary)

    # Web-research text. These were produced by separate focused Claude
    # calls (research.py) with WebSearch enabled — the synthesis pass
    # should treat them as ground truth and avoid re-doing the research.
    sections.append(render_founder_profiles_text(founder_profiles))
    sections.append(render_comparable_deals_text(comps))
    sections.append(render_recent_events_text(events))

    sections.extend(
        [
            "Raw materials (attached for spot-checking; rely primarily on the "
            "pre-parsed text above):",
            "  AngelList memo pages:",
            al_refs,
        ]
    )
    if deck_refs:
        sections.extend(["  Pitch deck pages:", deck_refs])
    if notes_text:
        sections.extend(["Freeform notes:", notes_text])
    sections.append(
        "\nProduce a DiligenceTopics with all 9 standard sections plus "
        "optional supplementary. For EACH section, fill bull_points / "
        "bear_points / verdict / kill_conditions. Bullets are ONE LINE "
        "each (≤25 words). Verdict takes a side. Counter every bull with "
        "the strongest bear. Anchor numbers and names from the FOUNDER "
        "PROFILES and COMPARABLE DEALS blocks above. Top-priorities is "
        "3-5 ACTIONS to take next (not summary thoughts). Return JSON "
        "matching the schema."
    )
    return "\n".join(sections)


_STYLES = """<style>
  :root { color-scheme: light; --bull: #047857; --bear: #b91c1c;
          --kill: #92400e; --go: #047857; --pass-c: #b91c1c;
          --watch: #92400e; --neutral: #374151; }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 Helvetica, Arial, sans-serif;
    max-width: 1240px;
    margin: 1.25rem auto;
    padding: 0 1rem 2rem;
    line-height: 1.35;
    font-size: 13.5px;
    color: #1f2937;
    background: #fafafa;
  }
  h1 {
    font-size: 1.4rem;
    margin: 0 0 0.4rem;
    padding-bottom: 0.3rem;
    border-bottom: 2px solid #d1d5db;
  }
  h2 {
    font-size: 0.95rem;
    margin: 0 0 0.4rem;
    color: #111827;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 0.15rem;
  }
  ul, ol {
    margin: 0;
    padding-left: 1.1rem;
  }
  li {
    margin-bottom: 0.15rem;
  }
  li.empty {
    color: #9ca3af;
    list-style: none;
    margin-left: -1.1rem;
  }
  section.datagap {
    background: #fef2f2;
    border: 2px solid #b91c1c;
    color: #7f1d1d;
    padding: 0.5rem 0.8rem;
    margin-bottom: 1rem;
    border-radius: 3px;
    font-size: 0.9rem;
  }
  section.priorities {
    background: #fffbeb;
    border-left: 3px solid #f59e0b;
    padding: 0.5rem 0.8rem;
    margin-bottom: 1rem;
    border-radius: 3px;
  }
  section.priorities h2 {
    margin-top: 0;
    border: none;
  }
  section.section {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 4px;
    padding: 0.5rem 0.8rem;
    margin-bottom: 0.7rem;
  }
  div.bullbear {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.8rem;
  }
  div.bull, div.bear {
    border-left: 3px solid var(--bull);
    padding-left: 0.6rem;
  }
  div.bear { border-left-color: var(--bear); }
  span.col-label {
    display: block;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.2rem;
    color: var(--bull);
  }
  div.bear span.col-label { color: var(--bear); }
  p.verdict {
    margin: 0.55rem 0 0;
    padding: 0.35rem 0.6rem;
    border-radius: 3px;
    font-weight: 500;
    line-height: 1.4;
  }
  span.verdict-label {
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.72rem;
    margin-right: 0.4rem;
    padding: 1px 6px;
    border-radius: 2px;
    color: #fff;
  }
  p.verdict.go { background: #ecfdf5; color: #065f46; }
  p.verdict.go .verdict-label { background: var(--go); }
  p.verdict.pass { background: #fef2f2; color: #7f1d1d; }
  p.verdict.pass .verdict-label { background: var(--pass-c); }
  p.verdict.watch { background: #fffbeb; color: #78350f; }
  p.verdict.watch .verdict-label { background: var(--watch); }
  p.verdict.neutral { background: #f3f4f6; color: #1f2937; }
  p.verdict.neutral .verdict-label { background: var(--neutral); }
  div.kill {
    margin-top: 0.4rem;
    padding: 0.3rem 0.6rem;
    background: #fff7ed;
    border-left: 3px solid var(--kill);
    border-radius: 3px;
  }
  span.kill-label {
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.15rem;
    color: var(--kill);
  }
  div.scenariofan {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 3px;
    padding: 0.4rem 0.5rem 0.5rem;
    margin-bottom: 0.6rem;
  }
  svg.scenariochart {
    width: 100%;
    height: auto;
    max-height: 130px;
    display: block;
  }
  div.scenariostats {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    font-size: 0.78rem;
    color: #1f2937;
    padding: 0.35rem 0.2rem 0.4rem;
    border-bottom: 1px solid #e5e7eb;
  }
  div.scenariostats strong {
    color: #4b5563;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.68rem;
    letter-spacing: 0.05em;
    margin-right: 0.25rem;
  }
  table.scenarios {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
    margin-top: 0.4rem;
  }
  table.scenarios th, table.scenarios td {
    text-align: left;
    padding: 0.18rem 0.45rem;
    border-bottom: 1px solid #f3f4f6;
  }
  table.scenarios th {
    font-weight: 600;
    color: #4b5563;
    text-transform: uppercase;
    font-size: 0.68rem;
    letter-spacing: 0.05em;
  }
  table.scenarios td.num, table.scenarios th:nth-child(2),
  table.scenarios th:nth-child(3), table.scenarios th:nth-child(4) {
    text-align: right;
    font-variant-numeric: tabular-nums;
    width: 60px;
  }
  @media (max-width: 720px) {
    div.bullbear { grid-template-columns: 1fr; gap: 0.4rem; }
  }
</style>"""
