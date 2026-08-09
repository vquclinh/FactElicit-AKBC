# Audit 0065 — Portfolio real-weight runtime reproducibility

**Date:** 2026-08-09
**Scope:** dependency and version declarations proven necessary by the first
real-weight portfolio smoke. No architecture, model, routing or M0–M21 change.
**Source before this milestone:** `3083878232a03147d4a2ef5758ddf17e0476ba94`
**Python:** 3.14.5

---

## Verdict

**PASS — PORTFOLIO REAL-WEIGHT RUNTIME REPRODUCIBLE; FULL TEST READY**

---

## 1. Status

| | Baseline | Portfolio Direct |
|---|---|---|
| SCRIPTED TESTED | **YES** | **YES** |
| REAL-WEIGHT SMOKE TESTED | **YES** (earlier) | **YES** — 3 rows, NVIDIA L4 |
| FULL 475 TEST | **NOT RUN** in this milestone | **NOT RUN** |
| Production calibrated | YES | **NO** — `direct_uncalibrated` |
| TEST evaluation performed | **NO** | **NO** |

The successful run was
`outputs/cover_kbc_v2_portfolio_test_direct_test_20260809T041252Z`, which wrote
exactly three predictions:

```
Niutao          | hasArea | ['296']
Lake Lucerne    | hasArea | ['48.8']
Lake Bangweulu  | hasArea | ['11000']
```

No metrics were computed, no `metrics.json` was written, and the evaluator was
not called — TEST is blind and `test` is in `BLIND_SPLITS`.

That run directory is not in this checkout; the smoke ran in Colab. Nothing in
this milestone depends on reading it.

---

## 2. The two blockers the real smoke found

Both surfaced **after** minutes of downloading and loading, at the moment a
model was constructed. Both are answerable from installed metadata in
milliseconds.

**1. Nemotron's Mamba kernels were missing.**

```
AutoModelForCausalLM:
ImportError: mamba-ssm is required by the Mamba model but cannot be imported
```

`nvidia/NVIDIA-Nemotron-Nano-9B-v2` is a hybrid Mamba2-Transformer, and its
`trust_remote_code` modelling file imports the Mamba CUDA kernels directly.
They are load-time requirements, not optional accelerators. Repaired with
`mamba-ssm==2.3.2.post1` and `causal-conv1d==1.6.2.post1`.

**2. transformers 4.57.6 could not recognize `qwen3_5`.**

```
The checkpoint has model type qwen3_5 but Transformers does not
recognize this architecture.
```

---

## 3. Which transformers release actually supports `qwen3_5`

Resolved against the upstream tree rather than a changelog or the checkpoint's
own `transformers_version` field — which reads `4.57.0.dev0` and is the version
that *saved* the checkpoint, not one that can load it:

| Tag | `src/transformers/models/qwen3_5/` |
|---|---|
| `v4.57.6` | absent |
| `v5.0.0` | absent |
| `v5.1.0` | absent |
| **`v5.2.0`** | **present (5 files)** |
| `v5.3.0` | present |

PyPI confirms `4.57.6` is the last of the 4.57 line, so nothing in 4.x can load
it. **The floor is `transformers>=5.2.0`.**

### The finding that resolves §3

`Qwen/Qwen3.5-4B` — the **baseline verifier** — is also `model_type: qwen3_5`
with `Qwen3_5ForConditionalGeneration`:

| Model | model_type | architecture |
|---|---|---|
| `Qwen/Qwen3.5-4B` (baseline verifier) | **`qwen3_5`** | `Qwen3_5ForConditionalGeneration` |
| `Qwen/Qwen3.5-9B` (portfolio verifier) | **`qwen3_5`** | `Qwen3_5ForConditionalGeneration` |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `mistral3` | `Mistral3ForConditionalGeneration` |
| `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | `nemotron_h` | `NemotronHForCausalLM` |

So the 5.2.0 floor is **not portfolio-only**, and raising it is **not a
baseline risk** — it records what the baseline already needed. The previous
declaration, `transformers>=4.51.0`, could never have loaded the baseline's own
verifier, and was also below Mistral-Small-3.2's own `4.52.4`. The baseline's
official TEST run must therefore already have used ≥5.2.0.

No baseline compatibility issue was found, so there is nothing to report as a
blocker under §3. The correction is to the *declaration*, not to behaviour.

---

## 4. The fix, in the canonical mechanism

`pyproject.toml` is this repository's dependency contract, so that is where the
versions live — not in ad-hoc Colab pip lines.

```toml
hf = [
    "torch",
    "transformers>=5.2.0",       # was >=4.51.0
    "accelerate",
    "mistral-common>=1.6.2",
]

portfolio = [                    # new extra
    "mamba-ssm==2.3.2.post1",
    "causal-conv1d==1.6.2.post1",
]
```

The Mamba packages are deliberately **not** in `hf`. They are CUDA extensions
that compile on install and the baseline never touches them, so a baseline
machine must not be made to build them. They also build against the installed
torch, which needs pip's build isolation off — a flag a pyproject dependency
cannot carry — so the exact command is documented in `pyproject.toml`, in the
README quickstart, and in the preflight's own error message:

```
pip install -e '.[hf]'
pip install --no-build-isolation 'mamba-ssm==2.3.2.post1' \
                                 'causal-conv1d==1.6.2.post1'
```

Portfolio config metadata corrected in all three portfolio configs:

* Qwen3.5-9B `transformers_requirement: ">=4.57"` → **`">=5.2.0"`**. The old
  value was a false claim: 4.57.6 satisfies it and cannot load the checkpoint.
* Nemotron gains `runtime_dependencies: [mamba-ssm==2.3.2.post1,
  causal-conv1d==1.6.2.post1]` beside its existing `custom_code_files`, with a
  note that its remote code imports them.

Gemma's `>=4.50` and Nemotron's `>=4.51.3` stay as each checkpoint's own
declared minimum; the *effective* floor is their maximum, which a test asserts
equals 5.2.0 and equals what `pyproject.toml` enforces.

No vLLM. `HuggingFaceRuntime` unchanged. Nemotron model, revision and
`trust_remote_code=True` unchanged.

---

## 5. Preflight — fail closed before the download

`src/cover_kbc/models/preflight.py`. It reads the installed distribution's
metadata rather than importing `transformers` (which would pull in torch), and
probes the Mamba packages with `importlib.util.find_spec` rather than importing
them. Nothing here downloads a checkpoint, so the deterministic suite exercises
every path on a machine with no GPU stack.

`run_cover.py` calls it **only when the portfolio strategy is selected**, after
model governance and before the first `build_runtime`:

```python
if strategy.strategy is ModelStrategy.PORTFOLIO:
    require_portfolio_runtime()
```

A baseline run never reaches it — asserted by driving the real `main()` on the
baseline TEST config with the preflight replaced by a counter and requiring
zero calls. Both blockers are reported at once, so an environment two packages
away learns both:

```
the portfolio cannot run in this environment:
  - transformers 4.57.6 does not recognize the 'qwen3_5' architecture;
    5.2.0 is the first release that ships it. Upgrade:
    pip install 'transformers>=5.2.0'
  - NVIDIA-Nemotron-Nano-9B-v2 is a hybrid Mamba2-Transformer and cannot load
    without ['causal_conv1d', 'mamba_ssm']. Install with build isolation off:
    pip install --no-build-isolation 'mamba-ssm==2.3.2.post1'
    'causal-conv1d==1.6.2.post1'
```

---

## 6. Manifest semantics — examined, no change needed

The successful manifest's nested pipeline config serialises
`enable_calibrated_gate: true` and `use_calibration: true` alongside
`production_calibrated: false`. Traced in executable source:

* `enable_calibrated_gate` → Module 4's null/existence gate (`GATE_QUESTIONS`);
* `use_calibration` → Module 4's contextual calibration control for the blind
  verifier, the `control_calls_needed` path Module 17 prices.

Neither appears anywhere in `control/relation_budget.py`,
`control/micro_planner.py`, `control/budget_accounting.py`,
`controller_calibration/production.py` or `controller_calibration/readiness.py`
— asserted by test. They are Module 4 architecture flags, present identically
in the calibrated and uncalibrated configs, so they cannot be what
distinguishes the two experiments and they do not contradict
`production_calibrated: false`.

**No semantic contradiction, so no code was changed** — per the brief's
instruction not to redesign the pipeline for JSON aesthetics. Two tests now pin
the distinction instead, including one asserting a direct manifest records
`controller_mode: direct_uncalibrated`, `production_calibrated: false`,
`calibration_owner: null`, `experiment_variant: portfolio_direct`, and **no
calibration artifact name anywhere** — because none was loaded.

---

## 7. Preserved

Portfolio, byte-for-byte from Audit 0062:

| Role | Model | Revision | Params |
|---|---|---|---:|
| FACTUAL_ENUMERATOR | `google/gemma-3-12b-it` | `96b6f1eccf38110c56df3a15bffe176da04bfd80` | 12,187,325,040 |
| STRUCTURAL_REASONER | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | `6533e8de2c68e4536bf7c411d7a3ce5734111476` | 8,888,227,328 |
| INDEPENDENT_VERIFIER | `Qwen/Qwen3.5-9B` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` | 9,653,104,368 |
| | | **TOTAL** | **30,728,656,736** / 32,000,000,000 |

Routing unchanged; no fourth model. Baseline unchanged: Mistral-Small-3.2-24B +
Qwen3.5-4B, same revisions, prompts, thresholds, controller, and calibration
artifacts (`8110fccb…`, `d6d19493…`, `36315cd7…`, all byte-identical). Baseline
M20/M21 not regenerated.

Current official TEST unchanged and unmodified — 475 rows,
`67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`, ordered
identity `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`.
Audit 0064 remains authoritative for it. Packaging contract unchanged: exactly
`predictions.jsonl` at archive root.

---

## 8. Validation

```
$ python -m pytest tests/test_runtime_preflight.py -q -p no:randomly
42 passed in 0.87s

$ python -m pytest tests/test_runtime_preflight.py tests/test_model_strategy.py \
      tests/test_portfolio_direct_test.py tests/test_mistral_runtime_compat.py \
      tests/test_real_model_smoke_harness.py tests/test_official_test_activation.py \
      tests/test_package_submission.py tests/test_production_activation.py \
      -q -p no:randomly
442 passed, 1 skipped in 7.01s

$ python -m pytest tests/ -q -p no:randomly
3650 passed, 4 skipped in 58.08s

$ python -m pytest tests/ -q                       # randomized order
3650 passed, 4 skipped in 58.55s
```

3606 → 3650: 42 preflight tests plus 2 manifest-semantics tests. No failures,
no xfails; the 4 skips are the pre-existing environment-dependent ones.

Two existing CLI harnesses gained a one-line stub of the preflight
(`tests/test_model_strategy.py`, `tests/test_portfolio_direct_test.py`). They
drive `main()` with scripted runtimes and load no real weights, so the
environment gate has nothing to protect there; its own behaviour is covered in
`tests/test_runtime_preflight.py`. That the gate fired on them at all is
evidence it is wired into the real path.

```
$ python -m pyflakes <every tracked .py + the new modules>
pf=0

$ git diff --check
check=0

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0
$ sha256sum benchmark/data/test.jsonl
67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1
```

The full 475-row official TEST was **not** run, and no TEST evaluation was
performed.

---

## 9. Files changed

```
 7   0  README.md
 9   1  configs/experiments/cover_kbc_v2_portfolio_test.yaml
 9   1  configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml
 9   1  configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml
33   1  pyproject.toml
14   0  scripts/run_cover.py
 4   0  tests/test_model_strategy.py
58   0  tests/test_portfolio_direct_test.py
```

**New:** `src/cover_kbc/models/preflight.py`, `tests/test_runtime_preflight.py`.

The single deletion in each of the three configs is the `>=4.57` line; the one
in `pyproject.toml` is the old `>=4.51.0` floor.

**Not touched:** benchmark data, `benchmark/evaluate.py`, baseline calibration
artifacts, model ids/revisions/parameter counts, routing, M0–M21 semantics,
Direct Uncalibrated controller semantics, `HuggingFaceRuntime`, the packaging
contract, prior audits.

---

## 10. Next

```
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml \
  --model-strategy portfolio

python scripts/package_submission.py --split test \
  --predictions outputs/<run_id>/predictions.jsonl \
  --input benchmark/data/test.jsonl --out test_submission_v2.zip
```

A fresh environment following the repository contract — `pip install -e '.[hf]'`
plus the documented `--no-build-isolation` line — now installs a Transformers
build that can load `qwen3_5` and the kernels Nemotron needs. If either is
missing the run stops before the first download and says which command fixes it.

One carry-over from Audit 0064 for whoever runs the portfolio TRAIN collection
later: it will observe TRAIN at `ad37cd30…`, not the `cb344aa3…` the baseline
was derived from. That is a real difference between experiments A and C and the
ablation table should state it.

---

**PASS — PORTFOLIO REAL-WEIGHT RUNTIME REPRODUCIBLE; FULL TEST READY**

Nothing was committed or pushed.
