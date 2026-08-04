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
from cover_kbc.elicitation.parsing import NumericObservation
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.normalization.strings import is_abstain, preferred_surface_form
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    EvidenceMode,
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
    #: Set when a gate answered a confident NO (see :meth:`close_gate`).
    gate_negative: bool = False
    gate_reason: str | None = None
    #: Calibrated gate read-out, kept for diagnostics even when not negative.
    gate_result: object | None = None
    #: Controller decision records (Phase A), for the audit trail.
    controller_log: list[dict] = field(default_factory=list)
    #: An action the controller selected but whose model role was not resident
    #: in that phase. Persisted so staged orchestration can execute it after a
    #: role swap, rather than silently dropping it.
    pending_action: dict = field(default_factory=dict)
    #: Module-6 temporal state (Phase A). Persisted because yield and
    #: saturation record *when* and *at what cost* something was found, which
    #: the final graph cannot reconstruct - it only records what was found.
    rcse_state: dict = field(default_factory=dict)
    #: Call/token accounting carried across staged phases.
    budget_snapshot: dict = field(default_factory=dict)
    #: Verifier calls spent on this query (Phase B).
    verification_calls: int = 0
    #: Every edge id seen, so a duplicate insertion fails loudly.
    _edge_ids: set = field(default_factory=set)

    # -- construction --------------------------------------------------------

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.candidates.values())

    def __len__(self) -> int:
        return len(self.candidates)

    def register_record(self, record: GenerationRecord) -> None:
        """Store a generation record so every edge can be traced back to it."""
        self.records[record.record_id] = record

    def _attach(self, candidate: Candidate, evidence: Evidence) -> None:
        """Attach one edge, refusing a duplicate rather than silently merging."""
        evidence.edge_id = evidence.edge_id or evidence.derive_edge_id()
        if evidence.edge_id in self._edge_ids:
            raise ValueError(
                f"duplicate evidence edge {evidence.edge_id} for candidate "
                f"{evidence.candidate_key!r} from record {evidence.record_id!r}"
            )
        self._edge_ids.add(evidence.edge_id)
        candidate.add_evidence(evidence)

    def _candidate_key(self, value: str) -> str:
        """Candidate identity: the evaluator's own normalisation.

        Hard merge happens only where the scorer itself would treat two surface
        forms as one prediction. The looser alias hint is recorded on the
        candidate but never keys the graph - a hint that turns out to be wrong
        would otherwise destroy an entity irrecoverably.
        """
        return self.contract.strict_key(value)

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
            # Defensive: the parser already drops these, but the graph must not
            # rely on that. "NONE"/"UNKNOWN"/"" express absence, not an object.
            if is_abstain(surface):
                continue
            key = self._candidate_key(surface)
            if not key:
                continue

            candidate = self._get_or_create(key, surface)
            if not candidate.strict_key:
                candidate.strict_key = key
            if not candidate.alias_hint:
                candidate.alias_hint = self.contract.key(surface)
            # A facet is a partition *inside* one mechanism, so it is recorded
            # as provenance only - never as extra independent support.
            candidate.add_facet(record.facet_id or record.view_id)
            # Alternative spellings are always recorded - they feed display
            # selection - but a second mention inside one generation adds no
            # evidence, because one answer listing a name twice has not
            # corroborated it.
            candidate.add_surface_form(surface)
            candidate.display_value = preferred_surface_form(candidate.surface_forms)
            if key in seen_in_record:
                continue
            seen_in_record.add(key)

            self._attach(
                candidate,
                Evidence(
                    candidate_key=key,
                    edge_type=EdgeType.SUPPORT,
                    independence_group=record.independence_group,
                    view_id=record.view_id,
                    model_id=record.model_id,
                    run_id=record.run_id,
                    record_id=record.record_id,
                    model_family=record.model_family,
                    mode=EvidenceMode.INDEPENDENT_RECALL,
                    token_cost=record.generated_tokens or 0,
                ),
            )
            touched.append(candidate)
        return touched

    def add_numeric_mentions(
        self,
        record: GenerationRecord,
        values: Iterable[float] | Iterable["NumericObservation"],
    ) -> list[Candidate]:
        """Add supporting evidence for each scalar produced by one generation.

        Accepts bare floats or ``NumericObservation`` values. An observation
        additionally preserves the numeral as the model wrote it and the unit it
        used, so a converted value stays auditable: 2145 sq mi and 5556 km2 both
        normalise to the same candidate but remain distinguishable in the trace.
        """
        self.register_record(record)
        integer_only = self.contract.selection.numeric_integer_only
        touched: list[Candidate] = []
        seen_in_record: set[str] = set()

        for item in values:
            raw_text, source_unit = "", None
            if isinstance(item, NumericObservation):
                value, raw_text, source_unit = item.value, item.raw_text, item.source_unit
            else:
                value = float(item)

            key = format_numeric(value, integer_only=integer_only)
            if key in seen_in_record:
                continue
            seen_in_record.add(key)

            candidate = self._get_or_create(key, key, numeric_value=value)
            candidate.strict_key = candidate.strict_key or key
            candidate.alias_hint = candidate.alias_hint or key
            if raw_text and not candidate.raw_text:
                candidate.raw_text = raw_text
                candidate.source_unit = source_unit
            candidate.add_facet(record.facet_id or record.view_id)
            candidate.add_surface_form(key)
            self._attach(
                candidate,
                Evidence(
                    candidate_key=key,
                    edge_type=EdgeType.SUPPORT,
                    independence_group=record.independence_group,
                    view_id=record.view_id,
                    model_id=record.model_id,
                    run_id=record.run_id,
                    record_id=record.record_id,
                    model_family=record.model_family,
                    mode=EvidenceMode.INDEPENDENT_RECALL,
                    token_cost=record.generated_tokens or 0,
                ),
            )
            touched.append(candidate)
        return touched

    def add_verification(self, result: VerificationResult) -> Candidate | None:
        """Attach a blind-verifier verdict as a signed edge.

        The edge is marked ``SHOWN_CANDIDATE``: the verifier was handed this
        name and asked to judge it. That is weaker than independently recalling
        it, and the scorer treats the two differently.
        """
        candidate = self.candidates.get(result.candidate_key)
        if candidate is None:
            return None
        candidate.verifications.append(result)
        self._attach(
            candidate,
            Evidence(
                candidate_key=result.candidate_key,
                edge_type=result.edge_type,
                independence_group=IndependenceGroup.BLIND_VERIFIER,
                view_id="blind_verifier",
                model_id=result.model_id,
                run_id=0,
                record_id=result.record_id,
                model_family=result.model_family,
                mode=EvidenceMode.SHOWN_CANDIDATE,
                valid_prob=result.valid_prob,
                invalid_prob=result.invalid_prob,
                unknown_prob=result.unknown_prob,
            ),
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
        """Record that an existence gate answered a *confident* NO.

        Only a calibrated, high-margin negative may call this: an uncertain
        gate must let discovery continue rather than force an empty answer.
        """
        self.gate_negative = True
        self.gate_reason = reason

    def alias_groups(self) -> dict[str, list[str]]:
        """Soft grouping of candidate keys that share an alias hint.

        Purely advisory. The graph keeps these as separate nodes because the
        hint is not proof of identity; this exposes the grouping so the output
        writer (and later modules) can avoid submitting two surface forms of
        what is probably one entity, without the graph having merged them.
        """
        groups: dict[str, list[str]] = {}
        for candidate in self.candidates.values():
            groups.setdefault(candidate.alias_hint or candidate.key, []).append(candidate.key)
        return {k: sorted(v) for k, v in sorted(groups.items()) if len(v) > 1}

    def executed_independence_groups(self) -> set[IndependenceGroup]:
        """Groups that actually ran for this query, gates included.

        Module 5 needs this to tell "mechanism declared" from "mechanism
        executed" when it computes a per-run coverage denominator. Module 3 only
        records it; the denominator itself is Module 5's to define.
        """
        return {record.independence_group for record in self.records.values()}

    def candidate_producing_groups(self) -> set[IndependenceGroup]:
        """Groups that actually produced at least one candidate edge."""
        return {
            edge.independence_group
            for candidate in self.candidates.values()
            for edge in candidate.all_evidence()
        }

    def model_family_summary(self) -> dict[str, int]:
        """How many candidates each model family independently recalled."""
        counts: dict[str, int] = {}
        for candidate in self.candidates.values():
            families = {
                e.model_family
                for e in candidate.all_evidence()
                if e.mode is EvidenceMode.INDEPENDENT_RECALL and e.model_family
            }
            for family in families:
                counts[family] = counts.get(family, 0) + 1
        return dict(sorted(counts.items()))

    def facet_summary(self) -> dict[str, int]:
        """How many candidates each facet contributed, for diagnostics."""
        counts: dict[str, int] = {}
        for candidate in self.candidates.values():
            for facet in candidate.facet_ids:
                counts[facet] = counts.get(facet, 0) + 1
        return dict(sorted(counts.items()))

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
            "facet_summary": self.facet_summary(),
            "alias_groups": self.alias_groups(),
            "executed_independence_groups": sorted(
                g.value for g in self.executed_independence_groups()
            ),
            "candidate_producing_groups": sorted(
                g.value for g in self.candidate_producing_groups()
            ),
            "model_family_summary": self.model_family_summary(),
            "gate": self.gate_result.to_json() if self.gate_result is not None else None,
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
