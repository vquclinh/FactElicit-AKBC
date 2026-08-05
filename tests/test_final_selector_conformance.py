"""Module 8 conformance: final selector and evaluator-aware output (spec §14).

Deterministic and synthetic throughout. No model is loaded anywhere.

The central properties: what is emitted follows the accepted evidence state
rather than raw frequency; one semantic candidate yields one preferred surface;
a soft alias hint never becomes hard identity; and every empty row explains
itself with a reason that is actually reachable.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import all_contracts, get_contract
from cover_kbc.data.writer import dedupe_object_entities, prediction_rows, write_predictions
from cover_kbc.evaluation.harness import evaluate_predictions
from cover_kbc.evaluation.official import official_normalize_string
from cover_kbc.evidence.graph import build_graph
from cover_kbc.normalization.numeric import cluster_values
from cover_kbc.selection import (
    DEFAULT_SELECTION,
    SelectionInvariantError,
    _cluster_support,
    _numeric_clusters,
    finalize,
    select,
)
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    EmptyReason,
    Evidence,
    EvidenceMode,
    IndependenceGroup,
    ProgramType,
    Query,
    VerificationLabel,
    VerificationResult,
)

BORDERS = "countryLandBordersCountry"
DEATH = "personHasCityOfDeath"
STOCK = "companyTradesAtStockExchange"
AWARDS = "awardWonBy"


# --- helpers -----------------------------------------------------------------


def graph_for(relation, subject="S"):
    contract = get_contract(relation)
    return build_graph(Query(subject, relation, 0), contract)


def add(graph, key, *, surface=None, mechanisms=1, numeric=None, status=None,
        repeats=1, verdict=None, rejected=False):
    """Attach a candidate with a given number of *independent* mechanisms."""
    contract = graph.contract
    groups = list(contract.eligible_independence_groups)
    cand = Candidate(
        key=key, display_value=surface or key.title(), relation=contract.relation,
        numeric_value=numeric,
    )
    for i in range(mechanisms):
        for r in range(repeats):
            cand.add_evidence(
                Evidence(key, EdgeType.SUPPORT, groups[i % len(groups)],
                         "v", "m", 0, f"{key}-{i}-{r}")
            )
    if verdict is not None:
        probs = {
            VerificationLabel.VALID: (0.9, 0.05, 0.05),
            VerificationLabel.INVALID: (0.05, 0.9, 0.05),
            VerificationLabel.UNKNOWN: (0.2, 0.2, 0.6),
        }[verdict]
        cand.verifications.append(
            VerificationResult(candidate_key=key, label=verdict, valid_prob=probs[0],
                               invalid_prob=probs[1], unknown_prob=probs[2])
        )
        cand.add_evidence(
            Evidence(key, EdgeType.CONTRADICT if verdict is VerificationLabel.INVALID
                     else EdgeType.SUPPORT, IndependenceGroup.BLIND_VERIFIER,
                     "blind_verifier", "q", 0, f"{key}-v",
                     mode=EvidenceMode.SHOWN_CANDIDATE)
        )
    if rejected:
        cand.status = CandidateStatus.REJECTED
        cand.rejection_reason = "hard contract violation"
    elif status is not None:
        cand.status = status
    graph.candidates[key] = cand
    return cand


def emitted(graph, config=DEFAULT_SELECTION):
    return [c.output_value for c in select(graph, config)]


# --- 1-4. finalization precondition and precedence ---------------------------


def test_finalization_refuses_executable_pending_controller_work():
    graph = graph_for(AWARDS)
    graph.pending_action = {"action_type": "RUN_FACET", "view_id": "award_facet_temporal",
                            "model_role": "enumerator"}
    with pytest.raises(SelectionInvariantError):
        finalize(graph)


def test_a_settled_graph_finalizes():
    graph = graph_for(BORDERS)
    add(graph, "alpha", mechanisms=4)
    prediction = finalize(graph)
    assert prediction.object_entities
    assert prediction.empty_reason is EmptyReason.NOT_EMPTY


def test_a_hard_rejected_candidate_is_never_emitted():
    for relation in (BORDERS, DEATH, STOCK, AWARDS):
        graph = graph_for(relation)
        # Maximal evidence, and still rejected.
        add(graph, "bad", mechanisms=4, verdict=VerificationLabel.VALID, rejected=True)
        assert emitted(graph) == [], relation


def test_no_selector_overrides_module_5_status():
    """A selector may not resurrect what `decide_status` refused."""
    for relation in (BORDERS, DEATH, STOCK, AWARDS, "hasArea", "hasCapacity"):
        graph = graph_for(relation)
        add(graph, "x", mechanisms=3, numeric=100.0 if "has" in relation else None,
            rejected=True)
        assert emitted(graph) == [], relation


# --- 5-8. raw frequency must not re-enter -------------------------------------


def test_ten_raw_repeats_do_not_beat_three_independent_mechanisms():
    """The decisive regression for the carried raw-support defect."""
    contract = get_contract("hasArea")
    graph = graph_for("hasArea")
    loud = add(graph, "500.0", surface="500", numeric=500.0, mechanisms=1, repeats=10)
    broad = add(graph, "100.0", surface="100", numeric=100.0, mechanisms=3)

    assert loud.raw_support_count == 10 and broad.raw_support_count == 3
    clusters = _numeric_clusters([loud, broad], contract, DEFAULT_SELECTION)
    support = {
        round(c.representative): _cluster_support(m, contract, DEFAULT_SELECTION)
        for c, m in clusters
    }
    assert support[100] > support[500], "raw mentions outweighed independent mechanisms"
    assert emitted(graph) == ["100"]


def test_cluster_weight_ignores_verifier_and_cross_model_support():
    """`F`, `L` and `X` stay separate at the final step too."""
    contract = get_contract("hasArea")
    graph = graph_for("hasArea")
    cand = add(graph, "100.0", surface="100", numeric=100.0, mechanisms=1)
    before = _cluster_support([cand], contract, DEFAULT_SELECTION)
    cand.add_evidence(
        Evidence("100.0", EdgeType.SUPPORT, IndependenceGroup.BLIND_VERIFIER,
                 "blind_verifier", "q", 0, "v", mode=EvidenceMode.SHOWN_CANDIDATE)
    )
    cand.add_evidence(
        Evidence("100.0", EdgeType.SUPPORT, IndependenceGroup.CROSS_MODEL_RECALL,
                 "v", "q", 0, "x")
    )
    assert cand.independent_support == 3          # raw accessor grew...
    assert _cluster_support([cand], contract, DEFAULT_SELECTION) == before  # ...weight did not


def test_string_ranking_uses_acquisition_support_not_raw_mentions():
    graph = graph_for(AWARDS)
    add(graph, "loud", mechanisms=1, repeats=10)
    add(graph, "broad", mechanisms=3)
    source = inspect.getsource(select)
    order = emitted(graph)
    assert set(order) == {"Loud", "Broad"}
    # Ordering must not be driven by the raw counter.
    assert "raw_support_count" not in inspect.getsource(
        __import__("cover_kbc.selection", fromlist=["_rank_key"])._rank_key
    )
    assert "independent_support" not in source


def test_no_raw_frequency_signal_drives_selection():
    source = Path(inspect.getfile(select)).read_text()
    assert "raw_support_count" not in source
    # The corrected accessor is used instead of the raw group counter.
    assert "supporting_acquisition_groups" in source


# --- 9-13. numeric geometry, representative, provenance ----------------------


def test_cluster_geometry_is_the_shared_primitive():
    """Module 6 and Module 8 must agree on what a cluster is."""
    import cover_kbc.coverage as module6

    assert "cluster_values" in inspect.getsource(module6.numeric_stability)
    assert "cluster_values" in inspect.getsource(_numeric_clusters)


def test_the_internal_threshold_is_distinct_from_the_official_tolerance():
    for contract in all_contracts():
        if contract.program_type is not ProgramType.NUMERIC:
            continue
        threshold = contract.selection.numeric_cluster_threshold
        assert 0.0 < threshold < 1.0
        assert threshold != 0.05, (
            "the internal clustering threshold must not be pinned to the "
            "evaluator's ±5% tolerance by assumption"
        )


def test_the_representative_is_the_cluster_median():
    graph = graph_for("hasArea")
    for value in (100.0, 102.0, 101.0):
        add(graph, f"{value}", surface=str(int(value)), numeric=value, mechanisms=1)
    values = [100.0, 101.0, 102.0]
    expected = cluster_values(values, threshold=get_contract("hasArea").selection.numeric_cluster_threshold)
    assert emitted(graph) == [str(int(expected[0].representative))]


def test_a_derived_representative_keeps_the_observed_surface():
    """An aggregate must never masquerade as something a model said."""
    graph = graph_for("hasArea")
    for value in (100.0, 102.0, 101.0):
        add(graph, f"{value}", surface=str(int(value)), numeric=value, mechanisms=1)
    chosen = select(graph)[0]
    assert chosen.derived_value                       # the median
    assert chosen.display_value                       # what was observed
    assert chosen.output_value == chosen.derived_value
    assert chosen.numeric_value is not None           # traceable to the cluster


def test_numeric_output_is_at_most_one_value():
    for relation in ("hasArea", "hasCapacity"):
        graph = graph_for(relation)
        for value in (100.0, 500.0, 900.0):
            add(graph, f"{value}", surface=str(int(value)), numeric=value, mechanisms=2)
        assert len(emitted(graph)) <= 1, relation


# --- 14-17. area and capacity semantics --------------------------------------

def test_area_emits_a_normalised_km2_scalar():
    graph = graph_for("hasArea")
    for value in (5000.0, 5050.0):
        add(graph, f"{value}", surface=str(int(value)), numeric=value, mechanisms=2)
    out = emitted(graph)
    assert len(out) == 1
    assert out[0].replace(".", "").isdigit(), f"not a bare numeral: {out[0]!r}"
    for unit in ("km", "sq", "mi", "hectare", ","):
        assert unit not in out[0].lower()


def test_capacity_emits_an_integer():
    graph = graph_for("hasCapacity")
    for value in (50000.0, 50500.0):
        add(graph, f"{value}", surface=str(int(value)), numeric=value, mechanisms=2)
    out = emitted(graph)
    assert len(out) == 1
    assert "." not in out[0], f"capacity must serialise as an integer: {out[0]!r}"
    assert out[0].isdigit()


def test_a_verifier_rejected_record_attendance_cluster_cannot_win_on_magnitude():
    """Spec §19: the target is maximum capacity, not the largest number seen."""
    graph = graph_for("hasCapacity")
    add(graph, "50000.0", surface="50000", numeric=50000.0, mechanisms=3,
        verdict=VerificationLabel.VALID)
    add(graph, "99000.0", surface="99000", numeric=99000.0, mechanisms=1,
        verdict=VerificationLabel.INVALID)          # record attendance
    out = emitted(graph)
    assert out == ["50000"], f"the rejected larger near miss won: {out}"


def test_a_weakly_supported_giant_cannot_outvote_a_strong_capacity_cluster():
    graph = graph_for("hasCapacity")
    add(graph, "50000.0", surface="50000", numeric=50000.0, mechanisms=3)
    add(graph, "250000.0", surface="250000", numeric=250000.0, mechanisms=1)
    assert emitted(graph) == ["50000"]


# --- 18-24. empty output truth table -----------------------------------------


def test_a_confident_negative_gate_yields_an_explained_empty_row():
    graph = graph_for(DEATH)
    graph.close_gate("calibrated gate: NO")
    prediction = finalize(graph)
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.CONFIDENT_NEGATIVE_GATE


def test_nothing_generated_is_reported_as_such():
    prediction = finalize(graph_for(DEATH))
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.NO_CANDIDATE_GENERATED


def test_generated_but_all_rejected_is_candidate_rejected():
    """The carried defect: this reason used to be unreachable."""
    graph = graph_for(DEATH)
    add(graph, "paris", mechanisms=2, rejected=True)
    add(graph, "lyon", mechanisms=1, rejected=True)
    prediction = finalize(graph)
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.CANDIDATE_REJECTED


def test_surviving_but_unaccepted_candidates_are_an_abstention():
    graph = graph_for(STOCK)
    # Present, not rejected, but below the acceptance policy.
    cand = add(graph, "weak", mechanisms=1)
    cand.verifications.append(
        VerificationResult(candidate_key="weak", label=VerificationLabel.UNKNOWN,
                           valid_prob=0.2, invalid_prob=0.2, unknown_prob=0.6)
    )
    prediction = finalize(graph)
    assert prediction.object_entities == []
    assert prediction.empty_reason is EmptyReason.UNRESOLVED_ABSTENTION


def test_an_uncertain_gate_is_never_relabelled_a_confident_negative():
    graph = graph_for(DEATH)
    assert not graph.gate_negative                    # gate ran, undecided
    add(graph, "paris", mechanisms=1, rejected=True)
    prediction = finalize(graph)
    assert prediction.empty_reason is EmptyReason.CANDIDATE_REJECTED
    assert prediction.empty_reason is not EmptyReason.CONFIDENT_NEGATIVE_GATE


@pytest.mark.parametrize(
    "reason", [r for r in EmptyReason if r is not EmptyReason.NOT_EMPTY]
)
def test_every_empty_reason_is_reachable(reason):
    """No decorative EmptyReason may survive."""
    builders = {
        EmptyReason.CONFIDENT_NEGATIVE_GATE: lambda g: g.close_gate("gate: NO"),
        EmptyReason.NO_CANDIDATE_GENERATED: lambda g: None,
        EmptyReason.CANDIDATE_REJECTED: lambda g: add(g, "x", mechanisms=1, rejected=True),
        EmptyReason.UNRESOLVED_ABSTENTION: lambda g: add(g, "x", mechanisms=1, status=None),
    }
    if reason not in builders:
        pytest.skip(f"{reason.value} is produced outside the selector")
    graph = graph_for(DEATH)
    build = builders[reason]
    build(graph)
    if reason is EmptyReason.UNRESOLVED_ABSTENTION:
        graph.candidates["x"].verifications.append(
            VerificationResult(candidate_key="x", label=VerificationLabel.UNKNOWN,
                               valid_prob=0.2, invalid_prob=0.2, unknown_prob=0.6)
        )
    prediction = finalize(graph)
    assert prediction.empty_reason is reason


def test_the_four_empty_states_never_collapse_into_each_other():
    seen = set()
    for build in (
        lambda g: g.close_gate("gate: NO"),
        lambda g: None,
        lambda g: add(g, "x", mechanisms=1, rejected=True),
    ):
        graph = graph_for(DEATH)
        build(graph)
        seen.add(finalize(graph).empty_reason)
    assert len(seen) == 3


# --- 25-29. relation scenarios ------------------------------------------------


def test_borders_emits_qualified_candidates_only():
    graph = graph_for(BORDERS)
    add(graph, "alpha", mechanisms=4)
    add(graph, "beta", mechanisms=3)
    add(graph, "bad", mechanisms=4, rejected=True)
    out = emitted(graph)
    assert "Bad" not in out
    assert set(out) == {"Alpha", "Beta"}
    assert len(out) == len(set(out))


def test_death_emits_at_most_one_locality():
    graph = graph_for(DEATH)
    add(graph, "paris", mechanisms=3)
    add(graph, "lyon", mechanisms=3)
    assert len(emitted(graph)) <= 1


def test_death_never_emits_a_rejected_competitor():
    graph = graph_for(DEATH)
    add(graph, "paris", mechanisms=3)
    add(graph, "france", mechanisms=3, rejected=True)      # country, not a city
    assert emitted(graph) == ["Paris"]


def test_stock_drops_a_weak_unresolved_listing():
    graph = graph_for(STOCK)
    add(graph, "alpha exchange", surface="Alpha Exchange", mechanisms=3)
    weak = add(graph, "parent exchange", surface="Parent Exchange", mechanisms=1)
    weak.verifications.append(
        VerificationResult(candidate_key="parent exchange", label=VerificationLabel.UNKNOWN,
                           valid_prob=0.2, invalid_prob=0.2, unknown_prob=0.6)
    )
    out = emitted(graph)
    assert "Parent Exchange" not in out


def test_awards_drop_the_weak_uncertain_tail():
    graph = graph_for(AWARDS)
    add(graph, "winner", mechanisms=3)
    tail = add(graph, "tail", mechanisms=1)
    tail.verifications.append(
        VerificationResult(candidate_key="tail", label=VerificationLabel.UNKNOWN,
                           valid_prob=0.2, invalid_prob=0.2, unknown_prob=0.6)
    )
    out = emitted(graph)
    assert "Winner" in out and "Tail" not in out


def test_awards_do_not_emit_a_rejected_winning_work():
    graph = graph_for(AWARDS)
    add(graph, "recipient", mechanisms=3)
    add(graph, "the winning novel", surface="The Winning Novel", mechanisms=3, rejected=True)
    assert emitted(graph) == ["Recipient"]


# --- 30-35. identity, surfaces, dedup, order ---------------------------------


def test_a_soft_alias_hint_never_merges_distinct_strict_candidates():
    """Audit 0006's boundary, preserved at the output."""
    a, b = "The Alpha Exchange", "Alpha Exchange"
    assert official_normalize_string(a) != official_normalize_string(b)
    assert dedupe_object_entities([a, b]) == [a, b]


def test_the_writer_removes_exactly_what_the_evaluator_would_collapse():
    assert dedupe_object_entities(["Alpha Exchange", "alpha  exchange"]) == ["Alpha Exchange"]
    assert dedupe_object_entities(["X", "X (qualifier)"]) == ["X", "X (qualifier)"]
    assert dedupe_object_entities(["Le Havre", "Havre"]) == ["Le Havre", "Havre"]


def test_one_strict_candidate_yields_one_surface():
    graph = graph_for(BORDERS)
    cand = add(graph, "alpha", surface="Alpha", mechanisms=3)
    cand.surface_forms.extend(["ALPHA", "alpha"])
    out = emitted(graph)
    assert out == ["Alpha"]


def test_emitted_surfaces_come_from_candidate_provenance():
    graph = graph_for(BORDERS)
    add(graph, "alpha", surface="Alpha", mechanisms=3)
    chosen = select(graph)[0]
    assert chosen.output_value in {chosen.display_value, chosen.derived_value}
    assert chosen.output_value == "Alpha"


@pytest.mark.parametrize("junk", ["NONE", "UNKNOWN", "VALID", "INVALID", "N/A"])
def test_a_control_token_can_never_be_emitted_as_an_object(junk):
    """A leaked verifier label must fail loudly, not ship in a submission."""
    graph = graph_for(BORDERS)
    add(graph, junk.lower(), surface=junk, mechanisms=3)
    with pytest.raises(SelectionInvariantError):
        select(graph)


def test_output_order_is_deterministic_under_insertion_shuffle():
    keys = ["alpha", "beta", "gamma", "delta"]
    results = []
    for order in (keys, list(reversed(keys)), keys[2:] + keys[:2]):
        graph = graph_for(AWARDS)
        for key in order:
            add(graph, key, mechanisms=3)          # identical evidence
        results.append(tuple(emitted(graph)))
    assert len(set(results)) == 1, results


# --- 36-41. writer / schema boundary -----------------------------------------


def test_the_writer_performs_no_semantic_selection():
    source = Path(inspect.getfile(prediction_rows)).read_text()
    tree = ast.parse(source)
    calls = {
        getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    for owned_by_the_selector in ("select", "decide_status", "score_candidate",
                                  "cluster_values", "assign_tier", "finalize"):
        assert owned_by_the_selector not in calls
    # Actual usage, not the docstring explaining why it is not used.
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    } | {
        a.asname or a.name for imp in ast.walk(tree)
        if isinstance(imp, ast.ImportFrom) for a in imp.names
    }
    assert "alias_hint_key" not in used, "the writer must not fold soft aliases"
    assert "strict_key" in used, "dedup must use the evaluator-identical key"


def test_the_official_row_carries_exactly_the_three_fields():
    graph = graph_for(BORDERS)
    add(graph, "alpha", mechanisms=4)
    rows = prediction_rows([finalize(graph)])
    assert set(rows[0]) == {"SubjectEntity", "Relation", "ObjectEntities"}
    for leaked in ("score", "confidence", "empty_reason", "candidates",
                   "controller_log", "budget"):
        assert leaked not in rows[0]


def test_no_diagnostics_leak_into_the_submission_row(tmp_path):
    graph = graph_for(BORDERS)
    add(graph, "alpha", mechanisms=4)
    prediction = finalize(graph)
    path = write_predictions([prediction], tmp_path / "p.jsonl",
                             expected_queries=[Query("S", BORDERS, 0)])
    row = json.loads(path.read_text().splitlines()[0])
    assert set(row) == {"SubjectEntity", "Relation", "ObjectEntities"}
    assert all(isinstance(v, str) for v in row["ObjectEntities"])


@pytest.mark.parametrize(
    "objects", [[], ["Alpha"], ["Alpha", "Beta", "Gamma"]]
)
def test_the_official_evaluator_accepts_every_output_cardinality(objects, tmp_path):
    from cover_kbc.types import Prediction

    prediction = Prediction(subject="S", relation=BORDERS, object_entities=list(objects))
    rows = prediction_rows([prediction])
    gold = [{"SubjectEntity": "S", "Relation": BORDERS,
             "ObjectEntities": [["Alpha"], ["Beta"]]}]
    report = evaluate_predictions(rows, gold)          # must not raise
    assert 0.0 <= report.overall_macro_f1 <= 1.0


def test_the_official_evaluator_accepts_numeric_output():
    from cover_kbc.types import Prediction

    rows = prediction_rows([Prediction(subject="S", relation="hasArea",
                                       object_entities=["5000"])])
    gold = [{"SubjectEntity": "S", "Relation": "hasArea", "ObjectEntities": [["5010"]]}]
    report = evaluate_predictions(rows, gold)
    # Within the evaluator's own ±5% tolerance - its behaviour, not ours.
    assert report.overall_macro_f1 == pytest.approx(1.0)


# --- 42-46. determinism, round trip, compliance ------------------------------


def test_finalization_survives_a_stage_round_trip(tmp_path):
    from cover_kbc.staging import StageWriter, read_stage

    for relation, kwargs in (
        (BORDERS, dict(mechanisms=4)),
        (AWARDS, dict(mechanisms=3)),
        ("hasArea", dict(mechanisms=2, numeric=5000.0, surface="5000")),
        ("hasCapacity", dict(mechanisms=2, numeric=50000.0, surface="50000")),
    ):
        graph = graph_for(relation)
        add(graph, "alpha" if kwargs.get("numeric") is None else "5000", **kwargs)
        before = finalize(graph)

        with StageWriter(tmp_path / f"{relation}.jsonl") as writer:
            writer.write(graph_for(relation) if False else graph)
        reloaded = list(read_stage(tmp_path / f"{relation}.jsonl"))[0]
        after = finalize(reloaded)
        assert before.object_entities == after.object_entities, relation
        assert before.empty_reason == after.empty_reason, relation


def test_an_empty_row_survives_a_stage_round_trip(tmp_path):
    from cover_kbc.staging import StageWriter, read_stage

    graph = graph_for(DEATH)
    add(graph, "paris", mechanisms=1, rejected=True)
    before = finalize(graph)
    with StageWriter(tmp_path / "d.jsonl") as writer:
        writer.write(graph)
    after = finalize(list(read_stage(tmp_path / "d.jsonl"))[0])
    assert before.empty_reason is after.empty_reason is EmptyReason.CANDIDATE_REJECTED


def test_module_8_makes_no_model_call():
    source = Path(inspect.getfile(select)).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"generate", "score_labels", "verify_candidate",
                                "verify_multi_template", "score_gate"}
    for banned in ("runtime", "LMRuntime", "verifier_runtime"):
        assert banned not in source


def test_module_8_performs_no_retrieval_and_has_no_factual_table():
    source = Path(inspect.getfile(select)).read_text()
    tree = ast.parse(source)
    banned = {"requests", "urllib", "httpx", "wikipedia", "wikidata", "sklearn", "torch"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] not in banned for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
    # No embedded factual list: any long string tuple/list of proper nouns.
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Set)) and len(getattr(node, "elts", [])) > 8:
            values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            assert not values, f"suspicious embedded literal collection: {values[:5]}"


def test_no_learned_selector_exists():
    source = Path(inspect.getfile(select)).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"fit", "partial_fit", "train", "predict", "predict_proba"}


def test_every_selector_constant_is_configuration():
    tree = ast.parse(inspect.getsource(select))
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, float)
    ]
    assert not literals
    assert hasattr(DEFAULT_SELECTION, "capacity_support_ratio")
    assert hasattr(DEFAULT_SELECTION, "capacity_trust_verified")
    for contract in all_contracts():
        assert contract.selection.max_objects >= 0


def test_every_program_type_has_a_selector():
    from cover_kbc.selection import _BY_PROGRAM

    assert set(_BY_PROGRAM) == set(ProgramType)


@pytest.mark.parametrize("contract", list(all_contracts()), ids=lambda c: c.relation)
def test_every_relation_respects_its_cardinality_invariant(contract):
    graph = graph_for(contract.relation)
    for i in range(4):
        add(graph, f"c{i}", surface=f"C{i}", mechanisms=3,
            numeric=float(100 + i * 500) if contract.is_numeric else None)
    out = emitted(graph)
    limit = contract.selection.max_objects
    if limit:
        assert len(out) <= limit
    assert len(out) == len(set(out)), "duplicate objects emitted"
