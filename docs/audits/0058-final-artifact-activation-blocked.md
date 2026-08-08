# Audit 0058 — Final artifact activation: BLOCKED, artifacts absent

**Date:** 2026-08-08
**Scope:** the final real-artifact activation milestone. No source review requested.
**HEAD:** `78ad89d3cd8a321f500807b11477fce2f8579e32` — working tree **clean**
**Python:** 3.14.5

---

## Verdict

**HOLD — DO NOT RUN VAL**

Not because anything is wrong with the system, and not because of any finding.
The milestone cannot start: **the three newly derived real artifact files are
not present in this environment.** They were produced on Colab and verified on
Drive; they were never transferred to this machine.

`configs/calibration/` still holds the stale bytes, committed at HEAD:

```
8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070  m20_relation_budget.json
8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5  m21_historical_bins.json
a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade  m21_planner_calibration.json
```

None of the three declared final hashes matches anything on disk. Steps 1, 2, 4
and 5 of the milestone all consume those bytes, and steps 3 and 6 are only
meaningful once they are in place. **Nothing was changed.**

---

## 1. What was searched

The final files were looked for exhaustively before declaring the block:

* by name — `find / -maxdepth 6 -name "m21_planner_calibration.json"`: only test
  fixtures under `/tmp/pytest-of-vquclinh/...` and three scratch directories
  from this session's own CLI probes;
* **by content** — every file under `/mnt`, `/home/vquclinh` and `/media`
  smaller than 2 MB with a `.json`, `.zip` or `.tar*` extension was hashed and
  compared against all three declared SHA256 values. **No match for any of the
  three.**
* by recency — every file under `/home/vquclinh`, `/mnt`, `/media` and `/tmp`
  smaller than 1 MB modified since 2026-08-07, excluding caches and
  `site-packages`. Nothing but editor and language-server temporaries.
* in the obvious hand-off locations — `~/Downloads`, `~/Desktop`,
  `~/Documents`, `outputs/`, `notebooks/`. `outputs/` contains only the older
  smoke and staged runs; it holds no derivation bundle.

The 134 MB TRAIN telemetry is also absent, so the artifacts could not be
reproduced locally even if that were permitted — and it is not: this milestone
explicitly forbids re-deriving calibration, and every prior milestone forbids
fabricating artifact contents.

---

## 2. What *was* verified (nothing that needed the new files)

**The derivation provenance story checks out.** HEAD is exactly the declared
derivation commit:

```
$ git rev-parse HEAD
78ad89d3cd8a321f500807b11477fce2f8579e32
```

which matches `derivation_repo_sha: 78ad89d3cd8a321f500807b11477fce2f8579e32`.
The previous milestone's source changes are committed and the tree is clean
(`git status --short` empty), so the artifacts really were derived from the
clean exact source the activation will package.

**Readiness is still correctly fail-closed on the stale bytes:**

```
state: NOT_READY   may_run_validation: False
blockers: 1
  - calibration: the planner calibration requests depth-2 lookahead but 5
    historical bin(s) record no successor statistics ...
```

That single blocker is exactly the condition the re-derivation resolves. It is
the marker that the swap has not happened yet.

**The intentional stale-artifact xfail is still xfailing**, i.e. it is still
telling the truth:

```
XFAIL tests/test_real_calibration_artifacts.py::test_the_real_artifacts_reach_full_validation_ready
  ... the shipped artifact still carries the old value ...
```

It must **not** be removed before the bytes land: removing it now turns the
suite red rather than green, because the shipped artifact genuinely still
declares `lookahead_depth = 2`.

**Baseline integrity, unchanged:**

```
$ python -m pytest tests/ -q -p no:randomly
3371 passed, 4 skipped, 1 xfailed in 65.01s

$ python -m pytest tests/test_real_calibration_artifacts.py \
      tests/test_production_activation.py tests/test_production_source_fixes.py \
      tests/test_accounting_invariant_fail_stop.py \
      tests/test_layer4_settlement_lifecycle.py -q -p no:randomly
184 passed, 1 skipped, 1 xfailed in 16.91s

$ sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22

$ git diff -- benchmark/ | wc -l
0

$ git status --short | wc -l
0
```

No real model weights were loaded, no VAL row was inferred, no TEST row was
read. The only artifact reads in this session were the three files the readiness
gate opens by name.

---

## 3. A defect in the brief: the M21 planner hash is stated twice, inconsistently

Worth fixing before the swap, because the wrong one would be pasted into the
config and the loader would then refuse the correct file.

| Where | Value | Length | Valid hex-64 |
|---|---|---|---|
| "Canonical SHA256" section | `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` | 64 | **yes** |
| Section 1 restatement | `36315cd72a2c31bcbc61bb1ada9f2e74d89861bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` | 82 | no |

The second contains a duplicated fragment (`61bb1ada9f2e74d898` repeated) and is
not a SHA256 at all. The 64-character value from the "Canonical SHA256" section
is the one that will be used, and it will be checked against the delivered file
before anything is written. The other two hashes are stated once each and are
well-formed.

---

## 4. What is needed to unblock

Place the three files anywhere readable and say where; the whole milestone is
then mechanical. For example:

```
<somewhere>/m20_relation_budget.json
<somewhere>/m21_historical_bins.json
<somewhere>/m21_planner_calibration.json
```

Before anything is copied they will be hashed and required to equal:

```
8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68  m20_relation_budget.json
d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071  m21_historical_bins.json
36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05  m21_planner_calibration.json
```

A mismatch stops the milestone; nothing will be edited to make a hash agree.

The remaining work, in order, once they arrive:

1. copy the three files byte-for-byte into `configs/calibration/`, re-hash on
   arrival, and confirm the three values above;
2. update **only** the calibration identity fields in the VAL config —
   `derivation_repo_sha` → `78ad89d3cd8a321f500807b11477fce2f8579e32` and the
   three `*_sha256` fields — preserving `collection_repo_sha`,
   `pipeline.mode: interleaved`, the frozen model ids/revisions and every other
   setting;
3. load through the canonical owners (`load_calibrations`, `load_history`,
   `PlannerCalibration.from_json` via `load_production_calibration`) and verify
   six relations at 44/30/24/22/23/22, `TRAIN_CALIBRATED`, 64 bins,
   `lookahead_depth = 1`, α=1.0 β=10.084164 γ=0.0 δ=0.069917 η=0.143625 κ=1.0
   τ=0.0, shared collection and derivation provenance, no synthetic source, no
   TRAIN factual dependency;
4. remove the strict xfail deliberately, leaving the test and its assertions
   intact, and confirm it now passes;
5. run the real readiness gate unmocked and require `FULL_VALIDATION_READY`,
   `may_run_validation = True`, zero blockers;
6. run the named suites, the full suite, pyflakes, and re-confirm
   `benchmark/evaluate.py` at `2d592ae1…` with an empty `git diff -- benchmark/`.

Provenance semantics are understood and will be respected: the artifacts'
`derivation_repo_sha` stays `78ad89d3…` forever. The later activation commit
only packages bytes that were already derived, and the artifacts will not be
regenerated to make their provenance equal it.

---

## 5. Final status

| # | Question | Answer |
|---|---|---|
| 1 | Final M20 hash correct? | **N/A — file absent** |
| 2 | Final M21 history hash correct? | **N/A — file absent** |
| 3 | Final M21 planner hash correct? | **N/A — file absent** (and the brief states this hash twice, inconsistently — see §3) |
| 4 | Derivation provenance exact? | **Not applied.** HEAD is `78ad89d3…` as declared, but the config still names `b1804646…` |
| 5 | Collection provenance exact? | **YES** — `264c980361a513078903526440c72adc6e10edaf`, unchanged in the config |
| 6 | M20 six relations and envelopes exact? | **Not verifiable** on the final bytes; the stale artifact does carry 44/30/24/22/23/22 |
| 7 | M21 64 bins? | **Not verifiable**; the stale artifact carries 64 |
| 8 | M21 `lookahead_depth = 1`? | **NO** — the shipped artifact still declares 2 |
| 9 | Planner coefficients preserved? | **Not verifiable** on the final bytes |
| 10 | Stale-artifact xfail removed and passing? | **NO** — deliberately left in place; removing it now would make the suite red |
| 11 | Production config points to final artifacts? | **NO** |
| 12 | Pipeline mode remains interleaved? | **YES** |
| 13 | `FULL_VALIDATION_READY`? | **NO** — `NOT_READY` |
| 14 | `may_run_validation = True`? | **NO** |
| 15 | Zero readiness blockers? | **NO** — 1 (the expected stale-depth-2 blocker) |
| 16 | Full tests green? | **YES** — 3371 passed, 4 skipped, 1 xfailed |
| 17 | Benchmark untouched? | **YES** — `2d592ae1…`, empty diff |
| 18 | Real-weight smoke run? | **NO** |
| 19 | Full VAL run? | **NO** |

**HOLD — DO NOT RUN VAL**

The system is not broken and no new finding was raised. The milestone is
waiting on three files. Nothing was committed or pushed, and nothing in the
working tree was modified — `git status --short` is empty apart from this audit.
