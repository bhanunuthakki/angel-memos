"""AngelList memo parser tests.

Unit tests mock the Claude call. The integration test (`-m integration`)
hits real Claude with the SpotAI fixture to verify the prompt + schema
actually produce a valid AngelListMetadata; it costs subscription quota
so it's skipped by default."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from angel_memos import angellist
from angel_memos.angellist import _Founders, _Terms, parse_angellist_metadata
from angel_memos.models import AngelListMetadata, Stage

FIXTURES = Path(__file__).parent / "fixtures"
SPOTAI_AL_PDF = FIXTURES / "spotai_al.pdf"

# A valid `_Terms` payload reused by the mocked-Claude tests. The founders are
# supplied by the separate founders pass, so this dict has none.
_TERMS_DATA: dict[str, Any] = {
    "company": "Spot AI",
    "round_label": "Series B+",
    "stage": Stage.SERIES_B.value,
    "instrument": "Equity",
    "estimated_round_size_usd": 20_000_000,
    "share_class": "Preferred",
    "pre_money_usd": 175_000_000,
    "allocation_usd": 63_000,
    "estimated_expenses_pct": 0.111,
    "leads_investment_usd": 10_000,
    "gross_carry_pct": 0.20,
    "min_investment_usd": 2_000,
    "deadline": "2025-11-20",
    "markets": ["AI", "ML"],
    "co_investors": ["Scale Venture Partners"],
    "total_prior_capital_usd": 93_000_000,
}


def _one_page_rasterize(_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    """Stub rasterizer: one blank PNG per PDF, under the caller's out_dir so
    the page @-refs encode which source (`.../al/...` vs `.../deck/...`) the
    founders pass is reading — that's what the fallback tests route on."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "page_001.png"
    png.write_bytes(b"")
    return [png]


def _spotai_metadata() -> AngelListMetadata:
    """Fixture matching the SpotAI AL Details PDF TERMS table."""
    return AngelListMetadata.model_validate(
        {
            "company": "Spot AI",
            "round_label": "Series B+",
            "stage": Stage.SERIES_B.value,
            "instrument": "Equity",
            "estimated_round_size_usd": 20_000_000,
            "share_class": "Preferred",
            "pre_money_usd": 175_000_000,
            "allocation_usd": 63_000,
            "estimated_expenses_pct": 0.111,
            "leads_investment_usd": 10_000,
            "gross_carry_pct": 0.20,
            "min_investment_usd": 2_000,
            "deadline": date(2025, 11, 20),
            "markets": ["AI", "ML"],
            "founders": ["Rish Gupta", "Sud Bhatija", "Tanuj Thapliyal"],
            "co_investors": ["Scale Venture Partners", "Qualcomm Ventures", "StepStone Group"],
            "total_prior_capital_usd": 93_000_000,
        }
    )


def test_parse_angellist_returns_typed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two-pass parser: first call returns `_Terms` (AL extraction), second
    returns `_Founders` (deck/team extraction). Verifies the parser dispatches
    to both, merges them, and returns a typed AngelListMetadata."""
    pdf = tmp_path / "spotai_al.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")

    expected_terms_data = {
        "company": "Spot AI",
        "round_label": "Series B+",
        "stage": Stage.SERIES_B.value,
        "instrument": "Equity",
        "estimated_round_size_usd": 20_000_000,
        "share_class": "Preferred",
        "pre_money_usd": 175_000_000,
        "allocation_usd": 63_000,
        "estimated_expenses_pct": 0.111,
        "leads_investment_usd": 10_000,
        "gross_carry_pct": 0.20,
        "min_investment_usd": 2_000,
        "deadline": "2025-11-20",
        "markets": ["AI", "ML"],
        "co_investors": ["Scale Venture Partners"],
        "total_prior_capital_usd": 93_000_000,
    }
    expected_founders = ["Rish Gupta", "Sud Bhatija", "Tanuj Thapliyal"]
    captured_model_types: list[type[Any]] = []

    def fake_rasterize(_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        png = out_dir / "page_001.png"
        png.write_bytes(b"")
        return [png]

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        captured_model_types.append(model_type)
        if model_type is _Terms:
            return _Terms.model_validate(expected_terms_data)
        if model_type is _Founders:
            return _Founders(founders=expected_founders)
        raise RuntimeError(f"unexpected model_type in fake: {model_type}")

    monkeypatch.setattr(angellist, "rasterize_pdf", fake_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    result = parse_angellist_metadata(pdf)

    assert isinstance(result, AngelListMetadata)
    assert result.company == "Spot AI"
    assert result.founders == expected_founders
    # Both passes ran.
    assert _Terms in captured_model_types
    assert _Founders in captured_model_types


def test_founders_fall_back_to_al_memo_when_deck_lacks_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a "deck" file with no team slide (an investor update or
    teaser — common for AngelList SPV attachments) must NOT zero out founders.

    When the deck founders-pass finds nothing, the parser falls back to the AL
    memo body, whose narrative "Memo" section names founders in prose, and
    recovers them. This is the recurring empty-founders bug that floors the
    team factor to the "no pedigree" minimum (hit Reflect Orbital, Efficient
    Computer, Tasklet, Arcee AI, Alljoined, Lazarus Energy)."""
    al = tmp_path / "angellist - Reflect Orbital.pdf"
    al.write_bytes(b"%PDF-1.4\n%stub\n")
    deck = tmp_path / "Reflect Orbital deck.pdf"
    deck.write_bytes(b"%PDF-1.4\n%stub\n")

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        if model_type is _Terms:
            return _Terms.model_validate(_TERMS_DATA)
        if model_type is _Founders:
            # The deck pass reads the (teaser) deck pages and finds no team
            # slide; the fallback reads the AL memo pages and recovers the name.
            if "/deck/" in prompt:
                return _Founders(founders=[])
            return _Founders(founders=["Ben Nowack"])
        raise RuntimeError(f"unexpected model_type in fake: {model_type}")

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    result = parse_angellist_metadata(al, deck_pdf=deck)

    # Founders recovered from the memo body rather than left empty.
    assert result.founders, "founders must not be empty when the memo names them"
    assert all(isinstance(f, str) and f.strip() for f in result.founders)


def test_founders_extracted_from_al_memo_text_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: when the AL PDF has a text layer (Chrome-print AL memos do),
    founders named in the memo prose are recovered from the TEXT — the reliable
    path, since vision skims prose-buried names. The image fallback finds
    nothing here, proving the text pass is what surfaces the founder."""
    al = tmp_path / "angellist - Reflect Orbital.pdf"
    al.write_bytes(b"%PDF-1.4\n%stub\n")
    deck = tmp_path / "Reflect Orbital deck.pdf"
    deck.write_bytes(b"%PDF-1.4\n%stub\n")

    memo_text = "SENTINEL_MEMO_TEXT Ben Nowack is now the CEO of Tons of Mirrors. " * 8

    def fake_read_text(_p: Path) -> str:
        return memo_text

    monkeypatch.setattr(angellist, "read_pdf_text", fake_read_text)

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        if model_type is _Terms:
            return _Terms.model_validate(_TERMS_DATA)
        if model_type is _Founders:
            if "SENTINEL_MEMO_TEXT" in prompt:  # the AL-memo text pass
                return _Founders(founders=["Ben Nowack"])
            return _Founders(founders=[])  # teaser deck + image fallback find none
        raise RuntimeError(f"unexpected model_type in fake: {model_type}")

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    result = parse_angellist_metadata(al, deck_pdf=deck)

    assert result.founders == ["Ben Nowack"]


def test_founders_prefer_deck_team_slide_over_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the deck DOES have a real team slide, its founders win and the AL
    memo fallback is not consulted — the deck is the canonical team source."""
    al = tmp_path / "angellist - Acme.pdf"
    al.write_bytes(b"%PDF-1.4\n%stub\n")
    deck = tmp_path / "Acme deck.pdf"
    deck.write_bytes(b"%PDF-1.4\n%stub\n")

    memo_pass_used = False

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        nonlocal memo_pass_used
        if model_type is _Terms:
            return _Terms.model_validate(_TERMS_DATA)
        if model_type is _Founders:
            if "/deck/" in prompt:
                return _Founders(founders=["Deck Founder"])
            memo_pass_used = True
            return _Founders(founders=["Memo Founder"])
        raise RuntimeError(f"unexpected model_type in fake: {model_type}")

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    result = parse_angellist_metadata(al, deck_pdf=deck)

    assert result.founders == ["Deck Founder"]
    assert not memo_pass_used, "memo fallback should be skipped when the deck yields founders"


def test_founders_use_al_memo_when_no_deck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no deck at all, the founders pass reads the AL memo pages directly."""
    al = tmp_path / "angellist - NoDeck.pdf"
    al.write_bytes(b"%PDF-1.4\n%stub\n")

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        if model_type is _Terms:
            return _Terms.model_validate(_TERMS_DATA)
        if model_type is _Founders:
            assert "/al/" in prompt, "founders must be read from the AL memo pages"
            return _Founders(founders=["Solo Founder"])
        raise RuntimeError(f"unexpected model_type in fake: {model_type}")

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    result = parse_angellist_metadata(al)

    assert result.founders == ["Solo Founder"]


def test_parse_angellist_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no_such.pdf"
    with pytest.raises(FileNotFoundError, match="AngelList"):
        parse_angellist_metadata(missing)


def test_parse_angellist_propagates_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Claude returns data that doesn't validate (Pydantic raises), the
    parser surfaces the error rather than swallowing it."""
    from pydantic import ValidationError

    pdf = tmp_path / "angellist.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")

    def fake_rasterize(_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        png = out_dir / "page_001.png"
        png.write_bytes(b"")
        return [png]

    def failing_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        # Whichever schema gets called first, fail validation with an empty company.
        return model_type.model_validate({})

    monkeypatch.setattr(angellist, "rasterize_pdf", fake_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", failing_extract)

    with pytest.raises(ValidationError):
        parse_angellist_metadata(pdf)


@pytest.mark.integration
def test_parse_angellist_against_real_spotai_pdf() -> None:
    """Real Claude call against the SpotAI AL fixture.

    Validates that the prompt + schema produce AngelListMetadata that matches
    the structural facts about the deal (round, valuation, carry, markets).
    Runs only when explicitly requested: `pytest -m integration`."""
    result = parse_angellist_metadata(SPOTAI_AL_PDF)

    # Structural identity — not exact string matching on prose-y fields.
    assert "spot" in result.company.lower()
    assert result.stage == Stage.SERIES_B
    assert result.pre_money_usd == pytest.approx(175_000_000, rel=0.001)
    assert result.estimated_round_size_usd == pytest.approx(20_000_000, rel=0.001)
    assert result.gross_carry_pct == pytest.approx(0.20, abs=0.01)
    assert result.post_money_usd == pytest.approx(195_000_000, rel=0.001)
    # Markets table cell was "AI / ML" — at least one of these should appear
    assert any("AI" in m or "ML" in m for m in result.markets)
    # Three founders surfaced in the team grid
    assert len(result.founders) >= 3
    # At least one named co-investor (the deck page had Scale / Qualcomm / StepStone)
    assert len(result.co_investors) >= 1


# ---------------------------------------------------------------------------
# Deterministic round parsing (no Claude call) — used by ingest to keep
# separate rounds of the same company in separate folders.
# ---------------------------------------------------------------------------

_TERMS_MEMO = """Dusty Robotics
TERMS
Investment adviser Platform Advisor, LLC
Round Series B+
Instrument Equity
Estimated round size $15M USD
Share class Preferred
Pre-money valuation $120M USD
"""


def test_parse_round_reads_the_terms_row() -> None:
    found = angellist.parse_round(_TERMS_MEMO)

    assert found is not None
    assert found.stage is Stage.SERIES_B
    assert found.label == "Series B+"


def test_parse_round_ignores_the_word_round_in_prose() -> None:
    """A real Dexterity memo says 'Attractive entry point. Round was priced
    earlier this year.' — a loose match would read that as the round."""
    prose = (
        "TERMS\nInvestment adviser Platform Advisor, LLC\nInstrument Equity\n\n"
        "Attractive entry point. Round was priced earlier this year.\n"
    )

    assert angellist.parse_round(prose) is None


def test_parse_round_without_a_terms_table_is_unknown() -> None:
    assert angellist.parse_round("Dexterity builds production-grade Physical AI.") is None


def test_parse_round_rejects_a_label_outside_the_stage_enum() -> None:
    assert angellist.parse_round("TERMS\nRound Series ZZ Recapitalization\n") is None


def test_parse_round_of_empty_text_layer_is_unknown() -> None:
    """Image-only AL captures extract to ~nothing; that must read as unknown."""
    assert angellist.parse_round("") is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Pre-Seed", Stage.PRE_SEED),
        ("Seed", Stage.SEED),
        ("Series A", Stage.SERIES_A),
        ("Series C+", Stage.SERIES_C),
        ("Series D", Stage.GROWTH),
        ("Growth", Stage.GROWTH),
    ],
)
def test_parse_round_maps_labels_to_stages(label: str, expected: Stage) -> None:
    found = angellist.parse_round(f"TERMS\nRound {label}\nInstrument Equity\n")

    assert found is not None
    assert found.stage is expected
    assert found.label == label


def test_deterministic_terms_row_overrides_vision_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Dexterity case: vision mapped 'Series C+' to growth while the
    deterministic parser (which ingest's folder routing uses) maps it to
    series_c — the parse is authoritative so cache and routing agree."""
    pdf = tmp_path / "angellist - Acme.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        if model_type is _Terms:
            return _Terms.model_validate(
                _TERMS_DATA | {"round_label": "Series C+", "stage": "growth"}
            )
        return _Founders(founders=["Jane Doe"])

    def _fake_text(_p: Path) -> str:
        return "TERMS\nRound Series C+\nInstrument Equity\n"

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)
    monkeypatch.setattr(angellist, "read_pdf_text", _fake_text)

    result = parse_angellist_metadata(pdf)
    assert result.stage is Stage.SERIES_C


def test_vision_stage_stands_when_no_text_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "angellist - Acme.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")

    def fake_extract(prompt: str, model_type: type[Any], **kwargs: Any) -> Any:
        if model_type is _Terms:
            return _Terms.model_validate(_TERMS_DATA | {"stage": "growth"})
        return _Founders(founders=["Jane Doe"])

    monkeypatch.setattr(angellist, "rasterize_pdf", _one_page_rasterize)
    monkeypatch.setattr(angellist, "extract_structured", fake_extract)

    def _empty_text(_p: Path) -> str:
        return ""

    monkeypatch.setattr(angellist, "read_pdf_text", _empty_text)

    result = parse_angellist_metadata(pdf)
    assert result.stage is Stage.GROWTH
