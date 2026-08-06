"""Layer 5 - Module 19, the Coverage Gap and Missingness Estimator.

Deliberately **not** named ``coverage``: that module is Module 6's RCSE, which
this milestone leaves untouched down to its imports. M19 is the upgraded shadow
estimator beside RCSE, not a replacement for it.
"""

from cover_kbc.coverage_gap.gap_types import (
    ESTIMATOR_VERSION as ESTIMATOR_VERSION,
    CoverageGapError as CoverageGapError,
    CoverageGapState as CoverageGapState,
    FacetCoverage as FacetCoverage,
)
from cover_kbc.coverage_gap.missingness import (
    CoverageGapConfig as CoverageGapConfig,
    CoverageGapEstimator as CoverageGapEstimator,
    build_coverage_gap_estimator as build_coverage_gap_estimator,
)

__all__ = [
    "ESTIMATOR_VERSION",
    "CoverageGapConfig",
    "CoverageGapError",
    "CoverageGapEstimator",
    "CoverageGapState",
    "FacetCoverage",
    "build_coverage_gap_estimator",
]
