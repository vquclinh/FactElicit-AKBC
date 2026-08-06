"""Module 10 public contract - the compiled prompt program.

A :class:`PromptProgram` is a **blueprint, not a prompt**. It is the structured,
inspectable answer to *"how should this relation be spoken about?"* - task
semantics, an answer schema, positive and negative constraints, semantic cues,
and directives conditioned on Module 9's risk profile.

**Structure is the source of truth.** A future module asks
``program.negative_constraints`` or ``program.answer_schema.canonical_unit``; it
never regexes English back out of a rendered string. Rendering
(:meth:`PromptProgram.fragments`) is a deterministic *projection* of the
structure, and every fragment can be reconstructed from the fields above it.

**What is not here, and why.** The proposal's ``PromptProgram`` also lists
``direct_templates``, ``facet_templates``, ``pseudo_memory_templates`` and
``verifier_templates``. None appear:

* which elicitation view runs is Module 2's, and stays Module 2's;
* pseudo-memory generation is Module 11's;
* verifier templates are Module 4's today and Module 17's later.

M10 says how to talk about a relation. It does not say what to run, when, or
how much.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from cover_kbc.query_intelligence.types import (
    CardinalityRegime,
    RiskLevel,
    SpecialistHint,
)
from cover_kbc.types import ProgramType


class ObjectKind(str, Enum):
    """What one object entity denotes, in prompt-facing terms."""

    ENTITY = "ENTITY"
    NUMBER = "NUMBER"


class NumericKind(str, Enum):
    """Numeric shape the answer must take, when it is numeric at all."""

    NOT_NUMERIC = "NOT_NUMERIC"
    INTEGER = "INTEGER"
    REAL = "REAL"


class DirectiveKind(str, Enum):
    """The kind of prompt language a risk axis compiles into.

    Language and constraints only. A directive never names an action, a budget,
    a facet to execute or a stopping condition - those are M19-M21's.
    """

    EXCLUSION = "EXCLUSION"
    STRICT_FORMAT = "STRICT_FORMAT"
    IDENTITY = "IDENTITY"
    RECALL_BREADTH = "RECALL_BREADTH"
    COMPLETENESS = "COMPLETENESS"
    TEMPORAL = "TEMPORAL"
    EMPTY_PERMITTED = "EMPTY_PERMITTED"


class SubjectDirectiveKind(str, Enum):
    """How the literal subject string must be carried into a prompt."""

    PRESERVE_VERBATIM = "PRESERVE_VERBATIM"
    PRESERVE_PARENTHETICAL = "PRESERVE_PARENTHETICAL"
    PRESERVE_COMMA_QUALIFIER = "PRESERVE_COMMA_QUALIFIER"
    PRESERVE_PREPOSITIONAL_QUALIFIER = "PRESERVE_PREPOSITIONAL_QUALIFIER"
    PRESERVE_UNICODE = "PRESERVE_UNICODE"
    PRESERVE_DIGITS = "PRESERVE_DIGITS"


@dataclass(frozen=True)
class TaskSemantics:
    """What the relation means, transcribed from the Module 0 contract.

    Every field is *copied from* the contract, never authored here: a second
    definition of a relation would be a second contract, and the whole point of
    M0 is that there is one. Tests assert field-by-field equality with
    :class:`~cover_kbc.contracts.base.RelationContract`.
    """

    relation: str
    definition: str
    answer_type: str
    #: One-line statement of what the objects are, for a prompt preamble.
    relation_focus: str

    def to_json(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "definition": self.definition,
            "answer_type": self.answer_type,
            "relation_focus": self.relation_focus,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "TaskSemantics":
        return cls(
            relation=str(payload["relation"]),
            definition=str(payload["definition"]),
            answer_type=str(payload["answer_type"]),
            relation_focus=str(payload["relation_focus"]),
        )


@dataclass(frozen=True)
class AnswerSchema:
    """The prompt-facing shape of a valid answer.

    Compiled from Modules 0 and 1 - output type, cardinality, target unit,
    integer-ness - and translated into terms a prompt can state. It declares
    *language*, not parsing: actual numeric conversion, clustering and consensus
    are Module 12's and Module 16's.
    """

    object_kind: ObjectKind
    cardinality: CardinalityRegime
    numeric_kind: NumericKind
    canonical_unit: str | None
    allow_empty: bool
    #: Structural cap on emitted objects; ``0`` means the regime is unbounded.
    max_objects: int
    #: The exact token a prompt should ask for when there is no answer. Derived
    #: from ``object_kind``, and asserted in tests to agree with the sentinels
    #: Module 2's format strings already use - agreement is checked rather than
    #: assumed, because two spellings of "nothing" would be a parser bug.
    empty_token: str
    #: Deterministic rendering of the fields above. A projection, not a source.
    output_instruction: str

    def to_json(self) -> dict[str, Any]:
        return {
            "object_kind": self.object_kind.value,
            "cardinality": self.cardinality.value,
            "numeric_kind": self.numeric_kind.value,
            "canonical_unit": self.canonical_unit,
            "allow_empty": self.allow_empty,
            "max_objects": self.max_objects,
            "empty_token": self.empty_token,
            "output_instruction": self.output_instruction,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "AnswerSchema":
        return cls(
            object_kind=ObjectKind(payload["object_kind"]),
            cardinality=CardinalityRegime(payload["cardinality"]),
            numeric_kind=NumericKind(payload["numeric_kind"]),
            canonical_unit=payload["canonical_unit"],
            allow_empty=bool(payload["allow_empty"]),
            max_objects=int(payload["max_objects"]),
            empty_token=str(payload["empty_token"]),
            output_instruction=str(payload["output_instruction"]),
        )


@dataclass(frozen=True)
class RiskDirective:
    """One prompt directive compiled from one Module 9 risk axis.

    ``axis`` and ``level`` are kept alongside the text so a later module can
    see *why* a directive is present without parsing the instruction.
    """

    kind: DirectiveKind
    axis: str
    level: RiskLevel
    instruction: str

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "axis": self.axis,
            "level": self.level.value,
            "instruction": self.instruction,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RiskDirective":
        return cls(
            kind=DirectiveKind(payload["kind"]),
            axis=str(payload["axis"]),
            level=RiskLevel(payload["level"]),
            instruction=str(payload["instruction"]),
        )


@dataclass(frozen=True)
class SubjectDirective:
    """A losslessness instruction compiled from a subject-surface feature."""

    kind: SubjectDirectiveKind
    instruction: str

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "instruction": self.instruction}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SubjectDirective":
        return cls(
            kind=SubjectDirectiveKind(payload["kind"]),
            instruction=str(payload["instruction"]),
        )


@dataclass(frozen=True)
class QuerySpecification:
    """The step-back layer: what to work out *before* recalling anything.

    Step-Back prompting abstracts a question before answering it. Here the
    abstraction is fully **contract-derived and deterministic** - no model is
    asked to produce it, and it contains no factual pseudo-context. Generating
    pseudo-memory from these cues is Module 11's job; M10 only states the
    specification M11 will work from.
    """

    relation_focus: str
    semantic_question: str
    abstraction_cues: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "relation_focus": self.relation_focus,
            "semantic_question": self.semantic_question,
            "abstraction_cues": list(self.abstraction_cues),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "QuerySpecification":
        return cls(
            relation_focus=str(payload["relation_focus"]),
            semantic_question=str(payload["semantic_question"]),
            abstraction_cues=tuple(payload["abstraction_cues"]),
        )


@dataclass(frozen=True)
class PromptProgram:
    """Module 10 output for one ``(subject, relation)`` query.

    Immutable, equality-comparable and hashable by value: every field is a
    scalar, an enum or a tuple of frozen dataclasses, so two programs compiled
    from the same inputs are ``==`` and can key a cache.
    """

    compiler_version: str
    #: The Module 9 profile version this program was compiled against. Recorded
    #: so a program can never be silently paired with a different risk vocabulary.
    profile_version: str

    relation: str
    subject: str
    row_index: int
    program_type: ProgramType
    cardinality_regime: CardinalityRegime

    task_semantics: TaskSemantics
    answer_schema: AnswerSchema

    #: Contract ``positive_rules`` / ``hard_negative_rules``, verbatim.
    positive_constraints: tuple[str, ...]
    negative_constraints: tuple[str, ...]

    #: Lexical steering for parametric recall. NOT search queries - see
    #: :mod:`cover_kbc.query_intelligence.prompt_registry`.
    semantic_cues: tuple[str, ...]
    negative_anchors: tuple[str, ...]

    risk_directives: tuple[RiskDirective, ...]
    subject_directives: tuple[SubjectDirective, ...]
    query_specification: QuerySpecification
    specialist_hint: SpecialistHint

    # -- derived views -------------------------------------------------------

    @property
    def output_contract(self) -> str:
        """The rendered answer-format instruction.

        A projection of :attr:`answer_schema`, exposed under the architectural
        name. The schema remains the source of truth.
        """
        return self.answer_schema.output_instruction

    @property
    def keyword_bundle(self) -> tuple[str, ...]:
        """Positive and negative lexical steering together, in that order."""
        return (*self.semantic_cues, *self.negative_anchors)

    def directive(self, kind: DirectiveKind) -> RiskDirective | None:
        """The compiled directive of one kind, if this query has it."""
        for entry in self.risk_directives:
            if entry.kind is kind:
                return entry
        return None

    def has_directive(self, kind: DirectiveKind) -> bool:
        return self.directive(kind) is not None

    def fragments(self) -> dict[str, str]:
        """Deterministic prompt fragments projected from the structure.

        Rendering lives here and only here. Nothing in COVER consumes these in
        this milestone - Module 2 keeps its own templates untouched - and every
        fragment is reconstructible from the typed fields, so the structure
        never has to be recovered by parsing prose.
        """
        parts: dict[str, str] = {
            "task": (
                f"{self.task_semantics.relation_focus}\n\n"
                f"{self.task_semantics.definition}\n\n"
                f"Expected answer type: {self.task_semantics.answer_type}."
            ),
            "positive_constraints": "Counts as correct:\n"
            + "\n".join(f"- {rule}" for rule in self.positive_constraints),
            "negative_constraints": "Does NOT count:\n"
            + "\n".join(f"- {rule}" for rule in self.negative_constraints),
            "output_contract": self.output_contract,
        }
        if self.semantic_cues:
            parts["semantic_cues"] = (
                "Phrasings that describe this relation: "
                + ", ".join(self.semantic_cues)
                + "."
            )
        if self.negative_anchors:
            parts["negative_anchors"] = (
                "Phrasings that describe something else and must not be answered: "
                + ", ".join(self.negative_anchors)
                + "."
            )
        if self.risk_directives:
            parts["risk_directives"] = "\n".join(
                f"- {entry.instruction}" for entry in self.risk_directives
            )
        if self.subject_directives:
            parts["subject_directives"] = "\n".join(
                f"- {entry.instruction}" for entry in self.subject_directives
            )
        parts["query_specification"] = (
            f"{self.query_specification.semantic_question}\n"
            + "\n".join(f"- {cue}" for cue in self.query_specification.abstraction_cues)
        )
        return parts

    # -- serialisation -------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "profile_version": self.profile_version,
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "program_type": self.program_type.value,
            "cardinality_regime": self.cardinality_regime.value,
            "specialist_hint": self.specialist_hint.value,
            "task_semantics": self.task_semantics.to_json(),
            "answer_schema": self.answer_schema.to_json(),
            "positive_constraints": list(self.positive_constraints),
            "negative_constraints": list(self.negative_constraints),
            "semantic_cues": list(self.semantic_cues),
            "negative_anchors": list(self.negative_anchors),
            "risk_directives": [entry.to_json() for entry in self.risk_directives],
            "subject_directives": [entry.to_json() for entry in self.subject_directives],
            "query_specification": self.query_specification.to_json(),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PromptProgram":
        return cls(
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            cardinality_regime=CardinalityRegime(payload["cardinality_regime"]),
            specialist_hint=SpecialistHint(payload["specialist_hint"]),
            task_semantics=TaskSemantics.from_json(payload["task_semantics"]),
            answer_schema=AnswerSchema.from_json(payload["answer_schema"]),
            positive_constraints=tuple(payload["positive_constraints"]),
            negative_constraints=tuple(payload["negative_constraints"]),
            semantic_cues=tuple(payload["semantic_cues"]),
            negative_anchors=tuple(payload["negative_anchors"]),
            risk_directives=tuple(
                RiskDirective.from_json(entry) for entry in payload["risk_directives"]
            ),
            subject_directives=tuple(
                SubjectDirective.from_json(entry) for entry in payload["subject_directives"]
            ),
            query_specification=QuerySpecification.from_json(payload["query_specification"]),
        )
