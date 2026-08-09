"""Deterministic supplemental coverage support for V3 TRAIN collection.

The full TRAIN run is the base corpus. If it finishes with a small target
deficit, a supplemental run should revisit only TRAIN rows whose relation can
legally surface the missing family, then merge offline after both artifacts are
complete. This module plans and validates that workflow without reading gold and
without mutating the base collection in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.controller_calibration.collection_policy import CoverageLedger
from cover_kbc.types import Query
from cover_kbc.v3_core.relation_programs import relation_train_collection_actions


class SupplementalCoverageError(ValueError):
    """A supplemental coverage plan or merge would be unsafe."""


@dataclass(frozen=True)
class SupplementalFamilyPlan:
    action_family: str
    deficit: int
    candidate_relations: tuple[str, ...]
    row_indices: tuple[int, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "action_family": self.action_family,
            "deficit": self.deficit,
            "candidate_relations": list(self.candidate_relations),
            "row_indices": list(self.row_indices),
        }


@dataclass(frozen=True)
class SupplementalCoveragePlan:
    schema_version: str = "v3-supplemental-coverage-plan-v1"
    split: str = "train"
    families: tuple[SupplementalFamilyPlan, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def row_indices(self) -> tuple[int, ...]:
        rows = {
            row
            for family in self.families
            for row in family.row_indices
        }
        return tuple(sorted(rows))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "split": self.split,
            "families": [family.to_json() for family in self.families],
            "row_indices": list(self.row_indices),
            "notes": list(self.notes),
        }


def family_relations() -> dict[str, tuple[str, ...]]:
    """Relations whose V3 TRAIN catalogue may offer each family."""
    by_family: dict[str, list[str]] = {}
    from cover_kbc.contracts.registry import CONTRACTS

    for relation in sorted(CONTRACTS):
        for action in relation_train_collection_actions(relation):
            by_family.setdefault(action.value, []).append(relation)
    return {
        family: tuple(sorted(relations))
        for family, relations in sorted(by_family.items())
    }


def plan_supplemental_coverage(
    coverage: CoverageLedger,
    queries: Sequence[Query],
    *,
    max_rows_per_family: int | None = None,
) -> SupplementalCoveragePlan:
    """Choose deterministic TRAIN rows for remaining family deficits.

    The plan uses only row identity and relation. It does not inspect
    ``ObjectEntities`` and it does not assert that a row will definitely reach a
    particular failure state; the online run must still derive legality from
    the live catalogue and may execute only legal, affordable actions.
    """
    relations_by_family = family_relations()
    by_relation: dict[str, list[int]] = {}
    for query in queries:
        by_relation.setdefault(query.relation, []).append(query.row_index)

    family_plans: list[SupplementalFamilyPlan] = []
    notes: list[str] = []
    for family in coverage.unobserved_families:
        entry = coverage.families[family]
        deficit = entry.coverage_deficit
        if deficit <= 0:
            continue
        relations = relations_by_family.get(family, ())
        candidates = [
            row
            for relation in relations
            for row in by_relation.get(relation, ())
        ]
        candidates = sorted(set(candidates))
        limit = deficit if max_rows_per_family is None else min(
            deficit, max_rows_per_family)
        selected = tuple(candidates[:limit])
        if not selected:
            notes.append(
                f"{family}: no TRAIN row has a relation that can offer it")
        family_plans.append(SupplementalFamilyPlan(
            action_family=family,
            deficit=deficit,
            candidate_relations=relations,
            row_indices=selected,
        ))
    return SupplementalCoveragePlan(
        families=tuple(sorted(family_plans, key=lambda item: item.action_family)),
        notes=tuple(notes),
    )


def merge_coverage_ledgers(
    base: CoverageLedger, supplement: CoverageLedger,
) -> CoverageLedger:
    """Merge two completed coverage ledgers without mutating either input."""
    if base.configured_target != supplement.configured_target:
        raise SupplementalCoverageError(
            "coverage ledgers use different configured targets: "
            f"{base.configured_target} vs {supplement.configured_target}"
        )
    merged = CoverageLedger.from_json(base.to_json())
    for family, entry in supplement.families.items():
        slot = merged._slot(family)
        slot.legal_opportunities += entry.legal_opportunities
        slot.executed += entry.executed
        slot.succeeded += entry.succeeded
        slot.failed += entry.failed
        slot.required = slot.required or entry.required
        slot.surfaced = slot.surfaced or entry.surfaced
    for relation, by_family in supplement.relation_families.items():
        for family, entry in by_family.items():
            slot = merged._relation_slot(relation, family)
            slot.legal_opportunities += entry.legal_opportunities
            slot.executed += entry.executed
            slot.succeeded += entry.succeeded
            slot.failed += entry.failed
            slot.required = slot.required or entry.required
            slot.surfaced = slot.surfaced or entry.surfaced
    return merged


def validate_no_duplicate_action_effect_ids(
    base_effects: Iterable[Mapping[str, Any]],
    supplement_effects: Iterable[Mapping[str, Any]],
) -> None:
    """Fail before an offline merge would duplicate action-effect identities."""
    seen: set[tuple[int, str]] = set()
    for source, effects in (("base", base_effects), ("supplement", supplement_effects)):
        for effect in effects:
            action = dict(effect.get("action") or {})
            identity = (
                int(action.get("row_index", -1)),
                str(action.get("action_id", "")),
            )
            if not identity[1]:
                raise SupplementalCoverageError(
                    f"{source} action effect has no action.action_id")
            if identity in seen:
                raise SupplementalCoverageError(
                    f"duplicate action-effect identity {identity}")
            seen.add(identity)


__all__ = [
    "SupplementalCoverageError",
    "SupplementalCoveragePlan",
    "SupplementalFamilyPlan",
    "family_relations",
    "merge_coverage_ledgers",
    "plan_supplemental_coverage",
    "validate_no_duplicate_action_effect_ids",
]
