# Audit 0093 - Profile F1 Stock Empty Rescue Promotion

## Status

Profile F1 is the promoted frozen baseline after user-provided hidden TEST
evidence. The implementation is a downstream Profile E3 post-repair layer that
may mutate only `companyTradesAtStockExchange`.

Starting repository state before implementation:

- `git rev-parse HEAD`: `9281cd251cfc3ed898c8d586a7e11b4b925fa398`
- `git status --short`: clean

No commit or push was performed by Codex.

## Frozen Score History

User-provided hidden TEST evidence:

| Profile | Overall F1 | Notes |
| --- | ---: | --- |
| D | 0.4952 | Mistral-only role swap baseline |
| E1 | 0.5752 | City rescue + direct area integrated line |
| E2 | 0.5836 | E1 + MistralCapacityMultiView |
| E3 | 0.5857 | E2 + MistralAreaMultiView |
| F1 | 0.5878 | E3 + MistralStockEmptyRescue |

Profile F1 relation scores from the user-provided leaderboard run:

| Relation | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| `awardWonBy` | 0.3255 | 0.3707 | 0.3105 |
| `companyTradesAtStockExchange` | 0.8992 | 0.7963 | 0.7385 |
| `countryLandBordersCountry` | 0.9712 | 0.9295 | 0.9291 |
| `hasArea` | 0.6700 | 0.6700 | 0.6700 |
| `hasCapacity` | 0.2449 | 0.1633 | 0.1633 |
| `personHasCityOfDeath` | 0.9600 | 0.5900 | 0.5700 |
| All relations | 0.7268 | 0.6055 | 0.5878 |
| Zero-object cases | 0.6038 | 0.9412 | 0.7356 |

Delta versus Profile E3:

- `companyTradesAtStockExchange` F1: `+0.0100`
- Overall F1: `+0.0021`
- All other relation F1 values unchanged.

The reverted Capacity exactness probe was not promoted and is not active.

## Causal Question

Can the existing E3 stock output improve if we selectively spend extra Mistral
calls only on `companyTradesAtStockExchange` rows where E3 predicted an empty
object set?

The accepted hidden TEST result says yes for this probe: stock recall increased
from `0.7863` to `0.7963`, stock F1 increased from `0.7285` to `0.7385`, and
overall F1 increased from `0.5857` to `0.5878`.

## Implementation

New module:

- `src/cover_kbc/leaderboard_repair/stock_empty_rescue.py`

New feature flag:

- `leaderboard_repair.features.mistral_stock_empty_rescue`

New mode:

- `leaderboard_repair.stock_empty_rescue_mode: EMPTY_ONLY_STRONG_CONSENSUS`

New promoted config:

- `configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml`

Profile E3 remains available as the historical predecessor:

- `configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`

## Stock Empty Rescue Policy

For every `companyTradesAtStockExchange` row:

1. If E3 already emitted one or more objects, keep E3 unchanged and spend zero
   stock-rescue calls.
2. If E3 emitted `[]`, run four deterministic, closed-book, candidate-blind
   Mistral recall views.
3. Parse only strict outputs:
   - `UNKNOWN`
   - or one to three `EXCHANGE: <exchange name>` lines.
4. Reject prose, ticker symbols, index/regulator/country/market-segment text,
   and structurally non-exchange-like values.
5. Normalize common exchange aliases only for clustering.
6. Accept at most one observed exchange value.
7. Fill the empty row only if the top normalized exchange cluster has support
   `>= 3` of the four views.
8. Otherwise keep the E3 empty output.

No candidate-fed verifier is used. No stock value is manually authored or read
from a subject-specific answer table.

## Model and Budget

Profile F1 uses the same single physical model as E3:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- unique published parameters: `24,011,361,280`
- competition limit: `32,000,000,000`

Qwen is inactive. The logical enumerator and verifier roles resolve to the same
Mistral runtime object for the targeted repair runner.

Call accounting:

- non-empty stock row: `0` F1 stock rescue calls
- empty stock row: exactly `4` F1 stock rescue calls
- all non-stock relations: `0` F1 stock rescue calls

## Targeted Execution

Dry-run:

```bash
python scripts/run_stock_empty_rescue.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml \
  --baseline-predictions /content/profile_e3_best_predictions.jsonl \
  --split test \
  --output-dir /content/f1_stock_empty_rescue \
  --dry-run
```

Real targeted run:

```bash
python scripts/run_stock_empty_rescue.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml \
  --baseline-predictions /content/profile_e3_best_predictions.jsonl \
  --split test \
  --output-dir /content/f1_stock_empty_rescue
```

Merge into the 475-row submission:

```bash
python scripts/merge_targeted_relation_results.py \
  --baseline-predictions /content/profile_e3_best_predictions.jsonl \
  --targeted-results /content/f1_stock_empty_rescue/stock_empty_rescue_results.jsonl \
  --relation companyTradesAtStockExchange \
  --expected-targeted-rows 100 \
  --output /content/profile_f1_stock_empty_rescue_475.jsonl
```

The merge utility fails closed on:

- baseline row count not equal to `475`
- targeted stock row count not equal to `100`
- missing/extra/duplicate stock keys
- row-order changes
- non-stock mutation

## Validation Scope

Added tests cover:

- strict stock parser acceptance/rejection
- four candidate-blind stock prompts
- alias-aware support clustering
- `>=3/4` consensus acceptance
- weak consensus rejection
- non-empty stock rows kept with zero calls
- non-stock rows receiving zero stock rescue calls
- F1 semantic diff from E3 is stock-rescue-only
- one Mistral checkpoint, no Qwen, total parameters unchanged
- targeted runner dry-run without model load

Focused validation completed during implementation:

```text
python -m pytest tests/test_profile_f1_stock_empty_rescue.py \
  tests/test_leaderboard_repair.py \
  tests/test_profile_e3_area_multiview.py -q

35 passed
```

## Provenance Statement

This implementation did not inspect hidden TEST gold and did not use web, RAG,
external KBs, APIs, or subject-specific lookup tables. The hidden TEST score is
user-provided leaderboard evidence from the targeted stock-empty rescue probe.

Profile F1 is the new promoted frozen baseline; Profile E3 remains the frozen
historical predecessor at overall F1 `0.5857`.
