"""Load the three TRAIN-derived calibration artifacts for a production run.

This is the seam where an offline derivation becomes live policy, and it is the
last place anything can be checked. After it, Module 20 holds real reservations
and Module 21 chooses real actions; a wrong artifact here is a validation run
that looks correct and answers 478 rows under a policy nobody derived.

So it is a loader, not a reader. Each of the three files goes through **its own
canonical owner** - ``load_calibrations``, ``load_history``,
``load_planner_calibration`` - which already refuse a ``SYNTHETIC_TEST`` source.
What this adds is the binding those loaders cannot see:

* the bytes are the bytes the config named, when it declares a hash;
* all three came from **one** derivation of **one** collection;
* that derivation matches what the experiment expects.

There is no fallback anywhere in this file. A missing, malformed, synthetic,
mismatched or provenance-inconsistent artifact raises. Production has no
default budget and no default history: §16 and §17 both say the values are
calibrated on TRAIN, so a run without them is not a degraded run, it is a
different system.

The three files are also the **only** TRAIN-derived input production needs.
Nothing here reads gold, telemetry, predictions or a collection manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cover_kbc.control.historical_bins import HistoricalBinPackage, load_history
from cover_kbc.control.micro_planner import (
    MicroPlannerConfig,
    load_planner_calibration,
)
from cover_kbc.control.planner_types import PlannerCalibration
from cover_kbc.control.relation_budget import (
    RelationBudgetConfig,
    load_calibrations,
)
from cover_kbc.control.budget_types import RelationBudgetCalibration

#: Provenance fields that must agree across all three artifacts. If they do not,
#: the files are from different derivations and their numbers describe different
#: systems - a history binned under one collection cannot price budgets derived
#: from another.
SHARED_PROVENANCE_FIELDS = (
    "collection_repo_sha",
    "derivation_repo_sha",
    "train_sha256",
    "telemetry_sha256",
    "derivation_schema_version",
)


class ProductionCalibrationError(RuntimeError):
    """A production calibration artifact could not be trusted."""


@dataclass(frozen=True)
class ProductionCalibration:
    """The three artifacts, loaded through their owners and cross-checked."""

    budgets: Mapping[str, RelationBudgetCalibration]
    history: HistoricalBinPackage
    planner: PlannerCalibration
    #: The provenance block all three agree on, carried so a run manifest can
    #: record which calibration answered its rows.
    provenance: Mapping[str, Any]
    paths: Mapping[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "relations": sorted(self.budgets),
            "historical_bins": len(self.history.bins),
            "history_version": self.history.history_version,
            "planner_calibration_version": self.planner.calibration_version,
            "lookahead_depth": self.planner.lookahead_depth,
            "tau_continue": self.planner.tau_continue,
            "provenance": dict(self.provenance),
            "paths": dict(self.paths),
        }


def _resolve(raw: Any, base: Path, label: str) -> Path:
    if not raw:
        raise ProductionCalibrationError(
            f"{label}: production requires this artifact and the config names "
            "none")
    path = Path(str(raw))
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise ProductionCalibrationError(
            f"{label}: no artifact at {path}. Production has no default; the "
            "TRAIN-derived file must be present")
    return path


def _load_json(path: Path, label: str, expected_sha256: str | None) -> dict:
    raw = path.read_bytes()
    if expected_sha256:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise ProductionCalibrationError(
                f"{label}: {path} hashes to {actual}, but the config declares "
                f"{expected_sha256}. This is not the artifact the experiment "
                "was written against")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProductionCalibrationError(
            f"{label}: {path} is not readable JSON ({error})") from None
    if not isinstance(payload, dict):
        raise ProductionCalibrationError(f"{label}: {path} is not a JSON object")
    return payload


def _provenance(payload: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    block = payload.get("provenance")
    if not isinstance(block, Mapping) or not block:
        raise ProductionCalibrationError(
            f"{label}: the artifact carries no provenance block, so it cannot "
            "be bound to a collection or a derivation")
    return block


def load_production_calibration(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    expected_collection_repo_sha: str | None = None,
    expected_derivation_repo_sha: str | None = None,
) -> ProductionCalibration:
    """Load and cross-check the three artifacts a production run needs.

    Args:
        config: the experiment mapping. Its ``relation_budget_scheduler`` and
            ``micro_planner`` blocks name the artifacts and, optionally, their
            hashes.
        base_dir: root that relative artifact paths resolve against - normally
            the config's own directory, so a config and the files it names
            travel together.
        expected_collection_repo_sha: refuse unless the artifacts were derived
            from this collection.
        expected_derivation_repo_sha: refuse unless they were produced by this
            derivation commit.

    Raises:
        ProductionCalibrationError: on a missing, malformed, mis-hashed,
            synthetic, incomplete or provenance-inconsistent artifact. Never
            returns a partial or defaulted calibration.
    """
    base = Path(base_dir)
    budget_config = RelationBudgetConfig.from_mapping(
        config.get("relation_budget_scheduler"))
    planner_config = MicroPlannerConfig.from_mapping(config.get("micro_planner"))

    if not budget_config.is_production or not planner_config.is_production:
        raise ProductionCalibrationError(
            "production calibration was requested but "
            f"relation_budget_scheduler.mode={budget_config.mode!r} / "
            f"micro_planner.mode={planner_config.mode!r}; both must be "
            "'production' or the run is neither one thing nor the other")

    m20_path = _resolve(budget_config.calibration_file, base, "M20 budget")
    bins_path = _resolve(planner_config.historical_bins, base, "M21 history")
    planner_path = _resolve(
        planner_config.planner_calibration, base, "M21 planner calibration")

    m20_payload = _load_json(m20_path, "M20 budget",
                             budget_config.calibration_sha256)
    bins_payload = _load_json(bins_path, "M21 history",
                              planner_config.historical_bins_sha256)
    planner_payload = _load_json(planner_path, "M21 planner calibration",
                                 planner_config.planner_calibration_sha256)

    # Each artifact through its own canonical owner. `allow_synthetic` is left
    # at its default of False everywhere, which is what refuses a fixture.
    try:
        budgets = load_calibrations(m20_payload)
        history = load_history(bins_payload)
        planner = load_planner_calibration(planner_payload)
    except Exception as error:                                  # noqa: BLE001
        raise ProductionCalibrationError(
            f"a canonical loader refused an artifact: {error}") from None

    if not budgets:
        raise ProductionCalibrationError(
            "the M20 artifact declares no relation budgets")
    if not history.bins:
        raise ProductionCalibrationError(
            "the M21 history package contains no bins, so no action could be "
            "priced")

    # Depth-2 lookahead reads ``successors`` from the bin of *every* action it
    # ranks, and raises when one has none. A package whose coefficients ask for
    # depth 2 while some bin never observed a transition therefore fails at a
    # random row rather than at startup - after hours of a 478-row run. Checked
    # here, where the whole package is in hand and refusing is cheap.
    if planner.lookahead_depth == 2:
        starved = sorted(
            f"{entry.relation}/{entry.state_bin_key}/{entry.action_family.value}"
            for entry in history.bins if not entry.successors)
        if starved:
            raise ProductionCalibrationError(
                f"the planner calibration requests depth-2 lookahead but "
                f"{len(starved)} historical bin(s) record no successor "
                f"statistics, e.g. {starved[:3]}. Module 21 raises when it "
                "ranks an action from such a bin, so this package would fail "
                "mid-run. Either derive it with lookahead_depth 1, or derive a "
                "history in which every shipped bin observed a transition")

    blocks = {
        "M20 budget": _provenance(m20_payload, "M20 budget"),
        "M21 history": _provenance(bins_payload, "M21 history"),
        "M21 planner calibration": _provenance(
            planner_payload, "M21 planner calibration"),
    }
    reference_label, reference = next(iter(blocks.items()))
    for field in SHARED_PROVENANCE_FIELDS:
        values = {label: block.get(field) for label, block in blocks.items()}
        if len(set(map(str, values.values()))) != 1:
            raise ProductionCalibrationError(
                f"the three artifacts disagree on {field}: {values}. They are "
                "not one calibration and must not be mixed")

    for label, expected in (
        ("collection_repo_sha", expected_collection_repo_sha),
        ("derivation_repo_sha", expected_derivation_repo_sha),
    ):
        if expected and str(reference.get(label, "")) != expected:
            raise ProductionCalibrationError(
                f"calibration {label} is {reference.get(label)!r}, but this "
                f"experiment expects {expected!r}")

    del reference_label
    return ProductionCalibration(
        budgets=budgets, history=history, planner=planner,
        provenance=dict(reference),
        paths={
            "m20_relation_budget": str(m20_path),
            "m21_historical_bins": str(bins_path),
            "m21_planner_calibration": str(planner_path),
        },
    )


__all__ = [
    "SHARED_PROVENANCE_FIELDS",
    "ProductionCalibration",
    "ProductionCalibrationError",
    "load_production_calibration",
]
