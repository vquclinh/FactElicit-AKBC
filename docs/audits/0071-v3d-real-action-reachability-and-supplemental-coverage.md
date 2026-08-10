# Audit 0071 - V3D Real Action Reachability and Supplemental Coverage

## Scope

V3_REACHABILITY_BASE_SHA:

`a11d75d2420bd178ad231b5eb1e47da4e5de37a5`

No commit or push was made.

No heavyweight neural model was run locally. No full 477-row real-weight
collection was run. No M20-V3 or M21-V3 calibration artifacts were derived.

Analyzed real collection path:

`outputs/v3_train_collect_v2_coverage/`

Resolved immutable run directory:

`outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`

## Real Collection Provenance

The downloaded base run was produced from source commit
`a11d75d2420bd178ad231b5eb1e47da4e5de37a5` with policy
`collect-v2-coverage`, TRAIN rows `477`, TRAIN SHA256
`ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`,
coverage target `10`, and persisted Drive artifacts.

`POSTRUN_STATUS.json` reports:

- collection process return code: `1`
- predictions: `477 / 477`
- official evaluator return code: `0`
- train telemetry records: `338`
- inference telemetry records: `477`
- V3 pre-M8 graphs: `690`
- V3 action effects: `213`

The non-zero collection return code is correct and remains correct.

## Audit 0070 Effect

Audit 0070 fixed the generic collection starvation problem:

- `INDEPENDENT_RECALL`: legal `67`, executed `34`, successful `34`, target `10`,
  `OBSERVED_SUFFICIENT`
- `MULTI_VIEW_RECALL`, `DEFINITION_RECALL`, `ALTERNATIVE_RECALL`,
  `ATTRIBUTE_DECOMPOSITION`, and `CONTRAST_VERIFY` were also observed
  sufficiently.

The remaining failure was narrower: four Stock/Award families reached legal
catalogues but never reached selectable execution.

## Forensic Reachability

New zero-neural analyzer:

```bash
python scripts/analyze_v3_action_reachability.py \
  --collection-dir outputs/v3_train_collect_v2_coverage
```

Outputs:

- `outputs/v3_train_collect_v2_coverage/reachability_analysis/v3_action_reachability.json`
- `outputs/v3_train_collect_v2_coverage/reachability_analysis/v3_action_reachability.csv`
- `outputs/v3_train_collect_v2_coverage/reachability_analysis/v3_action_reachability.md`

Analyzer result:

| action family | legal | selectable | executed | classification |
|---|---:|---:|---:|---|
| `LISTING_ELIMINATION` | 6 | 0 | 0 | `LEGAL_EXECUTABLE_BUT_UNAFFORDABLE` x6 |
| `SEMANTIC_VERIFY` | 6 | 0 | 0 | `LEGAL_EXECUTABLE_BUT_UNAFFORDABLE` x6 |
| `SET_EXPANSION` | 10 | 0 | 0 | `LEGAL_EXECUTABLE_BUT_UNAFFORDABLE` x10 |
| `UNARY_VERIFY` | 10 | 0 | 0 | `LEGAL_EXECUTABLE_BUT_UNAFFORDABLE` x10 |

The analyzer found no missing runtime fields for these classifications.

It also found 25 hgraph-only `SEMANTIC_VERIFY` advertisements in Stock
`SEMANTIC_AMBIGUITY` states with no active primary hypothesis. These were not
coverage-counted catalogue opportunities because `build_v3_action_catalog`
dropped them, but the saved pre-M8 graph field was too broad. Source now
persists owner-precondition-filtered V3 legal families.

## Root Cause

### LISTING_ELIMINATION

Rows: `247`, `262`, `266`, `271`, `276`, `305`.

Relation: `companyTradesAtStockExchange`.

State: `HIGH_FP_RISK`.

The primary stock candidate existed on all six rows and the M17
rejection-first owner request was constructible. The action was not blocked by
ActionHistory, missing primary, selector ranking, or round limit.

Real inference telemetry showed `calls_used=5` before V3 on every row. The
effective Stock cap is `min(global 12, relation 5) = 5`; the action needs one
verifier call. Therefore it was legal and executable but not selectable:
remaining physical call budget was `0`.

### SEMANTIC_VERIFY

Rows: `247`, `262`, `266`, `271`, `276`, `305` for the coverage-counted Stock
opportunities.

Relation: `companyTradesAtStockExchange`.

State: `HIGH_FP_RISK`.

The primary candidate existed and M17 semantic verifier request construction
was possible. It competed with `LISTING_ELIMINATION` only at the legal
catalogue level; the selector never received either as selectable because
Stock had already spent all five calls. Classification:
`LEGAL_EXECUTABLE_BUT_UNAFFORDABLE`.

Separate from the six counted opportunities, the persisted hgraph advertised
`SEMANTIC_VERIFY` in 25 Stock `SEMANTIC_AMBIGUITY` rows where all hypotheses
were dropped. That was an owner-precondition mismatch in the persisted graph
surface and is now filtered before saving pre-M8 legal families.

### SET_EXPANSION

Rows: `200`-`209`.

Relation: `awardWonBy`.

State: `SET_GROWING`.

The seen set existed, M13 generation ownership was intact, and the expansion
request was constructible. It was not blocked by ActionHistory, selector
ranking, state change, or round limit.

Real inference telemetry showed `calls_used=12` before V3 on all ten rows. The
effective Award cap is `min(global 12, relation 16) = 12`; the action needs one
enumerator call and 512 planned generated tokens. Remaining generated-token
budget was sufficient, but remaining physical calls were `0`. Classification:
`LEGAL_EXECUTABLE_BUT_UNAFFORDABLE`.

### UNARY_VERIFY

Rows: `200`-`209`.

Relation: `awardWonBy`.

State: `SET_GROWING`.

The primary award candidate existed and the M17 unary score-label request was
constructible. It was not blocked by ActionHistory, selector ranking, state
change, or round limit. It was legal and executable but not selectable because
the same ten Award rows had already spent `12 / 12` physical calls before V3.

## Legal / Selectable / Executed

Coverage now distinguishes:

- `LEGAL_OPPORTUNITY`: owner/relation/state semantics produced a current
  catalogue action.
- `SELECTABLE_OPPORTUNITY`: the action was executable, affordable, not removed
  by history/redundancy, and had a constructible owner request.
- `EXECUTED_OBSERVATION`: the owner was called and produced a measured
  action-effect record or explicit execution error.

The persisted coverage table now includes:

`action_family`, `legal_opportunities`, `selectable_opportunities`, `executed`,
`successful`, `failed`, `target`, `coverage_ratio`, `status`,
`blocked_unaffordable`, `blocked_execution_precondition`, `blocked_history`,
`blocked_round_limit`, `blocked_missing_primary`, and `blocked_other`.

`LEGAL_BUT_UNDERCOVERED` remains a hard final-gate blocker.

## Source Remediation

Changed files:

- `scripts/run_train_calibration_collection.py`
- `scripts/analyze_v3_action_reachability.py`
- `scripts/merge_v3_supplemental_collection.py`
- `src/cover_kbc/controller_calibration/collection_policy.py`
- `src/cover_kbc/controller_calibration/recovery.py`
- `src/cover_kbc/controller_calibration/supplemental_coverage.py`
- `src/cover_kbc/pipeline.py`
- `tests/test_controller_calibration_collection.py`
- `tests/test_v3d_activation.py`
- this audit

Runtime changes are TRAIN-collection-only:

- V3 collection computes selectability and blocked reasons before selection.
- The selector still may choose only from legal and selectable actions.
- When every legal V3 action is unaffordable, the branch is persisted as
  legal-but-blocked telemetry instead of disappearing.
- Pre-M8 V3 graph artifacts now persist legal families after owner
  preconditions, so no-primary verifier requests are not advertised as legal.
- Supplemental mode can use an explicit collection-only V3 coverage allowance
  for targeted replay rows. Production acquisition, V2 prediction, and V3
  production M21 planning are unchanged.
- Supplemental mode applies a deterministic companion order inside target
  deficit rows: Stock runs `SEMANTIC_VERIFY` before destructive
  `LISTING_ELIMINATION`; Award runs `SET_EXPANSION` before `UNARY_VERIFY`.
  This is collection-only and never changes production relation semantics.

## Supplemental Planner

New CLI shape:

```bash
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --supplement-base outputs/v3_train_collect_v2_coverage \
  --output-dir <SUPPLEMENT_OUTPUT>
```

The planner:

1. resolves the base run directory;
2. validates base coverage/provenance shape;
3. accepts only the current four under-covered families;
4. prefers rows whose real base pre-M8 graphs reached executable catalogues;
5. replays the deterministic union of needed TRAIN rows;
6. initializes coverage from the base ledger, so supplemental replay does not
   double-count base legal opportunities;
7. filters selectable actions to active deficit families;
8. stops once target deficits are satisfied;
9. writes `v3_supplemental_plan.json` and supplement provenance;
10. never mutates the base collection.

Current real-base supplement row set:

`200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 247, 262, 266, 271, 276, 305`

Expected global deficits before supplement:

- `LISTING_ELIMINATION`: `6`
- `SEMANTIC_VERIFY`: `6`
- `SET_EXPANSION`: `10`
- `UNARY_VERIFY`: `10`

## Supplemental Executor And Checkpoint

Supplement runs use the existing checkpoint architecture. The checkpoint
identity includes the base collection identity, so resume refuses a supplement
started against a different base.

Committed coverage is loaded from the supplement output on resume. Recovery
now rebuilds legal, selectable, blocker, and executed counts from the committed
telemetry prefix after truncating any interrupted row tail. Unique supplemental
action-effect IDs are stamped as `supplement:<run_id>:<ordinal>:<row>:<base_id>`
without changing base action effects.

No Google Drive path is hardcoded; all supplement artifacts stay under
`--output-dir`.

## Merged Corpus

New merge CLI:

```bash
python scripts/merge_v3_supplemental_collection.py \
  --base outputs/v3_train_collect_v2_coverage \
  --supplement <SUPPLEMENT_RUN_OR_ROOT> \
  --output-dir <MERGED_OUTPUT>
```

The merge validates schema/provenance/TRAIN identity, rejects duplicate
action-effect IDs, writes source-tagged merged `action_effects.jsonl` and
`train_telemetry.jsonl`, writes merged `coverage.json`/`coverage.csv`, writes
`manifest.json` and `SHA256SUMS.txt`, computes a merged corpus SHA256, and runs
the calibration sufficiency validator. Calibration derivation remains blocked
unless merged coverage integrity and sufficiency both pass.

## Relation-Level Sparse Cells

Relation x action-family sparse cells are not automatically hard blockers in
this milestone. The current derivation keys M21 bins by:

`relation`, `program_type`, `state_bin`, `family`, `target_class`

and emits a relation/program/family fallback bin (`__fallback__`) for sparse
exact state bins. Exact bins below `minimum_bin_support=8` roll into fallback.
Therefore cells such as `hasCapacity / INDEPENDENT_RECALL` with at least one
executed observation can ship a relation fallback rather than block globally.

Cells with zero executed observations for a production-legal family remain
blockers because they cannot produce even a fallback observation. That is the
Stock/Award failure remediated here.

## Zero-Model Precheck

Command:

```bash
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --supplement-base outputs/v3_train_collect_v2_coverage \
  --output-dir /tmp/v3_supplement_precheck \
  --precheck-only
```

Result: `PASS`, `model calls: 0`.

The precheck recognized the four deficits, verified the required owners,
verified deterministic candidate rows, checked output writability, kept V3
production `NOT_READY`, and kept TEST blocked.

## Tests

Focused tests:

```bash
python -m pytest tests/test_controller_calibration_collection.py \
  tests/test_v3d_activation.py -q -p no:randomly
```

Result: `66 passed in 0.77s`.

Readiness/resume/sufficiency target:

```bash
python -m pytest tests/test_collection_failure_resume.py \
  tests/test_calibration_sufficiency.py \
  tests/test_controller_calibration_readiness.py -q -p no:randomly
```

Result: `94 passed in 2.39s`.

Combined focused rerun:

`160 passed in 2.52s`.

Full deterministic suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `3563 passed, 4 skipped in 51.63s`.

Full default suite:

```bash
python -m pytest tests/ -q
```

Result: `3563 passed, 4 skipped in 51.13s`.

Static checks:

```bash
python -m pyflakes src/ tests/ scripts/
git diff --check
```

Result: both passed.

One diagnostic command, `jq` against a YAML config, failed with a parse error
and was immediately rerun correctly with Python/YAML.

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

Frozen model pair:

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

## Production And V2 Non-Regression

V2 prediction path is unchanged. Historical V2 M20/M21 artifacts are
byte-identical. V3 production still routes through M21 `_plan_next_action` only
when production readiness is true. No production relation semantics were
changed. No model, model revision, benchmark data, evaluator, external data,
RAG, web path, or fine-tuning path was introduced.

## Next Real-Weight Supplement Command

Recommended Colab command using the persistent Drive base/output roots:

```bash
cd /content/FactElicit-AKBC
python -u scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --supplement-base /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v3_reachability_supplement
```

After supplement completion, merge offline:

```bash
python scripts/merge_v3_supplemental_collection.py \
  --base /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z \
  --supplement /content/drive/MyDrive/Submissions-AKBC/v3_train_collect_v3_reachability_supplement \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/merged_v3_calibration
```

Do not derive M20-V3 or M21-V3 unless the merged sufficiency gate passes.

## Verdict

PASS — V3 REAL ACTION REACHABILITY AND SUPPLEMENTAL COVERAGE READY
