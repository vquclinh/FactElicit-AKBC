"""Module 16 - the Atomic Consensus Engine.

Architecture position::

    M2 -> M3 -> M4 -> M5            (core evidence, read-only)
      \\
       \\  M9 -> M10 -> M11 -> M12 | M13 | M14 | M15   (one applies)
        \\                   |
         `------------------>+
                             v
                        M16 consensus        <- here
                             |
                             v
              [future M17 / M18 -> M19-M21]

**Zero neural calls.** M16 consumes recorded evidence. There is no runtime
parameter on any public entry point, no import of a model module, and a test
asserts the runtime counters are identical across a consensus build.

**Read-only.** The production graph is projected, never mutated: no status is
changed, no edge is added, no score is written. Module 8's prediction is
byte-identical with M16 on or off.

The engine's whole job is §12.1::

    q_g(o) = max support(e, o)      phi(o) = (F, L, X, C, U, I, D, cost, risk)
             e in g

Two policies are recorded here because the proposal leaves them open and
guessing would corrupt audited semantics:

**F stays the core F.** ``F = q(o) = g(o)/m(o)`` over
``contract.eligible_independence_groups``, exactly as Module 5 computes it.
Specialist probe families are not in that denominator and the architecture
cannot say how many of them *could* have expressed a given candidate without
world knowledge - an open-set award facet cannot be asked "were you capable of
naming this recipient?". Extending the numerator without the denominator would
either push ``q`` above 1 or require an invented ``m(o)``. Specialist
structural support is therefore reported through ``I`` and ``group_supports``,
which need no denominator, and ``F`` keeps the meaning Audit 0008 froze.

**C keeps the core normalisation.** ``C`` is Module 5's contradiction term over
its own index set; specialist contradictions are exposed as signed events and
in ``contradicting_groups`` rather than folded into a scale whose denominator
the architecture does not define. Both halves of ``C`` are visible; neither is
fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.evidence.consensus_adapters import (
    SPECIALIST_ADAPTERS,
    applicable_specialist,
    candidate_kind,
    core_graph_events,
    parametric_events,
    pending_checks_from,
)
from cover_kbc.evidence.consensus_types import (
    CONSENSUS_VERSION,
    CandidateConsensusState,
    ConsensusCost,
    ConsensusError,
    ConsensusEvidenceEvent,
    ConsensusProvenanceError,
    DisagreementKind,
    EvidencePlane,
    GroupSupport,
    NullConsensusState,
    NumericClusterConsensus,
    QueryConsensusResult,
    RiskFlag,
    SemanticDisagreement,
    sort_events,
)
from cover_kbc.evidence.graph import EvidenceGraph
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.scoring import (
    DEFAULT_SCORING,
    ScoringConfig,
    contradiction_term,
    contradicting_groups,
    coverage_q,
    disagreement_term,
    inclusion_uncertainty,
    logit_term,
    support_term,
)
from cover_kbc.types import EdgeType, OutputType

#: Immutable per-origin metadata. Two records describing one physical output
#: may disagree about what they *read* in it; they may not disagree about what
#: produced it.
_IMMUTABLE_ORIGIN_FIELDS = ("model_id", "prompt_sha256", "sample_index")


@dataclass(frozen=True)
class ConsensusConfig:
    """Module 16 configuration.

    Non-neural and unweighted by design: there is no candidate-score
    coefficient here, because M16 produces a support vector and not a score.
    ``shadow`` is the only supported mode until Modules 17 and 18 exist to
    consume the result.
    """

    enabled: bool = False
    mode: str = "shadow"
    consensus_version: str = CONSENSUS_VERSION
    #: Register Module 11's records as query-level origins. On by default: it
    #: is what makes a specialist's mined observation recognisable as a
    #: description of an output already paid for.
    include_parametric_origins: bool = True
    #: Emit query-level events (gates, null classes, Module 11 records) in the
    #: artefact. Off makes the artefact smaller; it never changes any state.
    include_query_events: bool = True

    SUPPORTED_MODES = frozenset({"shadow"})

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "ConsensusConfig":
        payload = dict(config or {})
        known = {
            "enabled", "mode", "consensus_version", "include_parametric_origins",
            "include_query_events",
        }
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"unknown consensus key(s) {unknown}; expected {sorted(known)}"
            )
        version = str(payload.get("consensus_version", CONSENSUS_VERSION))
        if version != CONSENSUS_VERSION:
            raise ValueError(
                f"unsupported consensus_version {version!r}; this build "
                f"implements {CONSENSUS_VERSION!r}"
            )
        mode = str(payload.get("mode", "shadow"))
        if mode not in cls.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported consensus mode {mode!r}; this milestone implements "
                f"{sorted(cls.SUPPORTED_MODES)} only - Module 16 feeds no "
                "production decision until Modules 17 and 18 exist"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=mode,
            consensus_version=version,
            include_parametric_origins=bool(
                payload.get("include_parametric_origins", True)
            ),
            include_query_events=bool(payload.get("include_query_events", True)),
        )


# --------------------------------------------------------------------------
# Origin ledger - the anti-double-count
# --------------------------------------------------------------------------


def check_origin_consistency(events: Sequence[ConsensusEvidenceEvent]) -> None:
    """Refuse events that claim one origin but describe different sources.

    Never repaired: if two records share an origin id and disagree about the
    model or the prompt that produced it, the provenance is wrong, and silently
    preferring one of them would hide the corruption downstream.
    """
    seen: dict[str, ConsensusEvidenceEvent] = {}
    for event in events:
        first = seen.setdefault(event.origin_event_id, event)
        if first is event:
            continue
        for field_name in _IMMUTABLE_ORIGIN_FIELDS:
            if getattr(first, field_name) != getattr(event, field_name):
                raise ConsensusProvenanceError(
                    f"origin {event.origin_event_id} is claimed by "
                    f"{first.source_module} and {event.source_module} with "
                    f"different {field_name}: {getattr(first, field_name)!r} vs "
                    f"{getattr(event, field_name)!r}"
                )
        if first.model_family and event.model_family and (
            first.model_family != event.model_family
        ):
            raise ConsensusProvenanceError(
                f"origin {event.origin_event_id} is claimed with two model "
                f"families: {first.model_family!r} vs {event.model_family!r}"
            )


#: What one physical output cost: calls, generated tokens, prompt tokens,
#: latency (``None`` when nothing timed it).
OriginLedger = dict[str, tuple[int, int, int, "float | None", bool]]


def origin_ledger(events: Sequence[ConsensusEvidenceEvent]) -> OriginLedger:
    """What each **unique origin** cost, over every description of it.

    One Module 11 output mined by a specialist is described twice - once as the
    record, once as the derived observation - and is charged once. The
    specialist's reading declares no cost at all, so the arithmetic cannot
    depend on which description is seen first, and a candidate whose only
    evidence came through the derived reading still gets the true cost of the
    output behind it.
    """
    ledger: OriginLedger = {}
    for event in events:
        calls, generated, prompt, latency, recorded = ledger.get(
            event.origin_event_id, (0, 0, 0, None, False)
        )
        if event.latency_ms is not None:
            latency = event.latency_ms if latency is None else max(latency, event.latency_ms)
        ledger[event.origin_event_id] = (
            max(calls, event.calls),
            max(generated, event.generated_tokens),
            max(prompt, event.prompt_tokens),
            latency,
            recorded or event.tokens_recorded,
        )
    return ledger


def cost_from_ledger(ledger: OriginLedger, origins: Iterable[str]) -> ConsensusCost:
    """Sum the ledger over a set of origins, each counted once."""
    unique = sorted(set(origins))
    entries = [ledger[origin] for origin in unique if origin in ledger]
    timed = [entry[3] for entry in entries if entry[3] is not None]
    counted = [entry for entry in entries if entry[4]]
    return ConsensusCost(
        unique_origin_events=len(unique),
        neural_calls=sum(entry[0] for entry in entries),
        # Summed over origins that recorded token counts; the rest are counted,
        # not guessed at.
        generated_tokens=sum(entry[1] for entry in counted),
        prompt_tokens=sum(entry[2] for entry in counted),
        origins_missing_tokens=len(entries) - len(counted),
        # Absent latency stays absent. A scripted runtime timed nothing, and
        # reporting 0.0 ms would claim a measurement that was never taken.
        latency_ms=sum(timed) if timed else None,
        latency_available=bool(timed),
    )


def origin_cost(events: Sequence[ConsensusEvidenceEvent]) -> ConsensusCost:
    """Cost over every origin these events describe."""
    ledger = origin_ledger(events)
    return cost_from_ledger(ledger, ledger)


# --------------------------------------------------------------------------
# §12.1 - q_g and I
# --------------------------------------------------------------------------


def group_supports(
    events: Sequence[ConsensusEvidenceEvent],
) -> tuple[GroupSupport, ...]:
    """``q_g(o) = max_e support(e, o)`` for every group that saw the candidate.

    The max is the whole point: ten samples of one probe are ten origin events
    and one group contribution, and five facets of one declared mechanism are
    five facets and one group. Facets are recorded beside ``q_g`` so they stay
    inspectable without ever being counted.
    """
    buckets: dict[str, list[ConsensusEvidenceEvent]] = {}
    for event in events:
        buckets.setdefault(event.group_key, []).append(event)

    out: list[GroupSupport] = []
    for group_key in sorted(buckets):
        members = buckets[group_key]
        out.append(GroupSupport(
            group_key=group_key,
            plane=members[0].plane,
            role=members[0].role,
            q_g=max(e.support for e in members),
            total_events=len(members),
            origin_event_ids=tuple(sorted({e.origin_event_id for e in members})),
            facets=tuple(sorted({e.facet_id for e in members if e.facet_id})),
        ))
    return tuple(out)


def independent_support(supports: Sequence[GroupSupport]) -> int:
    """``I``: distinct structural recall groups that support the candidate.

    Recall groups only. A verifier shown the candidate is not an independent
    source however sure it sounds, and a gate produces no candidate at all.
    """
    return sum(1 for g in supports if g.supports and g.role.is_recall)


# --------------------------------------------------------------------------
# §12.2 - semantic disagreement, without an embedding model
# --------------------------------------------------------------------------


def candidate_disagreements(
    events: Sequence[ConsensusEvidenceEvent],
) -> tuple[SemanticDisagreement, ...]:
    """Structural conflicts already encoded by the specialists.

    Nothing is inferred about meaning: every conflict below is one module
    saying two incompatible things about one candidate, using its own declared
    taxonomy.
    """
    out: list[SemanticDisagreement] = []
    supporting = [e for e in events if e.sign is EdgeType.SUPPORT]
    contradicting = [e for e in events if e.sign is EdgeType.CONTRADICT]

    if supporting and contradicting:
        kinds = sorted({
            annotation for e in contradicting for annotation in e.annotations
            if annotation.startswith(("mention_kind=", "semantic_kind="))
        })
        out.append(SemanticDisagreement(
            kind=DisagreementKind.TARGET_VERSUS_NEAR_MISS,
            detail=(
                "one source presented this candidate as the target and another "
                f"as {', '.join(kinds) if kinds else 'a contract exclusion'}"
            ),
            origin_event_ids=tuple(sorted(
                {e.origin_event_id for e in (*supporting, *contradicting)}
            )),
            group_keys=tuple(sorted({e.group_key for e in contradicting})),
        ))

    temporal = {
        annotation.split("=", 1)[1]
        for e in events for annotation in e.annotations
        if annotation.startswith("temporal_status=")
    }
    if {"CURRENT", "FORMER_OR_DELISTED"} <= temporal:
        out.append(SemanticDisagreement(
            kind=DisagreementKind.TEMPORAL_STATUS_CONFLICT,
            detail="described as both a current and a former listing",
            origin_event_ids=tuple(sorted({e.origin_event_id for e in events})),
            group_keys=tuple(sorted({e.group_key for e in events})),
        ))
    return tuple(out)


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------


def candidate_risk_flags(
    events: Sequence[ConsensusEvidenceEvent],
    *,
    supports: Sequence[GroupSupport],
    hard_violation: bool,
    verified: bool,
    pending: bool,
    explosion: bool,
) -> tuple[RiskFlag, ...]:
    """Typed risk descriptors. **Not** a confidence, and never fitted.

    Every flag names a structural property something already recorded. There is
    no weighting, no combination and no scalar: collapsing these would tell a
    reader less than the flags themselves.
    """
    flags: list[RiskFlag] = []
    if hard_violation:
        flags.append(RiskFlag.HARD_CONTRACT_VIOLATION)
    if any(e.sign is EdgeType.CONTRADICT for e in events):
        flags.append(RiskFlag.NEAR_MISS_MENTION)
    if independent_support(supports) == 1:
        flags.append(RiskFlag.SINGLE_GROUP_SUPPORT)
    if explosion:
        flags.append(RiskFlag.CANDIDATE_EXPLOSION)
    if pending:
        flags.append(RiskFlag.PENDING_DOWNSTREAM_CHECK)
    if any(a.startswith("ambiguity=") for e in events for a in e.annotations):
        flags.append(RiskFlag.AMBIGUOUS_PARSE)
    if not verified:
        flags.append(RiskFlag.UNVERIFIED)
    return tuple(flags)


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------


class AtomicConsensusEngine:
    """§12's consensus over provenance-rich atomic evidence. Non-neural."""

    def __init__(
        self,
        config: ConsensusConfig | None = None,
        scoring: ScoringConfig | None = None,
    ) -> None:
        self.config = config or ConsensusConfig(enabled=True)
        self.scoring = scoring or DEFAULT_SCORING
        if self.config.mode not in ConsensusConfig.SUPPORTED_MODES:
            raise ConsensusError(
                f"unsupported consensus mode {self.config.mode!r}"
            )

    @property
    def consensus_version(self) -> str:
        return self.config.consensus_version

    # -- entry point ---------------------------------------------------------

    def consense(
        self,
        graph: EvidenceGraph,
        specialist_result: Any,
        *,
        retrieval: Any = None,
        query_risk: Mapping[str, Any] | None = None,
        upstream_versions: Mapping[str, str] | None = None,
    ) -> QueryConsensusResult:
        """Build one query's consensus. Reads; never writes.

        ``specialist_result`` must be the result of the *applicable* specialist
        for this relation - Module 16 needs one, and only one.
        """
        query = graph.query
        contract = graph.contract
        module = applicable_specialist(query.relation)
        self._check_specialist(module, specialist_result, query)

        events: list[ConsensusEvidenceEvent] = list(core_graph_events(graph))
        if retrieval is not None and self.config.include_parametric_origins:
            events.extend(parametric_events(
                retrieval, relation=query.relation, subject=query.subject,
                row_index=query.row_index,
            ))
        events.extend(SPECIALIST_ADAPTERS[module](specialist_result, contract))
        events = list(sort_events(events))
        check_origin_consistency(events)

        by_candidate: dict[str, list[ConsensusEvidenceEvent]] = {}
        query_events: list[ConsensusEvidenceEvent] = []
        for event in events:
            if event.is_query_level:
                query_events.append(event)
            else:
                by_candidate.setdefault(event.candidate_key, []).append(event)

        pending = tuple(pending_checks_from(specialist_result, module))
        explosion = bool(getattr(specialist_result, "candidate_explosion", False))
        pending_targets = {check.candidate for check in pending}
        ledger = origin_ledger(events)

        states = tuple(
            self._candidate_state(
                key, by_candidate[key], graph, contract, ledger,
                pending_targets=pending_targets, explosion=explosion,
            )
            for key in sorted(by_candidate)
        )
        states = self._add_single_value_disagreement(states, contract)

        clusters, unassigned = self._numeric_consensus(
            specialist_result, contract, states, module
        )
        if clusters:
            states = self._attach_clusters(states, clusters)

        return QueryConsensusResult(
            consensus_version=self.consensus_version,
            relation=query.relation, subject=query.subject,
            row_index=query.row_index, applicable_specialist=module,
            upstream_versions=dict(upstream_versions or self._versions(
                specialist_result, retrieval
            )),
            candidates=states,
            null_state=self._null_consensus(specialist_result, query, states, module),
            numeric_clusters=clusters,
            unassigned_numeric_keys=unassigned,
            pending_checks=pending,
            query_events=tuple(query_events) if self.config.include_query_events else (),
            cost=cost_from_ledger(ledger, ledger),
            query_risk=dict(query_risk or {}),
        )

    # -- candidate state -----------------------------------------------------

    def _candidate_state(
        self,
        key: str,
        events: Sequence[ConsensusEvidenceEvent],
        graph: EvidenceGraph,
        contract: RelationContract,
        ledger: OriginLedger,
        *,
        pending_targets: set[str],
        explosion: bool,
    ) -> CandidateConsensusState:
        supports = group_supports(events)
        core = graph.candidates.get(key)
        display = self._display(events, core)

        # F, L, C, U come from Module 5's own functions where a core candidate
        # exists, so the audited definitions are used rather than re-derived.
        if core is not None:
            f_support = support_term(core, contract, self.scoring)
            l_logit = logit_term(core, self.scoring)
            u_prompt = disagreement_term(core)
            c_value = contradiction_term(core, contract, self.scoring)
            h_inc = inclusion_uncertainty(coverage_q(core, contract, self.scoring))
            verified = [v for v in core.verifications if v.valid_prob is not None]
            latest = verified[-1] if verified else None
            core_contradicting = tuple(g.value for g in contradicting_groups(core))
        else:
            # Specialist-only candidate: the core plane never saw it, so its
            # core terms are structurally zero and its verifier evidence is
            # *unavailable* rather than neutral.
            f_support = l_logit = u_prompt = c_value = h_inc = 0.0
            latest = None
            core_contradicting = ()

        specialist_contradicting = tuple(sorted({
            g.group_key for g in supports
            if any(
                e.group_key == g.group_key and e.sign is EdgeType.CONTRADICT
                for e in events
            ) and g.plane is not EvidencePlane.CORE
        }))

        x_cross = 1.0 if any(
            g.supports and g.role.pays_x for g in supports
        ) else 0.0

        return CandidateConsensusState(
            relation=contract.relation, subject=graph.query.subject,
            row_index=graph.query.row_index,
            candidate_key=key, display=display,
            candidate_kind=candidate_kind(contract),
            group_supports=supports,
            contradicting_groups=tuple(sorted(
                {f"core:{g}" for g in core_contradicting} | set(specialist_contradicting)
            )),
            origin_event_ids=tuple(sorted({e.origin_event_id for e in events})),
            event_ids=tuple(sorted({e.event_id for e in events})),
            f_support=f_support,
            l_logit=l_logit,
            l_available=latest is not None,
            x_cross_model=x_cross,
            c_contradiction=c_value,
            u_prompt=u_prompt,
            u_available=latest is not None and latest.prompt_disagreement is not None,
            i_independent_support=independent_support(supports),
            d_semantic=0.0,     # filled in below, once details are known
            h_inc=h_inc,
            h_ver=latest.entropy if latest else None,
            cost=cost_from_ledger(ledger, (e.origin_event_id for e in events)),
            risk_flags=candidate_risk_flags(
                events, supports=supports,
                hard_violation=core is not None and core.rejection_reason is not None,
                verified=latest is not None,
                pending=display in pending_targets or key in pending_targets,
                explosion=explosion,
            ),
            disagreement_details=(),
            hard_contract_violation=core is not None and core.rejection_reason is not None,
            rejection_reason=core.rejection_reason if core is not None else None,
            total_support_events=sum(1 for e in events if e.sign is EdgeType.SUPPORT),
            verifier_label=latest.label.value if latest and latest.label else None,
        ).with_disagreements(candidate_disagreements(events))

    @staticmethod
    def _display(
        events: Sequence[ConsensusEvidenceEvent], core: Any
    ) -> str:
        """Prefer Module 3's chosen surface; fall back to the first event's."""
        if core is not None and core.display_value:
            return core.display_value
        for event in events:
            if event.display:
                return event.display
        return events[0].candidate_key if events else ""

    def _add_single_value_disagreement(
        self,
        states: tuple[CandidateConsensusState, ...],
        contract: RelationContract,
    ) -> tuple[CandidateConsensusState, ...]:
        """A zero-or-one relation with two supported candidates is a conflict."""
        if contract.selection.max_objects != 1:
            return states
        supported = [s for s in states if s.i_independent_support > 0]
        if len(supported) < 2:
            return states
        keys = tuple(sorted(s.candidate_key for s in supported))
        out = []
        for state in states:
            if state.i_independent_support > 0:
                out.append(state.with_disagreements((
                    *state.disagreement_details,
                    SemanticDisagreement(
                        kind=DisagreementKind.COMPETING_SINGLE_VALUE,
                        detail=(
                            "the relation admits at most one object and "
                            f"{len(supported)} candidates are supported: "
                            f"{', '.join(keys)}"
                        ),
                        origin_event_ids=state.origin_event_ids,
                        group_keys=state.supporting_groups,
                    ),
                )))
            else:
                out.append(state)
        return tuple(out)

    # -- numeric -------------------------------------------------------------

    def _numeric_consensus(
        self,
        specialist_result: Any,
        contract: RelationContract,
        states: Sequence[CandidateConsensusState],
        module: str,
    ) -> tuple[tuple[NumericClusterConsensus, ...], tuple[str, ...]]:
        """Project Module 12's clusters. Nothing is re-clustered.

        A core numeric candidate joins a cluster only when its canonical value
        **is one of that cluster's own values** - exact equality in canonical
        space, using Module 12's formatting. Anything else would be a second
        membership rule, and a second membership rule is a second definition of
        "the same number". Unmatched core values are reported as unassigned
        rather than folded in.
        """
        if module != "M12" or contract.output_type is not OutputType.NUMBER:
            return (), ()

        integer_only = contract.selection.numeric_integer_only
        competing = max(0, len(specialist_result.clusters) - 1)
        divergent = [
            check for check in specialist_result.cross_unit_checks
            if not check.agrees
        ]

        clusters: list[NumericClusterConsensus] = []
        assigned: set[str] = set()
        for index, cluster in enumerate(specialist_result.clusters):
            keys = tuple(sorted({
                format_numeric(value, integer_only=integer_only)
                for value in cluster.values
            }))
            assigned.update(keys)
            origins = tuple(sorted({
                origin
                for state in states if state.candidate_key in keys
                for origin in state.origin_event_ids
            }))
            details: list[SemanticDisagreement] = []
            if competing:
                details.append(SemanticDisagreement(
                    kind=DisagreementKind.NUMERIC_COMPETING_CLUSTERS,
                    detail=(
                        f"{competing + 1} clusters of the target quantity; "
                        "Module 16 records the competition and selects none"
                    ),
                    group_keys=tuple(f"specialist:{g}" for g in cluster.independence_groups),
                ))
            if divergent:
                details.append(SemanticDisagreement(
                    kind=DisagreementKind.NUMERIC_CROSS_UNIT_DIVERGENCE,
                    detail=(
                        f"{len(divergent)} cross-unit comparison(s) that "
                        "Module 12 recorded as disagreeing"
                    ),
                ))
            clusters.append(NumericClusterConsensus(
                cluster_index=index,
                # Copied from Module 12, never recomputed.
                representative=cluster.representative,
                dispersion=cluster.dispersion,
                canonical_unit=cluster.canonical_unit,
                values=cluster.values,
                total_support=cluster.total_support,
                independent_support=cluster.independent_support,
                independence_groups=cluster.independence_groups,
                candidate_keys=keys,
                origin_event_ids=origins,
                competing_clusters=competing,
                disagreement_details=tuple(details),
            ))

        unassigned = tuple(sorted(
            state.candidate_key for state in states
            if state.candidate_key not in assigned
        ))
        return tuple(clusters), unassigned

    @staticmethod
    def _attach_clusters(
        states: tuple[CandidateConsensusState, ...],
        clusters: Sequence[NumericClusterConsensus],
    ) -> tuple[CandidateConsensusState, ...]:
        index_of = {
            key: cluster.cluster_index
            for cluster in clusters for key in cluster.candidate_keys
        }
        competing = clusters[0].competing_clusters if clusters else 0
        out = []
        for state in states:
            cluster_index = index_of.get(state.candidate_key)
            updated = replace(state, numeric_cluster_index=cluster_index)
            if cluster_index is not None and competing:
                updated = updated.with_disagreements((
                    *updated.disagreement_details,
                    SemanticDisagreement(
                        kind=DisagreementKind.NUMERIC_COMPETING_CLUSTERS,
                        detail=(
                            f"{competing + 1} competing clusters of the target "
                            "quantity"
                        ),
                        origin_event_ids=updated.origin_event_ids,
                    ),
                ))
            out.append(updated)
        return tuple(out)

    # -- null / temporal -----------------------------------------------------

    def _null_consensus(
        self,
        specialist_result: Any,
        query: Any,
        states: Sequence[CandidateConsensusState],
        module: str,
    ) -> NullConsensusState | None:
        """Carry §10.3's three classes through, with §15A's invariant intact.

        ``failed_recall_operations`` is reported and is **never** added to the
        substantive groups: repetition of ignorance is still ignorance. There
        is no ``final_empty`` here, and a strongly supported city does not
        erase recorded null evidence - both are visible at once.
        """
        if module != "M14":
            return None
        null_evidence = getattr(specialist_result, "null_evidence", None)
        gate = getattr(specialist_result, "gate", None)
        supported = tuple(sorted(
            s.candidate_key for s in states if s.i_independent_support > 0
        ))
        if null_evidence is None:
            return NullConsensusState(
                relation=query.relation, subject=query.subject,
                row_index=query.row_index,
                competing_candidates=len(supported),
                competing_candidate_keys=supported,
                gate_state=gate.state.value if gate else None,
            )
        return NullConsensusState(
            relation=query.relation, subject=query.subject,
            row_index=query.row_index,
            living_support=null_evidence.living_support,
            living_groups=null_evidence.living_groups,
            no_known_locality_support=null_evidence.no_known_locality_support,
            no_known_locality_groups=null_evidence.no_known_locality_groups,
            failed_recall_operations=null_evidence.failed_recall_operations,
            failed_recall_operation_ids=null_evidence.failed_recall_operation_ids,
            competing_candidates=len(supported),
            competing_candidate_keys=supported,
            gate_state=gate.state.value if gate else None,
        )

    # -- validation ----------------------------------------------------------

    @staticmethod
    def _check_specialist(module: str, result: Any, query: Any) -> None:
        """Refuse the wrong specialist, or one for a different query."""
        if result is None:
            raise ConsensusError(
                f"{query.relation} is routed to {module}; Module 16 needs that "
                "specialist's result and was given none"
            )
        plan = getattr(result, "plan", None)
        if plan is None:
            raise ConsensusError(
                f"the {module} result has no plan, so its provenance cannot be "
                "checked against the query"
            )
        expected = SPECIALIST_ADAPTERS.get(module)
        if expected is None:  # pragma: no cover - table covers all four
            raise ConsensusError(f"no adapter is registered for {module}")
        problems = []
        if plan.relation != query.relation:
            problems.append(
                f"specialist result is for {plan.relation!r} but the query is "
                f"{query.relation!r}"
            )
        if plan.subject != query.subject:
            problems.append(
                f"specialist subject {plan.subject!r} != query subject "
                f"{query.subject!r}"
            )
        if plan.row_index != query.row_index:
            problems.append(
                f"specialist row_index {plan.row_index} != query row_index "
                f"{query.row_index}"
            )
        if problems:
            raise ConsensusError("; ".join(problems))

    @staticmethod
    def _versions(specialist_result: Any, retrieval: Any) -> dict[str, str]:
        versions: dict[str, str] = {}
        plan = getattr(specialist_result, "plan", None)
        for attribute, name in (
            ("specialist_version", "specialist"),
            ("compiler_version", "M10"),
            ("profile_version", "M9"),
        ):
            value = getattr(plan, attribute, "")
            if value:
                versions[name] = value
        if retrieval is not None:
            versions["M11"] = retrieval.plan.retrieval_version
        return versions


def build_consensus_engine(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
    retrieval_enabled: bool,
    available_specialists: Mapping[str, bool] | None = None,
    relations: Sequence[str] = (),
    scoring: ScoringConfig | None = None,
) -> "AtomicConsensusEngine | None":
    """Build M16 when configuration asks for it, refusing a broken wiring.

    Requires Modules 9, 10 and 11, and the *applicable* specialist for each
    relation in play - not all four. A run restricted to `hasCapacity` needs
    Module 12 and nothing else.
    """
    settings = ConsensusConfig.from_mapping(config)
    if not settings.enabled:
        return None

    missing = [
        name for name, present in (
            ("query_intelligence.profiler", profiler_enabled),
            ("query_intelligence.prompt_compiler", compiler_enabled),
            ("query_intelligence.parametric_retrieval", retrieval_enabled),
        ) if not present
    ]
    if missing:
        raise ValueError(
            f"consensus.enabled requires {', '.join(missing)}; Module 16 fuses "
            "evidence those modules produce and cannot invent it"
        )

    available = dict(available_specialists or {})
    needed = sorted({applicable_specialist(relation) for relation in relations})
    absent = [module for module in needed if not available.get(module, False)]
    if absent:
        raise ValueError(
            f"consensus.enabled requires the applicable specialist(s) "
            f"{', '.join(absent)} for the configured relation(s); Module 16 "
            "needs the specialist that owns each relation, not all four"
        )
    return AtomicConsensusEngine(settings, scoring=scoring)


__all__ = [
    "AtomicConsensusEngine",
    "ConsensusConfig",
    "build_consensus_engine",
    "candidate_disagreements",
    "candidate_risk_flags",
    "check_origin_consistency",
    "cost_from_ledger",
    "origin_ledger",
    "group_supports",
    "independent_support",
    "origin_cost",
]
