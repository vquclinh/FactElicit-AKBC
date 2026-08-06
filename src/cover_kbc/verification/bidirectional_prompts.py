"""Module 18 prompt rendering - four structurally different questions.

§14 asks for *new evidence*, so none of these may collapse into a rewording of
Module 17's A/B/C question:

| Mechanism | What the model is asked | Candidate visible? |
| --- | --- | --- |
| reverse | the same relation **from the candidate's side** | yes |
| key condition | to **recover** the masked decisive value | no target shown |
| counterfactual | to choose between the relation and one contract exclusion | yes |
| candidate-free | to recall objects, with **no candidate anywhere** | **no** |

**No generic self-correction.** §14 opens by contrasting M18 with "a generic
'think again' instruction". Nothing here says review, reconsider, are you sure,
double-check or reflect, and a test scans for all of them.

**No upstream suspicion.** A prompt may carry the contract's own exclusion
text - that is Module 0 speaking - but never what the acquisition layer
suspects about this candidate. "The relation excludes nominees" is a contract
rule; "Module 13 thinks this is a nominee" would tell the model which answer
the system wants.

**No chain of thought.** Every frame asks for a bounded structured answer,
because an explanation is not evidence and cannot be parsed deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.verification.bidirectional_contracts import (
    CheckProfile,
    counterfactual_class_text,
)
from cover_kbc.verification.bidirectional_types import (
    BidirectionalCheckError,
    BidirectionalCheckKind,
)

#: One system frame per mechanism. All closed-book, none asking for prose.
REVERSE_SYSTEM_PROMPT = (
    "You judge one relation claim from the direction stated in the question. "
    "Use only your internal parametric knowledge; you have no access to search "
    "or documents. Answer with exactly one of the listed words."
)
RECONSTRUCTION_SYSTEM_PROMPT = (
    "You answer one knowledge question from your own internal knowledge only. "
    "You have no access to search or documents. Give the answer in the exact "
    "form requested and add no commentary. If you do not know, answer exactly: "
    "UNKNOWN"
)
COUNTERFACTUAL_SYSTEM_PROMPT = (
    "You distinguish between two precisely defined relations. Use only your "
    "internal parametric knowledge; you have no access to search or documents. "
    "Answer with exactly one of the listed words and add no commentary."
)
CANDIDATE_FREE_SYSTEM_PROMPT = (
    "You answer knowledge-base questions from your own internal knowledge only. "
    "You have no access to search or documents. Answer with names only, one per "
    "line, and add no commentary. If there are none, answer exactly: NONE"
)

REVERSE_TEMPLATE_ID = "m18_reverse_v1"
KEY_CONDITION_TEMPLATE_ID = "m18_key_condition_v1"
COUNTERFACTUAL_TEMPLATE_ID = "m18_counterfactual_v1"
CANDIDATE_FREE_TEMPLATE_ID = "m18_candidate_free_v1"

TEMPLATE_IDS: Mapping[BidirectionalCheckKind, str] = {
    BidirectionalCheckKind.REVERSE: REVERSE_TEMPLATE_ID,
    BidirectionalCheckKind.KEY_CONDITION: KEY_CONDITION_TEMPLATE_ID,
    BidirectionalCheckKind.COUNTERFACTUAL: COUNTERFACTUAL_TEMPLATE_ID,
    BidirectionalCheckKind.CANDIDATE_FREE_RECALL: CANDIDATE_FREE_TEMPLATE_ID,
}

#: Bounded answer vocabularies. Deliberately not A/B/C: Module 17's labels
#: carry calibrated verifier semantics and must not be confused with these.
REVERSE_WORDS = ("SUPPORTED", "CONTRADICTED", "UNRESOLVED")
COUNTERFACTUAL_WORDS = ("TARGET", "EXCLUDED", "NEITHER", "UNKNOWN")


@dataclass(frozen=True)
class RenderedPrompt:
    """One rendered Module 18 prompt and the frame that produced it."""

    check_kind: BidirectionalCheckKind
    template_id: str
    prompt: str
    system_prompt: str
    candidate_shown: bool


def _definition_block(contract: RelationContract) -> str:
    """Module 0's authoritative relation text, as Module 4 renders it."""
    return contract.verifier_definition()


def render_reverse(
    profile: CheckProfile,
    contract: RelationContract,
    *,
    subject: str,
    candidate: str,
) -> RenderedPrompt:
    """§14 reverse: ask the same relation from the candidate's side.

    Structurally reversed, not renamed: the candidate is presented as the
    subject of the relation and the original subject as the object under test,
    which is a genuinely different question for the model even though the
    contract is the same.
    """
    if not profile.supports(BidirectionalCheckKind.REVERSE):
        raise BidirectionalCheckError(
            f"{profile.relation} declares no reverse framing: "
            f"{profile.reverse_rationale}"
        )
    prompt = (
        f"Relation: {contract.relation}\n"
        f"Definition:\n{_definition_block(contract)}\n\n"
        f"{profile.reverse_frame}\n\n"
        f"Take \"{candidate}\" as the subject.\n"
        f"Question: does \"{subject}\" satisfy this relation for it?\n\n"
        f"Answer with exactly one word: {', '.join(REVERSE_WORDS)}.\n"
        "Answer UNRESOLVED if you cannot establish either."
    )
    return RenderedPrompt(
        BidirectionalCheckKind.REVERSE, REVERSE_TEMPLATE_ID, prompt,
        REVERSE_SYSTEM_PROMPT, candidate_shown=True,
    )


def render_key_condition(
    profile: CheckProfile,
    contract: RelationContract,
    *,
    subject: str,
) -> RenderedPrompt:
    """§14 key-condition: mask the decisive value and ask for it back.

    The target is **not shown**. The model is given the subject and the
    relation and asked to reconstruct the object, so what comes back can be
    compared with what the system holds - the consistency signal §14 wants.
    This is why the frame never mentions a previous answer: "your answer was X,
    check it" would be the generic re-ask §14 rejects.
    """
    prompt = (
        f"Subject: {subject}\n"
        f"Relation: {contract.relation}\n"
        f"Definition:\n{_definition_block(contract)}\n\n"
        f"{profile.key_condition_frame}\n\n"
        + (
            "Answer with the number and its unit only.\n"
            if profile.reconstruction_output == "quantity"
            else "Answer with the name only, on one line.\n"
        )
        + "If you do not know, answer exactly: UNKNOWN"
    )
    return RenderedPrompt(
        BidirectionalCheckKind.KEY_CONDITION, KEY_CONDITION_TEMPLATE_ID, prompt,
        RECONSTRUCTION_SYSTEM_PROMPT, candidate_shown=False,
    )


def render_counterfactual(
    profile: CheckProfile,
    contract: RelationContract,
    *,
    subject: str,
    candidate: str,
    counterfactual_class: str,
) -> RenderedPrompt:
    """§14 counterfactual: the relation against one **contract** exclusion.

    The excluded class is Module 0's own rule text, quoted verbatim. Nothing
    here says which side the system suspects, and the two options are presented
    neutrally with an explicit way out.
    """
    excluded = counterfactual_class_text(contract.relation, counterfactual_class)
    prompt = (
        f"Subject: {subject}\n"
        f"Relation: {contract.relation}\n"
        f"Definition:\n{_definition_block(contract)}\n\n"
        f"Candidate: {candidate}\n\n"
        "The definition above excludes the following case:\n"
        f"- {excluded}\n\n"
        "Decide which of these the candidate is for this subject:\n"
        "  TARGET   - it satisfies the relation as defined\n"
        "  EXCLUDED - it matches the excluded case above instead\n"
        "  NEITHER  - it is neither\n"
        "  UNKNOWN  - you cannot establish which\n\n"
        f"Answer with exactly one word: {', '.join(COUNTERFACTUAL_WORDS)}."
    )
    return RenderedPrompt(
        BidirectionalCheckKind.COUNTERFACTUAL, COUNTERFACTUAL_TEMPLATE_ID, prompt,
        COUNTERFACTUAL_SYSTEM_PROMPT, candidate_shown=True,
    )


def render_candidate_free(
    profile: CheckProfile,
    contract: RelationContract,
    *,
    subject: str,
) -> RenderedPrompt:
    """§14 candidate-free: recall with **no candidate anywhere**.

    The strictest frame in Module 18. It receives the subject and the relation
    and nothing else - no candidate, no previous answer, no verifier label, no
    consensus state, no specialist suspicion - because the whole value of the
    probe is that anything it names, it named on its own.

    The signature deliberately has no candidate parameter. A leak would have to
    be added on purpose.
    """
    prompt = (
        f"Subject: {subject}\n"
        f"Relation: {contract.relation}\n"
        f"Definition:\n{_definition_block(contract)}\n\n"
        f"{profile.candidate_free_frame}\n\n"
        + (
            "Answer with the number and its unit only."
            if profile.candidate_free_output == "quantity"
            else "Answer with names only, one per line."
        )
    )
    return RenderedPrompt(
        BidirectionalCheckKind.CANDIDATE_FREE_RECALL, CANDIDATE_FREE_TEMPLATE_ID,
        prompt, CANDIDATE_FREE_SYSTEM_PROMPT, candidate_shown=False,
    )


def system_prompt_for(kind: BidirectionalCheckKind) -> str:
    return {
        BidirectionalCheckKind.REVERSE: REVERSE_SYSTEM_PROMPT,
        BidirectionalCheckKind.KEY_CONDITION: RECONSTRUCTION_SYSTEM_PROMPT,
        BidirectionalCheckKind.COUNTERFACTUAL: COUNTERFACTUAL_SYSTEM_PROMPT,
        BidirectionalCheckKind.CANDIDATE_FREE_RECALL: CANDIDATE_FREE_SYSTEM_PROMPT,
    }[kind]


__all__ = [
    "CANDIDATE_FREE_SYSTEM_PROMPT",
    "CANDIDATE_FREE_TEMPLATE_ID",
    "COUNTERFACTUAL_SYSTEM_PROMPT",
    "COUNTERFACTUAL_TEMPLATE_ID",
    "COUNTERFACTUAL_WORDS",
    "KEY_CONDITION_TEMPLATE_ID",
    "RECONSTRUCTION_SYSTEM_PROMPT",
    "REVERSE_SYSTEM_PROMPT",
    "REVERSE_TEMPLATE_ID",
    "REVERSE_WORDS",
    "TEMPLATE_IDS",
    "RenderedPrompt",
    "render_candidate_free",
    "render_counterfactual",
    "render_key_condition",
    "render_reverse",
    "system_prompt_for",
]
