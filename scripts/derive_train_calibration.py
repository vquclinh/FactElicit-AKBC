#!/usr/bin/env python3
"""Derive the Module 20 and Module 21 calibration package from TRAIN. **Offline.**

The collection ran the frozen models once and recorded what every action did.
This turns that record into the two artifacts Layer 6 refuses to start without,
and it is the only place TRAIN gold is ever read.

Nothing here loads a model. There is no runtime, no tokenizer and no weight
path in this file's import graph, and the derivation it calls is arithmetic
over JSONL. That is the whole reason the milestone can be run on a laptop after
an A100 session has ended.

It also fails closed, loudly, on every way the inputs could fail to be the run
they claim to be: a config that is not the TRAIN collection profile, a split
that is not TRAIN, a row count that is not 477, a hash that does not match the
manifest, a telemetry schema this build does not implement, a query named twice,
an action with no gold row, a non-finite number, an unknown relation or action
family, or telemetry the sufficiency gate would not have accepted. A calibration
derived from the wrong inputs is worse than no calibration, because it looks
like one.

Outputs, into ``--output-dir``:

``m20_relation_budget.json``       production artifact, loaded by Module 20
``m21_historical_bins.json``       production artifact, loaded by Module 21
``m21_planner_calibration.json``   production artifact, §17's coefficients
``derivation_report.json``         diagnostics: metrics, spend, replay, support
``derivation_report.md``           the same, readable

Only the first three are production artifacts. The report carries the gold-side
diagnostics and is **not** loaded at inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

import yaml

from cover_kbc.control.planner_types import ActionFamily
from cover_kbc.controller_calibration.derivation import (
    BINNING_SPEC_VERSION,
    DERIVATION_SCHEMA_VERSION,
    M20_DERIVATION_VERSION,
    M21_DERIVATION_VERSION,
    CalibrationBundle,
    CalibrationProvenance,
    DerivationError,
    DerivationSettings,
    DirtyDerivationSource,
    derive_binning_spec,
    derive_m20,
    derive_m21,
    derive_planner_calibration,
    observe_relation_spend,
    offline_state_bin_key,
    require_supported_schema,
    resolve_derivation_source,
)
from cover_kbc.controller_calibration.gold_join import (
    GoldJoinError,
    load_gold,
    score_actions,
)
from cover_kbc.controller_calibration.sufficiency import evaluate_sufficiency
from cover_kbc.controller_calibration.telemetry import read_telemetry
from cover_kbc.evaluation.harness import evaluate_files
from cover_kbc.integration_mode import CALIBRATION_SPLIT

#: Rows the official TRAIN split must contain, restated from the collection
#: runner so a derivation cannot silently run against a slice.
EXPECTED_TRAIN_ROWS = 477

#: Splits that must never reach this script. TRAIN is the only legal input, and
#: a file whose identity says otherwise is refused before it is parsed.
FORBIDDEN_SPLITS = ("val", "validation", "test")


class CalibrationDerivationError(RuntimeError):
    """The derivation could not be trusted and was refused."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CalibrationDerivationError(message)


def _reject_non_train(path: Path, label: str) -> None:
    """Refuse an input whose own name says it is not TRAIN.

    Only a heuristic - a VAL file renamed to ``train.jsonl`` is caught by the
    hash and row-count checks instead, not by this. It exists because the
    cheapest mistake to make is passing the wrong path, and the cheapest place
    to catch it is before anything is read.
    """
    parts = {part.casefold() for part in path.parts}
    stem = path.stem.casefold()
    for forbidden in FORBIDDEN_SPLITS:
        if forbidden in parts or stem == forbidden or stem.endswith(f"_{forbidden}"):
            raise CalibrationDerivationError(
                f"{label}: {path} looks like the {forbidden!r} split; calibration "
                f"may only read {CALIBRATION_SPLIT!r}"
            )


def check_manifest(manifest: dict, *, telemetry: Path, predictions: Path,
                   train: Path, config_sha: str) -> None:
    """Bind the derivation to the collection that produced its inputs.

    The manifest is the collection's own account of what it ran. If the files
    handed to this script do not hash to what it recorded, they are not that
    run's artifacts and the numbers derived from them would describe a system
    nobody audited.
    """
    identity = manifest.get("identity") or {}
    _require(bool(identity), "manifest carries no run identity")
    # Provenance that cannot name the code the collection ran binds the
    # artifact to nothing, so an absent or placeholder SHA is refused rather
    # than written into the package as "unknown".
    collection_sha = str(identity.get("repo_sha", ""))
    _require(bool(collection_sha) and collection_sha != "unknown",
             "manifest records no collection repo SHA; the calibration could "
             "not be bound to the code that produced its telemetry")

    train_sha = _sha256(train)
    _require(
        identity.get("train_sha256") == train_sha,
        f"TRAIN hash mismatch: manifest {identity.get('train_sha256')!r}, "
        f"file {train_sha!r}")
    _require(
        int(identity.get("total_rows", -1)) == EXPECTED_TRAIN_ROWS,
        f"manifest records {identity.get('total_rows')} rows, expected "
        f"{EXPECTED_TRAIN_ROWS}")
    _require(
        identity.get("config_sha256") == config_sha,
        "experiment config hash does not match the one the collection ran")
    _require(
        manifest.get("integration_mode") == "train_calibration_collection_only",
        f"manifest integration_mode is {manifest.get('integration_mode')!r}; "
        "only a TRAIN calibration collection may be calibrated from")

    # The collection's own exit gate must have passed. Deriving from a run that
    # refused to call itself complete would calibrate against a partial corpus.
    _require(not manifest.get("gate_blockers"),
             f"the collection did not pass its own exit gate: "
             f"{manifest.get('gate_blockers')}")
    _require(manifest.get("status") == "complete",
             f"collection status is {manifest.get('status')!r}, not 'complete'")
    _require(int(manifest.get("rows_completed", 0)) == EXPECTED_TRAIN_ROWS,
             f"collection completed {manifest.get('rows_completed')} rows")
    _require(not manifest.get("unresolved_failed_rows"),
             f"collection has unresolved failed rows: "
             f"{manifest.get('unresolved_failed_rows')}")
    sufficiency = manifest.get("sufficiency") or {}
    _require(bool(sufficiency.get("ok")),
             f"collection sufficiency did not pass: {sufficiency.get('blockers')}")

    # Hashes the manifest may carry for its own artifacts, when it recorded them.
    for label, path, field in (
        ("telemetry", telemetry, "telemetry_sha256"),
        ("predictions", predictions, "predictions_sha256"),
    ):
        recorded = manifest.get(field) or (manifest.get("artifacts") or {}).get(field)
        if recorded:
            actual = _sha256(path)
            _require(recorded == actual,
                     f"{label} hash mismatch: manifest {recorded!r}, file {actual!r}")


def build_report(
    *, bundle: CalibrationBundle, spend: dict, m21_diagnostics: dict,
    planner_diagnostics: dict, metrics: Any, effects: dict, records: list,
    binning: Any, settings: DerivationSettings,
) -> dict[str, Any]:
    """Everything the audit needs and production must never load."""
    executed = [r for r in records if r.executed]
    per_relation: dict[str, dict[str, Any]] = {}
    for record in executed:
        slot = per_relation.setdefault(record.relation, {
            "executed_actions": 0, "verified_gain": 0.0,
            "false_positive_supported": 0, "true_positive_supported": 0,
            "named_correct": 0, "contradicted_correct": 0,
            "physical_calls": 0, "by_family": {},
        })
        effect = effects[record.operation_id]
        slot["executed_actions"] += 1
        slot["verified_gain"] += effect.verified_gain
        slot["true_positive_supported"] += effect.supported_correct
        slot["false_positive_supported"] += effect.supported_incorrect
        slot["named_correct"] += effect.named_correct
        slot["contradicted_correct"] += effect.contradicted_correct
        slot["physical_calls"] += record.outcome.physical_calls
        family = slot["by_family"].setdefault(record.action_family, {
            "executed": 0, "verified_gain": 0.0, "calls": 0})
        family["executed"] += 1
        family["verified_gain"] += effect.verified_gain
        family["calls"] += record.outcome.physical_calls

    state_bins: dict[str, int] = {}
    for record in executed:
        key = offline_state_bin_key(
            record.pre_state, program_type=record.program_type,
            relation=record.relation, binning=binning)
        state_bins[key] = state_bins.get(key, 0) + 1

    return {
        "report": "train-calibration-derivation",
        "derivation_schema_version": DERIVATION_SCHEMA_VERSION,
        "provenance": bundle.provenance.to_json(),
        "pre_calibration_official_metrics": {
            "macro": metrics.macro, "micro": metrics.micro,
            "stats": metrics.stats, "tolerance": metrics.tolerance,
            "evaluator_sha256": metrics.evaluator_sha256,
            "num_gt_rows": metrics.num_gt_rows,
            "num_pred_rows": metrics.num_pred_rows,
        },
        "relation_spend": {name: s.to_json() for name, s in sorted(spend.items())},
        "action_level_statistics": {
            name: {
                **{k: (round(v, 6) if isinstance(v, float) else v)
                   for k, v in slot.items() if k != "by_family"},
                "by_family": {
                    fam: {**f, "verified_gain": round(f["verified_gain"], 6)}
                    for fam, f in sorted(slot["by_family"].items())
                },
            }
            for name, slot in sorted(per_relation.items())
        },
        "state_bin_support": dict(sorted(state_bins.items())),
        "m21_diagnostics": m21_diagnostics,
        "planner_diagnostics": planner_diagnostics,
        "derivation_settings": settings.to_json(),
        "m20": {name: bundle.budgets[name].to_json()
                for name in sorted(bundle.budgets)},
        "m21_planner": bundle.planner.to_json(),
        "m21_bin_count": len(bundle.history.bins),
    }


def render_report(report: dict[str, Any]) -> str:
    lines = ["# TRAIN calibration derivation report", ""]
    lines.append("## Pre-calibration official TRAIN metrics")
    lines.append("")
    lines.append("| relation | macro-P | macro-R | macro-F1 |")
    lines.append("|---|---|---|---|")
    for name, scores in sorted(
            report["pre_calibration_official_metrics"]["macro"].items()):
        lines.append(
            f"| {name} | {scores.get('p', 0):.3f} | {scores.get('r', 0):.3f} "
            f"| {scores.get('f1', 0):.3f} |")
    lines += ["", "## Module 20 derived budgets", "",
              "`hard_calls` and `hard_generated_tokens` are whole-query "
              "ceilings; the class caps and reserves describe Layer-4 spend, "
              "which is the only spend Module 20's ledger meters.", "",
              "| relation | hard_calls | gen_tokens | discovery | verification "
              "| reserve | special |", "|---|---|---|---|---|---|---|"]
    for name, entry in sorted(report["m20"].items()):
        reserves = ", ".join(
            f"{item['purpose']}={item['calls']}"
            for item in sorted(entry.get("special_reserves") or (),
                               key=lambda i: i["purpose"])
        ) or "-"
        lines.append(
            f"| {name} | {entry['hard_calls']} | {entry['hard_generated_tokens']} "
            f"| {entry['discovery_cap']} | {entry['verification_cap']} "
            f"| {entry['verification_reserve']} | {reserves} |")
    planner = report["m21_planner"]
    lines += ["", "## Module 21 coefficients (§17)", "",
              f"- alpha {planner['alpha']}  beta {planner['beta']}  "
              f"gamma {planner['gamma']}",
              f"- delta {planner['delta']}  eta {planner['eta']}  "
              f"kappa {planner['kappa']}",
              f"- tau_continue {planner['tau_continue']} (strict `U > tau`)",
              f"- lookahead_depth {planner['lookahead_depth']}",
              "", "## C-02: does ΔH move?", ""]
    diag = report["m21_diagnostics"]
    lines.append(
        f"- {diag['delta_h_non_zero']} of {diag['delta_h_observations']} executed "
        f"actions moved H; structurally zero = "
        f"{diag['delta_h_is_structurally_zero']}")
    if diag["delta_h_is_structurally_zero"]:
        lines.append(
            "- gamma is therefore inert: H is a function of acquisition-group "
            "coverage, and no Module 17/18 action changes an acquisition group. "
            "Recorded as measured, not manufactured.")
    lines += ["", f"- bins kept {diag['exact_bins_kept']}, "
              f"dropped for sparsity {diag['exact_bins_dropped_for_sparsity']}, "
              f"fallback {diag['fallback_bins']}",
              f"- observed transitions {diag['observed_transitions']}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path,
                        help="the TRAIN collection experiment YAML")
    parser.add_argument("--train-gold", required=True, type=Path,
                        help="benchmark/data/train.jsonl")
    parser.add_argument("--predictions", required=True, type=Path,
                        help="the collection's predictions.jsonl")
    parser.add_argument("--telemetry", required=True, type=Path,
                        help="the collection's train_telemetry.jsonl")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="the collection's manifest.json")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--budget-quantile", type=float, default=None,
                        help="override the ceiling quantile (default 0.95)")
    parser.add_argument("--minimum-bin-support", type=int, default=None,
                        help="override the minimum bin support (default 8)")
    parser.add_argument("--minimum-denominator", type=float, default=None,
                        help="smallest total ΔR/ΔH movement that can support a "
                             "rate (default 1.0, one full unit of the "
                             "observable); raising it only refuses more")
    # The collection manifest does not hash its own sibling artifacts, so these
    # let the operator assert the hashes recorded when the run was preserved.
    # Optional, and checked exactly when supplied: a derivation that silently
    # ignored a wrong hash would be worse than one that never asked.
    parser.add_argument("--expect-telemetry-sha256", default=None,
                        help="refuse unless the telemetry hashes to this")
    parser.add_argument("--expect-predictions-sha256", default=None,
                        help="refuse unless the predictions hash to this")
    parser.add_argument("--expect-manifest-sha256", default=None,
                        help="refuse unless the manifest hashes to this")
    args = parser.parse_args()

    # First, before an input is even opened: this derivation must be able to
    # say which code produced it. A dirty checkout runs modified source while
    # HEAD still names a clean commit, so the artifact would carry a provenance
    # that is precisely wrong (Audit 0048 P1-1). There is no override.
    derivation_sha = resolve_derivation_source()
    print(f"derivation source  : {derivation_sha} (clean checkout)")

    for label, path in (("--config", args.config), ("--train-gold", args.train_gold),
                        ("--predictions", args.predictions),
                        ("--telemetry", args.telemetry),
                        ("--manifest", args.manifest)):
        _require(path.is_file(), f"{label}: no file at {path}")
    _reject_non_train(args.train_gold, "--train-gold")
    _reject_non_train(args.predictions, "--predictions")
    _reject_non_train(args.telemetry, "--telemetry")

    for label, path, expected in (
        ("telemetry", args.telemetry, args.expect_telemetry_sha256),
        ("predictions", args.predictions, args.expect_predictions_sha256),
        ("manifest", args.manifest, args.expect_manifest_sha256),
    ):
        if expected:
            actual = _sha256(path)
            _require(actual == expected,
                     f"{label} hash mismatch: expected {expected!r}, "
                     f"file {actual!r}")

    config = yaml.safe_load(args.config.read_text()) or {}
    split = str((config.get("experiment") or {}).get("split", ""))
    _require(split == CALIBRATION_SPLIT,
             f"--config declares split {split!r}; calibration may only be "
             f"derived from {CALIBRATION_SPLIT!r}")
    config_sha = _config_sha(config)

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CalibrationDerivationError(f"--manifest: not valid JSON ({error})")
    check_manifest(manifest, telemetry=args.telemetry,
                   predictions=args.predictions, train=args.train_gold,
                   config_sha=config_sha)

    settings = DerivationSettings(
        **{k: v for k, v in (
            ("budget_quantile", args.budget_quantile),
            ("minimum_bin_support", args.minimum_bin_support),
            ("minimum_denominator", args.minimum_denominator),
        ) if v is not None}
    )

    print(f"reading telemetry  : {args.telemetry}")
    records = list(read_telemetry(args.telemetry))
    schema = require_supported_schema(records)
    executed = [r for r in records if r.executed]
    _require(bool(executed), "telemetry records no executed action")

    # The same gate the collection had to pass, re-run over what is on disk:
    # the manifest's claim is the collection's, this is ours.
    sufficiency = evaluate_sufficiency(records)
    _require(sufficiency.ok,
             f"telemetry is not calibration-sufficient: {sufficiency.blockers}")

    for record in records:
        try:
            ActionFamily(record.action_family)
        except ValueError:
            raise CalibrationDerivationError(
                f"{record.operation_id}: unknown action family "
                f"{record.action_family!r}")

    print(f"reading TRAIN gold : {args.train_gold}")
    gold = load_gold(args.train_gold, expected_rows=EXPECTED_TRAIN_ROWS)
    print(f"  {len(gold)} rows, evaluator {gold.evaluator_sha256[:12]}…")

    print("joining gold to action outcomes (offline, no model call)")
    effects = score_actions(records, gold)

    print("scoring the collection's predictions with the pinned evaluator")
    metrics = evaluate_files(args.predictions, args.train_gold)

    print("deriving Module 20 budgets")
    spend = observe_relation_spend(records, effects)
    budgets = derive_m20(spend, settings)

    print("deriving Module 21 historical bins")
    binning = derive_binning_spec(records, settings)
    history, m21_diagnostics = derive_m21(records, effects, binning, settings)
    planner, planner_diagnostics = derive_planner_calibration(
        history, records, effects, settings=settings)


    provenance = CalibrationProvenance(
        collection_repo_sha=str((manifest.get("identity") or {}).get(
            "repo_sha", "")),
        derivation_repo_sha=derivation_sha,
        train_sha256=_sha256(args.train_gold),
        train_rows=len(gold),
        predictions_sha256=_sha256(args.predictions),
        telemetry_sha256=_sha256(args.telemetry),
        manifest_sha256=_sha256(args.manifest),
        experiment_config_sha256=config_sha,
        evaluator_sha256=gold.evaluator_sha256,
        telemetry_schema_version=schema,
        derivation_schema_version=DERIVATION_SCHEMA_VERSION,
        m20_derivation_version=M20_DERIVATION_VERSION,
        m21_derivation_version=M21_DERIVATION_VERSION,
        binning_spec_version=BINNING_SPEC_VERSION,
        relation_catalogue=gold.relations(),
        collection_policy_version=str((manifest.get("identity") or {}).get(
            "collection_policy_version", "")),
        settings=settings,
        support_counts={
            "executed_actions": len(executed),
            "considered_actions": len(records),
            "queries": len({(r.row_index, r.relation) for r in records}),
            "historical_bins": len(history.bins),
            "observed_transitions": m21_diagnostics["observed_transitions"],
        },
    )
    bundle = CalibrationBundle(provenance=provenance, budgets=budgets,
                               history=history, planner=planner)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, payload in (
        ("m20_relation_budget.json", bundle.m20_json()),
        ("m21_historical_bins.json", bundle.m21_history_json()),
        ("m21_planner_calibration.json", bundle.m21_planner_json()),
    ):
        target = args.output_dir / name
        # sort_keys so the bytes are a function of the content alone.
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n", encoding="utf-8")
        written.append(target)

    report = build_report(
        bundle=bundle, spend=spend, m21_diagnostics=m21_diagnostics,
        planner_diagnostics=planner_diagnostics, metrics=metrics,
        effects=effects, records=records, binning=binning, settings=settings)
    (args.output_dir / "derivation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (args.output_dir / "derivation_report.md").write_text(
        render_report(report), encoding="utf-8")

    print("\nTRAIN CALIBRATION DERIVED\n")
    for target in written:
        print(f"  production artifact : {target}")
    print(f"  report              : {args.output_dir / 'derivation_report.json'}")
    print(f"  report              : {args.output_dir / 'derivation_report.md'}")
    print(f"\n  relations           : {len(budgets)}")
    print(f"  historical bins     : {len(history.bins)}")
    print(f"  transitions         : {m21_diagnostics['observed_transitions']}")
    print(f"  ΔH ever non-zero    : "
          f"{not m21_diagnostics['delta_h_is_structurally_zero']}")
    print("\n  Production activation is a LATER milestone: no validation config "
          "references these artifacts yet.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DirtyDerivationSource as error:
        print(f"REFUSED (dirty derivation source): {error}", file=sys.stderr)
        sys.exit(2)
    except (CalibrationDerivationError, DerivationError, GoldJoinError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        sys.exit(2)
