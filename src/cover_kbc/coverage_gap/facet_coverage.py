"""§15.1's facet coverage map, projected from the audited registries.

*"Each relation has a facet registry. M19 marks facets as {covered, weak,
unexplored, exhausted}."*

**The registry is not rewritten here.** Every facet comes from the structure
the applicable specialist already declares - Module 12's probe families,
Module 13's facet slices, Module 14's Stage-A/Stage-B families, Module 15's
gate, acquisition, missingness and cross-family templates. M19 adds no probe,
renames nothing and invents no facet, so the map cannot drift from what the
system actually runs.

Two distinctions do the real work:

**Applicable is not covered.** A facet the relation does not declare, or that
policy deliberately disables - §11.1's minimal-change border direct probe,
Module 13's geography facet with no slices - is *excluded with a reason*, not
marked ``UNEXPLORED``. Counting a deliberate omission as a gap would make a
minimal-change policy look like a failure and would push a future scheduler to
undo it.

**Failure is not exhaustion.** ``EXHAUSTED`` needs explicit structural evidence
that a facet was explored and yielded nothing further - a missingness probe
that ran and returned no new candidate. An empty answer, an abstention or a
runtime error is ``WEAK``. This is Audit 0024's hygiene rule in another place:
failed recall is not evidence of emptiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.coverage_gap.gap_types import (
    CoverageGapError,
    FacetCoverage,
    FacetCoverageRecord,
    FacetExclusion,
)

#: The specialist that owns each relation's facet registry (proposal §5.1).
FACET_OWNER: dict[str, str] = {
    "hasCapacity": "M12",
    "hasArea": "M12",
    "awardWonBy": "M13",
    "personHasCityOfDeath": "M14",
    "countryLandBordersCountry": "M15",
    "companyTradesAtStockExchange": "M15",
}


@dataclass(frozen=True)
class DeclaredFacet:
    """One facet as the owning specialist declares it."""

    facet_id: str
    family: str
    applicable: bool = True
    exclusion: FacetExclusion | None = None
    exclusion_reason: str = ""
    #: A facet whose whole purpose is to look for what is missing. Only these
    #: can reach EXHAUSTED, and only with explicit evidence.
    missingness: bool = False


def declared_facets(relation: str) -> tuple[DeclaredFacet, ...]:
    """The relation's facet registry, read from its owning specialist."""
    owner = FACET_OWNER.get(relation)
    if owner is None:
        raise CoverageGapError(
            f"no facet registry owner is declared for relation {relation!r}"
        )
    return {
        "M12": _numeric_facets,
        "M13": _large_set_facets,
        "M14": _null_temporal_facets,
        "M15": _small_set_facets,
    }[owner](relation)


def _numeric_facets(relation: str) -> tuple[DeclaredFacet, ...]:
    """Module 12's probe families.

    ``hasArea`` declares four and ``hasCapacity`` five: the historical/current
    configuration family is simply not part of the area contract, so it is
    *not declared* rather than unexplored.
    """
    from cover_kbc.specialists.numeric_registry import NUMERIC_RELATIONS

    spec = NUMERIC_RELATIONS[relation]
    declared = {family.value for family in spec.probe_families}
    every = {
        family.value
        for other in NUMERIC_RELATIONS.values()
        for family in other.probe_families
    }
    out = [
        DeclaredFacet(facet_id=name, family="numeric_probe")
        for name in sorted(declared)
    ]
    out.extend(
        DeclaredFacet(
            facet_id=name, family="numeric_probe", applicable=False,
            exclusion=FacetExclusion.NOT_DECLARED,
            exclusion_reason=(
                f"{relation}'s contract does not declare the {name} probe family"
            ),
        )
        for name in sorted(every - declared)
    )
    return tuple(out)


def _large_set_facets(relation: str) -> tuple[DeclaredFacet, ...]:
    """Module 13's seed plus each declared facet slice.

    A facet kind with no slices - geography, for this award contract - is
    declared but has nothing to run, so it is excluded with that reason rather
    than counted as an unexplored gap.
    """
    from cover_kbc.specialists.large_set_registry import LARGE_SET_RELATIONS

    spec = LARGE_SET_RELATIONS[relation]
    out = [DeclaredFacet(facet_id="seed", family="seed")]
    for facet in spec.facets:
        kind = facet.kind.value
        if not facet.enabled:
            out.append(DeclaredFacet(
                facet_id=kind, family=kind, applicable=False,
                exclusion=FacetExclusion.DISABLED_BY_POLICY,
                exclusion_reason=f"the {kind} facet is disabled for {relation}",
            ))
            continue
        if not facet.slices:
            out.append(DeclaredFacet(
                facet_id=kind, family=kind, applicable=False,
                exclusion=FacetExclusion.NO_OPERATIONS,
                exclusion_reason=(
                    f"the {kind} facet declares no slices for {relation}, so it "
                    "has no operation to run"
                ),
            ))
            continue
        out.extend(
            DeclaredFacet(
                facet_id=facet_id, family=kind,
                missingness=kind == "missingness",
            )
            for facet_id, _ in facet.slices
        )
    return tuple(out)


def _null_temporal_facets(relation: str) -> tuple[DeclaredFacet, ...]:
    """Module 14's Stage-A existence families and Stage-B locality families."""
    from cover_kbc.specialists.null_temporal_registry import NULL_TEMPORAL_RELATIONS

    spec = NULL_TEMPORAL_RELATIONS[relation]
    def _facet_id(template: Any) -> str:
        family = template.family
        return family.value if hasattr(family, "value") else str(family)

    out = [
        DeclaredFacet(facet_id=_facet_id(template), family="stage_a")
        for template in spec.stage_a
    ]
    out.extend(
        DeclaredFacet(facet_id=_facet_id(template), family="stage_b")
        for template in spec.stage_b
    )
    return tuple(out)


def _small_set_facets(relation: str) -> tuple[DeclaredFacet, ...]:
    """Module 15's gate, acquisition, missingness and cross-family templates.

    A template the registry declares with ``enabled=False`` - the border direct
    probe, disabled by §11.1's minimal-change rule - is excluded with that
    reason. It was not skipped; it was deliberately not part of the plan.
    """
    from cover_kbc.specialists.small_set_registry import SMALL_SET_RELATIONS

    spec = SMALL_SET_RELATIONS[relation]
    out: list[DeclaredFacet] = []
    for family, templates in (
        ("gate", spec.gate), ("acquisition", spec.acquisition),
        ("missingness", spec.missingness), ("cross_family", spec.cross_family),
    ):
        for template in templates:
            if not template.enabled:
                out.append(DeclaredFacet(
                    facet_id=template.facet_id, family=family, applicable=False,
                    exclusion=FacetExclusion.DISABLED_BY_POLICY,
                    exclusion_reason=template.rationale or (
                        f"{template.facet_id} is disabled by policy"
                    ),
                ))
                continue
            out.append(DeclaredFacet(
                facet_id=template.facet_id, family=family,
                missingness=family == "missingness",
            ))
    return tuple(out)


# --------------------------------------------------------------------------
# Execution evidence -> the four states
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FacetExecution:
    """What one facet actually produced, read from the specialist's own record."""

    facet_id: str
    operations: int = 0
    usable_observations: int = 0
    exhaustion_evidence: str = ""


def coverage_for(facet: DeclaredFacet, execution: FacetExecution | None) -> FacetCoverage:
    """§15.1's four states, decided from recorded execution only.

    ``UNEXPLORED`` - nothing ran.
    ``WEAK``       - something ran but produced no usable observation:
                     abstained, malformed, failed, near-miss only.
    ``COVERED``    - at least one usable, contract-relevant observation.
    ``EXHAUSTED``  - a missingness facet ran and **explicitly** reported no
                     further candidate. Never inferred from one empty answer.
    """
    if execution is None or execution.operations == 0:
        return FacetCoverage.UNEXPLORED
    if facet.missingness and execution.exhaustion_evidence:
        return FacetCoverage.EXHAUSTED
    if execution.usable_observations > 0:
        return FacetCoverage.COVERED
    return FacetCoverage.WEAK


def build_facet_map(
    relation: str, executions: Mapping[str, FacetExecution]
) -> tuple[FacetCoverageRecord, ...]:
    """The relation's whole facet map, applicable and excluded facets alike."""
    seen: set[str] = set()
    records: list[FacetCoverageRecord] = []
    for facet in declared_facets(relation):
        if facet.facet_id in seen:
            raise CoverageGapError(
                f"{relation} declares facet {facet.facet_id!r} twice"
            )
        seen.add(facet.facet_id)
        execution = executions.get(facet.facet_id)
        if not facet.applicable:
            records.append(FacetCoverageRecord(
                facet_id=facet.facet_id, family=facet.family, applicable=False,
                exclusion=facet.exclusion, exclusion_reason=facet.exclusion_reason,
                executed_operations=execution.operations if execution else 0,
                usable_observations=(
                    execution.usable_observations if execution else 0
                ),
            ))
            continue
        records.append(FacetCoverageRecord(
            facet_id=facet.facet_id, family=facet.family, applicable=True,
            coverage=coverage_for(facet, execution),
            executed_operations=execution.operations if execution else 0,
            usable_observations=execution.usable_observations if execution else 0,
            exhaustion_evidence=execution.exhaustion_evidence if execution else "",
        ))

    unknown = set(executions) - seen
    if unknown:
        raise CoverageGapError(
            f"{relation}: execution recorded for facets its registry does not "
            f"declare: {sorted(unknown)}"
        )
    return tuple(sorted(records, key=lambda r: r.facet_id))


def facet_gap(records: Sequence[FacetCoverageRecord]) -> tuple[float | None, str]:
    """§10's equation, over applicable active facets only::

        facetGap_t = (#UNEXPLORED + #WEAK) / #applicable_active_facets

    ``COVERED`` and ``EXHAUSTED`` contribute nothing; ``WEAK`` and
    ``UNEXPLORED`` each contribute one. There is no per-state severity weight,
    because none is defined and inventing one would be a fitted parameter.

    Returns ``(None, reason)`` when there is no applicable facet: dividing by
    one and calling the relation fully covered would be a fabricated certainty.
    """
    applicable = [r for r in records if r.applicable]
    if not applicable:
        return None, "the relation declares no applicable active facet"
    gaps = sum(1 for r in applicable if r.coverage and r.coverage.contributes_gap)
    return gaps / len(applicable), ""


def registry_snapshot(relation: str) -> list[Mapping[str, Any]]:
    """The declared registry, for a trace."""
    return [
        {
            "facet_id": facet.facet_id,
            "family": facet.family,
            "applicable": facet.applicable,
            "exclusion": facet.exclusion.value if facet.exclusion else None,
            "missingness": facet.missingness,
        }
        for facet in declared_facets(relation)
    ]




# --------------------------------------------------------------------------
# Execution metadata, read from the applicable specialist's own record
# --------------------------------------------------------------------------


def _bump(
    executions: dict[str, FacetExecution], facet_id: str, *, usable: bool,
    exhaustion: str = "",
) -> None:
    current = executions.get(facet_id) or FacetExecution(facet_id=facet_id)
    executions[facet_id] = FacetExecution(
        facet_id=facet_id,
        operations=current.operations + 1,
        usable_observations=current.usable_observations + (1 if usable else 0),
        exhaustion_evidence=exhaustion or current.exhaustion_evidence,
    )


def _is_specialist_probe(observation: Any) -> bool:
    """Whether an observation came from the specialist's **own** probe.

    A specialist also mines Module 11's parametric memory, and those
    observations carry Module 11 operation ids - they are upstream acquisition,
    not facets of this specialist's registry. They belong to novelty (they can
    genuinely name something new) and not to the facet coverage map, which
    describes what the specialist's own facet plan covered.
    """
    source = getattr(observation, "source", None)
    return getattr(source, "value", "") != "PARAMETRIC_MEMORY"


def facet_executions(relation: str, result: Any) -> dict[str, FacetExecution]:
    """What each facet produced, read from the applicable specialist result.

    Structural execution metadata only - which operations ran and whether each
    produced a usable, contract-relevant observation. The *factual* reading of
    that evidence is Layer 4's, and this never second-guesses it.

    ``None`` yields an empty map, which is the honest state for a query whose
    specialist never ran: every applicable facet is then ``UNEXPLORED``.
    """
    if result is None:
        return {}
    owner = FACET_OWNER.get(relation)
    executions: dict[str, FacetExecution] = {}

    if owner == "M12":
        for obs in result.observations:
            if _is_specialist_probe(obs):
                _bump(executions, obs.independence_group, usable=obs.usable)
    elif owner == "M13":
        for obs in result.observations:
            if _is_specialist_probe(obs):
                _bump(executions, obs.facet_id, usable=obs.usable)
        missingness = {f.facet_id for f in declared_facets(relation) if f.missingness}
        for facet in getattr(result, "facet_states", ()):
            # Only a *missingness* facet evidences exhaustion. An ordinary facet
            # that named nothing new may simply have re-named what the query
            # already held, or failed; neither says the facet is closed, and
            # recording it as such would overstate what Module 13 observed.
            if facet.facet_id not in missingness:
                continue
            if facet.facet_id in executions and not facet.new_surfaces:
                current = executions[facet.facet_id]
                executions[facet.facet_id] = FacetExecution(
                    facet_id=facet.facet_id, operations=current.operations,
                    usable_observations=current.usable_observations,
                    exhaustion_evidence=(
                        "the facet ran and reported no surface the query did not "
                        "already hold"
                    ),
                )
    elif owner == "M14":
        for obs in result.status_observations:
            if _is_specialist_probe(obs):
                _bump(executions, obs.family, usable=obs.parse_status.value == "OK")
        for obs in result.locality_observations:
            if _is_specialist_probe(obs):
                _bump(executions, obs.family, usable=obs.usable)
    elif owner == "M15":
        for obs in result.listing_observations:
            if _is_specialist_probe(obs):
                _bump(executions, obs.family, usable=obs.parse_status.value == "OK")
        closure = getattr(result, "closure", None)
        empty = bool(closure and closure.missingness_probed and closure.missingness_empty)
        for obs in result.candidate_observations:
            if not _is_specialist_probe(obs):
                continue
            exhaustion = ""
            if empty and obs.facet_id.endswith("missingness"):
                exhaustion = (
                    "the missingness probe ran and named no candidate the query "
                    "did not already hold"
                )
            _bump(executions, obs.facet_id, usable=obs.usable, exhaustion=exhaustion)
    return executions


def discovery_origins(relation: str, result: Any, layer4: Any) -> tuple[
    tuple[str, tuple[str, ...]], ...
]:
    """Discovery-capable operations in deterministic execution order.

    Verification is excluded by construction: only the specialist's own
    acquisition observations and Module 18's **candidate-free** records appear,
    because those are the operations that can name something new. A reverse or
    counterfactual check is shown the candidate and cannot discover it; a
    verifier reading cannot either.

    Order is the recorded order of the observations themselves, never a clock.
    """
    origins: dict[str, list[str]] = {}
    order: list[str] = []

    def _add(operation_id: str, key: str) -> None:
        if operation_id not in origins:
            origins[operation_id] = []
            order.append(operation_id)
        if key:
            origins[operation_id].append(key)

    owner = FACET_OWNER.get(relation)
    if result is not None:
        if owner == "M12":
            # Identity is Module 12's, not this module's. A numeric target is
            # "the same value we already have" exactly when M12's own tolerance
            # clustering says so, and M12 publishes that mapping as
            # ``member_indices``. Formatting the raw float here instead would
            # impose an implicit exact-equality rule, so two readings M12 calls
            # one target would register as two discoveries and inflate novelty.
            # Nothing is re-clustered: this only reads the assignment M12 made.
            cluster_of = {
                index: position
                for position, cluster in enumerate(result.clusters)
                for index in cluster.member_indices
            }
            for index, obs in enumerate(result.observations):
                position = cluster_of.get(index)
                _add(
                    obs.operation_id,
                    f"m12_cluster#{position}" if position is not None else "",
                )
        elif owner in ("M13", "M14", "M15"):
            observations = (
                result.observations if owner == "M13"
                else result.locality_observations if owner == "M14"
                else result.candidate_observations
            )
            for obs in observations:
                _add(obs.operation_id, obs.normalized_surface if obs.usable else "")

    if layer4 is not None:
        for overlay in layer4.candidates:
            for check in overlay.structural_checks:
                if check.check_kind != "CANDIDATE_FREE_RECALL":
                    continue
                if check.outcome.value == "SUPPORT":
                    _add(check.origin_event_id, overlay.candidate_key)
                else:
                    _add(check.origin_event_id, "")

    return tuple((operation_id, tuple(origins[operation_id])) for operation_id in order)


__all__ = [
    "FACET_OWNER",
    "DeclaredFacet",
    "FacetExecution",
    "build_facet_map",
    "coverage_for",
    "declared_facets",
    "discovery_origins",
    "facet_executions",
    "facet_gap",
    "registry_snapshot",
]
