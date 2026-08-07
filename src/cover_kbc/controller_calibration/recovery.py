"""Reconcile a run's artifacts to the checkpoint before resuming.

A row is a transaction: its telemetry, prediction, coverage and accounting all
become durable together, and the checkpoint's ``completed_rows`` is the commit
record. But the commit is several file writes, and a process killed between the
first and the last leaves the earlier ones on disk describing a row the
checkpoint never accepted.

Audit 0043 C-04 measured what happened next. The telemetry writer's duplicate
guard - correctly - refused to write the replayed row a second time, so *every*
subsequent resume aborted at the same point and the only way forward was to
hand-edit a JSONL file in the middle of a multi-hour session. Safe against
silent corruption, and unusable.

This module removes the hand-editing. On resume, before anything is opened for
append, artifacts are rolled back to the commit boundary: telemetry is first
validated against the exact byte prefix the checkpoint acknowledged, and only
bytes beyond that prefix are discarded so the normal replay path can re-run the
interrupted row cleanly.

Three rules keep this from becoming a data-loss hazard of its own:

* **The checkpoint is the only authority.** Nothing here infers what "should"
  have been committed from the artifacts themselves; it only removes what the
  checkpoint does not vouch for.
* **Committed telemetry is never touched.** The reconciliation is a truncation
  of bytes outside the checkpoint-acknowledged prefix, never a rewrite of
  committed records.
* **An inconsistency it cannot explain is refused, not repaired.** If what
  remains after rollback does not match the committed rows, that is not an
  interrupted commit - it is a corrupt run, and guessing would be worse than
  stopping.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.controller_calibration.checkpoint import (
    ResumeRefused,
    TelemetryCommitBoundary,
)
from cover_kbc.controller_calibration.telemetry import (
    ActionTelemetryRecord,
    TelemetryError,
)


@dataclass(frozen=True)
class ReconciliationReport:
    """What resume had to roll back, so the manifest can say so."""

    #: Telemetry bytes removed because they are outside the committed prefix.
    telemetry_bytes_truncated: int = 0
    #: Telemetry lines removed because their row was never committed.
    telemetry_records_dropped: int = 0
    #: Which rows those lines belonged to.
    telemetry_rows_dropped: tuple[int, ...] = ()
    #: Prediction lines removed for the same reason.
    prediction_lines_dropped: int = 0
    #: A final line cut off mid-write by the kill.
    torn_lines_dropped: int = 0
    #: Coverage counts rebuilt from the reconciled telemetry.
    coverage_rebuilt: bool = False
    notes: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """True when the previous process stopped on a transaction boundary."""
        return not (self.telemetry_records_dropped or self.prediction_lines_dropped
                    or self.torn_lines_dropped or self.telemetry_bytes_truncated)

    def to_json(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "telemetry_bytes_truncated": self.telemetry_bytes_truncated,
            "telemetry_records_dropped": self.telemetry_records_dropped,
            "telemetry_rows_dropped": list(self.telemetry_rows_dropped),
            "prediction_lines_dropped": self.prediction_lines_dropped,
            "torn_lines_dropped": self.torn_lines_dropped,
            "coverage_rebuilt": self.coverage_rebuilt,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        if self.clean:
            return "resume reconciliation: artifacts already match the checkpoint"
        parts = [f"{self.telemetry_bytes_truncated} telemetry byte(s)",
                 f"{self.telemetry_records_dropped} telemetry record(s)"]
        if self.telemetry_rows_dropped:
            parts.append(f"from row(s) {list(self.telemetry_rows_dropped)}")
        parts.append(f"{self.prediction_lines_dropped} prediction line(s)")
        if self.torn_lines_dropped:
            parts.append(f"{self.torn_lines_dropped} torn line(s)")
        return ("resume reconciliation: rolled back an interrupted row commit - "
                + ", ".join(parts) + " discarded")


def _atomic_write_lines(path: Path, lines: Sequence[str]) -> None:
    """Replace a file's contents atomically, so recovery is itself crash-safe."""
    scratch = path.with_suffix(path.suffix + ".partial")
    scratch.write_text(
        "".join(line if line.endswith("\n") else line + "\n" for line in lines),
        encoding="utf-8")
    scratch.replace(path)


def _atomic_truncate_bytes(path: Path, byte_length: int) -> None:
    """Atomically replace ``path`` with its first ``byte_length`` bytes."""
    scratch = path.with_suffix(path.suffix + ".partial")
    data = path.read_bytes()[:byte_length] if path.is_file() else b""
    scratch.write_bytes(data)
    scratch.replace(path)


def _split_lines(path: Path) -> tuple[list[str], int]:
    """Non-empty lines, dropping a final line the kill cut in half.

    Only the *last* line may be torn - a write is one ``write`` plus one
    ``flush`` of one complete line, so a broken line anywhere else is real
    corruption and is left in place for the caller's validation to refuse.
    """
    if not path.is_file():
        return [], 0
    raw = [line for line in path.read_text(encoding="utf-8").splitlines()
           if line.strip()]
    if not raw:
        return [], 0
    try:
        json.loads(raw[-1])
    except json.JSONDecodeError:
        return raw[:-1], 1
    return raw, 0


@dataclass(frozen=True)
class TelemetryPrefixValidation:
    """A committed telemetry prefix that has been validated against checkpoint."""

    records: tuple[ActionTelemetryRecord, ...]
    suffix: bytes = b""

    @property
    def suffix_bytes(self) -> int:
        return len(self.suffix)


def _parse_committed_telemetry_prefix(
    path: Path,
    payload: bytes,
) -> tuple[ActionTelemetryRecord, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ResumeRefused(
            f"{path}: committed telemetry prefix is not valid UTF-8: {error}"
        ) from None

    records: list[ActionTelemetryRecord] = []
    identities: set[tuple[int, int, str]] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = ActionTelemetryRecord.from_json(json.loads(line))
        except json.JSONDecodeError as error:
            raise ResumeRefused(
                f"{path}:{number}: committed telemetry prefix contains malformed "
                f"JSONL ({error}); this is committed corruption"
            ) from None
        except (KeyError, TypeError, TelemetryError) as error:
            raise ResumeRefused(
                f"{path}:{number}: committed telemetry prefix contains invalid "
                f"telemetry ({error}); this is committed corruption"
            ) from None
        identity = (record.row_index, record.round_index, record.operation_id)
        if identity in identities:
            raise ResumeRefused(
                f"{path}:{number}: duplicate committed telemetry identity "
                f"{identity}; this is committed corruption"
            )
        identities.add(identity)
        records.append(record)
    return tuple(records)


def capture_telemetry_commit_boundary(path: Path) -> TelemetryCommitBoundary:
    """Describe the current telemetry file as a checkpoint-committed prefix."""
    payload = path.read_bytes() if path.is_file() else b""
    records = _parse_committed_telemetry_prefix(Path(path), payload)
    return TelemetryCommitBoundary(
        byte_length=len(payload),
        record_count=len(records),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def validate_committed_telemetry_prefix(
    path: Path,
    boundary: TelemetryCommitBoundary,
    *,
    allow_uncommitted_tail: bool,
) -> TelemetryPrefixValidation:
    """Validate the telemetry bytes that the checkpoint has acknowledged.

    Bytes after the acknowledged prefix are not interpreted as committed data.
    Resume may remove them; the final gate requires there to be none.
    """
    source = Path(path)
    payload = source.read_bytes() if source.is_file() else b""
    if len(payload) < boundary.byte_length:
        raise ResumeRefused(
            f"{source} is {len(payload)} byte(s), but the checkpoint committed "
            f"{boundary.byte_length}; committed telemetry bytes are missing"
        )

    prefix = payload[:boundary.byte_length]
    actual_hash = hashlib.sha256(prefix).hexdigest()
    if actual_hash != boundary.sha256:
        raise ResumeRefused(
            f"{source}: committed telemetry SHA-256 mismatch; checkpoint has "
            f"{boundary.sha256}, file prefix has {actual_hash}"
        )

    records = _parse_committed_telemetry_prefix(source, prefix)
    if len(records) != boundary.record_count:
        raise ResumeRefused(
            f"{source}: committed telemetry prefix contains {len(records)} "
            f"record(s), but the checkpoint committed {boundary.record_count}"
        )

    suffix = payload[boundary.byte_length:]
    if suffix and not allow_uncommitted_tail:
        raise ResumeRefused(
            f"{source} has {len(suffix)} byte(s) beyond the checkpoint-committed "
            "telemetry prefix"
        )
    return TelemetryPrefixValidation(records=records, suffix=suffix)


def _inspect_uncommitted_suffix(
    suffix: bytes,
) -> tuple[int, tuple[int, ...], int]:
    """Best-effort report for bytes that are outside the committed prefix."""
    if not suffix:
        return 0, (), 0
    try:
        text = suffix.decode("utf-8")
    except UnicodeDecodeError:
        return 0, (), 1

    records = 0
    torn = 0
    rows: list[int] = []
    for line in (line for line in text.splitlines() if line.strip()):
        try:
            payload = json.loads(line)
            row_index = int(payload["row_index"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            torn += 1
            continue
        records += 1
        if row_index not in rows:
            rows.append(row_index)
    return records, tuple(rows), torn


def reconcile_to_checkpoint(
    *,
    telemetry_path: Path,
    predictions_path: Path,
    committed_rows: Iterable[int],
    committed_queries: Mapping[int, tuple[str, str]],
    telemetry_boundary: TelemetryCommitBoundary,
    coverage=None,
) -> ReconciliationReport:
    """Roll every artifact back to the checkpoint's committed-row boundary.

    Args:
        telemetry_path: the run's ``train_telemetry.jsonl``.
        predictions_path: the run's ``predictions.jsonl``.
        committed_rows: ``checkpoint.completed_rows`` - the authority.
        committed_queries: ``row_index -> (subject, relation)`` for the
            committed rows, used to check that what survives really is them.
        telemetry_boundary: the exact telemetry prefix the checkpoint commits.
        coverage: the run's :class:`CoverageLedger`, restored from disk. Its
            executed counts are rebuilt from the reconciled telemetry, which is
            the canonical record of what ran.

    Returns:
        A report of what was discarded, for the manifest.

    Raises:
        ResumeRefused: if the surviving artifacts do not correspond to the
            committed rows. That is not an interrupted commit and must not be
            silently repaired.
    """
    committed = set(committed_rows)
    notes: list[str] = []
    torn = 0

    # -- telemetry ---------------------------------------------------------
    telemetry = validate_committed_telemetry_prefix(
        Path(telemetry_path), telemetry_boundary, allow_uncommitted_tail=True)
    dropped, dropped_rows, torn_here = _inspect_uncommitted_suffix(
        telemetry.suffix)
    torn += torn_here
    if telemetry.suffix:
        _atomic_truncate_bytes(Path(telemetry_path),
                               telemetry_boundary.byte_length)
        notes.append(
            f"telemetry: discarded {telemetry.suffix_bytes} byte(s), "
            f"{dropped} complete record(s) for uncommitted row(s) "
            f"{list(dropped_rows)}")

    # -- predictions -------------------------------------------------------
    # One prediction per committed row, appended in completion order. Only one
    # row can ever be in flight, so any excess is that row's - and completion
    # order is not row order after a retry, which is why what survives is
    # checked as a *multiset* rather than positionally.
    pred_lines, torn_here = _split_lines(Path(predictions_path))
    torn += torn_here
    pred_dropped = 0
    if len(pred_lines) > len(committed):
        pred_dropped = len(pred_lines) - len(committed)
        pred_lines = pred_lines[: len(committed)]
    if pred_dropped or torn_here:
        _atomic_write_lines(Path(predictions_path), pred_lines)
        notes.append(
            f"predictions: discarded {pred_dropped} line(s) for an uncommitted row")

    if len(pred_lines) != len(committed):
        raise ResumeRefused(
            f"{predictions_path} holds {len(pred_lines)} prediction(s) for "
            f"{len(committed)} committed row(s); the run cannot be reconciled to "
            "its checkpoint and is refused rather than guessed at"
        )
    expected = sorted(committed_queries[row] for row in committed
                      if row in committed_queries)
    if len(expected) == len(pred_lines):
        actual = sorted(
            (str(json.loads(line).get("SubjectEntity", "")),
             str(json.loads(line).get("Relation", "")))
            for line in pred_lines)
        if actual != expected:
            raise ResumeRefused(
                f"{predictions_path} does not describe the committed rows; the "
                "surviving predictions and the checkpoint disagree about which "
                "rows are done"
            )

    # -- coverage ----------------------------------------------------------
    rebuilt = False
    if coverage is not None and (telemetry.suffix or pred_dropped or torn):
        executed: dict[str, int] = {}
        for record in telemetry.records:
            if record.executed:
                family = str(record.action_family)
                executed[family] = executed.get(family, 0) + 1
        for family, slot in coverage.families.items():
            slot.executed = executed.get(family, 0)
            slot.succeeded = executed.get(family, 0)
            slot.failed = 0
        for family, count in executed.items():
            slot = coverage._slot(family)
            slot.surfaced = True
            slot.executed = count
            slot.succeeded = count
        rebuilt = True
        notes.append("coverage: executed counts rebuilt from reconciled telemetry")

    return ReconciliationReport(
        telemetry_bytes_truncated=telemetry.suffix_bytes,
        telemetry_records_dropped=dropped,
        telemetry_rows_dropped=tuple(dropped_rows),
        prediction_lines_dropped=pred_dropped,
        torn_lines_dropped=torn,
        coverage_rebuilt=rebuilt,
        notes=tuple(notes),
    )


__all__ = [
    "ReconciliationReport",
    "TelemetryPrefixValidation",
    "capture_telemetry_commit_boundary",
    "reconcile_to_checkpoint",
    "validate_committed_telemetry_prefix",
]
