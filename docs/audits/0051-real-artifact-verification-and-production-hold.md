# 0051 — Real Artifact Verification and Production HOLD

**Verdict: HOLD — DO NOT RUN VAL**

The three real artifacts are genuine, clean and correctly bound. One of them
cannot drive the planner it was derived for, and my instructions for that case
are explicit: stop and report, do not edit, downgrade, fabricate or regenerate.

---

## 0. Audit numbering

`docs/audits/` already contains **two** files numbered 0050 —
`0050-targeted-p1-remediation-reverification.md` (in repository history) and
`0050-production-activation-f11-f22-f24.md` (my previous report, which
duplicated the number). This audit is **0051**, the next actually available
number. Neither 0050 was overwritten.

---

## 1. Scope and what was done

`COVER_KBC_Technical_Proposal_New.pdf` was re-read before touching anything
(§16 Table 6, §17 the utility / `τ_continue` / *"1–2 step micro-lookahead"*,
§9.3, §21.2, §22).

The mission's own gate governed this milestone:

> If the real artifact FAILS the depth-2 loader requirement: **STOP** and report
> the exact missing relation/state/action bins
. Do NOT edit the artifact,
> silently downgrade lookahead, fabricate successor statistics, regenerate
> calibration, or continue toward VAL.

It failed. So verification was completed in full, the failure was proved down to
a real runtime crash, and **no production source was changed**. P1-A and the
execution-mode mismatch were traced and are reported with complete evidence, but
deliberately **not** implemented: both would be continuing toward VAL, and both
would be validated against an artifact that cannot run.

Working-tree changes this milestone: one new test file and this audit.

---

## 2. Real artifact hashes — **VERIFIED**

```
MATCH  m20_relation_budget.json      8ef1f07e61c42dfee6a99bfc8a5afb62fb2ff992bef65b93010a6f9e01fd7070
MATCH  m21_historical_bins.json      8c6f9c067130f56ce13d05347742d375fa27c94e3fad47ddc3f8b242832d7aa5
MATCH  m21_planner_calibration.json  a8ceac7186242dc71df751e4b99fed0adc797488586eb035b8431b4a8ebfcade
```

All three byte-exact against the published values.

## 3. Canonical loaders — **PASS**

Loaded through `load_calibrations`, `load_history`, `load_planner_calibration`:

| property | observed |
|---|---|
| M20 relations | **6** — all of `CONTRACTS` |
| M20 source | `TRAIN_CALIBRATED` on every relation |
| M21 bins | **64** — exactly as stated |
| M21 history source | `TRAIN_CALIBRATED`, fallback bin `__fallback__` |
| M21 planner source | `TRAIN_CALIBRATED` |
| coefficients | α 1.0, β 10.084164, γ **0.0**, δ 0.069917, η 0.143625, κ 1.0 |
| τ_continue | **0.0**, strict `>` preserved |
| lookahead_depth | **2** |
| finite | every estimate finite; no `NaN`/`Infinity` in any file |

Provenance agrees across all three files on `collection_repo_sha`,
`derivation_repo_sha`, `train_sha256`, `telemetry_sha256` and
`derivation_schema_version`, and matches the expected commits exactly:

```
collection_repo_sha  264c980361a513078903526440c72adc6e10edaf
derivation_repo_sha  b1804646dec3d2343dcf2cf8b277529071b89485
train_sha256         cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74
```

**C-02 carried truthfully:** γ = 0.0 and `expected_delta_h = 0.0` in all 64
bins. Nothing was manufactured.

**Table 6 ownership intact:** every relation's special reserves are a subset of
what its policy declares, and `awardWonBy` — the only relation Table 6 marks
*hard-reserved* — is the only one with a non-zero `verification_reserve` (14).

## 4. No TRAIN factual identity, no telemetry dependency — **PASS**

Scanned every string leaf and key of all three files against the whole TRAIN
split — **20 864 gold surfaces and 468 subjects** — with exact-equality and
whole-word matching:

```
exact leaks 0    whole-word leaks 0
```

No forbidden key (`ObjectEntities`, `SubjectEntity`, `gold`, `aliases`,
`prompt`, `raw_output`, any `candidates_*`, `operation_id`, `row_index`).
Total size **55 KB** across the three files — far too small to be a lookup
table over 477 rows. Production needs no gold, no telemetry, no predictions and
no manifest.

---

## 5. P2-A — depth-2 successor completeness — **FAIL. This is the blocker.**

The planner calibration declares `lookahead_depth = 2`. **Five of the sixty-four
shipped bins record no successor statistics:**

| relation | program_type | state_bin_key | family | support |
|---|---|---|---|---|
| countryLandBordersCountry | SMALL_SET | `residual=b1\|unresolved_mass=b1` | REVERSE_CHECK | 36 |
| countryLandBordersCountry | SMALL_SET | `residual=b2\|unresolved_mass=b1` | REVERSE_CHECK | 31 |
| hasArea | NUMERIC | `residual=b2\|unresolved_mass=NA` | CANDIDATE_FREE_RECALL | 43 |
| hasCapacity | NUMERIC | `residual=b2\|unresolved_mass=NA` | CANDIDATE_FREE_RECALL | 57 |
| **countryLandBordersCountry** | **SMALL_SET** | **`__fallback__`** | **REVERSE_CHECK** | **67** |

(108 successor branches exist across the other 59 bins.)

### Why this is not a sparse-bin corner case

These bins carry **31 to 67 observations each** — they are among the
best-supported in the package. They have no successor because the action was
**the last one in every chain that reached them**. Module 18's reverse check is
the third and final Layer-4 round for borders under
`max_control_rounds_per_catalogue: 3`, and a terminal action has nothing after
it to observe. The same holds for candidate-free recall on the two numeric
relations in that state bin.

### Why it is certainly reachable

The fifth entry is a **fallback** bin. `HistoricalBinPackage.lookup` routes any
state that matches no exact bin to the fallback, so a borders `REVERSE_CHECK`
in *any* unmapped state resolves there. Confirmed directly:

```
lookup(countryLandBordersCountry, SMALL_SET, "a state the collection never reached",
       REVERSE_CHECK)
  -> resolved: countryLandBordersCountry/__fallback__/REVERSE_CHECK
  -> successors: 0        (depth-2 needs >= 1)
```

### Why it is a real crash, not a conservative guard

The load-time guard added in the previous milestone refuses the package. To
prove the guard is not merely cautious, it was disabled **in memory only** (no
file touched) and the real production graph was built on the real artifacts and
given one borders query:

```
built production pipeline on REAL artifacts; depth = 2

*** RUNTIME FAILURE: PlannerError ***
    depth-2 planning needs successor statistics, and the bin for
    'M18:REVERSE:andorra' records none; depth-1 remains available but must be
    requested explicitly
```

It crashes on the **first** borders query. A 478-row VAL run would die within
the first few borders rows.

### Root cause

`derive_planner_calibration` sets

```python
lookahead_depth = 2 if any(b.successors for b in package.bins) else 1
```

`any`, not `all` — one bin with successors makes the whole package claim depth
2 — while `derive_m21` attaches successors only to bins that observed a
consecutive executed pair. A terminal action family therefore legitimately has
none. This is systematic, not bad luck.

### Not resolved here, by instruction

Every available remedy is forbidden to me and is the owner's decision:

1. **Re-derive `m21_planner_calibration.json` with `lookahead_depth: 1`.** §17
   says *"1–2 step micro-lookahead"*, so depth 1 is fully proposal-compliant.
   This touches only the smallest artifact; the 64 bins and the six budgets are
   unchanged. Cheapest and most faithful — but it is regenerating a calibration
   artifact.
2. **Change the derivation to `all(...)` and re-derive.** Same effect, and it
   fixes the rule for every future derivation. Also a regeneration.
3. **Make the planner fall back to depth 1 for a successor-less bin.**
   Explicitly forbidden ("do not silently downgrade lookahead"), and it would
   make the planner's depth unattributable per action.

Recommendation, for the owner to accept or reject: **option 2**, because it
corrects the rule that produced the defect rather than only its output, and the
re-derivation is deterministic and byte-reproducible from the same inputs
(Audit 0049 §6). Option 1 is the minimal-blast-radius alternative.

---

## 6. P1-A — budget ownership — **traced, NOT implemented**

Reported with full evidence so the decision can be made alongside P2-A.

### The intersection, located exactly

`relation_budget.build_plan` (`src/cover_kbc/control/relation_budget.py:276`):

```python
hard_calls = _intersect_ceiling(
    relation, calibration.hard_calls, core_budget.max_calls, "hard_calls", notes)
```

This is precisely the `min(core_max_calls, m20_hard_calls)` the mission's item
E names.

### Measured against the REAL envelopes

| relation | M7 core | **real M20 hard_calls** | disc | verif | reserve | effective after `min()` |
|---|---|---|---|---|---|---|
| awardWonBy | 12 | **44** | 1 | 18 | 14 | **12** |
| companyTradesAtStockExchange | 5 | **30** | 1 | 14 | 0 | **5** |
| countryLandBordersCountry | 4 | **24** | 1 | 14 | 0 | **4** |
| hasArea | 4 | **22** | 1 | 10 | 0 | **4** |
| hasCapacity | 4 | **23** | 1 | 10 | 0 | **4** |
| personHasCityOfDeath | 4 | **22** | 1 | 8 | 0 | **4** |

The intersection collapses a calibrated 22–44 call envelope to 4–12 — a five- to
ten-fold reduction. One M17 action reserves four non-cacheable readings (two
phrasings × two label orders; the four controls are `CACHE_HIT` and cost
nothing), so on the four relations with a core ceiling of 4 the action is
denied and Module 21 answers `NO_AFFORDABLE_ACTION`.

The real M20 numbers are direct evidence for the mission's premise: they were
measured while collection's `_precharge` returned `True` unconditionally, so
Layer-4 spent entirely outside the core ceiling. A borders query that the
telemetry records at 24 whole-query calls has a core ceiling of 4.

### A second consequence, previously unreported

For `awardWonBy` the intersected ceiling is 12 while the §9.3 protected
verification floor is **14**. `RelationBudgetCalibration.__post_init__`
validated `protected ≤ hard_calls` against the *unintersected* 44, so after
intersection the plan carries a floor larger than its own ceiling — an
internally inconsistent envelope that no reservation can satisfy. This is a
second symptom of the same ownership conflict, and it will not appear until
Module 20 is live.

### Proposal reading

Items A–E of the mission match what the executable owners do: §16 gives Module
20 the budget for the upgraded action space and defers its concrete values to
TRAIN; §17 has Module 21 select within it; Module 7's `max_calls` governs the
core controller phase and is a separate, uncalibrated ceiling. I found nothing
in the proposal requiring a single shared hard ceiling in which Module 7's
`max_calls` also caps M17/M18 — §16 says *"Budget accounting must be
cache-aware and precharge before every neural call. No action may exceed the
hard cap"*, and the hard cap it names is Module 20's.

That reading supports the separate-ownership interpretation. **I did not
implement it**, because implementing and testing it requires the very artifact
that P2-A shows cannot drive the planner, and because it is the same decision
cycle as the re-derivation. It is ready to implement as a small change at
`relation_budget.build_plan` once P2-A is resolved.

---

## 7. Execution-mode mismatch — **traced, NOT implemented**

| where | value |
|---|---|
| collection config `pipeline.mode` | `interleaved` |
| collection runner | forces `ExecutionMode.INTERLEAVED` (`run_train_calibration_collection.py:201`) |
| **mode the calibration was actually measured under** | **INTERLEAVED** |
| my VAL config `pipeline.mode` | `staged` |
| `run_cover.py` | forces `ExecutionMode.INTERLEAVED` (`:194`) |

So the runtime would in fact run interleaved — the same mode the calibration was
measured under — while the config *says* staged. The mismatch is a
config-truthfulness defect rather than a behavioural one today, but it is
exactly the kind that becomes behavioural the moment someone makes the runner
honour the config.

**The correct resolution is the opposite of what it first looks like.** The
mission asks to prefer honouring the configured mode; but the mode appropriate
to the calibrated system is **interleaved**, because that is what produced the
bins and envelopes. Making `run_cover.py` honour a `staged` declaration would
run VAL under an execution mode the calibration never observed.

Recommendation: set the VAL config to `mode: interleaved`, make `run_cover.py`
honour the configured mode and fail closed on an unsupported value, and add
execution mode to the collection/validation profile-equivalence test. I wrote
`staged` in the previous milestone by copying the frozen target profile without
checking what the collection runner actually did — that was my error, and this
is the correction. **Not implemented here**, per the STOP.

---

## 8. Readiness gate — actual output on the real artifacts

```
state: NOT_READY    may_run_validation=False

blockers:
  - calibration: the planner calibration requests depth-2 lookahead but 5
    historical bin(s) record no successor statistics, e.g.
    ['countryLandBordersCountry/__fallback__/REVERSE_CHECK',
     'countryLandBordersCountry/program_type=SMALL_SET|residual=b1|unresolved_mass=b1/REVERSE_CHECK',
     'countryLandBordersCountry/program_type=SMALL_SET|residual=b2|unresolved_mass=b1/REVERSE_CHECK'].
    Module 21 raises when it ranks an action from such a bin, so this package
    would fail mid-run. Either derive it with lookahead_depth 1, or derive a
    history in which every shipped bin observed a transition

satisfied:
  ok  M20 relation budget calibration: present
  ok  M20: 6 TRAIN_CALIBRATED relation budgets
  ok  M21 historical bins: present
  ok  M21 planner calibration: present
  ok  split: val
  ok  M20 and M21 both declare production mode
```

**Exactly one blocker.** Everything else the gate checks is already satisfied by
the real files — which is why the depth-2 item is the whole of the hold.

---

## 9. Regression — nothing changed, nothing regressed

No production source was modified this milestone. F-11, F-22 and F-24 remain as
implemented and tested in the previous one; shadow behaviour, the canonical
loaders, strict `U > τ_continue`, M8's sole ownership, the production bridge,
Table 6's qualitative policy and the frozen models are untouched. No training,
no RAG, no external KB.

| check | result |
|---|---|
| `pytest tests/test_real_calibration_artifacts.py` | **17 passed, 1 xfailed** |
| `pytest -q` (full) | **3 262 passed, 4 skipped, 1 xfailed** |
| `pyflakes src/ tests/ scripts/` | clean, exit 0 |
| `git diff -- benchmark/` | empty |
| benchmark snapshot | untouched |

The single `xfail` is `test_the_real_artifacts_reach_full_validation_ready`,
marked **strict** and carrying the P2-A reason. It is deliberate: today it
records the open blocker without reddening the suite, and the moment the
artifact is re-derived it becomes an *unexpected pass*, which fails and forces
the expectation to be updated on purpose rather than drifting.

---

## 10. Status

| # | item | state |
|---|---|---|
| 1 | Real artifact hashes verified | **PASS** — all three byte-exact |
| 2 | P2-A depth-2 real history | **FAIL** — 5 of 64 bins, runtime crash proved |
| 3 | P1-A budget ownership | **NOT IMPLEMENTED** — traced, evidenced, blocked behind P2-A |
| 4 | Execution-mode agreement | **NOT IMPLEMENTED** — traced; correct target is `interleaved` |
| 5 | F-11 | **DONE** (previous milestone, unchanged) |
| 6 | F-22 | **DONE** (previous milestone, unchanged) |
| 7 | F-24 | **DONE** (previous milestone, unchanged) |
| 8 | Real M20 active | **NO** — artifact verified and loadable; gate refuses the package |
| 9 | Real M21 active | **NO** — same |
| 10 | FULL_VALIDATION_READY | **NO** — one blocker |
| 11 | Scripted production preflight | **NOT RUN** — blocked; the graph crashes on the real artifact |
| 12 | Real-weight smoke | **NOT RUN** |
| 13 | Full VAL | **NOT RUN** |

## 11. Exact path forward

1. **Owner decision on P2-A.** Re-derive with `all(...)` (recommended) or with
   `lookahead_depth: 1`. Deterministic and byte-reproducible from the same
   inputs; only the planner calibration and, under option 2, the derivation
   rule change. New hashes must then be put in the VAL config.
2. **Implement P1-A** — stop intersecting the Module 20 envelope with Module 7's
   core ceiling, and make Module 20 account for physical spend already incurred
   instead. Small, localised at `build_plan`. Re-check `awardWonBy`'s floor
   against its ceiling afterwards.
3. **Fix the execution mode** — VAL config to `interleaved`, runner to honour
   the config and fail closed, equivalence test extended.
4. Re-run the readiness gate; expect `FULL_VALIDATION_READY`.
5. Scripted production preflight across all six relations.
6. Real-weight smoke, then VAL.

## 12. Verdict

> **HOLD — DO NOT RUN VAL**

The artifacts themselves are in good order: correct bytes, correct provenance,
six relations, 64 bins, no leakage, C-02 truthful, Table 6 respected. The hold
is one property of one file — a planner calibration asking for two-step
lookahead over a history that, for five well-supported terminal-action bins,
has no second step to look at. It is not a sparse-data artefact and not a
conservative guard: the real production graph crashes on the first borders
query.

Resolving it means re-deriving a calibration artifact, which this milestone is
forbidden to do. So the work stops here, exactly as instructed, with the failing
bins named and the two dependent items traced and ready.

---

*No commit, no push. `benchmark/` untouched. No production source modified. No
VAL or TEST row read; no model weights loaded; no artifact edited or
regenerated.*
