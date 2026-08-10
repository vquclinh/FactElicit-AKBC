from __future__ import annotations

import builtins
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from cover_kbc.control.historical_bins import (
    POOLED_PROGRAM_FALLBACK,
    POOLED_RELATION_FALLBACK,
    load_history,
)
from cover_kbc.controller_calibration.readiness import (
    evaluate_test_readiness,
    evaluate_train_diagnostic_readiness,
)
from cover_kbc.controller_calibration.v3_derivation import (
    EXPECTED_MERGED_CORPUS_SHA256,
    PRODUCTION_ARTIFACTS,
    V3CalibrationDerivationError,
    _validate_fallback_regions,
    derive_v3_calibration,
    load_merged_v3_corpus,
    read_artifact_hashes,
    resolve_merged_corpus_dir,
)
from cover_kbc.v3_core.relation_programs import V3ActionFamily


MERGED = Path("outputs/v3_supplement2_ae33b2ee_20260810T084303Z")
CONFIG = Path("configs/experiments/cover_kbc_v3_production.yaml")
CALIBRATION_DIR = Path("configs/calibration/v3")
V2_HASHES = {
    "configs/calibration/m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "configs/calibration/m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "configs/calibration/m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_corpus(tmp_path: Path) -> Path:
    source = resolve_merged_corpus_dir(MERGED)
    target = tmp_path / "merged_v3_calibration"
    shutil.copytree(source, target)
    return target


def _refresh_hashes(corpus: Path) -> None:
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    merged = hashlib.sha256(
        (corpus / "action_effects.jsonl").read_bytes()
        + (corpus / "train_telemetry.jsonl").read_bytes()
    ).hexdigest()
    manifest["merged_corpus_sha256"] = merged
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    names = [
        "action_effects.jsonl",
        "coverage.csv",
        "coverage.json",
        "manifest.json",
        "train_telemetry.jsonl",
    ]
    (corpus / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha(corpus / name)}  {name}\n" for name in names),
        encoding="utf-8",
    )


def _load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_v3_derivation_repeats_byte_identically(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    a = derive_v3_calibration(merged_corpus=MERGED, output_dir=first)
    b = derive_v3_calibration(merged_corpus=MERGED, output_dir=second)
    assert a["artifact_sha256"] == b["artifact_sha256"]
    for name in PRODUCTION_ARTIFACTS:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_wrong_merged_corpus_sha_is_refused() -> None:
    with pytest.raises(V3CalibrationDerivationError, match="expected"):
        load_merged_v3_corpus(MERGED, expected_merged_corpus_sha256="0" * 64)
    assert load_merged_v3_corpus(
        MERGED).merged_corpus_sha256 == EXPECTED_MERGED_CORPUS_SHA256


def test_duplicate_action_effect_id_is_rejected(tmp_path: Path) -> None:
    corpus = _copy_corpus(tmp_path)
    first = (corpus / "action_effects.jsonl").read_text(encoding="utf-8").splitlines()[0]
    with (corpus / "action_effects.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(first + "\n")
    _refresh_hashes(corpus)
    with pytest.raises(
        V3CalibrationDerivationError, match="duplicate action-effect identity"
    ):
        derive_v3_calibration(
            merged_corpus=corpus,
            output_dir=tmp_path / "out",
            expected_merged_corpus_sha256="",
        )


def test_uncommitted_action_effect_tail_is_rejected(tmp_path: Path) -> None:
    corpus = _copy_corpus(tmp_path)
    payload = json.loads(
        (corpus / "action_effects.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    payload["action_effect_id"] = "tail:not-committed"
    payload["action"]["action_id"] = "tail-action-not-in-telemetry"
    with (corpus / "action_effects.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    _refresh_hashes(corpus)
    with pytest.raises(
        V3CalibrationDerivationError,
        match="no committed executed telemetry record matches",
    ):
        derive_v3_calibration(
            merged_corpus=corpus,
            output_dir=tmp_path / "out",
            expected_merged_corpus_sha256="",
        )


def test_sparse_relation_region_uses_observed_global_family_fallback() -> None:
    history = load_history(
        json.loads((CALIBRATION_DIR / "m21_historical_bins.json").read_text())
    )
    entry = history.lookup(
        relation="personHasCityOfDeath",
        program_type="NULL_SINGLE",
        state_bin_key="unobserved-production-state",
        family=V3ActionFamily.SEMANTIC_VERIFY,
    )
    assert entry.relation == POOLED_RELATION_FALLBACK
    assert entry.program_type == POOLED_PROGRAM_FALLBACK
    assert entry.support_count == 6


def test_missing_pooled_fallback_rejects_production_legal_region() -> None:
    payload = json.loads((CALIBRATION_DIR / "m21_historical_bins.json").read_text())
    payload["bins"] = [
        entry for entry in payload["bins"]
        if not (
            entry["Relation"] == POOLED_RELATION_FALLBACK
            and entry["program_type"] == POOLED_PROGRAM_FALLBACK
            and entry["action_family"] == "SEMANTIC_VERIFY"
        )
    ]
    history = load_history(payload)
    missing = _validate_fallback_regions(history)
    assert any(
        item.startswith("personHasCityOfDeath/NULL_SINGLE/SEMANTIC_VERIFY")
        for item in missing
    )


def test_v2_calibration_artifacts_remain_byte_identical() -> None:
    for raw, expected in V2_HASHES.items():
        assert _sha(Path(raw)) == expected


def test_production_v3_config_loads_only_v3_artifacts_and_is_ready() -> None:
    config = _load_config()
    report = evaluate_train_diagnostic_readiness(
        config, base_dir=CONFIG.parent, split="train")
    assert report.may_run_train_diagnostic
    paths = report.details["calibration"]["paths"]
    assert all("/calibration/v3/" in path for path in paths.values())
    assert not config.get("train_collection")


def test_production_v3_config_cannot_load_collection_mode() -> None:
    config = _load_config()
    config["train_collection"] = {"policy": "collect-v2-coverage-r2"}
    config["pipeline"]["v3_core"]["mode"] = "train_collection"
    report = evaluate_train_diagnostic_readiness(
        config, base_dir=CONFIG.parent, split="train")
    assert not report.may_run_train_diagnostic
    assert any("collection" in blocker for blocker in report.blockers)


def test_test_gate_blocks_this_train_diagnostic_profile() -> None:
    report = evaluate_test_readiness(
        _load_config(), base_dir=CONFIG.parent, split="test")
    assert not report.may_run_test
    assert report.blockers


def test_v3_derivation_does_not_read_val_or_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = builtins.open
    real_path_open = Path.open

    def guarded_open(file, *args, **kwargs):
        raw = str(file)
        if "benchmark/data/val" in raw or "benchmark/data/test" in raw:
            raise AssertionError(f"derivation read forbidden split {raw}")
        return real_open(file, *args, **kwargs)

    def guarded_path_open(self, *args, **kwargs):
        raw = str(self)
        if "benchmark/data/val" in raw or "benchmark/data/test" in raw:
            raise AssertionError(f"derivation read forbidden split {raw}")
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    derive_v3_calibration(merged_corpus=MERGED, output_dir=tmp_path / "out")


def test_generated_v3_artifact_hashes_match_report() -> None:
    report = json.loads(
        (CALIBRATION_DIR / "derivation_report.json").read_text(encoding="utf-8")
    )
    assert report["calibration_artifact_hashes"] == read_artifact_hashes(
        CALIBRATION_DIR)
