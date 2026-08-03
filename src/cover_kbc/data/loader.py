"""Read-only loaders for the official dataset splits.

Row order from the official file is always preserved.  No row is ever
rewritten, filtered, or repaired on load - a malformed file raises instead.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from cover_kbc.data.schema import DatasetRow, SchemaError, check_no_duplicate_keys, parse_row
from cover_kbc.paths import SPLIT_FILES
from cover_kbc.types import Query

#: ``test.jsonl`` is the blind split - it ships with empty ObjectEntities.
BLIND_SPLITS = frozenset({"test"})


@dataclass(frozen=True)
class Dataset:
    """An ordered, immutable view of one official split."""

    split: str
    path: Path
    rows: tuple[DatasetRow, ...]
    sha256: str

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[DatasetRow]:
        return iter(self.rows)

    @property
    def is_blind(self) -> bool:
        """True when no row carries gold objects, i.e. it cannot be scored here."""
        return all(row.is_empty for row in self.rows)

    def queries(self) -> list[Query]:
        """Input pairs in official file order."""
        return [Query(row.subject, row.relation, row.row_index) for row in self.rows]

    def relations(self) -> list[str]:
        return sorted({row.relation for row in self.rows})

    def filter_relation(self, relation: str) -> list[DatasetRow]:
        return [row for row in self.rows if row.relation == relation]

    def head(self, n: int) -> list[DatasetRow]:
        return list(self.rows[:n])

    def to_official_rows(self) -> list[dict]:
        """Exact upstream JSON rows, in the original order."""
        return [row.to_official_row() for row in self.rows]

    def relation_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.relation] = counts.get(row.relation, 0) + 1
        return dict(sorted(counts.items()))


def load_jsonl_rows(path: str | Path, *, split: str = "") -> list[DatasetRow]:
    """Parse and validate a JSONL file into ordered :class:`DatasetRow` objects."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Dataset file not found: {file_path}")

    rows: list[DatasetRow] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{file_path.name}:{index}: invalid JSON ({exc})") from exc
            rows.append(parse_row(raw, index=index, split=split, source=file_path.name))
    return rows


def load_dataset(split: str, *, path: str | Path | None = None) -> Dataset:
    """Load one official split (``train``, ``val`` or ``test``).

    Args:
        split: split name; also used to label rows.
        path: optional override, e.g. a small fixture file in tests.
    """
    if path is None:
        if split not in SPLIT_FILES:
            raise KeyError(f"Unknown split {split!r}; expected one of {sorted(SPLIT_FILES)}")
        file_path = SPLIT_FILES[split]
    else:
        file_path = Path(path)

    rows = load_jsonl_rows(file_path, split=split)
    check_no_duplicate_keys(rows, source=file_path.name)

    if split not in BLIND_SPLITS and rows and all(row.is_empty for row in rows):
        raise SchemaError(
            f"{file_path.name}: split {split!r} has no gold objects on any row. "
            "Only the blind test split is expected to look like this."
        )

    return Dataset(
        split=split,
        path=file_path,
        rows=tuple(rows),
        sha256=hashlib.sha256(file_path.read_bytes()).hexdigest(),
    )


def load_all_splits() -> dict[str, Dataset]:
    """Load ``train``, ``val`` and ``test`` in one call."""
    return {split: load_dataset(split) for split in SPLIT_FILES}


def gold_lookup(rows: Sequence[DatasetRow]) -> dict[tuple[str, str], DatasetRow]:
    """Index rows by ``(subject, relation)``.

    Intended for error analysis and threshold calibration only.  Using this on
    the inference path would turn the train split into a factual database,
    which the challenge rules forbid.
    """
    return {row.key: row for row in rows}
