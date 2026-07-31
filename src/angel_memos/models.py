"""Pydantic schemas and domain enums for angel-memos.

Values here are part of the on-disk schema (they appear in `decision.md`,
prompt templates, and the exit-math xlsx), so changes are breaking.
"""

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, computed_field, model_validator


class Stage(StrEnum):
    """Investment stage. Extracted from the AngelList memo's `Round` field."""

    PRE_SEED = "pre_seed"
    SEED = "seed"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C = "series_c"
    GROWTH = "growth"


class Verdict(StrEnum):
    """Investment decision recorded in `decision.md`."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    PASS = "pass"


class Conviction(StrEnum):
    """User-stated confidence in the verdict."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValuationMethod(StrEnum):
    """Formula used to derive exit value from terminal metrics.

    `seed_outcome` uses fixed dollar exit values per scenario (no growth-metric
    extrapolation); `custom` skips xlsx generation entirely.
    """

    ARR_MULTIPLE = "arr_multiple"
    REVENUE_EBITDA = "revenue_ebitda"
    REVENUE_PE = "revenue_pe"
    GMV_TAKE = "gmv_take"
    SEED_OUTCOME = "seed_outcome"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Scenarios — one class per valuation method.
#
# Shared fields (name, probability, future_dilution) live on `_ScenarioBase`.
# Each subclass adds the method-specific drivers that feed `exit_value` in the
# corresponding xlsx template.
# ---------------------------------------------------------------------------


class _ScenarioBase(BaseModel):
    """Shared shape across growth-stage scenarios and seed outcomes.

    `name` lives on subclasses (free-form on growth, Literal on seed) to keep
    the type system honest without a covariant override.
    """

    model_config = ConfigDict(extra="forbid")

    probability: float = Field(ge=0.0, le=1.0)
    future_dilution: float = Field(ge=0.0, lt=1.0)


class ArrScenario(_ScenarioBase):
    """`arr_multiple` method: exit value = terminal ARR x exit multiple."""

    name: str = Field(min_length=1)
    cagr: float
    exit_multiple: float = Field(gt=0.0)


class RevenueEbitdaScenario(_ScenarioBase):
    """`revenue_ebitda` method: exit value = terminal revenue x EBITDA margin x EV/EBITDA."""

    name: str = Field(min_length=1)
    revenue_cagr: float
    terminal_ebitda_margin: float = Field(ge=0.0, le=1.0)
    ev_ebitda: float = Field(gt=0.0)


class RevenuePeScenario(_ScenarioBase):
    """`revenue_pe` method: exit value = terminal revenue x net margin x P/E."""

    name: str = Field(min_length=1)
    revenue_cagr: float
    terminal_net_margin: float = Field(ge=0.0, le=1.0)
    pe_ratio: float = Field(gt=0.0)


class GmvScenario(_ScenarioBase):
    """`gmv_take` method: exit value = terminal GMV x take rate x revenue multiple."""

    name: str = Field(min_length=1)
    gmv_cagr: float
    take_rate: float = Field(ge=0.0, le=1.0)
    revenue_multiple: float = Field(gt=0.0)


SeedScenarioName = Literal["zero", "acqui_hire", "modest", "breakout", "generational"]


class SeedScenario(_ScenarioBase):
    """`seed_outcome` method: fixed dollar exit value; no growth-metric extrapolation."""

    name: SeedScenarioName
    exit_value_usd: float = Field(ge=0.0)


# ---------------------------------------------------------------------------
# Benchmarks — one class per valuation method.
#
# Benchmarks anchor the scenario priors to named comparable companies. For
# growth methods they encode both terminal-metric reach AND the exit
# multiple. For seed_outcome only the realized exit valuation is needed.
# ---------------------------------------------------------------------------


class _BenchmarkBase(BaseModel):
    """Shared shape across all benchmark classes."""

    model_config = ConfigDict(extra="forbid")

    rank_label: str = Field(min_length=1)  # e.g. "Top 1", "Top 5", "Top 20"
    comparable: str = Field(min_length=1)
    exit_valuation_usd: float = Field(ge=0.0)


class ArrBenchmark(_BenchmarkBase):
    """Comparable for `arr_multiple` scenarios."""

    terminal_arr_usd: float = Field(ge=0.0)
    exit_multiple: float = Field(gt=0.0)


class RevenueEbitdaBenchmark(_BenchmarkBase):
    """Comparable for `revenue_ebitda` scenarios."""

    terminal_revenue_usd: float = Field(ge=0.0)
    ebitda_margin: float = Field(ge=0.0, le=1.0)
    ev_ebitda: float = Field(gt=0.0)


class RevenuePeBenchmark(_BenchmarkBase):
    """Comparable for `revenue_pe` scenarios."""

    terminal_revenue_usd: float = Field(ge=0.0)
    net_margin: float = Field(ge=0.0, le=1.0)
    pe_ratio: float = Field(gt=0.0)


class GmvBenchmark(_BenchmarkBase):
    """Comparable for `gmv_take` scenarios."""

    terminal_gmv_usd: float = Field(ge=0.0)
    take_rate: float = Field(ge=0.0, le=1.0)
    revenue_multiple: float = Field(gt=0.0)


class SeedBenchmark(_BenchmarkBase):
    """Comparable for `seed_outcome` scenarios — fixed exit value only."""


# ---------------------------------------------------------------------------
# Decision composite — the schema `decision.md` parses into and `memo` consumes.
# ---------------------------------------------------------------------------


type Scenario = ArrScenario | RevenueEbitdaScenario | RevenuePeScenario | GmvScenario | SeedScenario

type Benchmark = (
    ArrBenchmark | RevenueEbitdaBenchmark | RevenuePeBenchmark | GmvBenchmark | SeedBenchmark
)


_SCENARIO_CLASS: dict[ValuationMethod, type[Scenario]] = {
    ValuationMethod.ARR_MULTIPLE: ArrScenario,
    ValuationMethod.REVENUE_EBITDA: RevenueEbitdaScenario,
    ValuationMethod.REVENUE_PE: RevenuePeScenario,
    ValuationMethod.GMV_TAKE: GmvScenario,
    ValuationMethod.SEED_OUTCOME: SeedScenario,
}

_BENCHMARK_CLASS: dict[ValuationMethod, type[Benchmark]] = {
    ValuationMethod.ARR_MULTIPLE: ArrBenchmark,
    ValuationMethod.REVENUE_EBITDA: RevenueEbitdaBenchmark,
    ValuationMethod.REVENUE_PE: RevenuePeBenchmark,
    ValuationMethod.GMV_TAKE: GmvBenchmark,
    ValuationMethod.SEED_OUTCOME: SeedBenchmark,
}

# Methods where the company has no meaningful "current metric" to extrapolate
# (or where we deliberately skip extrapolation to avoid fake precision).
_METHODS_WITHOUT_BASE_METRIC: set[ValuationMethod] = {
    ValuationMethod.SEED_OUTCOME,
    ValuationMethod.CUSTOM,
}

_PROBABILITY_TOLERANCE = 1e-3

# Boundary helpers: `model_validator(mode="before")` receives raw user input
# (typed as `object` since callers may pass anything). These TypeAdapters
# narrow confirmed-shape values into typed containers so the dispatcher can
# index them without untyped variables flowing through the function.
_RAW_DECISION_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_RAW_LIST_ADAPTER: TypeAdapter[list[object]] = TypeAdapter(list[object])


class Decision(BaseModel):
    """User's investment decision plus the scenario priors that justify it.

    Persisted as YAML frontmatter in `decision.md`. Consumed by `memo` to
    generate the private/public memo bodies and populate the exit-math xlsx.
    """

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)
    verdict: Verdict
    conviction: Conviction
    check_usd: float = Field(ge=0.0)
    post_money_usd: float = Field(gt=0.0)
    valuation_method: ValuationMethod
    current_base_metric_usd: float | None = Field(default=None, ge=0.0)
    scenarios: list[Scenario] | None = None
    benchmarks: list[Benchmark] | None = None
    top_reasons: list[str] = Field(min_length=3, max_length=3)
    top_risks: list[str] = Field(min_length=3, max_length=3)
    raw_reasoning: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _dispatch_subtypes(cls, data: object) -> object:
        """Coerce raw `list[dict]` scenarios/benchmarks to the right subclass
        based on `valuation_method`. Without this, Pydantic's union resolution
        is order-dependent and picks whichever class validates first."""
        if not isinstance(data, dict):
            return data
        typed = _RAW_DECISION_ADAPTER.validate_python(data)
        raw_method = typed.get("valuation_method")
        if raw_method is None:
            return typed
        try:
            method = ValuationMethod(raw_method)
        except ValueError:
            return typed  # invalid method — let normal validation surface it
        scenario_cls = _SCENARIO_CLASS.get(method)
        benchmark_cls = _BENCHMARK_CLASS.get(method)
        scenarios = typed.get("scenarios")
        if scenario_cls is not None and isinstance(scenarios, list):
            scenario_items = _RAW_LIST_ADAPTER.validate_python(scenarios)
            typed["scenarios"] = [scenario_cls.model_validate(s) for s in scenario_items]
        benchmarks = typed.get("benchmarks")
        if benchmark_cls is not None and isinstance(benchmarks, list):
            benchmark_items = _RAW_LIST_ADAPTER.validate_python(benchmarks)
            typed["benchmarks"] = [benchmark_cls.model_validate(b) for b in benchmark_items]
        return typed

    @model_validator(mode="after")
    def _validate_method_consistency(self) -> Self:
        """Cross-field rules: exit-math fields present iff committed decision;
        scenario/benchmark types match `valuation_method`; probabilities sum
        to 1.0; current_base_metric_usd present for growth methods."""
        is_commitment = (
            self.verdict != Verdict.PASS and self.valuation_method != ValuationMethod.CUSTOM
        )
        if not is_commitment:
            if self.scenarios is not None or self.benchmarks is not None:
                raise ValueError(
                    f"scenarios and benchmarks must be omitted when verdict={self.verdict.value} "
                    f"or valuation_method={self.valuation_method.value}"
                )
            return self

        if self.scenarios is None:
            raise ValueError(
                f"scenarios required for verdict={self.verdict.value} and "
                f"valuation_method={self.valuation_method.value}"
            )
        if self.benchmarks is None:
            raise ValueError("benchmarks required for committed decisions")

        expected_scenario = _SCENARIO_CLASS[self.valuation_method]
        for s in self.scenarios:
            if not isinstance(s, expected_scenario):
                raise ValueError(
                    f"scenario {s.name!r} is {type(s).__name__}, "
                    f"expected {expected_scenario.__name__} for {self.valuation_method.value}"
                )
        expected_benchmark = _BENCHMARK_CLASS[self.valuation_method]
        for b in self.benchmarks:
            if not isinstance(b, expected_benchmark):
                raise ValueError(
                    f"benchmark {b.comparable!r} is {type(b).__name__}, "
                    f"expected {expected_benchmark.__name__} for {self.valuation_method.value}"
                )

        total = sum(s.probability for s in self.scenarios)
        if abs(total - 1.0) > _PROBABILITY_TOLERANCE:
            raise ValueError(f"scenario probabilities sum to {total}, expected 1.0")

        if (
            self.valuation_method not in _METHODS_WITHOUT_BASE_METRIC
            and self.current_base_metric_usd is None
        ):
            raise ValueError(
                f"current_base_metric_usd required for valuation_method={self.valuation_method.value}"
            )
        return self


# ---------------------------------------------------------------------------
# AngelListMetadata — structured fields parsed from the AngelList memo's
# TERMS table. Narrative sections are handled separately (passed as raw
# text to the memo-generation prompt) and intentionally not modeled here.
# ---------------------------------------------------------------------------


class AngelListMetadata(BaseModel):
    """Deal terms + adjacent context extracted from the AngelList memo PDF.

    `extra="ignore"` (not "forbid") because the model includes a computed
    field (`post_money_usd`) that round-trips through JSON: `model_dump_json`
    emits it, but it shouldn't be accepted as user input — the computed
    property recalculates it. Forbidding extras would break cache reload.
    """

    model_config = ConfigDict(extra="ignore")

    company: str = Field(min_length=1)
    round_label: str = Field(min_length=1)  # raw AL string, e.g. "Series B+"
    stage: Stage
    instrument: str = Field(min_length=1)
    estimated_round_size_usd: float = Field(ge=0.0)
    share_class: str = Field(min_length=1)
    pre_money_usd: float = Field(gt=0.0)
    allocation_usd: float = Field(ge=0.0)  # syndicate allocation, not user's check
    estimated_expenses_pct: float = Field(ge=0.0, le=1.0)  # decimal, not percent
    leads_investment_usd: float = Field(ge=0.0)
    gross_carry_pct: float = Field(ge=0.0, le=1.0)
    min_investment_usd: float = Field(ge=0.0)
    deadline: date | None = None
    markets: list[str] = Field(min_length=1)
    # Founders may be empty for AL-only deals where the team isn't surfaced
    # in the memo body and no deck was supplied. Diligence will flag this
    # as a high-priority gap and (if web search is enabled) attempt to
    # recover names from public sources.
    founders: list[str] = []
    co_investors: list[str] = Field(default_factory=list)
    total_prior_capital_usd: float | None = Field(default=None, ge=0.0)

    @computed_field
    @property
    def post_money_usd(self) -> float:
        return self.pre_money_usd + self.estimated_round_size_usd

    def terms_gaps(self) -> list[str]:
        """Fields whose zero values indicate an incomplete TERMS parse.

        Real AngelList syndicate deals essentially always carry a nonzero
        carry, minimum check, and allocation; a memo whose TERMS table has
        no text layer (image-only capture) parses these as 0.0 and every
        downstream net-return figure silently becomes wrong. One zero can
        be a genuine disclosure quirk, so only two or more zeros in the
        fee-critical set reads as a parse failure. Callers must surface a
        non-empty result as a BLOCKING data gap, not a footnote."""
        zeros = [
            name
            for name, value in (
                ("gross_carry_pct", self.gross_carry_pct),
                ("min_investment_usd", self.min_investment_usd),
                ("allocation_usd", self.allocation_usd),
                ("estimated_round_size_usd", self.estimated_round_size_usd),
            )
            if value == 0
        ]
        return zeros if len(zeros) >= 2 else []


# ---------------------------------------------------------------------------
# DiligenceTopics — Phase A output. Adversarial structure: every section gets
# bull points, bear points, an opinionated verdict, and falsifiable kill
# conditions. Designed for density and signal — one-line bullets, no
# descriptive prose, no "mixed signals" hedging.
# ---------------------------------------------------------------------------


class DiligenceSection(BaseModel):
    """One section of the diligence topics output.

    Adversarial-by-design: bull and bear columns face each other so a
    skimming reader sees the debate at a glance. `verdict` forces an
    opinion — Claude can't return a section that's just descriptive
    summary. `kill_conditions` are the falsifiable things that would
    flip the verdict, providing the concrete diligence list.

    Bullets are constrained to 1-2 lines each by the system prompt; the
    schema can't enforce length precisely but `max_length` on the lists
    caps total real estate per section.
    """

    model_config = ConfigDict(extra="forbid")

    bull_points: list[str] = Field(default_factory=list, max_length=5)
    bear_points: list[str] = Field(default_factory=list, max_length=5)
    verdict: str = Field(min_length=1)  # 1-sentence opinionated call
    kill_conditions: list[str] = Field(default_factory=list, max_length=4)


class ScenarioOutcome(BaseModel):
    """One probability-weighted exit outcome anchoring the scenario fan.

    Diligence-time directional anchor. Decision.md will carry refined
    method-specific scenarios; this is the rough distribution Claude
    builds at the diligence stage so the HTML can render an outcome
    chart that the bull/bear debate references.

    `derivation` is the micro-economics contract: every MoM must be built
    bottom-up (exit revenue x named comp multiple → exit EV ÷ entry
    post-money x dilution retention), not asserted. A scenario whose math
    can't be written in one line is a guess, and guesses inflated E[MoM]
    ~50% on real deals before this field existed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)  # "Zero", "Slow Grind", "Base", "Bull", "Generational"
    probability: float = Field(ge=0.0, le=1.0)
    return_mom: float = Field(ge=0.0)  # gross multiple-on-money at exit (1.0 = break-even)
    key_assumption: str = Field(min_length=1)
    # Bottom-up math, e.g. "$350M rev x 9x (Symbotic 10.3x haircut) = $3.15B
    # EV ÷ $1.66B entry x 0.8 dilution = 1.5x". Required — see class docstring.
    derivation: str = Field(min_length=1)
    exit_value_usd: float | None = Field(default=None, ge=0.0)  # implied exit EV


class ScenarioAnalysisSection(DiligenceSection):
    """Scenario_analysis is the only diligence section that carries a
    numeric outcome distribution. Probabilities must sum to ~1.0
    (±0.05 tolerance — Claude often rounds, accept small drift)."""

    scenarios: list[ScenarioOutcome] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def _validate_probs_sum(self) -> Self:
        total = sum(s.probability for s in self.scenarios)
        if abs(total - 1.0) > 0.05:
            raise ValueError(f"scenario probabilities sum to {total:.3f}, expected ~1.0")
        return self


# ---------------------------------------------------------------------------
# DeckContent — structured extract of the pitch deck. Read once via Claude
# vision into dense text; downstream diligence/memo prompts consume the text
# instead of re-reading the images (which Claude tends to skim when given
# 30+ at once alongside a complex schema).
# ---------------------------------------------------------------------------


class DeckContent(BaseModel):
    """Structured extract of pitch-deck content."""

    model_config = ConfigDict(extra="forbid")

    product_description: str = Field(min_length=1)  # concrete; not "AI company"
    icp: str = Field(min_length=1)  # buyer persona / target customer
    primary_use_case: str = Field(min_length=1)  # the wedge problem
    pricing_model: str  # may be empty if not disclosed
    traction: str  # ARR, users, logos, design partners; empty if undisclosed
    competitors_mentioned: list[str] = []
    market_claims: str  # TAM/SAM/SOM language from the deck
    gtm_motion: str  # PLG / sales-led / partnership / hybrid
    roadmap: str  # near-term product/business plan
    differentiation: str  # moat thesis stated in the deck
    notable_metrics: list[str] = []  # any other quantitative claims worth surfacing


class SupplementarySection(DiligenceSection):
    """An ad-hoc section for deal-specific topics that don't fit the 9
    standard buckets (e.g., 'Regulatory Risk' for a healthtech, 'Token
    Economics' for a crypto deal)."""

    title: str = Field(min_length=1)


class DiligenceTopics(BaseModel):
    """Phase A output. Always covers the 9 standard memo sections; allows
    ad-hoc supplementary sections for deal-specific concerns."""

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)
    top_priorities: list[str] = Field(min_length=1)

    # The 9 standard sections (every memo gets all of these).
    business_overview: DiligenceSection
    strategy: DiligenceSection
    value_chain: DiligenceSection
    competitive_landscape: DiligenceSection
    opportunities_and_risks: DiligenceSection
    team: DiligenceSection
    financials: DiligenceSection
    scenario_analysis: ScenarioAnalysisSection
    verdict_and_open_questions: DiligenceSection

    # Optional ad-hoc additions.
    supplementary: list[SupplementarySection] = []
