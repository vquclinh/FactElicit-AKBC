#!/usr/bin/env python3
"""Assemble a QUARANTINED, human-inspection-only patched copy of a failed run.

DO NOT SUBMIT THE OUTPUT OF THIS SCRIPT.

Audit 0079 section 2. The user asked for a copy of the failed TEST predictions in
which the orchestration-failure holes are filled with Claude's own best-effort
guesses, so a human can eyeball what those rows were about. That artifact is a
reading aid and nothing else.

**This script contains no factual knowledge.** The guesses live in a CSV that the
caller supplies, which by repository policy sits inside the gitignored quarantine
directory. Nothing resembling a subject-to-exchange table enters the source tree,
because a closed-book benchmark must not acquire a hand-built answer key by the
back door - see audit 0079 section 9, category C.

The script's whole job is mechanical: check the guess file covers exactly the
failed identity set, splice those rows into a copy, and stamp the result with
warnings loud enough that nobody mistakes it for a submission.

Usage::

    python scripts/build_claude_diagnostic_patch.py \
      --run-dir outputs/<failed run>/run \
      --guesses outputs/<failed run>/claude_diagnostic_only/claude_guesses.csv \
      --output-dir outputs/<failed run>/claude_diagnostic_only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "claude-diagnostic-patch-v1"

BANNER = """\
# DO NOT SUBMIT.
# NOT COVER-KBC OUTPUT.
# CLAUDE-AUTHORED DIAGNOSTIC GUESSES.
# NOT GROUND TRUTH.
# NOT VALID FOR CALIBRATION OR BENCHMARK EVALUATION.
"""

VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_objects(cell: str) -> list[str]:
    """Split a guess cell into objects. Empty means 'no answer offered'."""
    return [part.strip() for part in (cell or "").split(";") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--guesses", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    predictions_path = args.run_dir / "predictions.jsonl"
    errors_path = args.run_dir / "errors.json"
    for path in (predictions_path, errors_path, args.guesses):
        if not path.exists():
            parser.error(f"missing required input: {path}")

    base = read_jsonl(predictions_path)
    errors = json.loads(errors_path.read_text())
    failed = {(entry["SubjectEntity"], entry["Relation"]) for entry in errors}
    if len(failed) != len(errors):
        parser.error("errors.json contains duplicate identities")

    guesses: dict[tuple[str, str], dict[str, Any]] = {}
    with args.guesses.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["SubjectEntity"], row["Relation"])
            if key in guesses:
                parser.error(f"guess file lists {key} twice")
            confidence = (row.get("confidence") or "").strip().upper()
            if confidence not in VALID_CONFIDENCE:
                parser.error(
                    f"{key}: confidence {confidence!r} is not one of "
                    f"{sorted(VALID_CONFIDENCE)}")
            guesses[key] = {
                "objects": parse_objects(row.get("claude_diagnostic_ObjectEntities", "")),
                "confidence": confidence,
                "short_reason": (row.get("short_reason") or "").strip(),
            }

    missing = failed - set(guesses)
    extra = set(guesses) - failed
    if missing:
        parser.error(f"{len(missing)} failed identity(ies) have no guess: {sorted(missing)[:5]}")
    if extra:
        parser.error(f"{len(extra)} guess(es) are not failed rows: {sorted(extra)[:5]}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    patched: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    for row in base:
        key = (row["SubjectEntity"], row["Relation"])
        if key not in failed:
            patched.append({k: row[k] for k in
                            ("SubjectEntity", "Relation", "ObjectEntities")})
            continue
        if row.get("ObjectEntities"):
            parser.error(
                f"{key} is listed as failed but its base row is non-empty; "
                "refusing to overwrite a real prediction")
        guess = guesses[key]
        patched.append({
            "SubjectEntity": key[0],
            "Relation": key[1],
            "ObjectEntities": list(guess["objects"]),
        })
        detail.append({
            "SubjectEntity": key[0],
            "Relation": key[1],
            "original_ObjectEntities": json.dumps(row.get("ObjectEntities") or []),
            "claude_diagnostic_ObjectEntities": json.dumps(guess["objects"]),
            "confidence": guess["confidence"],
            "short_reason": guess["short_reason"],
        })

    patched_path = args.output_dir / "predictions_claude_diagnostic.jsonl"
    with patched_path.open("w", encoding="utf-8") as handle:
        for row in patched:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    detail_path = args.output_dir / "claude_guesses.csv"
    if detail_path.resolve() != args.guesses.resolve():
        with detail_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(detail[0].keys()))
            writer.writeheader()
            writer.writerows(detail)

    offered = sum(1 for row in detail if json.loads(row["claude_diagnostic_ObjectEntities"]))
    by_confidence: dict[str, int] = {}
    for row in detail:
        by_confidence[row["confidence"]] = by_confidence.get(row["confidence"], 0) + 1

    readme = f"""{BANNER}
# Claude Diagnostic Patch - human inspection only

This directory contains a copy of a failed TEST prediction file in which the
{len(failed)} orchestration-failure rows have been filled in with **Claude's own
internal guesses**. It exists so a human can read what those rows were about.

## What this is not

* not a submission, and must never be submitted;
* not COVER-KBC output - {len(failed)} of its {len(patched)} rows were authored by a
  language model reasoning from memory, not by the system;
* not ground truth;
* not valid as calibration data, TRAIN data, M20/M21 derivation input,
  prompt-selection evidence, rule-selection evidence, or benchmark evaluation
  evidence;
* not evidence that any answer is correct.

Agreement between a Claude guess and a COVER-KBC output means only that two
language models produced the same string.

## How it was made

* the {len(patched)} base rows come from the failed run, unmodified;
* the failed identities were derived from that run's own `errors.json`;
* only rows that were **empty** in the base were touched;
* guesses came from Claude's internal knowledge with no web search, no RAG, no
  external knowledge base, no API and no file lookup.

## Honesty of the guesses

| confidence | rows |
|---|---:|
""" + "".join(
        f"| {name} | {by_confidence.get(name, 0)} |\n"
        for name in ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
    ) + f"""
An answer was offered for **{offered} of {len(failed)}** rows. The rest were left
empty on purpose: for many of these subjects the honest answer is either "I do
not know" or "this entity has no listing at all" - several are private limited
companies, subsidiaries, historical entities or an industry association, none of
which can trade on an exchange.

That distribution is itself the useful finding. Leaving a row empty is a
*plausible correct answer* for this relation far more often than for the others,
so an empty stock prediction is weaker evidence of failure than it looks.

## Firewall

No production code path may read this directory. A test asserts that no module
under `src/cover_kbc/`, no config, and none of the production entry points
references `claude_diagnostic_only`.
"""
    (args.output_dir / "README_DO_NOT_SUBMIT.md").write_text(readme, encoding="utf-8")

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "WARNING": (
            "DO NOT SUBMIT. NOT COVER-KBC OUTPUT. CLAUDE-AUTHORED DIAGNOSTIC "
            "GUESSES. NOT GROUND TRUTH. NOT VALID FOR CALIBRATION OR BENCHMARK "
            "EVALUATION."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_predictions": str(predictions_path),
        "base_predictions_sha256": sha256_file(predictions_path),
        "base_rows": len(base),
        "errors_json": str(errors_path),
        "errors_sha256": sha256_file(errors_path),
        "failed_rows": len(failed),
        "rows_patched": len(detail),
        "rows_preserved": len(patched) - len(detail),
        "answers_offered": offered,
        "left_empty": len(detail) - offered,
        "confidence_distribution": by_confidence,
        "knowledge_source": "claude-internal-parametric-memory-only",
        "external_sources_used": [],
        "valid_for": ["human qualitative inspection"],
        "invalid_for": [
            "submission", "ground truth", "calibration", "TRAIN data",
            "M20/M21 derivation", "prompt selection", "rule selection",
            "benchmark evaluation", "any claim that an answer is correct",
        ],
    }
    (args.output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(
        p for p in args.output_dir.iterdir()
        if p.is_file() and p.name != "SHA256SUMS.txt")
    (args.output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(BANNER)
    print(f"rows            : {len(patched)} ({len(detail)} patched, "
          f"{len(patched) - len(detail)} preserved)")
    print(f"answers offered : {offered}/{len(failed)}")
    print(f"confidence      : {by_confidence}")
    print(f"written         : {args.output_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
