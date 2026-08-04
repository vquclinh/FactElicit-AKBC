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
    ViewFamily.DESCRIPTION: IndependenceGroup.RELATION_FOCUSED_DESCRIPTION,
    ViewFamily.MISSINGNESS: IndependenceGroup.MISSINGNESS_SEARCH,
    ViewFamily.REVERSE: IndependenceGroup.REVERSE_ALTERNATE,
    # A gate yields a verdict, never a candidate, so its provenance group is
    # kept out of the candidate-acquisition families above.
    ViewFamily.GATE: IndependenceGroup.EXISTENCE_GATE,
}

#: Families that actually acquire object candidates. ``eligible_groups_for``
#: derives ``m(o)`` from these alone.
CANDIDATE_FAMILIES: tuple[ViewFamily, ...] = (
    ViewFamily.DIRECT,
    ViewFamily.STRUCTURAL,
    ViewFamily.DESCRIPTION,
    ViewFamily.CONTRASTIVE,
    ViewFamily.MISSINGNESS,
    ViewFamily.REVERSE,
)

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

#: Stage 1 of a relation-focused description. Deliberately asks for prose and
#: forbids a list: the point is to surface parametric memory *before* any
#: extraction, not to smuggle a second direct-recall prompt.
DESCRIPTION_FORMAT = (
    "Write two or three sentences of continuous prose. Do not produce a list, "
    "bullet points or a bare sequence of names."
)


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
    #: How many times this acquisition procedure is executed per query. Repeats
    #: share one ``view_id`` and one independence group and differ only by
    #: ``run_id``: they amplify evidence, they never create a new mechanism
    #: (spec section 7.3). Repetition is subordinate to structural diversity.
    runs: int = 1
    #: A gate view answers YES/NO/UNKNOWN and yields no candidates itself.
    is_gate: bool = False
    #: A missingness view needs the already-accepted set injected.
    needs_accepted_set: bool = False
    #: Stage-1 prompt of a relation-focused description. When set, the engine
    #: first generates prose with this, then mines it with ``template``. The
    #: prose itself never becomes a candidate or an evidence edge.
    description_template: str = ""
    #: A candidate-conditioned view: ``template`` carries a ``{candidate}``
    #: slot and the engine must be given the candidate to ask about.
    is_reverse: bool = False

    @property
    def is_description(self) -> bool:
        return bool(self.description_template)

    @property
    def independence_group(self) -> IndependenceGroup:
        return FAMILY_TO_GROUP[self.family]

    @property
    def facet(self) -> str:
        """Facet id, defaulting to the view id when the view is not partitioned."""
        return self.facet_id or self.view_id

    def validate(self) -> None:
        """Reject a view whose repetition could not produce anything new."""
        if self.runs < 1:
            raise ValueError(f"{self.view_id}: runs must be at least 1")
        if self.is_description:
            if self.family is not ViewFamily.DESCRIPTION:
                raise ValueError(f"{self.view_id}: a two-stage view must use the DESCRIPTION family")
            if "{context}" not in self.template:
                raise ValueError(
                    f"{self.view_id}: the extraction stage must consume "
                    "'{context}', otherwise it is a renamed direct prompt"
                )
        if self.family is ViewFamily.DESCRIPTION and not self.is_description:
            raise ValueError(f"{self.view_id}: a DESCRIPTION view needs a description_template")
        if self.is_reverse:
            if self.family is not ViewFamily.REVERSE:
                raise ValueError(f"{self.view_id}: a candidate-conditioned view must use REVERSE")
            if "{candidate}" not in self.template:
                raise ValueError(f"{self.view_id}: a reverse view must consume '{{candidate}}'")
        if self.family is ViewFamily.REVERSE and not self.is_reverse:
            raise ValueError(f"{self.view_id}: a REVERSE view must be candidate-conditioned")
        if self.runs > 1 and self.decode.deterministic:
            raise ValueError(
                f"{self.view_id}: runs={self.runs} with greedy decoding would "
                "repeat an identical generation; use a sampled decode profile"
            )
        if self.is_gate and self.family is not ViewFamily.GATE:
            raise ValueError(f"{self.view_id}: a gate view must declare the GATE family")

    def render(
        self,
        *,
        subject: str,
        definition: str,
        accepted: list[str] | None = None,
        context: str = "",
        candidate: str = "",
    ) -> str:
        """Fill the (extraction) template for one query."""
        return self.template.format(
            subject=subject,
            definition=definition,
            accepted="; ".join(accepted or []) or "(none yet)",
            context=context,
            candidate=candidate,
        ).strip()

    def render_description(self, *, subject: str, definition: str) -> str:
        """Fill the stage-1 description prompt."""
        if not self.is_description:
            raise ValueError(f"{self.view_id} is not a two-stage description view")
        return self.description_template.format(
            subject=subject, definition=definition
        ).strip()
