#!/usr/bin/env python3
"""Audit a model profile against the 32B inference-time parameter budget.

Runs entirely from configuration - no weights are downloaded - so compliance can
be checked before anybody fetches a checkpoint.

Example:
    python scripts/audit_model_budget.py configs/models/qwen3.5-9b-baseline.yaml
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

import yaml

from cover_kbc.models.budget import audit_parameter_budget
from cover_kbc.models.registry import spec_from_config


def load_profile(path: Path) -> list[dict]:
    """Read a profile file into a list of component config blocks."""
    data = yaml.safe_load(path.read_text()) or {}
    profile = data.get("model_profile", data)
    if isinstance(profile, list):
        return profile
    components = [
        block
        for key in ("generator", "verifier", "enumerator", "components")
        for block in (profile[key] if isinstance(profile.get(key), list) else [profile.get(key)])
        if isinstance(block, dict)
    ]
    return components or [profile]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+", type=Path, help="model profile YAML file(s)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    exit_code = 0
    for path in args.profiles:
        specs = [spec_from_config(block) for block in load_profile(path)]
        audit = audit_parameter_budget(specs)
        if args.json:
            print(json.dumps({"profile": str(path), **audit.to_json()}, indent=2))
        else:
            print(f"=== {path} ===")
            print(audit.summary())
            print()
        if not audit.passed:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
