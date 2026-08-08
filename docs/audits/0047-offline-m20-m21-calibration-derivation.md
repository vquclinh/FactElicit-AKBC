# 0047 — Offline Module 20 / Module 21 Calibration Derivation

**Verdict: PASS — DERIVATION PIPELINE COMPLETE AND VALIDATED AT FULL SCALE.
REAL TRAIN DERIVATION NOT YET RUN. PRODUCTION ACTIVATION NOT DONE.**

---

## 0. Scope

`COVER_KBC_Technical_Proposal_New.pdf` was re-read before any code was written;
§16 (Table 6), §17 (the utility, `τ_continue`, 1–2 step lookahead), §9.3 (the
hard verification reservation) and §15 (`R_t`) are the contract this milestone
implements.

The milestone builds the **offline deterministic derivation** of Modules 20 and
21 from collected TRAIN telemetry. It does not activate validation inference,
does not load a model, and does not read VAL or TEST.

**The real 134 MB frozen-model telemetry is not present in this environment.**
Nothing below claims otherwise: the pipeline is implemented, unit-tested, and
pipeline-tested end to end at full 477-row scale against telemetry produced by
the real collection runner with scripted runtimes. The exact command to run it
on the preserved Drive artifacts is in §14.

---

## 1. HEAD, working tree, benchmark

| item | value |
|---|---|
| HEAD | `264c980361a513078903526440c72adc6e10edaf` (`main`) — the stated collection source SHA |
| Working tree before | clean |
| Added by this milestone | 5 files, all new; **nothing existing was modified** |
| `git diff -- benchmark/` | empty; `git status -- benchmark/` empty |
| Tests | **3 148 passed, 3 skipped** (was 3 124) |
| Static | `python -m pyflakes src/ tests/ scripts/` clean, exit 0 |

```
?? scripts/derive_train_calibration.py
?? src/cover_kbc/controller_calibration/derivation.py
?? src/cover_kbc/controller_calibration/gold_join.py
?? tests/test_derive_train_calibration_cli.py
?? tests/test_train_calibration_derivation.py
```

That the diff is purely additive is itself a finding: the existing M20/M21
contracts were sufficient, so no scheduler, planner, state model, relation
registry, runtime loader or model-profile resolver was duplicated.

---

## 2. Existing implementation traced before coding

| concern | owner reused | how |
|---|---|---|
| M20 artifact | `RelationBudgetCalibration` + `load_calibrations` | derivation emits `{"relations": [...]}`, the exact shape the loader reads |
| Table 6 policy | `relation_policy` / `RELATION_BUDGET_POLICIES` | tiers, `discovery_capped`, `verification_hard_reserved` and declared reserve purposes are **read**, never re-decided |
| Spend vocabulary | `BudgetSpendClass`, `SpecialReservePurpose`, `CalibrationSource` | no parallel enum |
| M21 bins | `HistoricalBinPackage`, `HistoricalActionBin`, `StateBinningSpec`, `SuccessorStat` | derivation constructs the real dataclasses, so their own validation runs |
| M21 coefficients | `PlannerCalibration` | all seven §17 names, its own sign checks enforced |
| Bin key | `StateBinningSpec` + `historical_bins.state_bin_key` | one spec, two readers (§6) |
| Utility | `micro_planner.utility` | untouched; the derivation only supplies its inputs |
| Telemetry | `read_telemetry`, `ActionTelemetryRecord` v3, `RedundancyStatus` | no re-parsing |
| Sufficiency | `evaluate_sufficiency` | re-run over the file, not trusted from the manifest |
| Evaluator | `evaluation/official.py`, `evaluation/harness.py` | the pinned `benchmark/evaluate.py` itself |
| Relation catalogue | `contracts.registry` via `relation_policy` | unknown relation raises |
| Readiness | `controller_calibration/readiness.py` | asserted to accept the generated files |

No architectural conflict was found, so nothing was reported as blocked.

---

## 3. Proposal compliance

**§16 / Table 6.** The qualitative policy is not re-derived. `relation_policy`
already declares each relation's discovery and verification tier, whether
discovery is capped, whether verification is hard-reserved, and which special
purposes exist; §16 defers only the *concrete values*, and those are what the
derivation computes. Consequences visible in the full-scale run:

| relation | hard_calls | gen tokens | discovery | verification | reserve | special |
|---|---|---|---|---|---|---|
| awardWonBy | 35 | 92 | 1 | 14 | **10** | – |
| companyTradesAtStockExchange | 20 | 55 | 1 | 10 | 0 | – |
| countryLandBordersCountry | 24 | 36 | 1 | 14 | 0 | – |
| hasArea | 16 | 12 | 1 | 6 | 0 | – |
| hasCapacity | 17 | 13 | 1 | 6 | 0 | – |
| personHasCityOfDeath | 19 | 12 | 1 | 8 | 0 | CANDIDATE_FREE=1 |

`awardWonBy` is the only relation Table 6 marks *hard-reserved high*, and it is
the only one that receives a non-zero `verification_reserve` — §9.3's floor that
discovery may never consume, and §17.1's "verification reserve is unused" failure
made structurally impossible. Borders receives no extra reserve, matching
Table 6's *low / low-spot*. The numbers themselves are quantiles of observed
spend; nothing was chosen to improve a score, and `test_a_special_reserve_is_only_derived_where_table_6_declares_one`
pins that TRAIN may size a reserve but never create one.

**§17.** All six estimates and the successor distribution are derived per
`(relation, program_type, state_bin, family)`. `utility()` is untouched, and the
strict rule `U > τ_continue` is preserved with `τ_continue = 0.0` — every term
is expressed in *expected verified objects*, so `U > 0` means "expected to add
more correct objects than it costs" and a break-even action stops, which is what
the strictness is for.

**A scope limitation, stated rather than buried.** Module 20's ledger meters
only precharged Layer-4 actions, so `discovery_cap` and `verification_cap`
describe Layer-4 spend; `hard_calls` is the whole-query ceiling recovered from
the control state's query-scoped counters. This is why `discovery_cap` is 1 for
every relation: the only DISCOVERY-class Layer-4 action is candidate-free
recall. The distinction is written into the artifact itself under `"scope"`, and
into the report, because an operator reading `discovery_cap: 1` months later
must not read it as "one discovery call per query".

---

## 4. No neural model, no training

- Grep over all three new files for `build_runtime`, `HuggingFaceRuntime`,
  `from_pretrained`, `torch`, `score_labels`, `.generate(` — **no hits**.
- Grep for `optimizer`, `backward`, `torch.optim`, `requires_grad`, `lora`,
  `peft`, `fit(`, `gradient` — **no hits**.
- `test_the_derivation_cannot_reach_a_model_runtime` pins the first list.
- `test_the_cli_never_builds_a_model_runtime` replaces
  `registry.build_runtime` with a function that raises, and the CLI still
  returns 0 — the guarantee is asserted by making a violation fatal, not by
  reading the source.
- Every derived number is a mean, a count, a nearest-rank quantile or a ratio
  of two observed totals. There is no objective, no sweep and no seed, so
  re-running cannot produce a *better* number — only the same one.

---

## 5. No VAL, no TEST

- The CLI has no VAL/TEST path: `--config` must declare `split: train`
  (checked against `integration_mode.CALIBRATION_SPLIT`), and the manifest must
  declare `integration_mode: train_calibration_collection_only`.
- `_reject_non_train` refuses any input path whose own name says `val`,
  `validation` or `test` before the file is read.
  `test_a_val_shaped_path_is_refused` / `test_a_test_shaped_path_is_refused`.
- A renamed VAL file is caught instead by the TRAIN hash and row-count checks
  (`test_a_train_gold_that_is_not_the_collected_split_is_refused`).
- Nothing in the milestone calls `load_dataset` for any split.

---

## 6. Determinism

The bin key must be computable identically on two sides: offline from a recorded
`ControlStateFeatures`, and at inference from a live `PlannerStateSnapshot`. One
`StateBinningSpec` is shipped and both read it;
`test_offline_and_runtime_bin_keys_agree` asserts the two functions return the
same string for the same state. If they ever diverge, every derived bin becomes
unreachable and the planner raises on the first action it ranks — so the
equality is pinned rather than assumed.

Reproducibility, measured at full scale: two independent CLI invocations over
the same 477-row inputs produced **byte-identical** production artifacts.

```
IDENTICAL m20_relation_budget.json
IDENTICAL m21_historical_bins.json
IDENTICAL m21_planner_calibration.json
```

Achieved structurally: explicit ordering everywhere, fixed 6-dp rounding with
`-0.0` normalised, `sort_keys` on serialisation, and **no timestamp in any
production artifact** (`test_artifacts_carry_no_timestamp`).

---

## 7. No gold leakage

The decisive check, run over the full-scale artifacts against the **entire**
TRAIN split — 20 864 gold surfaces and 468 subjects — using both exact-equality
and whole-word matching over every string leaf and key:

```
exact-equality leaks: 0    whole-word leaks: 0
```

Structural, not incidental:

- `gold_join` converts candidate identities into per-action *counts*
  (`supported_correct`, `supported_incorrect`, …). Counts are all that cross
  into the derivation; no object string can.
- `assert_no_leakage` walks every artifact before it is written and refuses
  `ObjectEntities`, `SubjectEntity`, `gold`, `aliases`, `prompt`, `raw_output`
  and every `candidates_*` field — so a future field named `gold` cannot reach
  production by being overlooked in review. It also refuses a non-finite float.
- Bins carry `target_class = ""` universally. M17's target class is a candidate
  key, and binning on it would embed entity identity; the near-miss distinction
  is kept in the report instead.
- No subject or entity feature is used in the binning spec
  (`test_no_subject_identity_feature_is_used`), and there is no query lookup
  table: bins are keyed on `(relation, program_type, residual bucket,
  unresolved-mass bucket)`.

An earlier version of my own verification script produced 30-odd false
positives by substring-matching two-character gold aliases such as `th` and
`ng` inside ordinary schema words like `binning_spec_version`. That was a defect
in the check, not a leak; both the script and the repository test were corrected
to inspect string leaves with whole-word matching.

---

## 8. Provenance

Every artifact carries the same block, and the three blocks are asserted equal:

```
collection_repo_sha        264c980361a513078903526440c72adc6e10edaf
derivation_repo_sha        264c980361a513078903526440c72adc6e10edaf
train_sha256               cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74
train_rows                 477
predictions_sha256 / telemetry_sha256 / manifest_sha256
experiment_config_sha256   a6792c95407fec6a…
evaluator_sha256           2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
telemetry_schema_version   train-telemetry-v3
derivation_schema_version  train-calibration-v1
m20_derivation_version     m20-derivation-v1
m21_derivation_version     m21-derivation-v1
binning_spec_version       m21-state-binning-v1
relation_catalogue         all six
collection_policy_version  collect-v1
derivation_settings        {budget_quantile, minimum_bin_support, state_quantiles, …}
support_counts             {queries 477, considered 16016, executed 2352, bins 42, transitions 1875}
```

The evaluator SHA matches the pinned value stated for this milestone exactly.

**A real defect was found and fixed during this audit.** The first full-scale
run wrote `collection_repo_sha: unknown` and `derivation_repo_sha: unknown` —
`git rev-parse` had been run against the current working directory, which for a
derivation launched from an artifact folder is not a repository. An artifact
whose provenance says "unknown" binds to nothing. Fixed by resolving HEAD
against `REPO_ROOT`, and by **failing closed** when either SHA is absent or the
placeholder: `test_a_collection_with_no_repo_sha_is_refused` and
`test_both_repo_shas_are_recorded`. The guard was then observed to bite on a
manifest that genuinely lacked one.

*Related observation, not fixed here:* `run_train_calibration_collection.py`'s
own `_repo_sha()` has the same cwd fragility. The real collection was launched
from the repository and recorded `264c980…` correctly, so no artifact is
affected; noted for whoever next touches that runner.

The collection manifest does not hash its sibling artifacts, so
`--expect-telemetry-sha256`, `--expect-predictions-sha256` and
`--expect-manifest-sha256` were added: optional, and checked exactly when
supplied, so the owner can bind the documented hashes on the real run.

**Production artifacts contain no** TRAIN gold, query answers, raw telemetry,
prompts, model outputs, or VAL/TEST information. Total size 34 KB across the
three files.

---

## 9. Module 20 ownership

`derive_m20` produces `RelationBudgetCalibration` instances, so the dataclass's
own invariants run: caps ≤ `hard_calls`, reserve ≤ `verification_cap`, protected
floors ≤ ceiling, no duplicate purpose, nothing negative. When the derived
floors would exceed the ceiling, the *optional* special reserves are shed first
and §9.3's hard verification reservation is shed last — and if it alone would
not fit, the derivation raises rather than quietly shrinking it.

`test_budgets_reload_through_the_production_loader` round-trips the artifact
through `load_calibrations`, which is the function Module 20 will use, and which
refuses a `SYNTHETIC_TEST` source. The derivation stamps `TRAIN_CALIBRATED`.

## 10. Module 21 ownership

`derive_m21` constructs real `HistoricalActionBin`s, so every §17 estimate is
range-checked by the module that owns it, and successor probabilities must sum
to 1.0 — rounding drift is absorbed deterministically by the largest branch.

Sparse-bin handling is explicit: a bin below `minimum_bin_support` is **not
shipped**, because its observations are already in the relation's fallback bin
and shipping it would present a mean of two observations as an estimate. The
package declares `fallback_state_bin`, so `lookup` resolves for any unseen
state and a legal action is never silently dropped from the ranking
(`test_every_action_family_resolves_a_bin`,
`test_a_sparse_bin_falls_back_rather_than_shipping_a_mean_of_one`).

Redundancy uses a documented fallback hierarchy — bin → relation → run → 0.0 —
so a bin with no `MEASURED` observation borrows a real observation rather than
defaulting to zero.

Coefficients are ratios of observed totals, not a search: `α = 1` (the
numeraire), `κ = 1` (F1's own margin), `β` = verified objects per unit residual
reduction, `δ` = verified objects per physical call, `η` = mean verified gain
per action, `τ_continue = 0`. Full-scale values: `β 0.435, δ 0.006392,
η 0.01233, lookahead_depth 2`.

`test_the_history_artifact_reloads_and_ranks` reloads through `load_history` and
feeds the result to the real `utility()`.
`test_the_readiness_gate_accepts_the_generated_artifacts` shows the generated
files drive `evaluate_readiness` to `FULL_VALIDATION_READY` — the gate a later
production milestone must pass.

---

## 11. C-02 treatment

Measured, not assumed, and not manufactured. Over 2 352 executed actions in the
full-scale run:

```
0 of 2352 executed actions moved H; structurally zero = True
```

Every bin therefore carries `expected_delta_h = 0.0`, and `γ = 0.0` — the honest
representation of a term that multiplies nothing. The report says so in prose,
naming the mechanism: `H` is a function of acquisition-group coverage, and no
Module 17/18 action changes an acquisition group.

Critically, **γ is inert because ΔH is zero, not because the code forces it**.
`test_gamma_is_estimated_when_h_really_moves` replays the same fixture with H
genuinely moving and asserts `γ > 0` and
`gamma_is_inert_because_delta_h_never_moved is False`. Nothing was altered to
make ΔH non-zero.

---

## 12. Fail-closed inputs

Each asserted against the real guard:

| condition | test |
|---|---|
| wrong split in `--config` | `test_a_non_train_config_is_refused` |
| VAL / TEST shaped path | `test_a_val_shaped_path_is_refused`, `..._test_..._refused` |
| TRAIN hash mismatch | `test_a_train_gold_that_is_not_the_collected_split_is_refused` |
| wrong row count (real 477 guard) | `test_a_row_count_that_is_not_the_official_split_is_refused` |
| tampered artifact hash | `test_a_tampered_telemetry_file_is_refused` |
| operator-asserted hash mismatch | `test_an_asserted_hash_that_does_not_match_is_refused` |
| missing collection repo SHA | `test_a_collection_with_no_repo_sha_is_refused` |
| collection failed its own gate | `test_a_collection_that_failed_its_own_gate_is_refused` |
| collection not sufficiency-PASS | `test_an_insufficient_collection_is_refused` |
| unresolved failed rows | `test_an_unresolved_failed_row_is_refused` |
| wrong integration mode | `test_a_shadow_mode_collection_is_refused` |
| malformed JSONL | `test_malformed_telemetry_is_refused`, `test_malformed_gold_is_refused` |
| duplicate query identity | `test_a_duplicate_query_identity_in_gold_is_refused` |
| unsupported telemetry schema | `test_an_unsupported_telemetry_schema_is_refused` |
| empty telemetry / no executed action | two tests |
| action with no gold row | `test_an_action_with_no_gold_row_is_refused` |
| non-finite value | `test_the_leakage_guard_refuses_a_non_finite_number` |
| unknown relation / family / state feature | three tests |
| missing input file | `test_a_missing_input_is_refused` |

67 tests across the two new files.

---

## 13. Evaluator semantics

Correctness labels come from the pinned evaluator itself, never a local
re-implementation: `gold_join.GoldIndex.label` calls
`benchmark/evaluate.py::true_positives` with a one-element prediction list, so
alias handling, unicode normalisation and the 5 % numeric tolerance are its
rules. Pre-calibration official TRAIN metrics come from
`evaluation/harness.evaluate_files`, the same path the experiment pipeline uses.

The report's metrics from the full-scale run read 0.000 across the board,
correctly: the scripted runtimes answer fictional constants, so almost nothing
matches gold. That the evaluator ran, and that a handful of scripted answers did
match (`η = 0.0123 > 0`), is what the test demonstrates — not a score.

---

## 14. What was actually run

| stage | status |
|---|---|
| **IMPLEMENTED** | yes — 3 new source/script files, 0 existing files modified |
| **UNIT-TESTED** | yes — 43 tests in `test_train_calibration_derivation.py` |
| **PIPELINE-TESTED with fixture telemetry** | yes — 24 tests driving the real `main()` end to end over a real six-relation collection |
| **PIPELINE-TESTED at full 477-row scale** | yes — real collection runner, committed TRAIN config, all 477 rows, 16 016 telemetry records, 2 352 executed actions, 1 875 transitions, sufficiency PASS; real CLI unmodified; real 477-row gold; real pinned evaluator; artifacts byte-identical across two runs |
| **REAL TRAIN DERIVATION READY** | yes |
| **REAL TRAIN DERIVATION ACTUALLY RUN** | **NO** — the 134 MB frozen-model telemetry is not in this environment |
| **PRODUCTION-ACTIVE** | **NO** — no validation config references these artifacts; `IntegrationMode.PRODUCTION` still has no entrypoint |

Only the model runtimes were substituted in the full-scale test. Everything
else — runner, config, split, gold, evaluator, guards, artifacts — was real.

To run it on the preserved frozen-model artifacts:

```
python scripts/derive_train_calibration.py \
  --config configs/experiments/cover_kbc_v2_train_collection.yaml \
  --train-gold benchmark/data/train.jsonl \
  --predictions <RUN>/predictions.jsonl \
  --telemetry   <RUN>/train_telemetry.jsonl \
  --manifest    <RUN>/manifest.json \
  --output-dir  configs/calibration \
  --expect-predictions-sha256 1fe0ac17787af0fb68036b248f01b4e625350f359e02fe6459039034a9b276aa \
  --expect-telemetry-sha256   fa95b30762a93537f7e03c87143ff6b7cfd71ff48eab80194d21089493b2b9ed \
  --expect-manifest-sha256    54f8eb423d01601c736dd358ccfb2825c02b0a2fd696fc79db8a2ad27e22777c
```

Run it from the repository root so both repo SHAs resolve. The three
`--expect-*` flags are optional but recommended: they bind the run to the
hashes recorded when the collection was preserved. `--budget-quantile` and
`--minimum-bin-support` exist for sensitivity analysis and should be left at
their defaults for the artifact that ships.

---

## 15. Exact blockers

**Before the real TRAIN derivation:** none. The preserved artifacts must be
present and the command above run from the repository root.

**Before production activation (a later milestone):**

1. The three artifacts must exist, derived from the real frozen-model telemetry.
2. **F-24** — an entrypoint constructing the pipeline with
   `IntegrationMode.PRODUCTION`; none exists.
3. **F-11** — `MicroPlannerConfig` and `RelationBudgetConfig` still raise on any
   non-`shadow` mode, so no config can express `production`.
4. **F-22** — `layer6_integrator` must be supplied, or M21 receives an empty
   legal-action list and always returns `STOP/NO_LEGAL_ACTION`.
5. A validation config (`split: val`, M11–M21 enabled, all three artifacts
   declared) reaching `FULL_VALIDATION_READY`.
6. Real-weight validation behaviour is unmeasured. The derived `hard_calls`
   ceilings come from a collection whose Layer-4 bound was
   `max_control_rounds_per_catalogue: 3`; a production run that plans more
   rounds would be bounded by numbers derived under a different regime.
7. **C-02 disclosure** — the paper must state that §17's `γ·ΔĤ` term is
   calibrated to identically zero over the collected action space, rather than
   present a six-term utility as fully estimated.

**Not a blocker but worth deciding:** `discovery_cap = 1` for every relation is
correct for Layer-4 metering, but if a later milestone routes acquisition
through the ledger, these caps must be re-derived — they do not describe
acquisition spend and the artifact says so.

---

## 16. Verdict

> **PASS — DERIVATION PIPELINE COMPLETE AND VALIDATED AT FULL SCALE.
> REAL TRAIN DERIVATION NOT YET RUN. PRODUCTION ACTIVATION NOT DONE.**

The derivation is proposal-compliant: Table 6's qualitative policy is read from
its owner and only its concrete values are calibrated; §17's six estimates,
successor frequencies and all seven coefficients are derived from observed
totals with the strict continuation rule preserved. It calls no model, reads no
VAL or TEST, and leaks no gold — checked against all 20 864 TRAIN gold surfaces
with zero hits. It is byte-reproducible at full scale, binds to its collection
through fourteen provenance fields, and fails closed on twenty distinct input
defects. Two real defects found during the work — unresolvable repo SHAs written
as `unknown`, and a leakage check that produced false positives — were fixed
rather than argued away.

C-02 is represented truthfully: ΔH is zero because the action space cannot move
it, γ is 0.0 for that reason, and a test proves γ would be estimated if H ever
moved.

Production activation remains NOT DONE, as this milestone expected.

---

*No commit, no push. `benchmark/` untouched. No model weights were loaded; no
TRAIN, VAL or TEST inference was run.*
