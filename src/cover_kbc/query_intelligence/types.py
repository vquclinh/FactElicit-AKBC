"""Module 9 public contract - the typed query risk profile.

A profile answers *"what kind of problem is this query likely to be?"*. It is
computed once, before any candidate acquisition, from three inputs only:

* the compiled relation contract (Module 0);
* the typed programme the router selected (Module 1);
* the literal ``SubjectEntity`` string.

It never contains a factual claim, a candidate, or anything derived from model
output. It is deterministic, seed-independent and serialisable, so two runs of
the same query produce equal profiles and a persisted profile can be compared
against a recomputed one.

**Why a vector and not a scalar.** The six official relations fail in different
ways: an award list fails by missing recipients, a capacity figure fails by
answering a different question (seated vs total), a death city fails by
answering at all when the person is alive. Collapsing those into one
"difficulty" number would erase exactly the structure Modules 10-21 need to act
on, so each axis stays separately interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from cover_kbc.types import ProgramType


class RiskLevel(str, Enum):
    """An ordered, interpretable risk grade.

    Four grades, not a float: M9 declares *semantics*, and a continuous score
    would invite exactly the data-fitting this architecture forbids. Ordering is
    defined explicitly because ``str`` would otherwise compare these
    alphabetically - and ``"HIGH" < "LOW"`` is true in that ordering, which is
    the opposite of what every caller means.
    """

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        """Position on the ordinal scale, ``0`` (NONE) to ``3`` (HIGH)."""
        return _RISK_ORDER[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank >= other.rank


_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}


class CardinalityRegime(str, Enum):
    """How many objects the answer may contain, in profiler vocabulary.

    A strict renaming of :class:`~cover_kbc.types.ProgramType` for downstream
    readability - *not* a second opinion about it. The profiler derives this
    through one total mapping and has no way to state it independently, which
    is what stops M9 becoming a competing router (see
    :data:`cover_kbc.query_intelligence.priors.CARDINALITY_REGIME_BY_PROGRAM`).
    """

    ZERO_OR_ONE = "ZERO_OR_ONE"
    SMALL_SET = "SMALL_SET"
    NUMERIC_SINGLE = "NUMERIC_SINGLE"
    LARGE_OPEN_SET = "LARGE_OPEN_SET"


class SecondaryRoute(str, Enum):
    """Proposal Table 3's *secondary modules*, as a closed vocabulary.

    Table 3 gives each relation a primary specialist **and** a secondary path -
    "M18 reverse check for singleton/territory risk", "M11 pseudo-memory;
    M18 key-condition; cross-model freshness", and so on. ``specialist_hint``
    carries only the first column, so these carry the second.

    Advisory, static and derived from the Module 1 programme. Nothing here
    routes, schedules or authorises: legality stays with the owning module and
    value with Module 21.
    """

    M11_PSEUDO_MEMORY = "M11_PSEUDO_MEMORY"
    M11_QUERY_SPECIFICATION = "M11_QUERY_SPECIFICATION"
    M14_FRESHNESS = "M14_FRESHNESS"
    M17_NUMERIC_VERIFIER = "M17_NUMERIC_VERIFIER"
    M17_TOTAL_VS_LAND = "M17_TOTAL_VS_LAND"
    M18_REVERSE_SINGLETON = "M18_REVERSE_SINGLETON"
    M18_KEY_CONDITION = "M18_KEY_CONDITION"
    M18_CONTRAST_ATTENDANCE = "M18_CONTRAST_ATTENDANCE"
    M18_PARENT_SUBSIDIARY = "M18_PARENT_SUBSIDIARY"
    M19_MISSINGNESS = "M19_MISSINGNESS"
    M20_RESERVED_VERIFY = "M20_RESERVED_VERIFY"
    CROSS_MODEL_FRESHNESS = "CROSS_MODEL_FRESHNESS"
    CROSS_UNIT_CYCLE = "CROSS_UNIT_CYCLE"


class SpecialistHint(str, Enum):
    """Advisory pointer to the specialist a later milestone will route to.

    A *hint*, and only a hint: it is a pure function of the Module 1 programme
    (proposal Table 3), it selects nothing in this milestone, and no M2-M8
    decision reads it. When M13-M15 exist they will consume it; until then it
    exists so the profile records which branch the architecture intends.
    """

    NONE = "NONE"
    M12_NUMERIC = "M12_NUMERIC"
    M13_LARGE_SET = "M13_LARGE_SET"
    M14_NULL_TEMPORAL = "M14_NULL_TEMPORAL"
    M15_SMALL_SET_CLOSURE = "M15_SMALL_SET_CLOSURE"


@dataclass(frozen=True)
class SubjectSurfaceFeatures:
    """Deterministic structural facts about the ``SubjectEntity`` string.

    Structure only. Every field can be computed by looking at characters, and
    none of them claims to know anything about the entity the string denotes.
    "Estadio X in Madrid" gets ``has_prepositional_qualifier=True``; it does not
    get a guessed capacity, a country, or a category.

    **No cutoffs.** ``token_count`` and ``char_length`` are reported raw rather
    than bucketed into "short"/"long", because any such boundary would be an
    arbitrary constant with no principled value and nothing here needs one. A
    later module that genuinely needs a threshold must declare it in its own
    config, where it can be argued about.
    """

    token_count: int
    char_length: int
    #: A ``(...)`` group - the usual disambiguation device in entity names.
    has_parenthetical: bool
    #: A comma-separated trailing segment, as in "Springfield, Illinois".
    has_comma_qualifier: bool
    #: A standalone "in"/"at"/"on" token. Deliberately *not* called a location
    #: qualifier: deciding whether "Nobel Prize in Physiology" is locational
    #: needs world knowledge, which a closed-book structural profiler does not
    #: have and must not pretend to.
    has_prepositional_qualifier: bool
    has_digit: bool
    has_non_ascii: bool
    #: Any of ``. ' " / & -`` inside the string; these drive tokenisation and
    #: surface-form matching, so a prompt compiler wants to know.
    has_internal_punctuation: bool

    @property
    def has_disambiguation_marker(self) -> bool:
        """Whether the name carries an explicit qualifier of any structural kind."""
        return (
            self.has_parenthetical
            or self.has_comma_qualifier
            or self.has_prepositional_qualifier
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "token_count": self.token_count,
            "char_length": self.char_length,
            "has_parenthetical": self.has_parenthetical,
            "has_comma_qualifier": self.has_comma_qualifier,
            "has_prepositional_qualifier": self.has_prepositional_qualifier,
            "has_digit": self.has_digit,
            "has_non_ascii": self.has_non_ascii,
            "has_internal_punctuation": self.has_internal_punctuation,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "SubjectSurfaceFeatures":
        return cls(
            token_count=int(payload["token_count"]),
            char_length=int(payload["char_length"]),
            has_parenthetical=bool(payload["has_parenthetical"]),
            has_comma_qualifier=bool(payload["has_comma_qualifier"]),
            has_prepositional_qualifier=bool(payload["has_prepositional_qualifier"]),
            has_digit=bool(payload["has_digit"]),
            has_non_ascii=bool(payload["has_non_ascii"]),
            has_internal_punctuation=bool(payload["has_internal_punctuation"]),
        )


#: Every risk axis, in the order they are declared and serialised. Named once so
#: the priors table, the serialiser and the consistency checker cannot drift.
RISK_AXES: tuple[str, ...] = (
    "open_set_risk",
    "missingness_risk",
    "numeric_ambiguity",
    "temporal_sensitivity",
    "nullability_risk",
    "identity_ambiguity",
    "near_miss_risk",
    "format_sensitivity",
    "verification_priority",
    "search_breadth",
)


@dataclass(frozen=True)
class QueryRiskProfile:
    """Module 9 output for one ``(subject, relation)`` query.

    Frozen and comparable: two profiles of the same query under the same config
    are ``==``, which is what the determinism and shadow-mode tests assert.
    """

    relation: str
    subject: str
    program_type: ProgramType
    cardinality_regime: CardinalityRegime

    #: How large the plausible answer universe is. Drives future facet planning.
    open_set_risk: RiskLevel
    #: How likely a complete-looking answer is still missing objects. M19's
    #: eventual prior; M9 states only the *static* expectation.
    missingness_risk: RiskLevel
    #: How many defensible numeric answers the question admits (seated vs total,
    #: land vs total area). Zero for non-numeric relations.
    numeric_ambiguity: RiskLevel
    #: How much the correct answer depends on *when* it is asked.
    temporal_sensitivity: RiskLevel
    #: How likely the correct answer is the empty set.
    nullability_risk: RiskLevel
    #: How likely the subject string denotes more than one real entity.
    identity_ambiguity: RiskLevel
    #: How likely a plausible-but-wrong neighbour of the right answer is emitted.
    near_miss_risk: RiskLevel
    #: How much the answer's *form* (unit, granularity, integer-ness) matters.
    format_sensitivity: RiskLevel
    #: How much this relation needs verification rather than more recall.
    verification_priority: RiskLevel
    #: How wide the acquisition search is expected to need to be.
    search_breadth: RiskLevel

    subject_surface: SubjectSurfaceFeatures
    specialist_hint: SpecialistHint
    profile_version: str
    row_index: int = -1
    #: Proposal Table 3's *secondary modules* column - the rest of the "route
    #: hints" Appendix C assigns to M9. Advisory and static, exactly like
    #: ``specialist_hint``: it names branches the architecture intends, selects
    #: nothing, and no M2-M8 decision reads it.
    secondary_hints: tuple[SecondaryRoute, ...] = ()
    #: §5's ``q_novel``. ``None`` means **unmeasured**, which is the honest
    #: state before any evidence exists - §5 derives novelty from the initial
    #: graph and early-return signals, and guessing it from a subject string
    #: would be a factual claim M9 is not entitled to make.
    novelty_risk: RiskLevel | None = None
    #: Why ``novelty_risk`` holds its value, or why it is unmeasured.
    novelty_basis: str = "no early graph has been observed"

    def axis(self, name: str) -> RiskLevel:
        """One risk axis by name, refusing anything not in :data:`RISK_AXES`."""
        if name not in RISK_AXES:
            raise KeyError(f"unknown risk axis {name!r}; known axes: {list(RISK_AXES)}")
        return getattr(self, name)

    def axes(self) -> dict[str, RiskLevel]:
        """Every risk axis, in declaration order."""
        return {name: getattr(self, name) for name in RISK_AXES}

    def to_json(self) -> dict[str, Any]:
        """JSON-compatible form, carrying enough identity to rejoin the query."""
        payload: dict[str, Any] = {
            "profile_version": self.profile_version,
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "program_type": self.program_type.value,
            "cardinality_regime": self.cardinality_regime.value,
            "specialist_hint": self.specialist_hint.value,
            "secondary_hints": [h.value for h in self.secondary_hints],
            "novelty_risk": (
                self.novelty_risk.value if self.novelty_risk else None),
            "novelty_basis": self.novelty_basis,
        }
        payload["risk"] = {name: level.value for name, level in self.axes().items()}
        payload["subject_surface"] = self.subject_surface.to_json()
        return payload

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "QueryRiskProfile":
        risk = payload["risk"]
        return cls(
            relation=str(payload["Relation"]),
            subject=str(payload["SubjectEntity"]),
            program_type=ProgramType(payload["program_type"]),
            cardinality_regime=CardinalityRegime(payload["cardinality_regime"]),
            specialist_hint=SpecialistHint(payload["specialist_hint"]),
            secondary_hints=tuple(
                SecondaryRoute(h) for h in payload.get("secondary_hints", ())),
            novelty_risk=(
                RiskLevel(payload["novelty_risk"])
                if payload.get("novelty_risk") else None),
            novelty_basis=payload.get(
                "novelty_basis", "no early graph has been observed"),
            profile_version=str(payload["profile_version"]),
            row_index=int(payload.get("row_index", -1)),
            subject_surface=SubjectSurfaceFeatures.from_json(payload["subject_surface"]),
            **{name: RiskLevel(risk[name]) for name in RISK_AXES},
        )
