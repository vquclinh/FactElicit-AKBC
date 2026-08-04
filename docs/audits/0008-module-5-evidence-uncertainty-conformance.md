# Audit 0008 — Module 5: Evidence and Uncertainty State Conformance

Status: **complete — 5 defects found, 5 fixed; 2 findings deferred**
Date: 2026-08-04
Scope: Module 5 only. Modules 6, 7, 8 remain unreviewed.

---

## 1. Objective and scope

Answer the review's central question precisely:

> Does Module 5 faithfully separate independent acquisition support, calibrated
> verifier evidence, heterogeneous cross-model support, contradictions and
> uncertainty — or are the same evidence events accidentally counted multiple
> times?

**Answer: they were counted multiple times. Three mechanisms were each paid in
more than one component, and two more contributed to `F(o)` that should never
have been able to.** All are now separated by index set.

In scope: `src/cover_kbc/scoring.py` (the whole of Module 5), the availability
rule in `PipelineConfig`, and the seams where `Candidate` evidence becomes
`F/L/X/C/U`. Out of scope and untouched: RCSE (`coverage.py`, Module 6), the
controller (`controller.py`, Module 7), final selection (`selection.py`, Module
8). Findings in those are recorded in §38, not fixed.

No model was downloaded or run.

---

## 2. Proposal requirements

Spec section 11, quoted:

| § | Requirement |
|---|---|
| 11 | "The controller should not make decisions from a single scalar confidence. The state combines multiple signals." |
| 11.1 | "Let `g(o)` be the number of eligible independent evidence groups that support candidate `o`, and `m(o)` the number of groups **capable of expressing that candidate**." `q(o) = g(o)/m(o)`. |
| 11.1 | `H_inc(o) = −q log q − (1−q) log(1−q)`. |
| 11.2 | `S(o) = αF(o) + βL(o) + γX(o) − δC(o) − ηU(o)` with `F` = independent facet/view support, `L` = calibrated log-odds from the verifier, `X` = **optional cross-model support**, `C` = explicit contradiction count/strength, `U` = prompt/view disagreement. |
| 11.2 | `L(o) = clip(log((P_V + ε)/(P_I + P_U + ε)))`. |
| 11.2 | "Hard contract violations bypass the score and reject the candidate." |
| 11.3 | "The first implementation must not train a classifier on graph features." Weights are human-designed, few, to avoid overfitting ten-example relations. |

The decisive structural reading: **`F` and `X` are named as separate terms, and
`L` is the verifier's own term.** Nothing in the proposal asks for one mechanism
to appear in two of them.

---

## 3. Pre-work repository state

Branch `main`, HEAD `10f5e4c` ("refactor: align COVER-KBC blind verifier with
architecture"). Working tree clean; Module-4 work committed as expected. Audits
0001–0007 accepted. 523 tests passing.

---

## 4. Existing Module-5 architecture

Module 5 lives in `src/cover_kbc/scoring.py`. Before this review it provided
`support_term`, `logit_term`, `cross_model_term`, `contradiction_term`,
`disagreement_term`, `score_candidate`, `assign_tier`, `verification_targets`,
`decide_status`, `resolve_verification`.

Two spec quantities were **entirely absent**: `H_inc(o)` did not exist anywhere
in the codebase, and `q(o)` existed only as `Candidate.coverage()`, which
nothing called.

---

## 5. Evidence-type → score-component accounting matrix

Measured, not asserted, by scoring a candidate carrying exactly one evidence
event of each type (`countryLandBordersCountry`, `m(o) = 6`).

### Before

| evidence event | F | L | X | C | U |
|---|---|---|---|---|---|
| acquisition SUPPORT (`DIRECT_RECALL`) | 0.167 | 0 | 0 | 0 | 0 |
| `CROSS_MODEL_RECALL` SUPPORT | **0.167** | 0 | 1.000 | 0 | 0 |
| `BLIND_VERIFIER` VALID (shown) | **0.167** | 0.732 | **0.250** | 0 | 0 |
| `BLIND_VERIFIER` INVALID | 0 | −0.981 | 0 | 0.167 | 0 |
| `BLIND_VERIFIER` INVALID ×3 | 0 | −0.981 | 0 | **0.500** | 0 |
| `EXISTENCE_GATE` SUPPORT | **0.167** | 0 | 0 | 0 | 0 |
| `FACTUAL_DECODING` SUPPORT (branch disabled) | **0.167** | 0 | 0 | 0 | 0 |
| `DIRECT_RECALL` ×10 repeats | 0.167 | 0 | 0 | 0 | 0 |

Five rows are wrong. One shown-candidate verifier VALID was paid **three
times** — `F`, `L` and `X`.

### After

| evidence event | F | L | X | C | U | intentional |
|---|---|---|---|---|---|---|
| acquisition SUPPORT | 0.167 | 0 | 0 | 0 | 0 | yes — this *is* `F` |
| `CROSS_MODEL_RECALL` SUPPORT | 0 | 0 | 1.000 | 0 | 0 | yes — §11.2 gives it `X` |
| `BLIND_VERIFIER` VALID (shown) | 0 | 0.732 | 0 | 0 | 0 | yes — §11.2 gives it `L` |
| `BLIND_VERIFIER` INVALID | 0 | −0.981 | 0 | 0.143 | 0 | yes — explicit signed conflict |
| `BLIND_VERIFIER` INVALID ×3 | 0 | −0.981 | 0 | 0.143 | 0 | yes — one mechanism, not three |
| `EXISTENCE_GATE` SUPPORT | 0 | 0 | 0 | 0 | 0 | yes — a verdict, not a candidate |
| `FACTUAL_DECODING` SUPPORT | 0 | 0 | 0 | 0 | 0 | yes — branch unavailable |
| `DIRECT_RECALL` ×10 repeats | 0.167 | 0 | 0 | 0 | 0 | yes — one group |
| prompt disagreement 0.4 | 0 | — | 0 | 0 | 0.400 | yes — §11.2 `U` |

Test: `test_one_event_never_earns_duplicate_credit` sweeps **every** member of
`IndependenceGroup` and asserts each touches at most one component, so a new
group added later cannot silently land in two.

---

## 6. F(o) definition and eligible groups

`F(o) = q(o)`, computed over `acquisition_groups(contract, config)` only.

Eligible (contribute to `F`): `DIRECT_RECALL`, `STRUCTURAL_DECOMPOSITION`,
`RELATION_FOCUSED_DESCRIPTION`, `CONTRASTIVE_SEPARATION`, `MISSINGNESS_SEARCH`,
`REVERSE_ALTERNATE` — whichever of these the relation declares.

Excluded, by construction rather than by a filter list:
`contract.eligible_independence_groups` is built by `eligible_groups_for`, which
maps only candidate-acquisition **view families**. `EXISTENCE_GATE`,
`BLIND_VERIFIER`, `CROSS_MODEL_RECALL` and `FACTUAL_DECODING` have no view
family in that mapping, so they cannot enter `m(o)`, and `g(o)` is drawn from
`m(o)`, so they cannot enter `g(o)` either.

This is the "impossible by construction" the review asked for: adding a group to
the enum is not enough to give it `F` credit; it would need a view family and a
contract declaration.

---

## 7. g(o), m(o), q(o) semantics

```python
acquisition_groups(contract, config)             -> m(o), an index set
supporting_acquisition_groups(candidate, ...)    -> g(o), a subset of m(o)
coverage_q(candidate, contract, config)          -> |g| / |m|
```

`g(o)` is computed **by iterating `m(o)`**, so `0 ≤ g(o) ≤ m(o)` is a property
of the code's shape, not an invariant that must be checked. `q(o) ∈ [0, 1]`
follows.

The old `min(1.0, ...)` clamp is gone from `F`. It was not a safety net — it was
hiding the index-set mismatch, saturating silently at `g = 6, m = 4`.

Test `test_g_never_exceeds_m_for_any_evidence` is parametrized over all six
relations and saturates the candidate with **every** `IndependenceGroup`,
legal or not, then asserts `g ≤ m`. `test_q_reaches_one_without_a_clamp_hiding_it`
asserts `q = 1` genuinely means every eligible mechanism found it, and that
piling on verifier and cross-model evidence cannot push it past 1.

---

## 8. Available / enabled / executed / supporting group analysis

Four distinct concepts, kept distinct:

| concept | where it lives | used for |
|---|---|---|
| **declared** candidate-producing groups | `contract.eligible_independence_groups` | the catalogue |
| **available** under this run configuration | `acquisition_groups(contract, config)` | **`m(o)`** |
| **executed** so far in this query | `graph.executed_independence_groups()` (Module 3) | diagnostics, Module 6 |
| **supporting** this candidate | `supporting_acquisition_groups(...)` | **`g(o)`** |

The review warned specifically against collapsing `m(o)` to *executed*. That
trap is closed and tested: `test_an_unexecuted_but_available_family_stays_in_the_denominator`
asserts a candidate found by one direct view has `q = 1/6`, not `1.0`, and
`test_the_active_controller_denominator_does_not_collapse_to_executed_only`
asserts the controller run keeps the full denominator.

Availability is derived from the run mode, not hand-set, in
`PipelineConfig.__post_init__`:

```python
available = self.enable_active_controller or self.run_optional_views
```

The active controller may schedule any declared view, so everything is
available; a fixed run with `run_optional_views` executes them all; only a fixed
mandatory-only run cannot reach the optional families. The derived value is
propagated into `selection.scoring` as well, or Phase C would score against a
different denominator than Phase A.

### Denominator matrix — `m(o)` by relation and run mode

| relation | declared | active controller (target) | fixed multi-view ablation | fixed mandatory-only |
|---|---|---|---|---|
| `awardWonBy` | 5 | 5 | 5 | 3 |
| `companyTradesAtStockExchange` | 4 | 4 | 4 | 1 |
| `countryLandBordersCountry` | 6 | 6 | 6 | 2 |
| `hasArea` | 3 | 3 | 3 | 2 |
| `hasCapacity` | 3 | 3 | 3 | 2 |
| `personHasCityOfDeath` | 3 | 3 | 3 | 1 |

Cross-model enabled/disabled does not change `m(o)`: cross-model recall is
scored in `X(o)` and is never in the acquisition denominator. Factual decoding
is unavailable and has no view family, so it is absent from every column.

The matrix was not tuned to produce attractive scores; it is a direct readout of
which view families each relation declares.

---

## 9. H_inc analysis

`inclusion_uncertainty(q) = −q log q − (1−q) log(1−q)`, added — it did not exist
before. Zero at `q = 0` and `q = 1` by an explicit boundary guard (no `log(0)`,
no `NaN`, no `−0.0`), maximal `log 2` at `q = 0.5`.

The interpretive trap is guarded explicitly. `H_inc = 0` at `q = 0` does **not**
mean the candidate is confidently correct; it means the inclusion state is
unambiguous, and there it is unambiguously *unsupported*.
`CandidateState` therefore always carries `coverage` (`q`) beside
`inclusion_uncertainty`, and `test_low_inclusion_uncertainty_is_not_a_confidence_signal`
constructs the two candidates with identical `H_inc = 0` and opposite `q`,
asserting they remain distinguishable.

---

## 10. L(o) calibrated log-odds analysis

`logit_term` reads `VerificationResult.log_odds(ε)`, which computes
`log((P_V + ε) / (P_I + P_U + ε))` on the **calibrated** probabilities stored by
Module 4 — the raw logits are retained on the result but never used here.
`P_I` and `P_U` stay separate fields even though both appear in the
denominator.

| requirement | status |
|---|---|
| calibrated probabilities, never raw | ✓ `test_the_logit_term_uses_calibrated_probabilities` sets raw logits that say the opposite and asserts the sign follows the calibrated values |
| `P_I` / `P_U` separate in stored state | ✓ `test_invalid_and_unknown_stay_separate_in_stored_state` |
| explicit ε | ✓ **fixed** — `logit_epsilon = 1e-6` promoted from a bare default to versioned config |
| explicit clip | ✓ `logit_clip = 3.0`, config |
| no NaN/inf | ✓ tested at `P_V ∈ {0, 1}` and `P_I + P_U = 0` |
| no verification → neutral | ✓ returns `0.0`, and `test_no_verification_gives_a_neutral_logit_term` asserts an unverified candidate is not penalised |
| one L per candidate, not per template | ✓ `_verify_one` aggregates multi-template results into a single `VerificationResult` before it reaches the graph, so three templates produce one edge and one `L` |
| most-VALID template does not win | ✓ `aggregate_verifications` averages the distributions; a 0.95-VALID merged with a 0.90-INVALID gives `valid_prob = 0.5` |

---

## 11. X(o) cross-model analysis

`X(o)` is now credited **only** for `CROSS_MODEL_RECALL` carrying
`INDEPENDENT_RECALL` mode — the second model produced the name itself, having
been shown nothing.

Two changes:

1. The `SHOWN_CANDIDATE` branch is **removed** (see §12).
2. A mode check was added, so a `CROSS_MODEL_RECALL` group whose edges were
   somehow not independent recall earns nothing.

`CROSS_MODEL_RECALL` no longer contributes to `F(o)` either, since it is not in
`acquisition_groups`. So one independent cross-model recall earns full `X`
credit and nothing else — the review's §15 requirement that it must not earn
full `F` **and** full `X`.

---

## 12. Explicit decision on shown-candidate contribution to X

**Decision: shown-candidate verifier agreement contributes ZERO to `X(o)`. The
`shown_candidate_weight = 0.25` knob is removed, not reduced.**

Reasoning, as the review demanded — citing the proposal rather than preserving
an existing judgement call:

- Spec §11.2 lists `L(o) = calibrated log-odds from the verifier` and
  `X(o) = optional cross-model support` as **two separate terms**. The blind
  verifier already has a term with its name on it.
- No proposal text requires shown-candidate agreement to appear in `X`. The
  previous 0.25 was a judgement call recorded as such in audit 0002 §6; it was
  never traced to a requirement.
- Paying it in both places double-counts one measurement. The old comment even
  admitted the overlap — *"its calibrated strength is already carried by
  `L(o)`"* — and then credited it anyway.
- The weight was also the wrong instrument: reducing a double count to 25% still
  leaves a double count, and makes `γ` uninterpretable.

`shown_candidate_weight` is removed from `ScoringConfig` and from the target
YAML. Test `test_blind_verifier_valid_moves_only_l` asserts `F == 0` and
`X == 0` for a shown-candidate VALID.

---

## 13. C(o) contradiction analysis

`C(o)` now counts **distinct contradicting mechanisms**, not raw contradiction
edges:

```python
contradicting_groups(candidate)   # groups with >= 1 CONTRADICT edge
C = len(contradicting_groups) / (len(acquisition_groups) + 1)
```

The `+1` is the blind verifier — the only non-acquisition mechanism that can
produce a signed contradiction — so numerator and denominator again share one
index set.

What counts: an explicit signed `CONTRADICT` edge. What does not, each tested:

| non-contradiction | test |
|---|---|
| candidate absent from another view | `test_absence_from_another_view_is_not_a_contradiction` |
| optional view never executed | `test_an_unrun_optional_view_is_not_a_contradiction` |
| second model did not independently recall it | `test_a_candidate_the_second_model_did_not_recall_is_not_contradicted` |
| UNKNOWN verdict | `test_an_unknown_verdict_is_not_a_contradiction` |

Repeated INVALID probes from the same mechanism no longer inflate `C`: three
verifier contradictions give the same `C` as one
(`test_multiple_verifier_templates_are_one_mechanism`), while
`Candidate.contradiction_count` still exposes the raw edge count for
diagnostics. Where templates genuinely conflict, that is prompt disagreement and
belongs to `U(o)`.

No learned contradiction fusion exists.

---

## 14. U(o) disagreement analysis

Exactly **one** signal enters `U(o)`: `U_prompt`, the generalized JSD across
verifier templates from spec §10.4, which is what §11.2's "prompt/view
disagreement" names. Range `[0, 1]`, taken as the max over the candidate's
verifications.

Deliberately **not** summed into `U`: verifier entropy `H_ver`, inclusion
uncertainty `H_inc`, contradiction, unresolved status. They measure different
things on different scales; adding them would be exactly the "blindly sum
unrelated quantities with incompatible ranges" the review prohibits. All remain
separately exposed on `CandidateState`.

---

## 15. H_ver / H_inc / U_prompt separation

| signal | meaning | source | on the state as |
|---|---|---|---|
| `H_inc` | ambiguity of *how many acquisition mechanisms* found it | `q(o)` | `inclusion_uncertainty` |
| `H_ver` | the verifier's own A/B/C uncertainty | Module 4 calibrated distribution | `verifier_entropy` |
| `U_prompt` | instability of the verdict across paraphrases | Module 4 JSD | `prompt_disagreement` |

`test_the_three_uncertainties_are_separately_inspectable` builds a candidate
where all three take different values and asserts three distinct numbers
survive, none overwriting another, and that `U(o)` carries only `U_prompt`.

---

## 16. Complete S(o) derivation

| term | definition | function | input evidence | range | coefficient | test |
|---|---|---|---|---|---|---|
| `F(o)` | `q(o) = g(o)/m(o)` | `support_term` | acquisition families only | `[0, 1]` | `alpha_support = 1.0` | `test_acquisition_support_moves_only_f` |
| `L(o)` | `clip(log((P_V+ε)/(P_I+P_U+ε))/clip)` | `logit_term` | blind verifier, calibrated | `[−1, 1]` | `beta_logit = 0.6` | `test_the_logit_term_uses_calibrated_probabilities` |
| `X(o)` | 1 iff independent cross-model recall | `cross_model_term` | `CROSS_MODEL_RECALL` + `INDEPENDENT_RECALL` | `{0, 1}` | `gamma_cross_model = 0.5` | `test_independent_cross_model_recall_moves_only_x` |
| `C(o)` | contradicting mechanisms / (m+1) | `contradiction_term` | signed `CONTRADICT` edges | `[0, 1]` | `delta_contradiction = 1.5` | `test_multiple_verifier_templates_are_one_mechanism` |
| `U(o)` | `U_prompt` | `disagreement_term` | Module 4 JSD | `[0, 1]` | `eta_disagreement = 1.0` | `test_prompt_disagreement_moves_only_u` |

`test_the_breakdown_sums_exactly_to_the_reported_total` recomputes the weighted
sum from the stored breakdown and asserts exact equality with `candidate.score`.
`test_every_coefficient_comes_from_configuration` AST-parses `score_candidate`
and asserts it contains **no** numeric literal at all.

Monotonicity, all tested: more independent support never lowers `S`; stronger
VALID evidence never lowers `L`; stronger contradiction never raises `S`; higher
disagreement never raises `S`.

---

## 17. Coefficient / threshold inventory

| parameter | default | owner / config path | consumer | judgement call? | train calibration still required? |
|---|---|---|---|---|---|
| `alpha_support` | 1.0 | `pipeline.scoring` | `S(o)` | yes | yes |
| `beta_logit` | 0.6 | `pipeline.scoring` | `S(o)` | yes | yes |
| `gamma_cross_model` | 0.5 | `pipeline.scoring` | `S(o)` | yes | yes |
| `delta_contradiction` | 1.5 | `pipeline.scoring` | `S(o)` | yes | yes |
| `eta_disagreement` | 1.0 | `pipeline.scoring` | `S(o)` | yes | yes |
| `logit_clip` | 3.0 | `pipeline.scoring` | `L(o)` | yes | yes |
| `logit_epsilon` | 1e-6 | `pipeline.scoring` | `L(o)` | no — numerical guard | no |
| `accept_score` | 0.2 | `pipeline.scoring` | `decide_status` | yes | **yes — see §37.1** |
| `min_valid_prob` | 0.40 (contract wins) | contract / `pipeline.scoring` | `decide_status` | yes | yes |
| `drop_on_unknown` | True (contract wins) | contract / `pipeline.scoring` | `decide_status` | yes | yes |
| `auto_accept_support` | 3 (contract wins) | contract / `pipeline.scoring` | `assign_tier` | yes | yes |
| `verify_max_support` | 2 | `pipeline.scoring` | `assign_tier` | yes | yes |
| `adversarial_max_support` | 1 | `pipeline.scoring` | `assign_tier` | yes | yes |
| `adversarial_disagreement` | 0.15 | `pipeline.scoring` | `assign_tier` | yes | yes |
| `min_independent_support` | 1 | contract | `decide_status` | yes | yes |
| `optional_views_available` | derived | `PipelineConfig.__post_init__` | `m(o)` | **no — derived from run mode** | no |
| ~~`shown_candidate_weight`~~ | ~~0.25~~ | **removed** (§12) | — | — | — |

Net free-parameter count is unchanged: one removed (`shown_candidate_weight`),
one added that is a numerical guard rather than a tuning knob
(`logit_epsilon`), and one added that is *derived*, not free
(`optional_views_available`). No parameter was tuned during this review, and
none was tuned on val.

---

## 18. Component-orthogonality tests

Five isolation cases from the review's §14, plus a sweep:

| case | expectation | result |
|---|---|---|
| A. direct elicitation SUPPORT only | `F` changes, `L`/`X`/`C` neutral | ✓ |
| B. blind verifier VALID only | `L` changes, `F` **and** `X` unchanged | ✓ |
| C. independent second-model recall only | `X` changes, `L` neutral | ✓ |
| D. explicit INVALID verifier | `C` and `L` respond; not read as missing acquisition support | ✓ |
| E. high prompt JSD | `U` changes, support count unchanged | ✓ |
| sweep over all `IndependenceGroup` members | each touches ≤ 1 component | ✓ |

---

## 19. Verifier double/triple-counting investigation

Audit 0007 §34.1 is **resolved**. Reproduced on the exact case it recorded — a
`companyTradesAtStockExchange` candidate with one acquisition mechanism, then
shown to the verifier, which agrees:

| | `F` | `L` | `X` | `S` |
|---|---|---|---|---|
| before verification | 0.250 | 0.000 | 0.000 | 0.250 |
| after — **was** | **0.500** | 0.732 | **0.250** | — |
| after — **now** | 0.250 | 0.732 | 0.000 | 0.689 |

`F` no longer moves; the verifier is paid once, through `L`. The candidate still
rises (it should — a calibrated VALID is real evidence), but through the term
the proposal assigns to it.

The knock-on consumers are fixed too. `assign_tier`, `verification_targets` and
both support checks in `decide_status` now read
`supporting_acquisition_groups`, so a verifier agreement can no longer lift a
candidate over the auto-accept threshold that decides whether it needed
verifying at all.

---

## 20. Cross-model double-counting investigation

Before, an independent cross-model recall earned `F = 0.167` **and** `X = 1.0`.
After, it earns `X = 1.0` only. Spec §11.2 gives cross-model support its own
term; nothing asks for it to also count as an acquisition facet, and the
enumerator's own families are what `F` is about.

---

## 21. Repeated-run / facet accounting

| case | `g(o)`/`F` | diagnostic retained |
|---|---|---|
| 10 repeats of one direct view | 1 group, `F = 1/6` | `raw_support_count = 10` |
| 3 award decade facets (one `STRUCTURAL_DECOMPOSITION`) | 1 group | `facet_ids` length 3 |
| 3 verifier templates | 1 mechanism | `contradiction_count = 3` |

The accepted Module-2 decision that facets partition one mechanism is preserved,
not undone.

---

## 22. UNKNOWN / contradiction boundary

UNKNOWN produces an `EdgeType.UNKNOWN` edge, which is neither in `supports` nor
in `contradictions`. So an UNKNOWN verdict:

- adds nothing to `g(o)` or `F(o)`;
- adds nothing to `C(o)`;
- does not erase existing support (`test_an_unknown_verdict_does_not_erase_support`);
- leaves the candidate `UNRESOLVED`, never `REJECTED`.

Absence is not contradiction, and a non-recall by the second model is not
contradiction. Both tested.

---

## 23. drop_on_unknown rescue analysis

**Fixed:** the rescue now compares `min_independent_support` and
`auto_accept_support` against acquisition support, not the raw group count. The
circular case is real and tested — a candidate verified VALID once carries a
`BLIND_VERIFIER` SUPPORT edge, and under the old rule that edge could supply the
second "independent" support that rescued the *same verifier's* later UNKNOWN
(`test_the_unknown_rescue_cannot_be_fed_by_the_verifier_itself`).

**Finding (recorded, not repaired):** the rescue branch is currently
**unreachable in its rescuing direction**. `read_labels` labels by argmax, so an
UNKNOWN verdict implies `P_V < 0.5`; every relation sets `min_valid_prob ≥ 0.5`
(0.50 ×3, 0.60 ×3), so the probability check two lines later returns
`UNRESOLVED` whatever the rescue decides. Verified across all six relations in
`test_the_unknown_rescue_is_currently_unreachable`.

The branch is kept because it is now *correct* — it would matter the moment a
relation adopts a recall-first operating point below 0.5, which the Module-0
policy correction deliberately made possible. Recorded here so the choice is
visible rather than accidental.

---

## 24. Hard-reject bypass

`decide_status` returns `REJECTED` on the first line for a hard-rejected
candidate, before any threshold or score is consulted; `assign_tier` returns
`HARD_REJECT` likewise. `test_a_hard_rejected_candidate_cannot_be_rescued_by_score`
builds a candidate with **maximal** evidence — every eligible mechanism, a 0.99
VALID verdict, and independent cross-model recall — confirms its `S(o)` is above
`accept_score`, and asserts it stays `REJECTED`.

Evidence and provenance survive the rejection for audit; the status does not
delete the graph.

---

## 25. Query-level state exposed downstream

`candidate_state()` and `query_state()` were added. They are **computed
accessors, not stored fields** — nothing new is persisted, nothing can go stale,
and `query_state` output is byte-identical when recomputed after a staged
round trip.

`query_state` reports available vs supported vs unexplored acquisition groups,
candidate count, unresolved count, contradicted count and max prompt
disagreement. It deliberately computes **no** residual, marginal yield,
saturation or next action — `test_query_state_computes_no_residual_or_action`
AST-inspects its call graph (not its prose) to assert none of Module 6/7's
functions are invoked.

---

## 26. Numeric / NULL_SINGLE boundaries

Numeric: three nearby scalars (100.0, 101.0, 102.0) remain three distinct
candidate nodes through Module 5; no tolerance merging, no dominant-cluster
selection — those are Module 8's. The same scalar repeated from one mechanism is
one independent support, not four.

`NULL_SINGLE`: an uncertain `personHasCityOfDeath` candidate resolves to
`UNRESOLVED`, never to a confident rejection that would read downstream as a
clean empty. A confident negative gate remains query-level state on the graph,
not a candidate with a large negative score.

---

## 27. Staged recomputation / round-trip

`test_module_5_state_survives_a_staged_round_trip` drives Phase A → persist →
Phase B → persist, recomputes `query_state` from the reloaded graph, round-trips
that graph once more, recomputes, and asserts the two states are equal.

Nothing was added to the stage JSON. Every Module-5 quantity is recomputed from
lossless evidence, so caching would only create a way for the cache and the
evidence to disagree.

---

## 28. Mismatches found

| # | Severity | Description |
|---|---|---|
| 1 | **severe** | Shown-candidate verifier agreement earned `F` **and** `L` **and** `X` — one measurement paid three times (audit 0007 §34.1) |
| 2 | **severe** | Independent cross-model recall earned `F` **and** `X` |
| 3 | moderate | `g(o)` and `m(o)` used different index sets, so `g > m` was reachable (6 vs 4) and hidden by `min(1.0, ...)` |
| 4 | moderate | Repeated INVALID verdicts from one mechanism inflated `C(o)` linearly, as if independent facts |
| 5 | moderate | `H_inc(o)` (spec §11.1) was **not implemented at all**; `q(o)` existed but nothing used it |
| 6 | minor | `EXISTENCE_GATE` and disabled `FACTUAL_DECODING` support edges would have counted toward `F` |
| 7 | minor | `L(o)`'s ε was a bare function default rather than versioned configuration |

---

## 29. Fixes made

All in `src/cover_kbc/scoring.py` unless noted.

1. Added `acquisition_groups` / `supporting_acquisition_groups` / `coverage_q` —
   one consistent index set for `g` and `m` (defects 1, 2, 3, 6).
2. `support_term` now returns `q(o)`; the masking clamp is gone (defect 3).
3. `cross_model_term` credits independent recall only; `shown_candidate_weight`
   removed from config and from the target YAML (defect 1).
4. Added `contradicting_groups`; `contradiction_term` counts mechanisms over a
   matching index set (defect 4).
5. Added `inclusion_uncertainty` (`H_inc`), `CandidateState`, `candidate_state`,
   `query_state` (defect 5, and the review's §27).
6. `logit_epsilon` promoted to `ScoringConfig` and threaded into `log_odds`
   (defect 7).
7. `assign_tier`, `verification_targets` and both support checks in
   `decide_status` read acquisition support (defect 1's consumers, review §21).
8. `PipelineConfig.__post_init__` derives `optional_views_available` from the run
   mode and propagates it to `selection.scoring` (`src/cover_kbc/pipeline.py`).

No Module-6, -7 or -8 algorithm was changed.

---

## 30. Before/after synthetic examples

Recorded in §5 (full accounting matrix, before and after) and §19 (the exact
audit-0007 §34.1 case). One more, the saturation case that `min(1.0, …)` was
hiding:

| | `g(o)` | `m(o)` | raw ratio | `F(o)` reported |
|---|---|---|---|---|
| before | 6 | 4 | 1.500 | 1.000 (clamped) |
| after | 4 | 4 | 1.000 | 1.000 (genuine) |

Same reported `F`, but the first was a clamp concealing an index-set bug and the
second is a real saturation.

---

## 31. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/scoring.py` | modified — Module 5 rebuilt around one index set (+372/−49) |
| `src/cover_kbc/pipeline.py` | modified — availability derivation (+18/−1) |
| `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` | modified — retired `shown_candidate_weight`, added `logit_epsilon` |
| `tests/test_evidence_state_conformance.py` | **created** — 769 lines, 72 tests |
| `tests/test_pipeline.py` | modified — run mode pinned where the assertion depends on `m(o)` |
| `tests/test_verification.py` | modified — proposal-consistent X invariant; helper draws from eligible groups |
| `tests/test_verifier_conformance.py` | modified — assertion follows the renamed support accessor |
| `docs/audits/0008-module-5-evidence-uncertainty-conformance.md` | **created** — this file |

`benchmark/` untouched.

---

## 32. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_evidence_state_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/
git diff -- benchmark/
git diff --cached -- benchmark/
git diff --stat
```

No model download, no heavyweight inference.

---

## 33. Exact test results

**595 passed, 0 failed, 0 skipped** (up from 523).

| File | Tests |
|---|---|
| `tests/test_contracts.py` | 44 |
| `tests/test_controller.py` | 32 |
| `tests/test_data.py` | 26 |
| `tests/test_elicitation.py` | 63 |
| `tests/test_evaluation.py` | 13 |
| `tests/test_evidence.py` | 23 |
| `tests/test_evidence_state_conformance.py` | **72** |
| `tests/test_graph.py` | 59 |
| `tests/test_normalization.py` | 59 |
| `tests/test_pipeline.py` | 31 |
| `tests/test_programs.py` | 40 |
| `tests/test_staging.py` | 17 |
| `tests/test_verification.py` | 40 |
| `tests/test_verifier_conformance.py` | 76 |

`pyflakes`: clean apart from four intentional `import _bootstrap` sys.path shims
in `scripts/`.

Three pre-existing tests changed expectations, each because it encoded the old
accounting rather than the proposal:

1. `test_cross_model_independent_recall_outweighs_shown_agreement` asserted
   shown-candidate agreement earns `shown_candidate_weight` of `X`. Replaced
   with the proposal-consistent invariant: it earns **zero** (§12).
2. `test_a_better_supported_candidate_is_not_escalated` built a "2-mechanism"
   `companyTradesAtStockExchange` candidate using `STRUCTURAL_DECOMPOSITION` —
   a group that relation has no view family for. Under the corrected semantics
   that is 1 mechanism, not 2. The helper now draws from the contract's own
   eligible groups, which is what the test meant.
3. `test_pipeline_collects_multi_view_evidence` asserted an acceptance outcome
   that depends on `m(o)`. Because `q` is a ratio over *available* mechanisms,
   the run mode must be explicit for that claim to mean anything; the test now
   pins `run_optional_views=True`, under which its original intent holds.

No formula was altered to make an old numeric expectation pass.

---

## 34. Benchmark integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

---

## 35. Challenge-compliance impact

| constraint | status |
|---|---|
| No learned neural fusion (§11.3) | ✓ `test_no_learned_fusion_exists` AST-walks for `sklearn`/`torch`/`scipy`/`numpy`/`xgboost`/`lightgbm` imports and for `fit`/`partial_fit`/`train`/`backward`/`step` calls |
| No training of any kind | ✓ |
| No retrieval / external corpus | ✓ `test_module_5_performs_no_retrieval` |
| Deterministic | ✓ `test_the_scripted_pipeline_stays_deterministic` runs the pipeline three times and asserts identical outputs and scores |
| Few free parameters | ✓ net count unchanged; one knob retired |
| Parameter budget | unchanged — Module 5 is non-neural and adds no model |

---

## 36. Weights / thresholds still requiring train calibration

Everything in §17 marked "yes". They are hand-set defaults, not measurements,
and must be calibrated on train or a documented internal split, frozen, and only
then evaluated on val once.

---

## 37. Unresolved Module-5-only issues

### 37.1 `accept_score` is now run-mode-sensitive

`F(o) = q(o)` is a ratio over *available* mechanisms, so identical evidence
yields different `F` under different run modes — one mechanism gives `F = 1/6`
under the target configuration but `F = 1/2` in a mandatory-only borders run.
A single absolute `accept_score` is therefore not comparable across
configurations.

This follows directly from the proposal's own definition of `q(o)`, so it is not
a defect to repair by changing the formula. It is a **calibration constraint**:
`accept_score` must be calibrated under the target configuration and must not be
carried over to an ablation without recalibration. Flagged here so a future
ablation does not silently read as a precision change.

### 37.2 The `drop_on_unknown` rescue is currently unreachable

See §23. Correct now, but dormant until some relation sets
`min_valid_prob < 0.5`.

### 37.3 `raw_support_count` includes verifier edges

The diagnostic counter sums raw edges across all groups, including
`BLIND_VERIFIER`. That is harmless — nothing scores from it, and §21 relies on
it staying raw — but it is not a count of acquisition events, and a future
reader could mistake it for one.

---

## 38. Future-review notes for Modules 6 / 7 / 8

Recorded, **not fixed**.

### 38.1 Module 6 — RCSE reads Module-3 accessors, not the corrected state

`estimate_residual` computes `_facet_gap` from `contract.all_views()` against
`state.covered_facets`, and `_unresolved_mass` from candidate status. It never
consults `q(o)`, `H_inc` or the available/supported group split that Module 5
now exposes — so residual coverage is currently reasoning about *views* while
Module 5 reasons about *independence groups*. Worth reconciling when Module 6 is
authorized; `query_state()` was written to be the input it needs.

### 38.2 Module 7 — the calibrated gate's model identity is mode-dependent

Carried forward unchanged from audit 0007 §34.3. `_run_gate` scores with
`self.verifier_runtime`, which in staged Phase A falls back to the enumerator,
so the gate is scored by Mistral in staged runs and Qwen in interleaved ones.

### 38.3 Module 8 — `EmptyReason.CANDIDATE_REJECTED` is unreachable

Carried forward unchanged from audit 0007 §34.2.

### 38.4 Module 8 — selection sorts on the raw support count

`selection.py` orders accepted candidates by `-c.independent_support` and
weights numeric clusters by `max(1, c.independent_support)`. That is the *raw*
group count, which still includes `BLIND_VERIFIER` and `CROSS_MODEL_RECALL`.
It affects output **ordering and numeric cluster weight**, not acceptance, so it
is not an accounting defect of the kind fixed here — but for consistency Module
8 should probably use `supporting_acquisition_groups` too. Left untouched under
the review's §35.

---

## 39. Module 6+ remain unreviewed

Modules 6 (RCSE), 7 (Active Controller and Adaptive Stopping) and 8 (Final
Selection) have **not** been reviewed against the proposal. Their code exists
and their tests pass, but no conformance judgement has been made about them.
The notes in §38 are observations made while reviewing Module 5; they are not a
review of those modules and are not exhaustive.

---

## 40. Recommended next review

**Module 6 — Residual Coverage & Saturation Estimator (RCSE).**

It is the immediate consumer of the state corrected here, and §38.1 records a
concrete reconciliation it needs.

---

## Verdict

**Module 5 PASSES** after five defects were found and fixed.

The five score components now have explicit non-overlapping semantics enforced
by index set rather than by convention: acquisition families pay `F`, the blind
verifier pays `L`, independent cross-model recall pays `X`, signed conflicts pay
`C`, paraphrase instability pays `U`. A sweep over every independence group
asserts each touches at most one component. `g(o) ≤ m(o)` holds by construction,
`m(o)` means "capable of expressing under this run configuration" rather than a
catalogue or an executed-only shortcut, `H_inc` exists for the first time and is
kept distinct from `H_ver` and `U_prompt`, hard rejects bypass the score, and
status derives from the evidence state without erasing it.
