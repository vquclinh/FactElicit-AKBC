"""Orchestrator for active post-pipeline leaderboard repair."""

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
from cover_kbc.leaderboard_repair.relations import REPAIR_BY_RELATION
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import RepairResult, RowBudget, RowRepairRecord
from cover_kbc.leaderboard_repair.util import AREA, CAPACITY, CITY, candidate_signals


class LeaderboardRepairStack:
    """Feature-flagged post-pipeline repair stack.

    The active baseline uses deterministic award metadata cleanup, E1's Mistral
    City empty-row rescue, Direct Area or E3 Area Multi-View for hasArea rows,
    and E2 Capacity Multi-View for hasCapacity rows. Retired C2 L8/L9
    behaviours remain in historical audits/config metadata, but are no longer
    executable runtime branches.
    """

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
            post_repair = replace(prediction, object_entities=list(values))
            record.calls = list(budget.calls)
            record.after = list(post_repair.object_entities)
            repaired.append(post_repair)
            records[key] = record

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
            if (
                relation == CITY
                and self.config.features.mistral_city_empty_rescue
            ):
                by_relation[relation]["e1_city_rescue"] = (
                    _e1_city_rescue_accounting(relation_records)
                )
            if (
                relation == AREA
                and self.config.features.mistral_direct_area
                and not self.config.features.mistral_area_multiview
            ):
                by_relation[relation]["direct_area"] = (
                    _direct_area_accounting(relation_records)
                )
            if (
                relation == AREA
                and self.config.features.mistral_area_multiview
            ):
                by_relation[relation]["area_multiview"] = (
                    _area_multiview_accounting(relation_records)
                )
            if (
                relation == CAPACITY
                and self.config.features.mistral_capacity_multiview
            ):
                by_relation[relation]["capacity_multiview"] = (
                    _capacity_multiview_accounting(relation_records)
                )

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


def _e1_city_rescue_accounting(
    records: Sequence[RowRepairRecord],
) -> dict[str, int]:
    summary = {
        "eligible_empty_rows": 0,
        "bypassed_non_empty_rows": 0,
        "life_status_calls": 0,
        "deceased_count": 0,
        "living_count": 0,
        "unknown_count": 0,
        "invalid_life_outputs": 0,
        "city_calls": 0,
        "city_accepted_count": 0,
        "city_unknown_count": 0,
        "invalid_city_outputs": 0,
        "changed_rows": 0,
    }
    for record in records:
        e1_decisions = [
            decision for decision in record.decisions
            if decision.get("feature") == "MistralCityEmptyRescue"
        ]
        if not e1_decisions:
            continue
        if record.changed:
            summary["changed_rows"] += 1
        for decision in e1_decisions:
            kind = str(decision.get("decision", ""))
            label = str(decision.get("label", ""))
            if kind == "eligible_empty_row":
                summary["eligible_empty_rows"] += 1
            elif kind == "bypassed_profile_d_non_empty":
                summary["bypassed_non_empty_rows"] += 1
            elif kind == "life_status_output":
                summary["life_status_calls"] += 1
                if label == "DECEASED":
                    summary["deceased_count"] += 1
                elif label == "LIVING":
                    summary["living_count"] += 1
                elif label == "UNKNOWN":
                    summary["unknown_count"] += 1
                else:
                    summary["invalid_life_outputs"] += 1
            elif kind == "city_output":
                summary["city_calls"] += 1
                if label == "CITY":
                    summary["city_accepted_count"] += 1
                elif label == "UNKNOWN":
                    summary["city_unknown_count"] += 1
                else:
                    summary["invalid_city_outputs"] += 1
    return summary


def _direct_area_accounting(
    records: Sequence[RowRepairRecord],
) -> dict[str, int]:
    summary = {
        "eligible_hasArea_rows": 0,
        "direct_area_calls": 0,
        "valid_area_count": 0,
        "unknown_count": 0,
        "invalid_count": 0,
        "error_count": 0,
        "changed_rows": 0,
    }
    for record in records:
        direct_area_decisions = [
            decision for decision in record.decisions
            if decision.get("feature") == "MistralDirectArea"
        ]
        if not direct_area_decisions:
            continue
        if record.changed:
            summary["changed_rows"] += 1
        if any("MistralDirectArea" in item for item in record.skipped):
            summary["error_count"] += 1
        for decision in direct_area_decisions:
            kind = str(decision.get("decision", ""))
            status = str(decision.get("status", ""))
            if kind == "eligible_has_area_row":
                summary["eligible_hasArea_rows"] += 1
            elif kind == "direct_area_output":
                summary["direct_area_calls"] += 1
                if status == "VALID_AREA":
                    summary["valid_area_count"] += 1
                elif status == "UNKNOWN":
                    summary["unknown_count"] += 1
                else:
                    summary["invalid_count"] += 1
    return summary


def _area_multiview_accounting(
    records: Sequence[RowRepairRecord],
) -> dict[str, object]:
    summary: dict[str, object] = {
        "eligible_hasArea_rows": 0,
        "area_multiview_v1_direct_calls": 0,
        "area_multiview_v2_entity_type_calls": 0,
        "area_multiview_v3_infobox_calls": 0,
        "area_multiview_v4_attribute_contrast_calls": 0,
        "area_multiview_judge_calls": 0,
        "valid_count_by_view": {},
        "unknown_count_by_view": {},
        "invalid_count_by_view": {},
        "strong_consensus_count": 0,
        "judge_selected_count": 0,
        "fallback_v1_direct_area_count": 0,
        "fallback_top_cluster_count": 0,
        "empty_count": 0,
        "error_count": 0,
        "changed_rows": 0,
    }
    valid_by_view: Counter[str] = Counter()
    unknown_by_view: Counter[str] = Counter()
    invalid_by_view: Counter[str] = Counter()
    for record in records:
        decisions = [
            decision for decision in record.decisions
            if decision.get("feature") == "MistralAreaMultiView"
        ]
        if not decisions:
            continue
        if record.changed:
            summary["changed_rows"] = int(summary["changed_rows"]) + 1
        if any("MistralAreaMultiView" in item for item in record.skipped):
            summary["error_count"] = int(summary["error_count"]) + 1
        for decision in decisions:
            kind = str(decision.get("decision", ""))
            view_id = str(decision.get("view_id", ""))
            status = str(decision.get("status", ""))
            if kind == "eligible_has_area_row":
                summary["eligible_hasArea_rows"] = (
                    int(summary["eligible_hasArea_rows"]) + 1
                )
            elif kind == "view_output":
                calls_key = f"{view_id}_calls"
                if calls_key in summary:
                    summary[calls_key] = int(summary[calls_key]) + 1
                if status == "VALID_AREA":
                    valid_by_view[view_id] += 1
                elif status == "UNKNOWN":
                    unknown_by_view[view_id] += 1
                else:
                    invalid_by_view[view_id] += 1
            elif kind == "judge_output":
                summary["area_multiview_judge_calls"] = (
                    int(summary["area_multiview_judge_calls"]) + 1
                )
            elif kind == "final_decision":
                reason = str(decision.get("reason", ""))
                if reason == "MULTIVIEW_SUPPORT_GE_3":
                    summary["strong_consensus_count"] = (
                        int(summary["strong_consensus_count"]) + 1
                    )
                elif reason == "JUDGE":
                    summary["judge_selected_count"] = (
                        int(summary["judge_selected_count"]) + 1
                    )
                elif reason == "FALLBACK_V1_DIRECT_AREA":
                    summary["fallback_v1_direct_area_count"] = (
                        int(summary["fallback_v1_direct_area_count"]) + 1
                    )
                elif reason == "FALLBACK_TOP_CLUSTER":
                    summary["fallback_top_cluster_count"] = (
                        int(summary["fallback_top_cluster_count"]) + 1
                    )
                elif reason == "NO_VALUE":
                    summary["empty_count"] = int(summary["empty_count"]) + 1
    summary["valid_count_by_view"] = dict(sorted(valid_by_view.items()))
    summary["unknown_count_by_view"] = dict(sorted(unknown_by_view.items()))
    summary["invalid_count_by_view"] = dict(sorted(invalid_by_view.items()))
    return summary


def _capacity_multiview_accounting(
    records: Sequence[RowRepairRecord],
) -> dict[str, object]:
    summary: dict[str, object] = {
        "eligible_hasCapacity_rows": 0,
        "capacity_multiview_v1_calls": 0,
        "capacity_multiview_v2_calls": 0,
        "capacity_multiview_v3_calls": 0,
        "capacity_multiview_v4_calls": 0,
        "capacity_multiview_judge_calls": 0,
        "valid_count_by_view": {},
        "unknown_count_by_view": {},
        "invalid_count_by_view": {},
        "strong_consensus_count": 0,
        "judge_selected_count": 0,
        "fallback_v1_count": 0,
        "fallback_top_cluster_count": 0,
        "empty_count": 0,
        "error_count": 0,
        "changed_rows": 0,
    }
    valid_by_view: Counter[str] = Counter()
    unknown_by_view: Counter[str] = Counter()
    invalid_by_view: Counter[str] = Counter()
    for record in records:
        decisions = [
            decision for decision in record.decisions
            if decision.get("feature") == "MistralCapacityMultiView"
        ]
        if not decisions:
            continue
        if record.changed:
            summary["changed_rows"] = int(summary["changed_rows"]) + 1
        if any("MistralCapacityMultiView" in item for item in record.skipped):
            summary["error_count"] = int(summary["error_count"]) + 1
        for decision in decisions:
            kind = str(decision.get("decision", ""))
            view_id = str(decision.get("view_id", ""))
            status = str(decision.get("status", ""))
            if kind == "eligible_has_capacity_row":
                summary["eligible_hasCapacity_rows"] = (
                    int(summary["eligible_hasCapacity_rows"]) + 1
                )
            elif kind == "view_output":
                calls_key = f"{view_id}_calls"
                if calls_key in summary:
                    summary[calls_key] = int(summary[calls_key]) + 1
                if status == "VALID_CAPACITY":
                    valid_by_view[view_id] += 1
                elif status == "UNKNOWN":
                    unknown_by_view[view_id] += 1
                else:
                    invalid_by_view[view_id] += 1
            elif kind == "judge_output":
                summary["capacity_multiview_judge_calls"] = (
                    int(summary["capacity_multiview_judge_calls"]) + 1
                )
            elif kind == "final_decision":
                reason = str(decision.get("reason", ""))
                if reason == "strong_cluster":
                    summary["strong_consensus_count"] = (
                        int(summary["strong_consensus_count"]) + 1
                    )
                elif reason == "judge_selected":
                    summary["judge_selected_count"] = (
                        int(summary["judge_selected_count"]) + 1
                    )
                elif reason == "fallback_v1":
                    summary["fallback_v1_count"] = int(summary["fallback_v1_count"]) + 1
                elif reason == "fallback_top_cluster":
                    summary["fallback_top_cluster_count"] = (
                        int(summary["fallback_top_cluster_count"]) + 1
                    )
                elif reason == "no_numeric_values":
                    summary["empty_count"] = int(summary["empty_count"]) + 1
    summary["valid_count_by_view"] = dict(sorted(valid_by_view.items()))
    summary["unknown_count_by_view"] = dict(sorted(unknown_by_view.items()))
    summary["invalid_count_by_view"] = dict(sorted(invalid_by_view.items()))
    return summary
