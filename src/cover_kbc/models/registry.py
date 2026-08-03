"""Build model runtimes from configuration.

The backend is named in a config file, never hard-coded at a call site, so the
same COVER logic can be pointed at any compliant checkpoint for the bake-off.
"""

from __future__ import annotations

from typing import Any, Mapping

from cover_kbc.models.base import LMRuntime, ModelSpec
from cover_kbc.models.offline import NullRuntime, ScriptedRuntime

#: Backends that need no optional dependencies.
_OFFLINE_BACKENDS = {"null", "scripted"}


def build_runtime(config: Mapping[str, Any]) -> LMRuntime:
    """Construct a runtime from a ``model_profile`` config block.

    Expected keys::

        backend: null | scripted | huggingface
        model_id: str
        published_total_parameters: int | null   # required for huggingface
        revision / family / license / role / source / quantization: optional

    Raises:
        ValueError: on an unknown backend or a neural backend with no recorded
            parameter count.
    """
    # A bare `backend: null` in YAML parses to None, which means the same thing
    # here as the string "null".
    backend = str(config.get("backend") or "null").lower()

    if backend == "null":
        return NullRuntime(model_id=config.get("model_id", "offline/null"))

    if backend == "scripted":
        return ScriptedRuntime(model_id=config.get("model_id", "offline/scripted"))

    if backend in {"huggingface", "hf"}:
        model_id = config.get("model_id")
        if not model_id:
            raise ValueError("model_profile.model_id is required for the huggingface backend")
        params = config.get("published_total_parameters")
        if params is None:
            raise ValueError(
                f"{model_id}: 'published_total_parameters' must be recorded before this "
                "profile can be used. The 32B budget audit refuses to guess from a model name."
            )
        from cover_kbc.models.huggingface import HuggingFaceRuntime

        return HuggingFaceRuntime(
            model_id=model_id,
            published_total_parameters=int(params),
            revision=config.get("revision", "main"),
            family=config.get("family", ""),
            license=config.get("license", ""),
            role=config.get("role", "generator"),
            device_map=config.get("device_map", "auto"),
            torch_dtype=config.get("torch_dtype", "auto"),
            quantization=config.get("quantization"),
            trust_remote_code=bool(config.get("trust_remote_code", False)),
            source=config.get("source", ""),
        )

    raise ValueError(
        f"Unknown model backend {backend!r}; "
        f"expected one of {sorted(_OFFLINE_BACKENDS | {'huggingface'})}"
    )


def spec_from_config(config: Mapping[str, Any]) -> ModelSpec:
    """Build a :class:`ModelSpec` without loading any weights.

    Lets ``scripts/audit_model_budget.py`` check a profile's compliance before
    anybody downloads tens of gigabytes.
    """
    backend = str(config.get("backend") or "null").lower()
    params = config.get("published_total_parameters")
    return ModelSpec(
        model_id=config.get("model_id", f"offline/{backend}"),
        published_total_parameters=None if params is None else int(params),
        family=config.get("family", ""),
        revision=config.get("revision", "main"),
        license=config.get("license", ""),
        role=config.get("role", "generator"),
        is_neural=backend not in _OFFLINE_BACKENDS,
        supports_logits=backend not in _OFFLINE_BACKENDS or backend == "scripted",
        quantization=config.get("quantization"),
        source=config.get("source", ""),
        notes=config.get("notes", ""),
    )
