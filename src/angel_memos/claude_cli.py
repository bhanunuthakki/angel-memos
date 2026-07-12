"""
claude_cli.py — Reusable wrapper for calling Claude from Python via the user's
Claude Pro/Max subscription (NOT the metered Anthropic API).

Vendored from ~/.gemini/snippets/claude_cli.py and extended with
`extract_structured()` for schema-validated JSON output. Keep `call_claude`
behavior in sync with the canonical so earnings-summary code remains
compatible.

WHY THIS EXISTS
---------------
The `claude_agent_sdk` Python package and the Anthropic SDK both bill against
the metered Anthropic API (`ANTHROPIC_API_KEY`). The ONLY path to use a Claude
Code subscription from a Python script is to invoke the `claude` CLI as a
subprocess.

Three Windows-specific gotchas this wrapper handles:
  1. `subprocess.run(["claude", ...])` fails with FileNotFoundError on Windows
     because the npm-installed binary is `claude.CMD` and Python's subprocess
     doesn't apply PATHEXT to bare names. Fix: resolve the absolute path with
     shutil.which() at first call.
  2. `subprocess.run(input=str, text=True)` defaults to cp1252 on Windows and
     dies on financial/scientific Unicode (U+2212 minus, en/em dashes, arrows).
     Fix: force `encoding="utf-8"` and `errors="replace"`.
  3. Even with subscription auth set up, if `ANTHROPIC_API_KEY` is set in the
     environment, the CLI silently falls back to API billing. Fix: lazy check
     at first call, raise loudly with the unset instructions.

ONE-TIME SETUP (per machine)
----------------------------
1. Install Node.js (https://nodejs.org).
2. Install the CLI:
     npm install -g @anthropic-ai/claude-code
3. Authenticate to your subscription (browser flow):
     claude auth login
4. Verify:
     claude auth status     # should show your subscription account
     claude --version       # confirms binary is on PATH
5. Remove ANTHROPIC_API_KEY from your shell env / .env / shell profile.

USAGE
-----
    from claude_cli import call_claude
    response = call_claude("Summarize this in one sentence: " + text)

Or as a CLI test:
    python claude_cli.py "What is 2+2?"
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from pydantic import BaseModel, TypeAdapter

# Boundary helper: narrows the CLI's JSON envelope (json.loads returns Any)
# into a typed dict so field access doesn't propagate unknowns.
_ENVELOPE_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])

# Override DEFAULT_MODEL per-call if you need higher fidelity (Opus) or speed (Haiku).
DEFAULT_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT_SECONDS = 600

_setup_verified: bool = False
_claude_cli_path: str | None = None


def _verify_setup_once() -> None:
    """Lazy environment check on first call — fails loud rather than mis-billing."""
    global _setup_verified, _claude_cli_path
    if _setup_verified:
        return
    if "ANTHROPIC_API_KEY" in os.environ:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set in the environment. The Claude Code CLI will silently "
            "route calls to API billing instead of your subscription. Unset it before running:\n"
            "  PowerShell:  Remove-Item env:ANTHROPIC_API_KEY\n"
            "  Bash:        unset ANTHROPIC_API_KEY"
        )
    resolved = shutil.which("claude")
    if resolved is None:
        raise RuntimeError(
            "Claude Code CLI ('claude') not found in PATH. Install it with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "Then authenticate with: claude auth login"
        )
    _claude_cli_path = resolved
    _setup_verified = True


def call_claude(
    prompt: str,
    model: str = DEFAULT_MODEL,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """
    Single-shot LLM call via the Claude Code CLI. Returns the model's text response.

    Prompts are passed via stdin to avoid the Windows CreateProcess command-line
    length limit (32K). Output is decoded as UTF-8 with error replacement so bad
    bytes from upstream sources (e.g., PDF extraction) don't crash the call.

    Raises:
      RuntimeError: setup is wrong (CLI not installed, or ANTHROPIC_API_KEY set).
      subprocess.CalledProcessError: CLI returned non-zero exit; stderr in .stderr.
      subprocess.TimeoutExpired: prompt didn't finish within timeout_seconds.
    """
    _verify_setup_once()
    assert _claude_cli_path is not None
    result = subprocess.run(
        [_claude_cli_path, "-p", "--model", model],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=timeout_seconds,
    )
    text = result.stdout.strip()
    if not text:
        raise RuntimeError(f"claude -p returned empty stdout. stderr: {result.stderr.strip()}")
    return text


def extract_structured[T: BaseModel](
    prompt: str,
    model_type: type[T],
    *,
    model: str = OPUS_MODEL,
    system_prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    additional_dirs: list[str] | None = None,
) -> T:
    """Schema-described Claude call: embeds `model_type`'s JSON Schema in
    the prompt, asks Claude to emit conforming JSON, then parses with
    Pydantic.

    Implementation note: we tried `claude -p --json-schema` first, but it
    didn't strictly constrain output for vision-heavy prompts — Claude
    would emit prose explanations alongside (or instead of) JSON, breaking
    `model_validate_json`. Embedding the schema in the prompt and
    extracting the JSON block from Claude's text response is more robust
    in practice; we tolerate code-fence wrapping and leading/trailing
    prose around the JSON.
    """
    _verify_setup_once()
    assert _claude_cli_path is not None

    schema = model_type.model_json_schema()
    schema_block = (
        "Return ONLY the JSON object — no explanation, no markdown code "
        "fences. The JSON must satisfy this schema:\n\n" + json.dumps(schema, indent=2)
    )
    # System prompt goes through stdin (the prompt body) rather than the
    # `--system-prompt` CLI flag. Windows has a ~32K command-line length
    # limit and our style guides (~10KB each) push past it once you add
    # paths and the rest of the args. stdin is unbounded; functionally
    # equivalent priority for instruction-following.
    if system_prompt is not None:
        augmented_prompt = (
            "## SYSTEM INSTRUCTIONS\n\n"
            + system_prompt
            + "\n\n## USER REQUEST\n\n"
            + prompt
            + "\n\n"
            + schema_block
        )
    else:
        augmented_prompt = prompt + "\n\n" + schema_block

    args: list[str] = [
        _claude_cli_path,
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        # Non-interactive batch: permission prompts have no answerer, so
        # tool calls (Read on PDF rasters, WebSearch for founder research)
        # would otherwise stall. Safe here — own machine, own files.
        "--permission-mode",
        "bypassPermissions",
    ]
    # Grant Read-tool access to extra directories (e.g., a temp dir containing
    # rasterized PDF pages). The CLI's @-references load the file content at
    # prompt-parse time, but Claude sometimes falls back to its Read tool —
    # which is sandboxed to the cwd unless explicitly extended.
    if additional_dirs:
        for d in additional_dirs:
            args.extend(["--add-dir", d])

    completed = subprocess.run(
        args,
        input=augmented_prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        # `check=True` would lose stderr in the CalledProcessError repr on
        # Windows — surface it explicitly so callers can see what the CLI
        # actually said (Claude error messages are buried in stderr).
        raise RuntimeError(
            f"claude CLI exited with code {completed.returncode}.\n"
            f"stderr (last 2000 chars):\n{completed.stderr[-2000:]}\n"
            f"stdout (last 500 chars):\n{completed.stdout[-500:]}"
        )
    payload = completed.stdout.strip()
    if not payload:
        raise RuntimeError(f"claude returned empty stdout. stderr: {completed.stderr.strip()}")
    # Persist last raw response for debugging when validation fails downstream.
    debug_path = os.environ.get("ANGEL_MEMOS_DEBUG_RESPONSE")
    if debug_path:
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(payload)
        except OSError:
            pass  # debug aid; don't break the call if disk is full or path is bad

    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise RuntimeError(f"expected JSON object from claude CLI, got: {type(raw).__name__}")
    envelope = _ENVELOPE_ADAPTER.validate_python(raw)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI returned is_error=true: {envelope}")
    result = envelope.get("result")
    if not isinstance(result, str):
        raise RuntimeError(f"expected envelope.result to be a string, got: {type(result).__name__}")
    json_text = _extract_json_block(result)
    return model_type.model_validate_json(json_text)


def _extract_json_block(text: str) -> str:
    """Strip Markdown code fences and surrounding prose from `text`,
    returning the JSON-looking substring.

    Three failure modes this handles, observed empirically:
      - Leading and/or trailing ``` fences (sometimes unmatched).
      - Prose explanation before the JSON ("Here is the JSON: ...").
      - Prose explanation AFTER the JSON ("...} The above shows...").

    For the trailing-prose case we use `json.JSONDecoder.raw_decode` which
    parses the first valid JSON value and reports how many characters it
    consumed — we slice to that length and discard the rest. This is more
    robust than relying on the model to omit trailing prose."""
    stripped = text.strip()
    # Leading code fence: strip "```" through end of first line.
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 :].strip()
        else:
            stripped = stripped[3:].strip()
    # Trailing code fence: strip independently — Claude sometimes emits
    # ``` at the end without an opening fence.
    if stripped.endswith("```"):
        stripped = stripped[:-3].rstrip()
    # Find the first '{' or '[' (Claude may prefix with "Here is the JSON:").
    json_start = -1
    for opener in ("{", "["):
        idx = stripped.find(opener)
        if idx >= 0 and (json_start == -1 or idx < json_start):
            json_start = idx
    if json_start == -1:
        raise RuntimeError(
            f"No JSON object found in Claude response. First 300 chars: {stripped[:300]!r}"
        )
    candidate = stripped[json_start:]
    # Slice off any trailing prose by parsing only the first JSON value.
    try:
        _value, consumed = json.JSONDecoder().raw_decode(candidate)
        return candidate[:consumed]
    except json.JSONDecodeError:
        # The substring isn't valid JSON either — return the unsliced
        # candidate and let the downstream validator surface a richer
        # error message.
        return candidate


if __name__ == "__main__":
    # CLI test entrypoint: `python claude_cli.py "your prompt here"`
    if len(sys.argv) < 2:
        print("Usage: python claude_cli.py <prompt>", file=sys.stderr)
        sys.exit(2)
    prompt = " ".join(sys.argv[1:])
    print(call_claude(prompt))
