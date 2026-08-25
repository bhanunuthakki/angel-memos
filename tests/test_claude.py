"""Governed application-LLM routing and ledger behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from angel_memos import claude


class _StructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)


def _no_sleep(_seconds: float) -> None:
    return None


def test_call_llm_uses_openrouter_then_codex_after_claude_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fail_claude(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("claude")
        raise RuntimeError("subscription unavailable")

    def fail_openrouter(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("openrouter")
        raise RuntimeError("metered fallback unavailable")

    def succeed_codex(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("codex")
        return claude.TransportResult(
            text="fallback answer",
            input_tokens=11,
            output_tokens=3,
        )

    monkeypatch.setattr(claude, "_call_claude_transport", fail_claude)
    monkeypatch.setattr(claude, "_call_openrouter_transport", fail_openrouter)
    monkeypatch.setattr(claude, "_call_codex_transport", succeed_codex)
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = claude.call_llm("private prompt", purpose=claude.Purpose.DILIGENCE_TOPICS)

    assert result == "fallback answer"
    assert calls == ["claude", "openrouter", "codex"]


def test_call_llm_ledger_hashes_private_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_prompt = "private company details"
    private_response = "private investment analysis"

    def succeed(*_args: object, **_kwargs: object) -> claude.TransportResult:
        return claude.TransportResult(
            text=private_response,
            input_tokens=9,
            output_tokens=4,
        )

    monkeypatch.setattr(claude, "_call_claude_transport", succeed)
    ledger_path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(ledger_path))

    claude.call_llm(private_prompt, purpose=claude.Purpose.DILIGENCE_TOPICS)

    line = ledger_path.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert private_prompt not in line
    assert private_response not in line
    assert len(entry["prompt_sha256"]) == 64
    assert len(entry["response_sha256"]) == 64


def test_extract_structured_repairs_once_before_advancing_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    openrouter_replies = iter(['{"score": 150}', '{"score": 82}'])

    def fail_claude(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("claude")
        raise RuntimeError("subscription unavailable")

    def openrouter(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("openrouter")
        text = next(openrouter_replies)
        return claude.TransportResult(text=text, input_tokens=10, output_tokens=3)

    def codex(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("codex")
        return claude.TransportResult(text='{"score": 1}', input_tokens=10, output_tokens=3)

    monkeypatch.setattr(claude, "_call_claude_structured_transport", fail_claude)
    monkeypatch.setattr(claude, "_call_openrouter_transport", openrouter)
    monkeypatch.setattr(claude, "_call_codex_transport", codex)
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = claude.extract_structured(
        "score this deal",
        _StructuredAnswer,
        purpose=claude.Purpose.DILIGENCE_TOPICS,
    )

    assert result.score == 82
    assert calls == ["claude", "openrouter", "openrouter"]


def test_application_call_sites_use_only_the_governed_entry_point() -> None:
    package = Path(__file__).parents[1] / "src" / "angel_memos"
    exempt = {"claude.py", "claude_cli.py"}
    violations = [
        path.name
        for path in package.glob("*.py")
        if path.name not in exempt
        and (
            "angel_memos.claude_cli" in path.read_text(encoding="utf-8")
            or "call_claude(" in path.read_text(encoding="utf-8")
        )
    ]

    assert violations == []


def test_v2_scoring_purposes_have_governed_routes() -> None:
    expected = {
        claude.Purpose.SCORE_ARCHETYPE,
        claude.Purpose.SCORE_TEAM_FIT,
        claude.Purpose.SCORE_COMMERCIAL_EVIDENCE,
        claude.Purpose.SCORE_DEFENSIBILITY,
        claude.Purpose.SCORE_EXECUTION_CAPITAL,
    }

    assert expected <= set(claude.MODEL_ROUTES)


def test_codex_image_args_preserve_stdin_prompt_and_attach_every_image() -> None:
    base = ["codex", "exec", "--json", "-"]
    images = [Path("page-1.png"), Path("page-2.png")]

    args = claude._with_codex_images(base, images)

    assert args[-1] == "-"
    assert args.count("--image") == 2
    assert "page-1.png" in args
    assert "page-2.png" in args


def test_codex_paths_follow_agent_instructions_root_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent-instructions"
    wrapper = root / "snippets" / "codex_cli.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("def call_codex_with_usage(): pass\n", encoding="utf-8")
    executable = root / ".tools" / "node_modules" / ".bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv(claude.AGENT_INSTRUCTIONS_ROOT_ENV_VAR, str(root))

    assert claude._agent_instructions_root() == root
    assert claude._codex_cli_path(root) == executable
    assert claude._codex_membership_home(root) == root / ".codex-membership"


def test_codex_membership_accepts_success_marker_after_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli_path = tmp_path / "codex"
    cli_path.write_text("", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> claude.subprocess.CompletedProcess[str]:
        return claude.subprocess.CompletedProcess(
            args=[str(cli_path), "login", "status"],
            returncode=0,
            stdout="",
            stderr="WARNING: benign setup notice\nLogged in using ChatGPT\n",
        )

    monkeypatch.setattr(
        claude.subprocess,
        "run",
        fake_run,
    )

    claude._verify_codex_membership(cli_path, {})


def test_codex_web_required_call_uses_isolated_live_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[Path], bool]] = []

    def direct(
        _prompt: str,
        *,
        model: str,
        timeout_seconds: int,
        image_paths: list[Path],
        enable_web: bool,
    ) -> claude._CodexResultValue:
        del model, timeout_seconds
        calls.append((image_paths, enable_web))
        return claude._CodexResultValue(
            text="sourced answer",
            usage=claude._CodexUsageValue(
                input_tokens=12,
                cached_input_tokens=0,
                output_tokens=4,
                reasoning_output_tokens=1,
            ),
        )

    monkeypatch.setattr(claude, "_call_codex_direct", direct)

    result = claude._call_codex_transport(
        "research the founder",
        model="gpt-5.6-terra",
        timeout_seconds=60,
        image_paths=[],
        requires_web=True,
    )

    assert result.text == "sourced answer"
    assert calls == [([], True)]


def test_codex_direct_args_enable_only_web_search_not_shell_network() -> None:
    args = claude._build_codex_argv(
        Path("codex.cmd"),
        model="gpt-5.6-terra",
        working_directory=Path("isolated"),
        enable_web=True,
    )

    assert 'web_search="live"' in args
    assert "shell_tool" in args
    assert "apps" in args
    assert "remote_plugin" in args
    assert "danger-full-access" not in args


def test_structured_codex_operational_failure_retries_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fail_primary(*_args: object, **_kwargs: object) -> claude.TransportResult:
        raise RuntimeError("primary unavailable")

    def fail_openrouter(*_args: object, **_kwargs: object) -> claude.TransportResult:
        raise RuntimeError("openrouter unavailable")

    def codex(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("codex")
        if len(calls) == 1:
            raise RuntimeError("transient membership failure")
        return claude.TransportResult(text='{"score": 77}', input_tokens=10, output_tokens=3)

    monkeypatch.setattr(claude, "_call_claude_structured_transport", fail_primary)
    monkeypatch.setattr(claude, "_call_openrouter_transport", fail_openrouter)
    monkeypatch.setattr(claude, "_call_codex_transport", codex)
    monkeypatch.setattr(claude.time, "sleep", _no_sleep)
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = claude.extract_structured(
        "score this deal",
        _StructuredAnswer,
        purpose=claude.Purpose.SCORE_MARKET,
    )

    assert result.score == 77
    assert calls == ["codex", "codex"]


def test_plain_codex_operational_failure_retries_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> claude.TransportResult:
        raise RuntimeError("provider unavailable")

    def codex(*_args: object, **_kwargs: object) -> claude.TransportResult:
        calls.append("codex")
        if len(calls) == 1:
            raise RuntimeError("transient membership failure")
        return claude.TransportResult(text="recovered", input_tokens=8, output_tokens=2)

    monkeypatch.setattr(claude, "_call_claude_transport", fail)
    monkeypatch.setattr(claude, "_call_openrouter_transport", fail)
    monkeypatch.setattr(claude, "_call_codex_transport", codex)
    monkeypatch.setattr(claude.time, "sleep", _no_sleep)
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = claude.call_llm(
        "write a diligence memo",
        purpose=claude.Purpose.DILIGENCE_TOPICS,
    )

    assert result == "recovered"
    assert calls == ["codex", "codex"]
