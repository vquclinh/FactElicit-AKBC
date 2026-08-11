#!/usr/bin/env python3
"""Analyse the companyTradesAtStockExchange real-weight diagnostic. CPU only.

Audit 0081, phase B. Run this **after** the GPU artifact returns.

Why this is not the capacity analyzer with a relation string swapped: stock is a
**set** relation whose gold is legitimately empty on 34 of 100 TRAIN rows and
legitimately multi-valued on 12. Those three populations answer different
questions and averaging them hides the only thing worth knowing.

Audit 0080 rejected a prompt whose precision rose from 0.210 to 0.510 purely
because empty predictions went 13 -> 48. Stock is a *suppression* experiment, so
that failure mode is the expected one rather than a surprise, and the analyzer
is built to separate:

* **good suppression** - a false exchange removed, or an empty-gold row correctly
  emptied;
* **destructive abstention** - a true listing removed, a non-empty-gold row
  emptied, or a multi-listing collapsed to one.

Every gold comparison uses the official evaluator's own `normalize_string`, so
there is no second, incompatible alias matcher in the project.

TRAIN gold is joined **offline, after inference**. The diagnostic runner never
sees it, and a test asserts this module is not importable from production.

Usage::

    python scripts/analyze_stock_diagnostic.py \
      --baseline-run outputs/v3_train_collect_v2_coverage/collection/<run> \
      --diagnostic-run outputs/<stock run> \
      --gold benchmark/data/train.jsonl \
      --expected-source-sha <sha> \
      --output-dir outputs/v3_2_stock_diagnostic_analysis
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA_VERSION = "v3-2-stock-diagnostic-v1"
RELATION = "companyTradesAtStockExchange"
EXPECTED_ROWS = 100
EPSILON = 1e-9


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]],
              fieldnames: Iterable[str] | None = None) -> None:
    names = list(fieldnames or (rows[0].keys() if rows else ["empty"]))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --------------------------------------------------------------------------
# Gold matching - the evaluator's own semantics, never a second matcher
# --------------------------------------------------------------------------


def gold_alias_groups(evaluator, gold_row: Mapping[str, Any]) -> list[set[str]]:
    groups = []
    for aliases in gold_row.get("ObjectEntities") or []:
        members = aliases if isinstance(aliases, list) else [aliases]
        groups.append({evaluator.normalize_string(str(m)) for m in members})
    return groups


def gold_keys(evaluator, gold_row: Mapping[str, Any]) -> set[str]:
    return {key for group in gold_alias_groups(evaluator, gold_row) for key in group}


def score_row(evaluator, predicted: Sequence[Any],
              gold_row: Mapping[str, Any]) -> dict[str, Any]:
    """Maximum-bipartite counts, matching the evaluator's alias-group rule."""
    groups = gold_alias_groups(evaluator, gold_row)
    keys = [evaluator.normalize_string(str(v)) for v in predicted]
    used: set[int] = set()
    tp = 0
    for key in keys:
        for index, group in enumerate(groups):
            if index in used or key not in group:
                continue
            used.add(index)
            tp += 1
            break
    fp, fn = len(keys) - tp, len(groups) - tp
    precision = tp / len(keys) if keys else 1.0
    recall = tp / len(groups) if groups else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"tp": tp, "fp": fp, "fn": fn, "gold_size": len(groups),
            "predicted_size": len(keys), "precision": precision,
            "recall": recall, "f1": f1,
            "exact": tp == len(groups) and fp == 0}


def candidate_keys(evaluator, telemetry: Mapping[str, Any]) -> set[str]:
    return {
        evaluator.normalize_string(str(c.get("output_value") or ""))
        for c in (telemetry.get("candidates") or [])
        if str(c.get("output_value") or "").strip()
    }


# --------------------------------------------------------------------------
# Transition classification
# --------------------------------------------------------------------------


def classify(before: Mapping[str, Any], after: Mapping[str, Any],
             gold_size: int, gold_lost: int, gold_added: int,
             false_removed: int, false_added: int) -> str:
    """One label per row. Order matters: the destructive cases are named first."""
    if gold_size == 0:
        if before["predicted_size"] > 0 and after["predicted_size"] == 0:
            return "GOOD_EMPTY_GOLD_SUPPRESSION"
        if after["predicted_size"] > before["predicted_size"]:
            return "NEW_FALSE_CANDIDATE"
        if after["predicted_size"] < before["predicted_size"]:
            return "GOOD_FP_SUPPRESSION"
        return "UNCHANGED"

    if before["predicted_size"] > 0 and after["predicted_size"] == 0:
        return "DESTRUCTIVE_ABSTENTION"
    if gold_size >= 2 and before["tp"] >= 2 and after["predicted_size"] == 1:
        return "MULTI_LISTING_COLLAPSE"
    if after["tp"] < before["tp"]:
        return "GOLD_CANDIDATE_LOST"
    if after["tp"] > before["tp"]:
        return "RECALL_GAIN"
    # true positives held
    if after["fp"] < before["fp"]:
        if gold_size >= 2 and after["tp"] == before["tp"] == gold_size:
            return "MULTI_LISTING_PRESERVED"
        return "GOLD_PRESERVED_FP_REDUCED"
    if after["fp"] > before["fp"]:
        return "NEW_FALSE_CANDIDATE"
    if gold_lost or gold_added or false_removed or false_added:
        return "CANDIDATES_CHANGED_OUTPUT_UNCHANGED"
    return "UNCHANGED"


# --------------------------------------------------------------------------
# Provenance - audit 0080's corrected rule
# --------------------------------------------------------------------------


def check_provenance(run_dir: Path, expected_sha: str | None,
                     strict: bool) -> dict[str, Any]:
    problems: list[str] = []
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    predictions = read_jsonl(run_dir / "predictions.jsonl")
    relations = Counter(row["Relation"] for row in predictions)

    if len(predictions) != EXPECTED_ROWS:
        problems.append(f"{len(predictions)} prediction rows, expected {EXPECTED_ROWS}")
    if set(relations) != {RELATION}:
        problems.append(f"relations present: {dict(relations)}; expected only {RELATION}")

    errors_path = run_dir / "errors.json"
    errors = json.loads(errors_path.read_text()) if errors_path.exists() else []
    if errors:
        problems.append(f"{len(errors)} query error(s) recorded; a contaminated run "
                        "must not be analysed as a prompt result")

    accounting_path = run_dir / "run_accounting.json"
    accounting = json.loads(accounting_path.read_text()) if accounting_path.exists() else {}
    if accounting:
        for field, want in (("total_queries", EXPECTED_ROWS),
                            ("prediction_rows", EXPECTED_ROWS),
                            ("failed_queries", 0),
                            ("unresolved_invariant_errors", 0)):
            if accounting.get(field) != want:
                problems.append(
                    f"run_accounting.{field}={accounting.get(field)}, expected {want}")
    else:
        problems.append("run_accounting.json missing (audit 0078 gate not recorded)")

    # Audit 0080 phase B: provenance is git_revision. cover_kbc_version is the
    # package version ("0.1.0") and is identical on every commit, so comparing it
    # to a SHA is a check that can never pass.
    observed_sha = str(manifest.get("git_revision") or "")
    package_version = str(manifest.get("cover_kbc_version") or "")
    if expected_sha:
        if not observed_sha:
            problems.append(
                "manifest records no git_revision; source provenance cannot be "
                "established")
        elif not observed_sha.startswith(expected_sha[:12]):
            problems.append(
                f"source git_revision {observed_sha!r} != expected {expected_sha!r}")

    record = {
        "run_dir": str(run_dir),
        "prediction_rows": len(predictions),
        "relations": dict(relations),
        "query_errors": len(errors),
        "run_accounting": accounting,
        "manifest_git_revision": observed_sha,
        "manifest_package_version": package_version,
        "expected_source_sha": expected_sha,
        "problems": problems,
        "valid": not problems,
    }
    if problems and strict:
        print("PROVENANCE REFUSED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
    return record


# --------------------------------------------------------------------------


def load_run(run_dir: Path):
    predictions = {r["SubjectEntity"]: r for r in read_jsonl(run_dir / "predictions.jsonl")
                   if r["Relation"] == RELATION}
    telemetry = {r["SubjectEntity"]: r
                 for r in read_jsonl(run_dir / "inference_telemetry.jsonl")
                 if r.get("Relation") == RELATION}
    if not telemetry:
        telemetry = {r["SubjectEntity"]: r for r in read_jsonl(run_dir / "trace.jsonl")
                     if r.get("Relation") == RELATION}
    return predictions, telemetry


def candidate_profile(telemetry: Mapping[str, Mapping[str, Any]],
                      subjects: Sequence[str], evaluator,
                      gold_by: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    counts = [len((telemetry.get(s) or {}).get("candidates") or []) for s in subjects]
    ordered = sorted(counts)
    gold_like_rows = 0
    surfaced: set[str] = set()
    for subject in subjects:
        keys = gold_keys(evaluator, gold_by[subject])
        present = candidate_keys(evaluator, telemetry.get(subject) or {})
        hit = keys & present
        if hit:
            gold_like_rows += 1
        surfaced |= hit
    return {
        "total_candidates": sum(counts),
        "mean_candidates_per_row": round(statistics.mean(counts), 4) if counts else 0.0,
        "median_candidates_per_row": statistics.median(counts) if counts else 0,
        "p90_candidates_per_row": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
        if ordered else 0,
        "max_candidates_per_row": max(counts) if counts else 0,
        "rows_with_zero_candidates": sum(1 for c in counts if c == 0),
        "rows_with_2_or_more": sum(1 for c in counts if c >= 2),
        "rows_with_5_or_more": sum(1 for c in counts if c >= 5),
        "rows_with_10_or_more": sum(1 for c in counts if c >= 10),
        "gold_like_candidate_rows": gold_like_rows,
        "distinct_gold_objects_surfaced": len(surfaced),
    }


def call_profile(telemetry: Mapping[str, Mapping[str, Any]],
                 subjects: Sequence[str]) -> dict[str, Any]:
    calls = [(telemetry.get(s) or {}).get("calls_used", 0) or 0 for s in subjects]
    verification = [(telemetry.get(s) or {}).get("verification_calls", 0) or 0
                    for s in subjects]
    verified_candidates = sum(
        1 for s in subjects
        for c in ((telemetry.get(s) or {}).get("candidates") or [])
        if (c.get("verification_count") or 0) > 0)
    return {
        "mean_calls_per_query": round(statistics.mean(calls), 4) if calls else 0.0,
        "max_calls_per_query": max(calls) if calls else 0,
        "total_verification_calls": sum(verification),
        "rows_reaching_verification": sum(1 for v in verification if v > 0),
        "candidates_ever_verified": verified_candidates,
    }


def final_profile(evaluator, predictions, telemetry, subjects, gold_by) -> dict[str, Any]:
    scored = [score_row(evaluator, (predictions.get(s) or {}).get("ObjectEntities") or [],
                        gold_by[s]) for s in subjects]
    cards = [r["predicted_size"] for r in scored]
    macro_p = statistics.mean(r["precision"] for r in scored)
    macro_r = statistics.mean(r["recall"] for r in scored)
    macro_f1 = statistics.mean(r["f1"] for r in scored)
    return {
        "rows": len(scored),
        "macro_p": round(macro_p, 6),
        "macro_r": round(macro_r, 6),
        "macro_f1": round(macro_f1, 6),
        "tp": sum(r["tp"] for r in scored),
        "fp": sum(r["fp"] for r in scored),
        "fn": sum(r["fn"] for r in scored),
        "exact_rows": sum(1 for r in scored if r["exact"]),
        "partial_rows": sum(1 for r in scored if r["tp"] and not r["exact"]),
        "complete_misses": sum(1 for r in scored if r["tp"] == 0 and r["gold_size"]),
        "empty_predictions": sum(1 for r in scored if r["predicted_size"] == 0),
        "total_predicted_objects": sum(cards),
        "mean_prediction_cardinality": round(statistics.mean(cards), 4) if cards else 0.0,
        "median_prediction_cardinality": statistics.median(cards) if cards else 0,
        "max_prediction_cardinality": max(cards) if cards else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", required=True, type=Path)
    parser.add_argument("--diagnostic-run", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--evaluator", type=Path,
                        default=REPO_ROOT / "benchmark" / "evaluate.py")
    parser.add_argument("--expected-source-sha", default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-provenance-mismatch", action="store_true",
                        help="analyse anyway; the result may never inform a promotion")
    args = parser.parse_args()

    evaluator = load_module(args.evaluator, "official_evaluate")
    provenance = check_provenance(
        args.diagnostic_run, args.expected_source_sha,
        not args.allow_provenance_mismatch)
    if not provenance["valid"] and not args.allow_provenance_mismatch:
        return 2

    gold = [row for row in read_jsonl(args.gold) if row["Relation"] == RELATION]
    gold_by = {row["SubjectEntity"]: row for row in gold}
    subjects = [row["SubjectEntity"] for row in gold]

    base_pred, base_tel = load_run(args.baseline_run)
    diag_pred, diag_tel = load_run(args.diagnostic_run)

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- gold shape, derived rather than declared ----
    sizes = Counter(len(gold_by[s].get("ObjectEntities") or []) for s in subjects)
    gold_shape = {
        "rows": len(subjects),
        "empty_gold_rows": sizes.get(0, 0),
        "non_empty_gold_rows": sum(v for k, v in sizes.items() if k >= 1),
        "single_gold_rows": sizes.get(1, 0),
        "multi_gold_rows": sum(v for k, v in sizes.items() if k >= 2),
        "total_gold_objects": sum(k * v for k, v in sizes.items()),
        "gold_cardinality_distribution": dict(sorted(sizes.items())),
    }

    # ---- per-row ledger ----
    ledger: list[dict[str, Any]] = []
    for subject in subjects:
        gold_row = gold_by[subject]
        before = score_row(evaluator, (base_pred.get(subject) or {}).get(
            "ObjectEntities") or [], gold_row)
        after = score_row(evaluator, (diag_pred.get(subject) or {}).get(
            "ObjectEntities") or [], gold_row)

        keys = gold_keys(evaluator, gold_row)
        base_cands = candidate_keys(evaluator, base_tel.get(subject) or {})
        diag_cands = candidate_keys(evaluator, diag_tel.get(subject) or {})
        base_gold_c, diag_gold_c = base_cands & keys, diag_cands & keys
        base_false, diag_false = base_cands - keys, diag_cands - keys

        gold_lost = len(base_gold_c - diag_gold_c)
        gold_added = len(diag_gold_c - base_gold_c)
        false_removed = len(base_false - diag_false)
        false_added = len(diag_false - base_false)

        ledger.append({
            "SubjectEntity": subject,
            "row_index": (base_tel.get(subject) or {}).get("row_index"),
            "gold_cardinality": before["gold_size"],
            "baseline_prediction": json.dumps(
                (base_pred.get(subject) or {}).get("ObjectEntities") or [],
                ensure_ascii=False),
            "diagnostic_prediction": json.dumps(
                (diag_pred.get(subject) or {}).get("ObjectEntities") or [],
                ensure_ascii=False),
            "gold": json.dumps(gold_row.get("ObjectEntities") or [], ensure_ascii=False),
            "baseline_candidate_count": len(base_cands),
            "diagnostic_candidate_count": len(diag_cands),
            "candidate_delta": len(diag_cands) - len(base_cands),
            "baseline_gold_like_candidates": len(base_gold_c),
            "diagnostic_gold_like_candidates": len(diag_gold_c),
            "gold_like_candidates_removed": gold_lost,
            "gold_like_candidates_added": gold_added,
            "baseline_false_candidates": len(base_false),
            "diagnostic_false_candidates": len(diag_false),
            "false_candidates_removed": false_removed,
            "false_candidates_added": false_added,
            "baseline_emitted": before["predicted_size"],
            "diagnostic_emitted": after["predicted_size"],
            "baseline_tp": before["tp"], "baseline_fp": before["fp"],
            "baseline_fn": before["fn"],
            "diagnostic_tp": after["tp"], "diagnostic_fp": after["fp"],
            "diagnostic_fn": after["fn"],
            "baseline_f1": round(before["f1"], 6),
            "diagnostic_f1": round(after["f1"], 6),
            "delta_f1": round(after["f1"] - before["f1"], 6),
            "transition": classify(before, after, before["gold_size"],
                                   gold_lost, gold_added, false_removed, false_added),
        })

    write_csv(out_dir / "stock_candidate_transition_ledger.csv", ledger)

    def subset(predicate) -> list[dict[str, Any]]:
        return [row for row in ledger if predicate(row)]

    write_csv(out_dir / "improved_rows.csv", subset(lambda r: r["delta_f1"] > 0) or [{}])
    write_csv(out_dir / "harmed_rows.csv", subset(lambda r: r["delta_f1"] < 0) or [{}])
    write_csv(out_dir / "unchanged_rows.csv",
              subset(lambda r: r["delta_f1"] == 0) or [{}])
    write_csv(out_dir / "baseline_correct_regressions.csv", subset(
        lambda r: r["baseline_tp"] == r["gold_cardinality"] and r["baseline_fp"] == 0
        and not (r["diagnostic_tp"] == r["gold_cardinality"] and r["diagnostic_fp"] == 0)
    ) or [{}])
    write_csv(out_dir / "multi_listing_regressions.csv", subset(
        lambda r: r["gold_cardinality"] >= 2 and r["diagnostic_tp"] < r["baseline_tp"]
    ) or [{}])
    write_csv(out_dir / "empty_gold_fixes.csv", subset(
        lambda r: r["gold_cardinality"] == 0 and r["baseline_emitted"] > 0
        and r["diagnostic_emitted"] == 0) or [{}])
    write_csv(out_dir / "destructive_abstention_rows.csv", subset(
        lambda r: r["transition"] == "DESTRUCTIVE_ABSTENTION") or [{}])

    # ---- population breakdowns ----
    def population(rows, label) -> dict[str, Any]:
        return {
            "population": label,
            "rows": len(rows),
            "baseline_tp": sum(r["baseline_tp"] for r in rows),
            "diagnostic_tp": sum(r["diagnostic_tp"] for r in rows),
            "baseline_fp": sum(r["baseline_fp"] for r in rows),
            "diagnostic_fp": sum(r["diagnostic_fp"] for r in rows),
            "baseline_fn": sum(r["baseline_fn"] for r in rows),
            "diagnostic_fn": sum(r["diagnostic_fn"] for r in rows),
            "baseline_empty": sum(1 for r in rows if r["baseline_emitted"] == 0),
            "diagnostic_empty": sum(1 for r in rows if r["diagnostic_emitted"] == 0),
            "rows_improved": sum(1 for r in rows if r["delta_f1"] > 0),
            "rows_harmed": sum(1 for r in rows if r["delta_f1"] < 0),
            "baseline_mean_cardinality": round(
                statistics.mean([r["baseline_emitted"] for r in rows]), 4) if rows else 0,
            "diagnostic_mean_cardinality": round(
                statistics.mean([r["diagnostic_emitted"] for r in rows]), 4) if rows else 0,
        }

    empty_gold = subset(lambda r: r["gold_cardinality"] == 0)
    single_gold = subset(lambda r: r["gold_cardinality"] == 1)
    multi_gold = subset(lambda r: r["gold_cardinality"] >= 2)
    non_empty = subset(lambda r: r["gold_cardinality"] >= 1)
    populations = [population(empty_gold, "empty_gold"),
                   population(single_gold, "single_gold"),
                   population(multi_gold, "multi_gold"),
                   population(non_empty, "non_empty_gold")]
    write_csv(out_dir / "population_breakdown.csv", populations)

    def single_gold_shape(key_emitted: str, key_tp: str, key_fp: str) -> dict[str, int]:
        return {
            "exact_only": sum(1 for r in single_gold
                              if r[key_tp] == 1 and r[key_fp] == 0),
            "gold_plus_extra": sum(1 for r in single_gold
                                   if r[key_tp] == 1 and r[key_fp] > 0),
            "wrong_only": sum(1 for r in single_gold
                              if r[key_tp] == 0 and r[key_emitted] > 0),
            "empty": sum(1 for r in single_gold if r[key_emitted] == 0),
        }

    def multi_gold_shape(key_emitted: str, key_tp: str, key_fp: str) -> dict[str, Any]:
        return {
            "exact_set": sum(1 for r in multi_gold
                             if r[key_tp] == r["gold_cardinality"] and r[key_fp] == 0),
            "collapse_to_singleton": sum(1 for r in multi_gold if r[key_emitted] == 1),
            "under_enumerated": sum(1 for r in multi_gold
                                    if r[key_emitted] < r["gold_cardinality"]),
            "over_enumerated": sum(1 for r in multi_gold
                                   if r[key_emitted] > r["gold_cardinality"]),
            "gold_object_recall": sum(r[key_tp] for r in multi_gold),
            "gold_objects_total": sum(r["gold_cardinality"] for r in multi_gold),
            "mean_cardinality": round(statistics.mean(
                [r[key_emitted] for r in multi_gold]), 4) if multi_gold else 0.0,
        }

    # ---- suppression efficiency ----
    gold_removed = sum(r["gold_like_candidates_removed"] for r in ledger)
    false_removed_total = sum(r["false_candidates_removed"] for r in ledger)
    base = final_profile(evaluator, base_pred, base_tel, subjects, gold_by)
    diag = final_profile(evaluator, diag_pred, diag_tel, subjects, gold_by)
    fp_reduction = base["fp"] - diag["fp"]
    fn_increase = diag["fn"] - base["fn"]

    summary = {
        "schema_version": SCHEMA_VERSION,
        "relation": RELATION,
        "provenance": provenance,
        "gold_shape": gold_shape,
        "baseline_final": base,
        "diagnostic_final": diag,
        "metric_delta": {
            key: round(diag[key] - base[key], 6)
            for key in base if isinstance(base[key], (int, float))
        },
        "baseline_candidates": candidate_profile(base_tel, subjects, evaluator, gold_by),
        "diagnostic_candidates": candidate_profile(diag_tel, subjects, evaluator, gold_by),
        "baseline_calls": call_profile(base_tel, subjects),
        "diagnostic_calls": call_profile(diag_tel, subjects),
        "populations": {p["population"]: p for p in populations},
        "single_gold_shape": {
            "baseline": single_gold_shape("baseline_emitted", "baseline_tp", "baseline_fp"),
            "diagnostic": single_gold_shape(
                "diagnostic_emitted", "diagnostic_tp", "diagnostic_fp"),
        },
        "multi_gold_shape": {
            "baseline": multi_gold_shape("baseline_emitted", "baseline_tp", "baseline_fp"),
            "diagnostic": multi_gold_shape(
                "diagnostic_emitted", "diagnostic_tp", "diagnostic_fp"),
        },
        "suppression_efficiency": {
            "false_candidates_removed": false_removed_total,
            "gold_like_candidates_removed": gold_removed,
            "false_per_gold_removed": round(
                false_removed_total / (gold_removed + EPSILON), 4),
            "fp_reduction": fp_reduction,
            "fn_increase": fn_increase,
            "fp_reduction_per_fn_increase": round(
                fp_reduction / (fn_increase + EPSILON), 4),
            "note": ("offline diagnostic summaries only; never a production "
                     "threshold"),
        },
        "transitions": dict(Counter(r["transition"] for r in ledger)),
        "baseline_correct_regressions": sum(
            1 for r in ledger
            if r["baseline_tp"] == r["gold_cardinality"] and r["baseline_fp"] == 0
            and not (r["diagnostic_tp"] == r["gold_cardinality"]
                     and r["diagnostic_fp"] == 0)),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    (out_dir / "baseline_stock_metrics.json").write_text(
        json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "diagnostic_stock_metrics.json").write_text(
        json.dumps(diag, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "metric_delta.json").write_text(
        json.dumps(summary["metric_delta"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (out_dir / "analysis_provenance.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "baseline_run": str(args.baseline_run),
        "baseline_predictions_sha256": sha256_file(
            args.baseline_run / "predictions.jsonl"),
        "diagnostic_run": str(args.diagnostic_run),
        "diagnostic_predictions_sha256": sha256_file(
            args.diagnostic_run / "predictions.jsonl"),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(args.evaluator),
        "gold_usage": "joined offline after inference, for scoring and labelling only",
        "provenance_field": "manifest.git_revision",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(p for p in out_dir.iterdir()
                       if p.is_file() and p.name != "SHA256SUMS.txt")
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(f"provenance valid   : {provenance['valid']}")
    print(f"macro-F1           : {base['macro_f1']:.5f} -> {diag['macro_f1']:.5f} "
          f"({summary['metric_delta']['macro_f1']:+.5f})")
    print(f"FP / FN            : {base['fp']}/{base['fn']} -> {diag['fp']}/{diag['fn']}")
    print(f"empty predictions  : {base['empty_predictions']} -> {diag['empty_predictions']}")
    print(f"suppression        : {false_removed_total} false vs {gold_removed} gold-like "
          f"candidates removed")
    print(f"multi-gold recall  : "
          f"{summary['multi_gold_shape']['baseline']['gold_object_recall']} -> "
          f"{summary['multi_gold_shape']['diagnostic']['gold_object_recall']} "
          f"of {summary['multi_gold_shape']['baseline']['gold_objects_total']}")
    print(f"baseline-correct regressions: {summary['baseline_correct_regressions']}")
    print("\nNOTE: precision alone is not evidence. This script separates good "
          "suppression\n      from destructive abstention and decides neither.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
