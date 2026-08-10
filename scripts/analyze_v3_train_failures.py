#!/usr/bin/env python3
"""Offline TRAIN error forensics for the V3 full collection.

This script is analysis-only. It reads the full TRAIN prediction artifact, the
TRAIN gold file, and persisted run telemetry/graphs. It never loads a model,
never reads VAL/TEST, and never mutates the source run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from _bootstrap import ensure_src_on_path

ensure_src_on_path()


TRAIN_SHA256 = "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e"
EVALUATOR_SHA256 = "2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22"
EXPECTED_ROWS = 477
EXPECTED_ROUNDED_METRICS = {
    "awardWonBy": (0.423, 0.391, 0.389),
    "companyTradesAtStockExchange": (0.628, 0.743, 0.529),
    "countryLandBordersCountry": (0.987, 0.956, 0.964),
    "hasArea": (0.890, 0.240, 0.240),
    "hasCapacity": (0.210, 0.080, 0.080),
    "personHasCityOfDeath": (0.840, 0.480, 0.390),
    "*** All Relations ***": (0.686, 0.466, 0.403),
}
NUMERIC_RELATIONS = {"hasArea", "hasCapacity"}
RUN_REQUIRED_FILES = (
    "predictions.jsonl",
    "train_telemetry.jsonl",
    "inference_telemetry.jsonl",
    "v3_pre_m8_hypothesis_graphs.jsonl",
    "v3_final_hypothesis_graphs.jsonl",
    "v3_action_effects.jsonl",
    "manifest.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise SystemExit(f"{path}:{number}: invalid JSONL: {error}") from None
            if not isinstance(payload, dict):
                raise SystemExit(f"{path}:{number}: expected JSON object")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def evaluator_module() -> Any:
    path = Path("benchmark/evaluate.py")
    spec = importlib.util.spec_from_file_location("benchmark_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


EVAL = evaluator_module()


def norm(value: object) -> str:
    return EVAL.normalize_string(str(value))


def parse_num(value: object) -> float | None:
    if value is None:
        return None
    return EVAL.try_parse_number(str(value))


def flatten_predictions(values: Sequence[Any]) -> tuple[list[str], int]:
    flat: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        if isinstance(value, list):
            if not value:
                continue
            value = value[0]
        if not isinstance(value, str):
            continue
        key = norm(value)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        flat.append(value)
    return flat, duplicates


def gold_aliases(values: Sequence[Any]) -> list[list[str]]:
    out: list[list[str]] = []
    for value in values:
        if isinstance(value, list):
            aliases = [str(item) for item in value if isinstance(item, str)]
            out.append(aliases)
        elif isinstance(value, str):
            out.append([value])
    return out


def match_row(preds: Sequence[str], gold: Sequence[Sequence[str]], relation: str) -> dict[str, Any]:
    if relation in NUMERIC_RELATIONS:
        return match_numeric(preds, gold)
    return match_string(preds, gold)


def match_string(preds: Sequence[str], gold: Sequence[Sequence[str]]) -> dict[str, Any]:
    gold_sets = [{norm(alias) for alias in aliases} for aliases in gold]
    pred_keys = [norm(pred) for pred in preds]
    adj = [{j for j, aliases in enumerate(gold_sets) if pred in aliases}
           for pred in pred_keys]
    match_gold_to_pred: dict[int, int] = {}

    def augment(pred_i: int, visited: set[int]) -> bool:
        for gold_i in sorted(adj[pred_i]):
            if gold_i in visited:
                continue
            visited.add(gold_i)
            if gold_i not in match_gold_to_pred or augment(
                    match_gold_to_pred[gold_i], visited):
                match_gold_to_pred[gold_i] = pred_i
                return True
        return False

    for pred_i in range(len(pred_keys)):
        augment(pred_i, set())
    matched_pred = set(match_gold_to_pred.values())
    matched_gold = set(match_gold_to_pred)
    return {
        "tp": len(matched_gold),
        "matched_pred_indices": matched_pred,
        "matched_gold_indices": matched_gold,
        "matched_pairs": [
            {"prediction": preds[pred_i], "gold": gold[gold_i][0] if gold[gold_i] else ""}
            for gold_i, pred_i in sorted(match_gold_to_pred.items())
        ],
        "false_positives": [
            preds[i] for i in range(len(preds)) if i not in matched_pred
        ],
        "false_negatives": [
            gold[i][0] if gold[i] else "" for i in range(len(gold)) if i not in matched_gold
        ],
    }


def match_numeric(preds: Sequence[str], gold: Sequence[Sequence[str]]) -> dict[str, Any]:
    gold_nums = [parse_num(aliases[0] if aliases else None) for aliases in gold]
    pred_nums = [parse_num(pred) for pred in preds]
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    pairs: list[dict[str, Any]] = []
    for pred_i, pred_num in enumerate(pred_nums):
        if pred_num is None:
            continue
        for gold_i, gold_num in enumerate(gold_nums):
            if gold_i in matched_gold or gold_num in (None, 0):
                continue
            relerr = abs(pred_num - gold_num) / abs(gold_num)
            if relerr <= 0.05:
                matched_gold.add(gold_i)
                matched_pred.add(pred_i)
                pairs.append({
                    "prediction": preds[pred_i],
                    "gold": gold[gold_i][0] if gold[gold_i] else "",
                    "relative_error": relerr,
                })
                break
    return {
        "tp": len(matched_gold),
        "matched_pred_indices": matched_pred,
        "matched_gold_indices": matched_gold,
        "matched_pairs": pairs,
        "false_positives": [
            preds[i] for i in range(len(preds)) if i not in matched_pred
        ],
        "false_negatives": [
            gold[i][0] if gold[i] else "" for i in range(len(gold)) if i not in matched_gold
        ],
    }


def metric_tuple(tp: int, pred_count: int, gold_count: int) -> tuple[float, float, float]:
    precision = tp / pred_count if pred_count else 1.0
    recall = tp / gold_count if gold_count else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def resolve_run_dir(path: Path) -> Path:
    if all((path / name).is_file() for name in RUN_REQUIRED_FILES):
        return path
    matches: list[Path] = []
    if path.exists():
        for manifest in path.rglob("manifest.json"):
            candidate = manifest.parent
            if all((candidate / name).is_file() for name in RUN_REQUIRED_FILES):
                matches.append(candidate)
    if not matches:
        raise SystemExit(f"{path}: cannot find full TRAIN run directory")
    return sorted(matches, key=lambda p: str(p))[0]


def by_row(rows: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[int(row["row_index"])] = dict(row)
    return out


def rows_by_identity(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["SubjectEntity"]), str(row["Relation"])): dict(row)
        for row in rows
    }


def graph_values(graph: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for hyp in graph.get("hypotheses") or ():
        for key in ("display", "normalized_value"):
            value = hyp.get(key)
            if value not in (None, ""):
                values.append(str(value))
        values.extend(str(v) for v in hyp.get("raw_mentions") or ())
    return values


def telemetry_values(telemetry: Mapping[str, Any], stage: str | None = None) -> list[str]:
    values: list[str] = []
    if stage:
        values.extend(str(v) for v in (telemetry.get("stage_values") or {}).get(stage, ()))
        return values
    for stage_values in (telemetry.get("stage_values") or {}).values():
        values.extend(str(v) for v in stage_values)
    for candidate in telemetry.get("candidates") or ():
        for key in ("candidate_key", "display_value", "output_value"):
            value = candidate.get(key)
            if value not in (None, ""):
                values.append(str(value))
        values.extend(str(v) for v in candidate.get("surface_forms") or ())
    for generation in telemetry.get("generations") or ():
        values.extend(str(v) for v in generation.get("acquired_fragments") or ())
        values.extend(str(v) for v in generation.get("parsed_values") or ())
    return values


def value_matches(value: str, aliases: Sequence[str], relation: str) -> bool:
    if relation in NUMERIC_RELATIONS:
        pred = parse_num(value)
        gold = parse_num(aliases[0] if aliases else None)
        return bool(pred is not None and gold not in (None, 0)
                    and abs(pred - gold) / abs(gold) <= 0.05)
    key = norm(value)
    return key in {norm(alias) for alias in aliases}


def any_gold_in_values(gold: Sequence[Sequence[str]], values: Sequence[str], relation: str) -> bool:
    return any(
        value_matches(value, aliases, relation)
        for aliases in gold
        for value in values
    )


def missing_gold_presence(
    missing: Sequence[str],
    gold: Sequence[Sequence[str]],
    values: Sequence[str],
    relation: str,
) -> bool:
    missing_aliases = [
        aliases for aliases in gold
        if aliases and aliases[0] in set(missing)
    ]
    return any_gold_in_values(missing_aliases, values, relation)


def gold_candidate_details(
    gold: Sequence[Sequence[str]], telemetry: Mapping[str, Any], relation: str,
) -> dict[str, bool]:
    candidates = list(telemetry.get("candidates") or ())
    flags = {
        "verified": False,
        "verified_valid": False,
        "rejected": False,
        "emitted": False,
        "accepted_not_emitted": False,
    }
    for candidate in candidates:
        values = [
            str(candidate.get(key, ""))
            for key in ("candidate_key", "display_value", "output_value")
            if candidate.get(key, "") not in (None, "")
        ]
        values.extend(str(v) for v in candidate.get("surface_forms") or ())
        if not any_gold_in_values(gold, values, relation):
            continue
        verified = int(candidate.get("verification_count") or 0) > 0
        label = str(candidate.get("verifier_label") or "")
        status = str(candidate.get("final_status") or "")
        flags["verified"] |= verified
        flags["verified_valid"] |= verified and label == "VALID"
        flags["rejected"] |= bool(candidate.get("hard_rejected")) or label == "INVALID" or status == "REJECTED"
        flags["emitted"] |= bool(candidate.get("emitted"))
        flags["accepted_not_emitted"] |= status == "ACCEPTED" and not bool(candidate.get("emitted"))
    return flags


def classify_numeric(
    gold: Sequence[Sequence[str]], preds: Sequence[str],
) -> dict[str, Any]:
    gold_nums = [parse_num(aliases[0] if aliases else None) for aliases in gold]
    pred_nums = [parse_num(pred) for pred in preds]
    numeric_preds = [value for value in pred_nums if value is not None]
    nearest: tuple[float, float, float, float] | None = None
    for pred in numeric_preds:
        for gold_num in gold_nums:
            if gold_num in (None, 0):
                continue
            abs_err = abs(pred - gold_num)
            rel_err = abs_err / abs(gold_num)
            ratio = pred / gold_num
            candidate = (rel_err, abs_err, ratio, pred)
            if nearest is None or candidate < nearest:
                nearest = candidate
    tags: list[str] = []
    if not preds:
        tags.append("EMPTY_NUMERIC_RECALL")
    nonnumeric = len([pred for pred, num in zip(preds, pred_nums) if num is None])
    if nonnumeric:
        tags.append("NONNUMERIC_OUTPUT_FOR_NUMERIC_RELATION")
        tags.append("NUMERIC_FORMAT_ERROR")
    if len(numeric_preds) > 1:
        tags.append("MULTIPLE_NUMERIC_VARIANTS")
    nearest_payload = {
        "nearest_pred": "",
        "nearest_gold": "",
        "absolute_error": "",
        "relative_error": "",
        "ratio": "",
        "ratio_bucket": "",
        "within_5_percent": False,
    }
    if nearest is None:
        if preds:
            tags.append("NUMERIC_FORMAT_ERROR")
    else:
        rel_err, abs_err, ratio, pred = nearest
        nearest_gold = min(
            (g for g in gold_nums if g not in (None, 0)),
            key=lambda g: abs(pred - g) / abs(g),
        )
        nearest_payload = {
            "nearest_pred": pred,
            "nearest_gold": nearest_gold,
            "absolute_error": abs_err,
            "relative_error": rel_err,
            "ratio": ratio,
            "ratio_bucket": ratio_bucket(ratio),
            "within_5_percent": rel_err <= 0.05,
        }
        if rel_err == 0:
            tags.append("NUMERIC_EXACT")
        elif rel_err <= 0.05:
            tags.append("NUMERIC_WITHIN_5_PERCENT")
        elif rel_err <= 0.10:
            tags.append("NUMERIC_JUST_OUTSIDE_TOLERANCE")
        else:
            tags.append("NUMERIC_LARGE_MISS")
        if ratio <= 0.011 or 0.09 <= ratio <= 0.11 or 9 <= ratio <= 11 or ratio >= 90:
            tags.append("NUMERIC_SCALE_ERROR")
        if any(any(ch.isalpha() for ch in pred) for pred in preds):
            tags.append("NUMERIC_UNIT_LIKE_ERROR")
    return {
        **nearest_payload,
        "gold_numeric_values": [v for v in gold_nums if v is not None],
        "pred_numeric_values": [v for v in pred_nums if v is not None],
        "numeric_pred_count": len(numeric_preds),
        "nonnumeric_pred_count": nonnumeric,
        "numeric_tags": sorted(set(tags)),
    }


def ratio_bucket(ratio: float) -> str:
    if ratio <= 0:
        return "nonpositive"
    targets = [0.001, 0.01, 0.1, 1, 10, 100, 1000]
    nearest = min(targets, key=lambda item: abs(math.log10(ratio) - math.log10(item)))
    return f"~{nearest:g}"


def cardinality_bucket(gold_count: int, pred_count: int) -> str:
    if gold_count == 0:
        return "gold=0,pred=0" if pred_count == 0 else "gold=0,pred>0"
    if gold_count == 1:
        if pred_count == 0:
            return "gold=1,pred=0"
        if pred_count == 1:
            return "gold=1,pred=1"
        return "gold=1,pred>1"
    if pred_count == 0:
        return "gold>1,pred=0"
    if pred_count == 1:
        return "gold>1,pred=1"
    if pred_count < gold_count:
        return "gold>1,1<pred<gold"
    if pred_count == gold_count:
        return "gold>1,pred=gold"
    return "gold>1,pred>gold"


def localization_for(
    exact: bool,
    false_negatives: Sequence[str],
    false_positives: Sequence[str],
    gold: Sequence[Sequence[str]],
    relation: str,
    telemetry: Mapping[str, Any],
    pre_graph: Mapping[str, Any],
    final_graph: Mapping[str, Any],
) -> str:
    if exact:
        return "L0_CORRECT"
    evidence_values = telemetry_values(telemetry)
    pre_values = graph_values(pre_graph)
    final_values = graph_values(final_graph)
    candidate_flags = gold_candidate_details(gold, telemetry, relation)
    if false_negatives:
        if not missing_gold_presence(false_negatives, gold, evidence_values, relation):
            return "L1_NEVER_RECALLED"
        if not missing_gold_presence(false_negatives, gold, pre_values, relation):
            return "L2_RECALLED_BUT_NOT_HYPOTHESIZED"
        if candidate_flags["rejected"]:
            return "L3_HYPOTHESIZED_BUT_REJECTED"
        if candidate_flags["verified_valid"] and not candidate_flags["emitted"]:
            return "L4_VERIFIED_BUT_DROPPED"
        if missing_gold_presence(false_negatives, gold, final_values, relation):
            return "L5_FINALIZATION_OR_NORMALIZATION_LOSS"
        return "L6_UNKNOWN"
    if false_positives:
        return "L5_FINALIZATION_OR_NORMALIZATION_LOSS"
    return "L6_UNKNOWN"


def failure_tags_for(
    relation: str,
    subject: str,
    gold: Sequence[Sequence[str]],
    preds: Sequence[str],
    duplicates: int,
    match: Mapping[str, Any],
    telemetry: Mapping[str, Any],
    pre_graph: Mapping[str, Any],
    final_graph: Mapping[str, Any],
    localization: str,
    numeric: Mapping[str, Any],
) -> list[str]:
    tags: set[str] = set()
    gold_count = len(gold)
    pred_count = len(preds)
    tp = int(match["tp"])
    fp = list(match["false_positives"])
    fn = list(match["false_negatives"])
    if tp == gold_count and not fp:
        tags.add("EXACT_CORRECT")
    elif tp > 0:
        tags.add("PARTIAL_MATCH")
    elif gold_count > 0:
        tags.add("COMPLETE_MISS")
    if pred_count == 0:
        tags.add("EMPTY_PREDICTION")
    if fn:
        tags.add("UNDER_ENUMERATION")
    if fp:
        tags.add("OVER_ENUMERATION")
        tags.add("FALSE_POSITIVE_ACCUMULATION")
    if tp > 0 and fp:
        tags.add("CORRECT_PLUS_FALSE_POSITIVES")
    if gold_count > 1 and pred_count == 1:
        tags.add("SINGLETON_WHEN_MULTI_GOLD")
    if gold_count == 1 and pred_count > 1:
        tags.add("MULTI_WHEN_SINGLE_GOLD")
    if duplicates:
        tags.add("DUPLICATE_OUTPUT")
    if relation in NUMERIC_RELATIONS:
        tags.update(numeric.get("numeric_tags") or ())
        if fp and "NUMERIC_LARGE_MISS" in tags:
            tags.add("WRONG_NUMERIC_ATTRIBUTE")
    else:
        if fp:
            tags.add("WRONG_ENTITY")
            tags.add("RELATED_ATTRIBUTE_CONFUSION")
        if relation == "personHasCityOfDeath" and fp:
            tags.add("RELATED_LOCATION_CONFUSION")
        if relation == "companyTradesAtStockExchange":
            if fp:
                tags.add("WRONG_ATTRIBUTE")
            subject_key = norm(subject)
            for item in fp:
                pred_key = norm(item)
                if subject_key and (
                    subject_key in pred_key or pred_key in subject_key):
                    tags.add("RELATED_ENTITY_CONFUSION")
    if fn:
        if localization == "L1_NEVER_RECALLED":
            tags.add("CORRECT_CANDIDATE_NEVER_RECALLED")
        elif localization == "L2_RECALLED_BUT_NOT_HYPOTHESIZED":
            tags.add("CORRECT_CANDIDATE_IN_EVIDENCE_BUT_DROPPED")
        elif localization == "L3_HYPOTHESIZED_BUT_REJECTED":
            tags.add("CORRECT_CANDIDATE_IN_PRE_M8_GRAPH_BUT_DROPPED")
            tags.add("CORRECT_CANDIDATE_REJECTED_BY_VERIFIER")
        elif localization == "L4_VERIFIED_BUT_DROPPED":
            tags.add("CORRECT_CANDIDATE_VERIFIED_BUT_NOT_OUTPUT")
        elif localization == "L5_FINALIZATION_OR_NORMALIZATION_LOSS":
            tags.add("CORRECT_CANDIDATE_IN_FINAL_GRAPH_BUT_NOT_OUTPUT")
            tags.add("NORMALIZATION_MISMATCH")
    if str(telemetry.get("stopped_reason", "")) and fn:
        tags.add("STOPPED_TOO_EARLY")
    if relation == "awardWonBy" and pred_count > gold_count and any(
        action.get("family") == "SET_EXPANSION" and action.get("executed")
        for action in telemetry.get("actions") or ()
    ):
        tags.add("UNNECESSARY_EXPANSION")
    if not tags:
        tags.add("OTHER")
    return sorted(tags)


def official_metrics(pred_rows: Sequence[dict[str, Any]], gold_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scores = EVAL.evaluate_per_sr_pair(
        list(pred_rows), list(gold_rows), EVAL.RELATION_TYPE, tolerance=0.05)
    macro = EVAL.macro_average_per_relation(scores)
    micro = EVAL.micro_average_per_relation(scores)
    stats = EVAL.prediction_statistics(scores)
    out: dict[str, Any] = {}
    for relation in sorted(macro):
        out[relation] = {
            **macro[relation],
            **micro[relation],
            **stats[relation],
        }
    return out


def validate_metrics(metrics: Mapping[str, Any]) -> None:
    for relation, expected in EXPECTED_ROUNDED_METRICS.items():
        observed = (
            rounded_metric(metrics[relation]["macro-p"]),
            rounded_metric(metrics[relation]["macro-r"]),
            rounded_metric(metrics[relation]["macro-f1"]),
        )
        if observed != expected:
            raise SystemExit(
                f"official metric mismatch for {relation}: observed {observed}, "
                f"expected {expected}; refusing forensic classification")


def rounded_metric(value: Any) -> float:
    """Match the published evaluator table at three decimal places."""
    return float(Decimal(str(float(value))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def stage_flags(
    gold: Sequence[Sequence[str]], relation: str,
    telemetry: Mapping[str, Any], pre_graph: Mapping[str, Any],
    final_graph: Mapping[str, Any],
) -> dict[str, bool]:
    return {
        "gold_in_initial_evidence": any_gold_in_values(
            gold, telemetry_values(telemetry, "ACQUIRED"), relation),
        "gold_in_pre_m8_graph": any_gold_in_values(
            gold, graph_values(pre_graph), relation),
        "gold_in_final_graph": any_gold_in_values(
            gold, graph_values(final_graph), relation),
        "gold_verified": gold_candidate_details(gold, telemetry, relation)["verified"],
        "gold_verified_valid": gold_candidate_details(gold, telemetry, relation)["verified_valid"],
        "gold_rejected_or_suppressed": gold_candidate_details(gold, telemetry, relation)["rejected"],
        "gold_survived_final_prediction": any_gold_in_values(
            gold, telemetry_values(telemetry, "FINAL_EMITTED"), relation),
    }


def row_forensics(
    gold_rows: Sequence[dict[str, Any]],
    pred_by_id: Mapping[tuple[str, str], dict[str, Any]],
    inference_by_row: Mapping[int, dict[str, Any]],
    pre_by_row: Mapping[int, dict[str, Any]],
    final_by_row: Mapping[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_index, gold_row in enumerate(gold_rows):
        subject = str(gold_row["SubjectEntity"])
        relation = str(gold_row["Relation"])
        pred_row = pred_by_id.get((subject, relation), {})
        raw_preds = pred_row.get("ObjectEntities") or []
        preds, duplicates = flatten_predictions(raw_preds)
        gold = gold_aliases(gold_row.get("ObjectEntities") or [])
        match = match_row(preds, gold, relation)
        precision, recall, f1 = metric_tuple(
            int(match["tp"]), len(preds), len(gold))
        telemetry = inference_by_row.get(row_index, {})
        pre_graph = pre_by_row.get(row_index, {})
        final_graph = final_by_row.get(row_index, {})
        numeric = classify_numeric(gold, preds) if relation in NUMERIC_RELATIONS else {}
        exact = int(match["tp"]) == len(gold) and not match["false_positives"]
        localization = localization_for(
            exact, match["false_negatives"], match["false_positives"],
            gold, relation, telemetry, pre_graph, final_graph)
        flags = stage_flags(gold, relation, telemetry, pre_graph, final_graph)
        tags = failure_tags_for(
            relation, subject, gold, preds, duplicates, match, telemetry,
            pre_graph, final_graph, localization, numeric)
        record = {
            "row_index": row_index,
            "SubjectEntity": subject,
            "Relation": relation,
            "gold": json.dumps(gold, ensure_ascii=False),
            "predicted": json.dumps(preds, ensure_ascii=False),
            "normalized_gold": json.dumps(
                [[norm(alias) for alias in aliases] for aliases in gold],
                ensure_ascii=False),
            "normalized_predictions": json.dumps([norm(pred) for pred in preds], ensure_ascii=False),
            "gold_count": len(gold),
            "pred_count": len(preds),
            "cardinality_delta": len(preds) - len(gold),
            "empty_prediction": len(preds) == 0,
            "correct_values": json.dumps(match["matched_pairs"], ensure_ascii=False),
            "false_positives": json.dumps(match["false_positives"], ensure_ascii=False),
            "false_negatives": json.dumps(match["false_negatives"], ensure_ascii=False),
            "tp": int(match["tp"]),
            "fp": len(match["false_positives"]),
            "fn": len(match["false_negatives"]),
            "exact_set_match": exact,
            "partial_match": int(match["tp"]) > 0 and not exact,
            "complete_miss": int(match["tp"]) == 0 and len(gold) > 0,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "official_numeric_tolerance_correct": (
                relation not in NUMERIC_RELATIONS or int(match["tp"]) > 0),
            "failure_tags": json.dumps(tags),
            "pipeline_localization": localization,
            "stage_values": json.dumps(telemetry.get("stage_values") or {}, ensure_ascii=False),
            "failure_state": str((final_graph or pre_graph).get("failure_state", "")),
            "empty_reason": str(telemetry.get("empty_reason", "")),
            "stopped_reason": str(telemetry.get("stopped_reason", "")),
            "v3_actions_executed": json.dumps([
                action.get("family") for action in telemetry.get("actions") or ()
                if action.get("executed")
            ]),
            "hypothesis_count_pre_m8": len(pre_graph.get("hypotheses") or ()),
            "hypothesis_count_final": len(final_graph.get("hypotheses") or ()),
            "pre_m8_hypotheses": json.dumps(summarize_graph(pre_graph), ensure_ascii=False),
            "final_hypotheses": json.dumps(summarize_graph(final_graph), ensure_ascii=False),
            **flags,
            **{f"numeric_{key}": value for key, value in numeric.items()
               if key not in {"gold_numeric_values", "pred_numeric_values", "numeric_tags"}},
            "numeric_gold_values": json.dumps(numeric.get("gold_numeric_values", [])),
            "numeric_pred_values": json.dumps(numeric.get("pred_numeric_values", [])),
            "numeric_failure_tags": json.dumps(numeric.get("numeric_tags", [])),
        }
        records.append(record)
    return records


def summarize_graph(graph: Mapping[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for hyp in (graph.get("hypotheses") or ())[:limit]:
        out.append({
            "display": hyp.get("display", ""),
            "normalized_value": hyp.get("normalized_value", ""),
            "status": hyp.get("status", ""),
            "verifier_label": hyp.get("verifier_label", ""),
            "support": hyp.get("independent_support_count", 0),
            "flags": hyp.get("ambiguity_flags", []),
        })
    return out


def relation_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for relation in sorted({row["Relation"] for row in rows}):
        subset = [row for row in rows if row["Relation"] == relation]
        wrong = [row for row in subset if not row["exact_set_match"]]
        out.append({
            "Relation": relation,
            "rows": len(subset),
            "exact": len(subset) - len(wrong),
            "partial": sum(1 for row in subset if row["partial_match"]),
            "complete_miss": sum(1 for row in subset if row["complete_miss"]),
            "empty": sum(1 for row in subset if row["empty_prediction"]),
            "tp": sum(int(row["tp"]) for row in subset),
            "fp": sum(int(row["fp"]) for row in subset),
            "fn": sum(int(row["fn"]) for row in subset),
            "mean_precision": mean(row["precision"] for row in subset),
            "mean_recall": mean(row["recall"] for row in subset),
            "mean_f1": mean(row["f1"] for row in subset),
        })
    return out


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def failure_category_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counter: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        tags = json.loads(str(row["failure_tags"]))
        for tag in tags:
            key = (str(row["Relation"]), tag)
            acc = counter.setdefault(key, {
                "Relation": row["Relation"],
                "failure_category": tag,
                "rows": 0,
                "fp": 0,
                "fn": 0,
                "f1_loss": 0.0,
            })
            acc["rows"] += 1
            acc["fp"] += int(row["fp"])
            acc["fn"] += int(row["fn"])
            acc["f1_loss"] += 1.0 - float(row["f1"])
    return sorted(counter.values(), key=lambda r: (-r["f1_loss"], r["Relation"], r["failure_category"]))


def pipeline_localization(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter((row["Relation"], row["pipeline_localization"]) for row in rows)
    return [
        {"Relation": relation, "pipeline_localization": stage, "rows": count}
        for (relation, stage), count in sorted(counter.items())
    ]


def cardinality_analysis(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    details: list[dict[str, Any]] = []
    for row in rows:
        bucket = cardinality_bucket(int(row["gold_count"]), int(row["pred_count"]))
        matrix[str(row["Relation"])][bucket] += 1
    for relation, buckets in sorted(matrix.items()):
        subset = [row for row in rows if row["Relation"] == relation]
        total = len(subset)
        under = sum(1 for row in subset if int(row["fn"]) > 0)
        over = sum(1 for row in subset if int(row["fp"]) > 0)
        exact_card_wrong = sum(
            1 for row in subset
            if int(row["gold_count"]) == int(row["pred_count"])
            and not row["exact_set_match"]
        )
        singleton_multi = sum(
            1 for row in subset
            if int(row["gold_count"]) > 1 and int(row["pred_count"]) == 1
        )
        details.append({
            "Relation": relation,
            "rows": total,
            "under_enumerated_rows": under,
            "under_enumerated_pct": under / total if total else 0.0,
            "over_enumerated_rows": over,
            "over_enumerated_pct": over / total if total else 0.0,
            "exact_cardinality_wrong_entities": exact_card_wrong,
            "singleton_when_multi_gold": singleton_multi,
            "mean_missing_gold_entities": mean(int(row["fn"]) for row in subset),
            "mean_extra_entities": mean(int(row["fp"]) for row in subset),
        })
    return details, {rel: dict(counter) for rel, counter in sorted(matrix.items())}


def numeric_tables(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    numeric_rows = [row for row in rows if row["Relation"] in NUMERIC_RELATIONS]
    out = []
    buckets = Counter()
    for row in numeric_rows:
        tags = json.loads(str(row["numeric_failure_tags"]))
        for tag in tags:
            buckets[(row["Relation"], tag)] += 1
        if row.get("numeric_ratio_bucket"):
            buckets[(row["Relation"], "RATIO_" + str(row["numeric_ratio_bucket"]))] += 1
        out.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "gold_numeric_values": row["numeric_gold_values"],
            "pred_numeric_values": row["numeric_pred_values"],
            "nearest_pred": row.get("numeric_nearest_pred", ""),
            "nearest_gold": row.get("numeric_nearest_gold", ""),
            "absolute_error": row.get("numeric_absolute_error", ""),
            "relative_error": row.get("numeric_relative_error", ""),
            "ratio": row.get("numeric_ratio", ""),
            "ratio_bucket": row.get("numeric_ratio_bucket", ""),
            "within_5_percent": row.get("numeric_within_5_percent", False),
            "numeric_pred_count": row.get("numeric_numeric_pred_count", 0),
            "nonnumeric_pred_count": row.get("numeric_nonnumeric_pred_count", 0),
            "numeric_failure_tags": row["numeric_failure_tags"],
        })
    bucket_rows = [
        {"Relation": rel, "bucket": bucket, "rows": count}
        for (rel, bucket), count in sorted(buckets.items())
    ]
    return out, bucket_rows


def action_failure_analysis(
    rows: Sequence[Mapping[str, Any]],
    telemetry_rows: Sequence[Mapping[str, Any]],
    effects: Sequence[Mapping[str, Any]],
    gold_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    row_lookup = {int(row["row_index"]): row for row in rows}
    gold_by_row = {
        i: gold_aliases(row.get("ObjectEntities") or [])
        for i, row in enumerate(gold_rows)
    }
    effect_ids = {str((effect.get("action") or {}).get("action_id", "")) for effect in effects}
    acc: dict[str, dict[str, Any]] = {}
    for telemetry in telemetry_rows:
        if not telemetry.get("executed"):
            continue
        family = str(telemetry.get("action_family", ""))
        row_index = int(telemetry.get("row_index", -1))
        relation = str(telemetry.get("Relation", ""))
        outcome = dict(telemetry.get("outcome") or {})
        entry = acc.setdefault(family, {
            "action_family": family,
            "executed": 0,
            "affected_wrong_rows": set(),
            "affected_correct_rows": set(),
            "candidate_additions": 0,
            "candidate_removals_or_contradictions": 0,
            "gold_candidate_touches": 0,
            "false_positive_named": 0,
            "residual_improved": 0,
            "residual_degraded": 0,
            "effects_with_action_record": 0,
        })
        entry["executed"] += 1
        row = row_lookup.get(row_index)
        if row and row["exact_set_match"]:
            entry["affected_correct_rows"].add(row_index)
        else:
            entry["affected_wrong_rows"].add(row_index)
        entry["candidate_additions"] += len(outcome.get("candidates_added") or ())
        entry["candidate_removals_or_contradictions"] += len(
            outcome.get("candidates_contradicted") or ())
        gold = gold_by_row.get(row_index, [])
        touched = list(outcome.get("candidates_touched") or ()) + list(outcome.get("candidates_supported") or ())
        named = list(outcome.get("candidates_named") or ())
        entry["gold_candidate_touches"] += sum(
            1 for value in touched if any_gold_in_values(gold, [str(value)], relation))
        entry["false_positive_named"] += sum(
            1 for value in named if not any_gold_in_values(gold, [str(value)], relation))
        pre = telemetry.get("pre_state") or {}
        post = telemetry.get("post_state") or {}
        if pre and post:
            before = float(pre.get("residual", 0.0) or 0.0)
            after = float(post.get("residual", 0.0) or 0.0)
            if after < before:
                entry["residual_improved"] += 1
            elif after > before:
                entry["residual_degraded"] += 1
        if str(telemetry.get("action_id", "")) in effect_ids:
            entry["effects_with_action_record"] += 1
    out = []
    for entry in acc.values():
        row = dict(entry)
        row["affected_wrong_rows"] = len(entry["affected_wrong_rows"])
        row["affected_correct_rows"] = len(entry["affected_correct_rows"])
        out.append(row)
    return sorted(out, key=lambda r: r["action_family"])


def score_loss_ranking(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    category_rows = failure_category_summary([row for row in rows if not row["exact_set_match"]])
    ranked = []
    for rank, row in enumerate(category_rows[:60], 1):
        difficulty, risk = difficulty_for(str(row["failure_category"]))
        ranked.append({
            "rank": rank,
            "failure_category": row["failure_category"],
            "Relation": row["Relation"],
            "rows_affected": row["rows"],
            "fp": row["fp"],
            "fn": row["fn"],
            "estimated_recoverable_opportunity": round(float(row["f1_loss"]), 6),
            "implementation_difficulty": difficulty,
            "risk": risk,
        })
    return ranked


def difficulty_for(category: str) -> tuple[str, str]:
    if category in {
        "DUPLICATE_OUTPUT",
        "NONNUMERIC_OUTPUT_FOR_NUMERIC_RELATION",
        "NUMERIC_FORMAT_ERROR",
        "MULTI_WHEN_SINGLE_GOLD",
    }:
        return "LOW", "LOW"
    if category in {
        "CORRECT_CANDIDATE_IN_FINAL_GRAPH_BUT_NOT_OUTPUT",
        "CORRECT_CANDIDATE_VERIFIED_BUT_NOT_OUTPUT",
        "STOPPED_TOO_EARLY",
        "OVER_ENUMERATION",
        "FALSE_POSITIVE_ACCUMULATION",
    }:
        return "MEDIUM", "MEDIUM"
    return "HIGH", "HIGH"


def intervention_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def tags_for(row: Mapping[str, Any]) -> set[str]:
        return set(json.loads(str(row["failure_tags"])))

    def has_tags(row: Mapping[str, Any], names: set[str]) -> bool:
        return bool(tags_for(row) & names)

    candidates = [
        ("Numeric: suppress nonnumeric final outputs",
         "Numeric predictions include nonnumeric strings that cannot match official numeric tolerance.",
         lambda row: row["Relation"] in NUMERIC_RELATIONS and has_tags(row, {"NONNUMERIC_OUTPUT_FOR_NUMERIC_RELATION"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Numeric: canonicalize unit-like number strings",
         "Numeric candidates sometimes carry parse-blocking unit or text wrappers.",
         lambda row: row["Relation"] in NUMERIC_RELATIONS and has_tags(row, {"NUMERIC_UNIT_LIKE_ERROR", "NUMERIC_FORMAT_ERROR"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Numeric: single-valued best numeric guard",
         "Single-valued numeric rows sometimes emit multiple numeric variants.",
         lambda row: row["Relation"] in NUMERIC_RELATIONS and has_tags(row, {"MULTIPLE_NUMERIC_VARIANTS", "MULTI_WHEN_SINGLE_GOLD", "NUMERIC_LARGE_MISS"}),
         "MAY_SHIFT_CALIBRATION", "MEDIUM", "MEDIUM", "YES", "NO", "NO", "YES", "YES"),
        ("Finalization: retain accepted gold-equivalent candidate",
         "Correct candidate survives in final graph or accepted telemetry but is not emitted.",
         lambda row: has_tags(row, {
             "CORRECT_CANDIDATE_IN_FINAL_GRAPH_BUT_NOT_OUTPUT",
             "CORRECT_CANDIDATE_VERIFIED_BUT_NOT_OUTPUT",
         }),
         "SAFE_WITH_EXISTING_CALIBRATION", "MEDIUM", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Award: cardinality-aware continuation",
         "Award rows remain heavily under-enumerated even when expansion adds many answers.",
         lambda row: row["Relation"] == "awardWonBy" and has_tags(row, {"UNDER_ENUMERATION", "SINGLETON_WHEN_MULTI_GOLD", "STOPPED_TOO_EARLY"}),
         "INVALIDATES_CALIBRATION_SEMANTICS", "HIGH", "HIGH", "YES", "YES", "YES", "YES", "YES"),
        ("Award: false-positive cap after expansion",
         "Award expansion also creates a large unsupported-extra burden.",
         lambda row: row["Relation"] == "awardWonBy" and has_tags(row, {"FALSE_POSITIVE_ACCUMULATION", "OVER_ENUMERATION", "UNNECESSARY_EXPANSION"}),
         "MAY_SHIFT_CALIBRATION", "MEDIUM", "MEDIUM", "YES", "NO", "YES", "YES", "YES"),
        ("Stock: calibrated listing cardinality guard",
         "Single-gold stock rows often emit multiple exchanges, creating pure denominator burden.",
         lambda row: row["Relation"] == "companyTradesAtStockExchange" and has_tags(row, {"MULTI_WHEN_SINGLE_GOLD"}),
         "MAY_SHIFT_CALIBRATION", "MEDIUM", "MEDIUM", "YES", "NO", "NO", "YES", "YES"),
        ("Stock: rejection-first FP suppression",
         "Stock rows frequently include correct exchange plus false positives.",
         lambda row: row["Relation"] == "companyTradesAtStockExchange" and has_tags(row, {"FALSE_POSITIVE_ACCUMULATION", "OVER_ENUMERATION"}),
         "MAY_SHIFT_CALIBRATION", "MEDIUM", "MEDIUM", "YES", "NO", "YES", "YES", "YES"),
        ("Stock: reject subject/company self outputs",
         "Stock predictions sometimes look like the subject rather than an exchange.",
         lambda row: row["Relation"] == "companyTradesAtStockExchange" and has_tags(row, {"RELATED_ENTITY_CONFUSION"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("String: final output alias canonicalization",
         "Correct candidate appears before final output but normalized output mismatches.",
         lambda row: row["Relation"] not in NUMERIC_RELATIONS and has_tags(row, {"NORMALIZATION_MISMATCH", "ALIAS_OR_FORMAT_VARIANT"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "UNCERTAIN", "NO", "NO", "NO", "NO"),
        ("City: suppress multi-city output for singleton relation",
         "City-of-death is single-valued and multiple cities create pure FP burden.",
         lambda row: row["Relation"] == "personHasCityOfDeath" and has_tags(row, {"MULTI_WHEN_SINGLE_GOLD", "RELATED_LOCATION_CONFUSION", "OVER_ENUMERATION"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "MEDIUM", "YES", "NO", "NO", "NO", "NO"),
        ("Recall: relation-specific missing-recall prompt review",
         "Correct answer absent from all evidence traces.",
         lambda row: has_tags(row, {"CORRECT_CANDIDATE_NEVER_RECALLED"}),
         "INVALIDATES_CALIBRATION_SEMANTICS", "HIGH", "HIGH", "YES", "YES", "YES", "YES", "YES"),
        ("Verifier: do not discard verifier-valid candidate",
         "Correct candidate has valid verification but is absent from output.",
         lambda row: has_tags(row, {"CORRECT_CANDIDATE_VERIFIED_BUT_NOT_OUTPUT"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "MEDIUM", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Controller: avoid stopping with unresolved missing candidates",
         "Telemetry records stopped reasons while FNs remain.",
         lambda row: has_tags(row, {"STOPPED_TOO_EARLY"}),
         "INVALIDATES_CALIBRATION_SEMANTICS", "HIGH", "HIGH", "YES", "NO", "YES", "YES", "YES"),
        ("Borders: preserve conservative closure",
         "Borders is already high-F1; avoid broad expansion or relaxed FP filters.",
         lambda row: row["Relation"] == "countryLandBordersCountry",
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Output: normalized duplicate suppression",
         "Duplicate normalized outputs only add denominator burden before official dedupe.",
         lambda row: has_tags(row, {"DUPLICATE_OUTPUT"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "LOW", "YES", "NO", "NO", "NO", "NO"),
        ("Stock: exchange-name alias normalization",
         "Exchange naming variants can mismatch official aliases.",
         lambda row: row["Relation"] == "companyTradesAtStockExchange" and has_tags(row, {"ALIAS_OR_FORMAT_VARIANT", "NORMALIZATION_MISMATCH"}),
         "SAFE_WITH_EXISTING_CALIBRATION", "LOW", "MEDIUM", "UNCERTAIN", "NO", "NO", "NO", "NO"),
    ]
    out = []
    for name, pattern, predicate, compatibility, difficulty, risk, generalizable, calls, action_sem, graph_sem, calibration_shift in candidates:
        affected = [row for row in rows if predicate(row)]
        examples = affected[:5]
        rows_fixed = 0 if name == "Borders: preserve conservative closure" else len(affected)
        fp_reduction = 0 if name == "Borders: preserve conservative closure" else sum(int(row["fp"]) for row in affected)
        fn_reduction = 0 if name == "Borders: preserve conservative closure" else sum(int(row["fn"]) for row in affected)
        out.append({
            "rule_name": name,
            "observed_pattern": pattern,
            "affected_train_rows": len(affected),
            "examples": "; ".join(
                f"{row['row_index']}:{row['SubjectEntity']}/{row['Relation']}"
                for row in examples),
            "potential_rows_fixed": rows_fixed,
            "potential_rows_harmed": "unknown_without_heldout",
            "fp_reduction": fp_reduction,
            "fn_reduction": fn_reduction,
            "implementation_complexity": difficulty,
            "risk": risk,
            "generalizable": generalizable,
            "requires_model_calls": calls,
            "changes_action_semantics": action_sem,
            "changes_hypothesis_graph": graph_sem,
            "changes_m20_m21_state_distribution": calibration_shift,
            "current_v3_calibration_corpus_compatibility": compatibility,
        })
    return sorted(out, key=lambda r: (
        r["implementation_complexity"] != "LOW",
        -int(r["potential_rows_fixed"]),
        r["risk"],
        r["rule_name"],
    ))


def render_interventions(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = ["# Intervention Candidates", ""]
    for row in rows:
        lines.extend((
            f"## {row['rule_name']}",
            "",
            f"- observed pattern: {row['observed_pattern']}",
            f"- affected TRAIN rows: {row['affected_train_rows']}",
            f"- examples: {row['examples'] or 'none'}",
            f"- potential rows fixed: {row['potential_rows_fixed']}",
            f"- potential rows harmed: {row['potential_rows_harmed']}",
            f"- FP reduction: {row['fp_reduction']}",
            f"- FN reduction: {row['fn_reduction']}",
            f"- implementation complexity: {row['implementation_complexity']}",
            f"- risk: {row['risk']}",
            f"- generalizable: {row['generalizable']}",
            f"- requires model calls: {row['requires_model_calls']}",
            f"- changes action semantics: {row['changes_action_semantics']}",
            f"- changes hypothesis graph: {row['changes_hypothesis_graph']}",
            f"- changes M20/M21 distribution: {row['changes_m20_m21_state_distribution']}",
            f"- calibration compatibility: {row['current_v3_calibration_corpus_compatibility']}",
            "",
        ))
    return "\n".join(lines)


def counterfactuals(
    pred_rows: Sequence[dict[str, Any]],
    gold_rows: Sequence[dict[str, Any]],
    row_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    baseline = official_metrics(pred_rows, gold_rows)
    baseline_macro = baseline["*** All Relations ***"]["macro-f1"]
    gold_by_id = rows_by_identity(gold_rows)
    row_by_id = {(row["SubjectEntity"], row["Relation"]): row for row in row_records}

    def evaluate_variant(name: str, rows: list[dict[str, Any]], oracle: bool) -> dict[str, Any]:
        metrics = official_metrics(rows, gold_rows)
        out = {
            "rule_name": name,
            "train_oracle": oracle,
            "baseline_macro_f1": baseline_macro,
            "counterfactual_macro_f1": metrics["*** All Relations ***"]["macro-f1"],
            "delta_macro_f1": metrics["*** All Relations ***"]["macro-f1"] - baseline_macro,
            "baseline_micro_f1": baseline["*** All Relations ***"]["micro-f1"],
            "counterfactual_micro_f1": metrics["*** All Relations ***"]["micro-f1"],
            "delta_micro_f1": metrics["*** All Relations ***"]["micro-f1"] - baseline["*** All Relations ***"]["micro-f1"],
        }
        for relation in sorted(EVAL.RELATION_TYPE):
            out[f"{relation}_delta_macro_f1"] = (
                metrics[relation]["macro-f1"] - baseline[relation]["macro-f1"])
        return out

    variants: list[dict[str, Any]] = []
    numeric_drop = []
    for row in pred_rows:
        relation = row["Relation"]
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        if relation in NUMERIC_RELATIONS:
            preds = [pred for pred in preds if parse_num(pred) is not None]
        numeric_drop.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("drop_nonnumeric_numeric_predictions", numeric_drop, False))

    empty_to_survived = []
    for row in pred_rows:
        key = (row["SubjectEntity"], row["Relation"])
        record = row_by_id.get(key)
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        if record and not preds:
            stages = json.loads(str(record.get("stage_values", "{}")))
            survived = stages.get("CONTROL_SURVIVED") or []
            if survived:
                preds = [str(v) for v in survived]
        empty_to_survived.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("emit_control_survived_when_empty", empty_to_survived, False))

    single_keep_first = []
    for row in pred_rows:
        relation = row["Relation"]
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        if relation in {"hasArea", "hasCapacity", "companyTradesAtStockExchange", "personHasCityOfDeath"} and len(preds) > 1:
            preds = preds[:1]
        single_keep_first.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("single_valued_keep_first_prediction", single_keep_first, False))

    oracle_remove_fp = []
    for row in pred_rows:
        key = (row["SubjectEntity"], row["Relation"])
        gold = gold_aliases((gold_by_id[key].get("ObjectEntities") or []))
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        preds = [pred for pred in preds if any_gold_in_values(gold, [pred], row["Relation"])]
        oracle_remove_fp.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("TRAIN_ORACLE_remove_false_positives", oracle_remove_fp, True))

    oracle_add_recalled = []
    for row in pred_rows:
        key = (row["SubjectEntity"], row["Relation"])
        record = row_by_id.get(key)
        gold = gold_aliases((gold_by_id[key].get("ObjectEntities") or []))
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        if record:
            stages = json.loads(str(record.get("stage_values", "{}")))
            values = []
            for vals in stages.values():
                values.extend(str(v) for v in vals)
            for aliases in gold:
                if any(value_matches(value, aliases, row["Relation"]) for value in values):
                    canonical = aliases[0]
                    if not any(value_matches(pred, aliases, row["Relation"]) for pred in preds):
                        preds.append(canonical)
        oracle_add_recalled.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("TRAIN_ORACLE_add_recalled_gold", oracle_add_recalled, True))

    oracle_seen_exact = []
    for row in pred_rows:
        key = (row["SubjectEntity"], row["Relation"])
        record = row_by_id.get(key)
        gold = gold_aliases((gold_by_id[key].get("ObjectEntities") or []))
        preds, _ = flatten_predictions(row.get("ObjectEntities") or [])
        if record:
            stages = json.loads(str(record.get("stage_values", "{}")))
            values = []
            for vals in stages.values():
                values.extend(str(v) for v in vals)
            if gold and all(
                any(value_matches(value, aliases, row["Relation"]) for value in values)
                for aliases in gold
            ):
                preds = [aliases[0] for aliases in gold if aliases]
        oracle_seen_exact.append({**row, "ObjectEntities": preds})
    variants.append(evaluate_variant("TRAIN_ORACLE_exact_gold_when_all_seen", oracle_seen_exact, True))
    return variants


def render_cases(rows: Sequence[Mapping[str, Any]], *, limit_per_category: int = 3) -> str:
    categories = [
        "UNDER_ENUMERATION",
        "OVER_ENUMERATION",
        "EMPTY_PREDICTION",
        "NUMERIC_LARGE_MISS",
        "NUMERIC_SCALE_ERROR",
        "CORRECT_CANDIDATE_IN_FINAL_GRAPH_BUT_NOT_OUTPUT",
        "CORRECT_CANDIDATE_NEVER_RECALLED",
        "WRONG_ENTITY",
        "MULTI_WHEN_SINGLE_GOLD",
    ]
    lines = ["# Representative TRAIN Failure Cases", ""]
    for category in categories:
        examples = [
            row for row in rows
            if category in set(json.loads(str(row["failure_tags"])))
            and not row["exact_set_match"]
        ][:limit_per_category]
        if not examples:
            continue
        lines.extend((f"## {category}", ""))
        for row in examples:
            lines.extend((
                f"### Row {row['row_index']} - {row['SubjectEntity']} / {row['Relation']}",
                "",
                f"- GOLD: {row['gold']}",
                f"- PREDICTED: {row['predicted']}",
                f"- failure tags: {row['failure_tags']}",
                f"- cardinality: gold={row['gold_count']} pred={row['pred_count']}",
                f"- localization: {row['pipeline_localization']}",
                f"- initial evidence has gold: {row['gold_in_initial_evidence']}",
                f"- pre-M8 graph has gold: {row['gold_in_pre_m8_graph']}",
                f"- final graph has gold: {row['gold_in_final_graph']}",
                f"- V3 actions executed: {row['v3_actions_executed']}",
                f"- pre-M8 hypotheses: {row['pre_m8_hypotheses']}",
                f"- final hypotheses: {row['final_hypotheses']}",
                f"- why wrong: FP={row['fp']} FN={row['fn']} "
                f"empty_reason={row['empty_reason']} stopped={row['stopped_reason']}",
                "",
            ))
    return "\n".join(lines)


def render_top_opportunities(
    score_loss: Sequence[Mapping[str, Any]],
    interventions: Sequence[Mapping[str, Any]],
    relation_rows: Sequence[Mapping[str, Any]],
    localization_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = ["# Top Opportunities", ""]
    sections = [
        ("TOP 15 LARGEST SCORE-LOSS SOURCES", score_loss[:15],
         ("rank", "failure_category", "Relation", "rows_affected", "fp", "fn",
          "estimated_recoverable_opportunity", "implementation_difficulty", "risk")),
        ("TOP 15 CHEAPEST GENERALIZABLE FIXES", interventions[:15],
         ("rule_name", "affected_train_rows", "fp_reduction", "fn_reduction",
          "implementation_complexity", "risk", "current_v3_calibration_corpus_compatibility")),
        ("TOP 15 HIGHEST EXPECTED SCORE-RECOVERY INTERVENTIONS",
         sorted(interventions, key=lambda r: (-(int(r["fp_reduction"]) + int(r["fn_reduction"])), r["risk"]))[:15],
         ("rule_name", "affected_train_rows", "fp_reduction", "fn_reduction",
          "implementation_complexity", "risk", "current_v3_calibration_corpus_compatibility")),
        ("TOP 10 CONTROLLER/M21 OPPORTUNITIES",
         [r for r in interventions if r["changes_m20_m21_state_distribution"] == "YES"][:10],
         ("rule_name", "affected_train_rows", "fp_reduction", "fn_reduction",
          "implementation_complexity", "risk", "current_v3_calibration_corpus_compatibility")),
        ("TOP 10 RECALL/PROMPT OPPORTUNITIES",
         [r for r in interventions if r["requires_model_calls"] == "YES"][:10],
         ("rule_name", "affected_train_rows", "fp_reduction", "fn_reduction",
          "implementation_complexity", "risk", "current_v3_calibration_corpus_compatibility")),
        ("TOP 10 FINALIZATION/NORMALIZATION OPPORTUNITIES",
         [r for r in interventions if r["current_v3_calibration_corpus_compatibility"] == "SAFE_WITH_EXISTING_CALIBRATION"][:10],
         ("rule_name", "affected_train_rows", "fp_reduction", "fn_reduction",
          "implementation_complexity", "risk", "current_v3_calibration_corpus_compatibility")),
    ]
    for title, rows, fields in sections:
        lines.extend((f"## {title}", ""))
        for row in rows:
            lines.append("- " + " | ".join(f"{field}={row.get(field, '')}" for field in fields))
        lines.append("")
    lines.extend(("## TOP DO-NOT-BREAK BEHAVIORS", ""))
    for row in relation_rows:
        if row["Relation"] == "countryLandBordersCountry":
            lines.extend((
                f"- Preserve conservative borders behavior: mean F1={float(row['mean_f1']):.3f}, "
                f"FP={row['fp']}, FN={row['fn']}, exact={row['exact']}/{row['rows']}.",
                "- Do not add broad expansion for frozen/conservative borders.",
                "- Do not relax duplicate/alias suppression or final cardinality safeguards for borders.",
            ))
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> None:
    predictions = Path(args.predictions)
    gold_path = Path(args.gold)
    telemetry_dir = resolve_run_dir(Path(args.telemetry_dir))
    out = Path(args.output_dir)

    if "/test" in predictions.as_posix() or "/val" in predictions.as_posix():
        raise SystemExit("predictions path looks like VAL/TEST; TRAIN only")
    if "/test" in gold_path.as_posix() or "/val" in gold_path.as_posix():
        raise SystemExit("gold path looks like VAL/TEST; TRAIN only")
    if sha256_file(gold_path) != TRAIN_SHA256:
        raise SystemExit(f"TRAIN hash mismatch for {gold_path}")
    if sha256_file(Path("benchmark/evaluate.py")) != EVALUATOR_SHA256:
        raise SystemExit("official evaluator hash mismatch")

    pred_rows = read_jsonl(predictions)
    gold_rows = read_jsonl(gold_path)
    if len(pred_rows) != EXPECTED_ROWS or len(gold_rows) != EXPECTED_ROWS:
        raise SystemExit(
            f"expected {EXPECTED_ROWS} prediction/gold rows, got "
            f"{len(pred_rows)}/{len(gold_rows)}")
    metrics = official_metrics(pred_rows, gold_rows)
    validate_metrics(metrics)

    inference = by_row(read_jsonl(telemetry_dir / "inference_telemetry.jsonl"))
    pre_graphs = by_row(read_jsonl(telemetry_dir / "v3_pre_m8_hypothesis_graphs.jsonl"))
    final_graphs = by_row(read_jsonl(telemetry_dir / "v3_final_hypothesis_graphs.jsonl"))
    train_telemetry = read_jsonl(telemetry_dir / "train_telemetry.jsonl")
    action_effects = read_jsonl(telemetry_dir / "v3_action_effects.jsonl")
    row_records = row_forensics(
        gold_rows, rows_by_identity(pred_rows), inference, pre_graphs, final_graphs)
    rel_summary = relation_summary(row_records)
    category_summary = failure_category_summary(row_records)
    localization = pipeline_localization(row_records)
    card_rows, card_matrix = cardinality_analysis(row_records)
    numeric_rows, numeric_bucket_rows = numeric_tables(row_records)
    action_rows = action_failure_analysis(
        row_records, train_telemetry, action_effects, gold_rows)
    score_loss = score_loss_ranking(row_records)
    interventions = intervention_candidates(row_records)
    counterfactual_rows = counterfactuals(pred_rows, gold_rows, row_records)

    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "v3-train-error-forensics-v1",
        "source_head": git_head(),
        "run_dir": str(telemetry_dir),
        "predictions": str(predictions),
        "predictions_sha256": sha256_file(predictions),
        "gold": str(gold_path),
        "train_sha256": sha256_file(gold_path),
        "evaluator_sha256": sha256_file(Path("benchmark/evaluate.py")),
        "rows": len(row_records),
        "official_metrics": metrics,
        "row_counts": {
            "exact": sum(1 for row in row_records if row["exact_set_match"]),
            "partial": sum(1 for row in row_records if row["partial_match"]),
            "complete_miss": sum(1 for row in row_records if row["complete_miss"]),
            "incorrect": sum(1 for row in row_records if not row["exact_set_match"]),
            "empty": sum(1 for row in row_records if row["empty_prediction"]),
        },
        "correct_gold_seen_before_final_wrong_rows": sum(
            1 for row in row_records
            if not row["exact_set_match"]
            and (
                row["gold_in_initial_evidence"]
                or row["gold_in_pre_m8_graph"]
                or row["gold_in_final_graph"]
                or row["gold_verified"]
            )
        ),
        "dominant_failure_by_relation": dominant_failures(row_records),
        "analysis_outputs": [],
    }

    write_json(out / "summary.json", summary)
    write_jsonl(out / "per_row_errors.jsonl", row_records)
    per_row_fields = [
        "row_index", "SubjectEntity", "Relation", "gold", "predicted",
        "normalized_gold", "normalized_predictions", "gold_count", "pred_count",
        "cardinality_delta", "empty_prediction", "correct_values",
        "false_positives", "false_negatives", "tp", "fp", "fn",
        "exact_set_match", "partial_match", "complete_miss", "precision",
        "recall", "f1", "official_numeric_tolerance_correct", "failure_tags",
        "pipeline_localization", "gold_in_initial_evidence",
        "gold_in_pre_m8_graph", "gold_in_final_graph", "gold_verified",
        "gold_verified_valid", "gold_rejected_or_suppressed",
        "gold_survived_final_prediction", "failure_state", "empty_reason",
        "stopped_reason", "v3_actions_executed", "hypothesis_count_pre_m8",
        "hypothesis_count_final", "numeric_gold_values", "numeric_pred_values",
        "numeric_nearest_pred", "numeric_nearest_gold",
        "numeric_absolute_error", "numeric_relative_error", "numeric_ratio",
        "numeric_ratio_bucket", "numeric_within_5_percent",
        "numeric_numeric_pred_count", "numeric_nonnumeric_pred_count",
        "numeric_failure_tags",
    ]
    write_csv(out / "per_row_errors.csv", row_records, per_row_fields)
    write_csv(out / "relation_summary.csv", rel_summary, [
        "Relation", "rows", "exact", "partial", "complete_miss", "empty",
        "tp", "fp", "fn", "mean_precision", "mean_recall", "mean_f1"])
    write_csv(out / "failure_category_summary.csv", category_summary, [
        "Relation", "failure_category", "rows", "fp", "fn", "f1_loss"])
    write_csv(out / "pipeline_localization.csv", localization, [
        "Relation", "pipeline_localization", "rows"])
    write_csv(out / "cardinality_analysis.csv", card_rows, [
        "Relation", "rows", "under_enumerated_rows", "under_enumerated_pct",
        "over_enumerated_rows", "over_enumerated_pct",
        "exact_cardinality_wrong_entities", "singleton_when_multi_gold",
        "mean_missing_gold_entities", "mean_extra_entities"])
    write_json(out / "cardinality_matrices.json", card_matrix)
    write_csv(out / "numeric_error_analysis.csv", numeric_rows, [
        "row_index", "SubjectEntity", "Relation", "gold_numeric_values",
        "pred_numeric_values", "nearest_pred", "nearest_gold", "absolute_error",
        "relative_error", "ratio", "ratio_bucket", "within_5_percent",
        "numeric_pred_count", "nonnumeric_pred_count", "numeric_failure_tags"])
    write_csv(out / "numeric_ratio_buckets.csv", numeric_bucket_rows, [
        "Relation", "bucket", "rows"])
    write_csv(out / "action_failure_analysis.csv", action_rows, [
        "action_family", "executed", "affected_wrong_rows", "affected_correct_rows",
        "candidate_additions", "candidate_removals_or_contradictions",
        "gold_candidate_touches", "false_positive_named", "residual_improved",
        "residual_degraded", "effects_with_action_record"])
    write_csv(out / "score_loss_ranking.csv", score_loss, [
        "rank", "failure_category", "Relation", "rows_affected", "fp", "fn",
        "estimated_recoverable_opportunity", "implementation_difficulty", "risk"])
    write_csv(out / "intervention_candidates.csv", interventions, [
        "rule_name", "observed_pattern", "affected_train_rows", "examples",
        "potential_rows_fixed", "potential_rows_harmed", "fp_reduction",
        "fn_reduction", "implementation_complexity", "risk", "generalizable",
        "requires_model_calls", "changes_action_semantics",
        "changes_hypothesis_graph", "changes_m20_m21_state_distribution",
        "current_v3_calibration_corpus_compatibility"])
    (out / "intervention_candidates.md").write_text(
        render_interventions(interventions), encoding="utf-8")
    write_csv(out / "counterfactual_rule_results.csv", counterfactual_rows, [
        "rule_name", "train_oracle", "baseline_macro_f1",
        "counterfactual_macro_f1", "delta_macro_f1", "baseline_micro_f1",
        "counterfactual_micro_f1", "delta_micro_f1",
        *[f"{relation}_delta_macro_f1" for relation in sorted(EVAL.RELATION_TYPE)],
    ])
    (out / "representative_cases.md").write_text(
        render_cases(row_records), encoding="utf-8")
    (out / "top_opportunities.md").write_text(
        render_top_opportunities(score_loss, interventions, rel_summary, localization),
        encoding="utf-8")
    provenance = {
        "schema_version": "v3-train-error-forensics-provenance-v1",
        "source_head": git_head(),
        "frozen_test_submission_source": "16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5",
        "run_dir": str(telemetry_dir),
        "predictions_sha256": sha256_file(predictions),
        "train_sha256": sha256_file(gold_path),
        "evaluator_sha256": sha256_file(Path("benchmark/evaluate.py")),
        "model_calls": 0,
        "test_read": False,
        "val_read": False,
        "production_behavior_modified": False,
    }
    write_json(out / "analysis_provenance.json", provenance)
    artifact_names = sorted(
        path.name for path in out.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (out / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(out / name)}  {name}\n" for name in artifact_names),
        encoding="utf-8",
    )
    summary["analysis_outputs"] = artifact_names + ["SHA256SUMS.txt"]
    write_json(out / "summary.json", summary)
    artifact_names = sorted(
        path.name for path in out.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (out / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(out / name)}  {name}\n" for name in artifact_names),
        encoding="utf-8",
    )
    print(json.dumps({
        "output_dir": str(out),
        "rows": len(row_records),
        "metrics_verified": True,
        "exact": summary["row_counts"]["exact"],
        "partial": summary["row_counts"]["partial"],
        "complete_miss": summary["row_counts"]["complete_miss"],
        "sha256s": str(out / "SHA256SUMS.txt"),
    }, indent=2, sort_keys=True))


def dominant_failures(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for relation in sorted({row["Relation"] for row in rows}):
        counter: Counter[str] = Counter()
        for row in rows:
            if row["Relation"] == relation and not row["exact_set_match"]:
                counter.update(json.loads(str(row["failure_tags"])))
        out[relation] = counter.most_common(8)
    return out


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--telemetry-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
