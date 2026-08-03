"""Fixed-budget COVER pipeline - the Milestone 1 orchestrator.

This is the *non-adaptive* skeleton of Algorithm 1 from the spec: compile the
relation, route to a typed program, run the mandatory views once each, build the
evidence graph, apply hard contract rules, and finalise.

Everything the spec assigns to Milestone 3 is deliberately absent:

* no RCSE residual-coverage estimate;
* no active controller choosing the next action;
* no adaptive stopping (the loop stops when mandatory views are done);
* no DoLa or cross-model views.

Verification is wired but off by default: the blind verifier is uncalibrated
until Milestone 2, so running it would add unjustified confidence rather than
evidence.  The plumbing exists so turning it on is a config change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.router import compile_query
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.elicitation.library import views_for
from cover_kbc.evidence.graph import EvidenceGraph, apply_hard_contract_rules, build_graph
from cover_kbc.models.base import LMRuntime, LogitsUnavailable
from cover_kbc.runtime.tracing import RunTracer
from cover_kbc.selection import finalize
from cover_kbc.types import OutputType, Prediction, Query
from cover_kbc.verification import verify_candidate


@dataclass
class PipelineConfig:
    """Fixed-budget pipeline settings.

    ``run_optional_views`` and ``enable_verifier`` are off by default so the
    Milestone 1 default run is the cheapest honest configuration.
    """

    seed: int = 42
    run_optional_views: bool = False
    enable_verifier: bool = False
    max_verifications_per_query: int = 0
    trace_path: str | None = None


@dataclass
class PipelineResult:
    """Predictions plus the aggregate accounting for one run."""

    predictions: list[Prediction] = field(default_factory=list)
    total_calls: int = 0
    total_generated_tokens: int = 0
    total_prompt_tokens: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)


class CoverPipeline:
    """Runs the fixed-budget COVER programme over a sequence of queries."""

    def __init__(
        self,
        runtime: LMRuntime,
        config: PipelineConfig | None = None,
        *,
        tracer: RunTracer | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config or PipelineConfig()
        self.engine = ElicitationEngine(runtime, seed=self.config.seed)
        self.tracer = tracer

    # -- one query -----------------------------------------------------------

    def run_query(self, query: Query) -> Prediction:
        """Compile, elicit, normalise, graph and finalise a single query."""
        query, contract = compile_query(query.subject, query.relation, query.row_index)
        graph = build_graph(query, contract)

        view_ids = contract.mandatory_views
        if self.config.run_optional_views:
            view_ids = contract.all_views()

        self._elicit(graph, contract, view_ids)
        apply_hard_contract_rules(graph)

        if self.config.enable_verifier and self.config.max_verifications_per_query:
            self._verify(graph, contract)

        stopped = "gate_negative" if graph.gate_negative else "fixed_budget_views_complete"
        return finalize(graph, stopped_reason=stopped)

    def _elicit(
        self, graph: EvidenceGraph, contract: RelationContract, view_ids: Sequence[str]
    ) -> None:
        """Run views once each, feeding discovered entities into missingness views."""
        views = views_for(contract.relation, tuple(view_ids))
        # Gates first (they can end the query), then discovery, then missingness.
        ordered = (
            [v for v in views if v.is_gate]
            + [v for v in views if not v.is_gate and not v.needs_accepted_set]
            + [v for v in views if not v.is_gate and v.needs_accepted_set]
        )

        discovered: list[str] = []
        for view in ordered:
            if graph.gate_negative:
                # An existence gate answered NO; further discovery would only
                # manufacture candidates the contract already excludes.
                break

            outcome = self.engine.run_view(
                graph.query,
                contract,
                view,
                run_id=0,
                accepted=discovered if view.needs_accepted_set else None,
            )
            if self.tracer is not None:
                self.tracer.log_record(outcome.record)

            if outcome.gate is not None:
                graph.register_record(outcome.record)
                if outcome.gate.is_negative:
                    graph.close_gate(f"{view.view_id} answered NO")
                continue

            if contract.output_type is OutputType.NUMBER:
                graph.add_numeric_mentions(outcome.record, outcome.numbers)
            else:
                touched = graph.add_entity_mentions(outcome.record, outcome.entities)
                discovered.extend(c.display_value for c in touched)

    def _verify(self, graph: EvidenceGraph, contract: RelationContract) -> None:
        """Blind-verify the least-supported candidates, budget permitting.

        Verifier output is recorded as evidence only.  Until calibration lands
        it must not be treated as a probability of correctness.
        """
        candidates = sorted(
            graph.active_candidates(),
            key=lambda c: (c.independent_support, c.key),
        )[: self.config.max_verifications_per_query]

        for candidate in candidates:
            try:
                result = verify_candidate(
                    self.runtime, graph.query, contract, candidate.key, candidate.display_value
                )
            except LogitsUnavailable:
                return  # Backend cannot score labels; skip verification entirely.
            graph.add_verification(result)

    # -- many queries --------------------------------------------------------

    def run(self, queries: Iterable[Query], *, progress: bool = False) -> PipelineResult:
        """Run every query in order, collecting predictions and accounting."""
        result = PipelineResult()
        queries = list(queries)

        for index, query in enumerate(queries):
            try:
                prediction = self.run_query(query)
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
                )

            result.predictions.append(prediction)
            result.total_calls += prediction.calls_used
            result.total_generated_tokens += prediction.generated_tokens_used
            result.total_prompt_tokens += prediction.prompt_tokens_used

            if progress and (index + 1) % 50 == 0:
                print(f"  ... {index + 1}/{len(queries)} queries", flush=True)

        return result
