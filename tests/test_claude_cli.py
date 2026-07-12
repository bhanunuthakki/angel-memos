"""Pure-function helpers in `claude_cli`. The actual subprocess calls
(`call_claude`, `extract_structured`) need a live Claude CLI and are
covered by end-to-end runs."""

import pytest

from angel_memos.claude_cli import _extract_json_block


def test_extract_strips_leading_and_trailing_fences() -> None:
    text = '```json\n{"a": 1}\n```'
    assert _extract_json_block(text) == '{"a": 1}'


def test_extract_strips_trailing_fence_without_leading() -> None:
    """Claude occasionally emits ``` only at the end. Without independent
    handling of the trailing fence, this would raise on JSON parse."""
    text = '{"a": 1}\n```'
    assert _extract_json_block(text) == '{"a": 1}'


def test_extract_strips_leading_fence_without_trailing() -> None:
    text = '```\n{"a": 1}'
    assert _extract_json_block(text) == '{"a": 1}'


def test_extract_passes_clean_json_through() -> None:
    text = '{"a": 1}'
    assert _extract_json_block(text) == '{"a": 1}'


def test_extract_strips_leading_prose() -> None:
    text = 'Here is the JSON:\n{"a": 1}'
    assert _extract_json_block(text) == '{"a": 1}'


def test_extract_handles_json_array() -> None:
    text = "[1, 2, 3]"
    assert _extract_json_block(text) == "[1, 2, 3]"


def test_extract_raises_when_no_json_found() -> None:
    with pytest.raises(RuntimeError):
        _extract_json_block("This is just prose, no JSON anywhere.")


def test_extract_slices_trailing_prose_after_json() -> None:
    """When Claude emits valid JSON followed by an explanation, slice the
    explanation off so the validator only sees the JSON object."""
    text = '{"a": 1, "b": [1, 2, 3]}\n\nThe above represents the answer.'
    assert _extract_json_block(text) == '{"a": 1, "b": [1, 2, 3]}'


def test_extract_slices_trailing_prose_from_nested_json() -> None:
    """Nested objects/arrays need balanced-brace matching — raw_decode
    handles this correctly."""
    text = '{"x": {"y": [1, 2]}}\nmore prose'
    assert _extract_json_block(text) == '{"x": {"y": [1, 2]}}'


def test_extract_returns_candidate_when_unparseable() -> None:
    """If the substring after the opener isn't valid JSON, return it
    anyway so the validator can produce a richer error than 'no JSON'."""
    text = "{not actually json"
    result = _extract_json_block(text)
    assert result.startswith("{")
