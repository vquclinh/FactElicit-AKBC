# Audit 0052 — Source fixes: depth-2 derivation rule, M7/M20 budget ownership, execution mode

**Date:** 2026-08-08
**Scope:** source and configuration only. Continues from Audit 0051.
**HEAD at audit time:** `b1804646dec3d2343dcf2cf8b277529071b89485` (working tree dirty, uncommitted)
**Python:** 3.14.5

---

## 0. What this audit is and is not

Audit 0051 verified the three real TRAIN-derived calibration artifacts and then
placed the system on **HOLD**: the shipped planner calibration declares
`lookahead_depth = 2` while five of the sixty-four shipped historical bins record
no successor statistics, and Module 21 raises when it ranks an action from one of
them. That audit also recorded a second defect found while investigating the
first — Module 7's core call ceiling was being intersected with Module 20's
calibrated envelope — and a third, an execution-mode mismatch between the VAL
config and the runner.

This milestone resolves all three **in source**. It does not re-derive anything.

| Question | Answer |
|---|---|
| Are the three source/config issues fixed? | **YES** |
| Has the real 134 MB re-derivation been run? | **NO** — explicitly out of scope |
| Are the shipped calibration artifacts changed? | **NO** — byte-identical, read-only throughout |
| Is production readiness final? | **NO** — still `NOT_READY`, fail-closed |
| Was VAL, TEST, or any real model weight touched? | **NO** |

The three artifacts still on disk were derived under the **old** depth rule and
still carry `lookahead_depth = 2`. The corrected rule now yields **1** for that
same package, but only a real re-derivation may change the artifact. Until then
the loader and the readiness gate keep refusing it — which is the intended
behaviour, not a residual bug.

---

## 1. P2-A — the package-wide depth-2 derivation rule

### 1.1 The defect

`derive_planner_calibration` chose the depth with:

```python
lookahead_depth=2 if any(b.successors for b in package.bins) else 1
```

`lookahead_depth` is a property of the **whole package**. `MicroPlanner._lookahead`
looks up the root bin for each ranked action and raises `PlannerError` when that
bin records no successors, and `HistoricalBinPackage.lookup` routes any unmatched
state to the declared fallback — so *any* shipped bin can become that root. One
bin with an observed transition was therefore enough to make the entire package
advertise a depth it could not honour everywhere.

Audit 0051 proved this is a real runtime crash rather than a conservative guard,
by disabling the loader's pre-check **in memory only** and planning against the
real production graph:

```
PlannerError: depth-2 planning needs successor statistics, and the bin for
'M18:REVERSE:andorra' records none
```

The root cause is `any` where the semantics require `all`, compounded by the fact
that terminal-action bins legitimately have nothing after them to observe.

### 1.2 The fix

`src/cover_kbc/controller_calibration/derivation.py` gains
`supports_depth_two(package) -> (bool, reasons)`, and the derivation calls it:

```python
depth_two, depth_reasons = supports_depth_two(package)
...
lookahead_depth=2 if depth_two else 1,
```

`supports_depth_two` checks both places the lookahead can raise, using the real
`HistoricalBinPackage` / `MicroPlanner` semantics:

1. **Every shipped bin carries successors.** Not "some" — every one, because
   `lookup` can route to any of them.
2. **Every successor state bin resolves one level down**, for every action family
   the relation/program-type group ships, via either an exact bin or the declared
   fallback bin. This is the lookahead's *second* raise site ("no historical bin
   matches") and was previously unchecked at derivation time.

It returns a deterministic, sorted, de-duplicated list of blocking bins so the
derivation can record *why* it chose the depth it chose. Two new diagnostics are
emitted: `lookahead_depth_two_supported` and `lookahead_depth_blockers`.

### 1.3 Constraints honoured

| Constraint | Status |
|---|---|
| No fabricated successors | Held — the function only reads what the package ships |
| No runtime silent per-action downgrade | Held — `MicroPlanner._lookahead` still raises; a test asserts the `raise` survives and that no `except PlannerError` exists anywhere in the planner |
| No catch-and-retry at depth 1 | Held — same test |
| No special-casing the five current bins | Held — the rule is stated over the package, not over any relation, state or family name |
| Depth stays within §17's "1–2 step micro-lookahead" | Held — the result is always 1 or 2 |

### 1.4 Result on the real package (read-only)

The corrected rule applied to the shipped 64-bin package:

```
bins: 64   depth-2 supported: False   ->  corrected lookahead_depth = 1

blockers (5):
  countryLandBordersCountry/__fallback__/REVERSE_CHECK                                    (support 67)
  countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK  (support 36)
  countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK  (support 31)
  hasArea/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL        (support 43)
  hasCapacity/program_type=NUMERIC|residual=b2|unresolved_mass=NA/CANDIDATE_FREE_RECALL    (support 57)
```

**Expected corrected depth for the current package: 1.** Confirmed.

These are not sparse bins — they carry 31 to 67 observations each. They have no
successors because they are terminal actions: a reverse check or a candidate-free
recall that ends the chain. Depth 1 is what this package honestly supports, and
§17 permits it.

---

## 2. P1-A — M7 / M20 budget ownership

### 2.1 The defect

`build_plan` computed the query's ceiling as:

```python
hard_calls = _intersect_ceiling(relation, calibration.hard_calls,
                                core_budget.max_calls, ...)   # returned min(...)
```

Two different quantities were being intersected:

* **Module 7 `max_calls`** bounds the **legacy/core controller phase**.
* **Module 20 `hard_calls`** is the **calibrated whole-query envelope** for the
  upgraded Layer-4 / M17 / M18 action space, measured on TRAIN with Layer-4
  precharge non-enforcing — i.e. it describes spend that happened *outside* the
  core ceiling.

Applying the core ceiling to the calibrated envelope replaced every TRAIN
measurement with a number that had never observed the action space it was
gating.

Measured consequence on the real artifacts:

| Relation | M7 core | M20 calibrated | plan **before** | plan **after** |
|---|---:|---:|---:|---:|
| awardWonBy | 12 | 44 | 12 | **44** |
| companyTradesAtStockExchange | 5 | 30 | 5 | **30** |
| countryLandBordersCountry | 4 | 24 | 4 | **24** |
| hasArea | 4 | 22 | 4 | **22** |
| hasCapacity | 4 | 23 | 4 | **23** |
| personHasCityOfDeath | 4 | 22 | 4 | **22** |

One Module 17 action costs four non-cacheable readings (live config: two template
phrasings × two label orders, plus four contextual controls that a warm cache
serves for free). With a 4-call envelope already partly consumed by acquisition,
four of six relations could not fund a single Layer-4 action and Module 21
answered `NO_AFFORDABLE_ACTION`. Worse, `awardWonBy` was handed an envelope of 12
containing a §9.3 protected verification floor of 14 — an envelope no reservation
could ever satisfy.

### 2.2 The fix

Three files, one contract.

**`src/cover_kbc/control/relation_budget.py`** — `_intersect_ceiling` becomes
`_note_envelope`. It returns the calibrated value **unchanged** and records the
core ceiling in the plan's notes so a later reader can see both numbers without
reconstructing either:

```
awardWonBy: calibrated hard_calls 44 exceeds Module 7's core-phase ceiling 12;
the calibrated envelope governs the Layer-4 action space and the core ceiling
governs the core phase
```

**`src/cover_kbc/control/budget_accounting.py`** — `BudgetLedger` gains
`prior_calls` / `prior_tokens`. §16's envelope is whole-query, so physical spend
the query incurred before Layer 4 began belongs *inside* it: the ledger opens with
that spend already committed. It is held in its own field, apart from reservations
and settlements, so it can never be released, settled or refunded. Negative priors
raise `BudgetSchedulerError`.

**`src/cover_kbc/pipeline.py`** — `_budget_ledger_for` feeds the priors from the
counter the pipeline already keeps:

```python
spent = self.query_physical_cost(graph)
ledger = BudgetLedger(result.plan,
                      prior_calls=spent["physical_calls"],
                      prior_tokens=spent["generated_tokens"])
```

`query_physical_cost` differences the runtimes' own totals against this query's
baseline. **No new counter was created.** The ledger is cached per query and both
call sites are inside Layer-4 planning/affordability, so the priors are read
exactly once — before any reservation exists — and every later call is charged
through `reserve`/`settle` instead.

### 2.3 Constraints honoured

| Prohibition | Status |
|---|---|
| Do not raise Module 7's `max_calls` | Held — `pipeline.max_calls_per_query` is still 12 and the per-relation core snapshots are still 12/5/4/4/4/4 |
| Do not reduce M17's four readings | Held — `m17_call_plan` is untouched; a test asserts the live descriptor is still 4 readings + 4 controls |
| Do not remove protected reserves | Held — `awardWonBy`'s verification floor is still 14, and now 14 ≤ 44 |
| Do not edit M20 artifact numbers | Held — artifact SHA256 unchanged, read-only |
| No relation-specific exceptions | Held — the change is one helper with no relation names in it |
| Layer 4 may not bypass M20 | Held — `_affordable` still routes every action through `ledger.reserve` |
| No unaccounted calls | Held — acquisition enters as priors, Layer 4 as reservations |
| No double-charging | Held — priors are read once from a cached ledger built before the first reservation |
| No parallel call counter | Held — reuses `query_physical_cost`, the existing query-scoped counter |

### 2.4 Verified result

```
relation                          M7 core  M20 cal   plan  v.floor
awardWonBy                             12       44     44       14  floor<=ceil=True
companyTradesAtStockExchange            5       30     30        0  floor<=ceil=True
countryLandBordersCountry               4       24     24        0  floor<=ceil=True
hasArea                                 4       22     22        0  floor<=ceil=True
hasCapacity                             4       23     23        0  floor<=ceil=True
personHasCityOfDeath                    4       22     22        0  floor<=ceil=True
```

Prior spend arithmetic, on the real borders plan: `BudgetLedger(plan)` commits 0;
`BudgetLedger(plan, prior_calls=9)` commits exactly 9, leaving 24 − 9 = 15. One
Module 17 reservation then charges exactly the 4 non-cacheable sub-calls; settling
it at that cost charges nothing further; cancelling it refunds the reservation and
leaves the 9 priors intact.

---

## 3. Execution mode

### 3.1 The defect

`configs/experiments/cover_kbc_v2_validation.yaml` declared `pipeline.mode:
staged`, while `scripts/run_cover.py` hardcoded
`config_block["mode"] = ExecutionMode.INTERLEAVED.value`. The config was
therefore decorative: the runner would have run interleaved regardless of what
the file said. The real calibration was measured under **interleaved**.

### 3.2 The fix

* The VAL config now declares `pipeline.mode: interleaved`, matching the
  collection config and therefore the system the bins and envelopes describe.
  The header comment states why.
* `scripts/run_cover.py` gains `resolve_execution_mode(config)`, which reads
  `config["pipeline"]["mode"]`, maps it explicitly onto `ExecutionMode`, defaults
  to interleaved **only** when no pipeline block declares a mode at all, and
  raises `SystemExit` naming the supported modes on anything unrecognised. It is
  called before production activation and before any model is built, so an
  unsupported value stops the run before weights load. The hardcoded override is
  gone, and the resolved mode is echoed as `execution   : interleaved (from config)`.
* Execution mode is now part of the collection-vs-validation profile equivalence
  check in `tests/test_production_activation.py`, alongside the model profile,
  scoring, selection, controller and action-bound checks.

Note that `staged` remains a legal, honoured value — the fix is that the config
decides, not the runner.

---

## 4. Tests

New file `tests/test_production_source_fixes.py` — **48 tests**, all passing. The
real artifacts are used **read-only**, so the assertions are made against the
bytes a production run would actually load; every artifact-dependent test skips
cleanly if the files are absent.

**P2-A (12 tests).** No successors anywhere → depth 1. Successors in some but not
all bins → depth 1 (the exact `any`-vs-`all` defect). Successors in every bin →
depth 2. A terminal high-support bin blocks depth 2. A fallback bin without
successors blocks depth 2. An unresolvable successor bin blocks depth 2 (the
second raise site). The rule is deterministic. The depth is always 1 or 2. No
runtime silent downgrade: `_lookahead` still raises and the planner contains no
`except PlannerError`. The real package resolves to depth 1 with exactly 5 named
blockers, and the derivation source no longer contains `any(b.successors`.

**P1-A (17 tests).** The plan preserves the exact calibrated envelope for all six
relations (parametrised). The core ceiling does not replace it. The calibrated
envelope survives any core ceiling from 1 to 500. A calibrated envelope above the
core ceiling is recorded in the notes, not clipped. `_intersect_ceiling` no longer
exists. `awardWonBy`'s protected floor of 14 now fits inside its ceiling of 44,
and every envelope's cap and floor fit inside the plan's ceiling. Spend classes
and declared reserve purposes survive. The live M17 descriptor is still 4 readings
+ 4 controls. Prior physical spend reduces availability exactly once. A new
Layer-4 reservation charges exactly the non-cacheable sub-calls, and settling does
not charge again. Cache hits cost zero. Prior spend cannot be cancelled away. A
four-reading M17 action is affordable on each of the four previously starved
relations even when the query enters Layer 4 having spent Module 7's *entire* core
ceiling. An action beyond the remaining envelope is denied with
`DENIED_BY_HARD_CAP`. Negative priors are refused. The pipeline reuses
`query_physical_cost` rather than a second counter.

**Execution mode (6 tests).** The VAL config declares interleaved; it matches the
collection config; the runner resolves interleaved and staged correctly; an absent
pipeline block defaults to interleaved; five unsupported values each fail closed
with `SystemExit`; the hardcoded override is gone; the VAL config resolves to the
mode it declares.

### 4.1 Discrimination check

To confirm the P1-A tests are not vacuous, `_note_envelope` was temporarily
reverted **in memory** to `return min(calibrated, core)` and the suite re-run:
**18 of the 48 tests failed**. The fix was then restored from a scratch copy and
the file verified to contain no `min(calibrated, core)` outside its docstring.

The P2-A tests discriminate by construction: they call `supports_depth_two`, which
did not exist before this milestone.

### 4.2 Two pre-existing tests updated

Both were asserting behaviour this milestone deliberately changed or were false
positives caused by it. Neither was deleted.

* `tests/test_relation_budget.py::test_a_relation_can_never_raise_the_global_ceiling`
  asserted `min(calibrated, core)` directly. It is renamed
  `test_the_calibrated_envelope_is_the_ceiling_for_the_layer_4_action_space` and
  now asserts the corrected ownership, including that the core ceiling is still
  *recorded* rather than discarded. A companion test
  `test_no_envelope_may_exceed_the_calibrated_ceiling` preserves the bound that
  genuinely remains: `RelationBudgetCalibration` refuses a class cap above
  `hard_calls`, and every envelope the plan builds is clipped to it.
* `tests/test_relation_budget.py::test_module_20_reads_no_factual_evidence`
  scanned Module 20's source for the raw substring `r_t` and flagged the new
  `prior_tokens` parameter — a physical-call counter, not evidence. The two
  residual terms `R_t`/`r_t` are now matched on identifier boundaries; every other
  forbidden term keeps its original substring matching, so nothing is weakened.

No F-11, F-22 or F-24 test was removed or relaxed. All 60 tests in
`tests/test_production_activation.py` survive (one gains an assertion).

---

## 5. Validation run

| Check | Result |
|---|---|
| `tests/test_production_source_fixes.py` | 48 passed |
| `tests/test_production_activation.py` | 59 passed, 1 skipped |
| `tests/test_real_calibration_artifacts.py` | 17 passed, 1 strict xfail (P2-A artifact, expected) |
| M20/M21/Layer6/derivation suites (11 files) | 506 passed, 1 skipped, 1 xfailed |
| **Full `pytest tests/`** | **3311 passed, 4 skipped, 1 xfailed** |
| Full suite, randomized order | same |
| `pyflakes` on every touched source and test file | clean |
| `git diff -- benchmark/` | empty (0 lines) |
| Full VAL run | **not run** — out of scope |
| TEST split | **not read** |
| Real model weights | **not loaded** |
| Real 134 MB re-derivation | **not run** — out of scope |

The single strict `xfail` is
`test_the_real_artifacts_reach_full_validation_ready`. Its reason string is
updated to state the current position precisely: the derivation rule is fixed and
now yields 1 for this package, but the shipped artifact still carries the 2 it was
derived with, and only a real re-derivation may change that.

---

## 6. Readiness — still fail-closed

Evaluated against the real artifacts and the committed VAL config after all three
fixes:

```
LOADER REFUSED: ProductionCalibrationError
  the planner calibration requests depth-2 lookahead but 5 historical bin(s)
  record no successor statistics ... Module 21 raises when it ranks an action
  from such a bin, so this package would fail mid-run. Either derive it with
  lookahead_depth 1, or derive a history in which every shipped bin observed a
  transition

READINESS: ReadinessState.NOT_READY
  blocker (1 of 1): calibration: the planner calibration requests depth-2
  lookahead but 5 historical bin(s) record no successor statistics ...
```

**This is correct and required.** The source rule is fixed; the artifact is stale.
The system must not become READY until a real re-derivation regenerates it.

---

## 7. Provenance consequence

These source changes alter the derivation. Once committed, the three artifacts
share a single derivation provenance and **all three must be regenerated together**
by the real re-derivation. No mixed-provenance compatibility was implemented and
no provenance equality check was loosened; `SHARED_PROVENANCE_FIELDS` and the
loader's cross-artifact agreement checks are untouched. After the re-derivation,
`calibration_provenance.derivation_repo_sha` and all three
`*_sha256` values in the VAL config must be updated to the new artifacts.

Expected effect of the re-derivation on the numbers: `lookahead_depth` becomes 1;
`α, β, γ, δ, η, κ, τ` and the M20 envelopes are unchanged by these source edits,
but their bytes and hashes will change with the new provenance block.

---

## 8. Files changed in this milestone

**Source**
* `src/cover_kbc/controller_calibration/derivation.py` — `supports_depth_two`, corrected depth rule, two new diagnostics
* `src/cover_kbc/control/relation_budget.py` — `_intersect_ceiling` → `_note_envelope`
* `src/cover_kbc/control/budget_accounting.py` — `BudgetLedger` prior spend
* `src/cover_kbc/pipeline.py` — feed priors from `query_physical_cost`
* `scripts/run_cover.py` — `resolve_execution_mode`, honour the configured mode

**Config**
* `configs/experiments/cover_kbc_v2_validation.yaml` — `pipeline.mode: staged` → `interleaved`, header comment rewritten

**Tests**
* `tests/test_production_source_fixes.py` — new, 48 tests
* `tests/test_relation_budget.py` — two tests corrected as described in §4.2
* `tests/test_production_activation.py` — execution mode added to the profile equivalence check
* `tests/test_real_calibration_artifacts.py` — xfail reason updated

**Not changed:** the three calibration artifacts, `benchmark/`, the frozen model
profile, any prompt, view, or verification-quality setting.

---

## 9. Verdict

**SOURCE FIXES COMPLETE — REAL RE-DERIVATION NOT RUN — PRODUCTION READINESS NOT FINAL**

All three issues carried over from Audit 0051 are resolved in source, with tests
that fail against the previous behaviour. The full suite is green. The system
remains correctly fail-closed at `NOT_READY`.

**The system is NOT `FULL_VALIDATION_READY` and must not be called so.** Reaching
that state requires, in order:

1. An independent review of this milestone returning PASS.
2. A manual commit of these changes.
3. The real 134 MB TRAIN re-derivation at that commit, regenerating all three
   artifacts with `lookahead_depth = 1`.
4. Updating the VAL config's `derivation_repo_sha` and the three `*_sha256` values.
5. Re-running the readiness gate and confirming `FULL_VALIDATION_READY`, with the
   strict `xfail` in `tests/test_real_calibration_artifacts.py` removed
   deliberately at that point.

Nothing was committed or pushed.
