"""Evaluation harness: run the official scorer and capture structured metrics.

Two invocation modes are provided:

``evaluate_predictions`` / ``evaluate_files``
    Call the official module's scoring functions in-process.  This is what the
    experiment pipeline uses, because it returns machine-readable metrics.

``evaluate_via_cli``
    Shell out to ``python benchmark/evaluate.py -p ... -g ...`` exactly as a
    challenge participant would, and capture the printed table.  Used to prove
    that the in-process path and the official CLI agree.

Neither mode writes into ``benchmark/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from cover_kbc.evaluation.official import (
    evaluator_checksum,
    load_official_evaluator,
    relation_types,
)
from cover_kbc.paths import BENCHMARK_EVALUATOR, REPO_ROOT

#: The official tolerance for numeric relations (5% relative).
DEFAULT_TOLERANCE = 0.05

OVERALL_KEY = "*** All Relations ***"


@dataclass
class EvaluationReport:
    """Structured result of one official evaluation."""

    macro: dict[str, dict[str, float]] = field(default_factory=dict)
    micro: dict[str, dict[str, float]] = field(default_factory=dict)
    stats: dict[str, dict[str, float]] = field(default_factory=dict)
    per_pair: list[dict[str, Any]] = field(default_factory=list)
    tolerance: float = DEFAULT_TOLERANCE
    evaluator_sha256: str = ""
    num_gt_rows: int = 0
    num_pred_rows: int = 0
    missing_pred_rows: list[dict[str, str]] = field(default_factory=list)
    extra_pred_rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def overall_macro_f1(self) -> float:
        return self.macro.get(OVERALL_KEY, {}).get("macro-f1", 0.0)

    @property
    def overall_macro_precision(self) -> float:
        return self.macro.get(OVERALL_KEY, {}).get("macro-p", 0.0)

    @property
    def overall_macro_recall(self) -> float:
        return self.macro.get(OVERALL_KEY, {}).get("macro-r", 0.0)

    def relations(self) -> list[str]:
        return [rel for rel in self.macro if rel != OVERALL_KEY]

    def to_json(self) -> dict[str, Any]:
        return {
            "macro": self.macro,
            "micro": self.micro,
            "stats": self.stats,
            "tolerance": self.tolerance,
            "evaluator_sha256": self.evaluator_sha256,
            "num_gt_rows": self.num_gt_rows,
            "num_pred_rows": self.num_pred_rows,
            "missing_pred_rows": self.missing_pred_rows,
            "extra_pred_rows": self.extra_pred_rows,
        }

    def to_table(self) -> str:
        """Compact text table - the same numbers the official CLI prints."""
        header = f"{'relation':<32}{'macro-p':>9}{'macro-r':>9}{'macro-f1':>10}{'#preds':>9}{'#empty':>8}"
        lines = [header, "-" * len(header)]
        ordered = sorted(self.relations()) + [OVERALL_KEY]
        for rel in ordered:
            macro = self.macro.get(rel, {})
            stat = self.stats.get(rel, {})
            lines.append(
                f"{rel:<32}"
                f"{macro.get('macro-p', 0.0):>9.3f}"
                f"{macro.get('macro-r', 0.0):>9.3f}"
                f"{macro.get('macro-f1', 0.0):>10.3f}"
                f"{stat.get('avg. #preds', 0.0):>9.2f}"
                f"{int(stat.get('#empty preds', 0)):>8d}"
            )
        return "\n".join(lines)


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return load_official_evaluator().read_jsonl_file(str(path))


def evaluate_predictions(
    pred_rows: Sequence[Mapping[str, Any]],
    gt_rows: Sequence[Mapping[str, Any]],
    tolerance: float = DEFAULT_TOLERANCE,
) -> EvaluationReport:
    """Score in-memory rows with the official scoring functions.

    Args:
        pred_rows: rows with ``SubjectEntity``/``Relation``/``ObjectEntities``.
        gt_rows: official ground-truth rows.
        tolerance: relative tolerance for numeric relations.

    Returns:
        An :class:`EvaluationReport`.  Coverage bookkeeping (missing/extra
        prediction rows) is computed here rather than by the official scorer,
        which silently treats a missing row as an empty prediction.
    """
    evaluator = load_official_evaluator()
    rel_types = relation_types()

    per_pair = evaluator.evaluate_per_sr_pair(
        list(pred_rows), list(gt_rows), rel_types, tolerance=tolerance
    )
    if not per_pair:
        return EvaluationReport(
            tolerance=tolerance,
            evaluator_sha256=evaluator_checksum(),
            num_gt_rows=len(gt_rows),
            num_pred_rows=len(pred_rows),
        )

    gt_keys = {(r["SubjectEntity"], r["Relation"]) for r in gt_rows}
    pred_keys = {(r["SubjectEntity"], r["Relation"]) for r in pred_rows}

    return EvaluationReport(
        macro=evaluator.macro_average_per_relation(per_pair),
        micro=evaluator.micro_average_per_relation(per_pair),
        stats=evaluator.prediction_statistics(per_pair),
        per_pair=per_pair,
        tolerance=tolerance,
        evaluator_sha256=evaluator_checksum(),
        num_gt_rows=len(gt_rows),
        num_pred_rows=len(pred_rows),
        missing_pred_rows=[
            {"SubjectEntity": s, "Relation": r} for s, r in sorted(gt_keys - pred_keys)
        ],
        extra_pred_rows=[
            {"SubjectEntity": s, "Relation": r} for s, r in sorted(pred_keys - gt_keys)
        ],
    )


def evaluate_files(
    predictions_path: str | Path,
    ground_truth_path: str | Path,
    tolerance: float = DEFAULT_TOLERANCE,
) -> EvaluationReport:
    """Score two JSONL files with the official scoring functions."""
    return evaluate_predictions(
        _read_jsonl(predictions_path), _read_jsonl(ground_truth_path), tolerance=tolerance
    )


def evaluate_via_cli(
    predictions_path: str | Path,
    ground_truth_path: str | Path,
    timeout: float = 600.0,
) -> str:
    """Run the official evaluator exactly as the challenge documents it.

    Returns the captured stdout table.  Raises ``CalledProcessError`` on a
    non-zero exit so a broken prediction file cannot pass silently.
    """
    result = subprocess.run(
        [
            sys.executable,
            str(BENCHMARK_EVALUATOR),
            "-p",
            str(Path(predictions_path).resolve()),
            "-g",
            str(Path(ground_truth_path).resolve()),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout


def write_report(report: EvaluationReport, path: str | Path) -> Path:
    """Persist a report as JSON next to the predictions it scored."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n")
    return out
