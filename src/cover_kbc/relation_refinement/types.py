"""Typed records emitted by the relation refinement stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class CandidateSignal:
    """One pre-final candidate made available to post-pipeline refinement."""

    value: str
    source: str
    support: int = 0
    verifier_label: str = ""
    verifier_margin: float | None = None
    status: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "support": self.support,
            "verifier_label": self.verifier_label,
            "verifier_margin": self.verifier_margin,
            "status": self.status,
        }


@dataclass
class RefinementCall:
    """One post-pipeline model call, accounted separately from M20/M21."""

    layer: str
    feature: str
    relation: str
    subject: str
    model_role: str
    model_id: str
    prompt: str
    output: str
    prompt_tokens: int = 0
    generated_tokens: int = 0
    label_probabilities: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "feature": self.feature,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "model_role": self.model_role,
            "model_id": self.model_id,
            "prompt": self.prompt,
            "output": self.output,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "label_probabilities": dict(sorted(self.label_probabilities.items())),
        }


@dataclass
class RowRefinementRecord:
    """Audit record for one refined or inspected row."""

    subject: str
    relation: str
    row_index: int
    before: list[str]
    after: list[str]
    features: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    calls: list[RefinementCall] = field(default_factory=list)
    budget_cap: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def calls_used(self) -> int:
        return len(self.calls)

    def add_feature(self, name: str) -> None:
        if name not in self.features:
            self.features.append(name)

    def add_decision(self, feature: str, decision: str, **detail: Any) -> None:
        self.add_feature(feature)
        self.decisions.append({"feature": feature, "decision": decision, **detail})

    def to_json(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject,
            "Relation": self.relation,
            "row_index": self.row_index,
            "before": list(self.before),
            "after": list(self.after),
            "changed": self.changed,
            "features": list(self.features),
            "decisions": list(self.decisions),
            "calls_used": self.calls_used,
            "budget_cap": self.budget_cap,
            "skipped": list(self.skipped),
            "calls": [call.to_json() for call in self.calls],
        }


@dataclass(frozen=True)
class RefinementResult:
    """Output rows plus refinement audit records."""

    predictions: Sequence[Any]
    records: Sequence[RowRefinementRecord]
    accounting: dict[str, Any]


@dataclass
class RowBudget:
    """Per-row cap for post-pipeline refinement calls."""

    cap: int
    calls: list[RefinementCall] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.cap - len(self.calls))

    def can_spend(self, amount: int = 1) -> bool:
        return self.remaining >= amount

    def record(self, call: RefinementCall) -> None:
        if not self.can_spend():
            raise RuntimeError("refinement call budget exceeded")
        self.calls.append(call)
