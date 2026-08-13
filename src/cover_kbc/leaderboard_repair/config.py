"""Configuration for the downstream leaderboard repair stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


DEFAULT_CAPS = {
    "countryLandBordersCountry": 3,
    "companyTradesAtStockExchange": 4,
    "hasArea": 3,
    "hasCapacity": 3,
    "personHasCityOfDeath": 4,
    "awardWonBy": 8,
}


@dataclass(frozen=True)
class RepairFeatures:
    """Feature flags for the downstream leaderboard repair stack.

    All defaults are off so a config that omits `leaderboard_repair` exactly
    preserves the frozen pipeline.
    """

    stock_entity_guard: bool = False
    stock_alias_dedupe: bool = False
    stock_multi_listing_rescue: bool = False
    mistral_stock_empty_rescue: bool = False
    border_alias_dedupe: bool = False
    border_directional_sweep: bool = False
    border_reciprocity: bool = False
    death_existence_gate: bool = False
    death_city_recall: bool = False
    mistral_city_empty_rescue: bool = False
    area_empty_rescue: bool = False
    mistral_direct_area: bool = False
    mistral_area_multiview: bool = False
    mistral_capacity_multiview: bool = False
    capacity_repair: bool = False
    award_metadata_cleanup: bool = False
    award_recipient_witness: bool = False
    award_time_sliced_recall: bool = False
    #: Retired Audit-0082/C2 flags retained only so historical configs parse.
    #: The active runtime no longer implements retired C2 mutation branches.
    l8_consistency: bool = False
    l8_stock_consistency: bool = False
    l9_final_risk_guard: bool = False
    l9_stock_guard: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "RepairFeatures":
        fields = cls.__dataclass_fields__
        data = {
            key: bool(value)
            for key, value in dict(raw or {}).items()
            if key in fields
        }
        return cls(**data)


@dataclass(frozen=True)
class LeaderboardRepairConfig:
    """Top-level downstream leaderboard repair config block."""

    enabled: bool = False
    repair_version: str = "leaderboard-repair-default"
    profile: str = "off"
    features: RepairFeatures = field(default_factory=RepairFeatures)
    max_calls_by_relation: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_CAPS)
    )
    min_verifier_confidence: float = 0.55
    stock_rescue_min_confidence: float = 0.60
    numeric_cluster_tolerance: float = 0.05
    stock_empty_rescue_mode: str = "OFF"
    stock_empty_rescue_min_support: int = 3
    direct_area_mode: str = "OFF"
    area_multiview_mode: str = "OFF"
    capacity_multiview_mode: str = "OFF"
    artifacts_file: str = "leaderboard_repair.jsonl"
    accounting_file: str = "repair_accounting.json"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "LeaderboardRepairConfig":
        block = dict(raw or {})
        features = RepairFeatures.from_mapping(block.get("features"))
        caps = dict(DEFAULT_CAPS)
        caps.update({
            str(key): int(value)
            for key, value in dict(block.get("max_calls_by_relation") or {}).items()
        })
        return cls(
            enabled=bool(block.get("enabled", False)),
            repair_version=str(block.get("repair_version", "leaderboard-repair-default")),
            profile=str(block.get("profile", "off")),
            features=features,
            max_calls_by_relation=caps,
            min_verifier_confidence=float(block.get("min_verifier_confidence", 0.55)),
            stock_rescue_min_confidence=float(
                block.get("stock_rescue_min_confidence", 0.60)
            ),
            numeric_cluster_tolerance=float(block.get("numeric_cluster_tolerance", 0.05)),
            stock_empty_rescue_mode=str(block.get("stock_empty_rescue_mode", "OFF")),
            stock_empty_rescue_min_support=int(
                block.get("stock_empty_rescue_min_support", 3)
            ),
            direct_area_mode=str(block.get("direct_area_mode", "OFF")),
            area_multiview_mode=str(block.get("area_multiview_mode", "OFF")),
            capacity_multiview_mode=str(block.get("capacity_multiview_mode", "OFF")),
            artifacts_file=str(block.get("artifacts_file", "leaderboard_repair.jsonl")),
            accounting_file=str(block.get("accounting_file", "repair_accounting.json")),
        )

    def cap_for(self, relation: str) -> int:
        return int(self.max_calls_by_relation.get(relation, 0))


def build_config(raw: Mapping[str, Any] | None) -> LeaderboardRepairConfig:
    return LeaderboardRepairConfig.from_mapping(raw)
