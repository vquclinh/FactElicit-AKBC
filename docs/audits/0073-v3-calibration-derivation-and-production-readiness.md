# Audit 0073 - V3 Calibration Derivation And Production Readiness

## Verdict

PASS — V3 CALIBRATION DERIVATION AND PRODUCTION READINESS READY

V3 calibrated performance is NOT YET MEASURED. No TRAIN, VAL, or TEST inference
was run in this milestone.

## Source

- V3_CALIBRATION_BASE_SHA:
  `ae33b2ee4e3f437dab74cc95034958773e3cb675`
- Working-tree policy: no commit, no push.
- Execution class: CPU/offline only; no heavyweight neural model load, no web,
  no RAG, no external KB/corpus, no fine-tuning.

## Real Artifacts

Resolved and inspected local real-run artifacts:

- Base full TRAIN collection:
  `outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`
- Partial supplement:
  `outputs/v3_supplement_16ebbbf6_partial/v3_supplement_16ebbbf6_20260810T070529Z/supplement/cover_kbc_v3_train_collection_train-supplement_20260810T071043Z`
- Partial merged corpus from supplement 1:
  `outputs/v3_supplement_16ebbbf6_partial/v3_supplement_16ebbbf6_20260810T070529Z/merged_v3_calibration`
- Successful supplement 2:
  `outputs/v3_supplement2_ae33b2ee_20260810T084303Z/v3_supplement2_ae33b2ee_20260810T084303Z/supplement2/cover_kbc_v3_train_collection_train-supplement_20260810T084847Z`
- Authoritative merged calibration corpus:
  `outputs/v3_supplement2_ae33b2ee_20260810T084303Z/v3_supplement2_ae33b2ee_20260810T084303Z/merged_v3_calibration`

The authoritative merged corpus verified as:

- `merged_corpus_sha256`:
  `50be66dc88419f88ac811243244d534f0a8a160bdf2a48d02eb77295a58c33af`
- `action_effects.jsonl`: 246 committed action effects.
- `train_telemetry.jsonl`: 446 telemetry records.
- `calibration_derivation_blocked`: false.
- merged coverage integrity: true.
- final status source commit:
  `ae33b2ee4e3f437dab74cc95034958773e3cb675`.

## Global Coverage

Merged global V3 action coverage was sufficient for every required action
family:

| action family | legal | selectable | executed | successful | target | status |
|---|---:|---:|---:|---:|---:|---|
| ALTERNATIVE_RECALL | 223 | 0 | 24 | 24 | 10 | OBSERVED_SUFFICIENT |
| ATTRIBUTE_DECOMPOSITION | 128 | 0 | 50 | 50 | 10 | OBSERVED_SUFFICIENT |
| CONTRAST_VERIFY | 228 | 0 | 39 | 39 | 10 | OBSERVED_SUFFICIENT |
| DEFINITION_RECALL | 57 | 0 | 33 | 33 | 10 | OBSERVED_SUFFICIENT |
| INDEPENDENT_RECALL | 67 | 0 | 34 | 34 | 10 | OBSERVED_SUFFICIENT |
| LISTING_ELIMINATION | 6 | 20 | 6 | 6 | 6 | OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES |
| MULTI_VIEW_RECALL | 56 | 0 | 33 | 33 | 10 | OBSERVED_SUFFICIENT |
| SEMANTIC_VERIFY | 6 | 14 | 6 | 6 | 6 | OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES |
| SET_EXPANSION | 10 | 11 | 11 | 11 | 10 | OBSERVED_SUFFICIENT |
| UNARY_VERIFY | 10 | 27 | 10 | 10 | 10 | OBSERVED_SUFFICIENT |

## M20 Decision

M20 was not re-estimated from the V3 action-effect corpus. The merged V3 corpus
contains selected action observations, not a complete relation-spend sample for
all routed relations; in particular `countryLandBordersCountry` has no executed
V3 action observations. Re-estimating M20 from this corpus would fabricate a
budget for a production-routable relation.

The V3 namespace therefore ships an inherited frozen V2 M20 wrapper:

- Method: `inherited_frozen_v2_m20`.
- Source V2 artifact SHA256:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`.
- The relation budget entries are byte-equal as relation records to historical
  V2; the V3 artifact differs only by V3 provenance wrapper content.

## M21 Estimator

Derivation command:

```bash
python scripts/derive_v3_calibration.py \
  --merged-corpus outputs/v3_supplement2_ae33b2ee_20260810T084303Z \
  --output-dir configs/calibration/v3
```

M21 was derived from committed V3 action observations only. Failed transaction
tails, duplicate action-effect IDs, mismatched TRAIN identity, unsupported
families, invalid owners, and incompatible schemas are hard refusals.

Exact bin key:

`relation / program_type / state_bin / action_family / target_class`

State binning uses the existing M21 state features:

- categorical: `program_type`
- numeric: `residual`, `unresolved_mass`
- quantiles: 1/3 and 2/3
- minimum exact-bin support: 8

Each emitted bin records observed means for:

- expected verified gain;
- expected residual delta;
- expected entropy delta;
- expected physical cost;
- expected redundancy;
- expected false-positive risk;
- observed successor-state distribution where support exists.

Fallback hierarchy:

1. exact: `relation/program_type/state_bin/family/target_class`
2. relation fallback: `relation/program_type/__fallback__/family`
3. program pooled fallback: `__any_relation__/program_type/__fallback__/family`
4. global family fallback:
   `__any_relation__/__any_program__/__fallback__/family`

Pooled fallback bins are observed aggregates, not defaults. They are required
because the production-legal V3 catalogue can expose a relation/family region
that TRAIN did not execute for that exact relation state. The readiness
validator confirmed no missing production-legal calibration regions.

Planner coefficients:

- method:
  `v3_action_effect_utility_with_pooled_fallbacks_and_inert_unestimable_state_movement`
- alpha: 1.0
- beta: 0.0
- gamma: 22.634228
- delta: 1.01626
- eta: 1.01626
- kappa: 1.0
- tau_continue: 0.0
- lookahead_depth: 1

Beta is explicitly inert because the merged V3 corpus has 250.0 verified gain
but only 0.099102 total residual reduction, below the 1.0 denominator support
floor. Shipping the raw ratio would produce an unstable coefficient; lowering
the floor would weaken the Audit 0048/0049 safety rule. Gamma is estimable from
11.045219 total entropy reduction. Depth 2 is not advertised because 19 shipped
bins do not record successor statistics.

## Bin Counts

- Historical bins emitted: 50.
- Exact bins emitted: 11.
- Fallback bins emitted: 39.
- Relation fallback bins: 17.
- Program pooled fallback bins: 12.
- Global family fallback bins: 10.
- Exact bins dropped below minimum support: 14.
- Missing production-legal calibration regions: none.

Sparse exact relation/state cells are not blockers because every production
relation/family action exposed by `relation_train_collection_actions` resolves
through the fallback hierarchy. Sparse dropped exact bins are retained in
fallback aggregates; no support is fabricated.

## Generated Artifacts

Generated under the separate V3 namespace:

- `configs/calibration/v3/m20_relation_budget.json`
  - SHA256:
    `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29`
- `configs/calibration/v3/m21_historical_bins.json`
  - SHA256:
    `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675`
- `configs/calibration/v3/m21_planner_calibration.json`
  - SHA256:
    `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6`
- `configs/calibration/v3/calibration_provenance.json`
  - SHA256:
    `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d`
- `configs/calibration/v3/derivation_report.json`
  - SHA256:
    `8456c0824cb831257caf62a2b0624812fc170058788b7f954c760a5ddda2394d`
- `configs/calibration/v3/derivation_report.md`
  - SHA256:
    `1c397bf03938765c1f79318a7a041bd1f2880611db88e78ac08f5c9cb8935b95`
- `configs/calibration/v3/SHA256SUMS.txt`

The provenance artifact records schema version, current source SHA, TRAIN and
evaluator hashes, merged corpus SHA, base and supplement identities, source
telemetry schemas, action policy version, M20/M21 derivation methods, minimum
bin support, fallback hierarchy, output hashes, and inherited V2 M20 source
hash.

## Production Config And Readiness

Created:

- `configs/experiments/cover_kbc_v3_production.yaml`

The config is TRAIN diagnostic only. It references only V3 calibration
artifacts, enables M20 and M21 in production mode, sets
`pipeline.v3_core.mode: production`, declares
`production_calibration_ready: true`, and contains no `train_collection` block.

Zero-model readiness result:

- TRAIN: `TRAIN_DIAGNOSTIC_READY`
- TRAIN blockers: none
- TEST: `NOT_READY`
- TEST blockers: the TRAIN diagnostic config does not declare test dataset rows,
  test SHA256, or test identity. This is intentional; this milestone does not
  make TEST ready.

Production/collection separation:

- Supplemental coverage allowance: disabled in production config.
- Companion scheduling: absent from production config.
- Collection deficit prioritization: absent from production config.
- `collect-v2-coverage-r2`: not the production planner.
- M21 is the production decision-maker.
- M20 physical budgets are inherited from frozen V2 and unchanged.
- V3 production passes already executed V3 action identities into M21 so
  non-repeatable action protection is active.
- SET_EXPANSION repeatability remains governed by existing V3 action
  descriptors; production relation semantics were not changed for calibration.

## V2 Non-Regression

Historical V2 calibration artifacts remain byte-identical:

- `configs/calibration/m20_relation_budget.json`
  - `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- `configs/calibration/m21_historical_bins.json`
  - `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- `configs/calibration/m21_planner_calibration.json`
  - `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

The V2 prediction path remains unchanged. Shared fallback lookup now supports
optional pooled V3 bins, but historical V2 artifacts do not contain those keys,
so their lookup behavior remains exact then relation fallback. The production
loader still loads V2 artifacts when a V2 config points at them; V3 readiness
separately refuses V2 paths for V3 production.

## Immutable Hashes

- `benchmark/evaluate.py`:
  `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN rows: 477
  - `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- VAL rows: 475
  - `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- TEST rows: 475
  - `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`

Model pair:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  - revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- `Qwen/Qwen3.5-4B`
  - revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- total: `28,671,226,368 / 32,000,000,000`

## Tests

Focused:

- `python -m pytest tests/test_v3_calibration_derivation.py -q`
  - 12 passed
- `python -m pytest tests/test_v3_calibration_derivation.py tests/test_v3d_activation.py::test_v3_collection_artifact_schema_paths_are_not_fake_calibration -q`
  - 13 passed

Full:

- `python -m pytest tests/ -q -p no:randomly`
  - 3580 passed, 4 skipped
- `python -m pytest tests/ -q`
  - 3580 passed, 4 skipped

Static:

- `python -m pyflakes src/ tests/ scripts/`
  - passed
- `git diff --check`
  - passed

New regression coverage includes deterministic V3 derivation, wrong corpus SHA
rejection, insufficient/duplicate/uncommitted action-effect rejection, sparse
fallback resolution, zero-observation production-region rejection without
fallback, V2 calibration byte identity, V3 production loading only V3 artifacts,
collection-mode refusal for production, TEST blocking when readiness is false,
no VAL/TEST reads during derivation, and repeated byte-identical derivation.

## Next Real-Weight Command

The next GPU milestone is a full TRAIN diagnostic for the calibrated V3
production path, not VAL or TEST:

```bash
python -u scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_production.yaml \
  --split train \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/v3_train_diagnostic_ae33b2ee
```

Do not use this command for TEST. V3 calibrated TRAIN performance remains
unknown until that future diagnostic run completes.
