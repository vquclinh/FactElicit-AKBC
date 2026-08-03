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
from typing import Sequence

from cover_kbc.normalization.strings import NormalizationPolicy
from cover_kbc.types import (
    Cardinality,
    IndependenceGroup,
    OutputType,
    ProgramType,
    ViewFamily,
)


@dataclass(frozen=True)
class VerificationPolicy:
    """Which candidates get spent on a blind verifier call (spec section 10.5).

    Milestone 1 stores the policy; the tiering logic that consumes it lands
    with the verifier in Milestone 2.
    """

    #: Candidates at or above this many independent supports skip verification.
    auto_accept_independent_support: int = 3
    #: Minimum calibrated P(VALID) for a verified candidate to be accepted.
    accept_valid_prob: float = 0.5
    #: Near-miss classes that always get the adversarial verifier template.
    adversarial_classes: tuple[str, ...] = ()
    #: Whether an UNKNOWN verdict should keep a candidate out of the output.
    drop_on_unknown: bool = True


@dataclass(frozen=True)
class StoppingPolicy:
    """Relation-specific stopping criteria (spec section 12.3).

    Milestone 1 uses only ``max_calls``/``max_generated_tokens`` via the fixed
    budget.  The remaining fields are the declared interface for the RCSE and
    active controller in Milestone 3.
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
    numeric_cluster_threshold: float = 0.05
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
    def max_objects(self) -> int:
        """Structural upper bound implied by the cardinality regime."""
        if self.cardinality in (Cardinality.ZERO_OR_ONE, Cardinality.EXACTLY_ONE):
            return 1
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

        Includes the hard-negative classes, because the near misses are exactly
        what a verifier must be told to reject.
        """
        lines = [self.definition, "", "Counts as correct:"]
        lines += [f"- {rule}" for rule in self.positive_rules]
        lines += ["", "Does NOT count:"]
        lines += [f"- {rule}" for rule in self.hard_negative_rules]
        return "\n".join(lines)

    def validate(self) -> None:
        """Internal consistency check, exercised by the contract tests."""
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
        if not self.eligible_independence_groups:
            raise ValueError(f"{self.relation}: eligible independence groups must be declared")

    def coverage_denominator(self) -> int:
        """``m(o)``: how many independent groups could express a candidate."""
        return len(self.eligible_independence_groups)


def eligible_groups_for(families: Sequence[ViewFamily]) -> tuple[IndependenceGroup, ...]:
    """Map view families onto the independence groups they produce."""
    mapping = {
        ViewFamily.DIRECT: IndependenceGroup.DIRECT_RECALL,
        ViewFamily.STRUCTURAL: IndependenceGroup.STRUCTURAL_DECOMPOSITION,
        ViewFamily.CONTRASTIVE: IndependenceGroup.CONTRASTIVE_SEPARATION,
        ViewFamily.MISSINGNESS: IndependenceGroup.MISSINGNESS_SEARCH,
    }
    return tuple(dict.fromkeys(mapping[f] for f in families))
