"""Module 18's check registry - which relation supports which §14 mechanism.

**One declarative table.** No prompt code branches on a relation name; the
renderer, the catalogue and the executor all read this file, and a test scans
the others for relation names.

Two rules govern what may be declared here.

**Counterfactual classes come from Module 0, never from this file.** §14: the
near-miss class is "generated from the contract, not from external facts". A
class *is* one of `contract.hard_negative_rules`, addressed by index and
rendered verbatim. Nothing here writes a near-miss description, so nothing here
can invent a factual alternative.

**Reverse is declared, not assumed.** §14 says "when the relation supports a
meaningful reverse question", which is not every relation. Physical land
contact is symmetric, so a border candidate can be asked about in the reverse
direction with the same contract. Asking an exchange to list its companies or
an award to list its recipients would be uncontrolled open-set acquisition
wearing a verification label, so those relations declare no reverse.

The registry encodes verification **structure**. It encodes no external fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.types import OutputType
from cover_kbc.verification.bidirectional_types import (
    BidirectionalCheckKind,
    CheckTargetKind,
    UnsupportedCheckRelation,
)
from cover_kbc.verification.specialist_contracts import specialist_family
from cover_kbc.verification.specialist_types import SpecialistVerifierFamily

#: Bumped when a frame, a declared mechanism or a target kind changes.
CHECK_CONTRACT_VERSION = "m18-contract-v1"

#: How many hard-negative rules each relation declares. Pinned so a reordering
#: or an addition in Module 0 fails loudly here rather than silently shifting
#: every counterfactual class id.
EXPECTED_HARD_NEGATIVES: dict[str, int] = {
    "countryLandBordersCountry": 6,
    "personHasCityOfDeath": 5,
    "hasCapacity": 5,
    "awardWonBy": 5,
    "companyTradesAtStockExchange": 5,
    "hasArea": 5,
}


@dataclass(frozen=True)
class CheckProfile:
    """Which §14 mechanisms one relation supports, and how each is framed.

    ``*_frame`` strings are the **structural instruction** for a mechanism -
    what to reconstruct, what to recall - and never a statement about a
    particular candidate.
    """

    relation: str
    family: SpecialistVerifierFamily
    target_kind: CheckTargetKind
    supported_checks: tuple[BidirectionalCheckKind, ...]

    #: §14 reverse: the framing used when the relation is reversible.
    reverse_frame: str = ""
    reverse_rationale: str = ""

    #: §14 key-condition: what is masked, and what the model must recover.
    key_condition_frame: str = ""
    #: What a recovered value is: an entity name or a quantity.
    reconstruction_output: str = "entity"

    #: §14 candidate-free: a bounded relation-focused recall objective.
    candidate_free_frame: str = ""
    candidate_free_output: str = "entity_list"

    rationale: str = ""

    def supports(self, kind: BidirectionalCheckKind) -> bool:
        return kind in self.supported_checks

    def counterfactual_classes(self, contract: RelationContract) -> tuple[str, ...]:
        """The contract's own hard-negative rules, as class ids."""
        return tuple(f"hn{index}" for index in range(len(contract.hard_negative_rules)))

    @staticmethod
    def class_text(contract: RelationContract, class_id: str) -> str:
        """The rule text for one class id. Module 0's words, verbatim."""
        if not class_id.startswith("hn"):
            raise UnsupportedCheckRelation(
                f"counterfactual class {class_id!r} is not a contract rule id"
            )
        try:
            index = int(class_id[2:])
            return contract.hard_negative_rules[index]
        except (ValueError, IndexError) as exc:
            raise UnsupportedCheckRelation(
                f"{contract.relation} declares no counterfactual class "
                f"{class_id!r}; it has {len(contract.hard_negative_rules)} rules"
            ) from exc

    def to_json(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "family": self.family.value,
            "target_kind": self.target_kind.value,
            "supported_checks": [c.value for c in self.supported_checks],
            "reverse_supported": BidirectionalCheckKind.REVERSE in self.supported_checks,
            "reverse_rationale": self.reverse_rationale,
            "reconstruction_output": self.reconstruction_output,
            "candidate_free_output": self.candidate_free_output,
            "contract_version": CHECK_CONTRACT_VERSION,
        }


_NO_REVERSE = (
    BidirectionalCheckKind.KEY_CONDITION,
    BidirectionalCheckKind.COUNTERFACTUAL,
    BidirectionalCheckKind.CANDIDATE_FREE_RECALL,
)

BORDER_PROFILE = CheckProfile(
    relation="countryLandBordersCountry",
    family=SpecialistVerifierFamily.BORDER,
    target_kind=CheckTargetKind.ENTITY_CANDIDATE,
    supported_checks=(BidirectionalCheckKind.REVERSE, *_NO_REVERSE),
    reverse_frame=(
        "Answer from the candidate's side: treating the candidate as the "
        "subject of the same relation, does the original subject satisfy it?"
    ),
    reverse_rationale=(
        "§14's reverse check, and the only relation that supports it here: "
        "physical land contact is symmetric, so the same contract answers in "
        "both directions and the reverse question is a genuine second framing "
        "rather than a rewording."
    ),
    key_condition_frame=(
        "Name the countries that satisfy this relation for the subject in the "
        "compass direction given below."
    ),
    candidate_free_frame=(
        "Name the countries that satisfy this relation for the subject."
    ),
    rationale=(
        "§11.1's minimal-change rule still holds: Module 15 requests a reverse "
        "check only for a singleton or a territory ambiguity, and Module 18 "
        "runs it only when a caller asks."
    ),
)

STOCK_PROFILE = CheckProfile(
    relation="companyTradesAtStockExchange",
    family=SpecialistVerifierFamily.STOCK,
    target_kind=CheckTargetKind.ENTITY_CANDIDATE,
    supported_checks=_NO_REVERSE,
    reverse_rationale=(
        "No reverse. Asking an exchange to list the companies trading on it is "
        "unbounded open-set acquisition, not a targeted check of one claim - "
        "and §14 makes reverse conditional on the relation supporting a "
        "meaningful reverse question."
    ),
    key_condition_frame=(
        "Name the exchanges on which the subject company's own shares are "
        "listed."
    ),
    candidate_free_frame=(
        "Name the exchanges on which the subject company's own shares are "
        "listed."
    ),
    rationale=(
        "Table 5's 'company itself' is the decisive condition, so it is what "
        "the reconstruction asks the model to recover. Module 15's public-"
        "listing gate is neither rerun nor re-read here."
    ),
)

AWARD_PROFILE = CheckProfile(
    relation="awardWonBy",
    family=SpecialistVerifierFamily.AWARD_MEMBERSHIP,
    target_kind=CheckTargetKind.ENTITY_CANDIDATE,
    supported_checks=_NO_REVERSE,
    reverse_rationale=(
        "No reverse. 'Which awards did this recipient win?' is a different "
        "relation over an unbounded set, and answering it would acquire rather "
        "than check."
    ),
    key_condition_frame="Name the entities that received this exact award.",
    candidate_free_frame="Name the entities that received this exact award.",
    rationale=(
        "Table 5's decisive condition is receipt of this exact award, so both "
        "the reconstruction and the candidate-free probe ask for recipients "
        "and neither shows a candidate to agree with."
    ),
)

DEATH_PROFILE = CheckProfile(
    relation="personHasCityOfDeath",
    family=SpecialistVerifierFamily.NULL_TEMPORAL,
    target_kind=CheckTargetKind.ENTITY_CANDIDATE,
    supported_checks=_NO_REVERSE,
    reverse_rationale=(
        "No reverse. 'Who died in this city?' is an unbounded enumeration and "
        "says nothing about the subject."
    ),
    key_condition_frame=(
        "Name the locality that satisfies this relation for the subject, or "
        "answer exactly NONE if the relation has no object for the subject."
    ),
    candidate_free_frame=(
        "Name the locality that satisfies this relation for the subject, or "
        "answer exactly NONE if the relation has no object for the subject."
    ),
    rationale=(
        "The decisive condition is the locality itself, under the existence "
        "conditions the contract states. Audit 0024's abstention semantics "
        "govern the parsing of both frames."
    ),
)

NUMERIC_PROFILES = tuple(
    CheckProfile(
        relation=relation,
        family=SpecialistVerifierFamily.NUMERIC,
        target_kind=CheckTargetKind.NUMERIC_CLUSTER,
        supported_checks=_NO_REVERSE,
        reverse_rationale=(
            "No reverse. A quantity has no subject to ask about in the other "
            "direction."
        ),
        key_condition_frame=(
            "State the quantity this relation defines for the subject, as a "
            "single number with its unit."
        ),
        reconstruction_output="quantity",
        candidate_free_frame=(
            "State the quantity this relation defines for the subject, as a "
            "single number with its unit."
        ),
        candidate_free_output="quantity",
        rationale=(
            "Table 5's decisive condition is the exact quantity definition, so "
            "the reconstruction asks for the quantity and the recovered number "
            "is read through Module 12's own canonicalisation - never a second "
            "clustering rule."
        ),
    )
    for relation in ("hasCapacity", "hasArea")
)

CHECK_PROFILES: tuple[CheckProfile, ...] = (
    BORDER_PROFILE, STOCK_PROFILE, AWARD_PROFILE, DEATH_PROFILE, *NUMERIC_PROFILES,
)

_BY_RELATION: dict[str, CheckProfile] = {p.relation: p for p in CHECK_PROFILES}


def check_profile(relation: str) -> CheckProfile:
    """The one check profile for a relation. Fails closed."""
    try:
        return _BY_RELATION[relation]
    except KeyError as exc:
        raise UnsupportedCheckRelation(
            f"Module 18 declares no check profile for {relation!r}; it covers "
            f"{sorted(_BY_RELATION)}"
        ) from exc


def supports_reverse(relation: str) -> bool:
    return check_profile(relation).supports(BidirectionalCheckKind.REVERSE)


def counterfactual_class_text(relation: str, class_id: str) -> str:
    """Module 0's own rule text for one counterfactual class."""
    return CheckProfile.class_text(CONTRACTS[relation], class_id)


def check_registry_consistency() -> None:
    """Cross-check the profiles against Modules 0, 1 and 17."""
    problems: list[str] = []

    missing = set(CONTRACTS) - set(_BY_RELATION)
    if missing:
        problems.append(f"relations with no Module 18 profile: {sorted(missing)}")
    extra = set(_BY_RELATION) - set(CONTRACTS)
    if extra:
        problems.append(f"profiles for unknown relations: {sorted(extra)}")

    for relation, profile in sorted(_BY_RELATION.items()):
        contract = CONTRACTS.get(relation)
        if contract is None:
            continue
        if profile.family is not specialist_family(relation):
            problems.append(
                f"{relation}: profile claims {profile.family.value} but Module 17 "
                f"routes it to {specialist_family(relation).value}"
            )
        numeric = contract.output_type is OutputType.NUMBER
        expected_kind = (
            CheckTargetKind.NUMERIC_CLUSTER if numeric
            else CheckTargetKind.ENTITY_CANDIDATE
        )
        if profile.target_kind is not expected_kind:
            problems.append(
                f"{relation}: target kind {profile.target_kind.value} does not "
                f"match the contract's output type"
            )
        if not contract.hard_negative_rules:
            problems.append(
                f"{relation}: §14 counterfactuals need contract hard negatives "
                "and this contract declares none"
            )
        expected_rules = EXPECTED_HARD_NEGATIVES.get(relation)
        if expected_rules is not None and len(contract.hard_negative_rules) != expected_rules:
            problems.append(
                f"{relation}: expected {expected_rules} hard-negative rules but "
                f"the contract now declares {len(contract.hard_negative_rules)}; "
                "counterfactual class ids are positional and would shift"
            )
        if BidirectionalCheckKind.COUNTERFACTUAL not in profile.supported_checks:
            problems.append(f"{relation}: every relation supports a counterfactual")
        if BidirectionalCheckKind.CANDIDATE_FREE_RECALL not in profile.supported_checks:
            problems.append(f"{relation}: §14's candidate-free probe is unconditional")
        if profile.supports(BidirectionalCheckKind.REVERSE) and not profile.reverse_frame:
            problems.append(f"{relation}: declares reverse with no framing")
        if not profile.supports(BidirectionalCheckKind.REVERSE) and profile.reverse_frame:
            problems.append(f"{relation}: declares a reverse framing it cannot use")
        if not profile.reverse_rationale:
            problems.append(
                f"{relation}: §14 makes reverse conditional, so the decision "
                "needs a recorded rationale either way"
            )
        if not profile.key_condition_frame or not profile.candidate_free_frame:
            problems.append(f"{relation}: a declared mechanism has no frame")
        if not profile.rationale:
            problems.append(f"{relation}: no recorded rationale")
        if len(set(profile.supported_checks)) != len(profile.supported_checks):
            problems.append(f"{relation}: duplicate mechanism declared")

    reversible = sorted(r for r, p in _BY_RELATION.items() if p.supports(
        BidirectionalCheckKind.REVERSE
    ))
    if reversible != ["countryLandBordersCountry"]:
        problems.append(
            "§14 supports reverse only where the relation makes it meaningful; "
            f"got {reversible}"
        )

    if problems:
        raise ValueError(
            "Module 18 check registry is inconsistent:\n  - " + "\n  - ".join(problems)
        )


def registry_snapshot() -> list[Mapping[str, Any]]:
    return [profile.to_json() for profile in CHECK_PROFILES]


check_registry_consistency()


__all__ = [
    "AWARD_PROFILE",
    "BORDER_PROFILE",
    "CHECK_CONTRACT_VERSION",
    "CHECK_PROFILES",
    "DEATH_PROFILE",
    "EXPECTED_HARD_NEGATIVES",
    "NUMERIC_PROFILES",
    "STOCK_PROFILE",
    "CheckProfile",
    "check_profile",
    "check_registry_consistency",
    "counterfactual_class_text",
    "registry_snapshot",
    "supports_reverse",
]
