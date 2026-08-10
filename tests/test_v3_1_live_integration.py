"""Audit 0077: the SAFE submission matrix, and Class-B actually being live.

The failure this file exists to make impossible is the one audit 0076 shipped
with: prompt text written into a module, declared as an intervention, and never
reaching a model. Every Class-B assertion here renders the **real** prompt
through the **real** call path and asserts on the string the runtime would be
handed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.data.loader import load_dataset
from cover_kbc.elicitation.engine import ElicitationEngine
from cover_kbc.elicitation.library import views_for
from cover_kbc.elicitation.parsing import parse_numeric_observations
from cover_kbc.models.base import GenerationRequest, GenerationResult
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.types import Query
from cover_kbc.v3_1.acquisition_parser import expand_scientific_notation
from cover_kbc.v3_1.config import V31AggressiveConfig, V31Config, V31SafeConfig
from cover_kbc.v3_1.live_prompts import (
    LIVE_PROMPT_VERSION,
    M17_REJECT_LABEL,
    NO_INSTRUCTIONS,
    V3_REJECT_LABEL,
    RelationInstructions,
    live_prompt_inventory,
)
from cover_kbc.v3_1.promotion import GATES, RECOMMENDED_ORDER, gate_for
from cover_kbc.verification.specialist_verifier import (
    SpecialistVerifier,
    SpecialistVerifierConfig,
)
from cover_kbc.verification.v3_modes import (
    V3VerificationMode,
    V3VerificationRequest,
    render_v3_verification_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"

CAPACITY = "hasCapacity"
CITY = "personHasCityOfDeath"
STOCK = "companyTradesAtStockExchange"
AWARD = "awardWonBy"
AREA = "hasArea"
BORDERS = "countryLandBordersCountry"

ALL_CLASS_B = V31Config(
    enabled=True,
    aggressive=V31AggressiveConfig(
        capacity_definition_prompt=True,
        city_of_death_contrast_prompt=True,
        stock_listing_entity_prompt=True,
        award_expansion_and_fp_cap=True,
        scientific_notation_acquisition=True,
    ),
)


def _load(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# A recording runtime, so tests read the prompt the model would receive
# --------------------------------------------------------------------------


class RecordingRuntime(ScriptedRuntime):
    """Records every request, so a test can read the prompt the model receives.

    Built on the repository's own offline runtime rather than a hand-rolled
    stub: ``spec`` is a property there, and reproducing the real interface by
    hand is how a test ends up asserting against something the pipeline would
    never accept.
    """

    def __init__(self, reply: str = "NONE") -> None:
        super().__init__({}, fallback=lambda request: reply)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return super().generate(request)


def _engine(config: V31Config | None = None) -> tuple[ElicitationEngine, RecordingRuntime]:
    runtime = RecordingRuntime()
    engine = ElicitationEngine(
        runtime, relation_instructions=RelationInstructions.from_config(config)
    )
    return engine, runtime


def _run_first_view(relation: str, config: V31Config | None) -> GenerationRequest:
    """Execute the relation's first mandatory view and return the real request."""
    engine, runtime = _engine(config)
    contract = CONTRACTS[relation]
    view = views_for(relation, contract.mandatory_views)[0]
    engine.run_view(Query("Subject", relation, 0), contract, view)
    assert runtime.requests, "the view produced no model request"
    return runtime.requests[-1]


# --------------------------------------------------------------------------
# 1. SAFE submission matrix
# --------------------------------------------------------------------------


SAFE_CORE_FEATURES = {
    "final_candidate_retention",
    "enumeration_label_repair",
    "numeric_output_canonicalization",
    "stock_structural_validation",
}
SAFE_FULL_FEATURES = SAFE_CORE_FEATURES | {"stock_support_dominance"}


def test_safe_core_enables_exactly_the_monotone_features():
    config = _load("cover_kbc_v3_1_safe_core_test.yaml")
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert set(block.safe.enabled_features) == SAFE_CORE_FEATURES
    assert block.safe.stock_support_dominance is False
    assert not block.aggressive.any_enabled


def test_safe_full_enables_the_complete_safe_stack():
    config = _load("cover_kbc_v3_1_safe_full_test.yaml")
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert set(block.safe.enabled_features) == SAFE_FULL_FEATURES
    assert not block.aggressive.any_enabled


def test_safe_full_and_the_backward_compatible_config_cannot_drift():
    """``cover_kbc_v3_1_safe_test.yaml`` is audit 0076's name for SAFE_FULL."""
    full = _load("cover_kbc_v3_1_safe_full_test.yaml")
    legacy = _load("cover_kbc_v3_1_safe_test.yaml")
    assert (full["pipeline"]["selection"]["v3_1"]
            == legacy["pipeline"]["selection"]["v3_1"])


@pytest.mark.parametrize("name", [
    "cover_kbc_v3_1_safe_core_test.yaml",
    "cover_kbc_v3_1_safe_full_test.yaml",
])
def test_both_safe_variants_are_test_ready(name):
    report = evaluate_test_readiness(_load(name), base_dir=CONFIG_DIR, split="test")
    assert report.state is ReadinessState.FULL_TEST_READY, report.blockers
    assert report.details["v3_1_calibration_status"] == "SAFE_WITH_EXISTING_CALIBRATION"


@pytest.mark.parametrize("name", [
    "cover_kbc_v3_1_safe_core_test.yaml",
    "cover_kbc_v3_1_safe_full_test.yaml",
])
def test_safe_variants_reuse_the_audit_0073_calibration(name):
    v3 = _load("cover_kbc_v3_test.yaml")
    variant = _load(name)
    assert variant["relation_budget_scheduler"] == v3["relation_budget_scheduler"]
    assert variant["micro_planner"] == v3["micro_planner"]
    assert variant["calibration_provenance"] == v3["calibration_provenance"]
    assert variant["test_dataset"] == v3["test_dataset"]


@pytest.mark.parametrize("name", [
    "cover_kbc_v3_1_safe_core_test.yaml",
    "cover_kbc_v3_1_safe_full_test.yaml",
])
def test_safe_variants_declare_no_collection_mode(name):
    config = _load(name)
    assert "train_collection" not in config
    assert config["pipeline"]["v3_core"]["mode"] == "production"
    assert config["experiment"]["split"] == "test"


def test_safe_core_and_full_differ_only_by_stock_dominance():
    core = _load("cover_kbc_v3_1_safe_core_test.yaml")
    full = _load("cover_kbc_v3_1_safe_full_test.yaml")
    differing = {k for k in set(core) | set(full) if core.get(k) != full.get(k)}
    assert differing == {"experiment", "pipeline"}
    core_safe = dict(core["pipeline"]["selection"]["v3_1"]["safe"])
    full_safe = dict(full["pipeline"]["selection"]["v3_1"]["safe"])
    assert core_safe.pop("stock_support_dominance") is False
    assert full_safe.pop("stock_support_dominance") is True
    assert core_safe == full_safe


def test_aggressive_config_is_still_refused():
    report = evaluate_test_readiness(
        _load("cover_kbc_v3_1_aggressive_test.yaml"), base_dir=CONFIG_DIR, split="test")
    assert report.state is not ReadinessState.FULL_TEST_READY
    assert report.details["v3_1_calibration_status"] == "CALIBRATION_REVIEW_REQUIRED"
    assert any("CALIBRATION_REVIEW_REQUIRED" in blocker for blocker in report.blockers)


# --------------------------------------------------------------------------
# 2. Class-B is live: enumerator side
# --------------------------------------------------------------------------


def test_capacity_instruction_reaches_the_real_enumerator_request():
    off = _run_first_view(CAPACITY, None)
    on = _run_first_view(CAPACITY, ALL_CLASS_B)
    assert "seated capacity" not in off.system_prompt
    assert "seated capacity" in on.system_prompt
    assert "standing capacity" in on.system_prompt
    assert "concert" in on.system_prompt
    assert "post-renovation" in on.system_prompt or "renovation" in on.system_prompt
    # and it rejects the confusable quantities audit 0075 measured
    for excluded in ("attendance", "area", "cost", "date"):
        assert excluded in on.system_prompt.casefold()


def test_stock_instruction_reaches_the_real_enumerator_request():
    on = _run_first_view(STOCK, ALL_CLASS_B)
    lowered = on.system_prompt.casefold()
    assert "exchange" in lowered
    for excluded in ("ticker", "index", "company itself"):
        assert excluded in lowered


def test_award_instruction_reaches_the_real_enumerator_request():
    on = _run_first_view(AWARD, ALL_CLASS_B)
    lowered = on.system_prompt.casefold()
    assert "recipient" in lowered
    assert "individuals:" in lowered or "no prefix" in lowered
    assert "not already present" in lowered


def test_award_instruction_reaches_the_set_expansion_action_prompt():
    """SET_EXPANSION builds a dynamic ViewSpec; it must inherit the instruction."""
    from cover_kbc.elicitation.views import ViewSpec
    from cover_kbc.types import ViewFamily

    engine, _ = _engine(ALL_CLASS_B)
    expansion_view = ViewSpec(
        view_id="v3_set_expansion",
        relation=AWARD,
        family=ViewFamily.STRUCTURAL,
        template="{definition}\n\nName additional recipients of {subject}.",
        needs_accepted_set=True,
    )
    assert "recipient" in engine.system_prompt_for(expansion_view).casefold()


def test_enumerator_instruction_is_absent_when_the_feature_is_off():
    only_city = V31Config(
        enabled=True,
        aggressive=V31AggressiveConfig(city_of_death_contrast_prompt=True))
    request = _run_first_view(CAPACITY, only_city)
    assert "seated capacity" not in request.system_prompt


def test_enabled_false_neutralises_a_populated_aggressive_block():
    disabled = V31Config(enabled=False, aggressive=ALL_CLASS_B.aggressive)
    off = _run_first_view(CAPACITY, None)
    assert _run_first_view(CAPACITY, disabled).system_prompt == off.system_prompt


# --------------------------------------------------------------------------
# 3. Class-B is live: verifier side
# --------------------------------------------------------------------------


def _v3_prompt(relation: str, instructions: RelationInstructions) -> str:
    request = V3VerificationRequest(
        relation=relation, subject="Subject", mode=V3VerificationMode.SEMANTIC,
        target_id="t", target_text="Candidate", relation_definition="DEF",
        relation_boundary=instructions.verifier_boundary(relation),
    )
    return render_v3_verification_prompt(request)


def test_city_boundary_reaches_the_real_v3_verification_prompt():
    on = RelationInstructions.from_config(ALL_CLASS_B)
    rendered = _v3_prompt(CITY, on)
    assert "place of birth" in rendered
    assert "burial" in rendered
    assert "hospital" in rendered
    assert "residence" in rendered
    assert "country, state, province or region" in rendered
    assert "place of death" in rendered


def test_city_boundary_absent_from_the_v3_prompt_when_disabled():
    assert "place of birth" not in _v3_prompt(CITY, NO_INSTRUCTIONS)


def test_boundary_uses_the_label_vocabulary_of_the_frame_it_is_rendered_into():
    """A frame offering VALID/INVALID must not be told to 'Answer B'."""
    on = RelationInstructions.from_config(ALL_CLASS_B)
    v3_rendered = _v3_prompt(CITY, on)
    assert f"Answer {V3_REJECT_LABEL} if" in v3_rendered
    assert "Answer B if" not in v3_rendered

    verifier = SpecialistVerifier(
        SpecialistVerifierConfig(enabled=True), relation_instructions=on)
    m17_boundary = verifier.contract_for(CITY).boundary
    assert m17_boundary.startswith(f"Answer {M17_REJECT_LABEL} if")
    assert "Answer INVALID if" not in m17_boundary


def test_city_boundary_reaches_the_m17_specialist_contract():
    on = SpecialistVerifier(
        SpecialistVerifierConfig(enabled=True),
        relation_instructions=RelationInstructions.from_config(ALL_CLASS_B))
    off = SpecialistVerifier(SpecialistVerifierConfig(enabled=True))
    assert on.contract_for(CITY).boundary != off.contract_for(CITY).boundary
    assert on.contract_for(CITY).contract_version.endswith("v3.1-boundary")
    assert off.contract_for(CITY).contract_version == "m17-contract-v1"


def test_m17_override_replaces_only_the_boundary():
    on = SpecialistVerifier(
        SpecialistVerifierConfig(enabled=True),
        relation_instructions=RelationInstructions.from_config(ALL_CLASS_B))
    off = SpecialistVerifier(SpecialistVerifierConfig(enabled=True))
    new, base = on.contract_for(CITY), off.contract_for(CITY)
    assert new.question == base.question
    assert new.family == base.family
    assert new.relations == base.relations
    assert new.target_kinds == base.target_kinds
    assert new.propositions == base.propositions


def test_v3_request_identity_is_unchanged_when_no_boundary_is_supplied():
    """A V3 run with Class-B off must reuse the exact verification edge ids."""
    plain = V3VerificationRequest(
        relation=CITY, subject="S", mode=V3VerificationMode.SEMANTIC,
        target_id="t", target_text="C", relation_definition="D")
    explicit_empty = V3VerificationRequest(
        relation=CITY, subject="S", mode=V3VerificationMode.SEMANTIC,
        target_id="t", target_text="C", relation_definition="D", relation_boundary="")
    assert plain.request_id == explicit_empty.request_id


def test_v3_request_identity_changes_when_a_boundary_is_supplied():
    plain = V3VerificationRequest(
        relation=CITY, subject="S", mode=V3VerificationMode.SEMANTIC,
        target_id="t", target_text="C", relation_definition="D")
    with_boundary = V3VerificationRequest(
        relation=CITY, subject="S", mode=V3VerificationMode.SEMANTIC,
        target_id="t", target_text="C", relation_definition="D",
        relation_boundary="Answer INVALID if ...")
    assert plain.request_id != with_boundary.request_id


# --------------------------------------------------------------------------
# 4. Relation isolation, and the frozen border relation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relation", sorted(CONTRACTS))
def test_no_cross_relation_prompt_leakage(relation):
    """Only the relations with a declared binding may change at all."""
    on = RelationInstructions.from_config(ALL_CLASS_B)
    enumerator_changes = bool(on.enumerator_instruction(relation))
    verifier_changes = bool(on.verifier_boundary(relation))
    expected_enumerator = relation in {CAPACITY, STOCK, AWARD}
    expected_verifier = relation == CITY
    assert enumerator_changes is expected_enumerator
    assert verifier_changes is expected_verifier


def test_border_enumerator_prompt_is_byte_identical():
    off = _run_first_view(BORDERS, None)
    on = _run_first_view(BORDERS, ALL_CLASS_B)
    assert on.system_prompt == off.system_prompt
    assert on.prompt == off.prompt


def test_border_verifier_prompt_is_byte_identical():
    on = RelationInstructions.from_config(ALL_CLASS_B)
    assert _v3_prompt(BORDERS, on) == _v3_prompt(BORDERS, NO_INSTRUCTIONS)


def test_area_prompts_are_untouched_by_class_b():
    """hasArea has a Class-B *parser* feature but no prompt binding."""
    off = _run_first_view(AREA, None)
    on = _run_first_view(AREA, ALL_CLASS_B)
    assert on.system_prompt == off.system_prompt


def test_no_border_binding_exists_in_source():
    from cover_kbc.v3_1 import live_prompts
    assert BORDERS in live_prompts.FROZEN_RELATIONS
    assert BORDERS not in live_prompts._ENUMERATOR_BINDINGS
    assert BORDERS not in live_prompts._VERIFIER_BINDINGS


# --------------------------------------------------------------------------
# 5. Prompt provenance
# --------------------------------------------------------------------------


def test_generation_record_carries_a_system_prompt_hash():
    engine, runtime = _engine(ALL_CLASS_B)
    contract = CONTRACTS[CAPACITY]
    view = views_for(CAPACITY, contract.mandatory_views)[0]
    outcome = engine.run_view(Query("Subject", CAPACITY, 0), contract, view)
    assert outcome.record.system_prompt_hash
    assert len(outcome.record.system_prompt_hash) == 16


def test_system_prompt_hash_differs_when_the_instruction_is_live():
    """The claim 'the prompt changed' has to be checkable from the record."""
    contract = CONTRACTS[CAPACITY]
    view = views_for(CAPACITY, contract.mandatory_views)[0]
    query = Query("Subject", CAPACITY, 0)
    off_engine, _ = _engine(None)
    on_engine, _ = _engine(ALL_CLASS_B)
    off = off_engine.run_view(query, contract, view).record
    on = on_engine.run_view(query, contract, view).record
    assert off.system_prompt_hash != on.system_prompt_hash
    # The user turn is untouched: only the standing instruction moved.
    assert off.prompt_hash == on.prompt_hash


def test_border_system_prompt_hash_is_unchanged():
    contract = CONTRACTS[BORDERS]
    view = views_for(BORDERS, contract.mandatory_views)[0]
    query = Query("Subject", BORDERS, 0)
    off = _engine(None)[0].run_view(query, contract, view).record
    on = _engine(ALL_CLASS_B)[0].run_view(query, contract, view).record
    assert off.system_prompt_hash == on.system_prompt_hash


def test_live_prompt_inventory_is_versioned_and_hashed():
    inventory = live_prompt_inventory()
    assert inventory
    for entry in inventory:
        assert entry["live_prompt_version"] == LIVE_PROMPT_VERSION
        assert len(entry["body_sha256"]) == 64
        assert entry["call_site"]
        assert entry["relation"] != BORDERS


# --------------------------------------------------------------------------
# 6. Class-B acquisition parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected_km2", [
    ("7.5e4 m2", 0.075),
    ("7.5E4 m²", 0.075),
    ("1.2e3 km2", 1200.0),
    ("7.5e-2 km2", 0.075),
])
def test_scientific_notation_with_units_parses_behind_the_flag(text, expected_km2):
    contract = CONTRACTS[AREA]
    observations = parse_numeric_observations(text, contract, read_exponents=True)
    assert observations, f"{text!r} produced no observation"
    assert observations[0].value == pytest.approx(expected_km2, rel=1e-9)


@pytest.mark.parametrize("text", ["7.5e4 m2", "1.2e3 km2"])
def test_the_gap_is_still_present_with_the_flag_off(text):
    """The default path stays exactly as production behaves today."""
    contract = CONTRACTS[AREA]
    off = parse_numeric_observations(text, contract)
    on = parse_numeric_observations(text, contract, read_exponents=True)
    assert [o.value for o in off] != [o.value for o in on]


@pytest.mark.parametrize("text", [
    "75000 m2", "1,234 km2", "2145 sq mi", "500", "12.5 hectares", "1.234.567 km2",
])
def test_ordinary_numerals_are_unaffected_by_the_expansion(text):
    contract = CONTRACTS[AREA]
    off = parse_numeric_observations(text, contract)
    on = parse_numeric_observations(text, contract, read_exponents=True)
    assert [(o.value, o.source_unit) for o in off] == [(o.value, o.source_unit) for o in on]
    assert expand_scientific_notation(text) == text


def test_engine_reads_exponents_only_when_the_feature_is_on():
    contract = CONTRACTS[AREA]
    view = views_for(AREA, contract.mandatory_views)[0]

    query = Query("Subject", AREA, 0)
    off = ElicitationEngine(RecordingRuntime("1.2e3 km2")).run_view(query, contract, view)
    on = ElicitationEngine(
        RecordingRuntime("1.2e3 km2"),
        relation_instructions=RelationInstructions.from_config(ALL_CLASS_B),
    ).run_view(query, contract, view)
    assert [o.value for o in off.observations] == [1.2]
    assert [o.value for o in on.observations] == [1200.0]


def test_safe_config_cannot_enable_the_acquisition_parser_change():
    """It is not a safe feature, and there must be no way to spell it as one."""
    assert not hasattr(V31SafeConfig(), "scientific_notation_acquisition")
    with pytest.raises(ValueError, match="unknown v3_1.safe feature"):
        V31Config.from_mapping({
            "enabled": True,
            "safe": {"scientific_notation_acquisition": True},
        })


@pytest.mark.parametrize("name", [
    "cover_kbc_v3_1_safe_core_test.yaml",
    "cover_kbc_v3_1_safe_full_test.yaml",
    "cover_kbc_v3_test.yaml",
])
def test_no_safe_or_frozen_config_enables_any_class_b_feature(name):
    config = _load(name)
    raw = config["pipeline"]["selection"].get("v3_1")
    if raw is None:
        return
    assert not V31Config.from_mapping(raw).aggressive.any_enabled


# --------------------------------------------------------------------------
# 7. Targeted diagnostic configs
# --------------------------------------------------------------------------


DIAGNOSTICS = {
    "v3_1_diag_capacity.yaml": (CAPACITY, "capacity_definition_prompt", 100),
    "v3_1_diag_city.yaml": (CITY, "city_of_death_contrast_prompt", 100),
    "v3_1_diag_stock.yaml": (STOCK, "stock_listing_entity_prompt", 100),
    "v3_1_diag_award.yaml": (AWARD, "award_expansion_and_fp_cap", 10),
    "v3_1_diag_area_parser.yaml": (AREA, "scientific_notation_acquisition", 100),
}


@pytest.mark.parametrize("name,expected", sorted(DIAGNOSTICS.items()))
def test_diagnostic_enables_exactly_one_class_b_feature(name, expected):
    relation, feature, _ = expected
    config = _load(name)
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert block.aggressive.enabled_features == (feature,)
    assert config["experiment"]["relation_filter"] == [relation]


@pytest.mark.parametrize("name", sorted(DIAGNOSTICS))
def test_diagnostic_cannot_read_test(name):
    config = _load(name)
    assert config["experiment"]["split"] == "train"
    assert "test_dataset" not in config
    assert config["experiment"].get("diagnostic") is True


@pytest.mark.parametrize("name", sorted(DIAGNOSTICS))
def test_diagnostic_is_not_test_ready(name):
    report = evaluate_test_readiness(_load(name), base_dir=CONFIG_DIR, split="test")
    assert report.state is not ReadinessState.FULL_TEST_READY
    assert report.details["v3_1_calibration_status"] == "CALIBRATION_REVIEW_REQUIRED"


@pytest.mark.parametrize("name,expected", sorted(DIAGNOSTICS.items()))
def test_diagnostic_relation_filter_selects_the_declared_row_count(name, expected):
    """The filter is a relation membership test over real TRAIN rows."""
    relation, _, rows = expected
    config = _load(name)
    queries = [q for q in load_dataset("train").queries() if q.relation == relation]
    assert len(queries) == rows == config["experiment"]["expected_rows"]


def test_capacity_diagnostic_covers_all_hundred_capacity_rows():
    queries = [q for q in load_dataset("train").queries() if q.relation == CAPACITY]
    assert len(queries) == 100
    assert len({q.subject for q in queries}) == 100


def test_award_diagnostic_covers_all_ten_award_rows():
    queries = [q for q in load_dataset("train").queries() if q.relation == AWARD]
    assert len(queries) == 10


def test_relation_filter_reads_no_gold():
    """A filtered run must not be a disguised answer lookup."""
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    resolver = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_relation_filter"
    )
    names = {
        node.attr for node in ast.walk(resolver) if isinstance(node, ast.Attribute)
    } | {
        node.id for node in ast.walk(resolver) if isinstance(node, ast.Name)
    }
    assert not {"ObjectEntities", "gold", "objects"} & names


# --------------------------------------------------------------------------
# 8. Recollection config, prepared but inert
# --------------------------------------------------------------------------


def test_recollection_config_is_train_only_and_marked_not_run():
    config = _load("cover_kbc_v3_1_train_collection.yaml")
    assert config["experiment"]["split"] == "train"
    assert config["experiment"]["prepared_not_run"] is True
    assert "test_dataset" not in config


def test_recollection_config_promotes_nothing_yet():
    config = _load("cover_kbc_v3_1_train_collection.yaml")
    block = V31Config.from_mapping(config["pipeline"]["selection"]["v3_1"])
    assert block.aggressive.enabled_features == ()


def test_recollection_config_uses_a_new_namespace_and_policy():
    config = _load("cover_kbc_v3_1_train_collection.yaml")
    collection = config["train_collection"]
    assert collection["policy"] == "collect-v3-1-class-b"
    assert collection["output_namespace"].startswith("outputs/v3_1_")
    assert "outputs/v3_train_collect_v2_coverage" in collection[
        "overwrite_protected_namespaces"]


def test_recollection_config_cannot_overwrite_audit_0073_calibration():
    config = _load("cover_kbc_v3_1_train_collection.yaml")
    outputs = config["v3_1_calibration_outputs"]
    written = [v for k, v in outputs.items() if k.startswith("m2") and isinstance(v, str)]
    assert not any("configs/calibration/v3/" in path for path in written)
    assert outputs["m20_policy"] == "undecided"


def test_m20_is_not_assumed_to_be_re_derived():
    config = _load("cover_kbc_v3_1_train_collection.yaml")
    assert config["v3_1_calibration_outputs"]["m20_policy"] == "undecided"


# --------------------------------------------------------------------------
# 9. Promotion gates
# --------------------------------------------------------------------------


def test_every_class_b_feature_has_a_promotion_gate():
    from dataclasses import fields
    for field in fields(V31AggressiveConfig):
        assert gate_for(field.name)


def test_gates_state_a_rule_and_a_false_positive_check():
    for gate in GATES:
        assert gate.rule.strip()
        assert gate.false_positive_check.strip()
        assert gate.diagnostic_rows > 0


def test_recommended_order_starts_with_capacity():
    assert RECOMMENDED_ORDER[0] == "capacity_definition_prompt"
    assert set(RECOMMENDED_ORDER) == {gate.feature for gate in GATES}


def test_gate_for_rejects_an_undeclared_feature():
    with pytest.raises(KeyError, match="no promotion gate"):
        gate_for("some_new_experiment")


# --------------------------------------------------------------------------
# 10. Calibration artifacts and production isolation
# --------------------------------------------------------------------------


CALIBRATION_HASHES = {
    "configs/calibration/v3/m20_relation_budget.json":
        "74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29",
    "configs/calibration/v3/m21_historical_bins.json":
        "ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675",
    "configs/calibration/v3/m21_planner_calibration.json":
        "1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6",
    "configs/calibration/v3/calibration_provenance.json":
        "f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d",
    "configs/calibration/m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "configs/calibration/m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "configs/calibration/m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}


@pytest.mark.parametrize("path,digest", sorted(CALIBRATION_HASHES.items()))
def test_calibration_artifacts_are_byte_identical(path, digest):
    import hashlib
    blob = (REPO_ROOT / path).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest, (
        f"{path} changed; audits 0073 and earlier depend on it byte-for-byte")


def test_the_diagnostic_evaluator_is_not_importable_from_production():
    """It reads TRAIN gold, so no production module may reach it."""
    offenders = []
    for path in (REPO_ROOT / "src" / "cover_kbc").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "evaluate_v3_1_diagnostic" in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"production imports the gold-reading evaluator: {offenders}"


def test_live_prompt_bodies_contain_no_benchmark_answers():
    from cover_kbc.v3_1 import live_prompts
    bodies = [body for _, body in live_prompts._ENUMERATOR_BINDINGS.values()]
    bodies += [body for _, body in live_prompts._VERIFIER_BINDINGS.values()]
    import re
    for body in bodies:
        assert not re.search(r"\b\d{3,}\b", body), "a numeric answer leaked into a prompt"
        for banned in ("Wembley", "Nobel", "Nasdaq is", "died in"):
            assert banned not in body


def test_default_pipeline_has_no_class_b_instructions():
    from cover_kbc.pipeline import PipelineConfig
    config = PipelineConfig.from_mapping({})
    instructions = RelationInstructions.from_config(config.selection.v3_1)
    assert instructions.active_features == ()
    assert instructions.active_relations == ()
    assert instructions.scientific_notation_acquisition is False
