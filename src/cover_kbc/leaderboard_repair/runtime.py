"""Model-call helpers with separate L7-L9 accounting."""

from __future__ import annotations

from cover_kbc.models.base import GenerationRequest, LabelScoreRequest, LMRuntime
from cover_kbc.types import DecodeProfile

from cover_kbc.leaderboard_repair.types import RepairCall, RowBudget


class RepairCaller:
    """Spend post-pipeline calls against a row-local cap."""

    def __init__(
        self,
        *,
        enumerator: LMRuntime,
        verifier: LMRuntime,
        relation: str,
        subject: str,
        row_budget: RowBudget,
    ) -> None:
        self.enumerator = enumerator
        self.verifier = verifier
        self.relation = relation
        self.subject = subject
        self.row_budget = row_budget

    def generate(
        self,
        *,
        role: str,
        layer: str,
        feature: str,
        prompt: str,
        system_prompt: str | None = None,
        max_new_tokens: int = 192,
        view_id: str,
    ) -> str | None:
        if not self.row_budget.can_spend():
            return None
        runtime = self.enumerator if role == "enumerator" else self.verifier
        result = runtime.generate(
            GenerationRequest(
                prompt=prompt,
                system_prompt=system_prompt,
                decode=DecodeProfile(
                    name=f"{feature}_greedy",
                    temperature=0.0,
                    top_p=1.0,
                    max_new_tokens=max_new_tokens,
                ),
                metadata={
                    "view_id": view_id,
                    "subject": self.subject,
                    "relation": self.relation,
                    "repair_feature": feature,
                },
            )
        )
        call = RepairCall(
            layer=layer,
            feature=feature,
            relation=self.relation,
            subject=self.subject,
            model_role=role,
            model_id=result.model_id,
            prompt=prompt,
            output=result.text,
            prompt_tokens=int(result.prompt_tokens or 0),
            generated_tokens=int(result.generated_tokens or 0),
        )
        self.row_budget.record(call)
        return result.text

    def score_labels(
        self,
        *,
        layer: str,
        feature: str,
        prompt: str,
        labels: dict[str, str],
        view_id: str,
    ) -> tuple[str, dict[str, float]] | None:
        if not self.row_budget.can_spend():
            return None
        result = self.verifier.score_labels(
            LabelScoreRequest(
                prompt=prompt,
                labels=labels,
                metadata={
                    "view_id": view_id,
                    "subject": self.subject,
                    "relation": self.relation,
                    "repair_feature": feature,
                },
            )
        )
        probs = result.probabilities()
        label = max(probs, key=lambda item: (probs[item], item)) if probs else "UNKNOWN"
        call = RepairCall(
            layer=layer,
            feature=feature,
            relation=self.relation,
            subject=self.subject,
            model_role="verifier",
            model_id=result.model_id,
            prompt=prompt,
            output=label,
            prompt_tokens=int(result.prompt_tokens or 0),
            generated_tokens=0,
            label_probabilities=probs,
        )
        self.row_budget.record(call)
        return label, probs
