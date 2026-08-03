"""Module 1 - the four typed inference programmes (spec section 6, Table 4).

A ``ProgramType`` is an *inference regime*, not a label. This module is the one
authoritative statement of what each regime means at the programme level::

    SMALL_SET        small candidate universe; precision-aware verification;
                     fast stopping
    NULL_SINGLE      existence gate first; zero-or-one final object; explicit
                     abstention
    NUMERIC          semantically diverse scalar recalls; deterministic
                     normalization; robust clustering
    LARGE_OPEN_SET   recall-first facet exploration followed by atomic
                     verification and saturation control

**Scope boundary — this is the important part.**

``TypedProgramSpec`` answers *"what class of inference process does this
relation need?"*. :class:`~cover_kbc.contracts.base.RelationContract` answers
*"what does this exact relation mean?"*. The two must not leak into each other:

* Programme level: ``companyTradesAtStockExchange`` is a SMALL_SET - a small,
  bounded, precision-oriented search that may return nothing.
* Relation level: the *company itself* must be listed, a parent's listing does
  not count. That belongs to the contract and is deliberately absent here.

Consequently ``countryLandBordersCountry`` and ``companyTradesAtStockExchange``
share every field below and share no factual semantics whatsoever.

**This is not a god object.** It holds no prompts, no parsing, no scoring, no
RCSE math and no selection algorithm. Modules 2, 6, 7 and 8 own those and
dispatch on the regime; they consume this definition instead of each
re-deriving what a programme means.
"""

from __future__ import annotations

from dataclasses import dataclass

from cover_kbc.types import Cardinality, OutputType, ProgramType, ViewFamily


@dataclass(frozen=True)
class TypedProgramSpec:
    """Programme-level regime facts. Every field has a downstream consumer."""

    program_type: ProgramType
    #: Cardinality regimes a contract on this programme may declare.
    allowed_cardinalities: tuple[Cardinality, ...]
    #: The output type this programme requires, or ``None`` if it is agnostic.
    required_output_type: OutputType | None
    #: Structural cap on emitted objects; ``0`` means the regime is unbounded
    #: and the contract's own ``SelectionPolicy`` decides.
    max_objects: int
    #: Whether a contract on this programme may declare missingness views -
    #: i.e. whether "search for what is still missing" is meaningful here.
    supports_missingness: bool

    @property
    def bounded(self) -> bool:
        """True when the regime itself caps the answer set."""
        return self.max_objects > 0

    @property
    def allows_empty_answer(self) -> bool:
        """True when an empty object set is a legal answer for this regime."""
        return Cardinality.EXACTLY_ONE not in self.allowed_cardinalities or len(
            self.allowed_cardinalities
        ) > 1

    def to_json(self) -> dict[str, object]:
        return {
            "program_type": self.program_type.value,
            "allowed_cardinalities": [c.value for c in self.allowed_cardinalities],
            "required_output_type": (
                self.required_output_type.value if self.required_output_type else None
            ),
            "max_objects": self.max_objects,
            "supports_missingness": self.supports_missingness,
        }


#: Spec section 6, Table 4. The single authoritative programme definition.
PROGRAMS: dict[ProgramType, TypedProgramSpec] = {
    ProgramType.SMALL_SET: TypedProgramSpec(
        program_type=ProgramType.SMALL_SET,
        allowed_cardinalities=(Cardinality.ZERO_OR_MANY_SMALL,),
        required_output_type=OutputType.ENTITY,
        max_objects=0,          # small in practice, not capped by the regime
        supports_missingness=True,
    ),
    ProgramType.NULL_SINGLE: TypedProgramSpec(
        program_type=ProgramType.NULL_SINGLE,
        allowed_cardinalities=(Cardinality.ZERO_OR_ONE,),
        required_output_type=OutputType.ENTITY,
        max_objects=1,
        # There is nothing to "still be missing" once the single locality is
        # resolved, so a missingness sweep would only manufacture rivals.
        supports_missingness=False,
    ),
    ProgramType.NUMERIC: TypedProgramSpec(
        program_type=ProgramType.NUMERIC,
        allowed_cardinalities=(Cardinality.EXACTLY_ONE,),
        required_output_type=OutputType.NUMBER,
        max_objects=1,
        supports_missingness=False,
    ),
    ProgramType.LARGE_OPEN_SET: TypedProgramSpec(
        program_type=ProgramType.LARGE_OPEN_SET,
        allowed_cardinalities=(Cardinality.ZERO_OR_MANY_LARGE,),
        required_output_type=OutputType.ENTITY,
        max_objects=0,
        supports_missingness=True,
    ),
}


def get_program(program_type: ProgramType) -> TypedProgramSpec:
    """The authoritative spec for one programme.

    Raises:
        KeyError: for a programme with no definition. There is deliberately no
            default: an unrouted regime must fail closed rather than inherit
            some other programme's semantics.
    """
    try:
        return PROGRAMS[program_type]
    except KeyError as exc:  # pragma: no cover - unreachable while the enum is closed
        raise KeyError(
            f"No typed-programme definition for {program_type!r}; "
            f"defined programmes: {sorted(p.value for p in PROGRAMS)}"
        ) from exc


def all_programs() -> list[TypedProgramSpec]:
    """Programme specs in a stable order."""
    return [PROGRAMS[p] for p in ProgramType]


def check_program_compatibility(contract) -> list[str]:
    """Programme-level invariants for one contract (spec section 6).

    Returns a list of problems rather than raising, so the router can report
    every disagreement across all six relations at once.
    """
    spec = get_program(contract.program_type)
    problems: list[str] = []
    name = f"{contract.relation} [{contract.program_type.value}]"

    if contract.cardinality not in spec.allowed_cardinalities:
        allowed = ", ".join(c.value for c in spec.allowed_cardinalities)
        problems.append(
            f"{name}: cardinality {contract.cardinality.value} is not a "
            f"{contract.program_type.value} regime (allowed: {allowed})"
        )

    if spec.required_output_type is not None and contract.output_type is not spec.required_output_type:
        problems.append(
            f"{name}: output type {contract.output_type.value} contradicts the "
            f"programme, which requires {spec.required_output_type.value}"
        )

    if spec.bounded and contract.selection.max_objects not in (0, spec.max_objects):
        problems.append(
            f"{name}: selection cap {contract.selection.max_objects} exceeds the "
            f"programme cap of {spec.max_objects}"
        )

    if not spec.supports_missingness and ViewFamily.MISSINGNESS in contract.view_families:
        problems.append(
            f"{name}: declares a missingness view family, which this programme "
            "does not support"
        )

    return problems
