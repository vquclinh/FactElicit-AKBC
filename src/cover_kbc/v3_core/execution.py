"""Executable V3 action seam for TRAIN collection.

This module turns the V3 relation/failure-state catalogue into real actions
against the existing owners:

* M18-style acquisition actions call the enumerator through
  :class:`~cover_kbc.elicitation.engine.ElicitationEngine` and write ordinary
  Module-3 support edges.
* M15 relation-specialist disambiguation is represented as typed semantic
  evidence on the existing graph.
* M17 verification modes call the verifier runtime with restricted labels and
  attach ordinary :class:`~cover_kbc.types.VerificationResult` records.

There is no gold input and no external factual source. All mutations go through
the existing :class:`~cover_kbc.evidence.graph.EvidenceGraph` APIs so Module 8
remains the only finalizer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.control.action_catalog import ActionOwner
from cover_kbc.control.budget_types import (
    BudgetActionDescriptor,
    BudgetSpendClass,
    CallKind,
    SpecialReservePurpose,
    SubCall,
)
from cover_kbc.control.planner_types import PlannerActionCandidate
from cover_kbc.elicitation.engine import ElicitationEngine, prompt_hash
from cover_kbc.elicitation.parsing import parse_entities
from cover_kbc.elicitation.views import ENTITY_FORMAT, NUMERIC_FORMAT, ViewSpec
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.models.base import (
    LMRuntime,
    LabelScoreRequest,
    LogitsUnavailable,
    entropy as label_entropy,
)
from cover_kbc.types import (
    DecodeProfile,
    ModelRole,
    OutputType,
    VerificationLabel,
    VerificationResult,
    ViewFamily,
)
from cover_kbc.v3_core.hypothesis import Hypothesis, QueryHypothesisGraph
from cover_kbc.v3_1.live_prompts import NO_INSTRUCTIONS, RelationInstructions
from cover_kbc.v3_core.prompt_families import PromptFamily
from cover_kbc.v3_core.relation_programs import (
    ActionHistory,
    DeathAttributeSlot,
    ListingDisambiguation,
    V3ActionFamily,
    award_promote_suppress_round,
)
from cover_kbc.verification import VERIFIER_SYSTEM_PROMPT
from cover_kbc.verification.v3_modes import (
    V3VerificationMode,
    V3VerificationRequest,
    render_v3_verification_prompt,
    select_contrast_pair,
    stock_rejection_first_request,
)

V3_ACTION_EFFECT_VERSION = "v3-action-effect-v1"


@dataclass(frozen=True)
class V3ActionCandidate:
    """One executable V3 action declared legal by relation profile + state."""

    subject: str
    relation: str
    row_index: int
    action_id: str
    owner: ActionOwner
    family: V3ActionFamily
    budget_descriptor: BudgetActionDescriptor
    prompt_family: PromptFamily
    view_id: str
    model_role: str
    target: str = ""
    target_id: str = ""
    comparison_id: str = ""
    comparison_text: str = ""
    facet_id: str = ""
    legal_provenance: str = ""
    repeatable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def source_module(self) -> str:
        return self.owner.value

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.family.value, self.action_id, self.target, self.facet_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "action_id": self.action_id,
            "owner": self.owner.value,
            "family": self.family.value,
            "prompt_family": self.prompt_family.value,
            "view_id": self.view_id,
            "model_role": self.model_role,
            "target": self.target,
            "target_id": self.target_id,
            "comparison_id": self.comparison_id,
            "comparison_text": self.comparison_text,
            "facet_id": self.facet_id,
            "legal_provenance": self.legal_provenance,
            "repeatable": self.repeatable,
            "metadata": dict(self.metadata),
            "budget_descriptor": self.budget_descriptor.to_json(),
        }

    def to_planner_action(self) -> PlannerActionCandidate:
        """Project a V3 owner action onto Module 21's generic action contract."""
        return PlannerActionCandidate(
            action_id=self.action_id,
            source_module=self.owner.value,
            family=self.family,
            budget_descriptor=self.budget_descriptor,
            target=self.target_id or self.target,
            facet_id=self.facet_id,
            model_role=self.model_role,
            legal_provenance=self.legal_provenance,
            repeatable=self.repeatable,
        )


@dataclass(frozen=True)
class V3ActionExecution:
    """Owner-specific reading returned by an executed V3 action."""

    action: V3ActionCandidate
    candidates_named: tuple[str, ...] = ()
    candidates_touched: tuple[str, ...] = ()
    structural_outcome: str = ""
    verifier_outcome: str = ""
    errors: tuple[str, ...] = ()
    novelty: Mapping[str, Any] = field(default_factory=dict)
    verification: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": V3_ACTION_EFFECT_VERSION,
            "action": self.action.to_json(),
            "candidates_named": list(self.candidates_named),
            "candidates_touched": list(self.candidates_touched),
            "structural_outcome": self.structural_outcome,
            "verifier_outcome": self.verifier_outcome,
            "errors": list(self.errors),
            "novelty": dict(self.novelty),
            "verification": dict(self.verification),
        }


def _stable_id(*parts: object, prefix: str = "v3a") -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _descriptor(
    *, action_id: str, owner: ActionOwner, family: V3ActionFamily,
    subject: str, relation: str, row_index: int, model_role: str,
    spend_class: BudgetSpendClass, max_generated_tokens: int = 0,
    purpose: SpecialReservePurpose | None = None,
) -> BudgetActionDescriptor:
    call_kind = (
        CallKind.SCORE_LABELS
        if model_role == ModelRole.VERIFIER.value else CallKind.GENERATE
    )
    return BudgetActionDescriptor(
        subject=subject,
        relation=relation,
        row_index=row_index,
        action_id=action_id,
        source_module=owner.value,
        action_kind=family.value,
        spend_class=spend_class,
        model_role=model_role,
        special_purpose=purpose,
        sub_calls=(SubCall(
            kind=call_kind,
            max_generated_tokens=max_generated_tokens,
            label=family.value,
        ),),
    )


def _make_action(
    hgraph: QueryHypothesisGraph, *, owner: ActionOwner, family: V3ActionFamily,
    prompt_family: PromptFamily, view_id: str, model_role: str,
    target: str = "", target_id: str = "", comparison_id: str = "",
    comparison_text: str = "", facet_id: str = "", repeatable: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> V3ActionCandidate:
    spend_class = (
        BudgetSpendClass.VERIFICATION
        if model_role == ModelRole.VERIFIER.value
        else BudgetSpendClass.DISCOVERY
    )
    purpose = None
    if family in (V3ActionFamily.CONTRAST_VERIFY, V3ActionFamily.SEMANTIC_VERIFY):
        purpose = SpecialReservePurpose.CONTRAST
    if family is V3ActionFamily.SET_EXPANSION:
        purpose = SpecialReservePurpose.MISSINGNESS
    if family is V3ActionFamily.LISTING_ELIMINATION:
        purpose = SpecialReservePurpose.PARENT_SUBSIDIARY
    max_tokens = 0 if spend_class is BudgetSpendClass.VERIFICATION else (
        512 if family is V3ActionFamily.SET_EXPANSION else 128
    )
    action_id = _stable_id(
        hgraph.subject, hgraph.relation, hgraph.row_index, family.value,
        view_id, target_id, comparison_id, facet_id, prefix="v3act",
    )
    return V3ActionCandidate(
        subject=hgraph.subject,
        relation=hgraph.relation,
        row_index=hgraph.row_index,
        action_id=action_id,
        owner=owner,
        family=family,
        budget_descriptor=_descriptor(
            action_id=action_id,
            owner=owner,
            family=family,
            subject=hgraph.subject,
            relation=hgraph.relation,
            row_index=hgraph.row_index,
            model_role=model_role,
            spend_class=spend_class,
            max_generated_tokens=max_tokens,
            purpose=purpose,
        ),
        prompt_family=prompt_family,
        view_id=view_id,
        model_role=model_role,
        target=target,
        target_id=target_id,
        comparison_id=comparison_id,
        comparison_text=comparison_text,
        facet_id=facet_id or view_id,
        legal_provenance=(
            f"M1 RelationProfile + failure_state="
            f"{hgraph.failure_state.value if hgraph.failure_state else ''}"
        ),
        repeatable=repeatable,
        metadata=dict(metadata or {}),
    )


def _ordered_hypotheses(hgraph: QueryHypothesisGraph) -> tuple[Hypothesis, ...]:
    return tuple(sorted(
        (h for h in hgraph.hypotheses if h.status.value != "DROPPED"),
        key=lambda h: (
            -h.independent_support_count,
            -h.raw_support_count,
            h.hypothesis_id,
        ),
    ))


def _primary_hypothesis(hgraph: QueryHypothesisGraph) -> Hypothesis | None:
    ordered = _ordered_hypotheses(hgraph)
    return ordered[0] if ordered else None


def _contrast_hypotheses(
    hgraph: QueryHypothesisGraph,
) -> tuple[Hypothesis, Hypothesis] | None:
    by_id = {h.hypothesis_id: h for h in hgraph.hypotheses}
    pair = select_contrast_pair([h.to_json() for h in hgraph.hypotheses])
    if pair is None:
        return None
    left, right = by_id.get(pair[0]), by_id.get(pair[1])
    if left is None or right is None:
        return None
    return left, right


def build_v3_action_catalog(
    hgraph: QueryHypothesisGraph,
    graph: EvidenceGraph,
    contract: RelationContract,
    *,
    history: ActionHistory | None = None,
) -> tuple[V3ActionCandidate, ...]:
    """Build executable V3 actions from the current hypothesis graph.

    The graph's own ``legal_action_families`` has already applied the relation
    profile and failure-state region. This function only supplies owner,
    target, prompt family and budget identity.
    """
    if hgraph.failure_state is None:
        return ()
    legal = [
        family for family in hgraph.legal_action_families
        if family is not V3ActionFamily.STOP
        and (history is None or history.admissible(hgraph.failure_state, family))
    ]
    if not legal:
        return ()

    primary = _primary_hypothesis(hgraph)
    actions: list[V3ActionCandidate] = []
    for family in legal:
        if family in (
            V3ActionFamily.MULTI_VIEW_RECALL,
            V3ActionFamily.INDEPENDENT_RECALL,
            V3ActionFamily.DEFINITION_RECALL,
            V3ActionFamily.ALTERNATIVE_RECALL,
            V3ActionFamily.SET_EXPANSION,
            V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
        ):
            spec = _recall_spec(family, contract, graph)
            if spec is None:
                continue
            owner, prompt_family, view_id, facet_id, repeatable, metadata = spec
            actions.append(_make_action(
                hgraph,
                owner=owner,
                family=family,
                prompt_family=prompt_family,
                view_id=view_id,
                model_role=ModelRole.ENUMERATOR.value,
                facet_id=facet_id,
                repeatable=repeatable,
                metadata=metadata,
            ))
        elif family is V3ActionFamily.LISTING_ELIMINATION:
            if primary is None or contract.relation != "companyTradesAtStockExchange":
                continue
            actions.append(_make_action(
                hgraph,
                owner=ActionOwner.M17_VERIFIER,
                family=family,
                prompt_family=PromptFamily.CONTRAST,
                view_id="v3_listing_elimination",
                model_role=ModelRole.VERIFIER.value,
                target=primary.display,
                target_id=primary.normalized_value,
                facet_id="v3_listing_elimination",
                metadata={
                    "listing_disambiguation": (
                        primary.listing_disambiguation.value
                        if primary.listing_disambiguation else
                        ListingDisambiguation.UNKNOWN.value
                    ),
                    "rejection_first": True,
                },
            ))
        elif family in (V3ActionFamily.UNARY_VERIFY, V3ActionFamily.SEMANTIC_VERIFY):
            if primary is None:
                continue
            mode = (
                V3VerificationMode.UNARY
                if family is V3ActionFamily.UNARY_VERIFY
                else V3VerificationMode.SEMANTIC
            )
            actions.append(_make_action(
                hgraph,
                owner=ActionOwner.M17_VERIFIER,
                family=family,
                prompt_family=PromptFamily.CONTRAST,
                view_id=f"v3_{mode.value.lower()}_verify",
                model_role=ModelRole.VERIFIER.value,
                target=primary.display,
                target_id=primary.normalized_value,
                facet_id=f"v3_{mode.value.lower()}_verify",
                metadata={
                    "verification_mode": mode.value,
                    "semantic_qualifier": primary.semantic_qualifier,
                },
            ))
        elif family is V3ActionFamily.CONTRAST_VERIFY:
            pair = _contrast_hypotheses(hgraph)
            if pair is None:
                continue
            left, right = pair
            actions.append(_make_action(
                hgraph,
                owner=ActionOwner.M17_VERIFIER,
                family=family,
                prompt_family=PromptFamily.CONTRAST,
                view_id="v3_contrast_verify",
                model_role=ModelRole.VERIFIER.value,
                target=left.display,
                target_id=left.normalized_value,
                comparison_id=right.normalized_value,
                comparison_text=right.display,
                facet_id="v3_contrast_verify",
                metadata={
                    "verification_mode": V3VerificationMode.CONTRAST.value,
                    "left_hypothesis_id": left.hypothesis_id,
                    "right_hypothesis_id": right.hypothesis_id,
                    "semantic_qualifier": left.semantic_qualifier,
                },
            ))
    return tuple(sorted(actions, key=lambda a: a.action_id))


def _recall_spec(
    family: V3ActionFamily, contract: RelationContract, graph: EvidenceGraph,
) -> tuple[ActionOwner, PromptFamily, str, str, bool, Mapping[str, Any]] | None:
    relation = contract.relation
    if relation == "countryLandBordersCountry":
        return None
    if family is V3ActionFamily.ATTRIBUTE_DECOMPOSITION:
        if relation != "personHasCityOfDeath":
            return None
        return (
            ActionOwner.M14_NULL_TEMPORAL,
            PromptFamily.DECOMPOSITION,
            "v3_death_attribute_decomposition",
            "v3_death_attribute_decomposition",
            False,
            {},
        )
    if family is V3ActionFamily.SET_EXPANSION:
        if relation != "awardWonBy":
            return None
        seen = tuple(sorted(c.display_value for c in graph.active_candidates()))
        return (
            ActionOwner.M13_LARGE_SET,
            PromptFamily.ALTERNATIVE,
            "v3_award_set_expansion",
            "v3_award_set_expansion",
            True,
            {"seen": seen},
        )
    if family is V3ActionFamily.DEFINITION_RECALL:
        if relation not in {"hasArea", "hasCapacity"}:
            return None
        owner = ActionOwner.M12_NUMERIC
        facet = (
            "v3_hasCapacity_definition_current_maximum"
            if relation == "hasCapacity"
            else f"v3_{relation}_definition_recall"
        )
        return (
            owner,
            PromptFamily.DEFINITION,
            f"v3_{relation}_definition_recall",
            facet,
            False,
            {"capacity_qualifier": "CURRENT_MAXIMUM" if relation == "hasCapacity" else ""},
        )
    if family is V3ActionFamily.ALTERNATIVE_RECALL:
        if relation not in {"hasArea", "hasCapacity", "awardWonBy"}:
            return None
        owner = ActionOwner.M12_NUMERIC if relation.startswith("has") else ActionOwner.M13_LARGE_SET
        return (
            owner,
            PromptFamily.ALTERNATIVE,
            f"v3_{relation}_alternative_recall",
            (
                "v3_hasCapacity_alternative_historical_configuration"
                if relation == "hasCapacity"
                else f"v3_{relation}_alternative_recall"
            ),
            False,
            {},
        )
    if family is V3ActionFamily.INDEPENDENT_RECALL:
        if relation == "companyTradesAtStockExchange":
            return None
        owner = (
            ActionOwner.M12_NUMERIC if relation.startswith("has")
            else ActionOwner.M14_NULL_TEMPORAL
            if relation == "personHasCityOfDeath" else ActionOwner.M18_STRUCTURAL
        )
        return (
            owner,
            PromptFamily.SEMANTIC_PARAPHRASE,
            f"v3_{relation}_independent_recall",
            f"v3_{relation}_independent_recall",
            False,
            {},
        )
    if family is V3ActionFamily.MULTI_VIEW_RECALL:
        if relation == "companyTradesAtStockExchange" and graph.candidates:
            return None
        owner = (
            ActionOwner.M12_NUMERIC if relation.startswith("has")
            else ActionOwner.M13_LARGE_SET
            if relation == "awardWonBy" else ActionOwner.M18_STRUCTURAL
        )
        return (
            owner,
            PromptFamily.DIRECT,
            f"v3_{relation}_multi_view_recall",
            f"v3_{relation}_multi_view_recall",
            False,
            {},
        )
    return None


def _view_family(prompt_family: PromptFamily, relation: str) -> ViewFamily:
    if prompt_family is PromptFamily.DIRECT:
        return ViewFamily.DIRECT
    if prompt_family is PromptFamily.CONTRAST:
        return ViewFamily.CONTRASTIVE
    if prompt_family is PromptFamily.ALTERNATIVE:
        return ViewFamily.MISSINGNESS if relation == "awardWonBy" else ViewFamily.STRUCTURAL
    if prompt_family is PromptFamily.DECOMPOSITION:
        return ViewFamily.CONTRASTIVE
    if prompt_family is PromptFamily.DEFINITION:
        return ViewFamily.CONTRASTIVE
    return ViewFamily.STRUCTURAL


def _recall_template(action: V3ActionCandidate, contract: RelationContract) -> str:
    relation = contract.relation
    if action.family is V3ActionFamily.SET_EXPANSION:
        from cover_kbc.v3_core.relation_programs import render_seen_set

        seen = tuple(action.metadata.get("seen") or ())
        suppression = render_seen_set(seen) if seen else "(none yet)"
        return (
            "{definition}\n\n"
            f"Recipients already identified: {suppression}\n"
            "Name additional recipients of {subject} that are not in that seen "
            "set. Do not repeat listed recipients.\n\n"
            f"{ENTITY_FORMAT}"
        )
    if action.family is V3ActionFamily.ATTRIBUTE_DECOMPOSITION:
        return (
            "{definition}\n\n"
            "Fill these slots for {subject}: birth location, residence location, "
            "death city, death country, hospital or death location, burial "
            "location. Use UNKNOWN for unavailable slots. Only death city is "
            "the target relation.\n\n"
            "Output format: one slot per line as `slot: value`, no explanation."
        )
    if relation == "hasArea":
        if action.prompt_family is PromptFamily.ALTERNATIVE:
            body = (
                "State the strongest plausible alternative total-area figure "
                "for {subject}, including its unit."
            )
        elif action.prompt_family is PromptFamily.DEFINITION:
            body = (
                "{definition}\n\n"
                "Under this definition, recall the total area of {subject} as "
                "a value plus unit."
            )
        elif action.prompt_family is PromptFamily.SEMANTIC_PARAPHRASE:
            body = (
                "Using a wording different from ordinary direct recall, give "
                "the complete surface area of {subject}, including unit."
            )
        else:
            body = "What is the total surface area of {subject}, including unit?"
        return f"{body}\n\n{NUMERIC_FORMAT}"
    if relation == "hasCapacity":
        if action.prompt_family is PromptFamily.DEFINITION:
            body = (
                "{definition}\n\n"
                "Return the current or maximum spectator-capacity value for "
                "{subject}. Preserve the semantic slot."
            )
        elif action.prompt_family is PromptFamily.ALTERNATIVE:
            body = (
                "Name the strongest plausible alternative capacity figure for "
                "{subject}, such as historical, seated, concert or sports "
                "configuration, with a short qualifier."
            )
        elif action.prompt_family is PromptFamily.CONTRAST:
            body = (
                "Contrast current maximum, historical, seated, concert and "
                "sports-configuration capacity figures for {subject}; output "
                "the value most relevant to the relation."
            )
        else:
            body = (
                "Recall the maximum spectator capacity of {subject}, including "
                "a value and a short qualifier if known."
            )
        return f"{body}\n\n{NUMERIC_FORMAT}"
    if relation == "personHasCityOfDeath":
        return (
            "{definition}\n\n"
            "Recall the city or locality of death for {subject}. Do not output "
            "birthplace, residence, burial place, country or region.\n\n"
            f"{ENTITY_FORMAT}"
        )
    if relation == "companyTradesAtStockExchange":
        return (
            "{definition}\n\n"
            "Name exchanges only if shares of {subject} itself are directly "
            "listed there now. Avoid parent, subsidiary, ADR, OTC, historical "
            "or index-only associations.\n\n"
            f"{ENTITY_FORMAT}"
        )
    return "{definition}\n\nName valid object entities for {subject}.\n\n" + ENTITY_FORMAT


def _view_spec(action: V3ActionCandidate, contract: RelationContract) -> ViewSpec:
    family = _view_family(action.prompt_family, contract.relation)
    max_tokens = 512 if action.family is V3ActionFamily.SET_EXPANSION else 128
    return ViewSpec(
        view_id=action.view_id,
        relation=contract.relation,
        family=family,
        template=_recall_template(action, contract),
        facet_id=action.facet_id,
        decode=DecodeProfile(
            name=f"v3_{action.prompt_family.value.lower()}",
            temperature=0.0,
            max_new_tokens=max_tokens,
        ),
        needs_accepted_set=action.family is V3ActionFamily.SET_EXPANSION,
    )


def execute_v3_action(
    action: V3ActionCandidate,
    graph: EvidenceGraph,
    contract: RelationContract,
    *,
    enumerator_engine: ElicitationEngine,
    verifier_runtime: LMRuntime,
    tracer: Any = None,
    run_id: int = 0,
) -> V3ActionExecution:
    """Execute one legal V3 action and mutate the existing evidence graph."""

    if action.model_role == ModelRole.VERIFIER.value:
        return _execute_verification_action(
            action, graph, contract, verifier_runtime=verifier_runtime,
            # The engine already carries this run's instructions; reading them
            # from it keeps one source of truth instead of a second parameter
            # that could be threaded inconsistently.
            relation_instructions=getattr(
                enumerator_engine, "relation_instructions", NO_INSTRUCTIONS),
        )
    return _execute_generation_action(
        action, graph, contract, enumerator_engine=enumerator_engine,
        tracer=tracer, run_id=run_id)


def _execute_generation_action(
    action: V3ActionCandidate,
    graph: EvidenceGraph,
    contract: RelationContract,
    *,
    enumerator_engine: ElicitationEngine,
    tracer: Any,
    run_id: int,
) -> V3ActionExecution:
    view = _view_spec(action, contract)
    accepted = list(action.metadata.get("seen") or ())
    outcome = enumerator_engine.run_view(
        graph.query, contract, view, run_id=run_id, accepted=accepted)
    if tracer is not None:
        tracer.log_record(outcome.record)

    if action.family is V3ActionFamily.ATTRIBUTE_DECOMPOSITION:
        slots = _death_slot_mentions(outcome.record.raw_output, contract)
        positive = [surface for slot, surface in slots if slot.is_target]
        negative = [(slot, surface) for slot, surface in slots if not slot.is_target]
        positive_keys = {
            contract.strict_key(surface) for surface in positive
            if contract.strict_key(surface)
        }
        outcome.record.parsed_values = list(positive)
        graph.register_record(outcome.record)
        touched = graph.add_entity_mentions(outcome.record, positive)
        contradicted = []
        contradicted_keys: set[str] = set()
        for slot, surface in negative:
            key = contract.strict_key(surface)
            if not key or key in positive_keys or key in contradicted_keys:
                continue
            candidate = graph.add_semantic_contradiction(
                outcome.record, surface,
                reason=f"ATTRIBUTE_CONFUSION:{slot.value}",
            )
            if candidate is not None:
                graph.reject(candidate.key, f"ATTRIBUTE_CONFUSION:{slot.value}")
                contradicted.append(candidate.key)
                contradicted_keys.add(candidate.key)
        return V3ActionExecution(
            action=action,
            candidates_named=tuple(surface for _, surface in slots),
            candidates_touched=tuple(sorted(
                {c.key for c in touched} | set(contradicted))),
            structural_outcome="ATTRIBUTE_DECOMPOSITION",
            novelty={"death_slots": [
                {"slot": slot.value, "surface": surface}
                for slot, surface in slots
            ]},
        )

    if contract.output_type is OutputType.NUMBER:
        touched = graph.add_numeric_mentions(outcome.record, outcome.observations)
        named = tuple(outcome.record.parsed_values)
    else:
        touched = graph.add_entity_mentions(outcome.record, outcome.entities)
        named = tuple(outcome.entities)
    return V3ActionExecution(
        action=action,
        candidates_named=named,
        candidates_touched=tuple(sorted(c.key for c in touched)),
        structural_outcome=action.prompt_family.value,
        novelty={"prompt_family": action.prompt_family.value},
    )


def _death_slot_mentions(
    text: str, contract: RelationContract,
) -> tuple[tuple[DeathAttributeSlot, str], ...]:
    out: list[tuple[DeathAttributeSlot, str]] = []
    for line in (text or "").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
        elif "=" in line:
            name, value = line.split("=", 1)
        else:
            continue
        slot = _death_slot_from_name(name)
        if slot is None:
            continue
        for surface in parse_entities(value, contract):
            out.append((slot, surface))
    return tuple(out)


def _death_slot_from_name(name: str) -> DeathAttributeSlot | None:
    lowered = name.lower().strip()
    if "death city" in lowered or "city of death" in lowered:
        return DeathAttributeSlot.DEATH_CITY
    if "birth" in lowered or "born" in lowered:
        return DeathAttributeSlot.BIRTH_LOCATION
    if "residence" in lowered or "lived" in lowered:
        return DeathAttributeSlot.RESIDENCE_LOCATION
    if "death country" in lowered or "country of death" in lowered:
        return DeathAttributeSlot.DEATH_COUNTRY
    if "burial" in lowered or "buried" in lowered:
        return DeathAttributeSlot.BURIAL_LOCATION
    if "hospital" in lowered or "location" in lowered:
        return DeathAttributeSlot.OTHER_LOCATION
    return None


def _execute_verification_action(
    action: V3ActionCandidate,
    graph: EvidenceGraph,
    contract: RelationContract,
    *,
    verifier_runtime: LMRuntime,
    relation_instructions: RelationInstructions = NO_INSTRUCTIONS,
) -> V3ActionExecution:
    request = _verification_request(action, contract, relation_instructions)
    prompt = render_v3_verification_prompt(request)
    try:
        result = verifier_runtime.score_labels(LabelScoreRequest(
            prompt=prompt,
            labels=_label_tokens(request.label_schema),
            system_prompt=VERIFIER_SYSTEM_PROMPT,
            metadata={
                "view_id": action.view_id,
                "subject": action.subject,
                "relation": action.relation,
                "target": action.target_id,
            },
        ))
    except LogitsUnavailable:
        return V3ActionExecution(
            action=action,
            errors=("LogitsUnavailable",),
            verification=request.to_json(),
        )
    probabilities = result.probabilities()
    argmax = result.argmax_label()
    record_id = f"v3ver:{request.request_id}"

    touched: list[str] = []
    if request.mode is V3VerificationMode.CONTRAST:
        touched.extend(_apply_contrast_result(
            graph, request, result.logits, probabilities, argmax,
            model_id=result.model_id, model_family=verifier_runtime.spec.family,
            record_id=record_id,
        ))
        margin = probabilities.get("H1", 0.0) - probabilities.get("H2", 0.0)
        verification = {
            **request.to_json(),
            "argmax_label": argmax,
            "margin": margin,
            "probabilities": dict(probabilities),
            "prompt_sha256": prompt_hash(prompt),
            "prompt_tokens": int(result.prompt_tokens or 0),
        }
        return V3ActionExecution(
            action=action,
            candidates_touched=tuple(sorted(touched)),
            verifier_outcome=argmax,
            verification=verification,
        )

    touched.extend(_apply_unary_result(
        graph, request, result.logits, probabilities, argmax,
        model_id=result.model_id, model_family=verifier_runtime.spec.family,
        record_id=record_id,
    ))
    listing_label = str(action.metadata.get("listing_disambiguation", ""))
    if (
        action.family is V3ActionFamily.LISTING_ELIMINATION
        and argmax == VerificationLabel.INVALID.value
        and request.target_id
    ):
        graph.reject(
            request.target_id,
            f"LISTING_ELIMINATION:{listing_label or ListingDisambiguation.UNKNOWN.value}",
        )
    verification = {
        **request.to_json(),
        "argmax_label": argmax,
        "margin": _valid_margin(probabilities),
        "probabilities": dict(probabilities),
        "listing_disambiguation": listing_label,
        "prompt_sha256": prompt_hash(prompt),
        "prompt_tokens": int(result.prompt_tokens or 0),
    }
    return V3ActionExecution(
        action=action,
        candidates_touched=tuple(sorted(touched)),
        verifier_outcome=argmax,
        structural_outcome=listing_label if action.family is V3ActionFamily.LISTING_ELIMINATION else "",
        verification=verification,
    )


def _label_tokens(labels: Sequence[str]) -> dict[str, str]:
    tokens = ("A", "B", "C")
    return {label: tokens[index] for index, label in enumerate(labels)}


def _verification_request(
    action: V3ActionCandidate,
    contract: RelationContract,
    relation_instructions: RelationInstructions = NO_INSTRUCTIONS,
) -> V3VerificationRequest:
    mode = V3VerificationMode(str(
        action.metadata.get("verification_mode")
        or (
            V3VerificationMode.SEMANTIC.value
            if action.family is not V3ActionFamily.UNARY_VERIFY
            else V3VerificationMode.UNARY.value
        )
    ))
    # A relation-level class boundary, empty unless a V3.1 Class-B feature
    # supplies one for this relation. It is looked up here, at the one place
    # every V3 verification request is built, so SEMANTIC, UNARY, CONTRAST and
    # the stock rejection-first frame all carry the same boundary or none.
    boundary = relation_instructions.verifier_boundary(action.relation)
    if action.family is V3ActionFamily.LISTING_ELIMINATION:
        return replace(
            stock_rejection_first_request(
                relation=action.relation,
                subject=action.subject,
                target_id=action.target_id,
                target_text=action.target,
                relation_definition=contract.verifier_definition(),
            ),
            relation_boundary=boundary,
        )
    return V3VerificationRequest(
        relation=action.relation,
        subject=action.subject,
        mode=mode,
        target_id=action.target_id,
        target_text=action.target,
        relation_definition=contract.verifier_definition(),
        comparison_id=action.comparison_id,
        comparison_text=action.comparison_text,
        semantic_qualifier=str(action.metadata.get("semantic_qualifier") or ""),
        relation_boundary=boundary,
    )


def _apply_unary_result(
    graph: EvidenceGraph, request: V3VerificationRequest,
    logits: Mapping[str, float], probabilities: Mapping[str, float], argmax: str,
    *, model_id: str, model_family: str, record_id: str,
) -> tuple[str, ...]:
    if not request.target_id:
        return ()
    label = VerificationLabel(argmax) if argmax in {
        member.value for member in VerificationLabel
    } else VerificationLabel.UNKNOWN
    result = VerificationResult(
        candidate_key=request.target_id,
        label=label,
        valid_prob=float(probabilities.get("VALID", 0.0)),
        invalid_prob=float(probabilities.get("INVALID", 0.0)),
        unknown_prob=float(probabilities.get("UNKNOWN", 0.0)),
        raw_logits=dict(logits),
        calibrated_logits=dict(logits),
        calibrated=False,
        margin=_valid_margin(probabilities),
        entropy=label_entropy(dict(probabilities)),
        template_id=f"v3_{request.mode.value.lower()}",
        model_id=model_id,
        model_family=model_family,
        record_id=record_id,
    )
    return (request.target_id,) if graph.add_verification(result) is not None else ()


def _apply_contrast_result(
    graph: EvidenceGraph, request: V3VerificationRequest,
    logits: Mapping[str, float], probabilities: Mapping[str, float], argmax: str,
    *, model_id: str, model_family: str, record_id: str,
) -> tuple[str, ...]:
    touched: list[str] = []
    if argmax == "H1":
        left_label, right_label = VerificationLabel.VALID, VerificationLabel.INVALID
    elif argmax == "H2":
        left_label, right_label = VerificationLabel.INVALID, VerificationLabel.VALID
    else:
        left_label = right_label = VerificationLabel.UNKNOWN
    pairs = (
        (request.target_id, left_label, probabilities.get("H1", 0.0),
         probabilities.get("H2", 0.0)),
        (request.comparison_id, right_label, probabilities.get("H2", 0.0),
         probabilities.get("H1", 0.0)),
    )
    for key, label, valid_like, invalid_like in pairs:
        if not key:
            continue
        result = VerificationResult(
            candidate_key=key,
            label=label,
            valid_prob=float(valid_like),
            invalid_prob=float(invalid_like),
            unknown_prob=float(probabilities.get("UNKNOWN", 0.0)),
            raw_logits=dict(logits),
            calibrated_logits=dict(logits),
            calibrated=False,
            margin=float(valid_like) - max(
                float(invalid_like), float(probabilities.get("UNKNOWN", 0.0))),
            entropy=label_entropy(dict(probabilities)),
            template_id="v3_contrast",
            model_id=model_id,
            model_family=model_family,
            record_id=f"{record_id}:{key}",
        )
        if graph.add_verification(result) is not None:
            touched.append(key)
    return tuple(touched)


def _valid_margin(probabilities: Mapping[str, float]) -> float:
    valid = float(probabilities.get("VALID", 0.0))
    invalid = float(probabilities.get("INVALID", 0.0))
    unknown = float(probabilities.get("UNKNOWN", 0.0))
    return valid - max(invalid, unknown)


def award_round_effect(
    action: V3ActionCandidate, proposed: Sequence[str],
) -> Mapping[str, Any]:
    """Expose promote-suppress-iterate novelty in the action-effect artifact."""
    round_info = award_promote_suppress_round(
        round_index=int(action.metadata.get("round_index", 0) or 0),
        seen=tuple(action.metadata.get("seen") or ()),
        proposed=proposed,
        prompt_family=action.prompt_family.value,
        facet_id=action.facet_id,
    )
    return round_info.to_json()


__all__ = [
    "V3_ACTION_EFFECT_VERSION",
    "V3ActionCandidate",
    "V3ActionExecution",
    "award_round_effect",
    "build_v3_action_catalog",
    "execute_v3_action",
]
