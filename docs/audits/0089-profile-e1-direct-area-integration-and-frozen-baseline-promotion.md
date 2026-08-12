# Audit 0089 - Profile E1 Direct Area integration and frozen baseline promotion

## Scope

This audit records the promotion of the hidden-TEST-winning integrated Profile
E1 baseline. It is not a new modeling experiment and does not implement Profile
E2.

The new official baseline is:

```
Profile D core
+ AwardMetadataNormalizer
+ MistralCityEmptyRescue
+ MistralDirectArea
```

No web, RAG, external factual corpus, TEST gold, subject-answer table, new
checkpoint, fine-tuning, or retired C2 runtime path is used.

## Baseline lineage

| system | status | hidden TEST overall F1 |
|---|---|---:|
| A+Award | historical superseded baseline | 0.4910 |
| Profile D | previous frozen baseline | 0.4952 |
| v3.4 Profile E1 City-only | historical City rescue provenance | not the current baseline |
| Integrated Profile E1 | current frozen baseline | 0.5752 |

Historical v3.4 E1 remains available at:

`configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`

Current integrated E1 is configured at:

`configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml`

Profile E2 is reserved for future work and is not implemented by this audit.

## Hidden Leaderboard Evidence

| relation | precision | recall | F1 |
|---|---:|---:|---:|
| awardWonBy | 0.3255 | 0.3707 | 0.3105 |
| companyTradesAtStockExchange | 0.9092 | 0.7863 | 0.7285 |
| countryLandBordersCountry | 0.9712 | 0.9295 | 0.9291 |
| hasArea | 0.6700 | 0.6600 | 0.6600 |
| hasCapacity | 0.3776 | 0.1224 | 0.1224 |
| personHasCityOfDeath | 0.9600 | 0.5900 | 0.5700 |
| **All Relations** | **0.7563** | **0.5929** | **0.5752** |

Zero-object reference:

| precision | recall | F1 |
|---:|---:|---:|
| 0.5363 | 0.9412 | 0.6833 |

Deltas versus Profile D:

| relation | Profile D F1 | integrated E1 F1 | delta |
|---|---:|---:|---:|
| hasArea | 0.3600 | 0.6600 | +0.3000 |
| personHasCityOfDeath | 0.4900 | 0.5700 | +0.0800 |
| awardWonBy | 0.3105 | 0.3105 | 0.0000 |
| companyTradesAtStockExchange | 0.7285 | 0.7285 | 0.0000 |
| countryLandBordersCountry | 0.9291 | 0.9291 | 0.0000 |
| hasCapacity | 0.1224 | 0.1224 | 0.0000 |
| **All Relations** | **0.4952** | **0.5752** | **+0.0800** |

## Direct Area Contract

Direct Area applies only to `hasArea`. It runs for every `hasArea` row, not only
empty rows.

Runtime flow:

1. Run the normal integrated Profile E1 pipeline.
2. For each final `hasArea` row, issue one Mistral Direct Area generation.
3. Strictly parse the output.
4. Replace the `hasArea` `ObjectEntities` with the Direct Area result.
5. Leave all 375 non-Area rows byte-for-byte unchanged in targeted merge mode.

DIRECT_ALL finalization:

| Direct Area output | final `ObjectEntities` |
|---|---|
| `AREA: X` | `["X"]` |
| `UNKNOWN` | `[]` |
| invalid output | `[]` |

There is no fallback to the old numeric answer when Direct Area returns
`UNKNOWN` or invalid output.

## Direct Area Prompt

System prompt:

```text
You are a precise factual knowledge-base completion assistant.

Use only factual knowledge encoded in the model.
Follow the requested output format exactly.
If you are not sufficiently confident, return UNKNOWN.
Do not explain your answer.
```

User prompt:

```text
Subject: {subject}

Relation: hasArea

Question:
What is the canonical surface area of this exact named geographic entity in
square kilometres?

Identify the exact entity named by the subject before answering.

For an island:
return the land area of that exact island itself.

For a lake:
return the surface area of that exact lake.

For a country:
return its total area, including land and inland water.

Do NOT return the area of:
- a containing country
- a state, province, county, municipality, or administrative region
- an archipelago or island group unless the subject itself is that group
- only one part of the named island
- a drainage basin or catchment
- a lagoon unless the subject itself is the lagoon
- a protected area
- a nearby geographic feature

If you know the area in square miles or hectares, convert it to square
kilometres before answering.

Return exactly one of:

AREA: <number>

or:

UNKNOWN

<number> must be a single positive decimal number in km^2.

Do not include units after the number.
Do not return a range.
Do not give multiple candidate values.
Do not explain your answer.
```

Decoding:

| field | value |
|---|---|
| deterministic | yes |
| `do_sample` | false |
| `temperature` | 0.0 by `DecodeProfile` |
| `top_p` | 1.0 |
| `max_new_tokens` | 24 |
| calls per `hasArea` row | 1 maximum |

Strict parser:

- accepts exactly one non-empty line;
- accepts `AREA: <positive finite decimal>`;
- accepts `UNKNOWN`;
- rejects units, ranges, alternatives, bare numbers, NaN, Infinity, negative
  values, zero, multiple lines, and arbitrary prose.

## Model Portfolio

The active neural portfolio contains one unique checkpoint:

| role | model | revision | parameters |
|---|---|---|---:|
| enumerator/verifier/City rescue/Direct Area | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |

Unique neural parameter total:

`24,011,361,280 / 32,000,000,000`

Qwen is absent from the active model portfolio. The same physical Mistral
runtime is reused for logical enumerator, verifier, City rescue and Direct Area
roles.

## Mutation Boundary

Direct Area may change only:

- `hasArea`

It must not mutate:

- `awardWonBy`
- `companyTradesAtStockExchange`
- `countryLandBordersCountry`
- `hasCapacity`
- `personHasCityOfDeath`

City behavior remains the v3.4 E1 City rescue:

- non-empty City rows bypass rescue;
- empty rows receive at most one life-status call;
- only exact `DECEASED` triggers one city call;
- exact `CITY: X` produces a singleton;
- `LIVING`, `UNKNOWN`, invalid life output, invalid city output and city
  `UNKNOWN` keep `[]`.

## Targeted Area Execution And Merge

The targeted reproduction path is:

```bash
python scripts/run_direct_area.py \
  --config configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml \
  --split test \
  --output-dir outputs/direct_area_test

python scripts/merge_direct_area_results.py \
  --baseline-predictions outputs/submission-E1/predictions.jsonl \
  --area-results outputs/direct_area_test/direct_area_results.jsonl \
  --output outputs/integrated-e1/predictions.jsonl
```

Merge invariants:

1. baseline has exactly 475 rows;
2. Area result artifact has exactly 100 rows;
3. Area result keys exactly match the baseline `hasArea` keys;
4. no duplicate keys;
5. no missing or extra Area key;
6. every Area result relation is `hasArea`;
7. final output has exactly 475 rows;
8. row order is preserved;
9. all 375 non-Area rows are copied unchanged;
10. changed relation set is a subset of `{hasArea}`.

The normal full pipeline can also run the integrated baseline from scratch
through `scripts/run_cover.py`.

## Artifact Provenance

Local artifacts are gitignored under `outputs/`.

Verified local canonical integrated artifact:

| field | value |
|---|---|
| path | `outputs/submission-best/predictions.jsonl` |
| SHA256 | `67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6` |
| rows | 475 |

The same SHA was observed for the source local artifact
`outputs/predictions-best.jsonl`. It differs from the local City-only E1
artifact `outputs/submission-E1/predictions.jsonl` in 80 rows, all relation
`hasArea`; no non-Area row changed.

Relation counts and shape for `outputs/submission-best/predictions.jsonl`:

| relation | rows | empty | objects | max |
|---|---:|---:|---:|---:|
| awardWonBy | 10 | 0 | 513 | 102 |
| companyTradesAtStockExchange | 100 | 57 | 93 | 29 |
| countryLandBordersCountry | 67 | 11 | 260 | 14 |
| hasArea | 100 | 1 | 99 | 1 |
| hasCapacity | 98 | 25 | 73 | 1 |
| personHasCityOfDeath | 100 | 85 | 15 | 1 |

The task did not provide a separate expected hidden-submission checksum. The
repository records the locally verified canonical artifact SHA above and does
not fabricate any additional external submission hash.

## Semantic Config Diff

Prediction-affecting diff from historical City-only E1 to integrated E1:

```json
{
  "leaderboard_repair_caps": {
    "hasArea": [0, 1]
  },
  "leaderboard_repair_direct_area_mode": ["OFF", "DIRECT_ALL"],
  "leaderboard_repair_features": {
    "mistral_direct_area": [false, true]
  }
}
```

Everything related to City, Award, Stock, Borders, Capacity, core generation,
verification, thresholds, calibration provenance, dataset, model identity,
parameter accounting and output schema remains unchanged.

## Files Changed

- `configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`
- `configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`
- `configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml`
- `docs/IMPLEMENTATION_STATUS.md`
- `README.md`
- `scripts/audit_model_budget.py`
- `scripts/real_model_smoke.py`
- `scripts/run_direct_area.py`
- `scripts/merge_direct_area_results.py`
- `src/cover_kbc/leaderboard_repair/area.py`
- `src/cover_kbc/leaderboard_repair/config.py`
- `src/cover_kbc/leaderboard_repair/relations.py`
- `src/cover_kbc/leaderboard_repair/stack.py`
- `tests/test_baseline_restoration.py`
- `tests/test_leaderboard_repair.py`
- `tests/test_profile_d_mistral_role_swap.py`
- `tests/test_profile_e1_mistral_city_rescue.py`
- `tests/test_profile_e1_direct_area_baseline.py`
- `docs/audits/0089-profile-e1-direct-area-integration-and-frozen-baseline-promotion.md`

The stale uncommitted Profile E2 files from the prior attempt were not kept.

## Readiness Caveat

Integrated E1 is hidden-TEST scored and frozen, but the repository does not
claim newly derived TRAIN calibration. The readiness checker may continue to
report `CALIBRATION_REVIEW_REQUIRED`; the explicit leaderboard-probe path
documents and bounds that caveat without weakening unrelated integrity checks.

## Validation

| command/check | result |
|---|---|
| `python -m pytest tests/test_profile_e1_direct_area_baseline.py tests/test_profile_e1_mistral_city_rescue.py tests/test_profile_d_mistral_role_swap.py tests/test_leaderboard_repair.py tests/test_runtime_preflight.py tests/test_official_test_activation.py tests/test_v3_test_activation.py -q -p no:randomly` | `136 passed in 5.35s` |
| old E1 -> integrated E1 semantic diff | only `mistral_direct_area: false -> true`, `hasArea` cap `0 -> 1`, and `direct_area_mode: OFF -> DIRECT_ALL` |
| `sha256sum outputs/submission-best/predictions.jsonl` | `67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6` |
| `wc -l outputs/submission-best/predictions.jsonl` | `475` |
| relation count check on `outputs/submission-best/predictions.jsonl` | `hasArea=100`, `hasCapacity=98`, `awardWonBy=10`, `companyTradesAtStockExchange=100`, `countryLandBordersCountry=67`, `personHasCityOfDeath=100` |
| local City-only E1 artifact vs local integrated artifact | 80 changed rows, all `hasArea`; 0 non-Area changes |
| `python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml` | PASS, total `24.01B`, unique Mistral only |
| `python scripts/check_v3_test_readiness.py --config configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml --json` | exit `2` as expected; `state=NOT_READY`; `test_rows=475`; `test_rows_with_objects=0`; blockers limited to verifier role swap, parameter total shift, and `selection.v3_1: CALIBRATION_REVIEW_REQUIRED` |
| `python scripts/run_direct_area.py --help` | PASS, no model load |
| `python scripts/merge_direct_area_results.py --help` | PASS |
| `python scripts/merge_direct_area_results.py` synthetic 475-row smoke | PASS, merged rows `475`, changed rows `100`, changed relation `['hasArea']` |
| malformed synthetic 465-row merge smoke | fail-closed as expected: `baseline has 465 row(s); expected 475` |
| `git diff --check` | PASS |
| `python -m pyflakes src/ tests/ scripts/` | PASS |
| `python -m pytest tests/ -q -p no:randomly` | `4082 passed, 21 skipped in 78.70s` |
| `python -m pytest tests/ -q` | `4082 passed, 21 skipped in 76.04s` |

No hidden TEST neural inference was run during this promotion task.
