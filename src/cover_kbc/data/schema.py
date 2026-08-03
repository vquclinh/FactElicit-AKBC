"""Schema validation for official dataset rows and prediction rows.

The official format is deliberately re-stated here as *validation*, never as a
transformation: loading a row must not change it.

Ground truth (``train`` / ``val``)::

    {"SubjectEntity": str, "Relation": str, "ObjectEntities": [[alias, ...], ...]}

``test.jsonl`` ships with ``ObjectEntities: []`` on every row - it is the blind
split, so it cannot be scored locally.

Predictions (what we submit)::

    {"SubjectEntity": str, "Relation": str, "ObjectEntities": [str, ...]}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

OFFICIAL_FIELDS = ("SubjectEntity", "Relation", "ObjectEntities")


class SchemaError(ValueError):
    """Raised when a row does not match the official schema."""


@dataclass(frozen=True)
class DatasetRow:
    """One immutable official row.

    ``object_entities`` is always the alias-list form ``list[list[str]]``; a
    legacy flat ``list[str]`` row is wrapped on read (mirroring the evaluator's
    own back-compat branch) and flagged via :attr:`legacy_flat_gold`.
    """

    subject: str
    relation: str
    object_entities: tuple[tuple[str, ...], ...]
    row_index: int
    split: str = ""
    legacy_flat_gold: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.relation)

    @property
    def num_objects(self) -> int:
        return len(self.object_entities)

    @property
    def is_empty(self) -> bool:
        return not self.object_entities

    def preferred_surface_forms(self) -> list[str]:
        """First alias of each gold entity - the canonical label upstream uses."""
        return [aliases[0] for aliases in self.object_entities if aliases]

    def to_official_row(self) -> dict[str, Any]:
        """Round-trip back to the exact upstream JSON shape."""
        if self.legacy_flat_gold:
            objects: Any = [aliases[0] for aliases in self.object_entities]
        else:
            objects = [list(aliases) for aliases in self.object_entities]
        return {
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "ObjectEntities": objects,
        }


def validate_raw_row(raw: Mapping[str, Any], *, index: int, source: str) -> None:
    """Validate a raw ground-truth row, raising :class:`SchemaError` on failure."""
    where = f"{source}:{index}"
    missing = [f for f in OFFICIAL_FIELDS if f not in raw]
    if missing:
        raise SchemaError(f"{where}: missing required field(s) {missing}")

    if not isinstance(raw["SubjectEntity"], str) or not raw["SubjectEntity"].strip():
        raise SchemaError(f"{where}: 'SubjectEntity' must be a non-empty string")
    if not isinstance(raw["Relation"], str) or not raw["Relation"].strip():
        raise SchemaError(f"{where}: 'Relation' must be a non-empty string")

    objects = raw["ObjectEntities"]
    if not isinstance(objects, list):
        raise SchemaError(f"{where}: 'ObjectEntities' must be a list, got {type(objects).__name__}")

    kinds = {type(o) is list for o in objects}
    if len(kinds) > 1:
        raise SchemaError(f"{where}: 'ObjectEntities' mixes alias lists and bare strings")

    for position, entry in enumerate(objects):
        if isinstance(entry, list):
            if not entry:
                raise SchemaError(f"{where}: alias list #{position} is empty")
            if not all(isinstance(a, str) for a in entry):
                raise SchemaError(f"{where}: alias list #{position} contains a non-string")
        elif not isinstance(entry, str):
            raise SchemaError(
                f"{where}: 'ObjectEntities[{position}]' must be a string or alias list, "
                f"got {type(entry).__name__}"
            )


def parse_row(raw: Mapping[str, Any], *, index: int, split: str, source: str) -> DatasetRow:
    """Validate and convert one raw row into a :class:`DatasetRow`."""
    validate_raw_row(raw, index=index, source=source)
    objects = raw["ObjectEntities"]
    legacy = bool(objects) and isinstance(objects[0], str)
    if legacy:
        aliases = tuple((str(o),) for o in objects)
    else:
        aliases = tuple(tuple(str(a) for a in entry) for entry in objects)
    return DatasetRow(
        subject=raw["SubjectEntity"],
        relation=raw["Relation"],
        object_entities=aliases,
        row_index=index,
        split=split,
        legacy_flat_gold=legacy,
    )


def validate_prediction_row(raw: Mapping[str, Any], *, index: int) -> None:
    """Validate one prediction row against the official submission format."""
    where = f"prediction:{index}"
    missing = [f for f in OFFICIAL_FIELDS if f not in raw]
    if missing:
        raise SchemaError(f"{where}: missing required field(s) {missing}")
    extra = [f for f in raw if f not in OFFICIAL_FIELDS]
    if extra:
        raise SchemaError(f"{where}: unexpected field(s) {extra}; submissions carry exactly {list(OFFICIAL_FIELDS)}")
    if not isinstance(raw["SubjectEntity"], str) or not isinstance(raw["Relation"], str):
        raise SchemaError(f"{where}: 'SubjectEntity' and 'Relation' must be strings")
    objects = raw["ObjectEntities"]
    if not isinstance(objects, list):
        raise SchemaError(f"{where}: 'ObjectEntities' must be a list")
    for position, entry in enumerate(objects):
        if not isinstance(entry, str):
            raise SchemaError(
                f"{where}: predictions must be flat strings; "
                f"'ObjectEntities[{position}]' is {type(entry).__name__}"
            )


def check_no_duplicate_keys(rows: Sequence[DatasetRow], *, source: str) -> None:
    """Reject a file containing two rows for the same ``(subject, relation)``.

    The official evaluator keys by that pair, so a duplicate silently discards
    one of the rows.
    """
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        if row.key in seen:
            raise SchemaError(
                f"{source}: duplicate (SubjectEntity, Relation) {row.key} "
                f"at rows {seen[row.key]} and {row.row_index}"
            )
        seen[row.key] = row.row_index
