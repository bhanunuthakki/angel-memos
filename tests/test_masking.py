"""Public-memo masking tests. Structural assertions only — no exact prompt
or copy matching."""

from angel_memos.masking import MaskConfig, all_variants_for, apply_masks


def _config(**overrides: object) -> MaskConfig:
    base: dict[str, object] = {
        "company": "Spot AI",
        "founders": ["Rish Gupta", "Sud Bhatija", "Tanuj Thapliyal"],
        "check_usd": 10_000,
        "post_money_usd": 195_000_000,
    }
    base.update(overrides)
    return MaskConfig.model_validate(base)


def test_masks_company_name_at_word_boundary() -> None:
    text = "Spot AI is a leading Video AI Agents platform."
    out = apply_masks(text, _config())
    assert "Spot AI" not in out
    assert "[Company]" in out


def test_does_not_mask_company_substring_in_other_word() -> None:
    text = "Spot AI Spotlight Spotai"
    out = apply_masks(text, _config())
    assert "[Company]" in out
    # "Spotlight" and "Spotai" should be untouched (no word boundary on "Spot AI")
    assert "Spotlight" in out
    assert "Spotai" in out


def test_masks_each_founder_name() -> None:
    text = "Rish Gupta (CEO) and Sud Bhatija (COO) co-founded the company."
    out = apply_masks(text, _config())
    assert "Rish Gupta" not in out
    assert "Sud Bhatija" not in out
    assert out.count("[Founder]") == 2


def test_masks_check_amount_in_multiple_formats() -> None:
    config = _config(check_usd=10_000)
    for form in ["$10,000", "$10k", "$10K"]:
        text = f"My check is {form}."
        out = apply_masks(text, config)
        assert form not in out
        assert "$[CHECK]" in out


def test_masks_post_money_in_multiple_formats() -> None:
    config = _config(post_money_usd=195_000_000)
    for form in ["$195M", "$195 million", "$195,000,000"]:
        text = f"Post-money valuation is {form}."
        out = apply_masks(text, config)
        assert form not in out
        assert "$[VAL]" in out


def test_masking_idempotent_on_already_masked_text() -> None:
    config = _config()
    text = "Already-masked text: [Company], [Founder], $[CHECK], $[VAL]."
    assert apply_masks(text, config) == text


def test_extra_terms_get_redacted_placeholder() -> None:
    config = _config(extra_terms=["Sephora", "Crown"])
    text = "Customers include Sephora and Crown."
    out = apply_masks(text, config)
    assert "Sephora" not in out
    assert "Crown" not in out
    assert out.count("[Redacted]") == 2


def test_check_and_valuation_dont_collide_when_similar() -> None:
    """If check_usd and post_money_usd happen to format similarly (unlikely
    but possible), each gets its own placeholder — they aren't conflated."""
    config = _config(check_usd=25_000, post_money_usd=25_000_000)
    text = "Check $25k into a $25M post-money round."
    out = apply_masks(text, config)
    assert "$[CHECK]" in out
    assert "$[VAL]" in out
    assert "$25k" not in out
    assert "$25M" not in out


def test_usd_variants_for_check_size() -> None:
    """Sanity check that variants include the common formats angel investors
    actually write."""
    variants = list(all_variants_for(25_000))
    assert "$25,000" in variants
    assert "$25k" in variants
    assert "$25K" in variants


def test_usd_variants_for_billion_scale() -> None:
    variants = list(all_variants_for(2_500_000_000))
    assert any("B" in v for v in variants)
    assert any("billion" in v for v in variants)
