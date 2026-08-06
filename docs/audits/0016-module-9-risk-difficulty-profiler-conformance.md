# Audit 0016 — Module 9: Risk & Difficulty Profiler Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: first module of the upgraded COVER-KBC architecture (M9 of M9–M21).
Mode: **shadow** — no M0–M8 decision consumes the profile.

---

## 1. Objective and scope

Implement **M9 Risk & Difficulty Profiler**: deterministic, closed-book,
non-neural profiling of a `(SubjectEntity, Relation)` query *before* candidate
acquisition, producing a typed vector of interpretable inference risks for
Modules 10–21 to consume later.

In scope: the profiler, 



its typed contract, its versioned relation priors, its


configuration surface, its observability artefact, and the M1→M9→M2 seam.

Out of scope and not implemented: M10–M21. No placeholder files were created
for them. M9 does not select prompts, does not route, does not answer, and does
not consume evidence.

Frozen and unchanged: the M0–M8 core, the model profile
(Mistral-Small-3.2-24B-Instruct-2506 + Qwen3.5-4B, 28.67 B published), every
threshold, and `benchmark/`.

---

## 2. Architecture position

```
M0 Relation Compiler          contracts/registry.py
        v
M1 Typed Program Router       contracts/router.py      (authoritative)
        v
M9 Risk & Difficulty Profiler query_intelligence/      <- this milestone
        v
[future M10 Prompt Program Compiler / M11 Parametric Retrieval]
        v
M2 Diverse Elicitation        elicitation/             (unchanged)
```

The seam is one place: `CoverPipeline.enumerate_query`, immediately after
`compile_query` (M0 + M1) and immediately before `build_graph` (start of
acquisition).

```python
query, contract = compile_query(query.subject, query.relation, query.row_index)
if self.profiler is not None:
    self.query_profiles.append(self.profiler.profile(query, contract))
graph = build_graph(query, contract)
```

`self.profiler` defaults to `None`, which is the pre-M9 code path byte for
byte. The profile goes to an observability buffer and never to the graph.

---

## 3. Proposal requirements implemented

The proposal (§5) specifies the risk vector
`Q = (q_card, q_temp, q_num, q_open, q_amb, q_novel, q_verify)`. This milestone
implements it as ten separately interpretable axes, which refine rather than
replace it:

| Proposal | Implemented as |
| --- | --- |
| `q_card` | `cardinality_regime` (derived from M1, not declarable) |
| `q_temp` | `temporal_sensitivity` |
| `q_num` | `numeric_ambiguity` + `format_sensitivity` (unit/granularity is a separate failure from value ambiguity) |
| `q_open` | `open_set_risk` + `missingness_risk` + `search_breadth` |
| `q_amb` | `identity_ambiguity` + `near_miss_risk` (entity-level vs answer-level confusion are different problems) |
| `q_verify` | `verification_priority` |
| — | `nullability_risk` (the dominant failure for `personHasCityOfDeath` and `companyTradesAtStockExchange`) |
| `q_novel` | **deliberately not implemented** — see below |

Proposal Table 3's routing appears as an advisory `specialist_hint`, a pure
function of the M1 programme (§9).

**Why `q_novel` is absent.** "How novel/obscure is this entity" is a claim about
the entity, and a static closed-book profiler can only reach it by inferring
factual properties from the subject string — exactly what the milestone brief
forbids ("Do NOT infer factual properties of the entity from the subject
string"). It becomes estimable once evidence exists, which is M19's input
(`graph + facet registry`), not M9's. Declaring it here would either be a
guess or a leak of M19's responsibility. Recorded as a deliberate deferral
rather than an omission.

Two further deviations from the proposal, both following the milestone brief
where the two documents differ:

* The proposal lists M9's input as "QuerySpec + early graph". The brief scopes
  M9 to the **initial** profiler with dynamic difficulty assigned to M19–M21
  (§6), and the seam is before acquisition, so no graph exists yet. M9 takes
  the query only.
* The brief asks for a `has_location_qualifier` surface feature. It is
  implemented as **`has_prepositional_qualifier`** — see §8 for why the honest
  name matters.

---

## 4. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/query_intelligence/__init__.py` | **New.** Public surface. |
| `src/cover_kbc/query_intelligence/types.py` | **New.** `RiskLevel`, `CardinalityRegime`, `SpecialistHint`, `SubjectSurfaceFeatures`, `QueryRiskProfile`, `RISK_AXES`. |
| `src/cover_kbc/query_intelligence/priors.py` | **New.** `RELATION_RISK_PRIORS`, programme→regime and programme→hint maps, `check_priors_consistency`, config overlay. |
| `src/cover_kbc/query_intelligence/profiler.py` | **New.** `subject_surface_features`, `ProfilerConfig`, `QueryProfiler`, `build_profiler`. |
| `src/cover_kbc/pipeline.py` | `CoverPipeline` gained `profiler=None` and a `query_profiles` buffer; 3 lines at the M1 seam. Nothing else. |
| `scripts/run_staged.py` | Builds the profiler for Phase A; writes `query_profiles.jsonl`. |
| `scripts/run_cover.py` | Same, for the interleaved runner. |
| `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` | `query_intelligence.profiler` block. |
| `configs/experiments/smoke_staged_scripted.yaml`, `smoke_staged_roleswap.yaml` | Same block, so the smokes exercise M9. |
| `tests/test_query_profiler.py` | **New.** 52 tests. |

No M0–M8 logic was refactored. No integration blocker was found. `benchmark/`
untouched. No Claude-specific files.

---

## 5. Public contract

```python
class RiskLevel(str, Enum):        # NONE < LOW < MEDIUM < HIGH
class CardinalityRegime(str, Enum) # ZERO_OR_ONE | SMALL_SET | NUMERIC_SINGLE | LARGE_OPEN_SET
class SpecialistHint(str, Enum)    # NONE | M12_NUMERIC | M13_LARGE_SET | M14_NULL_TEMPORAL | M15_SMALL_SET_CLOSURE

@dataclass(frozen=True)
class SubjectSurfaceFeatures:
    token_count, char_length: int
    has_parenthetical, has_comma_qualifier, has_prepositional_qualifier: bool
    has_digit, has_non_ascii, has_internal_punctuation: bool
    has_disambiguation_marker  # derived property

@dataclass(frozen=True)
class QueryRiskProfile:
    relation, subject: str
    row_index: int
    program_type: ProgramType            # from M1
    cardinality_regime: CardinalityRegime
    <the ten RiskLevel axes>
    subject_surface: SubjectSurfaceFeatures
    specialist_hint: SpecialistHint
    profile_version: str
```

Frozen, hashable-by-value, `==`-comparable, `to_json`/`from_json` round-tripping.

`RiskLevel` is ordered **explicitly**, not by its `str` base: `"HIGH" < "LOW"`
alphabetically, which is the opposite of every caller's meaning. `__lt__`,
`__le__`, `__gt__`, `__ge__` are overridden and tested.

---

## 6. Risk axes and why each exists

| Axis | Question it answers | Future consumer |
| --- | --- | --- |
| `open_set_risk` | How large is the plausible answer universe? | M13 facet planning, M20 budget |
| `missingness_risk` | Can a complete-looking answer still be incomplete? | M19 gap estimation |
| `numeric_ambiguity` | How many defensible numeric answers exist? | M12 clustering, M17 numeric verifier |
| `temporal_sensitivity` | Does the answer depend on *when* it is asked? | M14 freshness, cross-family recall |
| `nullability_risk` | How likely is the empty set correct? | M14 existence gate |
| `identity_ambiguity` | Does the subject string denote one entity? | M10 query specification, M11 |
| `near_miss_risk` | How likely is a plausible-but-wrong neighbour emitted? | M17/M18 adversarial verification |
| `format_sensitivity` | Does the answer's unit/granularity/integer-ness matter? | M10 output contract, M12 normalisation |
| `verification_priority` | Verify harder or recall wider? | M20 budget split, M21 action value |
| `search_breadth` | How wide must acquisition be? | M20 compute envelope |

`cardinality_regime` is not a risk axis; it is M1's answer restated in profiler
vocabulary (§9).

---

## 7. Six-relation prior table

| Relation | Prog | open | miss | num | temp | null | ident | near | fmt | verif | breadth |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `countryLandBordersCountry` | SMALL_SET | LOW | MED | NONE | LOW | LOW | LOW | **HIGH** | LOW | MED | LOW |
| `companyTradesAtStockExchange` | SMALL_SET | LOW | MED | NONE | **HIGH** | **HIGH** | **HIGH** | **HIGH** | LOW | **HIGH** | LOW |
| `personHasCityOfDeath` | NULL_SINGLE | NONE | NONE | NONE | **HIGH** | **HIGH** | **HIGH** | **HIGH** | MED | **HIGH** | LOW |
| `hasCapacity` | NUMERIC | NONE | NONE | **HIGH** | MED | NONE | MED | **HIGH** | **HIGH** | MED | LOW |
| `hasArea` | NUMERIC | NONE | NONE | **HIGH** | LOW | NONE | MED | MED | **HIGH** | MED | LOW |
| `awardWonBy` | LARGE_OPEN_SET | **HIGH** | **HIGH** | NONE | MED | LOW | MED | **HIGH** | MED | **HIGH** | **HIGH** |

Each row carries a prose `rationale` in `priors.py`, and a missing rationale is
a hard error: a judgement with no stated reason cannot be reviewed.

These are **risk declarations about relations**, never facts. "Stock listings
are temporally sensitive" says nothing about which company lists where. A test
asserts the priors module names no real entity.

`check_priors_consistency()` — the counterpart of `check_router_consistency()` —
enforces every *hard implication* the contracts already carry, so a declaration
cannot drift away from M0/M1:

* numeric relation ⟺ non-zero `numeric_ambiguity`;
* `allows_empty` ⟺ non-zero `nullability_risk`;
* `program.supports_missingness == False` ⟹ `missingness_risk == NONE`;
* `LARGE_OPEN_SET` ⟹ `open_set_risk == HIGH`; single-object regimes ⟹ `NONE`;
* a contract naming `adversarial_classes` ⟹ non-zero `near_miss_risk`;
* every contract has priors and vice versa; every programme has a regime and a hint.

Debatable grades stay declarations; self-contradictory ones fail loudly.

---

## 8. Subject-surface feature table

| Feature | Rule | Example |
| --- | --- | --- |
| `token_count` | whitespace-split length | `"Wellington Island"` → 2 |
| `char_length` | NFC-normalised length | `"Köln"` composed == decomposed |
| `has_parenthetical` | contains `(` and `)` | `"Mercury (planet)"` |
| `has_comma_qualifier` | contains `,` | `"Springfield, Illinois"` |
| `has_prepositional_qualifier` | standalone `in`/`at`/`on` token | `"Estadio X in Madrid"` |
| `has_digit` | any digit | `"Boeing 747"` |
| `has_non_ascii` | non-ASCII after NFC | `"東京"`, `"Köln"` |
| `has_internal_punctuation` | any of `. ' " / & -` | `"St. Mary's"` |

**No cutoffs.** `token_count` and `char_length` are reported raw and never
bucketed into "unusually short/long". Any such boundary would be an arbitrary
constant with no principled value, and the brief prefers avoiding unnecessary
cutoffs entirely. A test asserts no `is_*`/`unusually_*` field exists, so none
can quietly appear and become a tuning target.

**On the name `has_prepositional_qualifier`.** The brief asked for
`has_location_qualifier`, with `"Estadio X in Madrid" → true` as the example.
That example is satisfied. The field is not *called* a location qualifier
because `"Nobel Prize in Physiology or Medicine"` is structurally identical and
is not locational, and separating them requires knowing what the words denote —
world knowledge a closed-book structural profiler does not have. Naming the
field for what it measures keeps it from becoming a false factual claim to a
later module. Both cases are asserted in tests.

---

## 9. Why M9 does not duplicate M1

M1 remains the sole router. M9 has **no code path that selects a programme**:

* `profile()` reads `contract.program` — Module 1's own accessor — and nothing
  else. There is no relation→programme table in the M9 package.
* Passing a programme in is a *shortcut for callers that already resolved it*,
  not a vote: a disagreeing programme raises
  `"Module 9 consumes the router and cannot override it"`.
* `cardinality_regime` comes from one total map keyed on `ProgramType`
  (`CARDINALITY_REGIME_BY_PROGRAM`) and is absent from the per-relation prior
  table, so no relation can state one independently.
* `specialist_hint` is keyed on `ProgramType` too. Two relations sharing a
  programme necessarily share a hint — asserted for borders/stock — so the hint
  cannot encode a per-relation routing decision.
* A mismatched `(query, contract)` pair raises rather than being profiled.

There is no `if relation == ...` anywhere in the profiler: relation-specific
behaviour is one data table, per the architecture invariant.

---

## 10. Static M9 risk vs dynamic M19–M21 state

M9 answers **"what kind of problem is this query likely to be?"** — computable
before a single token is generated. It never answers *"given the current
candidate graph, what should we do next?"*

Structurally guaranteed, not merely intended: the seam is *before*
`build_graph`, so no graph exists when `profile()` runs, and `profile()` takes
no graph, no evidence, no budget and no state. A profile that changed as
evidence arrived would not be a profile, it would be state.

Explicitly left to later milestones: dynamic search incompleteness (**M19**),
compute allocation (**M20**), next-action expected value (**M21**).
`missingness_risk` here is a *static prior* on a relation, not a residual
estimate for a query in progress.

---

## 11. Evidence of zero neural cost

* **Structural.** No file in `query_intelligence/` mentions `LMRuntime`,
  `models.registry`, `models.huggingface`, `requests` or `urllib` — asserted by
  a source scan.
* **Import-level.** A subprocess with a clean interpreter imports the package,
  profiles all six relations, and reports which of `torch`, `transformers`,
  `mistral_common`, `requests`, `urllib.request` or any `cover_kbc.models.*`
  module was loaded. The assertion is that the list is **empty**. Run in a
  subprocess deliberately, because the test session has already imported those
  modules for other reasons and would mask the result.
* **Counter-level.** A `ScriptedRuntime`'s `calls` and `generated_tokens` are
  `0` before and after profiling six queries.
* **End-to-end.** `diagnostics.json` is byte-identical with M9 on and off
  (§13), so `total_calls`, `total_verification_calls` and
  `total_generated_tokens` are all unchanged.

Profiling one query is a dict lookup and one pass over a short string. No I/O.

---

## 12. Shadow-mode integration proof

* `CoverPipeline(profiler=None)` is the default and is the pre-M9 path; a test
  asserts `pipeline.profiler is None` and `query_profiles == []`.
* With a profiler attached, the profile is appended to `pipeline.query_profiles`
  and nothing else. A test serialises the resulting `EvidenceGraph` and asserts
  none of `profile_version`, `risk`, `specialist_hint`, `subject_surface`
  appears anywhere in it.
* No M2–M8 function reads `self.profiler` or `self.query_profiles`; the only
  readers are the two CLIs, at artefact-writing time.
* `mode` is validated: anything other than `shadow` raises, naming Module 10 as
  the missing consumer. The system cannot be configured to act on M9 output
  because nothing exists to act on it.
* Staged execution: the profiler is built for Phase A only — the sole phase that
  crosses the M1 seam — so Phases B/C and the resume cycles are untouched.

---

## 13. Prediction and call-count invariance evidence

The real staged CLI, run twice over the scripted backend with identical
arguments and configs differing only in `query_intelligence.profiler.enabled`,
compared byte for byte.

Parameterised over three relations spanning three programmes (`SMALL_SET`,
`LARGE_OPEN_SET`, `NUMERIC`):

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
```

Repeated on the role-swap config (`smoke_staged_roleswap.yaml`, `awardWonBy`),
which exercises the Phase B → enumerator resume cycle:

```
predictions.jsonl          IDENTICAL
diagnostics.json           IDENTICAL
trace.jsonl                IDENTICAL
stage_a_enumerated.jsonl   IDENTICAL
stage_b_verified.jsonl     IDENTICAL
stage_r1_enumerator.jsonl  IDENTICAL
query_profiles.jsonl       present with M9 on, absent with M9 off
```

`stage_a_enumerated.jsonl` being identical is the strong result: the candidate
graphs themselves — candidates, evidence edges, controller log, RCSE state,
budget snapshot — are unchanged, so every M2–M8 decision was identical, not
merely the final answer.

**CURRENT PREDICTIONS == M9 SHADOW PREDICTIONS** holds for every fixture tested.

---

## 14. Config schema

```yaml
query_intelligence:
  profiler:
    enabled: true          # default false; false is the pre-M9 path
    mode: shadow           # the only supported value this milestone
    profile_version: m9-v1
    relation_priors:       # optional; per-relation axis overrides
      hasArea:
        near_miss_risk: HIGH
```

* No threshold is hidden in Python: every declared grade is in `priors.py`
  under `PROFILE_VERSION`, and every one can be overridden from config.
* Unknown keys at either level raise, naming what was expected. Unknown
  relations, unknown axes and non-`RiskLevel` values all raise.
* An override is re-checked against `check_priors_consistency()`: config may
  adjust a judgement, it may not contradict M0/M1. `hasCapacity:
  {numeric_ambiguity: NONE}` and `awardWonBy: {open_set_risk: LOW}` are both
  rejected.
* Building a profiler with overrides does not mutate the module-level table.

---

## 15. Serialisation and persistence

`query_profiles.jsonl`, one JSON object per query, written in **Phase A only**:

```json
{"profile_version":"m9-v1","SubjectEntity":"Wellington Island","Relation":"hasArea",
 "row_index":0,"program_type":"NUMERIC","cardinality_regime":"NUMERIC_SINGLE",
 "specialist_hint":"M12_NUMERIC",
 "risk":{"open_set_risk":"NONE","missingness_risk":"NONE","numeric_ambiguity":"HIGH",
         "temporal_sensitivity":"LOW","nullability_risk":"NONE","identity_ambiguity":"MEDIUM",
         "near_miss_risk":"MEDIUM","format_sensitivity":"HIGH","verification_priority":"MEDIUM",
         "search_breadth":"LOW"},
 "subject_surface":{"token_count":2,"char_length":17,"has_parenthetical":false,
         "has_comma_qualifier":false,"has_prepositional_qualifier":false,"has_digit":false,
         "has_non_ascii":false,"has_internal_punctuation":false}}
```

Identity: `SubjectEntity`, `Relation`, `row_index`, `program_type`,
`profile_version`. Contains no model reasoning, no external fact, no gold
object, no split label. A test pins the exact top-level key set so a factual
field cannot be added without the test failing.

**A dedicated file, not an existing artefact.** Folding profiles into
`diagnostics.json` or the staged schema would have changed files that must stay
comparable across the M9 rollout — and §13's invariance proof depends on those
files being untouched.

**Why later phases do not persist or reload it.** A profile is a total function
of `(subject, relation)` and the versioned priors, with no dependence on
evidence, seed or run order. Recomputation is exact, and a test proves it:
every persisted record is reloaded via `from_json` and compared against a fresh
`QueryProfiler().profile(...)` for equality. Passing profiles between phases
would add a correctness-critical dependency that buys nothing.

---

## 16. Test results

```
python -m pytest -q
    1075 passed, 3 skipped        (1023 before; +52)
```

`tests/test_query_profiler.py`, 52 tests, covering all twelve required areas:

1. **Determinism** — same query equal profile; equal across profiler instances;
   unaffected by `random.seed` or call order.
2. **Zero neural cost** — subprocess module probe; runtime counters; source scan.
3. **Six-relation coverage** — every relation profiles; priors match contracts exactly.
4. **Programme consistency** — `program_type` agrees with `PROGRAM_BY_RELATION`
   and the contract, for all six.
5. **No duplicate router** — a disagreeing programme raises; a mismatched
   contract raises; `cardinality_regime` and `specialist_hint` are total
   functions of the programme; borders and stock share a hint.
6. **Risk semantics** — `RiskLevel` orders by severity not alphabetically;
   capacity `numeric_ambiguity` > borders; award open-set/missingness/breadth
   HIGH and strictly above every other relation's open-set risk; death
   nullability + temporal + identity HIGH; area numeric + format HIGH; stock
   temporal + identity + nullability + near-miss HIGH; borders is a near-miss
   problem not a breadth problem; single-object regimes declare no missingness;
   exactly-one regimes declare no nullability.
7. **Surface features** — parenthetical, comma, prepositional (both the
   locational and the non-locational case), digits, Unicode incl. NFC
   composed/decomposed equality, internal punctuation, raw lengths with no
   bucketing, empty string.
8. **No factual inference** — key set pinned; two subjects of one relation share
   every risk axis while differing in surface features; priors name no entity.
9. **Round-trip** — `to_json` → `json.dumps`/`loads` → `from_json` equality for
   all six relations; stable serialisation.
10. **Shadow-mode invariance** — parameterised byte comparison over three
    relations, plus call-count equality.
11. **Staged execution** — phases keep their markers; one profile per selected
    query in manifest order; persisted == recomputed.
12. **Failure modes** — unknown relation, unsupported mode, unknown config keys
    at both levels, unknown relation/axis/value in overrides, overrides that
    contradict the contract, a prior table missing a relation, a prior with no
    rationale.

---

## 17. pyflakes

```
python -m pyflakes src/ tests/ scripts/
    clean
```

---

## 18. Model budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    Qwen/Qwen3.5-4B                                [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506  [enumerator] 24.011B (verified)
    total: 28.67B
    RESULT: PASS
```

Unchanged. M9 adds zero inference-time parameters.

---

## 19. Benchmark integrity

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no VAL or TEST gold was read at any point.

---

## 20. Challenge compliance

M9 is non-neural deterministic infrastructure. It performs no web access, no
retrieval, no RAG, no Wikipedia/Wikidata/KB lookup, and consults no external
factual corpus — it opens no network connection and reads no file. It contains
no fine-tuning, LoRA, continued pretraining, learned classifier, learned router,
learned calibrator or any hidden neural component. Its only inputs are the
compiled relation contract, the routed typed programme, versioned configuration
and the literal subject string.

---

## 21. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M9 value.** Every
grade in `RELATION_RISK_PRIORS` was derived from the relation's own contract
semantics — its definition, positive rules, hard-negative rules, cardinality and
declared near-miss classes — and each carries a written rationale. Nothing was
fitted, searched or scored. There are no learned parameters, and there are no
surface-feature cutoffs to tune.

---

## 22. Non-goals

M10–M21 remain unimplemented, and no placeholder files were created for them:

M10 Prompt Program Compiler · M11 Closed-Book Parametric Retrieval ·
M12 Numeric Specialist · M13 Large-Open-Set Specialist · M14 Null/Temporal
Specialist · M15 Small-Set Closure Specialist · M16 Atomic Consensus Engine ·
M17 Specialist Verifier Suite · M18 Bidirectional/Counterfactual/Dispute
Verifier · M19 Coverage Gap & Missingness Estimator · M20 Relation Budget
Scheduler · M21 Expected-Value Micro-Planner.

`specialist_hint` names M12–M15 as an advisory label only; nothing dispatches on
it, and the enum's presence implements none of them.

---

## 23. Verdict

**PASS.**

M9 is implemented, typed, versioned, config-driven and deterministic. It
consumes Module 1 and provably cannot override it. It spends zero neural calls,
zero tokens and zero verifier calls. It is closed-book. Enabling it leaves
predictions, neural call counts and candidate graphs byte-identical across the
staged, role-swap and interleaved paths. The M0–M8 core is unchanged apart from
three lines at the M1 seam and one defaulted constructor argument, and no
integration blocker was found.

Next architecture step: **M10 Prompt Program Compiler** — not implemented here.
