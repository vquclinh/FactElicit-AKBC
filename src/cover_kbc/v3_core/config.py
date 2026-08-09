"""Typed opt-in configuration for the V3 core source path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class V3CoreMode(str, Enum):
    """How the V3 core may be used in this milestone."""

    #: Deterministic graph/action observability beside an ordinary run.
    SHADOW = "shadow"
    #: Future V3D collection over TRAIN. This still does not mean calibrated.
    TRAIN_COLLECTION = "train_collection"
    #: Calibrated V3 production. Configurable only after V3 artifacts exist.
    PRODUCTION = "production"


@dataclass(frozen=True)
class V3CoreConfig:
    """V3 is explicit and fail-closed.

    The default is a fully disabled object so constructing a normal
    :class:`~cover_kbc.pipeline.PipelineConfig` keeps the V2 baseline path
    structurally unchanged.
    """

    enabled: bool = False
    mode: V3CoreMode = V3CoreMode.SHADOW
    schema_version: str = "v3-core-v1"
    production_calibration_ready: bool = False

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "V3CoreConfig":
        data = dict(config or {})
        if "mode" in data:
            data["mode"] = V3CoreMode(data["mode"])
        return cls(
            **{k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "schema_version": self.schema_version,
            "production_calibration_ready": self.production_calibration_ready,
        }


__all__ = ["V3CoreConfig", "V3CoreMode"]
