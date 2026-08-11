# Audit 0080 - hasCapacity Real-Weight Targeted Diagnostic And Promotion Decision

**Status: PHASE B — COMPLETE**
**PROMOTION: REJECTED**

`capacity_definition_prompt` was measured on 100 real-weight TRAIN rows and is
**rejected**. It does not improve recall of the correct capacity attribute; it
suppresses candidates.

---

# PHASE A — PRE-RUN

## 1. Scope

One relation, one intervention, one causal question:

> Does the already-wired capacity-definition-aware prompt improve factual recall
> of the *correct capacity attribute*, rather than merely producing more numbers?

No city, stock, award or area experiment is mixed in. No TEST. No model change.
No calibration change.

## 2. Source Freeze

At the start of this milestone the working tree carried completed but
uncommitted Audit 0078 and Audit 0079 work:

```
 M scripts/run_cover.py            (+75)
 M src/cover_kbc/pipeline.py       (+32/-2)
?? src/cover_kbc/run_accounting.py
?? 6 analysis/recovery scripts
?? 3 test files
?? docs/audits/0078-*.md, 0079-*.md
```

A GPU experiment must not be launched from an ambiguous tree, so this milestone
requires **one clean checkpoint commit** before the run. The runbook checks out
that exact SHA in detached HEAD and asserts the tree is clean.

First attempted pre-run commit: `3de4db0385c5dec1049ff5082047eca0545972a9`
(aborted before inference — see 2b)

Hotfix pre-run commit / **real diagnostic source SHA**:
`a7dfb5d68a55b83fab267a0b5e490972df10a275`

`HEAD` before the checkpoint: `b9eef10b6b9ba1333f7278e4fcc0b9b672f66357`
Frozen historical TEST source, untouched: `16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

**After the checkpoint commit, source must not change until the run completes.**

---

## 2b. First Colab Attempt — PRE-RUN INFRASTRUCTURE FAILURE, NO EXPERIMENT RESULT

Recorded rather than hidden. **The capacity prompt remains completely
unmeasured.**

Attempted source SHA: `3de4db0385c5dec1049ff5082047eca0545972a9`

What happened:

| Stage | Outcome |
|---|---|
| model weights (28.7B) | loaded successfully |
| relation filter | correct — printed `relation filter ['hasCapacity']: 100 rows` |
| query inference | **never started** |
| return code | `1` |
| `predictions.jsonl` | not created |
| `run_accounting.json` | not created |
| `manifest.json` | not created |

Abort message:

```
configs/experiments/v3_1_diag_capacity.yaml declares production mode for split
'train'; a production leaderboard run is defined only for ['test', 'val']
```

**Classification: PRE-RUN INFRASTRUCTURE FAILURE / NO EXPERIMENT RESULT.**
Zero diagnostic rows were evaluated, so nothing about `capacity_definition_prompt`
can be inferred — positively or negatively — from this attempt.

### Root cause

`scripts/run_cover.py`, production-activation block:

```python
if split == "train" and _diagnostics_enabled(config):
    gate, required = TRAIN_DIAGNOSTIC_GATE
else:
    gate, required = PRODUCTION_GATES.get(split, (None, None))   # -> None for train
if gate is None:
    raise SystemExit(...)
```

`_diagnostics_enabled` reads the **top-level `diagnostics.enabled`** block — the
V3A telemetry recorder switch. The audit-0077 diagnostic configs declare
`experiment.diagnostic: true`, which is a *different field* and which nothing in
the runner reads. So the TRAIN branch was never taken, `PRODUCTION_GATES` has no
`train` entry, and the run was refused.

A gate for this exact use case already existed —
`evaluate_train_diagnostic_readiness` / `TRAIN_DIAGNOSTIC_READY`, built for the
V3A labelled-TRAIN run — and the diagnostic configs simply never opted into it.

### Why the audit-0080 pre-run checks missed it

Every pre-run check, including this audit's own §4 and the runbook's CELL 9,
interrogated the **config**: feature sets, relation filter, TEST inaccessibility,
readiness-is-not-TEST-ready, prompt hashes. All of those were and remain correct.

None of them exercised the **runner's dispatch**. The bug was not in the config's
content but in whether `run_cover.py` would route that content to a gate at all,
and no test called that routing. Audit 0077 shipped five diagnostics that passed
every static assertion and could not execute.

### Second defect, same incident

The guard fired *after* `build_runtime`, so the refusal cost a full 28.7B
parameter download. A guard that is free to evaluate but runs after the
expensive step costs exactly what it exists to save.

## 2c. The Hotfix

**Config change (the actual fix), 5 files.** Each targeted diagnostic now opts
into the pre-existing TRAIN diagnostic gate by declaring what that gate has
always required:

```yaml
train_dataset:
  path: benchmark/data/train.jsonl
  rows: 477
  sha256: ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e
  identity_sha256: 04b56aa6f401f00ca8d672a8d6cade09cfa2492640aac16a9aab8bc42c1b8054
  labelled: true

diagnostics:
  enabled: true
  telemetry_file: inference_telemetry.jsonl
```

**No guard was weakened**, because no guard was changed. This is the mechanism
§3 asked to be preferred: a safer pre-existing path already intended for this
use case. A side benefit is that the run now emits `inference_telemetry.jsonl`,
the same candidate-level artifact the authoritative baseline has and the one
`analyze_capacity_diagnostic.py` reads.

| Config | TRAIN gate | TEST readiness |
|---|---|---|
| `v3_1_diag_capacity.yaml` | `TRAIN_DIAGNOSTIC_READY` | `NOT_READY` |
| `v3_1_diag_city.yaml` | `TRAIN_DIAGNOSTIC_READY` | `NOT_READY` |
| `v3_1_diag_stock.yaml` | `TRAIN_DIAGNOSTIC_READY` | `NOT_READY` |
| `v3_1_diag_award.yaml` | `TRAIN_DIAGNOSTIC_READY` | `NOT_READY` |
| `v3_1_diag_area_parser.yaml` | `TRAIN_DIAGNOSTIC_READY` | `NOT_READY` |

**Runner change (cost, not policy).** The gate resolution and readiness
evaluation were extracted into `resolve_production_gate()` and
`evaluate_production_readiness()` and moved **before** `build_runtime`. The
decision rules are byte-identical; only their position and reusability changed.
Extraction is what lets the tests and the runbook interrogate the real function
instead of a copy of its rules.

### Safety properties preserved

* a plain TRAIN production config **without** `diagnostics.enabled` is still
  refused — the branch requires it;
* `diagnostics.enabled` cannot open a TEST run, because the branch tests
  `split == "train"` first;
* an unknown split is still refused;
* `val` and `test` still take their own gates unchanged;
* the frozen `cover_kbc_v3_test.yaml` and both SAFE submission configs still
  evaluate `FULL_TEST_READY`;
* the relation filter still refuses blind splits outright.

Each of these is a test, not a claim.

### One collateral fix

Three pre-existing tests in `tests/test_production_source_fixes.py` broke on the
reorder. The cause was in their harness, not in the change: `_drive_main` copies
the VAL config into `tmp_path`, which breaks its relative
`../calibration/...` paths. Previously that went unnoticed because the readiness
gate ran after `build_runtime` and the stub raised first. The harness now
absolutises those paths against the real config directory; the three tests are
otherwise untouched and assert exactly what they asserted before.

## 2d. The Experiment Is Unchanged

Verified by test after the hotfix:

| | Value |
|---|---|
| system prompt OFF | `2fb9188dbeda44f3` |
| system prompt ON | `bcffa96f37770392` |
| capacity instruction sha256 | `27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36` |
| causal intervention | `capacity_definition_prompt`, and only that |
| models / revisions | unchanged |
| M20 / M21 / calibration | unchanged, all 7 hashes byte-identical |
| audit-0078 orchestration repair | unchanged and active |
| relation filter | `[hasCapacity]` → 100 rows |

## 3. Audit-0078 Orchestration Fix Is Active

Proven from source and by test, not assumed.

`CoverPipeline._phase_b_roles` (`src/cover_kbc/pipeline.py:1414`):

| Execution mode | Phase-B allowed roles |
|---|---|
| `interleaved` | `enumerator`, `verifier`, `none` |
| `staged` | `verifier`, `none` (unchanged) |

`tests/test_pending_action_orchestration.py`: **42 passed**, including the
byte-exact incident reproduction (`RUN_FACET` / enumerator / 1 call remaining)
which fails on the pre-repair source and passes here.

This matters for capacity specifically: the incident fired only on the one
relation with a spare call at the Phase-B boundary. Capacity currently spends
3.90 of its 4-call cap, so it can arrive at Phase B with budget left — and the
new prompt may change that spend. The fix must be active before the run, and it is.

## 4. Diagnostic Is Capacity-Only

`configs/experiments/v3_1_diag_capacity.yaml`, sha256
`f51839e1db243ad4396a93cce313a077d9ba7e1c6a4a973d6debe47046ec7834`

| Requirement | Value | |
|---|---|---|
| split | `train` | OK |
| relation filter | `[hasCapacity]` | OK |
| expected rows | `100` | OK |
| `test_dataset` block | absent | OK |
| `capacity_definition_prompt` | `true` | OK |
| `city_of_death_contrast_prompt` | `false` | OK |
| `stock_listing_entity_prompt` | `false` | OK |
| `award_expansion_and_fp_cap` | `false` | OK |
| `scientific_notation_acquisition` | `false` | OK |
| TEST readiness | `NOT_READY` | OK |
| calibration status | `CALIBRATION_REVIEW_REQUIRED` | OK |

**One change made in this milestone.** The config inherited the SAFE_FULL
finalization block, which had `stock_support_dominance` and
`stock_structural_validation` on. Both are relation-gated to
`companyTradesAtStockExchange` and are therefore *unreachable* in a
capacity-only run — but §4 requires one causal intervention and nothing else, so
they are now explicitly `false` rather than merely argued to be inert.

The three remaining SAFE features stay on and are measured no-ops for capacity:
audit 0076 recorded **0 hasCapacity rows changed** by final-candidate retention
and **0** by numeric canonicalisation, and enumeration-label repair is an
entity-relation path. Audit 0079 confirms it: capacity macro-F1 is `0.08000`
under both V3_BASELINE and SAFE_CORE. The finalization stack therefore adds no
confound to the comparison.

## 5. Model Contract

Unchanged from the calibrated system. No fine-tuning, no web, no RAG, no
external corpus.

| Role | Model | Revision | Parameters |
|---|---|---|---:|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 |

Total 28,671,226,368 of a 32,000,000,000 limit — legal.

## 6. Prompt Provenance — The Experiment Is Valid

The rendered request was produced through the real path
(`ElicitationEngine.system_prompt_for` → `GenerationRequest.system_prompt`), not
read off a constant.

| Version | Value |
|---|---|
| live prompt version | `v3.1-live-prompts-v1` |
| declared prompt version | `v3.1-prompts-v1` |
| capacity instruction body sha256 | `27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36` |

| View | system OFF | system ON | user OFF | user ON | changed |
|---|---|---|---|---|---|
| `capacity_direct` | `2fb9188dbeda44f3` | `bcffa96f37770392` | `03a3bbdaf17becee` | `03a3bbdaf17becee` | **yes** |
| `capacity_contrast` | `2fb9188dbeda44f3` | `bcffa96f37770392` | `0bb5871d96d6a5b6` | `0bb5871d96d6a5b6` | **yes** |

System prompt length `211` → `1933` characters. The user turn is deliberately
unchanged: the instruction is a standing system-prompt instruction, so view
identity, independence groups and facet accounting are untouched.

**`EXPERIMENT VALID: the rendered request differs OFF vs ON.`** The runbook
re-asserts both hashes in CELL 9 and aborts if either drifts.

Semantic coverage of the ON instruction, verified by substring:

| Distinguishes | | Rejects | |
|---|---|---|---|
| seated | yes | attendance | yes |
| standing | yes | area | yes |
| sport configuration | yes | construction cost | yes |
| concert configuration | yes | dates | yes |
| historical | yes | seat numbers | yes |
| post-renovation | yes | dimensions | via "size, area or footprint" |
| temporary/event | yes | | |

No TRAIN answers: the body contains no 4-or-more-digit number and no benchmark
subject.

## 7. Baseline — Authoritative, Not Re-Run

`outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`
predictions sha256 `36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816`

Reproduced exactly against §7's stated expectations:

| Metric | Value |
|---|---:|
| rows | 100 |
| macro-P / macro-R / **macro-F1** | 0.210 / 0.080 / **0.08000** |
| exact rows | 4 |
| within 5% | 8 |
| empty predictions | 13 |
| large misses | 48 |
| too large / too small | 64 / 15 |
| ratio buckets | `~1x` 68, `~10x` 9, `~100x` 1, other 1 |
| total candidates | 175 |
| distinct numeric values | 45 |
| rows with any candidate | 87 |
| mean candidates per row | 2.0115 |
| **gold-like candidate rows** | **8** |
| **gold-like seen but not emitted** | **0** |

The last two lines set up the whole experiment. Capacity has **no selection
loss today**: whenever a gold-like candidate exists it is already emitted. The
relation is bounded purely by recall, so this diagnostic tests the one lever
that can move it.

## 8. Output Namespace

`outputs/v3_2_capacity_diag_<SOURCE_SHA[:12]>_<UTC>/`, mirrored to
`MyDrive/AKBC/Diagnostics/capacity_<SOURCE_SHA[:12]>_<UTC>/`.

Nothing existing is overwritten. Expected artifacts: `predictions.jsonl`,
`calls.jsonl`, `trace.jsonl`, `run_accounting.json`, `manifest.json`, plus the
module telemetry (`query_profiles`, `prompt_programs`, `parametric_memory`,
`numeric_specialist`, `atomic_consensus`, `specialist_verification`,
`bidirectional_verification`, `v3_hypothesis_graph`, `layer4_evidence`,
`coverage_gap`, `relation_budget`, `micro_planner`), the run log, prompt hashes,
source SHA and an environment snapshot.

## 9. Runbook

`docs/runbooks/0080-capacity-diagnostic-colab.md` — 15 copy-paste cells. The only
manual edit is `SOURCE_SHA` in CELL 3.

Deliberate choices: `transformers` is **not** downgraded, and
`flash-linear-attention` is **not** installed — the warning it silences is
cosmetic and the package has caused version conflicts. Nothing auto-deletes the
runtime or the outputs.

## 10. GPU Command

```bash
python scripts/run_cover.py \
  --config configs/experiments/v3_1_diag_capacity.yaml \
  --output-dir outputs/v3_2_capacity_diag_<SHA12>_<UTC> \
  --no-eval
```

Relation filtering is enforced by the config's own `experiment.relation_filter`
(audit 0077 mechanism, a membership test on the query's relation name that reads
no gold). The run prints `relation filter ['hasCapacity']: 100 rows`.

**If the row count is not exactly 100: STOP.**

## 11. Run Accounting Gate

Audit 0078's accounting must report, and the runbook asserts:

```
total_queries               = 100
prediction_rows             = 100
failed_queries              = 0
unresolved_invariant_errors = 0
pipeline_error_rows         = 0
```

Any `PendingActionNotConsumed` or other invariant failure means the run is
**contaminated**, not a negative result about the prompt. Analysing it would
attribute an orchestration problem to the intervention.

## 12-13. Instrumentation

`scripts/analyze_capacity_diagnostic.py` (new, CPU-only) emits per candidate:
subject, raw text, parsed value, unit, semantic role, source views, acquisition
groups, independent support, score, verifier label and probability, hypothesis
status, emitted flag; and offline against TRAIN gold: gold value, gold-like flag,
ratio, ratio bucket.

Ratio buckets: `WITHIN_5_PERCENT`, `BETWEEN_1_05_AND_2X`, `2X_TO_5X`,
`5X_TO_20X`, `20X_TO_200X`, `OVER_200X` and the five reciprocal too-small
categories.

Semantic roles are matched against the candidate's **own inference-time
metadata** — surface forms, facet ids and the view/output text that produced it.
An unmatched candidate stays `UNKNOWN`; no label is invented. Roles:
`CANONICAL_CAPACITY`, `SEATED_CAPACITY`, `STANDING_CAPACITY`, `SPORT_CAPACITY`,
`CONCERT_CAPACITY`, `HISTORICAL_CAPACITY`, `POST_RENOVATION_CAPACITY`,
`TEMPORARY_CAPACITY`, `ATTENDANCE`, `AREA`, `COST`, `YEAR`, `DIMENSION`,
`OTHER_NUMERIC`, `UNKNOWN`. None is used in production.

## 15. The Primary Metric Is Not Final F1 Alone

This is a **recall** intervention, so candidate recall and final prediction
quality are reported separately and never collapsed.

A large rise in gold-like candidate rows with only a modest F1 gain would be a
*positive* result about the prompt and direct evidence for the Discriminative
Verification Budget — the downstream bottleneck audit 0079 predicted. Equally,
more candidates with no more gold-like rows is a rejection.

Baseline anchor: gold-like candidate rows `8`, of which `0` fail to be emitted.

## 16. Transition Matrix

Every one of the 100 rows is classified baseline → diagnostic:
`NO_RECALL -> GOLD_LIKE_RECALLED_AND_EMITTED`,
`NO_RECALL -> GOLD_LIKE_RECALLED_NOT_EMITTED`,
`NO_RECALL -> STILL_NO_RECALL`,
`WRONG_ATTRIBUTE -> CORRECT_ATTRIBUTE`,
`WRONG_ATTRIBUTE -> DIFFERENT_WRONG_ATTRIBUTE`,
`EMPTY -> CORRECT`, `EMPTY -> WRONG_NONEMPTY`,
`CORRECT -> WRONG`, `CORRECT -> CORRECT`.

## 17. Calibration Untouched

No M20 re-derivation. No M21 re-derivation. No calibration file edited.
Verified byte-identical:

| Artifact | SHA256 |
|---|---|
| `configs/calibration/v3/m20_relation_budget.json` | `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29` |
| `configs/calibration/v3/m21_historical_bins.json` | `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675` |
| `configs/calibration/v3/m21_planner_calibration.json` | `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6` |
| `configs/calibration/v3/calibration_provenance.json` | `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d` |

The existing calibration is **intentionally stale** with respect to the
experimental prompt. That is acceptable for controlled diagnosis and is not
acceptable for production. `CALIBRATION_REVIEW_REQUIRED` stands.

## 18. Promotion Gate — Fixed Before The Run

From `src/cover_kbc/v3_1/promotion.py` (written in audit 0077, unchanged):

> **PROMOTE** if relation macro-F1 rises materially above 0.080, **or** if
> gold-like candidate rows rise substantially while macro-precision does not
> collapse. The second clause matters because capacity is recall-starved:
> 92/100 rows never recalled the answer at all, so a prompt that surfaces the
> canonical figure as a *candidate* has done the hard part even if selection has
> not caught up — and selection is cheap to fix afterwards.
>
> **REJECT the pass** if `mean_candidates_per_row` rises without
> `gold_like_candidate_rows` rising with it — that is the prompt manufacturing
> numeric alternatives rather than recalling the right one. Also reject if the
> 10x/100x ratio buckets grow: a scale error is not a recall win.
>
> **NEEDS_MORE_EVIDENCE** if the result is mixed or unstable.

No minimum gain is invented after seeing the result.

## 19-20. Downstream Hypotheses To Be Sized

Not implemented in this milestone — measured only.

* **Discriminative Verification Budget** —
  `verification_budget_opportunity.csv` counts rows with ≥2 distinct plausible
  numeric candidates where one is gold-like. Baseline reference: 6 such rows.
* **Numeric Attribute Resolver** — `numeric_resolver_opportunity.csv` counts
  rows where a gold-like candidate exists but a different attribute is emitted.
  Baseline reference: **0** — the module has no target population today, and
  this diagnostic will show whether the prompt creates one.

## 21. Protecting The 8 Baseline-Correct Rows

`baseline_correct_regressions.csv` lists every row that was within 5% at
baseline and is not after. Each regression is reported individually. A recall
prompt that destroys already-correct cases is risky even if aggregate recall
rises.

## 24. Pre-GPU Test Gate

```bash
python -m pytest tests/ -q -p no:randomly   # 3978 passed, 4 skipped
python -m pytest tests/ -q                  # 3978 passed, 4 skipped
python -m pyflakes src/ tests/ scripts/     # clean
git diff --check                            # clean
```

New: `tests/test_targeted_diagnostic_executability.py` (**51**) — the regression
class that was missing. It calls the runner's own
`resolve_production_gate` / `evaluate_production_readiness` rather than
restating their rules, covers all five diagnostics positively, and covers the
negative cases: plain TRAIN production refused, missing `telemetry_file`
refused, `diagnostics.enabled` unable to open TEST or an unknown split,
val/test gates unchanged, frozen and SAFE configs unaffected, prompt hashes and
model contract unchanged, and readiness provably evaluated before
`build_runtime`.

Targeted suites: orchestration repair, live prompt wiring, weakness-mining
firewall and recovery tooling — **197 passed**.

## Phase A Verdict

**GPU_RUN_REQUIRED** — satisfied at `a7dfb5d`; see Phase B.

Everything that could be verified without weights was: the source was
freezable, the orchestration repair active, the config isolated to exactly one
intervention, the model contract matched the calibrated lineage, the rendered
prompt provably differed between OFF and ON, the baseline reproduced to the
digit, and the promotion gate was fixed before any data existed.

That the gate was pre-registered is what makes Phase B's rejection meaningful
rather than a threshold chosen after seeing the numbers.

---

# PHASE B — POST-RUN

## B1. Chronology

The record is kept intact; none of these steps is removed.

| # | Event | Outcome |
|---|---|---|
| 1 | attempt 1 @ `3de4db0` | **PRE-RUN INFRASTRUCTURE FAILURE — NO EXPERIMENT RESULT** (§2b) |
| 2 | hotfix commit | targeted TRAIN diagnostics made executable (§2c) |
| 3 | attempt 2 @ `a7dfb5d` | **VALID 100/100 REAL-WEIGHT DIAGNOSTIC** |
| 4 | analysis | **CPU ANALYZER FIELD BUG** — raw inference still valid (§B6) |
| 5 | permanent analyzer fix | `git_revision`, with regression tests |
| 6 | decision | `capacity_definition_prompt` = **REJECTED** |

## B2. Run Validity

Source SHA: `a7dfb5d68a55b83fab267a0b5e490972df10a275`
Runner reported `TRAIN_DIAGNOSTIC_READY`, return code `0`.

| Accounting field | Value |
|---|---:|
| total_queries | 100 |
| prediction_rows | 100 |
| successful_queries | 100 |
| failed_queries | **0** |
| unresolved_invariant_errors | **0** |
| pipeline_error_rows | **0** |
| query errors | **0** |

Relation distribution: `hasCapacity = 100`. **PROCESS-LEVEL GATE: PASS.**

This is a real result about the prompt, not an infrastructure artifact. No GPU
rerun was needed at any point in phase B.

## B3. Metrics — Baseline vs Diagnostic

Baseline is the authoritative persisted TRAIN run; it was not re-run.

| Metric | Baseline | Diagnostic | Δ |
|---|---:|---:|---:|
| macro-P | 0.210 | **0.510** | **+0.300** |
| macro-R | 0.080 | **0.030** | **-0.050** |
| **macro-F1** | **0.080** | **0.030** | **-0.050** |
| total GT | 100 | 100 | 0 |
| total predictions | 87 | 52 | **-35** |
| true positives | 8 | 3 | **-5** |
| correct rows | 8 | 3 | -5 |
| empty rows | 13 | **48** | **+35** |

### Candidate recall — the pre-registered primary question

| | Baseline | Diagnostic | Δ |
|---|---:|---:|---:|
| total candidates | 175 | 124 | **-51** |
| mean candidates / row | 1.75 | 1.24 | -0.51 |
| **gold-like candidate rows** | **8** | **9** | **+1** |

Ratio bucket `WITHIN_5_PERCENT`: **8 → 3**.

Baseline-correct regressions: **6** of the 8 originally correct rows.

Downstream opportunity counts on the diagnostic run:
`verification_budget_rows = 9`, `numeric_resolver_rows = 6`.

## B4. Promotion Verdict — REJECTED

The pre-registered gate (audit 0077 `promotion.py`, written before any data
existed) offered two routes:

> **PROMOTE** if relation macro-F1 rises materially above 0.080, **or** if
> gold-like candidate rows rise substantially while macro-precision does not
> collapse.
> **REJECT the pass** if `mean_candidates_per_row` rises without
> `gold_like_candidate_rows` rising with it.

Neither route opens:

* **Route A fails outright.** macro-F1 fell `0.080 → 0.030`, recall fell
  `0.080 → 0.030`, true positives fell `8 → 3`.
* **Route B fails on "substantially".** Gold-like candidate rows moved `8 → 9`.
  One row is not a substantial recall gain, and it is bought at the cost of five
  true positives and 35 emitted answers.

**The precision rise from 0.210 to 0.510 is not evidence of better recall.** It
is arithmetic: empty rows went from 13 to 48, and an empty prediction scores
precision 1.0. The model answered 35 fewer questions and was right 5 fewer
times. A system that abstains more looks more precise while knowing less.

Note that the rejection is *not* the mirror image of the anticipated failure
mode. The gate's REJECT clause was written for "more candidates, no more
gold-like rows". What actually happened is the opposite direction —
**fewer** candidates — and it still fails, because the deciding quantity in both
cases is gold-like recall, which did not move.

**FINAL PROMOTION VERDICT: `REJECTED`.**

Consequently: not enabled in any SAFE or production config; no full TRAIN
recollection with it; no M20 or M21 re-derivation on its account.

## B5. Architectural Interpretation

**Stated as an inference from one relation's behaviour, not a proven universal
mechanism.**

The observation is that loading detailed capacity-definition semantics into the
*acquisition* prompt made the enumerator **suppress** rather than enumerate:
candidates fell 175 → 124 and empty rows rose 13 → 48, while the number of rows
containing a gold-like value barely moved. The plausible reading is that asking
one model call to simultaneously (a) recall the fact, (b) decide which capacity
variant the relation means, and (c) reject every competing variant, causes the
third instruction to dominate. Told precisely what *not* to say, the enumerator
says less — including things it would otherwise have said correctly, which is
what the six baseline-correct regressions are.

This favours separating the jobs:

```
BROAD PARAMETRIC ACQUISITION      <- ask for candidates, not for judgement
        |
   candidate pool
        |
DISCRIMINATIVE VERIFICATION       <- decide between variants here
        |
NUMERIC ATTRIBUTE RESOLUTION      <- canonicalise and choose
        |
  relation finalization
```

over asking the enumerator to do all three at once.

This is consistent with, but does not by itself prove, the audit-0079 finding
that the system verifies 35 of 2,937 candidates (1.2%) and spends its whole
call budget on acquisition. The **Discriminative Verification Budget** and
**Numeric Attribute Resolver** remain justified by that broader TRAIN evidence,
not by this single experiment. Neither is built in this milestone; audit 0080
closes the capacity experiment and nothing more.

One caveat worth recording: the diagnostic ran under the Audit-0073 calibration,
which was derived against the *old* evidence distribution. Some of the
suppression may be M21 reacting to an evidence shape it was not calibrated for.
That does not rescue the feature — the candidate pool shrank before any
controller decision — but it means the magnitude should not be over-read.

## B6. Analyzer Provenance Bug — CPU Only, Raw Artifacts Untouched

`scripts/analyze_capacity_diagnostic.py` compared the expected commit SHA
against `manifest["cover_kbc_version"]`. That field is the **package version**
(`0.1.0`) and is identical on every commit this project has made; the source
commit lives in `manifest["git_revision"]`. The comparison could therefore never
pass, and the analyzer refused a valid run.

* The bug is **CPU-side analysis only**. The GPU run was already complete and
  correct.
* A temporary one-line copy of the analyzer was used in Colab to obtain the
  result. **It is not added to this repository** — a test asserts no
  `*hotfix*` file exists under `scripts/`.
* **No raw inference artifact was modified**: predictions, telemetry, manifest,
  calls and the run log are exactly as the GPU produced them.

Permanent fix applied to the canonical analyzer:

```python
observed_sha = str(manifest.get("git_revision") or "")     # source provenance
package_version = str(manifest.get("cover_kbc_version") or "")   # recorded, never compared
```

A missing `git_revision` is now itself a refusal, rather than silently skipping
the check.

## B7. Preserved Negative Evidence

This is scientifically useful negative evidence and is retained in full:
real run source SHA, run manifest, predictions, telemetry, analysis outputs and
provenance, the analyzer-hotfix attestation above, baseline identity, the metric
and candidate-recall tables, ratio buckets, the six baseline-correct
regressions, and the verification/resolver opportunity rows.

Raw inference artifacts are neither overwritten nor renamed. The failed
attempt 1 remains in §2b.

## B8. Unchanged By This Milestone

| | |
|---|---|
| M20 / M21 | not re-derived, not edited |
| all 7 calibration artifacts | byte-identical |
| SAFE_CORE / SAFE_FULL | unchanged; `capacity_definition_prompt` not enabled anywhere |
| models and revisions | unchanged |
| audit-0078 orchestration repair | unchanged and active |
| capacity prompt body and hashes | unchanged (`2fb9188dbeda44f3` → `bcffa96f37770392`) |
| raw GPU artifacts | not mutated |

The capacity prompt remains wired and available behind its Class-B flag, now
carrying a measured negative result rather than an untested hypothesis.

## B9. Next Diagnostic — Recommendation

Audit 0079's ordering put city ahead of stock. **This result inverts that.**

| | personHasCityOfDeath | companyTradesAtStockExchange |
|---|---:|---:|
| macro-F1 | 0.390 | 0.529 |
| macro-P / macro-R | 0.840 / 0.480 | 0.627 / 0.743 |
| error rows | 61 | 70 |
| NO_RECALL rows | **51** | 24 |
| FP / FN | 16 / 52 | **95** / 33 |
| candidates across 100 rows | **29** (0.29/row) | 908 (9.08/row) |
| Class-B owner | **verifier** boundary | **enumerator** prompt |
| wired | yes | yes |
| cost | 100 rows | 100 rows |

**Recommend stock next.**

The capacity experiment showed that a Class-B instruction of this kind acts as a
*suppressor*. That is the wrong medicine for city and plausibly the right one
for stock:

* **City** is recall-starved — 29 candidates across 100 rows, 51 NO_RECALL, and
  already at precision 0.840. Its instruction is verifier-side, and a verifier
  cannot create recall; it can only reject. The most likely outcome is precision
  0.840 → higher and recall 0.480 → lower, which is the capacity result again in
  a different relation. Low expected information.
* **Stock** is candidate-rich and precision-bound: 908 candidates, 95 FP against
  33 FN. Suppression is exactly what it needs, and its instruction is
  enumerator-side, the same lever capacity just demonstrated is potent. Whether
  that potency helps where precision is the binding constraint is genuinely
  unknown — which is what makes it worth a run.

The stock gate already guards the obvious failure: *reject if the FP reduction
comes from `mean_predictions` collapsing toward 1*, because multi-listed
companies are real.

City should be deferred until there is a recall-side intervention to test, or
until the Discriminative Verification Budget gives its 29 candidates something
to be discriminated by.

**No GPU experiment is started automatically.**
