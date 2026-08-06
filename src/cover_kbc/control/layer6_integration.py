"""Layer-6 integration: catalogue -> Module 20 affordability -> Module 21 value.

**Integration, not a new module.** Nothing here decides legality, prices
resources or values actions; it connects three existing authorities in the one
order that keeps them honest:

    owner modules      say what is *legal*
    Module 20          says what is *affordable*
    Module 21          says what is *worth doing*

The order matters. An action that is not legal never reaches Module 20; one
Module 20 denies never reaches Module 21. A large expected utility cannot buy
permission, and an unused reserve cannot create value.

Three states are kept explicitly distinct, because collapsing the middle one
hides exactly what Module 20 exists to catch: **illegal**, **legal but
unaffordable**, **legal and affordable**.

Nothing is executed. Module 7 remains the production controller and Module 8
the finaliser; Module 21's decision here is a shadow diagnostic that no
production path reads.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Sequence

from cover_kbc.control.action_catalog import (
    CATALOG_VERSION,
    CatalogExclusion,
    ControlActionCandidate,
    build_action_catalog,
    m7_actions,
    m11_actions,
    m17_actions,
    m18_actions,
    specialist_actions,
)
from cover_kbc.control.budget_types import BudgetSchedulerError
from cover_kbc.control.planner_types import (
    DeniedAction,
    MicroPlannerDecision,
    PlannerError,
    PlannerStateSnapshot,
)

#: Bumped when the integration's shape or ordering changes.
LAYER6_VERSION = "layer6-v1"

NO_EXECUTION_MARKER = (
    "Layer 6 planned only. No action was executed, no neural call was made, no "
    "budget was reserved, and Module 7 remains the production controller."
)


@dataclass(frozen=True)
class Layer6ControlState:
    """One shadow control state for one query. Integration observability."""

    layer6_version: str
    catalog_version: str
    subject: str
    relation: str
    row_index: int
    program_type: str
    catalog: tuple[ControlActionCandidate, ...] = ()
    exclusions: tuple[CatalogExclusion, ...] = ()
    affordable_actions: tuple[str, ...] = ()
    denied_actions: tuple[DeniedAction, ...] = ()
    decision: MicroPlannerDecision | None = None
    #: Module 7's production control state, carried for later comparison only.
    production_control: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def legal_actions(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.catalog)

    def to_json(self) -> dict[str, Any]:
        return {
            "layer6_version": self.layer6_version,
            "catalog_version": self.catalog_version,
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "program_type": self.program_type,
            "catalog": [action.to_json() for action in self.catalog],
            "exclusions": [x.to_json() for x in self.exclusions],
            "legal_actions": list(self.legal_actions),
            "affordable_actions": list(self.affordable_actions),
            "denied_actions": [d.to_json() for d in self.denied_actions],
            "decision": self.decision.to_json() if self.decision else None,
            "production_control": dict(self.production_control),
            "no_execution": NO_EXECUTION_MARKER,
            "errors": list(self.errors),
        }


def collect_catalog(
    *, subject: str, relation: str, row_index: int,
    specialist_result: Any = None, specialist_declared: bool = False,
    retrieval: Any = None,
    verifiable_targets: Sequence[Any] = (), eligible_checks: Sequence[Any] = (),
    core_actions: Sequence[Any] = (),
    executed: Sequence[tuple[str, str, str, str]] = (),
    verifier_config: Any = None, m17_readings: int | None = None,
    m17_control_calls_needed: int = 0, m17_controls_total: int | None = None,
) -> tuple[tuple[ControlActionCandidate, ...], tuple[CatalogExclusion, ...]]:
    """Project every owner's declared legality into one catalogue. **Zero calls.**

    Each argument is a surface its owner already publishes. Omitting one simply
    means that owner contributed nothing to this state - it never means Layer 6
    should invent the actions itself.
    """
    projections = []
    if retrieval is not None:
        projections.append(m11_actions(
            relation, retrieval, subject=subject, row_index=row_index))
    if specialist_declared or specialist_result is not None:
        # ``None`` is a real state, not an absence of information: a specialist
        # that has not run yet has executed nothing, so every enabled facet its
        # registry declares is legal.
        projections.append(specialist_actions(
            relation, specialist_result, subject=subject, row_index=row_index))
    if verifiable_targets:
        projections.append(m17_actions(
            verifiable_targets, subject=subject, relation=relation,
            row_index=row_index, verifier_config=verifier_config,
            readings=m17_readings,
            control_calls_needed=m17_control_calls_needed,
            controls_total=m17_controls_total))
    if eligible_checks:
        projections.append(m18_actions(
            eligible_checks, subject=subject, relation=relation,
            row_index=row_index, executed=executed))
    if core_actions:
        projections.append(m7_actions(
            core_actions, subject=subject, relation=relation,
            row_index=row_index))
    return build_action_catalog(projections)


class Layer6Integrator:
    """Connects the owner catalogue, Module 20 and Module 21. Executes nothing."""

    def __init__(self, planner: Any) -> None:
        if planner is None:
            raise PlannerError(
                "Layer-6 integration needs Module 21; it ranks nothing itself"
            )
        self.planner = planner

    def integrate(
        self, state: PlannerStateSnapshot,
        catalog: Sequence[ControlActionCandidate],
        exclusions: Sequence[CatalogExclusion] = (),
        *, production_control: dict[str, Any] | None = None,
    ) -> Layer6ControlState:
        """Screen, price and rank. **Zero calls, nothing executed.**

        Module 20 is asked on a **copy** of its ledger, so an affordability
        probe never reaches the real one: Layer 6 answers "could this be
        reserved", not "reserve it".
        """
        for action in catalog:
            if (action.subject, action.relation, action.row_index) != state.query_key:
                raise PlannerError(
                    f"catalogue action {action.action_id!r} belongs to another "
                    "query"
                )

        affordable: list[ControlActionCandidate] = []
        denied: list[DeniedAction] = []
        if catalog:
            if state.budget_ledger is None:
                raise PlannerError(
                    "Layer-6 integration needs Module 20's ledger; affordability "
                    "is Module 20's answer and Layer 6 may not guess it"
                )
            for action in catalog:
                probe = copy.deepcopy(state.budget_ledger)
                try:
                    outcome = probe.reserve(action.budget_descriptor)
                except BudgetSchedulerError as error:
                    raise PlannerError(
                        f"Module 20 refused to price {action.action_id!r}: {error}"
                    ) from error
                if hasattr(outcome, "reason"):
                    denied.append(DeniedAction(
                        action.action_id, outcome.reason.value, outcome.detail))
                else:
                    affordable.append(action)

        # Module 21 is handed the **legal** set, which is what Appendix C
        # specifies as its input, and applies its own Module 20 screen to
        # decide what it will *rank*. Handing it only the affordable subset
        # would collapse two different situations into one: with everything
        # denied it would see an empty list and report NO_LEGAL_ACTION, when
        # the truth is NO_AFFORDABLE_ACTION. Both layers ask Module 20 rather
        # than deciding for themselves, so the two screens agree by
        # construction - this is a diagnostic split, not a second policy.
        decision = self.planner.plan(
            state, [action.to_planner_action() for action in catalog])

        return Layer6ControlState(
            layer6_version=LAYER6_VERSION, catalog_version=CATALOG_VERSION,
            subject=state.subject, relation=state.relation,
            row_index=state.row_index, program_type=state.program_type,
            catalog=tuple(catalog), exclusions=tuple(exclusions),
            affordable_actions=tuple(
                sorted(action.action_id for action in affordable)),
            denied_actions=tuple(denied), decision=decision,
            production_control=dict(production_control or {}),
        )


__all__ = [
    "LAYER6_VERSION",
    "NO_EXECUTION_MARKER",
    "Layer6ControlState",
    "Layer6Integrator",
    "collect_catalog",
]
