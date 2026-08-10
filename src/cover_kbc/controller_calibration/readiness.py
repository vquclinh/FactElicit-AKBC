"""Is this profile allowed to start a full VALIDATION run?

The expensive failure this prevents is a VALIDATION run that *succeeds*: the
uncalibrated controller silently falls back, 478 rows are answered by a system
nobody intended, and the leaderboard number describes neither the core path nor
the upgraded one. A crash costs an hour; that outcome costs a submission and is
not visible in the artifacts.

So readiness is a single deterministic verdict with the reasons attached, and
its default is refusal. Every requirement below is checked against files that
must exist on disk and declare themselves TRAIN-derived - never against a
configuration flag asserting that calibration happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from cover_kbc.v3_1.compatibility import SAFE as V3_1_SAFE, compatibility_of
from cover_kbc.v3_1.config import V31Config

FROZEN_ENUMERATOR_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
FROZEN_ENUMERATOR_REVISION = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
FROZEN_VERIFIER_ID = "Qwen/Qwen3.5-4B"
FROZEN_VERIFIER_REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
FROZEN_PARAMETER_TOTAL = 28_671_226_368
PARAMETER_LIMIT = 32_000_000_000


class ReadinessState(str, Enum):
    """What this profile may legally be used for right now."""

    #: The upgraded seams are wired; TRAIN collection may run.
    CALIBRATION_COLLECTION_READY = "CALIBRATION_COLLECTION_READY"
    #: Real TRAIN-derived controller artifacts are present and consistent.
    FULL_VALIDATION_READY = "FULL_VALIDATION_READY"
    #: The same production system, cleared for the blind official TEST split.
    #: A separate state from ``FULL_VALIDATION_READY`` on purpose: TEST has
    #: requirements validation does not - the split is blind, no evaluator may
    #: run, and the dataset's exact identity has to be the one the config
    #: declares - so a val-ready profile must not read as test-ready.
    FULL_TEST_READY = "FULL_TEST_READY"
    #: The same production system, pointed at labelled TRAIN to be *measured*
    #: rather than scored (milestone V3A). Its own state because a TRAIN
    #: diagnostic run has a requirement neither leaderboard run has - it must
    #: actually record failure telemetry, or it produces nothing - and because
    #: a TRAIN-ready profile must never read as cleared for VAL or TEST.
    TRAIN_DIAGNOSTIC_READY = "TRAIN_DIAGNOSTIC_READY"
    #: V3 core source/action catalogues are wired for future TRAIN collection,
    #: but production calibration and official TEST are still forbidden.
    V3_TRAIN_COLLECTION_READY = "V3_TRAIN_COLLECTION_READY"
    #: Neither - the profile is incomplete or inconsistent.
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class ReadinessReport:
    """A verdict plus every reason behind it."""

    state: ReadinessState
    blockers: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def may_run_validation(self) -> bool:
        return self.state is ReadinessState.FULL_VALIDATION_READY

    @property
    def may_run_test(self) -> bool:
        """Cleared for the blind official split, and only by the TEST gate.

        Deliberately not satisfied by ``FULL_VALIDATION_READY``: a val-ready
        profile has not been checked against the test dataset's identity and
        has not been shown to be free of evaluator and gold dependencies.
        """
        return self.state is ReadinessState.FULL_TEST_READY

    @property
    def may_run_collection(self) -> bool:
        return self.state in (
            ReadinessState.CALIBRATION_COLLECTION_READY,
            ReadinessState.FULL_VALIDATION_READY,
            ReadinessState.V3_TRAIN_COLLECTION_READY,
        )

    @property
    def may_run_train_diagnostic(self) -> bool:
        """Cleared to run the calibrated system over labelled TRAIN.

        Like :attr:`may_run_test`, satisfied only by its own gate. A val-ready
        or test-ready profile has not been checked for the one thing a
        diagnostic run is *for*: that it will actually record telemetry.
        """
        return self.state is ReadinessState.TRAIN_DIAGNOSTIC_READY

    @property
    def may_run_v3_train_collection(self) -> bool:
        """Cleared for V3 TRAIN collection mechanics, not V3 production."""
        return self.state is ReadinessState.V3_TRAIN_COLLECTION_READY

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "may_run_collection": self.may_run_collection,
            "may_run_validation": self.may_run_validation,
            "may_run_test": self.may_run_test,
            "may_run_train_diagnostic": self.may_run_train_diagnostic,
            "may_run_v3_train_collection": self.may_run_v3_train_collection,
            "blockers": list(self.blockers),
            "satisfied": list(self.satisfied),
            "details": dict(self.details),
        }


def _load(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: not valid JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _check_artifact(
    label: str, raw: object, *, base: Path,
    blockers: list[str], satisfied: list[str], details: dict[str, Any],
) -> Mapping[str, Any] | None:
    """Resolve one declared artifact path, refusing anything unverifiable."""
    if not raw:
        blockers.append(f"{label}: no artifact path is configured")
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        # A configured-but-absent path is the fake-path case the brief forbids;
        # it must never read as "calibration exists".
        blockers.append(f"{label}: configured path does not exist ({path})")
        return None
    try:
        payload = _load(path)
    except ValueError as error:
        blockers.append(f"{label}: {error}")
        return None
    details[label] = str(path)
    satisfied.append(f"{label}: present at {path}")
    return payload


def evaluate_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
) -> ReadinessReport:
    """Decide what ``config`` may be used for. Never raises on a bad profile.

    Args:
        config: the loaded experiment mapping.
        base_dir: root that relative artifact paths resolve against.

    Returns:
        A report whose default is refusal. Missing artifacts are blockers for
        VALIDATION but never for collection - collecting is precisely how the
        missing artifacts get made.
    """
    base = Path(base_dir)
    blockers: list[str] = []
    satisfied: list[str] = []
    details: dict[str, Any] = {}

    budget_block = dict(config.get("relation_budget_scheduler") or {})
    planner_block = dict(config.get("micro_planner") or {})

    if not budget_block.get("enabled"):
        blockers.append("M20: relation_budget_scheduler.enabled is false")
    if not planner_block.get("enabled"):
        blockers.append("M21: micro_planner.enabled is false")

    m20 = _check_artifact(
        "M20 relation budget calibration", budget_block.get("calibration_file"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if m20 is not None:
        entries = m20.get("relations") or m20.get("calibrations") or []
        sources = {str(e.get("calibration_source", "")) for e in entries}
        if not entries:
            blockers.append("M20: artifact declares no relation calibrations")
        elif sources != {"TRAIN_CALIBRATED"}:
            # A fixture reaching production is the exact substitution the
            # SYNTHETIC_TEST marker exists to make impossible.
            blockers.append(
                f"M20: artifact is not purely TRAIN_CALIBRATED (found {sorted(sources)})")
        else:
            satisfied.append(f"M20: {len(entries)} TRAIN_CALIBRATED relation budgets")

    bins = _check_artifact(
        "M21 historical bins", planner_block.get("historical_bins"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if bins is not None and not (bins.get("bins") or ()):
        blockers.append("M21: historical bin package contains no bins")

    calibration = _check_artifact(
        "M21 planner calibration", planner_block.get("planner_calibration"),
        base=base, blockers=blockers, satisfied=satisfied, details=details)
    if calibration is not None:
        missing = [name for name in
                   ("alpha", "beta", "gamma", "delta", "eta", "kappa", "tau_continue")
                   if name not in calibration]
        if missing:
            blockers.append(f"M21: planner calibration is missing {missing}")

    if blockers:
        # Collection needs the seams, not the artifacts - so a profile blocked
        # only by absent calibration is still exactly what collection runs on.
        state = ReadinessState.CALIBRATION_COLLECTION_READY
        for blocker in blockers:
            if "does not exist" not in blocker and "no artifact path" not in blocker \
                    and "enabled is false" not in blocker:
                state = ReadinessState.NOT_READY
                break
        return ReadinessReport(state, tuple(blockers), tuple(satisfied), details)

    return ReadinessReport(
        ReadinessState.FULL_VALIDATION_READY, (), tuple(satisfied), details)


# --------------------------------------------------------------------------
# Collection readiness
# --------------------------------------------------------------------------

#: The upgraded stack TRAIN calibration collection has to observe, as
#: ``(config path, label)``. Anything disabled here is not a degraded run - it
#: is a run that emits no action telemetry at all, which is exactly the outcome
#: Audit 0041 F-01 caught reporting success.
REQUIRED_COLLECTION_MODULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("query_intelligence", "profiler"), "M9 risk profiler"),
    (("query_intelligence", "prompt_compiler"), "M10 prompt program compiler"),
    (("query_intelligence", "parametric_retrieval"), "M11 parametric retrieval"),
    (("specialists", "numeric"), "M12 numeric specialist"),
    (("specialists", "large_open_set"), "M13 large-open-set specialist"),
    (("specialists", "null_temporal"), "M14 null/temporal specialist"),
    (("specialists", "small_set_closure"), "M15 small-set closure specialist"),
    (("consensus",), "M16 atomic consensus"),
    (("specialist_verifier",), "M17 specialist verifier"),
    (("bidirectional_verification",), "M18 bidirectional verification"),
    (("layer4_integration",), "Layer-4 evidence integration"),
    (("coverage_gap",), "M19 coverage gap"),
)

#: Modules that must stay *off* during collection: each fails closed without a
#: TRAIN artifact, and collection is what produces those artifacts.
FORBIDDEN_COLLECTION_MODULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("relation_budget_scheduler",), "M20 relation budget scheduler"),
    (("micro_planner",), "M21 expected-value micro-planner"),
    (("layer6_integration",), "Layer-6 control integration"),
)


def _block(config: Mapping[str, Any], path: tuple[str, ...]) -> Mapping[str, Any]:
    node: Any = config
    for key in path:
        node = (node or {}).get(key) if isinstance(node, Mapping) else None
    return node if isinstance(node, Mapping) else {}


def _resolve_config_path(raw: object, *, base: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else base / path


def _v3_core_block(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return _block(config, ("pipeline", "v3_core"))


def _v3_artifact_path_blocker(
    label: str, raw: object, *, base: Path,
) -> str | None:
    path = _resolve_config_path(raw, base=base)
    if path is None:
        return f"V3 calibration: {label} is not configured"
    normalized = path.as_posix()
    if "/calibration/v3/" not in normalized and "configs/calibration/v3/" not in normalized:
        return (
            f"V3 calibration: {label} points at {path}; V3 may not reuse the "
            "historical V2 calibration artifacts"
        )
    return None


def _check_v3_production_readiness(
    config: Mapping[str, Any], *, base: Path,
    calibration: Any | None, blockers: list[str], satisfied: list[str],
    details: dict[str, Any],
) -> None:
    """Additional fail-closed checks when a production config enables V3."""
    v3 = dict(_v3_core_block(config))
    if not v3.get("enabled", False):
        details["v3_calibrated_baseline"] = "V2"
        return

    details["v3_core"] = dict(v3)
    details["v2_calibrated_baseline"] = "READY"
    mode = str(v3.get("mode", ""))
    if mode != "production":
        blockers.append(
            f"V3 production: pipeline.v3_core.mode is {mode!r}, expected "
            "'production' for validation/test activation"
        )
    if not bool(v3.get("production_calibration_ready", False)):
        blockers.append(
            "V3 production calibration: NOT_READY; V3 validation/test may not "
            "run until separate TRAIN-derived V3 M20/M21 artifacts exist"
        )

    budget_block = dict(config.get("relation_budget_scheduler") or {})
    planner_block = dict(config.get("micro_planner") or {})
    path_checks = (
        ("M20 relation budget", budget_block.get("calibration_file")),
        ("M21 historical bins", planner_block.get("historical_bins")),
        ("M21 planner calibration", planner_block.get("planner_calibration")),
    )
    for label, raw in path_checks:
        blocker = _v3_artifact_path_blocker(label, raw, base=base)
        if blocker:
            blockers.append(blocker)
    if not any("V3 calibration:" in blocker for blocker in blockers):
        satisfied.append("V3 calibration artifacts: separate v3 paths configured")

    if calibration is None:
        details["v3_production_calibration"] = "NOT_READY"
        return

    provenance = dict(getattr(calibration, "provenance", {}) or {})
    expected = dict(config.get("calibration_provenance") or {})
    for provenance_field in ("merged_corpus_sha256", "train_sha256"):
        configured = str(expected.get(provenance_field, ""))
        actual = str(provenance.get(provenance_field, ""))
        if configured and actual != configured:
            blockers.append(
                f"V3 calibration provenance: {provenance_field} is "
                f"{actual!r}, but the config expects {configured!r}"
            )
    if not str(provenance.get("merged_corpus_sha256", "")):
        blockers.append(
            "V3 calibration provenance: merged_corpus_sha256 is missing")
    if not str(provenance.get("action_policy_version", "")):
        blockers.append(
            "V3 calibration provenance: action_policy_version is missing")
    required_provenance = {
        "v3_core_schema_version": str(v3.get("schema_version", "")),
        "v3_action_effect_schema_version": "v3-action-effect-v1",
    }
    for provenance_field, expected in required_provenance.items():
        actual = str(provenance.get(provenance_field, ""))
        if actual != expected:
            blockers.append(
                f"V3 calibration provenance: {provenance_field} is "
                f"{actual!r}, expected {expected!r}"
            )
    if not str(provenance.get("v3_collection_run_id", "")):
        blockers.append(
            "V3 calibration provenance: v3_collection_run_id is missing")
    if not str(provenance.get("v3_action_effects_sha256", "")):
        blockers.append(
            "V3 calibration provenance: v3_action_effects_sha256 is missing")
    if str(provenance.get("m21_derivation_method", "")) != (
            "v3_action_effect_utility_with_pooled_fallbacks_and_"
            "inert_unestimable_state_movement"):
        blockers.append(
            "V3 calibration provenance: m21_derivation_method does not declare "
            "the V3 pooled-fallback estimator")
    if str(provenance.get("m20_derivation_method", "")) not in {
        "inherited_frozen_v2_m20",
        "v3_action_corpus_reestimate",
    }:
        blockers.append(
            "V3 calibration provenance: m20_derivation_method is missing or "
            "unsupported")

    collection = dict(config.get("train_collection") or {})
    if collection.get("policy"):
        blockers.append(
            "V3 production config: train_collection policy is configured; "
            "collection-only scheduling must not be present in production")

    try:
        from cover_kbc.models.registry import model_blocks

        enumerator, verifier = model_blocks(config)
    except Exception as error:                                  # noqa: BLE001
        blockers.append(f"V3 model profile: unreadable ({error})")
        enumerator, verifier = {}, {}
    for role, block, model_id, revision in (
        ("enumerator", enumerator, FROZEN_ENUMERATOR_ID,
         FROZEN_ENUMERATOR_REVISION),
        ("verifier", verifier, FROZEN_VERIFIER_ID, FROZEN_VERIFIER_REVISION),
    ):
        actual_id = str(block.get("model_id", ""))
        actual_revision = str(block.get("revision", ""))
        if actual_id != model_id:
            blockers.append(
                f"V3 model profile: {role} model_id is {actual_id!r}, "
                f"expected {model_id!r}")
        if actual_revision != revision:
            blockers.append(
                f"V3 model profile: {role} revision is {actual_revision!r}, "
                f"expected {revision!r}")
    assertion = config.get("budget_assertion") or {}
    total = int(assertion.get("total_published_parameters", 0) or 0)
    limit = int(assertion.get("limit", 0) or 0)
    if total != FROZEN_PARAMETER_TOTAL or limit != PARAMETER_LIMIT:
        blockers.append(
            f"V3 model budget: {total} / {limit}, expected "
            f"{FROZEN_PARAMETER_TOTAL} / {PARAMETER_LIMIT}")
    else:
        satisfied.append(
            f"V3 model budget: {FROZEN_PARAMETER_TOTAL} / {PARAMETER_LIMIT}")

    from cover_kbc.contracts.relation_profile import all_relation_profiles
    from cover_kbc.contracts.registry import CONTRACTS
    from cover_kbc.v3_core.relation_programs import relation_train_collection_actions

    required_families = {
        family.value
        for profile in all_relation_profiles()
        for family in relation_train_collection_actions(profile.relation)
    }
    observed_families = {
        str(getattr(entry.action_family, "value", entry.action_family))
        for entry in calibration.history.bins
    }
    missing = sorted(required_families - observed_families)
    details["v3_required_action_families"] = sorted(required_families)
    details["v3_calibrated_action_families"] = sorted(observed_families)
    if missing:
        blockers.append(
            f"V3 calibration coverage: missing historical bins for V3 action "
            f"families {missing}"
        )
    else:
        satisfied.append("V3 calibration coverage: required action families binned")

    unresolved_regions: list[str] = []
    for relation, contract in sorted(CONTRACTS.items()):
        program_type = getattr(contract.program_type, "value", contract.program_type)
        for family in relation_train_collection_actions(relation):
            try:
                calibration.history.lookup(
                    relation=relation,
                    program_type=str(program_type),
                    state_bin_key="__readiness_unseen_state__",
                    family=family,
                )
            except Exception as error:                          # noqa: BLE001
                unresolved_regions.append(
                    f"{relation}/{program_type}/{family.value}: {error}")
    details["v3_unresolved_calibration_regions"] = unresolved_regions
    if unresolved_regions:
        blockers.append(
            "V3 fallback coverage: production-legal planner region(s) cannot "
            f"resolve to a historical bin, e.g. {unresolved_regions[:3]}"
        )
    else:
        satisfied.append(
            "V3 fallback coverage: every production-legal relation/family "
            "resolves to an exact or fallback bin")

    if not any(blocker.startswith("V3 ") for blocker in blockers):
        details["v3_production_calibration"] = "READY"
        satisfied.append("V3 production calibration: READY")
    else:
        details["v3_production_calibration"] = "NOT_READY"


def evaluate_collection_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    split: str | None = None,
) -> ReadinessReport:
    """May this profile start a TRAIN calibration collection run?

    Composed from :func:`evaluate_readiness` rather than duplicating it, plus
    the three requirements that are specific to collection and that Audit 0041
    found nothing checking:

    * the split really is TRAIN - a ``val`` profile must never load a model;
    * the whole M9-M19 stack is enabled, because a collection with M16 off
      executes no action and writes an empty telemetry file while exiting 0;
    * M20/M21/Layer-6 stay off, since collection exists to produce the very
      artifacts they refuse to run without.

    The verdict is authoritative through ``may_run_collection``, and its default
    is refusal.
    """
    from cover_kbc.integration_mode import CALIBRATION_SPLIT
    from cover_kbc.models.registry import model_blocks

    artifacts = evaluate_readiness(config, base_dir=base_dir)
    blockers: list[str] = []
    satisfied: list[str] = []
    details: dict[str, Any] = dict(artifacts.details)

    declared = split if split is not None else str(
        (config.get("experiment") or {}).get("split", CALIBRATION_SPLIT))
    details["split"] = declared
    if declared != CALIBRATION_SPLIT:
        blockers.append(
            f"split: collection may only read {CALIBRATION_SPLIT!r}, this "
            f"profile declares {declared!r}"
        )
    else:
        satisfied.append(f"split: {CALIBRATION_SPLIT}")

    for path, label in REQUIRED_COLLECTION_MODULES:
        if not _block(config, path).get("enabled", False):
            blockers.append(
                f"{label}: {'.'.join(path)}.enabled is false; collection would "
                "observe no action for it"
            )
    for path, label in FORBIDDEN_COLLECTION_MODULES:
        if _block(config, path).get("enabled", False):
            blockers.append(
                f"{label}: {'.'.join(path)}.enabled is true, but no "
                "TRAIN-calibrated artifact exists yet; collection is what "
                "produces it"
            )
    enabled = len(REQUIRED_COLLECTION_MODULES) - len(
        [b for b in blockers if "enabled is false" in b])
    satisfied.append(
        f"upgraded stack: {enabled}/{len(REQUIRED_COLLECTION_MODULES)} enabled")

    try:
        enumerator, verifier = model_blocks(config)
    except Exception as error:                              # noqa: BLE001
        enumerator, verifier = {}, {}
        blockers.append(f"model profile: unreadable ({error})")
    for role, block in (("enumerator", enumerator), ("verifier", verifier)):
        if not block.get("model_id"):
            blockers.append(f"model profile: {role} declares no model_id")
        if "backend" not in block:
            blockers.append(f"model profile: {role} declares no backend")
    details["enumerator_model_id"] = enumerator.get("model_id", "")
    details["verifier_model_id"] = verifier.get("model_id", "")

    if blockers:
        return ReadinessReport(
            ReadinessState.NOT_READY, tuple(blockers),
            tuple(satisfied) + artifacts.satisfied, details)
    # Artifact-only blockers are exactly what collection exists to remove, so
    # they never block collection - ``evaluate_readiness`` already says so.
    return ReadinessReport(
        artifacts.state if artifacts.may_run_collection
        else ReadinessState.CALIBRATION_COLLECTION_READY,
        artifacts.blockers, tuple(satisfied) + artifacts.satisfied, details)


#: Everything a production validation run needs wired. The collection list plus
#: Layer 6: a validation run without Modules 20 and 21 is the shadow system
#: wearing a production label.
REQUIRED_VALIDATION_MODULES: tuple[tuple[tuple[str, ...], str], ...] = (
    REQUIRED_COLLECTION_MODULES + (
        (("relation_budget_scheduler",), "M20 relation budget scheduler"),
        (("micro_planner",), "M21 expected-value micro-planner"),
        (("layer6_integration",), "Layer-6 control integration"),
    )
)


def _evaluate_production_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path,
    split: str | None, expected_split: str, run_kind: str,
    expected_collection_repo_sha: str | None,
    expected_derivation_repo_sha: str | None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Everything a production run needs, whichever split it reads.

    Extracted from ``evaluate_validation_readiness`` unchanged so the VAL and
    TEST gates cannot drift: the same artifacts, the same module list, the same
    production-mode requirement and the same model-profile resolution decide
    both. Only the expected split name differs here; each wrapper adds what is
    genuinely specific to its split on top.

    Returns:
        ``(blockers, satisfied, details)`` for the caller to extend and turn
        into a verdict. Never raises on a bad profile.
    """
    from cover_kbc.contracts.registry import CONTRACTS
    from cover_kbc.controller_calibration.production import (
        ProductionCalibrationError,
        load_production_calibration,
    )
    from cover_kbc.models.registry import model_blocks

    base = Path(base_dir)
    artifacts = evaluate_readiness(config, base_dir=base_dir)
    blockers: list[str] = list(artifacts.blockers)
    satisfied: list[str] = list(artifacts.satisfied)
    details: dict[str, Any] = dict(artifacts.details)

    declared = split if split is not None else str(
        (config.get("experiment") or {}).get("split", ""))
    details["split"] = declared
    if declared != expected_split:
        blockers.append(
            f"split: a {run_kind} run may only read {expected_split!r}, this "
            f"profile declares {declared!r}")
    else:
        satisfied.append(f"split: {expected_split}")

    for path, label in REQUIRED_VALIDATION_MODULES:
        if not _block(config, path).get("enabled", False):
            blockers.append(
                f"{label}: {'.'.join(path)}.enabled is false; the production "
                "path needs the whole upgraded stack")

    for path, label in ((("relation_budget_scheduler",), "M20"),
                        (("micro_planner",), "M21")):
        mode = str(_block(config, path).get("mode", ""))
        if mode != "production":
            blockers.append(
                f"{label}: {'.'.join(path)}.mode is {mode!r}, not 'production'; "
                "a shadow module cannot govern a production run")
    if not any("not 'production'" in b for b in blockers):
        satisfied.append("M20 and M21 both declare production mode")

    try:
        enumerator, verifier = model_blocks(config)
    except Exception as error:                                  # noqa: BLE001
        enumerator, verifier = {}, {}
        blockers.append(f"model profile: unreadable ({error})")
    for role, block in (("enumerator", enumerator), ("verifier", verifier)):
        if not block.get("model_id"):
            blockers.append(f"model profile: {role} declares no model_id")
    details["enumerator_model_id"] = enumerator.get("model_id", "")
    details["verifier_model_id"] = verifier.get("model_id", "")

    # The decisive step: the artifacts must actually load. A configured path
    # that does not resolve, a fixture source, a hash mismatch or three files
    # from different derivations all fail here rather than at row 1 of 478.
    calibration = None
    try:
        calibration = load_production_calibration(
            config, base_dir=base_dir,
            expected_collection_repo_sha=expected_collection_repo_sha,
            expected_derivation_repo_sha=expected_derivation_repo_sha)
    except ProductionCalibrationError as error:
        blockers.append(f"calibration: {error}")
    except Exception as error:                                  # noqa: BLE001
        blockers.append(f"calibration: unreadable ({error})")
    else:
        details["calibration"] = calibration.to_json()
        satisfied.append(
            f"calibration: {len(calibration.budgets)} relation budget(s), "
            f"{len(calibration.history.bins)} historical bin(s), "
            f"tau_continue={calibration.planner.tau_continue}")
        missing = sorted(set(CONTRACTS) - set(calibration.budgets))
        if missing:
            blockers.append(
                f"calibration: no budget for relation(s) {missing}; every "
                "relation the router can route to needs one")
        else:
            satisfied.append("calibration: all six relations budgeted")

    _check_v3_production_readiness(
        config, base=base, calibration=calibration, blockers=blockers,
        satisfied=satisfied, details=details)

    return blockers, satisfied, details


def evaluate_validation_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    split: str | None = None,
    expected_collection_repo_sha: str | None = None,
    expected_derivation_repo_sha: str | None = None,
) -> ReadinessReport:
    """May this profile start a full production VALIDATION run?

    Composed from :func:`evaluate_readiness` - which already refuses an absent,
    unreadable or non-``TRAIN_CALIBRATED`` artifact - plus what only a
    validation run needs:

    * the split is ``val``, not train and not test;
    * **both** Module 20 and Module 21 declare ``mode: production``, because a
      production run in which one of them is in shadow is a system nobody
      designed;
    * every upgraded module M9-M21 and Layer 6 is enabled;
    * the three artifacts actually load through their canonical owners, agree
      on provenance, and match the expected collection/derivation commits;
    * the frozen model profile resolves.

    A config that merely parses is not ready. This loads the artifacts.

    Returns:
        A report whose default is refusal. ``FULL_VALIDATION_READY`` only when
        every one of the above holds.
    """
    blockers, satisfied, details = _evaluate_production_readiness(
        config, base_dir=base_dir, split=split, expected_split="val",
        run_kind="validation",
        expected_collection_repo_sha=expected_collection_repo_sha,
        expected_derivation_repo_sha=expected_derivation_repo_sha)
    if blockers:
        return ReadinessReport(
            ReadinessState.NOT_READY, tuple(blockers), tuple(satisfied), details)
    return ReadinessReport(
        ReadinessState.FULL_VALIDATION_READY, (), tuple(satisfied), details)


def _check_blind_test_dataset(
    config: Mapping[str, Any], data_dir: "str | Path | None",
    blockers: list[str], satisfied: list[str], details: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """The official blind split, checked three ways plus blindness.

    Row count, byte hash and the digest of the ordered SubjectEntity/Relation
    pairs - three ways to catch a submission built against a different or
    reordered file, which is invalid however good its answers are.
    """
    import hashlib

    # -- the dataset itself ------------------------------------------------
    expected = config.get("test_dataset") or {}
    root = Path(data_dir) if data_dir is not None else None
    if root is None:
        from cover_kbc.paths import SPLIT_FILES
        path = SPLIT_FILES.get("test")
    else:
        path = root / "test.jsonl"
    details["test_dataset_path"] = str(path) if path else ""

    if path is None or not Path(path).is_file():
        blockers.append(f"test dataset: not found at {path}")
        return blockers, satisfied, details
    raw = Path(path).read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    details["test_rows"] = len(lines)
    details["test_sha256"] = actual_sha

    declared_sha = str(expected.get("sha256", ""))
    declared_rows = expected.get("rows")
    if not declared_sha or declared_rows is None:
        blockers.append(
            "test_dataset: the config must record the exact rows and sha256 of "
            "the split it will answer; an unrecorded dataset cannot be shown "
            "to be the official one")
    else:
        if actual_sha != declared_sha:
            blockers.append(
                f"test dataset: sha256 is {actual_sha}, the config expects "
                f"{declared_sha}")
        else:
            satisfied.append(f"test dataset: sha256 {actual_sha[:12]}...")
        if len(lines) != int(declared_rows):
            blockers.append(
                f"test dataset: {len(lines)} rows, the config expects "
                f"{declared_rows}")
        else:
            satisfied.append(f"test dataset: {len(lines)} rows")

    try:
        rows = [json.loads(line) for line in lines]
    except json.JSONDecodeError as error:
        blockers.append(f"test dataset: malformed JSONL ({error})")
        return blockers, satisfied, details

    identity = ordered_identity_digest(
        (str(r.get("SubjectEntity", "")), str(r.get("Relation", "")))
        for r in rows)
    details["test_identity_sha256"] = identity
    declared_identity = str(expected.get("identity_sha256", ""))
    if not declared_identity:
        blockers.append(
            "test_dataset: the config must record identity_sha256, the digest "
            "of the ordered SubjectEntity/Relation pairs the submission must "
            "reproduce exactly")
    elif identity != declared_identity:
        blockers.append(
            f"test dataset: ordered identity digest is {identity}, the config "
            f"expects {declared_identity}")
    else:
        satisfied.append(f"test dataset: ordered identity {identity[:12]}...")

    # Blind means blind. An objects field that is present but non-empty would
    # make gold reachable from the inference path, so it is a blocker even
    # though nothing here would read it.
    with_objects = [
        f"{r.get('SubjectEntity','')}/{r.get('Relation','')}"
        for r in rows if r.get("ObjectEntities")]
    details["test_rows_with_objects"] = len(with_objects)
    if with_objects:
        blockers.append(
            f"test dataset: {len(with_objects)} row(s) carry ObjectEntities, "
            f"e.g. {with_objects[:3]}; the official test split is blind and a "
            "run that could read them is not a blind run")
    else:
        satisfied.append("test dataset: blind (no row carries objects)")

    return blockers, satisfied, details


def evaluate_test_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    split: str | None = None,
    expected_collection_repo_sha: str | None = None,
    expected_derivation_repo_sha: str | None = None,
    data_dir: str | Path | None = None,
) -> ReadinessReport:
    """May this profile start the official blind TEST run?

    Everything :func:`evaluate_validation_readiness` requires - the same
    artifacts, the same modules, the same production modes, the same frozen
    profile - plus what only the blind split needs, and the reason each is here
    rather than there:

    * ``pipeline.mode`` is the one the calibration was measured under. TEST has
      no second chance to notice it ran a different system.
    * the parameter budget is legal by the profile's own declared totals, since
      a TEST submission is what the rule is actually enforced against.
    * the test dataset **exists**, and its row count, SHA256 and ordered
      ``SubjectEntity``/``Relation`` identity are exactly the ones the config
      records. A submission built against a different file is invalid however
      good its answers are.
    * the dataset is genuinely **blind** - no row carries objects - so this run
      cannot consume gold even by accident.

    Nothing here relaxes a validation check; ``split`` must be ``test``, so a
    val-ready profile is refused and a test-ready one is not mistaken for it.

    Args:
        data_dir: optional override for where ``test.jsonl`` is looked up.
            Tests use it; production leaves it unset and gets the official path.

    Returns:
        A report whose default is refusal. ``FULL_TEST_READY`` only when every
        one of the above holds.
    """
    blockers, satisfied, details = _evaluate_production_readiness(
        config, base_dir=base_dir, split=split, expected_split="test",
        run_kind="test",
        expected_collection_repo_sha=expected_collection_repo_sha,
        expected_derivation_repo_sha=expected_derivation_repo_sha)

    pipeline = config.get("pipeline") or {}
    mode = str(pipeline.get("mode", ""))
    details["pipeline_mode"] = mode
    if mode != "interleaved":
        blockers.append(
            f"pipeline.mode is {mode!r}; the calibration was measured under "
            "'interleaved' and TEST must run the system it describes")
    else:
        satisfied.append("pipeline.mode: interleaved")

    assertion = config.get("budget_assertion") or {}
    total = int(assertion.get("total_published_parameters", 0) or 0)
    limit = int(assertion.get("limit", 0) or 0)
    details["published_parameters"] = total
    details["parameter_limit"] = limit
    if not total or not limit:
        blockers.append(
            "budget_assertion: the profile declares no published parameter "
            "total or no limit; the 32B rule is enforced against TEST")
    elif total > limit:
        blockers.append(
            f"budget_assertion: {total} published parameters exceeds the "
            f"{limit} limit")
    else:
        satisfied.append(f"parameter budget: {total} <= {limit}")

    blockers, satisfied, details = _check_blind_test_dataset(
        config, data_dir, blockers, satisfied, details)
    blockers, satisfied, details = _check_v3_1_calibration_compatibility(
        config, blockers, satisfied, details)

    return _test_verdict(blockers, satisfied, details)


def _check_v3_1_calibration_compatibility(
    config: Mapping[str, Any],
    blockers: list[str],
    satisfied: list[str],
    details: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Refuse TEST for a V3.1 profile whose calibration no longer describes it.

    Two independent refusals, because they fail for different reasons:

    * a **Class B** feature is on. Prompts, recall and action semantics change
      the action-effect distribution the Audit 0073 M20/M21 calibration was
      derived from, so reusing that calibration is a false claim about the run.
      The profile is reported ``CALIBRATION_REVIEW_REQUIRED`` and never
      ``FULL_TEST_READY``.
    * an enabled feature is **not declared** in
      :mod:`cover_kbc.v3_1.compatibility`, or is declared as anything other
      than ``SAFE_WITH_EXISTING_CALIBRATION``. A feature inherits nothing from
      the block it was written in; safety is a property of where the code runs,
      and that has to be stated before it can be believed.
    """
    pipeline = config.get("pipeline") or {}
    selection = pipeline.get("selection") or {}
    raw = selection.get("v3_1") or pipeline.get("v3_1")
    if not raw:
        details["v3_1"] = "absent"
        return blockers, satisfied, details

    try:
        v3_1 = V31Config.from_mapping(raw)
    except ValueError as error:
        blockers.append(f"selection.v3_1: {error}")
        details["v3_1"] = "invalid"
        return blockers, satisfied, details

    details["v3_1_enabled"] = v3_1.enabled
    details["v3_1_safe_features"] = list(v3_1.safe.enabled_features)
    details["v3_1_aggressive_features"] = list(v3_1.aggressive.enabled_features)
    details["v3_1_calibration_status"] = v3_1.calibration_status

    if not v3_1.enabled:
        satisfied.append("selection.v3_1: present but disabled")
        return blockers, satisfied, details

    if v3_1.aggressive_active:
        blockers.append(
            "selection.v3_1: CALIBRATION_REVIEW_REQUIRED - calibration-shifting "
            f"feature(s) {list(v3_1.aggressive.enabled_features)} are enabled. "
            "These change recall, prompt or action semantics, so the Audit 0073 "
            "M20/M21 calibration no longer describes this run and must be "
            "re-derived from a TRAIN collection made with them.")

    for feature in v3_1.safe.enabled_features:
        try:
            compatibility = compatibility_of(feature)
        except KeyError as error:
            blockers.append(f"selection.v3_1.safe: {error.args[0]}")
            continue
        if compatibility != V3_1_SAFE:
            blockers.append(
                f"selection.v3_1.safe.{feature} is classified {compatibility}, "
                "not SAFE_WITH_EXISTING_CALIBRATION; it may not run under the "
                "existing calibration")
        else:
            satisfied.append(f"selection.v3_1.safe.{feature}: {compatibility}")

    return blockers, satisfied, details



def evaluate_train_diagnostic_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    split: str | None = None,
    expected_collection_repo_sha: str | None = None,
    expected_derivation_repo_sha: str | None = None,
    data_dir: str | Path | None = None,
) -> ReadinessReport:
    """May this profile run the calibrated system over labelled TRAIN?

    Milestone V3A asks a question no leaderboard run answers: *where* does each
    relation lose its gold objects? Answering it means running the production
    decision path - the same models, the same prompts, the same Modules 20 and
    21 - against a split whose answers are known. So this gate requires
    everything :func:`evaluate_validation_readiness` requires, and then three
    things of its own:

    * the split is ``train``. Not val, not test.
    * ``diagnostics.enabled`` is true and an output path is declared. A
      "diagnostic" run that records nothing burns a GPU hour and produces no
      diagnosis - the same class of silent success Audit 0041 F-01 caught for
      collection.
    * the TRAIN file's row count, SHA256 and ordered identity are exactly the
      ones the config records. The benchmark has already been resnapshotted
      once during this project; a failure report attributed against a different
      TRAIN than the run read is worse than no report.

    Two things this gate deliberately does **not** relax, because a diagnostic
    run is still a real run of the frozen system: Modules 20 and 21 must both
    declare ``production``, and the three calibration artifacts must load.
    Diagnosing a system nobody runs would tell us nothing about the one we do.

    Note for the record, not a blocker: the shipped M20/M21 artifacts were
    derived from TRAIN, so a TRAIN diagnostic run is measured on the
    calibration's own source data. That is sound for "where is the gold lost",
    which is what V3A asks, and unsound for "how well does it generalise",
    which V3A does not ask and must not be read as answering.

    Returns:
        A report whose default is refusal. ``TRAIN_DIAGNOSTIC_READY`` only when
        every one of the above holds.
    """
    blockers, satisfied, details = _evaluate_production_readiness(
        config, base_dir=base_dir, split=split, expected_split="train",
        run_kind="train diagnostic",
        expected_collection_repo_sha=expected_collection_repo_sha,
        expected_derivation_repo_sha=expected_derivation_repo_sha)

    diagnostics = dict(config.get("diagnostics") or {})
    details["diagnostics_enabled"] = bool(diagnostics.get("enabled", False))
    if not diagnostics.get("enabled", False):
        blockers.append(
            "diagnostics: diagnostics.enabled is false; a TRAIN diagnostic run "
            "that records no failure telemetry produces nothing to analyse")
    elif not str(diagnostics.get("telemetry_file", "")):
        blockers.append(
            "diagnostics: diagnostics.telemetry_file is not declared; the "
            "telemetry has to be written somewhere the analysis can read it")
    else:
        satisfied.append(
            f"diagnostics: enabled, writing "
            f"{diagnostics.get('telemetry_file')!r}")

    blockers, satisfied, details = _check_train_diagnostic_baseline(
        config, blockers, satisfied, details)
    blockers, satisfied, details = _check_labelled_split(
        config, "train", "train_dataset", data_dir,
        blockers, satisfied, details)

    if blockers:
        return ReadinessReport(
            ReadinessState.NOT_READY, tuple(blockers), tuple(satisfied), details)
    return ReadinessReport(
        ReadinessState.TRAIN_DIAGNOSTIC_READY, (), tuple(satisfied), details)


def _check_train_diagnostic_baseline(
    config: Mapping[str, Any],
    blockers: list[str], satisfied: list[str], details: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """V3A diagnoses the frozen calibrated baseline, not a new architecture."""
    from cover_kbc.models.registry import model_blocks

    try:
        enumerator, verifier = model_blocks(config)
    except Exception as error:                                  # noqa: BLE001
        blockers.append(f"model profile: unreadable ({error})")
        return blockers, satisfied, details

    expected = (
        ("enumerator", enumerator, FROZEN_ENUMERATOR_ID,
         FROZEN_ENUMERATOR_REVISION),
        ("verifier", verifier, FROZEN_VERIFIER_ID, FROZEN_VERIFIER_REVISION),
    )
    for role, block, model_id, revision in expected:
        actual_id = str(block.get("model_id", ""))
        actual_revision = str(block.get("revision", ""))
        details[f"{role}_model_id"] = actual_id
        details[f"{role}_revision"] = actual_revision
        if actual_id != model_id:
            blockers.append(
                f"model profile: {role} model_id is {actual_id!r}, expected "
                f"{model_id!r}; V3A diagnostics must measure the frozen "
                "two-model baseline")
        if actual_revision != revision:
            blockers.append(
                f"model profile: {role} revision is {actual_revision!r}, "
                f"expected {revision!r}")

    assertion = config.get("budget_assertion") or {}
    total = int(assertion.get("total_published_parameters", 0) or 0)
    limit = int(assertion.get("limit", 0) or 0)
    details["published_parameters"] = total
    details["parameter_limit"] = limit
    if total != FROZEN_PARAMETER_TOTAL:
        blockers.append(
            f"budget_assertion: total_published_parameters is {total}, "
            f"expected {FROZEN_PARAMETER_TOTAL}")
    if limit != PARAMETER_LIMIT:
        blockers.append(
            f"budget_assertion: limit is {limit}, expected {PARAMETER_LIMIT}")
    if total == FROZEN_PARAMETER_TOTAL and limit == PARAMETER_LIMIT:
        satisfied.append(
            f"frozen model budget: {FROZEN_PARAMETER_TOTAL} / {PARAMETER_LIMIT}")

    v3b = config.get("v3b_features") or {}
    enabled = [
        name for name, value in sorted(dict(v3b).items())
        if bool(value)
    ] if isinstance(v3b, Mapping) else ["<invalid v3b_features block>"]
    if enabled:
        blockers.append(
            f"V3B: v3b_features enables {enabled}; V3A diagnostics must not "
            "start specialized recall or failure-aware control")
    else:
        satisfied.append("V3B: no v3b_features enabled")

    return blockers, satisfied, details


def evaluate_v3_core_readiness(
    config: Mapping[str, Any], *, base_dir: str | Path = ".",
    split: str | None = None,
) -> ReadinessReport:
    """May this profile exercise V3 core mechanics for future TRAIN collection?

    This is not a production-readiness gate. It explicitly reports the V2
    calibrated baseline as ready, the V3 source/action catalogue as implemented,
    and V3 production calibration / official TEST as not ready.
    """
    from cover_kbc.contracts.relation_profile import all_relation_profiles
    from cover_kbc.integration_mode import CALIBRATION_SPLIT
    from cover_kbc.models.registry import model_blocks
    from cover_kbc.v3_core.relation_programs import (
        V3ActionFamily,
        relation_train_collection_actions,
    )

    blockers: list[str] = []
    satisfied: list[str] = []
    details: dict[str, Any] = {
        "v2_calibrated_baseline": "READY",
        "v3_core_source": "IMPLEMENTED",
        "v3_production_calibration": "NOT_READY",
        "v3_official_test": "NOT_READY",
    }

    declared = split if split is not None else str(
        (config.get("experiment") or {}).get("split", ""))
    details["split"] = declared
    if declared != CALIBRATION_SPLIT:
        blockers.append(
            f"split: V3 TRAIN collection may only read {CALIBRATION_SPLIT!r}, "
            f"this profile declares {declared!r}")
    else:
        satisfied.append(f"split: {CALIBRATION_SPLIT}")

    pipeline = dict(config.get("pipeline") or {})
    v3 = dict(pipeline.get("v3_core") or {})
    details["v3_core"] = dict(v3)
    if not v3.get("enabled", False):
        blockers.append("pipeline.v3_core.enabled is false")
    else:
        satisfied.append("pipeline.v3_core.enabled: true")
    mode = str(v3.get("mode", ""))
    if mode != "train_collection":
        blockers.append(
            f"pipeline.v3_core.mode is {mode!r}, expected 'train_collection'")
    else:
        satisfied.append("pipeline.v3_core.mode: train_collection")
    if v3.get("production_calibration_ready", False):
        blockers.append(
            "pipeline.v3_core.production_calibration_ready is true, but V3 "
            "production calibration is explicitly NOT_READY before V3D")
    else:
        satisfied.append("V3 production calibration: NOT_READY as required")

    collection = evaluate_collection_readiness(
        config, base_dir=base_dir, split=declared)
    details["v2_collection_gate"] = collection.to_json()
    if collection.may_run_collection:
        satisfied.append("M9-M19 collection seams: ready")
    else:
        blockers.extend(collection.blockers)
    satisfied.extend(collection.satisfied)

    try:
        enumerator, verifier = model_blocks(config)
    except Exception as error:                                  # noqa: BLE001
        blockers.append(f"model profile: unreadable ({error})")
        enumerator, verifier = {}, {}
    expected = (
        ("enumerator", enumerator, FROZEN_ENUMERATOR_ID,
         FROZEN_ENUMERATOR_REVISION),
        ("verifier", verifier, FROZEN_VERIFIER_ID, FROZEN_VERIFIER_REVISION),
    )
    for role, block, model_id, revision in expected:
        actual_id = str(block.get("model_id", ""))
        actual_revision = str(block.get("revision", ""))
        details[f"{role}_model_id"] = actual_id
        details[f"{role}_revision"] = actual_revision
        if actual_id != model_id:
            blockers.append(
                f"model profile: {role} model_id is {actual_id!r}, expected "
                f"{model_id!r}")
        if actual_revision != revision:
            blockers.append(
                f"model profile: {role} revision is {actual_revision!r}, "
                f"expected {revision!r}")
    assertion = config.get("budget_assertion") or {}
    total = int(assertion.get("total_published_parameters", 0) or 0)
    limit = int(assertion.get("limit", 0) or 0)
    details["published_parameters"] = total
    details["parameter_limit"] = limit
    if total != FROZEN_PARAMETER_TOTAL or limit != PARAMETER_LIMIT:
        blockers.append(
            f"model budget: {total} / {limit}, expected "
            f"{FROZEN_PARAMETER_TOTAL} / {PARAMETER_LIMIT}")
    else:
        satisfied.append(
            f"model budget: {FROZEN_PARAMETER_TOTAL} / {PARAMETER_LIMIT}")

    diagnostics = dict(config.get("diagnostics") or {})
    details["diagnostics_enabled"] = bool(diagnostics.get("enabled", False))
    if not diagnostics.get("enabled", False):
        blockers.append(
            "diagnostics: V3 TRAIN collection must persist gold-free inference "
            "telemetry for offline V3A attribution")
    elif not str(diagnostics.get("telemetry_file", "")):
        blockers.append(
            "diagnostics: diagnostics.telemetry_file is not declared")
    else:
        satisfied.append(
            f"diagnostics: enabled, writing "
            f"{diagnostics.get('telemetry_file')!r}")

    blockers, satisfied, details = _check_labelled_split(
        config, "train", "train_dataset", None,
        blockers, satisfied, details)

    required = {
        V3ActionFamily.MULTI_VIEW_RECALL,
        V3ActionFamily.INDEPENDENT_RECALL,
        V3ActionFamily.DEFINITION_RECALL,
        V3ActionFamily.ALTERNATIVE_RECALL,
        V3ActionFamily.ATTRIBUTE_DECOMPOSITION,
        V3ActionFamily.SET_EXPANSION,
        V3ActionFamily.LISTING_ELIMINATION,
        V3ActionFamily.UNARY_VERIFY,
        V3ActionFamily.SEMANTIC_VERIFY,
        V3ActionFamily.CONTRAST_VERIFY,
    }
    by_relation = {
        p.relation: relation_train_collection_actions(p.relation)
        for p in all_relation_profiles()
    }
    available = {action for actions in by_relation.values() for action in actions}
    details["v3_train_collection_actions_by_relation"] = {
        relation: [a.value for a in actions]
        for relation, actions in by_relation.items()
    }
    missing = sorted(a.value for a in required - available)
    if missing:
        blockers.append(f"V3 action catalogue: missing required actions {missing}")
    else:
        satisfied.append("V3 action catalogue: required families observable")

    illegal_checks = (
        ("countryLandBordersCountry", V3ActionFamily.SET_EXPANSION),
        ("companyTradesAtStockExchange", V3ActionFamily.DEFINITION_RECALL),
        ("hasArea", V3ActionFamily.LISTING_ELIMINATION),
        ("hasCapacity", V3ActionFamily.SET_EXPANSION),
    )
    for relation, action in illegal_checks:
        if action in by_relation.get(relation, ()):
            blockers.append(
                f"V3 action catalogue: illegal {action.value} appears for "
                f"{relation}")
    if not any("illegal" in blocker for blocker in blockers):
        satisfied.append("V3 action catalogue: impossible relation/actions excluded")

    if blockers:
        details["v3_train_collection"] = "HOLD"
        return ReadinessReport(
            ReadinessState.NOT_READY, tuple(blockers), tuple(satisfied), details)
    details["v3_train_collection"] = "READY"
    return ReadinessReport(
        ReadinessState.V3_TRAIN_COLLECTION_READY, (), tuple(satisfied), details)


def _check_labelled_split(
    config: Mapping[str, Any], split: str, block: str,
    data_dir: "str | Path | None",
    blockers: list[str], satisfied: list[str], details: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """A labelled split, pinned three ways and required to *carry* its labels.

    The mirror image of :func:`_check_blind_test_dataset`. Row count, byte hash
    and ordered identity are checked identically - the reason is the same, that
    a run against a different or reordered file is not the run it claims to be.
    The blindness check is inverted: TEST is refused if any row carries objects,
    TRAIN is refused if none does, because a diagnostic joined against a gold
    file with no gold in it would report every relation as a total failure.
    """
    import hashlib

    expected = config.get(block) or {}
    if data_dir is not None:
        path = Path(data_dir) / f"{split}.jsonl"
    else:
        from cover_kbc.paths import SPLIT_FILES
        path = SPLIT_FILES.get(split)
    details[f"{split}_dataset_path"] = str(path) if path else ""

    if path is None or not Path(path).is_file():
        blockers.append(f"{split} dataset: not found at {path}")
        return blockers, satisfied, details

    raw = Path(path).read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    details[f"{split}_rows"] = len(lines)
    details[f"{split}_sha256"] = actual_sha

    declared_sha = str(expected.get("sha256", ""))
    declared_rows = expected.get("rows")
    if not declared_sha or declared_rows is None:
        blockers.append(
            f"{block}: the config must record the exact rows and sha256 of the "
            f"{split} split it will read; an unrecorded dataset cannot be shown "
            "to be the one the report describes")
    else:
        if actual_sha != declared_sha:
            blockers.append(
                f"{split} dataset: sha256 is {actual_sha}, the config expects "
                f"{declared_sha}")
        else:
            satisfied.append(f"{split} dataset: sha256 {actual_sha[:12]}...")
        if len(lines) != int(declared_rows):
            blockers.append(
                f"{split} dataset: {len(lines)} rows, the config expects "
                f"{declared_rows}")
        else:
            satisfied.append(f"{split} dataset: {len(lines)} rows")

    try:
        rows = [json.loads(line) for line in lines]
    except json.JSONDecodeError as error:
        blockers.append(f"{split} dataset: malformed JSONL ({error})")
        return blockers, satisfied, details

    identity = ordered_identity_digest(
        (str(r.get("SubjectEntity", "")), str(r.get("Relation", "")))
        for r in rows)
    details[f"{split}_identity_sha256"] = identity
    declared_identity = str(expected.get("identity_sha256", ""))
    if not declared_identity:
        blockers.append(
            f"{block}: the config must record identity_sha256, the digest of "
            "the ordered SubjectEntity/Relation pairs the run will read")
    elif identity != declared_identity:
        blockers.append(
            f"{split} dataset: ordered identity digest is {identity}, the "
            f"config expects {declared_identity}")
    else:
        satisfied.append(f"{split} dataset: ordered identity {identity[:12]}...")

    with_objects = sum(1 for r in rows if r.get("ObjectEntities"))
    details[f"{split}_rows_with_objects"] = with_objects
    if not with_objects:
        blockers.append(
            f"{split} dataset: no row carries ObjectEntities; that is a blind "
            "split, and a diagnostic attributed against it would report every "
            "gold object as lost")
    else:
        satisfied.append(
            f"{split} dataset: labelled ({with_objects} rows carry objects)")

    return blockers, satisfied, details


def _test_verdict(blockers: list[str], satisfied: list[str],
                  details: dict[str, Any]) -> ReadinessReport:
    if blockers:
        return ReadinessReport(
            ReadinessState.NOT_READY, tuple(blockers), tuple(satisfied), details)
    return ReadinessReport(
        ReadinessState.FULL_TEST_READY, (), tuple(satisfied), details)


def ordered_identity_digest(pairs: "Any") -> str:
    """SHA256 over the ordered ``SubjectEntity\\tRelation`` lines.

    One number that pins row count, per-row identity **and** order at once, so
    a submission that answers the right questions in the wrong sequence is as
    detectable as one that answers the wrong questions. Shared by the readiness
    gate and the packager so they cannot disagree about what identity means.
    """
    import hashlib

    joined = "\n".join(f"{subject}\t{relation}" for subject, relation in pairs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = [
    "FORBIDDEN_COLLECTION_MODULES",
    "REQUIRED_VALIDATION_MODULES",
    "evaluate_test_readiness",
    "evaluate_train_diagnostic_readiness",
    "evaluate_v3_core_readiness",
    "evaluate_validation_readiness",
    "ordered_identity_digest",
    "REQUIRED_COLLECTION_MODULES",
    "ReadinessReport",
    "ReadinessState",
    "evaluate_collection_readiness",
    "evaluate_readiness",
]
