# Audit 0079 - V3.2 Deep Weakness Mining And System-Upgrade Engineering

## Scope

CPU-only analysis milestone. No model was run. Four deliverables: a quarantined
human-inspection artifact, a second-pass weakness map of the *current* system, a
structural read of blind TEST, and an evidence-derived V3.2 upgrade plan.

Base SHA: `b9eef10b6b9ba1333f7278e4fcc0b9b672f66357`
(audit 0078's repair is present in the working tree, uncommitted at the time of
this analysis.)

Audit 0078 status, carried forward unchanged: orchestration repair ready,
`TARGETED_RECOVERY_SAFE`, `CALIBRATION_UNAFFECTED`, authoritative TRAIN corpus
uncontaminated.

Per the user's decision, the old frozen V3 TEST artifact will **not** be
submitted, and no GPU time was spent recovering its 39 holes.

## 1. Quarantined Claude Diagnostic - READ THE WARNING

`outputs/v3_test_16f60fb1_20260810T160048Z/claude_diagnostic_only/`

> **DO NOT SUBMIT. NOT COVER-KBC OUTPUT. CLAUDE-AUTHORED DIAGNOSTIC GUESSES.
> NOT GROUND TRUTH. NOT VALID FOR CALIBRATION OR BENCHMARK EVALUATION.**

475 rows: 436 copied verbatim from the failed run, 39 filled with my own
internal guesses. No web, no RAG, no external KB, no API, no file lookup.

| confidence | rows |
|---|---:|
| HIGH | 1 |
| MEDIUM | 15 |
| LOW | 7 |
| UNKNOWN | 16 |

An answer was offered for **8 of 39** rows. The other 31 were left empty on
purpose, and that is the diagnostically interesting part: many of these subjects
are private limited companies (`GmbH`), subsidiaries, historical entities or - in
one case - an industry association, none of which can have a listing at all.
**For this relation an empty prediction is often the correct answer**, which
means an empty stock row is much weaker evidence of failure than it looks. TRAIN
agrees: 34 of 100 stock rows have empty gold.

Original `predictions.jsonl` unmodified (`8a97a5e0…c649f08`).

### Firewall

The guesses live **only** in gitignored `outputs/`; no subject-to-exchange table
exists anywhere in the source tree. Enforced by test:

* no module under `src/cover_kbc/` mentions `claude_diagnostic_only`;
* none of 11 production entry points mentions it;
* no config mentions it;
* exactly one script may name it (`build_claude_diagnostic_patch.py`), and that
  script contains no factual token (`Stock Exchange`, `Nasdaq`, `NYSE`, …);
* the quarantine directory is confirmed gitignored;
* rows marked `UNKNOWN` are asserted to carry no answer.

## 2. Current Baseline

Replay over the authoritative persisted 477-row TRAIN run. Fidelity gate: with
V3.1 disabled the replay reproduces the committed predictions on 477/477 rows.

| Variant | macro-P | macro-R | macro-F1 | micro-F1 |
|---|---:|---:|---:|---:|
| V3_BASELINE | 0.6858 | 0.4660 | 0.40333 | 0.51799 |
| **SAFE_CORE** | 0.6072 | 0.4941 | **0.41853** | 0.53065 |
| SAFE_FULL | 0.6151 | 0.4869 | 0.42000 | 0.53079 |

SAFE_CORE per relation:

| Relation | macro-F1 | P | R | exact | empty | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| countryLandBordersCountry | 0.96437 | 0.987 | 0.956 | 58/67 | 12 | 3 | 11 |
| companyTradesAtStockExchange | 0.52890 | 0.627 | 0.743 | 30/100 | 39 | 95 | 33 |
| awardWonBy | 0.41375 | 0.569 | 0.398 | 0/10 | 1 | 197 | 412 |
| personHasCityOfDeath | 0.39000 | 0.840 | 0.480 | 39/100 | 78 | 16 | 52 |
| hasArea | 0.31000 | 0.410 | 0.310 | 31/100 | 10 | 59 | 69 |
| hasCapacity | 0.08000 | 0.210 | 0.080 | 8/100 | 13 | 79 | 92 |

Overall SAFE_CORE: 166 exact, 54 partial, 238 complete misses, 153 empty
outputs, 449 FP, 669 FN, mean cardinality 2.27.

### Differential (what audits 0076-0077 actually bought)

| Category | Rows |
|---|---:|
| UNCHANGED_CORRECT | 156 |
| UNCHANGED_WRONG | 287 |
| FIXED_BY_SAFE_CORE | 15 |
| FIXED_ONLY_BY_STOCK_DOMINANCE | 12 |
| HARMED_BY_STOCK_DOMINANCE | 7 |

## 3. The Central Finding

**311 SAFE_CORE error rows. 229 of them (74%) are `NO_RECALL` — the gold-like
value was never surfaced as a candidate at all. Only 14 (4.5%) are
selection-recoverable.**

| Stage | Rows |
|---|---:|
| L1_NEVER_RECALLED | 278 |
| L5_FINALIZATION | 33 |

Recall-bound by relation: hasCapacity 92, hasArea 61, personHasCityOfDeath 51,
companyTradesAtStockExchange 24, awardWonBy 1.

### Why selection cannot be improved cheaply

Four measurements, which are one finding:

1. **35 of 2,937 TRAIN candidates were ever verified (1.2%).**
   `companyTradesAtStockExchange`, `countryLandBordersCountry` and `awardWonBy`
   received **zero** verification calls across all 477 rows.
2. **219 of 240 `hasArea` numeric candidates carry independent support 1**
   (`hasCapacity`: 163 of 175). Competing candidates are indistinguishable.
3. **Every relation's contract call cap is fully consumed by Phase A
   acquisition**: award 12.00/12, borders 4.00/4, hasArea 4.00/4, city 3.97/4,
   capacity 3.90/4, stock 4.58/5. The configured
   `max_verifications_per_query: 6` is therefore **nominal** — no calls remain.
4. Consequently three principled numeric-cluster tiebreaks (max support, max
   score, max accepted-support) produce **byte-identical output** to the current
   policy: 0 rows changed.

**The budget buys recall and never buys discrimination, so selection has nothing
to rank on.** That single fact explains why the cheap-rule search below returned
nothing, and it determines the V3.2 architecture.

## 4. Deterministic Rule Search - All Rejected

Every candidate was simulated on TRAIN with the official evaluator.

| Rule | Δ macro-F1 | changed | improved | harmed | Verdict |
|---|---:|---:|---:|---:|---|
| stock: abstain when all accepted candidates are singly supported | **-0.00927** | 36 | 9 | 21 | REJECT |
| city: abstain when all accepted candidates are singly supported | +0.00629 | 22 | 9 | 6 | **REJECT** |
| hasArea: cluster tiebreak by max acquisition support | 0.00000 | 0 | 0 | 0 | REJECT |
| hasArea: cluster tiebreak by max candidate score | 0.00000 | 0 | 0 | 0 | REJECT |
| hasArea: cluster tiebreak by largest representative | -0.01048 | 53 | 5 | 10 | REJECT |

The city rule is **positive on TRAIN and still rejected**, which is the most
important judgement in this audit. Its support distributions barely differ
between empty and non-empty gold (9 vs 16 accepted candidates, all at support 1),
so the gain does not come from evidence — it comes from the 42% empty-gold base
rate. Shipping it would encode a TRAIN prior about how often the relation is
empty, which is a memorised statistic dressed as a rule.

The stock version of the same rule fails outright for the same reason made
visible: 92 of 116 accepted candidates on *correct* stock rows are also singly
supported, so the rule deletes right answers at twice the rate it removes wrong
ones.

**No production behaviour was changed by this audit.** A test asserts no rule was
marked `PROMOTE`, so a later reader can tell "we found nothing worth shipping"
from "we forgot to ship it".

## 5. Oracle Upper Bounds — TRAIN ORACLE UPPER BOUND ONLY

| Scenario | macro-F1 | Δ |
|---|---:|---:|
| SAFE_CORE (actual) | 0.41853 | — |
| perfect selection among already-recalled candidates | 0.50785 | +0.08932 |
| emit every gold-like candidate already present | 0.50775 | +0.08922 |
| remove all false positives, add no recall | 0.48666 | +0.06812 |
| abstain only on empty-gold rows | 0.45837 | +0.03983 |
| perfect recall + perfect finalization | 1.00000 | +0.58147 |

Read carefully: the +0.089 "perfect selection" figure also removes false
positives, and the FP-removal component alone is +0.068. Of that, +0.040 is
attributable purely to abstaining on the 88 empty-gold rows (stock 34, city 42,
borders 12) — and §4 shows the evidence does not support deciding which those
are. The genuinely selection-recoverable residue is **14 rows**.

None of this logic is wired into production; a test asserts no `ORACLE` token
appears under `src/cover_kbc/`.

## 6. Micro-Domains

Discovered by lexical probes over subject strings only (entity *kinds*, never
facts):

| Micro-domain | Relation | Rows | FP | FN | Class |
|---|---|---:|---:|---:|---|
| venue_stadium | hasCapacity | 86 | 73 | 86 | RELATION_SEMANTICS |
| island | hasArea | 23 | 19 | 23 | RELATION_SEMANTICS |
| private_form (GmbH/Ltd/SICAV…) | stock | 15 | 10 | 7 | FINALIZATION |
| lake | hasArea | 8 | 7 | 8 | RELATION_SEMANTICS |
| venue_hall | hasCapacity | 3 | 3 | 3 | RELATION_SEMANTICS |

The dominant numeric mode is `WRONG_NUMERIC_ATTRIBUTE`: hasCapacity 74/92 error
rows, hasArea 51/69. **The wrong number is usually a real figure for the subject —
just the wrong attribute or configuration.** That is a relation-semantics
problem, not an arithmetic one, and it is exactly what audit 0077's capacity
instruction was written for and never tested.

## 7. Blind TEST Structural Findings

Structure only. **No TEST row is labelled correct or incorrect** — a test asserts
the structural analyser references no gold, correctness, accuracy or F1.

330 of 475 rows carry at least one flag:

| Anomaly | Count |
|---|---:|
| EMPTY_OUTPUT | 244 |
| WIDE_NUMERIC_CANDIDATE_SPREAD (≥9x) | 127 |
| SUSPICIOUS_ENUMERATION_LABEL | 84 |
| ORCHESTRATION_FAILURE_HOLE | 39 |
| EXTREME_CARDINALITY | 2 |

Two of these corroborate TRAIN findings independently of any label: the 127
wide-spread numeric rows are the same undifferentiated-candidate problem seen in
§3, and the 84 enumeration labels are the leak audit 0076 fixed at finalization —
present here because this run predates SAFE_CORE. The 39 orchestration holes are
audit 0078's incident.

## 8. Hard-Coded Knowledge Policy

* **Category A (safe universal)** — already shipped in audit 0076: unit
  conversions, numeric parsing, scientific notation, Unicode normalisation,
  deduplication, cardinality semantics, structured-output cleanup. Nothing new
  is justified: 0 structured-output leaks, 0 malformed numerals and 0
  subject-as-object emissions remain in SAFE_CORE output.
* **Category B (relation semantics)** — already wired in audit 0077 as live
  Class-B instructions. No text change is proposed, because none of them has
  been measured yet and changing an unmeasured prompt destroys the experiment.
* **Category C (factual mini-KB)** — `EXTERNAL_FACTUAL_KB_RISK`. Not wired, not
  proposed. The only factual content produced in this milestone is the
  quarantined Claude guess file, which lives in gitignored `outputs/` behind the
  firewall of §1.

## 9. V3.2 Architecture — Two New Modules, Not Six

Full rationale in `outputs/v3_2_weakness_mining/recommended_v3_2_architecture.md`.

```
Relation Profile
      |
Parametric Multi-View Recall          <- unchanged
      |
Numeric / Set Specialist              <- unchanged
      |
Hypothesis Graph                      <- unchanged
      |
Discriminative Verification Budget    <- NEW
      |
Numeric Attribute Resolver            <- NEW (absorbs Capacity Variant Resolver)
      |
M21 Controller                        <- unchanged formula, recalibrated
      |
Relation Contract Finalizer           <- unchanged (audit 0076)
```

**Built:**

1. **Discriminative Verification Budget** — reserve 1-2 of a relation's calls for
   contrastive verification instead of a marginal recall view. Justified by §3:
   1.2% of candidates are verified, and the budget is 100% consumed by
   acquisition.
2. **Numeric Attribute Resolver** — qualifier-aware variant contrast for
   hasArea/hasCapacity. Justified by 125 of 161 numeric error rows being
   `WRONG_NUMERIC_ATTRIBUTE`.

**Not built, and why:**

* *Capacity Variant Resolver* → **merged** into the Numeric Attribute Resolver.
  Capacity qualifiers are one instance of the attribute-variant problem and
  hasArea has the same shape (total vs land vs metropolitan).
* *Listing Disambiguation Layer* + *Award Set Completeness Controller* →
  **merged** into one Set Completeness Controller and **deferred**: both need
  verification evidence that does not exist yet. Suppression without
  verification is precisely the rule §4 measured and rejected.
* *Generic Output Contract Guard* → **already shipped** in audit 0076. The
  failure class it targets is empty.
* *Attribute-Contrast Resolver (city)* → **already wired** in audit 0077; it is
  the existing city diagnostic, not a new module.

`countryLandBordersCountry` stays frozen at 0.964. Five of its rows are
finalization-recoverable and are not worth the regression risk.

## 10. Controller / Action-Space Findings

From telemetry, not speculation:

* the controller does not stop too early in the ordinary sense — it stops because
  the **budget is gone**, at 100% cap utilisation on five of six relations;
* verification starvation is a *budget allocation* consequence, not a planner
  ranking error, so M21's formula is not implicated;
* `awardWonBy` spends its full 12 calls and still ends 9/10 rows partial with
  both 197 FP and 412 FN — expansion and suppression are both failing, which is
  why the Set Completeness Controller is ranked 5th and blocked, not 1st.

## 11. Ranked Improvements

| # | Improvement | Rows | Value | Calibration | Status |
|---:|---|---:|---|---|---|
| 1 | Discriminative Verification Budget | 311 | HIGH | REVIEW REQUIRED | design only |
| 2 | capacity definition-aware recall | 92 | HIGH | REVIEW REQUIRED | **wired, never run** |
| 3 | Numeric Attribute Resolver | 161 | HIGH | REVIEW REQUIRED | design only |
| 4 | city recall binding (enumerator side) | 61 | MEDIUM | REVIEW REQUIRED | proposed |
| 5 | Set Completeness Controller | 89 | MEDIUM | REVIEW REQUIRED | blocked on #1 |
| 6 | stock listing-entity recall | 70 | MEDIUM | REVIEW REQUIRED | **wired, never run** |
| 7 | award expansion + FP cap | 10 | LOW | REVIEW REQUIRED | **wired, never run** |
| 8 | scientific-notation acquisition parser | 0 | LOW | REVIEW REQUIRED | **wired, never run** |
| 9 | borders finalization retention | 5 | LOW | SAFE | rejected (frozen relation) |
| 10 | deterministic abstention policies | 58 | NEGATIVE | SAFE | **measured, rejected** |

Ranks 2, 6, 7 and 8 cost **zero implementation** — they are already wired and
merely unmeasured. That is the cheapest available information in the project.

## 12. What This Audit Implemented

**No production behaviour change.** Deliberately. Every deterministic rule
measured was rejected, and the two justified modules are Class B, so building
them before the diagnostics run would spend the budget on verifying candidates
that better prompts might make unnecessary.

Added: three analysis scripts, one quarantine generator, one test file, this
audit. `src/cover_kbc/` is untouched by this milestone.

## 13. Answers To The Required Questions

1. **Largest remaining SAFE_CORE failure modes** — `NO_RECALL` (229 rows),
   `TOO_MANY_FALSE_POSITIVES` (217), `WRONG_NUMERIC_ATTRIBUTE` (125).
2. **Still recall-bound** — 229 of 311 (74%): capacity 92, area 61, city 51,
   stock 24, award 1.
3. **Deterministically repairable** — essentially none remain; all five candidate
   rules were rejected. Audit 0076 harvested this class.
4. **Need better prompt semantics** — hasCapacity (74/92 wrong-attribute),
   hasArea (51/69), personHasCityOfDeath (29 candidates over 100 rows).
5. **Justify a dedicated module** — verification budget allocation; numeric
   attribute variants.
6. **Overlapping modules to merge** — capacity variant resolver into the numeric
   resolver; listing disambiguation and award completeness into one deferred
   controller; output contract guard already exists.
7. **Systematic numeric mistakes** — wrong *attribute*, not wrong arithmetic:
   the emitted number is usually a real figure for the subject.
8. **Systematic set mistakes** — simultaneous over- and under-enumeration (stock
   40 over / 16 under; award 9/10 rows both).
9. **Stops too early** — nowhere by choice; it stops at 100% budget utilisation.
10. **Expands too much** — stock (40 rows) and award (197 FP), both without any
    verification to arbitrate.
11. **Theoretically fixable without a new model call** — 14 rows (4.5%) are
    selection-recoverable, and no available signal distinguishes them.
12. **Require better factual recall** — 229 rows (74%).
13. **Top 5 by expected value** — ranks 1-5 above.
14. **Safe with current calibration** — none of the proposed work; the safe class
    is exhausted.
15. **Require the new TRAIN/M21 cycle** — all of ranks 1-8.

## 14. Plan Forward

**GPU experiment matrix** (`outputs/v3_2_weakness_mining/gpu_experiment_matrix.md`):
five TRAIN-only, single-relation, single-feature diagnostics, all already wired
and never run. Order unchanged from audit 0077: capacity, city, stock, award,
area parser.

**Full TRAIN collection** — after promotion, using
`configs/experiments/cover_kbc_v3_1_train_collection.yaml` (audit 0077, prepared
and inert), under the fixed audit-0078 orchestration, new namespace
`outputs/v3_1_train_collect_class_b`, never overwriting V3 calibration.

**M21** — re-derived from the new collection into `configs/calibration/v3_1/`.
Historical M21 not overwritten.

**M20 decision status: `m20_policy = undecided`**, unchanged. Prompt changes
alone do not justify rebuilding a budget scheduler. *However*, if the
Discriminative Verification Budget is adopted, that **is** a spend-pattern change
and M20 re-derivation becomes a live question — to be answered from the new
collection's coverage and saturation evidence, not assumed.

**Then** a fresh full TEST from the final commit. The old TEST run and the
quarantined patched copy remain historical artifacts only and are never spliced
in.

## 15. Tests

New: `tests/test_v3_2_weakness_mining.py` (**33**) — firewall, quarantine
integrity, analysis contracts, oracle containment, calibration hashes,
no-production-change assertion.

```bash
python -m pytest tests/ -q -p no:randomly   # 3927 passed, 4 skipped
python -m pytest tests/ -q                  # 3927 passed, 4 skipped
python -m pyflakes src/ tests/ scripts/     # clean
git diff --check                            # clean
```

Audit 0078's baseline was `3894 passed, 4 skipped`; the difference is exactly the
33 new tests.

Calibration artifacts verified byte-identical (all 7 hashes).

## Verdict

**PASS — V3.2 WEAKNESS MAP AND UPGRADE PLAN READY**

The system is in a clean state to begin real-model targeted diagnostics: the
weakness map is rebuilt against the current system rather than the pre-repair
one, the cheap-rule class is provably exhausted, the architecture proposal is
two modules rather than six and each is tied to a measurement, and five
zero-implementation-cost diagnostics are wired and waiting.

The honest headline is that **74% of what remains is recall, not machinery** —
which is precisely why the next milestone is GPU diagnostics and not more CPU
engineering.
