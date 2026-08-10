"""When a Class-B feature has earned a full recollection.

Written *before* the diagnostics run, on purpose. A gate chosen after seeing the
numbers is not a gate, and the cost being guarded here is real: a full TRAIN
recollection plus an M21 re-derivation, which is the expensive path.

No gate hard-codes an expected gain. Each states the *shape* of an improvement
that would justify the spend, in terms the targeted evaluator already reports,
and each names what would make it a false positive. A feature that misses its
gate is not condemned - it goes back to prompt work, which is cheap.

The asymmetry across relations is deliberate, because the failures are not alike:
``hasCapacity`` is starved of recall and can afford to trade precision for it;
``companyTradesAtStockExchange`` is drowning in false positives and cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PromotionGate:
    """The condition under which one Class-B feature earns a recollection."""

    feature: str
    relation: str
    #: Authoritative TRAIN baseline this is measured against (audit 0075).
    baseline_macro_f1: float
    #: Rows the targeted diagnostic runs.
    diagnostic_rows: int
    #: The promotion rule, in the evaluator's own reported quantities.
    rule: str
    #: What would make an apparent pass spurious.
    false_positive_check: str


CAPACITY_GATE = PromotionGate(
    feature="capacity_definition_prompt",
    relation="hasCapacity",
    baseline_macro_f1=0.080,
    diagnostic_rows=100,
    rule=(
        "PROMOTE if relation macro-F1 rises materially above 0.080, OR if "
        "gold_like_candidate_rows rises substantially while macro-precision "
        "does not collapse. The second clause matters because capacity is "
        "recall-starved: 92/100 rows never recalled the answer at all, so a "
        "prompt that surfaces the canonical figure as a *candidate* has done "
        "the hard part even if selection has not caught up yet, and selection "
        "is cheap to fix afterwards."
    ),
    false_positive_check=(
        "REJECT the pass if mean_candidates_per_row rises without "
        "gold_like_candidate_rows rising with it - that is the prompt "
        "manufacturing numeric alternatives rather than recalling the right "
        "one, and it will cost precision at full scale. Also reject if the "
        "10x/100x ratio buckets grow: a scale error is not a recall win."
    ),
)

CITY_GATE = PromotionGate(
    feature="city_of_death_contrast_prompt",
    relation="personHasCityOfDeath",
    baseline_macro_f1=0.390,
    diagnostic_rows=100,
    rule=(
        "PROMOTE if exact-city recall rises, OR if false_positives fall "
        "materially with recall held. This is a verifier-side instruction, so "
        "the expected mechanism is rejecting wrong-attribute candidates "
        "(birthplace, burial, country) rather than finding new ones."
    ),
    false_positive_check=(
        "REJECT if empty_rows rises sharply: a verifier told to be strict can "
        "buy precision by rejecting everything, which reads as improved "
        "macro-precision on an empty prediction and gains no score."
    ),
)

STOCK_GATE = PromotionGate(
    feature="stock_listing_entity_prompt",
    relation="companyTradesAtStockExchange",
    baseline_macro_f1=0.529,
    diagnostic_rows=100,
    rule=(
        "PROMOTE only if the false-positive reduction outweighs any recall "
        "loss - false_positives down by more than false_negatives up, with "
        "relation macro-F1 at or above 0.529. Stock carries 95 FP against 33 "
        "FN, so precision is the binding constraint."
    ),
    false_positive_check=(
        "REJECT if the FP reduction comes from mean_predictions collapsing "
        "toward 1: multi-listed companies are real, and audit 0075 forbids a "
        "global cardinality-1 rule. Check the prediction_count_distribution, "
        "not just the totals."
    ),
)

AWARD_GATE = PromotionGate(
    feature="award_expansion_and_fp_cap",
    relation="awardWonBy",
    baseline_macro_f1=0.389,
    diagnostic_rows=10,
    rule=(
        "PROMOTE if recall rises without prediction cardinality exploding - "
        "true_positives up with false_positives flat or down. Award is both "
        "under- and over-enumerated (418 FN, 266 FP), so only a joint move "
        "counts."
    ),
    false_positive_check=(
        "Treat any result on 10 rows as weak evidence. A single row swings "
        "relation macro-F1 by 0.1, so require the direction to be consistent "
        "across rows rather than driven by one, and prefer the micro counts."
    ),
)

PARSER_GATE = PromotionGate(
    feature="scientific_notation_acquisition",
    relation="hasArea",
    baseline_macro_f1=0.240,
    diagnostic_rows=100,
    rule=(
        "PROMOTE if any TRAIN row's candidate values actually change, and the "
        "change is toward the gold scale. This is a correctness fix, so the "
        "bar is 'it fires and helps', not 'it wins big'. If it fires on zero "
        "rows, it stays available but unpromoted - it is still a real gap and "
        "may fire on TEST."
    ),
    false_positive_check=(
        "REJECT if ordinary numerals change at all. The rewrite is confined to "
        "exponent forms; any other movement means the expansion is too greedy."
    ),
)


GATES: tuple[PromotionGate, ...] = (
    CAPACITY_GATE, CITY_GATE, STOCK_GATE, AWARD_GATE, PARSER_GATE,
)

BY_FEATURE: Mapping[str, PromotionGate] = {gate.feature: gate for gate in GATES}

#: Order to spend GPU time in, most theoretical recovery first. Capacity is the
#: weakest relation in the benchmark (0.080) and the largest single opportunity.
RECOMMENDED_ORDER: tuple[str, ...] = (
    "capacity_definition_prompt",
    "city_of_death_contrast_prompt",
    "stock_listing_entity_prompt",
    "award_expansion_and_fp_cap",
    "scientific_notation_acquisition",
)


def gate_for(feature: str) -> PromotionGate:
    try:
        return BY_FEATURE[feature]
    except KeyError:
        raise KeyError(
            f"{feature!r} has no promotion gate. A Class-B feature without a "
            "gate cannot be promoted to a full recollection."
        ) from None


__all__ = [
    "AWARD_GATE",
    "BY_FEATURE",
    "CAPACITY_GATE",
    "CITY_GATE",
    "GATES",
    "PARSER_GATE",
    "PromotionGate",
    "RECOMMENDED_ORDER",
    "STOCK_GATE",
    "gate_for",
]
