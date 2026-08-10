#!/usr/bin/env python3
"""Offline merge for BASE + one or more SUPPLEMENT V3 TRAIN corpora."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import ensure_src_on_path

ensure_src_on_path()

from cover_kbc.controller_calibration.supplemental_coverage import (
    SupplementalCoverageError,
    merge_collections,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--supplement", required=True, action="append", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = merge_collections(
        base_dir=args.base,
        supplement_dirs=tuple(args.supplement),
        output_dir=args.output_dir,
    )
    print(json.dumps({
        "merged_manifest": str(args.output_dir / "manifest.json"),
        "merged_corpus_sha256": manifest.get("merged_corpus_sha256", ""),
        "calibration_derivation_blocked": manifest.get(
            "calibration_derivation_blocked", True),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SupplementalCoverageError as error:
        print(f"REFUSED: {error}")
        raise SystemExit(2)
