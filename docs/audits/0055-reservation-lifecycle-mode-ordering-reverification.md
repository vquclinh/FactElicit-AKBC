# Audit 0055 - Reservation Lifecycle / Mode Ordering Re-Verification

Date: 2026-08-08

Verdict: FAIL - DO NOT COMMIT

Scope: targeted independent re-verification of the two findings fixed after
Audit 0053:

1. Layer-4 reservation lifecycle / physical accounting.
2. Execution-mode refusal ordering.

This is not a full architecture review. The first pass was read-only over
`COVER_KBC_Technical_Proposal_New.pdf`, executable source, production call
paths, relevant tests/configs, and calibration artifact hashes. Audit 0054 was
not used as an authority.

## Proposal Contract

The proposal was read first. Relevant load-bearing points:

- The upgraded system remains closed-book, frozen-model, and non-training.
- M20 is Layer-6 relation/action-class budget scheduling; concrete quantities
  are calibrated on TRAIN.
- M21 is deterministic expected-value planning over full control state and
  legal actions.
- The control loop executes the chosen action and records its effect and budget
  as part of the same controller state.

## P1 Finding

### P1 - Settlement overrun is not process-fatal; production query loop can continue with an OUTSTANDING reservation

`BudgetLedger.settle()` refuses `actual_calls > reserved_calls` before mutating
the reservation at `src/cover_kbc/control/budget_accounting.py:367-383`.
`CoverPipeline._release_hold()` converts that refusal to
`AccountingInvariantError` at `src/cover_kbc/pipeline.py:2252-2259`. The
reservation therefore remains `OUTSTANDING`, which is reasonable only if the
process is fail-stop and the ledger cannot be reused.

It is not fail-stop in the production query loop. `CoverPipeline.run()` catches
ordinary `Exception` at `src/cover_kbc/pipeline.py:3064-3085`, converts the row
to a `PIPELINE_ERROR` prediction, and continues. `run_cover.py` then writes
predictions, module artifacts, `errors.json`, and `manifest.json`, and returns
0 at `scripts/run_cover.py:348-409`.

Independent forced-underreservation probe:

```
overrun_predictions 2
overrun_errors [{'SubjectEntity': 'Testland', 'Relation': 'countryLandBordersCountry', 'error': "AccountingInvariantError: Module 20 could not settle 'M17:SPECIALIST_VERIFY:alphaland': reservation '423d74063a776b83' held 4 calls but 8 were spent; a neural call was made outside the precharge"}]
overrun_ledger_keys [('Secondland', 'countryLandBordersCountry', 1), ('Testland', 'countryLandBordersCountry', 0)]
overrun_ledger ('Secondland', 'countryLandBordersCountry', 1) statuses ['SETTLED', 'SETTLED', 'SETTLED', 'SETTLED', 'SETTLED', 'SETTLED'] outstanding [] settlements [('122b684d0507782f', 4), ('a29dce469003e6be', 4), ('ffdcbde1ba748251', 4), ('9e97e45dc2f03ed0', 1), ('a4965cf7632b7c6e', 1), ('37d5af8500e34a5a', 1)] committed 22 hard 64
overrun_ledger ('Testland', 'countryLandBordersCountry', 0) statuses ['OUTSTANDING'] outstanding ['423d74063a776b83'] settlements [] committed 11 hard 64
```

This exactly matches the requested P1 condition: a process can continue after an
overrun with an `OUTSTANDING` reservation.

Additional exception-chain probe:

```
propagated_type AccountingInvariantError
propagated_message Module 20 could not settle 'M17:SPECIALIST_VERIFY:alphaland:underheld': reservation '998155bb53605893' held 0 calls but 4 were spent; a neural call was made outside the precharge
context_type BudgetSchedulerError
context_message reservation '998155bb53605893' held 0 calls but 4 were spent; a neural call was made outside the precharge
cause_type BudgetSchedulerError
last_status OUTSTANDING
last_reserved 0
last_settlement_count_for_hold 0
```

Recursive chain:

```
chain exc 0 AccountingInvariantError Module 20 could not settle 'M17:SPECIALIST_VERIFY:alphaland:underheld': reservation '998155bb53605893' held 0 calls but 4 were spent; a neural call was made outside the precharge
chain exc.__cause__ 1 BudgetSchedulerError reservation '998155bb53605893' held 0 calls but 4 were spent; a neural call was made outside the precharge
chain exc.__cause__.__context__ 2 Boom original action failure after spend
```

The raw exception chain retains both failures, but `PipelineResult.errors`
records only the top-level `AccountingInvariantError` string. More importantly,
continued execution is reachable.

Severity: P1. This blocks commit and blocks production/re-derivation release of
this source milestone.

## Verified Correct In Non-Overrun Paths

### Concrete reservation handle lifecycle

The concrete `BudgetReservation` returned by `BudgetLedger.reserve()` is now
retained as `(ledger, reservation)` by `_precharge()` and passed to
`_release_hold()`:

- `_precharge()` reserves and returns the concrete hold at
  `src/cover_kbc/pipeline.py:2169-2212`.
- `execute_action()` receives `admitted, refusal, hold` at
  `src/cover_kbc/pipeline.py:1869`.
- success calls `_release_hold(hold, before, after, executed=True)` at
  `src/cover_kbc/pipeline.py:1931`.
- failure calls `_release_hold(..., executed=False)` before re-raising at
  `src/cover_kbc/pipeline.py:1922-1930`.
- no reservation id is guessed or reconstructed; `_release_hold()` uses
  `reservation.reservation_id`.

Normal production-path probe:

```
normal_reservation_count 6
normal_statuses ['SETTLED', 'SETTLED', 'SETTLED', 'SETTLED', 'SETTLED', 'SETTLED']
normal_settlement_count 6
normal_outstanding []
normal_committed_calls 26
normal_runtime_calls 26
normal_committed_tokens 20
normal_runtime_tokens 20
normal_m17_reserved_calls [8, 4, 4]
normal_m17_actual_calls [8, 4, 4]
```

Therefore the success path settles exactly once, releases conservative token
over-reservation, and matches runtime counters when no accounting overrun
occurs.

### Failure paths

Source and tests verify:

- failure before spend cancels;
- failure after spend settles actual measured spend when `actual <= reserved`;
- denial returns no hold and does not execute;
- zero-cost/cache-hit execution settles to zero rather than cancelling;
- `BudgetLedger` refuses double close of the same reservation.

The blocker is not these ordinary paths; it is that the overrun invariant
failure is catchable by the production query loop.

### Action-owned neural work is inside the measured interval

`execute_action()` takes `before = physical_snapshot()` at
`src/cover_kbc/pipeline.py:1863`, precharges at `src/cover_kbc/pipeline.py:1869`,
then executes M17/M18 inside the guarded block at
`src/cover_kbc/pipeline.py:1899-1909`. It integrates Layer 4, applies the
production bridge, refreshes M19, then takes `after = physical_snapshot()` at
`src/cover_kbc/pipeline.py:1921`.

The inspected integration/bridge/M19 paths are deterministic state transforms:

- `_integrate_layer4()` uses `layer4_integrator.integrate(...)` and no runtime;
- `ProductionEvidenceBridge.apply()` mutates graph evidence and no runtime;
- `_estimate_coverage_gap()` calls the coverage estimator and no runtime.

M17 and M18 are therefore the action-owned neural work inside the measured
interval.

## M17 Cold/Warm Upper Bound

Verified from executable code:

- `m17_call_plan()` derives readings from live `template_ids x label_orders`
  and controls from `use_calibration`, not constants.
- `SpecialistVerifier.control_calls_needed()` builds the same
  `(template_id, label_order)` templates that `verify()` later renders.
- `ContextualCalibrator.control_calls_needed()` only reads cache keys and a
  local `seen` set; it performs no `score_labels()` call and does not mutate
  the cache.
- `control_logits()` is the mutating/neural path.
- unknown/uninspectable runtime state returns the cold control count in
  `CoverPipeline._m17_control_calls_needed()`.

Normal production probe confirmed cold then warm:

```
normal_m17_reserved_calls [8, 4, 4]
normal_m17_actual_calls [8, 4, 4]
```

No prompt, view, label-order, or verification-quality behavior change was found
in this fix.

## Prior Spend / Exactly Once

In normal execution, the invariant holds:

```
ledger total = prior physical spend before Layer 4 + actual settled Layer-4 spend
```

The same normal probe independently reproduced both equality checks:

```
normal_committed_calls 26
normal_runtime_calls 26
normal_committed_tokens 20
normal_runtime_tokens 20
```

The overrun continuation path violates this by leaving the failed row's actual
spend represented as an outstanding reservation rather than a settlement.

## Execution Mode Ordering

`run_cover.main()` now resolves execution mode before runtime construction:

- `resolve_execution_mode(config)` at `scripts/run_cover.py:135`
- `build_runtime(enumerator_cfg)` at `scripts/run_cover.py:147`
- verifier `build_runtime(...)` at `scripts/run_cover.py:148-149`

Dynamic probe:

```
mode_probe bogus calls 0 outcome SystemExit: pipeline.mode 'bogus' is not a supported execution mode; this build implements interleaved, staged
mode_probe Interleaved calls 0 outcome SystemExit: pipeline.mode 'Interleaved' is not a supported execution mode; this build implements interleaved, staged
mode_probe batch calls 0 outcome SystemExit: pipeline.mode 'batch' is not a supported execution mode; this build implements interleaved, staged
mode_probe parallel calls 0 outcome SystemExit: pipeline.mode 'parallel' is not a supported execution mode; this build implements interleaved, staged
mode_probe interleaved calls 2 outcome SystemExit: /tmp/tmp_86ik01d/interleaved.yaml declares production mode but is not FULL_VALIDATION_READY (NOT_READY)
mode_probe staged calls 2 outcome SystemExit: /tmp/tmpxnimg_d5/staged.yaml declares production mode but is not FULL_VALIDATION_READY (NOT_READY)
```

Conclusion: execution-mode refusal ordering is fixed. Invalid mode values build
zero enumerator/verifier runtimes. Valid modes reach runtime construction.
VAL and collection configs both resolve to `INTERLEAVED`. No hardcoded
`config_block["mode"] = ExecutionMode.INTERLEAVED.value` override remains.

## M20 / Regression Sanity

Real M20 hard calls remain:

```
m20_hard_calls {'awardWonBy': 44, 'companyTradesAtStockExchange': 30, 'countryLandBordersCountry': 24, 'hasArea': 22, 'hasCapacity': 23, 'personHasCityOfDeath': 22}
m7_core_caps {'awardWonBy': 12, 'companyTradesAtStockExchange': 5, 'countryLandBordersCountry': 4, 'hasArea': 4, 'hasCapacity': 4, 'personHasCityOfDeath': 4}
award_discovery_denied_by_reserve True DENIED_BY_PROTECTED_RESERVE
award_verification_admitted True
verification_cap_denied True DENIED_BY_CLASS_CAP
```

P2-A source rule remains correct:

```
p2a_bins 64
p2a_depth_two_supported False
p2a_derived_depth 1
p2a_blockers 5
p2a_blocker countryLandBordersCountry/__fallback__/REVERSE_CHECK (support 67) records no successor statistics
p2a_blocker countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK (support 36) records no successor statistics
p2a_blocker countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK (support 31) records no successor statistics
p2a_blocker hasArea/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 43) records no successor statistics
p2a_blocker hasCapacity/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 57) records no successor statistics
```

Readiness remains expectedly blocked by stale artifacts:

```
readiness_state NOT_READY
readiness_may_run False
readiness_blocker calibration: the planner calibration requests depth-2 lookahead but 5 historical bin(s) record no successor statistics, e.g. ['countryLandBordersCountry/__fallback__/REVERSE_CHECK', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK']. Module 21 raises when it ranks an action from such a bin, so this package would fail mid-run. Either derive it with lookahead_depth 1, or derive a history in which every shipped bin observed a transition
```

F-11/F-22/F-24 are covered by `tests/test_production_activation.py`, which
passes. Strict `U > tau_continue` remains in `micro_planner.py`. No real
artifacts changed. No neural training or TRAIN factual runtime dependency was
introduced in the reviewed paths. Benchmark evaluator is untouched.

## Validation Commands and Results

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_layer4_settlement_lifecycle.py
................................                                         [100%]
32 passed in 0.73s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_production_source_fixes.py
........................................................                 [100%]
56 passed in 1.27s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_production_activation.py
...............................s............................             [100%]
59 passed, 1 skipped in 1.58s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_m20_precharge_gate.py tests/test_relation_budget.py tests/test_layer6_integration.py tests/test_action_execution_seam.py
........................................................................ [ 36%]
........................................................................ [ 72%]
........................................................                 [100%]
200 passed in 1.70s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
3351 passed, 4 skipped, 1 xfailed in 45.77s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pyflakes scripts/run_cover.py src/cover_kbc/pipeline.py src/cover_kbc/control/budget_accounting.py src/cover_kbc/control/budget_types.py src/cover_kbc/control/action_catalog.py src/cover_kbc/control/layer6_integration.py src/cover_kbc/verification/specialist_verifier.py src/cover_kbc/verification/blind.py tests/test_layer4_settlement_lifecycle.py tests/test_production_source_fixes.py tests/test_production_activation.py tests/test_m20_precharge_gate.py tests/test_relation_budget.py tests/test_layer6_integration.py
```

No output; exit code 0.

```
sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py
```

```
sha256sum configs/calibration/*.json
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  configs/calibration/m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  configs/calibration/m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  configs/calibration/m21_planner_calibration.json
```

```
git diff -- benchmark/
```

No output.

```
git diff -- configs/calibration/
```

No output.

```
git diff --stat
 scripts/run_cover.py                               | 132 +++++++++++++-
 src/cover_kbc/control/budget_accounting.py         |  39 ++++-
 src/cover_kbc/control/micro_planner.py             |  31 +++-
 src/cover_kbc/control/relation_budget.py           |  82 ++++++---
 src/cover_kbc/controller_calibration/derivation.py |  68 +++++++-
 src/cover_kbc/controller_calibration/readiness.py  | 123 +++++++++++++
 src/cover_kbc/pipeline.py                          | 190 +++++++++++++++++----
 src/cover_kbc/verification/specialist_verifier.py  |  35 ++++
 tests/test_action_execution_seam.py                |   2 +-
 tests/test_layer6_integration.py                   |  29 +++-
 tests/test_m20_precharge_gate.py                   |   4 +-
 tests/test_micro_planner.py                        |   9 +
 tests/test_relation_budget.py                      |  66 ++++++-
 13 files changed, 729 insertions(+), 81 deletions(-)
```

`git diff` was run. It is non-empty for the pending tracked milestone changes;
the tool output was truncated, so the exact stat and changed-file list above are
the compact reproducible record. `git diff -- benchmark/` and
`git diff -- configs/calibration/` were empty.

Pre-audit `git status --short`:

```
 M scripts/run_cover.py
 M src/cover_kbc/control/budget_accounting.py
 M src/cover_kbc/control/micro_planner.py
 M src/cover_kbc/control/relation_budget.py
 M src/cover_kbc/controller_calibration/derivation.py
 M src/cover_kbc/controller_calibration/readiness.py
 M src/cover_kbc/pipeline.py
 M src/cover_kbc/verification/specialist_verifier.py
 M tests/test_action_execution_seam.py
 M tests/test_layer6_integration.py
 M tests/test_m20_precharge_gate.py
 M tests/test_micro_planner.py
 M tests/test_relation_budget.py
?? configs/calibration/
?? configs/experiments/cover_kbc_v2_validation.yaml
?? docs/audits/0050-production-activation-f11-f22-f24.md
?? docs/audits/0051-real-artifact-verification-and-production-hold.md
?? docs/audits/0052-source-fixes-depth-two-budget-ownership-execution-mode.md
?? docs/audits/0053-targeted-source-config-fix-review.md
?? docs/audits/0054-layer4-settlement-lifecycle-and-execution-mode-ordering.md
?? src/cover_kbc/controller_calibration/production.py
?? tests/test_layer4_settlement_lifecycle.py
?? tests/test_production_activation.py
?? tests/test_production_source_fixes.py
?? tests/test_real_calibration_artifacts.py
```

## Explicit Answers

A. Concrete reservation handle retained end-to-end? YES

B. Success settles exactly once? YES

C. Failure-before-spend cancels? YES

D. Failure-after-spend settles actual spend? YES when actual spend is within the
reserved bound; the overrun path remains the blocker.

E. No normal execution can spend more than reserved? YES

F. M17 cold/warm upper-bound logic correct? YES

G. No dangling reservation can affect continued production? NO

H. Prior + settled Layer-4 accounting exactly-once? NO in the reachable overrun
continuation path; YES in normal non-overrun execution.

I. Runtime counters and ledger totals agree? NO in the reachable overrun
continuation path; YES in normal non-overrun execution.

J. Execution mode refused before any runtime build? YES

K. Invalid mode => enumerator/verifier build_runtime calls both zero? YES

L. Any new P0/P1? YES

M. Safe to commit ALL pending production/source/config changes? NO

N. After commit, safe to run the one real 134 MB re-derivation? NO
