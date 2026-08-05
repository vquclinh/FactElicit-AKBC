"""Dataset loading, schema validation and prediction serialisation."""

from __future__ import annotations

import json

import pytest

from cover_kbc.data.loader import load_dataset, load_jsonl_rows
from cover_kbc.data.schema import SchemaError, check_no_duplicate_keys, validate_prediction_row
from cover_kbc.data.writer import dedupe_object_entities, write_predictions
from cover_kbc.paths import SPLIT_FILES
from cover_kbc.types import Prediction, Query

from conftest import write_jsonl


# --- official splits -------------------------------------------------------


@pytest.mark.parametrize("split", ["train", "val", "test"])
def test_official_split_round_trips_byte_for_byte(split):
    """Loading must never alter the official rows."""
    dataset = load_dataset(split)
    original = [json.loads(line) for line in SPLIT_FILES[split].read_text().splitlines() if line.strip()]
    assert dataset.to_official_rows() == original


@pytest.mark.parametrize("split", ["train", "val", "test"])
def test_row_order_is_preserved(split):
    dataset = load_dataset(split)
    assert [row.row_index for row in dataset.rows] == list(range(len(dataset)))


def test_test_split_is_blind_and_val_is_not():
    assert load_dataset("test").is_blind
    assert not load_dataset("val").is_blind


def test_gold_is_alias_lists():
    """The July 2026 gold format is list[list[str]], not list[str]."""
    val = load_dataset("val")
    non_empty = [row for row in val.rows if not row.is_empty]
    assert non_empty
    for row in non_empty[:20]:
        assert all(isinstance(aliases, tuple) and aliases for aliases in row.object_entities)


def test_official_splits_have_no_duplicate_keys():
    for split in ("train", "val", "test"):
        dataset = load_dataset(split)
        check_no_duplicate_keys(list(dataset.rows), source=split)


# --- schema validation -----------------------------------------------------


def test_legacy_flat_gold_is_wrapped(tmp_path):
    path = write_jsonl(
        tmp_path / "legacy.jsonl",
        [{"SubjectEntity": "S", "Relation": "hasArea", "ObjectEntities": ["10"]}],
    )
    rows = load_jsonl_rows(path)
    assert rows[0].object_entities == (("10",),)
    assert rows[0].legacy_flat_gold
    # Round-trip preserves the original flat shape.
    assert rows[0].to_official_row()["ObjectEntities"] == ["10"]


@pytest.mark.parametrize(
    "row",
    [
        {"SubjectEntity": "S", "Relation": "R"},  # missing ObjectEntities
        {"SubjectEntity": "", "Relation": "R", "ObjectEntities": []},  # empty subject
        {"SubjectEntity": "S", "Relation": "R", "ObjectEntities": "x"},  # not a list
        {"SubjectEntity": "S", "Relation": "R", "ObjectEntities": [[]]},  # empty alias list
        {"SubjectEntity": "S", "Relation": "R", "ObjectEntities": [{"a": 1}]},  # bad type
    ],
)
def test_malformed_rows_are_rejected(tmp_path, row):
    path = write_jsonl(tmp_path / "bad.jsonl", [row])
    with pytest.raises(SchemaError):
        load_jsonl_rows(path)


def test_duplicate_subject_relation_is_rejected(tmp_path):
    rows = [
        {"SubjectEntity": "S", "Relation": "hasArea", "ObjectEntities": [["1"]]},
        {"SubjectEntity": "S", "Relation": "hasArea", "ObjectEntities": [["2"]]},
    ]
    path = write_jsonl(tmp_path / "dup.jsonl", rows)
    with pytest.raises(SchemaError, match="duplicate"):
        load_dataset("val", path=path)


def test_invalid_json_line_is_rejected(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"SubjectEntity": "S", "Relation": "R", "ObjectEntities": []}\nnot json\n')
    with pytest.raises(SchemaError, match="invalid JSON"):
        load_jsonl_rows(path)


# --- prediction serialisation ---------------------------------------------


def test_prediction_row_has_exactly_the_official_fields():
    prediction = Prediction(subject="S", relation="R", object_entities=["a"])
    assert set(prediction.to_official_row()) == {"SubjectEntity", "Relation", "ObjectEntities"}


def test_prediction_row_rejects_extra_fields():
    with pytest.raises(SchemaError, match="unexpected field"):
        validate_prediction_row(
            {"SubjectEntity": "S", "Relation": "R", "ObjectEntities": [], "score": 1.0}, index=0
        )


def test_prediction_row_rejects_nested_objects():
    with pytest.raises(SchemaError, match="flat strings"):
        validate_prediction_row(
            {"SubjectEntity": "S", "Relation": "R", "ObjectEntities": [["a"]]}, index=0
        )


def test_dedupe_collapses_only_what_the_evaluator_would_collapse():
    """The writer removes evaluator-identical duplicates and nothing else.

    "alpha stock exchange" normalises to the same string as "Alpha Stock
    Exchange", so submitting both is a guaranteed false positive and one is
    dropped. The article variant does *not* normalise the same, so it survives.
    """
    values = ["The Alpha Stock Exchange", "Alpha Stock Exchange", "alpha stock exchange", "ASE"]
    assert dedupe_object_entities(values) == [
        "The Alpha Stock Exchange", "Alpha Stock Exchange", "ASE"
    ]


def test_dedupe_matches_the_evaluators_own_normalisation_exactly():
    """Not stricter, and not looser.

    The writer must not reconstruct the evaluator's alias database. Folding
    leading articles here would silently merge two *distinct* strict candidates
    on a soft hint - which Module 3 keeps as grouping metadata precisely so it
    can never destroy an entity.
    """
    from cover_kbc.evaluation.official import official_normalize_string

    a, b = "The Alpha Exchange", "Alpha Exchange"
    assert official_normalize_string(a) != official_normalize_string(b)
    assert dedupe_object_entities([a, b]) == [a, b]

    same = "Alpha Exchange", "alpha exchange"
    assert official_normalize_string(same[0]) == official_normalize_string(same[1])
    assert dedupe_object_entities(list(same)) == [same[0]]


def test_dedupe_drops_blank_values():
    assert dedupe_object_entities(["a", "", "   ", "a"]) == ["a"]


def test_write_predictions_round_trips(tmp_path):
    predictions = [
        Prediction(subject="S1", relation="hasArea", object_entities=["5000"]),
        Prediction(subject="S2", relation="hasArea", object_entities=[]),
    ]
    path = write_predictions(predictions, tmp_path / "p.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [
        {"SubjectEntity": "S1", "Relation": "hasArea", "ObjectEntities": ["5000"]},
        {"SubjectEntity": "S2", "Relation": "hasArea", "ObjectEntities": []},
    ]


def test_write_predictions_detects_a_missing_row(tmp_path):
    queries = [Query("S1", "hasArea", 0), Query("S2", "hasArea", 1)]
    predictions = [Prediction(subject="S1", relation="hasArea")]
    with pytest.raises(SchemaError, match="missing"):
        write_predictions(predictions, tmp_path / "p.jsonl", expected_queries=queries)


def test_write_predictions_detects_reordering(tmp_path):
    queries = [Query("S1", "hasArea", 0), Query("S2", "hasArea", 1)]
    predictions = [
        Prediction(subject="S2", relation="hasArea"),
        Prediction(subject="S1", relation="hasArea"),
    ]
    with pytest.raises(SchemaError, match="different order"):
        write_predictions(predictions, tmp_path / "p.jsonl", expected_queries=queries)
