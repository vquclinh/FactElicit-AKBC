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
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

#: Bumped whenever a field is added, removed or given a new meaning. The
#: deriver pins this exactly; a mismatch is a hard failure, never a warning.
TELEMETRY_SCHEMA_VERSION = "train-telemetry-v1"


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


@dataclass(frozen=True)
class ControlStateFeatures:
    """The Layer-5/6 state either side of an action.

    ``residual`` is Module 19's ``R_t`` and its five weighted components are
    kept alongside it rather than only the scalar: Module 21's state binning
    may key on a component, and recovering one from the sum afterwards is
    impossible.
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
    calls_used: int = 0
    calls_remaining: int = 0

    def __post_init__(self) -> None:
        for name in ("residual", "novelty_rate", "singleton_ratio", "facet_gap",
                     "disagreement", "unresolved_mass", "entropy"):
            _finite(getattr(self, name), name, "control state")

    def to_json(self) -> dict[str, Any]:
        return {
            "residual": self.residual, "novelty_rate": self.novelty_rate,
            "singleton_ratio": self.singleton_ratio, "facet_gap": self.facet_gap,
            "disagreement": self.disagreement,
            "unresolved_mass": self.unresolved_mass, "entropy": self.entropy,
            "active_candidates": self.active_candidates,
            "calls_used": self.calls_used, "calls_remaining": self.calls_remaining,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ControlStateFeatures":
        return cls(**{k: payload[k] for k in payload if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ActionOutcome:
    """What one executed action physically cost and factually produced.

    Empty for an action that was legal but not selected - such a record carries
    a pre-state and a legality judgement only, which is exactly what an
    unexplored branch should contribute.
    """

    physical_calls: int = 0
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
    #: Fraction of this action's candidates already held before it ran.
    redundancy: float = 0.0
    verifier_outcome: str = ""
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("physical_calls", "prompt_tokens", "generated_tokens", "cache_hits"):
            if getattr(self, name) < 0:
                raise TelemetryError(f"action outcome: {name} is negative")
        _finite(self.redundancy, "redundancy", "action outcome")

    def to_json(self) -> dict[str, Any]:
        return {
            "physical_calls": self.physical_calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "cache_hits": self.cache_hits, "parse_ok": self.parse_ok,
            "candidates_added": list(self.candidates_added),
            "candidates_supported": list(self.candidates_supported),
            "candidates_contradicted": list(self.candidates_contradicted),
            "redundancy": self.redundancy,
            "verifier_outcome": self.verifier_outcome,
            "errors": list(self.errors),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ActionOutcome":
        data = dict(payload)
        for name in ("candidates_added", "candidates_supported",
                     "candidates_contradicted", "errors"):
            if name in data:
                data[name] = tuple(data[name])
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
    action_id: str = ""
    model_role: str = ""
    reserved_class: str = ""
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
            "reserved_class": self.reserved_class, "legal": self.legal,
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


__all__ = [
    "TELEMETRY_SCHEMA_VERSION",
    "ActionOutcome",
    "ActionTelemetryRecord",
    "ControlStateFeatures",
    "TelemetryError",
    "TelemetryWriter",
    "executed_families",
    "read_telemetry",
]
