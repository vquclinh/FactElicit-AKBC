"""COVER-KBC v2 orchestrator: adaptive, relation-typed, staged or interleaved.

Stages are kept explicitly separate, because collapsing them is what makes a
system emit whatever the model happened to say::

    raw discovered candidates
        -> normalized candidates
        -> evidence accumulation
        -> verification
        -> accepted / rejected / unresolved
        -> final ObjectEntities

Discovery is recall-oriented and allowed to be noisy. The final decision is
evidence-oriented. A generated candidate is never emitted merely because it was
generated.

Two execution modes, both driving the same logic:

``INTERLEAVED``
    One loop. The controller may verify a candidate the moment it looks
    uncertain. Needs enumerator and verifier co-resident.

``STAGED``
    Phase A enumerate (enumerator only) -> persist -> Phase B verify (verifier
    only) -> persist -> Phase C decide (no model at all). This is what lets a
    28.67B pairing run on a Colab GPU that cannot hold both models at once.
    The counted parameter budget is unchanged by the split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.router import compile_query
from cover_kbc.controller import (
    DEFAULT_CONTROLLER,
    ActionDecision,
    ActionType,
    ControllerConfig,
    choose_action,
    record_outcome,
)
from cover_kbc.coverage import RCSEState
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.elicitation.library import get_view, views_for
from cover_kbc.evidence.graph import EvidenceGraph, apply_hard_contract_rules, build_graph
from cover_kbc.models.base import LMRuntime, LogitsUnavailable
from cover_kbc.runtime.tracing import RunTracer
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    ScoringConfig,
    assign_tier,
    score_candidate,
    verification_targets,
)
from cover_kbc.selection import DEFAULT_SELECTION, SelectionConfig, finalize
from cover_kbc.types import (
    Budget,
    CandidateStatus,
    EmptyReason,
    IndependenceGroup,
    OutputType,
    Prediction,
    Query,
    VerificationTier,
)
from cover_kbc.verification import (
    ContextualCalibrator,
    DISAGREEMENT_TEMPLATE_IDS,
    TEMPLATE_ADVERSARIAL,
    TEMPLATE_STANDARD,
    aggregate_verifications,
    score_gate,
    verify_candidate,
    verify_multi_template,
)

#: Gate questions per relation. Phrased so that NO is the empty-answer case.
GATE_QUESTIONS: dict[str, str] = {
    "personHasCityOfDeath": (
        "Is {subject} deceased? Answer B = NO only if you are confident the "
        "person is still living."
    ),
    "companyTradesAtStockExchange": (
        "Are shares of the company {subject} itself publicly traded on a stock "
        "exchange? Answer B = NO only if you are confident that {subject} itself "
        "is privately held, wholly owned, or delisted - not merely because its "
        "parent or a subsidiary is listed."
    ),
}


class ExecutionMode(str, Enum):
    INTERLEAVED = "interleaved"
    STAGED = "staged"


@dataclass
class PipelineConfig:
    """Every threshold the pipeline uses. Nothing hidden in code."""

    seed: int = 42
    mode: ExecutionMode = ExecutionMode.INTERLEAVED

    # -- discovery -----------------------------------------------------------
    run_optional_views: bool = False
    #: Let the controller choose actions instead of running a fixed view list.
    enable_active_controller: bool = False
    max_steps_per_query: int = 12

    # -- verification --------------------------------------------------------
    enable_verifier: bool = False
    use_calibration: bool = True
    max_verifications_per_query: int = 0
    enable_prompt_disagreement: bool = False
    disagreement_template_ids: tuple[str, ...] = DISAGREEMENT_TEMPLATE_IDS

    # -- cross-model ---------------------------------------------------------
    #: Run a discovery view on the verifier-family model for independent recall.
    enable_cross_model_recall: bool = False
    cross_model_view: str = ""

    # -- gates ---------------------------------------------------------------
    enable_calibrated_gate: bool = False
    gate_min_margin: float = 1.0
    gate_min_prob: float = 0.5

    # -- budget --------------------------------------------------------------
    max_calls_per_query: int = 12
    max_generated_tokens_per_query: int = 6000

    scoring: ScoringConfig = field(default_factory=lambda: DEFAULT_SCORING)
    selection: SelectionConfig = field(default_factory=lambda: DEFAULT_SELECTION)
    controller: ControllerConfig = field(default_factory=lambda: DEFAULT_CONTROLLER)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "PipelineConfig":
        config = dict(config or {})
        scoring = ScoringConfig.from_mapping(config.pop("scoring", None))
        controller = ControllerConfig.from_mapping(config.pop("controller", None))
        selection_cfg = dict(config.pop("selection", None) or {})
        selection = SelectionConfig(
            scoring=scoring,
            capacity_support_ratio=float(selection_cfg.get("capacity_support_ratio", 1.0)),
            capacity_trust_verified=bool(selection_cfg.get("capacity_trust_verified", True)),
        )
        if "mode" in config:
            config["mode"] = ExecutionMode(config["mode"])
        if "disagreement_template_ids" in config:
            config["disagreement_template_ids"] = tuple(config["disagreement_template_ids"])
        fields = set(cls.__dataclass_fields__) - {"scoring", "selection", "controller"}
        return cls(
            scoring=scoring,
            selection=selection,
            controller=controller,
            **{k: v for k, v in config.items() if k in fields},
        )

    def budget(self, contract: RelationContract | None = None) -> Budget:
        """Per-query budget, tightened by the relation contract.

        The contract's ``StoppingPolicy`` declares how much compute a relation
        is worth (spec section 5.1); the global config is a hard ceiling no
        relation may exceed. The stricter of the two applies, so borders stay
        cheap while awards keep their large allowance.

        Note this is deliberately *unlike* the verification thresholds, where
        the contract is authoritative and is never clamped. Calls and tokens are
        a safety and compute limit, not a quality operating point: a relation
        must not be able to spend more than the run was budgeted for.
        """
        calls = self.max_calls_per_query
        tokens = self.max_generated_tokens_per_query
        if contract is not None:
            calls = min(calls, contract.stopping.max_calls)
            tokens = min(tokens, contract.stopping.max_generated_tokens)
        return Budget(max_calls=calls, max_generated_tokens=tokens)


@dataclass
class PipelineResult:
    """Predictions plus aggregate accounting and diagnostics."""

    predictions: list[Prediction] = field(default_factory=list)
    total_calls: int = 0
    total_generated_tokens: int = 0
    total_prompt_tokens: int = 0
    total_verification_calls: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=dict)
    label_counts: dict[str, int] = field(default_factory=dict)
    empty_reasons: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    stop_reasons: dict[str, int] = field(default_factory=dict)

    def diagnostics(self) -> dict[str, Any]:
        n = max(1, len(self.predictions))
        return {
            "queries": len(self.predictions),
            "calls_per_query": self.total_calls / n,
            "generated_tokens_per_query": self.total_generated_tokens / n,
            "prompt_tokens_per_query": self.total_prompt_tokens / n,
            "verification_calls_per_query": self.total_verification_calls / n,
            "tier_counts": dict(sorted(self.tier_counts.items())),
            "verifier_label_counts": dict(sorted(self.label_counts.items())),
            "empty_reasons": dict(sorted(self.empty_reasons.items())),
            "action_counts": dict(sorted(self.action_counts.items())),
            "stop_reasons": dict(sorted(self.stop_reasons.items())),
            "errors": len(self.errors),
        }


class CoverPipeline:
    """Runs the COVER-KBC programme over a sequence of queries."""

    def __init__(
        self,
        runtime: LMRuntime,
        config: PipelineConfig | None = None,
        *,
        tracer: RunTracer | None = None,
        verifier_runtime: LMRuntime | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config or PipelineConfig()
        self.engine = ElicitationEngine(runtime, seed=self.config.seed)
        self.tracer = tracer
        # Falling back to the enumerator keeps the interface usable with one
        # model, but then no *cross-model* evidence is claimed anywhere.
        self.verifier_runtime = verifier_runtime or runtime
        self.verifier_engine = ElicitationEngine(self.verifier_runtime, seed=self.config.seed + 1)
        self.calibrator = ContextualCalibrator()

    @property
    def has_second_model(self) -> bool:
        return self.verifier_runtime.spec.model_id != self.runtime.spec.model_id

    # ---------------------------------------------------------------- gate --

    def _run_gate(self, graph: EvidenceGraph, contract: RelationContract) -> int:
        """Calibrated existence gate. Returns model calls spent.

        Only a *confident* negative closes the gate. An uncertain or
        high-entropy read falls through to discovery, because forcing an empty
        answer on a weak signal converts uncertainty into guaranteed zero recall.
        """
        question = GATE_QUESTIONS.get(contract.relation)
        if not question or not self.config.enable_calibrated_gate:
            return 0
        try:
            result = score_gate(
                self.verifier_runtime,
                question.format(subject=graph.query.subject),
                relation=contract.relation,
                subject=graph.query.subject,
                calibrator=self.calibrator,
                use_calibration=self.config.use_calibration,
                min_margin=self.config.gate_min_margin,
                min_prob=self.config.gate_min_prob,
            )
        except LogitsUnavailable:
            return 0

        graph.gate_result = result
        if result.is_confident_negative:
            graph.close_gate(
                f"calibrated gate: NO (p={result.p_no:.3f}, margin={result.margin:.3f})"
            )
        if self.tracer is not None:
            self.tracer.write(
                {
                    "kind": "gate",
                    "subject": graph.query.subject,
                    "relation": contract.relation,
                    **result.to_json(),
                }
            )
        return 1

    # ----------------------------------------------------------- discovery --

    def _run_discovery_view(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        view_id: str,
        discovered: list[str],
    ) -> tuple[int, int]:
        """Run one discovery view. Returns ``(new_candidates, generated_tokens)``."""
        view = get_view(contract.relation, view_id)
        if view.is_reverse:
            # Candidate-conditioned: it cannot run as a subject-only discovery
            # action. Module 2 exposes `run_reverse_view` for it.
            return 0, 0
        if view.is_description:
            # Two calls, one mechanism: prose first, then extraction from it.
            description, extraction = self.engine.run_description_view(
                graph.query, contract, view
            )
            outcomes = [description, extraction]
        else:
            outcomes = self.engine.run_view_repeats(
                graph.query,
                contract,
                view,
                accepted=discovered if view.needs_accepted_set else None,
            )

        before = len(graph.candidates)
        tokens = 0
        for outcome in outcomes:
            if self.tracer is not None:
                self.tracer.log_record(outcome.record)
            tokens += outcome.record.generated_tokens or 0

            if outcome.gate is not None:
                graph.register_record(outcome.record)
                if outcome.gate.is_negative and not self.config.enable_calibrated_gate:
                    graph.close_gate(f"{view.view_id} answered NO (uncalibrated)")
                continue

            if contract.output_type is OutputType.NUMBER:
                graph.add_numeric_mentions(outcome.record, outcome.observations)
            else:
                touched = graph.add_entity_mentions(outcome.record, outcome.entities)
                discovered.extend(c.display_value for c in touched)
        return len(graph.candidates) - before, tokens

    def _run_cross_model_recall(
        self, graph: EvidenceGraph, contract: RelationContract
    ) -> tuple[int, int]:
        """Independent recall by the second model family.

        This is *not* verification. The second model is shown no candidate list
        and asked to answer the relation directly, so anything it produces is
        genuinely independent evidence - recorded under CROSS_MODEL_RECALL
        rather than merged into the enumerator's own families.
        """
        if not self.config.enable_cross_model_recall or not self.has_second_model:
            return 0, 0
        view_id = self.config.cross_model_view or contract.mandatory_views[0]
        view = get_view(contract.relation, view_id)
        if view.is_gate:
            return 0, 0

        outcome = self.verifier_engine.run_view(
            graph.query,
            contract,
            view,
            run_id=0,
            independence_group=IndependenceGroup.CROSS_MODEL_RECALL,
        )
        if self.tracer is not None:
            self.tracer.log_record(outcome.record)

        before = len(graph.candidates)
        if contract.output_type is OutputType.NUMBER:
            graph.add_numeric_mentions(outcome.record, outcome.observations)
        else:
            graph.add_entity_mentions(outcome.record, outcome.entities)
        return len(graph.candidates) - before, outcome.record.generated_tokens or 0

    # -------------------------------------------------------- verification --

    def _verify_one(
        self, graph: EvidenceGraph, contract: RelationContract, candidate_key: str, adversarial: bool
    ) -> int:
        """Verify a single candidate. Returns verifier calls spent."""
        candidate = graph.candidates.get(candidate_key)
        if candidate is None:
            return 0
        try:
            if adversarial and self.config.enable_prompt_disagreement:
                results, _ = verify_multi_template(
                    self.verifier_runtime,
                    graph.query,
                    contract,
                    candidate.key,
                    candidate.display_value,
                    template_ids=self.config.disagreement_template_ids,
                    calibrator=self.calibrator,
                    use_calibration=self.config.use_calibration,
                )
                merged = aggregate_verifications(results)
                if merged is not None:
                    merged.model_family = self.verifier_runtime.spec.family
                    graph.add_verification(merged)
                return len(results)

            template = TEMPLATE_ADVERSARIAL if adversarial else TEMPLATE_STANDARD
            result = verify_candidate(
                self.verifier_runtime,
                graph.query,
                contract,
                candidate.key,
                candidate.display_value,
                template=template,
                calibrator=self.calibrator,
                use_calibration=self.config.use_calibration,
            )
            result.model_family = self.verifier_runtime.spec.family
            graph.add_verification(result)
            return 1
        except LogitsUnavailable:
            return 0

    def _verify_pending(self, graph: EvidenceGraph, contract: RelationContract) -> int:
        """Verify every candidate the tiering rules select, within budget."""
        if not self.config.enable_verifier or self.config.max_verifications_per_query <= 0:
            return 0
        candidates = graph.active_candidates()
        for candidate in candidates:
            score_candidate(candidate, contract, self.config.scoring)
            candidate.tier = assign_tier(candidate, contract, self.config.scoring)

        targets = verification_targets(
            candidates, contract, self.config.scoring,
            budget=self.config.max_verifications_per_query,
        )
        calls = 0
        for candidate in targets:
            calls += self._verify_one(
                graph, contract, candidate.key,
                candidate.tier is VerificationTier.ADVERSARIAL_VERIFY,
            )
        return calls

    # ------------------------------------------------------------- phase A --

    def enumerate_query(self, query: Query) -> EvidenceGraph:
        """Phase A: gate + candidate discovery. Enumerator model only."""
        query, contract = compile_query(query.subject, query.relation, query.row_index)
        graph = build_graph(query, contract)
        budget = self.config.budget(contract)
        state = RCSEState()

        gate_calls = self._run_gate(graph, contract)
        budget.charge(calls=gate_calls)
        if graph.gate_negative:
            graph.controller_log = []
            return graph

        decisions: list[ActionDecision] = []
        discovered: list[str] = []

        if self.config.enable_active_controller:
            self._adaptive_discovery(graph, contract, budget, state, decisions, discovered)
        else:
            view_ids = (
                contract.all_views() if self.config.run_optional_views else contract.mandatory_views
            )
            views = views_for(contract.relation, tuple(view_ids))
            ordered = (
                [v for v in views if v.is_gate]
                + [v for v in views if not v.is_gate and not v.needs_accepted_set]
                + [v for v in views if not v.is_gate and v.needs_accepted_set]
            )
            for view in ordered:
                if graph.gate_negative or budget.exhausted:
                    break
                _, tokens = self._run_discovery_view(graph, contract, view.view_id, discovered)
                budget.charge(calls=1, generated_tokens=tokens)
                state.covered_facets.add(view.view_id)

        if self.config.mode is not ExecutionMode.STAGED:
            new, tokens = self._run_cross_model_recall(graph, contract)
            if tokens or new:
                budget.charge(calls=1, generated_tokens=tokens)

        apply_hard_contract_rules(graph)
        graph.controller_log = [d.to_json() for d in decisions]
        graph.budget_snapshot = {
            "calls_used": budget.calls_used,
            "generated_tokens_used": budget.generated_tokens_used,
        }
        return graph

    def _adaptive_discovery(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        budget: Budget,
        state: RCSEState,
        decisions: list[ActionDecision],
        discovered: list[str],
    ) -> None:
        """Controller-driven discovery loop (Algorithm 1, discovery actions).

        In staged mode the verifier is not loaded, so verification actions are
        deferred to Phase B rather than executed here.
        """
        staged = self.config.mode is ExecutionMode.STAGED
        for step in range(self.config.max_steps_per_query):
            if budget.exhausted:
                break
            candidates = graph.active_candidates()
            for candidate in candidates:
                score_candidate(candidate, contract, self.config.scoring)
                candidate.tier = assign_tier(candidate, contract, self.config.scoring)

            decision = choose_action(
                contract, candidates, state, budget, step,
                config=self.config.controller,
                cross_model_available=self.config.enable_cross_model_recall
                and self.has_second_model,
            )
            action = decision.chosen

            if action.action_type is ActionType.STOP:
                decisions.append(decision)
                break

            new_candidates = 0
            tokens = 0
            verified = 0

            if action.action_type in (ActionType.RUN_VIEW, ActionType.RUN_FACET):
                new_candidates, tokens = self._run_discovery_view(
                    graph, contract, action.view_id, discovered
                )
                budget.charge(calls=1, generated_tokens=tokens)
            elif action.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY):
                if staged:
                    # Verifier is not resident in this phase; mark and move on.
                    state.covered_facets.add(f"deferred_verify:{action.candidate_key}")
                    decisions.append(decision)
                    continue
                verified = self._verify_one(
                    graph, contract, action.candidate_key,
                    action.action_type is ActionType.ADVERSARIAL_VERIFY,
                )
                graph.verification_calls += verified
                budget.charge(calls=max(1, verified))
            elif action.action_type is ActionType.CROSS_MODEL_CHECK:
                new_candidates, tokens = self._run_cross_model_recall(graph, contract)
                budget.charge(calls=1, generated_tokens=tokens)
            else:
                decisions.append(decision)
                break

            accepted = [
                c.key for c in graph.active_candidates()
                if c.status is CandidateStatus.ACCEPTED
            ]
            record_outcome(
                state, action,
                new_verified=verified + new_candidates,
                new_candidates=new_candidates,
                generated_tokens=tokens,
                accepted_keys=accepted,
            )
            decision.state_after = {
                "num_candidates": len(graph.candidates),
                "calls_used": budget.calls_used,
                "tokens_used": budget.generated_tokens_used,
            }
            decisions.append(decision)
            if self.tracer is not None:
                self.tracer.write({"kind": "decision", **decision.to_json()})

    # ------------------------------------------------------------- phase B --

    def verify_graph(self, graph: EvidenceGraph) -> EvidenceGraph:
        """Phase B: cross-model recall + blind verification. Verifier model only.

        Independent recall runs *before* verification, so any candidate the
        second model produces on its own is in the graph (as CROSS_MODEL_RECALL)
        before tiering decides what to spend verifier calls on.
        """
        if graph.gate_negative:
            return graph
        if self.config.mode is ExecutionMode.STAGED:
            new, tokens = self._run_cross_model_recall(graph, graph.contract)
            if new or tokens:
                apply_hard_contract_rules(graph)
                snapshot = dict(graph.budget_snapshot)
                snapshot["calls_used"] = int(snapshot.get("calls_used", 0)) + 1
                snapshot["generated_tokens_used"] = (
                    int(snapshot.get("generated_tokens_used", 0)) + tokens
                )
                graph.budget_snapshot = snapshot
        calls = self._verify_pending(graph, graph.contract)
        graph.verification_calls += calls
        if self.tracer is not None and calls:
            self.tracer.write(
                {
                    "kind": "verification",
                    "subject": graph.query.subject,
                    "relation": graph.query.relation,
                    "calls": calls,
                }
            )
        return graph

    # ------------------------------------------------------------- phase C --

    def decide_graph(self, graph: EvidenceGraph) -> Prediction:
        """Phase C: RCSE, scoring, relation-specific selection. No model calls."""
        budget_snapshot = graph.budget_snapshot or {}
        verification_calls = graph.verification_calls
        stopped = "gate_negative" if graph.gate_negative else "fixed_budget_views_complete"
        log = graph.controller_log
        if log:
            last = log[-1]
            stopped = last.get("chosen", {}).get("reason") or stopped

        prediction = finalize(
            graph,
            stopped_reason=stopped,
            config=self.config.selection,
            verification_calls=verification_calls,
        )
        prediction.calls_used = int(budget_snapshot.get("calls_used", prediction.calls_used))
        prediction.generated_tokens_used = int(
            budget_snapshot.get("generated_tokens_used", prediction.generated_tokens_used)
        )
        return prediction

    # ------------------------------------------------------------ combined --

    def run_query(self, query: Query) -> Prediction:
        """Interleaved single-query run: enumerate, verify, decide."""
        graph = self.enumerate_query(query)
        self.verify_graph(graph)
        return self.decide_graph(graph)

    def enumerate(self, queries: Iterable[Query], *, progress: bool = False) -> Iterator[EvidenceGraph]:
        """Phase A over many queries, yielding graphs for persistence."""
        for index, query in enumerate(queries):
            yield self.enumerate_query(query)
            if progress and (index + 1) % 25 == 0:
                print(f"  ... enumerated {index + 1}", flush=True)

    def verify(self, graphs: Iterable[EvidenceGraph], *, progress: bool = False) -> Iterator[EvidenceGraph]:
        """Phase B over many graphs."""
        for index, graph in enumerate(graphs):
            yield self.verify_graph(graph)
            if progress and (index + 1) % 25 == 0:
                print(f"  ... verified {index + 1}", flush=True)

    def decide(self, graphs: Iterable[EvidenceGraph]) -> PipelineResult:
        """Phase C over many graphs, producing the final result."""
        result = PipelineResult()
        for graph in graphs:
            self._collect(result, self.decide_graph(graph), graph)
        return result

    def run(self, queries: Iterable[Query], *, progress: bool = False) -> PipelineResult:
        """Run every query end to end, in order."""
        result = PipelineResult()
        queries = list(queries)
        for index, query in enumerate(queries):
            try:
                graph = self.enumerate_query(query)
                self.verify_graph(graph)
                prediction = self.decide_graph(graph)
            except Exception as exc:  # noqa: BLE001 - one query must not kill the run
                result.errors.append(
                    {
                        "SubjectEntity": query.subject,
                        "Relation": query.relation,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                prediction = Prediction(
                    subject=query.subject,
                    relation=query.relation,
                    object_entities=[],
                    row_index=query.row_index,
                    stopped_reason="pipeline_error",
                    empty_reason=EmptyReason.PIPELINE_ERROR,
                )
                graph = None
            self._collect(result, prediction, graph)
            if progress and (index + 1) % 25 == 0:
                print(f"  ... {index + 1}/{len(queries)} queries", flush=True)
        return result

    @staticmethod
    def _collect(
        result: PipelineResult, prediction: Prediction, graph: EvidenceGraph | None
    ) -> None:
        result.predictions.append(prediction)
        result.total_calls += prediction.calls_used
        result.total_generated_tokens += prediction.generated_tokens_used
        result.total_prompt_tokens += prediction.prompt_tokens_used
        result.total_verification_calls += prediction.verification_calls

        key = prediction.empty_reason.value
        result.empty_reasons[key] = result.empty_reasons.get(key, 0) + 1
        reason = prediction.stopped_reason or "unknown"
        result.stop_reasons[reason] = result.stop_reasons.get(reason, 0) + 1

        for candidate in prediction.candidates:
            tier = candidate.tier.value
            result.tier_counts[tier] = result.tier_counts.get(tier, 0) + 1
            for verification in candidate.verifications:
                label = verification.label.value
                result.label_counts[label] = result.label_counts.get(label, 0) + 1

        for entry in getattr(graph, "controller_log", None) or []:
            action = entry.get("chosen", {}).get("action_type", "UNKNOWN")
            result.action_counts[action] = result.action_counts.get(action, 0) + 1
