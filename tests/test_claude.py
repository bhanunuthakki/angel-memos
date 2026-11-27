"""Governed application-LLM routing, Gemini-first dispatch, and ledger behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from angel_memos import llm


class _StructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)


def test_call_llm_follows_fleet_route_gemini_then_claude(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fail_gemini(*_args: object, **_kwargs: object) -> llm.TransportResult:
        calls.append("gemini")
        raise RuntimeError("transient api error")

    def succeed_claude(*_args: object, **_kwargs: object) -> llm.TransportResult:
        calls.append("claude")
        return llm.TransportResult(text="fallback answer", input_tokens=11, output_tokens=3)

    monkeypatch.setattr(llm, "_call_gemini_transport", fail_gemini)
    monkeypatch.setattr(llm, "_call_claude_transport", succeed_claude)
    monkeypatch.setattr(llm, "_fleet_provider_order", lambda: ("gemini", "claude"))
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = llm.call_llm("private prompt", purpose=llm.Purpose.DILIGENCE_TOPICS)

    assert result == "fallback answer"
    assert calls == ["gemini", "claude"]


def test_judge_purposes_use_explicit_judge_route_separate_from_application_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "_fleet_provider_order", lambda: ("claude",))

    assert llm._backends_for_purpose(llm.Purpose.SCORE_MARKET) == llm.JUDGE_ROUTE
    assert llm._backends_for_purpose(llm.Purpose.DILIGENCE_TOPICS) == ("claude",)


def test_call_llm_ledger_hashes_private_text_and_records_gemini_usage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_prompt = "private company details"
    private_response = "private investment analysis"

    def succeed(*_args: object, **_kwargs: object) -> llm.TransportResult:
        return llm.TransportResult(
            text=private_response,
            input_tokens=1000,
            cached_input_tokens=200,
            output_tokens=100,
            reasoning_output_tokens=50,
        )

    monkeypatch.setattr(llm, "_call_gemini_transport", succeed)
    monkeypatch.setattr(llm, "_fleet_provider_order", lambda: ("gemini",))
    ledger_path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(ledger_path))

    llm.call_llm(private_prompt, purpose=llm.Purpose.DILIGENCE_TOPICS)

    line = ledger_path.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert private_prompt not in line
    assert private_response not in line
    assert entry["provider"] == "google"
    assert entry["transport"] == "metered_api"
    assert entry["model"] == llm.GEMINI_DRIVER_MODEL
    assert entry["cached_input_tokens"] == 200
    assert entry["reasoning_output_tokens"] == 50
    assert len(entry["prompt_sha256"]) == 64
    assert len(entry["response_sha256"]) == 64


def test_extract_structured_repairs_once_before_advancing_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    gemini_replies = iter(['{"score": 150}', '{"score": 82}'])

    def gemini(*_args: object, **_kwargs: object) -> llm.TransportResult:
        calls.append("gemini")
        return llm.TransportResult(text=next(gemini_replies), input_tokens=10, output_tokens=3)

    monkeypatch.setattr(llm, "_call_gemini_transport", gemini)
    monkeypatch.setattr(llm, "_fleet_provider_order", lambda: ("gemini", "claude"))
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = llm.extract_structured(
        "score this deal",
        _StructuredAnswer,
        purpose=llm.Purpose.DILIGENCE_TOPICS,
    )

    assert result.score == 82
    assert calls == ["gemini", "gemini"]


def test_extract_structured_falls_back_to_claude_after_gemini_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fail_gemini(*_args: object, **_kwargs: object) -> llm.TransportResult:
        calls.append("gemini")
        raise RuntimeError("gemini 503")

    def succeed_claude(*_args: object, **_kwargs: object) -> llm.TransportResult:
        calls.append("claude")
        return llm.TransportResult(text='{"score": 77}', input_tokens=10, output_tokens=3)

    monkeypatch.setattr(llm, "_call_gemini_transport", fail_gemini)
    monkeypatch.setattr(llm, "_call_claude_structured_transport", succeed_claude)
    monkeypatch.setenv("ANGEL_MEMOS_LLM_LEDGER", str(tmp_path / "llm_calls.jsonl"))

    result = llm.extract_structured(
        "score this deal",
        _StructuredAnswer,
        purpose=llm.Purpose.SCORE_MARKET,
    )

    assert result.score == 77
    assert calls == ["gemini", "claude"]


def test_application_call_sites_use_only_the_governed_entry_point() -> None:
    package = Path(__file__).parents[1] / "src" / "angel_memos"
    exempt = {"llm.py", "claude.py", "claude_cli.py"}
    violations = [
        path.name
        for path in package.glob("*.py")
        if path.name not in exempt
        and (
            "angel_memos.claude_cli" in path.read_text(encoding="utf-8")
            or "call_claude(" in path.read_text(encoding="utf-8")
            or "from google import genai" in path.read_text(encoding="utf-8")
        )
    ]

    assert violations == []


def test_every_purpose_has_a_governed_route() -> None:
    assert set(llm.Purpose) == set(llm.MODEL_ROUTES)


def test_strip_at_image_refs_removes_claude_file_markers_only() -> None:
    prompt = "@/tmp/deck/page_001.png\n@/tmp/deck/page_002.PNG\n\nExtract founder names.\nEmail: founder@acme.com"

    cleaned = llm._strip_at_image_refs(prompt)

    assert "/tmp/deck/page_001.png" not in cleaned
    assert "Extract founder names." in cleaned
    assert "founder@acme.com" in cleaned


def test_resolve_gemini_api_key_prefers_env_then_project_then_instructions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instructions = tmp_path / "agent-instructions"
    (instructions / "snippets").mkdir(parents=True)
    (instructions / "snippets" / "llm_policy.py").write_text("", encoding="utf-8")
    (instructions / ".env").write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv(llm.AGENT_INSTRUCTIONS_ROOT_ENV_VAR, str(instructions))

    assert llm._resolve_gemini_api_key() == "from-file"

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert llm._resolve_gemini_api_key() == "from-env"


def test_agent_instructions_root_finds_sibling_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    applications = tmp_path / "Applications"
    module_path = applications / "angel-memos" / "src" / "angel_memos" / "llm.py"
    root = applications / "agent-instructions"
    policy = root / "snippets" / "llm_policy.py"
    policy.parent.mkdir(parents=True)
    policy.write_text("", encoding="utf-8")
    monkeypatch.delenv(llm.AGENT_INSTRUCTIONS_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(llm, "__file__", str(module_path))

    assert llm._agent_instructions_root() == root
