"""Deterministic bootstrap policy for TRAIN calibration collection.

Module 21 needs historical bins to choose actions, and historical bins need
observed action outcomes - so something has to choose actions before a
calibrated planner exists. This is that something, and it is deliberately not a
planner: it ranks nothing, estimates nothing and consults no utility. It walks
the catalogue its owners published and executes a bounded, reproducible slice.

Two properties matter more than cleverness here:

**Family coverage.** Module 21 will later have to estimate the value of every
action family. A family the collection never executed has no support, and a bin
with no support cannot be calibrated - so the policy takes from *every* legally
available family before taking a second instance of any one of them. A greedy
policy that spent its whole budget on the cheapest family would leave exactly
the verification families Table 6 hard-reserves budget for unestimated.

**Legality is not ours to decide.** The catalogue is the eligibility authority;
this policy only ever selects a subset of what its owners already declared
legal. It never constructs an action, never forces eligibility, and never
reorders a family into existence. A family with zero legal instances in TRAIN
is a fact about TRAIN, reported as such - not an implementation failure, and
the two must never be conflated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Bumped when selection behaviour changes. A resume across two different
#: policy versions would splice incomparable observations into one bin.
COLLECTION_POLICY_VERSION = "collect-v1"

#: How many instances of one family the policy takes from a single query.
#: Bounded because §16's budget accounting must stay meaningful: collection is
#: a survey of the action space, not an exhaustive sweep of it.
DEFAULT_PER_FAMILY_LIMIT = 2


class CollectionPolicyError(RuntimeError):
    """The collection policy was asked for something it must not do."""


def _identity(action: Any, index: int) -> str:
    """A stable, human-legible ordering key for one catalogue entry.

    Falls back through the identifiers the various catalogues publish, and
    finally to position, so ordering is total even for an entry that carries no
    identifier at all.
    """
    for attribute in ("action_id", "operation_id", "check_id", "target_id", "key"):
        value = getattr(action, attribute, "")
        if value:
            return str(value)
    for attribute in ("candidate_key", "display", "name"):
        value = getattr(action, attribute, "")
        if value:
            return f"{value}#{index}"
    return f"entry#{index}"


def family_of(action: Any) -> str:
    """The canonical action-family name an owner published for this entry."""
    for attribute in ("action_family", "family", "check_kind", "mechanism", "kind"):
        value = getattr(action, attribute, None)
        if value is None:
            continue
        return str(getattr(value, "value", value))
    return ""


@dataclass
class FamilyCoverage:
    """Legal opportunities versus observed outcomes for one action family."""

    family: str
    legal_opportunities: int = 0
    executed: int = 0
    succeeded: int = 0
    failed: int = 0

    @property
    def unobserved(self) -> bool:
        """Legal somewhere in TRAIN, yet never executed - an integrity failure."""
        return self.legal_opportunities > 0 and self.executed == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "action_family": self.family,
            "legal_opportunities": self.legal_opportunities,
            "executed": self.executed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "unobserved": self.unobserved,
        }


@dataclass
class CoverageLedger:
    """Run-wide coverage, and the integrity verdict derived from it."""

    families: dict[str, FamilyCoverage] = field(default_factory=dict)

    def _slot(self, family: str) -> FamilyCoverage:
        return self.families.setdefault(family, FamilyCoverage(family))

    def note_legal(self, family: str, count: int = 1) -> None:
        self._slot(family).legal_opportunities += count

    def note_executed(self, family: str, *, succeeded: bool) -> None:
        slot = self._slot(family)
        slot.executed += 1
        if succeeded:
            slot.succeeded += 1
        else:
            slot.failed += 1

    @property
    def unobserved_families(self) -> tuple[str, ...]:
        return tuple(sorted(f for f, c in self.families.items() if c.unobserved))

    @property
    def families_absent_from_train(self) -> tuple[str, ...]:
        """Declared families TRAIN never made legal. A dataset fact, not a bug."""
        return tuple(sorted(
            f for f, c in self.families.items() if c.legal_opportunities == 0))

    def integrity_ok(self) -> bool:
        return not self.unobserved_families

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CoverageLedger":
        """Rebuild a committed ledger, so a resumed run continues its counts.

        Without this a resumed process starts an empty ledger and the final
        coverage table describes only the rows that ran after the restart -
        under-reporting exactly the support the offline derivation checks.
        """
        ledger = cls()
        for entry in payload.get("families", ()):
            family = str(entry.get("action_family", ""))
            if not family:
                continue
            ledger.families[family] = FamilyCoverage(
                family=family,
                legal_opportunities=int(entry.get("legal_opportunities", 0)),
                executed=int(entry.get("executed", 0)),
                succeeded=int(entry.get("succeeded", 0)),
                failed=int(entry.get("failed", 0)),
            )
        return ledger

    def table(self) -> str:
        header = (f"{'action family':<34}{'legal':>8}{'executed':>10}"
                  f"{'ok':>8}{'failed':>8}")
        lines = [header, "-" * len(header)]
        for family in sorted(self.families):
            c = self.families[family]
            lines.append(f"{family:<34}{c.legal_opportunities:>8}{c.executed:>10}"
                         f"{c.succeeded:>8}{c.failed:>8}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "families": [self.families[f].to_json() for f in sorted(self.families)],
            "unobserved_families": list(self.unobserved_families),
            "families_absent_from_train": list(self.families_absent_from_train),
            "integrity_ok": self.integrity_ok(),
        }


class TrainCollectionPolicy:
    """Chooses which legal catalogue entries to execute. Ranks nothing."""

    def __init__(self, *, per_family_limit: int = DEFAULT_PER_FAMILY_LIMIT) -> None:
        if per_family_limit < 1:
            raise CollectionPolicyError(
                f"per_family_limit must be at least 1, got {per_family_limit}; "
                "a limit of zero would collect no outcomes at all"
            )
        self.per_family_limit = per_family_limit
        self.version = COLLECTION_POLICY_VERSION
        self.coverage = CoverageLedger()

    def select(self, catalogue: Sequence[Any]) -> tuple[Any, ...]:
        """Pick a bounded, deterministic, family-balanced subset.

        Every entry is recorded as a legal opportunity - including entries not
        selected, because a family that was legal and skipped is exactly what
        the coverage gate must be able to see.
        """
        by_family: dict[str, list[tuple[str, int, Any]]] = {}
        for index, action in enumerate(catalogue):
            family = family_of(action)
            self.coverage.note_legal(family)
            by_family.setdefault(family, []).append((_identity(action, index), index, action))

        selected: list[tuple[str, int, Any]] = []
        # Round-robin across families so a long family cannot crowd out a
        # short one; within a family, order by published identity so the same
        # TRAIN row always yields the same selection.
        for rank in range(self.per_family_limit):
            for family in sorted(by_family):
                entries = sorted(by_family[family], key=lambda item: (item[0], item[1]))
                if rank < len(entries):
                    selected.append(entries[rank])
        selected.sort(key=lambda item: item[1])
        return tuple(action for _, _, action in selected)

    def record_outcome(self, action: Any, *, succeeded: bool) -> None:
        self.coverage.note_executed(family_of(action), succeeded=succeeded)

    def note_families(self, families: Iterable[str]) -> None:
        """Declare families that must appear in the final coverage table."""
        for family in families:
            self.coverage._slot(str(family))


__all__ = [
    "COLLECTION_POLICY_VERSION",
    "DEFAULT_PER_FAMILY_LIMIT",
    "CollectionPolicyError",
    "CoverageLedger",
    "FamilyCoverage",
    "TrainCollectionPolicy",
    "family_of",
]
