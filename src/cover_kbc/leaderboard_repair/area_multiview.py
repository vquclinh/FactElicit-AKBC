"""Profile E3 Mistral Area Multi-View resolver."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Mapping, Sequence

from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.area import (
    DIRECT_AREA_SYSTEM_PROMPT,
    direct_area_prompt,
)
from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import CandidateSignal, RowRepairRecord


AREA_MULTIVIEW_FEATURE = "MistralAreaMultiView"
AREA_MULTIVIEW_MODE = "DIRECT_ALL"
AREA_SYSTEM_PROMPT = DIRECT_AREA_SYSTEM_PROMPT

VALID_AREA = "VALID_AREA"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"

VIEW_IDS = (
    "area_multiview_v1_direct",
    "area_multiview_v2_entity_type",
    "area_multiview_v3_infobox",
    "area_multiview_v4_attribute_contrast",
)
DIRECT_VIEW_ID = VIEW_IDS[0]
JUDGE_VIEW_ID = "area_multiview_judge"

MULTIVIEW_SUPPORT_GE_3 = "MULTIVIEW_SUPPORT_GE_3"
JUDGE = "JUDGE"
FALLBACK_V1_DIRECT_AREA = "FALLBACK_V1_DIRECT_AREA"
FALLBACK_TOP_CLUSTER = "FALLBACK_TOP_CLUSTER"
NO_VALUE = "NO_VALUE"

_AREA_RE = re.compile(
    r"^AREA:\s*((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)$"
)
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class ParsedArea:
    """Strict parse result for one Area Multi-View generation."""

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


@dataclass(frozen=True)
class AreaObservation:
    """One valid numeric value emitted by one Area view."""

    view_id: str
    value: str
    number: Decimal

    def to_json(self) -> dict[str, str]:
        return {
            "view_id": self.view_id,
            "value": self.value,
            "number": str(self.number),
        }


@dataclass(frozen=True)
class AreaCluster:
    """A deterministic 5%-compatible cluster of observed Area values."""

    observations: tuple[AreaObservation, ...]

    @property
    def support(self) -> int:
        return len(self.observations)

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(observation.value for observation in self.observations)

    @property
    def first_view_rank(self) -> int:
        return min(_view_rank(observation.view_id) for observation in self.observations)

    @property
    def representative_observation(self) -> AreaObservation:
        med = Decimal(str(median(observation.number for observation in self.observations)))
        return min(
            self.observations,
            key=lambda observation: (
                abs(observation.number - med),
                _view_rank(observation.view_id),
                observation.number,
            ),
        )

    @property
    def representative(self) -> str:
        return self.representative_observation.value

    def to_json(self) -> dict[str, object]:
        return {
            "support": self.support,
            "representative": self.representative,
            "values": list(self.values),
            "observations": [observation.to_json() for observation in self.observations],
        }


@dataclass(frozen=True)
class AreaDecision:
    """Final deterministic Area Multi-View decision."""

    values: tuple[str, ...]
    reason: str
    judge_status: str = ""
    judge_value: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "reason": self.reason,
            "judge_status": self.judge_status,
            "judge_value": self.judge_value,
        }


def area_view_prompt(subject: str, view_id: str) -> str:
    """Render one of the four tested Profile E3 Area views."""
    if view_id == "area_multiview_v1_direct":
        return direct_area_prompt(subject)
    if view_id == "area_multiview_v2_entity_type":
        return (
            f"Subject: {subject}\n\n"
            "Relation: hasArea\n\n"
            "Silently classify the exact subject as one of:\n"
            "COUNTRY, ISLAND, LAKE, OTHER_GEOGRAPHIC_ENTITY.\n\n"
            "Resolve every qualifier in the full subject string before answering.\n\n"
            "For COUNTRY, return total area including land and inland water.\n"
            "For ISLAND, return the land area of that exact island itself.\n"
            "For LAKE, return the surface area of that exact lake.\n"
            "For OTHER_GEOGRAPHIC_ENTITY, return the area of the exact named feature.\n\n"
            "If you know the value in square miles or hectares, convert it to square\n"
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
    if view_id == "area_multiview_v3_infobox":
        return (
            f"Subject: {subject}\n\n"
            "Relation: hasArea\n\n"
            "Recall the canonical encyclopedic or infobox-style published area for\n"
            "this exact named geographic entity.\n\n"
            "Distinguish internally between:\n"
            "- the exact entity and a similarly named entity\n"
            "- an island and an archipelago\n"
            "- a lake surface area and a basin or catchment area\n"
            "- square kilometres and square miles or hectares\n"
            "- area and population, length, depth, elevation, or volume\n\n"
            "Convert square miles or hectares to square kilometres when necessary.\n\n"
            "Return exactly one of:\n\n"
            "AREA: <number>\n\n"
            "or:\n\n"
            "UNKNOWN\n\n"
            "Do not include units after the number.\n"
            "Do not return a range.\n"
            "Do not give multiple candidate values.\n"
            "Do not explain your answer."
        )
    if view_id == "area_multiview_v4_attribute_contrast":
        return (
            f"Subject: {subject}\n\n"
            "Relation: hasArea\n\n"
            "Reason silently through:\n"
            "1. exact entity identity\n"
            "2. entity type\n"
            "3. correct area attribute\n"
            "4. wrong-unit rejection\n"
            "5. conversion to square kilometres where needed\n\n"
            "Use these contrasts:\n"
            "- island area is not archipelago area\n"
            "- island area is not municipality or province area\n"
            "- lake surface area is not drainage basin area\n"
            "- lake surface area is not catchment area\n"
            "- country total area is not land-only area\n"
            "- exact entity area is not containing-country area\n"
            "- square kilometres are not square miles\n"
            "- square kilometres are not hectares\n\n"
            "Do not expose the reasoning.\n\n"
            "Return exactly one of:\n\n"
            "AREA: <number>\n\n"
            "or:\n\n"
            "UNKNOWN\n\n"
            "<number> must be a single positive decimal number in km^2."
        )
    raise ValueError(f"unknown Area view_id {view_id!r}")


def parse_area_output(text: str) -> ParsedArea:
    """Parse one strict Area output."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return ParsedArea(INVALID)
    line = lines[0]
    if line.upper() == UNKNOWN:
        return ParsedArea(UNKNOWN)
    match = _AREA_RE.fullmatch(line)
    if not match:
        return ParsedArea(INVALID)
    raw_value = match.group(1).replace(",", "")
    try:
        number = Decimal(raw_value)
    except InvalidOperation:
        return ParsedArea(INVALID)
    if not number.is_finite() or number <= 0:
        return ParsedArea(INVALID)
    return ParsedArea(VALID_AREA, value=format_area_number(number), number=number)


def format_area_number(number: Decimal) -> str:
    """Format an observed Area number for the official evaluator."""
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    text = format(number.normalize(), "f")
    return text.rstrip("0").rstrip(".")


def area_within_tolerance(a: Decimal, b: Decimal, *, tolerance: Decimal = Decimal("0.05")) -> bool:
    denominator = max(abs(a), abs(b))
    if denominator == 0:
        return a == b
    return (abs(a - b) / denominator) <= tolerance


def cluster_area_values(
    observations: Sequence[AreaObservation],
    *,
    tolerance: Decimal = Decimal("0.05"),
) -> list[AreaCluster]:
    """Cluster observed values with pairwise 5%-compatibility."""
    clusters: list[list[AreaObservation]] = []
    for observation in observations:
        placed = False
        for cluster in clusters:
            if all(
                area_within_tolerance(
                    observation.number,
                    member.number,
                    tolerance=tolerance,
                )
                for member in cluster
            ):
                cluster.append(observation)
                placed = True
                break
        if not placed:
            clusters.append([observation])
    return [AreaCluster(tuple(cluster)) for cluster in clusters]


def top_area_cluster(clusters: Sequence[AreaCluster]) -> AreaCluster | None:
    if not clusters:
        return None
    return min(
        clusters,
        key=lambda cluster: (
            -cluster.support,
            cluster.first_view_rank,
            Decimal(cluster.representative),
        ),
    )


def area_judge_prompt(subject: str, candidates: Sequence[str]) -> tuple[str, dict[str, str], str]:
    """Render the source-blind Area judge prompt."""
    if not candidates:
        raise ValueError("Area judge needs at least one numeric candidate")
    if len(candidates) >= len(_LABELS):
        raise ValueError("too many Area judge candidates")
    label_to_value = {_LABELS[index]: value for index, value in enumerate(candidates)}
    unknown_label = _LABELS[len(candidates)]
    candidate_lines = [f"{label}. {value}" for label, value in label_to_value.items()]
    candidate_lines.append(f"{unknown_label}. UNKNOWN")
    prompt = (
        f"Subject: {subject}\n\n"
        "Relation: hasArea\n\n"
        "Choose the candidate that best represents the canonical area in square\n"
        "kilometres for this exact named geographic entity.\n\n"
        "Reject candidates that are:\n"
        "- an administrative or container area\n"
        "- a country area instead of an island area\n"
        "- an archipelago area instead of an island area\n"
        "- a basin or catchment area instead of a lake surface area\n"
        "- a nearby or similarly named geographic feature\n"
        "- an unconverted square-mile or hectare value\n"
        "- population, length, depth, elevation, volume, or another numeric attribute\n\n"
        "Candidates:\n\n"
        + "\n".join(candidate_lines)
        + "\n\nReturn exactly one label only.\n\n"
        "Do not explain."
    )
    return prompt, label_to_value, unknown_label


def parse_area_judge_output(
    text: str,
    label_to_value: Mapping[str, str],
    unknown_label: str,
) -> tuple[str, str | None]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return INVALID, None
    label = lines[0].upper()
    if label in label_to_value:
        return VALID_AREA, label_to_value[label]
    if label == unknown_label:
        return UNKNOWN, None
    return INVALID, None


def decide_area_multiview(
    *,
    observations: Sequence[AreaObservation],
    parsed_by_view: Mapping[str, ParsedArea],
    clusters: Sequence[AreaCluster],
    judge_status: str = "",
    judge_value: str | None = None,
) -> AreaDecision:
    """Apply the tested deterministic Area Multi-View decision policy."""
    top = top_area_cluster(clusters)
    if top is not None and top.support >= 3:
        return AreaDecision((top.representative,), MULTIVIEW_SUPPORT_GE_3)
    if judge_status == VALID_AREA and judge_value:
        return AreaDecision((judge_value,), JUDGE, judge_status=judge_status, judge_value=judge_value)
    v1 = parsed_by_view.get(DIRECT_VIEW_ID)
    if v1 is not None and v1.valid:
        return AreaDecision(
            (v1.value,),
            FALLBACK_V1_DIRECT_AREA,
            judge_status=judge_status,
            judge_value=judge_value or "",
        )
    if top is not None:
        return AreaDecision(
            (top.representative,),
            FALLBACK_TOP_CLUSTER,
            judge_status=judge_status,
            judge_value=judge_value or "",
        )
    return AreaDecision((), NO_VALUE, judge_status=judge_status, judge_value=judge_value or "")


def repair_area_multiview(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    """Run Profile E3 Area Multi-View, when configured."""
    current = list(prediction.object_entities[:1])
    if not config.features.mistral_area_multiview:
        return current
    if config.area_multiview_mode != AREA_MULTIVIEW_MODE:
        raise ValueError(
            f"unsupported Area Multi-View mode {config.area_multiview_mode!r}; "
            f"expected {AREA_MULTIVIEW_MODE!r}"
        )
    if caller.row_budget.cap < 4:
        raise ValueError("MistralAreaMultiView requires at least 4 calls per hasArea row")

    record.add_decision(
        AREA_MULTIVIEW_FEATURE,
        "eligible_has_area_row",
        current=list(current),
        current_ignored=True,
    )

    parsed_by_view: dict[str, ParsedArea] = {}
    observations: list[AreaObservation] = []
    for view_id in VIEW_IDS:
        text = caller.generate(
            role="verifier",
            layer="E3_AREA_MULTIVIEW",
            feature=AREA_MULTIVIEW_FEATURE,
            system_prompt=AREA_SYSTEM_PROMPT,
            prompt=area_view_prompt(prediction.subject, view_id),
            view_id=view_id,
            max_new_tokens=24,
        )
        if text is None:
            record.skipped.append(f"MistralAreaMultiView: budget exhausted before {view_id}")
            parsed = ParsedArea(INVALID)
        else:
            parsed = parse_area_output(text)
        parsed_by_view[view_id] = parsed
        if parsed.valid and parsed.number is not None:
            observations.append(
                AreaObservation(
                    view_id=view_id,
                    value=parsed.value,
                    number=parsed.number,
                )
            )
        record.add_decision(
            AREA_MULTIVIEW_FEATURE,
            "view_output",
            view_id=view_id,
            raw=text or "",
            **parsed.to_json(),
        )

    clusters = cluster_area_values(observations)
    record.add_decision(
        AREA_MULTIVIEW_FEATURE,
        "clustered_numeric_outputs",
        clusters=[cluster.to_json() for cluster in clusters],
    )
    top = top_area_cluster(clusters)
    judge_status = ""
    judge_value: str | None = None

    if top is not None and top.support >= 3:
        decision = decide_area_multiview(
            observations=observations,
            parsed_by_view=parsed_by_view,
            clusters=clusters,
        )
    elif observations:
        candidates = sorted({observation.value for observation in observations}, key=Decimal)
        prompt, label_to_value, unknown_label = area_judge_prompt(
            prediction.subject,
            candidates,
        )
        text = caller.generate(
            role="verifier",
            layer="E3_AREA_MULTIVIEW",
            feature=AREA_MULTIVIEW_FEATURE,
            system_prompt=AREA_SYSTEM_PROMPT,
            prompt=prompt,
            view_id=JUDGE_VIEW_ID,
            max_new_tokens=4,
        )
        if text is None:
            record.skipped.append("MistralAreaMultiView: budget exhausted before judge")
            judge_status, judge_value = INVALID, None
        else:
            judge_status, judge_value = parse_area_judge_output(
                text,
                label_to_value,
                unknown_label,
            )
        record.add_decision(
            AREA_MULTIVIEW_FEATURE,
            "judge_output",
            raw=text or "",
            status=judge_status,
            value=judge_value or "",
            candidates=list(candidates),
        )
        decision = decide_area_multiview(
            observations=observations,
            parsed_by_view=parsed_by_view,
            clusters=clusters,
            judge_status=judge_status,
            judge_value=judge_value,
        )
    else:
        decision = decide_area_multiview(
            observations=observations,
            parsed_by_view=parsed_by_view,
            clusters=clusters,
        )

    record.add_decision(
        AREA_MULTIVIEW_FEATURE,
        "final_decision",
        **decision.to_json(),
    )
    return list(decision.values)


def _view_rank(view_id: str) -> int:
    try:
        return VIEW_IDS.index(view_id)
    except ValueError:
        return len(VIEW_IDS)
