# Audit 0074 - V3 TEST Production Activation

## Verdict

PASS — V3 TEST PRODUCTION ACTIVATION READY

No TEST inference was performed in Audit 0074. No TRAIN or VAL inference was
performed. No V3 calibration artifact was modified or re-derived.

## Source

- Source HEAD:
  `3780bb7a6d6b0680b70046cafbd4cccde012b73e`
- HEAD commit:
  `3780bb7 derive V3 calibration and enable production readiness`
- Working-tree policy: no commit, no push.
- Execution class: CPU/offline only; no heavyweight model load, no web, no RAG,
  no external data, no fine-tuning.

## TEST Config

Created a separate TEST production config:

- `configs/experiments/cover_kbc_v3_test.yaml`

The TRAIN diagnostic config remains separate:

- `configs/experiments/cover_kbc_v3_production.yaml`

The TEST config:

- declares `experiment.split: test`;
- declares `pipeline.v3_core.mode: production`;
- enables M20 and M21 in `mode: production`;
- points only at `configs/calibration/v3/*`;
- contains no `train_collection` block;
- contains no supplemental coverage policy;
- contains no deficit prioritization;
- contains no companion coverage scheduling;
- contains no collection allowance.

## TEST Identity

Repository TEST identity verified from `benchmark/data/test.jsonl`:

- rows: 475
- SHA256:
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`
- ordered identity:
  `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`
- rows with non-empty `ObjectEntities`: 0
- row schema: `SubjectEntity`, `Relation`, `ObjectEntities`

The TEST config declares the same row count, byte hash, ordered identity, and
blindness flag. The TEST readiness gate fails closed if any of these differ.

## Calibration Identity

Audit 0073 V3 calibration artifacts remain byte-identical:

- `configs/calibration/v3/m20_relation_budget.json`
  - `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29`
- `configs/calibration/v3/m21_historical_bins.json`
  - `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675`
- `configs/calibration/v3/m21_planner_calibration.json`
  - `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6`
- `configs/calibration/v3/calibration_provenance.json`
  - `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d`
- `configs/calibration/v3/derivation_report.json`
  - `8456c0824cb831257caf62a2b0624812fc170058788b7f954c760a5ddda2394d`
- `configs/calibration/v3/derivation_report.md`
  - `1c397bf03938765c1f79318a7a041bd1f2880611db88e78ac08f5c9cb8935b95`
- `configs/calibration/v3/SHA256SUMS.txt`
  - `6349a116a932007288eba53e24e33045123c8ce62455bc836876ac0d9c6853f5`

Calibration corpus identity:

- `merged_corpus_sha256`:
  `50be66dc88419f88ac811243244d534f0a8a160bdf2a48d02eb77295a58c33af`

The TEST config pins the three production artifact hashes and expects the
Audit 0073 merged-corpus identity through V3 calibration provenance.

## Readiness

Zero-model TEST readiness gate result:

- state: `FULL_TEST_READY`
- blockers: none
- missing production-legal calibration regions: 0
- TEST rows: 475
- TEST SHA256:
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`
- TEST ordered identity:
  `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`
- TEST rows with `ObjectEntities`: 0
- production mode:
  `v3_core=production`, `m20=production`, `m21=production`
- collection disabled: true

The readiness validator confirms:

- V3 calibration provenance is valid.
- All V3 calibration artifact hashes match.
- The calibration source merged-corpus identity matches Audit 0073.
- Production mode is active.
- Collection mode is disabled.
- Model pair is unchanged and legal.
- Parameter total remains `28,671,226,368 / 32,000,000,000`.
- TEST has exactly 475 rows.
- TEST SHA256 and ordered identity match.
- TEST is blind.
- No production-legal V3 calibration region is unresolved.
- V2 calibration artifacts are not loaded as V3.

## Zero-Model Precheck

Canonical precheck command:

```bash
python scripts/check_v3_test_readiness.py \
  --config configs/experiments/cover_kbc_v3_test.yaml
```

Observed result:

```text
V3 TEST PRODUCTION READINESS: READY
source commit: 3780bb7a6d6b0680b70046cafbd4cccde012b73e
TEST rows: 475
TEST SHA256: 67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1
TEST ordered identity: 69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640
calibration corpus SHA256: 50be66dc88419f88ac811243244d534f0a8a160bdf2a48d02eb77295a58c33af
parameter total: 28671226368 / 32000000000
missing calibration regions: 0
model calls: 0
```

The script uses the existing readiness gate and model-profile parser only. It
does not import or call the runtime builder, Hugging Face runtime, or model
generation seams.

## Submission Shape

The future TEST command will load `benchmark/data/test.jsonl`, whose dataset
length is 475. With no `--limit`, `scripts/run_cover.py` will therefore iterate
475 queries and write one prediction per canonical TEST row.

Submission format requirements verified offline:

- one prediction row per TEST row;
- canonical TEST order preserved;
- only `SubjectEntity`, `Relation`, and `ObjectEntities` in prediction rows;
- no gold fields;
- no TRAIN metadata fields embedded into predictions.

No TEST inference was executed.

## Production And Collection Separation

- TEST config is separate from the TRAIN diagnostic config.
- `train_collection` is absent.
- Supplemental coverage policy is absent.
- Coverage deficit priority is absent.
- Companion coverage scheduling is absent.
- Collection allowance is absent.
- `collect-v2-coverage-r2` is not the production planner.
- M21 is the production planner.
- M20/M21 artifact paths are under `configs/calibration/v3/`.

## V2 And V3 Non-Regression

Historical V2 calibration artifacts remain byte-identical:

- `configs/calibration/m20_relation_budget.json`
  - `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- `configs/calibration/m21_historical_bins.json`
  - `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- `configs/calibration/m21_planner_calibration.json`
  - `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

The V3 calibration artifacts listed above remain byte-identical to Audit 0073.
No M20-V3 or M21-V3 derivation command was run. The authoritative merged
calibration corpus under `outputs/` was not modified.

Immutable data and evaluator hashes:

- `benchmark/evaluate.py`
  - `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN: 477 rows
  - `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- VAL: 475 rows
  - `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- TEST: 475 rows
  - `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`

Model pair and budget:

- `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  - revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- `Qwen/Qwen3.5-4B`
  - revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- total: `28,671,226,368 / 32,000,000,000`

## Tests

Focused:

- `python -m pytest tests/test_v3_test_activation.py -q`
  - 14 passed

Full:

- `python -m pytest tests/ -q -p no:randomly`
  - 3594 passed, 4 skipped
- `python -m pytest tests/ -q`
  - 3594 passed, 4 skipped

Static:

- `python -m pyflakes src/ tests/ scripts/`
  - passed
- `git diff --check`
  - passed

Focused tests cover canonical TEST identity acceptance, wrong TEST SHA
rejection, wrong ordered identity rejection, wrong row count rejection,
non-empty TEST `ObjectEntities` rejection, V2 calibration path rejection for V3
TEST, collection-mode rejection, calibration hash mismatch rejection, unresolved
fallback rejection, zero-model precheck behavior, exact 475-row TEST resolution,
and Audit 0073 V3 artifact byte identity.

## Future GPU TEST Command

Prepared but not executed:

```bash
python -u scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_test.yaml \
  --split test \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/v3_test_submission_3780bb7a
```

This command is the first calibrated COVER-KBC V3 TEST submission run candidate.
It must be run only in the GPU environment.
