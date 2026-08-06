"""Module 17's specialist verification contracts - proposal §13, Table 5.

**One declarative registry.** Every relation maps to exactly one specialist
family here, and nothing downstream branches on a relation name: the prompt
renderer, the verifier and the target catalogue all read this table. A
``if relation == ...`` in prompt code is what this file exists to prevent, and
a test scans for it.

**Authoritative semantics come from Module 0.** A specialist contract does not
restate what a relation means - `contract.verifier_definition()` already
carries the definition, the answer type, the positive rules and the
hard-negative rules, and that is what the prompt embeds. What §13 adds, and all
this registry declares, is the **verification question frame**: Table 5's
"verifier question" and the boundary sentence naming the hard-negative *classes*
generically.

The distinction matters for blindness. "Does the candidate satisfy the exact
quantity definition, as opposed to attendance or a different configuration?" is
the contract speaking. "The generator thinks this one is attendance" would be
the acquisition layer speaking, and it never appears - see
:mod:`cover_kbc.verification.specialist_prompts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.types import OutputType, ProgramType
from cover_kbc.verification.specialist_types import (
    QueryPropositionKind,
    SpecialistVerifierFamily,
    UnsupportedSpecialistRelation,
    VerificationTargetKind,
)

#: Bumped when a question frame or boundary changes, so a persisted result can
#: never be read under the wrong contract.
SPECIALIST_CONTRACT_VERSION = "m17-contract-v1"


@dataclass(frozen=True)
class SpecialistVerifierContract:
    """Table 5's row for one specialist family.

    ``question`` and ``boundary`` are the only prose M17 owns. Everything else
    the verifier sees is Module 0's contract text, rendered by Module 4's own
    ``verifier_definition()``.
    """

    family: SpecialistVerifierFamily
    relations: tuple[str, ...]
    #: Table 5 "Verifier question", as a second-person instruction.
    question: str
    #: Table 5 "Typical hard negative", stated as a general class boundary -
    #: never as a claim about the candidate in front of the verifier.
    boundary: str
    #: Which target kinds this family can pose as a blind question.
    target_kinds: tuple[VerificationTargetKind, ...]
    #: Query-level propositions this family supports, if any (§10.3).
    propositions: tuple[QueryPropositionKind, ...] = ()
    contract_version: str = SPECIALIST_CONTRACT_VERSION
    rationale: str = ""

    def supports(self, kind: VerificationTargetKind) -> bool:
        return kind in self.target_kinds

    def to_json(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "relations": list(self.relations),
            "question": self.question,
            "boundary": self.boundary,
            "target_kinds": [k.value for k in self.target_kinds],
            "propositions": [p.value for p in self.propositions],
            "contract_version": self.contract_version,
        }


NUMERIC_CONTRACT = SpecialistVerifierContract(
    family=SpecialistVerifierFamily.NUMERIC,
    relations=("hasCapacity", "hasArea"),
    question=(
        "Does the value satisfy the exact quantity the definition asks for, "
        "for this subject?"
    ),
    boundary=(
        "Answer B if the value denotes a different quantity than the one "
        "defined - a related measurement, a different configuration or period, "
        "or a different scope than the definition's - even when the number "
        "itself is a real figure for the subject."
    ),
    target_kinds=(VerificationTargetKind.NUMERIC_CLUSTER,),
    rationale=(
        "Table 5: 'Does the candidate satisfy the exact quantity definition?' "
        "with 'attendance vs capacity; land vs total area' as the typical hard "
        "negative. Both are quantity-scope confusions, and the contract's own "
        "hard-negative rules already name them for each relation, so the "
        "boundary above stays general."
    ),
)

AWARD_CONTRACT = SpecialistVerifierContract(
    family=SpecialistVerifierFamily.AWARD_MEMBERSHIP,
    relations=("awardWonBy",),
    question="Is the candidate a recipient of this exact award?",
    boundary=(
        "Answer B if the candidate did not itself receive this exact award - "
        "for example if it was only considered for it, if it is a work or "
        "contribution rather than the recipient, if it received a different "
        "award however similar, or if the award to it does not stand."
    ),
    target_kinds=(VerificationTargetKind.ENTITY_CANDIDATE,),
    rationale=(
        "Table 5: 'Is the candidate a recipient of this exact award?' with "
        "'nominee, work, similar award, rescinded' as the typical hard "
        "negatives - the four clauses of the boundary above, in order."
    ),
)

NULL_TEMPORAL_CONTRACT = SpecialistVerifierContract(
    family=SpecialistVerifierFamily.NULL_TEMPORAL,
    relations=("personHasCityOfDeath",),
    question=(
        "Do the existence, death-status and locality conditions in the "
        "definition hold for the candidate?"
    ),
    boundary=(
        "Answer B if the candidate is not the locality the definition asks "
        "for - a place associated with the subject for another reason, or a "
        "place of the wrong kind or granularity - or if the subject's status "
        "makes the relation inapplicable."
    ),
    target_kinds=(
        VerificationTargetKind.ENTITY_CANDIDATE,
        VerificationTargetKind.QUERY_PROPOSITION,
    ),
    propositions=(
        QueryPropositionKind.SUBJECT_IS_LIVING,
        QueryPropositionKind.SUBJECT_IS_DECEASED,
        QueryPropositionKind.NO_KNOWN_QUALIFYING_LOCALITY,
    ),
    rationale=(
        "Table 5: 'Are existence/death status/locality conditions consistent?' "
        "with 'living, birthplace, residence, country' as the typical hard "
        "negatives. The first is a *query-level* condition and the rest are "
        "candidate-level, which is why this family alone declares both target "
        "kinds - conflating them would undo §10.3's separation."
    ),
)

STOCK_CONTRACT = SpecialistVerifierContract(
    family=SpecialistVerifierFamily.STOCK,
    relations=("companyTradesAtStockExchange",),
    question=(
        "Is the subject company itself listed on the candidate exchange, as "
        "the definition requires?"
    ),
    boundary=(
        "Answer B if the listing is not the subject company's own current "
        "listing on that exchange - for example a related company's listing, "
        "an index or other non-exchange venue, a listing that has ended, or a "
        "company that is not publicly listed at all."
    ),
    target_kinds=(VerificationTargetKind.ENTITY_CANDIDATE,),
    rationale=(
        "Table 5: 'Company itself currently/contractually listed on exchange?' "
        "with 'parent/subsidiary/index/private/delisted' as the typical hard "
        "negatives. 'Company itself' is the load-bearing phrase and leads the "
        "question."
    ),
)

BORDER_CONTRACT = SpecialistVerifierContract(
    family=SpecialistVerifierFamily.BORDER,
    relations=("countryLandBordersCountry",),
    question=(
        "Is there physical land contact between the subject and the candidate, "
        "as the definition requires?"
    ),
    boundary=(
        "Answer B if there is no qualifying land contact - for example a "
        "sea boundary only, proximity without contact, or contact only through "
        "a territory the definition excludes."
    ),
    target_kinds=(VerificationTargetKind.ENTITY_CANDIDATE,),
    rationale=(
        "Table 5: 'Physical land contact?' with 'maritime-only/nearby "
        "territory' as the typical hard negatives. §11.1's minimal-change rule "
        "applies to acquisition; M17 verifies a candidate only when the caller "
        "asks, and never fans out."
    ),
)

SPECIALIST_CONTRACTS: tuple[SpecialistVerifierContract, ...] = (
    NUMERIC_CONTRACT,
    AWARD_CONTRACT,
    NULL_TEMPORAL_CONTRACT,
    STOCK_CONTRACT,
    BORDER_CONTRACT,
)

_BY_RELATION: dict[str, SpecialistVerifierContract] = {
    relation: contract
    for contract in SPECIALIST_CONTRACTS
    for relation in contract.relations
}

_BY_FAMILY: dict[SpecialistVerifierFamily, SpecialistVerifierContract] = {
    contract.family: contract for contract in SPECIALIST_CONTRACTS
}


def specialist_contract(relation: str) -> SpecialistVerifierContract:
    """The one specialist contract for a relation. Fails closed."""
    try:
        return _BY_RELATION[relation]
    except KeyError as exc:
        raise UnsupportedSpecialistRelation(
            f"Module 17 declares no specialist verification contract for "
            f"{relation!r}; Table 5 covers {sorted(_BY_RELATION)}"
        ) from exc


def specialist_family(relation: str) -> SpecialistVerifierFamily:
    return specialist_contract(relation).family


def contract_for_family(
    family: SpecialistVerifierFamily,
) -> SpecialistVerifierContract:
    return _BY_FAMILY[family]


def check_specialist_registry_consistency() -> None:
    """Cross-check Table 5 against Modules 0 and 1. Raises listing every problem."""
    problems: list[str] = []

    missing = set(CONTRACTS) - set(_BY_RELATION)
    if missing:
        problems.append(
            f"official relations with no specialist verifier contract: {sorted(missing)}"
        )
    extra = set(_BY_RELATION) - set(CONTRACTS)
    if extra:
        problems.append(f"specialist contracts for unknown relations: {sorted(extra)}")

    if len(SPECIALIST_CONTRACTS) != 5:
        problems.append(
            f"Table 5 declares five specialist families; found "
            f"{len(SPECIALIST_CONTRACTS)}"
        )
    if len(_BY_FAMILY) != len(SPECIALIST_CONTRACTS):
        problems.append("two contracts declare the same specialist family")

    for contract in SPECIALIST_CONTRACTS:
        if not contract.question:
            problems.append(f"{contract.family.value}: Table 5 requires a question")
        if not contract.boundary:
            problems.append(f"{contract.family.value}: no hard-negative boundary")
        if not contract.target_kinds:
            problems.append(f"{contract.family.value}: no verifiable target kind")
        if not contract.rationale:
            problems.append(f"{contract.family.value}: no recorded rationale")

        for relation in contract.relations:
            relation_contract = CONTRACTS.get(relation)
            if relation_contract is None:
                continue
            numeric = relation_contract.output_type is OutputType.NUMBER
            if numeric and contract.family is not SpecialistVerifierFamily.NUMERIC:
                problems.append(
                    f"{relation}: a NUMBER relation must route to the numeric "
                    f"specialist, not {contract.family.value}"
                )
            if not numeric and contract.family is SpecialistVerifierFamily.NUMERIC:
                problems.append(
                    f"{relation}: an entity relation cannot route to the numeric "
                    "specialist"
                )
            if numeric and VerificationTargetKind.ENTITY_CANDIDATE in contract.target_kinds:
                problems.append(
                    f"{relation}: a numeric relation has no entity candidates to "
                    "verify; use NUMERIC_CLUSTER"
                )
            expects_null = relation_contract.program_type is ProgramType.NULL_SINGLE
            if expects_null and not contract.propositions:
                problems.append(
                    f"{relation}: §10.3 needs query-level propositions and this "
                    "contract declares none"
                )
            if contract.propositions and not expects_null:
                problems.append(
                    f"{relation}: query-level propositions are declared for a "
                    "relation Module 1 does not route to NULL_SINGLE"
                )
        if contract.propositions and (
            VerificationTargetKind.QUERY_PROPOSITION not in contract.target_kinds
        ):
            problems.append(
                f"{contract.family.value}: declares propositions but not the "
                "QUERY_PROPOSITION target kind"
            )

    if problems:
        raise ValueError(
            "Module 17 specialist contract registry is inconsistent:\n  - "
            + "\n  - ".join(problems)
        )


def registry_snapshot() -> list[Mapping[str, Any]]:
    """The whole table, for a run trace."""
    return [contract.to_json() for contract in SPECIALIST_CONTRACTS]


check_specialist_registry_consistency()


__all__ = [
    "AWARD_CONTRACT",
    "BORDER_CONTRACT",
    "NULL_TEMPORAL_CONTRACT",
    "NUMERIC_CONTRACT",
    "SPECIALIST_CONTRACTS",
    "SPECIALIST_CONTRACT_VERSION",
    "STOCK_CONTRACT",
    "SpecialistVerifierContract",
    "check_specialist_registry_consistency",
    "contract_for_family",
    "registry_snapshot",
    "specialist_contract",
    "specialist_family",
]
