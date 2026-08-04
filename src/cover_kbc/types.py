"""Core typed data model shared by every COVER-KBC module.

These are the structures referenced in section 23.1 of the architecture
specification, extended with the provenance fields that Milestone 1 requires.

Design rules enforced here:

* A :class:`Candidate` can never exist without provenance - every candidate
  carries the generation records that produced it.
* Evidence is attributed to an *independence group*, not to a raw run.  Three
  samples of the same view are one evidence family, not three.
* Nothing in this module imports a model backend; the data model is
  model-agnostic on purpose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Sequence


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class ProgramType(str, Enum):
    """The four typed inference programs (spec Table 4)."""

    SMALL_SET = "SMALL_SET"
    NULL_SINGLE = "NULL_SINGLE"
    NUMERIC = "NUMERIC"
    LARGE_OPEN_SET = "LARGE_OPEN_SET"


class OutputType(str, Enum):
    """What a single object entity denotes for a relation."""

    ENTITY = "ENTITY"
    NUMBER = "NUMBER"


class Cardinality(str, Enum):
    """Expected size regime of the gold object set."""

    ZERO_OR_ONE = "ZERO_OR_ONE"
    EXACTLY_ONE = "EXACTLY_ONE"
    ZERO_OR_MANY_SMALL = "ZERO_OR_MANY_SMALL"
    ZERO_OR_MANY_LARGE = "ZERO_OR_MANY_LARGE"


class ViewFamily(str, Enum):
    """Elicitation view families (spec section 7.2, Table 5).

    The first four are *candidate-acquisition* mechanisms: each one is a
    structurally different way of getting object candidates out of the model,
    and each maps to its own independence group.

    ``GATE`` is deliberately not one of them. An existence gate asks a yes/no
    question and returns no candidates at all, so counting it as an acquisition
    mechanism would inflate the coverage denominator ``m(o)`` with a mechanism
    that can never support a candidate.
    """

    DIRECT = "direct"
    STRUCTURAL = "structural"
    #: Relation-focused description: generate context to trigger parametric
    #: memory, then extract candidates from it. Two stages, one mechanism.
    DESCRIPTION = "description"
    CONTRASTIVE = "contrastive"
    MISSINGNESS = "missingness"
    #: Reverse / alternate framing: a candidate-conditioned re-ask, structurally
    #: different from subject-only enumeration.
    REVERSE = "reverse"
    GATE = "gate"


class ModelRole(str, Enum):
    """What a neural component is for.

    Roles are first-class architecture, not interchangeable backends: the
    enumerator is chosen for recall over a large candidate universe, the
    verifier for compact calibrated judgement.
    """

    ENUMERATOR = "enumerator"
    VERIFIER = "verifier"
    STUB = "stub"


class EvidenceMode(str, Enum):
    """How a model came to support a candidate.

    The distinction is load-bearing: a model that *independently produced* a
    name has told us something a model merely *agreeing with a name it was
    shown* has not. Anchoring makes shown-candidate agreement much cheaper to
    obtain, so the two must never be counted alike.
    """

    INDEPENDENT_RECALL = "independent_recall"
    SHOWN_CANDIDATE = "shown_candidate"


class IndependenceGroup(str, Enum):
    """Evidence families treated as mutually independent supports.

    Repeated runs of the same view share an independence group and therefore
    contribute *one* independent support, however many times they are sampled.
    """

    DIRECT_RECALL = "DIRECT_RECALL"
    STRUCTURAL_DECOMPOSITION = "STRUCTURAL_DECOMPOSITION"
    #: Candidates extracted from self-generated relation-focused context.
    RELATION_FOCUSED_DESCRIPTION = "RELATION_FOCUSED_DESCRIPTION"
    CONTRASTIVE_SEPARATION = "CONTRASTIVE_SEPARATION"
    MISSINGNESS_SEARCH = "MISSINGNESS_SEARCH"
    #: Candidate-conditioned alternate framing. Distinct from BLIND_VERIFIER:
    #: it acquires a free-text answer, it does not score fixed labels.
    REVERSE_ALTERNATE = "REVERSE_ALTERNATE"
    #: The verifier model judging a candidate it was *shown*.
    BLIND_VERIFIER = "BLIND_VERIFIER"
    #: The verifier-family model *independently recalling* objects, having been
    #: shown no candidate list. A genuinely separate source of evidence.
    CROSS_MODEL_RECALL = "CROSS_MODEL_RECALL"
    #: Provenance for existence-gate calls. Never carries candidate evidence and
    #: is never an eligible group, so it cannot dilute ``q(o) = g(o) / m(o)``.
    EXISTENCE_GATE = "EXISTENCE_GATE"
    # Reserved for the experimental factual-decoding branch.
    FACTUAL_DECODING = "FACTUAL_DECODING"


class EdgeType(str, Enum):
    """Signed evidence edge (spec section 9.2)."""

    SUPPORT = "SUPPORT"
    CONTRADICT = "CONTRADICT"
    UNKNOWN = "UNKNOWN"


class VerificationLabel(str, Enum):
    """Three-way blind verifier labels (spec section 10.2)."""

    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class CandidateStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class VerificationTier(str, Enum):
    """Deterministic verification tiers (spec section 10.5).

    Assigned before any verifier call is spent, from evidence already held.
    """

    HARD_REJECT = "HARD_REJECT"
    AUTO_ACCEPT = "AUTO_ACCEPT"
    VERIFY = "VERIFY"
    ADVERSARIAL_VERIFY = "ADVERSARIAL_VERIFY"
    UNRESOLVED = "UNRESOLVED"


class EmptyReason(str, Enum):
    """Why a query produced no objects.

    These must never be conflated in analysis: a confident negative gate is a
    correct answer, while an abstention is a coverage failure.
    """

    NOT_EMPTY = "NOT_EMPTY"
    CONFIDENT_NEGATIVE_GATE = "confident_negative_gate"
    UNRESOLVED_ABSTENTION = "unresolved_abstention"
    NO_CANDIDATE_GENERATED = "no_candidate_generated"
    CANDIDATE_REJECTED = "candidate_rejected"
    PIPELINE_ERROR = "pipeline_error"


# --------------------------------------------------------------------------
# Query and generation provenance
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Query:
    """One official ``(SubjectEntity, Relation)`` input pair.

    ``row_index`` preserves the position of the row in the official file so
    predictions can be written back in the original order.
    """

    subject: str
    relation: str
    row_index: int = -1

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.relation)


@dataclass(frozen=True)
class DecodeProfile:
    """Decoding configuration for one model call.

    ``temperature == 0`` means greedy/deterministic decoding.
    """

    name: str = "greedy"
    temperature: float = 0.0
    top_p: float = 1.0
    max_new_tokens: int = 256
    seed: int | None = None

    @property
    def deterministic(self) -> bool:
        return self.temperature <= 0.0


@dataclass
class GenerationRecord:
    """Full provenance for a single model call (spec section 7.4).

    Every candidate must point back to at least one of these.
    """

    record_id: str
    query: Query
    view_id: str
    view_family: ViewFamily
    independence_group: IndependenceGroup
    run_id: int
    model_id: str
    prompt: str
    prompt_hash: str
    raw_output: str
    decode_profile: DecodeProfile
    #: Sub-partition of one mechanism (e.g. an award decade). Provenance only:
    #: facets never count as extra independent support.
    facet_id: str = ""
    model_family: str = ""
    model_role: ModelRole = ModelRole.ENUMERATOR
    #: Which stage of a multi-stage acquisition produced this record, e.g.
    #: ``"description"`` then ``"extraction"``. Empty for single-stage views.
    stage: str = ""
    #: The record this one was derived from - the description whose prose was
    #: mined for candidates. Keeps the context/extraction chain traceable.
    source_record_id: str = ""
    #: The candidate a candidate-conditioned view was asked about.
    source_candidate_key: str = ""
    parsed_values: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    latency_ms: float | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["query"] = {"subject": self.query.subject, "relation": self.query.relation}
        payload["view_family"] = self.view_family.value
        payload["independence_group"] = self.independence_group.value
        payload["model_role"] = self.model_role.value
        payload["decode_profile"] = asdict(self.decode_profile)
        return payload


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


@dataclass
class Evidence:
    """One signed edge from an evidence event to a candidate.

    ``record_id`` links back to the :class:`GenerationRecord` (or verification
    call) that produced the edge, which keeps the graph auditable. There is no
    anonymous evidence: every edge names the event that produced it.
    """

    def derive_edge_id(self) -> str:
        """Deterministic id from the edge's own identifying fields."""
        import hashlib

        raw = "|".join(
            (
                self.candidate_key,
                self.record_id,
                self.edge_type.value,
                self.independence_group.value,
                str(self.run_id),
                self.view_id,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    candidate_key: str
    edge_type: EdgeType
    independence_group: IndependenceGroup
    view_id: str
    model_id: str
    run_id: int
    record_id: str
    #: Deterministic identity of this edge, so a duplicate insertion is
    #: detectable and a reloaded graph keeps the same edges.
    edge_id: str = ""
    model_family: str = ""
    mode: EvidenceMode = EvidenceMode.INDEPENDENT_RECALL
    valid_prob: float | None = None
    invalid_prob: float | None = None
    unknown_prob: float | None = None
    token_cost: int = 0

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["edge_type"] = self.edge_type.value
        payload["independence_group"] = self.independence_group.value
        payload["mode"] = self.mode.value
        return payload


@dataclass
class EvidenceGroup:
    """All evidence for one candidate coming from a single independence group.

    This is the unit that counts as *one* independent support, regardless of
    how many raw runs it contains.
    """

    independence_group: IndependenceGroup
    supports: list[Evidence] = field(default_factory=list)
    contradictions: list[Evidence] = field(default_factory=list)
    unknowns: list[Evidence] = field(default_factory=list)

    @property
    def raw_support_count(self) -> int:
        """Number of raw supporting events - explicitly *not* an independence count."""
        return len(self.supports)

    @property
    def supports_candidate(self) -> bool:
        return bool(self.supports)

    @property
    def contradicts_candidate(self) -> bool:
        return bool(self.contradictions)

    def add(self, evidence: Evidence) -> None:
        if evidence.edge_type is EdgeType.SUPPORT:
            self.supports.append(evidence)
        elif evidence.edge_type is EdgeType.CONTRADICT:
            self.contradictions.append(evidence)
        else:
            self.unknowns.append(evidence)

    def all_evidence(self) -> list[Evidence]:
        return [*self.supports, *self.contradictions, *self.unknowns]


@dataclass
class VerificationResult:
    """Outcome of a blind three-way verification call (spec section 10).

    ``valid_prob`` / ``invalid_prob`` / ``unknown_prob`` are **calibrated
    verifier-label probabilities, not probabilities of factual truth**.  They
    say how the model's label head responds under a fixed template once the
    template's own bias has been subtracted; nothing more.
    """

    candidate_key: str
    label: VerificationLabel
    valid_prob: float | None = None
    invalid_prob: float | None = None
    unknown_prob: float | None = None
    raw_logits: dict[str, float] | None = None
    calibrated_logits: dict[str, float] | None = None
    bias_logits: dict[str, float] | None = None
    calibrated: bool = False
    margin: float | None = None
    entropy: float | None = None
    prompt_disagreement: float | None = None
    template_id: str = ""
    num_templates: int = 1
    model_id: str = ""
    model_family: str = ""
    record_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "selected_label": self.label.value,
            "raw_label_logits": self.raw_logits,
            "calibrated_label_logits": self.calibrated_logits,
            "bias_logits": self.bias_logits,
            "p_valid": self.valid_prob,
            "p_invalid": self.invalid_prob,
            "p_unknown": self.unknown_prob,
            "valid_margin": self.margin,
            "entropy": self.entropy,
            "prompt_disagreement": self.prompt_disagreement,
            "calibrated": self.calibrated,
            "template_id": self.template_id,
            "num_templates": self.num_templates,
            "model_id": self.model_id,
            "model_family": self.model_family,
        }

    @property
    def edge_type(self) -> EdgeType:
        if self.label is VerificationLabel.VALID:
            return EdgeType.SUPPORT
        if self.label is VerificationLabel.INVALID:
            return EdgeType.CONTRADICT
        return EdgeType.UNKNOWN

    def log_odds(self, epsilon: float = 1e-6) -> float | None:
        """``L(o)`` from spec section 11.2, or ``None`` without probabilities."""
        if self.valid_prob is None:
            return None
        invalid = self.invalid_prob or 0.0
        unknown = self.unknown_prob or 0.0
        return math.log((self.valid_prob + epsilon) / (invalid + unknown + epsilon))


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


@dataclass
class CandidateScore:
    """Component-wise breakdown of ``S(o)`` (spec section 11.2).

    Every term is stored separately, never just the total: a scalar alone makes
    it impossible to tell a candidate carried by broad structural support from
    one carried by a single confident verifier call.

        S(o) = a*F(o) + b*L(o) + c*X(o) - d*C(o) - e*U(o)
    """

    support: float = 0.0        # F(o) independent mechanism support
    logit: float = 0.0          # L(o) calibrated verifier log-odds
    cross_model: float = 0.0    # X(o) cross-model support (0 this milestone)
    contradiction: float = 0.0  # C(o) explicit contradiction
    disagreement: float = 0.0   # U(o) verifier/prompt disagreement
    weights: dict[str, float] = field(default_factory=dict)
    total: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "F_support": self.support,
            "L_logit": self.logit,
            "X_cross_model": self.cross_model,
            "C_contradiction": self.contradiction,
            "U_disagreement": self.disagreement,
            "weights": dict(self.weights),
            "total": self.total,
        }


@dataclass
class Candidate:
    """An atomic candidate object entity with full provenance.

    ``key`` is the internal canonical form used for deduplication (derived from
    the official evaluator's normalisation).  ``display_value`` is the single
    human-readable surface form that will be emitted - the evaluator penalises
    multiple surface forms of the same entity, so exactly one is kept.
    """

    key: str
    display_value: str
    relation: str
    output_type: OutputType = OutputType.ENTITY
    numeric_value: float | None = None
    unit: str | None = None
    #: Soft grouping hint (strict key plus English article folding). Recorded
    #: for later modules and for output dedup; it is NEVER the identity - the
    #: graph keys on ``strict_key``, so a wrong fold cannot destroy an entity.
    alias_hint: str = ""
    #: The numeral exactly as the model wrote it, before conversion.
    raw_text: str = ""
    #: The unit the model expressed the value in, before conversion to ``unit``.
    source_unit: str | None = None
    groups: dict[IndependenceGroup, EvidenceGroup] = field(default_factory=dict)
    verifications: list[VerificationResult] = field(default_factory=list)
    surface_forms: list[str] = field(default_factory=list)
    record_ids: list[str] = field(default_factory=list)
    status: CandidateStatus = CandidateStatus.UNRESOLVED
    score: float = 0.0
    score_breakdown: CandidateScore = field(default_factory=CandidateScore)
    tier: VerificationTier = VerificationTier.UNRESOLVED
    strict_key: str = ""
    facet_ids: list[str] = field(default_factory=list)
    rejection_reason: str | None = None

    # -- evidence accounting -------------------------------------------------

    def add_evidence(self, evidence: Evidence) -> None:
        group = self.groups.setdefault(
            evidence.independence_group, EvidenceGroup(evidence.independence_group)
        )
        group.add(evidence)
        if evidence.record_id and evidence.record_id not in self.record_ids:
            self.record_ids.append(evidence.record_id)

    def add_surface_form(self, surface: str) -> None:
        if surface and surface not in self.surface_forms:
            self.surface_forms.append(surface)

    @property
    def independent_support(self) -> int:
        """``g(o)``: number of *distinct* independence groups that support it."""
        return sum(1 for g in self.groups.values() if g.supports_candidate)

    @property
    def raw_support_count(self) -> int:
        """Total supporting events - kept separate from independence on purpose."""
        return sum(g.raw_support_count for g in self.groups.values())

    @property
    def contradiction_count(self) -> int:
        return sum(len(g.contradictions) for g in self.groups.values())

    @property
    def supporting_groups(self) -> list[IndependenceGroup]:
        return [g.independence_group for g in self.groups.values() if g.supports_candidate]

    @property
    def num_facets(self) -> int:
        """Distinct facets that mentioned this candidate.

        A *diagnostic only*. Facets partition one mechanism (e.g. award decades
        inside STRUCTURAL_DECOMPOSITION), so this must never be substituted for
        ``independent_support`` when scoring - that would let a single mechanism
        sliced five ways masquerade as five independent corroborations.
        """
        return len(self.facet_ids)

    def add_facet(self, facet_id: str) -> None:
        if facet_id and facet_id not in self.facet_ids:
            self.facet_ids.append(facet_id)

    def coverage(self, eligible_groups: Sequence[IndependenceGroup]) -> float:
        """``q(o) = g(o) / m(o)`` from spec section 11.1."""
        eligible = list(dict.fromkeys(eligible_groups))
        if not eligible:
            return 0.0
        hit = sum(
            1
            for group in eligible
            if group in self.groups and self.groups[group].supports_candidate
        )
        return hit / len(eligible)

    def all_evidence(self) -> list[Evidence]:
        return [e for g in self.groups.values() for e in g.all_evidence()]

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_value": self.display_value,
            "relation": self.relation,
            "output_type": self.output_type.value,
            "numeric_value": self.numeric_value,
            "unit": self.unit,
            "alias_hint": self.alias_hint,
            "raw_text": self.raw_text,
            "source_unit": self.source_unit,
            "strict_key": self.strict_key,
            "status": self.status.value,
            "tier": self.tier.value,
            "score": self.score,
            "score_breakdown": self.score_breakdown.to_json(),
            "facet_ids": list(self.facet_ids),
            "rejection_reason": self.rejection_reason,
            "independent_support": self.independent_support,
            "raw_support_count": self.raw_support_count,
            "contradiction_count": self.contradiction_count,
            "supporting_groups": [g.value for g in self.supporting_groups],
            "num_facets": self.num_facets,
            "surface_forms": self.surface_forms,
            "record_ids": self.record_ids,
            "evidence": [e.to_json() for e in self.all_evidence()],
            "verifications": [v.to_json() for v in self.verifications],
        }


# --------------------------------------------------------------------------
# Program state and predictions
# --------------------------------------------------------------------------


@dataclass
class Budget:
    """Hard inference budget for one query."""

    max_calls: int = 12
    max_generated_tokens: int = 6000
    calls_used: int = 0
    generated_tokens_used: int = 0

    @property
    def calls_left(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    @property
    def tokens_left(self) -> int:
        return max(0, self.max_generated_tokens - self.generated_tokens_used)

    @property
    def exhausted(self) -> bool:
        return self.calls_left <= 0 or self.tokens_left <= 0

    def charge(self, calls: int = 1, generated_tokens: int = 0) -> None:
        self.calls_used += calls
        self.generated_tokens_used += generated_tokens


@dataclass
class ProgramState:
    """Controller state ``x_t`` for one query (spec section 3).

    Milestone 1 populates the graph/budget/history fields.  ``residual_coverage``
    and ``recent_yield`` are placeholders for the Milestone 3 RCSE.
    """

    query: Query
    program_type: ProgramType
    budget: Budget = field(default_factory=Budget)
    records: list[GenerationRecord] = field(default_factory=list)
    executed_views: list[str] = field(default_factory=list)
    residual_coverage: float = 1.0
    recent_yield: dict[str, float] = field(default_factory=dict)
    stopped_reason: str | None = None

    def note_record(self, record: GenerationRecord) -> None:
        self.records.append(record)
        self.executed_views.append(record.view_id)
        self.budget.charge(calls=1, generated_tokens=record.generated_tokens or 0)


@dataclass
class Prediction:
    """Final output row for one query, plus the audit trail behind it."""

    subject: str
    relation: str
    object_entities: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    row_index: int = -1
    stopped_reason: str | None = None
    calls_used: int = 0
    generated_tokens_used: int = 0
    prompt_tokens_used: int = 0
    empty_reason: EmptyReason = EmptyReason.NOT_EMPTY
    verification_calls: int = 0

    def to_official_row(self) -> dict[str, Any]:
        """Serialise exactly the three official fields, nothing else."""
        return {
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "ObjectEntities": list(self.object_entities),
        }

    def to_trace(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "ObjectEntities": list(self.object_entities),
            "row_index": self.row_index,
            "stopped_reason": self.stopped_reason,
            "calls_used": self.calls_used,
            "generated_tokens_used": self.generated_tokens_used,
            "prompt_tokens_used": self.prompt_tokens_used,
            "empty_reason": self.empty_reason.value,
            "verification_calls": self.verification_calls,
            "candidates": [c.to_json() for c in self.candidates],
        }
