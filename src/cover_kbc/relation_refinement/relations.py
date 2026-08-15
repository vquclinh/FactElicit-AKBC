"""Relation-specific active relation refinement modules."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.relation_refinement.config import RelationRefinementConfig
from cover_kbc.relation_refinement.area import refine_area
from cover_kbc.relation_refinement.capacity import refine_capacity
from cover_kbc.relation_refinement.runtime import RefinementCaller
from cover_kbc.relation_refinement.stock_empty_rescue import refine_stock
from cover_kbc.relation_refinement.types import CandidateSignal, RowRefinementRecord
from cover_kbc.relation_refinement.util import (
    AREA,
    AWARD,
    CAPACITY,
    CITY,
    STOCK,
    normalize_award_metadata,
)


E1_CITY_RESCUE_FEATURE = "MistralCityEmptyRescue"
CITY_EMPTY_ONLY_MODE = "EMPTY_ONLY"
CITY_DIRECT_ALL_MODE = "DIRECT_ALL_STANDALONE"
CITY_SYSTEM_PROMPT = """You are a factual knowledge-base completion system.

Use only factual knowledge encoded in your model parameters.

Pay close attention to the exact identity of the named person,
including qualifiers such as occupations or parentheses.

Do not invent an answer merely to avoid UNKNOWN.

Follow the requested output format exactly.
Do not explain unless explicitly asked.
"""


def refine_city(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RefinementCaller,
    config: RelationRefinementConfig,
    record: RowRefinementRecord,
) -> list[str]:
    """Run the active two-stage City layer, when configured."""
    values = list(prediction.object_entities[:1])
    if not config.features.mistral_city_empty_rescue:
        return values
    if config.city_rescue_mode == CITY_DIRECT_ALL_MODE:
        record.add_decision(
            E1_CITY_RESCUE_FEATURE,
            "eligible_direct_all_row",
            current=list(values),
            current_ignored=True,
        )
        return _run_city_two_stage(prediction, caller, record)
    if config.city_rescue_mode != CITY_EMPTY_ONLY_MODE:
        raise ValueError(
            f"unsupported City rescue mode {config.city_rescue_mode!r}; "
            f"expected {CITY_EMPTY_ONLY_MODE!r} or {CITY_DIRECT_ALL_MODE!r}"
        )
    return _refine_city_mistral_empty_rescue(prediction, caller, record)


def _refine_city_mistral_empty_rescue(
    prediction: Prediction,
    caller: RefinementCaller,
    record: RowRefinementRecord,
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
    return _run_city_two_stage(prediction, caller, record)


def _run_city_two_stage(
    prediction: Prediction,
    caller: RefinementCaller,
    record: RowRefinementRecord,
) -> list[str]:
    text = caller.generate(
        role="verifier",
        layer="E1",
        feature=E1_CITY_RESCUE_FEATURE,
        system_prompt=CITY_SYSTEM_PROMPT,
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
        system_prompt=CITY_SYSTEM_PROMPT,
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


def refine_award(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    _caller: RefinementCaller,
    config: RelationRefinementConfig,
    record: RowRefinementRecord,
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
    if not lines:
        return "INVALID"
    label = lines[0].upper()
    if label in {"DECEASED", "LIVING", "UNKNOWN"}:
        return label
    return "INVALID"


def _parse_e1_city_output(text: str) -> tuple[str, str | None]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return "INVALID", None
    line = lines[0]
    if line.upper() == "UNKNOWN":
        return "UNKNOWN", None
    match = re.match(r"^CITY\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
    if match is None:
        return "INVALID", None
    city = match.group(1).strip()
    if not city or city.upper() == "UNKNOWN":
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


REFINEMENT_BY_RELATION = {
    AREA: refine_area,
    CAPACITY: refine_capacity,
    CITY: refine_city,
    STOCK: refine_stock,
    AWARD: refine_award,
}
