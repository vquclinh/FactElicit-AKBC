"""V3.1 score recovery: finalization-safe fixes, and prototypes that are not.

Audit 0076. Two tracks, kept apart on purpose:

* :mod:`cover_kbc.v3_1.retention`, :mod:`cover_kbc.v3_1.output_repair`,
  :mod:`cover_kbc.v3_1.entity_finalization` and
  :mod:`cover_kbc.v3_1.numeric_recovery` run at Module 8 or later and preserve
  the Audit 0073 M20/M21 calibration - see :mod:`cover_kbc.v3_1.compatibility`
  for the argument;
* :mod:`cover_kbc.v3_1.prompts` changes what the models are asked, and
  therefore does not.

Nothing here is active by default: every flag on :class:`V31Config` is off, so
importing this package cannot change a prediction.
"""

from cover_kbc.v3_1.acquisition_parser import (
    expand_scientific_notation,
    has_scientific_notation,
    parse_numbers_with_exponents,
)
from cover_kbc.v3_1.compatibility import (
    BY_FEATURE,
    INTERVENTIONS,
    InterventionRecord,
    REVIEW,
    SAFE,
    compatibility_of,
)
from cover_kbc.v3_1.config import (
    DEFAULT_V31,
    V31AggressiveConfig,
    V31Config,
    V31SafeConfig,
)
from cover_kbc.v3_1.entity_finalization import (
    STOCK_RELATION,
    apply_support_dominance,
    rank_entity_candidates,
    reject_structurally_invalid_listings,
)
from cover_kbc.v3_1.live_prompts import (
    LIVE_PROMPT_VERSION,
    NO_INSTRUCTIONS,
    RelationInstructions,
    live_prompt_inventory,
)
from cover_kbc.v3_1.numeric_recovery import (
    canonicalize_numeric_output,
    explicit_unit_of,
    has_explicit_unit,
    parse_scalar,
    to_canonical_km2,
)
from cover_kbc.v3_1.output_repair import (
    is_abstention,
    is_bucket_label,
    repair_value,
    repair_values,
)
from cover_kbc.v3_1.promotion import GATES, PromotionGate, RECOMMENDED_ORDER, gate_for
from cover_kbc.v3_1.retention import (
    is_accepted,
    is_contradicted,
    is_retainable,
    retained_pool,
)


__all__ = [
    "BY_FEATURE",
    "DEFAULT_V31",
    "GATES",
    "INTERVENTIONS",
    "InterventionRecord",
    "LIVE_PROMPT_VERSION",
    "NO_INSTRUCTIONS",
    "PromotionGate",
    "RECOMMENDED_ORDER",
    "REVIEW",
    "RelationInstructions",
    "SAFE",
    "STOCK_RELATION",
    "V31AggressiveConfig",
    "V31Config",
    "V31SafeConfig",
    "apply_support_dominance",
    "canonicalize_numeric_output",
    "compatibility_of",
    "expand_scientific_notation",
    "explicit_unit_of",
    "gate_for",
    "has_explicit_unit",
    "has_scientific_notation",
    "is_abstention",
    "is_accepted",
    "is_bucket_label",
    "is_contradicted",
    "is_retainable",
    "live_prompt_inventory",
    "parse_numbers_with_exponents",
    "parse_scalar",
    "rank_entity_candidates",
    "reject_structurally_invalid_listings",
    "repair_value",
    "repair_values",
    "retained_pool",
    "to_canonical_km2",
]
