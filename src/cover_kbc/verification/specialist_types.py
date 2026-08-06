"""Module 17 public contract - the Specialist Verifier Suite.

Proposal §13: *"A single generic A/B/C prompt is too coarse for all relations.
M17 preserves CoVe's blind-verification invariant, but each relation gets its
own verifier contract."* Table 5 names five specialist families and the hard
negative each must reject.

Three things this module is **not**:

* **Not a decision.** A/B/C is verifier evidence. There is no ``ACCEPTED``, no
  ``REJECTED``, no final set and no pruning here - later layers decide what to
  do with the labels. ``argmax_label`` exists because it is literally the
  model's output; ``system_decision`` does not exist at all.
* **Not a second Module 4.** The softmax, the contextual-calibration
  subtraction, the label-token scoring, the entropy and the disagreement
  divergence are all Module 4's, called rather than re-derived. M17 owns the
  *contracts*; Module 4 remains the calibrated blind-scoring engine.
* **Not a scheduler.** M17 says whether a target is *eligible* for specialist
  verification. Whether it is *worth a call* is Module 20/21's question, and
  the two are separate fields for exactly that reason.

**These are calibrated verifier-label distributions, not probabilities of
factual truth.** The name of every field says so.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from cover_kbc.types import VerificationLabel

#: Bumped when the meaning of any field, contract or template changes.
VERIFICATION_VERSION = "m17-v1"


class SpecialistVerifierError(RuntimeError):
    """M17 could not run - bad inputs, bad routing or bad configuration."""


class UnsupportedSpecialistRelation(KeyError):
    """A relation with no declared specialist verification contract."""


class SpecialistVerifierFamily(str, Enum):
    """Proposal Table 5's five specialist verification contracts."""

    NUMERIC = "NUMERIC"
    AWARD_MEMBERSHIP = "AWARD_MEMBERSHIP"
    NULL_TEMPORAL = "NULL_TEMPORAL"
    STOCK = "STOCK"
    BORDER = "BORDER"


class VerificationTargetKind(str, Enum):
    """What is being verified.

    Kept apart deliberately. A numeric cluster is not an entity, and a
    query-level proposition ("no qualifying locality is known") is not a
    candidate - encoding either as a fake entity key would destroy the
    candidate/null separation Modules 14 and 16 maintain.
    """

    ENTITY_CANDIDATE = "ENTITY_CANDIDATE"
    NUMERIC_CLUSTER = "NUMERIC_CLUSTER"
    QUERY_PROPOSITION = "QUERY_PROPOSITION"


class QueryPropositionKind(str, Enum):
    """§10.3's query-level claims, verifiable independently of any candidate.

    A verifier label on one of these is **evidence about a proposition**, never
    a final empty answer: `ObjectEntities=[]` is Module 8's to write, and
    nothing here may shortcut it.
    """

    SUBJECT_IS_LIVING = "SUBJECT_IS_LIVING"
    SUBJECT_IS_DECEASED = "SUBJECT_IS_DECEASED"
    NO_KNOWN_QUALIFYING_LOCALITY = "NO_KNOWN_QUALIFYING_LOCALITY"


class LabelOrder(str, Enum):
    """§13.1's label-order swap. **Presentation order only.**

    A = VALID, B = INVALID, C = UNKNOWN in every variant. What changes is the
    order the three lines appear in the prompt, which is what makes positional
    bias measurable. Remapping the letters would change the question, not the
    presentation, and would make the readings incomparable.
    """

    ABC = "ABC"
    BAC = "BAC"
    CAB = "CAB"

    @property
    def sequence(self) -> tuple[str, ...]:
        return tuple(self.value)


class TargetIneligible(str, Enum):
    """Why a target cannot be verified, decided **without** a neural call."""

    HARD_CONTRACT_VIOLATION = "HARD_CONTRACT_VIOLATION"
    NO_PRINTABLE_VALUE = "NO_PRINTABLE_VALUE"
    UNSUPPORTED_TARGET_KIND = "UNSUPPORTED_TARGET_KIND"


@dataclass(frozen=True)
class VerificationTarget:
    """One thing that could be verified, and whether it may be.

    Produced by the deterministic catalogue over Module 16 state.
    ``eligible`` is a *type* judgement - can this be posed as a blind
    verification question at all - and never a *value* judgement about whether
    the evidence warrants spending a call.
    """

    relation: str
    subject: str
    row_index: int
    kind: VerificationTargetKind
    #: Strict Module 3/16 candidate key, cluster index, or proposition name.
    target_id: str
    #: What the verifier will be shown. Empty for a proposition target.
    display: str = ""
    family: SpecialistVerifierFamily | None = None
    proposition: QueryPropositionKind | None = None
    numeric_cluster_index: int | None = None
    canonical_unit: str = ""
    eligible: bool = True
    ineligible_reason: TargetIneligible | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "kind": self.kind.value,
            "target_id": self.target_id,
            "display": self.display,
            "family": self.family.value if self.family else None,
            "proposition": self.proposition.value if self.proposition else None,
            "numeric_cluster_index": self.numeric_cluster_index,
            "canonical_unit": self.canonical_unit,
            "eligible": self.eligible,
            "ineligible_reason": (
                self.ineligible_reason.value if self.ineligible_reason else None
            ),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "VerificationTarget":
        reason = payload.get("ineligible_reason")
        family = payload.get("family")
        proposition = payload.get("proposition")
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            kind=VerificationTargetKind(payload["kind"]),
            target_id=str(payload["target_id"]),
            display=str(payload.get("display", "")),
            family=SpecialistVerifierFamily(family) if family else None,
            proposition=QueryPropositionKind(proposition) if proposition else None,
            numeric_cluster_index=payload.get("numeric_cluster_index"),
            canonical_unit=str(payload.get("canonical_unit", "")),
            eligible=bool(payload.get("eligible", True)),
            ineligible_reason=TargetIneligible(reason) if reason else None,
        )


@dataclass(frozen=True)
class SpecialistVerificationRequest:
    """One blind verification the **caller** asked for.

    Deliberately carries no acquisition rationale: no support count, no
    independence group, no near-miss classification of this candidate, no
    consensus term. Everything here either identifies the target or selects the
    presentation, and a test asserts the field set contains nothing else.
    """

    target: VerificationTarget
    family: SpecialistVerifierFamily
    contract_version: str
    template_ids: tuple[str, ...]
    label_orders: tuple[LabelOrder, ...]
    verification_version: str = VERIFICATION_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target.to_json(),
            "family": self.family.value,
            "contract_version": self.contract_version,
            "template_ids": list(self.template_ids),
            "label_orders": [order.value for order in self.label_orders],
            "verification_version": self.verification_version,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SpecialistVerificationRequest":
        return cls(
            target=VerificationTarget.from_json(payload["target"]),
            family=SpecialistVerifierFamily(payload["family"]),
            contract_version=str(payload["contract_version"]),
            template_ids=tuple(payload["template_ids"]),
            label_orders=tuple(LabelOrder(o) for o in payload["label_orders"]),
            verification_version=str(
                payload.get("verification_version", VERIFICATION_VERSION)
            ),
        )


@dataclass(frozen=True)
class SpecialistTemplateResult:
    """One (template, label-order) reading, with everything it took to get it.

    Both the raw and the calibrated logits are kept, so a trace can show
    exactly what the contextual control changed. ``failed`` results carry the
    error and **no distribution**: after a failed call there is nothing to
    report, and reporting a uniform or zero distribution would be a fabricated
    measurement.
    """

    #: Fully-qualified: family, phrasing and label order. This is the id the
    #: control cache keys on, so a control can never cross an order boundary.
    template_id: str
    #: The bare phrasing, without the order. §13.1's two diagnostics group by
    #: different keys - across phrasings at one order, and across orders at one
    #: phrasing - so both are needed and neither can be derived from one id.
    phrasing_id: str
    label_order: LabelOrder
    prompt_sha256: str
    model_id: str
    model_revision: str = ""
    raw_logits: Mapping[str, float] | None = None
    control_logits: Mapping[str, float] | None = None
    calibrated_logits: Mapping[str, float] | None = None
    distribution: Mapping[str, float] | None = None
    argmax_label: str | None = None
    valid_margin: float | None = None
    entropy: float | None = None
    calibrated: bool = False
    #: True when the content-free control came from Module 4's cache, so this
    #: reading cost one call rather than two.
    control_cache_hit: bool = False
    calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and bool(self.distribution)

    def to_json(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "phrasing_id": self.phrasing_id,
            "label_order": self.label_order.value,
            "prompt_sha256": self.prompt_sha256,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "raw_logits": dict(self.raw_logits) if self.raw_logits else None,
            "control_logits": dict(self.control_logits) if self.control_logits else None,
            "calibrated_logits": (
                dict(self.calibrated_logits) if self.calibrated_logits else None
            ),
            "calibrated_label_distribution": (
                dict(self.distribution) if self.distribution else None
            ),
            "argmax_label": self.argmax_label,
            "valid_margin": self.valid_margin,
            "verifier_entropy": self.entropy,
            "calibrated": self.calibrated,
            "control_cache_hit": self.control_cache_hit,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SpecialistTemplateResult":
        return cls(
            template_id=str(payload["template_id"]),
            phrasing_id=str(payload["phrasing_id"]),
            label_order=LabelOrder(payload["label_order"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            model_id=str(payload["model_id"]),
            model_revision=str(payload.get("model_revision", "")),
            raw_logits=payload.get("raw_logits"),
            control_logits=payload.get("control_logits"),
            calibrated_logits=payload.get("calibrated_logits"),
            distribution=payload.get("calibrated_label_distribution"),
            argmax_label=payload.get("argmax_label"),
            valid_margin=payload.get("valid_margin"),
            entropy=payload.get("verifier_entropy"),
            calibrated=bool(payload.get("calibrated", False)),
            control_cache_hit=bool(payload.get("control_cache_hit", False)),
            calls=int(payload.get("calls", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class VerifierBiasDiagnostics:
    """§13.1's bias controls, measured rather than corrected.

    Three *different* instabilities, kept apart because they have different
    causes and different remedies:

    ``template_disagreement``
        semantically equivalent phrasings disagreeing - Module 4's own
        normalised Jensen-Shannon divergence, not a second formula.
    ``label_order_disagreement``
        the same phrasing disagreeing when A/B/C are listed in another order,
        i.e. positional bias.
    ``max_valid_shift``
        the largest swing in P(VALID) across label orders, which is the reading
        a human notices first.

    Neither is corrected here beyond contextual calibration: §13.1 asks for
    these to be *logged*, and inventing a fitted bias correction would be a
    trained component.
    """

    template_disagreement: float | None = None
    label_order_disagreement: float | None = None
    max_valid_shift: float | None = None
    templates_measured: int = 0
    label_orders_measured: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "template_disagreement": self.template_disagreement,
            "label_order_disagreement": self.label_order_disagreement,
            "max_valid_shift": self.max_valid_shift,
            "templates_measured": self.templates_measured,
            "label_orders_measured": self.label_orders_measured,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "VerifierBiasDiagnostics":
        return cls(
            template_disagreement=payload.get("template_disagreement"),
            label_order_disagreement=payload.get("label_order_disagreement"),
            max_valid_shift=payload.get("max_valid_shift"),
            templates_measured=int(payload.get("templates_measured", 0)),
            label_orders_measured=int(payload.get("label_orders_measured", 0)),
        )


@dataclass(frozen=True)
class SpecialistVerificationResult:
    """Everything M17 produced for one verification target.

    Deliberately absent: ``accepted``, ``rejected``, ``final``, ``prune``,
    ``score``, ``rank``, ``system_decision``. ``argmax_label`` is the verifier's
    own output and nothing more.
    """

    request: SpecialistVerificationRequest
    template_results: tuple[SpecialistTemplateResult, ...] = ()
    #: Deterministic mean of the usable calibrated distributions. ``None`` when
    #: none was usable - see ``available``.
    mean_distribution: Mapping[str, float] | None = None
    argmax_label: str | None = None
    bias: VerifierBiasDiagnostics = field(default_factory=VerifierBiasDiagnostics)
    verifier_model_id: str = ""
    verifier_model_revision: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float | None = None
    errors: tuple[str, ...] = ()
    verification_version: str = VERIFICATION_VERSION

    @property
    def available(self) -> bool:
        """Whether any usable calibrated reading exists.

        ``False`` is not "the verifier said UNKNOWN" - it is "the verifier was
        not successfully read", which is a different state and must stay one.
        """
        return self.mean_distribution is not None

    @property
    def usable_results(self) -> tuple[SpecialistTemplateResult, ...]:
        return tuple(r for r in self.template_results if r.usable)

    def to_json(self) -> dict[str, Any]:
        return {
            "verification_version": self.verification_version,
            "request": self.request.to_json(),
            "template_results": [r.to_json() for r in self.template_results],
            "mean_calibrated_label_distribution": (
                dict(self.mean_distribution) if self.mean_distribution else None
            ),
            "argmax_label": self.argmax_label,
            "available": self.available,
            "bias_diagnostics": self.bias.to_json(),
            "verifier_model_id": self.verifier_model_id,
            "verifier_model_revision": self.verifier_model_revision,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SpecialistVerificationResult":
        return cls(
            request=SpecialistVerificationRequest.from_json(payload["request"]),
            template_results=tuple(
                SpecialistTemplateResult.from_json(r)
                for r in payload["template_results"]
            ),
            mean_distribution=payload.get("mean_calibrated_label_distribution"),
            argmax_label=payload.get("argmax_label"),
            bias=VerifierBiasDiagnostics.from_json(payload.get("bias_diagnostics", {})),
            verifier_model_id=str(payload.get("verifier_model_id", "")),
            verifier_model_revision=str(payload.get("verifier_model_revision", "")),
            calls=int(payload.get("calls", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            errors=tuple(payload.get("errors", ())),
            verification_version=str(
                payload.get("verification_version", VERIFICATION_VERSION)
            ),
        )


@dataclass(frozen=True)
class QuerySpecialistVerificationResult:
    """Every verification M17 ran for one query, plus what it declined to run."""

    verification_version: str
    relation: str
    subject: str
    row_index: int
    family: SpecialistVerifierFamily
    contract_version: str
    results: tuple[SpecialistVerificationResult, ...] = ()
    #: The deterministic catalogue, including targets ruled out without a call.
    catalogue: tuple[VerificationTarget, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.results)

    @property
    def skipped_targets(self) -> tuple[VerificationTarget, ...]:
        return tuple(t for t in self.catalogue if not t.eligible)

    def to_json(self) -> dict[str, Any]:
        return {
            "verification_version": self.verification_version,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "row_index": self.row_index,
            "family": self.family.value,
            "contract_version": self.contract_version,
            "results": [r.to_json() for r in self.results],
            "catalogue": [t.to_json() for t in self.catalogue],
            "calls": self.calls,
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(
        cls, payload: Mapping[str, Any]
    ) -> "QuerySpecialistVerificationResult":
        return cls(
            verification_version=str(payload["verification_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            family=SpecialistVerifierFamily(payload["family"]),
            contract_version=str(payload["contract_version"]),
            results=tuple(
                SpecialistVerificationResult.from_json(r) for r in payload["results"]
            ),
            catalogue=tuple(
                VerificationTarget.from_json(t) for t in payload.get("catalogue", ())
            ),
            errors=tuple(payload.get("errors", ())),
        )


def prompt_sha256(prompt: str, system_prompt: str) -> str:
    """Identity of one rendered verification prompt."""
    return hashlib.sha256(
        f"{system_prompt}\x00{prompt}".encode("utf-8")
    ).hexdigest()


def mean_distribution(
    distributions: Sequence[Mapping[str, float]]
) -> dict[str, float] | None:
    """Deterministic mean over usable calibrated distributions.

    A plain mean, which is what Module 4's own aggregation does. Deliberately
    **not** a majority vote over argmax labels and not a fitted mixture: both
    would discard the calibrated distribution the proposal asks M17 to produce.
    Returns ``None`` when nothing was usable, rather than a uniform stand-in.
    """
    usable = [d for d in distributions if d]
    if not usable:
        return None
    keys = {k for d in usable for k in d}
    return {k: sum(d.get(k, 0.0) for d in usable) / len(usable) for k in sorted(keys)}


def argmax_label(distribution: Mapping[str, float] | None) -> str | None:
    """The verifier's own most-likely label. Not a system decision."""
    if not distribution:
        return None
    return min(distribution, key=lambda k: (-distribution[k], k))


def valid_margin(logits: Mapping[str, float] | None) -> float | None:
    """``z~(VALID) - max z~(other)``, Module 4's own margin definition."""
    if not logits or VerificationLabel.VALID.value not in logits:
        return None
    others = [v for k, v in logits.items() if k != VerificationLabel.VALID.value]
    if not others:
        return None
    return logits[VerificationLabel.VALID.value] - max(others)


__all__ = [
    "LabelOrder",
    "QueryPropositionKind",
    "QuerySpecialistVerificationResult",
    "SpecialistTemplateResult",
    "SpecialistVerificationRequest",
    "SpecialistVerificationResult",
    "SpecialistVerifierError",
    "SpecialistVerifierFamily",
    "TargetIneligible",
    "UnsupportedSpecialistRelation",
    "VERIFICATION_VERSION",
    "VerificationTarget",
    "VerificationTargetKind",
    "VerifierBiasDiagnostics",
    "argmax_label",
    "mean_distribution",
    "prompt_sha256",
    "valid_margin",
]
