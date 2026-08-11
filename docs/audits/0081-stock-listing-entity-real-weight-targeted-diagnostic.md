# Audit 0081 - Stock Listing-Entity Real-Weight Targeted Diagnostic

**Status: PHASE A — GPU_RUN_REQUIRED**

No result is written here. `stock_listing_entity_prompt` has never met a model.

---

# PHASE A — PRE-RUN

## 1. Source

`AUDIT_0081_BASE_SHA = f4315a53158f422ced4f02f6eae2248e420bd00e`
(`finalize rejected capacity diagnostic and fix provenance analysis`)

Working tree was **clean** at the start of this milestone. The SHA was read from
`git rev-parse HEAD`, not carried over from an earlier milestone.

The Colab runbook checks out a detached SHA and asserts a clean tree; that SHA is
the commit the user makes at the end of this milestone.

## 2. Scientific Question

One relation, one causal intervention:

```
stock_listing_entity_prompt = true      (enumerator, Class B)
relation = companyTradesAtStockExchange
rows     = 100 TRAIN
```

The pre-registered question is **not** "does precision rise".

> Does listing-entity semantics remove false stock-exchange candidates faster
> than it destroys true listed-exchange recall, while preserving genuine
> multi-listing cardinality?

Audit 0080 rejected a prompt whose precision rose `0.210 → 0.510` purely because
empty predictions rose `13 → 48`. For stock — a suppression experiment — that
failure mode is the *expected* confound rather than a surprise, so the whole
analysis is built to separate good suppression from destructive abstention.

## 3. Why Stock Follows Rejected Capacity

Audit 0080's measured outcome, and its labelled inference: loading strong
semantic exclusions into the *acquisition* prompt made the enumerator suppress
(candidates `175 → 124`, empty rows `13 → 48`, TP `8 → 3`, gold-like rows only
`8 → 9`, six of eight correct rows regressed).

That makes this prompt class a **suppressor**. Suppression is the wrong medicine
for a recall-starved relation and plausibly the right one where precision binds.
Stock is the candidate-rich, FP-heavy relation:

| | city | **stock** |
|---|---:|---:|
| NO_RECALL rows (audit 0079) | 51 | 24 |
| FP / FN | 16 / 52 | **95 / 33** |
| candidates over 100 rows | 29 | **908** |
| Class-B owner | verifier | **enumerator** |

Whether a demonstrated suppressor helps where precision is the binding
constraint is genuinely unknown, which is what makes the run worth its cost.

## 4. Baseline — Recomputed, Not Copied

Recomputed from the authoritative artifact and TRAIN gold at this SHA. Where a
prior narrative figure disagrees, the recomputation is authoritative and the
difference is stated.

Baseline run:
`outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`
predictions sha256 `36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816`

### Final output (SAFE_CORE background)

| Metric | Value |
|---|---:|
| rows | 100 |
| macro-P | 0.62750 |
| macro-R | 0.74333 |
| **macro-F1** | **0.52890** |
| TP / FP / FN | 48 / 95 / 33 |
| exact | 30 |
| partial | 36 |
| complete miss | 24 |
| empty predictions | 39 |
| total predicted objects | 143 |
| mean / median / max cardinality | 1.43 / 2.0 / 7 |

### Gold shape, derived from TRAIN

| | rows |
|---|---:|
| empty gold | **34** |
| single gold | **54** |
| multi gold (2 or 3) | **12** |
| total gold objects | 81 |

Cardinality distribution: `{0: 34, 1: 54, 2: 9, 3: 3}`.

**A third of this relation's gold is legitimately empty.** Suppression on those
34 rows is a real win; on the other 66 it is the capacity failure.

### Population breakdown

| Population | Finding |
|---|---|
| empty gold (34) | 24 already correctly empty; **27 FP objects** emitted across the other 10 |
| non-empty gold (66) | 15 already predicted empty |
| single gold (54) | exact-only **2**, gold+extra **32**, wrong-only 6, empty 14 |
| multi gold (12) | exact-set 4, collapse-to-singleton 0, under 2, over 3, gold-object recall **14/27**, mean card 2.33 |

**Correction to a prior narrative figure.** Audit 0075 reported "37 single-gold
rows emitting multiple exchanges". The recomputation against the current
SAFE_CORE baseline gives **32** single-gold rows emitting gold-plus-extra (plus 6
emitting only wrong exchanges). The 37 came from a pre-SAFE_CORE analysis with a
different definition. **32 is the figure Phase B compares against.**

### Candidate forensic

| | Value |
|---|---:|
| total candidates | **908** |
| mean / median / p90 / max per row | 9.08 / 13.0 / 17 / 22 |
| rows with 0 candidates | **39** |
| rows with ≥2 / ≥5 / ≥10 | 58 / 58 / 58 |
| gold-like candidate rows | **42** |
| distinct gold objects surfaced anywhere | 19 |

Two things stand out. The 39 zero-candidate rows are exactly the 39 empty
predictions — those empties are acquisition failures, not selection choices. And
the distribution is bimodal: a row has either nothing or a dozen-plus candidates.

### Verification starvation (recomputed)

| | Value |
|---|---:|
| total verifier calls | **0** |
| candidates ever verified | **0 / 908** |
| mean calls per query | 4.58 (cap 5) |

Audit 0079's finding reproduces exactly. Stock discriminates among 908
candidates with zero verification. Recorded as evidence for the future
Discriminative Verification Budget; **not implemented here**.

## 5. Diagnostic Config

`configs/experiments/v3_1_diag_stock.yaml`, sha256
`e29837be2432d8c34a3d1acb81580d4281529444e3655c5b26aab126d1259712`

| Requirement | Value | |
|---|---|---|
| split | `train` | OK |
| relation_filter | `[companyTradesAtStockExchange]` | OK |
| expected_rows | 100 | OK |
| `experiment.diagnostic` | true | OK |
| `diagnostics.enabled` | true | OK |
| `train_dataset` | present | OK |
| `test_dataset` | **absent** | OK |
| TEST readiness | `NOT_READY` | OK |
| calibration status | `CALIBRATION_REVIEW_REQUIRED` | OK |
| `stock_listing_entity_prompt` | **true** | OK |
| all other Class-B features | false | OK |

## 6. Confound Removed — SAFE_CORE Background

**This is the substantive config change of the milestone.**

The config inherited the SAFE_FULL block, which enables `stock_support_dominance`.
Unlike audit 0080 — where both stock features were provably unreachable in a
capacity run — here it is **live and material**. Measured by replay at this SHA:

| Variant | macro-P | macro-R | macro-F1 | stock rows changed vs V3 |
|---|---:|---:|---:|---:|
| V3 baseline | 0.62750 | 0.74333 | 0.52890 | 0 |
| **SAFE_CORE** | 0.62750 | 0.74333 | **0.52890** | **0** |
| SAFE_FULL | 0.67417 | 0.69500 | 0.53590 | **22** |

So:

* **SAFE_CORE is a measured no-op for stock** — 0 rows changed, macro-F1
  identical to the V3 baseline. It adds no confound, and the audit-0077 gate's
  ~0.529 baseline is therefore the right comparison point.
* **SAFE_FULL changes 22 stock rows** and moves relation macro-F1 to 0.53590.
  Leaving it on would have compared unlike systems and credited or blamed the
  prompt for a finalization rule's effect.

`stock_support_dominance` is now `false` in the diagnostic. `SAFE_CORE` and
`SAFE_FULL` themselves are **unmodified** — a test asserts SAFE_FULL still
enables it.

## 7. Model Contract — Unchanged

| Role | Model | Revision | Parameters |
|---|---|---|---:|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 |

Total 28,671,226,368 / 32,000,000,000 — legal. No fine-tuning, no LoRA, no
training, no third model, no revision change.

## 8. Data And Evaluator

Verified from disk at this SHA:

| | |
|---|---|
| TRAIN rows | 477 |
| TRAIN sha256 | `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e` |
| declared ordered identity | `04b56aa6f401f00ca8d672a8d6cade09cfa2492640aac16a9aab8bc42c1b8054` |
| stock rows | 100 |
| evaluator sha256 | `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22` |

The runner reads no TRAIN gold. Gold is joined **only** by the offline CPU
analyzer, after inference. No Audit-0081 path opens TEST.

## 9. Prompt Provenance — Measured From Source

| | Value |
|---|---|
| live prompt version | `v3.1-live-prompts-v1` |
| instruction body sha256 | `39af1c5be480c85350535f75ddd2466b497d1b124bb4e3b97c63b21bda1c39f8` |
| matches audit 0077 | **yes** |

Rendered through the real path (`ElicitationEngine.system_prompt_for` →
`GenerationRequest.system_prompt`):

| View | system OFF | system ON | user OFF | user ON | changed |
|---|---|---|---|---|---|
| `stock_listing_gate` | `2fb9188dbeda44f3` | `849a83f0c40440d1` | `da4f0098aee8076b` | `da4f0098aee8076b` | **yes** |
| `stock_exchange_direct` | `2fb9188dbeda44f3` | `849a83f0c40440d1` | `faf1219d2cb2465a` | `faf1219d2cb2465a` | **yes** |

System prompt `211 → 1122` chars. User prompts unchanged — the binding is a
standing system instruction, so view identity, independence groups and facet
accounting are untouched. **`EXPERIMENT VALID: OFF ≠ ON`.**

These hashes were **measured first and pinned second**; they were not asserted
before being observed.

Answer-type coverage, verified by substring: names `exchange`; excludes `ticker`,
`index`, `segment`, `broker`, `clearing`, `depositary`, `company itself`,
`parent`, `subsidiary`, `country`, `city`, and covers `not publicly listed`.
No 4-or-more-digit number and no named exchange appears in the body.

**The prompt is not redesigned in this milestone.** We measure the wired
audit-0077 intervention.

## 10. Pre-Registered Promotion Gate

Fixed before any GPU data exists.

**PROMOTE only if BOTH hold:**

* **A.** FP decreases by more than FN increases.
* **B.** macro-F1 ≥ the matching SAFE_CORE baseline (0.52890).

**A nominal pass is SPURIOUS / REJECTED if any of these hold:**

* mean final cardinality collapses toward 1 in a way that destroys genuine
  multi-listing rows;
* non-empty-gold rows become empty substantially more often (baseline: 15 of 66);
* multi-gold object recall collapses (baseline: 14/27);
* baseline-correct rows regress materially (baseline exact: 30);
* precision rises primarily because more rows are left empty (baseline empty: 39);
* candidate suppression removes gold-like candidates at a rate comparable to or
  greater than false candidates;
* the effect merely duplicates SAFE structural filtering rather than changing
  listing semantics.

Verdict is exactly one of `PROMOTED` / `REJECTED` / `NEEDS_MORE_EVIDENCE`. **No
numeric gain threshold will be invented after seeing results.**

## 11. Analyzer

`scripts/analyze_stock_diagnostic.py` — new, CPU only, stock-specific. Not the
capacity analyzer with a relation string swapped: stock is a set relation with a
legitimately empty and legitimately multi-valued gold, and those populations are
reported separately.

Gold matching reuses the official evaluator's `normalize_string` and its
alias-group rule (one gold entity absorbs at most one prediction). There is no
second matcher.

Provenance uses audit 0080's corrected rule: `manifest["git_revision"]`, never
`cover_kbc_version`; a missing `git_revision` fails closed. A run analysed with
`--allow-provenance-mismatch` may not inform a promotion.

Outputs: `summary.json`, `baseline_stock_metrics.json`,
`diagnostic_stock_metrics.json`, `metric_delta.json`,
`stock_candidate_transition_ledger.csv`, `population_breakdown.csv`,
`improved_rows.csv`, `harmed_rows.csv`, `unchanged_rows.csv`,
`baseline_correct_regressions.csv`, `multi_listing_regressions.csv`,
`empty_gold_fixes.csv`, `destructive_abstention_rows.csv`,
`analysis_provenance.json`, `SHA256SUMS.txt`.

Transition labels, most destructive first:
`DESTRUCTIVE_ABSTENTION`, `MULTI_LISTING_COLLAPSE`, `GOLD_CANDIDATE_LOST`,
`RECALL_GAIN`, `MULTI_LISTING_PRESERVED`, `GOLD_PRESERVED_FP_REDUCED`,
`GOOD_EMPTY_GOLD_SUPPRESSION`, `GOOD_FP_SUPPRESSION`, `NEW_FALSE_CANDIDATE`,
`CANDIDATES_CHANGED_OUTPUT_UNCHANGED`, `UNCHANGED`.

Precedence is deliberate and tested: emptying a real row is reported as
`DESTRUCTIVE_ABSTENTION` even when it also reduced false positives, so a
precision win can never mask it.

Suppression efficiency is reported as `false_removed / (gold_like_removed + ε)`
and `fp_reduction / (fn_increase + ε)`. **Offline diagnostic summaries only —
never production thresholds.**

## 12. Executability — Verified Before Any Weights

Audit 0080 showed static config checks are insufficient. These call the runner's
own functions:

| Check | Result |
|---|---|
| `resolve_production_gate` | routes to `TRAIN_DIAGNOSTIC_GATE` |
| `evaluate_production_readiness` | `TRAIN_DIAGNOSTIC_READY` |
| `_resolve_relation_filter` | `['companyTradesAtStockExchange']` → **100 rows** |
| TEST readiness | `NOT_READY` |
| Class-B enabled | `('stock_listing_entity_prompt',)` |

## 13. Run Command And Namespace

```bash
python scripts/run_cover.py \
  --config configs/experiments/v3_1_diag_stock.yaml \
  --output-dir /content/stock_diag_<SHA12>_<UTC> \
  --no-eval
```

Inference writes to local Colab SSD, then is copied to Drive — streaming
high-frequency telemetry directly to a mounted Drive is slow and can truncate.

* run: `outputs/v3_2_stock_diag_<SHA12>_<UTC>/` (local `/content/...` in Colab)
* Drive: `MyDrive/AKBC/Diagnostics/stock_<SHA12>_<UTC>/`
* analysis: `MyDrive/AKBC/Diagnostics/stock_<SHA12>_<UTC>_analysis/`

Audit 0080's artifacts and the authoritative baseline are never overwritten.

Runbook: `docs/runbooks/0081-stock-diagnostic-colab.md`.

## 14. Hard Validity Gate

The run is valid only if:

```
total_queries = 100   prediction_rows = 100   successful_queries = 100
failed_queries = 0    unresolved_invariant_errors = 0   pipeline_error_rows = 0
relations = {"companyTradesAtStockExchange": 100}
```

Any `PendingActionNotConsumed`, pipeline error, accounting invariant, row
mismatch or foreign relation **contaminates** the experiment. A contaminated run
is not a negative result about the prompt and must not produce a promotion
decision.

## 15. Calibration Immutability

All seven historical artifacts verified byte-identical at this SHA and pinned by
test:

| Artifact | SHA256 |
|---|---|
| `configs/calibration/v3/m20_relation_budget.json` | `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29` |
| `configs/calibration/v3/m21_historical_bins.json` | `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675` |
| `configs/calibration/v3/m21_planner_calibration.json` | `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6` |
| `configs/calibration/v3/calibration_provenance.json` | `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d` |
| `configs/calibration/m20_relation_budget.json` | `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68` |
| `configs/calibration/m21_historical_bins.json` | `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071` |
| `configs/calibration/m21_planner_calibration.json` | `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` |

M20 and M21 are not re-derived and no calibration file is regenerated. The
existing calibration is **intentionally stale** with respect to the experimental
prompt: acceptable for controlled diagnosis, not for production.

## 16. Not Implemented Here

Deliberately out of scope: Discriminative Verification Budget, Numeric Attribute
Resolver, city acquisition redesign, Set Completeness Controller, new M20, new
M21. Audit 0081 is one clean causal experiment.

## 17. Phase-B Contract

After the artifact returns, Phase B must, in order: validate provenance;
validate 100/100 accounting; compare official metrics; compare FP reduction
against FN increase; inspect empty-gold suppression separately; inspect
non-empty-gold abstention; inspect single-gold over-enumeration (against the
recomputed 32, not the historical 37); inspect multi-gold cardinality
preservation (against 14/27); inspect candidate-level gold loss versus
false-candidate removal; inspect baseline-correct regressions (against 30 exact);
inspect verification and call changes; apply the pre-registered gate; and choose
exactly one of `PROMOTED` / `REJECTED` / `NEEDS_MORE_EVIDENCE`.

If `PROMOTED`, TEST does **not** become ready. The order stays: promote → full
TRAIN collection → coverage check → derive new M21 → decide M20 on spend
evidence → readiness validation → only then a fresh TEST.

## Phase A Verdict

**GPU_RUN_REQUIRED.**

Everything verifiable without weights is verified: the source is clean and
freezable, the config isolates one causal intervention on an exactly-SAFE_CORE
background, the confound that would have made the comparison meaningless was
found and removed, the model contract matches, the rendered prompt provably
differs OFF vs ON with hashes measured before being pinned, the baseline is
recomputed rather than copied (including one correction to a prior narrative
figure), the analyzer distinguishes good suppression from destructive
abstention, and the promotion gate is fixed before any data exists.

No result is claimed.
