"""V3.1 feature switches, split by calibration compatibility.

Two classes of intervention, and the split is the whole point of this module.

**Class A - calibration-preserving.** Everything in :class:`V31SafeConfig` runs
strictly inside Module 8 (``cover_kbc.selection``) or later. Module 8 is the
last stage of a query: the pre-M8 hypothesis graph that Module 21 reads is
built with ``prediction=None`` (``pipeline._v3_hypothesis_graph``), so nothing
decided here can reach action eligibility, action execution, action cost, state
transitions, hypothesis construction, M21 input state or M20 budget behaviour.
The only V3 graph that sees a prediction is the post-hoc observation graph
(``pipeline._observe_v3_core``), which is telemetry. That is the formal
compatibility proof, and :mod:`cover_kbc.v3_1.compatibility` restates it in a
form tests can assert against.

**Class B - calibration-shifting.** Everything in :class:`V31AggressiveConfig`
changes recall, prompts or action semantics, so the Audit 0073 M20/M21
calibration derived against the old action-effect distribution no longer
describes the run. These flags never claim compatibility; a config that enables
one is reported as ``CALIBRATION_REVIEW_REQUIRED``.

Every flag defaults to ``False``. With no configuration at all, V3.1 is
inert and the pipeline reproduces frozen V3 behaviour byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


@dataclass(frozen=True)
class V31SafeConfig:
    """Class A interventions: finalization-only, calibration-preserving."""

    #: FINAL_CANDIDATE_RETENTION. When the winning numeric cluster carries no
    #: candidate the acceptance policy accepted, fall through to the best
    #: cluster that does, instead of emitting nothing.
    final_candidate_retention: bool = False
    #: Repair leaked ``<bucket label>: <payload>`` enumeration lines in the
    #: final output, and drop the ones whose payload is an abstention token.
    enumeration_label_repair: bool = False
    #: Stock only: when accepted listings differ in independent acquisition
    #: support, keep the maximally supported ones and drop the rest.
    stock_support_dominance: bool = False
    #: Stock only: refuse structurally impossible listing outputs (the subject
    #: company itself, control tokens, bare tickers).
    stock_structural_validation: bool = False
    #: Canonicalise numeric output representation, converting only when the
    #: model or evidence stated an explicit unit.
    numeric_output_canonicalization: bool = False

    @property
    def any_enabled(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    @property
    def enabled_features(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "V31SafeConfig":
        config = dict(config or {})
        known = {f.name for f in fields(cls)}
        unknown = set(config) - known
        if unknown:
            raise ValueError(
                f"unknown v3_1.safe feature(s): {sorted(unknown)}. "
                f"Known features: {sorted(known)}"
            )
        return cls(**{name: bool(config[name]) for name in config})


@dataclass(frozen=True)
class V31AggressiveConfig:
    """Class B interventions: recall/prompt/action changes that shift calibration."""

    #: Definition-aware hasCapacity enumerator instruction.
    capacity_definition_prompt: bool = False
    #: Contrastive death-attribute instruction for personHasCityOfDeath.
    city_of_death_contrast_prompt: bool = False
    #: Listing-entity (not ticker, not company) emphasis for stock recall.
    stock_listing_entity_prompt: bool = False
    #: Award set-continuation with an evidence-based false-positive cap.
    award_expansion_and_fp_cap: bool = False

    @property
    def any_enabled(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    @property
    def enabled_features(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "V31AggressiveConfig":
        config = dict(config or {})
        known = {f.name for f in fields(cls)}
        unknown = set(config) - known
        if unknown:
            raise ValueError(
                f"unknown v3_1.aggressive feature(s): {sorted(unknown)}. "
                f"Known features: {sorted(known)}"
            )
        return cls(**{name: bool(config[name]) for name in config})


@dataclass(frozen=True)
class V31Config:
    """The V3.1 switch block as a whole."""

    enabled: bool = False
    safe: V31SafeConfig = V31SafeConfig()
    aggressive: V31AggressiveConfig = V31AggressiveConfig()

    @property
    def safe_active(self) -> bool:
        """Class A behaviour is live."""
        return self.enabled and self.safe.any_enabled

    @property
    def aggressive_active(self) -> bool:
        """Class B behaviour is live, so the M21 calibration is stale."""
        return self.enabled and self.aggressive.any_enabled

    @property
    def calibration_status(self) -> str:
        """What a readiness report may claim about M20/M21 calibration."""
        if self.aggressive_active:
            return "CALIBRATION_REVIEW_REQUIRED"
        return "SAFE_WITH_EXISTING_CALIBRATION"

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "V31Config":
        config = dict(config or {})
        safe = V31SafeConfig.from_mapping(config.pop("safe", None))
        aggressive = V31AggressiveConfig.from_mapping(config.pop("aggressive", None))
        enabled = bool(config.pop("enabled", False))
        if config:
            raise ValueError(f"unknown v3_1 key(s): {sorted(config)}")
        return cls(enabled=enabled, safe=safe, aggressive=aggressive)


#: The inert default: V3.1 present in the code, absent from behaviour.
DEFAULT_V31 = V31Config()


__all__ = [
    "DEFAULT_V31",
    "V31AggressiveConfig",
    "V31Config",
    "V31SafeConfig",
]
