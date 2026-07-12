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
