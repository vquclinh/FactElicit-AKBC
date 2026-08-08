# 0050 — Production Activation: F-11, F-22, F-24

**Verdict: HOLD — DO NOT RUN VAL**

Two P1s block the run. Neither is a defect in this milestone's work; both are
conditions that only became observable once Layer 6 was actually switched on,
which is what this milestone was for.

---

## 0. Scope and honesty statement

`COVER_KBC_Technical_Proposal_New.pdf` was re-read before any change (§16
Table 6, §17 the utility and `τ_continue`, §21.2 interface invariants, §22
Algorithm 1). F-11, F-22 and F-24 are implemented and tested.

**The three real calibration artifacts are not present in this environment.**
`configs/calibration/` does not exist, and a filesystem-wide search found only
the scripted-runtime artifacts this session produced in a scratchpad (42 bins,
placeholder provenance) — demonstrably not the real ones (64 bins). Their
declared SHA256s and provenance therefore **could not be verified**, and the
readiness gate on the committed VAL config correctly returns `NOT_READY`
because the files it names are absent.

Everything that does not require those bytes was implemented and verified in
full. Everything that does is reported as unverified rather than assumed.

No VAL or TEST row was read. No model weights were loaded. No training.

---

## 1. HEAD, working tree, benchmark

| item | value |
|---|---|
| HEAD | `b1804646dec3d2343dcf2cf8b277529071b89485` — the stated derivation SHA |
| VAL split | 478 rows, `sha256 90e4f2475e7e69caf9316ffd3b2e0bc4fe2cd428a99027f2abf08c9f88c18d02` — **matches** the stated value exactly |
| TRAIN split | `sha256 cb344aa3…` — matches |
| Tests | **3 246 passed, 3 skipped** (was 3 186) |
| Static | `pyflakes src/ tests/ scripts/` clean, exit 0 |
| `git diff -- benchmark/` | empty; `git status -- benchmark/` empty |

```
 M scripts/run_cover.py
 M src/cover_kbc/control/micro_planner.py
 M src/cover_kbc/control/relation_budget.py
 M src/cover_kbc/controller_calibration/readiness.py
 M tests/test_layer6_integration.py
 M tests/test_micro_planner.py
 M tests/test_relation_budget.py
?? configs/experiments/cover_kbc_v2_validation.yaml
?? src/cover_kbc/controller_calibration/production.py
?? tests/test_production_activation.py
```

---

## 2. Ownership traced before modifying

| concern | canonical owner | reused, not duplicated |
|---|---|---|
| integration mode | `IntegrationMode` | production value already existed; only the wiring was missing |
| pipeline construction | `CoverPipeline` | extended `run_cover.py`; no second pipeline |
| M20 config / loader | `RelationBudgetConfig`, `load_calibrations` | mode extended; loader untouched |
| M21 config / loaders | `MicroPlannerConfig`, `load_history`, `load_planner_calibration` | same |
| Layer 6 | `Layer6Integrator`, `collect_catalog` | supplied, not reimplemented |
| M17/M18 legal actions | `verifiable_targets`, `eligible_checks` | untouched |
| M19 residual | `coverage_gap` | untouched |
| M20 precharge | `CoverPipeline._precharge` → `BudgetLedger.reserve` | untouched |
| M21 ranking/STOP | `MicroPlanner.plan` | untouched |
| evidence seam | `ProductionEvidenceBridge` | untouched |
| M8 output | `selection.finalize` | untouched |
| model registry | `model_blocks`, `build_runtime` | untouched |
| readiness | `controller_calibration/readiness.py` | extended, symmetric with the existing collection gate |

**A finding from the trace.** There are two M21 paths, and only one of them was
broken. `_plan_next_action` (the *executing* seam, reached from
`_select_actions` when `is_production`) already built its own state and ranked
the real M17/M18 catalogue. `_plan_micro_action` (the Layer-6
observability seam) is the one that received an empty list without the
integrator. F-22's description matches the second; the first was already
correct. Both are now supplied.

---

## 3. F-11 — production configuration — **DONE**

`MicroPlannerConfig` and `RelationBudgetConfig` now accept `mode: production`
alongside `shadow`, and nothing else: `_MODES = ("shadow", "production")`. A
"degraded" or "compatibility" mode is refused, because a budget that governs
some calls and not others is the same as no budget.

- **Shadow unchanged** — asserted directly, including that a disabled module
  still builds nothing.
- **Production requires the module enabled** and the artifacts named.
- **No fallback of any kind.** `load_production_calibration` refuses a missing
  file, a synthetic source, an empty history, a hash mismatch, an artifact
  without provenance, and three artifacts that disagree on provenance. Each
  file goes through **its own canonical loader**, which is what refuses
  `SYNTHETIC_TEST`.
- **Integrity check added at the loading boundary**, as the brief allows: two
  new optional config keys (`calibration_sha256`,
  `historical_bins_sha256`, `planner_calibration_sha256`) checked against the
  bytes. No parallel artifact system.
- **Provenance** is cross-checked across all three files on
  `collection_repo_sha`, `derivation_repo_sha`, `train_sha256`,
  `telemetry_sha256`, `derivation_schema_version`, and against the expected
  commits declared in the config.
- **No TRAIN data at runtime.** The loader opens exactly three paths; a test
  makes `load_dataset` raise and the loader still succeeds.

## 4. F-22 — Layer 6 integrator — **DONE**

`run_cover.py` now supplies `Layer6Integrator(planner)`. Measured behaviour on
the real production graph (scripted runtimes, fixture artifacts):

```
round 1  legal actions: 3    (M17 verifiable targets)
round 2  legal actions: 25   (M18 eligible checks)
round 3  legal actions: 28
stop_reason NO_LEGAL_ACTION: never
```

That is the F-22 symptom gone: the planner is handed the owners' real
catalogue. M17 keeps specialist verification; M18 keeps reverse, key-condition,
counterfactual and candidate-free recall. Nothing was faked to make the planner
look busy — the actions come from `verifiable_targets` and `eligible_checks`.

## 5. F-24 — production entrypoint — **DONE**

Extended `scripts/run_cover.py` rather than adding a pipeline. It detects a
production config from the two Layer-6 module modes, runs the readiness gate,
loads the calibration, and builds `CoverPipeline` with
`integration_mode=IntegrationMode.PRODUCTION`, the real scheduler, the real
planner and the integrator. A production config that is not
`FULL_VALIDATION_READY` exits rather than falling back.

`test_the_entrypoint_constructs_production_mode` drives the real `main()` end
to end — real gate, real loader, real assembly — with only the runtimes and the
dataset substituted, and asserts the constructed pipeline really holds
`IntegrationMode.PRODUCTION`, prints `FULL_VALIDATION_READY`, and writes one
prediction row per query in order with a list of objects.

All of M0–M21 are constructed; there is no M22; M8 remains the sole output
owner (asserted); the bridge remains the mutation seam.

## 6. VAL config — **DONE**

`configs/experiments/cover_kbc_v2_validation.yaml`. `split: val`; M9–M21 and
Layer 6 enabled; M20/M21 in production pointing at the three artifact paths
with their declared SHA256s; `calibration_provenance` naming both expected
commits and the VAL identity (478 rows, exact hash — verified).

Model profile, `budget_assertion`, `scoring`, `selection`, `controller` and
`max_control_rounds_per_catalogue` are asserted **equal to the collection
config**, so the calibration is applied to the system it was measured on.

*A defect this test caught in my own work:* the first draft dropped
`controller.rcse.w_instability: 0.8`. That is a silent controller change
between the calibrated system and the validation run. Fixed; the equality test
is what found it.

## 7. Readiness — gate implemented, **not satisfiable here**

`evaluate_validation_readiness` composes `evaluate_readiness` with: split is
`val`; both Layer-6 modules in production; every module M9–M21 plus Layer 6
enabled; the model profile resolvable; and — decisively — the three artifacts
actually **loading**, agreeing on provenance, matching the expected commits,
and covering all six relations from the canonical registry.

With fixture artifacts present it returns `FULL_VALIDATION_READY`
(`test_the_val_config_reaches_full_validation_ready`), and it refuses each of
seven independent mutations (wrong split, either module in shadow, Layer 6 off,
M19/M16/M17 off).

Against the committed config **as shipped**:

```
state: NOT_READY
  BLOCKER M20 relation budget calibration: configured path does not exist
  BLOCKER M21 historical bins: configured path does not exist
  BLOCKER M21 planner calibration: configured path does not exist
  ok      split: val
  ok      M20 and M21 both declare production mode
```

Correct and fail-closed. It will pass once the real files are placed at
`configs/calibration/`.

---

## 8. Findings

### P1-A — the core per-query budget starves Layer 4 on four of six relations

`build_plan` intersects the TRAIN-derived envelope with Module 7's own
per-query budget. Measured, per relation:

| relation | core `max_calls` | one M17 action reserves | available after protected reserve | Module 21 |
|---|---|---|---|---|
| awardWonBy | 12 | 4 | 3 | **ACTION selected** |
| companyTradesAtStockExchange | 5 | 4 | 0 | **ACTION selected** |
| countryLandBordersCountry | 4 | 4 | 3 | **STOP — NO_AFFORDABLE_ACTION** |
| hasArea | 4 | 4 | 3 | **STOP** |
| hasCapacity | 4 | 4 | 3 | **STOP** |
| personHasCityOfDeath | 4 | 4 | 3 | **STOP** |

A Module 17 action reserves four non-cacheable readings (two phrasings × two
label orders; the four controls are `CACHE_HIT` and cost nothing). Table 6's
protected special reserve withholds one call from any other purpose, exactly as
§9.3 requires. Four requested against three available is denied.

Every component is behaving as designed. The problem is the **interaction**:
during collection `_precharge` returned `True` unconditionally, so Layer-4
actions spent entirely outside the core budget — the telemetry records ~19
Layer-4 calls on a borders query whose core budget is 4. The derived envelopes
therefore describe spend that production will not permit.

Not a fixture artifact: the real artifact's borders numbers (`hard_calls 24`,
`verification_cap 14`) are intersected down to the same core 4.

**Consequence if VAL is run now:** on four of six relations Module 21 selects
nothing, no M17/M18 verification runs, and the upgraded path contributes
nothing to those rows — a validation run that looks activated and is not.

**Not fixed here, deliberately.** The candidate remedies each change something
the brief protects: raising `max_calls_per_query` alters a controller setting
the calibration was measured under; changing what `build_plan` intersects is an
ownership decision about whether Layer-4 competes with the core loop for one
budget; reducing M17's readings would change verification quality. This is the
architectural conflict the brief says to report rather than paper over. Pinned
by `test_the_core_budget_currently_starves_layer_4_on_four_relations` so it
cannot drift silently.

### P1-B — the real artifacts are absent and unverifiable here

Their SHA256s, provenance, relation coverage, bin count and depth-2 successor
completeness are all unverified. The gate refuses, correctly, but nothing in
this milestone can confirm the real files satisfy it.

### P2-A — depth-2 lookahead needs successors in *every* bin it ranks

`MicroPlanner._lookahead` raises `PlannerError` when a ranked action's bin
records no successor statistics. The derivation sets `lookahead_depth = 2` if
**any** bin has successors, and attaches successors only to bins that observed
a transition — so a package can ask for depth 2 while some bin cannot support
it, and then fail at an arbitrary row hours into a run.

Now refused at load time with an actionable message, rather than mid-run
(`test_depth_two_without_successor_statistics_is_refused`). Depth 1 is
unaffected. **The real artifact declares depth 2 and must be checked for this
before the run** — the loader will do it automatically.

### P3
- `run_cover.py` forces `ExecutionMode.INTERLEAVED` while the VAL config
  declares `staged`; pre-existing and documented in that runner.
- The M20 `scope` note and the derived caps describe Layer-4 spend only
  (Audit 0047 §3) — unchanged, still true.
- Three tests were updated because their assertions encoded "production is
  refused", which is now false by design. One (`test_module_20s_table_6_registry_is_unchanged`)
  was a `git status --porcelain` freeze check and was converted to assert
  Table 6's actual content — the same anti-pattern Audits 0042 and 0044 already
  converted twice.

**No P0.**

---

## 9. Proposal compliance

| requirement | status |
|---|---|
| §16 Table 6 qualitative ownership | unchanged; asserted field by field for all six relations |
| §9.3 hard verification reservation | unchanged and **active** — it is precisely what withholds the protected call in P1-A |
| §17 six-term utility | untouched; `utility()` unchanged |
| §17 strict `U > τ_continue` | asserted; `>=` asserted absent from the module |
| §17 1–2 step lookahead | configured depth honoured; depth 2 now validated at load |
| C-02 (`ΔH` inert) | untouched; γ = 0.0 carried from the artifact, no entropy manufactured |
| §21.2 specialists never bypass the graph | unchanged; bridge is still the seam |
| M8 sole output owner | asserted on the production graph |
| ≤32B published parameters | 28 671 226 368, audit passes; both ids and revisions exact |
| closed book / no training | asserted by grep over the production modules and by making `load_dataset` fatal during a production run |

---

## 10. Tests

`tests/test_production_activation.py` — **60 tests**, covering all 24 required
points: production configs accepted (1, 2); shadow unchanged (3); synthetic,
missing, empty-history, hash-mismatch, provenance-mismatch and
mixed-derivation refusals (4, 5, 6); all six relations (7); canonical history
and planner loaders (8, 9); integrator supplied (10); non-empty legal actions
(11); Module 20 screening before execution and a denial preventing it (12);
strict `>` (13); sparse-bin fallback (14); the entrypoint constructing
PRODUCTION (15); `FULL_VALIDATION_READY` (16); `split: val` (17); the 478-row
VAL identity (18); no TRAIN dependency (19); no split read at all (20);
benchmark unchanged (21); no alternate model (22); parameter budget (23); M8
sole owner (24).

Plus the end-to-end scripted production integration test required by the brief:
the real graph, the real artifacts' shape, scripted runtimes, and **a real
Module 21 ACTION decision** whose selected value clears `τ_continue`.

| check | result |
|---|---|
| `pytest tests/test_production_activation.py` | **60 passed** |
| `pytest -q` | **3 246 passed, 3 skipped** |
| `pyflakes src/ tests/ scripts/` | clean |
| `git diff -- benchmark/` | empty |

---

## 11. Status

| item | state |
|---|---|
| 1. F-11 production config | **YES** |
| 2. F-22 Layer 6 integrator | **YES** |
| 3. F-24 production entrypoint | **YES** |
| 4. Real M20 active | **NO** — code path complete and tested; the real artifact is absent here |
| 5. Real M21 active | **NO** — same |
| 6. VAL config | **YES** |
| 7. `FULL_VALIDATION_READY` | **NO** — gate implemented and demonstrated; refuses as shipped because the artifacts are absent |
| 8. Scripted production pipeline tested | **YES** |
| 9. Real-weight production smoke | **NOT RUN** |
| 10. Full VAL 478/478 | **NOT RUN** |

---

## 12. Blockers before VAL

1. **Place the three real artifacts** at `configs/calibration/` and confirm the
   gate reaches `FULL_VALIDATION_READY`. The declared SHA256s and both
   provenance commits are already in the config and will be enforced.
2. **Resolve P1-A.** As it stands, four of six relations will run with Layer 4
   entirely denied. This needs an owner decision, not a quiet edit.
3. **Confirm P2-A** on the real history: depth 2 with a bin lacking successors
   will now be refused at load. If it trips, the choice is a depth-1 planner
   calibration or a history whose shipped bins all observed a transition.
4. Real-weight smoke on the frozen profile before the 478-row run.

## 13. Verdict

> **HOLD — DO NOT RUN VAL**

F-11, F-22 and F-24 are implemented, wired into the canonical owners, and
tested — including a real Module 21 decision over a real legal-action list on
the real production graph. The VAL config exists, is byte-faithful to the
calibrated profile, and the readiness gate is real enough to refuse it today.

The hold is not about that work. It is that the real artifacts cannot be seen
from here, and that switching Module 20 on revealed a genuine interaction:
the core per-query budget is smaller than one Module 17 action on four of six
relations, so activation as it stands would suppress the very Layer-4
behaviour the calibration was derived from. Running VAL now would spend a
478-row session on a system that is production in name on two relations and
shadow in effect on four.

---

*No commit, no push. `benchmark/` untouched. No VAL or TEST row read; no model
weights loaded; no training of any kind.*
