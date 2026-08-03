"""Read-only wrappers around the official AKBC evaluator."""

from cover_kbc.evaluation.official import (
    evaluator_checksum,
    load_official_evaluator,
    official_normalize_string,
    official_try_parse_number,
    relation_types,
)
from cover_kbc.evaluation.harness import (
    EvaluationReport,
    evaluate_files,
    evaluate_predictions,
    evaluate_via_cli,
)

__all__ = [
    "EvaluationReport",
    "evaluate_files",
    "evaluate_predictions",
    "evaluate_via_cli",
    "evaluator_checksum",
    "load_official_evaluator",
    "official_normalize_string",
    "official_try_parse_number",
    "relation_types",
]
