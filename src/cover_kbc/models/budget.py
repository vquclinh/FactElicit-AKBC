"""Conservative inference-time parameter-budget audit (spec section 2.2).

The challenge rule: the total published neural parameters used at inference
must not exceed 32B.  Quantisation does not reduce the counted size, and an MoE
model counts by total published parameters, not active parameters.

Two deliberate design choices:

* Counts are supplied by configuration, not inferred from a model name.  A
  marketing suffix is not evidence, and this file must never guess.
* An unknown count is a **failure**, not a pass.  A profile with a missing
  count cannot be enabled until somebody records the published figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from cover_kbc.models.base import ModelSpec

#: Official inference-time budget: 32 billion total published parameters.
PARAMETER_BUDGET = 32_000_000_000


@dataclass
class BudgetAudit:
    """Result of auditing a set of neural components against the 32B budget."""

    specs: tuple[ModelSpec, ...]
    budget: int = PARAMETER_BUDGET
    problems: list[str] = field(default_factory=list)

    @property
    def counted_specs(self) -> list[ModelSpec]:
        """Distinct neural components; the same checkpoint is counted once.

        A generator and verifier that share one checkpoint load one set of
        weights, so double counting would be wrong.
        """
        seen: set[str] = set()
        out = []
        for spec in self.specs:
            if not spec.is_neural or spec.model_id in seen:
                continue
            seen.add(spec.model_id)
            out.append(spec)
        return out

    @property
    def total_parameters(self) -> int | None:
        counted = self.counted_specs
        if any(s.published_total_parameters is None for s in counted):
            return None
        return sum(s.published_total_parameters or 0 for s in counted)

    @property
    def passed(self) -> bool:
        total = self.total_parameters
        return not self.problems and total is not None and total <= self.budget

    def to_json(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "total_parameters": self.total_parameters,
            "total_billions": None if self.total_parameters is None else self.total_parameters / 1e9,
            "passed": self.passed,
            "problems": list(self.problems),
            "components": [s.to_json() for s in self.counted_specs],
        }

    def summary(self) -> str:
        lines = [f"Parameter budget: {self.budget / 1e9:.0f}B total published parameters"]
        for spec in self.counted_specs:
            billions = "UNKNOWN" if spec.billions is None else f"{spec.billions:.2f}B"
            lines.append(f"  - {spec.model_id} [{spec.role}] {billions}  {spec.source or ''}".rstrip())
        total = self.total_parameters
        lines.append(
            f"  total: {'UNKNOWN' if total is None else f'{total / 1e9:.2f}B'}"
        )
        for problem in self.problems:
            lines.append(f"  PROBLEM: {problem}")
        lines.append(f"  RESULT: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def audit_parameter_budget(
    specs: Sequence[ModelSpec], budget: int = PARAMETER_BUDGET
) -> BudgetAudit:
    """Audit neural components against the challenge budget.

    Fails when a count is missing, when a count is not positive, or when the
    conservative total exceeds the budget.
    """
    audit = BudgetAudit(specs=tuple(specs), budget=budget)

    for spec in audit.counted_specs:
        if spec.published_total_parameters is None:
            audit.problems.append(
                f"{spec.model_id}: published parameter count is unrecorded. "
                "Record the official published total before enabling this profile."
            )
        elif spec.published_total_parameters <= 0:
            audit.problems.append(
                f"{spec.model_id}: published parameter count must be positive, "
                f"got {spec.published_total_parameters}"
            )
        # Quantisation is recorded on the spec for reproducibility, but it is
        # intentionally not consulted here: it does not reduce the counted size.

    total = audit.total_parameters
    if total is not None and total > budget:
        audit.problems.append(
            f"total published parameters {total / 1e9:.2f}B exceeds the "
            f"{budget / 1e9:.0f}B inference-time budget"
        )
    return audit
