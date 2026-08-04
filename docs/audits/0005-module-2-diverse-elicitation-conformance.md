# Audit 0005 — Module 2 (Diverse Elicitation Engine): Architecture Conformance Review

**Scope:** Module 2 only. Modules 3+ were inspected *solely* to determine whether
Module 2 emits what they need. **No claim is made that Modules 3+ have been
reviewed.**

**Date:** 2026-08-03 (first pass) — **corrected before acceptance** (§25)

---

## 1. Objective and scope

Answer one question against `COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` §7:

> Does the current Module 2 actually implement structurally diverse,
> relation-typed candidate acquisition, or is it mostly a collection of prompt
> strings with duplicated / decorative metadata?

**Verdict: the acquisition repertoire was incomplete and carried one live defect
that silently crippled two of the six relations, plus real semantic duplication
of Module 0 and one dead field.**

The first pass found two of spec Table 5's non-optional view families —
*relation-focused description* and *reverse / alternate framing* — **entirely
absent**, and recorded that as an unresolved item (then §21.1). That was wrong:
Table 5 marks only *Factual-decoding* and *Cross-model* as optional, and §7.3
uses "direct + structural + contrastive + **reverse**" as its own example of
four qualitatively distinct supports. Module 2 could not be declared conformant
with both families missing. **This correction implements them** (§25), so the
proposal's acquisition repertoire now exists and is executable.

---

## 2. Proposal requirements

Spec §7.1 — "The Elicitation Engine is a candidate generator, not the final
answer generator. It is deliberately allowed to be noisy."

Spec §7.2, Table 5 — nine view families: Direct, Structural, Relation-focused
description, Contrastive, Facet, Missingness, Reverse/alternate framing,
Factual-decoding (optional), Cross-model (optional). Every model call carries a
`view_id`, `independence_group`, prompt template, decoding profile and cost
record.

Spec §7.3 — `raw frequency ≠ independent evidence diversity`. "Ten repeats of
the same direct view belong to one evidence family; direct + structural +
contrastive + reverse views represent four qualitatively different supports."

Spec §7.4 — a provenance record per call; "No downstream module should consume a
candidate without provenance."

---

## 3. Pre-work repository state

```
branch : main
HEAD   : 76ba490  refactor: align COVER-KBC semantic plane with architecture
tree   : clean
tests  : 319 passing
benchmark/ : clean
```

Modules 0 and 1 were accepted and were not undone.

---

## 4. Existing Module-2 architecture

Three files, all pre-existing:

- `elicitation/views.py` — `ViewSpec`, `FAMILY_TO_GROUP`, shared system prompt
  and the three output-format blocks.
- `elicitation/library.py` — 22 relation-specific views + the contract
  consistency check.
- `elicitation/engine.py` — `ElicitationEngine.run_view`, which renders,
  generates, parses and builds the `GenerationRecord`.

The engine is correctly a *candidate acquisition* mechanism: it never accepts,
scores or finalises. A test now asserts that (`ACCEPTED`, `decide_status`,
`finalize`, `select_` appear nowhere in Module 2).

---

## 5. Complete relation / view matrix

**28 views**, all ids unique, exact 1:1 with the contracts in both directions.
(22 at the first pass; +6 from the §25 correction, all optional.)

| Relation | view_id | M/O | family | facet_id | independence group |
|---|---|---|---|---|---|
| awardWonBy | `award_direct` | M | direct | award_enumeration | DIRECT_RECALL |
| awardWonBy | `award_facet_temporal` | M | structural | award_temporal | STRUCTURAL_DECOMPOSITION |
| awardWonBy | `award_facet_recipient_type` | M | structural | award_recipient_type | STRUCTURAL_DECOMPOSITION |
| awardWonBy | `award_missing` | M | missingness | award_missingness | MISSINGNESS_SEARCH |
| awardWonBy | `award_facet_category` | O | structural | award_category | STRUCTURAL_DECOMPOSITION |
| awardWonBy | `award_reverse_check` | O | **reverse** | award_reverse_check | **REVERSE_ALTERNATE** |
| awardWonBy | `award_exact_identity_contrast` | O | contrastive | award_exact_identity | CONTRASTIVE_SEPARATION |
| companyTradesAtStockExchange | `stock_listing_gate` | M | gate | stock_listing_gate | EXISTENCE_GATE |
| companyTradesAtStockExchange | `stock_exchange_direct` | M | direct | stock_exchange_direct | DIRECT_RECALL |
| companyTradesAtStockExchange | `stock_description` | O | **description** | stock_description | **RELATION_FOCUSED_DESCRIPTION** |
| companyTradesAtStockExchange | `stock_reverse_check` | O | **reverse** | stock_reverse_check | **REVERSE_ALTERNATE** |
| companyTradesAtStockExchange | `stock_parent_contrast` | O | contrastive | stock_parent_contrast | CONTRASTIVE_SEPARATION |
| countryLandBordersCountry | `borders_direct` | M | direct | borders_direct | DIRECT_RECALL |
| countryLandBordersCountry | `borders_compass` | M | structural | borders_compass | STRUCTURAL_DECOMPOSITION |
| countryLandBordersCountry | `borders_description` | O | **description** | borders_description | **RELATION_FOCUSED_DESCRIPTION** |
| countryLandBordersCountry | `borders_reverse_check` | O | **reverse** | borders_reverse_check | **REVERSE_ALTERNATE** |
| countryLandBordersCountry | `borders_land_vs_maritime` | O | contrastive | borders_land_vs_maritime | CONTRASTIVE_SEPARATION |
| countryLandBordersCountry | `borders_missing` | O | missingness | borders_missing | MISSINGNESS_SEARCH |
| hasArea | `area_direct_km2` | M | direct | area_direct_km2 | DIRECT_RECALL |
| hasArea | `area_total_vs_land` | M | contrastive | area_total_vs_land | CONTRASTIVE_SEPARATION |
| hasArea | `area_alternate_unit` | O | structural | area_alternate_unit | STRUCTURAL_DECOMPOSITION |
| hasCapacity | `capacity_direct` | M | direct | capacity_direct | DIRECT_RECALL |
| hasCapacity | `capacity_contrast` | M | contrastive | capacity_contrast | CONTRASTIVE_SEPARATION |
| hasCapacity | `capacity_configuration` | O | structural | capacity_configuration | STRUCTURAL_DECOMPOSITION |
| personHasCityOfDeath | `death_status_gate` | M | gate | death_status_gate | EXISTENCE_GATE |
| personHasCityOfDeath | `death_city_direct` | M | direct | death_city_direct | DIRECT_RECALL |
| personHasCityOfDeath | `death_description` | O | **description** | death_description | **RELATION_FOCUSED_DESCRIPTION** |
| personHasCityOfDeath | `death_locality_granularity` | O | contrastive | death_locality_granularity | CONTRASTIVE_SEPARATION |

### Mechanism coverage per relation

| Relation | direct | structural | description | contrastive | facet | missingness | reverse | gate | cross-model | m(o) |
|---|---|---|---|---|---|---|---|---|---|---|
| countryLandBordersCountry | ✅ | ✅ | ✅ | ✅ | – | ✅ | ✅ | – | available | **6** |
| companyTradesAtStockExchange | ✅ | – | ✅ | ✅ | – | – | ✅ | ✅ | available | **4** |
| personHasCityOfDeath | ✅ | – | ✅ | ✅ | – | – | – | ✅ | available | **3** |
| hasArea | ✅ | ✅ | – | ✅ | – | – | – | – | available | **3** |
| hasCapacity | ✅ | ✅ | – | ✅ | – | – | – | – | available | **3** |
| awardWonBy | ✅ | ✅ | – | ✅ | ✅ (3) | ✅ | ✅ | – | available | **5** |

Cross-model recall is "available" for every relation: it re-runs a declared view
on the second model family under `CROSS_MODEL_RECALL`, so it needs no view of
its own.

Not every relation carries every mechanism, by design (§25.3 records the
per-relation justifications).

## 6. Mandatory vs optional view analysis

The distinction is operationally real, verified by execution rather than by
reading:

| Path | Optional views run in Phase A? |
|---|---|
| default (`PipelineConfig()`) | **no** — measured for all six relations |
| `enable_active_controller=True` (frozen target config) | **no** — the controller ran 4 of 6 award views and chose no optional one |
| `run_optional_views=True`, controller off | yes — by explicit named flag |

The third path is the spec §27.2 "Multi-view" ablation rung, not the default and
not what the frozen config executes. Optional views are never unreachable: a
test asserts `contract.optional_views ⊆ controller.legal_actions` for every
relation, so they remain *selectable* by Module 7 while never being forced.

**Wart (not a defect):** `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`
sets both `run_optional_views: true` and `enable_active_controller: true`. The
former is silently ignored when the controller is on. Left as-is — changing the
frozen config is out of scope — but recorded in §21 because it misleads a reader.

---

## 7. Structural-diversity analysis

**No fake diversity found.** Every view is a distinct acquisition mechanism; no
relation counts paraphrases of one question as independent evidence. Two tests
enforce this: each relation uses ≥2 distinct candidate families, and no two views
of one relation share a `(family, facet)` pair.

| Mechanism | Present | Relations |
|---|---|---|
| Direct recall | ✅ 6 views | all six |
| Structural decomposition | ✅ 6 views | borders (compass), area (alternate unit), capacity (configurations), awards (3 facets) |
| Contrastive | ✅ 6 views | all six |
| Facet | ✅ as `facet_id` within STRUCTURAL | awards (temporal / recipient-type / category) |
| Missingness | ✅ 2 views | borders, awards |
| Existence gate | ✅ 2 views | stock, death |
| Cross-model recall | ✅ via `independence_group` override | any (Phase B) |
| **Relation-focused description** | ✅ 3 views (§25.1) | borders, stock, death |
| **Reverse / alternate framing** | ✅ 3 views (§25.2) | borders, stock, awards |
| Factual decoding (DoLa) | ❌ absent — **explicitly optional** in Table 5 | — |

Every Table 5 family that the proposal does **not** mark optional is now
implemented. Only *Factual-decoding* remains absent, which Table 5 itself marks
"optional" and the spec §8 confines to an experimental branch behind a feature
flag.

**Facet as a sub-partition, not a family.** Spec Table 5 lists Facet as its own
family. We model it as `facet_id` *within* `STRUCTURAL_DECOMPOSITION`. That is a
deliberate and stronger choice: a separate family would map to a separate
independence group, so award's three facets would read as three independent
corroborations when they are three slices of one decomposition mechanism. The
current design keeps them separable in traces while counting as one support.

---

## 8. View / facet / run / independence identity analysis

The five concepts are distinct and tested to be so:

| Concept | Meaning | Distinctness test |
|---|---|---|
| `view_id` | concrete acquisition procedure | 22 unique ids |
| `view_family` | Table 5 mechanism class | `FAMILY_TO_GROUP` is 1:1 and un-overridable |
| `facet_id` | semantic subspace inside one mechanism | award's 3 facets: 3 facet_ids, 1 group |
| `independence_group` | downstream evidence class | derived from family only |
| `run_id` | repeated execution of one procedure | 3 repeats → 3 run_ids, 1 view_id, 1 group |

The §7.3 invariant is now executable rather than merely asserted in a docstring:
four repeats of `borders_direct` produce `raw_support_count == 4` and
`independent_support == 1`.

---

## 9. Generated-context trust-boundary analysis

Three relation-focused description views now exist (§25.1). The boundary the
brief warns about is enforced structurally, not by convention:

```
frozen LM
   -> stage 1: prose describing the relation for this subject
   -> stage 2: extraction that may read ONLY that prose
   -> candidate mentions
   -> Evidence Graph (ordinary SUPPORT edges)
   -> ordinary scoring and later independent verification
```

What makes it a real boundary:

- **The prose is never a candidate.** The stage-1 record carries
  `parsed_values == []` and produces no evidence edge. It is recorded purely so
  the chain is auditable.
- **The extraction is constrained to the context.** Stage 2's template must
  contain `{context}`; `ViewSpec.validate()` raises otherwise, with the message
  "the extraction stage must consume '{context}', otherwise it is a renamed
  direct prompt". Stage 1 additionally forbids list output, so it cannot
  degenerate into a second direct-recall call.
- **Both stages are one mechanism.** Same `view_id`, same
  `RELATION_FOCUSED_DESCRIPTION` group; they differ only by `stage`
  (`"description"` / `"extraction"`). Two calls never read as two supports.
- **The chain is recoverable.** The extraction record carries
  `source_record_id` pointing at the description record that produced it, plus
  model id, run id, prompt hashes and token counts for both stages.
- **Nothing is auto-accepted.** A candidate extracted from generated prose
  enters at `UNRESOLVED` with `independent_support == 1`, exactly like any other
  single-mechanism candidate.

Tested by `test_generated_prose_is_never_itself_a_candidate`,
`test_extracted_candidates_enter_the_ordinary_pipeline`,
`test_context_and_extraction_provenance_stay_linked`,
`test_description_is_genuinely_two_stage_not_a_renamed_direct_prompt`.

## 10. Cross-model recall vs verifier boundary

Distinct and un-conflatable:

| Event | independence group | evidence mode |
|---|---|---|
| Qwen independently recalls "Delta" (shown no candidates) | `CROSS_MODEL_RECALL` | `INDEPENDENT_RECALL` |
| Qwen is shown "Alpha" and answers VALID | `BLIND_VERIFIER` | `SHOWN_CANDIDATE` |

Acquisition-side: `ElicitationEngine.run_view(independence_group=...)` lets the
*same discovery prompt* be recorded under a different mechanism when executed by
the second model family, with `model_family` stamped from the runtime spec. A
test asserts a cross-model recall carries `INDEPENDENT_RECALL` and never lands
in `BLIND_VERIFIER`.

### Reverse / alternate framing is a third, separate thing

The correction added a candidate-conditioned acquisition mechanism (§25.2).
It must not be confused with either of the above:

| | asks | returns | group | owner |
|---|---|---|---|---|
| direct recall | "list the objects of X" | free text | DIRECT_RECALL | Module 2 |
| **reverse framing** | "does *Y* hold for X?" | **free text** naming Y or NONE | **REVERSE_ALTERNATE** | Module 2 |
| blind verifier | shows Y, offers A/B/C | **label logits** | BLIND_VERIFIER | Module 4 |

Reverse framing is candidate-*conditioned* but not verification: it has no fixed
label set, no logit read-out and no calibration, and its output is parsed by the
ordinary entity parser into an ordinary candidate mention. A test asserts the
rendered reverse prompt contains no `A = VALID` / `B = INVALID` block and never
uses the `BLIND_VERIFIER` group.

---

## 11. Prompt / RelationContract responsibility boundary

The rule applied, per brief §10:

- **Module 2 owns structural instruction** — "work through the frontier one
  direction at a time", "take each recipient type in turn", "give the value in
  square miles".
- **Module 0 owns semantics** — which exclusions apply. Views that reason about
  exclusions inject `{definition}` (which carries the contract's positive rules
  *and* hard negatives) instead of restating them.

Direct-recall views deliberately do **not** inject the definition: spec Table 5
calls Direct "cheapest baseline factual recall", and loading it with the full
rule set would turn it into a contrastive view, destroying the structural
distinction between the two.

One judgement call recorded: `death_city_direct` retains "output NONE rather
than guessing". That overlaps the contract's "still living ⇒ empty" rule, but it
is an *acquisition-behaviour* instruction (do not fabricate) rather than a
semantic rule, and removing it would reintroduce the exact hallucination the
official baseline exhibits. Kept.

---

## 12. Provenance field matrix

Spec §7.4 requires per-call traceability. Measured on a real engine call:

| Field | Status |
|---|---|
| `record_id`, `view_id`, `view_family`, `independence_group`, `facet_id`, `run_id` | populated |
| `query` (subject + relation) | populated |
| `model_id`, `model_family`, `model_role` | populated |
| `prompt`, `prompt_hash` | populated |
| `raw_output`, `parsed_values` | populated |
| `decode_profile` (name, temperature, top_p, max_new_tokens, seed) | populated |
| `prompt_tokens`, `generated_tokens` | populated |
| `latency_ms` | **nullable — documented**: the scripted stub has no clock; `HuggingFaceRuntime` populates it |
| `error` | `None` on success, populated on backend failure |

No silent provenance loss: a test walks a full pipeline run and asserts every
emitted candidate carries `record_ids`, `facet_ids`, and edges with
`record_id` + `view_id` + `model_family`.

---

## 13. Scattered / duplicated semantics found

### 13.1 Gates counted as acquisition mechanisms — **live defect**

`stock_listing_gate` and `death_status_gate` were classified `ViewFamily.STRUCTURAL`.
A gate returns a YES/NO/UNKNOWN verdict and **produces no candidates**, yet its
family made `STRUCTURAL_DECOMPOSITION` an *eligible independence group* for those
two contracts. Measured consequences before the fix:

| Relation | m(o) | groups that can actually produce candidates | F(o) ceiling |
|---|---|---|---|
| companyTradesAtStockExchange | 3 | 2 | **0.67** |
| personHasCityOfDeath | 3 | 2 | **0.67** |
| (other four) | 3–4 | 3–4 | 1.00 |

So `q(o) = g(o)/m(o)` and `F(o)` could never reach 1.0 for exactly the two
gated relations, and — sharper — with `auto_accept_independent_support = 3`
against 2 reachable mechanisms, **the AUTO_ACCEPT tier was unreachable**: every
stock and death candidate was forced into VERIFY regardless of evidence.

### 13.2 Module-0 semantics copied into 8 templates

Eight views restated the contract's hard negatives inside their own prompt text.
Five *also* injected `{definition}`, so the same exclusion was stated twice in
one prompt; three (`stock_listing_gate`, `death_status_gate`, `award_missing`)
stated contract semantics **without** injecting the definition at all — a pure
drift risk, since editing the contract would not have updated them.

Probes found: `maritime`, `record attendance`, `average attendance`,
`seated-only`, `parent`, `subsidiary`, `land-only`, `nominee`, `rescinded`,
`predecessor`, `birth`, `burial`.

### 13.3 `ViewSpec.runs` was dead metadata

Declared on every view, consumed nowhere. `run_id` was hard-coded to `0` at
every call site, so the repetition-vs-independence machinery the spec makes
central was never exercised outside unit tests.

### 13.4 Not defects (verified)

- No factual answers, and no digits at all, in any template.
- No retrieval reachable: an AST walk over all four Module-2 files finds no
  import of `requests`, `urllib`, `httpx`, `aiohttp`, `socket`, `wikipedia` or
  `wikidata`.
- The system prompt states the closed-book rule explicitly.
- Parser routing is driven by `contract.output_type`; numeric relations never
  reach the entity list parser (`"35,000"` parses to `35000.0`, not `35`/`000`).
- Backend failures are captured on the record, not raised.

---

## 14. Hidden scheduling / repetition found

**None.** All 22 shipped views have `runs == 1`; there is no hard-coded sampling
count, no per-relation loop, and no "if award: run everything three times".
Tests assert `{v.runs for v in VIEW_LIBRARY.values()} == {1}` and that the
engine's source contains no `optional_views`, `residual`, `should_stop` or
`marginal_yield` — i.e. no controller responsibility has leaked into Module 2.

Repeated stochastic sampling is **not** the defining mechanism: it is opt-in per
view, currently unused, and now guarded so that `runs > 1` with greedy decoding
is a configuration error rather than a silent no-op. This is the ReWiSe boundary
of brief §16, held.

---

## 15. Fixes made

| # | Fix | Follows |
|---|---|---|
| 1 | New `ViewFamily.GATE` + `IndependenceGroup.EXISTENCE_GATE`; both gates reclassified | §7.2 — a gate is not one of Table 5's acquisition families |
| 2 | `eligible_groups_for()` skips non-candidate families, so gates cannot enter `m(o)` | §7.3 / §11.1 |
| 3 | Stock and death contracts declare `ViewFamily.GATE` and drop `STRUCTURAL` from their eligible groups → F(o) ceiling restored to 1.00 for all six relations | §7.3 |
| 4 | `check_library_covers_contracts()` now rejects a gate outside the GATE family **and** any eligible group no candidate-producing view can reach | §31.8 |
| 5 | `ViewSpec.runs` consumed: `ElicitationEngine.run_view_repeats()` emits `run_id` 0…n-1 under one `view_id`/group; the pipeline uses it | §7.3 |
| 6 | `ViewSpec.validate()` — `runs ≥ 1`, `runs > 1` requires sampled decoding, gates must declare GATE | no dead/incoherent metadata |
| 7 | Module-0 exclusions removed from 8 templates; `{definition}` injected where semantics had been hand-copied | §10 boundary |

Behaviour unchanged for the shipped configuration except where the defect was:
all views still `runs == 1`, and the only functional change is that stock and
death can now reach full coverage.

**Deliberately not done:** no new view families invented; no prompt wording
optimisation; no change to Module 3+ scoring, RCSE, controller or selectors.

---

## 16. Files created / modified

**Created (3)**
- `tests/test_elicitation.py` (63 tests)
- `configs/experiments/ablation_fixed_multiview.yaml` (§25.4)
- `docs/audits/0005-module-2-diverse-elicitation-conformance.md`

**Modified (5)**
- `src/cover_kbc/types.py` — `ViewFamily.GATE`, `IndependenceGroup.EXISTENCE_GATE`
- `src/cover_kbc/elicitation/views.py` — `CANDIDATE_FAMILIES`, `ViewSpec.validate()`, `runs` documented
- `src/cover_kbc/elicitation/library.py` — gates reclassified, 8 templates de-duplicated, two new consistency checks
- `src/cover_kbc/elicitation/engine.py` — `run_view_repeats()`
- `src/cover_kbc/contracts/base.py` — `eligible_groups_for()` skips non-candidate families
- `src/cover_kbc/contracts/registry.py` — gated contracts declare GATE, drop the unreachable group
- `src/cover_kbc/pipeline.py` — discovery honours `view.runs`; dispatches
  two-stage description views; guards candidate-conditioned views
- `src/cover_kbc/controller.py` — reverse views excluded from subject-only
  action enumeration (smallest interface change; see §22.4)
- `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`,
  `configs/experiments/smoke_staged_scripted.yaml` — `run_optional_views`
  removed (§25.4)
- `tests/test_evidence.py`, `tests/test_pipeline.py`, `tests/test_programs.py`,
  `tests/test_verification.py` — expectations derived from the contract rather
  than hard-coded denominators

**`benchmark/` — untouched.**

---

## 17. Commands executed

```bash
git status / branch --show-current / log --oneline -10 / diff --stat
pdftotext -layout COVER_KBC_V2_ARCHITECTURE_SPEC.pdf     # §4-§9, §15-20, §26, §31
grep -rn "\.runs\b|run_id=" src/ tests/                   # dead-field probe
python -c "...VIEW_LIBRARY vs contract.hard_negative_rules..."   # duplication probe
python -c "...eligible groups vs candidate-producing views..."   # coverage probe
python -c "...resolve_verification vs reachable mechanisms..."   # AUTO_ACCEPT probe
python -c "...CoverPipeline(...).enumerate_query(...)..."        # mandatory/optional probe
python -m pytest -q
python -m pyflakes src/ tests/ scripts/
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

---

## 18. Test results

**383 passed**, 0 failed (319 before this review; 365 after the first pass; +18
from the correction). `pyflakes` clean. The staged scripted smoke run completes
all three phases, and the new fixed-multi-view ablation config runs. No test
loads a heavyweight model.

Three tests failed on first run during the first pass and one more during the
correction; **all four were genuine catches, not test bugs**:
`death_status_gate` still hand-copied contract semantics; one probe matched the
word "requests" in a docstring (rewritten as an AST import walk); and the
correction surfaced the F(o)-denominator interaction recorded in §22.1.

Brief §18 coverage:

| Required | Test |
|---|---|
| contract ↔ view 1:1 | `test_every_declared_view_has_exactly_one_implementation`, `test_every_implemented_view_is_declared_by_its_contract` |
| unique view ids | `test_view_ids_are_unique` |
| mandatory/optional distinction | `test_default_discovery_runs_only_mandatory_views` |
| optional not run in initial discovery | same, plus `test_the_controller_path_does_not_force_every_optional_view` |
| unknown view fails loudly | `test_unknown_view_fails_loudly_with_no_fallback_prompt` |
| regimes get only legal families | `test_null_single_gets_no_open_set_facet_explosion`, `test_large_open_set_exposes_facet_and_missingness_acquisition` |
| repetition ≠ new view identity | `test_repeating_one_view_does_not_create_a_new_view_identity` |
| `view_id` ≠ `run_id` | same |
| `facet_id` ≠ `independence_group` | `test_view_id_facet_id_and_independence_group_are_distinct_concepts` |
| generated context cannot bypass verification | §9 — no such view exists; `test_elicitation_never_finalises_an_answer` |
| cross-model recall ≠ shown-candidate | `test_independent_recall_and_shown_candidate_cannot_be_conflated` |
| numeric relations use numeric parsing | `test_numeric_relations_are_parsed_numerically` |
| NULL_SINGLE no facet explosion | `test_null_single_gets_no_open_set_facet_explosion` |
| award exposes facet/missingness | `test_large_open_set_exposes_facet_and_missingness_acquisition` |
| no benchmark answers in views | `test_view_library_holds_no_benchmark_answers` |
| provenance survives into candidates | `test_provenance_survives_into_the_candidate_record` |
| malformed generations fail safely | `test_malformed_output_yields_no_candidates_but_keeps_provenance` |
| scripted runtime exercises the path | `test_provenance_survives_into_the_candidate_record` (full pipeline) |

Correction-specific coverage (brief §11 A–F, §12 A–G):

| Required | Test |
|---|---|
| description is multi-stage, not a renamed direct prompt | `test_description_is_genuinely_two_stage_not_a_renamed_direct_prompt` |
| generated prose is not accepted as evidence | `test_generated_prose_is_never_itself_a_candidate` |
| extracted candidates enter the ordinary pipeline | `test_extracted_candidates_enter_the_ordinary_pipeline` |
| context/extraction provenance linked | `test_context_and_extraction_provenance_stay_linked` |
| repeated description runs = one mechanism | `test_repeated_description_runs_remain_one_mechanism` |
| description is optional | `test_description_views_are_optional_not_mandatory` |
| reverse is candidate-conditioned | `test_reverse_acquisition_is_candidate_conditioned` |
| reverse ≠ direct recall | `test_reverse_is_structurally_different_from_direct_recall` |
| reverse ≠ blind verifier | `test_reverse_is_not_the_blind_verifier` |
| reverse cannot finalise a candidate | `test_reverse_output_cannot_finalise_a_candidate` |
| reverse provenance chain | `test_reverse_provenance_records_the_full_chain` |
| repeated reverse = one group | `test_repeating_a_reverse_check_stays_one_mechanism` |
| reverse not forced | `test_reverse_is_not_forced_for_every_candidate_or_query` |

---

## 19. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff -- benchmark/              -> (empty)
git diff --cached -- benchmark/     -> (empty)
```

---

## 20. Challenge-compliance impact

None adverse. Module 2 is prompt construction plus parsing; it adds no
inference-time parameters. The frozen pairing is unchanged:

```
mistralai/Mistral-Small-3.2-24B-Instruct-2506   24,011,361,280   enumerator
Qwen/Qwen3.5-4B                                  4,659,865,088   verifier
--------------------------------------------------------------------
total                                           28,671,226,368   < 32B
```

Closed-book compliance is now *tested* rather than assumed: an AST walk proves
no retrieval library is importable from any Module-2 file, and the shared system
prompt states the rule to the model. No model was downloaded, loaded or run.

---

## 21. Unresolved Module-2-only issues

**21.1 Facet is modelled as a sub-partition, not a Table 5 family.** Justified
in §7 — promoting it would inflate award independence three-fold — but it is a
documented divergence from the literal table.

**21.2 Factual-decoding (DoLa) is absent.** Table 5 marks it optional and spec
§8 confines it to an experimental branch that "must be disabled cleanly by
configuration" and that "the system architecture must not depend on". Correctly
deferred, not a conformance gap.

**21.3 Repetition is implemented but unused.** Every view ships `runs == 1`, so
the amplification path has unit-test coverage only. Whether any view benefits
from sampled repeats is an empirical question for the first Colab run.

**21.4 `latency_ms` is null under the scripted runtime.** By design (no clock in
a stub), and populated by `HuggingFaceRuntime`, but it means offline traces
cannot be used for cost analysis.

**21.5 The new mechanisms are untested against a real model.** Whether a
description-first prompt actually surfaces recall a direct prompt misses, and
whether reverse framing catches false positives, are empirical questions. The
architecture is in place; the value is unmeasured.

## 22. Future-review notes (NOT fixed here)

**22.1 Module 4 — `auto_accept_independent_support` is unreachable for two
relations.** After the §13.1 fix, stock and death have exactly **2** reachable
acquisition mechanisms, but their contracts set the auto-accept threshold to
**3**. AUTO_ACCEPT therefore remains unreachable for both, forcing every
candidate into VERIFY. The value is Module-0 contract data consumed by Module 4
tiering, so it is left for that review — but it is a live behavioural defect and
should be resolved there, not deferred further. The likely intent was "all
mechanisms agree", which is now 2.

**22.2 Module 7 — `RUN_VIEW` carries a mandatory-ness bonus keyed on action
type** (carried over from audit 0004 §17.1).

**22.3 Module 7 — `ActionType.RESAMPLE` is defined and scored but never
enumerated.** Now more relevant: with `run_view_repeats` implemented, RESAMPLE
has a real execution path for the first time.

**22.4 Module 7 — no candidate-conditioned action exists.** `run_reverse_view`
is an executable Module-2 primitive, but the controller's action space is
subject-only, so reverse views are deliberately excluded from `legal_actions`
(they would otherwise be dispatched without a candidate and fail). Module 7
needs a `REVERSE_CHECK(candidate)` action before reverse framing can be
*scheduled* rather than merely *called*. This is the concrete Module-7 item the
brief asked to be recorded.

**22.5 Module 5 — `F(o)` normalises by declared mechanisms, not executed ones.**
`scoring.support_term` divides by `contract.coverage_denominator()`, the count of
*declared* eligible groups. Adding the two missing families therefore raised
`m(o)` for borders from 4 to 6, so a candidate supported by one mechanism fell
from `1/4 = 0.25` to `1/6 = 0.167` — below `accept_score = 0.20`.

Spec §11.1 defines `m(o)` as "the number of groups **capable of expressing that
candidate**", which is arguably the number available *in that run*, not the full
declared catalogue. As written, declaring more optional mechanisms penalises
every candidate, which is the wrong incentive.

Measured impact: **no shipped configuration is affected.** Both configs that run
inference enable the active controller, which reaches 4 of 6 mechanisms for
borders (`independent_support = 4`, `F(o) = 0.67`). Only bare `PipelineConfig()`
— used in unit tests, not by any experiment — hits the threshold. Recorded here
rather than fixed, because `S(o)` is Module 5's and the brief forbids tuning it.
`test_single_mechanism_support_is_scored_against_all_declared_mechanisms`
documents the behaviour so it cannot regress unnoticed.

---

## 23. Module 3+ remains unreviewed

Modules 3 (Atomic Normalization + Evidence Graph), 4 (Blind Verifier), 5
(Evidence/Uncertainty State), 6 (RCSE), 7 (Active Controller) and 8 (Final
Selector) have **not** been reviewed. They were inspected only far enough to
confirm Module 2 hands them correct provenance.

---

## 24. Recommended next review

**Module 3 — Atomic Normalization + Candidate–Facet Evidence Graph**, pending
external authorisation.

---

## 25. Correction before acceptance — missing Table 5 families

The first Module-2 pass classified two proposal view families as "absent, and
out of scope to invent" (then §21.1). **That was a misreading.** Spec Table 5
marks only *Factual-decoding* and *Cross-model* as optional; *relation-focused
description* and *reverse / alternate framing* carry no such marker, and §7.3
names reverse explicitly in "direct + structural + contrastive + reverse …
represent four qualitatively different supports". Module 2 could not be closed
with both missing. This section records what was implemented to resolve it.

### 25.1 Relation-focused description — implemented

Table 5's purpose: *"trigger parametric memory before extraction"*, with the
examples *"describe only the geographic boundary / trading status / death
locality"*. Implemented on exactly those three relations:

| Relation | stage 1 asks for | justification |
|---|---|---|
| `countryLandBordersCountry` | the shape of the land frontier and what lies across each stretch | Table 5's "geographic boundary" |
| `companyTradesAtStockExchange` | ownership and trading status in prose | Table 5's "trading status" |
| `personHasCityOfDeath` | the last period of life and circumstances of death | Table 5's "death locality" |

Not added to `hasArea`, `hasCapacity` or `awardWonBy`: a scalar has no useful
prose precursor beyond the direct question, and an award description describes
the *award*, not its recipients, so it would not surface recipient recall.

Mechanism: `ViewSpec.description_template` + `ElicitationEngine.run_description_view()`,
returning `(description, extraction)`. Trust boundary in §9.

### 25.2 Reverse / alternate framing — implemented

Table 5's purpose: *"obtain structurally different evidence"*, example *"Does
candidate Y share a land border with X?"*. Implemented on the three multi-object
entity relations where candidate-conditioned re-asking genuinely differs from
enumeration:

| Relation | asks |
|---|---|
| `countryLandBordersCountry` | does *candidate* share a land boundary with *subject*? (Table 5's own example) |
| `companyTradesAtStockExchange` | are shares of *subject* itself traded on *candidate*? |
| `awardWonBy` | did *candidate* receive the *subject* award itself? |

Not added to `hasArea` / `hasCapacity`: asking "is 5000 the area of X?" is
verification, not acquisition, and Module 4 owns that. Not added to
`personHasCityOfDeath`: with at most one object and two competing localities, a
candidate-conditioned re-ask collapses into the same judgement the blind
verifier makes.

Mechanism: `ViewSpec.is_reverse` + `ElicitationEngine.run_reverse_view(…, candidate)`.
Distinction from the blind verifier in §10.

### 25.3 Independence accounting after the correction

Two new candidate-producing groups: `RELATION_FOCUSED_DESCRIPTION` and
`REVERSE_ALTERNATE`. Every eligible group remains reachable by a
candidate-producing view, and `EXISTENCE_GATE` is still in no contract's
eligible set:

| Relation | m(o) before | m(o) after | reachable | gate excluded |
|---|---|---|---|---|
| countryLandBordersCountry | 4 | **6** | 6 ✅ | ✅ |
| companyTradesAtStockExchange | 2 | **4** | 4 ✅ | ✅ |
| personHasCityOfDeath | 2 | **3** | 3 ✅ | ✅ |
| awardWonBy | 4 | **5** | 5 ✅ | ✅ |
| hasArea | 3 | 3 | 3 ✅ | ✅ |
| hasCapacity | 3 | 3 | 3 ✅ | ✅ |

Independence rules preserved: repeated description runs share one group,
repeated reverse checks share one group, and award facets keep the accepted
sub-partition treatment (three facets, one `STRUCTURAL_DECOMPOSITION`).

Side effect worth noting: stock's `auto_accept_independent_support = 3` is now
**reachable** (4 mechanisms, was 2), and death's is exactly reachable (3 of 3).
The §22.1 Module-4 finding is therefore partly relieved as a consequence of
correct Module-2 classification — but the thresholds themselves were **not**
touched, per the brief.

### 25.4 Frozen-config wart fixed

`configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` set both
`run_optional_views: true` and `enable_active_controller: true`, where the
former is silently ignored. `run_optional_views` is now **removed** from the
target config (and from the staged smoke config), with a comment stating that
the controller owns optional-view scheduling. A dedicated
`configs/experiments/ablation_fixed_multiview.yaml` carries the flag for spec
§27.2's "Multi-view" ablation rung, where it is the only place it has effect.

### 25.5 Claim boundary unchanged

COVER does not claim to invent description-first context, reverse questioning or
self-consistency. These are acquisition mechanisms drawn from prior work. The
contribution remains their use inside relation-typed active evidence
acquisition, independence-aware evidence, calibrated verification and
coverage-guided control. No research claim was rewritten.

---

## 26. Final verdict

Module 2 **conforms** to spec §7.

- The acquisition repertoire is complete: every Table 5 family the proposal does
  not mark optional is implemented and executable. Only *Factual-decoding*
  remains absent, which Table 5 itself marks optional.
- Structural diversity is real, not paraphrase counting: 28 views, all unique,
  exact 1:1 with contracts, no two views of one relation sharing a
  `(family, facet)`.
- Mandatory and optional acquisition are operationally distinct and measured.
- Repetition is repetition: four repeats give `raw_support_count = 4`,
  `independent_support = 1`.
- Generated prose is an intermediate artifact, never evidence.
- Reverse framing, cross-model recall and blind verification are three
  separately-provenanced mechanisms.
- Module-0 semantics are no longer duplicated in any template.
- Every elicitation call is traceable; the one nullable field is documented.
- No factual lookup, no retrieval, no heavyweight model activity.

Two live defects found and fixed (gate misclassification, semantic duplication),
one dead field made executable, one misreading corrected before acceptance.
