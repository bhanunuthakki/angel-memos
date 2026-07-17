"""Desktop launcher for the angel-memos CLI.

A button per subcommand so you don't have to remember the command line. Each
button shows what it does, echoes the exact `uv run angel-memos ...` command it
runs (so you learn the CLI over time), and streams live output below.

Run it with any Python 3.11+ that has tkinter (the stock python.org build does):

    python angel_memos_gui.py          # with a console (see errors)
    pythonw angel_memos_gui.py         # no console window

or double-click `Angel Memos.bat`. The GUI itself needs no project deps — it
only shells out to `uv run angel-memos`, so `uv` must be on your PATH.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import subprocess
import threading
import tkinter as tk
import tomllib
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# This file lives at the repo root; commands run from here.
REPO_ROOT = Path(__file__).resolve().parent
BASE_CMD = ["uv", "run", "angel-memos"]

_DEFAULT_EVAL = Path(r"G:\My Drive\Personal Finances\Angel Investing\Evaluation")
_DEFAULT_PORTFOLIO = Path(r"G:\My Drive\Personal Finances\Angel Investing\Portfolio")


def load_roots() -> tuple[Path, Path]:
    """Mirror config.py: defaults, overridden by ~/.config/angel-memos/config.toml."""
    eval_root, port_root = _DEFAULT_EVAL, _DEFAULT_PORTFOLIO
    cfg_dir = os.environ.get("ANGEL_MEMOS_CONFIG_DIR")
    cfg_path = (
        Path(cfg_dir) if cfg_dir else Path.home() / ".config" / "angel-memos"
    ) / "config.toml"
    if cfg_path.is_file():
        try:
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
            if raw.get("evaluation_root"):
                eval_root = Path(raw["evaluation_root"])
            if raw.get("portfolio_root"):
                port_root = Path(raw["portfolio_root"])
        except Exception:
            pass
    return eval_root, port_root


def list_companies(eval_root: Path, port_root: Path) -> list[str]:
    names: set[str] = set()
    for root in (eval_root, port_root):
        try:
            if root.is_dir():
                names.update(p.name for p in root.iterdir() if p.is_dir())
        except OSError:
            pass
    return sorted(names, key=str.lower)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen[str] | None = None
        self.q: queue.Queue[object] = queue.Queue()
        self.run_buttons: list[ttk.Button] = []
        self.eval_root, self.port_root = load_roots()

        root.title("Angel Memos")
        root.geometry("860x720")
        root.minsize(720, 560)

        self._build_company_row()
        self._build_percompany_section()
        self._build_global_section()
        self._build_console()

        self.root.after(100, self._poll)

    # ---- layout ---------------------------------------------------------
    def _build_company_row(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Company")
        frame.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(frame, text="Deal:").pack(side="left", padx=(8, 4), pady=8)
        self.company_var = tk.StringVar()
        self.company_box = ttk.Combobox(frame, textvariable=self.company_var, width=40)
        self.company_box["values"] = list_companies(self.eval_root, self.port_root)
        self.company_box.pack(side="left", padx=4, pady=8)
        ttk.Button(frame, text="Refresh list", command=self._refresh_companies).pack(
            side="left", padx=4
        )
        ttk.Label(
            frame,
            text="(type a name or pick one; matches an Evaluation/ or Portfolio/ folder)",
            foreground="gray",
        ).pack(side="left", padx=8)

    def _build_percompany_section(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Per-company steps  (need a deal selected above)")
        frame.pack(fill="x", padx=10, pady=4)

        self._cmd_row(
            frame,
            "Diligence",
            ["diligence"],
            needs_company=True,
            desc="Phase A — research topics → diligence_topics.html",
        )
        self._cmd_row(
            frame,
            "Score",
            ["score"],
            needs_company=True,
            desc="Rubric scorecard → score_report.json / .md  (advisory only)",
        )
        self._cmd_row(
            frame,
            "Review",
            ["review"],
            needs_company=True,
            desc="Adversarial pressure-test of decision.md → decision_review.md",
        )

        # Memo row with its flags.
        memo_wrap = ttk.Frame(frame)
        memo_wrap.pack(fill="x", padx=6, pady=(8, 2))
        self.f_no_docs = tk.BooleanVar()
        self.f_no_review = tk.BooleanVar()
        self.f_skip_long = tk.BooleanVar()
        btn = ttk.Button(memo_wrap, text="Memo", width=14, command=self._run_memo)
        btn.grid(row=0, column=0, rowspan=2, padx=(0, 8), sticky="n")
        self.run_buttons.append(btn)
        ttk.Label(
            memo_wrap,
            text="Phase B — private + masked-public memos, exit math, append to Google Docs",
        ).grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Checkbutton(
            memo_wrap, text="--no-docs (skip Google Docs)", variable=self.f_no_docs
        ).grid(row=1, column=1, sticky="w", padx=(0, 10))
        ttk.Checkbutton(memo_wrap, text="--no-review", variable=self.f_no_review).grid(
            row=1, column=2, sticky="w", padx=(0, 10)
        )
        ttk.Checkbutton(
            memo_wrap, text="--skip-long-memo (reuse existing)", variable=self.f_skip_long
        ).grid(row=1, column=3, sticky="w")

        self._cmd_row(
            frame,
            "Publish",
            ["publish"],
            needs_company=True,
            desc="Re-push cached entries to Google Docs (no Claude call)",
            confirm="Append this deal's cached entries to your Google Docs?",
        )

    def _build_global_section(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Inbox & cross-deal  (no deal needed)")
        frame.pack(fill="x", padx=10, pady=4)
        self._cmd_row(
            frame,
            "Ingest",
            ["ingest"],
            needs_company=False,
            desc="Move ready extension drops from Downloads → Evaluation/",
        )
        self._cmd_row(
            frame,
            "Watch",
            ["watch"],
            needs_company=False,
            desc="Daemon: ingest drops + auto-run quick tier. Use Stop to end.",
        )
        self._cmd_row(
            frame,
            "Investors: backfill",
            ["investors", "backfill"],
            needs_company=False,
            desc="Seed the investor DB from existing deals, then export investors.md",
        )
        self._cmd_row(
            frame,
            "Investors: export",
            ["investors", "export"],
            needs_company=False,
            desc="Rewrite investors.md next to the Drive roots",
        )

    def _cmd_row(
        self,
        parent: tk.Widget,
        label: str,
        args: list[str],
        *,
        needs_company: bool,
        desc: str,
        confirm: str | None = None,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=2)
        btn = ttk.Button(
            row,
            text=label,
            width=14,
            command=lambda: self._run(args, needs_company=needs_company, confirm=confirm),
        )
        btn.pack(side="left", padx=(0, 8))
        self.run_buttons.append(btn)
        ttk.Label(row, text=desc, foreground="#444").pack(side="left")

    def _build_console(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Output")
        frame.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=6, pady=4)
        self.status = ttk.Label(bar, text="Idle.", foreground="#333")
        self.status.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right")
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="right", padx=6)
        self.console = scrolledtext.ScrolledText(frame, height=16, wrap="word", state="disabled")
        self.console.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    # ---- actions --------------------------------------------------------
    def _refresh_companies(self) -> None:
        self.eval_root, self.port_root = load_roots()
        self.company_box["values"] = list_companies(self.eval_root, self.port_root)

    def _run_memo(self) -> None:
        args = ["memo"]
        if self.f_no_docs.get():
            args.append("--no-docs")
        if self.f_no_review.get():
            args.append("--no-review")
        if self.f_skip_long.get():
            args.append("--skip-long-memo")
        confirm = None
        if not self.f_no_docs.get():
            confirm = (
                "Memo will generate the memos AND append entries to your two Google "
                "Docs (private ledger + public memos). Continue?"
            )
        self._run(args, needs_company=True, confirm=confirm)

    def _run(self, args: list[str], *, needs_company: bool, confirm: str | None) -> None:
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showwarning("Busy", "A command is already running. Stop it first.")
            return
        full = list(args)
        if needs_company:
            company = self.company_var.get().strip()
            if not company:
                messagebox.showerror("No deal", "Enter or pick a company first.")
                return
            full.append(company)
        if confirm and not messagebox.askyesno("Confirm", confirm):
            return

        cmd = BASE_CMD + full
        self._append(f"\n$ {' '.join(cmd)}\n")
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except FileNotFoundError:
            self._append("ERROR: 'uv' not found on PATH. Install uv or add it to PATH.\n")
            self.proc = None
            return
        self._set_running(True, " ".join(full))
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.q.put(line)
        proc.stdout.close()
        self.q.put(("__DONE__", proc.wait()))

    def _stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        pid = self.proc.pid
        # Graceful first (lets `watch` finish its current move via KeyboardInterrupt).
        with contextlib.suppress(Exception):
            self.proc.send_signal(signal.CTRL_BREAK_EVENT)
        self.root.after(3000, lambda: self._force_kill(pid))

    def _force_kill(self, pid: int) -> None:
        if self.proc is not None and self.proc.pid == pid and self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
            )

    # ---- plumbing -------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__DONE__":
                    self._on_done(int(item[1]))
                else:
                    self._append(str(item))
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_done(self, rc: int) -> None:
        self._append(f"\n[exit code {rc}]\n")
        self.proc = None
        self._set_running(False, "")
        self.status.config(text=f"Done (exit {rc})." if rc == 0 else f"Failed (exit {rc}).")

    def _set_running(self, running: bool, label: str) -> None:
        for b in self.run_buttons:
            b.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        if running:
            self.status.config(text=f"Running: angel-memos {label} …")

    def _append(self, text: str) -> None:
        self.console.config(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.config(state="disabled")

    def _clear(self) -> None:
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        self.console.config(state="disabled")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
