# Audit 0012 — Full End-to-End + Six-Relation Architecture Conformance Freeze

Status: **ARCHITECTURE FREEZE CANDIDATE — 9 cross-module defects found, 9 fixed**
Date: 2026-08-05
Revision: a second pass corrected three findings the first pass got wrong or
left too weak — an unproven identity inference, a hard budget that counted
logical actions rather than neural calls, and an unrationalised calibration
surface. See **§§58-61**, which supersede the affected first-pass text.

---

## 1. Objective and scope

Answer one question:

> If we treat COVER-KBC as **one system** rather than nine individually reviewed
> modules, does the actual executable prediction path faithfully implement the
> frozen proposal for all six official relations?

**Answer: yes, after six cross-module defects were fixed.** Every one of them
was invisible to a module-local review: they lived in the seams — an
unconditional phase tail bypassing the controller, a mode-dependent semantic
difference, a silent no-op for gated relations, a budget charged for work that
never happened, and two mathematically unsound decisions carried in from audit
0011.

Scope: the whole executable prediction path, all six relations, both execution
modes, the production CLI and configs. `benchmark/` is immutable.

**No model was downloaded or run.** No threshold was tuned.

---

## 2. This is NOT Module 9

There is no Module 9 and no new module was created. This milestone reviews and
corrects the **existing** Modules 0–8 as an integrated system, and produces the
architecture-freeze candidate.

---

## 3. Proposal system-level requirements

| § | Requirement |
|---|---|
| 3 | The control problem: allocate inference-time compute per query rather than a fixed budget. |
| 4 | Nine-module architecture with a **stateful runtime loop**, not a one-pass pipeline. |
| 26 | Algorithm 1: mandatory initial actions, then `while not ShouldStop and budget remains` over verify/RCSE/choose/execute, then `Finalize`. |
| 28 | Closed book. No retrieval, no training, ≤32B published parameters counted by total. |
| 31 | Implementation invariants — every final candidate traceable to Evidence Graph events. |
| 32 | Definition of done before test submission. |

---

## 4. Pre-work repository state

Branch `main`, HEAD `ba77510` ("refactor: align COVER-KBC final selector with
architecture"). Working tree clean. Audits 0001–0011 committed. 823 tests
passing. `benchmark/` unchanged; organizer snapshot commit `b607ae1`.

Hygiene: no `CLAUDE.md`, no `.claude/`, no tracked model cache, predictions,
stage artefacts, nested git directories or secret files.

---

## 5. Module 0–8 acceptance chain

| Module | Audit | Verdict |
|---|---|---|
| 0 Relation Compiler | 0003 | pass |
| 1 Typed Program Router | 0004 | pass |
| 2 Diverse Elicitation | 0005 | pass |
| 3 Evidence Graph | 0006 | pass |
| 4 Blind Verifier | 0007 | pass |
| 5 Evidence & Uncertainty State | 0008 | pass |
| 6 RCSE | 0009 | pass |
| 7 Active Controller | 0010 | pass (revised) |
| 8 Final Selector | 0011 | pass |

All nine remain conformant after this milestone's changes; every module suite is
re-run green in §49.

---

## 6. Production inference-path map — DEFECT 1 (severe, fixed)

The single production path is `scripts/run_staged.py` →
`CoverPipeline.enumerate / verify / resume / decide`. There is no legacy
baseline path, no unconditional verify-all, and no direct-generation shortcut.

**But there was a production bypass.** `enumerate_query` ended with an
*unconditional* cross-model recall tail:

```python
if self.config.mode is not ExecutionMode.STAGED and not self._cross_model_done(graph):
    self._run_cross_model_recall(graph, contract)
```

It ran regardless of what the controller decided — and only in interleaved mode,
because the staged equivalent sat in `verify_graph`. Consequence, measured: for
`hasArea` and `hasCapacity` the interleaved run carried a `CROSS_MODEL_RECALL`
edge and the staged run did not, from the same config and the same scripted
answers. The controller had chosen `STOP`; the tail overrode it.

Cross-model recall is a controller action (`CROSS_MODEL_CHECK`). The tail is now
gated on `not enable_active_controller`, so it survives only in the fixed
(non-adaptive) ablation paths where there is no controller to bypass. Both modes
now execute exactly what the controller chose.

---

## 7. Algorithm-1 executable matrix

| Step | Function | Owner | Staged | Interleaved | Test |
|---|---|---|---|---|---|
| CompileRelation | `compile_query` | 0 | ✓ | ✓ | ✓ |
| RouteProgram | `contract.program_type` | 1 | ✓ | ✓ | ✓ |
| InitState | `build_graph`, `RCSEState()` | 3/6 | ✓ | ✓ | ✓ |
| Initial mandatory actions | `legal_actions` + `view_gap_relevance` | 7 | ✓ | ✓ | ✓ |
| Execute | `_execute_action` | 7 | ✓ | ✓ | ✓ |
| ParseNormalize | `parsing` + `normalization` | 2/3 | ✓ | ✓ | ✓ |
| UpdateGraph | `add_entity_mentions` / `add_numeric_mentions` | 3 | ✓ | ✓ | ✓ |
| UpdateState | `record_outcome` | 6 | ✓ | ✓ | ✓ |
| HighestImpactUnresolved | `candidate_impact` | 7 | ✓ | ✓ | ✓ |
| WorthVerifying | tier + `verify_first_unresolved` | 4/7 | ✓ | ✓ | ✓ |
| BlindVerifyWithLogits | `verify_candidate` / `verify_multi_template` | 4 | ✓ | ✓ | ✓ |
| RCSE | `estimate_residual` | 6 | ✓ | ✓ | ✓ |
| ChooseAction | `choose_action` | 7 | ✓ | ✓ | ✓ |
| **loop across role swaps** | `phase_resolve` / `resume` | 7 | ✓ | n/a | ✓ |
| STOP / Continue | `should_stop` | 7 | ✓ | ✓ | ✓ |
| Finalize | `finalize` | 8 | ✓ | ✓ | ✓ |

Every step is live in both modes. No step is decorative and no parallel path
bypasses the reviewed architecture.

---

## 8. Architectural ownership matrix

| Decision | Sole owner | Duplication found |
|---|---|---|
| relation semantics | Module 0 contract | none |
| ProgramType | Module 1 | none |
| view availability | Module 0 + Module 2 library | none |
| candidate identity | Module 3 `strict_key` | none |
| A/B/C verification | Module 4 | none |
| F/L/X/C/U | Module 5 | **fixed in audit 0011** (selector re-weighted) |
| residual search need | Module 6 | none |
| next action / stopping | Module 7 | **fixed in audit 0010** (STOP authority) |
| final emitted objects | Module 8 | none |
| cluster **geometry** | `normalization.numeric.cluster_values` | shared by 6 and 8 by design |
| cluster **winner** | Module 8 | none |
| serialization | writer | **fixed in audit 0011** (alias fold removed) |

Numeric clustering is shared deliberately: Module 6 observes stability, Module 8
picks the winner, and both read one primitive so they cannot disagree (§31).

---

## 9. Target model-role profile

| Role | Model | Published total |
|---|---|---|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 24,011,361,280 |
| verifier, gate scorer, optional cross-model recall | `Qwen/Qwen3.5-4B` | 4,659,865,088 |

Unchanged. No bake-off, no third checkpoint, no Qwen-9B, no Gemma-27B. DoLa is
not implemented and has no action type. Cross-model recall remains behind
`enable_cross_model_recall` and is never required for stopping.

---

## 10. Parameter-budget audit

```
$ python3 scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  - Qwen/Qwen3.5-4B [verifier] 4.660B (verified)
      full checkpoint: 4,659,865,088
  - mistralai/Mistral-Small-3.2-24B-Instruct-2506 [enumerator] 24.011B (verified)
      full checkpoint: 24,011,361,280
  total: 28.67B
  RESULT: PASS
```

28,671,226,368 < 32,000,000,000. Counts are published totals read from
safetensors headers; quantisation does not reduce them.
`test_no_config_enables_a_third_neural_component` sweeps every experiment config
and fails if any declares more than two neural profiles.

---

## 11. Target config audit

**Target** — `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`: staged
mode, active controller on, verifier on, calibration on, `run_optional_views`
deliberately absent (the controller owns optional scheduling), gate role =
verifier, no DoLa anywhere in the file. Asserted by
`test_the_target_config_is_internally_coherent`.

---

## 12. Ablation config audit

**Ablations** stay genuinely different:
`ablation_fixed_multiview.yaml` has `enable_active_controller: false` and
`run_optional_views: true` — a real fixed ladder rung, not adaptive execution
wearing a different name (`test_the_fixed_ablation_config_is_genuinely_fixed`).
`smoke_staged_scripted.yaml` and `smoke_staged_roleswap.yaml` are non-neural
plumbing fixtures.

---

## 13-18. Six relation programmes, end to end

Sections 13 (borders), 14 (death), 15 (stock), 16 (area), 17 (capacity) and
18 (awards) are presented together because they were validated by one
parametrized harness over the identical staged path — the point being that no
relation needs a special case.

Every relation was run through staged execution to completion, then finalized.

| Relation | Programme | Emitted | Cardinality | Calls |
|---|---|---|---|---|
| `countryLandBordersCountry` | SMALL_SET | strings | unbounded | 4 |
| `personHasCityOfDeath` | NULL_SINGLE | ≤1 string | 1 | 4 |
| `companyTradesAtStockExchange` | SMALL_SET | strings | unbounded | 5 |
| `hasArea` | NUMERIC | 1 km² scalar | 1 | 2 |
| `hasCapacity` | NUMERIC | 1 integer | 1 | 2 |
| `awardWonBy` | LARGE_OPEN_SET | strings | unbounded | 7 |

Specific programme properties asserted:

- **borders** finish at or below award cost — typed budgets differentiate
  (`test_an_easy_border_costs_far_less_than_an_open_award`);
- **death**: a confident negative gate produces `CONFIDENT_NEGATIVE_GATE`; an
  uncertain gate is never relabelled as one; cardinality ≤1;
- **stock**: high-precision selection, weak unresolved listings dropped;
- **area**: one bare km² numeral, no unit suffix or separator;
- **capacity**: integer, and a rejected larger near miss cannot win;
- **awards**: the controller stops before sweeping every optional facet
  (`test_awards_stop_before_exhausting_every_optional_facet`).

---

## 19. Six-relation action / programme matrix

All four programmes are covered by the six relations, every declared view
exists and validates, and every relation has at least one mandatory view so no
query can start with nothing to do.

---

## 20. Staged / interleaved equivalence — DEFECT 2 (severe, fixed)

Parametrized over **all six relations**, comparing emitted objects, candidate
keys, empty reason and the per-candidate `(independence_group, mode)` evidence
shape.

This is what exposed DEFECT 1: `hasArea` and `hasCapacity` disagreed, the staged
run lacking the `CROSS_MODEL_RECALL` edge the interleaved run had. After the fix
all six agree. Physical model-load order still differs by design — staged swaps
residency, interleaved keeps both resident — but logical semantics do not.

---

## 21. Role-swap execution

The production CLI drives the loop (`phase_resolve`), verified on `awardWonBy`:

```
[PHASE A] enumerate → [PHASE B] verify → [RESUME 1] role=enumerator → [PHASE C] decide

round 0 RUN_VIEW           enumerator award_missing
round 1 RUN_VIEW           enumerator award_facet_temporal
round 2 RUN_VIEW           enumerator award_direct
round 3 REVERSE_CHECK      enumerator award_reverse_check
round 4 ADVERSARIAL_VERIFY verifier   gamma
round 5 RUN_VIEW           enumerator award_facet_recipient_type  <- pended for enumerator
round 6 RUN_FACET          enumerator award_exact_identity_contrast
round 7 STOP               none
```

Contiguous rounds across the swap, one global budget, no mandatory work re-run,
no pending action left at finalization. `personHasCityOfDeath` also exercises
ENUMERATOR → VERIFIER → ENUMERATOR (§24).

---

## 22. Budget accounting — DEFECTS 3 and 4 (fixed)

### 22.1 Cross-model recall was a silent no-op for gated relations

`_run_cross_model_recall` took `contract.mandatory_views[0]` and returned early
if that view was a gate. For `personHasCityOfDeath` and
`companyTradesAtStockExchange` the first mandatory view **is** the existence
gate — so cross-model recall was structurally impossible for exactly the two
relations whose precision most depends on a second opinion, and nothing said so.

Fixed: `_first_recall_view` takes the first mandatory **non-gate** view.
`test_cross_model_recall_is_possible_for_every_relation` asserts it for all six.

### 22.2 The budget charged for the no-op

`_execute_action` returned a hard-coded `1` call for `CROSS_MODEL_CHECK` whether
or not the second model was asked anything. Measured: `personHasCityOfDeath`
charged 4 calls against 3 actual. It now charges only when
`verifier_runtime.calls` actually advanced.

### 22.3 Reconciliation after both fixes

| relation | runtime calls | charged |
|---|---|---|
| `countryLandBordersCountry` | 4 | 4 |
| `personHasCityOfDeath` | 4 | 4 |
| `companyTradesAtStockExchange` | 6 | 5 |
| `hasArea` | 2 | 2 |
| `hasCapacity` | 2 | 2 |
| `awardWonBy` | 7 | 7 |

> **SUPERSEDED by §59.** The first pass accepted stock's 6-vs-5 gap by defining
> the hard budget as *logical calls*. That is wrong for a hard ceiling: it let a
> multi-template verification hide several real neural calls inside one charged
> call. The budget now counts **actual neural invocations** and all six relations
> reconcile **exactly**.

---

## 23. Provenance trace matrix

**Every emitted object, all six relations**: maps to exactly one candidate, which
carries `record_ids`, each resolving to a `GenerationRecord` in the graph with a
view id and model id (`test_every_emitted_object_traces_back_to_evidence`).

---

## 24. Description-first trace

 stage-1 prose is registered for the trace but never
becomes a support edge — only the extraction stage produces candidate mentions,
and the parent chain survives staged persistence
(`test_description_prose_never_becomes_factual_support`).

---

## 25. Reverse trace

`REVERSE_ALTERNATE` evidence is `INDEPENDENT_RECALL` acquisition,
never `BLIND_VERIFIER`; `reverse(A)` never marks B complete; each action
instance executes once (`RCSEState.executed_actions`, audit 0010 §31.2).

**Numeric**: the derived median is kept in `derived_value`, distinct from the
observed `display_value`, with its cluster and members retained (audit 0011 §29).

---

## 26. Blind-verifier boundary

Checked on the real prompt builder for all three templates: no
`independent_support`, `raw_support`, `q_res`, `residual`, `score`, `controller`,
`facet_gap`, `F(o)` or `L(o)` reaches the prompt. The verifier receives subject,
contract semantics, candidate and the A/B/C key — nothing about how the
candidate was found.

---

## 27. F / L / X / C / U system accounting

Verified **through the real pipeline** for every relation, not in unit
isolation: for every candidate the system produced, `supporting_acquisition_groups`
excludes `BLIND_VERIFIER` and `CROSS_MODEL_RECALL`, and `X` is credited only
where a genuinely `INDEPENDENT_RECALL` cross-model edge exists.

Downstream re-merge points specifically audited and clean: controller
`candidate_impact`, RCSE state, final selector ordering, numeric cluster support.

Ten repeats of one view remain one acquisition mechanism end to end
(`test_repeated_output_from_one_view_is_not_independent_evidence`).

---

## 28. Available / executed / reachable consistency

For all six relations every mechanism Module 5 counts as available is reachable
by a legal executable action. Disabled branches — cross-model recall and
factual decoding — are absent from `m(o)`, leave no permanent residual gap and
never block stopping. **No xfail remains anywhere in the repository.**

---

## 29. Alias / surface-equivalence — the frozen decision — DEFECT 5 (fixed)

Audit 0011 §45.1 left this open. The milestone requires an explicit decision.

> **SUPERSEDED by §58.** The decision below was made on the first pass and is
> **wrong**: same-record co-occurrence plus a matching alias hint does not
> *prove* restatement. The grouping no longer affects output. The text is kept
> because the reasoning that produced it, and its refutation, are both part of
> the record.

**First-pass decision (withdrawn): implement a strictly-local,
provenance-backed restatement relation.**

`EvidenceGraph.restatement_groups()` (since renamed `same_record_alias_hints`, §58.3) grouped two candidates only when **all** of:

1. they were produced by the **same** `GenerationRecord` — one model output;
2. they share an `alias_hint`, whose only transformation beyond the evaluator's
   own normalisation is folding a leading article;
3. therefore: one name restated, not two names.

Module 8 emits one preferred surface per group, by the same deterministic rank
as everything else. **Nothing is merged**: both candidate nodes and all their
evidence survive in the graph.

Why this is safe where the global fold was not:

| case | global `alias_hint` fold | restatement relation |
|---|---|---|
| "The Alpha Exchange" / "Alpha Exchange" in **one** generation | merged | grouped ✓ |
| the same two strings from **different** generations | merged ✗ | **not grouped** ✓ |
| "London Stock Exchange" / "The Stock Exchange" in one generation | not merged | **not grouped** ✓ (different hints) |
| "Le Havre" / "Havre", "X" / "X (qualifier)" | not merged | not grouped ✓ |

Co-occurrence in a single generation is *provenance*, not string similarity: a
model answering "The Alpha Exchange; Alpha Exchange" has restated one entity.
No fuzzy resolver, no external alias database, no parenthetical stripping, no
reconstruction of the evaluator's gold aliases.

**Claimed effect (withdrawn)**: that this recovered the precision audit 0011 §12
gave up. A synthetic fixture score is not architecture evidence, and it must not
justify an unproven identity rule. See §58 for the frozen decision and the
fixture's honest outcome.

---

## 30. Numeric single-linkage chain — the frozen decision — DEFECT 6 (fixed)

Audit 0011 §22.1 lowered the threshold 0.05 → 0.025 arguing it "keeps a cluster
comfortably inside" the tolerance. **That argument was mathematically wrong**,
and this milestone measured it. With single-linkage at t = 0.025:

| chain length | cluster diameter | max median-to-member |
|---|---|---|
| 2 | 0.024 | 0.012 |
| 4 | **0.071** | 0.036 |
| 8 | **0.157** | 0.082 |
| 20 | **0.372** | 0.207 |

A pairwise threshold does not bound the diameter at all; lowering it only slows
the drift. Four chained values already exceed the official ±5% tolerance.

**Decision: option B — the threshold bounds the cluster diameter, not each
adjacent step.**

```python
if relative_distance(groups[-1][0], value) <= threshold:   # first, not last
```

After the change the diameter is ≤ threshold for **every** chain length, and the
median is provably within the threshold of every member:

| chain length | 2 | 4 | 8 | 20 | 40 |
|---|---|---|---|---|---|
| max diameter | 0.024 | 0.024 | 0.024 | 0.024 | 0.024 |
| max median-to-member | 0.012 | 0.012 | 0.012 | 0.012 | 0.012 |

This is not mimicking the evaluator: a diameter bound is what "one coherent
semantic cluster" means, and 0.025 stays conservatively inside the ±5%
tolerance rather than sitting on it. Both Module 6 and Module 8 suites were
re-run (§49).

An ambiguous chained sequence now splits into competing clusters, which RCSE
reports as `cluster_competition = 1.0` and the controller treats as a reason to
**continue searching** rather than silently pick one — measured `q_res` 0.029
for a coherent set versus 1.000 for a chained one.

---

## 31. Module-6 / Module-8 numeric consistency

Both call `cluster_values`, so membership cannot diverge. Verified directly for
`hasArea` and `hasCapacity` across normal, competing and chained inputs: cluster
counts agree in every case
(`test_module_6_and_module_8_agree_on_cluster_membership`).

---

## 32. Empty-state system matrix

| state | gate | generated | survive | reason |
|---|---|---|---|---|
| confident negative | NO (confident) | any | any | `CONFIDENT_NEGATIVE_GATE` |
| nothing generated | not negative | none | — | `NO_CANDIDATE_GENERATED` |
| all rejected | not negative | some | none | `CANDIDATE_REJECTED` |
| abstention | not negative | some | some, none accepted | `UNRESOLVED_ABSTENTION` |

No layer relabels another's semantics: the gate's uncertainty survives Module 4
→ 5 → 6 → 7 → 8, and an uncertain gate with no surviving candidate is never
reported as a confident negative.

---

## 33. Budget-exhausted semantics

`q_res` stays > 0 when search value remains but budget does not; the stop reason
is budget exhaustion, not low residual; unresolved candidates are not relabelled
rejected; Module 7 records the abandoned action in the controller log before
clearing it so Module 8 receives a legally finalizable state (audit 0010 §31.3).

---

## 34. Final-output traceability

Every output string maps to exactly one internal candidate; every numeric output
maps to one derived representative of one winning cluster. No output originates
from the writer, a controller reason string, a verifier label or a parser
placeholder — control tokens (`VALID`, `NONE`, …) raise rather than ship.

---

## 35. Writer / evaluator schema

Official rows carry exactly `SubjectEntity`, `Relation`, `ObjectEntities` with
`ObjectEntities` a flat `list[str]`. No diagnostics leak.

---

## 36. Official-evaluator plumbing smoke

Production CLI smoke, three `awardWonBy` val rows, scripted runtimes:

```
fields present: ['ObjectEntities', 'Relation', 'SubjectEntity']
schema OK; empty rows: 0
```

Cardinalities accepted by the pinned evaluator: `[]`, one string, several
strings, and a numeric string matched inside its own ±5% tolerance.

**Any score printed by that run is meaningless.** The scripted runtime emits the
literal "Alpha", which is not a fact. This is format validation only.

---

## 37. Benchmark snapshot integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

Organizer snapshot commit `b607ae1`; upstream pin
`30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` verified in audit 0001. The known
quirk stands: **the pinned commit contains no `baseline.py`** despite README
references. Nothing was fabricated or vendored in, and no evaluator behaviour
was patched or reimplemented.

---

## 38. No-retrieval audit

AST-swept every module under `src/cover_kbc/`: no `requests`, `urllib`, `httpx`,
`aiohttp`, `wikipedia`, `wikidata`, `duckduckgo`, `serpapi`, `faiss`,
`chromadb` or `pinecone` import anywhere; No disabled retrieval branch exists to become reachable.

---

## 39. No-training audit

No `fit`, `partial_fit`, `backward`, `step`, `train` or `get_peft_model` call;
no `peft`, `trl`, `deepspeed` or `accelerate` import anywhere in the production
path. Inference-time deterministic threshold calibration is permitted later and
is not performed here.

---

## 40. Model-budget compliance

See §10. PASS at 28.67B.

---

## 41. Master threshold inventory

**62 global tunables + 15 contract-level fields**, none duplicated across
owners (verified programmatically — no name controls the same decision in two
modules).

| group | count | examples |
|---|---|---|
| Module 5 evidence/acceptance | 17 | `alpha_support`, `accept_score`, `logit_clip`, `min_valid_prob` |
| Module 6 RCSE | 17 | `saturation_window`, `yield_scale`, `w_gate`, `stop_threshold` |
| Module 7 controller | 26 | `alpha_yield`…`rho_redundancy`, six `cost_*` priors, `untried_yield_prior` |
| Module 8 selector | 2 | `capacity_support_ratio`, `capacity_trust_verified` |
| contract-level | 15 | `numeric_cluster_threshold`, `accept_valid_prob`, `max_calls` |

Non-tunable by design: `logit_epsilon` and `yield_epsilon` (numerical guards),
`optional_views_available` (derived from run mode), `max_objects` and
`numeric_integer_only` (programme/schema facts).

**Every one is an architecture default. None is calibrated. None was tuned here.**

---

## 42. Train / internal calibration protocol

Frozen now, before any val number exists:

1. freeze architecture and code (this milestone);
2. run the frozen models on **train or a documented internal split only**;
3. collect logs — action costs, yields, verifier biases, cluster dispersions;
4. calibrate the deterministic non-neural thresholds in §41;
5. record chosen values, the split, and the objective;
6. **freeze the config**;
7. only then run val **once**, for analysis.

Prohibited: iterative val tuning, any use of test, fitting any neural component.
`awardWonBy` has ten validation examples, so the parameter surface must stay
conservative; prefer leaving a default over fitting it on thin data.

This milestone executes none of it.

---

## 43. Architecture-freeze candidate config

`configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` fully determines model
profiles and roles, feature flags, controller and RCSE settings, budgets, stage
mode, scoring/selection defaults and the seed. No machine-specific absolute
path is committed.

Its thresholds are **architecture defaults, not calibrated values** — the config
is an *architecture* freeze candidate, not a measured one.

---

## 44. Reproducibility commands

Scripted full-system validation, now:

```
python3 -m pytest -q
python3 scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_roleswap.yaml \
        --relation awardWonBy --limit 3
```

Model-backed, later, after weights are supplied — same entry point, same config:

```
python3 scripts/run_staged.py all --config configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
```

Colab's only job is to clone the exact commit, install, obtain weights and call
that command. No architecture logic belongs in a notebook.

---

## 45. Cross-module defects found

| # | Severity | Description |
|---|---|---|
| 1 | **severe** | Unconditional cross-model tail bypassed the controller and ran only in some modes (§6) |
| 2 | **severe** | Consequent staged/interleaved semantic divergence for both numeric relations (§20) |
| 3 | **severe** | Cross-model recall was a silent no-op for the two **gated** relations, because it took `mandatory_views[0]` which is the gate (§22.1) |
| 4 | moderate | The budget charged one call for that no-op; `personHasCityOfDeath` charged 4 against 3 real calls (§22.2) |
| 5 | moderate | Audit 0011's alias decision left an unhedged precision loss with no architectural recovery (§29) |
| 6 | moderate | Audit 0011's single-linkage rationale was mathematically unsound; cluster diameter was unbounded (§30) |

A seventh, introduced while fixing #1 and caught immediately: in staged Phase A a
controller-chosen `CROSS_MODEL_CHECK` executed against the non-resident
verifier, because pending was keyed on *action type* rather than *model role*.
Now keyed on role, so a future verifier-role action cannot leak through.

---

## 46. Fixes made

1. Cross-model tail gated on `not enable_active_controller` (`pipeline.py`).
2. Staged Phase A pends by **model role**, not action type (`pipeline.py`).
3. `_first_recall_view` skips gate views (`pipeline.py`).
4. `CROSS_MODEL_CHECK` charges only when the second model was really called.
5. `EvidenceGraph.same_record_alias_hints()` — a **diagnostic** accessor with
   no effect on output (`evidence/graph.py`); the first-pass selector hook was
   withdrawn in §58.
6. `cluster_values` bounds the cluster **diameter** (`normalization/numeric.py`).

---

## 47. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/pipeline.py` | modified — controller bypass, role-keyed pending, recall view, charging |
| `src/cover_kbc/normalization/numeric.py` | modified — diameter-bounded clustering |
| `src/cover_kbc/evidence/graph.py` | modified — `same_record_alias_hints` (diagnostic) |
| `src/cover_kbc/calibration.py` | **created** — parameter category inventory |
| `tests/test_system_e2e_conformance.py` | **created** — 106 tests |
| `tests/test_pipeline.py`, `tests/test_evidence.py` | modified — recovered precision expectation |
| `docs/audits/0012-full-e2e-six-relation-conformance-freeze.md` | **created** |

`benchmark/` untouched.

---

## 48. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_system_e2e_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 4
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_roleswap.yaml --relation awardWonBy --limit 3
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

No model download. No GPU inference. No Colab.

---

## 49. Exact tests / results

**948 passed, 3 skipped, 0 failed, 0 xfailed.**

| Suite | Tests |
|---|---|
| `test_system_e2e_conformance.py` | **125** |
| `test_controller_conformance.py` | 79 |
| `test_rcse_conformance.py` | 86 |
| `test_evidence_state_conformance.py` | 72 |
| `test_verifier_conformance.py` | 76 |
| `test_final_selector_conformance.py` | 64 |
| `test_graph.py` | 59 |
| remaining suites | 387 |
| `calibration.py` inventory tests | included above |

`pyflakes`: clean apart from four intentional `_bootstrap` sys.path shims.

---

## 50. Skipped tests, explained individually

| Test | Reason |
|---|---|
| `test_every_empty_reason_is_reachable[pipeline_error]` | `EmptyReason.PIPELINE_ERROR` is produced by the pipeline's exception handler, not the selector; it is out of that test's scope and is exercised where it is raised. |
| `test_repeated_output_from_one_view_is_not_independent_evidence[hasArea]` and `[hasCapacity]` | The test drives entity mentions; numeric relations use `add_numeric_mentions`, covered separately by the numeric cluster tests. |

No skip hides a known architecture defect. **No xfail exists anywhere.**

---

## 51. Unresolved SYSTEM-level issues

1. **Cross-model recall's view choice is a heuristic.** It uses the first
   mandatory non-gate view. A relation might prefer a different framing for the
   second model; the contract could name one explicitly. Non-blocking.
2. **`capacity_support_ratio = 1.0` is strict** (carried from audit 0011 §45.2).
3. **The restatement relation covers article variants only.** Other
   same-generation restatements — an abbreviation beside a full name — are not
   grouped. Deliberately conservative; widening it needs parser-level evidence
   that one item is a restatement, which does not exist today.
4. **Numeric cluster threshold is uniform** across area and capacity.

None blocks the freeze.

---

## 52. Thresholds and defaults still uncalibrated

All 77 inventoried values are architecture defaults, never fitted. **Five**
of them are the calibratable decisions of §60.3; the other 72 are semantic
facts, numerical guards, measurable runtime costs or structural constants that
a calibration run must not search.

---

## 53. NO REAL MISTRAL-24B / QWEN3.5-4B RESULT EXISTS YET

The frozen model pair has **never been downloaded and never been run**. Every
number in every audit — including this one — comes from `ScriptedRuntime`
fixtures emitting synthetic strings such as "Alpha".

Specifically still unmeasured: whether `A`/`B`/`C` are single tokens under the
real Qwen3.5-4B tokenizer; the magnitude of contextual-calibration biases; real
Mistral candidate quality; real action yields and costs.

---

## 54. NO VAL PERFORMANCE CLAIM HAS BEEN MADE

No validation F1 has been produced, reported or implied. The only evaluator
invocations were format checks against scripted output, explicitly labelled
meaningless. Nothing in this repository is a performance result, and no README
or audit presents one.

---

## 55. Benchmark integrity

All three git checks empty (§37). No organizer file was read as anything but an
immutable dependency.

---

## 58. FINAL CORRECTION A — surface equivalence, frozen conservatively

### 58.1 Why same-record + alias_hint was insufficient

The first pass (§29) argued that two candidates from one `GenerationRecord`
sharing an article-folded `alias_hint` are "the same name restated, not two
names". **That implication is not guaranteed.** One generation may legitimately
enumerate two distinct entities whose surfaces happen to satisfy the same
lexical fold. Co-occurrence is provenance about *when* two surfaces appeared,
not evidence about *what they denote*.

The hierarchy is:

| signal | what it establishes |
|---|---|
| `strict_key` | **hard candidate identity** |
| `alias_hint_key` | a soft lexical hint |
| same `GenerationRecord` | shared provenance |

Neither of the last two, individually **or together**, proves semantic identity.

### 58.2 Does explicit restatement evidence exist? No.

Inspected before deciding:

- `elicitation/parsing.py` recognises **no** alias construction — no "also known
  as", no "aka", no `alias`/`variant`/`synonym` handling of any kind;
- `GenerationRecord` carries no `restatement_of` or `alias_of` relation
  (22 fields, none of them that);
- `parenthetical_parts` is documented diagnostic-only and merges nothing.

There is no reliable parser-level restatement signal today.

### 58.3 The frozen decision

**Strict identity is retained. No surface-equivalence grouping affects output.**

`restatement_groups()` is renamed `same_record_alias_hints()`, documented as
**diagnostic only**, and removed from every selector. Both candidates keep their
nodes, all their evidence and their provenance; the hint survives for a human
reading a trace and changes nothing that is emitted.

The global writer fold from audit 0011 is **not** restored.

### 58.4 Honest outcome of the synthetic fixture

The end-to-end evaluator fixture falls back to **macro-F1 2/3** (precision 1/2,
recall 1/1) — both article variants are submitted and a gold whose alias set
covers both absorbs one.

That is accepted and recorded, not optimised. A false merge of two genuinely
distinct entities is a worse failure than an unmatched prediction, and a
synthetic fixture score is not architecture evidence.

Reopening this needs parser-level evidence that one surface restates another —
a Module-2/3 change, not a Module-8 heuristic.

---

## 59. FINAL CORRECTION B — the hard budget counts neural calls

### 59.1 Logical actions and neural calls are different quantities

`Budget` now tracks both, and they never share a counter:

| quantity | meaning | bounds anything? |
|---|---|---|
| `calls_used` | **actual neural runtime invocations** — one `generate` or one `score_labels` each | **yes**, `max_calls` |
| `logical_actions` | controller actions taken | no; diagnostic |
| `generated_tokens_used` | free-form tokens produced | yes, independent of calls |

A `score_labels` call generates no tokens and is still a neural call; a cache
hit performs no inference and costs nothing.

### 59.2 Charging is measured, not assumed

`_execute_action` reads the runtimes' own counters before and after, so the
charge is what actually happened regardless of what the primitive did
internally. Both execution paths — interleaved `_adaptive_discovery` and staged
`_controlled_phase` — now go through it, which is what makes the two modes spend
identically for identical decisions. (They previously did not: the interleaved
path had its own `max(1, verified)` charge, and that discrepancy surfaced as a
staged/interleaved divergence for `companyTradesAtStockExchange` the moment the
staged side became exact.)

### 59.3 Pre-flight on known multi-call actions

`_minimum_neural_cost` returns the floor an action is *known* to need — two for
a description-first view, one per template for a multi-template verification —
and `Budget.can_afford` refuses to start one that would overrun. Calibration
controls are excluded from the floor because a cache hit costs nothing, so
counting them would refuse affordable actions. `Budget.charge` rejects a
negative charge outright.

### 59.4 Exact six-relation reconciliation

| relation | logical actions | neural runtime calls | charged | tokens | calls left |
|---|---|---|---|---|---|
| `countryLandBordersCountry` | 4 | 4 | **4** | 8 | 0 |
| `personHasCityOfDeath` | 4 | 4 | **4** | 8 | 0 |
| `companyTradesAtStockExchange` | 4 | **5** | **5** | 6 | 0 |
| `hasArea` | 2 | 2 | **2** | 2 | 2 |
| `hasCapacity` | 2 | 2 | **2** | 2 | 2 |
| `awardWonBy` | 7 | 7 | **7** | 14 | 9 |

Charged **equals** actual for every relation. Stock is the case that matters:
4 logical actions but 5 neural calls, because one verification action spent
several score calls — and the hard budget sees all five.

The invariant is now equality, not `charged <= actual`
(`test_the_charged_budget_equals_the_neural_calls_actually_made`).

### 59.5 Calibration-cache accounting

The first control measurement for a given setup costs one score call; every
compatible reuse costs zero and is not charged
(`test_a_cached_calibration_control_is_not_charged_twice`). Cache identity is
Module 4's, unchanged.

### 59.6 Terminology

`calls_used` / `max_calls` / `calls_left` now mean **neural calls** everywhere,
documented on `Budget` itself. Any paper metric written as "calls/query" means
neural inference calls; controller actions are reported separately as
`logical_actions`.

---

## 60. FINAL CORRECTION C — configurable is not calibratable

### 60.1 The first pass conflated them

§41 inventoried 77 values and said all were "uncalibrated architecture
defaults". True but insufficient: it left the impression of 77 future degrees of
freedom, and `awardWonBy` has ten validation examples.

### 60.2 Every value is now classified

`src/cover_kbc/calibration.py` is a machine-readable inventory. Each value has
exactly one category:

| category | count | may be fitted to data? |
|---|---|---|
| `SEMANTIC` — schema or programme fact | 7 | no |
| `GUARD` — numerical safety constant | 2 | no |
| `COST` — runtime quantity to be **measured** | 10 | no |
| `STRUCTURAL` — human-designed architecture constant | 51 | no |
| `CALIBRATABLE` | **7** | **yes** |

Nothing was deleted to lower the count. A cost prior stays configurable for
engineering reasons while being classified as measurable rather than an F1 knob.

### 60.3 Degrees of freedom: 5, not 77

The seven calibratable knobs collapse to **five distinct decisions**, because a
global fallback and its per-relation override are two knobs on one decision:

| decision | knobs |
|---|---|
| `acceptance_operating_point` | `ScoringConfig.accept_score` |
| `verifier_operating_point` | `ScoringConfig.min_valid_prob` + `VerificationPolicy.accept_valid_prob` |
| `adaptive_stopping_point` | `ControllerConfig.residual_stop` + `StoppingPolicy.residual_stop_threshold` |
| `numeric_cluster_diameter` | `SelectionPolicy.numeric_cluster_threshold` |
| `capacity_cluster_preference` | `SelectionConfig.capacity_support_ratio` |

`Parameter.decision` makes this machine-checkable, so future calibration tooling
cannot treat the pair as two search dimensions.

Per-relation calibration is the exception (three parameters), justified where the
relations genuinely differ — audit 0007 §18 measured death and stock wanting a
0.60 verifier operating point against borders' 0.50.

### 60.4 Revised calibration protocol

Replaces §42:

1. freeze architecture and code;
2. run the frozen models on **train or a documented internal split only**
   (`ALLOWED_CALIBRATION_SPLITS`; val and test are refused);
3. **populate `COST` parameters from measured runtime statistics** — action
   costs and yields are measurements, not optimisation targets;
4. calibrate **only the five `CALIBRATABLE` decisions**;
5. record the chosen values, the split and the objective;
6. freeze the measured config;
7. run val **once**.

No coordinate search over YAML. No iterative val tuning. No neural fitting. For
tiny relations, prefer the shared default over a fitted per-relation value.

---

## 61. Post-correction re-validation

Every earlier invariant re-checked after all three corrections: candidate
identity and provenance intact; multi-template verification still executes where
budget permits; the award loop still terminates; borders still finish cheaply;
the death gate still behaves correctly; area and capacity retain diameter-bounded
clustering; staged and interleaved still agree for all six relations.

No architectural workaround was added to recover an earlier test output — the
alias fixture's honest 2/3 is recorded instead.

**948 passed, 3 skipped, 0 failed, 0 xfailed** (up from 929).
System suite: 125. `pyflakes` clean. Parameter budget PASS at 28.67B.
`benchmark/` unchanged.

---

---

## 56. Final system-level verdict

**PASS — ARCHITECTURE FREEZE CANDIDATE** (after the §§58-61 corrections).

Treated as one system, COVER-KBC implements the frozen proposal for all six
official relations. Algorithm 1 is the actual production path in both execution
modes; the stateful loop survives arbitrary model role swaps; logical semantics
no longer depend on which model is resident; every available mechanism is
reachable; `F`/`L`/`X`/`C`/`U` stay disjoint from acquisition through to the
emitted row; `q(o)` and `q_res` remain distinct; Modules 6 and 8 share coherent
cluster geometry that is now provably diameter-bounded; the alias seam has an
an explicit conservative decision — strict identity retained, because no
parser-level restatement evidence exists to justify anything stronger;
empty reasons stay distinct end to end; the writer serialises and nothing more;
the official schema is accepted; the hard call budget counts **actual neural
invocations** and reconciles exactly for all six relations; the calibration
surface is **five decisions**, not seventy-seven knobs; the parameter budget
passes at 28.67B; and there is no retrieval, no training and no real-model
experiment.

This freezes the **architecture**, not the configuration. The measured
configuration cannot freeze until the outstanding real-model measurements in
§53 and the calibration protocol in §42 have been carried out.

---

## 57. Recommended next milestone

**Independent Codex whole-repository review against
`COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` and audits 0003–0012.**

Nothing has been committed or pushed. The checkpoint decision belongs to
external review.
