# Audit 0087 - Profile E1 Mistral City empty rescue

## 1. Scope

This audit records implementation and CPU/static validation for one controlled
experiment only: Profile E1, a conservative Mistral-native two-stage rescue for
currently-empty `personHasCityOfDeath` rows.

No TRAIN, VAL, or full TEST neural inference was run. No web, RAG, external
factual corpus, TEST gold, fine-tuning, third runtime model, hand-edited
predictions, subject-answer lookup table, C2 repair, L10, numeric resolver,
Stock repair, Border repair, Award recall, or Profile E2 logic was introduced.

The user commits. This task does not commit or push.

## 2. Frozen Profile D Baseline

Frozen baseline config:
`configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`

Hidden TEST baseline:

| relation | precision | recall | F1 |
| --- | ---: | ---: | ---: |
| awardWonBy | 0.3255 | 0.3707 | 0.3105 |
| companyTradesAtStockExchange | 0.9092 | 0.7863 | 0.7285 |
| countryLandBordersCountry | 0.9712 | 0.9295 | 0.9291 |
| hasArea | 0.5100 | 0.3600 | 0.3600 |
| hasCapacity | 0.3776 | 0.1224 | 0.1224 |
| personHasCityOfDeath | 0.9900 | 0.4900 | 0.4900 |
| **All Relations** | **0.7289** | **0.5087** | **0.4952** |

Profile D submitted prediction SHA256:
`7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c`

Profile D City shape:

- rows: `100`
- empty: `98`
- non-empty: `2`
- objects: `2`
- max cardinality: `1`

## 3. Profile E1 Causal Question

Profile E1 asks exactly:

> What happens if frozen Profile D is augmented with a conservative
> Mistral-native two-stage factual rescue only for currently-empty
> `personHasCityOfDeath` rows?

The standalone Mistral observations that motivated this experiment are not
runtime knowledge. No observed subject-to-city pair is encoded in source,
config, tests, or predictions.

## 4. New Config

New config:
`configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`

It derives from Profile D and changes only:

- `leaderboard_repair.features.mistral_city_empty_rescue: false -> true`
- `leaderboard_repair.max_calls_by_relation.personHasCityOfDeath: 0 -> 2`
- experiment/profile metadata

Profile D is not modified in place by E1.

## 5. Runtime Flow

For every row:

1. Run frozen Profile D normally.
2. Enter the post-pipeline leaderboard-repair stack.
3. If relation is not `personHasCityOfDeath`, keep Profile D behavior.
4. If relation is `personHasCityOfDeath` and Profile D already emitted one or
   more objects, keep the objects exactly and spend zero E1 calls.
5. If relation is `personHasCityOfDeath` and Profile D emitted `[]`, run the
   E1 life-status call.
6. Only exact `DECEASED` from the life parser allows the City recall call.
7. Only exact `CITY: <city name>` from the City parser mutates the output to
   a singleton city.
8. `LIVING`, `UNKNOWN`, invalid life output, `UNKNOWN` city output, invalid city
   output, or exhausted budget all preserve `[]`.

## 6. Exact Prompts

Life status prompt:

```text
Subject: {subject}

Question:
Is this exact person deceased?

Return exactly one of:
DECEASED
LIVING
UNKNOWN

Use UNKNOWN if you are not sufficiently confident.

Do not explain your answer.
```

City prompt:

```text
Subject: {subject}

Relation: personHasCityOfDeath

The person has already been classified as DECEASED.

Question:
In which city did this exact person die?

Return exactly one of:

CITY: <city name>

or:

UNKNOWN

The answer must be the city/locality of death.

Do not return:
- hospital or institution name
- country
- state or province
- birthplace
- main residence
- burial place

If you cannot confidently identify the city of death, return UNKNOWN.

Do not explain your answer.
```

## 7. Decoding And Parser

Decoding:

- deterministic generation
- `temperature = 0.0`
- `top_p = 1.0`
- life status `max_new_tokens = 8`
- city recall `max_new_tokens = 20`

Life parser:

- trims blank lines
- accepts exactly one non-empty first-line value after uppercasing:
  `DECEASED`, `LIVING`, or `UNKNOWN`
- any extra text, explanation, or alternate label is `INVALID`

City parser:

- trims blank lines
- accepts exactly one non-empty line
- accepts `UNKNOWN`
- accepts `CITY: <non-empty city string>`
- rejects arbitrary prose, extra lines, empty city payloads, and packed
  `;` / `|` multi-values
- preserves Unicode city strings

No factual aliases or city knowledge are encoded.

## 8. Mutation Boundary

Profile E1 must not mutate:

- `awardWonBy`, except existing Profile D `AwardMetadataNormalizer` behavior
- `companyTradesAtStockExchange`
- `countryLandBordersCountry`
- `hasArea`
- `hasCapacity`

For `personHasCityOfDeath`, E1 mutates only rows whose Profile D final output is
exactly `[]`. Existing non-empty City rows are bypassed and receive zero E1
calls.

Retired C2 features remain disabled:

- `BorderDirectionalSweep`
- border alias/reciprocity repair
- `AreaEmptyRescue`
- broad `NumericAttributeResolver`
- `CapacityRepair`
- C2 `DeathExistenceGate` and `DeathCityRecall`
- `AwardRecipientWitness`
- `AwardTimeSlicedRecall`
- aggressive L8/L9 behavior

## 9. Runtime Reuse And Model Portfolio

E1 keeps the Profile D one-checkpoint portfolio:

| role | model | revision | parameter accounting |
| --- | --- | --- | ---: |
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier/E1 calls | same physical/configured Mistral checkpoint | same revision | counted once |

Unique neural parameter total: `24,011,361,280`

Competition limit: `32,000,000,000`

No Qwen runtime is active. `scripts/run_cover.py` reuses one physical runtime
when the resolved enumerator and verifier model blocks are equal.

## 10. Call Accounting

E1 writes ordinary leaderboard-repair call records with:

- `layer = E1`
- `feature = MistralCityEmptyRescue`
- `model_role = verifier`
- `model_id = mistralai/Mistral-Small-3.2-24B-Instruct-2506`

Per City relation accounting includes:

- eligible empty rows
- bypassed non-empty rows
- life-status calls
- DECEASED count
- LIVING count
- UNKNOWN count
- invalid life outputs
- city calls
- CITY accepted count
- city UNKNOWN count
- invalid city outputs
- changed rows

Per-row cap behavior:

| Profile D state | E1 calls |
| --- | ---: |
| City non-empty | 0 |
| City empty + `LIVING` | 1 |
| City empty + `UNKNOWN` | 1 |
| City empty + invalid life output | 1 |
| City empty + `DECEASED` + city/unknown/invalid City output | 2 |

## 11. Semantic Config Diff

Machine-readable Profile D -> E1 prediction-affecting diff:

```json
{
  "leaderboard_repair_caps": {
    "personHasCityOfDeath": [
      0,
      2
    ]
  },
  "leaderboard_repair_features": {
    "mistral_city_empty_rescue": [
      false,
      true
    ]
  },
  "profile_metadata": [
    "cover_kbc_v3_3_profile_d_mistral_only_role_swap_test",
    "cover_kbc_v3_4_profile_e1_mistral_city_rescue_test"
  ],
  "repair_profile_metadata": [
    "D_MISTRAL_ONLY_ROLE_SWAP",
    "E1_MISTRAL_CITY_RESCUE"
  ]
}
```

No model, prompt, threshold, relation-budget, M20/M21, TEST dataset, non-City
repair, Stock background, or output-schema setting changes.

## 12. Readiness Caveat

E1 is not newly TRAIN-calibrated. It remains a controlled hidden-TEST
leaderboard probe layered on the frozen Profile D baseline.

Zero-model readiness result:

- state: `NOT_READY`
- TEST rows: `475`
- TEST rows with ObjectEntities: `0`
- collection disabled: `true`
- model calls: `0`
- accepted through explicit leaderboard-probe path

Readiness blockers are the same intentional Profile D calibration/model-role
swap blockers:

- verifier model id differs from the old Qwen calibration reference
- verifier revision differs from the old Qwen calibration reference
- unique model budget total differs from the old Mistral+Qwen portfolio
- `selection.v3_1: CALIBRATION_REVIEW_REQUIRED`

No unrelated integrity blocker was bypassed.

## 13. Files Changed

E1 implementation files:

- `src/cover_kbc/leaderboard_repair/config.py`
- `src/cover_kbc/leaderboard_repair/relations.py`
- `src/cover_kbc/leaderboard_repair/stack.py`
- `configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`
- `tests/test_leaderboard_repair.py`
- `tests/test_profile_e1_mistral_city_rescue.py`
- `docs/audits/0087-profile-e1-mistral-city-empty-rescue.md`

The workspace also contains the prior uncommitted Profile D baseline-promotion
files from Audit 0086.

## 14. Validation

Observed validation:

| command | result |
| --- | --- |
| `python -m pytest tests/test_profile_e1_mistral_city_rescue.py -q -p no:randomly` | `11 passed in 0.86s` |
| `python -m pytest tests/test_leaderboard_repair.py -q -p no:randomly` | `17 passed in 1.45s` |
| `python -m pytest tests/test_profile_d_mistral_role_swap.py tests/test_profile_e1_mistral_city_rescue.py tests/test_leaderboard_repair.py tests/test_v3_test_activation.py -q -p no:randomly` | `52 passed in 1.98s` |
| semantic Profile D -> E1 config diff | only `mistral_city_empty_rescue` false->true and City cap 0->2, plus metadata |
| `python scripts/check_v3_test_readiness.py --config configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml --json` | exit `2` as expected; state `NOT_READY`; explicit leaderboard-probe gate accepts the blocker set |
| `python -m pytest tests/ -q -p no:randomly` | `4091 passed, 4 skipped in 67.71s` |
| `python -m pytest tests/ -q` | `4091 passed, 4 skipped in 66.54s` |
| `python -m pyflakes src/ tests/ scripts/` | pass |
| `git diff --check` | pass |

## 15. User-Run Plan

After review and commit, run E1 in Colab with:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml \
  --no-eval
```

Do not pass a relation filter for the leaderboard submission. E1 should emit all
475 official TEST rows. After the run, verify that no Qwen model id appears in
`calls.jsonl` and that `repair_accounting.json` contains the E1 City rescue
summary.
