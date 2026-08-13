"""Profile E2 Mistral Capacity Multi-View resolver."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Mapping, Sequence

from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import CandidateSignal, RowRepairRecord


CAPACITY_MULTIVIEW_FEATURE = "MistralCapacityMultiView"
CAPACITY_MULTIVIEW_MODE = "DIRECT_ALL"
CAPACITY_SYSTEM_PROMPT = (
    "You are a precise closed-book factual knowledge-base completion assistant.\n\n"
    "Use only factual knowledge encoded in the model.\n\n"
    "The target relation hasCapacity means the maximum spectator capacity of the\n"
    "exact named venue, expressed as an integer number of people.\n\n"
    "When multiple capacities are published, use the highest published spectator\n"
    "capacity.\n\n"
    "Never substitute attendance, area, construction cost, year, field dimensions,\n"
    "or another venue.\n\n"
    "If you are not sufficiently confident, return UNKNOWN.\n\n"
    "Follow the output format exactly."
)

VALID_CAPACITY = "VALID_CAPACITY"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"

VIEW_IDS = (
    "capacity_multiview_v1",
    "capacity_multiview_v2",
    "capacity_multiview_v3",
    "capacity_multiview_v4",
)
JUDGE_VIEW_ID = "capacity_multiview_judge"

_CAPACITY_RE = re.compile(r"^CAPACITY:\s*((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.0+)?)$")
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class ParsedCapacity:
    """Strict parse result for one Capacity generation."""

    status: str
    value: int | None = None

    @property
    def valid(self) -> bool:
        return self.status == VALID_CAPACITY and self.value is not None

    def to_json(self) -> dict[str, object]:
        return {"status": self.status, "value": self.value if self.value is not None else ""}


@dataclass(frozen=True)
class CapacityObservation:
    """One numeric value emitted by one view."""

    view_id: str
    value: int

    def to_json(self) -> dict[str, object]:
        return {"view_id": self.view_id, "value": self.value}


@dataclass(frozen=True)
class CapacityCluster:
    """A deterministic 5%-compatible cluster of observed Capacity values."""

    observations: tuple[CapacityObservation, ...]

    @property
    def support(self) -> int:
        return len(self.observations)

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(observation.value for observation in self.observations)

    @property
    def first_view_rank(self) -> int:
        return min(_view_rank(observation.view_id) for observation in self.observations)

    @property
    def representative(self) -> int:
        med = Decimal(str(median(self.values)))
        return min(
            self.observations,
            key=lambda observation: (
                abs(Decimal(observation.value) - med),
                _view_rank(observation.view_id),
                observation.value,
            ),
        ).value

    def to_json(self) -> dict[str, object]:
        return {
            "support": self.support,
            "representative": self.representative,
            "values": list(self.values),
            "observations": [observation.to_json() for observation in self.observations],
        }


def capacity_view_prompt(subject: str, view_id: str) -> str:
    """Render one of the four hidden-tested Capacity views."""
    if view_id == "capacity_multiview_v1":
        return (
            f"Subject: {subject}\n\n"
            "Relation: hasCapacity\n\n"
            "What is the maximum spectator capacity of this exact venue?\n\n"
            "Use the full subject name and location qualifier to identify the exact venue.\n\n"
            "If several capacities are published, including seated, standing, sporting,\n"
            "historical, pre-renovation, post-renovation, or event configurations, return\n"
            "the HIGHEST published spectator capacity.\n\n"
            "Do NOT return:\n"
            "- record attendance\n"
            "- average attendance\n"
            "- attendance at one event\n"
            "- area\n"
            "- field dimensions\n"
            "- construction cost\n"
            "- opening year\n"
            "- renovation year\n"
            "- one stand or section\n"
            "- another similarly named venue\n\n"
            "Return exactly:\n\n"
            "CAPACITY: <integer>\n\n"
            "or:\n\n"
            "UNKNOWN\n\n"
            "No explanation."
        )
    if view_id == "capacity_multiview_v2":
        return (
            f"Subject: {subject}\n\n"
            "Recall the encyclopedic spectator-capacity fact for this exact venue.\n\n"
            "First internally disambiguate the venue using the complete name and location.\n\n"
            "Think of the capacity figure that would normally appear in an encyclopedia,\n"
            "infobox, venue profile, or structured knowledge base.\n\n"
            "Return the HIGHEST published maximum spectator capacity for this exact venue.\n\n"
            "Do not return attendance, area, dates, costs, dimensions, or another venue.\n\n"
            "Return exactly:\n\n"
            "CAPACITY: <integer>\n\n"
            "or:\n\n"
            "UNKNOWN\n\n"
            "No explanation."
        )
    if view_id == "capacity_multiview_v3":
        return (
            f"Subject: {subject}\n\n"
            "This exact venue may have several published spectator-capacity figures.\n\n"
            "Internally distinguish:\n"
            "- seated capacity\n"
            "- total capacity\n"
            "- standing capacity\n"
            "- sport-specific configuration\n"
            "- concert/event configuration\n"
            "- historical capacity\n"
            "- post-renovation capacity\n"
            "- temporary capacity\n\n"
            "Do NOT confuse capacity with record attendance.\n\n"
            "For hasCapacity, return the HIGHEST published spectator capacity for the exact\n"
            "venue.\n\n"
            "Return exactly:\n\n"
            "CAPACITY: <integer>\n\n"
            "or:\n\n"
            "UNKNOWN\n\n"
            "No explanation."
        )
    if view_id == "capacity_multiview_v4":
        return (
            f"Subject: {subject}\n\n"
            "Before answering, reason internally:\n\n"
            "1. Identify the exact venue from its full name and location.\n"
            "2. Identify the venue type.\n"
            "3. Separate spectator capacity from attendance, area, dimensions, cost, and\n"
            "   dates.\n"
            "4. Consider whether multiple historical or configuration-specific capacities\n"
            "   exist.\n"
            "5. Select the HIGHEST published maximum spectator capacity.\n\n"
            "Do not reveal the reasoning.\n\n"
            "Return exactly:\n\n"
            "CAPACITY: <integer>\n\n"
            "or:\n\n"
            "UNKNOWN"
        )
    raise ValueError(f"unknown Capacity view_id {view_id!r}")


def parse_capacity_output(text: str) -> ParsedCapacity:
    """Parse a strict Capacity output."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return ParsedCapacity(INVALID)
    line = lines[0]
    if line.upper() == UNKNOWN:
        return ParsedCapacity(UNKNOWN)
    match = _CAPACITY_RE.fullmatch(line)
    if not match:
        return ParsedCapacity(INVALID)
    raw_number = match.group(1).replace(",", "")
    try:
        number = Decimal(raw_number)
    except InvalidOperation:
        return ParsedCapacity(INVALID)
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        return ParsedCapacity(INVALID)
    return ParsedCapacity(VALID_CAPACITY, value=int(number))


def capacity_within_tolerance(a: int, b: int, *, tolerance: float = 0.05) -> bool:
    denominator = max(abs(a), abs(b))
    if denominator == 0:
        return a == b
    return (abs(a - b) / denominator) <= tolerance


def cluster_capacity_values(
    observations: Sequence[CapacityObservation],
    *,
    tolerance: float = 0.05,
) -> list[CapacityCluster]:
    """Cluster observed values with pairwise 5%-compatibility."""
    clusters: list[list[CapacityObservation]] = []
    for observation in observations:
        placed = False
        for cluster in clusters:
            if all(
                capacity_within_tolerance(
                    observation.value,
                    member.value,
                    tolerance=tolerance,
                )
                for member in cluster
            ):
                cluster.append(observation)
                placed = True
                break
        if not placed:
            clusters.append([observation])
    return [CapacityCluster(tuple(cluster)) for cluster in clusters]


def top_capacity_cluster(clusters: Sequence[CapacityCluster]) -> CapacityCluster | None:
    if not clusters:
        return None
    return min(
        clusters,
        key=lambda cluster: (
            -cluster.support,
            cluster.first_view_rank,
            cluster.representative,
        ),
    )


def capacity_judge_prompt(subject: str, candidates: Sequence[int]) -> tuple[str, dict[str, int], str]:
    """Render the source-blind ambiguity judge prompt."""
    if not candidates:
        raise ValueError("capacity judge needs at least one numeric candidate")
    if len(candidates) >= len(_LABELS):
        raise ValueError("too many Capacity judge candidates")
    label_to_value = {_LABELS[index]: value for index, value in enumerate(candidates)}
    unknown_label = _LABELS[len(candidates)]
    candidate_lines = [
        f"{label}. {value}" for label, value in label_to_value.items()
    ]
    candidate_lines.append(f"{unknown_label}. UNKNOWN")
    prompt = (
        f"Subject: {subject}\n\n"
        "Relation: hasCapacity\n\n"
        "Choose the candidate that best represents the HIGHEST published maximum\n"
        "spectator capacity of this exact venue.\n\n"
        "Identify the exact venue using its complete name and location.\n\n"
        "Reject values that are:\n"
        "- attendance records\n"
        "- event attendance\n"
        "- area\n"
        "- dimensions\n"
        "- cost\n"
        "- dates\n"
        "- capacities of similarly named venues\n\n"
        "Candidates:\n\n"
        + "\n".join(candidate_lines)
        + "\n\nReturn exactly one label only.\n\n"
        "Do not explain."
    )
    return prompt, label_to_value, unknown_label


def parse_capacity_judge_output(
    text: str,
    label_to_value: Mapping[str, int],
    unknown_label: str,
) -> tuple[str, int | None]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return INVALID, None
    label = lines[0].upper()
    if label in label_to_value:
        return VALID_CAPACITY, label_to_value[label]
    if label == unknown_label:
        return UNKNOWN, None
    return INVALID, None


def repair_capacity(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    """Run Profile E2 Capacity Multi-View, when configured.

    The old upstream Capacity prediction is recorded as the current value but
    never participates in clustering, judging, or fallback.
    """
    current = list(prediction.object_entities[:1])
    if not config.features.mistral_capacity_multiview:
        return current
    if config.capacity_multiview_mode != CAPACITY_MULTIVIEW_MODE:
        raise ValueError(
            f"unsupported Capacity Multi-View mode {config.capacity_multiview_mode!r}; "
            f"expected {CAPACITY_MULTIVIEW_MODE!r}"
        )

    record.add_decision(
        CAPACITY_MULTIVIEW_FEATURE,
        "eligible_has_capacity_row",
        current=list(current),
        current_ignored=True,
    )

    parsed_by_view: dict[str, ParsedCapacity] = {}
    observations: list[CapacityObservation] = []
    for view_id in VIEW_IDS:
        text = caller.generate(
            role="verifier",
            layer="E2_CAPACITY_MULTIVIEW",
            feature=CAPACITY_MULTIVIEW_FEATURE,
            system_prompt=CAPACITY_SYSTEM_PROMPT,
            prompt=capacity_view_prompt(prediction.subject, view_id),
            view_id=view_id,
            max_new_tokens=24,
        )
        if text is None:
            record.skipped.append(f"MistralCapacityMultiView: budget exhausted before {view_id}")
            parsed = ParsedCapacity(INVALID)
        else:
            parsed = parse_capacity_output(text)
        parsed_by_view[view_id] = parsed
        if parsed.valid:
            observations.append(CapacityObservation(view_id=view_id, value=int(parsed.value)))
        record.add_decision(
            CAPACITY_MULTIVIEW_FEATURE,
            "view_output",
            view_id=view_id,
            raw=text or "",
            **parsed.to_json(),
        )

    clusters = cluster_capacity_values(
        observations,
        tolerance=config.numeric_cluster_tolerance,
    )
    record.add_decision(
        CAPACITY_MULTIVIEW_FEATURE,
        "clustered_numeric_outputs",
        clusters=[cluster.to_json() for cluster in clusters],
    )
    top = top_capacity_cluster(clusters)
    final_value: int | None = None
    final_reason = ""

    if top is not None and top.support >= 3:
        final_value = top.representative
        final_reason = "strong_cluster"
    elif observations:
        candidates = sorted({observation.value for observation in observations})
        prompt, label_to_value, unknown_label = capacity_judge_prompt(
            prediction.subject,
            candidates,
        )
        text = caller.generate(
            role="verifier",
            layer="E2_CAPACITY_MULTIVIEW",
            feature=CAPACITY_MULTIVIEW_FEATURE,
            system_prompt=CAPACITY_SYSTEM_PROMPT,
            prompt=prompt,
            view_id=JUDGE_VIEW_ID,
            max_new_tokens=4,
        )
        if text is None:
            record.skipped.append("MistralCapacityMultiView: budget exhausted before judge")
            judge_status, judge_value = INVALID, None
        else:
            judge_status, judge_value = parse_capacity_judge_output(
                text,
                label_to_value,
                unknown_label,
            )
        record.add_decision(
            CAPACITY_MULTIVIEW_FEATURE,
            "judge_output",
            raw=text or "",
            status=judge_status,
            value=judge_value if judge_value is not None else "",
            candidates=list(candidates),
        )
        if judge_status == VALID_CAPACITY and judge_value is not None:
            final_value = judge_value
            final_reason = "judge_selected"
        elif parsed_by_view["capacity_multiview_v1"].valid:
            final_value = int(parsed_by_view["capacity_multiview_v1"].value)
            final_reason = "fallback_v1"
        elif top is not None:
            final_value = top.representative
            final_reason = "fallback_top_cluster"
    else:
        final_reason = "no_numeric_values"

    out = [str(final_value)] if final_value is not None else []
    record.add_decision(
        CAPACITY_MULTIVIEW_FEATURE,
        "final_decision",
        reason=final_reason,
        values=list(out),
    )
    return out


def _view_rank(view_id: str) -> int:
    try:
        return VIEW_IDS.index(view_id)
    except ValueError:
        return len(VIEW_IDS)
