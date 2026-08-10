from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_analyzer():
    sys.path.insert(0, str(Path("scripts").resolve()))
    spec = importlib.util.spec_from_file_location(
        "analyze_v3_train_failures",
        Path("scripts/analyze_v3_train_failures.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metric_validation_matches_published_three_decimal_table():
    analyzer = _load_analyzer()
    metrics = {
        relation: {
            "macro-p": expected[0],
            "macro-r": expected[1],
            "macro-f1": expected[2],
        }
        for relation, expected in analyzer.EXPECTED_ROUNDED_METRICS.items()
    }
    metrics["companyTradesAtStockExchange"]["macro-p"] = 0.6275

    analyzer.validate_metrics(metrics)


def test_empty_gold_false_positive_is_not_complete_miss():
    analyzer = _load_analyzer()

    tags = analyzer.failure_tags_for(
        "companyTradesAtStockExchange",
        "ExampleCo",
        [],
        ["Example Exchange"],
        [],
        {
            "tp": 0,
            "false_positives": ["Example Exchange"],
            "false_negatives": [],
        },
        {},
        {},
        {},
        "L6_UNKNOWN",
        {},
    )

    assert "COMPLETE_MISS" not in tags
    assert "OVER_ENUMERATION" in tags
    assert "FALSE_POSITIVE_ACCUMULATION" in tags
