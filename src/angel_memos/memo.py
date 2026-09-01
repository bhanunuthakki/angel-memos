"""Phase B: generate the long bull/bear memo, structured doc entries,
exit math, and publish to Google Docs.

Inputs: `<folder>/decision.md` (YAML frontmatter), AL memo PDF, optional
pitch deck PDF, free-form notes.

Outputs in `<folder>`:
  - `memo_private.md` — long-form adversarial bull/bear memo (always)
  - `private_entry.json` — structured private-doc entry (always; cached)
  - `public_entry.json` — structured public-doc entry (buys only; cached)
  - `memo_public.md` — Markdown rendering of `public_entry.json` (buys only)
  - `exit_math.xlsx` — scenario xlsx (committed + non-`custom` methods only)
  - `decision_review.md` — adversarial review of the decision (if enabled)

Side effects:
  - Appends a new entry at the top of the Portfolio (buys) or Passed Deals
    (pass/hold) section of the private Google Doc.
  - For buys, appends a new entry at the top of the Memos section of the
    public Google Doc.

`publish_decision_to_docs` reads the cached `*_entry.json` artifacts and
re-inserts them — useful for iterating on the style guides without
re-running the heavy Claude calls.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import yaml

from angel_memos.claude import Purpose, call_llm
from angel_memos.config import Config, load_config
from angel_memos.doc_entries import (
    PrivateDocEntry,
    PublicDocEntry,
    generate_private_entry,
    generate_public_entry,
    render_public_entry_markdown,
)
from angel_memos.exit_math import generate_exit_math
from angel_memos.google_docs import insert_private_entry, insert_public_entry
from angel_memos.masking import PublicMemoLeakError, find_public_leaks
from angel_memos.materials import (
    Materials,
    load_materials,
    load_or_parse_angellist,
    load_or_parse_deck,
    read_text,
)
from angel_memos.models import (
    AngelListMetadata,
    Decision,
    ValuationMethod,
    Verdict,
)
from angel_memos.pdf_utils import rasterize_pdf

_PRIVATE_ENTRY_FILENAME = "private_entry.json"
_PUBLIC_ENTRY_FILENAME = "public_entry.json"
# Records the decision.md hash the cached entries were generated from, so
# `publish` can refuse to push entries that no longer match a since-edited
# decision (a buy->pass flip would otherwise publish contradictory content).
_ENTRY_META_FILENAME = ".entry_meta.json"
_PUBLIC_APPROVAL_FILENAME = ".public_approval.json"


class StaleEntryError(RuntimeError):
    """Raised when cached entry JSONs were generated from a different
    decision.md than the one now on disk. Re-run `memo`, or `--force`."""


_MEMO_SYSTEM_PROMPT = """You are a senior investment analyst writing a 2-3 page
adversarial bull/bear investment memo for an angel-stage private company.
Operate in two modes simultaneously:
  - Generator (Bull Analyst): construct the strongest investment thesis
  - Discriminator (Bear Analyst): systematically attack the bull thesis

Output structure (Markdown). Use exactly these section headings:

  # <Company> — Investment Memo

  ## 1. Business Overview
  Core products/services and how they generate revenue. Decompose the
  actual value exchange, not the company's pitch. Unit economics,
  contract structure. First-principles: what is the atomic unit of value
  this company creates, and what would have to be true for it to 10x?

  ## 2. Strategy & Stated Direction
  Synthesize management's articulated strategy. Bull lens: where is it
  internally consistent? Bear lens: where does it conflict with observed
  behavior or market reality?

  ## 3. Value Chain & Market Position
  Buyers, decision-makers, budget holders. Position in the value chain.
  Switching costs quantified. Platform dynamics — specific, not "network
  effects" handwave.

  ## 4. Competitive Landscape & Differentiation
  Direct + adjacent competitors. For each: strengths, weaknesses, intent.
  Is the differentiation structural (architecture/data/network) or
  executional (speed/talent/brand)? Visible to the customer at purchase
  decision? Replicable in 18 months by a well-funded competitor?

  ## 5. Opportunities & Risks
  Structured as explicit bull/bear debate.
  - Bull case: growth vectors, underappreciated tailwinds, non-linear upside
  - Bear case: competitive/tech/regulatory/execution/financial/key-person
    risks; base-rate analysis; pre-mortem; adverse selection signals
  - Resolution: for each major disagreement, what evidence in 6-12 months
    would confirm or disconfirm?

  ## 6. Team Assessment
  Founder backgrounds + track record. Bull: what suggests they can execute?
  Bear: gaps (commercial / technical / operational), key-person risk, has
  the team operated at the scale they're targeting?

  ## 7. Financials, Funding & Valuation
  Investors, rounds, amounts, investor quality. Available metrics (ARR,
  growth, gross margin, burn, runway). Operational KPIs (retention, NRR,
  engagement). Current valuation + implied multiples.
  MANDATORY: a "Comparable multiples (as-of dated)" table rendering the
  decision's benchmarks — Comparable | Multiple | Basis | As-of date |
  Source — from the benchmark fields (multiple_as_of, multiple_source).
  A benchmark missing its as-of date is rendered with "UNDATED — verify
  before relying" in the date column, never silently.

  ## 8. Scenario Analysis — Risk-Adjusted Net Returns
  Render the user-supplied scenarios from decision.md as a Markdown table
  (Scenario | Probability | Key Assumptions | Gross | Net MoM). Include a
  probability-weighted expected return and an asymmetry score
  (P-weighted upside / P-weighted downside). For seed/pre-seed deals,
  explicitly flag scenarios as directional rather than point estimates.

  ## 9. Verdict & Open Questions
  State the verdict from decision.md, justified in one sentence from the
  analysis above. List 2-3 falsifiable conditions that would change the
  rating. List 3-5 open questions that would materially sharpen the thesis.

Operating principles:
  - First principles over analogies. "Uber of X" is a hypothesis, not evidence.
  - Adversarial rigor. Never present a bull point without immediately
    stress-testing it.
  - Calibrated uncertainty. Probability ranges, not point estimates.
    Distinguish "I don't know" from "this is unknowable."
  - Asymmetry-first. The goal is structurally favorable upside/downside
    after probability + fees, not "good company."
  - Evidence hierarchy. Audited financials > verified KPIs > management
    claims > analyst estimates > narrative. Flag low-quality evidence.
  - Fee awareness. Model net returns after AL fees + carry waterfall."""


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def parse_decision(path: Path) -> Decision:
    """Load `decision.md`. Supports either a pure YAML file or YAML
    frontmatter delimited by `---` markers with a prose body that we
    ignore (the prose is for the user's own benefit)."""
    text = path.read_text(encoding="utf-8")
    yaml_text = _extract_yaml_frontmatter(text)
    data = yaml.safe_load(yaml_text)
    return Decision.model_validate(data)


def run_memo_phase(
    folder: Path,
    config: Config | None = None,
    *,
    append_to_docs: bool = True,
    run_review: bool = True,
    skip_long_memo: bool = False,
    force_publish: bool = False,
    today: date | None = None,
) -> dict[str, Path]:
    """End-to-end Phase B for one company folder.

    Steps:
      1. (optional) adversarial decision review -> writes `decision_review.md`
      2. Generate long bull/bear memo (Claude) -> `memo_private.md`
         (skipped if `skip_long_memo=True` AND `memo_private.md` already exists)
      3. Generate structured private entry (Claude) -> `private_entry.json`
      4. For buys only:
         a. Parse the deck (cached) -> `.deck_content_cache.json`
         b. Generate structured public entry (Claude) -> `public_entry.json`
         c. Render Markdown -> `memo_public.md`
      5. For committed decisions (non-pass, non-custom): exit_math.xlsx
      6. Publish to Google Docs (unless `append_to_docs=False`).

    `skip_long_memo` is the right flag for re-running entry generation
    after iterating on style guides — saves ~$0.50 and 90 seconds per
    company on the long-memo Claude call.
    """
    if config is None:
        config = load_config()
    if today is None:
        today = date.today()

    decision_path = folder / "decision.md"
    if not decision_path.is_file():
        raise FileNotFoundError(f"decision.md not found in {folder}. Run /angel-decide first.")
    decision = parse_decision(decision_path)

    # Auth preflight: if we're going to publish, verify credentials NOW so a
    # headless/unauthorized run fails fast instead of after ~$1 of Claude work.
    if append_to_docs:
        from angel_memos.google_docs import ensure_credentials

        ensure_credentials()

    materials = load_materials(folder)
    angellist = load_or_parse_angellist(folder, materials)

    outputs: dict[str, Path] = {}

    if run_review:
        from angel_memos.review import run_decision_review

        review_path = run_decision_review(folder)
        outputs["review"] = review_path

    # Long-form adversarial memo. Regenerated by default; skipped if the
    # caller passed `skip_long_memo=True` AND a memo already exists on disk
    # (lets republish runs avoid $0.50 / 90s of redundant Claude work).
    private_path = folder / "memo_private.md"
    if not (skip_long_memo and private_path.is_file()):
        private_md = _generate_long_memo(materials, angellist, decision)
        private_path.write_text(private_md, encoding="utf-8")
    outputs["private_memo"] = private_path

    # Deck content is needed for the structured private entry and (for buys)
    # for the structured public entry. Parse once, cache, share.
    deck_content = load_or_parse_deck(folder, materials)

    # Structured private entry (always).
    private_entry = generate_private_entry(decision, angellist, deck_content)
    private_entry_path = folder / _PRIVATE_ENTRY_FILENAME
    private_entry_path.write_text(private_entry.model_dump_json(indent=2), encoding="utf-8")
    outputs["private_entry"] = private_entry_path

    # Structured public entry (buys only).
    public_entry: PublicDocEntry | None = None
    is_buy = decision.verdict in {Verdict.BUY, Verdict.STRONG_BUY}
    if is_buy:
        public_entry = generate_public_entry(decision, angellist, materials, today)
        public_entry_path = folder / _PUBLIC_ENTRY_FILENAME
        public_entry_path.write_text(public_entry.model_dump_json(indent=2), encoding="utf-8")
        outputs["public_entry"] = public_entry_path

        public_md_path = folder / "memo_public.md"
        public_md_path.write_text(render_public_entry_markdown(public_entry), encoding="utf-8")
        outputs["public_memo"] = public_md_path

    # Stamp the decision.md hash the entries were generated from, so a later
    # `publish` can detect a since-edited decision and refuse to publish stale
    # entries.
    _write_entry_meta(folder, today)

    skip_exit_math = (
        decision.verdict == Verdict.PASS or decision.valuation_method == ValuationMethod.CUSTOM
    )
    if not skip_exit_math:
        xlsx_path = folder / "exit_math.xlsx"
        generate_exit_math(decision, angellist, xlsx_path)
        outputs["xlsx"] = xlsx_path

    if append_to_docs:
        publish_decision_to_docs(folder, config, force=force_publish)

    return outputs


def publish_decision_to_docs(folder: Path, config: Config, *, force: bool = False) -> None:
    """Publish the cached entries to the Google Docs.

    Reads `decision.md`, `private_entry.json`, and (for buys)
    `public_entry.json` from disk and inserts under the appropriate
    container headings.

    For buys, the public entry is scanned for deal-identifying content and
    requires a human approval receipt bound to the exact public-entry hash.
    `force` never bypasses this privacy gate.

    Raises:
      FileNotFoundError: cached entry files are missing. Run `memo` first.
      PublicMemoLeakError: the public entry still leaks deal identity.
    """
    decision = parse_decision(folder / "decision.md")
    private_entry = _load_private_entry(folder)
    is_buy = decision.verdict in {Verdict.BUY, Verdict.STRONG_BUY}
    container = "Portfolio" if is_buy else "Passed Deals"

    # Refuse to publish cached entries that were generated from a different
    # decision.md (unless forced) — otherwise a buy->pass edit silently
    # publishes contradictory content under headings from the new decision.
    if not force:
        _check_entry_freshness(folder)

    # Gate BEFORE either external write so a leak blocks BOTH docs (and a
    # retry can't leave the private doc appended while the public leg is
    # blocked).
    public_entry = _load_public_entry(folder) if is_buy else None
    if public_entry is not None:
        _guard_public_entry(folder, decision, public_entry)
        _check_public_approval(folder, public_entry)

    insert_private_entry(
        config.private_doc_id,
        container_heading=container,
        decision=decision,
        entry=private_entry,
    )

    if public_entry is not None:
        insert_public_entry(
            config.public_doc_id,
            container_heading="Memos",
            entry=public_entry,
        )


def _guard_public_entry(
    folder: Path,
    decision: Decision,
    public_entry: PublicDocEntry,
) -> None:
    """Raise `PublicMemoLeakError` if the public entry still contains any
    deal-identifying string. Founder names + the AL company label come from
    the (cached) AngelList parse."""
    al_company: str | None = None
    founders: list[str] = []
    angellist: AngelListMetadata | None = None
    try:
        materials = load_materials(folder)
        angellist = load_or_parse_angellist(folder, materials)
        al_company = angellist.company
        founders = list(angellist.founders)
    except Exception:
        # If AL metadata can't be loaded we still gate on the decision-derived
        # identifiers rather than skipping the check.
        pass

    aliases = [al_company] if al_company and al_company != decision.company else []
    private_values: list[float] = []
    if angellist is not None:
        private_values.extend(
            value
            for value in (
                angellist.pre_money_usd,
                angellist.estimated_round_size_usd,
                angellist.allocation_usd,
                angellist.leads_investment_usd,
                angellist.min_investment_usd,
                angellist.total_prior_capital_usd,
            )
            if value is not None
        )
    configured_terms = [
        term.strip()
        for term in os.environ.get("ANGEL_MEMOS_PRIVATE_ONLY_TERMS", "").split(",")
        if term.strip()
    ]
    source_private_terms = list(angellist.co_investors) if angellist is not None else []
    if decision.current_base_metric_usd is not None:
        private_values.append(decision.current_base_metric_usd)
    leaks = find_public_leaks(
        public_entry.model_dump_json(),
        company=decision.company,
        aliases=aliases,
        founders=founders,
        check_usd=decision.check_usd,
        post_money_usd=decision.post_money_usd,
        private_values=private_values,
        private_only_terms=[
            *configured_terms,
            *source_private_terms,
            *public_entry.private_only_terms,
        ],
    )
    if leaks:
        raise PublicMemoLeakError(leaks)


def public_entry_hash(public_entry: PublicDocEntry) -> str:
    """Hash the exact serialized public output that a human approved."""
    payload = public_entry.model_dump(exclude={"approval_sha256"}, mode="json")
    return hashlib.sha256(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")).hexdigest()


def approve_public_entry(folder: Path, *, approved_by: str) -> Path:
    """Record explicit human approval for the current public entry bytes."""
    if not approved_by.strip():
        raise ValueError("approved_by must not be empty")
    entry = _load_public_entry(folder)
    path = folder / _PUBLIC_APPROVAL_FILENAME
    approval_hash = public_entry_hash(entry)
    approved_entry = entry.model_copy(update={"approval_sha256": approval_hash})
    (folder / _PUBLIC_ENTRY_FILENAME).write_text(
        approved_entry.model_dump_json(indent=2), encoding="utf-8"
    )
    path.write_text(
        json.dumps({"public_sha256": approval_hash, "approved_by": approved_by.strip()}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def _check_public_approval(folder: Path, public_entry: PublicDocEntry) -> None:
    path = folder / _PUBLIC_APPROVAL_FILENAME
    if not path.is_file():
        raise RuntimeError(
            "public entry lacks human approval; run `angel-memos approve-public` after review"
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("public approval receipt is invalid; review and approve again") from exc
    expected_hash = public_entry_hash(public_entry)
    if (
        receipt.get("public_sha256") != expected_hash
        or public_entry.approval_sha256 != expected_hash
        or not str(receipt.get("approved_by", "")).strip()
    ):
        raise RuntimeError(
            "public approval receipt does not match the exact public output; review and approve again"
        )


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


def _decision_sha(folder: Path) -> str:
    """SHA-256 of decision.md's bytes — the freshness key for cached entries."""
    return hashlib.sha256((folder / "decision.md").read_bytes()).hexdigest()


def _write_entry_meta(folder: Path, today: date) -> None:
    meta = {"decision_sha": _decision_sha(folder), "generated_on": today.isoformat()}
    (folder / _ENTRY_META_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _check_entry_freshness(folder: Path) -> None:
    """Raise `StaleEntryError` if `.entry_meta.json` records a different
    decision.md hash than the current file. Silent no-op when the meta is
    absent (entries predate this check) — can't prove staleness, so allow."""
    meta_path = folder / _ENTRY_META_FILENAME
    if not meta_path.is_file():
        return
    try:
        recorded = json.loads(meta_path.read_text(encoding="utf-8")).get("decision_sha")
    except (json.JSONDecodeError, OSError):
        return
    if recorded and recorded != _decision_sha(folder):
        raise StaleEntryError(
            f"decision.md in {folder} changed since the cached entries were "
            f"generated. Re-run `angel-memos memo` to regenerate them, or pass "
            f"--force to publish the stale entries anyway."
        )


def _load_private_entry(folder: Path) -> PrivateDocEntry:
    path = folder / _PRIVATE_ENTRY_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run `angel-memos memo <Company>` first to "
            f"generate the structured entry."
        )
    return PrivateDocEntry.model_validate_json(path.read_text(encoding="utf-8"))


def _load_public_entry(folder: Path) -> PublicDocEntry:
    path = folder / _PUBLIC_ENTRY_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run `angel-memos memo <Company>` first to "
            f"generate the structured entry (verdict must be buy / strong_buy)."
        )
    return PublicDocEntry.model_validate_json(path.read_text(encoding="utf-8"))


def _extract_yaml_frontmatter(text: str) -> str:
    """Return the YAML body of `text`. Accepts:
    - Pure YAML (no fences) — returned as-is
    - YAML between `---` fences (Jekyll-style frontmatter) — fences stripped
    """
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1:
            end = text.find("\n---", 4)
        if end == -1:
            raise ValueError("decision.md frontmatter opened with --- but never closed")
        return text[4:end]
    return text


def _generate_long_memo(
    materials: Materials,
    angellist: AngelListMetadata,
    decision: Decision,
) -> str:
    """Build the bull/bear memo-generation prompt and call Claude, validating
    the result. Retries once, then raises, so a refusal / usage-limit banner /
    truncated response is never archived as the permanent investment rationale.
    """
    with tempfile.TemporaryDirectory(prefix="angel_memos_memo_") as tmp_str:
        tmp_dir = Path(tmp_str)
        al_pngs = rasterize_pdf(materials.angellist.path, tmp_dir / "al")
        deck_pngs: list[Path] = []
        if materials.deck is not None:
            deck_pngs = rasterize_pdf(materials.deck.path, tmp_dir / "deck")
        prompt = _format_memo_prompt(al_pngs, deck_pngs, materials, angellist, decision)

        last_problem = ""
        for _attempt in range(2):
            memo_md = call_llm(
                prompt,
                purpose=Purpose.LONG_MEMO,
                image_paths=al_pngs + deck_pngs,
            )
            missing = _validate_long_memo(memo_md, decision=decision)
            if not missing:
                return memo_md
            last_problem = "; ".join(missing)
        raise RuntimeError(
            f"Generated memo failed validation ({last_problem}). The response "
            f"looks like a refusal, a usage-limit banner, or a truncated memo — "
            f"refusing to save it as memo_private.md."
        )


# The nine numbered sections the memo system prompt mandates.
_REQUIRED_MEMO_SECTIONS: tuple[str, ...] = tuple(f"## {n}." for n in range(1, 10))
_MIN_MEMO_CHARS = 1500


# The one multiple-like driver each benchmark type carries (SeedBenchmark
# has none). Probed by name so the validator needs no isinstance ladder.
_BENCHMARK_MULTIPLE_FIELDS: tuple[str, ...] = (
    "exit_multiple",
    "ev_ebitda",
    "pe_ratio",
    "revenue_multiple",
)


_NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?")
_PERCENT_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*%")


_TABLE_SEPARATOR = re.compile(r"\|\s*:?-{3,}")


def _table_rows(section: str) -> list[str]:
    """Markdown table rows in a section — but only when the section contains
    a real table. A `|---` separator line is the signature no decorative
    pipe-in-prose sentence carries; without one, no line counts as a row
    (final adversarial-probe finding: "Note | Symbotic at 10.3x | end" must
    not satisfy the table mandate)."""
    if not _TABLE_SEPARATOR.search(section):
        return []
    return [line for line in section.splitlines() if line.count("|") >= 2]


def _row_for(name: str, rows: list[str]) -> str | None:
    """First table row mentioning `name` (case-insensitive)."""
    needle = name.casefold()
    return next((row for row in rows if needle in row.casefold()), None)


def _row_has_number(row: str, target: float) -> bool:
    """True when any numeric token in the row equals `target` — parsed and
    compared as floats so "10.3", "10.30", and "10.3x" all match 10.3 and a
    different number never does."""
    return any(
        math.isclose(float(tok), target, rel_tol=1e-3, abs_tol=0.005)
        for tok in _NUMBER_TOKEN.findall(row)
    )


def _row_has_probability(row: str, probability: float) -> bool:
    """Percent tokens are authoritative when present ("20%", "20.0 %"); a
    row with no percent token at all falls back to decimal rendering. The
    percent-first rule stops a gross-MoM cell like "0.2x" from accidentally
    satisfying probability 0.2."""
    percents = [float(tok) for tok in _PERCENT_TOKEN.findall(row)]
    if percents:
        return any(math.isclose(pct, probability * 100, abs_tol=0.05) for pct in percents)
    return any(
        math.isclose(float(tok), probability, abs_tol=0.0005) for tok in _NUMBER_TOKEN.findall(row)
    )


def _validate_long_memo(md: str, *, decision: Decision | None = None) -> list[str]:
    """Return a list of structural problems with a generated memo (empty when
    it looks like a real 9-section bull/bear memo).

    The system prompt's MANDATORY items were previously prose-only — the
    model could (and did) skip the dated-comparables table and the Net-MoM
    scenario columns with nothing catching it. With a `decision`, each
    benchmark and scenario must appear ON A TABLE ROW in its section with
    its value on the same row — prose mentions scattered through the section
    don't count, so a fabricated or missing table fails. Numbers are parsed
    and compared as floats, so any legitimate rendering ("10.3", "10.30x",
    "25%", "25.0 %", "0.25") matches and a different number never does."""
    problems: list[str] = []
    if len(md.strip()) < _MIN_MEMO_CHARS:
        problems.append(f"too short ({len(md.strip())} < {_MIN_MEMO_CHARS} chars)")
    missing = [s for s in _REQUIRED_MEMO_SECTIONS if s not in md]
    if missing:
        problems.append("missing sections: " + ", ".join(missing))
    if decision is None:
        return problems

    if decision.benchmarks:
        if "Comparable multiples" not in md:
            problems.append("missing the mandatory 'Comparable multiples (as-of dated)' table")
        section7 = md.partition("## 7.")[2].partition("## 8.")[0]
        rows7 = _table_rows(section7)
        for b in decision.benchmarks:
            row = _row_for(b.comparable, rows7)
            if row is None:
                problems.append(
                    f"benchmark comparable '{b.comparable}' has no table row in section 7"
                )
                continue
            multiple = next(
                (
                    getattr(b, field)
                    for field in _BENCHMARK_MULTIPLE_FIELDS
                    if getattr(b, field, None) is not None
                ),
                None,
            )
            if multiple is not None and not _row_has_number(row, multiple):
                problems.append(f"benchmark '{b.comparable}' row lacks its multiple {multiple:g}")
            if b.multiple_as_of:
                if b.multiple_as_of.casefold() not in row.casefold():
                    problems.append(
                        f"benchmark '{b.comparable}' row lacks its as-of date {b.multiple_as_of}"
                    )
            elif "undated" not in row.casefold():
                problems.append(
                    f"benchmark '{b.comparable}' has no as-of date and its row "
                    "carries no UNDATED marker"
                )
    if decision.scenarios:
        section8 = md.partition("## 8.")[2].partition("## 9.")[0]
        if "Net MoM" not in section8:
            problems.append("scenario table lacks the mandated Net MoM column")
        rows8 = _table_rows(section8)
        for s in decision.scenarios:
            row = _row_for(s.name, rows8)
            if row is None:
                problems.append(f"scenario '{s.name}' has no table row in section 8")
            elif not _row_has_probability(row, s.probability):
                problems.append(f"scenario '{s.name}' row lacks its probability {s.probability:g}")
    return problems


def _format_memo_prompt(
    al_pngs: list[Path],
    deck_pngs: list[Path],
    materials: Materials,
    angellist: AngelListMetadata,
    decision: Decision,
) -> str:
    refs = "\n".join(f"@{p.as_posix()}" for p in al_pngs + deck_pngs)
    notes_text = "\n\n".join(f"--- {n.path.name} ---\n{read_text(n)}" for n in materials.notes)
    decision_summary = (
        f"USER DECISION (from decision.md):\n"
        f"  Verdict: {decision.verdict.value}\n"
        f"  Conviction: {decision.conviction.value}\n"
        f"  Check: ${decision.check_usd:,.0f}\n"
        f"  Post-money: ${decision.post_money_usd:,.0f}\n"
        f"  Valuation method: {decision.valuation_method.value}\n"
        f"  Top reasons:\n"
        + "".join(f"    - {r}\n" for r in decision.top_reasons)
        + "  Top risks accepted:\n"
        + "".join(f"    - {r}\n" for r in decision.top_risks)
        + f"\n  Raw reasoning:\n  {decision.raw_reasoning}\n"
    )
    if decision.scenarios:
        decision_summary += "\n  Scenarios:\n"
        for s in decision.scenarios:
            decision_summary += f"    - {s.model_dump_json()}\n"
    if decision.benchmarks:
        decision_summary += "\n  Benchmarks:\n"
        for b in decision.benchmarks:
            decision_summary += f"    - {b.model_dump_json()}\n"

    al_summary = (
        f"AngelList metadata (parsed):\n"
        f"  Company: {angellist.company}\n"
        f"  Round: {angellist.round_label}\n"
        f"  Markets: {', '.join(angellist.markets)}\n"
        f"  Pre-money: ${angellist.pre_money_usd:,.0f}\n"
        f"  Founders: {', '.join(angellist.founders)}\n"
        f"  Co-investors: "
        f"{', '.join(angellist.co_investors) if angellist.co_investors else 'none disclosed'}\n"
    )

    parts: list[str] = [refs, "", al_summary, "", decision_summary]
    if notes_text:
        parts.extend(["Freeform notes:", notes_text])
    parts.append(
        "\nWrite the 2-3 page bull/bear memo in Markdown following the section "
        "structure in the system prompt. The user has already made the decision; "
        "your job is to produce the adversarial analysis that justifies it AND "
        "stress-tests it. Be concrete; cite specifics from the materials."
    )
    return "\n".join(parts)
