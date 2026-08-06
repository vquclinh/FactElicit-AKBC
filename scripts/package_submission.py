#!/usr/bin/env python3
"""Validate an official prediction file and package it for upload.

The archive is an ordinary zip holding exactly one member, ``predictions.jsonl``.
The repository owner inspects the archive before uploading it, so this script
optimises for *refusing* a bad submission rather than for guessing what a
scoring service might tolerate.

Every check here exists because the corresponding mistake scores silently:
a dropped row scores as an empty prediction, a reordered file scores against
the wrong subject, and an extra diagnostic field can invalidate a row without
any error being reported back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

#: The three fields the official format defines. Anything else - a residual
#: score, a trace id, a confidence - is a diagnostic that must not ship.
OFFICIAL_FIELDS = ("SubjectEntity", "Relation", "ObjectEntities")

#: The single member name inside the archive.
ARCHIVE_MEMBER = "predictions.jsonl"


class SubmissionError(RuntimeError):
    """A submission that would score wrongly if uploaded."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise SubmissionError(f"{path}:{number}: not valid JSON: {error}") from error
        if not isinstance(row, dict):
            raise SubmissionError(f"{path}:{number}: expected an object, got {type(row).__name__}")
        rows.append(row)
    return rows


def validate(predictions_path: Path, input_path: Path) -> dict[str, Any]:
    """Check predictions against the official input split they answer.

    ``input_path`` supplies the authoritative row identities and order. Only its
    ``SubjectEntity``/``Relation`` columns are read; any gold objects it happens
    to carry are never inspected, so validating against a labelled split cannot
    leak label information into the submission.
    """
    if input_path.name == "test.jsonl":
        raise SubmissionError(
            "refusing to package against test.jsonl; this task submits the "
            "validation split and TEST is out of scope"
        )

    expected = _read_jsonl(input_path)
    actual = _read_jsonl(predictions_path)

    if len(actual) != len(expected):
        raise SubmissionError(
            f"row count mismatch: predictions have {len(actual)} rows, "
            f"{input_path.name} has {len(expected)}"
        )

    seen: set[tuple[str, str]] = set()
    empty_rows = 0
    relations: dict[str, int] = {}

    for index, (want, got) in enumerate(zip(expected, actual)):
        where = f"{predictions_path}:{index + 1}"

        extra = set(got) - set(OFFICIAL_FIELDS)
        if extra:
            raise SubmissionError(
                f"{where}: row carries non-official field(s) {sorted(extra)}; "
                f"only {list(OFFICIAL_FIELDS)} may be submitted"
            )
        missing = set(OFFICIAL_FIELDS) - set(got)
        if missing:
            raise SubmissionError(f"{where}: row is missing {sorted(missing)}")

        # Identity and order both matter: the scorer pairs row i of the
        # submission with row i of the input.
        if got["SubjectEntity"] != want["SubjectEntity"] or got["Relation"] != want["Relation"]:
            raise SubmissionError(
                f"{where}: expected ({want['SubjectEntity']!r}, {want['Relation']!r}) "
                f"but found ({got['SubjectEntity']!r}, {got['Relation']!r}); "
                "predictions must keep the input's order"
            )

        objects = got["ObjectEntities"]
        if not isinstance(objects, list):
            raise SubmissionError(
                f"{where}: ObjectEntities must be a list, got {type(objects).__name__}"
            )
        for item in objects:
            if not isinstance(item, str):
                raise SubmissionError(
                    f"{where}: ObjectEntities holds a {type(item).__name__}; "
                    "every object must be a string"
                )
        if len(set(objects)) != len(objects):
            duplicates = sorted({o for o in objects if objects.count(o) > 1})
            raise SubmissionError(f"{where}: ObjectEntities repeats {duplicates}")

        key = (got["SubjectEntity"], got["Relation"])
        if key in seen:
            raise SubmissionError(f"{where}: duplicate row for {key}")
        seen.add(key)

        if not objects:
            empty_rows += 1
        relations[got["Relation"]] = relations.get(got["Relation"], 0) + 1

    return {
        "rows": len(actual),
        "empty_rows": empty_rows,
        "rows_with_objects": len(actual) - empty_rows,
        "relations": dict(sorted(relations.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path,
                        help="the predictions.jsonl produced by the run")
    parser.add_argument("--input", required=True, type=Path,
                        help="the official split the predictions answer")
    parser.add_argument("--out", type=Path, default=Path("submission.zip"),
                        help="path of the archive to write")
    args = parser.parse_args()

    summary = validate(args.predictions, args.input)

    archive = args.out
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(args.predictions, arcname=ARCHIVE_MEMBER)

    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
        if zf.testzip() is not None:
            raise SubmissionError(f"{archive}: archive is corrupt")
    if members != [ARCHIVE_MEMBER]:
        raise SubmissionError(f"archive holds {members}, expected [{ARCHIVE_MEMBER!r}]")

    manifest = {
        "predictions": str(args.predictions),
        "predictions_sha256": _sha256(args.predictions),
        "input": str(args.input),
        "input_sha256": _sha256(args.input),
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "archive_members": members,
        **summary,
    }
    archive.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"predictions path   : {args.predictions}")
    print(f"row count          : {summary['rows']}")
    print(f"predictions sha256 : {manifest['predictions_sha256']}")
    print(f"zip path           : {archive}")
    print(f"zip sha256         : {manifest['archive_sha256']}")
    print(f"archive members    : {members}")
    print(f"rows with objects  : {summary['rows_with_objects']} "
          f"(empty: {summary['empty_rows']})")
    print(f"relations          : {summary['relations']}")
    print(f"\nupload this file: {archive}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SubmissionError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        sys.exit(2)
