"""Proposal §17's *"relation-specific historical bins on TRAIN"*.

§17 puts every one of the six utility estimates in a historical bin rather than
in a formula, and this file is that schema. **No TRAIN bins have been built**,
so nothing here contains a number: it is the contract the eventual calibration
must satisfy.

Three rules make the lookup safe.

**Binning is part of the calibration, not of the code.** A boundary such as
"residual below 0.3" is a fitted threshold, so it belongs to the versioned
package alongside the estimates it selects. Categorical state - ProgramType, a
Module 9 risk grade, a facet state, verifier availability, an action family -
needs no boundary and is used directly.

**Lookup is exact.** One state and one action resolve to exactly one bin, or the
package's own declared fallback, or an error. No nearest neighbour, no
embedding, no fuzzy match: a planner that quietly reaches for an approximately
similar bin is making up estimates.

**Missing is never zero.** An action whose estimate is absent is not dropped and
not defaulted - both would bias ``arg max`` toward whichever actions happen to
have the sparsest history. It raises.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.control.planner_types import (
    ActionFamily,
    EstimateSource,
    PlannerError,
    PlannerStateSnapshot,
)

#: Bumped when the bin schema changes shape.
HISTORY_SCHEMA_VERSION = "m21-history-v1"

#: The six §17 estimates. Every bin must carry all of them.
REQUIRED_ESTIMATES = (
    "expected_verified_gain",
    "expected_delta_r",
    "expected_delta_h",
    "expected_cost",
    "expected_redundancy",
    "expected_fp",
)

#: Declared units and ranges. §19 of the brief: no hidden normalisation.
ESTIMATE_UNITS = {
    # Objects that became *verified* under the audited notion of verified gain -
    # never a raw candidate count and never an unverified mention.
    "expected_verified_gain": "verified objects, >= 0, unbounded above",
    # Reduction in Module 19's residual search need. Positive means the action
    # is expected to *reduce* R_t.
    "expected_delta_r": "residual points in [-1, 1]",
    # Reduction in uncertainty under the package's own versioned diagnostic
    # definition. M21 never recomputes an entropy of its own.
    "expected_delta_h": "uncertainty points in [-1, 1]",
    # Historically observed cost. Distinct from Module 20's safe reservation,
    # which alone governs affordability.
    "expected_cost": "physical neural calls, >= 0, unbounded above",
    "expected_redundancy": "redundancy fraction in [0, 1]",
    "expected_fp": "false-positive risk in [0, 1]",
}

_BOUNDED = {
    "expected_delta_r": (-1.0, 1.0),
    "expected_delta_h": (-1.0, 1.0),
    "expected_redundancy": (0.0, 1.0),
    "expected_fp": (0.0, 1.0),
}
_NON_NEGATIVE = ("expected_verified_gain", "expected_cost")


def _check(name: str, value: Any, where: str) -> float:
    if value is None:
        raise PlannerError(
            f"{where}: {name} has no historical estimate. A missing estimate is "
            "not zero - defaulting it would bias arg max toward actions with "
            "the sparsest history"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannerError(f"{where}: {name} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise PlannerError(f"{where}: {name} is not finite")
    if name in _BOUNDED:
        low, high = _BOUNDED[name]
        if not low <= value <= high:
            raise PlannerError(
                f"{where}: {name} is {value}, outside its declared range "
                f"[{low}, {high}] ({ESTIMATE_UNITS[name]})"
            )
    if name in _NON_NEGATIVE and value < 0:
        raise PlannerError(
            f"{where}: {name} is negative ({value}); {ESTIMATE_UNITS[name]}"
        )
    return value


@dataclass(frozen=True)
class SuccessorStat:
    """One recorded successor branch, for depth-2 micro-lookahead.

    A *historical* transition: the package records which state bin followed
    this action and how often. Nothing here is imagined at run time, and no
    model is asked to picture the future.
    """

    probability: float
    successor_state_bin: str

    def __post_init__(self) -> None:
        if not isinstance(self.probability, (int, float)) or isinstance(
            self.probability, bool
        ):
            raise PlannerError("successor probability must be a number")
        if not math.isfinite(self.probability):
            raise PlannerError("successor probability is not finite")
        if not 0.0 <= self.probability <= 1.0:
            raise PlannerError(
                f"successor probability {self.probability} is outside [0, 1]"
            )
        if not self.successor_state_bin:
            raise PlannerError("a successor branch needs a state bin key")

    def to_json(self) -> dict[str, Any]:
        return {"probability": self.probability,
                "successor_state_bin": self.successor_state_bin}


@dataclass(frozen=True)
class HistoricalActionBin:
    """One relation-specific historical bin. All six estimates, always."""

    relation: str
    program_type: str
    state_bin_key: str
    action_family: ActionFamily
    support_count: int
    expected_verified_gain: float
    expected_delta_r: float
    expected_delta_h: float
    expected_cost: float
    expected_redundancy: float
    expected_fp: float
    #: Optional narrowing, e.g. a target or facet class. Empty matches broadly.
    target_class: str = ""
    successors: tuple[SuccessorStat, ...] = ()

    def __post_init__(self) -> None:
        where = f"bin {self.relation}/{self.state_bin_key}/{self.action_family.value}"
        if self.support_count < 0:
            raise PlannerError(f"{where}: support_count is negative")
        for name in REQUIRED_ESTIMATES:
            _check(name, getattr(self, name), where)
        if self.successors:
            total = sum(s.probability for s in self.successors)
            if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise PlannerError(
                    f"{where}: successor probabilities sum to {total}, not 1.0"
                )
            seen = {s.successor_state_bin for s in self.successors}
            if len(seen) != len(self.successors):
                raise PlannerError(f"{where}: a successor state bin is repeated")

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.relation, self.program_type, self.state_bin_key,
                self.action_family.value, self.target_class)

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation, "program_type": self.program_type,
            "state_bin_key": self.state_bin_key,
            "action_family": self.action_family.value,
            "target_class": self.target_class,
            "support_count": self.support_count,
            "expected_verified_gain": self.expected_verified_gain,
            "expected_delta_r": self.expected_delta_r,
            "expected_delta_h": self.expected_delta_h,
            "expected_cost": self.expected_cost,
            "expected_redundancy": self.expected_redundancy,
            "expected_fp": self.expected_fp,
            "successors": [s.to_json() for s in self.successors],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "HistoricalActionBin":
        missing = [name for name in REQUIRED_ESTIMATES if name not in payload]
        if missing:
            raise PlannerError(
                f"historical bin is missing estimate(s) {missing}; every §17 "
                "component must be explicit"
            )
        return cls(
            relation=payload["Relation"], program_type=payload["program_type"],
            state_bin_key=payload["state_bin_key"],
            action_family=ActionFamily(payload["action_family"]),
            target_class=payload.get("target_class", ""),
            support_count=int(payload["support_count"]),
            expected_verified_gain=float(payload["expected_verified_gain"]),
            expected_delta_r=float(payload["expected_delta_r"]),
            expected_delta_h=float(payload["expected_delta_h"]),
            expected_cost=float(payload["expected_cost"]),
            expected_redundancy=float(payload["expected_redundancy"]),
            expected_fp=float(payload["expected_fp"]),
            successors=tuple(
                SuccessorStat(float(s["probability"]), s["successor_state_bin"])
                for s in payload.get("successors", ())
            ),
        )


@dataclass(frozen=True)
class StateBinningSpec:
    """How a state becomes a bin key. Versioned, because it is calibrated.

    ``categorical_features`` are read straight off the state and need no fitted
    boundary. ``numeric_boundaries`` maps a feature to ascending cut points that
    the **TRAIN package** supplies; none is shipped, because a threshold like
    "residual below 0.3" is exactly the kind of fitted number §17 defers.
    """

    spec_version: str
    categorical_features: tuple[str, ...] = ()
    numeric_boundaries: tuple[tuple[str, tuple[float, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not self.spec_version:
            raise PlannerError("a binning spec must be versioned")
        for feature, cuts in self.numeric_boundaries:
            if list(cuts) != sorted(cuts):
                raise PlannerError(
                    f"binning boundaries for {feature!r} are not ascending"
                )
            if len(set(cuts)) != len(cuts):
                raise PlannerError(
                    f"binning boundaries for {feature!r} repeat a cut point")
            for cut in cuts:
                if not math.isfinite(cut):
                    raise PlannerError(
                        f"binning boundary for {feature!r} is not finite")

    def bucket(self, feature: str, value: float | None) -> str:
        """Which bucket a numeric feature falls in. ``None`` is its own bucket."""
        cuts = dict(self.numeric_boundaries).get(feature)
        if cuts is None:
            raise PlannerError(
                f"the binning spec declares no boundaries for numeric feature "
                f"{feature!r}"
            )
        if value is None:
            return f"{feature}=NA"
        index = sum(1 for cut in cuts if value >= cut)
        return f"{feature}=b{index}"

    def to_json(self) -> dict[str, Any]:
        return {
            "spec_version": self.spec_version,
            "categorical_features": list(self.categorical_features),
            "numeric_boundaries": [
                {"feature": f, "cuts": list(c)} for f, c in self.numeric_boundaries
            ],
        }


@dataclass(frozen=True)
class HistoricalBinPackage:
    """A frozen, versioned collection of TRAIN bins.

    Frozen is load-bearing: §42 of the brief forbids online updating, and a
    package that could learn during VAL or TEST would destroy test blindness.
    Nothing here has a mutation path.
    """

    history_version: str
    source: EstimateSource
    binning: StateBinningSpec
    bins: tuple[HistoricalActionBin, ...]
    schema_version: str = HISTORY_SCHEMA_VERSION
    #: Declared by the calibration, not shipped. ``None`` means unspecified.
    minimum_bin_support: int | None = None
    #: A single deterministic fallback the package itself declares. Optional.
    fallback_state_bin: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != HISTORY_SCHEMA_VERSION:
            raise PlannerError(
                f"unsupported history schema {self.schema_version!r}; this build "
                f"implements {HISTORY_SCHEMA_VERSION}"
            )
        if not self.history_version:
            raise PlannerError("a historical package must be versioned")
        seen = set()
        for entry in self.bins:
            if entry.key in seen:
                raise PlannerError(f"duplicate historical bin {entry.key}")
            seen.add(entry.key)
            if (self.minimum_bin_support is not None
                    and entry.support_count < self.minimum_bin_support):
                raise PlannerError(
                    f"bin {entry.key} has support {entry.support_count}, below "
                    f"the package's own declared minimum "
                    f"{self.minimum_bin_support}"
                )

    def lookup(
        self, *, relation: str, program_type: str, state_bin_key: str,
        family: ActionFamily, target_class: str = "",
    ) -> HistoricalActionBin:
        """Resolve exactly one bin. Deterministic, exact, version-aware.

        Specificity is explicit: a bin naming a ``target_class`` beats one that
        does not. Two equally specific matches are an error rather than a
        coin-flip, because which one wins would then depend on ordering.
        """
        matches = [
            entry for entry in self.bins
            if entry.relation == relation
            and entry.program_type == program_type
            and entry.state_bin_key == state_bin_key
            and entry.action_family is family
            and entry.target_class in ("", target_class)
        ]
        if not matches and self.fallback_state_bin:
            matches = [
                entry for entry in self.bins
                if entry.relation == relation
                and entry.program_type == program_type
                and entry.state_bin_key == self.fallback_state_bin
                and entry.action_family is family
                and entry.target_class in ("", target_class)
            ]
        if not matches:
            raise PlannerError(
                f"no historical bin matches {relation}/{program_type}/"
                f"{state_bin_key}/{family.value}"
                + (f"/{target_class}" if target_class else "")
                + "; the action cannot be silently dropped from ranking"
            )
        specific = [entry for entry in matches if entry.target_class]
        chosen = specific or matches
        if len(chosen) > 1:
            raise PlannerError(
                f"{len(chosen)} equally specific historical bins match "
                f"{relation}/{state_bin_key}/{family.value}; the package must "
                "disambiguate rather than let ordering decide"
            )
        return chosen[0]

    def to_json(self) -> dict[str, Any]:
        return {
            "history_version": self.history_version,
            "schema_version": self.schema_version,
            "source": self.source.value,
            "binning": self.binning.to_json(),
            "minimum_bin_support": self.minimum_bin_support,
            "fallback_state_bin": self.fallback_state_bin,
            "bins": [entry.to_json() for entry in self.bins],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any], *,
                  allow_synthetic: bool = False) -> "HistoricalBinPackage":
        source = EstimateSource(payload["source"])
        if not allow_synthetic and not source.is_production:
            raise PlannerError(
                f"historical package source is {source.value}, which is a test "
                "fixture and may not be used as production history"
            )
        binning = payload["binning"]
        return cls(
            history_version=payload["history_version"],
            schema_version=payload.get("schema_version", HISTORY_SCHEMA_VERSION),
            source=source,
            binning=StateBinningSpec(
                spec_version=binning["spec_version"],
                categorical_features=tuple(binning.get("categorical_features", ())),
                numeric_boundaries=tuple(
                    (entry["feature"], tuple(float(c) for c in entry["cuts"]))
                    for entry in binning.get("numeric_boundaries", ())
                ),
            ),
            minimum_bin_support=(
                int(payload["minimum_bin_support"])
                if payload.get("minimum_bin_support") is not None else None
            ),
            fallback_state_bin=payload.get("fallback_state_bin", ""),
            bins=tuple(
                HistoricalActionBin.from_json(entry) for entry in payload["bins"]
            ),
        )


def state_bin_key(state: PlannerStateSnapshot, binning: StateBinningSpec) -> str:
    """Derive the state's bin key under the package's own spec.

    Categorical features are read directly. Numeric features are bucketed using
    boundaries the package supplies - never boundaries invented here.
    """
    parts: list[str] = []
    for feature in binning.categorical_features:
        parts.append(f"{feature}={_categorical(state, feature)}")
    for feature, _ in binning.numeric_boundaries:
        parts.append(binning.bucket(feature, _numeric(state, feature)))
    return "|".join(parts)


def _categorical(state: PlannerStateSnapshot, feature: str) -> str:
    """Read one categorical state feature. No thresholds involved."""
    if feature == "program_type":
        return state.program_type
    if feature == "relation":
        return state.relation
    profile = state.risk_profile
    if profile is not None and hasattr(profile, feature):
        return str(getattr(getattr(profile, feature), "value",
                           getattr(profile, feature)))
    gap = state.coverage_gap
    if gap is not None:
        if feature == "residual_availability":
            return gap.residual.availability.value
        if feature == "null_failed_recall_only":
            null_state = gap.null_state
            return str(bool(null_state and null_state.failed_recall_only))
    layer4 = state.layer4
    if layer4 is not None and feature == "has_candidate":
        return str(bool(layer4.candidates))
    if layer4 is not None and feature == "numeric_cluster_count":
        return str(len(layer4.numeric_targets))
    raise PlannerError(
        f"the binning spec names categorical feature {feature!r}, which the "
        "planner state does not expose"
    )


def _numeric(state: PlannerStateSnapshot, feature: str) -> float | None:
    """Read one numeric state feature. Context for lookup, never a utility term."""
    gap = state.coverage_gap
    if gap is not None:
        if feature == "residual":
            return gap.residual.residual
        if feature == "facet_gap":
            for component in gap.residual.components:
                if component.name.value == "facet_gap":
                    return component.value
            return None
        if feature == "novelty_rate":
            return gap.novelty.novelty_rate
        if feature == "singleton_ratio":
            for component in gap.residual.components:
                if component.name.value == "singleton_ratio":
                    return component.value
            return None
        if feature == "disagreement":
            return gap.disagreement.value
        if feature == "unresolved_mass":
            return gap.unresolved.value
    ledger = state.budget_ledger
    if ledger is not None and feature == "verification_reserve_unused":
        used = dict(ledger.by_class).get("VERIFICATION", 0)
        plan = state.budget_plan
        envelope = plan.envelope("verification") if plan else None
        floor = envelope.protected_floor if envelope else 0
        return float(max(0, floor - used))
    raise PlannerError(
        f"the binning spec names numeric feature {feature!r}, which the planner "
        "state does not expose"
    )


def load_history(payload: Mapping[str, Any], *,
                 allow_synthetic: bool = False) -> HistoricalBinPackage:
    """Read a historical package, refusing fixtures where production is meant."""
    return HistoricalBinPackage.from_json(payload, allow_synthetic=allow_synthetic)


def validate_bins(bins: Sequence[HistoricalActionBin]) -> None:
    """Every §17 component present and in range, for every bin."""
    for entry in bins:
        for name in REQUIRED_ESTIMATES:
            _check(name, getattr(entry, name), f"bin {entry.key}")


__all__ = [
    "ESTIMATE_UNITS",
    "HISTORY_SCHEMA_VERSION",
    "REQUIRED_ESTIMATES",
    "HistoricalActionBin",
    "HistoricalBinPackage",
    "StateBinningSpec",
    "SuccessorStat",
    "load_history",
    "state_bin_key",
    "validate_bins",
]
