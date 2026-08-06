"""Module 16 public contract - the Atomic Consensus Engine.

Proposal §12: *"Self-Consistency selects an answer mode; ASC merges atomic
facts. COVER needs a relation-aware variant: an atomic candidate graph rather
than list-level voting."* §12.1 fixes the arithmetic::

    q_g(o) = max  support(e, o)          for each independence group g
             e in g

    phi(o) = (F, L, X, C, U, I, D, cost, risk)

Three things this module is **not**:

* **Not a decision.** There is no accepted set, no rejected set, no final
  prediction, no ``should_stop``. M16 fuses evidence; M17/M18 verify and
  Module 8 finalizes. No field here is named ``accepted``, ``valid`` or
  ``final``, and a test asserts it of the serialised payload.
* **Not a re-definition of F/L/X/C/U.** Audit 0008 fixed those channels and
  removed two double counts. M16 *extends the evidence plane* onto the same
  channels; it does not reset their semantics.
* **Not a cache.** Every value here is a pure projection of evidence that
  already exists. Given the same graph, specialist result and configuration it
  recomputes identically, so it can never drift from what it describes.

Support strength is deliberately **categorical**. A parser that split a line on
a semicolon has not measured a probability, and dressing its output as one
would put a fabricated number where the proposal asks for evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from cover_kbc.types import EdgeType, EvidenceMode

#: Bumped when the meaning of any field changes.
CONSENSUS_VERSION = "m16-v1"


class ConsensusError(RuntimeError):
    """M16 could not run - bad inputs, bad routing or bad configuration."""


class ConsensusProvenanceError(ConsensusError):
    """Two records claim one origin but disagree about what produced it.

    Never repaired silently: if one physical model output is described two ways
    and the descriptions conflict, the safe reading is that the provenance is
    wrong, not that one description should win.
    """


class EvidencePlane(str, Enum):
    """Which subsystem an event's independence group belongs to.

    Group names collide across planes (both Module 2 and a specialist may call
    something ``DIRECT_RECALL``), so the plane is part of group identity. A
    specialist observation *mined from* Module 11 keeps the ``PARAMETRIC``
    plane, because the mechanism that produced it was Module 11's probe - which
    is what makes it the same group as any other mining of that probe.
    """

    CORE = "core"
    PARAMETRIC = "parametric"
    SPECIALIST = "specialist"


class EvidenceRole(str, Enum):
    """What kind of evidence an event is, which fixes what it may pay into.

    The roles are the audited channels of Audit 0008 plus the specialist
    planes. The mapping from role to term is enforced by index set, exactly as
    Module 5 enforces it - see :mod:`cover_kbc.evidence.consensus`.
    """

    #: Module 2/3 acquisition families. The only role that pays into ``F``.
    CORE_ACQUISITION = "CORE_ACQUISITION"
    #: The second family independently recalling. Pays into ``X``, never ``F``.
    CROSS_MODEL_RECALL = "CROSS_MODEL_RECALL"
    #: Module 4, shown the candidate. Pays into ``L`` (and signed ``C`` when
    #: the verdict is INVALID). Never ``F``, never ``X``, never ``I``.
    BLIND_VERIFIER = "BLIND_VERIFIER"
    #: An existence gate. Produces no candidate evidence at all.
    EXISTENCE_GATE = "EXISTENCE_GATE"
    #: A Layer-2 specialist's own acquisition probe, or its mining of Module
    #: 11. Pays into ``I``; never into ``F`` - see the engine's F policy.
    SPECIALIST_ACQUISITION = "SPECIALIST_ACQUISITION"
    #: §10.2's cross-family branch as invoked by M14/M15. Pays into ``X``.
    SPECIALIST_CROSS_FAMILY = "SPECIALIST_CROSS_FAMILY"
    #: A specialist's own gate (M14 death status, M15 public listing). Query
    #: state, never candidate acquisition.
    SPECIALIST_GATE = "SPECIALIST_GATE"
    #: Module 11 output registered for provenance and cost only.
    PARAMETRIC_MEMORY = "PARAMETRIC_MEMORY"
    #: §10.3 null classes. Query-level, never candidate-level.
    QUERY_NULL_EVIDENCE = "QUERY_NULL_EVIDENCE"

    @property
    def is_recall(self) -> bool:
        """Whether the role produced a candidate *without being shown it*.

        ``I`` counts these. A verifier that agreed with a name it was handed is
        not an independent structural source, however confident it was.
        """
        return self in (
            EvidenceRole.CORE_ACQUISITION,
            EvidenceRole.CROSS_MODEL_RECALL,
            EvidenceRole.SPECIALIST_ACQUISITION,
            EvidenceRole.SPECIALIST_CROSS_FAMILY,
        )

    @property
    def pays_f(self) -> bool:
        return self is EvidenceRole.CORE_ACQUISITION

    @property
    def pays_x(self) -> bool:
        return self in (
            EvidenceRole.CROSS_MODEL_RECALL, EvidenceRole.SPECIALIST_CROSS_FAMILY
        )

    @property
    def pays_l(self) -> bool:
        return self is EvidenceRole.BLIND_VERIFIER


class DisagreementKind(str, Enum):
    """§12.2 semantic disagreement, as the specialists already encode it.

    No embedding model, no similarity threshold, no equivalence judge: every
    kind below is a structural conflict some module already recorded.
    """

    #: M12: more than one cluster of the target quantity.
    NUMERIC_COMPETING_CLUSTERS = "NUMERIC_COMPETING_CLUSTERS"
    #: M12: a reading the contract excludes (attendance vs capacity).
    NUMERIC_QUANTITY_CONFLICT = "NUMERIC_QUANTITY_CONFLICT"
    #: M12: two unit representations that do not agree.
    NUMERIC_CROSS_UNIT_DIVERGENCE = "NUMERIC_CROSS_UNIT_DIVERGENCE"
    #: One surface described both as the target and as a contract near miss.
    TARGET_VERSUS_NEAR_MISS = "TARGET_VERSUS_NEAR_MISS"
    #: A zero-or-one relation with more than one target-like candidate.
    COMPETING_SINGLE_VALUE = "COMPETING_SINGLE_VALUE"
    #: M15: current versus historical/delisted readings of one venue.
    TEMPORAL_STATUS_CONFLICT = "TEMPORAL_STATUS_CONFLICT"
    #: M14/M15: candidate evidence against query-level null evidence.
    NULL_VERSUS_CANDIDATE = "NULL_VERSUS_CANDIDATE"


class RiskFlag(str, Enum):
    """Typed risk descriptors carried forward. **Not** factual confidence."""

    HARD_CONTRACT_VIOLATION = "HARD_CONTRACT_VIOLATION"
    NEAR_MISS_MENTION = "NEAR_MISS_MENTION"
    SINGLE_GROUP_SUPPORT = "SINGLE_GROUP_SUPPORT"
    CANDIDATE_EXPLOSION = "CANDIDATE_EXPLOSION"
    PENDING_DOWNSTREAM_CHECK = "PENDING_DOWNSTREAM_CHECK"
    AMBIGUOUS_PARSE = "AMBIGUOUS_PARSE"
    UNVERIFIED = "UNVERIFIED"


def derive_origin_event_id(
    *, model_id: str, operation_id: str, prompt_sha256: str, sample_index: int
) -> str:
    """Canonical identity of one physical evidence-producing output.

    Deterministic, never random, and deliberately **module-agnostic**: when a
    specialist mines a Module 11 record it copies exactly these four fields, so
    the derived observation lands on the same origin as the record it came
    from. That is what stops one model output being counted as two supports and
    charged twice - see the engine's origin ledger.
    """
    raw = "|".join(
        ("origin", "v1", model_id, operation_id, prompt_sha256, str(sample_index))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ConsensusEvidenceEvent:
    """One atomic evidence event, in the single representation M16 reasons over.

    ``candidate_key`` is empty for query-level events (a Module 11 record, a
    gate reading, a null-evidence class). Those carry provenance and cost but
    never support a candidate.
    """

    relation: str
    subject: str
    row_index: int

    #: M3 strict key for entities, formatted canonical value for numbers.
    candidate_key: str
    display: str

    source_module: str
    source_record_id: str
    origin_event_id: str

    plane: EvidencePlane
    independence_group: str
    role: EvidenceRole
    sign: EdgeType
    #: Categorical, never probabilistic: 1 supports, 0 does not.
    support: int

    model_id: str = ""
    model_family: str = ""
    mode: EvidenceMode = EvidenceMode.INDEPENDENT_RECALL
    facet_id: str = ""
    sample_index: int = 0
    prompt_sha256: str = ""

    #: Cost is declared by at most one representation of an origin; see
    #: :class:`ConsensusCost`.
    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    latency_ms: float | None = None
    #: Whether token counts were recorded for this origin at all. A specialist
    #: reports its spend in aggregate, not per observation, so its own probes
    #: have a knowable call count and unknowable token counts - and unknown
    #: must not be written down as zero.
    tokens_recorded: bool = True

    verified: bool = False
    #: Typed semantic annotations the source module recorded (mention kind,
    #: listing type, parse flags). Strings, because they come from four
    #: different taxonomies and flattening them into one enum would lose which
    #: taxonomy spoke.
    annotations: tuple[str, ...] = ()
    hard_violation: bool = False

    def __post_init__(self) -> None:
        if self.support not in (0, 1):
            raise ConsensusError(
                "support is categorical in Module 16; a parser's output is not "
                f"a calibrated probability, got {self.support!r}"
            )
        if self.sign is EdgeType.SUPPORT and self.support != 1:
            raise ConsensusError(
                f"a SUPPORT event must carry support=1, got {self.support}"
            )
        if self.sign is not EdgeType.SUPPORT and self.support != 0:
            raise ConsensusError(
                f"only a SUPPORT event may carry support=1; {self.sign.value} "
                "must carry 0"
            )
        if self.role.is_recall and self.mode is EvidenceMode.SHOWN_CANDIDATE:
            raise ConsensusError(
                f"role {self.role.value} counts as independent recall but the "
                "event was produced with the candidate shown; that combination "
                "would let anchored agreement buy independent support"
            )
        if self.verified and self.role is not EvidenceRole.BLIND_VERIFIER:
            raise ConsensusError(
                "only blind-verifier evidence may be marked verified; Modules "
                "11-15 never verify"
            )

    @property
    def group_key(self) -> str:
        """Plane-qualified independence group - the unit ``q_g`` is taken over."""
        return f"{self.plane.value}:{self.independence_group}"

    @property
    def event_id(self) -> str:
        """Identity of this *description* of an origin, not of the origin."""
        raw = "|".join((
            self.origin_event_id, self.source_module, self.candidate_key,
            self.sign.value, self.group_key, self.role.value, self.facet_id,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def is_query_level(self) -> bool:
        return not self.candidate_key

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "candidate_key": self.candidate_key,
            "display": self.display,
            "source_module": self.source_module,
            "source_record_id": self.source_record_id,
            "origin_event_id": self.origin_event_id,
            "event_id": self.event_id,
            "plane": self.plane.value,
            "independence_group": self.independence_group,
            "group_key": self.group_key,
            "role": self.role.value,
            "sign": self.sign.value,
            "support": self.support,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "mode": self.mode.value,
            "facet_id": self.facet_id,
            "sample_index": self.sample_index,
            "prompt_sha256": self.prompt_sha256,
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "latency_ms": self.latency_ms,
            "tokens_recorded": self.tokens_recorded,
            "verified": self.verified,
            "annotations": list(self.annotations),
            "hard_violation": self.hard_violation,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ConsensusEvidenceEvent":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            candidate_key=str(payload["candidate_key"]),
            display=str(payload["display"]),
            source_module=str(payload["source_module"]),
            source_record_id=str(payload["source_record_id"]),
            origin_event_id=str(payload["origin_event_id"]),
            plane=EvidencePlane(payload["plane"]),
            independence_group=str(payload["independence_group"]),
            role=EvidenceRole(payload["role"]),
            sign=EdgeType(payload["sign"]),
            support=int(payload["support"]),
            model_id=str(payload.get("model_id", "")),
            model_family=str(payload.get("model_family", "")),
            mode=EvidenceMode(payload.get("mode", EvidenceMode.INDEPENDENT_RECALL.value)),
            facet_id=str(payload.get("facet_id", "")),
            sample_index=int(payload.get("sample_index", 0)),
            prompt_sha256=str(payload.get("prompt_sha256", "")),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            tokens_recorded=bool(payload.get("tokens_recorded", True)),
            verified=bool(payload.get("verified", False)),
            annotations=tuple(payload.get("annotations", ())),
            hard_violation=bool(payload.get("hard_violation", False)),
        )


@dataclass(frozen=True)
class GroupSupport:
    """§12.1's ``q_g(o)`` for one independence group.

    ``q_g`` is a **max**, never a sum: ten samples of one probe are ten origin
    events and one group contribution.
    """

    group_key: str
    plane: EvidencePlane
    role: EvidenceRole
    q_g: int
    total_events: int
    origin_event_ids: tuple[str, ...]
    facets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.q_g not in (0, 1):
            raise ConsensusError(f"q_g is categorical, got {self.q_g!r}")

    @property
    def supports(self) -> bool:
        return self.q_g == 1

    def to_json(self) -> dict[str, Any]:
        return {
            "group_key": self.group_key,
            "plane": self.plane.value,
            "role": self.role.value,
            "q_g": self.q_g,
            "total_events": self.total_events,
            "origin_event_ids": list(self.origin_event_ids),
            "facets": list(self.facets),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "GroupSupport":
        return cls(
            group_key=str(payload["group_key"]),
            plane=EvidencePlane(payload["plane"]),
            role=EvidenceRole(payload["role"]),
            q_g=int(payload["q_g"]),
            total_events=int(payload["total_events"]),
            origin_event_ids=tuple(payload["origin_event_ids"]),
            facets=tuple(payload.get("facets", ())),
        )


@dataclass(frozen=True)
class SemanticDisagreement:
    """One structural conflict, with the provenance that produced it."""

    kind: DisagreementKind
    detail: str
    origin_event_ids: tuple[str, ...] = ()
    group_keys: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "origin_event_ids": list(self.origin_event_ids),
            "group_keys": list(self.group_keys),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SemanticDisagreement":
        return cls(
            kind=DisagreementKind(payload["kind"]),
            detail=str(payload["detail"]),
            origin_event_ids=tuple(payload.get("origin_event_ids", ())),
            group_keys=tuple(payload.get("group_keys", ())),
        )


@dataclass(frozen=True)
class ConsensusCost:
    """Cost over **unique origin events**.

    One physical output described by Module 11 and again by a specialist that
    mined it is one call, not two. Absent latency stays ``None``: under a
    scripted runtime nothing was timed, and reporting 0.0 ms would be a
    fabricated measurement rather than a missing one.
    """

    unique_origin_events: int = 0
    neural_calls: int = 0
    #: Summed over origins that recorded them. See ``origins_missing_tokens``.
    generated_tokens: int = 0
    prompt_tokens: int = 0
    #: Origins whose token counts nothing recorded. Reported rather than
    #: absorbed into the totals as zeros, so a reader can tell a cheap query
    #: from an unmeasured one.
    origins_missing_tokens: int = 0
    latency_ms: float | None = None
    latency_available: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "unique_origin_events": self.unique_origin_events,
            "neural_calls": self.neural_calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "origins_missing_tokens": self.origins_missing_tokens,
            "latency_ms": self.latency_ms,
            "latency_available": self.latency_available,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ConsensusCost":
        return cls(
            unique_origin_events=int(payload.get("unique_origin_events", 0)),
            neural_calls=int(payload.get("neural_calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            origins_missing_tokens=int(payload.get("origins_missing_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            latency_available=bool(payload.get("latency_available", False)),
        )


@dataclass(frozen=True)
class CandidateConsensusState:
    """§12.1's ``phi(o)`` for one candidate, with its provenance intact.

    Deliberately absent: ``accepted``, ``rejected``, ``valid``, ``final``,
    ``score``, ``rank``. The proposal gives M16 a support vector, and gives the
    verdict to Modules 17, 18 and 8.

    ``*_available`` flags exist because "not measured" and "measured as
    neutral" are different states that a bare ``0.0`` cannot distinguish - an
    unverified candidate has no verifier evidence, which is not the same as a
    verifier that could not decide.
    """

    relation: str
    subject: str
    row_index: int
    candidate_key: str
    display: str
    #: ENTITY or NUMBER, from the relation contract.
    candidate_kind: str

    group_supports: tuple[GroupSupport, ...] = ()
    contradicting_groups: tuple[str, ...] = ()
    origin_event_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()

    # -- phi(o) --------------------------------------------------------------
    f_support: float = 0.0
    l_logit: float = 0.0
    l_available: bool = False
    x_cross_model: float = 0.0
    c_contradiction: float = 0.0
    u_prompt: float = 0.0
    u_available: bool = False
    i_independent_support: int = 0
    d_semantic: float = 0.0

    #: Kept beside ``u_prompt`` rather than blended into it: three different
    #: uncertainties on three different scales (Audit 0008 §16).
    h_inc: float = 0.0
    h_ver: float | None = None

    cost: ConsensusCost = field(default_factory=ConsensusCost)
    risk_flags: tuple[RiskFlag, ...] = ()
    disagreement_details: tuple[SemanticDisagreement, ...] = ()

    hard_contract_violation: bool = False
    rejection_reason: str | None = None
    #: Total evidence events, including same-group repeats. Diagnostic: it is
    #: never an input to ``I``.
    total_support_events: int = 0
    verifier_label: str | None = None
    numeric_cluster_index: int | None = None

    consensus_version: str = CONSENSUS_VERSION

    @property
    def supporting_groups(self) -> tuple[str, ...]:
        return tuple(g.group_key for g in self.group_supports if g.supports)

    def with_disagreements(
        self, details: Sequence["SemanticDisagreement"]
    ) -> "CandidateConsensusState":
        """Attach §12.2 details and derive ``D`` from them.

        ``D`` is **binary**. §12.2 names semantic disagreement but defines no
        continuous formula, and normalising a count by an invented denominator
        would put a fabricated scale into ``phi``. The typed details carry
        everything a downstream module needs to weigh it properly, and a
        deterministic count is available from them without pretending to a
        measurement nobody defined.
        """
        from dataclasses import replace

        details = tuple(details)
        return replace(
            self, disagreement_details=details, d_semantic=1.0 if details else 0.0
        )

    @property
    def disagreement_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({d.kind.value for d in self.disagreement_details}))

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "candidate_key": self.candidate_key,
            "display": self.display,
            "candidate_kind": self.candidate_kind,
            "group_supports": [g.to_json() for g in self.group_supports],
            "supporting_groups": list(self.supporting_groups),
            "contradicting_groups": list(self.contradicting_groups),
            "origin_event_ids": list(self.origin_event_ids),
            "event_ids": list(self.event_ids),
            "F": self.f_support,
            "L": self.l_logit,
            "L_available": self.l_available,
            "X": self.x_cross_model,
            "C": self.c_contradiction,
            "U": self.u_prompt,
            "U_available": self.u_available,
            "I": self.i_independent_support,
            "D": self.d_semantic,
            "H_inc": self.h_inc,
            "H_ver": self.h_ver,
            "cost": self.cost.to_json(),
            "risk_flags": [r.value for r in self.risk_flags],
            "disagreement_kinds": list(self.disagreement_kinds),
            "disagreement_details": [d.to_json() for d in self.disagreement_details],
            "hard_contract_violation": self.hard_contract_violation,
            "rejection_reason": self.rejection_reason,
            "total_support_events": self.total_support_events,
            "verifier_label": self.verifier_label,
            "numeric_cluster_index": self.numeric_cluster_index,
            "consensus_version": self.consensus_version,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CandidateConsensusState":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            candidate_key=str(payload["candidate_key"]),
            display=str(payload["display"]),
            candidate_kind=str(payload["candidate_kind"]),
            group_supports=tuple(
                GroupSupport.from_json(g) for g in payload["group_supports"]
            ),
            contradicting_groups=tuple(payload.get("contradicting_groups", ())),
            origin_event_ids=tuple(payload.get("origin_event_ids", ())),
            event_ids=tuple(payload.get("event_ids", ())),
            f_support=float(payload["F"]),
            l_logit=float(payload["L"]),
            l_available=bool(payload["L_available"]),
            x_cross_model=float(payload["X"]),
            c_contradiction=float(payload["C"]),
            u_prompt=float(payload["U"]),
            u_available=bool(payload["U_available"]),
            i_independent_support=int(payload["I"]),
            d_semantic=float(payload["D"]),
            h_inc=float(payload.get("H_inc", 0.0)),
            h_ver=payload.get("H_ver"),
            cost=ConsensusCost.from_json(payload.get("cost", {})),
            risk_flags=tuple(RiskFlag(r) for r in payload.get("risk_flags", ())),
            disagreement_details=tuple(
                SemanticDisagreement.from_json(d)
                for d in payload.get("disagreement_details", ())
            ),
            hard_contract_violation=bool(payload.get("hard_contract_violation", False)),
            rejection_reason=payload.get("rejection_reason"),
            total_support_events=int(payload.get("total_support_events", 0)),
            verifier_label=payload.get("verifier_label"),
            numeric_cluster_index=payload.get("numeric_cluster_index"),
            consensus_version=str(payload.get("consensus_version", CONSENSUS_VERSION)),
        )


@dataclass(frozen=True)
class NullConsensusState:
    """§10.3's query-level null state, carried through unchanged.

    Audit 0021 §15A's invariant is the whole point of keeping this separate
    from candidate evidence: **failed recall is not evidence of emptiness**, so
    ``failed_recall_groups`` can never reach ``substantive_groups`` however
    many times recall failed. There is no ``final_empty``, no ``accepted_empty``
    and no ``gold_empty`` here - that decision belongs downstream.
    """

    relation: str
    subject: str
    row_index: int
    living_support: int = 0
    living_groups: tuple[str, ...] = ()
    no_known_locality_support: int = 0
    no_known_locality_groups: tuple[str, ...] = ()
    failed_recall_operations: int = 0
    failed_recall_operation_ids: tuple[str, ...] = ()
    competing_candidates: int = 0
    competing_candidate_keys: tuple[str, ...] = ()
    gate_state: str | None = None

    @property
    def substantive_groups(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.living_groups) | set(self.no_known_locality_groups)))

    @property
    def substantive_null_groups(self) -> int:
        return len(self.substantive_groups)

    @property
    def has_substantive_null_evidence(self) -> bool:
        return bool(self.substantive_groups)

    @property
    def failed_recall_only(self) -> bool:
        return bool(self.failed_recall_operations) and not self.has_substantive_null_evidence

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "living_support": self.living_support,
            "living_groups": list(self.living_groups),
            "no_known_locality_support": self.no_known_locality_support,
            "no_known_locality_groups": list(self.no_known_locality_groups),
            "failed_recall_operations": self.failed_recall_operations,
            "failed_recall_operation_ids": list(self.failed_recall_operation_ids),
            "substantive_groups": list(self.substantive_groups),
            "substantive_null_groups": self.substantive_null_groups,
            "has_substantive_null_evidence": self.has_substantive_null_evidence,
            "failed_recall_only": self.failed_recall_only,
            "competing_candidates": self.competing_candidates,
            "competing_candidate_keys": list(self.competing_candidate_keys),
            "gate_state": self.gate_state,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NullConsensusState":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            living_support=int(payload.get("living_support", 0)),
            living_groups=tuple(payload.get("living_groups", ())),
            no_known_locality_support=int(payload.get("no_known_locality_support", 0)),
            no_known_locality_groups=tuple(payload.get("no_known_locality_groups", ())),
            failed_recall_operations=int(payload.get("failed_recall_operations", 0)),
            failed_recall_operation_ids=tuple(
                payload.get("failed_recall_operation_ids", ())
            ),
            competing_candidates=int(payload.get("competing_candidates", 0)),
            competing_candidate_keys=tuple(payload.get("competing_candidate_keys", ())),
            gate_state=payload.get("gate_state"),
        )


@dataclass(frozen=True)
class NumericClusterConsensus:
    """§12.2's "numeric relations use clusters", projected from Module 12.

    Every statistic here is **copied**, never recomputed: the representative is
    M12's median and the dispersion is M12's ``D_num``. A second clustering
    implementation would be a second definition of what "the same value" means.
    """

    cluster_index: int
    representative: float
    dispersion: float
    canonical_unit: str
    values: tuple[float, ...]
    total_support: int
    independent_support: int
    independence_groups: tuple[str, ...]
    candidate_keys: tuple[str, ...] = ()
    origin_event_ids: tuple[str, ...] = ()
    competing_clusters: int = 0
    disagreement_details: tuple[SemanticDisagreement, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_index": self.cluster_index,
            "representative": self.representative,
            "dispersion": self.dispersion,
            "canonical_unit": self.canonical_unit,
            "values": list(self.values),
            "total_support": self.total_support,
            "independent_support": self.independent_support,
            "independence_groups": list(self.independence_groups),
            "candidate_keys": list(self.candidate_keys),
            "origin_event_ids": list(self.origin_event_ids),
            "competing_clusters": self.competing_clusters,
            "disagreement_details": [d.to_json() for d in self.disagreement_details],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericClusterConsensus":
        return cls(
            cluster_index=int(payload["cluster_index"]),
            representative=float(payload["representative"]),
            dispersion=float(payload["dispersion"]),
            canonical_unit=str(payload["canonical_unit"]),
            values=tuple(payload["values"]),
            total_support=int(payload["total_support"]),
            independent_support=int(payload["independent_support"]),
            independence_groups=tuple(payload["independence_groups"]),
            candidate_keys=tuple(payload.get("candidate_keys", ())),
            origin_event_ids=tuple(payload.get("origin_event_ids", ())),
            competing_clusters=int(payload.get("competing_clusters", 0)),
            disagreement_details=tuple(
                SemanticDisagreement.from_json(d)
                for d in payload.get("disagreement_details", ())
            ),
        )


@dataclass(frozen=True)
class PendingDownstreamCheck:
    """A check a specialist requested, carried forward unexecuted.

    M16 does not run reverse or counterfactual checks - that is Module 18's -
    and it does not discard the requests either.
    """

    source_module: str
    kind: str
    reason: str
    candidate: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source_module": self.source_module,
            "kind": self.kind,
            "reason": self.reason,
            "candidate": self.candidate,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PendingDownstreamCheck":
        return cls(
            source_module=str(payload["source_module"]),
            kind=str(payload["kind"]),
            reason=str(payload["reason"]),
            candidate=str(payload["candidate"]),
            detail=str(payload.get("detail", "")),
        )


@dataclass(frozen=True)
class QueryConsensusResult:
    """Everything M16 produced for one query.

    Deliberately absent: a prediction, a final set, an accepted set, a rejected
    set, a stopping decision, a ranking.
    """

    consensus_version: str
    relation: str
    subject: str
    row_index: int
    applicable_specialist: str
    upstream_versions: Mapping[str, str] = field(default_factory=dict)

    candidates: tuple[CandidateConsensusState, ...] = ()
    null_state: NullConsensusState | None = None
    numeric_clusters: tuple[NumericClusterConsensus, ...] = ()
    unassigned_numeric_keys: tuple[str, ...] = ()
    pending_checks: tuple[PendingDownstreamCheck, ...] = ()
    query_events: tuple[ConsensusEvidenceEvent, ...] = ()
    cost: ConsensusCost = field(default_factory=ConsensusCost)
    query_risk: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def unique_origin_events(self) -> int:
        return self.cost.unique_origin_events

    def to_json(self) -> dict[str, Any]:
        return {
            "consensus_version": self.consensus_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "applicable_specialist": self.applicable_specialist,
            "upstream_versions": dict(self.upstream_versions),
            "candidates": [c.to_json() for c in self.candidates],
            "null_state": self.null_state.to_json() if self.null_state else None,
            "numeric_clusters": [c.to_json() for c in self.numeric_clusters],
            "unassigned_numeric_keys": list(self.unassigned_numeric_keys),
            "pending_checks": [p.to_json() for p in self.pending_checks],
            "query_events": [e.to_json() for e in self.query_events],
            "cost": self.cost.to_json(),
            "query_risk": dict(self.query_risk),
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "QueryConsensusResult":
        null_state = payload.get("null_state")
        return cls(
            consensus_version=str(payload["consensus_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            applicable_specialist=str(payload["applicable_specialist"]),
            upstream_versions=dict(payload.get("upstream_versions", {})),
            candidates=tuple(
                CandidateConsensusState.from_json(c) for c in payload["candidates"]
            ),
            null_state=NullConsensusState.from_json(null_state) if null_state else None,
            numeric_clusters=tuple(
                NumericClusterConsensus.from_json(c)
                for c in payload.get("numeric_clusters", ())
            ),
            unassigned_numeric_keys=tuple(payload.get("unassigned_numeric_keys", ())),
            pending_checks=tuple(
                PendingDownstreamCheck.from_json(p)
                for p in payload.get("pending_checks", ())
            ),
            query_events=tuple(
                ConsensusEvidenceEvent.from_json(e)
                for e in payload.get("query_events", ())
            ),
            cost=ConsensusCost.from_json(payload.get("cost", {})),
            query_risk=dict(payload.get("query_risk", {})),
            errors=tuple(payload.get("errors", ())),
        )


def sort_events(events: Sequence[ConsensusEvidenceEvent]) -> tuple[ConsensusEvidenceEvent, ...]:
    """Deterministic order, so consensus never depends on arrival order."""
    return tuple(sorted(
        events,
        key=lambda e: (
            e.candidate_key, e.group_key, e.role.value, e.sign.value,
            e.origin_event_id, e.event_id,
        ),
    ))


__all__ = [
    "CONSENSUS_VERSION",
    "CandidateConsensusState",
    "ConsensusCost",
    "ConsensusError",
    "ConsensusEvidenceEvent",
    "ConsensusProvenanceError",
    "DisagreementKind",
    "EvidencePlane",
    "EvidenceRole",
    "GroupSupport",
    "NullConsensusState",
    "NumericClusterConsensus",
    "PendingDownstreamCheck",
    "QueryConsensusResult",
    "RiskFlag",
    "SemanticDisagreement",
    "derive_origin_event_id",
    "sort_events",
]
