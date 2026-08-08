# Audit 0056 — `AccountingInvariantError` is process-fatal

**Date:** 2026-08-08
**Scope:** the one P1 Audit 0055 left open. Fail-stop semantics only.
**HEAD at audit time:** `b1804646dec3d2343dcf2cf8b277529071b89485` (working tree dirty, uncommitted)
**Python:** 3.14.5

---

## 0. Position

Audit 0055 verified every normal Layer-4 accounting path and returned **FAIL —
DO NOT COMMIT** on one finding: the overrun path was catchable. This milestone
closes it and touches nothing 0055 verified.

| Question | Answer |
|---|---|
| Is `AccountingInvariantError` now process-fatal? | **YES** |
| Was any verified 0055 property changed? | **NO** |
| Calibration artifacts touched? | **NO** — byte-identical |
| Real re-derivation / VAL run / TEST read / weights? | **NO** |
| `FULL_VALIDATION_READY`? | **NO** — still `NOT_READY`, correctly |

Contract read first: `COVER_KBC_Technical_Proposal_New.pdf`, in full. The
governing sentences are §16 — *"Budget accounting must be cache-aware and
precharge before every neural call. **No action may exceed the hard cap.**"* —
§21.2 — *"all neural calls go through runtime accounting"* — and Algorithm 1
line 19, where `ExecuteAndRecord` returns the budget `B` alongside the graph.
A run that continues after the ledger has refused an impossible settlement is
running with a budget the algorithm can no longer produce.

---

## 1. Trace before editing

Every path that could catch the error was enumerated, not assumed.

| Site | Before | Catches it? |
|---|---|---|
| `_release_hold` (`pipeline.py:2252`) | raises `AccountingInvariantError` from the ledger's `BudgetSchedulerError` | it is the origin |
| `execute_action` (`pipeline.py:1922`) | `except BaseException: _release_hold(...); raise` | re-raises; does not swallow |
| **`CoverPipeline.run` (`pipeline.py:3068`)** | `except Exception` → `PIPELINE_ERROR` row → **continue** | **yes — the defect** |
| `CoverPipeline.decide` / `enumerate` / `verify` / `resume` | no handler | no |
| `run_cover.main()` | no handler around `pipeline.run` | no, but also no controlled failure |
| `run_train_calibration_collection.py:559` | `except BaseException` → `if is_fatal(error): raise` | already correct — `AccountingInvariantError` is in its `FATAL_ERRORS` |
| `run_train_calibration_collection.py:643` | outer `except BaseException` → `aborted`, exit gate returns **1** | already correct |
| `run_staged.py` | uses `enumerate`/`verify`/`resume`/`decide`, no per-row handler | no |
| `RunTracer.__exit__` | `self.close()`, returns `None` | no |
| `real_model_smoke.py` | not a production entry point | n/a |

Checkpoint and resume: `run_cover.py` **writes no checkpoint and reads none** —
`grep` finds no `add_argument("--resume")`, no `--checkpoint`, no checkpoint
read. The `collection-checkpoint-v2` machinery belongs to the TRAIN collection
runner, which already treats this error as fatal. So there was no resume path
to poison; the requirement is to keep it that way, and that is now asserted
behaviourally rather than by absence-of-feature.

The architecture already had a canonical fatal-error concept — `FATAL_ERRORS` /
`is_fatal` in the collection runner, whose comment states the boundary as
*"would the next row still be measured correctly?"* and lists
`AccountingInvariantError` first. This milestone applies the same boundary at
the second entry point rather than inventing a new one.

---

## 2. The change

Two edits. Nothing else.

### 2.1 `CoverPipeline.run` — the fatal handler precedes the generic one

```python
            except AccountingInvariantError:
                # Fail-stop. Deliberately before the generic handler below: a
                # broken accounting invariant is not a row-local data problem
                # and must never become a PIPELINE_ERROR prediction. No row
                # after this one is attempted and no partial result is
                # returned, so no caller can mistake this for a finished run.
                raise
            except Exception as exc:  # noqa: BLE001 - one query must not kill the run
```

Re-raised **bare**, so the chain survives untouched. `run` returns no
`PipelineResult` on this path, so there is no partial result object a caller
could write out.

The docstring now states the boundary explicitly and names the collection
runner's `FATAL_ERRORS` table as the same rule. Ordinary failures are unchanged:
one malformed row still becomes a `PIPELINE_ERROR` and the run continues.

### 2.2 `run_cover.main()` — a controlled, non-zero, artifact-free abort

```python
        try:
            result = pipeline.run(queries, progress=True)
        except AccountingInvariantError as error:
            _abort_on_accounting_invariant(
                out_dir, run_id, split, len(queries), error)
```

`_abort_on_accounting_invariant` never returns. It writes one diagnostic file,
prints the whole chain to stderr, and `raise SystemExit(2) from error`. It sits
inside the `with RunTracer(...)` block, so **everything below it is
unreachable**: `manifest.finish()`, `write_predictions`, `write_trace`, the
fifteen per-module artifacts, `errors.json`, `evaluate_predictions`,
`write_report` and `manifest.write`.

Before this, an unhandled exception would also have exited non-zero — but with
a bare traceback, no record of what happened, and no statement that the run
directory is a failure. The controlled abort makes the failure legible without
making it survivable.

**The diagnostic marker.** `FAILED_ACCOUNTING_INVARIANT.json`. It is not a
checkpoint and not a manifest, and it is named so it cannot be mistaken for
either. It carries `"status": "aborted"`, `"complete": false`,
`"submittable": false`, `"predictions_written": false`,
`"manifest_written": false`, the expected query count, and the **full exception
chain** — which is where the requirement to preserve both failures is honoured:
a settlement overrun sitting on top of an ordinary action failure records both,
outermost first, whereas a one-line error string records only the outer one.
No completion or submission contract reads this file, and nothing resumes from
it.

### 2.3 What was deliberately *not* done

The refused reservation stays `OUTSTANDING`. It was not marked settled with
`actual > reserved`, not cancelled, not released, and `hard_calls` was not
widened. `BudgetLedger` was right to refuse, and mutating the reservation to
make `outstanding == []` would be cosmetics over a real inconsistency. That is
safe **only** because the run is now provably fail-stop and no later row can
reach that ledger — which §3 proves rather than asserts.

---

## 3. The forced two-row transcript

Driven through the real `scripts/run_cover.py main()`. The readiness gate, the
production calibration loader, Module 20, Module 21, Layer 6, the artifact
writers and the exit path are all the real ones — the run genuinely reaches
`FULL_VALIDATION_READY`. Two things are substituted because a test machine has
neither: the model (a scripted non-neural runtime) and the rows (a synthetic
two-row file). **No official split is read and no weights are loaded.**

The fault is the pre-Audit-0054 defect reinstated: a `CoverPipeline` subclass
whose `_m17_control_calls_needed` returns 0, claiming every Module 17 control is
cached. On a cold cache the hold is 4 calls and the action really spends 8. The
error is therefore produced by real code, not constructed by hand.

```
=== FORCED TWO-ROW OVERRUN THROUGH scripts/run_cover.py main() ===
readiness   : FULL_VALIDATION_READY
calibration : 6 relation budget(s), 54 bin(s), tau=0.0
run_id      : cover_kbc_v2_validation_val_20260808T101655Z
split       : val (2 queries of 2)
model       : offline/scripted-enumerator
execution   : interleaved (from config)
outputs     : /tmp/tmpgpd6p0ms/run
  ROW ENTERED  row_index=0 subject='Testland'

RUN ABORTED - ACCOUNTING INVARIANT
  AccountingInvariantError: Module 20 could not settle
    'M17:SPECIALIST_VERIFY:alphaland': reservation '423d74063a776b83' held 4
    calls but 8 were spent; a neural call was made outside the precharge
  BudgetSchedulerError: reservation '423d74063a776b83' held 4 calls but 8 were
    spent; a neural call was made outside the precharge
  no predictions and no manifest were written for
    cover_kbc_v2_validation_val_20260808T101655Z
  diagnostic: /tmp/tmpgpd6p0ms/run/FAILED_ACCOUNTING_INVARIANT.json

=== TRANSCRIPT ===
rows entered           : [('Testland', 0)]
rows in split          : 2
CLI outcome            : SystemExit(2)
exit status is non-zero: True
output directory       : ['FAILED_ACCOUNTING_INVARIANT.json', 'calls.jsonl']
predictions.jsonl      : False
manifest.json          : False
metrics.json           : False
ledgers created        : [('Testland', 'countryLandBordersCountry', 0)]
  ('Testland', 'countryLandBordersCountry', 0) hard_calls=40 committed=13
    statuses=['OUTSTANDING']
```

Marker contents:

```json
{
 "status": "aborted",
 "reason": "accounting_invariant",
 "run_id": "cover_kbc_v2_validation_val_20260808T101655Z",
 "split": "val",
 "expected_queries": 2,
 "complete": false,
 "submittable": false,
 "predictions_written": false,
 "manifest_written": false,
 "detail": "Physical accounting stopped being representable by the precharged
   Module 20 envelope: a neural call happened outside the precharge, so the
   ledger refused the settlement and the reservation could not be closed. The
   run was stopped; later queries were not attempted. This file is a diagnostic
   record of a FAILED run and is not a manifest, a submission, or a resumable
   checkpoint.",
 "failures": [
  {"type": "AccountingInvariantError", "message": "Module 20 could not settle
    'M17:SPECIALIST_VERIFY:alphaland': reservation '423d74063a776b83' held 4
    calls but 8 were spent; a neural call was made outside the precharge"},
  {"type": "BudgetSchedulerError", "message": "reservation '423d74063a776b83'
    held 4 calls but 8 were spent; a neural call was made outside the precharge"}
 ]
}
```

Reading directly off that transcript:

* **row 1 entered** — `ROW ENTERED row_index=0`;
* **`AccountingInvariantError` raised** — with the ledger's own refusal beneath it;
* **row 2 NOT entered** — `rows entered: [('Testland', 0)]` against a 2-row split;
* **exactly one ledger exists** — no ledger was created for row 2, so the poisoned
  one cannot be observed or reused;
* **CLI failed** — `SystemExit(2)`;
* **no successful completion artifact** — no `predictions.jsonl`, no
  `manifest.json`, no `metrics.json`, no `trace.jsonl`, none of the fifteen
  per-module files. The directory holds the failure marker and the call log.

**Control.** The same harness with the fault removed returns 0, writes
`manifest.json` (`num_queries: 2`) and a 2-row `predictions.jsonl`, produces
`relation_budget.jsonl` / `micro_planner.jsonl` / `layer6_control.jsonl`, and
writes no marker. Every absence above is caused by the fault, not by the
harness.

---

## 4. Tests

New file `tests/test_accounting_invariant_fail_stop.py` — **20 tests**, all
passing. Eleven drive the real `run_cover.main()`.

| # | Required property | Test |
|---|---|---|
| 1 | forced under-reservation raises it | `test_a_forced_under_reservation_raises_the_accounting_invariant` |
| 2 | `run()` re-raises, no `PIPELINE_ERROR` | `test_run_re_raises_instead_of_recording_a_pipeline_error` |
| 3 | two-row run executes row 1 only | `test_a_two_row_run_stops_after_the_failing_row`, `test_the_failing_row_is_the_last_row_entered` (CLI) |
| 4 | no later ledger created | `test_no_ledger_is_created_for_any_later_query` |
| 5 | CLI exits non-zero | `test_the_cli_exits_non_zero` |
| 6 | no successful manifest | `test_the_cli_writes_no_manifest` |
| 7 | no complete predictions file | `test_the_cli_writes_no_predictions`, `test_the_only_artifacts_are_unmistakably_a_failure` |
| 8 | partial artifact cannot pass for completion | `test_the_diagnostic_marker_cannot_pass_for_a_completion_record` |
| 9 | resume cannot continue | `test_nothing_can_resume_from_the_failed_run`, `test_re_running_into_the_failed_directory_restarts_and_fails_again` |
| 10 | chain keeps all three failures | `test_the_exception_chain_keeps_both_failures`, `test_the_marker_records_the_whole_failure_chain` |
| 11 | ordinary row errors unchanged | `test_an_ordinary_row_failure_is_still_contained` |
| 12 | normal production unchanged | `test_a_healthy_two_row_run_is_unaffected`, `test_the_same_cli_run_succeeds_without_the_fault`, `test_the_control_run_really_reaches_production_layer_six` |
| 13 | lifecycle tests still pass | `tests/test_layer4_settlement_lifecycle.py` — 32 passed |
| 14 | ledger/runtime equality on normal runs | `test_ledger_and_runtime_still_agree_on_a_healthy_run` |
| 15 | execution-mode ordering still passes | `tests/test_production_source_fixes.py` — 56 passed |

Plus `test_the_fatal_handler_precedes_the_generic_one`, which pins the handler
ordering so a later edit cannot silently reverse it.

Item 9's second test is a behavioural proof rather than an absence-of-feature
claim: the same CLI is invoked twice into the same output directory, and it
starts at row 1 both times and fails both times.

`tests/test_pipeline_production_seam.py::build` gained an optional
`pipeline_cls` parameter so a fault-injecting subclass can be substituted while
every other wiring decision stays shared — the thing under test then differs
from production in exactly one place. No existing test was deleted or weakened.

### 4.1 Discrimination

Each edit was reverted **in memory**, the suite re-run, and the edit restored:

| Reverted | Result |
|---|---|
| `except AccountingInvariantError: raise` removed from `run` | **7 failed, 7 errored** of 20 |
| CLI `try/except` removed from `main()` | **2 failed, 7 errored** of 20 |

The errors are the CLI fixture failing at setup, which is itself the point: with
the handler gone there is no controlled `SystemExit` contract to assert against.

---

## 5. Validation

```
$ python -m pytest tests/test_accounting_invariant_fail_stop.py -q -p no:randomly
20 passed in 2.48s

$ python -m pytest tests/test_layer4_settlement_lifecycle.py -q -p no:randomly
32 passed in 0.97s

$ python -m pytest tests/test_production_source_fixes.py -q -p no:randomly
56 passed in 0.84s

$ python -m pytest tests/test_production_activation.py -q -p no:randomly
59 passed, 1 skipped in 1.57s

$ python -m pytest tests/test_m20_precharge_gate.py tests/test_relation_budget.py \
      tests/test_layer6_integration.py tests/test_action_execution_seam.py \
      tests/test_pipeline_production_seam.py tests/test_micro_planner.py \
      tests/test_m21_production_bridge.py -q -p no:randomly
313 passed in 2.74s

$ python -m pytest tests/test_real_calibration_artifacts.py -q -p no:randomly
17 passed, 1 xfailed in 13.51s

$ python -m pytest tests/ -q -p no:randomly
3371 passed, 4 skipped, 1 xfailed in 47.42s

$ python -m pytest tests/ -q            # randomized order
3371 passed, 4 skipped, 1 xfailed in 48.40s
```

3351 → 3371: the 20 new fail-stop tests.

```
$ python -m pyflakes scripts/run_cover.py src/cover_kbc/pipeline.py \
    src/cover_kbc/control/budget_accounting.py \
    src/cover_kbc/verification/specialist_verifier.py \
    src/cover_kbc/control/relation_budget.py \
    src/cover_kbc/controller_calibration/derivation.py \
    src/cover_kbc/controller_calibration/production.py \
    src/cover_kbc/controller_calibration/readiness.py \
    src/cover_kbc/control/micro_planner.py \
    tests/test_accounting_invariant_fail_stop.py \
    tests/test_layer4_settlement_lifecycle.py \
    tests/test_production_source_fixes.py tests/test_production_activation.py \
    tests/test_pipeline_production_seam.py tests/test_real_calibration_artifacts.py
pyflakes exit=0            # no output
```

```
$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py

$ git diff -- benchmark/ | wc -l
0

$ sha256sum configs/calibration/*.json
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  configs/calibration/m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  configs/calibration/m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  configs/calibration/m21_planner_calibration.json

$ git diff -- configs/calibration/ | wc -l
0
```

All four identical to Audit 0055's recorded values.

```
$ git status --short
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

**Not run, by instruction:** the real 134 MB TRAIN re-derivation, the 478-row
VAL inference, any TEST read, any real model weight load.

---

## 6. Preserved properties

Re-checked after the edits, not assumed.

```
P2-A: bins 64  depth_two False  -> depth 1  blockers 5
M20 : {'awardWonBy': 44, 'companyTradesAtStockExchange': 30,
       'countryLandBordersCountry': 24, 'hasArea': 22, 'hasCapacity': 23,
       'personHasCityOfDeath': 22}
readiness: NOT_READY  may_run: False  blockers: 1
```

Also intact, by suite: the normal reservation lifecycle (32 tests); M17 cold
reserve 8 / warm reserve 4; `control_calls_needed` making zero neural calls;
exactly-once prior + settlement accounting with `committed_calls ==
query_physical_cost`; protected reserves and spend-class caps; execution mode
resolved before runtime construction and invalid mode building zero runtimes
(56 tests); F-11 / F-22 / F-24 (59 tests); strict `U > tau_continue`; no neural
training; no external RAG/web/KB; `benchmark/` unchanged; calibration artifacts
byte-identical.

Readiness is still blocked by the one expected blocker — the shipped planner
artifact declares `lookahead_depth = 2` because it was derived under the old
rule. Nothing was weakened to make anything green, and the strict `xfail` on
`test_the_real_artifacts_reach_full_validation_ready` remains strict.

---

## 7. Files changed in this milestone

**Source**
* `src/cover_kbc/pipeline.py` — `run()` re-raises `AccountingInvariantError`
  ahead of the generic per-row handler; docstring states the boundary
* `scripts/run_cover.py` — `_abort_on_accounting_invariant`,
  `_exception_chain`, `ACCOUNTING_FAILURE_MARKER`; the `try/except` around
  `pipeline.run`

**Tests**
* `tests/test_accounting_invariant_fail_stop.py` — new, 20 tests
* `tests/test_pipeline_production_seam.py` — `build(..., pipeline_cls=...)`

**Not changed:** `BudgetLedger`'s hard-cap arithmetic; reservation widening;
borrowing; refunds of calls that occurred; the calibration artifacts;
`benchmark/`; models, prompts, views, M17 quality; P2-A; M7/M20 ownership; the
execution-mode mapping or ordering.

---

## 8. Explicit answers

| # | Question | Answer |
|---|---|---|
| 1 | `AccountingInvariantError` process-fatal? | **YES** |
| 2 | Generic per-row handler bypassed? | **YES** — the fatal clause precedes it, pinned by a test |
| 3 | Later rows impossible after an overrun? | **YES** — 2-row probe entered row 1 only |
| 4 | Corrupted ledger impossible to reuse? | **YES** — one ledger exists; no later query is entered, so none is created |
| 5 | CLI exits non-zero? | **YES** — `SystemExit(2)` |
| 6 | Successful manifest/completion output suppressed? | **YES** — no manifest, no metrics, no module artifacts |
| 7 | Resume from corrupted state impossible? | **YES** — no resume path exists, and a second invocation restarts at row 1 and fails again |
| 8 | Outstanding overrun reservation safe? | **YES** — left refused and unmutated, safe because the run is fail-stop |
| 9 | Diagnostic chain preserved? | **YES** — `AccountingInvariantError` → `BudgetSchedulerError` → original action failure, in the exception chain and in the marker |
| 10 | Ordinary row-error behaviour unchanged? | **YES** — still `PIPELINE_ERROR`, still continues |
| 11 | Normal lifecycle still correct? | **YES** |
| 12 | Execution-mode fix still correct? | **YES** |
| 13 | Any new P0/P1? | **NO** |
| 14 | Safe for one final targeted independent review? | **YES** |
| 15 | Safe to commit? | **YES, after an independent PASS** |
| 16 | Safe to run the real re-derivation? | **NO** — not until independent PASS **and** commit |

`FULL_VALIDATION_READY`: **NO**. The real artifacts have not been re-derived.

Nothing was committed or pushed.
