"""Module 0/1 - relation contracts and the typed program router."""

from cover_kbc.contracts.base import (
    RelationContract,
    SelectionPolicy,
    StoppingPolicy,
    VerificationPolicy,
    eligible_groups_for,
)
from cover_kbc.contracts.registry import (
    CONTRACTS,
    UnknownRelationError,
    all_contracts,
    get_contract,
)
from cover_kbc.contracts.router import (
    PROGRAM_BY_RELATION,
    check_router_consistency,
    compile_query,
    route,
)

__all__ = [
    "CONTRACTS",
    "PROGRAM_BY_RELATION",
    "RelationContract",
    "SelectionPolicy",
    "StoppingPolicy",
    "UnknownRelationError",
    "VerificationPolicy",
    "all_contracts",
    "check_router_consistency",
    "compile_query",
    "eligible_groups_for",
    "get_contract",
    "route",
]
