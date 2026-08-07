"""Is this telemetry enough to calibrate Modules 20 and 21 - without gold?

The expensive failure this prevents is discovering, weeks after a full TRAIN
session, that one statistic was never recorded and the only way to get it is to
run 477 rows of frozen-model inference again. Audit 0041 found exactly that:
Module 19's residual was never read, per-action state was rebuilt after the row
finished, and four of §17's six estimates had no observational basis.

So the run does not get to declare success merely by reaching the end. It has
to prove, structurally, that every quantity the offline derivation will need is
*present*.

Three rules shape every check below.

**Structural, never statistical.** This asks whether a field was instrumented,
not whether its value is interesting. A collection where every action genuinely
produced no new candidate is a valid observation; a collection where the
candidate-effect field was never populated is not, and the two must not be
conflated.

**A measured zero is valid; an absent measurement is not.** ``redundancy`` is
``None`` when an action had no candidate surface and ``0.0`` when it had one and
nothing was redundant. ``available_components`` says which of §15's five terms
Module 19 could measure, so a component reading 0.0 is distinguishable from a
component that never ran. Every check here respects that distinction rather than
testing ``!= 0``.

**No gold.** Verified gain and false-positive risk are joined offline against
``benchmark/data/train.jsonl``. What this file checks is that the *candidate
identities* those joins need were recorded, so the join is possible at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionTelemetryRecord,
    RedundancyStatus,
    successor_transitions,
)

#: §17's six estimates and the successor distribution, with the telemetry each
#: one is derived from. Stated here so the audit trail names the dependency
#: rather than leaving it implicit in a check.
M21_REQUIREMENTS: dict[str, str] = {
    "expected_verified_gain":
        "outcome.candidates_added/supported, gated on candidate_effect_measured "
        "(joined to gold offline)",
    "expected_fp":
        "outcome.candidates_added/supported, gated on candidate_effect_measured "
        "(joined to gold offline)",
    "expected_delta_r": "pre_state.residual - post_state.residual",
    "expected_delta_h": "pre_state.entropy - post_state.entropy",
    "expected_cost": "outcome.physical_calls / prompt_tokens / generated_tokens",
    "expected_redundancy":
        "outcome.redundancy where redundancy_status is MEASURED; actions marked "
        "NOT_APPLICABLE are excluded from the estimate rather than read as zero",
    "successor_state_probabilities": "consecutive executed rounds within one query",
}

#: The bin key §17 estimates are stored under.
M21_BIN_KEY = ("relation", "program_type", "state_bin", "family", "target_class")

#: §16/§9.3's per-relation quantities and the telemetry they group over.
M20_REQUIREMENTS: dict[str, str] = {
    "hard_calls": "per-query sum of outcome.physical_calls, by relation",
    "hard_generated_tokens": "per-query sum of outcome.generated_tokens, by relation",
    "discovery_cap": "outcome.physical_calls where spend_class == DISCOVERY",
    "verification_cap": "outcome.physical_calls where spend_class == VERIFICATION",
    "verification_reserve": "verification spend on protected-purpose actions",
    "special_reserve_sizes": "outcome.physical_calls grouped by reserve_purpose",
}


class SufficiencyError(ValueError):
    """Telemetry that could not be checked at all."""


@dataclass(frozen=True)
class SufficiencyReport:
    """A verdict on derivability, with every reason attached."""

    ok: bool
    blockers: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blockers": list(self.blockers),
            "satisfied": list(self.satisfied),
            "details": dict(self.details),
        }

    def summary(self) -> str:
        lines = [f"calibration sufficiency: {'PASS' if self.ok else 'FAIL'}"]
        for entry in self.satisfied:
            lines.append(f"  ok      {entry}")
        for entry in self.blockers:
            lines.append(f"  BLOCKER {entry}")
        return "\n".join(lines)


def _canonical_program_types() -> set[str]:
    from cover_kbc.contracts.base import ProgramType

    return {member.value for member in ProgramType}


def _canonical_families() -> set[str]:
    from cover_kbc.control.planner_types import ActionFamily

    return {member.value for member in ActionFamily}


def evaluate_sufficiency(
    records: Sequence[ActionTelemetryRecord], *, expect_transitions: bool = True,
) -> SufficiencyReport:
    """Can Modules 20 and 21 be derived from these records without a rerun?

    Args:
        records: every telemetry record the run committed.
        expect_transitions: require at least one observed
            ``state_t -> state_t+1 -> state_t+2`` chain. §17's depth-2
            lookahead has no support without one, so a full run must have them;
            a deliberately depth-1 slice may say so explicitly.

    Returns:
        A report whose default is refusal.
    """
    blockers: list[str] = []
    satisfied: list[str] = []
    details: dict[str, Any] = {}

    if not records:
        return SufficiencyReport(
            False, ("telemetry is empty; no action was ever observed",), (),
            {"records": 0})

    executed = [r for r in records if r.executed]
    details["records"] = len(records)
    details["executed"] = len(executed)
    details["legal_unexecuted"] = len(records) - len(executed)
    if not executed:
        blockers.append(
            "no action was executed; every §17 estimate needs at least one "
            "observed outcome")

    # -- schema and canonical identity ------------------------------------
    versions = {r.schema_version for r in records}
    if versions != {TELEMETRY_SCHEMA_VERSION}:
        blockers.append(
            f"telemetry mixes schema versions {sorted(versions)}; the deriver "
            f"pins {TELEMETRY_SCHEMA_VERSION!r} exactly")
    else:
        satisfied.append(f"schema {TELEMETRY_SCHEMA_VERSION} throughout")

    program_types = {r.program_type for r in records}
    details["program_types"] = sorted(program_types)
    unknown = sorted(program_types - _canonical_program_types())
    if unknown:
        blockers.append(
            f"program_type {unknown} is not a canonical ProgramType value; "
            "HistoricalBinPackage.lookup would never match a bin keyed on it")
    else:
        satisfied.append("program_type is the canonical ProgramType value")

    families = {r.action_family for r in records}
    details["action_families"] = sorted(families)
    stray = sorted(families - _canonical_families())
    if stray:
        blockers.append(
            f"action_family {stray} is not a canonical ActionFamily; Module "
            "21's bins are keyed on the enum")
    else:
        satisfied.append("action_family is the canonical ActionFamily value")

    # -- deterministic identity -------------------------------------------
    missing_ids = [r.operation_id for r in executed if not r.action_id]
    if missing_ids:
        blockers.append(
            f"{len(missing_ids)} executed action(s) carry no canonical "
            "action_id, so they cannot be joined offline")
    elif executed:
        satisfied.append("every executed action carries its owner's action_id")
    if any("0x" in r.operation_id or r.operation_id.rsplit(":", 1)[-1].isdigit()
           and len(r.operation_id.rsplit(":", 1)[-1]) > 12 for r in records):
        blockers.append(
            "an operation_id looks like a memory address; identity must be "
            "reproducible across processes")

    # -- Module 21 --------------------------------------------------------
    unmeasured = [r.operation_id for r in executed if not r.pre_state.measured
                  or (r.post_state is not None and not r.post_state.measured)]
    if unmeasured:
        blockers.append(
            f"{len(unmeasured)} executed action(s) carry an unmeasured control "
            "state; ΔR and the state bin cannot be derived from them")
    elif executed:
        satisfied.append("every executed action has a measured pre/post state")

    componentless = [r.operation_id for r in executed
                     if not r.pre_state.available_components]
    if componentless:
        blockers.append(
            f"{len(componentless)} executed action(s) recorded no available §15 "
            "component, so no state bin can be keyed on one")
    elif executed:
        satisfied.append("§15 components are recorded with their availability")

    # Measurement *presence*, never "the list came back non-empty". An action
    # that genuinely touched nothing is a valid observation; an action nobody
    # looked at is not, and Audit 0043 C-05 found the two indistinguishable -
    # erasing every candidate list still passed here.
    uninstrumented = [
        r.operation_id for r in executed if not r.outcome.candidate_effect_measured
    ]
    if uninstrumented:
        blockers.append(
            f"{len(uninstrumented)} executed action(s) record no candidate-effect "
            "measurement; expected_verified_gain and expected_fp have no basis, "
            "and an empty effect cannot be distinguished from an unmeasured one")
    elif executed:
        satisfied.append(
            "candidate-effect measurement is recorded on every executed action "
            "(empty lists are then a real observation)")

    unmeasured_redundancy = [
        r.operation_id for r in executed
        if r.outcome.redundancy_status is RedundancyStatus.UNMEASURED
    ]
    if unmeasured_redundancy:
        blockers.append(
            f"{len(unmeasured_redundancy)} executed action(s) record "
            "redundancy_status=UNMEASURED; expected_redundancy has no basis for "
            "them and the gap is not an inapplicability")
    # A record may not claim redundancy is inapplicable while reporting a
    # candidate surface: those two statements contradict each other, and the
    # falsehood would silently remove the action from §17's η term.
    #
    # "Surface" is `candidates_touched + candidates_named` - the bridge's own
    # account of what this action wrote or named - and deliberately **not** the
    # graph diff. An action can change a candidate's state without the bridge
    # writing anything for it, so testing NOT_APPLICABLE against
    # `candidates_supported`/`_contradicted` would reject truthful records.
    def _surface(record: ActionTelemetryRecord) -> int:
        return len(record.outcome.candidates_touched) + len(
            record.outcome.candidates_named)

    false_na = [
        r.operation_id for r in executed
        if r.outcome.redundancy_status is RedundancyStatus.NOT_APPLICABLE
        and _surface(r)
    ]
    if false_na:
        blockers.append(
            f"{len(false_na)} action(s) declare redundancy NOT_APPLICABLE while "
            "reporting a candidate surface; one of the two is untrue")
    # ...and the mirror: a measured redundancy needs a surface to be a fraction
    # of, or the number is not a measurement of anything.
    empty_measured = [
        r.operation_id for r in executed
        if r.outcome.redundancy_status is RedundancyStatus.MEASURED
        and not _surface(r)
    ]
    if empty_measured:
        blockers.append(
            f"{len(empty_measured)} action(s) report a MEASURED redundancy with "
            "no candidate surface to have measured it against")
    false_na = false_na + empty_measured
    if executed and not unmeasured_redundancy and not false_na:
        satisfied.append(
            "redundancy is measured, or explicitly inapplicable, on every "
            "executed action")

    m18_families = {"REVERSE_CHECK", "COUNTERFACTUAL_VERIFY", "CANDIDATE_FREE_RECALL"}
    verdictless = [
        r.operation_id for r in executed
        if r.action_family == "SPECIALIST_VERIFY"
        and not r.outcome.verifier_outcome and not r.outcome.errors
    ]
    if verdictless:
        blockers.append(
            f"{len(verdictless)} executed Module 17 action(s) recorded neither a "
            "verifier verdict nor an error; the verdict was not instrumented")
    elif executed:
        satisfied.append("Module 17 verdicts (or their errors) are recorded")

    readingless = [
        r.operation_id for r in executed
        if r.action_family in m18_families
        and not r.outcome.structural_outcome and not r.outcome.errors
    ]
    if readingless:
        blockers.append(
            f"{len(readingless)} executed Module 18 action(s) recorded neither a "
            "structural reading nor an error; the outcome was not instrumented")
    elif executed:
        satisfied.append("Module 18 structural readings (or their errors) are recorded")

    transitions = successor_transitions(records)
    details["successor_transitions"] = len(transitions)
    if expect_transitions and not transitions:
        blockers.append(
            "no successor transition was observed; §17's depth-2 lookahead has "
            "no successor-state frequency to be calibrated from")
    elif transitions:
        satisfied.append(
            f"{len(transitions)} successor transition(s) recorded, so successor "
            "state probabilities are derivable")

    bin_keys = {
        (r.relation, r.program_type, r.action_family, r.target_class)
        for r in executed
    }
    details["distinct_bin_keys"] = len(bin_keys)
    details["m21_requirements"] = dict(M21_REQUIREMENTS)
    details["m21_bin_key"] = list(M21_BIN_KEY)

    # -- Module 20 --------------------------------------------------------
    unclassified = [r.operation_id for r in executed if not r.spend_class]
    if unclassified:
        blockers.append(
            f"{len(unclassified)} executed action(s) carry no owner-declared "
            "spend_class; discovery and verification spend cannot be separated")
    elif executed:
        satisfied.append("every executed action carries its owner's spend_class")

    if any(r.reserved_class for r in records):
        blockers.append(
            "a record claims a Module 20 reserved_class, but collection runs "
            "before any calibrated ledger exists and reserves nothing")
    else:
        satisfied.append(
            "reserved_class is empty: collection asserts no reservation it did "
            "not make")

    charged = [r for r in executed if r.outcome.physical_calls]
    prompt_total = sum(r.outcome.prompt_tokens for r in executed)
    generated_total = sum(r.outcome.generated_tokens for r in executed)
    details["physical_calls"] = sum(r.outcome.physical_calls for r in executed)
    details["prompt_tokens"] = prompt_total
    details["generated_tokens"] = generated_total
    if charged and prompt_total <= 0:
        blockers.append(
            f"{len(charged)} action(s) made a physical call yet the run recorded "
            "zero prompt tokens; a real call always has a prompt")
    elif charged:
        satisfied.append(
            f"prompt-token accounting is live ({prompt_total} tokens over "
            f"{len(charged)} charged action(s))")

    role_mismatch = [
        r.operation_id for r in executed
        if r.outcome.enumerator_calls + r.outcome.verifier_calls
        != r.outcome.physical_calls
    ]
    if role_mismatch:
        blockers.append(
            f"{len(role_mismatch)} action(s) have a role partition that does not "
            "sum; per-role Module 20 caps cannot be derived")
    elif executed:
        satisfied.append("the role partition sums on every executed action")

    details["relations"] = sorted({r.relation for r in records})
    details["queries"] = len({(r.row_index, r.relation) for r in records})
    details["m20_requirements"] = dict(M20_REQUIREMENTS)

    return SufficiencyReport(
        not blockers, tuple(blockers), tuple(satisfied), details)


__all__ = [
    "M20_REQUIREMENTS",
    "M21_BIN_KEY",
    "M21_REQUIREMENTS",
    "SufficiencyError",
    "SufficiencyReport",
    "evaluate_sufficiency",
]
