# Audit 0032 — Layer 6: Control Integration and Conformance

Status: **PASS** (amended in place — see §15A and §15B)
Date: 2026-08-06
Amended: 2026-08-06 (§15A) correcting the stock protected-reserve routing, and
again (§15B) correcting the §17A cross-family legality gate to require Module
15's *local* trigger and not only its static eligibility.
Milestone: **layer-boundary integration**, not a numbered module.
Mode: **shadow**, **disabled by default**, **uncalibrated**, **zero neural calls**.

---

## 1. Objective and scope

Close the seam Audit 0031 left open: connect the modules that own legality to
Module 20's affordability and Module 21's valuation, so the planner ranks a real
owner-derived action set instead of an empty list.

In scope: a canonical owner-authoritative action catalogue, adapters for every
action-producing module, deterministic identity and deduplication, the
affordability seam, the ranking seam, and a shadow pipeline seam.

Out of scope and not implemented: any calibration, any action execution, any
production activation, DoLa, and any new numbered module.

**Nothing is executed.** Module 7 remains the production controller and Module 8
the finaliser.

---

## 2. Proposal sections read

| Section | What it fixed here |
| --- | --- |
| **§3.2** | The extended state and the action set — *"specialist probes, pseudo-memory probes, candidate-free recall, blind verification, counterfactual verification, reverse checks, cross-model checks, optional contrastive decoding, resampling, and STOP"* — which fixes the families the catalogue must reach. |
| **§16** | Module 20 is the affordability authority; Layer 6 asks it and recomputes nothing. |
| **§17** | `a* = arg max U_t(a)` over the actions supplied to it, with STOP as the fallback. Layer 6 supplies the legal set and changes no planner semantics. |
| **§17.1** | The four policy examples, now reproduced from owner-derived actions (§35–§38). |
| **Appendix C** | M21's input is *full state + legal actions*, which is exactly what this seam constructs. |

Prior audits read: **0010** (M7), **0012** (M0–M8), **0016** (M9), **0022**
(M15 §17A), **0024** (M14 NULL), **0025** (M17), **0026** (M18), **0027**
(Layer 4 §20A), **0028** (M19), **0029** (Layer 5), **0030** (M20), **0031**
(M21).

**No material conflict between this brief and the proposal was found.**

---

## 3. Why this is integration, not Module 22

No new architecture is introduced. Every judgement made here already belongs to
an existing module: legality to the owners, resource identity and affordability
to Module 20, value and STOP to Module 21. The two new files are adapters and a
call-ordering seam.

No `M22`, `MetaController`, `OrchestratorAgent` or `PlannerAgent` exists, in
name or in file (`test_this_is_integration_not_a_new_module`).

---

## 4. Architecture position

```
Layer 6 - Test-Time Control: M7 Active Controller (production)
                             M20 Relation Budget Scheduler (shadow)
                             M21 Expected-Value Micro-Planner (shadow)
                             Layer-6 integration (shadow)  <- this milestone
```

---

## 5. Files changed

New:

* `src/cover_kbc/control/action_catalog.py` — canonical action type, owner
  adapters, deterministic canonicalisation
* `src/cover_kbc/control/layer6_integration.py` — `Layer6ControlState`,
  `collect_catalog`, `Layer6Integrator`
* `tests/test_layer6_integration.py` — 85 tests (66 + 10 from §15A + 9 from §15B)
* this audit

Modified: `src/cover_kbc/control/__init__.py`, `src/cover_kbc/pipeline.py`
(Phase-C seam), `scripts/run_staged.py`, `scripts/run_cover.py`, three
experiment configs (disabled).

Unmodified and verified: `controller.py`, `types.py`, `selection.py`,
`coverage.py`, `benchmark/`.

---

## 6. Audit 0031's empty-live-action gap

Audit 0031 recorded that Module 21's live legal-action list was empty because
*"no module yet exposes an owner-declared legal-action surface"*.

**Inspecting the source for this milestone shows that claim was too strong.**
Three owners already published exactly such a surface:

| Surface | What it already guarantees |
| --- | --- |
| `controller.legal_actions` | Module 7's own legality engine — refuses a view that ran, a candidate already carrying the reverse mechanism, a resample before structural mechanisms are exhausted |
| `specialist_verifier.verifiable_targets` | Module 17's eligibility catalogue, with `eligible` and an ineligibility reason |
| `bidirectional_verifier.eligible_checks` | Module 18's, likewise, including Module 15's pending checks as provenance |

What was genuinely missing was a **unified adapter**. That is what this
milestone adds; no owner-side legality was moved into Layer 6.

The gap is closed: the live seam now produces non-empty catalogues for all six
relations (§34).

---

## 7. Owner-authoritative legality

Layer 6 declares no legality. Every action carries a `legal_provenance` naming
the owner surface it came from and an `eligibility_evidence` string, and
`ControlActionCandidate` **raises** without provenance — an action with no
declared owner cannot be constructed.

Legality is never derived from value or context:

* the module contains no `risk_profile.`, `temporal_sensitivity`,
  `open_set_risk` or `search_breadth`, so a Module 9 grade cannot create an
  action (§24);
* the residual is not consulted, so Module 19 cannot create one (§23);
* Module 20 is asked *after* the catalogue is built, so budget cannot make an
  illegal action legal (§25).

---

## 8. Canonical action type

`ControlActionCandidate` carries query identity, canonical action id, owner,
family, target, facet, model role, repeatability, the Module 20 descriptor, and
both provenance fields.

It carries **no value**: no utility, no expected gain, no factual status, no
accepted/rejected. Module 21 supplies value; nothing here anticipates it.

---

## 9. Action identity

`action_id` is `owner:family:target:facet` — semantic and stable, never random
and never order-dependent.

`semantic_key` is `(family, target, facet, model_role)` and deliberately
**excludes the owner**, so the same work reached through two owners collides and
the precedence rule can resolve it (§19). Identity for deduplication and
tie-breaking is `(family, action_id, target, facet)`, so `reverse(A)` and
`reverse(B)` stay distinct.

---

## 10. Action ownership table

| Family | Owner(s) | Legality source |
| --- | --- | --- |
| `SPECIALIST_PROBE` | M12 / M13 / M14 / M15, M7 | live facet registry + execution record; M7's `legal_actions` |
| `PSEUDO_MEMORY_PROBE` | M11 | declared probe families + retrieval record |
| `CANDIDATE_FREE_RECALL` | M18 | `eligible_checks` |
| `BLIND_VERIFY` | M7 | `legal_actions` |
| `SPECIALIST_VERIFY` | M17 | `verifiable_targets` |
| `COUNTERFACTUAL_VERIFY` | M18, M7 | `eligible_checks` (COUNTERFACTUAL, KEY_CONDITION); `legal_actions` |
| `REVERSE_CHECK` | M18, M7 | `eligible_checks`; `legal_actions` |
| `CROSS_MODEL_CHECK` | M7 | `legal_actions` |
| `RESAMPLE` | M7 | `legal_actions` |

Every family has at least one owner, asserted by test (§33).

---

## 11. Module 11 adapter

Projects the three declared probe families, excluding any Module 11 already
recorded as run. One-shot semantics are respected: a spent probe is **not**
reoffered because Module 19 still shows a gap
(`test_module_11_probes_are_one_shot`).

---

## 12. Module 12 adapter

Numeric probe families from Module 12's live registry, excluding any the
specialist already ran. `hasCapacity` declares five families and `hasArea`
four; the family `hasArea` does not declare is `NOT_DECLARED`, not unexplored,
and never becomes an action.

Cluster identity is Module 12's throughout — no reclustering, no evaluator
tolerance, no winning value is chosen (Audit 0031 §39 preserved).

---

## 13. Module 13 adapter

Award facet slices from Module 13's live registry. Another facet is legal only
if the registry declares it, it is enabled, and Module 13 has not already run
it. The `geography` facet is `DISABLED_BY_POLICY` and is excluded with that
reason, never offered.

The missingness facet carries the `MISSINGNESS` protected reserve where Table 6
declares one, so its cost draws on the right envelope.

---

## 14. Module 14 adapter

Stage-A and Stage-B families from Module 14's registry, respecting the audited
freshness branch: an action is legal only where Module 14's own state declares
it eligible.

**Audit 0024 preserved.** Nothing in the projection converts failed recall into
substantive NULL, and no catalogue payload contains `substantive_null`,
`final_empty` or `is_empty`
(`test_the_death_freshness_branch_follows_module_14`).

---

## 15. Module 15 adapter

Gate, acquisition, missingness and cross-family templates from Module 15's
registry. Three owner rules are read, never re-derived:

* a **disabled** template is excluded with the registry's own rationale — the
  border direct probe under Audit 0022 §11.1's minimal-change rule;
* an **already-run** template is excluded against Module 15's execution record;
* a template with recorded **exhaustion evidence** is not reopened.

Stock's Stage-2 structure follows Module 15 rather than Layer 6: the listing
gate templates are themselves owner-declared facets, and the projected set is
always a subset of what the registry declares
(`test_the_stock_gate_governs_stage_two`). The §17A cross-family template is
legal exactly when **both** of Module 15's recorded verdicts permit it — static
eligibility *and* the local per-query trigger (§15B) — and draws on the
**FRESHNESS** reserve (§15A).

A specialist that has not run yet is a real state, not missing information: it
has executed nothing, so every enabled facet its registry declares is legal.
`UNEXPLORED` is deliberately **not** the test (§23).

---

## 15A. Correction — stock protected-reserve routing (amendment)

**Inconsistency found during review.** This audit originally stated that the
§17A cross-family template *"carries the `PARENT_SUBSIDIARY` reserve"*. That
contradicts Audit 0022 §17A, which makes the M15 cross-family branch the stock
**temporal/freshness** rescue, not the parent/subsidiary check.

**Classification: executable reserve-routing defect, not a documentation typo.**

Verified by inspection rather than assumed. Both halves of the mapping were
wrong, and the second was the more serious:

```python
# before, in _facet_purpose
if facet.family == "cross_family" and _P.PARENT_SUBSIDIARY in declared:
    return _P.PARENT_SUBSIDIARY

# before, in _M18_PURPOSE - no parent/subsidiary entry at all
_M18_PURPOSE = {"REVERSE": ..., "CANDIDATE_FREE_RECALL": ...}
```

Observed before-state for stock: `stock_cross_family` carried
`PARENT_SUBSIDIARY`, and **the PARENT_SUBSIDIARY reserve had no consumer at
all** — Module 18's parent/subsidiary counterfactual received no purpose. So
the reserve protected budget that only the wrong action could spend, and the
action it exists for could not reach it.

### Authoritative stock flow

Audit 0022 §17A:

```
M15 listing gate / listing facets
    -> if listing status uncertain:
       M14 temporal/freshness-style cross-family subroutine   (acquisition)
    -> later M18 parent/subsidiary counterfactual             (verification)
```

Two structurally different operations that must not share a reserve tag merely
because both concern stock:

| | Cross-family freshness recall | Parent/subsidiary counterfactual |
| --- | --- | --- |
| Objective | independent, fresher parametric recall under temporal uncertainty | distinguish the company itself from parent/subsidiary confusion |
| Owner | **M15**, using the shared M14 cross-family primitive | **M18** |
| Output | acquisition / recall evidence | targeted structural verification evidence |
| Base spend class | `DISCOVERY` | `VERIFICATION` |
| Special reserve | **`FRESHNESS`** | **`PARENT_SUBSIDIARY`** |

| | Mapping |
| --- | --- |
| **before** | M15 cross-family → `PARENT_SUBSIDIARY`; M18 parent/subsidiary → *(none)* |
| **after** | M15 cross-family → `FRESHNESS`; M18 parent/subsidiary → `PARENT_SUBSIDIARY` |

### How M18's parent/subsidiary check is identified

By the **contract-declared counterfactual class**, never by prompt text. The
stock contract declares `adversarial_classes = ("parent_listing",
"subsidiary_listing", "historical_listing")`, and only the first two are the
parent/subsidiary distinction. `historical_listing` is deliberately excluded:
it is the *temporal* near-miss class, and tagging it would let a freshness
question spend the budget protected for distinguishing a company from its
parent. A generic company-itself or key-condition check receives no
parent/subsidiary reserve either.

### Legality was corrected too

The cross-family facet was previously offered whenever the registry enabled it
and it had not run. That is not §17A. This pass began routing it through Module
15's recorded `plan.cross_family_eligible` — which was an improvement but still
**incomplete**, because that field is only half the condition. §15B completes
it.

### Protected-reserve isolation

The central regression
(`test_the_two_stock_reserves_are_isolated_from_each_other`): one fictional
stock state carrying **both** legal actions, against a synthetic calibration
with separately protected `FRESHNESS` and `PARENT_SUBSIDIARY` pools.

| Resource state | Freshness recall | Parent/subsidiary check | Unrelated discovery |
| --- | --- | --- | --- |
| both reserves funded | affordable | affordable | — |
| only `FRESHNESS` remains | **affordable** | **denied** | denied |
| only `PARENT_SUBSIDIARY` remains | **denied** | **affordable** | denied |

Neither action can reach the other's pool, and unrelated stock discovery can
reach neither. Nothing executed in any case.

### Scope of the amendment

`_facet_purpose`, `_M18_COUNTERFACTUAL_PURPOSE` and the cross-family legality
gate in `action_catalog.py`, plus ten new tests. **Module 20's Table-6 registry
is unchanged** — Stock remains `discovery MEDIUM`, `verification MEDIUM`,
`special FRESHNESS, PARENT_SUBSIDIARY`, and `relation_budget.py` and
`budget_types.py` are both clean in git
(`test_module_20s_table_6_registry_is_unchanged`). This pass assigns the correct
live action to the correct already-declared reserve; it declares no new reserve
and changes no policy.

Audit 0026's M18 semantics are untouched: blindness, independence, call
accounting, candidate-free semantics and `X` semantics are unchanged, and the
parent/subsidiary check still accepts, rejects and finalises nothing. Only its
Layer-6 resource descriptor was corrected.

Canonical action identity is unchanged — the reserve is resource metadata and is
not part of `identity` — and Module 21's value is unchanged when both actions
remain affordable, because reserve bookkeeping is not an input to §17's equation.

---

## 15B. Correction — the §17A cross-family legality gate (second amendment)

**Inconsistency found during review.** §15A described `plan.cross_family_eligible`
as though it already required configuration `enabled` **and** a distinct second
model family **and** the relation-local listing uncertainty. It does not. Audit
0022 §17A deliberately keeps two questions apart, and Module 15's own source
says so in as many words:

| Field | Question it answers | Module 15's own comment |
| --- | --- | --- |
| `plan.cross_family_eligible` (+ `cross_family_rationale`) | may this **build** use the cross-family mechanism at all? | *"Static architectural eligibility - 'may this run at all?'"* |
| `result.cross_family_trigger` | does **this query** actually need the temporal rescue? | *"The runtime condition eligibility is not sufficient for."* |

**Classification: executable legality defect, not audit wording.**

The gate written in §15A read only the static field:

```python
def _cross_family_eligible(result) -> bool:
    plan = getattr(result, "plan", None)
    return bool(getattr(plan, "cross_family_eligible", False))   # static only
```

So a statically eligible build whose query was `LOCALLY_CLEAR` would have been
offered a freshness action — the exact broadening Audit 0022 §17A exists to
prevent. The docstring claimed all three conditions; the code checked one.

### The local trigger, using Module 15's live enum

`CrossFamilyTrigger`, with its own `fires` property as the authority:

| Trigger | `fires` | Layer-6 result |
| --- | --- | --- |
| `NOT_ELIGIBLE` | no | not legal — static eligibility failed |
| `NOT_EVALUATED` | no | not legal — eligible, but local state never observed; proves no local need |
| `LOCALLY_CLEAR` | no | **not legal** — this query's listing state is not uncertain |
| `UNRESOLVED_LISTING_GATE` | **yes** | local condition met |
| `TEMPORAL_STATUS_UNCLEAR` | **yes** | local condition met |
| `TEMPORAL_STATUS_CONFLICT` | **yes** | local condition met |

### Corrected legality rule

`_cross_family_legality` now requires, in order and all from the owner:

1. `plan.cross_family_eligible` — static architecture eligibility;
2. `result.cross_family_trigger.fires` — the owner-recorded local condition;
3. `not result.cross_family_executed` — the branch is one-shot;

plus the ordinary registry and execution-history rules. Nothing re-reads the
listing gate, the candidates, prompt text or Module 9's temporal grade to
reconstruct the condition. Each refusal carries the owner's own reason, and a
spent branch is excluded as `ALREADY_EXECUTED` rather than as ineligible.

### Valid post-Module-15 state matrix

Module 15 runs the branch under `if trigger.fires and runtime is not None`, so:

| Module 15 state | Reachable | Layer-6 catalogue |
| --- | --- | --- |
| static ineligible | yes | no action (owner's rationale) |
| eligible, `NOT_EVALUATED` | yes | no action |
| eligible, `LOCALLY_CLEAR` | yes | **no action** |
| trigger fires, executed | yes — the normal post-run state | no action (`ALREADY_EXECUTED`) |
| trigger fires, not executed | yes — plan-only, `runtime is None` | **action legal** |

The last row is a *reachable* Module 15 state, not a fabricated one, which is
why the fixtures construct it through Module 15's real
`SmallSetSpecialistPlan` / `SmallSetSpecialistResult` rather than a duck-typed
stub. No impossible state was invented to make the catalogue non-empty.

**In a normal post-Module-15 pipeline state there is therefore no remaining
cross-family action**, and that is correct. A complete action taxonomy does not
require every family to be live in every post-execution state — the mechanism
stays representable and classifiable (`DISCOVERY` + `FRESHNESS`) even when no
instance of it is currently legal.

### Module 9 cannot substitute for the local trigger

Module 9 grades stock's `temporal_sensitivity` as **HIGH**, and that is still
not permission: with the same high grade and a `LOCALLY_CLEAR` or
`NOT_EVALUATED` trigger, no action is offered
(`test_module_9_temporal_risk_cannot_open_the_branch`).

### Stock catalogue count, corrected honestly

§34's stock row was measured before this gate existed and is now stale. The
live seam yields **6 legal / 6 affordable / 0 denied / 6 excluded** for stock,
not 7/7/0/5: the seventh entry was a cross-family action that §17A does not
permit. The §34 table is corrected rather than preserved for snapshot
compatibility.

### Reserve routing unchanged

§15A's mapping is frozen and re-asserted: M15 cross-family →
`DISCOVERY` + `FRESHNESS`; M18 parent/subsidiary → `VERIFICATION` +
`PARENT_SUBSIDIARY`; `historical_listing` and generic stock checks receive
neither. Module 20's Table-6 registry is untouched.

---

## 16. Module 17 adapter

Only `eligible` targets become actions. A hard-contract violation cannot be
rescued by a verifier and a target with no printable value cannot be shown to
one — Module 17 already says so, and the adapter records its reason rather than
re-deciding (`test_a_hard_contract_invalid_target_never_becomes_an_action`).

No scheduling happens here: which eligible target is worth a call stays Module
20/21's question, so not every award candidate is verified.

---

## 17. Module 18 adapter

Reuses the eligible-check catalogue. The four mechanisms map onto canonical
families — `REVERSE` → `REVERSE_CHECK`, `COUNTERFACTUAL` and `KEY_CONDITION` →
`COUNTERFACTUAL_VERIFY`, `CANDIDATE_FREE_RECALL` → its own family. An ineligible
reverse relation never enters, and a completed non-repeatable check does not
reappear (`test_a_completed_non_repeatable_check_does_not_reappear`).

Candidate-free blindness is preserved: it is projected as its own family and
nothing attaches a candidate to it. Its protected reserve is `CANDIDATE_FREE`
where Table 6 declares one.

---

## 18. Core Module 7 adapter

Module 7's legality engine is **reused, not reimplemented**: the module contains
no `budget.exhausted`, `CandidateStatus`, `all_views()` or `is_reverse`, and
names `controller.legal_actions` as the owner surface
(`test_the_core_legality_engine_is_reused_not_reimplemented`).

`STOP` is dropped with a recorded reason — §17 makes it the planner's fallback,
never a ranked candidate. `RESAMPLE` is the only family marked repeatable.

The core action is not deleted; it remains the owner for generic operations no
upgraded module supersedes.

---

## 19. Ownership conflict resolution

Explicit precedence by specificity: `M17`/`M18` (3) > specialists (2) > `M11`
(1) > core `M7` (0).

When two owners express the same semantic action — core generic verify versus
Module 17's typed specialist verify, core reverse versus Module 18's reverse,
core `RUN_FACET` versus a specialist facet — the **more specific owner wins**,
because it carries a contract the generic form does not.

**Suppression is deduplication, not denial.** The loser is recorded as
`SAME_SEMANTIC_ACTION` with the winner named, and the outcome is identical in
either input order
(`test_the_more_specific_owner_wins_and_the_duplicate_is_recorded`).

---

## 20. Deduplication

An identical duplicate projection collapses to one entry with no exclusion
recorded. A **conflicting** duplicate — one action id with a different owner,
target, role, repeatability or cost — **raises**, because silently picking the
first would make the catalogue depend on adapter order
(`test_a_conflicting_duplicate_fails_loudly`).

Equal specificity with the same semantics resolves on action id, never on order.

---

## 21. Repeatability

Only `RESAMPLE` is repeatable, and only because Module 7's contract permits it.
Everything else is one-shot and is removed once its owner records execution. A
resample keeps its structural independence group — another sample of one
mechanism is not a new mechanism.

---

## 22. Execution-history handling

Read from typed owner records: the specialist's own execution map, Module 11's
retrieval records, and the executed-identity list on the planner state. No
history is inferred from artefact filenames.

State dependence is proven both ways: the award specialist's executed facets are
absent from the catalogue, and the same facets become legal when no execution
record exists (`test_an_executed_one_shot_facet_is_not_offered_again`).

---

## 23. Module 19 is not legality

Audit 0029 §14 preserved. Identical owner state with residual 0.05 and 0.99
produces an **identical** legal set and an identical affordable set
(`test_the_residual_cannot_create_or_delete_a_legal_action`).

Value may differ if the historical bin differs. Legality cannot.

---

## 24. Module 9 is not legality

A high temporal risk does not make a freshness branch legal. No Module 9 field
is reachable from the catalogue modules, and no action's provenance mentions
risk (`test_risk_cannot_create_a_legal_action`).

---

## 25. Module 20 affordability integration

Every legal neural action carries a valid `BudgetActionDescriptor` under Module
20's audited taxonomy, verified for all six relations
(`test_every_legal_neural_action_has_a_module_20_descriptor`). No action reaches
the planner with unknown resource identity; one whose cost cannot be safely
bounded stays legal and is denied.

Module 20 is asked on a **deep copy** of its ledger, so an affordability probe
never reaches the real one — Layer 6 asks "could this be reserved", not "reserve
it" (§44).

**Affordability is asked per action against the current ledger**, because Layer
6 schedules no sequence. That is recorded here because it is a real semantic
choice: a cap that would be exhausted by three actions in a row does not make
any one of them individually unaffordable.

The three states stay distinct: a denied action remains listed as legal, is
absent from `affordable_actions`, and cannot be the planner's choice
(`test_legal_but_unaffordable_is_a_distinct_visible_state`).

---

## 26. Protected reserve

With general capacity exhausted and a verification floor remaining, untagged
discovery actions are legal-and-denied while verification reaches its own floor
— the first complete `legality → affordability → value` test
(`test_a_protected_reserve_denial_survives_integration`).

---

## 27. Hard cap

With a zero hard cap, every action stays legal, none is affordable, and the
decision is `STOP / NO_AFFORDABLE_ACTION`
(`test_the_hard_cap_denial_survives_integration`). Nothing partial reaches the
real ledger.

---

## 28. Cache-aware cost

A Module 17 verification action with a cold control cache costs more than the
same action warm — and it is **the same semantic action**: identical
`action_id` and identical identity, only the resource plan differs
(`test_cache_state_changes_cost_not_semantic_identity`). No separate "cold
verify" and "warm verify" actions exist.

---

## 29. Module 21 ranking integration

The planner ranks exactly the affordable subset: the action ids in its
`utilities` equal `affordable_actions` and are a subset of `legal_actions`
(`test_the_planner_ranks_only_legal_and_affordable_actions`).

**Interpretation recorded rather than chosen silently.** Module 21 is handed the
**legal** set — which is what Appendix C names as its input — and applies its
own Module 20 screen to decide what it will *rank*. Handing it only the
affordable subset was the first implementation and was **wrong**: with
everything denied the planner saw an empty list and reported
`NO_LEGAL_ACTION`, when the truth was `NO_AFFORDABLE_ACTION`. Both layers ask
Module 20 rather than deciding for themselves, so the two screens agree by
construction; this is a diagnostic split, not a second policy.

No planner semantics changed: the utility equation, binning, coefficients,
threshold, depth-1, depth-2 and tie-break are all untouched.

---

## 30. Strict threshold

Preserved through integration: with every affordable action worth exactly
`τ_continue`, the decision is `STOP / UTILITY_BELOW_THRESHOLD`
(`test_the_strict_threshold_survives_integration`).

---

## 31. STOP semantics

The three reasons remain exact, and integration introduces none of `LOW_R_T`,
`STABLE_SET`, `LOW_RISK` or `M7_STOPPED`
(`test_the_three_stop_reasons_are_unchanged_by_integration`).

All three are now reachable from real state: `NO_LEGAL_ACTION` on an empty
catalogue, `NO_AFFORDABLE_ACTION` under a zero cap, `UTILITY_BELOW_THRESHOLD`
under low-value bins.

A missing historical bin remains a **configuration error**, not STOP
(`test_a_missing_bin_is_a_configuration_error_not_a_stop`).

---

## 32. Depth-2 integration

Unchanged from Audit 0031 §29 and re-exercised through the canonical catalogue:
second-step availability respects the first action's identity, its
repeatability, and Module 20 affordability *after* the first reservation on a
hypothetical ledger. No simulator was added, and a successor whose legality is
not recoverable from the recorded successor specification fails under the
existing depth-2 contract rather than being guessed.

---

## 33. All nine action families

`owner_action_families()` maps every one of the nine to at least one owning
module, and the mapping is asserted complete against `ActionFamily`
(`test_every_action_family_has_an_owner`). `STOP` is absent, as §17 requires.

**No family is silently unreachable**, which is the pass criterion for this
milestone.

---

## 34. All six relations

Every relation produces a non-empty owner-derived catalogue containing at least
one discovery-capable family, with every action carrying provenance
(`test_the_all_six_relation_matrix`). Live seam, three-query CLI-equivalent run:

| Relation | legal | affordable | denied | excluded | decision |
| --- | --- | --- | --- | --- | --- |
| `awardWonBy` | 1 | 1 | 0 | 14 | ACTION |
| `personHasCityOfDeath` | 8 | 4 | 4 | 6 | ACTION |
| `hasCapacity` | 1 | 0 | 1 | 8 | STOP / NO_AFFORDABLE_ACTION |
| `hasArea` | 1 | 0 | 1 | 8 | STOP / NO_AFFORDABLE_ACTION |
| `countryLandBordersCountry` | 1 | 1 | 0 | 6 | ACTION |
| `companyTradesAtStockExchange` | 6 | 6 | 0 | 6 | ACTION |

The award row shows the mechanism working: its specialist had already run every
facet, so fourteen actions are excluded as `ALREADY_EXECUTED` and one remains.
The stock row is post-§15B: its cross-family entry is excluded because Module 15
declares the branch statically ineligible in this configuration.

---

## 35. Capacity example

Owner-derived actions exist; low-value historical bins put every affordable
utility at or below `τ_continue`; decision `STOP / UTILITY_BELOW_THRESHOLD`.

Module 21 accepts nothing — §17.1's "accept rather than wasting verifier loops"
is expressed as *stop spending*, and Module 8 finalises the already-resolved
state (`test_capacity_policy_example_owner_derived`).

---

## 36. Award example

An owner-derived facet action and an owner-derived verification-class action are
both legal and both affordable; the higher-value one is selected. Value decided,
not affordability — `affordable_actions` contains more than one entry.

No relation-specific branch exists anywhere: the result comes from owner state,
historical bins, the §17 equation and Module 20 affordability
(`test_award_policy_example_owner_derived`).

---

## 37. Border example

Owner-derived actions exist and every one falls below threshold; decision
`STOP / UTILITY_BELOW_THRESHOLD`. No border-specific stop rule
(`test_border_policy_example_owner_derived`).

---

## 38. Death example

Failed-recall-only state. The owner-derived candidate-free action is legal and
affordable and wins on value.

**Audit 0024 preserved**: the decision payload contains no `substantive_null`,
`final_empty` or `ObjectEntities`, and the planner selects an action rather than
emitting an empty prediction (`test_death_policy_example_owner_derived`).

---

## 39. Module 7 and Module 21 coexistence

M7 is production; M21 is shadow. They are not combined, not required to agree,
and neither constrains the other. `controller.py`, `types.py` and
`selection.py` are unchanged in git, and the integration modules contain no
`ProgramState`, `Budget.charge`, `finalize` or `Prediction`.

Module 7's control state is carried in the artefact purely for later comparison.

---

## 40. M7 STOP versus M21 STOP

Both exist; neither reads the other. M21's STOP terminates nothing, and M7
continuing does not force M21 to select an action. Both are persisted so a
future integration can compare them.

---

## 41. Staged execution and model role

Model role is preserved in the action identity and in the Module 20 descriptor —
Module 17's verifications carry the verifier role, discovery carries the
enumerator role — so no action identity is lost across a role boundary. Layer 6
performs **no** swap and modifies no staged execution.

---

## 42. No execution

Layer 6 returns a catalogue and a decision. It contains no `def execute`,
`runtime.generate`, `score_labels(`, `pending_action`, `swap_model` or `graph.`
(`test_layer_6_executes_nothing`). Every record carries an explicit
`no_execution` marker.

---

## 43. Zero-neural proof

* **AST import scan** — no `torch`, `transformers`, `requests`, `httpx`, and
  nothing from `cover_kbc.data`.
* **Behavioural** — building the catalogue and running the full integration adds
  **zero** runtime calls, measured directly; the six-relation shadow run made
  **58 calls with Layer 6 on and 58 with it off**.

---

## 44. Upstream immutability

Before/after equality asserted for the consensus results, Layer-4 states,
coverage-gap states, specialist results and query profiles
(`test_layer_6_mutates_nothing_upstream`). Module 20's real ledger is untouched
— zero reserved calls and no reservations after integration
(`test_the_real_module_20_ledger_is_never_touched`).

---

## 45. Shadow invariance

Six relations, full stack, Layer 6 on versus off: runtime calls identical (58),
predictions identical, and `consensus_results`, `layer4_results`,
`coverage_gap_results`, `relation_budget_results`, all four specialist result
lists, `query_profiles`, `prompt_programs` and `retrieval_results` all equal.
`layer6_results` is populated only with it on, and its catalogue is genuinely
non-empty.

---

## 46. Module 8 invariance

Predictions are identical with Layer 6 on and off. Module 8 receives no Module
21 decision, and no relation-specific finalisation was modified.

---

## 47. Persistence

`layer6_control.jsonl`, one record per query, written only when Layer 6 is
enabled — which, with shipped configs, is never.

Each record carries the Layer-6 and catalogue versions, query identity, the full
catalogue with owner and provenance, exclusions with reasons, legal /
affordable / denied action lists, the embedded Module 21 decision, Module 7's
production-control snapshot, the `no_execution` marker and errors. Round-trips
exactly. No prior artefact schema was modified.

No record contains `gold`, `ObjectEntities`, `prediction`, `accepted`,
`rejected` or `leaderboard`.

---

## 48. Production calibration is still absent

Unchanged by this milestone. Module 20 has no TRAIN-calibrated budgets; Module
21 has no TRAIN historical bins, no α/β/γ/δ/η/κ and no τ_continue. Shipped
configs carry `layer6_integration.enabled: false`,
`relation_budget_scheduler.calibration_file: null`,
`micro_planner.historical_bins: null` and
`micro_planner.planner_calibration: null`, and the absence of every one of them
is asserted (`test_shipped_configs_keep_layer_6_disabled`).

Activation is **impossible** without TRAIN packages: enabling M20 or M21 without
its calibration raises before anything runs.

---

## 49. Why architecture can be complete while calibration is absent

They are different artefacts with different evidence requirements. The
integration architecture is a set of contracts — who owns legality, who owns
affordability, who owns value, and in what order — and the proposal fixes all of
it today. The numbers are estimates the proposal explicitly defers to TRAIN.

So this milestone can and does prove the seam correct while leaving the policy
uncalibrated, and it proves the second fact as strongly as the first: shipped
activation is impossible without TRAIN packages. Declaring the layer incomplete
because calibration has not happened would confuse a contract with a
measurement; declaring it *calibrated* would be worse.

---

## 50. No TRAIN, VAL or TEST

No calibration of any kind was performed: no historical-bin construction, no
coefficient search, no budget-envelope calibration, no threshold tuning, no
action-policy fitting. VAL and TEST were never run and no leaderboard submission
was made. Only `SYNTHETIC_TEST` fixtures supply numbers, and only in tests.

---

## 51. Challenge compliance

* **Closed book** — no web, RAG, Wikipedia, Wikidata, KB lookup, vector
  database, external corpus, entity linker or search API.
* **No learned component** — the catalogue is a projection and the seam is call
  ordering; nothing is fitted.
* **Frozen model profile** — unchanged, 28.67B, two models. No model was loaded.
* **No DoLa.**
* **Benchmark immutable** — verified three ways.

---

## 52. Tests

`tests/test_layer6_integration.py`, **85 tests**, covering the brief's 81
numbered requirements: integration-not-a-module, owner-derived catalogues for
all six relations, every adapter, identity and deduplication and precedence,
repeatability and history, the three non-legality rules (M19, M9, M20), the
three affordability denials, cache-aware cost, planner ranking and STOP
semantics, family coverage, the four policy examples, no-execution and
immutability, shadow and M8 invariance, persistence, and configuration —
plus the §15A corrective set: §17A trigger fidelity, the two reserve
assignments, generic checks receiving neither, and the protected-reserve
isolation matrix; and the §15B set: static eligibility alone never opening the
branch, `LOCALLY_CLEAR` and `NOT_EVALUATED` regressions, each firing trigger,
the one-shot executed rule, and Module 9 being unable to substitute for the
local trigger.

Full suite: **2643 passed, 3 skipped** (2558 before this milestone).

Every subject is fictional; every package is `SYNTHETIC_TEST`.

---

## 53. Pyflakes

`python -m pyflakes src/ tests/ scripts/` — **clean**.

---

## 54. Model budget

`scripts/audit_model_budget.py` → **PASS**, total **28.67B**.

---

## 55. Benchmark integrity

All three git commands **empty**, run directly and asserted by test. Upstream
pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged.

---

## 56. Explicit non-goals

Not implemented, not stubbed, not referenced:

* any new numbered module (§3);
* any action execution, reservation, role swap or graph mutation (§42);
* any legality decided by Layer 6, Module 19, Module 9 or Module 20 (§7);
* any TRAIN calibration (§50);
* any change to Module 7's production control or Module 8's output (§39, §46);
* DoLa.

---

## 57. Verdict

**PASS**, amended in place per §15A, against all twelve criteria in §52 of
the brief.

The stock protected reserves route correctly: Module 15's §17A cross-family
branch draws on `FRESHNESS`, Module 18's parent/subsidiary counterfactual draws
on `PARENT_SUBSIDIARY` and is identified by its contract-declared class, and
neither can spend the other's pool (§15A).

The §17A gate requires **both** of Module 15's verdicts — static eligibility and
the local per-query trigger — so a statically eligible build whose query is
locally clear has no freshness action, and a fired trigger that already ran is
excluded as one-shot (§15B).

Every proposal action family is representable and owned; every live action
carries an owner-declared legality source; canonical identities are
deterministic and duplicates resolve by explicit precedence with the loser
recorded; Module 20 classifies every neural action and legal-but-unaffordable
survives as a distinct visible state; Module 21 ranks only what is legal and
affordable while still being able to distinguish `NO_LEGAL_ACTION` from
`NO_AFFORDABLE_ACTION`; its §17 STOP semantics are exact; no action executes;
Module 7 remains production authority; Module 8's output is byte-identical; and
no calibration was invented.

Audit 0031's empty-live-action gap is closed — and its stated cause was
corrected: three owners already published a legality surface, and what was
missing was the adapter this milestone adds.

    M20 Relation Budget Scheduler              DONE
    M21 Expected-Value Micro-Planner           DONE
    Layer-6 Control Integration                DONE

    Layer 6 complete.

Next step: **Full M0–M21 Architecture Audit**, on a separate authorised brief.
Calibration is not begun here.
