"""View specifications for the Diverse Elicitation Engine (spec Module 2).

A *view* is one named way of asking the model for candidates.  Every view
declares the independence group its evidence belongs to, which is what stops
the system from mistaking repetition for corroboration:

    raw frequency  !=  independent evidence diversity

Three samples of ``borders_direct`` all land in ``DIRECT_RECALL`` and count as
one independent support; ``borders_direct`` plus ``borders_compass`` count as
two.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cover_kbc.types import DecodeProfile, IndependenceGroup, ViewFamily

#: Family -> independence group.  Views cannot override this: the mapping is
#: what makes independence accounting meaningful.
FAMILY_TO_GROUP: dict[ViewFamily, IndependenceGroup] = {
    ViewFamily.DIRECT: IndependenceGroup.DIRECT_RECALL,
    ViewFamily.STRUCTURAL: IndependenceGroup.STRUCTURAL_DECOMPOSITION,
    ViewFamily.CONTRASTIVE: IndependenceGroup.CONTRASTIVE_SEPARATION,
    ViewFamily.MISSINGNESS: IndependenceGroup.MISSINGNESS_SEARCH,
}

#: Shared system prompt.  States the closed-book rule explicitly so the model
#: is never invited to imagine a retrieval step.
SYSTEM_PROMPT = (
    "You answer knowledge-base completion questions using only your own internal "
    "knowledge. You have no access to search, documents or external tools. "
    "Follow the requested output format exactly and add no commentary."
)

#: Appended to entity-list views so the parser sees a predictable shape.
ENTITY_FORMAT = (
    "Output format: one line, items separated by semicolons, no numbering and no "
    "explanation. If there are none, output exactly: NONE"
)

#: Appended to numeric views.
NUMERIC_FORMAT = (
    "Output format: a single number and its unit, nothing else. "
    "If you do not know a factual value, output exactly: UNKNOWN"
)

#: Appended to yes/no gate views.
GATE_FORMAT = "Output format: exactly one word, YES, NO or UNKNOWN."


@dataclass(frozen=True)
class ViewSpec:
    """One named elicitation view."""

    view_id: str
    relation: str
    family: ViewFamily
    template: str
    #: Sub-partition of one mechanism, e.g. an award decade inside the
    #: structural family. Facets are diagnostic provenance, NOT independence:
    #: five slices of one mechanism are still one independent support.
    facet_id: str = ""
    decode: DecodeProfile = field(default_factory=DecodeProfile)
    system_prompt: str = SYSTEM_PROMPT
    runs: int = 1
    #: A gate view answers YES/NO/UNKNOWN and yields no candidates itself.
    is_gate: bool = False
    #: A missingness view needs the already-accepted set injected.
    needs_accepted_set: bool = False

    @property
    def independence_group(self) -> IndependenceGroup:
        return FAMILY_TO_GROUP[self.family]

    @property
    def facet(self) -> str:
        """Facet id, defaulting to the view id when the view is not partitioned."""
        return self.facet_id or self.view_id

    def render(self, *, subject: str, definition: str, accepted: list[str] | None = None) -> str:
        """Fill the template for one query."""
        accepted_block = "; ".join(accepted or []) or "(none yet)"
        return self.template.format(
            subject=subject, definition=definition, accepted=accepted_block
        ).strip()
