#!/usr/bin/env python3
"""Second-pass weakness mining over the current V3.1 system. CPU only.

Audit 0079. Audit 0075 mined the *pre-repair* V3 predictions. Those results are
now partly stale: audit 0076's finalization fixes changed 86 TRAIN rows, so the
question "what is still wrong" has to be asked again against SAFE_CORE rather
than against the old baseline.

Everything here is replay over persisted inference state. No model is called.
TRAIN gold is read for *scoring and labelling only*; every production rule this
analysis proposes is stated as a gold-independent predicate elsewhere.

Blind TEST is analysed for **structure only**. No TEST row is ever labelled
correct or incorrect, because nothing in this repository knows whether it is.

Usage::

    python scripts/analyze_v3_2_weaknesses.py \
      --train-run outputs/v3_train_collect_v2_coverage/collection/<run> \
      --gold benchmark/data/train.jsonl \
      --test-run outputs/v3_test_16f60fb1_20260810T160048Z/run \
      --output-dir outputs/v3_2_weakness_mining
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cover_kbc.v3_1.config import V31SafeConfig  # noqa: E402

SCHEMA_VERSION = "v3-2-weakness-mining-v1"

NUMERIC_RELATIONS = frozenset({"hasArea", "hasCapacity"})
SET_RELATIONS = frozenset({"awardWonBy", "countryLandBordersCountry",
                           "companyTradesAtStockExchange"})
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"


def _load(path: Path, name: str):
    """Import a sibling script as a module.

    Registered in ``sys.modules`` *before* execution because ``@dataclass``
    resolves its owning module by name while the class body is being processed;
    an unregistered module makes that lookup return ``None``.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


replay = _load(REPO_ROOT / "scripts" / "replay_v3_1_finalization.py", "v3_1_replay")


# --------------------------------------------------------------------------
# small io helpers
# --------------------------------------------------------------------------


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
              fieldnames: Sequence[str] | None = None) -> None:
    fieldnames = list(fieldnames or (rows[0].keys() if rows else ["empty"]))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def head_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover
        return ""


# --------------------------------------------------------------------------
# scoring helpers built on the official evaluator
# --------------------------------------------------------------------------


def gold_alias_sets(evaluator, row: Mapping[str, Any]) -> list[set[str]]:
    """Gold objects as normalised alias sets, the way the evaluator matches."""
    out = []
    for aliases in row.get("ObjectEntities") or []:
        group = aliases if isinstance(aliases, list) else [aliases]
        out.append({evaluator.normalize_string(str(a)) for a in group})
    return out


def numbers_of(evaluator, values: Iterable[Any]) -> list[float]:
    out = []
    for value in values:
        parsed = evaluator.try_parse_number(str(value))
        if parsed is not None:
            out.append(parsed)
    return out


def numeric_hit(value: float, targets: Sequence[float], tol: float = 0.05) -> bool:
    return any(abs(value - t) <= tol * abs(t) if t else value == t for t in targets)


def match_counts(evaluator, relation: str, predicted: Sequence[str],
                 gold_row: Mapping[str, Any]) -> tuple[int, int, int]:
    """(tp, fp, fn) under the evaluator's own matching for this relation type."""
    if evaluator.RELATION_TYPE[relation] == "numeric":
        targets = numbers_of(evaluator, [
            a[0] if isinstance(a, list) and a else a
            for a in gold_row.get("ObjectEntities") or []])
        values = numbers_of(evaluator, predicted)
        tp = sum(1 for v in values if numeric_hit(v, targets))
        return tp, len(predicted) - tp, max(0, len(targets) - tp)
    groups = gold_alias_sets(evaluator, gold_row)
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
    return tp, len(keys) - tp, len(groups) - tp


# --------------------------------------------------------------------------
# failure-mode labelling
# --------------------------------------------------------------------------


_BUCKET_LABEL = re.compile(r"^(?:\d{3,4}s|\d{4}\s*[-–]\s*\d{4}|\d{4})$")
_SCAFFOLD = re.compile(
    r"^(thinking process|analyze the request|retrieve knowledge|constraint \d|"
    r"query:|target:|question:|step \d|knowledge:|entity:)", re.IGNORECASE)


def _gold_like_recalled(evaluator, relation: str, gold_row: Mapping[str, Any],
                        candidates: Sequence[Mapping[str, Any]]) -> bool:
    """Did acquisition surface a candidate the evaluator would count as gold?

    The load-bearing distinction of this whole audit: a miss with a gold-like
    candidate in the graph is a *selection* failure and costs no model call to
    fix; a miss without one is a *recall* failure and costs a better prompt or
    more search. Conflating them sends engineering effort to the wrong place.
    """
    if not candidates:
        return False
    if evaluator.RELATION_TYPE[relation] == "numeric":
        targets = numbers_of(evaluator, [
            a[0] if isinstance(a, list) and a else a
            for a in gold_row.get("ObjectEntities") or []])
        if not targets:
            return False
        return any(
            numeric_hit(c["numeric_value"], targets)
            for c in candidates if c.get("numeric_value") is not None)
    gold_keys = {k for g in gold_alias_sets(evaluator, gold_row) for k in g}
    return any(
        evaluator.normalize_string(str(c.get("output_value") or "")) in gold_keys
        for c in candidates)


def failure_modes(evaluator, relation: str, predicted: Sequence[str],
                  gold_row: Mapping[str, Any], telemetry: Mapping[str, Any],
                  ) -> tuple[list[str], str]:
    """Multi-label failure modes plus the earliest unrecoverable stage."""
    modes: list[str] = []
    stages = telemetry.get("stage_values") or {}
    candidates = telemetry.get("candidates") or []
    gold_groups = gold_alias_sets(evaluator, gold_row)
    numeric = evaluator.RELATION_TYPE[relation] == "numeric"
    tp, fp, fn = match_counts(evaluator, relation, predicted, gold_row)

    if not gold_groups:
        if predicted:
            modes.append("TOO_MANY_FALSE_POSITIVES")
        return modes or ["OTHER"], "L0_CORRECT" if not predicted else "L5_FINALIZATION"

    # Was a gold-like value actually recalled? This distinction decides whether
    # a miss is a recall problem or a selection problem, so it is computed from
    # the candidate set rather than inferred from "there were some candidates".
    recoverable = _gold_like_recalled(evaluator, relation, gold_row, candidates)

    if not predicted:
        modes.append("EMPTY_OUTPUT")
    if fn and tp == 0:
        modes.append("GOOD_CANDIDATE_NOT_SELECTED" if recoverable else "NO_RECALL")
    if tp and fn:
        modes.append("PARTIAL_SET" if relation in SET_RELATIONS else "WRONG_NUMERIC_VALUE")
        if relation in SET_RELATIONS:
            modes.append("MISSING_SET_MEMBERS")
    if fp:
        modes.append("TOO_MANY_FALSE_POSITIVES")
    if relation in SET_RELATIONS and len(predicted) > len(gold_groups):
        modes.append("EXPANDED_TOO_MUCH")
    if relation in SET_RELATIONS and len(predicted) < len(gold_groups):
        modes.append("STOPPED_TOO_EARLY")

    # structured-output leakage
    for value in predicted:
        text = str(value)
        if ":" in text:
            head = text.split(":", 1)[0].strip()
            if _BUCKET_LABEL.match(head) or head.lower() in {
                "individuals", "groups", "organisations", "organizations", "projects"}:
                modes.append("STRUCTURED_OUTPUT_LEAK")
        if _SCAFFOLD.match(text):
            modes.append("PARSER_FAILURE")

    if numeric:
        targets = numbers_of(evaluator, [
            a[0] if isinstance(a, list) and a else a for a in gold_row["ObjectEntities"]])
        values = numbers_of(evaluator, predicted)
        if predicted and not values:
            modes.append("WRONG_ENTITY_TYPE")
        for value in values:
            if numeric_hit(value, targets):
                continue
            if not targets or not targets[0]:
                continue
            ratio = value / targets[0]
            if any(abs(ratio - scale) / scale < 0.25
                   for scale in (10, 100, 1000, 0.1, 0.01, 0.001)):
                modes.append("NUMERIC_SCALE_CONFUSION")
            elif any(abs(ratio - factor) / factor < 0.05
                     for factor in (2.589988110336, 1 / 2.589988110336,
                                    0.0040468564224, 1e6, 1e-6, 100, 0.01)):
                modes.append("UNIT_CONFUSION")
            else:
                modes.append("WRONG_NUMERIC_ATTRIBUTE")
        # a gold-like value present among candidates but not emitted
        if recoverable and not any(numeric_hit(v, targets) for v in values):
            modes.append("GOOD_CANDIDATE_NOT_SELECTED")
    else:
        # entity relations: was a gold-like candidate present but unemitted?
        emitted = {evaluator.normalize_string(str(v)) for v in predicted}
        gold_keys = {key for group in gold_groups for key in group}
        present = {evaluator.normalize_string(str(c.get("output_value") or ""))
                   for c in candidates}
        if (gold_keys & present) - emitted:
            rejected = any(
                c.get("final_status") == "REJECTED"
                and evaluator.normalize_string(str(c.get("output_value") or "")) in gold_keys
                for c in candidates)
            modes.append("VERIFIER_TOO_STRICT" if rejected else "GOOD_CANDIDATE_DROPPED")
        if fp:
            surviving = [c for c in candidates
                         if c.get("final_status") == "ACCEPTED"
                         and evaluator.normalize_string(
                             str(c.get("output_value") or "")) not in gold_keys]
            if surviving:
                modes.append("BAD_CANDIDATE_SURVIVED")

    # earliest unrecoverable stage
    if tp and not fp and not fn:
        stage = "L0_CORRECT"
    elif not candidates:
        stage = "L1_NEVER_RECALLED"
    elif "GOOD_CANDIDATE_NOT_SELECTED" in modes or "GOOD_CANDIDATE_DROPPED" in modes:
        stage = "L5_FINALIZATION"
    elif "VERIFIER_TOO_STRICT" in modes:
        stage = "L4_VERIFICATION"
    elif stages.get("ACQUIRED") and not stages.get("NORMALIZED"):
        stage = "L2_NORMALIZATION"
    else:
        stage = "L1_NEVER_RECALLED"

    return sorted(set(modes)) or ["OTHER"], stage


# --------------------------------------------------------------------------
# micro-domain clustering (discovered, not assumed)
# --------------------------------------------------------------------------


#: Lexical probes over the *subject string only*. These name a kind of entity,
#: never a fact about one, and are used to group errors for reporting - nothing
#: in production reads them.
MICRO_DOMAIN_PROBES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("venue_stadium", "hasCapacity", ("stadium", "arena", "estadio", "stade",
                                      "stadion", "park", "field", "coliseum",
                                      "ground", "bowl")),
    ("venue_hall", "hasCapacity", ("theatre", "theater", "hall", "centre",
                                   "center", "auditorium", "opera", "dome")),
    ("island", "hasArea", ("island", "isla", "isle", "ile", "-shima", "otok")),
    ("lake", "hasArea", ("lake", "lago", "loch", "meer", "see")),
    ("admin_area", "hasArea", ("province", "county", "district", "region",
                               "municipality", "territory", "prefecture")),
    ("private_form", STOCK, ("gmbh", "s.a.", "ltd", "limited", "b.v.", "a/s",
                             "oy", "s.p.a.", "sicav", "plc", "inc", "corp")),
    ("bank", STOCK, ("bank", "banca", "banco")),
    ("association", STOCK, ("verband", "association", "federation", "union")),
)


def micro_domain(relation: str, subject: str) -> list[str]:
    lowered = subject.casefold()
    return [name for name, rel, probes in MICRO_DOMAIN_PROBES
            if rel == relation and any(p in lowered for p in probes)]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_variant(telemetry, safe: V31SafeConfig) -> list[dict[str, Any]]:
    return replay.build_predictions(telemetry, safe)


def relation_metrics(evaluator, predictions, gold) -> dict[str, dict[str, float]]:
    scored = evaluator.evaluate_per_sr_pair(
        list(predictions), list(gold), evaluator.RELATION_TYPE, tolerance=0.05)
    macro = evaluator.macro_average_per_relation(scored)
    micro = evaluator.micro_average_per_relation(scored)
    out: dict[str, dict[str, float]] = {}
    for key in macro:
        out[key] = {**{k: round(v, 6) for k, v in macro[key].items()},
                    **{k: round(v, 6) for k, v in micro.get(key, {}).items()}}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-run", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--test-run", type=Path, default=None)
    parser.add_argument("--evaluator", type=Path,
                        default=REPO_ROOT / "benchmark" / "evaluate.py")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    evaluator = _load(args.evaluator, "official_evaluate")
    telemetry = read_jsonl(args.train_run / "inference_telemetry.jsonl")
    committed = read_jsonl(args.train_run / "predictions.jsonl")
    gold = read_jsonl(args.gold)
    gold_by_id = {(r["SubjectEntity"], r["Relation"]): r for r in gold}
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- variants -------------------------------------------------------
    variants = {
        "V3_BASELINE": V31SafeConfig(),
        "SAFE_CORE": replay.SAFE_CORE,
        "SAFE_FULL": replay.SAFE_FULL,
    }
    predictions = {name: build_variant(telemetry, cfg) for name, cfg in variants.items()}

    fidelity = sum(
        1 for want, got in zip(committed, predictions["V3_BASELINE"])
        if want["ObjectEntities"] != got["ObjectEntities"])
    if fidelity:
        print(f"FIDELITY FAILURE: {fidelity} rows differ with V3.1 off", file=sys.stderr)
        return 1

    metrics = {name: relation_metrics(evaluator, rows, gold)
               for name, rows in predictions.items()}
    relations = sorted({row["Relation"] for row in telemetry})

    # ---- per-row outcome accounting -------------------------------------
    outcome_rows: list[dict[str, Any]] = []
    for index, row in enumerate(telemetry):
        key = (row["SubjectEntity"], row["Relation"])
        gold_row = gold_by_id[key]
        entry: dict[str, Any] = {
            "row_index": row.get("row_index", index),
            "SubjectEntity": key[0],
            "Relation": key[1],
            "gold": json.dumps(gold_row.get("ObjectEntities") or [], ensure_ascii=False),
            "gold_size": len(gold_row.get("ObjectEntities") or []),
        }
        for name in variants:
            values = predictions[name][index]["ObjectEntities"]
            tp, fp, fn = match_counts(evaluator, key[1], values, gold_row)
            precision = tp / len(values) if values else 1.0
            recall = tp / entry["gold_size"] if entry["gold_size"] else 1.0
            f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
            entry.update({
                f"{name}_prediction": json.dumps(values, ensure_ascii=False),
                f"{name}_n": len(values),
                f"{name}_tp": tp, f"{name}_fp": fp, f"{name}_fn": fn,
                f"{name}_f1": round(f1, 6),
                f"{name}_exact": tp == entry["gold_size"] and fp == 0,
            })
        outcome_rows.append(entry)

    # ---- current system summary -----------------------------------------
    def shape(name: str) -> dict[str, Any]:
        rows = [r for r in outcome_rows]
        return {
            "exact_rows": sum(1 for r in rows if r[f"{name}_exact"]),
            "partial_rows": sum(1 for r in rows
                                if r[f"{name}_tp"] and not r[f"{name}_exact"]),
            "complete_miss_rows": sum(1 for r in rows
                                      if r[f"{name}_tp"] == 0 and r["gold_size"]),
            "empty_output_rows": sum(1 for r in rows if r[f"{name}_n"] == 0),
            "false_positives": sum(r[f"{name}_fp"] for r in rows),
            "false_negatives": sum(r[f"{name}_fn"] for r in rows),
            "mean_prediction_cardinality": round(
                sum(r[f"{name}_n"] for r in rows) / len(rows), 4),
        }

    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_head": head_sha(),
        "train_run": str(args.train_run),
        "train_rows": len(telemetry),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(args.evaluator),
        "fidelity_gate": "PASS",
        "variants": {
            name: {
                "enabled_features": list(cfg.enabled_features),
                "metrics": metrics[name],
                "shape": shape(name),
                "per_relation_shape": {
                    relation: {
                        "exact": sum(1 for r in outcome_rows
                                     if r["Relation"] == relation and r[f"{name}_exact"]),
                        "empty": sum(1 for r in outcome_rows
                                     if r["Relation"] == relation and r[f"{name}_n"] == 0),
                        "fp": sum(r[f"{name}_fp"] for r in outcome_rows
                                  if r["Relation"] == relation),
                        "fn": sum(r[f"{name}_fn"] for r in outcome_rows
                                  if r["Relation"] == relation),
                        "rows": sum(1 for r in outcome_rows if r["Relation"] == relation),
                    }
                    for relation in relations
                },
            }
            for name, cfg in variants.items()
        },
    }

    # ---- remaining errors, per variant ----------------------------------
    telemetry_by_id = {(r["SubjectEntity"], r["Relation"]): r for r in telemetry}
    remaining: dict[str, list[dict[str, Any]]] = {}
    for name in ("SAFE_CORE", "SAFE_FULL"):
        rows: list[dict[str, Any]] = []
        for index, entry in enumerate(outcome_rows):
            if entry[f"{name}_exact"]:
                continue
            key = (entry["SubjectEntity"], entry["Relation"])
            values = predictions[name][index]["ObjectEntities"]
            modes, stage = failure_modes(
                evaluator, key[1], values, gold_by_id[key], telemetry_by_id[key])
            rows.append({
                "row_index": entry["row_index"],
                "SubjectEntity": key[0],
                "Relation": key[1],
                "gold": entry["gold"],
                "prediction": entry[f"{name}_prediction"],
                "tp": entry[f"{name}_tp"], "fp": entry[f"{name}_fp"],
                "fn": entry[f"{name}_fn"], "f1": entry[f"{name}_f1"],
                "failure_modes": ";".join(modes),
                "earliest_unrecoverable_stage": stage,
                "micro_domains": ";".join(micro_domain(key[1], key[0])),
                "candidates": len(telemetry_by_id[key].get("candidates") or []),
                "empty_reason": telemetry_by_id[key].get("empty_reason", ""),
                "stopped_reason": telemetry_by_id[key].get("stopped_reason", ""),
            })
        remaining[name] = rows
        write_csv(out_dir / f"{name.lower()}_remaining_errors.csv", rows)

    # ---- differential ----------------------------------------------------
    differential: list[dict[str, Any]] = []
    for entry in outcome_rows:
        base_ok = entry["V3_BASELINE_exact"]
        core_ok = entry["SAFE_CORE_exact"]
        full_ok = entry["SAFE_FULL_exact"]
        base_f1, core_f1, full_f1 = (entry["V3_BASELINE_f1"], entry["SAFE_CORE_f1"],
                                     entry["SAFE_FULL_f1"])
        if core_f1 > base_f1:
            category = "FIXED_BY_SAFE_CORE"
        elif core_f1 < base_f1:
            category = "HARMED_BY_SAFE_CORE"
        elif full_f1 > core_f1:
            category = "FIXED_ONLY_BY_STOCK_DOMINANCE"
        elif full_f1 < core_f1:
            category = "HARMED_BY_STOCK_DOMINANCE"
        elif core_ok:
            category = "UNCHANGED_CORRECT"
        else:
            category = "UNCHANGED_WRONG"
        differential.append({
            "row_index": entry["row_index"],
            "SubjectEntity": entry["SubjectEntity"],
            "Relation": entry["Relation"],
            "category": category,
            "baseline_f1": base_f1, "safe_core_f1": core_f1, "safe_full_f1": full_f1,
            "baseline_prediction": entry["V3_BASELINE_prediction"],
            "safe_core_prediction": entry["SAFE_CORE_prediction"],
            "safe_full_prediction": entry["SAFE_FULL_prediction"],
            "gold": entry["gold"],
            "base_exact": base_ok, "core_exact": core_ok, "full_exact": full_ok,
        })
    write_csv(out_dir / "differential_error_analysis.csv", differential)
    summary["differential"] = dict(Counter(r["category"] for r in differential))

    # ---- relation failure matrix ----------------------------------------
    matrix: list[dict[str, Any]] = []
    mode_counter: dict[tuple[str, str], int] = defaultdict(int)
    for row in remaining["SAFE_CORE"]:
        for mode in row["failure_modes"].split(";"):
            if mode:
                mode_counter[(row["Relation"], mode)] += 1
    for (relation, mode), count in sorted(mode_counter.items(), key=lambda kv: -kv[1]):
        total = sum(1 for r in remaining["SAFE_CORE"] if r["Relation"] == relation)
        matrix.append({
            "relation": relation, "failure_mode": mode, "rows": count,
            "share_of_relation_errors": round(count / total, 4) if total else 0.0,
        })
    write_csv(out_dir / "relation_failure_matrix.csv", matrix)

    # ---- micro-domain clusters ------------------------------------------
    clusters: dict[str, dict[str, Any]] = {}
    for row in remaining["SAFE_CORE"]:
        for domain in (row["micro_domains"].split(";") if row["micro_domains"] else []):
            bucket = clusters.setdefault(domain, {
                "micro_domain": domain, "relation": row["Relation"], "rows": 0,
                "fp": 0, "fn": 0, "modes": Counter(), "examples": []})
            bucket["rows"] += 1
            bucket["fp"] += row["fp"]
            bucket["fn"] += row["fn"]
            bucket["modes"].update(m for m in row["failure_modes"].split(";") if m)
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(row["SubjectEntity"])
    cluster_rows = []
    for bucket in sorted(clusters.values(), key=lambda b: -b["rows"]):
        dominant = bucket["modes"].most_common(3)
        cluster_rows.append({
            "micro_domain": bucket["micro_domain"],
            "relation": bucket["relation"],
            "rows": bucket["rows"], "fp": bucket["fp"], "fn": bucket["fn"],
            "dominant_failure_modes": ";".join(f"{m}({c})" for m, c in dominant),
            "example_subjects": " | ".join(bucket["examples"]),
            "problem_class": _problem_class(dominant),
        })
    write_csv(out_dir / "micro_domain_failure_clusters.csv", cluster_rows)

    # ---- numeric deep dive ----------------------------------------------
    numeric_rows = _numeric_deep_dive(
        evaluator, telemetry, predictions["SAFE_CORE"], gold_by_id)
    write_csv(out_dir / "numeric_failure_deep_dive.csv", numeric_rows)

    # ---- set completeness ------------------------------------------------
    set_rows = _set_completeness(
        evaluator, telemetry, predictions["SAFE_CORE"], gold_by_id)
    write_csv(out_dir / "set_completeness_analysis.csv", set_rows)

    # ---- controller + finalization ---------------------------------------
    controller_rows, finalization_rows = _controller_and_finalization(
        evaluator, telemetry, predictions["SAFE_CORE"], gold_by_id)
    write_csv(out_dir / "controller_failure_analysis.csv", controller_rows)
    write_csv(out_dir / "finalization_failure_analysis.csv", finalization_rows)

    # ---- oracle upper bounds ---------------------------------------------
    oracle_rows = _oracle_bounds(
        evaluator, telemetry, predictions["SAFE_CORE"], gold, gold_by_id)
    write_csv(out_dir / "oracle_upper_bounds.csv", oracle_rows)
    summary["oracle_upper_bounds"] = {r["scenario"]: r["macro_f1"] for r in oracle_rows}

    # ---- blind TEST structure --------------------------------------------
    if args.test_run:
        anomalies = _test_structural(args.test_run, evaluator)
        write_csv(out_dir / "test_blind_structural_anomalies.csv", anomalies)
        summary["test_blind"] = {
            "rows": len({(a["SubjectEntity"], a["Relation"]) for a in anomalies}),
            "anomalies": dict(Counter(a["anomaly"] for a in anomalies)),
            "note": "structure only; no TEST row is labelled correct or incorrect",
        }

    (out_dir / "current_system_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "source_head": head_sha(),
        "train_run": str(args.train_run),
        "train_predictions_sha256": sha256_file(args.train_run / "predictions.jsonl"),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(args.evaluator),
        "test_run": str(args.test_run) if args.test_run else None,
        "gold_usage": "offline scoring and labelling only; no production rule reads gold",
        "test_usage": "structural analysis only; no correctness claim",
        "claude_diagnostic_used": False,
    }
    (out_dir / "analysis_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(p for p in out_dir.iterdir()
                       if p.is_file() and p.name != "SHA256SUMS.txt")
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    for name in variants:
        overall = metrics[name]["*** All Relations ***"]
        print(f"{name:12s} macro-F1 {overall['macro-f1']:.5f}  micro-F1 {overall['micro-f1']:.5f}")
    print(f"SAFE_CORE remaining errors: {len(remaining['SAFE_CORE'])}")
    print(f"differential: {summary['differential']}")
    return 0


def _problem_class(dominant: Sequence[tuple[str, int]]) -> str:
    modes = {m for m, _ in dominant}
    if modes & {"NO_RECALL", "EMPTY_OUTPUT"}:
        return "KNOWLEDGE_RECALL"
    if modes & {"NUMERIC_SCALE_CONFUSION", "UNIT_CONFUSION", "WRONG_NUMERIC_VALUE"}:
        return "NUMERIC_REASONING"
    if modes & {"WRONG_NUMERIC_ATTRIBUTE", "RELATED_ATTRIBUTE_CONFUSION"}:
        return "RELATION_SEMANTICS"
    if modes & {"PARSER_FAILURE", "STRUCTURED_OUTPUT_LEAK"}:
        return "PARSING"
    if modes & {"VERIFIER_TOO_STRICT", "VERIFIER_TOO_WEAK"}:
        return "VERIFICATION"
    if modes & {"GOOD_CANDIDATE_NOT_SELECTED", "GOOD_CANDIDATE_DROPPED",
                "BAD_CANDIDATE_SURVIVED"}:
        return "FINALIZATION"
    if modes & {"STOPPED_TOO_EARLY", "EXPANDED_TOO_MUCH"}:
        return "SET_COMPLETENESS"
    return "CONTROLLER"


def _numeric_deep_dive(evaluator, telemetry, predictions, gold_by_id):
    rows = []
    for row, prediction in zip(telemetry, predictions):
        relation = row["Relation"]
        if relation not in NUMERIC_RELATIONS:
            continue
        key = (row["SubjectEntity"], relation)
        targets = numbers_of(evaluator, [
            a[0] if isinstance(a, list) and a else a
            for a in gold_by_id[key].get("ObjectEntities") or []])
        values = numbers_of(evaluator, prediction["ObjectEntities"])
        recalled = [c["numeric_value"] for c in (row.get("candidates") or [])
                    if c.get("numeric_value") is not None]
        target = targets[0] if targets else None
        ratio = (values[0] / target) if (values and target) else None
        spread = (max(recalled) / min(recalled)
                  if recalled and min(recalled) > 0 else None)
        rows.append({
            "row_index": row.get("row_index"),
            "SubjectEntity": key[0], "Relation": relation,
            "gold_value": target,
            "predicted_value": values[0] if values else None,
            "ratio_to_gold": round(ratio, 6) if ratio else None,
            "within_tolerance": bool(values and target and numeric_hit(values[0], targets)),
            "recalled_candidate_count": len(recalled),
            "distinct_recalled_values": len(set(recalled)),
            "recalled_min": min(recalled) if recalled else None,
            "recalled_max": max(recalled) if recalled else None,
            "recalled_spread_ratio": round(spread, 4) if spread else None,
            "gold_like_candidate_present": bool(
                target and any(numeric_hit(v, targets) for v in recalled)),
            "gold_like_present_but_not_emitted": bool(
                target and any(numeric_hit(v, targets) for v in recalled)
                and not (values and numeric_hit(values[0], targets))),
            "scale_bucket": _scale_bucket(ratio),
            "empty_output": not prediction["ObjectEntities"],
        })
    return rows


def _scale_bucket(ratio):
    if ratio is None or ratio <= 0:
        return ""
    for name, scale in (("~1000x", 1000), ("~100x", 100), ("~10x", 10),
                        ("~1x", 1), ("~0.1x", 0.1), ("~0.01x", 0.01),
                        ("~0.001x", 0.001)):
        if abs(math.log10(ratio) - math.log10(scale)) < 0.25:
            return name
    return "other"


def _set_completeness(evaluator, telemetry, predictions, gold_by_id):
    rows = []
    for row, prediction in zip(telemetry, predictions):
        relation = row["Relation"]
        if relation not in SET_RELATIONS:
            continue
        key = (row["SubjectEntity"], relation)
        gold_row = gold_by_id[key]
        groups = gold_alias_sets(evaluator, gold_row)
        values = prediction["ObjectEntities"]
        tp, fp, fn = match_counts(evaluator, relation, values, gold_row)
        accepted = [c for c in (row.get("candidates") or [])
                    if c.get("final_status") == "ACCEPTED"]
        gold_keys = {k for g in groups for k in g}
        recoverable = sum(
            1 for c in (row.get("candidates") or [])
            if evaluator.normalize_string(str(c.get("output_value") or "")) in gold_keys
            and evaluator.normalize_string(str(c.get("output_value") or ""))
            not in {evaluator.normalize_string(str(v)) for v in values})
        rows.append({
            "row_index": row.get("row_index"),
            "SubjectEntity": key[0], "Relation": relation,
            "gold_size": len(groups), "predicted_size": len(values),
            "tp": tp, "fp": fp, "fn": fn,
            "cardinality_delta": len(values) - len(groups),
            "accepted_candidates": len(accepted),
            "total_candidates": len(row.get("candidates") or []),
            "missing_but_recallable": recoverable,
            "over_enumerated": len(values) > len(groups),
            "under_enumerated": len(values) < len(groups),
        })
    return rows


def _controller_and_finalization(evaluator, telemetry, predictions, gold_by_id):
    controller, finalization = [], []
    for row, prediction in zip(telemetry, predictions):
        key = (row["SubjectEntity"], row["Relation"])
        gold_row = gold_by_id[key]
        values = prediction["ObjectEntities"]
        tp, fp, fn = match_counts(evaluator, key[1], values, gold_row)
        if tp and not fp and not fn:
            continue
        candidates = row.get("candidates") or []
        accepted = [c for c in candidates if c.get("final_status") == "ACCEPTED"]
        unresolved = [c for c in candidates if c.get("final_status") == "UNRESOLVED"]
        rejected = [c for c in candidates if c.get("final_status") == "REJECTED"]
        controller.append({
            "row_index": row.get("row_index"),
            "SubjectEntity": key[0], "Relation": key[1],
            "stopped_reason": row.get("stopped_reason", ""),
            "calls_used": row.get("calls_used"),
            "verification_calls": row.get("verification_calls"),
            "actions_executed": len(row.get("actions") or []),
            "candidates": len(candidates),
            "accepted": len(accepted), "unresolved": len(unresolved),
            "rejected": len(rejected),
            "failure_state": row.get("failure_state", ""),
            "stopped_with_zero_candidates": not candidates,
            "stopped_with_unresolved": bool(unresolved) and not values,
        })
        gold_keys = {k for g in gold_alias_sets(evaluator, gold_row) for k in g}
        emitted = {evaluator.normalize_string(str(v)) for v in values}
        recoverable = [
            c for c in candidates
            if evaluator.normalize_string(str(c.get("output_value") or "")) in gold_keys
            and evaluator.normalize_string(str(c.get("output_value") or "")) not in emitted
        ]
        if recoverable or (fp and accepted):
            finalization.append({
                "row_index": row.get("row_index"),
                "SubjectEntity": key[0], "Relation": key[1],
                "gold": json.dumps(gold_row.get("ObjectEntities") or [], ensure_ascii=False),
                "prediction": json.dumps(values, ensure_ascii=False),
                "gold_like_candidates_not_emitted": len(recoverable),
                "statuses_of_those": ";".join(sorted(
                    {str(c.get("final_status")) for c in recoverable})),
                "false_positives": fp,
                "accepted_candidates": len(accepted),
            })
    return controller, finalization


def _oracle_bounds(evaluator, telemetry, predictions, gold, gold_by_id):
    """TRAIN ORACLE UPPER BOUND ONLY. Never wired into production."""
    def score(rows):
        scored = evaluator.evaluate_per_sr_pair(
            list(rows), list(gold), evaluator.RELATION_TYPE, tolerance=0.05)
        macro = evaluator.macro_average_per_relation(scored)
        return round(macro["*** All Relations ***"]["macro-f1"], 6)

    base = [dict(p) for p in predictions]
    scenarios = {"SAFE_CORE (actual)": base}

    # perfect selection among recalled candidates
    perfect_selection = []
    emit_all_gold_like = []
    no_false_positives = []
    for row, prediction in zip(telemetry, predictions):
        key = (row["SubjectEntity"], row["Relation"])
        gold_row = gold_by_id[key]
        numeric = evaluator.RELATION_TYPE[key[1]] == "numeric"
        candidates = row.get("candidates") or []
        values = prediction["ObjectEntities"]
        if numeric:
            targets = numbers_of(evaluator, [
                a[0] if isinstance(a, list) and a else a
                for a in gold_row.get("ObjectEntities") or []])
            recalled = [c["numeric_value"] for c in candidates
                        if c.get("numeric_value") is not None]
            good = [v for v in recalled if targets and numeric_hit(v, targets)]
            chosen = [str(good[0])] if good else values
            perfect_selection.append({**prediction, "ObjectEntities": chosen})
            emit_all_gold_like.append({**prediction, "ObjectEntities": chosen})
            keep = [v for v in values
                    if targets and numeric_hit(
                        evaluator.try_parse_number(str(v)) or math.nan, targets)]
            no_false_positives.append({**prediction, "ObjectEntities": keep})
        else:
            gold_keys = {k for g in gold_alias_sets(evaluator, gold_row) for k in g}
            present = [c.get("output_value") for c in candidates
                       if evaluator.normalize_string(str(c.get("output_value") or ""))
                       in gold_keys]
            perfect_selection.append({**prediction, "ObjectEntities": list(dict.fromkeys(
                [v for v in values
                 if evaluator.normalize_string(str(v)) in gold_keys] + present))})
            emit_all_gold_like.append({**prediction, "ObjectEntities": list(
                dict.fromkeys(present))})
            no_false_positives.append({**prediction, "ObjectEntities": [
                v for v in values if evaluator.normalize_string(str(v)) in gold_keys]})

    scenarios["ORACLE: perfect selection among recalled"] = perfect_selection
    scenarios["ORACLE: emit every gold-like candidate present"] = emit_all_gold_like
    scenarios["ORACLE: remove all false positives, add no recall"] = no_false_positives
    scenarios["ORACLE: perfect recall + perfect finalization"] = [
        {**p, "ObjectEntities": [
            a[0] if isinstance(a, list) and a else a
            for a in gold_by_id[(p["SubjectEntity"], p["Relation"])].get(
                "ObjectEntities") or []]}
        for p in predictions]

    baseline = score(base)
    rows = []
    for name, dataset in scenarios.items():
        value = score(dataset)
        rows.append({
            "scenario": name,
            "macro_f1": value,
            "delta_vs_safe_core": round(value - baseline, 6),
            "label": "TRAIN ORACLE UPPER BOUND ONLY" if name.startswith("ORACLE") else "actual",
        })
    return rows


def _test_structural(test_run: Path, evaluator):
    """Structure-only anomalies on blind TEST. No correctness is asserted."""
    predictions = read_jsonl(test_run / "predictions.jsonl")
    trace = {(r["SubjectEntity"], r["Relation"]): r
             for r in read_jsonl(test_run / "trace.jsonl")}
    anomalies: list[dict[str, Any]] = []

    def flag(row, name, detail=""):
        anomalies.append({
            "SubjectEntity": row["SubjectEntity"], "Relation": row["Relation"],
            "anomaly": name, "detail": detail,
            "prediction_size": len(row.get("ObjectEntities") or []),
        })

    sizes: dict[str, list[int]] = defaultdict(list)
    for row in predictions:
        sizes[row["Relation"]].append(len(row.get("ObjectEntities") or []))

    for row in predictions:
        relation = row["Relation"]
        values = row.get("ObjectEntities") or []
        record = trace.get((row["SubjectEntity"], relation), {})
        if not values:
            reason = record.get("empty_reason", "")
            flag(row, "EMPTY_OUTPUT", reason)
            if reason == "pipeline_error":
                flag(row, "ORCHESTRATION_FAILURE_HOLE", reason)
        keys = [evaluator.normalize_string(str(v)) for v in values]
        if len(keys) != len(set(keys)):
            flag(row, "DUPLICATE_ALIAS_AFTER_NORMALISATION")
        for value in values:
            text = str(value)
            if ":" in text:
                flag(row, "SUSPICIOUS_ENUMERATION_LABEL", text[:60])
            if _SCAFFOLD.match(text):
                flag(row, "PARSER_SCAFFOLD_LEAK", text[:60])
            if evaluator.normalize_string(text) == evaluator.normalize_string(
                    row["SubjectEntity"]):
                flag(row, "SUBJECT_EMITTED_AS_OBJECT", text[:60])
        if relation in NUMERIC_RELATIONS:
            parsed = [evaluator.try_parse_number(str(v)) for v in values]
            if values and any(p is None for p in parsed):
                flag(row, "MALFORMED_NUMERAL", json.dumps(values, ensure_ascii=False))
            if len([p for p in parsed if p is not None]) > 1:
                flag(row, "MULTIPLE_NUMERIC_VALUES")
        candidates = record.get("candidates") or []
        if relation in NUMERIC_RELATIONS and candidates:
            recalled = [c.get("numeric_value") for c in candidates
                        if c.get("numeric_value")]
            if recalled and min(recalled) > 0:
                spread = max(recalled) / min(recalled)
                if spread >= 9.0:
                    flag(row, "WIDE_NUMERIC_CANDIDATE_SPREAD", f"{spread:.1f}x")
        accepted_lost = [
            c for c in candidates
            if c.get("final_status") == "ACCEPTED" and not c.get("emitted")]
        if accepted_lost:
            flag(row, "ACCEPTED_CANDIDATE_NOT_EMITTED", str(len(accepted_lost)))
        if sizes[relation]:
            ordered = sorted(sizes[relation])
            p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
            if len(values) > max(p95, 1) * 2 and len(values) > 5:
                flag(row, "EXTREME_CARDINALITY", f"{len(values)} vs p95 {p95}")
    return anomalies


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
