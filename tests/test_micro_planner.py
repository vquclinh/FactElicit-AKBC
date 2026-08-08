"""Module 21 - Expected-Value Micro-Planner conformance.

Six things have to hold:

* §17's utility equation is implemented **exactly**, term by term, with no
  seventh term and no relation-specific adjustment;
* every estimate comes from a TRAIN historical bin - missing is never zero, and
  an action is never silently dropped from ``arg max``;
* legality, affordability and value stay three separate questions, in that
  order, and a Module 20 denial is absolute;
* ``a* = arg max U`` with a **strict** ``>`` threshold and a deterministic
  tie-break; STOP is the fallback, not a competitor with a fake utility;
* depth-2 is a two-step undiscounted finite-horizon extension over recorded
  successor bins - no MCTS, no third step, no imagined future state;
* M21 executes nothing and mutates nothing: M7, M19, M20 and the graph are
  untouched.

Every historical package and coefficient set below is a clearly-labelled
**fictional SYNTHETIC_TEST fixture**. No production values exist.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import subprocess
import tokenize
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control import (
    PLANNER_DISCLAIMER,
    PLANNER_VERSION,
    REQUIRED_ESTIMATES,
    ActionExecutionStatus,
    ActionFamily,
    BudgetActionDescriptor,
    BudgetLedger,
    BudgetSpendClass,
    CacheDisposition,
    CalibrationSource,
    CallKind,
    DecisionKind,
    EstimateSource,
    HistoricalActionBin,
    HistoricalBinPackage,
    MicroPlanner,
    MicroPlannerConfig,
    MicroPlannerDecision,
    PlannerActionCandidate,
    PlannerCalibration,
    PlannerError,
    PlannerStateSnapshot,
    RelationBudgetCalibration,
    SpecialReservePurpose,
    StateBinningSpec,
    StopReason,
    SubCall,
    SuccessorStat,
    build_micro_planner,
    build_plan,
    core_action_family,
    load_history,
    load_planner_calibration,
    relation_policy,
    state_bin_key,
    utility,
)
from cover_kbc.control.budget_types import CoreBudgetSnapshot
from cover_kbc.query_intelligence import QueryProfiler
from cover_kbc.types import Budget, Query

AWARD = "awardWonBy"
DEATH = "personHasCityOfDeath"
CAPACITY = "hasCapacity"
BORDERS = "countryLandBordersCountry"
STOCK = "companyTradesAtStockExchange"
AREA = "hasArea"
RELATIONS = (AWARD, DEATH, CAPACITY, AREA, BORDERS, STOCK)

SUBJECTS = {
    AWARD: "Aurora Prize for Invention",
    DEATH: "Person Alpha of Examplestan",
    CAPACITY: "Example Municipal Stadium",
    AREA: "Example Northern Region",
    BORDERS: "Country Alpha",
    STOCK: "Example Holdings Group",
}
M21_MODULES = ("planner_types.py", "historical_bins.py", "micro_planner.py")
_P = SpecialReservePurpose
_F = ActionFamily
SYNTH = EstimateSource.SYNTHETIC_TEST


def _code_without_prose(name: str) -> str:
    source = (Path("src/cover_kbc/control") / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
        # An error message explains a rule; it does not implement one. Scanning
        # it would make these tests fire on the very prose that documents the
        # prohibition being asserted.
        if isinstance(node, ast.Raise):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    docstrings.add(inner.value)
    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING:
            try:
                if ast.literal_eval(token.string) in docstrings:
                    continue
            except (ValueError, SyntaxError):  # pragma: no cover
                pass
        kept.append(token.string)
    return " ".join(kept)


def _scan_blob() -> str:
    import re

    blob = " ".join(_code_without_prose(name) for name in M21_MODULES)
    pattern = r"[\s\"\']*".join(re.escape(w) for w in PLANNER_DISCLAIMER.split())
    return re.sub(pattern, " ", blob)


# --------------------------------------------------------------------------
# Fictional fixtures. NOT production history or coefficients.
# --------------------------------------------------------------------------

#: A fictional binning spec. Its boundaries are invented for testing; real
#: boundaries are TRAIN-calibrated and do not exist yet.
BINNING = StateBinningSpec(
    spec_version="fixture-binning-v1",
    categorical_features=("program_type",),
    numeric_boundaries=(("residual", (0.5,)),),
)


def fixture_calibration(**overrides) -> PlannerCalibration:
    """Fictional §17 coefficients. Not production policy."""
    base = dict(
        calibration_version="fixture-planner-v1", source=SYNTH,
        alpha=1.0, beta=1.0, gamma=1.0, delta=1.0, eta=1.0, kappa=1.0,
        tau_continue=0.0, lookahead_depth=1,
    )
    base.update(overrides)
    return PlannerCalibration(**base)


def fixture_bin(relation, family, *, program_type=None, state_bin="", gain=0.0,
                delta_r=0.0, delta_h=0.0, cost=0.0, redundancy=0.0, fp=0.0,
                support=10, target_class="", successors=(), residual_bucket="b1"):
    program = program_type or CONTRACTS[relation].program_type.value
    return HistoricalActionBin(
        relation=relation,
        program_type=program,
        state_bin_key=state_bin
        or f"program_type={program}|residual={residual_bucket}",
        action_family=family, support_count=support,
        expected_verified_gain=gain, expected_delta_r=delta_r,
        expected_delta_h=delta_h, expected_cost=cost,
        expected_redundancy=redundancy, expected_fp=fp,
        target_class=target_class, successors=tuple(successors),
    )


def fixture_history(bins, *, version="fixture-history-v1", **overrides):
    base = dict(history_version=version, source=SYNTH, binning=BINNING,
                bins=tuple(bins))
    base.update(overrides)
    return HistoricalBinPackage(**base)


# --------------------------------------------------------------------------
# Fictional state
# --------------------------------------------------------------------------


class _Residual:
    def __init__(self, value):
        self.residual = value
        self.availability = type("A", (), {"value": "AVAILABLE"})()
        self.components = ()


class _FakeGap:
    """A minimal stand-in exposing only what the binning spec reads."""

    def __init__(self, relation, subject, row_index, residual):
        self.relation, self.subject, self.row_index = relation, subject, row_index
        self.residual = _Residual(residual)
        self.novelty = type("N", (), {"novelty_rate": None})()
        self.disagreement = type("D", (), {"value": None})()
        self.unresolved = type("U", (), {"value": None})()
        self.null_state = None


class _FakeLayer4:
    def __init__(self, relation, subject, row_index, candidates=(), clusters=()):
        self.relation, self.subject, self.row_index = relation, subject, row_index
        self.candidates = tuple(candidates)
        self.numeric_targets = tuple(clusters)


def _profile(relation):
    return QueryProfiler().profile(
        Query(SUBJECTS[relation], relation, 0), CONTRACTS[relation])


def _budget_calibration(relation, **overrides):
    policy = relation_policy(relation)
    base = dict(
        relation=relation, calibration_version="fixture-v1",
        calibration_source=CalibrationSource.SYNTHETIC_TEST,
        hard_calls=20, hard_generated_tokens=4000, discovery_cap=12,
        verification_cap=14, verification_reserve=6,
        special_reserves=tuple((p, 2) for p in policy.special_reserve_purposes),
    )
    base.update(overrides)
    return RelationBudgetCalibration(**base)


def _budget(relation, **overrides):
    plan = build_plan(
        subject=SUBJECTS[relation], relation=relation, row_index=0,
        program_type=CONTRACTS[relation].program_type.value,
        profile=_profile(relation),
        core_budget=CoreBudgetSnapshot.of(
            Budget(max_calls=20, max_generated_tokens=4000)),
        calibration=_budget_calibration(relation, **overrides),
    )
    return plan, BudgetLedger(plan)


def _state(relation, *, residual=0.9, executed=(), **overrides):
    plan, ledger = overrides.pop("budget", _budget(relation))
    subject = SUBJECTS[relation]
    base = dict(
        subject=subject, relation=relation, row_index=0,
        program_type=CONTRACTS[relation].program_type.value,
        risk_profile=_profile(relation),
        layer4=_FakeLayer4(relation, subject, 0),
        coverage_gap=_FakeGap(relation, subject, 0, residual),
        budget_plan=plan, budget_ledger=ledger, executed_actions=tuple(executed),
    )
    base.update(overrides)
    return PlannerStateSnapshot(**base)


def _descriptor(relation, action_id, spend_class, *, purpose=None, calls=1,
                tokens=0):
    return BudgetActionDescriptor(
        subject=SUBJECTS[relation], relation=relation, row_index=0,
        action_id=action_id, source_module="M13", action_kind="TEST",
        spend_class=spend_class, special_purpose=purpose,
        sub_calls=tuple(
            SubCall(kind=CallKind.GENERATE, cache=CacheDisposition.NOT_CACHEABLE,
                    max_generated_tokens=tokens, label=f"c{i}")
            for i in range(calls)
        ),
    )


def _action(relation, action_id, family, *, spend_class=None, purpose=None,
            calls=1, target="", facet="", status=ActionExecutionStatus.ELIGIBLE,
            repeatable=False, descriptor=None):
    spend_class = spend_class or (
        BudgetSpendClass.VERIFICATION
        if "VERIFY" in family.value or "CHECK" in family.value
        else BudgetSpendClass.DISCOVERY
    )
    return PlannerActionCandidate(
        action_id=action_id, source_module="M13", family=family,
        budget_descriptor=descriptor or _descriptor(
            relation, action_id, spend_class, purpose=purpose, calls=calls),
        target=target, facet_id=facet, model_role="enumerator",
        legal_provenance="fixture: declared legal by its owning registry",
        status=status, repeatable=repeatable,
    )


def _planner(bins, calibration=None):
    return MicroPlanner(fixture_history(bins), calibration or fixture_calibration())


# ==========================================================================
# 1-10. Proposal contract, non-neurality, no production numbers
# ==========================================================================


def test_the_section_17_equation_is_implemented_exactly():
    calibration = PlannerCalibration(
        calibration_version="v", source=SYNTH, alpha=2.0, beta=3.0, gamma=5.0,
        delta=7.0, eta=11.0, kappa=13.0, tau_continue=0.0)
    estimates = fixture_bin(
        AWARD, _F.SPECIALIST_VERIFY, gain=1.5, delta_r=0.4, delta_h=0.25,
        cost=2.0, redundancy=0.5, fp=0.125)
    breakdown = utility(estimates, calibration, action_id="a")

    assert breakdown.verified_gain_term == 2.0 * 1.5
    assert breakdown.delta_r_term == 3.0 * 0.4
    assert breakdown.delta_h_term == 5.0 * 0.25
    assert breakdown.cost_term == 7.0 * 2.0
    assert breakdown.redundancy_term == 11.0 * 0.5
    assert breakdown.false_positive_term == 13.0 * 0.125
    assert breakdown.utility == pytest.approx(
        2.0 * 1.5 + 3.0 * 0.4 + 5.0 * 0.25
        - 7.0 * 2.0 - 11.0 * 0.5 - 13.0 * 0.125)
    # Exactly six components, and no hidden seventh term.
    assert len(REQUIRED_ESTIMATES) == 6


def test_no_hidden_term_or_relation_specific_bonus():
    blob = _scan_blob()
    for forbidden in ("bonus_term", "boost", "mandatory_view", "action_score",
                      "prior_term", "adjustment"):
        assert forbidden not in blob, forbidden
    # Utility depends only on the bin and the coefficients: same estimates and
    # same coefficients give the same number in every relation.
    calibration = fixture_calibration()
    values = {
        relation: utility(
            fixture_bin(relation, _F.SPECIALIST_PROBE, gain=1.0, cost=0.5),
            calibration, action_id="a").utility
        for relation in RELATIONS
    }
    assert len(set(values.values())) == 1


def test_module_21_is_non_neural_and_learns_nothing():
    banned = {"torch", "transformers", "requests", "httpx", "urllib", "socket",
              "numpy", "sklearn"}
    for name in M21_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                assert module.split(".")[0] not in banned, (name, module)
                assert not module.startswith("cover_kbc.models"), (name, module)
    blob = _scan_blob()
    for forbidden in ("LMRuntime", "GenerationRequest", "score_labels",
                      "generate(", "backward", "optimizer", "def fit",
                      "def train", "reward", "bandit", "policy_gradient",
                      "epsilon_greedy", "ema_update", "posterior"):
        assert forbidden not in blob, forbidden


def test_no_online_learning_path_exists():
    """TRAIN-built history is frozen for VAL and TEST."""
    package = fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    for attribute in ("update", "observe", "record_outcome", "fit", "add_bin"):
        assert not hasattr(package, attribute), attribute
    with pytest.raises((AttributeError, TypeError)):
        package.bins = ()          # type: ignore[misc]


def test_shipped_configs_carry_no_history_and_no_coefficients():
    import yaml

    for name in ("cover_kbc_v2_mistral24_qwen4", "smoke_staged_scripted",
                 "smoke_staged_roleswap"):
        config = yaml.safe_load(
            Path(f"configs/experiments/{name}.yaml").read_text())
        block = config["micro_planner"]
        assert set(block) == {
            "enabled", "mode", "planner_version", "historical_bins",
            "planner_calibration"}
        assert block["enabled"] is False, name
        assert block["mode"] == "shadow", name
        assert block["planner_version"] == PLANNER_VERSION, name
        assert block["historical_bins"] is None, name
        assert block["planner_calibration"] is None, name
        text = json.dumps(block).casefold()
        for coefficient in ("alpha", "beta", "gamma", "delta", "eta", "kappa",
                            "tau"):
            assert coefficient not in text, (name, coefficient)
        assert "SYNTHETIC" not in json.dumps(config).upper()


def test_enabling_without_history_or_calibration_fails():
    with pytest.raises(ValueError, match="historical bins on TRAIN"):
        MicroPlannerConfig.from_mapping({"enabled": True})
    with pytest.raises(ValueError, match="gives a value for none of them"):
        MicroPlannerConfig.from_mapping(
            {"enabled": True, "historical_bins": "h.json"})
    with pytest.raises(ValueError, match="unknown micro_planner key"):
        MicroPlannerConfig.from_mapping({"alpha": 1.0})
    # `production` is now a supported mode, but only with the module enabled
    # and only with both artifacts named. An unknown mode is still refused.
    with pytest.raises(ValueError, match="unsupported micro_planner mode"):
        MicroPlannerConfig.from_mapping({"mode": "degraded"})
    with pytest.raises(ValueError, match="but the module is disabled"):
        MicroPlannerConfig.from_mapping({"mode": "production"})
    with pytest.raises(ValueError, match="historical bins on TRAIN"):
        MicroPlannerConfig.from_mapping({"enabled": True, "mode": "production"})
    assert MicroPlannerConfig.from_mapping({
        "enabled": True, "mode": "production", "historical_bins": "h.json",
        "planner_calibration": "c.json"}).is_production
    with pytest.raises(ValueError, match="unsupported planner_version"):
        MicroPlannerConfig.from_mapping({"planner_version": "m21-v9"})
    assert build_micro_planner(None) is None
    assert build_micro_planner({"enabled": False}) is None
    with pytest.raises(PlannerError, match="were not loaded"):
        build_micro_planner(
            {"enabled": True, "historical_bins": "h", "planner_calibration": "c"})


def test_synthetic_packages_are_marked_and_refused_in_production():
    package = fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    assert package.source is SYNTH and not package.source.is_production
    payload = json.loads(json.dumps(package.to_json()))
    with pytest.raises(PlannerError, match="test fixture"):
        load_history(payload)
    assert load_history(payload, allow_synthetic=True) == package

    calibration = fixture_calibration()
    with pytest.raises(PlannerError, match="test fixture"):
        load_planner_calibration(calibration.to_json())
    assert load_planner_calibration(
        calibration.to_json(), allow_synthetic=True) == calibration

    with pytest.raises(PlannerError, match="may not run outside tests"):
        build_micro_planner(
            {"enabled": True, "historical_bins": "h", "planner_calibration": "c"},
            package, calibration)


def test_a_mismatched_source_pair_is_refused():
    package = fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    production = fixture_calibration(source=EstimateSource.TRAIN_CALIBRATED)
    with pytest.raises(PlannerError, match="does not match history source"):
        MicroPlanner(package, production)


def test_the_calibration_schema_rejects_bad_coefficients():
    with pytest.raises(PlannerError, match="not finite"):
        fixture_calibration(alpha=float("inf"))
    with pytest.raises(PlannerError, match="§17 subtracts this term"):
        fixture_calibration(delta=-1.0)
    with pytest.raises(PlannerError, match="§17 adds this term"):
        fixture_calibration(alpha=-1.0)
    with pytest.raises(PlannerError, match="unsupported lookahead depth"):
        fixture_calibration(lookahead_depth=3)
    with pytest.raises(PlannerError, match="unsupported lookahead depth"):
        fixture_calibration(lookahead_depth=0)


# ==========================================================================
# 11-20. Full-state input and upstream requirements
# ==========================================================================


@pytest.mark.parametrize(
    "missing,match",
    [("risk_profile", "M9 risk profile"), ("layer4", "Layer-4"),
     ("coverage_gap", "M19"), ("budget_plan", "M20")],
)
def test_every_upstream_layer_is_required(missing, match):
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    state = _state(AWARD, **{missing: None})
    with pytest.raises(PlannerError, match=match):
        planner.plan(state, ())


def test_upstream_identity_is_validated():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    with pytest.raises(PlannerError, match="risk profile is for"):
        planner.plan(_state(AWARD, risk_profile=_profile(BORDERS)), ())
    with pytest.raises(PlannerError, match="Layer-4 state is for"):
        planner.plan(
            _state(AWARD, layer4=_FakeLayer4(AWARD, "Someone Else", 0)), ())
    with pytest.raises(PlannerError, match="M19 state is for"):
        planner.plan(
            _state(AWARD, coverage_gap=_FakeGap(AWARD, SUBJECTS[AWARD], 9, 0.9)),
            ())


def test_the_state_carries_every_layer_separately():
    state = _state(AWARD)
    assert state.risk_profile is not None
    assert state.layer4 is not None
    assert state.coverage_gap is not None
    assert state.budget_plan is not None
    # No collapsed score anywhere on the snapshot or the decision.
    for cls in (PlannerStateSnapshot, MicroPlannerDecision):
        fields = set(cls.__dataclass_fields__)
        for forbidden in ("planner_confidence", "candidate_truth_probability",
                          "final_answer_score", "score"):
            assert not any(forbidden in f for f in fields), (cls, forbidden)


def test_module_7_is_untouched_by_the_planner():
    """Module 21 reaches into no part of Module 7's vocabulary.

    Asserted over the planner's *own* source rather than over ``git status``:
    a clean working tree says nothing about what the planner references, and it
    fails on any deliberate change elsewhere in the tree.
    """
    blob = _scan_blob()
    for forbidden in ("pending_action", "action_score", "should_stop",
                      "ProgramState", "finalize"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 21-28. Legality, affordability, value - three separate questions
# ==========================================================================


def test_only_owner_declared_legal_actions_are_considered():
    with pytest.raises(PlannerError, match="no legal provenance"):
        PlannerActionCandidate(
            action_id="a", source_module="M13", family=_F.SPECIALIST_PROBE,
            budget_descriptor=None)


def test_an_ineligible_action_is_never_resurrected():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=100.0)])
    action = _action(AWARD, "a#0", _F.SPECIALIST_PROBE,
                     status=ActionExecutionStatus.INELIGIBLE)
    decision = planner.plan(_state(AWARD), [action])
    assert decision.kind is DecisionKind.STOP
    assert decision.stop_reason is StopReason.NO_LEGAL_ACTION
    assert decision.denied_actions[0].reason == "INELIGIBLE"


def test_an_already_executed_action_is_excluded_unless_repeatable():
    planner = _planner([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=5.0),
        fixture_bin(AWARD, _F.RESAMPLE, gain=5.0),
    ])
    probe = _action(AWARD, "a#0", _F.SPECIALIST_PROBE)
    decision = planner.plan(
        _state(AWARD, executed=(probe.identity,)), [probe])
    assert decision.kind is DecisionKind.STOP
    assert decision.denied_actions[0].reason == "ALREADY_EXECUTED"

    # A resample may repeat, because its contract says so.
    resample = _action(AWARD, "r#0", _F.RESAMPLE, repeatable=True)
    decision = planner.plan(
        _state(AWARD, executed=(resample.identity,)), [resample])
    assert decision.kind is DecisionKind.ACTION
    assert decision.selected_action == "r#0"


def test_a_module_20_denied_action_is_never_selectable():
    """A high utility does not buy affordability."""
    planner = _planner([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1000.0),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=1.0),
    ])
    budget = _budget(AWARD, hard_calls=4, discovery_cap=4, verification_cap=4,
                     verification_reserve=0, special_reserves=())
    expensive = _action(AWARD, "big", _F.SPECIALIST_PROBE, calls=9)
    cheap = _action(AWARD, "small", _F.SPECIALIST_VERIFY, calls=1)

    decision = planner.plan(_state(AWARD, budget=budget), [expensive, cheap])
    assert decision.selected_action == "small"
    assert "big" in {d.action_id for d in decision.denied_actions}
    assert "big" not in decision.affordable_actions
    assert "big" in decision.legal_actions      # legal, just not affordable


def test_no_affordable_action_stops_with_its_own_reason():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1000.0)])
    budget = _budget(AWARD, hard_calls=1, discovery_cap=1, verification_cap=1,
                     verification_reserve=0, special_reserves=())
    decision = planner.plan(
        _state(AWARD, budget=budget),
        [_action(AWARD, "big", _F.SPECIALIST_PROBE, calls=5)])
    assert decision.kind is DecisionKind.STOP
    assert decision.stop_reason is StopReason.NO_AFFORDABLE_ACTION


def test_the_planner_reuses_module_20_and_recomputes_nothing():
    blob = _scan_blob()
    for forbidden in ("hard_calls -", "discovery_cap", "class_cap",
                      "def available_calls", "def _class_cap"):
        assert forbidden not in blob, forbidden
    # It asks Module 20 instead of re-deriving capacity. Checked on the AST,
    # because tokenised source separates the name from its call parentheses.
    tree = ast.parse((Path("src/cover_kbc/control") / "micro_planner.py").read_text())
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "reserve" in called


def test_every_action_family_is_representable():
    families = set(ActionFamily)
    assert families == {
        _F.SPECIALIST_PROBE, _F.PSEUDO_MEMORY_PROBE, _F.CANDIDATE_FREE_RECALL,
        _F.BLIND_VERIFY, _F.SPECIALIST_VERIFY, _F.COUNTERFACTUAL_VERIFY,
        _F.REVERSE_CHECK, _F.CROSS_MODEL_CHECK, _F.RESAMPLE,
    }
    # STOP is not one of them.
    assert "STOP" not in {f.value for f in families}

    from cover_kbc.controller import ActionType

    for action_type in ActionType:
        if action_type is ActionType.STOP:
            continue
        assert core_action_family(action_type) in families
    with pytest.raises(PlannerError, match="unknown action family"):
        core_action_family("NOT_AN_ACTION")


def test_stop_is_not_an_action_with_a_fabricated_utility():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)])
    decision = planner.plan(
        _state(AWARD), [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)])
    assert "STOP" not in {u.action_id for u in decision.utilities}
    payload = json.dumps(decision.to_json())
    assert '"selected_action": "STOP"' not in payload


# ==========================================================================
# 29-39. Historical bins
# ==========================================================================


def test_bin_lookup_is_exact_and_deterministic():
    package = fixture_history([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=2.0),
    ])
    key = state_bin_key(_state(AWARD, residual=0.9), BINNING)
    first = package.lookup(relation=AWARD, program_type="LARGE_OPEN_SET",
                           state_bin_key=key, family=_F.SPECIALIST_PROBE)
    assert first.expected_verified_gain == 1.0
    assert first == package.lookup(
        relation=AWARD, program_type="LARGE_OPEN_SET", state_bin_key=key,
        family=_F.SPECIALIST_PROBE)
    blob = _scan_blob()
    for forbidden in ("nearest", "embedding", "cosine", "similarity", "fuzzy"):
        assert forbidden not in blob, forbidden


def test_a_missing_bin_fails_rather_than_dropping_the_action():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    with pytest.raises(PlannerError, match="cannot be silently dropped"):
        planner.plan(_state(AWARD),
                     [_action(AWARD, "v#0", _F.SPECIALIST_VERIFY)])


def test_bin_specificity_is_deterministic_and_ambiguity_unreachable():
    """A targeted bin beats an untargeted one, and a tie cannot be built.

    Two equally specific bins would make ``arg max`` depend on ordering, so the
    package refuses them at construction - which is why the ambiguity branch in
    ``lookup`` is unreachable from a validly constructed package rather than
    merely untested.
    """
    package = fixture_history([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0),
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, target_class="facet_a", gain=9.0),
    ])
    key = state_bin_key(_state(AWARD, residual=0.9), BINNING)
    targeted = package.lookup(
        relation=AWARD, program_type="LARGE_OPEN_SET", state_bin_key=key,
        family=_F.SPECIALIST_PROBE, target_class="facet_a")
    assert targeted.expected_verified_gain == 9.0
    general = package.lookup(
        relation=AWARD, program_type="LARGE_OPEN_SET", state_bin_key=key,
        family=_F.SPECIALIST_PROBE, target_class="facet_z")
    assert general.expected_verified_gain == 1.0

    with pytest.raises(PlannerError, match="duplicate historical bin"):
        fixture_history([
            fixture_bin(AWARD, _F.SPECIALIST_PROBE, target_class="facet_a"),
            fixture_bin(AWARD, _F.SPECIALIST_PROBE, target_class="facet_a",
                        gain=2.0),
        ])


def test_a_duplicate_bin_is_refused_at_construction():
    with pytest.raises(PlannerError, match="duplicate historical bin"):
        fixture_history([
            fixture_bin(AWARD, _F.SPECIALIST_PROBE),
            fixture_bin(AWARD, _F.SPECIALIST_PROBE),
        ])


def test_a_missing_estimate_fails_and_is_not_read_as_zero():
    payload = fixture_bin(AWARD, _F.SPECIALIST_PROBE).to_json()
    del payload["expected_delta_h"]
    with pytest.raises(PlannerError, match="missing estimate"):
        HistoricalActionBin.from_json(payload)

    with pytest.raises(PlannerError, match="not zero"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, delta_h=None)


def test_declared_estimate_ranges_are_validated():
    with pytest.raises(PlannerError, match="outside its declared range"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, delta_r=1.5)
    with pytest.raises(PlannerError, match="outside its declared range"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, fp=-0.1)
    with pytest.raises(PlannerError, match="negative"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, cost=-1.0)
    with pytest.raises(PlannerError, match="not finite"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=float("nan"))
    # No hidden normalisation constant rescales them.
    blob = _scan_blob()
    for forbidden in ("sigmoid", "tanh", "normalize", "rescale", "clip("):
        assert forbidden not in blob, forbidden


def test_binning_boundaries_belong_to_the_package():
    with pytest.raises(PlannerError, match="not ascending"):
        StateBinningSpec(spec_version="v", numeric_boundaries=(("r", (0.7, 0.3)),))
    with pytest.raises(PlannerError, match="must be versioned"):
        StateBinningSpec(spec_version="")
    # No production threshold is hard-coded in the planner.
    blob = _scan_blob()
    for forbidden in ("0.3", "0.5", "0.7"):
        assert forbidden not in blob, forbidden


def test_a_package_enforces_its_own_declared_minimum_support():
    with pytest.raises(PlannerError, match="below the package's own declared"):
        fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE, support=2)],
                        minimum_bin_support=5)
    assert fixture_history(
        [fixture_bin(AWARD, _F.SPECIALIST_PROBE, support=9)],
        minimum_bin_support=5) is not None


def test_verified_gain_is_not_a_raw_candidate_count():
    """Ten raw mentions with nothing verified is zero gain."""
    calibration = fixture_calibration()
    raw = utility(
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=0.0, cost=1.0),
        calibration, action_id="raw")
    verified = utility(
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=1.0, cost=1.0),
        calibration, action_id="verified")
    assert raw.verified_gain == 0.0
    assert verified.verified_gain == 1.0
    assert verified.utility > raw.utility
    from cover_kbc.control.historical_bins import ESTIMATE_UNITS

    assert "verified objects" in ESTIMATE_UNITS["expected_verified_gain"]


def test_delta_r_sign_means_reduction_in_residual():
    calibration = fixture_calibration()
    reduces = utility(fixture_bin(AWARD, _F.SPECIALIST_PROBE, delta_r=0.4),
                      calibration, action_id="a")
    raises_it = utility(fixture_bin(AWARD, _F.SPECIALIST_VERIFY, delta_r=-0.4),
                        calibration, action_id="b")
    assert reduces.delta_r_term > 0 > raises_it.delta_r_term
    assert reduces.utility > raises_it.utility


def test_delta_h_and_fp_and_redundancy_come_from_history_not_recomputed():
    blob = _scan_blob()
    for forbidden in ("entropy", "def _entropy", "log2", "math.log",
                      "contradiction", "near_miss", "verifier_invalid"):
        assert forbidden not in blob, forbidden
    breakdown = utility(
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, delta_h=0.3, redundancy=0.2,
                    fp=0.1),
        fixture_calibration(), action_id="a")
    assert (breakdown.delta_h, breakdown.redundancy,
            breakdown.false_positive_risk) == (0.3, 0.2, 0.1)


def test_expected_cost_is_distinct_from_module_20_safe_cost():
    """One is a value term; the other alone governs affordability."""
    descriptor = _descriptor(AWARD, "a#0", BudgetSpendClass.DISCOVERY, calls=3)
    assert descriptor.cost().neural_calls == 3        # M20 safe reservation
    breakdown = utility(
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, cost=0.5),
        fixture_calibration(), action_id="a#0")
    assert breakdown.expected_cost == 0.5             # historical expectation
    assert breakdown.expected_cost != descriptor.cost().neural_calls

    # And the cheap historical estimate does not make it affordable.
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, cost=0.5,
                                    gain=100.0)])
    budget = _budget(AWARD, hard_calls=2, discovery_cap=2, verification_cap=2,
                     verification_reserve=0, special_reserves=())
    decision = planner.plan(
        _state(AWARD, budget=budget),
        [_action(AWARD, "a#0", _F.SPECIALIST_PROBE, calls=3,
                 descriptor=descriptor)])
    assert decision.kind is DecisionKind.STOP
    assert decision.stop_reason is StopReason.NO_AFFORDABLE_ACTION


# ==========================================================================
# 40-53. Decision, threshold, tie-break
# ==========================================================================


def test_the_threshold_is_strictly_greater():
    calibration = fixture_calibration(tau_continue=1.0)
    # Exactly at tau: STOP.
    at = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)],
                  calibration).plan(
        _state(AWARD), [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)])
    assert at.kind is DecisionKind.STOP
    assert at.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD
    assert at.selected_value == 1.0

    above = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.001)],
                     calibration).plan(
        _state(AWARD), [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)])
    assert above.kind is DecisionKind.ACTION

    below = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=0.5)],
                     calibration).plan(
        _state(AWARD), [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)])
    assert below.kind is DecisionKind.STOP
    assert below.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD


def test_argmax_selects_the_highest_utility_action():
    planner = _planner([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=3.0),
        fixture_bin(AWARD, _F.REVERSE_CHECK, gain=2.0),
    ])
    decision = planner.plan(_state(AWARD), [
        _action(AWARD, "probe", _F.SPECIALIST_PROBE),
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY),
        _action(AWARD, "reverse", _F.REVERSE_CHECK),
    ])
    assert decision.selected_action == "verify"
    assert decision.selected_value == 3.0


def test_ties_break_on_canonical_identity_and_are_recorded():
    planner = _planner([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=2.0),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=2.0),
    ])
    actions = [
        _action(AWARD, "zzz", _F.SPECIALIST_VERIFY),
        _action(AWARD, "aaa", _F.SPECIALIST_PROBE),
    ]
    decision = planner.plan(_state(AWARD), actions)
    reversed_decision = planner.plan(_state(AWARD), list(reversed(actions)))
    assert decision.selected_action == reversed_decision.selected_action
    assert decision.tie_break_reason
    assert "canonical identity" in decision.tie_break_reason
    blob = _scan_blob()
    for forbidden in ("random", "uuid", "time.time", "datetime", "id("):
        assert forbidden not in blob, forbidden


def test_action_order_never_changes_the_decision():
    planner = _planner([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=3.0),
        fixture_bin(AWARD, _F.REVERSE_CHECK, gain=2.0),
    ])
    actions = [
        _action(AWARD, "probe", _F.SPECIALIST_PROBE),
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY),
        _action(AWARD, "reverse", _F.REVERSE_CHECK),
    ]
    first = planner.plan(_state(AWARD), actions)
    second = planner.plan(_state(AWARD), list(reversed(actions)))
    assert first.selected_action == second.selected_action
    assert first.state_signature == second.state_signature
    assert sorted(first.legal_actions) == sorted(second.legal_actions)


def test_the_decision_is_deterministic():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=2.0)])
    actions = [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)]
    assert (planner.plan(_state(AWARD), actions).to_json()
            == planner.plan(_state(AWARD), actions).to_json())


def test_a_duplicate_action_identity_fails():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)])
    action = _action(AWARD, "a#0", _F.SPECIALIST_PROBE)
    with pytest.raises(PlannerError, match="duplicate action identity"):
        planner.plan(_state(AWARD), [action, action])


def test_a_foreign_descriptor_fails():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)])
    foreign = _descriptor(BORDERS, "a#0", BudgetSpendClass.DISCOVERY)
    with pytest.raises(PlannerError, match="descriptor for"):
        planner.plan(
            _state(AWARD),
            [_action(AWARD, "a#0", _F.SPECIALIST_PROBE, descriptor=foreign)])


def test_a_missing_configuration_is_not_stop():
    with pytest.raises(PlannerError, match="needs both"):
        MicroPlanner(None, fixture_calibration())
    with pytest.raises(PlannerError, match="needs both"):
        MicroPlanner(fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE)]),
                     None)
    with pytest.raises(PlannerError, match="unsupported planner_version"):
        MicroPlanner(fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE)]),
                     fixture_calibration(), planner_version="m21-v9")


def test_the_three_stop_reasons_stay_distinct():
    assert {r.value for r in StopReason} == {
        "NO_LEGAL_ACTION", "NO_AFFORDABLE_ACTION", "UTILITY_BELOW_THRESHOLD"}
    # No stop rule of M21's own invention.
    blob = _scan_blob()
    for forbidden in ("residual <", "r_t <", "set_is_stable",
                      "budget_exhausted", "verifier_unknown"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 54-64. Two-step micro-lookahead
# ==========================================================================


def _depth2_history():
    successor = "program_type=LARGE_OPEN_SET|residual=b0"
    return fixture_history([
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0,
                    successors=(SuccessorStat(1.0, successor),)),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=1.2,
                    successors=(SuccessorStat(1.0, successor),)),
        # Successor-state estimates.
        fixture_bin(AWARD, _F.SPECIALIST_PROBE, state_bin=successor, gain=0.1),
        fixture_bin(AWARD, _F.SPECIALIST_VERIFY, state_bin=successor, gain=5.0),
    ])


def test_depth_two_adds_the_expected_best_successor_utility():
    planner = MicroPlanner(_depth2_history(),
                           fixture_calibration(lookahead_depth=2))
    decision = planner.plan(_state(AWARD), [
        _action(AWARD, "probe", _F.SPECIALIST_PROBE),
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY),
    ])
    assert decision.lookahead_depth == 2
    assert decision.successors
    # probe: 1.0 + best successor (verify at 5.0) = 6.0
    # verify: 1.2 + best successor (probe at 0.1, verify is one-shot) = 1.3
    by_action = {s.action_id: s for s in decision.successors}
    assert by_action["probe"].total_value == pytest.approx(6.0)
    assert by_action["verify"].total_value == pytest.approx(1.3)
    assert decision.selected_action == "probe"
    # Undiscounted and additive: no discount factor exists.
    blob = _scan_blob()
    for forbidden in ("gamma_discount", "discount", "decay"):
        assert forbidden not in blob, forbidden


def test_a_one_shot_action_cannot_be_its_own_successor():
    planner = MicroPlanner(_depth2_history(),
                           fixture_calibration(lookahead_depth=2))
    decision = planner.plan(_state(AWARD), [
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY),
    ])
    # Only one action exists and it is one-shot, so no second step is available.
    branch = decision.successors[0].branches[0]
    assert branch[3] == ""
    assert decision.successors[0].expected_successor_utility == 0.0


def test_the_second_action_must_remain_affordable_after_the_first():
    planner = MicroPlanner(_depth2_history(),
                           fixture_calibration(lookahead_depth=2))
    # Only two calls: the first action consumes both, so nothing follows.
    budget = _budget(AWARD, hard_calls=2, discovery_cap=2, verification_cap=2,
                     verification_reserve=0, special_reserves=())
    decision = planner.plan(_state(AWARD, budget=budget), [
        _action(AWARD, "probe", _F.SPECIALIST_PROBE, calls=2),
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY, calls=2),
    ])
    by_action = {s.action_id: s for s in decision.successors}
    assert by_action["probe"].expected_successor_utility == 0.0
    assert by_action["probe"].total_value == pytest.approx(1.0)


def test_depth_two_needs_recorded_successor_statistics():
    planner = MicroPlanner(
        fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)]),
        fixture_calibration(lookahead_depth=2))
    with pytest.raises(PlannerError, match="records none"):
        planner.plan(_state(AWARD), [_action(AWARD, "p", _F.SPECIALIST_PROBE)])
    # Depth 1 over the same package is unaffected.
    depth1 = MicroPlanner(
        fixture_history([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=1.0)]),
        fixture_calibration())
    assert depth1.plan(
        _state(AWARD), [_action(AWARD, "p", _F.SPECIALIST_PROBE)]
    ).kind is DecisionKind.ACTION


def test_successor_probabilities_must_be_a_distribution():
    with pytest.raises(PlannerError, match="sum to"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE,
                    successors=(SuccessorStat(0.4, "x"),))
    with pytest.raises(PlannerError, match="outside \\[0, 1\\]"):
        SuccessorStat(1.4, "x")
    with pytest.raises(PlannerError, match="needs a state bin key"):
        SuccessorStat(1.0, "")
    with pytest.raises(PlannerError, match="repeated"):
        fixture_bin(AWARD, _F.SPECIALIST_PROBE,
                    successors=(SuccessorStat(0.5, "x"), SuccessorStat(0.5, "x")))


def test_no_search_beyond_two_steps():
    blob = _scan_blob()
    for forbidden in ("mcts", "beam", "rollout", "simulate_tree", "expand_node",
                      "depth=3", "recurse"):
        assert forbidden not in blob.casefold(), forbidden
    with pytest.raises(PlannerError, match="unsupported lookahead depth"):
        fixture_calibration(lookahead_depth=3)


# ==========================================================================
# 65-70. §17.1 policy examples, from synthetic bins only
# ==========================================================================


def test_no_relation_specific_branch_exists():
    blob = _scan_blob()
    for relation in CONTRACTS:
        assert relation not in blob, f"the planner branches on {relation}"


def test_capacity_example_further_verification_is_not_worth_it():
    """§17.1: tight cluster + UNKNOWN verifier -> stop wasting verifier loops.

    M21 does not *accept* anything - it returns STOP, and Module 8 finalises
    the already-resolved state.
    """
    planner = MicroPlanner(
        fixture_history([
            fixture_bin(CAPACITY, _F.SPECIALIST_VERIFY, gain=0.05, cost=1.0,
                        residual_bucket="b0"),
            fixture_bin(CAPACITY, _F.COUNTERFACTUAL_VERIFY, gain=0.02, cost=1.0,
                        residual_bucket="b0"),
        ]),
        fixture_calibration(tau_continue=0.0))
    decision = planner.plan(_state(CAPACITY, residual=0.2), [
        _action(CAPACITY, "verify", _F.SPECIALIST_VERIFY),
        _action(CAPACITY, "contrast", _F.COUNTERFACTUAL_VERIFY),
    ])
    assert decision.kind is DecisionKind.STOP
    assert decision.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD
    # It accepted nothing and rejected nothing. The disclaimer is excluded
    # from the scan because its own wording is what denies these.
    scanned = dict(decision.to_json())
    scanned.pop("planner_disclaimer", None)
    payload = json.dumps(scanned).casefold()
    for forbidden in ("accept", "reject", "objectentities", "final"):
        assert forbidden not in payload, forbidden


def test_award_example_verify_the_shortlist_before_a_new_facet():
    """§17.1: novelty high but verification reserve unused -> verify first."""
    planner = MicroPlanner(
        fixture_history([
            fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=0.2, delta_r=0.3,
                        cost=1.0),
            fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=1.4, delta_r=0.1,
                        cost=1.0),
        ]),
        fixture_calibration(tau_continue=0.0))
    verify = _action(AWARD, "verify_shortlist", _F.SPECIALIST_VERIFY,
                     spend_class=BudgetSpendClass.VERIFICATION)
    facet = _action(AWARD, "open_facet", _F.SPECIALIST_PROBE,
                    spend_class=BudgetSpendClass.DISCOVERY)
    decision = planner.plan(_state(AWARD, residual=0.9), [facet, verify])

    assert decision.kind is DecisionKind.ACTION
    assert decision.selected_action == "verify_shortlist"
    # Both were affordable: value decided, not affordability.
    assert set(decision.affordable_actions) == {"verify_shortlist", "open_facet"}


def test_border_example_stops_early_when_everything_is_low_value():
    """§17.1: direct/compass sets stable -> stop early."""
    planner = MicroPlanner(
        fixture_history([
            fixture_bin(BORDERS, _F.SPECIALIST_PROBE, gain=0.01, cost=1.0,
                        residual_bucket="b0"),
            fixture_bin(BORDERS, _F.REVERSE_CHECK, gain=0.02, cost=1.0,
                        residual_bucket="b0"),
        ]),
        fixture_calibration(tau_continue=0.0))
    decision = planner.plan(_state(BORDERS, residual=0.1), [
        _action(BORDERS, "probe", _F.SPECIALIST_PROBE),
        _action(BORDERS, "reverse", _F.REVERSE_CHECK,
                purpose=_P.REVERSE_SINGLETON),
    ])
    assert decision.kind is DecisionKind.STOP
    assert decision.stop_reason is StopReason.UTILITY_BELOW_THRESHOLD


def test_death_example_runs_the_freshness_branch_before_empty():
    """§17.1: null evidence is only failed recall -> run candidate-free first.

    Audit 0024 preserved: failed recall never becomes substantive NULL, and the
    planner emits no empty prediction - it selects an action.
    """
    planner = MicroPlanner(
        fixture_history([
            fixture_bin(DEATH, _F.CANDIDATE_FREE_RECALL, gain=0.9, delta_r=0.4,
                        cost=1.0),
            fixture_bin(DEATH, _F.BLIND_VERIFY, gain=0.05, cost=1.0),
        ]),
        fixture_calibration(tau_continue=0.0))
    decision = planner.plan(_state(DEATH, residual=1.0), [
        _action(DEATH, "candidate_free", _F.CANDIDATE_FREE_RECALL,
                spend_class=BudgetSpendClass.DISCOVERY,
                purpose=_P.CANDIDATE_FREE),
        _action(DEATH, "verify", _F.BLIND_VERIFY,
                spend_class=BudgetSpendClass.VERIFICATION),
    ])
    assert decision.kind is DecisionKind.ACTION
    assert decision.selected_action == "candidate_free"
    payload = json.dumps(decision.to_json()).casefold()
    for forbidden in ("substantive_null", "final_empty", "is_empty",
                      "objectentities"):
        assert forbidden not in payload, forbidden


def test_the_planner_never_touches_numeric_or_stock_factual_state():
    blob = _scan_blob()
    for forbidden in ("recluster", "cluster_values", "tolerance", "0.05",
                      "ALTERNATE_RECOVERED", "listing_gate", "parent_subsidiary",
                      "winner"):
        assert forbidden not in blob, forbidden


# ==========================================================================
# 71-84. Mutation, R_t usage, invariance
# ==========================================================================


def test_r_t_is_context_for_binning_and_never_a_utility_term():
    blob = _scan_blob()
    for forbidden in ("+ residual", "utility += ", "+= r_t", "residual *"):
        assert forbidden not in blob, forbidden

    # Two residuals in the same bucket give the same estimates and the same
    # decision; the residual enters only through the package's own boundaries.
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=2.0)])
    actions = [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)]
    high = planner.plan(_state(AWARD, residual=0.9), actions)
    higher = planner.plan(_state(AWARD, residual=0.95), actions)
    assert high.selected_value == higher.selected_value


def test_an_unused_reserve_alone_creates_no_utility():
    """Module 20 state is context. Value comes from the bins."""
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_VERIFY, gain=0.0,
                                    cost=1.0)])
    generous = _budget(AWARD, verification_reserve=10, verification_cap=14)
    thin = _budget(AWARD, verification_reserve=0, verification_cap=14)
    action = _action(AWARD, "v#0", _F.SPECIALIST_VERIFY,
                     spend_class=BudgetSpendClass.VERIFICATION)
    first = planner.plan(_state(AWARD, budget=generous), [action])
    second = planner.plan(_state(AWARD, budget=thin), [action])
    assert first.selected_value == second.selected_value
    assert first.kind is second.kind is DecisionKind.STOP


def test_the_planner_mutates_nothing_upstream():
    planner = MicroPlanner(_depth2_history(),
                           fixture_calibration(lookahead_depth=2))
    state = _state(AWARD)
    before = {
        "ledger": copy.deepcopy(state.budget_ledger.state()),
        "plan": copy.deepcopy(state.budget_plan),
        "gap": copy.deepcopy(state.coverage_gap.residual.residual),
        "layer4": copy.deepcopy(state.layer4.candidates),
    }
    planner.plan(state, [
        _action(AWARD, "probe", _F.SPECIALIST_PROBE),
        _action(AWARD, "verify", _F.SPECIALIST_VERIFY),
    ])
    assert state.budget_ledger.state() == before["ledger"]
    assert state.budget_plan == before["plan"]
    assert state.coverage_gap.residual.residual == before["gap"]
    assert state.layer4.candidates == before["layer4"]
    # And it holds no reservation of its own.
    assert state.budget_ledger.state().reserved_calls == 0
    assert state.budget_ledger.reservations == ()


def test_the_planner_executes_nothing():
    blob = _scan_blob()
    for forbidden in ("def execute", "def run_action", "def apply", "runtime",
                      "def dispatch", "swap_model", "load_model"):
        assert forbidden not in blob, forbidden


def test_the_decision_round_trips_and_carries_no_factual_output():
    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE, gain=2.0)])
    decision = planner.plan(
        _state(AWARD), [_action(AWARD, "a#0", _F.SPECIALIST_PROBE)])
    payload = json.loads(json.dumps(decision.to_json()))
    assert payload["planner_version"] == PLANNER_VERSION
    assert payload["planner_disclaimer"] == PLANNER_DISCLAIMER
    assert payload["errors"] == []
    assert payload["decision_kind"] == "ACTION"

    scanned = dict(payload)
    scanned.pop("planner_disclaimer", None)
    text = json.dumps(scanned).casefold()
    for forbidden in ("gold", "objectentities", "prediction", "accepted",
                      "rejected", "leaderboard", "f1"):
        assert forbidden not in text, forbidden


def test_the_pipeline_seam_requires_module_19_and_module_20():
    from cover_kbc.models.offline import ScriptedRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    planner = _planner([fixture_bin(AWARD, _F.SPECIALIST_PROBE)])
    with pytest.raises(ValueError, match="needs Module 19"):
        CoverPipeline(ScriptedRuntime({}), PipelineConfig(),
                      micro_planner=planner)


def test_no_dola_no_retrieval_no_training():
    blob = _scan_blob().casefold()
    for forbidden in ("dola", "wikipedia", "wikidata", "retriev", "embedding",
                      "vector_store", "fine_tune"):
        assert forbidden not in blob, forbidden


def test_no_train_val_or_test_is_read():
    for name in M21_MODULES:
        tree = ast.parse((Path("src/cover_kbc/control") / name).read_text())
        for node in ast.walk(tree):
            imported = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in imported:
                assert not module.startswith("cover_kbc.data"), module
    source = "\n".join(
        (Path("src/cover_kbc/control") / name).read_text() for name in M21_MODULES)
    assert "historical bins on TRAIN" in source


def test_the_model_budget_is_unchanged():
    result = subprocess.run(
        ["python", "scripts/audit_model_budget.py",
         "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml"],
        capture_output=True, text=True, check=True)
    assert "RESULT: PASS" in result.stdout and "28.67B" in result.stdout


def test_benchmark_is_untouched():
    for args in (
        ["git", "status", "--porcelain", "benchmark/"],
        ["git", "diff", "--", "benchmark/"],
        ["git", "diff", "--cached", "--", "benchmark/"],
    ):
        assert subprocess.run(
            args, capture_output=True, text=True, check=True).stdout == "", args
