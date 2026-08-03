"""Deterministic non-neural backends for testing and plumbing smoke runs.

Neither backend contains factual knowledge, and neither may ever be used to
produce a reported system result.

:class:`NullRuntime`
    Always abstains.  Running the full pipeline on it yields the *abstain
    baseline* (predict nothing for every query), which exercises the whole
    data/evidence/output path - including empty-set handling - without a GPU.

:class:`ScriptedRuntime`
    Replays pre-recorded outputs keyed by view and query.  Used by tests, and
    able to replay a captured run for debugging without re-running a model.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from cover_kbc.models.base import (
    BaseRuntime,
    GenerationRequest,
    GenerationResult,
    LabelScoreRequest,
    LabelScoreResult,
    ModelSpec,
)

#: The token every view asks for when it has nothing to report.
ABSTAIN_OUTPUT = "NONE"


def approximate_token_count(text: str) -> int:
    """Whitespace-based token estimate for backends without a tokenizer.

    Only used for budget accounting in offline runs; real backends report real
    counts from their own tokenizer.
    """
    return len(text.split())


class NullRuntime(BaseRuntime):
    """A backend that abstains on every call.

    Produces the abstain baseline: empty predictions everywhere.  Under the
    official evaluator that scores macro-precision 1.0 (an empty prediction is
    defined as precise) and macro-recall equal to the fraction of queries whose
    gold set is genuinely empty.  It is a plumbing check and a floor, not a
    system result.
    """

    def __init__(self, model_id: str = "offline/null") -> None:
        super().__init__(
            ModelSpec(
                model_id=model_id,
                published_total_parameters=0,
                family="offline",
                role="stub",
                is_neural=False,
                supports_logits=True,
                notes="Non-neural abstain stub. Contains no factual knowledge.",
            )
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        start = time.perf_counter()
        self.calls += 1
        return GenerationResult(
            text=ABSTAIN_OUTPUT,
            model_id=self.spec.model_id,
            prompt_tokens=approximate_token_count(request.prompt),
            generated_tokens=1,
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )

    def score_labels(self, request: LabelScoreRequest) -> LabelScoreResult:
        """Uniform logits: the stub has no opinion about any candidate."""
        self.calls += 1
        return LabelScoreResult(
            logits={label: 0.0 for label in request.labels},
            model_id=self.spec.model_id,
            prompt_tokens=approximate_token_count(request.prompt),
        )


class ScriptedRuntime(BaseRuntime):
    """Replay recorded generations, selected by request metadata.

    Scripts are keyed by ``(view_id, subject, relation)`` and hold the ordered
    outputs for successive runs of that view.  A missing key abstains rather
    than raising, so a partial fixture still exercises the pipeline.
    """

    def __init__(
        self,
        script: Mapping[tuple[str, str, str], list[str]] | None = None,
        *,
        model_id: str = "offline/scripted",
        label_scores: Mapping[tuple[str, str, str], dict[str, float]] | None = None,
        fallback: Callable[[GenerationRequest], str] | None = None,
    ) -> None:
        super().__init__(
            ModelSpec(
                model_id=model_id,
                published_total_parameters=0,
                family="offline",
                role="stub",
                is_neural=False,
                supports_logits=True,
                notes="Deterministic replay of recorded outputs. No factual knowledge of its own.",
            )
        )
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.label_scores = dict(label_scores or {})
        self.fallback = fallback
        self._cursor: dict[tuple[str, str, str], int] = {}
        self.seen_prompts: list[str] = []

    @staticmethod
    def _key(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(metadata.get("view_id", "")),
            str(metadata.get("subject", "")),
            str(metadata.get("relation", "")),
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        self.seen_prompts.append(request.prompt)
        key = self._key(request.metadata)
        outputs = self.script.get(key)

        if outputs:
            index = self._cursor.get(key, 0)
            text = outputs[min(index, len(outputs) - 1)]
            self._cursor[key] = index + 1
        elif self.fallback is not None:
            text = self.fallback(request)
        else:
            text = ABSTAIN_OUTPUT

        generated = approximate_token_count(text)
        self.generated_tokens += generated
        return GenerationResult(
            text=text,
            model_id=self.spec.model_id,
            prompt_tokens=approximate_token_count(request.prompt),
            generated_tokens=generated,
        )

    def score_labels(self, request: LabelScoreRequest) -> LabelScoreResult:
        self.calls += 1
        key = self._key(request.metadata)
        logits = self.label_scores.get(key)
        return LabelScoreResult(
            logits=dict(logits) if logits else {label: 0.0 for label in request.labels},
            model_id=self.spec.model_id,
            prompt_tokens=approximate_token_count(request.prompt),
        )
