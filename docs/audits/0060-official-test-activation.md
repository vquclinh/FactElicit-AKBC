# Audit 0060 — Official TEST activation

**Date:** 2026-08-09
**Scope:** make the validated frozen system able to run the official blind TEST
split and package a submission. No optimisation, no architecture change.
**HEAD:** `0597673e4b8a33521850252b5c63b71f00730cc3` (the validated production commit)
**Python:** 3.14.5

---

## Verdict

**PASS — READY TO RUN OFFICIAL TEST**

---

## 1. The official TEST data

`benchmark/data/test.jsonl`, inspected read-only:

| Property | Value |
|---|---|
| Rows | **477** |
| SHA256 | `849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f` |
| Bytes | 45,419 (trailing newline present) |
| Keys, every row | exactly `SubjectEntity`, `Relation`, `ObjectEntities` |
| Gold | **absent** — every row's `ObjectEntities` is `[]`, 0 rows carry any object |
| Duplicate identities | 0 |
| Ordered identity SHA256 | `1bce6d40f843f7c743af6d896f2a390c4e210eac32d95f64d2887e5373fc2609` |
| Relations | awardWonBy 10, companyTradesAtStockExchange 100, countryLandBordersCountry 67, hasArea 100, hasCapacity 100, personHasCityOfDeath 100 |

The ordered identity digest is SHA256 over the `SubjectEntity\tRelation` lines
in file order — one number that pins row count, per-row identity and ordering
at once, so a submission that answers the right questions in the wrong sequence
is as detectable as one that answers the wrong questions.

No evaluation was run on TEST and no hidden answers were read or produced.

---

## 2. Readiness, generalised without loosening anything

Design A. The body of `evaluate_validation_readiness` was extracted verbatim
into `_evaluate_production_readiness(...)` — same artifacts, same
`REQUIRED_VALIDATION_MODULES`, same production-mode requirement, same
model-profile resolution — parameterised only by the expected split name. Two
thin wrappers sit on it.

`evaluate_validation_readiness` keeps its exact signature, checks and verdict.
Nothing was removed from it and nothing was added to it.

`evaluate_test_readiness` adds what only the blind split needs, and each
addition is there because TEST has no second chance:

* `pipeline.mode == interleaved` — the mode the calibration was measured under;
* parameter budget legal by the profile's own declared totals — the 32B rule is
  enforced against a submission;
* the test dataset exists, and its **row count, byte SHA256 and ordered
  identity digest** all equal what the config records;
* the dataset is genuinely blind — any row carrying `ObjectEntities` is a
  blocker, so a run that *could* read gold is refused even though nothing on
  the path would.

New `ReadinessState.FULL_TEST_READY` and `ReadinessReport.may_run_test`, both
deliberately not satisfied by `FULL_VALIDATION_READY`.

**Live results:**

```
TEST config, test gate : FULL_TEST_READY   may_run_test=True   blockers=0
                         may_run_validation=False
  satisfied: split: test | pipeline.mode: interleaved
             parameter budget: 28671226368 <= 32000000000
             test dataset: sha256 849f565d6fcf... | 477 rows
             ordered identity 1bce6d40f843... | blind (no row carries objects)
             M20/M21 production | 6 budgets, 64 bins, tau=0.0 | all six relations

VAL config, val gate   : FULL_VALIDATION_READY  may_run_validation=True  blockers=0
                         may_run_test=False

VAL gate on TEST config: NOT_READY  ("a validation run may only read 'val'")
TEST gate on VAL config: NOT_READY  ("a test run may only read 'test'")
```

`--split test` is therefore not a bypass in either direction.

---

## 3. The TEST config

`configs/experiments/cover_kbc_v2_test.yaml`, generated from the validation
config. Machine-diffed against it:

```
keys only in TEST : ['test_dataset']
keys only in VAL  : []
blocks that differ: ['experiment']   (name, split, notes only)

identical blocks  : bidirectional_verification, budget_assertion,
                    calibration_provenance, consensus, coverage_gap,
                    layer4_integration, layer6_integration, micro_planner,
                    model_profile, pipeline, query_intelligence,
                    relation_budget_scheduler, specialist_verifier, specialists
```

Every semantic block — models, prompts, decoding, scoring, selection,
controller, action bounds, all three calibration hashes — is **equal by value**
to the profile that produced the validated VAL result. Asserted by test, not by
inspection.

Preserved exactly: `mistralai/Mistral-Small-3.2-24B-Instruct-2506` @
`95a6d26c4bfb886c58daf9d3f7332c857cb27b43`; `Qwen/Qwen3.5-4B` @
`851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`; 28,671,226,368 ≤ 32,000,000,000;
`pipeline.mode: interleaved`; `collection_repo_sha 264c9803…`;
`derivation_repo_sha 78ad89d3…`; M20 `8110fccb…`, M21 history `d6d19493…`, M21
planner `36315cd7…`.

The calibration provenance stays TRAIN-derived at `78ad89d3…`. Activating these
bytes for TEST packages them; it does not re-derive them.

Added: a `test_dataset` block recording rows, sha256, identity digest and
`blind: true`.

---

## 4. Runner

One entry point, one production stack. Routing is a table, not an if/else, so a
split cannot silently inherit another's gate:

```python
PRODUCTION_GATES = {
    "val":  (evaluate_validation_readiness, ReadinessState.FULL_VALIDATION_READY),
    "test": (evaluate_test_readiness,       ReadinessState.FULL_TEST_READY),
}
```

An unlisted split with a production config exits before any model is built.
After the gate, TEST constructs the *same* `CoverPipeline`,
`IntegrationMode.PRODUCTION`, M20 scheduler, M21 planner, `Layer6Integrator`
and M0–M21 stack — asserted by counting the single construction site in source.
The only semantic difference is which queries are loaded.

Evaluation is now refused by name rather than incidentally:

```python
scoreable = split not in BLIND_SPLITS and not dataset.is_blind
```

`test` is in `BLIND_SPLITS`, so `evaluate_predictions` is unreachable and no
`metrics.json` is written for it.

---

## 5. Packaging

`scripts/package_submission.py` gains an explicit `--split {val,test}`, default
`val`. The existing validation route is unchanged in behaviour; only the refusal
message for `test.jsonl` now names the TEST route instead of denying it exists.

TEST mode validates **by identity only** — the input's `ObjectEntities` are
never read, which is what makes packaging a blind split safe — and additionally
pins the official dataset: name, byte SHA256, row count and ordered identity.
It reuses the readiness module's `ordered_identity_digest`, so the gate and the
packager cannot disagree about what identity means, and a test asserts the
packager's pinned constants equal the config's `test_dataset` block.

Refused, each with its own reason: missing / extra / reordered / duplicated
rows; non-list `ObjectEntities`; non-string objects; malformed JSONL; a
non-official or tampered input file; a gold or diagnostic field; and **VAL
predictions passed as TEST**.

Live dry run against 477 placeholder rows:

```
split              : test
dataset sha256     : 849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f
row count          : 477
ordered identity   : 1bce6d40f843f7c743af6d896f2a390c4e210eac32d95f64d2887e5373fc2609
predictions sha256 : a59b757890fa5c01f2ef026f5fce14f86dad42dff7406a23c081c93baec205e7
zip sha256         : c14c73a1ae40a779abd7197563296587f746909e861368feb6701a8e8809e837
archive members    : ['predictions.jsonl']
relations          : {awardWonBy: 10, companyTradesAtStockExchange: 100,
                      countryLandBordersCountry: 67, hasArea: 100,
                      hasCapacity: 100, personHasCityOfDeath: 100}
```

And the accident that would score zero:

```
REFUSED: val_predictions.jsonl does not answer the official test split
(ordered identity c12664e4fae2cecf..., expected 1bce6d40f843f7c7...);
these look like predictions for a different split          exit=2
```

Archive holds exactly `predictions.jsonl` at the root. The hashes above are for
placeholder rows; the real ones come from the actual run.

---

## 6. Tests

New `tests/test_official_test_activation.py` — **46 tests**, covering all
thirteen required properties: VAL still `FULL_VALIDATION_READY` (1); TEST
reaches `FULL_TEST_READY` (2); config differs only in `experiment` and
`test_dataset` (3, 13); each gate refuses the other's config and val data under
a test label (4, 5); the runner's gate table and single production stack (6);
no evaluator call for a blind split (7); the split is blind and identity-only
validation never reads objects (8); prediction identity and order (9);
packaging accepts a valid TEST file (10); refuses VAL-as-TEST (11); archive
holds exactly `predictions.jsonl` (12).

Plus fail-closed coverage the brief implies: wrong dataset pin (rows, sha256,
identity — parametrised), a dataset carrying objects, non-interleaved mode
(parametrised), an illegal parameter budget, and each Layer-6 module disabled.

One pre-existing test updated: `test_refuses_the_test_split` asserted the old
"TEST is out of scope" message. Renamed to
`test_refuses_the_test_split_as_a_validation_submission` and now asserts the
same refusal still happens by default *and* under an explicit `--split val` —
stricter than before, not weaker.

One rename for correctness: the shared digest helper was first written as
`test_identity_digest`, which pytest collects as a test wherever it is
imported. It is now `ordered_identity_digest`.

```
$ python -m pytest tests/test_official_test_activation.py -q -p no:randomly
46 passed in 0.74s

$ python -m pytest tests/ -q -p no:randomly
3419 passed, 4 skipped in 49.90s

$ python -m pytest tests/ -q            # randomized order
3419 passed, 4 skipped in 48.40s
```

3373 → 3419: the 46 new tests. No skips or xfails were added.

```
$ python -m pyflakes <every tracked .py> tests/test_official_test_activation.py
pyflakes exit=0            # no output
```

```
$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0
```

Calibration artifacts unchanged: `8110fccb…`, `d6d19493…`, `36315cd7…`.

---

## 7. Files changed

| File | Change |
|---|---|
| `src/cover_kbc/controller_calibration/readiness.py` | `_evaluate_production_readiness` core; `evaluate_test_readiness`; `FULL_TEST_READY`; `may_run_test`; `ordered_identity_digest` |
| `scripts/run_cover.py` | `PRODUCTION_GATES` split routing; blind splits never scored |
| `scripts/package_submission.py` | `--split {val,test}`; official TEST dataset pin; identity-digest checks |
| `configs/experiments/cover_kbc_v2_test.yaml` | **new** — validation config, split changed, dataset recorded |
| `tests/test_official_test_activation.py` | **new** — 46 tests |
| `tests/test_package_submission.py` | one refusal message assertion, tightened |

Not touched: M0–M21 semantics, model ids/revisions/roles, prompts, thresholds,
M20/M21 numbers, selection policy, controller policy, budgets, quantization,
calibration artifacts, `benchmark/`.

**Did not happen:** no Mistral or Qwen weights loaded, no TEST inference, no VAL
re-run, no TEST evaluation, no submission, no commit, no push.

---

## 8. Report

| Item | Result |
|---|---|
| TEST row count | **477** |
| TEST SHA256 | `849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f` |
| TEST readiness state | **`FULL_TEST_READY`**, `may_run_test=True`, 0 blockers |
| VAL readiness still passes | **YES** — `FULL_VALIDATION_READY`, 0 blockers |
| Config equivalence | **YES** — differs only in `experiment` (name/split/notes) plus the new `test_dataset` block |
| Targeted tests | 46 passed |
| Full pytest | 3419 passed, 4 skipped (same randomized) |
| pyflakes | clean |
| Benchmark integrity | `2d592ae1…`, empty diff |

**PASS — READY TO RUN OFFICIAL TEST**

Run with:

```
python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_test.yaml
python scripts/package_submission.py --split test \
    --predictions outputs/<run_id>/predictions.jsonl \
    --input benchmark/data/test.jsonl --out test_submission.zip
```

Nothing was committed or pushed.
