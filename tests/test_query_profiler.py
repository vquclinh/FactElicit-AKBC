"""Module 9 - Risk & Difficulty Profiler conformance.

Three things have to hold:

* the profiler is deterministic, closed-book and free (§ determinism, cost);
* it consumes Module 1 and cannot override it (§ no duplicate router);
* enabling it changes nothing about what the system predicts (§ shadow mode).

The last is load-bearing and is tested the same way Audit 0015 tested progress
logging: run the real staged CLI twice over the scripted backend, once with M9
on and once with it off, and compare every prediction artefact byte for byte.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS, UnknownRelationError
from cover_kbc.contracts.router import PROGRAM_BY_RELATION, compile_query
from cover_kbc.query_intelligence import (
    PROFILE_VERSION,
    RISK_AXES,
    CardinalityRegime,
    ProfilerConfig,
    QueryProfiler,
    QueryRiskProfile,
    RiskLevel,
    SpecialistHint,
    UnknownRelationPriorError,
    build_profiler,
    check_priors_consistency,
    get_priors,
    subject_surface_features,
)
from cover_kbc.types import ProgramType, Query

BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
AREA = "hasArea"
AWARD = "awardWonBy"
ALL_RELATIONS = (BORDERS, STOCK, DEATH, CAPACITY, AREA, AWARD)

CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
ARTEFACTS = (
    "predictions.jsonl",
    "diagnostics.json",
    "trace.jsonl",
    "stage_a_enumerated.jsonl",
    "stage_b_verified.jsonl",
)


@pytest.fixture
def profiler():
    return QueryProfiler()


def _profile(profiler, subject: str, relation: str, row_index: int = 0) -> QueryRiskProfile:
    query, contract = compile_query(subject, relation, row_index)
    return profiler.profile(query, contract)


# --------------------------------------------------------------------------
# 1. Determinism
# --------------------------------------------------------------------------


def test_the_same_query_profiles_identically(profiler):
    first = _profile(profiler, "Testland", BORDERS)
    second = _profile(profiler, "Testland", BORDERS)
    assert first == second
    assert first.to_json() == second.to_json()


def test_determinism_holds_across_profiler_instances():
    a = QueryProfiler().profile(Query("Testland", BORDERS, 0))
    b = QueryProfiler().profile(Query("Testland", BORDERS, 0))
    assert a == b


def test_profiling_is_independent_of_seed_and_call_order(profiler):
    import random

    random.seed(1)
    first = _profile(profiler, "Testville", DEATH)
    for relation in ALL_RELATIONS:            # exercise every other relation
        _profile(profiler, "Other", relation)
    random.seed(999)
    assert _profile(profiler, "Testville", DEATH) == first


# --------------------------------------------------------------------------
# 2. Zero neural cost
# --------------------------------------------------------------------------


def test_profiling_loads_no_model_backend_at_all(tmp_path):
    """Importing and running M9 must not pull in a runtime module.

    Run in a subprocess with a clean interpreter, because the test session has
    already imported half the package for other reasons and would hide this.
    """
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        "from cover_kbc.query_intelligence import QueryProfiler\n"
        "from cover_kbc.types import Query\n"
        "QueryProfiler().profile_all([\n"
        + "".join(f"    Query('S', {r!r}, 0),\n" for r in ALL_RELATIONS)
        + "])\n"
        "loaded = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m in ('torch', 'transformers', 'mistral_common', 'requests', 'urllib.request')\n"
        "    or m.startswith('cover_kbc.models')\n"
        ")\n"
        "print(','.join(loaded))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == "", f"M9 loaded {result.stdout.strip()}"


def test_profiling_spends_no_calls_or_tokens():
    from cover_kbc.models.offline import ScriptedRuntime

    runtime = ScriptedRuntime({}, model_id="offline/scripted")
    before = (runtime.calls, runtime.generated_tokens)
    QueryProfiler().profile_all(
        [Query(f"S{i}", relation, i) for i, relation in enumerate(ALL_RELATIONS)]
    )
    assert (runtime.calls, runtime.generated_tokens) == before == (0, 0)


def test_the_query_intelligence_package_imports_no_model_backend():
    """Structural guarantee, not just a behavioural one."""
    root = Path("src/cover_kbc/query_intelligence")
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("models.registry", "models.huggingface", "LMRuntime", "requests", "urllib"):
            assert forbidden not in source, f"{path.name} references {forbidden}"


# --------------------------------------------------------------------------
# 3. Six-relation coverage
# --------------------------------------------------------------------------


def test_every_official_relation_profiles(profiler):
    for relation in ALL_RELATIONS:
        profile = _profile(profiler, "Subject", relation)
        assert profile.relation == relation
        assert profile.profile_version == PROFILE_VERSION
        assert set(profile.axes()) == set(RISK_AXES)
        assert all(isinstance(level, RiskLevel) for level in profile.axes().values())


def test_priors_are_declared_for_exactly_the_contracted_relations():
    check_priors_consistency()
    assert set(ALL_RELATIONS) == set(CONTRACTS)


# --------------------------------------------------------------------------
# 4-5. Programme consistency; no duplicate router
# --------------------------------------------------------------------------


def test_program_type_always_agrees_with_module_1(profiler):
    for relation in ALL_RELATIONS:
        profile = _profile(profiler, "Subject", relation)
        assert profile.program_type is PROGRAM_BY_RELATION[relation]
        assert profile.program_type is CONTRACTS[relation].program_type


def test_cardinality_regime_is_a_total_function_of_the_program(profiler):
    expected = {
        ProgramType.SMALL_SET: CardinalityRegime.SMALL_SET,
        ProgramType.NULL_SINGLE: CardinalityRegime.ZERO_OR_ONE,
        ProgramType.NUMERIC: CardinalityRegime.NUMERIC_SINGLE,
        ProgramType.LARGE_OPEN_SET: CardinalityRegime.LARGE_OPEN_SET,
    }
    for relation in ALL_RELATIONS:
        profile = _profile(profiler, "Subject", relation)
        assert profile.cardinality_regime is expected[profile.program_type]


def test_the_profiler_cannot_override_the_routed_program(profiler):
    """A disagreeing programme is an error, never a second opinion."""
    from cover_kbc.contracts.programs import get_program

    query, contract = compile_query("Subject", CAPACITY, 0)     # routed NUMERIC
    with pytest.raises(ValueError, match="cannot override"):
        profiler.profile(query, contract, get_program(ProgramType.LARGE_OPEN_SET))

    # Supplying the *correct* programme is accepted and changes nothing.
    same = profiler.profile(query, contract, get_program(ProgramType.NUMERIC))
    assert same == profiler.profile(query, contract)


def test_a_mismatched_contract_is_rejected(profiler):
    query, _ = compile_query("Subject", CAPACITY, 0)
    with pytest.raises(ValueError, match="mismatched"):
        profiler.profile(query, CONTRACTS[AWARD])


def test_specialist_hint_is_derived_from_the_program_only(profiler):
    expected = {
        BORDERS: SpecialistHint.M15_SMALL_SET_CLOSURE,
        STOCK: SpecialistHint.M15_SMALL_SET_CLOSURE,
        DEATH: SpecialistHint.M14_NULL_TEMPORAL,
        CAPACITY: SpecialistHint.M12_NUMERIC,
        AREA: SpecialistHint.M12_NUMERIC,
        AWARD: SpecialistHint.M13_LARGE_SET,
    }
    for relation, hint in expected.items():
        assert _profile(profiler, "Subject", relation).specialist_hint is hint
    # Two relations sharing a programme share a hint: it is a programme
    # property, so it cannot encode a per-relation routing decision.
    assert (
        _profile(profiler, "A", BORDERS).specialist_hint
        is _profile(profiler, "B", STOCK).specialist_hint
    )


# --------------------------------------------------------------------------
# 6. Risk semantics
# --------------------------------------------------------------------------


def test_risk_levels_order_by_severity_not_alphabetically():
    assert RiskLevel.NONE < RiskLevel.LOW < RiskLevel.MEDIUM < RiskLevel.HIGH
    assert RiskLevel.HIGH > RiskLevel.LOW          # "HIGH" < "LOW" as plain strings
    assert max(RiskLevel.LOW, RiskLevel.HIGH) is RiskLevel.HIGH


def test_numeric_relations_are_more_numerically_ambiguous_than_set_relations(profiler):
    capacity = _profile(profiler, "Stadium", CAPACITY)
    borders = _profile(profiler, "Testland", BORDERS)
    assert capacity.numeric_ambiguity > borders.numeric_ambiguity
    assert borders.numeric_ambiguity is RiskLevel.NONE


def test_award_is_the_open_set_and_missingness_case(profiler):
    award = _profile(profiler, "Testprize", AWARD)
    assert award.open_set_risk is RiskLevel.HIGH
    assert award.missingness_risk is RiskLevel.HIGH
    assert award.search_breadth is RiskLevel.HIGH
    for other in (BORDERS, STOCK, DEATH, CAPACITY, AREA):
        assert _profile(profiler, "S", other).open_set_risk < RiskLevel.HIGH


def test_death_is_the_nullability_and_temporal_case(profiler):
    death = _profile(profiler, "Testperson", DEATH)
    assert death.nullability_risk is RiskLevel.HIGH
    assert death.temporal_sensitivity is RiskLevel.HIGH
    assert death.identity_ambiguity is RiskLevel.HIGH
    assert death.cardinality_regime is CardinalityRegime.ZERO_OR_ONE


def test_area_is_numeric_and_unit_sensitive(profiler):
    area = _profile(profiler, "Testisland", AREA)
    assert area.numeric_ambiguity is RiskLevel.HIGH
    assert area.format_sensitivity is RiskLevel.HIGH
    assert area.open_set_risk is RiskLevel.NONE


def test_stock_carries_temporal_and_corporate_identity_risk(profiler):
    stock = _profile(profiler, "Testcorp", STOCK)
    assert stock.temporal_sensitivity is RiskLevel.HIGH
    assert stock.identity_ambiguity is RiskLevel.HIGH
    assert stock.nullability_risk is RiskLevel.HIGH      # private / delisted
    assert stock.near_miss_risk is RiskLevel.HIGH        # parent / subsidiary


def test_borders_is_a_near_miss_problem_not_a_breadth_problem(profiler):
    borders = _profile(profiler, "Testland", BORDERS)
    assert borders.near_miss_risk is RiskLevel.HIGH
    assert borders.temporal_sensitivity is RiskLevel.LOW
    assert borders.search_breadth < RiskLevel.HIGH


def test_single_object_regimes_declare_no_missingness(profiler):
    for relation in (DEATH, CAPACITY, AREA):
        profile = _profile(profiler, "S", relation)
        assert profile.missingness_risk is RiskLevel.NONE
        assert not CONTRACTS[relation].program.supports_missingness


def test_exactly_one_regimes_declare_no_nullability(profiler):
    for relation in (CAPACITY, AREA):
        assert _profile(profiler, "S", relation).nullability_risk is RiskLevel.NONE


# --------------------------------------------------------------------------
# 7. Subject-surface features
# --------------------------------------------------------------------------


def test_parenthetical_qualifier_is_detected():
    features = subject_surface_features("Mercury (planet)")
    assert features.has_parenthetical
    assert features.has_disambiguation_marker
    assert features.token_count == 2


def test_comma_qualifier_is_detected():
    features = subject_surface_features("Springfield, Illinois")
    assert features.has_comma_qualifier
    assert features.has_disambiguation_marker
    assert not features.has_parenthetical


def test_prepositional_qualifier_is_detected_structurally():
    """The brief's example. Named for what it is, not what it might mean."""
    assert subject_surface_features("Estadio X in Madrid").has_prepositional_qualifier
    # The same structure with a non-locational phrase gets the same flag; the
    # profiler does not claim to know which is which without world knowledge.
    assert subject_surface_features(
        "Nobel Prize in Physiology or Medicine"
    ).has_prepositional_qualifier
    assert not subject_surface_features("Estadio Madrid").has_prepositional_qualifier


def test_digits_and_unicode_are_reported():
    assert subject_surface_features("Boeing 747").has_digit
    assert not subject_surface_features("Boeing").has_digit

    accented = subject_surface_features("Köln")
    assert accented.has_non_ascii
    assert not subject_surface_features("Cologne").has_non_ascii

    cjk = subject_surface_features("東京")
    assert cjk.has_non_ascii and cjk.char_length == 2


def test_unicode_is_normalised_so_one_name_has_one_length():
    composed = subject_surface_features("Köln")            # U+00F6
    decomposed = subject_surface_features("Köln")    # o + combining
    assert composed == decomposed


def test_internal_punctuation_is_reported():
    assert subject_surface_features("St. Mary's").has_internal_punctuation
    assert subject_surface_features("Guinea-Bissau").has_internal_punctuation
    assert not subject_surface_features("Guinea").has_internal_punctuation


def test_length_is_reported_raw_with_no_short_or_long_bucketing():
    """No arbitrary cutoff exists to be tuned later."""
    features = subject_surface_features("  Wellington   Island  ")
    assert features.token_count == 2
    assert features.char_length == len("Wellington   Island")
    assert not any(
        name.startswith(("is_", "unusually_")) for name in features.to_json()
    )


def test_an_empty_subject_profiles_rather_than_raising():
    features = subject_surface_features("")
    assert features.token_count == 0 and features.char_length == 0
    assert not features.has_disambiguation_marker


# --------------------------------------------------------------------------
# 8. No factual inference
# --------------------------------------------------------------------------


def test_the_profile_contains_no_candidate_or_factual_field(profiler):
    payload = _profile(profiler, "Estadio X in Madrid", CAPACITY).to_json()
    flat = json.dumps(payload).casefold()
    for leaked in ("madrid", "capacity_value", "objectentities", "candidate", "answer"):
        if leaked == "madrid":
            # The subject itself is echoed for identity; nothing *derived* from
            # it may appear.
            continue
        assert leaked not in flat, leaked
    assert set(payload) == {
        "profile_version", "SubjectEntity", "Relation", "row_index",
        "program_type", "cardinality_regime", "specialist_hint",
        "risk", "subject_surface",
    }


def test_two_different_subjects_of_one_relation_share_every_risk_axis(profiler):
    """Risk is a relation property; the subject string only adds structure."""
    a = _profile(profiler, "France", BORDERS)
    b = _profile(profiler, "Vatican City (enclave)", BORDERS)
    assert a.axes() == b.axes()
    assert a.subject_surface != b.subject_surface


def test_priors_declare_no_factual_content():
    from cover_kbc.query_intelligence import priors as priors_module

    source = Path(priors_module.__file__).read_text(encoding="utf-8")
    # A prior table that named real entities would be a knowledge base.
    for entity in ("France", "Germany", "NASDAQ", "Nobel", "Wembley", "Tokyo"):
        assert entity not in source, f"priors mention the entity {entity!r}"


# --------------------------------------------------------------------------
# 9. Serialisation round-trip
# --------------------------------------------------------------------------


def test_profile_round_trips_through_json(profiler):
    for relation in ALL_RELATIONS:
        original = _profile(profiler, "Subject (qualified), 1999", relation, row_index=7)
        payload = json.loads(json.dumps(original.to_json()))
        assert QueryRiskProfile.from_json(payload) == original


def test_serialised_profile_is_stable_across_runs(profiler):
    first = json.dumps(_profile(profiler, "Testland", BORDERS).to_json(), sort_keys=True)
    second = json.dumps(_profile(profiler, "Testland", BORDERS).to_json(), sort_keys=True)
    assert first == second


# --------------------------------------------------------------------------
# 10-11. Shadow mode and staged execution
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cli():
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_with_profiler(tmp_path: Path, enabled: bool) -> Path:
    import yaml

    config = yaml.safe_load(Path(CONFIG).read_text())
    config["query_intelligence"] = {"profiler": {"enabled": enabled, "mode": "shadow"}}
    path = tmp_path / f"config_{'on' if enabled else 'off'}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _run(cli, monkeypatch, config: Path, run_dir: Path, relation: str) -> None:
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_staged.py", "all",
            "--config", str(config),
            "--split", "train",
            "--limit", "4",
            "--relation", relation,
            "--run-dir", str(run_dir),
        ],
    )
    assert cli.main() == 0


@pytest.mark.parametrize("relation", [BORDERS, AWARD, AREA])
def test_shadow_mode_changes_no_prediction_artefact(
    cli, tmp_path, monkeypatch, capsys, relation
):
    """The load-bearing invariant: M9 on == M9 off, byte for byte."""
    on, off = tmp_path / f"on_{relation}", tmp_path / f"off_{relation}"
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, True), on, relation)
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, False), off, relation)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name

    # M9 on produces its own artefact; M9 off leaves none.
    assert (on / "query_profiles.jsonl").is_file()
    assert not (off / "query_profiles.jsonl").exists()


def test_shadow_mode_changes_no_neural_call_count(cli, tmp_path, monkeypatch, capsys):
    on, off = tmp_path / "on", tmp_path / "off"
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, True), on, AWARD)
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, False), off, AWARD)
    capsys.readouterr()

    a = json.loads((on / "diagnostics.json").read_text())
    b = json.loads((off / "diagnostics.json").read_text())
    assert a == b
    for key in ("total_calls", "total_verification_calls", "total_generated_tokens"):
        if key in a:
            assert a[key] == b[key], key


def test_staged_phases_keep_their_semantics_with_m9_on(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "staged"
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, True), run_dir, AWARD)
    out = capsys.readouterr().out

    for marker in ("[PHASE A]", "[PHASE B]", "[PHASE C]"):
        assert marker in out
    profiles = [
        json.loads(line)
        for line in (run_dir / "query_profiles.jsonl").read_text().splitlines()
    ]
    manifest = json.loads((run_dir / "query_manifest.json").read_text())["queries"]
    # One profile per selected query, in the same order, produced once in Phase A.
    assert len(profiles) == len(manifest)
    assert [(p["SubjectEntity"], p["Relation"]) for p in profiles] == [
        (q["SubjectEntity"], q["Relation"]) for q in manifest
    ]


def test_persisted_profiles_equal_recomputed_ones(cli, tmp_path, monkeypatch, capsys):
    """Recomputation is exact, which is why later phases need not persist them."""
    run_dir = tmp_path / "recompute"
    _run(cli, monkeypatch, _config_with_profiler(tmp_path, True), run_dir, STOCK)
    capsys.readouterr()

    profiler = QueryProfiler()
    for line in (run_dir / "query_profiles.jsonl").read_text().splitlines():
        payload = json.loads(line)
        persisted = QueryRiskProfile.from_json(payload)
        recomputed = profiler.profile(
            Query(payload["SubjectEntity"], payload["Relation"], payload["row_index"])
        )
        assert persisted == recomputed


def test_pipeline_without_a_profiler_is_the_pre_m9_path():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(NullRuntime(model_id="offline/null"), PipelineConfig())
    assert pipeline.profiler is None
    pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    assert pipeline.query_profiles == []


def test_profiles_never_reach_the_evidence_graph():
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    pipeline = CoverPipeline(
        NullRuntime(model_id="offline/null"), PipelineConfig(), profiler=QueryProfiler()
    )
    graph = pipeline.enumerate_query(Query("Testland", BORDERS, 0))
    assert len(pipeline.query_profiles) == 1
    blob = json.dumps(
        {k: str(v) for k, v in vars(graph).items() if not k.startswith("_")}
    ).casefold()
    for leaked in ("profile_version", "risk", "specialist_hint", "subject_surface"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------
# 12. Unknown relation / malformed config
# --------------------------------------------------------------------------


def test_an_unknown_relation_fails_loudly(profiler):
    with pytest.raises(UnknownRelationError):
        profiler.profile(Query("Subject", "notARelation", 0))
    with pytest.raises(UnknownRelationPriorError):
        get_priors("notARelation")


def test_an_unsupported_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported profiler mode"):
        QueryProfiler(ProfilerConfig(enabled=True, mode="active"))


def test_unknown_config_keys_are_rejected():
    with pytest.raises(ValueError, match="unknown query_intelligence.profiler key"):
        ProfilerConfig.from_mapping({"enabled": True, "enabledd": True})
    with pytest.raises(ValueError, match="unknown query_intelligence key"):
        build_profiler({"profiler": {"enabled": True}, "prompt_compiler": {}})


def test_disabled_or_absent_config_builds_no_profiler():
    assert build_profiler(None) is None
    assert build_profiler({}) is None
    assert build_profiler({"profiler": {"enabled": False}}) is None
    assert isinstance(build_profiler({"profiler": {"enabled": True}}), QueryProfiler)


def test_a_malformed_prior_override_is_rejected():
    with pytest.raises(ValueError, match="unknown relation"):
        QueryProfiler(ProfilerConfig(enabled=True, relation_priors={"notARelation": {}}))
    with pytest.raises(ValueError, match="unknown axes"):
        QueryProfiler(ProfilerConfig(enabled=True, relation_priors={BORDERS: {"vibes": "HIGH"}}))
    with pytest.raises(ValueError, match="not a risk level"):
        QueryProfiler(
            ProfilerConfig(enabled=True, relation_priors={BORDERS: {"near_miss_risk": "EXTREME"}})
        )


def test_an_override_that_contradicts_the_contract_is_rejected():
    """Config may adjust a judgement; it may not break Modules 0 and 1."""
    with pytest.raises(ValueError, match="numeric relation"):
        QueryProfiler(
            ProfilerConfig(enabled=True, relation_priors={CAPACITY: {"numeric_ambiguity": "NONE"}})
        )
    with pytest.raises(ValueError, match="open-set programme"):
        QueryProfiler(
            ProfilerConfig(enabled=True, relation_priors={AWARD: {"open_set_risk": "LOW"}})
        )


def test_a_valid_override_is_applied_and_leaves_the_table_untouched():
    from cover_kbc.query_intelligence import priors as priors_module

    profiler = QueryProfiler(
        ProfilerConfig(enabled=True, relation_priors={BORDERS: {"search_breadth": "MEDIUM"}})
    )
    assert profiler.profile(Query("S", BORDERS, 0)).search_breadth is RiskLevel.MEDIUM
    # The module-level declaration is not mutated by building a profiler.
    assert priors_module.RELATION_RISK_PRIORS[BORDERS].search_breadth is RiskLevel.LOW
    assert QueryProfiler().profile(Query("S", BORDERS, 0)).search_breadth is RiskLevel.LOW


def test_priors_consistency_catches_a_contradictory_declaration(monkeypatch):
    from cover_kbc.query_intelligence import priors as priors_module

    broken = dict(priors_module.RELATION_RISK_PRIORS)
    broken.pop(AWARD)
    monkeypatch.setattr(priors_module, "RELATION_RISK_PRIORS", broken)
    with pytest.raises(ValueError, match="no M9 priors"):
        check_priors_consistency()


def test_every_prior_states_a_rationale():
    for relation in ALL_RELATIONS:
        assert get_priors(relation).rationale, relation
