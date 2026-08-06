"""Module 12 - the Numeric Specialist (hasCapacity, hasArea).

Architecture position::

    M0 / M1
        v
    M9 QueryRiskProfile
        v
    M10 PromptProgram
        v
    M11 ParametricMemoryRecords
        v
    M12 Numeric Specialist            <- here
        v
    [future M16 Atomic Consensus -> M17 Specialist Verification -> M19-M21]

    M2 -> M3 -> ... -> M8             (unchanged production path)

M12 mines numbers out of Module 11's recall, runs the proposal's five numeric
probe families (§8.1), canonicalises every observation, classifies which
quantity it denotes, and clusters the target-quantity values with the
proposal's own distance and dispersion statistics (§8.2).

**The maths already exists.** ``relative_distance`` is the proposal's
``delta(x_i, x_j) = |x_i - x_j| / max(|x_i|, |x_j|, eps)``, ``_relative_mad`` is
``D_num = MAD(C*) / (|median(C*)| + eps)``, and ``cluster_values`` implements the
membership rule - all in :mod:`cover_kbc.normalization.numeric`, audited under
Audits 0011 and 0012. M12 reuses them rather than writing a second numeric
stack, so there is exactly one definition of what "close" means.

**M12 decides nothing.** The proposal's ``ACCEPT`` rule (§8.3) fuses numeric
consensus with verifier evidence; the verifier evidence is Module 17's and the
fusion is Module 16's. M12 computes ``I(C*)``, ``D_num`` and the hard-definition
violations, and hands them on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.normalization.numeric import (
    AREA_UNITS_TO_KM2,
    MAGNITUDES,
    NumericValue,
    _canonical_unit,
    _relative_mad,
    cluster_values,
    parse_numbers,
    relative_distance,
)
from cover_kbc.query_intelligence.prompt_types import PromptProgram
from cover_kbc.query_intelligence.retrieval_types import (
    ParametricRetrievalResult,
    ParseStatus,
)
from cover_kbc.specialists.numeric_registry import (
    SPECIALIST_VERSION,
    NumericRelationSpec,
    check_numeric_registry_consistency,
    handles,
    numeric_spec,
)
from cover_kbc.specialists.numeric_types import (
    CrossUnitCheck,
    NumericClusterState,
    NumericObservation,
    NumericParseStatus,
    NumericProbe,
    NumericProbeFamily,
    NumericSemanticKind,
    NumericSpecialistPlan,
    NumericSpecialistResult,
    ObservationSource,
)
from cover_kbc.types import DecodeProfile, ProgramType, Query

#: Shared system prompt. States the closed-book rule and asks for a number, not
#: an argument for one.
NUMERIC_SYSTEM_PROMPT = (
    "You answer from your own internal knowledge only. You have no access to "
    "search, documents, databases or external tools. Give the number asked for "
    "and nothing else. If you do not know the value, answer exactly: UNKNOWN"
)

#: Conservative default decoding. Greedy, short: these probes ask for one
#: number. Not selected from measured performance.
NUMERIC_DECODE = DecodeProfile(name="m12_numeric", temperature=0.0, max_new_tokens=64)

#: Text meaning "no answer". Not a number, and never parsed as one.
_ABSTENTIONS = frozenset({"unknown", "none", "n/a", "na", "no recollection", "-"})

#: Characters that end the sentence a number sits in. Semantic classification
#: reads only that sentence, so a near-miss mentioned elsewhere in a long
#: recall cannot mislabel an unrelated number.
_SENTENCE_SPLIT = re.compile(r"[.;\n]")

#: A separator followed by exactly three digits - "25,000", "1.234". The core
#: parser resolves these by a documented, audited convention
#: (``parse_number_token``), and M12 does not re-litigate that decision: one
#: numeric stack, one reading. What M12 does record is that the reading came
#: from a convention rather than from the text being unambiguous, so a later
#: module can see it. The flag does not downgrade the parse status - if it did,
#: almost every capacity figure ever written would be "ambiguous" and the status
#: would carry no information.
_SEPARATOR_CONVENTION = re.compile(r"\d[.,]\d{3}(?!\d)")


class NumericSpecialistError(RuntimeError):
    """M12 could not run - bad inputs, bad routing or bad configuration."""


@dataclass(frozen=True)
class NumericSpecialistConfig:
    """Module 12 configuration.

    ``shadow`` is the only supported mode: M12 output feeds no production
    decision until Modules 16 and 17 exist to decide how.
    """

    enabled: bool = False
    mode: str = "shadow"
    specialist_version: str = SPECIALIST_VERSION
    #: Probe families to run; ``None`` means every family the relation declares.
    families: tuple[NumericProbeFamily, ...] | None = None
    #: ``tau_cluster,r``. ``None`` means "use the contract's own
    #: ``numeric_cluster_threshold``", which is the declared per-relation value
    #: and therefore the default. An override exists because the proposal indexes
    #: tau by relation; it is not a knob anyone has fitted.
    cluster_tolerance: float | None = None
    #: Whether to mine numbers out of Module 11's records as well as M12's own
    #: probes. On by default: those calls have already been paid for.
    mine_parametric_memory: bool = True

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "NumericSpecialistConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload)
            - {"enabled", "mode", "specialist_version", "families",
               "cluster_tolerance", "mine_parametric_memory"}
        )
        if unknown:
            raise ValueError(
                f"unknown specialists.numeric key(s) {unknown}; expected enabled, "
                "mode, specialist_version, families, cluster_tolerance, "
                "mine_parametric_memory"
            )

        declared = payload.get("families")
        if declared is None:
            families = None
        else:
            if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
                raise ValueError(
                    "specialists.numeric.families must be a list of family names, "
                    f"got {type(declared).__name__}"
                )
            names = [str(name) for name in declared]
            duplicates = sorted({n for n in names if names.count(n) > 1})
            if duplicates:
                raise ValueError(f"duplicate numeric probe family/families {duplicates}")
            resolved = []
            for name in names:
                try:
                    resolved.append(NumericProbeFamily(name))
                except ValueError as exc:
                    raise ValueError(
                        f"unknown numeric probe family {name!r}; the proposal "
                        f"declares {[f.value for f in NumericProbeFamily]}"
                    ) from exc
            families = tuple(resolved)

        tolerance = payload.get("cluster_tolerance")
        if tolerance is not None:
            tolerance = float(tolerance)
            if not 0.0 < tolerance < 1.0:
                raise ValueError(
                    "specialists.numeric.cluster_tolerance must be a relative "
                    f"distance in (0, 1), got {tolerance}"
                )

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            specialist_version=str(payload.get("specialist_version", SPECIALIST_VERSION)),
            families=families,
            cluster_tolerance=tolerance,
            mine_parametric_memory=bool(payload.get("mine_parametric_memory", True)),
        )


# --------------------------------------------------------------------------
# Parsing and canonicalisation
# --------------------------------------------------------------------------


def classify_semantic_kind(
    text: str, expression: str, spec: NumericRelationSpec
) -> NumericSemanticKind:
    """Which quantity the model said this number was.

    Reads the sentence the number sits in, matching the relation's declared
    lexical cues in declaration order. A number the model did not label is the
    quantity the probe asked for, which is why ``TARGET`` is the default rather
    than a cue of its own.

    This is lexical, not factual: it notices "record attendance" as a phrase, and
    knows nothing about any venue.
    """
    sentence = _sentence_around(text, expression).casefold()
    for cue in spec.semantic_cues:
        if any(phrase.casefold() in sentence for phrase in cue.phrases):
            return cue.kind
    return NumericSemanticKind.TARGET


def _sentence_around(text: str, expression: str) -> str:
    """The clause containing ``expression``, or the whole text if not found."""
    if not expression:
        return text
    index = text.find(expression)
    if index < 0:
        return text
    start = 0
    end = len(text)
    for match in _SENTENCE_SPLIT.finditer(text):
        if match.start() < index:
            start = match.end()
        else:
            end = match.start()
            break
    return text[start:end]


def canonicalise(
    value: NumericValue,
    spec: NumericRelationSpec,
    *,
    unit_was_stated: bool = False,
) -> tuple[float | None, NumericParseStatus, tuple[str, ...]]:
    """Convert one parsed value into the relation's canonical unit.

    Returns ``(canonical_value, status, ambiguity_flags)``. A unit the relation
    cannot convert is reported, never guessed at; a physically impossible value
    is rejected rather than clamped.
    """
    flags: list[str] = []

    if spec.convertible_units:
        if value.unit is None and unit_was_stated:
            # A unit was written and it is not one this relation converts.
            # Defaulting here would fabricate a unit the model did not mean.
            return None, NumericParseStatus.UNSUPPORTED_UNIT, tuple(flags)
        unit = value.unit or spec.canonical_unit
        factor = AREA_UNITS_TO_KM2.get(unit)
        if factor is None:
            return None, NumericParseStatus.UNSUPPORTED_UNIT, tuple(flags)
        if value.unit is None:
            # The contract's own unit is the only defensible assumption, but it
            # *is* an assumption and is recorded as one.
            flags.append(f"unit_assumed:{spec.canonical_unit}")
        canonical = value.value * factor
    else:
        # A unitless count. A physical unit on it means the model answered a
        # different question.
        if value.unit is not None:
            return None, NumericParseStatus.UNSUPPORTED_UNIT, tuple(flags)
        canonical = value.value

    if canonical <= 0:
        return None, NumericParseStatus.INVALID_VALUE, tuple(flags)

    if spec.integer_only:
        if canonical != int(canonical):
            # A fractional count of people is not a count of people. Recorded,
            # not rounded: rounding would invent precision the model never gave.
            flags.append("non_integer_count")
            return None, NumericParseStatus.INVALID_VALUE, tuple(flags)
        canonical = float(int(canonical))

    return canonical, NumericParseStatus.OK, tuple(flags)


#: Up to two alphabetic tokens after a number - the shape a unit takes,
#: e.g. "km2", "square miles".
_TRAILING_WORDS = re.compile(r"\s*([A-Za-z\u00b2]+)(?:\s*\.?\s*([A-Za-z0-9\u00b2]+))?")


def stated_unrecognised_unit(text: str, expression: str) -> bool:
    """Did the model state a unit this relation cannot convert?

    ``parse_numbers`` reports ``unit=None`` both when no unit was given and when
    one was given that it does not recognise. Those are different situations: the
    first can default to the contract's own unit, the second cannot be defaulted
    at all without inventing a unit the model did not mean. This tells them
    apart by looking at the words immediately after the number.
    """
    index = text.find(expression)
    if index < 0:
        return False
    match = _TRAILING_WORDS.match(text[index + len(expression):])
    if not match:
        return False
    first = match.group(1)
    if first.casefold() in MAGNITUDES:
        return False
    both = " ".join(part for part in match.groups() if part)
    return _canonical_unit(first) is None and _canonical_unit(both) is None


def _ambiguity_flags(expression: str) -> tuple[str, ...]:
    """Notation whose reading came from a convention rather than the text."""
    if _SEPARATOR_CONVENTION.search(expression):
        return ("separator_reading_by_convention",)
    return ()


def _mark_disagreeing_readings(
    observations: list[NumericObservation], tolerance: float
) -> list[NumericObservation]:
    """Flag one answer that gave several irreconcilable target values.

    This is the ambiguity that actually matters: a probe asked for one number
    and the model offered "25,000 or 30,000". Both readings survive parsing, they
    disagree by more than the relation's own tolerance, and choosing one would be
    inventing a decision the model did not make.

    A cross-unit answer stating the same quantity twice in different units is
    *not* ambiguous - after canonicalisation those values agree, which is exactly
    the consistency signal proposal §8.1 asks for.
    """
    targets = [
        obs for obs in observations
        if obs.parse_status is NumericParseStatus.OK
        and obs.semantic_kind.is_target
        and obs.canonical_value is not None
    ]
    if len(targets) < 2:
        return observations
    values = [obs.canonical_value for obs in targets]
    if relative_distance(min(values), max(values)) <= tolerance:
        return observations

    disagreeing = {id(obs) for obs in targets}
    return [
        replace(
            obs,
            parse_status=NumericParseStatus.AMBIGUOUS,
            ambiguity_flags=(*obs.ambiguity_flags, "multiple_disagreeing_readings"),
        )
        if id(obs) in disagreeing else obs
        for obs in observations
    ]


def extract_observations(
    text: str,
    *,
    spec: NumericRelationSpec,
    query: Query,
    source: ObservationSource,
    operation_id: str,
    independence_group: str,
    sample_index: int,
    prompt_sha256: str,
    model_id: str,
    tolerance: float,
    parse_status: NumericParseStatus | None = None,
    error: str | None = None,
) -> list[NumericObservation]:
    """Turn one piece of recalled text into zero or more observations.

    Always returns at least one record. Text that yields no number becomes an
    explicit ``NO_NUMBER`` or ``ABSTAINED`` observation rather than vanishing:
    a downstream module needs to know a probe ran and produced nothing.
    """
    common = dict(
        relation=query.relation, subject=query.subject, row_index=query.row_index,
        source=source, operation_id=operation_id,
        independence_group=independence_group, sample_index=sample_index,
        prompt_sha256=prompt_sha256, model_id=model_id,
        canonical_unit=spec.canonical_unit, raw_text=text,
    )

    if parse_status is NumericParseStatus.RUNTIME_ERROR:
        return [NumericObservation(
            **common, raw_expression="", parsed_value=None, raw_unit=None,
            canonical_value=None, semantic_kind=NumericSemanticKind.TARGET,
            parse_status=NumericParseStatus.RUNTIME_ERROR, error=error,
        )]

    stripped = (text or "").strip()
    if stripped.casefold() in _ABSTENTIONS or not stripped:
        status = (
            NumericParseStatus.ABSTAINED if stripped else NumericParseStatus.NO_NUMBER
        )
        return [NumericObservation(
            **common, raw_expression="", parsed_value=None, raw_unit=None,
            canonical_value=None, semantic_kind=NumericSemanticKind.TARGET,
            parse_status=status,
        )]

    values = parse_numbers(stripped)
    if not values:
        return [NumericObservation(
            **common, raw_expression="", parsed_value=None, raw_unit=None,
            canonical_value=None, semantic_kind=NumericSemanticKind.TARGET,
            parse_status=NumericParseStatus.NO_NUMBER,
        )]

    out: list[NumericObservation] = []
    for value in values:
        canonical, status, flags = canonicalise(
            value, spec, unit_was_stated=stated_unrecognised_unit(stripped, value.raw)
        )
        flags = (*flags, *_ambiguity_flags(value.raw))
        out.append(NumericObservation(
            **common,
            raw_expression=value.raw,
            parsed_value=value.value,
            raw_unit=value.unit,
            canonical_value=canonical,
            semantic_kind=classify_semantic_kind(stripped, value.raw, spec),
            parse_status=status,
            ambiguity_flags=flags,
        ))
    return _mark_disagreeing_readings(out, tolerance)


# --------------------------------------------------------------------------
# Clustering and diagnostics
# --------------------------------------------------------------------------


def build_clusters(
    observations: Sequence[NumericObservation], *, tolerance: float, canonical_unit: str
) -> tuple[NumericClusterState, ...]:
    """Cluster the target-quantity observations in canonical space.

    Delegates the membership rule and the statistics to
    :mod:`cover_kbc.normalization.numeric`, which implements the proposal's
    ``delta`` and ``D_num`` exactly and bounds a cluster's whole diameter rather
    than chaining pairwise (Audit 0012 §30 - a pairwise rule lets a run of
    values each just under ``tau`` drift without bound).

    Only ``usable`` observations take part: a hard-definition violation is a
    different quantity, and letting an attendance figure join a capacity cluster
    would be the exact error the proposal's contrastive axis exists to prevent.
    """
    eligible = [
        (index, obs) for index, obs in enumerate(observations) if obs.usable
    ]
    if not eligible:
        return ()

    values = [obs.canonical_value for _, obs in eligible]
    grouped = cluster_values(values, threshold=tolerance)

    remaining = list(eligible)
    states: list[NumericClusterState] = []
    for cluster in grouped:
        members: list[tuple[int, NumericObservation]] = []
        for member_value in cluster.values:
            for position, (index, obs) in enumerate(remaining):
                if obs.canonical_value == member_value:
                    members.append((index, obs))
                    remaining.pop(position)
                    break
        groups = sorted({obs.independence_group for _, obs in members})
        states.append(NumericClusterState(
            values=tuple(sorted(cluster.values)),
            representative=cluster.representative,
            dispersion=cluster.relative_mad,
            canonical_unit=canonical_unit,
            total_support=len(members),
            # I(C*): distinct structural sources. Resamples of one probe family
            # share a group and are counted once.
            independent_support=len(groups),
            independence_groups=tuple(groups),
            member_indices=tuple(sorted(index for index, _ in members)),
        ))
    return tuple(states)


def cross_unit_checks(
    observations: Sequence[NumericObservation], *, tolerance: float
) -> tuple[CrossUnitCheck, ...]:
    """Agreement between target observations stated in different raw units.

    Proposal §8.1 makes cross-unit representation an independence axis; this is
    the diagnostic that says whether the representations converged. Diagnostic
    only - it settles nothing about which value is right.
    """
    usable = [
        (index, obs) for index, obs in enumerate(observations)
        if obs.usable and obs.raw_unit is not None
    ]
    checks: list[CrossUnitCheck] = []
    for position, (left_index, left) in enumerate(usable):
        for right_index, right in usable[position + 1:]:
            if left.raw_unit == right.raw_unit:
                continue
            distance = relative_distance(left.canonical_value, right.canonical_value)
            checks.append(CrossUnitCheck(
                left_index=left_index, right_index=right_index,
                left_unit=left.raw_unit, right_unit=right.raw_unit,
                left_canonical=left.canonical_value,
                right_canonical=right.canonical_value,
                relative_distance=distance,
                agrees=distance <= tolerance,
            ))
    return tuple(checks)


def dispersion_of(values: Sequence[float]) -> float:
    """``D_num`` for an arbitrary value set, exposed for downstream reuse."""
    return _relative_mad(values)


# --------------------------------------------------------------------------
# The specialist
# --------------------------------------------------------------------------


class NumericSpecialist:
    """Module 12. Deterministic analysis, frozen-model specialist probes."""

    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: NumericSpecialistConfig | None = None) -> None:
        self.config = config or NumericSpecialistConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported numeric specialist mode {self.config.mode!r}; this "
                f"milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Consuming M12 output is Module 16's and Module 17's job, and "
                "neither is implemented."
            )
        check_numeric_registry_consistency()

    @property
    def specialist_version(self) -> str:
        return self.config.specialist_version

    @staticmethod
    def applies_to(program: PromptProgram) -> bool:
        """Whether Module 12 handles this query at all.

        Keyed on the Module 1 programme *and* the relation registry, so a
        non-numeric relation cannot reach the specialist even by mistake.
        """
        return (
            program.program_type is ProgramType.NUMERIC
            and program.specialist_hint.value == "M12_NUMERIC"
            and handles(program.relation)
        )

    # -- planning ------------------------------------------------------------

    def plan(
        self, query: Query, program: PromptProgram, contract: RelationContract
    ) -> NumericSpecialistPlan:
        """Render every specialist probe, without calling a model."""
        self._check_inputs(query, program, contract)
        spec = numeric_spec(program.relation)
        families = self.config.families or spec.probe_families
        unsupported = [f for f in families if f not in spec.probe_families]
        if unsupported:
            raise NumericSpecialistError(
                f"{program.relation}: probe famil(ies) "
                f"{[f.value for f in unsupported]} are not declared for this "
                f"relation; declared: {[f.value for f in spec.probe_families]}"
            )

        probes = tuple(
            NumericProbe(
                operation_id=f"m12_{family.value}#0",
                family=family,
                purpose=spec.family_instructions[family],
                prompt=self._render(program, spec, family),
                system_prompt=NUMERIC_SYSTEM_PROMPT,
                decode_profile=NUMERIC_DECODE.name,
            )
            for family in families
        )

        return NumericSpecialistPlan(
            specialist_version=self.specialist_version,
            compiler_version=program.compiler_version,
            profile_version=program.profile_version,
            retrieval_version="",
            subject=program.subject,
            relation=program.relation,
            row_index=program.row_index,
            program_type=program.program_type,
            canonical_unit=spec.canonical_unit,
            cluster_tolerance=self._tolerance(contract),
            probes=probes,
        )

    def _tolerance(self, contract: RelationContract) -> float:
        """``tau_cluster,r``: the contract's own declaration unless overridden."""
        if self.config.cluster_tolerance is not None:
            return self.config.cluster_tolerance
        return contract.selection.numeric_cluster_threshold

    @staticmethod
    def _render(
        program: PromptProgram, spec: NumericRelationSpec, family: NumericProbeFamily
    ) -> str:
        """Build one probe from Module 10's structured program.

        Generic frame plus the family's instruction. The relation's meaning
        comes from Module 10 - definition, constraints, subject directives,
        output contract - and is never restated here.
        """
        parts = [
            program.task_semantics.relation_focus,
            "",
            program.task_semantics.definition,
            "",
            f'Subject: "{program.subject}"',
        ]
        if program.subject_directives:
            parts.append(
                "\n".join(f"- {entry.instruction}" for entry in program.subject_directives)
            )
        parts += ["", spec.family_instructions[family]]
        if family is NumericProbeFamily.CONTRASTIVE_DEFINITION:
            parts += [
                "",
                "Does NOT count:",
                "\n".join(f"- {rule}" for rule in program.negative_constraints),
            ]
        if family is not NumericProbeFamily.CROSS_UNIT_FORMAT:
            parts += ["", program.output_contract]
        return "\n".join(parts)

    # -- execution -----------------------------------------------------------

    def analyse(
        self,
        query: Query,
        program: PromptProgram,
        contract: RelationContract,
        runtime: LMRuntime | None = None,
        retrieval: ParametricRetrievalResult | None = None,
    ) -> NumericSpecialistResult:
        """Mine Module 11's recall, run the specialist probes, cluster the result.

        ``runtime`` may be ``None`` to analyse Module 11's records alone, which
        costs nothing. ``retrieval`` may be ``None`` when only fresh probes are
        wanted.
        """
        plan = self.plan(query, program, contract)
        spec = numeric_spec(program.relation)
        if retrieval is not None:
            plan = replace(plan, retrieval_version=retrieval.plan.retrieval_version)

        observations: list[NumericObservation] = []
        errors: list[str] = []
        calls = generated = prompt_tokens = 0

        if retrieval is not None and self.config.mine_parametric_memory:
            observations.extend(
                self._mine(retrieval, spec, query, program, plan.cluster_tolerance)
            )

        if runtime is not None:
            for probe in plan.probes:
                found, error, cost = self._execute(
                    probe, spec, query, runtime, plan.cluster_tolerance
                )
                observations.extend(found)
                if error:
                    errors.append(error)
                calls += cost[0]
                generated += cost[1]
                prompt_tokens += cost[2]

        tolerance = plan.cluster_tolerance
        return NumericSpecialistResult(
            plan=plan,
            observations=tuple(observations),
            clusters=build_clusters(
                observations, tolerance=tolerance, canonical_unit=spec.canonical_unit
            ),
            cross_unit_checks=cross_unit_checks(observations, tolerance=tolerance),
            errors=tuple(errors),
            calls=calls,
            generated_tokens=generated,
            prompt_tokens=prompt_tokens,
        )

    def _mine(
        self,
        retrieval: ParametricRetrievalResult,
        spec: NumericRelationSpec,
        query: Query,
        program: PromptProgram,
        tolerance: float,
    ) -> list[NumericObservation]:
        """Extract numbers from Module 11's unverified recall.

        Provenance is carried through unchanged - operation id, independence
        group, sample index, prompt hash - and the observations stay unverified.
        Parsing a number out of pseudo-memory does not make it a fact.
        """
        if retrieval.plan.subject != program.subject or (
            retrieval.plan.relation != program.relation
        ):
            raise NumericSpecialistError(
                "the parametric retrieval result is for "
                f"{retrieval.plan.subject!r}/{retrieval.plan.relation!r} but the "
                f"query is {program.subject!r}/{program.relation!r}"
            )

        out: list[NumericObservation] = []
        for record in retrieval.records:
            if record.parse_status is ParseStatus.RUNTIME_ERROR:
                continue
            out.extend(extract_observations(
                record.raw_output, spec=spec, query=query, tolerance=tolerance,
                source=ObservationSource.PARAMETRIC_MEMORY,
                operation_id=record.operation_id,
                independence_group=record.independence_group.value,
                sample_index=record.sample_index,
                prompt_sha256=record.prompt_sha256,
                model_id=record.model_id,
            ))
        return out

    def _execute(
        self,
        probe: NumericProbe,
        spec: NumericRelationSpec,
        query: Query,
        runtime: LMRuntime,
        tolerance: float,
    ) -> tuple[list[NumericObservation], str | None, tuple[int, int, int]]:
        """Run one specialist probe. A failure is recorded, never fabricated."""
        request = GenerationRequest(
            prompt=probe.prompt,
            system_prompt=probe.system_prompt,
            decode=NUMERIC_DECODE,
            metadata={
                "view_id": probe.operation_id,
                "subject": query.subject,
                "relation": query.relation,
                "module": "M12",
            },
        )
        model_spec = getattr(runtime, "spec", None)
        model_id = getattr(model_spec, "model_id", "unknown")
        before_calls = int(getattr(runtime, "calls", 0))

        try:
            result = runtime.generate(request)
        except Exception as exc:  # noqa: BLE001 - one probe must not kill the run
            spent = int(getattr(runtime, "calls", 0)) - before_calls
            return (
                extract_observations(
                    "", spec=spec, query=query, tolerance=tolerance,
                    source=ObservationSource.SPECIALIST_PROBE,
                    operation_id=probe.operation_id,
                    independence_group=probe.family.value,
                    sample_index=probe.sample_index,
                    prompt_sha256=probe.prompt_sha256, model_id=model_id,
                    parse_status=NumericParseStatus.RUNTIME_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                f"{probe.operation_id}: {type(exc).__name__}: {exc}",
                (spent, 0, 0),
            )

        observations = extract_observations(
            result.text or "", spec=spec, query=query, tolerance=tolerance,
            source=ObservationSource.SPECIALIST_PROBE,
            operation_id=probe.operation_id,
            independence_group=probe.family.value,
            sample_index=probe.sample_index,
            prompt_sha256=probe.prompt_sha256,
            model_id=result.model_id or model_id,
        )
        return (
            observations,
            None,
            (
                int(getattr(runtime, "calls", 0)) - before_calls,
                int(result.generated_tokens or 0),
                int(result.prompt_tokens or 0),
            ),
        )

    # -- validation ----------------------------------------------------------

    def _check_inputs(
        self, query: Query, program: PromptProgram, contract: RelationContract
    ) -> None:
        """Refuse to run on the wrong relation or on disagreeing upstream state."""
        problems: list[str] = []
        if program.relation != query.relation:
            problems.append(
                f"program is for {program.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if program.subject != query.subject:
            problems.append(
                f"program subject {program.subject!r} != query subject {query.subject!r}"
            )
        if program.row_index != query.row_index:
            problems.append(
                f"program row_index {program.row_index} != query row_index "
                f"{query.row_index}"
            )
        if contract.relation != query.relation:
            problems.append(
                f"contract is for {contract.relation!r} but the query is for "
                f"{query.relation!r}"
            )
        if not program.compiler_version:
            problems.append("program carries no compiler_version")
        if not program.profile_version:
            problems.append("program carries no profile_version")
        if program.program_type is not ProgramType.NUMERIC:
            problems.append(
                f"Module 12 handles NUMERIC queries only; Module 1 routed "
                f"{query.relation!r} to {program.program_type.value}"
            )
        elif not self.applies_to(program):
            problems.append(
                f"Module 12 does not handle relation {query.relation!r}"
            )
        if problems:
            raise NumericSpecialistError(
                "Module 12 cannot run:\n  - " + "\n  - ".join(problems)
            )


def build_numeric_specialist(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
    retrieval_enabled: bool,
) -> NumericSpecialist | None:
    """Build M12 from a top-level ``specialists`` config block.

    Returns ``None`` when M12 is not enabled, which is the default and is the
    pre-Module-12 code path exactly.

    Raises:
        ValueError: if M12 is enabled without Modules 9, 10 and 11. It consumes
            all three and rebuilds none of them.
    """
    block = dict(config or {})
    unknown = sorted(set(block) - {"numeric", "large_open_set", "null_temporal"})
    if unknown:
        raise ValueError(
            f"unknown specialists key(s) {unknown}; this milestone defines "
            "'numeric' (M12), 'large_open_set' (M13) and 'null_temporal' (M14) "
            "only (M15-M21 are not implemented)"
        )
    specialist_config = NumericSpecialistConfig.from_mapping(block.get("numeric"))
    if not specialist_config.enabled:
        return None
    missing = [
        name for name, present in (
            ("profiler", profiler_enabled),
            ("prompt_compiler", compiler_enabled),
            ("parametric_retrieval", retrieval_enabled),
        ) if not present
    ]
    if missing:
        raise ValueError(
            "specialists.numeric is enabled but "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not. "
            "Module 12 consumes Module 9's profile, Module 10's prompt program "
            "and Module 11's parametric memory; enable them or disable the "
            "numeric specialist."
        )
    return NumericSpecialist(specialist_config)


def probe_catalogue() -> list[dict[str, object]]:
    """The declared probe families per relation, for the audit."""
    from cover_kbc.specialists.numeric_registry import NUMERIC_RELATIONS

    return [
        {
            "relation": relation,
            "families": [f.value for f in NUMERIC_RELATIONS[relation].probe_families],
            "canonical_unit": NUMERIC_RELATIONS[relation].canonical_unit,
        }
        for relation in sorted(NUMERIC_RELATIONS)
    ]


def canonical_units() -> dict[str, float]:
    """The area conversion constants M12 relies on. Mathematical, not factual."""
    return dict(AREA_UNITS_TO_KM2)
