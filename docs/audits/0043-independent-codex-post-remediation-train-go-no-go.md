# 0043 — Independent Codex Post-Remediation TRAIN GO/NO-GO

**Verdict: PASS — SAFE TO START PRE-TRAIN REAL-WEIGHT SMOKE**

---

## Statement of independence

- **Audit 0042 was not trusted as evidence.** Its findings table, its §11 observed
  output and its verdict were treated as *claims to falsify*, not as facts. Every
  status in §4 below was re-derived from the current executable source and from
  read-only probes that drove the real `main()`. Where my measurements disagree
  with Audit 0042 I say so and give the measurement (§4 F-17, §10 ΔH, §21 C-05).
- **The current uncommitted remediation was independently inspected.** The review
  is of the working tree, not of `HEAD`. `git diff` (3 964 lines across 22 tracked
  files) plus the four untracked additions were read in full.
- **No production, configuration, test, notebook or benchmark file was modified by
  Codex.** The only repository change is the creation of this file. Every probe
  ran in a scratchpad outside the repository.

Authoritative contract: `COVER_KBC_Technical_Proposal_New.pdf`, read in full
(21 pages). Prior reviews `docs/audits/0041-*` and `0042-*` read in full.

No model weights were loaded. No VAL or TEST split was read. The 477-row
collection was not run. No factual `ObjectEntities` value was inspected — the
only benchmark fields read were `Relation` (to locate one row per relation) and,
through the runner, `SubjectEntity`.

---

## 1. Review identity and working-tree scope

| item | value |
|---|---|
| HEAD | `2512fe000c49c57aaf2ed4fd6b7d1f921f2ea2ba` (`main`) |
| Working tree | **modified, uncommitted** — the remediation under review |
| `git diff --stat` | 22 files, +2 359 / −451 |
| Untracked additions | 4 (1 config, 1 module, 1 test, 2 audits) |
| `git diff -- benchmark/` | empty; benchmark still single commit `b607ae1` |
| Review date | 2026-08-07 |
| Python | 3.14.5 |
| Excluded by instruction | real weights, full TRAIN, VAL, TEST, training, gold values |

---

## 2. Proposal sections rechecked

§2.1–2.3 (closed book, ≤32B published parameters, no training); §4 (M0–M21
layering); §7.2 + Evidence hygiene; §8.2/8.3 (relative-δ clustering,
`UNKNOWN ≠ contradiction`); §9.2/9.3 (atomic support, hard verification
reserve); §10.3 (three null-evidence classes); §12.1 (`q_g = max`, φ(o));
§13/13.1 (per-relation verifier contracts, label-order controls); §14 (four
structural mechanisms, candidate-free recall pays `X`); §15 (`R_t` and its five
components); §16 (relation budget, reserve by class, precharge before every
neural call, values calibrated on TRAIN); §17 (six-term utility, strict
`τ_continue`, relation-specific historical bins, depth ≤ 2); §18 (exact model
id + revision + published totals); §21.2 (interface invariants); §22
(Algorithm 1's bounded action loop).

---

## 3. Remediation diff inventory

Modified (tracked):

| file | Δ | what changed |
|---|---|---|
| `scripts/run_train_calibration_collection.py` | 470 | readiness gate, resume identity, row-failure containment, exit gate, telemetry transcription |
| `src/cover_kbc/pipeline.py` | 506 | `execute_action` state/effect capture, multi-round loop, `project_action`, `control_state`, prompt tokens in `physical_snapshot`, `AccountingInvariantError`, M17/M18 result merging, public `program_type_value` |
| `src/cover_kbc/controller_calibration/telemetry.py` | 229 | schema v2, `from_coverage_gap`, `successor_transitions`, stricter `__post_init__` |
| `src/cover_kbc/controller_calibration/collection_policy.py` | 173 | per-query rotation state, `required_families`, four-way `FamilyStatus`, ledger restore |
| `src/cover_kbc/controller_calibration/readiness.py` | 129 | `evaluate_collection_readiness` |
| `src/cover_kbc/control/action_catalog.py` | 57 | `M17_FAMILIES`/`M18_FAMILIES`, `action_family_for`, near-miss class in the M18 `action_id`/`facet_id` |
| `src/cover_kbc/types.py` | 19 | four `M18_*` `IndependenceGroup` members (N-01) |
| `src/cover_kbc/models/{base,huggingface,offline}.py` | 45 | cumulative `prompt_tokens` counter + three charge sites |
| `src/cover_kbc/controller_calibration/{progress,__init__}.py` | 19 | `failed_row_calls`, exports |
| 10 test files | 1 163 | see §31 |

Added (untracked): `configs/experiments/cover_kbc_v2_train_collection.yaml` (309),
`src/cover_kbc/controller_calibration/sufficiency.py` (324),
`tests/test_calibration_sufficiency.py`, and audits 0041/0042.

---

## 4. F-01 … F-24 independent status matrix

Derived from current source and the probes in §31–§32, not from Audit 0042.

| ID | Sev | Independent status | Evidence |
|---|---|---|---|
| F-01 | P1 | **FIXED** | `cover_kbc_v2_train_collection.yaml` exists, is the **only** committed profile that passes readiness (probe 8, all 6 configs), and the real runner consumes it unmodified (probes 1/4/7/15) |
| F-02 | P0 | **FIXED** | `ControlStateFeatures.from_coverage_gap` reads `state.residual` and `component.name.value`; the Audit-0041 `.coverage_gap` shape now **raises** (probe 14); 8 distinct residual values observed, none degenerate (probe 2) |
| F-03 | P0 | **FIXED** (with C-01) | state captured at the seam; `post(a) == pre(b)` on **24/24** transitions; ΔR ≠ 0 on 10/30 executed actions; candidate effect measured by graph diff. See C-01 for one mis-attributed field |
| F-04 | P2 | **FIXED** | unconditional `break` gone; `for _ in range(max_control_rounds_per_catalogue)`; 4–6 executed actions per query observed across all six relations |
| F-05 | P1 | **FIXED** | fresh → fail → resume → fail → resume → fail → resume → complete → resume-when-complete: **one** directory, **one** `run_id` in all 221 records, cumulative accounting **byte-equal** to the uninterrupted run (probe 4) |
| F-06 | P2 | **FIXED** | arithmetic reconciled exactly: 1 generate + 4 score = 5 calls / 40 prompt tokens for an 8-token prompt; single-role profile does not double count (probe 19) |
| F-07 | P2 | **FIXED** | typed `FATAL_ERRORS`; row-local failure commits nothing, is recorded, run continues (probe 7); `build_consensus_engine` now receives relations + specialist availability |
| F-08 | P3 | **FIXED** (with C-03) | `rows_failed`/`failed_rows`/`failures`/`failed_row_calls` all populated and truthful at the moment of failure |
| F-09 | P2 | **FIXED** | gate fires **before** any runtime is built — an exploding `build_runtime` is never reached for the VAL target (probe 9) |
| F-10 | P2 | **FIXED** | vocabulary from `M17_FAMILIES`/`M18_FAMILIES`; a required family no catalogue offered fails a full run with `NEVER_SURFACED` and exit 1 (probe 15) |
| F-11 | P2 | **DEFERRED-WITH-JUSTIFICATION** | `micro_planner.py:472` / `relation_budget.py:341` still raise on `mode != "shadow"`; both modules disabled during collection, which runs on `IntegrationMode`. Genuinely not a collection blocker |
| F-12 | P2 | **FIXED** | full telemetry (minus `run_id`) **byte-identical** across two separate processes; 221 identical `operation_id`s (probe 10) |
| F-13 | P1 | **FIXED** | `program_type_value()`; observed values exactly `{LARGE_OPEN_SET, NULL_SINGLE, NUMERIC, SMALL_SET}` (probe 2) |
| F-14 | P3 | **STILL-OPEN** | `_planned_neural_cost` unchanged; unreachable with the profile's verified single-token A/B/C |
| F-15 | P3 | **STILL-OPEN** | `production_bridge.py:265` still calls `graph._attach` |
| F-16 | P3 | **VERIFIED-NONISSUE** | `missingness.py` still renormalises over available components; defensible instantiation, must be stated in the paper |
| F-17 | P3 | **STILL-OPEN — Audit 0042's "FIXED (incidentally)" is wrong** | `pipeline.py:2204` still calls `mean_inclusion_uncertainty(graph.active_candidates(), graph.contract)` with **no** `self.config.scoring`. The code is unchanged. Behaviourally identical under the committed config (`enable_active_controller: true`), so it stays P3 — but the label was incorrect |
| F-18 | P3 | **STILL-OPEN** | `schedule()` still has two call sites (`:1669`, `:2044`); unreachable during collection (M20 off) |
| F-19 | P3 | **FIXED** | one `run_id` across every resume (probe 4) |
| F-20 | P3 | **FIXED** | a `round_line` per executed action with family and cost (probes 1/4) |
| F-21 | P3 | **FIXED** | the `id(other) is not id(action)` no-op is gone; the remaining `id(other)` is a legitimate dict key |
| F-22 | P3 | **DEFERRED-WITH-JUSTIFICATION** | no *production/validation* runner supplies `layer6_integrator`. Note: `scripts/architecture_smoke.py:135` does supply it, so Audit 0041's "no caller ever" was slightly overstated — immaterial either way |
| F-23 | P3 | **STILL-OPEN** | `has_second_model`, `_minimum_neural_cost`, `integrate_layer4` still zero-caller |
| F-24 | P2 | **DEFERRED-WITH-JUSTIFICATION** | no entrypoint constructs `IntegrationMode.PRODUCTION`. Blocker before FULL VALIDATION only |

**Zero unresolved P0. Zero unresolved P1.**

---

## 5. N-01 / N-02 status

**N-01 — M18 independence groups — FIXED, and independently verified not to
inflate anything** (probes 11 and 12):

- All four `BidirectionalCheckKind.independence_group` strings
  (`M18_REVERSE`, `M18_KEY_CONDITION`, `M18_COUNTERFACTUAL`,
  `M18_CANDIDATE_FREE_RECALL`) now resolve; the `ProductionBridgeError` path is
  unreachable.
- **No relation contract declares any `M18_*` group** — `m(o)` is unchanged at
  3/3/3/4/5/6 for the six relations, so `q(o) = g(o)/m(o)` cannot move.
- They are **their own groups**, not aliases of an acquisition family, so
  `q_g = max` within a group is preserved and §12.1's prohibition on structural
  evidence inflating acquisition support holds.
- They can pay neither `F` (only `CORE_ACQUISITION`), nor `X` (only
  `CROSS_MODEL_RECALL` / `SPECIALIST_CROSS_FAMILY`), nor `I` (`is_recall` roles).
- The path is genuinely exercised: `M18_CANDIDATE_FREE_RECALL` edges are present
  on the graph for `awardWonBy`, `countryLandBordersCountry` and
  `personHasCityOfDeath`, alongside — never replacing — `DIRECT_RECALL`,
  `STRUCTURAL_DECOMPOSITION`, `REVERSE_ALTERNATE`. Pre-fix this raised, and
  `ProductionBridgeError` is now in `FATAL_ERRORS`, so it would have killed the
  run on the first `awardWonBy` row exactly as Audit 0042 claims.

**N-02 — two near-miss classes are two actions — FIXED at the catalogue level.**
`M18:COUNTERFACTUAL:<target>:hn0` and `:hn1` are distinct, carry distinct
`facet_id`/`target_class`, execute distinct prompts (distinct `prompt_sha256`
⇒ distinct `origin_event_id`), and are byte-identical across processes.
**The fix stops one layer short** — see **C-01**.

---

## 6. M0–M21 regression result

No module logic was redesigned. Re-checked the invariants Audit 0041 verified,
against the current tree:

| area | result |
|---|---|
| M0–M8 core | untouched by the diff |
| M8 sole output owner | unchanged; the only `ObjectEntities` write on the collection path is `predictions.write` of an already-built `Prediction` |
| M12 clustering / `UNKNOWN ≠ contradiction` | untouched |
| M14 null classes | untouched |
| M16 `q_g = max`, `g(o) ≤ m(o)` | untouched; N-01 verified not to perturb it |
| M17 contracts / label-order controls | untouched |
| M18 four mechanisms | untouched; only `action_id` and the enum members changed |
| Layer-4 / production bridge | rules 1–4 untouched; merging happens **before** the bridge, in `specialist_verifications` / `bidirectional_results` |
| M19 estimator | untouched; only its *reader* changed |
| M20 / M21 modules | untouched; both remain fail-closed and disabled |
| `IntegrationMode` | unchanged; SHADOW still mutates nothing |

Result merging is the one genuinely new risk surface and is reviewed in §16.

---

## 7. Committed TRAIN config

`configs/experiments/cover_kbc_v2_train_collection.yaml`. Machine-diffed against
the frozen target `cover_kbc_v2_mistral24_qwen4.yaml`:

- `model_profile` **identical** (dict-equal), `budget_assertion` **identical**.
- `pipeline` block identical except `mode` (`staged` → `interleaved`, which the
  runner forces regardless) and the new `max_control_rounds_per_catalogue: 3`.
  Prompts, decoding, `scoring`, `selection` and `controller` blocks — including
  every threshold — are unchanged.
- `experiment.split: train`; only name/notes differ.
- M9, M10, M11, M12, M13, M14, M15, M16, M17, M18, Layer-4, M19 **enabled**.
- M20, M21, Layer-6 **disabled**, `calibration_file: null`,
  `historical_bins: null`, `planner_calibration: null`. **No `SYNTHETIC_TEST`
  reference anywhere**; no fake `TRAIN_CALIBRATED` artifact.
- Selection belongs to `TrainCollectionPolicy` — `_select_actions` routes to
  M21 only when `integration_mode.is_production`, which collection is not.
- Parameter audit on this file: Mistral 24.011B + Qwen 4.660B = **28.67B ≤ 32B,
  RESULT: PASS**, both revisions exact.

The real runner consumes it: probes 1, 4, 7 and 15 all load this file unmodified
and replace only the runtimes. No test-local config was used anywhere in this
review.

`max_control_rounds_per_catalogue: 3` is genuinely enforced — observed 1–3 M17
rounds and 2–3 M18 rounds per query, never more.

---

## 8. Readiness gate

`evaluate_collection_readiness` is called at `run_train_calibration_collection.py:352`,
**before** `load_dataset` (:362) and before `build_pipeline` (:393). Proved
operationally: with `build_runtime` replaced by an exploding stub, the frozen VAL
target is refused (`IntegrationModeError` from `require_split`) and never reaches
it, while the TRAIN config passes the gate and only then builds a runtime.

Adversarial sweep (probe 8) — every case fails closed:

| profile | verdict |
|---|---|
| all five other committed configs (`split: val`) | NOT_READY |
| `cover_kbc_v2_train_collection.yaml` | **CALIBRATION_COLLECTION_READY** |
| each of M11/M16/M17/M18/Layer-4/M19 individually disabled | NOT_READY, naming the module |
| M20, M21 or Layer-6 individually **enabled** | NOT_READY, "no TRAIN-calibrated artifact exists yet" |
| split forced to `val` | NOT_READY |
| verifier `model_id` removed | NOT_READY |

Exactly one committed config can start a collection.

---

## 9. M19 telemetry

`ControlStateFeatures.from_coverage_gap` reads `state.residual` (Module 19's own
`CoverageGapComponents`) and matches components on `component.name.value`.
Adversarial shapes (probe 14):

| shape | behaviour |
|---|---|
| planner-style `.coverage_gap` (the Audit-0041 bug) | **TelemetryError** |
| `.residual` present but not a component block | **TelemetryError** |
| component with `name=None` | **TelemetryError** |
| component name outside §15's five | **TelemetryError** |
| component with `value=None` | accepted, reads 0.0, **absent from `available_components`** |
| `state is None` (M19 never ran) | `measured=False`, `available_components=()`, and the record schema then **refuses** to attach it to an executed action |

Observed on disk: 8 distinct residual values (0.0 … 0.8333); three distinct
`available_components` patterns, including one where `disagreement` reads 0.0
*and* is absent — a measured zero and an unmeasured component are genuinely
distinguishable. No degradation-to-zero path remains.

---

## 10. Per-action state transitions

`execute_action` captures `state_before` before the precharge and `state_after`
after integrate → bridge → M19 refresh. On the six-relation committed-config run:

- **`post(a) == pre(b)` on 24/24 consecutive executed pairs**, checked by full
  dataclass equality, not by field sampling.
- ΔR ≠ 0 on **10/30** executed actions; the sign convention is reduction-positive
  and matches `+β·ΔR̂` and `historical_bins`' declared unit.
- `pre_state != post_state` wherever M19 moved.

**ΔH is zero on 30/30 executed actions — and this is structural, not accidental.**
`control_entropy` = mean `H_inc(coverage_q(o))` over active candidates, and
`coverage_q = g(o)/m(o)` is computed over **acquisition groups only**. No M17 or
M18 action can change an acquisition group (the bridge attaches verifier and
`M18_*` evidence, never acquisition evidence), cannot add a candidate (Rule 2),
and did not change the active-candidate count in any observed row. Audit 0042
§11 B asserts ΔH "is non-zero elsewhere in the run"; **I could not reproduce
that on the same six rows, and the mechanism says it cannot happen.** Recorded
as **C-02** below. It is not a rerun-forcing defect — rerunning produces the same
zeros — and `expected_delta_h = 0.0` is inside `historical_bins`' declared
`[-1, 1]`, so no package build fails.

---

## 11. Candidate-effect telemetry

Measured by differencing `(raw_support_count, contradiction_count, verdict
labels)` per candidate around the action, plus `BridgeReport.candidates_touched`
and `discovered_not_inserted`. Provenance checked field by field:

| field | supplied by | observed |
|---|---|---|
| `candidates_added` | graph key diff | structurally always empty for Layer-4 actions (Rule 2 forbids minting) — 0/30 |
| `candidates_supported` | support-count / `VALID` diff | 4/30 |
| `candidates_contradicted` | contradiction-count / `INVALID` diff | 16/30 |
| `candidates_named` | `BridgeReport.discovered_not_inserted` | 0/30 in the scripted run |
| `redundancy` | `touched / (touched + named)`, `None` when no surface | 1.0 on 11/30, `None` on 19/30 |
| `verifier_outcome` | Module 17's own `argmax_label` | 12/12 M17 actions |
| `structural_outcome` | Module 18's own mechanism enum | 6/18 M18 actions; the other 12 carry `errors` — **see C-01** |
| `target_class` | projection `facet_id` or `target` | 24/30; empty only for candidate-free recall, which has no target |
| `action_id`, `spend_class`, `reserve_purpose`, `model_role` | Layer-6 projection | 30/30, 30/30, 1 observed `CANDIDATE_FREE`, 30/30 |
| `prompt_tokens`, `generated_tokens`, `enumerator_calls`, `verifier_calls`, `physical_calls` | runtime counters, role partition asserted | 30/30, partition sums everywhere |
| `cache_hits`, `parse_ok` | **nothing** — defaults 0 / True | see **C-07** |

Legal-but-unexecuted records (191 of 221) all carry `post_state=None`, zero cost,
no candidate effect, `selected=False`, and a full projection — the schema refuses
anything else.

---

## 12. Program type

Persisted values across the run are exactly `LARGE_OPEN_SET`, `NULL_SINGLE`,
`NUMERIC`, `SMALL_SET` — the canonical `ProgramType.value` set. No repr form
anywhere; the sufficiency validator rejects one if it appears (probe 13).
`program_type_value()` is now public precisely so persisting callers use it.

---

## 13. Deterministic action identity

Two independent `python` processes over the same rows produced **byte-identical
telemetry** once `run_id` is removed (`sha256 b9abac3c533a9159` both times), and
identical `operation_id` lists (221 entries). No `id(...)`, no address, no
process-local hash anywhere on the identity path. Examples:
`0:1:M17:SPECIALIST_VERIFY:0`, `0:2:M18:COUNTERFACTUAL:0:hn0`,
`0:2:M18:COUNTERFACTUAL:0:hn1`. The sufficiency validator additionally rejects
`0x`-shaped and long-digit-tail identifiers.

---

## 14. M18 independence-group fix

Covered in §5. Verified not to become acquisition groups, not to inflate `F`,
`q_g`, `m(o)`, `X` or `I`, and to remain structurally separate from
recall-origin groups. Not a blanket remap onto an existing acquisition family —
that was checked explicitly and is what would have been wrong.

---

## 15. Multi-round collection

The loop re-reads the owner's catalogue each round, excludes canonical
`action_id`s already run **this query** (identity, not object address, so it
survives the reload), asks the policy, executes exactly one action, integrates →
bridges → refreshes M19 and H, and stops on: bound reached, policy returns
nothing, catalogue exhausted, or the action was refused. Observed per query:
4 (numeric) to 6 (borders, death) executed actions, with round indices strictly
increasing and shared across the two catalogues, so M17 and M18 rounds never
collide on an identity. No infinite loop, no stale-catalogue execution.

---

## 16. Result merging across rounds

`_merge_by` unions by owner identity (`entry.request.target.target_id` for M17,
`record.origin_event_id` for M18), previous-then-new, keeping the earlier record
on collision. Verified on three multi-round queries (probe 16):

- round-1 evidence **survives**: 3 M17 entries and 3 M18 records merged into one
  result object per query, not overwritten;
- M17 target ids unique in every merged set;
- **no duplicate evidence edge tuples on any candidate** — bridge Rule 4 plus
  the merge means re-integrating after every action does not double-count;
- `raw_support_count` / `contradiction_count` consistent with the number of
  distinct edges.

Layer 4 sees the complete current set. Bridge idempotency holds. **However**, M18
`request.operation_id` is not unique within a merged set — see C-01.

---

## 17. Collection-policy rotation

`begin_query()` resets `_query_selected` only; the run-wide coverage ledger is
untouched. Rotation genuinely works across sequential rounds: on
`countryLandBordersCountry` the M18 sequence was
`CANDIDATE_FREE_RECALL → COUNTERFACTUAL_VERIFY → REVERSE_CHECK`, and on
`awardWonBy` (which declares no reverse check)
`CANDIDATE_FREE_RECALL → COUNTERFACTUAL_VERIFY → COUNTERFACTUAL_VERIFY`.
All six queries began their M18 rotation at `CANDIDATE_FREE_RECALL`, which is
positive proof that no query inherited another's rotation position. M17 and M18
family sets are disjoint, so sharing one `_query_selected` dict across the two
catalogues cannot interfere. Selection is deterministic (byte-identical across
processes), bounded (`per_family_limit=2` × 3 rounds), legal-action-only
(`_select_actions` re-checks catalogue membership and raises `UnsupportedAction`
otherwise) and gold-blind (the policy reads nothing but the catalogue).

---

## 18. Prompt-token accounting

`BaseRuntime.prompt_tokens` accumulates via `charge_prompt_tokens`, which ignores
`None`/0 rather than guessing. Charge sites in `huggingface.py`: `generate`,
`_score_next_token`, `_score_sequence` (once per label — each is a real forward
pass). `score_labels` dispatches and does **not** charge, so there is exactly one
charge per `self.calls += 1`.

Arithmetic reconciled with known counts (probe 19), 8-token prompt:

```
1 generate            -> calls=1 prompt_tokens=8
+1 score_labels       -> calls=2 prompt_tokens=16
+3 score_labels       -> calls=5 prompt_tokens=40
two-role delta        -> {physical:2, prompt_tokens:16}
single-role delta     -> {physical:1, prompt_tokens:8}   (no double count)
```

No double counting through runtime → `physical_snapshot` → `physical_delta` →
telemetry → `RunCounters` → `accounting.json`: `physical_delta` **differences the
runtimes' own cumulative counters**, it does not re-sum per-call figures, and the
single-role guard prevents adding the same runtime twice. Whole-run
reconciliation on the committed config: telemetry Layer-4 prompt tokens 17 533,
`accounting.json` 27 235 — the difference is acquisition, which telemetry does
not record per action but which is fully recoverable from the query-scoped state
(§19).

---

## 19. M20 sufficiency

Decisive reconciliation (probe 20). `ControlStateFeatures.calls_used` /
`prompt_tokens` / `generated_tokens` are **query-scoped**, differenced against a
baseline taken at `enumerate_query`. Summing the last action's `post_state`
per query:

```
sum of per-query hard_calls from telemetry state = 151
accounting.json physical_model_calls             = 151     exact
telemetry Layer-4 calls only                     =  82
derived acquisition (hard_calls - L4)            =  69
```

Per-relation derivation, from telemetry alone:

| relation | hard_calls | acq | L4 | DISCOVERY | VERIFICATION | hard_gen_tokens | reserve |
|---|---|---|---|---|---|---|---|
| awardWonBy | 35 | 20 | 15 | 1 | 14 | 92 | — |
| companyTradesAtStockExchange | 25 | 10 | 15 | 1 | 14 | 55 | — |
| countryLandBordersCountry | 28 | 9 | 19 | 1 | 18 | 36 | — |
| hasArea | 20 | 9 | 11 | 1 | 10 | 12 | — |
| hasCapacity | 21 | 10 | 11 | 1 | 10 | 13 | — |
| personHasCityOfDeath | 22 | 11 | 11 | 1 | 10 | 12 | `CANDIDATE_FREE: 1` |

`hard_calls`, `hard_generated_tokens`, `discovery_cap`, `verification_cap`,
`verification_reserve` and the special-reserve sizes are all derivable, grouped
by `relation` / `spend_class` / `reserve_purpose`. `reserved_class` is empty on
every record and the validator **rejects** one that claims otherwise — collection
asserts no reservation it did not make, exactly as §16 requires of a run that
predates the calibrated ledger. The owner's declaration travels under
`spend_class`/`reserve_purpose`, which is not the same field and does not
masquerade as a budget decision.

**No M20 quantity requires rerunning a model.**

---

## 20. M21 sufficiency

| §17 estimate | derivable? | from |
|---|---|---|
| `expected_verified_gain` | **yes** | `candidates_supported` (+ `candidates_named`), joined to gold offline |
| `expected_fp` | **yes** | `candidates_supported` / `candidates_contradicted`, joined to gold offline |
| `expected_delta_r` | **yes** | `pre.residual − post.residual`; non-zero on 10/30 |
| `expected_delta_h` | **yes, but identically 0** | measured; structurally invariant (C-02) |
| `expected_cost` | **yes** | `physical_calls` / `prompt_tokens` / `generated_tokens` + role split |
| `expected_redundancy` | **yes, degenerate** | `redundancy` ∈ {`None`, 1.0} in practice (C-06); the raw identities are recorded, so the definition can be changed offline without a rerun |
| successor probabilities | **yes** | 24 observed transitions, `post(a) == pre(b)` verified |

Bin key `(relation, program_type, state_bin, family, target_class)`: all five
recorded; `program_type` canonical; `state_bin` derivable from the five §15
components plus `available_components`; `target_class` populated on 24/30
(empty only where the action genuinely has no target).

**No statistic is missing in a way that would require rerunning the frozen
models.** The two weaknesses (C-02, C-06) are properties of the metrics
themselves, not of the instrumentation, and re-running would reproduce them
exactly.

---

## 21. Sufficiency validator — adversarial review

Twenty structural adversaries (probe 13). Correctly refused:

empty telemetry · only-unexecuted records · mixed schema versions · repr
`program_type` · non-canonical `action_family` · missing `action_id` ·
memory-address and `0x` `operation_id` · unmeasured state on an executed action ·
state with no available component · M17 verdict *and* errors both stripped ·
M18 reading *and* errors both stripped · missing `spend_class` · claimed
`reserved_class` · zero prompt tokens on charged actions · no successor chain ·
broken role partition (caught at schema level).

Correctly **accepted**: an all-zero but genuinely *measured* state. The
measured-zero / absent-measurement distinction is respected throughout.

Ten schema-level adversaries all refused: executed without post-state, executed
without selection, selected-but-illegal, unexecuted-but-charged,
unexecuted-but-claims-effect, executed without `action_id`, executed against an
unmeasured state, NaN residual, foreign schema version, missing `operation_id`.

**Two genuine gaps (C-05):**

- **Wiping every candidate-effect field** (`candidates_added`, `_supported`,
  `_contradicted`, `_named`) on every record → validator returns **PASS**.
- **Wiping `redundancy` to `None`** everywhere → **PASS**, and it still prints
  "redundancy is recorded whenever the action had a candidate surface".

The `redundancy` check only fires when `candidates_named` is non-empty, and
`candidates_named` is empty on 30/30 records in practice, so the check is
vacuous. `expected_verified_gain`, `expected_fp` and `expected_redundancy` — three
of the six §17 estimates — are therefore *not* guarded by the gate written to
guard them. The instrumentation itself works today (verified directly), so this
does not affect the coming run; it is a hole in the safety net, not in the data.

---

## 22. Family-coverage gate

`required_families(("m17","m18"))` reads `M17_FAMILIES`/`M18_FAMILIES` from the
Layer-6 adapter — no duplicated string list. The four statuses are genuinely
distinguished by `(executed, legal_opportunities, surfaced, required)` and the
gate fails on both `LEGAL_BUT_UNEXECUTED` and `NEVER_SURFACED`.

Challenged operationally: with Module 18's `REVERSE` mechanism unwired and **no
`--limit`**, the full-run gate produced

```
BLOCKER action family REVERSE_CHECK was required but no catalogue ever offered it
        - a wiring failure, not a dataset fact
coverage: REVERSE_CHECK = NEVER_SURFACED    integrity_ok = False    exit 1
```

while sufficiency itself still read PASS — the two concerns stay separate, which
is right. The `--limit` downgrade to a printed note is correct and only applies
when `args.limit` is set; the real 477-row run has no `--limit` and fails closed.

Note: `KEY_CONDITION` and `COUNTERFACTUAL` both project to the single
`COUNTERFACTUAL_VERIFY` family, so the gate cannot distinguish them. That is the
canonical §17 action-family granularity, not a defect; the two remain distinct in
`action_id`.

---

## 23. Row failure containment

`FATAL_ERRORS = (MemoryError, OSError, AccountingInvariantError, TelemetryError,
ProductionBridgeError)`, plus any non-`Exception` `BaseException` and anything
CUDA-OOM by name. This is the right boundary:

- accounting invariant break, telemetry corruption, bridge invariant break and
  disk/IO failure all **abort** — verified for `MemoryError` (probe 4) and
  `TelemetryError` (probe 6);
- `KeyboardInterrupt` / `SystemExit` abort by the non-`Exception` rule;
- a corrupt checkpoint is refused by `ResumeRefused` before the loop starts, and
  a checkpoint with no recorded `run_id` is refused rather than guessed at;
- an ordinary row-local error aborts the row transaction, commits **nothing**
  (verified: no telemetry, no prediction, row absent from `completed`), records
  `row_index`/`relation`/`subject`/`error`/`physical_calls_burned`, and continues.

The broad `except BaseException` does not hide a global invariant break, because
every global invariant raises a type in `FATAL_ERRORS`. `physical_delta` inside
the containment handler can itself raise `AccountingInvariantError`, which then
propagates to the abort path — correct.

---

## 24. Failed-row success semantics

`rows_failed`, `failed_rows`, `failures[]` and `failed_row_calls` are populated
and truthful at the moment of failure; failed-row spend is kept out of the
committed totals and reported separately. The full run **cannot** exit 0 with a
failed row: the gate appends a blocker for any entry in `failed`, for
`rows_completed != total`, for `prediction_rows != rows_completed`, for any
`LEGAL_BUT_UNEXECUTED` or `NEVER_SURFACED` family, and for every sufficiency
blocker. An aborted run also always blocks.

It is in fact **over**-strict: see **C-03**.

---

## 25. Multi-resume

Beyond Audit 0042's single scenario I ran fresh → fatal(row 100) → resume →
fatal(row 210) → resume → fatal(row 377) → resume → complete → resume-when-
already-complete, through the real `main()` each time:

| property | result |
|---|---|
| run directories | **1**, throughout |
| `run_id` | **1** stable id; checkpoint names an existing directory at every step |
| telemetry records | 19 → 77 → 194 → 221; `run_id` identical in all 221 |
| duplicate identities | **none** at any step |
| previously committed telemetry | preserved verbatim |
| predictions | exactly once per row (6 lines, 6 distinct subject/relation) |
| accounting | cumulative — final 151 calls / 27 235 prompt tokens, **exactly equal** to the uninterrupted run |
| coverage | cumulative — final family counts identical to the uninterrupted run |
| failed rows | truthful |
| manifest | consistent with the files beside it |
| resume when all rows complete | every row skipped, no new records, no new directory |

F-05 is genuinely closed. The resumed run is byte-equivalent to the
uninterrupted one.

---

## 26. Transaction / atomicity review

Row commit order: telemetry writes (each flushed) → coverage → prediction write
+ flush → counters → `completed.add` → `persist()` (`*.partial` + `Path.replace`,
atomic on POSIX). A mid-*row* failure before the commit block leaves nothing
durable — verified.

**A hard kill *inside* the commit block is a real hole (C-04).** Simulated with
`os._exit(137)` after a telemetry flush for an uncommitted row (probe 6):

```
after crash : checkpoint completed=[0], telemetry contains rows [0, 100]
on resume   : COLLECTION ABORTED
              TelemetryError: duplicate telemetry identity 100:1:100:1:M17:SPECIALIST_VERIFY:0
              exit 1
```

Because `TelemetryError` is fatal, **every** subsequent `--resume` aborts at the
same point. Assessment:

- it **fails loudly**; no corrupt or duplicated calibration data is ever produced;
- no completed row is lost;
- recovery is manual but trivial — delete the trailing telemetry lines whose
  `row_index` is not in the checkpoint's `completed_rows`, then resume;
- the window is the few milliseconds of file writes inside a per-row budget of
  tens of seconds of real inference.

Judged against the intended Colab/local filesystem, this is **P2**, not P1: it
does not block starting TRAIN and cannot force a rerun. It must nonetheless be
in the operator's runbook, because without knowing the recovery an operator could
believe a multi-hour session had been destroyed.

No other realistic window produces duplicate records, missing rows, an
inconsistent completed set, manifest overcount or unrecoverable artifact
disagreement: prediction-before-checkpoint and checkpoint-write-failure crashes
all funnel into the same loud duplicate-identity refusal.

---

## 27. TRAIN-gold isolation

Re-traced after remediation. `Query` is still a frozen dataclass with exactly
`(subject, relation, row_index)`. `load_dataset` → `dataset.queries()` is the only
dataset path into the runner; no `DatasetRow` crosses into the pipeline.

The new candidate-effect instrumentation derives identities **only** from
`graph.candidates` keys, `candidate.raw_support_count`,
`candidate.contradiction_count`, `candidate.verifications[*].label`,
`BridgeReport.candidates_touched` and `BridgeReport.discovered_not_inserted` —
all model/evidence/action products. It never reads the dataset.

Repository grep over the whole collection path: the only `ObjectEntities`
occurrences are the docstring, the *prediction* writer (M8's own output), and
sufficiency/telemetry docstrings stating that gold is joined offline. No
`gold`-bearing value reaches prompts, acquisition, M3, specialists, verifiers,
M18, M19, `H`, the policy, the coverage ledger or telemetry.

---

## 28. Closed-book / no-training / parameter budget

- Grep over the entire `+` side of the diff and over both new source files for
  `requests`, `urllib`, `httpx`, `aiohttp`, `socket`, `wikipedia`, `wikidata`,
  `bm25`, `faiss`, `chroma`, `pinecone`, `elasticsearch`, `serpapi`,
  `duckduckgo`, `web_search`, `optimizer`, `.backward(`, `torch.optim`,
  `requires_grad`, `lora`, `peft`, `fine-tun`, `gradient`, `.train()` —
  **zero hits**.
- No new neural component: `build_pipeline` still constructs exactly `runtime`
  and `verifier_runtime` and audits exactly those specs.
- Frozen profile unchanged and exact:
  `mistralai/Mistral-Small-3.2-24B-Instruct-2506` @
  `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` (24 011 361 280) and
  `Qwen/Qwen3.5-4B` @ `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
  (4 659 865 088), total **28 671 226 368 ≤ 32 000 000 000**, audit `RESULT: PASS`.
- The runtime diff is **instrumentation-only**. `charge_prompt_tokens` reads
  values already computed for other purposes (`input_ids.shape[-1]`,
  `prompt_ids.shape[-1]`, the existing `prompt_tokens` local) and cannot raise on
  an int. Statically verified unchanged: prompt bytes/content, tokenizer inputs,
  generation kwargs, decoding path, logits computation, label scoring,
  quantization, model loading, cache semantics, exception behaviour. Nothing in
  the diff can plausibly alter inference behaviour.
- The **real-weight smoke required by Audit 0042 remains a sensible operational
  precondition** and I endorse it: `models/base.py` and `models/huggingface.py`
  are on the audited weight-loading path, and the repository's own discipline
  (§18, §D of the proposal) requires a real-weight smoke per profile after any
  change to that path — even an additive one.

---

## 29. Deferred F-11 / F-22 / F-24

| ID | may it stay deferred? | why |
|---|---|---|
| F-11 | **yes** | The per-module `mode:` key is read by module constructors that are *disabled* during collection (`micro_planner.enabled: false`, `relation_budget_scheduler.enabled: false`, `layer6_integration.enabled: false`). Collection's actual authority is `IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY`, which the pipeline owns and which correctly permits mutation. Nothing about the `mode: shadow` strings in the committed config affects what is collected. Does not interfere with `TRAIN_CALIBRATION_COLLECTION_ONLY`. |
| F-22 | **yes** | `Layer6Integrator` matters only when M21 selects. Collection selects with `TrainCollectionPolicy` and `_select_actions` never routes to the planner outside `is_production`. Its absence removes no telemetry: the catalogue projections it would supply (`action_id`, family, budget descriptor) are obtained directly through `project_action`, and all of them are present in the emitted records. |
| F-24 | **yes** | A calibrated PRODUCTION entrypoint is needed only after M20/M21 artifacts exist, which is the *output* of this collection. Its absence cannot affect collection quality; collection and production share `execute_action`, so the seam is already exercised. |

None of the three affects current collection quality. All three remain FULL
VALIDATION blockers.

---

## 30. New findings (this review)

| ID | Sev | Finding | Why it does not threaten the collection |
|---|---|---|---|
| **C-01** | P2 | **M18 structural-outcome mis-attribution.** `BidirectionalCheckRequest.operation_id` is `m18_<kind>:<template>#<sample>` — it carries neither target nor near-miss class, so two same-kind checks in one query collide. `_m18_reading` matches on it and returns the **first** match, so the second counterfactual in a query inherits the first's `structural_outcome` and `errors`. Proved directly: a record whose true reading is `SUPPORT`/unparseable is recorded as `CONTRADICT`/no-errors. Observed collisions on 2 of 3 multi-round M18 queries. N-02 fixed the *catalogue* id; this is one layer below it. | `structural_outcome` and `errors` are **not** among `REQUIRED_ESTIMATES` or `M20_REQUIREMENTS`. All six §17 estimates, the successor distribution and the bin key are captured by the seam (state diff, graph diff, runtime counters) and are unaffected. The evidence graph is unaffected — each check enters under its own `origin_event_id` with its own edge. **Strongly recommended to fix before the run** (include target + near-miss class in the M18 request identity, or match the record by check identity rather than `operation_id`) because it is small and the run is expensive, and because any paper claim about per-near-miss-class structural outcomes would otherwise be wrong. |
| **C-02** | P2 | **ΔH is identically zero over the entire collected action space.** `H` is a function of acquisition-group coverage; no M17/M18 action can change an acquisition group or add a candidate. 30/30 executed actions had ΔH = 0. `expected_delta_h` will therefore be 0.0 in every bin and §17's `+γ·ΔĤ` term will be inert. Audit 0042's claim that ΔH "is non-zero elsewhere in the run" was not reproducible. | Not a missing measurement and not rerun-fixable: the value is genuinely zero. `0.0` is inside `historical_bins`' declared `[-1, 1]`, so no package build fails and M21 still functions on the other five terms. Production selects from the same M17/M18 action space, so collection and production stay consistent. **Must be stated in the paper** rather than presented as a six-term utility calibrated on TRAIN. |
| **C-03** | P2 | **A contained row failure permanently blocks exit 0, even after a successful retry.** `failed` / `rows_failed` are restored from the checkpoint and never cleared when the row later succeeds. Observed: 3/3 rows completed, telemetry for all three, 3 predictions, sufficiency PASS — and still `gate: ['1 row(s) failed and were omitted from telemetry']`, `status: incomplete`, exit 1. The message is factually false at that point. | Fails closed in the safe direction; nothing is missing or wrong in the data. The operator can read `rows_completed == 477`, `failed_rows ⊆ completed` and `sufficiency.ok == true` from the manifest and proceed. It does, however, defeat the purpose of row containment as a *progress-preserving* feature. |
| **C-04** | P2 | **Crash inside the row-commit block makes every subsequent resume abort.** See §26. | Loud, non-corrupting, no completed work lost, manually recoverable, small window. Needs a runbook line, not a code gate. |
| **C-05** | P2 | **The sufficiency validator does not detect loss of candidate-effect or redundancy instrumentation.** Wiping all four candidate lists, or all `redundancy` values, still returns PASS — and still prints the corresponding "ok" lines. Three of six §17 estimates are unguarded. | The instrumentation works today, verified directly and by tests. This is a hole in the safety net, not in the data. It matters because the safety net exists precisely to stop Audit 0041's failure mode recurring silently. |
| **C-06** | P3 | `redundancy` is structurally `{None, 1.0}` unless a probe names an unheld candidate, and `candidates_added` is structurally always empty for Layer-4 actions (bridge Rule 2). | The raw identities (`candidates_supported/contradicted/named`) are recorded, so a different redundancy or gain definition can be derived offline without touching a model. |
| **C-07** | P3 | `cache_hits` is always 0 and `parse_ok` always `True` — neither is supplied by the execution path. 12 records carry `errors=['parse:MALFORMED']` while asserting `parse_ok: true`. | Neither field appears in `M20_REQUIREMENTS` or `M21_REQUIREMENTS`. |
| **C-08** | P3 | `legal_opportunities` is inflated: `note_legal` runs for every catalogue entry in **every** round, so an entry offered three times counts three times (183 for `COUNTERFACTUAL_VERIFY` over six rows). | Used only for the `> 0` status test and the printed table; no calibration quantity reads it. |
| **C-09** | P3 | Dead code: `TrainCollectionPolicy.record_outcome` and `family_of` have no production caller (the runner uses `coverage.note_executed` with the canonical family, which is correct); `coverage_ok` at runner `:602` is computed and never used. | Maintenance only. |
| **C-10** | P3 | Collection forces `interleaved`; the frozen target declares `staged`. Collection also never calls `verify_graph()` (Audit 0041 §6). So the collected action chain sits inside a slightly shorter core loop than validation will run. | Documented and deliberate in the config header, with a stated reason (a staged role swap would split one query's action chain). The Layer-4 seam — the only thing telemetry measures — is identical. |
| **C-11** | P3 | `_score_sequence` charges `prompt_ids.shape[-1]` per label, i.e. prompt-only, not prompt + continuation. | Consistent with the `LabelScoreResult.prompt_tokens` it reports; an accounting definition, not an error. |
| **C-12** | P3 | A query with zero executed Layer-4 actions emits no telemetry record at all, so its acquisition spend is invisible to per-relation M20 derivation. Not observed on any of the six relations. | Omits only the cheapest queries, which is conservative for a ceiling estimate. |
| **C-13** | P3 | The telemetry writer's duplicate key is `f"{row}:{round}:{operation_id}"` while `operation_id` already begins `row:round:`, producing keys like `100:1:100:1:M17:...`. | Still unique; cosmetic. |

---

## 31. pytest / pyflakes / benchmark

| check | command | result |
|---|---|---|
| unit + integration | `python -m pytest -q` | **3 053 passed, 3 skipped, 36.18s** |
| static | `python -m pyflakes src/ tests/ scripts/` | **clean, exit 0** |
| benchmark immutability | `git diff -- benchmark/` | **empty** (0 lines) |

Volume was not treated as evidence; the high-value tests were read. They exercise
production behaviour, not helpers:

- `test_collection_failure_resume.py` loads the **committed** config unmodified
  and drives the real `main()`; only the runtimes are replaced. It covers the
  readiness gate, M11–M19 construction, non-empty telemetry, canonical
  `program_type`, non-address identity, two resumes in one directory, one stable
  `run_id`, preserved records, no duplicate identity, predictions exactly once,
  cumulative accounting and coverage, checkpoint↔directory consistency, manifest
  ↔ artifact agreement, the exit gate, row-local containment, process-fatal
  abort, the explicit exception boundary, and a resume with no recorded `run_id`.
- `test_action_execution_seam.py` asserts per-action cost, `post(a) == pre(b)`,
  captured-not-reconstructed state, signed ΔR, the five §15 components,
  per-action prompt tokens, measured candidate effects, measured-zero vs absent
  redundancy, the owner's own verdict, canonical and cross-process-stable
  identity, distinct identities for distinct logical actions, unselected actions
  with no cost, role-partition failure modes, and M8 ownership after the seam.
- `test_controller_calibration_telemetry.py` covers the M19 read (canonical enum
  value, wrong shape raises, absent ≠ zero, unavailable ≠ measured zero),
  successor transitions including the no-fabrication case, and gold absence.
- `test_controller_calibration_readiness.py` asserts the committed TRAIN config
  may collect, is **not** validation-ready, the frozen target may not collect,
  every required module is enforced, calibrated modules may not be on, and the
  model profile is byte-equal to the frozen target.
- `test_calibration_sufficiency.py` and `test_pipeline_production_seam.py` cover
  the validator and the seam.

Gaps in the new tests, consistent with §21 and §30: none asserts that wiping
candidate-effect instrumentation is rejected, none pins ΔH behaviour, and none
covers a crash inside the row-commit block or a resume after a successful retry.

---

## 32. Scripted committed-config run

Real `main()`, **committed** config, no substitutions except the runtimes, one
TRAIN row per relation (rows 0, 100, 200, 210, 310, 377).

```
readiness        : CALIBRATION_COLLECTION_READY
integration mode : train_calibration_collection_only
action bound     : 3 round(s) per catalogue per query
exit code        : 0
telemetry        : 221 records (30 executed, 191 legal-but-unexecuted)
relations        : all six
program types    : LARGE_OPEN_SET, NULL_SINGLE, NUMERIC, SMALL_SET
families         : SPECIALIST_VERIFY, CANDIDATE_FREE_RECALL,
                   COUNTERFACTUAL_VERIFY, REVERSE_CHECK  — all OBSERVED
actions/query    : 4, 4, 5, 5, 6, 6
transitions      : 24, post(a)==pre(b) on all 24
residual         : 8 distinct values, ΔR≠0 on 10/30
prompt tokens    : 17 533 (telemetry) / 27 235 (accounting) — reconciled
role partition   : sums on every record
predictions      : 6 for 6 committed rows
manifest         : status=complete, gate_blockers=[]
sufficiency      : PASS (14 satisfied checks, 0 blockers)
```

All of M11–M19 were reached: M11 parametric retrieval and all four specialists
were constructed (readiness enforces it), M16 produced consensus, M17 and M18
executed real actions through the seam, Layer-4 integrated, the bridge applied
verification and structural edges, and M19 refreshed after every action.

---

## 33. Exact blockers before the 477-row TRAIN collection

**None.** Zero unresolved P0, zero unresolved P1. One operational precondition
(the real-weight smoke, §35) and one strong recommendation (C-01, §30), neither
of which gates the run.

---

## 34. Exact blockers before FULL VALIDATION

1. Real TRAIN-derived M20 (`RelationBudgetCalibration`, `TRAIN_CALIBRATED`) and
   M21 (`HistoricalBinPackage` + `PlannerCalibration`) artifacts. This collection
   produces the observations; the derivation is a separate milestone.
2. **F-24** — an entrypoint constructing the pipeline with
   `IntegrationMode.PRODUCTION`.
3. **F-11** — module configs able to express `production`; `MicroPlannerConfig`
   and `RelationBudgetConfig` still raise on any non-`shadow` mode.
4. **F-22** — `layer6_integrator` supplied to the production pipeline, or M21
   receives an empty legal-action list and always returns `STOP/NO_LEGAL_ACTION`.
5. A validation config (`split: val`, M11–M21 enabled, both artifacts declared)
   gated by `evaluate_readiness` returning `FULL_VALIDATION_READY`.
6. C-02 resolved or explicitly stated: if the paper claims a six-term utility
   calibrated on TRAIN, the γ term's identically-zero estimate must be disclosed.

`FULL_VALIDATION_READY` is **not** claimed.

---

## 35. Real-weight smoke recommendation

Endorsed. `src/cover_kbc/models/base.py` and `src/cover_kbc/models/huggingface.py`
are on the weight-loading path; the change is additive instrumentation and cannot
alter control flow, but the proposal's §18/§D discipline requires a real-weight
smoke per profile after any change to that path. Run it once before starting the
477-row collection. Command in §37.

---

## 36. Epistemic evidence levels

| claim | evidence |
|---|---|
| Committed config runs end-to-end, exit 0, sufficiency PASS | **Executed** — real `main()`, committed config, six relations, artifacts read off disk |
| Non-degenerate M19 state; measured ≠ unavailable ≠ absent | **Executed** — 8 residual values, 3 availability patterns; plus 6 adversarial shapes |
| `post(a) == pre(b)`; ΔR non-zero | **Executed** — 24/24 dataclass equality; 10/30 non-zero |
| **ΔH ≡ 0 (C-02)** | **Executed** (30/30) **+ mechanism traced** in `coverage_q`/`acquisition_groups` |
| Multi-resume, 3 resumes + already-complete | **Executed** — artifacts read back at every step; byte-equal to the uninterrupted run |
| **Commit-window crash (C-04)** | **Executed** — `os._exit(137)` mid-commit, then a real resume |
| **M18 outcome mis-attribution (C-01)** | **Executed** — collision observed in the live run, mis-attribution proved directly against `_m18_reading` |
| Determinism across processes | **Executed** — two processes, sha256-identical telemetry |
| Prompt-token arithmetic | **Executed** — known-count reconciliation, single- and two-role |
| M20 derivability | **Executed** — per-query state sum equals `accounting.json` exactly (151 = 151) |
| **Validator gaps (C-05)** | **Executed** — 20 adversarial mutations |
| Readiness fails closed | **Executed** — 6 committed configs + 11 mutations; gate-before-runtime proved with an exploding stub |
| Coverage gate on a full run | **Executed** — mechanism unwired, no `--limit`, exit 1 |
| N-01 does not inflate F/X/I/m(o) | **Executed** (contract/group enumeration, live graph groups) **+ static** (scoring source) |
| Gold isolation; closed-book; no training | **Static, exhaustive** — `Query` fields, diff-wide greps, new-file greps |
| Parameter budget 28.67B ≤ 32B | **Executed** — `audit_parameter_budget` on the committed config |
| Runtime diff cannot alter inference | **Static** — read line by line; not executable under this review's scope |
| Real-weight behaviour | **Not executed** — prohibited by scope |
| M20/M21 in a calibrated production run | **Not executed** — no such path exists yet |

---

## 37. Final verdict

> ## PASS — SAFE TO START PRE-TRAIN REAL-WEIGHT SMOKE

Every P0 and P1 from Audit 0041 is independently confirmed closed, and both
defects Audit 0042 discovered during remediation (N-01, N-02) are real and
genuinely fixed. The two failures that made Audit 0041 BLOCKED are gone at the
mechanism level, not merely at the symptom level: Module 19's state is read from
its owner and a shape mismatch now crashes instead of zeroing, and per-action
state is captured at the seam at the moment it is true, with `post(a) == pre(b)`
holding on every observed transition. The committed TRAIN config is executable,
byte-identical to the frozen target where it matters, and the only committed
profile that can start a collection. Durability is credible: three sequential
resumes plus an already-complete resume reproduce the uninterrupted run exactly.
Telemetry is sufficient for both M20 and M21 — the per-query accounting
reconciles to the last call — and **no missing statistic would force rerunning
the 477 rows**. No gold reaches inference or instrumentation, the closed-book and
no-training boundaries are intact, and the published parameter total is 28.67B.

Five P2 findings remain (C-01…C-05). None is a rerun-forcing defect: C-01
corrupts the attribution of a field no §17 estimate depends on; C-02 is a true
measurement of a quantity that is structurally zero, so a rerun would reproduce
it; C-03 and C-04 fail closed and loudly without losing data; C-05 is a hole in a
safety net whose subject is verified working today. I would fix **C-01** before
the run because it is small and the run is expensive, and I would put **C-04**'s
recovery procedure in the runbook before starting — but neither is a gate.

Then, before the 477-row collection:

```
python scripts/real_model_smoke.py \
  --config configs/experiments/cover_kbc_v2_train_collection.yaml \
  --output-dir outputs/smoke
```

Do not run it as part of this review. After it passes, the owner may start the
477-row TRAIN collection with the committed config:

```
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v2_train_collection.yaml \
  --output-dir outputs/train_collection
```

(and `--resume` on the same `--output-dir` after any interruption).

---

*Independent review. Audit 0042 was not trusted as evidence; the uncommitted
working tree was inspected directly. No production code, configuration, test,
notebook, benchmark file or existing audit was modified by Codex. `git status`
shows this file as the only addition.*
