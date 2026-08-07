"""Checkpointing and resume for a long TRAIN collection run.

Hours of frozen-model inference must not live only in memory, and a resumed run
must not splice together observations that describe two different systems. So
resume is gated on an exact identity: the same TRAIN file, the same repository,
the same config, the same model revisions, the same collection policy and the
same telemetry schema.

Every one of those can change the meaning of a recorded outcome. A resume that
merged across any of them would produce bins that look well-supported and are
quietly incomparable - the kind of corruption that shows up only as a bad
leaderboard score weeks later. Refusing is cheap; the run restarts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: Written into the checkpoint so a future format change is detectable.
#:
#: ``v3`` (Audit 0045 C-16) adds an explicit committed telemetry prefix. The
#: completed row set remains the row-commit authority, but the checkpoint now
#: also records the exact telemetry bytes that were acknowledged by that commit.
#: On resume, bytes outside that prefix are an interrupted tail; any mutation
#: inside it is committed corruption and is refused.
CHECKPOINT_VERSION = "collection-checkpoint-v3"

EMPTY_TELEMETRY_SHA256 = hashlib.sha256(b"").hexdigest()


class ResumeRefused(RuntimeError):
    """A checkpoint described a different run than the one being started."""


@dataclass(frozen=True)
class RunIdentity:
    """Everything that must match for two partial runs to be one run."""

    train_sha256: str
    repo_sha: str
    config_sha256: str
    enumerator_model_id: str
    enumerator_revision: str
    verifier_model_id: str
    verifier_revision: str
    collection_policy_version: str
    telemetry_schema_version: str
    total_rows: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RunIdentity":
        return cls(**{k: payload[k] for k in cls.__dataclass_fields__})

    def differences(self, other: "RunIdentity") -> tuple[str, ...]:
        return tuple(
            f"{name}: checkpoint={getattr(other, name)!r} current={getattr(self, name)!r}"
            for name in self.__dataclass_fields__
            if getattr(self, name) != getattr(other, name)
        )


@dataclass(frozen=True)
class TelemetryCommitBoundary:
    """The telemetry prefix acknowledged by the last checkpoint commit."""

    byte_length: int = 0
    record_count: int = 0
    sha256: str = EMPTY_TELEMETRY_SHA256

    def __post_init__(self) -> None:
        if self.byte_length < 0:
            raise ResumeRefused("telemetry_committed_bytes cannot be negative")
        if self.record_count < 0:
            raise ResumeRefused("telemetry_committed_records cannot be negative")
        if len(self.sha256) != 64:
            raise ResumeRefused(
                "telemetry_committed_sha256 must be a SHA-256 hex digest"
            )
        try:
            int(self.sha256, 16)
        except ValueError as error:
            raise ResumeRefused(
                "telemetry_committed_sha256 must be a SHA-256 hex digest"
            ) from error

    @classmethod
    def empty(cls) -> "TelemetryCommitBoundary":
        return cls()

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "TelemetryCommitBoundary":
        try:
            return cls(
                byte_length=int(payload["telemetry_committed_bytes"]),
                record_count=int(payload["telemetry_committed_records"]),
                sha256=str(payload["telemetry_committed_sha256"]),
            )
        except KeyError as error:
            raise ResumeRefused(
                f"checkpoint is missing committed telemetry boundary field "
                f"{error.args[0]!r}; refusing to infer it from artifacts"
            ) from None

    def to_json(self) -> dict[str, Any]:
        return {
            "telemetry_committed_bytes": self.byte_length,
            "telemetry_committed_records": self.record_count,
            "telemetry_committed_sha256": self.sha256,
        }


@dataclass
class CollectionCheckpoint:
    """Which rows are done, plus the accounting to restore alongside them.

    ``completed_rows`` is **the durable commit boundary**. A row is in it only
    once its telemetry, prediction, coverage and accounting have all been
    written, so on resume it is the authority against which every artifact is
    reconciled: anything on disk describing a row that is not listed here
    belongs to an interrupted transaction and is not committed calibration
    data (Audit 0043 C-04).

    ``unresolved_failed_rows`` and ``failure_history`` answer two different
    questions and are therefore two fields. The first is "is the corpus
    incomplete?", which the exit gate reads. The second is "what went wrong
    along the way?", which is diagnostics and survives a successful retry.
    """

    identity: RunIdentity
    completed_rows: list[int]
    #: Rows that failed and have not since completed. Empties as retries land.
    unresolved_failed_rows: list[int]
    counters: dict[str, Any]
    #: Every failed attempt, in order. Never pruned by a later success.
    failure_history: list[dict[str, Any]] = field(default_factory=list)
    telemetry_committed: TelemetryCommitBoundary = field(
        default_factory=TelemetryCommitBoundary.empty
    )
    checkpoint_version: str = CHECKPOINT_VERSION

    def to_json(self) -> dict[str, Any]:
        payload = {
            "checkpoint_version": self.checkpoint_version,
            "identity": self.identity.to_json(),
            "completed_rows": sorted(self.completed_rows),
            "unresolved_failed_rows": sorted(self.unresolved_failed_rows),
            "failure_history": list(self.failure_history),
            "counters": dict(self.counters),
        }
        payload.update(self.telemetry_committed.to_json())
        return payload

    def save(self, path: str | Path) -> Path:
        """Write atomically - a torn checkpoint is worse than none."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = target.with_suffix(target.suffix + ".partial")
        scratch.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        scratch.replace(target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> "CollectionCheckpoint":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        version = payload.get("checkpoint_version")
        if version != CHECKPOINT_VERSION:
            raise ResumeRefused(
                f"checkpoint format {version!r} is not {CHECKPOINT_VERSION!r}"
            )
        return cls(
            identity=RunIdentity.from_json(payload["identity"]),
            completed_rows=list(payload.get("completed_rows", [])),
            unresolved_failed_rows=list(payload.get("unresolved_failed_rows", [])),
            counters=dict(payload.get("counters", {})),
            failure_history=list(payload.get("failure_history", [])),
            telemetry_committed=TelemetryCommitBoundary.from_json(payload),
            checkpoint_version=version,
        )


def resume_from(path: str | Path, identity: RunIdentity) -> CollectionCheckpoint:
    """Load a checkpoint, refusing anything that is not the same run.

    Raises:
        ResumeRefused: on a missing, malformed or mismatched checkpoint. Never
            partially merges: an incompatible checkpoint is refused whole.
    """
    source = Path(path)
    if not source.is_file():
        raise ResumeRefused(f"no checkpoint to resume from at {source}")
    try:
        checkpoint = CollectionCheckpoint.load(source)
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ResumeRefused(f"{source}: unreadable checkpoint: {error}") from error

    drift = identity.differences(checkpoint.identity)
    if drift:
        raise ResumeRefused(
            f"{source} describes a different run and cannot be merged:\n  "
            + "\n  ".join(drift)
        )
    return checkpoint


__all__ = [
    "CHECKPOINT_VERSION",
    "CollectionCheckpoint",
    "ResumeRefused",
    "RunIdentity",
    "TelemetryCommitBoundary",
    "resume_from",
]
