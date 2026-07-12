"""Public-memo masking: substitute deal-identifying strings in the private
memo with bracketed placeholders. Used by `memo` to produce `memo_public.md`
from `memo_private.md` mechanically (no second Claude call)."""

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

_COMPANY_PLACEHOLDER = "[Company]"
_FOUNDER_PLACEHOLDER = "[Founder]"
_CHECK_PLACEHOLDER = "$[CHECK]"
_VAL_PLACEHOLDER = "$[VAL]"
_EXTRA_PLACEHOLDER = "[Redacted]"

# Common brand suffixes auto-appended to every masked company name so
# "Quaise Energy" -> "[Company]" rather than the partial "[Company] Energy".
_COMPANY_SUFFIXES: tuple[str, ...] = (
    "Inc",
    "Inc.",
    "Corp",
    "Corp.",
    "Co",
    "Co.",
    "LLC",
    "Ltd",
    "Ltd.",
    "Limited",
    "Energy",
    "Labs",
    "Lab",
    "AI",
    "Health",
    "Bio",
    "Tech",
    "Technologies",
    "Systems",
    "Networks",
    "Platforms",
    "Industries",
    "Cities",
    "Robotics",
)


class MaskConfig(BaseModel):
    """Inputs that determine what gets masked. Built from `Decision` and
    `AngelListMetadata` at memo-generation time."""

    model_config = ConfigDict(frozen=True)

    company: str = Field(min_length=1)
    aliases: list[str] = []  # known brand variants (e.g. "Quaise Energy")
    founders: list[str] = []
    check_usd: float | None = None
    post_money_usd: float | None = None
    extra_terms: list[str] = []  # customer names, etc., flagged by Claude


def apply_masks(text: str, config: MaskConfig) -> str:
    """Replace identifiers in `text` with placeholders. The substitution
    order matters: longer/more-specific terms first so they aren't partly
    masked by shorter terms. Dollar amounts use literal string replacement
    against multiple plausible formattings; names use word-boundary regex
    to avoid partial matches inside other words."""
    masked = text

    if config.check_usd is not None:
        for variant in _usd_variants(config.check_usd):
            masked = masked.replace(variant, _CHECK_PLACEHOLDER)
    if config.post_money_usd is not None:
        for variant in _usd_variants(config.post_money_usd):
            masked = masked.replace(variant, _VAL_PLACEHOLDER)

    name_substitutions: list[tuple[str, str]] = []
    for variant in _company_variants(config.company, config.aliases):
        name_substitutions.append((variant, _COMPANY_PLACEHOLDER))
    name_substitutions.extend((f, _FOUNDER_PLACEHOLDER) for f in config.founders)
    name_substitutions.extend((t, _EXTRA_PLACEHOLDER) for t in config.extra_terms)
    name_substitutions.sort(key=lambda pair: len(pair[0]), reverse=True)
    for original, placeholder in name_substitutions:
        masked = _replace_word(masked, original, placeholder)

    return masked


def _company_variants(company: str, aliases: list[str]) -> list[str]:
    """Generate candidate strings to mask for a company name: bare name,
    user-supplied aliases, and common brand-suffix extensions."""
    seen: set[str] = set()
    out: list[str] = []
    for base in [company, *aliases]:
        for variant in [base, *(f"{base} {suffix}" for suffix in _COMPANY_SUFFIXES)]:
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def _usd_variants(amount: float) -> list[str]:
    """Generate plausible textual representations of a USD amount, ordered
    longest-first so the more specific format is matched before a shorter
    suffix-of would catch the same substring."""
    n = round(amount)
    variants: list[str] = [f"${n:,} USD", f"${n:,}"]

    if amount >= 1_000_000_000:
        b_int = round(amount / 1_000_000_000)
        b_one = round(amount / 1_000_000_000, 1)
        variants.extend(
            [
                f"${b_int} billion",
                f"${b_int}B USD",
                f"${b_int}B",
                f"${b_one:g}B",
            ]
        )
    if 1_000_000 <= amount < 1_000_000_000_000:
        m_int = round(amount / 1_000_000)
        m_one = round(amount / 1_000_000, 1)
        variants.extend(
            [
                f"${m_int} million",
                f"${m_int}M USD",
                f"${m_int}M",
                f"${m_one:g}M",
            ]
        )
    if 1_000 <= amount < 1_000_000:
        k_int = round(amount / 1_000)
        variants.extend(
            [
                f"${k_int}k",
                f"${k_int}K",
                f"${k_int}k USD",
            ]
        )

    # De-dupe while preserving order, then sort longest-first so the more
    # specific format wins (e.g., "$25,000 USD" before "$25,000").
    seen: set[str] = set()
    deduped: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    deduped.sort(key=len, reverse=True)
    return deduped


def _replace_word(text: str, original: str, placeholder: str) -> str:
    """Replace `original` with `placeholder` at word boundaries (so a name
    like 'Sud' doesn't get matched inside 'study'). Names with internal
    punctuation/whitespace are escaped properly."""
    if not original:
        return text
    pattern = re.compile(rf"\b{re.escape(original)}\b")
    return pattern.sub(placeholder, text)


def all_variants_for(amount: float) -> Iterable[str]:
    """Expose `_usd_variants` for tests and downstream callers that want to
    see what mask candidates would be generated for a given amount."""
    return _usd_variants(amount)
