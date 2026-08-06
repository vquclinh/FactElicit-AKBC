"""The mode contract must fail closed.

An unrecognised mode that silently became ``shadow`` would disable a production
seam the operator meant to enable, and the run would still look successful -
that is the failure these tests exist to prevent.
"""

from __future__ import annotations

import pytest

from cover_kbc.integration_mode import (
    IntegrationMode,
    IntegrationModeError,
    parse_mode,
)


def test_exactly_two_modes_exist() -> None:
    assert [mode.value for mode in IntegrationMode] == ["shadow", "production"]


@pytest.mark.parametrize("value,expected", [
    ("shadow", IntegrationMode.SHADOW),
    ("production", IntegrationMode.PRODUCTION),
])
def test_parses_the_legal_modes(value: str, expected: IntegrationMode) -> None:
    assert parse_mode(value, module="m16") is expected


def test_accepts_an_already_parsed_mode() -> None:
    assert parse_mode(IntegrationMode.PRODUCTION, module="m16") is IntegrationMode.PRODUCTION


@pytest.mark.parametrize("value", ["prod", "PRODUCTION", "Shadow", "", "live", "enabled"])
def test_refuses_anything_else_rather_than_defaulting(value: str) -> None:
    """A near-miss must raise, not fall back to the permissive or safe option."""
    with pytest.raises(IntegrationModeError, match="unsupported mode"):
        parse_mode(value, module="m17")


@pytest.mark.parametrize("value", [None, True, 1, 0, ["shadow"]])
def test_refuses_non_string_modes(value: object) -> None:
    with pytest.raises(IntegrationModeError, match="must be a string"):
        parse_mode(value, module="m18")


def test_the_error_names_the_module() -> None:
    with pytest.raises(IntegrationModeError, match="m21"):
        parse_mode("whatever", module="m21")


def test_shadow_may_not_reach_production_state() -> None:
    mode = IntegrationMode.SHADOW
    assert mode.is_shadow and not mode.is_production
    assert not mode.may_mutate_production_state
    assert not mode.charges_production_budget


def test_production_may_reach_production_state() -> None:
    mode = IntegrationMode.PRODUCTION
    assert mode.is_production and not mode.is_shadow
    assert mode.may_mutate_production_state
    assert mode.charges_production_budget


def test_permissions_never_overlap() -> None:
    """No mode may both keep shadow isolation and mutate production state."""
    for mode in IntegrationMode:
        assert mode.may_mutate_production_state is not mode.is_shadow
        assert mode.charges_production_budget is not mode.is_shadow
