"""Governed, provider-neutral application-LLM entry point.

Synced with earnings-summary's Gemini-first + Claude-fallback architecture and
governed by agent-instructions' fleet routing policy (`preferred_provider_order`
and `subscription_route`). Private prompts and responses are represented in the
ledger only by SHA-256 hashes.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from angel_memos.claude_cli import call_claude
from angel_memos.claude_cli import extract_structured as extract_claude_structured
from angel_memos.log_redact import redact

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 600
AGENT_INSTRUCTIONS_ROOT_ENV_VAR = "AGENT_INSTRUCTIONS_HOME"

# Inline base64 requests on Gemini Interactions cap at 20 MB total payload.
# Keep a generous margin for prompt text and base64 (~33%) overhead; larger
# decks upload via Files API and are deleted immediately in `finally`.
_MAX_INLINE_IMAGE_BYTES = 12 * 1024 * 1024

_AT_IMAGE_REF_LINE_RE = re.compile(r"^\s*@.+\.(?:png|jpe?g|webp)\s*$", re.IGNORECASE)
_AUTH_ERROR_MARKERS = (
    "API_KEY_INVALID",
    "API key not valid",
    "PERMISSION_DENIED",
    "UNAUTHENTICATED",
)

GEMINI_WORKHORSE_MODEL = "gemini-4-hydrogen"
GEMINI_DRIVER_MODEL = "gemini-4-neon"
CLAUDE_FALLBACK_MODEL = "claude-opus-4-7"


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


JUDGE_PURPOSES = frozenset(
    {
        Purpose.SCORE_ARCHETYPE,
        Purpose.SCORE_TEAM_FIT,
        Purpose.SCORE_MARKET,
        Purpose.SCORE_TRACTION_TECH,
        Purpose.SCORE_COMMERCIAL_EVIDENCE,
        Purpose.SCORE_DEFENSIBILITY,
        Purpose.SCORE_EXECUTION_CAPITAL,
        Purpose.SCORE_CRITIQUE,
    }
)

# Explicit, separately registered route for advisory scoring judge purposes
# (distinct from ordinary application routing; registered in llm_usage_index.json).
JUDGE_ROUTE: tuple[str, ...] = ("gemini", "claude")


@dataclass(frozen=True)
class ModelRoute:
    gemini_model: str
    claude_model: str
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
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Multimodal structured terms extraction via fleet workhorse.",
    ),
    Purpose.ANGELLIST_FOUNDERS: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Founder name extraction via fleet workhorse.",
    ),
    Purpose.DECK_EXTRACTION: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Multi-slide pitch deck extraction via fleet multimodal workhorse.",
    ),
    Purpose.DILIGENCE_TOPICS: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Adversarial diligence synthesis uses the fleet driver tier.",
    ),
    Purpose.FOUNDER_PROFILE: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Current founder research requires live web grounding.",
        requires_web=True,
    ),
    Purpose.COMPARABLE_DEALS: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Current private-market comparable research requires live web grounding.",
        requires_web=True,
    ),
    Purpose.INVESTOR_GRADE: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Investor grading requires current sourced web research.",
        requires_web=True,
    ),
    Purpose.RECENT_EVENTS: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Recent-event discovery requires live web grounding.",
        requires_web=True,
    ),
    Purpose.SCORE_ARCHETYPE: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Closed-schema archetype selection on explicit advisory judge route.",
    ),
    Purpose.SCORE_TEAM_FIT: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Founder-market-fit advisory judge separated from deterministic pedigree.",
    ),
    Purpose.SCORE_MARKET: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Schema-bound market advisory judgment.",
    ),
    Purpose.SCORE_TRACTION_TECH: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Schema-bound traction and technology advisory judgment.",
    ),
    Purpose.SCORE_COMMERCIAL_EVIDENCE: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Schema-bound commercial evidence advisory judgment.",
    ),
    Purpose.SCORE_DEFENSIBILITY: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Schema-bound moat advisory judgment.",
    ),
    Purpose.SCORE_EXECUTION_CAPITAL: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Schema-bound execution and capital-scaling advisory judgment.",
    ),
    Purpose.SCORE_CRITIQUE: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Narrow adversarial score critique.",
    ),
    Purpose.PRIVATE_DOC_ENTRY: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Style-bound structured private investment summary.",
    ),
    Purpose.PUBLIC_DOC_ENTRY: ModelRoute(
        GEMINI_WORKHORSE_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Privacy-sensitive masked public summary.",
    ),
    Purpose.LONG_MEMO: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Long-form investment synthesis uses the fleet driver tier.",
    ),
    Purpose.DECISION_REVIEW: ModelRoute(
        GEMINI_DRIVER_MODEL,
        CLAUDE_FALLBACK_MODEL,
        "Adversarial decision pressure-test uses the fleet driver tier.",
    ),
}

# Public API list prices ($/MTok input, output) verified against
# agent-instructions/procedures/model-frontier.REFERENCE.md (2026-11-25).
_PUBLIC_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-4-neon": (3.00, 15.00),
    "gemini-4-carbon": (2.00, 10.00),
    "gemini-4-argon": (2.00, 10.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}


class LlmCallError(RuntimeError):
    """All configured transports failed without exposing private payloads."""


class GeminiSetupError(RuntimeError):
    """Gemini Developer API credentials are missing or rejected."""


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

    def call_gemini_plain(
        _combined: str,
        *,
        model: str,
        timeout_seconds: int,
        image_paths: list[Path],
        requires_web: bool,
    ) -> TransportResult:
        del _combined
        return _call_gemini_transport(
            prompt,
            system_prompt=system_prompt,
            model=model,
            timeout_seconds=timeout_seconds,
            image_paths=image_paths,
            requires_web=requires_web,
        )

    backend_attempts: dict[str, tuple[str, str, str, Callable[..., TransportResult]]] = {
        "gemini": (
            "google",
            "metered_api",
            route.gemini_model,
            call_gemini_plain,
        ),
        "claude": (
            "anthropic",
            "subscription_cli",
            route.claude_model,
            _call_claude_transport,
        ),
    }
    attempts: list[tuple[str, str, str, Callable[..., TransportResult]]] = []
    for backend in _backends_for_purpose(purpose):
        try:
            attempts.append(backend_attempts[backend])
        except KeyError:
            raise LlmCallError(f"unsupported fleet backend: {backend}") from None

    failures: list[str] = []
    for attempt_number, (provider, transport, model, caller) in enumerate(attempts, start=1):
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
            logger.warning(
                "llm call failed: purpose=%s provider=%s model=%s error=%s",
                purpose.value,
                provider,
                model,
                redact(f"{type(exc).__name__}: {str(exc)[:300]}"),
            )
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

    def call_gemini(current_prompt: str) -> TransportResult:
        schema = json.dumps(model_type.model_json_schema(), indent=2)
        request = (
            f"{current_prompt}\n\n"
            "Return ONLY one valid JSON object matching this schema — no markdown "
            f"code fences and no surrounding prose:\n{schema}"
        )
        return _call_gemini_transport(
            request,
            system_prompt=system_prompt,
            model=route.gemini_model,
            timeout_seconds=timeout_seconds,
            image_paths=images,
            requires_web=route.requires_web,
        )

    def call_claude_structured(current_prompt: str) -> TransportResult:
        return _call_claude_structured_transport(
            current_prompt,
            model_type=model_type,
            model=route.claude_model,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            additional_dirs=additional_dirs,
            image_paths=images,
        )

    backend_providers: dict[str, tuple[str, str, str, Callable[[str], TransportResult]]] = {
        "gemini": (
            "google",
            "metered_api",
            route.gemini_model,
            call_gemini,
        ),
        "claude": (
            "anthropic",
            "subscription_cli",
            route.claude_model,
            call_claude_structured,
        ),
    }
    try:
        providers = tuple(backend_providers[backend] for backend in _backends_for_purpose(purpose))
    except KeyError as exc:
        raise LlmCallError(f"unsupported fleet backend: {exc.args[0]}") from None

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
                logger.warning(
                    "structured llm call failed: purpose=%s provider=%s model=%s error=%s",
                    purpose.value,
                    provider,
                    model,
                    redact(f"{type(exc).__name__}: {str(exc)[:300]}"),
                )
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


def _resolve_gemini_api_key() -> str:
    """Resolve GEMINI_API_KEY per machine-operations.md: env -> project .env -> agent-instructions/.env."""
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val

    candidate_files: list[Path] = [Path(__file__).resolve().parents[2] / ".env"]
    with contextlib.suppress(FileNotFoundError):
        candidate_files.append(_agent_instructions_root() / ".env")

    for env_file in candidate_files:
        key = _read_env_var(env_file, "GEMINI_API_KEY") or _read_env_var(env_file, "GOOGLE_API_KEY")
        if key:
            return key

    raise GeminiSetupError(
        "GEMINI_API_KEY is not configured in environment, project .env, or agent-instructions/.env."
    )


def _read_env_var(env_file: Path, name: str) -> str:
    """Read one named variable from a .env file without loading unrelated secrets."""
    if not env_file.is_file():
        return ""
    prefix = f"{name}="
    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith(prefix):
                return line[len(prefix) :].strip().strip("'\"")
    except OSError:
        return ""
    return ""


def _strip_at_image_refs(prompt: str) -> str:
    """Remove Claude-CLI `@/path/to/page.png` lines when images are attached directly."""
    lines = [line for line in prompt.splitlines() if not _AT_IMAGE_REF_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def _safe_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _call_gemini_transport(
    prompt: str,
    *,
    system_prompt: str | None,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    requires_web: bool,
) -> TransportResult:
    """Call Gemini Developer API via google-genai Interactions API with store=False."""
    credential = _resolve_gemini_api_key()
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types

    for image_path in image_paths:
        if not image_path.is_file():
            raise FileNotFoundError(f"Gemini image attachment not found: {image_path}")

    client = genai.Client(
        api_key=credential,
        http_options=genai_types.HttpOptions(
            timeout=timeout_seconds * 1000,
            retry_options=genai_types.HttpRetryOptions(attempts=2),
        ),
    )
    uploaded_names: list[str] = []
    try:
        clean_prompt = _strip_at_image_refs(prompt) if image_paths else prompt
        if requires_web:
            clean_prompt = (
                "Use Google Search only for public-source research needed to answer the "
                "request. Never put confidential deal terms, private metrics, or other "
                "non-public material into a search query.\n\n" + clean_prompt
            )

        interaction_input: str | list[dict[str, object]]
        if image_paths:
            total_bytes = sum(p.stat().st_size for p in image_paths)
            parts: list[dict[str, object]] = [{"type": "text", "text": clean_prompt}]
            if total_bytes <= _MAX_INLINE_IMAGE_BYTES:
                for image_path in image_paths:
                    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
                    parts.append(
                        {
                            "type": "image",
                            "data": encoded,
                            "mime_type": _image_mime_type(image_path),
                        }
                    )
            else:
                for image_path in image_paths:
                    uploaded = client.files.upload(file=str(image_path))
                    if uploaded.name:
                        uploaded_names.append(uploaded.name)
                    parts.append(
                        {
                            "type": "image",
                            "uri": uploaded.uri,
                            "mime_type": uploaded.mime_type or _image_mime_type(image_path),
                        }
                    )
            interaction_input = parts
        else:
            interaction_input = clean_prompt

        kwargs: dict[str, Any] = {
            "model": model,
            "input": interaction_input,
            "store": False,
        }
        if system_prompt:
            kwargs["system_instruction"] = system_prompt
        if requires_web:
            kwargs["tools"] = [{"type": "google_search"}]

        try:
            interaction = client.interactions.create(**kwargs)
        except genai_errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code in {401, 403} or (
                code == 400 and any(marker in str(exc) for marker in _AUTH_ERROR_MARKERS)
            ):
                raise GeminiSetupError(
                    f"Gemini API rejected credentials (HTTP {code}): {redact(str(exc)[:200])}"
                ) from None
            raise RuntimeError(
                f"Gemini API error (HTTP {code}): {redact(str(exc)[:200])}"
            ) from None

        raw_output: object = getattr(interaction, "output_text", None)
        text = raw_output.strip() if isinstance(raw_output, str) else ""
        if not text:
            raise RuntimeError(f"Gemini returned empty output for model {model}")

        usage = getattr(interaction, "usage", None)
        return TransportResult(
            text=text,
            input_tokens=_safe_int(getattr(usage, "total_input_tokens", 0)),
            cached_input_tokens=_safe_int(getattr(usage, "total_cached_tokens", 0)),
            output_tokens=_safe_int(getattr(usage, "total_output_tokens", 0)),
            reasoning_output_tokens=_safe_int(getattr(usage, "total_thought_tokens", 0)),
        )
    finally:
        for file_name in uploaded_names:
            try:
                client.files.delete(name=file_name)
            except Exception as exc:
                logger.warning("failed to delete temporary Gemini file: %s", redact(str(exc)[:120]))
        with contextlib.suppress(Exception):
            client.close()


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
    text = call_claude(prompt, model=model, timeout_seconds=timeout_seconds)
    return TransportResult(
        text=text,
        input_tokens=_estimate_tokens(prompt),
        output_tokens=_estimate_tokens(text),
    )


def _agent_instructions_root() -> Path:
    override = os.environ.get(AGENT_INSTRUCTIONS_ROOT_ENV_VAR, "").strip()
    if override:
        return Path(override)

    candidates = (Path(__file__).resolve().parents[3] / "agent-instructions",)
    for candidate in candidates:
        if (candidate / "snippets" / "llm_policy.py").is_file():
            return candidate
    raise FileNotFoundError(
        "agent-instructions root not found; set " + AGENT_INSTRUCTIONS_ROOT_ENV_VAR
    )


def _load_llm_policy_module() -> Any:
    module_name = "_angel_memos_llm_policy"
    existing = sys.modules.get(module_name)
    if existing is None:
        path = _agent_instructions_root() / "snippets" / "llm_policy.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load the shared fleet LLM policy")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = loaded
        spec.loader.exec_module(loaded)
        existing = loaded
    return existing


def subscription_backends() -> tuple[str, ...]:
    """Return the fleet subscription CLI order (used by interactive launchers)."""
    policy_mod = _load_llm_policy_module()
    sub_fn = getattr(policy_mod, "subscription_route", None)
    if not callable(sub_fn):
        raise RuntimeError("shared fleet LLM policy has an invalid interface")
    return tuple(
        getattr(backend, "value", str(backend)) for backend in cast("tuple[object, ...]", sub_fn())
    )


def _fleet_provider_order() -> tuple[str, ...]:
    """Resolve fleet provider preference (`gemini > claude`) and subscription chain."""
    policy_mod = _load_llm_policy_module()
    preferred_fn = getattr(policy_mod, "preferred_provider_order", None)
    if not callable(preferred_fn):
        raise RuntimeError("shared fleet LLM policy has an invalid interface")
    preferred = tuple(str(p) for p in cast("tuple[object, ...]", preferred_fn()))
    sub_backends = subscription_backends()
    ordered: list[str] = []
    for provider in (*preferred, *sub_backends):
        if provider in {"gemini", "claude"} and provider not in ordered:
            ordered.append(provider)
    return tuple(ordered) or ("gemini", "claude")


def _backends_for_purpose(purpose: Purpose) -> tuple[str, ...]:
    if purpose in JUDGE_PURPOSES:
        return JUDGE_ROUTE
    return _fleet_provider_order()


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
    cached_tokens = result.cached_input_tokens if result else 0
    output_tokens = result.output_tokens if result else 0
    reasoning_tokens = result.reasoning_output_tokens if result else 0
    prices = _PUBLIC_PRICES_USD_PER_MTOK.get(model, (3.0, 15.0))
    uncached_input = max(0, input_tokens - cached_tokens)
    cached_rate = prices[0] * 0.05 if model.startswith("gemini-4-") else prices[0] * 0.10
    billable_output = output_tokens + reasoning_tokens
    cost = (
        (uncached_input / 1_000_000) * prices[0]
        + (cached_tokens / 1_000_000) * cached_rate
        + (billable_output / 1_000_000) * prices[1]
    )
    return _LedgerEntry(
        run_id=run_id,
        purpose=purpose.value,
        attempt=attempt,
        model=model,
        provider=provider,
        transport=transport,
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_tokens,
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
