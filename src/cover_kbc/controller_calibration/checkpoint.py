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

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

#: Written into the checkpoint so a future format change is detectable.
CHECKPOINT_VERSION = "collection-checkpoint-v1"


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


@dataclass
class CollectionCheckpoint:
    """Which rows are done, plus the accounting to restore alongside them."""

    identity: RunIdentity
    completed_rows: list[int]
    failed_rows: list[int]
    counters: dict[str, Any]
    checkpoint_version: str = CHECKPOINT_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "checkpoint_version": self.checkpoint_version,
            "identity": self.identity.to_json(),
            "completed_rows": sorted(self.completed_rows),
            "failed_rows": sorted(self.failed_rows),
            "counters": dict(self.counters),
        }

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
            failed_rows=list(payload.get("failed_rows", [])),
            counters=dict(payload.get("counters", {})),
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
    "resume_from",
]
