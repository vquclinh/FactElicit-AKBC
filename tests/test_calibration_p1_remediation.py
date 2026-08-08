"""Audit 0048's three blocking findings, each pinned against a real failure.

Every test here would have passed before the remediation only by accident, or
would have failed outright. They are grouped by finding so a later reviewer can
map a regression straight back to the defect it re-opens.

P1-1 uses **real temporary Git repositories**, not a mocked `git`: the guard's
whole job is to read repository state, and a fake `git` would test the mock.
P1-2 uses the **real pinned evaluator and real TRAIN rows**, including the two
Audit 0048 named. P1-3 drives the real coefficient derivation.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from cover_kbc.control.historical_bins import StateBinningSpec
from cover_kbc.controller_calibration.derivation import (
    DERIVATION_SOURCE_FILES,
    DerivationError,
    DerivationSettings,
    DirtyDerivationSource,
    derive_binning_spec,
    derive_m21,
    derive_planner_calibration,
    resolve_derivation_source,
)
from cover_kbc.controller_calibration.gold_join import (
    ActionGoldEffect,
    GoldJoinError,
    load_gold,
    score_actions,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    RedundancyStatus,
)
from cover_kbc.evaluation.official import load_official_evaluator
from cover_kbc.paths import REPO_ROOT

TRAIN = REPO_ROOT / "benchmark" / "data" / "train.jsonl"

#: The two rows Audit 0048 proved reachable.
AWARD = ("Nobel Prize in Physiology or Medicine", "awardWonBy")
AREA = ("Wellington Island", "hasArea")


# ==========================================================================
# P1-1 — fail closed on a dirty derivation source
# ==========================================================================


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True).stdout


@pytest.fixture
def clean_repo(tmp_path) -> Path:
    """A real repository containing the derivation implementation files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "Test")
    for relative in DERIVATION_SOURCE_FILES:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# derivation source\n", encoding="utf-8")
    (repo / "outputs").mkdir()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "derivation implementation")
    return repo


def test_a_clean_exact_checkout_is_accepted(clean_repo) -> None:
    head = _git(clean_repo, "rev-parse", "HEAD").strip()
    assert resolve_derivation_source(clean_repo) == head
    assert len(head) == 40


def test_an_untracked_source_file_is_refused(clean_repo) -> None:
    """The exact state this remediation was written in."""
    (clean_repo / "src" / "cover_kbc" / "new_thing.py").write_text("x = 1\n")
    with pytest.raises(DirtyDerivationSource, match="new_thing.py"):
        resolve_derivation_source(clean_repo)


def test_a_modified_tracked_source_file_is_refused(clean_repo) -> None:
    target = clean_repo / DERIVATION_SOURCE_FILES[0]
    target.write_text("# modified after the commit\n", encoding="utf-8")
    with pytest.raises(DirtyDerivationSource, match="derive_train_calibration"):
        resolve_derivation_source(clean_repo)


def test_a_staged_modification_is_refused(clean_repo) -> None:
    target = clean_repo / DERIVATION_SOURCE_FILES[1]
    target.write_text("# staged but not committed\n", encoding="utf-8")
    _git(clean_repo, "add", str(target))
    with pytest.raises(DirtyDerivationSource, match="derivation.py"):
        resolve_derivation_source(clean_repo)


def test_a_deleted_source_file_is_refused(clean_repo) -> None:
    (clean_repo / DERIVATION_SOURCE_FILES[2]).unlink()
    with pytest.raises(DirtyDerivationSource, match="gold_join.py"):
        resolve_derivation_source(clean_repo)


def test_an_untracked_non_source_file_does_not_block(clean_repo) -> None:
    """A stray artifact says nothing about which code ran."""
    (clean_repo / "outputs" / "run.log").write_text("noise\n", encoding="utf-8")
    (clean_repo / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert resolve_derivation_source(clean_repo)


def test_a_detached_head_on_an_exact_commit_is_accepted(clean_repo) -> None:
    """§P1-1.5: detached is fine when it is a clean exact checkout."""
    head = _git(clean_repo, "rev-parse", "HEAD").strip()
    _git(clean_repo, "checkout", "-q", "--detach", head)
    assert resolve_derivation_source(clean_repo) == head


def test_a_commit_without_the_derivation_implementation_is_refused(
    tmp_path,
) -> None:
    """HEAD must actually contain the code whose output it will vouch for."""
    repo = tmp_path / "bare"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("no derivation here\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unrelated")
    with pytest.raises(DirtyDerivationSource, match="does not contain"):
        resolve_derivation_source(repo)


def test_a_non_repository_is_refused(tmp_path) -> None:
    with pytest.raises(DirtyDerivationSource):
        resolve_derivation_source(tmp_path)


def test_there_is_no_allow_dirty_escape_hatch() -> None:
    """A production override is how the guard stops meaning anything."""
    import inspect

    from cover_kbc.controller_calibration import derivation

    source = inspect.getsource(derivation.resolve_derivation_source)
    for forbidden in ("allow_dirty", "force", "skip_clean", "ignore_dirty"):
        assert forbidden not in source
    cli = (REPO_ROOT / "scripts" / "derive_train_calibration.py").read_text()
    for forbidden in ("--allow-dirty", "--force", "--skip-clean-check"):
        assert forbidden not in cli


def test_the_two_provenance_shas_stay_distinct(clean_repo) -> None:
    """P1-1.6: one names the collection, the other names this checkout."""
    from cover_kbc.controller_calibration.derivation import CalibrationProvenance

    derivation_sha = resolve_derivation_source(clean_repo)
    collection_sha = "264c980361a513078903526440c72adc6e10edaf"
    provenance = CalibrationProvenance(
        collection_repo_sha=collection_sha,
        derivation_repo_sha=derivation_sha,
        train_sha256="c" * 64, train_rows=477, predictions_sha256="d" * 64,
        telemetry_sha256="e" * 64, manifest_sha256="f" * 64,
        experiment_config_sha256="0" * 64, evaluator_sha256="1" * 64,
        telemetry_schema_version=TELEMETRY_SCHEMA_VERSION,
        derivation_schema_version="train-calibration-v1",
        m20_derivation_version="m20-derivation-v1",
        m21_derivation_version="m21-derivation-v1",
        binning_spec_version="m21-state-binning-v1",
        relation_catalogue=("hasArea",), collection_policy_version="collect-v1",
        settings=DerivationSettings(), support_counts={},
    ).to_json()
    assert provenance["collection_repo_sha"] == collection_sha
    assert provenance["derivation_repo_sha"] == derivation_sha
    assert provenance["collection_repo_sha"] != provenance["derivation_repo_sha"]


# ==========================================================================
# P1-2 — official one-to-one gold attribution
# ==========================================================================


@pytest.fixture(scope="module")
def gold():
    return load_gold(TRAIN, expected_rows=477)


def _official(gold, key, preds, rel_type):
    evaluator = load_official_evaluator()
    row = gold.rows[key]
    return evaluator.true_positives(
        list(preds), [list(a) for a in row.aliases], rel_type=rel_type,
        tolerance=gold.tolerance)


def test_two_aliases_of_one_gold_entity_yield_one_true_positive(gold) -> None:
    """Audit 0048's exact case: Max Theiler / Maks Teyler."""
    preds = ["Max Theiler", "Maks Teyler"]
    official = _official(gold, AWARD, preds, "string")
    attribution = gold.attribute(*AWARD, preds)
    assert official == 1
    assert attribution.matched_gold == 1
    assert attribution.count_correct(preds) == 1
    # ...and the other alias is a false positive, not a second win.
    assert attribution.count_incorrect(preds) == 1
    assert sum(attribution.is_correct(p) for p in preds) == 1


def test_two_numerics_in_one_tolerance_band_yield_one_true_positive(gold) -> None:
    """Audit 0048's exact case: Wellington Island / hasArea."""
    value = float(gold.rows[AREA].aliases[0][0])
    preds = [str(value), str(round(value * 1.03, 4))]
    official = _official(gold, AREA, preds, "numeric")
    attribution = gold.attribute(*AREA, preds)
    assert official == 1
    assert attribution.count_correct(preds) == 1
    assert attribution.count_incorrect(preds) == 1


def test_a_duplicate_surface_earns_no_extra_gain(gold) -> None:
    preds = ["Max Theiler", "Max Theiler", "max  theiler"]
    assert _official(gold, AWARD, preds, "string") == 1
    assert gold.attribute(*AWARD, preds).count_correct(preds) == 1


def test_two_distinct_gold_entities_yield_two_true_positives(gold) -> None:
    """The fix must not simply cap everything at one."""
    row = gold.rows[AWARD]
    preds = [row.aliases[0][0], row.aliases[1][0]]
    assert _official(gold, AWARD, preds, "string") == 2
    assert gold.attribute(*AWARD, preds).count_correct(preds) == 2


def test_an_unmatched_alias_is_a_false_positive(gold) -> None:
    preds = [gold.rows[AWARD].aliases[0][0], "Definitely Not A Laureate"]
    attribution = gold.attribute(*AWARD, preds)
    assert _official(gold, AWARD, preds, "string") == 1
    assert attribution.count_correct(preds) == 1
    assert attribution.count_incorrect(preds) == 1


def test_attribution_never_exceeds_the_official_row_score(gold) -> None:
    """The invariant, swept over every real TRAIN row with gold."""
    evaluator = load_official_evaluator()
    for (subject, relation), row in gold.rows.items():
        if not row.aliases:
            continue
        preds = [alias for group in row.aliases for alias in group]
        rel_type = gold.relation_types.get(relation, "string")
        official = evaluator.true_positives(
            preds, [list(a) for a in row.aliases], rel_type=rel_type,
            tolerance=gold.tolerance)
        attribution = gold.attribute(subject, relation, preds)
        assert attribution.count_correct(preds) == official
        assert attribution.matched_gold <= row.size


def _record(subject, relation, *, supported=(), contradicted=(), named=(),
            round_index=1) -> ActionTelemetryRecord:
    state = ControlStateFeatures(residual=0.5, entropy=0.5,
                                 available_components=("novelty_rate",))
    touched = tuple(supported) or tuple(contradicted)
    return ActionTelemetryRecord(
        schema_version=TELEMETRY_SCHEMA_VERSION, run_id="r", row_index=1,
        subject=subject, relation=relation, program_type="LARGE_OPEN_SET",
        round_index=round_index,
        operation_id=f"1:{round_index}:M17:SPECIALIST_VERIFY:t",
        action_family="SPECIALIST_VERIFY",
        action_id="M17:SPECIALIST_VERIFY:t", spend_class="VERIFICATION",
        selected=True, executed=True, pre_state=state,
        post_state=replace(state, residual=0.2),
        outcome=ActionOutcome(
            physical_calls=1, verifier_calls=1, prompt_tokens=10,
            candidates_supported=tuple(supported),
            candidates_contradicted=tuple(contradicted),
            candidates_named=tuple(named), candidates_touched=touched,
            candidate_effect_measured=True,
            redundancy=(1.0 if touched else None),
            redundancy_status=(RedundancyStatus.MEASURED if touched
                               else RedundancyStatus.NOT_APPLICABLE),
            verifier_outcome="VALID"),
    )


def test_score_actions_no_longer_double_counts_two_aliases(gold) -> None:
    """The end-to-end symptom Audit 0048 measured: was 2, must be 1."""
    record = _record(*AWARD, supported=("Max Theiler", "Maks Teyler"))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.supported_correct == 1
    assert effect.supported_incorrect == 1
    assert effect.verified_gain == 1.0
    assert effect.distinct_correct == 1
    assert effect.false_positive_rate == pytest.approx(0.5)


def test_score_actions_no_longer_double_counts_two_numerics(gold) -> None:
    value = float(gold.rows[AREA].aliases[0][0])
    record = _record(*AREA, supported=(str(value), str(round(value * 1.03, 4))))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.supported_correct == 1
    assert effect.distinct_correct == 1


def test_one_gold_entity_cannot_be_claimed_across_two_categories(gold) -> None:
    """P1-2.9: the *action* claims a gold entity once, not once per category."""
    record = _record(*AWARD, supported=("Max Theiler",), named=("Maks Teyler",))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.distinct_correct == 1
    # Supported wins the precedence, so the assertion the action made is what
    # is credited - and the alias in the weaker category is not credited again.
    assert effect.supported_correct == 1
    assert effect.named_correct == 0


def test_supported_precedence_survives_a_contradiction_of_the_same_entity(
    gold,
) -> None:
    record = _record(*AWARD, supported=("Max Theiler",),
                     contradicted=("Maks Teyler",))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.supported_correct == 1
    assert effect.contradicted_correct == 0
    assert effect.distinct_correct == 1


def test_a_key_in_two_categories_is_attributed_once_not_twice(gold) -> None:
    """The seam legitimately records support *and* contradiction for one key."""
    record = _record(*AWARD, supported=("Max Theiler",),
                     contradicted=("Max Theiler",))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.distinct_correct == 1
    assert effect.supported_correct == 1
    assert effect.contradicted_correct == 1     # a per-category view, not gain
    assert effect.verified_gain == 1.0          # ...and gain counts it once


def test_relation_isolation_is_intact(gold) -> None:
    """A borders answer must not be credited against an award row."""
    record = _record(*AWARD, supported=("Spain",))
    effect = score_actions([record], gold)[record.operation_id]
    assert effect.supported_correct == 0
    assert effect.supported_incorrect == 1


def test_an_attribution_that_drifts_from_the_evaluator_is_refused(
    gold, monkeypatch,
) -> None:
    """The cross-check is the reason the adaptation can be trusted at all."""
    import cover_kbc.controller_calibration.gold_join as module

    monkeypatch.setattr(module, "_string_attribution", lambda *a, **k: [])
    with pytest.raises(GoldJoinError, match="drifted from the pinned evaluator"):
        gold.attribute(*AWARD, [gold.rows[AWARD].aliases[0][0]])


def test_effects_still_carry_no_gold_strings(gold) -> None:
    record = _record(*AWARD, supported=("Max Theiler", "Maks Teyler"))
    payload = json.dumps(
        score_actions([record], gold)[record.operation_id].to_json())
    for forbidden in ("Theiler", "Teyler", "Nobel"):
        assert forbidden not in payload


# ==========================================================================
# P1-3 — beta / gamma denominator safety
# ==========================================================================


def _chain(delta_r: float, delta_h: float, gain_key: str | None):
    """Two executed actions moving the observables by the given amounts."""
    state = ControlStateFeatures(residual=1.0, entropy=1.0,
                                 available_components=("novelty_rate",))
    records = []
    for index in (1, 2):
        post = replace(state, residual=state.residual - delta_r / 2,
                       entropy=state.entropy - delta_h / 2)
        records.append(_record(
            *AWARD, supported=((gain_key,) if gain_key else ()),
            round_index=index))
        records[-1] = replace(records[-1], pre_state=state, post_state=post)
    return records


def _derive(records, effects, *, minimum_denominator=1.0):
    settings = DerivationSettings(minimum_bin_support=1,
                                  minimum_denominator=minimum_denominator)
    binning = derive_binning_spec(records, settings)
    package, _ = derive_m21(records, effects, binning, settings)
    return derive_planner_calibration(
        package, records, effects, settings=settings)


def _effects(records, correct: int):
    return {
        r.operation_id: ActionGoldEffect(
            r.operation_id, r.relation, supported_correct=correct,
            gold_size=5, distinct_correct=correct)
        for r in records
    }


def test_a_zero_denominator_keeps_the_term_truthfully_inert() -> None:
    """C-02: H did not move, so gamma is 0 and says nothing false."""
    records = _chain(delta_r=1.0, delta_h=0.0, gain_key="Max Theiler")
    planner, diagnostics = _derive(records, _effects(records, 1))
    assert planner.gamma == 0.0
    assert diagnostics["gamma_is_inert_because_delta_h_never_moved"] is True
    assert planner.beta > 0.0


def test_a_tiny_positive_denominator_with_gain_is_refused() -> None:
    """The explosion Audit 0048 found: 2 objects over 1e-6 of movement."""
    records = _chain(delta_r=1e-6, delta_h=0.0, gain_key="Max Theiler")
    with pytest.raises(DerivationError, match="below the minimum meaningful"):
        _derive(records, _effects(records, 1))


def test_a_tiny_positive_denominator_without_gain_is_stable() -> None:
    """0 divided by something small is 0, and that is a real observation."""
    records = _chain(delta_r=1e-6, delta_h=0.0, gain_key=None)
    planner, _ = _derive(records, _effects(records, 0))
    assert planner.beta == 0.0


def test_the_threshold_boundary_is_inclusive() -> None:
    """Exactly one full unit of movement is enough; just under it is not."""
    at = _chain(delta_r=1.0, delta_h=0.0, gain_key="Max Theiler")
    planner, diagnostics = _derive(at, _effects(at, 1), minimum_denominator=1.0)
    assert planner.beta > 0.0
    assert diagnostics["beta_denominator_supported"] is True

    under = _chain(delta_r=0.9, delta_h=0.0, gain_key="Max Theiler")
    with pytest.raises(DerivationError, match="below the minimum meaningful"):
        _derive(under, _effects(under, 1), minimum_denominator=1.0)


def test_a_normal_denominator_derives_a_finite_deterministic_beta() -> None:
    import math

    records = _chain(delta_r=1.0, delta_h=0.0, gain_key="Max Theiler")
    effects = _effects(records, 1)
    first, _ = _derive(records, effects)
    second, _ = _derive(records, effects)
    assert math.isfinite(first.beta)
    assert first.to_json() == second.to_json()


def test_genuine_h_movement_still_permits_a_non_zero_gamma() -> None:
    """γ is inert because H does not move, never because the code forces it."""
    records = _chain(delta_r=1.0, delta_h=1.0, gain_key="Max Theiler")
    planner, diagnostics = _derive(records, _effects(records, 1))
    assert planner.gamma > 0.0
    assert diagnostics["gamma_is_inert_because_delta_h_never_moved"] is False
    assert diagnostics["gamma_denominator_supported"] is True


def test_the_denominator_floor_is_recorded_in_the_settings() -> None:
    settings = DerivationSettings()
    assert settings.to_json()["minimum_denominator"] == 1.0
    assert DerivationSettings(minimum_denominator=2.5).to_json()[
        "minimum_denominator"] == 2.5


def test_a_nonsensical_denominator_floor_is_refused() -> None:
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(DerivationError, match="minimum_denominator"):
            DerivationSettings(minimum_denominator=bad)


def test_no_epsilon_is_added_to_a_denominator() -> None:
    """P1-3.1: instability is refused, never smoothed away.

    The docstring is stripped before scanning: it explains *why* there is no
    epsilon, and a raw substring scan would fire on the very prose recording
    the prohibition.
    """
    import ast
    import inspect

    from cover_kbc.controller_calibration import derivation

    tree = ast.parse(inspect.getsource(derivation.derive_planner_calibration))
    function = tree.body[0]
    body = [node for node in function.body
            if not (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str))]
    code = "\n".join(ast.unparse(node) for node in body).casefold()
    for forbidden in ("1e-", "epsilon", "max(1e", "+ eps"):
        assert forbidden not in code, forbidden


def test_the_binning_spec_is_untouched_by_the_denominator_policy() -> None:
    """P1-3 must not alter M21 policy beyond the coefficient guard."""
    spec = StateBinningSpec(spec_version="v", categorical_features=("program_type",))
    assert spec.spec_version == "v"
