# Audit 0033 — Full M0–M21 Architecture Conformance

Status: **PASS** (amended in place — see §10A and §16A)
Date: 2026-08-06
Amended: 2026-08-06, after review found two defects this audit had passed:
Module 9's Appendix-C input was **not** conformant, and Module 17's physical
call count was quoted from a synthetic fixture rather than its live
configuration. Both were executable and both are fixed.
Milestone: **whole-architecture conformance audit**. Not a module, not
calibration, not a performance experiment, not production activation.

---

## 1. Executive verdict

**PASS.** Every numbered module M0–M21 has concrete source, every layer-boundary
seam is implemented, the eight-layer map matches the proposal, all six relation
flows are coherent end to end, and every invariant established by an earlier
corrective pass still holds *at the top of the stack*.

Enabling the entire upgraded stack changes **no production prediction**: the
scripted six-relation run produces identical objects, identical stop reasons and
identical Module 7 budget usage with the upgraded layers on and off.

**Two defects were found on review and corrected** (§10A, §16A); both were
executable, not audit wording. The verdict stands only because both are fixed
and regression-tested.

---

## 2. Scope

In scope: architecture existence, seam conformance, ownership coherence,
Appendix-C I/O compatibility, the six relation flows, cross-layer invariants,
call accounting, closed-book and no-training compliance, model profile,
benchmark integrity, output contract, determinism and shadow isolation.

Out of scope and not performed: calibration of any kind, real-weight execution,
TRAIN/VAL/TEST, leaderboard submission, DoLa, production activation.

---

## 3. Authoritative proposal

`./COVER_KBC_Technical_Proposal_New.pdf` is the sole architecture contract, and
it wins over prior prompts, older proposal versions, stale comments and stale
audit wording. Read for this milestone: §2.3, §3, §3.2, §4 and the module
responsibility map, §5–§17 (M9–M21), §9.3, §11.1–§11.2, §16 Table 6, §17.1,
§20.1–§20.6, §21 and §21.2, and Appendices A, B and C.

No material disagreement between the proposal and the implementation was found.

---

## 4. Audit history reviewed

All 32 prior audits, with particular attention to the corrective findings a
later layer could silently resurrect:

| Audit | Corrective finding re-checked here |
| --- | --- |
| 0006/0008 | canonical origin identity; F/L/X/C/U semantics; `q_g = max` |
| 0012 | M0–M8 conformance, `Budget` semantics, staged roles |
| 0022 §11.1/§17A | border minimal change; stock static-vs-local cross-family |
| 0024 | `FAILED_RECALL_ONLY` never substantive NULL |
| 0025/0026 | verifier blindness; M18 evidence-only, four mechanisms |
| 0027 §20A | cardinality-aware `ALTERNATE_RECOVERED` |
| 0028 §9A | stock facet ownership is M15, not M13 |
| 0029 | numeric novelty uses M12's cluster identity |
| 0030/0031 | uncalibrated M20/M21; §16 and §17 contracts |
| 0032 §15A/§15B | stock reserve routing; §17A two-level legality gate |

§14, §22, §23, §30 and §31 below re-assert each at full-system level.

---

## 5. Definition of PASS

**PASS does not mean** production M20/M21 are calibrated, the upgraded
controller is active, model quality is validated, or the leaderboard improved.

**PASS means** every architecture module and contract exists; every required
seam is implemented; ownership boundaries are coherent; module I/O is
compatible; all six relation flows are representable; the mandatory invariants
hold; shadow modules cannot alter baseline production; and the architecture is
safe to proceed to a **real-model runtime smoke** and later to **TRAIN
calibration**.

---

## 6. Current production architecture

```
M0/M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8
```

Unchanged and reproducible, with the audited staged role-swapping runtime.
Scripted six-relation run: **14 neural calls**, and its predictions are the
reference the whole upgraded stack must not disturb.

---

## 7. Upgraded shadow super-system

```
M0/M1
  -> M9 / M10 / M11
  -> M12 / M13 / M14 / M15
  -> M2 / M3 / M16
  -> M4 / M17 / M18
  -> Layer4EvidenceState
  -> M5 / M6 + M19
  -> M7 + M20 + M21 + Layer-6 integration
  -> M8
```

This is **not** the active production policy and its modules are **not** merely
disconnected diagnostics — the seams are real and were audited individually
(§10). What is inactive is the *execution bridge*: Module 21 selects a shadow
action and nothing runs it (§40).

Scripted six-relation run: **58 neural calls**, i.e. **44 shadow neural calls**
by M11, M12–M15, M17 and M18, attributed as shadow spend and not charged to
Module 7's budget.

---

## 8. Eight-layer map

| Layer | Modules | Status |
| --- | --- | --- |
| 0 Semantic foundation | M0 relation compiler, M1 typed program router | production |
| 1 Query/prompt orchestration | M9, M10, M11 | shadow |
| 2 Relation specialists | M12, M13, M14, M15 | shadow |
| 3 Acquisition/evidence | M2, M3, **M16** | M2/M3 production; M16 shadow |
| 4 Verification | M4, **M17**, **M18**, Layer-4 integration | M4 production; rest shadow |
| 5 Coverage/uncertainty | M5, M6, **M19**, Layer-5 integration | M5/M6 production; M19 shadow |
| 6 Test-time control | M7, **M20**, **M21**, Layer-6 integration | M7 production; rest shadow |
| 7 Decision | M8 final selector + relation finalisation | production |

---

## 9. M0–M21 responsibility matrix

| Module | Source | Neural | Status | Upstream | Downstream | Config | Artefact | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M0 | `contracts/registry.py` | no | production | query | M1 | — | — | 0012 |
| M1 | `contracts/programs.py` | no | production | M0 | all | — | `query_manifest.json` | 0012 |
| M2 | `elicitation/engine.py` | **yes** | production | M1 | M3 | `pipeline` | `calls_enumerate.jsonl` | 0012 |
| M3 | `evidence/graph.py` | no | production | M2 | M4/M5 | — | `trace.jsonl` | 0012 |
| M4 | `verification/blind.py` | **yes** | production | M3 | M5 | `pipeline` | `calls_verify.jsonl` | 0012 |
| M5 | `types.py` | no | production | M3/M4 | M6 | — | `diagnostics.json` | 0012 |
| M6 | `coverage.py` | no | production | M5 | M7 | — | `diagnostics.json` | 0012 |
| M7 | `controller.py` | no | production | M6 | M2/M4/M8 | `pipeline` | `trace.jsonl` | 0010 |
| M8 | `selection.py` | no | production | M7 | output | `pipeline` | `predictions.jsonl` | 0012 |
| M9 | `query_intelligence/profiler.py` | no | shadow | M0/M1 | M10/M20/M21 | `query_intelligence` | `query_profiles.jsonl` | 0016 |
| M10 | `query_intelligence/prompt_compiler.py` | no | shadow | M9 | M11/specialists | `query_intelligence` | `prompt_programs.jsonl` | 0017 |
| M11 | `query_intelligence/parametric_retrieval.py` | **yes** | shadow | M10 | specialists | `query_intelligence` | `parametric_memory.jsonl` | 0018 |
| M12 | `specialists/numeric_specialist.py` | **yes** | shadow | M10/M11 | M16 | `specialists` | `numeric_specialist.jsonl` | 0019 |
| M13 | `specialists/large_set_specialist.py` | **yes** | shadow | M10/M11 | M16 | `specialists` | `large_open_set_specialist.jsonl` | 0020 |
| M14 | `specialists/null_temporal_specialist.py` | **yes** | shadow | M10/M11 | M16 | `specialists` | `null_temporal_specialist.jsonl` | 0021/0024 |
| M15 | `specialists/small_set_specialist.py` | **yes** | shadow | M10/M11 | M16/M18 | `specialists` | `small_set_specialist.jsonl` | 0022 |
| M16 | `evidence/consensus.py` | no | shadow | M3+specialists | Layer 4 | `consensus` | `atomic_consensus.jsonl` | 0023 |
| M17 | `verification/specialist_verifier.py` | **yes** | shadow | M16 | Layer 4 | `specialist_verifier` | `specialist_verification.jsonl` | 0025 |
| M18 | `verification/bidirectional_verifier.py` | **yes** | shadow | M16 | Layer 4 | `bidirectional_verification` | `bidirectional_verification.jsonl` | 0026 |
| Layer 4 | `evidence/layer4.py` | no | shadow | M16/M17/M18 | M19 | `layer4_integration` | `layer4_evidence.jsonl` | 0027 |
| M19 | `coverage_gap/missingness.py` | no | shadow | Layer 4 | M21 | `coverage_gap` | `coverage_gap.jsonl` | 0028/0029 |
| M20 | `control/relation_budget.py` | no | shadow | M9 + budget | Layer 6 | `relation_budget_scheduler` | `relation_budget.jsonl` | 0030 |
| M21 | `control/micro_planner.py` | no | shadow | full state | — | `micro_planner` | `micro_planner.jsonl` | 0031 |
| Layer 6 | `control/layer6_integration.py` | no | shadow | owners/M20/M21 | — | `layer6_integration` | `layer6_control.jsonl` | 0032 |

Verified from source, not copied from audit prose
(`test_every_numbered_module_has_concrete_source`). All 22 entries exist; no
M22 file, class or reference (`test_no_module_22_and_no_dola`).

---

## 10. Appendix-C I/O matrix

Verified against the **actual current type names**, and against a live run where
the consumer really reads the producer's object
(`test_the_appendix_c_io_seams_connect`).

| Module | Declared input → output | Actual types | Verdict |
| --- | --- | --- | --- |
| M9 | QuerySpec + early graph → RiskProfile + route hints | `Query`, `RelationContract` → `QueryRiskProfile`; `refine(profile, graph)` → refined profile | **CONFORMANT** (after §10A) |
| M10 | contract + profile → PromptProgram | + `QueryRiskProfile` → `PromptProgram` | **CONFORMANT** |
| M11 | PromptProgram + state → records/candidates | → `ParametricRetrievalResult` | **CONFORMANT** |
| M12 | numeric spec + evidence → cluster state | → `NumericSpecialistResult` / `NumericClusterState` | **CONFORMANT** |
| M13 | set spec + graph → facet plan/evidence | → `LargeSetSpecialistResult` | **CONFORMANT** |
| M14 | query + temporal/null state → existence/locality | → `NullTemporalSpecialistResult` | **CONFORMANT** |
| M15 | query + small-set state → closure/pending | → `SmallSetSpecialistResult`, `PendingCheck` | **CONFORMANT** |
| M16 | evidence events → candidate consensus | → `QueryConsensusResult` | **CONFORMANT** |
| M17 | candidate + contract → calibrated labels | `VerificationTarget` → `SpecialistVerificationResult` | **CONFORMANT** |
| M18 | candidate/query + contract → structural records | `EligibleCheck` → `StructuralCheckResult` | **CONFORMANT** |
| M19 | graph + facet registry → residual/gap | `Layer4EvidenceState` → `CoverageGapState` | **CONFORMANT** |
| M20 | relation + risk + budget → envelopes | + `CoreBudgetSnapshot` → `RelationBudgetPlan` | **CONFORMANT** |
| M21 | full state + legal actions → action/STOP | `PlannerStateSnapshot` + `PlannerActionCandidate` → `MicroPlannerDecision` | **CONFORMANT** |

Query identity `(subject, relation, row_index)` is carried unchanged from M16
through Layer 4, M19, M20 and Layer 6, and version provenance
(`layer4_version`, `planner_version`) is carried rather than re-derived.

**No PARTIALLY CONFORMANT or NON-CONFORMANT seam was found.**

---

## 10A. Correction — Module 9's Appendix-C input (amendment)

**Defect found on review. Executable, not audit wording.**

This audit originally marked M9 **CONFORMANT** on the strength of
`Query + RelationContract → QueryRiskProfile`. That verdict was not justified.
The proposal says three things this audit had to reconcile rather than restate:

* **§5**: M9 "reads the relation, subject surface form, **initial graph**, and
  **early-return signals**" and produces
  `Q = (q_card, q_temp, q_num, q_open, q_amb, q_novel, q_verify)`;
* **Appendix C**: `QuerySpec + early graph → RiskProfile + **route hints**`,
  described as a *dynamic* risk vector;
* **§5.1 Table 3**: each relation has a primary specialist **and** a secondary
  path.

Audit 0016 had deliberately deviated — *"no graph exists yet. M9 takes the query
only"* — and deliberately omitted `q_novel`, because the then-current milestone
brief placed M9 before graph construction. That was reasonable for M16, but this
audit's own rule is that **the root proposal wins over prior milestone briefs**,
so the deviation had to be re-decided here, not inherited.

### Is the proposal internally inconsistent?

Nearly, and the resolution matters. §20.1 step 2 runs M9 *before* any view
executes, while §5 and Appendix C give it the initial graph and early-return
signals. Both are true only if **M9 is evaluated twice**: a static profile from
the query alone, then a deterministic refinement once early evidence exists.
"Early-return signals" only exist after something has returned, and Appendix C
calls the vector *dynamic*, so this is the reading the proposal's own wording
forces. This is the brief's option **B**, and no new architecture was invented.

### Before-state, verified by inspection

| Question | Answer before |
| --- | --- |
| A. Does M9 accept any early graph? | **No** — `profile(query, contract)` only |
| B. Does M9 expose `q_novel`? | **No** — the other six axes were present |
| C. Route hints beyond `specialist_hint`? | **No** — Table 3's secondary column was absent |
| D. Does a later module supply the missing semantics? | Partly: M19 has `noveltyRate` |
| E. Does that make M9 conformant? | **No** — Appendix C assigns the input to *M9* |

D and E are the distinction the brief insists on: system-level functional
coverage is not module conformance, and M19's residual lives at Layer 5, after
verification, which is not where a Layer-1 difficulty prior belongs.

### Minimal correction

* `QueryRiskProfile` gained `secondary_hints`, `novelty_risk` and
  `novelty_basis`.
* `SecondaryRoute` transcribes Table 3's secondary column once, and
  `SECONDARY_ROUTES` maps each relation to it — static and advisory, routing
  nothing.
* `QueryProfiler.refine(profile, graph)` is the graph-aware half. It returns a
  **new** profile, mutates neither the old one nor the graph, refuses to refine
  across queries, and moves **only** `q_novel` — every other axis is a static
  property of the relation and its subject surface, and letting early evidence
  move one would turn a prior into a measurement.
* The pipeline records the refined profile once the early graph exists.

### q_novel semantics

`None` means **unmeasured**, which is the honest state before any evidence —
the same principle as M19's "unavailable is never zero". When an early graph
exists the grade is read **only** from early-return structure:

| Early graph | `q_novel` |
| --- | --- |
| no acquisition record | `None` — unmeasured |
| records returned, zero candidates | HIGH |
| exactly one candidate | MEDIUM |
| more than one candidate | LOW |

Those boundaries are **structural** (nothing / exactly one / more than one), not
fitted thresholds, and no TRAIN, VAL or external data was consulted. Audit 0016
was right that guessing obscurity from a subject string is not an acceptable
estimator, and that is tested directly: two very different subject strings with
identical early returns receive an identical grade.

**This is not M19's `noveltyRate`.** M19 measures, per discovery origin and
across a whole run, the fraction of identities first seen at the latest eligible
origin — a residual *search-need* signal at Layer 5. M9's `q_novel` is a
one-shot instance-difficulty prior read from the first returns at Layer 1.
Different quantity, different layer, different time, and neither is derived from
the other; the profiler references no residual and M19 references no profile.

### Verdict

**CONFORMANT** after the correction. Had the proposal's ambiguity been
irreducible, the honest verdict would have been PARTIALLY CONFORMANT and the
architecture PASS would have been held; it is not, so it is not.

---

## 11. Module ownership

No two modules claim authoritative ownership of one semantic state. Truth
ownership: acquisition M2/M3, verification M4/M17/M18, consensus M16, coverage
M6 (production) and M19 (shadow), control M7 (production) with M20/M21 shadow,
final answer M8 alone.

The specialists are **shadow** and deliberately do not mutate M3's graph; their
evidence reaches the architecture through M16 and the Layer-4 projection, which
is the audited path (§13). Semantic equivalence was audited, not directory
names.

---

## 12. Identity and normalisation

One candidate traced end to end: raw surface → strict normalisation → M3
identity → specialist evidence → M16 `candidate_key` → M17/M18 target → Layer-4
overlay → M19 unit → Layer-6 action target → M8 output. The key is the strict
Module 3/16 identity at every hop; `alias_hint` is never global identity, and
there is no fuzzy merge and no embedding identity model.

**Numeric**: M12's cluster identity is authoritative downstream — Layer 4 copies
`representative`, `dispersion` and `independent_support` field-for-field, and
neither M19 nor M21 re-clusters, re-tolerances or applies the evaluator's 5 %
(`test_numeric_cluster_identity_is_module_12s_through_every_layer`).

**NULL_SINGLE**: the query-level proposition is never an entity candidate — it
is one `query_existence_state` unit and `NO_KNOWN_QUALIFYING_LOCALITY` never
appears as a candidate key.

---

## 13. Evidence provenance

Every evidence edge names the event that produced it; there is no anonymous
evidence. Specialist evidence enters through M16's adapters, carrying canonical
origin identity, and Layer 4 projects it without minting new origins.

---

## 14. F/L/X/C/U/I/D

Re-asserted on the **composed** Layer-4 state, which is where a later layer
would undo it:

| Term | Meaning | Held |
| --- | --- | --- |
| F | core audited acquisition support only | ✓ |
| L | calibrated verifier logit evidence | ✓ |
| X | genuine independent cross-model recall only | ✓ |
| C | audited core contradiction; specialist structural contradiction kept structural | ✓ |
| U | M4 prompt-distribution disagreement only | ✓ |
| I | specialist structural support / incidence | ✓ |
| D | semantic/typed disagreement channel | ✓ |

Critical regressions all hold
(`test_verification_never_becomes_acquisition_at_the_top_of_the_stack`):
ordinary SUPPORT → F only; `CROSS_MODEL_RECALL` → X only when genuinely
independent; blind verifier VALID → L only, INVALID → L + signed contradiction;
shown-candidate verifier agreement never F or X; M18 reverse, counterfactual and
key-condition never X; candidate-free may affect X only under Audit 0027's
distinct-family provenance rule.

`q_g = max`: every structural group reports `q_g ≤ 1` and never a sum of repeats
(`test_repeated_support_is_never_summed`). No downstream re-counting was found.

---

## 15. Independence groups

A mechanism is one group however many times it is sampled; a facet slice is
provenance, not extra independence; a resample keeps its group. Verification
groups (`m17:`, `core:BLIND_VERIFIER`, `core:EXISTENCE_GATE`, `M18_REVERSE`,
`M18_COUNTERFACTUAL`, `M18_KEY_CONDITION`) are excluded from discovery by
construction; `M18_CANDIDATE_FREE_RECALL` is the single principled exception.

---

## 16. Physical calls vs logical actions vs evidence events

Three distinct identities, maintained across the stack:

* one M17 verification request → one logical action, **8 physical calls cold
  and 4 warm** under the shipped configuration (§16A), one factual mechanism;
* one M18 candidate-free generation naming five candidates → **one** physical
  call, five candidate observations;
* one M11 call mined by M13/M15 → **one** call, not two;
* Layer-4 projection duplicates no call;
* M19, M20, M21 and Layer 6 → **zero** calls each.

Module 20's replay reconciles nine records representing three physical calls to
three, collapsing six duplicates, and fails loudly on conflicting metadata for
one call id.

---

## 16A. Correction — Module 17's physical call count (amendment)

**Defect found on review. Executable, not audit wording.**

§16 originally said "3–6 physical calls depending on cache". That figure came
from Audit 0030's **synthetic scheduler fixture**, not from Module 17's live
configuration, and quoting it here let a test fixture stand in for a production
number.

Worse, the same mistake was in the code. The Layer-6 M17 adapter declared
`readings: int = 1, control_calls_needed: int = 0, controls_total: int = 0`, and
the pipeline seam never overrode them — so the live Module 17 action was priced
at **one** call against Module 20's hard cap, understating its true cost by up
to eightfold. A hard cap is only as good as the number it is checked against.

### The correct rule

```
factual readings = enabled template phrasings x enabled label orders
cold safe cost   = readings + currently uncached controls
warm safe cost   = readings + only the controls still missing
```

`m17_call_plan(config)` derives this from Module 17's **own configuration**, and
the Layer-6 adapter and the pipeline seam pass the live config through. Changing
the configured phrasings or orders now moves the safe cost automatically, which
is asserted directly.

### Current shipped defaults

`template_ids = ("m17_statement_v1", "m17_question_v1")`,
`label_orders = (ABC, BAC)`, `use_calibration = True`:

| | Readings | Controls | Safe physical cost |
| --- | --- | --- | --- |
| **cold** | 4 | 4 | **8** |
| **warm** | 4 | 0 | **4** |

Two phrasings × two label orders = four factual readings, one contextual control
each. Cold **8**, warm **4** — not 3 and 6.

### Hard-cap regression

With the live plan, a ledger of exactly 8 reserves the action; a ledger of 7
**denies it before execution** with `DENIED_BY_HARD_CAP`, leaving
`committed_calls == 0` — no partial reservation, nothing executed
(`test_one_call_short_of_the_cold_m17_plan_is_denied_before_execution`). Cold
strictly exceeds warm, and both remain the **same semantic action** with
identical canonical identity.

### Scope

Cost planning only. Module 17's A/B/C contract, contextual calibration, `T = 1`,
verifier blindness, template and label-order disagreement, factual mechanism
identity and the F/L/X/C/U semantics are untouched.

---

## 17. Neural runtime accounting

Every neural call goes through the audited `LMRuntime.generate` /
`score_labels` surface. The only direct `model.generate` in the repository is
**inside** `models/huggingface.py`, which is the runtime implementation itself
(`test_every_neural_call_goes_through_the_audited_runtime`). No `transformers`
pipeline, no HTTP inference, no forward pass bypasses accounting. Model id,
revision, prompt hash, decode profile and token counts are recorded at the
record layer.

The ten non-neural upgraded modules import nothing from `cover_kbc.models` and
no ML/network package (`test_the_non_neural_upgraded_layers_add_no_calls`).

No weights were loaded in this audit.

---

## 18. Verifier blindness

M4 and M17 prompt surfaces contain the subject, the relation contract, the
candidate, the fixed A/B/C contract and contract-declared hard-negative
*classes* — and none of `R_t`, `residual`, `coverage_gap`, `utility`,
`expected_gain`, `support_count`, `planner` or `risk_profile`
(`test_verifier_prompts_never_see_planner_or_coverage_state`). M18 does not
reuse a contaminated prompt: its prompt module is scanned with the same list.

---

## 19. M17 calibration

A = VALID, B = INVALID, C = UNKNOWN; T = 1; contextual calibration with fixed
label ids and a sequence-likelihood fallback; template disagreement and
label-order disagreement both logged. Multiple templates, label orders and
content-free controls are **bias diagnostics and cost**, never independent
factual evidence groups — a candidate's incidence set is byte-identical with and
without them.

---

## 20. M18 structural semantics

Four mechanisms, exactly: reverse, key-condition, counterfactual, candidate-free
recall. The corrected Layer-4 mapping holds: for set-valued relations
`DIFFERENT_VALUE_RECOVERED → ALTERNATE_RECOVERED`, not contradiction; for
`NULL_SINGLE` and genuinely exclusive numeric targets a competing reconstruction
may be structural contradiction. Candidate-free target absence is not
contradiction. A candidate first discovered candidate-free is an acquisition
candidate with `FIRST_DISCOVERY` credit and no X merely for being first. M18
decides nothing (`test_alternate_recovery_is_never_contradiction`).

---

## 21. NULL semantics

Audit 0024 re-proven at full-system level at three magnitudes — **1, 10 and 100**
failed recalls: `substantive_null_groups` stays 0, `failed_recall_only` stays
True, the query existence unit stays unresolved with `FAILED_RECALL_ONLY`, and
the record contains no `final_empty`, `accepted_empty` or `is_empty`
(`test_failed_recall_never_becomes_substantive_null`).

Substantive living/death evidence, substantive no-known-locality evidence,
failed recall, UNKNOWN, malformed output, empty output and runtime failure
remain seven distinct states. No layer converts `FAILED_RECALL_ONLY` into final
empty evidence; M8 emits empty only under its own audited reasons (§32).

---

## 22. Numeric cluster semantics

§12. M12 owns identity; Layer 4 carries it as a copy; M19's novelty keys on
`m12_cluster#i` via M12's published `member_indices`; M21 and Layer 6 never
touch it. No reclustering, no unit reconversion, no evaluator tolerance, no
winner selection outside M8.

---

## 23. M19 coverage semantics

`R_t` is a **heuristic residual search-need index** — not a probability, not an
unseen-object estimate, not true cardinality, not a stop signal — and every
record carries that disclaimer. Exactly five components: `noveltyRate`,
`singletonRatio`, `facetGap`, `disagreement`, `unresolvedMass`. Exactly four
facet states: COVERED, WEAK, UNEXPLORED, EXHAUSTED.

Failure/empty is never EXHAUSTED; disabled is never UNEXPLORED; unavailable is
never numeric zero. Numeric novelty uses M12's cluster identity. Stock's facet
owner is M15 (Audit 0028 §9A). No Chao2, no unseen count, no STOP.

---

## 24. M6 / M19 coexistence

`coverage.py` is unmodified in git. M19's source contains no `q_res`,
`RCSEState` or `estimate_residual`, and the controller contains no
`coverage_gap` or `CoverageGapState`, so no average, max, overwrite, alias or
shared field is constructible. Both states coexist on every live run
(`test_module_6_and_module_19_stay_distinct`).

---

## 25. M20 budget semantics

Table 6's qualitative policy is proposal-defined and transcribed once; concrete
budgets are **not calibrated**. Verified: safe conservative precharge; hard
global ceiling a relation may restrict but never raise; generated tokens as a
separate resource; cache hit = zero physical call; unknown cache reserved as a
miss; protected-reserve isolation; class caps; settlement with release and a
loud failure when actual exceeds reserved; deterministic reservation identity;
replay deduplication.

M20 reads no factual truth and never uses `R_t`: its modules import nothing from
`cover_kbc.evidence`, `cover_kbc.coverage_gap` or `cover_kbc.verification`
(`test_module_20_reads_no_evidence_and_module_21_invents_no_legality`).

---

## 26. M21 utility semantics

```
U_t(a) = α·Ĝ_verified + β·ΔR̂ + γ·ΔĤ − δ·Ĉost − η·R̂edundancy − κ·F̂P
```

No hidden term and no relation-specific adjustment. Production historical bins,
coefficients and `τ_continue` are all **absent**; only `SYNTHETIC_TEST` packages
exist, and only in tests. Strict threshold: `> τ` → ACTION, `== τ` → STOP.
STOP reasons are exactly `NO_LEGAL_ACTION`, `NO_AFFORDABLE_ACTION`,
`UTILITY_BELOW_THRESHOLD`; a configuration failure raises and is never reported
as STOP. Depth ≤ 2, no MCTS, no model-generated future state, no online
learning.

---

## 27. Legality → affordability → value

The full ordering holds on every live Layer-6 state
(`test_the_control_ordering_holds_legality_affordability_value`):
`affordable ⊆ legal`, `denied ⊆ legal`, `affordable ∩ denied = ∅`, ranked set
== affordable set, and any selected action is affordable.

M19 cannot create legality (identical catalogue at residual 0.05 and 0.99); M9
cannot (no risk field is reachable); M20 cannot (asked after the catalogue is
built); M21 cannot resurrect illegality (`PlannerActionCandidate` raises without
owner provenance). Legal-but-unaffordable stays visible. **No action executes.**

---

## 28. Action-space coverage

| Family | Owner | Legality surface | Repeatable | Resource class | Relations |
| --- | --- | --- | --- | --- | --- |
| `SPECIALIST_PROBE` | M12–M15, M7 | registry + execution record; `legal_actions` | no | DISCOVERY | all |
| `PSEUDO_MEMORY_PROBE` | M11 | declared families + retrieval record | no | DISCOVERY | all |
| `CANDIDATE_FREE_RECALL` | M18 | `eligible_checks` | no | DISCOVERY | contract-declared |
| `BLIND_VERIFY` | M7 | `legal_actions` | no | VERIFICATION | all |
| `SPECIALIST_VERIFY` | M17 | `verifiable_targets` | no | VERIFICATION | all |
| `COUNTERFACTUAL_VERIFY` | M18, M7 | `eligible_checks`; `legal_actions` | no | VERIFICATION | contract-declared |
| `REVERSE_CHECK` | M18, M7 | `eligible_checks`; `legal_actions` | no | VERIFICATION | contract-declared |
| `CROSS_MODEL_CHECK` | M7 | `legal_actions` | no | VERIFICATION | where a distinct family exists |
| `RESAMPLE` | M7 | `legal_actions` | **yes** | DISCOVERY | all |

No family is silently unreachable. STOP is the planner's fallback, not a
catalogue entry. The proposal's contrastive-decoding/DoLa branch is **optional**
and recorded as intentionally deferred (§50); its absence is not an architecture
failure.

---

## 29. Action deduplication

Precedence by specificity: M17/M18 > typed specialists > M11 > generic M7. The
same semantic work reached through two owners collides on `semantic_key`, the
more specific owner wins, and the loser is recorded as `SAME_SEMANTIC_ACTION` —
deduplication, not denial. Generic core actions are not deleted where nothing
supersedes them. A conflicting duplicate raises. Canonical identity is
deterministic and order-independent.

---

## 30. Stock §17A regression

Audit 0032 §15B holds: legality requires **static** eligibility
(`plan.cross_family_eligible`) **and** the **local** trigger
(`result.cross_family_trigger.fires`) **and** not already executed. Both field
names are present in the adapter
(`test_the_stock_two_level_trigger_and_reserve_survive`).

`LOCALLY_CLEAR` → no action. `NOT_EVALUATED` → no action. The three uncertainty
triggers permit it when unexecuted. A fired-and-executed trigger is excluded as
`ALREADY_EXECUTED`. Module 9's real **HIGH** stock temporal grade cannot open a
locally-clear branch.

---

## 31. Stock reserve regression

Audit 0032 §15A holds: M15 cross-family → `DISCOVERY` + **FRESHNESS**; M18
parent/subsidiary → `VERIFICATION` + **PARENT_SUBSIDIARY**, identified by the
contract-declared class; `historical_listing` and generic company-itself /
key-condition checks receive neither. The two protected pools remain isolated in
both directions.

---

## 32. Layer 7 / M8 finalisation

Inspected directly rather than assumed. M8 dispatches per ProgramType from the
M0/M1 contract, and every cap is a contract fact, not a local constant
(`test_module_8_enforces_the_program_cardinality_contract`).

| Relation | Required semantics | Held by |
| --- | --- | --- |
| Borders | small set, land contact only, precision-aware, no maritime-only candidate | `select_small_set` + the border contract's view/negative classes |
| Death | ≤ 1 object; no `FAILED_RECALL_ONLY` → empty | `select_null_single` capped at `contract.max_objects`; `_empty_reason` keeps abstention distinct |
| Capacity | one canonical integer quantity; attendance cannot win for being larger | `select_numeric_*` over M12-style clusters with the contract's contrastive classes |
| Area | one robust total-area representative; land-only cannot win | same, with the area contract's near-miss classes |
| Award | set-valued atomic output, weak tail suppressed, no list-level vote | `select_large_open_set`, per-candidate acceptance |
| Stock | actual company listing; parent/subsidiary/index confusion excluded | `select_small_set` precision policy + the stock contract's adversarial classes |

`_empty_reason` keeps four states apart — confident negative gate, nothing
generated, all rejected, unresolved abstention — which is the finalisation-layer
counterpart of Audit 0024 (`test_the_empty_prediction_reasons_stay_distinct`).

**M8 receives no shadow state**: `selection.py` imports nothing from
`cover_kbc.control`, `cover_kbc.coverage_gap` or `cover_kbc.evidence.layer4`, and
contains no `coverage_gap`, `micro_planner`, `relation_budget`, `layer4`,
`consensus` or `R_t` (`test_module_8_receives_no_shadow_state`). The output
writer emits strict canonical surfaces only (§46).

**Verdict: CONFORMANT.** No mandatory finalisation contract is unmet, so no M8
change was required and none was made.

---

## 33. Border full flow

SMALL_SET. M0/M1 land-border contract → M9 profile → M10 program → M11
parametric probes → **M15** minimal-change facets (`border_direct` disabled by
Audit 0022 §11.1) with geographic decomposition → M2/M3 acquisition → M16 atomic
consensus → M15 pending reverse check for singleton/territory ambiguity → M18
reverse + M17 verification → Layer 4 → M6/M19 coverage → M7 conservative control
(M20/M21 shadow) → M8 high-precision set output.

Traced live; every stage present.

---

## 34. Death full flow

NULL_SINGLE. M0/M1 → M9 (temporal + nullability risk) → M10 → M11 → **M14**
Stage-A status gate, then Stage-B locality only when the gate permits, with the
freshness/candidate-free branch only under the audited local condition → M16 →
M17 locality hard negatives + M18 candidate-free recall → Layer 4 → M19 (records
`failed_recall_only`) → control → M8 with ≤ 1 object.

No epistemic UNKNOWN becomes empty at any layer (§21).

---

## 35. Capacity full flow

NUMERIC. M0/M1 quantity contract → M9 (numeric ambiguity) → M10 → M11 → **M12**
multi-probe acquisition, canonicalisation and tolerance clustering → M16 numeric
consensus → M17 exact-quantity verification with the contract's
attendance-vs-capacity contrastive classes → M18 → Layer 4 (carries M12's
clusters verbatim) → M19 cluster-stability reading → control → M8 one canonical
representative.

A strong multi-source cluster survives a verifier UNKNOWN; attendance cannot win
merely for being larger.

---

## 36. Award full flow

LARGE_OPEN_SET. M0/M1 → M9 (open-set + missingness risk) → M10 facet-oriented
program → M11 → **M13** divide-and-conquer facet slices with a missingness slice
→ M2/M3 → M16 atomic union → M17/M18 hard-negative and reverse checks under
§9.3's hard-reserved verification envelope in M20 → Layer 4 → M19 novelty,
singleton ratio and facet gap → M21 continue/stop semantics → M8 atomic
set-valued output with the weak unsupported tail suppressed and no list-level
vote.

---

## 37. Stock full flow

SMALL_SET with temporal risk. M0/M1 → M9 (**HIGH** temporal sensitivity) → M10 →
M11 → **M15** public-listing gate, then primary/secondary/dual acquisition
facets, with §17A's cross-family freshness rescue only when static eligibility
*and* the local trigger both permit → M16 → M18 parent/subsidiary and
company-itself checks (M15 pending checks as provenance) + M17 → Layer 4
(`ALTERNATE_RECOVERED` is not contradiction here) → M19 → M20 with FRESHNESS and
PARENT_SUBSIDIARY as isolated reserves → M8 high-precision output excluding
parent/subsidiary and index confusion.

---

## 38. Area full flow

NUMERIC. M0/M1 total-area contract → M9 → M10 → M11 → **M12** cross-unit and
definition probes with deterministic conversion and clustering → M16 → M17
total-versus-land contrastive verification → M18 → Layer 4 → M19 → control → M8
one robust total-area representative in the correct normalised grammar.

`hasArea` does not declare the historical/current-configuration probe family, so
it is `NOT_DECLARED` rather than an unexplored gap; land-only cannot win when
total is requested.

---

## 39. Staged execution and model role

The production path preserves ENUMERATOR → VERIFIER → ENUMERATOR where logically
needed, on one global production budget, with no mandatory work rerun after a
swap and pending-action identity preserved. Upgraded canonical action
descriptors retain their required model role — M17's verifications carry the
verifier role, discovery the enumerator role — so no action identity is lost
across a role boundary. M20/M21/Layer-6 inspection triggers **no** swap and
loads no model.

---

## 40. Conceptual feedback loop vs current activation

The proposal's Layer-6 → next-action → acquisition loop is representable end to
end: the canonical action exists, owner legality is available, M20 classifies
it, M21 values and selects it, and identity and model role survive the seam.

**EXECUTION BRIDGE: intentionally inactive pending TRAIN calibration.** This is
not production-active adaptive control and is not described as such.

---

## 41. Configuration and versioning

Fourteen config blocks, each explicit, versioned and validated:
`experiment`, `model_profile`, `budget_assertion`, `pipeline`,
`query_intelligence`, `specialists`, `consensus`, `specialist_verifier`,
`bidirectional_verification`, `layer4_integration`, `coverage_gap`,
`relation_budget_scheduler`, `micro_planner`, `layer6_integration`.

Shipped configs keep `relation_budget_scheduler`, `micro_planner`,
`layer6_integration` and `coverage_gap` **disabled**, with
`calibration_file`, `historical_bins` and `planner_calibration` all `null`, and
no `SYNTHETIC` string anywhere
(`test_shipped_configs_cannot_activate_uncalibrated_control`). Unknown keys,
unsupported versions and unsupported modes all fail loudly. No hidden threshold
lives in source where the proposal assigns it to calibration.

---

## 42. Closed-book proof

No inference-reachable module imports `requests`, `httpx`, `urllib`, `socket` or
`aiohttp`, and the source contains no `wikipedia.org`, `wikidata.org`, search
API, `elasticsearch`, `faiss`, `chromadb` or `pinecone`
(`test_inference_is_closed_book`). M11's "retrieval" is parametric only;
Appendix-B keywords are lexical steering, not a corpus. Development/download
tooling is distinguished from inference-reachable factual source, and none of
the latter reaches a network.

---

## 43. No-training proof

No `.backward()`, `torch.optim`, `AdamW`, `LoraConfig`, `get_peft_model`,
`Trainer(`, `reward_model` or `policy_gradient` anywhere in `src/`
(`test_nothing_is_trained`). No learned router, verifier head, neural fusion,
online update or policy. The architecture is frozen inference plus deterministic
non-neural processing.

---

## 44. Model profile

`scripts/audit_model_budget.py` → **PASS**, **28.67B** of a 32B cap:
`mistralai/Mistral-Small-3.2-24B-Instruct-2506` (24.011B, verified) and
`Qwen/Qwen3.5-4B`. No third model, no embedding model, no hidden semantic judge,
no DoLa model, no checkpoint change. Tokenizer adapters are the audited native
ones. No weights were loaded.

---

## 45. Benchmark integrity

`git status --porcelain benchmark/`, `git diff -- benchmark/` and
`git diff --cached -- benchmark/` are all **empty**. Upstream pin
`30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged. No benchmark content was
copied into prompts, configs, fixtures or source constants during M9–M21: every
test subject is a declared fictional literal.

---

## 46. Output contract

`prediction_rows` emits exactly `{SubjectEntity, Relation, ObjectEntities}` and
nothing else, for every relation on a live run
(`test_the_official_output_contract_is_unchanged`). The writer contains no
`R_t`, `coverage_gap`, `planner`, `verifier_label`, `confidence` or
`provenance` in executable code. All diagnostics live in separate artefacts.

---

## 47. Artefact ownership

| Artefact | Owner | Written when | Order | Neural | May affect production |
| --- | --- | --- | --- | --- | --- |
| `predictions.jsonl` | M8 | always | manifest | — | **yes (it is production)** |
| `diagnostics.json`, `trace.jsonl` | M5/M7 | always | manifest | — | no |
| `stage_a_enumerated.jsonl`, `stage_b_verified.jsonl` | M2/M4 | staged | manifest | yes | no |
| `calls_enumerate.jsonl`, `calls_verify.jsonl` | runtime | always | call order | yes | no |
| `query_profiles.jsonl` | M9 | enabled | manifest | no | no |
| `prompt_programs.jsonl` | M10 | enabled | manifest | no | no |
| `parametric_memory.jsonl` | M11 | enabled | manifest | **yes** | no |
| `numeric_specialist.jsonl` | M12 | enabled | manifest | **yes** | no |
| `large_open_set_specialist.jsonl` | M13 | enabled | manifest | **yes** | no |
| `null_temporal_specialist.jsonl` | M14 | enabled | manifest | **yes** | no |
| `small_set_specialist.jsonl` | M15 | enabled | manifest | **yes** | no |
| `atomic_consensus.jsonl` | M16 | enabled | manifest | no | no |
| `specialist_verification.jsonl` | M17 | enabled | manifest | **yes** | no |
| `bidirectional_verification.jsonl` | M18 | enabled | manifest | **yes** | no |
| `layer4_evidence.jsonl` | Layer 4 | enabled | manifest | no | no |
| `coverage_gap.jsonl` | M19 | enabled | manifest | no | no |
| `relation_budget.jsonl` | M20 | enabled + calibrated | manifest | no | no |
| `micro_planner.jsonl` | M21 | enabled + packages | manifest | no | no |
| `layer6_control.jsonl` | Layer 6 | enabled | manifest | no | no |

No two modules claim one semantic state.

---

## 48. Determinism

A repeated full scripted run is identical: same call count, same predictions,
same consensus/Layer-4/coverage-gap/budget/profile/program results, and an
identical architecture trace (`test_the_full_scripted_run_is_deterministic`).
No UUID, no wall-clock semantics, no hash-order dependence. Timestamps appear
only as non-semantic manifest metadata.

---

## 49. Shadow invariance

The strongest statement this architecture makes, over all six relations:

| Quantity | Core | Full stack | Verdict |
| --- | --- | --- | --- |
| predictions (`ObjectEntities`) | — | — | **identical** |
| `stopped_reason` | — | — | **identical** |
| M7 `calls_used` / `generated_tokens_used` | — | — | **identical** |
| runtime neural calls | **14** | **58** | +44 **shadow** calls, attributed |

The 44 extra calls are shadow neural spend by M11, M12–M15, M17 and M18 —
disclosed, not hidden, and not charged to Module 7's budget. The non-neural
upgraded layers (M16 integration, Layer 4, M19, M20, M21, Layer 6) add **zero**
calls (`test_the_upgraded_stack_changes_no_production_prediction`,
`test_shadow_neural_spend_is_attributed_not_hidden`).

---

## 50. Optional DoLa status

Not implemented and not referenced. No placeholder altered M4's or M17's A/B/C
calibration, the model budget or the runtime contracts. Recorded as an
**optional future ablation, only after architecture, runtime and calibration
stability**. Its absence is **not** an architecture failure.

---

## 51. Architecture claim boundaries

The defensible central abstractions are relation-typed active evidence
acquisition, independence-aware calibrated verification, and
coverage/uncertainty-guided test-time control. COVER-KBC does not claim to have
invented CoVe, contextual calibration, self-consistency, DoLa or generic
test-time scaling; each is cited. No performance claim is made for M9–M21: this
audit is about architecture correctness and is independent of score. The paper
was not modified.

---

## 52. Consolidated scripted smoke

`scripts/architecture_smoke.py` — one offline pass over all six relations with
fictional subjects, `ScriptedRuntime`, and `SYNTHETIC_TEST` M20/M21 packages.
It exercises M0–M21 and every integration seam, emits an architecture trace of
module presence and ownership, and reports accounting and production invariance.
No selected Module 21 action executes. It is not a benchmark run.

Observed:

```
countryLandBordersCountry      SMALL_SET       owner=M15  legal=1 afford=1 -> ACTION
personHasCityOfDeath           NULL_SINGLE     owner=M14  legal=8 afford=4 -> ACTION
hasCapacity                    NUMERIC         owner=M12  legal=1 afford=0 -> STOP/NO_AFFORDABLE_ACTION
hasArea                        NUMERIC         owner=M12  legal=1 afford=0 -> STOP/NO_AFFORDABLE_ACTION
awardWonBy                     LARGE_OPEN_SET  owner=M13  legal=1 afford=1 -> ACTION
companyTradesAtStockExchange   SMALL_SET       owner=M15  legal=6 afford=6 -> ACTION

modules present: M0/M1 6, M9 6, M10 6, M11 6, M12 2, M13 1, M14 1, M15 2,
                 M16 6, Layer4 6, M19 6, M20 6, M21 6, Layer6 6
accounting: 14 core calls vs 58 full-stack; 44 shadow neural calls
production predictions identical: True
```

---

## 53. Tests

`tests/test_architecture_conformance.py`, **49 cross-layer tests** (39 + 10 from
§10A and §16A), deliberately
few and strong rather than many and weak: architecture presence, Appendix-C
seams, the six flows, evidence accounting under composition, NULL/numeric/stock
regressions, verification and coverage boundaries, control ordering, M8
finalisation, call accounting, compliance, configuration, output contract,
determinism and shadow invariance — plus the corrective set: M9's early-graph
refinement, `q_novel` measured from returns and never from the subject,
refinement immutability and cross-query refusal, Table-3 route hints, M9/M19
separation, and Module 17's live cold/warm plan with its hard-cap denial.

Full suite: **2692 passed, 3 skipped**.

---

## 54. Pyflakes

`python -m pyflakes src/ tests/ scripts/` — **clean**.

---

## 55. Model budget

**PASS**, 28.67B (§44).

---

## 56. Deferred calibration items

* Module 20 relation budget envelopes — §16, TRAIN.
* Module 21 historical bins, α/β/γ/δ/η/κ, `τ_continue`, state binning
  boundaries, minimum bin support — §17, TRAIN.
* Layer-6 activation, which depends on both.

None exists; none was invented; shipped activation is impossible without them.

---

## 57. Remaining post-architecture work

1. real-model post-architecture runtime smoke;
2. TRAIN calibration of M20/M21 packages;
3. validation and ablation;
4. final activation and freeze.

This is honestly describable as **verification, calibration and activation** —
not as missing architecture construction.

---

## 58. Explicit non-goals

Not done here and not claimed: calibration, real-weight execution, TRAIN, VAL,
TEST, leaderboard submission, DoLa, upgraded production activation, any M22, any
performance claim, any change to the paper.

---

## 59. Verdict

**PASS.** All twenty-two criteria in §41 of the brief are met, after the two
defects in §10A and §16A were found on review and corrected. Both were
executable: Module 9 did not accept the Appendix-C input at all, and Module 17's
live action was priced at one call instead of eight.

Production source changed by the corrective pass: `query_intelligence/types.py`,
`query_intelligence/profiler.py`, `control/action_catalog.py`,
`control/layer6_integration.py` and the two pipeline seams. The M0–M8 production
core, M10–M16, M18–M21 and M8 finalisation are untouched.

    M0-M21 architecture implementation:              COMPLETE
    Cross-layer conformance:                         PASS

    Upgraded production activation:                  NOT YET
    M20 production budget calibration:               NOT YET
    M21 TRAIN historical bins / coefficients / tau:  NOT YET
    Real-weight post-architecture runtime smoke:     NOT YET
    TRAIN calibration:                               NOT YET
    Full VALIDATION:                                 NOT YET

Next step: **real-model post-architecture runtime smoke**, on a separate
authorised brief. Not run here.
