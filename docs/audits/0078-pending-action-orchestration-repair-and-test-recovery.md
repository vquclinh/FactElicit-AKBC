# Audit 0078 - Pending-Action Orchestration Repair And Clean TEST Recovery

## Scope

Incident repair. The calibrated V3 TEST run produced 475 rows, 39 of which were
not answers but holes left by an orchestration invariant failure, and the batch
exited 0.

Base SHA (HEAD at start of this milestone):

`b9eef10b6b9ba1333f7278e4fcc0b9b672f66357`

Frozen TEST submission source, unchanged and not rewritten:

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

Local incident artifacts (read-only evidence, never mutated):

`outputs/v3_test_16f60fb1_20260810T160048Z/`

No TEST gold, web, RAG, external KB or external corpus was used. No model was
fine-tuned. No subject→answer mapping exists anywhere in this milestone. Audit
0077's V3.1 checkpoint was not modified.

## 1. Incident Facts, Verified Locally

Every figure below was recomputed from the local artifacts, not taken on trust.

| Fact | Value |
|---|---|
| canonical TEST rows | `475` |
| prediction rows | `475` |
| predictions SHA256 | `8a97a5e0696f0f4c77ace3af88725bb90f568ee0587bc07fe0f6e7b75c649f08` ✓ matches |
| errors | `39` |
| error relations | `{companyTradesAtStockExchange: 39}` |
| error kinds | `{PendingActionNotConsumed: 39}` |
| error signature | `{RUN_FACET \| enumerator \| 1 calls remain: 39}` — identical for all 39 |
| error rows with empty prediction | `39` |
| error rows with non-empty prediction | `0` |
| stock rows | `100` |
| empty stock rows | `39` |
| error identities == empty stock identities | **`True`** |
| empty stock rows not in errors | `0` |
| prediction order == canonical order | `True` |
| execution mode | `interleaved` |

Empty predictions by relation across the run: `hasArea 71`, `hasCapacity 25`,
`companyTradesAtStockExchange 39`, `countryLandBordersCountry 11`,
`personHasCityOfDeath 98`. Only the stock 39 are orchestration failures; the
rest are ordinary abstentions.

Forensics: `outputs/v3_test_pending_action_forensics/`
(`failed_rows.csv`, `failed_rows.jsonl`, `control_flow_summary.json`,
`incident_summary.md`, `recovery_manifest.json`, `SHA256SUMS.txt`).

`SHA256SUMS.txt` SHA256: `bfde586a591d204bbec375483af8140aa93a10ab0881fddbde46f79e418ed1f6`

Per-failed-row control flow, uniform across all 39:

| Field | Value (all 39) |
|---|---|
| enumerator calls recorded | `3` |
| verifier calls recorded | `0` |
| candidates at failure | `0` |
| M21 planner rounds | `0` — M21 never ran |
| owner dispatch happened | `False` |
| generation happened | `False` |
| evidence mutation happened | `False` |
| stopped/empty reason | `pipeline_error` |

## 2. Root Cause

**File:** `src/cover_kbc/pipeline.py`
**Function:** `CoverPipeline.verify_graph`
**Branch:** the Phase-B controller was handed
`frozenset({ModelRole.VERIFIER, ModelRole.NONE})` **unconditionally**.

Three facts that are each harmless alone:

1. **The role restriction is a staged-mode fact.** In staged execution only the
   verifier is resident, so an enumerator-owned action genuinely cannot run in
   Phase B and is persisted for the orchestrator to dispatch after a role swap.
   In **interleaved** execution both runtimes are resident
   (`self.runtime`, `self.verifier_runtime`) and there is no role to swap to.

2. **The pended action has no consumer in interleaved mode.**
   `_controlled_phase` pends any action whose `model_role` is outside
   `allowed_roles` (`pipeline.py`, the `pended_for_role` branch). Only
   `CoverPipeline.resume` consumes an enumerator-role pending action, and
   `pipeline.resume` is called **solely** by `scripts/run_staged.py` — asserted
   by `test_only_run_staged_consumes_an_enumerator_pending_action`. The
   interleaved driver is `run` → `enumerate_query` → `verify_graph` →
   `decide_graph`, with no resume step.

3. **Only stock reaches Phase B with budget left.** Contract call caps:

   | Relation | cap |
   |---|---:|
   | companyTradesAtStockExchange | **5** |
   | countryLandBordersCountry | 4 |
   | hasArea | 4 |
   | hasCapacity | 4 |
   | personHasCityOfDeath | 4 |
   | awardWonBy | 12 |

   Phase A spends 4. Every relation whose cap is 4 arrives at Phase B with
   `budget.exhausted` and breaks before the controller can choose anything.
   Stock arrives with exactly **one** affordable call, so its Phase-B controller
   runs — and with zero candidates the only legal actions are `RUN_VIEW`,
   `RUN_FACET` and `STOP`, all enumerator-owned or terminal.

The controller selected `RUN_FACET`, `_controlled_phase` pended it and broke,
and `decide_graph` refused to finalize over executable work. `run` caught the
exception, wrote a `PIPELINE_ERROR` empty row, and continued.

**Which controller.** `RUN_FACET` is a **V2 controller** `ActionType`
(`src/cover_kbc/controller.py:54`, `ACTION_ROLE[RUN_FACET] = ENUMERATOR` at
line 73). M21 logged **zero** planner rounds on every failed row, because M21
runs inside `decide_graph` *after* the pending check. **M20 and M21 are not
implicated in this bug at all.**

### Why "1 calls remain" was the crash condition, not the cause

`decide_graph` tolerates a pending action *only* when the budget is exhausted,
in which case it records an explicit STOP with reason `hard budget exhausted`,
clears the action, and finalizes. With a call still affordable the state is
genuinely illegal and the invariant fires. Had the budget been exhausted, the
same pend would have been absorbed silently. The leftover call is what made the
bug visible; the missing consumer is what made it a bug.

## 3. The Fix

`CoverPipeline._phase_b_roles`, consulted by `verify_graph`:

```python
if self.config.mode is ExecutionMode.STAGED:
    return frozenset({ModelRole.VERIFIER, ModelRole.NONE})
return frozenset({ModelRole.ENUMERATOR, ModelRole.VERIFIER, ModelRole.NONE})
```

Residency, not policy: Phase B may execute against whichever runtimes are
actually loaded.

### Why this cannot change a row that did not fail

`choose_action` is deliberately **not** role-filtered — its own docstring says
filtering there "would make `pending_action` unreachable and quietly downgrade
staged execution". The action selected is therefore identical before and after.
The only behaviour `allowed_roles` governs is whether a selected enumerator-role
action is **executed** or **pended**. Every row that pended one raised. Rows that
never pended are untouched by construction, not by measurement.

### Interleaved vs staged

| Mode | Before | After |
|---|---|---|
| interleaved | enumerator action pended, no consumer, `PendingActionNotConsumed` | executed if affordable, refused if not; nothing pending |
| staged | enumerator action pended for `resume` | **unchanged** — still pended, still consumed by `run_staged.py` |

`test_staged_mode_still_pends_enumerator_work_for_the_orchestrator` pins the
staged contract.

## 4. Budget Boundaries

Measured on the repaired source with scripted runtimes:

| Remaining calls | Phase-B physical calls | Pending after | Finalizes |
|---:|---:|---|---|
| 0 | `0` | none | yes, `no_candidate_generated` |
| 1 | `1` (1-call action) or `0` (2-call action refused) | none | yes |
| 2 | `2` | none | yes |

Audit §7's requirement — *a 1-call action at a 1-call boundary MUST be dispatched
exactly once* — is pinned by
`test_a_one_call_action_at_a_one_call_boundary_is_dispatched_exactly_once`:
exactly one physical call, budget moves 4→5 of 5, nothing pending, no overrun. A
2-call action at a 1-call boundary is refused as unaffordable and **never**
pended, which is the "explicitly blocked" outcome §7 allows.

Also verified: no negative budget, no call exceeding the cap, no duplicate
generation record, no duplicate evidence attribution, no repeated controller-log
step, and zero physical calls at a zero budget.

## 5. The Invariant Is Preserved, Not Deleted

`PendingActionNotConsumed` is untouched and still fires on a genuinely illegal
state (`test_pending_action_not_consumed_still_fires_on_a_genuinely_illegal_state`),
and the pre-existing budget-exhausted cancellation path still records an explicit
`hard budget exhausted` STOP with the abandoned action
(`test_exhausted_budget_still_cancels_a_pending_action_explicitly`). Nothing is
caught and ignored.

## 6. Reproduction

Zero-model, `ScriptedRuntime` only. The state is a stock query whose acquisition
produced nothing, with an optional facet unexplored and exactly one affordable
call. On the frozen source it raises, byte-for-byte, the incident's message:

```
PendingActionNotConsumed: .../companyTradesAtStockExchange: the controller
selected RUN_FACET needing the enumerator role and 1 calls remain.
```

Proof the tests are load-bearing: with the fix stashed, **2 tests fail**; with it
applied, **all 42 pass**.

## 7. Historical TRAIN Audit (§10) — Mandatory Finding

The 39/100 figure appears in both runs, so the question was taken seriously
rather than dismissed.

| | TRAIN collection | Failed TEST |
|---|---|---|
| rows | 477 | 475 |
| stock rows | 100 | 100 |
| stock empty | **39** | **39** |
| `PendingActionNotConsumed` | **0** | 39 |
| rows with `pipeline_error` | **0** | 39 |
| `unresolved_failed_rows` (accounting) | **0** | n/a |
| stock empty `empty_reason` | `no_candidate_generated` ×39 | `pipeline_error` ×39 |
| stock empty `stopped_reason` | `unexplored optional facet` ×39 | `pipeline_error` ×39 |
| calls used on those rows | `4` | fallback rows report `0` |
| execution mode | interleaved | interleaved |
| pipeline config | identical (12 calls, 12 steps, 6 verifications) | identical |

Both ran interleaved with identical budgets, so mode and config do **not**
explain the difference. The explanation is the driver:

```
scripts/run_train_calibration_collection.py:
    graph      = pipeline.enumerate_query(query)
    prediction = pipeline.decide_graph(graph)      # verify_graph is NEVER called
```

The collection runner contains **zero** occurrences of `verify_graph`
(`test_the_train_collection_runner_never_runs_phase_b`). Phase B is the only
place a pending action is created. **A corpus collected that way cannot contain
one.**

**Finding: the 39 TRAIN stock empties are category A — genuine
no-candidate/model outcomes.** They are the same *population* as TEST's 39
(stock queries whose enumeration yielded nothing and left an optional facet
unexplored), reached through a path that ended in a legitimate abstention rather
than a crash. The coincidence is real and explained; it is not the same failure.

## 8. Calibration Consequence

### **CALIBRATION_UNAFFECTED**

Two separate claims, deliberately kept apart:

**Controller semantics unchanged.** The repair touches one expression in
`verify_graph`. It does not alter M21 ranking, utility coefficients, calibration
bins, tau, legal actions, action prices, M20 budgets, relation profiles, prompts
or target classes. `test_the_repair_touches_no_planner_or_scheduler_module`
fails if any file under `src/cover_kbc/control/`, the derivation modules, or any
`micro_planner`/`relation_budget` file is modified by this milestone.

**Calibration corpus still representative.** This is the claim that mattered,
and it rests on §7's finding rather than on the first claim. The authoritative
TRAIN corpus was collected through a driver that never runs Phase B, so no
action in it was ever pended, abandoned or left unconsumed. `accounting.json`
records `unresolved_failed_rows: 0`, `failed_attempts: 0`,
`rows_completed: 477`, and no telemetry row carries `pipeline_error`. The M21
action-effect statistics therefore describe a run in which every selected action
was consumed — which is exactly what fixed production now does.

Calibration artifacts verified byte-identical and pinned by test:

| Artifact | SHA256 |
|---|---|
| `configs/calibration/v3/m20_relation_budget.json` | `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29` |
| `configs/calibration/v3/m21_historical_bins.json` | `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675` |
| `configs/calibration/v3/m21_planner_calibration.json` | `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6` |
| `configs/calibration/v3/calibration_provenance.json` | `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d` |
| `configs/calibration/m20_relation_budget.json` | `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68` |
| `configs/calibration/m21_historical_bins.json` | `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071` |
| `configs/calibration/m21_planner_calibration.json` | `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` |

**One caveat, stated because it is a real asymmetry rather than a risk this
milestone creates.** The TRAIN corpus was collected *without* Phase B; production
runs *with* it. That difference predates this incident and is unchanged by the
repair — the repair narrows it, by making Phase B behave in interleaved
production the way the collection path already implicitly assumed. It is
recorded here as a known property of the calibration provenance, not as a
consequence of this bug.

## 9. Submission-Readiness Hardening

New module `src/cover_kbc/run_accounting.py`, wired into `scripts/run_cover.py`.

The distinction it draws: an empty prediction the system *decided on* is a
legitimate answer (the evaluator scores precision 1.0 for it); an empty
prediction produced by an exception is a missing row wearing an answer's shape.

Exposed in `run_accounting.json` and printed by the runner:
`total_queries`, `prediction_rows`, `successful_queries`, `failed_queries`,
`query_errors` (by exception class), `unresolved_invariant_errors`,
`pipeline_error_rows`, `error_rows_match_pipeline_error_rows`,
`failed_identities`.

Against the real incident numbers:

```
total_queries 475 | prediction_rows 475 | successful 436 | failed 39
query_errors {"PendingActionNotConsumed": 39} | unresolved_invariant_errors 39
verdict: SUBMISSION_NOT_READY
  BLOCKER: 39 unresolved orchestration invariant error(s)
           ['PendingActionNotConsumed']: these rows were abandoned mid-query
           and their empty predictions are not answers
```

A clean run reports `SUBMISSION_READY`. A full row count alone no longer clears
the gate (`test_a_full_row_count_alone_does_not_make_a_run_ready`).

**Scope.** Only blind splits turn a refusal into a non-zero exit (`3`). TRAIN
collection and diagnostic workflows keep their behaviour, because those runs are
*supposed* to be able to contain a failed row and study it — breaking them to
protect a submission would trade one silent failure for another.

## 10. Targeted vs Full Rerun (§15)

### **TARGETED_RECOVERY_SAFE** — 39 rows

Cross-query state, enumerated from source:

| State | Scope | Cross-query? |
|---|---|---|
| `EvidenceGraph` | fresh per `enumerate_query` | no |
| `Budget` | rebuilt per query from the contract | no |
| `ActionHistory` | keyed `self._v3_action_histories[(subject, relation, row)]` | no |
| M20 `RelationBudgetScheduler` | `schedule()` builds plan + ledger per query from static calibration | no |
| M21 micro planner | static calibration + per-query hypothesis graph | no |
| RNG / seeds | `self.seed + run_id`; no global RNG in pipeline or engine | no |
| telemetry lists | accumulate, observation only | no |
| **`ContextualCalibrator._controls`** | **per pipeline instance, shared across queries** | **yes** |

Exactly one coupling exists, and it is provably unreachable for these rows:

* the calibrator is consulted only inside `_planned_neural_cost`, and only for
  `VERIFY` / `ADVERSARIAL_VERIFY`;
* all 39 rows reached the Phase-B decision with **0 candidates**;
* with 0 candidates the legal action set is `['RUN_FACET', 'RUN_VIEW', 'STOP']` —
  **no VERIFY action is legal**, verified by executing `legal_actions` against
  that exact state;
* so no calibrator lookup can occur, and cache warmth cannot change the outcome.

Decoding is greedy and deterministic with per-view seeds, so re-running a query
from the start reproduces its Phase A exactly. Rerunning the 39 alone therefore
does **not** require replaying the preceding 436.

## 11. Recovery Tooling

**`scripts/build_test_recovery_manifest.py`** — derives identities from the
failed run's own `errors.json`. No subject list exists in source. Output
(`recovery_manifest.json`) carries identities, canonical indices, error text and
provenance hashes, and **contains no predicted values**: `39` identities, all
`companyTradesAtStockExchange`, all `PendingActionNotConsumed`, all originally
empty. The token `ObjectEntities` does not appear anywhere in the file.

**`scripts/run_cover.py --recovery-manifest`** — re-runs exactly those
identities, each from the start. It refuses a manifest carrying predictions, a
duplicate identity, or an identity absent from the split. No partial state is
resumed. Recovery writes a new run directory.

**`scripts/merge_test_recovery.py`** — mechanical replacement. Enforced, not
assumed: base row count equals canonical; failed set derived from `errors.json`;
recovered set equals failed set exactly (no missing, extra or duplicate); every
replaced base row was empty; every non-failed row compared field-by-field and
unchanged; output in canonical order with the three official fields; refuses to
write over its own input. Emits a provenance file with every input hash.

Merge dry-run against the real base (with placeholder recovery values supplied
by the test, never by the tool): `475` merged, `39` replaced, `436` preserved
byte-identical.

## 12. No Claude-Authored Answers (§17)

**No `ObjectEntities` value in any recovered TEST prediction was produced,
selected or modified using Claude's factual knowledge.**

What was done: source was fixed, traces were read, manifests were built from the
run's own error log, tooling was written, and machine-generated predictions are
validated and merged. What was not done: no stock exchange was named, inferred,
chosen between, or edited. The merge copies values verbatim from one of two
prediction files and has no other source of values — asserted by
`test_merge_copies_values_and_never_authors_them`, which checks every emitted
value against the set present in the inputs, and by
`test_no_gold_or_world_knowledge_path_exists_in_the_recovery_tools`, which fails
if any exchange or company literal appears in the tools.

The 39 recovered answers do not exist yet. They will be produced by
Mistral-Small-3.2-24B and Qwen3.5-4B on the recovery run described below.

## 13. Tests

New: `tests/test_pending_action_orchestration.py` (**42**),
`tests/test_test_recovery_tooling.py` (**25**).

Covering: RUN_FACET ownership; the stock-only budget asymmetry; the resume
consumer gap; the TRAIN collection driver never running Phase B; the incident
reproduction at its exact signature; a 1-call action dispatched exactly once at
a 1-call boundary; 0/1/2-call boundaries; no negative budget, no overrun, no
double dispatch, no duplicate evidence, no repeated controller-log step; the
invariant still firing on an illegal state; the budget-exhausted cancellation
path; staged mode still pending; all six relation program types finalizing;
border behaviour unchanged; calibration hashes byte-identical; the repair
touching no planner module; V3.1 configs unaffected; the incident artifacts'
hash; recovery manifest identity set; merge replace/preserve/order/count/schema;
missing, extra and duplicate recovery all rejected; refusal to overwrite an
answer; still-empty recovery reported honestly; submission readiness on the real
incident numbers; collection workflows not broken; no world-knowledge path.

```bash
python -m pytest tests/ -q -p no:randomly   # 3894 passed, 4 skipped
python -m pytest tests/ -q                  # 3894 passed, 4 skipped
python -m pyflakes src/ tests/ scripts/     # clean
git diff --check                            # clean
```

Audit 0077's baseline was `3827 passed, 4 skipped`; the difference is exactly the
67 new tests.

## 14. V3.1 Continuation Checkpoint (§19)

Restated unchanged; no V3.1 work was done or modified in this milestone.

- **SAFE_CORE**: ready (`configs/experiments/cover_kbc_v3_1_safe_core_test.yaml`)
- **SAFE_FULL**: ready (`configs/experiments/cover_kbc_v3_1_safe_full_test.yaml`)
- **Class-B live prompts**: wired (audit 0077 §4)
- **Targeted Class-B diagnostics**: prepared, not real-model tested
- **Future priority**: capacity diagnostic first

## 15. Future TRAIN / M21 Plan (§20)

Path **A applies**, since §7 found the authoritative corpus uncontaminated:

1. repair the baseline TEST (this milestone + the recovery run below);
2. resume V3.1 SAFE submissions;
3. run Class-B targeted TRAIN diagnostics;
4. promote winning Class-B features;
5. run a new full TRAIN collection under fixed orchestration;
6. derive a new V3.1 M21 from it.

Path B is not triggered. The existing calibration provenance stays valid and is
not overwritten.

**M20 decision status: `m20_policy = undecided`**, unchanged from audit 0077. M20
is not re-derived automatically. A future clean collection's coverage and spend
evidence decides inherit-vs-re-estimate; changing prompts does not by itself
justify re-estimating a budget scheduler.

## Verdicts

**ORCHESTRATION_REPAIR_READY** — root cause identified in one branch, repaired
in one expression, reproduced by a zero-model regression test that fails on the
frozen source and passes after, with the invariant preserved and all budget
boundaries pinned.

**TARGETED_RECOVERY_SAFE** — 39 rows. The only cross-query state is the
calibrator control cache, and it is provably unreachable for rows that reach the
decision point with no candidates.

**CALIBRATION_UNAFFECTED** — the repair touches no planner or scheduler module,
and the authoritative TRAIN corpus was collected through a driver that never
creates a pending action.

## Recommended GPU Commands

**1. Recovery run** — the 39 identities, each from the start, under fixed source:

```bash
python scripts/build_test_recovery_manifest.py \
  --run-dir outputs/v3_test_16f60fb1_20260810T160048Z/run \
  --canonical benchmark/data/test.jsonl \
  --output outputs/v3_test_pending_action_forensics/recovery_manifest.json

python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_test.yaml \
  --recovery-manifest outputs/v3_test_pending_action_forensics/recovery_manifest.json \
  --output-dir outputs/v3_test_recovery_39 \
  --no-eval
```

The run must report `submission  : SUBMISSION_READY` and write no `errors.json`.
If any row still fails, stop: the repair did not hold and merging would hide it.

**2. Merge** into a clean 475-row submission:

```bash
python scripts/merge_test_recovery.py \
  --base-predictions outputs/v3_test_16f60fb1_20260810T160048Z/run/predictions.jsonl \
  --base-errors      outputs/v3_test_16f60fb1_20260810T160048Z/run/errors.json \
  --recovered-predictions outputs/v3_test_recovery_39/predictions.jsonl \
  --canonical-test   benchmark/data/test.jsonl \
  --output           outputs/v3_test_recovered/predictions.jsonl
```

Expect `merged rows 475`, `rows replaced 39`, `rows preserved 436`.

**3. Then resume V3.1** — SAFE_CORE submission, then the capacity diagnostic
(audit 0077 §"Recommended GPU Execution Order").

### A note on the alternative

A full clean TEST rerun (~475 rows, ~3 hours at the observed 24 s/row) is also
defensible and gives single-commit provenance for all 475 rows rather than a
two-source merge. Targeted recovery is proven safe and is ~13× cheaper; the full
rerun buys provenance simplicity. That trade is a judgement call, and both paths
are supported by the tooling here.
