#!/usr/bin/env python3
"""Analyse the hasCapacity real-weight diagnostic against its baseline. CPU only.

Audit 0080, phase B. Run this **after** the GPU artifact returns.

The scientific question is not "did F1 go up". It is whether a
definition-aware prompt surfaces the *right capacity attribute* more often. A
recall intervention can be valuable while final F1 barely moves, if it puts
gold-like candidates into rows that had none - that outcome argues for the
Discriminative Verification Budget rather than against the prompt. The reverse
is also true: more numbers with no more gold-like values is a rejection.

So this script reports candidate recall and final prediction quality
separately, and refuses to collapse them into one number.

Provenance is checked before anything is measured: a run whose source SHA,
config, row count, relation or error count does not match the declared contract
is not analysed at all.

TRAIN gold is read for offline scoring and labelling only. Nothing here is
importable from production - a test asserts it.

Usage::

    python scripts/analyze_capacity_diagnostic.py \
      --baseline-run outputs/v3_train_collect_v2_coverage/collection/<run> \
      --diagnostic-run outputs/<capacity run> \
      --gold benchmark/data/train.jsonl \
      --expected-source-sha <sha> \
      --output-dir outputs/v3_2_capacity_diagnostic_analysis
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA_VERSION = "v3-2-capacity-diagnostic-v1"
RELATION = "hasCapacity"
EXPECTED_ROWS = 100

#: Ratio buckets, as audit 0080 section 12 defines them.
RATIO_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("OVER_200X", 200.0, float("inf")),
    ("20X_TO_200X", 20.0, 200.0),
    ("5X_TO_20X", 5.0, 20.0),
    ("2X_TO_5X", 2.0, 5.0),
    ("BETWEEN_1_05_AND_2X", 1.05, 2.0),
    ("WITHIN_5_PERCENT", 0.95, 1.05),
    ("TOO_SMALL_1_05_TO_2X", 0.5, 0.95),
    ("TOO_SMALL_2X_TO_5X", 0.2, 0.5),
    ("TOO_SMALL_5X_TO_20X", 0.05, 0.2),
    ("TOO_SMALL_20X_TO_200X", 0.005, 0.05),
    ("TOO_SMALL_OVER_200X", 0.0, 0.005),
)

#: Semantic roles a capacity candidate's *own* qualifier text may indicate.
#: Matched against inference-time metadata only (facet ids, view ids, surface
#: text). Never invented: an unmatched candidate stays UNKNOWN.
ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ATTENDANCE", r"attendance|record crowd|crowd record|highest crowd"),
    ("AREA", r"\barea\b|square (?:met|kilomet|feet|yard)|\bm2\b|\bsq\b"),
    ("COST", r"\bcost\b|budget|construction cost|\bUSD\b|\bEUR\b|million (?:dollar|euro)"),
    ("YEAR", r"\b(?:built|opened|founded|inaugurat|renovated) in\b|\byear\b"),
    ("DIMENSION", r"\bheight\b|\bwidth\b|\blength\b|\bpitch\b|dimension"),
    ("SEATED_CAPACITY", r"seated|seating(?! capacity of the)|all[- ]seater|fixed seat"),
    ("STANDING_CAPACITY", r"standing|terrace|safe standing|rail seat"),
    ("SPORT_CAPACITY", r"\bmatch\b|football|soccer|sport|league|stadium configuration"),
    ("CONCERT_CAPACITY", r"concert|general admission|\bgig\b|music event"),
    ("HISTORICAL_CAPACITY", r"historical|originally|former|before .*renovation|used to"),
    ("POST_RENOVATION_CAPACITY", r"after .*renovation|post[- ]renovation|refurbish|current capacity"),
    ("TEMPORARY_CAPACITY", r"temporary|expanded for|one[- ]off|event[- ]specific"),
    ("CANONICAL_CAPACITY", r"maximum (?:spectator )?capacity|total capacity|capacity of"),
)


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


def numbers_of(evaluator, values: Iterable[Any]) -> list[float]:
    out = []
    for value in values:
        parsed = evaluator.try_parse_number(str(value))
        if parsed is not None:
            out.append(parsed)
    return out


def gold_targets(evaluator, row: Mapping[str, Any]) -> list[float]:
    return numbers_of(evaluator, [
        alias[0] if isinstance(alias, list) and alias else alias
        for alias in row.get("ObjectEntities") or []])


def within_tolerance(value: float, targets: Sequence[float], tol: float = 0.05) -> bool:
    return any(abs(value - t) <= tol * abs(t) if t else value == t for t in targets)


def ratio_bucket(ratio: float | None) -> str:
    if ratio is None or ratio <= 0:
        return ""
    for name, low, high in RATIO_BUCKETS:
        if low <= ratio < high:
            return name
    return "OTHER"


def semantic_role(candidate: Mapping[str, Any], views: Mapping[str, str]) -> str:
    """Classify a candidate's capacity variant from its own metadata.

    Deliberately conservative: the text searched is the candidate's surface
    forms, facet ids and the view ids that produced it - all inference-time
    state. An unmatched candidate is ``UNKNOWN`` rather than guessed at.
    """
    haystack = " ".join([
        " ".join(candidate.get("surface_forms") or []),
        " ".join(candidate.get("facet_ids") or []),
        " ".join(views.get(rid, "") for rid in candidate.get("record_ids") or []),
    ]).casefold()
    if not haystack.strip():
        return "UNKNOWN"
    for role, pattern in ROLE_PATTERNS:
        if re.search(pattern, haystack):
            return role
    return "OTHER_NUMERIC" if candidate.get("numeric_value") is not None else "UNKNOWN"


def row_state(evaluator, prediction: Mapping[str, Any], telemetry: Mapping[str, Any],
              gold_row: Mapping[str, Any]) -> dict[str, Any]:
    targets = gold_targets(evaluator, gold_row)
    values = numbers_of(evaluator, prediction.get("ObjectEntities") or [])
    candidates = telemetry.get("candidates") or []
    recalled = [c.get("numeric_value") for c in candidates
                if c.get("numeric_value") is not None]
    gold_like = [v for v in recalled if targets and within_tolerance(v, targets)]
    emitted_ok = bool(values and targets and within_tolerance(values[0], targets))
    ratio = (values[0] / targets[0]) if (values and targets and targets[0]) else None
    return {
        "predicted": values[0] if values else None,
        "gold": targets[0] if targets else None,
        "empty": not (prediction.get("ObjectEntities") or []),
        "correct": emitted_ok,
        "gold_like_recalled": bool(gold_like),
        "gold_like_not_emitted": bool(gold_like) and not emitted_ok,
        "candidate_count": len(candidates),
        "numeric_candidate_count": len(recalled),
        "distinct_numeric_values": len(set(recalled)),
        "ratio": ratio,
        "ratio_bucket": ratio_bucket(ratio),
    }


def classify_transition(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """Audit 0080 section 16 transition label for one row."""
    if before["correct"] and after["correct"]:
        return "CORRECT -> CORRECT"
    if before["correct"] and not after["correct"]:
        return "CORRECT -> WRONG"
    if not before["correct"] and after["correct"]:
        if before["empty"]:
            return "EMPTY -> CORRECT"
        if not before["gold_like_recalled"]:
            return "NO_RECALL -> GOLD_LIKE_RECALLED_AND_EMITTED"
        return "WRONG_ATTRIBUTE -> CORRECT_ATTRIBUTE"
    # both wrong
    if not before["gold_like_recalled"] and after["gold_like_recalled"]:
        return "NO_RECALL -> GOLD_LIKE_RECALLED_NOT_EMITTED"
    if not before["gold_like_recalled"] and not after["gold_like_recalled"]:
        if before["empty"] and not after["empty"]:
            return "EMPTY -> WRONG_NONEMPTY"
        return "NO_RECALL -> STILL_NO_RECALL"
    if before["predicted"] != after["predicted"]:
        return "WRONG_ATTRIBUTE -> DIFFERENT_WRONG_ATTRIBUTE"
    return "UNCHANGED_WRONG"


def official(evaluator, predictions, gold_subset) -> dict[str, float]:
    scored = evaluator.evaluate_per_sr_pair(
        list(predictions), list(gold_subset), evaluator.RELATION_TYPE, tolerance=0.05)
    rows = [r for r in scored if r["Relation"] == RELATION]
    if not rows:
        return {"macro-p": 0.0, "macro-r": 0.0, "macro-f1": 0.0, "rows": 0}
    return {
        "macro-p": round(sum(r["p"] for r in rows) / len(rows), 6),
        "macro-r": round(sum(r["r"] for r in rows) / len(rows), 6),
        "macro-f1": round(sum(r["f1"] for r in rows) / len(rows), 6),
        "tp": sum(r["tp"] for r in rows),
        "total_pred": sum(r["total_pred"] for r in rows),
        "total_gt": sum(r["total_gt"] for r in rows),
        "rows": len(rows),
    }


# --------------------------------------------------------------------------


def check_provenance(run_dir: Path, expected_sha: str | None,
                     strict: bool) -> dict[str, Any]:
    """Refuse to analyse a run whose contract does not match."""
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
                problems.append(f"run_accounting.{field}={accounting.get(field)}, expected {want}")
    else:
        problems.append("run_accounting.json missing (audit 0078 gate not recorded)")

    # Source provenance is `git_revision`, never `cover_kbc_version`.
    # `RunManifest` carries both and they answer different questions: the former
    # is the commit the run executed, the latter is the package version string
    # (`0.1.0`) and is identical across every commit this project has ever made.
    # Comparing the package version to a SHA is a check that can never pass and
    # therefore silently refuses every valid run - which is exactly what it did
    # to the first analysis of the audit-0080 capacity artifact.
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
                        help="analyse anyway and record the mismatch (never for a decision)")
    args = parser.parse_args()

    evaluator = load_module(args.evaluator, "official_evaluate")
    provenance = check_provenance(
        args.diagnostic_run, args.expected_source_sha, not args.allow_provenance_mismatch)
    if not provenance["valid"] and not args.allow_provenance_mismatch:
        return 2

    gold = read_jsonl(args.gold)
    gold_subset = [row for row in gold if row["Relation"] == RELATION]
    gold_by = {row["SubjectEntity"]: row for row in gold_subset}

    def load_run(run_dir: Path):
        predictions = {r["SubjectEntity"]: r for r in read_jsonl(run_dir / "predictions.jsonl")
                       if r["Relation"] == RELATION}
        telemetry_path = run_dir / "inference_telemetry.jsonl"
        telemetry = {r["SubjectEntity"]: r for r in read_jsonl(telemetry_path)
                     if r.get("Relation") == RELATION}
        if not telemetry:
            trace = {r["SubjectEntity"]: r for r in read_jsonl(run_dir / "trace.jsonl")
                     if r.get("Relation") == RELATION}
            telemetry = trace
        views: dict[str, str] = {}
        for call in read_jsonl(run_dir / "calls.jsonl"):
            query = call.get("query") or {}
            if query.get("relation") == RELATION:
                views[call.get("record_id", "")] = " ".join(filter(None, [
                    str(call.get("view_id") or ""), str(call.get("facet_id") or ""),
                    str(call.get("raw_output") or "")[:400]]))
        return predictions, telemetry, views

    base_pred, base_tel, base_views = load_run(args.baseline_run)
    diag_pred, diag_tel, diag_views = load_run(args.diagnostic_run)

    subjects = [row["SubjectEntity"] for row in gold_subset]
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    per_row: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    candidate_rows: list[dict[str, Any]] = []
    role_rows: list[dict[str, Any]] = []

    for subject in subjects:
        gold_row = gold_by[subject]
        before = row_state(evaluator, base_pred.get(subject, {}),
                           base_tel.get(subject, {}), gold_row)
        after = row_state(evaluator, diag_pred.get(subject, {}),
                          diag_tel.get(subject, {}), gold_row)
        label = classify_transition(before, after)
        transitions[label] += 1
        per_row.append({
            "SubjectEntity": subject,
            "gold": before["gold"],
            "baseline_prediction": before["predicted"],
            "diagnostic_prediction": after["predicted"],
            "baseline_correct": before["correct"], "diagnostic_correct": after["correct"],
            "baseline_empty": before["empty"], "diagnostic_empty": after["empty"],
            "baseline_gold_like_recalled": before["gold_like_recalled"],
            "diagnostic_gold_like_recalled": after["gold_like_recalled"],
            "diagnostic_gold_like_not_emitted": after["gold_like_not_emitted"],
            "baseline_candidates": before["candidate_count"],
            "diagnostic_candidates": after["candidate_count"],
            "baseline_distinct_values": before["distinct_numeric_values"],
            "diagnostic_distinct_values": after["distinct_numeric_values"],
            "baseline_ratio_bucket": before["ratio_bucket"],
            "diagnostic_ratio_bucket": after["ratio_bucket"],
            "transition": label,
        })

        for run_label, telemetry, views in (("baseline", base_tel, base_views),
                                            ("diagnostic", diag_tel, diag_views)):
            record = telemetry.get(subject, {})
            targets = gold_targets(evaluator, gold_row)
            emitted = {str(v) for v in
                       (base_pred if run_label == "baseline" else diag_pred)
                       .get(subject, {}).get("ObjectEntities") or []}
            for candidate in record.get("candidates") or []:
                value = candidate.get("numeric_value")
                ratio = (value / targets[0]) if (value and targets and targets[0]) else None
                role = semantic_role(candidate, views)
                candidate_rows.append({
                    "run": run_label, "SubjectEntity": subject,
                    "raw_text": ";".join(candidate.get("surface_forms") or []),
                    "parsed_numeric_value": value,
                    "unit": candidate.get("unit") or candidate.get("source_unit") or "",
                    "semantic_role": role,
                    "source_views": ";".join(candidate.get("facet_ids") or []),
                    "acquisition_groups": ";".join(candidate.get("acquisition_groups") or []),
                    "independent_support": candidate.get("independent_support"),
                    "score": candidate.get("score"),
                    "verifier_label": candidate.get("verifier_label") or "",
                    "verifier_valid_prob": candidate.get("verifier_valid_prob"),
                    "hypothesis_status": candidate.get("final_status"),
                    "emitted": str(candidate.get("output_value")) in emitted,
                    "gold_value": targets[0] if targets else None,
                    "gold_like": bool(value is not None and targets
                                      and within_tolerance(value, targets)),
                    "ratio_to_gold": round(ratio, 6) if ratio else None,
                    "ratio_bucket": ratio_bucket(ratio),
                })
                role_rows.append({"run": run_label, "SubjectEntity": subject,
                                  "semantic_role": role,
                                  "gold_like": bool(value is not None and targets
                                                    and within_tolerance(value, targets))})

    write_csv(out_dir / "capacity_per_row.csv", per_row)
    write_csv(out_dir / "candidate_instrumentation.csv", candidate_rows)
    write_csv(out_dir / "capacity_transition_matrix.csv",
              [{"transition": k, "rows": v} for k, v in transitions.most_common()])

    # attribute-variant analysis
    variant: list[dict[str, Any]] = []
    for run_label in ("baseline", "diagnostic"):
        subset = [r for r in role_rows if r["run"] == run_label]
        counts = Counter(r["semantic_role"] for r in subset)
        gold_like = Counter(r["semantic_role"] for r in subset if r["gold_like"])
        for role, count in counts.most_common():
            variant.append({"run": run_label, "semantic_role": role,
                            "candidates": count, "gold_like_candidates": gold_like.get(role, 0)})
    write_csv(out_dir / "attribute_variant_analysis.csv", variant)

    # opportunity tables
    verification_rows = [
        r for r in per_row
        if r["diagnostic_distinct_values"] >= 2 and r["diagnostic_gold_like_recalled"]
    ]
    write_csv(out_dir / "verification_budget_opportunity.csv", verification_rows or [{}])
    resolver_rows = [r for r in per_row if r["diagnostic_gold_like_not_emitted"]]
    write_csv(out_dir / "numeric_resolver_opportunity.csv", resolver_rows or [{}])
    regressions = [r for r in per_row if r["baseline_correct"] and not r["diagnostic_correct"]]
    write_csv(out_dir / "baseline_correct_regressions.csv", regressions or [{}])

    base_metrics = official(evaluator, list(base_pred.values()), gold_subset)
    diag_metrics = official(evaluator, list(diag_pred.values()), gold_subset)
    (out_dir / "baseline_capacity_metrics.json").write_text(
        json.dumps(base_metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "diagnostic_capacity_metrics.json").write_text(
        json.dumps(diag_metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta = {k: round(diag_metrics.get(k, 0) - base_metrics.get(k, 0), 6)
             for k in base_metrics if isinstance(base_metrics[k], (int, float))}
    (out_dir / "metric_delta.json").write_text(
        json.dumps(delta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    base_gold_like = sum(1 for r in per_row if r["baseline_gold_like_recalled"])
    diag_gold_like = sum(1 for r in per_row if r["diagnostic_gold_like_recalled"])
    base_cands = sum(r["baseline_candidates"] for r in per_row)
    diag_cands = sum(r["diagnostic_candidates"] for r in per_row)

    def bucket_counts(field: str) -> dict[str, int]:
        return dict(Counter(r[field] for r in per_row if r[field]))

    summary = {
        "schema_version": SCHEMA_VERSION,
        "relation": RELATION,
        "provenance": provenance,
        "baseline_metrics": base_metrics,
        "diagnostic_metrics": diag_metrics,
        "metric_delta": delta,
        "candidate_recall": {
            "baseline_gold_like_rows": base_gold_like,
            "diagnostic_gold_like_rows": diag_gold_like,
            "delta": diag_gold_like - base_gold_like,
            "baseline_total_candidates": base_cands,
            "diagnostic_total_candidates": diag_cands,
            "baseline_mean_candidates_per_row": round(base_cands / len(per_row), 4),
            "diagnostic_mean_candidates_per_row": round(diag_cands / len(per_row), 4),
        },
        "final_prediction": {
            "baseline_correct_rows": sum(1 for r in per_row if r["baseline_correct"]),
            "diagnostic_correct_rows": sum(1 for r in per_row if r["diagnostic_correct"]),
            "baseline_empty_rows": sum(1 for r in per_row if r["baseline_empty"]),
            "diagnostic_empty_rows": sum(1 for r in per_row if r["diagnostic_empty"]),
        },
        "ratio_buckets": {
            "baseline": bucket_counts("baseline_ratio_bucket"),
            "diagnostic": bucket_counts("diagnostic_ratio_bucket"),
        },
        "transitions": dict(transitions),
        "opportunities": {
            "verification_budget_rows": len(verification_rows),
            "numeric_resolver_rows": len(resolver_rows),
        },
        "baseline_correct_regressions": len(regressions),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    (out_dir / "analysis_provenance.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "baseline_run": str(args.baseline_run),
        "baseline_predictions_sha256": sha256_file(args.baseline_run / "predictions.jsonl"),
        "diagnostic_run": str(args.diagnostic_run),
        "diagnostic_predictions_sha256": sha256_file(args.diagnostic_run / "predictions.jsonl"),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(args.evaluator),
        "gold_usage": "offline scoring and labelling only",
        "claude_diagnostic_used": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(p for p in out_dir.iterdir()
                       if p.is_file() and p.name != "SHA256SUMS.txt")
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(f"provenance valid        : {provenance['valid']}")
    print(f"macro-F1                : {base_metrics['macro-f1']:.5f} -> "
          f"{diag_metrics['macro-f1']:.5f} ({delta.get('macro-f1', 0):+.5f})")
    print(f"gold-like candidate rows: {base_gold_like} -> {diag_gold_like} "
          f"({diag_gold_like - base_gold_like:+d})")
    print(f"mean candidates per row : "
          f"{summary['candidate_recall']['baseline_mean_candidates_per_row']} -> "
          f"{summary['candidate_recall']['diagnostic_mean_candidates_per_row']}")
    print(f"verification opportunity: {len(verification_rows)} rows")
    print(f"resolver opportunity    : {len(resolver_rows)} rows")
    print(f"baseline-correct regress: {len(regressions)} rows")
    print("\nNOTE: promotion is a judgement over candidate recall AND final quality.")
    print("      This script reports both and decides neither.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
