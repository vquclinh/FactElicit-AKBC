# Audit 0088 - Profile E1 repository cleanup and Mistral-only runtime consolidation

## Scope

This cleanup was performed after Profile E1 implementation. It is not a
modeling experiment and does not change Profile E1 prompts, thresholds, model
identity, relation logic, output schema, or hidden-TEST predictions.

Current source HEAD before cleanup:

`5767356fac9fdb38a03063c985446ff6dd603c60`

Current development pipeline:

`configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`

Last verified hidden-TEST baseline:

`configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`

Profile D remains the last hidden-TEST-scored frozen baseline at overall F1
`0.4952`. Profile E1 is the current development pipeline and remains unscored.

## Active E1 identity

E1 is:

Profile D

+ deterministic `AwardMetadataNormalizer`

+ `MistralCityEmptyRescue` only for final-empty `personHasCityOfDeath` rows

The active neural model portfolio remains exactly one unique checkpoint:

| model | revision | unique parameters |
|---|---|---:|
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |

Competition limit: `32,000,000,000`.

Qwen is not active in E1.

## Inventory and classification

| Class | Items | Decision |
|---|---|---|
| `ACTIVE_E1_REQUIRED` | `configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`, Profile D config, Mistral runtime, `LeaderboardRepairStack`, `AwardMetadataNormalizer`, `MistralCityEmptyRescue`, readiness/probe checks, budget checks | Keep |
| `GENERIC_REUSABLE_INFRASTRUCTURE` | Core pipeline, verifier interface, Hugging Face runtime, Mistral/Tekken tokenizer, candidate signal sidecar collector, model budget audit, data integrity loaders | Keep and update misleading comments |
| `HISTORICAL_PROVENANCE` | A+Award, Profile B, Profile C, Profile C2, Profile D configs and audits, historical Mistral+Qwen calibration configs/tests | Keep but mark old B/C/C2 configs archival where they depended on retired implementation |
| `DEAD_IMPLEMENTATION` | Retired C2 L8/L9 implementation, stock guard/rescue implementation, broad border repair, broad numeric repair, C2 death recall, award witness/time recall, repair parsing helpers | Delete |
| `OBSOLETE_TEST_OR_SCRIPT` | C2 runtime behavior tests, `scripts/build_claude_diagnostic_patch.py` | Delete or replace with active-invariant tests |
| `LOCAL/GENERATED/RAW_ARTIFACT` | tracked root `tasks.jsonl` City task artifact added in commit `5767356fac9fdb38a03063c985446ff6dd603c60` | Delete from repository and add `.gitignore` rule |

## Files deleted

- `src/cover_kbc/leaderboard_repair/consistency.py`
- `src/cover_kbc/leaderboard_repair/parsing.py`
- `src/cover_kbc/leaderboard_repair/risk_guard.py`
- `scripts/build_claude_diagnostic_patch.py`
- `tasks.jsonl`

## Files simplified

- `src/cover_kbc/leaderboard_repair/relations.py`
  - Reduced to active `repair_city`, active `repair_award`, E1 prompts, E1 strict parsers, and local award dedupe.
  - Removed retired stock, border, area, capacity, old City, award witness, and time-sliced repair implementations.
- `src/cover_kbc/leaderboard_repair/stack.py`
  - Removed executable L8/L9 mutation branches.
  - Retains row coverage assertion and separate repair accounting.
- `src/cover_kbc/leaderboard_repair/util.py`
  - Retains relation constants, `candidate_signals`, and `normalize_award_metadata`.
  - Removed retired alias, stock-type, numeric-cluster, and no-border helpers.
- `tests/test_leaderboard_repair.py`
  - Replaced retired C2 behavior tests with current E1 repair invariants and archival-profile checks.

## Config cleanup

The following profiles were converted from executable retired probes to
archival provenance configs:

- `configs/experiments/cover_kbc_v3_2_profile_b_repair_core_test.yaml`
- `configs/experiments/cover_kbc_v3_2_profile_c_aggressive_recall_test.yaml`
- `configs/experiments/cover_kbc_v3_2_profile_c2_aggressive_nonstock_test.yaml`

Their historical feature blocks remain visible, but:

- `leaderboard_probe.enabled: false`
- `leaderboard_repair.enabled: false`
- `archived_runtime_status: RETIRED_IMPLEMENTATION_REMOVED_AUDIT_0088`

C2 remains explicitly `RETIRED_NEGATIVE_HIDDEN_TEST_PROBE` with hidden TEST F1
`0.4012`.

## Qwen cleanup

Active E1 and Profile D configs have no Qwen model block. Qwen remains only in:

- historical configs and audits for A+Award, Profile B/C/C2, V2/V3 calibration,
  and old diagnostics;
- readiness/probe code that compares Profile D/E1 against the older calibrated
  Mistral+Qwen provenance and reports the expected calibration-review blockers;
- generic Hugging Face/tokenizer compatibility tests that ensure historical
  Qwen configs still fail closed if someone tries to run them;
- test fixture names where `qwen` means "verifier-family fixture" in older
  module contracts.

The active runtime preflight no longer imposes the Qwen-only
`transformers>=5.2.0` floor on E1. The optional `hf` extra now uses the active
Mistral floor, `transformers>=4.52.4`; historical Qwen configs are still gated
by the model-aware preflight when they actually name Qwen.

## C2 cleanup

Retired C2 runtime code was removed. Remaining C2 references are historical:

- C2 config retained as archival provenance and disabled as a run target;
- historical audits 0083/0085/0086/0087;
- tests asserting C2 remains retired and not inherited.

No active E1 source file contains executable `BorderDirectionalSweep`,
`DeathCityRecall`, `AreaEmptyRescue`, `CapacityRepair`,
`AwardRecipientWitness`, `AwardTimeSlicedRecall`, or
`NumericAttributeResolver` implementation.

## Generated and tool-specific artifacts

- Root `tasks.jsonl` was a tracked raw City task artifact with no code
  references. Git shows it was added at
  `5767356fac9fdb38a03063c985446ff6dd603c60` as blob
  `ec2078df5761add2784621b0341297b3a50a0363`; its exact Git content has SHA256
  `f8f3f7616cfaf3e6ae4bc17cf9b7d81e185a019ec50fd975178d9536af0cbcb0`.
  It has 99 physical newline characters but 100 valid non-empty JSONL records,
  all `personHasCityOfDeath` with empty `ObjectEntities`. The first subject is
  `Ad Gerritsen`; the last subject is `Svetlana Svetlichnaya`.
- Git history proves only the repository path `tasks.jsonl`. It does not prove
  this file is the same artifact as any external Colab-local
  `/content/tasks.jsonl`; the cleanup therefore treats it only as a tracked
  repository-local raw task artifact. It was deleted from the repository and
  added to `.gitignore`.
- `scripts/build_claude_diagnostic_patch.py` was deleted. The old quarantined
  diagnostic remains documented historically, and tests still ensure no
  production module references the quarantine.
- No `CLAUDE.md`, `.claude/`, tracked model cache, prediction output, or Colab
  output was added.

## E1 semantic diff

The Profile E1 YAML file was not modified.

Resolved repair config comparison using the HEAD version of
`src/cover_kbc/leaderboard_repair/config.py` versus the cleaned version:

```json
{}
```

That comparison includes:

- repair enabled/profile/version;
- repair feature flags;
- per-relation repair caps;
- repair thresholds;
- repair artifact filenames.

The change to `RepairFeatures` defaults is a representation cleanup only for
omitted historical fields. E1 explicitly declares the relevant false flags, so
its resolved E1 semantics are unchanged.

## Validation

Focused validation already run:

```bash
python -m pyflakes src/cover_kbc/leaderboard_repair src/cover_kbc/models/preflight.py tests/test_leaderboard_repair.py tests/test_runtime_preflight.py tests/test_v3_2_weakness_mining.py
```

Result: initially found one unused import in `leaderboard_repair/util.py`; fixed.

```bash
python -m pytest tests/test_leaderboard_repair.py tests/test_profile_d_mistral_role_swap.py tests/test_profile_e1_mistral_city_rescue.py tests/test_runtime_preflight.py tests/test_v3_2_weakness_mining.py tests/test_real_model_smoke_harness.py -q -p no:randomly
```

Result: `156 passed, 7 skipped`.

```bash
python -m pytest tests/test_v3_calibration_derivation.py -q -p no:randomly
```

Result after cleanup: `7 passed, 5 skipped`. The skipped tests require the
gitignored generated corpus
`outputs/v3_supplement2_ae33b2ee_20260810T084303Z`; those tests now skip
cleanly when the local generated artifact is absent.

```bash
python -m pyflakes src/ tests/ scripts/
```

Result: pass.

```bash
git diff --check
```

Result: pass.

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `4072 passed, 21 skipped in 58.06s`.

```bash
python -m pytest tests/ -q
```

Result: `4072 passed, 21 skipped in 58.33s`.

## Post-cleanup search results

Required searches were run:

- `git grep -ni "qwen"`
- `git grep -ni "profile_c2"`
- `git grep -ni "DeathCityRecall"`
- `git grep -ni "AreaEmptyRescue"`
- `git grep -ni "CapacityRepair"`
- `git grep -ni "OpenRouter"`
- `git grep -ni "CLAUDE"`
- `git grep -ni "AwardRecipientWitness\|AwardTimeSlicedRecall\|NumericAttributeResolver\|DeathExistenceGate\|BorderDirectionalSweep"`

Summary:

- `OpenRouter`: no hits.
- C2 and retired repair names: historical audits/configs/tests only; no active
  implementation files.
- `Qwen`: historical configs/audits/tests, model-aware preflight, readiness
  blocker messages, and generic compatibility tests.
- `CLAUDE`: benchmark subjects, historical audits, quarantine firewall test,
  and analysis output fields that explicitly record `claude_diagnostic_used:
  False`.

## No predictive change statement

This cleanup removes retired implementation and clarifies active documentation.
It does not change the Profile E1 config, Mistral model id/revision, E1 prompts,
E1 strict parsers, deterministic decoding, repair caps, relation selection
settings, controller settings, output schema, or non-City mutation boundary.

No TRAIN, VAL, hidden TEST neural inference, web, RAG, external factual corpus,
fine-tuning, or new model was used.

## Commit note

Suggested commit message:

`Clean legacy runtime around Profile E1`
