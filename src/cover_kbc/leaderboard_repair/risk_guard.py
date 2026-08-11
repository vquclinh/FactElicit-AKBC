"""L9 relation-specific final risk guard."""

from __future__ import annotations

from dataclasses import replace

from cover_kbc.normalization.numeric import parse_numbers
from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.types import RowRepairRecord
from cover_kbc.leaderboard_repair.util import (
    AREA,
    AWARD,
    BORDERS,
    CAPACITY,
    CITY,
    STOCK,
    dedupe_aliases,
    normalize_award_metadata,
    stock_wrong_type_reason,
)


def apply_l9_guard(prediction: Prediction, record: RowRepairRecord) -> Prediction:
    relation = prediction.relation
    values = list(prediction.object_entities)
    before = list(values)
    if relation == BORDERS:
        values = [
            value for value in dedupe_aliases(values)
            if strict_key(value) and strict_key(value) != strict_key(prediction.subject)
        ]
    elif relation == STOCK:
        values = [
            value for value in dedupe_aliases(values)
            if stock_wrong_type_reason(value, prediction.subject) is None
        ]
    elif relation == CITY:
        values = dedupe_aliases(values)[:1]
    elif relation in (AREA, CAPACITY):
        numeric = []
        for value in values:
            parsed = parse_numbers(value)
            if parsed:
                numeric.append(value)
        values = numeric[:1]
    elif relation == AWARD:
        normalized = [
            item for item in (normalize_award_metadata(value) for value in values)
            if item
        ]
        values = dedupe_aliases(normalized)
    if values != before:
        record.add_decision(
            "L9RelationSpecificFinalRiskGuard",
            "relation_guard_adjusted_output",
            before=before,
            after=values,
        )
    return replace(prediction, object_entities=values)
