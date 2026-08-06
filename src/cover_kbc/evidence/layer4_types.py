"""Layer-4 verification-evidence integration - public contract.

**This is a layer boundary, not a new numbered module.** Modules 16, 17 and 18
each produce their own audited state; nothing here re-verifies, re-scores or
re-decides anything. It projects what those three already recorded into one
deterministic evidence view for Module 19 to consume.

    M16 atomic consensus
          |
          +----------------------+
          |                      |
          v                      v
        M17                     M18
  calibrated blind        new structural
  verification            evidence checks
          \\                    /
           v                  v
            Layer-4 evidence state
                     |
                     v
                 future M19

Three things this view is **not**:

* **Not a decision.** No accepted set, no rejected set, no final score, no
  ranking, no stopping rule. Module 19 estimates coverage from it; Modules
  20/21 decide what to do; Module 8 finalizes.
* **Not a fusion.** ``F``, ``L``, ``C``, ``U`` and ``D`` are copied from Module
  16 unchanged. Where the architecture has no audited rule for combining a
  Module 4 verifier reading with a Module 17 specialist reading, the two are
  reported **side by side** rather than averaged into a number nobody defined.
* **Not new evidence.** It spends zero calls. Unavailable stays unavailable.

Every "not measured" state is distinguishable from every "measured and
uncertain" state, because Module 19 needs to tell those apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.evidence.consensus_types import (
    CandidateConsensusState,
    NullConsensusState,
    NumericClusterConsensus,
)

#: Bumped when the meaning of any field or mapping here changes.
INTEGRATION_VERSION = "layer4-v1"


class Layer4IntegrationError(RuntimeError):
    """The three layers disagree about what they describe."""


class Layer4ProvenanceError(Layer4IntegrationError):
    """Two records claim one physical call but describe different sources.

    Never repaired silently: if one call is described two ways and the
    descriptions conflict, the provenance is wrong.
    """


class VerifierAvailability(str, Enum):
    """Whether Module 17 evidence exists for a target - and why not.

    ``NOT_REQUESTED`` and ``UNAVAILABLE`` are different states, and neither is
    "the verifier was unsure". Collapsing them to a zero would tell Module 19
    that a target was measured when it was not.
    """

    NOT_REQUESTED = "NOT_REQUESTED"
    #: Requested, but no usable calibrated reading came back.
    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"


class CheckExecutionStatus(str, Enum):
    """What happened to one Module 18 check. **Execution, never truth.**

    ``RESOLVED`` means a check produced a readable outcome, not that a fact was
    settled - see Audit 0027 §36.
    """

    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ELIGIBLE_NOT_SCHEDULED = "ELIGIBLE_NOT_SCHEDULED"
    FAILED = "FAILED"
    #: Ran and read cleanly, but the model declined to commit.
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"


class StructuralOutcome(str, Enum):
    """A Module 18 check's signed reading, in Module 16's own vocabulary.

    ``ALTERNATE_RECOVERED`` exists because a fourth reading is genuinely
    different from the other three: a masked reconstruction that returned
    *another qualifying object* of a set-valued relation. It read cleanly, it is
    not support for this target, and it is emphatically **not** evidence that
    this target is wrong - for a relation that admits many objects, naming a
    second one says nothing about the first. See Audit 0027 §20A.
    """

    SUPPORT = "SUPPORT"
    CONTRADICT = "CONTRADICT"
    ALTERNATE_RECOVERED = "ALTERNATE_RECOVERED"
    UNRESOLVED = "UNRESOLVED"

    @property
    def contradicts(self) -> bool:
        """Only an explicit CONTRADICT is evidence against the target."""
        return self is StructuralOutcome.CONTRADICT


class CrossModelCredit(str, Enum):
    """Whether one Module 18 record may credit ``X`` - and if not, why.

    §14 says a candidate recalled naturally in an independent probe should
    increase ``X``; Audit 0008 defines ``X`` as *cross-model* independent
    recall. Both hold only for a hidden-candidate probe answered by a family
    that did not already produce the candidate. Every other outcome is named,
    so a reader can see which condition failed.
    """

    #: The mechanism shows the candidate, so agreement is anchored.
    SHOWN_CANDIDATE = "SHOWN_CANDIDATE"
    #: Hidden, but not an independent recall of the candidate.
    NOT_INDEPENDENT_RECALL = "NOT_INDEPENDENT_RECALL"
    #: Hidden and independent, but the candidate was not named.
    TARGET_NOT_RECALLED = "TARGET_NOT_RECALLED"
    #: The answering family already produced the candidate.
    SAME_FAMILY = "SAME_FAMILY"
    #: Named by a probe, but no prior family held it at all - this is its first
    #: discovery. One family is not cross-family corroboration, so no credit.
    FIRST_DISCOVERY = "FIRST_DISCOVERY"
    #: The candidate's prior families are not knowable from the inputs, so no
    #: credit is given. A false negative is safer than an unsupported credit.
    UNRESOLVED_PROVENANCE = "UNRESOLVED_PROVENANCE"
    CREDITED = "CREDITED"

    @property
    def credits(self) -> bool:
        return self is CrossModelCredit.CREDITED


@dataclass(frozen=True)
class SpecialistVerifierEvidence:
    """Module 17's calibrated reading for one target, projected whole.

    Everything Module 17 measured is kept: the mean distribution, the margin,
    the entropy and **both** §13.1 disagreement channels. It is deliberately
    not reduced to an argmax, and it is deliberately not merged into Module
    16's ``L`` - see Audit 0027 §17.

    ``readings`` counts factual target readings. Content-free calibration
    controls are counted in ``control_calls`` and contribute **zero** factual
    evidence: they measure prompt-label bias, not the world.
    """

    availability: VerifierAvailability = VerifierAvailability.NOT_REQUESTED
    distribution: Mapping[str, float] | None = None
    argmax_label: str | None = None
    valid_margin: float | None = None
    verifier_entropy: float | None = None
    template_disagreement: float | None = None
    label_order_disagreement: float | None = None
    max_valid_shift: float | None = None
    #: Factual target readings, i.e. (phrasing x label order) measurements.
    readings: int = 0
    #: Content-free control measurements actually paid for.
    control_calls: int = 0
    physical_calls: int = 0
    #: One request is one mechanism, whatever it was measured with.
    independence_group: str = ""
    contradicts: bool = False
    contract_version: str = ""
    verification_version: str = ""

    @property
    def available(self) -> bool:
        return self.availability is VerifierAvailability.AVAILABLE

    def to_json(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "calibrated_label_distribution": (
                dict(self.distribution) if self.distribution else None
            ),
            "argmax_label": self.argmax_label,
            "valid_margin": self.valid_margin,
            "verifier_entropy": self.verifier_entropy,
            "template_disagreement": self.template_disagreement,
            "label_order_disagreement": self.label_order_disagreement,
            "max_valid_shift": self.max_valid_shift,
            "readings": self.readings,
            "control_calls": self.control_calls,
            "physical_calls": self.physical_calls,
            "independence_group": self.independence_group,
            "contradicts": self.contradicts,
            "contract_version": self.contract_version,
            "verification_version": self.verification_version,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SpecialistVerifierEvidence":
        return cls(
            availability=VerifierAvailability(payload["availability"]),
            distribution=payload.get("calibrated_label_distribution"),
            argmax_label=payload.get("argmax_label"),
            valid_margin=payload.get("valid_margin"),
            verifier_entropy=payload.get("verifier_entropy"),
            template_disagreement=payload.get("template_disagreement"),
            label_order_disagreement=payload.get("label_order_disagreement"),
            max_valid_shift=payload.get("max_valid_shift"),
            readings=int(payload.get("readings", 0)),
            control_calls=int(payload.get("control_calls", 0)),
            physical_calls=int(payload.get("physical_calls", 0)),
            independence_group=str(payload.get("independence_group", "")),
            contradicts=bool(payload.get("contradicts", False)),
            contract_version=str(payload.get("contract_version", "")),
            verification_version=str(payload.get("verification_version", "")),
        )


@dataclass(frozen=True)
class StructuralCheckEvidence:
    """One executed Module 18 check, mapped to a signed reading.

    The mapping is deterministic and conservative: an unresolved answer, a
    malformed answer, a failed call and an absent recall are **never**
    contradictions.
    """

    check_kind: str
    independence_group: str
    outcome: StructuralOutcome
    status: CheckExecutionStatus
    origin_event_id: str
    model_id: str = ""
    model_family: str = ""
    candidate_shown: bool = True
    cross_model_credit: CrossModelCredit = CrossModelCredit.SHOWN_CANDIDATE
    counterfactual_class: str = ""
    parse_status: str = ""
    raw_outcome: str = ""
    #: What a key-condition reconstruction actually returned, kept whether or
    #: not it matched the target. For a set-valued relation this is the
    #: alternate object, preserved as provenance and nothing more.
    recovered_value: str = ""
    calls: int = 0
    error: str | None = None

    @property
    def supports(self) -> bool:
        return self.outcome is StructuralOutcome.SUPPORT

    @property
    def contradicts(self) -> bool:
        return self.outcome.contradicts

    def to_json(self) -> dict[str, Any]:
        return {
            "check_kind": self.check_kind,
            "independence_group": self.independence_group,
            "outcome": self.outcome.value,
            "status": self.status.value,
            "origin_event_id": self.origin_event_id,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "candidate_shown": self.candidate_shown,
            "cross_model_credit": self.cross_model_credit.value,
            "counterfactual_class": self.counterfactual_class,
            "parse_status": self.parse_status,
            "raw_outcome": self.raw_outcome,
            "recovered_value": self.recovered_value,
            "calls": self.calls,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "StructuralCheckEvidence":
        return cls(
            check_kind=str(payload["check_kind"]),
            independence_group=str(payload["independence_group"]),
            outcome=StructuralOutcome(payload["outcome"]),
            status=CheckExecutionStatus(payload["status"]),
            origin_event_id=str(payload["origin_event_id"]),
            model_id=str(payload.get("model_id", "")),
            model_family=str(payload.get("model_family", "")),
            candidate_shown=bool(payload.get("candidate_shown", True)),
            cross_model_credit=CrossModelCredit(payload["cross_model_credit"]),
            counterfactual_class=str(payload.get("counterfactual_class", "")),
            parse_status=str(payload.get("parse_status", "")),
            raw_outcome=str(payload.get("raw_outcome", "")),
            recovered_value=str(payload.get("recovered_value", "")),
            calls=int(payload.get("calls", 0)),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class StructuralGroupSupport:
    """§12.1's ``q_g`` over one Layer-4 structural group.

    A **max**, never a sum: ten reverse checks are ten origins and one group
    contribution, and three counterfactual classes are three provenance
    entries and one group.
    """

    group_key: str
    q_g: int
    total_events: int
    origin_event_ids: tuple[str, ...] = ()
    #: Whether this group is independent *recall* of the candidate. Only a
    #: hidden-candidate probe is: a shown-candidate check is anchored, exactly
    #: as Module 4's shown-candidate agreement is.
    is_recall: bool = False

    def __post_init__(self) -> None:
        if self.q_g not in (0, 1):
            raise Layer4IntegrationError(f"q_g is categorical, got {self.q_g!r}")

    @property
    def supports(self) -> bool:
        return self.q_g == 1

    def to_json(self) -> dict[str, Any]:
        return {
            "group_key": self.group_key,
            "q_g": self.q_g,
            "total_events": self.total_events,
            "origin_event_ids": list(self.origin_event_ids),
            "is_recall": self.is_recall,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "StructuralGroupSupport":
        return cls(
            group_key=str(payload["group_key"]),
            q_g=int(payload["q_g"]),
            total_events=int(payload["total_events"]),
            origin_event_ids=tuple(payload.get("origin_event_ids", ())),
            is_recall=bool(payload.get("is_recall", False)),
        )


@dataclass(frozen=True)
class CandidateEvidenceOverlay:
    """One candidate's Module 16 state, plus what Layers 4 added to it.

    The ``base_*`` fields are Module 16's, copied and **never** recomputed.
    ``layer4_i`` extends ``I`` with hidden-candidate structural recall groups
    only; ``layer4_x`` may rise above ``base_x`` only through the audited
    cross-model rule. ``F`` has no Layer-4 counterpart at all, deliberately.
    """

    candidate_key: str
    display: str
    candidate_kind: str = "ENTITY"

    # -- Module 16, copied verbatim -----------------------------------------
    base_f: float = 0.0
    base_l: float = 0.0
    base_l_available: bool = False
    base_x: float = 0.0
    base_c: float = 0.0
    base_u: float = 0.0
    base_u_available: bool = False
    base_i: int = 0
    base_d: float = 0.0
    base_group_supports: tuple[str, ...] = ()
    base_contradicting_groups: tuple[str, ...] = ()
    hard_contract_violation: bool = False

    # -- Layer 4 -------------------------------------------------------------
    specialist_verifier: SpecialistVerifierEvidence = field(
        default_factory=SpecialistVerifierEvidence
    )
    structural_checks: tuple[StructuralCheckEvidence, ...] = ()
    structural_groups: tuple[StructuralGroupSupport, ...] = ()
    structural_contradicting_groups: tuple[str, ...] = ()
    layer4_i: int = 0
    layer4_x: float = 0.0
    cross_model_credit: CrossModelCredit = CrossModelCredit.SHOWN_CANDIDATE
    #: True when Module 18's candidate-free probe named a candidate Module 16
    #: never held. Unverified, uninserted, and never "accepted".
    discovered_by_structural_check: bool = False

    @property
    def structural_support_groups(self) -> tuple[str, ...]:
        return tuple(g.group_key for g in self.structural_groups if g.supports)

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "display": self.display,
            "candidate_kind": self.candidate_kind,
            "base": {
                "F": self.base_f,
                "L": self.base_l,
                "L_available": self.base_l_available,
                "X": self.base_x,
                "C": self.base_c,
                "U": self.base_u,
                "U_available": self.base_u_available,
                "I": self.base_i,
                "D": self.base_d,
                "supporting_groups": list(self.base_group_supports),
                "contradicting_groups": list(self.base_contradicting_groups),
                "hard_contract_violation": self.hard_contract_violation,
            },
            "specialist_verifier": self.specialist_verifier.to_json(),
            "structural_checks": [c.to_json() for c in self.structural_checks],
            "structural_groups": [g.to_json() for g in self.structural_groups],
            "structural_support_groups": list(self.structural_support_groups),
            "structural_contradicting_groups": list(
                self.structural_contradicting_groups
            ),
            "layer4_I": self.layer4_i,
            "layer4_X": self.layer4_x,
            "cross_model_credit": self.cross_model_credit.value,
            "discovered_by_structural_check": self.discovered_by_structural_check,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CandidateEvidenceOverlay":
        base = payload["base"]
        return cls(
            candidate_key=str(payload["candidate_key"]),
            display=str(payload["display"]),
            candidate_kind=str(payload.get("candidate_kind", "ENTITY")),
            base_f=float(base["F"]),
            base_l=float(base["L"]),
            base_l_available=bool(base["L_available"]),
            base_x=float(base["X"]),
            base_c=float(base["C"]),
            base_u=float(base["U"]),
            base_u_available=bool(base["U_available"]),
            base_i=int(base["I"]),
            base_d=float(base["D"]),
            base_group_supports=tuple(base.get("supporting_groups", ())),
            base_contradicting_groups=tuple(base.get("contradicting_groups", ())),
            hard_contract_violation=bool(base.get("hard_contract_violation", False)),
            specialist_verifier=SpecialistVerifierEvidence.from_json(
                payload["specialist_verifier"]
            ),
            structural_checks=tuple(
                StructuralCheckEvidence.from_json(c)
                for c in payload.get("structural_checks", ())
            ),
            structural_groups=tuple(
                StructuralGroupSupport.from_json(g)
                for g in payload.get("structural_groups", ())
            ),
            structural_contradicting_groups=tuple(
                payload.get("structural_contradicting_groups", ())
            ),
            layer4_i=int(payload["layer4_I"]),
            layer4_x=float(payload["layer4_X"]),
            cross_model_credit=CrossModelCredit(payload["cross_model_credit"]),
            discovered_by_structural_check=bool(
                payload.get("discovered_by_structural_check", False)
            ),
        )


@dataclass(frozen=True)
class PropositionEvidenceOverlay:
    """Module 17's reading of one Module 14 query-level proposition.

    A proposition is **not** a candidate. Verifier evidence about "no known
    qualifying locality" is evidence about that statement, and it never becomes
    an entity in the candidate set, a substantive null support, or an empty
    answer - Audit 0024's invariant and §10.3's separation both hold.
    """

    proposition: str
    specialist_verifier: SpecialistVerifierEvidence = field(
        default_factory=SpecialistVerifierEvidence
    )

    def to_json(self) -> dict[str, Any]:
        return {
            "proposition": self.proposition,
            "specialist_verifier": self.specialist_verifier.to_json(),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PropositionEvidenceOverlay":
        return cls(
            proposition=str(payload["proposition"]),
            specialist_verifier=SpecialistVerifierEvidence.from_json(
                payload["specialist_verifier"]
            ),
        )


@dataclass(frozen=True)
class NumericTargetOverlay:
    """Module 12's cluster, with whatever Layer 4 measured about it.

    The representative, the unit and the dispersion are Module 12's. Nothing
    here reclusters, reconverts, applies a tolerance, or picks a winner.
    """

    cluster_index: int
    representative: float
    canonical_unit: str
    #: Module 12's own dispersion and independent-support figures, copied so a
    #: downstream reader can judge cluster stability without reaching back past
    #: this layer. Neither is recomputed here.
    dispersion: float = 0.0
    independent_support: int = 0
    competing_clusters: int = 0
    specialist_verifier: SpecialistVerifierEvidence = field(
        default_factory=SpecialistVerifierEvidence
    )
    structural_checks: tuple[StructuralCheckEvidence, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_index": self.cluster_index,
            "representative": self.representative,
            "canonical_unit": self.canonical_unit,
            "dispersion": self.dispersion,
            "independent_support": self.independent_support,
            "competing_clusters": self.competing_clusters,
            "specialist_verifier": self.specialist_verifier.to_json(),
            "structural_checks": [c.to_json() for c in self.structural_checks],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "NumericTargetOverlay":
        return cls(
            cluster_index=int(payload["cluster_index"]),
            representative=float(payload["representative"]),
            canonical_unit=str(payload["canonical_unit"]),
            dispersion=float(payload.get("dispersion", 0.0)),
            independent_support=int(payload.get("independent_support", 0)),
            competing_clusters=int(payload.get("competing_clusters", 0)),
            specialist_verifier=SpecialistVerifierEvidence.from_json(
                payload["specialist_verifier"]
            ),
            structural_checks=tuple(
                StructuralCheckEvidence.from_json(c)
                for c in payload.get("structural_checks", ())
            ),
        )


@dataclass(frozen=True)
class PendingCheckStatus:
    """A Module 15 request, and whether a Module 18 check has run for it.

    **Execution status, never truth status.** An executed check has produced a
    reading; it has resolved nothing about the world, and an unexecuted request
    stays pending.
    """

    source_module: str
    kind: str
    reason: str
    candidate: str
    status: CheckExecutionStatus = CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED
    executed_origin_ids: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "source_module": self.source_module,
            "kind": self.kind,
            "reason": self.reason,
            "candidate": self.candidate,
            "execution_status": self.status.value,
            "executed_origin_ids": list(self.executed_origin_ids),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PendingCheckStatus":
        return cls(
            source_module=str(payload["source_module"]),
            kind=str(payload["kind"]),
            reason=str(payload["reason"]),
            candidate=str(payload["candidate"]),
            status=CheckExecutionStatus(payload["execution_status"]),
            executed_origin_ids=tuple(payload.get("executed_origin_ids", ())),
        )


@dataclass(frozen=True)
class Layer4CostLedger:
    """Physical calls this view represents, counted once each.

    Layer-4 spends nothing. What it reports is the spend of the Module 17 and
    Module 18 records it was handed - each physical call once, however many
    candidate observations or evidence events that call produced.
    """

    verifier_calls: int = 0
    structural_calls: int = 0
    unique_origin_events: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    #: Always zero. Integration performs no inference.
    integration_calls: int = 0

    @property
    def total_calls(self) -> int:
        return self.verifier_calls + self.structural_calls

    def to_json(self) -> dict[str, Any]:
        return {
            "verifier_calls": self.verifier_calls,
            "structural_calls": self.structural_calls,
            "total_physical_calls": self.total_calls,
            "unique_origin_events": self.unique_origin_events,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "integration_calls": self.integration_calls,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "Layer4CostLedger":
        return cls(
            verifier_calls=int(payload.get("verifier_calls", 0)),
            structural_calls=int(payload.get("structural_calls", 0)),
            unique_origin_events=int(payload.get("unique_origin_events", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            integration_calls=int(payload.get("integration_calls", 0)),
        )


@dataclass(frozen=True)
class Layer4EvidenceState:
    """The whole Layer-4 view of one query.

    Deliberately absent: a prediction, an accepted set, a rejected set, a final
    score, a ranking, a stopping decision, a residual estimate and a next
    action. Module 19 reads this; it does not read a verdict.
    """

    integration_version: str
    relation: str
    subject: str
    row_index: int
    #: Upstream identities, so a persisted view can never be read under the
    #: wrong architecture version.
    base_consensus_version: str = ""
    verification_version: str = ""
    check_version: str = ""

    candidates: tuple[CandidateEvidenceOverlay, ...] = ()
    propositions: tuple[PropositionEvidenceOverlay, ...] = ()
    numeric_targets: tuple[NumericTargetOverlay, ...] = ()
    #: Module 14's null state, carried through untouched.
    null_state: NullConsensusState | None = None
    pending_checks: tuple[PendingCheckStatus, ...] = ()
    cost: Layer4CostLedger = field(default_factory=Layer4CostLedger)
    errors: tuple[str, ...] = ()

    @property
    def discovered_candidates(self) -> tuple[CandidateEvidenceOverlay, ...]:
        return tuple(c for c in self.candidates if c.discovered_by_structural_check)

    @property
    def cross_model_credited(self) -> tuple[str, ...]:
        return tuple(
            c.candidate_key for c in self.candidates if c.cross_model_credit.credits
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "integration_version": self.integration_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "base_consensus_version": self.base_consensus_version,
            "verification_version": self.verification_version,
            "check_version": self.check_version,
            "candidates": [c.to_json() for c in self.candidates],
            "propositions": [p.to_json() for p in self.propositions],
            "numeric_targets": [n.to_json() for n in self.numeric_targets],
            "null_state": self.null_state.to_json() if self.null_state else None,
            "pending_checks": [p.to_json() for p in self.pending_checks],
            "discovered_candidates": [
                c.candidate_key for c in self.discovered_candidates
            ],
            "cross_model_credited": list(self.cross_model_credited),
            "cost": self.cost.to_json(),
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "Layer4EvidenceState":
        null_state = payload.get("null_state")
        return cls(
            integration_version=str(payload["integration_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            base_consensus_version=str(payload.get("base_consensus_version", "")),
            verification_version=str(payload.get("verification_version", "")),
            check_version=str(payload.get("check_version", "")),
            candidates=tuple(
                CandidateEvidenceOverlay.from_json(c) for c in payload["candidates"]
            ),
            propositions=tuple(
                PropositionEvidenceOverlay.from_json(p)
                for p in payload.get("propositions", ())
            ),
            numeric_targets=tuple(
                NumericTargetOverlay.from_json(n)
                for n in payload.get("numeric_targets", ())
            ),
            null_state=NullConsensusState.from_json(null_state) if null_state else None,
            pending_checks=tuple(
                PendingCheckStatus.from_json(p)
                for p in payload.get("pending_checks", ())
            ),
            cost=Layer4CostLedger.from_json(payload.get("cost", {})),
            errors=tuple(payload.get("errors", ())),
        )


def base_overlay(state: CandidateConsensusState) -> CandidateEvidenceOverlay:
    """Copy one Module 16 candidate state into an overlay. **No recomputation.**"""
    return CandidateEvidenceOverlay(
        candidate_key=state.candidate_key,
        display=state.display,
        candidate_kind=state.candidate_kind,
        base_f=state.f_support,
        base_l=state.l_logit,
        base_l_available=state.l_available,
        base_x=state.x_cross_model,
        base_c=state.c_contradiction,
        base_u=state.u_prompt,
        base_u_available=state.u_available,
        base_i=state.i_independent_support,
        base_d=state.d_semantic,
        base_group_supports=state.supporting_groups,
        base_contradicting_groups=state.contradicting_groups,
        hard_contract_violation=state.hard_contract_violation,
        layer4_i=state.i_independent_support,
        layer4_x=state.x_cross_model,
        cross_model_credit=CrossModelCredit.SHOWN_CANDIDATE,
    )


def numeric_overlay(cluster: NumericClusterConsensus) -> NumericTargetOverlay:
    """Copy one Module 12 cluster. Representative and unit are its own."""
    return NumericTargetOverlay(
        cluster_index=cluster.cluster_index,
        representative=cluster.representative,
        canonical_unit=cluster.canonical_unit,
        dispersion=cluster.dispersion,
        independent_support=cluster.independent_support,
        competing_clusters=cluster.competing_clusters,
    )


__all__ = [
    "INTEGRATION_VERSION",
    "CandidateEvidenceOverlay",
    "CheckExecutionStatus",
    "CrossModelCredit",
    "Layer4CostLedger",
    "Layer4EvidenceState",
    "Layer4IntegrationError",
    "Layer4ProvenanceError",
    "NumericTargetOverlay",
    "PendingCheckStatus",
    "PropositionEvidenceOverlay",
    "SpecialistVerifierEvidence",
    "StructuralCheckEvidence",
    "StructuralGroupSupport",
    "StructuralOutcome",
    "VerifierAvailability",
    "base_overlay",
    "numeric_overlay",
]
