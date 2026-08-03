"""Hugging Face backend for frozen open-weight checkpoints.

``torch``/``transformers`` are imported lazily inside the constructor so the
data, evidence and control layers stay installable and testable on a machine
with no GPU stack. Nothing here downloads a model at import time.

Three things this backend has to get right:

**Multimodal checkpoints.** Both target models are released as
``*ForConditionalGeneration`` (Mistral3, Qwen3_5) with a vision tower, even
though our task is text-only. The loader tries the auto classes in order rather
than assuming ``AutoModelForCausalLM`` works, and records what it actually
materialised.

**Label tokenisation.** ``score_labels`` asserts that every label encodes to a
single token before using next-token logits. If any label is multi-token it
falls back to full sequence log-likelihood over the label strings. It never
silently compares first tokens - that would make "A" and "Australia"
indistinguishable.

**Parameter accounting.** ``loaded_parameter_count`` is a diagnostic. The
budget always uses the *published* count from configuration, and quantisation
never reduces it.

Rule compliance: frozen inference only. No ``.train()``, no optimiser, no
weight update, and no network access beyond the checkpoint download.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Sequence

from cover_kbc.models.base import (
    BaseRuntime,
    GenerationRequest,
    GenerationResult,
    LabelScoreRequest,
    LabelScoreResult,
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
        published_language_parameters: int | None = None,
        published_checkpoint_parameters: int | None = None,
        budget_count_parameters: int | None = None,
        parameter_source: str = "",
        parameter_source_verified: bool = False,
        max_memory: dict[str | int, str] | None = None,
    ) -> None:
        try:
            import torch
            import transformers
            from transformers import AutoTokenizer
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
                published_language_parameters=published_language_parameters,
                published_checkpoint_parameters=published_checkpoint_parameters,
                budget_count_parameters=budget_count_parameters,
                parameter_source=parameter_source,
                parameter_source_verified=parameter_source_verified,
            )
        )

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=trust_remote_code
        )

        load_kwargs: dict[str, Any] = {
            "revision": revision,
            "device_map": device_map,
            "trust_remote_code": trust_remote_code,
        }
        if torch_dtype and torch_dtype != "auto":
            load_kwargs["dtype"] = getattr(torch, torch_dtype)
        else:
            load_kwargs["dtype"] = "auto"
        if max_memory:
            load_kwargs["max_memory"] = max_memory
        if quantization:
            load_kwargs["quantization_config"] = self._quantization_config(quantization)

        self.model = self._load_model(transformers, model_id, load_kwargs)
        self.model.eval()

        # Record what was actually materialised, so the conservative budget
        # claim can be checked rather than trusted.
        components = sorted({name.split(".")[0] for name, _ in self.model.named_modules() if name})
        self._spec = replace(
            self._spec,
            loaded_neural_components=tuple(components[:16]),
            loaded_parameter_count=self.loaded_parameter_count(),
        )

        self.label_encoding: Any = None

    # -- loading -------------------------------------------------------------

    @staticmethod
    def _quantization_config(quantization: str):
        from transformers import BitsAndBytesConfig
        import torch

        name = quantization.lower()
        if name in {"nf4", "4bit", "int4"}:
            return BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
        if name in {"int8", "8bit"}:
            return BitsAndBytesConfig(load_in_8bit=True)
        raise ValueError(f"Unknown quantization {quantization!r}; expected nf4/int4 or int8")

    @staticmethod
    def _load_model(transformers, model_id: str, load_kwargs: dict[str, Any]):
        """Try the auto classes in order; a VLM checkpoint needs the right one.

        Both target checkpoints are ``*ForConditionalGeneration``, so assuming
        ``AutoModelForCausalLM`` would fail on them. Errors from every attempt
        are reported together rather than only the last.
        """
        candidates = [
            getattr(transformers, name, None)
            for name in ("AutoModelForCausalLM", "AutoModelForImageTextToText")
        ]
        errors: list[str] = []
        for auto_class in candidates:
            if auto_class is None:
                continue
            try:
                return auto_class.from_pretrained(model_id, **load_kwargs)
            except Exception as exc:  # noqa: BLE001 - collect and report all
                errors.append(f"{auto_class.__name__}: {type(exc).__name__}: {exc}")
        raise RuntimeError(
            f"Could not load {model_id} with any known auto class.\n  " + "\n  ".join(errors)
        )

    # -- helpers -------------------------------------------------------------

    def loaded_parameter_count(self) -> int:
        """Parameters actually materialised.

        A diagnostic, not the budget number: quantised weights are packed, so
        this under-reports. The challenge counts the *published* total, which
        configuration supplies.
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

    def inspect_labels(self, labels: dict[str, str]):
        """Encode the label set once and cache the result."""
        from cover_kbc.verification import inspect_label_encoding

        if self.label_encoding is None or dict(self.label_encoding.labels) != labels:
            self.label_encoding = inspect_label_encoding(self.tokenizer, labels)
        return self.label_encoding

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
        """Score the label set, choosing the strategy from real tokenisation.

        Single-token labels use next-token logits (one forward pass). Otherwise
        the fallback scores each label's full sequence log-likelihood, which is
        correct but costs one forward pass per label.
        """
        encoding = self.inspect_labels(dict(request.labels))
        if encoding.single_token:
            return self._score_next_token(request, encoding)
        return self._score_sequence(request, encoding)

    def _score_next_token(self, request: LabelScoreRequest, encoding) -> LabelScoreResult:
        torch = self._torch
        start = time.perf_counter()
        self.calls += 1

        text = self._render(request)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            logits = self.model(**inputs).logits[0, -1, :]

        scores = {
            label: float(logits[ids[0]].item()) for label, ids in encoding.token_ids.items()
        }
        return LabelScoreResult(
            logits=scores,
            model_id=self.spec.model_id,
            prompt_tokens=int(inputs["input_ids"].shape[-1]),
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )

    def _score_sequence(self, request: LabelScoreRequest, encoding) -> LabelScoreResult:
        """Sum log P(token) over each label's full token sequence.

        Used when a label does not encode to a single token. Comparing only
        first tokens would be wrong, and silently doing so is the specific
        failure this guards against.
        """
        torch = self._torch
        start = time.perf_counter()

        text = self._render(request)
        prompt_ids = self.tokenizer(text, return_tensors="pt")["input_ids"]
        scores: dict[str, float] = {}

        for label, ids in encoding.token_ids.items():
            self.calls += 1
            continuation = torch.tensor([list(ids)], dtype=prompt_ids.dtype)
            full = torch.cat([prompt_ids, continuation], dim=-1).to(self.model.device)
            with torch.no_grad():
                logits = self.model(input_ids=full).logits[0]
            log_probs = torch.log_softmax(logits, dim=-1)
            start_index = prompt_ids.shape[-1]
            total = 0.0
            for offset, token_id in enumerate(ids):
                total += float(log_probs[start_index + offset - 1, token_id].item())
            scores[label] = total

        return LabelScoreResult(
            logits=scores,
            model_id=self.spec.model_id,
            prompt_tokens=int(prompt_ids.shape[-1]),
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )

    def hidden_states(self, prompt: str, layers: Sequence[int]) -> Any:
        """Intermediate hidden states, for the experimental DoLa branch.

        Implemented because the seam is cheap; nothing in the architecture
        depends on it.
        """
        torch = self._torch
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            out = self.model(**inputs, output_hidden_states=True)
        return {layer: out.hidden_states[layer] for layer in layers}
