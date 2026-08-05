"""The :class:`RelationContract` - Module 0 of the architecture spec.

A contract is the single place where one relation's official semantics live.
It is consumed simultaneously by the router, the elicitation engine, the
parser/normaliser, the verifier and the final selector, so a change to an
official relation definition is made once and propagates deterministically
(spec section 5.3).

Contracts contain *definitions*, never facts.  Nothing here may encode which
countries border which, or which company lists where - that would be an
external knowledge base by another name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from cover_kbc.normalization.strings import NormalizationPolicy
if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from cover_kbc.contracts.programs import TypedProgramSpec

from cover_kbc.types import (
    Cardinality,
    IndependenceGroup,
    OutputType,
    ProgramType,
    ViewFamily,
)


@dataclass(frozen=True)
class VerificationPolicy:
    """Relation-specific verification semantics (spec sections 5.1 and 10.5).

    Every field here is consumed: the support threshold and the acceptance
    thresholds by :mod:`cover_kbc.scoring`, and the near-miss classes by the
    adversarial verifier template.

    Every value here is **authoritative for its relation**. ``None`` means "this
    relation has no opinion", and the global :class:`~cover_kbc.scoring.ScoringConfig`
    default applies. A relation is free to sit at a lower precision / higher
    recall operating point than the global default - that is the whole point of
    a relation-typed system, and clamping it to the global value would make the
    six programmes behave alike exactly where they should differ.
    """

    #: Candidates at or above this many independent supports skip verification.
    #: ``None`` inherits the global default.
    auto_accept_independent_support: int | None = None
    #: Minimum calibrated P(VALID) for a verified candidate to be accepted.
    #: ``None`` inherits the global default.
    accept_valid_prob: float | None = None
    #: Near-miss classes named in the adversarial verifier prompt, so the
    #: verifier is told what this specific relation is usually confused with
    #: (spec section 10.5: "a common near miss specified by the contract").
    adversarial_classes: tuple[str, ...] = ()
    #: Whether an UNKNOWN verdict should keep a candidate out of the output.
    #: ``None`` inherits the global default.
    drop_on_unknown: bool | None = None


@dataclass(frozen=True)
class StoppingPolicy:
    """Relation-specific stopping criteria (spec sections 5.1 and 12.3).

    Consumed by :func:`cover_kbc.controller.should_stop` and by the per-query
    budget.  The controller honours these in preference to its own global
    defaults, because "we have searched enough" means different things for a
    single scalar and for an open-ended award list.
    """

    max_calls: int = 6
    max_generated_tokens: int = 2048
    #: Jaccard set-stability threshold above which the set counts as stable.
    stability_threshold: float = 1.0
    #: Consecutive zero-yield actions tolerated before saturation is declared.
    saturation_patience: int = 2
    #: RCSE residual below which continuing is not worth the compute.
    residual_stop_threshold: float = 0.15
    notes: str = ""


@dataclass(frozen=True)
class SelectionPolicy:
    """How the graph becomes the final ``ObjectEntities`` list (spec Module 8)."""

    #: Independent evidence groups required before a candidate is emitted.
    min_independent_support: int = 1
    #: Hard cap on emitted objects; 0 means unlimited.
    max_objects: int = 0
    #: Relative-distance threshold for numeric clustering.
    #: Bound on a cluster's **whole diameter**, not on each adjacent step:
    #: ``relative_distance(min(C), max(C)) <= threshold``.
    #:
    #: Clustering is *not* single-linkage. A pairwise threshold does not bound
    #: the diameter at all - a chain of values each just under it drifts without
    #: limit - so a diameter bound is what "one coherent numeric value" actually
    #: means, and it makes the median provably within ``threshold`` of every
    #: member (audit 0012 §30).
    #:
    #: Kept conservatively inside the official ±5% tolerance rather than pinned
    #: to it. An architecture default, not a fitted value.
    numeric_cluster_threshold: float = 0.025
    #: Emit integers only (capacity is a person count).
    numeric_integer_only: bool = False
    #: Unit every numeric candidate is converted to before clustering.
    numeric_target_unit: str | None = None


@dataclass(frozen=True)
class RelationContract:
    """Executable semantic contract for one official relation."""

    relation: str
    program_type: ProgramType
    output_type: OutputType
    cardinality: Cardinality
    answer_type: str
    #: Prose definition used verbatim inside verifier prompts.
    definition: str
    positive_rules: tuple[str, ...]
    hard_negative_rules: tuple[str, ...]
    mandatory_views: tuple[str, ...]
    optional_views: tuple[str, ...] = ()
    normalization: NormalizationPolicy = field(default_factory=NormalizationPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    stopping: StoppingPolicy = field(default_factory=StoppingPolicy)
    selection: SelectionPolicy = field(default_factory=SelectionPolicy)
    #: Independence groups this relation's programme can ever produce.  This is
    #: ``m(o)`` in the coverage ratio ``q(o) = g(o) / m(o)``.
    eligible_independence_groups: tuple[IndependenceGroup, ...] = ()
    #: Families the elicitation engine may draw views from.
    view_families: tuple[ViewFamily, ...] = ()

    # -- derived helpers -----------------------------------------------------

    @property
    def is_numeric(self) -> bool:
        return self.output_type is OutputType.NUMBER

    @property
    def allows_empty(self) -> bool:
        return self.cardinality is not Cardinality.EXACTLY_ONE

    @property
    def program(self) -> "TypedProgramSpec":
        """The typed programme regime this relation is routed to (Module 1)."""
        from cover_kbc.contracts.programs import get_program

        return get_program(self.program_type)

    @property
    def max_objects(self) -> int:
        """Structural upper bound on the emitted set.

        The programme regime caps it where the regime is bounded (NULL_SINGLE,
        NUMERIC); otherwise the relation's own selection policy decides. The
        bound is read from Module 1 rather than re-derived here, so there is one
        authoritative statement of "this regime returns at most one object".
        """
        program = self.program
        if program.bounded:
            return program.max_objects
        return self.selection.max_objects

    def all_views(self) -> tuple[str, ...]:
        return (*self.mandatory_views, *self.optional_views)

    def key(self, value: str) -> str:
        """Identity key the evidence graph groups candidates by."""
        return self.normalization.alias_hint_key(value)

    def strict_key(self, value: str) -> str:
        """Evaluator-identical key; collapsing on this is always lossless."""
        return self.normalization.strict_key(value)

    def verifier_definition(self) -> str:
        """The definition block a blind verifier prompt embeds.

        Carries the declared answer type and the hard-negative classes: the
        near misses are exactly what a verifier has to be told to reject.
        """
        lines = [self.definition, "", f"Expected answer type: {self.answer_type}."]
        lines += ["", "Counts as correct:"]
        lines += [f"- {rule}" for rule in self.positive_rules]
        lines += ["", "Does NOT count:"]
        lines += [f"- {rule}" for rule in self.hard_negative_rules]
        return "\n".join(lines)

    def near_miss_block(self) -> str:
        """The relation's named near-miss classes, for the adversarial template.

        Empty string when the contract declares none, so the template degrades
        to its generic exclusion check rather than emitting a dangling heading.
        """
        if not self.verification.adversarial_classes:
            return ""
        classes = ", ".join(self.verification.adversarial_classes)
        return f"Common near misses for this relation: {classes}."

    def validate(self) -> None:
        """Internal consistency check, exercised by the contract tests.

        Includes the Module 1 programme invariants: a contract may not declare a
        cardinality or output type its routed programme forbids.
        """
        from cover_kbc.contracts.programs import check_program_compatibility

        if not self.relation:
            raise ValueError("contract is missing a relation name")
        if not self.mandatory_views:
            raise ValueError(f"{self.relation}: at least one mandatory view is required")
        if not self.positive_rules:
            raise ValueError(f"{self.relation}: positive semantics must be stated")
        if not self.hard_negative_rules:
            raise ValueError(f"{self.relation}: near-miss semantics must be stated")
        overlap = set(self.mandatory_views) & set(self.optional_views)
        if overlap:
            raise ValueError(f"{self.relation}: views are both mandatory and optional: {overlap}")
        if self.is_numeric and self.selection.numeric_target_unit is None:
            raise ValueError(f"{self.relation}: numeric relations need a target unit")
        if not self.allows_empty and self.selection.max_objects not in (0, 1):
            raise ValueError(
                f"{self.relation}: cardinality forbids an empty answer, so the "
                "selection cap must be exactly one object"
            )
        if not self.eligible_independence_groups:
            raise ValueError(f"{self.relation}: eligible independence groups must be declared")
        probability = self.verification.accept_valid_prob
        if probability is not None and not 0.0 <= probability <= 1.0:
            raise ValueError(f"{self.relation}: accept_valid_prob must be a probability")
        if self.stopping.max_calls <= 0:
            raise ValueError(f"{self.relation}: stopping.max_calls must be positive")
        problems = check_program_compatibility(self)
        if problems:
            raise ValueError("; ".join(problems))

    def coverage_denominator(self) -> int:
        """``m(o)``: how many independent groups could express a candidate."""
        return len(self.eligible_independence_groups)


def eligible_groups_for(families: Sequence[ViewFamily]) -> tuple[IndependenceGroup, ...]:
    """Map view families onto the independence groups they can produce.

    Only *candidate-acquisition* families count. A gate family is skipped: it
    returns a verdict rather than candidates, so including it would put a
    mechanism into ``m(o)`` that no candidate can ever reach - capping ``q(o)``
    and ``F(o)`` below 1.0 for every gated relation.
    """
    mapping = {
        ViewFamily.DIRECT: IndependenceGroup.DIRECT_RECALL,
        ViewFamily.STRUCTURAL: IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        ViewFamily.DESCRIPTION: IndependenceGroup.RELATION_FOCUSED_DESCRIPTION,
        ViewFamily.CONTRASTIVE: IndependenceGroup.CONTRASTIVE_SEPARATION,
        ViewFamily.MISSINGNESS: IndependenceGroup.MISSINGNESS_SEARCH,
        ViewFamily.REVERSE: IndependenceGroup.REVERSE_ALTERNATE,
    }
    return tuple(dict.fromkeys(mapping[f] for f in families if f in mapping))
