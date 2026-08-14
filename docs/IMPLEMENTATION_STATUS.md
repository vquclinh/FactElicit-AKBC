# COVER-KBC Public Release Status

Current source-of-truth date: 2026-08-14.

## Frozen System

The public repository is trimmed around the frozen COVER-KBC F1 system:

- Config: `configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml`
- Hidden TEST Macro-F1: `0.5878`
- Model: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- Revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- Unique neural parameters: `24,011,361,280 / 32,000,000,000`

All logical neural roles reuse this one physical Mistral checkpoint. Qwen and
other historical model portfolios are not active in the public F1 runtime.

## Active Final Relation Layer

| Relation | Final strategy |
|---|---|
| `countryLandBordersCountry` | core small-set border path |
| `companyTradesAtStockExchange` | core stock listing path plus empty-row stock-exchange rescue |
| `personHasCityOfDeath` | two-stage standalone Mistral city protocol |
| `hasArea` | Area Multi-View |
| `hasCapacity` | Capacity Multi-View |
| `awardWonBy` | deterministic metadata cleanup and deduplication |

## Hidden TEST Scores

The hidden labels are not released, so these are leaderboard-reported scores.

| Relation | P | R | F1 |
|---|---:|---:|---:|
| `awardWonBy` | 0.3255 | 0.3707 | 0.3105 |
| `companyTradesAtStockExchange` | 0.8992 | 0.7963 | 0.7385 |
| `countryLandBordersCountry` | 0.9712 | 0.9295 | 0.9291 |
| `hasArea` | 0.6700 | 0.6700 | 0.6700 |
| `hasCapacity` | 0.2449 | 0.1633 | 0.1633 |
| `personHasCityOfDeath` | 0.9600 | 0.5900 | 0.5700 |
| **All Relations** | **0.7268** | **0.6055** | **0.5878** |

Zero-object cases: P = `0.6038`, R = `0.9412`, F1 = `0.7356`.

## Public Repository Scope

The public tree keeps the F1 runtime, the official benchmark snapshot, the F1
Colab notebook, minimal reproduction scripts, calibration artifacts required by
the config, and the audit trail. Historical experiment configs, diagnostic
scripts, generated outputs, runbooks, paper-review notes, and the large
development test suite have been removed from the public surface.

`docs/audits/` remains as provenance for prior profile promotions and rejected
probes.
