"""V3A failure attribution - diagnostics only, and structurally so.

Two layers with a hard boundary between them:

``inference_telemetry``
    Runs *inside* a run, after each query has already been decided. Records
    where every candidate got to. **Contains no gold and has no way to obtain
    any.** Safe on any split, blind included.

``gold_attribution`` / ``report``
    Run *after* a run, offline, over the persisted telemetry. Read TRAIN labels
    through the pinned official evaluator and refuse every other split.

Nothing in this package is imported by the inference path. The pipeline holds an
optional recorder and calls it once per finished query; with no recorder
attached the pre-V3A code path runs unchanged, which is what makes the
zero-prediction-change invariant structural rather than merely tested.

The one exception to "diagnostics only" is
:mod:`cover_kbc.contracts.relation_profile`, which lives with Module 1 because
it *is* Module 1's typed representation - not a diagnostic of it. In V3A even
that is declarative: it is compiled, checked against the M0 contract, and
recorded. No routing decision reads it.
"""

from cover_kbc.diagnostics.failure_state import derive_failure_state
from cover_kbc.diagnostics.gold_attribution import (
    EmptyGoldOutcome,
    GoldLeakageError,
    QueryAttribution,
    StageSurvival,
    TrainGoldAttribution,
)
from cover_kbc.diagnostics.inference_telemetry import (
    ActionProvenance,
    CandidateObservation,
    DiagnosticRecorder,
    GenerationObservation,
    InferenceTelemetryWriter,
    QueryInferenceRecord,
    TELEMETRY_VERSION,
    read_inference_telemetry,
)
from cover_kbc.diagnostics.report import (
    FailureAttributionReport,
    RelationFailureReport,
    REPORT_VERSION,
    build_report,
    write_report,
)
from cover_kbc.diagnostics.stages import (
    FailureCategory,
    FailureSearchState,
    FalsePositiveCategory,
    PipelineStage,
    STAGE_ORDER,
    STAGE_SOURCES,
)

__all__ = [
    "ActionProvenance",
    "CandidateObservation",
    "DiagnosticRecorder",
    "EmptyGoldOutcome",
    "FailureAttributionReport",
    "FailureCategory",
    "FailureSearchState",
    "FalsePositiveCategory",
    "GenerationObservation",
    "GoldLeakageError",
    "InferenceTelemetryWriter",
    "PipelineStage",
    "QueryAttribution",
    "QueryInferenceRecord",
    "REPORT_VERSION",
    "RelationFailureReport",
    "STAGE_ORDER",
    "STAGE_SOURCES",
    "StageSurvival",
    "TELEMETRY_VERSION",
    "TrainGoldAttribution",
    "build_report",
    "derive_failure_state",
    "read_inference_telemetry",
    "write_report",
]
