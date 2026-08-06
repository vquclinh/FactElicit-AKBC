# Audit 0029 — Layer 5: Coverage Gap Integration and Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: **layer-boundary integration**, not a numbered module.
Mode: **shadow**, **disabled by default**, **zero neural calls**.

---

## 1. Objective and scope

Prove that the complete Layer-5 seam is

```
corrected Layer4EvidenceState -> M19 CoverageGapEstimator -> CoverageGapState -> (future M20/M21)
```

and that the seams which must **not** exist are absent: no path from Module 19
to the controller, the final selector, a STOP, an action recommendation, a
budget, or a factual adjudication.

In scope: the seam itself, the factual-authority boundary, discovery-origin
provenance and deduplication, the facet-ownership matrix, the registry/execution
distinction, conditional-operation semantics, and boundary regression tests
across all six official relations.

Out of scope and not implemented: M20, M21, DoLa. No new architecture.

**Layer 5 is an observation and estimation layer.** It measures nothing new,
spends nothing, and changes no production prediction.

---

## 2. Proposal sections read

| Section | What it fixed here |
| --- | --- |
| **§15** | The five-term ensemble and the three per-program readings — the contract M19 already implements and this audit re-checks at the seam. |
| **§15.1** | The facet registry and the four states. Confirms stock's *"primary/secondary/dual listings are semantic facets"*, consistent with M15 ownership (§11). |
| **§16** | **Downstream boundary only.** M20 owns relation budget allocation, reserved envelopes, cache-aware accounting and *"precharge before every neural call"*. None of that vocabulary may exist in Layer 5 (§30). Not implemented. |
| **§17** | **STOP/action boundary only.** M21 owns `U_t(a) = α·Ĝ_verified + β·ΔR̂ + γ·ΔĤ − δ·Ĉost − η·R̂edundancy − κ·F̂P`, `a* = arg max U_t(a)` and *"selects a\* if U_t(a\*) > τ_continue; otherwise returns STOP"*. None of that may exist in Layer 5 (§31). Not implemented. |
| **§17.1** | The four policy examples confirm the division: they consume coverage state, they are not part of it. |
| **Appendix C** | Module I/O — M19 sits between "candidate consensus states" and a "residual/gap state"; Neural: **No**. |

Prior audits read: **0008** (F/L/X/C/U), **0012** (M0–M8), **0022** (M15 incl.
§17A), **0023** (M16), **0024** (M14 NULL), **0025** (M17), **0026** (M18),
**0027** (Layer 4 incl. §20A), **0028** (M19 incl. §9A).

**No material conflict between the proposal and the implementation was found.**

---

## 3. Architecture position

```
M0-M8    core                          production, frozen
M9-M11   query intelligence            shadow
M12-M15  Layer 2 specialists           shadow
M16      Layer 3 atomic consensus      shadow
M17/M18  Layer 4 verification          shadow
Layer 4  evidence integration          shadow
M19      Layer 5 coverage gap          shadow   <- this layer
M20/M21  Layer 6                       NOT IMPLEMENTED
```

Layer 5 has exactly one producer (Layer 4) and, today, no consumer.

---

## 4. Why this is a layer audit, not a module

Audit 0028 proved Module 19 computes §15 correctly **in isolation**. That is not
the same as proving it is wired correctly. A module can be individually correct
and still, at the seam:

* re-derive a factual verdict an audited layer already owns;
* count one physical model call as two discoveries because two representations
  of it exist;
* let a downstream layer's semantics leak upstream.

This milestone tests the wiring and the prohibitions. Its natural output is
tests and documentation, plus a fix only where a real defect appears — one did
(§9).

---

## 5. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/coverage_gap/facet_coverage.py` | **One defect fix** (§9): numeric novelty identity now reads Module 12's own cluster assignment. +17 / −7 lines. |
| `tests/test_layer5_integration.py` | **New**, 78 tests, 1471 lines. |
| `docs/audits/0029-...md` | This audit. |

Nothing else. No config change, no new dependency, no schema change. Module 19's
public contract, artefact schema and `estimator_version` (`m19-v1`) are
unchanged, because the fix alters which *identity* a numeric origin emits, not
the record's shape.

---

## 6. The Layer 4 → M19 seam

The seam lives at the Phase-C boundary in `CoverPipeline._run_consensus`, after
Layer-4 integration:

```python
if self.layer4_integrator is not None:
    self._integrate_layer4(result, graph)
    if self.coverage_gap_estimator is not None:
        self._estimate_coverage_gap(result, graph.contract)
```

It supplies three things and nothing else: the Layer-4 state, the
`ProgramType` from the M0/M1 contract, and the applicable specialist's
structural execution metadata. The estimator cannot be constructed with Layer 4
disabled — both `build_coverage_gap_estimator` and the pipeline raise.

Re-estimating over the same state twice returns an equal object and leaves the
Layer-4 state, the M16 consensus, the specialist result and `graph.rcse_state`
deep-equal (`test_the_layer5_seam_spends_nothing_and_mutates_nothing`). No
integration helper mutates an upstream object.

---

## 7. Layer 4 is the factual evidence authority

The central integration check, enforced structurally rather than by review.

`missingness.py` — the estimator — **imports nothing from
`cover_kbc.specialists`**, asserted on the AST. Every factual quantity it reads
arrives through the Layer-4 state: `hard_contract_violation`,
`specialist_verifier`, `structural_contradicting_groups`, `structural_checks`,
`base_d`, `base_group_supports`, `competing_clusters`, `null_state.*`,
`pending_checks`, `propositions`.

It re-implements no verdict: the source contains no `is_supported`,
`is_contradicted`, `is_verified`, `is_valid`, `is_invalid`, `adjudicate`,
`decide`, `accept`, `reject`, `recluster` or `reverify`
(`test_module_19_never_re_decides_an_audited_factual_verdict`).

Behavioural counterpart: removing the specialist metadata from the call changes
**only** the facet map. Incidence, disagreement and unresolved mass are
byte-identical with and without it, because those are Layer 4's
(`test_the_seam_reads_layer4_for_evidence_and_specialists_for_structure`).

---

## 8. Raw-specialist access, classified

Module 19 touches the specialists in exactly one file, `facet_coverage.py`, and
every attribute is classified:

| Attribute | Class | Why it is not factual |
| --- | --- | --- |
| `probe_families`, `facets`, `kind`, `slices`, `enabled`, `stage_a`, `stage_b`, `gate`, `acquisition`, `missingness`, `cross_family`, `facet_id`, `family`, `rationale` | **STRUCTURAL** — registry declaration | Says which probes exist and which are switched on. Layer 4 has no notion of a facet. |
| `operation_id`, `observations`, `status_observations`, `locality_observations`, `listing_observations`, `candidate_observations` | **EXECUTION METADATA** — identity and ordering | Says which operation ran, in what order. Carries no verdict. |
| `parse_status`, `usable`, `source` | **EXECUTION METADATA** — parse status | `usable` is *"parsed cleanly and denotes the quantity the contract asks for"*. Each specialist raises on any attempt to read it as verification: *"Module 12 never verifies"*. It never means supported or valid. |
| `normalized_surface`, `canonical_value`, `clusters`, `member_indices` | **STRUCTURAL** — identity formation | Used only to say *which* identity an origin emitted, for novelty. §9. |
| `new_surfaces`, `closure.missingness_probed`, `closure.missingness_empty` | **EXECUTION METADATA** — did this probe yield anything new | The only audited exhaustion evidence (§16). A statement about an operation, not about a candidate's truth. |

The allow-list is **enforced, not decorative**: the test collects every field
and property name the four specialist packages expose, intersects it with the
attributes `facet_coverage.py` touches, and fails if anything falls outside the
structural list. A future specialist field pulled into the projection must be
classified here before the suite passes
(`test_specialist_access_is_structural_only`). It also fails outright on
`verified`, `accepted`, `rejected`, `is_valid`, `contradicts`, `argmax_label`,
`final_objects`, `substantive_null` or `confidence`.

**No factual bypass found.**

---

## 9. Discovery-origin provenance — defect found and fixed

**Defect.** Numeric novelty formatted the raw canonical float as the emitted
identity:

```python
key = format_numeric(obs.canonical_value) if obs.usable ... else ""
```

That imposes an **exact-equality** identity rule of Module 19's own invention.
Module 12 decides numeric sameness by tolerance clustering, and two readings it
places in one cluster — 25 000 and 25 001 — were counted as two distinct
discoveries. Every such near-duplicate inflated `noveltyRate`, and therefore
`R_t`, for `hasCapacity` and `hasArea`.

This is a §4 violation as well as a §16 one: identity for a numeric target is a
judgement the owning module already made, and M19 was making it again,
differently.

**Fix, in the smallest owning layer** (Module 19's own projection):

```python
cluster_of = {
    index: position
    for position, cluster in enumerate(result.clusters)
    for index in cluster.member_indices
}
for index, obs in enumerate(result.observations):
    position = cluster_of.get(index)
    _add(obs.operation_id, f"m12_cluster#{position}" if position is not None else "")
```

`member_indices` is Module 12's own published mapping — *"index into the
result's observation list, so a member can be traced back"*. **Nothing is
re-clustered**: the assignment is read, not recomputed. The mapping is total
over usable observations, because M12 clusters exactly the usable ones
(`_cluster_targets`: *"Only usable observations take part"*).

Proven on a two-cluster fixture: two readings with different raw floats inside
one M12 cluster emit `m12_cluster#0` twice, the second is **not** novel, and an
unusable observation emits nothing
(`test_novelty_identity_for_numeric_is_module_12s_clustering`). The end-to-end
counterpart checks the identities against the real M12 result and that each
usable observation contributes exactly one
(`test_numeric_discovery_origins_are_well_formed_end_to_end`).

No artefact schema changed; `estimator_version` stays `m19-v1`.

---

## 10. One physical origin is counted once

The novelty stream keys on **operation identity**, so representations do not
multiply into discoveries.

* **M11 record → mined specialist observation → Layer-4 candidate is one
  origin.** The mined observation keeps Module 11's own `operation_id`, so the
  three representations collapse to one entry, and the mined ids are a subset of
  the retriever's own recorded operation ids
  (`test_a_mined_m11_record_is_one_discovery_not_two`).
* **One origin emitting five identities is one origin with five emissions**, not
  five origins (`test_one_origin_emitting_many_identities_is_one_discovery_origin`).
* **No operation id appears twice** in the stream, for any of the six relations
  (`test_a_repeated_origin_id_is_never_two_discovery_origins`).
* For M14 and M15, which record several observation kinds per operation, only
  the acquisition kind feeds novelty, so a single call that produced both a
  status and a locality reading is still one origin.

Verification origins are excluded by construction: for every relation, no
structural check whose kind is not `CANDIDATE_FREE_RECALL` appears as a
discovery origin (`test_verification_never_enters_the_discovery_stream`).

---

## 11. Facet-owner matrix

Frozen at Audit 0028 §9A and verified **against the live registries**, not a
duplicated table. The test derives the expected mapping by asking each registry
which relations it declares, asserts no relation is claimed twice, and compares
to `FACET_OWNER` (`test_the_facet_owner_matrix_is_frozen_against_live_registries`):

| Relation | Owner |
| --- | --- |
| `hasCapacity`, `hasArea` | **M12** numeric registry |
| `awardWonBy` | **M13** large-open-set registry |
| `personHasCityOfDeath` | **M14** null/temporal registry |
| `countryLandBordersCountry`, `companyTradesAtStockExchange` | **M15** small-set registry |

---

## 12. Stock ownership regression

`companyTradesAtStockExchange → M15` is re-asserted at the seam, not only in the
registry: the facet map the **pipeline actually produced** for stock equals
M15's declared set, and stock and award facet ids are disjoint in both
directions (`test_stock_and_award_facets_never_cross`).

The §23 matrix row confirms M15's own structure end to end: `stock_listing_gate`
and `stock_listing_existence` (gate), `stock_primary`, `stock_secondary_dual`,
`stock_temporal`, `stock_company_itself` (acquisition), `stock_cross_family`
(§17A), `stock_missingness` — eight applicable, with only the missingness
template flagged as such
(`test_the_stock_matrix_exercises_module_15s_own_structure`).

Audit 0028's monkeypatch isolation tests remain: an award-registry change cannot
move a stock facet and vice versa.

---

## 13. Registry versus execution state

Proven at the seam for all six relations: the facet set the pipeline produced
equals the declared registry set exactly — nothing added, nothing dropped
(`test_only_declared_registry_members_are_facets`). The same test proves the
four distinctions the brief names:

| Thing | Facet? | Where it goes |
| --- | --- | --- |
| declared registry template | **yes** | the facet map |
| disabled template | **yes, excluded** | outside the denominator, with a reason |
| M11 mined memory | **no** | the novelty stream only — asserted as a subset of discovery origins and disjoint from the facet map |
| M18 structural check | **no** | evidence; its independence group never appears as a facet id |
| pending M18 check | **no** | unresolved state; its `kind` never appears as a facet id |
| candidate-explosion / gate state | **no** | execution metadata; absent from the map |

Cross-family recall is a facet because it is a declared M15 template carrying
its own `instruction` — a probe that runs — not because it produces state.

---

## 14. Conditional operations: coverage state is not action legality

Stock makes this concrete. The listing gate and the §17A cross-family recall are
**conditionally** executable — whether either may run depends on system state
Module 19 does not model.

`UNEXPLORED` records exactly one thing: **no operation was observed for this
declared facet.** It does not assert that the facet is legal to run now, that it
is eligible, that it is affordable, or that it is worth running. Likewise a
`facetGap` of 1.0 is not an instruction to close it.

Enforced: no record or module contains `eligible`, `executable`, `legal`,
`allowed`, `should_run`, `schedule`, `priority`, `ranked` or `suggest`, and no
function named `is_eligible`, `may_run`, `can_execute`, `is_legal` or
`next_facet` exists
(`test_an_unexplored_facet_is_not_a_claim_that_it_may_run_now`,
`test_a_facet_gap_is_not_an_instruction_to_close_it`).

Action legality belongs to M20/M21, which will read the whole system state. It
is **not** implemented here.

---

## 15. Four facet states

Across all six relations only the four states ever appear, and an excluded facet
always has `coverage = None` plus a reason
(`test_only_four_states_ever_appear`). `FacetCoverage` has exactly four members.

Excluded facets stay outside every denominator, checked against the recomputed
ratio for the three relations that have exclusions
(`test_excluded_facets_stay_out_of_every_denominator`).

---

## 16. EXHAUSTED proof

Empty, `UNKNOWN`, malformed and failed runs are all **WEAK**, for both a
missingness facet and an ordinary one
(`test_failure_never_establishes_exhausted`).

`EXHAUSTED` requires **both** that the facet is a declared missingness facet and
that explicit exhaustion evidence was recorded. Given identical evidence, the
missingness facet reaches `EXHAUSTED` and the ordinary facet stays `WEAK`
(`test_exhausted_requires_the_audited_missingness_evidence`).

This is Audit 0024's rule one layer up: failed recall is a coverage gap, never
evidence of emptiness.

---

## 17. Incidence semantics

Only genuine discovery mechanisms create captures. A candidate smothered in four
verification groups — `m17:SPECIALIST_VERIFIER`, `core:BLIND_VERIFIER`,
`core:EXISTENCE_GATE`, plus reverse, counterfactual and key-condition checks —
has **exactly the same incidence set** as the bare candidate, for `awardWonBy`,
`countryLandBordersCountry` and `companyTradesAtStockExchange`
(`test_no_verification_mechanism_creates_a_capture`).

A repeated independence group is one capture, as `q_g(o) = max` requires
(`test_a_repeated_independence_group_is_one_capture`).

`M18_CANDIDATE_FREE_RECALL` remains the one discovery-capable structural group,
per Audits 0026 and 0027 — it is not shown the candidate.

---

## 18. Singleton semantics

Over **groups**, never events. Verification cannot deflate it: the singleton
ratio is identical for the bare and the smothered candidate above.

No cardinality estimator exists anywhere in Layer 5 — no `chao`, `unseen`,
`estimated_total`, `true_set_size`, `capture_recapture`, `expected_remaining` or
`richness` in executable code, and no such field in any of the six produced
records (`test_no_cardinality_estimator_anywhere_in_layer_5`).

---

## 19. Novelty semantics

Deterministic and clock-free: `discovery_origins` returns identical output on
repeated calls for all six relations, and the module contains no `time.time`,
`datetime`, `random`, `shuffle` or `uuid`
(`test_novelty_ordering_is_deterministic_and_clock_free`).

Ordering is the recorded order of observations. The rate comes from the most
recent **eligible** origin, so there is no window to tune.

---

## 20. Saturation semantics

`saturation = 1 − noveltyRate`, descriptive only, never a sixth term.

A **barren origin is not saturation**: an origin that emitted nothing records
`novelty = null`, does not become the reference point, and leaves saturation at
0.0 (`test_a_barren_origin_is_not_saturation_and_not_exhaustion`).

**Saturation and exhaustion stay separate.** Full saturation across the novelty
stream leaves every facet's own state untouched — all still `UNEXPLORED` —
because only explicit facet-level missingness evidence can establish
`EXHAUSTED` (`test_saturation_and_exhaustion_are_separate_concepts`).

---

## 21. Audit 0027 §20A preserved

For `awardWonBy`, `countryLandBordersCountry` and
`companyTradesAtStockExchange`, a `KEY_CONDITION` check returning
`ALTERNATE_RECOVERED`:

* leaves `structural_contradicting_groups` empty;
* leaves the disagreement signal **UNAVAILABLE** — it contributes nothing;
* does **not** add `STRUCTURAL_CONTRADICTION` to the unit's reasons;
* leaves the candidate **resolved**, not rejected;
* survives only as raw provenance under
  `raw_diagnostics["alternate_recoveries"]`.

(`test_alternate_recovered_is_never_contradiction_at_layer_5`.) No Layer-5 code
converts it back: the module contains no `StructuralOutcome.CONTRADICT`,
`contradicts = True` or `is_contradiction`
(`test_no_layer5_code_reintroduces_alternate_as_contradiction`).

---

## 22. Disagreement channels

Six named families stay separate and separately readable: `m16_semantic_d`,
`m17_template`, `m17_label_order`, `m18_structural_contradiction`,
`m12_competing_clusters`, and M14's `m14_competing_localities` /
`m14_null_class_conflict` (`test_the_six_channel_families_stay_named_and_separate`).

The scalar is the deterministic **MAX** of the available bounded channels, equal
to `max(c.value for c in channels)` with all three channels still present and
inspectable (`test_the_reducer_is_max_and_raw_channels_survive`). No sum, no
mean, no fitted reducer, and no new uncertainty score — the module contains no
`fitted`, `calibrat`, `sum(c.value` or `mean(`.

Out-of-range or non-finite channels are refused, not clipped.

---

## 23. Unresolved mass

The target pool matches the ProgramType, checked on the produced records for all
six relations (`test_the_target_pool_matches_the_program_type`):

| ProgramType | Unit kind |
| --- | --- |
| `SMALL_SET`, `LARGE_OPEN_SET` | `candidate` |
| `NUMERIC` | `numeric_cluster` |
| `NULL_SINGLE` | `query_proposition` — one query-level unit |

`VERIFIER_NOT_REQUESTED`, `VERIFIER_UNAVAILABLE` and `VERIFIER_UNKNOWN` remain
three distinct reasons. A **pending** check and a **failed** check both mark a
unit unresolved and neither adds `STRUCTURAL_CONTRADICTION`
(`test_pending_and_failed_checks_are_unresolved_not_contradiction`).

Hard contract violations are excluded and listed. An empty pool is
**UNAVAILABLE**, never `0.0`. No acceptance semantics exist.

---

## 24. NULL_SINGLE integration

Audit 0024 re-proven at the complete Layer-5 boundary at three magnitudes — **1,
10 and 100 failed recalls** (`test_failed_recall_never_becomes_substantive_null`):

* `substantive_null_groups` stays **0**;
* `failed_recall_only` stays **True**;
* the query existence unit stays **unresolved**, with `FAILED_RECALL_ONLY`;
* `unresolvedMass` is **1.0** at every magnitude — a hundred abstentions resolve
  no more than one (`test_a_hundred_failed_recalls_do_not_reduce_residual_uncertainty`);
* the record contains no `final_empty`, `accepted_empty`, `is_empty` or
  `gold_empty`.

M19 reports available residual uncertainty. It never reports final empty.

---

## 25. Numeric cluster integration

Layer 4's carry is proven to be a **copy**, field by field, against Module 12's
own clusters for both numeric relations: `representative`, `dispersion`,
`independent_support` and `canonical_unit` are equal, with matching cluster
counts (`test_layer4_carries_module_12_cluster_state_as_a_copy`).

M19 re-derives none of it: no `cluster_values`, `recluster`, `tolerance`,
`median`, `convert_unit`, `to_canonical` or `0.05` evaluator tolerance, and no
`winner`, `best_cluster`, `argmax_cluster` or `select_value`
(`test_module_19_never_reclusters_or_reconverts`). The produced diagnostics
mirror the Layer-4 targets exactly
(`test_the_numeric_diagnostics_mirror_module_12`).

Numeric **identity** now also comes from M12 — see §9.

---

## 26. Availability

For all six relations every one of the five components is present, in canonical
order, and carries availability with prose. An `AVAILABLE` component has a value
in [0, 1] and positive effective weight; an unavailable one has `value = None`,
`effective_weight = 0.0` and a stated reason
(`test_every_component_carries_availability_with_a_reason`).

Unavailable is **dropped and renormalised**, never read as zero: `R_t` equals
the mean of the available components exactly, and the effective weight mass is
1.0 whenever anything is available and 0.0 when nothing is
(`test_unavailable_is_dropped_and_never_read_as_zero`). With nothing available,
`R_t` is `None`. With everything available and zero, `R_t = 0` — and nothing
stops.

---

## 27. Weights

Frozen at `1, 1, 1, 1, 1` with `weight_source = "uniform_unfitted"`, verified on
every produced record. No `per_relation`, `relation_weight`, `threshold`,
`tau_continue` or `cutoff` exists (`test_the_weights_stay_uniform_and_unfitted`).

No TRAIN or VAL calibration was performed. **This is the neutral executable
architecture setting, not a claim about the optimal production setting.**

---

## 28. R_t semantics

The disclaimer is present verbatim on every record for all six relations and
says `heuristic` and `not a probability`. No semantic alias appears in
executable code: no `probability`, `confidence`, `expected_missing`,
`expected_gain`, `leaderboard` or `f1_gain`
(`test_r_t_is_described_as_a_heuristic_everywhere`).

`R_t` is a heuristic residual search-need / coverage-gap index. Nothing more.

---

## 29. Module 6 RCSE coexistence

`src/cover_kbc/coverage.py` is unmodified — `git status --porcelain` on that path
is empty, asserted by test.

**No blend exists.** Module 19's source contains no `q_res`, `RCSEState`,
`rcse`, `estimate_residual` or `mechanism_gap`, so `q_res + R_t`,
`max(q_res, R_t)` and `mean(q_res, R_t)` are all unconstructible. The controller
and the selector contain no `coverage_gap`, `CoverageGapState` or `R_t`, and the
controller still reads M6 (`test_rcse_and_r_t_coexist_without_blending`).

`graph.rcse_state` survives the seam unchanged for every relation, and no
produced record mentions `q_res` or `rcse`
(`test_the_production_graph_state_is_untouched_by_layer_5`).

M6 owns production `q_res`; M19 owns shadow `R_t`. They coexist for a future
ablation.

---

## 30. No Module 20 logic

§16's vocabulary is absent from executable code: no `budget`, `reserve`,
`precharge`, `envelope`, `call_cap`, `hard_cap`, `allocate`, `spend`, `quota` or
`throttle` (`test_no_module_20_logic_exists_in_layer_5`).

`R_t` **cannot** depend on a budget: every function signature in the estimator is
checked on the AST for a parameter containing `budget`, `remaining`, `cost`,
`spent` or `cap`, and none exists (`test_r_t_cannot_depend_on_a_budget`).

---

## 31. No Module 21 logic

§17's vocabulary is absent: no `utility`, `expected_gain`, `expected_value`,
`redundancy`, `fp_penalty`, `choose_next`, `argmax_action`,
`continue_threshold`, `should_stop`, `next_action`, `micro_planner` or
`lookahead`. **`R_t` is compared to no threshold** — the module contains no
`residual >`, `residual <`, `r_t >` or `r_t <`
(`test_no_module_21_logic_exists_in_layer_5`).

No stopping or action token reaches any produced artefact, for any relation
(`test_no_stop_or_action_token_reaches_any_artefact`).

**Recorded to avoid a false positive:** `should_stop` *does* exist at
`src/cover_kbc/controller.py:744`. That is **core Module 7's** production
stopping authority, audited in 0012, pre-dating this work, unmodified, and
unrelated to M21. It does not read Module 19 and Module 19 does not call it. The
scans above run over executable Layer-5 code with comments and docstrings
stripped, so negative prose in audits or config comments cannot satisfy them.

---

## 32. Zero-neural proof

* **AST import scan** — no `torch`, `transformers`, `requests`, `httpx`,
  `urllib`, `socket`, and notably no `cover_kbc.models`, so no runtime type is
  even reachable.
* **Source scan** — no `LMRuntime`, `GenerationRequest`, `score_labels`,
  `generate(`.
* **Behavioural** — `runtime.calls` is identical across the seam, and identical
  between the M19-on and M19-off six-relation runs.

The CLI reports `0 neural calls` on every run. No model was downloaded or
executed; every fixture is offline and scripted.

---

## 33. All-six-relation matrix

One scripted end-to-end run per official relation, with **fictional subjects**
and an offline runtime. Each produces a coherent state with `errors == ()`, a
facet set equal to its declared registry, `R_t ∈ [0, 1]` where available,
program-appropriate diagnostics (`numeric_stability` non-null exactly for
`NUMERIC`, `null_competing_state` exactly for `NULL_SINGLE`), and an exact JSON
round-trip (`test_every_relation_produces_a_coherent_layer5_state`).

Observed via the staged CLI, three queries each:

| Relation | Facet states | mean R_t | calls |
| --- | --- | --- | --- |
| `awardWonBy` | 27 weak, 3 exhausted | 0.900 | 0 |
| `countryLandBordersCountry` | 3 weak, 3 exhausted | 0.500 | 0 |
| `companyTradesAtStockExchange` | 6 weak, 18 unexplored | 1.000 | 0 |
| `personHasCityOfDeath` | 9 weak, 12 unexplored | 1.000 | 0 |
| `hasCapacity` | 15 weak | 1.000 | 0 |
| `hasArea` | 12 weak | 1.000 | 0 |

No benchmark truth is asserted anywhere — only structure, availability and
arithmetic.

---

## 34. Component sanity pairs

Component-level only. **No monotonicity theorem is claimed for `R_t`**, because
the proposal defines none and several components can move at once.

| Pair | Result |
| --- | --- |
| adding a genuine second independent discovery group | singleton fragility 1.0 → 0.0, never up |
| resolving an `UNKNOWN` verifier with usable evidence | unresolved mass 1.0 → 0.0, never up |
| converting an `UNEXPLORED` facet to `COVERED` | facet gap drops by exactly 1/n, never up |
| explicit `EXHAUSTED` missingness vs `WEAK` | facet gap never up |
| adding a strong structural contradiction | disagreement never down |

---

## 35. Shadow invariance

**Byte level**, retained from Audit 0028: for `awardWonBy`,
`countryLandBordersCountry` and `hasCapacity`, all 19 prior artefacts are
byte-identical with M19 on and off, and `coverage_gap.jsonl` exists only in the
enabled run.

**Semantic level, all six relations** (new): with every prior layer enabled, an
M19-on run and an M19-off run produce equal predictions, equal runtime call
counts, and equal `consensus_results`, `layer4_results`, all four specialist
result lists, `query_profiles`, `prompt_programs` and `retrieval_results`. Only
`coverage_gap_results` differs — six entries versus none
(`test_the_six_relation_seam_is_semantically_shadow`).

---

## 36. Persistence

`coverage_gap.jsonl`, one record per query, in query-manifest order.

Round-tripped for **every ProgramType** across all six relations:
`from_json(to_json())` reproduces an equal object and an identical payload
(`test_the_record_round_trips_for_every_program_type`). The schema is stable —
`numeric_stability` and `null_competing_state` are always present, `null` where
not applicable.

No record contains `gold`, `ObjectEntities`, `prediction`, `accepted`,
`rejected`, `should_stop`, `next_action`, `budget`, `unseen` or `cardinality`,
scanned with the disclaimer field excluded because its own wording denies
several of those words.

---

## 37. Determinism and order invariance

Repeated estimation over one state yields an equal object. Reversing candidate
order leaves incidence, `R_t` and unresolved mass unchanged
(`test_the_estimate_is_order_invariant_and_repeatable`). `discovery_origins` is
stable across repeated calls for all six relations. No clock and no RNG.

---

## 38. Error handling

`CoverageGapError` is raised, never swallowed, for a ratio with an empty
denominator, a disagreement channel outside [0, 1] or non-finite, an incoherent
facet record, an incoherent residual component, a duplicate facet declaration,
and recorded execution for a facet the registry does not declare.

That last check is what caught the M11 mining boundary in Audit 0028 §34; it is
deliberately kept strict, and `errors == ()` on every produced record shows it is
not firing spuriously.

---

## 39. Tests

| Suite | Tests |
| --- | --- |
| `tests/test_layer5_integration.py` (**new**) | **78** |
| `tests/test_coverage_gap.py` (M19 unit) | 96 |
| Full suite | **2417 passed, 3 skipped** |

The new suite reads **no benchmark row**: checked on the AST — it imports nothing
from `cover_kbc.data`, calls no loader (`load_dataset`, `load_jsonl_rows`,
`load_all_splits`, `gold_lookup`, `parse_row`), and every `Query` it builds takes
its subject from the fictional `SUBJECTS` table it declares
(`test_this_suite_reads_no_benchmark_row`).

---

## 40. Pyflakes

`python -m pyflakes src/ tests/ scripts/` — **clean**.

---

## 41. Model budget

`python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`
→ **PASS**, total **28.67B** against the 32B cap. Enumerator
`mistralai/Mistral-Small-3.2-24B-Instruct-2506` 24.011B (verified), verifier
`Qwen/Qwen3.5-4B`. Layer 5 adds no model, no checkpoint and no parameter, and
introduces no new model dependency in any config
(`test_no_dola_and_no_new_model_dependency`).

---

## 42. Benchmark integrity

`git status --porcelain benchmark/`, `git diff -- benchmark/` and
`git diff --cached -- benchmark/` are all **empty**, run directly and asserted by
test. Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged.

---

## 43. No TRAIN, VAL or TEST tuning

No split was read to choose any semantics. No constant was changed on the basis
of any run. VAL and TEST were never executed; no leaderboard submission was
made.

The §9 fix was derived from Module 12's published contract — `member_indices` and
the "only usable observations take part" clustering rule — not from observed
outputs.

The staged CLI runs in §33 use `--limit 3` on TRAIN purely as pipeline plumbing:
they inspect no gold, and nothing observed there was fed back into a constant.
The new integration suite avoids TRAIN entirely and uses fictional subjects.

---

## 44. Challenge compliance

* **Closed book** — no web, RAG, Wikipedia, Wikidata, KB lookup, vector
  database, external corpus, entity linker or external search API. Layer 5 makes
  no network-capable import at all.
* **No learned component** — no fine-tuning, LoRA, continued pretraining, or
  learned router/classifier/calibrator/verifier/scorer. The weights are uniform
  unfitted constants; the layer is arithmetic over recorded evidence.
* **Frozen model profile** — unchanged, 28.67B, two models.
* **No DoLa** — absent from executable code and from every config.
* **Benchmark immutable** — verified three ways.

---

## 45. Explicit non-goals

Not implemented, not stubbed, not referenced in executable code:

* M20 relation budget scheduler — budgets, reserves, envelopes, precharge, caps.
* M21 expected-value micro-planner — utility, `arg max`, `τ_continue`, STOP.
* DoLa.
* Any action legality, eligibility or scheduling decision (§14).
* Any cardinality or unseen-count estimator (§18).
* Any acceptance, rejection or factual adjudication (§7, §23).
* Any blend of `q_res` and `R_t` (§29).

---

## 46. Verdict

**PASS.**

The Layer-5 seam is `corrected Layer4EvidenceState → M19 → CoverageGapState →
(nothing yet)`. Layer 4 is the sole factual evidence authority; Module 19's
direct specialist access is structural and execution metadata only, and that
classification is enforced by a test that fails on any unclassified field. One
physical discovery origin is counted once across the M11 → mined observation →
Layer-4 candidate chain.

One real conformance defect was found and fixed in the smallest owning layer:
numeric novelty was applying Module 19's own exact-equality identity rule
instead of Module 12's audited cluster assignment, inflating `noveltyRate` for
`hasCapacity` and `hasArea`. The fix reads M12's published `member_indices` and
re-clusters nothing.

Every prior audited invariant survives, including Audit 0027 §20A and Audit
0024's NULL semantics at 1, 10 and 100 failed recalls. Zero neural calls, shadow
only, disabled by default, consumed by nothing. M20, M21 and DoLa are absent
from executable code.

    M19 Coverage Gap and Missingness Estimator    DONE
    Layer-5 Integration / Conformance             DONE

    Layer 5 complete.

Next architecture step: **M20 Relation Budget Scheduler**, on a separate
authorised brief. Not implemented here.
