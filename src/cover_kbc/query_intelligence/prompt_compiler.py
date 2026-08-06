"""Module 10 - the Prompt Program Compiler.

Architecture position::

    M0 Relation Compiler
            v
    M1 Typed Program Router
            v
    M9 Risk & Difficulty Profiler
            v
    M10 Prompt Program Compiler        <- here
            v
    [future M11 Closed-Book Parametric Retrieval]
            v
    M2 Diverse Elicitation             (unchanged; still owns which view runs)

M10 turns *(query, contract, risk profile)* into a typed
:class:`~cover_kbc.query_intelligence.prompt_types.PromptProgram`. It does not
execute prompts, call a model, retrieve anything, discover candidates, choose
controller actions or allocate budget.

**Shadow mode.** In this milestone the program is observational: Module 2's
templates are untouched, Module 4's verifier prompts are untouched, and nothing
in M0-M8 reads a compiled program. Module 11 will be the first consumer.

**Zero neural cost.** Nothing here imports a runtime, a registry or a network
client, and nothing performs I/O. Compiling one program is a dict lookup, a
handful of tuple builds and some string formatting.

**M9 is a hard dependency, not an internal detail.** The compiler takes a
profile; it never builds one. A second, invisible profiler would make the risk
vocabulary un-auditable, so the stack is ``M1 -> M9 -> M10`` and a missing
profile is an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.router import compile_query
from cover_kbc.query_intelligence.priors import cardinality_regime_for
from cover_kbc.query_intelligence.prompt_registry import (
    COMPILER_VERSION,
    DIRECTIVE_RULES,
    SUBJECT_DIRECTIVE_RULES,
    RelationPromptSpec,
    check_prompt_registry_consistency,
    get_prompt_spec,
    prompt_specs_from_mapping,
)
from cover_kbc.query_intelligence.prompt_types import (
    AnswerSchema,
    NumericKind,
    ObjectKind,
    PromptProgram,
    QuerySpecification,
    RiskDirective,
    SubjectDirective,
    TaskSemantics,
)
from cover_kbc.query_intelligence.types import RISK_AXES, QueryRiskProfile
from cover_kbc.types import OutputType, Query

#: The token a prompt asks for when an entity answer is empty.
EMPTY_ENTITY_TOKEN = "NONE"
#: The token a prompt asks for when a numeric answer is unavailable.
EMPTY_NUMERIC_TOKEN = "UNKNOWN"


def _answer_schema(contract: RelationContract, profile: QueryRiskProfile) -> AnswerSchema:
    """Compile the prompt-facing answer shape from Modules 0 and 1.

    Translation, not invention: every field restates something the contract or
    the routed programme already fixes. Parsing, unit conversion and consensus
    over the resulting text belong to Modules 12 and 16.
    """
    numeric = contract.output_type is OutputType.NUMBER
    object_kind = ObjectKind.NUMBER if numeric else ObjectKind.ENTITY
    if not numeric:
        numeric_kind = NumericKind.NOT_NUMERIC
    elif contract.selection.numeric_integer_only:
        numeric_kind = NumericKind.INTEGER
    else:
        numeric_kind = NumericKind.REAL
    unit = contract.selection.numeric_target_unit if numeric else None
    empty_token = EMPTY_NUMERIC_TOKEN if numeric else EMPTY_ENTITY_TOKEN

    return AnswerSchema(
        object_kind=object_kind,
        cardinality=profile.cardinality_regime,
        numeric_kind=numeric_kind,
        canonical_unit=unit,
        allow_empty=contract.allows_empty,
        max_objects=contract.max_objects,
        empty_token=empty_token,
        output_instruction=_render_output_instruction(
            object_kind=object_kind,
            numeric_kind=numeric_kind,
            unit=unit,
            allow_empty=contract.allows_empty,
            max_objects=contract.max_objects,
            empty_token=empty_token,
        ),
    )


def _render_output_instruction(
    *,
    object_kind: ObjectKind,
    numeric_kind: NumericKind,
    unit: str | None,
    allow_empty: bool,
    max_objects: int,
    empty_token: str,
) -> str:
    """Project the answer schema into one instruction line.

    A pure function of its arguments, so the schema stays the source of truth
    and the text can be regenerated from it at any time.
    """
    if object_kind is ObjectKind.NUMBER:
        shape = "a whole number" if numeric_kind is NumericKind.INTEGER else "a single number"
        parts = [f"Output {shape}"]
        if unit:
            parts.append(f"expressed in {unit}")
        sentence = " ".join(parts) + ", and nothing else."
        return f"{sentence} If you do not know the value, output exactly: {empty_token}"

    if max_objects == 1:
        sentence = "Output exactly one name, and nothing else."
    else:
        sentence = (
            "Output the names on one line, separated by semicolons, "
            "with no numbering and no explanation."
        )
    if allow_empty:
        sentence += f" If there are none, output exactly: {empty_token}"
    return sentence


def _risk_directives(profile: QueryRiskProfile) -> tuple[RiskDirective, ...]:
    """Compile prompt language from the M9 risk vector.

    Table-driven and ordered by :data:`RISK_AXES`, so the result is stable and
    diffable. Language only: no rule may name an action, a budget, a facet or a
    stopping condition - those belong to Modules 19-21.
    """
    order = {axis: index for index, axis in enumerate(RISK_AXES)}
    fired = [
        RiskDirective(
            kind=rule.kind,
            axis=rule.axis,
            level=profile.axis(rule.axis),
            instruction=rule.instruction,
        )
        for rule in DIRECTIVE_RULES
        if profile.axis(rule.axis) >= rule.trigger
    ]
    fired.sort(key=lambda entry: order[entry.axis])
    return tuple(fired)


def _subject_directives(profile: QueryRiskProfile) -> tuple[SubjectDirective, ...]:
    """Compile losslessness instructions from the M9 surface features.

    Structural only. A qualifier is preserved; it is never interpreted, resolved
    against any external source, or used to infer anything about the entity.
    """
    surface = profile.subject_surface
    return tuple(
        SubjectDirective(kind=rule.kind, instruction=rule.instruction)
        for rule in SUBJECT_DIRECTIVE_RULES
        if not rule.feature or getattr(surface, rule.feature)
    )


def _query_specification(spec: RelationPromptSpec) -> QuerySpecification:
    """The step-back layer: a search specification, never a factual answer.

    Contract-derived and deterministic. No model produces it, and it contains no
    pseudo-context - generating that from these cues is Module 11's job.
    """
    return QuerySpecification(
        relation_focus=spec.relation_focus,
        semantic_question=spec.semantic_question,
        abstraction_cues=tuple(spec.abstraction_cues),
    )


@dataclass(frozen=True)
class PromptCompilerConfig:
    """Module 10 configuration.

    ``shadow`` is the only supported mode in this milestone and is enforced:
    asking for anything else fails loudly rather than pretending M11-M21 exist.
    """

    enabled: bool = False
    mode: str = "shadow"
    compiler_version: str = COMPILER_VERSION
    #: Optional per-relation prompt-language overrides, validated at construction.
    relation_prompts: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "PromptCompilerConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload) - {"enabled", "mode", "compiler_version", "relation_prompts"}
        )
        if unknown:
            raise ValueError(
                f"unknown query_intelligence.prompt_compiler key(s) {unknown}; expected "
                "enabled, mode, compiler_version, relation_prompts"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            compiler_version=str(payload.get("compiler_version", COMPILER_VERSION)),
            relation_prompts=payload.get("relation_prompts"),
        )


class PromptProgramCompiler:
    """Module 10. Deterministic, closed-book, non-neural.

    Construction validates the declaration registry against Modules 0 and 9, so
    a prompt spec that contradicts a contract stops the run before any model
    loads.
    """

    #: Modes this milestone implements. ``shadow`` means: compile the program,
    #: let nothing consume it.
    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: PromptCompilerConfig | None = None) -> None:
        self.config = config or PromptCompilerConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported prompt compiler mode {self.config.mode!r}; this "
                f"milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Executing a prompt program is Module 11's job and Module 11 is "
                "not implemented."
            )
        check_prompt_registry_consistency()
        self.prompt_specs: dict[str, RelationPromptSpec] = prompt_specs_from_mapping(
            self.config.relation_prompts
        )
        if self.config.relation_prompts:
            _check_resolved_specs(self.prompt_specs)

    @property
    def compiler_version(self) -> str:
        return self.config.compiler_version

    def compile(
        self,
        query: Query,
        contract: RelationContract | None = None,
        profile: QueryRiskProfile | None = None,
    ) -> PromptProgram:
        """Compile one prompt program.

        ``profile`` is required: M10 consumes Module 9 and never reconstructs
        it. ``contract`` is a shortcut for callers that already resolved
        Modules 0 and 1.

        Raises:
            ValueError: if no profile is supplied, or if the query, contract and
                profile disagree on relation, subject, row index or programme.
            UnknownRelationError: for a relation with no contract.
            UnknownRelationPromptError: for a relation with no prompt spec.
        """
        if profile is None:
            raise ValueError(
                "Module 10 requires a Module 9 QueryRiskProfile. The stack is "
                "M1 -> M9 -> M10; compiling without a profile would mean an "
                "unprofiled program built by a second, invisible profiler."
            )
        if contract is None:
            _, contract = compile_query(query.subject, query.relation, query.row_index)

        self._check_agreement(query, contract, profile)

        spec = self.prompt_specs.get(contract.relation) or get_prompt_spec(contract.relation)
        # Module 1 owns the programme; it is read, never chosen.
        routed = contract.program

        return PromptProgram(
            compiler_version=self.compiler_version,
            profile_version=profile.profile_version,
            relation=contract.relation,
            subject=query.subject,
            row_index=query.row_index,
            program_type=routed.program_type,
            cardinality_regime=profile.cardinality_regime,
            task_semantics=TaskSemantics(
                relation=contract.relation,
                definition=contract.definition,
                answer_type=contract.answer_type,
                relation_focus=spec.relation_focus,
            ),
            answer_schema=_answer_schema(contract, profile),
            # Verbatim from Module 0. A second wording would be a second contract.
            positive_constraints=tuple(contract.positive_rules),
            negative_constraints=tuple(contract.hard_negative_rules),
            semantic_cues=tuple(spec.semantic_cues),
            negative_anchors=tuple(spec.negative_anchors),
            risk_directives=_risk_directives(profile),
            subject_directives=_subject_directives(profile),
            query_specification=_query_specification(spec),
            specialist_hint=profile.specialist_hint,
        )

    def compile_all(
        self,
        pairs: Iterable[tuple[Query, RelationContract | None, QueryRiskProfile]],
    ) -> list[PromptProgram]:
        """Compile many programs, preserving order."""
        return [self.compile(query, contract, profile) for query, contract, profile in pairs]

    @staticmethod
    def _check_agreement(
        query: Query, contract: RelationContract, profile: QueryRiskProfile
    ) -> None:
        """Refuse to compile from inputs that disagree about which query this is."""
        problems: list[str] = []
        if contract.relation != query.relation:
            problems.append(
                f"contract is for {contract.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if profile.relation != query.relation:
            problems.append(
                f"profile is for {profile.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if profile.subject != query.subject:
            problems.append(
                f"profile subject {profile.subject!r} != query subject {query.subject!r}"
            )
        if profile.row_index != query.row_index:
            problems.append(
                f"profile row_index {profile.row_index} != query row_index {query.row_index}"
            )
        if profile.program_type is not contract.program_type:
            problems.append(
                f"profile programme {profile.program_type.value} != routed programme "
                f"{contract.program_type.value}"
            )
        if profile.cardinality_regime is not cardinality_regime_for(contract.program_type):
            problems.append(
                f"profile cardinality regime {profile.cardinality_regime.value} does not "
                f"follow from the routed programme {contract.program_type.value}"
            )
        if problems:
            raise ValueError(
                "Module 10 inputs disagree about the query:\n  - " + "\n  - ".join(problems)
            )


def _check_resolved_specs(resolved: Mapping[str, RelationPromptSpec]) -> None:
    """Re-run the registry invariants against an override-modified table."""
    from cover_kbc.query_intelligence import prompt_registry

    original = prompt_registry.RELATION_PROMPT_SPECS
    prompt_registry.RELATION_PROMPT_SPECS = dict(resolved)
    try:
        check_prompt_registry_consistency()
    finally:
        prompt_registry.RELATION_PROMPT_SPECS = original


def build_prompt_compiler(
    config: Mapping[str, Any] | None, *, profiler_enabled: bool
) -> PromptProgramCompiler | None:
    """Build the compiler from a top-level ``query_intelligence`` config block.

    Returns ``None`` when M10 is not enabled, which is the default and is the
    pre-Module-10 code path exactly.

    Raises:
        ValueError: if M10 is enabled while M9 is not. A prompt program without
            a risk profile is not a degraded program, it is a different module,
            so this fails loudly rather than silently compiling an unprofiled one.
    """
    block = dict(config or {})
    compiler_config = PromptCompilerConfig.from_mapping(block.get("prompt_compiler"))
    if not compiler_config.enabled:
        return None
    if not profiler_enabled:
        raise ValueError(
            "query_intelligence.prompt_compiler is enabled but "
            "query_intelligence.profiler is not. Module 10 consumes Module 9's "
            "QueryRiskProfile; enable the profiler or disable the prompt compiler."
        )
    return PromptProgramCompiler(compiler_config)


def program_preview(program: PromptProgram, fragments: Sequence[str] | None = None) -> str:
    """A deterministic, human-readable rendering, for audits only.

    Optional and never persisted by default: the structured program is the
    primary artefact, and duplicating it as prose in every record would bloat
    the file without adding information.
    """
    parts = program.fragments()
    order = fragments or (
        "task",
        "positive_constraints",
        "negative_constraints",
        "semantic_cues",
        "negative_anchors",
        "risk_directives",
        "subject_directives",
        "query_specification",
        "output_contract",
    )
    return "\n\n".join(parts[name] for name in order if name in parts)
