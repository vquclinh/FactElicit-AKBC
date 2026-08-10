#!/usr/bin/env python3
"""Offline TRAIN replay of the V3.1 Class A finalization changes.

Replays Module 8 over the persisted inference state of the full 477-row TRAIN
run - no model calls, no GPU, no new evidence. The persisted
``inference_telemetry.jsonl`` carries, per candidate, exactly the state Module 8
reads: acceptance status, numeric value, acquisition groups, verifier verdict
and score. Reconstructing selection from it is faithful, and the harness proves
that rather than asserting it: with every V3.1 feature off, the replay must
reproduce the committed ``predictions.jsonl`` exactly, and the script fails if
it does not.

Gold is used only to *score* the replay. No production predicate implemented in
``cover_kbc.v3_1`` reads gold, TRAIN labels, row indices or subjects; the
ablation below scores rules, it does not fit them.

Usage::

    python scripts/replay_v3_1_finalization.py \
      --run-dir outputs/v3_train_collect_v2_coverage/collection/<run> \
      --gold benchmark/data/train.jsonl \
      --output-dir outputs/v3_1_score_recovery
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cover_kbc.contracts.registry import CONTRACTS  # noqa: E402
from cover_kbc.normalization.numeric import (  # noqa: E402
    NumericCluster,
    cluster_values,
    format_numeric,
)
from cover_kbc.v3_1.compatibility import INTERVENTIONS  # noqa: E402
from cover_kbc.v3_1.config import V31SafeConfig  # noqa: E402
from cover_kbc.v3_1.numeric_recovery import canonicalize_numeric_output  # noqa: E402
from cover_kbc.v3_1.output_repair import repair_values  # noqa: E402
from cover_kbc.v3_1.prompts import prompt_inventory  # noqa: E402

SCHEMA_VERSION = "v3-1-score-recovery-v1"
STOCK = "companyTradesAtStockExchange"
BORDERS = "countryLandBordersCountry"


def load_evaluator(path: Path):
    """Import the official evaluator as a module, unmodified."""
    spec = importlib.util.spec_from_file_location("official_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Module 8 replay over persisted candidate state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateView:
    """The subset of ``Candidate`` that Module 8 actually reads."""

    key: str
    output_value: str
    numeric_value: float | None
    status: str
    verifier_label: str
    verifier_valid_prob: float | None
    score: float
    acquisition_groups: tuple[str, ...]

    @classmethod
    def from_telemetry(cls, row: Mapping[str, Any]) -> "CandidateView":
        return cls(
            key=row["candidate_key"],
            output_value=row["output_value"],
            numeric_value=row["numeric_value"],
            status=row["final_status"],
            verifier_label=row["verifier_label"] or "",
            verifier_valid_prob=row["verifier_valid_prob"],
            score=float(row["score"]),
            acquisition_groups=tuple(row["acquisition_groups"]),
        )

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"

    @property
    def rejected(self) -> bool:
        return self.status == "REJECTED"

    @property
    def verdict(self) -> str | None:
        """The verdict Module 8 reads, or ``None`` when none is calibrated."""
        if self.verifier_valid_prob is None or not self.verifier_label:
            return None
        return self.verifier_label


def acquisition_support(candidate: CandidateView, relation: str) -> int:
    """``scoring.supporting_acquisition_groups`` over persisted groups.

    The contract's eligible groups exclude the gate, the blind verifier and
    cross-model recall, which are paid through their own score terms.
    """
    eligible = {g.value for g in CONTRACTS[relation].eligible_independence_groups}
    return sum(1 for group in candidate.acquisition_groups if group in eligible)


def rank_key(candidate: CandidateView, relation: str):
    return (-candidate.score, -acquisition_support(candidate, relation), candidate.key)


def cluster_verdict(members: Sequence[CandidateView]) -> str | None:
    labels = [m.verdict for m in members if m.verdict]
    if not labels:
        return None
    if "INVALID" in labels and "VALID" not in labels:
        return "INVALID"
    if "VALID" in labels:
        return "VALID"
    return "UNKNOWN"


def is_retainable(members: Sequence[CandidateView]) -> bool:
    """Mirror of ``v3_1.retention.is_retainable`` over persisted state."""
    return any(m.accepted for m in members) and cluster_verdict(members) != "INVALID"


def numeric_clusters(
    candidates: Sequence[CandidateView], relation: str
) -> list[tuple[NumericCluster, list[CandidateView]]]:
    contract = CONTRACTS[relation]
    numeric = [c for c in candidates if c.numeric_value is not None and not c.rejected]
    if not numeric:
        return []
    weighted: list[float] = []
    for candidate in numeric:
        weight = max(1, acquisition_support(candidate, relation))
        weighted.extend([candidate.numeric_value] * weight)
    out = []
    for cluster in cluster_values(
        weighted, threshold=contract.selection.numeric_cluster_threshold
    ):
        low, high = min(cluster.values), max(cluster.values)
        members = [c for c in numeric if low <= (c.numeric_value or 0.0) <= high]
        out.append((cluster, members))
    return out


def cluster_support(members: Sequence[CandidateView], relation: str) -> int:
    return sum(max(1, acquisition_support(m, relation)) for m in members)


def select_row(row: Mapping[str, Any], safe: V31SafeConfig) -> list[str]:
    """Replay Module 8 for one query under ``safe``."""
    relation = row["Relation"]
    contract = CONTRACTS[relation]
    candidates = [
        CandidateView.from_telemetry(c)
        for c in row["candidates"]
        if not c["hard_rejected"] and c["final_status"] != "REJECTED"
    ]

    if relation == "hasArea":
        clusters = numeric_clusters(candidates, relation)
        if not clusters:
            return []
        if safe.final_candidate_retention:
            retained = [(c, m) for c, m in clusters if m and is_retainable(m)]
            clusters = retained or clusters
        cluster, members = clusters[0]
        if not members or not any(m.accepted for m in members):
            return []
        values = [format_numeric(
            cluster.representative,
            integer_only=contract.selection.numeric_integer_only,
        )]
    elif relation == "hasCapacity":
        clusters = numeric_clusters(candidates, relation)
        if not clusters:
            return []
        dominant = max(cluster_support(m, relation) for _, m in clusters)
        qualifying = []
        for cluster, members in clusters:
            if not members:
                continue
            verdict = cluster_verdict(members)
            if verdict == "INVALID":
                continue
            strong = cluster_support(members, relation) >= dominant
            if strong or verdict == "VALID":
                qualifying.append((cluster, members))
        qualifying = [(c, m) for c, m in qualifying if any(x.accepted for x in m)]
        if not qualifying and safe.final_candidate_retention:
            qualifying = [(c, m) for c, m in clusters if m and is_retainable(m)]
        if not qualifying:
            return []
        cluster, members = max(
            qualifying, key=lambda cm: (cm[0].representative, -cm[0].relative_mad)
        )
        values = [format_numeric(
            cluster.representative,
            integer_only=contract.selection.numeric_integer_only,
        )]
    else:
        if row["gate_negative"]:
            return []
        accepted = [c for c in candidates if c.accepted]
        if relation == STOCK:
            if safe.stock_structural_validation:
                from cover_kbc.normalization.strings import strict_key
                subject_key = strict_key(row["SubjectEntity"])
                never = {"none", "n a", "na", "nan", "null", "nil", "unknown",
                         "empty", "valid", "invalid", "not applicable",
                         "not listed", "private", "unlisted", "otc",
                         "over the counter"}
                accepted = [
                    c for c in accepted
                    if strict_key(c.output_value)
                    and strict_key(c.output_value) != subject_key
                    and strict_key(c.output_value) not in never
                ]
            if safe.stock_support_dominance and len(accepted) > 1:
                supports = [acquisition_support(c, relation) for c in accepted]
                if min(supports) != max(supports):
                    top = max(supports)
                    accepted = [c for c, s in zip(accepted, supports) if s >= top]
        accepted.sort(key=lambda c: rank_key(c, relation))
        limit = contract.selection.max_objects
        chosen = accepted[:limit] if limit else accepted
        values = [c.output_value for c in chosen]

    # Finalization-time value repair, exactly as ``selection._final_values``.
    if relation in ("hasArea", "hasCapacity"):
        return [
            canonicalize_numeric_output(
                value,
                integer_only=contract.selection.numeric_integer_only,
                enabled=safe.numeric_output_canonicalization,
            )
            for value in values
        ]
    return repair_values(values, enabled=safe.enumeration_label_repair)


def build_predictions(
    telemetry: Sequence[Mapping[str, Any]], safe: V31SafeConfig
) -> list[dict[str, Any]]:
    return [
        {
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "ObjectEntities": select_row(row, safe),
        }
        for row in telemetry
    ]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Scored:
    macro: Mapping[str, Mapping[str, float]]
    micro: Mapping[str, Mapping[str, float]]
    per_row: Mapping[tuple[str, str], Mapping[str, float]]


def score(evaluator, predictions: Sequence[Mapping[str, Any]],
          gold: Sequence[Mapping[str, Any]]) -> Scored:
    rows = evaluator.evaluate_per_sr_pair(
        list(predictions), list(gold), evaluator.RELATION_TYPE, tolerance=0.05
    )
    return Scored(
        macro=evaluator.macro_average_per_relation(rows),
        micro=evaluator.micro_average_per_relation(rows),
        per_row={(r["SubjectEntity"], r["Relation"]): r for r in rows},
    )


def macro_f1(scored: Scored) -> float:
    return float(scored.macro["*** All Relations ***"]["macro-f1"])


def micro_f1(scored: Scored) -> float:
    return float(scored.micro["*** All Relations ***"]["micro-f1"])


# --------------------------------------------------------------------------
# Ablation
# --------------------------------------------------------------------------

#: Incremental ablation order, as required by audit 0076 section 17.
ABLATION_STAGES: tuple[tuple[str, dict[str, bool]], ...] = (
    ("baseline", {}),
    ("+ final_candidate_retention", {"final_candidate_retention": True}),
    ("+ enumeration_label_repair", {"enumeration_label_repair": True}),
    ("+ numeric_output_canonicalization", {"numeric_output_canonicalization": True}),
    ("+ stock_structural_validation", {"stock_structural_validation": True}),
    ("+ stock_support_dominance", {"stock_support_dominance": True}),
)


def cumulative_stages() -> list[tuple[str, V31SafeConfig]]:
    flags: dict[str, bool] = {}
    out = []
    for label, delta in ABLATION_STAGES:
        flags.update(delta)
        out.append((label, V31SafeConfig(**flags)))
    return out


def relation_rows(scored: Scored, relation: str) -> list[Mapping[str, float]]:
    return [r for k, r in scored.per_row.items() if k[1] == relation]


def compare(base: Scored, new: Scored) -> dict[str, int]:
    improved = harmed = unchanged = 0
    for key, before in base.per_row.items():
        after = new.per_row[key]
        if after["f1"] > before["f1"] + 1e-12:
            improved += 1
        elif after["f1"] < before["f1"] - 1e-12:
            harmed += 1
        else:
            unchanged += 1
    return {"rows_improved": improved, "rows_harmed": harmed, "rows_unchanged": unchanged}


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover
        return ""


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--evaluator", type=Path,
                        default=REPO_ROOT / "benchmark" / "evaluate.py")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    telemetry_path = run_dir / "inference_telemetry.jsonl"
    predictions_path = run_dir / "predictions.jsonl"
    for path in (telemetry_path, predictions_path, args.gold, args.evaluator):
        if not path.exists():
            parser.error(f"missing required input: {path}")

    evaluator = load_evaluator(args.evaluator)
    telemetry = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line]
    committed = [json.loads(line) for line in predictions_path.read_text().splitlines() if line]
    gold = [json.loads(line) for line in args.gold.read_text().splitlines() if line]

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- fidelity gate: all-off replay must reproduce the committed run ----
    baseline_predictions = build_predictions(telemetry, V31SafeConfig())
    mismatches = [
        {
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "committed": json.dumps(want["ObjectEntities"], ensure_ascii=False),
            "replayed": json.dumps(got["ObjectEntities"], ensure_ascii=False),
        }
        for row, want, got in zip(telemetry, committed, baseline_predictions)
        if want["ObjectEntities"] != got["ObjectEntities"]
    ]
    if mismatches:
        print(f"FIDELITY FAILURE: {len(mismatches)} rows differ with V3.1 disabled",
              file=sys.stderr)
        for row in mismatches[:10]:
            print(f"  {row}", file=sys.stderr)
        return 1
    print(f"fidelity gate: {len(telemetry)} rows replayed identically with V3.1 off")

    baseline = score(evaluator, baseline_predictions, gold)
    relations = sorted({row["Relation"] for row in telemetry})

    # ---- incremental ablation ----
    ablation_rows: list[dict[str, Any]] = []
    relation_rows_out: list[dict[str, Any]] = []
    stages = cumulative_stages()
    final_predictions = baseline_predictions
    final_scored = baseline
    for label, safe in stages:
        predictions = build_predictions(telemetry, safe)
        scored = score(evaluator, predictions, gold)
        counts = compare(baseline, scored)
        ablation_rows.append({
            "stage": label,
            "enabled_features": ";".join(safe.enabled_features),
            "macro_f1": round(macro_f1(scored), 6),
            "delta_macro_f1": round(macro_f1(scored) - macro_f1(baseline), 6),
            "micro_f1": round(micro_f1(scored), 6),
            "delta_micro_f1": round(micro_f1(scored) - micro_f1(baseline), 6),
            **counts,
        })
        for relation in relations:
            relation_rows_out.append({
                "stage": label,
                "relation": relation,
                "baseline_macro_f1": round(baseline.macro[relation]["macro-f1"], 6),
                "macro_f1": round(scored.macro[relation]["macro-f1"], 6),
                "delta_macro_f1": round(
                    scored.macro[relation]["macro-f1"] - baseline.macro[relation]["macro-f1"], 6
                ),
                "macro_p": round(scored.macro[relation]["macro-p"], 6),
                "macro_r": round(scored.macro[relation]["macro-r"], 6),
            })
        final_predictions, final_scored = predictions, scored

    write_csv(out_dir / "safe_ablation.csv",
              ["stage", "enabled_features", "macro_f1", "delta_macro_f1", "micro_f1",
               "delta_micro_f1", "rows_improved", "rows_harmed", "rows_unchanged"],
              ablation_rows)
    write_csv(out_dir / "safe_relation_metrics.csv",
              ["stage", "relation", "baseline_macro_f1", "macro_f1", "delta_macro_f1",
               "macro_p", "macro_r"],
              relation_rows_out)

    # ---- per-row changes at the full-safe stage ----
    per_row: list[dict[str, Any]] = []
    for row, before, after in zip(telemetry, baseline_predictions, final_predictions):
        if before["ObjectEntities"] == after["ObjectEntities"]:
            continue
        key = (row["SubjectEntity"], row["Relation"])
        per_row.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "baseline_prediction": json.dumps(before["ObjectEntities"], ensure_ascii=False),
            "v3_1_prediction": json.dumps(after["ObjectEntities"], ensure_ascii=False),
            "baseline_f1": round(baseline.per_row[key]["f1"], 6),
            "v3_1_f1": round(final_scored.per_row[key]["f1"], 6),
            "delta_f1": round(final_scored.per_row[key]["f1"] - baseline.per_row[key]["f1"], 6),
        })
    write_csv(out_dir / "safe_per_row_changes.csv",
              ["row_index", "SubjectEntity", "Relation", "baseline_prediction",
               "v3_1_prediction", "baseline_f1", "v3_1_f1", "delta_f1"],
              per_row)

    with (out_dir / "safe_counterfactual_predictions.jsonl").open("w", encoding="utf-8") as fh:
        for prediction in final_predictions:
            fh.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")

    # ---- per-intervention analyses ----
    _write_analyses(out_dir, telemetry, gold, evaluator, baseline, baseline_predictions)

    # ---- border non-regression, the hard gate ----
    border_changed = [
        row["row_index"]
        for row, before, after in zip(telemetry, baseline_predictions, final_predictions)
        if row["Relation"] == BORDERS and before["ObjectEntities"] != after["ObjectEntities"]
    ]
    border_regression = (
        final_scored.macro[BORDERS]["macro-f1"] < baseline.macro[BORDERS]["macro-f1"] - 1e-12
    )

    # ---- calibration compatibility record ----
    compatibility = {
        "schema_version": SCHEMA_VERSION,
        "argument": (
            "Module 8 runs after _run_v3_control_loop; the pre-M8 hypothesis graph "
            "Module 21 reads is built with prediction=None, so finalization cannot "
            "reach action eligibility, execution, cost, state transitions, M21 input "
            "state or M20 budget behaviour."
        ),
        "interventions": [
            {
                "feature": record.feature,
                "track": record.track,
                "stage": record.stage,
                "compatibility": record.compatibility,
                "production_predicate": record.production_predicate,
                "train_discovery": record.train_discovery,
            }
            for record in INTERVENTIONS
        ],
        "safe_config_status": "SAFE_WITH_EXISTING_CALIBRATION",
        "aggressive_config_status": "CALIBRATION_REVIEW_REQUIRED",
    }
    (out_dir / "calibration_compatibility.json").write_text(
        json.dumps(compatibility, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    (out_dir / "aggressive_change_inventory.json").write_text(
        json.dumps({
            "schema_version": SCHEMA_VERSION,
            "status": "PROTOTYPE_NOT_RUN",
            "reason": (
                "Class B changes alter recall/prompt/action semantics and require a "
                "fresh M20/M21 calibration; they cannot be replayed from persisted "
                "V3 inference state because they change which evidence exists."
            ),
            "prompts": prompt_inventory(),
            "features": [
                {
                    "feature": record.feature,
                    "stage": record.stage,
                    "compatibility": record.compatibility,
                    "production_predicate": record.production_predicate,
                    "train_discovery": record.train_discovery,
                }
                for record in INTERVENTIONS if record.track == "aggressive"
            ],
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_head": head_sha(),
        "frozen_test_submission_sha": "16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5",
        "run_dir": str(run_dir),
        "rows": len(telemetry),
        "predictions_sha256": sha256_file(predictions_path),
        "telemetry_sha256": sha256_file(telemetry_path),
        "gold": str(args.gold),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(args.evaluator),
        "fidelity_gate": "PASS",
        "baseline_macro_f1": round(macro_f1(baseline), 6),
        "safe_macro_f1": round(macro_f1(final_scored), 6),
        "delta_macro_f1": round(macro_f1(final_scored) - macro_f1(baseline), 6),
        "baseline_micro_f1": round(micro_f1(baseline), 6),
        "safe_micro_f1": round(micro_f1(final_scored), 6),
        "delta_micro_f1": round(micro_f1(final_scored) - micro_f1(baseline), 6),
        "rows_changed": len(per_row),
        **compare(baseline, final_scored),
        "per_relation": {
            relation: {
                "baseline_macro_f1": round(baseline.macro[relation]["macro-f1"], 6),
                "safe_macro_f1": round(final_scored.macro[relation]["macro-f1"], 6),
                "delta_macro_f1": round(
                    final_scored.macro[relation]["macro-f1"]
                    - baseline.macro[relation]["macro-f1"], 6
                ),
            }
            for relation in relations
        },
        "border_non_regression": {
            "relation": BORDERS,
            "rows_changed": len(border_changed),
            "changed_row_indices": border_changed,
            "baseline_macro_f1": round(baseline.macro[BORDERS]["macro-f1"], 6),
            "safe_macro_f1": round(final_scored.macro[BORDERS]["macro-f1"], 6),
            "regressed": bool(border_regression),
            "verdict": "REGRESSION" if border_regression else "IDENTICAL_PREDICTIONS"
            if not border_changed else "CHANGED_WITHOUT_REGRESSION",
        },
        "safe_config_status": "SAFE_WITH_EXISTING_CALIBRATION",
        "aggressive_config_status": "CALIBRATION_REVIEW_REQUIRED",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    checksums = sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt"
    )
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8"
    )

    print(f"baseline macro-F1 {macro_f1(baseline):.5f} -> safe {macro_f1(final_scored):.5f} "
          f"({macro_f1(final_scored) - macro_f1(baseline):+.5f})")
    print(f"rows changed {len(per_row)}, improved {summary['rows_improved']}, "
          f"harmed {summary['rows_harmed']}")
    print(f"borders: {summary['border_non_regression']['verdict']}")
    if border_regression:
        print("BORDER REGRESSION - safe track is not releasable", file=sys.stderr)
        return 1
    return 0


def _write_analyses(
    out_dir: Path,
    telemetry: Sequence[Mapping[str, Any]],
    gold: Sequence[Mapping[str, Any]],
    evaluator,
    baseline: Scored,
    baseline_predictions: Sequence[Mapping[str, Any]],
) -> None:
    """Per-intervention evidence tables."""

    def single(flag: str) -> tuple[list[dict[str, Any]], Scored]:
        predictions = build_predictions(telemetry, V31SafeConfig(**{flag: True}))
        return predictions, score(evaluator, predictions, gold)

    # finalization retention
    retention_predictions, retention_scored = single("final_candidate_retention")
    rows = []
    for row, before, after, goldrow in zip(
        telemetry, baseline_predictions, retention_predictions, gold
    ):
        survived = row["stage_values"]["CONTROL_SURVIVED"]
        emitted = row["stage_values"]["FINAL_EMITTED"]
        if not survived or emitted:
            continue
        key = (row["SubjectEntity"], row["Relation"])
        rows.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "control_survived": json.dumps(survived, ensure_ascii=False),
            "baseline_prediction": json.dumps(before["ObjectEntities"], ensure_ascii=False),
            "retained_prediction": json.dumps(after["ObjectEntities"], ensure_ascii=False),
            "gold": json.dumps(goldrow["ObjectEntities"], ensure_ascii=False),
            "delta_f1": round(
                retention_scored.per_row[key]["f1"] - baseline.per_row[key]["f1"], 6
            ),
        })
    write_csv(out_dir / "finalization_retention_analysis.csv",
              ["row_index", "SubjectEntity", "Relation", "control_survived",
               "baseline_prediction", "retained_prediction", "gold", "delta_f1"], rows)

    # numeric canonicalization
    numeric_predictions, numeric_scored = single("numeric_output_canonicalization")
    rows = []
    for row, before, after in zip(telemetry, baseline_predictions, numeric_predictions):
        if row["Relation"] not in ("hasArea", "hasCapacity"):
            continue
        rows.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "baseline_prediction": json.dumps(before["ObjectEntities"], ensure_ascii=False),
            "canonicalized_prediction": json.dumps(after["ObjectEntities"], ensure_ascii=False),
            "changed": before["ObjectEntities"] != after["ObjectEntities"],
        })
    write_csv(out_dir / "numeric_rule_analysis.csv",
              ["row_index", "SubjectEntity", "Relation", "baseline_prediction",
               "canonicalized_prediction", "changed"], rows)

    # stock finalization
    stock_predictions, stock_scored = single("stock_support_dominance")
    rows = []
    for row, before, after, goldrow in zip(
        telemetry, baseline_predictions, stock_predictions, gold
    ):
        if row["Relation"] != STOCK:
            continue
        key = (row["SubjectEntity"], row["Relation"])
        supports = {
            c["output_value"]: acquisition_support(CandidateView.from_telemetry(c), STOCK)
            for c in row["candidates"] if c["final_status"] == "ACCEPTED"
        }
        rows.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "baseline_prediction": json.dumps(before["ObjectEntities"], ensure_ascii=False),
            "dominance_prediction": json.dumps(after["ObjectEntities"], ensure_ascii=False),
            "gold": json.dumps(goldrow["ObjectEntities"], ensure_ascii=False),
            "accepted_support": json.dumps(supports, ensure_ascii=False, sort_keys=True),
            "delta_f1": round(stock_scored.per_row[key]["f1"] - baseline.per_row[key]["f1"], 6),
        })
    write_csv(out_dir / "stock_finalization_analysis.csv",
              ["row_index", "SubjectEntity", "baseline_prediction", "dominance_prediction",
               "gold", "accepted_support", "delta_f1"], rows)

    # city finalization: evidence ordering is already in force, recorded as such
    rows = []
    for row, before, goldrow in zip(telemetry, baseline_predictions, gold):
        if row["Relation"] != "personHasCityOfDeath":
            continue
        accepted = [
            CandidateView.from_telemetry(c) for c in row["candidates"]
            if c["final_status"] == "ACCEPTED"
        ]
        if not accepted:
            continue
        rows.append({
            "row_index": row["row_index"],
            "SubjectEntity": row["SubjectEntity"],
            "accepted_candidates": json.dumps(
                {c.output_value: acquisition_support(c, "personHasCityOfDeath")
                 for c in accepted}, ensure_ascii=False, sort_keys=True),
            "prediction": json.dumps(before["ObjectEntities"], ensure_ascii=False),
            "gold": json.dumps(goldrow["ObjectEntities"], ensure_ascii=False),
            "distinguishable_by_support": len({
                acquisition_support(c, "personHasCityOfDeath") for c in accepted
            }) > 1,
        })
    write_csv(out_dir / "city_finalization_analysis.csv",
              ["row_index", "SubjectEntity", "accepted_candidates", "prediction", "gold",
               "distinguishable_by_support"], rows)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
