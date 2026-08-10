"""Deterministic bootstrap policy for TRAIN calibration collection.

Module 21 needs historical bins to choose actions, and historical bins need
observed action outcomes - so something has to choose actions before a
calibrated planner exists. This is that something, and it is deliberately not a
planner: it ranks nothing, estimates nothing and consults no utility. It walks
the catalogue its owners published and executes a bounded, reproducible slice.

Two properties matter more than cleverness here:

**Family coverage.** Module 21 will later have to estimate the value of every
action family. A family the collection never executed has no support, and a bin
with no support cannot be calibrated - so the policy chases explicit run-wide
coverage deficits before falling back to the old deterministic catalogue walk.
One observation is not enough when TRAIN keeps supplying opportunities.

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
COLLECTION_POLICY_VERSION = "collect-v2-coverage-r2"

#: How many instances of one family the policy takes from a single query.
#: Bounded because §16's budget accounting must stay meaningful: collection is
#: a survey of the action space, not an exhaustive sweep of it.
DEFAULT_PER_FAMILY_LIMIT = 2

#: Successful observations sought for every family whose TRAIN opportunities
#: are common enough to support that many observations. Rare families instead
#: have target ``legal_opportunities_seen`` and must be exhausted.
DEFAULT_FAMILY_TARGET = 10

BLOCKED_UNAFFORDABLE = "blocked_unaffordable"
BLOCKED_EXECUTION_PRECONDITION = "blocked_execution_precondition"
BLOCKED_HISTORY = "blocked_history"
BLOCKED_ROUND_LIMIT = "blocked_round_limit"
BLOCKED_MISSING_PRIMARY = "blocked_missing_primary"
BLOCKED_OTHER = "blocked_other"

BLOCKED_COUNTER_FIELDS = {
    BLOCKED_UNAFFORDABLE: "blocked_unaffordable",
    BLOCKED_EXECUTION_PRECONDITION: "blocked_execution_precondition",
    BLOCKED_HISTORY: "blocked_history",
    BLOCKED_ROUND_LIMIT: "blocked_round_limit",
    BLOCKED_MISSING_PRIMARY: "blocked_missing_primary",
    BLOCKED_OTHER: "blocked_other",
}


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


def relation_of(action: Any) -> str:
    """Relation name published on an action, if the catalogue carries one."""
    return str(getattr(action, "relation", "") or "")


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

    The status is target-aware. One observation is not sufficient when TRAIN
    keeps supplying opportunities, and a rare family is not penalised for
    having fewer than the configured target when every available opportunity was
    successfully observed.
    """

    #: TRAIN supplied at least the configured target and the run met it.
    OBSERVED_SUFFICIENT = "OBSERVED_SUFFICIENT"
    #: TRAIN supplied fewer than the configured target, and every legal
    #: opportunity seen so far was successfully observed.
    OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES = (
        "OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES"
    )
    #: TRAIN supplied legal opportunities, but the successful observations do
    #: not meet the target semantics above.
    LEGAL_BUT_UNDERCOVERED = "LEGAL_BUT_UNDERCOVERED"
    #: The family is in the required vocabulary, but no legal TRAIN
    #: opportunity has been observed.
    NEVER_LEGAL = "NEVER_LEGAL"


@dataclass
class FamilyCoverage:
    """Legal opportunities versus observed outcomes for one action family."""

    family: str
    relation: str = ""
    legal_opportunities: int = 0
    selectable_opportunities: int = 0
    executed: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked_unaffordable: int = 0
    blocked_execution_precondition: int = 0
    blocked_history: int = 0
    blocked_round_limit: int = 0
    blocked_missing_primary: int = 0
    blocked_other: int = 0
    configured_target: int = DEFAULT_FAMILY_TARGET
    #: True when the collection declared this family up front as part of the
    #: vocabulary the report must account for.
    required: bool = False
    #: True once any catalogue offered this family, even zero times legal.
    surfaced: bool = False

    def __post_init__(self) -> None:
        if self.configured_target < 1:
            raise CollectionPolicyError(
                f"configured_target must be at least 1 for {self.family}, got "
                f"{self.configured_target}"
            )

    @property
    def target(self) -> int:
        return min(self.configured_target, self.legal_opportunities)

    @property
    def coverage_ratio(self) -> float:
        if self.target <= 0:
            return 0.0
        return min(1.0, self.succeeded / self.target)

    @property
    def coverage_deficit(self) -> int:
        return max(0, self.target - self.succeeded)

    @property
    def status(self) -> FamilyStatus:
        if self.legal_opportunities <= 0:
            return FamilyStatus.NEVER_LEGAL
        if self.legal_opportunities < self.configured_target:
            if self.succeeded >= self.legal_opportunities and not self.failed:
                return FamilyStatus.OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES
            return FamilyStatus.LEGAL_BUT_UNDERCOVERED
        if self.succeeded >= self.target:
            return FamilyStatus.OBSERVED_SUFFICIENT
        return FamilyStatus.LEGAL_BUT_UNDERCOVERED

    @property
    def unobserved(self) -> bool:
        """Legal in TRAIN, yet target coverage is missing."""
        return self.status is FamilyStatus.LEGAL_BUT_UNDERCOVERED

    def to_json(self) -> dict[str, Any]:
        return {
            "action_family": self.family,
            "relation": self.relation,
            "legal_opportunities": self.legal_opportunities,
            "selectable_opportunities": self.selectable_opportunities,
            "executed": self.executed,
            "successful": self.succeeded,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "blocked_unaffordable": self.blocked_unaffordable,
            "blocked_execution_precondition":
                self.blocked_execution_precondition,
            "blocked_history": self.blocked_history,
            "blocked_round_limit": self.blocked_round_limit,
            "blocked_missing_primary": self.blocked_missing_primary,
            "blocked_other": self.blocked_other,
            "target": self.target,
            "configured_target": self.configured_target,
            "coverage_ratio": self.coverage_ratio,
            "required": self.required,
            "surfaced": self.surfaced,
            "status": self.status.value,
            "unobserved": self.unobserved,
        }


@dataclass
class CoverageLedger:
    """Run-wide coverage, and the integrity verdict derived from it."""

    families: dict[str, FamilyCoverage] = field(default_factory=dict)
    relation_families: dict[str, dict[str, FamilyCoverage]] = field(
        default_factory=dict
    )
    configured_target: int = DEFAULT_FAMILY_TARGET

    def __post_init__(self) -> None:
        if self.configured_target < 1:
            raise CollectionPolicyError(
                f"configured_target must be at least 1, got "
                f"{self.configured_target}"
            )

    def _slot(self, family: str) -> FamilyCoverage:
        return self.families.setdefault(
            family,
            FamilyCoverage(family, configured_target=self.configured_target),
        )

    def _relation_slot(self, relation: str, family: str) -> FamilyCoverage:
        relation_key = str(relation or "")
        family_key = str(family)
        by_family = self.relation_families.setdefault(relation_key, {})
        return by_family.setdefault(
            family_key,
            FamilyCoverage(
                family_key,
                relation=relation_key,
                configured_target=self.configured_target,
            ),
        )

    def note_surfaced(self, family: str) -> None:
        """A catalogue offered this family, whatever it then did with it."""
        self._slot(family).surfaced = True

    def note_legal(self, family: str, count: int = 1, *,
                   relation: str = "") -> None:
        slot = self._slot(family)
        slot.surfaced = True
        slot.legal_opportunities += count
        if relation:
            relation_slot = self._relation_slot(relation, family)
            relation_slot.surfaced = True
            relation_slot.legal_opportunities += count

    def note_selectable(self, family: str, count: int = 1, *,
                        relation: str = "") -> None:
        slot = self._slot(family)
        slot.surfaced = True
        slot.selectable_opportunities += count
        if relation:
            relation_slot = self._relation_slot(relation, family)
            relation_slot.surfaced = True
            relation_slot.selectable_opportunities += count

    def note_blocked(
        self, family: str, reason: str, count: int = 1, *, relation: str = "",
    ) -> None:
        reason_key = str(reason).split(":", 1)[0]
        field_name = BLOCKED_COUNTER_FIELDS.get(reason_key, "blocked_other")
        slot = self._slot(family)
        slot.surfaced = True
        setattr(slot, field_name, getattr(slot, field_name) + count)
        if relation:
            relation_slot = self._relation_slot(relation, family)
            relation_slot.surfaced = True
            setattr(
                relation_slot, field_name,
                getattr(relation_slot, field_name) + count,
            )

    def note_executed(
        self, family: str, *, succeeded: bool, relation: str = "",
    ) -> None:
        slot = self._slot(family)
        slot.surfaced = True
        slot.executed += 1
        if succeeded:
            slot.succeeded += 1
        else:
            slot.failed += 1
        if relation:
            relation_slot = self._relation_slot(relation, family)
            relation_slot.surfaced = True
            relation_slot.executed += 1
            if succeeded:
                relation_slot.succeeded += 1
            else:
                relation_slot.failed += 1

    def _with_status(self, status: FamilyStatus) -> tuple[str, ...]:
        return tuple(sorted(
            name for name, entry in self.families.items()
            if entry.status is status))

    @property
    def unobserved_families(self) -> tuple[str, ...]:
        return self._with_status(FamilyStatus.LEGAL_BUT_UNDERCOVERED)

    @property
    def families_absent_from_train(self) -> tuple[str, ...]:
        """Declared families TRAIN never made legal. A dataset fact, not a bug."""
        return self._with_status(FamilyStatus.NEVER_LEGAL)

    @property
    def never_surfaced_families(self) -> tuple[str, ...]:
        """Required families no catalogue ever offered."""
        return tuple(sorted(
            name for name, entry in self.families.items()
            if entry.required and not entry.surfaced))

    def integrity_ok(self) -> bool:
        """Target undercoverage is a hard failure."""
        return not self.unobserved_families

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CoverageLedger":
        """Rebuild a committed ledger, so a resumed run continues its counts.

        Without this a resumed process starts an empty ledger and the final
        coverage table describes only the rows that ran after the restart -
        under-reporting exactly the support the offline derivation checks.
        """
        target = int(payload.get("configured_target", DEFAULT_FAMILY_TARGET))
        ledger = cls(configured_target=target)
        for entry in payload.get("families", ()):
            family = str(entry.get("action_family", ""))
            if not family:
                continue
            ledger.families[family] = FamilyCoverage(
                family=family,
                relation=str(entry.get("relation", "")),
                legal_opportunities=int(entry.get("legal_opportunities", 0)),
                selectable_opportunities=int(
                    entry.get("selectable_opportunities", 0)
                ),
                executed=int(entry.get("executed", 0)),
                succeeded=int(
                    entry.get("succeeded", entry.get("successful", 0))
                ),
                failed=int(entry.get("failed", 0)),
                blocked_unaffordable=int(
                    entry.get("blocked_unaffordable", 0)
                ),
                blocked_execution_precondition=int(
                    entry.get("blocked_execution_precondition", 0)
                ),
                blocked_history=int(entry.get("blocked_history", 0)),
                blocked_round_limit=int(entry.get("blocked_round_limit", 0)),
                blocked_missing_primary=int(
                    entry.get("blocked_missing_primary", 0)
                ),
                blocked_other=int(entry.get("blocked_other", 0)),
                configured_target=int(
                    entry.get("configured_target", ledger.configured_target)
                ),
                required=bool(entry.get("required", False)),
                surfaced=bool(entry.get("surfaced", False)),
            )
        for entry in payload.get("relation_families", ()):
            relation = str(entry.get("relation", ""))
            family = str(entry.get("action_family", ""))
            if not relation or not family:
                continue
            ledger.relation_families.setdefault(relation, {})[family] = (
                FamilyCoverage(
                    family=family,
                    relation=relation,
                    legal_opportunities=int(
                        entry.get("legal_opportunities", 0)
                    ),
                    selectable_opportunities=int(
                        entry.get("selectable_opportunities", 0)
                    ),
                    executed=int(entry.get("executed", 0)),
                    succeeded=int(
                        entry.get("succeeded", entry.get("successful", 0))
                    ),
                    failed=int(entry.get("failed", 0)),
                    blocked_unaffordable=int(
                        entry.get("blocked_unaffordable", 0)
                    ),
                    blocked_execution_precondition=int(
                        entry.get("blocked_execution_precondition", 0)
                    ),
                    blocked_history=int(entry.get("blocked_history", 0)),
                    blocked_round_limit=int(
                        entry.get("blocked_round_limit", 0)
                    ),
                    blocked_missing_primary=int(
                        entry.get("blocked_missing_primary", 0)
                    ),
                    blocked_other=int(entry.get("blocked_other", 0)),
                    configured_target=int(
                        entry.get("configured_target", ledger.configured_target)
                    ),
                    required=bool(entry.get("required", False)),
                    surfaced=bool(entry.get("surfaced", False)),
                )
            )
        return ledger

    def table(self) -> str:
        header = (
            f"{'action family':<26}{'legal':>7}{'select':>8}"
            f"{'executed':>9}{'ok':>5}{'failed':>8}{'target':>8}"
            f"{'ratio':>8}  {'status':<44}"
        )
        lines = [header, "-" * len(header)]
        for family in sorted(self.families):
            c = self.families[family]
            lines.append(
                f"{family:<26}{c.legal_opportunities:>7}"
                f"{c.selectable_opportunities:>8}{c.executed:>9}"
                f"{c.succeeded:>5}{c.failed:>8}{c.target:>8}"
                f"{c.coverage_ratio:>8.3f}  {c.status.value:<44}")
        return "\n".join(lines)

    def relation_table(self) -> str:
        header = (
            f"{'relation':<34}{'action family':<26}{'legal':>7}"
            f"{'select':>8}{'executed':>9}{'ok':>5}{'failed':>8}"
            f"{'target':>8}{'ratio':>8}  {'status':<44}"
        )
        lines = [header, "-" * len(header)]
        for relation in sorted(self.relation_families):
            for family in sorted(self.relation_families[relation]):
                c = self.relation_families[relation][family]
                lines.append(
                    f"{relation:<34}{family:<26}{c.legal_opportunities:>7}"
                    f"{c.selectable_opportunities:>8}{c.executed:>9}"
                    f"{c.succeeded:>5}{c.failed:>8}{c.target:>8}"
                    f"{c.coverage_ratio:>8.3f}  "
                    f"{c.status.value:<44}"
                )
        return "\n".join(lines)

    def csv_rows(self) -> list[dict[str, Any]]:
        rows = [self.families[family].to_json() for family in sorted(self.families)]
        relation_rows = [
            self.relation_families[relation][family].to_json()
            for relation in sorted(self.relation_families)
            for family in sorted(self.relation_families[relation])
        ]
        return rows + relation_rows

    def to_json(self) -> dict[str, Any]:
        return {
            "policy_version": COLLECTION_POLICY_VERSION,
            "configured_target": self.configured_target,
            "families": [self.families[f].to_json() for f in sorted(self.families)],
            "relation_families": [
                self.relation_families[relation][family].to_json()
                for relation in sorted(self.relation_families)
                for family in sorted(self.relation_families[relation])
            ],
            "unobserved_families": list(self.unobserved_families),
            "families_absent_from_train": list(self.families_absent_from_train),
            "never_surfaced_families": list(self.never_surfaced_families),
            "integrity_ok": self.integrity_ok(),
        }


class TrainCollectionPolicy:
    """Chooses which legal catalogue entries to execute. Ranks nothing."""

    def __init__(
        self, *, per_family_limit: int = DEFAULT_PER_FAMILY_LIMIT,
        family_target: int = DEFAULT_FAMILY_TARGET,
    ) -> None:
        if per_family_limit < 1:
            raise CollectionPolicyError(
                f"per_family_limit must be at least 1, got {per_family_limit}; "
                "a limit of zero would collect no outcomes at all"
            )
        if family_target < 1:
            raise CollectionPolicyError(
                f"family_target must be at least 1, got {family_target}"
            )
        self.per_family_limit = per_family_limit
        self.family_target = family_target
        self.version = COLLECTION_POLICY_VERSION
        self.coverage = CoverageLedger(configured_target=family_target)
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
        relation_key: "Callable[[Any], str]" = relation_of,
        selectable: Sequence[Any] | None = None,
        block_reasons: Mapping[int, str] | None = None,
        count_legal: bool = True,
    ) -> tuple[Any, ...]:
        """Pick a bounded, deterministic, coverage-deficit-aware subset.

        Every entry is recorded as a legal opportunity - including entries not
        selected, because a family that was legal and skipped is exactly what
        the coverage gate must be able to see.

        ``family_key`` lets a caller supply the *canonical* ``ActionFamily`` an
        owner's Layer-6 adapter assigns, rather than the raw attribute this
        module can guess at. The coverage ledger and Module 21's bins must key
        on the same vocabulary or the support the bins claim is not the support
        the ledger measured.
        ``selectable`` is the subset the caller has also proven executable and
        physically affordable in the current state. Legal opportunities are
        still counted from ``catalogue``; selection may only choose from
        ``selectable``.
        """
        by_family: dict[str, list[tuple[str, int, Any]]] = {}
        selectable_ids = (
            {id(action) for action in selectable}
            if selectable is not None else {id(action) for action in catalogue}
        )
        reasons = dict(block_reasons or {})
        for index, action in enumerate(catalogue):
            family = family_key(action)
            relation = relation_key(action)
            if count_legal:
                self.coverage.note_legal(family, relation=relation)
            if id(action) in selectable_ids:
                self.coverage.note_selectable(family, relation=relation)
                by_family.setdefault(family, []).append(
                    (_identity(action, index), index, action)
                )
            else:
                reason = reasons.get(id(action), BLOCKED_OTHER)
                self.coverage.note_blocked(family, reason, relation=relation)

        selected: list[tuple[str, int, Any]] = []
        # Families with positive target deficit go first. Once every currently
        # selectable family is sufficiently covered, the old deterministic
        # round-robin order resumes for the remaining opportunities.
        order = sorted(by_family, key=self._family_priority)
        for family in order:
            already = self._query_selected.get(family, 0)
            remaining = max(0, self.per_family_limit - already)
            if remaining <= 0:
                continue
            entries = sorted(by_family[family], key=lambda item: (item[0], item[1]))
            selected.extend(entries[:remaining])
        if selected:
            head = family_key(selected[0][2])
            self._query_selected[head] = self._query_selected.get(head, 0) + 1
        return tuple(action for _, _, action in selected)

    def _family_priority(self, family: str) -> tuple[object, ...]:
        coverage = self.coverage._slot(family)
        deficit = coverage.coverage_deficit
        if deficit > 0:
            return (
                0,
                coverage.coverage_ratio,
                coverage.succeeded,
                self._query_selected.get(family, 0),
                -deficit,
                family,
            )
        return (
            1,
            self._query_selected.get(family, 0),
            coverage.succeeded,
            family,
        )

    def record_outcome(self, action: Any, *, succeeded: bool) -> None:
        self.coverage.note_executed(
            family_of(action), succeeded=succeeded, relation=relation_of(action))

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
            slot.configured_target = self.family_target


__all__ = [
    "COLLECTION_POLICY_VERSION",
    "BLOCKED_COUNTER_FIELDS",
    "BLOCKED_EXECUTION_PRECONDITION",
    "BLOCKED_HISTORY",
    "BLOCKED_MISSING_PRIMARY",
    "BLOCKED_OTHER",
    "BLOCKED_ROUND_LIMIT",
    "BLOCKED_UNAFFORDABLE",
    "DEFAULT_FAMILY_TARGET",
    "DEFAULT_PER_FAMILY_LIMIT",
    "CollectionPolicyError",
    "CoverageLedger",
    "FamilyCoverage",
    "FamilyStatus",
    "TrainCollectionPolicy",
    "family_of",
    "relation_of",
    "required_families",
]
