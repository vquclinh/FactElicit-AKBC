# Audit 0018 — Module 11: Closed-Book Parametric Retrieval Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: third module of the upgraded architecture (M11 of M9–M21).
Mode: **shadow**, and **disabled by default** — M11 spends real neural calls.

---

## 1. Objective and scope

Implement **M11 Closed-Book Parametric Retrieval**: turn a compiled
`PromptProgram` into a small, fixed plan of structured probes and execute them
against the **frozen enumerator runtime**, producing unverified model-generated
recall for later specialists.

In scope: the typed contract, the three probe families the proposal declares,
prompt rendering from Module 10, independence-group provenance, call accounting,
configuration, the observability artefact, and the M10→M11 seam.

Out of scope and not implemented: M12–M21. No placeholder files.

**M11 is not free.** Modules 9 and 10 cost zero neural calls; this one exists to
query the model, so claiming zero cost would be false. What is guaranteed is
stated in §16.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§7.1** "Why call it 'parametric retrieval'?" | "Since external documents are forbidden, COVER replaces corpus retrieval with independent parametric-memory probes." Figure 2 shows external web / vector DB / KB struck through (✗) and frozen-model parametric probes retained (✓). |
| **§7.2** "Three probe families" | The **complete** probe set: pseudo-memory view, self-ask decomposition, query rewrite loop. |
| **§7.2** evidence-hygiene box | "Pseudo-context, generated explanations, and chain-of-thought are never passed verbatim into the verifier. They are acquisition artifacts. The Evidence Graph records only candidate occurrence, independence group, raw logit diagnostics, and verifier results." |
| **§7.2** pseudo-memory | "The sketch does not itself create a support edge." |
| **§6.4** step-back layer | "The output becomes the branch plan for M11–M15" — M10 produces the specification, M11 consumes it. |
| **§6.2** PromptProgram structure | M11's input surface. |
| **§5** M9 risk vector, **§5.1** routing | Upstream identity M11 must carry, never recompute. |
| **§8–§14** M12–M15 | What M11 must *not* do: clustering, facet planning, existence gating, set closure. |
| **Appendix C, Table 12** | M11 I/O contract: input "PromptProgram + state", output "GenerationRecords/pseudo-memory candidates", Neural: **Yes**. |
| **§26, Table 10** | Technical risks and the fail-closed invariants. |

Also read: Audit 0016 (M9), Audit 0017 (M10), and the current source.

### Where the brief and the proposal differ

The brief's §7 lists **five** conceptual families; the proposal's §7.2 declares
**three** and calls them "Three probe families". The brief itself resolves this —
"implement only those supported by the proposal" — so three are implemented. The
mapping, and the two deliberate omissions, are recorded in §7 below rather than
resolved silently.

One further narrowing, flagged rather than hidden: the proposal's query-rewrite
family is **adaptive** — "*if action yield is low or disagreement is high*, M11
rewrites the query". Yield and disagreement are Module 19 and Module 21 signals,
and the brief (§8, §17) forbids M11 from consuming or deciding on them. The
*rewrite operation* is implemented; its *adaptive trigger* is deferred to the
milestone that owns those signals. M11 plans statically and allocates nothing.

---

## 3. Architecture position

```
M0 Relation Compiler
        v
M1 Typed Program Router
        v
M9 Risk & Difficulty Profiler         (authoritative)
        v
M10 Prompt Program Compiler           (authoritative input)
        v
M11 Closed-Book Parametric Retrieval  <- this milestone
        v
[future M12-M15 specialists]
        v
M2 -> M3 -> ... -> M8                 (unchanged production path)
```

One seam, in `CoverPipeline.enumerate_query`, nested inside the existing M9/M10
branch:

```python
if self.profiler is not None:
    profile = self.profiler.profile(query, contract)
    self.query_profiles.append(profile)
    if self.prompt_compiler is not None:
        program = self.prompt_compiler.compile(query, contract, profile)
        self.prompt_programs.append(program)
        if self.retriever is not None:
            self._run_shadow_retrieval(query, program)
graph = build_graph(query, contract)
```

Nesting makes the stack structural: M11 cannot run without M10, which cannot run
without M9. `retriever=None` is the default and is the pre-M11 path.

---

## 4. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/query_intelligence/retrieval_types.py` | **New.** `ParametricRetrievalPlan`, `ParametricRecallOperation`, `ParametricMemoryRecord`, `ParametricRetrievalResult`, 5 enums, `prompt_digest`. |
| `src/cover_kbc/query_intelligence/retrieval_templates.py` | **New.** Three generic probe frames + shared system prompt. |
| `src/cover_kbc/query_intelligence/parametric_retrieval.py` | **New.** `ParametricRetriever`, `RetrievalConfig`, `OPERATION_SPECS`, `build_parametric_retriever`, `classify_output`. |
| `src/cover_kbc/query_intelligence/__init__.py` | M11 exports made **lazy** (§11); M9/M10 exports unchanged. |
| `src/cover_kbc/query_intelligence/profiler.py` | One line: config guard admits the `parametric_retrieval` sibling key. |
| `src/cover_kbc/pipeline.py` | `retriever=None` arg, `retrieval_results` / `shadow_calls` / `shadow_generated_tokens`, `_run_shadow_retrieval`, 3 lines at the seam. |
| `scripts/run_staged.py` | Builds the retriever for Phase A; writes `parametric_memory.jsonl`; subtracts shadow calls from the production progress figure. |
| `scripts/run_cover.py` | Same, for the interleaved runner. |
| 3 configs | `parametric_retrieval` block, `enabled: false`. |
| `tests/test_parametric_retrieval.py` | **New.** 80 tests. |
| `tests/test_query_profiler.py`, `tests/test_prompt_compiler.py` | Zero-backend guarantees rescoped from the package to M9's and M10's own modules (§11). |

M0–M10 semantics are otherwise untouched. `benchmark/` untouched.

---

## 5. Public types

```python
class RecallOperationKind(str, Enum)         # pseudo_memory | self_ask | query_rewrite
class ParametricIndependenceGroup(str, Enum) # PSEUDO_MEMORY_SKETCH | SELF_ASK_DECOMPOSITION | QUERY_REWRITE
class MemorySource(str, Enum)                # FROZEN_MODEL_PARAMETRIC_MEMORY  (only value)
class ExpectedOutputKind(str, Enum)          # PROSE | QA_PAIRS | OBJECT_LIST | NUMBER
class ParseStatus(str, Enum)                 # OK | EMPTY | ABSTAINED | MALFORMED | RUNTIME_ERROR

@dataclass(frozen=True) ParametricRecallOperation
@dataclass(frozen=True) ParametricRetrievalPlan
@dataclass(frozen=True) ParametricMemoryRecord
@dataclass(frozen=True) ParametricRetrievalResult
```

Plus `ParametricRetriever`, `RetrievalConfig`, `RetrievalError`,
`build_parametric_retriever`, `OPERATION_SPECS`, `DEFAULT_OPERATIONS`,
`RETRIEVAL_VERSION`, `operation_catalogue`, `classify_output`,
`prompt_digest`, `program_digest`, `RETRIEVAL_SYSTEM_PROMPT`.

All immutable, equality-comparable and JSON round-tripping.

---

## 6. ParametricRetrievalPlan

```python
retrieval_version, compiler_version, profile_version : str
program_sha256 : str            # identity of the exact prompt program
subject, relation : str ; row_index : int
program_type : ProgramType ; specialist_hint : str
operations : tuple[ParametricRecallOperation, ...]
```

Derived: `max_operations`, `estimated_calls`, `independence_groups`.

`ParametricRecallOperation` carries `operation_id`, `kind`,
`independence_group`, `purpose`, `prompt`, `system_prompt`, `decode_profile`,
`expected_output_kind`, `sample_index`, `estimated_calls` and a `prompt_sha256`
property.

**A plan is produced without calling a model**, so its cost is knowable before
any spend — which is exactly what a future Module 20 needs in order to schedule
it. M11 allocates nothing itself.

---

## 7. Operation families implemented

Three, matching the proposal exactly:

| Family | Proposal wording | Independence group | Output | Decode |
| --- | --- | --- | --- | --- |
| `pseudo_memory` | "The model writes a relation-focused memory sketch." | `PSEUDO_MEMORY_SKETCH` | PROSE | greedy, 192 tok |
| `self_ask` | "Self-ask decomposes a query into follow-up subquestions; the follow-ups are answered by a frozen model, not by a search engine." | `SELF_ASK_DECOMPOSITION` | QA_PAIRS | greedy, 256 tok |
| `query_rewrite` | "M11 rewrites the query using a missing facet/negative constraint instead of merely changing the random seed." | `QUERY_REWRITE` | follows M10's answer schema | greedy, 192 tok |

**Two brief families were not implemented as separate probes**, applying the
brief's own rule:

* **Direct parametric recall** (brief §7A) is not among the proposal's three
  families, and Module 2 already owns direct elicitation. Adding it would both
  exceed the declared family set and duplicate an existing acquisition view.
* **Contrast-conditioned recall** (brief §7E) is *folded into* `query_rewrite`,
  because §7.2 defines that family as rewriting "using a missing facet/**negative
  constraint**". The rewrite frame is built around Module 10's exclusions and
  near-miss anchors, which is what makes it structurally different from the
  other two rather than a reworded repeat.

Both omissions are surfaced in the final response so they can be overridden.

---

## 8. Prompt construction from Module 10

Every frame is *generic*: it contains no relation name, no relation definition
and no factual content. Everything relation-specific is read from the structured
`PromptProgram`. Tests assert no official relation name and no contract
definition appears anywhere in M11's executable code.

| Probe | Module 10 fields consumed |
| --- | --- |
| `pseudo_memory` | `task_semantics.relation_focus`, `.definition`, `subject_directives`, `semantic_cues`, `negative_anchors` |
| `self_ask` | `query_specification.{relation_focus, semantic_question, abstraction_cues}`, `task_semantics.definition`, `subject_directives` |
| `query_rewrite` | `task_semantics.*`, `positive_constraints`, `negative_constraints`, `negative_anchors`, `risk_directives`, `subject_directives`, `output_contract` |

`program_preview()` and `fragments()` are never used: the structure is read
directly, never parsed back out of prose. A test asserts the templates module
references neither.

---

## 9. Independence-group design

`ParametricIndependenceGroup` is a **separate enum** from the core
`IndependenceGroup`, and a test asserts the two value sets are disjoint. Reusing
the core groups would silently enrol parametric recall into `q(o) = g(o)/m(o)`
and change what the production system counts as independent support — exactly
what shadow mode forbids. Mapping M11 groups onto core evidence groups is a
decision for the specialist layer and Module 16, once consensus exists to make
it.

Resamples share their family's group: with `samples_per_operation: 3` a plan has
three operations, three distinct `operation_id`s and **one** independence group.
Repetition buys evidence volume, never structural diversity — the original COVER
principle, preserved.

A greedy resample would return an identical string, so a resample switches to
sampling with a seed derived from `sample_index`. The run stays reproducible.

---

## 10. Pseudo-memory provenance

`ParametricMemoryRecord` fixes two fields **by construction**, not by
configuration:

* `source = FROZEN_MODEL_PARAMETRIC_MEMORY` — constructing a record with any
  other source raises;
* `verified = False` — constructing a verified record raises, naming the reason:
  it would mean the blind-verification invariant had been bypassed.

`ParseStatus.OK` is a **shape** check, never a truth claim. A test feeds the
runtime a confidently fictitious answer, asserts `OK`, and asserts `verified` is
still `False`: M11 cannot tell the difference and does not pretend to.

Vocabulary is enforced. A test scans M11's executable code for
`retrieved_fact`, `retrieved_document`, `source_url`, `document_id` and fails on
any. The code says *recalled statement*, *pseudo-memory*, *parametric recall*.

Per §7.2, nothing here creates a support edge. Turning a record into candidate
evidence is a specialist decision, and the evidence-hygiene rule means this text
must never reach the verifier verbatim.

---

## 11. Why M11 is not external RAG

```
classical RAG:  query -> external retriever -> documents -> LLM
COVER M11:      query -> deterministic prompt transformation
                      -> frozen LM parametric memory -> model-generated recall
```

Enforced four ways:

* **AST import scan.** No M11 module imports anything whose root is `requests`,
  `httpx`, `urllib`, `socket`, `http`, `aiohttp`, `ftplib`, `smtplib`, `sqlite3`,
  `faiss`, `chromadb` or `pinecone`.
* **Executable-code scan.** No `search(`, `retrieve_documents`, `vector_db`,
  `vectorstore`, `wikipedia`, `wikidata`, `elasticsearch`, `http://`,
  `https://`, `api_key`, `endpoint` or `corpus`. The scan strips docstrings and
  comments first: these modules describe at length what they must *not* do, and
  a raw text scan would match the prohibition rather than a violation.
* **No new loader.** M11 imports `cover_kbc.models.base` and nothing else from
  the model layer; `from_pretrained`, `AutoModel`, `build_runtime`, `torch` and
  `transformers` are all absent. The runtime is *supplied*, never constructed.
* **Clean-subprocess probe.** Importing and constructing a retriever loads no
  `torch`, `transformers` or `mistral_common`.

Semantic cues are prompt text placed *inside* the prompt to steer the frozen
model's own attention. Nothing is sent anywhere.

**One structural consequence, recorded because it changed an earlier guarantee.**
M11 legitimately uses the runtime abstraction, and re-exporting it eagerly from
`query_intelligence/__init__.py` made `import ...query_intelligence.profiler`
pull in `cover_kbc.models.registry` as a side effect — which would have quietly
weakened Modules 9 and 10's zero-backend guarantee. M11's exports are therefore
**lazy** (PEP 562 `__getattr__`), and Audit 0016's and 0017's probes now import
M9's and M10's own modules rather than the package aggregate. The guarantee is
narrower in wording and identical in force: *those modules* still load no model
backend, verified in a clean interpreter.

---

## 12. Why M11 is not M2

Module 2 executes the relation-program acquisition views of the original COVER
core. M11 is a new query-intelligence layer built on M9 and M10.

* M11 references no `ViewSpec`, `views_for`, `get_view` or `ElicitationEngine`.
* M11 has its own `RETRIEVAL_SYSTEM_PROMPT`, distinct from Module 2's, and a
  test asserts they differ.
* M11's operation ids are used as the offline runtime's script key, so M11 and
  production fixtures are structurally disjoint and neither can consume the
  other's outputs.
* Module 2's `SYSTEM_PROMPT`, `ENTITY_FORMAT` and `NUMERIC_FORMAT` are asserted
  unchanged.
* M11 output feeds no M2/M3 decision in this milestone. The ablation ladder —
  core / +M9,M10 / +M11 / +specialists — is preserved by the config flags.

---

## 13. Why M11 is not M4

M11 asks *"what does the model recall?"*. M4 asks *"does this candidate satisfy
the exact relation?"*.

* No `VerificationLabel`, `score_labels`, `LABEL_TOKENS`, `VerifierTemplate`,
  `A = VALID` or `adversarial` anywhere in M11.
* No record carries a VALID/INVALID decision, acceptance threshold or verifier
  score. `ParseStatus` is about shape.
* Module 4's entire prompt surface is pinned by sha256
  `3acd7109…e6d874` — the same constant Audit 0017 introduced, re-asserted here.

---

## 14. Why M11 does not implement M12–M21

M11 is generic infrastructure that exposes material those modules will consume;
it makes none of their decisions. A test scans M11's executable code and fails
on `cluster`, `convert_unit`, `facet_plan`, `closure`, `existence_gate`,
`consensus`, `missingness_estimate`, `schedule_budget`, `allocate_budget` and
`expected_value`.

Concretely absent: numeric clustering and unit conversion (M12), award facet
planning and closure (M13), the death-status state machine (M14), stock/border
closure (M15), atomic consensus (M16), specialist verification (M17), dispute
resolution (M18), missingness estimation (M19), budget scheduling (M20),
expected-value planning (M21).

The deferred query-rewrite *trigger* (§2) is part of this boundary: it depends
on yield and disagreement, which are M19 and M21 signals.

---

## 15. Runtime integration

M11 receives an `LMRuntime` and calls `runtime.generate(GenerationRequest(...))`
— the same abstraction the production enumerator uses. No second inference path,
no model loader, no decode logic of its own: decode profiles are declared in
`OPERATION_SPECS` using the repository's `DecodeProfile`.

Request metadata carries `view_id` (the operation id), `subject`, `relation` and
`module: "M11"`, so a trace or an offline fixture can attribute every call.

Defaults are conservative: greedy everywhere, token limits sized to the shape
each probe asks for. None was chosen from measured performance.

---

## 16. Call and token accounting

**M11 spends neural calls.** The guarantees are:

* **Zero new parameters.** No weights, no checkpoint, no third model. The budget
  audit is unchanged at 28.67 B.
* **Every call attributable.** One operation is one call, tagged with its
  `operation_id`, `kind`, `independence_group` and `prompt_sha256`.
* **Counted exactly once, and measured not assumed.** Cost is a delta taken
  around each call from the runtime's own counters. A test with a runtime that
  never increments its counter yields `total_calls == 0`: a record cannot
  over-claim. A three-probe run gives `[1, 1, 1]`, `total_calls == 3 ==
  runtime.calls`, and equal generated-token totals — no phantom call, no double
  charge.
* **Outside Module 7's budget.** Shadow probes run at the M10 seam, before
  `build_graph`, and never call `budget.charge`. A test compares
  `graph.budget_snapshot` against an identical run with no retriever and asserts
  equality — the three shadow calls are outside the budget, not merely small.
* **Honest totals.** `pipeline.shadow_calls` and `shadow_generated_tokens`
  accumulate the real spend, and Phase A prints
  `[M11] parametric memory: … (N queries, C shadow calls, T generated tokens)`.
* **Production figures stay comparable.** `run_staged`'s per-query progress
  figure subtracts `shadow_calls`, so a run with M11 on shows the same
  production `calls=` as one with it off. Nothing is hidden: the shadow spend is
  reported per record and in the Phase A summary.

---

## 17. Shadow-mode behaviour

`mode: shadow` is the only supported value; anything else raises, naming the
specialist layer as the missing consumer. In shadow mode M11 produces records
and nothing consumes them:

* no candidate is created, no evidence edge is added — a test builds a graph,
  runs M11 against it and asserts candidate, record and edge counts are
  unchanged;
* M11 references no `EvidenceGraph`, `build_graph`, `add_candidate` or
  `Evidence(`;
* a test serialises the graph after a pipeline run with M11 enabled and asserts
  no `parametric`, `pseudo_memory`, `self_ask` or `prompt_sha256` appears in it.

**Disabled by default in every shipped config**, and a test asserts it: M11
costs real calls, so it must be opted into rather than inherited.

---

## 18. Disabled-path invariance and artefact comparison

The real staged CLI, run twice with identical arguments and configs differing
only in `query_intelligence.parametric_retrieval.enabled` (M9 and M10 on in
both).

Parameterised over three relations spanning three programmes:

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
query_profiles.jsonl     IDENTICAL      <- M9's artefact unchanged
prompt_programs.jsonl    IDENTICAL      <- M10's artefact unchanged
```

Repeated on the role-swap config (`awardWonBy`), including
`stage_r1_enumerator.jsonl`. `parametric_memory.jsonl` is present with M11 on
and absent with it off.

`stage_a_enumerated.jsonl` matching is the strong result: candidates, evidence
edges, controller log, RCSE state and budget snapshot are unchanged, so every
M2–M8 decision was identical.

**Why an interposed call cannot perturb production, even with a real model.**
Sampled production views receive an explicit per-run seed
(`elicitation/engine.py`: `decode = replace(decode, seed=self.seed + run_id)`),
and `HuggingFaceRuntime.generate` calls `torch.manual_seed(seed)` before
decoding. Production generation therefore does not depend on ambient RNG state,
so an M11 call between two production calls cannot change either. Under the
scripted runtime the argument is stronger still: M11's operation ids are
distinct script keys, so no production cursor advances.

---

## 19. Persistence

`parametric_memory.jsonl`, one line per **executed probe** — three lines per
query under the default plan. Written in Phase A alongside the M9 and M10
artefacts, in manifest order.

Each row carries: `retrieval_version`, `compiler_version`, `profile_version`,
`program_sha256`, `SubjectEntity`, `Relation`, `row_index`, `program_type`,
`operation_id`, `kind`, `independence_group`, `source`, `verified`,
`parse_status`, `raw_output`, `model_id`, `model_revision`, `decode_profile`,
`prompt_sha256`, `calls`, `generated_tokens`, `prompt_tokens`, `latency_ms`,
`error`, `sample_index`.

A test asserts every key is present, that `source` is
`FROZEN_MODEL_PARAMETRIC_MEMORY` and `verified` is `false`, and that no `url`,
`document_id`, `gold`, `ObjectEntities` or `label` appears.

`query_profiles.jsonl`, `prompt_programs.jsonl`, `stage_a_enumerated.jsonl`,
`stage_b_verified.jsonl` and `predictions.jsonl` are untouched — §18 proves it
byte for byte.

---

## 20. Error handling

Five distinguished failure modes, none silent:

| Situation | Result |
| --- | --- |
| Input identity disagreement | `RetrievalError`, listing every problem |
| M11 enabled without M9/M10 | `ValueError` at config *and* at the pipeline constructor |
| Invalid config | `ValueError` naming the key, mode, operation or value |
| Runtime raises | `ParseStatus.RUNTIME_ERROR`, empty `raw_output`, `error` populated, run continues |
| Empty / abstained / malformed output | `EMPTY` / `ABSTAINED` / `MALFORMED`, text retained |

A runtime failure fabricates nothing: `raw_output` is `""`. One failing probe
does not kill the others — a test makes `self_ask` raise and asserts
`query_rewrite` still returns `OK` with exactly one recorded error.

Malformed output is **kept**, not discarded: a later specialist may still find
it useful, and deleting information M12–M15 might need would be the wrong
default.

---

## 21. Config schema

```yaml
query_intelligence:
  profiler:              {enabled: true,  mode: shadow}    # M9, required
  prompt_compiler:       {enabled: true,  mode: shadow}    # M10, required
  parametric_retrieval:                                     # M11
    enabled: false        # default; M11 spends real calls
    mode: shadow
    retrieval_version: m11-v1
    operations: [pseudo_memory, self_ask, query_rewrite]
    samples_per_operation: 1
```

Validation, each with a test: M11 without M9/M10 raises at both boundaries;
unsupported mode raises; unknown key raises; unknown operation raises naming the
three legal families; duplicate operation raises, pointing at
`samples_per_operation`; a non-list `operations` raises; an empty operation set
raises; `samples_per_operation < 1` raises.

No relation-specific override exists — there is no place to put one, since M11
holds no relation language. No hidden call count: the plan's `estimated_calls`
is derivable before execution. No hidden decode parameters: every profile is
declared in `OPERATION_SPECS`.

---

## 22. ScriptedRuntime evidence

No real weights were downloaded or run. All execution tests use the repository's
existing `ScriptedRuntime`, keyed by `(view_id, subject, relation)` with the
operation id as `view_id`. Simulated outputs cover a prose sketch, Q/A pairs, an
entity list, an abstention, whitespace, malformed prose, a raising runtime and a
counter-silent runtime.

An end-to-end scripted staged run over `hasCapacity` produced:

```
[M9]  query profiles: …  (2 queries)
[M10] prompt programs: …  (2 queries)
[M11] parametric memory: …  (2 queries, 6 shadow calls, 6 generated tokens)
[PHASE A] [1/2] … calls=2      <- production figure unchanged
```

---

## 23. Test results

```
python -m pytest -q
    1246 passed, 3 skipped        (1166 before; +80)
```

`tests/test_parametric_retrieval.py`, 80 tests, covering all 24 required areas:
proposal family set; M9/M10 dependency at both boundaries; five parameterised
identity disagreements; AST and code scans for external retrieval; no new model
loader; six-relation planning; per-family scripted execution; PromptProgram
authority for all three frames; no relation scattering; distinct groups and
shared resample groups; disjointness from the core groups; provenance and the
un-constructable verified record; no evidence mutation; no M4 semantics; the M4
hash pin; no specialist logic; exact call accounting including a
counter-silent runtime; stable prompt hashes and operation ids; JSON round-trip
for all six relations; empty/abstained/malformed/failing responses; shadow
invariance over three relations plus the role-swap path; budget isolation
against a no-retriever baseline; persistence ordering and full provenance; nine
configuration-failure cases; and a zero-new-parameters subprocess probe.

Four test defects were found and fixed during development, all in the tests:
three raw-text scans matched the modules' own prohibition prose (fixed by
stripping docstrings and comments via AST/tokenize before scanning), and one
budget assertion expected `calls_used == 0` when production had legitimately
spent 2 — replaced by a comparison against a no-retriever baseline, which is the
stronger check.

---

## 24. pyflakes

```
python -m pyflakes src/ tests/ scripts/
    clean
```

---

## 25. Model budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    Qwen/Qwen3.5-4B                                [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506  [enumerator] 24.011B (verified)
    total: 28.67B
    RESULT: PASS
```

Unchanged. M11 adds zero inference-time parameters — it reuses the frozen
enumerator.

---

## 26. Benchmark integrity

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no VAL or TEST gold was read.

---

## 27. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M11 value.** The
probe families come from the proposal. Decode profiles are greedy with token
limits sized to the requested output shape. The default plan is one probe per
declared family — the smallest set that represents the architecture, not a
tuned allocation. `samples_per_operation` defaults to 1. Relation-aware
allocation is Module 20's, and M11 deliberately makes none.

---

## 28. Challenge compliance

Allowed and used: inference-only prompting, multiple calls to the frozen
open-weight model, model-generated pseudo-memory, deterministic prompt
rewriting, semantic-cue injection, rule-based parsing and metadata.

Absent and structurally prevented: external RAG, web search, external corpus,
Wikipedia, Wikidata, KB lookup, document retrieval, vector search, any factual
cache not originating from the current frozen-model run, fine-tuning, LoRA,
continued pretraining, learned retriever, learned router, any task-trained
neural component.

Every factual-looking string M11 produces originates from the frozen model's own
generation, and is recorded as such.

---

## 29. Non-goals

M12–M21 remain unimplemented, and no placeholder files were created:

M12 Numeric Specialist · M13 Large-Open-Set Specialist · M14 Null/Temporal
Specialist · M15 Small-Set Closure Specialist · M16 Atomic Consensus Engine ·
M17 Specialist Verifier Suite · M18 Bidirectional/Counterfactual/Dispute
Verifier · M19 Coverage Gap & Missingness Estimator · M20 Relation Budget
Scheduler · M21 Expected-Value Micro-Planner.

---

## 30. Verdict

**PASS.**

M11 is implemented as the proposal specifies: three probe families, no external
retriever, records marked unverified frozen-model parametric memory that create
no support edge. It consumes M9 and M10 through the compiled prompt program and
rebuilds neither. Its neural calls are real, attributable, counted exactly once
and kept outside Module 7's budget, while production accounting stays
comparable. Enabling it leaves predictions, candidate graphs, and M9's and M10's
own artefacts byte-identical across the staged, role-swap and interleaved paths.
It is disabled by default in every shipped config.

Next architecture step: **M12 Numeric Specialist** — not implemented here.
