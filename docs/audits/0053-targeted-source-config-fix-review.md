# Audit 0053 - Targeted Source/Config Fix Review

Date: 2026-08-08

Verdict: FAIL - DO NOT COMMIT

Scope: targeted independent review of Audit 0052's three source/config fixes
only. The first pass was read-only over the proposal, executable source,
current production calibration artifacts, readiness/production loaders, tests,
and config. No source, test, config, benchmark, or calibration artifact was
modified by this audit.

## Contract Read

`COVER_KBC_Technical_Proposal_New.pdf` was read first and treated as the
authoritative contract. Relevant constraints used in this review:

- Module 20 owns relation/action-class budget scheduling for the upgraded
  Layer-4 control path, with concrete quantities calibrated on TRAIN.
- Module 21 owns expected-value action choice using trained historical
  estimates and 1-2 step lookahead.
- The system remains closed-book and non-neural for control/calibration logic.
- M7's core controller budget and M20's upgraded Layer-4 envelope are not the
  same ownership surface.

## Findings

### P1 - Production precharge drops the reservation handle and never settles or cancels

`CoverPipeline.execute_action()` calls `_precharge()` before execution at
`src/cover_kbc/pipeline.py:1869`. `_precharge()` reserves against the real
ledger at `src/cover_kbc/pipeline.py:2133`, but returns only `(True, "")` at
`src/cover_kbc/pipeline.py:2141`. The concrete `BudgetReservation` and its
`reservation_id` are lost.

No production path then calls `BudgetLedger.settle(...)` or
`BudgetLedger.cancel(...)` after the action's measured `physical_delta`.
An adversarial source probe confirmed:

```
pipeline_has_settle_call False
pipeline_has_cancel_call False
```

The ledger core itself handles `reserve`, `settle`, and `cancel`, but the
production pipeline does not use settlement/cancellation. Executed Layer-4
reservations therefore remain outstanding forever, and any conservative
over-reservation is never released. This violates the requested P1-A invariant
that new Layer-4 calls are reserved/settled exactly once and creates production
physical-accounting mismatch.

Severity: P1. This blocks commit and blocks real re-derivation/production use.

### P2 - Execution-mode validation is not before runtime construction

`resolve_execution_mode()` correctly maps `interleaved` and `staged`, rejects
unknown values, and the VAL config declares `pipeline.mode: interleaved`.
However `run_cover.py` constructs model runtimes before resolving the mode:

- `runtime = build_runtime(enumerator_cfg)` at `scripts/run_cover.py:142`
- `verifier_runtime = ... build_runtime(verifier_cfg)` at
  `scripts/run_cover.py:143-144`
- `execution_mode = resolve_execution_mode(config)` at
  `scripts/run_cover.py:159`

The code comment at `scripts/run_cover.py:157-158` claims the opposite. A
dynamic monkeypatch probe using `pipeline.mode: bogus` confirmed that
`build_runtime()` was called once before the expected refusal:

```
system_exit pipeline.mode 'bogus' is not a supported execution mode; this build implements interleaved, staged
build_runtime_calls_before_refusal 1 [{}]
```

This does not change the valid VAL config's resolved mode, but it fails the
explicit requested property that unknown mode fail closed before model
construction.

Severity: P2 by itself; it is also an incomplete execution-mode source fix.

## Verified Correct

### P2-A package-wide lookahead depth

`supports_depth_two(package)` in
`src/cover_kbc/controller_calibration/derivation.py:759-813` replaced the old
`any bin has successors` rule. It requires:

- every shipped root bin to carry successor statistics;
- every named successor state to resolve at the next lookup level, either
  exactly or through the declared fallback bin for that relation/program/action
  family;
- no fabricated successors or runtime catch-and-retry.

`derive_planner_calibration()` uses that package-wide result at
`src/cover_kbc/controller_calibration/derivation.py:904` and serializes exactly
`lookahead_depth=2 if depth_two else 1` at
`src/cover_kbc/controller_calibration/derivation.py:928`.

Read-only application to the present real 64-bin package:

```
bins 64
depth_two_supported False
lookahead_depth 1
blocker_count 5
countryLandBordersCountry/__fallback__/REVERSE_CHECK (support 67) records no successor statistics
countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK (support 36) records no successor statistics
countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK (support 31) records no successor statistics
hasArea/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 43) records no successor statistics
hasCapacity/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 57) records no successor statistics
```

Conclusion: P2-A source fix is correct. The current real package would now
derive `lookahead_depth = 1`; the known five terminal bins are sufficient to
make depth 2 unsupported.

### M7/M20 envelope ownership arithmetic

`build_plan()` now records the core ceiling as a note and returns the calibrated
M20 envelope unchanged. The former `_intersect_ceiling` behavior is gone from
the pre-envelope path. Source references:

- `_note_envelope(...)` returns `calibrated` at
  `src/cover_kbc/control/relation_budget.py:233-239`
- `build_plan()` uses calibrated `hard_calls` at
  `src/cover_kbc/control/relation_budget.py:292-297`
- envelope caps are bounded by calibrated hard calls at
  `src/cover_kbc/control/relation_budget.py:299-315`

Read-only probe against the real M20 artifact confirmed the exact real
envelopes are preserved:

```
real_hard_calls {'awardWonBy': 44, 'companyTradesAtStockExchange': 30, 'countryLandBordersCountry': 24, 'hasArea': 22, 'hasCapacity': 23, 'personHasCityOfDeath': 22}
core_max_calls {'awardWonBy': 12, 'companyTradesAtStockExchange': 5, 'countryLandBordersCountry': 4, 'hasArea': 4, 'hasCapacity': 4, 'personHasCityOfDeath': 4}
plan awardWonBy hard 44 verification_floor 14 discovery_cap 1 verification_cap 18
plan companyTradesAtStockExchange hard 30 verification_floor 0 discovery_cap 1 verification_cap 14
plan countryLandBordersCountry hard 24 verification_floor 0 discovery_cap 1 verification_cap 14
plan hasArea hard 22 verification_floor 0 discovery_cap 1 verification_cap 10
plan hasCapacity hard 23 verification_floor 0 discovery_cap 1 verification_cap 10
plan personHasCityOfDeath hard 22 verification_floor 0 discovery_cap 1 verification_cap 8
```

The remaining P1 is not the envelope arithmetic; it is production settlement
integration.

### Ledger core accounting probes

The direct ledger probes passed the requested adversarial cases:

```
probe_prior 0 available_before 22 denied False committed_after 1
probe_prior 21 available_before 1 denied False committed_after 22
probe_prior 22 available_before 0 denied True committed_after 22
probe_prior 23 available_before 0 denied True committed_after 23
reserve_then_settle_before 5 ReservationStatus.OUTSTANDING
reserve_then_settle_after 4 0 1
reserve_then_cancel_before 9 ReservationStatus.OUTSTANDING
reserve_then_cancel_after 7 7 0
cache_hit_only denied False reserved 0 committed 5
repeat_affordability 18 18
sequential 0 denied False committed 5
sequential 1 denied False committed 6
sequential 2 denied False committed 7
award_protected_start hard 44 prior 30 discovery_available 0 verification_available 14
award_discovery_denied True
award_verification_denied False
m17_descriptor sub_calls 8 non_cacheable 4
pipeline_has_settle_call False
pipeline_has_cancel_call False
```

This shows the ledger API can account exactly once when called correctly, cache
hits cost zero, prior spend is not refunded, the cached ledger does not reset,
sequential reservations consume capacity, and award verification reserve is
protected. It also shows production does not call the settlement/cancellation
API.

### Execution mode properties that do hold

```
val_mode interleaved
collection_mode interleaved
resolved_val interleaved
resolved_interleaved interleaved
resolved_staged staged
bogus_refused pipeline.mode 'bogus' is not a supported execution mode; this build implements interleaved, staged
build_runtime_index 1352
resolve_index 2037
build_before_resolve True
```

Therefore:

- real TRAIN calibration collection config declares interleaved;
- VAL config declares interleaved;
- `run_cover.py` no longer silently overwrites the configured mode;
- `INTERLEAVED` and `STAGED` map to the corresponding enum values;
- unknown values fail, but too late relative to runtime construction.

### Provenance / stale artifact consequence

The production loader still loads all three artifacts through canonical owners
and enforces hash/provenance consistency in
`src/cover_kbc/controller_calibration/production.py:132-185`. Readiness remains
fail-closed on the current stale artifacts:

```
state NOT_READY
may_run_validation False
BLOCKER calibration: the planner calibration requests depth-2 lookahead but 5 historical bin(s) record no successor statistics, e.g. ['countryLandBordersCountry/__fallback__/REVERSE_CHECK', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK']. Module 21 raises when it ranks an action from such a bin, so this package would fail mid-run. Either derive it with lookahead_depth 1, or derive a history in which every shipped bin observed a transition
```

The current artifacts are unchanged in this source milestone:

```
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  configs/calibration/m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  configs/calibration/m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  configs/calibration/m21_planner_calibration.json
```

`git diff -- configs/calibration/` produced no output.

## Regression Checks

Within the requested targeted scope:

- no neural model/training code was added to M20/M21 derivation/control;
- no TRAIN factual runtime dependency was added to production artifacts;
- no VAL/TEST gold read was found in the changed control path;
- strict `U > tau_continue` remains in `src/cover_kbc/control/micro_planner.py`;
- current stale artifacts still block validation readiness;
- benchmark evaluator is untouched.

## Validation Commands and Results

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_production_source_fixes.py
................................................                         [100%]
48 passed in 0.45s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_production_activation.py tests/test_real_calibration_artifacts.py tests/test_controller_calibration_readiness.py
...............................s........................................ [ 66%]
....x...............................                                     [100%]
106 passed, 1 skipped, 1 xfailed in 15.29s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_relation_budget.py tests/test_m20_precharge_gate.py tests/test_micro_planner.py tests/test_m21_production_bridge.py tests/test_layer6_integration.py
........................................................................ [ 29%]
........................................................................ [ 58%]
........................................................................ [ 87%]
................................                                         [100%]
248 passed in 3.03s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_train_calibration_derivation.py tests/test_derive_train_calibration_cli.py tests/test_calibration_p1_remediation.py
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 3.36s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
3311 passed, 4 skipped, 1 xfailed in 55.09s
```

```
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pyflakes scripts/run_cover.py src/cover_kbc/control/budget_accounting.py src/cover_kbc/control/micro_planner.py src/cover_kbc/control/relation_budget.py src/cover_kbc/controller_calibration/derivation.py src/cover_kbc/controller_calibration/readiness.py src/cover_kbc/controller_calibration/production.py src/cover_kbc/pipeline.py tests/test_production_source_fixes.py tests/test_production_activation.py tests/test_real_calibration_artifacts.py
```

No output; exit code 0.

```
sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py
```

```
git diff -- benchmark/
```

No output.

```
git diff --stat
 scripts/run_cover.py                               | 131 +++++++++++++++++++--
 src/cover_kbc/control/budget_accounting.py         |  39 +++++-
 src/cover_kbc/control/micro_planner.py             |  31 ++++-
 src/cover_kbc/control/relation_budget.py           |  82 +++++++++----
 src/cover_kbc/controller_calibration/derivation.py |  68 ++++++++++-
 src/cover_kbc/controller_calibration/readiness.py  | 123 +++++++++++++++++++
 src/cover_kbc/pipeline.py                          |  14 ++-
 tests/test_layer6_integration.py                   |  29 ++++-
 tests/test_micro_planner.py                        |   9 ++
 tests/test_relation_budget.py                      |  66 +++++++++--
 10 files changed, 543 insertions(+), 49 deletions(-)
```

`git diff` was run; it is non-empty and limited to the tracked source/test
changes above. The tool display truncated the full patch, but
`git diff -- benchmark/` remained empty.

Pre-audit `git status --short`:

```
 M scripts/run_cover.py
 M src/cover_kbc/control/budget_accounting.py
 M src/cover_kbc/control/micro_planner.py
 M src/cover_kbc/control/relation_budget.py
 M src/cover_kbc/controller_calibration/derivation.py
 M src/cover_kbc/controller_calibration/readiness.py
 M src/cover_kbc/pipeline.py
 M tests/test_layer6_integration.py
 M tests/test_micro_planner.py
 M tests/test_relation_budget.py
?? configs/calibration/
?? configs/experiments/cover_kbc_v2_validation.yaml
?? docs/audits/0050-production-activation-f11-f22-f24.md
?? docs/audits/0051-real-artifact-verification-and-production-hold.md
?? docs/audits/0052-source-fixes-depth-two-budget-ownership-execution-mode.md
?? src/cover_kbc/controller_calibration/production.py
?? tests/test_production_activation.py
?? tests/test_production_source_fixes.py
?? tests/test_real_calibration_artifacts.py
```

## Explicit Answers

A. P2-A source fix correct? YES

B. Real 64-bin package would now derive depth 1? YES

C. P1-A ownership fix correct? NO. Envelope ownership is corrected, but
production settlement/cancellation integration is missing.

D. Physical accounting exactly-once? NO

E. Real M20 envelopes preserved? YES

F. Execution mode fix correct? NO. Valid mode resolution is correct, but
unknown mode does not fail before runtime construction.

G. Any new P0/P1? YES. The production precharge/settlement gap is P1.

H. Safe to commit source/config changes? NO

I. After commit, safe to run the one real 134 MB re-derivation? NO

J. FULL_VALIDATION_READY now? NO - stale artifacts still correctly block it.
