"""Whether a finished run is a submission, and the numbers behind the answer.

Audit 0078. The frozen calibrated V3 TEST run wrote 475 prediction rows, logged
39 ``PendingActionNotConsumed`` failures, turned each of them into an empty
``PIPELINE_ERROR`` row, and exited 0. Nothing downstream could distinguish that
file from a finished submission, because the only signal was a row count and the
row count was right.

The distinction this module draws is between *an answer the system decided on*
and *a hole where a query used to be*. An empty prediction is a legitimate
answer - the evaluator scores precision 1.0 for it, and §10.3's "failed recall is
not gold is empty" makes abstention meaningful. An empty prediction produced by
an exception is not an answer at all; it is a missing row wearing an answer's
shape.

Scope is deliberate. Only blind submission splits are refused. A TRAIN
collection or a diagnostic run is *supposed* to be able to contain a failed row
and study it, and breaking those workflows to protect a submission would trade
one silent failure for another.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from cover_kbc.types import EmptyReason

#: Exceptions that mean an orchestration invariant was violated rather than a
#: query being hard. These are never acceptable in a submission: each one is a
#: row the system was still working on when it was asked for an answer.
INVARIANT_ERROR_NAMES: frozenset[str] = frozenset({
    "PendingActionNotConsumed",
    "CorruptPendingAction",
    "SelectionInvariantError",
    "AccountingInvariantError",
})

READY = "SUBMISSION_READY"
NOT_READY = "SUBMISSION_NOT_READY"


def _error_name(entry: Mapping[str, Any]) -> str:
    """The exception class name recorded for one failed query."""
    return str(entry.get("error", "")).split(":", 1)[0].strip()


def run_accounting(queries: Sequence[Any], result: Any) -> dict[str, Any]:
    """Per-run counts, computed from what the run actually produced.

    ``failed_queries`` counts rows the pipeline caught an exception on.
    ``pipeline_error_rows`` counts rows whose prediction carries
    ``PIPELINE_ERROR`` - the two should agree, and a disagreement is itself
    reported rather than smoothed over, because it would mean a fallback row was
    written without an error being recorded or the reverse.
    """
    errors = list(getattr(result, "errors", []) or [])
    predictions = list(getattr(result, "predictions", []) or [])
    by_name: dict[str, int] = {}
    for entry in errors:
        name = _error_name(entry)
        by_name[name] = by_name.get(name, 0) + 1
    unresolved = sum(
        count for name, count in by_name.items() if name in INVARIANT_ERROR_NAMES
    )
    pipeline_error_rows = sum(
        1 for prediction in predictions
        if getattr(prediction, "empty_reason", None) is EmptyReason.PIPELINE_ERROR
    )
    return {
        "total_queries": len(queries),
        "prediction_rows": len(predictions),
        "successful_queries": len(queries) - len(errors),
        "failed_queries": len(errors),
        "query_errors": by_name,
        "unresolved_invariant_errors": unresolved,
        "pipeline_error_rows": pipeline_error_rows,
        "error_rows_match_pipeline_error_rows": len(errors) == pipeline_error_rows,
        "failed_identities": sorted(
            f"{entry.get('SubjectEntity', '')}|{entry.get('Relation', '')}"
            for entry in errors
        ),
    }


def submission_verdict(split: str, accounting: Mapping[str, Any]) -> dict[str, Any]:
    """May this run be submitted?

    Refused when any query failed at all, and refused with a named blocker when
    the failure was an orchestration invariant. A row count alone is never
    enough: ``prediction_rows == total_queries`` holds for a run in which every
    single query crashed.
    """
    blockers: list[str] = []
    if accounting.get("unresolved_invariant_errors"):
        names = sorted(
            name for name in (accounting.get("query_errors") or {})
            if name in INVARIANT_ERROR_NAMES
        )
        blockers.append(
            f"{accounting['unresolved_invariant_errors']} unresolved orchestration "
            f"invariant error(s) {names}: these rows were abandoned mid-query and "
            "their empty predictions are not answers"
        )
    elif accounting.get("failed_queries"):
        blockers.append(
            f"{accounting['failed_queries']} query error(s) "
            f"{sorted(accounting.get('query_errors') or {})}"
        )
    if accounting.get("prediction_rows") != accounting.get("total_queries"):
        blockers.append(
            f"{accounting.get('prediction_rows')} prediction row(s) for "
            f"{accounting.get('total_queries')} quer(ies)"
        )
    if not accounting.get("error_rows_match_pipeline_error_rows", True):
        blockers.append(
            f"{accounting.get('failed_queries')} recorded error(s) but "
            f"{accounting.get('pipeline_error_rows')} PIPELINE_ERROR row(s)"
        )
    return {
        "split": split,
        "ready": not blockers,
        "state": READY if not blockers else NOT_READY,
        "blockers": blockers,
    }


__all__ = [
    "INVARIANT_ERROR_NAMES",
    "NOT_READY",
    "READY",
    "run_accounting",
    "submission_verdict",
]
