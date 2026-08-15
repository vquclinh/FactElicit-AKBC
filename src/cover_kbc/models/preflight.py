"""Can this environment load the checkpoints it is about to build?

The active Profile F1 pipeline is Mistral-only, but historical profiles can
still name the retired Qwen verifier. This preflight is therefore model-aware:
it enforces the Mistral floor for current profiles and the stricter Qwen3.5
floor only when a declared neural block actually names Qwen.

Nothing here downloads a checkpoint or imports ``torch``. The version is read
from installed package metadata rather than by importing ``transformers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: First ``transformers`` release that ships ``transformers.models.qwen3_5``.
#: 4.57.6 - the last of the 4.57 line - does not, which is what the real run
#: hit. Verified against the upstream tree at v5.1.0 (absent) and v5.2.0
#: (present).
QWEN3_5_MIN_TRANSFORMERS = (5, 2, 0)

#: The architecture the verifier declares. Named rather than inferred from a
#: model id, because the id is not what the loader dispatches on.
QWEN3_5_MODEL_TYPE = "qwen3_5"

#: First floor retained for the current Mistral-Small-3.2/Tekken path. This is
#: lower than the historical Qwen floor, so current Mistral-only profiles no
#: longer inherit a Qwen-only dependency requirement.
MISTRAL3_MIN_TRANSFORMERS = (4, 52, 4)
MISTRAL3_MODEL_TYPE = "mistral3"

#: Backends that load a real checkpoint. A stub-backed profile - the abstain
#: and scripted smokes - needs no transformers at all and is never asked for it.
NEURAL_BACKENDS = frozenset({"huggingface", "hf"})


@dataclass(frozen=True)
class PreflightReport:
    """What is missing, and how to get it. Empty ``blockers`` means ready."""

    blockers: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_json(self) -> dict[str, object]:
        return {"ready": self.ready, "blockers": list(self.blockers),
                "satisfied": list(self.satisfied)}


def installed_transformers_version() -> str:
    """The installed ``transformers`` version, or ``""`` if absent.

    Read from distribution metadata rather than by importing the package: this
    runs before any model work, and importing ``transformers`` pulls in torch.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:                                   # pragma: no cover
        return ""
    try:
        return version("transformers")
    except PackageNotFoundError:
        return ""


def _parse(version_string: str) -> tuple[int, ...]:
    """Leading numeric components of a version, for ordering.

    Stops at the first non-numeric part, so ``5.2.0.dev0`` reads as ``(5, 2, 0)``
    and a release candidate does not sort above its release.
    """
    parts: list[int] = []
    for chunk in str(version_string).split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
        if digits != chunk:
            break
    return tuple(parts)


def supports_qwen3_5(version_string: str | None = None) -> bool:
    """Whether this ``transformers`` can dispatch a ``qwen3_5`` checkpoint."""
    resolved = (installed_transformers_version() if version_string is None
                else version_string)
    if not resolved:
        return False
    parsed = _parse(resolved)
    return bool(parsed) and parsed >= QWEN3_5_MIN_TRANSFORMERS


def check_qwen3_5(version_string: str | None = None) -> PreflightReport:
    """Refuse an environment that cannot load the Qwen3.5 verifier."""
    resolved = (installed_transformers_version() if version_string is None
                else version_string)
    minimum = ".".join(str(n) for n in QWEN3_5_MIN_TRANSFORMERS)
    if not resolved:
        return PreflightReport(blockers=(
            "transformers is not installed; the Qwen3.5 verifier needs "
            f">={minimum}. Install the neural extra: pip install -e '.[hf]'",))
    if not supports_qwen3_5(resolved):
        return PreflightReport(blockers=(
            f"transformers {resolved} does not recognize the "
            f"{QWEN3_5_MODEL_TYPE!r} architecture; {minimum} is the first "
            "release that ships it. Upgrade: "
            f"pip install 'transformers>={minimum}'",))
    return PreflightReport(
        satisfied=(f"transformers {resolved} supports {QWEN3_5_MODEL_TYPE}",))


def supports_mistral3(version_string: str | None = None) -> bool:
    """Whether this ``transformers`` satisfies the active Mistral3 floor."""
    resolved = (installed_transformers_version() if version_string is None
                else version_string)
    if not resolved:
        return False
    parsed = _parse(resolved)
    return bool(parsed) and parsed >= MISTRAL3_MIN_TRANSFORMERS


def check_mistral3(version_string: str | None = None) -> PreflightReport:
    """Refuse an environment that cannot load the active Mistral3 checkpoint."""
    resolved = (installed_transformers_version() if version_string is None
                else version_string)
    minimum = ".".join(str(n) for n in MISTRAL3_MIN_TRANSFORMERS)
    if not resolved:
        return PreflightReport(blockers=(
            "transformers is not installed; the Mistral runtime needs "
            f">={minimum}. Install the neural extra: pip install -e '.[hf]'",))
    if not supports_mistral3(resolved):
        return PreflightReport(blockers=(
            f"transformers {resolved} is below the {MISTRAL3_MODEL_TYPE!r} "
            f"runtime floor; upgrade: pip install 'transformers>={minimum}'",))
    return PreflightReport(
        satisfied=(f"transformers {resolved} satisfies {MISTRAL3_MODEL_TYPE}",))


def needs_transformers(*blocks: Mapping[str, Any] | None) -> bool:
    """Whether any of these model blocks will load a real checkpoint."""
    return any(
        str((block or {}).get("backend") or "").lower() in NEURAL_BACKENDS
        for block in blocks)


def _is_qwen_block(block: Mapping[str, Any] | None) -> bool:
    resolved = dict(block or {})
    family = str(resolved.get("family") or "").lower()
    model_id = str(resolved.get("model_id") or "").lower()
    tokenizer_backend = str(resolved.get("tokenizer_backend") or "").lower()
    return "qwen" in family or "qwen/" in model_id or tokenizer_backend == "qwen"


def _is_mistral_block(block: Mapping[str, Any] | None) -> bool:
    resolved = dict(block or {})
    family = str(resolved.get("family") or "").lower()
    model_id = str(resolved.get("model_id") or "").lower()
    tokenizer_backend = str(resolved.get("tokenizer_backend") or "").lower()
    return (
        "mistral" in family
        or "mistralai/" in model_id
        or tokenizer_backend == "mistral_common"
    )


def _combine(*reports: PreflightReport) -> PreflightReport:
    blockers: list[str] = []
    satisfied: list[str] = []
    for report in reports:
        blockers.extend(report.blockers)
        satisfied.extend(report.satisfied)
    return PreflightReport(blockers=tuple(blockers), satisfied=tuple(satisfied))


def huggingface_runtime_report(
    *blocks: Mapping[str, Any] | None, version_string: str | None = None,
) -> PreflightReport:
    """What this environment still needs to load the declared checkpoints.

    Returns an empty report - ready - when no declared block uses a neural
    backend, so the offline smokes are unaffected.
    """
    if not needs_transformers(*blocks):
        return PreflightReport(satisfied=(
            "no neural backend is declared; transformers is not required",))
    reports: list[PreflightReport] = []
    if any(_is_mistral_block(block) for block in blocks):
        reports.append(check_mistral3(version_string))
    if any(_is_qwen_block(block) for block in blocks):
        reports.append(check_qwen3_5(version_string))
    if not reports:
        resolved = installed_transformers_version() if version_string is None else version_string
        if not resolved:
            return PreflightReport(blockers=(
                "transformers is not installed; install the neural extra: "
                "pip install -e '.[hf]'",))
        return PreflightReport(satisfied=(f"transformers {resolved} is installed",))
    return _combine(*reports)


def require_huggingface_runtime(
    *blocks: Mapping[str, Any] | None, version_string: str | None = None,
) -> None:
    """Fail closed before the first checkpoint is fetched.

    Raises:
        RuntimeError: naming every unmet requirement and the command that
            fixes it.
    """
    report = huggingface_runtime_report(*blocks, version_string=version_string)
    if report.blockers:
        listed = "\n  - ".join(report.blockers)
        raise RuntimeError(
            "this environment cannot load the declared models:\n  - " + listed)


__all__ = [
    "NEURAL_BACKENDS",
    "MISTRAL3_MIN_TRANSFORMERS",
    "MISTRAL3_MODEL_TYPE",
    "QWEN3_5_MIN_TRANSFORMERS",
    "QWEN3_5_MODEL_TYPE",
    "PreflightReport",
    "check_mistral3",
    "check_qwen3_5",
    "huggingface_runtime_report",
    "installed_transformers_version",
    "needs_transformers",
    "require_huggingface_runtime",
    "supports_mistral3",
    "supports_qwen3_5",
]
