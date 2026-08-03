"""Module 2 - the Diverse Elicitation Engine."""

from cover_kbc.elicitation.engine import ElicitationEngine, ViewOutcome, prompt_hash
from cover_kbc.elicitation.library import (
    VIEW_LIBRARY,
    check_library_covers_contracts,
    get_view,
    views_for,
)
from cover_kbc.elicitation.parsing import (
    GateVerdict,
    parse_entities,
    parse_gate,
    parse_numeric_values,
)
from cover_kbc.elicitation.views import FAMILY_TO_GROUP, SYSTEM_PROMPT, ViewSpec

__all__ = [
    "ElicitationEngine",
    "FAMILY_TO_GROUP",
    "GateVerdict",
    "SYSTEM_PROMPT",
    "VIEW_LIBRARY",
    "ViewOutcome",
    "ViewSpec",
    "check_library_covers_contracts",
    "get_view",
    "parse_entities",
    "parse_gate",
    "parse_numeric_values",
    "prompt_hash",
    "views_for",
]
