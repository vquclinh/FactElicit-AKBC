"""Module 17 prompt rendering - where the blindness guarantee is enforced.

Proposal §13: M17 "preserves CoVe's blind-verification invariant". A specialist
prompt may carry exactly four things:

1. the subject;
2. Module 0's authoritative relation definition, as Module 4 already renders it
   (``contract.verifier_definition()`` - answer type, positive rules, hard
   negatives);
3. the target - one candidate, one numeric value, or one query-level
   proposition;
4. the fixed A/B/C labels, in one presentation order.

Plus §13's own question frame and hard-negative *class* boundary, which are
contract text and not observations about this candidate.

Nothing else. In particular **none of this may appear**: Module 11 recall,
generator rationale, chain-of-thought, the source prompt, Module 12 cluster
support or dispersion, Module 13 facet counts, Module 14 recall rationale,
Module 15 gate or closure state, Module 16's `F`/`L`/`X`/`C`/`U`/`I`/`D`,
support counts, independence groups, risk flags, pending checks, or "the
generator produced this five times". A verifier shown the acquisition evidence
is anchored on it, and its label stops being independent evidence.

The templates are built as :class:`~cover_kbc.verification.blind.VerifierTemplate`
values on purpose: Module 4's contextual calibrator renders **the same template
object** with a content-free subject and candidate to obtain the control, so a
specialist reading is calibrated against its own presentation - including its
own label order - with no new calibration code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.types import VerificationLabel
from cover_kbc.verification.blind import LABEL_TOKENS, VerifierTemplate
from cover_kbc.verification.specialist_contracts import SpecialistVerifierContract
from cover_kbc.verification.specialist_types import (
    LabelOrder,
    SpecialistVerifierError,
    SpecialistVerifierFamily,
)

#: What each fixed label means. Identical to Module 4's - reusing the mapping
#: rather than restating it is what keeps A/B/C semantics single-sourced.
_LABEL_MEANING: dict[str, str] = {
    LABEL_TOKENS[VerificationLabel.VALID.value]: "VALID",
    LABEL_TOKENS[VerificationLabel.INVALID.value]: "INVALID",
    LABEL_TOKENS[VerificationLabel.UNKNOWN.value]: "UNKNOWN",
}

#: Module 17's system frame. Says what UNKNOWN is *for*, because §6's semantics
#: only hold if the verifier is told that abstaining is available and is not a
#: soft "no".
SPECIALIST_SYSTEM_PROMPT = (
    "You verify exactly one claim against the supplied relation definition. "
    "Do not assume the claim is correct, and do not assume it is incorrect. Use "
    "only your internal parametric knowledge; you have no access to search or "
    "documents. Choose UNKNOWN when you cannot establish either answer "
    "reliably - it is not a weaker way of saying INVALID. Return exactly one "
    "label."
)


def render_label_block(order: LabelOrder) -> str:
    """The three fixed labels, listed in one presentation order.

    **Presentation only.** A always means VALID, B always means INVALID and C
    always means UNKNOWN; only the order of the lines changes. §13.1 asks for
    label-order swaps precisely so positional preference becomes measurable,
    and remapping the letters would change the question instead.
    """
    lines = [f"{letter} = {_LABEL_MEANING[letter]}" for letter in order.sequence]
    return "\n".join(lines) + "\n\nReturn exactly one label."


@dataclass(frozen=True)
class SpecialistTemplateSpec:
    """One specialist phrasing, before a label order is chosen.

    ``body`` is a ``str.format`` template over the same five field names Module
    4's :class:`VerifierTemplate` uses, plus the question frame and boundary
    which are baked in when the concrete template is built.
    """

    template_id: str
    family: SpecialistVerifierFamily
    body: str
    rationale: str = ""


_TARGET_LINE = {
    SpecialistVerifierFamily.NUMERIC: "Value: {candidate}",
    SpecialistVerifierFamily.AWARD_MEMBERSHIP: "Candidate: {candidate}",
    SpecialistVerifierFamily.NULL_TEMPORAL: "Candidate: {candidate}",
    SpecialistVerifierFamily.STOCK: "Candidate: {candidate}",
    SpecialistVerifierFamily.BORDER: "Candidate: {candidate}",
}

#: Two semantically equivalent phrasings per family, so §13.1's template
#: disagreement is measurable. Deliberately two, not many: verification is the
#: expensive half of the budget, and Module 4 made the same choice.
_STATEMENT_BODY = (
    "Subject: {subject}\n"
    "Relation: {relation}\n"
    "Definition:\n{definition}\n\n"
    "{target_line}\n\n"
    "{question}\n"
    "{boundary}\n\n"
    "{labels}"
)

_QUESTION_BODY = (
    "Definition of the relation '{relation}':\n{definition}\n\n"
    "Subject: {subject}\n"
    "{target_line}\n\n"
    "Under that definition exactly: {question}\n"
    "{boundary}\n\n"
    "{labels}"
)

#: Query-level propositions have no candidate, so they get their own frame.
_PROPOSITION_BODY = (
    "Subject: {subject}\n"
    "Relation: {relation}\n"
    "Definition:\n{definition}\n\n"
    "Statement: {candidate}\n\n"
    "Is that statement true of the subject?\n"
    "{boundary}\n\n"
    "{labels}"
)

SPECIALIST_TEMPLATE_IDS: tuple[str, ...] = (
    "m17_statement_v1",
    "m17_question_v1",
    "m17_proposition_v1",
)

#: Which templates each target frame may use.
CANDIDATE_TEMPLATE_IDS: tuple[str, ...] = ("m17_statement_v1", "m17_question_v1")
PROPOSITION_TEMPLATE_IDS: tuple[str, ...] = ("m17_proposition_v1",)


def specialist_template_ids(*, proposition: bool = False) -> tuple[str, ...]:
    return PROPOSITION_TEMPLATE_IDS if proposition else CANDIDATE_TEMPLATE_IDS


def _body_for(template_id: str) -> str:
    if template_id == "m17_statement_v1":
        return _STATEMENT_BODY
    if template_id == "m17_question_v1":
        return _QUESTION_BODY
    if template_id == "m17_proposition_v1":
        return _PROPOSITION_BODY
    raise SpecialistVerifierError(
        f"unknown Module 17 template {template_id!r}; expected one of "
        f"{list(SPECIALIST_TEMPLATE_IDS)}"
    )


def specialist_template(
    contract: SpecialistVerifierContract,
    template_id: str,
    order: LabelOrder,
    *,
    proposition: bool = False,
) -> VerifierTemplate:
    """Build the Module 4 template object for one (phrasing, label order).

    The specialist question, the boundary and the label block are baked in;
    ``{subject}``, ``{relation}``, ``{definition}`` and ``{candidate}`` stay
    open, which is exactly the signature Module 4's calibrator renders with a
    content-free instance. The template id carries the family, the phrasing and
    the **label order**, so the control cache cannot serve one order's bias to
    another.
    """
    if proposition and template_id not in PROPOSITION_TEMPLATE_IDS:
        raise SpecialistVerifierError(
            f"template {template_id!r} renders a candidate; a query-level "
            f"proposition needs one of {list(PROPOSITION_TEMPLATE_IDS)}"
        )
    if not proposition and template_id not in CANDIDATE_TEMPLATE_IDS:
        raise SpecialistVerifierError(
            f"template {template_id!r} renders a query-level proposition; a "
            f"candidate needs one of {list(CANDIDATE_TEMPLATE_IDS)}"
        )

    body = _body_for(template_id).format(
        subject="{subject}",
        relation="{relation}",
        definition="{definition}",
        candidate="{candidate}",
        target_line=_TARGET_LINE[contract.family].format(candidate="{candidate}"),
        question=contract.question,
        boundary=contract.boundary,
        labels=render_label_block(order),
    )
    return VerifierTemplate(
        template_id=f"m17:{contract.family.value}:{template_id}:{order.value}",
        body=body,
    )


def render_specialist_prompt(
    template: VerifierTemplate,
    *,
    subject: str,
    contract: RelationContract,
    target_text: str,
) -> str:
    """Render one blind specialist prompt.

    ``near_misses`` is passed empty on purpose: the contract's hard-negative
    *rules* already appear inside ``verifier_definition()``, and Module 4's
    near-miss block is the adversarial template's "this candidate is suspected
    of being a near miss" framing, which would tell the verifier what the
    upstream system suspects about this particular candidate.
    """
    return template.render(
        subject=subject,
        relation=contract.relation,
        definition=contract.verifier_definition(),
        candidate=target_text,
        near_misses="",
    )


def label_meaning() -> Mapping[str, str]:
    """A/B/C -> canonical label, for a trace. Identical to Module 4's."""
    return dict(_LABEL_MEANING)


__all__ = [
    "CANDIDATE_TEMPLATE_IDS",
    "PROPOSITION_TEMPLATE_IDS",
    "SPECIALIST_SYSTEM_PROMPT",
    "SPECIALIST_TEMPLATE_IDS",
    "SpecialistTemplateSpec",
    "label_meaning",
    "render_label_block",
    "render_specialist_prompt",
    "specialist_template",
    "specialist_template_ids",
]
