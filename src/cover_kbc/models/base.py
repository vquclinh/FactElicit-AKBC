"""Model runtime interface (spec section 21.2).

Two modes, deliberately separated:

``generate``
    free-form text plus token/cost accounting and optional token log-probs.

``score_labels``
    no free-form generation; return next-token logits restricted to a fixed
    label set (``A``/``B``/``C`` for the blind verifier).

Nothing above this interface may import a specific model family.  Prompts,
token ids, layer counts and label tokens are backend concerns, so the same
COVER logic can be evaluated against several open-weight checkpoints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from cover_kbc.types import DecodeProfile


@dataclass(frozen=True)
class ModelSpec:
    """Identity and compliance metadata for one neural component.

    The challenge counts *published* parameters, so several distinct numbers
    have to be kept apart rather than collapsed into one "size":

    ``published_language_parameters``
        The language model alone - usually the figure a model card headlines.
    ``published_checkpoint_parameters``
        Every neural parameter in the released checkpoint, including a vision
        tower, an MTP head, or any other non-text component.
    ``budget_count_parameters``
        What we actually charge against the 32B budget. Conservative by
        default: the full checkpoint, unless we can demonstrate that only a
        strict subset is instantiated at inference.
    ``loaded_neural_components``
        What the runtime really materialised, recorded after loading so the
        claim above can be checked rather than trusted.

    Quantisation is recorded but never reduces any of these.
    """

    model_id: str
    published_total_parameters: int | None
    family: str = ""
    revision: str = ""
    license: str = ""
    role: str = "generator"
    is_neural: bool = True
    supports_logits: bool = False
    supports_hidden_states: bool = False
    quantization: str | None = None
    source: str = ""
    notes: str = ""
    published_language_parameters: int | None = None
    published_checkpoint_parameters: int | None = None
    budget_count_parameters: int | None = None
    parameter_source: str = ""
    parameter_source_verified: bool = False
    loaded_neural_components: tuple[str, ...] = ()
    loaded_parameter_count: int | None = None

    @property
    def billions(self) -> float | None:
        if self.budget_parameters is None:
            return None
        return self.budget_parameters / 1e9

    @property
    def budget_parameters(self) -> int | None:
        """The number charged against the 32B budget.

        Falls back through the conservative chain: an explicit budget count,
        else the full checkpoint, else the legacy total.
        """
        for value in (
            self.budget_count_parameters,
            self.published_checkpoint_parameters,
            self.published_total_parameters,
        ):
            if value is not None:
                return value
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "published_total_parameters": self.published_total_parameters,
            "published_language_parameters": self.published_language_parameters,
            "published_checkpoint_parameters": self.published_checkpoint_parameters,
            "budget_count_parameters": self.budget_parameters,
            "billions": self.billions,
            "parameter_source": self.parameter_source,
            "parameter_source_verified": self.parameter_source_verified,
            "loaded_neural_components": list(self.loaded_neural_components),
            "loaded_parameter_count": self.loaded_parameter_count,
            "family": self.family,
            "revision": self.revision,
            "license": self.license,
            "role": self.role,
            "is_neural": self.is_neural,
            "supports_logits": self.supports_logits,
            "supports_hidden_states": self.supports_hidden_states,
            "quantization": self.quantization,
            "source": self.source,
            "notes": self.notes,
        }


@dataclass
class GenerationRequest:
    """One text-generation call."""

    prompt: str
    decode: DecodeProfile = field(default_factory=DecodeProfile)
    system_prompt: str | None = None
    stop: tuple[str, ...] = ()
    return_logprobs: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Result of a text-generation call, with cost accounting."""

    text: str
    model_id: str
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    latency_ms: float | None = None
    token_logprobs: list[float] | None = None
    finish_reason: str = "stop"
    error: str | None = None


@dataclass
class LabelScoreRequest:
    """Restricted scoring call for the blind verifier.

    ``labels`` maps a label name to the exact continuation string whose first
    token is scored, e.g. ``{"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}``.
    """

    prompt: str
    labels: dict[str, str]
    system_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LabelScoreResult:
    """Raw label logits plus the derived distribution.

    ``logits`` are the uncalibrated values ``z_j`` from spec section 10.2.
    Contextual calibration subtracts a content-free control's bias logits; that
    step lives in the verifier, not here, so this stays a pure model read-out.
    """

    logits: dict[str, float]
    model_id: str
    prompt_tokens: int | None = None
    latency_ms: float | None = None
    error: str | None = None

    def probabilities(self, temperature: float = 1.0) -> dict[str, float]:
        """Softmax over the label logits."""
        return softmax(self.logits, temperature=temperature)

    def argmax_label(self) -> str:
        """Highest-scoring label; ties break on label name for determinism."""
        return min(self.logits, key=lambda k: (-self.logits[k], k))


def softmax(logits: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    """Numerically stable softmax over a small label dictionary."""
    if not logits:
        return {}
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    scaled = {k: v / temperature for k, v in logits.items()}
    peak = max(scaled.values())
    exps = {k: math.exp(v - peak) for k, v in scaled.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def entropy(probabilities: dict[str, float]) -> float:
    """Shannon entropy in nats, ``H_ver`` from spec section 10.3."""
    return -sum(p * math.log(p) for p in probabilities.values() if p > 0)


@runtime_checkable
class LMRuntime(Protocol):
    """The contract every model backend implements."""

    @property
    def spec(self) -> ModelSpec:
        """Identity and compliance metadata for this backend."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate text for one prompt."""
        ...

    def score_labels(self, request: LabelScoreRequest) -> LabelScoreResult:
        """Return next-token logits restricted to ``request.labels``.

        Backends that cannot expose logits raise :class:`LogitsUnavailable`.
        """
        ...


class LogitsUnavailable(NotImplementedError):
    """Raised when a backend cannot expose label logits."""


class HiddenStatesUnavailable(NotImplementedError):
    """Raised when a backend cannot expose intermediate hidden states.

    Reserved for the Milestone 3 DoLa branch; the architecture must never
    depend on hidden-state access for correctness.
    """


class BaseRuntime:
    """Convenience base implementing the optional parts of the protocol."""

    def __init__(self, spec: ModelSpec) -> None:
        self._spec = spec
        self.calls = 0
        self.generated_tokens = 0

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    def generate(self, request: GenerationRequest) -> GenerationResult:  # pragma: no cover
        raise NotImplementedError

    def score_labels(self, request: LabelScoreRequest) -> LabelScoreResult:
        raise LogitsUnavailable(
            f"{type(self).__name__} ({self._spec.model_id}) does not expose label logits"
        )

    def hidden_states(self, prompt: str, layers: Sequence[int]) -> Any:
        raise HiddenStatesUnavailable(
            f"{type(self).__name__} ({self._spec.model_id}) does not expose hidden states"
        )

    def usage(self) -> dict[str, int]:
        return {"calls": self.calls, "generated_tokens": self.generated_tokens}
