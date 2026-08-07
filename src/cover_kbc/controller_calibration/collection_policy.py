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
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

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


def required_families(kinds: Iterable[str]) -> tuple[str, ...]:
    """The canonical ``ActionFamily`` vocabulary the given catalogues can surface.

    Read off Layer 6's own adapters rather than written out as strings here: the
    families a Module 17 or Module 18 catalogue can produce are the owners'
    declaration, and a hand-maintained list in a runner would drift the moment a
    mechanism was added.

    This is what turns Audit 0041's F-10 into a detectable condition. Without a
    declared vocabulary a family that was *never offered* simply never enters
    the ledger, and ``integrity_ok`` reports PASS for a collection that never
    surfaced it - which is indistinguishable from success and is not.
    """
    from cover_kbc.control.action_catalog import M17_FAMILIES, M18_FAMILIES

    published = {"m17": M17_FAMILIES, "m18": M18_FAMILIES}
    names: set[str] = set()
    for kind in kinds:
        try:
            families = published[kind]
        except KeyError:
            raise CollectionPolicyError(
                f"no action-family vocabulary is published for catalogue "
                f"{kind!r}; known: {sorted(published)}"
            ) from None
        names.update(family.value for family in families)
    return tuple(sorted(names))


class FamilyStatus(str, Enum):
    """What a run can honestly say about one action family.

    Audit 0041 F-10 found the third and fourth of these collapsing into the
    first: a family that was never *offered* simply never entered the ledger, so
    a collection that surfaced one family out of four printed PASS. They are
    kept apart here because they demand different responses - one is a dataset
    fact, the other is a bug to fix before spending an A100 session.
    """

    #: Offered and run. The only status that yields calibration support.
    OBSERVED = "OBSERVED"
    #: Offered by an owner, never selected. The policy under-explored.
    LEGAL_BUT_UNEXECUTED = "LEGAL_BUT_UNEXECUTED"
    #: Declared, and TRAIN never made it legal anywhere. A fact about TRAIN.
    ABSENT_FROM_TRAIN = "ABSENT_FROM_TRAIN"
    #: Declared as required, and never surfaced in any catalogue at all - the
    #: module that owns it never ran, or was never wired. **Never a PASS.**
    NEVER_SURFACED = "NEVER_SURFACED"


@dataclass
class FamilyCoverage:
    """Legal opportunities versus observed outcomes for one action family."""

    family: str
    legal_opportunities: int = 0
    executed: int = 0
    succeeded: int = 0
    failed: int = 0
    #: True when the collection declared this family up front as one it expects
    #: to be able to surface. A required family that never appears is a failure;
    #: an undeclared one that never appears is merely absent.
    required: bool = False
    #: True once any catalogue offered this family, even zero times legal.
    surfaced: bool = False

    @property
    def status(self) -> FamilyStatus:
        if self.executed:
            return FamilyStatus.OBSERVED
        if self.legal_opportunities > 0:
            return FamilyStatus.LEGAL_BUT_UNEXECUTED
        if self.surfaced:
            return FamilyStatus.ABSENT_FROM_TRAIN
        return FamilyStatus.NEVER_SURFACED

    @property
    def unobserved(self) -> bool:
        """Legal somewhere in TRAIN, yet never executed - an integrity failure."""
        return self.status is FamilyStatus.LEGAL_BUT_UNEXECUTED

    def to_json(self) -> dict[str, Any]:
        return {
            "action_family": self.family,
            "legal_opportunities": self.legal_opportunities,
            "executed": self.executed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "required": self.required,
            "surfaced": self.surfaced,
            "status": self.status.value,
            "unobserved": self.unobserved,
        }


@dataclass
class CoverageLedger:
    """Run-wide coverage, and the integrity verdict derived from it."""

    families: dict[str, FamilyCoverage] = field(default_factory=dict)

    def _slot(self, family: str) -> FamilyCoverage:
        return self.families.setdefault(family, FamilyCoverage(family))

    def note_surfaced(self, family: str) -> None:
        """A catalogue offered this family, whatever it then did with it."""
        self._slot(family).surfaced = True

    def note_legal(self, family: str, count: int = 1) -> None:
        slot = self._slot(family)
        slot.surfaced = True
        slot.legal_opportunities += count

    def note_executed(self, family: str, *, succeeded: bool) -> None:
        slot = self._slot(family)
        slot.surfaced = True
        slot.executed += 1
        if succeeded:
            slot.succeeded += 1
        else:
            slot.failed += 1

    def _with_status(self, status: FamilyStatus) -> tuple[str, ...]:
        return tuple(sorted(
            name for name, entry in self.families.items()
            if entry.status is status))

    @property
    def unobserved_families(self) -> tuple[str, ...]:
        return self._with_status(FamilyStatus.LEGAL_BUT_UNEXECUTED)

    @property
    def families_absent_from_train(self) -> tuple[str, ...]:
        """Declared families TRAIN never made legal. A dataset fact, not a bug."""
        return self._with_status(FamilyStatus.ABSENT_FROM_TRAIN)

    @property
    def never_surfaced_families(self) -> tuple[str, ...]:
        """Required families no catalogue ever offered. Always a failure."""
        return tuple(sorted(
            name for name, entry in self.families.items()
            if entry.required and entry.status is FamilyStatus.NEVER_SURFACED))

    def integrity_ok(self) -> bool:
        """Both failure modes, never only the one that happens to be visible."""
        return not (self.unobserved_families or self.never_surfaced_families)

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
                required=bool(entry.get("required", False)),
                surfaced=bool(entry.get("surfaced", False)),
            )
        return ledger

    def table(self) -> str:
        header = (f"{'action family':<26}{'legal':>7}{'executed':>9}{'ok':>5}"
                  f"{'failed':>8}  {'status':<22}")
        lines = [header, "-" * len(header)]
        for family in sorted(self.families):
            c = self.families[family]
            lines.append(
                f"{family:<26}{c.legal_opportunities:>7}{c.executed:>9}"
                f"{c.succeeded:>5}{c.failed:>8}  {c.status.value:<22}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "families": [self.families[f].to_json() for f in sorted(self.families)],
            "unobserved_families": list(self.unobserved_families),
            "families_absent_from_train": list(self.families_absent_from_train),
            "never_surfaced_families": list(self.never_surfaced_families),
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
        #: How many times each family has already been taken *for the query
        #: being processed*. The controller executes one action per round and
        #: re-asks with a fresh catalogue, so without this the round-robin
        #: restarts every round and a family with many entries wins every time -
        #: which is how a legal family ends a run never executed.
        self._query_selected: dict[str, int] = {}

    def begin_query(self) -> None:
        """Start a new query. Resets the per-query round-robin position only."""
        self._query_selected = {}

    def select(
        self, catalogue: Sequence[Any],
        *, family_key: "Callable[[Any], str]" = family_of,
    ) -> tuple[Any, ...]:
        """Pick a bounded, deterministic, family-balanced subset.

        Every entry is recorded as a legal opportunity - including entries not
        selected, because a family that was legal and skipped is exactly what
        the coverage gate must be able to see.

        ``family_key`` lets a caller supply the *canonical* ``ActionFamily`` an
        owner's Layer-6 adapter assigns, rather than the raw attribute this
        module can guess at. The coverage ledger and Module 21's bins must key
        on the same vocabulary or the support the bins claim is not the support
        the ledger measured.
        """
        by_family: dict[str, list[tuple[str, int, Any]]] = {}
        for index, action in enumerate(catalogue):
            family = family_key(action)
            self.coverage.note_legal(family)
            by_family.setdefault(family, []).append((_identity(action, index), index, action))

        selected: list[tuple[str, int, Any]] = []
        # Round-robin across families so a long family cannot crowd out a short
        # one. Families already taken for this query go last, so the position
        # survives the controller re-asking with a fresh catalogue each round.
        # Within a family, order by published identity, and break ties on family
        # name, so the same TRAIN row always yields the same selection.
        order = sorted(
            by_family, key=lambda family: (self._query_selected.get(family, 0), family))
        for rank in range(self.per_family_limit):
            for family in order:
                entries = sorted(by_family[family], key=lambda item: (item[0], item[1]))
                if rank < len(entries):
                    selected.append(entries[rank])
        # The round-robin order is *kept*, not re-sorted into catalogue order.
        # The controller consumes the head of this tuple and re-asks, so this
        # order is what decides which family each round gets; sorting by
        # catalogue position undid the balancing one line after achieving it.
        if selected:
            head = family_key(selected[0][2])
            self._query_selected[head] = self._query_selected.get(head, 0) + 1
        return tuple(action for _, _, action in selected)

    def record_outcome(self, action: Any, *, succeeded: bool) -> None:
        self.coverage.note_executed(family_of(action), succeeded=succeeded)

    def note_families(self, families: Iterable[str], *, required: bool = True) -> None:
        """Declare the families this run expects to be able to surface.

        Called at run start, before any query. A family declared here and never
        surfaced fails the integrity gate; without the declaration it would
        simply be absent from the table and the run would report PASS - the
        Audit-0041 F-10 hole.
        """
        for family in families:
            slot = self.coverage._slot(str(family))
            slot.required = slot.required or required


__all__ = [
    "COLLECTION_POLICY_VERSION",
    "DEFAULT_PER_FAMILY_LIMIT",
    "CollectionPolicyError",
    "CoverageLedger",
    "FamilyCoverage",
    "FamilyStatus",
    "TrainCollectionPolicy",
    "family_of",
    "required_families",
]
