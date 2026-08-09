"""Can this environment actually load the checkpoints it is about to?

One failure, learned the expensive way. During the first real-weight run on an
L4 (Audit 0065), ``transformers`` 4.57.6 raised

    The checkpoint has model type qwen3_5 but Transformers does not
    recognize this architecture.

**after** minutes of downloading, at the moment the verifier was constructed.
It is answerable in milliseconds from installed metadata, so it is answered
before the first ``build_runtime``.

This is not a portfolio concern and did not retire with the portfolio: the
baseline's own verifier, ``Qwen/Qwen3.5-4B``, declares ``model_type: qwen3_5``
exactly as the retired 9B did. The repository's ``hf`` extra therefore pins
``transformers>=5.2.0``, and this module is what enforces it at run time.

Nothing here downloads a checkpoint or imports ``torch``. The version is read
from the installed distribution's metadata rather than by importing
``transformers``, so a deterministic test suite on a machine with no GPU stack
exercises every path in this file.
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


def needs_transformers(*blocks: Mapping[str, Any] | None) -> bool:
    """Whether any of these model blocks will load a real checkpoint."""
    return any(
        str((block or {}).get("backend") or "").lower() in NEURAL_BACKENDS
        for block in blocks)


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
    return check_qwen3_5(version_string)


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
    "QWEN3_5_MIN_TRANSFORMERS",
    "QWEN3_5_MODEL_TYPE",
    "PreflightReport",
    "check_qwen3_5",
    "huggingface_runtime_report",
    "installed_transformers_version",
    "needs_transformers",
    "require_huggingface_runtime",
    "supports_qwen3_5",
]
