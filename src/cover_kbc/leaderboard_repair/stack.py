"""Orchestrator for L7-L9 leaderboard repair."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from cover_kbc.models.base import LMRuntime
from cover_kbc.types import Prediction, Query
from cover_kbc.v3_core.hypothesis import QueryHypothesisGraph

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig, build_config
from cover_kbc.leaderboard_repair.consistency import apply_l8_consistency
from cover_kbc.leaderboard_repair.relations import REPAIR_BY_RELATION
from cover_kbc.leaderboard_repair.risk_guard import apply_l9_guard
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import RepairResult, RowBudget, RowRepairRecord
from cover_kbc.leaderboard_repair.util import candidate_signals


class LeaderboardRepairStack:
    """Feature-flagged post-pipeline repair stack."""

    def __init__(
        self,
        *,
        config: LeaderboardRepairConfig,
        enumerator: LMRuntime,
        verifier: LMRuntime,
    ) -> None:
        self.config = config
        self.enumerator = enumerator
        self.verifier = verifier

    def apply(
        self,
        predictions: Sequence[Prediction],
        *,
        queries: Sequence[Query],
        hypothesis_graphs: Sequence[QueryHypothesisGraph] = (),
    ) -> RepairResult:
        if not self.config.enabled:
            return RepairResult(predictions=list(predictions), records=[], accounting={})
        graph_by_key = {
            (graph.subject, graph.relation): graph for graph in hypothesis_graphs
        }
        repaired: list[Prediction] = []
        records: dict[tuple[str, str], RowRepairRecord] = {}
        callers: dict[tuple[str, str], RepairCaller] = {}

        for prediction in predictions:
            key = (prediction.subject, prediction.relation)
            cap = self.config.cap_for(prediction.relation)
            budget = RowBudget(cap=cap)
            caller = RepairCaller(
                enumerator=self.enumerator,
                verifier=self.verifier,
                relation=prediction.relation,
                subject=prediction.subject,
                row_budget=budget,
            )
            record = RowRepairRecord(
                subject=prediction.subject,
                relation=prediction.relation,
                row_index=prediction.row_index,
                before=list(prediction.object_entities),
                after=list(prediction.object_entities),
                budget_cap=cap,
            )
            graph = graph_by_key.get(key)
            signals = candidate_signals(prediction, graph)
            repairer = REPAIR_BY_RELATION.get(prediction.relation)
            values = list(prediction.object_entities)
            if repairer is not None and cap > 0:
                values = repairer(prediction, signals, caller, self.config, record)
            elif cap <= 0:
                record.skipped.append("relation repair disabled by zero cap")
            post_l7 = replace(prediction, object_entities=list(values))
            if self.config.features.l9_final_risk_guard:
                post_l7 = apply_l9_guard(post_l7, record)
            record.calls = list(budget.calls)
            record.after = list(post_l7.object_entities)
            repaired.append(post_l7)
            records[key] = record
            callers[key] = caller

        repaired = apply_l8_consistency(repaired, records, callers, self.config)
        if self.config.features.l9_final_risk_guard:
            final_predictions = []
            for prediction in repaired:
                key = (prediction.subject, prediction.relation)
                guarded = apply_l9_guard(prediction, records[key])
                records[key].after = list(guarded.object_entities)
                records[key].calls = list(callers[key].row_budget.calls)
                final_predictions.append(guarded)
            repaired = final_predictions
        else:
            for prediction in repaired:
                key = (prediction.subject, prediction.relation)
                records[key].after = list(prediction.object_entities)
                records[key].calls = list(callers[key].row_budget.calls)

        ordered_records = [records[(p.subject, p.relation)] for p in repaired]
        accounting = self.accounting(ordered_records, predictions_before=predictions)
        _assert_query_coverage(repaired, queries)
        return RepairResult(
            predictions=repaired,
            records=ordered_records,
            accounting=accounting,
        )

    def accounting(
        self,
        records: Sequence[RowRepairRecord],
        *,
        predictions_before: Sequence[Prediction],
    ) -> dict[str, object]:
        by_relation: dict[str, dict[str, object]] = {}
        rows_by_relation = defaultdict(list)
        for record in records:
            rows_by_relation[record.relation].append(record)

        for relation, relation_records in sorted(rows_by_relation.items()):
            calls = [record.calls_used for record in relation_records]
            changed = sum(1 for record in relation_records if record.changed)
            feature_counts = Counter(
                feature for record in relation_records for feature in record.features
            )
            by_relation[relation] = {
                "rows": len(relation_records),
                "changed_rows": changed,
                "total_repair_calls": sum(calls),
                "mean_repair_calls": (sum(calls) / len(calls)) if calls else 0.0,
                "max_repair_calls": max(calls) if calls else 0,
                "features": dict(sorted(feature_counts.items())),
            }

        total_calls = sum(record.calls_used for record in records)
        changed_rows = sum(1 for record in records if record.changed)
        return {
            "schema_version": "leaderboard-repair-accounting-v1",
            "repair_version": self.config.repair_version,
            "profile": self.config.profile,
            "enabled": self.config.enabled,
            "rows": len(records),
            "changed_rows": changed_rows,
            "total_repair_calls": total_calls,
            "mean_repair_calls": total_calls / max(1, len(records)),
            "max_repair_calls": max((record.calls_used for record in records), default=0),
            "max_calls_by_relation": dict(sorted(self.config.max_calls_by_relation.items())),
            "by_relation": by_relation,
            "pipeline_rows_before_repair": len(predictions_before),
        }

    def write_artifacts(self, result: RepairResult, out_dir: Path) -> tuple[Path, Path]:
        records_path = out_dir / self.config.artifacts_file
        accounting_path = out_dir / self.config.accounting_file
        with records_path.open("w", encoding="utf-8") as handle:
            for record in result.records:
                handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        accounting_path.write_text(
            json.dumps(result.accounting, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return records_path, accounting_path


def build_repair_stack(
    raw_config: object,
    *,
    enumerator: LMRuntime,
    verifier: LMRuntime,
) -> LeaderboardRepairStack | None:
    if not isinstance(raw_config, dict):
        return None
    config = build_config(raw_config)
    if not config.enabled:
        return None
    return LeaderboardRepairStack(
        config=config,
        enumerator=enumerator,
        verifier=verifier,
    )


def _assert_query_coverage(predictions: Sequence[Prediction], queries: Sequence[Query]) -> None:
    expected = [(q.subject, q.relation) for q in queries]
    actual = [(p.subject, p.relation) for p in predictions]
    if expected != actual:
        raise RuntimeError("leaderboard repair changed row coverage or order")
