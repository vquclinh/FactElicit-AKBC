"""Full M0-M21 architecture conformance.

Cross-layer only. Every module already has its own suite; this file proves the
things no single module's tests can see:

* all eight layers and all of M0-M21 exist, are reachable, and own disjoint
  responsibilities;
* the Appendix-C I/O seams actually connect;
* every relation routes to the specialist Audit 0028 §9A assigns it, end to end;
* the invariants earlier corrective passes established are still true **at the
  top of the stack**, where a later layer is most likely to have quietly undone
  one;
* every neural call goes through the audited runtime, and the non-neural
  upgraded layers add none;
* enabling the whole upgraded stack cannot change a production prediction, and
  shipped configuration cannot activate uncalibrated M20/M21.

Deliberately a small number of strong tests rather than many weak ones: where a
module suite already pins an invariant, this file checks it survives
composition instead of restating it.

Every subject is fictional; every M20/M21 package is `SYNTHETIC_TEST`.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS

sys.path.insert(0, str(Path("scripts").resolve()))

RELATIONS = (
    "countryLandBordersCountry", "personHasCityOfDeath", "hasCapacity",
    "hasArea", "awardWonBy", "companyTradesAtStockExchange",
)
SRC = Path("src/cover_kbc")


@pytest.fixture(scope="module")
def smoke():
    """The consolidated architecture smoke, run once."""
    module = importlib.import_module("architecture_smoke")
    upgraded, upgraded_predictions, upgraded_calls = module.run(upgraded=True)
    core, core_predictions, core_calls = module.run(upgraded=False)
    return {
        "module": module,
        "upgraded": upgraded, "upgraded_predictions": upgraded_predictions,
        "upgraded_calls": upgraded_calls,
        "core": core, "core_predictions": core_predictions,
        "core_calls": core_calls,
        "trace": module.architecture_trace(upgraded, upgraded_predictions),
    }


def _module_source(*paths: str) -> str:
    return "\n".join((SRC / p).read_text(encoding="utf-8") for p in paths)


def _code_only(*paths: str) -> str:
    """Executable source with docstrings, comments and raise-prose removed.

    These modules document their prohibitions in prose - "no reclustering",
    "Module 6's q_res" - so a raw substring scan would fire on the very
    sentences that record the invariant being asserted.
    """
    import io
    import tokenize

    kept: list[str] = []
    for path in paths:
        source = (SRC / path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        prose = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    prose.add(doc)
            if isinstance(node, ast.Raise):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Constant) and isinstance(
                        inner.value, str
                    ):
                        prose.add(inner.value)
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING:
                try:
                    if ast.literal_eval(token.string) in prose:
                        continue
                except (ValueError, SyntaxError):  # pragma: no cover
                    pass
            kept.append(token.string)
    return " ".join(kept)


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


# ==========================================================================
# A. Architecture: eight layers, M0-M21, no M22
# ==========================================================================


#: Every numbered module, and the source that owns its contract.
MODULE_OWNERS = {
    "M0": "contracts/registry.py", "M1": "contracts/programs.py",
    "M2": "elicitation/engine.py", "M3": "evidence/graph.py",
    "M4": "verification/blind.py", "M5": "types.py",
    "M6": "coverage.py", "M7": "controller.py", "M8": "selection.py",
    "M9": "query_intelligence/profiler.py",
    "M10": "query_intelligence/prompt_compiler.py",
    "M11": "query_intelligence/parametric_retrieval.py",
    "M12": "specialists/numeric_specialist.py",
    "M13": "specialists/large_set_specialist.py",
    "M14": "specialists/null_temporal_specialist.py",
    "M15": "specialists/small_set_specialist.py",
    "M16": "evidence/consensus.py",
    "M17": "verification/specialist_verifier.py",
    "M18": "verification/bidirectional_verifier.py",
    "M19": "coverage_gap/missingness.py",
    "M20": "control/relation_budget.py",
    "M21": "control/micro_planner.py",
}


def test_every_numbered_module_has_concrete_source():
    missing = [name for name, path in MODULE_OWNERS.items()
               if not (SRC / path).is_file()]
    assert not missing, f"missing module source: {missing}"
    assert len(MODULE_OWNERS) == 22          # M0..M21


def test_every_layer_integration_seam_exists():
    for path in ("evidence/layer4.py", "coverage_gap/facet_coverage.py",
                 "control/layer6_integration.py", "control/action_catalog.py"):
        assert (SRC / path).is_file(), path


def test_no_module_22_and_no_dola():
    for path in SRC.rglob("*.py"):
        name = path.name.casefold()
        assert "m22" not in name, path
        assert "dola" not in name, path
    blob = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    for forbidden in ("class DoLa", "def dola", "M22", "MetaController",
                      "OrchestratorAgent"):
        assert forbidden not in blob, forbidden


def test_all_twenty_one_modules_produced_state(smoke):
    """Presence proven by what actually ran, not by file listing."""
    present = smoke["trace"]["modules_present"]
    assert present["M0_M1_contracts"] == 6
    for module in ("M9", "M10", "M11", "M16", "Layer4", "M19", "M20", "M21",
                   "Layer6"):
        assert present[module] == 6, module
    # Specialists run only for the relations they own.
    assert present["M12"] == 2 and present["M13"] == 1
    assert present["M14"] == 1 and present["M15"] == 2
    assert sum(present[m] for m in ("M12", "M13", "M14", "M15")) == 6


# ==========================================================================
# B/C. Appendix-C seams and the six relation flows
# ==========================================================================


def test_every_relation_routes_to_its_audited_owner(smoke):
    from cover_kbc.coverage_gap.facet_coverage import FACET_OWNER

    for row in smoke["trace"]["relations"]:
        relation = row["Relation"]
        assert FACET_OWNER[relation] == row["specialist_owner"], relation
        assert row["program_type"] == CONTRACTS[relation].program_type.value


@pytest.mark.parametrize("relation", RELATIONS)
def test_each_relation_has_a_complete_end_to_end_trace(smoke, relation):
    """M0/M1 -> M9 -> M10 -> M11 -> specialist -> M16 -> Layer4 -> M19 -> M20
    -> Layer6/M21 -> M8, with no gap."""
    row = next(r for r in smoke["trace"]["relations"] if r["Relation"] == relation)
    for stage in ("M9_profile", "M10_program", "M11_retrieval", "M16_consensus",
                  "layer4"):
        assert row[stage], f"{relation}: {stage} missing"
    assert row["M19_residual"] is None or 0.0 <= row["M19_residual"] <= 1.0
    assert row["M20_numeric_plan"] is True
    assert row["M21_decision"] in ("ACTION", "STOP")
    assert row["M8_objects"] >= 0


def test_the_appendix_c_io_seams_connect(smoke):
    """Each consumer really reads its declared producer's object."""
    pipeline = smoke["upgraded"]
    for relation in RELATIONS:
        consensus = next(c for c in pipeline.consensus_results
                         if c.relation == relation)
        layer4 = next(s for s in pipeline.layer4_results if s.relation == relation)
        gap = next(g for g in pipeline.coverage_gap_results
                   if g.relation == relation)
        budget = next(b for b in pipeline.relation_budget_results
                      if b.relation == relation)
        layer6 = next(s for s in pipeline.layer6_results if s.relation == relation)

        # M16 -> Layer4 -> M19, keyed by the same query identity throughout.
        key = (consensus.subject, consensus.relation, consensus.row_index)
        assert (layer4.subject, layer4.relation, layer4.row_index) == key
        assert (gap.subject, gap.relation, gap.row_index) == key
        assert (budget.subject, budget.relation, budget.row_index) == key
        assert (layer6.subject, layer6.relation, layer6.row_index) == key
        # Version provenance is carried, not re-derived.
        assert gap.layer4_version == layer4.integration_version
        assert layer6.decision.planner_version


# ==========================================================================
# D. Evidence accounting survives composition
# ==========================================================================


def test_verification_never_becomes_acquisition_at_the_top_of_the_stack(smoke):
    """Audit 0008: a verifier reading is never F or X.

    Checked on the composed Layer-4 state rather than in one module, because a
    later layer is exactly where this would be undone.
    """
    from cover_kbc.coverage_gap.missingness import is_discovery_group

    for state in smoke["upgraded"].layer4_results:
        for overlay in state.candidates:
            for group in overlay.base_group_supports:
                if group.startswith(("m17:", "core:BLIND_VERIFIER",
                                     "core:EXISTENCE_GATE")):
                    assert not is_discovery_group(group), group
            for check in overlay.structural_checks:
                if check.check_kind != "CANDIDATE_FREE_RECALL":
                    assert check.cross_model_credit.value != "INDEPENDENT_RECALL"


def test_repeated_support_is_never_summed(smoke):
    """q_g = max, not a sum, wherever group support is reported."""
    for state in smoke["upgraded"].layer4_results:
        for overlay in state.candidates:
            for group in overlay.structural_groups:
                assert group.q_g <= 1, (group.group_key, group.q_g)
                assert group.q_g <= group.total_events or group.total_events == 0


def test_alternate_recovery_is_never_contradiction(smoke):
    """Audit 0027 §20A, at the composed layer."""
    for state in smoke["upgraded"].layer4_results:
        admits_one = CONTRACTS[state.relation].selection.max_objects == 1
        for overlay in state.candidates:
            for check in overlay.structural_checks:
                if check.outcome.value == "ALTERNATE_RECOVERED":
                    assert not admits_one, (
                        f"{state.relation} admits one object; an alternate "
                        "reconstruction there is a contradiction, not a recovery")
                    assert (check.independence_group
                            not in overlay.structural_contradicting_groups)


# ==========================================================================
# E/F/G. NULL, numeric and stock invariants at full-system level
# ==========================================================================


@pytest.mark.parametrize("failures", [1, 10, 100])
def test_failed_recall_never_becomes_substantive_null(failures):
    """Audit 0024 at the top of the stack, at three magnitudes."""
    from cover_kbc.coverage_gap.gap_types import UnresolvedReason
    from cover_kbc.coverage_gap.missingness import CoverageGapEstimator
    from cover_kbc.evidence.consensus_types import NullConsensusState
    from cover_kbc.evidence.layer4_types import Layer4EvidenceState

    relation = "personHasCityOfDeath"
    null = NullConsensusState(
        relation=relation, subject="Person Alpha of Examplestan", row_index=0,
        failed_recall_operations=failures,
        failed_recall_operation_ids=tuple(f"op{i}" for i in range(failures)))
    state = Layer4EvidenceState(
        integration_version="layer4-v1", relation=relation,
        subject="Person Alpha of Examplestan", row_index=0,
        base_consensus_version="m16-v1", null_state=null)
    result = CoverageGapEstimator().estimate_coverage_gap(
        state, program_type="NULL_SINGLE")

    assert result.null_state.substantive_null_groups == 0
    assert result.null_state.failed_recall_only is True
    unit = {u.unit_id: u for u in result.unresolved.units}["query_existence_state"]
    assert UnresolvedReason.FAILED_RECALL_ONLY in unit.reasons
    payload = json.dumps(result.to_json())
    for forbidden in ("final_empty", "accepted_empty", "is_empty"):
        assert forbidden not in payload, forbidden


def test_numeric_cluster_identity_is_module_12s_through_every_layer(smoke):
    """Audit 0029: Layer 4 carries M12's clusters and nobody re-derives them."""
    pipeline = smoke["upgraded"]
    for relation in ("hasCapacity", "hasArea"):
        specialist = next(r for r in pipeline.numeric_results
                          if r.plan.relation == relation)
        layer4 = next(s for s in pipeline.layer4_results if s.relation == relation)
        assert len(layer4.numeric_targets) == len(specialist.clusters)
        for overlay, cluster in zip(layer4.numeric_targets, specialist.clusters):
            assert overlay.representative == cluster.representative
            assert overlay.dispersion == cluster.dispersion
            assert overlay.independent_support == cluster.independent_support
    blob = _code_only("coverage_gap/missingness.py", "control/micro_planner.py")
    for forbidden in ("cluster_values", "recluster", "0.05"):
        assert forbidden not in blob, forbidden


def test_the_stock_two_level_trigger_and_reserve_survive():
    """Audit 0032 §15A/§15B at the composed layer."""
    from cover_kbc.control.action_catalog import (
        _M18_COUNTERFACTUAL_PURPOSE, _facet_purpose)
    from cover_kbc.control.budget_types import SpecialReservePurpose as P
    from cover_kbc.coverage_gap.facet_coverage import declared_facets

    stock = "companyTradesAtStockExchange"
    cross = next(f for f in declared_facets(stock) if f.family == "cross_family")
    assert _facet_purpose(stock, cross) is P.FRESHNESS

    declared = frozenset({P.FRESHNESS, P.PARENT_SUBSIDIARY})
    assert _M18_COUNTERFACTUAL_PURPOSE(
        "COUNTERFACTUAL", "parent_listing", declared) is P.PARENT_SUBSIDIARY
    assert _M18_COUNTERFACTUAL_PURPOSE(
        "COUNTERFACTUAL", "historical_listing", declared) is None

    # And the two-level §17A gate is still the legality rule.
    source = (SRC / "control/action_catalog.py").read_text()
    assert "cross_family_trigger" in source
    assert "cross_family_executed" in source


# ==========================================================================
# H/I/J. Verification, coverage, control boundaries
# ==========================================================================


def test_verifier_prompts_never_see_planner_or_coverage_state():
    """Blindness at the composed layer: no evidence-strength leakage."""
    blob = _module_source(
        "verification/blind.py", "verification/specialist_prompts.py",
        "verification/bidirectional_prompts.py")
    for forbidden in ("R_t", "residual", "coverage_gap", "utility",
                      "expected_gain", "support_count", "planner",
                      "risk_profile"):
        assert forbidden not in blob, f"verifier prompt surface leaks {forbidden}"


def test_module_6_and_module_19_stay_distinct(smoke):
    """No blend of production q_res and shadow R_t."""
    assert subprocess.run(
        ["git", "status", "--porcelain", "src/cover_kbc/coverage.py"],
        capture_output=True, text=True, check=True).stdout == ""
    blob = _code_only("coverage_gap/missingness.py", "coverage_gap/gap_types.py")
    for forbidden in ("q_res", "RCSEState", "estimate_residual"):
        assert forbidden not in blob, forbidden
    controller = _code_only("controller.py")
    for forbidden in ("coverage_gap", "CoverageGapState"):
        assert forbidden not in controller, forbidden
    # Both states really coexist on a live run.
    for graph_state in smoke["upgraded"].coverage_gap_results:
        assert graph_state.residual is not None


def test_the_control_ordering_holds_legality_affordability_value(smoke):
    """Owner -> M20 -> M21, with the middle state visible."""
    for state in smoke["upgraded"].layer6_results:
        legal = set(state.legal_actions)
        affordable = set(state.affordable_actions)
        denied = {d.action_id for d in state.denied_actions}
        assert affordable <= legal
        assert denied <= legal                    # denied stays visible as legal
        assert not (affordable & denied)
        ranked = {u.action_id for u in state.decision.utilities}
        assert ranked == affordable
        if state.decision.selected_action:
            assert state.decision.selected_action in affordable


def test_module_20_reads_no_evidence_and_module_21_invents_no_legality():
    budget = _code_only("control/relation_budget.py", "control/budget_types.py",
                        "control/budget_accounting.py")
    for forbidden in ("coverage_gap", "residual", "candidate_key", "verifier"):
        assert forbidden not in budget, f"M20 reads evidence via {forbidden}"
    planner = _code_only("control/micro_planner.py", "control/planner_types.py")
    for forbidden in ("def execute", "runtime", "def legal_actions",
                      "def _derive_legal", "ActionExecutionStatus.ELIGIBLE ="):
        assert forbidden not in planner, forbidden
    # Legality arrives as owner provenance and is required, never synthesised.
    from cover_kbc.control import ActionFamily, PlannerActionCandidate, PlannerError

    with pytest.raises(PlannerError, match="no legal provenance"):
        PlannerActionCandidate(
            action_id="a", source_module="M13",
            family=ActionFamily.SPECIALIST_PROBE, budget_descriptor=None)


# ==========================================================================
# K. Layer 7 / M8 finalisation
# ==========================================================================


def test_module_8_enforces_the_program_cardinality_contract(smoke):
    """Each relation's final output obeys its own contract, not a constant."""
    for index, relation in enumerate(RELATIONS):
        prediction = smoke["upgraded_predictions"][index]
        contract = CONTRACTS[relation]
        limit = contract.selection.max_objects
        if limit:
            assert len(prediction.object_entities) <= limit, relation
        if contract.program_type.value == "NULL_SINGLE":
            assert len(prediction.object_entities) <= 1, relation


def test_module_8_receives_no_shadow_state():
    """M8 is fed by the audited core path only."""
    selection = (SRC / "selection.py").read_text()
    for forbidden in ("coverage_gap", "CoverageGapState", "micro_planner",
                      "MicroPlannerDecision", "relation_budget", "layer4",
                      "consensus", "R_t"):
        assert forbidden not in selection, f"M8 reads shadow state: {forbidden}"
    assert not (_imports_of(SRC / "selection.py") & {
        "cover_kbc.control", "cover_kbc.coverage_gap", "cover_kbc.evidence.layer4"})


def test_the_empty_prediction_reasons_stay_distinct():
    """A recall failure, a rejection and an abstention are different answers."""
    from cover_kbc.types import EmptyReason

    assert {r.value for r in EmptyReason} >= {
        "confident_negative_gate", "no_candidate_generated",
        "candidate_rejected", "unresolved_abstention"}


# ==========================================================================
# L. Neural call accounting
# ==========================================================================


def test_every_neural_call_goes_through_the_audited_runtime():
    """No direct model execution outside the runtime implementation."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.relative_to(SRC).as_posix() == "models/huggingface.py":
            continue                              # the runtime implementation
        text = path.read_text(encoding="utf-8")
        for marker in ("model.generate(", "pipeline(", "AutoModelForCausalLM("):
            if marker in text:
                offenders.append((path.as_posix(), marker))
    assert not offenders, offenders


def test_the_non_neural_upgraded_layers_add_no_calls():
    """M16 integration, M19, M20, M21 and Layer 6 are arithmetic."""
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket"}
    for path in ("evidence/consensus.py", "evidence/layer4.py",
                 "coverage_gap/missingness.py", "coverage_gap/facet_coverage.py",
                 "control/relation_budget.py", "control/budget_accounting.py",
                 "control/micro_planner.py", "control/historical_bins.py",
                 "control/action_catalog.py", "control/layer6_integration.py"):
        imports = _imports_of(SRC / path)
        assert not any(i.split(".")[0] in banned for i in imports), path
        assert not any(i.startswith("cover_kbc.models") for i in imports), path


def test_shadow_neural_spend_is_attributed_not_hidden(smoke):
    """The upgraded stack spends more, and it is shadow spend, not M7 budget."""
    assert smoke["upgraded_calls"] > smoke["core_calls"]
    trace = smoke["trace"]
    module = smoke["module"]
    accounted = module.architecture_trace(
        smoke["upgraded"], smoke["upgraded_predictions"])
    assert accounted["modules_present"]
    assert trace["no_execution"]
    # The production core's own spend is unchanged by the shadow layers.
    assert smoke["core_calls"] > 0


# ==========================================================================
# M/N/O. Compliance, configuration, output contract
# ==========================================================================


def test_inference_is_closed_book():
    for path in SRC.rglob("*.py"):
        imports = _imports_of(path)
        for banned in ("requests", "httpx", "urllib", "socket", "aiohttp"):
            assert not any(i.split(".")[0] == banned for i in imports), path
    blob = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    for forbidden in ("wikipedia.org", "wikidata.org", "api.search",
                      "elasticsearch", "faiss", "chromadb", "pinecone"):
        assert forbidden.casefold() not in blob.casefold(), forbidden


def test_nothing_is_trained():
    blob = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    for forbidden in (".backward()", "torch.optim", "AdamW", "LoraConfig",
                      "get_peft_model", "Trainer(", "reward_model",
                      "policy_gradient"):
        assert forbidden not in blob, forbidden


def test_shipped_configs_cannot_activate_uncalibrated_control():
    import yaml

    for name in ("cover_kbc_v2_mistral24_qwen4", "smoke_staged_scripted",
                 "smoke_staged_roleswap"):
        config = yaml.safe_load(
            Path(f"configs/experiments/{name}.yaml").read_text())
        assert config["relation_budget_scheduler"]["enabled"] is False, name
        assert config["relation_budget_scheduler"]["calibration_file"] is None
        assert config["micro_planner"]["enabled"] is False, name
        assert config["micro_planner"]["historical_bins"] is None
        assert config["micro_planner"]["planner_calibration"] is None
        assert config["layer6_integration"]["enabled"] is False, name
        assert config["coverage_gap"]["enabled"] is False, name
        assert "SYNTHETIC" not in json.dumps(config).upper(), name


def test_the_official_output_contract_is_unchanged(smoke):
    """No diagnostic field leaks into the prediction record."""
    from cover_kbc.data.writer import prediction_rows

    for row in prediction_rows(smoke["upgraded_predictions"]):
        assert set(row) == {"SubjectEntity", "Relation", "ObjectEntities"}
        assert isinstance(row["ObjectEntities"], list)
    text = _code_only("data/writer.py")
    for forbidden in ("R_t", "coverage_gap", "planner", "verifier_label",
                      "confidence", "provenance"):
        assert forbidden not in text, forbidden


# ==========================================================================
# P/Q. Determinism and production invariance
# ==========================================================================


def test_the_full_scripted_run_is_deterministic(smoke):
    module = smoke["module"]
    first, first_predictions, first_calls = module.run(upgraded=True)
    second, second_predictions, second_calls = module.run(upgraded=True)
    assert first_calls == second_calls
    assert first_predictions == second_predictions
    for attribute in ("consensus_results", "layer4_results",
                      "coverage_gap_results", "relation_budget_results",
                      "query_profiles", "prompt_programs"):
        assert getattr(first, attribute) == getattr(second, attribute), attribute
    assert (module.architecture_trace(first, first_predictions)
            == module.architecture_trace(second, second_predictions))


def test_the_upgraded_stack_changes_no_production_prediction(smoke):
    """The strongest invariance statement this architecture makes."""
    core = [tuple(p.object_entities) for p in smoke["core_predictions"]]
    upgraded = [tuple(p.object_entities) for p in smoke["upgraded_predictions"]]
    assert core == upgraded
    for left, right in zip(smoke["core_predictions"], smoke["upgraded_predictions"]):
        assert left.stopped_reason == right.stopped_reason
        assert left.calls_used == right.calls_used
        assert left.generated_tokens_used == right.generated_tokens_used


def test_no_module_21_action_was_executed(smoke):
    for state in smoke["upgraded"].layer6_results:
        assert state.to_json()["no_execution"]
    # Nothing reserved against a real ledger, nothing charged to M7.
    for left, right in zip(smoke["core_predictions"], smoke["upgraded_predictions"]):
        assert left.calls_used == right.calls_used


def test_the_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True).stdout == "", args


def test_the_model_profile_is_unchanged():
    result = subprocess.run(
        ["python", "scripts/audit_model_budget.py",
         "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"],
        capture_output=True, text=True, check=True)
    assert "RESULT: PASS" in result.stdout
    assert "28.67B" in result.stdout
    assert "Mistral-Small-3.2-24B-Instruct-2506" in result.stdout
    assert "Qwen3.5-4B" in result.stdout


# ==========================================================================
# Corrective pass: M9 Appendix-C reconciliation and M17 live cost planning
# ==========================================================================


def _graph(relation, subject, *, records=0, candidates=0):
    """A minimal early-graph stand-in exposing only what refinement reads."""
    from types import SimpleNamespace

    from cover_kbc.types import Query

    return SimpleNamespace(
        query=Query(subject, relation, 0),
        records={f"r{i}": object() for i in range(records)},
        candidates={f"c{i}": object() for i in range(candidates)},
    )


def test_module_9_now_accepts_the_early_graph_appendix_c_requires():
    """§5: "relation, subject surface form, initial graph, early-return signals"."""
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.types import Query

    relation, subject = "awardWonBy", "Aurora Prize for Invention"
    profiler = QueryProfiler()
    static = profiler.profile(Query(subject, relation, 0), CONTRACTS[relation])

    # Before any evidence, q_novel is unmeasured - not zero, not guessed.
    assert static.novelty_risk is None
    assert "no early graph" in static.novelty_basis

    refined = profiler.refine(static, _graph(relation, subject, records=2,
                                             candidates=3))
    assert refined.novelty_risk is not None
    assert refined is not static                       # a new profile


def test_q_novel_is_measured_from_early_returns_never_from_the_subject():
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.query_intelligence.types import RiskLevel
    from cover_kbc.types import Query

    relation = "personHasCityOfDeath"
    profiler = QueryProfiler()

    def grade(subject, **graph):
        static = profiler.profile(Query(subject, relation, 0), CONTRACTS[relation])
        return profiler.refine(static, _graph(relation, subject, **graph))

    # Two very different subject strings, identical early returns -> identical
    # grade. Obscurity is never inferred from a name.
    obscure = grade("Zzyzx Qwerty of Nowhere", records=1, candidates=0)
    famous = grade("Person Alpha of Examplestan", records=1, candidates=0)
    assert obscure.novelty_risk == famous.novelty_risk == RiskLevel.HIGH

    # And the grade tracks the early returns, structurally.
    assert grade("Person Alpha of Examplestan",
                 records=1, candidates=1).novelty_risk is RiskLevel.MEDIUM
    assert grade("Person Alpha of Examplestan",
                 records=1, candidates=5).novelty_risk is RiskLevel.LOW
    assert grade("Person Alpha of Examplestan",
                 records=0).novelty_risk is None


def test_module_9_refinement_mutates_nothing_and_is_deterministic():
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.types import Query

    relation, subject = "hasCapacity", "Example Municipal Stadium"
    profiler = QueryProfiler()
    static = profiler.profile(Query(subject, relation, 0), CONTRACTS[relation])
    graph = _graph(relation, subject, records=2, candidates=2)
    before = (set(graph.records), set(graph.candidates), graph.query)

    first = profiler.refine(static, graph)
    second = profiler.refine(static, graph)
    assert first == second
    assert (set(graph.records), set(graph.candidates), graph.query) == before
    assert static.novelty_risk is None                 # the original is untouched


def test_refinement_cannot_cross_queries_or_move_static_axes():
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.types import Query

    relation, subject = "hasArea", "Example Northern Region"
    profiler = QueryProfiler()
    static = profiler.profile(Query(subject, relation, 0), CONTRACTS[relation])

    with pytest.raises(ValueError, match="may not refine across"):
        profiler.refine(static, _graph("awardWonBy", subject, records=1))
    with pytest.raises(ValueError, match="may not refine across"):
        profiler.refine(static, _graph(relation, "Someone Else", records=1))

    # A dynamic signal never moves a static relation axis or the programme.
    refined = profiler.refine(static, _graph(relation, subject, records=3))
    assert refined.program_type is static.program_type
    assert refined.specialist_hint is static.specialist_hint
    assert refined.secondary_hints == static.secondary_hints
    assert refined.axes() == static.axes()


def test_the_table_3_route_hints_are_transcribed():
    """Appendix C's "route hints" is Table 3's *secondary modules* column."""
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.query_intelligence.types import SecondaryRoute as R
    from cover_kbc.types import Query

    expected = {
        "countryLandBordersCountry": (R.M18_REVERSE_SINGLETON,),
        "personHasCityOfDeath": (R.M11_PSEUDO_MEMORY, R.M18_KEY_CONDITION,
                                 R.CROSS_MODEL_FRESHNESS),
        "hasCapacity": (R.M17_NUMERIC_VERIFIER, R.M18_CONTRAST_ATTENDANCE),
        "awardWonBy": (R.M11_QUERY_SPECIFICATION, R.M19_MISSINGNESS,
                       R.M20_RESERVED_VERIFY),
        "companyTradesAtStockExchange": (R.M14_FRESHNESS, R.M18_PARENT_SUBSIDIARY),
        "hasArea": (R.M17_TOTAL_VS_LAND, R.CROSS_UNIT_CYCLE),
    }
    profiler = QueryProfiler()
    for relation, routes in expected.items():
        profile = profiler.profile(Query("X", relation, 0), CONTRACTS[relation])
        assert profile.secondary_hints == routes, relation


def test_module_9_still_carries_no_factual_answer():
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.types import Query

    relation, subject = "awardWonBy", "Aurora Prize for Invention"
    profiler = QueryProfiler()
    refined = profiler.refine(
        profiler.profile(Query(subject, relation, 0), CONTRACTS[relation]),
        _graph(relation, subject, records=2, candidates=3))
    payload = json.dumps(refined.to_json())
    for forbidden in ("ObjectEntities", "candidate_key", "gold", "accepted",
                      "verifier", "answer"):
        assert forbidden not in payload, forbidden
    assert json.loads(payload)["novelty_risk"] == refined.novelty_risk.value


def test_module_19_remains_the_owner_of_residual_coverage():
    """M9's q_novel and M19's noveltyRate are different quantities."""
    profiler_source = _code_only("query_intelligence/profiler.py")
    for forbidden in ("noveltyRate", "novelty_rate", "CoverageGapState",
                      "residual"):
        assert forbidden not in profiler_source, forbidden
    gap = _code_only("coverage_gap/missingness.py")
    for forbidden in ("novelty_risk", "novelty_basis", "QueryRiskProfile"):
        assert forbidden not in gap, forbidden


def test_the_m17_call_plan_is_derived_from_its_live_configuration():
    """Blocker B: the safe precharge must track M17's real configuration."""
    from cover_kbc.control.action_catalog import m17_call_plan
    from cover_kbc.verification.specialist_verifier import SpecialistVerifierConfig

    config = SpecialistVerifierConfig()
    readings, controls = m17_call_plan(config)
    # Shipped default: two phrasings x two label orders.
    assert len(config.template_ids) == 2 and len(config.label_orders) == 2
    assert readings == 4
    assert controls == 4                       # one control per reading

    # Changing the configuration moves the cost automatically.
    from dataclasses import replace

    wider = replace(config, template_ids=config.template_ids + ("m17_extra_v1",))
    assert m17_call_plan(wider)[0] == 6
    uncalibrated = replace(config, use_calibration=False)
    assert m17_call_plan(uncalibrated) == (4, 0)
    with pytest.raises(Exception, match="no template phrasing|no label order"):
        m17_call_plan(replace(config, template_ids=()))


def test_the_live_m17_cold_and_warm_precharge_are_exact():
    from cover_kbc.control import m17_actions
    from cover_kbc.verification.specialist_verifier import SpecialistVerifierConfig

    class _Target:
        target_id, display = "candidate alpha", "Candidate Alpha"
        eligible, ineligible_reason = True, None
        kind = type("K", (), {"value": "ENTITY_CANDIDATE"})()

    config = SpecialistVerifierConfig()
    cold, _ = m17_actions([_Target()], subject="Aurora Prize for Invention",
                          relation="awardWonBy", row_index=0,
                          verifier_config=config, control_calls_needed=4)
    warm, _ = m17_actions([_Target()], subject="Aurora Prize for Invention",
                          relation="awardWonBy", row_index=0,
                          verifier_config=config, control_calls_needed=0)

    assert cold[0].budget_descriptor.cost().neural_calls == 8
    assert warm[0].budget_descriptor.cost().neural_calls == 4
    assert cold[0].identity == warm[0].identity      # same semantic action


def test_one_call_short_of_the_cold_m17_plan_is_denied_before_execution():
    """The hard cap is only as good as the number it is checked against."""
    from cover_kbc.control import (
        BudgetLedger, CalibrationSource, RelationBudgetCalibration, build_plan,
        m17_actions,
    )
    from cover_kbc.control.budget_types import CoreBudgetSnapshot
    from cover_kbc.query_intelligence import QueryProfiler
    from cover_kbc.types import Budget, Query
    from cover_kbc.verification.specialist_verifier import SpecialistVerifierConfig

    class _Target:
        target_id, display = "candidate alpha", "Candidate Alpha"
        eligible, ineligible_reason = True, None
        kind = type("K", (), {"value": "ENTITY_CANDIDATE"})()

    relation, subject = "awardWonBy", "Aurora Prize for Invention"
    cold, _ = m17_actions([_Target()], subject=subject, relation=relation,
                          row_index=0, verifier_config=SpecialistVerifierConfig(),
                          control_calls_needed=4)
    action = cold[0]
    required = action.budget_descriptor.cost().neural_calls
    assert required == 8

    def ledger(hard_calls):
        plan = build_plan(
            subject=subject, relation=relation, row_index=0,
            program_type="LARGE_OPEN_SET",
            profile=QueryProfiler().profile(
                Query(subject, relation, 0), CONTRACTS[relation]),
            core_budget=CoreBudgetSnapshot.of(
                Budget(max_calls=hard_calls, max_generated_tokens=40000)),
            calibration=RelationBudgetCalibration(
                relation=relation, calibration_version="fixture-v1",
                calibration_source=CalibrationSource.SYNTHETIC_TEST,
                hard_calls=hard_calls, hard_generated_tokens=40000,
                discovery_cap=hard_calls, verification_cap=hard_calls,
                verification_reserve=0, special_reserves=()))
        return BudgetLedger(plan)

    exact = ledger(required).reserve(action.budget_descriptor)
    assert exact.reserved_calls == required

    short = ledger(required - 1)
    denial = short.reserve(action.budget_descriptor)
    assert denial.reason.value == "DENIED_BY_HARD_CAP"
    assert short.state().committed_calls == 0        # no partial reservation
