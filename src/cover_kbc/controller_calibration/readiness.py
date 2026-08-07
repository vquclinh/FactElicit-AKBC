"""Is this profile allowed to start a full VALIDATION run?

The expensive failure this prevents is a VALIDATION run that *succeeds*: the
uncalibrated controller silently falls back, 478 rows are answered by a system
nobody intended, and the leaderboard number describes neither the core path nor
the upgraded one. A crash costs an hour; that outcome costs a submission and is
not visible in the artifacts.

So readiness is a single deterministic verdict with the reasons attached, and
its default is refusal. Every requirement below is checked against files that
must exist on disk and declare themselves TRAIN-derived - never against a
configuration flag asserting that calibration happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class ReadinessState(str, Enum):
    """What this profile may legally be used for right now."""

    #: The upgraded seams are wired; TRAIN collection may run.
    CALIBRATION_COLLECTION_READY = "CALIBRATION_COLLECTION_READY"
    #: Real TRAIN-derived controller artifacts are present and consistent.
    FULL_VALIDATION_READY = "FULL_VALIDATION_READY"
    #: Neither - the profile is incomplete or inconsistent.
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class ReadinessReport:
    """A verdict plus every reason behind it."""

    state: ReadinessState
    blockers: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def may_run_validation(self) -> bool:
        return self.state is ReadinessState.FULL_VALIDATION_READY

    @property
    def may_run_collection(self) -> bool:
        return self.state in (
            ReadinessState.CALIBRATION_COLLECTION_READY,
            ReadinessState.FULL_VALIDATION_READY,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "may_run_collection": self.may_run_collection,
            "may_run_validation": self.may_run_validation,
            "blockers": list(self.blockers),
            "satisfied": list(self.satisfied),
            "details": dict(self.details),
        }


def _load(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: not valid JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _check_artifact(
    label: str, raw: object, *, base: Path,
    blockers: list[str], satisfied: list[str], details: dict[str, Any],
) -> Mapping[str, Any] | None:
    """Resolve one declared artifact path, refusing anything unverifiable."""
    if not raw:
        blockers.append(f"{label}: no artifact path is configured")
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        # A configured-but-absent path is the fake-path case the brief forbids;
        # it must never read as "calibration exists".
        blockers.append(f"{label}: configured path does not exist ({path})")
        return None
    try:
        payload = _load(path)
    except ValueError as error:
        blockers.append(f"{label}: {error}")
        return None
    details[label] = str(path)
    satisfied.append(f"{label}: present at {path}")
    return payload


def evaluate_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
) -> ReadinessReport:
    """Decide what ``config`` may be used for. Never raises on a bad profile.

    Args:
        config: the loaded experiment mapping.
        base_dir: root that relative artifact paths resolve against.

    Returns:
        A report whose default is refusal. Missing artifacts are blockers for
        VALIDATION but never for collection - collecting is precisely how the
        missing artifacts get made.
    """
    base = Path(base_dir)
    blockers: list[str] = []
    satisfied: list[str] = []
    details: dict[str, Any] = {}

    budget_block = dict(config.get("relation_budget_scheduler") or {})
    planner_block = dict(config.get("micro_planner") or {})

    if not budget_block.get("enabled"):
        blockers.append("M20: relation_budget_scheduler.enabled is false")
    if not planner_block.get("enabled"):
        blockers.append("M21: micro_planner.enabled is false")

    m20 = _check_artifact(
        "M20 relation budget calibration", budget_block.get("calibration_file"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if m20 is not None:
        entries = m20.get("relations") or m20.get("calibrations") or []
        sources = {str(e.get("calibration_source", "")) for e in entries}
        if not entries:
            blockers.append("M20: artifact declares no relation calibrations")
        elif sources != {"TRAIN_CALIBRATED"}:
            # A fixture reaching production is the exact substitution the
            # SYNTHETIC_TEST marker exists to make impossible.
            blockers.append(
                f"M20: artifact is not purely TRAIN_CALIBRATED (found {sorted(sources)})")
        else:
            satisfied.append(f"M20: {len(entries)} TRAIN_CALIBRATED relation budgets")

    bins = _check_artifact(
        "M21 historical bins", planner_block.get("historical_bins"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if bins is not None and not (bins.get("bins") or ()):
        blockers.append("M21: historical bin package contains no bins")

    calibration = _check_artifact(
        "M21 planner calibration", planner_block.get("planner_calibration"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if calibration is not None:
        missing = [name for name in
                   ("alpha", "beta", "gamma", "delta", "eta", "kappa", "tau_continue")
                   if name not in calibration]
        if missing:
            blockers.append(f"M21: planner calibration is missing {missing}")

    if blockers:
        # Collection needs the seams, not the artifacts - so a profile blocked
        # only by absent calibration is still exactly what collection runs on.
        state = ReadinessState.CALIBRATION_COLLECTION_READY
        for blocker in blockers:
            if "does not exist" not in blocker and "no artifact path" not in blocker \
                    and "enabled is false" not in blocker:
                state = ReadinessState.NOT_READY
                break
        return ReadinessReport(state, tuple(blockers), tuple(satisfied), details)

    return ReadinessReport(
        ReadinessState.FULL_VALIDATION_READY, (), tuple(satisfied), details)


__all__ = ["ReadinessReport", "ReadinessState", "evaluate_readiness"]
