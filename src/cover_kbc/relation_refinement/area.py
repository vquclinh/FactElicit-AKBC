"""Integrated Profile E1 Direct Area resolver."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

from cover_kbc.types import Prediction

from cover_kbc.relation_refinement.config import RelationRefinementConfig
from cover_kbc.relation_refinement.runtime import RefinementCaller
from cover_kbc.relation_refinement.types import CandidateSignal, RowRefinementRecord


DIRECT_AREA_FEATURE = "MistralDirectArea"
DIRECT_AREA_MODE = "DIRECT_ALL"
DIRECT_AREA_SYSTEM_PROMPT = (
    "You are a precise factual knowledge-base completion assistant.\n\n"
    "Use only factual knowledge encoded in the model.\n"
    "Follow the requested output format exactly.\n"
    "If you are not sufficiently confident, return UNKNOWN.\n"
    "Do not explain your answer."
)

VALID_AREA = "VALID_AREA"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"

_AREA_RE = re.compile(r"^AREA:\s*(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class ParsedArea:
    """Strict parse result for one Direct Area generation."""

    status: str
    value: str = ""
    number: Decimal | None = None

    @property
    def valid(self) -> bool:
        return self.status == VALID_AREA and bool(self.value) and self.number is not None

    def to_json(self) -> dict[str, str]:
        return {
            "status": self.status,
            "value": self.value,
            "number": str(self.number) if self.number is not None else "",
        }


def direct_area_prompt(subject: str) -> str:
    """The exact integrated Profile E1 Direct Area user prompt."""
    return (
        f"Subject: {subject}\n\n"
        "Relation: hasArea\n\n"
        "Question:\n"
        "What is the canonical surface area of this exact named geographic entity in\n"
        "square kilometres?\n\n"
        "Identify the exact entity named by the subject before answering.\n\n"
        "For an island:\n"
        "return the land area of that exact island itself.\n\n"
        "For a lake:\n"
        "return the surface area of that exact lake.\n\n"
        "For a country:\n"
        "return its total area, including land and inland water.\n\n"
        "Do NOT return the area of:\n"
        "- a containing country\n"
        "- a state, province, county, municipality, or administrative region\n"
        "- an archipelago or island group unless the subject itself is that group\n"
        "- only one part of the named island\n"
        "- a drainage basin or catchment\n"
        "- a lagoon unless the subject itself is the lagoon\n"
        "- a protected area\n"
        "- a nearby geographic feature\n\n"
        "If you know the area in square miles or hectares, convert it to square\n"
        "kilometres before answering.\n\n"
        "Return exactly one of:\n\n"
        "AREA: <number>\n\n"
        "or:\n\n"
        "UNKNOWN\n\n"
        "<number> must be a single positive decimal number in km^2.\n\n"
        "Do not include units after the number.\n"
        "Do not return a range.\n"
        "Do not give multiple candidate values.\n"
        "Do not explain your answer."
    )


def parse_direct_area_output(text: str) -> ParsedArea:
    """Parse Direct Area output strictly."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return ParsedArea(INVALID)
    line = lines[0]
    if line.upper() == UNKNOWN:
        return ParsedArea(UNKNOWN)
    match = _AREA_RE.fullmatch(line)
    if not match:
        return ParsedArea(INVALID)
    value = match.group(1)
    try:
        number = Decimal(value)
    except InvalidOperation:
        return ParsedArea(INVALID)
    if not number.is_finite() or number <= 0:
        return ParsedArea(INVALID)
    return ParsedArea(VALID_AREA, value=value, number=number)


def direct_area_values(parsed: ParsedArea) -> list[str]:
    """DIRECT_ALL replacement semantics."""
    return [parsed.value] if parsed.valid else []


def refine_area(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RefinementCaller,
    config: RelationRefinementConfig,
    record: RowRefinementRecord,
) -> list[str]:
    """Run integrated E1 Direct Area, when configured.

    DIRECT_ALL means the direct output replaces the existing Area prediction:
    valid ``AREA: X`` becomes ``["X"]``; ``UNKNOWN`` or invalid output becomes
    ``[]``. The old numeric answer is not retained as a fallback.
    """
    if config.features.mistral_area_multiview:
        from cover_kbc.relation_refinement.area_multiview import refine_area_multiview

        return refine_area_multiview(prediction, _signals, caller, config, record)

    values = list(prediction.object_entities[:1])
    if not config.features.mistral_direct_area:
        return values
    if config.direct_area_mode != DIRECT_AREA_MODE:
        raise ValueError(
            f"unsupported Direct Area mode {config.direct_area_mode!r}; "
            f"expected {DIRECT_AREA_MODE!r}"
        )

    record.add_decision(
        DIRECT_AREA_FEATURE,
        "eligible_has_area_row",
        current=list(values),
    )
    text = caller.generate(
        role="verifier",
        layer="E1_DIRECT_AREA",
        feature=DIRECT_AREA_FEATURE,
        system_prompt=DIRECT_AREA_SYSTEM_PROMPT,
        prompt=direct_area_prompt(prediction.subject),
        view_id="mistral_direct_area",
        max_new_tokens=24,
    )
    if text is None:
        record.skipped.append("MistralDirectArea: budget exhausted before direct area")
        return []

    parsed = parse_direct_area_output(text)
    record.add_decision(
        DIRECT_AREA_FEATURE,
        "direct_area_output",
        raw=text,
        **parsed.to_json(),
    )
    out = direct_area_values(parsed)
    record.add_decision(
        DIRECT_AREA_FEATURE,
        "direct_all_replacement",
        values=list(out),
        status=parsed.status,
    )
    return out
