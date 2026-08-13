# COVER-KBC Implementation Status

Current source-of-truth date: 2026-08-13.

## Current Profile

**Profile E3 - Mistral Area Multi-View** is the current frozen best system.

- Config: [`configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`](../configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml)
- Starting cleanup HEAD: `b94f0089b261ff98027bcdaa7d2dd9027191e10d`
- Hidden TEST overall Macro-F1: `0.5857`
- Current paper summary: [`docs/PAPER_SYSTEM_SUMMARY.md`](PAPER_SYSTEM_SUMMARY.md)
- Latest promotion audit before this cleanup: [`docs/audits/0091-profile-e3-area-multiview-baseline-promotion.md`](audits/0091-profile-e3-area-multiview-baseline-promotion.md)

Conceptual lineage:

```text
Profile D
  -> Integrated Profile E1
  -> Profile E2
  -> Profile E3
```

Profile E3 is Profile E2 plus `MistralAreaMultiView` for `hasArea`. It preserves
the E2 behavior for every non-Area relation in the controlled hidden TEST
submission.

## Model Portfolio

| Role | Model | Revision | Counted parameters |
|---|---|---|---:|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier/logical repair caller | same physical runtime | same revision | counted once |
| **total unique neural parameters** | | | **24,011,361,280 / 32,000,000,000** |

Qwen is not active in Profile E3. Historical Qwen configs and runtime
compatibility tests remain only to preserve provenance and prevent accidental
breakage of archived profiles.

## Active Runtime Graph

Canonical entrypoint:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml \
  --split test \
  --no-eval
```

Runtime flow:

```text
config YAML
  -> scripts/run_cover.py
  -> cover_kbc.models.registry.model_blocks
  -> one HuggingFaceRuntime for Mistral
  -> CoverPipeline.run
  -> core relation contracts, elicitation, parsing, evidence graph
  -> calibrated gates / verifier / controller / V3 core / M20 / M21
  -> final selector
  -> LeaderboardRepairStack
  -> official JSONL predictions + traces + repair accounting
```

Active final relation layer:

| Relation | Active final strategy | Post-pipeline mutation |
|---|---|---|
| `countryLandBordersCountry` | core small-set border path | none |
| `companyTradesAtStockExchange` | core stock listing path | none |
| `personHasCityOfDeath` | core null-single path plus Mistral empty-row rescue | only empty rows may become one city |
| `hasArea` | Area Multi-View | all Area rows replaced by MV decision |
| `hasCapacity` | Capacity Multi-View | all Capacity rows replaced by MV decision |
| `awardWonBy` | core award enumeration plus deterministic metadata normalizer | wrapper cleanup and dedupe only |

`MistralDirectArea` is not a separate E3 final layer. Its prompt is reused as
Area Multi-View V1, and upstream Area answers are ignored as votes.

## Hidden TEST Scores

User-provided Profile E3 hidden TEST evidence:

| Relation | P | R | F1 |
|---|---:|---:|---:|
| `awardWonBy` | 0.3255 | 0.3707 | 0.3105 |
| `companyTradesAtStockExchange` | 0.9092 | 0.7863 | 0.7285 |
| `countryLandBordersCountry` | 0.9712 | 0.9295 | 0.9291 |
| `hasArea` | 0.6700 | 0.6700 | 0.6700 |
| `hasCapacity` | 0.2449 | 0.1633 | 0.1633 |
| `personHasCityOfDeath` | 0.9600 | 0.5900 | 0.5700 |
| **All Relations** | **0.7289** | **0.6034** | **0.5857** |

Zero-object reference: P = `0.5963`, R = `0.9412`, F1 = `0.7300`.

## Positive Profile History

| Profile | Main verified change | Overall hidden F1 |
|---|---|---:|
| Profile D | Mistral-only core / verifier-role consolidation | 0.4952 |
| Integrated Profile E1 | Profile D + AwardMetadataNormalizer + City empty rescue + Direct Area | 0.5752 |
| Profile E2 | E1 + Capacity Multi-View | 0.5836 |
| Profile E3 | E2 + Area Multi-View | 0.5857 |

## Retired / Historical Only

These names may remain in historical configs, audits, and tests but are not
active in Profile E3:

- Qwen verifier portfolio
- C2 aggressive repair probes
- CHIV capacity probe
- NSMV / Neighborhood-Stable capacity probe
- Direct Border directional sweep experiments
- DeathCityRecall, AreaEmptyRescue, CapacityRepair, NumericAttributeResolver
- AwardRecipientWitness and AwardTimeSlicedRecall
- OpenRouter/Claude diagnostic quarantine references

The active `src/cover_kbc/leaderboard_repair/` package contains only the E3
repair stack and parseable retired feature flags needed for archival config
loading.

## Artifact Status

Generated predictions, logs, model caches, notebooks checkpoints, and local
outputs are ignored by `.gitignore`. The local `outputs/submission-best/`
artifact is an earlier tracked-by-user local artifact location but is ignored by
git; this cleanup does not claim it is the winning E3 hidden TEST artifact.

Known SHA evidence:

| Artifact | Status |
|---|---|
| Integrated E1 local `outputs/submission-best/predictions.jsonl` SHA256 | recorded in historical config as `67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6` |
| Profile E2 winning artifact SHA256 | not established locally |
| Profile E3 winning artifact SHA256 | not established locally |

## Current Commands

Budget audit:

```bash
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml
```

Profile E3 full run:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml \
  --split test \
  --no-eval
```

Targeted Area dry run without loading a model:

```bash
python scripts/run_area_multiview.py \
  --config configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml \
  --split test \
  --output-dir outputs/e3_area_multiview_dry_run \
  --dry-run
```

Validation:

```bash
python -m pyflakes src/ tests/ scripts/
python -m pytest tests/ -q -p no:randomly
python -m pytest tests/ -q
git diff --check
```

## Caveats

- Profile E3 is hidden-TEST scored, but its role-swap and post-pipeline
  City/Area/Capacity layers are not newly TRAIN-calibrated through the older V3
  calibration precheck. `run_cover.py` accepts this profile through explicit
  frozen-leaderboard baseline logic.
- Hidden TEST scores are user-provided leaderboard evidence. This repository
  cannot recompute hidden TEST metrics locally.
- The winning E3 artifact SHA256 is not known from local files.
- No current claim should describe Qwen, CHIV, C2, NSMV, or broad numeric repair
  as active implementation.

## Immediate Research Targets

- Capacity remains the weakest relation at F1 `0.1633`; improvements should
  target configuration ambiguity without adding external retrieval.
- Award remains recall-limited at F1 `0.3105`; improvements should focus on
  high-cardinality set completeness and recipient-name cleanliness.
- City has high precision but lower recall; any rescue expansion must preserve
  the empty-row precision boundary.
