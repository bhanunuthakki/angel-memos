"""Decision-review orchestration with the application LLM boundary mocked."""

from pathlib import Path

import pytest

import angel_memos.memo as memo_mod
import angel_memos.review as review_mod
from angel_memos.models import AngelListMetadata, Decision


def test_review_call_sends_the_reviewer_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "angellist - Acme.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "decision.md").write_text("placeholder", encoding="utf-8")
    decision = Decision.model_validate(
        {
            "company": "Acme",
            "verdict": "pass",
            "conviction": "medium",
            "check_usd": 0.0,
            "post_money_usd": 20_000_000.0,
            "valuation_method": "custom",
            "top_reasons": ["a", "b", "c"],
            "top_risks": ["x", "y", "z"],
            "raw_reasoning": "r",
        }
    )
    angellist = AngelListMetadata.model_validate(
        {
            "company": "Acme",
            "round_label": "Seed",
            "stage": "seed",
            "instrument": "SAFE",
            "estimated_round_size_usd": 2_000_000.0,
            "share_class": "SAFE",
            "pre_money_usd": 18_000_000.0,
            "allocation_usd": 100_000.0,
            "estimated_expenses_pct": 0.05,
            "leads_investment_usd": 10_000.0,
            "gross_carry_pct": 0.2,
            "min_investment_usd": 1_000.0,
            "markets": ["Software"],
        }
    )
    calls: list[dict[str, object]] = []

    def fake_call_llm(prompt: str, **kwargs: object) -> str:
        calls.append(kwargs)
        return "## Bottom line\nDefensible."

    def fake_parse_decision(_path: Path) -> Decision:
        return decision

    def fake_load_angellist(_folder: Path, _materials: object) -> AngelListMetadata:
        return angellist

    monkeypatch.setattr(memo_mod, "parse_decision", fake_parse_decision)
    monkeypatch.setattr(memo_mod, "load_or_parse_angellist", fake_load_angellist)
    monkeypatch.setattr(review_mod, "call_llm", fake_call_llm)

    out = review_mod.run_decision_review(tmp_path)

    assert out == tmp_path / "decision_review.md"
    assert out.read_text(encoding="utf-8").strip()
    assert len(calls) == 1
    system_prompt = calls[0].get("system_prompt")
    assert isinstance(system_prompt, str) and system_prompt.strip()
