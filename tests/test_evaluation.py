"""Wrapping and invoking the official evaluator.

These tests pin down evaluator behaviour our system design depends on.  If an
upstream snapshot refresh changes one of these, the design assumption behind it
needs revisiting - that is the point of asserting them here.
"""

from __future__ import annotations

import pytest

from cover_kbc.evaluation.harness import (
    OVERALL_KEY,
    evaluate_files,
    evaluate_predictions,
    evaluate_via_cli,
)
from cover_kbc.evaluation.official import (
    evaluator_checksum,
    load_official_evaluator,
    official_normalize_string,
    relation_types,
)
from cover_kbc.paths import SPLIT_FILES

from conftest import write_jsonl


def test_official_evaluator_loads_from_the_snapshot():
    evaluator = load_official_evaluator()
    assert hasattr(evaluator, "evaluate_per_sr_pair")
    assert len(evaluator_checksum()) == 64


def test_relation_types_come_from_the_snapshot():
    assert relation_types()["hasArea"] == "numeric"
    assert relation_types()["awardWonBy"] == "string"


# --- evaluator behaviour our design relies on ------------------------------


def test_empty_prediction_scores_precision_one(synthetic_gold_rows):
    """Abstaining is precise by definition - which is why a null gate can win."""
    predictions = [
        {"SubjectEntity": r["SubjectEntity"], "Relation": r["Relation"], "ObjectEntities": []}
        for r in synthetic_gold_rows
    ]
    report = evaluate_predictions(predictions, synthetic_gold_rows)
    assert report.macro[OVERALL_KEY]["macro-p"] == 1.0


def test_empty_prediction_on_empty_gold_is_a_perfect_score(synthetic_gold_rows):
    gold = [r for r in synthetic_gold_rows if not r["ObjectEntities"]]
    predictions = [{**r, "ObjectEntities": []} for r in gold]
    report = evaluate_predictions(predictions, gold)
    assert report.macro[OVERALL_KEY]["macro-f1"] == 1.0


def test_any_alias_of_a_gold_entity_matches(synthetic_gold_rows):
    gold = [r for r in synthetic_gold_rows if r["Relation"] == "companyTradesAtStockExchange"]
    predictions = [{**gold[0], "ObjectEntities": ["ASE", "BSE"]}]
    report = evaluate_predictions(predictions, gold)
    assert report.macro[OVERALL_KEY]["macro-f1"] == 1.0


def test_two_surface_forms_of_one_entity_cost_precision(synthetic_gold_rows):
    """The reason the writer emits exactly one form per semantic candidate."""
    gold = [r for r in synthetic_gold_rows if r["Relation"] == "companyTradesAtStockExchange"]
    predictions = [{**gold[0], "ObjectEntities": ["Alpha Stock Exchange", "The Alpha Stock Exchange"]}]
    report = evaluate_predictions(predictions, gold)
    macro = report.macro[OVERALL_KEY]
    assert macro["macro-p"] == pytest.approx(0.5)
    assert macro["macro-r"] == pytest.approx(0.5)


def test_predictions_identical_after_normalisation_are_collapsed(synthetic_gold_rows):
    """Case, diacritic and apostrophe differences are folded away before scoring."""
    assert official_normalize_string("ASE") == official_normalize_string("ase")
    assert official_normalize_string("O'Brien") == official_normalize_string("OBrien")
    gold = [r for r in synthetic_gold_rows if r["Relation"] == "companyTradesAtStockExchange"]
    predictions = [{**gold[0], "ObjectEntities": ["ASE", "ase", "BSE"]}]
    report = evaluate_predictions(predictions, gold)
    assert report.macro[OVERALL_KEY]["macro-p"] == 1.0


def test_numeric_tolerance_is_five_percent(synthetic_gold_rows):
    gold = [r for r in synthetic_gold_rows if r["Relation"] == "hasArea"]  # gold 5000
    within = evaluate_predictions([{**gold[0], "ObjectEntities": ["5200"]}], gold)
    outside = evaluate_predictions([{**gold[0], "ObjectEntities": ["5300"]}], gold)
    assert within.macro[OVERALL_KEY]["macro-f1"] == 1.0
    assert outside.macro[OVERALL_KEY]["macro-f1"] == 0.0


def test_numeric_prediction_with_a_unit_never_matches(synthetic_gold_rows):
    """"5000 km2" fails try_parse_number, so it can only ever hurt."""
    gold = [r for r in synthetic_gold_rows if r["Relation"] == "hasArea"]
    report = evaluate_predictions([{**gold[0], "ObjectEntities": ["5000 km2"]}], gold)
    assert report.macro[OVERALL_KEY]["macro-f1"] == 0.0


def test_missing_prediction_rows_are_reported(synthetic_gold_rows):
    report = evaluate_predictions([], synthetic_gold_rows)
    assert len(report.missing_pred_rows) == len(synthetic_gold_rows)


def test_extra_prediction_rows_are_reported(synthetic_gold_rows):
    extra = {"SubjectEntity": "Ghost", "Relation": "hasArea", "ObjectEntities": []}
    report = evaluate_predictions([*synthetic_gold_rows, extra], synthetic_gold_rows)
    assert report.extra_pred_rows == [{"SubjectEntity": "Ghost", "Relation": "hasArea"}]


# --- CLI parity ------------------------------------------------------------


def test_in_process_harness_matches_the_official_cli(tmp_path, synthetic_gold_rows):
    """Our wrapper must report exactly what `python benchmark/evaluate.py` reports."""
    gold_path = write_jsonl(tmp_path / "gold.jsonl", synthetic_gold_rows)
    predictions = [
        {"SubjectEntity": "Testland", "Relation": "countryLandBordersCountry", "ObjectEntities": []},
        {"SubjectEntity": "Testperson", "Relation": "personHasCityOfDeath", "ObjectEntities": ["Test Ville"]},
        {
            "SubjectEntity": "Testcorp",
            "Relation": "companyTradesAtStockExchange",
            "ObjectEntities": ["ASE"],
        },
        {"SubjectEntity": "Testisland", "Relation": "hasArea", "ObjectEntities": ["5100"]},
    ]
    pred_path = write_jsonl(tmp_path / "pred.jsonl", predictions)

    report = evaluate_files(pred_path, gold_path)

    # The CLI must accept our file and report on the same relations. Its printed
    # table is column-truncated by pandas, so numeric parity is asserted against
    # the official scoring functions instead, just below.
    cli_output = evaluate_via_cli(pred_path, gold_path)
    for relation in report.macro:
        label = "*** All Relations ***" if relation == OVERALL_KEY else relation
        assert label in cli_output

    evaluator = load_official_evaluator()
    per_pair = evaluator.evaluate_per_sr_pair(
        evaluator.read_jsonl_file(str(pred_path)),
        evaluator.read_jsonl_file(str(gold_path)),
        relation_types(),
        tolerance=0.05,
    )
    assert report.macro == evaluator.macro_average_per_relation(per_pair)
    assert report.micro == evaluator.micro_average_per_relation(per_pair)
    assert report.stats == evaluator.prediction_statistics(per_pair)


def test_val_split_scores_against_itself_perfectly():
    """Feeding gold back in is a sanity check on the whole wrapper."""
    from cover_kbc.data.loader import load_dataset

    val = load_dataset("val")
    predictions = [
        {
            "SubjectEntity": row.subject,
            "Relation": row.relation,
            "ObjectEntities": row.preferred_surface_forms(),
        }
        for row in val.rows
    ]
    report = evaluate_predictions(predictions, [r.to_official_row() for r in val.rows])
    assert report.overall_macro_f1 == pytest.approx(1.0)
    assert str(SPLIT_FILES["val"]).endswith("val.jsonl")
