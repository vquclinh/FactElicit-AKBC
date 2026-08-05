"""The checkpoint-native tokenizer for Mistral-Small-3.2 (Tekken).

``Mistral-Small-3.2-24B-Instruct-2506`` ships a **Tekken** tokenizer
(``tekken.json``), not a Hugging Face ``tokenizer.json``. ``AutoTokenizer`` will
happily load it - transformers logs *"Converting tekken.json to
tokenizer.json"* - but the converted artefact does not round-trip this
checkpoint's vocabulary. In a real Colab run that produced generations like::

    oun Ġrebell ì§ ption ĠAn ĠAn ĠAn ĠAn ...

The ``Ġ`` is a GPT-2 byte-BPE space marker. Seeing it in *decoded* output means
the decoder is emitting token pieces from a vocabulary the model never wrote in,
and the degeneration into a repeated token follows from feeding the model an
input it cannot parse - no chat structure, wrong ids.

Mistral's own guidance for this checkpoint is to use ``mistral-common`` and
``MistralTokenizer.from_hf_hub(...)`` for tokenisation and chat encoding, so
this adapter does exactly that.

It is a **runtime compatibility shim, not an architecture change**: it presents
the small slice of the tokenizer surface :class:`HuggingFaceRuntime` already
uses, so nothing above the runtime changes, and Qwen keeps its untouched
``AutoTokenizer`` path.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


class TokenBatch(dict):
    """A ``dict`` of tensors that also answers ``.to(device)``.

    ``AutoTokenizer`` returns a ``BatchEncoding``, and the runtime calls
    ``.to(device)`` on it. Matching that shape keeps every caller identical for
    both tokenizer backends.
    """

    def to(self, device) -> "TokenBatch":
        return TokenBatch(
            {
                key: value.to(device) if hasattr(value, "to") else value
                for key, value in self.items()
            }
        )


class MistralTokenizerUnavailable(RuntimeError):
    """The checkpoint needs ``mistral-common`` and it is not importable.

    Raised rather than falling back to ``AutoTokenizer``: a silent fallback is
    what produced corrupt text in the first place, and a run that looks like it
    worked is worse than one that stops.
    """


def requires_mistral_tokenizer(model_id: str, family: str = "") -> bool:
    """Does this checkpoint need the Tekken/mistral-common tokenizer?

    Keyed on the declared family first, because the config states the model
    role explicitly; the id is a fallback for profiles that omit it.
    """
    return "mistral" in f"{family} {model_id}".lower()


#: The Tekken vocabulary file shipped by Mistral-Small-3.2.
TEKKEN_FILE = "tekken.json"


def _load_from_hub(tokenizer_cls, model_id: str, revision: str | None):
    """Load the checkpoint's tokenizer, honouring the frozen revision.

    ``from_hf_hub`` is the documented entry point, but its ``revision``
    parameter is not present in every ``mistral-common`` release. Rather than
    quietly resolving ``main`` - which would unpin the frozen checkpoint - fall
    back to downloading ``tekken.json`` at the exact revision and loading that.
    """
    if not revision:
        return tokenizer_cls.from_hf_hub(model_id)

    import inspect

    try:
        parameters = inspect.signature(tokenizer_cls.from_hf_hub).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        parameters = {}
    if "revision" in parameters:
        return tokenizer_cls.from_hf_hub(model_id, revision=revision)

    from huggingface_hub import hf_hub_download

    path = hf_hub_download(repo_id=model_id, filename=TEKKEN_FILE, revision=revision)
    return tokenizer_cls.from_file(path)


def _keep_special_tokens():
    """``SpecialTokenPolicy.KEEP`` if this ``mistral-common`` exposes it.

    Older releases have no policy argument; returning ``None`` then means the
    caller falls back to the default decode rather than crashing.
    """
    try:
        from mistral_common.tokens.tokenizers.base import SpecialTokenPolicy
    except ImportError:  # pragma: no cover - version dependent
        return None
    return getattr(SpecialTokenPolicy, "KEEP", None)


class MistralCommonTokenizer:
    """Adapter over ``mistral_common``'s :class:`MistralTokenizer`.

    Exposes only what the runtime needs:

    * :meth:`encode_chat` - the correct path for an instruct checkpoint, which
      encodes structured messages rather than a pre-rendered string;
    * :meth:`encode` / :meth:`decode` / ``__call__`` - so label scoring and any
      plain-text path use the same vocabulary as generation;
    * ``eos_token_id`` / ``pad_token_id`` - for ``generate`` kwargs.
    """

    #: Marks this as a chat-capable tokenizer without a Jinja template, so the
    #: runtime routes through :meth:`encode_chat` instead of string rendering.
    chat_template = "mistral-common"

    def __init__(self, model_id: str, revision: str | None = None) -> None:
        try:
            from mistral_common.protocol.instruct.request import ChatCompletionRequest
            from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise MistralTokenizerUnavailable(
                f"{model_id} ships a Tekken tokenizer, which AutoTokenizer cannot "
                "round-trip correctly. Install the optional extra: "
                "pip install -e '.[hf]'  (brings mistral-common>=1.6.2)"
            ) from exc

        self._request_cls = ChatCompletionRequest
        self.model_id = model_id
        self.revision = revision or ""
        self._tokenizer = _load_from_hub(MistralTokenizer, model_id, revision)

    # -- chat ---------------------------------------------------------------

    def encode_chat(self, prompt: str, system_prompt: str = "") -> list[int]:
        """Encode a system/user exchange the way this checkpoint expects.

        Structured messages, not a rendered string: the instruct format is the
        tokenizer's business, and hand-rolling it is how the wrong control
        tokens get in.
        """
        from mistral_common.protocol.instruct.messages import (
            SystemMessage,
            UserMessage,
        )

        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(UserMessage(content=prompt))
        encoded = self._tokenizer.encode_chat_completion(
            self._request_cls(messages=messages)
        )
        return list(encoded.tokens)

    def apply_chat_template(
        self,
        messages: Sequence[dict],
        tokenize: bool = False,
        add_generation_prompt: bool = True,
        **_: Any,
    ) -> str:
        """``AutoTokenizer``-shaped rendering, for the label-scoring path.

        Label scoring tokenises a rendered string, so it must see the *same*
        instruct framing generation uses. Rendering through the real chat
        encoder and decoding back guarantees that, rather than reconstructing
        the format by hand.
        """
        system = next(
            (m.get("content", "") for m in messages if m.get("role") == "system"), ""
        )
        user = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"), ""
        )
        ids = self.encode_chat(user, system)
        if tokenize:
            return ids
        return self.decode(ids, skip_special_tokens=False)

    # -- plain text ---------------------------------------------------------

    @property
    def _inner(self):
        """The underlying byte-level tokenizer, for non-chat encode/decode."""
        return self._tokenizer.instruct_tokenizer.tokenizer

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return list(self._inner.encode(text, bos=add_special_tokens, eos=False))

    def decode(self, token_ids: Iterable[int], skip_special_tokens: bool = True) -> str:
        ids = [int(t) for t in token_ids]
        if not skip_special_tokens:
            policy = _keep_special_tokens()
            if policy is not None:
                return self._inner.decode(ids, special_token_policy=policy)
        return self._inner.decode(ids)

    def __call__(self, text: str, return_tensors: str | None = None, **_: Any):
        """Minimal ``AutoTokenizer``-shaped call for the plain-text path."""
        ids = self.encode(text, add_special_tokens=True)
        if return_tensors == "pt":
            import torch

            return TokenBatch({"input_ids": torch.tensor([ids], dtype=torch.long)})
        return TokenBatch({"input_ids": [ids]})

    # -- ids ----------------------------------------------------------------

    @property
    def eos_token_id(self) -> int | None:
        return getattr(self._inner, "eos_id", None)

    @property
    def pad_token_id(self) -> int | None:
        # Tekken has no distinct pad id; padding on EOS is the usual convention
        # and matches what the runtime already does for other checkpoints.
        return getattr(self._inner, "pad_id", None) or self.eos_token_id


def build_tokenizer(
    model_id: str,
    *,
    revision: str = "",
    family: str = "",
    trust_remote_code: bool = False,
    tokenizer_backend: str = "auto",
):
    """Return the tokenizer this checkpoint actually needs.

    ``tokenizer_backend`` may pin the choice explicitly (``auto``,
    ``mistral_common``, ``huggingface``); ``auto`` selects mistral-common only
    for Mistral-family checkpoints, so every other model - Qwen included -
    keeps the untouched ``AutoTokenizer`` path.
    """
    backend = (tokenizer_backend or "auto").lower()
    if backend not in {"auto", "mistral_common", "huggingface"}:
        raise ValueError(
            f"Unknown tokenizer_backend {tokenizer_backend!r}; "
            "expected auto, mistral_common or huggingface"
        )

    use_mistral = backend == "mistral_common" or (
        backend == "auto" and requires_mistral_tokenizer(model_id, family)
    )
    if use_mistral:
        return MistralCommonTokenizer(model_id, revision=revision or None)

    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        model_id, revision=revision or "main", trust_remote_code=trust_remote_code
    )


def chat_token_ids(tokenizer, prompt: str, system_prompt: str = "") -> Sequence[int] | None:
    """Chat-encoded ids when the tokenizer encodes chat natively, else ``None``.

    ``None`` means "use the ordinary render-then-tokenize path", which is what
    every ``AutoTokenizer``-backed checkpoint does.
    """
    encode_chat = getattr(tokenizer, "encode_chat", None)
    if encode_chat is None:
        return None
    return encode_chat(prompt, system_prompt)
