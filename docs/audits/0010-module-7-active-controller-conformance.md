# Audit 0010 — Module 7: Active Controller and Adaptive Stopping Conformance

Status: **complete — 13 defects found, 13 fixed; audit 0009 §41 resolved**
Revision: second pass closed the staged role-swap orchestration gap that the
first pass had found but left open (§§31, 38.11, 47).
Date: 2026-08-04
Scope: Module 7 only. Module 8 remains unreviewed.

---

## 1. Objective and scope

Answer the review's central question:

> Does the current controller **actually** implement the stateful COVER loop, or
> does it merely score/stop after most actions have already been run by a fixed
> pipeline?

**Answer: interleaved execution was a real loop; staged execution was not.** In
staged mode the controller ran only during Phase A, Phase B applied a fixed
`verification_targets` list, and no controller decision made after verification
could ever execute. That is diagnostics, not active control, and it is fixed.

In scope: `src/cover_kbc/controller.py`, the controller loops and action
execution in `src/cover_kbc/pipeline.py`, the gate's model-role routing, and the
persistence Module 7 needs across the staged seam. Out of scope and untouched:
final selection and output policy (Module 8).

No model was downloaded or run.

---

## 2. Proposal requirements

| § | Requirement |
|---|---|
| 13 | "COVER-KBC uses a **deterministic controller** for reproducibility and rule safety." |
| 13.1 | `A_t(a) = αŶ_t(a) + βG_t(a) + γU_t(a) − λC(a) − ρD_t(a)`, with `Ŷ` = expected marginal verified-candidate yield, `G` = relevance to an uncovered facet or unresolved candidate, `U` = expected uncertainty reduction, `C` = expected token/call/latency cost, `D` = redundancy with evidence already acquired. |
| 13.1 | `a*_t = argmax A_t(a)` "unless a relation-specific stopping rule fires first". |
| 13.2 | Rule-based policy v1, eight steps — mandatory views first; verify high-impact uncertain candidates **before** more discovery; targeted view on a semantic gap; continue while large-set yield is high; prefer verification when yield collapses but uncertainty remains; stop on relation-specific stability; **always** stop at the hard budget. |
| 13.3 | `J_t` set stability: "High `J_t` is useful but **never sufficient alone**: a wrong or incomplete set can also be stable." |

---

## 3. Pre-work repository state

Branch `main`, HEAD `4fb1535` ("refactor: align COVER-KBC RCSE with
architecture"). Working tree clean; Module-6 work committed as expected. Audits
0001–0009 accepted. 680 tests passing plus one strict xfail (audit 0009 §41).

---

## 4. Existing controller architecture

`legal_actions` → `score_action` → argmax, with `should_stop` overriding
afterwards; `record_outcome` folding results into RCSE state. Seven action
types were declared. `_adaptive_discovery` in the pipeline drove the loop for
Phase A / interleaved runs.

---

## 5. Algorithm-1 conformance matrix

| Algorithm-1 step | Implementation | Executable? | Correct owner? | Outcome |
|---|---|---|---|---|
| CompileRelation | `compile_query` | yes | Module 0 | ✓ |
| RouteProgram | `contract.program_type` | yes | Module 1 | ✓ |
| InitState | `build_graph`, `RCSEState()` | yes | Modules 3/6 | ✓ |
| Mandatory initial actions | `legal_actions` + `view_gap_relevance` | yes | Module 7 | **fixed** — priority now derives from the contract, not the enum member (§11) |
| Execute / ParseNormalize / UpdateGraph | `_run_discovery_view` etc. | yes | Module 2/3 | ✓ |
| ShouldStop | `should_stop` | yes | Module 7 | **fixed** — three defects (§27) |
| HighestImpactUnresolved | `candidate_impact` | yes | Module 7 | **added** (§20) |
| WorthVerifying | `verify_first_unresolved` + tier | yes | Module 7 | **fixed** — verification now precedes stopping (§20) |
| RCSE | `estimate_residual` | yes | Module 6 | ✓ |
| ChooseAction | `choose_action` | yes | Module 7 | **fixed** — tie-break, hidden bonuses, STOP authority |
| Execute selected action | pipeline dispatch | **was partly no** | Module 7 | **fixed** — REVERSE_CHECK and RESAMPLE now execute; staged runs a real loop, and an action needing the other model is dispatched after a role swap rather than persisted and abandoned (§31) |
| UpdateState | `record_outcome` | yes | Module 6 | ✓ |
| Finalize | `finalize` | yes | Module 8 | ✓ (untouched) — but now refused while executable work remains (§31.3) |
| **loop** (`while not ShouldStop and budget remains`) | `phase_resolve` role-swap loop | **was no** | Module 7 | **fixed in the second pass** — the outer loop now continues across model residency changes (§31.2) |

No step is decorative.

---

## 6. Complete action-space inventory

| Action | Legal when | Model role | Arguments | Executed by | Test |
|---|---|---|---|---|---|
| `RUN_VIEW` | mandatory view not yet executed | enumerator | `view_id` | `_run_discovery_view` | ✓ |
| `RUN_FACET` | optional view/facet not yet executed | enumerator | `view_id`, `facet_id` | `_run_discovery_view` | ✓ |
| `REVERSE_CHECK` | relation declares a reverse view **and** this candidate lacks that mechanism | enumerator | `view_id`, `candidate_key` | `_run_reverse_check` | ✓ |
| `RESAMPLE` | no structural gap remains **and** the view is stochastic | enumerator | `view_id` | `_run_resample` | ✓ |
| `VERIFY` | candidate in the VERIFY tier, not rejected, not already verified | verifier | `candidate_key` | `_verify_one` | ✓ |
| `ADVERSARIAL_VERIFY` | candidate in the ADVERSARIAL tier, same guards | verifier | `candidate_key` | `_verify_one` (multi-template) | ✓ |
| `CROSS_MODEL_CHECK` | branch enabled, second model present, not already run | verifier | — | `_run_cross_model_recall` | ✓ |
| `STOP` | `should_stop` agrees | none | — | loop exit | ✓ |

`ModelRole` is now declared per action (`ACTION_ROLE`) rather than inferred at
execution time, which is what lets staged orchestration route correctly instead
of substituting whichever runtime is resident.

`Action.identity` is `(type, view_id, candidate_key)` — so `reverse(A)` and
`reverse(B)` are distinct instances for deduplication, redundancy and
tie-breaking. No DoLa action exists.

---

## 7. Legal-action analysis

Legality is a property of **state**, never of the action name. Newly enforced
conditions, each tested:

- a view already executed is not offered again;
- a candidate that already carries the reverse mechanism is not reverse-checked
  again — and reverse-checking one candidate does not mark another as covered;
- a **hard-rejected** candidate is never offered for verification;
- an **auto-accepted** candidate is never offered for verification;
- an already-verified candidate is not re-verified with unchanged inputs (it
  would return the same verdict and produce an identical edge, which Module 3
  correctly rejects);
- resampling is offered only once no structural gap remains;
- with the budget exhausted, `STOP` is the only legal action;
- an action *instance* already executed is not offered again (keyed on the full
  identity, so `reverse(alpha)` and `reverse(beta)` are tracked apart);
  `RESAMPLE` is exempt because repetition is its purpose.

**Second-pass correction.** `allowed_roles` no longer filters the *choice*. The
first pass restricted each phase's legal actions to its resident role, which
looked safe but meant a phase could never *want* the other model — so
`pending_action` was unreachable and staged execution quietly degraded to
"whatever this phase can manage". The controller now scores the whole legal
space and the role check happens at **execution**: a winner needing the other
model becomes the pending action (§31.2).

---

## 8. Reverse-action reachability fix — DEFECT 1 (blocking, fixed)

Audit 0009 §41 recorded that `legal_actions` skipped reverse views entirely, so
`REVERSE_ALTERNATE` was counted available by Module 5 but was never schedulable,
leaving three relations with a permanent mechanism gap.

`REVERSE_CHECK` now enumerates **per candidate**, using Module 2's existing
`run_reverse_view` primitive through `_run_reverse_check`. It is acquisition,
not verification: free text, no label scoring, no calibration, and its output
becomes ordinary candidate mentions under `REVERSE_ALTERNATE`.

| relation | available `m(o)` | reachable before | reachable after |
|---|---|---|---|
| `awardWonBy` | 5 | 4 (gap 0.200) | **5 (gap 0.000)** |
| `companyTradesAtStockExchange` | 4 | 3 (gap 0.250) | **4 (gap 0.000)** |
| `countryLandBordersCountry` | 6 | 5 (gap 0.167) | **6 (gap 0.000)** |
| `hasArea` / `hasCapacity` / `personHasCityOfDeath` | 3 | 3 | 3 |

Closure is genuine: `REVERSE_ALTERNATE` was **not** removed from the
availability denominator.

### 8.1 A record-identity defect this exposed

Making reverse schedulable immediately produced
`duplicate evidence edge ... for candidate 'gamma'`. `ElicitationEngine._record_id`
hashed `subject|relation|view_id|run_id|stage` — **omitting the conditioning
candidate** — so `reverse(alpha)` and `reverse(beta)` had identical record ids
and the second was rejected as a duplicate. The candidate is now part of the
record identity. Without this, candidate-conditioned acquisition is
unschedulable in principle, not merely unscheduled.

---

## 9. Former strict-xfail resolution

`test_every_available_mechanism_is_reachable_by_some_legal_action` in
`tests/test_rcse_conformance.py` is now an **ordinary passing test**. The
`xfail(strict=True)` marker is removed and the test is not deleted. The sweep
supplies a candidate, because candidate-conditioned legality genuinely requires
one — that is the mechanism's real precondition, not a weakened assertion.

The same invariant is asserted independently in
`tests/test_controller_conformance.py::test_every_available_mechanism_is_reachable`.

---

## 10. RESAMPLE analysis — DEFECT 2 (fixed)

`RESAMPLE` existed in the enum and was scored, but `legal_actions` never emitted
it: dead.

Determining its proposal-faithful purpose produced a decisive architectural
fact: **every view Module 2 declares uses deterministic decoding**
(`temperature == 0`). Re-running one returns byte-identical text, so the repeat
carries no information and Module 3 would reject its duplicate edge.

So the legality condition is now principled rather than absent:

```python
if view.decode.deterministic:
    continue          # a greedy repeat cannot yield anything new
```

`RESAMPLE` is therefore **legal-but-never-triggered under the frozen config**,
not removed — spec section 7 names repeated sampling as a subordinate
capability, and deleting it would discard a mechanism the architecture may adopt
the moment a sampled view is declared. Both halves are tested: that no declared
view is stochastic today, and that the branch becomes legal for a view whose
decode profile is sampled. It is live code, not decoration.

Its redundancy is configured (`resample_redundancy = 0.8` plus
`repeat_redundancy_step = 0.1` per repeat), so it grows steadily less attractive
and a fresh structural action outranks it — from the score components, not a
hidden constant.

---

## 11. Mandatory-action priority analysis — DEFECT 3 (fixed)

`score_action` contained:

```python
if action.action_type is ActionType.RUN_VIEW:
    gap += 0.5  # mandatory structure has priority over optional facets
```

Two problems: the constant was hard-coded, and the priority derived from the
**enum member** rather than from mandatory status. In another relation an
optional view is also a `RUN_VIEW`, and a `RUN_FACET` would lose priority merely
for being differently named.

Replaced by `view_gap_relevance`, which reads the contract and state:

| case | `G_t(a)` |
|---|---|
| mandatory view not yet executed | `mandatory_gap_relevance` (1.0) |
| optional facet | `facet_gap × optional_gap_scale` (≤ 0.8) |
| other acquisition | `mechanism_gap × optional_gap_scale` |
| resample | 0.0 — it addresses no gap by construction |

`optional_gap_scale = 0.8` keeps spec §13.2 step 1 ("run each mandatory initial
view once") structural rather than dependent on which residual happens to be
larger. Tested with a deliberately mislabelled action: priority follows the
contract even when the enum member says otherwise.

A second hidden constant, `score += 0.5` for the verify-first preference, is now
`config.verify_first_bonus`. `test_no_hidden_numeric_bonus_survives_in_the_score`
AST-checks that `score_action`'s body contains no float literal beyond `0.0`
and `1.0`.

---

## 12. Action-score derivation

```
A_t(a) = alpha_yield        * expected_yield
       + beta_gap           * gap
       + gamma_uncertainty  * uncertainty
       - lambda_cost        * cost
       - rho_redundancy     * redundancy
```

All five components are returned alongside the score and logged per considered
action. `test_the_score_is_exactly_the_five_weighted_terms` recomputes the sum
and asserts the component set is exactly the five terms — nothing else may be
folded in.

---

## 13. Expected-yield component

`mechanism_yield_prior` — an **estimate for a future action**, deliberately
distinct from Module 6's `new_trusted`, which is an *observed past* outcome.

- an untried mechanism gets `untried_yield_prior = 0.5` — it is exactly what the
  architecture knows nothing about;
- a mechanism already run is estimated from how productive past acquisition runs
  were, so a repeatedly fruitless view is not offered as though it were fresh.

No gold, no factual lookup, no val outcomes, no trained predictor.

---

## 14. Acquisition yield vs verification utility

Kept conceptually separate, as the review requires:

- verification actions receive `expected_yield = 0.0` — label scoring is not
  entity generation, and pretending otherwise would distort the yield history;
- their value comes from `candidate_impact` (uncertainty reduction), not from
  discovery yield;
- `mechanism_yield_prior` filters Module 6's history on `is_verification`, so
  three verifier calls that resolved candidates do **not** read as evidence that
  another discovery view will generate one;
- conversely, verification is not penalised for producing no entity text.

Module 6 was not rewritten; only its existing `is_verification` field is
consumed.

---

## 15. Gap-relevance component

See §11 for acquisition actions. For `REVERSE_CHECK` it is `1 − q(o)` for the
specific candidate — the mechanism coverage that candidate is missing. For
verification it is the query's `unresolved_mass`. For `CROSS_MODEL_CHECK` it is
`mechanism_gap`.

---

## 16. Uncertainty-reduction component

`candidate_impact(candidate, contract, residual)` maps an action to the
uncertainty it can plausibly reduce, from evidence state only:

- base: the candidate's Module-5 coverage `q(o)` — broad support that still
  cannot be resolved is the case worth another action;
- raised to 1.0 by an explicit contradiction;
- raised to the maximum prompt disagreement recorded on it;
- raised to `unresolved_mass` when it sits in the adversarial tier.

`ADVERSARIAL_VERIFY` additionally receives `adversarial_uncertainty_bonus`.
Acquisition actions receive `unresolved_mass × indirect_uncertainty` — real but
indirect, so well below a targeted verification. No action receives a flat
global bonus, and no neural predictor is involved.

---

## 17. Cost component

Per-action priors, all configured: `cost_run_view` 1.0, `cost_reverse_check`
1.0, `cost_resample` 1.0, `cost_verify` 1.0, `cost_adversarial_verify` 2.0
(several templates plus a control), `cost_cross_model` 1.5.

They are explicitly **priors, not measurements**, calibratable against logged
costs once Colab runs exist. Parameter count is not used as cost; wall-clock
latency is not required.

---

## 18. Redundancy component — DEFECT 4 (fixed)

The old rule was `1.0 if action.view_id in state.executed_views else 0.0` —
**dead code**, because `legal_actions` already filtered executed views out, so
it was always 0.

`mechanism_redundancy` now keys on the **independence group**, not on prompt
wording:

- first use of a mechanism: 0.0, however many other views have run;
- a view whose independence group is already covered:
  `covered_mechanism_redundancy` (0.5);
- an explicit repeat: `resample_redundancy` plus `repeat_redundancy_step` per
  repeat already taken, so it is non-decreasing.

Tested: a fresh structural action is less redundant than a repeat, repeated
resampling grows more redundant, and a structural action outranks a repeat
through the score rather than by a special case.

---

## 19. Deterministic tie breaking — DEFECT 5 (fixed)

Ties broke on `action_type.value` alone, so `reverse(A)` and `reverse(B)` were
indistinguishable and the winner depended on enumeration order. Now
`(score, action.identity)` — type, view and candidate.

`test_ties_break_on_full_action_identity_not_just_type` builds the same
candidate set in two different orders and asserts the same action is chosen.
`test_legal_actions_are_deterministically_ordered` and
`test_the_controller_is_deterministic` cover the rest.

---

## 20. HighestImpactUnresolved / WorthVerifying — DEFECT 6 (fixed)

Spec §13.2 step 3 puts verification of high-impact uncertain candidates *before*
further budget spend. `should_stop` did not honour it: a disputed candidate in
the adversarial tier could be abandoned because the aggregate residual sat below
the stop threshold.

Added, before the generic residual check:

```python
if pending_verification and not budget.exhausted:
    return False, "candidate awaiting verification"
```

A candidate still in a verification tier, not rejected, and never verified is
unfinished business whatever the aggregate residual says.
`candidate_impact` supplies the deterministic "high impact" ordering (§16).

---

## 21. VERIFY vs ADVERSARIAL_VERIFY

Not collapsed. The tier Module 4 assigned decides which primitive is offered,
and the pipeline dispatches `_verify_one(..., adversarial=True)` only for the
adversarial action, which runs the multi-template path. An adversarial-tier
candidate is offered `ADVERSARIAL_VERIFY` and **not** `VERIFY`.

Re-verification with unchanged inputs is not offered (§7), which is what stops
the controller re-verifying the same candidate indefinitely.

---

## 22. Dynamic award-facet scheduling

Award facets are chosen adaptively, never swept. The controller runs mandatory
views first, then picks the highest-value unvisited facet by score, updates
yield/saturation and reconsiders. Missingness is an ordinary view action, not a
mandatory exhaustive loop.

Both directions tested: `test_awards_may_stop_before_every_optional_facet_runs`
(an uncovered facet does not block stopping) and
`test_awards_keep_exploring_while_facets_still_yield`.

---

## 23. SMALL_SET control

Mandatory structure first, then resolve disputed candidates, then stop early.
`test_small_set_stops_early_when_settled` and
`test_small_set_keeps_going_while_a_candidate_is_disputed`. No award-style facet
loop applies.

---

## 24. NULL_SINGLE control — DEFECT 7 (fixed)

`should_stop` contained:

```python
if not candidates and no_gain >= 1:
    return True, "null-single: no locality candidate produced"
```

with **no reference to the gate**. A query whose existence gate was undecided
stopped simply because nothing had been generated — exactly the conflation the
review forbids: *"Never interpret: zero generated candidate == confident
negative gate."*

Now guarded on `residual.components["gate_unresolved"]`. An uncertain gate keeps
the query open; a confident negative may stop; two competing localities may not.

---

## 25. NUMERIC control — DEFECT 8 (fixed)

`should_stop` derived numeric stability from the trusted-set Jaccard:

```python
instability = residual.components.get("set_instability", 1.0)
if instability <= 0.0 and unresolved <= 0.0:
    return True, "numeric: dominant cluster stable, dispersion low"
```

Two disagreeing clusters could therefore look settled merely because the
accepted set had stopped changing — while the reason string claimed the cluster
was stable. It now consumes Module 6's `cluster_competition` and
`numeric_dispersion` as spec table 6 requires, rather than re-deriving stability
from an unrelated signal.

Final cluster selection remains Module 8's; Module 7 only reads the diagnostics.

---

## 26. LARGE_OPEN_SET control

Yield decay, facet coverage, missingness saturation and the unresolved tail
drive continuation, consumed from Module 6's typed residual. No theoretical
cardinality is chased.

---

## 27. Relation-specific stopping matrix

| Program | Stops when | Cannot stop while |
|---|---|---|
| SMALL_SET | mandatory complete, set stable, nothing unresolved | a candidate awaits verification |
| NULL_SINGLE | gate resolved **and** ≤1 stable locality with no rival | gate undecided, or localities compete |
| NUMERIC | dominant cluster stable, low dispersion, no rival cluster | clusters compete or dispersion is high |
| LARGE_OPEN_SET | yield saturated and no unvisited facet | recent facet actions still yield |
| all | hard budget exhausted (absolute) | — |

Precedence, in order: **hard budget** → **mandatory incomplete** → **candidate
awaiting verification** → typed settled conditions → generic residual threshold.
Not one magic scalar.

---

## 28. Hard-budget precedence

Budget exhaustion returns `STOP` from `legal_actions` and `should_stop`
regardless of residual, with a distinct reason string. `q_res` is **not** rewritten
to zero — "no search value remains" and "no budget remains" stay different
claims, as audit 0009 §37 requires. Both are tested.

---

## 29. STOP action vs should_stop — DEFECT 9 (fixed)

The two could contradict each other: `should_stop` returned *continue*, yet the
`STOP` action could still win the argmax on its residual-threshold baseline, so
the query stopped anyway.

`should_stop` is now the single stopping authority. When it says continue, the
`STOP` action is removed from the candidate set before the argmax; when it says
stop, it overrides the argmax with its own reason. `STOP` remains in
`legal_actions` for traceability and scoring, but can never win against the
authority that owns the decision.

---

## 30. Gate model-role consistency — DEFECT 10 (fixed)

Confirmed exactly as audit 0007 §34.3 predicted: `_run_gate` used
`self.verifier_runtime`, which falls back to the enumerator when no verifier is
loaded. So the same frozen config had Qwen scoring the null gate interleaved and
Mistral scoring it in staged Phase A — the factual decision-maker changed with
execution mode.

Three changes:

1. `PipelineConfig.gate_model_role` (default `VERIFIER`) makes it an explicit
   architectural choice.
2. `_gate_runtime()` raises `GateRoleUnavailable` rather than substituting
   another model.
3. `_gate_deferred()` defers the gate from staged Phase A to Phase B, where the
   verifier role is resident — so the **same logical role** scores it in both
   modes, with no residency-based fallback and no simultaneous residency.

Tested with distinct scripted model ids: the interleaved gate result carries
`model_id == "offline/qwen"`; staged Phase A leaves `gate_result is None`
(deferred, not substituted); and requesting the runtime without a verifier
raises.

---

## 31. Staged active-loop architecture — DEFECT 11 (severe, fixed)

### 31.1 What the first pass found and fixed

Staged Phase B called only `_verify_pending`, a fixed `verification_targets`
list. The controller never ran after Phase A, so no decision made *after* seeing
verification results could execute. Staged execution computed a decision trace
after all model work was finished.

A controller loop was added to the verification phase: it reloads `RCSEState`
and the budget, the chosen action really spends a model call, and state is
folded back and re-persisted.

Two double-execution defects surfaced while doing this: cross-model recall ran
both as a controller action **and** unconditionally in the phase tail — in both
staged and interleaved modes — producing an identical record and a duplicate
edge. Guarded by `_cross_model_done`.

### 31.2 What the first pass left open — DEFECT 12 (blocking, fixed)

The first pass persisted `graph.pending_action` but **nothing consumed it**.
`scripts/run_staged.py` ran enumerate → verify → decide and finalized whatever
state it happened to reach. A controller-selected action could therefore be
recorded and then silently abandoned, which is not an executable loop: Algorithm
1's `while not ShouldStop and budget remains` was never actually a loop in
staged mode.

The second pass closes it:

- **`CoverPipeline._controlled_phase(graph, contract, allowed_roles, phase=…)`**
  replaces the verification-only loop. It *resumes* rather than restarts: any
  `pending_action` whose role is resident is executed **first**, as that exact
  instance, and cleared only once consumed. An action needing the other role
  becomes the new pending action.
- **`CoverPipeline.resume(graphs)`** is the enumerator-side half of the swap.
- **`CoverPipeline.pending_role(graph)`** reports which runtime a graph waits on,
  raising on a payload it cannot read.
- **`scripts/run_staged.py::phase_resolve`** is the production outer loop. It
  reloads whichever role the pending actions need — one at a time, so the two
  models are never co-resident — and repeats until none remain. The `all`
  subcommand runs it before `decide`, so the default production path drives the
  loop. `--relation` was added so a smoke can target one relation.
- `MAX_ROLE_SWAPS = 12` is a **bug detector, not a stopping rule**: the
  call/token budget bounds the loop, and exceeding the guard raises rather than
  returning a row that pretends the query settled.
- Unsupported roles, unexecutable actions and corrupt payloads raise
  `SystemExit`, `UnsupportedAction` and `CorruptPendingAction` respectively.
  None is silently swallowed.

Two further defects surfaced doing this:

- **DEFECT 13**: `legal_actions` keyed reverse legality on whether the *candidate*
  had received the mechanism. But a reverse probe asking about `gamma` may
  answer "Alpha", leaving gamma without it — so the identical probe was offered
  forever and re-ran to a duplicate edge. `RCSEState.executed_actions` now
  records executed action *identities*, and an instance runs at most once.
- The round counter is now the decision's position in the persisted log, so it
  is contiguous, unique and never restarts at a swap; a pended decision is
  logged exactly once, marked `pended_for_role`.

### 31.3 Finalization is gated

`decide_graph` raises `PendingActionNotConsumed` if a graph still has an
executable pending action and budget to run it. Module 8 can therefore never
receive a voluntarily abandoned action — deciding what to do with one is Module
7's responsibility, not Module 8's.

Finalization is allowed once the controller stops cleanly, or once the hard
budget is exhausted (with its own distinct stop reason).

---

## 32. Staged / interleaved semantic-equivalence test

`test_staged_and_interleaved_reach_the_same_semantic_result` runs a **non-empty**
award scenario in both modes with identical scripted runtimes, driving staged
execution through the full role-swap loop. Asserted equal:

- emitted objects;
- the candidate set;
- **evidence semantics** — per candidate, the same `(independence_group, mode)`
  pairs, so it is not merely the same names carrying different provenance;
- the trusted (accepted) set;
- the empty reason;
- the leading logical action sequence.

The physical model-load order deliberately differs — staged swaps residency,
interleaved keeps both resident — but the logical COVER action sequence agrees.
The old zero-candidate staging smoke was insufficient and is no longer the only
evidence.

---

## 33. Controller-state persistence

Persisted on the graph and carried across every role swap: `controller_log`
(every decision with its full breakdown and contiguous round numbers),
`rcse_state` (action history, trusted history, the three execution registers,
and **`executed_actions`** — the identities already run), `budget_snapshot`,
`gate_result`, and `pending_action`. Stage schema bumped **4 → 5**; an older
stage file fails loudly rather than losing controller state.

Each resume cycle writes its own `stage_r{cycle}_{role}.jsonl`, so every
intermediate state stays inspectable rather than being overwritten.

Verified by test: after a swap the round counter never resets, no completed
action is re-run, the budget accumulates onto one global allowance, and a
round trip preserves `pending_action`, `rcse_state`, `budget_snapshot` and
`executed_actions` exactly.

---

## 34. Call/token budget accounting

One `budget.charge(calls=1)` per acquisition action; verification charges
`max(1, spent)` where `spent` is the runtime's actual call count, so a
three-template adversarial verification is not counted as one. Description-first
views make two generation calls inside one mechanism and their tokens are summed
from both records. Calibration controls are cached, so a reused control makes no
runtime call and is not charged again — the accounting follows what actually
happened.

`legal_actions` returns only `STOP` once the budget is exhausted, so no action
starts without budget, and the budget never goes negative.

---

## 35. Action-trace schema

Each decision records: step, chosen action (type, view, facet, candidate,
reason, estimated cost, **model role**), score, the full residual with its
components/diagnostics/reasons, every considered action with its five-component
breakdown, the state before, and the state after. For `STOP`, the explicit stop
reason. No free-form rationale anywhere.

---

## 36. Coefficient / threshold inventory

| constant | default | consumer | judgement call? | train calibration? |
|---|---|---|---|---|
| `alpha_yield` | 1.0 | `A_t` | yes | yes |
| `beta_gap` | 1.0 | `A_t` | yes | yes |
| `gamma_uncertainty` | 0.8 | `A_t` | yes | yes |
| `lambda_cost` | 0.15 | `A_t` | yes | yes |
| `rho_redundancy` | 1.0 | `A_t` | yes | yes |
| `untried_yield_prior` | 0.5 | `Ŷ_t` | yes | yes |
| `mandatory_gap_relevance` | 1.0 | `G_t` | yes | yes |
| `optional_gap_scale` | 0.8 | `G_t` | yes | yes |
| `covered_mechanism_redundancy` | 0.5 | `D_t` | yes | yes |
| `resample_redundancy` | 0.8 | `D_t` | yes | yes |
| `repeat_redundancy_step` | 0.1 | `D_t` | yes | yes |
| `indirect_uncertainty` | 0.5 | `U_t` | yes | yes |
| `adversarial_uncertainty_bonus` | 0.2 | `U_t` | yes | yes |
| `reverify_redundancy` | 0.5 | `D_t` | yes | yes |
| `verify_first_bonus` | 0.5 | policy step 3 | yes | yes |
| `cost_run_view` / `_reverse_check` / `_resample` / `_verify` | 1.0 | `C(a)` | yes | yes |
| `cost_adversarial_verify` | 2.0 | `C(a)` | yes | yes |
| `cost_cross_model` | 1.5 | `C(a)` | yes | yes |
| `residual_stop` | 0.25 (contract wins) | stopping | yes | yes |
| `saturation_patience` | 2 (contract wins) | stopping | yes | yes |
| `stability_threshold` | 1.0 (contract wins) | stopping | yes | yes |
| `verify_first_unresolved` | 0.5 | policy step 3 | yes | yes |

Every one is a versioned `ControllerConfig` field. None was tuned during this
review; none was tuned on val.

---

## 37. Module-5 / Module-6 revalidation

Both conformance suites pass unchanged after the controller changes:
`tests/test_evidence_state_conformance.py` 72/72,
`tests/test_rcse_conformance.py` 86/86 — including the former xfail, now an
ordinary pass. No accepted availability assumption remains impossible: the three
irreducible mechanism gaps (1/6, 1/4, 1/5) are closed by legal controller
execution, not by removing `REVERSE_ALTERNATE` from the denominator.

---

## 38. Mismatches found

| # | Severity | Description |
|---|---|---|
| 1 | **blocking** | `REVERSE_CHECK` unschedulable; three relations carried a permanent mechanism gap (§8) |
| 2 | **severe** | Staged execution was not an active loop — Phase B ran a fixed list and no post-verification decision could execute (§31) |
| 3 | **severe** | `_record_id` omitted the conditioning candidate, making candidate-conditioned acquisition unschedulable in principle (§8.1) |
| 4 | **severe** | Cross-model recall executed twice (controller action **and** unconditional phase tail), producing a duplicate edge in both modes (§31) |
| 5 | severe | `should_stop` stopped a NULL_SINGLE query with an **unresolved gate** merely because nothing had been generated (§24) |
| 6 | severe | NUMERIC stopping used trusted-set Jaccard instead of Module 6's cluster diagnostics, so competing clusters could read as settled (§25) |
| 7 | moderate | `should_stop` could abandon a candidate awaiting verification, contradicting spec §13.2 step 3 (§20) |
| 8 | moderate | The `STOP` action could win the argmax while `should_stop` said continue (§29) |
| 9 | moderate | Gate model identity changed with execution mode (§30) |
| 10 | moderate | `RESAMPLE` was dead; hidden `+0.5` bonuses; redundancy branch was unreachable; tie-break ignored action arguments (§§10, 11, 18, 19) |
| 11 | **blocking** | *(second pass)* `pending_action` was persisted but **nothing consumed it** — the production CLI finalized without ever dispatching a controller-selected action, so Algorithm 1's outer loop did not exist in staged mode (§31.2) |
| 12 | severe | *(second pass)* Role filtering at *choice* time made `pending_action` unreachable, quietly degrading staged execution to "whatever this phase can manage" (§7) |
| 13 | severe | *(second pass)* Reverse legality keyed on the candidate's evidence, not on the executed action, so a probe whose answer named a different candidate was re-offered forever and re-ran to a duplicate edge (§31.2) |

**One defect I introduced myself**, in the Module-4 review and caught here:
`ContextualCalibrator.gate_control_logits` referenced `GATE_LABELS` before a
local import of the same name, making it function-local and unbound —
`UnboundLocalError` on every calibrated-gate control measurement. No existing
test exercised that path. Fixed by hoisting the import above first use.

---

## 39. Fixes made

All in `controller.py` and `pipeline.py` unless noted.

1. `REVERSE_CHECK` action, per-candidate legality, `_run_reverse_check`.
2. `_record_id` includes the conditioning candidate (`elicitation/engine.py`).
3. `_controlled_verification`: a real Phase B controller loop with role
   restriction and `pending_action` hand-off.
4. `_cross_model_done` guard on both phase tails.
5. NULL_SINGLE stop guarded on `gate_unresolved`.
6. NUMERIC stop consumes `cluster_competition` / `numeric_dispersion`.
7. `pending_verification` check before the generic residual stop.
8. `should_stop` made the single stopping authority.
9. `gate_model_role`, `_gate_runtime`, `_gate_deferred`, `GateRoleUnavailable`.
10. `RESAMPLE` legality on stochastic views; `view_gap_relevance`,
    `mechanism_redundancy`, `mechanism_yield_prior`, `candidate_impact`;
    `Action.identity` tie-break; all constants moved to config.
11. `gate_control_logits` import hoisted (`verification.py`).
12. `ModelRole` unified on the canonical `types.py` enum, gaining `NONE`.

**Second pass:**

13. `_controlled_phase` (role-parameterised, resumes rather than restarts),
    `_execute_action`, `_take_pending`, `resume`, `pending_role`.
14. `scripts/run_staged.py::phase_resolve` — the production role-swap loop —
    wired into the `all` path, plus `--relation` and per-cycle stage files.
15. `RCSEState.executed_actions`, persisted, enforcing one execution per action
    instance.
16. `decide_graph` raises `PendingActionNotConsumed` while executable work
    remains.
17. `UnsupportedAction` / `CorruptPendingAction` / `SystemExit` on unsupported
    roles — nothing fails silently; `MAX_ROLE_SWAPS` as a loud bug detector.
18. Round numbers taken from the persisted log position; a pended decision is
    logged once, marked `pended_for_role`.
19. The `scripted` backend accepts `responses` / `default_response`, so a CLI
    smoke can produce candidates instead of abstaining on every call.

Module 8's selection and output policy were not touched.

---

## 40. Before/after controller scenarios

**Multi-role-swap staged smoke**, run through the **production CLI**
(`configs/experiments/smoke_staged_roleswap.yaml`; scripted, plumbing only,
never an accuracy result):

```
$ python3 scripts/run_staged.py all --config .../smoke_staged_roleswap.yaml \
      --relation awardWonBy --limit 1

[PHASE A]  enumerate
[PHASE B]  verify
[RESUME 1] role=enumerator  queries_waiting=1
[PHASE C]  decide

  round 0: RUN_VIEW            enumerator award_missing
  round 1: RUN_VIEW            enumerator award_facet_temporal
  round 2: RUN_VIEW            enumerator award_direct
  round 3: REVERSE_CHECK       enumerator award_reverse_check
  round 4: ADVERSARIAL_VERIFY  verifier   gamma
  round 5: RUN_VIEW            enumerator award_facet_recipient_type   <-- pended for enumerator role
  round 6: RUN_FACET           enumerator award_exact_identity_contrast
  round 7: STOP                none       -

pending_action: (none)   budget: 8 calls, 7 generated tokens
```

Role sequence **ENUMERATOR → VERIFIER → ENUMERATOR → STOP**. Round 5 is the
decisive one: the verification phase chose an enumerator-role action, the
orchestrator reloaded that role, and the exact chosen instance executed. Round
numbers are contiguous across the swap, the budget accumulates onto one global
allowance, and `pending_action` is empty at finalization.

Before the second pass this run ended after round 4 with a persisted pending
action that nothing executed, and Phase C finalized anyway.

**Gate identity**, same frozen config:

| | before | after |
|---|---|---|
| interleaved | Qwen | Qwen |
| staged Phase A | **Mistral** | deferred (no substitute) |
| staged Phase B | — | Qwen |

---

## 41. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/controller.py` | modified — action space, five score components, stopping authority |
| `src/cover_kbc/pipeline.py` | modified — action execution, Phase B loop, gate role |
| `src/cover_kbc/elicitation/engine.py` | modified — record identity includes the candidate |
| `src/cover_kbc/verification.py` | modified — hoisted `GATE_LABELS` import |
| `src/cover_kbc/evidence/graph.py` | modified — `pending_action` field |
| `src/cover_kbc/staging.py` | modified — persist `pending_action`, schema v5 |
| `src/cover_kbc/types.py` | modified — `ModelRole.NONE` |
| `tests/test_controller_conformance.py` | **created** — 79 tests |
| `scripts/run_staged.py` | modified — `phase_resolve` role-swap loop, `--relation`, per-cycle stage files |
| `src/cover_kbc/models/registry.py` | modified — scriptable `responses` / `default_response` |
| `src/cover_kbc/coverage.py` | modified — `RCSEState.executed_actions` |
| `configs/experiments/smoke_staged_roleswap.yaml` | **created** — multi-role-swap smoke |
| `tests/test_rcse_conformance.py` | modified — xfail removed, now an ordinary pass |
| `tests/test_controller.py` | modified — configured redundancy assertion |
| `docs/audits/0010-module-7-active-controller-conformance.md` | **created** |

`benchmark/` untouched.

---

## 42. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_controller_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_roleswap.yaml --relation awardWonBy --limit 1
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
git diff --stat
```

No model download, no heavyweight inference.

---

## 43. Exact tests / results

**760 passed, 0 failed, 0 xfailed** (up from 680 passed + 1 xfailed).

| File | Tests |
|---|---|
| `tests/test_contracts.py` | 44 |
| `tests/test_controller.py` | 32 |
| `tests/test_controller_conformance.py` | **79** |
| `tests/test_data.py` | 26 |
| `tests/test_elicitation.py` | 63 |
| `tests/test_evaluation.py` | 13 |
| `tests/test_evidence.py` | 23 |
| `tests/test_evidence_state_conformance.py` | 72 |
| `tests/test_graph.py` | 59 |
| `tests/test_normalization.py` | 59 |
| `tests/test_pipeline.py` | 31 |
| `tests/test_programs.py` | 40 |
| `tests/test_rcse_conformance.py` | 86 |
| `tests/test_staging.py` | 17 |
| `tests/test_verification.py` | 40 |
| `tests/test_verifier_conformance.py` | 76 |

`pyflakes`: clean apart from four intentional `import _bootstrap` sys.path shims.

---

## 44. Benchmark integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

---

## 45. Challenge-compliance impact

| constraint | status |
|---|---|
| No learned controller / RL | ✓ `test_no_learned_controller_exists` AST-walks for `sklearn`/`torch`/`gym`/`stable_baselines3` imports, `fit`/`train`/`backward`/`learn`/`update_policy` calls, and the words `policy_gradient`/`q_learning`/`replay_buffer`/`reward` |
| Deterministic | ✓ repeated construction yields identical choices and scores |
| No retrieval / factual lookup | ✓ same AST check |
| No DoLa added | ✓ `test_no_dola_action_exists` |
| Parameter budget | unchanged — Module 7 is non-neural |
| Model roles first-class | ✓ strengthened: every action declares its required role |

---

## 46. Constants requiring later train calibration

All 23 in §36 marked "yes". They are architecture defaults, not measurements.
The per-action cost priors in particular should be recalibrated against logged
call/token costs once Colab runs exist, since they currently encode an assumed
rather than measured cost ratio.

---

## 47. Unresolved Module-7-only issues

1. ~~`pending_action` has no orchestrator consumer.~~ **Resolved in the second
   pass** (§31.2): `phase_resolve` is the production role-swap loop, wired into
   the default `all` path and covered by tests. Recorded here because the first
   pass found the gap and left it open, which is the fact this audit should
   preserve.
2. **Deferring the gate costs discovery calls in staged mode.** Because the gate
   moves to Phase B, staged Phase A runs discovery it might have skipped on a
   confident negative. That is the honest price of keeping the decision-maker
   identical across modes; the alternative was co-residency, which staged
   execution exists to avoid.
3. **`untried_yield_prior = 0.5` is a judgement call** that sets how eagerly the
   controller explores an unknown mechanism.

---

## 48. Future Module-8 notes

Carried forward unchanged; nothing new found:

- `EmptyReason.CANDIDATE_REJECTED` is unreachable (audit 0007 §34.2).
- `selection.py` sorts and weights numeric clusters on the **raw** support count
  rather than acquisition support (audit 0008 §38.4).

Nothing about `pending_action` is deferred to Module 8: Module 7 now finishes
its controller execution before finalization, and `decide_graph` refuses to
produce a row while executable work and budget remain (§31.3). Module 8 receives
a settled graph or an explicitly budget-exhausted one.

---

## 49. Module 8 remains unreviewed

Module 8 (Final Selector and Evaluator-Aware Output) has **not** been reviewed
against the proposal. Its code exists and its tests pass, but no conformance
judgement has been made about it. The notes in §48 are observations made while
reviewing Module 7 and are not exhaustive.

---

## 50. Recommended next review

**Module 8 — Final Selector and Evaluator-Aware Output.**

It is the last unreviewed module, it owns the two findings carried since audit
0007, and it converts everything the previous seven modules produce into the
rows the official evaluator scores.

---

## Verdict

**Module 7 PASSES** after ten defects were found and fixed, plus one I had
introduced myself in the Module-4 review.

The controller is a genuine stateful loop in **both** execution modes, and in
staged mode that loop now survives model residency changes: the production CLI
reloads whichever role a pending action needs and executes that exact instance,
repeating until the controller stops or the budget runs out. A selected action
can no longer be persisted and abandoned, and finalization is refused while
executable work remains. Every mechanism the architecture counts as available is reachable —
`REVERSE_ALTERNATE` is candidate-conditionally schedulable and audit 0009 §41's
strict xfail is an ordinary passing test. The action score is the proposal's five
terms with every constant in versioned config and no hidden bonuses; redundancy
keys on independence groups; tie-breaking uses full action identity. Stopping has
one authority with explicit precedence, verification precedes stopping, an
unresolved gate cannot be mistaken for a confident negative, and competing
numeric clusters can no longer read as settled. The gate's model role is an
explicit configuration choice that no longer changes with execution mode.
