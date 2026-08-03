"""Read-only access to the official dataset, and official-format output."""

from cover_kbc.data.loader import (
    Dataset,
    gold_lookup,
    load_all_splits,
    load_dataset,
    load_jsonl_rows,
)
from cover_kbc.data.schema import (
    DatasetRow,
    SchemaError,
    check_no_duplicate_keys,
    parse_row,
    validate_prediction_row,
    validate_raw_row,
)
from cover_kbc.data.writer import (
    dedupe_object_entities,
    prediction_rows,
    write_predictions,
    write_trace,
)

__all__ = [
    "Dataset",
    "DatasetRow",
    "SchemaError",
    "check_no_duplicate_keys",
    "dedupe_object_entities",
    "gold_lookup",
    "load_all_splits",
    "load_dataset",
    "load_jsonl_rows",
    "parse_row",
    "prediction_rows",
    "validate_prediction_row",
    "validate_raw_row",
    "write_predictions",
    "write_trace",
]
