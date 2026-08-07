# 0042 — Post-Codex TRAIN Collection Remediation Audit

**Verdict: PASS — SAFE TO START FULL 477-ROW TRAIN COLLECTION**

*(One operational precondition is stated in §12 and is not a code blocker.)*

---

## 0. What this audit is, and what it is not

This audit re-reads `COVER_KBC_Technical_Proposal_New.pdf` and
[`0041-independent-codex-full-repository-pre-train-review.md`](0041-independent-codex-full-repository-pre-train-review.md),
then inspects the **current** executable source and drives a scripted collection
through the **committed** TRAIN config, reading every artefact back off disk.
It does not rely on pytest alone: every claim in §11 is backed by observed
output reproduced verbatim.

Four things are kept apart throughout, because conflating them is how a
milestone declares itself finished:

| concern | verdict |
|---|---|
| **Module architecture correctness** (M0–M21 against the proposal) | unchanged and sound — Audit 0041 found it so, and this milestone did not redesign it |
| **Collection instrumentation correctness** (does telemetry record what happened) | **rebuilt** — this was the failure |
| **Runtime durability** (multi-hour run, resume, containment) | **repaired and tested** |
| **Calibration sufficiency** (could M20/M21 be derived without a rerun) | **now provable, and proved by a gate the run must pass** |
| **Post-calibration production readiness** | **explicitly deferred**, unchanged, still blocked |

---

## 1. Review identity

| item | value |
|---|---|
| Base commit | `2512fe000c49c57aaf2ed4fd6b7d1f921f2ea2ba` (`main`) |
| Working tree | modified, uncommitted (this milestone) |
| Proposal | `COVER_KBC_Technical_Proposal_New.pdf`, 26 pp. |
| Prior review | `docs/audits/0041-*` (independent, treated as findings to verify) |
| Date | 2026-08-07 |
| Tests | `3053 passed, 3 skipped` |
| Static | `python -m pyflakes src/ tests/ scripts/` — clean |
| Benchmark | `git diff -- benchmark/` empty |

No model weights were loaded. No VAL or TEST split was read. No factual
`ObjectEntities` value was inspected. The 477-row collection was **not** run.

---

## 2. Reproduction of the Audit-0041 findings

Every finding this milestone was asked to close was first re-derived against
the source at the base commit. All eleven reproduced; none was a false report.

| ID | reproduced? | how |
|---|---|---|
| F-01 | **yes** | all five committed configs declared `split: val`; `require_split` refuses before any row. With the split corrected, `parametric_retrieval.enabled: false` → M12–M15 `None` → M16 `None` → zero action records → empty telemetry, exit 0, "PASS" |
| F-02 | **yes** | `hasattr(CoverageGapState, "coverage_gap")` → `False`; `hasattr(..., "gap")` → `False`. Second bug confirmed on Python 3.14.5: `str(ResidualComponentName.NOVELTY_RATE)` → `'ResidualComponentName.NOVELTY_RATE'` ≠ `'novelty_rate'` |
| F-03 | **yes** | `pre_state` and `post_state` both built from `_state_features(pipeline, graph)` at the same moment, after the row; `candidates_*`, `redundancy`, `verifier_outcome`, `prompt_tokens`, `target_class`, `action_id`, `reserved_class` never populated |
| F-04 | **yes** | unconditional `break` at the end of the `while True` loop capped each query at one M17 + one M18 action; `remaining`/`executed_ids` unreachable past one iteration |
| F-05 | **yes** | fail → resume → fail → resume produced two run directories (rows 0–1 in one, 2–3 in the other) and a checkpoint naming a third id with no directory |
| F-06 | **yes** | no runtime carried a cumulative `prompt_tokens`; `counters.charge(...)` never passed one; live progress and `accounting.json` printed 0 |
| F-07 | **yes** | `build_consensus_engine` called without `relations`/`available_specialists` (guard vacuous); no per-row exception handling — one row aborted all 477 |
| F-09 | **yes** | `evaluate_readiness` referenced only from tests |
| F-10 | **yes** | `note_families` never called; a 4-row probe that surfaced one family printed `required action-family coverage: PASS` and exited 0 |
| F-12 | **yes** | `operation_id` contained `id(action)`, e.g. `m18:1:1:140144940782416` |
| F-13 | **yes** | emitted `program_type: "ProgramType.NUMERIC"`; `PlannerStateSnapshot.program_type` is `"NUMERIC"`, so no bin could ever match |

**Two further defects were found during remediation that Audit 0041 did not
report**, because its probes covered only numeric TRAIN rows. Both are recorded
as new findings in §10 and both are fixed:

- **N-01 (P0-class):** `ProductionEvidenceBridge` raised
  `ProductionBridgeError: Module 18 check 'CANDIDATE_FREE_RECALL' declared
  unknown independence group 'M18_CANDIDATE_FREE_RECALL'` on the first
  entity-relation candidate a structural check resolved against. `IndependenceGroup`
  had no M18 members. **This would have killed the 477-row run on the first
  `awardWonBy` row.**
- **N-02 (P1-class):** `m18_actions` gave two counterfactual checks on one
  target with different contract-declared near-miss classes the *same*
  `action_id`, so the planner silently dropped one and two telemetry records
  collided on one identity.

---

## 3. F-01 — the committed TRAIN collection config

`configs/experiments/cover_kbc_v2_train_collection.yaml` (new, 250 lines).

- `experiment.split: train`.
- **Model profile byte-identical to the frozen target** —
  `test_the_committed_train_config_keeps_the_frozen_model_profile` asserts
  `train["model_profile"] == target["model_profile"]`, so collection measures
  the system validation will run, not a cheaper relative. Mistral-Small-3.2-24B
  `95a6d26c…` + Qwen3.5-4B `851bf6e8…`, 28,671,226,368 ≤ 32B, `nf4`.
- Prompts, decoding, scoring, selection and controller settings identical to
  the target.
- M9, M10, M11, M12, M13, M14, M15, M16, M17, M18, Layer-4, M19 **enabled**.
- M20, M21, Layer-6 **disabled** with `calibration_file: null` /
  `historical_bins: null` / `planner_calibration: null`. No fake artifact, no
  `SYNTHETIC_TEST` reference. Selection stays with `TrainCollectionPolicy`.
- `pipeline.max_control_rounds_per_catalogue: 3` — the explicit bound (§7).

The file states its three deviations from the frozen target in its own header so
no reader has to diff.

---

## 4. F-02 — Module 19 state extraction

`ControlStateFeatures.from_coverage_gap` (`controller_calibration/telemetry.py`)
now owns the read, and the runner has no state-building code at all:

- reads `state.residual` — Module 19's own `CoverageGapComponents`;
- matches components on `component.name.value`, the canonical enum value;
- **raises `TelemetryError` on a shape it does not recognise.** There is no
  alias fallback and no `getattr` default: *"a zeroed control state is worse
  than a crash — it calibrates."*
- `state is None` (M19 never ran) yields `measured=False`, an honest *absent*
  state that `ActionTelemetryRecord` then refuses to attach to an executed
  action.
- `available_components` records which of §15's five were measurable, so a
  component reading `0.0` is distinguishable from one that never ran — §15 is
  explicit that unavailable is not zero.

`test_a_wrong_state_shape_raises_rather_than_zeroing`,
`test_component_names_are_matched_on_the_canonical_enum_value`,
`test_an_unavailable_component_is_not_reported_as_a_measured_zero`.

---

## 5. F-03 — real per-action state and outcome

`CoverPipeline.execute_action` is now the sole capture point. It records, at the
moment each is true:

```
before:  physical_snapshot, control_state(graph), candidate-evidence signature
         precharge (Module 20)                       ← still before any runtime
execute: exactly one action, through its owner
after:   integrate → ProductionEvidenceBridge → Module 19 refresh
         physical_snapshot, control_state(graph), candidate-evidence diff
```

The record carries `state_before`, `state_after`, `delta_residual`,
`delta_entropy`, the measured `cost` (including prompt tokens and the role
split), the candidate `effect`, the `BridgeReport`, and the owner's canonical
`projection`. The runner **transcribes**; it builds nothing.
`test_the_runner_contains_no_entropy_mathematics` now asserts the runner
contains no `ControlStateFeatures(` construction at all.

Populated for real, from execution and integration results:

| field | source |
|---|---|
| `candidates_added` / `_supported` / `_contradicted` | graph diff over `raw_support_count`, `contradiction_count` and attached verdicts |
| `candidates_named` | `BridgeReport.discovered_not_inserted` — a §14 probe may name, never mint |
| `redundancy` | held ÷ (held + named). **`None`** when the action had no candidate surface, so a measured `0.0` stays distinct |
| `verifier_outcome` | Module 17's own `argmax_label`. Read from the result M17 returned, not inferred from the graph — a numeric-*cluster* verdict never reaches a candidate, and a graph-derived reading would be empty for exactly the two relations §8 exists for. Empty for M18 actions, which have no verdict to give |
| `structural_outcome` | Module 18's own signed reading, unflattened: `ALTERNATE_RECOVERED` is neither support nor contradiction |
| `prompt_tokens` / `generated_tokens` / `enumerator_calls` / `verifier_calls` | measured from the runtimes, role partition asserted to sum |
| `target_class`, `action_id`, `spend_class`, `reserve_purpose` | the Layer-6 projection (§6) |

An unexecuted legal action gets a pre-state, `post_state=None`, zero cost and no
effect — and the schema **refuses** a record that claims otherwise
(`test_a_candidate_effect_on_an_unexecuted_action`).

`test_one_actions_post_state_is_the_next_actions_pre_state` asserts the identity
§3 of the brief required.

---

## 6. F-12 / F-13 — canonical identity and program type

- `program_type_value()` was promoted from private to public in `pipeline.py`
  precisely because *anything that persists a program type needs it*. The
  runner imports the public name.
- `operation_id` = `row:round:<owner action_id>`; `action_id` is the owner's
  own `M17:SPECIALIST_VERIFY:<target>` / `M18:<kind>:<target>[:<class>]`.
  No `id()`, no address.
- `project_action()` is the single projection point, one entry at a time — which
  also removed a latent positional-`zip` misalignment in `_plan_next_action`,
  where the adapters' filtered output was paired against the unfiltered
  catalogue.
- The schema refuses an executed action with no `action_id`.
- The sufficiency validator rejects an `operation_id` that looks like an
  address and a non-canonical `program_type`/`action_family`.

---

## 7. F-04 — real multi-round collection, explicitly bounded

The unconditional `break` is gone. The loop now:

1. re-reads the owner's catalogue,
2. drops entries whose canonical `action_id` already ran **this query**
   (identity, not object address, so it survives the reload),
3. asks the collection policy,
4. executes exactly one action through the canonical seam,
5. integrates → bridges → refreshes Module 19 and `H`,
6. repeats, up to `PipelineConfig.max_control_rounds_per_catalogue`.

**The bound (documented, §23 of the brief):** `3` rounds **per catalogue kind**
per query — up to 3 Module 17 actions and 3 Module 18 actions, so a query
contributes at most 6 action transitions. Per *kind* rather than shared, because
Module 18 publishes four mechanism families and Module 17 one: a shared pool
would let a long M17 target list exhaust the allowance before any M18 family was
reached — the family-coverage failure the round-robin exists to prevent. It stops
early when the policy returns nothing, when Module 20 refuses, or when the
catalogue is exhausted. It is never "until nothing is legal".

Two supporting defects had to be fixed for multi-round to be correct:

- **Results were replaced, not merged.** `verify_specialist_targets` and
  `execute_bidirectional_checks` overwrote the query's entry, so round 2 erased
  round 1's typed results from the Layer-4 projection — undoing evidence the
  bridge had already applied. Both now merge by owner identity.
- **The policy's round-robin restarted every round.** It is stateless within a
  query, and the controller re-asks with a fresh catalogue each round, so a
  family with many entries won every round and a short one was never reached.
  `TrainCollectionPolicy.begin_query()` now holds the rotation position, and the
  final catalogue-order sort — which silently undid the balancing one line after
  achieving it — was removed.

Observed before the fix, on a borders row: `CANDIDATE_FREE_RECALL,
COUNTERFACTUAL_VERIFY, COUNTERFACTUAL_VERIFY`. After: `CANDIDATE_FREE_RECALL,
COUNTERFACTUAL_VERIFY, REVERSE_CHECK`.

---

## 8. F-05 / F-07 / F-06 / F-09 / F-10 in brief

**F-05 — one directory, one identity, any number of resumes.** On resume the
runner *adopts* the checkpoint's `run_id` as its own instead of minting a fresh
one and pointing `out_dir` at the old directory. A checkpoint with no recorded
`run_id` is refused rather than guessed at. Tested with fail → resume + fail →
resume: one directory, one `run_id` in every telemetry record, cumulative
coverage and accounting, no duplicate identity, four predictions for four rows.

**F-07 — an explicit exception boundary.** `FATAL_ERRORS` = `MemoryError`,
`OSError`, `AccountingInvariantError` (new, typed, raised by `physical_delta`),
`TelemetryError`, `ProductionBridgeError`; plus any non-`Exception`
`BaseException` and anything CUDA-OOM by name. Everything else is a **row-local**
failure: the row commits nothing, is recorded in `failed_rows` with its error and
the calls it burned, and the run continues. `build_consensus_engine` is now
given the run's relations and specialist availability, so its guard is real.

**F-08 / §18 — truthful failed-row reporting, fail-closed.** `rows_failed`,
`failed_rows`, `failures[]` and `failed_row_calls` are all populated and
reported. Failed-row spend is kept **out** of the committed totals — those
describe the work backing the telemetry — and reported separately. Per §18 the
gate fails closed: **any** row failure prevents a success verdict.

**F-06 — prompt tokens.** `BaseRuntime.prompt_tokens` is a cumulative counter
fed by `charge_prompt_tokens()` from the backend's own per-call figure (never
re-tokenised in a runner). Charged in `generate`, `_score_next_token`, and once
per label in `_score_sequence` (each is a real forward pass over the prompt).
Surfaced through `physical_snapshot` → `physical_delta` → per-action telemetry →
`RunCounters` → `accounting.json` → live progress.

**F-09 — the readiness gate is real.** `evaluate_collection_readiness()` composes
`evaluate_readiness` with three collection-specific requirements: split is TRAIN,
all twelve upgraded modules enabled, M20/M21/Layer-6 off, model profile
resolvable. The runner calls it **before building any runtime**, and refuses with
`CollectionError` (exit 2). Logic lives in `readiness.py`, not duplicated in the
runner. The frozen target config is refused; the committed TRAIN config passes.

**F-10 — the family vocabulary is declared.** `required_families(("m17","m18"))`
reads Layer 6's own `M17_FAMILIES` / `M18_FAMILIES`, never a string list in the
runner. `FamilyStatus` distinguishes four states — `OBSERVED`,
`LEGAL_BUT_UNEXECUTED`, `ABSENT_FROM_TRAIN`, `NEVER_SURFACED` — and
`integrity_ok()` now fails on the last two conditions, not only the second.
A `--limit` slice downgrades `NEVER_SURFACED` to a printed note, because a
slice covers one or two relations and cannot be a coverage sample; the full run
treats it as the wiring failure it is.

---

## 9. The exit gate and the sufficiency validator

Reaching the end is no longer success. Before exit 0 the runner validates, from
what is on disk:

- run not aborted, zero failed rows, all rows completed;
- prediction count equals committed row count;
- no legal-but-unexecuted family; no required-but-never-surfaced family;
- `evaluate_sufficiency(records)` returns `ok`.

`controller_calibration/sufficiency.py` (new) is structural, never statistical:
it asks whether a field was *instrumented*, not whether its value is
interesting, and it respects measured-zero versus absent-measurement throughout.
It names every §17 estimate and §16 quantity with the telemetry it is derived
from (`M21_REQUIREMENTS`, `M20_REQUIREMENTS`, `M21_BIN_KEY`), and refuses:
empty telemetry, mixed schema versions, a repr `program_type`, a non-canonical
family, a missing `action_id`, an address-shaped `operation_id`, an unmeasured
state, a state with no available §15 component, a missing successor chain, a
missing Module 17 verdict or Module 18 reading, an uninstrumented redundancy, a
missing `spend_class`, a claimed `reserved_class`, zero prompt tokens on a
charged action, and a role partition that does not sum.

---

## 10. Findings status — F-01 … F-24 (Audit 0041) and N-01 … N-02 (new)

| ID | status | note |
|---|---|---|
| **F-01** | **FIXED** | `configs/experiments/cover_kbc_v2_train_collection.yaml`, gated by real readiness |
| **F-02** | **FIXED** | `ControlStateFeatures.from_coverage_gap`; raises on shape mismatch |
| **F-03** | **FIXED** | state and effect captured by the seam; every outcome field populated |
| **F-04** | **FIXED** | bounded multi-round loop; results merged; rotation held per query |
| **F-05** | **FIXED** | resume adopts the checkpoint's `run_id`; multi-resume tested |
| **F-06** | **FIXED** | cumulative prompt tokens end to end |
| **F-07** | **FIXED** | typed fatal set; row-local failures contained and reported |
| **F-08** | **FIXED** | `rows_failed` / `failed_rows` / `failures` / `failed_row_calls` populated |
| **F-09** | **FIXED** | `evaluate_collection_readiness` called before any runtime is built |
| **F-10** | **FIXED** | vocabulary from Layer 6; four-way `FamilyStatus`; gate fails on never-surfaced |
| **F-11** | **DEFERRED-WITH-JUSTIFICATION** | per-module `mode:` still cannot express `production`. Not a collection blocker: collection runs on `IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY`, which the pipeline owns. **Must be fixed before FULL VALIDATION** |
| **F-12** | **FIXED** | owner-published `action_id`; identical across two separate processes |
| **F-13** | **FIXED** | public `program_type_value()`; bin lookup proved in §11 D |
| **F-14** | **STILL-OPEN (P3)** | `_planned_neural_cost` still assumes one call per template. Unreachable with the current profile's verified single-token A/B/C labels, and Module 17 reads runtime counters rather than the plan. No effect on collection |
| **F-15** | **STILL-OPEN (P3)** | bridge still calls `graph._attach`. Undeclared coupling, no behavioural effect |
| **F-16** | **VERIFIED-NONISSUE** | R_t renormalisation is a documented, defensible instantiation of §15, which supplies no weights. To be stated in the paper, not changed |
| **F-17** | **FIXED (incidentally)** | `control_state` now feeds `control_entropy` through the same query path; the divergence needed `optional_views_available` to differ, which the committed config makes impossible (`enable_active_controller: true`) |
| **F-18** | **STILL-OPEN (P3)** | M20 still schedules twice per query in production. Non-neural, deterministic, unreachable during collection (M20 disabled) |
| **F-19** | **FIXED** | one stable `run_id` across every resume |
| **F-20** | **FIXED** | a `round_line` is printed per executed action with its family and cost |
| **F-21** | **FIXED** | the `id(other) is not id(action)` no-op was removed with the loop rewrite |
| **F-22** | **DEFERRED-WITH-JUSTIFICATION** | `layer6_integrator` still unsupplied. Correct for collection (M21 must not select); **blocker before FULL VALIDATION** |
| **F-23** | **STILL-OPEN (P3)** | `has_second_model`, `_minimum_neural_cost`, `integrate_layer4` still zero-caller. Cosmetic |
| **F-24** | **DEFERRED-WITH-JUSTIFICATION** | no calibrated `IntegrationMode.PRODUCTION` entrypoint. Out of scope by instruction; **blocker before FULL VALIDATION** |
| **N-01** | **FIXED** | four `M18_*` members added to `IndependenceGroup`. Deliberately their own groups, not aliases of an acquisition family — mapping a reverse check onto `REVERSE_ALTERNATE` would let a structural verification inflate acquisition support, the double counting §12.1 forbids. No relation contract declares them, so `m(o)` and `q(o) = g(o)/m(o)` are unchanged |
| **N-02** | **FIXED** | the contract-declared near-miss class is now part of the M18 `action_id` and becomes the action's `facet_id` / `target_class` — §14's counterfactual poses a different question per class, so two classes are two actions |

**Zero unresolved P0. Zero unresolved P1.** The four `STILL-OPEN` items are all
P3 with no semantic effect on collection or on later calibration, for the reasons
given in each row.

---

## 11. Required evidence (§27), observed

Scripted collection through the **committed** config, one TRAIN row per relation
(rows 0, 100, 200, 210, 310, 377), offline runtimes injected in place of the
frozen weights. Runner **exit code 0**.

### A. Non-degenerate Module 19 state

```json
{"residual": 0.25, "novelty_rate": 0.0, "singleton_ratio": 0.0,
 "facet_gap": 0.0, "disagreement": 0.0, "unresolved_mass": 1.0,
 "measured": true,
 "available_components": ["novelty_rate","singleton_ratio","facet_gap","unresolved_mass"]}
```

Read from `CoverageGapState.residual`; `disagreement` reads 0.0 *and* is absent
from `available_components`, so the unmeasured component is not a measured zero.

### B. A real per-action transition

```
hasArea SPECIALIST_VERIFY round=1
  pre.residual =0.250000  post.residual=0.000000  deltaR=+0.250000
  pre.entropy  =0.918296  post.entropy =0.918296  deltaH=+0.000000
  pre_state != post_state: True
  actions with non-zero deltaR: 10/30
```

ΔR is a *reduction*-positive, matching §17's `+β·ΔR̂`. ΔH is a measured zero
here (this action changed no candidate's inclusion state) and is non-zero
elsewhere in the run — the two are distinguishable.

### C. Candidate-effect fields populated

```
awardWonBy SPECIALIST_VERIFY     contradicted=['albert einstein']            redundancy=1.0 verdict='INVALID'  structural=''
awardWonBy SPECIALIST_VERIFY     contradicted=['albert einstein','marie curie'] redundancy=1.0 verdict='INVALID' structural=''
awardWonBy CANDIDATE_FREE_RECALL supported=['albert einstein','marie curie']  redundancy=1.0 verdict=''         structural='TARGET_RECALLED'
```

`verdict` is empty for the Module 18 action, which has no verdict to give.

### D. `program_type` is what `HistoricalBinPackage.lookup` consumes

A package was built keyed on the emitted `program_type` and every executed record
resolved a bin:

```
30/30 executed records resolved a bin keyed on their own program_type;
values seen = ['LARGE_OPEN_SET', 'NULL_SINGLE', 'NUMERIC', 'SMALL_SET']
```

### E. Deterministic identity across two process images

Two separate `python` invocations, diffed:

```
IDENTICAL across two separate processes
["M17:SPECIALIST_VERIFY:0", "M18:CANDIDATE_FREE_RECALL",
 "M18:COUNTERFACTUAL:0:hn0", "M18:COUNTERFACTUAL:0:hn1", …,
 "M17:SPECIALIST_VERIFY:albert einstein", …]
```

Note `:hn0` / `:hn1` — the N-02 fix making two near-miss classes two actions.

### F. Prompt tokens

```
telemetry prompt tokens = 17533
accounting.json         = prompt_tokens=27235 generated_tokens=220
                          physical=151 (enum=80 + verify=71)
```

Telemetry counts Layer-4 actions; accounting counts the whole run including
acquisition — the two differ for the right reason, and the role partition sums.

### G. Successor transitions

```
24 transition(s)
hasArea: r1 SPECIALIST_VERIFY (dR=+0.2500) -> r2 CANDIDATE_FREE_RECALL (dR=+0.0000)
         post(a)==pre(b): True
```

### H. The committed config reached the M11–M19 stack

```
readiness=CALIBRATION_COLLECTION_READY  mode=train_calibration_collection_only
families: CANDIDATE_FREE_RECALL=OBSERVED, COUNTERFACTUAL_VERIFY=OBSERVED,
          REVERSE_CHECK=OBSERVED, SPECIALIST_VERIFY=OBSERVED
relations observed: all six
```

### I. Coverage fails when a required family is never offered

Observed from the runner before the `--limit` downgrade was added:

```
BLOCKER action family REVERSE_CHECK was required but no catalogue ever offered it
        - a wiring failure, not a dataset fact
```

and pinned by `test_a_required_family_never_offered_fails_integrity` plus
`test_the_four_coverage_states_are_distinguished`.

### J. fail → resume → fail → resume stays in one directory

`test_every_resume_stays_in_one_run_directory`,
`test_one_stable_run_id_across_every_resume`,
`test_accounting_stays_cumulative_across_both_resumes`,
`test_checkpoint_references_the_existing_run_directory` — all through the real
`main()`, all artefacts read back off disk.

### K. The sufficiency validator accepts the complete output

```
calibration sufficiency: PASS
  ok  schema train-telemetry-v2 throughout
  ok  program_type is the canonical ProgramType value
  ok  action_family is the canonical ActionFamily value
  ok  every executed action carries its owner's action_id
  ok  every executed action has a measured pre/post state
  ok  §15 components are recorded with their availability
  ok  redundancy is recorded whenever the action had a candidate surface
  ok  Module 17 verdicts (or their errors) are recorded
  ok  Module 18 structural readings (or their errors) are recorded
  ok  24 successor transition(s) recorded, so successor state probabilities are derivable
  ok  every executed action carries its owner's spend_class
  ok  reserved_class is empty: collection asserts no reservation it did not make
  ok  prompt-token accounting is live (17533 tokens over 30 charged action(s))
  ok  the role partition sums on every executed action
```

---

## 12. Calibration sufficiency — the go/no-go question

### M21 (§17)

| estimate | derivable? | from |
|---|---|---|
| `expected_verified_gain` | **yes** | `candidates_added` / `candidates_supported` / `candidates_named`, joined to gold offline |
| `expected_fp` | **yes** | same identities, joined to gold offline |
| `expected_delta_r` | **yes** | `pre_state.residual − post_state.residual`; observed non-zero (§11 B) |
| `expected_delta_h` | **yes** | `pre_state.entropy − post_state.entropy`, sign consistent with §17 and `historical_bins` |
| `expected_cost` | **yes** | `physical_calls`, `prompt_tokens`, `generated_tokens`, role-split |
| `expected_redundancy` | **yes** | `redundancy`, with `None` reserved for "no candidate surface" |
| successor state probabilities | **yes** | 24 observed transitions; `post(a) == pre(b)` asserted |

Bin key `relation / program_type / state_bin / family / target_class`: all five
recorded, `program_type` canonical (§11 D), `state_bin` derivable from the five
§15 components plus `available_components`, `target_class` populated.

### M20 (§16, §9.3)

Observed grouping from the same run:

```
awardWonBy                   {VERIFICATION: 14, DISCOVERY: 1, tokens: 12, prompt: 3123}
companyTradesAtStockExchange {VERIFICATION: 14, DISCOVERY: 1, tokens: 15, prompt: 3252}
countryLandBordersCountry    {VERIFICATION: 18, DISCOVERY: 1, tokens:  9, prompt: 4678}
hasArea                      {VERIFICATION: 10, DISCOVERY: 1, tokens:  3, prompt: 2164}
hasCapacity                  {VERIFICATION: 10, DISCOVERY: 1, tokens:  3, prompt: 2177}
personHasCityOfDeath         {VERIFICATION: 10, DISCOVERY: 1, tokens:  3, prompt: 2139,
                              reserve:CANDIDATE_FREE: 1}
```

`hard_calls`, `hard_generated_tokens`, `discovery_cap`, `verification_cap`,
`verification_reserve` and the special-reserve sizes are all derivable from
per-relation, per-`spend_class`, per-`reserve_purpose` observed spend. **No
value is derived here** — this milestone guarantees the observations exist.

`reserved_class` stays empty throughout and the validator *rejects* a record
that claims one: collection runs before any calibrated ledger and reserves
nothing. The owner's declaration travels under `spend_class` /
`reserve_purpose`, which is what offline derivation groups by.

**No quantity was found that would require rerunning a model.**

### One operational precondition

`src/cover_kbc/models/base.py` and `src/cover_kbc/models/huggingface.py` changed
(additively: three `charge_prompt_tokens()` calls and one counter). The audited
real-weight smoke (Audits 0014/0034) therefore no longer covers the exact code
that will run. The change cannot alter control flow and is covered by scripted
tests, so it is **not a code blocker** — but the repository's own discipline and
§18 of the proposal require a real-weight smoke per profile, so:

> **Run `scripts/real_model_smoke.py` once on the target profile before starting
> the 477-row collection.** It is one generation call and one scoring call.

This is stated as an operational step, not a P0/P1 finding. The two git-status
freeze tests that would have flagged it were converted to assert the behavioural
contract instead, because a clean working tree cannot distinguish an intentional
change from a regression — the anti-pattern Audit 0041 §34 named.

---

## 13. What was deliberately **not** done

- No M0–M21 module logic was redesigned. The bridge, consensus, specialists,
  planner and scheduler keep their audited semantics.
- No offline M20/M21 calibration was derived.
- No VAL or TEST run, no real weights, no 477-row run.
- Post-calibration production activation (F-11, F-22, F-24) is untouched and
  remains a later milestone.
- Unrelated P3 findings were left alone.

---

## 14. Blockers before the full 477-row TRAIN collection

**None.** All P0 and P1 findings are FIXED; the two new defects found during
remediation are FIXED. One operational step (§12): run the real-weight smoke.

## 15. Blockers before FULL VALIDATION (unchanged, still open)

1. Real TRAIN-derived M20 (`RelationBudgetCalibration`) and M21
   (`HistoricalBinPackage` + `PlannerCalibration`) artifacts — this collection
   produces them.
2. **F-24** — a production entrypoint constructing the pipeline with
   `IntegrationMode.PRODUCTION`.
3. **F-11** — module configs able to express `production`;
   `MicroPlannerConfig` and `RelationBudgetConfig` currently raise on any
   non-`shadow` mode.
4. **F-22** — `layer6_integrator` supplied, or M21 always returns
   `STOP/NO_LEGAL_ACTION`.
5. A validation config (`split: val`, M11–M21 enabled, both artifacts declared),
   gated by `evaluate_readiness` returning `FULL_VALIDATION_READY`.

`FULL_VALIDATION_READY` is **not** claimed.

---

## 16. Local checks

| check | result |
|---|---|
| `python -m pytest -q` | **3053 passed, 3 skipped** |
| `python -m pyflakes src/ tests/ scripts/` | clean |
| `git diff -- benchmark/` | empty |
| scripted collection, committed config, six relations | exit 0, sufficiency PASS |
| fail → resume → fail → resume | one directory, one `run_id`, no duplicates |
| two-process identity determinism | identical |

---

## 17. Verdict

> ## PASS — SAFE TO START FULL 477-ROW TRAIN COLLECTION


Zero unresolved P0. Zero unresolved P1. Every collection-affecting P2 from
Audit 0041 is FIXED. The remaining P3s are argued in §10 not to threaten the
telemetry or the later calibration. The collection output is provably sufficient
to derive both M20 and M21 without another inference session — and the run now
*refuses to call itself successful* unless that remains true, which is the part
that matters: the previous failure was not that the telemetry was wrong, but
that nothing checked.

`FULL_VALIDATION_READY` is not claimed, and post-calibration production
activation remains a later milestone.
