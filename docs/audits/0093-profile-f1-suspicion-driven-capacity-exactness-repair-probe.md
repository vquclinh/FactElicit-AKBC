# Audit 0093 - Profile F1 Suspicion-Driven Capacity Exactness Repair Probe

Date: 2026-08-13

## Starting State

Starting HEAD:

`991bca5640723a24d84c1d41a174e8d1d3dceed5`

Starting `git status --short`:

```text
```

No commit or push was made. No hidden TEST neural inference was run locally.

## Frozen Baseline

The repository was inspected before edits. Current promoted baseline is:

Profile E3 =

Profile E2 + `MistralAreaMultiView`

Config:

`configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`

User-provided hidden TEST evidence recorded in E3:

| relation | F1 |
|---|---:|
| awardWonBy | 0.3105 |
| companyTradesAtStockExchange | 0.7285 |
| countryLandBordersCountry | 0.9291 |
| hasArea | 0.6700 |
| hasCapacity | 0.1633 |
| personHasCityOfDeath | 0.5700 |
| All Relations | 0.5857 |

Profile E3 remains the frozen official baseline. Profile F1 is an unscored
leaderboard probe.

## Causal Question

Profile F1 asks one question:

Can the current E3 hidden-tested `hasCapacity` F1 of 0.1633 be improved by
detecting suspicious Capacity predictions and spending four additional
candidate-blind Mistral exactness-recall calls only on those rows?

Default behavior is always:

```text
final = E3 prediction
```

The exactness repair layer must pass a conservative acceptance gate before it
may overwrite E3.

## Implemented Profile

New config:

`configs/experiments/cover_kbc_v3_8_profile_f1_capacity_exactness_repair_test.yaml`

Profile F1 =

Profile E3
+ `CapacitySuspicionScorer`
+ `MistralCapacityExactnessRepair`

Only `hasCapacity` may differ from E3. The F1 config explicitly records:

- `profile_f1_probe.status: UNSCORED_LEADERBOARD_PROBE`
- `hidden_test_scores_source: FROZEN_PROFILE_E3_BASELINE_NOT_F1`
- `chiv_active: false`
- `nsmv_active: false`
- `qwen_active: false`
- stochastic sampling disabled
- no web, RAG, external corpus, external KB, or subject-specific table

Future repair ideas were not implemented or imported.

## Suspicion Rules

Implemented in:

`src/cover_kbc/leaderboard_repair/capacity_exactness.py`

The `CapacitySuspicionScorer` is deterministic and uses zero neural calls. It
scores only current-batch predictions and existing E3 Capacity Multi-View
diagnostics.

Flags:

- `CAPACITY_EMPTY`
- `CAPACITY_ORIGINAL_JUDGE`
- `CAPACITY_FALLBACK_V1`
- `CAPACITY_FALLBACK_CLUSTER`
- `CAPACITY_ROUND_1K`
- `CAPACITY_ROUND_5K`
- `CAPACITY_ROUND_10K`
- `CAPACITY_REPEATED_VALUE_PRIOR`
- `CAPACITY_HOMOGENEOUS_ROUND_CONSENSUS`

Initial F1 trigger:

```text
suspicious if any of:
- E3 result is empty
- original E3 Capacity Multi-View final reason used judge/fallback
- predicted value repeats across at least 3 distinct subjects in the batch
- at least 3 original views returned the same value divisible by 1000
- at least 2 round-number flags are present
```

Repeated-value suspicion uses only predictions from the current input batch.
No gold values, external data, or subject-specific constants are consulted.

## Exactness Prompts

All F1 exactness calls use:

- model: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- greedy decoding
- `temperature=0.0`
- `top_p=1.0`
- no stochastic retry
- no candidate-fed yes/no verifier

All four views are candidate-blind and return only:

```text
CAPACITY: <integer>
CONFIG: <label>
EXACTNESS: <label>
```

or:

```text
UNKNOWN
```

Allowed CONFIG labels:

`SEATED`, `TOTAL`, `STANDING`, `SPORT`, `CONCERT`, `HISTORICAL`,
`TEMPORARY`, `UNKNOWN`

Allowed EXACTNESS labels:

`EXACT`, `APPROXIMATE`, `ESTIMATE`, `UNKNOWN`

Prompt bodies:

- `capacity_exactness_x1`: recall the most specific encyclopedic/reference
  spectator-capacity fact for the exact venue; do not round uncertainty; return
  the highest legitimate published spectator capacity across configurations.
- `capacity_exactness_x2`: internally disambiguate the exact venue using the
  full subject string; recall whether a distinctive non-generic capacity number
  is associated with it; do not substitute a generic round capacity.
- `capacity_exactness_x3`: retrieve possible published capacity configurations
  for the exact venue, including seated, total, standing, sport, concert,
  historical, post-renovation, and temporary; do not confuse capacity with
  record attendance.
- `capacity_exactness_x4`: reason silently about whether the first number is an
  exact venue-specific fact or a generic rounded prior; only mark EXACT with
  specific factual memory.

The exact string renderer is `capacity_exactness_view_prompt`.

## Parser

The strict parser accepts exactly:

```text
CAPACITY: 42300
CONFIG: SEATED
EXACTNESS: EXACT
```

It also accepts grouped commas such as:

```text
CAPACITY: 42,300
CONFIG: TOTAL
EXACTNESS: EXACT
```

`UNKNOWN` parses as no candidate.

Rejected forms include prose, ranges, multiple numbers, bad labels, zero,
negative values, `NaN`, `Infinity`, and bare numeric responses.

Each valid recall is represented as:

```json
{"value": 42300, "config": "SEATED", "exactness": "EXACT", "view": "capacity_exactness_x1"}
```

## Clustering

Valid repair values cluster under:

```text
abs(a - b) / max(abs(a), abs(b)) <= 0.05
```

The representative is an observed candidate nearest the cluster median. F1 does
not synthesize arithmetic-only values.

Each cluster records:

- values
- views
- support
- representative
- exact_count
- approximate_count
- estimate_count
- known_config_count
- roundness metadata

Roundness metadata records divisibility by 1000, 5000, and 10000 plus
`non_round_distinctive`. Roundness is suspicion/evidence metadata only; it is
not a claim that a number is wrong.

## Acceptance Gate

Reason codes:

- `CAPACITY_REPAIR_EMPTY_STRONG`
- `CAPACITY_REPAIR_CORROBORATES_E3`
- `CAPACITY_REPAIR_STRONG_OVERRIDE`
- `CAPACITY_REPAIR_REJECTED`
- `CAPACITY_REPAIR_NOT_SUSPICIOUS`

If E3 is empty, F1 fills only when the top repair cluster has:

```text
support >= 3
exact_count >= 2
known_config_count >= 2
```

If E3 is non-empty and the top repair cluster is within 5 percent of E3, F1
keeps E3 as corroborated.

If E3 is non-empty and the top repair cluster differs by more than 5 percent,
F1 overrides only when:

```text
support >= 3
exact_count >= 2
known_config_count >= 2
and (
  repair representative is non-round/distinctive
  or support == 4 and exact_count >= 3
)
```

Self-reported exactness alone is never sufficient.

## Mutation Boundary

Prediction-affecting E3 -> F1 diff:

- feature delta: `mistral_capacity_exactness_repair: false -> true`
- cap delta: `hasCapacity: 5 -> 9`
- nested F1 exactness config added under `leaderboard_repair.capacity_exactness_repair`

Unchanged from E3:

- Area Multi-View
- City rescue
- Award metadata normalizer
- Stock behavior
- Border behavior
- original Capacity Multi-View
- pipeline settings
- shadow diagnostic sections
- calibration provenance
- test dataset identity
- model profile
- budget assertion

## Model And Calls

One physical model:

- model id: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- unique published parameters: 24,011,361,280
- limit: 32,000,000,000

No second Mistral copy is configured. The model resolver returns the same block
for enumerator and verifier. Qwen is not present in the F1 model profile.

For each Capacity row:

- SuspicionScorer: 0 neural calls
- not suspicious: 0 F1 repair calls
- suspicious: exactly 4 additional Mistral calls

The targeted runner records:

- `total_capacity_rows`
- `suspicious_rows`
- `untouched_rows`
- `overridden_rows`
- `corroborated_rows`
- `rejected_repair_rows`
- `empty_rescued_rows`
- `capacity_exactness_x1` through `capacity_exactness_x4` call counts

## Targeted Execution

Runner:

`scripts/run_capacity_exactness_repair.py`

Dry-run validation command:

```bash
python scripts/run_capacity_exactness_repair.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_capacity_exactness_repair_test.yaml \
  --split test \
  --output-dir outputs/profile_f1_capacity_exactness \
  --dry-run
```

Real Colab command:

```bash
python scripts/run_capacity_exactness_repair.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_capacity_exactness_repair_test.yaml \
  --split test \
  --output-dir outputs/profile_f1_capacity_exactness
```

The real command runs only the 98 official `hasCapacity` rows. It first
reproduces E3 Capacity Multi-View outputs and diagnostics, then runs F1
exactness repair only on suspicious rows.

Outputs:

- `capacity_exactness_repair_results.jsonl`
- `capacity_exactness_repair_diagnostics.jsonl`
- `capacity_exactness_repair_records.jsonl`
- `calls.jsonl`
- `capacity_exactness_repair_accounting.json`

The result artifact contains exactly 98 official rows:

```json
{"SubjectEntity": "...", "Relation": "hasCapacity", "ObjectEntities": ["12345"]}
```

or an empty `ObjectEntities` list.

## Merge Path

Use the existing fail-closed relation merge utility:

```bash
python scripts/merge_targeted_relation_results.py \
  --baseline-predictions PATH_TO_PROFILE_E3_475_ROW_PREDICTIONS.jsonl \
  --targeted-results outputs/profile_f1_capacity_exactness/capacity_exactness_repair_results.jsonl \
  --relation hasCapacity \
  --expected-targeted-rows 98 \
  --output outputs/profile_f1_capacity_exactness/profile_f1_merged_475.jsonl
```

Merge invariants:

- baseline rows = 475
- targeted Capacity rows = 98
- non-Capacity rows = 377
- exact Capacity key set
- no duplicates
- no missing or extra keys
- preserve baseline row order
- all 377 non-Capacity rows unchanged
- only `hasCapacity` may differ

## Tests

Added:

`tests/test_profile_f1_capacity_exactness_repair.py`

Coverage includes:

- suspicion scorer flags and zero-call behavior
- non-Capacity rows excluded from the scorer
- strict parser accepts only the F1 contract
- 5 percent clustering and observed representative
- roundness metadata
- all required acceptance cases
- exact four-call suspicious-row runtime behavior
- non-suspicious zero F1 repair calls
- E3 -> F1 semantic diff is Capacity-exactness-only
- one Mistral checkpoint, no Qwen, CHIV inactive, NSMV inactive
- targeted runner dry run
- diagnostics sidecar schema
- Capacity merge preserves 377 non-Capacity rows

## Artifact Status

Profile E3 winning 475-row artifact is still not available locally. F1 targeted
runner and merge path are prepared for the user's Colab execution and manual
submission packaging.

Profile F1 remains UNSCORED until the user provides hidden TEST evidence. E3
remains the frozen 0.5857 baseline.
