"""Module 17 - the Specialist Verifier Suite.

Architecture position::

    M16 consensus state  (read-only)
            |
            v
    deterministic verifiable-target catalogue
            |
            v      <- the CALLER chooses which targets to request
    SpecialistVerificationRequest
            |
            v
    Module 4 kernel: score_labels -> contextual control -> read_labels
            |                        (frozen Qwen verifier role)
            v
    SpecialistVerificationResult   -> specialist_verification.jsonl

**Module 4 is called, not copied.** The softmax, the control subtraction, the
margin, the entropy and the divergence all come from
:mod:`cover_kbc.verification.blind`. This module owns the *contracts*, the
*presentation orders* and the *bookkeeping*, and nothing else.

**M17 decides nothing.** A/B/C labels are verifier evidence. There is no
accept, reject, prune, final set or score here, and ``argmax_label`` is the
model's own output rather than a system verdict.

**M17 schedules nothing.** The catalogue says which targets *can* be posed as a
blind question; whether one is *worth a call* is Module 20/21's, and the two are
separate fields so no future reader can confuse them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import LabelScoreRequest, LMRuntime
from cover_kbc.types import VerificationLabel
from cover_kbc.verification.blind import (
    LABEL_TOKENS,
    ContextualCalibrator,
    normalized_disagreement,
    read_labels,
)
from cover_kbc.verification.specialist_contracts import (
    SPECIALIST_CONTRACT_VERSION,
    SpecialistVerifierContract,
    check_specialist_registry_consistency,
    specialist_contract,
)
from cover_kbc.verification.specialist_prompts import (
    CANDIDATE_TEMPLATE_IDS,
    PROPOSITION_TEMPLATE_IDS,
    SPECIALIST_SYSTEM_PROMPT,
    SPECIALIST_TEMPLATE_IDS,
    render_specialist_prompt,
    specialist_template,
)
from cover_kbc.verification.specialist_types import (
    VERIFICATION_VERSION,
    LabelOrder,
    QueryPropositionKind,
    QuerySpecialistVerificationResult,
    SpecialistTemplateResult,
    SpecialistVerificationRequest,
    SpecialistVerificationResult,
    SpecialistVerifierError,
    TargetIneligible,
    VerificationTarget,
    VerificationTargetKind,
    VerifierBiasDiagnostics,
    argmax_label,
    mean_distribution,
    prompt_sha256,
    valid_margin,
)

#: How each query-level proposition is stated to the verifier. Plain claims
#: about the subject, carrying no hint of what the system currently believes.
PROPOSITION_TEXT: dict[QueryPropositionKind, str] = {
    QueryPropositionKind.SUBJECT_IS_LIVING: "The subject is living.",
    QueryPropositionKind.SUBJECT_IS_DECEASED: "The subject is deceased.",
    QueryPropositionKind.NO_KNOWN_QUALIFYING_LOCALITY: (
        "There is no locality of the kind this relation asks for that is known "
        "for the subject."
    ),
}


@dataclass(frozen=True)
class SpecialistVerifierConfig:
    """Module 17 configuration.

    No fitted anything: no threshold, no weight, no per-relation tuned value.
    ``shadow`` is the only supported mode - M17 spends real verifier calls and
    nothing downstream consumes its output yet.
    """

    enabled: bool = False
    mode: str = "shadow"
    verification_version: str = VERIFICATION_VERSION
    #: Phrasings used per target. Two gives §13.1 a template-disagreement
    #: reading; one would silently make that diagnostic unavailable.
    template_ids: tuple[str, ...] = CANDIDATE_TEMPLATE_IDS
    #: §13.1's label-order swaps. Presentation only; A/B/C keep their meanings.
    label_orders: tuple[LabelOrder, ...] = (LabelOrder.ABC, LabelOrder.BAC)
    use_calibration: bool = True

    SUPPORTED_MODES = frozenset({"shadow"})

    @classmethod
    def from_mapping(
        cls, config: Mapping[str, Any] | None
    ) -> "SpecialistVerifierConfig":
        payload = dict(config or {})
        known = {
            "enabled", "mode", "verification_version", "templates", "bias_controls",
            "use_calibration",
        }
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"unknown specialist_verifier key(s) {unknown}; expected "
                f"{sorted(known)}"
            )

        version = str(payload.get("verification_version", VERIFICATION_VERSION))
        if version != VERIFICATION_VERSION:
            raise ValueError(
                f"unsupported verification_version {version!r}; this build "
                f"implements {VERIFICATION_VERSION!r}"
            )
        mode = str(payload.get("mode", "shadow"))
        if mode not in cls.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported specialist_verifier mode {mode!r}; this milestone "
                f"implements {sorted(cls.SUPPORTED_MODES)} only - nothing "
                "downstream consumes Module 17 evidence yet"
            )

        templates = tuple(payload.get("templates") or CANDIDATE_TEMPLATE_IDS)
        for template_id in templates:
            if template_id not in SPECIALIST_TEMPLATE_IDS:
                raise ValueError(
                    f"unknown specialist_verifier template {template_id!r}; "
                    f"expected some of {list(SPECIALIST_TEMPLATE_IDS)}"
                )
        if not templates:
            raise ValueError(
                "specialist_verifier.templates must name at least one phrasing"
            )

        orders_block = payload.get("bias_controls") or {}
        if not isinstance(orders_block, Mapping):
            raise ValueError("specialist_verifier.bias_controls must be a mapping")
        unknown_bias = sorted(set(orders_block) - {"label_orders"})
        if unknown_bias:
            raise ValueError(
                f"unknown specialist_verifier.bias_controls key(s) {unknown_bias}; "
                "expected ['label_orders']"
            )
        raw_orders = orders_block.get("label_orders") or ["ABC", "BAC"]
        if isinstance(raw_orders, str) or not isinstance(raw_orders, (list, tuple)):
            raise ValueError(
                "specialist_verifier.bias_controls.label_orders must be a list"
            )
        orders = []
        for value in raw_orders:
            try:
                orders.append(LabelOrder(str(value)))
            except ValueError as exc:
                raise ValueError(
                    f"unknown label order {value!r}; §13.1's variants are "
                    f"{[o.value for o in LabelOrder]}"
                ) from exc
        if not orders:
            raise ValueError(
                "specialist_verifier.bias_controls.label_orders must name at "
                "least one presentation order"
            )

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=mode,
            verification_version=version,
            template_ids=templates,
            label_orders=tuple(dict.fromkeys(orders)),
            use_calibration=bool(payload.get("use_calibration", True)),
        )


# --------------------------------------------------------------------------
# The deterministic verifiable-target catalogue
# --------------------------------------------------------------------------


def verifiable_targets(consensus: Any) -> tuple[VerificationTarget, ...]:
    """Which targets of one Module 16 result *could* be verified.

    A **type** judgement, made without any neural call and without reading a
    single support count: a candidate with a hard contract violation cannot be
    rescued by a verifier, a candidate with no printable value cannot be shown
    to one, and everything else can. Which of them is worth a call is Module
    20/21's question, and this function deliberately cannot answer it.

    Module 16's state is read and never modified.
    """
    contract = specialist_contract(consensus.relation)
    query = (consensus.relation, consensus.subject, consensus.row_index)
    targets: list[VerificationTarget] = []

    if contract.supports(VerificationTargetKind.ENTITY_CANDIDATE):
        for state in consensus.candidates:
            eligible, reason = True, None
            if state.hard_contract_violation:
                eligible, reason = False, TargetIneligible.HARD_CONTRACT_VIOLATION
            elif not state.display:
                eligible, reason = False, TargetIneligible.NO_PRINTABLE_VALUE
            targets.append(VerificationTarget(
                relation=query[0], subject=query[1], row_index=query[2],
                kind=VerificationTargetKind.ENTITY_CANDIDATE,
                # Strict Module 3/16 identity. Never the alias hint.
                target_id=state.candidate_key, display=state.display,
                family=contract.family, eligible=eligible, ineligible_reason=reason,
            ))

    if contract.supports(VerificationTargetKind.NUMERIC_CLUSTER):
        for cluster in consensus.numeric_clusters:
            targets.append(VerificationTarget(
                relation=query[0], subject=query[1], row_index=query[2],
                kind=VerificationTargetKind.NUMERIC_CLUSTER,
                target_id=str(cluster.cluster_index),
                # Module 12's representative, formatted with its own unit. M17
                # neither reclusters nor rounds.
                display=f"{cluster.representative:g} {cluster.canonical_unit}".strip(),
                family=contract.family,
                numeric_cluster_index=cluster.cluster_index,
                canonical_unit=cluster.canonical_unit,
            ))

    for proposition in contract.propositions:
        targets.append(VerificationTarget(
            relation=query[0], subject=query[1], row_index=query[2],
            kind=VerificationTargetKind.QUERY_PROPOSITION,
            target_id=proposition.value, display="",
            family=contract.family, proposition=proposition,
        ))

    return tuple(targets)


# --------------------------------------------------------------------------
# The verifier
# --------------------------------------------------------------------------


class SpecialistVerifier:
    """§13's relation-specialised blind verifier. Reuses Module 4's kernel."""

    def __init__(
        self,
        config: SpecialistVerifierConfig | None = None,
        calibrator: ContextualCalibrator | None = None,
    ) -> None:
        self.config = config or SpecialistVerifierConfig(enabled=True)
        if self.config.mode not in SpecialistVerifierConfig.SUPPORTED_MODES:
            raise SpecialistVerifierError(
                f"unsupported specialist verifier mode {self.config.mode!r}"
            )
        check_specialist_registry_consistency()
        # Module 4's calibrator, shared so one control measurement is amortised
        # across candidates exactly as it is for the generic verifier.
        self.calibrator = calibrator or ContextualCalibrator()

    @property
    def verification_version(self) -> str:
        return self.config.verification_version

    # -- requests ------------------------------------------------------------

    def build_request(
        self, target: VerificationTarget
    ) -> SpecialistVerificationRequest:
        """Turn one caller-chosen target into a typed request.

        Carries identity and presentation. It carries no acquisition rationale,
        because there is no field for one.
        """
        contract = specialist_contract(target.relation)
        if target.family is not None and target.family is not contract.family:
            raise SpecialistVerifierError(
                f"target claims family {target.family.value} but {target.relation} "
                f"routes to {contract.family.value}"
            )
        if not contract.supports(target.kind):
            raise SpecialistVerifierError(
                f"the {contract.family.value} contract cannot pose a "
                f"{target.kind.value} target; it supports "
                f"{[k.value for k in contract.target_kinds]}"
            )
        proposition = target.kind is VerificationTargetKind.QUERY_PROPOSITION
        if proposition and target.proposition not in contract.propositions:
            raise SpecialistVerifierError(
                f"unsupported query proposition {target.proposition!r} for "
                f"{contract.family.value}"
            )
        template_ids = (
            PROPOSITION_TEMPLATE_IDS if proposition else self.config.template_ids
        )
        return SpecialistVerificationRequest(
            target=target,
            family=contract.family,
            contract_version=contract.contract_version,
            template_ids=tuple(template_ids),
            label_orders=self.config.label_orders,
            verification_version=self.verification_version,
        )

    # -- execution -----------------------------------------------------------

    def verify(
        self,
        request: SpecialistVerificationRequest,
        contract: RelationContract,
        runtime: LMRuntime,
    ) -> SpecialistVerificationResult:
        """Run one target's readings. Every call is explicit and attributed."""
        target = request.target
        if contract.relation != target.relation:
            raise SpecialistVerifierError(
                f"contract is for {contract.relation!r} but the target is "
                f"{target.relation!r}"
            )
        specialist = specialist_contract(target.relation)

        if not target.eligible:
            # A deterministic type/format impossibility. Module 4 could not
            # rescue it, so no call is spent - and this is a skip, not a
            # rejection: nothing here prunes the candidate.
            return SpecialistVerificationResult(
                request=request,
                verifier_model_id=getattr(getattr(runtime, "spec", None), "model_id", ""),
                verifier_model_revision=getattr(
                    getattr(runtime, "spec", None), "revision", ""
                ),
                errors=(
                    f"{target.target_id}: not eligible for specialist "
                    f"verification ({(target.ineligible_reason or '').value if target.ineligible_reason else 'unspecified'})",
                ),
            )

        target_text = self._target_text(target)
        results: list[SpecialistTemplateResult] = []
        errors: list[str] = []
        for template_id in request.template_ids:
            for order in request.label_orders:
                reading, error = self._read_one(
                    template_id, order, specialist, contract, target, target_text,
                    runtime,
                )
                results.append(reading)
                if error:
                    errors.append(error)

        usable = [r for r in results if r.usable]
        mean = mean_distribution([r.distribution for r in usable if r.distribution])
        timed = [r.latency_ms for r in results if r.latency_ms is not None]
        spec = getattr(runtime, "spec", None)
        return SpecialistVerificationResult(
            request=request,
            template_results=tuple(results),
            mean_distribution=mean,
            argmax_label=argmax_label(mean),
            bias=self._bias_diagnostics(results),
            verifier_model_id=getattr(spec, "model_id", ""),
            verifier_model_revision=getattr(spec, "revision", ""),
            calls=sum(r.calls for r in results),
            prompt_tokens=sum(r.prompt_tokens for r in results),
            generated_tokens=sum(r.generated_tokens for r in results),
            latency_ms=sum(timed) if timed else None,
            errors=tuple(errors),
            verification_version=self.verification_version,
        )

    def verify_query(
        self,
        consensus: Any,
        contract: RelationContract,
        runtime: LMRuntime,
        targets: Sequence[VerificationTarget],
    ) -> QuerySpecialistVerificationResult:
        """Verify the targets **the caller supplied**, and no others.

        The catalogue is recorded whole - including targets ruled out without a
        call - so a reader can see what was available as well as what was asked
        for. M17 never widens the request.
        """
        specialist = specialist_contract(consensus.relation)
        catalogue = verifiable_targets(consensus)
        results = [
            self.verify(self.build_request(target), contract, runtime)
            for target in targets
        ]
        return QuerySpecialistVerificationResult(
            verification_version=self.verification_version,
            relation=consensus.relation,
            subject=consensus.subject,
            row_index=consensus.row_index,
            family=specialist.family,
            contract_version=specialist.contract_version,
            results=tuple(results),
            catalogue=catalogue,
            errors=tuple(e for r in results for e in r.errors),
        )

    # -- one reading ---------------------------------------------------------

    def _read_one(
        self,
        template_id: str,
        order: LabelOrder,
        specialist: SpecialistVerifierContract,
        contract: RelationContract,
        target: VerificationTarget,
        target_text: str,
        runtime: LMRuntime,
    ) -> tuple[SpecialistTemplateResult, str | None]:
        """One (template, label order) reading, calibrated by Module 4."""
        proposition = target.kind is VerificationTargetKind.QUERY_PROPOSITION
        template = specialist_template(
            specialist, template_id, order, proposition=proposition
        )
        prompt = render_specialist_prompt(
            template, subject=target.subject, contract=contract,
            target_text=target_text,
        )
        digest = prompt_sha256(prompt, SPECIALIST_SYSTEM_PROMPT)
        spec = getattr(runtime, "spec", None)
        model_id = getattr(spec, "model_id", "unknown")
        common = dict(
            template_id=template.template_id, phrasing_id=template_id,
            label_order=order,
            prompt_sha256=digest, model_id=model_id,
            model_revision=getattr(spec, "revision", ""),
        )

        started = time.perf_counter()
        try:
            scored = runtime.score_labels(LabelScoreRequest(
                prompt=prompt,
                labels=dict(LABEL_TOKENS),
                system_prompt=SPECIALIST_SYSTEM_PROMPT,
                metadata={
                    "view_id": "specialist_verifier",
                    "subject": target.subject,
                    "relation": target.relation,
                    "module": "M17",
                    "family": specialist.family.value,
                    "target_kind": target.kind.value,
                    "target_id": target.target_id,
                    "template_id": template.template_id,
                    "label_order": order.value,
                },
            ))
        except Exception as exc:  # noqa: BLE001
            # No distribution is invented. A failed call produced no reading,
            # and reporting a uniform one would be a fabricated measurement.
            return (
                SpecialistTemplateResult(
                    **common, calls=1,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                f"{template.template_id}: {type(exc).__name__}: {exc}",
            )

        calls = 1
        control = None
        cache_hit = False
        if self.config.use_calibration:
            try:
                needed = self.calibrator.control_calls_needed(
                    runtime, contract, [template]
                )
                cache_hit = needed == 0
                control = self.calibrator.control_logits(runtime, contract, template)
                calls += 0 if cache_hit else 1
            except Exception as exc:  # noqa: BLE001
                return (
                    SpecialistTemplateResult(
                        **common, raw_logits=dict(scored.logits), calls=calls + 1,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        error=f"calibration control failed: {type(exc).__name__}: {exc}",
                    ),
                    f"{template.template_id}: control {type(exc).__name__}: {exc}",
                )

        if not self._logits_complete(scored.logits):
            return (
                SpecialistTemplateResult(
                    **common, raw_logits=dict(scored.logits), calls=calls,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=(
                        "incomplete label logits: expected "
                        f"{sorted(LABEL_TOKENS)}, got {sorted(scored.logits)}"
                    ),
                ),
                f"{template.template_id}: incomplete label logits",
            )

        # Module 4 does the arithmetic: control subtraction, softmax, entropy.
        verification = read_labels(scored, control=control, calibrator=self.calibrator)
        distribution = {
            VerificationLabel.VALID.value: verification.valid_prob,
            VerificationLabel.INVALID.value: verification.invalid_prob,
            VerificationLabel.UNKNOWN.value: verification.unknown_prob,
        }
        return (
            SpecialistTemplateResult(
                **common,
                raw_logits=dict(verification.raw_logits or {}),
                control_logits=dict(control) if control else None,
                calibrated_logits=dict(verification.calibrated_logits or {}),
                distribution={k: v for k, v in distribution.items() if v is not None},
                argmax_label=verification.label.value if verification.label else None,
                valid_margin=valid_margin(verification.calibrated_logits),
                entropy=verification.entropy,
                calibrated=verification.calibrated,
                control_cache_hit=cache_hit,
                calls=calls,
                prompt_tokens=int(getattr(scored, "prompt_tokens", 0) or 0),
                generated_tokens=int(getattr(scored, "generated_tokens", 0) or 0),
                latency_ms=(time.perf_counter() - started) * 1000.0,
            ),
            None,
        )

    @staticmethod
    def _logits_complete(logits: Mapping[str, float]) -> bool:
        return set(LABEL_TOKENS) <= set(logits)

    @staticmethod
    def _target_text(target: VerificationTarget) -> str:
        """What the verifier is shown - identity and value only."""
        if target.kind is VerificationTargetKind.QUERY_PROPOSITION:
            if target.proposition is None:
                raise SpecialistVerifierError(
                    "a query-proposition target must name its proposition"
                )
            return PROPOSITION_TEXT[target.proposition]
        if not target.display:
            raise SpecialistVerifierError(
                f"target {target.target_id!r} has no printable value to verify"
            )
        return target.display

    @staticmethod
    def _bias_diagnostics(
        results: Sequence[SpecialistTemplateResult],
    ) -> VerifierBiasDiagnostics:
        """§13.1's three readings, kept apart.

        Template disagreement holds the label order fixed and varies the
        phrasing; label-order disagreement holds the phrasing fixed and varies
        the order. Mixing them would produce one number that cannot say which
        instability it saw. Both reuse Module 4's normalised divergence.
        """
        usable = [r for r in results if r.usable and r.distribution]
        if len(usable) < 2:
            return VerifierBiasDiagnostics(
                templates_measured=len({r.phrasing_id for r in usable}),
                label_orders_measured=len({r.label_order for r in usable}),
            )

        by_order: dict[LabelOrder, list[Mapping[str, float]]] = {}
        by_phrasing: dict[str, list[Mapping[str, float]]] = {}
        for reading in usable:
            by_order.setdefault(reading.label_order, []).append(reading.distribution)
            by_phrasing.setdefault(reading.phrasing_id, []).append(reading.distribution)

        template_scores = [
            normalized_disagreement(group)
            for group in by_order.values() if len(group) > 1
        ]
        order_scores = [
            normalized_disagreement(group)
            for group in by_phrasing.values() if len(group) > 1
        ]
        valid_by_order = [
            sum(d.get(VerificationLabel.VALID.value, 0.0) for d in group) / len(group)
            for group in by_order.values()
        ]
        return VerifierBiasDiagnostics(
            template_disagreement=max(template_scores) if template_scores else None,
            label_order_disagreement=max(order_scores) if order_scores else None,
            max_valid_shift=(
                max(valid_by_order) - min(valid_by_order)
                if len(valid_by_order) > 1 else None
            ),
            templates_measured=len(by_phrasing),
            label_orders_measured=len(by_order),
        )


def build_specialist_verifier(
    config: Mapping[str, Any] | None,
    *,
    consensus_enabled: bool,
    verifier_available: bool,
    calibrator: ContextualCalibrator | None = None,
) -> "SpecialistVerifier | None":
    """Build M17 when configuration asks for it, refusing a broken wiring."""
    settings = SpecialistVerifierConfig.from_mapping(config)
    if not settings.enabled:
        return None
    if not consensus_enabled:
        raise ValueError(
            "specialist_verifier.enabled requires consensus (M16); Module 17 "
            "verifies targets Module 16 identifies and cannot enumerate them"
        )
    if not verifier_available:
        raise ValueError(
            "specialist_verifier.enabled requires the verifier model role; "
            "Module 17 scores fixed labels and cannot run without it"
        )
    return SpecialistVerifier(settings, calibrator=calibrator)


__all__ = [
    "PROPOSITION_TEXT",
    "SPECIALIST_CONTRACT_VERSION",
    "SpecialistVerifier",
    "SpecialistVerifierConfig",
    "build_specialist_verifier",
    "verifiable_targets",
]
