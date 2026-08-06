# Audit 0017 — Module 10: Prompt Program Compiler Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: second module of the upgraded architecture (M10 of M9–M21).
Mode: **shadow** — no M0–M8 decision consumes a prompt program.

---

## 1. Objective and scope

Implement **M10 Prompt Program Compiler**: convert *(query, relation contract,
M9 risk profile)* into a deterministic, typed, immutable **prompt program** — a
blueprint describing how a relation should be spoken about.

In scope: the typed contract, the versioned prompt registry, risk- and
surface-conditioned directive compilation, the step-back query specification,
configuration, the observability artefact, and the M9→M10 seam.

Out of scope and not implemented: M11–M21. No placeholder files. M10 does not
execute prompts, call a model, retrieve anything, discover candidates, choose
controller actions, allocate budget, or implement any specialist.

Frozen and unchanged: M0–M8, M9's schema and artefact, the model profile
(28.67 B published), every threshold, and `benchmark/`.

---

## 2. Architecture position

```
M0 Relation Compiler          contracts/registry.py     (authoritative)
        v
M1 Typed Program Router       contracts/router.py       (authoritative)
        v
M9 Risk & Difficulty Profiler query_intelligence/       (authoritative input)
        v
M10 Prompt Program Compiler   query_intelligence/       <- this milestone
        v
[future M11 Closed-Book Parametric Retrieval]
        v
M2 Diverse Elicitation        elicitation/              (unchanged)
```

One seam, in `CoverPipeline.enumerate_query`, after `compile_query` and before
`build_graph`:

```python
query, contract = compile_query(query.subject, query.relation, query.row_index)
if self.profiler is not None:
    profile = self.profiler.profile(query, contract)
    self.query_profiles.append(profile)
    if self.prompt_compiler is not None:
        self.prompt_programs.append(self.prompt_compiler.compile(query, contract, profile))
graph = build_graph(query, contract)
```

M10 is nested inside the M9 branch, so it is structurally impossible to compile
without a profile. Both buffers are observability-only; neither reaches the
graph. `prompt_compiler=None` is the default and is the pre-M10 path.

---

## 3. Public types

```python
class ObjectKind(str, Enum)           # ENTITY | NUMBER
class NumericKind(str, Enum)          # NOT_NUMERIC | INTEGER | REAL
class DirectiveKind(str, Enum)        # EXCLUSION | STRICT_FORMAT | IDENTITY |
                                      # RECALL_BREADTH | COMPLETENESS | TEMPORAL | EMPTY_PERMITTED
class SubjectDirectiveKind(str, Enum) # PRESERVE_VERBATIM | _PARENTHETICAL | _COMMA_QUALIFIER |
                                      # _PREPOSITIONAL_QUALIFIER | _UNICODE | _DIGITS

@dataclass(frozen=True) class TaskSemantics       # relation, definition, answer_type, relation_focus
@dataclass(frozen=True) class AnswerSchema        # object_kind, cardinality, numeric_kind,
                                                  # canonical_unit, allow_empty, max_objects,
                                                  # empty_token, output_instruction
@dataclass(frozen=True) class RiskDirective       # kind, axis, level, instruction
@dataclass(frozen=True) class SubjectDirective    # kind, instruction
@dataclass(frozen=True) class QuerySpecification  # relation_focus, semantic_question, abstraction_cues
@dataclass(frozen=True) class PromptProgram       # see §5
```

Plus `PromptProgramCompiler`, `PromptCompilerConfig`, `build_prompt_compiler`,
`program_preview`, `RelationPromptSpec`, `RELATION_PROMPT_SPECS`,
`DIRECTIVE_RULES`, `SUBJECT_DIRECTIVE_RULES`, `check_prompt_registry_consistency`,
`get_prompt_spec`, `prompt_registry_table`, `COMPILER_VERSION`.

---

## 4. Input contract

```python
PromptProgramCompiler.compile(query, contract=None, profile=None) -> PromptProgram
```

`profile` is **required**. Omitting it raises, naming the reason: the stack is
`M1 -> M9 -> M10`, and a program compiled without a profile would have been
built by a second, invisible profiler. `contract` is a shortcut for callers that
already resolved M0/M1; when omitted it is looked up.

`_check_agreement` refuses to compile from inputs that disagree, reporting every
problem at once. Five disagreements are checked and each is tested:

| Disagreement | Result |
| --- | --- |
| contract relation ≠ query relation | raise |
| profile relation ≠ query relation | raise |
| profile subject ≠ query subject | raise |
| profile row_index ≠ query row_index | raise |
| profile programme ≠ routed programme | raise |
| profile cardinality regime ≠ the one M1's programme implies | raise |

---

## 5. PromptProgram schema

```python
compiler_version, profile_version : str
relation, subject : str ; row_index : int
program_type : ProgramType            # from M1, read never chosen
cardinality_regime : CardinalityRegime # from M9
task_semantics : TaskSemantics        # copied verbatim from M0
answer_schema : AnswerSchema
positive_constraints : tuple[str, ...]  # contract.positive_rules, verbatim
negative_constraints : tuple[str, ...]  # contract.hard_negative_rules, verbatim
semantic_cues, negative_anchors : tuple[str, ...]
risk_directives : tuple[RiskDirective, ...]
subject_directives : tuple[SubjectDirective, ...]
query_specification : QuerySpecification
specialist_hint : SpecialistHint      # from M9
```

Derived views: `output_contract` (the rendered answer instruction),
`keyword_bundle` (cues + anchors), `directive(kind)`, `has_directive(kind)`.

Immutable, `==`-comparable, hashable by value (every field is a scalar, an enum
or a tuple of frozen dataclasses), and JSON round-tripping.

`profile_version` is recorded on every program so one can never be silently
paired with a different risk vocabulary.

**Deviation from the proposal's `PromptProgram`.** The proposal (§6.2) also
lists `direct_templates`, `facet_templates`, `pseudo_memory_templates`,
`verifier_templates`, `independence_groups` and `stop_strings`. None appear,
each for a stated reason:

| Proposal field | Where it belongs | Why not here |
| --- | --- | --- |
| `direct_templates`, `facet_templates` | **M2** | Which view runs is M2's, and the brief forbids duplicating it. |
| `pseudo_memory_templates` | **M11** | Generating pseudo-context is M11's; M10 only prepares the specification. |
| `verifier_templates` | **M4** today, **M17** later | M4 is frozen and audited; the brief forbids routing it through M10. |
| `independence_groups` | **M0** | Already on the contract as `eligible_independence_groups`; copying it would create a second declaration. |
| `stop_strings` | **M2** | A decode concern owned by the view library, which currently declares none. |

---

## 6. Structured program vs rendering

Two clearly separated concepts, as required:

* **The program** is the source of truth. A future module asks
  `program.negative_constraints`, `program.answer_schema.canonical_unit` or
  `program.directive(DirectiveKind.EXCLUSION)` — never a regex over prose.
* **Rendering** is a projection: `PromptProgram.fragments()` returns named
  fragments (`task`, `positive_constraints`, `negative_constraints`,
  `semantic_cues`, `negative_anchors`, `risk_directives`, `subject_directives`,
  `query_specification`, `output_contract`), and `program_preview()` joins them.

Direction of derivation is tested: every declared constraint and cue appears in
the rendered text, so text is built *from* structure and structure is never
recovered *from* text. `output_instruction` is likewise a pure function of the
schema fields beside it.

---

## 7. Six-relation answer schemas

| Relation | object_kind | numeric_kind | canonical_unit | allow_empty | max_objects |
| --- | --- | --- | --- | --- | --- |
| `countryLandBordersCountry` | ENTITY | NOT_NUMERIC | — | true | 0 (unbounded) |
| `companyTradesAtStockExchange` | ENTITY | NOT_NUMERIC | — | true | 0 (unbounded) |
| `personHasCityOfDeath` | ENTITY | NOT_NUMERIC | — | true | 1 |
| `hasCapacity` | NUMBER | INTEGER | `persons` | false | 1 |
| `hasArea` | NUMBER | REAL | `km2` | false | 1 |
| `awardWonBy` | ENTITY | NOT_NUMERIC | — | true | 0 (unbounded) |

Every field restates something M0 or M1 already fixes — `output_type`,
`allows_empty`, `max_objects`, `numeric_target_unit`, `numeric_integer_only` —
translated into prompt-facing terms. Tests assert field-by-field agreement with
the contract, so the schema cannot drift into a second declaration.

`empty_token` is `NONE` for entity answers and `UNKNOWN` for numeric ones. A
test asserts these are the same sentinels Module 2's `ENTITY_FORMAT` and
`NUMERIC_FORMAT` already use: two spellings of "nothing" would be a parser bug,
so agreement is **checked**, not assumed.

This module specifies output *language* only. Parsing, unit conversion,
clustering and consensus remain Modules 12 and 16.

---

## 8. Semantic cues and negative anchors

| Relation | Positive cues | Negative anchors |
| --- | --- | --- |
| `countryLandBordersCountry` | shares a land border with; shares a land boundary with; physically adjacent by land; neighbouring country; land frontier | maritime border; sea boundary; nearby country; in the same region; reachable by bridge or tunnel; overseas dependency |
| `companyTradesAtStockExchange` | publicly listed on; shares are traded on; stock exchange listing; primary listing; secondary listing | stock market index; parent company listing; subsidiary listing; formerly listed on; privately held; ticker symbol |
| `personHasCityOfDeath` | died in; place of death; city of death; locality where the person died | born in; place of birth; lived in; place of residence; buried in; country of death; still living |
| `hasCapacity` | spectator capacity; maximum capacity; total capacity; seating capacity; how many spectators the venue holds | record attendance; average attendance; attendance at an event; seated-only capacity when the total is higher; capacity before or after a renovation |
| `hasArea` | total area; surface area; area in square kilometres; area in square miles; area in hectares | land area only; water area only; metropolitan area of a surrounding region; population; length or coastline; elevation |
| `awardWonBy` | won the award; recipient of the award; laureate; honoured with the award; award winners across all years | nominee; finalist or shortlisted; the winning work rather than its creator; a similarly named predecessor or successor award; a different category of the same award; an award later rescinded |

Every relation carries a written `rationale`; a missing one is a hard error.
Cues and anchors are checked disjoint, non-empty and duplicate-free.

Two judgement calls worth recording:

* **Capacity keeps its ambiguity open.** `seating`, `total` and `maximum` are
  all offered rather than one silently preferred, because choosing among them is
  Module 12's job and a compiler that picked one would be asserting a fact.
* **Area cues carry alternate units.** `square miles` and `hectares` appear
  because the contract accepts them *once converted*; a recall that can only
  reach a figure in its published unit would lose it.

---

## 9. Risk axis → prompt directive

| M9 axis | Trigger | DirectiveKind | Compiled language (abridged) |
| --- | --- | --- | --- |
| `open_set_risk` | HIGH | RECALL_BREADTH | "The answer set may be large. Recall as many distinct qualifying objects as you can…" |
| `missingness_risk` | HIGH | COMPLETENESS | "A plausible-looking list may still be incomplete…" |
| `numeric_ambiguity` | HIGH | STRICT_FORMAT | "Several different numbers may be defensible… Answer the quantity the definition names." |
| `temporal_sensitivity` | HIGH | TEMPORAL | "The correct answer can change over time. Answer for the present state…" |
| `nullability_risk` | HIGH | EMPTY_PERMITTED | "An empty answer is a valid and expected outcome… rather than supplying a plausible guess." |
| `identity_ambiguity` | HIGH | IDENTITY | "More than one entity may share this name. Answer for exactly the subject as written…" |
| `near_miss_risk` | HIGH | EXCLUSION | "Closely related but incorrect answers are common… Check each candidate against the exclusions." |
| `format_sensitivity` | HIGH | STRICT_FORMAT | "The form of the answer is part of its correctness. Follow the stated unit, granularity and output format exactly." |

Resulting per relation:

| Relation | Directives compiled |
| --- | --- |
| `countryLandBordersCountry` | EXCLUSION |
| `companyTradesAtStockExchange` | EXCLUSION, IDENTITY, TEMPORAL, EMPTY_PERMITTED |
| `personHasCityOfDeath` | EXCLUSION, IDENTITY, TEMPORAL, EMPTY_PERMITTED |
| `hasCapacity` | EXCLUSION, STRICT_FORMAT |
| `hasArea` | STRICT_FORMAT |
| `awardWonBy` | EXCLUSION, RECALL_BREADTH, COMPLETENESS |

Table-driven — one `DirectiveRule` per axis, fired on
`profile.axis(rule.axis) >= rule.trigger` and ordered by `RISK_AXES` so output
is stable and diffable. Every trigger is `HIGH`, matching the brief's stated
mapping and introducing **no tunable cut-point**: there is no intermediate value
to fit.

**Language only.** A test regexes every directive for whole words naming an
action, budget, call, token, facet, view, sample or retry, and fails if one
appears. Those belong to M19–M21. (Whole-word matching matters: "recall" is
legitimate prompt language and must not trip a substring match on "call".)

---

## 10. Subject surface → preservation directive

| M9 surface feature | SubjectDirectiveKind | Instruction |
| --- | --- | --- |
| *(unconditional)* | PRESERVE_VERBATIM | Use the subject name exactly as supplied; no rewriting, translating, expanding or abbreviating. |
| `has_parenthetical` | PRESERVE_PARENTHETICAL | Keep the parenthetical; it may be what distinguishes this entity from another. |
| `has_comma_qualifier` | PRESERVE_COMMA_QUALIFIER | Keep the comma-separated qualifier rather than answering about the part before it. |
| `has_prepositional_qualifier` | PRESERVE_PREPOSITIONAL_QUALIFIER | Keep the whole qualifying phrase; do not shorten to head words. |
| `has_non_ascii` | PRESERVE_UNICODE | Reproduce the original spelling; do not transliterate or strip accents. |
| `has_digit` | PRESERVE_DIGITS | Reproduce digits exactly. |

Preservation only. Nothing interprets a qualifier, resolves it against any
source, or infers a type, location, popularity or factual status from it. A test
asserts the compiled instructions for `"Estadio X in Madrid"` contain no
interpretation of "Madrid". Subject strings survive compilation *and* JSON
round-trip losslessly for Unicode, parentheticals, commas, digits and internal
punctuation.

---

## 11. Why M10 does not duplicate M0 or M1

* **M1.** `program_type` is read from `contract.program` and nothing else. There
  is no relation→programme table in M10, and a profile whose programme
  disagrees with the routed one raises rather than winning.
* **M0.** `task_semantics.definition`, `answer_type`, `positive_constraints` and
  `negative_constraints` are **copied verbatim** from the contract; tests assert
  field-by-field equality. A separate test scans `prompt_registry.py` for every
  contract definition and every positive/negative rule and fails if any is
  restated — the registry may add *phrasing*, never a second definition.
* **The answer schema** restates only what M0/M1 already fix, and each field is
  asserted equal to its contract source.
* **No relation branching.** A test asserts no official relation name appears in
  `prompt_types.py` or `prompt_compiler.py`; all six live only in the registry.

---

## 12. Why M10 does not duplicate M2

M10 says *how a relation should be spoken about*. M2 says *which elicitation
view runs*. The boundary is enforced, not merely stated:

* `prompt_compiler.py` is asserted free of `ViewSpec`, `views_for`, `get_view`,
  `ViewFamily` and `elicitation`.
* A compiled program is asserted to carry no `view_id`, `facet_id`,
  `independence_group`, `decode` or `template` anywhere in its serialised form.
* No view sequence, facet plan or run count is compiled. M10 emits reusable
  primitives — task semantics, cues, constraints, output contract — that a
  future M2 view or an M11 recall can combine with its own purpose.
* Module 2's `SYSTEM_PROMPT`, `ENTITY_FORMAT` and `NUMERIC_FORMAT` are asserted
  unchanged, and M2 still uses its own strings in this milestone.

---

## 13. Why M10 does not touch M4

The blind A/B/C verifier is frozen and audited (Audit 0007). M10 does not
rewrite it, route it, or reach it:

* `prompt_compiler.py` is asserted free of `verification` and `VerifierTemplate`.
* Module 4's **entire prompt surface** — `VERIFIER_SYSTEM_PROMPT`,
  `GATE_TEMPLATE`, `LABEL_TOKENS` and all three template bodies — is pinned by
  sha256 `3acd7109…e6d874`. Changing any byte fails the test, so an edit has to
  be a deliberate act that updates the constant, never a side effect.
* Verifier independence is untouched: M10 produces acquisition-side language
  only. Specialist verifier semantics are Module 17's.

---

## 14. Why keywords are not external retrieval

A cue is *lexical steering placed inside a prompt* to move the frozen model
towards the right region of its own weights. A retrieval query is a request sent
to a system that holds facts. Only the first exists here, and the distinction is
enforced three ways:

* **Structurally.** An AST scan of all three M10 modules asserts no import whose
  root is `requests`, `httpx`, `urllib`, `socket`, `http`, `aiohttp`, `torch` or
  `transformers`. The package cannot open a connection.
* **In the data.** Every declared cue, anchor, abstraction cue, relation focus
  and semantic question is scanned for `http`, `www.`, `.com`, `.org`,
  `wikipedia`, `wikidata`, `search for`, `look up`, `query the`, `api` and
  `database`. (The scan targets the *declared strings*, not the module prose —
  the file's own docstring names those systems in order to forbid them.)
* **At runtime.** A clean-subprocess probe compiles all six relations and
  reports any loaded `torch`, `transformers`, `mistral_common`, `requests`,
  `urllib.request`, `http.client`, `socket` or `cover_kbc.models.*` module. The
  assertion is that the list is empty.

No web, no RAG, no Wikipedia, no Wikidata, no KB, no vector store, no external
corpus, no search API.

---

## 15. Step-back support and the M11 boundary

`QuerySpecification` carries `relation_focus`, `semantic_question` and
`abstraction_cues` — the Step-Back layer, restricted exactly as the proposal
(§6.4) requires: it produces a **search specification, never a factual answer**.

It is fully contract-derived and deterministic. **No model is called to produce
it**, and it contains no pseudo-context. A test asserts the semantic question is
about the *relation* and never mentions the subject.

The boundary with M11 is sharp: M10 states the specification; M11 executes
parametric recall against it and produces pseudo-memory candidates. Nothing in
M10 generates, stores or evaluates recalled content.

No prompt optimisation of any kind is implemented — no OPRO, MIPRO, AMPO, prompt
search, mutation, scoring, bandits, Bayesian optimisation or learned selection.
Programs are deterministic and proposal-derived.

---

## 16. Config and registry design

```yaml
query_intelligence:
  profiler:                    # M9, required when the compiler is enabled
    enabled: true
    mode: shadow
  prompt_compiler:             # M10
    enabled: true
    mode: shadow
    compiler_version: m10-v1
    relation_prompts:          # optional per-relation overrides
      hasArea:
        semantic_cues: ["total surface area"]
```

* One declaration surface: `prompt_registry.py`, versioned by
  `COMPILER_VERSION`. No `if relation == ...` in the compiler.
* Unknown keys at either level raise, naming what was expected. Unknown
  relations, unknown fields and wrong types in overrides all raise.
* Overrides are re-validated through `check_prompt_registry_consistency()`, so
  config may change wording but cannot leave the registry inconsistent — an
  empty `semantic_cues` list is rejected. Building a compiler does not mutate
  the module-level table.
* `mode` accepts `shadow` only; anything else raises, naming Module 11 as the
  missing consumer.
* **M10 enabled without M9 fails loudly**, at both the config boundary
  (`build_prompt_compiler`) and the pipeline constructor. There is no path that
  silently compiles an unprofiled program.

---

## 17. Persistence

`prompt_programs.jsonl`, one JSON object per Phase-A query, alongside
`query_profiles.jsonl`. `stage_a_enumerated.jsonl`, `diagnostics.json`,
`trace.jsonl` and M9's `query_profiles.jsonl` schema are all untouched.

Identity: `compiler_version`, `profile_version`, `SubjectEntity`, `Relation`,
`row_index`, `program_type`. Then the structured fields. Contains no gold
object, no model output, no model reasoning, no external fact — a test pins the
exact top-level key set so a factual field cannot be added silently.

**No rendered prose is persisted.** `program_preview()` exists for audits and is
optional and deterministic, but writing it into every record would duplicate the
structured fields in English without adding information. A test asserts no
`preview`/`rendered` key appears.

Recompilation is exact — a test reloads every persisted record and compares it
against a freshly compiled program for equality — so later phases neither
persist nor reload programs.

---

## 18. Zero-neural-cost evidence

* **Import-level (AST).** No M10 module imports anything network- or
  backend-capable.
* **Runtime (subprocess).** A clean interpreter compiles all six relations and
  loads none of `torch`, `transformers`, `mistral_common`, `requests`,
  `urllib.request`, `http.client`, `socket`, `cover_kbc.models.*`.
* **Counters.** A `ScriptedRuntime`'s `calls` and `generated_tokens` are `0`
  after compiling all six relations.
* **End-to-end.** `diagnostics.json` is byte-identical with M10 on and off, so
  `total_calls`, `total_verification_calls` and `total_generated_tokens` are
  unchanged.

Compiling one program is a dict lookup, a few tuple builds and string
formatting. No I/O.

---

## 19-20. Shadow-mode invariance and artefact comparison

The real staged CLI, run twice with identical arguments and configs differing
only in `query_intelligence.prompt_compiler.enabled` (M9 on in both).

Parameterised over three relations spanning three programmes (`SMALL_SET`,
`LARGE_OPEN_SET`, `NUMERIC`):

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
query_profiles.jsonl     IDENTICAL      <- M9's artefact is unchanged by M10
```

Repeated on the role-swap config (`awardWonBy`), which exercises the
Phase B → enumerator resume cycle:

```
…all of the above           IDENTICAL
stage_r1_enumerator.jsonl   IDENTICAL
prompt_programs.jsonl       present with M10 on, absent with M10 off
```

`stage_a_enumerated.jsonl` matching is the strong result: candidates, evidence
edges, controller log, RCSE state and budget snapshot are all unchanged, so
every M2–M8 decision was identical, not merely the final answer.

**CURRENT PREDICTIONS == M9 + M10 SHADOW PREDICTIONS** holds for every fixture
tested, and neural call counts are equal.

---

## 21. Test results

```
python -m pytest -q
    1166 passed, 3 skipped        (1075 before; +91)
```

`tests/test_prompt_compiler.py`, 91 tests, covering all eighteen required areas:

1. **Determinism** — identical programs from identical inputs, across compiler
   instances; hashable by value; deterministic rendering.
2. **Zero neural cost** — AST import scan, subprocess module probe, runtime
   counters.
3. **Six-relation coverage** — every relation compiles; registry matches the
   contract set.
4. **M1 consistency** — `program_type` agrees with `PROGRAM_BY_RELATION` and the
   contract for all six.
5. **M9 consistency** — a profile is required; a profile for a different query
   is rejected; all five disagreement modes raise (parameterised);
   `profile_version` is recorded; the compiler is asserted never to reference
   `QueryProfiler` or `subject_surface_features`.
6. **Contract authority** — verbatim equality for definition, answer type and
   both rule sets; the registry restates no contract text; the schema follows
   the contract.
7. **Typed answer schemas** — numeric (integer/real, unit), null-single, small
   set, large open set; empty tokens agree with M2's format strings;
   `output_contract` is a projection.
8. **Cues and anchors** — parameterised per relation; area units; capacity
   ambiguity kept open; disjointness; rationale present.
9. **Risk directives** — each of the seven mappings, positive and negative
   cases; exact firing set matches the profile; ordering follows `RISK_AXES`;
   no directive names an action, budget or stop.
10. **No factual leakage** — key set pinned; two subjects of one relation share
    all relation-level material.
11. **Subject preservation** — parameterised over five surface kinds; a plain
    subject gets only the verbatim directive; lossless through JSON for
    Unicode, parentheticals, commas, digits, punctuation; directives interpret
    nothing.
12. **Centralised declarations** — no relation name outside the registry.
13. **Round-trip** — `to_json` → `json` → `from_json` equality for all six.
14. **M2 ownership** — no view machinery referenced; no view plan in the
    program; M2's format strings unchanged.
15. **M4 ownership** — the full prompt surface pinned by sha256; M10 cannot
    reference `verification`.
16. **Shadow invariance** — parameterised byte comparison over three relations,
    plus the role-swap path, plus call-count equality.
17. **Persistence** — one record per query in manifest order, paired 1:1 with
    M9's artefact; persisted == recompiled; no rendered prose.
18. **Configuration failure** — M10-without-M9 at both boundaries, unsupported
    mode, unknown keys, unknown relation, malformed overrides, override that
    would empty a cue list, registry missing a relation, directive rules
    referencing unknown axes or surface features.

Three test defects were found and fixed during development, all in the tests
rather than the module: a substring match that flagged "recall" as naming a
"call"; a retrieval-marker scan that matched the module's own prohibition prose;
and a guessed Module 4 prompt string, replaced by a sha256 pin of the real
surface.

---

## 22. pyflakes

```
python -m pyflakes src/ tests/ scripts/
    clean
```

---

## 23. Model budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    Qwen/Qwen3.5-4B                                [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506  [enumerator] 24.011B (verified)
    total: 28.67B
    RESULT: PASS
```

Unchanged. M10 adds zero inference-time parameters.

---

## 24. Benchmark integrity

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no VAL or TEST gold was read.

---

## 25. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M10 value.** Every
cue, anchor, focus line, semantic question, abstraction cue and directive was
derived from the relation's own contract semantics — its definition, positive
rules and hard-negative rules — and every relation carries a written rationale.
Nothing was searched, scored or fitted. No prompt optimisation exists. Every
directive trigger is `HIGH`, so there is no cut-point that could be tuned.

---

## 26. Challenge compliance

M10 is closed-book deterministic infrastructure. No web search, RAG, external
corpus, Wikipedia, Wikidata, KB lookup, vector database or external search API —
it imports nothing that could open a connection (§14) and performs no I/O. No
fine-tuning, LoRA, continued pretraining, learned router, learned prompt
selector, learned prompt optimiser or neural prompt generator. Semantic cues are
lexical steering for future parametric-memory elicitation, never retrieval
queries sent to an external system.

---

## 27. Non-goals

M11–M21 remain unimplemented, and no placeholder files were created:

M11 Closed-Book Parametric Retrieval · M12 Numeric Specialist · M13
Large-Open-Set Specialist · M14 Null/Temporal Specialist · M15 Small-Set Closure
Specialist · M16 Atomic Consensus Engine · M17 Specialist Verifier Suite · M18
Bidirectional/Counterfactual/Dispute Verifier · M19 Coverage Gap & Missingness
Estimator · M20 Relation Budget Scheduler · M21 Expected-Value Micro-Planner.

`specialist_hint` is carried through from M9 as an advisory label; nothing
dispatches on it.

---

## 28. Verdict

**PASS.**

M10 is implemented, typed, immutable, versioned, config-driven and
deterministic. It consumes M0, M1 and M9 and can override none of them. It
spends zero neural calls and cannot reach a network. Module 2 still owns which
view runs; Module 4's prompt surface is pinned byte-identical by hash. Enabling
M10 leaves predictions, neural call counts, candidate graphs and M9's own
artefact byte-identical across the staged, role-swap and interleaved paths. The
M0–M9 core is unchanged apart from one defaulted constructor argument and four
lines at the existing M9 seam.

Next architecture step: **M11 Closed-Book Parametric Retrieval** — not
implemented here.
