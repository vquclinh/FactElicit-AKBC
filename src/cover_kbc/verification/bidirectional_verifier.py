"""Module 18 - Bidirectional and Counterfactual Verification.

Architecture position::

    M16 consensus  +  M17 catalogue  +  M15 pending checks   (all READ-ONLY)
            |
            v
    deterministic eligible-check catalogue          <- zero neural calls
            |
            v      the CALLER chooses which checks to execute
    BidirectionalCheckRequest
            |
            v
    frozen runtime supplied by the caller (no checkpoint named here)
            |
            v
    BidirectionalCheckRecord   ->  bidirectional_verification.jsonl

**M18 creates evidence.** §14's four mechanisms produce outcomes -
SUPPORTED/CONTRADICTED, TARGET_RECOVERED/DIFFERENT_VALUE_RECOVERED,
TARGET_RELATION/NEAR_MISS_RELATION, TARGET_RECALLED/TARGET_ABSENT - and nothing
else. A mismatch is evidence, never a rejection; an absence is never a
contradiction; a failed call is never support.

**M18 schedules nothing.** The catalogue says which checks *can* be posed. It
reads no support count, no verifier label and no risk flag, so it cannot
prefer one. A Module 17 UNKNOWN triggers nothing here.

**M18 changes no upstream state.** Modules 3, 5, 16 and 17 are read; a separate
result is written; how this evidence is folded into the unified plane is the
Layer-4 integration that comes next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.normalization.numeric import format_numeric, parse_numbers
from cover_kbc.normalization.strings import is_abstain, official_normalize_string
from cover_kbc.specialists import canonicalise, numeric_spec
from cover_kbc.types import DecodeProfile, OutputType
from cover_kbc.verification.bidirectional_contracts import (
    CHECK_CONTRACT_VERSION,
    CheckProfile,
    check_profile,
    check_registry_consistency,
)
from cover_kbc.verification.bidirectional_prompts import (
    TEMPLATE_IDS,
    RenderedPrompt,
    render_candidate_free,
    render_counterfactual,
    render_key_condition,
    render_reverse,
)
from cover_kbc.verification.bidirectional_types import (
    CHECK_VERSION,
    BidirectionalCheckError,
    BidirectionalCheckKind,
    BidirectionalCheckRecord,
    BidirectionalCheckRequest,
    CheckIneligible,
    CheckParseStatus,
    CheckTarget,
    CheckTargetKind,
    CounterfactualOutcome,
    EligibleCheck,
    PendingCheckOrigin,
    QueryBidirectionalResult,
    RecallOutcome,
    RecalledCandidate,
    ReconstructionOutcome,
    ReverseOutcome,
    derive_check_origin_id,
    prompt_sha256,
    sort_checks,
)

#: Deterministic decoding, as every acquisition path in the system uses.
CHECK_DECODE = DecodeProfile(name="m18_check", temperature=0.0, max_new_tokens=192)
SHORT_DECODE = DecodeProfile(name="m18_short", temperature=0.0, max_new_tokens=32)

#: Module 15 pending-check kinds that name a reverse question.
_REVERSE_PENDING_KINDS = frozenset({"REVERSE_ADJACENCY"})


def _is_abstention(text: str) -> bool:
    """Whether a **whole bounded answer** expresses "no object".

    Module 3's own predicate, reused rather than restated - the same one Audit
    0024's correction relies on, so NONE, UNKNOWN, "I don't know" and n/a can
    never become a candidate on any Module 18 path either.

    Deliberately *not* Module 14's ``is_epistemic_abstention``: that one also
    scans for its phrases as substrings, which is right for a free-form Stage-B
    sentence and wrong here - every Module 18 frame asks for a bounded answer,
    and a substring rule would read "banana" as an abstention because it
    contains "na".
    """
    # ``official_normalize_string`` strips punctuation, so "!!!" normalises to
    # the empty string and would otherwise read as "no object". An answer that
    # normalises to nothing is unreadable, not an abstention.
    return bool(official_normalize_string(text)) and is_abstain(text)


@dataclass(frozen=True)
class BidirectionalVerifierConfig:
    """Module 18 configuration.

    Nothing fitted, nothing tunable and nothing that could schedule: no
    threshold, no weight, no retry count, no automatic escalation. ``shadow``
    is the only supported mode, and no check runs without an explicit request.
    """

    enabled: bool = False
    mode: str = "shadow"
    check_version: str = CHECK_VERSION
    #: Which §14 mechanisms the catalogue may offer. All four by default.
    supported_checks: tuple[BidirectionalCheckKind, ...] = tuple(BidirectionalCheckKind)

    SUPPORTED_MODES = frozenset({"shadow"})

    @classmethod
    def from_mapping(
        cls, config: Mapping[str, Any] | None
    ) -> "BidirectionalVerifierConfig":
        payload = dict(config or {})
        known = {"enabled", "mode", "check_version", "supported_checks"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"unknown bidirectional_verification key(s) {unknown}; expected "
                f"{sorted(known)}"
            )
        version = str(payload.get("check_version", CHECK_VERSION))
        if version != CHECK_VERSION:
            raise ValueError(
                f"unsupported check_version {version!r}; this build implements "
                f"{CHECK_VERSION!r}"
            )
        mode = str(payload.get("mode", "shadow"))
        if mode not in cls.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported bidirectional_verification mode {mode!r}; this "
                f"milestone implements {sorted(cls.SUPPORTED_MODES)} only"
            )
        raw_checks = payload.get("supported_checks")
        if raw_checks is None:
            checks = tuple(BidirectionalCheckKind)
        else:
            if isinstance(raw_checks, str) or not isinstance(raw_checks, (list, tuple)):
                raise ValueError(
                    "bidirectional_verification.supported_checks must be a list"
                )
            checks = []
            for value in raw_checks:
                try:
                    checks.append(BidirectionalCheckKind(str(value)))
                except ValueError as exc:
                    raise ValueError(
                        f"unknown check kind {value!r}; §14 defines "
                        f"{[k.value for k in BidirectionalCheckKind]}"
                    ) from exc
            checks = tuple(dict.fromkeys(checks))
            if not checks:
                raise ValueError(
                    "bidirectional_verification.supported_checks must name at "
                    "least one §14 mechanism"
                )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=mode,
            check_version=version,
            supported_checks=checks,
        )


# --------------------------------------------------------------------------
# The deterministic eligible-check catalogue
# --------------------------------------------------------------------------


def _target_from_state(relation: str, subject: str, row_index: int, state: Any) -> CheckTarget:
    return CheckTarget(
        relation=relation, subject=subject, row_index=row_index,
        kind=CheckTargetKind.ENTITY_CANDIDATE,
        target_id=state.candidate_key, display=state.display,
        hard_contract_violation=state.hard_contract_violation,
    )


def _target_from_cluster(
    relation: str, subject: str, row_index: int, cluster: Any
) -> CheckTarget:
    return CheckTarget(
        relation=relation, subject=subject, row_index=row_index,
        kind=CheckTargetKind.NUMERIC_CLUSTER,
        target_id=str(cluster.cluster_index),
        display=f"{cluster.representative:g} {cluster.canonical_unit}".strip(),
        numeric_cluster_index=cluster.cluster_index,
        canonical_unit=cluster.canonical_unit,
    )


def _eligibility(
    kind: BidirectionalCheckKind, profile: CheckProfile, target: CheckTarget
) -> tuple[bool, CheckIneligible | None]:
    """Can this check be posed at all? A **type** judgement, never a value one."""
    if not profile.supports(kind):
        reason = (
            CheckIneligible.RELATION_HAS_NO_REVERSE
            if kind is BidirectionalCheckKind.REVERSE
            else CheckIneligible.CHECK_NOT_DECLARED_FOR_RELATION
        )
        return False, reason
    if kind is BidirectionalCheckKind.CANDIDATE_FREE_RECALL:
        return True, None
    if target.kind is not profile.target_kind:
        return False, CheckIneligible.UNSUPPORTED_TARGET_KIND
    if target.hard_contract_violation:
        return False, CheckIneligible.HARD_CONTRACT_VIOLATION
    if kind.shows_candidate and not target.display:
        return False, CheckIneligible.NO_PRINTABLE_VALUE
    return True, None


def eligible_checks(
    consensus: Any,
    *,
    supported: Sequence[BidirectionalCheckKind] = tuple(BidirectionalCheckKind),
) -> tuple[EligibleCheck, ...]:
    """Every §14 check that *could* be posed for one query. Zero calls.

    Reads Module 16's candidate identity, cluster identity and pending-check
    descriptors - and nothing else. It never reads ``F``, ``I``, ``D``, a
    verifier label or a risk flag, so it cannot rank, prefer or select.
    Module 15's pending checks are attached as provenance on the checks they
    asked for; they change no prompt.
    """
    profile = check_profile(consensus.relation)
    relation, subject, row = consensus.relation, consensus.subject, consensus.row_index
    allowed = tuple(k for k in BidirectionalCheckKind if k in set(supported))

    targets: list[CheckTarget] = []
    if profile.target_kind is CheckTargetKind.ENTITY_CANDIDATE:
        targets.extend(
            _target_from_state(relation, subject, row, state)
            for state in consensus.candidates
        )
    else:
        targets.extend(
            _target_from_cluster(relation, subject, row, cluster)
            for cluster in consensus.numeric_clusters
        )

    pending_by_candidate: dict[str, PendingCheckOrigin] = {}
    for pending in getattr(consensus, "pending_checks", ()):
        pending_by_candidate.setdefault(
            pending.candidate,
            PendingCheckOrigin(
                source_module=pending.source_module, kind=pending.kind,
                reason=pending.reason, detail=pending.detail,
            ),
        )

    contract_classes = _class_ids(relation)
    checks: list[EligibleCheck] = []

    for target in targets:
        requested = pending_by_candidate.get(target.display) or (
            pending_by_candidate.get(target.target_id)
        )
        for kind in allowed:
            if kind is BidirectionalCheckKind.CANDIDATE_FREE_RECALL:
                continue
            eligible, reason = _eligibility(kind, profile, target)
            if kind is BidirectionalCheckKind.COUNTERFACTUAL:
                for class_id in contract_classes:
                    checks.append(EligibleCheck(
                        check_kind=kind, target=target, eligible=eligible,
                        ineligible_reason=reason, counterfactual_class=class_id,
                        requested_by=requested,
                    ))
                continue
            checks.append(EligibleCheck(
                check_kind=kind, target=target, eligible=eligible,
                ineligible_reason=reason,
                # Only a reverse pending descriptor motivates a reverse check.
                requested_by=(
                    requested if (
                        requested is None
                        or kind is not BidirectionalCheckKind.REVERSE
                        or requested.kind in _REVERSE_PENDING_KINDS
                    ) else None
                ),
            ))

    if BidirectionalCheckKind.CANDIDATE_FREE_RECALL in allowed:
        query_target = CheckTarget(
            relation=relation, subject=subject, row_index=row,
            kind=CheckTargetKind.QUERY,
            # Comparison keys only. The candidate-free renderer never receives
            # a target, so these cannot reach a prompt.
            known_candidate_keys=tuple(sorted(t.target_id for t in targets if t.target_id)),
        )
        eligible, reason = _eligibility(
            BidirectionalCheckKind.CANDIDATE_FREE_RECALL, profile, query_target
        )
        checks.append(EligibleCheck(
            check_kind=BidirectionalCheckKind.CANDIDATE_FREE_RECALL,
            target=query_target, eligible=eligible, ineligible_reason=reason,
        ))

    return sort_checks(checks)


def _class_ids(relation: str) -> tuple[str, ...]:
    from cover_kbc.contracts.registry import CONTRACTS

    return check_profile(relation).counterfactual_classes(CONTRACTS[relation])


# --------------------------------------------------------------------------
# Parsing - bounded, deterministic, and never generous
# --------------------------------------------------------------------------


def parse_reverse(text: str) -> tuple[ReverseOutcome | None, CheckParseStatus]:
    stripped = (text or "").strip()
    if not stripped:
        return None, CheckParseStatus.EMPTY
    folded = stripped.casefold().strip(".!,")
    if folded.startswith("supported"):
        return ReverseOutcome.SUPPORTED, CheckParseStatus.OK
    if folded.startswith("contradicted"):
        return ReverseOutcome.CONTRADICTED, CheckParseStatus.OK
    if folded.startswith("unresolved") or _is_abstention(stripped):
        return ReverseOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED
    # An answer nobody can read is not a contradiction.
    return None, CheckParseStatus.MALFORMED


def parse_counterfactual(
    text: str,
) -> tuple[CounterfactualOutcome | None, CheckParseStatus]:
    stripped = (text or "").strip()
    if not stripped:
        return None, CheckParseStatus.EMPTY
    folded = stripped.casefold().strip(".!,")
    if folded.startswith("target"):
        return CounterfactualOutcome.TARGET_RELATION, CheckParseStatus.OK
    if folded.startswith("excluded"):
        return CounterfactualOutcome.NEAR_MISS_RELATION, CheckParseStatus.OK
    if folded.startswith("neither"):
        return CounterfactualOutcome.NEITHER, CheckParseStatus.OK
    if folded.startswith("unknown") or _is_abstention(stripped):
        return CounterfactualOutcome.UNRESOLVED, CheckParseStatus.ABSTAINED
    return None, CheckParseStatus.MALFORMED


def _entity_surfaces(text: str) -> list[str]:
    """Atomic surfaces from a bounded name list. No alias folding, no merging."""
    out: list[str] = []
    for line in (text or "").splitlines():
        for piece in line.split(";"):
            surface = piece.strip().strip("-*•").strip().strip("\"'")
            if surface and surface not in out:
                out.append(surface)
    return out


def parse_reconstruction(
    text: str,
    contract: RelationContract,
    profile: CheckProfile,
    *,
    target_display: str,
) -> tuple[ReconstructionOutcome | None, str, CheckParseStatus]:
    """Read one recovered value and compare it with the target.

    Comparison is **strict**: Module 3's own key for entities, Module 12's own
    canonicalisation and formatting for quantities. No fuzzy matching, no
    embedding, no alias resolver, and no second clustering rule.

    A different recovered value is evidence, not a rejection.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None, "", CheckParseStatus.EMPTY
    if _is_abstention(stripped):
        return ReconstructionOutcome.UNRESOLVED, "", CheckParseStatus.ABSTAINED

    if profile.reconstruction_output == "quantity":
        recovered_key, status = _canonical_numeric_key(stripped, contract)
        if recovered_key is None:
            return None, "", status
        target_key, _ = _canonical_numeric_key(target_display, contract)
        outcome = (
            ReconstructionOutcome.TARGET_RECOVERED
            if target_key is not None and recovered_key == target_key
            else ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED
        )
        return outcome, recovered_key, CheckParseStatus.OK

    surfaces = _entity_surfaces(stripped)
    if not surfaces:
        return None, "", CheckParseStatus.MALFORMED
    recovered = surfaces[0]
    if _is_abstention(recovered):
        return ReconstructionOutcome.UNRESOLVED, "", CheckParseStatus.ABSTAINED
    key = contract.strict_key(recovered)
    if not key:
        # Nothing keyable came back: an unreadable answer, not a different
        # value and certainly not a rejection of the target.
        return None, "", CheckParseStatus.MALFORMED
    target_key = contract.strict_key(target_display) if target_display else ""
    outcome = (
        ReconstructionOutcome.TARGET_RECOVERED
        if key and target_key and key == target_key
        else ReconstructionOutcome.DIFFERENT_VALUE_RECOVERED
    )
    return outcome, recovered, CheckParseStatus.OK


def _canonical_numeric_key(
    text: str, contract: RelationContract
) -> tuple[str | None, CheckParseStatus]:
    """Module 12's canonical value, formatted with Module 3's own key rule."""
    values = parse_numbers(text)
    if not values:
        return None, CheckParseStatus.NUMERIC_PARSE_FAILED
    spec = numeric_spec(contract.relation)
    canonical, status, _ = canonicalise(values[0], spec)
    if canonical is None:
        return None, CheckParseStatus.NUMERIC_PARSE_FAILED
    del status
    return format_numeric(
        canonical, integer_only=contract.selection.numeric_integer_only
    ), CheckParseStatus.OK


def parse_candidate_free(
    text: str,
    contract: RelationContract,
    profile: CheckProfile,
    *,
    target_keys: Sequence[str] = (),
) -> tuple[RecallOutcome | None, tuple[RecalledCandidate, ...], CheckParseStatus]:
    """Read a candidate-free probe. The target is used **only here**, after.

    Audit 0024's abstention semantics are reused rather than re-derived: NONE,
    UNKNOWN and "I don't know" never become a candidate on any path, and an
    empty recall is `NOTHING_RECALLED` rather than a contradiction.
    """
    stripped = (text or "").strip()
    if not stripped:
        return RecallOutcome.NOTHING_RECALLED, (), CheckParseStatus.EMPTY

    if profile.candidate_free_output == "quantity":
        # One value is expected, so the whole answer may be an abstention.
        if _is_abstention(stripped):
            return RecallOutcome.NOTHING_RECALLED, (), CheckParseStatus.ABSTAINED
        key, status = _canonical_numeric_key(stripped, contract)
        if key is None:
            return None, (), status
        candidate = RecalledCandidate(
            surface=key, candidate_key=key, is_target=key in set(target_keys)
        )
        outcome = (
            RecallOutcome.TARGET_RECALLED if candidate.is_target
            else RecallOutcome.TARGET_ABSENT
        )
        return outcome, (candidate,), CheckParseStatus.OK

    recalled: list[RecalledCandidate] = []
    for surface in _entity_surfaces(stripped):
        if _is_abstention(surface):
            continue
        key = contract.strict_key(surface)
        if not key:
            continue
        recalled.append(RecalledCandidate(
            surface=surface, candidate_key=key, is_target=key in set(target_keys),
        ))
    if not recalled:
        # Every surface was an abstention token, or none could be keyed. The
        # whole answer is deliberately *not* tested for abstention up front:
        # Module 3 applies that predicate per surface, and applying it to a
        # multi-line list would discard a real recall whose first line happened
        # to be NONE.
        status = (
            CheckParseStatus.ABSTAINED if _is_abstention(stripped)
            else CheckParseStatus.OK
        )
        return RecallOutcome.NOTHING_RECALLED, (), status
    outcome = (
        RecallOutcome.TARGET_RECALLED
        if any(c.is_target for c in recalled) else RecallOutcome.TARGET_ABSENT
    )
    return outcome, tuple(recalled), CheckParseStatus.OK


# --------------------------------------------------------------------------
# The executor
# --------------------------------------------------------------------------


class BidirectionalVerifier:
    """§14's four mechanisms. Catalogues freely; executes only on request."""

    def __init__(self, config: BidirectionalVerifierConfig | None = None) -> None:
        self.config = config or BidirectionalVerifierConfig(enabled=True)
        if self.config.mode not in BidirectionalVerifierConfig.SUPPORTED_MODES:
            raise BidirectionalCheckError(
                f"unsupported bidirectional mode {self.config.mode!r}"
            )
        check_registry_consistency()

    @property
    def check_version(self) -> str:
        return self.config.check_version

    # -- catalogue -----------------------------------------------------------

    def catalogue(self, consensus: Any) -> tuple[EligibleCheck, ...]:
        """Every check that could be posed. **Zero neural calls.**"""
        return eligible_checks(consensus, supported=self.config.supported_checks)

    def build_request(
        self, check: EligibleCheck, *, model_role: str = "enumerator",
        sample_index: int = 0,
    ) -> BidirectionalCheckRequest:
        """Turn one caller-chosen check into a typed request."""
        profile = check_profile(check.target.relation)
        if not profile.supports(check.check_kind):
            raise BidirectionalCheckError(
                f"{check.target.relation} declares no {check.check_kind.value} "
                f"check: {profile.reverse_rationale or profile.rationale}"
            )
        if check.check_kind not in self.config.supported_checks:
            raise BidirectionalCheckError(
                f"{check.check_kind.value} is not enabled in configuration"
            )
        if check.check_kind is BidirectionalCheckKind.COUNTERFACTUAL:
            # Raises if the class is not one of Module 0's own rules.
            CheckProfile.class_text(
                _contract_for(check.target.relation), check.counterfactual_class
            )
        return BidirectionalCheckRequest(
            check=check, template_id=TEMPLATE_IDS[check.check_kind],
            model_role=model_role, sample_index=sample_index,
            check_version=self.check_version,
        )

    # -- execution -----------------------------------------------------------

    def execute(
        self,
        request: BidirectionalCheckRequest,
        contract: RelationContract,
        runtime: LMRuntime,
        *,
        primary_model_family: str = "",
    ) -> BidirectionalCheckRecord:
        """Run one requested check. One physical call, one origin.

        ``primary_model_family`` is the family that produced the candidate. It
        decides only whether a **candidate-free** record is marked
        cross-model *eligible*; nothing here credits ``X``.
        """
        target = request.target
        if contract.relation != target.relation:
            raise BidirectionalCheckError(
                f"contract is for {contract.relation!r} but the target is "
                f"{target.relation!r}"
            )
        if not request.check.eligible:
            raise BidirectionalCheckError(
                f"{target.target_id or target.subject}: this check is not "
                f"eligible ({request.check.ineligible_reason.value if request.check.ineligible_reason else 'unspecified'})"
            )

        profile = check_profile(target.relation)
        rendered = self._render(request, profile, contract)
        digest = prompt_sha256(rendered.prompt, rendered.system_prompt)
        spec = getattr(runtime, "spec", None)
        model_id = getattr(spec, "model_id", "unknown")
        family = getattr(spec, "family", "") or ""
        origin = derive_check_origin_id(
            model_id=model_id, operation_id=request.operation_id,
            prompt_sha256=digest, sample_index=request.sample_index,
        )
        common = dict(
            request=request, origin_event_id=origin, prompt_sha256=digest,
            model_id=model_id, model_revision=getattr(spec, "revision", ""),
            model_family=family,
            independence_group=request.check_kind.independence_group,
            candidate_shown=rendered.candidate_shown,
        )

        started = time.perf_counter()
        try:
            result = runtime.generate(GenerationRequest(
                prompt=rendered.prompt,
                system_prompt=rendered.system_prompt,
                decode=(
                    SHORT_DECODE if request.check_kind in (
                        BidirectionalCheckKind.REVERSE,
                        BidirectionalCheckKind.COUNTERFACTUAL,
                    ) else CHECK_DECODE
                ),
                metadata={
                    "view_id": request.operation_id,
                    "subject": target.subject,
                    "relation": target.relation,
                    "module": "M18",
                    "check_kind": request.check_kind.value,
                    "target_id": target.target_id,
                    "template_id": request.template_id,
                },
            ))
        except Exception as exc:  # noqa: BLE001
            # A failed call is not a contradiction and not support.
            return BidirectionalCheckRecord(
                **common, parse_status=CheckParseStatus.RUNTIME_ERROR, calls=1,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )

        text = (result.text or "").strip()
        record = dict(
            common, raw_output=text, calls=1,
            prompt_tokens=int(result.prompt_tokens or 0),
            generated_tokens=int(result.generated_tokens or 0),
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        record["model_id"] = result.model_id or model_id
        return self._parse(request, profile, contract, text, record,
                           primary_model_family=primary_model_family)

    def execute_all(
        self,
        consensus: Any,
        contract: RelationContract,
        runtime: LMRuntime,
        requests: Sequence[BidirectionalCheckRequest],
        *,
        primary_model_family: str = "",
    ) -> QueryBidirectionalResult:
        """Execute exactly the requests the caller supplied, and no others."""
        records = [
            self.execute(request, contract, runtime,
                         primary_model_family=primary_model_family)
            for request in requests
        ]
        return QueryBidirectionalResult(
            check_version=self.check_version,
            relation=consensus.relation, subject=consensus.subject,
            row_index=consensus.row_index,
            catalogue=self.catalogue(consensus),
            records=tuple(records),
            errors=tuple(r.error for r in records if r.error),
        )

    # -- rendering and parsing ----------------------------------------------

    @staticmethod
    def _render(
        request: BidirectionalCheckRequest,
        profile: CheckProfile,
        contract: RelationContract,
    ) -> RenderedPrompt:
        target = request.target
        kind = request.check_kind
        if kind is BidirectionalCheckKind.REVERSE:
            return render_reverse(
                profile, contract, subject=target.subject, candidate=target.display
            )
        if kind is BidirectionalCheckKind.KEY_CONDITION:
            return render_key_condition(profile, contract, subject=target.subject)
        if kind is BidirectionalCheckKind.COUNTERFACTUAL:
            return render_counterfactual(
                profile, contract, subject=target.subject, candidate=target.display,
                counterfactual_class=request.check.counterfactual_class,
            )
        return render_candidate_free(profile, contract, subject=target.subject)

    def _parse(
        self,
        request: BidirectionalCheckRequest,
        profile: CheckProfile,
        contract: RelationContract,
        text: str,
        record: dict[str, Any],
        *,
        primary_model_family: str,
    ) -> BidirectionalCheckRecord:
        kind = request.check_kind
        target = request.target

        if kind is BidirectionalCheckKind.REVERSE:
            outcome, status = parse_reverse(text)
            return BidirectionalCheckRecord(
                **record, reverse_outcome=outcome, parse_status=status
            )

        if kind is BidirectionalCheckKind.COUNTERFACTUAL:
            outcome, status = parse_counterfactual(text)
            return BidirectionalCheckRecord(
                **record, counterfactual_outcome=outcome, parse_status=status
            )

        if kind is BidirectionalCheckKind.KEY_CONDITION:
            outcome, recovered, status = parse_reconstruction(
                text, contract, profile, target_display=target.display
            )
            return BidirectionalCheckRecord(
                **record, reconstruction_outcome=outcome, recovered_value=recovered,
                parse_status=status,
            )

        outcome, recalled, status = parse_candidate_free(
            text, contract, profile, target_keys=self._target_keys(contract, target)
        )
        # §14 says a natural appearance in an independent probe increases X.
        # Audit 0008 defines X as *cross-model* independent recall, so M18
        # records eligibility and credits nothing: only a hidden candidate
        # recalled by a genuinely distinct family can qualify, and the final
        # decision belongs to the Layer-4 integration.
        family = record.get("model_family") or ""
        distinct = bool(family and primary_model_family and family != primary_model_family)
        return BidirectionalCheckRecord(
            **record, recall_outcome=outcome, recalled_candidates=recalled,
            parse_status=status, independent_recall=True,
            cross_model_eligible=distinct,
        )

    @staticmethod
    def _target_keys(contract: RelationContract, target: CheckTarget) -> tuple[str, ...]:
        """Keys a recalled candidate is compared against, **after** inference.

        Strict identity only - Module 3's key for entities, Module 12's
        canonical value for quantities. No alias folding, no fuzzy matching.
        """
        if target.known_candidate_keys:
            return target.known_candidate_keys
        if not target.display:
            return ()
        if contract.output_type is OutputType.NUMBER:
            key, _ = _canonical_numeric_key(target.display, contract)
            return (key,) if key else ()
        key = contract.strict_key(target.display)
        return (key,) if key else ()


def _contract_for(relation: str) -> RelationContract:
    from cover_kbc.contracts.registry import CONTRACTS

    return CONTRACTS[relation]


def build_bidirectional_verifier(
    config: Mapping[str, Any] | None,
    *,
    consensus_enabled: bool,
) -> "BidirectionalVerifier | None":
    """Build M18 when configuration asks for it, refusing a broken wiring."""
    settings = BidirectionalVerifierConfig.from_mapping(config)
    if not settings.enabled:
        return None
    if not consensus_enabled:
        raise ValueError(
            "bidirectional_verification.enabled requires consensus (M16); "
            "Module 18 checks targets Module 16 identifies"
        )
    return BidirectionalVerifier(settings)


__all__ = [
    "CHECK_CONTRACT_VERSION",
    "CHECK_DECODE",
    "SHORT_DECODE",
    "BidirectionalVerifier",
    "BidirectionalVerifierConfig",
    "build_bidirectional_verifier",
    "eligible_checks",
    "parse_candidate_free",
    "parse_counterfactual",
    "parse_reconstruction",
    "parse_reverse",
]
