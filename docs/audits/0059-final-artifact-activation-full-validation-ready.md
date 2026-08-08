# Audit 0059 — Final artifact activation: FULL_VALIDATION_READY

**Date:** 2026-08-08
**Scope:** activation of the final real TRAIN-derived calibration. No source review.
**HEAD:** `78ad89d3cd8a321f500807b11477fce2f8579e32`
**Python:** 3.14.5

---

## Verdict

**PASS — FULL VALIDATION READY**

The three final artifacts arrived, matched their authoritative hashes on
arrival and again at their destination, load through every canonical owner with
the exact expected semantics, and the unmocked production readiness gate
returns `FULL_VALIDATION_READY` with `may_run_validation = True` and **zero
blockers**. The deliberate stale-artifact xfail is removed and its test now
passes on its original assertions. Full suite: **3373 passed, 4 skipped, 0
xfailed**.

Contract read first: `COVER_KBC_Technical_Proposal_New.pdf`, in full. §16 (M20
allocates by relation and reserves by action class, concrete values calibrated
on TRAIN), §17 (six-term utility, strict `U > τ_continue`, "1–2 step
micro-lookahead"), §21.2 (interface invariants) and Algorithm 1 govern what was
activated.

---

## 1. Root artifacts, verified before anything moved

The three files were placed at the repository root. Hashed **before** any
filesystem operation:

```
$ sha256sum m20_relation_budget.json m21_historical_bins.json m21_planner_calibration.json
8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68  m20_relation_budget.json
d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071  m21_historical_bins.json
36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05  m21_planner_calibration.json
```

All three equal the authoritative values exactly. Sizes 4412 / 48464 / 2123
bytes.

The 82-character planner hash recorded in Audit 0058 §3 is confirmed as the
transcription typo the brief says it is; the 64-character value above is the
only authoritative one and is what everything below uses.

Provenance read read-only from the JSON before moving — **identical across all
three**:

```
collection_repo_sha : 264c980361a513078903526440c72adc6e10edaf
derivation_repo_sha : 78ad89d3cd8a321f500807b11477fce2f8579e32
derivation_schema   : train-calibration-v1
telemetry_schema    : train-telemetry-v3
train_sha256        : cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74
telemetry_sha256    : fa95b30762a93537f7e03c87143ff6b7cfd71ff48eab80194d21089493b2b9ed
evaluator_sha256    : 2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
support_counts      : 477 queries, 86074 considered, 2047 executed,
                      64 historical bins, 1570 observed transitions
```

The embedded `evaluator_sha256` independently matches this repository's
`benchmark/evaluate.py`, so the derivation and this checkout agree on the
official evaluator.

`derivation_repo_sha` is `78ad89d3…` — the commit the bytes were derived from,
and HEAD. It was not rewritten to any future activation commit, and the
artifacts were not regenerated to make it so.

---

## 2. Movement into the production location

`mv` — one operation, no re-encoding, no reformatting, and it leaves no
redundant root copy (the repository has no convention keeping one).

Re-hashed at the destination:

```
$ sha256sum configs/calibration/*.json
8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68  configs/calibration/m20_relation_budget.json
d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071  configs/calibration/m21_historical_bins.json
36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05  configs/calibration/m21_planner_calibration.json
```

Byte-identical. The repository root holds no artifact copies.

### 2.1 What actually changed against the stale bytes

`git diff -- configs/calibration/` is **six lines**, and they are worth
recording because they are the strongest available evidence that nothing was
tuned:

| File | Change |
|---|---|
| `m20_relation_budget.json` | `derivation_repo_sha` only |
| `m21_historical_bins.json` | `derivation_repo_sha` only |
| `m21_planner_calibration.json` | `derivation_repo_sha`, and `lookahead_depth: 2 → 1` |

Every M20 envelope, every one of the 64 bins and all six coefficients are
byte-identical to the previous derivation. That is exactly what the Audit 0052
fix predicted: the corrected rule changed only *how the depth is chosen*, and
the depth was the only measurement it could affect.

---

## 3. VAL config activation

Four identity lines in `configs/experiments/cover_kbc_v2_validation.yaml`, and
nothing else — the whole diff is `4 insertions(+), 4 deletions(-)`:

```diff
-  derivation_repo_sha: b1804646dec3d2343dcf2cf8b277529071b89485
+  derivation_repo_sha: 78ad89d3cd8a321f500807b11477fce2f8579e32
-  calibration_sha256: 8ef1f07e…fd7070
+  calibration_sha256: 8110fccb…c15c68
-  historical_bins_sha256: 8c6f9c06…2d7aa5
+  historical_bins_sha256: d6d19493…bcd071
-  planner_calibration_sha256: a8ceac71…8ebfcade
+  planner_calibration_sha256: 36315cd7…44f9ce05
```

Confirmed preserved, read back from the file:

```
pipeline.mode              : interleaved
experiment.split           : val
collection_repo_sha        : 264c980361a513078903526440c72adc6e10edaf
enumerator                 : mistralai/Mistral-Small-3.2-24B-Instruct-2506 @ 95a6d26c4bfb886c58daf9d3f7332c857cb27b43
verifier                   : Qwen/Qwen3.5-4B @ 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
budget_assertion           : 28,671,226,368 / 32,000,000,000  legal=True
max_calls_per_query        : 12
max_control_rounds         : 3
M20 / M21 / Layer 6        : production / production / enabled
```

No budget, coefficient, threshold, prompt, view, model or controller setting was
touched.

---

## 4. Canonical loader verification

Through the real owners, one at a time, then through the production path.

**`load_calibrations`** — 6 relations, all `TRAIN_CALIBRATED`:

| Relation | hard_calls | hard_generated_tokens |
|---|---:|---:|
| awardWonBy | **44** | 4794 |
| companyTradesAtStockExchange | **30** | 456 |
| countryLandBordersCountry | **24** | 592 |
| hasArea | **22** | 197 |
| hasCapacity | **23** | 186 |
| personHasCityOfDeath | **22** | 228 |

**`load_history`** — 64 bins, `TRAIN_CALIBRATED`, fallback `__fallback__`, every
relation in `CONTRACTS` represented.

**`PlannerCalibration.from_json`** (and `load_planner_calibration`):

```
alpha=1.0  beta=10.084164  gamma=0.0  delta=0.069917  eta=0.143625
kappa=1.0  tau_continue=0.0  lookahead_depth=1  source=TRAIN_CALIBRATED
```

Every value equals the expected one exactly, including `gamma = 0.0` — C-02
carried truthfully: ΔH never moved over the M17/M18 action space, so γ is
inert and the artifact says so rather than inventing a coefficient.

**`load_production_calibration`** — the path a run actually takes, with the
expected collection and derivation SHAs supplied: **ACCEPTED**. 6 budgets, 64
bins, depth 1, shared provenance `78ad89d3…` / `264c9803…`. The hash gate,
cross-artifact provenance agreement and the depth-vs-successor pre-check all
passed rather than being bypassed.

No synthetic source anywhere (all three report `TRAIN_CALIBRATED`), and no TRAIN
factual runtime dependency: the artifacts carry statistics and provenance only,
which the existing gold-leak and telemetry-identity tests re-assert.

---

## 5. Stale-artifact xfail removed

`tests/test_real_calibration_artifacts.py::test_the_real_artifacts_reach_full_validation_ready`
lost its `@pytest.mark.xfail(strict=True, ...)`. The test body and its assertion
are unchanged; a docstring now records why the marker existed and why it went.
**It passes.**

Five sibling tests pinned the stale identity and were realigned. None was
deleted and none weakened:

| Test | Change |
|---|---|
| `test_the_artifact_hashes_are_exactly_the_published_ones` | `EXPECTED_SHA256` → the three final hashes |
| `test_the_provenance_names_one_collection_and_one_derivation` | `DERIVATION_SHA` → `78ad89d3…` |
| `test_the_planner_calibration_is_the_real_one` | **strengthened** — the six coefficients and the depth are now pinned exactly instead of only bounded `>= 0.0` |
| `test_the_loader_refuses_the_real_package_for_depth_two` | became `test_the_real_package_is_accepted_because_it_asks_for_depth_one`; **the safety property it carried was not lost** — a new `test_the_depth_two_guard_is_still_armed_over_this_history` pairs the real history with a depth-2 planner calibration in `tmp_path` and requires the loader to still refuse it, naming the missing successors. Nothing in the shipped bytes is modified to do it. |
| `test_everything_except_depth_two_already_satisfies_the_gate` | became `test_the_gate_reports_no_blockers_at_all` — asserts zero blockers, `may_run_validation is True`, **and** that the satisfied list still names the six budgets, `split: val`, production mode, 64 bins and all six relations, so an empty blocker list produced by checking nothing would not pass |

`test_the_real_history_has_bins_without_successor_statistics` needed no change —
the five terminal bins are still there, still well-supported (31–67
observations), and are precisely *why* the depth is 1. Only its framing was
updated from "the open blocker" to what it is: a fact about the collection.

One further fixture pin: `tests/test_production_activation.py::DERIVATION_SHA`
was `b1804646…`, so its synthetic artifacts disagreed with the activated
config's expectation and ten tests failed on a provenance mismatch. Updated to
`78ad89d3…`. That is the fixture naming the derivation the config expects; the
genuine mismatch-refusal cases are separate tests and still pass.

---

## 6. Real production readiness — unmocked

Run against the committed config and the real repository artifacts, with the
expected SHAs taken from the config's own `calibration_provenance`:

```
state              : FULL_VALIDATION_READY
may_run_validation : True
blockers           : 0
satisfied          : 8
  - M20 relation budget calibration: present at .../configs/calibration/m20_relation_budget.json
  - M20: 6 TRAIN_CALIBRATED relation budgets
  - M21 historical bins: present at .../configs/calibration/m21_historical_bins.json
  - M21 planner calibration: present at .../configs/calibration/m21_planner_calibration.json
  - split: val
  - M20 and M21 both declare production mode
  - calibration: 6 relation budget(s), 64 historical bin(s), tau_continue=0.0
  - calibration: all six relations budgeted
```

Not mocked, not fixtured, not weakened, and no provenance or hash validation was
bypassed. No activation fix beyond the identity realignments in §5 was needed —
nothing in the source had to change.

---

## 7. Regression validation

```
$ python -m pytest tests/test_real_calibration_artifacts.py -q -p no:randomly
19 passed in 13.28s

$ python -m pytest tests/test_production_activation.py -q -p no:randomly
59 passed, 1 skipped in 1.51s

$ python -m pytest tests/test_production_source_fixes.py -q -p no:randomly
56 passed in 0.83s

$ python -m pytest tests/test_accounting_invariant_fail_stop.py -q -p no:randomly
20 passed in 2.22s

$ python -m pytest tests/test_layer4_settlement_lifecycle.py -q -p no:randomly
32 passed in 0.90s

$ python -m pytest tests/test_relation_budget.py tests/test_m20_precharge_gate.py \
      tests/test_micro_planner.py tests/test_m21_production_bridge.py \
      tests/test_layer6_integration.py tests/test_controller_calibration_readiness.py \
      tests/test_train_calibration_derivation.py tests/test_calibration_p1_remediation.py \
      tests/test_calibration_sufficiency.py tests/test_pipeline_production_seam.py \
      tests/test_action_execution_seam.py -q -p no:randomly
441 passed in 5.01s

$ python -m pytest tests/ -q -p no:randomly
3373 passed, 4 skipped in 51.14s

$ python -m pytest tests/ -q            # randomized order
3373 passed, 4 skipped in 53.48s
```

3371 passed + 1 xfailed → **3373 passed, 0 xfailed**: the xfail became a pass,
plus the new depth-2-guard test.

```
$ python -m pyflakes <every tracked .py>
pyflakes exit=0            # no output
```

```
$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22

$ git diff -- benchmark/ | wc -l
0
```

Unchanged, and equal to the `evaluator_sha256` recorded inside the artifacts.

```
$ git status --short
 M configs/calibration/m20_relation_budget.json
 M configs/calibration/m21_historical_bins.json
 M configs/calibration/m21_planner_calibration.json
 M configs/experiments/cover_kbc_v2_validation.yaml
 M tests/test_production_activation.py
 M tests/test_real_calibration_artifacts.py
?? docs/audits/0058-final-artifact-activation-blocked.md
?? docs/audits/0059-final-artifact-activation-full-validation-ready.md

$ git diff --stat
 configs/calibration/m20_relation_budget.json     |   2 +-
 configs/calibration/m21_historical_bins.json     |   2 +-
 configs/calibration/m21_planner_calibration.json |   4 +-
 configs/experiments/cover_kbc_v2_validation.yaml |   8 +-
 tests/test_production_activation.py              |   2 +-
 tests/test_real_calibration_artifacts.py         | 123 +++++++++++++++++------
 6 files changed, 100 insertions(+), 41 deletions(-)
```

No source file under `src/` or `scripts/` was modified. The activation is
artifacts, config identity and test identity only.

**Confirmed did not happen:** no TRAIN inference, no 134 MB re-derivation, no
Mistral weights, no Qwen weights, no real-weight smoke, no VAL inference, no
TEST read, no submission, no commit, no push. The only file reads were the three
calibration artifacts, the config, and the test fixtures.

---

## 8. Explicit answers

| # | Question | Answer |
|---|---|---|
| 1 | Root M20 artifact matched authoritative hash? | **YES** — `8110fccb…c15c68` |
| 2 | Root M21 history artifact matched? | **YES** — `d6d19493…bcd071` |
| 3 | Root M21 planner artifact matched? | **YES** — `36315cd7…44f9ce05` |
| 4 | Final repository copies byte-identical? | **YES** — re-hashed at destination, all three equal |
| 5 | Correct derivation provenance? | **YES** — `78ad89d3cd8a321f500807b11477fce2f8579e32`, in all three artifacts and the config |
| 6 | Correct collection provenance? | **YES** — `264c980361a513078903526440c72adc6e10edaf` |
| 7 | M20 exact? | **YES** — 44 / 30 / 24 / 22 / 23 / 22, all `TRAIN_CALIBRATED` |
| 8 | M21 exact? | **YES** — 64 bins; α=1.0 β=10.084164 γ=0.0 δ=0.069917 η=0.143625 κ=1.0 τ=0.0 |
| 9 | `lookahead_depth = 1`? | **YES** |
| 10 | Stale xfail removed? | **YES** — marker gone, assertions unchanged |
| 11 | Real readiness test passes? | **YES** |
| 12 | `FULL_VALIDATION_READY`? | **YES** |
| 13 | `may_run_validation = True`? | **YES** |
| 14 | `blockers = 0`? | **YES** |
| 15 | `pipeline.mode` still interleaved? | **YES** |
| 16 | Full suite green? | **YES** — 3373 passed, 4 skipped, 0 xfailed |
| 17 | Benchmark untouched? | **YES** — `2d592ae1…`, empty diff |
| 18 | Any new P0/P1? | **NO** |
| 19 | Real-weight smoke executed? | **NO** |
| 20 | VAL executed? | **NO** |

---

**PASS — FULL VALIDATION READY**

Nothing was committed or pushed. Stopping here as instructed: no further source
review, no smoke, no VAL.
