# Audit 0076 - V3.1 Score-Recovery Engineering

## Scope

This milestone implements V3.1 score recovery in two separate tracks: Class A
finalization-only changes that preserve the Audit 0073 M20/M21 calibration, and
Class B prototypes that do not and are therefore not runnable as production.

Base SHA (work started from):

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

Frozen calibrated V3 TEST submission source (not rewritten, not mutated):

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

Verified present:

```bash
git cat-file -e 16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5^{commit}
```

No TEST gold, TEST prediction, VAL data, web data, RAG, external KB, external
factual corpus or model call was used. No neural model was fine-tuned. No
subject-specific TRAIN answer lookup table was added. No immutable real-run
artifact under `outputs/` was modified; the replay reads persisted state and
writes only to a new directory.

## Audit 0075 Findings Used

Verified against the artifacts in `outputs/v3_train_error_forensics/`, not only
the audit prose.

| Finding | Value | Used for |
|---|---:|---|
| TRAIN macro-F1 baseline | `0.40333` | replay baseline, reproduced exactly |
| exact rows | `159` | outcome accounting |
| incorrect rows | `318` | outcome accounting |
| `L1_NEVER_RECALLED` | `242` | scoped *out* of Class A |
| `L5_FINALIZATION_OR_NORMALIZATION_LOSS` | `75` | motivated retention work |
| gold seen before final output in wrong rows | `109` | motivated retention work |
| `hasArea` empty / L1 / L5 | `65` / `58` / `17` | retention target |
| `hasCapacity` L1 / large misses / too-large | `92` / `75` / `64` | Class B only |
| stock FP / over-generated / L5 | `95` / `54` / `42` | rejection-first target |
| award FN / FP, all rows L1 | `418` / `266` | structural repair + Class B |
| `personHasCityOfDeath` L1 / L5 / FP rows | `51` / `10` / `16` | city semantics |
| borders macro-F1 | `0.964` | frozen, hard non-regression gate |
| `single_valued_keep_first_prediction` | `-0.00117` | rule explicitly *not* implemented |
| `emit_control_survived_when_empty` | `+0.01712` | investigated, not copied |

## What CONTROL_SURVIVED Actually Means

`src/cover_kbc/diagnostics/stages.py` defines it exactly:

> `Candidate.status` is `ACCEPTED` after `scoring.decide_status`, i.e. the last
> state before Module 8's relation-specific selection.

So a row with a non-empty `CONTROL_SURVIVED` stage and an empty `FINAL_EMITTED`
is a row where the acceptance policy accepted a candidate and **Module 8 then
discarded it**. That is a gold-independent statement about production state, and
it is the whole basis of the retention rule. The audit-0075 counterfactual was
not copied; its *mechanism* was located instead.

### The mechanism, located

Measured over the persisted 477-row TRAIN run:

- rows with `CONTROL_SURVIVED` non-empty and `FINAL_EMITTED` empty: **55**
- of those, `hasArea`: **55**; every other relation: **0**

Every other relation emits every accepted candidate. Border, stock, city and
capacity empties are all `no_candidate_generated` - true recall failures, not
finalization losses. The `109`-row "gold seen earlier" figure from audit 0075 is
therefore *not* all recoverable at finalization; only this channel is.

The bug is in `selection.select_numeric_robust`. It takes `clusters[0]` and
returns `[]` if that cluster carries no accepted candidate. But
`normalization.numeric.cluster_values` orders clusters by
`(-size, relative_mad, representative)`. When every cluster is a singleton - the
normal case for a thinly recalled area - size and dispersion tie and the winner
is decided by **the numerically smallest value**. Acceptance is not consulted.

TRAIN row 1, Mangareva, is the bug in one row: candidate `1` is UNRESOLVED,
candidate `15` is ACCEPTED, gold is `15.4`. The tiebreak hands the query to `1`,
`1` is not emittable, and the row goes out empty.

## Calibration Compatibility Proof (Class A)

For one query the pipeline runs, in order:

1. `enumerate_query` / `verify_graph`;
2. `decide_graph` -> `_run_consensus` (M16), then `_run_v3_control_loop` (M20/M21
   and V3 action execution);
3. `decide_graph` -> `selection.finalize` (M8);
4. `_observe_v3_core` - post-hoc telemetry.

The V3 hypothesis graph Module 21 reads is built at step 2 by
`pipeline._v3_hypothesis_graph`, which passes **`prediction=None`**
(`src/cover_kbc/pipeline.py:2554`). The only `build_hypothesis_graph` call that
receives a prediction is step 4 (`src/cover_kbc/pipeline.py:3290`), whose result
goes to `v3_core_results` for persistence and is never read back during
inference.

Therefore a change confined to step 3 cannot alter V3 action eligibility, action
execution, action cost, state transitions, hypothesis construction, M21 input
state, or M20 budget behaviour. It can only alter `Prediction.object_entities`
and the `emitted` flags step 4 records.

This argument is restated machine-readably in
`src/cover_kbc/v3_1/compatibility.py`, and `test_every_enabled_feature_is_declared`
fails if a feature is added to a config block without an `InterventionRecord`
stating where it runs. Safety is never inherited from the block a flag sits in.

## Implemented Interventions

Every flag defaults to `False`. With no configuration, V3.1 is inert.

### Class A - SAFE_WITH_EXISTING_CALIBRATION

#### 1. `final_candidate_retention` (FINAL_CANDIDATE_RETENTION)

*TRAIN discovery evidence:* 55 rows recorded a non-empty `CONTROL_SURVIVED` and
an empty `FINAL_EMITTED`, all `hasArea`; cluster ties are broken by smallest
representative, which ignores acceptance.

*Production predicate:* if the winning numeric cluster carries no ACCEPTED
candidate, re-run the **unchanged** cluster ordering over clusters that carry an
ACCEPTED, non-INVALID candidate; emit nothing if none qualifies.

Reads `Candidate.status`, `Candidate.verifications` and cluster geometry only.
A cluster whose strongest verdict is INVALID is never retained, so the rule can
only rescue a candidate the pipeline itself still believed in. It widens the
pool; it never overrides an ordering the pool already supports, so a row that
already emits something is untouched.

Location: `selection.select_numeric_robust`, `selection.select_numeric_highest_valid`.

Four cluster-choice policies were compared (dominant-first, max support, max
score, max accepted-support). All four produce identical TRAIN output, so the
minimal formulation - preserve the existing ordering, restrict the pool - was
chosen.

#### 2. `enumeration_label_repair`

*TRAIN discovery evidence:* 76 emitted `awardWonBy` values are whole enumeration
lines, not entities: `'Groups: NONE'`, `'Organisations: NONE'`,
`'Individuals: Albert Schweitzer'`, `'1990s: Alan Shearer'`,
`'1901-1909: Wilhelm Conrad Röntgen'`. One row, *Order of the Golden Fleece
(Georgia)*, emitted four values of which all four were `: NONE`.

Both halves cost score. `'Groups: NONE'` is a pure false positive - the payload
is the prompt's own "no members" token. `'1990s: Alan Shearer'` names a real
winner but can never match gold, because the evaluator normalises the whole
string.

*Production predicate:* split a final value on its first `':'`. If the prefix is
a bucket label - temporal (`1990s`, `1901-1909`, `1974`) or one of a closed
vocabulary of generic enumeration nouns - drop the value when the payload
normalises to an abstention token, otherwise reduce it to the payload;
deduplicate on `strict_key`. Any other value is returned untouched.

This extends an *existing* production guard: `selection._NEVER_AN_OBJECT`
already refuses a bare `NONE`; the guard was defeated purely by the label prefix.
The label vocabulary is generic English enumeration structure - no subject, no
answer, no relation-specific fact.

Location: `selection._final_values`, called from `selection.finalize`.

#### 3. `stock_support_dominance`

*TRAIN discovery evidence:* stock's dominant loss is precision (95 FP vs 33 FN).
On rows where accepted listings differ in independent acquisition support, the
maximally supported one is the gold one: McBride plc (LSE 2, Irish SE 1),
BNP Paribas (Euronext Paris 2, Euronext Brussels 1), JGC Holdings (Tokyo 2,
Nagoya 1).

*Production predicate:* for `companyTradesAtStockExchange` **only**, if accepted
listings differ in independent acquisition support, keep those at the maximum;
on a tie keep all.

Because the test is *relative*, no fixed cardinality is imposed: a company with
three equally corroborated listings still emits three. Audit 0075's "do not
globally enforce cardinality=1" constraint is satisfied structurally.

**This is a trade, not a free win.** It improves 12 TRAIN rows and harms 7; three
of the harmed rows fall to F1 `0.0` because the dropped listing was the gold one
(Nautilus, Tokmanni, GD Power Development). Net relation macro-F1 `+0.00700`,
net overall `+0.00147`. It is the one safe feature a reviewer might reasonably
choose to disable, and it is individually toggleable for exactly that reason.

Location: `selection.select_small_set`, gated on relation name so borders - which
share the SMALL_SET programme - cannot inherit it.

#### 4. `stock_structural_validation`

*TRAIN discovery evidence:* guardrail. 0 such emissions observed on TRAIN
(0 subject-as-object emissions across all 477 rows).

*Production predicate:* for `companyTradesAtStockExchange` only, drop a listing
whose `strict_key` equals the subject's, or is a status/abstention token
(`NONE`, `unlisted`, `OTC`, ...).

**Ticker rejection was evaluated and deliberately refused.** It cannot be decided
structurally: `NYSE`, `AIM`, `SIX` and `Nasdaq` are legitimate exchange names
with exactly the shape any ticker heuristic keys on - short, capitalised, no
spaces - and `AIM` is the gold answer for TRAIN row 211. A rule that cannot
separate the two would destroy real answers to remove hypothetical ones. A test
asserts these four names survive.

#### 5. `numeric_output_canonicalization`

*TRAIN discovery evidence:* guardrail. `hasArea` has only 2 scale-like errors and
1 near-tolerance row, so formatting is not the dominant failure. 0 of 200 numeric
TRAIN rows change.

*Production predicate:* re-serialise an emitted numeral through the
official-parser-safe formatter, converting units **only** when an explicit unit
token is present; never convert a bare number.

It is included because an un-parseable or double-converted numeral is a silent
false positive under the official `try_parse_number`, and that risk exists on
TEST whether or not it fired on TRAIN.

#### City-of-death: measured, and deliberately not changed

`rank_entity_candidates` was written as an explicit, tested policy that orders on
verifier acceptance, then independent support, then score, with the candidate key
last purely for determinism. Insertion order contributes nothing - the property
that separates it from the `keep_first_prediction` rule audit 0075 measured as
harmful (`-0.00117`).

Replay shows it reproduces the existing `select_null_single` output on every TRAIN
row: **delta `0.00000`**. Of the 22 city rows carrying accepted candidates, **0**
have candidates distinguishable by support, so support-based arbitration has
nothing to arbitrate on. `personHasCityOfDeath` already emits at most one object
(`max_objects=1`), so audit 0075's "suppress multi-city output" intervention is
already in force in production and its projected 16-row gain is not available.

The honest finding is that city loss is recall (`51` L1 rows), not finalization,
and no defensible finalization-only rule recovers it. It is retained as a tested
invariant, not claimed as a gain.

### Class B - CALIBRATION_REVIEW_REQUIRED (prototyped, not run)

Declared in `src/cover_kbc/v3_1/prompts.py`, versioned `v3.1-prompts-v1`, each
with a recorded SHA256. None is wired into the live prompt path: they change
what the frozen models are asked, so they require a fresh TRAIN collection run
followed by M20/M21 re-derivation before any use.

| Feature | Relation | Role | New prompt SHA256 |
|---|---|---|---|
| `capacity_definition_prompt` | hasCapacity | enumerator | `27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36` |
| `city_of_death_contrast_prompt` | personHasCityOfDeath | verifier | `c7697baad06fd7b7e39bb708cccf0cc6f1f068ea492e1bb640c1f7a3432e07da` |
| `stock_listing_entity_prompt` | companyTradesAtStockExchange | enumerator | `39af1c5be480c85350535f75ddd2466b497d1b124bb4e3b97c63b21bda1c39f8` |
| `award_expansion_and_fp_cap` | awardWonBy | enumerator + action | `0e9d2a920396ba2c485729f0fda25de601dbe25e712c9986310db49f2e9056dc` |

The same inventory is written machine-readably to
`outputs/v3_1_score_recovery/aggressive_change_inventory.json` under `prompts`.

**Old prompt hash: none.** These are additive instructions in a new module; no
existing prompt body was edited, so `configs`/`prompt_registry` prompt hashes are
unchanged and the V3 prompt surface is bit-for-bit what the frozen submission
used. `query_intelligence.prompt_registry.COMPILER_VERSION` remains `m10-v1`.
Adopting a Class B instruction in a future milestone means editing the live
prompt path, which is when an old-vs-new hash pair becomes meaningful.

The capacity instruction distinguishes total / seated / standing / sports /
concert / historical / post-renovation / temporary capacity, asks the model to
identify the venue type first, and explicitly excludes attendance, area, cost,
dates and seat numbers - the exact confusions audit 0075 measured. The city
instruction contrastively separates death city from birth city, burial place,
country, hospital and residence. The stock instruction names the listing venue as
the answer type and excludes tickers, indices and the company itself. The award
instruction asks for distinct recalled recipients with no bucket labels, and to
stop rather than pad.

`countryLandBordersCountry` has **no prompt entry at all**, so no prompt change
can reach it; `instruction_for("countryLandBordersCountry")` returns `None` and a
test asserts it.

## Numeric Formulas Added

Exact, universal, not benchmark-derived. Applied only when an explicit unit token
is present in the value.

```
1 km2          = 1 km2
1 m2           = 0.000001 km2
1 hectare      = 0.01 km2
1 acre         = 0.0040468564224 km2
1 square mile  = 2.589988110336 km2
```

Canonical output unit is **km2**, matching both the TRAIN gold representation
(Wellington Island `5556`, Molokaʻi `673.4`, Victoria Island `217291`) and the
unit the existing acquisition path already normalises to. Unchanged from V3.

Parsing supported and tested: `75,000`, `75 000`, `75000`, `75,000.0`, `7.5e4`,
`7.5E4`, `7.5e-2`, `12'345`, `1.234.567`, `1 234,5`.

A bare number is **never** multiplied by anything. `test_a_bare_number_is_never_
scaled_by_the_square_mile_factor` and `test_no_conversion_without_an_explicit_unit`
enforce this directly.

The shared acquisition parser `normalization/numeric.py` was **not** modified,
although it has a real gap (it reads `7.5e4 m2` as `7.5` with no unit, because its
number regex stops before the exponent). Fixing it there would change which
candidates enter the evidence graph, which changes M21 input state, which
invalidates the calibration. The fix is implemented in
`v3_1/numeric_recovery.py` for the finalization path only, and the gap is
recorded here as a Class B item for a future recalibrated run.

## Offline TRAIN Replay

`scripts/replay_v3_1_finalization.py` replays Module 8 over the persisted
inference state of the full 477-row TRAIN run. No model calls, no GPU, no new
evidence. Copies only; the source run directory is read-only.

Inputs:

- run dir: `outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`
- predictions SHA256: `36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816`
- gold: `benchmark/data/train.jsonl`, SHA256 `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- evaluator: `benchmark/evaluate.py`, SHA256 `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`

### Fidelity gate

With every V3.1 feature off, the replay must reproduce the committed
`predictions.jsonl` exactly, and the script exits non-zero if it does not.

Result: **477 / 477 rows byte-identical**. The harness is therefore measuring the
production selector, not an approximation of it.

The script is deterministic: run twice, `SHA256SUMS.txt` was byte-identical.

`outputs/v3_1_score_recovery/SHA256SUMS.txt` SHA256:

`e11c7d20024a2eb8a2a446626df7dd78041bc98828bfb151c1f67d62a6f44786`

### Incremental ablation

Official evaluator, cumulative stages.

| Stage | macro-F1 | delta | micro-F1 | delta | improved | harmed |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0.40333 | +0.00000 | 0.51799 | +0.00000 | 0 | 0 |
| + final_candidate_retention | 0.41800 | +0.01468 | 0.51207 | -0.00593 | 7 | 0 |
| + enumeration_label_repair | 0.41853 | +0.01520 | 0.53065 | +0.01265 | 15 | 0 |
| + numeric_output_canonicalization | 0.41853 | +0.01520 | 0.53065 | +0.01265 | 15 | 0 |
| + stock_structural_validation | 0.41853 | +0.01520 | 0.53065 | +0.01265 | 15 | 0 |
| + stock_support_dominance | **0.42000** | **+0.01667** | **0.53079** | **+0.01279** | 27 | 7 |

Retention alone lowers micro-F1, exactly as audit 0075 predicted for its
counterfactual: it converts empty rows (precision 1.0) into answered ones. The
label repair more than repays that cost, so the **full safe stack improves both
metrics**. No stage was selected on global F1 while harming a relation: no
relation regresses at any stage.

### Per-relation deltas (full safe stack)

| Relation | baseline macro-F1 | V3.1 safe | delta |
|---|---:|---:|---:|
| awardWonBy | 0.38850 | 0.41375 | +0.02525 |
| companyTradesAtStockExchange | 0.52890 | 0.53590 | +0.00700 |
| countryLandBordersCountry | 0.96437 | 0.96437 | **+0.00000** |
| hasArea | 0.24000 | 0.31000 | +0.07000 |
| hasCapacity | 0.08000 | 0.08000 | +0.00000 |
| personHasCityOfDeath | 0.39000 | 0.39000 | +0.00000 |

### Rows

- changed: `86` (hasArea `55`, companyTradesAtStockExchange `22`, awardWonBy `9`)
- improved: `27` (hasArea `7`, awardWonBy `8`, stock `12`)
- harmed: `7` (all stock, all from `stock_support_dominance`)
- unchanged: `443`

All 7 harmed rows, in full:

| Row | Subject | baseline -> V3.1 | F1 |
|---:|---|---|---|
| 213 | West Japan Railway Company | [Tokyo, Fukuoka] -> [Tokyo] | 0.800 -> 0.500 |
| 214 | Chubu Electric Power | [Tokyo, Nagoya] -> [Tokyo] | 1.000 -> 0.667 |
| 246 | Enbridge | [Toronto, NYSE] -> [Toronto] | 1.000 -> 0.667 |
| 276 | Nautilus, Inc. | [NYSE American, NYSE] -> [NYSE American] | 0.667 -> 0.000 |
| 293 | Magna International | [Toronto, NYSE] -> [Toronto] | 1.000 -> 0.667 |
| 301 | Tokmanni | [Nasdaq Nordic, Helsinki] -> [Nasdaq Nordic] | 0.667 -> 0.000 |
| 302 | GD Power Development Company | [Shenzhen, Shanghai] -> [Shenzhen] | 0.667 -> 0.000 |

The 55 hasArea rows retention changes were **all** empty-to-non-empty, and **0**
of them have empty gold, so retention cannot cost macro-F1 on any TRAIN row.
On TEST the risk is non-zero but small: `hasArea` has 0 empty-gold rows out of
100 on TRAIN, consistent with every subject of that relation having an area.

### Border non-regression (hard gate)

`countryLandBordersCountry`: **0 rows changed**, predictions byte-identical,
macro-F1 `0.964369` unchanged. Verdict `IDENTICAL_PREDICTIONS`. The replay script
exits non-zero on any border regression. Stock rules are gated on relation name
rather than programme type, so borders cannot inherit them, and
`test_stock_rules_do_not_apply_to_borders` asserts it.

## No Oracle In Production

TRAIN gold was used to *score* the replay and to *discover* mechanisms. Every
implemented predicate is re-expressible without gold, and the split is recorded
per intervention in `src/cover_kbc/v3_1/compatibility.py` and
`outputs/v3_1_score_recovery/calibration_compatibility.json`.

Enforced by test, not by convention:

- `test_no_gold_or_label_dependency` walks the AST of every V3.1 source and fails
  on any identifier, attribute, argument or non-docstring string naming gold,
  ground truth, a label file or a row index. Docstrings are exempt by design -
  they must be free to explain which TRAIN evidence motivated a rule.
- `test_no_subject_specific_answer_tables` fails on any dict literal whose values
  are entity-shaped strings.
- `test_no_network_or_filesystem_access` fails on any network, database or file
  import.
- `test_prompts_state_relation_semantics_not_answers` fails if an instruction
  names a benchmark subject or embeds a numeric answer.

## M20/M21 Compatibility

The Audit 0073 V3 calibration artifacts are byte-identical and untracked by this
change (`git status` shows nothing modified under `configs/calibration/`):

| Artifact | SHA256 |
|---|---|
| `configs/calibration/v3/m20_relation_budget.json` | `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29` |
| `configs/calibration/v3/m21_historical_bins.json` | `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675` |
| `configs/calibration/v3/m21_planner_calibration.json` | `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6` |

Historical V2 calibration hashes, also unchanged:

| Artifact | SHA256 |
|---|---|
| `configs/calibration/m20_relation_budget.json` | `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68` |
| `configs/calibration/m21_historical_bins.json` | `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071` |
| `configs/calibration/m21_planner_calibration.json` | `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` |

## Configs

### `configs/experiments/cover_kbc_v3_1_safe_test.yaml`

Differs from `cover_kbc_v3_test.yaml` in exactly two top-level keys: `experiment`
and `pipeline` - and within `pipeline`, only by the added
`selection.v3_1` block. Asserted by
`test_safe_config_differs_from_v3_only_in_experiment_and_v3_1`. It reuses the
exact Audit 0073 M20/M21 calibration files and hashes, asserted by
`test_safe_config_reuses_the_audit_0073_calibration_hashes`.

Enables all five Class A features and no Class B feature.

Readiness: **`FULL_TEST_READY`**, `v3_1_calibration_status = SAFE_WITH_EXISTING_CALIBRATION`.

### `configs/experiments/cover_kbc_v3_1_aggressive_test.yaml`

Enables the five Class A features plus all four Class B features.

Readiness: **`NOT_READY`**, `v3_1_calibration_status = CALIBRATION_REVIEW_REQUIRED`,
with the blocker naming the offending features. It does not and cannot report
`FULL_TEST_READY`.

### Readiness gating

`readiness._check_v3_1_calibration_compatibility` was added to
`evaluate_test_readiness`. It refuses TEST on two independent grounds:

1. any Class B feature is enabled - the calibration no longer describes the run;
2. an enabled feature is undeclared in `v3_1.compatibility`, or declared as
   anything other than `SAFE_WITH_EXISTING_CALIBRATION`.

The frozen `cover_kbc_v3_test.yaml` is unaffected: it has no `v3_1` block, the
check records `v3_1: absent`, and it remains `FULL_TEST_READY`.

The existing zero-model precheck picks the gate up without modification:

```bash
python scripts/check_v3_test_readiness.py --config configs/experiments/<config>.yaml
```

| Config | Precheck verdict |
|---|---|
| `cover_kbc_v3_test.yaml` | `READY` |
| `cover_kbc_v3_1_safe_test.yaml` | `READY` |
| `cover_kbc_v3_1_aggressive_test.yaml` | `NOT_READY` + calibration-review blocker |

All three prechecks make `0` model calls and report the same unchanged V3
calibration artifact hashes.

## Output Analysis

`outputs/v3_1_score_recovery/` (git-ignored per repository policy - `.gitignore:34`
ignores `outputs/`):

- `safe_ablation.csv`
- `safe_per_row_changes.csv`
- `safe_relation_metrics.csv`
- `safe_counterfactual_predictions.jsonl`
- `numeric_rule_analysis.csv`
- `finalization_retention_analysis.csv`
- `stock_finalization_analysis.csv`
- `city_finalization_analysis.csv`
- `aggressive_change_inventory.json`
- `calibration_compatibility.json`
- `summary.json`
- `SHA256SUMS.txt`

## Tests

New file `tests/test_v3_1_score_recovery.py`: **128 tests**, covering every item
required by the milestone:

no gold dependency in production rules; no subject-specific answer mappings; no
network/filesystem access; final candidate retention; contradicted candidate not
retained; rejected candidate not retained; numeric parser (10 formats);
explicit area unit conversion (all five units, 13 spellings); unit conversion not
applied without explicit unit; numeric candidate deduplication; scientific
notation; stock output structural validation; ticker-shaped real exchange names
preserved; city semantic finalization; evidence-based singleton selection
(order-independence asserted directly); award normalized deduplication; border
non-regression; V3 baseline behaviour unchanged when V3.1 disabled (per-relation,
all six); `enabled: false` overriding a populated safe block; V3.1-safe not
altering M20/M21 action semantics; V3.1-aggressive readiness reporting
calibration review.

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `3724 passed, 4 skipped` (audit 0075 baseline was `3596 passed, 4 skipped`;
the difference is exactly the 128 new tests, so nothing regressed).

```bash
python -m pytest tests/ -q
```

Result: `3724 passed, 4 skipped`.

```bash
python -m pyflakes src/ tests/ scripts/
git diff --check
```

Result: both clean.

## Honest Limitations

1. `hasCapacity` (`0.080`) and `personHasCityOfDeath` (`0.390`) are **unchanged**.
   Both are dominated by L1 recall failure (`92` and `51` rows), which no
   finalization rule can reach. Their fixes are Class B and unvalidated.
2. `awardWonBy`'s `+0.02525` is structural repair of leaked enumeration lines. The
   underlying `418` FN / `266` FP recall problem is untouched.
3. `stock_support_dominance` harms 7 rows to help 12. It is net positive on TRAIN
   on both metrics, but it is the least robust safe feature.
4. TRAIN is not TEST. A `+0.01667` TRAIN macro-F1 delta measured on 477 rows is a
   diagnostic, not a promise. **No TEST improvement is claimed and none can be
   until a new TEST submission is actually scored.**
5. The Class B prompts have never been executed against a model. Their value is
   unmeasured.

## Verdict

**PASS - V3.1 SAFE SCORE-RECOVERY READY**

The Class A track is defensible: it is confined to Module 8 by construction, the
compatibility argument is checked by test rather than asserted, the replay
reproduces the frozen baseline exactly before changing anything, both official
metrics improve, no relation regresses, and `countryLandBordersCountry` is
byte-identical. `configs/experiments/cover_kbc_v3_1_safe_test.yaml` is
`FULL_TEST_READY` under the unchanged Audit 0073 calibration.

**CALIBRATION_REVIEW_REQUIRED - V3.1 AGGRESSIVE**

The Class B track is prototyped, versioned and hashed, but is not runnable as
production. `configs/experiments/cover_kbc_v3_1_aggressive_test.yaml` reports
`NOT_READY` and must not be used for a TEST submission until M20/M21 are
re-derived from a TRAIN collection run made with those prompts.

## Recommended Next GPU Runs

1. **V3.1 SAFE TEST submission** - the immediately actionable run. Same
   calibration, same models, same budget as the frozen V3 TEST run:

   ```bash
   python scripts/check_v3_test_readiness.py \
     --config configs/experiments/cover_kbc_v3_1_safe_test.yaml

   python scripts/run_cover.py \
     --config configs/experiments/cover_kbc_v3_1_safe_test.yaml \
     --no-eval
   ```

   The precheck already reports `READY` for this config and `NOT_READY` for the
   aggressive one. Score the submission before drawing any conclusion about TEST.

2. **V3.1 aggressive TRAIN collection**, to make Class B measurable at all:

   ```bash
   python scripts/run_cover.py \
     --config configs/experiments/cover_kbc_v3_1_aggressive_test.yaml \
     --split train
   ```

   then re-derive M20/M21 against that run (`scripts/derive_v3_calibration.py`)
   and re-run this milestone's replay before considering any aggressive TEST use.

3. **Capacity-only ablation** on TRAIN, enabling `capacity_definition_prompt`
   alone. It is the single largest theoretical recovery (`92` L1 rows,
   relation macro-F1 `0.080`) and the one most likely to move the total score.
