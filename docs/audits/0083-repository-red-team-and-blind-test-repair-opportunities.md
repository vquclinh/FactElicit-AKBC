# Audit 0083 - Repository Red-Team and Blind TEST Repair Opportunities

Status: REVIEW ONLY - NO IMPLEMENTATION PERFORMED
Date: 2026-08-12
Source HEAD: `2129e4518627834fecb5e4874dff18ef77e9644c`

## 1. Scope and constraints

This audit is a read-only repository and blind-output review.  No TRAIN, VAL or
TEST inference was run.  No model weights were loaded.  No web, RAG, external
corpus, TEST gold, fine-tuning, third model, subject-answer lookup table, commit
or push was used.

The only repository file created by this task is this document:

`docs/audits/0083-repository-red-team-and-blind-test-repair-opportunities.md`

Primary blind artifact:

`outputs/submission-best/predictions.jsonl`

Evidence labels used below:

- OBSERVED: directly visible from source, configs, local artifacts, or
  prediction shape.
- INFERRED: architecture/output-pattern reasoning without hidden TEST labels.
- TRAIN-SUPPORTED: already established by repository TRAIN artifacts or audits;
  no new TRAIN inference was run here.
- PROPOSED: design opportunity only; not implemented in this audit.

CPU-only commands used for this review included:

```bash
git rev-parse HEAD
git status --short
sha256sum outputs/submission-best/predictions.jsonl
sha256sum benchmark/data/test.jsonl benchmark/data/train.jsonl benchmark/evaluate.py
wc -l outputs/submission-best/predictions.jsonl benchmark/data/test.jsonl
rg --files src/cover_kbc configs/experiments tests docs outputs/submission-best benchmark
python - <<'PY'  # one-off JSONL shape and artifact census; no repository write
PY
```

## 2. Current architecture review

OBSERVED from implementation, not only audit prose:

- Runner: `scripts/run_cover.py`
- Pipeline: `src/cover_kbc/pipeline.py`
- Finalization: `src/cover_kbc/selection.py`
- Relation contracts: `src/cover_kbc/contracts/registry.py`
- V3 relation profiles/actions:
  `src/cover_kbc/contracts/relation_profile.py`,
  `src/cover_kbc/v3_core/execution.py`,
  `src/cover_kbc/v3_core/hypothesis.py`,
  `src/cover_kbc/v3_core/relation_programs.py`
- V3.1 live prompts/finalization:
  `src/cover_kbc/v3_1/config.py`,
  `src/cover_kbc/v3_1/live_prompts.py`,
  `src/cover_kbc/v3_1/entity_finalization.py`
- V3.2 repair:
  `src/cover_kbc/leaderboard_repair/config.py`,
  `src/cover_kbc/leaderboard_repair/stack.py`,
  `src/cover_kbc/leaderboard_repair/relations.py`,
  `src/cover_kbc/leaderboard_repair/consistency.py`,
  `src/cover_kbc/leaderboard_repair/risk_guard.py`,
  `src/cover_kbc/leaderboard_repair/util.py`
- A/B/C configs:
  `configs/experiments/cover_kbc_v3_2_profile_a_stock_probe_test.yaml`,
  `configs/experiments/cover_kbc_v3_2_profile_b_repair_core_test.yaml`,
  `configs/experiments/cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml`

### Actual call flow

```text
Query row from benchmark/data/test.jsonl
  -> M0 relation contract / M1 typed router
     owner: CPU, no calls
     loss risk: wrong relation semantics would poison all later prompts

  -> M9 query profiler
     owner: CPU, no calls
     output: query_profiles.jsonl
     unused-later risk: high residual/risk can be recorded but not acted on

  -> M10 prompt program compiler
     owner: CPU, no calls
     output: prompt_programs.jsonl

  -> M11 parametric retrieval
     owner: Mistral24 enumerator, shadow calls
     output: parametric_memory.jsonl
     recall risk: useful recalled facts can stay outside the production graph

  -> M12/M13/M14/M15 relation specialists
     owner: Mistral24; M14/M15 can use Qwen cross-family when configured, but
            A/B/C keep cross_family_recall false in those specialist blocks
     output: numeric, open-set, null-temporal and small-set specialist JSONL
     recall risk: specialist evidence may be summarized away before final repair

  -> M2 acquisition views
     owner: Mistral24 enumerator; Qwen4 can contribute via cross-model recall
     budget: Module 7 per-query Budget plus active-controller choices
     recall loss: no generated candidate, weak view diversity, bad parse
     FP introduction: broad prompts, city/list/ticker leakage, wrong attribute

  -> M3 evidence graph
     owner: CPU
     suppression risk: hard contract rules can reject candidates

  -> M4 blind verification
     owner: Qwen4 verifier label scoring
     budget: Module 7, and in V3 production through M20 reservations
     suppression risk: UNKNOWN/INVALID can prevent final emission

  -> M16 atomic consensus
     owner: CPU, no calls
     output: atomic_consensus.jsonl

  -> M17 specialist verifier catalogue / M18 bidirectional catalogue
     owner: catalogue CPU, selected executions Qwen4
     budget: selected actions governed by M20/M21 in production
     lost opportunity: large catalogues are often recorded with zero executions

  -> Layer 4 evidence integration -> M19 coverage-gap estimator
     owner: CPU, no calls
     output: layer4_evidence.jsonl, coverage_gap.jsonl

  -> M20 relation budget scheduler -> M21 micro-planner -> Layer 6
     owner: CPU, no calls
     selected V3 action calls: Mistral24 for recall actions, Qwen4 for semantic
     or listing verification actions
     suppression risk: STOP/no affordable action leaves unresolved candidates

  -> V3 action loop
     owner: Mistral24 or Qwen4 depending on action family
     budget: M20 ledger; bounded by max_control_rounds_per_catalogue
     recovery point: can mutate graph before M8 only for legal, selected actions

  -> M8 relation-specific finalization
     owner: CPU
     finalization behavior:
       stock/borders: SMALL_SET selection; stock V3.1 structural/dominance rules
       city: NULL_SINGLE, at most one
       awards: LARGE_OPEN_SET, recall-first
       area: dominant numeric cluster median
       capacity: highest qualifying capacity cluster
     suppression risk: thresholds, support dominance, numeric clustering,
     UNKNOWN dropping, final cardinality limits

  -> L7 relation-specific repair
     owner: CPU plus bounded Mistral24/Qwen4 repair calls
     budget: leaderboard_repair.max_calls_by_relation, separate from M20/M21

  -> L8 cross-query consistency
     owner: CPU plus Qwen4 only for border reciprocity conflicts

  -> L9 relation-specific final risk guard
     owner: CPU
     no later recovery currently exists

  -> predictions.jsonl official output
     repair sidecars: leaderboard_repair.jsonl and repair_accounting.json
```

### Where current information can be lost

OBSERVED:

- `run_cover.py` passes only `Prediction` objects and in-memory V3 hypothesis
  graphs into L7-L9.  It does not pass raw M11, M12, M13, M14, M15, M16, Layer
  4, M19 or M21 records to the repair stack.
- `candidate_signals()` sees the current prediction, `Prediction.candidates`,
  and `QueryHypothesisGraph.hypotheses`.  It does not consume raw specialist
  observations, facet states, null-temporal status observations, numeric
  cross-unit checks, pending M17/M18 catalogues, or M19 residual directly.
- `L9RelationSpecificFinalRiskGuard` is the last current stage.  Once it drops
  a value, no later layer can recover it.

INFERRED:

- The main late-layer opportunity is not more general architecture.  It is
  giving L10+ access to already-paid evidence that is currently summarized too
  early, then spending additional model calls only on rows whose final shape is
  suspicious.

### Duplicated and overlapping logic

OBSERVED:

- Stock type filtering appears in V3.1 finalization
  (`stock_structural_validation`) and again in L7/L9
  (`StockExchangeEntityGuard`, `stock_wrong_type_reason`).
- Award metadata cleanup appears in V3.1 output repair and again in L7/L9.
- Alias dedupe appears in L7 stock, L8 generic consistency, and L9 guards.
- Numeric clustering appears in M8 and in L7 numeric rescue.
- Border semantic exclusions are encoded in the M0 contract, the base view
  library, L7 directional prompts, L8 reciprocity prompts and L9 guard.

This overlap is not automatically bad for leaderboard probes, but it creates
two risks: redundant calls, and one layer undoing a more nuanced prior decision
with a simpler deterministic rule.

## 3. `submission-best` integrity and output-shape census

Primary file:

`outputs/submission-best/predictions.jsonl`

OBSERVED integrity:

| item | value |
|---|---:|
| source HEAD | `2129e4518627834fecb5e4874dff18ef77e9644c` |
| predictions SHA256 | `a15a95e6c327699c00cdebba0e0d3bb65176da0c9401937de4ceaa3571058792` |
| prediction rows | 475 |
| canonical blind TEST rows | 475 |
| canonical TEST SHA256 | `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1` |
| order matches TEST subject/relation | true |
| TEST `ObjectEntities` all empty | true |
| TRAIN SHA256, for provenance only | `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e` |
| evaluator SHA256 | `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22` |

Relation and cardinality census:

| relation | rows | empty | nonempty | mean card | median | max | card distribution |
|---|---:|---:|---:|---:|---:|---:|---|
| `awardWonBy` | 10 | 0 | 10 | 62.800 | 45.0 | 117 | `{14:1,32:1,40:2,42:1,48:1,87:1,100:1,108:1,117:1}` |
| `companyTradesAtStockExchange` | 100 | 31 | 69 | 1.940 | 2.0 | 41 | `{0:31,1:12,2:48,3:4,4:1,7:2,15:1,41:1}` |
| `countryLandBordersCountry` | 67 | 11 | 56 | 3.851 | 4.0 | 13 | `{0:11,1:9,2:5,3:4,4:11,5:8,6:7,7:5,8:3,9:1,10:2,13:1}` |
| `hasArea` | 100 | 71 | 29 | 0.290 | 0.0 | 1 | `{0:71,1:29}` |
| `hasCapacity` | 98 | 25 | 73 | 0.745 | 1.0 | 1 | `{0:25,1:73}` |
| `personHasCityOfDeath` | 100 | 98 | 2 | 0.020 | 0.0 | 1 | `{0:98,1:2}` |

Global shape anomalies:

| observed shape | count |
|---|---:|
| rows with normalized duplicate outputs | 0 |
| subject copied as object | 0 |
| anomaly objects with metadata/control/long-string shape | 83 |
| award outputs containing `:` | 84 |
| award packed-list-like objects | 6 |
| award role-leakage-like objects | 30 |
| award parenthetical repeat metadata objects | 49 |
| stock outputs flagged by current deterministic wrong-type rule | 58 |
| stock city/short non-exchange-like outputs by heuristic | 49 |
| stock ticker-like outputs by regex | 13 |
| border reciprocity conflicts among rows where both endpoints are TEST subjects | 9 |

Top repeated normalized output strings:

| output key | count |
|---|---:|
| `new york stock exchange` | 19 |
| `nasdaq` | 17 |
| `london stock exchange` | 10 |
| `nyse` | 10 |
| `10000` | 8 |
| `60000` | 7 |
| `30000` | 7 |
| `austria` | 6 |
| `groups none` | 5 |
| `projects none` | 5 |

OBSERVED provenance note:

`outputs/submission-best/predictions.jsonl` is not byte-identical to
`outputs/v3_test_16f60fb1_20260810T160048Z/run/predictions.jsonl`; it differs
on 8 stock rows where the earlier run had empty pipeline-error outputs.  This is
recorded only as artifact provenance and shape evidence, not as hidden TEST
correctness.

## 4. Relation-by-relation forensic review

### `hasArea`

OBSERVED:

- `71/100` blind predictions are empty.
- Every non-empty prediction is singleton numeric.
- Numeric outputs range from `0.388498` to `1,759,540`; median is `70,276`.
- Shape categories:

| subject shape | rows | empty | mean card | max card |
|---|---:|---:|---:|---:|
| short ambiguous name | 57 | 38 | 0.33 | 1 |
| island/reef/atoll keyword | 23 | 20 | 0.13 | 1 |
| lake keyword | 13 | 7 | 0.46 | 1 |
| qualifier/comma | 5 | 5 | 0.00 | 0 |
| other geo | 3 | 2 | 0.33 | 1 |

TRAIN-SUPPORTED:

- `outputs/v3_2_weakness_mining/relation_failure_matrix.csv` reports TRAIN
  area failures dominated by `NO_RECALL`, `TOO_MANY_FALSE_POSITIVES`, and
  `WRONG_NUMERIC_ATTRIBUTE`.
- `outputs/v3_2_weakness_mining/numeric_failure_deep_dive.csv` shows TRAIN
  examples with wide candidate spreads and gold-like candidates sometimes
  present but not emitted.

Audit-0082 coverage:

- Profile C enables `AreaEntityTypeProfiler`, two recall views,
  `AreaEmptyRescue`, and `NumericAttributeResolver`.

Remaining weakness:

- The current area entity-type profiler is string-simple: `lake` -> LAKE,
  `island` -> ISLAND, otherwise two-token subjects become COUNTRY.  The blind
  shape has many short ambiguous geographic names; that heuristic can route a
  non-country exact entity through country semantics.
- The repair path makes fresh calls instead of first exhausting already-paid
  M12 numeric clusters/cross-unit checks.

PROPOSED:

- Add an L10 `NumericEvidenceReuseResolver` that reads M12 clusters and M16
  numeric hypotheses before new calls.  Trigger on empty/low-confidence numeric
  rows or multiple non-output numeric clusters.  Only escalate to Qwen when
  existing clusters disagree outside 5%.

### `hasCapacity`

OBSERVED:

- `25/98` blind predictions are empty.
- Every non-empty prediction is singleton numeric.
- Repeated capacity outputs are very visible: `10000` appears 8 times,
  `60000` 7 times, `30000` 7 times.
- `51/73` numeric outputs are multiples of 1000; `59/73` multiples of 100.
- Shape categories:

| subject shape | rows | empty | mean card |
|---|---:|---:|---:|
| location-qualified venue | 98 | 25 | 0.74 |
| stadium | 66 | 14 | 0.79 |
| field | 5 | 4 | 0.20 |
| sports centre/center | 4 | 1 | 0.75 |
| park/ground | 4 | 1 | 0.75 |
| arena | 1 | 0 | 1.00 |
| bullring | 1 | 0 | 1.00 |
| cricket venue | 1 | 0 | 1.00 |

TRAIN-SUPPORTED:

- `outputs/v3_2_weakness_mining/relation_failure_matrix.csv` reports capacity
  TRAIN failures as `NO_RECALL` on 92 rows and `WRONG_NUMERIC_ATTRIBUTE` on 74
  rows.
- `outputs/v3_2_weakness_mining/improvement_ranking.csv` ranks capacity
  definition-aware recall and numeric attribute resolution as high expected
  value.

Audit-0082 coverage:

- Profile C enables exact venue/location prompts, two recall views, configuration
  variants, round-number-triggered repair, and `NumericAttributeResolver`.

Remaining weakness:

- Roundness is only a trigger, correctly, but current shape still suggests many
  generic defaults.  A pure recall retry can reproduce the same anchor.
- Existing M12 clusters and hard-definition violations can identify candidate
  ratios and unit/attribute conflicts before spending C's extra calls.

PROPOSED:

- Add L10 `CapacityClusterShapeResolver`: use M12 clusters, candidate ratios,
  and qualifier text first; call Qwen only on conflicting clusters or identity
  ambiguity.  Require the exact venue plus supplied location in any prompt.

### `companyTradesAtStockExchange`

OBSERVED:

- `31/100` blind predictions are empty in `submission-best`.
- `57/100` rows are multi-output; two rows have extreme cardinality (`15` and
  `41`).
- Stock output shape contains city/venue-type leakage.  A generic heuristic
  found 49 short non-exchange-like values and 13 ticker-like values.
- Current deterministic stock guard dry-run over `submission-best` would change
  16 stock rows and remove 58 values.
- Important red-team finding: current `stock_wrong_type_reason()` would reject
  several short/brand venue-name shapes as `city_or_bare_location` or
  `ticker_only`, including examples shaped like `Nasdaq`, `Euronext Paris`,
  `Euronext Amsterdam`, `B3`, `KRX`, `NYSE American`, `Xetra`, `NSX`.  This is
  output-shape/semantic suspicion only, not a TEST label claim.

TRAIN-SUPPORTED:

- `docs/audits/0082-v3-2-leaderboard-repair-stack.md` records the real-weight
  Audit-0081 stock prompt result: macro-F1 `0.52890 -> 0.61967`, macro-P
  `0.62750 -> 0.86000`, macro-R `0.74333 -> 0.66500`, TP/FP/FN
  `48/95/33 -> 39/20/42`.
- That establishes the stock listing-entity prompt as high-value and the
  remaining issue as multi-listing/recall loss.

Audit-0082 coverage:

- Profile A enables `stock_listing_entity_prompt` and keeps
  `stock_support_dominance=false`.
- Profiles B/C add `StockExchangeEntityGuard`, `StockAliasDeduplicator`, and
  `StockMultiListingRescue`.

Remaining weakness:

- L7 stock guard currently runs deterministic rejection before model-assisted
  classification; L9 repeats the deterministic guard at the end.  This can
  delete short legitimate venue names without giving Qwen a chance to classify
  them.
- `StockMultiListingRescue` triggers only when strict final output has `<=1`
  venue.  An overlong row full of city leakage can be reduced to singleton or
  empty by the guard, but suppressed plausible secondary listings may not be
  considered if the trigger/candidate set is affected by earlier deletion.

PROPOSED:

- Add L10 `StockShortVenueSemanticHoldout`: values rejected solely because they
  are short/acronym/brand venue-shaped should be held for Qwen exchange-type
  classification if they have any pre-final support or appear in the current
  prediction.  Do not use a company-to-exchange table.
- Add `StockPostGuardRescue`: run multi-listing rescue after the final L9 guard
  if L9 removed values and the candidate graph still has two or more plausible
  exchange entities.

### `countryLandBordersCountry`

OBSERVED:

- `11/67` blind predictions are empty.
- `9/67` rows have singleton output.
- Reciprocity scan among TEST subjects found 9 directed conflicts where `A`
  predicts `B`, `B` is also a subject, and `B` does not predict `A`.
- Sidecar V3 graphs show 17 support>=2 non-output border hypotheses across 9
  rows.

TRAIN-SUPPORTED:

- Existing TRAIN artifacts report this relation as already strong: SAFE_CORE
  TRAIN macro-F1 `0.964369`, with remaining errors mostly set incompleteness
  rather than broad type confusion.

Audit-0082 coverage:

- Profile B adds border alias dedupe and verified reciprocity repair.
- Profile C adds directional sweep plus verifier filtering.

Remaining weakness:

- V3 executable action mapping currently returns no recall spec for borders
  (`_recall_spec()` returns `None` for `countryLandBordersCountry`), so border
  recall expansion lives only in L7 rather than in the M20/M21 action loop.
- Directional sweep asks two broad direction bundles.  It does not yet do
  clockwise missing-neighbor review, count consistency, or "what neighbor is
  missing from this current set?" prompts.

PROPOSED:

- Add `BorderGraveyardCompletenessAuditor`: before new directional calls, inspect
  pre-final support>=2 non-output border hypotheses and existing reciprocity
  conflicts.  Verify only candidate pairs not already settled.
- Add `BorderMissingNeighborPrompt`: show the current set and ask one bounded
  "what terrestrial neighbor is missing?" question, then pairwise verify.

### `personHasCityOfDeath`

OBSERVED:

- `98/100` blind predictions are empty.
- Only 2 rows emit a city.
- Query-shape categories:

| subject shape | rows | empty |
|---|---:|---:|
| two/three-token name | 79 | 77 |
| initial/abbrev | 10 | 10 |
| four-plus-token name | 10 | 10 |
| hyphenated | 2 | 2 |
| particle/prefix | 1 | 1 |

- Sidecar V3 graphs show 40 city rows where graph candidates exceed final
  output, with 9 support>=2 non-output hypotheses.

TRAIN-SUPPORTED:

- `outputs/v3_2_weakness_mining/relation_failure_matrix.csv` reports city TRAIN
  failures dominated by `NO_RECALL` and `EMPTY_OUTPUT`.

Audit-0082 coverage:

- Profile C enables `DeathExistenceGate` and `DeathCityRecall` with status,
  two recall views, and contrast selection.

Remaining weakness:

- Current L7 city repair does not consume the already-paid M14
  `status_observations`, `gate`, `locality_observations`,
  `competing_candidates`, or `near_miss_mentions` directly.  It calls both
  models again for status even when M14 already produced status evidence.
- Wrong-identity and hospital-to-city conversion are prompt instructions, but
  not a distinct final auditor.

PROPOSED:

- Add `DeathEvidenceReuseGate`: read M14 status/locality evidence first.  If M14
  has high-confidence ALIVE or high-confidence DECEASED plus target-locality
  candidate, avoid redundant status calls or route directly to contrast.
- Add `DeathIdentityAndLocalityAuditor`: final singleton city must be a locality
  for the exact person, not a birth/residence/burial/hospital token; use one
  Qwen contrast only when final city conflicts with M14 near-miss evidence.

### `awardWonBy`

OBSERVED:

- `10/10` award rows are non-empty.
- Mean cardinality is `62.8`; maximum is `117`.
- Structural categories over emitted objects:

| category | count |
|---|---:|
| clean-or-unknown by regex | 447 |
| decade/range prefixed | 57 |
| year prefixed | 37 |
| category label | 16 |
| NONE/UNKNOWN token | 37 |
| organization/project-like | 35 |
| role leakage-like | 30 |
| parenthetical repeat metadata | 49 |
| packed list-like object | 6 |

- Existing deterministic award cleanup dry-run changes all 10 award rows and
  removes 115 net objects, but it leaves packed recipient lists such as
  comma/conjunction groups as single objects after stripping the bucket prefix.

TRAIN-SUPPORTED:

- Existing TRAIN artifacts identify awards as both over-generated and incomplete:
  `BAD_CANDIDATE_SURVIVED`, `MISSING_SET_MEMBERS`, `PARTIAL_SET`, and
  `TOO_MANY_FALSE_POSITIVES`.

Audit-0082 coverage:

- Profiles B/C include `AwardMetadataNormalizer`.
- Profile C adds time-sliced recall and `AwardRecipientWitness`.

Remaining weakness:

- A single batched witness prompt over 80-120 candidates can be too dense; valid
  recipients, invalid roles, and unknowns may be under-separated.
- Current metadata cleanup does not split time-bucketed lists into individual
  candidate entities before witness verification.
- Role leakage terms such as committee, jury, panel and communication-team
  strings are visible in blind output shape and can often be rejected by generic
  award-role semantics.

PROPOSED:

- Add `AwardPackedRecipientSplitter`: after metadata cleanup, split only
  structurally obvious bucketed recipient lists and keep provenance.  Avoid
  blindly splitting legitimate organization/project names.
- Add `AwardRoleNegativeAuditor`: deterministic hard-negative role terms
  decrease support; Qwen witness is used for borderline candidates, chunked by
  era or recipient type rather than one huge prompt.

## 5. Query-shape risk taxonomy

OBSERVED shape correlations from blind TEST subjects and outputs:

| relation | shape signal | rows | risk pattern |
|---|---:|---:|---|
| `hasArea` | island/reef/atoll keyword | 23 | 20 empty; strong empty-rescue target |
| `hasArea` | lake keyword | 13 | 7 empty; lake surface-area semantics important |
| `hasArea` | qualifier/comma | 5 | 5 empty; exact-entity identity risk |
| `hasCapacity` | field | 5 | 4 empty; smaller/obscure venue risk |
| `hasCapacity` | stadium | 66 | 14 empty; generic rounded-number risk |
| `companyTradesAtStockExchange` | other company shape | 70 | 25 empty, max card 41 |
| `companyTradesAtStockExchange` | group/holding-like | 11 | mean card 2.91, max 15 |
| `companyTradesAtStockExchange` | bank/financial | 7 | 2 empty, parent/subsidiary/listing-state risk |
| `countryLandBordersCountry` | singleton output | 9 | likely completeness/reciprocity trigger |
| `personHasCityOfDeath` | initial/abbrev name | 10 | 10 empty, identity ambiguity |
| `personHasCityOfDeath` | four-plus-token name | 10 | 10 empty, identity ambiguity |
| `awardWonBy` | prize/award with person-name shape | 9 | very high output cardinality |

INFERRED:

- The most exploitable shapes are those where the final prediction has a
  suspicious shape and the sidecar graph contains suppressed candidates:
  area/capacity numeric conflicts, city empties with candidate mentions, stock
  post-guard venue short forms, borders singleton/incomplete sets, and award
  metadata/role/list packing.

## 6. Audit-0082 coverage: what is already fixed

OBSERVED from current A/B/C configs:

| profile | split | probe status | stock prompt | stock dominance | repair enabled | repair stack |
|---|---|---|---:|---:|---:|---|
| A | test | `CALIBRATION_REVIEW_LEADERBOARD_PROBE` | true | false | false | none |
| B | test | `CALIBRATION_REVIEW_LEADERBOARD_PROBE` | true | false | true | stock guard/dedupe/rescue; award metadata; border alias/reciprocity; L8/L9 |
| C | test | `CALIBRATION_REVIEW_LEADERBOARD_PROBE` | true | false | true | all B plus border sweep, death rescue, area/capacity numeric rescue, award witness/time-sliced recall |

Profile C caps:

| relation | cap |
|---|---:|
| `countryLandBordersCountry` | 3 |
| `companyTradesAtStockExchange` | 2 |
| `hasArea` | 3 |
| `hasCapacity` | 3 |
| `personHasCityOfDeath` | 5 |
| `awardWonBy` | 8 |

Existing tests in `tests/test_leaderboard_repair.py` cover disabled-stack
identity, no web/RAG/TEST-gold markers in the repair package, stock
multi-listing rescue with support, border reciprocity non-union on UNKNOWN,
city living-status empty preservation, numeric 5% clustering, award
normalizer idempotence, bounded budgets, profile parsing, and explicit
leaderboard-probe readiness.

## 7. Remaining weaknesses

Highest-impact newly observed weaknesses:

1. OBSERVED: `submission-best` city remains almost entirely empty (`98/100`).
   Profile C attacks this, but current C does not first reuse M14 evidence.
2. OBSERVED: area remains mostly empty (`71/100`), and short ambiguous
   geographic names dominate.  Current area typing can misclassify short
   non-country entities as COUNTRY.
3. OBSERVED: award output contains 84 colon-bearing objects, 49 repeat
   parentheticals, 37 NONE/UNKNOWN tokens, 30 role-leakage-like strings, and 6
   packed list-like objects.  Existing cleanup helps but does not split packed
   lists or chunk witness verification.
4. OBSERVED: current stock L9 guard dry-run over `submission-best` removes 58
   values, including short/brand exchange-like forms.  The guard may be too
   brittle for B/C if A/B/C produce short exchange names.
5. OBSERVED: border predictions have 9 reciprocity conflicts and sidecar graphs
   show support>=2 non-output border hypotheses across 9 rows.
6. OBSERVED: capacity predictions contain repeated generic round numbers.
   Current C treats roundness as a trigger, but existing M12 cluster evidence is
   not consumed before making new calls.
7. OBSERVED: `outputs/submission-best` contains only `predictions.jsonl`; the
   latest best artifact has no repair/trace sidecar in the same directory.
   That limits post-submission forensic attribution.
8. INFERRED: L7-L9 currently apply relation repair, then L9, then L8, then L9.
   There is no final "why did L9 delete this?" model-auditor or graveyard
   recovery after the second L9 pass.

## 8. Unused evidence / telemetry opportunities

OBSERVED sidecar availability from
`outputs/v3_test_16f60fb1_20260810T160048Z/run`:

| artifact | rows | useful signal | L7-L9 currently consume it? | L10+ opportunity | extra calls |
|---|---:|---|---|---|---|
| `query_profiles.jsonl` | 475 | risk dimensions: missingness, numeric ambiguity, identity ambiguity, verification priority | not directly | trigger repairs from risk rather than only output shape | zero |
| `prompt_programs.jsonl` | 475 | positive/negative constraints, semantic cues, subject directives | not directly | final auditor can reuse exact relation instructions | zero |
| `parametric_memory.jsonl` | 475 | pseudo-memory, self-ask, rewrite records | not directly | graveyard recovery and second opinion from already-paid recall | zero to low |
| `numeric_specialist.jsonl` | 198 | numeric observations, clusters, cross-unit checks, hard-definition violations | not directly | numeric evidence reuse before C calls | zero |
| `large_open_set_specialist.jsonl` | 10 | award facet states, near-miss mentions, duplicate ratio | not directly | award witness prioritization/chunking | zero |
| `null_temporal_specialist.jsonl` | 100 | life-status observations, gate, locality observations, near-miss mentions | not directly | death evidence reuse, avoid redundant status calls | zero |
| `small_set_specialist.jsonl` | 167 | stock/border observations, gate, closure, pending checks, near-miss mentions | not directly | stock short-venue audit and border candidate rescue | zero |
| `atomic_consensus.jsonl` | 436 | candidate states, null state, numeric clusters, pending checks, query events | indirectly through V3 graph only | richer scoring from candidate-level consensus | zero |
| `specialist_verification.jsonl` | 436 | M17 catalogues; zero executed results in this artifact | only if hgraph summarizes | choose a tiny post-final subset for verification | model-call |
| `bidirectional_verification.jsonl` | 436 | M18 catalogues; zero executed results in this artifact | not directly | pairwise checks on contradictions/reciprocity only | model-call |
| `layer4_evidence.jsonl` | 436 | integrated candidates/propositions/numeric targets | not directly | zero-call graveyard scoring | zero |
| `coverage_gap.jsonl` | 436 | residual, weak facets, unresolved mass, numeric stability | not directly | trigger second opinion only on high residual | zero |
| `relation_budget.jsonl` | 781 | M20 plans, reservations, denials, settlements | separate from L7-L9 | avoid adding calls to rows already denied/exhausted | zero |
| `micro_planner.jsonl` | 306 | selected/denied action and utility state | separate from L7-L9 | route final unresolved rows to highest-value action family | zero |
| `v3_hypothesis_graph.jsonl` | 436 | hypotheses, support, contradictions, failure state | yes, in summarized form when available | graveyard recovery with stricter criteria | zero/model-call |
| `trace.jsonl` | 475 | final candidates, empty reasons, call counts | `Prediction.candidates` in memory | final shape and empty-reason triage | zero |

Candidate-graveyard counts from the older blind TEST sidecars:

| relation | rows with sidecar graph | rows where graph candidates > output | support>=2 non-output hypotheses | rows with support>=2 non-output |
|---|---:|---:|---:|---:|
| `awardWonBy` | 10 | 10 | 110 | 7 |
| `companyTradesAtStockExchange` | 61 | 61 | 15 | 10 |
| `countryLandBordersCountry` | 67 | 67 | 17 | 9 |
| `hasArea` | 100 | 87 | 52 | 46 |
| `hasCapacity` | 98 | 71 | 25 | 19 |
| `personHasCityOfDeath` | 100 | 40 | 9 | 9 |

Coverage residuals from the same sidecars:

| relation | mean residual | median | max | rows >=0.5 | rows >=0.75 |
|---|---:|---:|---:|---:|---:|
| `awardWonBy` | 0.645 | 0.563 | 0.975 | 9 | 2 |
| `companyTradesAtStockExchange` | 0.823 | 0.933 | 1.000 | 61 | 33 |
| `countryLandBordersCountry` | 0.627 | 0.568 | 0.900 | 64 | 15 |
| `hasArea` | 0.655 | 0.500 | 1.000 | 54 | 39 |
| `hasCapacity` | 0.794 | 1.000 | 1.000 | 73 | 63 |
| `personHasCityOfDeath` | 0.910 | 0.952 | 1.000 | 95 | 91 |

INFERRED:

- M19 residual is too available to ignore.  It should be a trigger feature for
  L10/L11 rather than only a sidecar.
- Existing specialist modules already spent many calls; repeating the same
  status/numeric/award probes in L7 can waste budget if the original evidence
  is still inspectable.

## 9. Generic hard-coded knowledge opportunities

Already encoded generically:

- M0 contracts encode relation semantics and hard negatives for all six
  relations.
- Relation profiles encode failure families and search/verification policies.
- V3.1 city verifier boundary encodes birth/residence/burial/hospital/country
  exclusions.
- L7 repair prompts encode stock listing scope, land-border scope, death-city
  exclusions, area/lake/island semantics, capacity variants, and award role
  exclusions.
- L9 encodes singleton city/numeric, no self-border, stock wrong type, award
  metadata cleanup and alias dedupe.

Missing or underused generic knowledge worth adding:

- A packed list inside one `ObjectEntities` string is not one award recipient
  when it is a time/category bucket such as `2000s: A, B, C`.
- A short stock venue name can be a real venue short form.  `ticker_only` and
  `city_or_bare_location` should not be terminal rejection reasons when the
  string has exchange-brand shape or pre-final support.
- City-of-death requires exact-person identity before life-status or locality
  recall; same-name collisions are a generic risk.
- Numeric relations should maintain a unit hypothesis and attribute hypothesis
  alongside the numeric value: km2 vs mi2, total vs land/water/basin, current vs
  historical/seated/record/attendance.
- Border relation is symmetric, but overseas/dependency/integral-territory
  status can change whether a candidate counts; reciprocal repair should keep
  pairwise verifier semantics, not blind union.
- Award roles should have negative evidence states: recipient, nominee, jury,
  committee, organizer, field-famous, similarly-named-award recipient.
- A final row with high M19 residual and no repair-call spend should be eligible
  for one targeted second opinion in recall profiles.

None of these require subject-specific factual tables.

## 10. Proposed L10+ repair modules

PROPOSED modules, all post-pipeline and feature-flagged:

1. `L10OutputSemanticAuditor`
   - Relations: all.
   - Trigger: final output has control tokens, metadata prefixes, subject copy,
     impossible singleton/cardinality shape, non-numeric numeric values, or
     duplicate aliases.
   - Input: final prediction, relation contract, L9 decisions.
   - Calls: zero.

2. `L10StockShortVenueSemanticHoldout`
   - Relations: stock.
   - Trigger: stock guard wants to drop a value only because it is short,
     alphanumeric, acronym-like, or brand-venue-like.
   - Input: final values, candidate signals, M15 listing observations if
     available.
   - Calls: zero if existing evidence is decisive; otherwise one Qwen
     exchange-type verifier.

3. `L10AwardPackedRecipientSplitter`
   - Relations: award.
   - Trigger: metadata-normalized object still contains a time/category bucket
     list or 3+ comma/conjunction-separated person-like names.
   - Input: final award values and metadata-normalization provenance.
   - Calls: zero, with optional witness verification downstream.

4. `L10NumericEvidenceReuseResolver`
   - Relations: area/capacity.
   - Trigger: empty/low-confidence final output; competing M12 clusters;
     suspicious repeated rounded output.
   - Input: M12 clusters, M16 numeric hypotheses, Layer4 numeric targets.
   - Calls: zero unless clusters conflict outside tolerance.

5. `L10DeathEvidenceReuseGate`
   - Relations: city.
   - Trigger: empty city output.
   - Input: M14 status/gate/locality/near-miss observations.
   - Calls: zero if evidence is decisive; otherwise route to existing C recall.

6. `L10CandidateGraveyardRecovery`
   - Relations: all, with relation-specific rules.
   - Trigger: high-support non-output hypotheses or high M19 residual.
   - Input: V3 pre-M8/post-hoc hypothesis graph, M16 consensus, Layer4 state.
   - Calls: zero for strict deterministic rescue; one Qwen verifier for risky
     candidates.

7. `L10NegativeEvidenceAuditor`
   - Relations: all.
   - Trigger: candidate has positive support and recorded near-miss/invalid role
     evidence.
   - Input: specialist near-miss mentions, hard contract violations, verifier
     labels, contradictions.
   - Calls: zero.

8. `L11UncertaintyTriggeredSecondOpinion`
   - Relations: city, area, capacity, stock, borders, award.
   - Trigger: final shape remains suspicious after L10, e.g. empty city with
     deceased evidence, empty area with numeric clusters, stock singleton with
     multiple supported venues, award huge set after cleanup.
   - Input: L10 findings and M19 residual.
   - Calls: one bounded relation-specific prompt/view, plus verifier when needed.

9. `L11CompletenessAuditor`
   - Relations: borders, stock, awards.
   - Trigger: set-valued row has high residual, singleton collapse, reciprocity
     conflict, or open-set output remains extreme.
   - Input: current set, candidate graveyard, facet states.
   - Calls: one "is current set likely complete?" call; expansion only if NO.

10. `L11EntityIdentityLock`
    - Relations: capacity, city, stock, area.
    - Trigger: ambiguous subject shape, qualifier/comma, generic venue name,
      legal entity/subsidiary ambiguity, or person initials.
    - Input: subject string, supplied location/qualifier, current candidate.
    - Calls: one Qwen verifier only on high-risk rows.

11. `L11TemporalStateResolver`
    - Relations: stock, capacity, city, borders, awards.
    - Trigger: current/historical/former/listing/capacity/life-status conflict.
    - Input: temporal labels from M14/M15, V3 hypothesis qualifiers, final values.
    - Calls: one resolver when contradictions are present.

12. `L11ModelDisagreementRouter`
    - Relations: all.
    - Trigger: Mistral and Qwen disagree, or M16 records contradiction kinds.
    - Input: independent support units, verifier labels, contradiction kind.
    - Calls: targeted resolver selected by disagreement class.

## 11. Border deep dive

OBSERVED:

- Blind output has 11 empty border rows, 9 singleton rows, and 9 reciprocity
  conflicts among countries that are also subjects.
- Current V3 owner recall spec explicitly returns no border recall actions, so
  Profile C's `BorderDirectionalSweep` is the only active expansion route.
- The older sidecar graph has 17 support>=2 non-output border hypotheses.

INFERRED:

- The safest border improvement is not larger recall fan-out.  Borders already
  score strongly on TRAIN, so every expansion should be pairwise-verified and
  explainable.

PROPOSED:

- Use three-stage border repair:
  1. zero-call graveyard scan: support>=2 non-output border-country hypotheses;
  2. current-set completeness prompt: "which terrestrial neighbor is missing?";
  3. pairwise verifier only on additions/removals and reciprocity conflicts.
- Directional prompting can be improved by clockwise or region-grouped sweep,
  but only after the zero-call graveyard scan because the graph already exposes
  many candidate neighbors.
- Do not implement automatic border-count filling.  Border counts would be
  generic structure only at the relation level, but subject-specific counts
  would become an answer table.

## 12. Numeric deep dive

OBSERVED:

Area:

- `71/100` empty rows.
- `29/29` emitted values are numeric.
- `5/29` have decimals; `8/29` are multiples of 10; no scientific notation in
  final output.
- No repeated area value appears in final output.

Capacity:

- `25/98` empty rows.
- `73/73` emitted values are numeric.
- Repeated values: `10000` x8, `60000` x7, `30000` x7, `20000` x3,
  `45000` x3.
- `51/73` outputs are multiples of 1000; `10/73` are powers of ten.
- Final outputs expose no multi-numeric rows because M8 collapses to singleton.

TRAIN-SUPPORTED:

- Numeric wrong-attribute and no-recall failures are already established in
  existing TRAIN mining artifacts.

PROPOSED generic numeric rules:

- Keep a numeric value, unit, and attribute tuple through L10:
  `value`, `unit`, `attribute`, `entity_identity`, `temporal_state`.
- Prefer clusters where at least two independent sources agree within the
  evaluator's 5% tolerance.
- Use ratio diagnostics as triggers, not correctness claims:
  values separated by 10x/100x/1000x suggest unit or wrong-entity conflicts.
- For capacity, classify current/historical/seated/standing/concert/attendance
  before selecting.
- For area, classify total/land/water/basin/catchment/wrong-entity before
  selecting.

## 13. Award deep dive

OBSERVED:

- All 10 award rows are non-empty, but the output is structurally noisy.
- Existing metadata normalizer would remove 115 net objects on this artifact,
  but it leaves some packed lists as a single object.
- Role-leakage-like strings cluster around committee, jury, panel and team
  roles.  Those are generic hard negatives for recipient relations, but
  organizations/projects are not globally invalid and must not be dropped by
  type alone.

PROPOSED:

- Run deterministic cleanup in this order:
  1. strip/drop time/category/NONE/repeat metadata;
  2. split only structurally obvious packed lists with provenance;
  3. dedupe aliases;
  4. apply role-negative evidence;
  5. run chunked witness verifier on high-risk/high-impact candidates.
- Witness should be batched by era or recipient type.  A single 100-candidate
  prompt is cheap but can hide errors and produce incomplete INVALID lists.
- Preserve organizations/projects unless role evidence says they are not
  recipients.

## 14. Stock deep dive

OBSERVED:

- Stock still shows three distinct output pathologies in `submission-best`:
  city leakage, acronym/full-name alias duplication, and overwide exchange-city
  lists.
- Current B/C repair addresses the intended broad problem, but the deterministic
  wrong-type rule is too terminal for short venue forms.

INFERRED:

- Audit-0081's strict listing prompt is a high-value suppressor, but any
  downstream guard that deletes real venue short forms can give back that gain.
- The dangerous cases are values rejected because they are short or acronym-like
  while the relation itself permits venue short forms.

PROPOSED:

- Convert stock wrong-type deletion into a two-bin decision:
  `hard_drop` for control tokens, subject copies, index/segment/broker/clearing
  terms; `needs_exchange_type_check` for short venue-like values.
- Run Qwen on `needs_exchange_type_check` values in one batch per row.  Ask only
  "is this candidate a stock exchange venue name?", not "is the company listed
  there".
- After type survival, run listing-scope verification only on candidate venues
  that would change the output.

## 15. City deep dive

OBSERVED:

- City output shape is almost entirely empty.
- Current sidecar graphs still contain candidate city hypotheses for 40 rows;
  9 rows have support>=2 non-output hypotheses.
- M14 sidecars carry status and locality evidence that L7 city repair does not
  directly consume.

PROPOSED:

- Stage city repair as:
  1. reuse M14 status/locality evidence;
  2. if status is confidently alive, preserve empty;
  3. if status is deceased or unknown with locality evidence, run exact-person
     identity verifier;
  4. convert hospital/death-location mentions to city only when the model
     explicitly states the city or a locality parser extracts one;
  5. enforce singleton after contrast.

No proposal here forces an answer for a living person.

## 16. Ranked leaderboard opportunities

| priority | module | relations helped | trigger | input evidence | deterministic/model | extra calls | expected benefit | main risk | complexity | overlaps 0082? | profile |
|---:|---|---|---|---|---|---:|---|---|---|---|---|
| 1 | `StockShortVenueSemanticHoldout` | stock | L9 would drop short/acronym/brand venue shape | final output, candidate signals, M15 | model-assisted | 0-1 | VERY HIGH | preserving ticker/city leakage | LOW | extension | D/E |
| 2 | `DeathEvidenceReuseGate` | city | empty city output | M14 status/locality/near-miss | deterministic first | 0 | VERY HIGH | stale/ambiguous status evidence | LOW | extension | D |
| 3 | `NumericEvidenceReuseResolver` | area/capacity | empty, low-confidence, conflicting numeric clusters | M12/M16/Layer4 | deterministic first | 0-1 | VERY HIGH | selecting wrong attribute cluster | MEDIUM | extension | D/E |
| 4 | `CandidateGraveyardRecovery` | all | support>=2 non-output hypotheses | V3 graph, M16, Layer4 | deterministic+verifier | 0-1 | HIGH | resurrecting known FPs | MEDIUM | extension | E |
| 5 | `AwardPackedRecipientSplitter` | award | bucketed/comma-packed recipient object | final output | deterministic | 0 | HIGH | splitting legitimate joint recipient names | LOW | new | D |
| 6 | `AwardRoleNegativeAuditor` | award | jury/committee/nominee/organizer role terms | final output, M13 near-miss | deterministic+verifier | 0-2 | HIGH | over-dropping valid organization awards | LOW | extension | D/E |
| 7 | `BorderGraveyardCompletenessAuditor` | borders | singleton/empty/high residual, support>=2 non-output neighbor | V3 graph/M19 | verifier | 0-1 | MEDIUM | FP on already strong relation | MEDIUM | extension | E |
| 8 | `L10OutputSemanticAuditor` | all | final impossible shape | final output, relation contract | deterministic | 0 | MEDIUM | too conservative if generic pattern is valid | LOW | partly | D |
| 9 | `EntityIdentityLockVerifier` | capacity/city/stock/area | ambiguous subject, qualifier, generic venue/person name | subject, candidate, evidence | model | 1 | MEDIUM | extra calls on low-value rows | MEDIUM | partial | E/F |
| 10 | `CompletenessAuditor` | borders/stock/award | set seems incomplete after repair | final set, graph, residual | model | 1-2 | MEDIUM | recall expansion adds FPs | MEDIUM | partial | F |
| 11 | `TemporalStateResolver` | stock/capacity/city | current/historical/former conflict | M14/M15/V3 qualifiers | model | 1 | MEDIUM | resolver overconfidence | MEDIUM | partial | E |
| 12 | `ModelDisagreementRouter` | all | Mistral/Qwen conflict or contradiction kind | M16/M17/M18/hgraph | model | 1 | MEDIUM | more calls without decisive signal | MEDIUM | new | F |

Best zero-call opportunities:

- Consume M14 status/locality evidence before DeathExistenceGate.
- Consume M12 numeric clusters before area/capacity recall.
- Split award bucketed packed-list objects after metadata cleanup.
- Apply role-negative award strings deterministically as support penalties.
- Use M19 residual and V3 non-output support as repair triggers.
- Preserve stock short venue candidates from deterministic deletion until a
  semantic type check runs.

Best model-call opportunities:

- Qwen stock short-venue type check before L9 terminal deletion.
- Qwen candidate-graveyard verifier for support>=2 non-output candidates.
- Qwen numeric attribute resolver only for conflicting existing clusters.
- Qwen border pair verifier for graveyard additions and reciprocity conflicts.
- Qwen award witness chunks by era/type instead of one oversized candidate list.
- Exact-identity verifier for ambiguous city/capacity/stock rows.

## 17. Proposed D/E/F profiles

Do not create these configs until A/B/C leaderboard scores are known.

PROFILE D - Zero/cheap final semantic auditor

- Base: whichever of A/B/C performs best on leaderboard.
- Add: `L10OutputSemanticAuditor`, `DeathEvidenceReuseGate`,
  `NumericEvidenceReuseResolver` in zero-call mode,
  `AwardPackedRecipientSplitter`, `AwardRoleNegativeAuditor` deterministic mode,
  stock short-venue holdout that prevents deterministic deletion but does not
  add listing venues without verification.
- Goal: harvest obvious structural gains without materially increasing call
  cost.

PROFILE E - Graveyard and disagreement resolver

- Base: D.
- Add: `CandidateGraveyardRecovery` with relation-specific criteria,
  Qwen stock short-venue type check, numeric attribute resolver on existing
  clusters, border graveyard pair verifier, exact-identity verifier for high-risk
  city/capacity/stock rows.
- Goal: exploit already-generated suppressed evidence with bounded verification.

PROFILE F - Completeness and high-call second opinion

- Base: E.
- Add: `CompletenessAuditor` for borders/stock/award, current-set missing-neighbor
  prompt, chunked award witness/time-sliced recall, second opinion for city/area
  rows still empty with high residual, temporal-state resolver.
- Goal: maximize recall after precision/shape guards are in place.

Recommended submission order after A/B/C scores:

1. Submit the best A/B/C profile result first if it is already available.
2. Build D if A/B/C reveal that deterministic repair moved the score or if B/C
   are hurt by type-guard brittleness.
3. Build E before F unless leaderboard feedback shows recall is still the main
   deficit; E is the better risk-adjusted next step.
4. Reserve F for the last high-call probe because it has the largest FP risk.

## 18. Risks and limitations

- This audit uses blind TEST outputs only for shape and artifact forensics.  It
  does not identify hidden TEST correctness.
- The sidecar evidence review uses
  `outputs/v3_test_16f60fb1_20260810T160048Z/run`, not a fresh A/B/C run.
  Current A/B/C sidecars may differ after the stock prompt and repair stack.
- Some observations are parametric/semantic suspicions, especially short stock
  venue forms.  They should be validated with TRAIN or a leaderboard probe
  before becoming permanent production policy.
- Existing `docs/audits/0081-stock-listing-entity-real-weight-targeted-diagnostic.md`
  still opens as a Phase A pre-run document, while Audit 0082 records the later
  real-weight stock result.  This audit relies on the current configs and
  Audit 0082 for the Profile A/B/C stock background.
- `outputs/submission-best/` contains only `predictions.jsonl`; without matching
  repair sidecars, some decisions cannot be attributed for that exact file.

## 19. Recommended next action after A/B/C scores

If A/B/C leaderboard scores arrive before another GPU window:

1. Compare A vs B: if B underperforms A, inspect stock guard and award cleanup
   first, especially short venue deletion and packed-list handling.
2. Compare B vs C: if C improves, prioritize D/E numeric and city evidence reuse
   to lower call cost and stabilize the gains; if C harms, isolate aggressive
   recall modules by relation before expanding.
3. Build Profile D as the next fastest patch if time allows, because its main
   modules are zero-call and late-layer isolated.
4. Build Profile E only if D is stable or if A/B/C feedback points to suppressed
   high-support candidates.
5. Defer Profile F until a high-call probe is justified by score feedback.

Final audit conclusion:

- The largest newly discovered opportunity is not another broad recall layer.
  It is late reuse of already-paid evidence, especially M12 numeric clusters,
  M14 death evidence, M15 stock/border observations, M19 residual, and V3
  non-output hypotheses.
- The largest newly discovered risk is stock L9 over-deletion of short
  venue-name forms in B/C.
- The cheapest deterministic opportunity is award packed-list/role cleanup
  after the existing metadata normalizer.

## 20. Leaderboard hotfix addendum - Profile A is the new base

Date: 2026-08-12

New hidden-TEST leaderboard evidence supplied by the user after this audit was
first drafted:

| profile | overall F1 | `awardWonBy` | `companyTradesAtStockExchange` | other four relations |
|---|---:|---:|---:|---|
| Profile A | 0.4906 | 0.2929 | 0.7103 | unchanged vs B |
| Profile B | 0.4837 | 0.3105 | 0.6757 | unchanged vs A |

Blind stock shape supplied with the same result:

| profile | empty | objects | mean | max |
|---|---:|---:|---:|---:|
| Profile A Stock | 46 | 101 | 1.01 | 28 |
| Profile B Stock | 50 | 100 | 1.00 | 27 |

Interpretation:

- Profile A is now the frozen best base.
- `AwardMetadataNormalizer` helped hidden TEST.
- B's Stock repair stack harmed hidden TEST.
- The Stock repair decreased recall without meaningful precision gain.
- Future aggressive probes must not inherit B's Stock repair path.

Hotfix profiles added:

- `configs/experiments/cover_kbc_v3_2_profile_a_plus_award_test.yaml`
  - Base: Profile A.
  - Keeps `stock_listing_entity_prompt=true` and
    `stock_support_dominance=false`.
  - Enables only `AwardMetadataNormalizer`.
  - Sets all non-award repair caps to 0 and disables global L8/L9 so every
    non-award relation follows the Profile-A path.

- `configs/experiments/cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml`
  - Base: Profile A, not Profile B.
  - Keeps Profile-A Stock semantics.
  - Disables `stock_entity_guard`, `stock_alias_dedupe`,
    `stock_multi_listing_rescue`, generic Stock L8 consistency, and Stock L9
    terminal guard.
  - Enables non-Stock aggressive repair for awards, borders, city, area and
    capacity with bounded caps.

Source hotfix:

- `RepairFeatures.l8_stock_consistency` and `RepairFeatures.l9_stock_guard`
  were added as default-on switches so existing B/C behavior remains unchanged,
  while C2 can keep useful non-Stock L8/L9 without mutating Stock.

Validation target:

- A_PLUS_AWARD and C2 must be accepted only through the existing explicit
  `CALIBRATION_REVIEW_LEADERBOARD_PROBE` path.
- No TRAIN, VAL or TEST neural inference was run for this hotfix.
