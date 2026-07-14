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
from angel_memos.scoring import ScoreReport, run_score_phase


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
def score(company: str, folder: Path | None) -> None:
    """Rubric scorecard: deterministic factor model + LLM-judge subscores.
    Writes score_report.json (consumed by /angel-decide) and score_report.md.
    Tier is 'deep' automatically when research_memo.md exists in the folder."""
    cfg = load_config()
    target = _resolve_company_folder(company, folder, cfg)
    click.echo(f"Scoring {target}")
    outputs = run_score_phase(target)
    report = ScoreReport.model_validate_json(outputs["score_json"].read_text(encoding="utf-8"))
    click.echo(f"  Total: {report.total:.0f}/100 ({report.band.value}, {report.tier} tier)")
    for label, path in outputs.items():
        click.echo(f"  {label}: {path}")


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
                    except Exception as exc:  # noqa: BLE001
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
    if portfolio_folder.is_dir():
        return portfolio_folder

    evaluation_folder = cfg.evaluation_root / company
    if evaluation_folder.is_dir():
        return evaluation_folder

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


def _flat_matches(root: Path, company: str) -> list[Path]:
    """Find flat files in `root` whose names start with `<Company> `.

    Glob is case-insensitive on Windows; we still filter explicitly so
    behavior is identical across OSes."""
    if not root.is_dir():
        return []
    matches: list[Path] = []
    prefix = f"{company} ".lower()
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".pdf", ".md", ".txt"}:
            continue
        if path.name.lower().startswith(prefix):
            matches.append(path)
    return matches


if __name__ == "__main__":
    main()
