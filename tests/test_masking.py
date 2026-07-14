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


def test_usd_variants_include_half_million_word_form() -> None:
    """The `$X.5 million` word form must be generated (regression: it was
    missing, so an exact figure like '$12.5 million' slipped the leak check)."""
    variants = list(all_variants_for(12_500_000))
    assert "$12.5 million" in variants


# ---------------------------------------------------------------------------
# find_public_leaks — the pre-publish leak gate.
# ---------------------------------------------------------------------------

from angel_memos.masking import find_public_leaks  # noqa: E402


def test_clean_anonymized_entry_has_no_leaks() -> None:
    text = (
        '{"category_descriptor": "Video Management Agents", '
        '"metrics": "low-$XM CARR, sub-$XXM post"}'
    )
    leaks = find_public_leaks(
        text,
        company="Spot AI",
        founders=["Rish Gupta", "Tanuj Thapliyal"],
        check_usd=10_000,
        post_money_usd=195_000_000,
    )
    assert leaks == []


def test_leak_gate_catches_company_name_case_insensitive() -> None:
    leaks = find_public_leaks("We love spot ai as a company.", company="Spot AI")
    assert any(m.startswith("company:") for m in leaks)


def test_leak_gate_catches_concatenated_and_domain_forms() -> None:
    assert find_public_leaks("Check out SpotAI today.", company="Spot AI")
    assert find_public_leaks("Visit spotai.com for info.", company="Spot AI")


def test_leak_gate_catches_stem_of_suffixed_name() -> None:
    """'Quaise Energy' -> the bare stem 'Quaise' is still identifying."""
    leaks = find_public_leaks("Quaise is drilling deep.", company="Quaise Energy")
    assert any(m.startswith("company:") for m in leaks)


def test_leak_gate_catches_founder_surname() -> None:
    leaks = find_public_leaks(
        "The CEO, previously at Google, leads the team. Thapliyal is technical.",
        company="Spot AI",
        founders=["Tanuj Thapliyal"],
    )
    assert any("Thapliyal" in m for m in leaks)


def test_leak_gate_catches_exact_dollar_figure() -> None:
    leaks = find_public_leaks(
        "Post-money is $195 million.", company="Spot AI", post_money_usd=195_000_000
    )
    assert any(m.startswith("post_money:") for m in leaks)


def test_leak_gate_catches_review_marker() -> None:
    leaks = find_public_leaks("[NEEDS BHANU REVIEW: is the moat real?]", company="Spot AI")
    assert "review-marker" in leaks


def test_leak_gate_ignores_short_company_name_false_positive() -> None:
    """A 2-char name is too collision-prone to assert on (word-boundary + the
    3-char floor), so it doesn't fire on unrelated text."""
    leaks = find_public_leaks("A completely unrelated sentence.", company="Ai")
    assert leaks == []
