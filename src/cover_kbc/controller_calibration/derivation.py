"""Derive Modules 20 and 21 from the collected TRAIN telemetry. **Offline.**

§16 and §17 both end the same way: *"concrete values are calibrated on TRAIN"*.
This module is that calibration. It reads what the frozen system actually did -
one record per considered action, schema ``train-telemetry-v3`` - joins TRAIN
gold to it once, and emits the two artifacts Layer 6 refuses to run without.

Four properties hold throughout, and each is a constraint on the code rather
than a claim about it:

**No model, ever.** Nothing here imports a runtime, and nothing here can. Every
number is an arithmetic function of records already on disk.

**Nothing is fitted by search.** Every constant below is a ratio of two observed
totals, a quantile of an observed distribution, or a count. There is no
objective being maximised, no sweep, no seed - so "re-running the derivation"
cannot produce a better number, only the same one.

**Gold enters as counts and leaves as statistics.** :mod:`gold_join` turns
candidate identities into per-action correct/incorrect counts; this module
aggregates those counts. No object string reaches an artifact, which is what
lets a production run load the result without the benchmark.

**Determinism is structural.** Ordering is explicit everywhere it could matter,
floats are rounded at fixed precision before serialisation, and the artifacts
carry no timestamp. The same inputs produce byte-identical output.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from cover_kbc.control.budget_types import (
    BudgetSpendClass,
    CalibrationSource,
    RelationBudgetCalibration,
    SpecialReservePurpose,
)
from cover_kbc.control.historical_bins import (
    HISTORY_SCHEMA_VERSION,
    HistoricalActionBin,
    HistoricalBinPackage,
    StateBinningSpec,
    SuccessorStat,
)
from cover_kbc.control.planner_types import (
    ActionFamily,
    EstimateSource,
    PlannerCalibration,
)
from cover_kbc.control.relation_budget import relation_policy
from cover_kbc.controller_calibration.gold_join import ActionGoldEffect
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionTelemetryRecord,
    RedundancyStatus,
)

#: Bumped when the derivation *rules* change, independently of the code that
#: runs them. Two packages with the same version and the same inputs are the
#: same package; a different version means the numbers mean something else.
DERIVATION_SCHEMA_VERSION = "train-calibration-v1"
M20_DERIVATION_VERSION = "m20-derivation-v1"
M21_DERIVATION_VERSION = "m21-derivation-v1"
BINNING_SPEC_VERSION = "m21-state-binning-v1"

#: Decimal places every serialised float is rounded to. Fixed, so the artifact
#: is byte-stable across platforms whose repr differs in the last ulp.
FLOAT_PRECISION = 6


class DerivationError(ValueError):
    """The telemetry could not support a proposal-compliant derivation."""


def require_supported_schema(records: Sequence[ActionTelemetryRecord]) -> str:
    """Refuse telemetry this derivation does not know how to read.

    The estimates below depend on fields ``v3`` introduced - measurement
    presence, redundancy status, query-scoped accounting - so a ``v2`` file
    would derive silently wrong numbers rather than fail.

    Raises:
        DerivationError: on an empty file or any unsupported schema version.
    """
    if not records:
        raise DerivationError("telemetry is empty; there is nothing to derive")
    versions = {record.schema_version for record in records}
    if versions != {TELEMETRY_SCHEMA_VERSION}:
        raise DerivationError(
            f"telemetry declares schema {sorted(versions)}; this derivation "
            f"implements {TELEMETRY_SCHEMA_VERSION!r} exactly"
        )
    return TELEMETRY_SCHEMA_VERSION


def _round(value: float) -> float:
    """Canonical rounding. ``-0.0`` is normalised so two runs cannot differ."""
    out = round(float(value), FLOAT_PRECISION)
    return 0.0 if out == 0.0 else out


def _finite(value: float, what: str) -> float:
    if not math.isfinite(value):
        raise DerivationError(f"{what} is not finite ({value!r})")
    return value


def _quantile(values: Sequence[float], q: float) -> float:
    """Nearest-rank quantile over a sorted copy. No interpolation, no library.

    Nearest-rank because a budget ceiling must be a value the system actually
    reached, not an average of two it did not.
    """
    if not values:
        raise DerivationError("cannot take a quantile of no observations")
    ordered = sorted(values)
    if q <= 0:
        return float(ordered[0])
    if q >= 1:
        return float(ordered[-1])
    rank = math.ceil(q * len(ordered))
    return float(ordered[min(len(ordered) - 1, max(0, rank - 1))])


def _mean(values: Sequence[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivationSettings:
    """Every deterministic choice the derivation makes, in one place.

    Recorded verbatim in the provenance block, because a reader who wants to
    know why ``hard_calls`` is what it is needs the quantile as much as the
    telemetry.
    """

    #: Quantile used for a ceiling. High rather than maximum: a ceiling set at
    #: the single most expensive query observed is a ceiling that never binds.
    budget_quantile: float = 0.95
    #: A bin is emitted only with at least this much support; everything below
    #: rolls up into the relation's fallback bin instead of shipping a mean of
    #: one observation as though it were an estimate.
    minimum_bin_support: int = 8
    #: Quantile cut points for the numeric state features. Three buckets.
    state_quantiles: tuple[float, ...] = (1 / 3, 2 / 3)
    #: Numeric state features the bin key is built from. Both are readable by
    #: the production planner's own ``_numeric`` and by telemetry, which is what
    #: makes an offline-derived key match a runtime-computed one.
    state_numeric_features: tuple[str, ...] = ("residual", "unresolved_mass")
    #: Categorical state features, same requirement.
    state_categorical_features: tuple[str, ...] = ("program_type",)
    #: Smallest total movement of ``ΔR`` or ``ΔH`` that can support a rate.
    #:
    #: ``β`` and ``γ`` convert an observable into verified objects by dividing
    #: a gain total by a movement total, so a movement total near zero turns a
    #: handful of objects into an arbitrarily large production coefficient
    #: (Audit 0048 P1-3). The floor is **one full unit of the observable**:
    #: both are clamped to ``[-1, 1]`` per action, so 1.0 is exactly one action
    #: moving the quantity across its entire range. Below that the collection
    #: observed less than one unit of movement in total, and a rate *per unit*
    #: would be extrapolation rather than measurement.
    #:
    #: It is a property of the observable's scale, not of any score: nothing
    #: about TRAIN F1 informs it, and raising it can only make the derivation
    #: refuse more often.
    minimum_denominator: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 < self.budget_quantile <= 1.0:
            raise DerivationError(
                f"budget_quantile {self.budget_quantile} is not in (0, 1]")
        if self.minimum_bin_support < 1:
            raise DerivationError("minimum_bin_support must be at least 1")
        if list(self.state_quantiles) != sorted(self.state_quantiles):
            raise DerivationError("state_quantiles must be ascending")
        if not math.isfinite(self.minimum_denominator) or (
                self.minimum_denominator <= 0):
            raise DerivationError(
                f"minimum_denominator must be a positive finite number, got "
                f"{self.minimum_denominator!r}")

    def to_json(self) -> dict[str, Any]:
        return {
            "budget_quantile": self.budget_quantile,
            "minimum_bin_support": self.minimum_bin_support,
            "state_quantiles": list(self.state_quantiles),
            "state_numeric_features": list(self.state_numeric_features),
            "state_categorical_features": list(self.state_categorical_features),
            "minimum_denominator": self.minimum_denominator,
            "float_precision": FLOAT_PRECISION,
        }


#: The declared fallback bin key. A state that matches no derived bucket, and
#: any bucket too sparse to ship, resolves here rather than raising - which is
#: what stops a legal action being silently dropped from the ranking.
FALLBACK_STATE_BIN = "__fallback__"


# --------------------------------------------------------------------------
# State binning — the offline half of a two-sided contract
# --------------------------------------------------------------------------

#: Telemetry field backing each numeric state feature the spec may name. The
#: production side reads the same quantities off ``PlannerStateSnapshot`` via
#: ``historical_bins._numeric``; this is the same feature, read from the record
#: that observed it. ``test_offline_and_runtime_bin_keys_agree`` pins the two
#: together, because a mismatch would make every derived bin unreachable.
_TELEMETRY_NUMERIC = {
    "residual": lambda s: s.residual,
    "novelty_rate": lambda s: s.novelty_rate,
    "singleton_ratio": lambda s: s.singleton_ratio,
    "facet_gap": lambda s: s.facet_gap,
    "disagreement": lambda s: s.disagreement,
    "unresolved_mass": lambda s: s.unresolved_mass,
}


def telemetry_numeric_feature(state: Any, feature: str) -> float | None:
    """One numeric state feature, read from a telemetry control state.

    A component Module 19 could not measure reads ``None`` - the same "not
    available" bucket the runtime spec gives it - rather than 0.0, because §15
    is explicit that unavailable is not zero and a bin keyed on a fake zero
    would collect observations that never happened.
    """
    try:
        reader = _TELEMETRY_NUMERIC[feature]
    except KeyError:
        raise DerivationError(
            f"no telemetry backing for numeric state feature {feature!r}; the "
            f"known features are {sorted(_TELEMETRY_NUMERIC)}"
        ) from None
    if not state.measured:
        return None
    # ``residual`` is the scalar Module 19 always publishes when it ran; the
    # five components each carry their own availability.
    if feature != "residual" and feature not in state.available_components:
        return None
    return float(reader(state))


def offline_state_bin_key(
    state: Any, *, program_type: str, relation: str, binning: StateBinningSpec,
) -> str:
    """The bin key for a telemetry state, in the runtime's exact format.

    Deliberately built from the **same** :class:`StateBinningSpec` the package
    ships and the runtime reads, and in the same order - categorical features
    first, then numeric buckets, joined by ``|``. Only the *source* of the
    feature values differs: here a recorded ``ControlStateFeatures``, at
    inference a live ``PlannerStateSnapshot``.
    """
    parts: list[str] = []
    for feature in binning.categorical_features:
        if feature == "program_type":
            parts.append(f"{feature}={program_type}")
        elif feature == "relation":
            parts.append(f"{feature}={relation}")
        else:
            raise DerivationError(
                f"no telemetry backing for categorical state feature {feature!r}"
            )
    for feature, _cuts in binning.numeric_boundaries:
        parts.append(
            binning.bucket(feature, telemetry_numeric_feature(state, feature)))
    return "|".join(parts)


def derive_binning_spec(
    records: Sequence[ActionTelemetryRecord], settings: DerivationSettings,
) -> StateBinningSpec:
    """Cut points from the observed pre-state distribution. Nothing invented.

    §17 defers a threshold like *"residual below 0.3"* to TRAIN precisely
    because it is a fitted number. These are the fitted numbers: quantiles of
    what the collection actually saw, rounded so they serialise identically
    every time.
    """
    boundaries: list[tuple[str, tuple[float, ...]]] = []
    for feature in settings.state_numeric_features:
        observed = [
            value for record in records
            if (value := telemetry_numeric_feature(record.pre_state, feature))
            is not None
        ]
        if not observed:
            # No observation of this feature at all: ship no cut points, so it
            # contributes a single bucket rather than a fabricated split.
            boundaries.append((feature, ()))
            continue
        cuts: list[float] = []
        for q in settings.state_quantiles:
            cut = _round(_quantile(observed, q))
            if cut not in cuts:
                cuts.append(cut)
        boundaries.append((feature, tuple(sorted(cuts))))
    return StateBinningSpec(
        spec_version=BINNING_SPEC_VERSION,
        categorical_features=tuple(settings.state_categorical_features),
        numeric_boundaries=tuple(boundaries),
    )


# --------------------------------------------------------------------------
# Module 20
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationSpend:
    """Observed per-relation spend, before any policy judgement is applied."""

    relation: str
    queries: int
    per_query_total_calls: tuple[int, ...]
    per_query_generated_tokens: tuple[int, ...]
    per_query_prompt_tokens: tuple[int, ...]
    per_query_by_class: Mapping[str, tuple[int, ...]]
    per_query_by_purpose: Mapping[str, tuple[int, ...]]
    executed_actions: int
    useful_actions: int
    verified_gain_total: float

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation, "queries": self.queries,
            "executed_actions": self.executed_actions,
            "useful_actions": self.useful_actions,
            "verified_gain_total": _round(self.verified_gain_total),
            "median_total_calls": _round(_quantile(
                self.per_query_total_calls, 0.5)) if self.queries else 0.0,
            "p95_total_calls": _round(_quantile(
                self.per_query_total_calls, 0.95)) if self.queries else 0.0,
            "by_class_p95": {
                name: _round(_quantile(values, 0.95)) if values else 0.0
                for name, values in sorted(self.per_query_by_class.items())
            },
            "by_purpose_p95": {
                name: _round(_quantile(values, 0.95)) if values else 0.0
                for name, values in sorted(self.per_query_by_purpose.items())
            },
        }


def observe_relation_spend(
    records: Sequence[ActionTelemetryRecord],
    effects: Mapping[str, ActionGoldEffect],
) -> dict[str, RelationSpend]:
    """Aggregate the telemetry into per-relation, per-query spend. No policy.

    Whole-query totals come from the control state's **query-scoped** counters
    (``calls_used`` at the last executed action of a query), which include the
    acquisition phase. Class and purpose totals come from the Layer-4 actions
    themselves, because those are the only actions Module 20's ledger ever
    meters. Keeping the two apart is what stops a per-class cap being derived
    from spend the class never made.
    """
    per_query: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        if not record.executed:
            continue
        key = (record.relation, record.row_index)
        slot = per_query.setdefault(key, {
            "relation": record.relation, "last_round": -1,
            "total_calls": 0, "generated": 0, "prompt": 0,
            "by_class": {}, "by_purpose": {}, "actions": 0,
            "useful": 0, "gain": 0.0,
        })
        if record.post_state is not None and record.round_index > slot["last_round"]:
            slot["last_round"] = record.round_index
            slot["total_calls"] = record.post_state.calls_used
            slot["generated"] = record.post_state.generated_tokens
            slot["prompt"] = record.post_state.prompt_tokens
        calls = record.outcome.physical_calls
        slot["actions"] += 1
        if record.spend_class:
            slot["by_class"][record.spend_class] = (
                slot["by_class"].get(record.spend_class, 0) + calls)
        if record.reserve_purpose:
            slot["by_purpose"][record.reserve_purpose] = (
                slot["by_purpose"].get(record.reserve_purpose, 0) + calls)
        effect = effects.get(record.operation_id)
        if effect is not None and effect.verified_gain > 0:
            slot["useful"] += 1
            slot["gain"] += effect.verified_gain

    grouped: dict[str, list[dict[str, Any]]] = {}
    for (relation, _row), slot in sorted(per_query.items()):
        grouped.setdefault(relation, []).append(slot)

    out: dict[str, RelationSpend] = {}
    for relation, slots in sorted(grouped.items()):
        classes = sorted({name for s in slots for name in s["by_class"]})
        purposes = sorted({name for s in slots for name in s["by_purpose"]})
        out[relation] = RelationSpend(
            relation=relation, queries=len(slots),
            per_query_total_calls=tuple(s["total_calls"] for s in slots),
            per_query_generated_tokens=tuple(s["generated"] for s in slots),
            per_query_prompt_tokens=tuple(s["prompt"] for s in slots),
            per_query_by_class={
                name: tuple(s["by_class"].get(name, 0) for s in slots)
                for name in classes
            },
            per_query_by_purpose={
                name: tuple(s["by_purpose"].get(name, 0) for s in slots)
                for name in purposes
            },
            executed_actions=sum(s["actions"] for s in slots),
            useful_actions=sum(s["useful"] for s in slots),
            verified_gain_total=sum(s["gain"] for s in slots),
        )
    return out


def derive_m20(
    spend: Mapping[str, RelationSpend], settings: DerivationSettings, *,
    calibration_version: str = M20_DERIVATION_VERSION,
) -> dict[str, RelationBudgetCalibration]:
    """Table 6's qualitative policy, given the numbers TRAIN supplies.

    The tiers are **not** re-decided here. ``relation_policy`` already declares,
    per relation, whether discovery is low/medium/high, whether it is capped,
    whether verification is hard-reserved and which special purposes exist;
    §16 says only the concrete values are calibrated. So each number below is
    an observed quantile, and the qualitative structure constrains how it is
    used:

    * ``hard_calls`` is the whole-query ceiling at the settings' quantile.
    * ``discovery_cap`` / ``verification_cap`` come from that class's own
      observed Layer-4 spend - the only spend the ledger meters. A relation
      whose policy caps discovery keeps the observed ceiling; one that does not
      is still bounded by ``hard_calls``.
    * ``verification_reserve`` protects what §9.3 calls a hard reservation. It
      is the observed spend on protected-purpose verification actions, and for
      a relation Table 6 marks *hard-reserved* it is not allowed to fall below
      that relation's observed verification median - which is exactly the
      "awards verification reserve is unused" failure §17.1 describes.
    * ``special_reserves`` are the observed spend per declared purpose, and
      only for purposes the relation's policy actually declares.

    Raises:
        DerivationError: if a relation has no observed query, or if the
            resulting numbers cannot satisfy the scheduler's own invariants.
    """
    out: dict[str, RelationBudgetCalibration] = {}
    q = settings.budget_quantile
    for relation, observed in sorted(spend.items()):
        policy = relation_policy(relation)          # raises on an unknown relation
        if observed.queries == 0:
            raise DerivationError(
                f"{relation}: no query was observed, so no budget can be derived")

        hard_calls = int(math.ceil(_quantile(observed.per_query_total_calls, q)))
        hard_tokens = int(math.ceil(
            _quantile(observed.per_query_generated_tokens, q)))

        def class_cap(name: BudgetSpendClass) -> int:
            values = observed.per_query_by_class.get(name.value)
            return int(math.ceil(_quantile(values, q))) if values else 0

        discovery_cap = min(hard_calls, class_cap(BudgetSpendClass.DISCOVERY))
        verification_cap = min(hard_calls, class_cap(BudgetSpendClass.VERIFICATION))

        declared = tuple(policy.special_reserve_purposes)
        reserves: list[tuple[SpecialReservePurpose, int]] = []
        for purpose in declared:
            values = observed.per_query_by_purpose.get(purpose.value)
            size = int(math.ceil(_quantile(values, q))) if values else 0
            if size:
                reserves.append((purpose, size))

        verification_values = observed.per_query_by_class.get(
            BudgetSpendClass.VERIFICATION.value, ())
        reserve = 0
        if policy.verification_hard_reserved and verification_values:
            # §9.3: discovery may never consume this. Table 6 marks awards
            # "hard-reserved high", and the observed median is the floor that
            # keeps the shortlist verifiable however much discovery wants.
            reserve = int(math.floor(_quantile(verification_values, 0.5)))
        reserve = min(reserve, verification_cap)

        # The scheduler's own invariant: protected floors must fit under the
        # ceiling. Shrink the *optional* special reserves first, never the
        # hard verification reservation §9.3 protects.
        protected = reserve + sum(size for _, size in reserves)
        while protected > hard_calls and reserves:
            purpose, size = reserves.pop()
            protected -= size
        if protected > hard_calls:
            raise DerivationError(
                f"{relation}: verification reserve {reserve} alone exceeds the "
                f"derived hard ceiling {hard_calls}"
            )

        out[relation] = RelationBudgetCalibration(
            relation=relation,
            calibration_version=calibration_version,
            calibration_source=CalibrationSource.TRAIN_CALIBRATED,
            hard_calls=hard_calls,
            hard_generated_tokens=hard_tokens,
            discovery_cap=discovery_cap,
            verification_cap=verification_cap,
            verification_reserve=reserve,
            special_reserves=tuple(reserves),
        )
    return out


# --------------------------------------------------------------------------
# Module 21
# --------------------------------------------------------------------------


@dataclass
class _BinAccumulator:
    """Running totals for one (relation, program_type, state bin, family)."""

    relation: str
    program_type: str
    state_bin_key: str
    family: ActionFamily
    support: int = 0
    verified_gain: list[float] = field(default_factory=list)
    delta_r: list[float] = field(default_factory=list)
    delta_h: list[float] = field(default_factory=list)
    cost: list[float] = field(default_factory=list)
    redundancy: list[float] = field(default_factory=list)
    false_positive: list[float] = field(default_factory=list)
    successors: dict[str, int] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.relation, self.program_type, self.state_bin_key,
                self.family.value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def derive_m21(
    records: Sequence[ActionTelemetryRecord],
    effects: Mapping[str, ActionGoldEffect],
    binning: StateBinningSpec,
    settings: DerivationSettings, *,
    history_version: str = M21_DERIVATION_VERSION,
) -> tuple[HistoricalBinPackage, dict[str, Any]]:
    """Historical bins for §17's six estimates, plus successor frequencies.

    Every executed action contributes to exactly two accumulators: its own
    state bin, and its relation's fallback bin. A bin that ends below the
    settings' minimum support is not shipped - its observations are already in
    the fallback, so dropping it loses nothing and shipping it would present a
    mean of two observations as an estimate.

    Returns:
        The package, and a diagnostics mapping recording what was aggregated,
        what was dropped for sparsity, and the ΔH finding.
    """
    exact: dict[tuple[str, str, str, str], _BinAccumulator] = {}
    fallback: dict[tuple[str, str, str, str], _BinAccumulator] = {}
    #: (row, relation) -> [(round, bin key, program type, family)], the
    #: observed action chain a successor frequency is counted from.
    chains: dict[tuple[int, str], list[tuple[int, str, str, str]]] = {}
    delta_h_values: list[float] = []
    redundancy_by_relation: dict[str, list[float]] = {}
    all_redundancy: list[float] = []

    executed = [r for r in records if r.executed]
    if not executed:
        raise DerivationError(
            "no executed action in the telemetry; §17 has nothing to estimate")

    for record in executed:
        if record.post_state is None:                  # schema forbids, belt+braces
            raise DerivationError(
                f"{record.operation_id}: executed without a post-state")
        if record.outcome.redundancy_status is RedundancyStatus.MEASURED:
            value = _finite(float(record.outcome.redundancy or 0.0), "redundancy")
            redundancy_by_relation.setdefault(record.relation, []).append(value)
            all_redundancy.append(value)

    for record in executed:
        try:
            family = ActionFamily(record.action_family)
        except ValueError:
            raise DerivationError(
                f"{record.operation_id}: action_family "
                f"{record.action_family!r} is not a canonical ActionFamily"
            ) from None
        state_key = offline_state_bin_key(
            record.pre_state, program_type=record.program_type,
            relation=record.relation, binning=binning)
        effect = effects.get(record.operation_id)
        if effect is None:
            raise DerivationError(
                f"{record.operation_id}: executed but carries no gold-joined "
                "effect; verified gain and false-positive risk are underivable"
            )

        delta_r = _finite(record.delta_residual or 0.0, "delta_residual")
        delta_h = _finite(record.delta_entropy or 0.0, "delta_entropy")
        delta_h_values.append(delta_h)

        for table, key in (
            (exact, (record.relation, record.program_type, state_key,
                     family.value)),
            (fallback, (record.relation, record.program_type,
                        FALLBACK_STATE_BIN, family.value)),
        ):
            acc = table.get(key)
            if acc is None:
                acc = _BinAccumulator(
                    relation=record.relation, program_type=record.program_type,
                    state_bin_key=key[2], family=family)
                table[key] = acc
            acc.support += 1
            acc.verified_gain.append(effect.verified_gain)
            acc.delta_r.append(_clamp(delta_r, -1.0, 1.0))
            acc.delta_h.append(_clamp(delta_h, -1.0, 1.0))
            acc.cost.append(float(record.outcome.physical_calls))
            acc.false_positive.append(
                _clamp(effect.false_positive_rate, 0.0, 1.0))
            if record.outcome.redundancy_status is RedundancyStatus.MEASURED:
                acc.redundancy.append(
                    _clamp(float(record.outcome.redundancy or 0.0), 0.0, 1.0))

        chains.setdefault((record.row_index, record.relation), []).append(
            (record.round_index, state_key, record.program_type,
             family.value))

    # Successor frequencies: which state bin followed this one, within a query.
    # The chain already carries everything the attribution needs, so the pairs
    # are read straight off it rather than re-scanning the records.
    successor_counts: dict[tuple[str, str, str, str], dict[str, int]] = {}
    transitions = 0
    for (_row_index, relation), steps in sorted(chains.items()):
        steps.sort()
        for (_round_a, key_a, program_type, family_value), step_b in zip(
                steps, steps[1:]):
            key_b = step_b[1]
            transitions += 1
            for table_key in (
                (relation, program_type, key_a, family_value),
                (relation, program_type, FALLBACK_STATE_BIN, family_value),
            ):
                counts = successor_counts.setdefault(table_key, {})
                counts[key_b] = counts.get(key_b, 0) + 1

    def redundancy_for(acc: _BinAccumulator) -> float:
        """Documented fallback hierarchy: bin -> relation -> run -> 0.0.

        A bin with no ``MEASURED`` redundancy has no observation of its own, and
        §17 still needs a number. Falling back to the relation and then the run
        keeps the estimate an observation rather than a default; 0.0 is reached
        only when the whole collection measured redundancy nowhere, which the
        sufficiency gate already refuses.
        """
        if acc.redundancy:
            return _mean(acc.redundancy)
        relation_values = redundancy_by_relation.get(acc.relation)
        if relation_values:
            return _mean(relation_values)
        if all_redundancy:
            return _mean(all_redundancy)
        return 0.0

    def build(acc: _BinAccumulator) -> HistoricalActionBin:
        counts = successor_counts.get(acc.key, {})
        total = sum(counts.values())
        successors = tuple(
            SuccessorStat(
                probability=_round(count / total),
                successor_state_bin=key,
            )
            for key, count in sorted(counts.items())
        ) if total else ()
        # Rounding can move the sum off 1.0; the package refuses that, so the
        # largest branch absorbs the residue deterministically.
        if successors:
            drift = 1.0 - sum(s.probability for s in successors)
            if abs(drift) > 0:
                ordered = sorted(
                    range(len(successors)),
                    key=lambda i: (-successors[i].probability,
                                   successors[i].successor_state_bin))
                index = ordered[0]
                fixed = list(successors)
                fixed[index] = SuccessorStat(
                    probability=_round(fixed[index].probability + drift),
                    successor_state_bin=fixed[index].successor_state_bin)
                successors = tuple(fixed)
        return HistoricalActionBin(
            relation=acc.relation, program_type=acc.program_type,
            state_bin_key=acc.state_bin_key, action_family=acc.family,
            target_class="",
            support_count=acc.support,
            expected_verified_gain=_round(max(0.0, _mean(acc.verified_gain))),
            expected_delta_r=_round(_clamp(_mean(acc.delta_r), -1.0, 1.0)),
            expected_delta_h=_round(_clamp(_mean(acc.delta_h), -1.0, 1.0)),
            expected_cost=_round(max(0.0, _mean(acc.cost))),
            expected_redundancy=_round(_clamp(redundancy_for(acc), 0.0, 1.0)),
            expected_fp=_round(_clamp(_mean(acc.false_positive), 0.0, 1.0)),
            successors=successors,
        )

    kept = [acc for acc in exact.values()
            if acc.support >= settings.minimum_bin_support]
    dropped = [acc for acc in exact.values()
               if acc.support < settings.minimum_bin_support]
    bins = [build(acc) for acc in sorted(kept, key=lambda a: a.key)]
    bins += [build(acc) for acc in sorted(fallback.values(), key=lambda a: a.key)]

    package = HistoricalBinPackage(
        history_version=history_version,
        schema_version=HISTORY_SCHEMA_VERSION,
        source=EstimateSource.TRAIN_CALIBRATED,
        binning=binning,
        bins=tuple(bins),
        minimum_bin_support=None,   # the fallback bins are intentionally dense
        fallback_state_bin=FALLBACK_STATE_BIN,
    )

    non_zero_delta_h = [v for v in delta_h_values if v != 0.0]
    diagnostics = {
        "executed_actions": len(executed),
        "exact_bins_kept": len(kept),
        "exact_bins_dropped_for_sparsity": len(dropped),
        "dropped_bin_keys": sorted(
            f"{a.relation}|{a.state_bin_key}|{a.family.value}" for a in dropped),
        "fallback_bins": len(fallback),
        "observed_transitions": transitions,
        "minimum_bin_support": settings.minimum_bin_support,
        # C-02, recorded as a measurement rather than asserted as a belief.
        "delta_h_observations": len(delta_h_values),
        "delta_h_non_zero": len(non_zero_delta_h),
        "delta_h_is_structurally_zero": not non_zero_delta_h,
        "redundancy_measured_observations": len(all_redundancy),
    }
    return package, diagnostics


# --------------------------------------------------------------------------
# §17 coefficients
# --------------------------------------------------------------------------


def derive_planner_calibration(
    package: HistoricalBinPackage,
    records: Sequence[ActionTelemetryRecord],
    effects: Mapping[str, ActionGoldEffect], *,
    settings: DerivationSettings | None = None,
    calibration_version: str = M21_DERIVATION_VERSION,
) -> tuple[PlannerCalibration, dict[str, Any]]:
    """§17's seven coefficients, each a ratio of two observed totals.

    The utility is put in one unit - **expected verified objects** - so the six
    terms can be added at all. Everything follows from that choice, and nothing
    is searched:

    ``alpha = 1``
        The numeraire. One verified object is worth one verified object.
    ``kappa = 1``
        A false positive costs what a true positive earns. That is the
        evaluator's own margin: F1 penalises a wrong prediction and rewards a
        right one symmetrically at the boundary, and κ is where the proposal
        puts that trade-off.
    ``beta``
        Verified objects observed per unit of residual reduction. If the
        collection reduced ``R`` by 40 in total while producing 20 verified
        objects, a unit of ``ΔR`` is worth 0.5 objects.
    ``gamma``
        The same ratio for ``ΔH`` - and **0.0 when ΔH never moved**, which is
        the honest representation of C-02 rather than a coefficient that
        multiplies nothing.
    ``delta``
        Verified objects per physical call: the opportunity cost of spending a
        call, priced from what a call historically returned.
    ``eta``
        The mean verified gain of an action, so a fully redundant action
        (``redundancy = 1``) loses exactly the gain an average action brings and
        nets out at zero.
    ``tau_continue = 0``
        With every term in objects, ``U > 0`` means "expected to add more
        correct objects than it costs". §17's rule is strict ``>``, so an action
        that breaks even stops - which is the behaviour the strictness is for.

    ``β`` and ``γ`` divide by an *observed total movement*, which is the one
    place a ratio can explode: a handful of verified objects over a movement
    total of ``1e-6`` is an arbitrarily large production coefficient derived
    from rounding noise (Audit 0048 P1-3). Three cases are kept apart, and none
    of them hides the problem behind an epsilon or a clamp:

    * **denominator exactly 0** - the term genuinely never moved. The
      coefficient is 0.0 and the term is inert, which is truthful and is what
      C-02 requires of ``γ`` under the current action space.
    * **denominator below** :attr:`DerivationSettings.minimum_denominator`
      **with a non-zero numerator** - the ratio is unstable and the derivation
      **refuses**. Shipping it would put an unbounded number into production
      policy; suppressing it would invent one.
    * **denominator below the floor with a zero numerator** - the ratio is
      0/small, which is 0.0 and stable. No gain was observed, and that is a
      real observation.

    Raises:
        DerivationError: when a movement total is positive but too small to
            support a rate and the numerator is non-zero.
    """
    settings = settings or DerivationSettings()
    executed = [r for r in records if r.executed]
    gain_total = sum(effects[r.operation_id].verified_gain
                     for r in executed if r.operation_id in effects)
    delta_r_total = sum(max(0.0, r.delta_residual or 0.0) for r in executed)
    delta_h_total = sum(max(0.0, r.delta_entropy or 0.0) for r in executed)
    call_total = sum(r.outcome.physical_calls for r in executed)

    floor = settings.minimum_denominator

    def rate(numerator: float, denominator: float, name: str,
             observable: str) -> float:
        if denominator == 0.0:
            return 0.0
        if denominator < floor:
            if numerator == 0.0:
                return 0.0
            raise DerivationError(
                f"{name}: {observable} moved by only {denominator!r} in total "
                f"across the whole collection, below the minimum meaningful "
                f"denominator {floor!r}, yet {numerator!r} verified object(s) "
                f"were observed. The ratio would be {numerator / denominator:.1f} "
                "and is noise, not a rate; refusing rather than shipping an "
                "unbounded coefficient into production policy"
            )
        return numerator / denominator

    beta = rate(gain_total, delta_r_total, "beta", "the residual")
    gamma = rate(gain_total, delta_h_total, "gamma", "H")
    # These two denominators are counts, not movements: a physical call and an
    # executed action are whole units with a natural floor of one, so they
    # cannot be small-but-positive the way an accumulated float can.
    delta = (gain_total / call_total) if call_total > 0 else 0.0
    eta = (gain_total / len(executed)) if executed else 0.0

    calibration = PlannerCalibration(
        calibration_version=calibration_version,
        source=EstimateSource.TRAIN_CALIBRATED,
        alpha=1.0,
        beta=_round(beta),
        gamma=_round(gamma),
        delta=_round(delta),
        eta=_round(eta),
        kappa=1.0,
        tau_continue=0.0,
        lookahead_depth=2 if any(b.successors for b in package.bins) else 1,
    )
    diagnostics = {
        "verified_gain_total": _round(gain_total),
        "delta_r_reduction_total": _round(delta_r_total),
        "delta_h_reduction_total": _round(delta_h_total),
        "physical_calls_total": int(call_total),
        "executed_actions": len(executed),
        "minimum_denominator": floor,
        "gamma_is_inert_because_delta_h_never_moved": delta_h_total == 0.0,
        "beta_denominator_supported": delta_r_total >= floor,
        "gamma_denominator_supported": delta_h_total >= floor,
        "beta_estimable": delta_r_total > 0,
        "delta_estimable": call_total > 0,
        "lookahead_depth": calibration.lookahead_depth,
    }
    return calibration, diagnostics


# --------------------------------------------------------------------------
# Provenance and the bundle
# --------------------------------------------------------------------------


#: Directories whose contents are *derivation source*. A stray file under
#: ``outputs/`` says nothing about which code ran; a modified file under
#: ``src/`` says everything. Only these are allowed to block a derivation.
SOURCE_PREFIXES = ("src/", "scripts/", "tests/", "configs/", "benchmark/")

#: Files that must be tracked by the commit the derivation claims as its
#: source. If the implementation itself is untracked, ``derivation_repo_sha``
#: names a commit that does not contain the code that produced the artifact.
DERIVATION_SOURCE_FILES = (
    "scripts/derive_train_calibration.py",
    "src/cover_kbc/controller_calibration/derivation.py",
    "src/cover_kbc/controller_calibration/gold_join.py",
)

#: A 40-character lowercase hex commit id, and nothing else. ``unknown``, an
#: empty string and a ref name are all refused.
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class DirtyDerivationSource(DerivationError):
    """The checkout executing the derivation is not the commit it names."""


def _git(repo_root: Any, *args: str) -> str:
    """Run git against the repository, never against the caller's cwd."""
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        raise DirtyDerivationSource(
            "git is not available, so the derivation source cannot be "
            "identified") from None
    except subprocess.CalledProcessError as error:
        raise DirtyDerivationSource(
            f"git {' '.join(args)} failed in {repo_root}: "
            f"{error.stderr.strip() or error}") from None


def resolve_derivation_source(
    repo_root: Any = None, *,
    required_files: Sequence[str] = DERIVATION_SOURCE_FILES,
) -> str:
    """The commit this derivation is executing from. **Fails closed.**

    ``derivation_repo_sha`` is a claim about *which code produced the numbers*.
    HEAD alone cannot support that claim: a dirty checkout runs modified source
    while HEAD still names a clean commit, so the artifact would record a
    provenance that is precisely, silently wrong (Audit 0048 P1-1). The only
    way the claim can be true is if the working tree matches the commit.

    Refused, in order:

    * ``git`` unavailable, or HEAD unresolvable;
    * a HEAD that is not a 40-character commit id;
    * **any** staged or unstaged modification to a source path;
    * **any** untracked file under a source path;
    * a derivation implementation file the commit does not contain.

    A detached HEAD is fine - it is an exact commit checkout, which is the
    property being asserted. Ignored files never block: they are not source.
    There is deliberately **no override**. A production calibration derived
    from unidentifiable code is not a calibration, and an escape hatch is how
    that happens at 2 a.m.

    Raises:
        DirtyDerivationSource: with the offending paths named.
    """
    if repo_root is None:
        from cover_kbc.paths import REPO_ROOT

        repo_root = REPO_ROOT

    head = _git(repo_root, "rev-parse", "HEAD").strip()
    if not _COMMIT_SHA.match(head):
        raise DirtyDerivationSource(
            f"HEAD resolved to {head!r}, which is not a commit id; the "
            "derivation source cannot be recorded"
        )

    dirty: list[str] = []
    untracked: list[str] = []
    for line in _git(repo_root, "status", "--porcelain").splitlines():
        if not line.strip():
            continue
        status, _, path = line[:2], line[2:3], line[3:]
        # A rename reports "old -> new"; the new path is what matters.
        path = path.split(" -> ")[-1].strip().strip('"')
        if not any(path.startswith(prefix) for prefix in SOURCE_PREFIXES):
            continue
        (untracked if status == "??" else dirty).append(f"{status.strip()} {path}")

    if dirty or untracked:
        detail = "; ".join(sorted(dirty) + sorted(untracked))
        raise DirtyDerivationSource(
            f"the derivation source at {repo_root} is not a clean checkout of "
            f"{head}: {detail}. A production calibration must be derived from a "
            "committed, exact source state, so that its derivation_repo_sha "
            "names the code that actually produced it"
        )

    missing = [
        path for path in required_files
        if _git(repo_root, "ls-tree", "-r", "--name-only", head, "--", path).strip()
        != path
    ]
    if missing:
        raise DirtyDerivationSource(
            f"commit {head} does not contain {missing}; the derivation "
            "implementation must be part of the commit it records as its source"
        )
    return head


@dataclass(frozen=True)
class CalibrationProvenance:
    """What binds these numbers to one collection, and to nothing else.

    Every field is a hash or a version. Together they answer "may this artifact
    be used with this code, against this benchmark, from this run?" - and a
    later production milestone can answer it **without** the TRAIN file, which
    is the point of writing them down.
    """

    collection_repo_sha: str
    derivation_repo_sha: str
    train_sha256: str
    train_rows: int
    predictions_sha256: str
    telemetry_sha256: str
    manifest_sha256: str
    experiment_config_sha256: str
    evaluator_sha256: str
    telemetry_schema_version: str
    derivation_schema_version: str
    m20_derivation_version: str
    m21_derivation_version: str
    binning_spec_version: str
    relation_catalogue: tuple[str, ...]
    collection_policy_version: str
    settings: DerivationSettings
    support_counts: Mapping[str, int]

    def to_json(self) -> dict[str, Any]:
        return {
            "collection_repo_sha": self.collection_repo_sha,
            "derivation_repo_sha": self.derivation_repo_sha,
            "train_sha256": self.train_sha256,
            "train_rows": self.train_rows,
            "predictions_sha256": self.predictions_sha256,
            "telemetry_sha256": self.telemetry_sha256,
            "manifest_sha256": self.manifest_sha256,
            "experiment_config_sha256": self.experiment_config_sha256,
            "evaluator_sha256": self.evaluator_sha256,
            "telemetry_schema_version": self.telemetry_schema_version,
            "derivation_schema_version": self.derivation_schema_version,
            "m20_derivation_version": self.m20_derivation_version,
            "m21_derivation_version": self.m21_derivation_version,
            "binning_spec_version": self.binning_spec_version,
            "relation_catalogue": list(self.relation_catalogue),
            "collection_policy_version": self.collection_policy_version,
            "derivation_settings": self.settings.to_json(),
            "support_counts": dict(sorted(self.support_counts.items())),
        }


#: Field names that must never appear anywhere in a production artifact. Gold
#: objects and raw prompts are the obvious ones; ``SubjectEntity`` is here
#: because a bin keyed on a subject would be a memorised answer table wearing a
#: statistics costume.
FORBIDDEN_ARTIFACT_KEYS = (
    "ObjectEntities", "SubjectEntity", "gold", "aliases", "prompt",
    "raw_output", "candidates_added", "candidates_supported",
    "candidates_contradicted", "candidates_named", "candidates_touched",
)


def assert_no_leakage(payload: Any, *, where: str = "artifact") -> None:
    """Refuse an artifact that carries anything inference must not receive.

    Structural, not advisory: the derivation runs this over everything it is
    about to write, so a future field that happens to be named ``gold`` cannot
    reach production by being overlooked in review.

    Raises:
        DerivationError: on the first forbidden key found.
    """
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_ARTIFACT_KEYS:
                raise DerivationError(
                    f"{where}: field {key!r} may not appear in a production "
                    "calibration artifact"
                )
            assert_no_leakage(value, where=f"{where}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            assert_no_leakage(value, where=f"{where}[{index}]")
    elif isinstance(payload, float):
        _finite(payload, where)


@dataclass(frozen=True)
class CalibrationBundle:
    """The three production artifacts plus the provenance that binds them."""

    provenance: CalibrationProvenance
    budgets: Mapping[str, RelationBudgetCalibration]
    history: HistoricalBinPackage
    planner: PlannerCalibration

    def m20_json(self) -> dict[str, Any]:
        payload = {
            "artifact": "m20-relation-budget",
            # What these numbers are *about*, stated in the artifact rather
            # than only in a report nobody will still have. Module 20's ledger
            # meters precharged Layer-4 actions, so the class caps describe
            # Layer-4 spend; ``hard_calls`` is the whole-query ceiling, which
            # is why it is much larger than the two caps under it.
            "scope": {
                "hard_calls": "whole query, including the acquisition phase",
                "hard_generated_tokens": "whole query",
                "discovery_cap": "Layer-4 DISCOVERY actions only - the spend "
                                 "Module 20's ledger meters",
                "verification_cap": "Layer-4 VERIFICATION actions only",
                "verification_reserve": "§9.3 protected floor inside "
                                        "verification_cap",
                "special_reserves": "observed spend per Table 6 purpose the "
                                    "relation's policy declares",
            },
            "provenance": self.provenance.to_json(),
            "relations": [
                self.budgets[name].to_json() for name in sorted(self.budgets)
            ],
        }
        assert_no_leakage(payload, where="m20")
        return payload

    def m21_history_json(self) -> dict[str, Any]:
        payload = dict(self.history.to_json())
        payload["artifact"] = "m21-historical-bins"
        payload["provenance"] = self.provenance.to_json()
        assert_no_leakage(payload, where="m21-history")
        return payload

    def m21_planner_json(self) -> dict[str, Any]:
        payload = dict(self.planner.to_json())
        payload["artifact"] = "m21-planner-calibration"
        payload["provenance"] = self.provenance.to_json()
        assert_no_leakage(payload, where="m21-planner")
        return payload


__all__ = [
    "BINNING_SPEC_VERSION",
    "DERIVATION_SCHEMA_VERSION",
    "FALLBACK_STATE_BIN",
    "FORBIDDEN_ARTIFACT_KEYS",
    "M20_DERIVATION_VERSION",
    "M21_DERIVATION_VERSION",
    "DERIVATION_SOURCE_FILES",
    "SOURCE_PREFIXES",
    "CalibrationBundle",
    "CalibrationProvenance",
    "DerivationError",
    "DirtyDerivationSource",
    "DerivationSettings",
    "RelationSpend",
    "require_supported_schema",
    "assert_no_leakage",
    "derive_binning_spec",
    "derive_m20",
    "derive_m21",
    "derive_planner_calibration",
    "observe_relation_spend",
    "offline_state_bin_key",
    "resolve_derivation_source",
    "telemetry_numeric_feature",
]
