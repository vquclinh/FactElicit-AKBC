# Audit 0014 — Mistral Real-Runtime Tokenizer Compatibility

Status: **PASS — RUNTIME COMPATIBILITY CORRECTED (real-weight retest still required)**
Date: 2026-08-05
Freeze commit under test: `8fe31d2d2c77c90b5450fb1b893a1f8e31188ff0`
Scope: one real-runtime integration defect. No architecture change.

---

## 1. Objective

The architecture-freeze commit was executed against real weights in Colab for
the first time (run R0). Qwen behaved correctly; the Mistral enumerator produced
unusable text. This audit records the root cause, the smallest production-quality
correction, and the evidence for it.

Nothing in COVER-KBC v2 was redesigned. No module semantics, no scoring
function, no threshold, no controller behaviour and no selector rule was
touched. The correction lives entirely inside the model runtime layer.

---

## 2. What Colab R0 actually reported

**Qwen verifier — correct.**

* Loaded at the frozen revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` with
  NF4 quantization.
* Labels `A`/`B`/`C` encoded to single token ids `32`/`33`/`34`, so the
  single-forward-pass scoring path was selected as designed.
* One scoring operation counted as exactly one runtime call.

**Mistral enumerator — broken.**

* Loaded as `Mistral3ForConditionalGeneration`, ~13.41 GB resident under NF4.
* The load log contained:

  ```
  Converting tekken.json to tokenizer.json
  ```

* Generation output, repeating until `max_new_tokens=256`:

  ```
  oun Ġrebell ì§ ption ĠAn ĠAn ĠAn ĠAn ...
  ```

The model loaded, the call completed, and the accounting was intact. The *text*
was garbage. Nothing above the runtime could detect this — the pipeline saw a
normal `GenerationResult` and parsed nonsense out of it.

---

## 3. Root cause

`mistralai/Mistral-Small-3.2-24B-Instruct-2506` ships a **Tekken** tokenizer as
`tekken.json`. It does not ship a Hugging Face `tokenizer.json`.

`AutoTokenizer.from_pretrained(...)` does not refuse this. It runs a conversion
to a `tokenizer.json` — the line quoted in §2 is that conversion — and hands
back a tokenizer object that is *not* faithful to the checkpoint's vocabulary.
Two consequences follow, and R0 shows both:

1. **The decoder is reading the wrong table.** `Ġ` is the GPT-2 byte-BPE marker
   for a leading space. Seeing it in *decoded* output means the decode step is
   rendering pieces from a byte-BPE vocabulary that this checkpoint was never
   trained to emit. `ì§` — an orphaned multi-byte fragment — is the same fault
   seen from the other side.

2. **The prompt was never framed as an instruct exchange.** The runtime rendered
   the prompt through `tokenizer.apply_chat_template(...)` and tokenised the
   resulting string. With the converted tokenizer, the control tokens that make
   this an instruct checkpoint were not the ones the model expects. An instruct
   model fed an input it cannot parse degenerates, which is exactly the
   `ĠAn ĠAn ĠAn` tail running to the token limit.

The failure is therefore in tokenisation on both the input and the output side,
not in the model, not in the quantization, and not in COVER.

The reason this survived every previous audit is that it is invisible without
real weights: 973 tests passed on the freeze commit, and none of them could
observe which tokenizer class the runtime constructed.

---

## 4. The official expectation for this checkpoint

Mistral's guidance for Mistral-Small-3.2 is that tokenisation and chat encoding
go through **`mistral-common`**:

* `MistralTokenizer.from_hf_hub(model_id, revision=...)` loads the Tekken
  tokenizer natively — no conversion step.
* `encode_chat_completion(ChatCompletionRequest(messages=[...]))` produces the
  instruct-framed token ids. Structured messages, not a pre-rendered string.
* The same tokenizer decodes, so generation and decoding share one vocabulary.

The model class itself was already correct: R0 loaded
`Mistral3ForConditionalGeneration`, which is what the existing `_load_model`
auto-class ladder is there to reach. Generation stays text-only — the runtime
passes `input_ids` and nothing else, so the vision tower is never fed.

---

## 5. The correction

### 5.1 What was *not* done

Per the brief, and independently because each would be wrong:

* The checkpoint was **not** switched.
* The frozen revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` was **not**
  changed. §5.4 explains how it is now protected harder than before.
* No community-converted checkpoint was substituted.
* No web access, retrieval or external corpus was added. The prediction path
  remains closed-book.
* No COVER architecture, module or threshold was altered.
* The France example from R0 was **not** special-cased anywhere.
* There is **no silent fallback to malformed `AutoTokenizer` output**. See §5.5.

### 5.2 A checkpoint-native tokenizer adapter

`src/cover_kbc/models/mistral_tokenizer.py` (new) provides
`MistralCommonTokenizer`, which wraps `mistral_common`'s `MistralTokenizer` and
exposes only the slice of the tokenizer surface `HuggingFaceRuntime` already
uses:

| Member | Purpose |
| --- | --- |
| `encode_chat(prompt, system_prompt)` | Builds `SystemMessage`/`UserMessage`, encodes via `encode_chat_completion`, returns `.tokens`. |
| `encode` / `decode` / `__call__` | Plain-text paths, through the same Tekken vocabulary. |
| `apply_chat_template` | `AutoTokenizer`-shaped rendering, routed through the real chat encoder. |
| `eos_token_id` / `pad_token_id` | `generate` kwargs. Tekken has no distinct pad id, so pad falls back to EOS. |
| `chat_template = "mistral-common"` | A non-empty marker, so the existing `_render` never tries to apply a Jinja template that does not exist. |

`build_tokenizer(...)` selects between this and `AutoTokenizer`.
`chat_token_ids(tokenizer, prompt, system_prompt)` returns ids when the
tokenizer encodes chat natively and `None` otherwise — `None` meaning "use the
ordinary render-then-tokenise path".

### 5.3 Runtime routing

`HuggingFaceRuntime` gained one constructor parameter, `tokenizer_backend`
(default `"auto"`), and one private helper, `_prompt_ids(request)`:

```python
chat_ids = chat_token_ids(self.tokenizer, request.prompt, request.system_prompt or "")
if chat_ids is None:
    return self.tokenizer(self._render(request), return_tensors="pt")["input_ids"]
return self._torch.tensor([list(chat_ids)], dtype=self._torch.long)
```

`generate`, `_score_next_token` and `_score_sequence` all tokenise through this
one helper. That matters beyond tidiness: under a staged **role swap** the
enumerator can be asked to verify, and both paths must frame the prompt
identically or the calibrated verifier reads a different input than the one it
was measured on.

The `GenerationRequest -> generate() -> GenerationResult` contract is unchanged.
Slicing still happens at `output[0][prompt_tokens:]` with `prompt_tokens` taken
from the tensor actually fed to the model, so it is correct for both backends.
Decoding uses `self.tokenizer.decode(...)`, which is now the matching tokenizer.
`prompt_tokens`, `generated_tokens`, `self.calls` and `self.generated_tokens`
accounting is byte-for-byte the same code as before. Stop strings, temperature,
`top_p`, `max_new_tokens`, `do_sample` and seeding are untouched.

### 5.4 The revision pin is now harder to lose

`from_hf_hub`'s `revision` parameter is not present in every `mistral-common`
release. Passing it blindly would raise `TypeError` on an older one; omitting it
would silently resolve `main` and quietly unpin the frozen checkpoint — a worse
outcome. `_load_from_hub` inspects the signature: if `revision` is supported it
is passed; if not, `huggingface_hub.hf_hub_download(..., revision=...)` fetches
`tekken.json` at the exact commit and `from_file` loads that. The pin is never
dropped on either branch.

### 5.5 Failure is loud

If `mistral-common` is not importable, `MistralCommonTokenizer` raises
`MistralTokenizerUnavailable` with the install instruction. It does **not** fall
back to `AutoTokenizer`. A silent fallback is precisely what produced R0's
corrupt output, and a run that looks like it worked is worse than one that stops.

### 5.6 Selection is declared, not guessed

`auto` selects `mistral_common` only for Mistral-family checkpoints, keyed on
the declared `family` first and the model id as a fallback. The frozen config now
states the choice outright rather than relying on that inference:

```yaml
enumerator:
  tokenizer_backend: mistral_common
verifier:
  tokenizer_backend: huggingface
```

---

## 6. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/models/mistral_tokenizer.py` | **New.** Adapter, backend selection, revision-pinned hub loading, `TokenBatch`. |
| `src/cover_kbc/models/huggingface.py` | `tokenizer_backend` parameter; `build_tokenizer` replaces the direct `AutoTokenizer` call; `_prompt_ids` shared by `generate`/`_score_next_token`/`_score_sequence`. |
| `src/cover_kbc/models/registry.py` | One line: threads `tokenizer_backend` from the model profile. |
| `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` | Declares `tokenizer_backend` per model. Checkpoints, revisions and parameter counts unchanged. |
| `pyproject.toml` | `mistral-common>=1.6.2` added to the `hf` optional extra. |
| `tests/test_mistral_runtime_compat.py` | **New.** 35 tests. |

Diffstat for the modified files: 47 insertions, 13 deletions.

`benchmark/` was not touched. No `CLAUDE.md`, no `.claude/`.

---

## 7. Tests added

`tests/test_mistral_runtime_compat.py` — 35 tests, **no weights downloaded**.
`torch`, `transformers` and `mistral_common` are all injected into `sys.modules`
as fakes. This works because the defect is *structural*: the runtime built the
wrong tokenizer class and handed the model a rendered string instead of the
checkpoint's own chat encoding, and both facts are observable without a GPU.

The fake Tekken tokenizer uses ids `ord(ch) + 1000`; the fake converted
`AutoTokenizer` uses ids `ord(ch)` and renders spaces as `Ġ`. The vocabularies
are deliberately disjoint, so a test that decodes with the wrong table gets
visible `Ġ`-garbage rather than a plausible-looking string — the same signature
as the real failure.

**Tests that would have caught R0:**

| Test | What it pins |
| --- | --- |
| `test_auto_backend_selects_mistral_common_for_the_frozen_enumerator` | The Mistral checkpoint gets the Tekken tokenizer, at the frozen revision. |
| `test_mistral_generate_never_builds_an_autotokenizer` | `AutoTokenizer.from_pretrained` is never called for Mistral. |
| `test_mistral_generate_feeds_the_model_chat_encoded_ids` | The model receives `encode_chat` ids, not a rendered string. |
| `test_mistral_generation_is_not_byte_bpe_debris` | No `Ġ` in the result, and no repeated-token degeneration. |
| `test_missing_mistral_common_raises_instead_of_falling_back` | No silent fallback to a converted tokenizer. |

**Other coverage:** family/id backend selection (6 cases), explicit backend
pinning, unknown-backend rejection, revision forwarding on both hub-loading
branches, chat encoding with and without a system message, Tekken round-trip and
foreign-id corruption, `apply_chat_template` routing, EOS/pad fallback,
`chat_token_ids` returning `None` for a plain tokenizer, `TokenBatch.to`, prompt
boundary slicing, stop strings, greedy vs sampled decode kwargs, call and token
accounting, absent system prompt, Mistral label scoring under a role swap,
registry threading, config declarations, and the `pyproject` extra.

**Qwen regression coverage** (requirement: behaviour must be unchanged):

| Test | Assertion |
| --- | --- |
| `test_auto_backend_leaves_qwen_on_autotokenizer` | `AutoTokenizer.from_pretrained(QWEN_ID, revision=..., trust_remote_code=False)`, and no Mistral tokenizer constructed. |
| `test_qwen_generate_still_renders_then_tokenises` | Chat template applied, string tokenised, ids match, one runtime call. |
| `test_qwen_single_token_label_scoring_is_one_runtime_call` | One forward pass, one runtime call, each label reads its own vocabulary id. |
| `test_qwen_multi_token_labels_fall_back_to_sequence_scoring` | Multi-token labels still refuse first-token comparison. |
| `test_qwen_never_touches_mistral_common` | `mistral-common` is never loaded for Qwen. |

---

## 8. Validation results

```
python -m pytest -q
    1008 passed, 3 skipped

python -m pyflakes src/ tests/ scripts/
    clean

python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    Qwen/Qwen3.5-4B  [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506 [enumerator] 24.011B (verified)
    total: 28.67B
    RESULT: PASS
```

Test count: 973 on the freeze commit → 1008. All 973 pre-existing tests still
pass, unmodified.

**Benchmark integrity** — all three checks empty:

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. No val or test
data was read at any point in this work.

**What is not validated here.** These tests prove the runtime selects the
checkpoint-native tokenizer, encodes chat through it, slices at the right
boundary and decodes with the matching vocabulary. They cannot prove that the
real Tekken tokenizer produces coherent French-border text from the real 24B
weights, because that requires the download this work is not permitted to do.
That is what the retest in §11 is for. The fix is not confirmed against real
weights until that cell runs clean.

---

## 9. Is this an architecture change?

**No.** The test for this is whether any COVER-KBC v2 semantic could produce a
different answer given the same tokens, and none can:

* Modules 0–8 are untouched. No file outside `src/cover_kbc/models/` changed,
  other than the config's tokenizer declarations.
* No scoring term, weight, threshold, gate, stopping rule or selection rule was
  modified. The calibration surface is still the same 5 decisions from Audit 0012.
* The `LMRuntime` interface is unchanged. `GenerationRequest -> generate() ->
  GenerationResult` and `LabelScoreRequest -> score_labels() -> LabelScoreResult`
  have the same shapes and the same accounting fields.
* Budget semantics are unchanged: one `generate` is one neural call, one
  single-token scoring op is one neural call, one call per label in the
  multi-token fallback. The 32B parameter total is unchanged at 28.67B.
* `HuggingFaceRuntime` remains generic. Nothing Mistral-specific is hard-coded in
  it; it asks the tokenizer whether it encodes chat and acts on the answer.

This is a correction to how bytes become token ids for one checkpoint. Above the
runtime, the system cannot tell the difference except that the text is now
correct.

---

## 10. Does Qwen behave identically?

**Yes.** `build_tokenizer` with `auto` returns
`AutoTokenizer.from_pretrained(model_id, revision=..., trust_remote_code=...)`
for any non-Mistral checkpoint — the same call, with the same arguments, that
the freeze commit made. An `AutoTokenizer` has no `encode_chat`, so
`chat_token_ids` returns `None` and `_prompt_ids` takes the original
render-then-tokenise path. The frozen config now says `tokenizer_backend:
huggingface` for the verifier, which is what `auto` would have chosen anyway.

R0's Qwen observations — single-token `A`/`B`/`C` at ids 32/33/34, one scoring
op per runtime call — are preserved by construction and asserted by the five
tests in §7.

---

## 11. Minimal Colab retest

One cell. It touches no benchmark data, no val, no test — it loads the frozen
enumerator and generates once on a synthetic prompt.

```python
!pip install -q "mistral-common>=1.6.2"

import sys; sys.path.insert(0, "src")   # or: pip install -e ".[hf]"

import yaml
from cover_kbc.models.registry import build_runtime, model_blocks
from cover_kbc.models.base import GenerationRequest
from cover_kbc.types import DecodeProfile

cfg = yaml.safe_load(open("configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml").read())
enumerator, _ = model_blocks(cfg)
rt = build_runtime(enumerator)

print("tokenizer:", type(rt.tokenizer).__name__)   # expect MistralCommonTokenizer

r = rt.generate(GenerationRequest(
    prompt="List the countries that share a land border with France. "
           "Answer as a semicolon-separated list and nothing else.",
    system_prompt="You are a precise knowledge base assistant.",
    decode=DecodeProfile(name="greedy", temperature=0.0, max_new_tokens=64),
))
print(repr(r.text))
print("prompt_tokens:", r.prompt_tokens, "generated_tokens:", r.generated_tokens)
print("runtime calls:", rt.calls)
```

**Pass criteria**

1. `tokenizer: MistralCommonTokenizer`.
2. No `Converting tekken.json to tokenizer.json` line in the load log.
3. `r.text` contains no `Ġ` and no repeated-token run.
4. `r.text` is a readable semicolon-separated list of country names.
5. `generated_tokens` is well under 64 — the model stopped, rather than running
   to the limit.
6. `rt.calls == 1`.

If (1) or (2) fails, the tokenizer backend did not take effect. If (3) fails
while (1) passes, the diagnosis in §3 is incomplete and the fix must be
revisited rather than patched around.

Only after this cell passes should the staged pipeline be re-run, on **train**
data:

```
python scripts/run_staged.py all --config configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml --split train --limit 5
```

---

## 12. Verdict

The defect is understood, reproduced structurally, and corrected at the smallest
site that can carry the fix. Qwen is unchanged. The architecture is unchanged.
The frozen checkpoints and revisions are unchanged, and the revision pin is now
harder to lose than it was.

**Status: PASS**, conditional on the §11 retest against real weights. The suite
cannot substitute for that cell, and this audit does not claim it does.
