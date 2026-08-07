"""TRAIN controller calibration: telemetry, collection policy, readiness.

Nothing in this package invokes a model, and nothing in it changes a neural
weight. It records what the frozen system did on TRAIN, and derives the
deterministic scheduler and planner statistics the proposal says are
*"calibrated on TRAIN"* (§16, §17).
"""

from cover_kbc.controller_calibration.sufficiency import (
    SufficiencyReport,
    evaluate_sufficiency,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    TelemetryError,
    TelemetryWriter,
    executed_families,
    read_telemetry,
    successor_transitions,
)

__all__ = [
    "TELEMETRY_SCHEMA_VERSION",
    "ActionOutcome",
    "ActionTelemetryRecord",
    "ControlStateFeatures",
    "SufficiencyReport",
    "TelemetryError",
    "TelemetryWriter",
    "evaluate_sufficiency",
    "executed_families",
    "read_telemetry",
    "successor_transitions",
]
