# Audit 0075 - V3 Train Error Forensics And Score-Recovery Map

## Scope

This audit is CPU/offline analysis only. It does not modify the frozen calibrated V3 TEST submission behavior, M20-V3, M21-V3, calibration artifacts, benchmark data, evaluator code, or immutable real-run artifacts under `outputs/`.

Frozen calibrated V3 TEST submission source:

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

Audit source/base SHA:

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

## Authoritative Inputs

Full TRAIN prediction artifact:

`outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z/predictions.jsonl`

Telemetry directory:

`outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z/`

Ground truth:

`benchmark/data/train.jsonl`

TRAIN rows: `477`

TRAIN SHA256:

`ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`

Evaluator:

`benchmark/evaluate.py`

Evaluator SHA256:

`2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`

Prediction artifact SHA256:

`36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816`

No TEST artifact, TEST gold, VAL artifact, web data, external KB, RAG, or model was read.

## Reproduced Official TRAIN Metrics

The analysis reproduced the saved official full TRAIN metrics.

| Relation | macro-P | macro-R | macro-F1 | micro-P | micro-R | micro-F1 | avg preds | empty preds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| awardWonBy | 0.423 | 0.391 | 0.389 | 0.565 | 0.453 | 0.503 | 61.200 | 0 |
| companyTradesAtStockExchange | 0.628 | 0.743 | 0.529 | 0.336 | 0.593 | 0.429 | 1.430 | 39 |
| countryLandBordersCountry | 0.987 | 0.956 | 0.964 | 0.984 | 0.944 | 0.964 | 2.836 | 12 |
| hasArea | 0.890 | 0.240 | 0.240 | 0.686 | 0.240 | 0.356 | 0.350 | 65 |
| hasCapacity | 0.210 | 0.080 | 0.080 | 0.092 | 0.080 | 0.086 | 0.870 | 13 |
| personHasCityOfDeath | 0.840 | 0.480 | 0.390 | 0.273 | 0.103 | 0.150 | 0.220 | 78 |
| All Relations | 0.686 | 0.466 | 0.403 | 0.568 | 0.476 | 0.518 | 2.283 | 207 |

The exact stock macro-P is `0.6275`; the official pandas-rendered table rounds it to `0.628`.

## Generated Forensic Artifacts

Command:

```bash
python scripts/analyze_v3_train_failures.py \
  --predictions outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z/predictions.jsonl \
  --gold benchmark/data/train.jsonl \
  --telemetry-dir outputs/v3_train_collect_v2_coverage \
  --output-dir outputs/v3_train_error_forensics
```

The script is deterministic and CPU-only. It was run twice after the final analyzer fix; `SHA256SUMS.txt` was byte-identical.

`outputs/v3_train_error_forensics/SHA256SUMS.txt` SHA256:

`ba0f8e8cf6e10c89e4e8019da3ebf6932c9ed08755b808f8bb4f02b67122d15c`

Primary artifacts:

- `summary.json`
- `per_row_errors.csv`
- `per_row_errors.jsonl`
- `relation_summary.csv`
- `failure_category_summary.csv`
- `pipeline_localization.csv`
- `cardinality_analysis.csv`
- `cardinality_matrices.json`
- `numeric_error_analysis.csv`
- `numeric_ratio_buckets.csv`
- `action_failure_analysis.csv`
- `score_loss_ranking.csv`
- `intervention_candidates.csv`
- `intervention_candidates.md`
- `counterfactual_rule_results.csv`
- `representative_cases.md`
- `top_opportunities.md`
- `analysis_provenance.json`
- `SHA256SUMS.txt`

## Row Outcome Counts

Across 477 TRAIN rows:

- exact set match: `159`
- partial match: `54`
- complete miss with gold present: `245`
- false-positive-only rows with empty gold: `19`
- empty prediction rows: `207`
- incorrect rows total: `318`

## Relation Summary

| Relation | rows | exact | partial | complete miss | empty | TP | FP | FN | mean F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| awardWonBy | 10 | 0 | 9 | 1 | 0 | 346 | 266 | 418 | 0.389 |
| companyTradesAtStockExchange | 100 | 30 | 36 | 24 | 39 | 48 | 95 | 33 | 0.529 |
| countryLandBordersCountry | 67 | 58 | 9 | 0 | 12 | 187 | 3 | 11 | 0.964 |
| hasArea | 100 | 24 | 0 | 76 | 65 | 24 | 11 | 76 | 0.240 |
| hasCapacity | 100 | 8 | 0 | 92 | 13 | 8 | 79 | 92 | 0.080 |
| personHasCityOfDeath | 100 | 39 | 0 | 52 | 78 | 6 | 16 | 52 | 0.390 |

## Cardinality Analysis

Cardinality is a major failure source.

| Relation | under rows | under pct | over rows | over pct | singleton when multi-gold | mean FN | mean FP |
|---|---:|---:|---:|---:|---:|---:|---:|
| awardWonBy | 10 | 100.0% | 10 | 100.0% | 0 | 41.8 | 26.6 |
| companyTradesAtStockExchange | 28 | 28.0% | 54 | 54.0% | 0 | 0.33 | 0.95 |
| countryLandBordersCountry | 8 | 11.9% | 3 | 4.5% | 3 | 0.16 | 0.04 |
| hasArea | 76 | 76.0% | 11 | 11.0% | 0 | 0.76 | 0.11 |
| hasCapacity | 92 | 92.0% | 79 | 79.0% | 0 | 0.92 | 0.79 |
| personHasCityOfDeath | 52 | 52.0% | 16 | 16.0% | 0 | 0.52 | 0.16 |

For `awardWonBy`, all 10 rows are under-enumerated and over-enumerated simultaneously: the system usually knows some winners, but far too few of the gold set and too many unsupported extras. The loss is both recall and precision, with recall volume dominant: `418` FNs vs `266` FPs.

For stock exchange, the dominant burden is false positives: `54` over-enumerated rows and `95` FPs. Empty predictions still matter (`39` empty rows, `16` FNs), but the larger score loss is listing over-generation.

## Numeric Error Analysis

For `hasArea`:

- empty numeric recall: `65/100`
- within 5 percent: `24/100`
- exact numeric: `9/100`
- just outside tolerance: `1/100`
- large miss: `10/100`
- scale-like error: `2/100`
- wrong non-empty predictions: `11`; among these, `4` are too small and `7` too large.

For `hasCapacity`:

- empty numeric recall: `13/100`
- within 5 percent: `8/100`
- exact numeric: `4/100`
- just outside tolerance: `4/100`
- large miss: `75/100`
- scale-like error: `1/100`
- wrong non-empty predictions: `79`; among these, `15` are too small and `64` too large.

Capacity errors are not mostly formatting failures. They are mostly wrong numeric attributes or wrong venue-scale guesses. Ratio buckets show `hasCapacity` has `16` rows around `10x` and `1` around `100x`; most wrong non-empty capacity values remain in the broad `~1` ratio bucket but outside the 5 percent tolerance.

## Pipeline Localization

Earliest unrecoverable stage counts:

| Stage | rows |
|---|---:|
| L0_CORRECT | 159 |
| L1_NEVER_RECALLED | 242 |
| L2_RECALLED_BUT_NOT_HYPOTHESIZED | 1 |
| L3_HYPOTHESIZED_BUT_REJECTED | 0 |
| L4_VERIFIED_BUT_DROPPED | 0 |
| L5_FINALIZATION_OR_NORMALIZATION_LOSS | 75 |
| L6_UNKNOWN | 0 |

By relation:

| Relation | L0 | L1 | L2 | L5 |
|---|---:|---:|---:|---:|
| awardWonBy | 0 | 10 | 0 | 0 |
| companyTradesAtStockExchange | 30 | 28 | 0 | 42 |
| countryLandBordersCountry | 58 | 3 | 0 | 6 |
| hasArea | 24 | 58 | 1 | 17 |
| hasCapacity | 8 | 92 | 0 | 0 |
| personHasCityOfDeath | 39 | 51 | 0 | 10 |

Correct gold appeared somewhere before final output in `109` wrong rows. That is the main low-model-call opportunity. Conversely, `242` rows are true recall failures in the persisted evidence; a smarter controller alone cannot fix those.

## Relation-Specific Findings

### awardWonBy

Gold and prediction cardinalities are both large. The system partially matches `9/10` rows but never exactly matches a row. All rows are localized to `L1_NEVER_RECALLED`, with `418` FNs and `266` FPs. The dominant loss is incomplete recall plus large false-positive accumulation. This is not a finalization-only problem.

### companyTradesAtStockExchange

There are `30` exact rows, `36` partial rows, `24` complete misses, and `39` empty predictions. The largest failure is over-generation: `54` rows have false-positive accumulation and `37` single-gold rows emit multiple exchanges. `42` wrong rows are `L5_FINALIZATION_OR_NORMALIZATION_LOSS`, meaning useful candidate information exists but final output shape/filtering is poor. Rejection-first semantics should focus on FP suppression without broadening stock recall.

### countryLandBordersCountry

This relation remains strong: `58/67` exact, mean F1 `0.964`, only `3` FPs and `11` FNs. Remaining failures are small: `9` partial rows, `8` under-enumerated rows, `3` singleton-when-multi rows, and `5` final-graph-but-not-output rows. Do not relax conservative border behavior or add broad expansion for this relation.

### hasArea

The primary failure is empty output / recall absence: `65` empty rows and `58` L1 never-recalled rows. However, `17` rows have the gold value in the final graph but not the output, making finalization retention a concrete low-cost opportunity. Wrong non-empty numeric values are mixed, with `2` scale-like errors and `1` just outside tolerance.

### hasCapacity

This is the weakest relation. `92/100` rows are complete misses and all are localized to L1 never recalled. `79` wrong non-empty predictions are false positives, `75` are large numeric misses, and `64` wrong non-empty values are too large. The dominant failure is wrong capacity value generation/selection, not punctuation or units.

### personHasCityOfDeath

There are `39` exact rows, `52` complete misses, and `78` empty predictions. L1 recall failure dominates (`51` rows), but `10` rows are L5 finalization/normalization losses and `16` rows have multi/wrong-city FP burden. The cheapest safe fix class is single-valued city output-shape suppression.

## Action-Family Behavior

The full TRAIN base run action-effect telemetry contains these executed families:

| Action family | executed | affected wrong rows | affected correct rows | candidate additions | removals/contradictions | gold touches | FP named |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALTERNATIVE_RECALL | 24 | 5 | 19 | 12 | 0 | 26 | 11 |
| ATTRIBUTE_DECOMPOSITION | 50 | 30 | 20 | 21 | 0 | 14 | 54 |
| CONTRAST_VERIFY | 39 | 20 | 19 | 0 | 11 | 41 | 0 |
| DEFINITION_RECALL | 33 | 33 | 0 | 3 | 0 | 0 | 3 |
| INDEPENDENT_RECALL | 34 | 18 | 16 | 3 | 0 | 0 | 4 |
| MULTI_VIEW_RECALL | 33 | 33 | 0 | 9 | 0 | 0 | 9 |

The TRAIN base predictions predate the supplemental reachability observations for rare stock/award families; do not infer production utility for `LISTING_ELIMINATION`, `SEMANTIC_VERIFY`, `SET_EXPANSION`, or `UNARY_VERIFY` from this base prediction action table alone.

## Top 15 Largest Score-Loss Sources

| Rank | Category | Relation | Rows | FP | FN | Opportunity |
|---:|---|---|---:|---:|---:|---:|
| 1 | COMPLETE_MISS | hasCapacity | 92 | 79 | 92 | 92.0 |
| 2 | CORRECT_CANDIDATE_NEVER_RECALLED | hasCapacity | 92 | 79 | 92 | 92.0 |
| 3 | STOPPED_TOO_EARLY | hasCapacity | 92 | 79 | 92 | 92.0 |
| 4 | UNDER_ENUMERATION | hasCapacity | 92 | 79 | 92 | 92.0 |
| 5 | FALSE_POSITIVE_ACCUMULATION | hasCapacity | 79 | 79 | 79 | 79.0 |
| 6 | OVER_ENUMERATION | hasCapacity | 79 | 79 | 79 | 79.0 |
| 7 | COMPLETE_MISS | hasArea | 76 | 11 | 76 | 76.0 |
| 8 | STOPPED_TOO_EARLY | hasArea | 76 | 11 | 76 | 76.0 |
| 9 | UNDER_ENUMERATION | hasArea | 76 | 11 | 76 | 76.0 |
| 10 | NUMERIC_LARGE_MISS | hasCapacity | 75 | 75 | 75 | 75.0 |
| 11 | WRONG_NUMERIC_ATTRIBUTE | hasCapacity | 75 | 75 | 75 | 75.0 |
| 12 | EMPTY_NUMERIC_RECALL | hasArea | 65 | 0 | 65 | 65.0 |
| 13 | EMPTY_PREDICTION | hasArea | 65 | 0 | 65 | 65.0 |
| 14 | COMPLETE_MISS | personHasCityOfDeath | 52 | 7 | 52 | 52.0 |
| 15 | CORRECT_CANDIDATE_NEVER_RECALLED | hasArea | 58 | 9 | 58 | 58.0 |

Opportunity values are diagnostic upper bounds, not expected held-out gains.

## Top 15 Cheapest Generalizable Fixes

| Rank | Candidate | Affected rows | FP red. | FN red. | Compatibility |
|---:|---|---:|---:|---:|---|
| 1 | City: suppress multi-city output for singleton relation | 16 | 16 | 7 | SAFE_WITH_EXISTING_CALIBRATION |
| 2 | String: final output alias canonicalization | 6 | 1 | 9 | SAFE_WITH_EXISTING_CALIBRATION |
| 3 | Borders: preserve conservative closure | 67 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 4 | Numeric: canonicalize unit-like number strings | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 5 | Numeric: suppress nonnumeric final outputs | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 6 | Output: normalized duplicate suppression | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 7 | Stock: reject subject/company self outputs | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 8 | Stock: exchange-name alias normalization | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 9 | Controller: avoid stopping with unresolved missing candidates | 266 | 394 | 682 | INVALIDATES_CALIBRATION_SEMANTICS |
| 10 | Recall: relation-specific missing-recall prompt review | 242 | 391 | 655 | INVALIDATES_CALIBRATION_SEMANTICS |
| 11 | Numeric: single-valued best numeric guard | 85 | 85 | 85 | MAY_SHIFT_CALIBRATION |
| 12 | Stock: rejection-first FP suppression | 54 | 95 | 16 | MAY_SHIFT_CALIBRATION |
| 13 | Stock: calibrated listing cardinality guard | 37 | 53 | 5 | MAY_SHIFT_CALIBRATION |
| 14 | Finalization: retain accepted gold-equivalent candidate | 23 | 3 | 26 | SAFE_WITH_EXISTING_CALIBRATION |
| 15 | Award: cardinality-aware continuation | 10 | 266 | 418 | INVALIDATES_CALIBRATION_SEMANTICS |

Rows with zero observed effect are included because they are cheap guardrails but should not be prioritized for V3.1 score recovery.

## Top 15 Highest Expected Score-Recovery Interventions

| Rank | Candidate | Affected rows | FP red. | FN red. | Compatibility |
|---:|---|---:|---:|---:|---|
| 1 | Controller: avoid stopping with unresolved missing candidates | 266 | 394 | 682 | INVALIDATES_CALIBRATION_SEMANTICS |
| 2 | Recall: relation-specific missing-recall prompt review | 242 | 391 | 655 | INVALIDATES_CALIBRATION_SEMANTICS |
| 3 | Award: cardinality-aware continuation | 10 | 266 | 418 | INVALIDATES_CALIBRATION_SEMANTICS |
| 4 | Award: false-positive cap after expansion | 10 | 266 | 418 | MAY_SHIFT_CALIBRATION |
| 5 | Numeric: single-valued best numeric guard | 85 | 85 | 85 | MAY_SHIFT_CALIBRATION |
| 6 | Stock: rejection-first FP suppression | 54 | 95 | 16 | MAY_SHIFT_CALIBRATION |
| 7 | Stock: calibrated listing cardinality guard | 37 | 53 | 5 | MAY_SHIFT_CALIBRATION |
| 8 | Finalization: retain accepted gold-equivalent candidate | 23 | 3 | 26 | SAFE_WITH_EXISTING_CALIBRATION |
| 9 | City: suppress multi-city output for singleton relation | 16 | 16 | 7 | SAFE_WITH_EXISTING_CALIBRATION |
| 10 | String: final output alias canonicalization | 6 | 1 | 9 | SAFE_WITH_EXISTING_CALIBRATION |
| 11 | Borders: preserve conservative closure | 67 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 12 | Numeric: canonicalize unit-like number strings | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 13 | Numeric: suppress nonnumeric final outputs | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 14 | Output: normalized duplicate suppression | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |
| 15 | Stock: reject subject/company self outputs | 0 | 0 | 0 | SAFE_WITH_EXISTING_CALIBRATION |

## Controller/M21 Opportunities

1. Avoid stopping with unresolved missing candidates (`266` affected rows; invalidates calibration semantics).
2. Numeric single-valued best numeric guard (`85`; may shift calibration).
3. Stock rejection-first FP suppression (`54`; may shift calibration).
4. Stock calibrated listing cardinality guard (`37`; may shift calibration).
5. Award cardinality-aware continuation (`10`; invalidates calibration semantics).
6. Award false-positive cap after expansion (`10`; may shift calibration).
7. Retain accepted/gold-equivalent candidate in finalization (`23`; safe with existing calibration).

No controller-only change should be applied directly to the frozen submission.

## Recall/Prompt Opportunities

1. Relation-specific missing-recall prompt review (`242` L1 rows; requires model calls and recollection/recalibration review).
2. Award cardinality-aware continuation (`10` rows, `418` FNs; requires action/recalibration review).
3. Capacity definition/attribute recall repair (`92` L1 rows; requires model calls and calibration review).
4. Area empty-recall repair (`58` L1 rows; requires model calls and calibration review).
5. City-of-death recall repair (`51` L1 rows; requires model calls and calibration review).
6. Stock empty/missing listing recall repair (`28` L1 rows; requires model calls and calibration review).

## Finalization/Normalization Opportunities

1. City single-valued output-shape guard (`16` rows; safe with existing calibration).
2. Retain final-graph gold-equivalent candidate (`23` rows; safe with existing calibration).
3. String final output alias canonicalization (`6` rows; safe with existing calibration).
4. Border conservative closure preservation (`67` rows protected; safe).
5. Numeric canonicalization/format guards (`0` currently observed, but safe guardrail).
6. Duplicate normalized output suppression (`0` currently observed, safe guardrail).

## Top Do-Not-Break Behaviors

- Preserve conservative `countryLandBordersCountry` behavior: mean F1 `0.964`, exact `58/67`, FP `3`, FN `11`.
- Do not add broad expansion for frozen/conservative borders.
- Do not relax duplicate/alias suppression or final cardinality safeguards for borders.
- Do not tune against TEST or use external factual data.
- Do not convert TRAIN oracle counterfactuals into production rules without a separate V3.1 validation plan.

## Counterfactual TRAIN Simulations

These are TRAIN oracle/diagnostic-only simulations on copies of predictions. They do not claim held-out gains.

| Rule | Oracle | macro-F1 | delta macro-F1 | micro-F1 | delta micro-F1 |
|---|---|---:|---:|---:|---:|
| baseline | no | 0.40333 | 0.00000 | 0.51799 | 0.00000 |
| drop_nonnumeric_numeric_predictions | no | 0.40333 | 0.00000 | 0.51799 | 0.00000 |
| emit_control_survived_when_empty | no | 0.42045 | +0.01712 | 0.50600 | -0.01199 |
| single_valued_keep_first_prediction | no | 0.40216 | -0.00117 | 0.52166 | +0.00367 |
| TRAIN_ORACLE_remove_false_positives | yes | 0.47188 | +0.06855 | 0.63782 | +0.11982 |
| TRAIN_ORACLE_add_recalled_gold | yes | 0.44439 | +0.04106 | 0.53455 | +0.01656 |
| TRAIN_ORACLE_exact_gold_when_all_seen | yes | 0.47098 | +0.06765 | 0.54423 | +0.02624 |

The only non-oracle positive macro-F1 simulation is emitting `CONTROL_SURVIVED` for empty rows, driven by `hasArea` (+0.08167 relation macro-F1). It lowers micro-F1 and must be treated as a diagnostic hint, not a production rule.

## Calibration Compatibility

Safe with existing V3 calibration:

- city single-valued output-shape suppression;
- final output alias canonicalization;
- retaining an already-present final-graph/accepted gold-equivalent candidate;
- duplicate normalized output suppression guardrail;
- border conservative behavior preservation.

May shift calibration:

- stock rejection-first FP suppression;
- stock calibrated listing cardinality guard;
- numeric single-valued best numeric guard;
- award false-positive cap after expansion.

Invalidates or materially shifts action-effect distribution:

- relation-specific missing-recall prompt review;
- open-set/cardinality-aware continuation;
- controller stopping policy changes with unresolved missing candidates;
- award recall/continuation changes requiring extra model calls.

## Representative Casebook

The full casebook is in:

`outputs/v3_train_error_forensics/representative_cases.md`

Representative cases include:

- row `0`, Wellington Island / hasArea: empty output, gold never recalled.
- row `1`, Mangareva / hasArea: gold present in final graph but not output.
- row `22`, Montserrat / hasArea: correct value present in final graph, wrong scale-like output emitted.
- row `102`, Estadio Revolucion Ciudad de Guatemala in Guatemala City / hasCapacity: `50000` predicted vs gold `5000`, scale-like capacity miss.
- rows `210`, `211`, `215`, stock: single-gold rows with multiple exchanges.
- rows `379`, `385`, `387`, city: singleton city relation with FP city burden.
- border rows remain mostly exact and should be treated as protected behavior.

## Tests And Checks

Focused analyzer tests:

```bash
python -m pytest tests/test_v3_train_failure_forensics.py -q
```

Result:

`2 passed`

Full deterministic tests:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result:

`3596 passed, 4 skipped`

Full default tests:

```bash
python -m pytest tests/ -q
```

Result:

`3596 passed, 4 skipped`

Static checks:

```bash
python -m pyflakes src/ tests/ scripts/
git diff --check
```

Result:

Both passed.

## Scientific Safety

TRAIN gold was used only for offline error analysis and counterfactual diagnostics. No TEST prediction, TEST gold, VAL data, web data, external corpus, external KB, RAG, or model call was used. No production behavior was changed. The frozen calibrated V3 TEST submission remains separate from any future V3.1 intervention.

## Recommended V3.1 Order

1. Implement and test safe finalization-only guards first: city singleton output shape, final-graph candidate retention, string alias canonicalization.
2. Separately prototype numeric selection guards, but treat them as calibration-shifting until proven otherwise.
3. Investigate stock rejection-first FP suppression as a calibrated V3.1 planner/finalization change.
4. Treat hasCapacity and award recall/continuation as larger recollection/recalibration work; they dominate theoretical recovery but cannot be fixed by post-processing alone.
5. Keep border behavior frozen except for narrowly proven finalization-loss fixes.

## Verdict

PASS — V3 TRAIN ERROR FORENSICS AND SCORE-RECOVERY MAP COMPLETE
