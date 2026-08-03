"""Write prediction JSONL in exactly the official submission format.

Guarantees enforced on write:

* one row per input query, in the input file's order;
* exactly the three official fields, nothing else;
* ``ObjectEntities`` is a flat ``list[str]``;
* no two predictions in a row share the evaluator's normalised form, because
  the evaluator would collapse them and multiple surface forms of one entity
  cost precision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from cover_kbc.data.schema import SchemaError, validate_prediction_row
from cover_kbc.normalization.strings import alias_hint_key
from cover_kbc.types import Prediction, Query


def dedupe_object_entities(values: Iterable[str]) -> list[str]:
    """Drop later values that share a canonical key with an earlier one.

    Order is preserved, so the form the selector chose first survives.

    The key is the conservative *alias hint* key: the evaluator's own
    normalisation plus leading-article folding.  The evaluator collapses only
    exact normalised matches, so "The Alpha Exchange" and "Alpha Exchange"
    would reach it as two predictions, and its bipartite matcher lets one gold
    entity absorb only one of them - the second is then a guaranteed false
    positive.  Parenthetical qualifiers are preserved, so two genuinely
    different entities sharing a base string both survive.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise SchemaError(f"prediction values must be strings, got {type(value).__name__}")
        text = value.strip()
        if not text:
            continue
        key = alias_hint_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def prediction_rows(predictions: Sequence[Prediction]) -> list[dict]:
    """Convert predictions to validated official rows."""
    rows = []
    for index, prediction in enumerate(predictions):
        row = prediction.to_official_row()
        row["ObjectEntities"] = dedupe_object_entities(row["ObjectEntities"])
        validate_prediction_row(row, index=index)
        rows.append(row)
    return rows


def write_predictions(
    predictions: Sequence[Prediction],
    path: str | Path,
    *,
    expected_queries: Sequence[Query] | None = None,
) -> Path:
    """Write predictions to ``path`` as official JSONL.

    Args:
        predictions: one prediction per query, already in output order.
        path: destination file; parent directories are created.
        expected_queries: when given, the prediction set must cover exactly
            these queries in exactly this order.  A submission with a missing
            row scores as an empty prediction, so this check is worth having.
    """
    rows = prediction_rows(predictions)

    if expected_queries is not None:
        expected = [(q.subject, q.relation) for q in expected_queries]
        actual = [(r["SubjectEntity"], r["Relation"]) for r in rows]
        if actual != expected:
            missing = set(expected) - set(actual)
            extra = set(actual) - set(expected)
            detail = []
            if missing:
                detail.append(f"{len(missing)} missing, e.g. {sorted(missing)[:3]}")
            if extra:
                detail.append(f"{len(extra)} unexpected, e.g. {sorted(extra)[:3]}")
            if not detail:
                detail.append("same rows but different order")
            raise SchemaError("prediction rows do not match the input queries: " + "; ".join(detail))

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def write_trace(predictions: Sequence[Prediction], path: str | Path) -> Path:
    """Write the full per-query audit trail (candidates, evidence, provenance)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction.to_trace(), ensure_ascii=False) + "\n")
    return out
