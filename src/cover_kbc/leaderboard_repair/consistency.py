"""L8 cross-query consistency for the V3.2 repair stack."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import RowRepairRecord
from cover_kbc.leaderboard_repair.util import BORDERS, STOCK, dedupe_aliases


def apply_l8_consistency(
    predictions: Sequence[Prediction],
    records: dict[tuple[str, str], RowRepairRecord],
    callers: dict[tuple[str, str], RepairCaller],
    config: LeaderboardRepairConfig,
) -> list[Prediction]:
    if not config.features.l8_consistency:
        return list(predictions)
    repaired = [
        replace(
            p,
            object_entities=_dedupe_for_relation(
                p.relation,
                p.object_entities,
                stock_enabled=config.features.l8_stock_consistency,
            ),
        )
        for p in predictions
    ]
    if config.features.border_reciprocity:
        repaired = _repair_border_reciprocity(repaired, records, callers)
    return repaired


def _dedupe_for_relation(
    relation: str, values: Sequence[str], *, stock_enabled: bool = True
) -> list[str]:
    if relation == STOCK and not stock_enabled:
        return list(values)
    if relation in CONTRACTS and CONTRACTS[relation].output_type.value == "ENTITY":
        return dedupe_aliases(values)
    return list(values)


def _repair_border_reciprocity(
    predictions: Sequence[Prediction],
    records: dict[tuple[str, str], RowRepairRecord],
    callers: dict[tuple[str, str], RepairCaller],
) -> list[Prediction]:
    by_subject = {
        strict_key(prediction.subject): prediction
        for prediction in predictions
        if prediction.relation == BORDERS
    }
    updates = {prediction.subject: list(prediction.object_entities)
               for prediction in predictions if prediction.relation == BORDERS}
    conflicts: list[tuple[Prediction, str, Prediction]] = []

    for prediction in predictions:
        if prediction.relation != BORDERS:
            continue
        for neighbor in prediction.object_entities:
            reverse = by_subject.get(strict_key(neighbor))
            if reverse is None:
                continue
            if strict_key(prediction.subject) not in {
                strict_key(value) for value in reverse.object_entities
            }:
                conflicts.append((prediction, neighbor, reverse))

    for source, neighbor, reverse in conflicts:
        key = (source.subject, source.relation)
        record = records[key]
        record.add_decision(
            "BORDER_RECIPROCITY_CONFLICT",
            "detected_one_way_border",
            source=source.subject,
            neighbor=neighbor,
            reverse_subject=reverse.subject,
        )
        caller = callers[key]
        if not caller.row_budget.can_spend():
            record.skipped.append("BORDER_RECIPROCITY_CONFLICT: budget exhausted")
            continue
        prompt = (
            f"Exact pair: {source.subject} and {neighbor}\n"
            "Do these two share an actual terrestrial land boundary? Exclude "
            "maritime adjacency, nearby islands, bridge/causeway-only links, and "
            "political association without terrestrial boundary. Return VALID, "
            "INVALID, or UNKNOWN with a brief reason."
        )
        text = caller.generate(
            role="verifier",
            layer="L8",
            feature="BORDER_RECIPROCITY_CONFLICT",
            prompt=prompt,
            view_id="l8_border_reciprocity",
            max_new_tokens=96,
        )
        verdict = (text or "").strip().split(None, 1)[0].strip(":,.;").upper()
        if verdict == "VALID":
            current = updates.get(reverse.subject, list(reverse.object_entities))
            if strict_key(source.subject) not in {strict_key(v) for v in current}:
                current.append(source.subject)
                updates[reverse.subject] = current
            record.add_decision(
                "BORDER_RECIPROCITY_CONFLICT",
                "validated_made_reciprocal",
                added_to=reverse.subject,
            )
        elif verdict == "INVALID":
            current = updates.get(source.subject, list(source.object_entities))
            updates[source.subject] = [
                value for value in current if strict_key(value) != strict_key(neighbor)
            ]
            record.add_decision(
                "BORDER_RECIPROCITY_CONFLICT",
                "invalid_removed_bad_direction",
                removed_from=source.subject,
                removed=neighbor,
            )
        else:
            record.add_decision(
                "BORDER_RECIPROCITY_CONFLICT",
                "unknown_preserved_existing_state",
                preserved=source.subject,
                neighbor=neighbor,
            )

    out = []
    for prediction in predictions:
        if prediction.relation == BORDERS:
            out.append(replace(prediction, object_entities=dedupe_aliases(
                updates.get(prediction.subject, prediction.object_entities))))
        else:
            out.append(prediction)
    return out
