"""Layer-4 verification-evidence integration.

The seam between Module 16's pre-verification consensus and Module 19's
coverage estimator. It reads three already-audited states - M16's consensus,
M17's calibrated verifications, M18's structural checks - and produces one
deterministic evidence view.

**Zero neural calls.** There is no runtime parameter on any entry point, no
model import, and a test asserts every runtime counter is unmoved across an
integration. Missing evidence stays missing: nothing here executes a
verification M17 did not run or a check M18 did not run.

**Nothing upstream is mutated.** M16's result, M17's results, M18's records,
Module 3's graph and Module 5's state all come out byte-identical.
``atomic_consensus.jsonl`` in particular stays exactly as Module 16 wrote it,
which is what keeps the M16-only / M16+M17 / M16+M17+M18 ablations honest.

Three accounting rules do the real work, and each exists because a naive
projection would break an audited invariant:

1. **One Module 17 request is one mechanism.** Two phrasings times two label
   orders is four *readings* and four physical calls, but one specialist
   verifier evidence family. §13.1's variations are diagnostics of prompt and
   position sensitivity, not four independent witnesses.
2. **Content-free controls are never factual evidence.** They measure
   prompt-label bias. They cost real calls, they are counted in the ledger, and
   they support and contradict nothing.
3. **Only a hidden-candidate probe answered by a new family can credit X.**
   §14 says a natural recall increases ``X``; Audit 0008 defines ``X`` as
   cross-model independent recall. Both conditions must hold, and when the
   candidate's prior families are unknowable the credit is withheld.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from cover_kbc.evidence.layer4_types import (
    INTEGRATION_VERSION,
    CandidateEvidenceOverlay,
    CheckExecutionStatus,
    CrossModelCredit,
    Layer4CostLedger,
    Layer4EvidenceState,
    Layer4IntegrationError,
    Layer4ProvenanceError,
    PendingCheckStatus,
    PropositionEvidenceOverlay,
    SpecialistVerifierEvidence,
    StructuralCheckEvidence,
    StructuralGroupSupport,
    StructuralOutcome,
    VerifierAvailability,
    base_overlay,
    numeric_overlay,
)

#: Module 17's whole measurement of one target is one structural mechanism.
SPECIALIST_VERIFIER_GROUP = "m17:SPECIALIST_VERIFIER"

#: Immutable per-origin metadata. Two records may read one output differently;
#: they may not disagree about what produced it.
_IMMUTABLE_ORIGIN_FIELDS = ("model_id", "prompt_sha256")


@dataclass(frozen=True)
class Layer4IntegrationConfig:
    """Layer-4 configuration.

    Deliberately almost empty: the projection is deterministic, so there is
    nothing to tune. No threshold, no weight, no cutoff, no stop value.
    """

    enabled: bool = False
    mode: str = "shadow"
    integration_version: str = INTEGRATION_VERSION

    SUPPORTED_MODES = frozenset({"shadow"})

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "Layer4IntegrationConfig":
        payload = dict(config or {})
        known = {"enabled", "mode", "integration_version"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"unknown layer4_integration key(s) {unknown}; expected {sorted(known)}"
            )
        version = str(payload.get("integration_version", INTEGRATION_VERSION))
        if version != INTEGRATION_VERSION:
            raise ValueError(
                f"unsupported integration_version {version!r}; this build "
                f"implements {INTEGRATION_VERSION!r}"
            )
        mode = str(payload.get("mode", "shadow"))
        if mode not in cls.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported layer4_integration mode {mode!r}; this milestone "
                f"implements {sorted(cls.SUPPORTED_MODES)} only"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=mode,
            integration_version=version,
        )


# --------------------------------------------------------------------------
# Module 17 adapter
# --------------------------------------------------------------------------


def specialist_evidence(result: Any) -> SpecialistVerifierEvidence:
    """Project one Module 17 result. Everything measured is kept.

    ``readings`` counts factual target readings and ``control_calls`` counts
    the content-free controls actually paid for - a cached control performs no
    inference and is charged nothing. The two are reported separately because
    only the first is evidence about the world.
    """
    readings = len(result.template_results)
    control_calls = sum(
        0 if reading.control_cache_hit else max(0, reading.calls - 1)
        for reading in result.template_results
    )
    available = (
        VerifierAvailability.AVAILABLE if result.available
        else VerifierAvailability.UNAVAILABLE
    )
    return SpecialistVerifierEvidence(
        availability=available,
        distribution=dict(result.mean_distribution) if result.mean_distribution else None,
        argmax_label=result.argmax_label,
        valid_margin=_mean_or_none([r.valid_margin for r in result.usable_results]),
        verifier_entropy=_mean_or_none([r.entropy for r in result.usable_results]),
        template_disagreement=result.bias.template_disagreement,
        label_order_disagreement=result.bias.label_order_disagreement,
        max_valid_shift=result.bias.max_valid_shift,
        readings=readings,
        control_calls=control_calls,
        physical_calls=result.calls,
        # One request, one mechanism - however many phrasings and orders
        # measured it.
        independence_group=SPECIALIST_VERIFIER_GROUP,
        contradicts=result.argmax_label == "INVALID" and result.available,
        contract_version=result.request.contract_version,
        verification_version=result.verification_version,
    )


def _mean_or_none(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


# --------------------------------------------------------------------------
# Module 18 adapter
# --------------------------------------------------------------------------

#: §14's outcomes, mapped to Module 16's signed vocabulary. Conservative by
#: construction: an unresolved answer, an absence, a malformed answer and a
#: failed call are **never** contradictions.
_REVERSE_MAP = {
    "SUPPORTED": StructuralOutcome.SUPPORT,
    "CONTRADICTED": StructuralOutcome.CONTRADICT,
    "UNRESOLVED": StructuralOutcome.UNRESOLVED,
}
#: Key-condition reconstruction is the one mapping that **depends on the
#: relation's cardinality**, and getting it wrong would manufacture evidence.
#: Masking the target and receiving a different object means two different
#: things:
#:
#: * where the contract admits **at most one** object, the two compete, and a
#:   different reconstruction is genuine evidence against this target;
#: * where the contract admits **many**, both may hold at once - naming another
#:   recipient of an award says nothing whatever about this recipient.
#:
#: The exclusivity test is Module 0's own ``selection.max_objects``, not a
#: relation-name switch. See Audit 0027 §20A.
_RECONSTRUCTION_EXCLUSIVE_MAP = {
    "TARGET_RECOVERED": StructuralOutcome.SUPPORT,
    "DIFFERENT_VALUE_RECOVERED": StructuralOutcome.CONTRADICT,
    "UNRESOLVED": StructuralOutcome.UNRESOLVED,
}
_RECONSTRUCTION_SET_VALUED_MAP = {
    "TARGET_RECOVERED": StructuralOutcome.SUPPORT,
    "DIFFERENT_VALUE_RECOVERED": StructuralOutcome.ALTERNATE_RECOVERED,
    "UNRESOLVED": StructuralOutcome.UNRESOLVED,
}
_COUNTERFACTUAL_MAP = {
    "TARGET_RELATION": StructuralOutcome.SUPPORT,
    "NEAR_MISS_RELATION": StructuralOutcome.CONTRADICT,
    "NEITHER": StructuralOutcome.UNRESOLVED,
    "UNRESOLVED": StructuralOutcome.UNRESOLVED,
}
_RECALL_MAP = {
    "TARGET_RECALLED": StructuralOutcome.SUPPORT,
    # The probe answered and named other things. An absence is not a denial.
    "TARGET_ABSENT": StructuralOutcome.UNRESOLVED,
    "NOTHING_RECALLED": StructuralOutcome.UNRESOLVED,
    "UNRESOLVED": StructuralOutcome.UNRESOLVED,
}

_OUTCOME_MAPS = {
    "REVERSE": ("reverse_outcome", _REVERSE_MAP),
    "KEY_CONDITION": ("reconstruction_outcome", None),      # chosen per relation
    "COUNTERFACTUAL": ("counterfactual_outcome", _COUNTERFACTUAL_MAP),
    "CANDIDATE_FREE_RECALL": ("recall_outcome", _RECALL_MAP),
}


def admits_one_object(relation: str) -> bool:
    """Whether Module 0's contract admits **at most one** object.

    The authoritative exclusivity signal, read from the contract rather than
    inferred from a relation name: ``selection.max_objects == 1`` is true for
    the null-single and numeric relations and false for the set-valued ones.
    """
    from cover_kbc.contracts.registry import CONTRACTS

    contract = CONTRACTS.get(relation)
    if contract is None:
        raise Layer4IntegrationError(
            f"no relation contract for {relation!r}; cardinality cannot be read"
        )
    return contract.selection.max_objects == 1


def structural_evidence(
    record: Any,
    *,
    target_key: str,
    prior_families: Mapping[str, Sequence[str]] | None,
    previously_known: bool = True,
) -> StructuralCheckEvidence:
    """Project one executed Module 18 record into signed structural evidence.

    ``target_key`` is the candidate this projection is *about*; for a
    candidate-free record it is the key that was found to appear, if any.
    """
    kind = record.request.check_kind.value
    attribute, mapping = _OUTCOME_MAPS[kind]
    if mapping is None:
        mapping = (
            _RECONSTRUCTION_EXCLUSIVE_MAP
            if admits_one_object(record.request.target.relation)
            else _RECONSTRUCTION_SET_VALUED_MAP
        )
    raw = getattr(record, attribute, None)

    if record.error is not None:
        outcome, status = StructuralOutcome.UNRESOLVED, CheckExecutionStatus.FAILED
    elif raw is None:
        # Malformed or unparsable. Not support, and certainly not contradiction.
        outcome, status = StructuralOutcome.UNRESOLVED, CheckExecutionStatus.UNRESOLVED
    else:
        outcome = mapping[raw.value]
        status = (
            CheckExecutionStatus.UNRESOLVED
            if outcome is StructuralOutcome.UNRESOLVED
            else CheckExecutionStatus.RESOLVED
        )

    return StructuralCheckEvidence(
        check_kind=kind,
        independence_group=record.independence_group
        or record.request.check_kind.independence_group,
        outcome=outcome,
        status=status,
        origin_event_id=record.origin_event_id,
        model_id=record.model_id,
        model_family=record.model_family,
        candidate_shown=record.candidate_shown,
        cross_model_credit=cross_model_credit(
            record, target_key=target_key, prior_families=prior_families,
            previously_known=previously_known,
        ),
        counterfactual_class=record.request.check.counterfactual_class,
        parse_status=record.parse_status.value,
        raw_outcome=raw.value if raw is not None else "",
        recovered_value=getattr(record, "recovered_value", "") or "",
        calls=record.calls,
        error=record.error,
    )


def cross_model_credit(
    record: Any,
    *,
    target_key: str,
    prior_families: Mapping[str, Sequence[str]] | None,
    previously_known: bool = True,
) -> CrossModelCredit:
    """Whether one record may credit ``X``, and if not, exactly why.

    Every condition of the audited rule is checked in order, and each failure
    has its own name so a reader can see which one stopped it:

    1. the mechanism must hide the candidate - a shown candidate is anchored
       agreement, which Audit 0008 excluded for the blind verifier and Audit
       0026 excluded for reverse and counterfactual alike;
    2. it must be an independent recall, not a masked reconstruction;
    3. the candidate must actually have been named;
    4. the candidate must have been held before. A candidate this probe
       *discovered* has been produced by exactly one family, and one family is
       not cross-family corroboration;
    5. the answering family must not already be one that produced the
       candidate - otherwise it is a resample of a family, not a second family;
    6. the candidate's prior families must be **knowable**. When they are not,
       no credit is given: a false negative costs a missed signal, an
       unsupported credit corrupts an audited channel.
    """
    if record.candidate_shown:
        return CrossModelCredit.SHOWN_CANDIDATE
    if not record.independent_recall:
        return CrossModelCredit.NOT_INDEPENDENT_RECALL
    if not target_key or not any(
        c.candidate_key == target_key for c in record.recalled_candidates
    ):
        return CrossModelCredit.TARGET_NOT_RECALLED
    if not previously_known:
        return CrossModelCredit.FIRST_DISCOVERY

    family = record.model_family
    if prior_families is None or target_key not in prior_families:
        return CrossModelCredit.UNRESOLVED_PROVENANCE
    known = {f for f in prior_families[target_key] if f}
    if not known or not family:
        return CrossModelCredit.UNRESOLVED_PROVENANCE
    if family in known:
        return CrossModelCredit.SAME_FAMILY
    return CrossModelCredit.CREDITED


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------


def _verifier_origin(reading: Any, result: Any) -> tuple[str, str, str]:
    """Identity of one Module 17 physical reading: model, template, prompt."""
    return (reading.model_id, reading.template_id, reading.prompt_sha256)


def check_origin_consistency(records: Sequence[Any]) -> None:
    """Refuse Module 18 records that claim one origin but describe two sources."""
    seen: dict[str, Any] = {}
    for record in records:
        first = seen.setdefault(record.origin_event_id, record)
        if first is record:
            continue
        for field_name in _IMMUTABLE_ORIGIN_FIELDS:
            if getattr(first, field_name) != getattr(record, field_name):
                raise Layer4ProvenanceError(
                    f"origin {record.origin_event_id} is claimed twice with "
                    f"different {field_name}: {getattr(first, field_name)!r} vs "
                    f"{getattr(record, field_name)!r}"
                )
        if first.request.check_kind is not record.request.check_kind:
            raise Layer4ProvenanceError(
                f"origin {record.origin_event_id} is claimed by two check kinds: "
                f"{first.request.check_kind.value} vs {record.request.check_kind.value}"
            )


def cost_ledger(
    verifications: Sequence[Any], records: Sequence[Any]
) -> Layer4CostLedger:
    """Physical calls this view represents, each counted once.

    Module 17's readings are deduplicated on (model, template, prompt) so one
    persisted result projected twice is still one call; Module 18's are
    deduplicated on their own deterministic origin id. One Module 18 output
    naming five candidates stays one call.
    """
    verifier_seen: set[tuple[str, str, str]] = set()
    verifier_calls = 0
    generated = prompt_tokens = 0
    for result in verifications:
        for reading in result.template_results:
            key = _verifier_origin(reading, result)
            if key in verifier_seen:
                continue
            verifier_seen.add(key)
            verifier_calls += reading.calls
            generated += reading.generated_tokens
            prompt_tokens += reading.prompt_tokens

    structural_seen: set[str] = set()
    structural_calls = 0
    for record in records:
        if record.origin_event_id in structural_seen:
            continue
        structural_seen.add(record.origin_event_id)
        structural_calls += record.calls
        generated += record.generated_tokens
        prompt_tokens += record.prompt_tokens

    return Layer4CostLedger(
        verifier_calls=verifier_calls,
        structural_calls=structural_calls,
        unique_origin_events=len(verifier_seen) + len(structural_seen),
        generated_tokens=generated,
        prompt_tokens=prompt_tokens,
        integration_calls=0,
    )


def structural_groups(
    checks: Sequence[StructuralCheckEvidence],
) -> tuple[StructuralGroupSupport, ...]:
    """``q_g = max`` over Layer-4 structural groups.

    Ten reverse checks are ten origins and one group contribution; three
    counterfactual classes are three provenance entries and one group. Only a
    hidden-candidate group is *recall*, so only it can raise ``I``: an anchored
    check is not an independent source however differently it was phrased.
    """
    buckets: dict[str, list[StructuralCheckEvidence]] = {}
    for check in checks:
        buckets.setdefault(check.independence_group, []).append(check)

    out: list[StructuralGroupSupport] = []
    for group_key in sorted(buckets):
        members = buckets[group_key]
        out.append(StructuralGroupSupport(
            group_key=group_key,
            q_g=1 if any(c.supports for c in members) else 0,
            total_events=len(members),
            origin_event_ids=tuple(sorted({c.origin_event_id for c in members})),
            is_recall=all(not c.candidate_shown for c in members)
            and any(c.check_kind == "CANDIDATE_FREE_RECALL" for c in members),
        ))
    return tuple(out)


# --------------------------------------------------------------------------
# The integrator
# --------------------------------------------------------------------------


class Layer4EvidenceIntegrator:
    """The Layer-4 boundary. Deterministic, non-neural, read-only."""

    def __init__(self, config: Layer4IntegrationConfig | None = None) -> None:
        self.config = config or Layer4IntegrationConfig(enabled=True)
        if self.config.mode not in Layer4IntegrationConfig.SUPPORTED_MODES:
            raise Layer4IntegrationError(
                f"unsupported layer4 integration mode {self.config.mode!r}"
            )

    @property
    def integration_version(self) -> str:
        return self.config.integration_version

    def integrate(
        self,
        consensus: Any,
        *,
        verifications: Sequence[Any] = (),
        checks: Sequence[Any] = (),
        prior_families: Mapping[str, Sequence[str]] | None = None,
    ) -> Layer4EvidenceState:
        """Project M16 + executed M17 + executed M18 into one evidence state.

        ``prior_families`` maps a candidate key to the model families that
        already produced it. Supplied by the caller because Module 16's
        persisted state does not carry it; when a candidate is absent from the
        mapping, cross-model credit is withheld rather than guessed.
        """
        self._check_identity(consensus, verifications, checks)
        check_origin_consistency(checks)

        by_candidate = {c.candidate_key: base_overlay(c) for c in consensus.candidates}
        # Which candidates Module 16 already held, fixed before Module 18 can
        # add any: a candidate this pass discovers has exactly one family
        # behind it and cannot be cross-family corroborated by that same pass.
        base_keys = frozenset(by_candidate)
        clusters = {
            c.cluster_index: numeric_overlay(c) for c in consensus.numeric_clusters
        }
        propositions: dict[str, PropositionEvidenceOverlay] = {}
        candidate_checks: dict[str, list[StructuralCheckEvidence]] = {}
        cluster_checks: dict[int, list[StructuralCheckEvidence]] = {}
        errors: list[str] = []

        # -- Module 17 ------------------------------------------------------
        for result in verifications:
            target = result.request.target
            evidence = specialist_evidence(result)
            kind = target.kind.value
            if kind == "ENTITY_CANDIDATE":
                overlay = by_candidate.get(target.target_id)
                if overlay is None:
                    raise Layer4IntegrationError(
                        f"Module 17 verified {target.target_id!r}, which Module 16 "
                        "does not hold; the two describe different candidate sets"
                    )
                by_candidate[target.target_id] = replace(
                    overlay, specialist_verifier=evidence
                )
            elif kind == "NUMERIC_CLUSTER":
                index = target.numeric_cluster_index
                if index not in clusters:
                    raise Layer4IntegrationError(
                        f"Module 17 verified numeric cluster {index}, which Module "
                        "16 does not hold"
                    )
                clusters[index] = replace(clusters[index], specialist_verifier=evidence)
            else:
                # A query-level proposition never becomes a candidate.
                propositions[target.target_id] = PropositionEvidenceOverlay(
                    proposition=target.target_id, specialist_verifier=evidence,
                )

        # -- Module 18 ------------------------------------------------------
        for record in checks:
            target = record.request.target
            kind = target.kind.value
            if kind == "NUMERIC_CLUSTER":
                index = target.numeric_cluster_index
                if index not in clusters:
                    raise Layer4IntegrationError(
                        f"Module 18 checked numeric cluster {index}, which Module "
                        "16 does not hold"
                    )
                cluster_checks.setdefault(index, []).append(structural_evidence(
                    record, target_key=target.target_id, prior_families=prior_families,
                ))
                continue

            if kind == "QUERY":
                # A candidate-free probe is about the query. Its reading is
                # attributed to each candidate it actually named.
                for recalled in record.recalled_candidates:
                    evidence = structural_evidence(
                        record, target_key=recalled.candidate_key,
                        prior_families=prior_families,
                        previously_known=recalled.candidate_key in base_keys,
                    )
                    if recalled.candidate_key not in by_candidate:
                        by_candidate[recalled.candidate_key] = CandidateEvidenceOverlay(
                            candidate_key=recalled.candidate_key,
                            display=recalled.surface,
                            discovered_by_structural_check=True,
                        )
                    candidate_checks.setdefault(
                        recalled.candidate_key, []
                    ).append(evidence)
                if not record.recalled_candidates:
                    errors.extend(
                        [] if record.error is None else [record.error]
                    )
                continue

            overlay = by_candidate.get(target.target_id)
            if overlay is None:
                raise Layer4IntegrationError(
                    f"Module 18 checked {target.target_id!r}, which Module 16 does "
                    "not hold"
                )
            candidate_checks.setdefault(target.target_id, []).append(
                structural_evidence(
                    record, target_key=target.target_id, prior_families=prior_families,
                )
            )

        candidates = tuple(
            self._finish(by_candidate[key], candidate_checks.get(key, ()))
            for key in sorted(by_candidate)
        )
        numeric_targets = tuple(
            replace(clusters[index], structural_checks=tuple(cluster_checks.get(index, ())))
            for index in sorted(clusters)
        )

        return Layer4EvidenceState(
            integration_version=self.integration_version,
            relation=consensus.relation, subject=consensus.subject,
            row_index=consensus.row_index,
            base_consensus_version=consensus.consensus_version,
            verification_version=(
                verifications[0].verification_version if verifications else ""
            ),
            check_version=checks[0].request.check_version if checks else "",
            candidates=candidates,
            propositions=tuple(propositions[k] for k in sorted(propositions)),
            numeric_targets=numeric_targets,
            null_state=consensus.null_state,
            pending_checks=self._pending(consensus, checks),
            cost=cost_ledger(verifications, checks),
            errors=tuple(errors),
        )

    # -- per-candidate assembly ---------------------------------------------

    @staticmethod
    def _finish(
        overlay: CandidateEvidenceOverlay,
        checks: Sequence[StructuralCheckEvidence],
    ) -> CandidateEvidenceOverlay:
        """Fold this candidate's structural checks in. ``F`` is never touched."""
        groups = structural_groups(checks)
        contradicting = tuple(sorted({
            check.independence_group for check in checks if check.contradicts
        }))
        # I gains only hidden-candidate recall groups. A shown-candidate check
        # is anchored, exactly as a shown-candidate verifier reading is.
        recall_groups = sum(1 for g in groups if g.supports and g.is_recall)
        credit = next(
            (c.cross_model_credit for c in checks if c.cross_model_credit.credits),
            CrossModelCredit.SHOWN_CANDIDATE,
        )
        if not credit.credits and checks:
            # Report the most informative refusal, so the reason is visible.
            for candidate_reason in (
                CrossModelCredit.SAME_FAMILY,
                CrossModelCredit.FIRST_DISCOVERY,
                CrossModelCredit.UNRESOLVED_PROVENANCE,
                CrossModelCredit.TARGET_NOT_RECALLED,
                CrossModelCredit.NOT_INDEPENDENT_RECALL,
            ):
                if any(c.cross_model_credit is candidate_reason for c in checks):
                    credit = candidate_reason
                    break
        return replace(
            overlay,
            structural_checks=tuple(checks),
            structural_groups=groups,
            structural_contradicting_groups=contradicting,
            layer4_i=overlay.base_i + recall_groups,
            # X rises only through the audited cross-model rule, and only to 1.
            layer4_x=max(overlay.base_x, 1.0 if credit.credits else 0.0),
            cross_model_credit=credit,
        )

    # -- pending checks ------------------------------------------------------

    @staticmethod
    def _pending(consensus: Any, checks: Sequence[Any]) -> tuple[PendingCheckStatus, ...]:
        """Reconcile Module 15's requests with what actually ran.

        Execution status only. An executed check has produced a reading; it has
        settled nothing, and an unexecuted request stays pending.
        """
        executed: dict[str, list[Any]] = {}
        for record in checks:
            target = record.request.target
            for key in {target.target_id, target.display}:
                if key:
                    executed.setdefault(key, []).append(record)

        out: list[PendingCheckStatus] = []
        for pending in getattr(consensus, "pending_checks", ()):
            matches = executed.get(pending.candidate, [])
            if not matches:
                status = CheckExecutionStatus.ELIGIBLE_NOT_SCHEDULED
            elif all(m.error is not None for m in matches):
                status = CheckExecutionStatus.FAILED
            else:
                status = CheckExecutionStatus.RESOLVED
            out.append(PendingCheckStatus(
                source_module=pending.source_module, kind=pending.kind,
                reason=pending.reason, candidate=pending.candidate, status=status,
                executed_origin_ids=tuple(sorted(m.origin_event_id for m in matches)),
            ))
        return tuple(out)

    # -- validation ----------------------------------------------------------

    @staticmethod
    def _check_identity(
        consensus: Any, verifications: Sequence[Any], checks: Sequence[Any]
    ) -> None:
        """Refuse inputs that describe different queries."""
        expected = (consensus.relation, consensus.subject, consensus.row_index)
        problems: list[str] = []
        for result in verifications:
            target = result.request.target
            if (target.relation, target.subject, target.row_index) != expected:
                problems.append(
                    f"Module 17 result for {target.subject}/{target.relation}"
                    f"#{target.row_index}"
                )
        for record in checks:
            target = record.request.target
            if (target.relation, target.subject, target.row_index) != expected:
                problems.append(
                    f"Module 18 record for {target.subject}/{target.relation}"
                    f"#{target.row_index}"
                )
        if problems:
            raise Layer4IntegrationError(
                f"Layer-4 integration was given evidence for another query "
                f"({consensus.subject}/{consensus.relation}#{consensus.row_index} "
                f"expected): {'; '.join(sorted(set(problems)))}"
            )


def prior_family_map(graph: Any) -> dict[str, tuple[str, ...]]:
    """Which model families already produced each candidate, from Module 3.

    Read-only over the production graph, and the only defensible source for the
    cross-model rule: Module 16's persisted state carries candidate keys but
    not the families behind them. A candidate missing here leaves its credit
    ``UNRESOLVED_PROVENANCE`` rather than credited.
    """
    return {
        key: tuple(sorted({
            edge.model_family for edge in candidate.all_evidence() if edge.model_family
        }))
        for key, candidate in graph.candidates.items()
    }


def build_layer4_integrator(
    config: Mapping[str, Any] | None, *, consensus_enabled: bool
) -> "Layer4EvidenceIntegrator | None":
    """Build the integrator when configuration asks for it."""
    settings = Layer4IntegrationConfig.from_mapping(config)
    if not settings.enabled:
        return None
    if not consensus_enabled:
        raise ValueError(
            "layer4_integration.enabled requires consensus (M16); the Layer-4 "
            "view is a projection of Module 16's state"
        )
    return Layer4EvidenceIntegrator(settings)


__all__ = [
    "INTEGRATION_VERSION",
    "SPECIALIST_VERIFIER_GROUP",
    "Layer4EvidenceIntegrator",
    "Layer4IntegrationConfig",
    "build_layer4_integrator",
    "check_origin_consistency",
    "cost_ledger",
    "cross_model_credit",
    "prior_family_map",
    "specialist_evidence",
    "structural_evidence",
    "structural_groups",
]
