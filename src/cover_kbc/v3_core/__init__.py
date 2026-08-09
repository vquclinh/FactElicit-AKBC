"""COVER-KBC V3 relation-conditioned hypothesis-search core.

V3 shadow remains inert analysis. In TRAIN-collection mode the same typed
relation profiles can now schedule bounded pre-M8 actions through the existing
M12-M18 owners. V3 production activation is representable but still fails
closed unless separate matching V3 calibration artifacts are supplied; official
TEST remains not ready.
"""

from cover_kbc.v3_core.config import V3CoreConfig, V3CoreMode
from cover_kbc.v3_core.execution import (
    V3_ACTION_EFFECT_VERSION,
    V3ActionCandidate,
    V3ActionExecution,
    build_v3_action_catalog,
    execute_v3_action,
)
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
    "V3ActionCandidate",
    "V3ActionExecution",
    "V3_ACTION_EFFECT_VERSION",
    "V3CoreConfig",
    "V3CoreMode",
    "action_region_for_failure_state",
    "award_promote_suppress_round",
    "build_hypothesis_graph",
    "build_v3_action_catalog",
    "capacity_qualifier_from_text",
    "classify_death_slot",
    "classify_listing_disambiguation",
    "independent_prompt_support",
    "legal_action_families",
    "prompt_family_for_group",
    "render_seen_set",
    "execute_v3_action",
]
