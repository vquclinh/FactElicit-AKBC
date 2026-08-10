from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
    evaluate_train_diagnostic_readiness,
    ordered_identity_digest,
)
from cover_kbc.data.loader import load_dataset
from cover_kbc.paths import REPO_ROOT


EXPERIMENTS = REPO_ROOT / "configs" / "experiments"
V3_TEST_CONFIG = EXPERIMENTS / "cover_kbc_v3_test.yaml"
V3_TRAIN_CONFIG = EXPERIMENTS / "cover_kbc_v3_production.yaml"
TEST_DATA = REPO_ROOT / "benchmark" / "data" / "test.jsonl"
TEST_ROWS = 475
TEST_SHA256 = "67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1"
TEST_IDENTITY = "69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640"
MERGED_CORPUS_SHA256 = (
    "50be66dc88419f88ac811243244d534f0a8a160bdf2a48d02eb77295a58c33af"
)
V3_ARTIFACT_HASHES = {
    "configs/calibration/v3/m20_relation_budget.json":
        "74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29",
    "configs/calibration/v3/m21_historical_bins.json":
        "ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675",
    "configs/calibration/v3/m21_planner_calibration.json":
        "1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6",
    "configs/calibration/v3/calibration_provenance.json":
        "f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d",
    "configs/calibration/v3/derivation_report.json":
        "8456c0824cb831257caf62a2b0624812fc170058788b7f954c760a5ddda2394d",
    "configs/calibration/v3/derivation_report.md":
        "1c397bf03938765c1f79318a7a041bd1f2880611db88e78ac08f5c9cb8935b95",
    "configs/calibration/v3/SHA256SUMS.txt":
        "6349a116a932007288eba53e24e33045123c8ce62455bc836876ac0d9c6853f5",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_config() -> dict:
    return yaml.safe_load(V3_TEST_CONFIG.read_text(encoding="utf-8"))


def _ready(config: dict, **kwargs):
    provenance = dict(config.get("calibration_provenance") or {})
    return evaluate_test_readiness(
        config,
        base_dir=EXPERIMENTS,
        split="test",
        expected_collection_repo_sha=provenance.get("collection_repo_sha"),
        expected_derivation_repo_sha=provenance.get("derivation_repo_sha"),
        **kwargs,
    )


def _script_module():
    path = REPO_ROOT / "scripts" / "check_v3_test_readiness.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("check_v3_test", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_canonical_v3_test_identity_is_accepted() -> None:
    rows = _rows(TEST_DATA)
    assert len(rows) == TEST_ROWS
    assert _sha(TEST_DATA) == TEST_SHA256
    assert ordered_identity_digest(
        (row["SubjectEntity"], row["Relation"]) for row in rows
    ) == TEST_IDENTITY
    assert all(row["ObjectEntities"] == [] for row in rows)

    report = _ready(_load_config())
    assert report.state is ReadinessState.FULL_TEST_READY, report.blockers
    assert report.details["test_rows"] == TEST_ROWS
    assert report.details["test_sha256"] == TEST_SHA256
    assert report.details["test_identity_sha256"] == TEST_IDENTITY
    assert report.details["v3_unresolved_calibration_regions"] == []


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("sha256", "0" * 64, "sha256"),
        ("identity_sha256", "0" * 64, "ordered identity"),
        ("rows", 474, "rows"),
    ],
)
def test_wrong_test_dataset_pins_are_rejected(
    field: str, value: object, needle: str,
) -> None:
    config = _load_config()
    config["test_dataset"][field] = value
    report = _ready(config)
    assert report.state is ReadinessState.NOT_READY
    assert any(needle in blocker for blocker in report.blockers)


def test_non_empty_test_object_entities_are_rejected(tmp_path: Path) -> None:
    rows = _rows(TEST_DATA)
    rows[0]["ObjectEntities"] = ["leaked answer"]
    body = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
    (tmp_path / "test.jsonl").write_text(body, encoding="utf-8")
    config = _load_config()
    config["test_dataset"]["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    report = _ready(config, data_dir=tmp_path)
    assert report.state is ReadinessState.NOT_READY
    assert any("carry ObjectEntities" in blocker for blocker in report.blockers)


def test_v2_calibration_paths_are_rejected_by_v3_test_config() -> None:
    config = _load_config()
    config["relation_budget_scheduler"]["calibration_file"] = (
        "../calibration/m20_relation_budget.json")
    config["relation_budget_scheduler"]["calibration_sha256"] = (
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68")
    config["micro_planner"]["historical_bins"] = (
        "../calibration/m21_historical_bins.json")
    config["micro_planner"]["historical_bins_sha256"] = (
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071")
    config["micro_planner"]["planner_calibration"] = (
        "../calibration/m21_planner_calibration.json")
    config["micro_planner"]["planner_calibration_sha256"] = (
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05")
    report = _ready(config)
    assert report.state is ReadinessState.NOT_READY
    assert any("may not reuse the historical V2" in b for b in report.blockers)


def test_collection_mode_is_rejected_by_v3_test_config() -> None:
    config = _load_config()
    config["pipeline"]["v3_core"]["mode"] = "train_collection"
    config["train_collection"] = {"policy": "collect-v2-coverage-r2"}
    report = _ready(config)
    assert report.state is ReadinessState.NOT_READY
    joined = " | ".join(report.blockers)
    assert "train_collection" in joined
    assert "collection-only scheduling" in joined


def test_calibration_hash_mismatch_is_rejected() -> None:
    config = _load_config()
    config["micro_planner"]["planner_calibration_sha256"] = "0" * 64
    report = _ready(config)
    assert report.state is ReadinessState.NOT_READY
    assert any("hashes to" in blocker for blocker in report.blockers)


def test_unresolved_v3_calibration_fallback_is_rejected(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "configs" / "calibration" / "v3"
    artifact_dir.mkdir(parents=True)
    for name in (
        "m20_relation_budget.json",
        "m21_historical_bins.json",
        "m21_planner_calibration.json",
    ):
        shutil.copyfile(REPO_ROOT / "configs" / "calibration" / "v3" / name,
                        artifact_dir / name)
    history_path = artifact_dir / "m21_historical_bins.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history["bins"] = [
        entry for entry in history["bins"]
        if not (
            entry["Relation"] == "__any_relation__"
            and entry["program_type"] == "__any_program__"
            and entry["action_family"] == "SEMANTIC_VERIFY"
        )
    ]
    history_path.write_text(
        json.dumps(history, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config = _load_config()
    config["relation_budget_scheduler"]["calibration_file"] = str(
        artifact_dir / "m20_relation_budget.json")
    config["relation_budget_scheduler"]["calibration_sha256"] = _sha(
        artifact_dir / "m20_relation_budget.json")
    config["micro_planner"]["historical_bins"] = str(history_path)
    config["micro_planner"]["historical_bins_sha256"] = _sha(history_path)
    config["micro_planner"]["planner_calibration"] = str(
        artifact_dir / "m21_planner_calibration.json")
    config["micro_planner"]["planner_calibration_sha256"] = _sha(
        artifact_dir / "m21_planner_calibration.json")
    report = _ready(config)
    assert report.state is ReadinessState.NOT_READY
    assert any("V3 fallback coverage" in blocker for blocker in report.blockers)


def test_v3_test_precheck_loads_zero_neural_models(capsys) -> None:
    module = _script_module()
    source = (REPO_ROOT / "scripts" / "check_v3_test_readiness.py").read_text()
    for forbidden in (
        "build_runtime",
        "require_huggingface_runtime",
        "HuggingFaceRuntime",
        "from_pretrained",
        "generate(",
    ):
        assert forbidden not in source
    report = module.build_report(V3_TEST_CONFIG)
    assert report["ready"] is True
    assert report["model_calls"] == 0
    assert report["test_rows"] == TEST_ROWS
    assert report["missing_calibration_regions"] == 0
    print(module.render(report), end="")
    captured = capsys.readouterr().out
    assert "V3 TEST PRODUCTION READINESS: READY" in captured
    assert "model calls: 0" in captured


def test_v3_test_config_resolves_exactly_475_rows() -> None:
    dataset = load_dataset("test")
    assert len(dataset) == TEST_ROWS
    report = _ready(_load_config())
    assert report.details["test_rows"] == TEST_ROWS


def test_audit_0073_v3_calibration_artifacts_are_byte_identical() -> None:
    for raw, expected in V3_ARTIFACT_HASHES.items():
        assert _sha(REPO_ROOT / raw) == expected


def test_train_diagnostic_config_remains_valid_and_separate() -> None:
    train_config = yaml.safe_load(V3_TRAIN_CONFIG.read_text(encoding="utf-8"))
    report = evaluate_train_diagnostic_readiness(
        train_config, base_dir=EXPERIMENTS, split="train")
    assert report.state is ReadinessState.TRAIN_DIAGNOSTIC_READY
    assert train_config["experiment"]["split"] == "train"
    assert _load_config()["experiment"]["split"] == "test"
    assert "train_collection" not in _load_config()


def test_submission_shape_for_test_preserves_order_and_schema(tmp_path: Path) -> None:
    rows = _rows(TEST_DATA)
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(
        "\n".join(json.dumps({
            "SubjectEntity": row["SubjectEntity"],
            "Relation": row["Relation"],
            "ObjectEntities": [],
        }, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    payload = _rows(predictions)
    assert len(payload) == TEST_ROWS
    assert [
        (row["SubjectEntity"], row["Relation"]) for row in payload
    ] == [
        (row["SubjectEntity"], row["Relation"]) for row in rows
    ]
    assert {tuple(sorted(row)) for row in payload} == {
        ("ObjectEntities", "Relation", "SubjectEntity")}
    assert all("gold" not in row and "train" not in row for row in payload)
