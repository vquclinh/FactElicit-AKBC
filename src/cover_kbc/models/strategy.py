"""Which models answer a run, and under which logical roles.

One architecture, two model strategies. M0-M21 do not know which is active:
the strategy chooses *which checkpoint serves a logical role*, and nothing
else. There is no second pipeline, no second controller and no Module 22.

``baseline`` is the frozen Mistral+Qwen system that produced the official TEST
result. It is the default everywhere, and it is deliberately the strategy you
get when a config says nothing at all - a run that omits the flag must be the
run that was already validated.

``portfolio`` is a heterogeneous role-specialised set. It exists so the
hypothesis "different checkpoints are better at different *roles*" can be
measured on TRAIN. It is **not** production calibrated: changing the models
changes expected action gain, cost, false-positive risk and the transition
history, so Module 20 and Module 21's TRAIN-derived packages describe a system
the portfolio is not. That is enforced, not documented - see
:func:`calibration_strategy`.

The three governance facts this module refuses to guess at: which checkpoint,
which immutable revision, and how many parameters it really instantiates. The
32B rule is checked against recorded, *verified* counts, and quantisation never
reduces them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from cover_kbc.types import ModelRole


class ModelStrategyError(RuntimeError):
    """A strategy could not be resolved, or disagrees with its config."""


class ModelStrategy(str, Enum):
    """Which portfolio of checkpoints serves this run."""

    #: Mistral-Small-3.2-24B + Qwen3.5-4B. Frozen, calibrated, reproducible.
    BASELINE = "baseline"
    #: Gemma + Nemotron + Qwen, role-specialised. TRAIN bake-off only.
    PORTFOLIO = "portfolio"


class StrategyStatus(str, Enum):
    """How far a strategy has been taken, stated rather than implied."""

    #: Real TRAIN-derived M20/M21 exist for these exact models.
    PRODUCTION_CALIBRATED = "PRODUCTION_CALIBRATED"
    #: May be measured on TRAIN. May not answer VAL or TEST.
    TRAIN_BAKEOFF_ONLY = "TRAIN_BAKEOFF_ONLY"


#: The status each strategy currently holds. Portfolio moves to
#: ``PRODUCTION_CALIBRATED`` only when a portfolio TRAIN collection and a
#: portfolio M20/M21 derivation have both actually happened.
STRATEGY_STATUS: dict[ModelStrategy, StrategyStatus] = {
    ModelStrategy.BASELINE: StrategyStatus.PRODUCTION_CALIBRATED,
    ModelStrategy.PORTFOLIO: StrategyStatus.TRAIN_BAKEOFF_ONLY,
}

#: The strategy an artifact belongs to when its provenance names none. The
#: three shipped packages were derived under Mistral+Qwen and predate this
#: field; reading them as baseline is the truth, and it keeps their bytes -
#: and their hashes - untouched.
DEFAULT_CALIBRATION_STRATEGY = ModelStrategy.BASELINE

#: Which config key declares each logical role's checkpoint, per strategy.
#: ``baseline`` keeps the existing two-key ``model_profile`` shape exactly.
BASELINE_ROLES: tuple[ModelRole, ...] = (ModelRole.ENUMERATOR, ModelRole.VERIFIER)
PORTFOLIO_ROLES: tuple[ModelRole, ...] = (
    ModelRole.FACTUAL_ENUMERATOR,
    ModelRole.STRUCTURAL_REASONER,
    ModelRole.INDEPENDENT_VERIFIER,
)
STRATEGY_ROLES: dict[ModelStrategy, tuple[ModelRole, ...]] = {
    ModelStrategy.BASELINE: BASELINE_ROLES,
    ModelStrategy.PORTFOLIO: PORTFOLIO_ROLES,
}


# --------------------------------------------------------------------------
# role routing
# --------------------------------------------------------------------------

#: Which logical role each kind of work asks for under ``portfolio``.
#:
#: A **TRAIN hypothesis**, not a measurement: it says which checkpoint gets to
#: try, never which answer wins. Module ownership is unchanged - Module 15
#: still owns stock discovery and facets, Module 13 still owns the large open
#: set, Module 18 still owns parent/subsidiary structural verification, and
#: Module 8 is still the only thing that emits a prediction.
#:
#: Keyed by the work, not by the module, because the same module does
#: different work for different relations.
PORTFOLIO_OPERATION_ROLES: dict[str, ModelRole] = {
    # Layer 1-3: getting candidates out of parametric memory.
    "acquisition": ModelRole.FACTUAL_ENUMERATOR,
    "parametric_retrieval": ModelRole.FACTUAL_ENUMERATOR,
    "specialist_probe": ModelRole.FACTUAL_ENUMERATOR,
    "candidate_free_recall": ModelRole.FACTUAL_ENUMERATOR,
    # Layer 4: §14's structural mechanisms are reasoning over a relation's
    # shape - reverse direction, key conditions, near-miss contrast - rather
    # than recall of a name.
    "reverse_check": ModelRole.STRUCTURAL_REASONER,
    "key_condition": ModelRole.STRUCTURAL_REASONER,
    "counterfactual": ModelRole.STRUCTURAL_REASONER,
    # Layer 4: blind verification stays with one compact calibrated judge, and
    # stays independent of whoever produced the candidate.
    "specialist_verify": ModelRole.INDEPENDENT_VERIFIER,
    "blind_verify": ModelRole.INDEPENDENT_VERIFIER,
}

#: The per-relation reading of the same hypothesis, for the bake-off tables and
#: the paper. Derived from the operation map above plus §5's routing table;
#: relations differ in *which operations dominate*, not in the mapping.
PORTFOLIO_RELATION_ROLES: dict[str, dict[str, ModelRole]] = {
    "hasArea": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
    "hasCapacity": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
    "personHasCityOfDeath": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
    "awardWonBy": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
    "countryLandBordersCountry": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "structural": ModelRole.STRUCTURAL_REASONER,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
    "companyTradesAtStockExchange": {
        "factual_recall": ModelRole.FACTUAL_ENUMERATOR,
        "structural": ModelRole.STRUCTURAL_REASONER,
        "verification": ModelRole.INDEPENDENT_VERIFIER,
    },
}


def role_for_operation(strategy: ModelStrategy, operation: str) -> ModelRole:
    """Which logical role performs ``operation`` under ``strategy``.

    Under ``baseline`` this is the two-role split the system has always used:
    verification goes to the verifier, everything else to the enumerator. That
    is the existing semantics restated, not a new decision - and it is why
    baseline mode never reaches the heterogeneous table below.

    Raises:
        ModelStrategyError: for an operation the portfolio has no declared role
            for. Guessing would route real inference at an unaudited model.
    """
    key = str(operation or "").strip().lower()
    if strategy is ModelStrategy.BASELINE:
        return (ModelRole.VERIFIER if key in {"specialist_verify", "blind_verify"}
                else ModelRole.ENUMERATOR)
    try:
        return PORTFOLIO_OPERATION_ROLES[key]
    except KeyError:
        raise ModelStrategyError(
            f"the portfolio declares no role for operation {operation!r}; "
            f"known: {sorted(PORTFOLIO_OPERATION_ROLES)}"
        ) from None


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyProfile:
    """One resolved strategy: its roles, its checkpoints, its status."""

    strategy: ModelStrategy
    status: StrategyStatus
    #: Logical role -> the config block that names its checkpoint.
    blocks: Mapping[ModelRole, Mapping[str, Any]]

    @property
    def may_run_production(self) -> bool:
        """Only a production-calibrated strategy may answer VAL or TEST."""
        return self.status is StrategyStatus.PRODUCTION_CALIBRATED

    def block(self, role: ModelRole) -> Mapping[str, Any]:
        try:
            return self.blocks[role]
        except KeyError:
            raise ModelStrategyError(
                f"{self.strategy.value} declares no model for role "
                f"{role.value}; declared: "
                f"{sorted(r.value for r in self.blocks)}") from None

    def to_json(self) -> dict[str, Any]:
        """What the manifest records, so the strategy is never guessed later."""
        return {
            "model_strategy": self.strategy.value,
            "status": self.status.value,
            "may_run_production": self.may_run_production,
            "roles": {
                role.value: {
                    "model_id": str(block.get("model_id", "")),
                    "revision": str(block.get("revision", "")),
                    "published_total_parameters": block.get(
                        "published_total_parameters"),
                    "parameter_source_verified": bool(
                        block.get("parameter_source_verified", False)),
                    "quantization": block.get("quantization"),
                }
                for role, block in self.blocks.items()
            },
        }


def declared_strategy(config: Mapping[str, Any]) -> ModelStrategy:
    """The strategy a config declares, defaulting to ``baseline``.

    A config with no ``model_strategy`` key is a baseline config: every
    existing committed config is one, and they must keep meaning exactly what
    they meant before this field existed.

    Raises:
        ModelStrategyError: on a value that is not a known strategy.
    """
    raw = config.get("model_strategy")
    if raw is None:
        return ModelStrategy.BASELINE
    return parse_strategy(str(raw))


def parse_strategy(name: str) -> ModelStrategy:
    """A strategy name, or a refusal naming the ones that exist."""
    try:
        return ModelStrategy(str(name).strip().lower())
    except ValueError:
        raise ModelStrategyError(
            f"{name!r} is not a model strategy; this build implements "
            f"{sorted(s.value for s in ModelStrategy)}") from None


def resolve_strategy(
    config: Mapping[str, Any], requested: str | ModelStrategy | None = None,
) -> StrategyProfile:
    """Resolve the strategy for this run, or refuse.

    The config and the flag must agree. There is no fallback in either
    direction: a portfolio flag over a baseline config would silently answer
    with checkpoints the config never declared, and a baseline flag over a
    portfolio config would silently answer with the ones it did not ask for.
    Both are refused by name.

    Args:
        config: the loaded experiment mapping.
        requested: the ``--model-strategy`` value, or ``None`` for the default.

    Raises:
        ModelStrategyError: on an unknown strategy, a disagreement between the
            flag and the config, or a strategy whose required roles the config
            does not fully declare.
    """
    wanted = (ModelStrategy.BASELINE if requested is None
              else requested if isinstance(requested, ModelStrategy)
              else parse_strategy(requested))
    declared = declared_strategy(config)
    if declared is not wanted:
        raise ModelStrategyError(
            f"--model-strategy {wanted.value} was requested but this config "
            f"declares {declared.value}; a config and a strategy that disagree "
            "would run models the experiment never declared")

    blocks = _role_blocks(config, wanted)
    return StrategyProfile(
        strategy=wanted, status=STRATEGY_STATUS[wanted], blocks=blocks)


def _role_blocks(
    config: Mapping[str, Any], strategy: ModelStrategy,
) -> dict[ModelRole, Mapping[str, Any]]:
    """The checkpoint block for every role this strategy needs. Fails closed."""
    if strategy is ModelStrategy.BASELINE:
        from cover_kbc.models.registry import model_blocks

        enumerator, verifier = model_blocks(config)
        blocks = {ModelRole.ENUMERATOR: enumerator, ModelRole.VERIFIER: verifier}
    else:
        declared = config.get("model_portfolio")
        if not isinstance(declared, Mapping) or not declared:
            raise ModelStrategyError(
                "the portfolio strategy needs a 'model_portfolio' block "
                "naming a checkpoint for each logical role; this config "
                "declares none")
        blocks = {}
        for role in PORTFOLIO_ROLES:
            block = declared.get(role.value)
            if not isinstance(block, Mapping) or not block.get("model_id"):
                raise ModelStrategyError(
                    f"model_portfolio.{role.value} declares no model_id; the "
                    "portfolio cannot route this role")
            blocks[role] = dict(block)

    for role, block in blocks.items():
        if not block.get("model_id"):
            raise ModelStrategyError(
                f"{strategy.value}: role {role.value} declares no model_id")
    return blocks


#: Wiring the portfolio still needs before it can execute a single real call.
#: Empty as of Audit 0062: ``CoverPipeline`` now takes a third optional
#: ``structural_runtime``, so all three logical roles have a physical runtime.
#: Kept as a named, tested list so a future gap is declared here rather than
#: discovered at row 1 of 477.
PORTFOLIO_IMPLEMENTATION_BLOCKERS: tuple[str, ...] = ()

#: The exact portfolio this repository is prepared for, resolved from each
#: checkpoint's own Hugging Face metadata rather than from its name.
#:
#: ``parameters`` is the **full instantiated** ``safetensors.total``. Gemma 3
#: and Qwen3.5 are multimodal checkpoints whose official classes -
#: ``Gemma3ForConditionalGeneration`` and ``Qwen3_5ForConditionalGeneration`` -
#: instantiate the vision tower whether or not a prompt contains an image, so
#: the tower is counted. Counting only the language stack would be claiming a
#: text-only class this system does not load.
PORTFOLIO_V2: dict[ModelRole, dict[str, Any]] = {
    ModelRole.FACTUAL_ENUMERATOR: {
        "model_id": "google/gemma-3-12b-it",
        "revision": "96b6f1eccf38110c56df3a15bffe176da04bfd80",
        "parameters": 12_187_325_040,
        "gated": True,
    },
    ModelRole.STRUCTURAL_REASONER: {
        "model_id": "nvidia/NVIDIA-Nemotron-Nano-9B-v2",
        "revision": "6533e8de2c68e4536bf7c411d7a3ce5734111476",
        "parameters": 8_888_227_328,
        "gated": False,
    },
    ModelRole.INDEPENDENT_VERIFIER: {
        "model_id": "Qwen/Qwen3.5-9B",
        "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
        "parameters": 9_653_104_368,
        "gated": False,
    },
}

#: 12,187,325,040 + 8,888,227,328 + 9,653,104,368.
PORTFOLIO_V2_PARAMETERS = sum(
    m["parameters"] for m in PORTFOLIO_V2.values())

#: The baseline verifier. The portfolio uses Qwen3.5-**9B**; naming the 4B here
#: lets a test assert the portfolio never quietly falls back to it.
BASELINE_VERIFIER_MODEL_ID = "Qwen/Qwen3.5-4B"


def portfolio_fingerprint(profile: "StrategyProfile") -> str:
    """Which exact portfolio a calibration belongs to.

    A calibration measures *these checkpoints at these revisions*. Bumping any
    revision - the verifier included - produces a different system whose
    expected gain, cost and transitions were never observed, so the fingerprint
    covers the revision and not only the model id.
    """
    import hashlib

    parts = []
    for role in sorted(profile.blocks, key=lambda r: r.value):
        block = profile.blocks[role]
        parts.append(f"{role.value}\t{block.get('model_id','')}\t"
                     f"{block.get('revision','')}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def portfolio_governance_blockers(profile: StrategyProfile) -> list[str]:
    """Everything that must be true before the portfolio may make a real call.

    Governance first, because the 32B rule is enforced against exact
    instantiated counts and this repository refuses to infer one from a model
    name. A missing revision is the same class of problem: "google/gemma-3-12b-it"
    without a commit sha is not a reproducible experiment.

    Returns:
        Every blocker, not the first - a run that is three facts away from
        legal should learn all three at once.
    """
    if profile.strategy is not ModelStrategy.PORTFOLIO:
        return []

    blockers: list[str] = []
    total = 0
    complete = True
    for role in PORTFOLIO_ROLES:
        block = profile.blocks.get(role, {})
        name = str(block.get("model_id", "")) or role.value
        if not str(block.get("revision", "")).strip():
            blockers.append(
                f"{name}: no immutable revision is pinned; a moving tag is not "
                "a reproducible experiment")
        count = (block.get("budget_count_parameters")
                 or block.get("published_total_parameters"))
        if count is None:
            complete = False
            blockers.append(
                f"{name}: exact instantiated parameter count is unrecorded. "
                "Read it from the checkpoint - never from the model name - and "
                "record it with its source")
        elif not block.get("parameter_source_verified", False):
            complete = False
            blockers.append(
                f"{name}: parameter count {int(count):,} is not marked "
                "verified; record a primary source and set "
                "parameter_source_verified")
        else:
            total += int(count)
        if block.get("gated") and not str(block.get("access_note", "")).strip():
            blockers.append(
                f"{name}: gated repository with no recorded access route")

    if complete:
        from cover_kbc.models.budget import PARAMETER_BUDGET

        if total > PARAMETER_BUDGET:
            blockers.append(
                f"portfolio total {total:,} exceeds the {PARAMETER_BUDGET:,} "
                "inference-time budget; quantization does not reduce it")
    else:
        blockers.append(
            "portfolio legality cannot be proven while any parameter count is "
            "unrecorded or unverified")

    blockers.extend(PORTFOLIO_IMPLEMENTATION_BLOCKERS)
    return blockers


def require_portfolio_governance(profile: StrategyProfile) -> None:
    """Refuse a portfolio run that is not fully governed. **Fails closed.**

    Raises:
        ModelStrategyError: listing every unmet requirement. Never substitutes
            another checkpoint for one that is unavailable - a portfolio run
            that quietly used Mistral would be a baseline run wearing a
            portfolio label, and its measurements would be attributed to the
            wrong system.
    """
    blockers = portfolio_governance_blockers(profile)
    if blockers:
        listed = "\n  - ".join(blockers)
        raise ModelStrategyError(
            f"the portfolio strategy is not ready to make a real call:\n  - "
            f"{listed}")


# --------------------------------------------------------------------------
# calibration ownership
# --------------------------------------------------------------------------


def calibration_strategy(provenance: Mapping[str, Any]) -> ModelStrategy:
    """Which strategy a calibration artifact was derived under.

    Absent means ``baseline``: the three shipped packages were derived from a
    Mistral+Qwen collection and were written before this field existed, so
    reading them as baseline is both true and byte-preserving.

    Raises:
        ModelStrategyError: on a recorded value that is not a strategy.
    """
    raw = (provenance or {}).get("model_strategy")
    if raw is None:
        return DEFAULT_CALIBRATION_STRATEGY
    return parse_strategy(str(raw))


def check_calibration_portfolio(
    provenance: Mapping[str, Any], fingerprint: str,
) -> None:
    """Refuse a calibration measured on a different portfolio revision.

    ``model_strategy: portfolio`` is not specific enough. A calibration
    measures *these checkpoints at these revisions*; bumping Gemma, Nemotron or
    the verifier produces a system whose expected gain, cost and transitions
    were never observed. Baseline artifacts record no fingerprint and are not
    checked here - :func:`check_calibration_strategy` already refuses them for
    a portfolio run.

    Raises:
        ModelStrategyError: when the artifact names a different portfolio.
    """
    recorded = str((provenance or {}).get("portfolio_fingerprint", ""))
    if recorded and fingerprint and recorded != fingerprint:
        raise ModelStrategyError(
            f"this calibration was measured on portfolio {recorded[:16]}... "
            f"but the run declares {fingerprint[:16]}...; a changed model or a "
            "changed revision is a changed system, and its expected gain, cost "
            "and transitions were never observed")


def check_calibration_strategy(
    provenance: Mapping[str, Any], strategy: ModelStrategy,
) -> None:
    """Refuse a calibration derived under a different strategy.

    The load-bearing rule of this milestone. Module 20's envelopes and Module
    21's bins are measurements of *specific checkpoints* answering specific
    actions: expected verified gain, cost, false-positive risk and the
    successor transitions all move when the models move. Applying one
    strategy's package to another is not a conservative approximation, it is a
    calibration of a system that was never run.

    Raises:
        ModelStrategyError: when the artifact's strategy is not ``strategy``.
    """
    owner = calibration_strategy(provenance)
    if owner is not strategy:
        raise ModelStrategyError(
            f"this calibration was derived under model_strategy "
            f"{owner.value!r} but the run declares {strategy.value!r}; "
            "changed models change expected gain, cost, FP risk and the "
            "transition history, so the package does not describe this system")


__all__ = [
    "BASELINE_ROLES",
    "DEFAULT_CALIBRATION_STRATEGY",
    "PORTFOLIO_OPERATION_ROLES",
    "PORTFOLIO_RELATION_ROLES",
    "PORTFOLIO_ROLES",
    "BASELINE_VERIFIER_MODEL_ID",
    "PORTFOLIO_IMPLEMENTATION_BLOCKERS",
    "PORTFOLIO_V2",
    "PORTFOLIO_V2_PARAMETERS",
    "STRATEGY_ROLES",
    "STRATEGY_STATUS",
    "ModelStrategy",
    "ModelStrategyError",
    "StrategyProfile",
    "StrategyStatus",
    "calibration_strategy",
    "check_calibration_portfolio",
    "check_calibration_strategy",
    "declared_strategy",
    "parse_strategy",
    "portfolio_fingerprint",
    "portfolio_governance_blockers",
    "require_portfolio_governance",
    "resolve_strategy",
    "role_for_operation",
]
