"""Real-runtime tokenizer compatibility for the frozen Mistral enumerator.

The defect these tests pin down was found in a live Colab run of the
architecture-freeze commit: ``Mistral-Small-3.2-24B-Instruct-2506`` ships a
**Tekken** tokenizer, ``AutoTokenizer`` silently converted it ("Converting
tekken.json to tokenizer.json"), and the enumerator emitted GPT-2 byte-BPE
debris - ``oun Ġrebell ì§ ption ĠAn ĠAn ĠAn ...`` - until it hit
``max_new_tokens``.

No weights are downloaded here. Everything below runs against fakes injected
into :data:`sys.modules`, because the defect is *structural*: the runtime built
the wrong tokenizer and handed the model a rendered string instead of the
checkpoint's own chat encoding. Both facts are observable without a GPU.

The fake Tekken tokenizer deliberately uses a disjoint vocabulary from the fake
converted one, so a test that decodes with the wrong table gets visible
``Ġ``-garbage rather than a silently plausible string - the same signature as
the real failure.
"""

from __future__ import annotations

import sys
import types

import pytest

from cover_kbc.models.mistral_tokenizer import (
    MistralCommonTokenizer,
    MistralTokenizerUnavailable,
    TokenBatch,
    build_tokenizer,
    chat_token_ids,
    requires_mistral_tokenizer,
)

MISTRAL_ID = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
MISTRAL_REV = "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
QWEN_ID = "Qwen/Qwen3.5-4B"
QWEN_REV = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"


# --------------------------------------------------------------------------
# Fake mistral-common
# --------------------------------------------------------------------------


class _FakeTekken:
    """A byte-level tokenizer with its own private vocabulary.

    Ids are ``ord(ch) + 1000`` so they cannot be confused with the fake
    converted tokenizer's ids, and decoding a foreign id is *visible*.
    """

    bos_id = 1
    eos_id = 2
    pad_id = None

    def encode(self, text: str, bos: bool = False, eos: bool = False) -> list[int]:
        ids = [ord(ch) + 1000 for ch in text]
        if bos:
            ids = [self.bos_id, *ids]
        if eos:
            ids = [*ids, self.eos_id]
        return ids

    def decode(self, ids) -> str:
        out = []
        for token in ids:
            if token in (self.bos_id, self.eos_id):
                continue
            if token < 1000:
                # A foreign id: the converted-vocabulary signature.
                out.append("Ġ?")
            else:
                out.append(chr(token - 1000))
        return "".join(out)


class _FakeInstructTokenizer:
    def __init__(self) -> None:
        self.tokenizer = _FakeTekken()


class _FakeEncoded:
    def __init__(self, tokens: list[int]) -> None:
        self.tokens = tokens


class _FakeMistralTokenizer:
    """Stands in for ``mistral_common``'s ``MistralTokenizer``."""

    from_hf_hub_calls: list[tuple[str, dict]] = []

    def __init__(self) -> None:
        self.instruct_tokenizer = _FakeInstructTokenizer()

    @classmethod
    def from_hf_hub(cls, model_id: str, revision: str | None = None):
        kwargs = {"revision": revision} if revision is not None else {}
        cls.from_hf_hub_calls.append((model_id, kwargs))
        return cls()

    def encode_chat_completion(self, request):
        # A recognisable instruct framing, so a test can assert the model saw
        # structured messages rather than a bare prompt string.
        parts = []
        for message in request.messages:
            parts.append(f"[{message.role.upper()}]{message.content}[/{message.role.upper()}]")
        text = "".join(parts)
        return _FakeEncoded([_FakeTekken.bos_id, *self.instruct_tokenizer.tokenizer.encode(text)])


class _FakeMessage:
    role = "message"

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeSystemMessage(_FakeMessage):
    role = "system"


class _FakeUserMessage(_FakeMessage):
    role = "user"


class _FakeChatCompletionRequest:
    def __init__(self, messages) -> None:
        self.messages = list(messages)


def _install_mistral_common(monkeypatch) -> None:
    """Inject a fake ``mistral_common`` package tree into ``sys.modules``."""
    root = types.ModuleType("mistral_common")
    protocol = types.ModuleType("mistral_common.protocol")
    instruct = types.ModuleType("mistral_common.protocol.instruct")
    request_mod = types.ModuleType("mistral_common.protocol.instruct.request")
    messages_mod = types.ModuleType("mistral_common.protocol.instruct.messages")
    tokens = types.ModuleType("mistral_common.tokens")
    tokenizers = types.ModuleType("mistral_common.tokens.tokenizers")
    mistral_mod = types.ModuleType("mistral_common.tokens.tokenizers.mistral")

    request_mod.ChatCompletionRequest = _FakeChatCompletionRequest
    messages_mod.SystemMessage = _FakeSystemMessage
    messages_mod.UserMessage = _FakeUserMessage
    mistral_mod.MistralTokenizer = _FakeMistralTokenizer

    for name, module in {
        "mistral_common": root,
        "mistral_common.protocol": protocol,
        "mistral_common.protocol.instruct": instruct,
        "mistral_common.protocol.instruct.request": request_mod,
        "mistral_common.protocol.instruct.messages": messages_mod,
        "mistral_common.tokens": tokens,
        "mistral_common.tokens.tokenizers": tokenizers,
        "mistral_common.tokens.tokenizers.mistral": mistral_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    _FakeMistralTokenizer.from_hf_hub_calls = []


# --------------------------------------------------------------------------
# Fake transformers (the AutoTokenizer path Qwen must keep)
# --------------------------------------------------------------------------


class _FakeAutoTokenizerInstance:
    """A converted-vocabulary tokenizer, GPT-2 style.

    Ids are ``ord(ch)``, disjoint from the Tekken fake, and ``decode`` renders
    spaces as ``Ġ`` - so if this object ever decodes Mistral output the test
    sees exactly the real-world corruption.
    """

    chat_template = "{{ jinja }}"
    eos_token_id = 151645
    pad_token_id = None

    def __init__(self) -> None:
        self.rendered: list[str] = []

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        body = "".join(f"<|{m['role']}|>{m['content']}" for m in messages)
        text = body + "<|assistant|>"
        self.rendered.append(text)
        return text

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return [ord(ch) for ch in text]

    def decode(self, ids, skip_special_tokens: bool = True) -> str:
        return "".join("Ġ" if int(t) == 32 else chr(int(t)) for t in ids)

    def __call__(self, text: str, return_tensors=None, **_):
        ids = self.encode(text)
        if return_tensors == "pt":
            return TokenBatch({"input_ids": _FakeTensor([ids])})
        return TokenBatch({"input_ids": [ids]})


class _FakeAutoTokenizer:
    calls: list[tuple[str, dict]] = []
    instances: list[_FakeAutoTokenizerInstance] = []

    @classmethod
    def from_pretrained(cls, model_id: str, **kwargs):
        cls.calls.append((model_id, dict(kwargs)))
        instance = _FakeAutoTokenizerInstance()
        cls.instances.append(instance)
        return instance


# --------------------------------------------------------------------------
# Fake torch / model
# --------------------------------------------------------------------------


class _FakeTensor:
    """Just enough tensor for the runtime: shape, indexing, slicing, ``.to``."""

    def __init__(self, rows) -> None:
        self.rows = [list(row) for row in rows]

    @property
    def shape(self):
        return (len(self.rows), len(self.rows[0]) if self.rows else 0)

    def __getitem__(self, index):
        if isinstance(index, int):
            return _FakeRow(self.rows[index])
        raise TypeError(index)

    def to(self, _device):
        return self

    def tolist(self):
        return [list(row) for row in self.rows]


class _FakeRow:
    def __init__(self, values) -> None:
        self.values = list(values)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _FakeRow(self.values[index])
        return self.values[index]

    def __iter__(self):
        return iter(self.values)

    @property
    def shape(self):
        return (len(self.values),)


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_tensor(data, dtype=None):
    return _FakeTensor(data)


def _install_torch(monkeypatch):
    torch = types.ModuleType("torch")
    torch.tensor = _fake_tensor
    torch.long = "long"
    torch.no_grad = _NoGrad
    torch.manual_seed = lambda seed: None
    torch.float16 = "float16"
    torch.bfloat16 = "bfloat16"
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


class _Scalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def item(self) -> float:
        return self.value


class _ScoreRow:
    """Per-vocabulary-id logits; the score is the id itself, so the runtime's
    id lookup is observable in the returned numbers."""

    def __getitem__(self, token_id):
        return _Scalar(float(token_id))


class _FakeLogits:
    def __getitem__(self, index):
        assert index == (0, -1, slice(None, None, None)), index
        return _ScoreRow()


class _FakeOutput:
    logits = _FakeLogits()


class _FakeModel:
    def __init__(self, continuation_ids) -> None:
        self.device = "cpu"
        self.continuation_ids = list(continuation_ids)
        self.seen_input_ids: list[list[int]] = []
        self.generate_kwargs: list[dict] = []
        self.forward_calls: list[dict] = []

    def __call__(self, **kwargs):
        self.forward_calls.append(dict(kwargs))
        return _FakeOutput()

    def eval(self):
        return self

    def named_modules(self):
        return [("", self), ("language_model", self)]

    def parameters(self):
        return iter(())

    def generate(self, input_ids=None, **kwargs):
        ids = input_ids.rows[0]
        self.seen_input_ids.append(list(ids))
        self.generate_kwargs.append(dict(kwargs))
        return _FakeTensor([[*ids, *self.continuation_ids]])


class _FakeAutoModel:
    model: _FakeModel | None = None
    load_kwargs: list[dict] = []

    @classmethod
    def from_pretrained(cls, model_id: str, **kwargs):
        cls.load_kwargs.append(dict(kwargs))
        return cls.model


def _install_transformers(monkeypatch, model: _FakeModel):
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = _FakeAutoTokenizer
    _FakeAutoModel.model = model
    _FakeAutoModel.load_kwargs = []
    transformers.AutoModelForCausalLM = _FakeAutoModel
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    _FakeAutoTokenizer.calls = []
    _FakeAutoTokenizer.instances = []
    return transformers


@pytest.fixture
def hf_env(monkeypatch):
    """A fully faked torch/transformers/mistral-common environment."""

    def _make(continuation_ids):
        _install_torch(monkeypatch)
        model = _FakeModel(continuation_ids)
        _install_transformers(monkeypatch, model)
        _install_mistral_common(monkeypatch)
        return model

    return _make


def _build_runtime(model_id: str, family: str, revision: str, **kwargs):
    from cover_kbc.models.huggingface import HuggingFaceRuntime

    return HuggingFaceRuntime(
        model_id=model_id,
        published_total_parameters=1,
        revision=revision,
        family=family,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Tokenizer selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,family,expected",
    [
        (MISTRAL_ID, "mistral", True),
        (MISTRAL_ID, "", True),
        ("some/checkpoint", "mistral", True),
        (QWEN_ID, "qwen", False),
        (QWEN_ID, "", False),
        ("offline/scripted", "offline", False),
    ],
)
def test_requires_mistral_tokenizer_keys_on_family_or_id(model_id, family, expected):
    assert requires_mistral_tokenizer(model_id, family) is expected


def test_auto_backend_selects_mistral_common_for_the_frozen_enumerator(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, revision=MISTRAL_REV, family="mistral")
    assert isinstance(tokenizer, MistralCommonTokenizer)
    # The frozen revision is forwarded, not dropped.
    assert _FakeMistralTokenizer.from_hf_hub_calls == [
        (MISTRAL_ID, {"revision": MISTRAL_REV})
    ]


def test_auto_backend_leaves_qwen_on_autotokenizer(monkeypatch):
    _install_transformers(monkeypatch, _FakeModel([]))
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(QWEN_ID, revision=QWEN_REV, family="qwen")
    assert isinstance(tokenizer, _FakeAutoTokenizerInstance)
    assert _FakeAutoTokenizer.calls == [
        (QWEN_ID, {"revision": QWEN_REV, "trust_remote_code": False})
    ]
    # No Mistral tokenizer is constructed for a non-Mistral checkpoint.
    assert _FakeMistralTokenizer.from_hf_hub_calls == []


def test_revision_pin_survives_an_older_from_hf_hub_signature(monkeypatch):
    """Never silently resolve ``main`` when the freeze names a revision."""
    _install_mistral_common(monkeypatch)
    mistral_mod = sys.modules["mistral_common.tokens.tokenizers.mistral"]

    downloads: list[dict] = []

    class _NoRevision:
        @classmethod
        def from_hf_hub(cls, model_id):  # no revision parameter
            raise AssertionError("must not be used when a revision is pinned")

        @classmethod
        def from_file(cls, path):
            downloads.append({"from_file": path})
            return _FakeMistralTokenizer()

    hub = types.ModuleType("huggingface_hub")

    def _download(repo_id, filename, revision):
        downloads.append({"repo_id": repo_id, "filename": filename, "revision": revision})
        return f"/cache/{revision}/{filename}"

    hub.hf_hub_download = _download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(mistral_mod, "MistralTokenizer", _NoRevision)

    tokenizer = build_tokenizer(MISTRAL_ID, revision=MISTRAL_REV, family="mistral")
    assert isinstance(tokenizer, MistralCommonTokenizer)
    assert downloads == [
        {"repo_id": MISTRAL_ID, "filename": "tekken.json", "revision": MISTRAL_REV},
        {"from_file": f"/cache/{MISTRAL_REV}/tekken.json"},
    ]


def test_no_revision_means_no_revision_kwarg(monkeypatch):
    _install_mistral_common(monkeypatch)
    build_tokenizer(MISTRAL_ID, revision="", family="mistral")
    assert _FakeMistralTokenizer.from_hf_hub_calls == [(MISTRAL_ID, {})]


def test_backend_can_be_pinned_explicitly(monkeypatch):
    _install_transformers(monkeypatch, _FakeModel([]))
    _install_mistral_common(monkeypatch)
    assert isinstance(
        build_tokenizer(QWEN_ID, family="qwen", tokenizer_backend="mistral_common"),
        MistralCommonTokenizer,
    )
    assert isinstance(
        build_tokenizer(MISTRAL_ID, family="mistral", tokenizer_backend="huggingface"),
        _FakeAutoTokenizerInstance,
    )


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="tokenizer_backend"):
        build_tokenizer(MISTRAL_ID, tokenizer_backend="tekken-ish")


def test_missing_mistral_common_raises_instead_of_falling_back(monkeypatch):
    """The whole defect was a silent fallback; refuse to repeat it."""
    _install_transformers(monkeypatch, _FakeModel([]))
    for name in list(sys.modules):
        if name == "mistral_common" or name.startswith("mistral_common."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "mistral_common", None)

    with pytest.raises(MistralTokenizerUnavailable) as excinfo:
        build_tokenizer(MISTRAL_ID, revision=MISTRAL_REV, family="mistral")
    assert "mistral-common" in str(excinfo.value)
    # Critically: it did not quietly hand back a converted AutoTokenizer.
    assert _FakeAutoTokenizer.calls == []


# --------------------------------------------------------------------------
# The adapter surface
# --------------------------------------------------------------------------


def test_encode_chat_builds_system_and_user_messages(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, family="mistral")

    ids = tokenizer.encode_chat("Which countries border France?", "Answer precisely.")
    text = tokenizer.decode(ids)
    assert text == (
        "[SYSTEM]Answer precisely.[/SYSTEM]"
        "[USER]Which countries border France?[/USER]"
    )


def test_encode_chat_omits_an_absent_system_message(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, family="mistral")

    assert tokenizer.decode(tokenizer.encode_chat("List them.", "")) == "[USER]List them.[/USER]"


def test_encode_decode_round_trip_through_the_tekken_vocabulary(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, family="mistral")

    ids = tokenizer.encode("A", add_special_tokens=False)
    assert ids == [ord("A") + 1000]
    assert tokenizer.decode(ids) == "A"
    # A converted-vocabulary id decodes to the corruption marker, which is what
    # the real run produced.
    assert tokenizer.decode([ord("A")]) == "Ġ?"


def test_apply_chat_template_routes_through_the_chat_encoder(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, family="mistral")

    rendered = tokenizer.apply_chat_template(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        tokenize=False,
        add_generation_prompt=True,
    )
    assert rendered == "[SYSTEM]S[/SYSTEM][USER]U[/USER]"


def test_pad_token_id_falls_back_to_eos(monkeypatch):
    _install_mistral_common(monkeypatch)
    tokenizer = build_tokenizer(MISTRAL_ID, family="mistral")
    assert tokenizer.eos_token_id == _FakeTekken.eos_id
    assert tokenizer.pad_token_id == _FakeTekken.eos_id


def test_chat_token_ids_is_none_for_a_plain_autotokenizer(monkeypatch):
    _install_transformers(monkeypatch, _FakeModel([]))
    _install_mistral_common(monkeypatch)
    qwen = build_tokenizer(QWEN_ID, family="qwen")
    assert chat_token_ids(qwen, "prompt", "system") is None

    mistral = build_tokenizer(MISTRAL_ID, family="mistral")
    assert chat_token_ids(mistral, "prompt", "system") is not None


def test_token_batch_moves_tensors_and_stays_a_mapping():
    batch = TokenBatch({"input_ids": _FakeTensor([[1, 2]])})
    moved = batch.to("cuda")
    assert isinstance(moved, TokenBatch)
    assert moved["input_ids"].tolist() == [[1, 2]]


# --------------------------------------------------------------------------
# Runtime generation - the defect itself
# --------------------------------------------------------------------------


def _mistral_runtime(hf_env, continuation: str):
    model = _FakeModel(_FakeTekken().encode(continuation))
    hf_env(model.continuation_ids)
    runtime = _build_runtime(MISTRAL_ID, "mistral", MISTRAL_REV)
    runtime.model = model
    return runtime, model


def test_mistral_generate_feeds_the_model_chat_encoded_ids(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, model = _mistral_runtime(hf_env, "Belgium; Germany")
    request = GenerationRequest(
        prompt="Which countries border France?", system_prompt="Answer precisely."
    )
    result = runtime.generate(request)

    expected_prompt_ids = runtime.tokenizer.encode_chat(
        request.prompt, request.system_prompt
    )
    assert model.seen_input_ids == [expected_prompt_ids]
    # Structured messages reached the model, not a hand-rendered string.
    assert runtime.tokenizer.decode(model.seen_input_ids[0]).startswith("[SYSTEM]")
    assert result.text == "Belgium; Germany"
    assert result.prompt_tokens == len(expected_prompt_ids)
    assert result.generated_tokens == len(model.continuation_ids)


def test_mistral_generate_never_builds_an_autotokenizer(hf_env):
    _mistral_runtime(hf_env, "ok")
    assert _FakeAutoTokenizer.calls == []


def test_mistral_generation_is_not_byte_bpe_debris(hf_env):
    """The exact real-run signature: repeated ``Ġ``-marked pieces."""
    from cover_kbc.models.base import GenerationRequest

    runtime, _ = _mistral_runtime(hf_env, "Belgium; Germany; Spain")
    result = runtime.generate(
        GenerationRequest(prompt="Which countries border France?", system_prompt="S")
    )
    assert "Ġ" not in result.text
    words = result.text.split()
    assert len(set(words)) == len(words)


def test_mistral_generate_slices_at_the_prompt_boundary(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, model = _mistral_runtime(hf_env, "Andorra")
    result = runtime.generate(GenerationRequest(prompt="Border query", system_prompt="S"))
    # No prompt echo survives the slice.
    assert result.text == "Andorra"
    assert "[USER]" not in result.text
    assert result.prompt_tokens == len(model.seen_input_ids[0])


def test_mistral_generate_preserves_decode_controls_and_accounting(hf_env):
    from cover_kbc.models.base import GenerationRequest
    from cover_kbc.types import DecodeProfile

    runtime, model = _mistral_runtime(hf_env, "Belgium STOP Germany")
    request = GenerationRequest(
        prompt="p",
        system_prompt="s",
        decode=DecodeProfile(name="sampled", temperature=0.7, top_p=0.9, max_new_tokens=64),
        stop=("STOP",),
    )
    result = runtime.generate(request)

    kwargs = model.generate_kwargs[0]
    assert kwargs["max_new_tokens"] == 64
    assert kwargs["do_sample"] is True
    assert kwargs["temperature"] == 0.7
    assert kwargs["top_p"] == 0.9
    assert kwargs["pad_token_id"] == _FakeTekken.eos_id
    # Stop strings still truncate, and the result is stripped.
    assert result.text == "Belgium"
    # One generate() is one runtime call; token accounting counts *all* new
    # tokens, not the truncated text.
    assert runtime.calls == 1
    assert runtime.generated_tokens == len(model.continuation_ids)
    assert result.generated_tokens == len(model.continuation_ids)


def test_mistral_greedy_decode_omits_sampling_kwargs(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, model = _mistral_runtime(hf_env, "x")
    runtime.generate(GenerationRequest(prompt="p", system_prompt="s"))
    kwargs = model.generate_kwargs[0]
    assert kwargs["do_sample"] is False
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_mistral_generate_handles_a_missing_system_prompt(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, model = _mistral_runtime(hf_env, "y")
    runtime.generate(GenerationRequest(prompt="p"))
    assert runtime.tokenizer.decode(model.seen_input_ids[0]) == "[USER]p[/USER]"


def test_mistral_label_scoring_also_uses_the_chat_encoder(hf_env):
    """Under a role swap the enumerator verifies; it must be framed the same."""
    from cover_kbc.models.base import LabelScoreRequest

    runtime, model = _mistral_runtime(hf_env, "")
    labels = {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}
    request = LabelScoreRequest(prompt="Is this true?", labels=labels, system_prompt="S")
    result = runtime.score_labels(request)

    expected = runtime.tokenizer.encode_chat(request.prompt, request.system_prompt)
    assert model.forward_calls[0]["input_ids"].rows[0] == expected
    assert len(model.forward_calls) == 1
    assert runtime.calls == 1
    # Tekken ids, not converted-vocabulary ids.
    assert result.logits == {
        "VALID": float(ord("A") + 1000),
        "INVALID": float(ord("B") + 1000),
        "UNKNOWN": float(ord("C") + 1000),
    }
    assert result.prompt_tokens == len(expected)


# --------------------------------------------------------------------------
# Qwen must be untouched
# --------------------------------------------------------------------------


def _qwen_runtime(hf_env, continuation: str):
    model = _FakeModel([ord(ch) for ch in continuation])
    hf_env(model.continuation_ids)
    runtime = _build_runtime(QWEN_ID, "qwen", QWEN_REV)
    runtime.model = model
    return runtime, model


def test_qwen_generate_still_renders_then_tokenises(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, model = _qwen_runtime(hf_env, "Belgium")
    request = GenerationRequest(prompt="Border query", system_prompt="S")
    result = runtime.generate(request)

    # The chat template was applied, exactly as before this fix.
    assert runtime.tokenizer.rendered == ["<|system|>S<|user|>Border query<|assistant|>"]
    assert model.seen_input_ids[0] == [ord(c) for c in runtime.tokenizer.rendered[0]]
    assert result.text == "Belgium"
    assert result.prompt_tokens == len(runtime.tokenizer.rendered[0])
    assert result.generated_tokens == len("Belgium")
    assert runtime.calls == 1


def test_qwen_single_token_label_scoring_is_one_runtime_call(hf_env):
    from cover_kbc.models.base import LabelScoreRequest

    runtime, model = _qwen_runtime(hf_env, "")
    labels = {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}

    result = runtime.score_labels(
        LabelScoreRequest(prompt="p", labels=labels, system_prompt="s")
    )
    # A/B/C each encode to one token, so the runtime takes the
    # single-forward-pass path: one model call, one runtime call.
    assert runtime.inspect_labels(labels).single_token
    assert len(model.forward_calls) == 1
    assert runtime.calls == 1
    # Each label read its own vocabulary id, not a shared first token.
    assert result.logits == {
        "VALID": float(ord("A")),
        "INVALID": float(ord("B")),
        "UNKNOWN": float(ord("C")),
    }
    assert result.prompt_tokens == len(runtime.tokenizer.rendered[0])


def test_qwen_multi_token_labels_fall_back_to_sequence_scoring(hf_env):
    from cover_kbc.models.base import LabelScoreRequest

    runtime, _ = _qwen_runtime(hf_env, "")
    labels = {"VALID": "yes", "INVALID": "no"}
    encoding = runtime.inspect_labels(labels)
    # "yes"/"no" are multi-token here, so first-token comparison is refused.
    assert not encoding.single_token
    assert len(encoding.token_ids["VALID"]) == 3

    calls = {"count": 0}

    def _score_sequence(request, enc):
        calls["count"] += 1
        return "sequence"

    runtime._score_sequence = _score_sequence
    assert runtime.score_labels(LabelScoreRequest(prompt="p", labels=labels)) == "sequence"
    assert calls["count"] == 1


def test_qwen_never_touches_mistral_common(hf_env):
    from cover_kbc.models.base import GenerationRequest

    runtime, _ = _qwen_runtime(hf_env, "z")
    runtime.generate(GenerationRequest(prompt="p", system_prompt="s"))
    assert _FakeMistralTokenizer.from_hf_hub_calls == []


# --------------------------------------------------------------------------
# Config wiring
# --------------------------------------------------------------------------


def test_registry_threads_tokenizer_backend_into_the_runtime(monkeypatch):
    from cover_kbc.models import registry

    captured: dict = {}

    class _Spy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = types.ModuleType("cover_kbc.models.huggingface")
    module.HuggingFaceRuntime = _Spy
    monkeypatch.setitem(sys.modules, "cover_kbc.models.huggingface", module)

    registry.build_runtime(
        {
            "backend": "huggingface",
            "model_id": MISTRAL_ID,
            "published_total_parameters": 24_011_361_280,
            "family": "mistral",
            "revision": MISTRAL_REV,
            "tokenizer_backend": "mistral_common",
        }
    )
    assert captured["tokenizer_backend"] == "mistral_common"
    assert captured["revision"] == MISTRAL_REV

    captured.clear()
    registry.build_runtime(
        {
            "backend": "huggingface",
            "model_id": QWEN_ID,
            "published_total_parameters": 4_659_865_088,
            "family": "qwen",
        }
    )
    # Default stays "auto", which for a non-Mistral checkpoint means
    # AutoTokenizer - unchanged behaviour.
    assert captured["tokenizer_backend"] == "auto"


def test_frozen_config_declares_a_tokenizer_backend_per_model():
    from pathlib import Path

    import yaml

    from cover_kbc.models.registry import model_blocks

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (root / "configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read_text()
    )
    enumerator, verifier = model_blocks(config)
    assert enumerator["tokenizer_backend"] == "mistral_common"
    assert verifier["tokenizer_backend"] == "huggingface"
    # The freeze is unchanged: same checkpoints, same revisions.
    assert enumerator["model_id"] == MISTRAL_ID
    assert enumerator["revision"] == MISTRAL_REV
    assert verifier["model_id"] == QWEN_ID
    assert verifier["revision"] == QWEN_REV


def test_hf_extra_declares_mistral_common():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "mistral-common>=1.6.2" in text
