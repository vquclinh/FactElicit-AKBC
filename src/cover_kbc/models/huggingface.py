"""Optional Hugging Face backend for frozen open-weight checkpoints.

``torch``/``transformers`` are imported lazily inside the constructor so the
data and evaluation foundation stays installable and testable on a machine with
no GPU stack.

Nothing here downloads a model at import time.  A checkpoint is fetched only
when a runtime is actually constructed, and its published parameter count comes
from configuration - never guessed from the model name.

Rule compliance: this backend performs frozen inference only.  It never calls
``.train()``, never updates weights, and reaches no network resource other than
the checkpoint download that ``transformers`` performs.
"""

from __future__ import annotations

import time
from typing import Any

from cover_kbc.models.base import (
    BaseRuntime,
    GenerationRequest,
    GenerationResult,
    LabelScoreRequest,
    LabelScoreResult,
    LogitsUnavailable,
    ModelSpec,
)


class HuggingFaceRuntime(BaseRuntime):
    """Frozen causal-LM inference with generate and label-scoring modes."""

    def __init__(
        self,
        model_id: str,
        *,
        published_total_parameters: int | None,
        revision: str = "main",
        family: str = "",
        license: str = "",
        role: str = "generator",
        device_map: str = "auto",
        torch_dtype: str = "auto",
        quantization: str | None = None,
        trust_remote_code: bool = False,
        source: str = "",
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "The Hugging Face backend needs the optional 'hf' extra: "
                "pip install -e '.[hf]'"
            ) from exc

        super().__init__(
            ModelSpec(
                model_id=model_id,
                published_total_parameters=published_total_parameters,
                family=family,
                revision=revision,
                license=license,
                role=role,
                supports_logits=True,
                supports_hidden_states=True,
                quantization=quantization,
                source=source,
            )
        )

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=trust_remote_code
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            revision=revision,
            device_map=device_map,
            torch_dtype=torch_dtype,
            trust_remote_code=trust_remote_code,
        )
        self.model.eval()

    # -- helpers -------------------------------------------------------------

    def loaded_parameter_count(self) -> int:
        """Parameters actually materialised, for cross-checking the config.

        This is a diagnostic, not the budget number: the challenge counts the
        *published* total, which the configuration records.
        """
        return sum(p.numel() for p in self.model.parameters())

    def _render(self, request: GenerationRequest | LabelScoreRequest) -> str:
        """Apply the checkpoint's chat template when it has one."""
        template = getattr(self.tokenizer, "chat_template", None)
        if not template:
            if request.system_prompt:
                return f"{request.system_prompt}\n\n{request.prompt}"
            return request.prompt
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    # -- interface -----------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        torch = self._torch
        start = time.perf_counter()
        self.calls += 1

        if request.decode.seed is not None:
            torch.manual_seed(request.decode.seed)

        text = self._render(request)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        prompt_tokens = int(inputs["input_ids"].shape[-1])

        kwargs: dict[str, Any] = {
            "max_new_tokens": request.decode.max_new_tokens,
            "do_sample": not request.decode.deterministic,
            "pad_token_id": self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
        }
        if not request.decode.deterministic:
            kwargs["temperature"] = request.decode.temperature
            kwargs["top_p"] = request.decode.top_p

        with torch.no_grad():
            output = self.model.generate(**inputs, **kwargs)

        new_tokens = output[0][prompt_tokens:]
        completion = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        for stop in request.stop:
            if stop and stop in completion:
                completion = completion.split(stop)[0]

        generated = int(new_tokens.shape[-1])
        self.generated_tokens += generated
        return GenerationResult(
            text=completion.strip(),
            model_id=self.spec.model_id,
            prompt_tokens=prompt_tokens,
            generated_tokens=generated,
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )

    def score_labels(self, request: LabelScoreRequest) -> LabelScoreResult:
        """Read next-token logits for each label's first token.

        This is the raw ``z_j`` of spec section 10.2.  Calibration happens
        downstream so this method stays a faithful model read-out.
        """
        torch = self._torch
        start = time.perf_counter()
        self.calls += 1

        text = self._render(request)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            logits = self.model(**inputs).logits[0, -1, :]

        scores: dict[str, float] = {}
        for label, continuation in request.labels.items():
            token_ids = self.tokenizer.encode(continuation, add_special_tokens=False)
            if not token_ids:
                raise LogitsUnavailable(
                    f"Label {label!r} maps to continuation {continuation!r}, "
                    "which the tokenizer encodes to zero tokens"
                )
            scores[label] = float(logits[token_ids[0]].item())

        return LabelScoreResult(
            logits=scores,
            model_id=self.spec.model_id,
            prompt_tokens=int(inputs["input_ids"].shape[-1]),
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )
