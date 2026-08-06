"""Query Intelligence layer - Modules 9, 10 and 11; later Modules 12-21.

Implemented: **M9 Risk & Difficulty Profiler**, **M10 Prompt Program Compiler**
and **M11 Closed-Book Parametric Retrieval**. Nothing here is a placeholder for
the unimplemented modules: the specialists, the consensus engine, the verifier
suite, the gap estimator, the scheduler and the planner get their files when
they get their milestones.

**Module 11 is exported lazily.** Modules 9 and 10 are pure deterministic
computation and must not drag a model loader into a process that only wants to
profile a query; Module 11, by design, uses the runtime abstraction. Eagerly
re-exporting it here would make ``import cover_kbc.query_intelligence.profiler``
pull in ``cover_kbc.models.registry`` as a side effect, and the layering would
stop being checkable. The ``__getattr__`` below keeps M11 reachable by name
while deferring its import to first use.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed at runtime
    from cover_kbc.query_intelligence.parametric_retrieval import (
        DEFAULT_OPERATIONS,
        OPERATION_SPECS,
        RETRIEVAL_VERSION,
        ParametricRetriever,
        RetrievalConfig,
        RetrievalError,
        build_parametric_retriever,
        classify_output,
        operation_catalogue,
        program_digest,
    )
    from cover_kbc.query_intelligence.retrieval_templates import (
        RETRIEVAL_SYSTEM_PROMPT,
        render_pseudo_memory,
        render_query_rewrite,
        render_self_ask,
    )
    from cover_kbc.query_intelligence.retrieval_types import (
        ExpectedOutputKind,
        MemorySource,
        ParametricIndependenceGroup,
        ParametricMemoryRecord,
        ParametricRecallOperation,
        ParametricRetrievalPlan,
        ParametricRetrievalResult,
        ParseStatus,
        RecallOperationKind,
        prompt_digest,
    )


from cover_kbc.query_intelligence.priors import (
    PROFILE_VERSION,
    RELATION_RISK_PRIORS,
    RelationRiskPriors,
    UnknownRelationPriorError,
    check_priors_consistency,
    get_priors,
    priors_table,
)
from cover_kbc.query_intelligence.prompt_compiler import (
    PromptCompilerConfig,
    PromptProgramCompiler,
    build_prompt_compiler,
    program_preview,
)
from cover_kbc.query_intelligence.prompt_registry import (
    COMPILER_VERSION,
    DIRECTIVE_RULES,
    RELATION_PROMPT_SPECS,
    SUBJECT_DIRECTIVE_RULES,
    RelationPromptSpec,
    UnknownRelationPromptError,
    check_prompt_registry_consistency,
    get_prompt_spec,
    prompt_registry_table,
)
from cover_kbc.query_intelligence.prompt_types import (
    AnswerSchema,
    DirectiveKind,
    NumericKind,
    ObjectKind,
    PromptProgram,
    QuerySpecification,
    RiskDirective,
    SubjectDirective,
    SubjectDirectiveKind,
    TaskSemantics,
)
from cover_kbc.query_intelligence.profiler import (
    ProfilerConfig,
    QueryProfiler,
    build_profiler,
    subject_surface_features,
)
from cover_kbc.query_intelligence.types import (
    RISK_AXES,
    CardinalityRegime,
    QueryRiskProfile,
    RiskLevel,
    SpecialistHint,
    SubjectSurfaceFeatures,
)

__all__ = [
    "AnswerSchema",
    "DEFAULT_OPERATIONS",
    "ExpectedOutputKind",
    "MemorySource",
    "OPERATION_SPECS",
    "ParametricIndependenceGroup",
    "ParametricMemoryRecord",
    "ParametricRecallOperation",
    "ParametricRetrievalPlan",
    "ParametricRetrievalResult",
    "ParametricRetriever",
    "ParseStatus",
    "RETRIEVAL_SYSTEM_PROMPT",
    "RETRIEVAL_VERSION",
    "RecallOperationKind",
    "RetrievalConfig",
    "RetrievalError",
    "build_parametric_retriever",
    "classify_output",
    "operation_catalogue",
    "program_digest",
    "prompt_digest",
    "render_pseudo_memory",
    "render_query_rewrite",
    "render_self_ask",
    "COMPILER_VERSION",
    "DIRECTIVE_RULES",
    "DirectiveKind",
    "NumericKind",
    "ObjectKind",
    "PROFILE_VERSION",
    "PromptCompilerConfig",
    "PromptProgram",
    "PromptProgramCompiler",
    "QuerySpecification",
    "RELATION_PROMPT_SPECS",
    "RelationPromptSpec",
    "RiskDirective",
    "SUBJECT_DIRECTIVE_RULES",
    "SubjectDirective",
    "SubjectDirectiveKind",
    "TaskSemantics",
    "UnknownRelationPromptError",
    "build_prompt_compiler",
    "check_prompt_registry_consistency",
    "get_prompt_spec",
    "program_preview",
    "prompt_registry_table",
    "RELATION_RISK_PRIORS",
    "RISK_AXES",
    "CardinalityRegime",
    "ProfilerConfig",
    "QueryProfiler",
    "QueryRiskProfile",
    "RelationRiskPriors",
    "RiskLevel",
    "SpecialistHint",
    "SubjectSurfaceFeatures",
    "UnknownRelationPriorError",
    "build_profiler",
    "check_priors_consistency",
    "get_priors",
    "priors_table",
    "subject_surface_features",
]


#: Module 11 names, mapped to the module that defines them. Resolved on first
#: attribute access so Modules 9 and 10 stay importable without a runtime.
_LAZY_M11: dict[str, str] = {
    name: "cover_kbc.query_intelligence.parametric_retrieval"
    for name in (
        "DEFAULT_OPERATIONS", "OPERATION_SPECS", "RETRIEVAL_VERSION",
        "ParametricRetriever", "RetrievalConfig", "RetrievalError",
        "build_parametric_retriever", "classify_output", "operation_catalogue",
        "program_digest",
    )
}
_LAZY_M11.update(
    {
        name: "cover_kbc.query_intelligence.retrieval_templates"
        for name in (
            "RETRIEVAL_SYSTEM_PROMPT", "render_pseudo_memory",
            "render_query_rewrite", "render_self_ask",
        )
    }
)
_LAZY_M11.update(
    {
        name: "cover_kbc.query_intelligence.retrieval_types"
        for name in (
            "ExpectedOutputKind", "MemorySource", "ParametricIndependenceGroup",
            "ParametricMemoryRecord", "ParametricRecallOperation",
            "ParametricRetrievalPlan", "ParametricRetrievalResult", "ParseStatus",
            "RecallOperationKind", "prompt_digest",
        )
    }
)


def __getattr__(name: str):
    """Resolve a Module 11 export on first use (PEP 562)."""
    module_path = _LAZY_M11.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_LAZY_M11))
