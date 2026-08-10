"""CPU-only V3 calibration derivation from the merged TRAIN action corpus."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control.historical_bins import (
    POOLED_PROGRAM_FALLBACK,
    POOLED_RELATION_FALLBACK,
)
from cover_kbc.control.relation_budget import load_calibrations
from cover_kbc.controller_calibration.derivation import (
    BINNING_SPEC_VERSION,
    FALLBACK_STATE_BIN,
    FLOAT_PRECISION,
    M20_DERIVATION_VERSION,
    CalibrationBundle,
    CalibrationProvenance,
    DerivationError,
    DerivationSettings,
    assert_no_leakage,
    derive_binning_spec,
    derive_m21,
    parse_calibration_action_family,
    require_supported_schema,
    supports_depth_two,
)
from cover_kbc.controller_calibration.gold_join import load_gold, score_actions
from cover_kbc.controller_calibration.sufficiency import evaluate_sufficiency
from cover_kbc.controller_calibration.telemetry import read_telemetry
from cover_kbc.control.planner_types import EstimateSource, PlannerCalibration
from cover_kbc.paths import REPO_ROOT
from cover_kbc.v3_core.execution import V3_ACTION_EFFECT_VERSION
from cover_kbc.v3_core.relation_programs import relation_train_collection_actions


EXPECTED_MERGED_CORPUS_SHA256 = (
    "50be66dc88419f88ac811243244d534f0a8a160bdf2a48d02eb77295a58c33af"
)
EXPECTED_TRAIN_SHA256 = (
    "ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e"
)
EXPECTED_TRAIN_ROWS = 477
V3_DERIVATION_SCHEMA_VERSION = "v3-calibration-derivation-v1"
V3_M21_DERIVATION_VERSION = "m21-v3-derivation-v1"
V3_M20_METHOD = "inherited_frozen_v2_m20"
V3_M21_METHOD = (
    "v3_action_effect_utility_with_pooled_fallbacks_and_inert_unestimable_state_movement"
)
V3_FALLBACK_HIERARCHY = (
    "exact: relation/program_type/state_bin/family/target_class",
    "relation fallback: relation/program_type/__fallback__/family",
    "program pooled fallback: __any_relation__/program_type/__fallback__/family",
    "global family fallback: __any_relation__/__any_program__/__fallback__/family",
)
REQUIRED_MERGED_FILES = (
    "action_effects.jsonl",
    "train_telemetry.jsonl",
    "coverage.json",
    "coverage.csv",
    "manifest.json",
    "SHA256SUMS.txt",
)
PRODUCTION_ARTIFACTS = (
    "m20_relation_budget.json",
    "m21_historical_bins.json",
    "m21_planner_calibration.json",
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class V3CalibrationDerivationError(RuntimeError):
    """The V3 merged corpus or derived package failed a hard safety check."""


@dataclass(frozen=True)
class MergedV3Corpus:
    path: Path
    manifest: Mapping[str, Any]
    coverage: Mapping[str, Any]
    action_effects: tuple[Mapping[str, Any], ...]
    telemetry_path: Path
    action_effects_path: Path
    coverage_path: Path
    manifest_path: Path
    train_telemetry_sha256: str
    action_effects_sha256: str
    coverage_sha256: str
    manifest_sha256: str
    merged_corpus_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise V3CalibrationDerivationError(
            f"{path}: unreadable JSON: {error}") from error
    if not isinstance(payload, dict):
        raise V3CalibrationDerivationError(f"{path}: expected a JSON object")
    return payload


def _jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as error:
                    raise V3CalibrationDerivationError(
                        f"{path}:{number}: malformed JSONL: {error}") from None
                if not isinstance(payload, dict):
                    raise V3CalibrationDerivationError(
                        f"{path}:{number}: expected a JSON object")
                rows.append(payload)
    except OSError as error:
        raise V3CalibrationDerivationError(f"{path}: unreadable: {error}") from error
    return tuple(rows)


def resolve_merged_corpus_dir(path: str | Path) -> Path:
    """Resolve wrapper directories to the merged V3 calibration corpus."""
    root = Path(path)
    if all((root / name).is_file() for name in REQUIRED_MERGED_FILES):
        return root
    matches: list[Path] = []
    if root.exists():
        for manifest in root.rglob("manifest.json"):
            candidate = manifest.parent
            if all((candidate / name).is_file() for name in REQUIRED_MERGED_FILES):
                matches.append(candidate)
    if not matches:
        raise V3CalibrationDerivationError(
            f"{root}: no merged V3 calibration corpus with required files found")
    return sorted(matches, key=lambda item: str(item))[0]


def _verify_sha256s(corpus_dir: Path, sums_path: Path) -> None:
    recorded: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        recorded[name.strip()] = digest
    for name in REQUIRED_MERGED_FILES:
        if name == "SHA256SUMS.txt":
            continue
        path = corpus_dir / name
        expected = recorded.get(name)
        if expected and sha256_file(path) != expected:
            raise V3CalibrationDerivationError(
                f"{path}: SHA256SUMS records {expected}, file hashes to "
                f"{sha256_file(path)}")


def _effect_identity(effect: Mapping[str, Any]) -> str:
    explicit = str(effect.get("action_effect_id", ""))
    if explicit:
        return explicit
    action = dict(effect.get("action") or {})
    action_id = str(action.get("action_id", ""))
    row_index = str(action.get("row_index", ""))
    source = str(effect.get("source_tag", "base"))
    if not action_id:
        raise V3CalibrationDerivationError("action effect has no action_id")
    return f"{source}:{row_index}:{action_id}"


def _validate_action_effects(
    action_effects: Sequence[Mapping[str, Any]], records: Sequence[Any],
) -> None:
    identities: set[str] = set()
    remaining: dict[tuple[int, str], int] = {}
    for record in records:
        if not record.executed:
            continue
        key = (record.row_index, record.action_id)
        remaining[key] = remaining.get(key, 0) + 1
    for effect in action_effects:
        if str(effect.get("schema_version", "")) != V3_ACTION_EFFECT_VERSION:
            raise V3CalibrationDerivationError(
                "action effect schema mismatch: "
                f"{effect.get('schema_version')!r}")
        identity = _effect_identity(effect)
        if identity in identities:
            raise V3CalibrationDerivationError(
                f"duplicate action-effect identity {identity}")
        identities.add(identity)
        action = dict(effect.get("action") or {})
        if not str(action.get("owner", "")):
            raise V3CalibrationDerivationError(
                f"{identity}: action effect has no owner")
        family = str(action.get("family", ""))
        try:
            parse_calibration_action_family(family)
        except DerivationError as error:
            raise V3CalibrationDerivationError(
                f"{identity}: unknown action family {family!r}: {error}") from None
        key = (int(action.get("row_index", -1)), str(action.get("action_id", "")))
        count = remaining.get(key, 0)
        if count <= 0:
            raise V3CalibrationDerivationError(
                f"{identity}: no committed executed telemetry record matches "
                f"row/action {key}")
        remaining[key] = count - 1
    missing = {
        f"{row}:{action}": count for (row, action), count in sorted(remaining.items())
        if count
    }
    if missing:
        raise V3CalibrationDerivationError(
            f"committed telemetry is missing action effects: {missing}")


def _required_v3_families() -> tuple[str, ...]:
    families = {
        family.value
        for relation in CONTRACTS
        for family in relation_train_collection_actions(relation)
    }
    return tuple(sorted(families))


def _validate_coverage(coverage: Mapping[str, Any]) -> None:
    if not bool(coverage.get("integrity_ok", False)):
        raise V3CalibrationDerivationError("merged coverage integrity is false")
    by_family = {
        str(entry.get("action_family", "")): dict(entry)
        for entry in coverage.get("families", ())
    }
    good = {
        "OBSERVED_SUFFICIENT",
        "OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES",
    }
    for family in _required_v3_families():
        entry = by_family.get(family)
        if entry is None:
            raise V3CalibrationDerivationError(
                f"coverage is missing required V3 family {family}")
        if str(entry.get("status", "")) not in good:
            raise V3CalibrationDerivationError(
                f"{family}: coverage status {entry.get('status')!r} is not "
                "sufficient for calibration")
        if int(entry.get("successful", entry.get("succeeded", 0)) or 0) < int(
                entry.get("target", 0) or 0):
            raise V3CalibrationDerivationError(
                f"{family}: successful observations do not meet target")


def load_merged_v3_corpus(
    path: str | Path,
    *,
    expected_merged_corpus_sha256: str = EXPECTED_MERGED_CORPUS_SHA256,
) -> MergedV3Corpus:
    corpus_dir = resolve_merged_corpus_dir(path)
    _verify_sha256s(corpus_dir, corpus_dir / "SHA256SUMS.txt")
    manifest_path = corpus_dir / "manifest.json"
    coverage_path = corpus_dir / "coverage.json"
    telemetry_path = corpus_dir / "train_telemetry.jsonl"
    action_effects_path = corpus_dir / "action_effects.jsonl"
    manifest = _json(manifest_path)
    coverage = _json(coverage_path)
    action_effects = _jsonl(action_effects_path)
    if manifest.get("schema_version") != "merged-v3-calibration-corpus-v1":
        raise V3CalibrationDerivationError(
            f"merged corpus schema is {manifest.get('schema_version')!r}")
    if bool(manifest.get("calibration_derivation_blocked", True)):
        raise V3CalibrationDerivationError(
            "merged manifest says calibration_derivation_blocked=true")
    if not bool((manifest.get("sufficiency") or {}).get("ok", False)):
        raise V3CalibrationDerivationError(
            "merged manifest sufficiency did not pass")
    if str(manifest.get("train_sha256", "")) != EXPECTED_TRAIN_SHA256:
        raise V3CalibrationDerivationError(
            f"merged manifest train_sha256 is {manifest.get('train_sha256')!r}")
    _validate_coverage(coverage)
    corpus_sha = hashlib.sha256(
        action_effects_path.read_bytes() + telemetry_path.read_bytes()
    ).hexdigest()
    if corpus_sha != str(manifest.get("merged_corpus_sha256", "")):
        raise V3CalibrationDerivationError(
            f"merged corpus hash recomputes to {corpus_sha}, manifest records "
            f"{manifest.get('merged_corpus_sha256')!r}")
    if expected_merged_corpus_sha256 and corpus_sha != expected_merged_corpus_sha256:
        raise V3CalibrationDerivationError(
            f"merged corpus hash is {corpus_sha}, expected "
            f"{expected_merged_corpus_sha256}")
    return MergedV3Corpus(
        path=corpus_dir,
        manifest=manifest,
        coverage=coverage,
        action_effects=action_effects,
        telemetry_path=telemetry_path,
        action_effects_path=action_effects_path,
        coverage_path=coverage_path,
        manifest_path=manifest_path,
        train_telemetry_sha256=sha256_file(telemetry_path),
        action_effects_sha256=sha256_file(action_effects_path),
        coverage_sha256=sha256_file(coverage_path),
        manifest_sha256=sha256_file(manifest_path),
        merged_corpus_sha256=corpus_sha,
    )


def git_head(repo_root: Path = REPO_ROOT) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise V3CalibrationDerivationError(
            f"cannot resolve derivation source commit: {error}") from None
    if not _COMMIT_SHA.match(head):
        raise V3CalibrationDerivationError(
            f"HEAD resolved to {head!r}, not a 40-character commit SHA")
    return head


def _load_inherited_m20(path: Path) -> tuple[dict[str, Any], str, Mapping[str, Any]]:
    payload = _json(path)
    budgets = load_calibrations(payload)
    missing = sorted(set(CONTRACTS) - set(budgets))
    if missing:
        raise V3CalibrationDerivationError(
            f"inherited M20 artifact lacks relation budgets for {missing}")
    return payload, sha256_file(path), budgets


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _config_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _validate_fallback_regions(history: Any) -> tuple[str, ...]:
    missing: list[str] = []
    for relation, contract in sorted(CONTRACTS.items()):
        program_type = str(getattr(contract.program_type, "value",
                                   contract.program_type))
        for family in relation_train_collection_actions(relation):
            try:
                history.lookup(
                    relation=relation,
                    program_type=program_type,
                    state_bin_key="__unseen_readiness_state__",
                    family=family,
                )
            except Exception as error:                          # noqa: BLE001
                missing.append(f"{relation}/{program_type}/{family.value}: {error}")
    return tuple(missing)


def _round(value: float) -> float:
    out = round(float(value), FLOAT_PRECISION)
    return 0.0 if out == 0.0 else out


def derive_v3_planner_calibration(
    package: Any,
    records: Sequence[Any],
    effects: Mapping[str, Any],
    *,
    settings: DerivationSettings,
) -> tuple[PlannerCalibration, dict[str, Any]]:
    """Derive V3 planner coefficients without shipping unstable movement rates.

    The shared V2 estimator intentionally refuses a positive-but-tiny residual
    denominator because that would produce an explosive beta. The merged V3
    action corpus has directly observed action utility/cost/FP bins but does
    not supply enough residual/entropy movement to price those state-movement
    terms. For V3 only, those unestimable movement coefficients are therefore
    explicit inert terms in the emitted calibration and diagnostics.
    """
    executed = [record for record in records if record.executed]
    gain_total = sum(effects[record.operation_id].verified_gain
                     for record in executed if record.operation_id in effects)
    delta_r_total = sum(max(0.0, record.delta_residual or 0.0)
                        for record in executed)
    delta_h_total = sum(max(0.0, record.delta_entropy or 0.0)
                        for record in executed)
    call_total = sum(record.outcome.physical_calls for record in executed)
    floor = settings.minimum_denominator

    inert_terms: list[str] = []

    def rate(numerator: float, denominator: float, name: str) -> tuple[float, str]:
        if denominator == 0.0:
            return 0.0, "zero_denominator"
        if denominator < floor:
            if numerator != 0.0:
                inert_terms.append(name)
                return 0.0, "inert_unestimable_below_floor"
            return 0.0, "zero_gain_below_floor"
        return numerator / denominator, "estimated"

    beta, beta_status = rate(gain_total, delta_r_total, "beta")
    gamma, gamma_status = rate(gain_total, delta_h_total, "gamma")
    delta = (gain_total / call_total) if call_total > 0 else 0.0
    eta = (gain_total / len(executed)) if executed else 0.0
    depth_two, depth_reasons = supports_depth_two(package)
    planner = PlannerCalibration(
        calibration_version=V3_M21_DERIVATION_VERSION,
        source=EstimateSource.TRAIN_CALIBRATED,
        alpha=1.0,
        beta=_round(beta),
        gamma=_round(gamma),
        delta=_round(delta),
        eta=_round(eta),
        kappa=1.0,
        tau_continue=0.0,
        lookahead_depth=2 if depth_two else 1,
    )
    diagnostics = {
        "verified_gain_total": _round(gain_total),
        "delta_r_reduction_total": _round(delta_r_total),
        "delta_h_reduction_total": _round(delta_h_total),
        "physical_calls_total": int(call_total),
        "executed_actions": len(executed),
        "minimum_denominator": floor,
        "beta_status": beta_status,
        "gamma_status": gamma_status,
        "state_movement_terms_inert": sorted(inert_terms),
        "gamma_is_inert_because_delta_h_never_moved": delta_h_total == 0.0,
        "beta_denominator_supported": delta_r_total >= floor,
        "gamma_denominator_supported": delta_h_total >= floor,
        "lookahead_depth_two_supported": depth_two,
        "lookahead_depth_blockers": list(depth_reasons),
        "beta_estimable": beta_status == "estimated",
        "gamma_estimable": gamma_status == "estimated",
        "delta_estimable": call_total > 0,
        "lookahead_depth": planner.lookahead_depth,
        "planner_derivation_method": V3_M21_METHOD,
    }
    return planner, diagnostics


def derive_v3_calibration(
    *,
    merged_corpus: str | Path,
    output_dir: str | Path,
    train_gold: str | Path = REPO_ROOT / "benchmark" / "data" / "train.jsonl",
    inherited_m20: str | Path = REPO_ROOT / "configs" / "calibration" / "m20_relation_budget.json",
    collection_config: str | Path = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v3_train_collection.yaml",
    expected_merged_corpus_sha256: str = EXPECTED_MERGED_CORPUS_SHA256,
) -> dict[str, Any]:
    """Derive deterministic V3 M21 artifacts and a V3 M20 inherited wrapper."""
    out = Path(output_dir)
    v2_targets = {
        (REPO_ROOT / "configs" / "calibration" / name).resolve()
        for name in PRODUCTION_ARTIFACTS
    }
    for name in PRODUCTION_ARTIFACTS:
        if (out / name).resolve() in v2_targets:
            raise V3CalibrationDerivationError(
                f"refusing to overwrite historical V2 artifact {out / name}")

    corpus = load_merged_v3_corpus(
        merged_corpus,
        expected_merged_corpus_sha256=expected_merged_corpus_sha256,
    )
    records = list(read_telemetry(corpus.telemetry_path))
    schema = require_supported_schema(records)
    sufficiency = evaluate_sufficiency(records)
    if not sufficiency.ok:
        raise V3CalibrationDerivationError(
            f"merged telemetry is not calibration sufficient: "
            f"{sufficiency.blockers}")
    _validate_action_effects(corpus.action_effects, records)
    executed = [record for record in records if record.executed]
    for record in executed:
        try:
            parse_calibration_action_family(record.action_family)
        except DerivationError as error:
            raise V3CalibrationDerivationError(
                f"{record.operation_id}: {error}") from None

    train_gold_path = Path(train_gold)
    if sha256_file(train_gold_path) != EXPECTED_TRAIN_SHA256:
        raise V3CalibrationDerivationError(
            f"TRAIN hash is {sha256_file(train_gold_path)}, expected "
            f"{EXPECTED_TRAIN_SHA256}")
    gold = load_gold(train_gold_path, expected_rows=EXPECTED_TRAIN_ROWS)
    effects = score_actions(records, gold)
    inherited_payload, inherited_sha, budgets = _load_inherited_m20(Path(inherited_m20))
    del inherited_payload

    settings = DerivationSettings(pooled_fallbacks=True)
    binning = derive_binning_spec(records, settings)
    history, m21_diagnostics = derive_m21(
        records,
        effects,
        binning,
        settings,
        history_version=V3_M21_DERIVATION_VERSION,
    )
    missing_regions = _validate_fallback_regions(history)
    if missing_regions:
        raise V3CalibrationDerivationError(
            "V3 historical bins cannot resolve production-legal regions: "
            f"{missing_regions[:5]}")
    planner, planner_diagnostics = derive_v3_planner_calibration(
        history,
        records,
        effects,
        settings=settings,
    )
    manifest = corpus.manifest
    collection_repo_sha = str(
        manifest.get("supplement_repo_sha")
        or (manifest.get("supplement_repo_shas") or [""])[-1]
    )
    source_head = git_head()
    provenance = CalibrationProvenance(
        collection_repo_sha=collection_repo_sha,
        derivation_repo_sha=source_head,
        train_sha256=sha256_file(train_gold_path),
        train_rows=len(gold),
        predictions_sha256="",
        telemetry_sha256=corpus.train_telemetry_sha256,
        manifest_sha256=corpus.manifest_sha256,
        experiment_config_sha256=_config_sha(Path(collection_config)),
        evaluator_sha256=gold.evaluator_sha256,
        telemetry_schema_version=schema,
        derivation_schema_version=V3_DERIVATION_SCHEMA_VERSION,
        m20_derivation_version=M20_DERIVATION_VERSION,
        m21_derivation_version=V3_M21_DERIVATION_VERSION,
        binning_spec_version=BINNING_SPEC_VERSION,
        relation_catalogue=gold.relations(),
        collection_policy_version=str(corpus.coverage.get("policy_version", "")),
        settings=settings,
        support_counts={
            "considered_actions": len(records),
            "committed_action_effects": len(corpus.action_effects),
            "executed_actions": len(executed),
            "historical_bins": len(history.bins),
            "observed_transitions": m21_diagnostics["observed_transitions"],
            "queries": len({(r.row_index, r.relation) for r in records}),
        },
        merged_corpus_sha256=corpus.merged_corpus_sha256,
        merged_manifest_schema_version=str(manifest.get("schema_version", "")),
        base_collection_identity=str(manifest.get("base_identity", "")),
        base_collection_run=str(manifest.get("base_run", "")),
        supplement_identities=tuple(str(v) for v in manifest.get(
            "supplement_identities", ())),
        supplement_runs=tuple(str(v) for v in manifest.get("supplement_runs", ())),
        supplement_repo_shas=tuple(str(v) for v in manifest.get(
            "supplement_repo_shas", ())),
        action_policy_version=str(corpus.coverage.get("policy_version", "")),
        v3_core_schema_version="v3-core-v1",
        v3_action_effect_schema_version=V3_ACTION_EFFECT_VERSION,
        v3_action_effects_sha256=corpus.action_effects_sha256,
        v3_collection_run_id=f"merged:{corpus.merged_corpus_sha256[:16]}",
        m20_derivation_method=V3_M20_METHOD,
        m21_derivation_method=V3_M21_METHOD,
        minimum_bin_support=settings.minimum_bin_support,
        fallback_hierarchy=V3_FALLBACK_HIERARCHY,
        inherited_m20_source_sha256=inherited_sha,
    )
    bundle = CalibrationBundle(
        provenance=provenance,
        budgets=budgets,
        history=history,
        planner=planner,
    )
    payloads = {
        "m20_relation_budget.json": bundle.m20_json(),
        "m21_historical_bins.json": bundle.m21_history_json(),
        "m21_planner_calibration.json": bundle.m21_planner_json(),
    }
    for payload in payloads.values():
        assert_no_leakage(payload)

    out.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        _write_json(out / name, payload)

    artifact_sha256 = {
        name: sha256_file(out / name)
        for name in PRODUCTION_ARTIFACTS
    }
    report = validation_report(
        corpus=corpus,
        records=records,
        history=history,
        planner=planner,
        m21_diagnostics=m21_diagnostics,
        planner_diagnostics=planner_diagnostics,
        artifact_sha256=artifact_sha256,
        m20_status=V3_M20_METHOD,
        readiness_status="READY_FOR_CONFIG_VALIDATION",
    )
    _write_json(out / "derivation_report.json", report)
    (out / "derivation_report.md").write_text(
        render_validation_report(report), encoding="utf-8")
    provenance_payload = {
        "schema_version": "v3-calibration-provenance-v1",
        "provenance": provenance.to_json(),
        "artifact_sha256": artifact_sha256,
    }
    _write_json(out / "calibration_provenance.json", provenance_payload)
    sums = {
        name: sha256_file(out / name)
        for name in (
            *PRODUCTION_ARTIFACTS,
            "derivation_report.json",
            "derivation_report.md",
            "calibration_provenance.json",
        )
    }
    (out / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    return {
        "output_dir": str(out),
        "merged_corpus_sha256": corpus.merged_corpus_sha256,
        "committed_observations": len(executed),
        "artifact_sha256": artifact_sha256,
        "historical_bins": len(history.bins),
        "exact_bins": m21_diagnostics["exact_bins_kept"],
        "fallback_bins": m21_diagnostics["fallback_bins"],
        "m21_diagnostics": m21_diagnostics,
        "planner_diagnostics": planner_diagnostics,
        "m20_status": V3_M20_METHOD,
        "readiness_status": "READY_FOR_CONFIG_VALIDATION",
    }


def validation_report(
    *,
    corpus: MergedV3Corpus,
    records: Sequence[Any],
    history: Any,
    planner: Any,
    m21_diagnostics: Mapping[str, Any],
    planner_diagnostics: Mapping[str, Any],
    artifact_sha256: Mapping[str, str],
    m20_status: str,
    readiness_status: str,
) -> dict[str, Any]:
    executed = [record for record in records if record.executed]
    by_family: dict[str, int] = {}
    relations: set[str] = set()
    for record in executed:
        by_family[record.action_family] = by_family.get(record.action_family, 0) + 1
        relations.add(record.relation)
    fallback_bins = [
        entry for entry in history.bins if entry.state_bin_key == FALLBACK_STATE_BIN
    ]
    exact_bins = [
        entry for entry in history.bins if entry.state_bin_key != FALLBACK_STATE_BIN
    ]
    sparse_below_support = list(m21_diagnostics.get("dropped_bin_keys", ()))
    return {
        "schema_version": "v3-calibration-validation-report-v1",
        "corpus_sha256": corpus.merged_corpus_sha256,
        "committed_action_effects": len(corpus.action_effects),
        "committed_executed_observations": len(executed),
        "observations_by_family": dict(sorted(by_family.items())),
        "emitted_exact_bins": len(exact_bins),
        "emitted_fallback_bins": len(fallback_bins),
        "relation_fallback_bins": m21_diagnostics.get("relation_fallback_bins", 0),
        "program_fallback_bins": m21_diagnostics.get("program_fallback_bins", 0),
        "global_family_fallback_bins": m21_diagnostics.get(
            "global_family_fallback_bins", 0),
        "bins_below_minimum_support": len(sparse_below_support),
        "dropped_sparse_bin_keys": sparse_below_support,
        "relations_covered": sorted(relations),
        "action_families_covered": sorted(by_family),
        "missing_production_legal_calibration_regions": list(
            _validate_fallback_regions(history)),
        "m20_status": m20_status,
        "m21_status": "DERIVED",
        "lookahead_depth": planner.lookahead_depth,
        "readiness_status": readiness_status,
        "calibration_artifact_hashes": dict(sorted(artifact_sha256.items())),
        "m21_diagnostics": dict(m21_diagnostics),
        "planner_diagnostics": dict(planner_diagnostics),
        "fallback_hierarchy": list(V3_FALLBACK_HIERARCHY),
        "pooled_fallback_keys": {
            "relation": POOLED_RELATION_FALLBACK,
            "program_type": POOLED_PROGRAM_FALLBACK,
            "state_bin": FALLBACK_STATE_BIN,
        },
    }


def render_validation_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# V3 Calibration Validation Report",
        "",
        f"- corpus SHA256: `{report['corpus_sha256']}`",
        f"- committed action effects: {report['committed_action_effects']}",
        f"- executed observations: {report['committed_executed_observations']}",
        f"- exact bins: {report['emitted_exact_bins']}",
        f"- fallback bins: {report['emitted_fallback_bins']}",
        f"- readiness: {report['readiness_status']}",
        "",
        "## Observations by family",
        "",
    ]
    for family, count in report["observations_by_family"].items():
        lines.append(f"- `{family}`: {count}")
    lines.extend(("", "## Fallback hierarchy", ""))
    for item in report["fallback_hierarchy"]:
        lines.append(f"- {item}")
    missing = report["missing_production_legal_calibration_regions"]
    lines.extend(("", "## Missing production-legal regions", ""))
    if missing:
        for item in missing:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def read_artifact_hashes(calibration_dir: str | Path) -> dict[str, str]:
    root = Path(calibration_dir)
    return {name: sha256_file(root / name) for name in PRODUCTION_ARTIFACTS}


def coverage_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"action_family": family, "executed": count}
        for family, count in report["observations_by_family"].items()
    ]


def write_validation_csv(path: Path, report: Mapping[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("action_family", "executed"))
        writer.writeheader()
        writer.writerows(coverage_csv_rows(report))


__all__ = [
    "EXPECTED_MERGED_CORPUS_SHA256",
    "EXPECTED_TRAIN_ROWS",
    "EXPECTED_TRAIN_SHA256",
    "PRODUCTION_ARTIFACTS",
    "V3CalibrationDerivationError",
    "V3_DERIVATION_SCHEMA_VERSION",
    "V3_FALLBACK_HIERARCHY",
    "V3_M20_METHOD",
    "V3_M21_DERIVATION_VERSION",
    "V3_M21_METHOD",
    "derive_v3_calibration",
    "git_head",
    "load_merged_v3_corpus",
    "read_artifact_hashes",
    "render_validation_report",
    "resolve_merged_corpus_dir",
    "sha256_file",
    "validation_report",
    "write_validation_csv",
]
