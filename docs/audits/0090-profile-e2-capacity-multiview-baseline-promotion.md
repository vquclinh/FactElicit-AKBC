# Audit 0090 - Profile E2 Capacity Multi-View Baseline Promotion

## Scope

This audit documents the implementation and promotion wiring for **Profile E2**:

Profile E2 = integrated Profile E1 + `MistralCapacityMultiView`.

Starting HEAD for this work:
`34810e72260d586181ca0053aaa729553257c1ba`.

No commit was made by the agent. No hidden TEST neural inference was run locally.
No web, RAG, external factual corpus, TEST gold, subject-answer lookup table,
Qwen runtime, CHIV, candidate harvesting, stochastic sampling, or retired C2
runtime was introduced.

## Baseline History

| profile | status | overall hidden TEST F1 | hasCapacity F1 |
|---|---|---:|---:|
| Profile D | previous frozen baseline | 0.4952 | 0.1224 |
| Integrated Profile E1 | previous frozen baseline | 0.5752 | 0.1224 |
| Direct Mistral Capacity probe | predecessor probe | 0.5794 | 0.1429 |
| CHIV | retired negative probe | 0.5773 | 0.1327 |
| Profile E2 Capacity Multi-View | current frozen baseline | 0.5836 | 0.1633 |

Profile E2 is promoted because the 4-view deterministic Multi-View Capacity
probe was the winning hidden-TEST Capacity method. CHIV is explicitly retired.

## Profile E2 Definition

Current official config:
`configs/experiments/cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml`.

Profile E2 keeps integrated Profile E1 unchanged for all relations except
`hasCapacity`. The active layers are:

1. Profile D core Mistral-only pipeline.
2. `AwardMetadataNormalizer`.
3. `MistralCityEmptyRescue`.
4. `MistralDirectArea`.
5. `MistralCapacityMultiView`.

`MistralCapacityMultiView` applies only when `Relation == "hasCapacity"`.
It ignores the upstream final Capacity answer, runs the four deterministic
Mistral views, and replaces `ObjectEntities` for that row using DIRECT_ALL
semantics. No old upstream Capacity value is used as a fifth vote or fallback.

## Model Portfolio

Active checkpoint:

- model id: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- unique neural parameters: `24,011,361,280`
- competition limit: `32,000,000,000`

Enumerator, verifier, City rescue, Direct Area, and Capacity Multi-View reuse
the same physical Mistral runtime. Qwen is not active.

## Capacity Prompt Contract

System prompt semantic content:

```text
You are a precise closed-book factual knowledge-base completion assistant.

Use only factual knowledge encoded in the model.

The target relation hasCapacity means the maximum spectator capacity of the
exact named venue, expressed as an integer number of people.

When multiple capacities are published, use the highest published spectator
capacity.

Never substitute attendance, area, construction cost, year, field dimensions,
or another venue.

If you are not sufficiently confident, return UNKNOWN.

Follow the output format exactly.
```

Four views are implemented in
`src/cover_kbc/leaderboard_repair/capacity.py`:

- `capacity_multiview_v1`: direct maximum spectator-capacity question.
- `capacity_multiview_v2`: identity / encyclopedic capacity recall.
- `capacity_multiview_v3`: configuration-aware capacity recall.
- `capacity_multiview_v4`: step-back exact-venue / wrong-attribute separation.

All four views require `CAPACITY: <integer>` or `UNKNOWN` and no explanation.

## Parser

Accepted:

- `CAPACITY: 40000`
- `CAPACITY: 40,000`
- `CAPACITY: 40000.0`
- `UNKNOWN`

Rejected:

- bare numbers
- approximate prose
- units
- ranges
- multiple candidates
- empty `CAPACITY:`
- `NaN`, `Infinity`, non-positive values
- multi-line explanatory output

Valid internal values are canonical positive integers. Final official output is
`["40000"]` or `[]`.

## Clustering And Judge

Numeric answers from V1/V2/V3/V4 are clustered with:

```text
abs(a - b) / max(abs(a), abs(b)) <= 0.05
```

UNKNOWN and invalid outputs do not enter clusters. The cluster representative is
an actual observed value nearest the cluster median; no arithmetic-only value is
invented.

Decision order:

1. Top cluster support `>= 3` -> accept representative; no judge call.
2. Otherwise, call one source-blind Mistral judge over unique numeric candidate
   values plus `UNKNOWN`.
3. If judge selects a numeric candidate -> accept it.
4. Otherwise, if V1 is valid -> use V1.
5. Otherwise, if any numeric cluster exists -> use the deterministic top-cluster
   representative.
6. Otherwise -> `[]`.

The judge prompt includes the subject, relation definition, and values labelled
`A`, `B`, ... plus `UNKNOWN`. It does not reveal V1/V2/V3/V4 provenance.

## Mutation Boundary

Only `hasCapacity` may change relative to integrated Profile E1.

Frozen non-Capacity behavior:

- `awardWonBy`: unchanged.
- `companyTradesAtStockExchange`: unchanged.
- `countryLandBordersCountry`: unchanged.
- `hasArea`: Direct Area remains unchanged.
- `personHasCityOfDeath`: E1 City rescue remains unchanged.

## Call Accounting

For every eligible `hasCapacity` row:

- one `capacity_multiview_v1` call
- one `capacity_multiview_v2` call
- one `capacity_multiview_v3` call
- one `capacity_multiview_v4` call
- optional one `capacity_multiview_judge` call on ambiguous rows

For official TEST:

- eligible rows: `98`
- base Multi-View calls: `98 * 4 = 392`
- max calls per hasCapacity row: `5`
- judge calls: adaptive and recorded exactly

Repair accounting records per-view valid/UNKNOWN/invalid counts, strong
consensus rows, judge-selected rows, fallback rows, empty rows, error rows, and
changed rows.

## Targeted Execution And Merge

Targeted runner:
`scripts/run_capacity_multiview.py`.

It selects only `hasCapacity` rows from VAL or TEST, validates the one-Mistral
model portfolio, runs `MistralCapacityMultiView`, and emits:

- `capacity_multiview_results.jsonl`
- `capacity_multiview_records.jsonl`
- `calls.jsonl`
- `capacity_multiview_accounting.json`

Generic merge utility:
`scripts/merge_targeted_relation_results.py`.

For a Capacity merge it validates:

- baseline rows = `475`
- targeted rows = `98`
- all targeted rows have `Relation == "hasCapacity"`
- exact key coverage of the baseline Capacity rows
- no duplicate keys
- no missing or extra keys
- row order preserved
- all `377` non-Capacity rows copied verbatim
- final rows = `475`
- changed relation set is a subset of `{hasCapacity}`

## Winning Artifact Status

Local artifacts checked:

- `outputs/submission-best/predictions.jsonl`
- `outputs/predictions-best.jsonl`

Both local files are still the integrated Profile E1 artifact:
`67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6`,
475 rows.

The exact Profile E2 hidden-winning 0.5836 artifact is not present locally in
the repository outputs. Config metadata records:
`WINNING_ARTIFACT_SHA_PENDING_USER_IMPORT`.

The source promotion does not synthesize or replace the winning artifact.

## Semantic Diff

Resolved prediction-affecting diff from integrated Profile E1 to Profile E2:

```json
{
  "leaderboard_repair_capacity_multiview_mode": ["OFF", "DIRECT_ALL"],
  "leaderboard_repair_caps": {
    "hasCapacity": [0, 5]
  },
  "leaderboard_repair_features": {
    "mistral_capacity_multiview": [false, true]
  }
}
```

Unchanged:

- model profile
- parameter accounting
- TEST dataset identity
- core pipeline
- calibration provenance
- Direct Area mode
- City rescue flags and caps
- Award cleanup
- Stock, Border, Area, City, Award behavior flags
- output schema

## Files Changed

Implementation:

- `src/cover_kbc/leaderboard_repair/capacity.py`
- `src/cover_kbc/leaderboard_repair/config.py`
- `src/cover_kbc/leaderboard_repair/relations.py`
- `src/cover_kbc/leaderboard_repair/stack.py`
- `scripts/run_capacity_multiview.py`
- `scripts/merge_targeted_relation_results.py`

Configuration and docs:

- `configs/experiments/cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml`
- `configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml`
- `configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`
- `README.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/audits/0090-profile-e2-capacity-multiview-baseline-promotion.md`
- `scripts/audit_model_budget.py`
- `scripts/real_model_smoke.py`

Tests:

- `tests/test_profile_e2_capacity_multiview.py`
- `tests/test_leaderboard_repair.py`
- `tests/test_profile_e1_direct_area_baseline.py`
- `tests/test_profile_d_mistral_role_swap.py`

## Validation

Validation commands:

```text
python -m pytest tests/test_profile_e2_capacity_multiview.py -q
13 passed
```

```text
python -m pytest tests/test_leaderboard_repair.py tests/test_profile_e1_direct_area_baseline.py tests/test_profile_d_mistral_role_swap.py -q
29 passed
```

```text
python -m pytest tests/test_profile_e2_capacity_multiview.py tests/test_leaderboard_repair.py tests/test_profile_e1_direct_area_baseline.py tests/test_profile_d_mistral_role_swap.py -q
42 passed
```

```text
python -m pytest tests/test_profile_e2_capacity_multiview.py tests/test_leaderboard_repair.py tests/test_profile_e1_direct_area_baseline.py tests/test_profile_d_mistral_role_swap.py tests/test_production_source_fixes.py tests/test_targeted_diagnostic_executability.py -q
149 passed
```

```text
python -m pyflakes src/ tests/ scripts/
PASS
```

```text
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml
RESULT: PASS
total: 24.01B
```

```text
python -m pytest tests/ -q
4095 passed, 21 skipped
```

```text
python -m pytest tests/ -q -p no:randomly
4095 passed, 21 skipped
```

```text
git diff --check
PASS
```

## Readiness Caveat

Profile E2 is hidden-TEST scored and promoted as the current frozen baseline.
No new TRAIN calibration is invented here. Existing readiness behavior should
continue to report the explicit leaderboard-probe calibration caveat rather
than pretending this is a newly TRAIN-calibrated profile.

## Conclusion

Profile E2 promotes only the hidden-TEST-winning 4-view deterministic
Capacity Multi-View layer on top of integrated Profile E1. It preserves all
non-Capacity behavior, keeps one Mistral checkpoint as the only active neural
model, retires CHIV, and leaves the exact winning artifact pending user import
because it is not locally present.
