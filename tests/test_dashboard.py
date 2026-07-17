"""Dashboard pure-logic: pipeline stage detection from folder contents,
Markdown rendering, deal resolution, and the Claude Code launch command.

The HTTP layer and in-process job runner (which touch Claude) are not
exercised here — only the dependency-free logic that carries the behavior."""

from pathlib import Path

import pytest

from angel_memos.config import Config
from angel_memos.dashboard import (
    decide_launch_command,
    list_deals,
    render_markdown,
    resolve_deal,
    scan_deal,
)


def _stub_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%stub\n")


def _stage(state, key):  # type: ignore[no-untyped-def]
    return next(s for s in state.stages if s.key == key)


# ---------------------------------------------------------------------------
# Stage detection
# ---------------------------------------------------------------------------


def test_empty_folder_materials_blocked(tmp_path: Path) -> None:
    state = scan_deal("Acme", tmp_path, "Evaluation")
    assert _stage(state, "materials").status == "blocked"
    # Downstream stages are blocked with no AL memo.
    assert _stage(state, "brief").status == "blocked"
    assert _stage(state, "decision").status == "blocked"


def test_materials_done_when_angellist_present(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "acme_al.pdf")
    state = scan_deal("Acme", tmp_path, "Evaluation")
    materials = _stage(state, "materials")
    assert materials.status == "done"
    al = next(a for a in materials.artifacts if a.label == "AngelList memo")
    assert al.exists and al.filename == "acme_al.pdf"
    # Brief becomes actionable (not blocked) but nothing generated yet.
    assert _stage(state, "brief").status == "todo"


def test_deck_and_notes_listed_as_inputs(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist.pdf")
    _stub_pdf(tmp_path / "pitch_deck.pdf")
    (tmp_path / "notes.md").write_text("call notes")
    labels = {
        a.label for a in _stage(scan_deal("Acme", tmp_path, "Evaluation"), "materials").artifacts
    }
    assert "Pitch deck" in labels
    assert any(label.startswith("Note:") for label in labels)


def test_brief_partial_then_done(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "al.pdf")
    (tmp_path / "diligence_topics.html").write_text("<html></html>")
    # Only the primary artifact present -> partial (scorecard still missing).
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "brief").status == "partial"
    (tmp_path / "score_report.md").write_text("# score")
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "brief").status == "done"


def test_decision_done_on_decision_md(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "al.pdf")
    (tmp_path / "decision.md").write_text("---\nverdict: buy\n---\nbody")
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "decision").status == "partial"
    (tmp_path / "decision_review.md").write_text("# review")
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "decision").status == "done"


def test_publish_blocked_without_decision(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "al.pdf")
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "publish").status == "blocked"


def test_publish_partial_with_memo(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "al.pdf")
    (tmp_path / "decision.md").write_text("body")
    (tmp_path / "memo_private.md").write_text("# memo")
    assert _stage(scan_deal("Acme", tmp_path, "Evaluation"), "publish").status == "partial"


def test_stage_actions_are_defined(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "al.pdf")
    state = scan_deal("Acme", tmp_path, "Evaluation")
    assert [a.key for a in _stage(state, "brief").actions] == ["diligence", "score"]
    assert [a.key for a in _stage(state, "decision").actions] == ["decide", "review"]
    decide = next(a for a in _stage(state, "decision").actions if a.key == "decide")
    assert decide.kind == "launch"
    publish = next(a for a in _stage(state, "publish").actions if a.key == "publish")
    assert publish.danger is True


# ---------------------------------------------------------------------------
# Deal resolution & listing
# ---------------------------------------------------------------------------


def _cfg(tmp_path: Path) -> Config:
    ev = tmp_path / "Evaluation"
    pf = tmp_path / "Portfolio"
    ev.mkdir()
    pf.mkdir()
    return Config(evaluation_root=ev, portfolio_root=pf, public_doc_id="x", private_doc_id="y")


def test_resolve_prefers_portfolio(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    (cfg.evaluation_root / "Acme").mkdir()
    (cfg.portfolio_root / "Acme").mkdir()
    resolved = resolve_deal("Acme", cfg)
    assert resolved is not None
    folder, location = resolved
    assert location == "Portfolio"
    assert folder == cfg.portfolio_root / "Acme"


def test_resolve_missing_returns_none(tmp_path: Path) -> None:
    assert resolve_deal("Ghost", _cfg(tmp_path)) is None


def test_list_deals_spans_both_roots(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    (cfg.evaluation_root / "Beta").mkdir()
    (cfg.portfolio_root / "Alpha").mkdir()
    names = {d.company: d.location for d in list_deals(cfg)}
    assert names == {"Beta": "Evaluation", "Alpha": "Portfolio"}


def test_list_deals_reports_furthest_stage(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    folder = cfg.evaluation_root / "Acme"
    folder.mkdir()
    _stub_pdf(folder / "al.pdf")
    (folder / "diligence_topics.html").write_text("x")
    (folder / "score_report.md").write_text("x")
    summary = next(d for d in list_deals(cfg) if d.company == "Acme")
    assert "Quick brief" in summary.furthest_stage


# ---------------------------------------------------------------------------
# Claude Code launch command
# ---------------------------------------------------------------------------


def test_decide_launch_command_mentions_company_and_skill() -> None:
    cmd = decide_launch_command("Acme Corp")
    joined = " ".join(cmd)
    assert "claude" in cmd
    assert "/angel-decide" in joined
    assert "Acme Corp" in joined


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_markdown_headings_and_bold() -> None:
    html = render_markdown("# Title\n\nSome **bold** and `code`.")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html


def test_render_markdown_lists() -> None:
    html = render_markdown("- one\n- two\n\n1. first\n2. second")
    assert html.count("<li>") == 4
    assert "<ul>" in html and "<ol>" in html


def test_render_markdown_escapes_html() -> None:
    html = render_markdown("A <script>alert(1)</script> tag")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_markdown_frontmatter_block() -> None:
    html = render_markdown("---\nverdict: buy\ncheck_usd: 5000\n---\n\n# Body")
    assert "Decision metadata" in html
    assert "verdict: buy" in html
    assert "<h1>Body</h1>" in html


def test_render_markdown_links() -> None:
    html = render_markdown("See [the memo](memo_private.md) now.")
    assert '<a href="memo_private.md">the memo</a>' in html


def test_render_markdown_code_fence_preserved() -> None:
    html = render_markdown("```\nraw **not bold**\n```")
    assert "raw **not bold**" in html
    assert "<strong>" not in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
