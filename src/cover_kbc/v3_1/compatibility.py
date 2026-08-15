"""What each V3.1 intervention is allowed to claim about M20/M21 calibration.

The classification is not a label somebody wrote down; it follows from *where*
the code runs. This module states the argument in a form tests assert against,
so a future intervention cannot quietly inherit "safe" by being added to the
safe config.

The Class A argument
--------------------
For one query the pipeline runs, in order:

1. ``enumerate_query`` / ``verify_graph`` - acquisition and verification;
2. ``decide_graph`` -> ``_run_consensus`` (Module 16), then
   ``_run_v3_control_loop`` (Modules 20/21 and V3 action execution);
3. ``decide_graph`` -> ``selection.finalize`` (Module 8);
4. ``_observe_v3_core`` - observation-only telemetry.

The V3 hypothesis graph that Module 21 reads is built at step 2 by
``pipeline._v3_hypothesis_graph``, which passes ``prediction=None``. The only
``build_hypothesis_graph`` call that receives a prediction is step 4, whose
result goes to ``v3_core_results`` for persistence and is never read back
during inference.

Therefore a change confined to step 3 cannot alter V3 action eligibility,
action execution, action cost, state transitions, hypothesis construction,
Module 21 input state, or Module 20 budget behaviour. It can only alter
``Prediction.object_entities`` and the ``emitted`` flags step 4 records.

Every Class A feature below is confined to ``cover_kbc.selection`` or a module
it calls at finalization. Every Class B feature changes a prompt, a recall
behaviour or an action, all of which live at step 1 or 2 and therefore change
the action-effect distribution the Audit 0073 calibration was derived from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Verdict strings a readiness report may print.
SAFE = "SAFE_WITH_EXISTING_CALIBRATION"
REVIEW = "CALIBRATION_REVIEW_REQUIRED"


@dataclass(frozen=True)
class InterventionRecord:
    """One V3.1 intervention and the reason for its classification."""

    feature: str
    #: "safe" (Class A) or "aggressive" (Class B).
    track: str
    #: The pipeline stage the code runs at.
    stage: str
    compatibility: str
    #: The gold-independent predicate the production rule evaluates.
    production_predicate: str
    #: What TRAIN evidence motivated it (never what it keys on).
    train_discovery: str


#: Every implemented intervention, with its classification argument.
INTERVENTIONS: tuple[InterventionRecord, ...] = (
    InterventionRecord(
        feature="final_candidate_retention",
        track="safe",
        stage="Module 8 numeric cluster selection (selection.select_numeric_*)",
        compatibility=SAFE,
        production_predicate=(
            "if the winning numeric cluster carries no ACCEPTED candidate, "
            "re-run the unchanged cluster ordering over clusters that carry an "
            "ACCEPTED, non-INVALID candidate; emit nothing if none qualifies"
        ),
        train_discovery=(
            "55 TRAIN rows recorded a non-empty CONTROL_SURVIVED stage and an "
            "empty FINAL_EMITTED, all hasArea; cluster ties are broken by "
            "smallest representative, which ignores acceptance"
        ),
    ),
    InterventionRecord(
        feature="enumeration_label_repair",
        track="safe",
        stage="Module 8 finalization (selection.finalize output construction)",
        compatibility=SAFE,
        production_predicate=(
            "if a final value splits as '<temporal or category bucket label>: "
            "<payload>', drop it when the payload normalises to an abstention "
            "token, otherwise reduce it to the payload; deduplicate on strict_key"
        ),
        train_discovery=(
            "76 awardWonBy values emitted whole enumeration lines such as "
            "'Groups: NONE' and '1990s: Alan Shearer'; the existing "
            "_NEVER_AN_OBJECT guard is defeated by the label prefix"
        ),
    ),
    InterventionRecord(
        feature="stock_support_dominance",
        track="safe",
        stage="Module 8 entity selection (selection.select_small_set)",
        compatibility=SAFE,
        production_predicate=(
            "for companyTradesAtStockExchange only, if accepted listings differ "
            "in independent acquisition support, keep those at the maximum; on a "
            "tie keep all"
        ),
        train_discovery=(
            "95 stock false positives against 33 false negatives; on rows where "
            "supports differ, the maximally supported listing is the gold one"
        ),
    ),
    InterventionRecord(
        feature="stock_structural_validation",
        track="safe",
        stage="Module 8 entity selection (selection.select_small_set)",
        compatibility=SAFE,
        production_predicate=(
            "for companyTradesAtStockExchange only, drop a listing whose "
            "strict_key equals the subject's, or is a status/abstention token"
        ),
        train_discovery=(
            "guardrail: 0 such emissions observed on TRAIN; ticker rejection was "
            "evaluated and refused as structurally undecidable"
        ),
    ),
    InterventionRecord(
        feature="numeric_output_canonicalization",
        track="safe",
        stage="Module 8 finalization (selection.finalize output construction)",
        compatibility=SAFE,
        production_predicate=(
            "re-serialise an emitted numeral through the official-parser-safe "
            "formatter, converting units only when an explicit unit token is "
            "present in the value; never convert a bare number"
        ),
        train_discovery=(
            "guardrail: hasArea has only 2 scale-like errors and 1 near-tolerance "
            "row, so formatting is not the dominant failure; 0 TRAIN rows change"
        ),
    ),
    InterventionRecord(
        feature="capacity_definition_prompt",
        track="aggressive",
        stage="acquisition prompt (Module 2 enumerator view)",
        compatibility=REVIEW,
        production_predicate=(
            "relation-conditioned enumerator instruction distinguishing total / "
            "seated / standing / configuration-specific capacity"
        ),
        train_discovery="92/100 hasCapacity rows are L1 never-recalled",
    ),
    InterventionRecord(
        feature="city_of_death_contrast_prompt",
        track="aggressive",
        stage="verification prompt (Module 17 specialist verifier)",
        compatibility=REVIEW,
        production_predicate=(
            "contrastive verification of the exact death-location attribute "
            "against birth city, burial place, residence and country"
        ),
        train_discovery="51 L1 rows plus 16 wrong-city false-positive rows",
    ),
    InterventionRecord(
        feature="stock_listing_entity_prompt",
        track="aggressive",
        stage="acquisition prompt (Module 2 enumerator view)",
        compatibility=REVIEW,
        production_predicate=(
            "enumerator instruction naming the listing venue as the answer type, "
            "excluding tickers, indices and the company itself"
        ),
        train_discovery="28 L1 rows and 54 over-generated rows",
    ),
    InterventionRecord(
        feature="scientific_notation_acquisition",
        track="aggressive",
        stage="acquisition parsing (Module 2 -> Module 3 candidate values)",
        compatibility=REVIEW,
        production_predicate=(
            "expand a scientific-notation numeral to plain decimal before the "
            "shared parser reads it, so its unit suffix is still adjacent"
        ),
        train_discovery=(
            "audit 0076: parse_numbers('7.5e4 m2') yields 7.5 with no unit "
            "because the shared number regex stops before the exponent"
        ),
    ),
    InterventionRecord(
        feature="award_expansion_and_fp_cap",
        track="aggressive",
        stage="V3 action semantics (SET_EXPANSION continuation) and M21 stopping",
        compatibility=REVIEW,
        production_predicate=(
            "continue expansion while new independently supported members "
            "appear; cap unsupported additions after expansion"
        ),
        train_discovery="all 10 award rows both under- and over-enumerated",
    ),
)

#: Feature name -> record, for lookups and readiness reports.
BY_FEATURE: Mapping[str, InterventionRecord] = {
    record.feature: record for record in INTERVENTIONS
}


def compatibility_of(feature: str) -> str:
    """The compatibility verdict for one feature name."""
    try:
        return BY_FEATURE[feature].compatibility
    except KeyError:
        raise KeyError(
            f"{feature!r} is not a declared V3.1 intervention. Add an "
            "InterventionRecord stating where it runs before enabling it."
        ) from None


__all__ = [
    "BY_FEATURE",
    "INTERVENTIONS",
    "InterventionRecord",
    "REVIEW",
    "SAFE",
    "compatibility_of",
]
