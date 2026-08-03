"""Model-agnostic runtime abstraction and the 32B budget audit."""

from cover_kbc.models.base import (
    BaseRuntime,
    GenerationRequest,
    GenerationResult,
    HiddenStatesUnavailable,
    LabelScoreRequest,
    LabelScoreResult,
    LMRuntime,
    LogitsUnavailable,
    ModelSpec,
    entropy,
    softmax,
)
from cover_kbc.models.budget import PARAMETER_BUDGET, BudgetAudit, audit_parameter_budget
from cover_kbc.models.offline import ABSTAIN_OUTPUT, NullRuntime, ScriptedRuntime
from cover_kbc.models.registry import build_runtime, spec_from_config

__all__ = [
    "ABSTAIN_OUTPUT",
    "BaseRuntime",
    "BudgetAudit",
    "GenerationRequest",
    "GenerationResult",
    "HiddenStatesUnavailable",
    "LMRuntime",
    "LabelScoreRequest",
    "LabelScoreResult",
    "LogitsUnavailable",
    "ModelSpec",
    "NullRuntime",
    "PARAMETER_BUDGET",
    "ScriptedRuntime",
    "audit_parameter_budget",
    "build_runtime",
    "entropy",
    "softmax",
    "spec_from_config",
]
