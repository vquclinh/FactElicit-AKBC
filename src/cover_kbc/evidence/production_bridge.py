"""The one seam by which upgraded evidence reaches the production graph.

Modules 11-18 have always executed and always produced typed results; what they
could never do is affect what Module 8 emitted. This is the bridge that closes
that gap, and it is deliberately the *only* one: a single chokepoint that can be
read, tested and audited, rather than each specialist reaching into Module 3 on
its own.

It consumes ``Layer4EvidenceState`` because that is already the canonical typed
integration of everything upstream - Module 16's consensus (which itself has
consumed Module 11's retrieval and Modules 12-15's specialists), Module 17's
calibrated verifications and Module 18's structural checks. Bridging one state
therefore bridges all of them, with no second evidence schema and no module
bypassing its owner.

Four rules do the real work, and each one exists because the obvious shortcut
breaks an audited invariant:

1. **Shadow mutates nothing.** Not "mutates less" - nothing. The M16-only /
   M16+M17 / M16+M17+M18 ablations and Audits 0033/0034 depend on shadow output
   being byte-identical, so the mode check happens before any write.
2. **A candidate the graph does not hold is never inserted.** Module 18's
   candidate-free probe can name something Module 16 never had;
   ``CandidateEvidenceOverlay`` marks it ``discovered_by_structural_check`` and
   describes it as unverified and never accepted. Crediting it here would let a
   structural probe invent an answer.
3. **Only executed checks produce edges.** A check that was eligible and never
   scheduled, or that failed, describes work that did not happen. Turning it
   into support would fabricate evidence.
4. **Each physical measurement becomes at most one edge.** The graph refuses a
   duplicate edge id outright, so a record consumed by several downstream
   layers still bills once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cover_kbc.evidence.layer4_types import (
    CheckExecutionStatus,
    StructuralOutcome,
    VerifierAvailability,
)
from cover_kbc.integration_mode import (
    IntegrationMode,
    IntegrationModeError,
    parse_mode,
)
from cover_kbc.types import (
    EdgeType,
    Evidence,
    EvidenceMode,
    IndependenceGroup,
    VerificationLabel,
    VerificationResult,
)

#: Bumped when the bridge's application rules change.
BRIDGE_VERSION = "production-bridge-v1"

#: How a structural outcome signs an evidence edge. ``ALTERNATE_RECOVERED`` is
#: absent deliberately: Audit 0027 §20A settled that recovering a different
#: valid answer is not a contradiction of this one, so it signs nothing.
_OUTCOME_EDGES = {
    StructuralOutcome.SUPPORT: EdgeType.SUPPORT,
    StructuralOutcome.CONTRADICT: EdgeType.CONTRADICT,
}

#: Verifier verdicts, mapped to the graph's own label vocabulary.
_LABELS = {
    "A": VerificationLabel.VALID, "VALID": VerificationLabel.VALID,
    "B": VerificationLabel.INVALID, "INVALID": VerificationLabel.INVALID,
    "C": VerificationLabel.UNKNOWN, "UNKNOWN": VerificationLabel.UNKNOWN,
}


class ProductionBridgeError(RuntimeError):
    """The bridge was asked to apply evidence it must not apply."""


@dataclass
class BridgeReport:
    """Exactly what one application did, for accounting and for tests."""

    mode: IntegrationMode
    bridge_version: str = BRIDGE_VERSION
    applied: bool = False
    verifications_applied: int = 0
    structural_edges_applied: int = 0
    candidates_touched: tuple[str, ...] = ()
    #: Named by a structural probe but absent from the graph - reported, never
    #: inserted, so the count is visible without the candidate being credited.
    discovered_not_inserted: tuple[str, ...] = ()
    skipped_unexecuted_checks: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value, "bridge_version": self.bridge_version,
            "applied": self.applied,
            "verifications_applied": self.verifications_applied,
            "structural_edges_applied": self.structural_edges_applied,
            "candidates_touched": list(self.candidates_touched),
            "discovered_not_inserted": list(self.discovered_not_inserted),
            "skipped_unexecuted_checks": self.skipped_unexecuted_checks,
            "notes": list(self.notes),
        }


class ProductionEvidenceBridge:
    """Applies a ``Layer4EvidenceState`` to the production graph, or does not."""

    #: Every mode this bridge knows how to honour. An unlisted mode fails
    #: closed rather than being treated as the permissive case.
    SUPPORTED_MODES = frozenset({
        IntegrationMode.SHADOW,
        IntegrationMode.PRODUCTION,
        IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY,
    })

    def __init__(self, mode: IntegrationMode | str) -> None:
        # Normalised through the canonical parser so a bare string cannot be
        # stored as the mode: ``IntegrationMode`` is a ``str`` enum, so an
        # unnormalised value would compare equal here and then fail to answer
        # ``may_mutate_production_state`` later.
        mode = parse_mode(mode, module="production bridge")
        if mode not in self.SUPPORTED_MODES:
            raise IntegrationModeError(
                f"production bridge does not support mode {mode!r}; supported: "
                f"{sorted(m.value for m in self.SUPPORTED_MODES)}"
            )
        self.mode = mode

    def apply(self, graph: Any, state: Any) -> BridgeReport:
        """Apply one query's integrated Layer-4 evidence to its graph.

        Collection uses the identical path as production - that is the whole
        point of collecting on the real seams, and it is why TRAIN observes the
        same transitions VALIDATION will.
        """
        report = BridgeReport(mode=self.mode)
        if not self.mode.may_mutate_production_state:
            report.notes.append(
                "shadow: evidence observed, production state untouched")
            return report

        if state is None:
            report.notes.append("no Layer-4 state for this query")
            return report
        if getattr(state, "subject", None) != graph.query.subject or \
                getattr(state, "relation", None) != graph.query.relation:
            raise ProductionBridgeError(
                f"Layer-4 state for {state.subject!r}/{state.relation!r} cannot be "
                f"applied to {graph.query.subject!r}/{graph.query.relation!r}"
            )

        touched: list[str] = []
        discovered: list[str] = []
        for overlay in getattr(state, "candidates", ()):
            candidate = graph.candidates.get(overlay.candidate_key)
            if candidate is None:
                # Rule 2: a structural probe may report a name, never mint one.
                if getattr(overlay, "discovered_by_structural_check", False):
                    discovered.append(overlay.candidate_key)
                continue
            before = (report.verifications_applied, report.structural_edges_applied)
            self._apply_verifier(graph, overlay, report)
            self._apply_structural(graph, overlay, report)
            if (report.verifications_applied, report.structural_edges_applied) != before:
                touched.append(overlay.candidate_key)

        report.applied = bool(touched)
        report.candidates_touched = tuple(touched)
        report.discovered_not_inserted = tuple(discovered)
        return report

    # -- Module 17 ----------------------------------------------------------

    def _apply_verifier(self, graph: Any, overlay: Any, report: BridgeReport) -> None:
        """Module 17's verdict enters as a canonical blind-verifier verdict.

        ``graph.add_verification`` marks the edge ``SHOWN_CANDIDATE`` in the
        ``BLIND_VERIFIER`` group, which is exactly what Module 17 is: the
        verifier was handed this name and asked to judge it. It moves score and
        tier; it never writes ``ObjectEntities``.
        """
        verifier = getattr(overlay, "specialist_verifier", None)
        if verifier is None:
            return
        availability = getattr(verifier, "availability", None)
        if availability is not VerifierAvailability.AVAILABLE:
            return
        if not getattr(verifier, "readings", 0):
            # Controls alone are prompt-label bias measurements, never factual
            # evidence, however many calls they cost.
            return
        label = _LABELS.get(str(getattr(verifier, "argmax_label", "") or "").upper())
        if label is None:
            return
        distribution = dict(getattr(verifier, "distribution", None) or {})
        result = VerificationResult(
            candidate_key=overlay.candidate_key,
            label=label,
            valid_prob=distribution.get("A", distribution.get("VALID")),
            invalid_prob=distribution.get("B", distribution.get("INVALID")),
            unknown_prob=distribution.get("C", distribution.get("UNKNOWN")),
            calibrated=bool(getattr(verifier, "control_calls", 0)),
            margin=getattr(verifier, "valid_margin", None),
            entropy=getattr(verifier, "verifier_entropy", None),
            prompt_disagreement=getattr(verifier, "template_disagreement", None),
            num_templates=max(1, int(getattr(verifier, "readings", 1))),
            model_id=getattr(verifier, "model_id", "") or "",
            model_family=getattr(verifier, "model_family", "") or "",
            record_id=f"m17:{overlay.candidate_key}",
        )
        try:
            applied = graph.add_verification(result)
        except ValueError:
            # Rule 4, same as the structural path: the graph refuses a
            # duplicate edge id, so re-integrating a state whose verification
            # is already attached is a no-op rather than an error. Layer 4 is
            # rebuilt after every execution round, so this is the ordinary
            # case, not a pathological one.
            return
        if applied is not None:
            report.verifications_applied += 1

    # -- Module 18 ----------------------------------------------------------

    def _apply_structural(self, graph: Any, overlay: Any, report: BridgeReport) -> None:
        """Module 18's executed checks enter as signed structural edges."""
        for check in getattr(overlay, "structural_checks", ()):
            if getattr(check, "status", None) is not CheckExecutionStatus.RESOLVED:
                # Rule 3: eligible-but-unscheduled and failed checks describe
                # work that did not happen.
                report.skipped_unexecuted_checks += 1
                continue
            edge_type = _OUTCOME_EDGES.get(getattr(check, "outcome", None))
            if edge_type is None:
                continue
            try:
                group = IndependenceGroup(str(check.independence_group))
            except ValueError:
                raise ProductionBridgeError(
                    f"Module 18 check {check.check_kind!r} declared unknown "
                    f"independence group {check.independence_group!r}"
                ) from None
            evidence = Evidence(
                candidate_key=overlay.candidate_key,
                edge_type=edge_type,
                independence_group=group,
                view_id=f"m18:{check.check_kind}",
                model_id=getattr(check, "model_id", "") or "",
                run_id=0,
                record_id=check.origin_event_id,
                model_family=getattr(check, "model_family", "") or "",
                # A shown-candidate check is anchored; only a hidden-candidate
                # probe is independent recall, exactly as Audit 0008 defines X.
                mode=(EvidenceMode.SHOWN_CANDIDATE
                      if getattr(check, "candidate_shown", True)
                      else EvidenceMode.INDEPENDENT_RECALL),
            )
            try:
                graph._attach(graph.candidates[overlay.candidate_key], evidence)
            except ValueError:
                # Rule 4: the graph refuses a duplicate edge id, so a record
                # consumed by several layers still bills exactly once.
                continue
            report.structural_edges_applied += 1


__all__ = [
    "BRIDGE_VERSION",
    "BridgeReport",
    "ProductionBridgeError",
    "ProductionEvidenceBridge",
]
