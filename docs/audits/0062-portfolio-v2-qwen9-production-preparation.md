# Audit 0062 — Portfolio v2 (Gemma + Nemotron + Qwen3.5-9B) production preparation

**Date:** 2026-08-09
**Scope:** activate the second model strategy for Submission #2. Routing,
governance, third runtime, calibration ownership, portfolio configs, tests.
**Python:** 3.14.5

---

## Verdict

**PASS — PORTFOLIO v2 CODE READY FOR REAL-WEIGHT CALIBRATION-TO-TEST RUN**

The decisive gate: **30,728,656,736 ≤ 32,000,000,000**, with 1,271,343,264 to
spare. Every count is the checkpoint's own `safetensors.total`, read from the
Hugging Face API, not inferred from a name.

---

## 1. Starting Git state

```
HEAD 30efcad3980030be1baec77c8ca6bea6f25e4565  Activate official TEST production path
     0597673e4b8a33521850252b5c63b71f00730cc3  final artifact activation full validation
```

Working tree carried the uncommitted Audit 0061 milestone. Official TEST
macro-F1 0.4499 (awardWonBy 0.2565, stock 0.6305, borders 0.9264, hasArea
0.2500, hasCapacity 0.1224, death 0.4900).

---

## 2. Status table

| | BASELINE | PORTFOLIO v2 |
|---|---|---|
| IMPLEMENTED | **YES** | **YES** |
| UNIT_TESTED | **YES** | **YES** |
| SCRIPTED_PIPELINE_TESTED | **YES** | **YES** |
| PARAMETER_LEGAL | **YES** (28,671,226,368) | **YES** (30,728,656,736) |
| PRODUCTION_CALIBRATED | **YES** | **NO** |
| REAL_WEIGHT_SMOKED / REAL-WEIGHT_VERIFIED | **YES** | **NO** |
| TRAIN_COLLECTION_RUN | n/a | **NO** |
| OFFICIAL_TEST_READY | **YES** | **NO** — `NOT_READY`, as expected |
| OFFICIAL_TEST_RUN / REPRODUCIBLE | **YES** (0.4499) | **NO** |

Portfolio is **not** production calibrated. No fixture artifact was created and
none would change that.

---

## 3. Baseline preservation proof

`git diff --numstat` over tracked files — the baseline-bearing ones are
additive, and the only deletions are the four lines of `physical_snapshot` /
M18 routing that were *replaced by supersets of themselves*:

```
68  4  scripts/run_cover.py
45  0  src/cover_kbc/controller_calibration/production.py
33  4  src/cover_kbc/pipeline.py
10  0  src/cover_kbc/runtime/manifest.py
12  0  src/cover_kbc/types.py
```

Dynamic proof that the replacement is a no-op for two models:

```
two-model snapshot : {'enumerator_calls': 26, 'verifier_calls': 0,
                      'physical_calls': 26, 'generated_tokens': 20}
single_role_profile: True
structural is runtime: True
partition sums     : True
```

26 / 20 are byte-for-byte the numbers Audit 0054 recorded for the same fixture.

Also verified by test: the three baseline configs contain no `model_strategy`
key and resolve to `baseline`; their model ids, revisions and
`budget_assertion` are unchanged; the three calibration hashes are unchanged
(`8110fccb…`, `d6d19493…`, `36315cd7…`) and still load through
`load_production_calibration` (6 budgets, 64 bins, depth 1); the baseline TEST
config still reaches `FULL_TEST_READY`; `package_submission` baseline behaviour
is unchanged; and baseline never constructs a third runtime.

---

## 4. Flag semantics

```
--model-strategy {baseline,portfolio}      default: baseline
```

No flag → baseline. A config with no `model_strategy` **is** a baseline config.
Config and flag must agree; there is no fallback in either direction. An
explicit `--model-strategy baseline` produces a **byte-identical** runtime
block to no flag (asserted). Unknown values are refused by argparse and again
by `parse_strategy`.

---

## 5. Portfolio v2 role table

| Logical role | Model | Operations |
|---|---|---|
| `FACTUAL_ENUMERATOR` | `google/gemma-3-12b-it` | acquisition, parametric_retrieval, specialist_probe, candidate_free_recall |
| `STRUCTURAL_REASONER` | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | reverse_check, key_condition, counterfactual |
| `INDEPENDENT_VERIFIER` | `Qwen/Qwen3.5-9B` | specialist_verify, blind_verify |

Routing is a table lookup on the operation, never a string match on a model
name, and an undeclared operation raises. Baseline never reaches this table:
its routing is the old two-role split (verification → verifier, everything else
→ enumerator).

---

## 6-11. Exact governance and the 32B verdict

Resolved from each repository's own metadata (`/api/models/<repo>`), not from
its name.

| | Gemma | Nemotron | Qwen3.5-9B |
|---|---|---|---|
| Repository | `google/gemma-3-12b-it` | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | `Qwen/Qwen3.5-9B` |
| Revision | `96b6f1eccf38110c56df3a15bffe176da04bfd80` | `6533e8de2c68e4536bf7c411d7a3ce5734111476` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| License | `gemma` | NVIDIA Open Model License | `apache-2.0` |
| Gated | **yes (manual)** | no | no |
| Architecture | `Gemma3ForConditionalGeneration` | `NemotronHForCausalLM` | `Qwen3_5ForConditionalGeneration` |
| model_type | gemma3 | `nemotron_h` | `qwen3_5` |
| Tokenizer / processor | `AutoProcessor` | `AutoTokenizer` | `AutoProcessor` |
| `trust_remote_code` | no | **yes** | no |
| Custom code | — | `configuration_nemotron_h.py`, `modeling_nemotron_h.py` | — |
| transformers | ≥ 4.50 | ≥ 4.51.3 | ≥ 4.57 |
| Context | 128,000 | 131,072 | 262,144 |
| Quantization | nf4 compatible | nf4 compatible | nf4 compatible |
| **Instantiated params** | **12,187,325,040** | **8,888,227,328** | **9,653,104,368** |
| dtype breakdown | BF16 12,187,325,040 | BF16 8,888,227,328 | BF16 9,653,100,528 + F32 3,840 |
| Source | `api/models/google/gemma-3-12b-it` (`safetensors.total`) | same, nvidia repo | same, Qwen repo |

```
12,187,325,040 + 8,888,227,328 + 9,653,104,368 = 30,728,656,736
                                        limit =  32,000,000,000
                                       LEGAL  =  YES  (1,271,343,264 spare)
```

### Multimodal / text-only accounting rationale

**Both Gemma 3 and Qwen3.5-9B are multimodal, and both towers are counted.**

Gemma's `pipeline_tag` is `image-text-to-text` and its official class is
`Gemma3ForConditionalGeneration`; Qwen3.5-9B likewise declares a `vision_config`
and `Qwen3_5ForConditionalGeneration`, with **333 of its 775 tensors
vision-prefixed** (`model.visual.*`). Those classes instantiate the vision tower
whether or not a prompt contains an image. This system loads the official
classes, so the full `safetensors.total` is the honest count.

The alternative — counting only the language stack — would assert a supported,
reproducible text-only model class that we do not load. The brief forbids
excluding a component the runtime still instantiates, and the conservative
reading is legal anyway, so there is no reason to argue the narrower one.
Nemotron is text-only and contributes no such question.

Quantization is recorded for reproducibility and never consulted for legality:
a test audits the same spec under `None` / `nf4` / `int8` / `gptq` and gets the
same counted total, and a 33B spec fails under `nf4`.

### Access

Gemma is `gated: manual`. The config records the access route (accept the terms
on Hugging Face; supply the token through the environment or HF cache) and
**no credential** — asserted by a test that scans for `hf_`, `token:` and
`api_key`. There is no fallback: without access the run fails with that reason
rather than substituting Mistral or the 4B verifier.

---

## 12. Third runtime — Audit 0061's blocker, closed

`CoverPipeline` gained an optional `structural_runtime`, defaulting to `None` →
`self.runtime`. §14's mechanisms now route to `self.structural_runtime`.

* **Baseline**: no third runtime is passed, `structural_runtime is runtime`, and
  every expression reduces to the pre-existing one. Verified dynamically above.
* **Portfolio**: `run_cover.py` builds one runtime per logical role and passes
  all three.

`PORTFOLIO_IMPLEMENTATION_BLOCKERS` is now `()`, asserted by test.

### Physical accounting

`PHYSICAL_COUNTERS` is unchanged. The partition has always been two buckets —
verifier and non-verifier — and a second non-verifier checkpoint joins the
enumerator bucket, so the `enumerator + verifier == physical` identity that
`physical_delta` enforces still holds exactly. Only *distinct* runtime objects
are summed, by `is`-identity, so a two-model profile is unaffected.

Per-model attribution lives in the evidence provenance, where it belongs; these
counters exist to make the hard cap and settlement exact, and they remain
exact. Scripted three-runtime test:

```
snapshot["physical_calls"] == gemma.calls + nemotron.calls + qwen9.calls
snapshot["enumerator_calls"] == gemma.calls + nemotron.calls
snapshot["verifier_calls"]   == qwen9.calls
enumerator + verifier == physical
```

Reserve → execute → settle/cancel, and `AccountingInvariantError` as
process-fatal, are untouched (their suites pass unchanged: 32 and 20 tests).

---

## 13. Scripted pipeline test (§30) — **not** a real-weight smoke

`tests/test_model_strategy.py` drives the canonical `CoverPipeline` with three
distinct scripted runtimes and shows: all three are reachable; M18 reaches the
structural runtime; evidence records each model (`model_family_summary()` shows
both `gemma` and `nemotron`, and Module 17's verdicts are attributed to
`offline/qwen9-role`); physical accounting is exact; and production Layer 6 is
unavailable without portfolio calibration.

Cross-model evidence still requires genuine independent recall — asserted both
on the graph and in the owner: `evidence/consensus.py` contains no `model_id ==`
or `model_id !=` comparison.

---

## 14. Calibration ownership

Fails closed in both directions, and now at revision granularity.

* An artifact naming no strategy **is baseline** — true of the three shipped
  packages, and byte-preserving.
* Portfolio run + baseline calibration → refused
  (`derived under model_strategy 'baseline' but the run declares 'portfolio'`).
* Baseline run + portfolio calibration → refused symmetrically.
* **Portfolio A + portfolio B calibration → refused.** `portfolio_fingerprint`
  is SHA256 over each role's `model_id` and `revision`; bumping *any* revision —
  the verifier included — changes it, and `check_calibration_portfolio` refuses
  the mismatch. Enforced inside `load_production_calibration`.
* Before all of that, `run_cover.py` refuses an official split for a strategy
  whose status is `TRAIN_BAKEOFF_ONLY`.

---

## 15. Portfolio configs

**`configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml`** —
`split: train`, `model_strategy: portfolio`, the exact three-model profile with
verified counts, Layer 6 in **shadow** (no portfolio calibration exists, and a
shadow M20/M21 governs nothing). Everything below the model profile is the
frozen baseline profile, so the portfolio is the single independent variable.

**`configs/experiments/cover_kbc_v2_portfolio_test.yaml`** — `split: test`,
Layer 6 in production pointing at `../calibration/portfolio/*.json` with empty
SHA256s and empty provenance SHAs, all marked PENDING. The readiness gate
returns **`NOT_READY`** (asserted), which is correct: those files do not exist.
It becomes `FULL_TEST_READY` only when the real artifacts are installed and
hash-matched.

No `cover_kbc_v2_portfolio_validation.yaml` — VAL is deliberately skipped.
`configs/calibration/portfolio/` contains no artifact (asserted).

The v1 bake-off config was replaced by the collection config; the three
baseline configs are untouched.

---

## 16. TRAIN collection and derivation readiness

The existing collection runner is reused, not duplicated: its checkpoint /
resume guarantees (exact run identity, ordered query identity, no duplicated
completed rows, no silently skipped unresolved failures, preserved revisions
and config hash) already meet §19, and `AccountingInvariantError` is already in
its `FATAL_ERRORS`. The existing offline derivation algorithm is reused
unchanged — no new formulas, and C-02 stands: if ΔH is still structurally zero
over the observed action space, γ stays 0.0.

The TRAIN-only bake-off harness from Audit 0061 (`models/bakeoff.py`) is intact
and still refuses any split but `train`.

---

## 17. A100 40GB feasibility — **ESTIMATED**

NF4 at 4 bits/param plus double-quantised scales (~0.127 bits/param):

| Model | Params | NF4 |
|---|---:|---:|
| gemma-3-12b-it | 12,187,325,040 | 5.86 GiB |
| Nemotron-Nano-9B-v2 | 8,888,227,328 | 4.27 GiB |
| Qwen3.5-9B | 9,653,104,368 | 4.64 GiB |
| **Weights total** | **30,728,656,736** | **14.76 GiB** |

Plus CUDA/torch reserve ≈ 1.80 GiB and KV cache for one interleaved query
≈ 2.50 GiB → **estimated peak ≈ 19.06 GiB** against ≈ 39.50 GiB usable:
**headroom ≈ 20.44 GiB**.

Comfortable, and **ESTIMATED** — not measured. The real-weight smoke
establishes MEASURED.

---

## 18. Tests

`tests/test_model_strategy.py` — **125 tests**, covering all 18 baseline
regression properties (§28) and all 50 portfolio properties (§29), plus the
governance gate, the fingerprint, four dynamic CLI probes and the scripted
three-runtime integration.

```
$ python -m pytest tests/test_model_strategy.py -q -p no:randomly
125 passed in 2.41s

$ python -m pytest <the seven targeted suites> -q -p no:randomly
310 passed, 1 skipped in 19.11s

$ python -m pytest tests/ -q -p no:randomly
3544 passed, 4 skipped in 50.34s

$ python -m pytest tests/ -q                       # randomized order
3544 passed, 4 skipped in 52.98s
```

3519 → 3544. No failures, **no xfails**, and the 4 skips are the pre-existing
environment-dependent ones. Nothing hidden.

```
$ python -m pyflakes <every tracked .py + the new modules>
pf=0                # no output

$ git diff --check
check=0
```

### One real defect found and fixed mid-milestone

Adding `from _bootstrap import ensure_src_on_path` to
`scripts/package_submission.py` made it importable only when `scripts/` was
already on `sys.path`. It passed in the full suite purely because another test
had inserted that path first — an order-dependent pass, which is worse than a
failure. `package_submission.py` now adds its own directory explicitly;
`tests/test_package_submission.py` passes standalone (9 tests) and in the suite.

---

## 19. Benchmark integrity

```
$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0
```

Baseline calibration artifacts unchanged: `8110fccb…`, `d6d19493…`,
`36315cd7…`.

---

## 20. Files changed

**Modified**
* `src/cover_kbc/types.py` (+12/−0) — three `ModelRole` members
* `src/cover_kbc/pipeline.py` (+33/−4) — optional `structural_runtime`, M18
  routing, three-runtime physical accounting
* `src/cover_kbc/controller_calibration/production.py` (+45/−0) — strategy and
  portfolio-fingerprint ownership
* `src/cover_kbc/runtime/manifest.py` (+10/−0) — strategy fields
* `scripts/run_cover.py` (+68/−4) — `--model-strategy`, governance gate,
  three-runtime construction, manifest recording
* `scripts/package_submission.py` — self-contained bootstrap (see §18)

**New**
* `src/cover_kbc/models/strategy.py` — strategies, roles, routing, governance,
  portfolio v2 profile, fingerprint, calibration ownership
* `src/cover_kbc/models/bakeoff.py` — TRAIN-only comparison harness
* `configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml`
* `configs/experiments/cover_kbc_v2_portfolio_test.yaml`
* `tests/test_model_strategy.py`

**Removed:** `configs/experiments/cover_kbc_v2_portfolio_train_bakeoff.yaml`
(the v1 draft, superseded).

**Not touched:** M0–M21 semantics, prompts, thresholds, decoding, scoring,
selection, controller, M20/M21 formulas, baseline calibration artifacts, the
three baseline configs, the TEST packager contract, `benchmark/`, the first
official TEST submission.

**Did not happen:** no weights downloaded, no TRAIN/VAL/TEST inference, no
portfolio calibration derived, no submission, no commit, no push.

---

## 21. Blockers before real-weight execution

None that code can close. The remaining items are environmental and are the
first steps of the Colab sequence:

1. Accept the Gemma gated terms on the account that will run it, and expose the
   token through the environment (never in Git).
2. Confirm the Colab image satisfies `transformers >= 4.57` — Qwen3.5-9B
   declares `4.57.0.dev0`, the highest of the three requirements.
3. `trust_remote_code=True` is required for Nemotron and is recorded; it will
   execute `configuration_nemotron_h.py` and `modeling_nemotron_h.py` from the
   pinned revision.
4. A100 40GB feasibility is **ESTIMATED**, not measured.

---

## 22. Next real-world sequence

1. Commit and push this milestone.
2. Fresh Colab checkout at that exact SHA.
3. Authenticate Gemma access through the environment only.
4. Real-weight three-model smoke →
   `python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml --model-strategy portfolio --limit 3`
5. Full 477-row portfolio TRAIN calibration collection.
6. Offline derive portfolio M20/M21 from the new telemetry.
7. Install artifacts under `configs/calibration/portfolio/`, fill the three
   SHA256s and `derivation_repo_sha` in `cover_kbc_v2_portfolio_test.yaml`.
8. Confirm `FULL_TEST_READY` with zero blockers.
9. Full official TEST 477/477 —
   `python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_portfolio_test.yaml --model-strategy portfolio`
10. `python scripts/package_submission.py --split test --predictions outputs/<run_id>/predictions.jsonl --input benchmark/data/test.jsonl --out test_submission_v2.zip`
11. Submit to Codabench.
12. Record overall and per-relation leaderboard scores against baseline 0.4499.

---

**PASS — PORTFOLIO v2 CODE READY FOR REAL-WEIGHT CALIBRATION-TO-TEST RUN**

Nothing was committed or pushed.
