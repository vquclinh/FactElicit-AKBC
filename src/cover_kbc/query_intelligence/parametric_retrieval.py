"""Module 11 - Closed-Book Parametric Retrieval.

Architecture position::

    M0 Relation Compiler
            v
    M1 Typed Program Router
            v
    M9 Risk & Difficulty Profiler
            v
    M10 Prompt Program Compiler
            v
    M11 Closed-Book Parametric Retrieval    <- here
            v
    [future M12-M15 specialists]
            v
    M2 -> M8                                (unchanged production path)

M11 turns a compiled :class:`PromptProgram` into a small, fixed plan of probes
and executes them against the **frozen enumerator runtime**. It produces
:class:`ParametricMemoryRecord` objects: unverified, model-generated recall for
later specialists to work from.

**M11 does spend neural calls.** Unlike Modules 9 and 10, this module's whole
function is to query the model, so "zero neural cost" would be false. What is
guaranteed instead: zero new *parameters*, every call attributable to one
operation, every call counted exactly once, and M11's spend accounted separately
from the production path's - it never touches Module 7's per-query budget.

**What M11 does not do.** It does not verify (Module 4 / Module 17), does not
cluster numbers (Module 12), does not plan facets (Module 13), does not gate
existence (Module 14), does not close sets (Module 15), does not reach consensus
(Module 16), does not estimate missingness (Module 19), does not allocate
compute (Module 20) and does not choose actions (Module 21). It exposes material
those modules will consume; it makes none of their decisions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from cover_kbc.models.base import GenerationRequest, LMRuntime
from cover_kbc.query_intelligence.prompt_types import PromptProgram
from cover_kbc.query_intelligence.retrieval_templates import (
    NO_RECOLLECTION,
    RETRIEVAL_SYSTEM_PROMPT,
    expected_output_kind,
    render_pseudo_memory,
    render_query_rewrite,
    render_self_ask,
)
from cover_kbc.query_intelligence.retrieval_types import (
    ExpectedOutputKind,
    ParametricIndependenceGroup,
    ParametricMemoryRecord,
    ParametricRecallOperation,
    ParametricRetrievalPlan,
    ParametricRetrievalResult,
    ParseStatus,
    RecallOperationKind,
    prompt_digest,
)
from cover_kbc.types import DecodeProfile, Query

#: Bumped whenever a template, decode default or plan rule below changes, so a
#: persisted artefact can never be read under the wrong architecture version.
RETRIEVAL_VERSION = "m11-v1"


@dataclass(frozen=True)
class OperationSpec:
    """Static declaration of one probe family.

    Everything about a family lives here: which independence group it carries,
    what shape it asks for, how it decodes and what it is for. Adding a family
    means adding a row, not editing control flow.
    """

    kind: RecallOperationKind
    independence_group: ParametricIndependenceGroup
    purpose: str
    render: Any
    output_kind: ExpectedOutputKind | None
    decode: DecodeProfile


#: Conservative defaults. Greedy everywhere, so a scripted or a real run is
#: reproducible and no sampling budget is smuggled in. Token limits are sized to
#: the shape each probe asks for - three sentences, a handful of Q/A lines, one
#: answer line - and none was chosen from measured performance.
OPERATION_SPECS: dict[RecallOperationKind, OperationSpec] = {
    RecallOperationKind.PSEUDO_MEMORY: OperationSpec(
        kind=RecallOperationKind.PSEUDO_MEMORY,
        independence_group=ParametricIndependenceGroup.PSEUDO_MEMORY_SKETCH,
        purpose=(
            "Externalise a short relation-focused memory sketch. An acquisition "
            "artifact only: the sketch never becomes a support edge."
        ),
        render=render_pseudo_memory,
        output_kind=ExpectedOutputKind.PROSE,
        decode=DecodeProfile(name="m11_sketch", temperature=0.0, max_new_tokens=192),
    ),
    RecallOperationKind.SELF_ASK: OperationSpec(
        kind=RecallOperationKind.SELF_ASK,
        independence_group=ParametricIndependenceGroup.SELF_ASK_DECOMPOSITION,
        purpose=(
            "Answer the step-back sub-questions Module 10 specified, so the "
            "intermediate recall is visible to later specialists."
        ),
        render=render_self_ask,
        output_kind=ExpectedOutputKind.QA_PAIRS,
        decode=DecodeProfile(name="m11_self_ask", temperature=0.0, max_new_tokens=256),
    ),
    RecallOperationKind.QUERY_REWRITE: OperationSpec(
        kind=RecallOperationKind.QUERY_REWRITE,
        independence_group=ParametricIndependenceGroup.QUERY_REWRITE,
        purpose=(
            "Re-ask the query conditioned on the relation's exclusions and "
            "near-miss anchors, rather than by resampling the same phrasing."
        ),
        render=render_query_rewrite,
        output_kind=None,          # follows Module 10's answer schema
        decode=DecodeProfile(name="m11_rewrite", temperature=0.0, max_new_tokens=192),
    ),
}

#: The plan M11 runs when configuration names no operations. One probe per
#: declared family, one sample each - the smallest set that represents the
#: architecture. Not tuned: a relation-aware allocation is Module 20's.
DEFAULT_OPERATIONS: tuple[RecallOperationKind, ...] = (
    RecallOperationKind.PSEUDO_MEMORY,
    RecallOperationKind.SELF_ASK,
    RecallOperationKind.QUERY_REWRITE,
)


class RetrievalError(RuntimeError):
    """M11 could not run at all - bad inputs or bad configuration."""


@dataclass(frozen=True)
class RetrievalConfig:
    """Module 11 configuration.

    ``shadow`` is the only supported mode: M11 records are observational in this
    milestone, and no production decision may consume them until the specialist
    layer exists to decide how.
    """

    enabled: bool = False
    mode: str = "shadow"
    retrieval_version: str = RETRIEVAL_VERSION
    operations: tuple[RecallOperationKind, ...] = DEFAULT_OPERATIONS
    #: Samples per family. Resamples share their family's independence group, so
    #: raising this buys evidence volume and never structural diversity.
    samples_per_operation: int = 1

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "RetrievalConfig":
        payload = dict(config or {})
        unknown = sorted(
            set(payload)
            - {"enabled", "mode", "retrieval_version", "operations", "samples_per_operation"}
        )
        if unknown:
            raise ValueError(
                f"unknown query_intelligence.parametric_retrieval key(s) {unknown}; "
                "expected enabled, mode, retrieval_version, operations, "
                "samples_per_operation"
            )

        declared = payload.get("operations")
        if declared is None:
            operations = DEFAULT_OPERATIONS
        else:
            if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
                raise ValueError(
                    "query_intelligence.parametric_retrieval.operations must be a "
                    f"list of operation names, got {type(declared).__name__}"
                )
            names = [str(name) for name in declared]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                raise ValueError(
                    f"duplicate parametric retrieval operation(s) {duplicates}; each "
                    "family may be declared once - use samples_per_operation for repeats"
                )
            resolved: list[RecallOperationKind] = []
            for name in names:
                try:
                    resolved.append(RecallOperationKind(name))
                except ValueError as exc:
                    raise ValueError(
                        f"unknown parametric retrieval operation {name!r}; the "
                        "proposal declares exactly "
                        f"{[k.value for k in RecallOperationKind]}"
                    ) from exc
            operations = tuple(resolved)

        samples = int(payload.get("samples_per_operation", 1))
        if samples < 1:
            raise ValueError("samples_per_operation must be at least 1")

        return cls(
            enabled=bool(payload.get("enabled", False)),
            mode=str(payload.get("mode", "shadow")),
            retrieval_version=str(payload.get("retrieval_version", RETRIEVAL_VERSION)),
            operations=operations,
            samples_per_operation=samples,
        )


class ParametricRetriever:
    """Module 11. Closed-book, deterministic planning, frozen-model execution.

    The runtime is supplied, never constructed: M11 adds no model loader and no
    second inference path. It uses the same :class:`LMRuntime` abstraction the
    production enumerator uses.
    """

    SUPPORTED_MODES = frozenset({"shadow"})

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig(enabled=True)
        if self.config.mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"unsupported parametric retrieval mode {self.config.mode!r}; this "
                f"milestone implements {sorted(self.SUPPORTED_MODES)} only. "
                "Consuming M11 records is the specialist layer's job and M12-M15 "
                "are not implemented."
            )
        if not self.config.operations:
            raise ValueError("at least one parametric retrieval operation is required")

    @property
    def retrieval_version(self) -> str:
        return self.config.retrieval_version

    # -- planning ------------------------------------------------------------

    def plan(self, query: Query, program: PromptProgram) -> ParametricRetrievalPlan:
        """Render every probe for one query, without calling a model.

        Deterministic and free: a plan can be inspected, costed and scheduled
        before any spend, which is what a future Module 20 needs.
        """
        self._check_agreement(query, program)

        operations: list[ParametricRecallOperation] = []
        for kind in self.config.operations:
            spec = OPERATION_SPECS[kind]
            prompt = spec.render(program)
            output_kind = spec.output_kind or expected_output_kind(program)
            for sample in range(self.config.samples_per_operation):
                operations.append(
                    ParametricRecallOperation(
                        operation_id=f"{kind.value}#{sample}",
                        kind=kind,
                        # Resamples share the family's group: repetition is
                        # volume, never a second structural source.
                        independence_group=spec.independence_group,
                        purpose=spec.purpose,
                        prompt=prompt,
                        system_prompt=RETRIEVAL_SYSTEM_PROMPT,
                        decode_profile=spec.decode.name,
                        expected_output_kind=output_kind,
                        sample_index=sample,
                    )
                )

        return ParametricRetrievalPlan(
            retrieval_version=self.retrieval_version,
            compiler_version=program.compiler_version,
            profile_version=program.profile_version,
            program_sha256=program_digest(program),
            subject=program.subject,
            relation=program.relation,
            row_index=program.row_index,
            program_type=program.program_type,
            specialist_hint=program.specialist_hint.value,
            operations=tuple(operations),
        )

    # -- execution -----------------------------------------------------------

    def retrieve(
        self, query: Query, program: PromptProgram, runtime: LMRuntime
    ) -> ParametricRetrievalResult:
        """Plan and execute every probe against the frozen model.

        One operation is one runtime call. Cost is measured from the runtime's
        own counters as a delta around each call, so a record can never claim a
        call that did not happen or miss one that did.
        """
        plan = self.plan(query, program)
        records: list[ParametricMemoryRecord] = []
        errors: list[str] = []

        for operation in plan.operations:
            record, error = self._execute(operation, program, runtime)
            records.append(record)
            if error:
                errors.append(error)

        return ParametricRetrievalResult(
            plan=plan, records=tuple(records), errors=tuple(errors)
        )

    def _execute(
        self, operation: ParametricRecallOperation, program: PromptProgram, runtime: LMRuntime
    ) -> tuple[ParametricMemoryRecord, str | None]:
        """Run one probe. A failure becomes an explicit record, never fiction."""
        spec = OPERATION_SPECS[operation.kind]
        decode = spec.decode
        if operation.sample_index and decode.deterministic:
            # A greedy resample would return the identical string, so a repeat
            # only means anything under sampling. The seed is derived from the
            # sample index, so the run stays reproducible.
            decode = replace(
                decode, temperature=0.7, seed=operation.sample_index
            )

        request = GenerationRequest(
            prompt=operation.prompt,
            system_prompt=operation.system_prompt,
            decode=decode,
            metadata={
                # ``view_id`` is how the offline runtime keys its script; using
                # the operation id keeps M11 fixtures disjoint from every
                # production view, so neither can consume the other's outputs.
                "view_id": operation.operation_id,
                "subject": program.subject,
                "relation": program.relation,
                "module": "M11",
            },
        )

        spec_id = getattr(runtime, "spec", None)
        model_id = getattr(spec_id, "model_id", "unknown")
        revision = getattr(spec_id, "revision", "")

        before_calls = int(getattr(runtime, "calls", 0))
        before_tokens = int(getattr(runtime, "generated_tokens", 0))
        started = time.perf_counter()

        try:
            result = runtime.generate(request)
        except Exception as exc:  # noqa: BLE001 - one probe must not kill the run
            # An explicit failed record. Nothing is invented to fill the gap.
            return (
                ParametricMemoryRecord(
                    operation_id=operation.operation_id,
                    kind=operation.kind,
                    independence_group=operation.independence_group,
                    raw_output="",
                    parse_status=ParseStatus.RUNTIME_ERROR,
                    model_id=model_id,
                    model_revision=revision,
                    decode_profile=decode.name,
                    prompt_sha256=operation.prompt_sha256,
                    calls=int(getattr(runtime, "calls", 0)) - before_calls,
                    generated_tokens=int(getattr(runtime, "generated_tokens", 0))
                    - before_tokens,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=f"{type(exc).__name__}: {exc}",
                    sample_index=operation.sample_index,
                ),
                f"{operation.operation_id}: {type(exc).__name__}: {exc}",
            )

        text = (result.text or "").strip()
        return (
            ParametricMemoryRecord(
                operation_id=operation.operation_id,
                kind=operation.kind,
                independence_group=operation.independence_group,
                raw_output=text,
                parse_status=classify_output(text, operation, program),
                model_id=result.model_id or model_id,
                model_revision=revision,
                decode_profile=decode.name,
                prompt_sha256=operation.prompt_sha256,
                # Measured, not assumed: the delta is what the runtime actually
                # spent, so a runtime that batched or cached cannot be
                # over-charged.
                calls=int(getattr(runtime, "calls", 0)) - before_calls,
                generated_tokens=int(result.generated_tokens or 0),
                prompt_tokens=int(result.prompt_tokens or 0),
                latency_ms=result.latency_ms,
                sample_index=operation.sample_index,
            ),
            None,
        )

    # -- validation ----------------------------------------------------------

    def _check_agreement(self, query: Query, program: PromptProgram) -> None:
        """Refuse to run on inputs that disagree about which query this is.

        M11 consumes M9 and M10 through the compiled program; it never rebuilds
        either. A disagreement means the upstream state does not belong to this
        query, which is a bug rather than something to paper over.
        """
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
        if not program.compiler_version:
            problems.append("program carries no compiler_version")
        if not program.profile_version:
            problems.append(
                "program carries no profile_version; M11 requires a program "
                "compiled against a Module 9 risk profile"
            )
        if problems:
            raise RetrievalError(
                "Module 11 inputs disagree about the query:\n  - " + "\n  - ".join(problems)
            )


def program_digest(program: PromptProgram) -> str:
    """Stable identity for the exact prompt program a plan was built from."""
    import json

    payload = json.dumps(program.to_json(), sort_keys=True, ensure_ascii=False)
    return prompt_digest(payload)


def classify_output(
    text: str, operation: ParametricRecallOperation, program: PromptProgram
) -> ParseStatus:
    """How usable one probe's output is.

    A shape check, never a truth check. ``OK`` means the model returned
    something of the requested form; it says nothing about whether the content
    is correct, and Module 11 has no way to find out.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ParseStatus.EMPTY

    folded = stripped.casefold()
    abstentions = {
        NO_RECOLLECTION.casefold(),
        program.answer_schema.empty_token.casefold(),
        "unknown",
        "none",
    }
    if folded in abstentions:
        return ParseStatus.ABSTAINED

    if operation.expected_output_kind is ExpectedOutputKind.QA_PAIRS:
        # The frame asks for Q:/A: lines; anything else is unusable in the shape
        # a downstream parser was told to expect.
        if "a:" not in folded:
            return ParseStatus.MALFORMED

    return ParseStatus.OK


def build_parametric_retriever(
    config: Mapping[str, Any] | None,
    *,
    profiler_enabled: bool,
    compiler_enabled: bool,
) -> ParametricRetriever | None:
    """Build M11 from a top-level ``query_intelligence`` block.

    Returns ``None`` when M11 is not enabled, which is the default and is the
    pre-Module-11 code path exactly.

    Raises:
        ValueError: if M11 is enabled without Modules 9 and 10. The stack is
            ``M1 -> M9 -> M10 -> M11``; a retriever with no compiled prompt
            program would have to rebuild one, which is precisely the duplicated
            architecture this layering exists to prevent.
    """
    block = dict(config or {})
    retrieval_config = RetrievalConfig.from_mapping(block.get("parametric_retrieval"))
    if not retrieval_config.enabled:
        return None
    missing = [
        name
        for name, present in (("profiler", profiler_enabled), ("prompt_compiler", compiler_enabled))
        if not present
    ]
    if missing:
        raise ValueError(
            "query_intelligence.parametric_retrieval is enabled but "
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not. "
            "Module 11 consumes Module 10's PromptProgram, which requires Module "
            "9's QueryRiskProfile; enable them or disable parametric retrieval."
        )
    return ParametricRetriever(retrieval_config)


def operation_catalogue() -> list[dict[str, str]]:
    """The declared probe families, for the audit."""
    return [
        {
            "operation": spec.kind.value,
            "independence_group": spec.independence_group.value,
            "decode_profile": spec.decode.name,
            "purpose": spec.purpose,
        }
        for spec in (OPERATION_SPECS[kind] for kind in RecallOperationKind)
    ]


def as_sequence(kinds: Sequence[str]) -> tuple[RecallOperationKind, ...]:
    """Resolve operation names, failing loudly on an unknown one."""
    return tuple(RecallOperationKind(name) for name in kinds)
