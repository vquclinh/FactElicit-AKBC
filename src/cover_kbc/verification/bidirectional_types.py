"""Module 18 public contract - Bidirectional and Counterfactual Verification.

Proposal §14: *"M18 creates genuinely new evidence instead of issuing a generic
'think again' instruction."* Four mechanisms, and exactly four:

* **Reverse check** - "candidate o → subject s when the relation supports a
  meaningful reverse question";
* **Key-condition reconstruction** - "mask the subject/candidate condition and
  ask the model to recover it; use the resulting consistency signal";
* **Counterfactual pair** - "compare a true-looking candidate against a
  near-miss class **generated from the contract, not from external facts**";
* **Candidate-free recall** - "do not show the candidate; if it appears
  naturally in an independent probe, increase X".

Three things this module is **not**:

* **Not a verdict.** Every outcome here is an evidence event. There is no
  ``ACCEPTED``, ``REJECTED``, ``final_score``, ``final_set``, ``should_stop`` or
  ``prune``, and a mismatch is evidence rather than a rejection.
* **Not more Module 17.** M17 asks one calibrated A/B/C question about a
  candidate. M18 asks structurally different questions and keeps its own
  result contract, so calibrated verifier evidence and adversarial evidence can
  never be confused.
* **Not a scheduler.** M18 says which checks are *eligible*; the caller says
  which to execute. `M17` established that separation and M18 keeps it.

**On X.** §14's candidate-free bullet says "increase X". Audit 0008 froze
``X`` as *genuinely independent cross-model recall*. M18 therefore records the
provenance that decides the question - ``candidate_shown``, model family,
``cross_model_eligible`` - and **changes no** ``X`` itself. See Audit 0026 §24.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

#: Bumped when the meaning of any field, frame or parse state changes.
CHECK_VERSION = "m18-v1"


class BidirectionalCheckError(RuntimeError):
    """M18 could not run - bad inputs, bad routing or bad configuration."""


class UnsupportedCheckRelation(KeyError):
    """A relation with no declared Module 18 check profile."""


class BidirectionalCheckKind(str, Enum):
    """§14's four mechanisms. Exactly four, and no generic re-ask."""

    REVERSE = "REVERSE"
    KEY_CONDITION = "KEY_CONDITION"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    CANDIDATE_FREE_RECALL = "CANDIDATE_FREE_RECALL"

    @property
    def shows_candidate(self) -> bool:
        """Whether the mechanism puts the candidate in front of the model.

        Only the candidate-free probe hides it, which is what makes that one -
        and only that one - a possible route to cross-model credit.
        """
        return self is not BidirectionalCheckKind.CANDIDATE_FREE_RECALL

    @property
    def independence_group(self) -> str:
        """One stable structural group per mechanism.

        Repeats of one mechanism share it: a second sample, a second template
        or a second near-miss class is provenance, never a second independent
        source.
        """
        return f"M18_{self.value}"


class CheckTargetKind(str, Enum):
    """What a check is about. Mirrors Module 17's split deliberately."""

    ENTITY_CANDIDATE = "ENTITY_CANDIDATE"
    NUMERIC_CLUSTER = "NUMERIC_CLUSTER"
    #: A candidate-free probe is about the *query*, not about any candidate.
    QUERY = "QUERY"


class CheckIneligible(str, Enum):
    """Why a check cannot be posed. Decided **without** a neural call."""

    RELATION_HAS_NO_REVERSE = "RELATION_HAS_NO_REVERSE"
    CHECK_NOT_DECLARED_FOR_RELATION = "CHECK_NOT_DECLARED_FOR_RELATION"
    UNSUPPORTED_TARGET_KIND = "UNSUPPORTED_TARGET_KIND"
    NO_PRINTABLE_VALUE = "NO_PRINTABLE_VALUE"
    HARD_CONTRACT_VIOLATION = "HARD_CONTRACT_VIOLATION"
    UNKNOWN_COUNTERFACTUAL_CLASS = "UNKNOWN_COUNTERFACTUAL_CLASS"


class CheckParseStatus(str, Enum):
    """How one bounded response resolved. Every failure stays explicit."""

    OK = "OK"
    ABSTAINED = "ABSTAINED"
    EMPTY = "EMPTY"
    MALFORMED = "MALFORMED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    NUMERIC_PARSE_FAILED = "NUMERIC_PARSE_FAILED"


class ReverseOutcome(str, Enum):
    """The reverse framing's bounded answer.

    ``UNRESOLVED`` is the model declining, not a soft no - and an absence is
    never turned into a contradiction.
    """

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"


class ReconstructionOutcome(str, Enum):
    """Whether the masked condition came back as the target.

    A ``DIFFERENT_VALUE_RECOVERED`` is **evidence**, not a rejection: the model
    reconstructed something else, which is exactly the consistency signal §14
    asks for.
    """

    TARGET_RECOVERED = "TARGET_RECOVERED"
    DIFFERENT_VALUE_RECOVERED = "DIFFERENT_VALUE_RECOVERED"
    UNRESOLVED = "UNRESOLVED"


class CounterfactualOutcome(str, Enum):
    """Which side of the contract's own distinction the model chose.

    Deliberately **not** A/B/C: Module 17's labels carry calibrated verifier
    semantics, and reusing them here would let adversarial evidence be read as
    a calibrated reading.
    """

    TARGET_RELATION = "TARGET_RELATION"
    NEAR_MISS_RELATION = "NEAR_MISS_RELATION"
    NEITHER = "NEITHER"
    UNRESOLVED = "UNRESOLVED"


class RecallOutcome(str, Enum):
    """Did a known candidate appear on its own in a probe that never named it?

    ``TARGET_RECALLED`` means at least one candidate the system already held
    was produced by a probe that had never seen it - §14's "appears naturally
    in an independent probe". ``TARGET_ABSENT`` means the probe answered but
    named none of them, which is an absence and **not** a contradiction.
    """

    TARGET_RECALLED = "TARGET_RECALLED"
    TARGET_ABSENT = "TARGET_ABSENT"
    NOTHING_RECALLED = "NOTHING_RECALLED"
    UNRESOLVED = "UNRESOLVED"


def derive_check_origin_id(
    *, model_id: str, operation_id: str, prompt_sha256: str, sample_index: int
) -> str:
    """Canonical identity of one physical Module 18 output.

    Deterministic and module-agnostic, matching Module 16's formula so a future
    Layer-4 integration can charge one physical call once. A new M18 call is a
    **new** origin: it never reuses an M16 or M17 one, because it is a
    different physical generation.
    """
    raw = "|".join(
        ("origin", "v1", model_id, operation_id, prompt_sha256, str(sample_index))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def prompt_sha256(prompt: str, system_prompt: str) -> str:
    return hashlib.sha256(f"{system_prompt}\x00{prompt}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CheckTarget:
    """What a check is about, and whether it can be posed at all."""

    relation: str
    subject: str
    row_index: int
    kind: CheckTargetKind
    #: Strict Module 3/16 candidate key, cluster index, or "" for a query probe.
    target_id: str = ""
    display: str = ""
    numeric_cluster_index: int | None = None
    canonical_unit: str = ""
    #: Carried from Module 16's own state so a check is never posed for a
    #: candidate the contract already rules impossible.
    hard_contract_violation: bool = False
    #: The keys a candidate-free probe compares against **after** inference.
    #: They exist so "did it appear naturally?" is answerable at all; the
    #: candidate-free renderer takes only a subject, so none of them can reach
    #: a prompt even by accident.
    known_candidate_keys: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "kind": self.kind.value,
            "target_id": self.target_id,
            "display": self.display,
            "numeric_cluster_index": self.numeric_cluster_index,
            "canonical_unit": self.canonical_unit,
            "hard_contract_violation": self.hard_contract_violation,
            "known_candidate_keys": list(self.known_candidate_keys),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CheckTarget":
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            kind=CheckTargetKind(payload["kind"]),
            target_id=str(payload.get("target_id", "")),
            display=str(payload.get("display", "")),
            numeric_cluster_index=payload.get("numeric_cluster_index"),
            canonical_unit=str(payload.get("canonical_unit", "")),
            hard_contract_violation=bool(payload.get("hard_contract_violation", False)),
            known_candidate_keys=tuple(payload.get("known_candidate_keys", ())),
        )


@dataclass(frozen=True)
class PendingCheckOrigin:
    """Where an eligible check came from, when a specialist asked for it.

    Kept **outside** the model-visible prompt. Module 15 knows it suspects a
    parent/subsidiary confusion; telling the verifier so would anchor it, and
    the prompt therefore carries only the contract's generic class.
    """

    source_module: str
    kind: str
    reason: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source_module": self.source_module,
            "kind": self.kind,
            "reason": self.reason,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PendingCheckOrigin":
        return cls(
            source_module=str(payload["source_module"]),
            kind=str(payload["kind"]),
            reason=str(payload["reason"]),
            detail=str(payload.get("detail", "")),
        )


@dataclass(frozen=True)
class EligibleCheck:
    """One check that *could* be posed, and what it would ask.

    Eligibility is a **type** judgement - does this relation support this
    framing, is there a printable value, is the class declared by the contract.
    It is never a judgement that the check is worth a call: that is Module
    20/21's, and no field here reads an support count, a label or a risk flag.
    """

    check_kind: BidirectionalCheckKind
    target: CheckTarget
    eligible: bool = True
    ineligible_reason: CheckIneligible | None = None
    #: Contract-declared near-miss class, for a counterfactual only.
    counterfactual_class: str = ""
    #: Set when a specialist requested this check (Module 15 pending checks).
    requested_by: PendingCheckOrigin | None = None

    @property
    def check_id(self) -> str:
        """**Module 18's canonical logical check identity.**

        The mechanism, the target it is posed about, and - for a counterfactual
        - the contract-declared near-miss class. Those three are what make two
        checks different questions: §14's counterfactual against ``hn0`` and
        against ``hn1`` render different prompts and produce different
        evidence, so they are two checks, not one asked twice.

        This is the *single* identity Module 18 publishes. Everything that has
        to name a check downstream derives from it rather than inventing its
        own scheme: Layer 6's ``action_id`` is ``M18:<check_id>``, the
        request's ``operation_id`` embeds it, and the execution seam attributes
        a reading back to the action through it. Audit 0043 C-01 found the
        request identity omitting target and class, so a second same-mechanism
        check in one query silently inherited the first one's outcome.
        """
        return ":".join(
            part for part in (
                self.check_kind.value, self.target.target_id,
                self.counterfactual_class,
            ) if part
        )

    @property
    def candidate_shown(self) -> bool:
        return self.check_kind.shows_candidate

    def to_json(self) -> dict[str, Any]:
        return {
            "check_kind": self.check_kind.value,
            "target": self.target.to_json(),
            "eligible": self.eligible,
            "ineligible_reason": (
                self.ineligible_reason.value if self.ineligible_reason else None
            ),
            "counterfactual_class": self.counterfactual_class,
            "candidate_shown": self.candidate_shown,
            "independence_group": self.check_kind.independence_group,
            "requested_by": (
                self.requested_by.to_json() if self.requested_by else None
            ),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "EligibleCheck":
        reason = payload.get("ineligible_reason")
        requested = payload.get("requested_by")
        return cls(
            check_kind=BidirectionalCheckKind(payload["check_kind"]),
            target=CheckTarget.from_json(payload["target"]),
            eligible=bool(payload.get("eligible", True)),
            ineligible_reason=CheckIneligible(reason) if reason else None,
            counterfactual_class=str(payload.get("counterfactual_class", "")),
            requested_by=(
                PendingCheckOrigin.from_json(requested) if requested else None
            ),
        )


@dataclass(frozen=True)
class BidirectionalCheckRequest:
    """One check the **caller** asked for."""

    check: EligibleCheck
    template_id: str
    #: Which runtime role must serve it. A candidate-free probe may be pointed
    #: at the second family; nothing else needs to be.
    model_role: str = "enumerator"
    sample_index: int = 0
    decode_identity: str = "default"
    check_version: str = CHECK_VERSION

    @property
    def check_kind(self) -> BidirectionalCheckKind:
        return self.check.check_kind

    @property
    def target(self) -> CheckTarget:
        return self.check.target

    @property
    def check_id(self) -> str:
        """The logical check this request poses, in its owner's vocabulary."""
        return self.check.check_id

    @property
    def operation_id(self) -> str:
        """This request's identity: the logical check, plus how it was asked.

        Built **on** :attr:`EligibleCheck.check_id` rather than beside it, so
        one identity runs from the catalogue through the request, the origin
        event id, the prompt's ``view_id`` and the telemetry attribution.

        The template and sample index stay in it because two renderings of one
        check are two operations with two prompts and two costs. What changed
        (Audit 0043 C-01) is that the *check* is now named: the previous form
        was ``m18_<kind>:<template>#<sample>``, which was identical for every
        counterfactual in a query however many targets and near-miss classes
        were in play, and the seam then attributed the first record's reading
        to all of them.
        """
        return (
            f"m18:{self.check_id}"
            f":{self.template_id}#{self.sample_index}"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check.to_json(),
            "template_id": self.template_id,
            "operation_id": self.operation_id,
            "model_role": self.model_role,
            "sample_index": self.sample_index,
            "decode_identity": self.decode_identity,
            "check_version": self.check_version,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "BidirectionalCheckRequest":
        return cls(
            check=EligibleCheck.from_json(payload["check"]),
            template_id=str(payload["template_id"]),
            model_role=str(payload.get("model_role", "enumerator")),
            sample_index=int(payload.get("sample_index", 0)),
            decode_identity=str(payload.get("decode_identity", "default")),
            check_version=str(payload.get("check_version", CHECK_VERSION)),
        )


@dataclass(frozen=True)
class RecalledCandidate:
    """One atomic candidate a candidate-free probe produced.

    **Unverified and un-inserted.** It is not added to Module 3, not added to
    Module 16 and not accepted anywhere: a future Layer-4 integration decides
    what to do with it. Identity is Module 3's strict key; there is no alias
    folding and no fuzzy matching.
    """

    surface: str
    candidate_key: str
    #: Whether this is the target the caller was checking. Computed **after**
    #: inference - the target never entered the prompt.
    is_target: bool = False
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise BidirectionalCheckError(
                "Module 18 never verifies; a recalled candidate is an "
                "acquisition observation, not an established fact"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "candidate_key": self.candidate_key,
            "is_target": self.is_target,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RecalledCandidate":
        return cls(
            surface=str(payload["surface"]),
            candidate_key=str(payload["candidate_key"]),
            is_target=bool(payload.get("is_target", False)),
        )


@dataclass(frozen=True)
class BidirectionalCheckRecord:
    """One executed check: what was asked, what came back, what it cost.

    One physical output is **one** origin however many candidates it named -
    ``recalled_candidates`` are observations of that single origin, and cost is
    charged to the origin once.
    """

    request: BidirectionalCheckRequest
    origin_event_id: str
    prompt_sha256: str
    model_id: str
    model_revision: str = ""
    model_family: str = ""
    independence_group: str = ""
    raw_output: str = ""
    parse_status: CheckParseStatus = CheckParseStatus.OK

    reverse_outcome: ReverseOutcome | None = None
    reconstruction_outcome: ReconstructionOutcome | None = None
    recovered_value: str = ""
    counterfactual_outcome: CounterfactualOutcome | None = None
    recall_outcome: RecallOutcome | None = None
    recalled_candidates: tuple[RecalledCandidate, ...] = ()

    #: Provenance that decides cross-model eligibility. Recorded, never acted on
    #: here - see Audit 0026 §24.
    candidate_shown: bool = True
    independent_recall: bool = False
    cross_model_eligible: bool = False

    calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float | None = None
    error: str | None = None
    #: Fixed. Module 18 acquires and contrasts; Module 17 is the verifier, and
    #: neither establishes truth.
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.verified:
            raise BidirectionalCheckError(
                "Module 18 never verifies; its output is independent evidence, "
                "not an established fact"
            )
        if self.cross_model_eligible and self.candidate_shown:
            raise BidirectionalCheckError(
                "a check that showed the candidate cannot be cross-model "
                "eligible; anchored agreement is not independent recall"
            )

    @property
    def usable(self) -> bool:
        return self.error is None and self.parse_status is CheckParseStatus.OK

    @property
    def new_candidates(self) -> tuple[RecalledCandidate, ...]:
        return tuple(c for c in self.recalled_candidates if not c.is_target)

    def to_json(self) -> dict[str, Any]:
        return {
            "request": self.request.to_json(),
            "origin_event_id": self.origin_event_id,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_family": self.model_family,
            "independence_group": self.independence_group,
            "raw_output": self.raw_output,
            "parse_status": self.parse_status.value,
            "reverse_outcome": (
                self.reverse_outcome.value if self.reverse_outcome else None
            ),
            "reconstruction_outcome": (
                self.reconstruction_outcome.value
                if self.reconstruction_outcome else None
            ),
            "recovered_value": self.recovered_value,
            "counterfactual_outcome": (
                self.counterfactual_outcome.value
                if self.counterfactual_outcome else None
            ),
            "recall_outcome": (
                self.recall_outcome.value if self.recall_outcome else None
            ),
            "recalled_candidates": [c.to_json() for c in self.recalled_candidates],
            "candidate_shown": self.candidate_shown,
            "independent_recall": self.independent_recall,
            "cross_model_eligible": self.cross_model_eligible,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "BidirectionalCheckRecord":
        def _enum(name: str, enum):
            value = payload.get(name)
            return enum(value) if value else None

        return cls(
            request=BidirectionalCheckRequest.from_json(payload["request"]),
            origin_event_id=str(payload["origin_event_id"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            model_revision=str(payload.get("model_revision", "")),
            model_family=str(payload.get("model_family", "")),
            independence_group=str(payload.get("independence_group", "")),
            raw_output=str(payload.get("raw_output", "")),
            parse_status=CheckParseStatus(payload["parse_status"]),
            reverse_outcome=_enum("reverse_outcome", ReverseOutcome),
            reconstruction_outcome=_enum(
                "reconstruction_outcome", ReconstructionOutcome
            ),
            recovered_value=str(payload.get("recovered_value", "")),
            counterfactual_outcome=_enum(
                "counterfactual_outcome", CounterfactualOutcome
            ),
            recall_outcome=_enum("recall_outcome", RecallOutcome),
            recalled_candidates=tuple(
                RecalledCandidate.from_json(c)
                for c in payload.get("recalled_candidates", ())
            ),
            candidate_shown=bool(payload.get("candidate_shown", True)),
            independent_recall=bool(payload.get("independent_recall", False)),
            cross_model_eligible=bool(payload.get("cross_model_eligible", False)),
            calls=int(payload.get("calls", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class QueryBidirectionalResult:
    """Everything M18 produced for one query.

    Deliberately absent: acceptance, rejection, ranking, pruning, a final set, a
    stopping decision, a budget decision and a residual estimate.
    """

    check_version: str
    relation: str
    subject: str
    row_index: int
    catalogue: tuple[EligibleCheck, ...] = ()
    records: tuple[BidirectionalCheckRecord, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.records)

    @property
    def origin_event_ids(self) -> tuple[str, ...]:
        return tuple(sorted({r.origin_event_id for r in self.records}))

    @property
    def newly_recalled_candidates(self) -> tuple[RecalledCandidate, ...]:
        return tuple(c for r in self.records for c in r.new_candidates)

    @property
    def ineligible_checks(self) -> tuple[EligibleCheck, ...]:
        return tuple(c for c in self.catalogue if not c.eligible)

    def to_json(self) -> dict[str, Any]:
        return {
            "check_version": self.check_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "catalogue": [c.to_json() for c in self.catalogue],
            "records": [r.to_json() for r in self.records],
            "calls": self.calls,
            "origin_event_ids": list(self.origin_event_ids),
            "newly_recalled_candidates": [
                c.to_json() for c in self.newly_recalled_candidates
            ],
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "QueryBidirectionalResult":
        return cls(
            check_version=str(payload["check_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            catalogue=tuple(
                EligibleCheck.from_json(c) for c in payload.get("catalogue", ())
            ),
            records=tuple(
                BidirectionalCheckRecord.from_json(r) for r in payload.get("records", ())
            ),
            errors=tuple(payload.get("errors", ())),
        )


def sort_checks(checks: Sequence[EligibleCheck]) -> tuple[EligibleCheck, ...]:
    """Deterministic catalogue order, independent of upstream iteration order."""
    return tuple(sorted(
        checks,
        key=lambda c: (
            c.check_kind.value, c.target.kind.value, c.target.target_id,
            c.counterfactual_class,
        ),
    ))


__all__ = [
    "CHECK_VERSION",
    "BidirectionalCheckError",
    "BidirectionalCheckKind",
    "BidirectionalCheckRecord",
    "BidirectionalCheckRequest",
    "CheckIneligible",
    "CheckParseStatus",
    "CheckTarget",
    "CheckTargetKind",
    "CounterfactualOutcome",
    "EligibleCheck",
    "PendingCheckOrigin",
    "QueryBidirectionalResult",
    "RecallOutcome",
    "RecalledCandidate",
    "ReconstructionOutcome",
    "ReverseOutcome",
    "UnsupportedCheckRelation",
    "derive_check_origin_id",
    "prompt_sha256",
    "sort_checks",
]
