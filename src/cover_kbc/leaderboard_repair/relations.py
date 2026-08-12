"""Relation-specific active leaderboard repair modules."""

from __future__ import annotations

from typing import Iterable, Sequence

from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.area import repair_area
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import CandidateSignal, RowRepairRecord
from cover_kbc.leaderboard_repair.util import AREA, AWARD, CITY, normalize_award_metadata


E1_CITY_RESCUE_FEATURE = "MistralCityEmptyRescue"


def repair_city(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    """Run the active E1 empty-row City rescue, when configured."""
    values = list(prediction.object_entities[:1])
    if not config.features.mistral_city_empty_rescue:
        return values
    return _repair_city_mistral_empty_rescue(prediction, caller, record)


def _repair_city_mistral_empty_rescue(
    prediction: Prediction,
    caller: RepairCaller,
    record: RowRepairRecord,
) -> list[str]:
    values = list(prediction.object_entities)
    if values:
        record.add_decision(
            E1_CITY_RESCUE_FEATURE,
            "bypassed_profile_d_non_empty",
            existing=list(values),
        )
        return values

    record.add_decision(E1_CITY_RESCUE_FEATURE, "eligible_empty_row")
    text = caller.generate(
        role="verifier",
        layer="E1",
        feature=E1_CITY_RESCUE_FEATURE,
        prompt=_e1_life_status_prompt(prediction.subject),
        view_id="e1_city_life_status",
        max_new_tokens=8,
    )
    if text is None:
        record.skipped.append("MistralCityEmptyRescue: budget exhausted before life status")
        return []
    life_label = _parse_e1_life_status(text)
    record.add_decision(
        E1_CITY_RESCUE_FEATURE,
        "life_status_output",
        label=life_label,
        raw=text,
    )
    if life_label != "DECEASED":
        record.add_decision(
            E1_CITY_RESCUE_FEATURE,
            "kept_empty_after_life_status",
            label=life_label,
        )
        return []

    text = caller.generate(
        role="verifier",
        layer="E1",
        feature=E1_CITY_RESCUE_FEATURE,
        prompt=_e1_city_prompt(prediction.subject),
        view_id="e1_city_of_death_recall",
        max_new_tokens=20,
    )
    if text is None:
        record.skipped.append("MistralCityEmptyRescue: budget exhausted before city recall")
        return []
    city_label, city = _parse_e1_city_output(text)
    record.add_decision(
        E1_CITY_RESCUE_FEATURE,
        "city_output",
        label=city_label,
        city=city or "",
        raw=text,
    )
    if city_label == "CITY" and city:
        record.add_decision(
            E1_CITY_RESCUE_FEATURE,
            "accepted_city",
            city=city,
        )
        return [city]
    record.add_decision(
        E1_CITY_RESCUE_FEATURE,
        "kept_empty_after_city_recall",
        label=city_label,
    )
    return []


def repair_award(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    _caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    """Apply the active deterministic award metadata cleanup."""
    values = list(prediction.object_entities)
    if not config.features.award_metadata_cleanup:
        return values
    before = list(values)
    values = _unique_by_strict_key(
        normalized
        for normalized in (normalize_award_metadata(value) for value in values)
        if normalized
    )
    if values != before:
        record.add_decision(
            "AwardMetadataNormalizer",
            "normalized_structural_metadata",
            before=before,
            after=values,
        )
    return values


def _e1_life_status_prompt(subject: str) -> str:
    return (
        f"Subject: {subject}\n\n"
        "Question:\n"
        "Is this exact person deceased?\n\n"
        "Return exactly one of:\n"
        "DECEASED\n"
        "LIVING\n"
        "UNKNOWN\n\n"
        "Use UNKNOWN if you are not sufficiently confident.\n\n"
        "Do not explain your answer."
    )


def _e1_city_prompt(subject: str) -> str:
    return (
        f"Subject: {subject}\n\n"
        "Relation: personHasCityOfDeath\n\n"
        "The person has already been classified as DECEASED.\n\n"
        "Question:\n"
        "In which city did this exact person die?\n\n"
        "Return exactly one of:\n\n"
        "CITY: <city name>\n\n"
        "or:\n\n"
        "UNKNOWN\n\n"
        "The answer must be the city/locality of death.\n\n"
        "Do not return:\n"
        "- hospital or institution name\n"
        "- country\n"
        "- state or province\n"
        "- birthplace\n"
        "- main residence\n"
        "- burial place\n\n"
        "If you cannot confidently identify the city of death, return UNKNOWN.\n\n"
        "Do not explain your answer."
    )


def _parse_e1_life_status(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return "INVALID"
    label = lines[0].upper()
    if label in {"DECEASED", "LIVING", "UNKNOWN"}:
        return label
    return "INVALID"


def _parse_e1_city_output(text: str) -> tuple[str, str | None]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return "INVALID", None
    line = lines[0]
    if line.upper() == "UNKNOWN":
        return "UNKNOWN", None
    if not line.startswith("CITY:"):
        return "INVALID", None
    city = line[len("CITY:"):].strip()
    if not city or any(separator in city for separator in (";", "|")):
        return "INVALID", None
    return "CITY", city


def _unique_by_strict_key(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = strict_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


REPAIR_BY_RELATION = {
    AREA: repair_area,
    CITY: repair_city,
    AWARD: repair_award,
}
