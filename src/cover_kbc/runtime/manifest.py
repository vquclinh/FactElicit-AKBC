"""Run manifests: everything needed to reproduce or audit an inference run.

Spec invariant 10: "A complete validation run can be reproduced from one
command and a committed config."  The manifest is what makes that checkable -
it pins the config hash, the model identity and parameter count, the seed, the
dataset checksum and the evaluator checksum.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cover_kbc import __version__
from cover_kbc.evaluation.official import evaluator_checksum
from cover_kbc.models.base import ModelSpec
from cover_kbc.paths import REPO_ROOT


def config_hash(config: Mapping[str, Any]) -> str:
    """Stable hash of a configuration mapping."""
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def git_revision() -> str:
    """Current commit, with a ``-dirty`` suffix when the tree has changes."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if rev.returncode != 0:
            return "unknown"
        commit = rev.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return f"{commit}-dirty" if status.stdout.strip() else commit
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return "unknown"


@dataclass
class RunManifest:
    """Immutable-by-convention record of one inference run."""

    run_id: str
    experiment: str
    split: str
    seed: int
    config: dict[str, Any] = field(default_factory=dict)
    model_specs: list[dict[str, Any]] = field(default_factory=list)
    dataset_sha256: str = ""
    dataset_path: str = ""
    num_queries: int = 0
    started_at: str = ""
    finished_at: str = ""
    total_calls: int = 0
    total_generated_tokens: int = 0
    total_prompt_tokens: int = 0
    budget_audit: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # -- environment ---------------------------------------------------------
    cover_kbc_version: str = __version__
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    git_revision: str = field(default_factory=git_revision)
    evaluator_sha256: str = field(default_factory=evaluator_checksum)

    @property
    def config_hash(self) -> str:
        return config_hash(self.config)

    def add_model(self, spec: ModelSpec) -> None:
        payload = spec.to_json()
        if payload not in self.model_specs:
            self.model_specs.append(payload)

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["config_hash"] = self.config_hash
        return payload

    def write(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n")
        return out


def new_run_id(experiment: str, split: str) -> str:
    """Timestamped, human-sortable run identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{experiment}_{split}_{stamp}"
