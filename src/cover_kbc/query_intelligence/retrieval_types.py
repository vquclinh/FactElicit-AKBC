"""Module 11 public contract - closed-book parametric retrieval.

**This is not RAG.** The proposal (§7.1) is explicit: "Since external documents
are forbidden, COVER replaces corpus retrieval with independent parametric-memory
probes." There is no retriever, no corpus, no index and no cache. The only
factual source is the frozen model's own weights::

    classical RAG:  query -> external retriever -> documents -> LLM
    COVER M11:      query -> deterministic prompt transformation
                          -> frozen LM parametric memory
                          -> model-generated recall

Everything a probe returns is a **recalled statement**, not a retrieved fact. The
vocabulary here is deliberate: :class:`ParametricMemoryRecord` carries
``source = FROZEN_MODEL_PARAMETRIC_MEMORY`` and ``verified`` that is always
``False``, because the proposal's evidence-hygiene rule holds that
"pseudo-context, generated explanations, and chain-of-thought are never passed
verbatim into the verifier. They are acquisition artifacts."

Nothing here creates an evidence edge. Per §7.2, "the sketch does not itself
create a support edge"; turning a record into candidate evidence is the
specialist layer's decision, not this module's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cover_kbc.types import ProgramType


class RecallOperationKind(str, Enum):
    """The probe families the proposal declares (§7.2, "Three probe families").

    Exactly three, because exactly three are specified. A fourth family would be
    an architecture change, not an implementation detail.
    """

    #: "The model writes a relation-focused memory sketch."
    PSEUDO_MEMORY = "pseudo_memory"
    #: "Self-ask decomposes a query into follow-up subquestions; in COVER, the
    #: follow-ups are answered by a frozen model, not by a search engine."
    SELF_ASK = "self_ask"
    #: "M11 rewrites the query using a missing facet/negative constraint instead
    #: of merely changing the random seed."
    QUERY_REWRITE = "query_rewrite"


class ParametricIndependenceGroup(str, Enum):
    """Structural provenance for a recall operation.

    Deliberately a **separate** enum from
    :class:`~cover_kbc.types.IndependenceGroup`. Reusing the core groups would
    silently enrol parametric recall into ``q(o) = g(o) / m(o)`` and change what
    the production system counts as independent support - exactly what shadow
    mode forbids. Mapping these onto core evidence groups is a decision for the
    specialist layer and Module 16, once consensus exists to make it.

    Repeated samples of one family share its group: three samples of a
    pseudo-memory sketch are one structural source, not three.
    """

    PSEUDO_MEMORY_SKETCH = "PSEUDO_MEMORY_SKETCH"
    SELF_ASK_DECOMPOSITION = "SELF_ASK_DECOMPOSITION"
    QUERY_REWRITE = "QUERY_REWRITE"


class MemorySource(str, Enum):
    """Where a record's text came from. There is exactly one legal answer."""

    FROZEN_MODEL_PARAMETRIC_MEMORY = "FROZEN_MODEL_PARAMETRIC_MEMORY"


class ExpectedOutputKind(str, Enum):
    """What shape an operation asks the model to produce.

    A hint for downstream parsing, never a guarantee: the model may ignore it,
    and :class:`ParametricMemoryRecord` records what actually came back.
    """

    PROSE = "PROSE"
    QA_PAIRS = "QA_PAIRS"
    OBJECT_LIST = "OBJECT_LIST"
    NUMBER = "NUMBER"


class ParseStatus(str, Enum):
    """How usable a record's raw output is.

    A status, not a judgement of truth. ``OK`` means "the model returned
    something of the requested shape", never "this is correct".
    """

    OK = "OK"
    #: The model returned nothing, or only whitespace.
    EMPTY = "EMPTY"
    #: The model explicitly declined - the answer schema's empty sentinel.
    ABSTAINED = "ABSTAINED"
    #: Output arrived but not in the requested structural shape.
    MALFORMED = "MALFORMED"
    #: The runtime raised. No text exists; nothing is fabricated to fill the gap.
    RUNTIME_ERROR = "RUNTIME_ERROR"


def prompt_digest(prompt: str, system_prompt: str = "") -> str:
    """Stable identity for an executed prompt.

    Lets a persisted record be matched to a re-rendered operation without
    storing the prompt twice, and makes a silent prompt change detectable.
    """
    payload = f"{system_prompt}\x00{prompt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ParametricRecallOperation:
    """One closed-book probe, fully rendered and ready to execute.

    Immutable and self-describing: everything needed to run it, attribute its
    cost and reproduce its prompt is here.
    """

    operation_id: str
    kind: RecallOperationKind
    independence_group: ParametricIndependenceGroup
    purpose: str
    prompt: str
    system_prompt: str
    #: Name of the decode profile used, for provenance. The profile itself lives
    #: in configuration; this records which one ran.
    decode_profile: str
    expected_output_kind: ExpectedOutputKind
    #: Which sample of this operation family this is. Resamples share the
    #: independence group above; they are never a second structural source.
    sample_index: int = 0
    #: Declared cost, so a future Module 20 can schedule without executing.
    estimated_calls: int = 1

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt, self.system_prompt)

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "independence_group": self.independence_group.value,
            "purpose": self.purpose,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "decode_profile": self.decode_profile,
            "expected_output_kind": self.expected_output_kind.value,
            "sample_index": self.sample_index,
            "estimated_calls": self.estimated_calls,
            "prompt_sha256": self.prompt_sha256,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ParametricRecallOperation":
        return cls(
            operation_id=str(payload["operation_id"]),
            kind=RecallOperationKind(payload["kind"]),
            independence_group=ParametricIndependenceGroup(payload["independence_group"]),
            purpose=str(payload["purpose"]),
            prompt=str(payload["prompt"]),
            system_prompt=str(payload["system_prompt"]),
            decode_profile=str(payload["decode_profile"]),
            expected_output_kind=ExpectedOutputKind(payload["expected_output_kind"]),
            sample_index=int(payload.get("sample_index", 0)),
            estimated_calls=int(payload.get("estimated_calls", 1)),
        )


@dataclass(frozen=True)
class ParametricRetrievalPlan:
    """Every probe M11 intends to run for one query, decided before any of them.

    A plan is produced without calling a model, so its cost is knowable in
    advance - which is what a future Module 20 needs in order to schedule it.
    M11 itself allocates nothing dynamically.
    """

    retrieval_version: str
    #: Upstream identities, carried so an artefact can never be read under the
    #: wrong architecture version.
    compiler_version: str
    profile_version: str
    #: Identity of the exact prompt program this plan was built from.
    program_sha256: str

    subject: str
    relation: str
    row_index: int
    program_type: ProgramType
    specialist_hint: str

    operations: tuple[ParametricRecallOperation, ...] = ()

    @property
    def max_operations(self) -> int:
        return len(self.operations)

    @property
    def estimated_calls(self) -> int:
        """Total declared cost, without executing anything."""
        return sum(op.estimated_calls for op in self.operations)

    @property
    def independence_groups(self) -> tuple[ParametricIndependenceGroup, ...]:
        """Distinct structural sources this plan can produce, in order."""
        seen: dict[ParametricIndependenceGroup, None] = {}
        for op in self.operations:
            seen.setdefault(op.independence_group, None)
        return tuple(seen)

    def to_json(self) -> dict[str, Any]:
        return {
            "retrieval_version": self.retrieval_version,
            "compiler_version": self.compiler_version,
            "profile_version": self.profile_version,
            "program_sha256": self.program_sha256,
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "program_type": self.program_type.value,
            "specialist_hint": self.specialist_hint,
            "max_operations": self.max_operations,
            "estimated_calls": self.estimated_calls,
            "operations": [op.to_json() for op in self.operations],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ParametricRetrievalPlan":
        return cls(
            retrieval_version=str(payload["retrieval_version"]),
            compiler_version=str(payload["compiler_version"]),
            profile_version=str(payload["profile_version"]),
            program_sha256=str(payload["program_sha256"]),
            subject=str(payload["SubjectEntity"]),
            relation=str(payload["Relation"]),
            row_index=int(payload["row_index"]),
            program_type=ProgramType(payload["program_type"]),
            specialist_hint=str(payload["specialist_hint"]),
            operations=tuple(
                ParametricRecallOperation.from_json(op) for op in payload["operations"]
            ),
        )


@dataclass(frozen=True)
class ParametricMemoryRecord:
    """What one probe recalled. **Unverified model output, always.**

    ``source`` and ``verified`` are not configurable and not per-record
    judgements: every string in ``raw_output`` was generated by the frozen model
    from its own weights, and nothing in Module 11 can establish that any of it
    is true. Establishing that is Module 4's job today and Module 17's later,
    and neither of them may be shown this text verbatim.
    """

    operation_id: str
    kind: RecallOperationKind
    independence_group: ParametricIndependenceGroup
    raw_output: str
    parse_status: ParseStatus

    model_id: str
    model_revision: str
    decode_profile: str
    prompt_sha256: str

    calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    latency_ms: float | None = None
    error: str | None = None
    sample_index: int = 0

    #: Fixed by construction. A record whose text came from anywhere else would
    #: be a different, non-compliant module.
    source: MemorySource = MemorySource.FROZEN_MODEL_PARAMETRIC_MEMORY
    #: Fixed by construction. Module 11 verifies nothing.
    verified: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.source is not MemorySource.FROZEN_MODEL_PARAMETRIC_MEMORY:
            raise ValueError(
                "a ParametricMemoryRecord may only carry frozen-model parametric "
                "memory; there is no external source in a closed-book system"
            )
        if self.verified:
            raise ValueError(
                "Module 11 never verifies. A verified record would mean the "
                "blind-verification invariant had been bypassed."
            )

    @property
    def usable(self) -> bool:
        """Whether downstream parsing has anything of the requested shape."""
        return self.parse_status is ParseStatus.OK

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "independence_group": self.independence_group.value,
            "source": self.source.value,
            "verified": self.verified,
            "parse_status": self.parse_status.value,
            "raw_output": self.raw_output,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "decode_profile": self.decode_profile,
            "prompt_sha256": self.prompt_sha256,
            "calls": self.calls,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens": self.prompt_tokens,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "sample_index": self.sample_index,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ParametricMemoryRecord":
        return cls(
            operation_id=str(payload["operation_id"]),
            kind=RecallOperationKind(payload["kind"]),
            independence_group=ParametricIndependenceGroup(payload["independence_group"]),
            raw_output=str(payload["raw_output"]),
            parse_status=ParseStatus(payload["parse_status"]),
            model_id=str(payload["model_id"]),
            model_revision=str(payload["model_revision"]),
            decode_profile=str(payload["decode_profile"]),
            prompt_sha256=str(payload["prompt_sha256"]),
            calls=int(payload.get("calls", 0)),
            generated_tokens=int(payload.get("generated_tokens", 0)),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            latency_ms=payload.get("latency_ms"),
            error=payload.get("error"),
            sample_index=int(payload.get("sample_index", 0)),
            source=MemorySource(payload.get("source", MemorySource.FROZEN_MODEL_PARAMETRIC_MEMORY.value)),
            verified=bool(payload.get("verified", False)),
        )


@dataclass(frozen=True)
class ParametricRetrievalResult:
    """Everything one query's parametric retrieval produced.

    Cost is summed from the records rather than read off a shared runtime
    counter, so M11's spend is attributable operation by operation and can never
    be confused with the production path's.
    """

    plan: ParametricRetrievalPlan
    records: tuple[ParametricMemoryRecord, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def total_calls(self) -> int:
        return sum(record.calls for record in self.records)

    @property
    def total_generated_tokens(self) -> int:
        return sum(record.generated_tokens for record in self.records)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(record.prompt_tokens for record in self.records)

    @property
    def usable_records(self) -> tuple[ParametricMemoryRecord, ...]:
        return tuple(record for record in self.records if record.usable)

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_json(),
            "records": [record.to_json() for record in self.records],
            "errors": list(self.errors),
            "total_calls": self.total_calls,
            "total_generated_tokens": self.total_generated_tokens,
            "total_prompt_tokens": self.total_prompt_tokens,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ParametricRetrievalResult":
        return cls(
            plan=ParametricRetrievalPlan.from_json(payload["plan"]),
            records=tuple(
                ParametricMemoryRecord.from_json(entry) for entry in payload["records"]
            ),
            errors=tuple(payload.get("errors", ())),
        )
