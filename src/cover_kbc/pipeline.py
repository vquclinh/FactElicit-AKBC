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

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.router import compile_query
from cover_kbc.controller import (
    DEFAULT_CONTROLLER,
    Action,
    ActionDecision,
    ActionType,
    ControllerConfig,
    choose_action,
    record_outcome,
)
from cover_kbc.coverage import GateState, RCSEState, trusted_keys
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.elicitation.library import get_view, views_for
from cover_kbc.evidence.consensus import AtomicConsensusEngine
from cover_kbc.evidence.consensus_adapters import applicable_specialist
from cover_kbc.evidence.consensus_types import ConsensusError, QueryConsensusResult
from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator, prior_family_map
from cover_kbc.evidence.layer4_types import Layer4EvidenceState
from cover_kbc.verification.bidirectional_types import QueryBidirectionalResult
from cover_kbc.verification.bidirectional_verifier import BidirectionalVerifier
from cover_kbc.verification.specialist_types import QuerySpecialistVerificationResult
from cover_kbc.verification.specialist_verifier import SpecialistVerifier
from cover_kbc.evidence.graph import EvidenceGraph, apply_hard_contract_rules, build_graph
from cover_kbc.models.base import LMRuntime, LogitsUnavailable
from cover_kbc.query_intelligence.parametric_retrieval import ParametricRetriever
from cover_kbc.query_intelligence.retrieval_types import ParametricRetrievalResult
from cover_kbc.query_intelligence.profiler import QueryProfiler
from cover_kbc.specialists.large_set_specialist import LargeSetSpecialist
from cover_kbc.specialists.large_set_types import LargeSetSpecialistResult
from cover_kbc.specialists.cross_family import distinct_families
from cover_kbc.specialists.small_set_specialist import SmallSetSpecialist
from cover_kbc.specialists.small_set_types import SmallSetSpecialistResult
from cover_kbc.specialists.null_temporal_specialist import NullTemporalSpecialist
from cover_kbc.specialists.null_temporal_types import NullTemporalSpecialistResult
from cover_kbc.specialists.numeric_specialist import NumericSpecialist
from cover_kbc.specialists.numeric_types import NumericSpecialistResult
from cover_kbc.query_intelligence.prompt_compiler import PromptProgramCompiler
from cover_kbc.query_intelligence.prompt_types import PromptProgram
from cover_kbc.query_intelligence.types import QueryRiskProfile
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
    EmptyReason,
    ModelRole,
    IndependenceGroup,
    OutputType,
    Prediction,
    Query,
    VerificationTier,
)
from cover_kbc.verification import (
    ContextualCalibrator,
    TEMPLATES_BY_ID,
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


class GateRoleUnavailable(RuntimeError):
    """The configured gate model role has no runtime loaded in this phase."""


class UnsupportedAction(RuntimeError):
    """The orchestrator was handed an action it has no executor for."""


class CorruptPendingAction(RuntimeError):
    """A persisted pending action could not be reconstructed."""


class PendingActionNotConsumed(RuntimeError):
    """Finalization was attempted while executable work remained."""


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
    #: Which model role scores the existence gate. Explicit, and identical in
    #: staged and interleaved execution - see ``CoverPipeline._gate_runtime``.
    gate_model_role: ModelRole = ModelRole.VERIFIER
    gate_min_margin: float = 1.0
    gate_min_prob: float = 0.5

    # -- logical model roles -------------------------------------------------
    #: The model ids the *architecture* assigns to each role, independent of
    #: which runtime objects happen to be resident right now. Staged Phase B
    #: passes one Qwen runtime as both ``runtime`` and ``verifier_runtime``;
    #: capability must not be inferred from that coincidence.
    enumerator_model_id: str = ""
    verifier_model_id: str = ""

    # -- budget --------------------------------------------------------------
    max_calls_per_query: int = 12
    max_generated_tokens_per_query: int = 6000

    scoring: ScoringConfig = field(default_factory=lambda: DEFAULT_SCORING)
    selection: SelectionConfig = field(default_factory=lambda: DEFAULT_SELECTION)
    controller: ControllerConfig = field(default_factory=lambda: DEFAULT_CONTROLLER)

    def __post_init__(self) -> None:
        """Derive ``m(o)``'s availability rule from the run mode.

        Which acquisition families are *available* is a property of how this run
        schedules views, not a free knob, so the pipeline owns it rather than
        the YAML: the active controller may schedule any declared view, and a
        fixed run with ``run_optional_views`` executes them all. Only a fixed
        mandatory-only run cannot reach the optional families, and there ``m(o)``
        must shrink so ``q(o)`` is not depressed by a mechanism that never had a
        chance to run.
        """
        available = self.enable_active_controller or self.run_optional_views
        if self.scoring.optional_views_available != available:
            self.scoring = replace(self.scoring, optional_views_available=available)
        if self.selection.scoring is not self.scoring:
            self.selection = replace(self.selection, scoring=self.scoring)

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
        if "gate_model_role" in config:
            config["gate_model_role"] = ModelRole(config["gate_model_role"])
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
        profiler: "QueryProfiler | None" = None,
        prompt_compiler: "PromptProgramCompiler | None" = None,
        retriever: "ParametricRetriever | None" = None,
        numeric_specialist: "NumericSpecialist | None" = None,
        large_set_specialist: "LargeSetSpecialist | None" = None,
        null_temporal_specialist: "NullTemporalSpecialist | None" = None,
        small_set_specialist: "SmallSetSpecialist | None" = None,
        consensus_engine: "AtomicConsensusEngine | None" = None,
        specialist_verifier: "SpecialistVerifier | None" = None,
        bidirectional_verifier: "BidirectionalVerifier | None" = None,
        layer4_integrator: "Layer4EvidenceIntegrator | None" = None,
    ) -> None:
        self.runtime = runtime
        self.config = config or PipelineConfig()
        self.engine = ElicitationEngine(runtime, seed=self.config.seed)
        self.tracer = tracer
        # Module 9, shadow mode. ``None`` - the default - is the pre-M9 code
        # path exactly. When present it observes each query at the M1 seam and
        # appends to the buffer below; it never touches the graph, the budget or
        # any decision, so predictions and call counts are unaffected.
        self.profiler = profiler
        self.query_profiles: list[QueryRiskProfile] = []
        # Module 10, shadow mode. It consumes M9's profile and produces a
        # prompt blueprint; Module 2's templates and Module 4's verifier
        # prompts are untouched, and nothing below reads either buffer.
        if prompt_compiler is not None and profiler is None:
            raise ValueError(
                "a prompt compiler (M10) was supplied without a profiler (M9); "
                "M10 consumes M9's QueryRiskProfile and cannot run without it"
            )
        self.prompt_compiler = prompt_compiler
        self.prompt_programs: list[PromptProgram] = []
        # Module 11, shadow mode. Unlike M9/M10 this one *spends neural calls*,
        # so its cost is tracked separately: it never enters Module 7's per-query
        # budget, and the counters below let production accounting stay
        # comparable while total physical spend stays honest.
        if retriever is not None and prompt_compiler is None:
            raise ValueError(
                "a parametric retriever (M11) was supplied without a prompt "
                "compiler (M10); M11 consumes M10's PromptProgram and cannot "
                "rebuild one"
            )
        self.retriever = retriever
        self.retrieval_results: list[ParametricRetrievalResult] = []
        # Module 12, shadow mode. It consumes M9/M10/M11, spends its own
        # specialist calls, and feeds nothing back: no candidate, no evidence
        # edge, no controller budget. Its spend joins the same shadow counters
        # M11 established, so a physical call is counted exactly once.
        if numeric_specialist is not None and retriever is None:
            raise ValueError(
                "a numeric specialist (M12) was supplied without a parametric "
                "retriever (M11); M12 consumes M11's parametric memory"
            )
        self.numeric_specialist = numeric_specialist
        self.numeric_results: list[NumericSpecialistResult] = []
        # Module 13, shadow mode. A sibling of M12 over a disjoint relation:
        # either may run without the other. Same shadow counters, so a physical
        # call is still counted exactly once.
        if large_set_specialist is not None and retriever is None:
            raise ValueError(
                "a large-open-set specialist (M13) was supplied without a "
                "parametric retriever (M11); M13 consumes M11's parametric memory"
            )
        self.large_set_specialist = large_set_specialist
        self.large_set_results: list[LargeSetSpecialistResult] = []
        # Module 14, shadow mode. A sibling of M12 and M13 over a disjoint
        # relation. Same shadow counters, so a physical call is counted once.
        if null_temporal_specialist is not None and retriever is None:
            raise ValueError(
                "a null/temporal specialist (M14) was supplied without a "
                "parametric retriever (M11); M14 consumes M11's parametric memory"
            )
        self.null_temporal_specialist = null_temporal_specialist
        self.null_temporal_results: list[NullTemporalSpecialistResult] = []
        # Module 15, shadow mode. The fourth and last Layer-2 sibling. Same
        # shadow counters, so a physical call is counted exactly once.
        if small_set_specialist is not None and retriever is None:
            raise ValueError(
                "a small-set closure specialist (M15) was supplied without a "
                "parametric retriever (M11); M15 consumes M11's parametric memory"
            )
        self.small_set_specialist = small_set_specialist
        self.small_set_results: list[SmallSetSpecialistResult] = []
        # Module 16, shadow mode. Non-neural: it fuses evidence the modules
        # above already produced and spends nothing. It reads the graph and
        # writes nowhere near it, so Module 8's prediction is unchanged.
        #
        # Deliberately *no* object-level dependency check here. M16 runs at the
        # Phase-C seam, where no specialist and no retriever is resident and no
        # model is loaded: what it consumes is recorded *results*, which a
        # staged run reloads from Phase A's artefacts. The dependency is
        # checked where it is real - against configuration in
        # ``build_consensus_engine``, and against the actual results in
        # ``_specialist_result_for``, which fails loudly rather than fusing
        # half an evidence state.
        self.consensus_engine = consensus_engine
        self.consensus_results: list[QueryConsensusResult] = []
        # Module 17, shadow mode. It spends real verifier calls, so the seam
        # below builds only the deterministic *catalogue* of verifiable targets
        # and verifies nothing on its own: choosing which targets are worth a
        # call is Module 20/21's, and an automatic fan-out here would be an
        # implicit budget policy shipped four modules early.
        if specialist_verifier is not None and consensus_engine is None:
            raise ValueError(
                "a specialist verifier (M17) was supplied without a consensus "
                "engine (M16); M17 verifies targets M16 identifies"
            )
        self.specialist_verifier = specialist_verifier
        self.specialist_verifications: list[QuerySpecialistVerificationResult] = []
        # Module 18, shadow mode. Like Module 17 it builds only the
        # deterministic catalogue of eligible checks - §14's four mechanisms
        # each spend a real call, and choosing which is worth one is Module
        # 20/21's. Nothing is executed without an explicit request.
        if bidirectional_verifier is not None and consensus_engine is None:
            raise ValueError(
                "a bidirectional verifier (M18) was supplied without a "
                "consensus engine (M16); M18 checks targets M16 identifies"
            )
        self.bidirectional_verifier = bidirectional_verifier
        self.bidirectional_results: list[QueryBidirectionalResult] = []
        # Layer-4 integration, shadow mode. Non-neural: it projects Modules 16,
        # 17 and 18 into one evidence view for Module 19 and spends nothing.
        if layer4_integrator is not None and consensus_engine is None:
            raise ValueError(
                "a Layer-4 integrator was supplied without a consensus engine "
                "(M16); the Layer-4 view is a projection of Module 16's state"
            )
        self.layer4_integrator = layer4_integrator
        self.layer4_results: list[Layer4EvidenceState] = []
        self.shadow_calls = 0
        self.shadow_generated_tokens = 0
        # Falling back to the enumerator keeps the interface usable with one
        # model, but then no *cross-model* evidence is claimed anywhere.
        self.verifier_runtime = verifier_runtime or runtime
        self.verifier_engine = ElicitationEngine(self.verifier_runtime, seed=self.config.seed + 1)
        self.calibrator = ContextualCalibrator()

    @property
    def has_second_model(self) -> bool:
        """Deprecated alias for :attr:`cross_model_recall_available`.

        Retained only so older call sites keep working; new code should say
        which *capability* it means.
        """
        return self.cross_model_recall_available

    @property
    def verifier_available(self) -> bool:
        """Can the verifier-role scoring capability execute right now?

        A capability question, not a residency one. In staged Phase B the same
        Qwen runtime is passed as both ``runtime`` and ``verifier_runtime``;
        judging availability by object or id inequality made blind
        verification, gate scoring and cross-model recall all vanish exactly
        when Qwen finally *was* loaded.

        Available when the runtime bound to the verifier role can score labels
        **and** genuinely fills that role - either it is a distinct object from
        the enumerator, or it identifies itself as the configured verifier. A
        bare enumerator standing in for an absent verifier is *not* the
        capability, and must not silently score gates or candidates.
        """
        spec = self.verifier_runtime.spec
        if not spec.supports_logits:
            return False
        if self.verifier_runtime is not self.runtime:
            return True
        configured = self.config.verifier_model_id
        if configured:
            return spec.model_id == configured
        return spec.role == ModelRole.VERIFIER.value

    @property
    def cross_model_recall_available(self) -> bool:
        """Is Qwen's independent recall genuinely *heterogeneous* evidence?

        Measured against the **configured enumerator model**, not against
        whichever runtime object is resident. Qwen recalling a name in Phase B
        is a second opinion relative to Mistral's enumeration even though only
        one runtime object exists at that moment.
        """
        if not self.config.enable_cross_model_recall or not self.verifier_available:
            return False
        enumerator = self.config.enumerator_model_id or self.runtime.spec.model_id
        verifier = self.config.verifier_model_id or self.verifier_runtime.spec.model_id
        return bool(enumerator) and bool(verifier) and enumerator != verifier

    # ---------------------------------------------------------------- gate --

    def _gate_deferred(self) -> bool:
        """Is the gate's model role absent from this phase?

        Deferring keeps the *same logical role* scoring the gate in staged and
        interleaved runs. The alternative - letting the enumerator score it
        because it happens to be loaded - changes the factual decision-maker
        based on execution mode, which is not a property a frozen config may
        have.
        """
        if not self.config.enable_calibrated_gate:
            return False
        if self.config.gate_model_role is ModelRole.ENUMERATOR:
            return False
        # Deferred only while the verifier-role capability is genuinely absent.
        return not self.verifier_available

    def _gate_runtime(self) -> LMRuntime:
        """The runtime that scores the existence gate.

        The gate is a **configured architectural choice**, not a consequence of
        which model happens to be resident. It is calibrated label scoring, so
        it belongs to the verifier role by default - and if that role is not
        loaded, this raises rather than silently substituting the enumerator.

        Without this, the same frozen config changed its factual decision-maker
        purely by execution mode: Qwen scored the gate interleaved, Mistral
        scored it in staged Phase A where the verifier is not resident.
        """
        role = self.config.gate_model_role
        if role is ModelRole.ENUMERATOR:
            return self.runtime
        if not self.verifier_available:
            raise GateRoleUnavailable(
                f"the calibrated gate is configured for the {role.value} role, but no "
                f"{role.value} runtime is loaded. Load it for this phase, or set "
                "pipeline.gate_model_role explicitly - substituting another model "
                "would silently change which model makes the null decision."
            )
        return self.verifier_runtime

    def _run_gate(self, graph: EvidenceGraph, contract: RelationContract) -> int:
        """Calibrated existence gate. Returns model calls spent.

        Only a *confident* negative closes the gate. An uncertain or
        high-entropy read falls through to discovery, because forcing an empty
        answer on a weak signal converts uncertainty into guaranteed zero recall.
        """
        question = GATE_QUESTIONS.get(contract.relation)
        if not question or not self.config.enable_calibrated_gate:
            return 0
        if graph.gate_result is not None:
            return 0                       # already scored in an earlier phase
        try:
            result = score_gate(
                self._gate_runtime(),
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

    def _run_reverse_check(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        view_id: str,
        candidate_key: str,
    ) -> tuple[int, int]:
        """Candidate-conditioned reverse/alternate acquisition.

        Asks the relation the other way round about one specific candidate. It
        is **acquisition, not verification**: free text, no label scoring, no
        calibration, and its output becomes ordinary candidate mentions under
        ``REVERSE_ALTERNATE``. Module 4 owns verification.
        """
        candidate = graph.candidates.get(candidate_key)
        if candidate is None:
            return 0, 0
        view = get_view(contract.relation, view_id)
        outcome = self.engine.run_reverse_view(
            graph.query, contract, view, candidate.display_value
        )
        if self.tracer is not None:
            self.tracer.log_record(outcome.record)

        before = len(graph.candidates)
        if contract.output_type is OutputType.NUMBER:
            graph.add_numeric_mentions(outcome.record, outcome.observations)
        else:
            graph.add_entity_mentions(outcome.record, outcome.entities)
        return len(graph.candidates) - before, outcome.record.generated_tokens or 0

    def _run_resample(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        view_id: str,
        discovered: list[str],
        run_id: int,
    ) -> tuple[int, int]:
        """Repeat an already-executed acquisition view as a further run.

        Subordinate to structural diversity by construction: the repeat carries
        the same view id and independence group, so Module 3 sees one mechanism
        sampled again rather than a new one, and ``g(o)`` does not move (spec
        section 7.3).

        It must carry a *distinct* ``run_id``, or it would produce a record and
        edge identical to the original run - which Module 3 correctly rejects
        as a duplicate rather than silently double-counting.
        """
        view = get_view(contract.relation, view_id)
        if view.is_gate or view.is_reverse:
            return 0, 0
        outcome = self.engine.run_view(
            graph.query,
            contract,
            view,
            run_id=run_id,
            accepted=discovered if view.needs_accepted_set else None,
        )
        if self.tracer is not None:
            self.tracer.log_record(outcome.record)
        before = len(graph.candidates)
        if contract.output_type is OutputType.NUMBER:
            graph.add_numeric_mentions(outcome.record, outcome.observations)
        else:
            touched = graph.add_entity_mentions(outcome.record, outcome.entities)
            discovered.extend(c.display_value for c in touched)
        return len(graph.candidates) - before, outcome.record.generated_tokens or 0

    @staticmethod
    def _first_recall_view(contract: RelationContract) -> str:
        """The first mandatory view that actually asks for candidates.

        Not simply ``mandatory_views[0]``: for the gated relations that view is
        the existence gate, which returns a verdict rather than names. Taking it
        blindly made cross-model recall a silent no-op for exactly the two
        relations whose precision most depends on a second opinion.
        """
        for view_id in contract.mandatory_views:
            if not get_view(contract.relation, view_id).is_gate:
                return view_id
        return ""

    def _cross_model_done(self, graph: EvidenceGraph) -> bool:
        """Has independent cross-model recall already run for this query?

        Under the active controller it is a schedulable action, so Phase B must
        not run it a second time - the repeat would regenerate the same record
        and Module 3 would (correctly) reject the duplicate edge.
        """
        return any(
            IndependenceGroup.CROSS_MODEL_RECALL in candidate.groups
            for candidate in graph.candidates.values()
        )

    def _run_cross_model_recall(
        self, graph: EvidenceGraph, contract: RelationContract
    ) -> tuple[int, int]:
        """Independent recall by the second model family.

        This is *not* verification. The second model is shown no candidate list
        and asked to answer the relation directly, so anything it produces is
        genuinely independent evidence - recorded under CROSS_MODEL_RECALL
        rather than merged into the enumerator's own families.
        """
        if not self.cross_model_recall_available:
            return 0, 0
        view_id = self.config.cross_model_view or self._first_recall_view(contract)
        if not view_id:
            return 0, 0
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

    # ------------------------------------------------------- RCSE inputs --

    def _gate_state(self, graph: EvidenceGraph, contract: RelationContract) -> GateState:
        """The existence gate as Module 6 reads it.

        ``resolved`` distinguishes "the gate answered confidently" from "the
        gate ran and could not decide". An uncertain gate must keep residual
        high rather than reading as permission to stop.
        """
        present = contract.relation in GATE_QUESTIONS and self.config.enable_calibrated_gate
        if not present:
            return GateState(present=False, resolved=False, negative=False)
        result = graph.gate_result
        if result is None:
            return GateState(present=True, resolved=False, negative=False)
        return GateState(
            present=True,
            resolved=bool(result.decision),
            negative=graph.gate_negative,
        )

    def _action_group(
        self, contract: RelationContract, action: "Action"
    ) -> IndependenceGroup | None:
        """Which acquisition mechanism an action exercised, if any."""
        if action.action_type is ActionType.CROSS_MODEL_CHECK:
            return IndependenceGroup.CROSS_MODEL_RECALL
        if not action.view_id:
            return None
        view = get_view(contract.relation, action.view_id)
        return None if view.is_gate else view.independence_group

    def _action_facet(self, contract: RelationContract, action: "Action") -> str:
        """Which semantic facet an action covered, if the view declares one."""
        if not action.view_id:
            return ""
        return get_view(contract.relation, action.view_id).facet_id

    def _run_shadow_retrieval(
        self, query: Query, program: PromptProgram
    ) -> ParametricRetrievalResult:
        """Execute Module 11's probes and record their cost separately.

        Deliberately outside the query budget: these calls are shadow
        acquisition, they produce no candidate and no evidence edge, and letting
        them draw on Module 7's allowance would change what the controller can
        afford. The spend is still counted - honestly, and attributably.
        """
        result = self.retriever.retrieve(query, program, self.runtime)
        self.retrieval_results.append(result)
        self.shadow_calls += result.total_calls
        self.shadow_generated_tokens += result.total_generated_tokens
        return result

    def _run_numeric_specialist(self, query, program, contract, retrieval) -> None:
        """Run Module 12 for a NUMERIC query, outside the production budget.

        Skipped silently for every other programme: the specialist declares
        which relations it handles, and asking it about an award list is not an
        error to report, it is simply not its query.
        """
        if not self.numeric_specialist.applies_to(program):
            return
        result = self.numeric_specialist.analyse(
            query, program, contract, self.runtime, retrieval
        )
        self.numeric_results.append(result)
        self.shadow_calls += result.calls
        self.shadow_generated_tokens += result.generated_tokens

    def _run_large_set_specialist(self, query, program, contract, retrieval) -> None:
        """Run Module 13 for a LARGE_OPEN_SET query, outside the production budget.

        Skipped silently for every other programme, for the same reason Module
        12 is: the specialist declares which relations it handles, and a numeric
        query is simply not its query.
        """
        if not self.large_set_specialist.applies_to(program):
            return
        result = self.large_set_specialist.analyse(
            query, program, contract, self.runtime, retrieval
        )
        self.large_set_results.append(result)
        self.shadow_calls += result.calls
        self.shadow_generated_tokens += result.generated_tokens

    def _run_null_temporal_specialist(self, query, program, contract, retrieval) -> None:
        """Run Module 14 for a NULL_SINGLE query, outside the production budget.

        Skipped silently for every other programme, as the sibling specialists
        are. The cross-family availability test mirrors
        :attr:`cross_model_recall_available`'s audited rule - compare the
        *configured* model ids, not whichever runtime object is resident - so a
        Phase-A run where one object serves both roles is correctly reported as
        having no second family.
        """
        if not self.null_temporal_specialist.applies_to(program):
            return
        enumerator = self.config.enumerator_model_id or self.runtime.spec.model_id
        verifier = self.config.verifier_model_id or self.verifier_runtime.spec.model_id
        distinct = distinct_families(enumerator, verifier)
        result = self.null_temporal_specialist.analyse(
            query, program, contract, self.runtime, retrieval,
            cross_family_runtime=self.verifier_runtime if distinct else None,
            cross_family_available=distinct,
        )
        self.null_temporal_results.append(result)
        self.shadow_calls += result.calls
        self.shadow_generated_tokens += result.generated_tokens

    def _run_small_set_specialist(self, query, program, contract, retrieval) -> None:
        """Run Module 15 for a SMALL_SET query, outside the production budget.

        Uses the same cross-family availability rule as Module 14 - compare the
        *configured* model ids, not resident runtime objects.
        """
        if not self.small_set_specialist.applies_to(program):
            return
        enumerator = self.config.enumerator_model_id or self.runtime.spec.model_id
        verifier = self.config.verifier_model_id or self.verifier_runtime.spec.model_id
        distinct = distinct_families(enumerator, verifier)
        result = self.small_set_specialist.analyse(
            query, program, contract, self.runtime, retrieval,
            cross_family_runtime=self.verifier_runtime if distinct else None,
            cross_family_available=distinct,
        )
        self.small_set_results.append(result)
        self.shadow_calls += result.calls
        self.shadow_generated_tokens += result.generated_tokens

    def enumerate_query(self, query: Query) -> EvidenceGraph:
        """Phase A: gate + candidate discovery. Enumerator model only."""
        query, contract = compile_query(query.subject, query.relation, query.row_index)
        # M0 -> M1 -> M9 -> acquisition. The profile is written to an
        # observability buffer, never to the graph: Module 10 will be its first
        # consumer, and until then nothing below may read it.
        if self.profiler is not None:
            profile = self.profiler.profile(query, contract)
            self.query_profiles.append(profile)
            if self.prompt_compiler is not None:
                program = self.prompt_compiler.compile(query, contract, profile)
                self.prompt_programs.append(program)
                if self.retriever is not None:
                    retrieval = self._run_shadow_retrieval(query, program)
                    if self.numeric_specialist is not None:
                        self._run_numeric_specialist(query, program, contract, retrieval)
                    if self.large_set_specialist is not None:
                        self._run_large_set_specialist(
                            query, program, contract, retrieval
                        )
                    if self.null_temporal_specialist is not None:
                        self._run_null_temporal_specialist(
                            query, program, contract, retrieval
                        )
                    if self.small_set_specialist is not None:
                        self._run_small_set_specialist(
                            query, program, contract, retrieval
                        )
        graph = build_graph(query, contract)
        budget = self.config.budget(contract)
        state = RCSEState()

        # The gate is a verifier-role action. In staged mode that role is not
        # resident during Phase A, so it is deferred to Phase B rather than
        # scored by whichever model happens to be loaded.
        if not self._gate_deferred():
            budget.charge(calls=self._run_gate(graph, contract))
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
                # Measured, not assumed: a description-first view makes two
                # generations, and charging it as one understated the fixed
                # ablation's budget by exactly that difference.
                before = self._total_runtime_calls()
                _, tokens = self._run_discovery_view(graph, contract, view.view_id, discovered)
                budget.charge(
                    calls=self._total_runtime_calls() - before,
                    generated_tokens=tokens, logical_actions=1,
                )
                state.executed_views.add(view.view_id)
                if view.facet_id:
                    state.executed_facets.add(view.facet_id)
                if not view.is_gate:
                    state.executed_groups.add(view.independence_group)

        # Cross-model recall is a controller action (CROSS_MODEL_CHECK), not a
        # phase tail. Running it unconditionally here bypassed the controller
        # entirely and made the same config behave differently by execution
        # mode: interleaved always ran it, staged only when a verify phase
        # happened to occur. Both modes now run exactly what the controller
        # chose (audit 0012 §6).
        if (
            not self.config.enable_active_controller
            and self.config.mode is not ExecutionMode.STAGED     # verifier not resident
            and not self._cross_model_done(graph)
        ):
            new, tokens = self._run_cross_model_recall(graph, contract)
            if tokens or new:
                budget.charge(calls=1, generated_tokens=tokens)

        apply_hard_contract_rules(graph)
        graph.controller_log = [d.to_json() for d in decisions]
        # Module 6's temporal state must cross the staged seam: Phase C cannot
        # recover *when* a candidate was found or what it cost from the final
        # graph, and inventing that history would fabricate yield.
        graph.rcse_state = state.to_json()
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
        resample_runs: dict[str, int] = {}
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
                cross_model_available=self.cross_model_recall_available,
                gate=self._gate_state(graph, contract),
                scoring=self.config.scoring,
            )
            action = decision.chosen

            if action.action_type is ActionType.STOP:
                decisions.append(decision)
                break

            if action.model_role is not ModelRole.ENUMERATOR and staged:
                # This phase holds the enumerator only. *Any* action needing
                # another model is persisted for the orchestrator to dispatch
                # after a role swap. Keying this on the model role rather than
                # the action type stops a new verifier-role action leaking.
                graph.pending_action = action.to_json()
                decisions.append(decision)
                break
            if not budget.can_afford(self._planned_neural_cost(contract, action)):
                decisions.append(decision)
                break

            # One execution and charging path for both modes: measuring actual
            # runtime invocations here is what keeps staged and interleaved
            # spending identically for identical decisions.
            spent, new_candidates, tokens = self._execute_action(
                graph, contract, action, discovered, resample_runs
            )
            budget.charge(calls=spent, generated_tokens=tokens, logical_actions=1)

            # Verified yield is the growth of the *trusted* set, which needs
            # the current scores and tiers. Raw mention counts are not yield:
            # a view repeating a name we already trust has added nothing.
            active = graph.active_candidates()
            for candidate in active:
                score_candidate(candidate, contract, self.config.scoring)
                candidate.tier = assign_tier(candidate, contract, self.config.scoring)
            record_outcome(
                state, action,
                trusted_keys=trusted_keys(active, contract, self.config.scoring),
                new_candidates=new_candidates,
                generated_tokens=tokens,
                independence_group=self._action_group(contract, action),
                facet_id=self._action_facet(contract, action),
                synthetic_cost=not self.runtime.spec.is_neural,
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
        if self._gate_deferred() is False and self.config.enable_calibrated_gate:
            # Verifier role is resident here: score any gate deferred by Phase A.
            graph.verification_calls += self._run_gate(graph, graph.contract)
        if graph.gate_negative:
            return graph
        if (
            not self.config.enable_active_controller
            and self.config.mode is ExecutionMode.STAGED
            and not self._cross_model_done(graph)
        ):
            new, tokens = self._run_cross_model_recall(graph, graph.contract)
            if new or tokens:
                apply_hard_contract_rules(graph)
                snapshot = dict(graph.budget_snapshot)
                snapshot["calls_used"] = int(snapshot.get("calls_used", 0)) + 1
                snapshot["generated_tokens_used"] = (
                    int(snapshot.get("generated_tokens_used", 0)) + tokens
                )
                graph.budget_snapshot = snapshot
        if self.config.enable_active_controller:
            calls = self._controlled_phase(
                graph, graph.contract,
                frozenset({ModelRole.VERIFIER, ModelRole.NONE}), phase="verify",
            )
        else:
            # The fixed path spends verifier calls too, and they belong to the
            # *same* query budget. Charging them onto the persisted snapshot is
            # what stops the final row reporting a stale Phase-A figure.
            before = self._total_runtime_calls()
            calls = self._verify_pending(graph, graph.contract)
            snapshot = dict(graph.budget_snapshot)
            snapshot["calls_used"] = int(snapshot.get("calls_used", 0)) + (
                self._total_runtime_calls() - before
            )
            graph.budget_snapshot = snapshot
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

    def _total_runtime_calls(self) -> int:
        """Neural invocations made so far across the resident runtimes.

        Object identity is the right question *here*, unlike for capability:
        staged Phase B passes one runtime under both names, and adding its
        counter twice would double-charge every call it makes.
        """
        total = self.runtime.calls
        if self.verifier_runtime is not self.runtime:
            total += self.verifier_runtime.calls
        return total

    def _planned_neural_cost(self, contract: RelationContract, action: Action) -> int:
        """Neural calls this action will need, given the **current** cache state.

        Exact, not a floor. A hard ceiling cannot be repaired after it is
        crossed, so the plan must include every invocation the action can make -
        including an uncached contextual-calibration control, whose omission
        was what let a 1-call remainder run a 2-call verification and finish at
        ``calls_used = 5/4``.

        Cache hits contribute zero, so a warm calibrator correctly makes the
        same action cheaper rather than permanently unaffordable.
        """
        if action.action_type is ActionType.STOP:
            return 0

        if action.action_type in (
            ActionType.RUN_VIEW, ActionType.RUN_FACET, ActionType.RESAMPLE
        ):
            view = get_view(contract.relation, action.view_id)
            return 2 if view.is_description else max(1, view.runs)

        if action.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY):
            adversarial = action.action_type is ActionType.ADVERSARIAL_VERIFY
            if adversarial and self.config.enable_prompt_disagreement:
                templates = [
                    TEMPLATES_BY_ID[t] for t in self.config.disagreement_template_ids
                    if t in TEMPLATES_BY_ID
                ] or [TEMPLATE_STANDARD]
            else:
                templates = [TEMPLATE_ADVERSARIAL if adversarial else TEMPLATE_STANDARD]
            cost = len(templates)
            if self.config.use_calibration:
                cost += self.calibrator.control_calls_needed(
                    self.verifier_runtime, contract, templates
                )
            return cost

        return 1

    #: Retained name for callers that only need the floor.
    _minimum_neural_cost = _planned_neural_cost

    def _execute_action(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        action: Action,
        discovered: list[str],
        resample_runs: dict[str, int],
    ) -> tuple[int, int, int]:
        """Run one controller action. Returns ``(calls, new_candidates, tokens)``.

        The returned call count is the number of **actual neural invocations**
        the runtimes made, measured from their own counters - not an assumption
        per action type. A description-first view makes two generations, a
        multi-template verification several scores plus possibly a calibration
        control, and a cache hit makes none. One logical action, N neural calls,
        and the hard budget must see N.
        """
        before = self._total_runtime_calls()

        if action.action_type in (ActionType.RUN_VIEW, ActionType.RUN_FACET):
            new, tokens = self._run_discovery_view(
                graph, contract, action.view_id, discovered
            )
        elif action.action_type is ActionType.REVERSE_CHECK:
            new, tokens = self._run_reverse_check(
                graph, contract, action.view_id, action.candidate_key
            )
        elif action.action_type is ActionType.RESAMPLE:
            run_id = resample_runs.get(
                action.view_id, get_view(contract.relation, action.view_id).runs
            )
            resample_runs[action.view_id] = run_id + 1
            new, tokens = self._run_resample(
                graph, contract, action.view_id, discovered, run_id=run_id
            )
        elif action.action_type in (ActionType.VERIFY, ActionType.ADVERSARIAL_VERIFY):
            graph.verification_calls += self._verify_one(
                graph, contract, action.candidate_key,
                action.action_type is ActionType.ADVERSARIAL_VERIFY,
            )
            new, tokens = 0, 0
        elif action.action_type is ActionType.CROSS_MODEL_CHECK:
            new, tokens = self._run_cross_model_recall(graph, contract)
            apply_hard_contract_rules(graph)
        else:
            raise UnsupportedAction(f"no executor for {action.action_type.value}")

        return self._total_runtime_calls() - before, new, tokens

    def _controlled_phase(
        self,
        graph: EvidenceGraph,
        contract: RelationContract,
        allowed_roles: frozenset[ModelRole],
        *,
        phase: str,
    ) -> int:
        """One controller phase, restricted to the roles resident right now.

        This is what makes staged execution genuinely *active* rather than a
        decision trace computed after all model work is finished: the controller
        re-runs against reloaded state, and an action it selects really does
        spend a model call.

        It resumes rather than restarts. Any ``pending_action`` whose role is
        resident is executed **first** - that exact instance, not a fresh
        choice - and cleared only once consumed. An action needing the other
        role is persisted as the new pending action for the orchestrator to
        dispatch after a role swap, never silently dropped.
        """
        state = RCSEState.from_json(graph.rcse_state)
        budget = self.config.budget(contract)
        snapshot = graph.budget_snapshot or {}
        budget.charge(
            calls=int(snapshot.get("calls_used", 0)),
            generated_tokens=int(snapshot.get("generated_tokens_used", 0)),
        )

        decisions = [dict(d) for d in graph.controller_log]
        discovered = [c.display_value for c in graph.active_candidates()]
        resample_runs: dict[str, int] = {}
        calls = 0

        pending = self._take_pending(graph, allowed_roles)
        for _ in range(self.config.max_steps_per_query):
            # Round number is the decision's position in the persisted log, so
            # it is contiguous, unique, and never restarts at a role swap.
            step = len(decisions)
            if budget.exhausted:
                break

            candidates = graph.active_candidates()
            for candidate in candidates:
                score_candidate(candidate, contract, self.config.scoring)
                candidate.tier = assign_tier(candidate, contract, self.config.scoring)

            if pending is not None:
                # A resumed action was already scored in the phase that chose
                # it; log it again here so the trace is contiguous and shows
                # exactly where the role swap happened.
                # Already logged by the phase that chose it; executing it here
                # must not add a second entry for the same action.
                action, decision = pending, None
                pending = None
            else:
                decision = choose_action(
                    contract, candidates, state, budget, step,
                    config=self.config.controller,
                    cross_model_available=self.cross_model_recall_available
                    and not self._cross_model_done(graph),
                    gate=self._gate_state(graph, contract),
                    scoring=self.config.scoring,
                    # Deliberately *not* role-filtered: the controller must
                    # choose the best action over the whole legal space. If the
                    # winner needs the other model, that is a role swap to
                    # schedule, not an action to hide - filtering here would
                    # make `pending_action` unreachable and quietly downgrade
                    # staged execution to "whatever this phase can manage".
                )
                action = decision.chosen
                record = decision.to_json()

                if action.action_type is ActionType.STOP:
                    decisions.append(record)
                    break
                if action.model_role not in allowed_roles:
                    # Needs the other model: hand it to orchestration rather
                    # than running it against the wrong one. The decision is
                    # logged once, here, and marked so the trace shows exactly
                    # where the role swap was required.
                    record["pended_for_role"] = action.model_role.value
                    decisions.append(record)
                    graph.pending_action = action.to_json()
                    break
                decisions.append(record)
                if not budget.can_afford(self._planned_neural_cost(contract, action)):
                    # A multi-call action that is guaranteed to overrun must not
                    # start: it would push the hard counter past its ceiling
                    # before the guard could fire.
                    break

            spent, new_candidates, tokens = self._execute_action(
                graph, contract, action, discovered, resample_runs
            )
            calls += spent
            budget.charge(calls=spent, generated_tokens=tokens, logical_actions=1)

            for candidate in graph.active_candidates():
                score_candidate(candidate, contract, self.config.scoring)
                candidate.tier = assign_tier(candidate, contract, self.config.scoring)
            record_outcome(
                state, action,
                trusted_keys=trusted_keys(
                    graph.active_candidates(), contract, self.config.scoring
                ),
                new_candidates=new_candidates,
                generated_tokens=tokens,
                independence_group=self._action_group(contract, action),
                facet_id=self._action_facet(contract, action),
                synthetic_cost=not self.runtime.spec.is_neural,
            )
            if self.tracer is not None and decision is not None:
                self.tracer.write({"kind": "decision", "phase": phase, **decision.to_json()})

        graph.controller_log = decisions
        graph.rcse_state = state.to_json()
        graph.budget_snapshot = {
            "calls_used": budget.calls_used,
            "generated_tokens_used": budget.generated_tokens_used,
        }
        return calls

    def _take_pending(
        self, graph: EvidenceGraph, allowed_roles: frozenset[ModelRole]
    ) -> Action | None:
        """Reconstruct and consume a pending action whose role is resident.

        Fails loudly on a payload it cannot execute. Silently discarding one
        would abandon work the controller decided was worth doing, and returning
        a prediction anyway would claim the query settled when it did not.
        """
        payload = graph.pending_action
        if not payload:
            return None
        try:
            action_type = ActionType(payload["action_type"])
            action = Action(
                action_type=action_type,
                view_id=str(payload.get("view_id", "")),
                facet_id=str(payload.get("facet_id", "")),
                candidate_key=str(payload.get("candidate_key", "")),
                reason=str(payload.get("reason", "")),
                estimated_cost=float(payload.get("estimated_cost", 1.0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise CorruptPendingAction(
                f"cannot reconstruct pending action from {payload!r}: {exc}"
            ) from exc
        if action.model_role not in allowed_roles:
            return None
        graph.pending_action = {}
        return action

    # ------------------------------------------------------------- phase C --

    def _specialist_result_for(self, graph: EvidenceGraph):
        """The applicable Layer-2 result for this query, matched by identity.

        Matched on subject/relation/row rather than on position, so a filtered
        or resumed run cannot silently pair a query with another query's
        specialist output.
        """
        module = applicable_specialist(graph.query.relation)
        results = {
            "M12": self.numeric_results,
            "M13": self.large_set_results,
            "M14": self.null_temporal_results,
            "M15": self.small_set_results,
        }[module]
        query = graph.query
        for result in results:
            plan = result.plan
            if (
                plan.relation == query.relation
                and plan.subject == query.subject
                and plan.row_index == query.row_index
            ):
                return result
        raise ConsensusError(
            f"{query.subject}/{query.relation} is routed to {module} but no "
            f"{module} result is available for it; Module 16 fuses evidence and "
            "cannot invent the specialist's half of it"
        )

    def _retrieval_result_for(self, graph: EvidenceGraph):
        query = graph.query
        for result in self.retrieval_results:
            plan = result.plan
            if (
                plan.relation == query.relation
                and plan.subject == query.subject
                and plan.row_index == query.row_index
            ):
                return result
        return None

    def _profile_for(self, graph: EvidenceGraph):
        query = graph.query
        for profile in self.query_profiles:
            if (
                profile.relation == query.relation
                and profile.subject == query.subject
                and profile.row_index == query.row_index
            ):
                return profile
        return None

    def _run_consensus(self, graph: EvidenceGraph) -> None:
        """Build Module 16's consensus for one finished query.

        Runs at the Phase-C seam, after verification, so the verifier evidence
        Module 4 produced is visible as ``L``. Spends nothing, mutates nothing:
        the graph goes in and only a separate result object comes out.
        """
        profile = self._profile_for(graph)
        result = self.consensus_engine.consense(
            graph,
            self._specialist_result_for(graph),
            retrieval=self._retrieval_result_for(graph),
            # Module 9's grades are carried, not recomputed and not combined
            # into a scalar: they are query-level risk descriptors, and M16
            # adds no judgement of its own to them.
            query_risk=(profile.to_json().get("risk", {}) if profile else {}),
        )
        self.consensus_results.append(result)
        if self.specialist_verifier is not None:
            self._catalogue_specialist_targets(result)
        if self.bidirectional_verifier is not None:
            self._catalogue_bidirectional_checks(result)
        if self.layer4_integrator is not None:
            self._integrate_layer4(result, graph)

    def _integrate_layer4(
        self, consensus: QueryConsensusResult, graph: EvidenceGraph
    ) -> None:
        """Project M16 + whatever M17/M18 already ran. **Zero calls.**

        Whatever a caller has executed by this point is integrated; nothing is
        executed to fill a gap, and a query with no verification simply has an
        overlay that says so.
        """
        self.layer4_results.append(self.layer4_integrator.integrate(
            consensus,
            verifications=[
                result
                for query in self.specialist_verifications
                if self._same_query(query, consensus)
                for result in query.results
            ],
            checks=[
                record
                for query in self.bidirectional_results
                if self._same_query(query, consensus)
                for record in query.records
            ],
            # Module 16's persisted state carries candidate keys but not the
            # families behind them, so the only defensible source is Module 3.
            prior_families=prior_family_map(graph),
        ))

    @staticmethod
    def _same_query(result: Any, consensus: QueryConsensusResult) -> bool:
        return (
            result.relation == consensus.relation
            and result.subject == consensus.subject
            and result.row_index == consensus.row_index
        )

    def integrate_layer4(
        self, consensus: QueryConsensusResult, graph: EvidenceGraph
    ) -> Layer4EvidenceState:
        """Rebuild one query's Layer-4 view after explicit M17/M18 execution."""
        if self.layer4_integrator is None:
            raise ValueError("no Layer-4 integrator is configured")
        before = len(self.layer4_results)
        self._integrate_layer4(consensus, graph)
        result = self.layer4_results.pop()
        for index in range(before):
            if self._same_query(self.layer4_results[index], consensus):
                self.layer4_results[index] = result
                return result
        self.layer4_results.append(result)
        return result

    def _catalogue_bidirectional_checks(self, consensus: QueryConsensusResult) -> None:
        """Record which §14 checks *could* be posed. Spends nothing.

        Module 15's pending descriptors are attached to the checks they asked
        for, as provenance - they never reach a prompt.
        """
        self.bidirectional_results.append(QueryBidirectionalResult(
            check_version=self.bidirectional_verifier.check_version,
            relation=consensus.relation, subject=consensus.subject,
            row_index=consensus.row_index,
            catalogue=self.bidirectional_verifier.catalogue(consensus),
        ))

    def execute_bidirectional_checks(
        self,
        consensus: QueryConsensusResult,
        requests: "Sequence[Any]",
        runtime: LMRuntime | None = None,
    ) -> QueryBidirectionalResult:
        """Run Module 18 checks **the caller chose**. Explicit by design.

        Shadow spend: it joins the same shadow counters Module 11 established,
        never Module 7's per-query budget - Module 20 owns production
        verification spend and does not exist yet.
        """
        if self.bidirectional_verifier is None:
            raise ValueError("no bidirectional verifier (M18) is configured")
        _, contract = compile_query(
            consensus.subject, consensus.relation, consensus.row_index
        )
        result = self.bidirectional_verifier.execute_all(
            consensus, contract, runtime or self.runtime, requests,
            primary_model_family=getattr(self.runtime.spec, "family", ""),
        )
        for index, existing in enumerate(self.bidirectional_results):
            if (
                existing.relation == result.relation
                and existing.subject == result.subject
                and existing.row_index == result.row_index
            ):
                self.bidirectional_results[index] = result
                break
        else:
            self.bidirectional_results.append(result)
        self.shadow_calls += result.calls
        return result

    def _catalogue_specialist_targets(self, consensus: QueryConsensusResult) -> None:
        """Record which targets Module 17 *could* verify. Spends nothing.

        A type judgement over Module 16 state - a hard-contract violation
        cannot be rescued by a verifier and a candidate with no printable value
        cannot be shown to one. It reads no support count and makes no
        selection: ``verify_specialist_targets`` is how a caller asks for
        actual readings.
        """
        from cover_kbc.verification.specialist_contracts import specialist_contract
        from cover_kbc.verification.specialist_verifier import verifiable_targets

        specialist = specialist_contract(consensus.relation)
        self.specialist_verifications.append(QuerySpecialistVerificationResult(
            verification_version=self.specialist_verifier.verification_version,
            relation=consensus.relation, subject=consensus.subject,
            row_index=consensus.row_index, family=specialist.family,
            contract_version=specialist.contract_version,
            results=(), catalogue=verifiable_targets(consensus),
        ))

    def verify_specialist_targets(
        self,
        consensus: QueryConsensusResult,
        targets: "Sequence[Any]",
        runtime: LMRuntime | None = None,
    ) -> QuerySpecialistVerificationResult:
        """Run Module 17 on targets **the caller chose**. Explicit by design.

        Shadow spend: it joins the same shadow counters Module 11 established,
        never Module 7's per-query budget, because Module 20 owns the
        production verification budget and does not exist yet.
        """
        if self.specialist_verifier is None:
            raise ValueError("no specialist verifier (M17) is configured")
        _, contract = compile_query(
            consensus.subject, consensus.relation, consensus.row_index
        )
        result = self.specialist_verifier.verify_query(
            consensus, contract, runtime or self.verifier_runtime, targets
        )
        for index, existing in enumerate(self.specialist_verifications):
            if (
                existing.relation == result.relation
                and existing.subject == result.subject
                and existing.row_index == result.row_index
            ):
                self.specialist_verifications[index] = result
                break
        else:
            self.specialist_verifications.append(result)
        self.shadow_calls += result.calls
        return result

    def decide_graph(self, graph: EvidenceGraph) -> Prediction:
        """Phase C: RCSE, scoring, relation-specific selection. No model calls.

        Refuses to finalize while the controller still has executable work and
        the budget to do it. Abandoning a chosen action and emitting a row
        anyway would claim the query settled when the controller had decided it
        had not - and it is Module 7's job to finish, not Module 8's to guess.
        """
        if graph.pending_action:
            budget = self.config.budget(graph.contract)
            snapshot = graph.budget_snapshot or {}
            budget.charge(
                calls=int(snapshot.get("calls_used", 0)),
                generated_tokens=int(snapshot.get("generated_tokens_used", 0)),
            )
            if not budget.exhausted:
                raise PendingActionNotConsumed(
                    f"{graph.query.subject}/{graph.query.relation}: the controller "
                    f"selected {graph.pending_action.get('action_type')} needing the "
                    f"{graph.pending_action.get('model_role')} role and "
                    f"{budget.calls_left} calls remain. Run the staged orchestrator to "
                    "completion before finalizing."
                )
            # Budget exhausted: the action is superseded by an explicit,
            # recorded decision rather than silently abandoned, so Module 8
            # receives a state it can legally finalize and the trace still says
            # what was given up.
            graph.controller_log = [
                *graph.controller_log,
                {
                    "step": len(graph.controller_log),
                    "chosen": {"action_type": ActionType.STOP.value, "view_id": "",
                               "facet_id": "", "candidate_key": "", "estimated_cost": 0.0,
                               "model_role": ModelRole.NONE.value,
                               "reason": "hard budget exhausted"},
                    "abandoned_action": dict(graph.pending_action),
                    "score": None, "considered": [], "residual": {},
                    "state_before": {}, "state_after": {},
                },
            ]
            graph.pending_action = {}
        budget_snapshot = graph.budget_snapshot or {}
        verification_calls = graph.verification_calls
        stopped = "gate_negative" if graph.gate_negative else "fixed_budget_views_complete"
        log = graph.controller_log
        if log:
            last = log[-1]
            stopped = last.get("chosen", {}).get("reason") or stopped

        # Module 16 observes the finished evidence state before Module 8 reads
        # it. Deliberately before ``finalize`` so the two see the same graph,
        # and deliberately incapable of changing what ``finalize`` returns.
        if self.consensus_engine is not None:
            self._run_consensus(graph)

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

    # ``verify_graph`` folds its own spend into ``graph.budget_snapshot``, so
    # ``decide_graph`` reads one authoritative figure for every mode.

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

    def resume(
        self, graphs: Iterable[EvidenceGraph], *, progress: bool = False
    ) -> Iterator[EvidenceGraph]:
        """Execute pending enumerator-role work and continue the controller.

        The other half of the staged role swap: Phase B leaves an action that
        needs the enumerator, orchestration reloads that role, and this runs
        the exact instance the controller chose before carrying on from the
        persisted state - no mandatory view is repeated and no counter resets.
        """
        for index, graph in enumerate(graphs):
            if not graph.gate_negative:
                self._controlled_phase(
                    graph, graph.contract,
                    frozenset({ModelRole.ENUMERATOR, ModelRole.NONE}), phase="resume",
                )
            yield graph
            if progress and (index + 1) % 25 == 0:
                print(f"  ... resumed {index + 1}", flush=True)

    @staticmethod
    def pending_role(graph: EvidenceGraph) -> ModelRole | None:
        """Which runtime the graph is waiting on, if any."""
        payload = graph.pending_action
        if not payload:
            return None
        try:
            return ModelRole(payload["model_role"])
        except (KeyError, ValueError) as exc:
            raise CorruptPendingAction(
                f"pending action has no usable model role: {payload!r}"
            ) from exc

    def decide(
        self,
        graphs: Iterable[EvidenceGraph],
        *,
        on_result: Callable[[Prediction, EvidenceGraph], None] | None = None,
    ) -> PipelineResult:
        """Phase C over many graphs, producing the final result.

        ``on_result`` is a progress observer, called once per finished query
        after its prediction is collected. It takes no part in the decision and
        must not modify what it is handed; ``decide`` behaves identically
        whether or not one is supplied.
        """
        result = PipelineResult()
        for graph in graphs:
            prediction = self.decide_graph(graph)
            self._collect(result, prediction, graph)
            if on_result is not None:
                on_result(prediction, graph)
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
