"""Layer-6 integration: one owner-authoritative catalogue of legal actions.

**This is an integration seam, not a new module.** It declares no legality of
its own. Every action here is projected from a surface its owning module
already publishes, and the projection is a pure read - no call, no mutation, no
reservation.

Audit 0031 recorded that Module 21's live legal-action list was empty because
"no module yet exposes an owner-declared legal-action surface". Inspecting the
source for this milestone shows that claim was **too strong**: three owners
already publish exactly such a surface -

* ``controller.legal_actions`` - Module 7's own legality engine, which already
  refuses a view that ran, a candidate already reverse-checked, and a resample
  before structural mechanisms are exhausted;
* ``specialist_verifier.verifiable_targets`` - Module 17's eligibility
  catalogue, with ``eligible`` and an ineligibility reason;
* ``bidirectional_verifier.eligible_checks`` - Module 18's, likewise.

What was genuinely missing is a **unified adapter**, which is what this file
is. The specialists are projected from their live registries plus their own
execution records, so a disabled facet, an unsatisfied gate or a spent one-shot
probe never becomes an action.

Three rules hold throughout:

**Legality is the owner's word.** A high residual, a risky query, an unused
reserve or a large expected utility never create an action. They may make an
existing action more valuable, which is Module 21's business, and they may make
one unaffordable, which is Module 20's; neither can make one *permitted*.

**One semantic action has one canonical identity.** When both core Module 7 and
a specialist can express the same work, the more specific owner wins and the
generic duplicate is **recorded as suppressed**, never silently dropped -
suppression is deduplication, not denial.

**Illegal, legal-but-unaffordable and legal-and-affordable are three distinct
states.** Collapsing the middle one hides exactly the cases Module 20 exists to
catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.control.budget_types import (
    BudgetActionDescriptor,
    BudgetSpendClass,
    CacheDisposition,
    CallKind,
    SpecialReservePurpose,
    SubCall,
)
from cover_kbc.control.planner_types import (
    ActionExecutionStatus,
    ActionFamily,
    PlannerActionCandidate,
    PlannerError,
)

#: Bumped when the catalogue's projection or identity rules change.
CATALOG_VERSION = "layer6-catalog-v1"

_F = ActionFamily
_P = SpecialReservePurpose


class ActionOwner(str, Enum):
    """Which module's contract establishes an action's legality.

    Ordered by specificity. When two owners can express the same semantic work,
    the more specific one wins - a typed specialist verification carries a
    contract the generic core verify does not, so collapsing them onto the
    generic form would lose the constraint.
    """

    M7_CORE = "M7"
    M11_PARAMETRIC = "M11"
    M12_NUMERIC = "M12"
    M13_LARGE_SET = "M13"
    M14_NULL_TEMPORAL = "M14"
    M15_SMALL_SET = "M15"
    M17_VERIFIER = "M17"
    M18_STRUCTURAL = "M18"

    @property
    def specificity(self) -> int:
        return _OWNER_SPECIFICITY[self]


#: Core Module 7 is the least specific owner: it remains authoritative for
#: generic operations no upgraded module supersedes, and yields where one does.
_OWNER_SPECIFICITY = {
    ActionOwner.M7_CORE: 0,
    ActionOwner.M11_PARAMETRIC: 1,
    ActionOwner.M12_NUMERIC: 2,
    ActionOwner.M13_LARGE_SET: 2,
    ActionOwner.M14_NULL_TEMPORAL: 2,
    ActionOwner.M15_SMALL_SET: 2,
    ActionOwner.M17_VERIFIER: 3,
    ActionOwner.M18_STRUCTURAL: 3,
}


class ExclusionReason(str, Enum):
    """Why a projected action did not reach the planner.

    Every exclusion is recorded. An action that vanished without a reason is
    indistinguishable from one nobody thought of.
    """

    #: The owner declared it ineligible, with its own reason attached.
    OWNER_INELIGIBLE = "OWNER_INELIGIBLE"
    #: The owner's execution record shows this one-shot action already ran.
    ALREADY_EXECUTED = "ALREADY_EXECUTED"
    #: A more specific owner expresses the same semantic action.
    SAME_SEMANTIC_ACTION = "SAME_SEMANTIC_ACTION"


@dataclass(frozen=True)
class CatalogExclusion:
    """One action that was projected and then set aside, and why."""

    action_id: str
    owner: ActionOwner
    reason: ExclusionReason
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "owner": self.owner.value,
                "reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True)
class ControlActionCandidate:
    """One action an owning module declares legal in the current state.

    Carries resource identity and provenance, and **no value**: no utility, no
    expected gain, no factual status. Module 21 supplies value; nothing here
    anticipates it.
    """

    subject: str
    relation: str
    row_index: int
    #: ``owner:family:target:facet`` - stable, semantic, never random.
    action_id: str
    owner: ActionOwner
    family: ActionFamily
    budget_descriptor: BudgetActionDescriptor
    target: str = ""
    facet_id: str = ""
    model_role: str = "enumerator"
    repeatable: bool = False
    #: What the owner pointed at, structurally. Audit provenance only.
    legal_provenance: str = ""
    #: The owner's own evidence that prerequisites hold.
    eligibility_evidence: str = ""

    def __post_init__(self) -> None:
        if not self.legal_provenance:
            raise PlannerError(
                f"action {self.action_id!r} carries no owner provenance; Layer 6 "
                "declares no legality of its own"
            )
        if self.budget_descriptor.query_key != (
            self.subject, self.relation, self.row_index
        ):
            raise PlannerError(
                f"action {self.action_id!r} carries a Module 20 descriptor for "
                f"another query"
            )

    @property
    def semantic_key(self) -> tuple[str, str, str, str]:
        """What makes two projections *the same action*.

        Owner is deliberately excluded: that is the whole point of the
        precedence rule - the same semantic work reached through two owners
        must collide here so the more specific one can win.
        """
        return (self.family.value, self.target, self.facet_id, self.model_role)

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.family.value, self.action_id, self.target, self.facet_id)

    def to_planner_action(self) -> PlannerActionCandidate:
        return PlannerActionCandidate(
            action_id=self.action_id, source_module=self.owner.value,
            family=self.family, budget_descriptor=self.budget_descriptor,
            target=self.target, facet_id=self.facet_id,
            model_role=self.model_role,
            legal_provenance=self.legal_provenance,
            status=ActionExecutionStatus.ELIGIBLE, repeatable=self.repeatable,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "owner": self.owner.value,
            "family": self.family.value, "target": self.target,
            "facet_id": self.facet_id, "model_role": self.model_role,
            "repeatable": self.repeatable,
            "legal_provenance": self.legal_provenance,
            "eligibility_evidence": self.eligibility_evidence,
            "budget_descriptor": self.budget_descriptor.to_json(),
        }


# --------------------------------------------------------------------------
# Cost plans. Resource identity, supplied to Module 20's audited taxonomy.
# --------------------------------------------------------------------------


def _generation(tokens: int, label: str) -> tuple[SubCall, ...]:
    return (SubCall(kind=CallKind.GENERATE,
                    cache=CacheDisposition.NOT_CACHEABLE,
                    max_generated_tokens=tokens, label=label),)


def _descriptor(
    candidate_subject: str, relation: str, row_index: int, *, action_id: str,
    owner: ActionOwner, action_kind: str, spend_class: BudgetSpendClass,
    sub_calls: tuple[SubCall, ...], purpose: SpecialReservePurpose | None = None,
    model_role: str = "enumerator", bounded: bool = True,
) -> BudgetActionDescriptor:
    return BudgetActionDescriptor(
        subject=candidate_subject, relation=relation, row_index=row_index,
        action_id=action_id, source_module=owner.value, action_kind=action_kind,
        spend_class=spend_class, special_purpose=purpose, model_role=model_role,
        sub_calls=sub_calls, cost_is_bounded=bounded,
    )


#: Declared decode bounds for projected actions. Structural, not calibrated:
#: they describe how much a call may generate, not how much it is worth.
DEFAULT_GENERATION_TOKENS = 256


# --------------------------------------------------------------------------
# Owner adapters
# --------------------------------------------------------------------------


def m17_actions(
    targets: Iterable[Any], *, subject: str, relation: str, row_index: int,
    readings: int = 1, control_calls_needed: int = 0, controls_total: int = 0,
) -> tuple[list[ControlActionCandidate], list[CatalogExclusion]]:
    """Module 17's eligibility catalogue, adapted.

    Only ``eligible`` targets become actions: a hard-contract violation cannot
    be rescued by a verifier and a target with no printable value cannot be
    shown to one, and Module 17 already says so. Nothing here schedules
    anything - which target is worth a call stays Module 20/21's question.

    The cost plan is cache-aware through Module 20's own helper, so a warm
    control cache produces a cheaper *plan* for the **same semantic action**.
    """
    from cover_kbc.control.budget_accounting import specialist_verification_plan

    actions: list[ControlActionCandidate] = []
    excluded: list[CatalogExclusion] = []
    for target in targets:
        target_id = target.target_id
        action_id = f"M17:SPECIALIST_VERIFY:{target_id}"
        if not target.eligible:
            excluded.append(CatalogExclusion(
                action_id, ActionOwner.M17_VERIFIER,
                ExclusionReason.OWNER_INELIGIBLE,
                getattr(target.ineligible_reason, "value", "") or "owner ineligible",
            ))
            continue
        actions.append(ControlActionCandidate(
            subject=subject, relation=relation, row_index=row_index,
            action_id=action_id, owner=ActionOwner.M17_VERIFIER,
            family=_F.SPECIALIST_VERIFY, target=target_id,
            model_role="verifier",
            budget_descriptor=_descriptor(
                subject, relation, row_index, action_id=action_id,
                owner=ActionOwner.M17_VERIFIER, action_kind="SPECIALIST_VERIFY",
                spend_class=BudgetSpendClass.VERIFICATION, model_role="verifier",
                sub_calls=specialist_verification_plan(
                    readings=readings,
                    control_calls_needed=control_calls_needed,
                    controls_total=controls_total,
                ),
            ),
            legal_provenance=f"M17 verifiable_targets: {target.kind.value}",
            eligibility_evidence="Module 17 declares this target eligible",
        ))
    return actions, excluded


#: Module 18's four mechanisms, mapped onto canonical planner families.
_M18_FAMILY = {
    "REVERSE": _F.REVERSE_CHECK,
    "KEY_CONDITION": _F.COUNTERFACTUAL_VERIFY,
    "COUNTERFACTUAL": _F.COUNTERFACTUAL_VERIFY,
    "CANDIDATE_FREE_RECALL": _F.CANDIDATE_FREE_RECALL,
}

#: Which protected reserve each mechanism draws on, per Table 6.
_M18_PURPOSE = {
    "REVERSE": _P.REVERSE,
    "CANDIDATE_FREE_RECALL": _P.CANDIDATE_FREE,
}

#: The contract-declared counterfactual classes that *are* the parent/subsidiary
#: check, and therefore the consumers of the PARENT_SUBSIDIARY reserve.
#:
#: Matched on the class the relation contract declares, never on prompt text.
#: ``historical_listing`` is deliberately absent: it is the *temporal* near-miss
#: class, and tagging it PARENT_SUBSIDIARY would let a freshness question spend
#: the budget protected for distinguishing a company from its parent.
_PARENT_SUBSIDIARY_CLASSES = frozenset({"parent_listing", "subsidiary_listing"})


def _M18_COUNTERFACTUAL_PURPOSE(
    kind: str, counterfactual_class: str, declared: frozenset
) -> SpecialReservePurpose | None:
    """The reserve one Module 18 check draws on.

    A counterfactual earns PARENT_SUBSIDIARY only when its own contract-declared
    class *is* the parent/subsidiary distinction. A generic company-itself or
    key-condition check does not, and a temporal near-miss does not become a
    freshness action merely because the relation is time-sensitive - Module 15's
    cross-family branch owns freshness (see ``_facet_purpose``).
    """
    if (kind == "COUNTERFACTUAL"
            and counterfactual_class in _PARENT_SUBSIDIARY_CLASSES
            and _P.PARENT_SUBSIDIARY in declared):
        return _P.PARENT_SUBSIDIARY
    return _M18_PURPOSE.get(kind)


def m18_actions(
    checks: Iterable[Any], *, subject: str, relation: str, row_index: int,
    executed: Sequence[tuple[str, str, str, str]] = (),
) -> tuple[list[ControlActionCandidate], list[CatalogExclusion]]:
    """Module 18's eligible-check catalogue, adapted.

    An unsupported reverse relation and an undeclared counterfactual class are
    already ineligible upstream, so they never arrive. A completed
    non-repeatable check is dropped here against the owner's execution record.

    Candidate-free recall keeps its blindness: it is projected as its own
    family, and nothing attaches a candidate to it.
    """
    from cover_kbc.control.relation_budget import relation_policy

    declared = set(relation_policy(relation).special_reserve_purposes)
    actions: list[ControlActionCandidate] = []
    excluded: list[CatalogExclusion] = []
    for check in checks:
        kind = check.check_kind.value
        target_id = getattr(check.target, "target_id", "")
        action_id = f"M18:{kind}:{target_id}"
        family = _M18_FAMILY[kind]
        if not check.eligible:
            excluded.append(CatalogExclusion(
                action_id, ActionOwner.M18_STRUCTURAL,
                ExclusionReason.OWNER_INELIGIBLE,
                getattr(check.ineligible_reason, "value", "") or "owner ineligible",
            ))
            continue
        candidate_identity = (family.value, action_id, target_id, "")
        if candidate_identity in set(executed):
            excluded.append(CatalogExclusion(
                action_id, ActionOwner.M18_STRUCTURAL,
                ExclusionReason.ALREADY_EXECUTED,
                "this non-repeatable structural check already ran",
            ))
            continue
        purpose = _M18_COUNTERFACTUAL_PURPOSE(
            kind, getattr(check, "counterfactual_class", ""), frozenset(declared)
        )
        actions.append(ControlActionCandidate(
            subject=subject, relation=relation, row_index=row_index,
            action_id=action_id, owner=ActionOwner.M18_STRUCTURAL, family=family,
            target=target_id,
            budget_descriptor=_descriptor(
                subject, relation, row_index, action_id=action_id,
                owner=ActionOwner.M18_STRUCTURAL, action_kind=kind,
                spend_class=(
                    BudgetSpendClass.DISCOVERY
                    if family is _F.CANDIDATE_FREE_RECALL
                    else BudgetSpendClass.VERIFICATION
                ),
                purpose=purpose if purpose in declared else None,
                sub_calls=_generation(DEFAULT_GENERATION_TOKENS, kind.lower()),
            ),
            legal_provenance=(
                f"M18 eligible_checks: {kind}"
                + (f" requested by {check.requested_by.source_module}"
                   if getattr(check, "requested_by", None) else "")
            ),
            eligibility_evidence="Module 18 declares this check eligible",
        ))
    return actions, excluded


def specialist_actions(
    relation: str, result: Any, *, subject: str, row_index: int,
) -> tuple[list[ControlActionCandidate], list[CatalogExclusion]]:
    """Facet/probe actions from the owning specialist's live registry.

    Three owner rules are respected and none is re-derived here:

    * a facet the registry **disables** is not an action - it was deliberately
      not part of the plan, and Audit 0029 already froze that a deliberate
      omission is not a gap;
    * a facet the specialist **already ran** is not an action, read from its own
      execution record, not from an artefact name;
    * a facet whose owner has recorded **exhaustion evidence** is not reopened.

    ``UNEXPLORED`` is deliberately *not* the test. Audit 0029 §14 froze that a
    facet with no observation is not thereby executable now - its prerequisite
    may be unmet - so legality comes from the registry and the execution record,
    never from Module 19's coverage state.
    """
    from cover_kbc.coverage_gap.facet_coverage import (
        FACET_OWNER, build_facet_map, declared_facets, facet_executions,
    )

    owner = {
        "M12": ActionOwner.M12_NUMERIC, "M13": ActionOwner.M13_LARGE_SET,
        "M14": ActionOwner.M14_NULL_TEMPORAL, "M15": ActionOwner.M15_SMALL_SET,
    }[FACET_OWNER[relation]]

    executions = facet_executions(relation, result)
    records = {r.facet_id: r for r in build_facet_map(relation, executions)}

    actions: list[ControlActionCandidate] = []
    excluded: list[CatalogExclusion] = []
    for facet in declared_facets(relation):
        action_id = f"{owner.value}:SPECIALIST_PROBE:{facet.facet_id}"
        if not facet.applicable:
            excluded.append(CatalogExclusion(
                action_id, owner, ExclusionReason.OWNER_INELIGIBLE,
                facet.exclusion_reason,
            ))
            continue
        if facet.family == "cross_family":
            legal, reason, detail = _cross_family_legality(result)
            if not legal:
                excluded.append(CatalogExclusion(action_id, owner, reason, detail))
                continue
        record = records[facet.facet_id]
        if record.executed_operations:
            excluded.append(CatalogExclusion(
                action_id, owner, ExclusionReason.ALREADY_EXECUTED,
                f"the owner recorded {record.executed_operations} operation(s) "
                "for this one-shot facet",
            ))
            continue
        if record.exhaustion_evidence:
            excluded.append(CatalogExclusion(
                action_id, owner, ExclusionReason.OWNER_INELIGIBLE,
                "the owner recorded exhaustion evidence for this facet",
            ))
            continue
        purpose = _facet_purpose(relation, facet)
        actions.append(ControlActionCandidate(
            subject=subject, relation=relation, row_index=row_index,
            action_id=action_id, owner=owner, family=_F.SPECIALIST_PROBE,
            facet_id=facet.facet_id,
            budget_descriptor=_descriptor(
                subject, relation, row_index, action_id=action_id, owner=owner,
                action_kind="SPECIALIST_PROBE",
                spend_class=BudgetSpendClass.DISCOVERY, purpose=purpose,
                sub_calls=_generation(DEFAULT_GENERATION_TOKENS, facet.facet_id),
            ),
            legal_provenance=(
                f"{owner.value} registry declares facet {facet.facet_id!r} "
                "enabled and it has not run"
            ),
            eligibility_evidence=(
                _cross_family_legality(result)[2]
                if facet.family == "cross_family"
                else "registry enabled, no recorded operation"
            ),
        ))
    return actions, excluded


def _cross_family_legality(result: Any) -> tuple[bool, ExclusionReason, str]:
    """Audit 0022 §17A, read from Module 15 rather than re-derived.

    §17A keeps **two** questions apart, and both must be answered yes:

    * ``plan.cross_family_eligible`` - *static* architecture eligibility. May
      this build use the cross-family mechanism at all? Configuration
      ``enabled`` plus a genuinely distinct second model family.
    * ``result.cross_family_trigger`` - the *local, per-query* trigger. Does
      **this** stock query's listing state actually call for the temporal
      rescue? Only ``UNRESOLVED_LISTING_GATE``, ``TEMPORAL_STATUS_UNCLEAR`` and
      ``TEMPORAL_STATUS_CONFLICT`` fire, which is exactly what the owner's own
      ``fires`` property says.

    Static eligibility alone is **not** permission: an eligible build whose
    query is ``LOCALLY_CLEAR`` has no freshness action, and ``NOT_EVALUATED``
    proves nothing about local need. Nothing here re-reads the gate, the
    candidates or Module 9's temporal grade to reconstruct the condition.

    The branch is also one-shot. Module 15 runs it as soon as the trigger fires
    and a runtime exists, so in a normal post-Module-15 state a fired trigger is
    *already executed* - and the honest catalogue then contains no cross-family
    action at all. A complete taxonomy does not require every family to be live
    in every post-execution state.
    """
    plan = getattr(result, "plan", None)
    if plan is None:
        return (
            False, ExclusionReason.OWNER_INELIGIBLE,
            "no Module 15 result, so no owner verdict on the §17A branch exists",
        )
    if not getattr(plan, "cross_family_eligible", False):
        return (
            False, ExclusionReason.OWNER_INELIGIBLE,
            getattr(plan, "cross_family_rationale", "")
            or "Module 15 declares the §17A branch statically ineligible",
        )
    trigger = getattr(result, "cross_family_trigger", None)
    if trigger is None or not getattr(trigger, "fires", False):
        name = getattr(trigger, "value", "NOT_EVALUATED")
        return (
            False, ExclusionReason.OWNER_INELIGIBLE,
            f"Module 15 recorded local trigger {name}; static eligibility alone "
            "does not make this query's listing state uncertain",
        )
    if getattr(result, "cross_family_executed", False):
        return (
            False, ExclusionReason.ALREADY_EXECUTED,
            "the one-shot cross-family recall already ran on this query",
        )
    return (
        True, ExclusionReason.OWNER_INELIGIBLE,
        f"Module 15 recorded local trigger {trigger.value} and the one-shot "
        "cross-family recall has not run",
    )


def _facet_purpose(relation: str, facet: Any) -> SpecialReservePurpose | None:
    """Which protected reserve a facet's probe draws on, if any.

    Read from Table 6's declared purposes for the relation, matched on the
    owner's own facet family - never on prompt text.

    **Cross-family recall draws on FRESHNESS.** Audit 0022 §17A makes the M15
    cross-family branch the stock *temporal* rescue: it fires on an unresolved
    listing gate or an unclear temporal status, and it asks a second model
    family for independent, fresher recall. It is acquisition evidence.

    That is a different operation from Module 18's parent/subsidiary
    counterfactual, which is targeted structural verification distinguishing the
    company itself from its parent or subsidiary. The two must not share a
    reserve tag merely because both concern stock - see ``_M18_COUNTERFACTUAL_PURPOSE``.
    """
    from cover_kbc.control.relation_budget import relation_policy

    declared = relation_policy(relation).special_reserve_purposes
    if facet.missingness and _P.MISSINGNESS in declared:
        return _P.MISSINGNESS
    if facet.family == "cross_family" and _P.FRESHNESS in declared:
        return _P.FRESHNESS
    return None


def m11_actions(
    relation: str, retrieval: Any, *, subject: str, row_index: int,
) -> tuple[list[ControlActionCandidate], list[CatalogExclusion]]:
    """Module 11's parametric probe families, adapted.

    Each family is one-shot per query: a probe that already ran is excluded
    against Module 11's own record, however large a gap Module 19 still shows.
    """
    from cover_kbc.query_intelligence.parametric_retrieval import OPERATION_SPECS

    executed = {
        record.operation_id.split("#")[0]
        for record in getattr(retrieval, "records", ())
    } if retrieval is not None else set()

    actions: list[ControlActionCandidate] = []
    excluded: list[CatalogExclusion] = []
    for kind, spec in OPERATION_SPECS.items():
        name = kind.value.lower()
        action_id = f"M11:PSEUDO_MEMORY_PROBE:{name}"
        if name in executed:
            excluded.append(CatalogExclusion(
                action_id, ActionOwner.M11_PARAMETRIC,
                ExclusionReason.ALREADY_EXECUTED,
                "Module 11 recorded this one-shot probe",
            ))
            continue
        actions.append(ControlActionCandidate(
            subject=subject, relation=relation, row_index=row_index,
            action_id=action_id, owner=ActionOwner.M11_PARAMETRIC,
            family=_F.PSEUDO_MEMORY_PROBE, facet_id=name,
            budget_descriptor=_descriptor(
                subject, relation, row_index, action_id=action_id,
                owner=ActionOwner.M11_PARAMETRIC,
                action_kind="PSEUDO_MEMORY_PROBE",
                spend_class=BudgetSpendClass.DISCOVERY,
                sub_calls=_generation(DEFAULT_GENERATION_TOKENS, name),
            ),
            legal_provenance=(
                f"M11 declares probe family {spec.independence_group.value} and "
                "it has not run"
            ),
            eligibility_evidence="declared probe family, no recorded operation",
        ))
    return actions, excluded


#: Core Module 7's action types, onto canonical families.
_CORE_FAMILY = {
    "RUN_VIEW": _F.SPECIALIST_PROBE,
    "RUN_FACET": _F.SPECIALIST_PROBE,
    "VERIFY": _F.BLIND_VERIFY,
    "ADVERSARIAL_VERIFY": _F.COUNTERFACTUAL_VERIFY,
    "REVERSE_CHECK": _F.REVERSE_CHECK,
    "CROSS_MODEL_CHECK": _F.CROSS_MODEL_CHECK,
    "RESAMPLE": _F.RESAMPLE,
}


def m7_actions(
    actions: Iterable[Any], *, subject: str, relation: str, row_index: int,
) -> tuple[list[ControlActionCandidate], list[CatalogExclusion]]:
    """Core Module 7's own legal actions, adapted.

    Module 7's legality engine is reused rather than reimplemented: it already
    refuses a view that ran, a candidate already carrying the reverse
    mechanism, and a resample before structural mechanisms are exhausted.
    ``STOP`` is dropped, because §17 makes it the planner's fallback rather
    than a ranked candidate.

    A resample is the one family where repetition may be legal, so it is the
    only one marked repeatable - and it keeps its structural independence
    group, since another sample of one mechanism is not a new mechanism.
    """
    out: list[ControlActionCandidate] = []
    excluded: list[CatalogExclusion] = []
    for action in actions:
        kind = getattr(action.action_type, "value", str(action.action_type))
        if kind == "STOP":
            excluded.append(CatalogExclusion(
                "M7:STOP", ActionOwner.M7_CORE, ExclusionReason.OWNER_INELIGIBLE,
                "STOP is Module 21's fallback, never a ranked candidate",
            ))
            continue
        family = _CORE_FAMILY.get(kind)
        if family is None:
            raise PlannerError(f"unknown core action type {kind!r}")
        target = action.candidate_key
        facet = action.facet_id or action.view_id
        action_id = f"M7:{kind}:{facet}:{target}".rstrip(":")
        role = getattr(action.model_role, "value", str(action.model_role))
        verification = family in (
            _F.BLIND_VERIFY, _F.COUNTERFACTUAL_VERIFY, _F.CROSS_MODEL_CHECK)
        out.append(ControlActionCandidate(
            subject=subject, relation=relation, row_index=row_index,
            action_id=action_id, owner=ActionOwner.M7_CORE, family=family,
            target=target, facet_id=facet, model_role=role,
            repeatable=family is _F.RESAMPLE,
            budget_descriptor=_descriptor(
                subject, relation, row_index, action_id=action_id,
                owner=ActionOwner.M7_CORE, action_kind=kind,
                spend_class=(
                    BudgetSpendClass.VERIFICATION if verification
                    else BudgetSpendClass.DISCOVERY
                ),
                model_role=role,
                sub_calls=(
                    (SubCall(kind=CallKind.SCORE_LABELS,
                             cache=CacheDisposition.NOT_CACHEABLE,
                             label=kind.lower()),)
                    if family is _F.BLIND_VERIFY
                    else _generation(DEFAULT_GENERATION_TOKENS, kind.lower())
                ),
            ),
            legal_provenance=f"M7 legal_actions: {kind} ({action.reason})",
            eligibility_evidence="Module 7's legality engine offered this action",
        ))
    return out, excluded


# --------------------------------------------------------------------------
# Canonicalisation
# --------------------------------------------------------------------------


def build_action_catalog(
    projections: Sequence[tuple[list[ControlActionCandidate], list[CatalogExclusion]]],
) -> tuple[tuple[ControlActionCandidate, ...], tuple[CatalogExclusion, ...]]:
    """Merge owner projections into one deterministic catalogue.

    Two actions with the same semantic key are the same work reached through
    two owners: the more specific owner wins and the generic one is recorded as
    ``SAME_SEMANTIC_ACTION``. Two identical projections collapse to one.

    A genuine conflict - one canonical action id claimed by different owners,
    targets, roles, repeatability or cost - **raises**, because silently
    picking the first would make the catalogue depend on adapter order.
    """
    kept: dict[tuple[str, str, str, str], ControlActionCandidate] = {}
    by_id: dict[str, ControlActionCandidate] = {}
    exclusions: list[CatalogExclusion] = []
    for actions, excluded in projections:
        exclusions.extend(excluded)
        for action in actions:
            existing = by_id.get(action.action_id)
            if existing is not None and existing != action:
                raise PlannerError(
                    f"two projections claim action id {action.action_id!r} with "
                    "conflicting owner, target, role, repeatability or cost"
                )
            by_id[action.action_id] = action

            rival = kept.get(action.semantic_key)
            if rival is None:
                kept[action.semantic_key] = action
                continue
            if rival.action_id == action.action_id:
                continue
            loser, winner = (
                (rival, action)
                if action.owner.specificity > rival.owner.specificity
                else (action, rival)
            )
            if action.owner.specificity == rival.owner.specificity:
                # Same specificity and same semantics: order must not decide.
                loser, winner = sorted(
                    (rival, action), key=lambda a: a.action_id)[::-1]
            kept[action.semantic_key] = winner
            exclusions.append(CatalogExclusion(
                loser.action_id, loser.owner, ExclusionReason.SAME_SEMANTIC_ACTION,
                f"{winner.owner.value} owns the same semantic action "
                f"({winner.action_id}); the more specific owner wins",
            ))

    catalogue = tuple(sorted(kept.values(), key=lambda a: a.identity))
    return catalogue, tuple(exclusions)


def owner_action_families() -> Mapping[ActionFamily, tuple[ActionOwner, ...]]:
    """Which owners can produce each canonical family. Coverage audit input."""
    return {
        _F.SPECIALIST_PROBE: (
            ActionOwner.M12_NUMERIC, ActionOwner.M13_LARGE_SET,
            ActionOwner.M14_NULL_TEMPORAL, ActionOwner.M15_SMALL_SET,
            ActionOwner.M7_CORE,
        ),
        _F.PSEUDO_MEMORY_PROBE: (ActionOwner.M11_PARAMETRIC,),
        _F.CANDIDATE_FREE_RECALL: (ActionOwner.M18_STRUCTURAL,),
        _F.BLIND_VERIFY: (ActionOwner.M7_CORE,),
        _F.SPECIALIST_VERIFY: (ActionOwner.M17_VERIFIER,),
        _F.COUNTERFACTUAL_VERIFY: (
            ActionOwner.M18_STRUCTURAL, ActionOwner.M7_CORE),
        _F.REVERSE_CHECK: (ActionOwner.M18_STRUCTURAL, ActionOwner.M7_CORE),
        _F.CROSS_MODEL_CHECK: (ActionOwner.M7_CORE,),
        _F.RESAMPLE: (ActionOwner.M7_CORE,),
    }


__all__ = [
    "CATALOG_VERSION",
    "DEFAULT_GENERATION_TOKENS",
    "ActionOwner",
    "CatalogExclusion",
    "ControlActionCandidate",
    "ExclusionReason",
    "build_action_catalog",
    "m7_actions",
    "m11_actions",
    "m17_actions",
    "m18_actions",
    "owner_action_families",
    "specialist_actions",
]
