"""Load the official ``benchmark/evaluate.py`` as a module, without copying it.

The scoring logic is never reimplemented in this repository.  Every metric we
report comes from executing the upstream file that lives in the pinned snapshot,
so a snapshot refresh automatically changes our numbers too.

``benchmark/`` is not a Python package (no ``__init__.py``), so the module is
loaded by file path.  The loaded module is cached; it is never mutated.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from functools import lru_cache
from types import ModuleType

from cover_kbc.paths import BENCHMARK_EVALUATOR

_MODULE_NAME = "cover_kbc._vendored_official_evaluate"


@lru_cache(maxsize=1)
def load_official_evaluator() -> ModuleType:
    """Import and return the official evaluator module.

    Raises:
        FileNotFoundError: if the benchmark snapshot is missing.
    """
    if not BENCHMARK_EVALUATOR.is_file():
        raise FileNotFoundError(
            f"Official evaluator not found at {BENCHMARK_EVALUATOR}. "
            "The benchmark/ snapshot must be present and unmodified."
        )
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, BENCHMARK_EVALUATOR)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Could not build an import spec for {BENCHMARK_EVALUATOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # pragma: no cover - defensive
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return module


@lru_cache(maxsize=1)
def evaluator_checksum() -> str:
    """SHA-256 of the official evaluator, recorded in every run manifest."""
    return hashlib.sha256(BENCHMARK_EVALUATOR.read_bytes()).hexdigest()


def relation_types() -> dict[str, str]:
    """Official relation -> ``"string"`` / ``"numeric"`` map, from the snapshot."""
    return dict(load_official_evaluator().RELATION_TYPE)


def official_normalize_string(value: str) -> str:
    """The evaluator's exact normalisation, reused for internal deduplication.

    Using the evaluator's own function guarantees that two predictions we treat
    as distinct are also distinct to the scorer - which matters because the
    evaluator silently collapses predictions that share a normalised form.
    """
    return load_official_evaluator().normalize_string(value)


def official_try_parse_number(value: str) -> float | None:
    """The evaluator's exact numeric parser.

    Any numeric string we emit must survive this function, otherwise it can
    never be a true positive while still inflating the precision denominator.
    """
    return load_official_evaluator().try_parse_number(value)
