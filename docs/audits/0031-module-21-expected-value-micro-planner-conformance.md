# Audit 0031 — Module 21: Expected-Value Micro-Planner Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: **M21**, the last numbered architecture module.
Mode: **shadow**, **disabled by default**, **uncalibrated**, **zero neural calls**.

---

## 1. Objective and scope

Implement proposal §17's expected-value micro-planner: the utility equation, the
historical-bin contract, the policy-coefficient contract, one- and two-step
micro-lookahead, deterministic `arg max` with a strict threshold, and STOP.

In scope: the planner machinery, the complete historical-bin schema, the
complete calibration schema, the action taxonomy and adapters, the full-state
snapshot, and a shadow pipeline seam.

Out of scope and not implemented: DoLa, any TRAIN calibration, any production
activation, any action execution.

**M21 ranks actions and executes none.** It generates nothing, reserves
nothing, mutates nothing, and no production decision reads it.

---

## 2. The exact §17 equation

> ```
> U_t(a) = α·Ĝ_verified(a) + β·ΔR̂(a) + γ·ΔĤ(a)
>          − δ·Ĉost(a) − η·R̂edundancy(a) − κ·F̂P(a)
> ```
>
> *"The estimates come from relation-specific historical bins on TRAIN; no
> neural policy is trained. M21 selects a\* = arg max U_t(a) if U_t(a\*) >
> τ_continue; otherwise it returns STOP."*
>
> *"COVER does not require full MCTS. Instead it uses 1–2 step micro-lookahead
> over a small action space."*

Transcribed once, in `micro_planner.utility`, and asserted term by term against
distinct prime coefficients so no two terms can be confused
(`test_the_section_17_equation_is_implemented_exactly`).

**No material conflict between this brief and the proposal was found.**

---

## 3. §17.1's policy examples

> * **Capacity**: three independent values form a tight cluster + Qwen UNKNOWN →
>   accept rather than wasting verifier loops.
> * **Award**: novelty remains high but verification reserve is unused → verify
>   the shortlist before opening another facet.
> * **Border**: direct/compass sets are stable → stop early.
> * **Death**: no candidate, but null evidence is only failed recall → run the
>   fresh/candidate-free branch before returning empty.

All four are reproduced from synthetic bins in §34–§37, with **no
relation-specific branch** anywhere in the module.

---

## 4. Appendix C I/O

> **M21 | Expected-Value Micro-Planner | Pick next action by expected verified
> gain, uncertainty reduction, redundancy and cost. | Neural: No**

Implemented as `full state + legal actions -> next action | STOP`.

---

## 5. Architecture position

```
Layer 6 - Test-Time Control: M7 Active Controller -> M20 Relation Budget
                             Scheduler -> M21 Expected-Value Micro-Planner
```

M21 is the last numbered module. M7 is production; M20 and M21 are shadow.

---

## 6. Relationship to core Module 7

M7 is **unchanged in git** — `controller.py` and `types.py` both clean — and
remains the production controller. M21 is the shadow upgraded planner beside it,
exactly as M19's `R_t` sits beside M6's `q_res`.

M21 contains no `pending_action`, `action_score`, `should_stop`, `ProgramState`
or `finalize`, so it cannot reach M7's state or reuse its scoring
(`test_module_7_is_untouched_by_the_planner`). Production execution is not
routed through it, staged role swapping is untouched, and M7's `Budget` is not
modified.

Core M7 action types adapt onto canonical planner families through
`core_action_family`, so one semantic action never competes with itself under
two names. `STOP` is excluded from the mapping by construction.

---

## 7. Relationship to Module 19

Unlike M20, M21 **does** consume Layer-5 coverage state — but as *context for
bin selection*, never as a utility term. §17's term is the expected **reduction**
in residual from an action, not the current residual.

The module contains no `+ residual`, `utility += `, `+= r_t` or `residual *`.
Behaviourally, two residuals inside one calibrated bucket give identical
estimates and identical decisions
(`test_r_t_is_context_for_binning_and_never_a_utility_term`).

---

## 8. Relationship to Module 20

Three questions stay separate and ordered: **legality** (the owning registry),
**affordability** (Module 20), **value** (M21).

M21 reuses `BudgetActionDescriptor`, `ActionCost` and the M20 ledger, and
recomputes none of it — no `discovery_cap`, `class_cap` or `available_calls`
appears in the module, and affordability is established by *calling* M20's
`reserve` on a deep copy (AST-checked).

A Module 20 denial is absolute: an action with utility 1000 that M20 refuses is
recorded as legal, listed as denied, and **not selectable**; the cheap
alternative wins (`test_a_module_20_denied_action_is_never_selectable`). An
unused reserve creates no utility by itself
(`test_an_unused_reserve_alone_creates_no_utility`).

---

## 9. Files changed

New:

* `src/cover_kbc/control/planner_types.py` — public contract
* `src/cover_kbc/control/historical_bins.py` — bin, binning spec, package, lookup
* `src/cover_kbc/control/micro_planner.py` — utility, planner, config, adapters
* `tests/test_micro_planner.py` — 67 tests
* this audit

Modified: `src/cover_kbc/control/__init__.py`, `src/cover_kbc/pipeline.py`
(Phase-C shadow seam), `scripts/run_staged.py`, `scripts/run_cover.py`, three
experiment configs (disabled, uncalibrated), and two earlier tests whose
"M21 is absent" assertions were rescoped now that M21 legitimately exists —
Layer 5's config scan narrowed to the `coverage_gap` block, and M20's narrowed
to proving M20's own modules contain no planner logic and import none.

Unmodified and verified: `controller.py`, `types.py`, `coverage.py`,
`selection.py`, `benchmark/`.

---

## 10. Public types

`ActionFamily`, `EstimateSource`, `DecisionKind`, `StopReason`,
`ActionExecutionStatus`; `PlannerActionCandidate`, `PlannerStateSnapshot`,
`ActionUtilityBreakdown`, `PlannerCalibration`, `SuccessorDiagnostics`,
`DeniedAction`, `MicroPlannerDecision`; `HistoricalActionBin`, `SuccessorStat`,
`StateBinningSpec`, `HistoricalBinPackage`.

All immutable, validated, versioned and serialisable. None exposes
`planner_confidence`, `candidate_truth_probability` or `final_answer_score`.

---

## 11. The full-state snapshot

`PlannerStateSnapshot` carries the query identity, ProgramType, round, and each
upstream layer **as its own object**: Module 9's profile, the corrected Layer-4
state, Module 19's coverage state, Module 20's plan and ledger, the executed
action identities, and the resident model role.

Nothing is flattened into a score, because the bins select on these
individually and collapsing them would destroy exactly the distinctions the
bins are keyed by. Nothing upstream is mutated (§45).

---

## 12. Legal-action ownership

M21 **never derives legality**. `PlannerActionCandidate` requires a
`legal_provenance` string and raises without one, so an action with no declared
owner cannot even be constructed.

An action its owner marks `INELIGIBLE` is not resurrected however valuable it
looks, and an already-executed action is excluded unless its contract marks it
`repeatable` — a resample may repeat, a one-shot probe may not
(`test_an_ineligible_action_is_never_resurrected`,
`test_an_already_executed_action_is_excluded_unless_repeatable`). Nothing is
silently dropped: every exclusion is recorded with its reason.

A weak facet, a high residual or an unsupported candidate are reasons an action
might be *valuable*. They are never reasons it is *permitted*.

---

## 13. Action taxonomy and adapters

Nine canonical families cover the proposal's action space and both vocabularies:
`SPECIALIST_PROBE`, `PSEUDO_MEMORY_PROBE`, `CANDIDATE_FREE_RECALL`,
`BLIND_VERIFY`, `SPECIALIST_VERIFY`, `COUNTERFACTUAL_VERIFY`, `REVERSE_CHECK`,
`CROSS_MODEL_CHECK`, `RESAMPLE`.

`core_action_family` adapts every core M7 action type onto one of them, so no
duplicate semantic action exists. Classification is by module and action
identity — never by prompt text.

**`STOP` is deliberately not a family.** §17 makes it the fallback, and giving
it a fabricated utility would let it compete on a number nobody calibrated
(`test_stop_is_not_an_action_with_a_fabricated_utility`).

---

## 14. Historical-bin schema

`HistoricalActionBin` carries relation, ProgramType, state bin key, action
family, optional target class, `support_count`, **all six** §17 estimates, and
optional successor statistics.

Every estimate is explicit and validated. A payload missing one raises rather
than defaulting (`test_a_missing_estimate_fails_and_is_not_read_as_zero`).

Declared units and ranges (`ESTIMATE_UNITS`), so no hidden normalisation is
needed or possible — the module contains no `sigmoid`, `tanh`, `normalize`,
`rescale` or `clip(`:

| Estimate | Unit / range |
| --- | --- |
| `expected_verified_gain` | verified objects, ≥ 0, unbounded above |
| `expected_delta_r` | residual points in [−1, 1] |
| `expected_delta_h` | uncertainty points in [−1, 1] |
| `expected_cost` | physical neural calls, ≥ 0, unbounded above |
| `expected_redundancy` | redundancy fraction in [0, 1] |
| `expected_fp` | false-positive risk in [0, 1] |

Out-of-range, negative-where-non-negative and non-finite values all raise.

---

## 15. History source semantics

`EstimateSource` is `TRAIN_CALIBRATED` or `SYNTHETIC_TEST`, and `is_production`
is true only for the former. `load_history` and `load_planner_calibration`
**refuse** a synthetic package unless `allow_synthetic=True`, which only tests
pass; `build_micro_planner` refuses one outright. A planner whose calibration
source disagrees with its history source raises.

Every package that exists today is `SYNTHETIC_TEST` and lives in a test fixture.

---

## 16. State-binning versioning

`StateBinningSpec` is versioned and owned by the package.

* **Categorical** features — ProgramType, relation, a Module 9 risk grade,
  residual availability, `failed_recall_only`, candidate presence, numeric
  cluster count — are read directly and need no fitted boundary.
* **Numeric** features are bucketed using cut points the **package** supplies.

No production threshold is hard-coded: the module contains no `0.3`, `0.5` or
`0.7`. Boundaries must be ascending and distinct, and the spec must be
versioned (`test_binning_boundaries_belong_to_the_package`).

---

## 17. No production bins exist

**No TRAIN historical bins have been built**, and this milestone forbids
building them. Shipped configs carry `historical_bins: null` and
`planner_calibration: null`, and enabling the planner without either fails
loudly:

> `micro_planner.enabled is true but no historical_bins package is supplied;
> §17 states the estimates come from relation-specific historical bins on
> TRAIN, and none exist yet`

---

## 18. Planner calibration schema

`PlannerCalibration` carries `alpha`, `beta`, `gamma`, `delta`, `eta`, `kappa`,
`tau_continue`, `lookahead_depth`, a version and a source.

Validated: every coefficient finite; the three **subtracted** coefficients
(δ, η, κ) non-negative, because a negative one would silently turn a penalty
into a reward; the three added coefficients non-negative; depth in {1, 2}.

These are **deterministic policy coefficients, not neural weights** — nothing is
fitted by gradient and nothing updates at run time (§42).

---

## 19. No production coefficients or τ

§17 names all seven and supplies **no value for any of them**. None is shipped.
Shipped config blocks contain no `alpha`, `beta`, `gamma`, `delta`, `eta`,
`kappa` or `tau` token at all, and no integer or float field
(`test_shipped_configs_carry_no_history_and_no_coefficients`).

---

## 20. Utility components

`ActionUtilityBreakdown` records all six raw estimates **and** all six weighted
contributions **and** the total, so the arithmetic can be checked line by line
against the proposal.

---

## 21. Exact arithmetic

With α=2, β=3, γ=5, δ=7, η=11, κ=13 and estimates 1.5 / 0.4 / 0.25 / 2.0 / 0.5 /
0.125, every term is asserted individually and the total equals

```
2(1.5) + 3(0.4) + 5(0.25) − 7(2.0) − 11(0.5) − 13(0.125)
```

There is **no seventh term**: identical estimates and coefficients give an
identical utility in all six relations, so no relation-specific adjustment can
exist (`test_no_hidden_term_or_relation_specific_bonus`). No `bonus_term`,
`boost`, `mandatory_view`, `action_score`, `prior_term` or `adjustment` appears.

---

## 22. G_verified semantics

Expected **newly verified** factual gain — not a raw candidate count, not a
mention count, not an unverified discovery. Ten raw mentions with nothing
verified is `expected_verified_gain = 0.0` and loses to one newly verified
object (`test_verified_gain_is_not_a_raw_candidate_count`). The declared unit
says "verified objects".

---

## 23. ΔR semantics

Module 19 defines `R_t` as residual **search need**, so `ΔR̂` is the expected
**reduction** in it. Positive means the action is expected to reduce `R_t`, and
the sign is not reversed: an action with `ΔR̂ = +0.4` scores strictly above one
with `−0.4` (`test_delta_r_sign_means_reduction_in_residual`).

The estimate comes from the bin. There is no invented transition model and no
`current R_t − imagined R_t` computation.

---

## 24. ΔH semantics

Consumed from the bin under the package's own versioned diagnostic definition.
M21 recomputes **no** entropy: the module contains no `entropy`, `log2` or
`math.log`, and no new candidate confidence
(`test_delta_h_and_fp_and_redundancy_come_from_history_not_recomputed`).

---

## 25. Cost_hat versus Module 20's safe cost

Two distinct notions, and the distinction is enforced:

* **Module 20's safe reservation** is the conservative upper bound and the
  **only** thing that governs affordability;
* **`Ĉost`** is the historical expectation and appears only as a utility term.

An action with historical cost 0.5 but a three-call safe reservation against a
two-call ceiling is **denied**, not funded on the strength of its cheap
expectation (`test_expected_cost_is_distinct_from_module_20_safe_cost`).

---

## 26. Redundancy semantics

`R̂edundancy` is a historical **value** estimate, not a legality rule. Exact
execution history still governs legal-action deduplication (§12); the planner
never recounts prompts, models or candidates to synthesise a redundancy term.

---

## 27. FP semantics

`F̂P` is action-level expected false-positive risk from TRAIN history. Current
contradiction, near-miss flags and verifier INVALID are **state features for bin
selection only** — the module contains no `contradiction`, `near_miss` or
`verifier_invalid`. No factual candidate is rejected by M21.

---

## 28. Depth-1 planner

Among actions that are legal **and** affordable **and** fully estimated:
`a* = arg max U_t(a)`.

* strictly greater than `τ_continue` → return `a*`;
* **exactly** `τ_continue` → **STOP**;
* below → STOP.

All three boundaries are asserted (`test_the_threshold_is_strictly_greater`).

---

## 29. Depth-2 micro-lookahead

```
value(a₁) = U(a₁) + Σ_i p_i · max_{a₂} U(a₂ | successor bin i)
```

The successor distribution is **recorded TRAIN history**: `SuccessorStat`
carries a probability and a successor state-bin key, probabilities must sum to
1.0, and a repeated successor bin raises. **No model is asked to imagine a
future state.**

Verified on a fixture where probe (1.0) unlocks a high-value verify (5.0) and
verify (1.2) unlocks only a low-value probe (0.1): totals 6.0 and 1.3, and probe
wins (`test_depth_two_adds_the_expected_best_successor_utility`).

Maximum depth is exactly two — depth 3 and depth 0 both raise — and the module
contains no `mcts`, `beam`, `rollout`, `expand_node` or `recurse`.

If a bin lacks successor statistics, depth-2 **fails loudly** rather than
falling back; depth-1 over the same package still works
(`test_depth_two_needs_recorded_successor_statistics`).

---

## 30. Why no discount was introduced

§17 gives the utility equation and the 1–2 step horizon, and supplies **no
discount term**. Introducing one would be a fitted number wearing a structural
hat — precisely what §3 of the brief forbids.

The extension is therefore **undiscounted and additive**, a minimal
deterministic finite-horizon combination. This is a *structural* definition, not
a calibrated one, and the module contains no `discount` or `decay`.

---

## 31. Module 20 affordability inside the lookahead

A second action counts only if it is still affordable **after** the first one's
Module 20 reservation. The planner deep-copies the ledger, reserves `a₁` on the
copy, and screens second steps against that hypothetical — **Module 20's real
ledger is never touched** (§45).

With a two-call ceiling consumed entirely by `a₁`, the expected successor
utility is 0.0 and the path value collapses to `U(a₁)`
(`test_the_second_action_must_remain_affordable_after_the_first`).

An action whose canonical identity equals `a₁`'s cannot be its own successor
unless its contract marks it repeatable
(`test_a_one_shot_action_cannot_be_its_own_successor`).

---

## 32. Tie-breaking

Equal values break on **canonical action identity** — `(family, action_id,
target, facet)` — never on insertion order, object id, hash order or a clock.
The reason is persisted. Reversing the input order selects the same action, and
the module contains no `random`, `uuid`, `time.time`, `datetime` or `id(`.

---

## 33. STOP semantics

Three distinct reasons:

| Reason | When |
| --- | --- |
| `NO_LEGAL_ACTION` | nothing survived owner screening |
| `NO_AFFORDABLE_ACTION` | legal actions existed; Module 20 denied all |
| `UTILITY_BELOW_THRESHOLD` | complete estimates, best value ≤ τ_continue |

M21 has **no stop rule of its own**: a low residual, a stable set, an UNKNOWN
verifier or a non-zero budget are state features, never independent stop
conditions. The module contains no `residual <`, `r_t <`, `set_is_stable`,
`budget_exhausted` or `verifier_unknown`.

**A missing calibration is not STOP.** It raises — a planner reporting STOP when
it cannot think looks exactly like one that thought and decided to stop
(`test_a_missing_configuration_is_not_stop`).

---

## 34. Capacity example

Tight cluster, verifier UNKNOWN, low-value verification bins → every affordable
action falls at or below τ_continue → **STOP** with
`UTILITY_BELOW_THRESHOLD`.

M21 accepts nothing: the decision payload contains no `accept`, `reject`,
`ObjectEntities` or `final`. Module 8 finalises the already-resolved state.
Driven entirely by synthetic bins and the equation.

---

## 35. Award example

Novelty high, verification reserve unused, a verification bin worth more than a
new discovery facet, **both affordable** → the planner selects
`verify_shortlist` over `open_facet`.

Value decided, not affordability — both appear in `affordable_actions`. There is
no `if relation == award` anywhere: the module contains no relation name at all
(`test_no_relation_specific_branch_exists`).

---

## 36. Border example

Stable coverage and low-value remaining actions → every utility falls below
τ_continue → **STOP**. No border-specific stop rule.

---

## 37. Death example

No locality candidate, null evidence only failed recall, a legal and affordable
candidate-free action with positive expected utility → the planner selects
`candidate_free`.

**Audit 0024 preserved**: failed recall never becomes substantive NULL, and the
decision payload contains no `substantive_null`, `final_empty`, `is_empty` or
`ObjectEntities`. M21 selects an action; it does not emit an empty prediction.

---

## 38. Stock invariants

Stock's structure survives as action identities and state features. M21 reopens
no conditional branch its owner declares illegal (§12), and Audit 0027 §20A is
untouched — the module contains no `ALTERNATE_RECOVERED`, `listing_gate` or
`parent_subsidiary`, so it cannot reinterpret an alternate recovery as a
contradiction.

---

## 39. Numeric invariants

Cluster identities come from M12/M16/Layer 4 and are used only as state
features. The module contains no `recluster`, `cluster_values`, `tolerance`,
`0.05` or `winner`: no reclustering, no evaluator tolerance, no factual winning
value. M21 chooses control actions or STOP.

---

## 40. NULL invariants

Covered in §37. `failed_recall_only` is available as a categorical binning
feature, so history can distinguish that state — which is exactly how §17.1's
death example is reproduced without a hard-coded rule.

---

## 41. Zero-neural proof

* **AST import scan** — no `torch`, `transformers`, `requests`, `httpx`,
  `urllib`, `socket`, `numpy`, `sklearn`, and no `cover_kbc.models`.
* **Source scan** — no `LMRuntime`, `GenerationRequest`, `score_labels`,
  `generate(`, `runtime`, `dispatch`, `swap_model` or `load_model`.
* **Behavioural** — the six-relation shadow run made **58 runtime calls with M21
  on and 58 with it off**.

---

## 42. No online learning

No `backward`, `optimizer`, `fit`, `train`, `reward`, `bandit`,
`policy_gradient`, `epsilon_greedy`, `ema_update` or `posterior`. The package is
frozen: it exposes no `update`, `observe`, `record_outcome`, `fit` or `add_bin`,
and its fields cannot be reassigned
(`test_no_online_learning_path_exists`). A TRAIN-built package stays frozen for
VAL and TEST, which is what protects test blindness.

---

## 43. Shadow pipeline seam

```
Layer4EvidenceState + M19 CoverageGapState + M20 budget state + legal actions
    -> M21 shadow decision
```

Sits at the Phase-C seam after Module 19, because §17 plans over the full state.
The pipeline refuses a planner without Module 19 and Module 20.

**The live legal-action list is empty on purpose.** No module yet exposes an
owner-declared legal-action surface, and M21 may not invent legality — so the
honest live decision is STOP with `NO_LEGAL_ACTION` until Layer-6 integration
supplies one. Fabricating a legal-action list to make the seam look busy would
have violated §12.

No execution follows the decision. `M7 -> M8` is unchanged.

---

## 44. Shadow invariance

Six relations, full stack (M9–M11, four specialists, M16, Layer 4, M19, M20 with
synthetic calibrations), M21 on versus off:

* runtime calls **identical** (58);
* predictions **identical**;
* `consensus_results`, `layer4_results`, `coverage_gap_results`,
  `relation_budget_results`, all four specialist result lists, `query_profiles`,
  `prompt_programs`, `retrieval_results` all **equal**;
* `micro_planner_results` is 6 with M21 on and empty with it off.

Only M21's own artefact appears.

---

## 45. Persistence

`micro_planner.jsonl`, one record per planned round, written only when M21 is
explicitly enabled with valid packages — which, with shipped configs, is never.

Each record carries the planner version, query identity, round, state signature,
decision kind, τ_continue, lookahead depth, history and calibration versions,
legal / affordable / denied action lists, the full utility breakdown per action,
successor diagnostics at depth 2, the selected action and value, the stop reason,
the tie-break reason, and errors. Round-trips exactly.

No record contains `gold`, `ObjectEntities`, `prediction`, `accepted`,
`rejected`, `leaderboard` or `f1` — scanned with the disclaimer excluded, since
its own wording is what denies them.

---

## 46. Determinism

Identical state, actions, budget, bins and coefficients give a byte-identical
decision. Reversing the action order changes neither the selection nor the state
signature. No randomness, clock, UUID or hash-order dependence.

---

## 47. Error handling

`PlannerError` or `ValueError` is raised, never converted to STOP, for: query,
ProgramType and per-layer identity mismatch; a missing upstream layer;
unsupported planner version; mismatched history/calibration source; a synthetic
package in production; no bin match; a duplicate bin; a missing or out-of-range
estimate; a non-finite value; a malformed successor distribution; an action
without legal provenance; a duplicate action identity; a foreign Module 20
descriptor; a missing affordability state; unsupported lookahead depth; missing
depth-2 successor statistics; and an unknown action family.

Bin ambiguity is unreachable from a validly constructed package, because
duplicates are refused at construction — documented and tested rather than left
as an untested branch
(`test_bin_specificity_is_deterministic_and_ambiguity_unreachable`).

---

## 48. Tests

`tests/test_micro_planner.py`, **67 tests**, covering the brief's 94 numbered
requirements: the equation and its exact arithmetic, non-neurality and no online
learning, absent production numbers and fixture containment, full-state and
upstream identity, legality/affordability/value separation, the bin contract and
lookup, each utility component's semantics, threshold and tie-breaking, depth-1
and depth-2, the four §17.1 policy scenarios, mutation-freedom, determinism,
serialisation and architecture boundaries.

Full suite: **2558 passed, 3 skipped** (2491 before M21).

Every subject is fictional; every package and coefficient set is
`SYNTHETIC_TEST`.

One improvement was made to the source-scanning helper in this suite: string
literals inside `raise` statements are stripped along with docstrings, since an
error message *documents* a prohibition rather than implementing one, and
scanning it made forbidden-token assertions fire on their own explanatory prose.

---

## 49. Pyflakes

`python -m pyflakes src/ tests/ scripts/` — **clean**.

---

## 50. Model budget

`scripts/audit_model_budget.py` → **PASS**, total **28.67B**. M21 adds no model,
no checkpoint and no parameter.

---

## 51. Benchmark integrity

`git status --porcelain benchmark/`, `git diff -- benchmark/` and
`git diff --cached -- benchmark/` are all **empty**, run directly and asserted
by test. Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged.

---

## 52. No TRAIN, VAL or TEST

No historical bins were built and no coefficient was chosen. No split was read;
the module imports nothing from `cover_kbc.data`. VAL and TEST were never
executed and no leaderboard submission was made. The only mention of TRAIN in
the source is §17's own sentence explaining why no numbers exist.

---

## 53. Challenge compliance

* **Closed book** — no web, RAG, Wikipedia, Wikidata, KB lookup, vector
  database, external corpus, entity linker or search API; no network-capable
  import.
* **No learned component** — no RL, policy gradient, learned router, classifier,
  value network or reward model; no fit, train, backward or optimizer. The
  planner is deterministic arithmetic over declared coefficients and recorded
  statistics.
* **Frozen model profile** — unchanged, 28.67B, two models.
* **No DoLa.**
* **Benchmark immutable** — verified three ways.

---

## 54. Explicit non-goals

Not implemented, not stubbed, not referenced in executable code:

* DoLa.
* Any TRAIN calibration of bins or coefficients (§17, §19).
* Any action execution, reservation, generation or verification (§43).
* Any factual judgement — no candidate accepted, rejected or scored (§27, §34).
* Any replacement of Module 7 or Module 8 (§6).
* Any stop rule of M21's own invention (§33).
* Any search beyond two steps (§29).

---

## 55. Verdict

**PASS.**

Proposal §17 is implemented exactly: the six-term utility equation with its
signs, `arg max` with a strictly-greater threshold, STOP as the fallback, and
1–2 step micro-lookahead over recorded successor bins with no MCTS, no third
step and no invented discount. Every estimate comes from a TRAIN historical bin,
missing is never zero, and no action is ever silently dropped from the ranking.

Legality, affordability and value remain three separate questions in that order:
an ineligible action is never resurrected and a Module 20 denial is absolute.
All four §17.1 policy examples are reproduced from synthetic bins with no
relation-specific branch. Audit 0024's NULL semantics and Audit 0027 §20A both
survive.

M21 executes nothing, mutates nothing and holds nothing: 58 runtime calls with
it on and 58 with it off across all six relations, every M9–M20 artefact equal,
Module 7 and Module 8 untouched.

**Nothing is calibrated.** No production historical bins exist. No production
α, β, γ, δ, η or κ exists. No production τ_continue exists. TRAIN calibration
has not been performed, no fake values were introduced, and enabling the planner
without both packages fails loudly.

    M20 Relation Budget Scheduler           DONE
    M21 Expected-Value Micro-Planner        DONE

    Layer 6 modules complete.

Next step: **Layer 6 integration / conformance audit**, on a separate authorised
brief. Calibration is not begun here.
