"""The offline derivation CLI, driven end to end through its real ``main()``.

Nothing about the pipeline is mocked. A real collection is produced by the real
collection runner against the **committed** TRAIN config - only the model
runtimes are replaced, because a test may not load 28.67B of weights - and the
derivation CLI is then pointed at the artifacts it wrote, with the real pinned
evaluator and a real slice of TRAIN gold.

The fail-closed cases matter as much as the happy path: a calibration derived
from the wrong run, the wrong split or a tampered artifact is worse than none,
because it looks like one. Each refusal below is asserted against the real
guard, not against a flag.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = _load("run_train_calibration_collection")
cli = _load("derive_train_calibration")

CONFIG = ROOT / "configs" / "experiments" / "cover_kbc_v2_train_collection.yaml"
TRAIN = ROOT / "benchmark" / "data" / "train.jsonl"

#: One real TRAIN row per relation. All six are needed because the collection's
#: own coverage gate fails closed when a required action family never surfaces,
#: and ``REVERSE_CHECK`` only surfaces for borders - so a contiguous slice would
#: (correctly) refuse to complete.
FIXTURE_ROW_INDICES = (0, 100, 200, 210, 310, 377)
#: The real guard is 477; the constant is patched so the *pipeline* can be
#: exercised at test speed, and
#: ``test_a_row_count_that_is_not_the_official_split_is_refused`` covers the
#: guard itself.
FIXTURE_ROWS = len(FIXTURE_ROW_INDICES)

ANSWERS = {
    "hasArea": "45000",
    "hasCapacity": "45000",
    "personHasCityOfDeath": "Paris",
    "companyTradesAtStockExchange": "NASDAQ; New York Stock Exchange",
    "awardWonBy": "Marie Curie; Albert Einstein",
    "countryLandBordersCountry": "Spain; Andorra; Monaco",
}


def _scripted_runtime(config):
    from cover_kbc.models.offline import ScriptedRuntime

    role = str(config.get("role", "enumerator"))
    return ScriptedRuntime(
        model_id=f"offline/{role}", role=role,
        family=str(config.get("family", "offline")),
        fallback=lambda request: ANSWERS.get(
            str(request.metadata.get("relation", "")), "NONE"))


@pytest.fixture(scope="module")
def collection(tmp_path_factory):
    """A real collection over the first few TRAIN rows, plus its gold subset."""
    monkeypatch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("collect")
    monkeypatch.setattr(collector, "build_runtime", _scripted_runtime)
    monkeypatch.setattr(collector, "EXPECTED_TRAIN_ROWS", FIXTURE_ROWS)
    monkeypatch.setattr(sys, "argv", [
        "run_train_calibration_collection.py", "--config", str(CONFIG),
        "--output-dir", str(out)])

    # The collection runner reads the whole split, so it is sliced here rather
    # than with --limit: --limit changes the run identity, and the derivation
    # must be able to bind to a manifest that claims a full split.
    real_loader = collector.load_dataset

    class _Subset:
        def __init__(self, dataset):
            self._d = dataset
            self.sha256 = dataset.sha256
            self.path = dataset.path

        def queries(self):
            wanted = set(FIXTURE_ROW_INDICES)
            return [q for q in self._d.queries() if q.row_index in wanted]

    monkeypatch.setattr(collector, "load_dataset",
                        lambda split: _Subset(real_loader(split)))
    assert collector.main() == 0
    monkeypatch.undo()

    run_dir = next(p for p in out.iterdir() if p.is_dir())
    gold_subset = out / "train_subset.jsonl"
    lines = TRAIN.read_text().splitlines(keepends=True)
    gold_subset.write_text(
        "".join(lines[index] for index in FIXTURE_ROW_INDICES), encoding="utf-8")

    # The collection read the *whole* split for its identity, so its manifest
    # names the full file. This fixture derives against the six-row subset, so
    # the manifest is restated to describe that subset - making the triple
    # self-consistent, exactly as a real full run is. Each binding check is
    # asserted independently below, so nothing is being waved through.
    import hashlib
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["identity"]["train_sha256"] = hashlib.sha256(
        gold_subset.read_bytes()).hexdigest()
    manifest["identity"]["total_rows"] = FIXTURE_ROWS
    bound = out / "manifest.json"
    bound.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "root": out, "run_dir": run_dir, "gold": gold_subset,
        "telemetry": run_dir / "train_telemetry.jsonl",
        "predictions": run_dir / "predictions.jsonl",
        "manifest": bound,
    }


#: The fixture is six rows, so the whole collection moves the residual by well
#: under one full unit and the default denominator floor (1.0) correctly
#: refuses it - see ``test_the_default_denominator_floor_refuses_a_tiny_slice``.
#: The pipeline tests therefore declare a floor proportional to the slice, which
#: still refuses the rounding-noise case the guard exists for.
FIXTURE_DENOMINATOR = "0.1"


def _argv(collection, out: Path, **overrides) -> list[str]:
    args = {
        "--config": str(CONFIG), "--train-gold": str(collection["gold"]),
        "--minimum-denominator": FIXTURE_DENOMINATOR,
        "--predictions": str(collection["predictions"]),
        "--telemetry": str(collection["telemetry"]),
        "--manifest": str(collection["manifest"]), "--output-dir": str(out),
    }
    args.update(overrides)
    argv = ["derive_train_calibration.py"]
    for key, value in args.items():
        argv += [key, value]
    return argv


#: A stand-in for the clean-commit SHA the guard resolves. The guard itself is
#: exercised exhaustively against real temporary repositories in
#: ``tests/test_calibration_p1_remediation.py``; here it is satisfied so the
#: rest of the CLI can be tested, because this working tree is - by
#: construction, during development - not a clean checkout.
FAKE_DERIVATION_SHA = "1234567890abcdef1234567890abcdef12345678"


def _derive(monkeypatch, collection, out: Path, **overrides) -> int:
    monkeypatch.setattr(cli, "EXPECTED_TRAIN_ROWS", FIXTURE_ROWS)
    monkeypatch.setattr(cli, "resolve_derivation_source",
                        lambda *a, **k: FAKE_DERIVATION_SHA)
    monkeypatch.setattr(sys, "argv", _argv(collection, out, **overrides))
    return cli.main()


def test_the_default_denominator_floor_refuses_a_tiny_slice(
    monkeypatch, collection, tmp_path,
) -> None:
    """P1-3 at CLI level: too little observed movement to support a rate.

    Six rows move the residual by roughly two thirds of a unit in total. A rate
    of verified objects *per unit* of residual is then extrapolation, and the
    derivation says so rather than shipping the number.
    """
    from cover_kbc.controller_calibration.derivation import DerivationError

    out = tmp_path / "out"
    monkeypatch.setattr(cli, "EXPECTED_TRAIN_ROWS", FIXTURE_ROWS)
    monkeypatch.setattr(cli, "resolve_derivation_source",
                        lambda *a, **k: FAKE_DERIVATION_SHA)
    argv = [a for a in _argv(collection, out)]
    index = argv.index("--minimum-denominator")
    del argv[index:index + 2]                       # fall back to the default
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(DerivationError, match="below the minimum meaningful"):
        cli.main()


def test_the_cli_refuses_a_dirty_derivation_source(
    monkeypatch, collection, tmp_path,
) -> None:
    """P1-1: the CLI is gated, and nothing is written when the gate closes."""
    from cover_kbc.controller_calibration.derivation import DirtyDerivationSource

    def dirty(*_args, **_kwargs):
        raise DirtyDerivationSource("?? src/cover_kbc/something.py")

    out = tmp_path / "out"
    monkeypatch.setattr(cli, "EXPECTED_TRAIN_ROWS", FIXTURE_ROWS)
    monkeypatch.setattr(cli, "resolve_derivation_source", dirty)
    monkeypatch.setattr(sys, "argv", _argv(collection, out))
    with pytest.raises(DirtyDerivationSource):
        cli.main()
    assert not out.exists(), "an artifact directory was created despite refusal"


def test_the_guard_fires_before_any_input_is_read(
    monkeypatch, collection, tmp_path,
) -> None:
    """Ordering: a dirty tree is refused even when an input is also missing."""
    from cover_kbc.controller_calibration.derivation import DirtyDerivationSource

    def dirty(*_args, **_kwargs):
        raise DirtyDerivationSource("dirty")

    monkeypatch.setattr(cli, "EXPECTED_TRAIN_ROWS", FIXTURE_ROWS)
    monkeypatch.setattr(cli, "resolve_derivation_source", dirty)
    monkeypatch.setattr(sys, "argv", _argv(
        collection, tmp_path / "out",
        **{"--predictions": str(tmp_path / "absent.jsonl")}))
    with pytest.raises(DirtyDerivationSource):
        cli.main()


@pytest.fixture(scope="module")
def derived(collection, tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("calibration")
    assert _derive(monkeypatch, collection, out) == 0
    monkeypatch.undo()
    return out


# --------------------------------------------------------------------------
# the happy path, end to end
# --------------------------------------------------------------------------


def test_the_cli_writes_every_artifact(derived) -> None:
    for name in ("m20_relation_budget.json", "m21_historical_bins.json",
                 "m21_planner_calibration.json", "derivation_report.json",
                 "derivation_report.md"):
        assert (derived / name).is_file(), name


def test_the_artifacts_load_through_their_production_owners(derived) -> None:
    """Written by the derivation, read by Modules 20 and 21 themselves."""
    from cover_kbc.control.historical_bins import load_history
    from cover_kbc.control.planner_types import PlannerCalibration
    from cover_kbc.control.relation_budget import load_calibrations

    budgets = load_calibrations(
        json.loads((derived / "m20_relation_budget.json").read_text()))
    assert budgets
    for calibration in budgets.values():
        assert calibration.calibration_source.is_production

    history = load_history(
        json.loads((derived / "m21_historical_bins.json").read_text()))
    assert history.bins
    assert history.source.is_production

    planner = PlannerCalibration.from_json(
        json.loads((derived / "m21_planner_calibration.json").read_text()))
    assert planner.source.is_production
    assert planner.tau_continue == 0.0


def test_the_derivation_is_reproducible(monkeypatch, collection, tmp_path) -> None:
    """Same inputs, byte-identical production artifacts."""
    first, second = tmp_path / "a", tmp_path / "b"
    assert _derive(monkeypatch, collection, first) == 0
    assert _derive(monkeypatch, collection, second) == 0
    for name in ("m20_relation_budget.json", "m21_historical_bins.json",
                 "m21_planner_calibration.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def _string_leaves(payload, out: list[str]) -> list[str]:
    """Every string a JSON document contains, key or value.

    Gold can only leak as text, so numbers are skipped deliberately: a gold
    area of ``5556`` is a substring of the float ``0.055556``, and testing raw
    bytes would fail on arithmetic that leaked nothing.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.append(str(key))
            _string_leaves(value, out)
    elif isinstance(payload, list):
        for value in payload:
            _string_leaves(value, out)
    elif isinstance(payload, str):
        out.append(payload)
    return out


def test_no_gold_object_reaches_a_production_artifact(derived, collection) -> None:
    """Against the real gold this run was joined to, over every string written."""
    surfaces: list[str] = []
    subjects: list[str] = []
    for line in collection["gold"].read_text().splitlines():
        row = json.loads(line)
        subjects.append(row["SubjectEntity"])
        for entry in row["ObjectEntities"]:
            surfaces += [entry] if isinstance(entry, str) else list(entry)

    for name in ("m20_relation_budget.json", "m21_historical_bins.json",
                 "m21_planner_calibration.json"):
        payload = json.loads((derived / name).read_text())
        strings = [s.casefold() for s in _string_leaves(payload, [])]
        for wanted in surfaces + subjects:
            folded = wanted.casefold()
            for found in strings:
                assert folded not in found, f"{name}: {wanted!r} in {found!r}"


def test_the_report_carries_the_pre_calibration_official_metrics(derived) -> None:
    report = json.loads((derived / "derivation_report.json").read_text())
    metrics = report["pre_calibration_official_metrics"]
    assert metrics["macro"], "no per-relation macro metrics were recorded"
    assert metrics["evaluator_sha256"]
    assert report["relation_spend"]
    assert report["action_level_statistics"]
    assert report["state_bin_support"]


def test_provenance_binds_the_artifacts_to_this_collection(
    derived, collection,
) -> None:
    import hashlib

    provenance = json.loads(
        (derived / "m20_relation_budget.json").read_text())["provenance"]
    for label, path in (("telemetry_sha256", collection["telemetry"]),
                        ("predictions_sha256", collection["predictions"]),
                        ("manifest_sha256", collection["manifest"]),
                        ("train_sha256", collection["gold"])):
        assert provenance[label] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert provenance["telemetry_schema_version"] == "train-telemetry-v3"
    assert provenance["evaluator_sha256"]
    assert provenance["support_counts"]["executed_actions"] > 0


def test_delta_h_is_reported_truthfully(derived) -> None:
    """C-02 on real collected telemetry, not a fixture that forces it."""
    report = json.loads((derived / "derivation_report.json").read_text())
    diagnostics = report["m21_diagnostics"]
    planner = json.loads((derived / "m21_planner_calibration.json").read_text())
    if diagnostics["delta_h_is_structurally_zero"]:
        assert diagnostics["delta_h_non_zero"] == 0
        assert planner["gamma"] == 0.0
        assert "gamma is therefore inert" in (
            derived / "derivation_report.md").read_text()
    history = json.loads((derived / "m21_historical_bins.json").read_text())
    for entry in history["bins"]:
        if diagnostics["delta_h_is_structurally_zero"]:
            assert entry["expected_delta_h"] == 0.0


# --------------------------------------------------------------------------
# fail closed
# --------------------------------------------------------------------------


def test_a_val_shaped_path_is_refused(monkeypatch, collection, tmp_path) -> None:
    """The cheapest mistake is the wrong path, so it is caught first."""
    val = tmp_path / "val.jsonl"
    val.write_text(collection["gold"].read_text(), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="'val' split"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--train-gold": str(val)})


def test_a_test_shaped_path_is_refused(monkeypatch, collection, tmp_path) -> None:
    leaked = tmp_path / "test.jsonl"
    leaked.write_text(collection["gold"].read_text(), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="'test' split"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--train-gold": str(leaked)})


def test_a_non_train_config_is_refused(monkeypatch, collection, tmp_path) -> None:
    target = ROOT / "configs" / "experiments" / "cover_kbc_v2_mistral24_qwen4.yaml"
    with pytest.raises(cli.CalibrationDerivationError, match="declares split"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--config": str(target)})


def test_a_tampered_telemetry_file_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    """The manifest records what the collection wrote; anything else is not it."""
    import hashlib

    manifest = json.loads(collection["manifest"].read_text())
    manifest["telemetry_sha256"] = hashlib.sha256(b"not this file").hexdigest()
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="hash mismatch"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_a_train_gold_that_is_not_the_collected_split_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    """A different TRAIN file is a different derivation, whatever it is named."""
    other = tmp_path / "train_other.jsonl"
    other.write_text(
        "".join(TRAIN.read_text().splitlines(keepends=True)[:FIXTURE_ROWS]),
        encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="TRAIN hash mismatch"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--train-gold": str(other)})


def test_an_asserted_hash_that_does_not_match_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    """The operator's own provenance assertion, checked when supplied."""
    with pytest.raises(cli.CalibrationDerivationError,
                       match="telemetry hash mismatch"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--expect-telemetry-sha256": "0" * 64})


def test_a_matching_asserted_hash_is_accepted(
    monkeypatch, collection, tmp_path,
) -> None:
    import hashlib

    assert _derive(
        monkeypatch, collection, tmp_path / "out",
        **{"--expect-telemetry-sha256": hashlib.sha256(
               collection["telemetry"].read_bytes()).hexdigest(),
           "--expect-predictions-sha256": hashlib.sha256(
               collection["predictions"].read_bytes()).hexdigest(),
           "--expect-manifest-sha256": hashlib.sha256(
               collection["manifest"].read_bytes()).hexdigest()}) == 0


def test_a_row_count_that_is_not_the_official_split_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    """The guard the fixture patches away, asserted directly."""
    monkeypatch.setattr(cli, "resolve_derivation_source",
                        lambda *a, **k: FAKE_DERIVATION_SHA)
    monkeypatch.setattr(sys, "argv", _argv(collection, tmp_path / "out"))
    # EXPECTED_TRAIN_ROWS stays at its real 477 here.
    with pytest.raises(cli.CalibrationDerivationError, match="rows, expected 477"):
        cli.main()


def test_a_collection_that_failed_its_own_gate_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    manifest = json.loads(collection["manifest"].read_text())
    manifest["gate_blockers"] = ["a family was never surfaced"]
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="exit gate"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_an_insufficient_collection_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    manifest = json.loads(collection["manifest"].read_text())
    manifest["sufficiency"] = {"ok": False, "blockers": ["no successor chain"]}
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="sufficiency"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_an_unresolved_failed_row_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    manifest = json.loads(collection["manifest"].read_text())
    manifest["unresolved_failed_rows"] = [7]
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="unresolved"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_a_shadow_mode_collection_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    manifest = json.loads(collection["manifest"].read_text())
    manifest["integration_mode"] = "shadow"
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="integration_mode"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_malformed_telemetry_is_refused(monkeypatch, collection, tmp_path) -> None:
    from cover_kbc.controller_calibration.telemetry import TelemetryError

    broken = tmp_path / "train_telemetry.jsonl"
    broken.write_text("{not json}\n", encoding="utf-8")
    manifest = json.loads(collection["manifest"].read_text())
    manifest.pop("telemetry_sha256", None)
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(TelemetryError):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--telemetry": str(broken), "--manifest": str(forged)})


def test_a_missing_input_is_refused(monkeypatch, collection, tmp_path) -> None:
    with pytest.raises(cli.CalibrationDerivationError, match="no file at"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--predictions": str(tmp_path / "nope.jsonl")})


def test_the_cli_never_builds_a_model_runtime(monkeypatch, collection,
                                              tmp_path) -> None:
    """The structural guarantee, asserted by making it fatal to try."""
    import cover_kbc.models.registry as registry

    def explode(*_args, **_kwargs):
        raise AssertionError("the derivation built a model runtime")

    monkeypatch.setattr(registry, "build_runtime", explode)
    assert _derive(monkeypatch, collection, tmp_path / "out") == 0


def test_a_collection_with_no_repo_sha_is_refused(
    monkeypatch, collection, tmp_path,
) -> None:
    """Provenance that names no code binds the artifact to nothing."""
    manifest = json.loads(collection["manifest"].read_text())
    manifest["identity"]["repo_sha"] = "unknown"
    forged = tmp_path / "manifest.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cli.CalibrationDerivationError, match="repo SHA"):
        _derive(monkeypatch, collection, tmp_path / "out",
                **{"--manifest": str(forged)})


def test_both_repo_shas_are_recorded(derived) -> None:
    provenance = json.loads(
        (derived / "m21_historical_bins.json").read_text())["provenance"]
    for field in ("collection_repo_sha", "derivation_repo_sha"):
        value = provenance[field]
        assert value and value != "unknown", field
        assert len(value) == 40, f"{field} is not a git SHA: {value!r}"
    assert provenance["derivation_repo_sha"] == FAKE_DERIVATION_SHA
    assert provenance["collection_repo_sha"] != FAKE_DERIVATION_SHA
