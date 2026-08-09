# Audit 0066 — Portfolio Direct retired; two-model calibrated baseline restored

**Date:** 2026-08-09
**Scope:** cleanup and baseline restoration. No architecture design, no V3 work.
**HEAD before this milestone:** `3083878232a03147d4a2ef5758ddf17e0476ba94`
**Python:** 3.14.5

---

## Verdict

**PASS — PORTFOLIO DIRECT RETIRED; TWO-MODEL CALIBRATED BASELINE RESTORED**

---

## 1. Starting state

```
$ git rev-parse HEAD
3083878232a03147d4a2ef5758ddf17e0476ba94       # as expected
```

The working tree was **not** clean. It carried the uncommitted Audit 0065
milestone — the runtime preflight, the `portfolio` dependency extra, the
`transformers` floor correction and the portfolio config metadata. These are
not unrelated third-party changes; they are the immediately preceding
milestone in this same sequence, and most of what they added is what this
cleanup retires. Nothing was hard-reset and no commit was rolled back; the
retirement was done by editing forward.

One piece of Audit 0065 is **kept deliberately** — see §6.

---

## 2. Historical negative ablation

Recorded as evidence, not as a live path.

**COVER-KBC Portfolio Direct** — Gemma-3-12B + Nemotron-Nano-9B-v2 +
Qwen3.5-9B, `controller_mode: direct_uncalibrated`, Modules 20 and 21
governing nothing.

Run `cover_kbc_v2_portfolio_test_direct_test_20260809T052008Z`, predictions
`e648932e131cfa2ac5f257180af09a6f355f7bf4c6bec96e460d172af0711966`, on the
current 475-row official TEST:

| Relation | Portfolio Direct | Baseline |
|---|---:|---:|
| awardWonBy | 0.2123 | 0.2565 |
| companyTradesAtStockExchange | 0.4500 | 0.6305 |
| countryLandBordersCountry | 0.8628 | 0.9264 |
| hasArea | 0.2400 | 0.2500 |
| hasCapacity | 0.0612 | 0.1224 |
| personHasCityOfDeath | 0.2400 | 0.4900 |
| **overall** | **0.3346** | **0.4499** |

Worse on all six relations. Treated as a completed negative ablation; no
attempt was made to rescue it.

(The baseline column is the historical 477-row snapshot result, so the two
columns are not a controlled comparison of the same split — the direction and
the margin are what motivate the retirement.)

---

## 3. Active architecture after cleanup

| Role | Model | Revision | Params |
|---|---|---|---:|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 |
| | | **total** | **28,671,226,368** / 32,000,000,000, legal |

Counts and revisions are the repository's already-verified values; nothing was
recomputed or invented. The budget audit accepts the pair, asserted by test.

The baseline command needs no experimental flag:

```
python scripts/run_cover.py --config configs/experiments/cover_kbc_v2_test.yaml
```

Driven through the real `main()`, that builds exactly
`mistralai/Mistral-Small-3.2-24B-Instruct-2506` and nothing else.

---

## 4. Retired

| What | How |
|---|---|
| `configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml` | deleted (351 lines) |
| `configs/experiments/cover_kbc_v2_portfolio_test.yaml` | deleted (356) |
| `configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml` | deleted (330) |
| `src/cover_kbc/models/strategy.py` | deleted (555) — strategies, roles, routing, governance, fingerprint |
| `src/cover_kbc/models/bakeoff.py` | deleted (416) — the TRAIN comparison harness |
| `tests/test_model_strategy.py` | deleted (1278) |
| `tests/test_portfolio_direct_test.py` | deleted (604) |
| `--model-strategy` CLI flag | removed |
| three-runtime construction in the runner | removed |
| `IntegrationMode.DIRECT_UNCALIBRATED`, `is_direct`, `uses_calibrated_controller`, `production_calibrated` | removed |
| `ReadinessState.FULL_TEST_DIRECT_READY`, `may_run_test_direct`, `evaluate_direct_test_readiness` | removed |
| `CoverPipeline(structural_runtime=…)`, the three-runtime `physical_snapshot`, `_begin_query_for_selector`, the direct precharge short-circuit | removed |
| direct fixed-policy selector wiring in the runner | removed |
| manifest `model_strategy*`, `controller_mode`, `production_calibrated`, `calibration_owner`, `experiment_variant` | removed |
| `ModelRole.FACTUAL_ENUMERATOR` / `STRUCTURAL_REASONER` / `INDEPENDENT_VERIFIER` | removed |
| calibration strategy-ownership and portfolio-fingerprint checks in the loader | removed |
| `mamba-ssm`, `causal-conv1d`, the `portfolio` extra, the README install block | removed |

**No dead branch remains.** Asserted mechanically over docstring-stripped
source: no executable line in `src/` or `scripts/` contains
`google/gemma-3-12b-it`, `nvidia/NVIDIA-Nemotron-Nano-9B-v2`, `Qwen/Qwen3.5-9B`,
`ModelStrategy`, `model_strategy`, `DIRECT_UNCALIBRATED`, `is_direct`,
`FULL_TEST_DIRECT_READY` or `evaluate_direct_test_readiness`; and no experiment
config names a retired model. `parse_mode("direct_uncalibrated")` now raises.

Docstrings are excluded from that scan on purpose: this repository records what
it deliberately does not do, and after a retirement it records what it used to.
A raw-text scan would flag the record of the removal as the thing itself.

### Deliberately kept

`configs/models/bakeoff-candidates.yaml` and `configs/models/qwen3.5-9b.yaml`
name Qwen3.5-9B and Gemma, and both were added in commit `584de69` — long
before the portfolio experiment. They are model-profile metadata, not
executable support: no experiment config and no code path references them, and
`run_cover.py` takes an experiment config. `tests/test_pipeline.py` likewise
uses Qwen3.5-9B only as arithmetic in a pre-existing test proving that
Mistral-24B + Qwen-9B is *over* budget and must be refused. Removing any of
these would be deleting baseline safety evidence, not portfolio support.

---

## 5. Preserved

**Current official TEST snapshot** (Audit 0064), unchanged and unmodified:
475 rows, `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`,
ordered identity `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`.
The baseline TEST config and the packager both still pin exactly that, and the
historical 477-row hashes were not reinstated.

**Baseline M20/M21**, byte-identical and not regenerated:

```
8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68  m20_relation_budget.json
d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071  m21_historical_bins.json
36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05  m21_planner_calibration.json
```

They load, all six relations are `TRAIN_CALIBRATED`, 64 bins, depth 1. The
baseline stays *calibrated*: M20 and M21 are in `production` mode in both the
VAL and TEST configs, Module 21 still owns action choice under
`is_production`, and Module 20 still precharges every production action.

**M0–M21 contract**: one `CoverPipeline`, one canonical runner, no Module 22,
`PHYSICAL_COUNTERS` unchanged, `AccountingInvariantError` still process-fatal
ahead of the generic per-row handler. Prompts, scoring, thresholds, selection
and module ownership untouched.

**Historical evidence**: Audits 0061–0065 are unmodified and still on disk — a
test asserts all five are present. They describe the portfolio design, the
direct path, the TEST synchronization and the real-weight runtime work, and
they remain true of the moment they were written. Nothing was rewritten because
the experiment failed. Benchmark data untouched; TEST not evaluated.

---

## 6. Dependency cleanup, and the one thing kept from Audit 0065

`mamba-ssm` and `causal-conv1d` existed only for Nemotron's hybrid
Mamba2-Transformer remote code. Nemotron is retired, so the `portfolio` extra,
both pins and the README `--no-build-isolation` block are gone. A test asserts
neither package name survives in `pyproject.toml` or the README.

**`transformers>=5.2.0` stays.** This is not a portfolio dependency and it must
not be reverted to the earlier `>=4.51.0` or to a false `>=4.57`:

* the baseline's own verifier, `Qwen/Qwen3.5-4B`, declares
  `model_type: qwen3_5`;
* `transformers.models.qwen3_5` first ships in **v5.2.0** — verified against
  the upstream tree (absent at v4.57.6, v5.0.0, v5.1.0; present at v5.2.0), and
  4.57.6 is the last of the 4.57 line;
* 4.57.6 was observed failing on exactly this, for real (Audit 0065);
* the successful runtime used 5.14.1, which satisfies `>=5.2.0`.

`>=5.2.0` is the narrowest truthful floor: it is the first version that can
load the baseline verifier, and it also clears Mistral-Small-3.2's own
`mistral3` minimum of 4.52.4. The earlier `>=4.51.0` could never have loaded
the verifier at all.

`src/cover_kbc/models/preflight.py` survives in **reduced** form — the Mamba
checks are gone, the `qwen3_5` check remains — and the runner calls it before
the first `build_runtime`, only when a declared model block uses a neural
backend. The abstain and scripted smokes still run on a machine with no
transformers at all, asserted by driving the real `main()` on
`smoke_abstain.yaml` and reaching `build_runtime`.

---

## 7. Verification

```
BASELINE TEST  FULL_TEST_READY        0 blockers
BASELINE VAL   FULL_VALIDATION_READY  0 blockers
calibration    6 budgets, 64 bins, depth 1, all TRAIN_CALIBRATED
execution modes  ['shadow', 'production', 'train_calibration_collection_only']
model roles      ['enumerator', 'verifier', 'stub', 'none']
experiment configs  ablation_fixed_multiview, cover_kbc_v2_mistral24_qwen4,
                    cover_kbc_v2_test, cover_kbc_v2_train_collection,
                    cover_kbc_v2_validation, smoke_abstain,
                    smoke_staged_roleswap, smoke_staged_scripted
```

No `*portfolio*` config remains.

---

## 8. Tests

New `tests/test_baseline_restoration.py` — **40 tests**, covering every
property §11 asks for: exactly two active models; exact pinned revisions;
total 28,671,226,368 and the budget audit accepting it; no retired model id in
any experiment config or executable line; no `DIRECT_UNCALIBRATED` in the mode
vocabulary or anywhere in code; the current 475-row TEST with its exact hashes;
baseline M20/M21 unchanged and still governing; baseline runtime and accounting
semantics unchanged; and the archive contract still exactly
`predictions.jsonl`. It also asserts the five experiment audits are still on
disk.

`tests/test_runtime_preflight.py` reduced from 42 to the qwen3_5 checks plus
the "stub profiles need no transformers" guard.

Four pre-existing CLI harnesses gained a one-line stub of the preflight
(`test_production_activation.py` ×2, `test_production_source_fixes.py`). They
drive `main()` with scripted runtimes and load no real weights, so the
environment gate has nothing to guard there. That it fired on them at all is
evidence it is wired into the real path.

`tests/test_integration_mode.py` went back to asserting three modes, with a
comment recording that a fourth briefly existed and was retired.

```
$ python -m pytest tests/test_baseline_restoration.py -q -p no:randomly
40 passed in 4.33s

$ python -m pytest <the nine targeted suites> -q -p no:randomly
287 passed, 1 skipped in 19.58s

$ python -m pytest tests/ -q -p no:randomly
3488 passed, 4 skipped in 57.80s

$ python -m pytest tests/ -q                       # randomized order
3488 passed, 4 skipped in 58.20s
```

3650 → 3488. The 1,882 deleted portfolio tests are replaced by 40 that assert
the retirement instead; no failures, no xfails, and the 4 skips are the
pre-existing environment-dependent ones.

```
$ python -m pyflakes <every tracked .py + the new modules>
pf=0

$ git diff --check
check=0

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22
$ git diff -- benchmark/ | wc -l
0
$ sha256sum benchmark/data/test.jsonl
67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1
```

No TEST inference, no TEST evaluation, no calibration regenerated.

---

## 9. Files changed

```
   0  356  configs/experiments/cover_kbc_v2_portfolio_test.yaml          (deleted)
   0  351  configs/experiments/cover_kbc_v2_portfolio_test_direct.yaml   (deleted)
   0  330  configs/experiments/cover_kbc_v2_portfolio_train_collection.yaml (deleted)
   0  555  src/cover_kbc/models/strategy.py                              (deleted)
   0  416  src/cover_kbc/models/bakeoff.py                               (deleted)
   0 1278  tests/test_model_strategy.py                                  (deleted)
   0  604  tests/test_portfolio_direct_test.py                           (deleted)
  16  134  scripts/run_cover.py
   3  156  src/cover_kbc/controller_calibration/readiness.py
   0   45  src/cover_kbc/controller_calibration/production.py
   1   44  src/cover_kbc/integration_mode.py
   5   52  src/cover_kbc/pipeline.py
   0   20  src/cover_kbc/runtime/manifest.py
   0   12  src/cover_kbc/types.py
   0    1  src/cover_kbc/evidence/production_bridge.py
   8    1  pyproject.toml
   7   23  tests/test_integration_mode.py
  10    0  tests/test_production_activation.py
   5    0  tests/test_production_source_fixes.py
```

**New:** `tests/test_baseline_restoration.py`.
**Reduced, not removed:** `src/cover_kbc/models/preflight.py`,
`tests/test_runtime_preflight.py`.
**Reverted to its pre-portfolio state:** `README.md` (no diff remains).

**Not touched:** benchmark data, `benchmark/evaluate.py`, baseline calibration
artifacts, the three baseline experiment configs' model/controller semantics,
prompts, thresholds, scoring, M0–M21 ownership, the packaging contract, and
every prior audit.

---

## 10. Where this leaves the project

**Active:** COVER-KBC baseline — Mistral-Small-3.2-24B + Qwen3.5-4B,
TRAIN-calibrated M20/M21, one pipeline, one runner, 28,671,226,368 parameters,
`FULL_TEST_READY` against the current 475-row official TEST.

**Historical:** the heterogeneous portfolio, designed in Audit 0062, executed
in 0063, run for real in 0065, and measured at 0.3346 — a completed negative
ablation whose record survives in the audits even though its code does not.

V3 design is explicitly out of scope for this milestone and was not started.

---

**PASS — PORTFOLIO DIRECT RETIRED; TWO-MODEL CALIBRATED BASELINE RESTORED**

Nothing was committed or pushed.
