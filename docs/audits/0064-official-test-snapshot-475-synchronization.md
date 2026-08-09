# Audit 0064 — Official TEST snapshot synchronization (477 → 475)

**Date:** 2026-08-09
**Scope:** benchmark-snapshot synchronization only. No architecture, model,
routing or M0–M21 change.
**HEAD:** `9c12f248dd957db706e750588a8d4212fd2e7438` ("update benchmark")
**Python:** 3.14.5

---

## Verdict

**PASS — CURRENT 475-ROW OFFICIAL TEST SNAPSHOT FULLY SYNCHRONIZED**

---

## 1. The two benchmark generations

Both must remain expressible; only one is current.

| | HISTORICAL (Submission #1) | CURRENT (official) |
|---|---|---|
| TEST rows | 477 | **475** |
| TEST sha256 | `849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f` | **`67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`** |
| TEST ordered identity | `1bce6d40f843f7c743af6d896f2a390c4e210eac32d95f64d2887e5373fc2609` | **`69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`** |

Verified against the file on disk, not taken from the brief:

```
rows            : 475
sha256          : 67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1
ordered identity: 69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640
relations       : awardWonBy 10, companyTradesAtStockExchange 100,
                  countryLandBordersCountry 67, hasArea 100,
                  hasCapacity 98, personHasCityOfDeath 100
all blind       : True        duplicate identities: 0
```

The two removed rows are both `hasCapacity` (100 → 98). Every row still ships
an empty `ObjectEntities`, so TEST remains blind.

The official TEST macro-F1 of **0.4499** was measured on the *historical* 477-row
snapshot. It is not a result on this one, and nothing in this milestone implies
otherwise.

---

## 2. The update was wider than TEST

`git show --stat 9c12f24` changed **all three splits**, which the brief did not
mention. Recorded here because two of the three have live consequences:

| Split | Rows before → after | sha256 before → after |
|---|---|---|
| train | 477 → **477** | `cb344aa3…2a2c74` → **`ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`** |
| val | 478 → **475** | `90e4f247…c18d02` → **`ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`** |
| test | 477 → **475** | `849f565d…44fc1f` → **`67c31c83…f043f1`** |

TRAIN kept its row count but not its contents. This matters for the *next*
portfolio derivation and is handled in §4; it does not affect the shipped
baseline calibration, whose provenance correctly records the TRAIN it was
actually derived from.

---

## 3. Active TEST pins updated

Six, and only these:

| File | Field | 477 → 475 |
|---|---|---|
| `configs/experiments/cover_kbc_v2_test.yaml` | `test_dataset.rows/sha256/identity_sha256` | ✔ |
| `configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml` | `test_dataset.rows/sha256/identity_sha256` | ✔ |
| `configs/experiments/cover_kbc_v2_portfolio_test.yaml` | `test_dataset.rows/sha256/identity_sha256` | ✔ |
| `scripts/package_submission.py` | `OFFICIAL_TEST` rows/sha256/identity_sha256 | ✔ |
| `tests/test_official_test_activation.py` | `TEST_ROWS` / `TEST_SHA256` / `TEST_IDENTITY` | ✔ |
| `tests/test_portfolio_direct_test.py` | `TEST_ROWS` / `TEST_SHA256` / `TEST_IDENTITY` | ✔ |

Edits were confined to the `test_dataset:` block of each config — the partition
was done textually so no other key could be touched. After the change,
`grep` for either old TEST hash across `src/`, `scripts/`, `tests/` and
`configs/` returns nothing.

Three assertions in my own tests also moved, each on its own merits rather than
by find-and-replace:

* a literal `"test dataset: 477 rows"` → `f"…{TEST_ROWS} rows"`;
* `test_the_test_gate_refuses_val_data_under_a_test_label` no longer requires a
  *row-count* blocker — VAL and TEST now have the same row count, so the byte
  hash and the ordered identity are what catch it, which is precisely why
  identity is pinned three ways and not one;
* `test_validation_packaging_is_unchanged` now counts VAL's rows from the file
  instead of pinning 478. VAL is not the submission split and the packager pins
  no identity for it, so the guard is the identity contract, not a constant the
  organizer can move.

---

## 4. Values deliberately left at their old figures

| Where | Value | Why it stays |
|---|---|---|
| `cover_kbc_v2_validation.yaml` / `cover_kbc_v2_test.yaml` → `calibration_provenance.train_sha256` | `cb344aa3…` | Historical fact: the shipped M20/M21 **were** derived from that TRAIN. Rewriting it would erase which data produced the calibration. |
| same → `calibration_provenance.val_sha256` / `val_rows` | `90e4f247…` / 478 | Historical fact: the snapshot the calibration was validated against, and the run behind the 0.4230 VAL figure. |
| `EXPECTED_TRAIN_ROWS = 477` in `derive_train_calibration.py`, `run_train_calibration_collection.py` | 477 | TRAIN is still 477 rows. |
| `78ad89d3cd8a321f500807b11477fce2f8579e32` (×4) | — | A commit SHA that merely contains the digits `477`. |
| Prose/docstrings mentioning 477 or 478 | — | Narrative about past runs; §3 of the brief forbids mechanical edits to them. |
| All of `docs/audits/**` | — | Append-only. Audits 0060–0063 describe the 477-row snapshot and remain true of it. |

**One forward-looking pin was changed, and deliberately.**
`cover_kbc_v2_portfolio_test.yaml` → `calibration_provenance.train_sha256` held
`cb344aa3…`, inherited from the baseline. That block is a set of PENDING
placeholders for a portfolio derivation that has not run; a portfolio TRAIN
collection today would observe `ad37cd30…`, so the old hash was an expectation
the derivation is guaranteed to contradict. It is now `""` (PENDING), matching
the two repo SHAs beside it, with the reason recorded in the file.
`train_rows: 477` stays — only TRAIN's contents moved.

---

## 5. The VAL drift, made visible instead of hidden

`test_the_val_split_identity_is_declared_and_correct` hashed `val.jsonl` and
asserted it equalled the config. That was true and no longer is, and the
readiness gate does not hash VAL, so this test was the only place the drift
would surface.

It is now `test_the_val_provenance_records_the_snapshot_it_was_validated_on`:
it pins the historical 478 / `90e4f247…` as a *provenance claim*, reads the
current file, and asserts the two differ. The config keeps its historical
values, the drift is asserted rather than silently reconciled, and a future
reader learns which VAL the 0.4230 figure came from.

No assertion was weakened: the test went from one live equality to a historical
pin plus an explicit divergence check.

---

## 6. Readiness against the new snapshot

Run unmocked against the real files:

```
BASELINE TEST        (experiment A)  FULL_TEST_READY         blockers 0
                                     rows 475  sha 67c31c8388c5  id 69d7d7cbafed
BASELINE VAL                          FULL_VALIDATION_READY   blockers 0
PORTFOLIO DIRECT     (experiment B)  FULL_TEST_DIRECT_READY  blockers 0
                                     rows 475  sha 67c31c8388c5  id 69d7d7cbafed
PORTFOLIO CALIBRATED (experiment C)  NOT_READY               blockers 6
```

Experiment C's six blockers are the three missing portfolio artifacts, the
calibration load that depends on them, and the model-profile lookup. **None is
a dataset-identity blocker** — §6 of the brief satisfied: its refusal concerns
missing calibration, not a stale snapshot.

Baseline reaching `FULL_TEST_READY` on the 475-row split does **not** mean
Submission #1 ran on it. Submission #1 answered the 477-row snapshot; that
remains recorded in Audits 0060–0063 and in its own manifest and ZIP, none of
which were touched.

---

## 7. Packaging

`OFFICIAL_TEST` now pins the current snapshot, and the archive contract is
unchanged — root holds exactly `predictions.jsonl`, nothing else. Live:

```
accepts the new snapshot
  split            : test
  dataset sha256   : 67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1
  row count        : 475
  ordered identity : 69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640
  zip sha256       : 68038f479e2f9a0a009ab43954d238877c9760d70a01b172af6d0ff6e2afd789

refuses a short/stale predictions file
  REFUSED: ordered identity 408fe554ce7282c7..., expected 69d7d7cbafed0a61...
refuses VAL predictions as TEST
  REFUSED: ordered identity 3968a5bad12b4afb..., expected 69d7d7cbafed0a61...
```

Identity validation was not weakened; no log, manifest, telemetry or config
enters the ZIP.

---

## 8. Unchanged

Baseline models, revisions, prompts, thresholds, decoding, scoring, selection,
controller and parameter total (28,671,226,368). Portfolio Direct semantics:
`model_strategy=portfolio`, Gemma3-12B + Nemotron9B + Qwen3.5-9B,
`controller_mode=direct_uncalibrated`, `production_calibrated=false`, M20 and
M21 controlling **NO**. Baseline calibration artifacts byte-identical:

```
8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68  m20_relation_budget.json
d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071  m21_historical_bins.json
36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05  m21_planner_calibration.json
```

---

## 9. Validation

```
$ python -m pytest tests/test_portfolio_direct_test.py tests/test_model_strategy.py \
      -q -p no:randomly
184 passed in 4.48s                      # was 3 failed, 181 passed

$ python -m pytest tests/test_package_submission.py \
      tests/test_official_test_activation.py tests/test_production_activation.py \
      tests/test_real_calibration_artifacts.py \
      tests/test_controller_calibration_readiness.py -q -p no:randomly
163 passed, 1 skipped in 16.21s

$ python -m pytest tests/ -q -p no:randomly
3606 passed, 4 skipped in 63.87s

$ python -m pytest tests/ -q                       # randomized order
3606 passed, 4 skipped in 57.47s
```

The full suite began this milestone at **15 failed, 3591 passed** — five times
what the named two-file command reported, because eleven of the failures were
in `test_official_test_activation.py` and one in `test_production_activation.py`.
All fifteen are resolved. No test count changed (3606 before and after the
benchmark update), no xfails, and the 4 skips are the pre-existing
environment-dependent ones.

```
$ python -m pyflakes <every tracked .py + the new modules>
pf=0                # no output

$ git diff --check
check=0

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/evaluate.py | wc -l
0
$ git diff --stat -- benchmark/
(empty)
```

Benchmark data was not modified in this milestone; the update arrived at HEAD.

---

## 10. Files changed

```
13   4  configs/experiments/cover_kbc_v2_portfolio_test.yaml
 3   3  configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml
 3   3  configs/experiments/cover_kbc_v2_test.yaml
 3   3  scripts/package_submission.py
13   6  tests/test_official_test_activation.py
 3   3  tests/test_portfolio_direct_test.py
31   5  tests/test_production_activation.py
```

The three-line diffs are exactly the rows/sha256/identity triple. The larger
counts are the comment explaining the PENDING portfolio TRAIN hash (§4) and the
rewritten VAL-provenance test (§5).

**Not touched:** benchmark data, `benchmark/evaluate.py`, baseline calibration
artifacts, baseline model/controller semantics, Portfolio Direct semantics, the
archive contract, prior audits, and the Submission #1 manifest, predictions and
ZIP.

---

## 11. Next

The direct portfolio run is unblocked against the current snapshot:

```
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml \
  --model-strategy portfolio

python scripts/package_submission.py --split test \
  --predictions outputs/<run_id>/predictions.jsonl \
  --input benchmark/data/test.jsonl --out test_submission_v2.zip
```

Environmental blockers from Audit 0063 are unchanged: Gemma gated terms,
`transformers >= 4.57`, Nemotron `trust_remote_code`, and A100 40GB feasibility
still **ESTIMATED**.

One item for whoever runs the portfolio TRAIN collection later: it will observe
TRAIN at `ad37cd30…`, not the `cb344aa3…` the baseline was derived from. The
portfolio calibration will therefore be measured on a different TRAIN corpus
than the baseline's — legitimate, but a real difference between experiments A
and C that the ablation table should state.

---

**PASS — CURRENT 475-ROW OFFICIAL TEST SNAPSHOT FULLY SYNCHRONIZED**

Nothing was committed or pushed.
