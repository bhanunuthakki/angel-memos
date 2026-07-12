"""Per-valuation-method benchmark types. Anchors terminal-metric reach and
exit multiple to a named comparable so scenarios can't drift into vibes."""

import pytest
from pydantic import ValidationError

from angel_memos.models import (
    ArrBenchmark,
    GmvBenchmark,
    RevenueEbitdaBenchmark,
    RevenuePeBenchmark,
    SeedBenchmark,
)


def test_arr_benchmark_round_trip() -> None:
    b = ArrBenchmark(
        rank_label="Top 5",
        comparable="Datadog",
        terminal_arr_usd=2_000_000_000,
        exit_multiple=14.0,
        exit_valuation_usd=28_000_000_000,
    )
    assert b.exit_multiple == 14.0
    assert b.exit_valuation_usd == 28_000_000_000


def test_revenue_ebitda_benchmark_round_trip() -> None:
    b = RevenueEbitdaBenchmark(
        rank_label="Top 5",
        comparable="Generac",
        terminal_revenue_usd=4_000_000_000,
        ebitda_margin=0.22,
        ev_ebitda=18.0,
        exit_valuation_usd=15_840_000_000,
    )
    assert b.ev_ebitda == 18.0


def test_revenue_pe_benchmark_round_trip() -> None:
    b = RevenuePeBenchmark(
        rank_label="Top 10",
        comparable="ExampleCo",
        terminal_revenue_usd=1_000_000_000,
        net_margin=0.18,
        pe_ratio=22.0,
        exit_valuation_usd=3_960_000_000,
    )
    assert b.pe_ratio == 22.0


def test_gmv_benchmark_round_trip() -> None:
    b = GmvBenchmark(
        rank_label="Top 1",
        comparable="Airbnb",
        terminal_gmv_usd=80_000_000_000,
        take_rate=0.12,
        revenue_multiple=8.0,
        exit_valuation_usd=76_800_000_000,
    )
    assert b.take_rate == 0.12


def test_seed_benchmark_round_trip() -> None:
    b = SeedBenchmark(
        rank_label="Top 1",
        comparable="Snowflake",
        exit_valuation_usd=70_000_000_000,
    )
    assert b.comparable == "Snowflake"


def test_seed_benchmark_rejects_extra_fields() -> None:
    """Seed benchmarks intentionally omit metric extrapolation fields."""
    with pytest.raises(ValidationError):
        SeedBenchmark.model_validate(
            {
                "rank_label": "Top 1",
                "comparable": "Snowflake",
                "exit_valuation_usd": 70_000_000_000,
                "terminal_arr_usd": 2_000_000_000,
            }
        )


def test_benchmark_rejects_negative_exit_valuation() -> None:
    with pytest.raises(ValidationError):
        ArrBenchmark(
            rank_label="Top 5",
            comparable="Datadog",
            terminal_arr_usd=2_000_000_000,
            exit_multiple=14.0,
            exit_valuation_usd=-1.0,
        )


def test_benchmark_rejects_empty_comparable() -> None:
    with pytest.raises(ValidationError):
        ArrBenchmark(
            rank_label="Top 5",
            comparable="",
            terminal_arr_usd=2_000_000_000,
            exit_multiple=14.0,
            exit_valuation_usd=28_000_000_000,
        )
