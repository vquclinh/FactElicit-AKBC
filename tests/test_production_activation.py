"""Production activation: F-11, F-22, F-24, and the VAL readiness contract.

The system has run in shadow for its whole life. These tests are about the one
transition that changes what the architecture *does*: Module 20 holding real
reservations and Module 21 actually choosing actions, on TRAIN-derived numbers.

Two failures are targeted throughout.

**A production run that is quietly still shadow.** A config can parse, a
pipeline can build, 478 rows can be answered - and Layer 6 can have governed
none of it. So the tests assert behaviour at the seam: the integration mode the
pipeline really holds, the planner really receiving a non-empty legal-action
list, Module 20 really screening before anything executes.

**A production run on numbers nobody derived.** Every fallback is a way for an
invented budget to look authoritative, so each one is asserted absent: no
synthetic source, no default bins, no empty history, no partial load.

No real model weights are loaded anywhere here, and no VAL or TEST row is read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control.budget_types import (
    CalibrationSource,
    RelationBudgetCalibration,
)
from cover_kbc.control.historical_bins import (
    HistoricalActionBin,
    HistoricalBinPackage,
    StateBinningSpec,
    SuccessorStat,
)
from cover_kbc.control.micro_planner import MicroPlannerConfig, build_micro_planner
from cover_kbc.control.planner_types import (
    ActionFamily,
    EstimateSource,
    PlannerCalibration,
)
from cover_kbc.control.relation_budget import (
    RelationBudgetConfig,
    build_relation_budget_scheduler,
    relation_policy,
)
from cover_kbc.controller_calibration.production import (
    ProductionCalibrationError,
    load_production_calibration,
)
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_validation_readiness,
)
from cover_kbc.integration_mode import IntegrationMode
from cover_kbc.paths import REPO_ROOT

VAL_CONFIG = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_validation.yaml"
COLLECTION_SHA = "264c980361a513078903526440c72adc6e10edaf"
DERIVATION_SHA = "78ad89d3cd8a321f500807b11477fce2f8579e32"


# --------------------------------------------------------------------------
# fixture artifacts, in the exact shape the derivation emits
# --------------------------------------------------------------------------


def _provenance() -> dict:
    return {
        "collection_repo_sha": COLLECTION_SHA,
        "derivation_repo_sha": DERIVATION_SHA,
        "train_sha256": "cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74",
        "telemetry_sha256": "fa95b30762a93537f7e03c87143ff6b7cfd71ff48eab80194d21089493b2b9ed",
        "derivation_schema_version": "train-calibration-v1",
    }


def _budget(relation: str) -> RelationBudgetCalibration:
    policy = relation_policy(relation)
    reserves = tuple((purpose, 1)
                     for purpose in policy.special_reserve_purposes[:1])
    return RelationBudgetCalibration(
        relation=relation, calibration_version="m20-derivation-v1",
        calibration_source=CalibrationSource.TRAIN_CALIBRATED,
        hard_calls=40, hard_generated_tokens=200,
        discovery_cap=8, verification_cap=24,
        verification_reserve=(4 if policy.verification_hard_reserved else 0),
        special_reserves=reserves,
    )


def _history() -> HistoricalBinPackage:
    """A fallback bin per relation and family, as the real package has."""
    families = (ActionFamily.SPECIALIST_VERIFY, ActionFamily.CANDIDATE_FREE_RECALL,
                ActionFamily.COUNTERFACTUAL_VERIFY, ActionFamily.REVERSE_CHECK,
                ActionFamily.SPECIALIST_PROBE, ActionFamily.PSEUDO_MEMORY_PROBE,
                ActionFamily.BLIND_VERIFY, ActionFamily.CROSS_MODEL_CHECK,
                ActionFamily.RESAMPLE)
    bins = [
        HistoricalActionBin(
            relation=relation, program_type=contract.program_type.value,
            state_bin_key="__fallback__", action_family=family,
            support_count=40,
            # A verify action earns its cost; a probe does not. The ranking
            # below is then a real preference rather than a coin flip.
            expected_verified_gain=(
                0.9 if family is ActionFamily.SPECIALIST_VERIFY else 0.1),
            expected_delta_r=0.2, expected_delta_h=0.0,
            expected_cost=2.0, expected_redundancy=0.1, expected_fp=0.05,
            # Depth-2 lookahead reads these from every bin it ranks, and the
            # real package carries 1570 observed transitions. A bin without
            # them is refused at load time rather than mid-run.
            successors=(SuccessorStat(probability=1.0,
                                      successor_state_bin="__fallback__"),),
        )
        for relation, contract in sorted(CONTRACTS.items())
        for family in families
    ]
    return HistoricalBinPackage(
        history_version="m21-derivation-v1",
        source=EstimateSource.TRAIN_CALIBRATED,
        binning=StateBinningSpec(
            spec_version="m21-state-binning-v1",
            categorical_features=("program_type",),
            numeric_boundaries=(("residual", (0.4, 0.6)),
                                ("unresolved_mass", (0.5, 1.0)))),
        bins=tuple(bins), minimum_bin_support=None,
        fallback_state_bin="__fallback__")


def _planner_calibration() -> PlannerCalibration:
    return PlannerCalibration(
        calibration_version="m21-derivation-v1",
        source=EstimateSource.TRAIN_CALIBRATED,
        alpha=1.0, beta=0.435, gamma=0.0, delta=0.006392, eta=0.01233,
        kappa=1.0, tau_continue=0.0, lookahead_depth=2)


def _write_artifacts(directory: Path) -> dict[str, Path]:
    """Serialise the three artifacts exactly as ``CalibrationBundle`` does."""
    directory.mkdir(parents=True, exist_ok=True)
    provenance = _provenance()
    payloads = {
        "m20_relation_budget.json": {
            "artifact": "m20-relation-budget", "provenance": provenance,
            "relations": [_budget(r).to_json() for r in sorted(CONTRACTS)],
        },
        "m21_historical_bins.json": {
            **_history().to_json(), "artifact": "m21-historical-bins",
            "provenance": provenance,
        },
        "m21_planner_calibration.json": {
            **_planner_calibration().to_json(),
            "artifact": "m21-planner-calibration", "provenance": provenance,
        },
    }
    out = {}
    for name, payload in payloads.items():
        target = directory / name
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        out[name] = target
    return out


@pytest.fixture
def artifacts(tmp_path) -> dict[str, Path]:
    return _write_artifacts(tmp_path / "calibration")


@pytest.fixture
def production_config(artifacts) -> dict:
    """The committed VAL config, repointed at the fixture artifacts.

    Everything else - modes, module enablement, model profile, provenance
    expectations - is the committed file's, unedited, so this exercises the real
    configuration rather than a convenient one.
    """
    config = yaml.safe_load(VAL_CONFIG.read_text())
    config["relation_budget_scheduler"].update({
        "calibration_file": str(artifacts["m20_relation_budget.json"]),
        "calibration_sha256": hashlib.sha256(
            artifacts["m20_relation_budget.json"].read_bytes()).hexdigest(),
    })
    config["micro_planner"].update({
        "historical_bins": str(artifacts["m21_historical_bins.json"]),
        "historical_bins_sha256": hashlib.sha256(
            artifacts["m21_historical_bins.json"].read_bytes()).hexdigest(),
        "planner_calibration": str(artifacts["m21_planner_calibration.json"]),
        "planner_calibration_sha256": hashlib.sha256(
            artifacts["m21_planner_calibration.json"].read_bytes()).hexdigest(),
    })
    return config


# ==========================================================================
# F-11 — production configuration
# ==========================================================================


def test_production_micro_planner_config_is_accepted() -> None:
    parsed = MicroPlannerConfig.from_mapping({
        "enabled": True, "mode": "production",
        "historical_bins": "bins.json", "planner_calibration": "cal.json"})
    assert parsed.is_production and parsed.enabled


def test_production_relation_budget_config_is_accepted() -> None:
    parsed = RelationBudgetConfig.from_mapping({
        "enabled": True, "mode": "production", "calibration_file": "m20.json"})
    assert parsed.is_production and parsed.enabled


def test_shadow_behaviour_is_unchanged() -> None:
    """The mode that has always existed must behave exactly as before."""
    planner = MicroPlannerConfig.from_mapping({"enabled": False})
    budget = RelationBudgetConfig.from_mapping({"enabled": False})
    assert planner.mode == "shadow" and not planner.is_production
    assert budget.mode == "shadow" and not budget.is_production
    assert build_micro_planner({"enabled": False}) is None
    assert build_relation_budget_scheduler({"enabled": False}) is None


@pytest.mark.parametrize("mode", ["degraded", "compatibility", "demo", "prod", ""])
def test_no_third_mode_exists(mode) -> None:
    """A "compatibility" mode is a budget that governs some calls and not others."""
    with pytest.raises(ValueError, match="unsupported"):
        MicroPlannerConfig.from_mapping({"mode": mode})
    with pytest.raises(ValueError, match="unsupported"):
        RelationBudgetConfig.from_mapping({"mode": mode})


def test_production_mode_requires_the_module_to_be_enabled() -> None:
    with pytest.raises(ValueError, match="production"):
        MicroPlannerConfig.from_mapping({"enabled": False, "mode": "production"})
    with pytest.raises(ValueError, match="production"):
        RelationBudgetConfig.from_mapping({"enabled": False, "mode": "production"})


def test_production_refuses_a_synthetic_calibration(
    production_config, tmp_path, artifacts,
) -> None:
    """A fixture must never be able to masquerade as derived policy."""
    payload = json.loads(artifacts["m21_planner_calibration.json"].read_text())
    payload["source"] = EstimateSource.SYNTHETIC_TEST.value
    artifacts["m21_planner_calibration.json"].write_text(json.dumps(payload))
    production_config["micro_planner"]["planner_calibration_sha256"] = None
    with pytest.raises(ProductionCalibrationError, match="test fixture"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_production_refuses_a_missing_calibration(
    production_config, tmp_path, artifacts,
) -> None:
    artifacts["m20_relation_budget.json"].unlink()
    with pytest.raises(ProductionCalibrationError, match="no artifact at"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_production_refuses_an_empty_history(
    production_config, tmp_path, artifacts,
) -> None:
    """No default bins: an empty package would price every action at nothing."""
    payload = json.loads(artifacts["m21_historical_bins.json"].read_text())
    payload["bins"] = []
    artifacts["m21_historical_bins.json"].write_text(json.dumps(payload))
    production_config["micro_planner"]["historical_bins_sha256"] = None
    with pytest.raises(ProductionCalibrationError, match="no bins"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_a_hash_mismatch_is_refused(production_config, tmp_path) -> None:
    production_config["relation_budget_scheduler"]["calibration_sha256"] = "0" * 64
    with pytest.raises(ProductionCalibrationError, match="hashes to"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_a_provenance_mismatch_is_refused(production_config, tmp_path) -> None:
    with pytest.raises(ProductionCalibrationError, match="derivation_repo_sha"):
        load_production_calibration(
            production_config, base_dir=tmp_path,
            expected_derivation_repo_sha="f" * 40)
    with pytest.raises(ProductionCalibrationError, match="collection_repo_sha"):
        load_production_calibration(
            production_config, base_dir=tmp_path,
            expected_collection_repo_sha="e" * 40)


def test_artifacts_from_different_derivations_are_refused(
    production_config, tmp_path, artifacts,
) -> None:
    """Three files that do not describe one calibration must not be mixed."""
    payload = json.loads(artifacts["m21_historical_bins.json"].read_text())
    payload["provenance"]["derivation_repo_sha"] = "a" * 40
    artifacts["m21_historical_bins.json"].write_text(json.dumps(payload))
    production_config["micro_planner"]["historical_bins_sha256"] = None
    with pytest.raises(ProductionCalibrationError, match="disagree on"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_an_artifact_without_provenance_is_refused(
    production_config, tmp_path, artifacts,
) -> None:
    payload = json.loads(artifacts["m20_relation_budget.json"].read_text())
    del payload["provenance"]
    artifacts["m20_relation_budget.json"].write_text(json.dumps(payload))
    production_config["relation_budget_scheduler"]["calibration_sha256"] = None
    with pytest.raises(ProductionCalibrationError, match="no provenance"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_a_shadow_config_cannot_load_production_calibration(
    production_config, tmp_path,
) -> None:
    production_config["micro_planner"]["mode"] = "shadow"
    with pytest.raises(ProductionCalibrationError, match="both must be"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_all_six_relations_load(production_config, tmp_path) -> None:
    calibration = load_production_calibration(production_config, base_dir=tmp_path)
    assert set(calibration.budgets) == set(CONTRACTS)
    assert len(calibration.budgets) == 6
    for relation, budget in calibration.budgets.items():
        assert budget.calibration_source is CalibrationSource.TRAIN_CALIBRATED
        assert budget.discovery_cap <= budget.hard_calls
        assert budget.verification_reserve <= budget.verification_cap
        declared = set(relation_policy(relation).special_reserve_purposes)
        assert {p for p, _ in budget.special_reserves} <= declared


def test_the_canonical_loaders_are_the_ones_used(
    production_config, tmp_path,
) -> None:
    """Not a re-parse: the objects must be the owners' own types."""
    calibration = load_production_calibration(production_config, base_dir=tmp_path)
    assert isinstance(calibration.history, HistoricalBinPackage)
    assert isinstance(calibration.planner, PlannerCalibration)
    assert all(isinstance(b, RelationBudgetCalibration)
               for b in calibration.budgets.values())
    assert calibration.history.source is EstimateSource.TRAIN_CALIBRATED
    assert calibration.planner.source is EstimateSource.TRAIN_CALIBRATED


def test_loading_needs_no_train_data(
    production_config, tmp_path, monkeypatch,
) -> None:
    """The three artifacts are the only TRAIN-derived input production needs."""
    import cover_kbc.data.loader as loader

    monkeypatch.setattr(loader, "load_dataset", _explode("loading read a split"))
    calibration = load_production_calibration(production_config, base_dir=tmp_path)
    assert calibration.budgets
    for path in calibration.paths.values():
        assert "train.jsonl" not in path
        assert "telemetry" not in path
        assert "manifest" not in path


def _explode(message: str):
    def boom(*_args, **_kwargs):
        raise AssertionError(message)
    return boom


# ==========================================================================
# VAL config and readiness
# ==========================================================================


def test_the_committed_val_config_declares_val_and_production() -> None:
    config = yaml.safe_load(VAL_CONFIG.read_text())
    assert config["experiment"]["split"] == "val"
    assert config["relation_budget_scheduler"]["mode"] == "production"
    assert config["micro_planner"]["mode"] == "production"
    assert config["layer6_integration"]["enabled"] is True
    provenance = config["calibration_provenance"]
    assert provenance["collection_repo_sha"] == COLLECTION_SHA
    assert provenance["derivation_repo_sha"] == DERIVATION_SHA


def test_the_committed_val_config_keeps_the_frozen_profile() -> None:
    """Calibration derived from one system may not be applied to another."""
    validation = yaml.safe_load(VAL_CONFIG.read_text())
    collection = yaml.safe_load(
        (REPO_ROOT / "configs" / "experiments"
         / "cover_kbc_v2_train_collection.yaml").read_text())
    assert validation["model_profile"] == collection["model_profile"]
    assert validation["budget_assertion"] == collection["budget_assertion"]
    for block in ("scoring", "selection", "controller"):
        assert validation["pipeline"][block] == collection["pipeline"][block]
    assert (validation["pipeline"]["max_control_rounds_per_catalogue"]
            == collection["pipeline"]["max_control_rounds_per_catalogue"])
    # The execution mode is part of the profile: the bins and envelopes
    # describe the system that produced them, and interleaved and staged are
    # not the same system (Audit 0052).
    assert validation["pipeline"]["mode"] == collection["pipeline"]["mode"]


def test_the_val_split_identity_is_declared_and_correct() -> None:
    """478 rows, and the exact snapshot this milestone was written against."""
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    val = REPO_ROOT / "benchmark" / "data" / "val.jsonl"
    rows = [line for line in val.read_text().splitlines() if line.strip()]
    assert len(rows) == provenance["val_rows"] == 478
    assert hashlib.sha256(val.read_bytes()).hexdigest() == provenance["val_sha256"]


def test_the_val_config_reaches_full_validation_ready(
    production_config, tmp_path,
) -> None:
    """The gate's whole point: READY only when everything really is."""
    report = evaluate_validation_readiness(
        production_config, base_dir=tmp_path, split="val",
        expected_collection_repo_sha=COLLECTION_SHA,
        expected_derivation_repo_sha=DERIVATION_SHA)
    assert report.state is ReadinessState.FULL_VALIDATION_READY, report.blockers
    assert report.may_run_validation
    assert report.details["calibration"]["relations"] == sorted(CONTRACTS)


@pytest.mark.parametrize("block,changes,expected", [
    ("experiment", {"split": "train"}, "split"),
    ("relation_budget_scheduler", {"mode": "shadow"}, "production"),
    ("micro_planner", {"mode": "shadow"}, "production"),
    ("layer6_integration", {"enabled": False}, "Layer-6"),
    ("coverage_gap", {"enabled": False}, "M19"),
    ("consensus", {"enabled": False}, "M16"),
    ("specialist_verifier", {"enabled": False}, "M17"),
])
def test_readiness_refuses_an_incomplete_production_profile(
    production_config, tmp_path, block, changes, expected,
) -> None:
    production_config.setdefault(block, {}).update(changes)
    report = evaluate_validation_readiness(
        production_config, base_dir=tmp_path,
        split=production_config["experiment"]["split"])
    assert report.state is not ReadinessState.FULL_VALIDATION_READY
    assert any(expected in blocker for blocker in report.blockers), report.blockers


def test_readiness_refuses_when_the_artifacts_are_absent() -> None:
    """The committed config, as shipped, until the real files are placed."""
    config = yaml.safe_load(VAL_CONFIG.read_text())
    report = evaluate_validation_readiness(
        config, base_dir=VAL_CONFIG.parent, split="val")
    if (REPO_ROOT / "configs" / "calibration"
            / "m20_relation_budget.json").is_file():
        pytest.skip("the real artifacts are present in this checkout")
    assert report.state is ReadinessState.NOT_READY
    assert any("does not exist" in b or "no artifact at" in b
               for b in report.blockers), report.blockers


# ==========================================================================
# F-22 / F-24 — the real production graph
# ==========================================================================


def _scripted_runtime(config):
    from cover_kbc.models.offline import ScriptedRuntime

    answers = {
        "hasArea": "45000", "hasCapacity": "45000",
        "personHasCityOfDeath": "Paris",
        "companyTradesAtStockExchange": "NASDAQ; New York Stock Exchange",
        "awardWonBy": "Marie Curie; Albert Einstein",
        "countryLandBordersCountry": "Spain; Andorra; Monaco",
    }
    role = str(config.get("role", "enumerator"))
    return ScriptedRuntime(
        model_id=f"offline/{role}", role=role,
        family=str(config.get("family", "offline")),
        fallback=lambda request: answers.get(
            str(request.metadata.get("relation", "")), "NONE"))


def _build_production_pipeline(config, base_dir):
    """The real production graph, with scripted runtimes in place of weights."""
    from cover_kbc.control.layer6_integration import Layer6Integrator
    from cover_kbc.coverage_gap.missingness import build_coverage_gap_estimator
    from cover_kbc.evidence.consensus import build_consensus_engine
    from cover_kbc.evidence.layer4 import build_layer4_integrator
    from cover_kbc.models.registry import model_blocks
    from cover_kbc.pipeline import CoverPipeline, ExecutionMode, PipelineConfig
    from cover_kbc.query_intelligence import (
        build_parametric_retriever, build_profiler, build_prompt_compiler)
    from cover_kbc.specialists import (
        build_large_set_specialist, build_null_temporal_specialist,
        build_numeric_specialist, build_small_set_specialist)
    from cover_kbc.verification.bidirectional_verifier import (
        build_bidirectional_verifier)
    from cover_kbc.verification.specialist_verifier import (
        build_specialist_verifier)

    calibration = load_production_calibration(config, base_dir=base_dir)
    enumerator_cfg, verifier_cfg = model_blocks(config)
    runtime = _scripted_runtime(enumerator_cfg)
    verifier_runtime = _scripted_runtime(verifier_cfg)

    block = dict(config.get("pipeline", {}))
    block["mode"] = ExecutionMode.INTERLEAVED.value
    pipeline_config = PipelineConfig.from_mapping(block)
    profiler = build_profiler(config.get("query_intelligence"))
    compiler = build_prompt_compiler(
        config.get("query_intelligence"), profiler_enabled=profiler is not None)
    retriever = build_parametric_retriever(
        config.get("query_intelligence"), profiler_enabled=profiler is not None,
        compiler_enabled=compiler is not None)
    kw = dict(profiler_enabled=profiler is not None,
              compiler_enabled=compiler is not None,
              retrieval_enabled=retriever is not None)
    specialists = config.get("specialists")
    consensus = build_consensus_engine(
        config.get("consensus"), **kw,
        available_specialists={"M12": True, "M13": True, "M14": True,
                               "M15": True},
        relations=tuple(sorted(CONTRACTS)))
    planner = build_micro_planner(
        config.get("micro_planner"), calibration.history, calibration.planner)
    pipeline = CoverPipeline(
        runtime, pipeline_config, verifier_runtime=verifier_runtime,
        profiler=profiler, prompt_compiler=compiler, retriever=retriever,
        numeric_specialist=build_numeric_specialist(specialists, **kw),
        large_set_specialist=build_large_set_specialist(specialists, **kw),
        null_temporal_specialist=build_null_temporal_specialist(
            specialists, **kw),
        small_set_specialist=build_small_set_specialist(specialists, **kw),
        consensus_engine=consensus,
        specialist_verifier=build_specialist_verifier(
            config.get("specialist_verifier"), consensus_enabled=True,
            verifier_available=True),
        bidirectional_verifier=build_bidirectional_verifier(
            config.get("bidirectional_verification"), consensus_enabled=True),
        layer4_integrator=build_layer4_integrator(
            config.get("layer4_integration"), consensus_enabled=True),
        coverage_gap_estimator=build_coverage_gap_estimator(
            config.get("coverage_gap"), layer4_enabled=True),
        relation_budget_scheduler=build_relation_budget_scheduler(
            config.get("relation_budget_scheduler"), calibration.budgets),
        micro_planner=planner,
        layer6_integrator=Layer6Integrator(planner),
        integration_mode=IntegrationMode.PRODUCTION,
    )
    return pipeline, calibration


@pytest.fixture
def production_pipeline(production_config, tmp_path):
    return _build_production_pipeline(production_config, tmp_path)


def _run_one(pipeline, subject="Portugal",
             relation="countryLandBordersCountry"):
    """One query through the production graph. Literal strings, no split read."""
    from cover_kbc.types import Query

    graph = pipeline.enumerate_query(
        Query(subject=subject, relation=relation, row_index=0))
    return graph, pipeline.decide_graph(graph)


def test_the_production_pipeline_really_holds_production_mode(
    production_pipeline,
) -> None:
    pipeline, _ = production_pipeline
    assert pipeline.integration_mode is IntegrationMode.PRODUCTION
    assert pipeline.integration_mode.is_production
    assert pipeline.integration_mode.may_mutate_production_state


def test_the_layer6_integrator_is_supplied(production_pipeline) -> None:
    """F-22: without it Module 21 can only answer STOP/NO_LEGAL_ACTION."""
    from cover_kbc.control.layer6_integration import Layer6Integrator

    pipeline, _ = production_pipeline
    assert isinstance(pipeline.layer6_integrator, Layer6Integrator)
    assert pipeline.layer6_integrator.planner is pipeline.micro_planner


def test_module_20_and_21_are_both_live(production_pipeline) -> None:
    from cover_kbc.control.micro_planner import MicroPlanner
    from cover_kbc.control.relation_budget import RelationBudgetScheduler

    pipeline, calibration = production_pipeline
    assert isinstance(pipeline.relation_budget_scheduler, RelationBudgetScheduler)
    assert isinstance(pipeline.micro_planner, MicroPlanner)
    assert set(pipeline.relation_budget_scheduler.calibrations) == set(CONTRACTS)
    assert pipeline.micro_planner.history is calibration.history
    assert pipeline.micro_planner.calibration is calibration.planner


def test_module_21_receives_a_real_legal_action_list(production_pipeline) -> None:
    """F-22, precisely: the planner is handed the owners' real catalogue.

    Before the integrator was supplied this list was always empty and the only
    possible answer was STOP/NO_LEGAL_ACTION. Asserted separately from
    *selection*, because the two fail for different reasons: an empty list is a
    wiring defect, an unaffordable list is a budget one.
    """
    pipeline, _ = production_pipeline
    _run_one(pipeline)

    decisions = pipeline.micro_planner_results
    assert decisions, "Module 21 was never consulted"
    with_actions = [d for d in decisions if d.legal_actions]
    assert with_actions, (
        "Module 21 saw no legal action in any round; the planner is still being "
        "handed an empty list")
    assert max(len(d.legal_actions) for d in decisions) > 1
    for decision in decisions:
        assert decision.tau_continue == 0.0
        stop = getattr(decision.stop_reason, "value", decision.stop_reason)
        assert stop != "NO_LEGAL_ACTION", (
            "the planner still reports NO_LEGAL_ACTION, which is what a missing "
            "Layer-6 integrator produces")


def test_module_21_selects_and_the_selection_clears_tau(
    production_config, tmp_path,
) -> None:
    """F-24 end to end: a real ACTION decision over real legal actions.

    ``awardWonBy`` is used because it is the one relation whose core per-query
    budget leaves room for a Module 17 reading set - see
    ``test_the_core_budget_currently_starves_layer_4_on_four_relations``, which
    pins the reason the others cannot.
    """
    pipeline, _ = _build_production_pipeline(production_config, tmp_path)
    _run_one(pipeline, subject="Nobel Prize in Physics", relation="awardWonBy")
    chosen = [d for d in pipeline.micro_planner_results
              if getattr(d.kind, "value", d.kind) == "ACTION"]
    assert chosen, "Module 21 ranked legal actions but never selected one"
    assert chosen[0].selected_action
    assert chosen[0].selected_value > chosen[0].tau_continue
    assert chosen[0].utilities, "no utility breakdown was recorded"


def test_the_core_budget_currently_starves_layer_4_on_four_relations(
    production_config, tmp_path,
) -> None:
    """A known, measured production state - pinned so it cannot drift silently.

    ``build_plan`` intersects the TRAIN-derived envelope with Module 7's own
    per-query budget, and for four relations that budget is 4 calls. One Module
    17 action reserves 4 non-cacheable readings (two phrasings x two label
    orders), while a protected special reserve withholds one call from any
    other purpose - so 4 requested against 3 available is denied, and Module 21
    correctly answers NO_AFFORDABLE_ACTION.

    This is Table 6 and §9.3 behaving as designed against a core budget that was
    never binding during collection, where Module 20 was bypassed. It is
    recorded as a finding rather than fixed here: raising the core budget or
    changing what the envelope intersects is an ownership decision, not a test
    fixture's to make.
    """
    starved = {"countryLandBordersCountry": "Portugal",
               "hasArea": "Wellington Island", "hasCapacity": "Some Stadium",
               "personHasCityOfDeath": "Someone"}
    for relation, subject in sorted(starved.items()):
        pipeline, _ = _build_production_pipeline(production_config, tmp_path)
        graph = pipeline.enumerate_query(
            __import__("cover_kbc.types", fromlist=["Query"]).Query(
                subject=subject, relation=relation, row_index=0))
        assert pipeline.config.budget(graph.contract).max_calls == 4
        pipeline.decide_graph(graph)
        reasons = {getattr(d.stop_reason, "value", d.stop_reason)
                   for d in pipeline.micro_planner_results}
        assert "NO_AFFORDABLE_ACTION" in reasons, (relation, reasons)
        # Legality was never the problem: the actions existed and were priced.
        assert any(d.legal_actions for d in pipeline.micro_planner_results)


def test_actions_executed_in_production_are_module_21s_choices(
    production_config, tmp_path,
) -> None:
    """The executing seam must be the planner's, not a collection policy's."""
    pipeline, _ = _build_production_pipeline(production_config, tmp_path)
    _run_one(pipeline, subject="Nobel Prize in Physics", relation="awardWonBy")
    executed = [r for r in pipeline.action_records if r["executed"]]
    assert executed, "no action executed in the production path"
    selected = {d.selected_action for d in pipeline.micro_planner_results
                if getattr(d.kind, "value", d.kind) == "ACTION"}
    for record in executed:
        projection = record.get("projection")
        assert projection is not None
        assert projection.action_id in selected, (
            f"{projection.action_id} executed but Module 21 never selected it")


def test_the_collection_policy_is_absent_from_production(
    production_pipeline,
) -> None:
    """Production choice belongs to Module 21, not to the bootstrap selector."""
    pipeline, _ = production_pipeline
    assert pipeline.action_selector is None


def test_module_20_screens_every_executed_action(production_pipeline) -> None:
    """An action that is not budget-legal must never reach a runtime."""
    pipeline, _ = production_pipeline
    graph, _ = _run_one(pipeline)
    assert pipeline._budget_ledger_for(graph) is not None, (
        "production must hold a real Module 20 ledger")
    for record in pipeline.action_records:
        if not record["executed"]:
            assert record["cost"]["physical_calls"] == 0, (
                "a refused action still spent calls")


def test_a_budget_denial_prevents_execution(production_pipeline) -> None:
    """Refusal is asserted by returning before a runtime is touched."""
    pipeline, _ = production_pipeline
    before = pipeline.physical_snapshot()
    original = pipeline._precharge
    pipeline._precharge = lambda kind, action, graph: (
        False, "Module 20 denied: forced for this test", None)
    try:
        _run_one(pipeline)
    finally:
        pipeline._precharge = original
    executed = [r for r in pipeline.action_records if r["executed"]]
    assert not executed, "an action ran despite Module 20 refusing it"
    after = pipeline.physical_snapshot()
    # Acquisition still happened; no Layer-4 action did.
    for record in pipeline.action_records:
        assert record["cost"]["physical_calls"] == 0
    del before, after


def test_the_sparse_bin_fallback_stays_active(production_pipeline) -> None:
    """A legal action must not vanish because its exact state bin is sparse."""
    _, calibration = production_pipeline
    assert calibration.history.fallback_state_bin == "__fallback__"
    entry = calibration.history.lookup(
        relation="countryLandBordersCountry", program_type="SMALL_SET",
        state_bin_key="a state no query ever reached",
        family=ActionFamily.SPECIALIST_VERIFY)
    assert entry.state_bin_key == "__fallback__"


def test_strict_continuation_is_preserved(production_pipeline) -> None:
    """§17 stops on equality; this must never become >=."""
    import inspect

    from cover_kbc.control import micro_planner as module

    source = inspect.getsource(module)
    assert ">= self.calibration.tau_continue" not in source
    assert "> self.calibration.tau_continue" in source
    _, calibration = production_pipeline
    assert calibration.planner.tau_continue == 0.0


def test_the_configured_lookahead_depth_is_used(production_pipeline) -> None:
    pipeline, calibration = production_pipeline
    assert calibration.planner.lookahead_depth == 2
    assert pipeline.micro_planner.calibration.lookahead_depth == 2


def test_depth_two_without_successor_statistics_is_refused(
    production_config, tmp_path, artifacts,
) -> None:
    """Otherwise Module 21 raises at a random row, hours into a 478-row run.

    ``_lookahead`` reads ``successors`` from the bin of every action it ranks.
    A package asking for depth 2 while some bin observed no transition is a
    package that fails mid-run, so it is refused at load.
    """
    payload = json.loads(artifacts["m21_historical_bins.json"].read_text())
    payload["bins"][0]["successors"] = []
    artifacts["m21_historical_bins.json"].write_text(json.dumps(payload))
    production_config["micro_planner"]["historical_bins_sha256"] = None
    with pytest.raises(ProductionCalibrationError, match="no successor"):
        load_production_calibration(production_config, base_dir=tmp_path)


def test_depth_one_does_not_need_successor_statistics(
    production_config, tmp_path, artifacts,
) -> None:
    """The refusal is about depth 2 specifically, not about sparse history."""
    bins = json.loads(artifacts["m21_historical_bins.json"].read_text())
    for entry in bins["bins"]:
        entry["successors"] = []
    artifacts["m21_historical_bins.json"].write_text(json.dumps(bins))
    calibration = json.loads(
        artifacts["m21_planner_calibration.json"].read_text())
    calibration["lookahead_depth"] = 1
    artifacts["m21_planner_calibration.json"].write_text(
        json.dumps(calibration))
    production_config["micro_planner"]["historical_bins_sha256"] = None
    production_config["micro_planner"]["planner_calibration_sha256"] = None
    loaded = load_production_calibration(production_config, base_dir=tmp_path)
    assert loaded.planner.lookahead_depth == 1


def test_m8_remains_the_sole_output_owner(production_pipeline) -> None:
    pipeline, _ = production_pipeline
    _, prediction = _run_one(pipeline)
    assert isinstance(prediction.object_entities, (list, tuple))
    for decision in pipeline.micro_planner_results:
        assert not hasattr(decision, "object_entities")
    for report in pipeline.bridge_reports:
        assert not hasattr(report, "object_entities")


def test_physical_accounting_is_one_call_per_forward_pass(
    production_pipeline,
) -> None:
    pipeline, _ = production_pipeline
    before = pipeline.physical_snapshot()
    _run_one(pipeline)
    after = pipeline.physical_snapshot()
    delta = pipeline.physical_delta(before, after)
    assert delta["physical_calls"] == (
        delta["enumerator_calls"] + delta["verifier_calls"])
    assert delta["physical_calls"] == (
        pipeline.runtime.calls + pipeline.verifier_runtime.calls)


def test_every_relation_runs_through_the_production_graph(
    production_config, tmp_path,
) -> None:
    """All six, so no relation is only ever exercised in shadow."""
    subjects = {
        "countryLandBordersCountry": "Portugal", "hasArea": "Wellington Island",
        "hasCapacity": "Some Stadium", "awardWonBy": "Some Prize",
        "personHasCityOfDeath": "Someone", "companyTradesAtStockExchange": "Acme",
    }
    for relation, subject in sorted(subjects.items()):
        pipeline, _ = _build_production_pipeline(production_config, tmp_path)
        _, prediction = _run_one(pipeline, subject=subject, relation=relation)
        assert prediction.relation == relation
        assert isinstance(prediction.object_entities, (list, tuple))


# ==========================================================================
# closed book
# ==========================================================================


def test_the_production_path_reads_no_dataset_split(
    production_config, tmp_path, monkeypatch,
) -> None:
    """Structural: make reading a split fatal, then run the production graph."""
    import cover_kbc.data.loader as loader

    monkeypatch.setattr(
        loader, "load_dataset", _explode("the production path read a split"))
    pipeline, _ = _build_production_pipeline(production_config, tmp_path)
    _run_one(pipeline)


def test_no_network_or_external_corpus_in_the_production_modules() -> None:
    forbidden = ("requests", "urllib", "httpx", "aiohttp", "wikipedia",
                 "wikidata", "bm25", "faiss", "chromadb", "elasticsearch")
    for relative in ("controller_calibration/production.py",
                     "control/layer6_integration.py",
                     "control/micro_planner.py",
                     "control/relation_budget.py"):
        source = (REPO_ROOT / "src" / "cover_kbc" / relative).read_text().casefold()
        for name in forbidden:
            assert f"import {name}" not in source, f"{relative}: {name}"


def test_no_training_in_the_production_modules() -> None:
    forbidden = ("torch.optim", ".backward(", "requires_grad", "lora", "peft",
                 "optimizer")
    for relative in ("controller_calibration/production.py",
                     "control/layer6_integration.py",
                     "control/micro_planner.py"):
        source = (REPO_ROOT / "src" / "cover_kbc" / relative).read_text().casefold()
        for name in forbidden:
            assert name not in source, f"{relative}: {name}"


def test_the_parameter_budget_still_holds() -> None:
    from cover_kbc.models.budget import audit_parameter_budget
    from cover_kbc.models.registry import model_blocks, spec_from_config

    config = yaml.safe_load(VAL_CONFIG.read_text())
    enumerator, verifier = model_blocks(config)
    audit = audit_parameter_budget(
        [spec_from_config(enumerator), spec_from_config(verifier)])
    assert audit.passed
    assert enumerator["model_id"] == "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    assert enumerator["revision"] == "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
    assert verifier["model_id"] == "Qwen/Qwen3.5-4B"
    assert verifier["revision"] == "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"


def test_no_alternate_model_is_introduced() -> None:
    config = yaml.safe_load(VAL_CONFIG.read_text())
    assert set(config["model_profile"]) == {"enumerator", "verifier"}


# ==========================================================================
# VAL output contract, structurally
# ==========================================================================


def test_one_prediction_per_query_is_enforced_by_the_writer(tmp_path) -> None:
    """The runner's writer already owns this; assert it actually refuses."""
    from cover_kbc.data.writer import write_predictions
    from cover_kbc.types import EmptyReason, Prediction, Query

    queries = [Query(subject=f"S{i}", relation="hasArea", row_index=i)
               for i in range(3)]
    predictions = [
        Prediction(subject=q.subject, relation=q.relation, row_index=q.row_index,
                   object_entities=[],
                   empty_reason=EmptyReason.NO_CANDIDATE_GENERATED)
        for q in queries
    ]
    path = write_predictions(predictions, tmp_path / "p.jsonl",
                             expected_queries=queries)
    rows = [json.loads(line)
            for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) == len(queries)
    assert [r["SubjectEntity"] for r in rows] == [q.subject for q in queries]
    assert all(isinstance(r["ObjectEntities"], list) for r in rows)

    with pytest.raises(Exception):
        write_predictions(predictions[:2], tmp_path / "short.jsonl",
                          expected_queries=queries)


def test_an_empty_prediction_stays_legal_and_reasoned() -> None:
    """Empty is a decision with a reason, never a silent default."""
    from cover_kbc.types import EmptyReason, Prediction

    prediction = Prediction(
        subject="S", relation="personHasCityOfDeath", row_index=0,
        object_entities=[], empty_reason=EmptyReason.UNRESOLVED_ABSTENTION)
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.UNRESOLVED_ABSTENTION
    # UNKNOWN and failed recall keep their own reasons rather than collapsing.
    assert (EmptyReason.UNRESOLVED_ABSTENTION
            is not EmptyReason.NO_CANDIDATE_GENERATED)
    assert (EmptyReason.CONFIDENT_NEGATIVE_GATE
            is not EmptyReason.NO_CANDIDATE_GENERATED)


# ==========================================================================
# F-24 — the entrypoint itself
# ==========================================================================


def test_the_entrypoint_constructs_production_mode(
    production_config, tmp_path, monkeypatch, capsys,
) -> None:
    """``run_cover.py`` end to end, with the split replaced by literal rows.

    The real entrypoint, the real readiness gate, the real calibration loader
    and the real production assembly - only the runtimes and the dataset are
    substituted, because this milestone may neither load 28.67B of weights nor
    read the VAL split. What is asserted is the thing F-24 is about: the
    pipeline the entrypoint actually built is in ``IntegrationMode.PRODUCTION``
    with Layer 6 supplied.
    """
    import importlib.util
    import sys

    from cover_kbc.types import Query

    spec = importlib.util.spec_from_file_location(
        "run_cover", REPO_ROOT / "scripts" / "run_cover.py")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    config_path = tmp_path / "production.yaml"
    config_path.write_text(yaml.safe_dump(production_config), encoding="utf-8")

    class _Dataset:
        """Two literal queries. No split file is opened."""

        sha256 = "0" * 64
        path = tmp_path / "not-a-split.jsonl"
        is_blind = True
        rows = ()

        def __len__(self) -> int:
            return 2

        def queries(self):
            return [
                Query(subject="Nobel Prize in Physics", relation="awardWonBy",
                      row_index=0),
                Query(subject="Nobel Prize in Chemistry", relation="awardWonBy",
                      row_index=1),
            ]

    built: dict[str, object] = {}
    real_pipeline = runner.CoverPipeline

    class _Capturing(real_pipeline):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built["pipeline"] = self

    monkeypatch.setattr(runner, "load_dataset", lambda *_a, **_k: _Dataset())
    monkeypatch.setattr(runner, "build_runtime", _scripted_runtime)
    monkeypatch.setattr(runner, "CoverPipeline", _Capturing)
    monkeypatch.setattr(sys, "argv", [
        "run_cover.py", "--config", str(config_path),
        "--output-dir", str(tmp_path / "out"), "--no-eval"])

    assert runner.main() == 0

    pipeline = built["pipeline"]
    assert pipeline.integration_mode is IntegrationMode.PRODUCTION
    assert pipeline.layer6_integrator is not None
    assert pipeline.micro_planner is not None
    assert pipeline.relation_budget_scheduler is not None
    assert set(pipeline.relation_budget_scheduler.calibrations) == set(CONTRACTS)

    printed = capsys.readouterr().out
    assert "FULL_VALIDATION_READY" in printed
    assert "calibration :" in printed

    # One prediction row per query, in order, with a list of objects.
    rows = [json.loads(line) for line
            in (tmp_path / "out" / "predictions.jsonl").read_text().splitlines()
            if line.strip()]
    assert len(rows) == 2
    assert [r["SubjectEntity"] for r in rows] == [
        "Nobel Prize in Physics", "Nobel Prize in Chemistry"]
    assert all(isinstance(r["ObjectEntities"], list) for r in rows)
    sys.path.remove(str(REPO_ROOT / "scripts"))


def test_the_entrypoint_refuses_a_production_config_that_is_not_ready(
    production_config, tmp_path, monkeypatch,
) -> None:
    """A production config whose artifacts are gone must not fall back."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "run_cover_refuse", REPO_ROOT / "scripts" / "run_cover.py")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    Path(production_config["relation_budget_scheduler"]
         ["calibration_file"]).unlink()
    config_path = tmp_path / "broken.yaml"
    config_path.write_text(yaml.safe_dump(production_config), encoding="utf-8")

    monkeypatch.setattr(runner, "build_runtime", _scripted_runtime)
    monkeypatch.setattr(sys, "argv", [
        "run_cover.py", "--config", str(config_path),
        "--output-dir", str(tmp_path / "out"), "--no-eval"])
    with pytest.raises(SystemExit, match="FULL_VALIDATION_READY"):
        runner.main()
    sys.path.remove(str(REPO_ROOT / "scripts"))


def test_a_shadow_config_still_runs_in_shadow(tmp_path, monkeypatch) -> None:
    """The mode that has always existed must not have been changed underneath."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "run_cover_shadow", REPO_ROOT / "scripts" / "run_cover.py")
    runner = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec.loader.exec_module(runner)
    config = yaml.safe_load(
        (REPO_ROOT / "configs" / "experiments"
         / "cover_kbc_v2_train_collection.yaml").read_text())
    assert runner._wants_production(config) is False
    sys.path.remove(str(REPO_ROOT / "scripts"))
