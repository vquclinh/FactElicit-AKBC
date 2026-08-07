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
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from cover_kbc.controller_calibration.telemetry import ControlStateFeatures

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
from cover_kbc.coverage_gap.facet_coverage import discovery_origins, facet_executions
from cover_kbc.coverage_gap.gap_types import CoverageGapState
from cover_kbc.control.budget_types import (
    BudgetSchedulerError,
    RelationBudgetResult,
)
from cover_kbc.control.layer6_integration import (
    Layer6ControlState,
    Layer6Integrator,
    collect_catalog,
)
from cover_kbc.control.micro_planner import MicroPlanner
from cover_kbc.control.planner_types import (
    MicroPlannerDecision,
    PlannerStateSnapshot,
)
from cover_kbc.control.relation_budget import RelationBudgetScheduler
from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
from cover_kbc.evidence.layer4 import Layer4EvidenceIntegrator, prior_family_map
from cover_kbc.evidence.production_bridge import (
    BridgeReport,
    ProductionEvidenceBridge,
)
from cover_kbc.integration_mode import IntegrationMode, parse_mode
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


def program_type_value(contract: Any) -> str:
    """The contract's ProgramType as its canonical value.

    ``str(enum)`` yields ``"ProgramType.SMALL_SET"``, which does not match the
    risk profile's ``"SMALL_SET"``, makes Module 20 reject every schedule, and -
    once persisted into TRAIN telemetry - produces historical bins whose
    ``program_type`` no ``HistoricalBinPackage.lookup`` can ever match.

    Public because anything that *persists* a program type needs it, not just
    the pipeline: a caller reaching for ``str(contract.program_type)`` is the
    bug this exists to prevent (Audit 0041 F-13).
    """
    program_type = getattr(contract, "program_type", "")
    return str(getattr(program_type, "value", program_type))


#: Retained for internal call sites that predate the public name.
_program_type_value = program_type_value


class ExecutionMode(str, Enum):
    INTERLEAVED = "interleaved"
    STAGED = "staged"


class GateRoleUnavailable(RuntimeError):
    """The configured gate model role has no runtime loaded in this phase."""


class AccountingInvariantError(ValueError):
    """Physical accounting stopped being measurable.

    A counter moved backwards, or a call was attributed to no role or to two.
    Distinct from an ordinary ``ValueError`` because it is **process-fatal**:
    every cost estimate derived after it would be wrong, so a long run must
    abort rather than contain it as a row-local failure.
    """


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

    #: Hard ceiling on Layer-4 action rounds **per catalogue kind** (Module 17,
    #: Module 18) for one query. §22's loop is adaptive, not unbounded, and this
    #: is the explicit deterministic bound that keeps it so.
    #:
    #: Per *kind* rather than per query on purpose: Module 18 publishes four
    #: mechanism families and Module 17 one, so a shared pool would let a long
    #: M17 catalogue exhaust the allowance before any M18 family was reached -
    #: exactly the family-coverage failure the collection policy's round-robin
    #: exists to prevent.
    max_control_rounds_per_catalogue: int = 3

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
        coverage_gap_estimator: "CoverageGapEstimator | None" = None,
        relation_budget_scheduler: "RelationBudgetScheduler | None" = None,
        micro_planner: "MicroPlanner | None" = None,
        layer6_integrator: "Layer6Integrator | None" = None,
        integration_mode: "IntegrationMode | str" = IntegrationMode.SHADOW,
        action_selector: "Callable[[str, Sequence[Any]], Sequence[Any]] | None" = None,
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
        # Module 19, shadow mode. Non-neural: it reads the Layer-4 state and
        # the applicable specialist's execution metadata and spends nothing.
        # Module 6's RCSE is untouched and still owns production q_res.
        if coverage_gap_estimator is not None and layer4_integrator is None:
            raise ValueError(
                "a coverage-gap estimator (M19) was supplied without the Layer-4 "
                "integration; M19 estimates from the Layer-4 evidence state"
            )
        self.coverage_gap_estimator = coverage_gap_estimator
        self.coverage_gap_results: list[CoverageGapState] = []
        # Module 20, shadow. Plans compute envelopes and spends nothing: it
        # never decrements the production budget below and never blocks an
        # action. Module 7 remains the production budget authority.
        if relation_budget_scheduler is not None and profiler is None:
            raise ValueError(
                "the relation budget scheduler (M20) needs Module 9's risk "
                "profiler; its proposal I/O is relation + risk + remaining budget"
            )
        self.relation_budget_scheduler = relation_budget_scheduler
        self.relation_budget_results: list[RelationBudgetResult] = []
        # Module 21, shadow. Ranks actions and returns one or STOP; it executes
        # nothing, reserves nothing and mutates nothing. Module 7 still drives
        # production and Module 8 still finalises.
        if micro_planner is not None and (
            coverage_gap_estimator is None or relation_budget_scheduler is None
        ):
            raise ValueError(
                "the micro-planner (M21) needs Module 19's coverage state and "
                "Module 20's budget state; Appendix C gives it the full state "
                "and it may not reconstruct a missing layer"
            )
        self.micro_planner = micro_planner
        self.micro_planner_results: list[MicroPlannerDecision] = []
        # Layer-6 integration, shadow. Projects each owner's declared legality
        # into one catalogue, prices it through Module 20 and ranks it through
        # Module 21. Executes nothing.
        if layer6_integrator is not None and micro_planner is None:
            raise ValueError(
                "Layer-6 integration needs Module 21; it ranks nothing itself"
            )
        self.layer6_integrator = layer6_integrator
        self.layer6_results: list[Layer6ControlState] = []
        # Normalised exactly once, here at the pipeline boundary, so no module
        # downstream ever parses a raw mode string again.
        self.integration_mode = parse_mode(integration_mode, module="pipeline")
        self.production_bridge = ProductionEvidenceBridge(self.integration_mode)
        self.bridge_reports: list[BridgeReport] = []
        #: One entry per action considered, with its own measured cost and
        #: pre/post state. The runner turns these into telemetry.
        self.action_records: list[dict[str, Any]] = []
        #: One Module 20 ledger per query, so caps actually bind.
        self._budget_ledgers: dict[tuple[str, str, int], Any] = {}
        #: Physical counters as they stood when each query started, so a control
        #: state can report query-scoped accounting rather than a run total.
        self._query_baselines: dict[tuple[str, str, int], dict[str, int]] = {}
        # Chooses which *legal* catalogue entries execute. Deliberately
        # injected rather than decided here: in collection it will be the
        # TrainCollectionPolicy, in production it belongs to Modules 20/21, and
        # neither of those is the pipeline's judgement to make. ``None`` means
        # nothing is selected - fail-closed, so an uncalibrated production run
        # spends nothing rather than verifying everything it can see.
        self.action_selector = action_selector
        self.shadow_calls = 0
        #: Physical calls made by upgraded modules in a mode that charges
        #: production. Kept apart from ``shadow_calls`` because summing the two
        #: would claim shadow diagnostics bought production evidence.
        self.production_calls = 0
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
        self._charge_calls(result.total_calls)
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
        self._charge_calls(result.calls)
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
        self._charge_calls(result.calls)
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
        self._charge_calls(result.calls)
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
        self._charge_calls(result.calls)
        self.shadow_generated_tokens += result.generated_tokens

    def enumerate_query(self, query: Query) -> EvidenceGraph:
        """Phase A: gate + candidate discovery. Enumerator model only."""
        query, contract = compile_query(query.subject, query.relation, query.row_index)
        # Taken before any work for this query, so every later control state can
        # report what *this* query cost rather than what the run has cost.
        self._query_baselines.setdefault(
            (query.subject, query.relation, query.row_index),
            self.physical_snapshot())
        # M0 -> M1 -> M9 -> acquisition. The profile is written to an
        # observability buffer, never to the graph: Module 10 will be its first
        # consumer, and until then nothing below may read it.
        if self.profiler is not None:
            profile = self.profiler.profile(query, contract)
            self.query_profiles.append(profile)
            if self.relation_budget_scheduler is not None:
                self._schedule_relation_budget(query, contract, profile)
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
        self._refine_risk_profile(graph)
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

    def _refine_risk_profile(self, graph: EvidenceGraph) -> None:
        """Proposal §5's *early graph* half of Module 9's input.

        §5 gives M9 the initial graph and early-return signals, while §20.1 runs
        it before any view executes; both hold only if M9 is evaluated twice.
        The static profile was recorded before acquisition; this replaces it
        with the graph-aware refinement now that early evidence exists.

        Observability only: the profile is a shadow artefact, the refinement is
        deterministic and non-neural, and it mutates neither the graph nor any
        production state.
        """
        if self.profiler is None:
            return
        query = graph.query
        for index, profile in enumerate(self.query_profiles):
            if (profile.relation == query.relation
                    and profile.subject == query.subject
                    and profile.row_index == query.row_index):
                self.query_profiles[index] = self.profiler.refine(profile, graph)
                return

    def _schedule_relation_budget(self, query: Query, contract, profile) -> None:
        """Plan Module 20's compute envelopes. **Zero calls.**

        Reads the query's relation, Module 9's risk profile and an *immutable
        snapshot* of the production budget. The production ``Budget`` object
        itself is never passed in, never charged and never reset - Module 7
        stays the production authority, and Module 20 only describes what the
        envelopes would be.
        """
        # The real per-query production ceiling, already tightened by the
        # relation contract. A fresh object, so the snapshot cannot alias or
        # mutate the budget an execution would use.
        budget = self.config.budget(contract)
        self.relation_budget_results.append(
            self.relation_budget_scheduler.schedule(
                subject=query.subject, relation=query.relation,
                row_index=query.row_index,
                program_type=contract.program_type.value,
                profile=profile, budget=budget,
            )
        )

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

        # Establish the round-zero control state *before* any selection, so
        # Module 21 plans over real Layer-4/19 state rather than over nothing.
        # Appendix C gives the planner the full state and forbids it
        # reconstructing a missing layer, so the layer has to exist first.
        if self.layer4_integrator is not None:
            self._integrate_layer4(result, graph)
            self.bridge_reports.append(
                self.production_bridge.apply(graph, self.layer4_results[-1]))
            if self.coverage_gap_estimator is not None:
                self._estimate_coverage_gap(result, graph.contract)

        # Each action re-integrates and refreshes, so round t+1 plans over what
        # round t changed.
        self.action_records.extend(
            self._execute_selected_verifications(result, graph))

        if self.layer4_integrator is not None:
            self._integrate_layer4(result, graph)
            # The single production seam. In shadow it is still called and
            # still writes nothing, so the call site cannot drift between
            # modes and the isolation is proven on the same path it protects.
            self.bridge_reports.append(
                self.production_bridge.apply(graph, self.layer4_results[-1]))
            if self.coverage_gap_estimator is not None:
                self._estimate_coverage_gap(result, graph.contract)
                if self.micro_planner is not None:
                    self._plan_micro_action(result, graph.contract)

    def _plan_next_action(
        self, kind: str, catalogue: "Sequence[Any]",
        consensus: QueryConsensusResult, graph: EvidenceGraph,
    ) -> "Sequence[Any]":
        """Module 21 chooses at most one action from the legal catalogue.

        The planner is handed the *current* state - rebuilt from this round's
        Layer-4 result, Module 19 residual and Module 20 ledger - so a second
        round sees what the first round's action changed. It returns a decision
        and executes nothing; mapping that decision back to the owner's own
        catalogue entry is done here, and ``execute_action`` does the work.
        """
        if not catalogue or self.micro_planner is None:
            return ()
        # Projected one entry at a time, so an ineligible entry the owner drops
        # cannot shift the pairing: a positional ``zip`` over the adapter's
        # filtered output would silently label action *i* with entry *i*.
        #
        # Several catalogue entries can still project to one action identity.
        # The planner refuses to be offered the same action twice, and rightly
        # so: ranking one action as two would double its apparent value. Keep
        # the first occurrence, which preserves the catalogue's own order.
        unique: list = []
        paired: list = []
        seen: set = set()
        for entry in catalogue:
            candidate = self.project_action(kind, entry, graph)
            if candidate is None or candidate.action_id in seen:
                continue
            seen.add(candidate.action_id)
            unique.append(candidate)
            paired.append(entry)
        if not unique:
            return ()
        candidates, catalogue = unique, paired

        query = graph.query
        ledger = self._budget_ledger_for(graph)
        state = PlannerStateSnapshot(
            subject=query.subject, relation=query.relation,
            row_index=query.row_index,
            program_type=_program_type_value(graph.contract),
            risk_profile=self._profile_for(graph),
            layer4=self.layer4_results[-1] if self.layer4_results else None,
            coverage_gap=(self.coverage_gap_results[-1]
                          if self.coverage_gap_results else None),
            # Appendix C requires the *full* state: the planner may not
            # reconstruct a layer it was not given, so the plan travels with
            # the ledger it came from.
            budget_plan=(ledger.plan if ledger is not None else None),
            budget_ledger=ledger,
        )
        decision = self.micro_planner.plan(
            state, [c.to_planner_action() for c in candidates])
        self.micro_planner_results.append(decision)
        if getattr(decision.kind, "value", decision.kind) != "ACTION":
            return ()                       # STOP: nothing executes this round

        # Map the chosen action id back to the owner's catalogue entry. The
        # projection preserves input order, so identity is positional.
        for candidate, entry in zip(candidates, catalogue):
            if candidate.action_id == decision.selected_action:
                return (entry,)
        return ()

    def _select_actions(
        self, kind: str, catalogue: "Sequence[Any]",
        consensus: QueryConsensusResult | None = None,
        graph: EvidenceGraph | None = None,
    ) -> "Sequence[Any]":
        """Ask the injected selector which legal entries to execute.

        The catalogue is the eligibility authority; the selector may only
        return a subset of it. A selector that invented an entry would be
        forcing eligibility, so that is refused rather than trusted.
        """
        if not catalogue:
            return ()
        if not self.integration_mode.may_mutate_production_state:
            return ()
        if (self.integration_mode.is_production
                and self.micro_planner is not None
                and consensus is not None and graph is not None):
            # Module 21 owns choice in calibrated production. Collection never
            # reaches here: using an uncalibrated planner to gather the very
            # bins it needs would be circular.
            return self._plan_next_action(kind, catalogue, consensus, graph)
        if self.action_selector is None:
            return ()
        chosen = tuple(self.action_selector(kind, tuple(catalogue)) or ())
        legal = {id(entry) for entry in catalogue}
        for entry in chosen:
            if id(entry) not in legal:
                raise UnsupportedAction(
                    f"{kind}: selector returned an entry that is not in the "
                    "catalogue; legality is the catalogue's to declare"
                )
        return chosen

    def execute_action(
        self, kind: str, action: Any, consensus: QueryConsensusResult,
        graph: EvidenceGraph, *, round_index: int = 1,
    ) -> dict[str, Any]:
        """The canonical seam: execute exactly one already-legal action.

        Both callers land here - Module 21 in production, the collection policy
        during TRAIN collection - so TRAIN measures the same execution and
        integration semantics production will later invoke. Selection differs;
        everything downstream of selection does not.

        Module 7 keeps execution ownership: this routes to the module that owns
        the action and never verifies or checks anything itself. Module 21, when
        it is the caller, has already returned; it never touches a runtime.

        Returns:
            A record carrying the **canonical control state either side of this
            action**, the measured cost of this action alone, the candidate
            effect it produced, and the owner's own action identity. Everything
            per-action telemetry needs is captured *here*, at the moment it is
            true; reconstructing it afterwards from mutable pipeline state is
            what made ``ΔR`` zero by construction (Audit 0041 F-03).
        """
        from cover_kbc.controller_calibration.telemetry import RedundancyStatus

        before = self.physical_snapshot()
        projection = self.project_action(kind, action, graph)
        # The full control state, not just H: Module 19's residual and its five
        # components are part of what an action changes.
        state_before = self.control_state(graph)
        evidence_before = self._candidate_evidence_signature(graph)
        admitted, refusal = self._precharge(kind, action, graph)
        base = {
            "kind": kind, "round_index": round_index, "action": action,
            "projection": projection,
            "pre": before, "state_before": state_before,
            "entropy_before": state_before.entropy,
        }
        if not admitted:
            # A refused action must cost nothing: no generate, no score_labels,
            # no counter movement. Asserted by returning before touching a
            # runtime rather than by cleaning up afterwards.
            return {
                **base, "executed": False, "admitted": False, "refusal": refusal,
                "post": before, "state_after": None, "entropy_after": None,
                "effect": None, "bridge": None,
                "cost": self.physical_delta(before, before),
            }

        # The owner's own reading is captured here, from the result it just
        # returned - not inferred later from a graph diff. For a relation whose
        # Module 17 target is a numeric *cluster* rather than an entity
        # candidate, the bridge correctly applies no candidate edge, and a
        # graph-derived verdict would silently be empty for exactly the two
        # relations §8 exists for.
        owner_reading = {"verifier_outcome": "", "structural_outcome": "",
                         "errors": ()}
        if kind == "m17":
            executed = self.verify_specialist_targets(consensus, (action,))
            owner_reading.update(
                self._m17_reading(executed, getattr(projection, "target", "")))
        elif kind == "m18":
            request = self.bidirectional_verifier.build_request(action)
            executed = self.execute_bidirectional_checks(consensus, (request,))
            owner_reading.update(self._m18_reading(executed, request))
        else:
            raise UnsupportedAction(f"no executor for action kind {kind!r}")

        # Integrate, then refresh: Module 19 must describe the state that
        # exists after this action, not the one before it.
        self._integrate_layer4(consensus, graph)
        report = self.production_bridge.apply(graph, self.layer4_results[-1])
        self.bridge_reports.append(report)
        if self.coverage_gap_estimator is not None:
            self._estimate_coverage_gap(consensus, graph.contract)

        after = self.physical_snapshot()
        # Read *after* integration, bridge and the Module 19 refresh, so it
        # describes the state the next planner round will actually see - and is
        # therefore literally the next action's pre-state.
        state_after = self.control_state(graph)
        effect = self._candidate_effect(
            evidence_before, self._candidate_evidence_signature(graph))
        # Names the probe produced that the graph does not hold. The bridge
        # reports them and inserts nothing; recording them keeps redundancy
        # measurable and preserves recall the action genuinely produced.
        named = tuple(report.discovered_not_inserted)
        touched = tuple(report.candidates_touched)
        surface = len(named) + len(touched)
        return {
            **base, "executed": True, "admitted": True, "refusal": "",
            "post": after, "state_after": state_after,
            "entropy_after": state_after.entropy,
            #: Reduction in uncertainty is positive, matching §17's +γ·ΔĤ and
            #: ``historical_bins``' "reduction in uncertainty" definition.
            "delta_entropy": state_before.entropy - state_after.entropy,
            #: Reduction in residual search need is positive, same convention.
            "delta_residual": state_before.residual - state_after.residual,
            "effect": {
                **effect,
                # Module 17's own verdict, and only Module 17's: the graph
                # -derived fallback exists because a numeric-cluster verdict
                # never reaches a candidate, but attributing it to a Module 18
                # action - which has no verdict to give - would misreport whose
                # reading it was.
                "verifier_outcome": (
                    owner_reading["verifier_outcome"] or effect["verifier_outcome"]
                    if kind == "m17" else ""),
                "structural_outcome": owner_reading["structural_outcome"],
                "errors": owner_reading["errors"],
                "candidates_named": named,
                # The bridge's own account of what this action wrote evidence
                # for. Recorded because it *is* the surface redundancy is a
                # fraction of, so the NOT_APPLICABLE claim below is checkable
                # from the record instead of taken on trust.
                "candidates_touched": touched,
                # The diff above genuinely ran, so four empty lists would be a
                # real observation rather than a hole. Stated rather than left
                # to be inferred (Audit 0043 C-05).
                "candidate_effect_measured": True,
                # Fraction of this action's candidate surface the graph already
                # held. An action that touched and named nothing has no surface
                # for redundancy to be about, which is a contract fact and not
                # a missing measurement - so it is named, not encoded as None.
                "redundancy": (len(touched) / surface) if surface else None,
                "redundancy_status": (
                    RedundancyStatus.MEASURED if surface
                    else RedundancyStatus.NOT_APPLICABLE),
            },
            "bridge": report,
            "cost": self.physical_delta(before, after),
        }

    @staticmethod
    def _m17_reading(result: Any, target_id: str) -> dict[str, Any]:
        """Module 17's own verdict for one target, and its errors.

        Read off the result M17 just returned rather than inferred from the
        graph: the verdict is what §17's false-positive estimate is built from,
        and it exists whether or not the bridge had a candidate to attach it to.
        """
        for entry in getattr(result, "results", ()):
            if getattr(entry.request.target, "target_id", "") != target_id:
                continue
            return {
                "verifier_outcome": str(entry.argmax_label or ""),
                "errors": tuple(entry.errors),
            }
        return {}

    @staticmethod
    def _m18_reading(result: Any, request: Any) -> dict[str, Any]:
        """Module 18's own signed reading for one executed check.

        §14's four mechanisms each publish their own outcome enum; the record
        carries exactly one of them. Recorded as the owner named it, with no
        re-interpretation - ``ALTERNATE_RECOVERED`` in particular is neither
        support nor contradiction, and flattening it here would lose that.

        Attribution is by ``operation_id``, which now embeds Module 18's
        canonical ``check_id`` and is therefore unique to this logical check.
        Two matches would mean the identity had gone ambiguous again, so that
        raises rather than quietly taking the first - the merged result holds
        every round's records, and picking the wrong one is precisely how a
        second counterfactual inherited the first one's outcome (Audit 0043
        C-01). No positional or fuzzy matching is used anywhere on this path.

        Raises:
            AccountingInvariantError: if the merged result holds more than one
                record for this request's identity.
        """
        matched = [record for record in getattr(result, "records", ())
                   if record.request.operation_id == request.operation_id]
        if len(matched) > 1:
            raise AccountingInvariantError(
                f"Module 18 returned {len(matched)} records for one request "
                f"identity {request.operation_id!r}; a structural reading "
                "cannot be attributed to an action that shares an identity "
                "with another"
            )
        for record in matched:
            outcome = next(
                (value for value in (
                    record.reverse_outcome, record.reconstruction_outcome,
                    record.counterfactual_outcome, record.recall_outcome,
                ) if value is not None), None)
            return {
                "structural_outcome": str(getattr(outcome, "value", "") or ""),
                "errors": (() if record.parse_status.name == "OK"
                           else (f"parse:{record.parse_status.value}",)),
            }
        return {}

    def project_action(self, kind: str, action: Any, graph: EvidenceGraph):
        """The owner's canonical projection of one catalogue entry.

        The single source of an action's persistent identity: ``action_id``,
        ``ActionFamily``, target class and the Module 20 spend classification
        all come from the owning module's own adapter rather than from this call
        site. Projecting one entry at a time keeps identity exact - the
        catalogue adapters drop ineligible entries, so zipping their output
        against the input catalogue positionally would mislabel actions.

        Returns:
            The ``ControlActionCandidate``, or ``None`` when the owner declares
            this entry ineligible and it therefore projects to no action.
        """
        from cover_kbc.control.action_catalog import m17_actions, m18_actions

        query = graph.query
        if kind == "m17":
            candidates, _ = m17_actions(
                (action,), subject=query.subject, relation=query.relation,
                row_index=query.row_index,
                verifier_config=getattr(self.specialist_verifier, "config", None))
        elif kind == "m18":
            candidates, _ = m18_actions(
                (action,), subject=query.subject, relation=query.relation,
                row_index=query.row_index)
        else:
            raise UnsupportedAction(f"no budget projection for kind {kind!r}")
        return candidates[0] if candidates else None

    def _budget_ledger_for(self, graph: EvidenceGraph):
        """Module 20's ledger for this query, built through its real contract.

        ``RelationBudgetScheduler.schedule`` plans the envelopes; ``BudgetLedger``
        is what actually holds reservations. Built once per query and cached, so
        every action in a query precharges against the same remaining budget -
        a fresh ledger per action would make every reserve succeed and the caps
        meaningless.
        """
        if self.relation_budget_scheduler is None:
            return None
        key = (graph.query.subject, graph.query.relation, graph.query.row_index)
        cached = self._budget_ledgers.get(key)
        if cached is not None:
            return cached
        from cover_kbc.control.budget_accounting import BudgetLedger

        result = self.relation_budget_scheduler.schedule(
            subject=graph.query.subject, relation=graph.query.relation,
            row_index=graph.query.row_index,
            program_type=_program_type_value(graph.contract),
            profile=self._profile_for(graph),
            budget=self.config.budget(graph.contract),
        )
        ledger = BudgetLedger(result.plan)
        self._budget_ledgers[key] = ledger
        self.relation_budget_results.append(result)
        return ledger

    def _action_descriptor(self, kind: str, action: Any, graph: EvidenceGraph):
        """The owner's own budget descriptor for one action.

        Built by the same ``action_catalog`` projection Layer 6 uses, so the
        spend class, reserve purpose and sub-call plan are the owner's
        declaration rather than this call site's opinion. An action the owner
        excluded projects to nothing and therefore has no descriptor.
        """
        projected = self.project_action(kind, action, graph)
        return projected.budget_descriptor if projected is not None else None

    def _precharge(
        self, kind: str, action: Any, graph: EvidenceGraph
    ) -> tuple[bool, str]:
        """Reserve this action's whole call plan with Module 20. **Before execution.**

        Collection runs before any TRAIN-calibrated artifact exists, so Module
        20 has nothing to schedule and the bounded collection policy is the only
        ceiling. That bound is the policy's own and is never serialised as a
        ``RelationBudgetCalibration``. Ordinary production without a real
        artifact has no scheduler at all and stays fail-closed upstream.
        """
        if self.integration_mode.is_collection:
            return True, ""
        ledger = self._budget_ledger_for(graph)
        if ledger is None:
            return True, ""
        try:
            descriptor = self._action_descriptor(kind, action, graph)
            if descriptor is None:
                return True, ""
            outcome = ledger.reserve(descriptor)
        except BudgetSchedulerError as error:
            return False, f"Module 20 refused {kind}: {error}"
        if hasattr(outcome, "reason"):
            # A resource denial: the action cannot be funded. It says nothing
            # about whether the action was worth doing - that is Module 21's.
            return False, (f"Module 20 denied {kind}: "
                           f"{getattr(outcome.reason, 'value', outcome.reason)}")
        return True, ""

    def _execute_selected_verifications(
        self, consensus: QueryConsensusResult, graph: EvidenceGraph
    ) -> list[dict[str, Any]]:
        """Run one action per round through the canonical seam. **Real calls.**

        Iterative rather than batched, because each execution changes the graph
        and therefore the state the next decision should be made against. A
        batched sweep would price every action against one stale snapshot,
        which is exactly what made the previous telemetry unusable for Module
        21's per-action estimates.

        Returns:
            One record per action considered, executed or not, in execution
            order. The runner turns these into telemetry; the pipeline itself
            keeps no opinion about how they are logged.
        """
        records: list[dict[str, Any]] = []
        if not self.integration_mode.may_mutate_production_state:
            return records

        bound = max(0, int(self.config.max_control_rounds_per_catalogue))
        round_index = 0
        for kind, source, available in (
            ("m17", self._catalogued_targets, self.specialist_verifier),
            ("m18", self._catalogued_checks, self.bidirectional_verifier),
        ):
            if available is None:
                continue
            #: Canonical, owner-published identities of the actions already run
            #: for this query. Keyed on ``action_id`` rather than ``id(entry)``
            #: because the catalogue is re-read between rounds and yields fresh
            #: objects: a memory address would let a completed action be offered
            #: again, and a resumed process would not recognise it at all.
            executed_ids: set[str] = set()
            for _ in range(bound):
                # Re-read the catalogue between actions: a stale one would offer
                # targets the previous action already resolved.
                pairs = [
                    (entry, projection)
                    for entry in source(consensus)
                    if (projection := self.project_action(kind, entry, graph))
                    is not None and projection.action_id not in executed_ids
                ]
                pending = [entry for entry, _ in pairs]
                by_entry = {id(entry): projection for entry, projection in pairs}
                chosen = self._select_actions(
                    kind, tuple(pending), consensus, graph)
                if not chosen:
                    break
                action = chosen[0]          # exactly one action per round
                round_index += 1
                record = self.execute_action(
                    kind, action, consensus, graph, round_index=round_index)
                records.append(record)
                projection = record.get("projection") or by_entry.get(id(action))
                if projection is not None:
                    executed_ids.add(projection.action_id)
                elif not record["executed"]:
                    # Nothing ran and nothing projects: offering it again would
                    # spin. Stop this catalogue rather than loop.
                    break
                for other in pending:
                    if other is action:
                        continue
                    records.append({
                        "kind": kind, "round_index": round_index,
                        "action": other, "projection": by_entry.get(id(other)),
                        "executed": False, "admitted": True,
                        "refusal": "", "legal_not_selected": True,
                        "pre": record["pre"], "post": None,
                        "state_before": record["state_before"],
                        "state_after": None,
                        "entropy_before": record.get("entropy_before"),
                        "entropy_after": None, "effect": None, "bridge": None,
                        "cost": self.physical_delta(record["pre"], record["pre"]),
                    })
                if not record["executed"]:
                    # Module 20 refused this action; the next round would be
                    # offered the same catalogue and refused identically.
                    break
        return records

    def _catalogued_targets(self, consensus: QueryConsensusResult) -> tuple:
        for entry in self.specialist_verifications:
            if self._same_query(entry, consensus):
                return tuple(entry.catalogue)
        return ()

    def _catalogued_checks(self, consensus: QueryConsensusResult) -> tuple:
        for entry in self.bidirectional_results:
            if self._same_query(entry, consensus):
                return tuple(entry.catalogue)
        return ()

    def control_entropy(self, graph: EvidenceGraph) -> float:
        """The canonical control-state uncertainty ``H``, from its owner.

        This is Module 5's ``mean_inclusion_uncertainty`` - the mean of
        ``H_inc(o) = -q log q - (1-q) log(1-q)`` over active candidates,
        already normalised to ``[0, 1]`` by its owner. No entropy is computed
        here and none is computed in the collection runner: ``historical_bins``
        states that Module 21 never recomputes an entropy of its own, and two
        definitions of ``H`` would make ``ΔĤ`` mean different things in the
        planner and in the telemetry it was derived from.
        """
        from cover_kbc.coverage import mean_inclusion_uncertainty

        return float(mean_inclusion_uncertainty(
            graph.active_candidates(), graph.contract))

    #: Counters ``physical_snapshot`` publishes and ``physical_delta`` differences.
    PHYSICAL_COUNTERS = (
        "enumerator_calls", "verifier_calls", "physical_calls",
        "prompt_tokens", "generated_tokens",
    )

    def physical_snapshot(self) -> dict[str, int]:
        """Ground-truth physical counters, read from the runtimes themselves.

        The role partition is *measured*, never inferred: Modules 14 and 15 use
        the enumerator and the verifier in one operation, so no call site can
        say which role a given ``result.calls`` belonged to. The runtimes know,
        because they counted. Differencing this around an action gives that
        action's true cost, which is what Module 21's ``Ĉost`` has to be built
        from.

        Prompt tokens are summed from the same runtimes for the same reason
        generated tokens are: Module 20's token ceiling is priced in real
        tokens, and re-tokenising a prompt in a runner to guess would be a
        second measurement of a number the backend already reported.
        """
        single_role = self.verifier_runtime is self.runtime
        enumerator = int(getattr(self.runtime, "calls", 0))
        verifier = 0 if single_role else int(
            getattr(self.verifier_runtime, "calls", 0))
        generated = int(getattr(self.runtime, "generated_tokens", 0))
        prompt = int(getattr(self.runtime, "prompt_tokens", 0))
        if not single_role:
            generated += int(getattr(self.verifier_runtime, "generated_tokens", 0))
            prompt += int(getattr(self.verifier_runtime, "prompt_tokens", 0))
        return {
            "enumerator_calls": enumerator,
            "verifier_calls": verifier,
            "physical_calls": enumerator + verifier,
            "prompt_tokens": prompt,
            "generated_tokens": generated,
            "single_role_profile": single_role,
        }

    @classmethod
    def physical_delta(
        cls, before: dict[str, int], after: dict[str, int]
    ) -> dict[str, int]:
        """Cost of whatever happened between two snapshots.

        Raises:
            AccountingInvariantError: if a counter moved backwards, which would
                mean a counter was reset mid-action and the delta is
                meaningless, or if the role partition stops summing. Both are
                process-fatal: every later cost estimate would inherit the
                error.
        """
        delta = {}
        for key in cls.PHYSICAL_COUNTERS:
            moved = int(after.get(key, 0)) - int(before.get(key, 0))
            if moved < 0:
                raise AccountingInvariantError(
                    f"physical counter {key} moved backwards ({moved}); an "
                    "action's cost cannot be measured across a counter reset"
                )
            delta[key] = moved
        if delta["enumerator_calls"] + delta["verifier_calls"] != delta["physical_calls"]:
            raise AccountingInvariantError(
                "role partition does not sum to the physical call total; a "
                "neural call was attributed to no role or to two"
            )
        return delta

    # ------------------------------------------------------- control state --

    def _query_key(self, graph: EvidenceGraph) -> tuple[str, str, int]:
        return (graph.query.subject, graph.query.relation, graph.query.row_index)

    def query_physical_cost(self, graph: EvidenceGraph) -> dict[str, int]:
        """Physical spend attributable to *this query* so far.

        Differenced against the baseline taken when the query started, so a
        control state carries query-scoped accounting rather than a
        run-cumulative counter that says nothing about the query being planned.
        """
        baseline = self._query_baselines.get(self._query_key(graph))
        if baseline is None:
            return {key: 0 for key in self.PHYSICAL_COUNTERS}
        return self.physical_delta(baseline, self.physical_snapshot())

    def coverage_gap_state(self, graph: EvidenceGraph):
        """Module 19's current state for this query, or ``None`` if it never ran.

        One state per query by construction - ``_estimate_coverage_gap``
        replaces rather than appends - so this is the current residual, not a
        history of it.
        """
        query = graph.query
        for state in self.coverage_gap_results:
            if (state.relation == query.relation
                    and state.subject == query.subject
                    and getattr(state, "row_index", query.row_index) == query.row_index):
                return state
        return None

    def control_state(self, graph: EvidenceGraph) -> "ControlStateFeatures":
        """The canonical Layer-5/6 control state, read from its owners.

        Module 19 owns the residual and its five §15 components; Module 5 owns
        ``H``; Module 3 owns the active candidate set; the runtimes own the
        physical accounting. Nothing is recomputed here and nothing is
        defaulted - a shape Module 19 does not publish raises, because a zeroed
        control state is indistinguishable from a real one once it is written.
        """
        from cover_kbc.controller_calibration.telemetry import ControlStateFeatures

        cost = self.query_physical_cost(graph)
        budget = self.config.budget(graph.contract)
        used = int((graph.budget_snapshot or {}).get("calls_used", 0))
        return ControlStateFeatures.from_coverage_gap(
            self.coverage_gap_state(graph),
            entropy=self.control_entropy(graph),
            active_candidates=len(graph.active_candidates()),
            calls_used=cost["physical_calls"],
            # Module 7's own remaining per-query allowance, which is the ceiling
            # an action is actually planned against. Never Module 20's, which
            # has no calibrated envelope yet.
            calls_remaining=max(0, budget.max_calls - used),
            prompt_tokens=cost["prompt_tokens"],
            generated_tokens=cost["generated_tokens"],
        )

    @staticmethod
    def _candidate_evidence_signature(graph: EvidenceGraph) -> dict[str, tuple]:
        """Per-candidate evidence counters, for differencing around one action.

        Reads only what Module 3 already publishes: supporting-event totals,
        contradiction totals and the verdicts attached so far. A candidate that
        gains support is *supported by* the action that ran between two of these.
        """
        return {
            key: (
                candidate.raw_support_count,
                candidate.contradiction_count,
                tuple(v.label.value for v in candidate.verifications),
            )
            for key, candidate in graph.candidates.items()
        }

    @classmethod
    def _candidate_effect(
        cls, before: dict[str, tuple], after: dict[str, tuple],
    ) -> dict[str, Any]:
        """What one action did to the candidate graph. Measured, never assumed."""
        added = sorted(set(after) - set(before))
        supported: list[str] = []
        contradicted: list[str] = []
        verdicts: list[str] = []
        for key, (support, contradiction, labels) in after.items():
            prior_support, prior_contradiction, prior_labels = before.get(
                key, (0, 0, ()))
            new_labels = list(labels[len(prior_labels):])
            verdicts.extend(new_labels)
            if support > prior_support or "VALID" in new_labels:
                supported.append(key)
            if contradiction > prior_contradiction or "INVALID" in new_labels:
                contradicted.append(key)
        return {
            "candidates_added": tuple(added),
            "candidates_supported": tuple(sorted(supported)),
            "candidates_contradicted": tuple(sorted(contradicted)),
            # One action verifies one target, so a single verdict is the normal
            # case; several are joined rather than one silently winning.
            "verifier_outcome": "|".join(verdicts),
        }

    def _charge_calls(self, calls: int) -> None:
        """Bill physical calls to whichever ledger this mode owns.

        Never both: shadow work is real work, but it bought no production
        evidence, and summing the two would claim otherwise.
        """
        if self.integration_mode.charges_production_budget:
            self.production_calls += calls
        else:
            self.shadow_calls += calls

    def _estimate_coverage_gap(
        self, consensus: QueryConsensusResult, contract: RelationContract
    ) -> None:
        """Estimate §15's residual search need. **Zero calls.**

        The Layer-4 state supplies the evidence; the applicable specialist's own
        record supplies the structural facet/execution metadata Layer 4 does not
        carry. Module 6's RCSE is neither read nor written.
        """
        layer4 = self.layer4_results[-1]
        specialist = self._specialist_result_or_none(consensus)
        # One residual per query, always the latest. M19 is recomputed after
        # every executed action, and keeping superseded snapshots would turn
        # this from the control state into a history of it.
        estimated = self.coverage_gap_estimator.estimate_coverage_gap(
            layer4,
            program_type=contract.program_type.value,
            facet_executions=facet_executions(consensus.relation, specialist),
            discovery_origins=discovery_origins(
                consensus.relation, specialist, layer4
            ),
        )
        for index, existing in enumerate(self.coverage_gap_results):
            if self._same_query(existing, consensus):
                self.coverage_gap_results[index] = estimated
                break
        else:
            self.coverage_gap_results.append(estimated)

    def _plan_micro_action(
        self, consensus: QueryConsensusResult, contract: RelationContract
    ) -> None:
        """Rank actions and select one, or STOP. **Zero calls, no execution.**

        Sits after Layer 4, Module 19 and Module 20 because §17 plans over the
        full state. The decision is recorded and nothing acts on it: Module 7
        still drives production and Module 8 still finalises.

        The legal-action list is empty here on purpose. No module yet exposes an
        owner-declared legal-action surface, and Module 21 may not invent
        legality from a weak facet or a high residual - so the honest live
        decision is STOP with reason ``NO_LEGAL_ACTION`` until such a surface
        exists. Layer-6 integration will supply it.
        """
        budget = self._relation_budget_for(consensus)
        ledger = None
        if self.layer6_integrator is not None and budget and budget.plan.is_numeric:
            from cover_kbc.control.budget_accounting import BudgetLedger

            ledger = BudgetLedger(budget.plan)
        state = PlannerStateSnapshot(
            subject=consensus.subject, relation=consensus.relation,
            row_index=consensus.row_index,
            program_type=contract.program_type.value,
            risk_profile=self._profile_for_consensus(consensus),
            layer4=self.layer4_results[-1],
            coverage_gap=self.coverage_gap_results[-1],
            budget_plan=budget.plan if budget else None,
            budget_ledger=ledger,
        )
        if self.layer6_integrator is None:
            # Module 21 alone has no owner-declared legal-action surface to
            # read, and may not invent one, so the honest decision is STOP.
            self.micro_planner_results.append(self.micro_planner.plan(state, ()))
            return

        catalog, exclusions = self._owner_action_catalog(consensus)
        result = self.layer6_integrator.integrate(
            state, catalog, exclusions,
            production_control={
                "controller": "M7",
                "note": "Module 7 remains the production controller",
            },
        )
        self.layer6_results.append(result)
        self.micro_planner_results.append(result.decision)

    def _owner_action_catalog(self, consensus: QueryConsensusResult):
        """Project every owner's declared legality. **Zero calls.**

        Each surface is the owner's own: Module 17's verifiable targets, Module
        18's eligible checks, the applicable specialist's live registry and
        execution record, and Module 11's probe record. Layer 6 declares
        nothing.
        """
        from cover_kbc.verification.bidirectional_verifier import eligible_checks
        from cover_kbc.verification.specialist_verifier import verifiable_targets

        return collect_catalog(
            subject=consensus.subject, relation=consensus.relation,
            row_index=consensus.row_index,
            specialist_result=self._specialist_result_or_none(consensus),
            specialist_declared=True,
            retrieval=self._retrieval_result_for_consensus(consensus),
            verifiable_targets=verifiable_targets(consensus),
            eligible_checks=eligible_checks(consensus),
            # Module 17's live configuration, so its safe precharge tracks the
            # phrasings and label orders actually configured rather than a
            # fixed count that would silently become a production assumption.
            verifier_config=(
                self.specialist_verifier.config
                if self.specialist_verifier is not None else None
            ),
        )

    def _retrieval_result_for_consensus(self, consensus: QueryConsensusResult):
        for result in self.retrieval_results:
            plan = result.plan
            if (plan.relation == consensus.relation
                    and plan.subject == consensus.subject
                    and plan.row_index == consensus.row_index):
                return result
        return None

    def _relation_budget_for(self, consensus: QueryConsensusResult):
        for result in self.relation_budget_results:
            if (result.relation == consensus.relation
                    and result.subject == consensus.subject
                    and result.row_index == consensus.row_index):
                return result
        return None

    def _profile_for_consensus(self, consensus: QueryConsensusResult):
        for profile in self.query_profiles:
            if (profile.relation == consensus.relation
                    and profile.subject == consensus.subject
                    and profile.row_index == consensus.row_index):
                return profile
        return None

    def _specialist_result_or_none(self, consensus: QueryConsensusResult):
        """The applicable specialist result, or None when it never ran."""
        try:
            return self._specialist_result_for_consensus(consensus)
        except ConsensusError:
            return None

    def _specialist_result_for_consensus(self, consensus: QueryConsensusResult):
        module = applicable_specialist(consensus.relation)
        results = {
            "M12": self.numeric_results,
            "M13": self.large_set_results,
            "M14": self.null_temporal_results,
            "M15": self.small_set_results,
        }[module]
        for result in results:
            plan = result.plan
            if (
                plan.relation == consensus.relation
                and plan.subject == consensus.subject
                and plan.row_index == consensus.row_index
            ):
                return result
        raise ConsensusError(
            f"{consensus.subject}/{consensus.relation}: no {module} result"
        )

    def _integrate_layer4(
        self, consensus: QueryConsensusResult, graph: EvidenceGraph
    ) -> None:
        """Project M16 + whatever M17/M18 already ran. **Zero calls.**

        Whatever a caller has executed by this point is integrated; nothing is
        executed to fill a gap, and a query with no verification simply has an
        overlay that says so.
        """
        # One Layer-4 result per query, always the latest: the state is
        # re-integrated after every executed action, and keeping the
        # superseded snapshots would make ``layer4_results`` a history rather
        # than the state, breaking every "one artefact per query" invariant.
        integrated = self.layer4_integrator.integrate(
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
        )
        for index, existing in enumerate(self.layer4_results):
            if self._same_query(existing, consensus):
                self.layer4_results[index] = integrated
                break
        else:
            self.layer4_results.append(integrated)

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
                # Merge, never replace. Module 18 reports exactly what it was
                # asked to run, so a second round would otherwise erase the
                # first round's records from the Layer-4 projection and undo
                # evidence the bridge had already applied.
                result = replace(
                    result,
                    records=self._merge_by(
                        existing.records, result.records,
                        lambda record: record.origin_event_id),
                    errors=tuple(existing.errors) + tuple(result.errors),
                )
                self.bidirectional_results[index] = result
                break
        else:
            self.bidirectional_results.append(result)
        self._charge_calls(result.calls)
        return result

    @staticmethod
    def _merge_by(
        previous: "Sequence[Any]", latest: "Sequence[Any]",
        key: "Callable[[Any], Any]",
    ) -> tuple:
        """Union two rounds' typed records, keeping each identity once.

        Order is previous-then-new, so the sequence reads as execution history.
        A repeated identity keeps the earlier record: it is the one whose
        evidence the bridge already applied.
        """
        merged = list(previous)
        seen = {key(item) for item in merged}
        for item in latest:
            identity = key(item)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
        return tuple(merged)

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
                # Merge, never replace - see ``execute_bidirectional_checks``.
                result = replace(
                    result,
                    results=self._merge_by(
                        existing.results, result.results,
                        lambda entry: entry.request.target.target_id),
                    errors=tuple(existing.errors) + tuple(result.errors),
                )
                self.specialist_verifications[index] = result
                break
        else:
            self.specialist_verifications.append(result)
        self._charge_calls(result.calls)
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
