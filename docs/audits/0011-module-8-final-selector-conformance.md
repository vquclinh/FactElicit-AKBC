# Audit 0011 — Module 8: Final Selector and Evaluator-Aware Output Conformance

Status: **complete — 7 defects found, 7 fixed; both carried findings resolved**
Date: 2026-08-05
Scope: Module 8 only. This is the last per-module review; end-to-end conformance
is **not** covered here.

---

## 1. Objective and scope

Answer the review's central question:

> Does the existing final selector emit exactly the semantic objects the
> accepted COVER evidence state supports — or does it accidentally reintroduce
> raw-frequency heuristics, unsafe alias merging, generic cardinality rules, or
> output artifacts?

**Answer: three of those four had crept in.** The numeric path bypassed Module
5's acceptance decision entirely and force-accepted whatever cluster won; its
cluster weighting counted the blind verifier and cross-model recall a second
time; the writer folded leading articles, promoting a soft alias hint to hard
output identity; and `EmptyReason.CANDIDATE_REJECTED` was still unreachable.

In scope: `src/cover_kbc/selection.py`, `src/cover_kbc/data/writer.py`, the
numeric contract thresholds they read, and the `Candidate` fields they emit.
Out of scope: everything Modules 0–7 own; `benchmark/` is immutable.

No model was downloaded or run.

---

## 2. Proposal requirements

| § | Requirement |
|---|---|
| 14 | "The final selector maps graph state to ObjectEntities. It must optimize the actual evaluator, not linguistic elegance." |
| 14.1 | "A candidate is emitted if it exceeds the relation threshold and has no hard violation. Emit only **one preferred surface form per semantic candidate**." The evaluator uses maximum bipartite alias matching and "multiple surface forms of the same entity can reduce precision". |
| 14.2 | Numeric outputs come "from the dominant robust cluster rather than by token likelihood", clustering on relative distance `d = |x_i − x_j| / max(|x_i|, |x_j|)` with "a conservative threshold **related to, but not automatically identical to**, the official 5% tolerance". Representative "is typically `median(C_dominant)`". |
| 26 | Algorithm 1 ends at `return P.Finalize(S, G)` — finalization consumes state, it does not extend it. |
| 31 | Every final candidate must be traceable to Evidence Graph events. |

---

## 3. Pre-work repository state

Branch `main`, HEAD `35671ad` ("refactor: align COVER-KBC active controller with
architecture"). Working tree clean; Module-7 work committed. Audits 0001–0010
accepted. 760 tests passing.

---

## 4. Existing Module-8 architecture

`selection.py` held four selector functions dispatched by relation name with a
`ProgramType` fallback, plus `finalize()` building the `Prediction`.
`writer.py` converted predictions to official rows and deduplicated.

---

## 5. Finalization precondition

Module 7 guarantees the controller has settled or the budget is exhausted, and
that no executable `pending_action` survives. Module 8 now **enforces** that
rather than assuming it: `finalize()` raises `SelectionInvariantError` if a
pending action is present.

It executes nothing, resumes nothing, and spends no budget. Where Module 7
legitimately abandons an action because the budget ran out, it clears the slot
and records the abandonment in the controller log first (audit 0010 §31.3), so
Module 8 never has to decide what to do with one.

---

## 6. ProgramType → selector-family map

| ProgramType | Selector | Relations |
|---|---|---|
| `SMALL_SET` | `select_small_set` | borders, stock exchange |
| `NULL_SINGLE` | `select_null_single` | city of death |
| `NUMERIC` | `select_numeric_robust` | area (default) |
| `NUMERIC` (override) | `select_numeric_highest_valid` | capacity |
| `LARGE_OPEN_SET` | `select_large_open_set` | awards |

Dispatch is a two-level table — relation override, then programme fallback — not
an `if relation == …` chain. `test_every_program_type_has_a_selector` asserts
the table is total over `ProgramType`.

---

## 7. RelationContract final-policy consumer matrix

| Contract field | Module-8 consumer | Live? |
|---|---|---|
| `selection.max_objects` | cardinality cap **and** invariant check | yes |
| `selection.numeric_cluster_threshold` | `_numeric_clusters` | yes |
| `selection.numeric_integer_only` | `format_numeric` (capacity) | yes |
| `selection.min_independent_support` | via `decide_status` | yes (Module 5) |
| `selection.numeric_target_unit` | Module 3 normalisation | yes (upstream) |
| `verification.accept_valid_prob` / `drop_on_unknown` | via `decide_status` | yes (Module 5) |
| `program_type` | selector dispatch | yes |

No dead final-policy metadata was found.

---

## 8. CandidateStatus / score / threshold precedence — DEFECT 1 (severe, fixed)

**The single authority for emission is Module 5's `decide_status`.** Module 8
filters and orders; it does not re-decide.

The string selectors already respected this. The **numeric selectors did not**:
`_emit_numeric` contained

```python
winner.status = CandidateStatus.ACCEPTED
```

which overwrote whatever `decide_status` had concluded. Reproduced before the
fix: a single-mechanism numeric candidate that Module 5 had *not* accepted was
emitted with its status rewritten to `ACCEPTED`.

Now a numeric cluster is emittable only if it contains a candidate Module 5
accepted (`_cluster_is_emittable`), and status is never rewritten. A numeric
answer must clear the same bar as a string one; emitting the best of several
unresolved clusters because the search stopped would convert "we could not
resolve this" into a confident scalar.

Precedence, in order: hard rejection → `decide_status` → relation cardinality →
deterministic ordering.

---

## 9. Hard-violation handling

Absolute. `graph.active_candidates()` excludes `REJECTED`, and the numeric
clusterer filters them explicitly as well, so a rejected candidate cannot enter
a cluster or be pulled back by magnitude, verifier VALID, cross-model recall or
raw frequency. Tested across all six relations with maximal contrary evidence.

Provenance is retained on the node for diagnostics. No new factual hard filters
were added.

---

## 10. Raw-frequency audit

Searched `selection.py` and every ranking helper for `raw_support_count`,
`len(...supports)`, mention counts and edge counts used as support signals.

| site | before | after |
|---|---|---|
| numeric cluster weighting | `candidate.independent_support` | `supporting_acquisition_groups` |
| `_cluster_support` | `independent_support` | `supporting_acquisition_groups` |
| string ordering (3 sites) | `-c.independent_support` | `_rank_key` → acquisition support |

`independent_support` is a *group* count, so ten repeats of one view were
already one unit — but it also counts `BLIND_VERIFIER` and
`CROSS_MODEL_RECALL`, which Module 5 deliberately separated into `L` and `X`.
Using it here paid the same evidence a second time at the very last step.

`test_no_raw_frequency_signal_drives_selection` asserts `raw_support_count`
appears nowhere in the module and that the corrected accessor is used.

---

## 11. String selector semantics

Emission requires `decide_status is ACCEPTED`; ordering is
`(-score, -acquisition_support, key)`. No selector-local confidence system, no
model call, no second threshold.

- **borders** — precision-aware, no cardinality cap;
- **stock** — same family, and weak unresolved listings are dropped by
  `drop_on_unknown` plus the acceptance threshold rather than by a local rule;
- **awards** — recall-first, no cap, but the same acceptance bar, so the
  uncertain tail the controller stopped exploring does not become final;
- **death** — capped at one by the contract.

---

## 12. Strict identity vs alias-hint boundary — DEFECT 2 (fixed)

`writer.dedupe_object_entities` keyed on `alias_hint_key`, which is
`strict_key` **plus leading-article folding**. Measured:

| surfaces | evaluator sees | `alias_hint_key` |
|---|---|---|
| "The Alpha Exchange" / "Alpha Exchange" | **different** | same |
| "Le Havre" / "Havre" | different | different |
| "X" / "X (qualifier)" | different | different |

So the writer was dropping a prediction the evaluator treats as distinct. Since
Module 3 keys candidates on `strict_key`, two values reaching the writer are
always two *distinct strict candidates* — exactly the case the review's §16B
forbids merging on a weak hint, and exactly the promotion of `alias_hint` to
hard identity that audit 0006 prohibits. It was also a *semantic* decision taken
in the writer rather than the selector.

Dedup now keys on `strict_key`, which reproduces the official evaluator's
`normalize_string` exactly: it removes what the evaluator would collapse anyway,
and nothing more.

**This has a measured precision cost, recorded rather than hidden.** In the
end-to-end test whose gold alias set covers both article variants, macro-F1
falls from 1.000 to 0.667 — precision 1/2, recall 1/1 — because both variants
are now submitted. See §45.1.

---

## 13. Preferred-surface policy

Module 3 keys on `strict_key`, so one semantic candidate is one node with one
`display_value` — §14.1's "one preferred surface per semantic candidate" holds
by construction, not by a post-hoc filter. Extra observed forms live in
`surface_forms` and never reach the output.

Every emitted value comes from `candidate.output_value`, which is either the
observed surface or a deterministically derived numeric representative (§16).
No writer-generated string, no invented alias, no novel semantic object.

---

## 14. Output dedup and order

Dedup: evaluator-identical only, order-preserving, blank-dropping.

Order: `(-score, -acquisition_support, key)` — deterministic and independent of
insertion order. `test_output_order_is_deterministic_under_insertion_shuffle`
builds the same candidates in three different insertion orders with identical
evidence and asserts one result.

Forbidden at the boundary: empty strings and control tokens. `_NEVER_AN_OBJECT`
rejects `valid`, `invalid`, `unknown`, `none`, `null`, `n/a`, `na`, `nan` —
raising rather than filtering, because a verifier label reaching the output path
means an upstream boundary leaked and shipping it silently would be worse
(**DEFECT 3**, fixed: `VALID`/`INVALID` were previously emittable as objects).

---

## 15. NULL_SINGLE cardinality

Capped at `contract.selection.max_objects == 1`, and independently enforced by
`_check_cardinality`, which **raises** rather than truncating. Silent truncation
would hide a real selector bug behind a plausible row.

---

## 16. Empty-output truth table

| gate | candidates generated | any survive | outcome | reason |
|---|---|---|---|---|
| confident NO | any | any | `[]` | `CONFIDENT_NEGATIVE_GATE` |
| not negative | none | — | `[]` | `NO_CANDIDATE_GENERATED` |
| not negative | some | **none** (all rejected) | `[]` | `CANDIDATE_REJECTED` |
| not negative | some | some, none accepted | `[]` | `UNRESOLVED_ABSTENTION` |
| not negative | some | some accepted | objects | `NOT_EMPTY` |

All five verified by test, and
`test_the_four_empty_states_never_collapse_into_each_other` asserts three
distinct empty reasons arise from three distinct states.

An uncertain gate is never relabelled a confident negative: with the gate
undecided and every candidate rejected, the reason is `CANDIDATE_REJECTED`.

---

## 17. EmptyReason reachability

Every member is reachable and tested, parametrized over the enum so a future
addition cannot be decorative.

---

## 18. CANDIDATE_REJECTED fix — DEFECT 4 (carried from audits 0007/0010, fixed)

`_empty_reason` received `graph.active_candidates()`, which **already excludes**
rejected candidates. So `if not candidates` fired first and the
`CANDIDATE_REJECTED` branch below it was unreachable: a query whose every
candidate was rejected looked identical to one that generated nothing.

Fixed by reading generation from the **full** graph and survival from the active
list:

```python
everything = list(graph.candidates.values())
if not everything:        return NO_CANDIDATE_GENERATED
if not active:            return CANDIDATE_REJECTED
return UNRESOLVED_ABSTENTION
```

"We generated nothing" is a recall failure; "we generated things and rejected
them all" is a precision success. They call for opposite fixes and must never
have shared a label.

---

## 19. Stock high-precision selector

High precision. A weak unresolved listing is dropped by `drop_on_unknown` plus
the acceptance threshold, not by a selector-local rule
(`test_stock_drops_a_weak_unresolved_listing`). Zero output remains possible
when the subject is confidently not publicly traded or nothing meets policy. No
exchange whitelist, and parent/subsidiary near misses are handled upstream by
the contract and verifier rather than resurrected here.

---

## 20. Border selector

Emits qualified candidates only; a hard-rejected candidate never appears; no
cardinality cap; no geography facts added. Disputed weak candidates follow the
contract's acceptance policy.

---

## 21. Award selector and long-tail precision

Recall-first with no cap, but the same acceptance bar as every other string
relation, so the weak uncertain tail the controller stopped exploring does not
become final. A rejected winning-work near miss is not emitted.

Stopping means further computation was no longer worth its cost — it does not
mean every currently unresolved object is correct, and nothing in this selector
treats it that way.

---

## 22. Numeric cluster geometry

`cluster_values` from `normalization/numeric.py` — the **same primitive Module 6
uses** for its stability diagnostic, so the two can never disagree about what a
cluster is. Module 6 observes; Module 8 decides which cluster wins.

Single-linkage over sorted values on relative distance
`|x_i − x_j| / max(|x_i|, |x_j|)`, deterministic and order-independent, with
clusters returned largest-first and ties broken by dispersion then
representative.

### 22.1 Internal threshold vs the official tolerance — DEFECT 5 (fixed)

The internal threshold was **0.05 — exactly the evaluator's ±5% tolerance**,
which §14.2 explicitly warns against.

The argument for changing it is technical, not empirical: clustering is
single-linkage, so a chain of values each exactly at the threshold spans far
more than the threshold. At 0.05 a three-value chain can span ~10%, and the
cluster median could then sit *outside* the evaluator's tolerance of its own
members. The default is now **0.025** — half the tolerance — which keeps a
cluster comfortably inside it.

This is an architecture default derived from a property of the algorithm, **not
a value fitted to val**. Recorded for train calibration in §44.

---

## 23. Numeric support weighting — DEFECT 6 (carried from audits 0008/0010, fixed)

Each candidate contributed `max(1, independent_support)` copies of its value to
the clustering input, and `_cluster_support` summed the same. That raw accessor
includes `BLIND_VERIFIER` and `CROSS_MODEL_RECALL`, so a verified or
cross-recalled figure pulled its cluster harder purely for having been verified
— re-merging `F`, `L` and `X` at the last step after Module 5 separated them.

Both now use `supporting_acquisition_groups`.
`test_cluster_weight_ignores_verifier_and_cross_model_support` adds a verifier
edge and a cross-model edge to a candidate, asserts `independent_support` grows
1 → 3, and asserts the cluster weight does not move.

---

## 24. Before/after the raw-support defect

The decisive regression, `hasArea`:

| cluster | evidence | raw edges | weight before | weight after |
|---|---|---|---|---|
| A — value 500 | 1 mechanism × 10 repeats | **10** | 1 | 1 |
| B — value 100 | 3 independent mechanisms | 3 | 3 | **3** |

Emitted: **`100`**. The three-mechanism cluster wins despite 3 < 10 raw
mentions. (The group-based accessor already handled this case; the verifier
double-count in §23 is what actually changed.)

---

## 25. Area finalization

Dominant robust cluster, representative `median(C_dominant)`, one scalar,
already normalised to km² by Module 3 — no second unit system, no re-conversion.
Output is a bare numeral: no unit suffix, no thousands separator, no scientific
notation. At most one object, enforced by the contract cap and the invariant
check.

---

## 26. Capacity finalization

The target is the **highest published maximum spectator capacity**, not the
largest number seen. `select_numeric_highest_valid`:

1. clusters as above;
2. **excludes** any cluster the verifier labelled INVALID — record attendance
   and seated-only near misses;
3. keeps clusters that are either supported about as strongly as the dominant
   one (`capacity_support_ratio`) or explicitly verified VALID;
4. **now also requires** the cluster to contain a Module-5-accepted candidate;
5. among survivors takes the highest representative, ties broken by lower
   dispersion then cluster key — never iteration order.

Serialised as an integer. No venue knowledge added.

---

## 27. Capacity record-attendance and seated-only near misses

The proposal distinguishes maximum spectator capacity from record attendance and
from a smaller seated-only figure. That boundary is preserved by evidence, not
by magnitude:

| scenario | outcome |
|---|---|
| 50 000 (3 mechanisms, VALID) vs 99 000 (1 mechanism, INVALID record attendance) | **50 000** |
| 50 000 (3 mechanisms) vs 250 000 (1 mechanism, unverified) | **50 000** |
| 50 000 (3 mechanisms) vs 30 000 (1 mechanism, seated-only) | **50 000** |

A larger number never wins for being larger; it must first survive the verifier
and the acceptance policy.

---

## 28. Numeric representative — DEFECT 7 (fixed)

`_emit_numeric` overwrote `candidate.display_value` with the derived median,
destroying the observed surface. A median need never have been generated
verbatim, so an aggregate became indistinguishable from something a model
actually said.

The representative itself is `median(C_dominant)` — robust, deterministic, and
never token likelihood, recency or raw-frequency mode.

---

## 29. Derived numeric provenance

`Candidate.derived_value` was added alongside `display_value`, with
`output_value` returning the derived figure when present. A median may never
have been generated verbatim; that is legitimate deterministic aggregation, but
the trace must not present it as an observation.

Retained and inspectable: the cluster members, their normalised values, the
cluster support, the winning candidate's own observed value, and the selection
rule that chose the cluster.

---

## 30. Output writer boundary

The selector owns *what* is emitted; the writer owns *how* the row is
serialised. `test_the_writer_performs_no_semantic_selection` AST-checks that the
writer calls none of `select`, `decide_status`, `score_candidate`,
`cluster_values`, `assign_tier`, `finalize`, and that `alias_hint_key` is not
*used* (its name now appears only in a docstring explaining why it must not be).

---

## 31. Exact official schema

Confirmed against `benchmark/data/`: submission rows carry exactly
`SubjectEntity`, `Relation`, `ObjectEntities`, with `ObjectEntities` a **flat
list of strings** (gold uses a list-of-alias-lists; predictions do not).

Verified end to end: no `score`, `confidence`, `empty_reason`, `candidates`,
`controller_log` or `budget` field reaches the file. Diagnostics live in
separate trace and stage files.

---

## 32. Official-evaluator plumbing smoke

Ran the production staged CLI on three `awardWonBy` val rows with scripted
runtimes, then the pinned official evaluator:

```
fields present: ['ObjectEntities', 'Relation', 'SubjectEntity']
schema OK; empty rows: 0
```

Accepted cardinalities verified separately: `[]`, one string, several strings,
and a numeric string matched inside the evaluator's own ±5% tolerance.

**The reported F1 of this run is 0.000 and is meaningless.** The scripted
runtime emits the literal string "Alpha", which is not a fact. This is
**format/plumbing validation only** and must never be quoted as system
performance. No threshold was adjusted from any score.

---

## 33. Staged round-trip

`test_finalization_survives_a_stage_round_trip` covers a string relation,
awards, area and capacity: the emitted objects and the empty reason are
identical before and after persistence. `test_an_empty_row_survives_a_stage_round_trip`
covers an all-rejected `CANDIDATE_REJECTED` row, which requires rejected
candidates to survive serialisation — they do.

---

## 34. Deterministic ordering and tie breaking

String order: `(-score, -acquisition_support, key)`. Numeric cluster ties:
`(representative, -relative_mad)`, then the winning member by
`(|value − representative|, key)`. No Python set or dict iteration order reaches
an output decision; verified by insertion-order shuffle.

---

## 35. Threshold / config inventory

| parameter | default | owner | relation override | judgement call? | train calibration? |
|---|---|---|---|---|---|
| `selection.max_objects` | 0 / 1 | contract | yes (1 for death, area, capacity) | no — a programme fact | no |
| `selection.numeric_cluster_threshold` | **0.025** | contract | uniform today | yes | **yes** (§44) |
| `selection.numeric_integer_only` | False / True | contract | True for capacity | no — a schema fact | no |
| `capacity_support_ratio` | 1.0 | `SelectionConfig` | — | yes | yes |
| `capacity_trust_verified` | True | `SelectionConfig` | — | yes | yes |
| acceptance thresholds | — | **Module 5** contract policy | yes | — | yes (audit 0008 §36) |

`test_every_selector_constant_is_configuration` asserts `select` contains no
float literal. No hidden numeric literal controls semantic output.

---

## 36. Mismatches found

| # | Severity | Description |
|---|---|---|
| 1 | **severe** | Numeric selectors bypassed `decide_status` and force-set `ACCEPTED`, emitting candidates Module 5 had not accepted (§8) |
| 2 | **severe** | Writer folded leading articles, promoting `alias_hint_key` to hard output identity and merging distinct strict candidates (§12) |
| 3 | severe | Numeric cluster weighting counted the blind verifier and cross-model recall, re-merging `F`/`L`/`X` (§23) — carried from audits 0008/0010 |
| 4 | severe | `EmptyReason.CANDIDATE_REJECTED` unreachable; "generated then all rejected" was reported as "nothing generated" (§18) — carried from audits 0007/0010 |
| 5 | moderate | Internal cluster threshold was pinned to the evaluator's ±5% tolerance, which single-linkage chaining can exceed (§22.1) |
| 6 | moderate | `_emit_numeric` overwrote the observed surface with the derived median, erasing the observed/derived distinction (§28) |
| 7 | moderate | Verifier labels (`VALID`/`INVALID`) and placeholders were emittable as objects; string ranking used the raw group counter; no cardinality invariant existed (§§14, 10, 15) |

---

## 37. Fixes made

1. `_empty_reason` reads generation from the full graph (§18).
2. `_rank_key` / `_acquisition_support` — acquisition-aware ordering (§10).
3. `_numeric_clusters` and `_cluster_support` use `supporting_acquisition_groups`
   and exclude rejected nodes (§23).
4. `_cluster_is_emittable` — numeric emission requires an accepted candidate;
   `_emit_numeric` no longer rewrites status (§8).
5. `Candidate.derived_value` / `output_value` — observed vs derived (§28).
6. `_check_cardinality` + `_NEVER_AN_OBJECT` — fail-closed invariants (§§14, 15).
7. `finalize` refuses an executable pending action (§5).
8. `numeric_cluster_threshold` 0.05 → 0.025 with a stated rationale (§22.1).
9. `writer.dedupe_object_entities` keys on `strict_key` (§12).

---

## 38. Before/after synthetic scenarios

| scenario | emitted | empty reason |
|---|---|---|
| A borders: 2 accepted, 1 rejected, 1 weak unresolved | `['Alpha', 'Beta']` | NOT_EMPTY |
| B death: 1 accepted locality, 1 rejected country | `['Paris']` | NOT_EMPTY |
| C death: confident negative gate | `[]` | `confident_negative_gate` |
| D death: all candidates rejected | `[]` | **`candidate_rejected`** |
| E death: unresolved, uncertain gate | `[]` | `unresolved_abstention` |
| F stock: 1 accepted, 1 weak unresolved | `['Alpha Exchange']` | NOT_EMPTY |
| G area: 3 mechanisms @100 vs 10 repeats @500 | `['100']` | NOT_EMPTY |
| H capacity: 50 000 VALID vs 99 000 INVALID vs 30 000 weak | `['50000']` | NOT_EMPTY |
| I awards: 2 recipients, weak tail, rejected work | `['R1', 'R2']` | NOT_EMPTY |

Rows C, D and E were previously indistinguishable in two of the three cases.

---

## 39. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/selection.py` | modified — status authority, acquisition weighting, invariants |
| `src/cover_kbc/data/writer.py` | modified — evaluator-identical dedup |
| `src/cover_kbc/types.py` | modified — `derived_value`, `output_value` |
| `src/cover_kbc/contracts/base.py`, `registry.py` | modified — cluster threshold 0.05 → 0.025 |
| `tests/test_final_selector_conformance.py` | **created** — 64 tests |
| `tests/test_data.py`, `tests/test_evidence.py`, `tests/test_pipeline.py` | modified — corrected alias invariant |
| `docs/audits/0011-module-8-final-selector-conformance.md` | **created** |

`benchmark/` untouched.

---

## 40. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_final_selector_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_roleswap.yaml --relation awardWonBy --limit 3
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

No model download, no heavyweight inference.

---

## 41. Exact tests / results

**823 passed, 1 skipped, 0 failed** (up from 760).

| File | Tests |
|---|---|
| `tests/test_final_selector_conformance.py` | **64** (1 skipped: an EmptyReason produced outside the selector) |
| `tests/test_controller_conformance.py` | 79 |
| `tests/test_rcse_conformance.py` | 86 |
| `tests/test_evidence_state_conformance.py` | 72 |
| `tests/test_verifier_conformance.py` | 76 |
| remaining suites | 446 |

`pyflakes`: clean apart from four intentional `_bootstrap` sys.path shims.

Three pre-existing tests changed expectations, each because it encoded the old
alias-folding behaviour the review overrules (§12). One now asserts a **lower**
F1 (2/3 instead of 1.0) on its synthetic gold — the honest cost, recorded rather
than adjusted away.

---

## 42. Benchmark integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

No evaluator behaviour was patched or reimplemented.

---

## 43. Challenge-compliance impact

| constraint | status |
|---|---|
| No model call in finalization | ✓ AST-checked for `generate`, `score_labels`, `verify_*`, `score_gate`; no runtime reference exists |
| No retrieval / factual table | ✓ AST-checked imports plus a scan for embedded literal collections |
| No learned selector | ✓ AST-checked for `fit`/`predict`/`train` |
| Deterministic | ✓ insertion-order shuffle and stage round trip |
| Traceable output | ✓ every value is an observed surface or a derived representative with its cluster retained |
| Parameter budget | unchanged — Module 8 is non-neural |

---

## 44. Thresholds requiring later train calibration

`numeric_cluster_threshold` (0.025), `capacity_support_ratio` (1.0),
`capacity_trust_verified` (True), and the Module-5 acceptance thresholds these
selectors consume. All are architecture defaults. Calibrate on train or a
documented internal split, freeze, then evaluate val once. **Nothing was tuned
on val in this review.**

---

## 45. Unresolved Module-8-only issues

### 45.1 The alias-folding trade-off is now unhedged

Removing the writer's leading-article fold is correct per §§15–16 — a soft hint
must not become hard identity, and reconstructing the evaluator's alias database
is not COVER's job. But it is measurably worse where gold aliases cover both
variants: macro-F1 1.000 → 0.667 in the end-to-end test.

The architecturally clean recovery is **not** to restore the writer fold. It is
for Module 3 to record when two strict candidates came from the *same generation
event* (one view emitting "The Alpha Exchange; Alpha Exchange" is one entity
restated), giving the selector genuine provenance-based identity evidence rather
than a string heuristic. That is a Module-3/8 boundary change and was out of
scope here. **Recommended for the end-to-end milestone.**

### 45.2 `capacity_support_ratio = 1.0` is strict

A rival capacity cluster must match the dominant cluster's support exactly, or
be explicitly verified VALID, to win on height. With sparse evidence this
biases toward the most-recalled figure rather than the highest published one.
A judgement call awaiting calibration.

### 45.3 The cluster threshold is uniform across numeric relations

Area and capacity share 0.025. Published areas and published capacities may
disperse differently; the contract already allows a per-relation override.

---

## 46. All modules have now received an individual review

Modules **0 through 8** have each been reviewed against the proposal in a
dedicated pass, with findings recorded in audits 0003–0011:

| Module | Audit | Verdict |
|---|---|---|
| 0 Relation Compiler | 0003 | pass |
| 1 Typed Program Router | 0004 | pass |
| 2 Diverse Elicitation | 0005 | pass |
| 3 Evidence Graph | 0006 | pass |
| 4 Blind Verifier | 0007 | pass |
| 5 Evidence & Uncertainty State | 0008 | pass |
| 6 RCSE | 0009 | pass |
| 7 Active Controller | 0010 | pass |
| 8 Final Selector | 0011 | pass |

---

## 47. End-to-end conformance is NOT accepted

Individual module conformance is **not** system conformance. None of the
following has been reviewed or accepted:

- the six relations behaving correctly **together** on real queries;
- cross-module interactions beyond the pairwise seams each audit examined;
- any behaviour with the frozen Mistral-24B + Qwen3.5-4B models, which have
  never been run — every result in every audit is scripted and non-neural;
- calibration of the ~40 architecture-default thresholds now inventoried across
  audits 0008, 0009, 0010 and 0011;
- any performance claim whatsoever. No number in this repository is a system
  result.

---

## 48. Recommended next milestone

**Full COVER-KBC End-to-End + Six-Relation Architecture Conformance Freeze.**

It should cover the six relations end to end, the cross-module interactions no
per-module pass could see, the Module-3/8 provenance question in §45.1, and the
train-split calibration protocol for every threshold the module audits recorded
— before any val number is produced.

---

## Verdict

**Module 8 PASSES** after seven defects were found and fixed, including both
findings carried since audits 0007 and 0008.

Emission now follows Module 5's accepted state rather than a second confidence
system — the numeric path no longer force-accepts a cluster Module 5 refused.
Raw frequency cannot re-enter: cluster weight and ranking read acquisition
support, so the verifier and cross-model recall are not paid twice at the last
step. `alias_hint` is no longer promoted to hard output identity, and the writer
removes only what the evaluator itself would collapse. `CANDIDATE_REJECTED` is
reachable, so "we generated nothing" and "we rejected everything" are finally
distinguishable. Capacity prefers a semantically valid maximum over a larger
rejected near miss; area emits one robust km² median with its derived status
recorded; cardinality violations and leaked control tokens fail closed; and the
submission row carries exactly the three official fields.
