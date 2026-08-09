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
from cover_kbc.contracts.relation_profile import (
    RELATION_PROFILES,
    RelationProfile,
    UnknownRelationProfileError,
    all_relation_profiles,
    check_profile_consistency,
    get_relation_profile,
)
from cover_kbc.contracts.router import (
    PROGRAM_BY_RELATION,
    check_router_consistency,
    compile_query,
    route,
    route_profile,
)

__all__ = [
    "CONTRACTS",
    "PROGRAM_BY_RELATION",
    "RELATION_PROFILES",
    "RelationContract",
    "RelationProfile",
    "SelectionPolicy",
    "StoppingPolicy",
    "UnknownRelationError",
    "UnknownRelationProfileError",
    "VerificationPolicy",
    "all_contracts",
    "all_relation_profiles",
    "check_profile_consistency",
    "check_router_consistency",
    "compile_query",
    "eligible_groups_for",
    "get_contract",
    "get_relation_profile",
    "route",
    "route_profile",
]
