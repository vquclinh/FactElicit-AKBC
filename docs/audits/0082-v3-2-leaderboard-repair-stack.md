# Audit 0082 - V3.2 Leaderboard Repair Stack

**Status: PHASE A - IMPLEMENTED, TRAIN_ABLATION_GPU_RUN_REQUIRED**

Primary objective: maximize held-out leaderboard score under the declared
competition constraints.  This milestone adds a downstream L7-L9 repair stack;
it does not destructively rewrite M1-M21, does not add a third model, and does
not force a new M21 before post-layers are measured.

## Constraints

No web, no RAG, no external factual corpus, no TEST ground truth, no
subject-answer lookup table, no fine-tuning, no third neural model.  The stack
uses only the existing Mistral24 enumerator and Qwen4 verifier runtimes.

Previous blind TEST predictions are used only for output-shape and
failure-pattern discovery.  TRAIN gold remains the permitted ablation source.

## Prior Evidence

Audit 0081 stock result:

| metric | SAFE_CORE | stock prompt |
|---|---:|---:|
| macro-F1 | 0.52890 | 0.61967 |
| macro-P | 0.62750 | 0.86000 |
| macro-R | 0.74333 | 0.66500 |
| TP / FP / FN | 48 / 95 / 33 | 39 / 20 / 42 |
| exact rows | 30 | 56 |
| candidate pool | 908 | 288 |

Inference: `stock_listing_entity_prompt` is high-value for leaderboard work and
must stay in all V3.2 leaderboard variants.  The repair target is the measured
multi-listing / recall loss, not the prompt itself.

## Blind TEST Output-Shape Census

Source: `outputs/v3_test_16f60fb1_20260810T160048Z/run/predictions.jsonl`.
This file contains no TEST labels.

| relation | rows | empty | mean card | max card | shape signal |
|---|---:|---:|---:|---:|---|
| `awardWonBy` | 10 | 0 | 62.800 | 117 | extreme cardinality / metadata labels |
| `companyTradesAtStockExchange` | 100 | 39 | 1.860 | 41 | type leakage and two extreme-cardinality rows |
| `countryLandBordersCountry` | 67 | 11 | 3.851 | 13 | alias and reciprocity-risk surface |
| `hasArea` | 100 | 71 | 0.290 | 1 | empty rescue target |
| `hasCapacity` | 98 | 25 | 0.745 | 1 | exact-venue / numeric conflict target |
| `personHasCityOfDeath` | 100 | 98 | 0.020 | 1 | existence-gated recall target |

Structural anomaly counts from
`outputs/v3_2_weakness_mining/test_blind_structural_anomalies.csv`:

| anomaly | count |
|---|---:|
| `EMPTY_OUTPUT` | 244 |
| `WIDE_NUMERIC_CANDIDATE_SPREAD` | 127 |
| `SUSPICIOUS_ENUMERATION_LABEL` | 84 |
| `ORCHESTRATION_FAILURE_HOLE` | 39 |
| `EXTREME_CARDINALITY` | 2 |

These are shape triggers only.  No TEST row is labelled correct or incorrect.

## Architecture Added

`src/cover_kbc/leaderboard_repair/`

* L7 Relation-Specific Repair
  * `StockExchangeEntityGuard`
  * `StockAliasDeduplicator`
  * `StockMultiListingRescue`
  * `BorderDirectionalSweep`
  * `DeathExistenceGate`
  * `DeathCityRecall`
  * `AreaEntityTypeProfiler`
  * `AreaEmptyRescue`
  * `CapacityRepair`
  * `NumericAttributeResolver`
  * `AwardMetadataNormalizer`
  * `AwardRecipientWitness`
  * `AwardTimeSlicedRecall`
* L8 Cross-Query Consistency
  * border reciprocity conflict detection
  * verified reciprocal addition / invalid-direction removal / UNKNOWN preserve
  * alias and output-type consistency
* L9 Relation-Specific Final Risk Guard
  * Borders: completeness-oriented, remove self and aliases
  * Stock: precision-oriented, type guard plus alias dedupe, no cardinality cap
  * Award: metadata cleanup and witness-aware filtering
  * Area/Capacity: numeric output only, singleton
  * City: life-status-aware singleton

The stack runs after the existing final prediction.  Disabled L7-L9 is
byte-identical to the frozen current output path.

## Accounting

Repair calls are recorded separately:

* `leaderboard_repair.jsonl` - one row ledger with decisions, prompts, outputs,
  restored/rejected candidates, verifier confidence, and skipped-budget reasons.
* `repair_accounting.json` - total/mean/max calls and relation-level call cost.

Repair calls are not written into historical M20/M21 calibration artifacts and
do not re-derive M20 or M21.

Default caps are configurable:

| relation | cap |
|---|---:|
| Borders | 3 |
| Stock | 2 |
| Area | 3 |
| Capacity | 3 |
| City | 4 |
| Award | 8 |

Profile C raises City to 5 so the required status+two-recall+contrast path is
executable when recall views disagree.

## TEST Profiles

All three profiles keep the frozen Mistral24 + Qwen4 model pair and produce a
full official TEST file when run.  They are explicit calibration-review
leaderboard probes, not `FULL_TEST_READY` production profiles, because the
stock prompt is an acquisition prompt.

| profile | config | purpose |
|---|---|---|
| A | `configs/experiments/cover_kbc_v3_2_profile_a_stock_probe_test.yaml` | frozen current system + `stock_listing_entity_prompt` |
| B | `configs/experiments/cover_kbc_v3_2_profile_b_repair_core_test.yaml` | A + stock guard/dedupe/rescue + award cleanup + border alias/reciprocity |
| C | `configs/experiments/cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml` | B + border sweep + city/death recall + area/capacity numeric rescue + award witness/time slices |

Readiness status for A/B/C: **CALIBRATION_REVIEW_LEADERBOARD_PROBE**.  The
standard `evaluate_test_readiness` report remains `NOT_READY` with
`CALIBRATION_REVIEW_REQUIRED`; `run_cover.py` allows these profiles only through
the explicit `leaderboard_probe` declaration and still prints the accepted
blockers.

## TRAIN Ablation Plan

Analyzer: `scripts/analyze_v3_2_repair_ablations.py`.

Required isolated variants:

| id | variant | status |
|---|---|---|
| A | stock prompt existing result | measured in Audit 0081 |
| B | stock entity guard | TRAIN run required |
| C | stock alias dedupe | TRAIN run required |
| D | stock multi-listing rescue | TRAIN run required |
| E | border directional sweep | TRAIN run required |
| F | border reciprocity | TRAIN run required |
| G | city existence/death rescue | TRAIN run required |
| H | area empty rescue | TRAIN run required |
| I | capacity repair/resolver | TRAIN run required |
| J | award metadata cleanup | deterministic TRAIN ablation required |
| K | award recipient witness | TRAIN run required |
| L | all deterministic repair only | TRAIN replay required |
| M | full leaderboard stack | TRAIN run required |

The analyzer reports official macro-P/R/F1, micro-F1, per-relation results,
rows changed, rows improved, rows harmed, TP/FP/FN, empty rows and cardinality
distribution.  It accepts only supplied TRAIN prediction artifacts and labelled
TRAIN gold; it does not run neural models.

## Limitations

No new TRAIN L7-L9 neural ablation artifacts have been produced in this source
freeze.  Therefore this audit does not claim cumulative TRAIN or TEST
improvement for B/C.  Profile A is justified by the real-weight Audit 0081
stock result; Profiles B/C are engineered leaderboard probes whose repair-call
cost and macro-F1 must be measured after TRAIN or leaderboard execution.
