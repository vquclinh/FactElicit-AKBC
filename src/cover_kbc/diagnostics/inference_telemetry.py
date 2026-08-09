"""What one query's inference actually did - recorded **without any gold**.

This is one half of the V3A gold boundary, and the half that runs *inside* a
run. It reads a finished ``EvidenceGraph`` and its ``Prediction`` and writes
down where every candidate got to. It never reads a label, never imports the
gold reader, never touches ``ObjectEntities`` from a dataset row, and has no
parameter through which one could be supplied. Correctness is decided later,
offline, by :mod:`cover_kbc.diagnostics.gold_attribution` - which is a separate
module for exactly this reason.

The three properties that make it safe to run on any split, blind included:

1. **It runs after the fact.** ``observe`` is called once per query, after
   ``decide_graph`` has already returned a finished ``Prediction``. There is no
   point at which a decision is still open.
2. **It only reads.** Every field below is a primitive or a tuple of
   primitives, copied out of state the run already produced. Nothing is written
   back to the graph, the ledger, the budget or the prediction.
3. **It carries no labels.** The record shape has no gold field. A blind TEST
   run can produce this telemetry safely, because there is nothing in it that a
   blind run could not honestly fill in.

Payload discipline: raw model text is **not** duplicated here. The tracer
already writes every call to ``calls.jsonl``; this stores ``record_id``
references and the deterministically derived fragment list, so the diagnostic
artifact stays a fraction of the trace's size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from cover_kbc.contracts.relation_profile import get_relation_profile
from cover_kbc.diagnostics.stages import PipelineStage
from cover_kbc.elicitation.parsing import acquisition_fragments
from cover_kbc.types import CandidateStatus, VerificationLabel

#: Bumped when the record shape changes in a way an analysis must notice.
TELEMETRY_VERSION = "v3a-inference-telemetry-v1"


def _latest_label(candidate: Any) -> str:
    """The verdict ``scoring.decide_status`` reads: the last one with a probability.

    Not "any VALID" and not "the strongest". ``decide_status`` takes
    ``verifications[-1]`` among those carrying a probability, so any other
    reading here would describe a different system from the one that ran.
    """
    scored = [v for v in candidate.verifications if v.valid_prob is not None]
    if not scored:
        return ""
    return scored[-1].label.value


@dataclass(frozen=True)
class GenerationObservation:
    """One neural call's provenance. No prompt, no raw output, no reasoning."""

    record_id: str
    view_id: str
    view_family: str
    independence_group: str
    model_id: str
    model_role: str
    model_family: str
    run_id: int
    stage: str
    facet_id: str
    prompt_tokens: int
    generated_tokens: int
    #: Everything the model offered before rejecting normalisation, derived
    #: deterministically from the raw output. The raw output itself stays in
    #: ``calls.jsonl``, keyed by ``record_id``.
    acquired_fragments: tuple[str, ...]
    #: What the production parser kept - the other side of the same boundary.
    parsed_values: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id, "view_id": self.view_id,
            "view_family": self.view_family,
            "independence_group": self.independence_group,
            "model_id": self.model_id, "model_role": self.model_role,
            "model_family": self.model_family, "run_id": self.run_id,
            "stage": self.stage, "facet_id": self.facet_id,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "acquired_fragments": list(self.acquired_fragments),
            "parsed_values": list(self.parsed_values),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "GenerationObservation":
        return cls(
            record_id=str(payload.get("record_id", "")),
            view_id=str(payload.get("view_id", "")),
            view_family=str(payload.get("view_family", "")),
            independence_group=str(payload.get("independence_group", "")),
            model_id=str(payload.get("model_id", "")),
            model_role=str(payload.get("model_role", "")),
            model_family=str(payload.get("model_family", "")),
            run_id=int(payload.get("run_id", 0)),
            stage=str(payload.get("stage", "")),
            facet_id=str(payload.get("facet_id", "")),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            acquired_fragments=tuple(payload.get("acquired_fragments") or ()),
            parsed_values=tuple(payload.get("parsed_values") or ()),
        )


@dataclass(frozen=True)
class CandidateObservation:
    """One graph candidate and the furthest stage it reached."""

    candidate_key: str
    display_value: str
    #: What Module 8 would emit for it - the derived cluster representative for
    #: a winning numeric, otherwise the display form. Kept apart from
    #: ``display_value`` because a median need never have been generated.
    output_value: str
    numeric_value: float | None
    surface_forms: tuple[str, ...]
    acquisition_groups: tuple[str, ...]
    independent_support: int
    raw_support_count: int
    facet_ids: tuple[str, ...]
    record_ids: tuple[str, ...]
    #: §9.3's type/format rejection, which happens at ingestion. Distinct from
    #: ``final_status == "REJECTED"``, which is the acceptance policy's verdict.
    hard_rejected: bool
    rejection_reason: str
    verification_count: int
    #: "" when no verifier call was ever spent on this candidate.
    verifier_label: str
    verifier_valid_prob: float | None
    final_status: str
    tier: str
    score: float
    emitted: bool

    @property
    def reached_verifier(self) -> bool:
        return self.verification_count > 0

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "display_value": self.display_value,
            "output_value": self.output_value,
            "numeric_value": self.numeric_value,
            "surface_forms": list(self.surface_forms),
            "acquisition_groups": list(self.acquisition_groups),
            "independent_support": self.independent_support,
            "raw_support_count": self.raw_support_count,
            "facet_ids": list(self.facet_ids),
            "record_ids": list(self.record_ids),
            "hard_rejected": self.hard_rejected,
            "rejection_reason": self.rejection_reason,
            "verification_count": self.verification_count,
            "verifier_label": self.verifier_label,
            "verifier_valid_prob": self.verifier_valid_prob,
            "final_status": self.final_status,
            "tier": self.tier, "score": self.score, "emitted": self.emitted,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CandidateObservation":
        probability = payload.get("verifier_valid_prob")
        numeric = payload.get("numeric_value")
        return cls(
            candidate_key=str(payload.get("candidate_key", "")),
            display_value=str(payload.get("display_value", "")),
            output_value=str(payload.get("output_value", "")),
            numeric_value=None if numeric is None else float(numeric),
            surface_forms=tuple(payload.get("surface_forms") or ()),
            acquisition_groups=tuple(payload.get("acquisition_groups") or ()),
            independent_support=int(payload.get("independent_support", 0)),
            raw_support_count=int(payload.get("raw_support_count", 0)),
            facet_ids=tuple(payload.get("facet_ids") or ()),
            record_ids=tuple(payload.get("record_ids") or ()),
            hard_rejected=bool(payload.get("hard_rejected", False)),
            rejection_reason=str(payload.get("rejection_reason") or ""),
            verification_count=int(payload.get("verification_count", 0)),
            verifier_label=str(payload.get("verifier_label") or ""),
            verifier_valid_prob=(None if probability is None
                                 else float(probability)),
            final_status=str(payload.get("final_status", "")),
            tier=str(payload.get("tier", "")),
            score=float(payload.get("score", 0.0)),
            emitted=bool(payload.get("emitted", False)),
        )


@dataclass(frozen=True)
class ActionProvenance:
    """One Layer-4 control action, as identity and outcome - never as content."""

    kind: str
    round_index: int
    action_id: str
    family: str
    owner: str
    target: str
    facet_id: str
    model_role: str
    executed: bool
    admitted: bool
    legal_not_selected: bool
    refusal: str
    physical_calls: int
    generated_tokens: int

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "round_index": self.round_index,
            "action_id": self.action_id, "family": self.family,
            "owner": self.owner, "target": self.target,
            "facet_id": self.facet_id, "model_role": self.model_role,
            "executed": self.executed, "admitted": self.admitted,
            "legal_not_selected": self.legal_not_selected,
            "refusal": self.refusal, "physical_calls": self.physical_calls,
            "generated_tokens": self.generated_tokens,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ActionProvenance":
        return cls(
            kind=str(payload.get("kind", "")),
            round_index=int(payload.get("round_index", 0)),
            action_id=str(payload.get("action_id", "")),
            family=str(payload.get("family", "")),
            owner=str(payload.get("owner", "")),
            target=str(payload.get("target", "")),
            facet_id=str(payload.get("facet_id", "")),
            model_role=str(payload.get("model_role", "")),
            executed=bool(payload.get("executed", False)),
            admitted=bool(payload.get("admitted", False)),
            legal_not_selected=bool(payload.get("legal_not_selected", False)),
            refusal=str(payload.get("refusal") or ""),
            physical_calls=int(payload.get("physical_calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
        )


@dataclass(frozen=True)
class QueryInferenceRecord:
    """One query's inference, at every stage. Contains no gold and no label.

    ``stage_values`` is the load-bearing field: for each
    :class:`~cover_kbc.diagnostics.stages.PipelineStage`, the exact strings that
    were present at that stage, in a form the official evaluator can score. The
    attribution layer needs nothing else from a run.
    """

    telemetry_version: str
    run_id: str
    split: str
    subject: str
    relation: str
    row_index: int
    program_type: str
    relation_profile: Mapping[str, Any]
    gate_negative: bool
    gate_reason: str
    empty_reason: str
    stopped_reason: str
    calls_used: int
    generated_tokens_used: int
    prompt_tokens_used: int
    verification_calls: int
    #: Stage name -> the values present at that stage, in emission-relevant form.
    stage_values: Mapping[str, tuple[str, ...]]
    candidates: tuple[CandidateObservation, ...]
    generations: tuple[GenerationObservation, ...]
    actions: tuple[ActionProvenance, ...]
    #: §8's typed search state. Shadow: recorded, never acted on.
    failure_state: str = ""
    #: Set only when the query produced no finished graph at all, e.g. a
    #: PIPELINE_ERROR row. Attribution reports NOT_OBSERVABLE rather than
    #: inferring loss from an absence it cannot explain.
    observable: bool = True

    def values_at(self, stage: PipelineStage) -> tuple[str, ...]:
        return tuple(self.stage_values.get(stage.value, ()))

    @property
    def emitted(self) -> tuple[str, ...]:
        return self.values_at(PipelineStage.FINAL_EMITTED)

    def to_json(self) -> dict[str, Any]:
        return {
            "telemetry_version": self.telemetry_version,
            "run_id": self.run_id, "split": self.split,
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "program_type": self.program_type,
            "relation_profile": dict(self.relation_profile),
            "gate_negative": self.gate_negative,
            "gate_reason": self.gate_reason,
            "empty_reason": self.empty_reason,
            "stopped_reason": self.stopped_reason,
            "calls_used": self.calls_used,
            "generated_tokens_used": self.generated_tokens_used,
            "prompt_tokens_used": self.prompt_tokens_used,
            "verification_calls": self.verification_calls,
            "stage_values": {k: list(v) for k, v in self.stage_values.items()},
            "candidates": [c.to_json() for c in self.candidates],
            "generations": [g.to_json() for g in self.generations],
            "actions": [a.to_json() for a in self.actions],
            "failure_state": self.failure_state,
            "observable": self.observable,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "QueryInferenceRecord":
        stages = payload.get("stage_values") or {}
        return cls(
            telemetry_version=str(payload.get("telemetry_version", "")),
            run_id=str(payload.get("run_id", "")),
            split=str(payload.get("split", "")),
            subject=str(payload.get("SubjectEntity", "")),
            relation=str(payload.get("Relation", "")),
            row_index=int(payload.get("row_index", -1)),
            program_type=str(payload.get("program_type", "")),
            relation_profile=dict(payload.get("relation_profile") or {}),
            gate_negative=bool(payload.get("gate_negative", False)),
            gate_reason=str(payload.get("gate_reason") or ""),
            empty_reason=str(payload.get("empty_reason", "")),
            stopped_reason=str(payload.get("stopped_reason") or ""),
            calls_used=int(payload.get("calls_used", 0)),
            generated_tokens_used=int(payload.get("generated_tokens_used", 0)),
            prompt_tokens_used=int(payload.get("prompt_tokens_used", 0)),
            verification_calls=int(payload.get("verification_calls", 0)),
            stage_values={str(k): tuple(v or ()) for k, v in stages.items()},
            candidates=tuple(CandidateObservation.from_json(c)
                             for c in payload.get("candidates") or ()),
            generations=tuple(GenerationObservation.from_json(g)
                              for g in payload.get("generations") or ()),
            actions=tuple(ActionProvenance.from_json(a)
                          for a in payload.get("actions") or ()),
            failure_state=str(payload.get("failure_state") or ""),
            observable=bool(payload.get("observable", True)),
        )


def _unique(values: "Sequence[str]") -> tuple[str, ...]:
    """Order-preserving deduplication. Order is the caller's priority signal."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


class DiagnosticRecorder:
    """Observes finished queries. **Reads only; changes nothing.**

    Held by the pipeline as an optional collaborator and consulted at exactly
    one place - after ``decide_graph`` has returned. With no recorder attached
    the pipeline runs the pre-V3A code path unchanged; with one attached it runs
    the same path and then copies numbers out of it. That is what makes the
    zero-prediction-change invariant structural rather than tested-into-place.
    """

    def __init__(self, *, run_id: str = "", split: str = "") -> None:
        self.run_id = run_id
        self.split = split
        self.records: list[QueryInferenceRecord] = []

    # -- the one entry point -------------------------------------------------

    def observe(
        self, graph: Any, prediction: Any, *,
        action_records: "Sequence[Mapping[str, Any]] | None" = None,
    ) -> QueryInferenceRecord:
        """Snapshot one finished query and keep the record.

        Args:
            graph: the query's finished ``EvidenceGraph``, or ``None`` when the
                query failed before producing one.
            prediction: the ``Prediction`` ``decide_graph`` returned.
            action_records: this query's slice of ``CoverPipeline.action_records``.
                Optional: absent provenance is recorded as absent, never guessed.
        """
        record = (self._unobservable(prediction) if graph is None
                  else self._observe_graph(graph, prediction, action_records))
        self.records.append(record)
        return record

    # -- construction --------------------------------------------------------

    def _unobservable(self, prediction: Any) -> QueryInferenceRecord:
        """A query with no graph. Every stage is unknown, and says so."""
        return QueryInferenceRecord(
            telemetry_version=TELEMETRY_VERSION, run_id=self.run_id,
            split=self.split, subject=prediction.subject,
            relation=prediction.relation, row_index=prediction.row_index,
            program_type="", relation_profile=_profile_json(prediction.relation),
            gate_negative=False, gate_reason="",
            empty_reason=prediction.empty_reason.value,
            stopped_reason=prediction.stopped_reason or "",
            calls_used=prediction.calls_used,
            generated_tokens_used=prediction.generated_tokens_used,
            prompt_tokens_used=prediction.prompt_tokens_used,
            verification_calls=prediction.verification_calls,
            stage_values={}, candidates=(), generations=(), actions=(),
            failure_state="", observable=False,
        )

    def _observe_graph(
        self, graph: Any, prediction: Any,
        action_records: "Sequence[Mapping[str, Any]] | None",
    ) -> QueryInferenceRecord:
        contract = graph.contract
        generations = tuple(
            _generation(record, contract)
            for record in sorted(graph.records.values(),
                                 key=lambda r: r.record_id)
        )
        emitted = tuple(prediction.object_entities)
        emitted_keys = {c.key for c in prediction.candidates
                        if c.output_value in emitted}
        candidates = tuple(
            _candidate(candidate, emitted_keys)
            for candidate in sorted(graph.candidates.values(),
                                    key=lambda c: c.key)
        )

        stage_values = {
            PipelineStage.ACQUIRED.value: _unique(
                [fragment for generation in generations
                 for fragment in generation.acquired_fragments]),
            PipelineStage.NORMALIZED.value: _unique(
                [c.display_value for c in candidates if not c.hard_rejected]),
            PipelineStage.VERIFIER_REACHED.value: _unique(
                [c.display_value for c in candidates if c.reached_verifier]),
            PipelineStage.VERIFIER_ACCEPTED.value: _unique(
                [c.display_value for c in candidates
                 if c.verifier_label == VerificationLabel.VALID.value]),
            PipelineStage.CONTROL_SURVIVED.value: _unique(
                [c.display_value for c in candidates
                 if c.final_status == CandidateStatus.ACCEPTED.value]),
            PipelineStage.FINAL_EMITTED.value: tuple(emitted),
        }

        from cover_kbc.diagnostics.failure_state import derive_failure_state

        record = QueryInferenceRecord(
            telemetry_version=TELEMETRY_VERSION, run_id=self.run_id,
            split=self.split, subject=graph.query.subject,
            relation=graph.query.relation, row_index=graph.query.row_index,
            program_type=str(getattr(contract.program_type, "value",
                                     contract.program_type)),
            relation_profile=_profile_json(graph.query.relation),
            gate_negative=bool(graph.gate_negative),
            gate_reason=graph.gate_reason or "",
            empty_reason=prediction.empty_reason.value,
            stopped_reason=prediction.stopped_reason or "",
            calls_used=prediction.calls_used,
            generated_tokens_used=prediction.generated_tokens_used,
            prompt_tokens_used=prediction.prompt_tokens_used,
            verification_calls=prediction.verification_calls,
            stage_values=stage_values, candidates=candidates,
            generations=generations,
            actions=tuple(_action(entry) for entry in action_records or ()),
        )
        # Derived last, because it reads the finished record. Recorded on the
        # record; consulted by nothing in this process.
        from dataclasses import replace

        return replace(
            record,
            failure_state=derive_failure_state(
                record, get_relation_profile(graph.query.relation)).value)


def _profile_json(relation: str) -> dict[str, Any]:
    """The relation's profile, or an empty mapping for an unprofiled relation.

    Diagnostics must not be the thing that stops a run. The profile itself fails
    closed (:func:`get_relation_profile`); recording it does not.
    """
    try:
        return get_relation_profile(relation).to_json()
    except KeyError:
        return {}


def _generation(record: Any, contract: Any) -> GenerationObservation:
    return GenerationObservation(
        record_id=record.record_id,
        view_id=record.view_id,
        view_family=str(getattr(record.view_family, "value", record.view_family)),
        independence_group=str(getattr(record.independence_group, "value",
                                       record.independence_group)),
        model_id=record.model_id,
        model_role=str(getattr(record.model_role, "value", record.model_role)),
        model_family=record.model_family,
        run_id=int(record.run_id),
        stage=record.stage,
        facet_id=record.facet_id,
        prompt_tokens=int(record.prompt_tokens or 0),
        generated_tokens=int(record.generated_tokens or 0),
        acquired_fragments=tuple(
            acquisition_fragments(record.raw_output or "", contract)),
        parsed_values=tuple(record.parsed_values or ()),
    )


def _candidate(candidate: Any, emitted_keys: "set[str]") -> CandidateObservation:
    scored = [v for v in candidate.verifications if v.valid_prob is not None]
    return CandidateObservation(
        candidate_key=candidate.key,
        display_value=candidate.display_value,
        output_value=candidate.output_value,
        numeric_value=candidate.numeric_value,
        surface_forms=tuple(candidate.surface_forms),
        acquisition_groups=tuple(g.value for g in candidate.supporting_groups),
        independent_support=candidate.independent_support,
        raw_support_count=candidate.raw_support_count,
        facet_ids=tuple(candidate.facet_ids),
        record_ids=tuple(candidate.record_ids),
        # A hard-contract rejection carries a reason and happens at ingestion;
        # the acceptance policy's REJECTED never sets one. That is the only
        # thing that distinguishes the two after the fact.
        hard_rejected=bool(candidate.rejection_reason),
        rejection_reason=candidate.rejection_reason or "",
        verification_count=len(candidate.verifications),
        verifier_label=_latest_label(candidate),
        verifier_valid_prob=(scored[-1].valid_prob if scored else None),
        final_status=candidate.status.value,
        tier=candidate.tier.value,
        score=float(candidate.score),
        emitted=candidate.key in emitted_keys,
    )


def _action(entry: "Mapping[str, Any]") -> ActionProvenance:
    projection = entry.get("projection")
    cost = entry.get("cost") or {}
    return ActionProvenance(
        kind=str(entry.get("kind", "")),
        round_index=int(entry.get("round_index", 0)),
        action_id=str(getattr(projection, "action_id", "") or ""),
        family=str(getattr(getattr(projection, "family", None), "value", "")),
        owner=str(getattr(getattr(projection, "owner", None), "value", "")),
        target=str(getattr(projection, "target", "") or ""),
        facet_id=str(getattr(projection, "facet_id", "") or ""),
        model_role=str(getattr(projection, "model_role", "") or ""),
        executed=bool(entry.get("executed", False)),
        admitted=bool(entry.get("admitted", False)),
        legal_not_selected=bool(entry.get("legal_not_selected", False)),
        refusal=str(entry.get("refusal") or ""),
        physical_calls=int(cost.get("physical_calls", 0) or 0),
        generated_tokens=int(cost.get("generated_tokens", 0) or 0),
    )


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


@dataclass
class InferenceTelemetryWriter:
    """Append-only JSONL, one :class:`QueryInferenceRecord` per line."""

    path: Path
    written: int = field(default=0, init=False)

    def write_all(self, records: "Sequence[QueryInferenceRecord]") -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(
                    json.dumps(record.to_json(), ensure_ascii=False) + "\n")
                self.written += 1
        return self.path


def read_inference_telemetry(
    path: "str | Path",
) -> Iterator[QueryInferenceRecord]:
    """Stream a persisted telemetry file back into records."""
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{source}:{number}: not valid JSON ({error})") from None
            yield QueryInferenceRecord.from_json(payload)


__all__ = [
    "ActionProvenance",
    "CandidateObservation",
    "DiagnosticRecorder",
    "GenerationObservation",
    "InferenceTelemetryWriter",
    "QueryInferenceRecord",
    "TELEMETRY_VERSION",
    "read_inference_telemetry",
]
