"""Query Intelligence layer - Module 9 and, later, Modules 10-21.

Only **M9 Risk & Difficulty Profiler** is implemented. Nothing here is a
placeholder for the unimplemented modules: the prompt compiler, parametric
retrieval, the specialists and the planner get their files when they get their
milestones.
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
    "PROFILE_VERSION",
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
