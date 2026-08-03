"""Module 1 - the typed program router.

A deterministic relation -> program mapping.  No learned classifier is used or
needed: there are exactly six relation ids (spec section 6).

The router also cross-checks each contract against the official evaluator's own
``RELATION_TYPE`` map, so a snapshot update that reclassified a relation as
numeric would fail loudly here instead of silently producing wrong output.
"""

from __future__ import annotations

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.programs import check_program_compatibility, get_program
from cover_kbc.contracts.registry import CONTRACTS, get_contract
from cover_kbc.evaluation.official import relation_types
from cover_kbc.types import OutputType, ProgramType, Query

#: The mapping stated in spec Table 4.
PROGRAM_BY_RELATION: dict[str, ProgramType] = {
    "countryLandBordersCountry": ProgramType.SMALL_SET,
    "companyTradesAtStockExchange": ProgramType.SMALL_SET,
    "personHasCityOfDeath": ProgramType.NULL_SINGLE,
    "hasArea": ProgramType.NUMERIC,
    "hasCapacity": ProgramType.NUMERIC,
    "awardWonBy": ProgramType.LARGE_OPEN_SET,
}


def route(relation: str) -> ProgramType:
    """Return the typed programme for an official relation.

    Fails closed: an unknown relation raises ``UnknownRelationError`` rather
    than falling back to a default programme.
    """
    return get_contract(relation).program_type


def route_program(relation: str):
    """Return the executable programme regime for an official relation.

    This is what downstream modules should ask for when they need to know how
    to *treat* a relation, rather than what it means.
    """
    return get_program(get_contract(relation).program_type)


def compile_query(subject: str, relation: str, row_index: int = -1) -> tuple[Query, RelationContract]:
    """Module 0 + Module 1: compile ``(s, r)`` into a query and its contract."""
    return Query(subject=subject, relation=relation, row_index=row_index), get_contract(relation)


def check_router_consistency() -> None:
    """Verify contracts agree with the spec table and the official evaluator.

    Raises:
        ValueError: on any disagreement, listing every problem found.
    """
    problems: list[str] = []
    official = relation_types()

    missing = set(official) - set(CONTRACTS)
    if missing:
        problems.append(f"official relations without a contract: {sorted(missing)}")
    unexpected = set(CONTRACTS) - set(official)
    if unexpected:
        problems.append(f"contracts for relations the evaluator does not score: {sorted(unexpected)}")

    for relation, contract in sorted(CONTRACTS.items()):
        expected_program = PROGRAM_BY_RELATION.get(relation)
        if expected_program is None:
            problems.append(f"{relation}: not present in PROGRAM_BY_RELATION")
        elif contract.program_type is not expected_program:
            problems.append(
                f"{relation}: contract programme {contract.program_type.value} "
                f"!= routed programme {expected_program.value}"
            )

        official_type = official.get(relation)
        if official_type is None:
            continue
        expected_output = OutputType.NUMBER if official_type == "numeric" else OutputType.ENTITY
        if contract.output_type is not expected_output:
            problems.append(
                f"{relation}: contract output {contract.output_type.value} disagrees with "
                f"official RELATION_TYPE={official_type!r}"
            )

        problems.extend(check_program_compatibility(contract))

        try:
            contract.validate()
        except ValueError as exc:
            problems.append(str(exc))

    if problems:
        raise ValueError("Router/contract inconsistency:\n  - " + "\n  - ".join(problems))
