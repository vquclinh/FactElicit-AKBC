"""Query Intelligence layer - Modules 9 and 10, and later Modules 11-21.

Implemented: **M9 Risk & Difficulty Profiler** and **M10 Prompt Program
Compiler**. Nothing here is a placeholder for the unimplemented modules:
parametric retrieval, the specialists, the verifier suite and the planner get
their files when they get their milestones.
"""

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
