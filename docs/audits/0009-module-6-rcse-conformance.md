# Audit 0009 — Module 6: Residual Coverage & Saturation Estimator Conformance

Status: **complete — 7 defects found, 7 fixed; 1 blocking Module-7 dependency recorded**
Date: 2026-08-04
Scope: Module 6 only. Modules 7 and 8 remain unreviewed.

---

## 1. Objective and scope

Answer the review's central question:

> Does the current RCSE estimate residual **search need** from the corrected
> COVER evidence state, or is it mostly a heuristic over views/facets that
> ignores Module-5 coverage and uncertainty?

**Answer: it was the latter.** Before this review RCSE consumed *no* Module-5
quantity, counted raw parsed mentions as "verified" yield, had a structurally
dead set-stability signal, conflated views with semantic facets, and had no
numeric-stability or gate diagnostic despite spec table 6 naming both.

In scope: `src/cover_kbc/coverage.py`, the RCSE input production in
`src/cover_kbc/pipeline.py` and `record_outcome` in
`src/cover_kbc/controller.py`, and the staged persistence of temporal state.
Out of scope and untouched: the controller's action scoring and stopping policy
(Module 7), final selection (Module 8).

No model was downloaded or run.

---

## 2. Proposal requirements

| § | Requirement |
|---|---|
| 12.1 | Capture-recapture is removed from the core: "model-generated views are not independent captures", and "a small number of very large/open-ended award rows have necessarily partial gold sets", so "a literal estimate of the true real-world set size is not aligned with the leaderboard objective". |
| 12.1 | `q_res ∈ [0,1]`, "interpreted as **need to continue searching**, not 'probability that exactly n objects remain'." |
| 12.2 | Signals: newly **verified** candidates in recent actions; marginal verified yield per generated token; overlap between recent candidate sets; unresolved-candidate mass; mandatory-facet completion; verifier disagreement; set stability; relation-specific gates. |
| 12.2 | `Y_t(a_t) = #new verified candidates from a_t / (generated tokens(a_t) + ε)`. |
| 12.2 | `Sat_t = 1 − (1/k) Σ 1[ΔV_i > 0]`, where `ΔV_i` is newly **verified** candidates. |
| 12.3 | RCSE "is intentionally relation-specific" — table 6 gives four distinct residual signal sets. |

Table 6, quoted, is the acceptance criterion for §§20-23 below:

| Program | Residual signal |
|---|---|
| SMALL_SET | mandatory views complete + high set overlap + no disputed candidate + low new-candidate yield |
| NULL_SINGLE | existence gate resolved + zero or one stable locality + no competing high-score locality |
| NUMERIC | dominant cluster stable + low relative dispersion + independent semantic/unit views agree |
| LARGE_OPEN_SET | verified-yield decay + facet coverage + missingness queries stop adding trusted objects + low unresolved tail |

---

## 3. Pre-work repository state

Branch `main`, HEAD `ef0d9f7` ("refactor: align COVER-KBC evidence state with
architecture"). Working tree clean; Module-5 work committed as expected. Audits
0001–0008 accepted. 595 tests passing.

---

## 4. Existing RCSE architecture

`estimate_residual(contract, candidates, state, config)` combined six signals
(`marginal_yield`, `saturation`, `unresolved_mass`, `facet_gap`,
`verifier_disagreement`, `set_instability`) through a per-programme weighted
mean. `RCSEState` held `outcomes`, `accepted_history` and a single
`covered_facets` set.

It received neither the graph nor any Module-5 accessor, so gate state, `q(o)`,
`H_inc` and the available/executed/supporting distinction were all unavailable
to it.

---

## 5. Confirmation that Chao / cardinality estimation is not core

A repository-wide search for `chao`, `capture.recapture`, `unseen_count`,
`estimated_cardinality`, `expected_total`, `singleton`, `doubleton`, `species`
over `src/`, `scripts/` and `configs/` returns **only two hits, both in the
module docstring explaining why the estimator was removed**. No executable code
implements or calls one, and no legacy experimental branch remains to be
disabled.

`test_no_cardinality_estimator_drives_the_core` asserts this structurally: it
AST-walks the call graph for the banned names and checks that no component or
diagnostic key contains `cardinality` or `unseen`.

No new true-cardinality estimator was added.

---

## 6. RCSE input schema

```
estimate_residual(
    contract,                 # relation contract (Module 0)
    candidates,               # candidate list (Module 3 graph)
    state: RCSEState,         # temporal action history (this module)
    config: RCSEConfig,       # versioned weights/thresholds
    *,
    gate:    GateState,       # existence-gate read-out (Module 4/pipeline)
    scoring: ScoringConfig,   # Module-5 availability + coverage rule
) -> ResidualEstimate
```

`gate` and `scoring` are new. Both are live: `gate` is the only route to the
`NULL_SINGLE` gate signal spec table 6 requires, and `scoring` carries the
Module-5 availability rule without which `mechanism_gap` cannot tell an
unexplored mechanism from an unavailable one.

No large mutable shared object was introduced, and there is no
`scoring → coverage → scoring` cycle: `coverage` imports from `scoring`, never
the reverse.

---

## 7. Module-5 state integration

Resolves audit 0008 §38.1. RCSE now consumes:

| Module-5 quantity | RCSE consumer |
|---|---|
| `acquisition_groups` (available `m(o)` index set) | `mechanism_gap` |
| `coverage_q` (`q(o)`) | `unresolved_mass` importance weighting |
| `inclusion_uncertainty` (`H_inc`) | `mean_inclusion_uncertainty` component |
| `supporting_acquisition_groups` | numeric cluster weighting |
| `decide_status` | `trusted_keys`, hence yield and stability |

Facet/view reasoning was **not** replaced by `q(o)` — the review warned that
would be equally wrong. Both axes are carried; see §§9-10.

---

## 8. Available / executed / supporting mechanism analysis

| concept | source | RCSE meaning |
|---|---|---|
| **available** | `acquisition_groups(contract, scoring)` | denominator of `mechanism_gap` |
| **executed** | `RCSEState.executed_groups` | numerator; evidence of saturation when it yielded nothing |
| **supporting** | `supporting_acquisition_groups(candidate)` | candidate coverage `q(o)`, feeds `unresolved_mass` |

Neither wrong reading is present, both tested: an unexecuted mechanism is
search need and **not** negative evidence
(`test_an_unexecuted_mechanism_is_search_need_not_contradiction` asserts
`contradiction_count == 0`); an executed mechanism that never mentioned a
candidate is **not** a contradiction against it.

`CROSS_MODEL_RECALL` and `FACTUAL_DECODING` are absent from
`acquisition_groups` by construction (Module 5), so neither leaves a permanent
gap when disabled —
`test_cross_model_and_factual_decoding_never_leave_a_permanent_gap` proves
`mechanism_gap` reaches exactly 0 for all six relations. Cross-model recall is
therefore never required for stopping.

---

## 9. Mandatory-view gap analysis

`mandatory_view_gap` measures **only** `contract.mandatory_views`. Requiring
optional views would destroy adaptive stopping, which is the point of the
controller.

It is applied as a **floor** on `q_res`, not as another weighted term. Two
consequences, both intended:

1. Missing mandatory work cannot be averaged away by a stable candidate set —
   `test_the_mandatory_gap_is_a_floor_that_stability_cannot_hide` builds a
   perfectly stable, fully saturated, fully resolved query and asserts residual
   still ≥ the mandatory gap.
2. A missing mandatory view is not charged twice. It raises the floor; it does
   not also inflate a weighted mechanism term against the same absence.

---

## 10. Semantic / facet gap analysis

Facets are declared on **views**, via `ViewSpec.facet_id`. Measured:

| relation | declared facets |
|---|---|
| `awardWonBy` | `award_category`, `award_enumeration`, `award_exact_identity`, `award_missingness`, `award_recipient_type`, `award_temporal` |
| all five others | none |

So facet search is exactly where the proposal puts it — `LARGE_OPEN_SET` — and
every other relation returns `facet_gap = 0.0` rather than an artificial
permanent gap.

`declared_facets` deliberately excludes views without a `facet_id`: such a view
is the whole mechanism, not a subspace of one, and counting it would make every
relation look facet-partitioned.

The facet axis remains distinct from the mechanism axis —
`test_a_facet_is_not_an_independence_group` asserts the two name sets are
disjoint and that the six award facets all sit inside *fewer* independence
groups than there are facets. Facet coverage is never required to be exhaustive:
`test_award_search_does_not_require_every_facet_before_residual_can_fall`.

---

## 11. Verified-set definition

`trusted_keys(candidates, contract, scoring)` — candidates whose Module-5
`decide_status` is `ACCEPTED`.

"Trusted" deliberately means *accepted by the current evidence state*, not "a
verifier call happened". A candidate found by enough independent mechanisms is
`AUTO_ACCEPT`ed with no Module-4 call, and excluding it would make broad
structural agreement invisible to both yield and stability.
`test_an_auto_accepted_candidate_counts_as_trusted_without_a_verifier_call`
pins this.

Rejected and unresolved candidates are excluded. The set is computed, never
stored, so it cannot drift from the evidence.

---

## 12. Recent-action history schema

`ActionOutcome` records, per action: `action` type, `new_trusted`,
`new_candidates`, `generated_tokens`, `is_verification`, `synthetic_cost`.
`RCSEState` additionally holds `trusted_history` plus three separate execution
registers — `executed_views`, `executed_facets`, `executed_groups`.

Every field has a producer (`record_outcome`), a consumer (a residual signal or
diagnostic) and a test. `synthetic_cost` exists so a scripted run's token cost
is never presented in a trace as a real measurement.

The previous single `covered_facets` register held **view ids**, not facet ids
— the name asserted a distinction the data did not carry. It is now
`executed_views`, with the other two axes as their own registers.

---

## 13. Verified-yield definition — DEFECT 1 (severe, fixed)

### The defect

```python
new_verified = verified + new_candidates
```

where `verified` was the **verifier call count** (`_verify_one` returns
`len(results)`, i.e. **3** for a multi-template adversarial verification of one
candidate) and `new_candidates` was the count of **raw parsed candidate nodes**.

Neither is verified yield. Measured on a scripted borders query, a single view
answering `"Alpha; Beta"` was recorded as `new_verified = 2` although nothing
had been verified or trusted at all.

### The fix

`record_outcome` now derives yield itself, from the **trusted-set delta**:

```python
before = state.last_trusted
after  = frozenset(trusted_keys)
new_trusted = len(after - before)
```

This is the smallest provenance-correct attribution available: the gain is
credited to the action that produced it, and no later verification is
retro-attributed to an earlier action. Verified by unit sequence:

| step | event | `new_trusted` |
|---|---|---|
| 1 | 1 mechanism supports alpha (below acceptance) | 0 |
| 2 | 2nd mechanism → alpha becomes trusted | **1** |
| 3 | 3rd mechanism re-mentions alpha | 0 |
| 4 | 10 raw repeats of one mechanism | 0 |

Excluded from yield, each tested: raw parsed mentions, rejected candidates,
unresolved candidates, repeated mentions of an already-trusted candidate,
verifier call counts.

Verification actions are *not* forced into "generation yield": an action that
resolves an unresolved candidate has `new_trusted = 1`, `new_candidates = 0`,
`is_verification = True`, and counts as productive — so the controller is not
pushed away from verification exactly when verification is what the query needs
(`test_a_verification_action_that_resolves_a_candidate_counts_as_value`).

---

## 14. Marginal-yield / token analysis

`Y_t = new_trusted / (generated_tokens/1000 + ε)`, `ε = 1e-9`, configured as
`yield_epsilon`.

The zero-token case is handled explicitly rather than by dividing by epsilon: a
pure verification round has no token cost, and `found / ε` would report a yield
of order 1e9. It returns a bounded `float(found > 0)` instead, and
`test_zero_token_actions_give_a_finite_bounded_yield` asserts the result stays
in `[0, 1]`.

Token cost comes from the action's actual logged `generated_tokens`. Wall-clock
latency is not used as a substitute anywhere.

---

## 15. Saturation derivation

`Sat_t = 1 − (1/k) Σ 1[ΔV_i > 0]` over the last `k = saturation_window` actions,
with `ΔV_i = new_trusted` — now genuinely "newly verified", which it was not
before (§13).

Empty history returns 0.0. Repeated no-gain actions raise it monotonically to
1.0; a productive action lowers it. `k` is versioned config.

**Saturation alone cannot stop anything.** The residual is floored by the
mandatory gap, so a fully saturated query with an unexecuted mandatory view
still reports `q_res = 1.0`
(`test_saturation_alone_cannot_zero_the_residual`). This is the spec's own
warning made structural: a wrong or incomplete set can be perfectly
unproductive.

The component reported to the controller is `unsaturated = 1 − Sat_t`, so every
`components` entry is oriented "higher = more reason to search". Raw
`saturation` is reported in `diagnostics`, keeping its natural orientation.

---

## 16. Set-overlap / stability analysis — DEFECT 2 (severe, fixed)

### The defect

Stability was Jaccard over `accepted_history`, fed by

```python
accepted = [c.key for c in graph.active_candidates()
            if c.status is CandidateStatus.ACCEPTED]
```

but `candidate.status` is **never set to `ACCEPTED` during discovery** — only
`decide_status` at Phase C does that, and hard rules only ever set `REJECTED`.
So `accepted` was empty on every action, `accepted_history` was a list of empty
sets, and the old `set_stability()` returned **1.0** for two empty sets. The
signal reported maximal stability for a query that had found nothing, on every
query. Confirmed empirically before the fix.

### The fix

`trusted_history` is fed from `trusted_keys(...)`, computed after scoring and
tiering the live candidates. Two *empty* trusted sets now return **0.0**, not
1.0: there is nothing to agree about, and calling it agreement would let a query
that found nothing look settled
(`test_two_empty_trusted_sets_are_not_stable_agreement`).

Fewer than two observations returns 0.0. Identity is the Module-3 strict
candidate key, so aliases and numeric nodes behave as they do everywhere else.

Stability alone never completes a query:
`test_stability_alone_cannot_complete_a_query_with_unresolved_candidates`
holds stability at 1.0 and shows residual still rises with a disputed candidate.

---

## 17. Unresolved-mass analysis

Previously `count(unresolved) / count(active)` — every candidate equal. Now each
unresolved candidate is weighted by its Module-5 coverage `q(o)`, with a 0.25
floor:

```python
total += max(0.25, coverage_q(candidate, contract, config))
```

A candidate several independent mechanisms found and the architecture still
cannot resolve is exactly what another action should be spent on; a
single-mechanism hallucinated tail presses proportionally less but never
vanishes. Tested in both directions, plus that rejected candidates contribute
nothing.

Deterministic, evidence-state only. No factual lookup, no classifier.

---

## 18. Verifier-uncertainty analysis

`U_prompt` (max prompt disagreement) enters as `verifier_disagreement`.
`H_inc` enters separately as `mean_inclusion_uncertainty`, normalised by
`log 2` to `[0, 1]`. `H_ver` remains Module 4's, stored on the verification
result and summed into neither.

`test_inclusion_uncertainty_disagreement_and_entropy_stay_separate` asserts the
three take different values on one candidate and that none overwrites another —
the Module-5 §15 separation surviving into Module 6.

---

## 19. q(o) vs q_res distinction

Different quantities, and the code cannot alias them: `q(o)` is
`coverage_q(candidate, ...)` in Module 5; `q_res` is the return of
`estimate_residual(query-level)` here. The residual carries no `q_coverage`
component.

`test_q_res_is_not_candidate_coverage_q_of_o` constructs the decisive case: a
candidate found by *every* eligible mechanism (`q(o) = 1`) in a query that has
run *no* views, and asserts `q_res = 1.0`. Maximal candidate coverage, maximal
search need.

The converse is tested too: one low-coverage rejected candidate does not force
high residual in an otherwise settled query.

---

## 20. SMALL_SET residual

Signals: `mechanism_gap`, `unresolved_mass`, `set_instability`,
`marginal_yield` (half weight), `inclusion_uncertainty`. No blocking floor
beyond mandatory — these relations must be able to finish cheaply.

| scenario | `q_res` | vs stop threshold 0.25 |
|---|---|---|
| mandatory complete, set stable, nothing unresolved, no recent yield | **0.135** | may stop ✓ |
| the same, plus one disputed candidate | **0.178** | rises ✓ |
| the same, minus one mandatory view | ≥ mandatory gap | stays high ✓ |

Also asserted: a settled small set finishes at lower residual than a still
yielding award query.

---

## 21. NULL_SINGLE residual

Signals: `gate_unresolved`, `locality_competition`, `unresolved_mass`,
`mechanism_gap`, `verifier_disagreement`. **Blocking:** `gate_unresolved` and
`locality_competition`.

| scenario | `q_res` | required behaviour |
|---|---|---|
| A. confident negative gate | **0.000** | may stop ✓ |
| B. uncertain gate | **1.000** | must continue ✓ |
| C. one stable locality, no rival | **0.000** | may stop ✓ |
| D. two plausible competing localities | **0.944** | must stay high ✓ |
| E. zero candidates, gate unresolved | **1.000** | ≠ confident negative ✓ |

Case E is asserted distinct from case A: "nothing was generated" is not
"confidently empty". Module 6 does not decide the empty reason — that is
Module 8's.

---

## 22. NUMERIC residual

Signals: `numeric_dispersion`, `cluster_competition`, `mechanism_gap`,
`verifier_disagreement`. **Blocking:** `cluster_competition`.

| scenario | `q_res` | required behaviour |
|---|---|---|
| dominant cluster stable, low dispersion | **0.015** | may stop ✓ |
| a genuine rival cluster | **1.000** | must continue ✓ |
| high relative dispersion | rises with rMAD ✓ | |

---

## 23. LARGE_OPEN_SET residual

Signals: `marginal_yield`, `facet_gap`, `mechanism_gap`, `unresolved_mass`
(half weight), `unsaturated`. No blocking floor — awards stop on the *balance*
of yield decay and coverage, never on one signal.

| scenario | `q_res` | required behaviour |
|---|---|---|
| recent facet actions still adding trusted recipients | **0.476** | stay high ✓ |
| several no-gain actions, facets covered, low unresolved tail | **0.000** | may stop ✓ |
| unresolved high-score tail remains | rises above the settled case ✓ | |

No theoretical true cardinality is chased; partial gold is respected by making
saturation the permission to stop rather than an estimated set size.

---

## 24. Numeric stability diagnostic

`numeric_stability()` returns `dominant_support`, `dispersion` (relative MAD),
`competitor_ratio` and `num_clusters`.

It reuses the **existing pure primitive** `cluster_values` from
`normalization/numeric.py` — the same function Module 8's selector uses — so the
two can never disagree about what a cluster is, and no second definition of
"cluster" is maintained. Module 8's selection behaviour is unchanged; nothing in
`selection.py` was touched.

RCSE emits **no** representative value: `test_numeric_diagnostics_do_not_select_the_final_answer`
asserts no component or diagnostic equals any candidate's value, and that
`numeric_stability`'s source never mentions `representative`. Values are weighted
by independent acquisition mechanisms (Module 5), not by raw mention count.

No official val gold is read anywhere.

---

## 25. Cross-model / DoLa optional-branch treatment

Neither is in `acquisition_groups`, so neither can leave a permanent gap. Proven
for all six relations: with every available mechanism executed,
`mechanism_gap == 0.0` exactly.

Cross-model recall is therefore never required for stopping. When enabled and
unexecuted it remains a possible high-value action for Module 7 to schedule; its
yield, once executed, flows through the ordinary trusted-set delta. Shown-candidate
verification is never counted as cross-model recall — that separation is Module
5's and is inherited unchanged.

DoLa was not implemented and is not waited on.

---

## 26. RCSE output / breakdown schema

`ResidualEstimate` exposes `residual`, `components`, `weights`, `diagnostics`,
`program_type`, `rationale`, `reasons`.

**Components** — all `[0, 1]`, all oriented *higher = more reason to search*:

| component | meaning |
|---|---|
| `mandatory_gap` | required views not run |
| `mechanism_gap` | available acquisition mechanisms unexplored |
| `facet_gap` | declared semantic facets uncovered |
| `unresolved_mass` | coverage-weighted unresolved pressure |
| `marginal_yield` | recent trusted yield per token, scaled |
| `unsaturated` | `1 − Sat_t` |
| `set_instability` | `1 − J_t` |
| `verifier_disagreement` | `U_prompt` |
| `inclusion_uncertainty` | mean `H_inc`, normalised |
| `gate_unresolved` | NULL_SINGLE only |
| `locality_competition` | NULL_SINGLE only |
| `numeric_dispersion` | NUMERIC only |
| `cluster_competition` | NUMERIC only |

**Diagnostics** — natural orientation, reported separately so no reader has to
guess which way a number points: `saturation`, `set_stability`,
`raw_marginal_yield_per_1k_tokens`, `consecutive_no_gain`, `num_actions`,
`trusted_set_size`, plus `dominant_cluster_support` / `relative_mad` /
`num_clusters` for numeric and `gate_present` / `gate_negative` for null-single.

**Reasons** — deterministic codes, never free text: `mandatory_views_incomplete`,
`residual_floored_by_mandatory_gap`, `acquisition_mechanism_unexplored`,
`semantic_facet_unexplored`, `unresolved_candidates_remain`,
`recent_actions_still_yielding`, `recent_actions_saturated`,
`verifier_templates_disagree`, `existence_gate_unresolved`,
`competing_localities`, `competing_numeric_clusters`,
`numeric_cluster_dispersed`, `state_settled`.

---

## 27. q_res combination rule — DEFECT 3 (severe, fixed)

### The defect

A pure per-programme weighted mean **dilutes a decisive signal against its
zero-valued siblings**. Two competing death localities scored
`locality_competition = 0.944`, but averaged against four zero-valued terms:

```
0.944 × 1.0 / (1.2 + 1.0 + 0.8 + 0.8 + 0.6) = 0.215
```

0.215 is *below* the 0.25 stop threshold — the controller would have been told a
contested `NULL_SINGLE` query was settled, directly contradicting spec table 6.

### The fix

Floors, not averages, for signals that are on their own a sufficient reason to
keep searching:

```python
floors = [mandatory_gap, *(components[name] for name in blocking)]
q_res  = clamp(max(weighted_mean, *floors))
```

`blocking` is **typed**, because what makes a query unfinished differs by
programme:

| programme | blocking signals | why |
|---|---|---|
| SMALL_SET | — | must finish cheaply; stops on balance |
| NULL_SINGLE | `gate_unresolved`, `locality_competition` | table 6: gate resolved, no competing locality |
| NUMERIC | `cluster_competition` | table 6: dominant cluster stable |
| LARGE_OPEN_SET | — | stops on the balance of yield decay and coverage |

`test_a_decisive_signal_is_not_diluted_by_its_zero_valued_siblings` recomputes
the diluted value, asserts it *would* fall below the threshold, and asserts the
actual residual does not.

Requirements from the review's §29, all held: more unresolved uncertainty never
reduces `q_res`; missing mandatory coverage never reduces it; continued
productive yield never reduces it; higher saturation tends to reduce it;
`q_res ∈ [0, 1]` always.

---

## 28. Coefficient / threshold inventory

| constant | default | config path | consumer | judgement call? | train calibration required? |
|---|---|---|---|---|---|
| `saturation_window` | 3 | `pipeline.controller.rcse` | `Sat_t`, `Y_t` | yes | yes |
| `yield_epsilon` | 1e-9 | ″ | `Y_t` denominator | no — numerical guard | no |
| `yield_scale` | 2.0 | ″ | yield normalisation | yes | yes |
| `numeric_dispersion_threshold` | 0.05 | ″ | `numeric_dispersion` | yes | yes |
| `competitor_support_ratio` | 0.5 | ″ | `cluster_competition` | yes | yes |
| `w_yield` | 1.0 | ″ | weighted mean | yes | yes |
| `w_saturation` | 1.0 | ″ | ″ | yes | yes |
| `w_unresolved` | 0.8 | ″ | ″ | yes | yes |
| `w_facet_gap` | 1.0 | ″ | ″ | yes | yes |
| `w_mechanism_gap` | 0.8 | ″ | ″ | yes | yes |
| `w_disagreement` | 0.6 | ″ | ″ | yes | yes |
| `w_instability` | 0.8 | ″ | ″ | yes | yes |
| `w_inclusion` | 0.5 | ″ | ″ | yes | yes |
| `w_gate` | 1.2 | ″ | ″ | yes | yes |
| `w_competition` | 1.0 | ″ | ″ | yes | yes |
| `w_dispersion` | 1.0 | ″ | ″ | yes | yes |
| `stop_threshold` | 0.25 | ″ | read by **Module 7** | yes | yes |

Four constants are new (`yield_epsilon`, `yield_scale`,
`numeric_dispersion_threshold`, `competitor_support_ratio`) plus four weights
for the new components. `test_every_tunable_constant_comes_from_config`
AST-checks that `estimate_residual`'s body contains no float literal other than
the structural `0.0`/`1.0` bounds and the documented `0.5` tail de-weighting.

No constant was tuned during this review, and none was tuned on val.

---

## 29. Monotonicity / property analysis

Isolated per component, as the review requires:

| property | test |
|---|---|
| completing a mandatory view never raises `mandatory_gap` | ✓ |
| executing a mechanism never raises `mechanism_gap` | ✓ |
| covering an award facet never raises `facet_gap` | ✓ |
| a fruitless action never lowers saturation | ✓ |
| a productive action lowers saturation | ✓ |
| adding an unresolved candidate never lowers residual | ✓ (small-set, large-open-set) |
| higher verifier disagreement raises residual | ✓ |
| a stabilising numeric cluster never raises numeric residual | ✓ |
| resolving NULL_SINGLE competition never raises its residual | ✓ |
| cheaper action beats dearer for the same yield | ✓ |

---

## 30. Staged persistence analysis — DEFECT 4 (fixed)

RCSE's temporal state was **not persisted**. Yield and saturation record *when*
something was found and *at what cost*; the final graph records only *what* was
found, so Phase C could not have recomputed them — and reconstructing them from
the final graph would have fabricated history.

Fixed with the minimum persistence: `EvidenceGraph.rcse_state`, written at the
end of Phase A, serialised by `RCSEState.to_json()` / `from_json()`, carried in
the stage file. `STAGE_FILE_VERSION` bumped 3 → 4, so an old stage file fails
loudly rather than silently losing history.

`test_rcse_state_survives_a_staged_round_trip` drives Phase A, persists,
reloads, computes the residual, round-trips again and asserts the two
`ResidualEstimate.to_json()` payloads are equal.

Phase C requires no model to compute `q_res`: `coverage.py` imports nothing
neural, and the whole residual is deterministic arithmetic over persisted state.

---

## 31. Mismatches found

| # | Severity | Description |
|---|---|---|
| 1 | **severe** | Verified yield counted raw parsed mentions plus verifier *call counts*; a multi-template verification of one candidate scored 3 (§13) |
| 2 | **severe** | Set stability was structurally dead — `status` is never `ACCEPTED` during discovery, so every observation was the empty set, and two empty sets returned stability **1.0** (§16) |
| 3 | **severe** | The weighted mean diluted decisive typed signals below the stop threshold; two competing localities read as settled (§27) |
| 4 | moderate | RCSE temporal state was not persisted, so staged Phase C could not recompute `q_res` (§30) |
| 5 | moderate | `facet_gap` was a *view* gap misnamed as a facet gap, and required every **optional** view to execute before residual could fall — destroying adaptive stopping (§10) |
| 6 | moderate | No Module-5 integration at all: no `q(o)`, no `H_inc`, no available/executed/supporting distinction (§7) |
| 7 | moderate | `NUMERIC` had no cluster-stability or dispersion signal and `NULL_SINGLE` had no gate signal, though spec table 6 names both (§§21-22) |

Also fixed in passing: `ResidualEstimate.should_continue` returned
`residual >= 0.0`, i.e. **always True** — a dead property that also implied RCSE
made stopping decisions. Removed; `test_rcse_neither_chooses_actions_nor_emits_predictions`
asserts it and any `stop_*` callable are absent.

---

## 32. Fixes made

1. `coverage.py` rewritten around three separate coverage axes plus Module-5
   consumption (defects 5, 6).
2. `trusted_keys()` added; `ActionOutcome.new_verified` → `new_trusted`, derived
   from the trusted-set delta in `record_outcome` (defects 1, 2).
3. `RCSEState.covered_facets` split into `executed_views`, `executed_facets`,
   `executed_groups` (defect 5).
4. `set_stability` returns 0.0 for two empty sets (defect 2).
5. Typed blocking floors added to the combination rule (defect 3).
6. `GateState` and `numeric_stability` added; `NULL_SINGLE` and `NUMERIC`
   residuals rewritten to spec table 6 (defect 7).
7. `EvidenceGraph.rcse_state` + stage schema v4 (defect 4).
8. `should_continue` removed.

`controller.py` changed only where it *produces* RCSE input (`record_outcome`,
the `executed_views` rename, threading `gate`/`scoring` into
`estimate_residual`). Its action scoring, legality rules and stopping policy are
untouched. `selection.py` was not modified.

---

## 33. Before/after synthetic scenarios

**Verified yield**, scripted borders query, one view answering `"Alpha; Beta"`:

| | recorded yield |
|---|---|
| before | `new_verified = 2` (two raw mentions, nothing trusted) |
| after | `new_trusted = 0` (nothing crossed into the trusted set) |

**Set stability**, two consecutive observations during discovery:

| | `set_stability()` |
|---|---|
| before | **1.0** — two empty `accepted` sets read as perfect agreement |
| after | 0.0 — nothing found twice is not agreement |

**Competing death localities** (scores 0.90 vs 0.85):

| | `q_res` | controller reads |
|---|---|---|
| before (weighted mean) | 0.215 | *stop* — wrong |
| after (blocking floor) | 0.944 | continue |

**Full typed scenario matrix** in §§20-23.

---

## 34. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/coverage.py` | rewritten — Module 6 (+596/−99) |
| `src/cover_kbc/controller.py` | modified — `record_outcome`, register rename, residual inputs |
| `src/cover_kbc/pipeline.py` | modified — trusted-set yield, gate/facet/group registration, state persistence |
| `src/cover_kbc/evidence/graph.py` | modified — `rcse_state` field |
| `src/cover_kbc/staging.py` | modified — persist `rcse_state`, schema v4 |
| `tests/test_rcse_conformance.py` | **created** — 85 tests + 1 xfail |
| `tests/test_controller.py` | modified — renamed API, helper covers all three axes |
| `tests/test_elicitation.py`, `tests/test_staging.py`, `tests/test_evidence_state_conformance.py`, `tests/test_pipeline.py` | modified — renamed API |
| `docs/audits/0009-module-6-rcse-conformance.md` | **created** — this file |

`benchmark/` untouched.

---

## 35. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_rcse_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/
git diff -- benchmark/
git diff --cached -- benchmark/
git diff --stat
```

No model download, no heavyweight inference.

---

## 36. Exact tests / results

**680 passed, 1 xfailed, 0 failed** (up from 595).

| File | Tests |
|---|---|
| `tests/test_contracts.py` | 44 |
| `tests/test_controller.py` | 32 |
| `tests/test_data.py` | 26 |
| `tests/test_elicitation.py` | 63 |
| `tests/test_evaluation.py` | 13 |
| `tests/test_evidence.py` | 23 |
| `tests/test_evidence_state_conformance.py` | 72 |
| `tests/test_graph.py` | 59 |
| `tests/test_normalization.py` | 59 |
| `tests/test_pipeline.py` | 31 |
| `tests/test_programs.py` | 40 |
| `tests/test_rcse_conformance.py` | **85 + 1 xfail** |
| `tests/test_staging.py` | 17 |
| `tests/test_verification.py` | 40 |
| `tests/test_verifier_conformance.py` | 76 |

`pyflakes`: clean apart from four intentional `import _bootstrap` sys.path shims
in `scripts/`.

The single `xfail(strict=True)` is deliberate — see §41. It documents an
invariant Module 7 must satisfy, and will flip to a failure (alerting the next
reviewer) the moment Module 7 is corrected.

Pre-existing tests changed only where the RCSE API was renamed, plus one helper
(`_covered`) that now populates all three execution registers rather than only
views — which is the point of the separation, not a weakening.

---

## 37. Benchmark integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

---

## 38. Challenge-compliance impact

| constraint | status |
|---|---|
| No learned RCSE | ✓ `test_no_learned_model_or_retrieval_exists_in_module_6` AST-walks for `sklearn`/`torch`/`scipy`/`numpy`/`xgboost` imports and `fit`/`partial_fit`/`train`/`backward`/`step` calls |
| No regression/classifier/RL/trained stopping predictor | ✓ none exists |
| No retrieval / factual lookup | ✓ same test covers `requests`/`urllib`/`httpx`/`wikipedia`/`wikidata` |
| No cardinality estimator | ✓ §5 |
| Deterministic | ✓ `test_rcse_is_deterministic` computes the residual three times and asserts identical components and reasons |
| Small parameter surface | ✓ §28 |
| Parameter budget | unchanged — Module 6 is non-neural |

---

## 39. Constants requiring later train calibration

Everything in §28 marked "yes" — seventeen constants, of which four are
thresholds and eleven are weights. They are architecture defaults, not
measurements. Calibrate on train or a documented internal split, freeze, then
evaluate val once.

`stop_threshold` in particular is read by Module 7 and interacts with the typed
blocking floors; it should be calibrated after Module 7 is reviewed, not before.

---

## 40. Unresolved Module-6-only issues

1. **`yield_scale = 2.0` is a judgement call.** It sets what counts as "high"
   verified yield per 1k tokens. With real Colab token counts the appropriate
   scale is an empirical question; the current value is a placeholder that makes
   the signal usable, not a measurement.
2. **The zero-token yield fallback is bounded but coarse.** A verification-only
   window reports `float(found > 0)` rather than a rate. That is deliberately
   conservative — the alternative fabricates a rate — but it means yield cannot
   distinguish one resolved candidate from three in a token-free window.
3. **Facet coverage exists only for `awardWonBy`.** That matches the current
   view library, but if another relation later declares facets, its residual
   weights have not been considered.

None blocks Module 7.

---

## 41. Blocking dependency notes for Module 7

**`REVERSE_ALTERNATE` is counted available but is never schedulable.**

Module 5 (accepted, audit 0008) treats every declared optional family as
*available* under the active-controller configuration, so `REVERSE_ALTERNATE`
enters `m(o)`. But `legal_actions()` skips candidate-conditioned reverse views:

```python
if get_view(contract.relation, view_id).is_reverse:
    continue
```

Measured by exhaustively executing every action the controller will ever offer:

| relation | available `m(o)` | reachable | irreducible `mechanism_gap` |
|---|---|---|---|
| `awardWonBy` | 5 | 4 | **0.200** |
| `companyTradesAtStockExchange` | 4 | 3 | **0.250** |
| `countryLandBordersCountry` | 6 | 5 | **0.167** |
| `hasArea` / `hasCapacity` / `personHasCityOfDeath` | 3 | 3 | 0.000 |

So three of six relations carry a permanent residual floor that no amount of
searching can close, and their `q(o)` can never reach 1.

**Not fixed here** — scheduling candidate-conditioned reverse views is Module 7's
(the review's §32 forbids it, and audit 0005 §22 already flagged the scheduling
gap). Recorded as `test_every_available_mechanism_is_reachable_by_some_legal_action`,
marked `xfail(strict=True)` so it turns into a hard failure the moment Module 7
makes reverse views schedulable — at which point the Module-5/6 availability
invariants should be re-run.

Also carried forward, unchanged, from earlier audits:

- **Module 7**: the calibrated gate's model identity differs between staged and
  interleaved runs (audit 0007 §34.3).
- **Module 7**: `RESAMPLE` is never enumerated; `RUN_VIEW` carries a hard-coded
  `+0.5` mandatory bonus inside `action_score`.

---

## 42. Future Module-8 notes

Carried forward unchanged from audits 0007 and 0008; nothing new was found:

- `EmptyReason.CANDIDATE_REJECTED` is unreachable (audit 0007 §34.2).
- `selection.py` sorts and weights numeric clusters on the **raw** support count
  rather than acquisition support (audit 0008 §38.4).

Module 8's numeric selection was deliberately not touched. RCSE reuses the same
pure `cluster_values` primitive rather than defining a second notion of cluster,
so correcting Module 8 later cannot desynchronise the two.

---

## 43. Modules 7-8 remain unreviewed

Modules 7 (Active Controller and Adaptive Stopping) and 8 (Final Selection) have
**not** been reviewed against the proposal. Their code exists and their tests
pass, but no conformance judgement has been made about them. The notes in §§41-42
are observations made while reviewing Module 6; they are not a review of those
modules and are not exhaustive.

---

## 44. Recommended next review

**Module 7 — Active Controller and Adaptive Stopping.**

It is the sole consumer of `q_res`, it owns the stopping decision RCSE
deliberately does not make, and §41 records a confirmed reachability defect
sitting in its code that currently caps Module 5's and Module 6's coverage
signals.

---

## Verdict

**Module 6 PASSES** after seven defects were found and fixed.

`q_res` now measures residual *search need* and is computed from the corrected
Module-5 evidence state rather than from a view heuristic. Verified yield counts
candidates crossing into the trusted set — not raw mentions, not verifier calls
— and the previously dead set-stability signal is live and correctly
conservative about empty sets. The three coverage axes stay distinct, mandatory
work is a floor that stability cannot hide, and each of the four typed programmes
has its own signal set matching spec table 6, including the numeric cluster
diagnostic and null-single gate semantics that were entirely absent. RCSE
chooses no actions, emits no predictions, trains nothing, and its temporal state
survives the staged seam.

One reachability defect in Module 7 currently caps the mechanism-coverage signal
for three relations; it is recorded, tested as a strict xfail, and left for the
module that owns it.
