"""Module 16 adapters - existing evidence, one canonical representation.

Each adapter is a **narrow projection**: it reads what a module already
produced and emits :class:`ConsensusEvidenceEvent` values. No specialist is
rewritten, no specialist is imported by another, and nothing is recomputed that
a specialist already computed.

Two rules run through every adapter.

**Canonical origin identity.** Provenance is copied, never re-minted. When
Module 13 mines a Module 11 record it already carries that record's
``operation_id``, ``prompt_sha256``, ``model_id`` and ``sample_index``
unchanged, so :func:`derive_origin_event_id` maps the record and its derived
observation onto the *same* origin. The engine then charges that origin once
and counts it once. Nothing here has to detect the derivation: it is visible in
the provenance the specialists already preserve.

**Role, not source, decides what an event may pay into.** An adapter assigns
:class:`EvidenceRole`; the engine maps role to term. That keeps Audit 0008's
index-set separation intact across four new evidence producers.
"""

from __future__ import annotations

from typing import Any, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.evidence.consensus_types import (
    ConsensusEvidenceEvent,
    EvidencePlane,
    EvidenceRole,
    PendingDownstreamCheck,
    derive_origin_event_id,
)
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.normalization.strings import is_abstain
from cover_kbc.types import EdgeType, EvidenceMode, IndependenceGroup, OutputType

#: Which Layer-2 specialist owns each relation (proposal §5.1, Table 3).
APPLICABLE_SPECIALIST: dict[str, str] = {
    "hasCapacity": "M12",
    "hasArea": "M12",
    "awardWonBy": "M13",
    "personHasCityOfDeath": "M14",
    "countryLandBordersCountry": "M15",
    "companyTradesAtStockExchange": "M15",
}


def applicable_specialist(relation: str) -> str:
    """The one specialist M16 needs for this relation. Fails closed."""
    try:
        return APPLICABLE_SPECIALIST[relation]
    except KeyError as exc:  # pragma: no cover - contracts cover all six
        raise KeyError(
            f"no Layer-2 specialist is declared for relation {relation!r}"
        ) from exc


def _origin(
    *, model_id: str, operation_id: str, prompt_sha256: str, sample_index: int
) -> str:
    return derive_origin_event_id(
        model_id=model_id, operation_id=operation_id,
        prompt_sha256=prompt_sha256, sample_index=sample_index,
    )


# --------------------------------------------------------------------------
# A. Core evidence - Modules 3 and 4
# --------------------------------------------------------------------------


_CORE_ROLES: dict[IndependenceGroup, EvidenceRole] = {
    IndependenceGroup.BLIND_VERIFIER: EvidenceRole.BLIND_VERIFIER,
    IndependenceGroup.CROSS_MODEL_RECALL: EvidenceRole.CROSS_MODEL_RECALL,
    IndependenceGroup.EXISTENCE_GATE: EvidenceRole.EXISTENCE_GATE,
}


def core_role(group: IndependenceGroup) -> EvidenceRole:
    """Audit 0008's channel for one core independence group."""
    return _CORE_ROLES.get(group, EvidenceRole.CORE_ACQUISITION)


def core_graph_events(graph: EvidenceGraph) -> list[ConsensusEvidenceEvent]:
    """Project Module 3's edges. **Reads only** - the graph is untouched.

    Cost is taken from the :class:`GenerationRecord`, once per record, and
    attached to the first event that names it, so a record that produced five
    candidate edges is still one call.
    """
    query = graph.query
    charged: set[str] = set()
    events: list[ConsensusEvidenceEvent] = []

    for candidate in graph.candidates.values():
        for edge in candidate.all_evidence():
            record = graph.records.get(edge.record_id)
            origin = _origin(
                model_id=edge.model_id,
                operation_id=record.view_id if record else edge.view_id,
                prompt_sha256=record.prompt_hash if record else edge.record_id,
                sample_index=edge.run_id,
            )
            first = origin not in charged
            charged.add(origin)
            supports = edge.edge_type is EdgeType.SUPPORT
            events.append(ConsensusEvidenceEvent(
                relation=query.relation, subject=query.subject,
                row_index=query.row_index,
                candidate_key=candidate.key, display=candidate.display_value,
                source_module="M4" if edge.independence_group
                is IndependenceGroup.BLIND_VERIFIER else "M3",
                source_record_id=edge.record_id,
                origin_event_id=origin,
                plane=EvidencePlane.CORE,
                independence_group=edge.independence_group.value,
                role=core_role(edge.independence_group),
                sign=edge.edge_type,
                support=1 if supports else 0,
                model_id=edge.model_id,
                model_family=edge.model_family,
                mode=edge.mode,
                facet_id=(record.facet_id if record else "") or "",
                sample_index=edge.run_id,
                prompt_sha256=record.prompt_hash if record else "",
                calls=1 if first else 0,
                generated_tokens=(record.generated_tokens or 0) if first and record else 0,
                prompt_tokens=(record.prompt_tokens or 0) if first and record else 0,
                latency_ms=(record.latency_ms if first and record else None),
                verified=edge.independence_group is IndependenceGroup.BLIND_VERIFIER,
                hard_violation=candidate.rejection_reason is not None,
            ))

    # Records that produced no candidate still cost something and still name a
    # mechanism that ran. Dropping them would understate spend and would hide
    # an executed mechanism that found nothing.
    for record in graph.records.values():
        origin = _origin(
            model_id=record.model_id, operation_id=record.view_id,
            prompt_sha256=record.prompt_hash, sample_index=record.run_id,
        )
        if origin in charged:
            continue
        charged.add(origin)
        events.append(ConsensusEvidenceEvent(
            relation=query.relation, subject=query.subject,
            row_index=query.row_index, candidate_key="", display="",
            source_module="M3", source_record_id=record.record_id,
            origin_event_id=origin, plane=EvidencePlane.CORE,
            independence_group=record.independence_group.value,
            role=core_role(record.independence_group),
            sign=EdgeType.UNKNOWN, support=0,
            model_id=record.model_id, model_family=record.model_family,
            facet_id=record.facet_id, sample_index=record.run_id,
            prompt_sha256=record.prompt_hash,
            calls=1, generated_tokens=record.generated_tokens or 0,
            prompt_tokens=record.prompt_tokens or 0, latency_ms=record.latency_ms,
        ))
    return events


# --------------------------------------------------------------------------
# B. Module 11 - registered for provenance and cost, never re-parsed
# --------------------------------------------------------------------------


def parametric_events(
    result: Any, *, relation: str, subject: str, row_index: int
) -> list[ConsensusEvidenceEvent]:
    """Register each Module 11 record as a query-level origin.

    No candidate is extracted here. Parsing Module 11's text is the applicable
    specialist's job, and doing it twice would be exactly the double count §5
    of the brief warns about. What this contributes is the origin itself, so
    the cost is attributable and so a mined observation can be recognised as a
    *description* of an output that is already accounted for.
    """
    events: list[ConsensusEvidenceEvent] = []
    for record in result.records:
        events.append(ConsensusEvidenceEvent(
            relation=relation, subject=subject, row_index=row_index,
            candidate_key="", display="",
            source_module="M11", source_record_id=record.operation_id,
            origin_event_id=_origin(
                model_id=record.model_id, operation_id=record.operation_id,
                prompt_sha256=record.prompt_sha256, sample_index=record.sample_index,
            ),
            plane=EvidencePlane.PARAMETRIC,
            independence_group=record.independence_group.value,
            role=EvidenceRole.PARAMETRIC_MEMORY,
            sign=EdgeType.UNKNOWN, support=0,
            model_id=record.model_id, facet_id=record.operation_id,
            sample_index=record.sample_index, prompt_sha256=record.prompt_sha256,
            calls=record.calls, generated_tokens=record.generated_tokens,
            prompt_tokens=record.prompt_tokens, latency_ms=record.latency_ms,
            annotations=(f"parse_status={record.parse_status.value}",),
        ))
    return events


# --------------------------------------------------------------------------
# Shared helpers for the four specialist adapters
# --------------------------------------------------------------------------


def _plane_for(source_value: str) -> EvidencePlane:
    """Mined-from-M11 observations keep the parametric plane.

    The mechanism that produced the text was Module 11's probe, so two
    specialists mining one probe name one group, not two.
    """
    return (
        EvidencePlane.PARAMETRIC if source_value == "PARAMETRIC_MEMORY"
        else EvidencePlane.SPECIALIST
    )


def _specialist_event(
    *, relation: str, subject: str, row_index: int, module: str,
    candidate_key: str, display: str, obs: Any, group: str, role: EvidenceRole,
    sign: EdgeType, charged: set[str], annotations: Sequence[str] = (),
    facet_id: str = "", hard_violation: bool = False,
) -> ConsensusEvidenceEvent:
    """One specialist observation, with its provenance copied verbatim.

    Cost follows the plane, which is what keeps the arithmetic honest:

    * a **mined** observation sits on the parametric plane and charges nothing.
      Module 11 already declared that output's cost, and charging the reading
      of it again is precisely the double count to avoid.
    * a specialist's **own** probe is a physical call nothing else declares, so
      the first description of that origin charges one call - the same rule
      already applied to a Module 3 generation record.

    Token counts are a different matter: a specialist reports spend in
    aggregate, not per observation. ``tokens_recorded=False`` says so, rather
    than writing an unknown down as zero.
    """
    plane = _plane_for(obs.source.value)
    origin = _origin(
        model_id=obs.model_id, operation_id=obs.operation_id,
        prompt_sha256=obs.prompt_sha256, sample_index=obs.sample_index,
    )
    own_probe = plane is EvidencePlane.SPECIALIST
    first = own_probe and origin not in charged
    charged.add(origin)
    return ConsensusEvidenceEvent(
        relation=relation, subject=subject, row_index=row_index,
        candidate_key=candidate_key, display=display,
        source_module=module, source_record_id=obs.operation_id,
        origin_event_id=origin,
        plane=plane,
        independence_group=group, role=role, sign=sign,
        support=1 if sign is EdgeType.SUPPORT else 0,
        model_id=obs.model_id, facet_id=facet_id,
        sample_index=obs.sample_index, prompt_sha256=obs.prompt_sha256,
        calls=1 if first else 0,
        tokens_recorded=not own_probe,
        annotations=tuple(annotations), hard_violation=hard_violation,
    )


def _cross_family_role(obs: Any, primary_model_ids: frozenset[str]) -> EvidenceRole:
    """§10.2's branch pays ``X`` only when the family is genuinely distinct.

    M14 and M15 already refuse to run the branch through one checkpoint, so
    this is a defence in depth rather than a second policy: an observation
    labelled cross-family but carrying a model id the primary family also used
    is demoted to ordinary specialist acquisition.
    """
    if getattr(obs, "recall_family", None) is None:
        return EvidenceRole.SPECIALIST_ACQUISITION
    if obs.recall_family.value != "CROSS_FAMILY":
        return EvidenceRole.SPECIALIST_ACQUISITION
    if obs.model_id in primary_model_ids:
        return EvidenceRole.SPECIALIST_ACQUISITION
    return EvidenceRole.SPECIALIST_CROSS_FAMILY


def _primary_model_ids(observations: Sequence[Any]) -> frozenset[str]:
    return frozenset(
        obs.model_id for obs in observations
        if getattr(obs, "recall_family", None) is None
        or obs.recall_family.value == "PRIMARY_FAMILY"
    )


def _candidate_key_for(contract: RelationContract, surface: str) -> str:
    """Module 3's key, with Module 3's abstention guard.

    ``add_entity_mentions`` refuses "NONE"/"UNKNOWN"/"" before keying, because
    those express absence rather than an object. M16 applies the same guard to
    specialist observations - using the same function, not a second rule - so
    consensus can never mint a candidate the production graph deliberately
    declined to create. (Module 14's mining path currently marks a "NONE"
    record as a usable target locality; this guard means that cannot become a
    phantom consensus candidate. The underlying M14 behaviour is reported, not
    silently altered here.)
    """
    if not surface or is_abstain(surface):
        return ""
    return contract.strict_key(surface)


# --------------------------------------------------------------------------
# C. Module 12 - numeric
# --------------------------------------------------------------------------


def numeric_events(
    result: Any, contract: RelationContract
) -> list[ConsensusEvidenceEvent]:
    """Numeric observations, keyed the way Module 3 keys a numeric candidate.

    A hard-definition violation ("that is the attendance figure") is an
    explicit signed contradiction of the value it names: the model itself said
    the number denotes a quantity the contract excludes.

    An observation that parsed to no number still yields a query-level event.
    The probe ran and cost a call, and dropping it would understate the spend
    and hide a mechanism that was tried and found nothing.
    """
    plan = result.plan
    integer_only = contract.selection.numeric_integer_only
    charged: set[str] = set()
    events: list[ConsensusEvidenceEvent] = []

    for obs in result.observations:
        violation = obs.semantic_kind.is_hard_definition_violation
        if obs.canonical_value is None:
            key, sign = "", EdgeType.UNKNOWN
        else:
            key = format_numeric(obs.canonical_value, integer_only=integer_only)
            sign = EdgeType.SUPPORT if obs.usable else (
                EdgeType.CONTRADICT if violation else EdgeType.UNKNOWN
            )
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M12", candidate_key=key, display=key, obs=obs,
            group=obs.independence_group, role=EvidenceRole.SPECIALIST_ACQUISITION,
            sign=sign, charged=charged, facet_id=obs.operation_id,
            annotations=(
                f"semantic_kind={obs.semantic_kind.value}",
                f"parse_status={obs.parse_status.value}",
                *(f"ambiguity={flag}" for flag in obs.ambiguity_flags),
            ),
            hard_violation=violation,
        ))
    return events


# --------------------------------------------------------------------------
# D. Module 13 - large open set
# --------------------------------------------------------------------------


def large_set_events(
    result: Any, contract: RelationContract
) -> list[ConsensusEvidenceEvent]:
    """Award mentions, keyed by Module 3's strict key.

    A near-miss mention is a signed contradiction of that surface: the model
    said this name is a nominee, a work or a different award's recipient, which
    is exactly what the contract excludes.
    """
    plan = result.plan
    charged: set[str] = set()
    events: list[ConsensusEvidenceEvent] = []
    for obs in result.observations:
        key = _candidate_key_for(contract, obs.normalized_surface)
        sign = EdgeType.UNKNOWN if not key else (
            EdgeType.SUPPORT if obs.usable else (
                EdgeType.CONTRADICT if obs.mention_kind.is_near_miss else EdgeType.UNKNOWN
            )
        )
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M13", candidate_key=key,
            display=obs.normalized_surface if key else "", obs=obs,
            group=obs.independence_group, role=EvidenceRole.SPECIALIST_ACQUISITION,
            sign=sign, charged=charged, facet_id=obs.facet_id,
            annotations=(
                f"mention_kind={obs.mention_kind.value}",
                f"facet_kind={obs.facet_kind.value}",
                f"parse_status={obs.parse_status.value}",
            ),
        ))
    return events


# --------------------------------------------------------------------------
# E. Module 14 - null / temporal
# --------------------------------------------------------------------------


def null_temporal_events(
    result: Any, contract: RelationContract
) -> list[ConsensusEvidenceEvent]:
    """Locality mentions, Stage-A status readings, and the null classes.

    Stage-A readings are **query-level**: they say something about whether an
    object exists, never about which city it is, so they carry no candidate key
    and can never become candidate acquisition support.
    """
    plan = result.plan
    primary = _primary_model_ids(result.locality_observations)
    charged: set[str] = set()
    events: list[ConsensusEvidenceEvent] = []

    for obs in result.locality_observations:
        key = _candidate_key_for(contract, obs.normalized_surface)
        sign = EdgeType.UNKNOWN if not key else (
            EdgeType.SUPPORT if obs.usable else (
                EdgeType.CONTRADICT if obs.mention_kind.is_near_miss else EdgeType.UNKNOWN
            )
        )
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M14", candidate_key=key,
            display=obs.normalized_surface if key else "", obs=obs,
            group=obs.independence_group,
            role=_cross_family_role(obs, primary), sign=sign, charged=charged,
            facet_id=obs.family,
            annotations=(
                f"mention_kind={obs.mention_kind.value}",
                f"recall_family={obs.recall_family.value}",
                f"parse_status={obs.parse_status.value}",
            ),
        ))

    for obs in result.status_observations:
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M14", candidate_key="", display="", obs=obs,
            group=obs.independence_group, role=EvidenceRole.SPECIALIST_GATE,
            sign=EdgeType.UNKNOWN, charged=charged, facet_id=obs.family,
            annotations=(
                f"death_status={obs.status.value}",
                f"parse_status={obs.parse_status.value}",
            ),
        ))
    return events


# --------------------------------------------------------------------------
# F. Module 15 - small-set closure
# --------------------------------------------------------------------------


def small_set_events(
    result: Any, contract: RelationContract
) -> list[ConsensusEvidenceEvent]:
    """Border and exchange mentions, plus the public-listing gate readings."""
    plan = result.plan
    primary = _primary_model_ids(result.candidate_observations)
    charged: set[str] = set()
    events: list[ConsensusEvidenceEvent] = []

    for obs in result.candidate_observations:
        key = _candidate_key_for(contract, obs.normalized_surface)
        sign = EdgeType.UNKNOWN if not key else (
            EdgeType.SUPPORT if obs.usable else (
                EdgeType.CONTRADICT if not obs.is_target else EdgeType.UNKNOWN
            )
        )
        annotations = [
            f"mention_kind={obs.mention_kind}",
            f"recall_family={obs.recall_family.value}",
            f"parse_status={obs.parse_status.value}",
        ]
        if obs.listing_type is not None:
            annotations.append(f"listing_type={obs.listing_type.value}")
        if obs.temporal_status is not None:
            annotations.append(f"temporal_status={obs.temporal_status.value}")
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M15", candidate_key=key,
            display=obs.normalized_surface if key else "", obs=obs,
            group=obs.independence_group,
            role=_cross_family_role(obs, primary), sign=sign, charged=charged,
            facet_id=obs.facet_id, annotations=annotations,
        ))

    for obs in result.listing_observations:
        events.append(_specialist_event(
            relation=plan.relation, subject=plan.subject, row_index=plan.row_index,
            module="M15", candidate_key="", display="", obs=obs,
            group=obs.independence_group, role=EvidenceRole.SPECIALIST_GATE,
            sign=EdgeType.UNKNOWN, charged=charged, facet_id=obs.family,
            annotations=(
                f"listing_status={obs.status.value}",
                f"parse_status={obs.parse_status.value}",
            ),
        ))
    return events


# --------------------------------------------------------------------------
# Pending checks - carried forward, never executed
# --------------------------------------------------------------------------


def pending_checks_from(result: Any, module: str) -> list[PendingDownstreamCheck]:
    """Requests a specialist made of Module 18. M16 runs none of them."""
    checks = getattr(result, "pending_checks", ())
    return [
        PendingDownstreamCheck(
            source_module=module, kind=check.kind.value, reason=check.reason.value,
            candidate=check.candidate, detail=getattr(check, "detail", ""),
        )
        for check in checks
    ]


SPECIALIST_ADAPTERS = {
    "M12": numeric_events,
    "M13": large_set_events,
    "M14": null_temporal_events,
    "M15": small_set_events,
}


def candidate_kind(contract: RelationContract) -> str:
    return (
        "NUMBER" if contract.output_type is OutputType.NUMBER else "ENTITY"
    )


def shown_candidate_modes() -> tuple[EvidenceMode, ...]:
    """Exposed so a test can assert the anchoring distinction is still read."""
    return (EvidenceMode.SHOWN_CANDIDATE,)


__all__ = [
    "APPLICABLE_SPECIALIST",
    "SPECIALIST_ADAPTERS",
    "applicable_specialist",
    "candidate_kind",
    "core_graph_events",
    "core_role",
    "large_set_events",
    "null_temporal_events",
    "numeric_events",
    "parametric_events",
    "pending_checks_from",
    "small_set_events",
]
