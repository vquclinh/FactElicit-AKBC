# Audit 0030 — Module 20: Relation Budget Scheduler Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: **M20**, the first Layer-6 module.
Mode: **shadow**, **disabled by default**, **uncalibrated**, **zero neural calls**.

---

## 1. Objective and scope

Implement proposal §16's relation budget scheduler: relation-aware compute
envelopes, discovery/verification reservation, protected special reserves,
global hard-cap enforcement, cache-aware accounting, precharge and settlement.

In scope: the qualitative Table-6 registry, the Module 9 risk projection, the
numeric calibration *surface*, the action taxonomy, the reservation ledger, and
a shadow replay that proves the taxonomy covers the architecture's existing
neural work.

Out of scope and not implemented: M21, DoLa, any numeric production
calibration, any action selection, any STOP.

**Module 20 reasons about neural calls and makes none.** It does not decrement
Module 7's budget, does not block a production action, and changes no
prediction.

---

## 2. The exact §16 contract

> *"M20 allocates budget by relation and reserves budget by action class."*
>
> *"Budget accounting must be cache-aware and precharge before every neural
> call. No action may exceed the hard cap."*
>
> *"concrete values are calibrated on TRAIN."*

Table 6, transcribed exactly (§9–§14 below).

§9.3 supplies the decomposition the envelopes implement:

```
B_r = B_seed + B_facet + B_verify + B_reverse + B_reserve
```

> *"where B_verify is a hard reservation that discovery cannot spend. This
> directly addresses the observed failure in which award queries generate tens
> or hundreds of candidates while almost none are verified."*

That sentence is why a protected reserve here is a constraint and not a label.

**No material conflict between this brief and the proposal was found.**

---

## 3. Appendix C I/O

> **M20 | Relation Budget Scheduler | Reserve discovery/verification/freshness
> budget by relation. | Neural: No**

Implemented as `relation + risk + remaining budget -> reserved envelopes`.
"Freshness" appears as a first-class `SpecialReservePurpose`, declared by the
two relations Table 6 gives it to.

---

## 4. Architecture position

```
Layer 6 - Test-Time Control: M7 Active Controller -> M20 Relation Budget
                             Scheduler -> M21 Expected-Value Micro-Planner
```

M7 is production and unchanged. M20 is shadow beside it. M21 is absent.

---

## 5. Relationship to core Module 7's Budget

M20 does **not** replace, wrap, mutate or route around `types.Budget`. It reads
an immutable `CoreBudgetSnapshot`, taken from the same contract-tightened
per-query budget the pipeline builds via `PipelineConfig.budget(contract)`.

Audit 0010's semantics are preserved verbatim and re-asserted by test:

| Concept | Meaning | Preserved by |
| --- | --- | --- |
| `calls_used` | actual neural runtime invocations | the snapshot copies it and never writes it |
| `logical_actions` | diagnostic, **never** a call count | carried separately; §31 test proves M20 never reinterprets it |
| `generated_tokens_used` | a separate resource | tracked separately throughout |
| `score_labels` | a neural call with zero generated tokens | `CallKind.SCORE_LABELS`; the type *refuses* a token bound on it |
| cache hit | no inference, zero cost | `CacheDisposition.CACHE_HIT.charges_a_call is False` |
| runtime counters | authoritative for settlement | `settle()` takes actual spend and reconciles against the hold |

The snapshot is a copy: mutating the source `Budget` afterwards does not move
it, and it exposes no `charge` or `reserve`
(`test_module_7_budget_semantics_are_preserved`).

---

## 6. Why M7 remains the production authority

M20 is shadow for this milestone by the brief's own instruction, and for a
structural reason: it has **no numbers**. §16 defers concrete values to TRAIN
calibration that has not been performed, so a scheduler promoted to production
today would have to invent its own ceilings. M7's audited hard cap is a real,
tested constraint; M20's numeric half is currently an empty surface. Layer-6
integration will decide how M20 becomes the active control budget once
calibration exists.

---

## 7. Files changed

New:

* `src/cover_kbc/control/__init__.py`
* `src/cover_kbc/control/budget_types.py` — public contract
* `src/cover_kbc/control/relation_budget.py` — Table 6, risk, plans, config
* `src/cover_kbc/control/budget_accounting.py` — cost plans, ledger, replay
* `tests/test_relation_budget.py` — 74 tests
* this audit

Modified: `src/cover_kbc/pipeline.py` (Phase-A shadow seam),
`scripts/run_staged.py`, `scripts/run_cover.py`, three experiment configs
(disabled, uncalibrated), and one Layer-5 test whose config scan was scoped to
the `coverage_gap` block now that M20 legitimately has its own.

Named `control/`, not `controller/`: core M7 owns `cover_kbc.controller` and
must not be shadowed. Unmodified and verified: `controller.py`, `types.py`,
`coverage.py`, `selection.py`, `benchmark/`.

---

## 8. Public types

`BudgetDemandTier`, `BudgetSpendClass`, `SpecialReservePurpose`,
`BudgetPressure`, `CallKind`, `CacheDisposition`, `CalibrationSource`,
`ReservationStatus`, `BudgetDenialReason`; `SubCall`, `ActionCost`,
`BudgetActionDescriptor`, `QualitativeRelationBudgetPolicy`,
`RiskBudgetDemand`, `CoreBudgetSnapshot`, `RelationBudgetCalibration`,
`BudgetEnvelope`, `RelationBudgetPlan`, `BudgetReservation`,
`BudgetSettlement`, `BudgetDenial`, `PhysicalCallRecord`,
`ReplayReconciliation`, `BudgetLedgerState`, `RelationBudgetResult`.

All immutable, validated, versioned and serialisable. None exposes
`next_action`, `utility` or `should_stop`.

---

## 9. The Table-6 registry

One declarative registry, `RELATION_BUDGET_POLICIES`. No relation name appears
in `budget_types.py` or `budget_accounting.py` — asserted by test — so relation
policy cannot drift into scattered branches.

| Relation | Discovery | Verification | Special reserve | Modifiers |
| --- | --- | --- | --- | --- |
| `countryLandBordersCountry` | LOW | LOW | REVERSE_SINGLETON | `verification_spot` |
| `hasCapacity` | MEDIUM | MEDIUM | CROSS_UNIT, CONTRAST | `multi_probe` |
| `hasArea` | MEDIUM | MEDIUM | CROSS_UNIT, CONTRAST | `multi_probe` |
| `awardWonBy` | HIGH | HIGH | MISSINGNESS, REVERSE | `discovery_capped`, `verification_hard_reserved` |
| `personHasCityOfDeath` | MEDIUM | MEDIUM_HIGH | FRESHNESS, CANDIDATE_FREE | — |
| `companyTradesAtStockExchange` | MEDIUM | MEDIUM | FRESHNESS, PARENT_SUBSIDIARY | — |

Asserted exactly, including that each relation declares **only** its own
purposes (`test_table_6_is_transcribed_exactly`,
`test_each_relations_special_reserves_are_preserved`).

---

## 10. Borders policy

Low discovery, low/**spot** verification, reverse-singleton reserve.
`verification_spot = True` records that this is a narrow envelope, **not** a
verify-everything policy. M20 does not choose which candidate gets the spot
check — it only ensures the relation does not reserve a broad high verification
envelope. M21 decides whether a particular border candidate is worth it.

---

## 11. Numeric policy (capacity / area)

The proposal gives capacity and area **one row**, so they share one qualitative
family here rather than two entries that could drift apart. Medium multi-probe
discovery, medium verification, and a cross-unit/contrast reserve protecting
the two checks that separate one defensible number from another — unit
agreement, and the seated-versus-attendance (land-versus-total) contrast.

---

## 12. Award policy

High but **capped** discovery, **hard-reserved** high verification, missingness
+ reverse reserve. This is §9.3's motivating case, and both structural flags are
enforced numerically once a calibration exists (§22, §21).

---

## 13. Death policy

Medium discovery, medium-high verification, freshness + candidate-free reserve.
§17.1's own worked example — *"no candidate, but null evidence is only failed
recall → run the fresh/candidate-free branch before returning empty"* — is why
that branch gets protected budget rather than competing with general discovery.

---

## 14. Stock policy

Medium discovery, medium verification, freshness + parent/subsidiary reserve.
Listings change over time and a parent's listing is not the subsidiary's, so
both get protected budget.

---

## 15. Module 9 risk consumption

`RiskBudgetDemand` carries five M9 grades into budget vocabulary:
`search_breadth → discovery_pressure`, `verification_priority →
verification_pressure`, `temporal_sensitivity → temporal_pressure`,
`near_miss_risk → near_miss_pressure`, `open_set_risk → open_set_pressure`.

**Carried, not combined.** Each pressure equals its M9 grade exactly, asserted
for all six relations. No scalar, no weight, no formula: the module contains no
`2 *`, `3 *`, `q_open`, `q_verify`, `weight`, `coefficient`, `sigmoid` or
`softmax` (`test_no_numeric_risk_formula_exists`).

Identity is validated before anything else — wrong subject, wrong relation,
wrong row or wrong `ProgramType` all raise. A profile for another query would
fund the wrong relation's envelopes, and that error is invisible until the run
is over.

---

## 16. Qualitative versus numeric separation

Two layers with different lifetimes, and they cannot reach each other:

* the qualitative policy is a frozen dataclass with **no int or float field**,
  asserted structurally and on the serialised payload;
* the tiers never become arithmetic — no `rank *`, `* rank` or `_TIER_ORDER[`
  outside the ordering accessor itself;
* changing a synthetic calibration cannot alter Table 6, the special purposes,
  the action classification or the M9 profile, because the calibration is a
  separate object the policy never reads;
* the policy registry is not reachable through ordinary calibration config —
  `load_calibrations` validates *against* the registry and can only be refused
  by it, never extend it.

---

## 17. Why no concrete production values exist

§16 says concrete values are *"calibrated on TRAIN."* TRAIN calibration has not
been performed, and this milestone explicitly forbids performing it.

So the repository contains **no production budget number**. Shipped configs
carry `enabled: false`, `calibration_file: null` and no integer field at all,
asserted by test. Enabling the scheduler without a calibration **fails loudly**
rather than defaulting to a guess:

> `relation_budget_scheduler.enabled is true but no calibration_file is
> supplied; proposal §16 states concrete budget values are calibrated on TRAIN,
> and none exist yet`

An invented ceiling is worse than an absent one, because it looks authoritative.

---

## 18. Calibration-source semantics

`CalibrationSource` is `TRAIN_CALIBRATED` or `SYNTHETIC_TEST`, and
`is_production` is true only for the former.

`load_calibrations` **refuses** a `SYNTHETIC_TEST` calibration unless
`allow_synthetic=True`, which is passed only by tests. This is what stops a
fixture from becoming a shipped budget by being copied into a config file
(`test_a_synthetic_calibration_is_marked_and_refused_in_production`). Every
calibration that exists today is `SYNTHETIC_TEST` and lives in a test fixture.

---

## 19. Action base classes

Two: `DISCOVERY` and `VERIFICATION`, because §9.3 protects one from the other.

A special purpose is a **modifier**, not a third class. M13's missingness probe
is discovery that happens to be protected; M18's reverse check is verification
that happens to be protected. Making "special" its own class would lose which
pool the action fundamentally draws on.

Classification is by **module and action identity**, never by prompt text — the
module contains no `prompt`, `raw_output` or text-matching branch
(`test_classification_never_parses_prompt_text`). A budget that depends on
wording changes when a prompt is reworded.

---

## 20. Special reserve purposes

A closed enum, not free strings: `REVERSE_SINGLETON`, `CROSS_UNIT`, `CONTRAST`,
`MISSINGNESS`, `REVERSE`, `FRESHNESS`, `CANDIDATE_FREE`, `PARENT_SUBSIDIARY` —
exactly Table 6's column.

An action tagged with a purpose the relation does not declare is **denied**
(`DENIED_BY_UNDECLARED_PURPOSE`), and a calibration reserving an undeclared
purpose raises at plan time.

A special reserve is resource protection. It is not evidence, and it is not an
instruction to execute the branch it protects.

---

## 21. Protected reserve semantics

**Interpretation recorded, not silently chosen.** The proposal leaves
cross-borrowing undefined, so the conservative reading is taken and documented
in the module: *a protected reserve is non-borrowable by unrelated classes.*
Verification's floor is reachable only by verification actions; a
special-purpose reserve only by actions tagged with that purpose. Everything
else spends from general capacity.

Mechanically, `_foreign_protected` withholds every pool the action may not
touch from its available capacity, so a "reserve" cannot be spent by the class
it is protected from. Proven three ways:

* discovery is denied once only the verification floor remains, and
  verification then reaches that floor
  (`test_discovery_cannot_consume_the_protected_verification_reserve`);
* seven discovery reservations exhaust general capacity and the five protected
  award verification calls survive intact
  (`test_hard_reserved_award_verification_survives_heavy_discovery`);
* an untagged action cannot reach a freshness reserve, a freshness-tagged
  action reaches its own and not candidate-free's
  (`test_an_unrelated_action_cannot_consume_a_special_reserve`), and a
  correctly tagged action does consume its own
  (`test_the_correctly_tagged_action_may_consume_its_special_reserve`).

---

## 22. Award discovery cap

Table 6 says *high but capped*, and high is not unlimited. `discovery_capped`
records it qualitatively; the class cap enforces it numerically and
**independently of the hard cap**: with a hard ceiling of 30 and a discovery cap
of 8, the ninth discovery call is denied by `DENIED_BY_CLASS_CAP` while 22 calls
remain globally (`test_award_discovery_is_capped_independently_of_the_hard_cap`).

---

## 23. Hard global ceiling

A relation policy may restrict a global ceiling and may **never** raise it —
the conservative resource-ceiling principle the core already applies.
`_intersect_ceiling` takes the minimum and **records a note** in the artefact,
so an over-generous calibration is visible rather than silently clipped.

A calibration asking for 100 calls against a caller ceiling of 10 yields
`hard_calls = 10`, both class caps ≤ 10, and the note *"the global ceiling
wins"* (`test_a_relation_can_never_raise_the_global_ceiling`). The same applies
to the generated-token ceiling.

Observed end to end: an award calibration with `verification_cap = 14` produced
an envelope cap of 12, because the contract-tightened per-query budget is 12.

`RelationBudgetCalibration` additionally refuses at construction: negative
values, reserve > class cap, class cap > relation hard ceiling, protected
reserves totalling more than the ceiling, and a duplicate purpose.

---

## 24. Cache-awareness

Four dispositions: `CACHE_HIT`, `CACHE_MISS`, `CACHE_UNKNOWN`,
`NOT_CACHEABLE`. `charges_a_call` is false only for `CACHE_HIT`.

**`CACHE_UNKNOWN` is reserved as a miss.** An unknown cache reserves exactly
what a known miss reserves. This is the one optimism a hard ceiling cannot
survive, because the mistake is discovered only after the call has been made.

A cache hit reserves zero calls and moves the ledger not at all
(`test_a_cache_hit_costs_no_call`).

---

## 25. Module 17 cold/warm control accounting

One M17 verification request is several real label readings plus contextual
calibration controls that may already be cached.
`specialist_verification_plan` consumes `control_calls_needed` — the
calibrator's **own audited number**, which returns zero for a template whose
control is already measured — rather than guessing a count here.

| State | Reserved calls |
| --- | --- |
| cold (3 readings, 3 controls, none cached) | **6** |
| warm (3 readings, 3 controls, all cached) | **3** |

Warm is strictly smaller, cached controls are recorded as hits rather than
dropped so the artefact shows what caching saved, and **no control is charged
twice** — sub-call labels are unique in both plans
(`test_module_17_warm_precharge_is_smaller_than_cold`). A plan claiming more
control calls than it has controls raises.

---

## 26. Precharge semantics

`plan cost -> reserve -> (execution elsewhere) -> settle`.

An action is M20-authorised only once its resource is held. `reserve()` returns
either a `BudgetReservation` or a `BudgetDenial`; nothing partial exists.
Reservation ids are deterministic — derived from the query, the action and the
ledger sequence — with no UUID, clock or RNG, so a replayed run reconciles
against the artefact it produced.

For this shadow milestone M20 is **not** wired into production execution;
reservation mechanics are tested against synthetic offline actions.

---

## 27. Action atomicity

An action is reserved **whole or not at all**, at its conservative complete
upper bound — never at a known minimum with the rest hoped about.

With a hard cap of 5 and 2 calls already held, a 4-call action is denied
outright: the ledger stays at 2, no partial hold is created, and nothing runs
(`test_a_multi_call_action_is_denied_before_execution`). This freezes *"no
action may exceed the hard cap"*.

An action that cannot state a safe upper bound is refused authorisation
(`DENIED_BY_UNKNOWN_COST`) rather than started — and `cost()` raises rather than
returning a guess. Expected cost is never used here; expected anything belongs
to M21.

---

## 28. Generated-token reservation

Tracked as a separate resource from calls, per Audit 0010.

* generation: one call, reserved at the declared decode upper bound;
* `score_labels`: one call, **zero** generated tokens — the type raises if a
  token bound is declared on one;
* prompt tokens are not treated as the same hard resource, matching M7.

Settlement releases the unused remainder: 900 reserved, 42 spent, 858 released
(`test_settlement_releases_the_unused_reservation`).

---

## 29. Settlement and release

`actual_calls <= reserved_calls` and `actual_generated_tokens <=
reserved_generated_tokens`, both enforced. Exceeding a hold raises:

> `reservation ... held 2 calls but 3 were spent; a neural call was made outside
> the precharge`

which is precisely the failure the precharge exists to prevent. The budget is
never allowed to go negative and nothing is silently clamped. Unused precharge
is released back to the class and purpose pools, so a cancelled 4-call hold
restores capacity for a later action.

Settlement records resource facts only; it rewrites no factual output.

---

## 30. Outstanding reservation safety

Every reservation is settled **or** cancelled exactly once. Double settlement,
double cancellation, settle-after-cancel and unknown ids all raise. A descriptor
belonging to another query is refused before any capacity is touched. Ids are
deterministic and query-scoped, so two queries cannot collide
(`test_reservation_ids_are_deterministic_and_query_scoped`,
`test_a_foreign_querys_action_cannot_be_reserved`).

---

## 31. Physical calls versus logical actions

Four distinct concepts, kept apart: logical action, reservation, physical
neural call, factual evidence event. **M20 charges only physical calls.**

* one M17 verification request → one logical action, 3 or 6 physical calls
  depending on cache, one factual mechanism;
* one M18 candidate-free generation naming five candidates → **one** physical
  call (`test_one_module_18_check_is_one_call_however_many_candidates`);
* `logical_actions` is never reinterpreted as a call count
  (`test_module_20_never_reinterprets_logical_actions_as_calls`).

Candidate evidence count never becomes call count.

---

## 32. Canonical physical-call identity

`PhysicalCallRecord.call_id`, with `identity_payload()` naming the immutable
metadata any two records claiming one call must agree on. Deduplication is by
that identity, so downstream representations collapse to the one call that
actually happened.

---

## 33. Replay of core M2 and M4

Both classify. M2 generation → `CallKind.GENERATE`, DISCOVERY, with its declared
tokens; M4 label scoring → `CallKind.SCORE_LABELS`, VERIFICATION, zero tokens.
`classify_generation_record` adapts a real core `GenerationRecord` from a live
scripted pipeline run into a physical-call record
(`test_a_core_generation_record_adapts_to_a_physical_call`).

The budget counts the whole architecture's neural work, not only the upgraded
half.

---

## 34. Replay of M11–M15

M11 parametric generation and each specialist's acquisition call classify as
DISCOVERY, carrying a special purpose where Table 6 gives one — M13's
missingness probe as `MISSINGNESS`, M14's freshness recall as `FRESHNESS`.

---

## 35. Replay of M17 and M18

M17 readings and controls classify as VERIFICATION `SCORE_LABELS`, with a
recorded cached control costing **zero**. M18's executed check classifies as one
generation call, tagged with its purpose where applicable.

The full ten-record matrix reconciles to **9 physical calls, 1 cache hit**, with
per-module, per-class and per-purpose breakdowns
(`test_every_module_in_the_architecture_is_classifiable`).

---

## 36. Deduplication

Nine records representing three physical calls — an M11 call plus its
specialist-mined representation, an M17 reading plus its Layer-4 projection, and
an M18 generation repeated once per recalled candidate — reconcile to **3
calls** with **6 duplicates collapsed**
(`test_one_physical_call_is_counted_once_however_many_representations`).

Two records claiming one call id with conflicting immutable metadata **raise**
(`test_conflicting_metadata_for_one_call_id_fails_loudly`).

A recorded cache hit costs zero on replay; a recorded physical call is counted
once. No cache hit is ever invented retroactively — physical runtime truth wins.

Replay mutates nothing and is marked **`REPLAYED`**, never `PRECHARGED`: these
calls were not precharged, and recording them as though they had been would
invent a history the run does not have.

---

## 37. Shadow pipeline seam

```
Query + M9 RiskProfile + read-only CoreBudgetSnapshot -> M20 plan
                                                      -> (numeric plan + ledger, only with a calibration)
```

Sits in Phase A immediately after Module 9, because the plan is an input to
execution rather than a summary of it. The scheduler cannot be constructed
without Module 9 — the pipeline raises, since the proposal I/O requires risk.

The snapshot comes from a **fresh** `config.budget(contract)`, so it can neither
alias nor mutate a budget an execution would use. Nothing is decremented, no
action is blocked, and predictions are unchanged.

---

## 38. Why M20 does not read R_t

Appendix C's I/O for M20 is *relation + risk + remaining budget*. Module 19's
residual is not in it, and *"higher R_t → more budget"* is a **value**
judgement: it says the query deserves more compute because its coverage looks
thin. That is exactly M21's expected-value calculation over the full state.

Enforced structurally: `budget_types.py`, `relation_budget.py` and
`budget_accounting.py` import nothing from `cover_kbc.coverage_gap`,
`cover_kbc.coverage`, `cover_kbc.evidence` or `cover_kbc.verification`, and
contain no `residual`, `R_t`, `coverage_gap`, `confidence` or `accepted`.
Neither `build_plan` nor `schedule` accepts a coverage, residual or graph
parameter, so no residual is reachable
(`test_module_20_reads_no_factual_evidence`,
`test_the_plan_is_identical_whatever_the_coverage_gap_says`).

Two actions with identical cost class price identically regardless of any
evidence attached to them elsewhere
(`test_two_actions_with_different_evidence_price_identically`).

---

## 39. Why M20 does not choose actions

Three properties are kept distinct: **resource-affordable** (M20),
**semantically legal** (the existing action registries), **useful** (M21). M20
owns only the first.

It answers: can this *supplied* action be reserved, what would it consume, which
envelope funds it, and why was it refused. It answers none of: which action next,
which candidate to verify, which facet is best, whether to continue.

No `choose`, `select_action`, `rank_actions`, `rank_candidates`, `best_`,
`recommend`, `plan_next` or `next_` function exists. Every denial reason begins
`DENIED_BY_` and none is `STOP` (`test_module_20_selects_no_action`).

---

## 40. Why M21 is absent

§17's vocabulary is absent from executable code: no `utility`,
`expected_verified_gain`, `expected_value`, `uncertainty_reduction`,
`redundancy`, `fp_penalty`, `argmax`, `lookahead`, `next_action`,
`should_stop`, `tau_continue`, `continue_threshold` or `micro_planner`. No M21
file or stub exists in `control/` (`test_no_module_21_logic_exists`).

---

## 41. Why DoLa is absent

Not implemented and not referenced; `dola` does not appear in the module. M20
adds no model and no decoding strategy.

---

## 42. Zero-neural proof

* **AST import scan** — no `torch`, `transformers`, `requests`, `httpx`,
  `urllib`, `socket`, and no `cover_kbc.models`, so no runtime type is reachable.
* **Source scan** — no `LMRuntime`, `GenerationRequest`, `score_labels(`,
  `generate(`, `Qwen`, `Mistral`, `load_model`.
* **Behavioural** — the six-relation shadow run made **58 runtime calls with M20
  on and 58 with it off**.

The CLI reports `0 neural calls`. No model was downloaded or executed.

---

## 43. Shadow invariance

Six relations, full stack enabled (M9–M11, all four specialists, M16, Layer 4,
M19), M20 on with synthetic calibrations versus off:

* runtime calls **identical** (58);
* predictions **identical**;
* `consensus_results`, `layer4_results`, `coverage_gap_results`, all four
  specialist result lists, `query_profiles`, `prompt_programs` and
  `retrieval_results` all **equal**;
* `relation_budget_results` is 6 with M20 on and empty with it off.

Only M20's own artefact appears. The production budget is never decremented and
no production action is blocked.

---

## 44. Persistence

`relation_budget.jsonl`, one record per query, written only when M20 is enabled
with a valid calibration — which, with shipped configs, is never.

Each record carries the scheduler version, query identity, relation and
ProgramType, the M9 profile version, the qualitative policy, the qualitative
risk demand, the calibration version and source, the hard ceilings, the
envelopes with their protected floors, the ledger, reservations, settlements,
denials, replayed spend and errors. `to_json`/`from_json` round-trips.

No record contains `gold`, `ObjectEntities`, `prediction`, `accepted`,
`rejected`, `should_stop`, `next_action`, `utility`, `expected_gain`, `R_t` or
`residual` — scanned with the disclaimer excluded, since it names what it denies.

---

## 45. Error handling

`BudgetSchedulerError` or `ValueError` is raised, never swallowed or clamped,
for: query/profile mismatch (subject, relation, row, ProgramType), unsupported
relation, unsupported scheduler version, unknown config key, enabled without
calibration, synthetic calibration where production is required, negative
values, reserve > class cap, class cap > relation ceiling, infeasible protected
reserves, duplicate purpose declaration, purpose not declared for the relation,
a token bound on a label-scoring call, a control plan exceeding its own control
count, unbounded cost, duplicate reservation id, cross-query reservation,
double settlement, double cancellation, settlement exceeding its hold, and
conflicting metadata for one physical-call id.

The only ceiling intersection that clamps is the global-ceiling rule of §23,
which the contract defines explicitly and which records a note when it fires.

---

## 46. Tests

`tests/test_relation_budget.py`, **74 tests**, covering the brief's 94 numbered
requirements: proposal contract and non-neurality, Table 6 and all six
relations, calibration absence and fixture containment, M9 risk, factual and
R_t independence, classes and purposes, ceilings/caps/protected reserves,
cache-aware precharge, M17 cold/warm, M18, atomicity, settlement and
reservation safety, physical-call classification and replay, M7 preservation,
determinism, serialisation and architecture boundaries.

Every subject is fictional; every calibration is labelled `SYNTHETIC_TEST`.

Full suite: **2491 passed, 3 skipped** (2417 before M20).

---

## 47. Pyflakes

`python -m pyflakes src/ tests/ scripts/` — **clean**.

---

## 48. Model budget

`scripts/audit_model_budget.py` → **PASS**, total **28.67B**. Enumerator
`mistralai/Mistral-Small-3.2-24B-Instruct-2506` 24.011B (verified), verifier
`Qwen/Qwen3.5-4B`. M20 adds no model, no checkpoint and no parameter.

---

## 49. Benchmark integrity

`git status --porcelain benchmark/`, `git diff -- benchmark/` and
`git diff --cached -- benchmark/` are all **empty**, run directly and asserted
by test. Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged.

---

## 50. No TRAIN, VAL or TEST calibration

**No numeric envelope was calibrated.** No split was read, no historical
benchmark performance was inspected, and no parameter was chosen from any run.
VAL and TEST were never executed; no leaderboard submission was made.

The module imports nothing from `cover_kbc.data`. The only mention of TRAIN in
the source is the proposal's own sentence explaining why no numbers exist
(`test_no_train_val_or_test_is_read`).

The only proposal-derived production semantics in this milestone are the
qualitative Table-6 policy, the reserve structure, cache-aware accounting,
precharge and hard-cap safety — all of which the proposal fixes without numbers.

---

## 51. Challenge compliance

* **Closed book** — no web, RAG, Wikipedia, Wikidata, KB lookup, vector
  database, external corpus, entity linker or search API; no network-capable
  import.
* **No learned component** — no fine-tuning, LoRA, continued pretraining, or
  learned router/classifier/calibrator/scorer. The scheduler is deterministic
  arithmetic over declared policy.
* **Frozen model profile** — unchanged, 28.67B, two models.
* **No DoLa.**
* **Benchmark immutable** — verified three ways.

---

## 52. Explicit non-goals

Not implemented, not stubbed, not referenced in executable code:

* M21 expected-value micro-planner — utility, `arg max`, `τ_continue`, STOP.
* DoLa.
* Any numeric production calibration (§17).
* Any action selection, ranking or recommendation (§39).
* Any factual reading — evidence, verdicts, coverage, residual (§38).
* Any replacement of, or write to, Module 7's production budget (§5).
* Any production enforcement: M20 blocks nothing yet (§37).

---

## 53. Verdict

**PASS.**

Proposal §16 is implemented as architecture without inventing the numbers §16
defers to TRAIN. Table 6 is transcribed exactly into one registry; §9.3's
decomposition becomes real envelopes in which a protected reserve genuinely
cannot be spent by the class it is protected from. Accounting is cache-aware
with unknown caches reserved as misses, precharge is atomic so no action can
exceed the hard cap, settlement releases the unused remainder and refuses to go
negative, and a physical call is counted exactly once however many
representations it accumulates.

Module 20 reasons about neural calls and makes none: 58 runtime calls with it on
and 58 with it off, across all six relations, with every prior artefact equal.
Module 7 remains the production budget authority, unmodified. M21, DoLa,
utility, action selection, STOP and any residual-driven allocation are absent
from executable code.

**Concrete production budgets are not calibrated.** The proposal requires TRAIN
calibration; it has not been performed; no fake production values were
introduced, and enabling the scheduler without a calibration fails loudly.

Next architecture step: **M21 Expected-Value Micro-Planner**, on a separate
authorised brief. Not implemented here.
