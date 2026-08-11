#!/usr/bin/env python3
"""Reconstruct the control flow of every PendingActionNotConsumed failure.

CPU only, read only. Joins one failed TEST run's artifacts by query identity and
reports, per failed query, exactly where the orchestration stopped and why
finalization was reached with executable work still pending.

The failed run is immutable evidence: this script never writes into the run
directory it reads.

Usage::

    python scripts/analyze_pending_action_incident.py \
      --run-dir outputs/v3_test_16f60fb1_20260810T160048Z/run \
      --canonical benchmark/data/test.jsonl \
      --output-dir outputs/v3_test_pending_action_forensics
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA_VERSION = "v3-pending-action-incident-v1"

#: The exception text the incident is defined by.
_ERROR_SIGNATURE = re.compile(
    r"^(?P<kind>\w+): (?P<subject>.*?)/(?P<relation>\w+): the controller selected "
    r"(?P<action>\w+) needing the (?P<role>\w+) role and (?P<remaining>\d+) calls remain"
)


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


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (row.get("SubjectEntity", ""), row.get("Relation", ""))


def index_by_identity(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        out.setdefault(identity(row), []).append(dict(row))
    return out


def parse_error(text: str) -> dict[str, str]:
    match = _ERROR_SIGNATURE.match(text or "")
    if not match:
        return {"kind": (text or "").split(":")[0], "action": "", "role": "",
                "remaining_calls": ""}
    groups = match.groupdict()
    return {
        "kind": groups["kind"],
        "action": groups["action"],
        "role": groups["role"],
        "remaining_calls": groups["remaining"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--canonical", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    errors_path = run_dir / "errors.json"
    predictions_path = run_dir / "predictions.jsonl"
    if not errors_path.exists() or not predictions_path.exists():
        parser.error(f"{run_dir} is missing errors.json or predictions.jsonl")

    errors = json.loads(errors_path.read_text())
    predictions = read_jsonl(predictions_path)
    canonical = read_jsonl(args.canonical)
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (
        run_dir / "manifest.json").exists() else {}

    trace = index_by_identity(read_jsonl(run_dir / "trace.jsonl"))
    planner = index_by_identity(read_jsonl(run_dir / "micro_planner.jsonl"))
    budget = index_by_identity(read_jsonl(run_dir / "relation_budget.jsonl"))
    hypothesis = index_by_identity(read_jsonl(run_dir / "v3_hypothesis_graph.jsonl"))

    # calls.jsonl keys the query differently (nested under "query").
    calls_by_identity: dict[tuple[str, str], list[dict]] = {}
    for call in read_jsonl(run_dir / "calls.jsonl"):
        query = call.get("query") or {}
        key = (query.get("subject", ""), query.get("relation", ""))
        calls_by_identity.setdefault(key, []).append(call)

    predictions_by_identity = {identity(row): row for row in predictions}
    canonical_order = [identity(row) for row in canonical]
    canonical_index = {key: i for i, key in enumerate(canonical_order)}

    rows: list[dict[str, Any]] = []
    for entry in errors:
        key = (entry["SubjectEntity"], entry["Relation"])
        parsed = parse_error(entry.get("error", ""))
        prediction = predictions_by_identity.get(key, {})
        trace_rows = trace.get(key, [])
        planner_rows = planner.get(key, [])
        budget_rows = budget.get(key, [])
        calls = calls_by_identity.get(key, [])
        graphs = hypothesis.get(key, [])

        last_plan = planner_rows[-1] if planner_rows else {}
        selected = last_plan.get("selected_action") or {}
        if isinstance(selected, str):
            selected = {"action_family": selected}
        last_budget = budget_rows[-1] if budget_rows else {}
        ledger = last_budget.get("ledger") or {}
        trace_row = trace_rows[-1] if trace_rows else {}

        enumerator_calls = sum(1 for c in calls if c.get("model_role") == "enumerator")
        verifier_calls = sum(1 for c in calls if c.get("model_role") == "verifier")

        # Derive dispatch rather than assert it. The pended action names a view
        # and a facet; if the owner had been dispatched there would be a
        # generation record carrying them. Absence of such a record is the
        # evidence that dispatch never happened.
        pending_view = str(selected.get("view_id") or selected.get("view") or "")
        pending_facet = str(selected.get("facet_id") or selected.get("facet") or "")
        dispatch_records = [
            call for call in calls
            if (pending_view and call.get("view_id") == pending_view)
            or (pending_facet and call.get("facet_id") == pending_facet)
        ] if (pending_view or pending_facet) else []
        # A generation record produced by the pended action would also have to
        # postdate every recorded call for the query, since it was selected last.
        generation_happened = bool(dispatch_records) and any(
            call.get("raw_output") for call in dispatch_records)
        # Evidence mutation is observable as parsed values reaching the graph.
        evidence_mutation = bool(dispatch_records) and any(
            call.get("parsed_values") for call in dispatch_records)

        rows.append({
            "row_index": trace_row.get("row_index", canonical_index.get(key, -1)),
            "canonical_index": canonical_index.get(key, -1),
            "SubjectEntity": key[0],
            "Relation": key[1],
            "error_kind": parsed["kind"],
            "selected_action_from_error": parsed["action"],
            "selected_action_owner": parsed["role"],
            "remaining_calls_at_failure": parsed["remaining_calls"],
            "m21_selected_action": json.dumps(selected, ensure_ascii=False, sort_keys=True),
            "m21_decision_kind": last_plan.get("decision_kind", ""),
            "m21_stop_reason": last_plan.get("stop_reason", ""),
            "m21_rounds": len(planner_rows),
            "m20_ledger": json.dumps(ledger, ensure_ascii=False, sort_keys=True),
            "calls_used_reported": trace_row.get("calls_used", ""),
            "enumerator_calls_recorded": enumerator_calls,
            "verifier_calls_recorded": verifier_calls,
            "total_calls_recorded": len(calls),
            "action_request_created": bool(selected),
            "pending_view_id": pending_view,
            "pending_facet_id": pending_facet,
            "owner_dispatch_happened": bool(dispatch_records),
            "generation_happened": generation_happened,
            "evidence_mutation_happened": evidence_mutation,
            "hypothesis_graph_rows": len(graphs),
            "stopped_reason": trace_row.get("stopped_reason", ""),
            "empty_reason": trace_row.get("empty_reason", ""),
            "prediction": json.dumps(prediction.get("ObjectEntities", []), ensure_ascii=False),
            "prediction_empty": not prediction.get("ObjectEntities"),
            "candidates_in_trace": len(trace_row.get("candidates") or []),
            "outer_loop_exit_reason": (
                "verify phase pended an enumerator-role action and broke; "
                "interleaved run_query has no resume() step"
            ),
            "finalization_attempted_because": (
                "run_query calls decide_graph unconditionally after verify_graph"
            ),
            "exception": entry.get("error", ""),
        })

    rows.sort(key=lambda r: r["canonical_index"])

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv(out_dir / "failed_rows.csv", list(rows[0].keys()) if rows else ["row_index"], rows)
    with (out_dir / "failed_rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    error_ids = {(e["SubjectEntity"], e["Relation"]) for e in errors}
    empty_ids = {
        identity(p) for p in predictions if not p.get("ObjectEntities")
    }
    stock_empty_ids = {k for k in empty_ids if k[1] == "companyTradesAtStockExchange"}

    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "source_commit": manifest.get("cover_kbc_version") or manifest.get("run_id", ""),
        "execution_mode": ((manifest.get("config") or {}).get("pipeline") or {}).get("mode", ""),
        "predictions_sha256": sha256_file(predictions_path),
        "errors_sha256": sha256_file(errors_path),
        "canonical_sha256": sha256_file(args.canonical),
        "canonical_rows": len(canonical),
        "prediction_rows": len(predictions),
        "errors": len(errors),
        "error_kinds": dict(Counter(parse_error(e.get("error", ""))["kind"] for e in errors)),
        "error_relations": dict(Counter(e["Relation"] for e in errors)),
        "error_signatures": {
            f"{action}|{role}|{remaining}": count
            for (action, role, remaining), count in Counter(
                (parse_error(e.get("error", ""))["action"],
                 parse_error(e.get("error", ""))["role"],
                 parse_error(e.get("error", ""))["remaining_calls"])
                for e in errors
            ).items()
        },
        "error_rows_with_empty_prediction": sum(
            1 for k in error_ids if not predictions_by_identity.get(k, {}).get("ObjectEntities")),
        "error_rows_with_nonempty_prediction": sum(
            1 for k in error_ids if predictions_by_identity.get(k, {}).get("ObjectEntities")),
        "empty_predictions_by_relation": dict(Counter(k[1] for k in empty_ids)),
        "stock_empty_rows": len(stock_empty_ids),
        "error_ids_equal_stock_empty_ids": error_ids == stock_empty_ids,
        "stock_empty_not_in_errors": sorted(
            f"{s}|{r}" for s, r in (stock_empty_ids - error_ids)),
        "prediction_order_is_canonical": (
            [identity(p) for p in predictions] == canonical_order),
        "enumerator_calls_on_failed_rows": sum(r["enumerator_calls_recorded"] for r in rows),
        "verifier_calls_on_failed_rows": sum(r["verifier_calls_recorded"] for r in rows),
        "rows_where_dispatch_happened": sum(1 for r in rows if r["owner_dispatch_happened"]),
        "root_cause": {
            "file": "src/cover_kbc/pipeline.py",
            "function": "ElicitationPipeline.verify_graph",
            "branch": (
                "the Phase-B controller is always given "
                "frozenset({ModelRole.VERIFIER, ModelRole.NONE}) as allowed_roles, "
                "regardless of execution mode"
            ),
            "consumer_gap": (
                "_controlled_phase pends any action whose model_role is not in "
                "allowed_roles; only ElicitationPipeline.resume consumes an "
                "enumerator-role pending action, and resume is called solely by "
                "scripts/run_staged.py, never by the interleaved run_query path"
            ),
            "why_one_remaining_call": (
                "decide_graph tolerates a pending action only when the budget is "
                "exhausted, in which case it records an explicit STOP and clears "
                "it. With calls still affordable the state is genuinely illegal, "
                "so the invariant fires. The remaining call is the condition for "
                "the crash, not its cause"
            ),
        },
    }
    (out_dir / "control_flow_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Pending-Action Orchestration Incident",
        "",
        f"Run: `{run_dir}`",
        f"Execution mode: `{summary['execution_mode']}`",
        "",
        f"- canonical TEST rows: `{summary['canonical_rows']}`",
        f"- prediction rows: `{summary['prediction_rows']}`",
        f"- errors: `{summary['errors']}`",
        f"- error kinds: `{summary['error_kinds']}`",
        f"- error relations: `{summary['error_relations']}`",
        f"- error signatures: `{summary['error_signatures']}`",
        f"- error rows with empty prediction: `{summary['error_rows_with_empty_prediction']}`",
        f"- error rows with non-empty prediction: "
        f"`{summary['error_rows_with_nonempty_prediction']}`",
        f"- stock empty rows: `{summary['stock_empty_rows']}`",
        f"- error identities == empty stock identities: "
        f"`{summary['error_ids_equal_stock_empty_ids']}`",
        f"- enumerator calls recorded on failed rows: "
        f"`{summary['enumerator_calls_on_failed_rows']}`",
        f"- rows where owner dispatch happened: `{summary['rows_where_dispatch_happened']}`",
        "",
        "## Root cause",
        "",
        f"`{summary['root_cause']['file']}` :: "
        f"`{summary['root_cause']['function']}`",
        "",
        summary["root_cause"]["branch"] + ".",
        "",
        summary["root_cause"]["consumer_gap"] + ".",
        "",
        "### Why one remaining call",
        "",
        summary["root_cause"]["why_one_remaining_call"] + ".",
        "",
        "## Failed identities, in canonical TEST order",
        "",
        "| # | SubjectEntity | selected | owner | remaining | prediction |",
        "|---:|---|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['canonical_index']} | {row['SubjectEntity']} | "
            f"{row['selected_action_from_error']} | {row['selected_action_owner']} | "
            f"{row['remaining_calls_at_failure']} | `{row['prediction']}` |"
        )
    (out_dir / "incident_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    checksums = sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(f"failed queries: {len(rows)}")
    print(f"error kinds: {summary['error_kinds']}")
    print(f"error signatures: {summary['error_signatures']}")
    print(f"error ids == empty stock ids: {summary['error_ids_equal_stock_empty_ids']}")
    print(f"enumerator calls on failed rows: {summary['enumerator_calls_on_failed_rows']}")
    print(f"rows where dispatch happened: {summary['rows_where_dispatch_happened']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
