"""Collection policy, live accounting and resume safety.

The failures targeted here are the ones that would silently produce a telemetry
file that looks complete: a family never executed, a call billed twice, a
resume that merges two different systems into one set of bins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from cover_kbc.controller_calibration.checkpoint import (
    CollectionCheckpoint,
    ResumeRefused,
    RunIdentity,
    resume_from,
)
from cover_kbc.controller_calibration.collection_policy import (
    COLLECTION_POLICY_VERSION,
    CollectionPolicyError,
    CoverageLedger,
    FamilyStatus,
    TrainCollectionPolicy,
    family_of,
    required_families,
)
from cover_kbc.controller_calibration.supplemental_coverage import (
    SupplementalCoverageError,
    committed_action_effects,
    load_chained_coverage,
    merge_collections,
    merge_coverage_ledgers,
    plan_supplemental_coverage,
    plan_supplemental_coverage_from_base,
    resolve_collection_run_dir,
    supplemental_base_identity,
    validate_no_duplicate_action_effect_ids,
)
from cover_kbc.controller_calibration.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    ActionOutcome,
    ActionTelemetryRecord,
    ControlStateFeatures,
    RedundancyStatus,
)
from cover_kbc.controller_calibration.progress import (
    MIN_ROWS_FOR_ETA,
    RunCounters,
    format_duration,
    query_line,
    round_line,
    summary_block,
)
from cover_kbc.types import Query

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FakeAction:
    action_id: str
    action_family: str


def _catalogue(*pairs: tuple[str, str]) -> tuple[FakeAction, ...]:
    return tuple(FakeAction(a, f) for a, f in pairs)


# --------------------------------------------------------------------------
# collection policy
# --------------------------------------------------------------------------

def test_takes_from_every_legal_family_before_repeating_one() -> None:
    """A family left unexecuted has no support and cannot be calibrated."""
    policy = TrainCollectionPolicy(per_family_limit=1)
    chosen = policy.select(_catalogue(
        ("a1", "REVERSE"), ("a2", "REVERSE"), ("a3", "REVERSE"),
        ("b1", "COUNTERFACTUAL"), ("c1", "KEY_CONDITION"),
    ))
    assert {family_of(a) for a in chosen} == {
        "REVERSE", "COUNTERFACTUAL", "KEY_CONDITION"}
    assert len(chosen) == 3


def test_selection_is_deterministic() -> None:
    catalogue = _catalogue(("z", "REVERSE"), ("a", "REVERSE"), ("m", "REVERSE"))
    first = TrainCollectionPolicy(per_family_limit=2).select(catalogue)
    second = TrainCollectionPolicy(per_family_limit=2).select(catalogue)
    assert [a.action_id for a in first] == [a.action_id for a in second]


def test_selection_is_bounded_per_family() -> None:
    policy = TrainCollectionPolicy(per_family_limit=2)
    chosen = policy.select(_catalogue(*[(f"a{i}", "REVERSE") for i in range(9)]))
    assert len(chosen) == 2


def test_policy_never_invents_an_action() -> None:
    """The catalogue is the eligibility authority; selection is a subset of it."""
    catalogue = _catalogue(("a1", "REVERSE"))
    chosen = TrainCollectionPolicy().select(catalogue)
    assert set(chosen).issubset(set(catalogue))


def test_empty_catalogue_selects_nothing() -> None:
    assert TrainCollectionPolicy().select(()) == ()


def test_unselected_legal_actions_still_count_as_opportunities() -> None:
    policy = TrainCollectionPolicy(per_family_limit=1)
    policy.select(_catalogue(("a1", "REVERSE"), ("a2", "REVERSE")))
    assert policy.coverage.families["REVERSE"].legal_opportunities == 2


def test_a_legal_family_never_executed_fails_integrity() -> None:
    policy = TrainCollectionPolicy(per_family_limit=1)
    catalogue = _catalogue(("a1", "REVERSE"), ("b1", "COUNTERFACTUAL"))
    chosen = policy.select(catalogue)
    policy.record_outcome(chosen[0], succeeded=True)  # only one of two executed
    assert not policy.coverage.integrity_ok()
    assert policy.coverage.unobserved_families


def test_family_absent_from_train_is_distinct_from_a_coverage_failure() -> None:
    """Zero legal instances is a fact about TRAIN, not an implementation bug.

    "Surfaced by a catalogue and never legal" is the dataset fact. "Never
    surfaced at all" is a wiring failure and is tested separately below.
    """
    policy = TrainCollectionPolicy()
    policy.note_families(["CANDIDATE_FREE_RECALL"])
    policy.coverage.note_surfaced("CANDIDATE_FREE_RECALL")
    assert "CANDIDATE_FREE_RECALL" in policy.coverage.families_absent_from_train
    assert policy.coverage.integrity_ok()


def test_a_required_family_never_offered_fails_integrity() -> None:
    """Audit 0041 F-10: this used to read as PASS because the family was
    simply absent from the ledger."""
    policy = TrainCollectionPolicy()
    policy.note_families(["REVERSE_CHECK", "SPECIALIST_VERIFY"])
    policy.select(_catalogue(("a1", "SPECIALIST_VERIFY")))
    policy.coverage.note_executed("SPECIALIST_VERIFY", succeeded=True)

    assert policy.coverage.never_surfaced_families == ("REVERSE_CHECK",)
    assert policy.coverage.integrity_ok()
    assert policy.coverage.families["REVERSE_CHECK"].status is (
        FamilyStatus.NEVER_LEGAL)


def test_target_coverage_states_are_distinguished() -> None:
    policy = TrainCollectionPolicy()
    policy.note_families(["REVERSE_CHECK"])                      # never surfaced
    policy.coverage.note_surfaced("COUNTERFACTUAL_VERIFY")        # absent from TRAIN
    policy.coverage.note_legal("CANDIDATE_FREE_RECALL")           # legal, unexecuted
    policy.coverage.note_legal("SPECIALIST_VERIFY")
    policy.coverage.note_executed("SPECIALIST_VERIFY", succeeded=True)   # observed

    states = {f: c.status for f, c in policy.coverage.families.items()}
    assert states == {
        "REVERSE_CHECK": FamilyStatus.NEVER_LEGAL,
        "COUNTERFACTUAL_VERIFY": FamilyStatus.NEVER_LEGAL,
        "CANDIDATE_FREE_RECALL": FamilyStatus.LEGAL_BUT_UNDERCOVERED,
        "SPECIALIST_VERIFY":
            FamilyStatus.OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES,
    }


def test_common_family_needs_configured_success_target() -> None:
    policy = TrainCollectionPolicy(family_target=3)
    for _ in range(5):
        policy.coverage.note_legal("INDEPENDENT_RECALL")
    policy.coverage.note_executed("INDEPENDENT_RECALL", succeeded=True)
    policy.coverage.note_executed("INDEPENDENT_RECALL", succeeded=True)

    entry = policy.coverage.families["INDEPENDENT_RECALL"]
    assert entry.target == 3
    assert entry.coverage_deficit == 1
    assert entry.status is FamilyStatus.LEGAL_BUT_UNDERCOVERED


def test_rare_family_is_limited_only_after_every_opportunity_succeeds() -> None:
    policy = TrainCollectionPolicy(family_target=10)
    for _ in range(2):
        policy.coverage.note_legal("LISTING_ELIMINATION")
        policy.coverage.note_executed("LISTING_ELIMINATION", succeeded=True)

    entry = policy.coverage.families["LISTING_ELIMINATION"]
    assert entry.target == 2
    assert entry.status is (
        FamilyStatus.OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES)


def test_coverage_deficit_beats_fixed_family_order() -> None:
    """A later family with no support must not keep losing to a prefix family."""
    policy = TrainCollectionPolicy(family_target=10)
    previous = _catalogue(
        ("a1", "ALTERNATIVE_RECALL"),
        ("d1", "DEFINITION_RECALL"),
        ("i1", "INDEPENDENT_RECALL"),
    )
    policy.begin_query()
    first = policy.select(previous)[0]
    policy.record_outcome(first, succeeded=True)
    second = policy.select(previous)[0]
    policy.record_outcome(second, succeeded=True)
    third = policy.select(previous)[0]

    assert family_of(first) == "ALTERNATIVE_RECALL"
    assert family_of(second) == "DEFINITION_RECALL"
    assert family_of(third) == "INDEPENDENT_RECALL"


def test_selector_counts_legal_but_only_returns_selectable_actions() -> None:
    policy = TrainCollectionPolicy()
    legal = _catalogue(
        ("l1", "LISTING_ELIMINATION"),
        ("s1", "SEMANTIC_VERIFY"),
    )

    chosen = policy.select(legal, selectable=(legal[1],))

    assert chosen == (legal[1],)
    assert policy.coverage.families["LISTING_ELIMINATION"].legal_opportunities == 1
    assert policy.coverage.families["SEMANTIC_VERIFY"].selectable_opportunities == 1
    assert policy.coverage.families["LISTING_ELIMINATION"].selectable_opportunities == 0
    assert policy.coverage.families["LISTING_ELIMINATION"].executed == 0


def test_the_family_vocabulary_comes_from_layer_6_not_a_string_list() -> None:
    from cover_kbc.control.planner_types import ActionFamily

    families = required_families(("m17", "m18"))
    assert set(families) <= {member.value for member in ActionFamily}
    assert "SPECIALIST_VERIFY" in families and "REVERSE_CHECK" in families


def test_the_round_robin_position_survives_the_controller_re_asking() -> None:
    """One action executes per round, so a restarting rotation starves a family."""
    policy = TrainCollectionPolicy()
    catalogue = _catalogue(
        ("c1", "COUNTERFACTUAL_VERIFY"), ("c2", "COUNTERFACTUAL_VERIFY"),
        ("c3", "COUNTERFACTUAL_VERIFY"), ("r1", "REVERSE_CHECK"),
    )
    policy.begin_query()
    heads = []
    remaining = list(catalogue)
    for _ in range(2):
        chosen = policy.select(tuple(remaining))
        heads.append(family_of(chosen[0]))
        remaining = [a for a in remaining if a is not chosen[0]]
    assert heads == ["COUNTERFACTUAL_VERIFY", "REVERSE_CHECK"]


def test_begin_query_resets_only_the_rotation_not_the_coverage() -> None:
    policy = TrainCollectionPolicy()
    policy.select(_catalogue(("a1", "REVERSE_CHECK")))
    policy.begin_query()
    assert policy.coverage.families["REVERSE_CHECK"].legal_opportunities == 1


def test_outcomes_split_success_and_failure() -> None:
    policy = TrainCollectionPolicy()
    chosen = policy.select(_catalogue(("a1", "REVERSE"), ("a2", "REVERSE")))
    policy.record_outcome(chosen[0], succeeded=True)
    policy.record_outcome(chosen[1], succeeded=False)
    entry = policy.coverage.families["REVERSE"]
    assert (entry.executed, entry.succeeded, entry.failed) == (2, 1, 1)
    assert "REVERSE" in policy.coverage.table()


def test_zero_family_limit_is_refused() -> None:
    with pytest.raises(CollectionPolicyError, match="at least 1"):
        TrainCollectionPolicy(per_family_limit=0)


def test_family_of_reads_enum_values() -> None:
    @dataclass(frozen=True)
    class Enumish:
        value: str

    @dataclass(frozen=True)
    class Check:
        check_kind: Enumish

    assert family_of(Check(Enumish("COUNTERFACTUAL"))) == "COUNTERFACTUAL"


def test_supplemental_coverage_plan_uses_train_relations_without_gold() -> None:
    policy = TrainCollectionPolicy(family_target=3)
    for _ in range(5):
        policy.coverage.note_legal(
            "SEMANTIC_VERIFY", relation="companyTradesAtStockExchange")

    plan = plan_supplemental_coverage(
        policy.coverage,
        (
            Query("A", "hasArea", 0),
            Query("B", "companyTradesAtStockExchange", 1),
            Query("C", "companyTradesAtStockExchange", 2),
        ),
    )

    family = plan.families[0]
    assert family.action_family == "SEMANTIC_VERIFY"
    assert family.deficit == 3
    assert family.row_indices == (1, 2)
    assert "companyTradesAtStockExchange" in family.candidate_relations


def test_supplemental_coverage_merge_is_offline_and_guarded() -> None:
    base = TrainCollectionPolicy(family_target=2).coverage
    base.note_legal("SET_EXPANSION", relation="awardWonBy")
    supplement = TrainCollectionPolicy(family_target=2).coverage
    supplement.note_legal("SET_EXPANSION", relation="awardWonBy")
    supplement.note_executed("SET_EXPANSION", succeeded=True, relation="awardWonBy")

    merged = merge_coverage_ledgers(base, supplement)

    assert base.families["SET_EXPANSION"].executed == 0
    assert merged.families["SET_EXPANSION"].legal_opportunities == 1
    assert merged.families["SET_EXPANSION"].succeeded == 1
    with pytest.raises(SupplementalCoverageError, match="duplicate"):
        validate_no_duplicate_action_effect_ids(
            ({"action": {"row_index": 1, "action_id": "a"}},),
            ({"action": {"row_index": 1, "action_id": "a"}},),
        )


def test_supplemental_planner_prefers_persisted_base_evidence(tmp_path) -> None:
    run = tmp_path / "base"
    run.mkdir()
    coverage = TrainCollectionPolicy(family_target=2).coverage
    for family, relation in (
        ("SET_EXPANSION", "awardWonBy"),
        ("UNARY_VERIFY", "awardWonBy"),
        ("LISTING_ELIMINATION", "companyTradesAtStockExchange"),
        ("SEMANTIC_VERIFY", "companyTradesAtStockExchange"),
    ):
        coverage.note_legal(family, relation=relation)
    (run / "v3_action_coverage.json").write_text(
        json.dumps(coverage.to_json()), encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    for name in (
        "predictions.jsonl",
        "train_telemetry.jsonl",
        "inference_telemetry.jsonl",
        "v3_final_hypothesis_graphs.jsonl",
        "v3_action_effects.jsonl",
    ):
        (run / name).write_text("", encoding="utf-8")
    (run / "v3_action_coverage.csv").write_text("", encoding="utf-8")
    hgraphs = [
        {
            "row_index": 10,
            "SubjectEntity": "Prize",
            "Relation": "awardWonBy",
            "failure_state": "SET_GROWING",
            "legal_action_families": ["SET_EXPANSION", "UNARY_VERIFY"],
            "hypotheses": [{
                "status": "CHALLENGED",
                "hypothesis_id": "h1",
                "independent_support_count": 1,
                "raw_support_count": 1,
            }],
        },
        {
            "row_index": 20,
            "SubjectEntity": "Company",
            "Relation": "companyTradesAtStockExchange",
            "failure_state": "HIGH_FP_RISK",
            "legal_action_families": ["LISTING_ELIMINATION", "SEMANTIC_VERIFY"],
            "hypotheses": [{
                "status": "CHALLENGED",
                "hypothesis_id": "h2",
                "independent_support_count": 1,
                "raw_support_count": 1,
            }],
        },
    ]
    (run / "v3_pre_m8_hypothesis_graphs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in hgraphs),
        encoding="utf-8",
    )

    plan = plan_supplemental_coverage_from_base(
        run,
        (
            Query("Prize", "awardWonBy", 10),
            Query("Company", "companyTradesAtStockExchange", 20),
        ),
    )

    by_family = {family.action_family: family.row_indices for family in plan.families}
    assert by_family == {
        "LISTING_ELIMINATION": (20,),
        "SEMANTIC_VERIFY": (20,),
        "SET_EXPANSION": (10,),
        "UNARY_VERIFY": (10,),
    }


def _coverage_with_stock_award_deficits() -> CoverageLedger:
    coverage = TrainCollectionPolicy(family_target=10).coverage
    for _ in range(10):
        coverage.note_legal("SET_EXPANSION", relation="awardWonBy")
        coverage.note_legal("UNARY_VERIFY", relation="awardWonBy")
    for _ in range(6):
        coverage.note_legal(
            "LISTING_ELIMINATION",
            relation="companyTradesAtStockExchange",
        )
        coverage.note_legal(
            "SEMANTIC_VERIFY",
            relation="companyTradesAtStockExchange",
        )
    return coverage


def _stock_award_hgraphs() -> list[dict]:
    rows = []
    for row in range(200, 210):
        rows.append({
            "row_index": row,
            "SubjectEntity": f"Prize {row}",
            "Relation": "awardWonBy",
            "failure_state": "SET_GROWING",
            "legal_action_families": ["SET_EXPANSION", "UNARY_VERIFY"],
            "hypotheses": [{
                "status": "CHALLENGED",
                "hypothesis_id": f"ha{row}",
                "independent_support_count": 1,
                "raw_support_count": 1,
            }],
        })
    for row in (247, 262, 266, 271, 276, 305):
        rows.append({
            "row_index": row,
            "SubjectEntity": f"Company {row}",
            "Relation": "companyTradesAtStockExchange",
            "failure_state": "HIGH_FP_RISK",
            "legal_action_families": [
                "LISTING_ELIMINATION",
                "SEMANTIC_VERIFY",
            ],
            "hypotheses": [{
                "status": "CHALLENGED",
                "hypothesis_id": f"hs{row}",
                "independent_support_count": 1,
                "raw_support_count": 1,
            }],
        })
    return rows


def _write_stock_award_hgraphs(run) -> None:
    (run / "v3_pre_m8_hypothesis_graphs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in _stock_award_hgraphs()),
        encoding="utf-8",
    )


def test_continuation_planner_uses_prior_supplement_deficits(tmp_path) -> None:
    base = tmp_path / "base"
    base_coverage = _coverage_with_stock_award_deficits()
    _minimal_run_dir(base, coverage=base_coverage)
    _write_stock_award_hgraphs(base)

    prior_coverage = CoverageLedger.from_json(base_coverage.to_json())
    for _ in range(11):
        prior_coverage.note_selectable("SET_EXPANSION", relation="awardWonBy")
        prior_coverage.note_executed(
            "SET_EXPANSION", succeeded=True, relation="awardWonBy")
    for _ in range(4):
        prior_coverage.note_selectable(
            "SEMANTIC_VERIFY",
            relation="companyTradesAtStockExchange",
        )
        prior_coverage.note_executed(
            "SEMANTIC_VERIFY",
            succeeded=True,
            relation="companyTradesAtStockExchange",
        )
    for _ in range(3):
        prior_coverage.note_selectable("UNARY_VERIFY", relation="awardWonBy")
        prior_coverage.note_executed(
            "UNARY_VERIFY", succeeded=True, relation="awardWonBy")

    s0 = ControlStateFeatures(residual=0.9, entropy=0.8, calls_used=12)
    s1 = ControlStateFeatures(residual=0.7, entropy=0.5, calls_used=13)
    telemetry = [
        *(
            _telemetry_record(
                family="SET_EXPANSION", relation="awardWonBy",
                round_index=index + 1, pre=s0, post=s1,
                row_index=row, subject=f"Prize {row}")
            for index, row in enumerate(
                (200, 200, 201, 201, 202, 202, 203, 204, 204, 205, 205)
            )
        ),
        _telemetry_record(
            family="UNARY_VERIFY", relation="awardWonBy", round_index=1,
            pre=s0, post=s1, row_index=203, subject="Prize 203"),
        _telemetry_record(
            family="UNARY_VERIFY", relation="awardWonBy", round_index=1,
            pre=s0, post=s1, row_index=209, subject="Prize 209"),
        _telemetry_record(
            family="UNARY_VERIFY", relation="awardWonBy", round_index=2,
            pre=s0, post=s1, row_index=209, subject="Prize 209"),
        *(
            _telemetry_record(
                family="SEMANTIC_VERIFY",
                relation="companyTradesAtStockExchange",
                round_index=index + 1,
                pre=s0,
                post=s1,
                row_index=row,
                subject=f"Company {row}",
            )
            for index, row in enumerate((247, 247, 271, 271))
        ),
    ]
    effects = [
        {
            "action_effect_id": f"supplement:{index}",
            "action": {
                "row_index": record.row_index,
                "action_id": record.action_id,
            },
        }
        for index, record in enumerate(telemetry)
    ]
    effects.append({
        "action_effect_id": "supplement:uncommitted",
        "action": {"row_index": 206, "action_id": "v3act-unary_verify"},
    })
    prior = tmp_path / "prior"
    _minimal_run_dir(
        prior,
        coverage=prior_coverage,
        telemetry=telemetry,
        effects=effects,
        manifest={
            "unresolved_failed_rows": [206, 207, 208, 262, 266, 276, 305],
            "failure_history": [
                {
                    "row_index": row,
                    "relation": (
                        "awardWonBy" if row < 247
                        else "companyTradesAtStockExchange"
                    ),
                    "subject": f"row {row}",
                    "error": "ValueError: duplicate evidence edge",
                    "resolved": False,
                }
                for row in (206, 207, 208, 262, 266, 276, 305)
            ],
            "supplemental": {
                "base_collection_identity": supplemental_base_identity(base),
                "base_inclusive_coverage": True,
            },
        },
    )

    committed, ignored = committed_action_effects(prior)
    assert len(committed) == len(telemetry)
    assert len(ignored) == 1

    coverage = load_chained_coverage(base, (prior,))
    assert {
        family: coverage.families[family].coverage_deficit
        for family in coverage.unobserved_families
    } == {
        "LISTING_ELIMINATION": 6,
        "SEMANTIC_VERIFY": 2,
        "UNARY_VERIFY": 7,
    }
    plan = plan_supplemental_coverage_from_base(
        base,
        tuple(
            Query(f"Prize {row}", "awardWonBy", row)
            for row in range(200, 210)
        ) + tuple(
            Query(f"Company {row}", "companyTradesAtStockExchange", row)
            for row in (247, 262, 266, 271, 276, 305)
        ),
        coverage=coverage,
        prior_supplement_dirs=(prior,),
    )

    by_family = {family.action_family: family.row_indices for family in plan.families}
    assert by_family == {
        "LISTING_ELIMINATION": (262, 266, 276, 305, 247, 271),
        "SEMANTIC_VERIFY": (262, 266),
        "UNARY_VERIFY": (206, 207, 208, 200, 201, 202, 204),
    }
    assert plan.row_indices == (
        200, 201, 202, 204, 206, 207, 208, 247, 262, 266, 271, 276, 305
    )


def test_real_partial_supplement_continuation_plan_matches_artifacts() -> None:
    base = ROOT / "outputs/v3_train_collect_v2_coverage"
    partial = (
        ROOT / "outputs/v3_supplement_16ebbbf6_partial"
        / "v3_supplement_16ebbbf6_20260810T070529Z"
        / "supplement"
        / "cover_kbc_v3_train_collection_train-supplement_20260810T071043Z"
    )
    if not base.exists() or not partial.exists():
        pytest.skip("downloaded real V3 supplement artifacts are not present")

    partial_run = resolve_collection_run_dir(partial)
    coverage = load_chained_coverage(base, (partial_run,))
    deficits = {
        family: coverage.families[family].coverage_deficit
        for family in coverage.unobserved_families
    }
    assert deficits == {
        "LISTING_ELIMINATION": 6,
        "SEMANTIC_VERIFY": 2,
        "UNARY_VERIFY": 7,
    }
    assert coverage.families["SET_EXPANSION"].coverage_deficit == 0

    committed, ignored = committed_action_effects(partial_run)
    assert len(committed) == 18
    assert len(ignored) == 7

    plan = plan_supplemental_coverage_from_base(
        base,
        tuple(
            Query(f"Prize {row}", "awardWonBy", row)
            for row in range(200, 210)
        ) + tuple(
            Query(f"Company {row}", "companyTradesAtStockExchange", row)
            for row in (247, 262, 266, 271, 276, 305)
        ),
        coverage=coverage,
        prior_supplement_dirs=(partial_run,),
    )
    assert plan.row_indices == (
        200, 201, 202, 204, 206, 207, 208, 247, 262, 266, 271, 276, 305
    )
    assert plan.families_for_row(200) == ("UNARY_VERIFY",)
    assert plan.families_for_row(247) == ("LISTING_ELIMINATION",)
    assert plan.families_for_row(262) == (
        "LISTING_ELIMINATION", "SEMANTIC_VERIFY")
    assert plan.families_for_row(276) == ("LISTING_ELIMINATION",)


def _write_test_jsonl(path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _minimal_run_dir(path, *, coverage, telemetry=(), effects=(), manifest=None):
    path.mkdir()
    for name in (
        "predictions.jsonl",
        "inference_telemetry.jsonl",
        "v3_pre_m8_hypothesis_graphs.jsonl",
        "v3_final_hypothesis_graphs.jsonl",
    ):
        (path / name).write_text("", encoding="utf-8")
    _write_test_jsonl(path / "train_telemetry.jsonl", [
        record.to_json() for record in telemetry
    ])
    _write_test_jsonl(path / "v3_action_effects.jsonl", list(effects))
    (path / "v3_action_coverage.json").write_text(
        json.dumps(coverage.to_json()), encoding="utf-8")
    (path / "v3_action_coverage.csv").write_text("", encoding="utf-8")
    payload = {
        "identity": {
            "train_sha256": "train-sha",
            "repo_sha": "repo-sha",
        },
        "status": "complete",
    }
    if manifest:
        payload.update(manifest)
    (path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def _telemetry_record(
    *,
    family: str,
    relation: str,
    round_index: int,
    pre: ControlStateFeatures,
    post: ControlStateFeatures,
    row_index: int = 10,
    subject: str = "Prize",
) -> ActionTelemetryRecord:
    verifier = family.endswith("VERIFY") or family == "LISTING_ELIMINATION"
    return ActionTelemetryRecord(
        schema_version=TELEMETRY_SCHEMA_VERSION,
        run_id="supplement",
        row_index=row_index,
        subject=subject,
        relation=relation,
        program_type="LARGE_OPEN_SET",
        round_index=round_index,
        operation_id=f"{row_index}:{round_index}:{family}",
        action_family=family,
        target_class=family,
        action_id=f"v3act-{family.lower()}",
        model_role="verifier" if verifier else "enumerator",
        spend_class="VERIFICATION" if verifier else "DISCOVERY",
        selected=True,
        executed=True,
        pre_state=pre,
        post_state=post,
        outcome=ActionOutcome(
            physical_calls=1,
            enumerator_calls=0 if verifier else 1,
            verifier_calls=1 if verifier else 0,
            prompt_tokens=10,
            generated_tokens=0 if verifier else 5,
            candidates_named=("Bob",),
            candidates_touched=("bob",),
            candidate_effect_measured=True,
            redundancy=0.5,
            redundancy_status=RedundancyStatus.MEASURED,
            verifier_outcome="VALID" if verifier else "",
            structural_outcome="" if verifier else "ALTERNATIVE",
        ),
    )


def test_supplemental_merge_end_to_end_is_deterministic(tmp_path) -> None:
    base_coverage = TrainCollectionPolicy(family_target=2).coverage
    for family in ("SET_EXPANSION", "UNARY_VERIFY"):
        base_coverage.note_legal(family, relation="awardWonBy")
    base = tmp_path / "base"
    _minimal_run_dir(base, coverage=base_coverage)

    s0 = ControlStateFeatures(residual=0.9, entropy=0.8, calls_used=12)
    s1 = ControlStateFeatures(residual=0.7, entropy=0.5, calls_used=13)
    s2 = ControlStateFeatures(residual=0.4, entropy=0.2, calls_used=14)
    supplement_coverage = CoverageLedger.from_json(base_coverage.to_json())
    for family in ("SET_EXPANSION", "UNARY_VERIFY"):
        supplement_coverage.note_selectable(family, relation="awardWonBy")
        supplement_coverage.note_executed(
            family, succeeded=True, relation="awardWonBy")
    supplement = tmp_path / "supplement"
    base_id = supplemental_base_identity(base)
    _minimal_run_dir(
        supplement,
        coverage=supplement_coverage,
        telemetry=(
            set_record := _telemetry_record(
                family="SET_EXPANSION", relation="awardWonBy",
                round_index=1, pre=s0, post=s1),
            unary_record := _telemetry_record(
                family="UNARY_VERIFY", relation="awardWonBy",
                round_index=2, pre=s1, post=s2),
        ),
        effects=(
            {
                "action_effect_id": "supplement:1",
                "action": {
                    "row_index": set_record.row_index,
                    "action_id": set_record.action_id,
                },
            },
            {
                "action_effect_id": "supplement:2",
                "action": {
                    "row_index": unary_record.row_index,
                    "action_id": unary_record.action_id,
                },
            },
        ),
        manifest={
            "supplemental": {
                "base_collection_identity": base_id,
                "base_inclusive_coverage": True,
            }
        },
    )

    first = tmp_path / "merged-a"
    second = tmp_path / "merged-b"
    first_manifest = merge_collections(
        base_dir=base, supplement_dir=supplement, output_dir=first)
    second_manifest = merge_collections(
        base_dir=base, supplement_dir=supplement, output_dir=second)

    assert first_manifest["sufficiency"]["ok"] is True
    assert first_manifest["coverage"]["integrity_ok"] is True
    assert first_manifest["calibration_derivation_blocked"] is False
    assert (
        first_manifest["merged_corpus_sha256"]
        == second_manifest["merged_corpus_sha256"]
    )
    assert (
        first / "SHA256SUMS.txt"
    ).read_text(encoding="utf-8") == (
        second / "SHA256SUMS.txt"
    ).read_text(encoding="utf-8")


def test_chained_supplemental_merge_filters_uncommitted_effect_tails(tmp_path) -> None:
    base_coverage = TrainCollectionPolicy(family_target=2).coverage
    for _ in range(2):
        base_coverage.note_legal("UNARY_VERIFY", relation="awardWonBy")
    base = tmp_path / "base"
    _minimal_run_dir(base, coverage=base_coverage)
    base_id = supplemental_base_identity(base)

    s0 = ControlStateFeatures(residual=0.9, entropy=0.8, calls_used=12)
    s1 = ControlStateFeatures(residual=0.7, entropy=0.5, calls_used=13)
    first_coverage = CoverageLedger.from_json(base_coverage.to_json())
    first_coverage.note_selectable("UNARY_VERIFY", relation="awardWonBy")
    first_coverage.note_executed(
        "UNARY_VERIFY", succeeded=True, relation="awardWonBy")
    first_record = _telemetry_record(
        family="UNARY_VERIFY", relation="awardWonBy",
        round_index=1, pre=s0, post=s1, row_index=20, subject="Prize 20")
    first = tmp_path / "supplement-1"
    _minimal_run_dir(
        first,
        coverage=first_coverage,
        telemetry=(first_record,),
        effects=({
            "action_effect_id": "supplement:first",
            "action": {
                "row_index": first_record.row_index,
                "action_id": first_record.action_id,
            },
        },),
        manifest={
            "supplemental": {
                "base_collection_identity": base_id,
                "base_inclusive_coverage": True,
            }
        },
    )

    second_coverage = CoverageLedger.from_json(first_coverage.to_json())
    second_coverage.note_selectable("UNARY_VERIFY", relation="awardWonBy")
    second_coverage.note_executed(
        "UNARY_VERIFY", succeeded=True, relation="awardWonBy")
    second_record = _telemetry_record(
        family="UNARY_VERIFY", relation="awardWonBy",
        round_index=1, pre=s0, post=s1, row_index=21, subject="Prize 21")
    second = tmp_path / "supplement-2"
    _minimal_run_dir(
        second,
        coverage=second_coverage,
        telemetry=(second_record,),
        effects=(
            {
                "action_effect_id": "supplement:second",
                "action": {
                    "row_index": second_record.row_index,
                    "action_id": second_record.action_id,
                },
            },
            {
                "action_effect_id": "supplement:uncommitted",
                "action": {"row_index": 22, "action_id": second_record.action_id},
            },
        ),
        manifest={
            "supplemental": {
                "base_collection_identity": base_id,
                "base_inclusive_coverage": True,
            }
        },
    )

    out = tmp_path / "merged-chain"
    manifest = merge_collections(
        base_dir=base,
        supplement_dirs=(first, second),
        output_dir=out,
    )
    assert manifest["coverage"]["integrity_ok"] is True
    assert manifest["coverage"]["families"][0]["successful"] == 2
    assert manifest["ignored_uncommitted_action_effects"][str(second)] == 1
    assert len([
        line for line in (out / "action_effects.jsonl").read_text(
            encoding="utf-8").splitlines()
        if line.strip()
    ]) == 2


# --------------------------------------------------------------------------
# live accounting
# --------------------------------------------------------------------------

def test_each_call_is_charged_to_exactly_one_role() -> None:
    counters = RunCounters(total_rows=477)
    counters.charge(role="enumerate", calls=3, prompt_tokens=10, generated_tokens=4)
    counters.charge(role="verify", calls=8, prompt_tokens=20)
    assert counters.physical_model_calls == 11
    assert counters.enumerator_calls + counters.verifier_calls == 11
    assert counters.prompt_tokens == 30 and counters.generated_tokens == 4


def test_an_unknown_role_cannot_absorb_calls() -> None:
    counters = RunCounters(total_rows=477)
    with pytest.raises(ValueError, match="exactly one role"):
        counters.charge(role="decide", calls=1)


def test_negative_accounting_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        RunCounters(total_rows=477).charge(role="verify", calls=-1)


def test_eta_stays_blank_until_enough_rows() -> None:
    """A confident wrong ETA is worse than an honest blank."""
    counters = RunCounters(total_rows=477)
    counters.rows_completed = MIN_ROWS_FOR_ETA - 1
    assert counters.eta_seconds is None
    assert format_duration(counters.eta_seconds) == "--"
    counters.rows_completed = MIN_ROWS_FOR_ETA
    assert counters.eta_seconds is not None


def test_percent_reaches_exactly_full() -> None:
    counters = RunCounters(total_rows=477, rows_completed=477)
    assert counters.percent == pytest.approx(100.0)


def test_summary_reports_every_required_field() -> None:
    block = summary_block(RunCounters(total_rows=477, rows_completed=120))
    for field in ("failed", "physical calls", "enumerator calls", "verifier calls",
                  "prompt tokens", "generated tokens", "elapsed", "ETA"):
        assert field in block
    assert "120 / 477" in block


def test_query_line_shows_current_over_total() -> None:
    line = query_line(21, 477, relation="hasArea", subject="Lošinj")
    assert "[TRAIN 21/477]" in line and "hasArea" in line


def test_round_line_has_no_fabricated_denominator() -> None:
    """Adaptive rounds have no known total; inventing one is a fake progress bar."""
    line = round_line(21, 477, round_index=3, detail="M18 COUNTERFACTUAL")
    assert "[round=3]" in line
    assert "round=3/" not in line


def test_restored_counters_do_not_inherit_a_stale_clock() -> None:
    original = RunCounters(total_rows=477, rows_completed=100, verifier_calls=40)
    original.started_at -= 10_000
    restored = RunCounters.restore(original.to_json(), total_rows=477)
    assert restored.rows_completed == 100 and restored.verifier_calls == 40
    assert restored.elapsed_seconds < 100


# --------------------------------------------------------------------------
# checkpoint and resume
# --------------------------------------------------------------------------

def _identity(**overrides) -> RunIdentity:
    base = dict(
        train_sha256="abc", repo_sha="def", config_sha256="ghi",
        enumerator_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        enumerator_revision="95a6d26c",
        verifier_model_id="Qwen/Qwen3.5-4B", verifier_revision="851bf6e8",
        collection_policy_version=COLLECTION_POLICY_VERSION,
        telemetry_schema_version="train-telemetry-v1", total_rows=477,
    )
    base.update(overrides)
    return RunIdentity(**base)


def test_resume_skips_completed_rows(tmp_path) -> None:
    identity = _identity()
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(identity, [0, 1, 2], [], {"rows_completed": 3}).save(path)
    restored = resume_from(path, identity)
    assert restored.completed_rows == [0, 1, 2]


@pytest.mark.parametrize("field,value", [
    ("train_sha256", "other"),
    ("repo_sha", "other"),
    ("config_sha256", "other"),
    ("enumerator_revision", "other"),
    ("verifier_revision", "other"),
    ("collection_policy_version", "collect-v0"),
    ("telemetry_schema_version", "train-telemetry-v0"),
    ("total_rows", 478),
])
def test_resume_refuses_any_identity_drift(tmp_path, field, value) -> None:
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(_identity(), [0], [], {}).save(path)
    with pytest.raises(ResumeRefused, match="different run"):
        resume_from(path, _identity(**{field: value}))


def test_resume_refuses_a_missing_checkpoint(tmp_path) -> None:
    with pytest.raises(ResumeRefused, match="no checkpoint"):
        resume_from(tmp_path / "absent.json", _identity())


def test_resume_refuses_a_corrupt_checkpoint(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ResumeRefused, match="unreadable checkpoint"):
        resume_from(path, _identity())


def test_checkpoint_write_is_atomic(tmp_path) -> None:
    """A torn checkpoint is worse than none, so no partial file may survive."""
    path = tmp_path / "checkpoint.json"
    CollectionCheckpoint(_identity(), [0, 1], [], {}).save(path)
    assert path.is_file()
    assert not list(tmp_path.glob("*.partial"))


def test_checkpoint_round_trips_counters(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    counters = RunCounters(total_rows=477, rows_completed=12, verifier_calls=96)
    CollectionCheckpoint(_identity(), [0], [], counters.to_json()).save(path)
    restored = resume_from(path, _identity())
    assert restored.counters["verifier_calls"] == 96
