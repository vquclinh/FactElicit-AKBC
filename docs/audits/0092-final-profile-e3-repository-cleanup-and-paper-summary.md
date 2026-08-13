# Audit 0092 - Final Profile E3 Repository Cleanup and Paper Summary

Date: 2026-08-13

## Starting State

Starting HEAD:

`b94f0089b261ff98027bcdaa7d2dd9027191e10d`

Pre-cleanup `git status --short`:

```text
 M README.md
 M configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml
 M configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml
 M docs/IMPLEMENTATION_STATUS.md
 M src/cover_kbc/leaderboard_repair/__init__.py
 M src/cover_kbc/leaderboard_repair/config.py
 M src/cover_kbc/leaderboard_repair/types.py
 M tests/test_profile_d_mistral_role_swap.py
?? docs/PAPER_SYSTEM_SUMMARY.md
```

Pre-cleanup `git diff --check`: clean.

Repository inventory inspected before cleanup:

- 426 tracked files
- 146 tracked files under `src/`
- 84 tracked test files
- 33 tracked scripts
- 33 tracked experiment configs
- README, docs, audits, configs, scripts, tests, notebooks, package metadata,
  `.gitignore`, benchmark snapshot, and current working tree diff

No commit or push was made. No hidden TEST neural inference was run.

## Canonical Current Profile

Current best config:

`configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`

Status:

- Profile E3 - Mistral Area Multi-View
- Profile E2 + `MistralAreaMultiView`
- hidden TEST overall F1 0.5857, user-provided
- canonical E3 config semantics were not changed by this cleanup

## Repository Classification

### A. ACTIVE_PRODUCTION_REQUIRED

Required by current Profile E3 runtime:

- `configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml`
- `scripts/run_cover.py`
- `src/cover_kbc/pipeline.py`
- `src/cover_kbc/contracts/`
- `src/cover_kbc/elicitation/`
- `src/cover_kbc/evidence/`
- `src/cover_kbc/verification.py`
- `src/cover_kbc/verification/`
- `src/cover_kbc/scoring.py`
- `src/cover_kbc/coverage.py`
- `src/cover_kbc/controller.py`
- `src/cover_kbc/control/`
- `src/cover_kbc/v3_core/`
- `src/cover_kbc/selection.py`
- `src/cover_kbc/models/`
- `src/cover_kbc/runtime/`
- `src/cover_kbc/data/`
- `src/cover_kbc/leaderboard_repair/`
- calibration artifacts referenced by the E3 config

### B. GENERIC_REUSABLE_INFRASTRUCTURE

Generic infrastructure genuinely used for reproducibility and validation:

- `benchmark/evaluate.py` and read-only benchmark snapshot
- `scripts/audit_model_budget.py`
- `scripts/run_area_multiview.py`
- `scripts/run_capacity_multiview.py`
- `scripts/merge_targeted_relation_results.py`
- `scripts/evaluate_local.py`
- `scripts/package_submission.py`
- smoke configs and scripted runtimes
- tests for contracts, parsing, evidence, output schema, package/merge
  invariants, model budget, one-runtime reuse, and no-Qwen active profile
- `pyproject.toml`
- `.gitignore`
- current Colab/runtime notebooks after rewrite

### C. HISTORICAL_PROVENANCE_ONLY

Retained for experiment history, score provenance, or historical invariant
tests:

- `docs/audits/0001` through `0091`
- `docs/runbooks/`
- superseded Profile D/E1/E2 configs
- historical v2/v3 Qwen configs
- Profile A/B/C/C2 configs explicitly marked as historical/superseded/retired
- historical direct-area and city-rescue tests
- runtime compatibility tests for archived Qwen profiles
- diagnostic scripts such as capacity/stock/v3 weakness analyzers

### D. DEAD_IMPLEMENTATION

No tracked source file qualified for deletion in this pass. The active source
search found no executable Profile E3 branch for:

- `DeathCityRecall`
- `AreaEmptyRescue`
- `CapacityRepair`
- `NumericAttributeResolver`
- `AwardRecipientWitness`
- `AwardTimeSlicedRecall`
- `BorderDirectionalSweep`
- `CHIV`
- `NSMV`
- `Neighborhood`
- `profile_c2`

Retired feature flags remain parseable in config dataclasses only so historical
configs can be loaded and tested.

### E. OBSOLETE_TEST_OR_SCRIPT

No tracked tests or scripts were removed. Existing historical tests still assert
important invariants: retired profiles stay marked archival, Qwen remains absent
from E3, and retired repair classes are absent from the active repair package.
Current script discoverability was improved instead of deleting provenance
tools.

### F. GENERATED_OR_LOCAL_ARTIFACT

Ignored/generated artifacts observed:

- `.pytest_cache/`
- `__pycache__/` trees
- `outputs/`
- `full_collect.log`
- local model/cache/checkpoint patterns covered by `.gitignore`

No tracked generated artifact was found by `git ls-files` checks. Generated
artifacts were not committed.

## Runtime Graph Provenance

Entry point:

`scripts/run_cover.py`

Call graph:

```text
run_cover.main
  -> yaml.safe_load(config)
  -> model_blocks(config)
  -> require_huggingface_runtime(enumerator, verifier)
  -> build_runtime(enumerator)
  -> verifier_runtime = runtime when configs are identical
  -> CoverPipeline(...)
  -> CoverPipeline.run(queries)
  -> build_repair_stack(config["leaderboard_repair"], runtime, runtime)
  -> LeaderboardRepairStack.apply(...)
  -> write predictions.jsonl / trace.jsonl / calls and repair sidecars
```

Active final-layer map:

| Relation | ACTIVE module/function | Notes |
|---|---|---|
| `awardWonBy` | `repair_award` | deterministic metadata cleanup, no model calls |
| `personHasCityOfDeath` | `repair_city` | empty-only Mistral rescue, max 2 calls |
| `hasArea` | `repair_area` -> `repair_area_multiview` | four views plus optional judge, max 5 calls |
| `hasCapacity` | `repair_capacity` | four views plus optional judge, max 5 calls |
| `companyTradesAtStockExchange` | core selector only | repair cap 0 |
| `countryLandBordersCountry` | core selector only | repair cap 0 |

Reachable but not final-answer-mutating unless explicitly wired:

- shadow query intelligence
- shadow specialists
- shadow consensus/verifier diagnostics
- V3 telemetry sidecars

## Model Portfolio

Active unique checkpoint:

- model id: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- revision: `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- published parameters: 24,011,361,280
- budget: 32,000,000,000
- quantization: `nf4`, not counted as parameter reduction

Model inspection output:

```text
same_runtime_config: True
enumerator_model_id: mistralai/Mistral-Small-3.2-24B-Instruct-2506
verifier_model_id: mistralai/Mistral-Small-3.2-24B-Instruct-2506
revision: 95a6d26c4bfb886c58daf9d3f7332c857cb27b43
qwen_in_model_profile: False
budget_passed: True
budget_total: 24011361280
budget_limit: 32000000000
```

Budget audit result:

```text
RESULT: PASS
total: 24.01B
```

## Current Scores

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

Zero-object reference: P 0.5963, R 0.9412, F1 0.7300.

## Files Changed or Simplified

Documentation:

- `README.md` rewritten around current Profile E3
- `docs/IMPLEMENTATION_STATUS.md` rewritten as concise source of truth
- `docs/PAPER_SYSTEM_SUMMARY.md` created as the full paper-ready summary
- this audit created as `docs/audits/0092-final-profile-e3-repository-cleanup-and-paper-summary.md`

Notebooks:

- `notebooks/COVER_KBC_Colab.ipynb` replaced with a Profile E3 one-model driver
- `notebooks/COVER_KBC_PostArchitecture_RealModel_Smoke.ipynb` config updated
  to the E3 config

Current-source terminology cleanup:

- `src/cover_kbc/models/preflight.py`
- `src/cover_kbc/leaderboard_repair/__init__.py`
- `src/cover_kbc/leaderboard_repair/config.py`
- `src/cover_kbc/leaderboard_repair/relations.py`
- `src/cover_kbc/leaderboard_repair/runtime.py`
- `src/cover_kbc/leaderboard_repair/stack.py`
- `src/cover_kbc/leaderboard_repair/types.py`
- `scripts/run_cover.py`
- `scripts/run_capacity_multiview.py`
- `scripts/audit_model_budget.py`
- `scripts/real_model_smoke.py`
- `tests/test_runtime_preflight.py`
- `pyproject.toml`

Pre-existing worktree changes retained:

- Profile D config metadata already pointed current baseline to E3
- Integrated E1 config metadata already mentioned E3 supersession
- Profile D test already expected E3 as the current frozen baseline

## Files Deleted

No tracked files were deleted. The cleanup found no tracked dead Profile E3
runtime implementation requiring deletion. Ignored generated artifacts were left
ignored and untracked.

## Config Changes

The canonical E3 config was not changed. Historical Profile D/E1 metadata edits
were present before this cleanup and retained.

## Dependencies

No dependency was removed or added. `pyproject.toml` terminology was updated so
the Mistral-only dependency floor refers to the current E-profile line.

## Tests and Scripts Removed

No tests removed. No scripts removed. Current script docs/examples now point at
E3 where the utility is current; historical scripts remain classified as
provenance or generic tooling.

## Semantic Preservation Gate

The cleanup intentionally did not change Profile E3 predictions.

Preserved:

- canonical E3 config path
- model id/revision
- one-runtime reuse
- no active Qwen
- Area prompts/logic/parser/5% threshold/clustering/judge/fallback
- Capacity prompts/logic/parser/5% threshold/clustering/judge/fallback
- City empty-only trigger, life/death gate, strict city parser
- Award deterministic metadata normalization
- Stock core path with no repair
- Border core path with no repair
- relation routing
- output schema

Changed only:

- documentation
- notebook commands
- script examples/docstrings
- repair log label text from `[L7-L9]` to `[repair]`
- default repair-version metadata when no explicit repair version is supplied

The E3 config supplies an explicit repair version, so that default metadata
change does not alter current E3 behavior.

## Post-Cleanup Search Results

Required searches run:

- `qwen`
- `Qwen`
- `CHIV`
- `C2`
- `profile_c2`
- `NSMV`
- `Neighborhood`
- `DeathCityRecall`
- `AreaEmptyRescue`
- `CapacityRepair`
- `NumericAttributeResolver`
- `AwardRecipientWitness`
- `AwardTimeSlicedRecall`
- `BorderDirectionalSweep`
- `OpenRouter`
- `CLAUDE`
- `claude`
- `Profile A`
- `Profile B`
- `Profile C`
- `Profile D`
- `Profile E1`
- `Profile E2`
- `Profile E3`

Classification of remaining hits:

- Qwen hits: historical configs, historical tests, runtime compatibility
  preflight/tokenizer code, `run_cover.py` historical accepted-readiness branch,
  E3 config `qwen_runtime_calls: 0`, and targeted-runner no-Qwen guards.
- C2/CHIV/NSMV/Neighborhood hits: current docs explaining inactive status,
  historical configs/audits, and tests proving retired code is absent.
- DeathCityRecall/AreaEmptyRescue/CapacityRepair/NumericAttributeResolver/
  AwardRecipientWitness/AwardTimeSlicedRecall/BorderDirectionalSweep hits:
  historical docs/tests only; active source search returned no hits.
- Claude/OpenRouter hits: quarantined diagnostic history and tests only; no
  production entrypoint references the quarantine.
- Profile A/B/C/D/E1/E2/E3 hits: expected profile lineage in docs, tests, and
  configs. E3 is now the visible current profile.
- Remaining `L7-L9` hit: one historical Profile B config comment only.

Focused active-source retired-implementation search:

```text
rg -n "DeathCityRecall|AreaEmptyRescue|CapacityRepair|NumericAttributeResolver|AwardRecipientWitness|AwardTimeSlicedRecall|BorderDirectionalSweep|CHIV|NSMV|Neighborhood|profile_c2" src/cover_kbc scripts/run_cover.py scripts/run_area_multiview.py scripts/run_capacity_multiview.py scripts/merge_targeted_relation_results.py
```

Result: no matches.

## Artifact / Generated File Status

`git status --short --ignored` shows ignored local artifacts:

- `.pytest_cache/`
- `benchmark/__pycache__/`
- `outputs/`
- `full_collect.log`
- script/source/test `__pycache__/` trees

They are ignored by `.gitignore` and are not tracked. The current E3 winning
artifact SHA256 is not known locally.

## Validation

Focused validation:

```text
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml
RESULT: PASS

python scripts/run_area_multiview.py --config configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml --split test --output-dir outputs/e3_area_multiview_dry_run --dry-run
dry_run: outputs/e3_area_multiview_dry_run/area_multiview_dry_run.json

python -m pytest tests/test_profile_e3_area_multiview.py tests/test_profile_e2_capacity_multiview.py tests/test_profile_e1_mistral_city_rescue.py tests/test_profile_e1_direct_area_baseline.py tests/test_profile_d_mistral_role_swap.py tests/test_leaderboard_repair.py tests/test_runtime_preflight.py -q
104 passed

python -m pytest tests/test_data.py tests/test_evaluation.py tests/test_contracts.py tests/test_elicitation.py tests/test_evidence.py tests/test_graph.py tests/test_final_selector_conformance.py tests/test_package_submission.py tests/test_official_test_activation.py tests/test_production_source_fixes.py tests/test_pipeline.py tests/test_pipeline_production_seam.py tests/test_mistral_runtime_compat.py -q
502 passed, 1 skipped

python -m pytest tests/test_leaderboard_repair.py tests/test_profile_e3_area_multiview.py -q
24 passed
```

Static validation:

```text
python -m pyflakes src/ tests/ scripts/
PASS

git diff --check
PASS

python -m json.tool notebooks/COVER_KBC_Colab.ipynb
PASS

python -m json.tool notebooks/COVER_KBC_PostArchitecture_RealModel_Smoke.ipynb
PASS
```

Full validation:

```text
python -m pytest tests/ -q -p no:randomly
4110 passed, 21 skipped in 48.26s

python -m pytest tests/ -q
4110 passed, 21 skipped in 47.87s
```

## Final Statement

No predictive Profile E3 behavior was intentionally changed. No hidden TEST
neural inference was run. The repository now has a current-first README, a
concise engineering implementation status, and a comprehensive paper-ready
system summary for Profile E3.
