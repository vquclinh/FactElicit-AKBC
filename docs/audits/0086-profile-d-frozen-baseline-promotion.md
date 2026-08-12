# Audit 0086 - Profile D frozen baseline promotion

## 1. Scope

This audit records a promotion-only repository update. No TRAIN, VAL, or TEST
neural inference was run. No web, RAG, external factual corpus, TEST gold,
fine-tuning, third runtime model, hand-edited predictions, Profile E logic, or
new modeling behavior was introduced.

The user commits. This task does not commit or push.

## 2. Promotion Purpose

Profile D is promoted from leaderboard probe to the frozen COVER-KBC baseline
because hidden TEST scoring beat the previous A+Award baseline without adding a
new repair architecture.

Exact originating source:
`170c48756660a34d611b3a563ac26cd4564434ef`

Profile D config:
`configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`

Submitted prediction SHA256:
`7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c`

Submitted rows: `475`

Standalone submission name:
`SUBMIT_PROFILE_D_MISTRAL_ONLY_170c48756660_20260812T104512Z.jsonl`

Full run name:
`profile_d_mistral_only_role_swap_170c48756660_20260812T104512Z`

## 3. Hidden Leaderboard Evidence

| relation | precision | recall | F1 |
| --- | ---: | ---: | ---: |
| awardWonBy | 0.3255 | 0.3707 | 0.3105 |
| companyTradesAtStockExchange | 0.9092 | 0.7863 | 0.7285 |
| countryLandBordersCountry | 0.9712 | 0.9295 | 0.9291 |
| hasArea | 0.5100 | 0.3600 | 0.3600 |
| hasCapacity | 0.3776 | 0.1224 | 0.1224 |
| personHasCityOfDeath | 0.9900 | 0.4900 | 0.4900 |
| **All Relations** | **0.7289** | **0.5087** | **0.4952** |

Zero-object reference:

| precision | recall | F1 |
| ---: | ---: | ---: |
| 0.4757 | 0.9608 | 0.6364 |

Profile D beats A+Award:

| profile | overall F1 |
| --- | ---: |
| A+Award historical baseline | 0.4910 |
| Profile D frozen baseline | 0.4952 |
| delta | +0.0042 |

Relation deltas vs A+Award:

| relation | A+Award F1 | Profile D F1 | delta |
| --- | ---: | ---: | ---: |
| awardWonBy | 0.3105 | 0.3105 | 0.0000 |
| companyTradesAtStockExchange | 0.7103 | 0.7285 | +0.0182 |
| countryLandBordersCountry | 0.9264 | 0.9291 | +0.0027 |
| hasArea | 0.3600 | 0.3600 | 0.0000 |
| hasCapacity | 0.1224 | 0.1224 | 0.0000 |
| personHasCityOfDeath | 0.4900 | 0.4900 | 0.0000 |

City did not improve. Profile D still has `personHasCityOfDeath` F1 `0.4900`
and only `2 / 100` non-empty City rows. The standalone Mistral City experiment
is not part of this baseline.

## 4. Causal Interpretation

Profile D starts from A+Award and changes one conceptual variable only:

```text
verifier role:
Qwen/Qwen3.5-4B
->
mistralai/Mistral-Small-3.2-24B-Instruct-2506
```

The enumerator remains Mistral-Small-3.2-24B. The verifier receives the same
base verifier/gate/decision contracts as before. No prompts, thresholds, action
budgets, repair features, relation ordering, output schema, or TEST dataset are
changed by the promotion.

## 5. Model Portfolio

Frozen Profile D active unique neural portfolio:

| role | model | revision | parameter accounting |
| --- | --- | --- | ---: |
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier | same physical/configured Mistral checkpoint | same revision | counted once |

Unique neural parameter total: `24,011,361,280`

Competition limit: `32,000,000,000`

Qwen is not active in Profile D.

## 6. Runtime And Call Accounting

Submitted run accounting:

| field | value |
| --- | ---: |
| total queries | 475 |
| successful queries | 475 |
| failed queries | 0 |
| pipeline error rows | 0 |
| unresolved invariant errors | 0 |

`calls.jsonl` summary:

| field | value |
| --- | ---: |
| total call records | 2560 |
| Mistral model-id records | 1380 |
| Qwen model-id records | 0 |
| generation calls | 1180 |
| decision calls | 1180 |
| gate calls | 200 |
| records without model_id | 1180 |

Repair accounting:

| field | value |
| --- | ---: |
| changed rows | 1 |
| total repair calls | 0 |
| changed feature | `AwardMetadataNormalizer` |

No model-backed post-repair calls were part of the submitted run.

## 7. Output Shape

| relation | rows | empty | objects | max objects |
| --- | ---: | ---: | ---: | ---: |
| awardWonBy | 10 | 0 | 513 | 102 |
| companyTradesAtStockExchange | 100 | 57 | 93 | 29 |
| countryLandBordersCountry | 67 | 11 | 260 | 14 |
| hasArea | 100 | 15 | 85 | 1 |
| hasCapacity | 98 | 25 | 73 | 1 |
| personHasCityOfDeath | 100 | 98 | 2 | 1 |

## 8. Repository Promotion

Promotion metadata is now stored in the Profile D config under
`experiment.frozen_baseline`. A+Award remains available and is marked
`HISTORICAL_BASELINE_SUPERSEDED_BY_PROFILE_D`. C2 remains available and marked
`RETIRED_NEGATIVE_HIDDEN_TEST_PROBE`.

The repository currently has no tracked standalone best-profile registry beyond
config metadata, documentation, tests, and gitignored `outputs/submission-best`
artifacts.

Local artifact availability:

- `outputs/submission-best/predictions.jsonl` exists with the historical
  A+Award SHA256
  `bf113ce4fb87f5ac7a54c5d78dbf14e691b07a562f78c9b9ee61ec5763982a88`.
- The exact Profile D prediction artifact SHA
  `7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c`
  was not found locally under `outputs/`.
- No synthetic replacement was created.

Therefore the code/config/docs promotion is complete, but the gitignored
`outputs/submission-best/predictions.jsonl` artifact still needs the exact
Profile D artifact copied by the user when available locally.

## 9. Calibration And Readiness Caveat

Profile D remains an intentional leaderboard-probe baseline relative to older
M20/M21 calibration provenance. The verifier model identity changed from Qwen
to Mistral without re-derived TRAIN calibration.

The promotion records the hidden TEST winner; it does not invent new
calibration measurements and does not claim that the older calibrated readiness
state automatically applies.

## 10. Predictive Behavior

No predictive algorithm changed in this promotion task.

Specifically, this task did not add or enable:

- Profile E
- direct City QA
- forced life-status QA
- C2 features
- L10 or FailureSignatureVector
- new repair modules
- new thresholds
- new calibration
- new model
- Qwen fallback
- new Stock, Border, numeric, City, or Award recall logic

The future Profile E1 candidate must diff cleanly against the Profile D frozen
baseline.

## 11. Files Changed

Promotion files:

- `README.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `configs/experiments/cover_kbc_v3_2_profile_a_plus_award_test.yaml`
- `configs/experiments/cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml`
- `configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`
- `docs/audits/0085-a-plus-award-baseline-and-profile-d-mistral-only-role-swap.md`
- `docs/audits/0086-profile-d-frozen-baseline-promotion.md`
- `tests/test_baseline_restoration.py`
- `tests/test_profile_d_mistral_role_swap.py`

No raw prediction artifact was changed because the exact Profile D artifact was
not locally available.

## 12. Validation

Observed validation for this audit:

| command | result |
| --- | --- |
| `git status --short` | working tree changed only by promotion files plus pre-existing untracked `tasks.jsonl` |
| semantic Profile D vs A+Award config dump | prediction-affecting sections match; repair flags/caps match; diff is verifier id, verifier revision, unique model portfolio, parameter total |
| `python -m pytest tests/test_profile_d_mistral_role_swap.py -q -p no:randomly` | `10 passed in 0.67s` |
| `python -m pytest tests/test_leaderboard_repair.py tests/test_v3_test_activation.py -q -p no:randomly` | `31 passed in 1.88s` |
| `python scripts/check_v3_test_readiness.py --config configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml --json` | exit `2` as expected; state `NOT_READY`; 475 TEST rows; 0 TEST rows with ObjectEntities; blockers limited to verifier model id/revision role swap, parameter total shift, and `selection.v3_1` calibration review |
| `python -m pytest tests/ -q -p no:randomly` | `4080 passed, 4 skipped in 67.24s` |
| `python -m pytest tests/ -q` | `4080 passed, 4 skipped in 67.40s` |
| `python -m pyflakes src/ tests/ scripts/` | pass |
| `git diff --check` | pass |

## 13. Recommended User Action

Commit the promotion after reviewing the diff. When the exact Profile D
prediction artifact is available locally, copy that artifact to
`outputs/submission-best/predictions.jsonl` and verify SHA256
`7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c`.
