#!/usr/bin/env python3
"""Score one targeted Class-B TRAIN diagnostic against the authoritative baseline.

CPU only. Compares the persisted state of a Class-B diagnostic run against the
authoritative V3 TRAIN run, for one relation, using the official evaluator.

**This script may read TRAIN gold. It must never be imported by production.**
A test asserts that nothing under ``src/cover_kbc`` imports it. It lives in
``scripts/`` for that reason: the production package has no path to it.

What it answers, which is more than "did the number go up". Audit 0077 §12 asks
whether a new capacity prompt

  A. recalls the gold-like canonical value more often;
  B. merely creates more numeric alternatives;
  C. moves values toward the correct scale;
  D. increases false-positive ambiguity;

and those four have different signatures. A rises with
``gold_like_candidate_rows``; B rises with ``mean_candidates_per_row`` while A
does not; C shows as movement out of the 10x/100x ratio buckets; D shows as a
rising candidate count with a falling emitted-precision.

No numeric target range is hard-coded anywhere. "Gold-like" is computed from
the official tolerance against the gold file, and it is a *diagnostic label
applied after the run*, never a production predicate.

Usage::

    python scripts/evaluate_v3_1_diagnostic.py \
      --relation hasCapacity \
      --baseline-run outputs/v3_train_collect_v2_coverage/collection/<run> \
      --diagnostic-run outputs/<new diagnostic run> \
      --gold benchmark/data/train.jsonl \
      --output-dir outputs/v3_1_class_b_diagnostics/capacity
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA_VERSION = "v3-1-class-b-diagnostic-v1"

#: Ratio buckets for a wrong numeric prediction, as audit 0075 defined them.
RATIO_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("~0.01x", 0.005, 0.02),
    ("~0.1x", 0.05, 0.2),
    ("~1x", 0.2, 5.0),
    ("~10x", 5.0, 20.0),
    ("~100x", 50.0, 200.0),
)


def load_evaluator(path: Path):
    spec = importlib.util.spec_from_file_location("official_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --------------------------------------------------------------------------
# numeric helpers - the official parser's own notion of a number
# --------------------------------------------------------------------------


def parse_number(evaluator, value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return evaluator.try_parse_number(str(value))


def gold_numbers(evaluator, gold_row: Mapping[str, Any]) -> list[float]:
    out = []
    for alias in gold_row.get("ObjectEntities") or []:
        text = alias[0] if isinstance(alias, list) and alias else alias
        number = parse_number(evaluator, text)
        if number is not None:
            out.append(number)
    return out


def within_tolerance(value: float, targets: Sequence[float], tolerance: float = 0.05) -> bool:
    return any(
        abs(value - target) <= tolerance * abs(target) if target else value == target
        for target in targets
    )


def ratio_bucket(value: float, target: float) -> str:
    if not target:
        return "undefined"
    ratio = value / target
    for name, low, high in RATIO_BUCKETS:
        if low <= ratio < high:
            return name
    return "other"


# --------------------------------------------------------------------------
# per-run extraction
# --------------------------------------------------------------------------


def relation_rows(rows: Sequence[Mapping[str, Any]], relation: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("Relation") == relation]


def candidate_table(
    evaluator, telemetry: Sequence[Mapping[str, Any]], gold_by_key: Mapping[tuple, Mapping],
    relation: str, run_label: str,
) -> list[dict[str, Any]]:
    """Audit 0077 §12: every candidate, with the state that produced it.

    Instrumentation, not heuristics. ``gold_like`` is attached for offline
    analysis only and is computed with the official tolerance; nothing in this
    table feeds a rule.
    """
    numeric = evaluator.RELATION_TYPE.get(relation) == "numeric"
    view_by_record: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for row in telemetry:
        if row.get("Relation") != relation:
            continue
        view_by_record = {
            generation["record_id"]: generation.get("view_id", "")
            for generation in row.get("generations") or []
        }
        key = (row["SubjectEntity"], row["Relation"])
        gold_row = gold_by_key.get(key, {})
        targets = gold_numbers(evaluator, gold_row)
        # "Gold-like" is whatever the *official evaluator* would count as a hit
        # for this relation's type: tolerance for numbers, its own normalised
        # alias match for entities. Using a numeric test on an entity relation
        # would silently report zero recall for every string relation.
        gold_keys = {
            evaluator.normalize_string(alias)
            for aliases in (gold_row.get("ObjectEntities") or [])
            for alias in (aliases if isinstance(aliases, list) else [aliases])
        }
        emitted = {
            evaluator.normalize_string(str(value))
            for value in row["stage_values"]["FINAL_EMITTED"]
        }
        for candidate in row.get("candidates") or []:
            value = candidate.get("numeric_value")
            views = sorted({
                view_by_record.get(record_id, "")
                for record_id in candidate.get("record_ids") or []
            } - {""})
            rows.append({
                "run": run_label,
                "row_index": row.get("row_index"),
                "SubjectEntity": row["SubjectEntity"],
                "candidate_key": candidate.get("candidate_key"),
                "raw_text": ";".join(candidate.get("surface_forms") or []),
                "parsed_numeric_value": value,
                "unit": candidate.get("unit") or candidate.get("source_unit") or "",
                "qualifier": ";".join(candidate.get("facet_ids") or []),
                "source_views": ";".join(views),
                "acquisition_groups": ";".join(candidate.get("acquisition_groups") or []),
                "support_count": candidate.get("independent_support"),
                "verifier_label": candidate.get("verifier_label") or "",
                "verifier_valid_prob": candidate.get("verifier_valid_prob"),
                "hypothesis_status": candidate.get("final_status"),
                "score": candidate.get("score"),
                "emitted": evaluator.normalize_string(
                    str(candidate.get("output_value"))) in emitted,
                "gold_like": bool(
                    value is not None and targets and within_tolerance(value, targets)
                ) if numeric else bool(
                    gold_keys
                    and evaluator.normalize_string(
                        str(candidate.get("output_value"))) in gold_keys
                ),
                "ratio_bucket": (
                    ratio_bucket(value, targets[0])
                    if value is not None and targets else ""),
            })
    return rows


def numeric_profile(
    evaluator, predictions: Sequence[Mapping[str, Any]],
    gold_by_key: Mapping[tuple, Mapping], relation: str,
) -> dict[str, Any]:
    """Numeric-relation shape: empties, tolerance, misses, scale buckets."""
    empty = within = exact = large_miss = too_large = too_small = 0
    buckets: dict[str, int] = {}
    total = 0
    for prediction in predictions:
        if prediction.get("Relation") != relation:
            continue
        total += 1
        key = (prediction["SubjectEntity"], prediction["Relation"])
        targets = gold_numbers(evaluator, gold_by_key.get(key, {}))
        values = [
            number for number in
            (parse_number(evaluator, value) for value in prediction["ObjectEntities"])
            if number is not None
        ]
        if not prediction["ObjectEntities"]:
            empty += 1
            continue
        if not values or not targets:
            continue
        value, target = values[0], targets[0]
        if within_tolerance(value, targets):
            within += 1
            if value == target:
                exact += 1
            continue
        bucket = ratio_bucket(value, target)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        if target and abs(value - target) > 0.5 * abs(target):
            large_miss += 1
        if value > target:
            too_large += 1
        elif value < target:
            too_small += 1
    return {
        "rows": total,
        "empty_rows": empty,
        "within_5_percent": within,
        "exact": exact,
        "large_miss": large_miss,
        "too_large": too_large,
        "too_small": too_small,
        "ratio_buckets": dict(sorted(buckets.items())),
    }


def entity_profile(
    evaluator, predictions: Sequence[Mapping[str, Any]],
    gold_by_key: Mapping[tuple, Mapping], relation: str,
) -> dict[str, Any]:
    """Entity-relation shape: cardinality, FP/FN burden."""
    empty = tp = fp = fn = total = 0
    counts: dict[int, int] = {}
    for prediction in predictions:
        if prediction.get("Relation") != relation:
            continue
        total += 1
        key = (prediction["SubjectEntity"], prediction["Relation"])
        gold_row = gold_by_key.get(key, {})
        gold_aliases = gold_row.get("ObjectEntities") or []
        gold_keys = {
            evaluator.normalize_string(a[0] if isinstance(a, list) and a else a)
            for a in gold_aliases
        }
        predicted = prediction["ObjectEntities"]
        counts[len(predicted)] = counts.get(len(predicted), 0) + 1
        if not predicted:
            empty += 1
        predicted_keys = {evaluator.normalize_string(str(v)) for v in predicted}
        hit = predicted_keys & gold_keys
        tp += len(hit)
        fp += len(predicted_keys - gold_keys)
        fn += len(gold_keys - predicted_keys)
    return {
        "rows": total,
        "empty_rows": empty,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "prediction_count_distribution": dict(sorted(counts.items())),
        "mean_predictions": round(
            sum(k * v for k, v in counts.items()) / total, 4) if total else 0.0,
    }


def candidate_summary(rows: Sequence[Mapping[str, Any]], run_label: str) -> dict[str, Any]:
    subset = [row for row in rows if row["run"] == run_label]
    by_row: dict[Any, list[Mapping[str, Any]]] = {}
    for row in subset:
        by_row.setdefault(row["row_index"], []).append(row)
    gold_like_rows = sum(
        1 for candidates in by_row.values() if any(c["gold_like"] for c in candidates))
    gold_like_not_emitted = sum(
        1 for candidates in by_row.values()
        if any(c["gold_like"] for c in candidates)
        and not any(c["gold_like"] and c["emitted"] for c in candidates))
    return {
        "rows_with_candidates": len(by_row),
        "total_candidates": len(subset),
        "mean_candidates_per_row": round(len(subset) / len(by_row), 4) if by_row else 0.0,
        "distinct_numeric_values": len({
            row["parsed_numeric_value"] for row in subset
            if row["parsed_numeric_value"] is not None}),
        "gold_like_candidate_rows": gold_like_rows,
        "gold_like_seen_but_not_emitted": gold_like_not_emitted,
        "accepted_candidates": sum(
            1 for row in subset if row["hypothesis_status"] == "ACCEPTED"),
    }


def macro_for_relation(evaluator, predictions, gold, relation) -> dict[str, float]:
    scored = evaluator.evaluate_per_sr_pair(
        list(predictions), list(gold), evaluator.RELATION_TYPE, tolerance=0.05)
    rows = [row for row in scored if row["Relation"] == relation]
    if not rows:
        return {"macro-p": 0.0, "macro-r": 0.0, "macro-f1": 0.0, "rows": 0}
    return {
        "macro-p": round(sum(r["p"] for r in rows) / len(rows), 6),
        "macro-r": round(sum(r["r"] for r in rows) / len(rows), 6),
        "macro-f1": round(sum(r["f1"] for r in rows) / len(rows), 6),
        "rows": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relation", required=True)
    parser.add_argument("--baseline-run", required=True, type=Path)
    parser.add_argument("--diagnostic-run", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--evaluator", type=Path,
                        default=REPO_ROOT / "benchmark" / "evaluate.py")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    evaluator = load_evaluator(args.evaluator)
    relation = args.relation
    if relation not in evaluator.RELATION_TYPE:
        parser.error(f"unknown relation {relation!r}")

    gold = read_jsonl(args.gold)
    gold_by_key = {(row["SubjectEntity"], row["Relation"]): row for row in gold}
    gold_subset = relation_rows(gold, relation)
    if not gold_subset:
        parser.error(f"gold contains no {relation} rows")

    runs: dict[str, dict[str, Any]] = {}
    for label, run_dir in (("baseline", args.baseline_run), ("diagnostic", args.diagnostic_run)):
        predictions_path = run_dir / "predictions.jsonl"
        telemetry_path = run_dir / "inference_telemetry.jsonl"
        if not predictions_path.exists():
            parser.error(f"{label}: missing {predictions_path}")
        predictions = relation_rows(read_jsonl(predictions_path), relation)
        telemetry = (
            relation_rows(read_jsonl(telemetry_path), relation)
            if telemetry_path.exists() else []
        )
        runs[label] = {
            "run_dir": str(run_dir),
            "predictions": predictions,
            "telemetry": telemetry,
            "predictions_sha256": sha256_file(predictions_path),
        }

    # A diagnostic run is relation-filtered, so the official evaluator needs a
    # gold file restricted the same way; scoring 100 predictions against 477
    # gold rows would count 377 absent rows as empty predictions.
    numeric = evaluator.RELATION_TYPE[relation] == "numeric"
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics: dict[str, Any] = {}
    candidate_rows: list[dict[str, Any]] = []
    for label, payload in runs.items():
        predictions = payload["predictions"]
        metrics[label] = {
            "official": macro_for_relation(evaluator, predictions, gold_subset, relation),
            "shape": (
                numeric_profile(evaluator, predictions, gold_by_key, relation)
                if numeric else
                entity_profile(evaluator, predictions, gold_by_key, relation)
            ),
        }
        candidate_rows.extend(
            candidate_table(evaluator, payload["telemetry"], gold_by_key, relation, label))

    for label in runs:
        metrics[label]["candidates"] = candidate_summary(candidate_rows, label)

    if candidate_rows:
        write_csv(out_dir / "candidate_instrumentation.csv",
                  list(candidate_rows[0].keys()), candidate_rows)

    base_f1 = metrics["baseline"]["official"]["macro-f1"]
    new_f1 = metrics["diagnostic"]["official"]["macro-f1"]
    comparison_rows = [{
        "relation": relation,
        "metric": name,
        "baseline": metrics["baseline"]["official"][name],
        "diagnostic": metrics["diagnostic"]["official"][name],
        "delta": round(
            metrics["diagnostic"]["official"][name] - metrics["baseline"]["official"][name], 6),
    } for name in ("macro-p", "macro-r", "macro-f1")]
    write_csv(out_dir / "relation_metrics.csv",
              ["relation", "metric", "baseline", "diagnostic", "delta"], comparison_rows)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "relation": relation,
        "gold": str(args.gold),
        "gold_sha256": sha256_file(args.gold),
        "gold_rows_for_relation": len(gold_subset),
        "evaluator_sha256": sha256_file(args.evaluator),
        "baseline_run": runs["baseline"]["run_dir"],
        "baseline_predictions_sha256": runs["baseline"]["predictions_sha256"],
        "diagnostic_run": runs["diagnostic"]["run_dir"],
        "diagnostic_predictions_sha256": runs["diagnostic"]["predictions_sha256"],
        "metrics": metrics,
        "delta_macro_f1": round(new_f1 - base_f1, 6),
        "note": (
            "Diagnostic only. The Audit 0073 M20/M21 calibration does not describe "
            "a Class-B run; this comparison is a control, not a validity claim."
        ),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(f"{relation}: macro-F1 {base_f1:.5f} -> {new_f1:.5f} ({new_f1 - base_f1:+.5f})")
    for label in ("baseline", "diagnostic"):
        shape = metrics[label]["shape"]
        cands = metrics[label]["candidates"]
        print(f"  {label:10s} empty={shape['empty_rows']:3d} "
              f"candidates/row={cands['mean_candidates_per_row']:.2f} "
              f"gold_like_rows={cands['gold_like_candidate_rows']:3d} "
              f"gold_like_not_emitted={cands['gold_like_seen_but_not_emitted']:3d}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
