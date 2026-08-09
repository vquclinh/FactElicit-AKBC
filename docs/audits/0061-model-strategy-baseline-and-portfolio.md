# Audit 0061 — Two model strategies: frozen baseline, new portfolio

**Date:** 2026-08-09
**Scope:** model/runtime routing, governance, calibration ownership, TRAIN
bake-off harness. No optimisation, no architecture change.
**Canonical safe baseline commit:** `30efcad3980030be1baec77c8ca6bea6f25e4565`
**Official TEST macro-F1 (baseline):** 0.4499
**Python:** 3.14.5

---

## Verdict

**PASS — BASELINE PRESERVED; PORTFOLIO READY FOR REAL-WEIGHT TRAIN BAKE-OFF**

---

## 1. Strategy status — stated, not collapsed

| | BASELINE | PORTFOLIO |
|---|---|---|
| IMPLEMENTED | **YES** | **YES** (routing, governance, calibration ownership) |
| UNIT-TESTED | **YES** | **YES** |
| PIPELINE-TESTED | **YES** | **NO** — the third runtime is not wired into `CoverPipeline` yet |
| REAL-WEIGHT-SMOKED | **YES** (pre-existing) | **NO** |
| TRAIN-BAKEOFF-RUN | n/a | **NO** |
| PRODUCTION-CALIBRATED | **YES** | **NO** — `TRAIN_BAKEOFF_ONLY` |
| REGRESSION VERIFIED | **YES** | n/a |
| OFFICIAL TEST REPRODUCIBLE | **YES** | **NO** — and refused by construction |

Portfolio is `IMPLEMENTED` as a routing and governance layer that **fails
closed**. It cannot make a real call today, and the refusal lists exactly why.

---

## 2. Flag semantics

```
--model-strategy {baseline,portfolio}      default: baseline
```

* No flag → `baseline`. A config with no `model_strategy` key is a baseline
  config, so every committed config keeps its exact prior meaning.
* Unknown value → argparse refuses against `{baseline, portfolio}`;
  `parse_strategy` refuses anything else by name. Case and surrounding
  whitespace are tolerated (a CLI value, not a hash); nothing else is.
* **Config and flag must agree.** No fallback in either direction:

```
baseline config  + --model-strategy portfolio  -> REFUSED ("this config declares baseline")
portfolio config + no flag / --model-strategy baseline -> REFUSED ("declares portfolio")
portfolio config + --model-strategy portfolio  -> ACCEPTED, then governance-gated
```

Both refusals were driven through the real `main()` with `build_runtime`
counted: **0 runtimes built** in each case.

---

## 3. Baseline invariance — the load-bearing result

**The whole change is additive. `git diff --numstat` over tracked files:
97 insertions, 0 deletions.**

| File | +/- |
|---|---|
| `scripts/run_cover.py` | 46 / **0** |
| `src/cover_kbc/controller_calibration/production.py` | 29 / **0** |
| `src/cover_kbc/runtime/manifest.py` | 10 / **0** |
| `src/cover_kbc/types.py` | 12 / **0** |

No existing line of baseline behaviour was rewritten, and **no existing test
file was modified**. Baseline internals were not refactored for elegance.

Verified by test and by execution:

* `cover_kbc_v2_validation.yaml`, `cover_kbc_v2_test.yaml` and
  `cover_kbc_v2_train_collection.yaml` contain no `model_strategy` key and
  resolve to `baseline`;
* both production configs resolve to
  `mistralai/Mistral-Small-3.2-24B-Instruct-2506` @ `95a6d26c…` and
  `Qwen/Qwen3.5-4B` @ `851bf6e8…`, budget 28,671,226,368 ≤ 32,000,000,000,
  `legal: true`;
* the three calibration artifacts are byte-identical (`8110fccb…`,
  `d6d19493…`, `36315cd7…`) and still load through
  `load_production_calibration` — 6 budgets, 64 bins, depth 1;
* `python scripts/run_cover.py --config .../cover_kbc_v2_test.yaml` with no
  flag reaches runtime construction with the Mistral block, and the explicit
  `--model-strategy baseline` produces a **byte-identical** runtime block;
* `CoverPipeline.PHYSICAL_COUNTERS` is unchanged and `pipeline.py` contains no
  reference to `ModelStrategy` — the strategy layer never reached the
  accounting path, nor `relation_budget.py`, `micro_planner.py` or
  `budget_accounting.py`.

---

## 4. One pipeline, one runner

No `CoverPipelineBaseline`, no `CoverPipelinePortfolio`, no second entry point,
no Module 22 — each asserted by test. `class CoverPipeline:` is the only
pipeline class in the tree; `run_cover.py` is the only runner; `M22` appears
nowhere in executable source; there is no majority vote anywhere.

The strategy selects **which checkpoint serves a logical role** and nothing
else. M0 → … → M21 → M8 is untouched, and M8 remains the only thing that emits
a prediction (`write_predictions` is called exactly once, and neither new
module constructs a `Prediction`).

---

## 5. Role routing matrix

`ModelRole` was **extended**, not duplicated — the existing role vocabulary
gains three members alongside `enumerator` / `verifier` / `stub` / `none`.

**Baseline** (unchanged semantics, restated): verification → `VERIFIER`,
everything else → `ENUMERATOR`. Baseline never reaches the heterogeneous table.

**Portfolio**, by operation:

| Operation | Logical role | Checkpoint |
|---|---|---|
| acquisition, parametric_retrieval, specialist_probe, candidate_free_recall | `FACTUAL_ENUMERATOR` | Gemma |
| reverse_check, key_condition, counterfactual | `STRUCTURAL_REASONER` | Nemotron |
| specialist_verify, blind_verify | `INDEPENDENT_VERIFIER` | Qwen |

By relation (the §5 hypothesis, TRAIN-only, never tuned from TEST):

| Relation | factual | structural | verification |
|---|---|---|---|
| hasArea | Gemma | — (M12 keeps numeric handling) | Qwen |
| hasCapacity | Gemma | — (M12 keeps unit handling) | Qwen |
| personHasCityOfDeath | Gemma | — | Qwen |
| awardWonBy | Gemma (M13 still owns the open set) | — | Qwen |
| countryLandBordersCountry | Gemma | **Nemotron** | Qwen |
| companyTradesAtStockExchange | Gemma (M15 still owns discovery/facets) | **Nemotron** (M18 still owns parent/subsidiary) | Qwen |

Candidate-free recall is deliberately `FACTUAL_ENUMERATOR` even though §14
lists it under Module 18: it is recall, not structural reasoning. An operation
with no declared role raises rather than being guessed at.

---

## 6. Models, revisions, parameter counts

| Role | Model | Revision | Params | Verified |
|---|---|---|---|---|
| baseline enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 | **yes** |
| baseline verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 | **yes** |
| | | **baseline total** | **28,671,226,368** ≤ 32B | |
| portfolio factual | `google/gemma-3-12b-it` | **UNPINNED** | **UNRECORDED** | **no** |
| portfolio structural | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | **UNPINNED** | **UNRECORDED** | **no** |
| portfolio verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 | **yes** |

The two unknown counts are recorded as `null` with
`parameter_source_verified: false` **on purpose**. This repository does not
infer a parameter count from a model name, and it has no network access here,
so the honest state is "unproven" and the gate enforces it. The verifier is
pinned to the baseline's exact revision so a portfolio-vs-baseline delta can
never be the judge changing.

Also recorded per portfolio model: license, gated status, tokenizer/processor,
`trust_remote_code`, context length, transformers requirement, quantization and
device map. Gemma is marked `gated: true` with an access route that names the
environment, and the config contains no credential (asserted by test).

Quantization is recorded but never consulted for legality — proven by a test
that audits the same 31B spec under `None`, `nf4`, `int8` and `gptq` and gets
the same counted total, and a 33B spec that fails under `nf4`.

---

## 7. Governance gate — the current portfolio blockers

`require_portfolio_governance` runs **before any runtime is built** and lists
every unmet requirement at once. Live output:

```
model strategy: the portfolio strategy is not ready to make a real call:
  - google/gemma-3-12b-it: no immutable revision is pinned; a moving tag is not
    a reproducible experiment
  - google/gemma-3-12b-it: exact instantiated parameter count is unrecorded.
    Read it from the checkpoint - never from the model name - and record it
    with its source
  - nvidia/NVIDIA-Nemotron-Nano-9B-v2: no immutable revision is pinned; ...
  - nvidia/NVIDIA-Nemotron-Nano-9B-v2: exact instantiated parameter count is
    unrecorded. ...
  - portfolio legality cannot be proven while any parameter count is unrecorded
    or unverified
  - CoverPipeline takes an enumerator and a verifier runtime; the third logical
    role (STRUCTURAL_REASONER) is not wired into it yet
```

A test proves the gate **opens**: with the four facts recorded, only the
declared wiring blocker remains. Another proves a verified-but-over-budget
portfolio is still refused. Nothing substitutes Mistral for an unavailable
Gemma — that would be a baseline run wearing a portfolio label.

### Blockers before the real-weight portfolio bake-off

1. Pin Gemma's immutable commit sha; accept the gated terms; supply the token
   via environment only.
2. Pin Nemotron's immutable commit sha; confirm and justify `trust_remote_code`.
3. Read each checkpoint's **exact instantiated** parameter count from its own
   safetensors index — including any vision tower actually loaded for Gemma —
   record the URL and set `parameter_source_verified`.
4. Confirm the three-model sum ≤ 32,000,000,000.
5. Wire the third runtime (`STRUCTURAL_REASONER`) into `CoverPipeline`'s
   construction and its call routing.
6. A100 40GB NF4 feasibility check for three concurrent checkpoints.

---

## 8. Calibration ownership

The rule this milestone turns on. `check_calibration_strategy` is enforced
inside `load_production_calibration`, and it defaults to the config's own
declared strategy — so every existing caller behaves identically.

* An artifact whose provenance names no strategy **is baseline**. True of all
  three shipped packages (asserted), and it keeps their bytes and hashes
  untouched.
* A portfolio run loading the baseline packages is refused:
  `M20 budget: this calibration was derived under model_strategy 'baseline' but
  the run declares 'portfolio'; changed models change expected gain, cost, FP
  risk and the transition history`.
* A baseline run loading a future portfolio package is refused symmetrically.
* Before any of that, `run_cover.py` refuses a production split for a strategy
  whose status is `TRAIN_BAKEOFF_ONLY` — a stronger objection than any
  individual readiness check, so it is checked first.

---

## 9. Evidence provenance and physical accounting

The manifest now records `model_strategy`, `model_strategy_requested`,
`model_strategy_status` and `model_strategy_profile` — the last carrying every
logical role with its model id, immutable revision, parameter count, verified
flag and quantization. Requested and effective are separate fields because
there is no fallback: a difference would be a defect, and a paper ablation must
see it rather than infer it.

Physical accounting is untouched. `PHYSICAL_COUNTERS` is unchanged, one
forward/generate/`score_labels` is one physical call whichever model served it,
a cache hit is zero, and no strategy abstraction sits between a logical action
and its physical cost — `pipeline.py` does not import the strategy module at
all. Cross-model X support still requires genuine independent recall; nothing
here makes "a different model_id" sufficient.

---

## 10. TRAIN-only bake-off harness

`src/cover_kbc/models/bakeoff.py`. Overall F1 cannot answer this milestone's
question — a portfolio can find more true positives *and* introduce more false
positives and land on the same number — so the comparison is decomposed the way
the architecture is.

**Candidate generation**, per relation: TP, FP, recall, precision, **novel TPs
relative to baseline**, lost TPs, redundant candidates, physical calls, prompt
tokens, generated tokens, latency.

**Verification / structural**, per relation × action family: TPs preserved, TPs
recovered, FPs prevented, FPs introduced, UNKNOWN rate, mean disagreement,
successor-state effects, cost.

Both emit fixed-width tables and `ablation_rows()` for the paper. **TRAIN
only**: every entry point calls `require_train`, and the module contains no
reference to the evaluator, `benchmark`, `val.jsonl` or `test.jsonl` (asserted).

---

## 11. Files changed

**Modified (additive only, 0 deletions)**
* `src/cover_kbc/types.py` — three `ModelRole` members
* `src/cover_kbc/controller_calibration/production.py` — strategy ownership check
* `src/cover_kbc/runtime/manifest.py` — four strategy fields
* `scripts/run_cover.py` — `--model-strategy`, resolution, governance gate,
  production-status refusal, manifest recording

**New**
* `src/cover_kbc/models/strategy.py` — strategies, roles, routing, governance,
  calibration ownership
* `src/cover_kbc/models/bakeoff.py` — TRAIN-only comparison harness
* `configs/experiments/cover_kbc_v2_portfolio_train_bakeoff.yaml`
* `tests/test_model_strategy.py` — 100 tests

**Not touched:** M0–M21 semantics, prompts, thresholds, decoding, scoring,
controller, M20/M21 numbers, calibration artifacts, the three baseline configs,
packaging, `benchmark/`, the official TEST submission.

No `cover_kbc_v2_portfolio_validation.yaml` or `..._test.yaml` was created —
they may only exist after a portfolio calibration does (asserted by test).

---

## 12. Validation

```
$ python -m pytest tests/test_model_strategy.py -q -p no:randomly
100 passed in 2.88s

$ python -m pytest tests/ -q -p no:randomly
3519 passed, 4 skipped in 56.85s

$ python -m pytest tests/ -q            # randomized order
3519 passed, 4 skipped in 56.32s
```

3419 → 3519: the 100 new tests. No skips or xfails added; no existing test
changed.

All 27 required regression properties are covered, plus the governance gate and
four dynamic CLI probes.

```
$ python -m pyflakes <every tracked .py> + the three new files
exit=0            # no output

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0

$ sha256sum configs/calibration/*.json
8110fccb…c15c68  m20_relation_budget.json
d6d19493…bcd071  m21_historical_bins.json
36315cd7…f9ce05  m21_planner_calibration.json
```

**Did not happen:** no Gemma or Nemotron weights downloaded, no TRAIN
inference, no VAL run, no TEST run, no portfolio M20/M21 derivation, no change
to the official TEST submission, no commit, no push.

---

## 13. Paper readiness

Every run artifact makes the strategy recoverable without guessing, so the
paper can truthfully report:

```
COVER-KBC Baseline       model_strategy=baseline    Mistral + Qwen
                         official TEST macro-F1 = 0.4499
                         awardWonBy 0.2565 | stock 0.6305 | borders 0.9264
                         hasArea 0.2500 | hasCapacity 0.1224 | death 0.4900

COVER-KBC Heterogeneous  model_strategy=portfolio   Gemma + Nemotron + Qwen
                         result to be measured
```

and the controlled ladder — baseline, + factual specialist, + structural
specialist, + role-specialised routing, + portfolio-specific M20/M21 — is
expressible because each rung is a recorded `model_strategy` plus a recorded
calibration owner, not a filename convention.

---

**PASS — BASELINE PRESERVED; PORTFOLIO READY FOR REAL-WEIGHT TRAIN BAKE-OFF**

subject to the six blockers in §7, which the gate enforces rather than
documents. Nothing was committed or pushed.
