"""Module 9 - the Risk & Difficulty Profiler.

Architecture position::

    M0 Relation Compiler
            v
    M1 Typed Program Router
            v
    M9 Risk & Difficulty Profiler      <- here
            v
    [future M10 Prompt Program Compiler / M11 Parametric Retrieval]
            v
    M2 Diverse Elicitation

The profiler runs *before* any candidate acquisition and produces a
:class:`~cover_kbc.query_intelligence.types.QueryRiskProfile` describing what
kind of problem the query is likely to be. It does not answer the query, does
not select prompts, and does not route.

**Zero neural cost.** Nothing here imports a runtime, a registry or a model
backend, and nothing here performs I/O. Profiling one query is a dictionary
lookup and a pass over a short string.

**Shadow mode.** In this milestone the profile is observational: no M2-M8
decision function reads it, and the pipeline computes it into a side buffer
that never reaches the evidence graph or the staged schema. Module 10 will be
the first consumer.

**Static, not dynamic.** M9 answers "what kind of problem is this?" from the
query alone. "Given the current candidate graph, what should we do next?" is
M19 (coverage gaps), M20 (budget) and M21 (next action), and none of that
belongs here - a profile that changed as evidence arrived would not be a
profile, it would be state.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from cover_kbc.contracts.base import RelationContract
from cover_kbc.contracts.programs import TypedProgramSpec
from cover_kbc.contracts.router import compile_query
from cover_kbc.query_intelligence.priors import (
    PROFILE_VERSION,
    RelationRiskPriors,
    cardinality_regime_for,
    check_priors_consistency,
    priors_from_mapping,
    specialist_hint_for,
)
from cover_kbc.query_intelligence.types import (
    QueryRiskProfile,
    SubjectSurfaceFeatures,
)
from cover_kbc.types import Query

#: Tokens that mark a prepositional qualifier. Structural only - matching one
#: says the name has a qualifying phrase, never that the phrase is a place.
_QUALIFIER_TOKENS = frozenset({"in", "at", "on"})
#: Punctuation that changes how a surface form tokenises or matches.
_INTERNAL_PUNCTUATION = frozenset(".'\"/&-")


def subject_surface_features(subject: str) -> SubjectSurfaceFeatures:
    """Structural features of a subject string. Pure text, no inference.

    Deterministic and total: any string, including the empty one, produces a
    complete feature set rather than an error, because a malformed subject is
    the acquisition path's problem to report, not the profiler's to guess at.
    """
    text = str(subject)
    stripped = text.strip()
    tokens = stripped.split()
    # NFC first, so a composed and a decomposed spelling of the same name are
    # not reported as different surface forms.
    normalized = unicodedata.normalize("NFC", stripped)

    return SubjectSurfaceFeatures(
        token_count=len(tokens),
        char_length=len(normalized),
        has_parenthetical="(" in normalized and ")" in normalized,
        has_comma_qualifier="," in normalized,
        has_prepositional_qualifier=any(
            token.strip(".,;:()").casefold() in _QUALIFIER_TOKENS for token in tokens
        ),
        has_digit=any(char.isdigit() for char in normalized),
        has_non_ascii=not normalized.isascii(),
        has_internal_punctuation=any(char in _INTERNAL_PUNCTUATION for char in normalized),
    )


@dataclass(frozen=True)
class ProfilerConfig:
    """Module 9 configuration.

    ``shadow`` is the only supported mode in this milestone and is enforced:
    asking for anything else fails loudly rather than silently behaving as if
    M10-M21 existed.
    """

    enabled: bool = False
    mode: str = "shadow"
    profile_version: str = PROFILE_VERSION
    #: Optional per-relation axis overrides, validated at construction.
    relation_priors: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "ProfilerConfig":
        """Build from the ``query_intelligence.profiler`` config block."""
        payload = dict(config or {})
        unknown = sorted(
            set(payload) - {"enabled", "mode", "profile_version", "relation_priors"}
        )
        if unknown:
            raise ValueError(
                f"unknown query_intelligence.profiler key(s) {unknown}; expected "
                "enabled, mode, profile_version, relation_priors"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            profile_version=str(payload.get("profile_version", PROFILE_VERSION)),
            relation_priors=payload.get("relation_priors"),
        )


class QueryProfiler:
    """Module 9. Deterministic, closed-book, non-neural.

    Construction validates the declaration table against Modules 0 and 1, so a
    prior that contradicts a contract stops the run before any model loads.
    """

    #: Modes this milestone implements. ``shadow`` means: produce the profile,
    #: let nothing consume it.
    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: ProfilerConfig | None = None) -> None:
        self.config = config or ProfilerConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported profiler mode {self.config.mode!r}; this milestone "
                f"implements {sorted(self.SUPPORTED_MODES)} only. Consuming the "
                "profile is Module 10's job and Module 10 is not implemented."
            )
        check_priors_consistency()
        self.priors: dict[str, RelationRiskPriors] = priors_from_mapping(
            self.config.relation_priors
        )
        if self.config.relation_priors:
            # An override may only weaken or strengthen a grade, never make the
            # table self-contradictory.
            _check_resolved_priors(self.priors)

    @property
    def profile_version(self) -> str:
        return self.config.profile_version

    def profile(
        self,
        query: Query,
        contract: RelationContract | None = None,
        program: TypedProgramSpec | None = None,
    ) -> QueryRiskProfile:
        """Profile one query.

        ``contract`` and ``program`` are accepted so a caller that already
        resolved Modules 0 and 1 does not resolve them twice; when omitted they
        are looked up. Either way the programme comes from Module 1 - the
        profiler has no path that decides one for itself.

        Raises:
            UnknownRelationError: for a relation with no contract.
            UnknownRelationPriorError: for a relation with no declared priors.
            ValueError: if a supplied contract disagrees with the query, or a
                supplied programme disagrees with the contract.
        """
        if contract is None:
            _, contract = compile_query(query.subject, query.relation, query.row_index)
        elif contract.relation != query.relation:
            raise ValueError(
                f"contract is for {contract.relation!r} but the query is for "
                f"{query.relation!r}; refusing to profile a mismatched pair"
            )

        # Module 1 owns the programme. Passing one in is a shortcut, not a vote:
        # a programme that disagrees with the contract is an error, never an
        # override.
        routed = contract.program
        if program is not None and program.program_type is not routed.program_type:
            raise ValueError(
                f"{contract.relation}: supplied programme {program.program_type.value} "
                f"disagrees with the routed programme {routed.program_type.value}; "
                "Module 9 consumes the router and cannot override it"
            )

        priors = self.priors.get(contract.relation)
        if priors is None:
            from cover_kbc.query_intelligence.priors import get_priors

            priors = get_priors(contract.relation)

        return QueryRiskProfile(
            relation=contract.relation,
            subject=query.subject,
            row_index=query.row_index,
            program_type=routed.program_type,
            cardinality_regime=cardinality_regime_for(routed.program_type),
            specialist_hint=specialist_hint_for(routed.program_type),
            subject_surface=subject_surface_features(query.subject),
            profile_version=self.profile_version,
            **priors.axes(),
        )

    def profile_all(self, queries: Iterable[Query]) -> list[QueryRiskProfile]:
        """Profile many queries, preserving order."""
        return [self.profile(query) for query in queries]


def _check_resolved_priors(resolved: Mapping[str, RelationRiskPriors]) -> None:
    """Re-run the contract invariants against an override-modified table."""
    from cover_kbc.query_intelligence import priors as priors_module

    original = priors_module.RELATION_RISK_PRIORS
    priors_module.RELATION_RISK_PRIORS = dict(resolved)
    try:
        check_priors_consistency()
    finally:
        priors_module.RELATION_RISK_PRIORS = original


def build_profiler(config: Mapping[str, Any] | None) -> QueryProfiler | None:
    """Build the profiler from a top-level ``query_intelligence`` config block.

    Returns ``None`` when profiling is not enabled, which is the default and is
    byte-for-byte the pre-Module-9 code path.
    """
    block = dict(config or {})
    unknown = sorted(set(block) - {"profiler"})
    if unknown:
        raise ValueError(
            f"unknown query_intelligence key(s) {unknown}; this milestone defines "
            "'profiler' only (M10-M21 are not implemented)"
        )
    profiler_config = ProfilerConfig.from_mapping(block.get("profiler"))
    if not profiler_config.enabled:
        return None
    return QueryProfiler(profiler_config)
