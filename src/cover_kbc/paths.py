"""Canonical filesystem locations.

The official benchmark snapshot under ``benchmark/`` is treated as a read-only
upstream dependency.  Nothing in this package may write into it.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
REPO_ROOT = SRC_DIR.parent

BENCHMARK_DIR = REPO_ROOT / "benchmark"
BENCHMARK_DATA_DIR = BENCHMARK_DIR / "data"
BENCHMARK_EVALUATOR = BENCHMARK_DIR / "evaluate.py"
BENCHMARK_PROMPTS_DIR = BENCHMARK_DIR / "prompt_templates"

CONFIGS_DIR = REPO_ROOT / "configs"
OUTPUTS_DIR = REPO_ROOT / "outputs"

SPLIT_FILES = {
    "train": BENCHMARK_DATA_DIR / "train.jsonl",
    "val": BENCHMARK_DATA_DIR / "val.jsonl",
    "test": BENCHMARK_DATA_DIR / "test.jsonl",
}


def outputs_dir(*parts: str) -> Path:
    """Return a path under ``outputs/``, creating parent directories."""
    path = OUTPUTS_DIR.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
