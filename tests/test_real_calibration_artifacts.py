"""The three REAL TRAIN-derived artifacts, verified as shipped.

These are the files a production run will load. They were derived once from the
real frozen-model collection and must not be regenerated, so everything here is
a check on bytes that already exist rather than on a pipeline that could be
re-run to make a test pass.

Skipped wholesale when the artifacts are absent: a developer checkout without
them is a legitimate state, and a test that silently passed on missing files
would be worse than no test.

One blocker is currently open and is recorded here as a strict ``xfail`` rather
than a comment - see ``test_the_real_artifacts_reach_full_validation_ready``.
When it is resolved the strict marker turns the unexpected pass into a failure,
which is the point: the expectation has to be updated deliberately.
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
        "8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070",
    "m21_historical_bins.json":
        "8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5",
    "m21_planner_calibration.json":
        "a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade",
}
COLLECTION_SHA = "264c980361a513078903526440c72adc6e10edaf"
DERIVATION_SHA = "b1804646dec3d2343dcf2cf8b277529071b89485"
EXPECTED_BINS = 64

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
    assert planner.alpha == 1.0 and planner.kappa == 1.0
    assert planner.tau_continue == 0.0
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
# P2-A — the open blocker
# --------------------------------------------------------------------------


def test_the_real_history_has_bins_without_successor_statistics(loaded) -> None:
    """The measured state of the shipped package. Five of sixty-four.

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


def test_the_loader_refuses_the_real_package_for_depth_two(loaded) -> None:
    """Refused at load, so it cannot fail at an arbitrary row hours into a run."""
    from cover_kbc.controller_calibration.production import (
        ProductionCalibrationError,
        load_production_calibration,
    )

    assert loaded["planner"].lookahead_depth == 2
    config = yaml.safe_load(VAL_CONFIG.read_text())
    with pytest.raises(ProductionCalibrationError, match="no successor"):
        load_production_calibration(config, base_dir=VAL_CONFIG.parent)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P2-A: the real planner calibration declares lookahead_depth 2 while "
        "five of the sixty-four shipped bins record no successor statistics. "
        "Module 21 raises when it ranks an action from one of them, so the "
        "package cannot drive a production run. The derivation rule that "
        "produced the 2 is fixed as of audit 0052 and now yields 1 for this "
        "package, but the shipped artifact still carries the old value: it was "
        "derived under the old rule and only a real re-derivation may change "
        "it. When that re-derivation lands this xfail turns into an unexpected "
        "pass and must be removed deliberately."),
)
def test_the_real_artifacts_reach_full_validation_ready() -> None:
    """The end state this milestone exists to reach."""
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    report = evaluate_validation_readiness(
        config, base_dir=VAL_CONFIG.parent, split=config["experiment"]["split"],
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert report.state is ReadinessState.FULL_VALIDATION_READY, report.blockers


def test_everything_except_depth_two_already_satisfies_the_gate() -> None:
    """So the one open blocker is visible rather than buried in a list."""
    config = yaml.safe_load(VAL_CONFIG.read_text())
    provenance = config["calibration_provenance"]
    report = evaluate_validation_readiness(
        config, base_dir=VAL_CONFIG.parent, split=config["experiment"]["split"],
        expected_collection_repo_sha=provenance["collection_repo_sha"],
        expected_derivation_repo_sha=provenance["derivation_repo_sha"])
    assert len(report.blockers) == 1, report.blockers
    assert "successor" in report.blockers[0]
    satisfied = " | ".join(report.satisfied)
    assert "6 TRAIN_CALIBRATED relation budgets" in satisfied
    assert "split: val" in satisfied
    assert "production mode" in satisfied
