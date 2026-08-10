"""Class-B instructions, bound to the prompts the models are actually sent.

Audit 0076 declared four relation instructions in :mod:`cover_kbc.v3_1.prompts`
and wired none of them. Writing prompt text into a module changes nothing; this
module is the binding, and everything here is reachable only from
:class:`~cover_kbc.v3_1.config.V31AggressiveConfig`.

The live call graph, traced from source (audit 0077 §5)
-------------------------------------------------------
**Enumerator / factual recall.** Both acquisition paths converge on one call::

    library.py static ViewSpec          v3_core.execution._recall_template
    (contract.mandatory_views)          -> execution._view_spec  (dynamic ViewSpec)
                    \\                          /
                     ElicitationEngine.run_view
                        view.render(...)            -> GenerationRequest.prompt
                        engine system prompt        -> GenerationRequest.system_prompt
                                    |
                              LMRuntime.generate

``run_view`` is therefore the single choke point for *every* enumerator prompt,
including the V3 actions - ``SET_EXPANSION`` for awards, the stock listing
recall templates and the capacity recall templates. Injecting there reaches all
of them without touching a template, so view identity, independence groups and
facet accounting are unchanged.

**Verifier.** Two distinct surfaces, and they are not interchangeable:

* Module 17 blind specialist verification renders
  ``specialist_contracts.<FAMILY>_CONTRACT.boundary`` through
  ``specialist_prompts.specialist_template``;
* the V3 ``SEMANTIC_VERIFY`` / ``CONTRAST_VERIFY`` actions render
  ``verification.v3_modes.render_v3_verification_prompt``.

The city instruction is bound to **both**, because the relation's contrast
verification runs through the V3 action path while its blind verification runs
through M17, and an instruction present in only one would silently not apply to
half the calls.

Why the verifier binding is admissible
--------------------------------------
``specialist_prompts`` enforces a blindness invariant: a specialist prompt may
carry the subject, Module 0's definition, the target, the labels, and "§13's own
question frame and hard-negative *class* boundary, which are contract text and
not observations about this candidate". The city instruction is exactly a
hard-negative class boundary - it names the confusable attribute classes (birth
city, burial place, hospital, residence, country, region) and says nothing about
the candidate in front of the verifier. It is admissible under the invariant as
stated, and :data:`CITY_VERIFIER_BOUNDARY_TEMPLATE` is written to stay inside it: no
support counts, no acquisition evidence, no "the enumerator believes".

Relation isolation
------------------
Every lookup is keyed on the relation name and returns ``""`` for anything not
explicitly bound. ``countryLandBordersCountry`` has no binding at all and is
additionally named in :data:`FROZEN_RELATIONS`, so a future edit that adds a
border entry fails a test rather than changing border prompts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from cover_kbc.v3_1.config import V31AggressiveConfig, V31Config
from cover_kbc.v3_1.prompts import (
    AWARD_SUPPORTED_ENUMERATION,
    CAPACITY_DEFINITION,
    CITY_OF_DEATH_CONTRAST,
    STOCK_LISTING_ENTITY,
)

#: Bumped whenever a binding or an injected body changes. Recorded on every
#: generation record so a persisted run can be told apart from one produced
#: under different instructions.
LIVE_PROMPT_VERSION = "v3.1-live-prompts-v1"

#: Relations no Class-B binding may ever touch.
FROZEN_RELATIONS: frozenset[str] = frozenset({"countryLandBordersCountry"})

#: Enumerator bindings: relation -> (feature flag, instruction body).
#: The bodies are the audit-0076 instructions, single-sourced from
#: :mod:`cover_kbc.v3_1.prompts` so the declared hash and the live hash cannot
#: describe different text.
_ENUMERATOR_BINDINGS: Mapping[str, tuple[str, str]] = {
    CAPACITY_DEFINITION.relation: (
        CAPACITY_DEFINITION.feature, CAPACITY_DEFINITION.body),
    STOCK_LISTING_ENTITY.relation: (
        STOCK_LISTING_ENTITY.feature, STOCK_LISTING_ENTITY.body),
    AWARD_SUPPORTED_ENUMERATION.relation: (
        AWARD_SUPPORTED_ENUMERATION.feature, AWARD_SUPPORTED_ENUMERATION.body),
}

#: The city instruction as a verifier *class boundary*: a reject-answer rule
#: over attribute classes, never a statement about the candidate being verified.
#:
#: ``{reject}`` is a placeholder because the two verifier surfaces do not share
#: a label vocabulary. M17 presents fixed ``A``/``B``/``C`` tokens, while the V3
#: SEMANTIC/UNARY/CONTRAST frames present ``VALID``/``INVALID``/``UNKNOWN``.
#: Emitting "Answer B" into a prompt whose labels are VALID/INVALID/UNKNOWN
#: would instruct the model to produce a token that frame never offers, so the
#: reject token is filled in per call site by :meth:`RelationInstructions.
#: verifier_boundary`.
CITY_VERIFIER_BOUNDARY_TEMPLATE = (
    "Answer {reject} if the candidate answers a different attribute of the "
    "subject than the place of death - the place of birth, a burial or "
    "memorial place, a hospital, clinic or residence rather than a settlement, "
    "the country, state, province or region rather than the city, a place the "
    "subject was merely associated with, or a place belonging to a different "
    "person of a similar name. A larger administrative unit that merely "
    "contains the place of death is also {reject}; a district, ward or borough "
    "that is itself the recorded place of death is not."
)

#: M17's reject token. ``B`` is INVALID in every label order - only the *order*
#: of the lines rotates, never the letter-to-meaning mapping.
M17_REJECT_LABEL = "B"
#: The V3 verification frames' reject token.
V3_REJECT_LABEL = "INVALID"

#: Verifier bindings: relation -> (feature flag, boundary template).
_VERIFIER_BINDINGS: Mapping[str, tuple[str, str]] = {
    CITY_OF_DEATH_CONTRAST.relation: (
        CITY_OF_DEATH_CONTRAST.feature, CITY_VERIFIER_BOUNDARY_TEMPLATE),
}


def instruction_hash(text: str) -> str:
    """Stable identity of one injected body."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RelationInstructions:
    """The Class-B instructions active for one run.

    Constructed from configuration and passed to the engine and the verifier.
    An instance built from a default (all-off) config returns ``""`` for every
    relation, which is what keeps V3 behaviour byte-identical.
    """

    aggressive: V31AggressiveConfig = V31AggressiveConfig()
    enabled: bool = False

    @classmethod
    def from_config(cls, config: V31Config | None) -> "RelationInstructions":
        if config is None or not config.enabled:
            return cls()
        return cls(aggressive=config.aggressive, enabled=True)

    def _active(self, feature: str) -> bool:
        return self.enabled and bool(getattr(self.aggressive, feature, False))

    def enumerator_instruction(self, relation: str) -> str:
        """Extra standing instruction for this relation's recall prompts."""
        if relation in FROZEN_RELATIONS:
            return ""
        binding = _ENUMERATOR_BINDINGS.get(relation)
        if binding is None or not self._active(binding[0]):
            return ""
        return binding[1].strip()

    def verifier_boundary(self, relation: str, *, reject_label: str = V3_REJECT_LABEL) -> str:
        """Extra hard-negative class boundary for this relation's verification.

        ``reject_label`` is the token the *calling frame* offers for "no": ``B``
        for M17's fixed A/B/C labels, ``INVALID`` for the V3 frames. Naming a
        token the frame does not present would ask for an unparseable answer.
        """
        if relation in FROZEN_RELATIONS:
            return ""
        binding = _VERIFIER_BINDINGS.get(relation)
        if binding is None or not self._active(binding[0]):
            return ""
        return binding[1].strip().format(reject=reject_label)

    def system_prompt_for(self, base: str, relation: str) -> str:
        """The system prompt actually sent for a recall call on ``relation``.

        Appended, never substituted: the closed-book rule and the
        format-compliance instruction in the base prompt are what keep the
        parser working, and a Class-B experiment must not be able to drop them.
        """
        instruction = self.enumerator_instruction(relation)
        if not instruction:
            return base
        return f"{base}\n\n{instruction}"

    @property
    def scientific_notation_acquisition(self) -> bool:
        """Class-B acquisition parsing (audit 0077 §11). Off unless configured."""
        return self._active("scientific_notation_acquisition")

    @property
    def active_features(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return self.aggressive.enabled_features

    @property
    def active_relations(self) -> tuple[str, ...]:
        relations = {
            relation
            for relation in (*_ENUMERATOR_BINDINGS, *_VERIFIER_BINDINGS)
            if self.enumerator_instruction(relation) or self.verifier_boundary(relation)
        }
        return tuple(sorted(relations))


#: The inert instance: what production uses unless a Class-B config is loaded.
NO_INSTRUCTIONS = RelationInstructions()


def live_prompt_inventory() -> list[dict[str, str]]:
    """Every binding with its call site and body hash, for the audit."""
    rows: list[dict[str, str]] = []
    for relation, (feature, body) in sorted(_ENUMERATOR_BINDINGS.items()):
        rows.append({
            "relation": relation,
            "feature": feature,
            "owner": "enumerator",
            "call_site": (
                "cover_kbc.elicitation.engine.ElicitationEngine.run_view "
                "-> GenerationRequest.system_prompt"
            ),
            "live_prompt_version": LIVE_PROMPT_VERSION,
            "body_sha256": instruction_hash(body),
        })
    for relation, (feature, body) in sorted(_VERIFIER_BINDINGS.items()):
        rows.append({
            "relation": relation,
            "feature": feature,
            "owner": "verifier",
            "call_site": (
                "cover_kbc.verification.v3_modes.render_v3_verification_prompt "
                "and cover_kbc.verification.specialist_prompts.specialist_template"
            ),
            "live_prompt_version": LIVE_PROMPT_VERSION,
            "body_sha256": instruction_hash(body),
            "m17_body_sha256": instruction_hash(body.format(reject=M17_REJECT_LABEL)),
            "v3_body_sha256": instruction_hash(body.format(reject=V3_REJECT_LABEL)),
        })
    return rows


__all__ = [
    "CITY_VERIFIER_BOUNDARY_TEMPLATE",
    "M17_REJECT_LABEL",
    "V3_REJECT_LABEL",
    "FROZEN_RELATIONS",
    "LIVE_PROMPT_VERSION",
    "NO_INSTRUCTIONS",
    "RelationInstructions",
    "instruction_hash",
    "live_prompt_inventory",
]
