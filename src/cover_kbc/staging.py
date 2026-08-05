"""Staged execution: enumerate, then verify, then decide.

The target architecture pairs a 24B enumerator with a 4.66B verifier - 28.67B
of published parameters. Colab GPUs frequently cannot hold both at once, so the
runtime must not *require* them to be co-resident:

    PHASE A  load enumerator -> discover candidates -> persist graph -> unload
    PHASE B  load verifier   -> verify candidates   -> persist evidence -> unload
    PHASE C  no model at all  -> RCSE, controller, selection -> predictions

The conceptual budget stays 28.67B whether the models are co-resident or
sequential: the challenge counts published parameters used at inference, not
peak VRAM. Nothing here reduces the counted size.

Phase C is entirely non-neural, so it is cheap to re-run with different
thresholds against one expensive set of generations - which is what makes
threshold calibration affordable without re-running the models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from cover_kbc.contracts.registry import get_contract
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.verification import GateResult
from cover_kbc.types import (
    Candidate,
    CandidateScore,
    CandidateStatus,
    DecodeProfile,
    EdgeType,
    Evidence,
    EvidenceMode,
    GenerationRecord,
    IndependenceGroup,
    ModelRole,
    OutputType,
    Query,
    VerificationLabel,
    VerificationResult,
    VerificationTier,
    ViewFamily,
)

STAGE_FILE_VERSION = 6


class StageError(RuntimeError):
    """Raised when a persisted stage file cannot be used."""


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _record_to_json(record: GenerationRecord, *, keep_prompt: bool) -> dict[str, Any]:
    payload = record.to_json()
    if not keep_prompt:
        payload.pop("prompt", None)
    return payload


def _record_from_json(payload: dict[str, Any]) -> GenerationRecord:
    query = Query(payload["query"]["subject"], payload["query"]["relation"])
    return GenerationRecord(
        record_id=payload["record_id"],
        query=query,
        view_id=payload["view_id"],
        view_family=ViewFamily(payload["view_family"]),
        independence_group=IndependenceGroup(payload["independence_group"]),
        run_id=payload.get("run_id", 0),
        model_id=payload.get("model_id", ""),
        prompt=payload.get("prompt", ""),
        prompt_hash=payload.get("prompt_hash", ""),
        raw_output=payload.get("raw_output", ""),
        decode_profile=DecodeProfile(**payload.get("decode_profile", {})),
        facet_id=payload.get("facet_id", ""),
        model_family=payload.get("model_family", ""),
        model_role=ModelRole(payload.get("model_role", ModelRole.ENUMERATOR.value)),
        stage=payload.get("stage", ""),
        source_record_id=payload.get("source_record_id", ""),
        source_candidate_key=payload.get("source_candidate_key", ""),
        parsed_values=list(payload.get("parsed_values", [])),
        prompt_tokens=payload.get("prompt_tokens"),
        generated_tokens=payload.get("generated_tokens"),
        latency_ms=payload.get("latency_ms"),
        error=payload.get("error"),
    )


def _evidence_from_json(payload: dict[str, Any]) -> Evidence:
    return Evidence(
        candidate_key=payload["candidate_key"],
        edge_type=EdgeType(payload["edge_type"]),
        independence_group=IndependenceGroup(payload["independence_group"]),
        view_id=payload.get("view_id", ""),
        model_id=payload.get("model_id", ""),
        run_id=payload.get("run_id", 0),
        record_id=payload.get("record_id", ""),
        edge_id=payload.get("edge_id", ""),
        model_family=payload.get("model_family", ""),
        mode=EvidenceMode(payload.get("mode", EvidenceMode.INDEPENDENT_RECALL.value)),
        valid_prob=payload.get("valid_prob"),
        invalid_prob=payload.get("invalid_prob"),
        unknown_prob=payload.get("unknown_prob"),
        token_cost=payload.get("token_cost", 0),
    )


def _verification_from_json(payload: dict[str, Any]) -> VerificationResult:
    return VerificationResult(
        candidate_key=payload.get("candidate_key", ""),
        label=VerificationLabel(payload["selected_label"]),
        valid_prob=payload.get("p_valid"),
        invalid_prob=payload.get("p_invalid"),
        unknown_prob=payload.get("p_unknown"),
        raw_logits=payload.get("raw_label_logits"),
        calibrated_logits=payload.get("calibrated_label_logits"),
        bias_logits=payload.get("bias_logits"),
        calibrated=payload.get("calibrated", False),
        margin=payload.get("valid_margin"),
        entropy=payload.get("entropy"),
        prompt_disagreement=payload.get("prompt_disagreement"),
        template_id=payload.get("template_id", ""),
        num_templates=payload.get("num_templates", 1),
        model_id=payload.get("model_id", ""),
        model_family=payload.get("model_family", ""),
    )


def _candidate_from_json(payload: dict[str, Any]) -> Candidate:
    candidate = Candidate(
        key=payload["key"],
        display_value=payload["display_value"],
        relation=payload["relation"],
        output_type=OutputType(payload.get("output_type", OutputType.ENTITY.value)),
        numeric_value=payload.get("numeric_value"),
        unit=payload.get("unit"),
        alias_hint=payload.get("alias_hint", ""),
        raw_text=payload.get("raw_text", ""),
        source_unit=payload.get("source_unit"),
        surface_forms=list(payload.get("surface_forms", [])),
        record_ids=list(payload.get("record_ids", [])),
        status=CandidateStatus(payload.get("status", CandidateStatus.UNRESOLVED.value)),
        score=payload.get("score", 0.0),
        tier=VerificationTier(payload.get("tier", VerificationTier.UNRESOLVED.value)),
        strict_key=payload.get("strict_key", ""),
        facet_ids=list(payload.get("facet_ids", [])),
        rejection_reason=payload.get("rejection_reason"),
    )
    breakdown = payload.get("score_breakdown") or {}
    if breakdown:
        candidate.score_breakdown = CandidateScore(
            support=breakdown.get("F_support", 0.0),
            logit=breakdown.get("L_logit", 0.0),
            cross_model=breakdown.get("X_cross_model", 0.0),
            contradiction=breakdown.get("C_contradiction", 0.0),
            disagreement=breakdown.get("U_disagreement", 0.0),
            weights=dict(breakdown.get("weights", {})),
            total=breakdown.get("total", 0.0),
        )
    for edge in payload.get("evidence", []):
        candidate.add_evidence(_evidence_from_json(edge))

    for verification in payload.get("verifications", []):
        candidate.verifications.append(_verification_from_json(verification))
    return candidate


def graph_to_json(graph: EvidenceGraph, *, keep_prompts: bool = False) -> dict[str, Any]:
    """Serialise one query's graph, losslessly enough to resume from."""
    gate = graph.gate_result
    return {
        "version": STAGE_FILE_VERSION,
        "subject": graph.query.subject,
        "relation": graph.query.relation,
        "row_index": graph.query.row_index,
        "gate_negative": graph.gate_negative,
        "gate_reason": graph.gate_reason,
        "gate_result": gate.to_json() if gate is not None and hasattr(gate, "to_json") else None,
        "controller_log": list(graph.controller_log),
        "rcse_state": dict(graph.rcse_state),
        "pending_action": dict(graph.pending_action),
        "budget_snapshot": dict(graph.budget_snapshot),
        "verification_calls": graph.verification_calls,
        "records": [
            _record_to_json(r, keep_prompt=keep_prompts) for r in graph.records.values()
        ],
        "candidates": [c.to_json() for c in graph.candidates.values()],
    }


def graph_from_json(payload: dict[str, Any]) -> EvidenceGraph:
    """Rebuild a graph from its serialised form."""
    version = payload.get("version")
    if version != STAGE_FILE_VERSION:
        raise StageError(
            f"stage file version {version!r} is not {STAGE_FILE_VERSION}; "
            "re-run the enumeration phase rather than mixing formats"
        )
    query = Query(payload["subject"], payload["relation"], payload.get("row_index", -1))
    graph = EvidenceGraph(query=query, contract=get_contract(payload["relation"]))
    graph.gate_negative = payload.get("gate_negative", False)
    graph.gate_reason = payload.get("gate_reason")
    # The gate read-out is real state, not a diagnostic: without restoring it a
    # confident negative silently became None across a role swap and the gate
    # looked never to have run.
    graph.gate_result = GateResult.from_json(payload.get("gate_result"))
    graph.controller_log = list(payload.get("controller_log", []))
    graph.rcse_state = dict(payload.get("rcse_state", {}))
    graph.pending_action = dict(payload.get("pending_action", {}))
    graph.budget_snapshot = dict(payload.get("budget_snapshot", {}))
    graph.verification_calls = payload.get("verification_calls", 0)

    for record in payload.get("records", []):
        graph.register_record(_record_from_json(record))
    for candidate in payload.get("candidates", []):
        rebuilt = _candidate_from_json(candidate)
        graph.candidates[rebuilt.key] = rebuilt
        for edge in rebuilt.all_evidence():
            edge.edge_id = edge.edge_id or edge.derive_edge_id()
            graph._edge_ids.add(edge.edge_id)
    return graph


# --------------------------------------------------------------------------
# Stage files
# --------------------------------------------------------------------------


@dataclass
class StageWriter:
    """Append graphs to a JSONL stage file, one per query."""

    path: Path
    keep_prompts: bool = False
    count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")

    def write(self, graph: EvidenceGraph) -> None:
        payload = graph_to_json(graph, keep_prompts=self.keep_prompts)
        self._handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "StageWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_stage(path: str | Path) -> Iterator[EvidenceGraph]:
    """Stream graphs back from a stage file, preserving query order."""
    file_path = Path(path)
    if not file_path.is_file():
        raise StageError(f"stage file not found: {file_path}")
    with file_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StageError(f"{file_path.name}:{index}: invalid JSON ({exc})") from exc
            yield graph_from_json(payload)


def write_stage(graphs: Iterable[EvidenceGraph], path: str | Path, *, keep_prompts: bool = False) -> Path:
    """Write a whole phase's graphs in one call."""
    with StageWriter(Path(path), keep_prompts=keep_prompts) as writer:
        for graph in graphs:
            writer.write(graph)
    return Path(path)


def stage_summary(path: str | Path) -> dict[str, Any]:
    """Cheap integrity summary of a stage file, for the Colab notebook."""
    relations: dict[str, int] = {}
    candidates = 0
    verified = 0
    graphs = 0
    for graph in read_stage(path):
        graphs += 1
        relations[graph.query.relation] = relations.get(graph.query.relation, 0) + 1
        candidates += len(graph.candidates)
        verified += sum(1 for c in graph.candidates.values() if c.verifications)
    return {
        "path": str(path),
        "graphs": graphs,
        "candidates": candidates,
        "verified_candidates": verified,
        "relations": dict(sorted(relations.items())),
    }


def check_stage_matches(path: str | Path, queries: Sequence[Query]) -> None:
    """Verify a stage file covers exactly the expected queries, in order."""
    actual = [(g.query.subject, g.query.relation) for g in read_stage(path)]
    expected = [(q.subject, q.relation) for q in queries]
    if actual != expected:
        missing = set(expected) - set(actual)
        extra = set(actual) - set(expected)
        detail = []
        if missing:
            detail.append(f"{len(missing)} missing")
        if extra:
            detail.append(f"{len(extra)} unexpected")
        if not detail:
            detail.append("same queries in a different order")
        raise StageError(f"stage file does not match the requested split: {', '.join(detail)}")
