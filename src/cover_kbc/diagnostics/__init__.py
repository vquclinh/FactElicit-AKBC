"""Runtime diagnostics for COVER-KBC.

The public F1 runner keeps only gold-free runtime telemetry. Historical
TRAIN gold-attribution reports were development artifacts and are not part of
the released inference path.
"""

from cover_kbc.diagnostics.failure_state import derive_failure_state
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
    "FailureCategory",
    "FailureSearchState",
    "FalsePositiveCategory",
    "GenerationObservation",
    "InferenceTelemetryWriter",
    "PipelineStage",
    "QueryInferenceRecord",
    "STAGE_ORDER",
    "STAGE_SOURCES",
    "TELEMETRY_VERSION",
    "derive_failure_state",
    "read_inference_telemetry",
]
