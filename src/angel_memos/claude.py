"""Governed application-LLM entry point.

All application calls select a route by purpose and use the same ordered
transport chain: Claude subscription, OpenRouter, then Codex membership.
Private prompts and responses are represented in the ledger only by hashes.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from angel_memos.claude_cli import call_claude
from angel_memos.claude_cli import extract_structured as extract_claude_structured

DEFAULT_TIMEOUT_SECONDS = 600
_CODEX_RETRY_DELAY_SECONDS = 5
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AGENT_INSTRUCTIONS_ROOT_ENV_VAR = "ANGEL_MEMOS_AGENT_INSTRUCTIONS_ROOT"


class Purpose(StrEnum):
    ANGELLIST_TERMS = "angellist_terms"
    ANGELLIST_FOUNDERS = "angellist_founders"
    DECK_EXTRACTION = "deck_extraction"
    DILIGENCE_TOPICS = "diligence_topics"
    FOUNDER_PROFILE = "founder_profile"
    COMPARABLE_DEALS = "comparable_deals"
    RECENT_EVENTS = "recent_events"
    INVESTOR_GRADE = "investor_grade"
    SCORE_ARCHETYPE = "score_archetype"
    SCORE_TEAM_FIT = "score_team_fit"
    SCORE_MARKET = "score_market"
    SCORE_TRACTION_TECH = "score_traction_tech"
    SCORE_COMMERCIAL_EVIDENCE = "score_commercial_evidence"
    SCORE_DEFENSIBILITY = "score_defensibility"
    SCORE_EXECUTION_CAPITAL = "score_execution_capital"
    SCORE_CRITIQUE = "score_critique"
    PRIVATE_DOC_ENTRY = "private_doc_entry"
    PUBLIC_DOC_ENTRY = "public_doc_entry"
    LONG_MEMO = "long_memo"
    DECISION_REVIEW = "decision_review"


@dataclass(frozen=True)
class ModelRoute:
    claude_model: str
    openrouter_model: str
    codex_model: str
    rationale: str
    requires_web: bool = False


@dataclass(frozen=True)
class TransportResult:
    text: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0


MODEL_ROUTES: dict[Purpose, ModelRoute] = {
    Purpose.ANGELLIST_TERMS: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Vision-heavy structured terms extraction; preserve incumbent.",
    ),
    Purpose.ANGELLIST_FOUNDERS: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Name extraction needs strong vision and instruction following.",
    ),
    Purpose.DECK_EXTRACTION: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Long multimodal extraction remains on the validated incumbent.",
    ),
    Purpose.DILIGENCE_TOPICS: ModelRoute(
        claude_model="claude-opus-4-7",
        openrouter_model="openai/gpt-5.6-terra",
        codex_model="gpt-5.6-terra",
        rationale="Preserve the incumbent judgment tier; use balanced cross-provider fallbacks.",
    ),
    Purpose.FOUNDER_PROFILE: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Current founder research requires grounded web evidence.",
        requires_web=True,
    ),
    Purpose.COMPARABLE_DEALS: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Current private-market comparable research requires web evidence.",
        requires_web=True,
    ),
    Purpose.INVESTOR_GRADE: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Investor grading requires current sourced research.",
        requires_web=True,
    ),
    Purpose.RECENT_EVENTS: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Event discovery (exec quotes, competitor M&A, insourcing) requires live web search.",
        requires_web=True,
    ),
    Purpose.SCORE_MARKET: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Schema-bound market judgment needs balanced reasoning.",
    ),
    Purpose.SCORE_ARCHETYPE: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Closed-schema archetype selection chooses evidence anchors without scoring.",
    ),
    Purpose.SCORE_TEAM_FIT: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Founder-market-fit judgment is separated from deterministic pedigree.",
    ),
    Purpose.SCORE_TRACTION_TECH: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Schema-bound traction and technology judgment needs balanced reasoning.",
    ),
    Purpose.SCORE_COMMERCIAL_EVIDENCE: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Schema-bound commercial evidence judgment includes claim reconciliation.",
    ),
    Purpose.SCORE_DEFENSIBILITY: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Schema-bound moat judgment uses archetype-specific evidence anchors.",
    ),
    Purpose.SCORE_EXECUTION_CAPITAL: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Schema-bound execution and capital-scaling judgment.",
    ),
    Purpose.SCORE_CRITIQUE: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Narrow adversarial score review is schema constrained.",
    ),
    Purpose.PRIVATE_DOC_ENTRY: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Style-bound structured investment summary.",
    ),
    Purpose.PUBLIC_DOC_ENTRY: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-terra",
        "gpt-5.6-terra",
        "Privacy-sensitive masked memo generation needs strong instruction following.",
    ),
    Purpose.LONG_MEMO: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-sol",
        "gpt-5.6-sol",
        "Long-form investment synthesis is judgment heavy.",
    ),
    Purpose.DECISION_REVIEW: ModelRoute(
        "claude-opus-4-7",
        "openai/gpt-5.6-sol",
        "gpt-5.6-sol",
        "Adversarial investment review is judgment heavy.",
    ),
}

_PUBLIC_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "openai/gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "openai/gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-sol": (5.0, 30.0),
}


class LlmCallError(RuntimeError):
    """All configured transports failed without exposing private payloads."""


class _LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    purpose: str
    attempt: int = Field(ge=1)
    model: str
    provider: str
    transport: str
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_output_tokens: int = Field(ge=0)
    cost_estimate_usd: float = Field(ge=0.0)
    elapsed_ms: int = Field(ge=0)
    success: bool
    error_type: str | None
    fallback_used: str | None
    prompt_sha256: str = Field(min_length=64, max_length=64)
    response_sha256: str | None = None


class _OpenRouterMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class _OpenRouterChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _OpenRouterMessage


class _OpenRouterUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class _OpenRouterResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: list[_OpenRouterChoice] = Field(min_length=1)
    usage: _OpenRouterUsage = Field(default_factory=_OpenRouterUsage)


class _CodexUsage(Protocol):
    @property
    def input_tokens(self) -> int: ...

    @property
    def cached_input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def reasoning_output_tokens(self) -> int: ...


class _CodexResult(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def usage(self) -> _CodexUsage: ...


class _CodexUsageValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_output_tokens: int = Field(ge=0)


class _CodexItemValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str
    text: str | None = None


class _CodexEventValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str
    item: _CodexItemValue | None = None
    usage: _CodexUsageValue | None = None


@dataclass(frozen=True)
class _CodexResultValue:
    text: str
    usage: _CodexUsageValue


class _CodexModule(Protocol):
    def call_codex_with_usage(
        self,
        prompt: str,
        *,
        model: str,
        timeout_seconds: int,
    ) -> _CodexResult: ...


def call_llm(
    prompt: str,
    *,
    purpose: Purpose,
    system_prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    image_paths: list[Path] | None = None,
) -> str:
    """Call the purpose route, falling back only on operational failure."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    route = MODEL_ROUTES[purpose]
    run_id = str(uuid.uuid4())
    images = image_paths or []
    full_prompt = _combine_prompts(system_prompt, prompt)
    attempts = (
        (
            "anthropic",
            "subscription_cli",
            route.claude_model,
            _call_claude_transport,
            0,
        ),
        (
            "openrouter",
            "metered_api",
            route.openrouter_model,
            _call_openrouter_transport,
            0,
        ),
        (
            "openai",
            "subscription_cli",
            route.codex_model,
            _call_codex_transport,
            0,
        ),
        (
            "openai",
            "subscription_cli",
            route.codex_model,
            _call_codex_transport,
            _CODEX_RETRY_DELAY_SECONDS,
        ),
    )
    failures: list[str] = []
    for attempt_number, (provider, transport, model, caller, delay_seconds) in enumerate(
        attempts, start=1
    ):
        if delay_seconds:
            time.sleep(delay_seconds)
        started = time.perf_counter()
        try:
            result = caller(
                full_prompt,
                model=model,
                timeout_seconds=timeout_seconds,
                image_paths=images,
                requires_web=route.requires_web,
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            failures.append(type(exc).__name__)
            _append_ledger(
                _ledger_entry(
                    run_id=run_id,
                    purpose=purpose,
                    attempt=attempt_number,
                    model=model,
                    provider=provider,
                    transport=transport,
                    prompt=full_prompt,
                    result=None,
                    elapsed_ms=elapsed_ms,
                    error_type=type(exc).__name__,
                )
            )
            continue
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _append_ledger(
            _ledger_entry(
                run_id=run_id,
                purpose=purpose,
                attempt=attempt_number,
                model=model,
                provider=provider,
                transport=transport,
                prompt=full_prompt,
                result=result,
                elapsed_ms=elapsed_ms,
                error_type=None,
            )
        )
        return result.text

    failure_summary = ", ".join(failures)
    raise LlmCallError(
        f"all configured transports failed for purpose={purpose.value}: {failure_summary}"
    ) from None


def extract_structured[T: BaseModel](
    prompt: str,
    model_type: type[T],
    *,
    purpose: Purpose,
    system_prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    additional_dirs: list[str] | None = None,
    image_paths: list[Path] | None = None,
) -> T:
    """Return schema-validated output with one repair attempt per provider."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    route = MODEL_ROUTES[purpose]
    run_id = str(uuid.uuid4())
    images = image_paths or []
    failures: list[str] = []
    attempt_number = 0

    def call_primary(current_prompt: str) -> TransportResult:
        return _call_claude_structured_transport(
            current_prompt,
            model_type=model_type,
            model=route.claude_model,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            additional_dirs=additional_dirs,
            image_paths=images,
        )

    def call_openrouter(current_prompt: str) -> TransportResult:
        return _call_openrouter_transport(
            _structured_prompt(current_prompt, model_type, system_prompt),
            model=route.openrouter_model,
            timeout_seconds=timeout_seconds,
            image_paths=images,
            requires_web=route.requires_web,
        )

    def call_codex(current_prompt: str) -> TransportResult:
        return _call_codex_transport(
            _structured_prompt(current_prompt, model_type, system_prompt),
            model=route.codex_model,
            timeout_seconds=timeout_seconds,
            image_paths=images,
            requires_web=route.requires_web,
        )

    providers: tuple[tuple[str, str, str, Callable[[str], TransportResult]], ...] = (
        (
            "anthropic",
            "subscription_cli",
            route.claude_model,
            call_primary,
        ),
        (
            "openrouter",
            "metered_api",
            route.openrouter_model,
            call_openrouter,
        ),
        (
            "openai",
            "subscription_cli",
            route.codex_model,
            call_codex,
        ),
    )
    for provider, transport, model, caller in providers:
        current_prompt = prompt
        for repair_number in range(2):
            attempt_number += 1
            ledger_prompt = _structured_prompt(current_prompt, model_type, system_prompt)
            started = time.perf_counter()
            try:
                result = caller(current_prompt)
                validated = model_type.model_validate_json(_extract_json_value(result.text))
            except ValidationError as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                failures.append(type(exc).__name__)
                _append_ledger(
                    _ledger_entry(
                        run_id=run_id,
                        purpose=purpose,
                        attempt=attempt_number,
                        model=model,
                        provider=provider,
                        transport=transport,
                        prompt=ledger_prompt,
                        result=None,
                        elapsed_ms=elapsed_ms,
                        error_type=type(exc).__name__,
                    )
                )
                if repair_number == 0:
                    current_prompt = _repair_prompt(prompt, exc)
                    continue
                break
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                failures.append(type(exc).__name__)
                _append_ledger(
                    _ledger_entry(
                        run_id=run_id,
                        purpose=purpose,
                        attempt=attempt_number,
                        model=model,
                        provider=provider,
                        transport=transport,
                        prompt=ledger_prompt,
                        result=None,
                        elapsed_ms=elapsed_ms,
                        error_type=type(exc).__name__,
                    )
                )
                if (
                    provider == "openai"
                    and repair_number == 0
                    and not isinstance(exc, (ValueError, FileNotFoundError))
                ):
                    time.sleep(_CODEX_RETRY_DELAY_SECONDS)
                    continue
                break
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            _append_ledger(
                _ledger_entry(
                    run_id=run_id,
                    purpose=purpose,
                    attempt=attempt_number,
                    model=model,
                    provider=provider,
                    transport=transport,
                    prompt=ledger_prompt,
                    result=result,
                    elapsed_ms=elapsed_ms,
                    error_type=None,
                )
            )
            return validated

    failure_summary = ", ".join(failures)
    raise LlmCallError(
        f"all configured transports failed structured output for "
        f"purpose={purpose.value}: {failure_summary}"
    ) from None


def _call_claude_structured_transport[T: BaseModel](
    prompt: str,
    *,
    model_type: type[T],
    model: str,
    system_prompt: str | None,
    timeout_seconds: int,
    additional_dirs: list[str] | None,
    image_paths: list[Path],
) -> TransportResult:
    del image_paths  # Claude consumes the same images through @path references.
    value = extract_claude_structured(
        prompt,
        model_type,
        model=model,
        system_prompt=system_prompt,
        timeout_seconds=timeout_seconds,
        additional_dirs=additional_dirs,
    )
    text = value.model_dump_json()
    return TransportResult(
        text=text,
        input_tokens=_estimate_tokens(_combine_prompts(system_prompt, prompt)),
        output_tokens=_estimate_tokens(text),
    )


def _call_claude_transport(
    prompt: str,
    *,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    requires_web: bool,
) -> TransportResult:
    del image_paths, requires_web
    # Claude resolves @path references already embedded in the prompt. Raw
    # prose purposes currently do not require WebSearch.
    text = call_claude(prompt, model=model, timeout_seconds=timeout_seconds)
    return TransportResult(
        text=text,
        input_tokens=_estimate_tokens(prompt),
        output_tokens=_estimate_tokens(text),
    )


def _call_openrouter_transport(
    prompt: str,
    *,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    requires_web: bool,
) -> TransportResult:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    content: str | list[dict[str, object]]
    if image_paths:
        parts: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            mime_type = _image_mime_type(image_path)
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        content = parts
    else:
        content = prompt
    request_body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if requires_web:
        request_body["tools"] = [{"type": "openrouter:web_search"}]
    payload = json.dumps(request_body).encode("utf-8")
    request = urllib.request.Request(
        _OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Angel Memos",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
        parsed = _OpenRouterResponse.model_validate_json(raw)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenRouter returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValidationError, ValueError):
        raise RuntimeError("OpenRouter request failed or returned an invalid response") from None
    text = parsed.choices[0].message.content.strip()
    if not text:
        raise RuntimeError("OpenRouter returned empty content")
    return TransportResult(
        text=text,
        input_tokens=parsed.usage.prompt_tokens,
        output_tokens=parsed.usage.completion_tokens,
    )


def _call_codex_transport(
    prompt: str,
    *,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    requires_web: bool,
) -> TransportResult:
    if requires_web or image_paths:
        direct_prompt = prompt
        if requires_web:
            direct_prompt = (
                "Use live web search only for public-source research needed to answer "
                "the request. Never put confidential deal terms, private metrics, or "
                "other non-public material into a search query.\n\n" + prompt
            )
        result = _call_codex_direct(
            direct_prompt,
            model=model,
            timeout_seconds=timeout_seconds,
            image_paths=image_paths,
            enable_web=requires_web,
        )
    else:
        module = _load_codex_module()
        result = module.call_codex_with_usage(
            prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    return TransportResult(
        text=result.text,
        input_tokens=result.usage.input_tokens,
        cached_input_tokens=result.usage.cached_input_tokens,
        output_tokens=result.usage.output_tokens,
        reasoning_output_tokens=result.usage.reasoning_output_tokens,
    )


def _load_codex_module() -> _CodexModule:
    module_name = "_angel_memos_codex_cli"
    existing = sys.modules.get(module_name)
    if existing is None:
        path = _agent_instructions_root() / "snippets" / "codex_cli.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load the canonical Codex membership wrapper")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = loaded
        spec.loader.exec_module(loaded)
        existing = loaded
    if not hasattr(existing, "call_codex_with_usage"):
        raise RuntimeError("canonical Codex membership wrapper has an invalid interface")
    return cast("_CodexModule", existing)


def _call_codex_direct(
    prompt: str,
    *,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    enable_web: bool,
) -> _CodexResult:
    for image_path in image_paths:
        if not image_path.is_file():
            raise FileNotFoundError(f"Codex image attachment not found: {image_path}")
    instructions_root = _agent_instructions_root()
    cli_path = _codex_cli_path(instructions_root)
    codex_home = _codex_membership_home(instructions_root)
    env = _codex_membership_env(codex_home)
    _verify_codex_membership(cli_path, env)
    with tempfile.TemporaryDirectory(prefix="codex-llm-") as temp_dir:
        argv = _build_codex_argv(
            cli_path,
            model=model,
            working_directory=Path(temp_dir),
            enable_web=enable_web,
        )
        argv = _with_codex_images(argv, image_paths)
        try:
            completed = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Codex membership call timed out") from None
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Codex membership call exited with status {exc.returncode}"
            ) from None
        except OSError:
            raise RuntimeError("Codex membership call could not start") from None
    return _parse_codex_events(completed.stdout)


def _agent_instructions_root() -> Path:
    override = os.environ.get(AGENT_INSTRUCTIONS_ROOT_ENV_VAR, "").strip()
    if override:
        return Path(override)

    candidates = (
        Path.home() / ".gemini",
        Path(os.environ.get("AGENT_INSTRUCTIONS_DIR", "agent-instructions")),
    )
    for candidate in candidates:
        if (candidate / "snippets" / "codex_cli.py").is_file():
            return candidate
    raise FileNotFoundError(
        "agent-instructions root not found; set " + AGENT_INSTRUCTIONS_ROOT_ENV_VAR
    )


def _codex_cli_path(instructions_root: Path) -> Path:
    bin_dir = instructions_root / ".tools" / "node_modules" / ".bin"
    for name in ("codex.cmd", "codex"):
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate
    # Return the platform-native expected path so the existing setup check
    # emits the stable "not installed" error without guessing another home.
    return bin_dir / ("codex.cmd" if os.name == "nt" else "codex")


def _codex_membership_home(instructions_root: Path) -> Path:
    return instructions_root / ".codex-membership"


def _codex_membership_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    env["CODEX_HOME"] = str(codex_home)
    env["NO_COLOR"] = "1"
    return env


def _verify_codex_membership(cli_path: Path, env: dict[str, str]) -> None:
    if not cli_path.is_file():
        raise RuntimeError("managed Codex CLI is not installed")
    try:
        status = subprocess.run(
            [str(cli_path), "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Codex membership authentication could not be verified") from None
    output = f"{status.stdout}\n{status.stderr}"
    if status.returncode != 0 or "Logged in using ChatGPT" not in output:
        raise RuntimeError("dedicated Codex home is not signed in with ChatGPT")


def _build_codex_argv(
    cli_path: Path,
    *,
    model: str,
    working_directory: Path,
    enable_web: bool,
) -> list[str]:
    if not model.startswith("gpt-"):
        raise ValueError("Codex membership transport accepts only GPT model IDs")
    return [
        str(cli_path),
        "exec",
        "--model",
        model,
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--json",
        "--disable",
        "shell_tool",
        "--disable",
        "shell_snapshot",
        "--disable",
        "apps",
        "--disable",
        "hooks",
        "--disable",
        "multi_agent",
        "--disable",
        "remote_plugin",
        "--config",
        f'web_search="{"live" if enable_web else "disabled"}"',
        "--config",
        'model_reasoning_effort="medium"',
        "--cd",
        str(working_directory),
        "-",
    ]


def _parse_codex_events(stdout: str) -> _CodexResultValue:
    final_text: str | None = None
    final_usage: _CodexUsageValue | None = None
    if not stdout.strip():
        raise RuntimeError("Codex returned an empty event stream")
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = _CodexEventValue.model_validate_json(raw_line)
        except ValidationError:
            raise RuntimeError("Codex returned an invalid event stream") from None
        if event.type == "item.completed" and event.item is not None:
            if event.item.type == "agent_message" and event.item.text:
                final_text = event.item.text.strip()
        elif event.type == "turn.completed" and event.usage is not None:
            final_usage = event.usage
    if not final_text:
        raise RuntimeError("Codex did not return a final agent message")
    if final_usage is None:
        raise RuntimeError("Codex did not return token usage")
    return _CodexResultValue(text=final_text, usage=final_usage)


def _with_codex_images(argv: list[str], image_paths: list[Path]) -> list[str]:
    if not argv or argv[-1] != "-":
        raise ValueError("Codex exec argv must end with the stdin prompt marker")
    with_images = list(argv[:-1])
    for image_path in image_paths:
        with_images.extend(["--image", str(image_path)])
    with_images.append("-")
    return with_images


def _ledger_entry(
    *,
    run_id: str,
    purpose: Purpose,
    attempt: int,
    model: str,
    provider: str,
    transport: str,
    prompt: str,
    result: TransportResult | None,
    elapsed_ms: int,
    error_type: str | None,
) -> _LedgerEntry:
    input_tokens = result.input_tokens if result else _estimate_tokens(prompt)
    output_tokens = result.output_tokens if result else 0
    prices = _PUBLIC_PRICES_USD_PER_MTOK[model]
    cost = (input_tokens / 1_000_000) * prices[0] + (output_tokens / 1_000_000) * prices[1]
    return _LedgerEntry(
        run_id=run_id,
        purpose=purpose.value,
        attempt=attempt,
        model=model,
        provider=provider,
        transport=transport,
        input_tokens=input_tokens,
        cached_input_tokens=result.cached_input_tokens if result else 0,
        output_tokens=output_tokens,
        reasoning_output_tokens=result.reasoning_output_tokens if result else 0,
        cost_estimate_usd=cost,
        elapsed_ms=elapsed_ms,
        success=result is not None,
        error_type=error_type,
        fallback_used=provider if attempt > 1 else None,
        prompt_sha256=_sha256(prompt),
        response_sha256=_sha256(result.text) if result else None,
    )


def _append_ledger(entry: _LedgerEntry) -> None:
    configured = os.environ.get("ANGEL_MEMOS_LLM_LEDGER", "").strip()
    path = Path(configured) if configured else Path.home() / ".angel-memos" / "llm_calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")


def _combine_prompts(system_prompt: str | None, prompt: str) -> str:
    if system_prompt is None:
        return prompt
    return f"## SYSTEM INSTRUCTIONS\n\n{system_prompt}\n\n## USER REQUEST\n\n{prompt}"


def _structured_prompt[T: BaseModel](
    prompt: str,
    model_type: type[T],
    system_prompt: str | None,
) -> str:
    schema = json.dumps(model_type.model_json_schema(), indent=2)
    request = f"{prompt}\n\nReturn only one JSON object matching this schema:\n{schema}"
    return _combine_prompts(system_prompt, request)


def _repair_prompt(prompt: str, error: ValidationError) -> str:
    return (
        f"{prompt}\n\nYour previous response did not satisfy the required JSON schema:\n"
        f"{error}\nReturn only corrected JSON."
    )


def _extract_json_value(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = (
            stripped[first_newline + 1 :].strip() if first_newline >= 0 else stripped[3:].strip()
        )
    if stripped.endswith("```"):
        stripped = stripped[:-3].rstrip()
    starts = [index for opener in ("{", "[") if (index := stripped.find(opener)) >= 0]
    if not starts:
        raise RuntimeError("LLM response contained no JSON value")
    candidate = stripped[min(starts) :]
    try:
        _value, consumed = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return candidate
    return candidate[:consumed]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    raise ValueError(f"unsupported image type: {suffix}")
