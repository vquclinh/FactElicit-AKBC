# Audit 0067 — V3A Failure Attribution and Relation Profile Layer

V3A_BASE_SHA:
75381677559058938e65ce706c1bce02624e381d

Handoff note: implementation began in another agent session and was
independently completed/reverified from the dirty working tree. The dirty tree
was preserved; no reset, checkout, stash, commit, push, benchmark rewrite,
calibration regeneration, TEST inference, or TEST evaluation was performed.

## Scope

V3A is diagnostic only. It adds a relation profile layer, gold-free inference
telemetry, TRAIN-only offline attribution, a report generator, a TRAIN
diagnostic readiness gate, an opt-in runner path, and focused tests. It does
not alter production prediction semantics, prompts, model strategy, M20/M21
calibration, benchmark data, evaluator code, or the output contract.

## Files Changed

Modified tracked files:

- `scripts/run_cover.py`
- `src/cover_kbc/contracts/__init__.py`
- `src/cover_kbc/contracts/router.py`
- `src/cover_kbc/controller_calibration/gold_join.py`
- `src/cover_kbc/controller_calibration/readiness.py`
- `src/cover_kbc/elicitation/parsing.py`
- `src/cover_kbc/pipeline.py`

Added files:

- `configs/experiments/cover_kbc_v2_train_diagnostic_v3a.yaml`
- `scripts/analyze_failure_attribution.py`
- `src/cover_kbc/contracts/relation_profile.py`
- `src/cover_kbc/diagnostics/__init__.py`
- `src/cover_kbc/diagnostics/failure_state.py`
- `src/cover_kbc/diagnostics/gold_attribution.py`
- `src/cover_kbc/diagnostics/inference_telemetry.py`
- `src/cover_kbc/diagnostics/report.py`
- `src/cover_kbc/diagnostics/stages.py`
- `tests/test_v3a_failure_attribution.py`
- `docs/audits/0067-v3a-failure-attribution-relation-profiles.md`

## Relation Profiles

Six immutable M1-owned `RelationProfile` records exist, exactly one per official
relation:

| Relation | family | primary_failure | semantic_risk | set_behavior | recall_policy | verification_policy | search_bias | frozen |
|---|---|---|---|---|---|---|---|---|
| `hasArea` | `NUMERIC_SINGLE` | `MISSING_RECALL` | `AREA_DEFINITION` | `SINGLE` | `MULTI_VIEW` | `NUMERIC_CONTRAST` | `NEUTRAL` | false |
| `hasCapacity` | `NUMERIC_SINGLE` | `MEMORY_AMBIGUITY` | `CAPACITY_VARIANT` | `SINGLE` | `MULTI_VIEW_DEFINITION_AWARE` | `DEFINITION_CONTRAST` | `NEUTRAL` | false |
| `companyTradesAtStockExchange` | `ENTITY_SET` | `FALSE_POSITIVE` | `OWNERSHIP_LISTING` | `SMALL_SET` | `BASELINE` | `REJECTION_FIRST` | `ELIMINATION` | false |
| `personHasCityOfDeath` | `ENTITY_SINGLE` | `ATTRIBUTE_CONFUSION` | `RELATED_LOCATION` | `ZERO_OR_ONE` | `ATTRIBUTE_CONTRAST` | `SEMANTIC` | `NEUTRAL` | false |
| `awardWonBy` | `ENTITY_SET` | `INCOMPLETE_SET` | `SET_COMPLETENESS` | `OPEN_SET` | `PROMOTE_SUPPRESS_ITERATE` | `UNARY` | `EXPANSION` | false |
| `countryLandBordersCountry` | `STRUCTURAL_SET` | `LOW` | `LAND_BORDER_SCOPE` | `SMALL_SET` | `BASELINE_CONSERVATIVE` | `BASELINE` | `NEUTRAL` | true |

The representation is frozen dataclasses plus a `MappingProxyType` registry.
Unknown relations fail closed. `check_profile_consistency()` verifies the
profiles against M0 contracts and is called from router consistency checking.

Declarative-only proof in V3A: `get_relation_profile` and `route_profile` are
used only in `contracts` and `diagnostics`; no production action, controller,
selection, prompt, verifier, or scoring path reads a profile to alter output.
The focused test `test_relation_profiles_are_declarative_only_in_v3a` enforces
that source boundary.

## Inference/Gold Boundary

Inference telemetry is physically separate from attribution:

- `DiagnosticRecorder.observe(graph, prediction, action_records=...)` has no
  gold argument and is called only after a prediction already exists.
- `QueryInferenceRecord` and `CandidateObservation` have no gold fields and no
  `ObjectEntities` label channel.
- Telemetry copies primitives from finished `EvidenceGraph`, `Prediction`,
  generation records, candidates, and Layer-4 action records.
- The recorder does not mutate `EvidenceGraph`, `Prediction`, budgets,
  accounting, Module 8, M20, or M21.
- Raw model payloads are not duplicated; generation observations keep trace
  IDs plus deterministic parsed fragment/value summaries.

TRAIN gold is joined offline by `TrainGoldAttribution`, which refuses every
record whose split is not exactly `train` and refuses blind/all-empty gold
indices. It uses `GoldIndex.gold_assignment()` backed by the pinned official
evaluator semantics for string matching, alias collapse, maximum matching, and
numeric tolerance.

## Observable Stages

The six observable stages are:

- `ACQUIRED`: `acquisition_fragments(GenerationRecord.raw_output)` for every
  generation record registered before finalization, including later
  candidate-producing recall actions. Numeric extraction uses the production
  low-level numeric scanner plus deterministic unit conversion; entity
  extraction uses `parse_entities()` so arbitrary prose and `UNKNOWN` do not
  inflate oracle recall.
- `NORMALIZED`: `EvidenceGraph.candidates` excluding hard-contract rejections.
- `VERIFIER_REACHED`: candidates with any `VerificationResult`.
- `VERIFIER_ACCEPTED`: candidates whose latest scored verifier label is
  `VALID`, matching `scoring.decide_status`.
- `CONTROL_SURVIVED`: candidates whose final candidate status is `ACCEPTED`.
- `FINAL_EMITTED`: `Prediction.object_entities`.

Not truly observable:

- A raw entity "pre-normalization object" before parser shape rejection is not
  recoverable without fabricating candidates from prose. V3A therefore treats
  entity acquisition as parser-recoverable object surfaces.
- There is no separate controller-drop stage. M20 reserves/accounting compute
  and M21 chooses actions/STOP; neither directly removes candidates. Candidate
  removal after verification is observed through acceptance policy status and
  final selection.
- Rows with no trustworthy graph become `NOT_OBSERVABLE`; absence is not
  re-labeled as `NEVER_ACQUIRED`.

Scripted smoke after the final fixes:

- prediction: `["Germany", "Poland"]`
- `ACQUIRED`: `["Germany", "Poland"]`
- `NORMALIZED`: `["Germany", "Poland"]`
- `VERIFIER_REACHED`: `[]`
- `VERIFIER_ACCEPTED`: `[]`
- `CONTROL_SURVIVED`: `["Germany", "Poland"]`
- `FINAL_EMITTED`: `["Germany", "Poland"]`
- calls: `2`; generated tokens: `5`; verifier calls: `0`

This is legitimate baseline behavior: no verifier candidate was reached, but
sufficient acquisition support allowed the acceptance policy to keep the two
candidates through final selection.

## Attribution Semantics

OracleCandidateRecall is reported two ways over non-empty-gold queries only:

- micro object-level: gold objects present in the pre-final candidate pool over
  total gold objects;
- macro query-level: mean of per-query acquired-gold fractions.

The pre-final candidate pool is the union of `ACQUIRED`, `NORMALIZED`,
`VERIFIER_REACHED`, `VERIFIER_ACCEPTED`, and `CONTROL_SURVIVED`, excluding
`FINAL_EMITTED` so a derived Module 8 numeric representative cannot inflate the
oracle ceiling. Later candidate-producing actions count only when they produced
registered generation records before finalization.

Set-valued relations remain object-level. A row with gold `{A,B,C}` and a stage
containing `{A,C,X}` records `gold_present_count = 2` and
`gold_fraction = 2/3`, not a boolean success.

Empty TRAIN gold rows are reported separately with `empty_gold_query_count`,
`acquisition_empty`, `proposed_a_candidate`, and `final_correctly_empty`; they
never enter a recall denominator.

Gold failure categories:

- `NEVER_ACQUIRED`
- `LOST_IN_NORMALIZATION`
- `NOT_SENT_TO_VERIFIER`
- `REJECTED_BY_VERIFIER`
- `DROPPED_AFTER_VERIFICATION`
- `DROPPED_BY_FINAL_SELECTION`
- `SUCCESSFULLY_EMITTED`
- `NOT_OBSERVABLE`

False-positive categories are assigned by the latest observable stage reached:

- `ACQUISITION_FP`
- `NORMALIZATION_ALIAS_FP`
- `VERIFIER_FALSE_ACCEPT`
- `CONTROL_FP_SURVIVAL`
- `FINAL_FP`

Failure-state vocabulary is deterministic and shadow-only:

- `NO_CANDIDATE`
- `SINGLE_LOW_SUPPORT`
- `MULTIPLE_CONFLICTING`
- `HIGH_FP_RISK`
- `SET_GROWING`
- `SEMANTIC_AMBIGUITY`
- `STABLE_VERIFIED`
- `NULL_UNRESOLVED`

Module 21 does not consume this state in V3A. Source tests verify
`FailureSearchState` and `derive_failure_state` are absent from `src/cover_kbc/control`.

## Runner, Readiness, Analyzer

Diagnostics are opt-in. Normal `run_cover.py` behavior is unchanged unless
`diagnostics.enabled: true` and `diagnostics.telemetry_file` are declared.
Telemetry is written after normal prediction and trace artifacts, with exactly
one record per completed prediction.

`PRODUCTION_GATES` remains leaderboard-only (`val`, `test`). TRAIN diagnostics
use a separate `TRAIN_DIAGNOSTIC_GATE` only for an explicit diagnostics-enabled
`train` run of the calibrated baseline.

The TRAIN diagnostic readiness gate verifies:

- split is TRAIN;
- expected TRAIN row count/hash/ordered identity;
- diagnostics are enabled and telemetry output is explicit;
- active frozen two-model architecture and revisions;
- total parameter budget `28,671,226,368 / 32,000,000,000`;
- M20/M21 calibration artifacts load through their production owner;
- TEST is not used and TEST evaluation is not invoked;
- no `v3b_features` are enabled.

The ready real-weight command for a later A100 run:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v2_train_diagnostic_v3a.yaml \
  --output-dir outputs/v3a_train_diagnostic_real

python scripts/analyze_failure_attribution.py \
  --telemetry outputs/v3a_train_diagnostic_real/inference_telemetry.jsonl \
  --output-dir outputs/v3a_train_diagnostic_real/failure_attribution \
  --split train \
  --expected-train-rows 477 \
  --expected-train-sha256 ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e
```

The analyzer performs zero neural calls, accepts TRAIN only, loads pinned TRAIN
gold through existing gold/evaluator semantics, refuses VAL/TEST telemetry,
verifies TRAIN row count and SHA256, and writes:

- `failure_attribution.json`
- `failure_attribution.md`
- `failure_attribution.csv`

The JSON output is the authoritative machine-readable report.

REAL TRAIN FAILURE ATTRIBUTION:
PENDING A100 REAL-WEIGHT RUN

No 477-row real-weight TRAIN failure statistics were produced locally.

## Zero-Change Evidence

Focused scripted tests compare diagnostics OFF and ON:

- serialized official prediction rows are identical;
- byte-equivalent predictions JSONL is identical;
- query order is identical;
- `empty_reason` and `stopped_reason` are identical;
- total neural calls, generated tokens, and prompt tokens are identical;
- diagnostics add no verifier calls.

Additional tests cover `run_query()` and staged `decide()` so each completed
query records exactly once and enumerate-only paths record nothing.

## Verification

Targeted tests:

- `python -m pytest tests/test_v3a_failure_attribution.py -q -p no:randomly`
  - `30 passed in 3.32s`
- `python -m pytest tests/test_staging.py tests/test_controller_conformance.py tests/test_production_activation.py -q -p no:randomly`
  - `155 passed, 1 skipped in 4.45s`
- `python -m pytest tests/test_architecture_conformance.py::test_no_module_22_and_no_dola tests/test_official_test_activation.py::test_the_runner_maps_each_split_to_its_own_gate tests/test_official_test_activation.py::test_the_runner_has_no_production_path_for_any_other_split tests/test_v3a_failure_attribution.py -q -p no:randomly`
  - `33 passed in 3.41s`

Full suites:

- `python -m pytest tests/ -q -p no:randomly`
  - `3518 passed, 4 skipped in 65.64s`
- `python -m pytest tests/ -q`
  - `3518 passed, 4 skipped in 66.77s`

Static checks:

- `python -m pyflakes src/ tests/ scripts/`
  - clean, exit 0
- `git diff --check`
  - clean, exit 0

## Immutable Artifacts

Active model architecture remains:

- enumerator: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- enumerator revision:
  `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- verifier: `Qwen/Qwen3.5-4B`
- verifier revision:
  `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- parameter budget: `28,671,226,368 / 32,000,000,000`

Calibration artifact hashes:

- M20 `configs/calibration/m20_relation_budget.json`:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- M21 historical bins `configs/calibration/m21_historical_bins.json`:
  `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- M21 planner calibration `configs/calibration/m21_planner_calibration.json`:
  `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

Benchmark and evaluator hashes:

- `benchmark/evaluate.py`:
  `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN rows/hash:
  `477`,
  `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- TRAIN ordered identity:
  `04b56aa6f401f00ca8d672a8d6cade09cfa2492640aac16a9aab8bc42c1b8054`
- VAL rows/hash:
  `475`,
  `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- VAL ordered identity:
  `3968a5bad12b4afb66b9ace7c1ab8e4ee76a0f69608c75af33eb84e380f9bc0d`
- TEST rows/hash:
  `475`,
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`
- TEST ordered identity:
  `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`
- TEST rows with `ObjectEntities`: `0`; TEST remains blind.

Retired path scan:

- strict executable-token scan for `DIRECT_UNCALIBRATED`, `ModelStrategy`,
  `model_strategy`, `Gemma12`, `Nemotron9`, and `Qwen3.5-9B` over
  `src`, `scripts`, and `configs/experiments` returned no matches.
- the broader word `portfolio` appears only in a `models/preflight.py`
  explanatory docstring, not as an executable strategy path.

## Verdict

PASS — V3A FAILURE ATTRIBUTION AND RELATION PROFILES READY
