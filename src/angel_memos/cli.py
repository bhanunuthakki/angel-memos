"""angel-memos CLI entrypoint.

Subcommands:
  - `angel-memos diligence <company>` -- Phase A; writes diligence_topics.html
  - `angel-memos score <company>`     -- Rubric scorecard; writes score_report.{json,md}
  - `angel-memos review <company>`    -- Adversarial review of decision.md
  - `angel-memos memo <company>`      -- Phase B; writes memos + exit math; publishes docs
  - `angel-memos publish <company>`   -- Republish a decision to docs without regenerating memos
  - `angel-memos ingest`              -- Move extension drops from Downloads into Evaluation/
  - `angel-memos watch`               -- Daemon: ingest drops + auto-run quick research
  - `angel-memos investors ...`       -- Cross-deal investor DB (backfill / export)

Company-folder resolution:
  1. If `--folder <path>` is passed, use it directly.
  2. Else look for `<Portfolio>/<Company>/` (committed deals are checked first).
  3. Else look for `<Evaluation>/<Company>/`.
  4. Else auto-migrate flat files matching `<Company> *` in `<Evaluation>/` into
     a new `<Evaluation>/<Company>/` subfolder, then proceed.
"""

import re
import shutil
import time
from pathlib import Path

import click

from angel_memos import investors as investors_db
from angel_memos.config import Config, load_config
from angel_memos.diligence import run_diligence_phase
from angel_memos.ingest import IngestResult, default_inbox, run_ingest
from angel_memos.memo import (
    parse_decision,
    publish_decision_to_docs,
    run_memo_phase,
)
from angel_memos.review import run_decision_review
from angel_memos.scoring import (
    DealArchetype,
    RubricVersion,
    ScoreReport,
    calibration_summary,
    run_score_phase,
)


@click.group()
def main() -> None:
    """Generate adversarial bull/bear investment memos for angel-stage deals."""


@main.command()
@click.argument("company")
@click.option(
    "--folder",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the company folder location (skips name-based resolution).",
)
def diligence(company: str, folder: Path | None) -> None:
    """Phase A: read materials, write diligence_topics.md."""
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    click.echo(f"Running diligence on {target}")
    out = run_diligence_phase(target)
    click.echo(f"Wrote {out}")


@main.command()
@click.argument("company")
@click.option(
    "--folder",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the company folder location (skips name-based resolution).",
)
@click.option(
    "--no-docs",
    is_flag=True,
    default=False,
    help="Skip the Google Docs append step (useful before OAuth is set up).",
)
@click.option(
    "--no-review",
    is_flag=True,
    default=False,
    help="Skip the adversarial decision review step (saves ~$0.10 + 1 min).",
)
@click.option(
    "--skip-long-memo",
    is_flag=True,
    default=False,
    help=(
        "Reuse existing memo_private.md if present (skip the long-form "
        "bull/bear regeneration). Useful when re-running just to refresh "
        "the structured doc entries after a style-guide tweak."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Bypass the public-memo leak gate and publish even if the anonymized "
        "entry still contains an identifier (use only after reviewing a false "
        "positive)."
    ),
)
def memo(
    company: str,
    folder: Path | None,
    no_docs: bool,
    no_review: bool,
    skip_long_memo: bool,
    force: bool,
) -> None:
    """Phase B: review the decision, generate memos and exit math, publish to docs."""
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    click.echo(f"Running memo on {target}")
    outputs = run_memo_phase(
        target,
        config=cfg,
        append_to_docs=not no_docs,
        run_review=not no_review,
        skip_long_memo=skip_long_memo,
        force_publish=force,
    )
    for label, path in outputs.items():
        click.echo(f"  {label}: {path}")
    if no_docs:
        click.echo("Skipped Google Docs append (--no-docs).")
    else:
        click.echo("Published to Google Docs.")


@main.command()
@click.argument("company")
@click.option(
    "--folder",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the company folder location (skips name-based resolution).",
)
@click.option(
    "--rubric-version",
    type=click.Choice([version.value for version in RubricVersion], case_sensitive=False),
    default=RubricVersion.V2_2.value,
    show_default=True,
    help=(
        "Scoring contract. V2.2 is the comp-free default with 15% "
        "co-investor weight; v2.1, v2, and v1 remain rollback paths."
    ),
)
@click.option(
    "--archetype",
    type=click.Choice(
        [value.value for value in DealArchetype if value is not DealArchetype.GENERAL],
        case_sensitive=False,
    ),
    default=None,
    help="Archetype-aware evidence profile. Omit to classify through the governed LLM route.",
)
def score(
    company: str,
    folder: Path | None,
    rubric_version: str,
    archetype: str | None,
) -> None:
    """Rubric scorecard: deterministic factor model + LLM-judge subscores.
    Writes score_report.json (consumed by /angel-decide) and score_report.md.
    Tier is 'deep' automatically when research_memo.md exists in the folder."""
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    version = RubricVersion(rubric_version)
    if version is RubricVersion.V1 and archetype is not None:
        raise click.ClickException("--archetype requires --rubric-version v2, v2.1, or v2.2")
    selected_archetype = DealArchetype(archetype) if archetype is not None else None
    click.echo(f"Scoring {target}")
    outputs = run_score_phase(
        target,
        rubric_version=version,
        archetype=selected_archetype,
    )
    report = ScoreReport.model_validate_json(outputs["score_json"].read_text(encoding="utf-8"))
    click.echo(f"  Total: {report.total:.0f}/100 ({report.band.value}, {report.tier} tier)")
    if report.rubric_version in {
        RubricVersion.V2,
        RubricVersion.V2_1,
        RubricVersion.V2_2,
    }:
        click.echo(
            f"  Effective: {(report.effective_band or report.band).value}; "
            f"coverage {report.score_coverage:.0%}; archetype {report.archetype.value}"
        )
    for label, path in outputs.items():
        click.echo(f"  {label}: {path}")


@main.command(name="score-eval")
def score_eval() -> None:
    """Calibration monitor: gate fire-rates and band distribution across every
    existing score_report.json (Evaluation, Portfolio, Passed). No LLM calls.
    A gate firing on ~every deal discriminates nothing and needs retuning."""
    cfg = load_config()
    reports: list[ScoreReport] = []
    skipped = 0
    for root in (cfg.evaluation_root, cfg.portfolio_root, cfg.passed_root):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/score_report.json")):
            try:
                reports.append(ScoreReport.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                skipped += 1
    click.echo(calibration_summary(reports))
    if skipped:
        click.echo(f"  ({skipped} report(s) skipped: legacy/invalid contract)")


@main.command()
@click.option(
    "--inbox",
    type=click.Path(path_type=Path),
    default=None,
    help="Extension drop folder (default: ~/Downloads/angel-memos).",
)
def ingest(inbox: Path | None) -> None:
    """Move ready extension drops into Evaluation/<Company>/ (one-shot)."""
    cfg = load_config()
    results = run_ingest(inbox or default_inbox(), cfg)
    if not results:
        click.echo("Nothing to ingest.")
        return
    for result in results:
        _echo_ingest(result)


@main.command()
@click.option(
    "--inbox",
    type=click.Path(path_type=Path),
    default=None,
    help="Extension drop folder (default: ~/Downloads/angel-memos).",
)
@click.option(
    "--interval",
    type=int,
    default=20,
    show_default=True,
    help="Seconds between inbox scans.",
)
def watch(inbox: Path | None, interval: int) -> None:
    """Daemon: ingest extension drops and auto-run quick research.

    For drops whose job.json requested tier=quick, runs diligence + score
    in-process (Claude via subscription CLI). Deep research is deliberate:
    launch /angel-research from a Claude Code session. Ctrl+C to stop."""
    cfg = load_config()
    target_inbox = inbox or default_inbox()

    def _on_ingest_error(drop: Path, exc: Exception) -> None:
        click.echo(f"  QUARANTINED bad drop {drop.name}: {exc}", err=True)

    click.echo(f"Watching {target_inbox} (every {interval}s). Ctrl+C to stop.")
    while True:
        try:
            for result in run_ingest(target_inbox, cfg, on_error=_on_ingest_error):
                _echo_ingest(result)
                if result.job.tier == "quick":
                    # Isolate quick-tier failures per drop: a transient Claude
                    # error on one company must not abort ingestion of the rest
                    # or wedge the daemon.
                    try:
                        _run_quick_tier(result)
                    except Exception as exc:
                        click.echo(
                            f"  ERROR: quick tier failed for {result.job.company}: {exc}. "
                            f"Re-run manually: angel-memos diligence '{result.job.company}' "
                            f"&& angel-memos score '{result.job.company}'",
                            err=True,
                        )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            click.echo(f"  ERROR: {exc}", err=True)
        time.sleep(interval)


def _echo_ingest(result: IngestResult) -> None:
    click.echo(f"Ingested {result.job.company} -> {result.folder} ({len(result.moved)} files)")
    if result.deduped:
        click.echo(
            f"  Discarded {len(result.deduped)} byte-identical re-capture(s): "
            f"{', '.join(result.deduped)}"
        )
    if result.missing_angellist:
        click.echo(
            "  WARNING: no angellist*.pdf in this drop — diligence needs the AL memo.",
            err=True,
        )
    if result.missing_deck:
        click.echo(
            "  WARNING: no deck/pitch PDF in this drop — the scorecard will run "
            "deck-less and be lower-confidence. Re-capture the deck if possible.",
            err=True,
        )


def _run_quick_tier(result: IngestResult) -> None:
    """Quick tier = diligence + scorecard, in-process. Skipped when the AL
    memo is missing since both phases require it."""
    if result.missing_angellist:
        click.echo("  Skipping quick research (no AL memo).", err=True)
        return
    click.echo(f"  Quick research: diligence on {result.job.company} ...")
    out = run_diligence_phase(result.folder)
    click.echo(f"  Wrote {out}")
    click.echo(f"  Quick research: scorecard on {result.job.company} ...")
    outputs = run_score_phase(result.folder)
    click.echo(f"  Wrote {outputs['score_md']}")


@main.group(name="investors")
def investors_group() -> None:
    """Cross-deal investor database (grades co-investors and syndicate leads)."""


@investors_group.command()
def backfill() -> None:
    """Seed the DB from co-investors named in existing deal folders' caches."""
    cfg = load_config()
    conn = investors_db.connect()
    records = investors_db.backfill(conn, cfg)
    click.echo(f"Backfilled {len(records)} investor(s) into {investors_db.db_path()}")
    out = investors_db.export_markdown(conn, cfg)
    click.echo(f"Exported {out}")


@investors_group.command()
def export() -> None:
    """Write the human-readable investors.md next to the Drive roots."""
    cfg = load_config()
    conn = investors_db.connect()
    out = investors_db.export_markdown(conn, cfg)
    click.echo(f"Exported {out}")


@main.command()
@click.argument("company")
@click.option(
    "--folder",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the company folder location.",
)
def review(company: str, folder: Path | None) -> None:
    """Adversarially pressure-test the decision in `<company>/decision.md` and
    write `decision_review.md`. Run after `/angel-decide`, before `memo`."""
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    click.echo(f"Running adversarial review on {target}")
    out = run_decision_review(target)
    click.echo(f"Wrote {out}")


@main.command()
@click.option(
    "--port", type=int, default=8765, show_default=True, help="Localhost port to serve on."
)
@click.option("--no-browser", is_flag=True, default=False, help="Do not auto-open the browser.")
def dashboard(port: int, no_browser: bool) -> None:
    """Launch the local deal-orchestration dashboard in the browser.

    Per-deal pipeline view (save info → quick brief → diligence & decision →
    publish): click into every artifact, run each step, and open the Q&A in a
    Claude Code session. Ctrl+C to stop."""
    from angel_memos.dashboard import serve

    serve(load_config(), port=port, open_browser=not no_browser)


@main.command()
@click.argument("company")
@click.option(
    "--folder",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the company folder location.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Bypass the public-memo leak gate (use only after reviewing a false positive).",
)
def publish(company: str, folder: Path | None, force: bool) -> None:
    """Re-publish a company's cached structured entries to the Google Docs
    without regenerating them via Claude. Reads `private_entry.json` and
    (for buys) `public_entry.json` from the company folder.

    If the cache files are missing, run `angel-memos memo <Company>` first
    to generate them.
    """
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    decision = parse_decision(target / "decision.md")
    publish_decision_to_docs(target, cfg, force=force)
    click.echo(f"Published {decision.company} ({decision.verdict.value}) to Google Docs.")


def _resolve_company_folder(company: str, override: Path | None, cfg: Config) -> Path:
    """Find or auto-create the company subfolder. Returns the resolved path
    or raises a `ClickException` with actionable instructions if nothing
    matches.

    Auto-migration: if neither `Evaluation/<Company>/` nor `Portfolio/<Company>/`
    exists but flat files matching `<Company> *.{pdf,md,txt}` are present in
    Evaluation/, create `Evaluation/<Company>/` and move them in.
    """
    if override is not None:
        resolved = override.resolve()
        if not resolved.is_dir():
            raise click.ClickException(f"Folder does not exist: {resolved}")
        return resolved

    portfolio_folder = cfg.portfolio_root / company
    evaluation_folder = cfg.evaluation_root / company
    if portfolio_folder.is_dir():
        if evaluation_folder.is_dir():
            # A follow-on capture landed in Evaluation while the committed deal
            # lives in Portfolio. Resolving to Portfolio here (with its stale
            # cache) while ingest fed the new materials into Evaluation would
            # silently score/decide on the OLD round. Make the split visible.
            click.echo(
                f"  WARNING: '{company}' exists in BOTH Portfolio and Evaluation. "
                f"Using Portfolio/{company}; the Evaluation copy (a follow-on "
                f"capture?) is ignored. Pass --folder to target it explicitly.",
                err=True,
            )
        return portfolio_folder

    if evaluation_folder.is_dir():
        return evaluation_folder

    # Archived passes resolve last: an active Evaluation/Portfolio deal always
    # wins over its archive, but `angel-memos memo Dexterity` after a
    # pass-and-move must not error with "no folder".
    passed_folder = cfg.passed_root / company
    if passed_folder.is_dir():
        click.echo(
            f"  NOTE: '{company}' resolved from the Passed archive ({passed_folder}).",
            err=True,
        )
        return passed_folder

    matches = _flat_matches(cfg.evaluation_root, company)
    if not matches:
        raise click.ClickException(
            f"No folder or flat files for '{company}' under {cfg.evaluation_root}.\n"
            f"Either create {evaluation_folder}/ and drop materials inside, or "
            f"place flat files there with '{company} ' as the filename prefix "
            f"(e.g., 'HiCap Seed AL Memo.pdf')."
        )

    evaluation_folder.mkdir(parents=True, exist_ok=True)
    click.echo(f"Auto-migrating {len(matches)} flat file(s) into {evaluation_folder}/")
    for src in matches:
        dst = evaluation_folder / src.name
        shutil.move(str(src), str(dst))
        click.echo(f"  moved: {src.name}")
    return evaluation_folder


# Tokens that legitimately follow a company name in a materials filename.
# Requiring the token immediately after `<Company> ` to be one of these stops
# resolving "Acme" from sweeping in "Acme Robotics AL.pdf" (whose next token,
# "Robotics", identifies a DIFFERENT company).
_ARTIFACT_TOKENS: frozenset[str] = frozenset(
    {
        "al",
        "angellist",
        "angel",
        "deck",
        "pitch",
        "details",
        "memo",
        "notes",
        "note",
        "links",
        "seed",
        "preseed",
        "series",
        "round",
        "terms",
        "call",
    }
)


def _flat_matches(root: Path, company: str) -> list[Path]:
    """Find flat files in `root` whose names start with `<Company> ` AND whose
    next token is a known materials-artifact word.

    The artifact-token gate is what keeps `<Company>` from matching a longer
    company that shares its prefix: 'Acme Robotics AL.pdf' has next token
    'Robotics' (not an artifact word), so it is NOT swept in when resolving
    'Acme'."""
    if not root.is_dir():
        return []
    matches: list[Path] = []
    prefix = f"{company} ".lower()
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".pdf", ".md", ".txt"}:
            continue
        name = path.name.lower()
        if not name.startswith(prefix):
            continue
        remainder = name[len(prefix) :]
        first_token = re.split(r"[\s._\-]+", remainder, maxsplit=1)[0]
        if first_token in _ARTIFACT_TOKENS:
            matches.append(path)
    return matches


if __name__ == "__main__":
    main()
