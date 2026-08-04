"""The Diverse Elicitation Engine (spec Module 2).

The engine is a *candidate generator*, not the final answer generator.  It is
allowed to be noisy: high-recall discovery is separated from high-precision
verification.

Every call produces a :class:`GenerationRecord` carrying the full provenance
required by the spec - view, independence group, run id, model, prompt hash,
raw output, parsed values and cost.  No downstream module consumes a candidate
without one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from cover_kbc.contracts.base import RelationContract
from cover_kbc.elicitation.library import views_for
from cover_kbc.elicitation.parsing import (
    GateVerdict,
    NumericObservation,
    parse_entities,
    parse_gate,
    parse_numeric_observations,
)
from cover_kbc.elicitation.views import ViewSpec
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.types import GenerationRecord, IndependenceGroup, ModelRole, OutputType, Query


def prompt_hash(prompt: str) -> str:
    """Short stable hash of a rendered prompt, recorded for reproducibility."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


@dataclass
class ViewOutcome:
    """What one view call produced."""

    record: GenerationRecord
    entities: list[str]
    numbers: list[float]
    gate: GateVerdict | None = None
    #: The same scalars with their source numeral and unit preserved, which is
    #: what Module 3 stores. ``numbers`` stays as the normalised values.
    observations: list[NumericObservation] = field(default_factory=list)


class ElicitationEngine:
    """Runs contract-declared views against a model runtime."""

    def __init__(self, runtime: LMRuntime, *, seed: int = 42) -> None:
        self.runtime = runtime
        self.seed = seed

    def _record_id(self, query: Query, view: ViewSpec, run_id: int, stage: str = "") -> str:
        raw = f"{query.subject}|{query.relation}|{view.view_id}|{run_id}|{stage}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _role(self) -> ModelRole:
        role = self.runtime.spec.role
        return ModelRole(role) if role in {r.value for r in ModelRole} else ModelRole.ENUMERATOR

    def run_view(
        self,
        query: Query,
        contract: RelationContract,
        view: ViewSpec,
        *,
        run_id: int = 0,
        accepted: list[str] | None = None,
        independence_group: "IndependenceGroup | None" = None,
        context: str = "",
        candidate: str = "",
        stage: str = "",
        source_record_id: str = "",
    ) -> ViewOutcome:
        """Execute one view once and parse its output.

        ``independence_group`` overrides the view's default group. It exists so
        the *same* discovery prompt run on the second model family is recorded
        as CROSS_MODEL_RECALL - independent recall by another model - rather
        than being merged into the enumerator's own evidence family.

        A backend failure is captured on the record rather than raised, so one
        bad query cannot abort a full evaluation run.
        """
        prompt = view.render(
            subject=query.subject,
            definition=contract.verifier_definition(),
            accepted=accepted,
            context=context,
            candidate=candidate,
        )
        decode = view.decode
        if not decode.deterministic and decode.seed is None:
            # Sampled views get a run-derived seed so a repeat run reproduces.
            decode = replace(decode, seed=self.seed + run_id)

        request = GenerationRequest(
            prompt=prompt,
            decode=decode,
            system_prompt=view.system_prompt,
            metadata={
                "view_id": view.view_id,
                "subject": query.subject,
                "relation": query.relation,
                "run_id": run_id,
                "stage": stage,
                "candidate": candidate,
            },
        )

        error: str | None = None
        try:
            result = self.runtime.generate(request)
            raw_output = result.text
        except Exception as exc:  # noqa: BLE001 - a single query must not kill the run
            error = f"{type(exc).__name__}: {exc}"
            raw_output = ""
            result = None

        entities: list[str] = []
        numbers: list[float] = []
        observations: list[NumericObservation] = []
        gate: GateVerdict | None = None

        if view.is_gate:
            gate = parse_gate(raw_output)
        elif contract.output_type is OutputType.NUMBER:
            observations = parse_numeric_observations(raw_output, contract)
            numbers = [o.value for o in observations]
        else:
            entities = parse_entities(raw_output, contract)

        record = GenerationRecord(
            record_id=self._record_id(query, view, run_id, stage),
            query=query,
            view_id=view.view_id,
            view_family=view.family,
            independence_group=independence_group or view.independence_group,
            run_id=run_id,
            model_id=self.runtime.spec.model_id,
            model_family=self.runtime.spec.family,
            model_role=self._role(),
            facet_id=view.facet,
            stage=stage,
            source_record_id=source_record_id,
            source_candidate_key=candidate,
            prompt=prompt,
            prompt_hash=prompt_hash(prompt),
            raw_output=raw_output,
            decode_profile=decode,
            parsed_values=entities or [str(n) for n in numbers],
            prompt_tokens=result.prompt_tokens if result else None,
            generated_tokens=result.generated_tokens if result else None,
            latency_ms=result.latency_ms if result else None,
            error=error,
        )
        return ViewOutcome(
            record=record, entities=entities, numbers=numbers, gate=gate,
            observations=observations,
        )

    def run_description_view(
        self,
        query: Query,
        contract: RelationContract,
        view: ViewSpec,
        *,
        run_id: int = 0,
    ) -> tuple[ViewOutcome, ViewOutcome]:
        """Relation-focused description: generate context, then extract from it.

        Two model calls, **one** acquisition mechanism. Returns
        ``(description, extraction)``.

        The description stage is prose. It is recorded for traceability and it
        yields no candidates and no evidence edge - a self-generated context is
        an intermediate memory-elicitation artifact, never proof. Only the
        extraction stage produces candidate mentions, and its record points back
        at the description that produced them via ``source_record_id``.
        """
        if not view.is_description:
            raise ValueError(f"{view.view_id} is not a two-stage description view")

        prompt = view.render_description(
            subject=query.subject, definition=contract.verifier_definition()
        )
        error: str | None = None
        try:
            result = self.runtime.generate(
                GenerationRequest(
                    prompt=prompt,
                    decode=view.decode,
                    system_prompt=view.system_prompt,
                    metadata={
                        "view_id": view.view_id,
                        "subject": query.subject,
                        "relation": query.relation,
                        "run_id": run_id,
                        "stage": "description",
                    },
                )
            )
            context = result.text
        except Exception as exc:  # noqa: BLE001 - one query must not kill the run
            error = f"{type(exc).__name__}: {exc}"
            context = ""
            result = None

        description_record = GenerationRecord(
            record_id=self._record_id(query, view, run_id, "description"),
            query=query,
            view_id=view.view_id,
            view_family=view.family,
            independence_group=view.independence_group,
            run_id=run_id,
            model_id=self.runtime.spec.model_id,
            model_family=self.runtime.spec.family,
            model_role=self._role(),
            facet_id=view.facet,
            stage="description",
            prompt=prompt,
            prompt_hash=prompt_hash(prompt),
            raw_output=context,
            decode_profile=view.decode,
            # Deliberately empty: prose is not a candidate.
            parsed_values=[],
            prompt_tokens=result.prompt_tokens if result else None,
            generated_tokens=result.generated_tokens if result else None,
            latency_ms=result.latency_ms if result else None,
            error=error,
        )
        description = ViewOutcome(record=description_record, entities=[], numbers=[])

        extraction = self.run_view(
            query,
            contract,
            view,
            run_id=run_id,
            context=context,
            stage="extraction",
            source_record_id=description_record.record_id,
        )
        return description, extraction

    def run_reverse_view(
        self,
        query: Query,
        contract: RelationContract,
        view: ViewSpec,
        candidate: str,
        *,
        run_id: int = 0,
    ) -> ViewOutcome:
        """Reverse / alternate framing, conditioned on one candidate.

        Asks the relation the other way round - "does <candidate> hold for
        <subject>?" - and parses the free-text answer with the ordinary parser.

        This is **not** the blind verifier: no fixed A/B/C label set, no logit
        scoring, no calibration. It is an acquisition mechanism that happens to
        take a candidate as input, and its output is a candidate mention like
        any other. Module 4 owns verification.
        """
        if not view.is_reverse:
            raise ValueError(f"{view.view_id} is not a candidate-conditioned view")
        if not candidate:
            raise ValueError(f"{view.view_id}: a reverse view needs a candidate to ask about")
        return self.run_view(
            query, contract, view, run_id=run_id, candidate=candidate, stage="reverse"
        )

    def run_view_repeats(
        self,
        query: Query,
        contract: RelationContract,
        view: ViewSpec,
        *,
        accepted: list[str] | None = None,
        independence_group: "IndependenceGroup | None" = None,
    ) -> list[ViewOutcome]:
        """Execute one view ``view.runs`` times, as run 0, 1, ... n-1.

        Every repeat carries the same ``view_id`` and independence group, so
        Module 3 sees one mechanism sampled n times rather than n mechanisms.
        Repetition amplifies evidence; it never manufactures independence.
        """
        return [
            self.run_view(
                query,
                contract,
                view,
                run_id=run_id,
                accepted=accepted,
                independence_group=independence_group,
            )
            for run_id in range(max(1, view.runs))
        ]

    def run_views(
        self,
        query: Query,
        contract: RelationContract,
        view_ids: tuple[str, ...],
        *,
        accepted: list[str] | None = None,
    ) -> list[ViewOutcome]:
        """Execute an ordered list of views once each."""
        return [
            self.run_view(query, contract, view, run_id=0, accepted=accepted)
            for view in views_for(contract.relation, view_ids)
        ]

    def run_mandatory_views(
        self, query: Query, contract: RelationContract
    ) -> list[ViewOutcome]:
        """Execute the contract's mandatory initial views (spec section 13.2 step 1).

        Missingness views are executed last and receive the entities discovered
        so far, so "do not repeat" means something.
        """
        outcomes: list[ViewOutcome] = []
        discovered: list[str] = []

        views = views_for(contract.relation, contract.mandatory_views)
        ordered = [v for v in views if not v.needs_accepted_set] + [
            v for v in views if v.needs_accepted_set
        ]

        for view in ordered:
            outcome = self.run_view(
                query, contract, view, run_id=0, accepted=discovered if view.needs_accepted_set else None
            )
            outcomes.append(outcome)
            discovered.extend(outcome.entities)
        return outcomes
