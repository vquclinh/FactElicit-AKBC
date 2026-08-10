#!/usr/bin/env python3
"""Offline reachability analysis for V3 TRAIN action-family coverage.

This script makes zero model calls and reads no gold. It inspects persisted
collection artifacts to distinguish legal catalogue opportunities from
selectable opportunities and executed observations.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.controller_calibration.supplemental_coverage import (
    EXPECTED_SUPPLEMENTAL_FAMILIES,
    resolve_collection_run_dir,
)
from cover_kbc.contracts.registry import CONTRACTS


TARGET_FAMILIES = EXPECTED_SUPPLEMENTAL_FAMILIES
SCHEMA_VERSION = "v3-action-reachability-v1"

PLANNED_COSTS = {
    "LISTING_ELIMINATION": {"physical_calls": 1, "generated_tokens": 0},
    "SEMANTIC_VERIFY": {"physical_calls": 1, "generated_tokens": 0},
    "SET_EXPANSION": {"physical_calls": 1, "generated_tokens": 512},
    "UNARY_VERIFY": {"physical_calls": 1, "generated_tokens": 0},
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _active_primary(hgraph: Mapping[str, Any]) -> Mapping[str, Any] | None:
    active = [
        dict(item) for item in hgraph.get("hypotheses", ())
        if item.get("status") != "DROPPED"
    ]
    if not active:
        return None
    return sorted(
        active,
        key=lambda item: (
            -int(item.get("independent_support_count") or 0),
            -int(item.get("raw_support_count") or 0),
            str(item.get("hypothesis_id", "")),
        ),
    )[0]


def _target_catalogue_families(hgraph: Mapping[str, Any]) -> tuple[str, ...]:
    relation = str(hgraph.get("Relation", ""))
    state = str(hgraph.get("failure_state", ""))
    primary = _active_primary(hgraph)
    if relation == "awardWonBy" and state == "SET_GROWING":
        families = ["SET_EXPANSION"]
        if primary is not None:
            families.append("UNARY_VERIFY")
        return tuple(families)
    if (
        relation == "companyTradesAtStockExchange"
        and state == "HIGH_FP_RISK"
        and primary is not None
    ):
        return ("LISTING_ELIMINATION", "SEMANTIC_VERIFY")
    return ()


def _hgraph_only_mismatches(hgraph: Mapping[str, Any]) -> tuple[str, ...]:
    relation = str(hgraph.get("Relation", ""))
    state = str(hgraph.get("failure_state", ""))
    legal = set(hgraph.get("legal_action_families") or ())
    primary = _active_primary(hgraph)
    if (
        relation == "companyTradesAtStockExchange"
        and state == "SEMANTIC_AMBIGUITY"
        and "SEMANTIC_VERIFY" in legal
        and primary is None
    ):
        return ("SEMANTIC_VERIFY",)
    return ()


def _relation_budget_caps(config: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    pipeline = dict((config or {}).get("pipeline") or {})
    global_calls = int(pipeline.get("max_calls_per_query", 12))
    global_tokens = int(pipeline.get("max_generated_tokens_per_query", 6000))
    caps = {}
    for relation, contract in CONTRACTS.items():
        caps[relation] = {
            "max_calls": min(global_calls, contract.stopping.max_calls),
            "max_generated_tokens": min(
                global_tokens, contract.stopping.max_generated_tokens
            ),
        }
    return caps


def _load_config(collection_dir: Path, run_dir: Path, config_path: Path | None) -> dict:
    if config_path is not None:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    candidates = [
        collection_dir / "cover_kbc_v3_train_collection.yaml",
        run_dir.parent.parent / "cover_kbc_v3_train_collection.yaml",
        Path("configs/experiments/cover_kbc_v3_train_collection.yaml"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    return {}


def analyze(collection_dir: Path, *, config_path: Path | None = None) -> dict[str, Any]:
    run_dir = resolve_collection_run_dir(collection_dir)
    config = _load_config(collection_dir, run_dir, config_path)
    caps = _relation_budget_caps(config)
    coverage = _json(run_dir / "v3_action_coverage.json")
    manifest = _json(run_dir / "manifest.json")

    inference = {
        int(row["row_index"]): row
        for row in _jsonl(run_dir / "inference_telemetry.jsonl")
    }
    telemetry = defaultdict(list)
    for record in _jsonl(run_dir / "train_telemetry.jsonl"):
        telemetry[(int(record["row_index"]), int(record.get("round_index", 0)))].append(
            record
        )

    opportunities: list[dict[str, Any]] = []
    hgraph_mismatches: list[dict[str, Any]] = []
    row_round = defaultdict(int)
    for hgraph in _jsonl(run_dir / "v3_pre_m8_hypothesis_graphs.jsonl"):
        row_index = int(hgraph.get("row_index", -1))
        row_round[row_index] += 1
        round_index = row_round[row_index]
        relation = str(hgraph.get("Relation", ""))
        subject = str(hgraph.get("SubjectEntity", ""))
        primary = _active_primary(hgraph)
        primary_summary = {
            "normalized_value": primary.get("normalized_value", "") if primary else "",
            "display": primary.get("display", "") if primary else "",
            "status": primary.get("status", "") if primary else "",
            "listing_disambiguation": (
                primary.get("listing_disambiguation", "") if primary else ""
            ),
            "semantic_qualifier": primary.get("semantic_qualifier", "") if primary else "",
            "independent_support_count": (
                primary.get("independent_support_count", 0) if primary else 0
            ),
            "raw_support_count": primary.get("raw_support_count", 0) if primary else 0,
        }
        same_round = telemetry.get((row_index, round_index), [])
        selected = [record for record in same_round if record.get("executed")]
        info = inference.get(row_index, {})
        cap = caps.get(relation, {"max_calls": None, "max_generated_tokens": None})
        calls_used = info.get("calls_used")
        tokens_used = info.get("generated_tokens_used")
        calls_left = (
            None if calls_used is None or cap["max_calls"] is None
            else max(0, int(cap["max_calls"]) - int(calls_used))
        )
        tokens_left = (
            None if tokens_used is None or cap["max_generated_tokens"] is None
            else max(0, int(cap["max_generated_tokens"]) - int(tokens_used))
        )

        for family in _target_catalogue_families(hgraph):
            cost = PLANNED_COSTS[family]
            family_records = [
                record for record in same_round
                if record.get("action_family") == family
            ]
            executed = any(record.get("executed") for record in family_records)
            selected_family = (
                selected[0].get("action_family", "") if selected else ""
            )
            selectable: bool | None = None
            classification = "TELEMETRY_INSUFFICIENT_TO_DETERMINE"
            reason = ""
            missing_fields: list[str] = []
            if executed:
                selectable = True
                classification = "EXECUTED_OBSERVATION"
            elif calls_left is None:
                missing_fields.append("inference_telemetry.calls_used")
            elif calls_left < cost["physical_calls"]:
                selectable = False
                classification = "LEGAL_EXECUTABLE_BUT_UNAFFORDABLE"
                reason = (
                    f"planned_calls={cost['physical_calls']} "
                    f"calls_left={calls_left}"
                )
            elif tokens_left is None:
                missing_fields.append("inference_telemetry.generated_tokens_used")
            elif tokens_left < cost["generated_tokens"]:
                selectable = False
                classification = "LEGAL_EXECUTABLE_BUT_UNAFFORDABLE"
                reason = (
                    f"planned_generated_tokens={cost['generated_tokens']} "
                    f"tokens_left={tokens_left}"
                )
            elif selected_family and selected_family != family:
                selectable = True
                classification = "LEGAL_SELECTABLE_BUT_SELECTOR_STARVED"
                reason = f"selected {selected_family}"
            else:
                missing_fields.extend([
                    "selectable_catalogue",
                    "selection_block_reason",
                ])

            opportunities.append({
                "row_index": row_index,
                "round_index": round_index,
                "query_identity": f"{row_index}:{relation}:{subject}",
                "relation": relation,
                "subject": subject,
                "action_family": family,
                "failure_state": hgraph.get("failure_state", ""),
                "program_type": info.get("program_type", ""),
                "current_primary_hypothesis": primary_summary,
                "hypothesis_count": len(hgraph.get("hypotheses", ()) or ()),
                "active_hypothesis_count": len([
                    item for item in hgraph.get("hypotheses", ())
                    if item.get("status") != "DROPPED"
                ]),
                "candidate_state": {
                    "final_failure_state": info.get("failure_state", ""),
                    "stopped_reason": info.get("stopped_reason", ""),
                    "candidate_count": len(info.get("candidates", ()) or ()),
                },
                "full_legal_catalogue": list(hgraph.get("legal_action_families") or ()),
                "executable_catalogue": list(_target_catalogue_families(hgraph)),
                "affordable_catalogue": [
                    family if selectable else ""
                ] if selectable else [],
                "remaining_physical_call_budget": calls_left,
                "remaining_generated_token_budget": tokens_left,
                "planned_cost": cost,
                "action_history": (
                    hgraph.get("action_family_mapping", {}).get("history", [])
                ),
                "actions_already_executed_for_query": [
                    record.get("action_family", "")
                    for (idx, _round), records in telemetry.items()
                    if idx == row_index
                    for record in records
                    if record.get("executed")
                ],
                "coverage_deficit_at_analysis_time": None,
                "actual_selected_action": selected_family,
                "state_transition_after_selected_action": (
                    selected[0].get("post_state") if selected else None
                ),
                "target_survived_into_next_round": None,
                "selectable": selectable,
                "executed": executed,
                "classification": classification,
                "why_not_executed": reason,
                "missing_runtime_fields": missing_fields,
            })

        for family in _hgraph_only_mismatches(hgraph):
            hgraph_mismatches.append({
                "row_index": row_index,
                "round_index": round_index,
                "relation": relation,
                "subject": subject,
                "action_family": family,
                "failure_state": hgraph.get("failure_state", ""),
                "classification": "LEGALITY_EXECUTABILITY_MISMATCH",
                "why": (
                    "pre-M8 legal_action_families advertised SEMANTIC_VERIFY "
                    "but every hypothesis was DROPPED, so the M17 owner could "
                    "not build a primary-target request"
                ),
            })

    counts = {
        family: {
            "classifications": dict(Counter(
                item["classification"]
                for item in opportunities
                if item["action_family"] == family
            )),
            "legal_opportunities": sum(
                1 for item in opportunities if item["action_family"] == family
            ),
            "selectable_opportunities": sum(
                1 for item in opportunities
                if item["action_family"] == family and item["selectable"]
            ),
            "executed": sum(
                1 for item in opportunities
                if item["action_family"] == family and item["executed"]
            ),
            "hgraph_legality_mismatches": sum(
                1 for item in hgraph_mismatches if item["action_family"] == family
            ),
        }
        for family in TARGET_FAMILIES
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_dir": str(collection_dir),
        "run_dir": str(run_dir),
        "manifest_identity": manifest.get("identity", {}),
        "manifest_status": manifest.get("status", ""),
        "coverage_summary": {
            entry["action_family"]: entry
            for entry in coverage.get("families", ())
            if entry.get("action_family") in TARGET_FAMILIES
        },
        "target_families": list(TARGET_FAMILIES),
        "counts_by_family": counts,
        "opportunities": opportunities,
        "hgraph_only_legality_mismatches": hgraph_mismatches,
        "missing_runtime_fields": sorted({
            field
            for item in opportunities
            for field in item.get("missing_runtime_fields", ())
        }),
    }


def write_outputs(payload: Mapping[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "v3_action_reachability.json"
    csv_path = out_dir / "v3_action_reachability.csv"
    md_path = out_dir / "v3_action_reachability.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fieldnames = (
        "row_index",
        "round_index",
        "relation",
        "subject",
        "action_family",
        "failure_state",
        "hypothesis_count",
        "active_hypothesis_count",
        "remaining_physical_call_budget",
        "remaining_generated_token_budget",
        "selectable",
        "executed",
        "classification",
        "why_not_executed",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in payload.get("opportunities", ()):
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    lines = [
        "# V3 Action Reachability",
        "",
        f"Collection: `{payload['collection_dir']}`",
        f"Run: `{payload['run_dir']}`",
        "",
        "| action_family | legal | selectable | executed | classifications |",
        "|---|---:|---:|---:|---|",
    ]
    for family, counts in payload["counts_by_family"].items():
        lines.append(
            f"| {family} | {counts['legal_opportunities']} | "
            f"{counts['selectable_opportunities']} | {counts['executed']} | "
            f"{json.dumps(counts['classifications'], sort_keys=True)} |"
        )
    lines.extend([
        "",
        "## HGraph-Only Legality Mismatches",
        "",
        f"Count: {len(payload.get('hgraph_only_legality_mismatches', []))}",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    payload = analyze(args.collection_dir, config_path=args.config)
    out_dir = args.output_dir or (args.collection_dir / "reachability_analysis")
    write_outputs(payload, out_dir)
    print(f"reachability analysis: {out_dir / 'v3_action_reachability.json'}")
    for family, counts in payload["counts_by_family"].items():
        print(
            f"{family}: legal={counts['legal_opportunities']} "
            f"selectable={counts['selectable_opportunities']} "
            f"executed={counts['executed']} "
            f"classifications={counts['classifications']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
