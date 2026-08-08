"""The three REAL TRAIN-derived artifacts, verified as shipped.

These are the files a production run will load. They were derived once from the
real frozen-model collection and must not be regenerated, so everything here is
a check on bytes that already exist rather than on a pipeline that could be
re-run to make a test pass.

Skipped wholesale when the artifacts are absent: a developer checkout without
them is a legitimate state, and a test that silently passed on missing files
would be worse than no test.

These are the **final** artifacts: derived at ``78ad89d3`` after the corrected
package-wide lookahead rule (Audit 0052) and activated in Audit 0059. The
package they describe supports depth-1 planning, which is what it honestly
observed, and the readiness gate now passes on them with zero blockers.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest
import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.control.budget_types import CalibrationSource
from cover_kbc.control.historical_bins import load_history
from cover_kbc.control.micro_planner import load_planner_calibration
from cover_kbc.control.planner_types import EstimateSource
from cover_kbc.control.relation_budget import load_calibrations, relation_policy
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_validation_readiness,
)
from cover_kbc.paths import REPO_ROOT

CALIBRATION = REPO_ROOT / "configs" / "calibration"
VAL_CONFIG = REPO_ROOT / "configs" / "experiments" / "cover_kbc_v2_validation.yaml"

#: The hashes the owner published for the one real derivation. Anything else is
#: a different calibration, whatever it is named.
EXPECTED_SHA256 = {
    "m20_relation_budget.json":
        "8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68",
    "m21_historical_bins.json":
        "d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071",
    "m21_planner_calibration.json":
        "36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05",
}
COLLECTION_SHA = "264c980361a513078903526440c72adc6e10edaf"
DERIVATION_SHA = "78ad89d3cd8a321f500807b11477fce2f8579e32"
EXPECTED_BINS = 64

#: §17's six coefficients as the real derivation measured them. Pinned exactly,
#: not by range: these are the numbers a leaderboard run will price actions
#: with, and a silent change to any of them is a different system.
EXPECTED_COEFFICIENTS = {
    "alpha": 1.0, "beta": 10.084164, "gamma": 0.0, "delta": 0.069917,
    "eta": 0.143625, "kappa": 1.0, "tau_continue": 0.0,
}
EXPECTED_LOOKAHEAD_DEPTH = 1

pytestmark = pytest.mark.skipif(
    not (CALIBRATION / "m20_relation_budget.json").is_file(),
    reason="the real TRAIN-derived calibration artifacts are not in this checkout",
)


def _payload(name: str) -> dict:
    return json.loads((CALIBRATION / name).read_text())


@pytest.fixture(scope="module")
def loaded():
    """All three, through their canonical production loaders."""
    return {
        "budgets": load_calibrations(_payload("m20_relation_budget.json")),
        "history": load_history(_payload("m21_historical_bins.json")),
        "planner": load_planner_calibration(
            _payload("m21_planner_calibration.json")),
    }


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_SHA256.items()))
def test_the_artifact_hashes_are_exactly_the_published_ones(name, expected) -> None:
    actual = hashlib.sha256((CALIBRATION / name).read_bytes()).hexdigest()
    assert actual == expected, f"{name}: {actual}"


def test_the_provenance_names_one_collection_and_one_derivation(loaded) -> None:
    """Three files from different runs would price one system with another's."""
    blocks = {name: _payload(name)["provenance"] for name in EXPECTED_SHA256}
    for field in ("collection_repo_sha", "derivation_repo_sha", "train_sha256",
                  "telemetry_sha256", "derivation_schema_version"):
        values = {block[field] for block in blocks.values()}
        assert len(values) == 1, f"{field} disagrees: {values}"
    reference = next(iter(blocks.values()))
    assert reference["collection_repo_sha"] == COLLECTION_SHA
    assert reference["derivation_repo_sha"] == DERIVATION_SHA
    assert reference["train_sha256"] == (
        "cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74")


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------


def test_every_relation_has_a_train_calibrated_budget(loaded) -> None:
    budgets = loaded["budgets"]
    assert set(budgets) == set(CONTRACTS)
    for relation, budget in budgets.items():
        assert budget.calibration_source is CalibrationSource.TRAIN_CALIBRATED
        assert budget.discovery_cap <= budget.hard_calls
        assert budget.verification_cap <= budget.hard_calls
        assert budget.verification_reserve <= budget.verification_cap
        declared = set(relation_policy(relation).special_reserve_purposes)
        assert {p for p, _ in budget.special_reserves} <= declared, relation


def test_table_6_is_still_the_owner_of_which_reserves_exist(loaded) -> None:
    """TRAIN sized the reserves; it did not invent any."""
    for relation, budget in loaded["budgets"].items():
        policy = relation_policy(relation)
        if policy.verification_hard_reserved:
            assert budget.verification_reserve > 0, (
                f"{relation} is hard-reserved in Table 6 but has no floor")
        assert {p for p, _ in budget.special_reserves} <= set(
            policy.special_reserve_purposes)


def test_the_history_is_the_real_sixty_four_bin_package(loaded) -> None:
    history = loaded["history"]
    assert len(history.bins) == EXPECTED_BINS
    assert history.source is EstimateSource.TRAIN_CALIBRATED
    assert history.fallback_state_bin == "__fallback__"
    assert {b.relation for b in history.bins} == set(CONTRACTS)


def test_the_planner_calibration_is_the_real_one(loaded) -> None:
    planner = loaded["planner"]
    assert planner.source is EstimateSource.TRAIN_CALIBRATED
    for name, value in EXPECTED_COEFFICIENTS.items():
        assert getattr(planner, name) == value, name
    assert planner.lookahead_depth == EXPECTED_LOOKAHEAD_DEPTH
    for name in ("alpha", "beta", "gamma", "delta", "eta", "kappa"):
        assert getattr(planner, name) >= 0.0


def test_c02_is_carried_truthfully_into_production(loaded) -> None:
    """ΔH never moved in the real collection, so γ is inert and says so."""
    assert loaded["planner"].gamma == 0.0
    assert all(b.expected_delta_h == 0.0 for b in loaded["history"].bins)


def test_every_estimate_is_finite(loaded) -> None:
    import math

    for entry in loaded["history"].bins:
        for name in ("expected_verified_gain", "expected_delta_r",
                     "expected_delta_h", "expected_cost",
                     "expected_redundancy", "expected_fp"):
            assert math.isfinite(getattr(entry, name)), (entry.key, name)
    for name in EXPECTED_SHA256:
        blob = (CALIBRATION / name).read_text()
        assert "NaN" not in blob and "Infinity" not in blob


# --------------------------------------------------------------------------
# no TRAIN factual identity, no raw telemetry
# --------------------------------------------------------------------------


def _string_leaves(payload, out: list[str]) -> list[str]:
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


def test_no_train_gold_or_subject_reaches_the_real_artifacts() -> None:
    """The decisive check, over every string in the shipped bytes."""
    train = REPO_ROOT / "benchmark" / "data" / "train.jsonl"
    gold: set[str] = set()
    subjects: set[str] = set()
    for line in train.read_text().splitlines():
        row = json.loads(line)
        subjects.add(row["SubjectEntity"])
        for entry in row["ObjectEntities"]:
            gold.update([entry] if isinstance(entry, str) else entry)

    for name in EXPECTED_SHA256:
        strings = _string_leaves(_payload(name), [])
        folded = {s.casefold() for s in strings}
        for wanted in gold | subjects:
            needle = wanted.casefold()
            assert needle not in folded, f"{name}: exact leak {wanted!r}"
            if len(needle) >= 4:
                pattern = re.compile(
                    rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])")
                for candidate in strings:
                    assert not pattern.search(candidate.casefold()), (
                        f"{name}: {wanted!r} inside {candidate!r}")


def test_the_artifacts_carry_no_telemetry_or_candidate_identity() -> None:
    forbidden = {"ObjectEntities", "SubjectEntity", "gold", "aliases", "prompt",
                 "raw_output", "candidates_added", "candidates_supported",
                 "candidates_contradicted", "candidates_named",
                 "candidates_touched", "operation_id", "row_index"}
    for name in EXPECTED_SHA256:
        present = set(_string_leaves(_payload(name), [])) & forbidden
        assert not present, f"{name}: {present}"


def test_the_artifacts_are_small_enough_to_be_statistics() -> None:
    """A lookup table of 477 answers would not fit in 55 KB of bins."""
    total = sum((CALIBRATION / name).stat().st_size for name in EXPECTED_SHA256)
    assert total < 200_000, total


# --------------------------------------------------------------------------
# P2-A — the five terminal bins, and why the depth is 1
# --------------------------------------------------------------------------


def test_the_real_history_has_bins_without_successor_statistics(loaded) -> None:
    """The measured state of the shipped package. Five of sixty-four.

    These are the bins the corrected package-wide rule reads to decide the
    lookahead depth: because they exist, depth 2 is unsupportable and the real
    derivation wrote 1. They are a fact about the collection, not a defect.

    Not sparse noise: these bins carry 31-67 observations each. They have no
    successor because the action was the **last** one in every chain that
    reached them - Module 18's reverse check is the third and final Layer-4
    round for borders under ``max_control_rounds_per_catalogue: 3``, and a
    terminal action has nothing after it to observe.
    """
    starved = [b for b in loaded["history"].bins if not b.successors]
    assert len(starved) == 5
    assert {b.relation for b in starved} == {
        "countryLandBordersCountry", "hasArea", "hasCapacity"}
    assert {b.action_family.value for b in starved} == {
        "REVERSE_CHECK", "CANDIDATE_FREE_RECALL"}
    # Well-supported, which is what makes this a real gap rather than a sparse
    # bin the minimum-support rule should have dropped.
    assert min(b.support_count for b in starved) >= 31
    # ...and one of them is a fallback bin, so it catches any unmapped state.
    assert any(b.state_bin_key == "__fallback__" for b in starved)


def test_a_starved_bin_is_reachable_through_the_fallback(loaded) -> None:
    """`lookup` routes an unseen state here, so this is not a corner case."""
    from cover_kbc.control.planner_types import ActionFamily

    entry = loaded["history"].lookup(
        relation="countryLandBordersCountry", program_type="SMALL_SET",
        state_bin_key="a state the collection never reached",
        family=ActionFamily.REVERSE_CHECK)
    assert entry.state_bin_key == "__fallback__"
    assert entry.successors == ()


def test_the_real_package_is_accepted_because_it_asks_for_depth_one(loaded) -> None:
    """The five terminal bins are why the depth is 1, and 1 is loadable.

    Until Audit 0058 this test asserted the opposite - the shipped artifact
    declared depth 2 over the same five successor-less bins and the loader
    refused it. The re-derivation at ``78ad89d3`` applied the corrected
    package-wide rule and produced depth 1, so the same history now loads. The
    guard that did the refusing is unchanged and still armed; the test below
    proves it.
    """
    from cover_kbc.controller_calibration.production import (
        load_production_calibration,
    )

    assert loaded["planner"].lookahead_depth == 1
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    calibration = load_production_calibration(
        config, base_dir=VAL_CONFIG.parent,
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert calibration.planner.lookahead_depth == 1
    assert len(calibration.history.bins) == EXPECTED_BINS
    assert len(calibration.budgets) == len(CONTRACTS)


def test_the_depth_two_guard_is_still_armed_over_this_history(tmp_path) -> None:
    """The safety property the previous test used to carry, kept alive.

    Depth 2 over a history whose bins record no successor is a package that
    raises mid-run, and the loader must refuse it at load time rather than at
    an arbitrary row hours in. Exercised by pairing the **real** history with a
    planner calibration that asks for depth 2 - nothing in the shipped bytes is
    modified, and the refusal must still name the missing successors.
    """
    from cover_kbc.controller_calibration.production import (
        ProductionCalibrationError,
        load_production_calibration,
    )

    payload = _payload("m21_planner_calibration.json")
    assert payload["lookahead_depth"] == 1
    payload["lookahead_depth"] = 2
    forged = tmp_path / "m21_planner_calibration.json"
    forged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    config = yaml.safe_load(VAL_CONFIG.read_text())
    config["micro_planner"]["planner_calibration"] = str(forged)
    config["micro_planner"]["planner_calibration_sha256"] = hashlib.sha256(
        forged.read_bytes()).hexdigest()
    with pytest.raises(ProductionCalibrationError, match="no successor"):
        load_production_calibration(config, base_dir=VAL_CONFIG.parent)


def test_the_real_artifacts_reach_full_validation_ready() -> None:
    """The end state this milestone exists to reach.

    Held as a strict ``xfail`` from Audit 0052 to Audit 0058, because the
    shipped planner artifact still declared ``lookahead_depth 2`` while five of
    its sixty-four bins recorded no successor statistics - a package that would
    raise mid-run. The corrected derivation rule (Audit 0052) yields 1 for this
    history, and the real re-derivation at ``78ad89d3`` produced an artifact
    that says so. The xfail was removed deliberately when those bytes landed
    (Audit 0059); the assertions below are unchanged.
    """
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    report = evaluate_validation_readiness(
        config, base_dir=VAL_CONFIG.parent, split=config["experiment"]["split"],
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert report.state is ReadinessState.FULL_VALIDATION_READY, report.blockers


def test_the_gate_reports_no_blockers_at_all() -> None:
    """Zero, not "few". A single surviving blocker is a refusal.

    The list of *satisfied* conditions is asserted alongside, because a gate
    that returned an empty blocker list by checking nothing would also pass an
    emptiness test.
    """
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    report = evaluate_validation_readiness(
        config, base_dir=VAL_CONFIG.parent, split=config["experiment"]["split"],
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert list(report.blockers) == [], report.blockers
    assert report.may_run_validation is True
    satisfied = " | ".join(report.satisfied)
    assert "6 TRAIN_CALIBRATED relation budgets" in satisfied
    assert "split: val" in satisfied
    assert "production mode" in satisfied
    assert "64 historical bin(s)" in satisfied
    assert "all six relations budgeted" in satisfied
