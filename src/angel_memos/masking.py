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
                f"${b_one:g} billion",
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
                # The `$X.5 million` word form was missing — an exact figure
                # like "$12.5 million" slipped past the check entirely.
                f"${m_one:g} million",
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


# ---------------------------------------------------------------------------
# Leak gate: deterministic pre-publish check on the public memo.
#
# The public entry is LLM-generated from a prompt that contains the real
# company name, founder names, and exact figures; the model is merely
# *instructed* to anonymize. Before anything is written to the externally
# shared public Google Doc, scan the generated entry for any identifier that
# should never appear and hard-fail if one survives. This is the mechanical
# backstop the pipeline was missing.
# ---------------------------------------------------------------------------

# The review marker the public-entry prompt mandates for unvetted inferences.
# It must never reach an external reader.
REVIEW_MARKER = "[NEEDS BHANU REVIEW"

_DOMAIN_TLDS: tuple[str, ...] = (".com", ".io", ".ai", ".co", ".xyz", ".app", ".dev", ".net")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)<>]+|\b(?:www\.)[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# Generic trailing words that make a company name (e.g. "Quaise Energy") — if
# the last token is one of these, the bare stem ("Quaise") is also identifying.
_GENERIC_TAIL_WORDS: frozenset[str] = frozenset(
    s.lower() for s in _COMPANY_SUFFIXES if s not in {"Co", "Co.", "AI"}
)


class PublicMemoLeakError(RuntimeError):
    """Raised when a would-be-published public entry still contains a
    deal-identifying string. Carries the list of offending matches."""

    def __init__(self, leaks: list[str]) -> None:
        self.leaks = leaks
        super().__init__(
            "public memo still contains deal-identifying content; refusing to "
            f"publish. Offending matches: {', '.join(leaks)}"
        )


def find_public_leaks(
    text: str,
    *,
    company: str,
    aliases: list[str] | None = None,
    founders: list[str] | None = None,
    check_usd: float | None = None,
    post_money_usd: float | None = None,
    private_values: Iterable[float] = (),
    private_only_terms: Iterable[str] = (),
) -> list[str]:
    """Return the list of deal-identifying strings found in `text` (empty if
    clean). Case-insensitive; catches concatenated/hyphenated/domain company
    forms, founder full names and distinctive surnames, exact dollar figures,
    and the internal review marker."""
    leaks: list[str] = []
    lowered = text.casefold()

    for form in _company_name_forms(company, aliases or []):
        if _word_present(text, form):
            leaks.append(f"company:{form}")
    for domain in _company_domain_forms(company, aliases or []):
        if domain.casefold() in lowered:
            leaks.append(f"domain:{domain}")

    for founder in founders or []:
        for part in _founder_forms(founder):
            if _word_present(text, part):
                leaks.append(f"founder:{part}")

    amounts = [
        (check_usd, "check"),
        (post_money_usd, "post_money"),
        *[(v, "private") for v in private_values],
    ]
    for amount, label in amounts:
        if amount is not None:
            for variant in _usd_variants(amount):
                if variant in text:
                    leaks.append(f"{label}:{variant}")

    for term in private_only_terms:
        if term and _word_present(text, term):
            leaks.append(f"private-term:{term}")
    for match in _EMAIL_RE.finditer(text):
        leaks.append(f"email:{match.group(0)}")
    for match in _URL_RE.finditer(text):
        leaks.append(f"url:{match.group(0)}")

    if REVIEW_MARKER in text:
        leaks.append("review-marker")

    # Preserve order, drop duplicates.
    return list(dict.fromkeys(leaks))


def _word_present(text: str, term: str) -> bool:
    """Case-insensitive word-boundary match. Skips terms shorter than 3 chars
    (too collision-prone to assert on)."""
    if len(term) < 3:
        return False
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None


def _company_name_forms(company: str, aliases: list[str]) -> list[str]:
    """Identifying textual forms of a company name: the bare name, aliases,
    the space-stripped ('SpotAI') and hyphenated ('Spot-AI') variants, and the
    stem with a generic trailing word removed ('Quaise' from 'Quaise Energy')."""
    forms: list[str] = []
    for base in [company, *aliases]:
        base = base.strip()
        if not base:
            continue
        forms.append(base)
        collapsed = re.sub(r"\s+", "", base)
        forms.append(collapsed)
        forms.append(re.sub(r"\s+", "-", base))
        tokens = base.split()
        if len(tokens) > 1 and tokens[-1].lower() in _GENERIC_TAIL_WORDS:
            forms.append(" ".join(tokens[:-1]))
            forms.append("".join(tokens[:-1]))
    return list(dict.fromkeys(f for f in forms if len(f) >= 3))


def _company_domain_forms(company: str, aliases: list[str]) -> list[str]:
    """`spotai.com`-style domain candidates for each name."""
    out: list[str] = []
    for base in [company, *aliases]:
        stem = re.sub(r"[^0-9a-z]", "", base.casefold())
        if len(stem) >= 3:
            out.extend(stem + tld for tld in _DOMAIN_TLDS)
    return list(dict.fromkeys(out))


def _founder_forms(founder: str) -> list[str]:
    """A founder's full name plus each distinctive name-part (len >= 5) so a
    stray surname is caught even when the full name isn't spelled out."""
    founder = founder.strip()
    if not founder:
        return []
    forms = [founder]
    forms.extend(tok for tok in founder.split() if len(tok) >= 5)
    return list(dict.fromkeys(forms))
