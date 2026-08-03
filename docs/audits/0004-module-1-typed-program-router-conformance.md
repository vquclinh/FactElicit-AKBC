# Audit 0004 — Module 1 (Typed Program Router): Architecture Conformance Review

**Scope:** Module 1 only. Modules 2+ were inspected *solely* to answer "does
Module 1 correctly reach and drive this consumer?". **No claim is made that
Modules 2+ have been reviewed.**

**Date:** 2026-08-03

---

## 1. Objective and scope

Review the existing Module 1 against `COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` §6
(and §4 for its place in the semantic plane), and answer one question:

> Does Module 1 provide one authoritative typed-program abstraction that
> downstream COVER modules consume, or is `ProgramType` only a label whose
> semantics are duplicated across the repository?

**Verdict: it was a label with an unguarded contract.** Routing itself was
correct in every respect; what was missing was (a) any assertion that a
relation's declared regime agrees with its cardinality and output type, and
(b) one authoritative statement of what each regime *is*. Both are fixed.

---

## 2. Proposal requirements

Spec §6 defines the router as "a deterministic mapping from relation to an
inference program. No learned classifier is needed because only six relation
IDs exist." Table 4 gives the four regimes and their core behaviour:

| Program | Relations | Core behaviour (spec Table 4) |
|---|---|---|
| SMALL_SET | borders, stock exchange | small candidate universe; precision-aware verification; fast stopping |
| NULL_SINGLE | city of death | existence gate first; zero-or-one final object; explicit abstention |
| NUMERIC | area, capacity | semantically diverse scalar recalls; deterministic normalization; robust clustering |
| LARGE_OPEN_SET | awards | recall-first facet exploration followed by atomic verification and saturation control |

Spec §4.1 places the router in the semantic plane:
`Relation Compiler → Typed Program Router → executable relation contract`.

Spec §31.8: "Relation-specific code implements an interface; no large if/elif
chain should leak throughout the codebase."

---

## 3. Pre-work repository state

```
branch : main
HEAD   : 584de69  logit-calibrated cover-core
tree   : Module-0 review changes uncommitted (8 files) + audit 0003 untracked
tests  : 279 passing
```

Module 0's policy-precedence corrections were left untouched, as instructed.

---

## 4. Existing Module-1 implementation

`src/cover_kbc/contracts/router.py`, 82 lines:

- `PROGRAM_BY_RELATION` — the spec Table 4 mapping, used as a cross-check.
- `route(relation)` — returns `get_contract(relation).program_type`.
- `compile_query(subject, relation, row_index)` — Module 0 + Module 1 entry point.
- `check_router_consistency()` — asserts contracts agree with Table 4 and with
  the official evaluator's `RELATION_TYPE`, and validates every contract.

The contract is the source of truth; `PROGRAM_BY_RELATION` is a deterministic
consistency assertion over it. Spec §5 of the review brief permits exactly this
("one source of truth **or** a deterministic consistency assertion"), and
`check_router_consistency()` is invoked at the start of both
`scripts/run_cover.py` and `scripts/run_staged.py`, so the assertion actually
runs before inference.

---

## 5. Six-relation routing table

| Relation | Routed programme | Contract agrees | Spec Table 4 agrees |
|---|---|---|---|
| `countryLandBordersCountry` | SMALL_SET | ✅ | ✅ |
| `companyTradesAtStockExchange` | SMALL_SET | ✅ | ✅ |
| `personHasCityOfDeath` | NULL_SINGLE | ✅ | ✅ |
| `hasArea` | NUMERIC | ✅ | ✅ |
| `hasCapacity` | NUMERIC | ✅ | ✅ |
| `awardWonBy` | LARGE_OPEN_SET | ✅ | ✅ |

Exactly six relations; each maps to exactly one programme; no relation maps to
two. Verified against a copy of Table 4 transcribed independently in
`tests/test_programs.py`.

**Failure behaviour:** an unknown relation id raises `UnknownRelationError`
from `get_contract`. There is no default programme and no fallback branch.
Tested with `""`, wrong-case (`"hasarea"`), a retired relation
(`"seriesHasNumberOfEpisodes"`) and free text.

**Non-neural:** a source-level test asserts neither `router.py` nor
`programs.py` references `generate(`, `score_labels`, `LMRuntime`, `tokenizer`
or `torch`. Routing is a dict lookup — deterministic, stateless, reproducible.

---

## 6. Four-program semantic table

Now stated once, in `src/cover_kbc/contracts/programs.py`:

| Programme | Allowed cardinality | Required output | Regime cap | Missingness |
|---|---|---|---|---|
| SMALL_SET | `ZERO_OR_MANY_SMALL` | ENTITY | unbounded (contract decides) | yes |
| NULL_SINGLE | `ZERO_OR_ONE` | ENTITY | 1 | no |
| NUMERIC | `EXACTLY_ONE` | NUMBER | 1 | no |
| LARGE_OPEN_SET | `ZERO_OR_MANY_LARGE` | ENTITY | unbounded (contract decides) | yes |

Only five fields, each with a downstream consumer and a test — see §7. No prose
"core behaviour" field was added: it would have been dead metadata, so Table 4's
wording lives in the module docstring instead.

`supports_missingness` is a genuine regime distinction, not decoration: "search
for what is still missing" is meaningful for an open set (awards) and for a
small set that may have been under-enumerated (borders), but meaningless once a
NULL_SINGLE locality or a NUMERIC scalar is resolved — there, a missingness
sweep would only manufacture rivals to the single answer.

---

## 7. ProgramType consumer matrix

| File / function | Programme branch | Meaning implemented | Classification | Action |
|---|---|---|---|---|
| `contracts/programs.PROGRAMS` | all four | regime facts: cardinality, output, cap, missingness | **authoritative abstraction** | added |
| `contracts/router.route` / `route_program` | — | relation → regime | authoritative routing | `route_program` added |
| `contracts/router.check_router_consistency` | all four | Table 4 + evaluator + regime invariants | authoritative assertion | extended |
| `contracts/base.RelationContract.program` | — | contract → regime handle | legitimate delegation | added |
| `contracts/base.RelationContract.max_objects` | bounded vs not | structural cap | **was duplicated** — re-derived the cap from `Cardinality` locally | now reads the regime |
| `contracts/base.validate` | all four | regime/contract compatibility | authoritative invariant | added |
| `selection._BY_PROGRAM` | all four | selector family per regime | legitimate delegation (dispatch table) | none |
| `selection.select_null_single` | NULL_SINGLE | `return accepted[:1]` | **was duplicated** — hard-coded the regime cap | now `[: contract.max_objects]` |
| `selection.select_small_set` / `select_large_open_set` | — | uses `contract.max_objects` / `selection.max_objects` | legitimate delegation | none |
| `coverage.estimate_residual` | all four | per-regime residual weighting | legitimate delegation (Module 6 owns the math) | none |
| `controller.should_stop` | all four | per-regime stopping predicate | legitimate delegation (Module 7 owns the rules) | none |
| `controller.legal_actions` | — | actions bounded by `contract.all_views()` | relation-specific, correctly bounded | documented + tested |
| `pipeline`, `parsing`, `engine`, `graph` (`output_type is NUMBER`) | — | numeric candidate path | relation-specific via `output_type`, now *asserted* to coincide with NUMERIC | invariant added |

**Legitimate delegation vs duplication.** The if/elif chains in `coverage.py`,
`controller.py` and `selection.py` were examined and **left alone**. Each
implements its own module's mathematics for a regime it is handed; none
re-defines what the regime *is*. That is the modular pattern the proposal asks
for, and collapsing them into one object would produce exactly the "god
program" §8 of the brief warns against.

The two genuine duplications were both *cardinality* facts: `max_objects`
re-derived from `Cardinality` in `base.py`, and `[:1]` hard-coded in
`select_null_single`. Both now read the regime.

---

## 8. Program-vs-RelationContract responsibility boundary

| | RelationContract (Module 0) | TypedProgramSpec (Module 1) |
|---|---|---|
| Answers | *what does this relation mean?* | *what class of inference process does it need?* |
| `companyTradesAtStockExchange` | the company **itself** must be listed; a parent's listing does not count; a subsidiary that is not separately listed has an empty answer | SMALL_SET: small bounded universe, entity output, zero-or-many, missingness meaningful |
| `hasArea` vs `hasCapacity` | km² total area **vs** integer people, highest published | identical — both NUMERIC |
| `countryLandBordersCountry` vs `companyTradesAtStockExchange` | disjoint definitions, rules and views | identical — both SMALL_SET |

Two tests lock this boundary in both directions:

- `test_two_small_set_relations_share_the_regime_but_no_semantics` asserts
  borders and stock share the *same regime object* while their definitions,
  positive rules, hard-negative rules and view sets are pairwise **disjoint**.
- `test_the_programme_registry_holds_no_relation_semantics` asserts no relation
  id appears inside the `PROGRAMS` table itself.

No semantic rule was moved out of Module 0, and no Module 0 semantics were
duplicated into Module 1.

---

## 9. Duplicated / scattered semantics found

**9.1 No programme-compatibility invariant existed (primary finding).** All
three contradictions the brief names in §11 passed `validate()` silently before
this review:

```
NULL_SINGLE   + ZERO_OR_MANY_LARGE  -> validate() PASSED
NUMERIC       + ENTITY output       -> validate() PASSED
LARGE_OPEN_SET + NUMBER output      -> validate() PASSED
```

A contract could therefore declare a regime it did not implement, and the
mismatch would surface only as wrong output at inference time.

**9.2 Cardinality cap duplicated in two places.** `RelationContract.max_objects`
re-derived "ZERO_OR_ONE or EXACTLY_ONE ⇒ 1" locally, and
`selection.select_null_single` independently hard-coded `[:1]`. Three
statements of one regime fact (`Cardinality`, `max_objects`, `[:1]`).

**9.3 `ProgramType.NUMERIC` and `OutputType.NUMBER` were coincidentally 1:1.**
The numeric candidate path is keyed on `output_type` across `pipeline.py`,
`parsing.py`, `engine.py` and `graph.py`. That is defensible — `output_type` is
independently checked against the evaluator's `RELATION_TYPE` — but nothing
asserted the two agree. Keying kept as-is; the coincidence is now an enforced
invariant.

**9.4 Not a defect — action-space bounding.** §10 of the brief asked whether
SMALL_SET can "gain arbitrary large-open-set expansion because the controller
exposes RUN_FACET globally". It cannot: `controller.legal_actions` enumerates
only `contract.all_views()`. Measured for all six relations, offered view ids
⊆ declared view ids in every case (borders 4/4, awards 6/6). Per the brief's
instruction this is **documented and tested**, not refactored.

---

## 10. Fixes made

| # | Fix | Follows |
|---|---|---|
| 1 | New `contracts/programs.py`: `TypedProgramSpec` + `PROGRAMS` registry — the one authoritative regime definition | §6 Table 4 |
| 2 | `check_program_compatibility(contract)` — regime/cardinality, regime/output, regime/cap and regime/missingness invariants | brief §11 |
| 3 | `RelationContract.validate()` and `check_router_consistency()` both enforce them; the router reports every violation at once | §6, §31 |
| 4 | `RelationContract.program` property — the contract's handle on its regime | §4.1 |
| 5 | `RelationContract.max_objects` now reads the regime cap instead of re-deriving it | removes 9.2 |
| 6 | `selection.select_null_single` uses `contract.max_objects` instead of `[:1]` | removes 9.2 |
| 7 | `router.route_program(relation)` — what downstream asks for when it needs the regime rather than the meaning | §6 |

Deliberately **not** done: no programme object owns prompting, parsing,
verification, RCSE, action scoring or selection; the if/elif dispatchers in
Modules 6/7/8 are untouched; no prose field was added to the registry.

---

## 11. Files created / modified

**Created (2)**
- `src/cover_kbc/contracts/programs.py`
- `tests/test_programs.py` (40 tests)
- `docs/audits/0004-module-1-typed-program-router-conformance.md`

**Modified (3)**
- `src/cover_kbc/contracts/base.py` — `program` property, regime-derived
  `max_objects`, programme invariants in `validate()`
- `src/cover_kbc/contracts/router.py` — `route_program`, programme invariants
- `src/cover_kbc/selection.py` — NULL_SINGLE cap read from the regime

**`benchmark/` — untouched.**

---

## 12. Commands executed

```bash
git status / branch --show-current / log --oneline -10 / diff --stat
pdftotext -layout COVER_KBC_V2_ARCHITECTURE_SPEC.pdf      # §4, §6, §26, §31
grep -rn "ProgramType|program_type" src/ tests/ scripts/
grep -rn "OutputType.NUMBER|max_objects|_BY_RELATION|is_numeric" src/
python -c "...dataclasses.replace(...).validate()"        # probe the invariant gap
python -c "...legal_actions(...)"                          # probe action-space bounding
python -m pytest -q
python -m pyflakes src/ tests/ scripts/
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

---

## 13. Test results

**319 passed**, 0 failed (was 279 before this review; +40). `pyflakes` clean.
The staged scripted smoke run still completes all three phases. No test loads a
heavyweight model.

`tests/test_programs.py` covers every item the brief's §12 lists:

| Required check | Test |
|---|---|
| all six relation → programme mappings | `test_each_relation_routes_to_its_spec_programme` (×6) |
| unknown relation fails loudly | `test_unknown_relation_fails_loudly` |
| no default/fallback routing | `test_there_is_no_default_fallback_programme` |
| router deterministic | `test_routing_is_deterministic_and_stateless` |
| router contains no factual knowledge | `test_router_holds_no_factual_object_knowledge` |
| router is non-neural | `test_routing_involves_no_model_call` |
| contract.program_type agrees with router | `test_routing_agrees_with_the_contract` |
| programme/cardinality compatibility | `test_null_single_rejects_a_many_cardinality` |
| programme/output-type compatibility | `test_numeric_rejects_entity_output`, `test_large_open_set_rejects_scalar_numeric_output` |
| NULL_SINGLE cannot finalize >1 object | `test_null_single_cannot_finalize_more_than_one_object` |
| NUMERIC cannot enter entity-list parsing | `test_numeric_regime_never_enters_the_entity_list_parser` |
| LARGE_OPEN_SET exposes facet capability | `test_large_open_set_exposes_facet_and_missingness_capability` |
| SMALL_SET does not inherit open-set expansion | `test_small_set_does_not_inherit_unrestricted_open_set_expansion` |
| semantics not duplicated | `test_contract_cap_is_derived_from_the_programme`, `test_program_type_reaches_every_regime_dispatcher` |
| programme/relation boundary | `test_two_small_set_relations_share_the_regime_but_no_semantics`, `test_the_programme_registry_holds_no_relation_semantics` |

---

## 14. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff -- benchmark/              -> (empty)
git diff --cached -- benchmark/     -> (empty)
```

No organizer file was read-modified or written.

---

## 15. Challenge-compliance impact

None, positive or negative. Module 1 is non-neural by construction and adds no
inference-time parameters. The frozen target pairing is unchanged:

```
mistralai/Mistral-Small-3.2-24B-Instruct-2506   24,011,361,280   enumerator
Qwen/Qwen3.5-4B                                  4,659,865,088   verifier
--------------------------------------------------------------------
total                                           28,671,226,368   < 32B
```

No model was downloaded, loaded or run during this review.

---

## 16. Unresolved Module-1-only issues

**16.1 Two routing tables persist by design.** `PROGRAM_BY_RELATION` (spec
Table 4) and `CONTRACTS[r].program_type` are both present. The contract is the
source of truth; the table is a cross-check enforced by
`check_router_consistency()`, which runs before inference in both entry-point
scripts. Collapsing them would remove an independent statement of the spec, so
they are deliberately retained — but a *third* copy would be a defect.

**16.2 `route()` does not self-assert.** It returns the contract's programme
without re-checking Table 4 on every call; the check is per-run, not per-query.
That is the right cost trade-off, but it means an in-process mutation of
`CONTRACTS` after start-up would go unnoticed until the next run.

**16.3 `SMALL_SET.max_objects = 0` is "unbounded at the regime level".** The
regime declines to cap borders and stock, deferring to each contract's
`SelectionPolicy` (both currently `0`). Spec Table 4 says "small candidate
universe" but names no number, so no numeric cap was invented. If a cap is
ever wanted it belongs in the contract, not the regime.

**16.4 `ProgramType` is a `str`-Enum, so `get_program("NUMERIC")` resolves.**
Deliberate — stage files persist the programme as a string and must rehydrate
without manual conversion. Unknown strings still raise `KeyError`. Tested.

---

## 17. Future-review notes (NOT fixed here)

Recorded per §14 of the brief; these belong to later module reviews.

**17.1 Module 7 — `RUN_VIEW` carries a "mandatory" scoring bonus keyed on
action type.** `controller.score_action` adds `gap += 0.5` for `RUN_VIEW`,
using the action type as a proxy for "this view is mandatory". That coupling is
correct today only because `legal_actions` emits `RUN_VIEW` exactly for
mandatory views. It is fragile, and belongs to the Module 7 review.

**17.2 Module 7 — `ActionType.RESAMPLE` is defined and scored but never
enumerated** by `legal_actions`, so it cannot currently be chosen. Not a Module
1 concern.

---

## 18. Verdict

Module 1 **now conforms** to spec §6.

- Routing was already correct: six relations, deterministic, non-neural, fails
  closed, agrees with Table 4 and with the official evaluator.
- `ProgramType` was **not** an executable regime — it was a label, and the
  contract/regime agreement was entirely unguarded. That is fixed by one small
  authoritative registry with five consumed, tested fields.
- The two genuine duplications (`max_objects` re-derivation, `[:1]`) are removed.
- The if/elif dispatchers in Modules 6/7/8 are legitimate delegation and were
  correctly left in place.
- The programme/relation boundary is explicit and test-locked in both directions.

**Modules 2+ remain unreviewed.**

**Recommended next review: Module 2 — Diverse Elicitation Engine**, pending
external authorisation.
