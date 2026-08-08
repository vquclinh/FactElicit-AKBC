# Audit 0057 - Final targeted source review: accounting invariant fail-stop

Required verdict:

PASS — ACCOUNTING FAIL-STOP INDEPENDENTLY VERIFIED; SAFE TO COMMIT AND RE-DERIVE AFTER COMMIT

Scope: final targeted independent source review of the remediation for
`AccountingInvariantError` after Audit 0055/Audit 0056. I did not re-open TRAIN
collection, M20/M21 derivation, gold attribution, P2-A depth derivation,
M7/M20 ownership, ordinary reservation lifecycle, execution-mode
implementation, or F-11/F-22/F-24 except for direct regression sanity.

The proposal was read first and treated as the controlling architecture
contract. The relevant contract points are the Layer-6 precharge rule, hard
physical envelopes, frozen closed-book runtimes, no online learning, and
deterministic offline calibration artifacts.

## Source Trace

No P0/P1 findings.

- `BudgetLedger.settle()` refuses `actual_calls > reserved_calls` and
  `actual_generated_tokens > reserved_generated_tokens` before changing
  reservation state (`src/cover_kbc/control/budget_accounting.py:367-384`).
  The hold is not widened, cancelled, falsely settled, or refunded.
- `CoverPipeline._release_hold()` computes actual spend from runtime counter
  deltas, cancels only a zero-spend failed action, otherwise calls
  `ledger.settle()`, and raises `AccountingInvariantError` from
  `BudgetSchedulerError` on an overrun (`src/cover_kbc/pipeline.py:2214-2259`).
- `CoverPipeline.execute_action()` takes its pre-execution snapshot before
  precharge, performs action-owned work inside the measured interval, settles
  on success, and on failure calls `_release_hold(..., executed=False)` before
  re-raising (`src/cover_kbc/pipeline.py:1838-1931`). If `_release_hold()` raises
  the accounting invariant, Python exception chaining keeps the original action
  exception as context under the ledger refusal.
- `CoverPipeline.run()` has `except AccountingInvariantError: raise` before the
  generic row-local `except Exception` block (`src/cover_kbc/pipeline.py:3059-3095`).
  Therefore the invariant cannot become `PIPELINE_ERROR`.
- `scripts/run_cover.py` catches `AccountingInvariantError` only around
  `pipeline.run()` and immediately calls `_abort_on_accounting_invariant()`
  (`scripts/run_cover.py:433-438`).
- `_abort_on_accounting_invariant()` writes only
  `FAILED_ACCOUNTING_INVARIANT.json` and raises `SystemExit(2) from error`
  (`scripts/run_cover.py:144-193`).
- Successful completion code is below that abort point: `manifest.finish()`,
  `write_predictions(... predictions.jsonl ...)`, module JSONL writes,
  optional metrics, and final `manifest.write(... manifest.json ...)` are
  unreachable after the fatal abort (`scripts/run_cover.py:441-497`).
- `RunTracer.__exit__()` only closes the file and returns `None`, so it cannot
  suppress `SystemExit` or convert the abort to success
  (`src/cover_kbc/runtime/tracing.py:44-51`).
- `run_cover.py` has no `--resume`, `--checkpoint`, or `--continue` argument.
  The accounting marker is not a checkpoint and carries no ledger/reservation
  state.
- Submission packaging requires an explicit `--predictions` file and writes an
  archive member named exactly `predictions.jsonl`; it does not scan for or
  consume `FAILED_ACCOUNTING_INVARIANT.json`
  (`scripts/package_submission.py:144-175`).

## Independent Adversarial Probes

### Real pipeline, two rows, forced under-reservation

Command: inline Python probe using `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests`
with a `CoverPipeline` subclass that restores the old bad projection by
returning zero M17 control calls.

Result:

```text
raised AccountingInvariantError
message Module 20 could not settle 'M17:SPECIALIST_VERIFY:alphaland': reservation '423d74063a776b83' held 4 calls but 8 were spent; a neural call was made outside the precharge
cause BudgetSchedulerError
entered [('Testland', 0)]
ledger_keys [('Testland', 'countryLandBordersCountry', 0)]
ledger ('Testland', 'countryLandBordersCountry', 0) committed 11 hard 64 statuses [('423d74063a776b83', 'OUTSTANDING', 4)]
```

Row 2 was never entered and no row-2 ledger was created. The failed row's
reservation remains outstanding, which is safe here because the process path is
fail-stop and no later production action can observe the poisoned ledger.

### Real CLI wrapper, two rows, forced under-reservation

Command: inline Python probe importing `scripts/run_cover.py`, using synthetic
two-row JSONL input, scripted runtimes, real VAL production wiring with fixture
calibration artifacts, and the same under-reserving pipeline subclass.

Result:

```text
system_exit 2
entered [('Testland', 0)]
out_names ['FAILED_ACCOUNTING_INVARIANT.json', 'calls.jsonl']
FAILED_ACCOUNTING_INVARIANT.json exists True
predictions.jsonl exists False
manifest.json exists False
metrics.json exists False
trace.jsonl exists False
calls.jsonl exists True
marker_subset {'status': 'aborted', 'complete': False, 'submittable': False, 'predictions_written': False, 'manifest_written': False, 'expected_queries': 2}
marker_chain ['AccountingInvariantError', 'BudgetSchedulerError']
```

The CLI exited non-zero and wrote no successful run artifacts.

### Second invocation into the same failed output directory

Result:

```text
codes [(1, 'exit', 2), (2, 'exit', 2)]
entered [('Testland', 0), ('Testland', 0)]
out_names ['FAILED_ACCOUNTING_INVARIANT.json', 'calls.jsonl']
marker_exists True
predictions_exists False
manifest_exists False
```

The second invocation restarted independently at row 1. It did not resume from
the marker or `calls.jsonl`, and no in-memory ledger survived the process-level
boundary.

### Action failure after spend plus settlement overrun

Result:

```text
raised AccountingInvariantError
chain_types ['AccountingInvariantError', 'BudgetSchedulerError', 'Boom']
AccountingInvariantError Module 20 could not settle 'M17:SPECIALIST_VERIFY:alphaland': reservation '6e0de55b7a6ccfed' held 1 calls but 4 were spent; a neural call was made outside the precharge
BudgetSchedulerError reservation '6e0de55b7a6ccfed' held 1 calls but 4 were spent; a neural call was made outside the precharge
Boom ordinary action failure after measured spend
```

The accounting invariant remains the fatal top-level condition, and the
ordinary action failure remains diagnosable through the exception chain.

### Ordinary non-accounting row error control

Result:

```text
returned PipelineResult
entered [('Testland', 0), ('Secondland', 1)]
predictions 2
errors [{'SubjectEntity': 'Testland', 'Relation': 'countryLandBordersCountry', 'error': 'Boom: ordinary non-accounting row error'}]
empty_reasons ['pipeline_error', 'candidate_rejected']
row0_pipeline_error True
row1_pipeline_error False
```

The fix did not convert ordinary row-local failures into fatal run failures.

## Regression Sanity

Independent sanity probe result:

```text
real_hard_calls {'awardWonBy': 44, 'companyTradesAtStockExchange': 30, 'countryLandBordersCountry': 24, 'hasArea': 22, 'hasCapacity': 23, 'personHasCityOfDeath': 22}
planner_lookahead_depth_artifact 2
history_bins 64
supports_depth_two False blockers 5
depth_if_rederived_now 1
blocker_sample ['countryLandBordersCountry/__fallback__/REVERSE_CHECK (support 67) records no successor statistics', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK (support 36) records no successor statistics', 'countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK (support 31) records no successor statistics', 'hasArea/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 43) records no successor statistics', 'hasCapacity/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL (support 57) records no successor statistics']
validation_readiness NOT_READY
production_loader_error the planner calibration requests depth-2 lookahead but 5 historical bin(s) record no successor statistics, e.g
m17_call_plan 4 4
m17_cold_cost 8
m17_warm_cost 4
normal_ledger_calls 26 runtime_calls 26
normal_ledger_tokens 20 runtime_tokens 20
normal_outstanding []
val_mode INTERLEAVED
invalid_mode bogus refused pipeline.mode 'bogus' is not a supported execution mode
invalid_mode Interleaved refused pipeline.mode 'Interleaved' is not a supported execution mode
invalid_mode batch refused pipeline.mode 'batch' is not a supported execution mode
invalid_mode parallel refused pipeline.mode 'parallel' is not a supported execution mode
valid_mode interleaved INTERLEAVED
valid_mode staged STAGED
```

The current stale real artifacts still keep readiness `NOT_READY` because the
old planner artifact declares `lookahead_depth = 2`. This is expected and is
not a source-fix failure. Under the corrected source, the current 64-bin package
would derive depth 1.

Execution-mode refusal ordering probe:

```text
bogus exit pipeline.mode 'bogus' is not a supported execution mode; this build implements interleaved, staged built {'enumerator': 0, 'verifier': 0}
Interleaved exit pipeline.mode 'Interleaved' is not a supported execution mode; this build implements interleaved, staged built {'enumerator': 0, 'verifier': 0}
batch exit pipeline.mode 'batch' is not a supported execution mode; this build implements interleaved, staged built {'enumerator': 0, 'verifier': 0}
parallel exit pipeline.mode 'parallel' is not a supported execution mode; this build implements interleaved, staged built {'enumerator': 0, 'verifier': 0}
```

No model/runtime construction occurred for invalid execution modes.

## Validation

```text
$ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_accounting_invariant_fail_stop.py tests/test_layer4_settlement_lifecycle.py tests/test_production_source_fixes.py tests/test_production_activation.py
167 passed, 1 skipped in 4.98s

$ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_m20_precharge_gate.py tests/test_relation_budget.py tests/test_layer6_integration.py tests/test_action_execution_seam.py tests/test_pipeline_production_seam.py
234 passed in 4.06s

$ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
3371 passed, 4 skipped, 1 xfailed in 49.23s

$ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pyflakes src scripts tests
exit 0, no output

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py

$ sha256sum configs/calibration/*.json
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  configs/calibration/m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  configs/calibration/m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  configs/calibration/m21_planner_calibration.json

$ git diff -- benchmark/
<no output>

$ git diff -- configs/calibration/
<no output>

$ git diff --name-only
scripts/run_cover.py
src/cover_kbc/control/budget_accounting.py
src/cover_kbc/control/micro_planner.py
src/cover_kbc/control/relation_budget.py
src/cover_kbc/controller_calibration/derivation.py
src/cover_kbc/controller_calibration/readiness.py
src/cover_kbc/pipeline.py
src/cover_kbc/verification/specialist_verifier.py
tests/test_action_execution_seam.py
tests/test_layer6_integration.py
tests/test_m20_precharge_gate.py
tests/test_micro_planner.py
tests/test_pipeline_production_seam.py
tests/test_relation_budget.py

$ git diff --stat
 scripts/run_cover.py                               | 226 ++++++++++++++++++++-
 src/cover_kbc/control/budget_accounting.py         |  39 +++-
 src/cover_kbc/control/micro_planner.py             |  31 ++-
 src/cover_kbc/control/relation_budget.py           |  82 ++++++--
 src/cover_kbc/controller_calibration/derivation.py |  68 ++++++-
 src/cover_kbc/controller_calibration/readiness.py  | 123 +++++++++++
 src/cover_kbc/pipeline.py                          | 219 +++++++++++++++++---
 src/cover_kbc/verification/specialist_verifier.py  |  35 ++++
 tests/test_action_execution_seam.py                |   2 +-
 tests/test_layer6_integration.py                   |  29 ++-
 tests/test_m20_precharge_gate.py                   |   4 +-
 tests/test_micro_planner.py                        |   9 +
 tests/test_pipeline_production_seam.py             |  10 +-
 tests/test_relation_budget.py                      |  66 +++++-
 14 files changed, 857 insertions(+), 86 deletions(-)
```

`git diff` was run and inspected. Its full terminal output was non-empty because
the pending remediation is uncommitted; the tool display truncated the textual
diff after the relevant hunks. The exact tracked-file list and stat are above.

`git status --short` before writing this audit:

```text
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
 M tests/test_pipeline_production_seam.py
 M tests/test_relation_budget.py
?? configs/calibration/
?? configs/experiments/cover_kbc_v2_validation.yaml
?? docs/audits/0050-production-activation-f11-f22-f24.md
?? docs/audits/0051-real-artifact-verification-and-production-hold.md
?? docs/audits/0052-source-fixes-depth-two-budget-ownership-execution-mode.md
?? docs/audits/0053-targeted-source-config-fix-review.md
?? docs/audits/0054-layer4-settlement-lifecycle-and-execution-mode-ordering.md
?? docs/audits/0055-reservation-lifecycle-mode-ordering-reverification.md
?? docs/audits/0056-accounting-invariant-fail-stop.md
?? src/cover_kbc/controller_calibration/production.py
?? tests/test_accounting_invariant_fail_stop.py
?? tests/test_layer4_settlement_lifecycle.py
?? tests/test_production_activation.py
?? tests/test_production_source_fixes.py
?? tests/test_real_calibration_artifacts.py
```

The calibration artifact hashes match Audits 0053-0056. I did not run the real
134 MB TRAIN re-derivation and did not modify any calibration artifact.

## Explicit Answers

A. AccountingInvariantError is process-fatal end-to-end? YES

B. Generic row handler cannot swallow it? YES

C. Later rows cannot execute? YES

D. Poisoned ledger cannot be reused? YES

E. CLI exits non-zero? YES

F. Successful predictions/manifest cannot be emitted? YES

G. Diagnostic artifacts cannot masquerade as completion? YES

H. Failed run cannot resume from accounting-corrupted state? YES

I. Full failure chain remains diagnosable? YES

J. Ordinary non-accounting row errors retain existing behavior? YES

K. Normal reservation/accounting regression sanity passes? YES

L. Any new P0/P1? NO

M. Safe to commit ALL pending source/config/artifact/audit files? YES

N. After commit, safe to run the ONE real 134 MB re-derivation? YES

STOP SOURCE REVIEW - NEXT STEP IS COMMIT, THEN CLEAN EXACT-SHA REAL RE-DERIVATION.
