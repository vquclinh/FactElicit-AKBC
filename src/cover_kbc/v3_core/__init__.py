"""COVER-KBC V3 relation-conditioned hypothesis-search core.

The package is deliberately inert unless ``pipeline.v3_core.enabled`` is true.
It consumes evidence already produced by Modules 1, 12-18 and the final M8
prediction, then writes deterministic analysis/state artefacts. It does not add
models, training, web/RAG, or calibration.
"""

from cover_kbc.v3_core.config import V3CoreConfig, V3CoreMode
from cover_kbc.v3_core.hypothesis import (
    CapacityQualifier,
    ContradictionEdge,
    ContradictionKind,
    Hypothesis,
    HypothesisStatus,
    NumericHypothesisCluster,
    QueryHypothesisGraph,
    SemanticType,
    build_hypothesis_graph,
    capacity_qualifier_from_text,
)
from cover_kbc.v3_core.prompt_families import (
    PromptFamily,
    PromptSupportUnit,
    independent_prompt_support,
    prompt_family_for_group,
)
from cover_kbc.v3_core.relation_programs import (
    ActionHistory,
    AwardSearchRound,
    ListingDisambiguation,
    NoveltyChange,
    V3ActionFamily,
    action_region_for_failure_state,
    award_promote_suppress_round,
    classify_death_slot,
    classify_listing_disambiguation,
    legal_action_families,
    render_seen_set,
)

__all__ = [
    "ActionHistory",
    "AwardSearchRound",
    "CapacityQualifier",
    "ContradictionEdge",
    "ContradictionKind",
    "Hypothesis",
    "HypothesisStatus",
    "ListingDisambiguation",
    "NoveltyChange",
    "NumericHypothesisCluster",
    "PromptFamily",
    "PromptSupportUnit",
    "QueryHypothesisGraph",
    "SemanticType",
    "V3ActionFamily",
    "V3CoreConfig",
    "V3CoreMode",
    "action_region_for_failure_state",
    "award_promote_suppress_round",
    "build_hypothesis_graph",
    "capacity_qualifier_from_text",
    "classify_death_slot",
    "classify_listing_disambiguation",
    "independent_prompt_support",
    "legal_action_families",
    "prompt_family_for_group",
    "render_seen_set",
]
