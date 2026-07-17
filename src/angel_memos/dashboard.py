"""Local single-deal orchestration dashboard.

A browser front-end over one company folder: it reads which artifacts exist,
lays the deal out as a pipeline (save info -> quick brief -> diligence & decision
-> publish), lets you click into every artifact (AL memo PDF, deck PDF, the
diligence brief, the scorecard, the decision), and runs each pipeline step with
one button. The Q&A step redirects you into a Claude Code session running
`/angel-decide` for the deal, since that conversation lives in Claude, not here.

Design:
  - The stage/artifact detection and Markdown rendering are pure functions
    (`scan_deal`, `render_markdown`) with no Claude / network / server
    dependency — they carry the tests.
  - The HTTP layer is a thin stdlib `http.server` on top. No web framework
    dependency; this is a single-user localhost tool.
  - Pipeline actions run IN-PROCESS in a background thread, one at a time
    (mirrors the desktop launcher's single-job model). Heavy phase functions
    are imported lazily so the pure logic stays cheap to import under test.

Launch with `angel-memos dashboard` (opens the browser to the deal picker).
"""

from __future__ import annotations

import html as _html
import io
import os
import re
import subprocess
import threading
import traceback
from contextlib import redirect_stdout
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict

from angel_memos.config import Config, load_config
from angel_memos.materials import MaterialsError, load_materials

# ---------------------------------------------------------------------------
# Deal model — the pipeline, its stages, and their on-disk artifacts.
# ---------------------------------------------------------------------------

type StageKey = Literal["materials", "brief", "decision", "publish"]
type StageStatus = Literal["done", "partial", "todo", "blocked"]
type ArtifactKind = Literal["pdf", "html", "md", "xlsx", "json", "text"]
type Location = Literal["Evaluation", "Portfolio"]


class Artifact(BaseModel):
    """One file the dashboard can link into (input material or generated output)."""

    model_config = ConfigDict(frozen=True)

    label: str
    filename: str  # actual on-disk basename (empty if not yet present)
    kind: ArtifactKind
    exists: bool
    is_input: bool = False


class Action(BaseModel):
    """One runnable pipeline step. `cli` runs a phase in-process; `launch`
    shells out to open a Claude Code session (the Q&A redirect)."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    kind: Literal["cli", "launch"]
    desc: str = ""
    danger: bool = False  # side-effecting (publishes to external docs)


class StageState(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: StageKey
    label: str
    status: StageStatus
    blurb: str
    artifacts: list[Artifact]
    actions: list[Action]


class DealState(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    folder: Path
    location: Location
    stages: list[StageState]


class DealSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    location: Location
    furthest_stage: str  # human label of the furthest completed stage


# Generated-output artifacts have fixed filenames; input materials are resolved
# from the folder via the materials classifier (their names vary).
def _kind_for(filename: str) -> ArtifactKind:
    suffix = Path(filename).suffix.lower()
    return {
        ".pdf": "pdf",
        ".html": "html",
        ".md": "md",
        ".xlsx": "xlsx",
        ".json": "json",
    }.get(suffix, "text")  # type: ignore[return-value]


def _artifact(label: str, filename: str, folder: Path, *, is_input: bool = False) -> Artifact:
    return Artifact(
        label=label,
        filename=filename,
        kind=_kind_for(filename),
        exists=(folder / filename).is_file(),
        is_input=is_input,
    )


def scan_deal(company: str, folder: Path, location: Location) -> DealState:
    """Read `folder` and describe the deal as a four-stage pipeline. Pure:
    only touches the filesystem for existence checks, never Claude/network."""
    materials_arts, has_al = _scan_materials(folder)
    has_decision = (folder / "decision.md").is_file()

    brief_arts = [
        _artifact("Diligence brief", "diligence_topics.html", folder),
        _artifact("Scorecard", "score_report.md", folder),
    ]
    decision_arts = [
        _artifact("Decision", "decision.md", folder),
        _artifact("Decision review", "decision_review.md", folder),
    ]
    publish_arts = [
        _artifact("Private memo", "memo_private.md", folder),
        _artifact("Public memo", "memo_public.md", folder),
        _artifact("Exit math", "exit_math.xlsx", folder),
    ]

    stages = [
        StageState(
            key="materials",
            label="1 · Save info",
            status="done" if has_al else "blocked",
            blurb="Captured from the extension and ingested into the deal folder.",
            artifacts=materials_arts,
            actions=[],
        ),
        StageState(
            key="brief",
            label="2 · Quick brief",
            status=_all_or_partial(brief_arts, blocked=not has_al),
            blurb="Adversarial bull/bear diligence topics and the rubric scorecard.",
            artifacts=brief_arts,
            actions=[
                Action(
                    key="diligence",
                    label="Run diligence",
                    kind="cli",
                    desc="Phase A → diligence_topics.html",
                ),
                Action(
                    key="score",
                    label="Run score",
                    kind="cli",
                    desc="Rubric scorecard → score_report.md (advisory)",
                ),
            ],
        ),
        StageState(
            key="decision",
            label="3 · Diligence & decision",
            status=_all_or_partial(decision_arts, blocked=not has_al),
            blurb="Ask questions of Claude, then capture the decision. The Q&A runs in Claude Code.",
            artifacts=decision_arts,
            actions=[
                Action(
                    key="decide",
                    label="Open Q&A in Claude Code",
                    kind="launch",
                    desc="Runs /angel-decide for this deal in a Claude Code session",
                ),
                Action(
                    key="review",
                    label="Run review",
                    kind="cli",
                    desc="Adversarial pressure-test of decision.md",
                ),
            ],
        ),
        StageState(
            key="publish",
            # Memos + exit math vary by verdict (pass omits exit math; only buys
            # get a public memo) and the Google-Docs append leaves no local
            # marker — so publish tops out at "partial" once memos exist.
            label="4 · Memo & publish",
            status="blocked"
            if not has_decision
            else ("partial" if (folder / "memo_private.md").is_file() else "todo"),
            blurb="Generate the private + masked-public memos and exit math, then publish to Google Docs.",
            artifacts=publish_arts,
            actions=[
                Action(
                    key="memo",
                    label="Generate memo",
                    kind="cli",
                    desc="Memos + exit math (does NOT publish to docs)",
                ),
                Action(
                    key="publish",
                    label="Publish to Google Docs",
                    kind="cli",
                    desc="Append cached entries to your Google Docs",
                    danger=True,
                ),
            ],
        ),
    ]
    return DealState(company=company, folder=folder, location=location, stages=stages)


def _scan_materials(folder: Path) -> tuple[list[Artifact], bool]:
    """Resolve input materials via the shared classifier. Tolerant of a folder
    with no AL memo. Returns (artifacts, has_angellist)."""
    arts: list[Artifact] = []
    try:
        materials = load_materials(folder)
    except MaterialsError:
        # No AL memo yet — show the slot as missing so the UI reads "blocked".
        arts.append(
            Artifact(label="AngelList memo", filename="", kind="pdf", exists=False, is_input=True)
        )
        return arts, False
    arts.append(_artifact("AngelList memo", materials.angellist.path.name, folder, is_input=True))
    if materials.deck is not None:
        arts.append(_artifact("Pitch deck", materials.deck.path.name, folder, is_input=True))
    for note in materials.notes:
        arts.append(_artifact(f"Note: {note.path.name}", note.path.name, folder, is_input=True))
    return arts, True


def _all_or_partial(artifacts: list[Artifact], *, blocked: bool) -> StageStatus:
    """done = every artifact present; partial = some; todo = none."""
    if blocked:
        return "blocked"
    present = sum(1 for a in artifacts if a.exists)
    if present == len(artifacts):
        return "done"
    return "partial" if present else "todo"


# ---------------------------------------------------------------------------
# Deal resolution (mirrors cli._resolve_company_folder, without auto-migration).
# ---------------------------------------------------------------------------


def resolve_deal(company: str, cfg: Config) -> tuple[Path, Location] | None:
    """Portfolio first (committed), then Evaluation. None if neither exists."""
    portfolio = cfg.portfolio_root / company
    if portfolio.is_dir():
        return portfolio, "Portfolio"
    evaluation = cfg.evaluation_root / company
    if evaluation.is_dir():
        return evaluation, "Evaluation"
    return None


def list_deals(cfg: Config) -> list[DealSummary]:
    """Every deal folder under the two roots, Evaluation then Portfolio."""
    summaries: list[DealSummary] = []
    for root, location in ((cfg.evaluation_root, "Evaluation"), (cfg.portfolio_root, "Portfolio")):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            state = scan_deal(folder.name, folder, location)  # type: ignore[arg-type]
            done = [s.label for s in state.stages if s.status == "done"]
            summaries.append(
                DealSummary(
                    company=folder.name,
                    location=location,  # type: ignore[arg-type]
                    furthest_stage=done[-1] if done else "—",
                )
            )
    return summaries


# ---------------------------------------------------------------------------
# Claude Code redirect for the Q&A step.
# ---------------------------------------------------------------------------


def decide_launch_command(company: str) -> list[str]:
    """Command that opens a Claude Code session seeded to run /angel-decide.

    On Windows this opens a new console window (`start`) so the chat is
    interactive and visible. The seed prompt triggers the angel-decide skill
    for the deal; the user drives the Q&A from there."""
    prompt = f'Run the /angel-decide skill for the deal "{company}".'
    if os.name == "nt":
        # `start "" cmd /k claude "<prompt>"` — empty title arg, keep window open.
        return ["cmd", "/c", "start", "", "cmd", "/k", "claude", prompt]
    return ["claude", prompt]


# ---------------------------------------------------------------------------
# Markdown → HTML (minimal, dependency-free). Handles the subset our generated
# docs use: YAML frontmatter, headings, lists, bold/italic/code, links, rules.
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"(#{1,6})\s+(.*)")
_HR = re.compile(r"(?:\*\s*){3,}$|(?:-\s*){3,}$|(?:_\s*){3,}$")
_UL = re.compile(r"[-*+]\s+(.*)")
_OL = re.compile(r"\d+[.)]\s+(.*)")


def _inline(text: str) -> str:
    t = _html.escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)


def render_markdown(text: str) -> str:
    """Render a Markdown document body to an HTML fragment. Best-effort but
    stable: unknown constructs degrade to paragraphs, everything is escaped."""
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    in_ul = in_ol = in_code = False

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    # YAML frontmatter → rendered as a labeled code block up top.
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            fm = "\n".join(lines[1:end])
            out.append(
                '<div class="frontmatter"><span class="fm-label">Decision metadata</span>'
                f"<pre>{_html.escape(fm)}</pre></div>"
            )
            lines = lines[end + 1 :]

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush_para()
            close_lists()
            out.append("<pre><code>" if not in_code else "</code></pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(_html.escape(raw))
            continue
        if not stripped:
            flush_para()
            close_lists()
            continue
        if m := _HEADING.match(stripped):
            flush_para()
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if _HR.match(stripped):
            flush_para()
            close_lists()
            out.append("<hr>")
            continue
        if m := _UL.match(stripped):
            flush_para()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        if m := _OL.match(stripped):
            flush_para()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        para.append(stripped)

    flush_para()
    close_lists()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Job runner — one pipeline action at a time, in a background thread.
# ---------------------------------------------------------------------------


class Job:
    """A single in-flight (or finished) pipeline action, with captured log."""

    def __init__(self, company: str, action: str) -> None:
        self.company = company
        self.action = action
        self.started = datetime.now()
        self.finished: datetime | None = None
        self.status: Literal["running", "ok", "error"] = "running"
        self.log: list[str] = []
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        with self._lock:
            self.log.append(text)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "company": self.company,
                "action": self.action,
                "status": self.status,
                "log": "".join(self.log),
                "started": self.started.strftime("%H:%M:%S"),
                "finished": self.finished.strftime("%H:%M:%S") if self.finished else None,
            }


class JobRunner:
    """Serializes pipeline actions: at most one runs at a time (heavy Claude
    work + a process-global stdout redirect make concurrency unsafe here)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._current: Job | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> Job | None:
        return self._current

    def busy(self) -> bool:
        return self._current is not None and self._current.status == "running"

    def start(self, company: str, folder: Path, action: str) -> tuple[bool, str]:
        """Kick off `action` for the deal. Returns (started, message)."""
        with self._lock:
            if self.busy():
                return False, f"Busy running '{self._current.action}' — wait for it to finish."  # type: ignore[union-attr]
            job = Job(company, action)
            self._current = job
        threading.Thread(target=self._run, args=(job, folder), daemon=True).start()
        return True, f"Started {action} for {company}."

    def _run(self, job: Job, folder: Path) -> None:
        buffer = _LineSink(job)
        try:
            with redirect_stdout(buffer):
                _dispatch(job.action, folder, self.cfg)
            job.append("\n[done]\n")
            job.status = "ok"
        except Exception:
            job.append("\n[ERROR]\n" + traceback.format_exc())
            job.status = "error"
        finally:
            job.finished = datetime.now()


class _LineSink(io.TextIOBase):
    """Funnels captured stdout into a Job's log."""

    def __init__(self, job: Job) -> None:
        super().__init__()
        self._job = job

    def write(self, s: str) -> int:  # type: ignore[override]
        self._job.append(s)
        return len(s)


def _dispatch(action: str, folder: Path, cfg: Config) -> None:
    """Run one pipeline action in-process. Phase functions imported lazily so
    the pure logic above stays cheap to import (no Claude deps) under test."""
    if action == "diligence":
        from angel_memos.diligence import run_diligence_phase

        print(f"Running diligence on {folder} …")
        out = run_diligence_phase(folder)
        print(f"Wrote {out}")
    elif action == "score":
        from angel_memos.scoring import run_score_phase

        print(f"Scoring {folder} …")
        outputs = run_score_phase(folder)
        for label, path in outputs.items():
            print(f"  {label}: {path}")
    elif action == "review":
        from angel_memos.review import run_decision_review

        print(f"Reviewing {folder} …")
        out = run_decision_review(folder)
        print(f"Wrote {out}")
    elif action == "memo":
        from angel_memos.memo import run_memo_phase

        print(f"Generating memo for {folder} (no docs) …")
        outputs = run_memo_phase(folder, config=cfg, append_to_docs=False, run_review=False)
        for label, path in outputs.items():
            print(f"  {label}: {path}")
    elif action == "publish":
        from angel_memos.memo import parse_decision, publish_decision_to_docs

        decision = parse_decision(folder / "decision.md")
        print(f"Publishing {decision.company} to Google Docs …")
        publish_decision_to_docs(folder, cfg)
        print("Published.")
    else:
        raise ValueError(f"unknown action: {action}")


# ---------------------------------------------------------------------------
# HTTP layer — thin stdlib server.
# ---------------------------------------------------------------------------

_INLINE_KINDS: frozenset[ArtifactKind] = frozenset({"pdf", "html"})
_RENDER_KINDS: frozenset[ArtifactKind] = frozenset({"md", "text", "json"})
_CONTENT_TYPES: dict[ArtifactKind, str] = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def make_server(cfg: Config, port: int) -> ThreadingHTTPServer:
    runner = JobRunner(cfg)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AngelMemosDashboard/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silence default stderr request logging

        # -- helpers ------------------------------------------------------
        def _send_html(self, body: str, code: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, body: str, code: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # -- routing ------------------------------------------------------
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send_html(_render_index(cfg))
            elif path.startswith("/deal/"):
                self._route_deal(unquote(path[len("/deal/") :]))
            elif path.startswith("/file/"):
                self._route_file(path[len("/file/") :])
            elif path == "/api/job":
                job = runner.current
                self._send_json(_json(job.snapshot() if job else {"status": "idle"}))
            else:
                self._send_html("<h1>404</h1>", 404)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            m = re.match(r"/api/deal/([^/]+)/action/([a-z]+)$", path)
            if not m:
                self._send_json(_json({"ok": False, "error": "bad route"}), 404)
                return
            company = unquote(m.group(1))
            action = m.group(2)
            resolved = resolve_deal(company, cfg)
            if resolved is None:
                self._send_json(_json({"ok": False, "error": "deal not found"}), 404)
                return
            folder, _ = resolved
            if action == "decide":
                try:
                    subprocess.Popen(decide_launch_command(company), cwd=str(folder))
                    self._send_json(
                        _json({"ok": True, "launched": True, "message": "Opening Claude Code…"})
                    )
                except OSError as exc:
                    self._send_json(_json({"ok": False, "error": str(exc)}), 500)
                return
            ok, message = runner.start(company, folder, action)
            self._send_json(_json({"ok": ok, "message": message}), 200 if ok else 409)

        # -- handlers -----------------------------------------------------
        def _route_deal(self, company: str) -> None:
            resolved = resolve_deal(company, cfg)
            if resolved is None:
                self._send_html(f"<h1>Deal not found: {_html.escape(company)}</h1>", 404)
                return
            folder, location = resolved
            state = scan_deal(company, folder, location)
            self._send_html(_render_deal(state, runner))

        def _route_file(self, rest: str) -> None:
            company, _, filename = rest.partition("/")
            company = unquote(company)
            filename = unquote(filename)
            resolved = resolve_deal(company, cfg)
            if resolved is None:
                self._send_html("<h1>404</h1>", 404)
                return
            folder, _ = resolved
            target = folder / filename
            # Guard against traversal: only serve top-level files in the folder.
            if Path(filename).name != filename or not target.is_file():
                self._send_html("<h1>404</h1>", 404)
                return
            kind = _kind_for(filename)
            if kind in _RENDER_KINDS:
                raw = target.read_text(encoding="utf-8", errors="replace")
                body = render_markdown(raw) if kind == "md" else f"<pre>{_html.escape(raw)}</pre>"
                self._send_html(_render_doc(company, filename, body))
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _CONTENT_TYPES.get(kind, "application/octet-stream"))
            if kind == "xlsx":
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(cfg: Config | None = None, port: int = 8765, *, open_browser: bool = True) -> None:
    """Start the dashboard on localhost and (optionally) open the browser."""
    cfg = cfg or load_config()
    httpd = make_server(cfg, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Angel Memos dashboard → {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser

        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# HTML rendering.
# ---------------------------------------------------------------------------


def _json(obj: dict[str, object]) -> str:
    import json

    return json.dumps(obj)


_STATUS_LABEL: dict[StageStatus, str] = {
    "done": "Done",
    "partial": "Partial",
    "todo": "To do",
    "blocked": "Blocked",
}


def _page(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_html.escape(title)}</title>{_CSS}</head><body>{body}</body></html>"
    )


def _render_index(cfg: Config) -> str:
    deals = list_deals(cfg)
    rows: list[str] = []
    for d in deals:
        rows.append(
            f'<a class="deal-card" href="/deal/{_html.escape(d.company)}">'
            f'<span class="deal-name">{_html.escape(d.company)}</span>'
            f'<span class="deal-loc {d.location.lower()}">{d.location}</span>'
            f'<span class="deal-stage">{_html.escape(d.furthest_stage)}</span></a>'
        )
    listing = "\n".join(rows) or '<p class="muted">No deals found under the configured roots.</p>'
    body = (
        '<header><h1>Angel Memos</h1><p class="sub">Deal orchestration dashboard</p></header>'
        f'<main><h2>Deals</h2><div class="deal-list">{listing}</div></main>'
    )
    return _page("Angel Memos — Deals", body)


def _render_deal(state: DealState, runner: JobRunner) -> str:
    strip = "".join(
        f'<div class="pip {s.status}"><span class="pip-dot"></span>'
        f'<span class="pip-label">{_html.escape(s.label)}</span>'
        f'<span class="pip-status">{_STATUS_LABEL[s.status]}</span></div>'
        for s in state.stages
    )
    stages_html = "".join(_render_stage(state.company, s) for s in state.stages)
    body = (
        f'<header><a class="back" href="/">← all deals</a>'
        f"<h1>{_html.escape(state.company)}</h1>"
        f'<p class="sub">{state.location} · {_html.escape(str(state.folder))}</p></header>'
        f'<div class="pipeline">{strip}</div>'
        f"<main>{stages_html}</main>"
        f'<aside id="runlog" class="runlog hidden"><div class="runlog-head">'
        f'<span id="runlog-title">Run log</span><button onclick="hideLog()">&times;</button></div>'
        f'<pre id="runlog-body"></pre></aside>'
        f"{_JS}"
    )
    return _page(f"{state.company} — Angel Memos", body)


def _render_stage(company: str, stage: StageState) -> str:
    arts = (
        "".join(_render_artifact(company, a) for a in stage.artifacts)
        or '<span class="muted">—</span>'
    )
    acts = "".join(
        f'<button class="act {"danger" if a.danger else ""} {a.kind}" '
        f"onclick=\"runAction('{_html.escape(company)}','{a.key}','{a.kind}')\" "
        f'title="{_html.escape(a.desc)}">{_html.escape(a.label)}</button>'
        for a in stage.actions
    )
    return (
        f'<section class="stage {stage.status}"><div class="stage-head">'
        f"<h2>{_html.escape(stage.label)}</h2>"
        f'<span class="badge {stage.status}">{_STATUS_LABEL[stage.status]}</span></div>'
        f'<p class="blurb">{_html.escape(stage.blurb)}</p>'
        f'<div class="artifacts">{arts}</div>'
        f'<div class="actions">{acts}</div></section>'
    )


def _render_artifact(company: str, a: Artifact) -> str:
    icon = {"pdf": "📄", "html": "📊", "md": "📝", "xlsx": "📈", "json": "{}", "text": "📄"}[a.kind]
    if not a.exists:
        return (
            f'<span class="artifact missing" title="not generated yet">'
            f"{icon} {_html.escape(a.label)}</span>"
        )
    href = f"/file/{_html.escape(company)}/{_html.escape(a.filename)}"
    target = ' target="_blank"' if a.kind in _INLINE_KINDS or a.kind == "md" else ""
    return f'<a class="artifact" href="{href}"{target}>{icon} {_html.escape(a.label)}</a>'


def _render_doc(company: str, filename: str, body: str) -> str:
    inner = (
        f'<header><a class="back" href="/deal/{_html.escape(company)}">← {_html.escape(company)}</a>'
        f"<h1>{_html.escape(filename)}</h1></header>"
        f'<main><article class="doc">{body}</article></main>'
    )
    return _page(f"{filename} — {company}", inner)


_CSS = """<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;
--done:#047857;--partial:#b45309;--todo:#6b7280;--blocked:#9ca3af;--accent:#0369a1;--danger:#b91c1c;}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--ink);margin:0;line-height:1.4;font-size:14px}
header{padding:1.2rem 1.4rem .6rem}
h1{font-size:1.5rem;margin:.2rem 0}
.sub{color:var(--muted);margin:.1rem 0 0;font-size:.85rem}
.back{color:var(--accent);text-decoration:none;font-size:.85rem}
main{padding:0 1.4rem 3rem;max-width:960px}
h2{font-size:1rem;margin:0}
.muted{color:var(--muted)}
.deal-list{display:flex;flex-direction:column;gap:.4rem;margin-top:.6rem}
.deal-card{display:flex;align-items:center;gap:.8rem;background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:.7rem 1rem;text-decoration:none;color:var(--ink)}
.deal-card:hover{border-color:var(--accent)}
.deal-name{font-weight:600;flex:1}
.deal-loc{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;padding:.1rem .5rem;
border-radius:999px;background:#eef2ff;color:#3730a3}
.deal-loc.portfolio{background:#ecfdf5;color:#065f46}
.deal-stage{color:var(--muted);font-size:.8rem}
.pipeline{display:flex;gap:.5rem;padding:.4rem 1.4rem 1rem;flex-wrap:wrap}
.pip{display:flex;align-items:center;gap:.4rem;background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:.35rem .8rem;font-size:.8rem}
.pip-dot{width:9px;height:9px;border-radius:50%;background:var(--todo)}
.pip.done .pip-dot{background:var(--done)}.pip.partial .pip-dot{background:var(--partial)}
.pip.blocked .pip-dot{background:var(--blocked)}
.pip-status{color:var(--muted)}
.stage{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:8px;padding:.9rem 1.1rem;margin-bottom:.8rem}
.stage.done{border-left-color:var(--done)}.stage.partial{border-left-color:var(--partial)}
.stage.blocked{border-left-color:var(--blocked);opacity:.75}
.stage-head{display:flex;align-items:center;gap:.6rem}
.badge{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;padding:.12rem .5rem;
border-radius:999px;background:#f3f4f6;color:var(--muted)}
.badge.done{background:#ecfdf5;color:var(--done)}.badge.partial{background:#fffbeb;color:var(--partial)}
.badge.blocked{background:#f3f4f6;color:var(--blocked)}
.blurb{color:var(--muted);margin:.35rem 0 .7rem;font-size:.85rem}
.artifacts{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.7rem}
.artifact{display:inline-flex;align-items:center;gap:.35rem;background:#f8fafc;border:1px solid var(--line);
border-radius:6px;padding:.3rem .6rem;text-decoration:none;color:var(--ink);font-size:.82rem}
.artifact:hover{border-color:var(--accent);color:var(--accent)}
.artifact.missing{color:var(--blocked);border-style:dashed;background:transparent}
.actions{display:flex;flex-wrap:wrap;gap:.5rem}
.act{border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:6px;
padding:.4rem .8rem;font-size:.82rem;cursor:pointer}
.act.launch{background:#fff;color:var(--accent)}
.act.danger{background:#fff;color:var(--danger);border-color:var(--danger)}
.act:hover{filter:brightness(.95)}
.runlog{position:fixed;right:1rem;bottom:1rem;width:min(560px,92vw);max-height:60vh;
background:#0b1020;color:#d1d5db;border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,.35);
display:flex;flex-direction:column;overflow:hidden}
.runlog.hidden{display:none}
.runlog-head{display:flex;justify-content:space-between;align-items:center;padding:.5rem .8rem;
background:#111827;font-size:.8rem}
.runlog-head button{background:none;border:none;color:#9ca3af;font-size:1.1rem;cursor:pointer}
.runlog pre{margin:0;padding:.6rem .8rem;overflow:auto;font-size:.76rem;white-space:pre-wrap}
.doc{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1.2rem 1.6rem;
margin-top:.6rem}
.doc h1,.doc h2,.doc h3{margin:1.1rem 0 .5rem}.doc h1{font-size:1.3rem}.doc h2{font-size:1.05rem}
.doc pre{background:#f6f8fa;padding:.7rem;border-radius:6px;overflow:auto;font-size:.8rem}
.doc code{background:#f0f1f3;padding:.05rem .3rem;border-radius:4px}
.doc pre code{background:none;padding:0}
.frontmatter{background:#f8fafc;border:1px solid var(--line);border-radius:6px;padding:.6rem .8rem;margin-bottom:1rem}
.fm-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
</style>"""

_JS = """<script>
let poll=null;
function showLog(){document.getElementById('runlog').classList.remove('hidden');}
function hideLog(){document.getElementById('runlog').classList.add('hidden');if(poll){clearInterval(poll);poll=null;}}
async function runAction(company,action,kind){
  const r=await fetch(`/api/deal/${encodeURIComponent(company)}/action/${action}`,{method:'POST'});
  const j=await r.json();
  if(kind==='launch'){alert(j.message||j.error||'Launched.');return;}
  if(!j.ok){alert(j.message||j.error||'Could not start.');return;}
  document.getElementById('runlog-title').textContent=`Running: ${action}`;
  document.getElementById('runlog-body').textContent='';
  showLog();
  if(poll)clearInterval(poll);
  poll=setInterval(pollJob,1200);
}
async function pollJob(){
  const r=await fetch('/api/job');const j=await r.json();
  if(j.status==='idle')return;
  document.getElementById('runlog-title').textContent=
    (j.status==='running'?'Running: ':(j.status==='ok'?'Done: ':'Failed: '))+j.action;
  const b=document.getElementById('runlog-body');b.textContent=j.log||'';b.scrollTop=b.scrollHeight;
  if(j.status!=='running'){clearInterval(poll);poll=null;setTimeout(()=>location.reload(),1500);}
}
</script>"""
