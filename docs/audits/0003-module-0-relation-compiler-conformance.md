# Audit 0003 — Module 0 (Relation Compiler): Architecture Conformance Review

**Scope:** Module 0 only. Modules 1+ were inspected *solely* to determine
whether Module 0's fields reach a consumer. **No claim is made that Modules 1+
have been reviewed.**

**Pre-work HEAD:** `3037c1f`, working tree carrying the Milestone-2 changes.
**Date:** 2026-08-03

---

## 1. Proposal requirements

Spec §5.1 — the Relation Compiler "converts an official relation ID into an
executable semantic contract", specifying:

1. answer type and cardinality regime
2. exact positive semantics
3. hard negative / near-miss classes
4. mandatory and optional views
5. parser and normalizer
6. verification templates
7. relation-specific stopping and final-selection policy

Spec §5.3 — "The contract is consumed by several modules simultaneously. It
controls the prompt template, the parser, the evidence types, the candidate
verifier, the action space, and the stopping rule."

Spec §10.5 — the adversarial verifier tier fires when "candidate is a common
near miss **specified by the contract**".

Spec §12.3 — RCSE and stopping are "intentionally relation-specific".

Spec §31.8 — "Relation-specific code implements an interface; no large if/elif
chain should leak throughout the codebase."

Official relation definitions were taken from the organizer README at the
pinned commit `30d8cfa` (development metadata, read outside the repository;
`benchmark/` untouched).

---

## 2. Mismatches found

### 2.1 Dead metadata (spec §5.3 violated)

Grep over `src/cover_kbc/` for each contract field, excluding `contracts/`
itself, showed the following stored-but-ignored:

| Field | Finding |
|---|---|
| `RelationContract.answer_type` | **DEAD** — zero consumers |
| `VerificationPolicy.accept_valid_prob` | **DEAD** — `scoring.decide_status` used the global `ScoringConfig.min_valid_prob` only |
| `VerificationPolicy.drop_on_unknown` | **DEAD** — shadowed by `ScoringConfig.drop_on_unknown` |
| `VerificationPolicy.adversarial_classes` | **DEAD** — spec §10.5 requires it to drive the adversarial tier |
| `StoppingPolicy.max_calls` | **DEAD** — budget came only from `PipelineConfig` |
| `StoppingPolicy.max_generated_tokens` | **DEAD** — same |
| `StoppingPolicy.stability_threshold` | **DEAD** — `should_stop` used `ControllerConfig` |
| `StoppingPolicy.saturation_patience` | **DEAD** — same |
| `StoppingPolicy.residual_stop_threshold` | **DEAD** — same |
| `RelationContract.allows_empty` | **DEAD** — unused property |
| `RelationContract.is_numeric` | **DEAD** — callers duplicated `output_type is OutputType.NUMBER` |

The whole of `StoppingPolicy` was inert: `grep '\.stopping\.'` returned nothing
outside `contracts/`. Since spec §5.1 and §12.3 both make stopping
relation-specific, this was the single largest conformance gap — the system had
*one global* stopping rule while claiming six relation-typed ones.

### 2.2 Semantic gaps against the official definitions

| Relation | Gap |
|---|---|
| `countryLandBordersCountry` | Official: *"Includes only currently-recognised states; deprecated/disputed border statements are not considered."* — **absent** from the contract |
| `hasCapacity` | Official: *"expressed as an integer number of people"* — the integer framing was implicit only |
| `hasArea` | Contract generalised "total area" to every subject; the official scopes total area **to countries** (*"For countries, the total area (land + inland water)"*) |
| `companyTradesAtStockExchange` | Official: *"Subsidiaries that are not separately listed have an empty answer set"* — implied but not stated |

No relation contained a *contradiction* of the official definition. All gaps
were omissions or imprecision.

### 2.3 Not a mismatch (verified correct)

- All six relation ids, program types, output types and cardinality regimes
  match spec Table 1 and the evaluator's `RELATION_TYPE`.
- `countryLandBordersCountry`: land-only ✓, maritime excluded ✓, integral
  overseas territories count ✓, non-integral dependencies excluded ✓, enclaves
  count ✓, islands → empty ✓.
- `personHasCityOfDeath`: city/locality granularity ✓, living → empty ✓,
  birthplace/residence/burial/country excluded ✓.
- `hasCapacity`: maximum spectator capacity ✓, highest published ✓, record
  attendance / average attendance / seated-only excluded ✓.
- `awardWonBy`: exact award ✓, recipient not work ✓, nominee excluded ✓,
  predecessor/successor distinct ✓, rescinded excluded ✓, partial gold noted ✓.
- `companyTradesAtStockExchange`: company itself must be traded ✓,
  parent/subsidiary insufficient ✓, delisted/private → empty ✓.
- `hasArea`: km² ✓, unit conversion ✓, land-only excluded ✓.
- Contracts carry **no factual content** — enforced by an existing test that
  rejects digits in any rule text.

---

## 3. Fixes made

All fixes are wiring or transcription. **No new architectural component, model,
scheduler or scoring scheme was introduced.**

| # | Fix | Follows |
|---|---|---|
| 1 | `answer_type` now emitted in `verifier_definition()` ("Expected answer type: …") | §5.1 "answer type" |
| 2 | New `near_miss_block()`; `adversarial_classes` injected into `TEMPLATE_ADVERSARIAL` via a `{near_misses}` slot | §10.5 "near miss specified by the contract" |
| 3 | `decide_status` and `assign_tier` now resolve thresholds through `resolve_verification()`: the contract is authoritative, the global config is the default for undeclared fields (see §3.1) | §5.1 verification policy |
| 4 | New `resolve_stopping(contract, config) -> EffectiveStopping`; `should_stop` and the STOP action baseline use it | §5.1, §12.3 |
| 5 | `PipelineConfig.budget(contract)` clamps per-query calls/tokens to `StoppingPolicy`, with the global value as a ceiling | §5.1 stopping policy |
| 6 | `ControllerConfig.honor_contract_stopping` (default `True`) keeps a versioned global override | §31.9 |
| 7 | `validate()` now enforces `allows_empty`/`is_numeric` invariants, plus probability and budget sanity | §5.1 |
| 8 | Official semantics transcribed for borders (recognised states, deprecated/disputed claims, worked examples), capacity (integer people), area (country total-area scoping, hectares/sq-mi conversion), stock (subsidiary empty-set) | organizer definitions |

### 3.1 Policy precedence (corrected after review)

An earlier revision of this fix combined contract and global values
arithmetically — `max(global, contract)` for `accept_valid_prob` and
`global AND contract` for `drop_on_unknown` — on the reasoning that a relation
should only ever be allowed to tighten.

**That rule was wrong and has been removed.** It appears nowhere in the
proposal, and it defeats the point of a relation-typed system: it makes the
global value a floor that no relation can go below, so a recall-first relation
can never choose a lower acceptance bar than a precision-first one. The six
programmes would have been forced to converge exactly where they should differ.

The corrected rule, applied uniformly and mirroring
`controller.resolve_stopping`:

| Kind of setting | Precedence | Rationale |
|---|---|---|
| Quality / operating point (`accept_valid_prob`, `auto_accept_independent_support`, `drop_on_unknown`, stopping thresholds) | **contract authoritative**; global used only where the contract declares nothing (`None`) | a relation's precision/recall trade-off is relation semantics, which Module 0 owns |
| Hard resource ceiling (`max_calls`, `max_generated_tokens`) | `min(global, contract)` | a safety/compute limit, not a quality knob: no relation may spend more than the run was budgeted for |

Values are **never** blended. The only way for global settings to displace a
contract is the explicitly named escape hatch
`ScoringConfig.force_global_verification_policy` (default `False`), matching
`ControllerConfig.honor_contract_stopping`. `EffectiveVerification.source`
records which side supplied each value, so a run trace shows the provenance
rather than leaving it to be inferred.

`VerificationPolicy` fields are now `| None` with `None` defaults, so "this
relation has no opinion" is expressible and distinguishable from "this relation
chose the same number as the global default".

**Behaviour impact: none for the current six contracts.** Every declared value
already sat at or above the old global floor, so `max()` was already returning
the contract value and `AND` was already returning the contract boolean. The
refactor removes a latent constraint rather than changing today's numbers —
confirmed by the full suite passing unchanged across the refactor.

---

## 4. Contract-consumer matrix

Fields marked *via* reach downstream through a contract method rather than by
direct attribute access.

| Contract field | Downstream consumer | Test | Status |
|---|---|---|---|
| `relation` | `router.route`, `library.get_view`, `selection._BY_RELATION` | `test_router_matches_the_specification_table` | LIVE |
| `program_type` | `selection._BY_PROGRAM`, `coverage.estimate_residual`, `controller.should_stop` | `test_program_type_selects_the_final_selector`, `test_residual_is_relation_typed` | LIVE |
| `output_type` | `parsing.parse_entities` (type guard), `engine.run_view`, `graph`, `pipeline` | `test_numeric_relations_are_numeric_to_the_evaluator` | LIVE |
| `cardinality` | `max_objects` → `selection.select_*` | `test_cardinality_bounds_the_emitted_set` | LIVE |
| `answer_type` | *via* `verifier_definition()` → verifier + elicitation prompts | `test_answer_type_reaches_the_verifier_prompt` | **LIVE (fixed)** |
| `definition` | *via* `verifier_definition()` | `test_verifier_prompt_carries_contract_rules_and_hides_reasoning` | LIVE |
| `positive_rules` | *via* `verifier_definition()` | as above | LIVE |
| `hard_negative_rules` | *via* `verifier_definition()` | as above | LIVE |
| `mandatory_views` | `pipeline._discover`, `controller.legal_actions`, `coverage._facet_gap`, `should_stop` | `test_unrun_views_become_actions`, `test_never_stops_before_mandatory_views_are_done` | LIVE |
| `optional_views` | *via* `all_views()` → controller, coverage, library | `test_mandatory_views_outrank_optional_facets` | LIVE |
| `normalization.merge_leading_article_variants` | *via* `key()` → `EvidenceGraph._candidate_key` | `test_normalization_policy_reaches_the_identity_key` | LIVE |
| `normalization.max_words` | `parsing.parse_entities` | `test_entity_parsing_handles_common_shapes` | LIVE |
| *(strict key)* | *via* `strict_key()` → `graph.add_entity_mentions` | `test_strict_key_is_exactly_the_evaluator_normalisation` | LIVE |
| `verification.auto_accept_independent_support` | *via* `resolve_verification()` → `scoring.assign_tier` | `test_tier_auto_accept_for_broad_support`, `test_thresholds_are_never_combined_arithmetically` | LIVE |
| `verification.accept_valid_prob` | *via* `resolve_verification()` → `scoring.decide_status` | `test_verification_thresholds_reach_the_decision`, `test_a_relation_may_choose_a_lower_acceptance_bar_than_the_global_default` | **LIVE (fixed)** |
| `verification.drop_on_unknown` | *via* `resolve_verification()` → `scoring.decide_status` | `test_drop_on_unknown_is_not_an_and_of_two_booleans` | **LIVE (fixed)** |
| `verification.adversarial_classes` | *via* `near_miss_block()` → `TEMPLATE_ADVERSARIAL` | `test_near_miss_classes_reach_the_adversarial_template` | **LIVE (fixed)** |
| `stopping.max_calls` | `PipelineConfig.budget` | `test_stopping_policy_bounds_the_query_budget` | **LIVE (fixed)** |
| `stopping.max_generated_tokens` | `PipelineConfig.budget` | as above | **LIVE (fixed)** |
| `stopping.stability_threshold` | `controller.resolve_stopping` → `should_stop` | `test_stopping_policy_reaches_the_controller` | **LIVE (fixed)** |
| `stopping.saturation_patience` | as above | `test_stopping_thresholds_come_from_the_contract_by_default` | **LIVE (fixed)** |
| `stopping.residual_stop_threshold` | as above + STOP action baseline | `test_contract_stopping_actually_drives_the_decision` | **LIVE (fixed)** |
| `stopping.notes` | — | — | **DOCUMENTATION ONLY** (intentional) |
| `selection.min_independent_support` | `scoring.decide_status` | `test_score_components_are_stored_separately` | LIVE |
| `selection.max_objects` | `selection.select_large_open_set`, `max_objects` | `test_cardinality_bounds_the_emitted_set` | LIVE |
| `selection.numeric_cluster_threshold` | `selection._numeric_clusters` | `test_area_uses_the_robust_dominant_cluster_not_the_highest` | LIVE |
| `selection.numeric_integer_only` | `graph.add_numeric_mentions`, `selection._emit_numeric` | `test_capacity_semantics_match_the_official_definition` | LIVE |
| `selection.numeric_target_unit` | `parsing.parse_numeric_values`, `graph` | `test_selection_policy_reaches_parser_graph_and_selector` | LIVE |
| `eligible_independence_groups` | `scoring.support_term`/`contradiction_term`, `graph.coverage_of`, `library` check | `test_structurally_different_views_are_independent_supports` | LIVE |
| `view_families` | `library.check_library_covers_contracts` | `test_view_library_and_contracts_agree` | LIVE |
| `allows_empty` | `validate()` invariant | `test_validate_rejects_an_incoherent_contract` | **LIVE (fixed)** |
| `is_numeric` | `validate()` invariant | as above | **LIVE (fixed)** |

**Result: 0 dead fields remain.** The single non-executable item,
`stopping.notes`, is free-text rationale and is intentionally documentation.

### Spec §5.1 checklist

| Requirement | Status |
|---|---|
| answer type + cardinality regime | ✅ |
| exact positive semantics | ✅ |
| hard negative / near-miss classes | ✅ (both prose rules and named classes now executable) |
| mandatory and optional views | ✅ |
| parser and normalizer | ✅ — the parser is *driven by* the contract (`output_type`, `numeric_target_unit`, `max_words`) rather than being a contract-owned callable; see §7.1 |
| verification templates | ⚠️ partial — the contract supplies the definition block and near-miss classes, but the template *set* is global; see §7.2 |
| relation-specific stopping and final-selection policy | ✅ |

---

## 5. Tests

**279 passed**, 0 failed. `pyflakes` clean. No test loads a heavyweight model.

Added to `tests/test_contracts.py` (19 new tests):

- `test_spec_5_1_required_fields_are_all_present`
- `test_answer_type_reaches_the_verifier_prompt`
- `test_near_miss_classes_reach_the_adversarial_template`
- `test_a_contract_without_near_misses_degrades_cleanly`
- `test_verification_thresholds_reach_the_decision`
- `test_stopping_policy_reaches_the_controller`
- `test_stopping_policy_bounds_the_query_budget`
- `test_the_global_ceiling_still_caps_a_generous_contract`
- `test_selection_policy_reaches_parser_graph_and_selector`
- `test_normalization_policy_reaches_the_identity_key`
- `test_program_type_selects_the_final_selector`
- `test_cardinality_bounds_the_emitted_set`
- `test_validate_rejects_an_incoherent_contract`
- six per-relation semantic tests (`test_*_semantics_match_the_official_definition`)

Added after the precedence review (7 further tests):

- `test_a_relation_may_choose_a_lower_acceptance_bar_than_the_global_default`
- `test_an_undeclared_field_falls_back_to_the_global_default`
- `test_thresholds_are_never_combined_arithmetically`
- `test_drop_on_unknown_is_not_an_and_of_two_booleans`
- `test_the_named_emergency_override_restores_global_policy`
- `test_resolution_source_is_reported_for_the_audit_trail`
- `test_resource_ceilings_are_still_clamped_not_authoritative`

Modified in `tests/test_controller.py`: the old
`test_stopping_thresholds_come_from_config` asserted global precedence, which is
now wrong. Replaced by three tests covering contract precedence, the decision
path, and the explicit global override.

---

## 6. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff -- benchmark/              -> (empty)
```

No organizer file was read-modified or written. The official relation
definitions used for comparison were read from a temporary upstream clone
outside the repository.

---

## 7. Unresolved Module 0 issues

**7.1 The parser is contract-*driven*, not contract-*owned*.** Spec §5.1 lists
"parser and normalizer" as contract contents. The normalizer genuinely is one
(`NormalizationPolicy` with `strict_key`/`alias_hint_key`). The parser is a
module-level function that *reads* contract fields. Behaviour is correct and
relation-specific; the ownership differs from a literal reading of the spec.
Not fixed: making the parser a contract-held callable is a refactor, not a
conformance gap, and would exceed this review's remit.

**7.2 Verifier templates are global, not per-relation.** Spec §5.1 says the
contract specifies "verification templates". Today the contract supplies the
*content* (definition block, near-miss classes) while
`TEMPLATE_STANDARD/QUESTION/ADVERSARIAL` are module-level. No relation currently
needs a structurally different template, so this is latent rather than harmful.
Fixing it properly means letting a contract declare template ids — a Module 4
change, out of scope here.

**7.3 Contracts are Python literals, not versioned YAML.** Spec §5.2 shows a
YAML contract and §31.9 requires thresholds "stored in versioned config, not
hidden in code". Contracts are versioned in git and are the single declared home
of relation semantics, so the intent is met; the serialisation format is not.
Deliberately not changed — moving six contracts to YAML is a structural change
requiring external authorisation.

**7.4 `stopping.notes` is documentation only.** By design, but recorded so it is
not mistaken for an executable field later.

**7.5 Operating points are hand-set, not calibrated.** With the contract now
authoritative, `accept_valid_prob` genuinely determines each relation's
precision/recall trade-off. The current values (0.5 / 0.6) are judgement calls
carried over from Milestone 2, not measurements. They must be calibrated on
`train` and frozen before `val` is scored.

**7.6 Contract semantics are unvalidated against a real model.** Every rule
above is asserted by string-level tests. Whether a real verifier *honours*
"only currently recognised states" is unknown until the first Colab run.

**7.7 `hasArea` non-country subjects.** The official text scopes "total area" to
countries and says only "the surface area of the subject geographic entity"
otherwise. The contract now mirrors this, but for islands and lakes the
land/water distinction is left implicit — as the official definition leaves it.

---

## 8. Git review

Changed in this review:

```
M src/cover_kbc/contracts/base.py       answer_type, near_miss_block, invariants
M src/cover_kbc/contracts/registry.py   official semantics for 4 relations
M src/cover_kbc/verification.py         {near_misses} slot in the adversarial template
M src/cover_kbc/scoring.py              EffectiveVerification / resolve_verification
M src/cover_kbc/controller.py           EffectiveStopping / resolve_stopping
M src/cover_kbc/pipeline.py             budget(contract)
M tests/test_contracts.py               +26 conformance and precedence tests
M tests/test_controller.py              stopping-precedence tests updated
A docs/audits/0003-module-0-relation-compiler-conformance.md
```

`benchmark/` unchanged. No new module, model, or dependency added.

---

## 9. Verdict

Module 0 **now conforms** to spec §5.1 and §5.3, with two documented partials
(§7.1 parser ownership, §7.2 template ownership) and one format deviation
(§7.3 Python instead of YAML). Every contract field except free-text `notes`
has a traced consumer and a regression test.

Modules 1+ have **not** been reviewed. Awaiting external authorisation before
proceeding to Module 1 (Typed Program Router).
