# Audit 0054 — Layer-4 settlement lifecycle (P1) and execution-mode ordering (P2)

**Date:** 2026-08-08
**Scope:** the two remediation items Audit 0053 left open. Source and tests only.
**HEAD at audit time:** `b1804646dec3d2343dcf2cf8b277529071b89485` (working tree dirty, uncommitted)
**Python:** 3.14.5

---

## 0. Position

Audit 0053 returned **FAIL — DO NOT COMMIT** with two findings and three
verified-correct areas. This milestone closes the two findings and touches
nothing that 0053 verified.

| Question | Answer |
|---|---|
| P1 — precharge → execute → settle/cancel completed? | **YES** |
| P2 — execution mode resolved before model construction? | **YES** |
| Were the verified P2-A / M7-M20 / mode-mapping fixes changed? | **NO** |
| Were the real calibration artifacts touched? | **NO** — byte-identical |
| Real re-derivation run? | **NO** — out of scope |
| VAL run / TEST read / weights loaded? | **NO** |
| Is the system `FULL_VALIDATION_READY`? | **NO** — still `NOT_READY`, correctly |

Contract read first: `COVER_KBC_Technical_Proposal_New.pdf`, in full. The
sentences this milestone is accountable to are §16 — *"Budget accounting must be
cache-aware and precharge before every neural call. No action may exceed the
hard cap."* — §21.2 — *"all neural calls go through runtime accounting"* — and
Algorithm 1 line 19, where `ExecuteAndRecord` returns **both** the graph and the
budget `B`, i.e. execution is what updates the budget, not reservation alone.

---

## 1. P1 — the reservation handle is retained and the cycle closes

### 1.1 What was wrong

`CoverPipeline.execute_action` called `_precharge`, which reserved against the
real ledger and then returned `(True, "")`. The `BudgetReservation` was
discarded. No production path called `BudgetLedger.settle` or `.cancel`, so
every executed Layer-4 action left an outstanding hold for the rest of the query
and its conservative over-reservation was never released.

### 1.2 Tracing first

Before editing, the existing contract was traced end to end:

| Component | What it actually does |
|---|---|
| `execute_action` (`pipeline.py:1838`) | snapshot → project → precharge → execute → integrate → snapshot → record |
| `_precharge` (`pipeline.py:2113`) | collection/no-scheduler short-circuits, then `ledger.reserve(descriptor)` |
| `_budget_ledger_for` | one ledger per query, **cached**, priors read once at creation |
| `query_physical_cost` | runtimes' own counters differenced against this query's baseline |
| `BudgetLedger.reserve` | atomic; returns `BudgetReservation` **or** `BudgetDenial`; a denial holds nothing |
| `BudgetLedger.settle` | `actual > reserved` **raises** `BudgetSchedulerError`; releases the difference; status → `SETTLED` |
| `BudgetLedger.cancel` | releases the whole hold; status → `CANCELLED` |
| `_held` | refuses any second close on a non-`OUTSTANDING` reservation |
| `physical_delta` | raises `AccountingInvariantError` on a backwards counter or a broken role partition |

No second reservation object and no second ledger were introduced. The existing
`BudgetReservation` travels back from `_precharge` and is settled by identity
that this precharge produced — nothing is reconstructed or guessed.

### 1.3 The change

**`_precharge` now returns `(admitted, refusal, hold)`**, where `hold` is the
concrete `(ledger, reservation)` pair or `None`. `None` covers every path that
reserved nothing: collection mode, no scheduler, no descriptor, and a denial —
`reserve` holds nothing on a denial, so there is nothing to release.

**`execute_action` wraps everything that can spend a call** — both executors,
Layer-4 integration, the bridge and the Module 19 refresh — in one block, and
closes the hold on both exits:

```python
try:
    ...execute, integrate, refresh...
    after = self.physical_snapshot()
except BaseException:
    self._release_hold(hold, before, self.physical_snapshot(), executed=False)
    raise
self._release_hold(hold, before, after, executed=True)
```

**`_release_hold`** decides settle-vs-cancel on what actually happened:

* **ran** → `settle` at the measured cost, *even when that cost is zero*. A
  fully cache-warm action is a real execution that cost nothing, and the
  settlement record says so; cancelling would erase the fact that it ran.
* **failed, spent nothing** → `cancel`.
* **failed after spending** → `settle` the measured spend. Calls that really
  happened are never refunded because the action later threw; that would put the
  run over the hard cap with the ledger reporting it was under.

The original exception is what propagates — settlement is bookkeeping, not an
outcome, and no action failure is converted into a success.

**Overrun is fail-closed.** `settle` raises when the actual spend exceeds the
hold, which means a neural call happened outside the precharge — precisely what
§16's precharge exists to prevent. `_release_hold` re-raises it as
`AccountingInvariantError`, the codebase's existing process-fatal accounting
signal, chained from the ledger's own message. Nothing borrows, nothing is
absorbed, and the hard cap is not widened.

### 1.4 The precharge had to become a genuine upper bound first

Tracing the settle contract surfaced a second half of the same defect, and the
fix is incomplete without it.

`project_action` built the Module 17 descriptor with `m17_actions(...)` and
never passed `control_calls_needed`, taking the default **0** — "every
contextual calibration control is already cached". That is false until the first
Module 17 action of the run for that relation. A cold action therefore reserved
**4** calls and spent **8**: four factual readings plus four real `score_labels`
control measurements. Under the old code that silently exceeded the envelope;
with settlement in place it would fail closed on the very first production
action.

`m17_actions` has accepted `control_calls_needed` all along and
`test_the_live_m17_cold_and_warm_precharge_are_exact` already asserts the
cold(8)/warm(4) contract — the production call site simply never supplied the
number. Two small changes close it:

* **`SpecialistVerifier.control_calls_needed(request, contract, runtime)`** — a
  read-only method on the owner. It enumerates the same (phrasing, label order)
  templates `verify` will render (the template id embeds the label order, so two
  orders of one phrasing are two controls) and asks Module 4's calibrator. It
  renders nothing, mutates no cache, and **makes no neural call**. Zero when
  calibration is off.
* **`CoverPipeline._m17_control_calls_needed`** — asks Module 17 for the live
  number. When it cannot be asked (no verifier, no runtime, or a target Module
  17 will not build a request for) it reserves the **cold** plan, following
  `budget_accounting`'s own stated rule that an unknown cache state is reserved
  as a miss because the mistake is only discovered after the call.

No Module 17 reading, template, label order, prompt or verdict changed. Only the
accounting of its controls did.

### 1.5 Measured result — the real production path

Driven through `enumerate_query` / `decide_graph` with a scripted offline
runtime (no weights), on the `countryLandBordersCountry` fixture:

```
action                                      held   tok  act  atok  relC  relT  status
M17:SPECIALIST_VERIFY:alphaland                8     0    8     0     0     0  SETTLED
M17:SPECIALIST_VERIFY:alphaland betaland       4     0    4     0     0     0  SETTLED
M17:SPECIALIST_VERIFY:betaland                 4     0    4     0     0     0  SETTLED
M18:CANDIDATE_FREE_RECALL                      1   256    1     2     0   254  SETTLED
M18:COUNTERFACTUAL:alphaland:hn0               1   256    1     2     0   254  SETTLED
M18:COUNTERFACTUAL:alphaland:hn1               1   256    1     2     0   254  SETTLED

outstanding                 : []
cancelled                   : []
prior_calls                 : 7
ledger.committed_calls      : 26
query_physical_cost calls   : 26
ledger.committed_tokens     : 20
query_physical_cost tokens  : 20
hard_calls                  : 64
```

Every property the milestone asked for is visible in that table:

* the first M17 action is **cold**: held 8, spent 8, released 0;
* the next two are **warm**: held 4, spent 4 — no charge for controls not made;
* M18 holds its 256-token decode bound and spends 2, releasing 254 each time —
  conservative over-reservation genuinely returned;
* nothing outstanding, nothing cancelled, one settlement per reservation;
* **`ledger.committed_calls == query_physical_cost["physical_calls"]` (26 == 26)**
  and the same for tokens (20 == 20). Two independent owners — Module 20's
  ledger and the runtimes' own counters — agree exactly. Nothing is lost, and
  nothing is charged twice.

### 1.6 The Audit 0052 prior-spend interaction is intact

Explicitly re-verified rather than assumed. `_budget_ledger_for` caches one
ledger per query and reads `query_physical_cost` **once**, at construction,
before any Layer-4 reservation exists — both call sites are inside Layer-4
planning/affordability. `prior_calls` is held in its own field, apart from
reservations and settlements, so no settle or cancel can refund it.

Measured: priors at ledger open = 7 = the acquisition phase's physical calls;
after the whole Layer-4 loop, `prior_calls` is still 7 and `committed_calls` is
7 + 19 settled = 26. Settled Layer-4 spend is never re-imported as prior spend.
No parallel counter was created.

---

## 2. P2 — the mode is refused before any model is built

`resolve_execution_mode(config)` moved up in `run_cover.main()`, next to
`check_router_consistency()` and `check_library_covers_contracts()` — the other
cheap fail-closed config checks — and therefore before `load_dataset` and well
before `build_runtime(enumerator_cfg)`. The stale comment that claimed this was
already true is gone with it.

Nothing else about the mode changed: `interleaved` and `staged` both map, the
VAL config still declares `pipeline.mode: interleaved`, the config remains the
owner, and `INTERLEAVED` is not hardcoded anywhere.

Proved dynamically rather than by reading the file. The real `main()` is driven
on the real VAL config with `build_runtime` replaced by a counter that raises on
first use:

| `pipeline.mode` | result | `build_runtime` calls |
|---|---|---|
| `bogus` | `SystemExit: not a supported execution mode` | **0** |
| `Interleaved` | `SystemExit: not a supported execution mode` | **0** |
| `batch` | `SystemExit: not a supported execution mode` | **0** |
| `parallel` | `SystemExit: not a supported execution mode` | **0** |
| `interleaved` | reached runtime construction | 1 |
| `staged` | reached runtime construction | 1 |
| absent | reached runtime construction | 1 |

The counter raises rather than returning, so neither the enumerator nor the
verifier path can proceed past it and no weight is ever loaded. The valid cases
are asserted too, so the refusal is specific rather than a blanket early exit.

---

## 3. Tests

### 3.1 New: `tests/test_layer4_settlement_lifecycle.py` — 32 tests

Scripted offline runtimes throughout; the real artifacts are read-only. Both
levels are covered, because "the ledger can settle" and "production settles" are
different claims and only the second one was missing — **eleven of the tests
enter through the real `CoverPipeline` path**, not the ledger API.

Mapped against the twenty required properties:

| # | Property | Test |
|---|---|---|
| 1 | reserve → execute → settle | `test_a_production_query_leaves_no_outstanding_reservation` |
| 2 | conservative reserve > actual, released | `test_a_conservative_reservation_releases_what_it_did_not_use`, `test_released_capacity_is_available_to_the_next_action` |
| 3 | reserve == actual | `test_an_exact_reservation_releases_nothing` |
| 4 | zero-cost / cache-hit execution | `test_a_zero_cost_execution_settles_rather_than_cancels`, `test_a_fully_cached_control_costs_zero_and_is_reserved_as_zero` |
| 5 | failure before any call → cancel | `test_a_failure_before_any_physical_call_cancels_the_hold` |
| 6 | failure after calls → settle actual | `test_a_failure_after_physical_calls_settles_the_real_spend` |
| 7 | original exception still propagates | `test_the_original_failure_is_what_propagates` |
| 8 | never `OUTSTANDING` afterwards | tests 1, 5, 6, `test_an_unknown_action_kind_still_closes_its_hold` |
| 9 | settle exactly once | `test_every_reservation_is_settled_exactly_once` |
| 10 | cancel exactly once | `test_a_failure_before_any_physical_call_cancels_the_hold` |
| 11 | settle and cancel mutually exclusive | `test_settlement_and_cancellation_are_mutually_exclusive` |
| 12 | cumulative capacity across actions | `test_repeated_actions_consume_cumulative_capacity` |
| 13 | no ledger recreation | `test_the_ledger_is_never_recreated_within_a_query` |
| 14 | prior spend never refunded | `test_prior_spend_survives_every_settlement_and_cancellation` |
| 15 | Layer-4 spend not re-imported as prior | `test_settled_layer_four_spend_is_never_re_imported_as_prior_spend` |
| 16 | denied action never executes | `test_a_denied_action_never_executes_and_leaves_no_hold` |
| 17 | denial leaves no dangling hold | same, plus `test_a_denial_returns_no_hold_from_precharge` |
| 18 | award protected reserve correct | `test_the_award_verification_reserve_still_protects_its_floor` |
| 19 | M17 still four non-cacheable readings | `test_the_live_m17_descriptor_is_still_four_factual_readings` |
| 20 | all six real M20 envelopes unchanged | `test_the_real_m20_envelopes_are_unchanged` (parametrised ×6) |

Plus the cross-owner invariant
`test_the_ledger_total_equals_what_the_runtimes_actually_spent`, the fail-closed
overrun (`test_a_settlement_overrun_fails_closed_without_widening_the_cap`,
which also asserts the hold stays `OUTSTANDING` and `hard_calls` is unchanged),
the upper-bound trio
(`test_the_precharge_reserves_the_cold_control_plan_when_it_is_cold`,
`test_an_unknowable_cache_state_is_reserved_as_a_miss`,
`test_module_17_answers_the_control_question_itself` — which asserts the
question costs zero physical calls), and
`test_collection_reserves_nothing_and_therefore_settles_nothing`.

### 3.2 Extended: `tests/test_production_source_fixes.py` — 48 → 56 tests

Eight new execution-mode tests, seven of them dynamic (`main()` with a counted
`build_runtime`) and one asserting the source ordering so a later edit cannot
silently undo it.

### 3.3 Three call sites updated for the new `_precharge` arity

`tests/test_action_execution_seam.py`, `tests/test_production_activation.py` and
`tests/test_m20_precharge_gate.py` each stub or unpack `_precharge`. All three
now use the three-tuple and additionally assert `hold is None` where that is the
contract. No test was deleted, weakened or skipped.

### 3.4 Discrimination — the tests fail against the previous behaviour

Each fix was reverted **in memory**, the suite re-run, and the fix restored from
a scratch copy:

| Reverted to the pre-fix behaviour | Failures |
|---|---|
| `_release_hold` calls removed from `execute_action` (the exact 0053 defect) | **11 of 32** |
| `control_calls_needed` argument removed from `project_action` | **18 of 32** |
| `resolve_execution_mode` moved back below `build_runtime` | **5 of 56** |

The second row is the joint requirement made visible: without the upper-bound
fix, settlement itself fails closed on the first cold Module 17 action. Both
halves of P1 are necessary.

---

## 4. Validation

Exact commands and results.

```
$ python -m pytest tests/test_layer4_settlement_lifecycle.py -q -p no:randomly
32 passed in 1.13s

$ python -m pytest tests/test_production_source_fixes.py -q -p no:randomly
56 passed in 1.31s

$ python -m pytest tests/test_layer4_settlement_lifecycle.py \
      tests/test_production_source_fixes.py \
      tests/test_production_activation.py \
      tests/test_real_calibration_artifacts.py -q -p no:randomly
164 passed, 1 skipped, 1 xfailed in 15.40s

$ python -m pytest tests/test_relation_budget.py tests/test_m20_precharge_gate.py \
      tests/test_micro_planner.py tests/test_m21_production_bridge.py \
      tests/test_layer6_integration.py tests/test_action_execution_seam.py \
      tests/test_pipeline_production_seam.py -q -p no:randomly
313 passed in 6.14s

$ python -m pytest tests/test_train_calibration_derivation.py \
      tests/test_derive_train_calibration_cli.py \
      tests/test_calibration_p1_remediation.py \
      tests/test_calibration_sufficiency.py \
      tests/test_controller_calibration_readiness.py -q -p no:randomly
155 passed in 3.23s

$ python -m pytest tests/ -q -p no:randomly
3351 passed, 4 skipped, 1 xfailed in 52.75s

$ python -m pytest tests/ -q            # randomized order
3351 passed, 4 skipped, 1 xfailed in 48.09s
```

Before this milestone the suite was 3311 passed; the 40 additions are the 32 new
lifecycle tests and 8 new execution-mode tests.

```
$ python -m pyflakes scripts/run_cover.py src/cover_kbc/pipeline.py \
    src/cover_kbc/verification/specialist_verifier.py \
    src/cover_kbc/control/budget_accounting.py \
    src/cover_kbc/control/relation_budget.py \
    src/cover_kbc/controller_calibration/derivation.py \
    src/cover_kbc/controller_calibration/production.py \
    src/cover_kbc/controller_calibration/readiness.py \
    src/cover_kbc/control/micro_planner.py \
    tests/test_layer4_settlement_lifecycle.py tests/test_production_source_fixes.py \
    tests/test_production_activation.py tests/test_real_calibration_artifacts.py \
    tests/test_relation_budget.py tests/test_m20_precharge_gate.py \
    tests/test_action_execution_seam.py
pyflakes exit=0            # no output
```

```
$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py

$ git diff -- benchmark/ | wc -l
0
```

Unchanged from Audit 0053's recorded value.

Calibration artifacts, before and after this milestone — identical:

```
$ sha256sum configs/calibration/*.json
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  configs/calibration/m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  configs/calibration/m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  configs/calibration/m21_planner_calibration.json

$ git diff -- configs/calibration/ | wc -l
0
```

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

$ git diff --stat
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

**Not run, by instruction:** the real 134 MB TRAIN re-derivation, the 478-row VAL
inference, any TEST read, any real model weight load.

---

## 5. Verified fixes preserved

Re-checked after this milestone's edits, not assumed.

**P2-A.** `supports_depth_two` unchanged. Applied read-only to the shipped
package: `bins 64, depth_two_supported False -> depth 1, blockers 5`. No runtime
silent downgrade, no fabricated successors, the package-wide rule intact.

**M7 / M20.** Envelopes read from the real artifact and through `build_plan`:

```
{'awardWonBy': 44, 'companyTradesAtStockExchange': 30,
 'countryLandBordersCountry': 24, 'hasArea': 22, 'hasCapacity': 23,
 'personHasCityOfDeath': 22}
```

No `min(core, calibrated)`; Module 7's core caps unchanged; prior physical spend
still belongs to Module 20's whole-query accounting through the one existing
counter; protected reserves and spend-class caps enforced (asserted in the new
suite as well as the existing one).

**Execution.** TRAIN collection `interleaved`, VAL config `interleaved`, config
owns the runtime mode.

**Also preserved:** F-11, F-22, F-24 (all 60 `test_production_activation.py`
tests pass), strict `U > tau_continue` in `micro_planner.py`, Module 8 as sole
final-output owner, `ProductionEvidenceBridge`, no neural training, no
RAG/web/external factual KB, `benchmark/` untouched, real calibration artifacts
byte-identical.

---

## 6. Readiness — still fail-closed, as expected

```
LOADER REFUSED: ProductionCalibrationError
state NOT_READY   may_run_validation False
blockers 1
  - calibration: the planner calibration requests depth-2 lookahead but 5
    historical bin(s) record no successor statistics ...
```

The shipped planner artifact still declares `lookahead_depth = 2` because it was
derived under the old rule. The gate was not weakened to make anything green,
and the strict `xfail` on
`test_the_real_artifacts_reach_full_validation_ready` remains strict.

---

## 7. Files changed in this milestone

**Source**
* `src/cover_kbc/pipeline.py` — `_precharge` returns the hold; `_release_hold`;
  guarded execution block in `execute_action`; `_m17_control_calls_needed`;
  `project_action` passes the live control count
* `src/cover_kbc/verification/specialist_verifier.py` — read-only
  `control_calls_needed`
* `scripts/run_cover.py` — `resolve_execution_mode` moved above `build_runtime`

**Tests**
* `tests/test_layer4_settlement_lifecycle.py` — new, 32 tests
* `tests/test_production_source_fixes.py` — 8 new execution-mode tests
* `tests/test_action_execution_seam.py`, `tests/test_production_activation.py`,
  `tests/test_m20_precharge_gate.py` — `_precharge` arity

**Not changed:** the three calibration artifacts, `benchmark/`, the frozen model
profile, any prompt, view, decoding setting, template, label order or verdict;
the depth rule; M7/M20 ownership; M20 envelope values.

---

## 8. Explicit answers

| # | Question | Answer |
|---|---|---|
| 1 | P1 reservation handle retained? | **YES** — the concrete `BudgetReservation` returns from `_precharge` and is settled by its own id |
| 2 | Success path settles? | **YES** — one settlement per reservation, at the measured cost, including zero |
| 3 | Failure-before-spend cancels? | **YES** |
| 4 | Failure-after-spend settles actual cost? | **YES** — and the original exception propagates |
| 5 | No dangling `OUTSTANDING` reservations? | **YES** — `outstanding: []` after a full production query, and after both failure paths |
| 6 | No double charging? | **YES** — `committed_calls == query_physical_cost` (26 == 26), tokens 20 == 20 |
| 7 | M7/M20 prior accounting still correct? | **YES** — priors read once, never refunded, never re-imported |
| 8 | REAL M20 envelopes unchanged? | **YES** — 44 / 30 / 24 / 22 / 23 / 22 |
| 9 | Execution mode resolved before runtime construction? | **YES** |
| 10 | Invalid mode ⇒ `build_runtime` calls = 0? | **YES** — 0 for four invalid values; 1 for each valid one |
| 11 | P2-A source fix still correct? | **YES** — 64 bins, depth 1, 5 blockers |
| 12 | F-11 / F-22 / F-24 intact? | **YES** |
| 13 | Any new P0/P1? | **NO** |
| 14 | Safe for targeted independent re-review? | **YES** |
| 15 | Safe to commit? | **YES, after an independent PASS** |
| 16 | Safe to run the real re-derivation? | **NO** — not until independent PASS **and** commit |

`FULL_VALIDATION_READY`: **NO**. The shipped artifacts are still stale and still
correctly block it.

Nothing was committed or pushed.
