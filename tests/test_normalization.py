"""String and numeric normalisation, and output parsing."""

from __future__ import annotations

import pytest

from cover_kbc.elicitation.parsing import parse_entities, parse_gate, parse_numeric_values
from cover_kbc.evaluation.official import official_try_parse_number
from cover_kbc.normalization.numeric import (
    cluster_values,
    dominant_cluster,
    format_numeric,
    parse_number_token,
    parse_numbers,
    relative_distance,
)
from cover_kbc.normalization.strings import (
    canonical_key,
    clean_surface,
    collapse_exact_duplicates,
    is_abstain,
    preferred_surface_form,
)


# --- numeric parsing -------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("5556", 5556.0),
        ("5,556", 5556.0),          # en thousands
        ("1,234.5", 1234.5),        # en thousands + decimal
        ("1.234,5", 1234.5),        # de decimal comma
        ("1.234.567", 1234567.0),   # de thousands
        ("1.234", 1.234),           # a single dot stays a decimal
        ("12'345", 12345.0),        # ch apostrophe
        ("not a number", None),
        ("", None),
    ],
)
def test_parse_number_token(token, expected):
    assert parse_number_token(token) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("35k", 35000.0),
        ("1.5 million", 1500000.0),
        ("2 billion", 2e9),
    ],
)
def test_magnitude_words_scale_the_value(text, expected):
    assert parse_numbers(text)[0].value == expected


def test_unit_digits_are_not_read_as_a_second_number():
    """The "2" in "km2" belongs to the unit."""
    values = parse_numbers("5556 km2")
    assert [v.value for v in values] == [5556.0]
    assert values[0].unit == "km2"


def test_area_units_convert_to_km2(area_contract):
    assert parse_numeric_values("5556 km2", area_contract) == pytest.approx([5556.0])
    assert parse_numeric_values("2145 square miles", area_contract) == pytest.approx(
        [5555.52], rel=1e-4
    )
    assert parse_numeric_values("500 hectares", area_contract) == pytest.approx([5.0])


def test_capacity_rejects_area_units(capacity_contract):
    """A person count measured in square kilometres is a type error."""
    assert parse_numeric_values("5000 km2", capacity_contract) == []
    assert parse_numeric_values("35,000 seats", capacity_contract) == pytest.approx([35000.0])


def test_numeric_parsing_of_malformed_output_yields_nothing(area_contract):
    for text in ["UNKNOWN", "", "I do not know.", "somewhere in Europe"]:
        assert parse_numeric_values(text, area_contract) == []


# --- clustering and formatting --------------------------------------------


def test_relative_distance():
    assert relative_distance(100, 100) == 0.0
    assert relative_distance(100, 105) == pytest.approx(0.047619, rel=1e-4)


def test_dominant_cluster_picks_the_largest_group():
    cluster = dominant_cluster([5000, 5050, 4990, 12000])
    assert cluster.size == 3
    assert cluster.representative == 5000


def test_clustering_is_order_independent():
    a = cluster_values([5000, 12000, 5050, 4990])
    b = cluster_values([12000, 4990, 5050, 5000])
    assert [c.values for c in a] == [c.values for c in b]


def test_formatted_output_survives_the_official_parser():
    """Anything we emit must parse, or it costs precision and can never match."""
    for value in [5556.0, 1294.994, 3.7, 35000.0, 0.5]:
        text = format_numeric(value)
        assert official_try_parse_number(text) is not None


def test_format_numeric_integer_only():
    assert format_numeric(34999.6, integer_only=True) == "35000"


# --- string normalisation --------------------------------------------------


def test_leading_articles_collapse_into_one_key():
    assert canonical_key("The Alpha Exchange") == canonical_key("Alpha Exchange")


def test_parentheticals_are_dropped_from_the_key():
    assert canonical_key("Alpha Exchange (AE)") == canonical_key("Alpha Exchange")


def test_list_markers_are_stripped():
    assert clean_surface("- Alpha") == "Alpha"
    assert clean_surface("1. Alpha") == "Alpha"
    assert clean_surface('"Alpha"') == "Alpha"


@pytest.mark.parametrize(
    "text", ["none", "NONE", "N/A", "unknown", "I don't know", "not applicable", ""]
)
def test_abstain_tokens_are_recognised(text):
    assert is_abstain(text)


@pytest.mark.parametrize("text", ["Alpha", "None of Your Business Records Ltd"])
def test_real_names_are_not_abstentions(text):
    assert not is_abstain(text)


def test_preferred_surface_form_is_deterministic():
    surfaces = ["Alpha Exchange", "alpha exchange", "Alpha Exchange"]
    assert preferred_surface_form(surfaces) == "Alpha Exchange"


def test_collapse_exact_duplicates_keeps_one_form_per_entity():
    """One form per entity survives; the preferred form is the shorter one here."""
    assert collapse_exact_duplicates(
        ["The Alpha Exchange", "Alpha Exchange", "Beta Exchange", "NONE"]
    ) == ["Alpha Exchange", "Beta Exchange"]


def test_collapse_prefers_the_most_frequently_generated_form():
    assert collapse_exact_duplicates(
        ["The Alpha Exchange", "The Alpha Exchange", "Alpha Exchange"]
    ) == ["The Alpha Exchange"]


@pytest.mark.parametrize(
    "text",
    [
        "I'm sorry, I cannot help with that.",
        "As an AI, I do not have that information.",
        "I am unable to answer this question.",
    ],
)
def test_refusals_are_detected(text, borders_contract):
    from cover_kbc.normalization.strings import is_refusal

    assert is_refusal(text)
    assert parse_entities(text, borders_contract) == []


def test_code_fences_are_stripped(borders_contract):
    assert parse_entities('```json\n["Alpha", "Beta"]\n```', borders_contract) == ["Alpha", "Beta"]


def test_punctuation_debris_is_dropped(borders_contract):
    assert parse_entities("{{{ ]]] ;; ---", borders_contract) == []


# --- entity parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Alpha; Beta; Gamma", ["Alpha", "Beta", "Gamma"]),
        ("Answer: Alpha, Beta", ["Alpha", "Beta"]),
        ("1. Alpha\n2. Beta", ["Alpha", "Beta"]),
        ("- Alpha\n- Beta", ["Alpha", "Beta"]),
        ('["Alpha", "Beta"]', ["Alpha", "Beta"]),
    ],
)
def test_entity_parsing_handles_common_shapes(text, expected, borders_contract):
    assert parse_entities(text, borders_contract) == expected


@pytest.mark.parametrize(
    "text",
    [
        "NONE",
        "",
        "I do not know.",
        "Testland is a landlocked country whose borders were established by treaty "
        "in the nineteenth century and have not changed since then.",
    ],
)
def test_malformed_or_abstaining_output_yields_no_entities(text, borders_contract):
    assert parse_entities(text, borders_contract) == []


def test_entity_parsing_never_raises_on_junk(borders_contract):
    for junk in [None, 123, "{{{", "\x00\x01", "[unclosed"]:
        assert isinstance(parse_entities(junk, borders_contract), list)


@pytest.mark.parametrize(
    "text,expected",
    [("YES", True), ("No.", False), ("UNKNOWN", None), ("maybe", None), ("", None)],
)
def test_gate_parsing(text, expected):
    assert parse_gate(text).value is expected
