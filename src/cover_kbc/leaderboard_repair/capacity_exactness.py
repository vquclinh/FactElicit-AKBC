"""Profile F1 suspicion-driven Capacity exactness repair."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Mapping, Sequence

from cover_kbc.models.base import LMRuntime
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.capacity import (
    CAPACITY_MULTIVIEW_FEATURE,
    VALID_CAPACITY,
    capacity_within_tolerance,
)
from cover_kbc.leaderboard_repair.config import (
    CapacityExactnessAcceptanceConfig,
    CapacityExactnessSuspicionConfig,
    LeaderboardRepairConfig,
)
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import RowBudget, RowRepairRecord
from cover_kbc.leaderboard_repair.util import CAPACITY


CAPACITY_EXACTNESS_FEATURE = "MistralCapacityExactnessRepair"
CAPACITY_EXACTNESS_MODE = "SUSPICIOUS_ONLY"
CAPACITY_EXACTNESS_LAYER = "F1_CAPACITY_EXACTNESS_REPAIR"

EXACTNESS_VIEW_IDS = (
    "capacity_exactness_x1",
    "capacity_exactness_x2",
    "capacity_exactness_x3",
    "capacity_exactness_x4",
)

VALID_RECALL = "VALID_RECALL"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"

CONFIG_LABELS = (
    "SEATED",
    "TOTAL",
    "STANDING",
    "SPORT",
    "CONCERT",
    "HISTORICAL",
    "TEMPORARY",
    "UNKNOWN",
)
EXACTNESS_LABELS = ("EXACT", "APPROXIMATE", "ESTIMATE", "UNKNOWN")

CAPACITY_EMPTY = "CAPACITY_EMPTY"
CAPACITY_ORIGINAL_JUDGE = "CAPACITY_ORIGINAL_JUDGE"
CAPACITY_FALLBACK_V1 = "CAPACITY_FALLBACK_V1"
CAPACITY_FALLBACK_CLUSTER = "CAPACITY_FALLBACK_CLUSTER"
CAPACITY_ROUND_1K = "CAPACITY_ROUND_1K"
CAPACITY_ROUND_5K = "CAPACITY_ROUND_5K"
CAPACITY_ROUND_10K = "CAPACITY_ROUND_10K"
CAPACITY_REPEATED_VALUE_PRIOR = "CAPACITY_REPEATED_VALUE_PRIOR"
CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS = "CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS"

CAPACITY_REPAIR_EMPTY_STRONG = "CAPACITY_REPAIR_EMPTY_STRONG"
CAPACITY_REPAIR_CORROBORATES_E3 = "CAPACITY_REPAIR_CORROBORATES_E3"
CAPACITY_REPAIR_STRONG_OVERRIDE = "CAPACITY_REPAIR_STRONG_OVERRIDE"
CAPACITY_REPAIR_REJECTED = "CAPACITY_REPAIR_REJECTED"
CAPACITY_REPAIR_NOT_SUSPICIOUS = "CAPACITY_REPAIR_NOT_SUSPICIOUS"

_STRUCTURED_CAPACITY_RE = re.compile(
    r"^CAPACITY:\s*((?:\d+|\d{1,3}(?:,\d{3})+))$"
)
_CONFIG_RE = re.compile(r"^CONFIG:\s*([A-Z]+)$")
_EXACTNESS_RE = re.compile(r"^EXACTNESS:\s*([A-Z]+)$")

CAPACITY_EXACTNESS_SYSTEM_PROMPT = (
    "You are a precise closed-book factual knowledge-base completion assistant.\n\n"
    "Use only factual knowledge encoded in the model.\n\n"
    "The relation hasCapacity means the maximum spectator capacity of the exact\n"
    "named venue, expressed as an integer number of people.\n\n"
    "Return only the strict structured output requested by the user."
)


@dataclass(frozen=True)
class CapacityExactnessRecall:
    """One valid candidate-blind exactness recall."""

    value: int
    config: str
    exactness: str
    view_id: str

    @property
    def known_config(self) -> bool:
        return self.config != UNKNOWN

    def to_json(self) -> dict[str, object]:
        return {
            "view": self.view_id,
            "value": self.value,
            "config": self.config,
            "exactness": self.exactness,
            "roundness": capacity_roundness_metadata(self.value),
        }


@dataclass(frozen=True)
class ParsedCapacityExactness:
    """Strict parser result for one F1 exactness view."""

    status: str
    recall: CapacityExactnessRecall | None = None

    @property
    def valid(self) -> bool:
        return self.status == VALID_RECALL and self.recall is not None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "value": self.recall.value if self.recall is not None else "",
            "config": self.recall.config if self.recall is not None else "",
            "exactness": self.recall.exactness if self.recall is not None else "",
        }


@dataclass(frozen=True)
class CapacityExactnessCluster:
    """A 5%-compatible cluster of F1 exactness recalls."""

    recalls: tuple[CapacityExactnessRecall, ...]

    @property
    def support(self) -> int:
        return len(self.recalls)

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(recall.value for recall in self.recalls)

    @property
    def views(self) -> tuple[str, ...]:
        return tuple(recall.view_id for recall in self.recalls)

    @property
    def exact_count(self) -> int:
        return sum(1 for recall in self.recalls if recall.exactness == "EXACT")

    @property
    def approximate_count(self) -> int:
        return sum(1 for recall in self.recalls if recall.exactness == "APPROXIMATE")

    @property
    def estimate_count(self) -> int:
        return sum(1 for recall in self.recalls if recall.exactness == "ESTIMATE")

    @property
    def known_config_count(self) -> int:
        return sum(1 for recall in self.recalls if recall.known_config)

    @property
    def first_view_rank(self) -> int:
        return min(_view_rank(recall.view_id) for recall in self.recalls)

    @property
    def representative(self) -> int:
        med = Decimal(str(median(self.values)))
        return min(
            self.recalls,
            key=lambda recall: (
                abs(Decimal(recall.value) - med),
                _view_rank(recall.view_id),
                recall.value,
            ),
        ).value

    def to_json(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "views": list(self.views),
            "support": self.support,
            "representative": self.representative,
            "exact_count": self.exact_count,
            "approximate_count": self.approximate_count,
            "estimate_count": self.estimate_count,
            "known_config_count": self.known_config_count,
            "roundness": capacity_roundness_metadata(self.representative),
            "recalls": [recall.to_json() for recall in self.recalls],
        }


@dataclass(frozen=True)
class CapacitySuspicionResult:
    """Deterministic suspicion score for one Capacity row."""

    suspicious: bool
    risk_score: float
    risk_flags: tuple[str, ...]
    value: int | None = None
    original_reason: str = ""
    repeated_value_subject_count: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "suspicious": self.suspicious,
            "risk_score": self.risk_score,
            "risk_flags": list(self.risk_flags),
            "value": self.value if self.value is not None else "",
            "original_reason": self.original_reason,
            "repeated_value_subject_count": self.repeated_value_subject_count,
        }


@dataclass(frozen=True)
class CapacityExactnessDecision:
    """Final F1 keep-or-replace decision."""

    values: tuple[str, ...]
    reason: str
    action: str
    top_cluster: CapacityExactnessCluster | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "reason": self.reason,
            "action": self.action,
            "top_cluster": (
                self.top_cluster.to_json() if self.top_cluster is not None else {}
            ),
        }


class CapacitySuspicionScorer:
    """Zero-call deterministic scorer for current-batch Capacity predictions."""

    def __init__(self, config: CapacityExactnessSuspicionConfig | None = None) -> None:
        self.config = config or CapacityExactnessSuspicionConfig()

    def score_batch(
        self,
        predictions: Sequence[Prediction],
        records_by_key: Mapping[tuple[str, str], RowRepairRecord] | None = None,
    ) -> dict[tuple[str, str], CapacitySuspicionResult]:
        capacity_predictions = [
            prediction for prediction in predictions if prediction.relation == CAPACITY
        ]
        value_subjects: dict[int, set[str]] = defaultdict(set)
        for prediction in capacity_predictions:
            value = _single_capacity_value(prediction.object_entities)
            if value is not None:
                value_subjects[value].add(prediction.subject)

        out: dict[tuple[str, str], CapacitySuspicionResult] = {}
        for prediction in capacity_predictions:
            key = (prediction.subject, prediction.relation)
            record = (records_by_key or {}).get(key)
            value = _single_capacity_value(prediction.object_entities)
            out[key] = self._score_one(
                prediction,
                record,
                value=value,
                repeated_subject_count=(
                    len(value_subjects[value]) if value is not None else 0
                ),
            )
        return out

    def _score_one(
        self,
        prediction: Prediction,
        record: RowRepairRecord | None,
        *,
        value: int | None,
        repeated_subject_count: int,
    ) -> CapacitySuspicionResult:
        flags: list[str] = []
        original_reason = _original_capacity_final_reason(record)

        if self.config.empty and not prediction.object_entities:
            flags.append(CAPACITY_EMPTY)

        if self.config.ambiguous_original:
            flag = _original_reason_flag(original_reason)
            if flag:
                flags.append(flag)

        if value is not None:
            flags.extend(capacity_round_flags(value))
            if (
                self.config.repeated_value_threshold > 1
                and repeated_subject_count >= self.config.repeated_value_threshold
            ):
                flags.append(CAPACITY_REPEATED_VALUE_PRIOR)

        if (
            self.config.homogeneous_round_consensus
            and _has_homogeneous_round_original_consensus(record)
        ):
            flags.append(CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS)

        round_flag_count = sum(
            1
            for flag in flags
            if flag in {CAPACITY_ROUND_1K, CAPACITY_ROUND_5K, CAPACITY_ROUND_10K}
        )
        suspicious = any(
            flag in flags
            for flag in (
                CAPACITY_EMPTY,
                CAPACITY_ORIGINAL_JUDGE,
                CAPACITY_FALLBACK_V1,
                CAPACITY_FALLBACK_CLUSTER,
                CAPACITY_REPEATED_VALUE_PRIOR,
                CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS,
            )
        ) or round_flag_count >= self.config.minimum_round_flags

        return CapacitySuspicionResult(
            suspicious=suspicious,
            risk_score=float(len(flags)),
            risk_flags=tuple(dict.fromkeys(flags)),
            value=value,
            original_reason=original_reason,
            repeated_value_subject_count=repeated_subject_count,
        )


def capacity_exactness_view_prompt(subject: str, view_id: str) -> str:
    """Render one of the four candidate-blind F1 exactness prompts."""
    format_contract = (
        "Return exactly one of:\n\n"
        "CAPACITY: <integer>\n"
        "CONFIG: <label>\n"
        "EXACTNESS: <label>\n\n"
        "or:\n\n"
        "UNKNOWN\n\n"
        "Allowed CONFIG labels: SEATED, TOTAL, STANDING, SPORT, CONCERT,\n"
        "HISTORICAL, TEMPORARY, UNKNOWN.\n\n"
        "Allowed EXACTNESS labels: EXACT, APPROXIMATE, ESTIMATE, UNKNOWN.\n\n"
        "Do not return prose, ranges, multiple numbers, attendance records, or\n"
        "unrelated numeric facts."
    )
    if view_id == "capacity_exactness_x1":
        return (
            f"Subject: {subject}\n\n"
            "We need the published spectator-capacity fact for this exact venue.\n\n"
            "Recall the most specific encyclopedic/reference capacity number you know\n"
            "for the exact venue.\n\n"
            "Do not round a number merely because the exact value is uncertain.\n\n"
            "If you only have a rough magnitude such as about ten thousand, mark it as\n"
            "ESTIMATE or return UNKNOWN.\n\n"
            "Return the highest legitimate published spectator capacity when multiple\n"
            "configurations exist.\n\n"
            "Explicitly distinguish total, seated, standing, sport, concert,\n"
            "historical, and temporary configurations.\n\n"
            + format_contract
        )
    if view_id == "capacity_exactness_x2":
        return (
            f"Subject: {subject}\n\n"
            "First internally disambiguate the exact venue using the full subject\n"
            "string.\n\n"
            "Then recall whether a distinctive, non-generic capacity number is\n"
            "associated with that exact venue.\n\n"
            "Do not substitute a generic round capacity typical of similar venues.\n\n"
            "If the only memory is approximate, say ESTIMATE or UNKNOWN rather than\n"
            "pretending it is exact.\n\n"
            + format_contract
        )
    if view_id == "capacity_exactness_x3":
        return (
            f"Subject: {subject}\n\n"
            "Retrieve possible published capacity configurations for the exact venue\n"
            "and select the highest legitimate spectator capacity.\n\n"
            "Internally consider seated, total, standing, sport, concert, historical,\n"
            "post-renovation, and temporary configurations.\n\n"
            "Do not confuse capacity with record attendance.\n\n"
            "Output the chosen capacity together with which configuration memory\n"
            "supports it and whether the recalled number is exact, approximate, or\n"
            "estimated.\n\n"
            + format_contract
        )
    if view_id == "capacity_exactness_x4":
        return (
            f"Subject: {subject}\n\n"
            "Reason silently.\n\n"
            "Ask yourself whether the first number that comes to mind is an exact\n"
            "factual capacity associated with this exact venue, or a generic plausible\n"
            "rounded number for a venue of this type.\n\n"
            "Only return a capacity as EXACT when you have specific factual memory.\n\n"
            "If uncertain, mark APPROXIMATE/ESTIMATE or return UNKNOWN.\n\n"
            "Then provide the highest supported published capacity.\n\n"
            "Do not reveal reasoning.\n\n"
            + format_contract
        )
    raise ValueError(f"unknown Capacity exactness view_id {view_id!r}")


def parse_capacity_exactness_output(
    text: str,
    *,
    view_id: str = "",
) -> ParsedCapacityExactness:
    """Parse the strict F1 exactness output contract."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) == 1 and lines[0].upper() == UNKNOWN:
        return ParsedCapacityExactness(UNKNOWN)
    if len(lines) != 3:
        return ParsedCapacityExactness(INVALID)

    cap_match = _STRUCTURED_CAPACITY_RE.fullmatch(lines[0])
    config_match = _CONFIG_RE.fullmatch(lines[1])
    exactness_match = _EXACTNESS_RE.fullmatch(lines[2])
    if not cap_match or not config_match or not exactness_match:
        return ParsedCapacityExactness(INVALID)

    config = config_match.group(1)
    exactness = exactness_match.group(1)
    if config not in CONFIG_LABELS or exactness not in EXACTNESS_LABELS:
        return ParsedCapacityExactness(INVALID)

    try:
        number = Decimal(cap_match.group(1).replace(",", ""))
    except InvalidOperation:
        return ParsedCapacityExactness(INVALID)
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        return ParsedCapacityExactness(INVALID)
    return ParsedCapacityExactness(
        VALID_RECALL,
        recall=CapacityExactnessRecall(
            value=int(number),
            config=config,
            exactness=exactness,
            view_id=view_id,
        ),
    )


def cluster_capacity_exactness_recalls(
    recalls: Sequence[CapacityExactnessRecall],
    *,
    tolerance: float = 0.05,
) -> list[CapacityExactnessCluster]:
    """Cluster valid F1 exactness recalls with benchmark-compatible tolerance."""
    clusters: list[list[CapacityExactnessRecall]] = []
    for recall in recalls:
        placed = False
        for cluster in clusters:
            if all(
                capacity_within_tolerance(
                    recall.value,
                    member.value,
                    tolerance=tolerance,
                )
                for member in cluster
            ):
                cluster.append(recall)
                placed = True
                break
        if not placed:
            clusters.append([recall])
    return [CapacityExactnessCluster(tuple(cluster)) for cluster in clusters]


def top_capacity_exactness_cluster(
    clusters: Sequence[CapacityExactnessCluster],
) -> CapacityExactnessCluster | None:
    if not clusters:
        return None
    return min(
        clusters,
        key=lambda cluster: (
            -cluster.support,
            -cluster.exact_count,
            -cluster.known_config_count,
            cluster.first_view_rank,
            cluster.representative,
        ),
    )


def decide_capacity_exactness_repair(
    *,
    e3_values: Sequence[str],
    clusters: Sequence[CapacityExactnessCluster],
    acceptance: CapacityExactnessAcceptanceConfig | None = None,
) -> CapacityExactnessDecision:
    """Apply the conservative F1 keep-or-replace policy."""
    policy = acceptance or CapacityExactnessAcceptanceConfig()
    top = top_capacity_exactness_cluster(clusters)
    old = _single_capacity_value(e3_values)

    if top is None:
        return CapacityExactnessDecision(
            values=tuple(e3_values),
            reason=CAPACITY_REPAIR_REJECTED,
            action="KEEP_E3",
        )

    strong_base = (
        top.support >= policy.min_support
        and top.exact_count >= policy.min_exact
        and top.known_config_count >= policy.min_known_config
    )
    representative = top.representative
    replacement = (str(representative),)

    if not e3_values:
        if strong_base:
            return CapacityExactnessDecision(
                values=replacement,
                reason=CAPACITY_REPAIR_EMPTY_STRONG,
                action="REPLACE",
                top_cluster=top,
            )
        return CapacityExactnessDecision(
            values=(),
            reason=CAPACITY_REPAIR_REJECTED,
            action="KEEP_E3",
            top_cluster=top,
        )

    if old is None:
        return CapacityExactnessDecision(
            values=tuple(e3_values),
            reason=CAPACITY_REPAIR_REJECTED,
            action="KEEP_E3",
            top_cluster=top,
        )

    if capacity_within_tolerance(old, representative, tolerance=policy.tolerance):
        return CapacityExactnessDecision(
            values=tuple(e3_values),
            reason=CAPACITY_REPAIR_CORROBORATES_E3,
            action="KEEP_E3",
            top_cluster=top,
        )

    metadata = capacity_roundness_metadata(representative)
    strong_round_override = top.support == 4 and top.exact_count >= 3
    if strong_base and (
        bool(metadata["non_round_distinctive"]) or strong_round_override
    ):
        return CapacityExactnessDecision(
            values=replacement,
            reason=CAPACITY_REPAIR_STRONG_OVERRIDE,
            action="REPLACE",
            top_cluster=top,
        )
    return CapacityExactnessDecision(
        values=tuple(e3_values),
        reason=CAPACITY_REPAIR_REJECTED,
        action="KEEP_E3",
        top_cluster=top,
    )


def apply_capacity_exactness_repair(
    predictions: Sequence[Prediction],
    *,
    records_by_key: Mapping[tuple[str, str], RowRepairRecord],
    config: LeaderboardRepairConfig,
    enumerator: LMRuntime,
    verifier: LMRuntime,
) -> list[Prediction]:
    """Run F1 exactness repair as a second pass over current E3 predictions."""
    if not _capacity_exactness_enabled(config):
        return list(predictions)
    repair_config = config.capacity_exactness_repair
    if repair_config.mode != CAPACITY_EXACTNESS_MODE:
        raise ValueError(
            f"unsupported Capacity exactness mode {repair_config.mode!r}; "
            f"expected {CAPACITY_EXACTNESS_MODE!r}"
        )
    if repair_config.max_repair_calls_per_suspicious_row != len(EXACTNESS_VIEW_IDS):
        raise ValueError("Capacity exactness repair must run exactly four views")

    scorer = CapacitySuspicionScorer(repair_config.suspicion)
    suspicion_by_key = scorer.score_batch(predictions, records_by_key)
    repaired: list[Prediction] = []
    for prediction in predictions:
        if prediction.relation != CAPACITY:
            repaired.append(prediction)
            continue
        key = (prediction.subject, prediction.relation)
        record = records_by_key[key]
        suspicion = suspicion_by_key[key]
        record.add_decision(
            CAPACITY_EXACTNESS_FEATURE,
            "suspicion_score",
            **suspicion.to_json(),
        )
        if not suspicion.suspicious:
            record.add_decision(
                CAPACITY_EXACTNESS_FEATURE,
                "final_decision",
                original_e3_answer=list(prediction.object_entities),
                final_answer=list(prediction.object_entities),
                reason=CAPACITY_REPAIR_NOT_SUSPICIOUS,
                action="KEEP_E3",
                f1_repair_calls=0,
            )
            repaired.append(prediction)
            continue

        budget = RowBudget(cap=config.cap_for(CAPACITY), calls=list(record.calls))
        before_calls = len(budget.calls)
        needed = repair_config.max_repair_calls_per_suspicious_row
        if budget.remaining < needed:
            raise RuntimeError(
                "Capacity exactness repair requires four remaining calls for each "
                "suspicious row"
            )
        caller = RepairCaller(
            enumerator=enumerator,
            verifier=verifier,
            relation=prediction.relation,
            subject=prediction.subject,
            row_budget=budget,
        )
        recalls = _run_exactness_views(
            prediction.subject,
            caller,
            record,
        )
        record.calls = list(budget.calls)
        clusters = cluster_capacity_exactness_recalls(
            recalls,
            tolerance=repair_config.acceptance.tolerance,
        )
        record.add_decision(
            CAPACITY_EXACTNESS_FEATURE,
            "clustered_numeric_outputs",
            clusters=[cluster.to_json() for cluster in clusters],
        )
        decision = decide_capacity_exactness_repair(
            e3_values=prediction.object_entities,
            clusters=clusters,
            acceptance=repair_config.acceptance,
        )
        final_prediction = replace(prediction, object_entities=list(decision.values))
        record.after = list(final_prediction.object_entities)
        record.add_decision(
            CAPACITY_EXACTNESS_FEATURE,
            "final_decision",
            original_e3_answer=list(prediction.object_entities),
            final_answer=list(final_prediction.object_entities),
            f1_repair_calls=len(budget.calls) - before_calls,
            **decision.to_json(),
        )
        repaired.append(final_prediction)
    return repaired


def capacity_exactness_accounting(
    records: Sequence[RowRepairRecord],
) -> dict[str, object]:
    """Summarise the F1 exactness pass from row records."""
    summary: dict[str, object] = {
        "total_capacity_rows": 0,
        "suspicious_rows": 0,
        "untouched_rows": 0,
        "overridden_rows": 0,
        "corroborated_rows": 0,
        "rejected_repair_rows": 0,
        "empty_rescued_rows": 0,
        "total_capacity_exactness_repair_calls": 0,
        "capacity_exactness_x1_calls": 0,
        "capacity_exactness_x2_calls": 0,
        "capacity_exactness_x3_calls": 0,
        "capacity_exactness_x4_calls": 0,
        "risk_flags": {},
        "final_reasons": {},
    }
    risk_flags: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for record in records:
        if record.relation != CAPACITY:
            continue
        summary["total_capacity_rows"] = int(summary["total_capacity_rows"]) + 1
        suspicion = _latest_decision(record, "suspicion_score")
        final = _latest_decision(record, "final_decision")
        if suspicion and bool(suspicion.get("suspicious", False)):
            summary["suspicious_rows"] = int(summary["suspicious_rows"]) + 1
        else:
            summary["untouched_rows"] = int(summary["untouched_rows"]) + 1
        for flag in list((suspicion or {}).get("risk_flags") or []):
            risk_flags[str(flag)] += 1
        reason = str((final or {}).get("reason", ""))
        if reason:
            reasons[reason] += 1
        if reason == CAPACITY_REPAIR_STRONG_OVERRIDE:
            summary["overridden_rows"] = int(summary["overridden_rows"]) + 1
        elif reason == CAPACITY_REPAIR_CORROBORATES_E3:
            summary["corroborated_rows"] = int(summary["corroborated_rows"]) + 1
        elif reason == CAPACITY_REPAIR_REJECTED:
            summary["rejected_repair_rows"] = int(summary["rejected_repair_rows"]) + 1
        elif reason == CAPACITY_REPAIR_EMPTY_STRONG:
            summary["empty_rescued_rows"] = int(summary["empty_rescued_rows"]) + 1

        calls = [
            call
            for call in record.calls
            if call.feature == CAPACITY_EXACTNESS_FEATURE
        ]
        summary["total_capacity_exactness_repair_calls"] = (
            int(summary["total_capacity_exactness_repair_calls"]) + len(calls)
        )
        for decision in record.decisions:
            if (
                decision.get("feature") != CAPACITY_EXACTNESS_FEATURE
                or decision.get("decision") != "view_output"
            ):
                continue
            key = f"{decision.get('view_id', '')}_calls"
            if key in summary:
                summary[key] = int(summary[key]) + 1

    summary["risk_flags"] = dict(sorted(risk_flags.items()))
    summary["final_reasons"] = dict(sorted(reasons.items()))
    return summary


def capacity_round_flags(value: int) -> list[str]:
    flags: list[str] = []
    if value > 0 and value % 1000 == 0:
        flags.append(CAPACITY_ROUND_1K)
    if value > 0 and value % 5000 == 0:
        flags.append(CAPACITY_ROUND_5K)
    if value > 0 and value % 10000 == 0:
        flags.append(CAPACITY_ROUND_10K)
    return flags


def capacity_roundness_metadata(value: int) -> dict[str, bool]:
    return {
        "divisible_by_1000": value > 0 and value % 1000 == 0,
        "divisible_by_5000": value > 0 and value % 5000 == 0,
        "divisible_by_10000": value > 0 and value % 10000 == 0,
        "non_round_distinctive": value > 0 and value % 1000 != 0,
    }


def _run_exactness_views(
    subject: str,
    caller: RepairCaller,
    record: RowRepairRecord,
) -> list[CapacityExactnessRecall]:
    recalls: list[CapacityExactnessRecall] = []
    for view_id in EXACTNESS_VIEW_IDS:
        text = caller.generate(
            role="verifier",
            layer=CAPACITY_EXACTNESS_LAYER,
            feature=CAPACITY_EXACTNESS_FEATURE,
            system_prompt=CAPACITY_EXACTNESS_SYSTEM_PROMPT,
            prompt=capacity_exactness_view_prompt(subject, view_id),
            view_id=view_id,
            max_new_tokens=48,
        )
        parsed = parse_capacity_exactness_output(text or "", view_id=view_id)
        if parsed.valid and parsed.recall is not None:
            recalls.append(parsed.recall)
        record.add_decision(
            CAPACITY_EXACTNESS_FEATURE,
            "view_output",
            view_id=view_id,
            raw=text or "",
            **parsed.to_json(),
        )
    return recalls


def _capacity_exactness_enabled(config: LeaderboardRepairConfig) -> bool:
    return (
        config.features.mistral_capacity_exactness_repair
        and config.capacity_exactness_repair.enabled
    )


def _latest_decision(record: RowRepairRecord, decision: str) -> dict[str, object] | None:
    for item in reversed(record.decisions):
        if item.get("feature") == CAPACITY_EXACTNESS_FEATURE and item.get("decision") == decision:
            return item
    return None


def _single_capacity_value(values: Sequence[str]) -> int | None:
    if len(values) != 1:
        return None
    text = str(values[0]).strip().replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    value = int(text)
    return value if value > 0 else None


def _original_capacity_final_reason(record: RowRepairRecord | None) -> str:
    if record is None:
        return ""
    for decision in reversed(record.decisions):
        if (
            decision.get("feature") == CAPACITY_MULTIVIEW_FEATURE
            and decision.get("decision") == "final_decision"
        ):
            return str(decision.get("reason", ""))
    return ""


def _original_reason_flag(reason: str) -> str:
    return {
        "judge_selected": CAPACITY_ORIGINAL_JUDGE,
        "fallback_v1": CAPACITY_FALLBACK_V1,
        "fallback_top_cluster": CAPACITY_FALLBACK_CLUSTER,
    }.get(reason, "")


def _has_homogeneous_round_original_consensus(
    record: RowRepairRecord | None,
) -> bool:
    values = _original_capacity_view_values(record)
    if len(values) < 3:
        return False
    exact_counts = Counter(values)
    return any(count >= 3 and value % 1000 == 0 for value, count in exact_counts.items())


def _original_capacity_view_values(record: RowRepairRecord | None) -> list[int]:
    if record is None:
        return []
    values: list[int] = []
    for decision in record.decisions:
        if (
            decision.get("feature") == CAPACITY_MULTIVIEW_FEATURE
            and decision.get("decision") == "view_output"
            and decision.get("status") == VALID_CAPACITY
        ):
            value = _coerce_positive_int(decision.get("value"))
            if value is not None:
                values.append(value)
    return values


def _coerce_positive_int(value: object) -> int | None:
    try:
        number = int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _view_rank(view_id: str) -> int:
    try:
        return EXACTNESS_VIEW_IDS.index(view_id)
    except ValueError:
        return len(EXACTNESS_VIEW_IDS)
