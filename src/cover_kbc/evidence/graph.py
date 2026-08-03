"""The Candidate-Facet Evidence Graph (spec Module 3).

The graph's job is to keep *how* a candidate was discovered attached to the
candidate itself.  The invariant that matters:

    direct_run_1, direct_run_2, direct_run_3

are three repetitions of one evidence mechanism.  They land in a single
:class:`~cover_kbc.types.EvidenceGroup` and contribute **one** independent
support, not three.  ``raw_support_count`` stays available separately, because
frequency is still a signal - just not an independence signal.

Numeric candidates are keyed by their formatted value; clustering across near
values happens in the selector, where the 5% tolerance applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

from cover_kbc.contracts.base import RelationContract
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.normalization.strings import preferred_surface_form
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    GenerationRecord,
    IndependenceGroup,
    OutputType,
    Query,
    VerificationResult,
)


@dataclass
class EvidenceGraph:
    """Candidates plus their signed, provenance-carrying evidence edges."""

    query: Query
    contract: RelationContract
    candidates: dict[str, Candidate] = field(default_factory=dict)
    records: dict[str, GenerationRecord] = field(default_factory=dict)
    #: Set when a gate view (death status, public listing) answered NO.
    gate_negative: bool = False
    gate_reason: str | None = None

    # -- construction --------------------------------------------------------

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.candidates.values())

    def __len__(self) -> int:
        return len(self.candidates)

    def register_record(self, record: GenerationRecord) -> None:
        """Store a generation record so every edge can be traced back to it."""
        self.records[record.record_id] = record

    def _candidate_key(self, value: str) -> str:
        return self.contract.key(value)

    def _get_or_create(
        self,
        key: str,
        display: str,
        *,
        numeric_value: float | None = None,
    ) -> Candidate:
        candidate = self.candidates.get(key)
        if candidate is None:
            candidate = Candidate(
                key=key,
                display_value=display,
                relation=self.contract.relation,
                output_type=self.contract.output_type,
                numeric_value=numeric_value,
                unit=self.contract.selection.numeric_target_unit,
            )
            self.candidates[key] = candidate
        return candidate

    # -- ingestion -----------------------------------------------------------

    def add_entity_mentions(
        self, record: GenerationRecord, surfaces: Iterable[str]
    ) -> list[Candidate]:
        """Add supporting evidence from one generation for each mentioned entity.

        Repeated mentions inside a single generation add one edge, not several -
        a model listing "Poland" twice in one answer has not corroborated it.
        """
        self.register_record(record)
        touched: list[Candidate] = []
        seen_in_record: set[str] = set()

        for surface in surfaces:
            key = self._candidate_key(surface)
            if not key:
                continue

            candidate = self._get_or_create(key, surface)
            # Alternative spellings are always recorded - they feed display
            # selection - but a second mention inside one generation adds no
            # evidence, because one answer listing a name twice has not
            # corroborated it.
            candidate.add_surface_form(surface)
            candidate.display_value = preferred_surface_form(candidate.surface_forms)
            if key in seen_in_record:
                continue
            seen_in_record.add(key)

            candidate.add_evidence(
                Evidence(
                    candidate_key=key,
                    edge_type=EdgeType.SUPPORT,
                    independence_group=record.independence_group,
                    view_id=record.view_id,
                    model_id=record.model_id,
                    run_id=record.run_id,
                    record_id=record.record_id,
                    token_cost=record.generated_tokens or 0,
                )
            )
            touched.append(candidate)
        return touched

    def add_numeric_mentions(
        self, record: GenerationRecord, values: Iterable[float]
    ) -> list[Candidate]:
        """Add supporting evidence for each scalar produced by one generation."""
        self.register_record(record)
        integer_only = self.contract.selection.numeric_integer_only
        touched: list[Candidate] = []
        seen_in_record: set[str] = set()

        for value in values:
            key = format_numeric(value, integer_only=integer_only)
            if key in seen_in_record:
                continue
            seen_in_record.add(key)

            candidate = self._get_or_create(key, key, numeric_value=value)
            candidate.add_surface_form(key)
            candidate.add_evidence(
                Evidence(
                    candidate_key=key,
                    edge_type=EdgeType.SUPPORT,
                    independence_group=record.independence_group,
                    view_id=record.view_id,
                    model_id=record.model_id,
                    run_id=record.run_id,
                    record_id=record.record_id,
                    token_cost=record.generated_tokens or 0,
                )
            )
            touched.append(candidate)
        return touched

    def add_verification(self, result: VerificationResult) -> Candidate | None:
        """Attach a blind-verifier verdict as a signed edge.

        Verifier evidence has its own independence group, so a VALID verdict is
        an additional independent support rather than a duplicate of the view
        that discovered the candidate.
        """
        candidate = self.candidates.get(result.candidate_key)
        if candidate is None:
            return None
        candidate.verifications.append(result)
        candidate.add_evidence(
            Evidence(
                candidate_key=result.candidate_key,
                edge_type=result.edge_type,
                independence_group=IndependenceGroup.BLIND_VERIFIER,
                view_id="blind_verifier",
                model_id=result.model_id,
                run_id=0,
                record_id=result.record_id,
                valid_prob=result.valid_prob,
                invalid_prob=result.invalid_prob,
                unknown_prob=result.unknown_prob,
            )
        )
        return candidate

    def reject(self, key: str, reason: str) -> None:
        """Mark a candidate rejected by a deterministic hard contract rule.

        Hard rejection removes type/format impossibilities only.  It must never
        encode a factual lookup (spec section 9.3).
        """
        candidate = self.candidates.get(key)
        if candidate is not None:
            candidate.status = CandidateStatus.REJECTED
            candidate.rejection_reason = reason

    def close_gate(self, reason: str) -> None:
        """Record that an existence gate answered NO for this query."""
        self.gate_negative = True
        self.gate_reason = reason

    # -- queries -------------------------------------------------------------

    def active_candidates(self) -> list[Candidate]:
        """Candidates not hard-rejected, ordered deterministically.

        Sort is by independent support, then raw support, then key - never by
        insertion order, so the output does not depend on view scheduling.
        """
        return sorted(
            (c for c in self.candidates.values() if c.status is not CandidateStatus.REJECTED),
            key=lambda c: (-c.independent_support, -c.raw_support_count, c.key),
        )

    def independence_summary(self) -> dict[str, int]:
        """How many candidates each independence group contributed support to."""
        summary: dict[str, int] = {}
        for candidate in self.candidates.values():
            for group in candidate.supporting_groups:
                summary[group.value] = summary.get(group.value, 0) + 1
        return dict(sorted(summary.items()))

    def coverage_of(self, candidate: Candidate) -> float:
        """``q(o) = g(o) / m(o)`` using the contract's eligible groups."""
        return candidate.coverage(self.contract.eligible_independence_groups)

    def total_generated_tokens(self) -> int:
        return sum(r.generated_tokens or 0 for r in self.records.values())

    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens or 0 for r in self.records.values())

    def to_json(self) -> dict:
        return {
            "subject": self.query.subject,
            "relation": self.query.relation,
            "gate_negative": self.gate_negative,
            "gate_reason": self.gate_reason,
            "independence_summary": self.independence_summary(),
            "candidates": [c.to_json() for c in self.active_candidates()],
        }


def build_graph(query: Query, contract: RelationContract) -> EvidenceGraph:
    """Create an empty graph for one query."""
    return EvidenceGraph(query=query, contract=contract)


def apply_hard_contract_rules(graph: EvidenceGraph) -> None:
    """Reject candidates that violate a type or format rule (spec section 9.3).

    Conservative by design: it removes only impossibilities, never facts.
    """
    contract = graph.contract
    for key, candidate in graph.candidates.items():
        if contract.output_type is OutputType.NUMBER:
            if candidate.numeric_value is None:
                graph.reject(key, "non-numeric output for a numeric relation")
            elif candidate.numeric_value <= 0:
                graph.reject(key, "numeric value must be positive")
        elif not any(ch.isalpha() for ch in candidate.display_value):
            graph.reject(key, "entity candidate contains no letters")
