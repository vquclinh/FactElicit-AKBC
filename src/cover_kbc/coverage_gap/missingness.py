"""Module 19 - the Coverage Gap and Missingness Estimator.

Architecture position::

    M16 -> (M17, M18) -> Layer-4 evidence state
                              |
                              v
                    M19 coverage-gap estimator     <- here, 0 neural calls
                              |
                              v
                      future M20 / M21

§15's ensemble, implemented exactly::

    R_t = w1*noveltyRate_t + w2*singletonRatio_t + w3*facetGap_t
        + w4*disagreement_t + w5*unresolvedMass_t

**`R_t` is a residual search-need heuristic, not a probability.** §15 opens by
rejecting Chao2-as-cardinality-oracle, and nothing here estimates a set size:
incidence statistics appear as heuristics and nothing more.

**Module 6 is untouched.** RCSE remains the production estimator with its own
``q_res``; M19 is the upgraded shadow estimator beside it, and this module
imports nothing from it.

**M19 decides nothing.** No stop, no continue, no next action, no recommended
check, no budget. `R_t = 0` stops nothing. Weak and unexplored facets are
reported as *state*; choosing one to act on is Module 20/21's.

Two hygiene rules run through the whole file, both inherited:

* **Unavailable is never zero.** Every component carries its availability, and
  `R_t` renormalises over the available ones rather than reading a missing
  signal as a measured absence of gap.
* **Failure is not evidence.** A failed recall is not exhaustion, an absence is
  not contradiction, and an alternate object recovered for a set-valued
  relation is not disagreement with the target (Audit 0027 §20A).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.coverage_gap.facet_coverage import (
    FacetExecution,
    build_facet_map,
    facet_gap,
)
from cover_kbc.coverage_gap.gap_types import (
    ESTIMATOR_VERSION,
    UNIFORM_WEIGHT_SOURCE,
    CoverageGapComponents,
    CoverageGapError,
    CoverageGapState,
    DisagreementChannel,
    DisagreementDiagnostics,
    IncidenceDiagnostics,
    NoveltyDiagnostics,
    NoveltyObservation,
    NullCompetingStateDiagnostics,
    NumericStabilityDiagnostics,
    ResidualComponent,
    ResidualComponentName,
    SignalAvailability,
    UnresolvedMassDiagnostics,
    UnresolvedReason,
    UnresolvedUnit,
    ratio,
)

#: Independence groups that are **verification**, never discovery. A verifier
#: template, a label order, a control, a reverse check and a counterfactual
#: check are all anchored or shown-candidate mechanisms: none of them is an
#: independent sighting of a candidate, so none may enter incidence.
_NON_DISCOVERY_GROUP_PREFIXES = ("m17:", "M18_REVERSE", "M18_COUNTERFACTUAL",
                                 "M18_KEY_CONDITION", "core:BLIND_VERIFIER",
                                 "core:EXISTENCE_GATE")


@dataclass(frozen=True)
class CoverageGapConfig:
    """Module 19 configuration.

    §15 names ``w1..w5`` and supplies no values, and no other section does
    either. Fitting them would require TRAIN or VAL, which architecture
    construction may not read, so the neutral uniform vector is used and
    recorded as ``uniform_unfitted``. There is no threshold, no window length
    and no per-relation weight.
    """

    enabled: bool = False
    mode: str = "shadow"
    estimator_version: str = ESTIMATOR_VERSION
    novelty_rate: float = 1.0
    singleton_ratio: float = 1.0
    facet_gap: float = 1.0
    disagreement: float = 1.0
    unresolved_mass: float = 1.0

    SUPPORTED_MODES = frozenset({"shadow"})

    @property
    def weights(self) -> dict[ResidualComponentName, float]:
        return {
            ResidualComponentName.NOVELTY_RATE: self.novelty_rate,
            ResidualComponentName.SINGLETON_RATIO: self.singleton_ratio,
            ResidualComponentName.FACET_GAP: self.facet_gap,
            ResidualComponentName.DISAGREEMENT: self.disagreement,
            ResidualComponentName.UNRESOLVED_MASS: self.unresolved_mass,
        }

    @property
    def weight_source(self) -> str:
        return (
            UNIFORM_WEIGHT_SOURCE
            if len(set(self.weights.values())) == 1 else "configured"
        )

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "CoverageGapConfig":
        payload = dict(config or {})
        known = {"enabled", "mode", "estimator_version", "weights"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"unknown coverage_gap key(s) {unknown}; expected {sorted(known)}"
            )
        version = str(payload.get("estimator_version", ESTIMATOR_VERSION))
        if version != ESTIMATOR_VERSION:
            raise ValueError(
                f"unsupported estimator_version {version!r}; this build "
                f"implements {ESTIMATOR_VERSION!r}"
            )
        mode = str(payload.get("mode", "shadow"))
        if mode not in cls.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported coverage_gap mode {mode!r}; this milestone "
                f"implements {sorted(cls.SUPPORTED_MODES)} only"
            )

        weights = payload.get("weights") or {}
        if not isinstance(weights, Mapping):
            raise ValueError("coverage_gap.weights must be a mapping")
        names = {name.value for name in ResidualComponentName}
        unknown_weights = sorted(set(weights) - names)
        if unknown_weights:
            raise ValueError(
                f"unknown coverage_gap.weights key(s) {unknown_weights}; §15 "
                f"defines {sorted(names)}"
            )
        values = {name: float(weights.get(name, 1.0)) for name in names}
        for name, value in sorted(values.items()):
            if value < 0:
                raise ValueError(
                    f"coverage_gap.weights.{name} is negative ({value}); a "
                    "residual term cannot reduce the residual"
                )
        if sum(values.values()) <= 0:
            raise ValueError(
                "coverage_gap.weights sum to zero; R_t would have no weight mass"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=mode,
            estimator_version=version,
            **values,
        )


# --------------------------------------------------------------------------
# Incidence and singleton ratio
# --------------------------------------------------------------------------


def is_discovery_group(group_key: str) -> bool:
    """Whether one group is an independent *sighting* rather than a check."""
    return not any(group_key.startswith(p) for p in _NON_DISCOVERY_GROUP_PREFIXES)


def discovery_groups(overlay: Any) -> tuple[str, ...]:
    """The discovery groups that produced one candidate.

    Module 16's own supporting groups, plus any Layer-4 structural group that
    Audit 0027 already classified as *recall* - which is the candidate-free
    probe and nothing else. Verification groups are filtered out by name.
    """
    groups = {g for g in overlay.base_group_supports if is_discovery_group(g)}
    groups.update(
        g.group_key for g in overlay.structural_groups if g.supports and g.is_recall
    )
    return tuple(sorted(groups))


def incidence_diagnostics(state: Any) -> IncidenceDiagnostics:
    """Candidate x discovery-group incidence. Heuristics, never a cardinality.

    A candidate Module 3 already marks impossible is excluded: it is not a
    sighting of anything.
    """
    incidence: dict[str, tuple[str, ...]] = {}
    excluded: list[str] = []
    for overlay in state.candidates:
        if overlay.hard_contract_violation:
            excluded.append(overlay.candidate_key)
            continue
        incidence[overlay.candidate_key] = discovery_groups(overlay)

    supported = {k: v for k, v in incidence.items() if v}
    return IncidenceDiagnostics(
        candidate_count=len(incidence),
        supported_candidate_count=len(supported),
        singleton_count=sum(1 for v in supported.values() if len(v) == 1),
        doubleton_count=sum(1 for v in supported.values() if len(v) == 2),
        discovery_group_count=len({g for v in supported.values() for g in v}),
        incidence=dict(sorted(incidence.items())),
        excluded_candidates=tuple(sorted(excluded)),
    )


def singleton_ratio(
    diagnostics: IncidenceDiagnostics,
) -> tuple[float | None, str]:
    """§12's equation, over **groups** rather than events::

        singletonRatio_t = #candidates with exactly one discovery group
                         / #candidates with at least one

    Ten samples of one mechanism are still one group, so a heavily resampled
    candidate is still a singleton. An empty pool is unavailable, never
    "perfectly covered".
    """
    if diagnostics.supported_candidate_count == 0:
        return None, "no candidate has an eligible discovery group"
    return ratio(
        diagnostics.singleton_count, diagnostics.supported_candidate_count
    ), ""


# --------------------------------------------------------------------------
# Novelty
# --------------------------------------------------------------------------


def novelty_diagnostics(
    origins: Sequence[tuple[str, tuple[str, ...]]]
) -> NoveltyDiagnostics:
    """§13's novelty, over discovery-capable operations in execution order.

    For each origin, ``novelty = #strict-new identities / #usable identities``
    it emitted, where "new" means unseen by any earlier origin. The current
    ``noveltyRate_t`` is the **most recent** origin's, which needs no window
    length - and the whole history is kept so a later consumer can smooth it
    however it wishes.

    Ordering is the deterministic execution order the caller supplies. Wall
    clock is never consulted.
    """
    seen: set[str] = set()
    history: list[NoveltyObservation] = []
    for index, (operation_id, identities) in enumerate(origins):
        usable = [key for key in identities if key]
        novel = [key for key in usable if key not in seen]
        seen.update(usable)
        history.append(NoveltyObservation(
            operation_id=operation_id, order_index=index,
            emitted=len(usable), novel=len(novel),
            novelty=(len(novel) / len(usable)) if usable else None,
        ))

    measurable = [h for h in history if h.novelty is not None]
    if not measurable:
        return NoveltyDiagnostics(
            history=tuple(history),
            availability=SignalAvailability.UNAVAILABLE,
            reason=(
                "no discovery-capable operation has produced a usable identity; "
                "a novelty rate would be fabricated"
            ),
        )
    latest = measurable[-1]
    return NoveltyDiagnostics(
        history=tuple(history),
        novelty_rate=latest.novelty,
        # Descriptive only, and never a sixth term in R_t.
        saturation=1.0 - latest.novelty,
        latest_operation_id=latest.operation_id,
        availability=SignalAvailability.AVAILABLE,
    )


# --------------------------------------------------------------------------
# Disagreement
# --------------------------------------------------------------------------


def disagreement_diagnostics(state: Any) -> DisagreementDiagnostics:
    """Every bounded channel, kept named, reduced by ``max``.

    §15 names ``disagreement_t`` but prescribes no aggregator, so the minimal
    non-tuned conservative rule is used: the maximum of the channels that are
    **already** audited into [0, 1]. Summing them would exceed the range and
    weighting them would be a fitted blend.

    Channels never merge: Module 16's semantic ``D``, Module 17's template
    disagreement and Module 17's label-order disagreement stay individually
    readable, exactly as Layer 4 keeps them.
    """
    channels: list[DisagreementChannel] = []
    raw: dict[str, Any] = {}

    for overlay in state.candidates:
        key = overlay.candidate_key
        if overlay.base_d:
            channels.append(DisagreementChannel(
                name=f"m16_semantic_d:{key}", value=overlay.base_d, source="M16",
            ))
        verifier = overlay.specialist_verifier
        if verifier.template_disagreement is not None:
            channels.append(DisagreementChannel(
                name=f"m17_template:{key}", value=verifier.template_disagreement,
                source="M17",
            ))
        if verifier.label_order_disagreement is not None:
            channels.append(DisagreementChannel(
                name=f"m17_label_order:{key}",
                value=verifier.label_order_disagreement, source="M17",
            ))
        if verifier.contradicts:
            channels.append(DisagreementChannel(
                name=f"m17_contradiction:{key}", value=1.0, source="M17",
            ))
        if overlay.structural_contradicting_groups:
            channels.append(DisagreementChannel(
                name=f"m18_structural_contradiction:{key}", value=1.0, source="M18",
            ))
        # Audit 0027 §20A: an alternate object recovered for a set-valued
        # relation is NOT disagreement with the target. It is recorded raw and
        # deliberately kept out of the scalar.
        alternates = [
            c.recovered_value for c in overlay.structural_checks
            if c.outcome.value == "ALTERNATE_RECOVERED"
        ]
        if alternates:
            raw.setdefault("alternate_recoveries", {})[key] = alternates
        if verifier.max_valid_shift is not None:
            raw.setdefault("m17_max_valid_shift", {})[key] = verifier.max_valid_shift

    for target in state.numeric_targets:
        if target.competing_clusters:
            channels.append(DisagreementChannel(
                name=f"m12_competing_clusters:{target.cluster_index}", value=1.0,
                source="M12",
            ))
        if target.specialist_verifier.template_disagreement is not None:
            channels.append(DisagreementChannel(
                name=f"m17_template:cluster{target.cluster_index}",
                value=target.specialist_verifier.template_disagreement, source="M17",
            ))

    null_state = state.null_state
    if null_state is not None:
        if null_state.competing_candidates > 1:
            channels.append(DisagreementChannel(
                name="m14_competing_localities", value=1.0, source="M14",
            ))
        if null_state.living_support and null_state.no_known_locality_support:
            channels.append(DisagreementChannel(
                name="m14_null_class_conflict", value=1.0, source="M14",
            ))
        raw["m14_failed_recall_operations"] = null_state.failed_recall_operations

    if not channels:
        return DisagreementDiagnostics(
            raw_diagnostics=raw, availability=SignalAvailability.UNAVAILABLE,
        )
    return DisagreementDiagnostics(
        channels=tuple(sorted(channels, key=lambda c: c.name)),
        raw_diagnostics=raw,
        value=max(c.value for c in channels),
        availability=SignalAvailability.AVAILABLE,
    )


# --------------------------------------------------------------------------
# Unresolved mass
# --------------------------------------------------------------------------

_UNRESOLVED_VERIFIER = {
    "NOT_REQUESTED": UnresolvedReason.VERIFIER_NOT_REQUESTED,
    "UNAVAILABLE": UnresolvedReason.VERIFIER_UNAVAILABLE,
}


def _candidate_unit(overlay: Any, pending_targets: set[str]) -> UnresolvedUnit:
    """Whether one candidate's state is resolved, from explicit audited state."""
    reasons: list[UnresolvedReason] = []
    verifier = overlay.specialist_verifier
    availability = verifier.availability.value
    if availability in _UNRESOLVED_VERIFIER:
        reasons.append(_UNRESOLVED_VERIFIER[availability])
    elif verifier.argmax_label == "UNKNOWN":
        # Measured, and the verifier could not establish either answer. That is
        # unresolvedness, not contradiction.
        reasons.append(UnresolvedReason.VERIFIER_UNKNOWN)
    if verifier.contradicts:
        reasons.append(UnresolvedReason.VERIFIER_CONTRADICTION)
    if overlay.structural_contradicting_groups:
        reasons.append(UnresolvedReason.STRUCTURAL_CONTRADICTION)
    if overlay.candidate_key in pending_targets or overlay.display in pending_targets:
        reasons.append(UnresolvedReason.PENDING_CHECK)
    return UnresolvedUnit(
        unit_id=overlay.candidate_key, kind="candidate",
        unresolved=bool(reasons), reasons=tuple(reasons),
    )


def unresolved_mass(state: Any, program_type: str) -> UnresolvedMassDiagnostics:
    """§17's measure over ProgramType-specific target units::

        unresolvedMass_t = #unresolved units / #applicable represented units

    A unit is unresolved only for an explicit reason: the verifier was not
    requested, was unavailable or answered UNKNOWN; a structural or verifier
    contradiction stands; a requested check is still pending; or the state is
    competing. A single support event does not make a unit resolved, and the
    mere absence of Module 18 does not make it unresolved.

    Hard-contract-impossible units are excluded from the denominator - Module 3
    already settled them.
    """
    pending_targets = {
        p.candidate for p in state.pending_checks
        if p.status.value in ("ELIGIBLE_NOT_SCHEDULED", "FAILED")
    }
    units: list[UnresolvedUnit] = []
    excluded: list[str] = []

    if program_type == "NUMERIC":
        competing = any(t.competing_clusters for t in state.numeric_targets)
        for target in state.numeric_targets:
            reasons: list[UnresolvedReason] = []
            availability = target.specialist_verifier.availability.value
            if availability in _UNRESOLVED_VERIFIER:
                reasons.append(_UNRESOLVED_VERIFIER[availability])
            elif target.specialist_verifier.argmax_label == "UNKNOWN":
                reasons.append(UnresolvedReason.VERIFIER_UNKNOWN)
            if competing:
                reasons.append(UnresolvedReason.COMPETING_STATE)
            if any(c.contradicts for c in target.structural_checks):
                reasons.append(UnresolvedReason.STRUCTURAL_CONTRADICTION)
            units.append(UnresolvedUnit(
                unit_id=f"cluster:{target.cluster_index}", kind="numeric_cluster",
                unresolved=bool(reasons), reasons=tuple(reasons),
            ))
    else:
        for overlay in state.candidates:
            if overlay.hard_contract_violation:
                excluded.append(overlay.candidate_key)
                continue
            units.append(_candidate_unit(overlay, pending_targets))

    if program_type == "NULL_SINGLE":
        # The query-level existence state is its own unit, and stays a
        # proposition: it never becomes an entity candidate.
        null_state = state.null_state
        reasons = []
        if null_state is None:
            reasons.append(UnresolvedReason.VERIFIER_NOT_REQUESTED)
        else:
            if null_state.failed_recall_only:
                # Audit 0024: repetition of ignorance is a coverage gap, never
                # evidence that the gold is empty.
                reasons.append(UnresolvedReason.FAILED_RECALL_ONLY)
            if null_state.competing_candidates > 1:
                reasons.append(UnresolvedReason.COMPETING_STATE)
            if null_state.living_support and null_state.no_known_locality_support:
                reasons.append(UnresolvedReason.COMPETING_STATE)
        if not state.propositions:
            reasons.append(UnresolvedReason.VERIFIER_NOT_REQUESTED)
        units.append(UnresolvedUnit(
            unit_id="query_existence_state", kind="query_proposition",
            unresolved=bool(reasons), reasons=tuple(dict.fromkeys(reasons)),
        ))

    if not units:
        return UnresolvedMassDiagnostics(
            excluded_units=tuple(sorted(excluded)),
            availability=SignalAvailability.UNAVAILABLE,
            reason="no applicable target unit is represented for this query",
        )
    unresolved = sum(1 for u in units if u.unresolved)
    return UnresolvedMassDiagnostics(
        units=tuple(units), excluded_units=tuple(sorted(excluded)),
        unresolved_count=unresolved, applicable_count=len(units),
        value=ratio(unresolved, len(units)),
        availability=SignalAvailability.AVAILABLE,
    )


# --------------------------------------------------------------------------
# The residual ensemble
# --------------------------------------------------------------------------


def combine(
    values: Mapping[ResidualComponentName, tuple[float | None, SignalAvailability, str]],
    weights: Mapping[ResidualComponentName, float],
    weight_source: str,
) -> CoverageGapComponents:
    """§15's weighted ensemble over the **available** components.

    An unavailable component is not a zero: it is dropped and the remaining
    weights are renormalised, so a query that could only measure two signals is
    scored on those two rather than punished for the three it could not.

    With uniform weights this is exactly the mean of the available components,
    which is why the neutral vector is the honest default.
    """
    ordered = list(ResidualComponentName)
    available = [
        name for name in ordered
        if values[name][1].usable and weights.get(name, 0.0) > 0
    ]
    mass = sum(weights[name] for name in available)

    components = tuple(
        ResidualComponent(
            name=name, value=values[name][0] if values[name][1].usable else None,
            availability=values[name][1], reason=values[name][2],
            configured_weight=weights.get(name, 0.0),
            effective_weight=(
                weights[name] / mass if name in available and mass > 0 else 0.0
            ),
        )
        for name in ordered
    )
    if not available or mass <= 0:
        return CoverageGapComponents(
            components=components, weight_source=weight_source,
            effective_weight_mass=0.0, residual=None,
            availability=SignalAvailability.UNAVAILABLE,
        )
    residual = sum(
        (weights[name] / mass) * values[name][0] for name in available
    )
    # Guard against float drift only; every input is already bounded.
    residual = min(1.0, max(0.0, residual))
    return CoverageGapComponents(
        components=components, weight_source=weight_source,
        effective_weight_mass=1.0, residual=residual,
        availability=SignalAvailability.AVAILABLE,
    )


class CoverageGapEstimator:
    """§15's non-neural coverage-gap estimator. Deterministic, read-only."""

    def __init__(self, config: CoverageGapConfig | None = None) -> None:
        self.config = config or CoverageGapConfig(enabled=True)
        if self.config.mode not in CoverageGapConfig.SUPPORTED_MODES:
            raise CoverageGapError(
                f"unsupported coverage_gap mode {self.config.mode!r}"
            )

    @property
    def estimator_version(self) -> str:
        return self.config.estimator_version

    def estimate_coverage_gap(
        self,
        state: Any,
        *,
        program_type: str,
        facet_executions: Mapping[str, FacetExecution] | None = None,
        discovery_origins: Sequence[tuple[str, tuple[str, ...]]] = (),
    ) -> CoverageGapState:
        """Estimate the residual search need for one query. **Zero calls.**

        ``facet_executions`` and ``discovery_origins`` are the structural
        execution metadata Layer 4 does not itself carry - which facets ran and
        in what order discovery-capable operations emitted identities. They come
        from the applicable specialist's own record, read-only.
        """
        relation = state.relation
        facets = build_facet_map(relation, dict(facet_executions or {}))
        gap_value, gap_reason = facet_gap(facets)

        incidence = incidence_diagnostics(state)
        novelty = novelty_diagnostics(discovery_origins)
        disagreement = disagreement_diagnostics(state)
        unresolved = unresolved_mass(state, program_type)

        set_valued = program_type in ("SMALL_SET", "LARGE_OPEN_SET")
        if set_valued:
            singleton, singleton_reason = singleton_ratio(incidence)
            singleton_availability = (
                SignalAvailability.AVAILABLE if singleton is not None
                else SignalAvailability.UNAVAILABLE
            )
        else:
            # §11 gives incidence statistics to set-valued relations; a numeric
            # or null-single relation uses cluster stability and competing-state
            # uncertainty instead, so the ratio is not applicable rather than
            # unavailable.
            singleton, singleton_reason = self._non_set_singleton(state, program_type)
            if singleton is not None:
                singleton_availability = SignalAvailability.AVAILABLE
            elif program_type == "NUMERIC":
                # Applicable in principle, but nothing was measured yet.
                singleton_availability = SignalAvailability.UNAVAILABLE
            else:
                singleton_availability = SignalAvailability.NOT_APPLICABLE

        components = combine(
            {
                ResidualComponentName.NOVELTY_RATE: (
                    novelty.novelty_rate, novelty.availability, novelty.reason
                ),
                ResidualComponentName.SINGLETON_RATIO: (
                    singleton, singleton_availability, singleton_reason
                ),
                ResidualComponentName.FACET_GAP: (
                    gap_value,
                    SignalAvailability.AVAILABLE if gap_value is not None
                    else SignalAvailability.UNAVAILABLE,
                    gap_reason,
                ),
                ResidualComponentName.DISAGREEMENT: (
                    disagreement.value, disagreement.availability,
                    "" if disagreement.availability.usable
                    else "no bounded disagreement channel was measured",
                ),
                ResidualComponentName.UNRESOLVED_MASS: (
                    unresolved.value, unresolved.availability, unresolved.reason
                ),
            },
            self.config.weights,
            self.config.weight_source,
        )

        return CoverageGapState(
            estimator_version=self.estimator_version,
            layer4_version=state.integration_version,
            relation=relation, subject=state.subject, row_index=state.row_index,
            program_type=program_type,
            facets=facets, incidence=incidence, novelty=novelty,
            disagreement=disagreement, unresolved=unresolved,
            numeric=(
                self._numeric_diagnostics(state) if program_type == "NUMERIC" else None
            ),
            null_state=(
                self._null_diagnostics(state) if program_type == "NULL_SINGLE" else None
            ),
            residual=components,
        )

    # -- relation-specific readings -----------------------------------------

    @staticmethod
    def _non_set_singleton(state: Any, program_type: str) -> tuple[float | None, str]:
        """The singleton reading for a non-set-valued relation.

        For NUMERIC it is the fraction of Module 12 clusters resting on a
        single independent discovery group - §15's cluster-stability reading of
        the same idea, using Module 12's own ``independent_support`` and never
        a reclustering of its own.

        For NULL_SINGLE the relation admits at most one object, so a singleton
        ratio over a candidate pool would say nothing useful; §15 gives that
        relation competing-state uncertainty instead, and the null diagnostics
        carry it.
        """
        if program_type != "NUMERIC":
            return None, (
                "a null-single relation uses competing-state uncertainty rather "
                "than incidence statistics (§15)"
            )
        clusters = state.numeric_targets
        if not clusters:
            return None, "no numeric cluster is represented"
        supported = [c for c in clusters if c.independent_support > 0]
        if not supported:
            return None, "no numeric cluster has an independent discovery group"
        single = sum(1 for c in supported if c.independent_support == 1)
        return ratio(single, len(supported)), ""

    @staticmethod
    def _numeric_diagnostics(state: Any) -> NumericStabilityDiagnostics:
        """§15's cluster stability, entirely from Module 12's own figures."""
        clusters = state.numeric_targets
        return NumericStabilityDiagnostics(
            cluster_count=len(clusters),
            competing_clusters=max(
                (c.competing_clusters for c in clusters), default=0
            ),
            representatives=tuple(c.representative for c in clusters),
            dispersions=tuple(c.dispersion for c in clusters),
            independent_support=tuple(c.independent_support for c in clusters),
            single_group_clusters=sum(
                1 for c in clusters if c.independent_support == 1
            ),
            verifier_available_clusters=sum(
                1 for c in clusters if c.specialist_verifier.available
            ),
            structural_evidence_clusters=sum(
                1 for c in clusters if c.structural_checks
            ),
        )

    @staticmethod
    def _null_diagnostics(state: Any) -> NullCompetingStateDiagnostics:
        """§15's competing-state uncertainty, with Audit 0024's classes intact."""
        null_state = state.null_state
        if null_state is None:
            return NullCompetingStateDiagnostics(
                proposition_verifier_available=sum(
                    1 for p in state.propositions if p.specialist_verifier.available
                ),
            )
        return NullCompetingStateDiagnostics(
            living_support=null_state.living_support,
            no_known_locality_support=null_state.no_known_locality_support,
            failed_recall_operations=null_state.failed_recall_operations,
            substantive_null_groups=null_state.substantive_null_groups,
            failed_recall_only=null_state.failed_recall_only,
            competing_candidates=null_state.competing_candidates,
            competing_candidate_keys=null_state.competing_candidate_keys,
            status_conflict=bool(
                null_state.living_support and null_state.no_known_locality_support
            ),
            gate_state=null_state.gate_state,
            proposition_verifier_available=sum(
                1 for p in state.propositions if p.specialist_verifier.available
            ),
        )


def build_coverage_gap_estimator(
    config: Mapping[str, Any] | None, *, layer4_enabled: bool
) -> "CoverageGapEstimator | None":
    """Build M19 when configuration asks for it, refusing a broken wiring."""
    settings = CoverageGapConfig.from_mapping(config)
    if not settings.enabled:
        return None
    if not layer4_enabled:
        raise ValueError(
            "coverage_gap.enabled requires the Layer-4 integration; Module 19 "
            "estimates from the Layer-4 evidence state"
        )
    return CoverageGapEstimator(settings)


__all__ = [
    "CoverageGapConfig",
    "CoverageGapEstimator",
    "build_coverage_gap_estimator",
    "combine",
    "disagreement_diagnostics",
    "discovery_groups",
    "incidence_diagnostics",
    "is_discovery_group",
    "novelty_diagnostics",
    "singleton_ratio",
    "unresolved_mass",
]
