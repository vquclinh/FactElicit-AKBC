# Audit 0091 - Profile E3 Area Multi-View Baseline Promotion

## Scope

This audit documents promotion wiring for **Profile E3**:

Profile E3 = Profile E2 + `MistralAreaMultiView` for `hasArea`.

No hidden TEST neural inference, TRAIN inference, or VAL inference was run for
this promotion. The hidden TEST scores below are user-provided leaderboard
evidence from a controlled Area-only ablation.

## Repository State

- Starting HEAD: `5426571e7fe467f80b53db4ef347e9b195381c56`
- Starting working tree: clean
- Current audit path: `docs/audits/0091-profile-e3-area-multiview-baseline-promotion.md`
- User commits; no commit or push was performed by Codex.

## User-Provided Hidden TEST Evidence

Previous official baseline, Profile E2:

| relation | F1 |
|---|---:|
| awardWonBy | 0.3105 |
| companyTradesAtStockExchange | 0.7285 |
| countryLandBordersCountry | 0.9291 |
| hasArea | 0.6600 |
| hasCapacity | 0.1633 |
| personHasCityOfDeath | 0.5700 |
| All Relations | 0.5836 |

New controlled Area-only submission, Profile E3:

| relation | precision | recall | F1 |
|---|---:|---:|---:|
| awardWonBy | 0.3255 | 0.3707 | 0.3105 |
| companyTradesAtStockExchange | 0.9092 | 0.7863 | 0.7285 |
| countryLandBordersCountry | 0.9712 | 0.9295 | 0.9291 |
| hasArea | 0.6700 | 0.6700 | 0.6700 |
| hasCapacity | 0.2449 | 0.1633 | 0.1633 |
| personHasCityOfDeath | 0.9600 | 0.5900 | 0.5700 |
| All Relations | 0.7289 | 0.6034 | 0.5857 |
| Zero-object cases | 0.5963 | 0.9412 | 0.7300 |

Recorded deltas:

| relation | delta F1 |
|---|---:|
| hasArea | +0.0100 |
| All Relations | +0.0021 |
| all non-Area relations | 0.0000 |

This is recorded only as a positive controlled hidden TEST probe. It is not a
claim that Area Multi-View is universally superior.

## Baseline Progression

| profile | status after this audit | overall F1 | hasArea F1 | hasCapacity F1 |
|---|---|---:|---:|---:|
| Profile D | historical | 0.4952 | 0.3600 | 0.1224 |
| Integrated Profile E1 | historical | 0.5752 | 0.6600 | 0.1224 |
| Profile E2 Capacity Multi-View | historical predecessor | 0.5836 | 0.6600 | 0.1633 |
| Profile E3 Area Multi-View | current frozen baseline | 0.5857 | 0.6700 | 0.1633 |

## Implemented Method

`MistralAreaMultiView` applies only when `Relation == "hasArea"` and uses
`DIRECT_ALL` replacement semantics. The upstream/current Area answer is
recorded as ignored and never participates as a vote.

For every Area row it runs four deterministic recall views:

1. `area_multiview_v1_direct`: existing Direct Area semantics, with exact entity
   identity, island/lake/country handling, unit conversion, and explicit
   near-miss rejection.
2. `area_multiview_v2_entity_type`: silently classify the exact subject as
   COUNTRY, ISLAND, LAKE, or OTHER_GEOGRAPHIC_ENTITY, then return the
   relation-compatible area.
3. `area_multiview_v3_infobox`: recall the canonical encyclopedic/infobox-style
   published area and distinguish entity/type/unit/attribute confusions.
4. `area_multiview_v4_attribute_contrast`: silently step through entity
   identity, type, correct attribute, wrong-unit rejection, and km2 conversion.

Output format is strict:

```text
AREA: <number>
```

or:

```text
UNKNOWN
```

The source-blind judge is run only when no cluster has support at least 3 and
there is at least one numeric candidate. The judge sees the exact subject,
relation semantics, anonymized numeric candidate labels, and an UNKNOWN option.
It does not see view names, view identities, or support counts.

## Parser

The parser accepts exactly one non-empty line. Accepted forms include integers,
decimals, grouped comma numbers, and `UNKNOWN`. A valid `AREA:` value is
canonicalized to a positive finite `Decimal`, commas are removed, integral
decimals are formatted as integers, and non-integral decimals retain only needed
decimal digits.

Rejected forms include bare numbers, unit-bearing output, ranges, multiple
candidates, prose, `NaN`, `Infinity`, zero, and negative values.

## Clustering and Decision

Valid numeric outputs from V1-V4 are clustered using:

```text
abs(a - b) / max(abs(a), abs(b)) <= 0.05
```

UNKNOWN and invalid outputs do not enter clusters. No arithmetic synthetic
answer is created. The representative is one observed output nearest the
cluster median, tie-broken by view order and numeric value.

Decision order:

1. If the best cluster has `support >= 3`, accept its representative and do not
   run the judge. Reason: `MULTIVIEW_SUPPORT_GE_3`.
2. Otherwise, run at most one source-blind judge when numeric candidates exist.
   A valid judge choice wins. Reason: `JUDGE`.
3. If the judge is invalid or UNKNOWN and V1 has a valid number, use V1.
   Reason: `FALLBACK_V1_DIRECT_AREA`.
4. If V1 is absent but a cluster exists, use the top cluster representative.
   Reason: `FALLBACK_TOP_CLUSTER`.
5. If no numeric answers exist, output `[]`. Reason: `NO_VALUE`.

## Mutation Boundary

Profile E3 differs from Profile E2 in prediction-affecting config only here:

```json
{
  "feature_delta": {
    "mistral_area_multiview": [false, true]
  },
  "cap_delta": {
    "hasArea": [1, 5]
  },
  "mode_delta": {
    "area_multiview_mode": ["OFF", "DIRECT_ALL"],
    "capacity_multiview_mode": ["DIRECT_ALL", "DIRECT_ALL"],
    "direct_area_mode": ["DIRECT_ALL", "DIRECT_ALL"]
  }
}
```

The following sections are byte/semantic unchanged from E2 to E3:

- `pipeline`
- `query_intelligence`
- `specialists`
- `consensus`
- `specialist_verifier`
- `bidirectional_verification`
- `layer4_integration`
- `coverage_gap`
- `relation_budget_scheduler`
- `micro_planner`
- `layer6_integration`
- `test_dataset`
- `calibration_provenance`
- `model_profile`
- `budget_assertion`

Tests additionally prove no Area Multi-View calls are made on non-Area rows and
the targeted merge preserves all 375 non-Area rows.

## Model Portfolio

Profile E3 uses the same single physical Mistral checkpoint as Profile E2:

- model id: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- unique published parameters: `24,011,361,280`
- competition limit: `32,000,000,000`

The `model_blocks` resolver returns the same block for enumerator and verifier.
Qwen is not active in the E3 config. No API model, web retrieval, RAG, external
KB, external factual corpus, or subject-specific lookup table was introduced.

## Call Accounting

For each `hasArea` row:

- always four Area recall views
- zero judge calls when `support >= 3`
- otherwise at most one source-blind judge call
- maximum calls per Area row: 5

All calls reuse the same Mistral runtime through the existing repair stack.

## Targeted Runner and Merge

Added `scripts/run_area_multiview.py`, a TEST-only targeted runner that:

- validates Profile E3 uses one Mistral checkpoint under the 32B budget
- requires exactly 100 TEST `hasArea` rows
- preserves official TEST Area order
- writes 100 official Area result rows
- writes diagnostics sidecars: records, calls, accounting
- supports `--dry-run` for static validation without loading the model

Added `scripts/merge_area_multiview_results.py`, a wrapper around the generic
targeted merge helper with `relation=hasArea` and `expected_targeted_rows=100`.
Merge invariants include 475 baseline rows, 100 Area result rows, exact key
coverage, no duplicates, unchanged output order, unchanged non-Area rows, and
Area-only changed relation set.

## Artifact Status

The exact Profile E3 hidden-winning 475-row artifact was not verifiably
available locally during this promotion.

Observed local artifact:

- path: `outputs/submission-best/predictions.jsonl`
- SHA256: `c7e52a71d8366ba5863ce1e207cda13e294498e0bd89ac35d1ad951d53290e2d`
- rows: 475
- relation census:
  - `awardWonBy`: 10
  - `companyTradesAtStockExchange`: 100
  - `countryLandBordersCountry`: 67
  - `hasArea`: 100
  - `hasCapacity`: 98
  - `personHasCityOfDeath`: 100

Because the exact E2 winning artifact is also not locally available, the
required non-Area comparison against prior E2 could not be completed. Therefore
no local artifact was promoted as the exact Profile E3 winner, and Profile E3
metadata records:

```text
WINNING_PROFILE_E3_ARTIFACT_PENDING_USER_IMPORT
```

Outputs remain gitignored.

## Files Changed

- `README.md`
- `configs/experiments/cover_kbc_v3_6_profile_e2_mistral_capacity_multiview_test.yaml`
- `configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/audits/0091-profile-e3-area-multiview-baseline-promotion.md`
- `scripts/merge_area_multiview_results.py`
- `scripts/run_area_multiview.py`
- `src/cover_kbc/leaderboard_repair/area.py`
- `src/cover_kbc/leaderboard_repair/area_multiview.py`
- `src/cover_kbc/leaderboard_repair/config.py`
- `src/cover_kbc/leaderboard_repair/stack.py`
- `tests/test_leaderboard_repair.py`
- `tests/test_profile_e2_capacity_multiview.py`
- `tests/test_profile_e3_area_multiview.py`

No historical audit 0089 or 0090 provenance was removed.

## Validation

Focused tests:

```bash
python -m pytest tests/test_profile_e3_area_multiview.py -q -p no:randomly
```

Result:

```text
14 passed in 0.55s
```

Repair regression tests:

```bash
python -m pytest tests/test_leaderboard_repair.py tests/test_profile_e2_capacity_multiview.py -q -p no:randomly
```

Result:

```text
23 passed in 1.29s
```

Full suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result:

```text
4110 passed, 21 skipped in 48.51s
```

Full suite, default plugin set:

```bash
python -m pytest tests/ -q
```

Result:

```text
4110 passed, 21 skipped in 47.35s
```

Pyflakes:

```bash
python -m pyflakes src/ tests/ scripts/
```

Result: pass, no output.

Diff whitespace:

```bash
git diff --check
```

Result: pass, no output.

Parameter budget:

```bash
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml
```

Result:

```text
total: 24.01B
RESULT: PASS
```

Targeted runner dry/static validation:

```bash
python scripts/run_area_multiview.py \
  --config configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml \
  --split test \
  --output-dir /tmp/cover-kbc-area-multiview-dry-run \
  --dry-run
```

Result:

```text
dry_run: /tmp/cover-kbc-area-multiview-dry-run/area_multiview_dry_run.json
```

No heavyweight hidden TEST inference, TRAIN inference, or VAL inference was run.

## Conclusion

Profile E3 is now the repository's current frozen hidden-TEST-winning baseline
metadata. Predictive behavior outside `hasArea` is unchanged by the E2-to-E3
semantic diff, and all active neural calls remain on one physical Mistral
checkpoint.
