"""Versioned telemetry for TRAIN controller calibration.

One TRAIN run has to answer every question Modules 20 and 21 will later be
calibrated from, because a second run costs another A100 session. So the schema
is fixed and versioned *before* the run rather than discovered during it, and
the offline deriver refuses a file whose version it does not recognise.

**No TRAIN gold appears here.** These records describe what the frozen system
did, not whether it was right. Correctness is joined offline, from
``benchmark/data/train.jsonl``, by the calibration step - keeping the boundary
the brief draws between "frozen inference output" and "offline evaluator".
That separation is what lets the same telemetry be re-derived under a different
scoring rule without re-running any model.

Each record is one *considered action* in one round of one query. Actions that
were legal but not selected are recorded too: Module 21 estimates the value of
actions it did not take, so a file containing only executed actions would bias
every bin toward whatever the collection policy happened to prefer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

#: Bumped whenever a field is added, removed or given a new meaning. The
#: deriver pins this exactly; a mismatch is a hard failure, never a warning.
#:
#: ``v2`` (Audit 0041 remediation) changes three things that a ``v1`` reader
#: would silently misread, so the version had to move:
#:
#: * ``pre_state``/``post_state`` are now the genuine state either side of one
#:   action, captured by the execution seam. In ``v1`` both were rebuilt after
#:   the row finished, so ``ΔR`` was zero by construction.
#: * ``calls_used``/``prompt_tokens``/``generated_tokens`` in a control state
#:   are **query-scoped**, not run-cumulative.
#: * ``redundancy`` is ``None`` when the action has no candidate surface to be
#:   redundant against, so a measured zero is distinguishable from an absent
#:   measurement.
#:
#: ``v3`` (Audit 0043 C-05) makes *measurement presence* explicit rather than
#: inferred, which a ``v2`` reader would get wrong in the dangerous direction:
#:
#: * ``candidate_effect_measured`` states whether the seam performed the
#:   candidate diff at all. In ``v2`` an erased diff was indistinguishable from
#:   an action that genuinely touched nothing, and the sufficiency gate passed
#:   on both.
#: * ``redundancy_status`` replaces "``None`` means either not-applicable or
#:   instrumentation-missing" with a typed three-way answer.
TELEMETRY_SCHEMA_VERSION = "train-telemetry-v3"


class TelemetryError(ValueError):
    """Telemetry that could not be trusted to calibrate a controller."""


def _finite(value: Any, name: str, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TelemetryError(f"{where}: {name} must be a number, got {value!r}") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise TelemetryError(f"{where}: {name} is not finite ({value!r})")
    return number


#: The five §15 components, named exactly as ``ResidualComponentName`` values.
#: Kept as a tuple here rather than importing the enum so this schema stays a
#: schema; :meth:`ControlStateFeatures.from_coverage_gap` validates against the
#: real enum by reading ``component.name.value``.
RESIDUAL_COMPONENTS = (
    "novelty_rate", "singleton_ratio", "facet_gap", "disagreement",
    "unresolved_mass",
)


@dataclass(frozen=True)
class ControlStateFeatures:
    """The Layer-5/6 state either side of an action.

    ``residual`` is Module 19's ``R_t`` and its five weighted components are
    kept alongside it rather than only the scalar: Module 21's state binning
    may key on a component, and recovering one from the sum afterwards is
    impossible.

    ``measured`` says whether Module 19 actually produced a residual for this
    query. A query M19 never scored has an *absent* state, not a zero one, and
    the calibration-sufficiency gate has to be able to tell them apart - the
    Audit-0041 failure was precisely an absent state that read as a zero one.
    """

    residual: float = 0.0
    novelty_rate: float = 0.0
    singleton_ratio: float = 0.0
    facet_gap: float = 0.0
    disagreement: float = 0.0
    unresolved_mass: float = 0.0
    #: Candidate-distribution uncertainty, the ``H`` of ``ΔĤ``.
    entropy: float = 0.0
    active_candidates: int = 0
    #: **Query-scoped** physical accounting at this instant, not run-cumulative.
    calls_used: int = 0
    calls_remaining: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    #: False when Module 19 published no residual for this query.
    measured: bool = True
    #: Which of §15's five components Module 19 could actually measure. A
    #: component absent from this tuple reads 0.0 above because the schema is
    #: numeric - and §15 is explicit that unavailable is *not* zero, so the
    #: distinction is kept here rather than lost in the number.
    available_components: tuple[str, ...] = RESIDUAL_COMPONENTS

    def __post_init__(self) -> None:
        for name in ("residual", *RESIDUAL_COMPONENTS, "entropy"):
            _finite(getattr(self, name), name, "control state")

    def to_json(self) -> dict[str, Any]:
        return {
            "residual": self.residual, "novelty_rate": self.novelty_rate,
            "singleton_ratio": self.singleton_ratio, "facet_gap": self.facet_gap,
            "disagreement": self.disagreement,
            "unresolved_mass": self.unresolved_mass, "entropy": self.entropy,
            "active_candidates": self.active_candidates,
            "calls_used": self.calls_used, "calls_remaining": self.calls_remaining,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "measured": self.measured,
            "available_components": list(self.available_components),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ControlStateFeatures":
        data = {k: payload[k] for k in payload if k in cls.__dataclass_fields__}
        if "available_components" in data:
            data["available_components"] = tuple(data["available_components"])
        return cls(**data)

    @classmethod
    def from_coverage_gap(
        cls, state: Any, *, entropy: float, active_candidates: int,
        calls_used: int, calls_remaining: int, prompt_tokens: int,
        generated_tokens: int,
    ) -> "ControlStateFeatures":
        """Read Module 19's canonical ``CoverageGapState``. **Fails loudly.**

        The residual lives at ``state.residual`` - a ``CoverageGapComponents``
        carrying the scalar and the five named components - and each component
        names itself with a ``ResidualComponentName`` whose ``.value`` is the
        canonical key. Audit 0041 found the runner reaching for
        ``state.coverage_gap`` (a *planner* field name) and comparing
        ``str(component.name)`` against those keys; both miss, and the result
        was a silently all-zero state.

        There is deliberately no alias fallback and no ``getattr`` default: a
        shape this function does not recognise raises, because a zeroed control
        state is worse than a crash - it calibrates.

        Args:
            state: Module 19's ``CoverageGapState`` for this query, or ``None``
                when M19 published nothing (an honest *unmeasured* state).

        Raises:
            TelemetryError: if ``state`` is not ``None`` but does not expose
                Module 19's published contract.
        """
        common = dict(
            entropy=entropy, active_candidates=active_candidates,
            calls_used=calls_used, calls_remaining=calls_remaining,
            prompt_tokens=prompt_tokens, generated_tokens=generated_tokens,
        )
        if state is None:
            return cls(measured=False, available_components=(), **common)

        components = getattr(state, "residual", None)
        if components is None or not hasattr(components, "components"):
            raise TelemetryError(
                f"{type(state).__name__} does not expose Module 19's "
                "`residual` component block; refusing to emit a zeroed control "
                "state that would calibrate as a real observation"
            )
        values = {name: 0.0 for name in RESIDUAL_COMPONENTS}
        available: list[str] = []
        for component in components.components:
            name = getattr(component, "name", None)
            key = getattr(name, "value", None)
            if key is None:
                raise TelemetryError(
                    "Module 19 residual component has no ResidualComponentName; "
                    f"got {name!r}"
                )
            if key not in values:
                raise TelemetryError(
                    f"Module 19 published residual component {key!r}, which is "
                    f"not one of §15's five: {list(RESIDUAL_COMPONENTS)}"
                )
            value = getattr(component, "value", None)
            if value is None:
                # §15: unavailable is never zero. It reads 0.0 in the numeric
                # field and stays absent from ``available_components``.
                continue
            values[key] = float(value)
            available.append(key)
        return cls(
            residual=float(getattr(components, "residual", 0.0) or 0.0),
            measured=True, available_components=tuple(available),
            **values, **common,
        )


class RedundancyStatus(str, Enum):
    """Why an action's ``redundancy`` reads the way it does.

    §17's ``η·R̂edundancy`` is calibrated from observed redundancy, so the
    offline derivation has to tell three situations apart without guessing, and
    a bare ``float | None`` cannot express the third:

    * the action had a candidate surface and it was measured - the value is a
      real observation, **including when it is 0.0**;
    * the action has no candidate surface at all, so redundancy is not a
      question about it - a *contract* fact, not a gap;
    * nothing measured it - a gap, and the calibration corpus is incomplete.

    Audit 0043 C-05 found ``None`` carrying the second and third meanings at
    once, so erasing the instrumentation entirely still passed the sufficiency
    gate. These are enum members rather than sentinel numbers precisely so no
    reader has to infer which one it is looking at.
    """

    #: Measured. ``redundancy`` is a real number in [0, 1].
    MEASURED = "MEASURED"
    #: The action touched and named nothing; redundancy is inapplicable.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    #: No measurement was taken. **Never valid for an executed action.**
    UNMEASURED = "UNMEASURED"


@dataclass(frozen=True)
class ActionOutcome:
    """What one executed action physically cost and factually produced.

    Empty for an action that was legal but not selected - such a record carries
    a pre-state and a legality judgement only, which is exactly what an
    unexplored branch should contribute.
    """

    physical_calls: int = 0
    #: Role partition of ``physical_calls``, measured from the runtimes.
    enumerator_calls: int = 0
    verifier_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    cache_hits: int = 0
    parse_ok: bool = True
    #: Candidate keys this action newly introduced, and ones it supported or
    #: contradicted. Gain and false-positive rates are computed from these
    #: offline, against gold.
    candidates_added: tuple[str, ...] = ()
    candidates_supported: tuple[str, ...] = ()
    candidates_contradicted: tuple[str, ...] = ()
    #: Candidate keys this action *named* without the graph holding them - the
    #: bridge reports them and inserts nothing (§14 candidate-free recall may
    #: not mint an object). Recorded because redundancy is measured against
    #: named-versus-held, and because a name that gold later matches is real
    #: recall this action produced.
    candidates_named: tuple[str, ...] = ()
    #: Candidate keys the production bridge actually wrote evidence for during
    #: this action. Together with ``candidates_named`` this **is** the action's
    #: candidate surface, which is what ``redundancy`` is a fraction of - so a
    #: ``NOT_APPLICABLE`` claim can be checked against the record rather than
    #: taken on trust. Deliberately not the same set as
    #: ``candidates_supported``/``_contradicted``: those come from the graph
    #: diff and answer "what changed", which is a different question.
    candidates_touched: tuple[str, ...] = ()
    #: Whether the execution seam actually performed the candidate diff. Four
    #: empty lists mean "this action changed no candidate" only when this is
    #: True; when it is False they mean **nothing was looked at**, and
    #: ``expected_verified_gain`` and ``expected_fp`` have no basis. Default
    #: False so an absent measurement is never the silent case.
    candidate_effect_measured: bool = False
    #: Fraction of the candidates this action touched or named that the graph
    #: already held. ``None`` unless :attr:`redundancy_status` is ``MEASURED``.
    redundancy: float | None = None
    #: Which of the three redundancy situations this record describes.
    redundancy_status: RedundancyStatus = RedundancyStatus.UNMEASURED
    #: Module 17's own ``argmax_label`` for this action's target.
    verifier_outcome: str = ""
    #: Module 18's own signed reading for this check, in its owner's vocabulary
    #: (``SUPPORT`` / ``CONTRADICT`` / ``ALTERNATE_RECOVERED`` / ``UNRESOLVED``
    #: and the per-mechanism enums). Recorded unflattened: §14's
    #: alternate-recovered is neither support nor contradiction, and collapsing
    #: it into one of them would invent evidence.
    structural_outcome: str = ""
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("physical_calls", "enumerator_calls", "verifier_calls",
                     "prompt_tokens", "generated_tokens", "cache_hits"):
            if getattr(self, name) < 0:
                raise TelemetryError(f"action outcome: {name} is negative")
        if self.enumerator_calls + self.verifier_calls != self.physical_calls:
            raise TelemetryError(
                f"action outcome: role partition "
                f"{self.enumerator_calls}+{self.verifier_calls} does not sum to "
                f"{self.physical_calls} physical call(s); a neural call was "
                "attributed to no role or to two"
            )
        # Shape only. Whether a *given record* was allowed to be unmeasured is
        # a sufficiency question, and `evaluate_sufficiency` owns it - keeping
        # it out of here is what lets the validator be tested against a record
        # that lost its instrumentation.
        if self.redundancy_status is RedundancyStatus.MEASURED:
            if self.redundancy is None:
                raise TelemetryError(
                    "action outcome: redundancy_status is MEASURED but no "
                    "redundancy value was recorded"
                )
            _finite(self.redundancy, "redundancy", "action outcome")
        elif self.redundancy is not None:
            raise TelemetryError(
                f"action outcome: redundancy_status is "
                f"{self.redundancy_status.value} yet a value "
                f"({self.redundancy!r}) was recorded; only MEASURED carries one"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "physical_calls": self.physical_calls,
            "enumerator_calls": self.enumerator_calls,
            "verifier_calls": self.verifier_calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "cache_hits": self.cache_hits, "parse_ok": self.parse_ok,
            "candidates_added": list(self.candidates_added),
            "candidates_supported": list(self.candidates_supported),
            "candidates_contradicted": list(self.candidates_contradicted),
            "candidates_named": list(self.candidates_named),
            "candidates_touched": list(self.candidates_touched),
            "candidate_effect_measured": self.candidate_effect_measured,
            "redundancy": self.redundancy,
            "redundancy_status": self.redundancy_status.value,
            "verifier_outcome": self.verifier_outcome,
            "structural_outcome": self.structural_outcome,
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ActionOutcome":
        data = dict(payload)
        for name in ("candidates_added", "candidates_supported",
                     "candidates_contradicted", "candidates_named",
                     "candidates_touched", "errors"):
            if name in data:
                data[name] = tuple(data[name])
        if "redundancy_status" in data:
            # Fails closed on an unrecognised member rather than defaulting to
            # MEASURED, which would read an unknown state as a real one.
            try:
                data["redundancy_status"] = RedundancyStatus(
                    str(data["redundancy_status"]))
            except ValueError:
                raise TelemetryError(
                    f"action outcome: {data['redundancy_status']!r} is not a "
                    f"RedundancyStatus"
                ) from None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ActionTelemetryRecord:
    """One considered action, in one round, for one query."""

    schema_version: str
    run_id: str
    row_index: int
    subject: str
    relation: str
    program_type: str
    round_index: int
    #: Deterministic identity of this execution. Duplicates are a hard error:
    #: the same physical call counted twice would inflate every cost estimate.
    operation_id: str
    action_family: str
    target_class: str = ""
    #: The owner's canonical, deterministic action id (``M17:...``/``M18:...``).
    #: Never a memory address: offline joins and the duplicate guard both key
    #: on it across processes.
    action_id: str = ""
    model_role: str = ""
    #: The class an actual Module 20 reservation was charged to. Empty during
    #: calibration collection, where **no reservation happens** - collection
    #: predates the TRAIN-calibrated ledger. It is deliberately *not* reused to
    #: carry the owner's declaration; see ``spend_class``.
    reserved_class: str = ""
    #: The owner-declared budget classification of this action (DISCOVERY /
    #: VERIFICATION) and the protected reserve it would draw on. These are
    #: Module 20 *vocabulary*, published by the action's own descriptor, and are
    #: what offline derivation groups observed spend by. They assert nothing
    #: about a reservation having occurred.
    spend_class: str = ""
    reserve_purpose: str = ""
    legal: bool = True
    selected: bool = False
    executed: bool = False
    selection_reason: str = ""
    pre_state: ControlStateFeatures = field(default_factory=ControlStateFeatures)
    post_state: ControlStateFeatures | None = None
    outcome: ActionOutcome = field(default_factory=ActionOutcome)

    def __post_init__(self) -> None:
        where = f"{self.subject}/{self.relation}#{self.round_index}"
        if self.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise TelemetryError(
                f"{where}: telemetry schema {self.schema_version!r} is not "
                f"{TELEMETRY_SCHEMA_VERSION!r}"
            )
        if not self.operation_id:
            raise TelemetryError(f"{where}: an action record needs an operation_id")
        if not self.action_family:
            raise TelemetryError(f"{where}: an action record needs an action_family")
        if self.executed and not self.selected:
            raise TelemetryError(
                f"{where}: {self.operation_id} executed without being selected"
            )
        if self.selected and not self.legal:
            raise TelemetryError(
                f"{where}: {self.operation_id} was selected but marked illegal"
            )
        if self.executed and self.post_state is None:
            raise TelemetryError(
                f"{where}: {self.operation_id} executed but recorded no post-state, "
                "so its ΔR and ΔH cannot be derived"
            )
        if not self.executed and self.outcome.physical_calls:
            raise TelemetryError(
                f"{where}: {self.operation_id} was not executed but charged "
                f"{self.outcome.physical_calls} physical call(s)"
            )
        if not self.executed and (
            self.outcome.candidates_added or self.outcome.candidates_supported
            or self.outcome.candidates_contradicted
        ):
            raise TelemetryError(
                f"{where}: {self.operation_id} was not executed but claims a "
                "candidate effect; an unexplored branch produces none"
            )
        if self.executed and not self.action_id:
            raise TelemetryError(
                f"{where}: {self.operation_id} executed without the owner's "
                "canonical action_id, so it cannot be joined offline"
            )
        if self.executed and not self.pre_state.measured:
            raise TelemetryError(
                f"{where}: {self.operation_id} executed against an unmeasured "
                "control state; ΔR would be zero by construction"
            )

    @property
    def delta_residual(self) -> float | None:
        """``ΔR`` - a reduction is positive, matching §17's ``ΔR̂`` sign."""
        if self.post_state is None:
            return None
        return self.pre_state.residual - self.post_state.residual

    @property
    def delta_entropy(self) -> float | None:
        if self.post_state is None:
            return None
        return self.pre_state.entropy - self.post_state.entropy

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "run_id": self.run_id,
            "row_index": self.row_index, "SubjectEntity": self.subject,
            "Relation": self.relation, "program_type": self.program_type,
            "round_index": self.round_index, "operation_id": self.operation_id,
            "action_family": self.action_family, "target_class": self.target_class,
            "action_id": self.action_id, "model_role": self.model_role,
            "reserved_class": self.reserved_class,
            "spend_class": self.spend_class,
            "reserve_purpose": self.reserve_purpose, "legal": self.legal,
            "selected": self.selected, "executed": self.executed,
            "selection_reason": self.selection_reason,
            "pre_state": self.pre_state.to_json(),
            "post_state": self.post_state.to_json() if self.post_state else None,
            "outcome": self.outcome.to_json(),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ActionTelemetryRecord":
        post = payload.get("post_state")
        return cls(
            schema_version=payload["schema_version"], run_id=payload.get("run_id", ""),
            row_index=int(payload["row_index"]), subject=payload["SubjectEntity"],
            relation=payload["Relation"], program_type=payload.get("program_type", ""),
            round_index=int(payload.get("round_index", 0)),
            operation_id=payload["operation_id"],
            action_family=payload["action_family"],
            target_class=payload.get("target_class", ""),
            action_id=payload.get("action_id", ""),
            model_role=payload.get("model_role", ""),
            reserved_class=payload.get("reserved_class", ""),
            spend_class=payload.get("spend_class", ""),
            reserve_purpose=payload.get("reserve_purpose", ""),
            legal=bool(payload.get("legal", True)),
            selected=bool(payload.get("selected", False)),
            executed=bool(payload.get("executed", False)),
            selection_reason=payload.get("selection_reason", ""),
            pre_state=ControlStateFeatures.from_json(payload.get("pre_state", {})),
            post_state=ControlStateFeatures.from_json(post) if post else None,
            outcome=ActionOutcome.from_json(payload.get("outcome", {})),
        )


class TelemetryWriter:
    """Append-only JSONL writer that refuses a duplicate operation identity.

    ``resume`` is required rather than defaulted because the two behaviours are
    destructive in opposite directions. A fresh run must truncate, or it would
    silently graft new records onto an unrelated file; a resumed run must
    append, or it destroys every row the previous process committed. Guessing
    from whether the file exists gets the second case wrong exactly when it is
    most expensive - hours into a run.

    On resume the existing identities are loaded first, so the duplicate guard
    spans the whole file rather than only this process's writes.
    """

    def __init__(self, path: str | Path, *, run_id: str, resume: bool) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.resume = resume
        self._seen: set[str] = set()
        if resume and self.path.is_file():
            # Reading the committed file up front is what makes the duplicate
            # guard meaningful across a restart.
            for record in read_telemetry(self.path):
                self._seen.add(
                    f"{record.row_index}:{record.round_index}:{record.operation_id}")
            self._handle = self.path.open("a", encoding="utf-8")
        else:
            self._handle = self.path.open("w", encoding="utf-8")

    @property
    def committed_identities(self) -> int:
        """How many distinct action records this writer already accounts for."""
        return len(self._seen)

    def write(self, record: ActionTelemetryRecord) -> ActionTelemetryRecord:
        record = replace(record, run_id=self.run_id)
        key = f"{record.row_index}:{record.round_index}:{record.operation_id}"
        if key in self._seen:
            raise TelemetryError(
                f"duplicate telemetry identity {key}; a physical call recorded "
                "twice would inflate every cost estimate derived from it"
            )
        self._seen.add(key)
        self._handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        self._handle.flush()
        return record

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TelemetryWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_telemetry(path: str | Path) -> Iterator[ActionTelemetryRecord]:
    """Stream a telemetry file, validating every record as it is read."""
    source = Path(path)
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise TelemetryError(f"{source}:{number}: not valid JSON: {error}") from error
        try:
            yield ActionTelemetryRecord.from_json(payload)
        except (KeyError, TypeError) as error:
            raise TelemetryError(f"{source}:{number}: malformed record: {error}") from error


def executed_families(records: Sequence[ActionTelemetryRecord]) -> dict[str, int]:
    """How many times each action family actually ran. Drives the support gate."""
    counts: dict[str, int] = {}
    for record in records:
        if record.executed:
            counts[record.action_family] = counts.get(record.action_family, 0) + 1
    return dict(sorted(counts.items()))


def successor_transitions(
    records: Sequence[ActionTelemetryRecord],
) -> list[tuple[ActionTelemetryRecord, ActionTelemetryRecord]]:
    """Consecutive executed-action pairs within one query, in round order.

    Each pair is one observed ``state_t --a--> state_{t+1} --b--> state_{t+2}``
    link, which is what §17's depth-2 lookahead needs successor frequencies for.
    A pair only forms when the first action's post-state really is the second
    action's pre-state, so a broken instrumentation chain yields no transitions
    rather than a fabricated one.
    """
    by_row: dict[tuple[int, str, str], list[ActionTelemetryRecord]] = {}
    for record in records:
        if record.executed:
            by_row.setdefault(
                (record.row_index, record.subject, record.relation), []
            ).append(record)
    pairs = []
    for row in by_row.values():
        row.sort(key=lambda r: r.round_index)
        for first, second in zip(row, row[1:]):
            if first.post_state is not None and first.post_state == second.pre_state:
                pairs.append((first, second))
    return pairs


__all__ = [
    "RESIDUAL_COMPONENTS",
    "TELEMETRY_SCHEMA_VERSION",
    "ActionOutcome",
    "ActionTelemetryRecord",
    "ControlStateFeatures",
    "RedundancyStatus",
    "TelemetryError",
    "TelemetryWriter",
    "executed_families",
    "read_telemetry",
    "successor_transitions",
]
