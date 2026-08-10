"""Audit 0076: V3.1 score recovery - safety, semantics and non-regression.

Four things these tests exist to prevent:

1. a production rule that reads gold, a TRAIN label or a subject-specific
   answer table;
2. a V3.1 feature that changes behaviour while V3.1 is switched off;
3. a calibration-shifting feature reaching TEST under the frozen calibration;
4. ``countryLandBordersCountry`` moving at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.controller_calibration.readiness import (
    ReadinessState,
    evaluate_test_readiness,
)
from cover_kbc.evidence.graph import EvidenceGraph, build_graph
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.selection import DEFAULT_SELECTION, SelectionConfig, finalize, select
from cover_kbc.types import (
    Candidate,
    CandidateStatus,
    EdgeType,
    Evidence,
    EvidenceMode,
    IndependenceGroup,
    Query,
    VerificationLabel,
    VerificationResult,
)
from cover_kbc.v3_1 import (
    INTERVENTIONS,
    SAFE,
    V31Config,
    V31SafeConfig,
    apply_support_dominance,
    canonicalize_numeric_output,
    compatibility_of,
    explicit_unit_of,
    is_contradicted,
    is_retainable,
    parse_scalar,
    rank_entity_candidates,
    reject_structurally_invalid_listings,
    repair_value,
    repair_values,
    retained_pool,
    to_canonical_km2,
)
from cover_kbc.v3_1.prompts import INSTRUCTIONS, prompt_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]
V3_1_PACKAGE = REPO_ROOT / "src" / "cover_kbc" / "v3_1"
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"


# --------------------------------------------------------------------------
# 1. No gold dependency, no subject-specific answers
# --------------------------------------------------------------------------


def _v3_1_sources() -> list[Path]:
    return sorted(V3_1_PACKAGE.glob("*.py"))


def test_v3_1_package_has_sources():
    assert _v3_1_sources(), "no V3.1 sources found to audit"


@pytest.mark.parametrize("path", _v3_1_sources(), ids=lambda p: p.name)
def test_no_gold_or_label_dependency(path: Path):
    """No production predicate may name gold, a label file or a row index."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Identifiers, not prose: the docstrings legitimately discuss TRAIN gold as
    # discovery evidence, and forbidding that would forbid explaining the rule.
    forbidden = re.compile(
        r"\b(gold|ground_truth|train_jsonl|object_entities_gold|is_correct|"
        r"row_index|gold_set|answer_key|gold_objects)\b"
    )
    # Docstrings are exempt by design: they must be free to *explain* which
    # TRAIN evidence motivated a rule, which is the opposite of keying on it.
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and forbidden.search(node.id):
            offenders.append(node.id)
        elif isinstance(node, ast.Attribute) and forbidden.search(node.attr):
            offenders.append(node.attr)
        elif isinstance(node, ast.arg) and forbidden.search(node.arg):
            offenders.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if "train.jsonl" in node.value or "benchmark/data" in node.value:
                offenders.append(node.value)
    assert not offenders, f"{path.name} references gold state: {sorted(set(offenders))}"


@pytest.mark.parametrize("path", _v3_1_sources(), ids=lambda p: p.name)
def test_no_network_or_filesystem_access(path: Path):
    """Production predicates are pure functions of inference-time state."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"requests", "urllib", "httpx", "socket", "wikipedia", "wikidata",
              "sqlite3", "pickle", "open"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, f"{path.name}: {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned, f"{path.name}: {node.module}"


def test_no_subject_specific_answer_tables():
    """No module-level mapping may hold entity->answer pairs.

    The only literal collections V3.1 declares are structural vocabularies
    (abstention tokens, enumeration category nouns, unit spellings). A dict
    whose values are entity-like strings would be a memorised answer table.
    """
    entity_like = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z.]+)+$")
    for path in _v3_1_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    assert not entity_like.match(value.value), (
                        f"{path.name} maps to an entity-like value {value.value!r}")


def test_every_enabled_feature_is_declared():
    """A feature cannot inherit safety from the config block it sits in."""
    declared = {record.feature for record in INTERVENTIONS}
    from dataclasses import fields
    from cover_kbc.v3_1.config import V31AggressiveConfig
    for config_cls in (V31SafeConfig, V31AggressiveConfig):
        for field in fields(config_cls):
            assert field.name in declared, (
                f"{config_cls.__name__}.{field.name} has no InterventionRecord "
                "stating where it runs")


def test_declared_tracks_match_config_blocks():
    from dataclasses import fields
    from cover_kbc.v3_1.config import V31AggressiveConfig
    safe_names = {f.name for f in fields(V31SafeConfig)}
    for record in INTERVENTIONS:
        if record.track == "safe":
            assert record.feature in safe_names
            assert record.compatibility == SAFE
        else:
            assert record.feature in {f.name for f in fields(V31AggressiveConfig)}
            assert record.compatibility == "CALIBRATION_REVIEW_REQUIRED"


def test_compatibility_of_rejects_undeclared_feature():
    with pytest.raises(KeyError, match="not a declared V3.1 intervention"):
        compatibility_of("some_new_idea")


# --------------------------------------------------------------------------
# Graph fixtures
# --------------------------------------------------------------------------


def _graph(relation: str, subject: str = "Subject") -> EvidenceGraph:
    return build_graph(Query(subject, relation, 0), CONTRACTS[relation])


def _candidate(
    graph: EvidenceGraph,
    value: str,
    *,
    status: CandidateStatus,
    numeric: float | None = None,
    mechanisms: int = 1,
    verdict: VerificationLabel | None = None,
) -> Candidate:
    """Attach one candidate whose *evidence* realises the requested status.

    Setting ``Candidate.status`` directly would be meaningless for anything
    that goes through :func:`select`: Module 8 re-runs ``decide_status`` over
    the evidence and overwrites it. So the status is produced the way
    production produces it - an UNRESOLVED candidate is one the verifier
    answered UNKNOWN about, a REJECTED one is a candidate it called INVALID -
    which is also what makes these tests exercise the real acceptance policy.
    """
    contract = graph.contract
    groups = list(contract.eligible_independence_groups)
    key = contract.key(value)
    if verdict is None:
        verdict = {
            CandidateStatus.UNRESOLVED: VerificationLabel.UNKNOWN,
            CandidateStatus.REJECTED: VerificationLabel.INVALID,
        }.get(status)
    candidate = Candidate(
        key=key, display_value=value, relation=contract.relation, numeric_value=numeric,
    )
    for index in range(mechanisms):
        candidate.add_evidence(
            Evidence(key, EdgeType.SUPPORT, groups[index % len(groups)],
                     "view", "model", 0, f"{key}-{index}")
        )
    if verdict is not None:
        probs = {
            VerificationLabel.VALID: (0.9, 0.05, 0.05),
            VerificationLabel.INVALID: (0.05, 0.9, 0.05),
            VerificationLabel.UNKNOWN: (0.2, 0.2, 0.6),
        }[verdict]
        candidate.verifications.append(
            VerificationResult(candidate_key=key, label=verdict, valid_prob=probs[0],
                               invalid_prob=probs[1], unknown_prob=probs[2])
        )
        candidate.add_evidence(
            Evidence(key, EdgeType.CONTRADICT if verdict is VerificationLabel.INVALID
                     else EdgeType.SUPPORT, IndependenceGroup.BLIND_VERIFIER,
                     "blind_verifier", "q", 0, f"{key}-verdict",
                     mode=EvidenceMode.SHOWN_CANDIDATE)
        )
    candidate.status = status
    graph.candidates[key] = candidate
    return candidate


SAFE_ALL = SelectionConfig(
    v3_1=V31Config(
        enabled=True,
        safe=V31SafeConfig(
            final_candidate_retention=True,
            enumeration_label_repair=True,
            stock_support_dominance=True,
            stock_structural_validation=True,
            numeric_output_canonicalization=True,
        ),
    )
)


# --------------------------------------------------------------------------
# 2. FINAL_CANDIDATE_RETENTION
# --------------------------------------------------------------------------


def test_retention_predicate_requires_acceptance():
    graph = _graph("hasArea")
    unresolved = _candidate(graph, "1", status=CandidateStatus.UNRESOLVED, numeric=1.0)
    accepted = _candidate(graph, "15", status=CandidateStatus.ACCEPTED, numeric=15.0)
    assert not is_retainable([unresolved])
    assert is_retainable([accepted])


def test_contradicted_candidate_is_not_retained():
    graph = _graph("hasArea")
    invalid = _candidate(
        graph, "15", status=CandidateStatus.ACCEPTED, numeric=15.0,
        verdict=VerificationLabel.INVALID,
    )
    assert is_contradicted([invalid])
    assert not is_retainable([invalid]), "an INVALID verdict must veto retention"


def test_valid_outranks_invalid_across_a_cluster():
    graph = _graph("hasArea")
    invalid = _candidate(graph, "15", status=CandidateStatus.ACCEPTED, numeric=15.0,
                         verdict=VerificationLabel.INVALID)
    valid = _candidate(graph, "15.2", status=CandidateStatus.ACCEPTED, numeric=15.2,
                       verdict=VerificationLabel.VALID)
    assert not is_contradicted([invalid, valid])
    assert is_retainable([invalid, valid])


def test_rejected_candidate_is_not_retained():
    graph = _graph("hasArea")
    rejected = _candidate(graph, "15", status=CandidateStatus.REJECTED, numeric=15.0)
    assert not is_retainable([rejected])


def test_retained_pool_is_identity_when_disabled():
    pool = [("a", []), ("b", [])]
    assert retained_pool(pool, enabled=False) == pool


def test_retained_pool_falls_back_when_nothing_qualifies():
    graph = _graph("hasArea")
    unresolved = _candidate(graph, "1", status=CandidateStatus.UNRESOLVED, numeric=1.0)
    pool = [("only", [unresolved])]
    assert retained_pool(pool, enabled=True) == pool


def test_retention_rescues_the_mangareva_shape():
    """The exact TRAIN row 1 mechanism: accepted candidate in the larger cluster.

    Cluster order breaks a size tie on the smallest representative, so ``1``
    wins the ordering while ``15`` is the only ACCEPTED candidate. Without
    retention the row is empty; with it the row emits ``15``.
    """
    graph = _graph("hasArea", subject="Mangareva")
    _candidate(graph, "1", status=CandidateStatus.UNRESOLVED, numeric=1.0)
    _candidate(graph, "15", status=CandidateStatus.ACCEPTED, numeric=15.0)

    assert select(graph, DEFAULT_SELECTION) == []

    chosen = select(graph, SAFE_ALL)
    assert [c.output_value for c in chosen] == ["15"]


def test_retention_does_not_change_a_row_that_already_emits():
    graph = _graph("hasArea")
    _candidate(graph, "100", status=CandidateStatus.ACCEPTED, numeric=100.0)
    _candidate(graph, "500", status=CandidateStatus.ACCEPTED, numeric=500.0)
    assert ([c.output_value for c in select(graph, DEFAULT_SELECTION)]
            == [c.output_value for c in select(graph, SAFE_ALL)])


def test_retention_never_invents_an_answer_from_nothing():
    graph = _graph("hasArea")
    _candidate(graph, "1", status=CandidateStatus.UNRESOLVED, numeric=1.0)
    _candidate(graph, "900", status=CandidateStatus.UNRESOLVED, numeric=900.0)
    assert select(graph, SAFE_ALL) == []


# --------------------------------------------------------------------------
# 3. Enumeration-label repair
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    "Groups: NONE", "Organisations: NONE", "Projects: NONE",
    "1920s: NONE", "1930s: NONE", "Individuals: NONE",
])
def test_abstention_buckets_are_dropped(value):
    assert repair_value(value) is None


@pytest.mark.parametrize("value,expected", [
    ("Individuals: Albert Schweitzer", "Albert Schweitzer"),
    ("1990s: Alan Shearer", "Alan Shearer"),
    ("1901-1909: Wilhelm Conrad Rontgen", "Wilhelm Conrad Rontgen"),
    ("2020s: Hugo Duminil-Copin", "Hugo Duminil-Copin"),
    ("Groups: The Whistleblowers", "The Whistleblowers"),
])
def test_bucket_payload_is_extracted(value, expected):
    assert repair_value(value) == expected


@pytest.mark.parametrize("value", [
    "Alan Shearer",
    "Sun Microsystems: A History",       # colon, but the prefix is not a bucket
    "Dr. Strangelove: How I Learned",
    "Nasdaq",
])
def test_non_bucket_values_are_untouched(value):
    assert repair_value(value) == value


def test_repair_is_identity_when_disabled():
    values = ["Groups: NONE", "1990s: Alan Shearer"]
    assert repair_values(values, enabled=False) == values


def test_repair_deduplicates_on_evaluator_normalisation():
    values = ["Individuals: Albert Einstein", "1930s: Albert Einstein", "Albert Einstein"]
    assert repair_values(values, enabled=True) == ["Albert Einstein"]


def test_repair_can_empty_a_row_and_finalize_reports_a_reason():
    """The all-``NONE`` award row: four values in, nothing out, reason intact."""
    graph = _graph("awardWonBy", subject="Order of the Golden Fleece (Georgia)")
    for value in ("Groups: NONE", "Individuals: NONE",
                  "Organisations: NONE", "Projects: NONE"):
        _candidate(graph, value, status=CandidateStatus.ACCEPTED, mechanisms=3)
    prediction = finalize(graph, config=SAFE_ALL)
    assert prediction.object_entities == []
    assert prediction.empty_reason.value != "not_empty"


# --------------------------------------------------------------------------
# 4. Numeric parsing, units and canonicalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("75,000", 75000.0),
    ("75 000", 75000.0),
    ("75000", 75000.0),
    ("75,000.0", 75000.0),
    ("7.5e4", 75000.0),
    ("7.5E4", 75000.0),
    ("7.5e-2", 0.075),
    ("12'345", 12345.0),
    ("1.234.567", 1234567.0),
    ("1 234,5", 1234.5),
])
def test_scalar_parsing_formats(text, expected):
    assert parse_scalar(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "  ", "abc", "12 km2", "1,2,3 apples"])
def test_scalar_parsing_refuses_non_scalars(text):
    assert parse_scalar(text) is None


@pytest.mark.parametrize("text,unit", [
    ("100 km2", "km2"), ("100 km²", "km2"), ("100 sq km", "km2"),
    ("100 square kilometres", "km2"), ("100 m2", "m2"), ("100 sq m", "m2"),
    ("100 hectares", "ha"), ("100 ha", "ha"),
    ("100 acres", "acre"), ("100 acre", "acre"),
    ("100 sq mi", "mi2"), ("100 square miles", "mi2"), ("100 mi2", "mi2"),
])
def test_explicit_units_are_recognised(text, unit):
    assert explicit_unit_of(text) == unit


@pytest.mark.parametrize("text,expected", [
    ("1 km2", 1.0),
    ("1000000 m2", 1.0),
    ("100 hectares", 1.0),
    ("1 hectare", 0.01),
    ("1 acre", 0.0040468564224),
    ("1 square mile", 2.589988110336),
    ("1 sq mi", 2.589988110336),
])
def test_area_conversions_are_the_declared_constants(text, expected):
    assert to_canonical_km2(text) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("text", ["75000", "75,000", "7.5e4", "100"])
def test_no_conversion_without_an_explicit_unit(text):
    """The absolute rule: a bare number is never multiplied by anything."""
    assert explicit_unit_of(text) is None
    assert to_canonical_km2(text) == pytest.approx(parse_scalar(text))


def test_a_bare_number_is_never_scaled_by_the_square_mile_factor():
    for value in ("100", "5556", "217291"):
        assert to_canonical_km2(value) == pytest.approx(float(value))


def test_canonicalization_is_identity_when_disabled():
    assert canonicalize_numeric_output("75,000", enabled=False) == "75,000"


def test_canonicalization_emits_official_parser_safe_output():
    assert canonicalize_numeric_output("75,000", enabled=True) == "75000"
    assert canonicalize_numeric_output("7.5e4", enabled=True) == "75000"
    assert float(canonicalize_numeric_output("1 sq mi", enabled=True)) == pytest.approx(
        2.589988110336, rel=1e-6)


def test_canonicalization_leaves_unreadable_text_alone():
    assert canonicalize_numeric_output("about half the island", enabled=True) == (
        "about half the island")


def test_format_numeric_round_trips_through_the_official_parser():
    for value in (0.5, 15.4, 673.4, 5556.0, 217291.0):
        assert float(format_numeric(value)) == pytest.approx(value, rel=5e-2)


# --------------------------------------------------------------------------
# 5. Stock finalization
# --------------------------------------------------------------------------


def test_support_dominance_keeps_the_maximally_supported_listing():
    graph = _graph("companyTradesAtStockExchange", subject="McBride plc")
    lse = _candidate(graph, "London Stock Exchange", status=CandidateStatus.ACCEPTED)
    ise = _candidate(graph, "Irish Stock Exchange", status=CandidateStatus.ACCEPTED)
    support = {lse.key: 2, ise.key: 1}
    kept = apply_support_dominance([lse, ise], lambda c: support[c.key], enabled=True)
    assert [c.display_value for c in kept] == ["London Stock Exchange"]


def test_support_dominance_keeps_every_listing_on_a_tie():
    """Multi-listed companies must survive: no fixed cardinality is imposed."""
    graph = _graph("companyTradesAtStockExchange", subject="Chubu Electric Power")
    tokyo = _candidate(graph, "Tokyo Stock Exchange", status=CandidateStatus.ACCEPTED)
    nagoya = _candidate(graph, "Nagoya Stock Exchange", status=CandidateStatus.ACCEPTED)
    kept = apply_support_dominance([tokyo, nagoya], lambda c: 2, enabled=True)
    assert len(kept) == 2


def test_support_dominance_is_identity_when_disabled():
    graph = _graph("companyTradesAtStockExchange")
    a = _candidate(graph, "A Exchange", status=CandidateStatus.ACCEPTED)
    b = _candidate(graph, "B Exchange", status=CandidateStatus.ACCEPTED)
    support = {a.key: 5, b.key: 1}
    assert apply_support_dominance([a, b], lambda c: support[c.key], enabled=False) == [a, b]


def test_structural_validation_rejects_the_subject_company():
    graph = _graph("companyTradesAtStockExchange", subject="Morningstar, Inc.")
    self_ref = _candidate(graph, "Morningstar, Inc.", status=CandidateStatus.ACCEPTED)
    nasdaq = _candidate(graph, "Nasdaq", status=CandidateStatus.ACCEPTED)
    kept = reject_structurally_invalid_listings(
        [self_ref, nasdaq], "Morningstar, Inc.", enabled=True)
    assert [c.display_value for c in kept] == ["Nasdaq"]


@pytest.mark.parametrize("value", ["NONE", "unlisted", "OTC", "not applicable"])
def test_structural_validation_rejects_status_tokens(value):
    graph = _graph("companyTradesAtStockExchange", subject="Acme")
    token = _candidate(graph, value, status=CandidateStatus.ACCEPTED)
    assert reject_structurally_invalid_listings([token], "Acme", enabled=True) == []


@pytest.mark.parametrize("value", ["NYSE", "AIM", "Nasdaq", "SIX Swiss Exchange"])
def test_structural_validation_keeps_short_real_exchange_names(value):
    """Ticker-shaped names that are genuine exchanges must never be rejected."""
    graph = _graph("companyTradesAtStockExchange", subject="Acme")
    exchange = _candidate(graph, value, status=CandidateStatus.ACCEPTED)
    kept = reject_structurally_invalid_listings([exchange], "Acme", enabled=True)
    assert [c.display_value for c in kept] == [value]


def test_stock_rules_do_not_apply_to_borders():
    """Borders share SMALL_SET; the rules are relation-gated, not programme-gated."""
    graph = _graph("countryLandBordersCountry", subject="France")
    # Borders declare six eligible independence groups, so acceptance needs more
    # than one mechanism here; two differently supported borders are exactly the
    # shape stock_support_dominance would prune if it were programme-gated.
    _candidate(graph, "Germany", status=CandidateStatus.ACCEPTED, mechanisms=4)
    _candidate(graph, "Spain", status=CandidateStatus.ACCEPTED, mechanisms=2)
    baseline = [c.output_value for c in select(graph, DEFAULT_SELECTION)]
    with_v3_1 = [c.output_value for c in select(graph, SAFE_ALL)]
    assert baseline == with_v3_1 == ["Germany", "Spain"]


# --------------------------------------------------------------------------
# 6. City-of-death: evidence-based, never first-seen
# --------------------------------------------------------------------------


def test_city_ranking_is_evidence_based_not_insertion_ordered():
    """Audit 0075 measured keep-first as harmful; ranking must not reproduce it."""
    graph = _graph("personHasCityOfDeath", subject="A Person")
    first = _candidate(graph, "Aaa City", status=CandidateStatus.ACCEPTED, mechanisms=1)
    second = _candidate(graph, "Zzz City", status=CandidateStatus.ACCEPTED, mechanisms=3)
    support = {first.key: 1, second.key: 3}
    ranked = rank_entity_candidates([first, second], lambda c: support[c.key])
    assert ranked[0].display_value == "Zzz City", "support must beat arrival order"

    reversed_order = rank_entity_candidates([second, first], lambda c: support[c.key])
    assert [c.key for c in ranked] == [c.key for c in reversed_order]


def test_city_ranking_prefers_a_verified_candidate():
    graph = _graph("personHasCityOfDeath", subject="A Person")
    plain = _candidate(graph, "Plain City", status=CandidateStatus.ACCEPTED, mechanisms=1)
    verified = _candidate(graph, "Verified City", status=CandidateStatus.ACCEPTED,
                          verdict=VerificationLabel.VALID)
    ranked = rank_entity_candidates([plain, verified], lambda c: 1)
    assert ranked[0].display_value == "Verified City"


def test_city_ranking_drops_a_contradicted_candidate():
    graph = _graph("personHasCityOfDeath", subject="A Person")
    good = _candidate(graph, "Good City", status=CandidateStatus.ACCEPTED)
    bad = _candidate(graph, "Bad City", status=CandidateStatus.ACCEPTED,
                     verdict=VerificationLabel.INVALID)
    ranked = rank_entity_candidates([bad, good], lambda c: 1)
    assert [c.display_value for c in ranked] == ["Good City"]


def test_city_relation_stays_single_valued():
    graph = _graph("personHasCityOfDeath", subject="A Person")
    _candidate(graph, "First City", status=CandidateStatus.ACCEPTED, mechanisms=2)
    _candidate(graph, "Second City", status=CandidateStatus.ACCEPTED, mechanisms=1)
    assert len(select(graph, SAFE_ALL)) == 1


# --------------------------------------------------------------------------
# 7. V3 baseline unchanged when V3.1 is disabled
# --------------------------------------------------------------------------


def test_default_selection_config_has_v3_1_fully_off():
    assert DEFAULT_SELECTION.v3_1.enabled is False
    assert DEFAULT_SELECTION.v3_1.safe.enabled_features == ()
    assert DEFAULT_SELECTION.v3_1.aggressive.enabled_features == ()
    assert DEFAULT_SELECTION.v3_1_safe.any_enabled is False


def test_enabled_false_neutralises_configured_features():
    """``enabled: false`` must win over a fully populated safe block."""
    config = SelectionConfig(
        v3_1=V31Config(
            enabled=False,
            safe=V31SafeConfig(final_candidate_retention=True),
        )
    )
    graph = _graph("hasArea")
    _candidate(graph, "1", status=CandidateStatus.UNRESOLVED, numeric=1.0)
    _candidate(graph, "15", status=CandidateStatus.ACCEPTED, numeric=15.0)
    assert select(graph, config) == []


@pytest.mark.parametrize("relation", sorted(CONTRACTS))
def test_v3_1_disabled_reproduces_v3_selection(relation):
    graph_a = _graph(relation, subject="Subject")
    graph_b = _graph(relation, subject="Subject")
    for graph in (graph_a, graph_b):
        _candidate(graph, "Alpha", status=CandidateStatus.ACCEPTED,
                   numeric=10.0 if relation in ("hasArea", "hasCapacity") else None)
        _candidate(graph, "Beta", status=CandidateStatus.UNRESOLVED,
                   numeric=90.0 if relation in ("hasArea", "hasCapacity") else None)
    disabled = SelectionConfig(v3_1=V31Config(enabled=False, safe=V31SafeConfig(
        final_candidate_retention=True, enumeration_label_repair=True)))
    assert ([c.output_value for c in select(graph_a, DEFAULT_SELECTION)]
            == [c.output_value for c in select(graph_b, disabled)])


def test_config_rejects_an_unknown_feature_name():
    with pytest.raises(ValueError, match="unknown v3_1.safe feature"):
        V31Config.from_mapping({"enabled": True, "safe": {"make_it_better": True}})


# --------------------------------------------------------------------------
# 8. Configs and readiness
# --------------------------------------------------------------------------


def _load(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def test_safe_config_is_test_ready_under_the_existing_calibration():
    report = evaluate_test_readiness(
        _load("cover_kbc_v3_1_safe_test.yaml"), base_dir=CONFIG_DIR, split="test")
    assert report.state is ReadinessState.FULL_TEST_READY, report.blockers
    assert report.details["v3_1_calibration_status"] == SAFE


def test_aggressive_config_is_refused_and_says_why():
    report = evaluate_test_readiness(
        _load("cover_kbc_v3_1_aggressive_test.yaml"), base_dir=CONFIG_DIR, split="test")
    assert report.state is not ReadinessState.FULL_TEST_READY
    assert report.details["v3_1_calibration_status"] == "CALIBRATION_REVIEW_REQUIRED"
    assert any("CALIBRATION_REVIEW_REQUIRED" in b for b in report.blockers)


def test_frozen_v3_test_config_is_still_ready_and_untouched_by_v3_1():
    report = evaluate_test_readiness(
        _load("cover_kbc_v3_test.yaml"), base_dir=CONFIG_DIR, split="test")
    assert report.state is ReadinessState.FULL_TEST_READY, report.blockers
    assert report.details["v3_1"] == "absent"


def test_safe_config_reuses_the_audit_0073_calibration_hashes():
    v3 = _load("cover_kbc_v3_test.yaml")
    safe = _load("cover_kbc_v3_1_safe_test.yaml")
    assert safe["relation_budget_scheduler"] == v3["relation_budget_scheduler"]
    assert safe["micro_planner"] == v3["micro_planner"]
    assert safe["calibration_provenance"] == v3["calibration_provenance"]


def test_safe_config_differs_from_v3_only_in_experiment_and_v3_1():
    v3 = _load("cover_kbc_v3_test.yaml")
    safe = _load("cover_kbc_v3_1_safe_test.yaml")
    differing = {k for k in set(v3) | set(safe) if v3.get(k) != safe.get(k)}
    assert differing == {"experiment", "pipeline"}
    v3_pipeline = dict(v3["pipeline"])
    safe_pipeline = dict(safe["pipeline"])
    v3_selection = dict(v3_pipeline.pop("selection"))
    safe_selection = dict(safe_pipeline.pop("selection"))
    assert v3_pipeline == safe_pipeline
    assert safe_selection.pop("v3_1")["enabled"] is True
    assert safe_selection == v3_selection


def test_safe_config_enables_only_safe_features():
    safe = _load("cover_kbc_v3_1_safe_test.yaml")
    block = V31Config.from_mapping(safe["pipeline"]["selection"]["v3_1"])
    assert not block.aggressive.any_enabled
    for feature in block.safe.enabled_features:
        assert compatibility_of(feature) == SAFE


# --------------------------------------------------------------------------
# 9. Class B prompts
# --------------------------------------------------------------------------


def test_prompts_are_versioned_and_hashed():
    inventory = prompt_inventory()
    assert len(inventory) == len(INSTRUCTIONS)
    for entry in inventory:
        assert len(entry["sha256"]) == 64
        assert entry["prompt_version"] == "v3.1-prompts-v1"


def test_no_prompt_targets_the_frozen_border_relation():
    from cover_kbc.v3_1.prompts import instruction_for
    assert instruction_for("countryLandBordersCountry") is None
    assert all(i.relation != "countryLandBordersCountry" for i in INSTRUCTIONS)


def test_prompts_state_relation_semantics_not_answers():
    """No instruction may name a benchmark subject or embed an answer."""
    banned = re.compile(
        r"\b(Wembley|Camp Nou|Maracan|Nobel|Fields Medal|Ballon|Wellington Island|"
        r"Mangareva|Molokai|Nasdaq is|Tokyo Stock Exchange is)\b", re.IGNORECASE)
    for instruction in INSTRUCTIONS:
        assert not banned.search(instruction.body), instruction.relation
        assert not re.search(r"\b\d{4,}\s*(seats|spectators|km2)\b", instruction.body)


def test_prompt_features_are_all_classified_as_calibration_shifting():
    for instruction in INSTRUCTIONS:
        assert compatibility_of(instruction.feature) == "CALIBRATION_REVIEW_REQUIRED"
