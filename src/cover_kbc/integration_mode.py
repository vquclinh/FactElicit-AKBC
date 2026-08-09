"""Canonical execution-mode semantics for the upgraded (M11-M21) modules.

Every upgraded module was built shadow-only while the architecture was being
audited: it executed real work, recorded a typed result, and was *structurally
incapable* of changing what Module 8 emitted. That restriction is what made the
M16-only / M16+M17 / M16+M17+M18 ablations honest, and Audits 0033/0034 depend
on it. It is not something to delete.

This module replaces the twelve independent ``if mode != "shadow": raise``
checks with one contract that names both legal modes and says precisely what
each one permits:

``SHADOW``
    The module runs, spends real calls, and publishes a typed result object.
    It may not mutate the production evidence graph, the production budget, or
    anything Module 8 reads. Byte-identical predictions are an invariant, and
    the shadow-invariance tests assert exactly that.

``PRODUCTION``
    The module runs and its evidence may enter the canonical production state
    **through an explicit typed integration** - never by a module reaching into
    Module 3's graph on its own. Its calls are charged to the production budget
    rather than the shadow counters.

The distinction that matters is not "does it run" - shadow modules genuinely
run - but "may its output reach Module 8". Nothing here grants that reach; it
only names the permission that a typed integration layer must check before
applying an overlay.

``mode`` is deliberately not a boolean. A third state ("enabled but somehow
neither") is the bug this enum exists to make unrepresentable, and an unknown
string fails closed rather than defaulting to the permissive option.
"""

from __future__ import annotations

from enum import Enum


class IntegrationModeError(ValueError):
    """A module was configured with a mode it cannot honour."""


class IntegrationMode(str, Enum):
    """How far an upgraded module's output is allowed to travel."""

    SHADOW = "shadow"
    PRODUCTION = "production"
    #: Runs the real production seams so that TRAIN observes the same action
    #: families VALIDATION will, but exists solely to collect the outcomes
    #: Modules 20 and 21 are calibrated from. It is not a planner: action
    #: choice comes from a fixed deterministic policy, never from utility.
    TRAIN_CALIBRATION_COLLECTION_ONLY = "train_calibration_collection_only"

    @property
    def is_production(self) -> bool:
        return self is IntegrationMode.PRODUCTION

    @property
    def is_shadow(self) -> bool:
        return self is IntegrationMode.SHADOW

    @property
    def is_collection(self) -> bool:
        return self is IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY

    @property
    def train_split_only(self) -> bool:
        """Whether this mode may only ever be pointed at TRAIN.

        Collection executes actions chosen by a fixed policy rather than by
        calibrated utility. Pointing it at VALIDATION would produce a
        leaderboard number for a system that does not exist, and pointing it at
        TEST would spend the blind split on diagnostics.
        """
        return self is IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY

    @property
    def may_mutate_production_state(self) -> bool:
        """Whether a typed integration may apply this module's evidence.

        Consulted by the integration layer, never by the producing module: a
        module deciding for itself that it is allowed to write is precisely the
        bypass this design forbids.

        Collection mutates production state too - that is the entire point of
        collecting on the real seams rather than on a mock.
        """
        return self is not IntegrationMode.SHADOW

    @property
    def charges_production_budget(self) -> bool:
        """Whether this module's physical calls belong to Module 20's ledger.

        Shadow work is real work and is counted - on the shadow counters. The
        two must never be summed into one number, because a shadow call did not
        buy any production evidence.
        """
        return self is not IntegrationMode.SHADOW


def parse_mode(value: object, *, module: str) -> IntegrationMode:
    """Resolve a configured ``mode`` string, failing closed.

    Args:
        value: the raw ``mode:`` value from configuration.
        module: the module name, used only to make the error legible.

    Raises:
        IntegrationModeError: for anything that is not exactly one of the two
            legal modes. An unrecognised mode is never silently downgraded to
            ``shadow`` - a typo like ``"prod"`` would then quietly disable the
            production seam the operator meant to switch on, and the run would
            look successful.
    """
    if isinstance(value, IntegrationMode):
        return value
    if not isinstance(value, str):
        raise IntegrationModeError(
            f"{module}: mode must be a string, got {type(value).__name__}"
        )
    try:
        return IntegrationMode(value)
    except ValueError:
        legal = ", ".join(repr(mode.value) for mode in IntegrationMode)
        raise IntegrationModeError(
            f"{module}: unsupported mode {value!r}; legal modes are {legal}"
        ) from None


#: The only split calibration collection may ever read.
CALIBRATION_SPLIT = "train"


def require_split(mode: IntegrationMode, split: str) -> None:
    """Refuse a mode/split pairing that would misuse a split.

    Raises:
        IntegrationModeError: when collection is pointed anywhere but TRAIN.
            The check lives here rather than in each script so that a new
            entry point cannot forget it.
    """
    if mode.train_split_only and split != CALIBRATION_SPLIT:
        raise IntegrationModeError(
            f"{mode.value} may only run on {CALIBRATION_SPLIT!r}, not {split!r}; "
            "its actions are chosen by a fixed collection policy, so results on "
            "any other split would describe a system that does not exist"
        )


__all__ = [
    "CALIBRATION_SPLIT",
    "IntegrationMode",
    "IntegrationModeError",
    "parse_mode",
    "require_split",
]
