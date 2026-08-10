# Audit 0072 - V3D Supplement Duplicate-Evidence Recovery

## Scope

V3_SUPPLEMENT_DUPLICATE_BASE_SHA:

`16ebbbf646c0924e825ff95dae96a0d74320ecc1`

No commit or push was made.

No heavyweight neural model was run locally. No full 477-row TRAIN collection
was run. No M20-V3 or M21-V3 calibration artifacts were derived.

Base collection:

`outputs/v3_train_collect_v2_coverage/`

Partial supplement:

`outputs/v3_supplement_16ebbbf6_partial/v3_supplement_16ebbbf6_20260810T070529Z/supplement/cover_kbc_v3_train_collection_train-supplement_20260810T071043Z`

The partial supplement was produced from source commit
`16ebbbf646c0924e825ff95dae96a0d74320ecc1` with run id
`cover_kbc_v3_train_collection_train-supplement_20260810T071043Z`.

## Real Partial Result

The supplement targeted 16 TRAIN rows and committed 9 rows:

`200, 201, 202, 203, 204, 205, 209, 247, 271`

Seven rows failed unresolved:

`206, 207, 208, 262, 266, 276, 305`

The checkpoint reports `failed_attempts=7`, `unresolved_failed_rows=7`, and
`failed_attempt_calls=139`.

The committed telemetry contains 18 executed supplemental observations. The raw
`v3_action_effects.jsonl` contains 25 effect rows; the seven extra rows are
failed-row transaction tails and are not committed calibration observations.

## Duplicate Root Cause

The failure was a true duplicate execution of the same non-repeatable V3
verifier action, not an idempotent graph replay.

First insertion path:

`CoverPipeline._run_v3_control_loop` round 1 ->
`_execute_v3_action_record` -> `execute_v3_action` ->
`_execute_verifier_action` -> `_apply_unary_result` ->
`EvidenceGraph.add_verification` -> `EvidenceGraph._attach`

Second insertion path:

`CoverPipeline._run_v3_control_loop` round 2 ->
`_execute_v3_action_record` -> `execute_v3_action` ->
`_execute_verifier_action` -> `_apply_unary_result` ->
`EvidenceGraph.add_verification` -> `EvidenceGraph._attach`

The collection policy still had a positive global deficit for `UNARY_VERIFY` or
`SEMANTIC_VERIFY`. `ActionHistory` only made a family redundant after a
non-material execution. A `VALID` verifier result was material, so the exact
same non-repeatable action identity stayed selectable in the next round. The
V3 request id is deterministic, so the second execution reused the same
`v3ver:*` record id and derived the same evidence edge id.

`EvidenceGraph` was correct to raise. The fix is at the source of the duplicate
execution, not by weakening graph insertion.

## Failed Rows

| row | relation | subject | family | target | record id | duplicate edge |
|---:|---|---|---|---|---|---|
| 206 | `awardWonBy` | `Time Person of the Year` | `UNARY_VERIFY` | `elon musk` | `v3ver:516ced73db2599ad` | `7f52aa44e3761208` |
| 207 | `awardWonBy` | `UEFA Team of the Year` | `UNARY_VERIFY` | `kevin de bruyne` | `v3ver:4bd1bbeedcc86acc` | `fda219dca6c173c2` |
| 208 | `awardWonBy` | `Fields medal` | `UNARY_VERIFY` | `laurent lafforgue` | `v3ver:1f07b6235b32235e` | `3aeee00d7301153f` |
| 262 | `companyTradesAtStockExchange` | `Korean Air` | `SEMANTIC_VERIFY` | `korea exchange` | `v3ver:53af38a59e8305a2` | `7419af9b2f399602` |
| 266 | `companyTradesAtStockExchange` | `IDP Education` | `SEMANTIC_VERIFY` | `target idp education company` | `v3ver:3fc25d7e2e9f4aba` | `c2e78a48e8bbb7bf` |
| 276 | `companyTradesAtStockExchange` | `Nautilus, Inc.` | `SEMANTIC_VERIFY` | `nyse american` | `v3ver:3d6ea07f213275b4` | `357b379681495177` |
| 305 | `companyTradesAtStockExchange` | `Prysmian Group` | `SEMANTIC_VERIFY` | `borsa italiana` | `v3ver:8570295f55b35cea` | `c96792dab131ad9f` |

For every failed row, the first effect was persisted in the raw action-effect
tail with `verifier_outcome=VALID` and `candidates_touched` containing the
target candidate. The second effect did not persist because `_attach` raised
before the action record completed. The first edge payload is persisted; the
second probability payload is not, so exact byte equality of the two payloads
is not recoverable from artifacts. The source path and deterministic request id
prove the same semantic owner request was executed twice.

## Successful Row Comparison

Rows `209`, `247`, and `271` also repeated the same V3 verifier action id and
request id. They did not raise because `EvidenceGraph.add_verification` found
no live graph candidate for the V3 target and returned `None`; the repeated
effects had `candidates_touched=[]`, so no duplicate edge was attached.

This explains why only the seven listed rows crashed: their first verifier
pass attached a live edge, and the second pass attempted the same edge again.

## Source Remediation

Changed files:

- `src/cover_kbc/v3_core/relation_programs.py`
- `src/cover_kbc/pipeline.py`
- `src/cover_kbc/controller_calibration/collection_policy.py`
- `src/cover_kbc/controller_calibration/supplemental_coverage.py`
- `scripts/run_train_calibration_collection.py`
- `scripts/merge_v3_supplemental_collection.py`
- `tests/test_v3d_activation.py`
- `tests/test_controller_calibration_collection.py`
- this audit

`ActionHistory` now records exact action identities in addition to
failure-state/family novelty. In V3 TRAIN collection only, the V3 loop marks a
non-repeatable action as `blocked_history:
exact_non_repeatable_action_already_executed` if the same action identity has
already executed for the query. The action remains legal for reporting, but it
is not selectable and cannot call the owner a second time.

`SET_EXPANSION` remains repeatable. Production V3 M21 planning is unchanged.
V2 is unchanged.

`COLLECTION_POLICY_VERSION` is now `collect-v2-coverage-r2`, so checkpoint
resume refuses incompatible old source-state checkpoints.

## Graph Invariant

The `EvidenceGraph` duplicate invariant remains strict:

- same edge identity from a second ordinary insertion: hard error;
- conflicting payload with the same identity: hard error;
- no broad `try/except ValueError` suppression was added.

This milestone did not introduce graph-level idempotent insertion because the
real bug was duplicate action execution, not replay/rebuild idempotence.

## Partial Supplement Preservation

The raw partial supplement was not modified.

Committed artifact truth from `BASE + PARTIAL_SUPPLEMENT_1`:

| family | legal | executed | successful | target | remaining deficit |
|---|---:|---:|---:|---:|---:|
| `LISTING_ELIMINATION` | 6 | 0 | 0 | 6 | 6 |
| `SEMANTIC_VERIFY` | 6 | 4 | 4 | 6 | 2 |
| `SET_EXPANSION` | 10 | 11 | 11 | 10 | 0 |
| `UNARY_VERIFY` | 10 | 3 | 3 | 10 | 7 |

`committed_action_effects()` keeps 18 partial supplement effects and identifies
7 uncommitted failed-row effect tails. Future runner output also writes only
effects backed by committed executed telemetry.

## Continuation Plan

Zero-model planner input:

`BASE + PARTIAL_SUPPLEMENT_1`

Remaining global deficits:

- `LISTING_ELIMINATION`: 6
- `SEMANTIC_VERIFY`: 2
- `UNARY_VERIFY`: 7
- `SET_EXPANSION`: 0

The continuation planner chooses 13 TRAIN rows:

`200, 201, 202, 204, 206, 207, 208, 247, 262, 266, 271, 276, 305`

Row/family targets:

| row | families |
|---:|---|
| 200 | `UNARY_VERIFY` |
| 201 | `UNARY_VERIFY` |
| 202 | `UNARY_VERIFY` |
| 204 | `UNARY_VERIFY` |
| 206 | `UNARY_VERIFY` |
| 207 | `UNARY_VERIFY` |
| 208 | `UNARY_VERIFY` |
| 247 | `LISTING_ELIMINATION` |
| 262 | `SEMANTIC_VERIFY`, `LISTING_ELIMINATION` |
| 266 | `SEMANTIC_VERIFY`, `LISTING_ELIMINATION` |
| 271 | `LISTING_ELIMINATION` |
| 276 | `LISTING_ELIMINATION` |
| 305 | `LISTING_ELIMINATION` |

`SET_EXPANSION` is not targeted again. Stock rows that still need both families
preserve Audit 0071's collection-only companion order:
`SEMANTIC_VERIFY` before destructive `LISTING_ELIMINATION`.

## Chained Merge

Merge now accepts ordered repeated supplements:

```bash
python scripts/merge_v3_supplemental_collection.py \
  --base <BASE> \
  --supplement <PARTIAL_SUPPLEMENT_1> \
  --supplement <SUPPLEMENT_2> \
  --output-dir <MERGED>
```

The merge:

- validates TRAIN identity and declared base identity;
- rejects duplicate action-effect ids;
- filters supplement effects to committed executed telemetry;
- reports ignored uncommitted effect tails;
- preserves source tags;
- writes deterministic coverage, telemetry, manifest and `SHA256SUMS.txt`;
- runs final calibration sufficiency on the merged telemetry;
- leaves calibration derivation blocked unless merged coverage and sufficiency
  both pass.

An offline real partial merge smoke produced
`merged_corpus_sha256=9686ef8721d04ad73c84065c347638f0660522b8bce74fb788f69de74c6645d8`
and `calibration_derivation_blocked=true`, as expected.

## Checkpoint And Source-Version Safety

Supplement continuation should not resume the old partial checkpoint. The safe
workflow is:

`BASE + immutable PARTIAL_SUPPLEMENT_1 + new SUPPLEMENT_2 from fixed source`.

The runner identity includes `collect-v2-coverage-r2`, base collection identity,
and prior supplement identities. A stale checkpoint produced by the previous
policy/source state is refused rather than resumed silently.

Resume of a fixed supplement preserves completed rows, committed coverage,
unique supplemental action ids, target deficits, row replay state and physical
accounting through the existing checkpoint boundary.

## Zero-Model Precheck

Command:

```bash
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --supplement-base outputs/v3_train_collect_v2_coverage \
  --prior-supplement outputs/v3_supplement_16ebbbf6_partial/v3_supplement_16ebbbf6_20260810T070529Z/supplement/cover_kbc_v3_train_collection_train-supplement_20260810T071043Z \
  --output-dir /tmp/v3_supplement_continuation_precheck \
  --precheck-only
```

Result: `PASS`, `model calls: 0`.

It recognized the three remaining deficits, selected the 13 continuation rows,
verified required M17 owners, verified output writability, kept production V3
`NOT_READY`, and kept TEST blocked.

## Tests

Focused duplicate tests:

```bash
python -m pytest tests/test_v3d_activation.py::test_award_unary_verify_is_not_reexecuted_with_same_edge_identity tests/test_v3d_activation.py::test_stock_semantic_verify_is_not_reexecuted_with_same_edge_identity -q -p no:randomly
```

Result: `2 passed in 1.13s`.

Focused supplement tests:

```bash
python -m pytest tests/test_controller_calibration_collection.py::test_continuation_planner_uses_prior_supplement_deficits tests/test_controller_calibration_collection.py::test_real_partial_supplement_continuation_plan_matches_artifacts tests/test_controller_calibration_collection.py::test_chained_supplemental_merge_filters_uncommitted_effect_tails -q -p no:randomly
```

Result: `3 passed in 0.78s`.

Targeted collection/V3D:

```bash
python -m pytest tests/test_controller_calibration_collection.py tests/test_v3d_activation.py -q -p no:randomly
```

Result: `71 passed in 1.84s`.

Readiness/resume/sufficiency:

```bash
python -m pytest tests/test_collection_failure_resume.py tests/test_calibration_sufficiency.py tests/test_controller_calibration_readiness.py -q -p no:randomly
```

Result: `94 passed in 5.56s`.

Full deterministic suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `3568 passed, 4 skipped in 65.98s`.

Full default suite:

```bash
python -m pytest tests/ -q
```

Result: `3568 passed, 4 skipped in 65.53s`.

Static checks:

```bash
python -m pyflakes src/ tests/ scripts/
git diff --check
```

Result: both passed.

## Immutable Checks

- `benchmark/evaluate.py` SHA256:
  `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN rows: `477`
- TRAIN SHA256:
  `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- VAL rows: `475`
- VAL SHA256:
  `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- TEST rows: `475`
- TEST SHA256:
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`

Frozen model pair remains:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- `Qwen/Qwen3.5-4B`
  revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- Total: `28,671,226,368 / 32,000,000,000`

Historical V2 calibration hashes remain byte-identical:

- `configs/calibration/m20_relation_budget.json`:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- `configs/calibration/m21_historical_bins.json`:
  `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- `configs/calibration/m21_planner_calibration.json`:
  `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

No evaluator, benchmark data, V2 calibration artifact, model id, model
revision, parameter budget, external data, RAG path, web lookup, or fine-tuning
path changed.

## Next Real-Weight Supplement Command

Recommended Colab command with persistent Drive paths:

```bash
cd /content/FactElicit-AKBC
python -u scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --supplement-base /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z \
  --prior-supplement /content/drive/MyDrive/Submissions-AKBC/v3_supplement_16ebbbf6_20260810T070529Z/supplement/cover_kbc_v3_train_collection_train-supplement_20260810T071043Z \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/v3_supplement_2_duplicate_recovery
```

After `SUPPLEMENT_2` completes, merge:

```bash
python scripts/merge_v3_supplemental_collection.py \
  --base /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z \
  --supplement /content/drive/MyDrive/Submissions-AKBC/v3_supplement_16ebbbf6_20260810T070529Z/supplement/cover_kbc_v3_train_collection_train-supplement_20260810T071043Z \
  --supplement /content/drive/MyDrive/Submissions-AKBC/v3_supplement_2_duplicate_recovery/<NEW_RUN_ID> \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/merged_v3_calibration
```

Do not derive M20-V3 or M21-V3 unless the merged sufficiency and coverage gates
pass.

## Verdict

PASS — V3 SUPPLEMENT DUPLICATE-EVIDENCE RECOVERY READY
