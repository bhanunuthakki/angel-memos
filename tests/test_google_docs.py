"""Block composition + range merging in `google_docs`.

These exercise the pure functions that compute the Docs API payload shape
without making an actual API call. The OAuth-touching entry points are
covered by end-to-end use on real docs.
"""

import pytest

from angel_memos.doc_entries import (
    BullCaseSection,
    CompetitiveMoatSection,
    KeyMetricsSection,
    MarketAndOpportunity,
    PrivateDocEntry,
    PublicDocEntry,
    TeamSection,
)
from angel_memos.google_docs import (
    Block,
    GoogleDocsStructureError,
    _build_private_blocks,
    _build_public_blocks,
    _compose_blocks,
    _find_container_heading_end,
    _merge_adjacent_ranges,
)
from angel_memos.models import (
    Conviction,
    Decision,
    ValuationMethod,
    Verdict,
)


def _buy_decision() -> Decision:
    return Decision.model_validate(
        {
            "company": "Zeno Moto",
            "verdict": Verdict.BUY,
            "conviction": Conviction.HIGH,
            "check_usd": 5000.0,
            "post_money_usd": 64_000_000.0,
            "valuation_method": ValuationMethod.CUSTOM,
            "current_base_metric_usd": None,
            "scenarios": None,
            "benchmarks": None,
            "top_reasons": ["a", "b", "c"],
            "top_risks": ["x", "y", "z"],
            "raw_reasoning": "r",
        }
    )


def _pass_decision() -> Decision:
    return Decision.model_validate(
        {
            "company": "Anode",
            "verdict": Verdict.PASS,
            "conviction": Conviction.MEDIUM,
            "check_usd": 0.0,
            "post_money_usd": 34_000_000.0,
            "valuation_method": ValuationMethod.CUSTOM,
            "current_base_metric_usd": None,
            "scenarios": None,
            "benchmarks": None,
            "top_reasons": ["a", "b", "c"],
            "top_risks": ["x", "y", "z"],
            "raw_reasoning": "r",
        }
    )


def _private_entry() -> PrivateDocEntry:
    return PrivateDocEntry(
        rationale_bullets=["Pretty Rich", "Co-Investors: Eclipse"],
        second_section_bullets=["Execution"],
    )


def _public_entry(**overrides: object) -> PublicDocEntry:
    base: dict[str, object] = {
        "category_descriptor": "Synthetic Feed Additives",
        "stage_label": "Seed",
        "date_label": "May 2026",
        "what_does_it_do": "Replaces meat-based feed.",
        "why_is_it_important": "Decouples beef CO2 from cattle headcount.",
        "market_and_opportunity": MarketAndOpportunity(
            job_to_be_done="Reduce enteric methane.",
            market_size="$30B feed market globally.",
            why_now="Regulatory tightening this year.",
        ),
        "team": TeamSection(
            founder_market_fit="CEO ran R&D at a top feed company.",
            superpower_or_execution_advantage="Patent stack from prior employer.",
        ),
        "key_metrics": KeyMetricsSection(
            arr_or_contracted_revenue="low-$XM CARR",
            retention="NDR >130%",
            efficiency_or_techno_economics="~$XX per feed",
        ),
        "anti_thesis_paragraphs": [
            "Regulatory backsliding could collapse the demand curve.",
            "Patent challenges from generic feed manufacturers.",
        ],
        "bull_case": BullCaseSection(
            thesis="Sticky supply chains compound with FDA-grade trials.",
            verdict="GO. Sub-$XXM post for a derisked pipeline.",
        ),
    }
    base.update(overrides)
    return PublicDocEntry.model_validate(base)


# ---------------------------------------------------------------------------
# _merge_adjacent_ranges
# ---------------------------------------------------------------------------


def test_merge_collapses_adjacent_ranges() -> None:
    out = _merge_adjacent_ranges([(0, 5), (5, 10), (10, 15)])
    assert out == [(0, 15)]


def test_merge_keeps_disjoint_ranges_separate() -> None:
    out = _merge_adjacent_ranges([(0, 5), (10, 15), (15, 20)])
    assert out == [(0, 5), (10, 20)]


def test_merge_empty_returns_empty() -> None:
    assert _merge_adjacent_ranges([]) == []


# ---------------------------------------------------------------------------
# _compose_blocks: text + offsets.
# ---------------------------------------------------------------------------


def test_compose_concatenates_text_with_newlines() -> None:
    blocks = [
        Block(text="Hello", style="heading_3"),
        Block(text="World", style="normal"),
    ]
    text, _meta = _compose_blocks(blocks)
    assert text == "Hello\nWorld\n"


def test_compose_emits_paragraph_style_for_every_block() -> None:
    """Even body blocks get an explicit NORMAL_TEXT request — otherwise the
    Docs API inherits the preceding heading style."""
    blocks = [
        Block(text="H", style="heading_3"),
        Block(text="B", style="normal"),
        Block(text="C", style="bullet"),
    ]
    _text, meta = _compose_blocks(blocks)
    styles = [s for s, _start, _end in meta["paragraph_styles"]]
    assert styles == ["HEADING_3", "NORMAL_TEXT", "NORMAL_TEXT"]


def test_compose_bold_prefix_bolds_only_the_visible_prefix() -> None:
    """The bold range covers the first N characters of `text`, not the newline."""
    blocks = [Block(text="Label: body", style="normal", bold_prefix_chars=6)]
    _text, meta = _compose_blocks(blocks)
    assert meta["bold_ranges"] == [(0, 6)]


def test_compose_bold_prefix_capped_to_visible_text() -> None:
    """If bold_prefix_chars > len(text), cap to the visible characters
    (don't include the trailing newline)."""
    blocks = [Block(text="ABC", style="normal", bold_prefix_chars=99)]
    _text, meta = _compose_blocks(blocks)
    assert meta["bold_ranges"] == [(0, 3)]


def test_compose_groups_consecutive_bullets() -> None:
    """Adjacent bullet blocks merge into one createParagraphBullets range."""
    blocks = [
        Block(text="H", style="heading_4"),
        Block(text="A", style="bullet"),
        Block(text="B", style="bullet"),
        Block(text="H2", style="heading_4"),
        Block(text="C", style="bullet"),
    ]
    _text, meta = _compose_blocks(blocks)
    # First bullet group: A, B  (offsets 2..4 and 4..6 -> merged 2..6)
    # Second bullet group: C alone
    assert len(meta["bullet_ranges"]) == 2


def test_compose_no_bullets_returns_empty_bullet_ranges() -> None:
    blocks = [Block(text="X", style="normal")]
    _text, meta = _compose_blocks(blocks)
    assert meta["bullet_ranges"] == []


def test_compose_offset_arithmetic_consistent() -> None:
    """Every paragraph_style range's `end - start` equals len(block.text)+1."""
    blocks = [
        Block(text="alpha", style="heading_3"),
        Block(text="beta gamma", style="normal"),
        Block(text="delta", style="bullet"),
    ]
    _text, meta = _compose_blocks(blocks)
    for (_style, start, end), block in zip(meta["paragraph_styles"], blocks):
        assert end - start == len(block.text) + 1


# ---------------------------------------------------------------------------
# _build_private_blocks
# ---------------------------------------------------------------------------


def test_private_blocks_buy_uses_plain_company_heading() -> None:
    blocks = _build_private_blocks(_buy_decision(), _private_entry())
    # First block is the company heading; no [Passed] prefix
    assert blocks[0].text == "Zeno Moto"
    assert blocks[0].style == "heading_3"
    assert "[Passed]" not in blocks[0].text


def test_private_blocks_pass_uses_passed_prefix() -> None:
    blocks = _build_private_blocks(_pass_decision(), _private_entry())
    assert blocks[0].text == "[Passed] Anode"


def test_private_blocks_buy_uses_risks_subheading() -> None:
    blocks = _build_private_blocks(_buy_decision(), _private_entry())
    headings = [b.text for b in blocks if b.style == "heading_4"]
    assert headings == ["Rationale", "Risks"]


def test_private_blocks_pass_uses_why_passing_subheading() -> None:
    blocks = _build_private_blocks(_pass_decision(), _private_entry())
    headings = [b.text for b in blocks if b.style == "heading_4"]
    assert headings == ["Rationale", "Why Passing?"]


def test_private_blocks_bullets_match_entry_content() -> None:
    entry = PrivateDocEntry(
        rationale_bullets=["bullet one", "bullet two", "bullet three"],
        second_section_bullets=["risk a", "risk b"],
    )
    blocks = _build_private_blocks(_buy_decision(), entry)
    bullets = [b.text for b in blocks if b.style == "bullet"]
    assert bullets == ["bullet one", "bullet two", "bullet three", "risk a", "risk b"]


# ---------------------------------------------------------------------------
# _build_public_blocks
# ---------------------------------------------------------------------------


def test_public_blocks_first_block_is_heading_4_with_descriptor() -> None:
    blocks = _build_public_blocks(_public_entry())
    assert blocks[0].style == "heading_4"
    assert "Synthetic Feed Additives" in blocks[0].text
    assert "Seed Deal Memo" in blocks[0].text


def test_public_blocks_includes_all_required_section_banners() -> None:
    blocks = _build_public_blocks(_public_entry())
    banner_texts = {b.text for b in blocks if b.bold_prefix_chars == len(b.text)}
    for required in (
        "Market & Opportunity",
        "Team",
        "Key Metrics (Anonymized Ranges)",
        "Anti-Thesis",
        "Bull Case",
    ):
        assert required in banner_texts


def test_public_blocks_includes_competitive_moat_when_present() -> None:
    entry = _public_entry(
        competitive_moat=CompetitiveMoatSection(
            structural_advantage="Workflow lock-in",
            execution_velocity="Two ship-events per week",
        )
    )
    blocks = _build_public_blocks(entry)
    banner_texts = [b.text for b in blocks]
    assert "Competitive Moat & Company Superpower" in banner_texts


def test_public_blocks_omits_competitive_moat_when_absent() -> None:
    blocks = _build_public_blocks(_public_entry(competitive_moat=None))
    banner_texts = [b.text for b in blocks]
    assert all("Competitive Moat" not in t for t in banner_texts)


def test_public_blocks_bullets_have_bold_sublabels() -> None:
    """Bullets like 'Job(s) to be done: <body>' must have a bold prefix
    covering the sub-label including the colon."""
    blocks = _build_public_blocks(_public_entry())
    job_bullets = [
        b for b in blocks if b.style == "bullet" and b.text.startswith("Job(s) to be done:")
    ]
    assert len(job_bullets) == 1
    assert job_bullets[0].bold_prefix_chars == len("Job(s) to be done:")


def test_public_blocks_anti_thesis_paragraphs_render_as_bullets() -> None:
    entry = _public_entry(
        anti_thesis_paragraphs=["mechanism one", "mechanism two", "mechanism three"]
    )
    blocks = _build_public_blocks(entry)
    # Find the Anti-Thesis banner and check the next three bullet blocks
    banner_idx = next(i for i, b in enumerate(blocks) if b.text == "Anti-Thesis")
    following = blocks[banner_idx + 1 : banner_idx + 4]
    assert [b.text for b in following] == ["mechanism one", "mechanism two", "mechanism three"]
    assert all(b.style == "bullet" for b in following)
    # Anti-thesis paragraphs do NOT have bold prefix sub-labels
    assert all(b.bold_prefix_chars == 0 for b in following)


def test_public_blocks_date_label_appears_as_normal_paragraph() -> None:
    blocks = _build_public_blocks(_public_entry(date_label="May 2026"))
    date_block = next(b for b in blocks if b.text.startswith("Date:"))
    assert date_block.style == "normal"
    assert "May 2026" in date_block.text


def test_public_blocks_what_does_it_do_has_bold_prefix() -> None:
    blocks = _build_public_blocks(_public_entry())
    block = next(b for b in blocks if b.text.startswith("What does it do?"))
    assert block.style == "normal"
    assert block.bold_prefix_chars == len("What does it do?")


# ---------------------------------------------------------------------------
# _find_container_heading_end
# ---------------------------------------------------------------------------


def _fake_doc(*paragraphs: tuple[str, str, int]) -> dict[str, object]:
    """Construct a minimal `documents.get` response.

    Each input tuple is `(text, named_style, end_index)`.
    """
    content: list[dict[str, object]] = []
    for text, named_style, end_index in paragraphs:
        content.append(
            {
                "endIndex": end_index,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": named_style},
                    "elements": [
                        {"textRun": {"content": text + "\n"}},
                    ],
                },
            }
        )
    return {"body": {"content": content}}


def test_find_container_returns_end_index_of_matching_heading() -> None:
    doc = _fake_doc(
        ("Memos", "HEADING_3", 42),
        ("[Passed] Anode", "HEADING_4", 100),
    )
    assert _find_container_heading_end(doc, "Memos", level=3) == 42


def test_find_container_raises_when_heading_missing() -> None:
    doc = _fake_doc(("Other", "HEADING_3", 42))
    with pytest.raises(GoogleDocsStructureError):
        _find_container_heading_end(doc, "Memos", level=3)


def test_find_container_ignores_text_at_wrong_level() -> None:
    """A 'Memos' paragraph at HEADING_2 is NOT the H3 container."""
    doc = _fake_doc(("Memos", "HEADING_2", 42))
    with pytest.raises(GoogleDocsStructureError):
        _find_container_heading_end(doc, "Memos", level=3)
