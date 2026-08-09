# Audit 0063 — Portfolio direct TEST, without M20/M21

**Date:** 2026-08-09
**Scope:** an explicit, reproducible uncalibrated portfolio TEST path for
Submission #2. No new model strategy, no new pipeline, no calibration.
**Python:** 3.14.5

---

## Verdict

**PASS — PORTFOLIO DIRECT READY FOR REAL-WEIGHT OFFICIAL TEST**

---

## 1. Status

| | BASELINE | PORTFOLIO DIRECT | PORTFOLIO CALIBRATED |
|---|---|---|---|
| Config preserved / present | **YES**, untouched | **NEW** | **YES**, untouched |
| Models exact | Mistral24 + Qwen4 | Gemma12 + Nemotron9 + Qwen9 | same as direct |
| Parameter legal | **YES** 28,671,226,368 | **YES** 30,728,656,736 | **YES** |
| Production calibrated | **YES** | **NO** | **NO** |
| M20 controlling | yes | **NO** | not yet |
| M21 controlling | yes | **NO** | not yet |
| Scripted pipeline tested | YES | **YES** | n/a |
| Real-weight smoked | YES | **NO** | **NO** |
| TEST ready | **`FULL_TEST_READY`** | **`FULL_TEST_DIRECT_READY`** | **`NOT_READY`** |
| Official TEST run | **YES** — 0.4499 | pending | pending |

The three are experiments A, B and C. They are separate readiness states,
separate configs and separate manifest records; nothing lets B be reported as
A or C.

---

## 2. Exact direct execution semantics

The honest answer to "run the architecture without M20/M21" was not `shadow`.
`IntegrationMode.SHADOW` means *Layer 4 does not execute at all* —
`_select_actions` returns `()` and no verification or structural evidence
reaches Module 8. A shadow run would have measured the pre-upgrade core with
new models, not the upgraded architecture, and §13's verification and
structural traces would not exist.

The execution-complete uncalibrated path already in this system is the one the
TRAIN collection uses: real seams, evidence reaching Module 8, actions chosen
by a **fixed deterministic policy** instead of calibrated utility. That mode is
hard-gated to TRAIN by design (`train_split_only`), with the documented reason
that pointing it at TEST would spend the blind split on diagnostics.

So this milestone adds one **integration mode** — not a model strategy, of
which there are still exactly two:

```
IntegrationMode.DIRECT_UNCALIBRATED = "direct_uncalibrated"
```

* `may_mutate_production_state` → **True** (Layer-4 evidence reaches M8)
* `uses_calibrated_controller` / `production_calibrated` → **False**
* `train_split_only` → **False**
* action choice → `TrainCollectionPolicy`, the existing deterministic policy.
  **No new heuristic was invented.**

`uses_calibrated_controller` is true for `PRODUCTION` and nothing else, which
is asserted for every member of the enum.

---

## 3. Proof that no baseline M20/M21 affects a direct decision

Four independent mechanisms, each tested:

1. **The config names no artifact.** `relation_budget_scheduler.calibration_file`,
   `micro_planner.historical_bins` and `.planner_calibration` are all `null`;
   there is no `calibration_provenance` block; and the three baseline artifact
   filenames appear nowhere in the file.
2. **The gate refuses one if it appeared.** Naming any calibration artifact, or
   enabling M20/M21, or putting either in production mode, makes
   `evaluate_direct_test_readiness` return `NOT_READY`.
3. **The runner loads none.** The direct branch never calls
   `load_production_calibration`; it is in the `if production:` branch, which a
   direct run does not enter, and the two are mutually exclusive by an explicit
   `SystemExit`.
4. **The pipeline cannot consult one.** `_precharge` short-circuits before any
   ledger is constructed:

```python
if self.integration_mode.is_collection or self.integration_mode.is_direct:
    return True, "", None
ledger = self._budget_ledger_for(graph)
```

and `_select_actions` routes to Module 21 only under
`self.integration_mode.is_production`.

Belt and braces: `build_relation_budget_scheduler` already *raises* rather than
constructing an enabled scheduler with no TRAIN-derived values, so a config
that tried to enable M20 without calibration would fail at construction.

---

## 4. Hard safety caps that remain

Uncalibrated is not unbounded. Every cap below is an **architecture** cap —
declared in `pipeline:` or built into Module 7 — and none is TRAIN-derived:

| Cap | Value | Owner |
|---|---|---|
| `max_calls_per_query` | 12 | M7 |
| `max_steps_per_query` | 12 | M7 |
| `max_generated_tokens_per_query` | 6000 | M7 |
| `max_control_rounds_per_catalogue` | 3 | M7 / catalogue bound |
| per-family action bound | collection policy default | deterministic policy |
| physical-call accounting | `PHYSICAL_COUNTERS` unchanged | M7 |
| `AccountingInvariantError` | process-fatal, ahead of the generic handler | M7 |
| reservation lifecycle | reserve → execute → settle/cancel (unused here: no ledger) | M20 |
| cache semantics | unchanged | M4/M17 |
| context limits | per model, recorded in the profile | model plane |

The direct config's `pipeline:` block is asserted **equal** to the calibrated
portfolio config's, so these caps are not quietly different between B and C.

What is *absent* is the TRAIN-calibrated M20 relation budget — exactly the
distinction §7 asked for, and no substitute was invented for it.

---

## 5. Readiness

New state `ReadinessState.FULL_TEST_DIRECT_READY` and
`ReadinessReport.may_run_test_direct`, both deliberately **not** satisfied by
`FULL_TEST_READY` and vice versa.

`evaluate_direct_test_readiness` requires: split `test`; every M9–M19 module
enabled; M20/M21 **not** enabled and **not** in production and naming no
artifact; a declared `pipeline.mode`; every model with an immutable revision
and a verified count; total ≤ 32B; and the dataset byte-, row-, order-exact and
blind. The dataset check is now a shared helper both TEST gates call, so they
cannot drift.

Live:

```
DIRECT     : FULL_TEST_DIRECT_READY  may_run_test_direct=True
             may_run_test=False  may_run_validation=False  blockers=0
  satisfied: split: test
             M20 and M21 govern nothing (uncalibrated by design)
             pipeline.mode: interleaved
             parameter budget: 30,728,656,736 <= 32,000,000,000
             test dataset: sha256 849f565d6fcf... | 477 rows
             ordered identity 1bce6d40f843... | blind (no row carries objects)

CALIBRATED portfolio test : NOT_READY        (unchanged)
BASELINE test / validation: FULL_TEST_READY / FULL_VALIDATION_READY, 0 blockers
```

---

## 6. Models

Unchanged from Audit 0062, and asserted identical between the direct and
calibrated configs:

| Role | Model | Revision | Params |
|---|---|---|---:|
| `FACTUAL_ENUMERATOR` | `google/gemma-3-12b-it` | `96b6f1eccf38110c56df3a15bffe176da04bfd80` | 12,187,325,040 |
| `STRUCTURAL_REASONER` | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | `6533e8de2c68e4536bf7c411d7a3ce5734111476` | 8,888,227,328 |
| `INDEPENDENT_VERIFIER` | `Qwen/Qwen3.5-9B` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` | 9,653,104,368 |
| | | **TOTAL** | **30,728,656,736** ≤ 32,000,000,000 |

Routing is unchanged: factual → Gemma, structural → Nemotron, verification →
Qwen9, undeclared operation → fail closed.

---

## 7. End-to-end scripted run through the real CLI

`run_cover.main()` on a two-row synthetic blind split, scripted runtimes, no
weights:

```
readiness   : FULL_TEST_DIRECT_READY (uncalibrated)
strategy    : portfolio (TRAIN_BAKEOFF_ONLY)
controller  : direct_uncalibrated  (production_calibrated=False)
execution   : interleaved (from config)
runtimes built : ['google/gemma-3-12b-it',
                  'nvidia/NVIDIA-Nemotron-Nano-9B-v2',
                  'Qwen/Qwen3.5-9B']
artifacts   : predictions.jsonl, trace.jsonl, calls.jsonl, manifest.json,
              query_profiles, prompt_programs, parametric_memory,
              small_set_specialist, atomic_consensus,
              specialist_verification, bidirectional_verification,
              layer4_evidence, coverage_gap
split 'test' is blind; no evaluation and no metrics.json.
exit code 0
```

Layer 4 genuinely executed — `specialist_verification.jsonl`,
`bidirectional_verification.jsonl`, `layer4_evidence.jsonl` and
`coverage_gap.jsonl` are all present, which is precisely what `shadow` would
not have produced. No `relation_budget.jsonl` and no `micro_planner.jsonl`,
because M20/M21 governed nothing.

Manifest:

```
model_strategy            : 'portfolio'
model_strategy_requested  : 'portfolio'
controller_mode           : 'direct_uncalibrated'
production_calibrated     : False
calibration_owner         : None
experiment_variant        : 'portfolio_direct'
model_strategy_profile.roles: the three model ids, revisions and counts
```

No baseline model was built. **This is a scripted test, not a real-weight
smoke.**

---

## 8. Two real defects found and fixed

**The Audit 0062 portfolio TRAIN collection config could not have run.** It
declared `relation_budget_scheduler.enabled: true` with no calibration;
`build_relation_budget_scheduler` raises in exactly that case, so pipeline
construction would have failed at the start of the 477-row run. The canonical
baseline collection config has Layer 6 `enabled: false`, and the portfolio one
now matches it. Caught by building the direct config from it.

**The stateful selector had no per-query reset.** The deterministic policy
round-robins action families *within* a query; without `begin_query` its
position would carry across queries and the first family would win every time.
`enumerate_query` now calls `_begin_query_for_selector`, which is a no-op for a
plain callable — every baseline path.

---

## 9. Baseline preservation

`git diff --numstat` on baseline-bearing files: the deletions are lines
replaced by supersets of themselves (`readiness.py`'s dataset block factored
into a shared helper, `pipeline.py`'s precharge condition and `physical_snapshot`).

```
  6   0  scripts/package_submission.py
138   6  scripts/run_cover.py
 45   0  src/cover_kbc/controller_calibration/production.py
238  69  src/cover_kbc/controller_calibration/readiness.py
  1   0  src/cover_kbc/evidence/production_bridge.py
 44   1  src/cover_kbc/integration_mode.py
 53   5  src/cover_kbc/pipeline.py
 21   0  src/cover_kbc/runtime/manifest.py
 12   0  src/cover_kbc/types.py
 23   2  tests/test_integration_mode.py
```

Asserted by test: baseline no-flag and explicit-flag both resolve baseline; the
three baseline configs are unchanged and still carry no `model_strategy`; the
three baseline calibration hashes are unchanged and still load; baseline VAL
and TEST still return `FULL_VALIDATION_READY` / `FULL_TEST_READY` with zero
blockers; `PHYSICAL_COUNTERS` and the fatal-accounting ordering are unchanged;
and the packager contract is unchanged.

One pre-existing test was updated deliberately:
`test_exactly_three_modes_exist` → `test_exactly_four_modes_exist`, since the
mode vocabulary is stated there once on purpose. It gained three companions
asserting the new mode's properties rather than merely its existence.
`ProductionEvidenceBridge.SUPPORTED_MODES` gained the new mode — a direct run
must be able to apply its evidence.

---

## 10. Packaging

Untouched. `ARCHIVE_MEMBER = "predictions.jsonl"`, the archive must contain
exactly that one member, and no manifest, log, telemetry, calibration or config
is written into the ZIP. A direct run's predictions package identically to a
baseline run's — the submission format is strategy-independent.

---

## 11. Tests

`tests/test_portfolio_direct_test.py` — **59 tests**, covering all 30 required
properties plus the end-to-end CLI run, parametrised refusals for every model
role and every dataset pin, and the M9–M19 stack requirement.

```
$ python -m pytest tests/test_portfolio_direct_test.py -q -p no:randomly
59 passed in 1.71s

$ python -m pytest <the eight targeted suites> -q -p no:randomly
374 passed, 1 skipped in 18.21s

$ python -m pytest tests/ -q -p no:randomly
3606 passed, 4 skipped in 51.07s

$ python -m pytest tests/ -q                       # randomized order
3606 passed, 4 skipped in 51.77s
```

3544 → 3606. No failures, **no xfails**; the 4 skips are the pre-existing
environment-dependent ones.

```
$ python -m pyflakes <every tracked .py + the new modules>
pf=0

$ git diff --check
check=0

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0

$ sha256sum configs/calibration/*.json
8110fccb…c15c68   d6d19493…bcd071   36315cd7…f9ce05
```

---

## 12. Files changed

**Modified**
* `src/cover_kbc/integration_mode.py` — `DIRECT_UNCALIBRATED`, `is_direct`,
  `uses_calibrated_controller`, `production_calibrated`
* `src/cover_kbc/pipeline.py` — precharge short-circuit for direct;
  `_begin_query_for_selector`
* `src/cover_kbc/controller_calibration/readiness.py` —
  `FULL_TEST_DIRECT_READY`, `may_run_test_direct`,
  `evaluate_direct_test_readiness`, shared `_check_blind_test_dataset`
* `src/cover_kbc/evidence/production_bridge.py` — supports the new mode
* `src/cover_kbc/runtime/manifest.py` — `controller_mode`,
  `production_calibrated`, `calibration_owner`, `experiment_variant`
* `scripts/run_cover.py` — `_wants_direct`, the direct gate, the deterministic
  selector, mutual exclusion, manifest recording
* `tests/test_integration_mode.py` — the mode vocabulary, deliberately extended

**New**
* `configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml`
* `tests/test_portfolio_direct_test.py`

**Fixed**
* `configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml` — Layer 6
  disabled (see §8)

**Not touched:** the three baseline configs, baseline calibration artifacts,
`cover_kbc_v2_portfolio_test.yaml` (the calibrated path), M0–M19 semantics,
prompts, thresholds, scoring, selection, the packager contract, `benchmark/`,
the first official TEST submission.

**Did not happen:** no weights downloaded, no TRAIN/VAL/TEST inference, no
M20/M21 derived, no fake calibration artifact created, no submission, no
commit, no push.

---

## 13. Remaining blockers before the real-weight direct TEST

None in code. The remaining items are environmental:

1. Accept the Gemma gated terms on the account that will run it; expose the
   token through the environment only.
2. Confirm `transformers >= 4.57` — Qwen3.5-9B's requirement, the highest of
   the three.
3. `trust_remote_code=True` is required for Nemotron and is recorded; it
   executes `configuration_nemotron_h.py` and `modeling_nemotron_h.py` from the
   pinned revision.
4. A100 40GB feasibility remains **ESTIMATED** (≈19.06 GiB peak against ≈39.50
   usable, Audit 0062 §17), not measured.

---

## 14. Next real-world sequence

1. Commit and push this milestone.
2. Fresh Colab checkout at that exact SHA.
3. Authenticate Gemma access through the environment only.
4. Real-weight three-model smoke:
   `python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml --model-strategy portfolio --limit 3`
5. Full official TEST 477/477:
   `python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml --model-strategy portfolio`
6. `python scripts/package_submission.py --split test --predictions outputs/<run_id>/predictions.jsonl --input benchmark/data/test.jsonl --out test_submission_v2.zip`
7. Submit to Codabench; record overall and per-relation scores against baseline
   0.4499.
8. Only if promising: portfolio TRAIN 477 collection → derive portfolio M20/M21
   → `cover_kbc_v2_portfolio_test.yaml` → Submission #3.

---

**PASS — PORTFOLIO DIRECT READY FOR REAL-WEIGHT OFFICIAL TEST**

Nothing was committed or pushed.
