# Audit 0028 — Module 19: Coverage Gap and Missingness Estimator Conformance

Status: **PASS** (amended in place — see §9A)
Date: 2026-08-06
Amended: 2026-08-06, correcting the §9 ownership table for
`companyTradesAtStockExchange` and adding ownership-isolation tests.
Milestone: **M19**, the first Layer-5 module.
Mode: **shadow**, **disabled by default**, **zero neural calls**.

---

## 1. Objective and scope

Implement proposal §15's residual estimator as an upgraded **shadow** signal
beside core Module 6, so the system can describe *where its own search is thin*
without deciding anything about it.

In scope: the facet registry projection and its four states, the incidence and
novelty heuristics, the disagreement reducer, the unresolved-mass accounting,
the `R_t` combination, configuration, the artefact and the Phase-C seam.

Out of scope and not implemented: M20, M21, DoLa, Chao2, any stopping rule, any
budget policy, any next-action recommendation.

**Module 19 measures nothing new.** It spends zero calls, mutates nothing
upstream, replaces nothing, and no production prediction reads it.

---

## 2. Proposal sections read

| Section | What it fixed here |
| --- | --- |
| **§15** | The equation, verbatim: `R_t = w1·noveltyRate + w2·singletonRatio + w3·facetGap + w4·disagreement + w5·unresolvedMass`. Also the per-program reading: set-valued relations use **incidence statistics as heuristics**, numeric relations use **cluster stability**, null-single relations use **competing-state uncertainty**. |
| **§15.1** | "Each relation has a facet registry. M19 marks facets as {covered, weak, unexplored, exhausted}." Four states, no fifth. |
| **§12, §12.1** | `q_g(o) = max` and the `(F, L, X, C, U, I, D, cost, risk)` tuple M19 reads and never recomputes. |
| **§13.1** | Template and label-order variation are **bias diagnostics** — which is why they are two named disagreement channels and not two witnesses. |
| **§14** | The four structural mechanisms, and which of them can *discover* (§18). |
| **Appendix C** | "M19 Coverage Gap \| graph + facet registry \| residual/gap state \| Neural: **No**." |

Prior audits read: **0006** (identity), **0008** (F/L/X/C/U), **0012** (M0–M8),
**0022** (M15, incl. §17A), **0023** (M16), **0024** (M14 NULL semantics),
**0025** (M17), **0026** (M18), **0027** (Layer 4, incl. §20A).

---

## 3. The proposal supplies no weights

Searched for numeric values of `w1`–`w5` in §15, §15.1, Appendix C and the
tables. **The proposal names the five weights and supplies no values anywhere.**

Fitting them would require TRAIN or VAL, which architecture construction may not
read. So M19 uses one global neutral vector, `w1 = w2 = w3 = w4 = w5 = 1`, and
records `weight_source = "uniform_unfitted"` in every artefact record. With
uniform weights `R_t` is exactly the **mean of the available components**, which
is the honest reading of "we have not calibrated this".

No per-relation weight, no per-program weight, no threshold, no cutoff and no
tunable window exists anywhere in the module
(`test_the_weights_are_uniform_global_and_unfitted`).

---

## 4. Module 6 RCSE is preserved byte-for-byte

`src/cover_kbc/coverage.py` is **unmodified**: `git status --porcelain` on that
path is empty, asserted in `test_module_6_rcse_is_untouched` rather than
claimed.

The brief suggested `src/cover_kbc/coverage/`. That path is **not available**:
`src/cover_kbc/coverage.py` *is* Module 6's RCSE, imported by nine modules, and
creating a package of that name would shadow it. The brief permits "a clean
repository-native equivalent", and §4 of the brief requires M6 to survive
untouched, so the module lives at **`src/cover_kbc/coverage_gap/`**. The
package docstring records why the name is what it is.

The two coexist without contact:

* M19 imports nothing from `cover_kbc.coverage` — checked on the **AST**, since
  tokenised source splits a dotted path and a substring scan would pass
  vacuously.
* M19's source contains no `RCSEState`, `q_res`, `mechanism_gap` or
  `estimate_residual`.
* Both packages own a `declared_facets` and an `unresolved_mass`. They are
  **different quantities in different namespaces** and neither is imported by
  the other; the test pins each to its own module.
* `graph.rcse_state` is deep-compared across the seam and is unchanged (§40).

Module 6 still owns production `q_res`. Module 19 owns nothing.

---

## 5. Zero neural calls

Established three ways:

1. **Import scan on the AST** — no `torch`, `transformers`, `requests`,
   `httpx`, `urllib` or `socket` in any M19 module.
2. **Source scan** — no `LMRuntime`, `generate(`, `score_labels`,
   `GenerationRequest`, `runtime`, `Qwen` or `Mistral`.
3. **Behavioural** — the pipeline seam runs against a `ScriptedRuntime` and
   `runtime.calls` is identical before and after
   (`test_the_pipeline_seam_spends_nothing_and_leaves_rcse_alone`).

The CLI line reports `0 neural calls` on every run.

---

## 6. What R_t is, and what it is not

`R_t` is a **heuristic residual search-need index in [0, 1]**.

It is **not** a probability, **not** an estimate of unseen objects, **not**
factual confidence, and **not** a stopping decision. Every persisted record
carries `residual_disclaimer` saying exactly that, and the disclaimer text is
asserted, not merely present.

A high `R_t` means *this query's search looks thin along axes we can measure*.
It licenses no conclusion about the answer.

---

## 7. Module 19 emits no decision

No field, name or literal in the module or the artefact matches `should_stop`,
`stop`, `next_action`, `recommend`, `budget`, `allocate`, `accepted`,
`prediction`, `confidence` or `probability`
(`test_no_decision_no_action_and_no_budget_fields`).

Module 21 owns STOP. Module 20 owns budget. Module 19 owns neither, and the
absence is enforced by test rather than by intent.

---

## 8. Module 7 does not consume Module 19

`src/cover_kbc/controller.py` and `src/cover_kbc/selection.py` contain no
reference to `coverage_gap`, `CoverageGapState`, `R_t` or `estimate_coverage`.
The production controller is unchanged and unaware.

---

## 9. The facet registry is projected, never re-declared

M19 declares **no facets of its own**. `FACET_OWNER` maps each relation to the
specialist that owns its registry, and `declared_facets(relation)` reads that
specialist's live registry:

| Relation | Owner | Applicable | Excluded |
| --- | --- | --- | --- |
| `hasCapacity` | M12 `probe_families` | 5 | — |
| `hasArea` | M12 `probe_families` | 4 | `historical_current_configuration` (NOT_DECLARED) |
| `awardWonBy` | M13 `facets` → slices | 10 | `geography` (DISABLED_BY_POLICY) |
| `companyTradesAtStockExchange` | **M15** gate + acquisition + missingness + cross-family | 8 | — |
| `personHasCityOfDeath` | M14 `stage_a` + `stage_b` | 7 | — |
| `countryLandBordersCountry` | M15 acquisition + missingness | 2 | `border_direct` (DISABLED_BY_POLICY) |

The projection is verified against the upstream registries directly
(`test_the_facet_registry_is_projected_from_upstream`), so a facet added to a
specialist appears here without editing M19.

No relation name appears in `missingness.py` or `gap_types.py`; all six appear
in `facet_coverage.py`, which is the registry layer
(`test_no_relation_switch_lives_outside_the_registry`).

Ownership is Audit 0022's and is now asserted **against the registries
themselves** rather than a table copied into the test: for every relation, the
owning module's registry declares it and no other registry does
(`test_each_relation_resolves_to_its_audited_owner_module`).

---

## 9A. Correction — stock facet ownership (amendment)

**Inconsistency found during review.** The §9 table above originally named
**M13** as the owner of `companyTradesAtStockExchange`, citing the M13
facet-slice registry as the source of its eight facets. That contradicts
Audit 0022, which assigns `companyTradesAtStockExchange` to **M15**, the
small-set closure specialist. M13 is the LARGE_OPEN_SET award specialist and
must not own stock.

**Classification: documentation-only. Not executable.**

Verified by inspection rather than assumed. `FACET_OWNER` in
`src/cover_kbc/coverage_gap/facet_coverage.py` has always read:

```python
"companyTradesAtStockExchange": "M15",
```

`declared_facets` dispatches on that value to `_small_set_facets`, which reads
`SMALL_SET_RELATIONS`. The routing could not have been wrong and gone
unnoticed: **`LARGE_SET_RELATIONS` contains only `awardWonBy`**, so a
dispatch to M13 would have raised `KeyError` on the first stock query rather
than producing a wrong map. The end-to-end run in §36 exercised all six
relations and stock produced its M15 facets.

| | Mapping |
| --- | --- |
| `FACET_OWNER` **before** correction | `hasCapacity→M12, hasArea→M12, awardWonBy→M13, personHasCityOfDeath→M14, countryLandBordersCountry→M15, companyTradesAtStockExchange→M15` |
| `FACET_OWNER` **after** correction | **unchanged** — the code was already correct |
| Audit §9 table **before** | stock owner recorded as M13 (**incorrect**) |
| Audit §9 table **after** | stock owner recorded as M15 (matches the code and Audit 0022) |

**Source registry for stock:** `SMALL_SET_RELATIONS["companyTradesAtStockExchange"]`,
projected across all four of M15's template groups.

**Resulting stock facet count: 8 applicable, 0 excluded** — not preserved to
match the old number, but re-derived from the live registry:

| Group | Facet ids | Count |
| --- | --- | --- |
| `gate` | `stock_listing_gate`, `stock_listing_existence` | 2 |
| `acquisition` | `stock_primary`, `stock_secondary_dual`, `stock_temporal`, `stock_company_itself` | 4 |
| `missingness` | `stock_missingness` | 1 |
| `cross_family` | `stock_cross_family` | 1 |

All eight are `enabled=True`, so nothing is excluded and the facet-gap
denominator is 8. The count is asserted as *whatever the registry yields*, never
as the literal 8 (`test_the_stock_projection_comes_from_module_15s_live_registry`).

**Non-facet state was not promoted to make a count.** Each of the eight is a
declared M15 template carrying its own `instruction` — a probe the specialist
actually runs. Cross-family recall qualifies on exactly that basis: it is a
registry template, not a flag. The listing gate *state*, pending Module 18
checks, candidate-explosion flags and temporal-uncertainty triggers are
execution metadata that name no probe and belong to no registry, and none of
them appears in the map
(`test_only_registry_templates_become_facets_never_execution_state`). This is
§34's distinction applied again: not every operation is a facet.

**Ownership-isolation tests added** (§49), so implementation and test can no
longer encode the same wrong owner:

* every relation resolves to the owner whose registry declares it, and to no
  other registry;
* disabling **every** M13 award facet changes the award map and leaves the
  stock map byte-identical;
* disabling every M15 stock acquisition template changes the stock map and
  leaves the award map byte-identical;
* disabling **one** M15 stock template moves exactly that facet, sets
  `DISABLED_BY_POLICY` with M15's own rationale, and drops the denominator by
  one;
* stock and award facet id sets are disjoint, with no award slice in stock and
  no stock template in award;
* Module 19 hard-codes no stock facet id anywhere (`grep` and source scan);
* an award facet id supplied as stock execution metadata raises
  `CoverageGapError`, and a full award run cannot move stock's `facetGap`.

**Scope of the amendment:** one line of this audit plus new tests. No
production code changed. §13's facet-gap equation, the four states, the
exclusion rules and every other M19 semantic are untouched.

---

## 10. The four facet states

Exactly four, in this order, with no fifth and no severity weight:

| State | Meaning | Contributes to gap |
| --- | --- | --- |
| `COVERED` | The facet ran and produced at least one usable, contract-relevant observation. | No |
| `WEAK` | The facet ran and produced none. | **Yes** |
| `UNEXPLORED` | The facet never ran. | **Yes** |
| `EXHAUSTED` | A missingness probe ran and evidenced closure. | No |

`FacetCoverage.contributes_gap` is the single place this is decided.

---

## 11. EXHAUSTED requires explicit evidence

An empty answer is `WEAK`, never `EXHAUSTED`. Failed recall is a coverage gap,
not proof of closure — the same principle Audit 0024 fixed in M14.

`EXHAUSTED` is reachable only when the facet is a **missingness** facet *and*
carries recorded `exhaustion_evidence` — a probe that explicitly asked for
anything uncovered and named nothing the query did not already hold. A
non-missingness facet cannot reach `EXHAUSTED` even with evidence attached
(`test_exhausted_requires_explicit_evidence`).

**Defect found and fixed during this milestone.** The first implementation
attached exhaustion evidence to *any* M13 facet that reported no new surface,
and `coverage_for` then ignored it for non-missingness facets. The artefact
showed ordinary facets carrying exhaustion prose while reading `WEAK` — a
misleading record. An ordinary facet naming nothing new may simply have
re-named what the query already held, which is not closure. Evidence is now
attached only where a missingness probe ran, and the constraint is pinned by
test.

---

## 12. A disabled facet is not a gap

The brief's central correctness requirement. Three exclusion reasons exist, all
carrying prose:

* `DISABLED_BY_POLICY` — the specialist declares the facet and switched it off.
  `countryLandBordersCountry`'s `border_direct` (Audit 0022 §11.1's
  minimal-change constraint) and `awardWonBy`'s `geography`.
* `NOT_DECLARED` — the relation's registry does not contain the facet at all.
  `hasArea`'s `historical_current_configuration`, which `hasCapacity` does
  declare.
* `NO_OPERATIONS` — reserved for a facet whose declared operation set is empty.

An excluded facet has `coverage = None` and is **outside the denominator
entirely** — not counted as covered, not counted as a gap. The type refuses the
incoherent combinations: an excluded record may not carry a coverage state, an
applicable record must carry one, and an excluded record must carry a reason
(`test_an_excluded_facet_cannot_carry_a_coverage_state`).

---

## 13. The facet-gap equation

```
facetGap_t = (#UNEXPLORED + #WEAK) / #applicable_active_facets
```

No per-state severity weight exists; `WEAK` and `UNEXPLORED` count the same.
The source contains no `0.7`, `0.3`, `severity` or `weak_weight`
(`test_the_facet_gap_equation_is_exact`).

When **no** applicable facet exists the result is `(None, reason)` — the signal
is **UNAVAILABLE**, never `0.0`. Dividing by a fabricated denominator of one
would have reported perfect coverage for a relation with nothing to cover.

Recorded execution for a facet the registry does not declare raises
`CoverageGapError` rather than being silently absorbed (§35).

---

## 14. Incidence counts groups, not events

`IncidenceDiagnostics` records, per candidate, the **sorted set of independence
groups** that captured it. Ten events inside one group are one capture, exactly
as `q_g(o) = max` requires (Audit 0006/0008). The incidence map is asserted
group-wise, not count-wise (`test_incidence_counts_groups_not_events`).

Candidates carrying `hard_contract_violation` are excluded from the pool and
listed in `excluded_candidates`.

---

## 15. No verification mechanism is a sighting

`_NON_DISCOVERY_GROUP_PREFIXES` excludes `m17:`, `M18_REVERSE`,
`M18_COUNTERFACTUAL`, `M18_KEY_CONDITION`, `core:BLIND_VERIFIER` and
`core:EXISTENCE_GATE`.

A verifier is **shown** the candidate; it cannot capture it. Adding a candidate
to a verifier and watching it agree would otherwise manufacture a second
capture and silently deflate the singleton ratio. The test proves a candidate's
incidence set is byte-identical with and without four verification groups
attached (`test_a_verifier_measurement_is_not_an_incidence_capture`).

The one exception is principled: **`M18_CANDIDATE_FREE_RECALL`** is a discovery
group, because that mechanism is not shown the candidate and can genuinely name
it (§18).

---

## 16. The singleton ratio

```
singletonRatio_t = #(objects captured by exactly one group) / #(objects captured by ≥1 group)
```

Over **groups**, never events. A candidate captured by zero eligible discovery
groups is outside both numerator and denominator.

An empty pool yields **UNAVAILABLE**, not `0.0` — no candidate is not the same
as no missingness (`test_an_empty_pool_is_unavailable_not_perfectly_covered`).

---

## 17. No cardinality estimation

The brief's hard prohibition. The module contains no `chao`, `unseen`,
`estimated_total`, `true_set_size`, `predicted_gold`, `capture_recapture` or
`expected_remaining`, and no field name contains `cardinality`, `unseen`,
`remaining` or `total_objects`
(`test_no_cardinality_estimator_exists`).

Singleton and doubleton counts are recorded as **descriptive incidence
diagnostics**. They are never assembled into `f1²/2f2` or any other estimator
of unseen richness. A per-query enumeration under an unknown, non-uniform,
model-dependent capture process does not satisfy Chao2's assumptions, and a
number that looks like a set size but is not one is worse than no number.

---

## 18. The novelty stream contains only discovery-capable operations

`discovery_origins()` builds the stream from:

* the applicable specialist's own acquisition observations, **including** its
  mined Module 11 sketches, in recorded order; and
* Module 18's **candidate-free recall** records only.

Reverse checks, counterfactual checks, key-condition checks, the blind verifier
and the specialist verifier are excluded **by construction** — each is shown
what it is judging. The test proves a state whose only structural checks are
reverse and counterfactual yields an empty novelty stream
(`test_verification_operations_do_not_enter_the_novelty_stream`).

---

## 19. The novelty rate

```
noveltyRate_t = #(distinct identities first seen at the latest eligible origin) / #(identities that origin emitted)
```

Read from the **most recent eligible discovery origin**, so there is no rolling
window, no lookback length and no smoothing constant to tune — the source
contains none of those words (`test_novelty_uses_the_latest_eligible_origin`).

The full per-origin history is persisted with `order_index`, `emitted`, `novel`
and `novelty`, so the trajectory is inspectable even though only the last
eligible entry drives the rate. An origin that emitted nothing records
`novelty = null` and does **not** become the reference point — a barren probe
is not evidence of saturation.

With no eligible origin at all the signal is **UNAVAILABLE**.

Ordering is the recorded order of observations, never a clock: the module
contains no `random`, `time.time`, `datetime` or `shuffle`.

---

## 20. Saturation is descriptive only

`saturation = 1 - noveltyRate`, recorded in the novelty diagnostics and
`None` whenever novelty is unavailable.

It is **not a sixth term**. `ResidualComponentName` has exactly five members and
`saturation` is not one of them (`test_saturation_is_derived_and_never_a_sixth_term`).

---

## 21. Disagreement is a max over bounded channels

Four candidate-level channel families, each already bounded in [0, 1] by an
audited upstream module, plus two program-specific ones:

| Channel | Source | Audit |
| --- | --- | --- |
| `m16_semantic_d` | M16's `D` | 0023 |
| `m17_template` | M17 template variation | 0025 |
| `m17_label_order` | M17 label-order variation | 0025 |
| `m18_structural_contradiction` | M18 contradicting group present | 0026 |
| `m12_competing_clusters` | competing numeric clusters | — |
| `m14_competing_localities`, `m14_null_class_conflict` | competing localities / status conflict | 0024 |

```
disagreement_t = max(available channels)
```

**MAX, never a sum and never a mean.** Summing bounded channels would leave the
unit interval; averaging would let one strong contradiction be diluted by three
quiet agreements. The channels stay **separately readable** in the artefact
under their own names, so a consumer can see *which* disagreement drove the
value (`test_the_disagreement_channels_stay_named_and_separate`).

With no channel measured the signal is **UNAVAILABLE**, not `0.0` — nobody
disagreeing is not the same as nobody being asked.

---

## 22. Channel bounds are refused, not clipped

`DisagreementChannel` raises `CoverageGapError` on a value outside [0, 1] or a
non-finite value. Clipping would hide an upstream contract break behind a
plausible number.

---

## 23. Audit 0027 §20A survives into Module 19

The corrective pass that made key-condition mapping cardinality-aware must not
be undone here. `StructuralOutcome.ALTERNATE_RECOVERED` — a set-valued relation
recovering a *different but co-valid* object — **is not contradiction** and
contributes **nothing** to `disagreement_t`.

Tested across `awardWonBy`, `countryLandBordersCountry` and
`companyTradesAtStockExchange`: the disagreement signal stays **UNAVAILABLE**
and the recovered value is preserved instead under
`raw_diagnostics["alternate_recoveries"]`, where it remains inspectable without
being scored (`test_an_alternate_recovery_never_becomes_disagreement`).

---

## 24. Unresolved mass over ProgramType-specific pools

```
unresolvedMass_t = #(unresolved target units) / #(applicable target units)
```

The target pool is the one the ProgramType actually has:

| ProgramType | Target unit |
| --- | --- |
| `SMALL_SET`, `LARGE_OPEN_SET` | each surviving candidate |
| `NUMERIC` | each numeric cluster |
| `NULL_SINGLE` | the query's existence state, as **one** unit |

A candidate is unresolved when its verifier was not requested, was unavailable,
returned UNKNOWN, or when a check remains pending. Eight distinct
`UnresolvedReason` values are recorded per unit, so the *why* survives.

Hard contract violations are excluded and listed. An empty pool is
**UNAVAILABLE**, never `0.0`.

---

## 25. The three verifier states are distinguished

`VERIFIER_NOT_REQUESTED`, `VERIFIER_UNAVAILABLE` and `VERIFIER_UNKNOWN` are
three different situations and stay three different reasons
(`test_the_three_verifier_states_are_distinguished`). Collapsing them would
make "we never asked" indistinguishable from "we asked and it could not tell".

A pending check marks a unit unresolved **without** contradicting it:
`PENDING_CHECK` is recorded and `STRUCTURAL_CONTRADICTION` is not
(`test_a_pending_check_makes_a_unit_unresolved_without_contradicting_it`).

---

## 26. Numeric relations use cluster stability

Per §15, `NUMERIC` reads Module 12's clusters **as Module 12 produced them**.
M19 does not re-cluster, does not re-tolerance and does not re-aggregate: the
source contains no `cluster_values`, `recluster`, `tolerance`, `median` or
numeric tolerance constant (`test_numeric_uses_module_12s_clusters_untouched`).

Recorded: cluster count, representatives, dispersions, per-cluster independent
support, single-group clusters, competing clusters, and how many clusters carry
verifier or structural evidence.

The **singleton reading for NUMERIC** is the fraction of clusters resting on a
single independent group — the cluster-stability analogue of the set-valued
singleton ratio, and the reason `NumericTargetOverlay` was extended to carry
`independent_support` (§29). A tight, well-supported single cluster yields a
lower `R_t` than two competing single-group clusters
(`test_a_tight_single_cluster_reads_as_more_stable`).

---

## 27. Null-single relations use competing-state uncertainty

Per §15, and constrained by Audit 0024. `NullCompetingStateDiagnostics` records
living support, no-known-locality support, failed-recall operations, substantive
null groups, the `failed_recall_only` flag, competing candidates, status
conflict and gate state.

**One hundred failed recalls do not resolve the existence state.** The test
constructs exactly that and asserts the unit stays unresolved with
`FAILED_RECALL_ONLY` recorded (`test_the_null_single_state_preserves_audit_0024`).
Abstention is a coverage gap, never evidence of emptiness — the defect Audit
0024 corrected in M14, re-pinned here at the layer above.

The persisted record contains no `final_empty`, `accepted_empty`, `gold_empty`
or `is_empty`. M19 describes the uncertainty; it does not resolve it.

A query-level proposition never becomes a candidate: the existence state is one
unit named `query_existence_state`, and `NO_KNOWN_QUALIFYING_LOCALITY` does not
appear in the unit set (`test_a_query_proposition_never_becomes_a_candidate`).

---

## 28. Availability is first-class, and unavailable is never zero

`SignalAvailability` is `AVAILABLE`, `NOT_APPLICABLE` or `UNAVAILABLE`, and
every diagnostic and every component carries it with prose.

`ResidualComponent` refuses the two incoherent shapes: an `AVAILABLE` component
with no value or a value outside [0, 1], and an `UNAVAILABLE` component
carrying a value.

An unavailable component is **dropped and the remaining weights renormalised**,
never read as zero. With one available component at `1.0`, `R_t = 1.0` — not
`0.2` (`test_an_unavailable_component_is_never_zero`). Treating "not measured"
as "measured zero" would systematically report thin queries as well covered,
which is the exact failure mode this module exists to detect.

With **no** available component, `R_t` is `None`,
`effective_weight_mass = 0.0`, and the availability is `UNAVAILABLE`.

---

## 29. Layer-4 change made for M19

`NumericTargetOverlay` gained two copied fields, `dispersion` and
`independent_support`, taken directly from Module 12's cluster record in
`numeric_overlay()`. Without per-cluster independent support the numeric
singleton reading (§26) could not be computed at all.

This is a **carry, not a computation** — Layer 4 copies what M12 already
recorded and derives nothing. Layer 4 is part of the same uncommitted milestone
sequence, and every prior test still passes.

---

## 30. `R_t` arithmetic

Verified exactly:

| Components available | `R_t` |
| --- | --- |
| `{1.0, 0.0, 0.5}` | `0.5` — the mean of three |
| `{1.0}` only | `1.0` |
| all five at `0.0` | `0.0` |
| all five at `1.0` | `1.0` |
| none | `None`, UNAVAILABLE |

`R_t ∈ [0, 1]` is asserted for all six relations. All-zero components produce
`0.0` and **stop nothing** — the payload still contains no stopping vocabulary
(`test_all_zero_components_give_zero_and_stop_nothing`).

---

## 31. Determinism and order invariance

Two estimates over the same state are equal; an estimate over reversed
candidate order produces identical incidence and identical `R_t`. Facets,
candidates, groups and channels are emitted in sorted or recorded order, never
in dictionary-insertion order (`test_the_estimate_is_deterministic_and_order_invariant`).

---

## 32. Configuration

```yaml
coverage_gap:
  enabled: false          # shadow only, off by default
  mode: shadow            # the only accepted mode
  estimator_version: m19-v1
  weights: {novelty_rate: 1, singleton_ratio: 1, facet_gap: 1,
            disagreement: 1, unresolved_mass: 1}
```

Present and **disabled** in all three shipped configs, asserted by test.

Rejected loudly: an unsupported mode (`production`), an unknown key, an
unsupported `estimator_version`, an unknown weight name, a negative weight, and
weights summing to zero. `build_coverage_gap_estimator` refuses to construct an
enabled estimator when Layer 4 is disabled, and the pipeline raises the same
way — M19 estimates *from* the Layer-4 state and cannot be enabled without it
(`test_configuration_failures_are_loud`, `test_the_pipeline_refuses_an_estimator_without_layer4`).

---

## 33. The Phase-C seam

```
Layer4EvidenceState → M19 estimate_coverage_gap(...) → coverage_gap.jsonl
```

The seam sits after Layer-4 integration in `_run_consensus`. It reads the
Layer-4 state, the query's `ProgramType` from the M0/M1 contract, and the
applicable specialist's own execution metadata. It appends to
`coverage_gap_results` and touches nothing else. **Nothing consumes the
result.**

A query whose specialist never ran yields an empty execution map, which is the
honest state: every applicable facet reads `UNEXPLORED`.

---

## 34. Mined Module 11 memory is acquisition, not a facet

Found by the strict undeclared-facet check (§35) on the first end-to-end run,
which failed loudly on `pseudo_memory#0`, `query_rewrite#0` and `self_ask#0`
across all six relations.

A specialist also mines Module 11's parametric sketches, and those observations
carry **Module 11 operation ids**, not facet ids. They are upstream acquisition,
not facets of the specialist's registry. The correct split:

* **novelty** keeps them — they can genuinely name something new;
* the **facet coverage map** excludes them — they are not part of the facet plan
  whose coverage is being described.

Folding them into the facet map would have invented facets no registry declares;
dropping them from novelty would have hidden real discovery. Both halves are
pinned by test (`test_only_the_specialists_own_probes_populate_the_facet_map`).

---

## 35. Failures are loud

`CoverageGapError` is raised, never swallowed, for: a ratio with an empty
denominator, a disagreement channel outside [0, 1] or non-finite, an incoherent
facet record, an incoherent residual component, and recorded execution for a
facet the relation's registry does not declare.

That last check is what caught §34. It is kept strict for exactly that reason.

---

## 36. Artefact

`coverage_gap.jsonl`, one record per query, in **query-manifest order**,
asserted against `query_manifest.json`.

Each record carries `estimator_version`, `layer4_version`, the query identity,
`program_type`, the full facet list with exclusions and reasons, the four facet
roll-ups, incidence, novelty history, disagreement channels, unresolved units,
program-specific diagnostics, the residual with per-component weights and
reasons, `residual_disclaimer`, and `errors`.

`to_json` / `from_json` round-trips exactly. The schema is **stable across
ProgramTypes**: `numeric_stability` and `null_competing_state` are always
present, `null` where not applicable.

No record contains `gold`, `ObjectEntities`, `accepted`, `should_stop`,
`next_action`, `unseen`, `cardinality` or `budget` — scanned with the
disclaimer field excluded, since the disclaimer's own wording denies several of
those words.

Observed on `awardWonBy` under scripted fixtures:

```
estimator_version m19-v1   layer4_version layer4-v1   program_type LARGE_OPEN_SET
weak      9  (seed, 3 temporal, 4 recipient_type, category_dimension)
exhausted 1  (missingness_uncovered)
excluded  1  (geography, DISABLED_BY_POLICY)
R_t 0.9  used [facet_gap]  weight_source uniform_unfitted  errors []
```

The four unavailable components are the honest reading of a fixture runtime
that names nothing: no discovery, so no novelty and no incidence; no verifier
reading, so no disagreement and no unresolved mass.

---

## 37. Shadow invariance

For `awardWonBy`, `countryLandBordersCountry` and `hasCapacity`, the full
staged CLI was run twice — `coverage_gap.enabled: true` and `false` — with
every prior layer enabled.

**All 19 prior artefacts are byte-identical**: `predictions.jsonl`,
`diagnostics.json`, `trace.jsonl`, `stage_a_enumerated.jsonl`,
`stage_b_verified.jsonl`, `calls_enumerate.jsonl`, `calls_verify.jsonl`,
`query_profiles.jsonl`, `prompt_programs.jsonl`, `parametric_memory.jsonl`,
the four specialist artefacts, `atomic_consensus.jsonl`,
`specialist_verification.jsonl`, `bidirectional_verification.jsonl`,
`layer4_evidence.jsonl` and `metrics.json`.

`coverage_gap.jsonl` exists only in the enabled run
(`test_shadow_mode_changes_no_prior_artefact`).

---

## 38. Interpretations recorded rather than resolved silently

**1. `coverage_gap/`, not `coverage/`.** §4 above. The brief's suggested path
would shadow Module 6.

**2. Uniform weights.** §3. The proposal supplies none and no data may be read
to fit them.

**3. `M18_CANDIDATE_FREE_RECALL` is a discovery group.** §15/§18. It is the one
verification-adjacent mechanism not shown its target, and Audit 0026 already
credits it as capable of first discovery. Excluding it would have discarded
real novelty; including any other M18 mechanism would have manufactured it.

**4. Mined M11 memory splits.** §34. Novelty yes, facet map no.

**5. The numeric singleton reading.** §26. §15 says numeric relations use
cluster stability but does not say which quantity fills `singletonRatio`. The
fraction of clusters resting on a single independent group is the direct
structural analogue and reuses M12's own accounting rather than inventing one.

**6. `NULL_SINGLE` has one target unit.** §24/§27. The existence state is a
single query-level unit, so `unresolvedMass` is `0.0` or `1.0` there. Splitting
it into per-candidate units would have re-litigated Audit 0024's finding that
competing localities are uncertainty *about the state*, not separate targets.

---

## 39. Prior audits preserved

| Audit | What M19 had to preserve | Preserved by |
| --- | --- | --- |
| 0006 | canonical origin identity, `q_g = max` | §14 |
| 0008 | F/L/X/C/U semantics; verification is not acquisition | §15, §18 |
| 0012 | M0–M8 untouched | §4, §8 |
| 0022 | `border_direct` disabled by policy | §12 |
| 0023 | `D` stays M16's semantic disagreement | §21 |
| 0024 | abstention/NULL semantics; failed recall ≠ empty | §11, §27 |
| 0025 | template and label-order are bias diagnostics | §21 |
| 0026 | candidate-free recall can discover | §18 |
| 0027 §20A | `ALTERNATE_RECOVERED` is not contradiction | §23 |

---

## 40. Invariance proofs run

* `runtime.calls` identical across the seam.
* `graph.rcse_state` deep-equal across the seam.
* `git status --porcelain src/cover_kbc/coverage.py` empty.
* 19 prior artefacts byte-identical, M19 on vs off, three relations.
* Production prediction still returned and unchanged.

---

## 41. Model budget

`python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`
→ **PASS**, total **28.67B**. Enumerator
`mistralai/Mistral-Small-3.2-24B-Instruct-2506` 24.011B (verified), verifier
`Qwen/Qwen3.5-4B`. M19 adds no model, no checkpoint and no parameter.

---

## 42. Benchmark integrity

`git status --porcelain benchmark/`, `git diff -- benchmark/` and
`git diff --cached -- benchmark/` are all **empty**, asserted by
`test_benchmark_is_untouched` as well as run directly. Upstream pin
`30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` unchanged.

---

## 43. Closed-book compliance

No web, RAG, Wikipedia, Wikidata, KB lookup, vector database, external corpus,
entity linker or external search API. M19 reads only in-process state and makes
no network-capable import (§5).

---

## 44. No learned component

No fine-tuning, LoRA, continued pretraining, learned router, classifier,
calibrator, verifier or scorer. The weights are uniform, unfitted constants
declared in configuration; the module is arithmetic over recorded evidence.

---

## 45. No tuning on TRAIN, VAL or TEST

No split was read to choose any value. The five weights are `1`; there is no
threshold, no window and no smoothing constant to fit. Fixture runs used
`--limit 3` on TRAIN purely to exercise the seam and inspect the artefact, and
nothing observed there was fed back into a constant.

---

## 46. DoLa remains deferred

Not implemented, not referenced. `dola` does not appear in the module.

---

## 47. Modules not implemented

M20 and M21 are not implemented, not stubbed and not referenced. M19 produces
no input either would need beyond the state it already persists.

---

## 48. Defects found and fixed during this milestone

1. **Dead numeric branch.** `_non_set_singleton` was left with an unreachable
   `if False` placeholder. Replaced with the real per-cluster implementation
   (§26).
2. **Layer 4 lacked per-cluster support.** `NumericTargetOverlay` did not carry
   `independent_support`, so the numeric singleton reading was uncomputable.
   Added as a copied field (§29).
3. **M14 `family` is a plain `str`.** The null-temporal projection assumed an
   enum and raised `AttributeError`. Fixed with a `_facet_id` helper that
   accepts either.
4. **Undeclared facets from mined M11 memory.** §34.
5. **Exhaustion evidence on ordinary facets.** §11.
6. Unused import removed.

Four test premises of my own were also wrong and were **rescoped with a stated
reason rather than the code weakened**: `"unseen"` matched the disclaimer's own
"not an estimate of unseen objects", `"fitted"` matched `uniform_unfitted`, the
RCSE import used names that do not exist, and a dotted-path scan on tokenised
source passed vacuously (now an AST check, §4). The scan blob now subtracts the
two constants the brief mandates verbatim, and says why.

---

## 49. Test suite

`tests/test_coverage_gap.py`, **96 tests**, covering the brief's 115 numbered
requirements plus the §9A ownership-isolation set: proposal mapping and
zero-call proofs, the facet registry, its owner modules and the four
states, incidence and singleton ratio, novelty and saturation, the disagreement
reducer, unresolved mass, the numeric and null-single readings, `R_t`
arithmetic and availability, configuration, the seam, shadow invariance and
persistence.

Every subject and object in the fixtures is fictional.

---

## 50. Validation

| Check | Result |
| --- | --- |
| `python -m pytest -q` | **2339 passed, 3 skipped** (2243 before M19) |
| `python -m pyflakes src/ tests/ scripts/` | clean |
| `scripts/audit_model_budget.py` | PASS, 28.67B |
| benchmark integrity (3 git commands) | all empty |
| shadow invariance, 3 relations × 19 artefacts | byte-identical |
| end-to-end, all 6 relations | states produced, 0 neural calls |

---

## 51. Files

New:

* `src/cover_kbc/coverage_gap/__init__.py` (28)
* `src/cover_kbc/coverage_gap/gap_types.py` (788)
* `src/cover_kbc/coverage_gap/facet_coverage.py` (~500)
* `src/cover_kbc/coverage_gap/missingness.py` (766)
* `tests/test_coverage_gap.py` (1535)
* this audit

Modified: `src/cover_kbc/pipeline.py` (seam), `src/cover_kbc/evidence/layer4_types.py`
(§29), `scripts/run_staged.py`, `scripts/run_cover.py`, three experiment configs.

Unmodified and verified: `src/cover_kbc/coverage.py`, `controller.py`,
`selection.py`, `benchmark/`.

**Nothing committed. Nothing pushed.**

---

## 52. Verdict

**PASS**, amended in place per §9A.

Relation → specialist ownership matches Audit 0022 exactly, and the stock facet
map is projected from M15's live registry.

Proposal §15 and §15.1 are implemented exactly: the five-term equation, the
four facet states, the three per-program readings. `R_t` is a heuristic
residual search-need index and is documented, tested and persisted as one.

Module 6's RCSE is byte-for-byte intact and still owns production `q_res`. No
Chao2, no cardinality estimate, no probability, no decision, no budget, no
next-action, no DoLa. Zero neural calls. Disabled by default, shadow only,
consumed by nothing.

Next milestone: **M20**, on a separate authorised brief.
