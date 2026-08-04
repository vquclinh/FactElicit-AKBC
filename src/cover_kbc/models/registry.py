"""Build model runtimes from configuration.

The backend is named in a config file, never hard-coded at a call site, so the
same COVER logic can be pointed at any compliant checkpoint for the bake-off.
"""

from __future__ import annotations

from typing import Any, Mapping

from cover_kbc.models.base import LMRuntime, ModelSpec
from cover_kbc.models.offline import ABSTAIN_OUTPUT, NullRuntime, ScriptedRuntime

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
        # Optional canned outputs, so a scripted smoke can produce candidates
        # and exercise the control loop rather than abstaining on every call.
        # Purely a plumbing fixture: these are not facts and any metric derived
        # from them is meaningless.
        responses = config.get("responses") or {}
        default = str(config.get("default_response", "")) or None

        def fallback(request, _responses=dict(responses), _default=default):
            view_id = str(request.metadata.get("view_id", ""))
            if view_id in _responses:
                return str(_responses[view_id])
            return _default if _default is not None else ABSTAIN_OUTPUT

        return ScriptedRuntime(
            model_id=config.get("model_id", "offline/scripted"),
            family=str(config.get("family", "offline")),
            role=str(config.get("role", "enumerator")),
            fallback=fallback if (responses or default) else None,
        )

    if backend in {"huggingface", "hf"}:
        model_id = config.get("model_id")
        if not model_id:
            raise ValueError("model_profile.model_id is required for the huggingface backend")
        params = (
            config.get("budget_count_parameters")
            or config.get("published_checkpoint_parameters")
            or config.get("published_total_parameters")
        )
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
            published_language_parameters=config.get("published_language_parameters"),
            published_checkpoint_parameters=config.get("published_checkpoint_parameters"),
            budget_count_parameters=config.get("budget_count_parameters"),
            parameter_source=config.get("parameter_source", ""),
            parameter_source_verified=bool(config.get("parameter_source_verified", False)),
            max_memory=config.get("max_memory"),
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
    is_neural = backend not in _OFFLINE_BACKENDS
    params = config.get("published_total_parameters")

    def _int(key: str) -> int | None:
        value = config.get(key)
        return None if value is None else int(value)

    return ModelSpec(
        model_id=config.get("model_id", f"offline/{backend}"),
        published_total_parameters=None if params is None else int(params),
        family=config.get("family", ""),
        revision=config.get("revision", "main"),
        license=config.get("license", ""),
        role=config.get("role", "generator"),
        is_neural=is_neural,
        supports_logits=True,
        quantization=config.get("quantization"),
        source=config.get("source", ""),
        notes=config.get("notes", ""),
        published_language_parameters=_int("published_language_parameters"),
        published_checkpoint_parameters=_int("published_checkpoint_parameters"),
        budget_count_parameters=_int("budget_count_parameters"),
        parameter_source=config.get("parameter_source", ""),
        # A non-neural stub has nothing to verify; a real model must say so.
        parameter_source_verified=bool(
            config.get("parameter_source_verified", not is_neural)
        ),
    )
