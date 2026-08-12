# Audit 0085 - A+Award baseline and Profile D Mistral-only role swap

Supersession note, 2026-08-12: Audit 0086 records the later hidden TEST result
that promoted Profile D to the frozen best baseline at overall F1 `0.4952`.
This audit remains the historical CPU/static preparation record from before
that leaderboard result was known.

## 1. Scope

This audit records a CPU/static implementation pass only.  No TRAIN, VAL, or
TEST neural inference was run.  No TEST gold, web, RAG, external factual corpus,
fine-tuning, third runtime model, hand-edited predictions, or subject-answer
lookup table was used.

## 2. Hidden leaderboard evidence

User-provided hidden TEST evidence:

| profile | overall F1 | awardWonBy | companyTradesAtStockExchange | countryLandBordersCountry | hasArea | hasCapacity | personHasCityOfDeath |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Profile A | 0.4906 | 0.2929 | 0.7103 | 0.9264 | 0.3600 | 0.1224 | 0.4900 |
| Profile A+Award | 0.4910 | 0.3105 | 0.7103 | 0.9264 | 0.3600 | 0.1224 | 0.4900 |
| Profile C2 aggressive non-Stock | 0.4012 | 0.2263 | 0.7103 | 0.7206 | 0.3200 | 0.0204 | 0.3500 |

Source commit at implementation time:
`e9d2f42505dd2ee98f3e7ab7ad846340d607169a`.

## 3. Why A+Award was promoted at that time

At the time of Audit 0085, Profile A+Award was the frozen best baseline because
it was the only measured profile above Profile A. Its intended behavior was:

- Profile-A base pipeline.
- `stock_listing_entity_prompt = true`.
- `stock_support_dominance = false`.
- `AwardMetadataNormalizer = enabled`.
- No Stock repair stack.
- No aggressive C2 repair stack.
- Zero post-model repair calls from AwardMetadataNormalizer.
- Only `awardWonBy` differs from Profile A.

Known A+Award prediction artifact:

- path after promotion: `outputs/submission-best/predictions.jsonl`
- SHA256: `bf113ce4fb87f5ac7a54c5d78dbf14e691b07a562f78c9b9ee61ec5763982a88`
- rows: 475

Known blind output shape:

| relation | rows | empty | objects | max objects |
| --- | ---: | ---: | ---: | ---: |
| awardWonBy | 10 | 0 | 513 | 102 |
| companyTradesAtStockExchange | 100 | 46 | 101 | 28 |
| countryLandBordersCountry | 67 | 11 | 258 | 13 |
| hasArea | 100 | 15 | 85 | 1 |
| hasCapacity | 98 | 25 | 73 | 1 |
| personHasCityOfDeath | 100 | 98 | 2 | 1 |

Known repair accounting:

| scope | changed rows | repair calls | feature |
| --- | ---: | ---: | --- |
| profile | 1 | 0 | `A_PLUS_AWARD` |
| awardWonBy | 1 | 0 | `AwardMetadataNormalizer` |
| all other relations | 0 | 0 | none |

## 4. Why C2 is retired

C2 made 1075 post-calls and changed 246/475 rows.  Hidden TEST evidence showed
large regressions across weak non-Stock relations while Stock stayed unchanged
only because the C2 Stock bypass worked.

| relation | A+Award F1 | C2 F1 | direction |
| --- | ---: | ---: | --- |
| awardWonBy | 0.3105 | 0.2263 | regression |
| companyTradesAtStockExchange | 0.7103 | 0.7103 | unchanged |
| countryLandBordersCountry | 0.9264 | 0.7206 | regression |
| hasArea | 0.3600 | 0.3200 | regression |
| hasCapacity | 0.1224 | 0.0204 | regression |
| personHasCityOfDeath | 0.4900 | 0.3500 | regression |
| overall | 0.4910 | 0.4012 | regression |

C2 is retained only for historical reproducibility and marked
`RETIRED_NEGATIVE_HIDDEN_TEST_PROBE` in
`configs/experiments/cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml`.
Future active profiles must not derive from C2.

## 5. Profile D causal question

Profile D asks exactly one leaderboard question:

> What happens if the exact jobs currently done by Qwen/Qwen3.5-4B are instead
> done by Mistral-Small-3.2-24B using the same verifier/gate/resolver
> contracts?

Profile D starts from A+Award and changes only the verifier model identity.
It does not add L10, new routers, new prompts, direct-answer mode, second-pass
reasoning, C2 repairs, numeric/city/border/award recall, Stock repair, or new
thresholds.

## 6. A+Award vs Profile D config diff

Machine-readable semantic diff:

```json
{
  "verifier.model_id": [
    "Qwen/Qwen3.5-4B",
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
  ],
  "verifier.revision": [
    "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
    "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
  ],
  "unique_model_portfolio": [
    [
      "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
      "Qwen/Qwen3.5-4B"
    ],
    [
      "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    ]
  ],
  "parameter_total": [
    28671226368,
    24011361280
  ]
}
```

Prediction-affecting sections are required by tests to match A+Award:
`pipeline`, `leaderboard_repair.features`, `leaderboard_repair.max_calls_by_relation`,
M9-M19 shadow modules, M20/M21 calibration wiring, Layer 6 wiring,
`test_dataset`, and calibration provenance.

## 7. Model portfolio before/after

A+Award active unique neural portfolio:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  @ `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- `Qwen/Qwen3.5-4B`
  @ `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`

Profile D active unique neural portfolio:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  @ `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`

Qwen must not be instantiated, downloaded, loaded, called, or used as fallback
in Profile D.

## 8. Runtime reuse strategy

`configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`
declares a single physical `model_profile.enumerator` block.  The canonical
`model_blocks(config)` resolver returns that same block for the logical verifier
when no separate verifier block is declared.  `scripts/run_cover.py` already
aliases the verifier runtime when the resolved configs are equal:

```python
verifier_runtime = runtime if verifier_cfg == enumerator_cfg else build_runtime(verifier_cfg)
```

Therefore Profile D loads one Mistral runtime object and reuses it for both
logical roles.

## 9. Parameter accounting

Profile D unique neural parameter accounting:

- total: `24,011,361,280`
- limit: `32,000,000,000`
- status: legal

The same Mistral checkpoint is counted once because the same weights are loaded
once.  The budget audit must not count the same physical checkpoint twice, but
also must not hide any separate loaded checkpoint.  Focused tests assert the
Profile D portfolio contains only the Mistral checkpoint.

## 10. Calibration caveat

Profile D is not `FULL_TEST_READY`.  Swapping Qwen out of the verifier role is
calibration-shifting relative to the frozen M20/M21 derivation.

Expected readiness blockers:

- `selection.v3_1: CALIBRATION_REVIEW_REQUIRED`
- verifier model id differs from the frozen Qwen verifier
- verifier revision differs from the frozen Qwen verifier
- model budget total differs from the frozen Mistral+Qwen portfolio

The explicit leaderboard-probe path allows only those blockers for Profile D.
Unrelated TEST readiness blockers still refuse before weights load.

## 11. Readiness behavior

Expected zero-model readiness state:

- state: `NOT_READY`
- execution permission: `LEADERBOARD_PROBE_ALLOWED`
- reason: intentional calibration-review leaderboard probe for Mistral-only
  verifier role swap.

Profile D should be run only as a leaderboard probe.  It must not be described
as better than A+Award unless hidden TEST scoring proves it.

## 12. Exact files changed

- `configs/experiments/cover_kbc_v3_2_profile_a_plus_award_test.yaml`
- `configs/experiments/cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml`
- `configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`
- `docs/audits/0085-a-plus-award-baseline-and-profile-d-mistral-only-role-swap.md`
- `scripts/run_cover.py`
- `tests/test_leaderboard_repair.py`
- `tests/test_profile_d_mistral_role_swap.py`
- `outputs/submission-best/predictions.jsonl` if the exact artifact is promoted
  from `outputs/submission-best.zip`.

## 13. Tests

CPU validation commands for this audit:

```bash
python -m pytest tests/test_profile_d_mistral_role_swap.py tests/test_leaderboard_repair.py -q -p no:randomly
python scripts/check_v3_test_readiness.py --config configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml --json
python -m pyflakes src/ tests/ scripts/
git diff --check
```

The readiness command is expected to exit non-zero because Profile D is
`NOT_READY`; the acceptance condition is the explicit leaderboard-probe gate,
covered by tests.

Observed CPU validation results:

| command | result |
| --- | --- |
| `python -m pytest tests/test_profile_d_mistral_role_swap.py tests/test_leaderboard_repair.py -q -p no:randomly` | `26 passed in 2.22s` |
| `python scripts/check_v3_test_readiness.py --config configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml --json` | exit `2` as expected; state `NOT_READY`; explicit probe gate accepts the blocker set |
| semantic diff / preflight dump | exact expected diff; `leaderboard_probe_allowed=true`; `unique_parameter_total=24011361280`; active Qwen in Profile D model profile: `false` |
| `python -m pyflakes src/ tests/ scripts/` | pass |
| `git diff --check` | pass |

## 14. User-run TEST plan

Run this config in Colab:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml \
  --no-eval
```

Do not pass a relation filter.  Profile D must emit all 475 official TEST rows.
After the run, `calls.jsonl` should contain one unique model id:
`mistralai/Mistral-Small-3.2-24B-Instruct-2506`.

## 15. Interpretation matrix

| Profile D hidden TEST result | interpretation |
| --- | --- |
| D > 0.4910 | Evidence that Qwen may be a bottleneck for this pipeline/config. |
| D ~= 0.4910 | Qwen provides little measurable value here; Mistral-only may still be preferable for runtime simplicity. |
| D < 0.4910 | Independent Qwen verification is beneficial; retain the dual-model A+Award baseline. |

One leaderboard probe cannot prove universal model superiority.  It can only
measure this pipeline, these prompts, this calibration state, and this
Mistral-only verifier role swap.
