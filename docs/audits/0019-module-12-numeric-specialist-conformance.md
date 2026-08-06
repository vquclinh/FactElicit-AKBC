# Audit 0019 — Module 12: Numeric Specialist Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: first Layer-2 specialist (M12 of M9–M21).
Mode: **shadow**, and **disabled by default** — M12 spends real neural calls.

---

## 1. Objective and scope

Implement **M12 Numeric Specialist** for `hasCapacity` and `hasArea`: mine
numbers out of Module 11's recall, run the proposal's five numeric probe
families, canonicalise every observation, classify which quantity it denotes,
and cluster the target values with the proposal's own distance and dispersion
statistics.

In scope: the typed contract, the relation registry, numeric parsing and
canonicalisation, semantic classification, clustering, cross-unit diagnostics,
configuration, the observability artefact, and the M11→M12 seam.

Out of scope and not implemented: M13–M21. No placeholder files.

**M12 decides nothing.** No acceptance rule, no verifier label, no consensus, no
control. §20–22 below prove it.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§8** header | "M12 reframes a numeric query as a consensus problem"; the relations are `hasCapacity` and `hasArea`. |
| **§8.1** "Numeric multi-probe" | The **complete** family set — five independence groups (§9 below), including "historical/current configuration **where contract permits**". |
| **§8.2** "Canonicalization and clustering" | `delta(x_i,x_j) = |x_i - x_j| / max(|x_i|,|x_j|,eps) <= tau_cluster,r`; `x_hat = median(C*)`; `D_num = MAD(C*)/(|median(C*)|+eps)`; the state stores `independent_support`, `total_support`, `dispersion`, verifier evidence and hard-definition violations. |
| **§8.3** "UNKNOWN is not a contradiction" | The `ACCEPT` rule — explicitly **not** M12's (§20). |
| **§8.4** | "**M17** can present a small choice set" — contrastive numeric verification is Module 17's (§21). |
| **§7.1–7.2** (M11) | Probe provenance M12 consumes; the evidence-hygiene rule that pseudo-context is an acquisition artifact and creates no support edge. |
| **§6.2, §6.4** (M10) | The `PromptProgram` M12 renders from. |
| **§5** (M9) | Upstream identity M12 carries and never recomputes. |
| **§9–§14** (M13–M15) | Specialist responsibilities M12 must not take. |
| **Appendix C, Table 12** | M12 I/O: input "numeric QuerySpec + evidence", output "NumericClusterState + actions", Neural: **Mixed**. |

Also read: Audits 0016 (M9), 0017 (M10), 0018 (M11), and the current source.

### Interpretations recorded rather than resolved silently

**1. §8.2's membership rule is read as a diameter bound.** "Two values belong to
the same cluster if `delta(x_i,x_j) <= tau`" quantifies over *any two* members,
which is a diameter bound, not single-linkage chaining. That is also what the
frozen core already implements: Audit 0012 §30 established that a chaining rule
does not bound a cluster at all — at `tau = 0.025`, four chained values span 7 %
and twenty span 37 %, so the median could sit further from its own members than
the official ±5 % tolerance. The literal reading and the audited implementation
agree, so M12 reuses `cluster_values` unchanged.

**2. `tau_cluster,r` is the contract's existing per-relation declaration.** The
proposal indexes `tau` by relation; the repository already declares
`SelectionPolicy.numeric_cluster_threshold = 0.025` per relation, used by Module
8. M12 reads that rather than introducing a second tunable. A config override
exists because the proposal makes `tau` relation-indexed; its default *is* the
contract's value, and nothing was fitted.

**3. The verifier slot in §8.2's state is omitted.** The same sentence that
lists `independent_support`, `total_support` and `dispersion` also lists
"verifier VALID/INVALID/UNKNOWN evidence". M12 cannot produce that, and an
always-empty field would read as "the verifier said nothing" rather than "the
verifier has not run". `NumericClusterState` carries the four M12 can compute;
Module 17 attaches its evidence when it exists.

No material conflict between this brief and the proposal was found.

---

## 3. Architecture position

```
M0 / M1
    v
M9  QueryRiskProfile
    v
M10 PromptProgram
    v
M11 ParametricMemoryRecords
    v
M12 Numeric Specialist            <- this milestone
    v
[future M16 Consensus -> M17 Specialist Verification -> M19-M21 control]

M2 -> M3 -> ... -> M8             (unchanged production path)
```

One seam, in `CoverPipeline.enumerate_query`, nested inside the existing
M9→M10→M11 branch:

```python
if self.retriever is not None:
    retrieval = self._run_shadow_retrieval(query, program)
    if self.numeric_specialist is not None:
        self._run_numeric_specialist(query, program, contract, retrieval)
graph = build_graph(query, contract)
```

Nesting makes the stack structural: M12 cannot run without M11, which cannot run
without M10, which cannot run without M9. `numeric_specialist=None` is the
default and is the pre-M12 path.

---

## 4. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/specialists/__init__.py` | **New.** Public surface. |
| `src/cover_kbc/specialists/numeric_types.py` | **New.** `NumericObservation`, `NumericClusterState`, `CrossUnitCheck`, `NumericProbe`, `NumericSpecialistPlan`, `NumericSpecialistResult`, 4 enums. |
| `src/cover_kbc/specialists/numeric_registry.py` | **New.** `NUMERIC_RELATIONS`, the near-miss taxonomy, `check_numeric_registry_consistency`. |
| `src/cover_kbc/specialists/numeric_specialist.py` | **New.** `NumericSpecialist`, parsing, canonicalisation, classification, clustering, cross-unit checks, config. |
| `src/cover_kbc/pipeline.py` | `numeric_specialist=None` arg, `numeric_results`, `_run_numeric_specialist`, 3 lines at the seam; `_run_shadow_retrieval` now returns its result. |
| `scripts/run_staged.py` | Builds M12 for Phase A; writes `numeric_specialist.jsonl`. |
| `scripts/run_cover.py` | Same, for the interleaved runner. |
| 3 configs | `specialists.numeric` block, `enabled: false`. |
| `tests/test_numeric_specialist.py` | **New.** 136 tests. |

M0–M11 semantics are otherwise untouched. No prior test needed changing.
`benchmark/` untouched.

---

## 5-6. Public types and contracts

```python
class NumericProbeFamily(str, Enum)    # the proposal's five, in order
class NumericSemanticKind(str, Enum)   # TARGET | ATTENDANCE | SEATED_ONLY |
                                       # HISTORICAL_CONFIGURATION | LAND_ONLY |
                                       # WATER_ONLY | SURROUNDING_REGION |
                                       # UNRELATED_QUANTITY
class NumericParseStatus(str, Enum)    # OK | NO_NUMBER | ABSTAINED | AMBIGUOUS |
                                       # UNSUPPORTED_UNIT | INVALID_VALUE | RUNTIME_ERROR
class ObservationSource(str, Enum)     # PARAMETRIC_MEMORY | SPECIALIST_PROBE
```

`NumericObservation` — relation/subject/row_index, source, `operation_id`,
`independence_group`, `sample_index`, `prompt_sha256`, `model_id`, `raw_text`,
`raw_expression`, `parsed_value`, `raw_unit`, `canonical_value`,
`canonical_unit`, `semantic_kind`, `parse_status`, `ambiguity_flags`, `error`,
and `verified` fixed to `False` by `__post_init__`.

`NumericClusterState` — sorted `values`, `representative` (`x_hat`),
`dispersion` (`D_num`), `canonical_unit`, `total_support`,
`independent_support` (`I(C*)`), `independence_groups`, `member_indices`.

`NumericSpecialistPlan` — the four upstream versions, query identity,
`canonical_unit`, `cluster_tolerance`, `probes`, derived `estimated_calls`.

`NumericSpecialistResult` — plan, observations, clusters, `cross_unit_checks`,
errors, calls/tokens; derived `dominant_cluster`, `competing_clusters`,
`hard_definition_violations`. **No acceptance field of any kind.**

---

## 7. hasCapacity semantic taxonomy

Canonical: positive **integer** count of `persons`, from the contract.

| Kind | Contract rule it comes from | Example cue |
| --- | --- | --- |
| `TARGET` | "the maximum number of spectators the venue can hold" | (default when unlabelled) |
| `ATTENDANCE` | "the record or peak attendance actually achieved at an event"; "an average or typical attendance figure" | record attendance, average attendance, crowd of |
| `SEATED_ONLY` | "a seated-only capacity when the total capacity is higher" | seated capacity, all-seater |
| `HISTORICAL_CONFIGURATION` | "a smaller capacity from before or after a renovation…" | before the renovation, originally, when it opened |
| `UNRELATED_QUANTITY` | derived from the contract's spectator-count answer type | population, elevation, parking spaces |

---

## 8. hasArea semantic taxonomy

Canonical: real value in `km2`, from the contract.

| Kind | Contract rule it comes from | Example cue |
| --- | --- | --- |
| `TARGET` | "the total area including inland water" | (default when unlabelled) |
| `LAND_ONLY` | "the land-only area when the total area is larger" | land area, excluding water |
| `WATER_ONLY` | "the water area alone" | water area, of water |
| `SURROUNDING_REGION` | "the area of a surrounding metropolitan, urban or administrative region" | metropolitan area, greater …, urban area |
| `UNRELATED_QUANTITY` | "a population, elevation, length or year mistaken for an area" | population, coastline, perimeter |

Every kind carries the contract rule it derives from, and a missing rule is a
hard error. The taxonomy is **derived from Module 0, not invented**: a test
asserts each declared kind names its contract rule.

Classification is **lexical and clause-scoped**: it reads the clause the number
sits in, so `"The total area is 100 km2. The land area is 90 km2."` yields one
`TARGET` and one `LAND_ONLY`. It knows the phrase "record attendance"; it knows
nothing about any venue.

---

## 9. Specialist probe families

The proposal's five (§8.1), applied per relation:

| Family | hasCapacity | hasArea |
| --- | :---: | :---: |
| `exact_quantity_direct` | ✓ | ✓ |
| `contrastive_definition` | ✓ | ✓ |
| `cross_unit_format` | ✓ | ✓ |
| `historical_current_configuration` | ✓ | — |
| `candidate_free_reelicitation` | ✓ | ✓ |

**Why the area set omits one.** §8.1 qualifies that family with "where contract
permits". The capacity contract explicitly contemplates "before versus after a
renovation"; the area contract permits no temporal variant, and Module 9 grades
`hasArea` temporal sensitivity LOW. The omission carries a written
`family_rationale` and is asserted by a test.

Capacity therefore plans 5 probes, area 4 — knowable from the plan before any
call is spent.

---

## 10. Prompt construction from Module 10

Every probe is a generic frame plus the family's instruction. The relation's
meaning comes from Module 10 and is never restated: `task_semantics`,
`subject_directives`, `negative_constraints` (contrastive family only) and
`output_contract`. A test asserts no relation name and no contract definition
appears in M12's executable code.

---

## 11. Consumption of Module 11 parametric memory

M12 mines numbers out of `ParametricMemoryRecord.raw_output`, carrying through
`operation_id`, `independence_group`, `sample_index`, `prompt_sha256` and
`model_id` unchanged. Mining costs **zero calls** — those were paid for by M11.

Provenance and safety are preserved:

* the M11 records stay `verified = False` — asserted after mining;
* the derived observations are `verified = False` **by construction**;
* `ParseStatus.OK` from M11 is never treated as correctness — M12 re-parses the
  text itself and assigns its own status;
* nothing is passed to Module 4, which M12 cannot even reach (§21);
* a retrieval result belonging to a different query is rejected.

---

## 12. Numeric parsing rules

Parsing delegates to the audited `normalization.numeric.parse_numbers`; M12 adds
canonicalisation, semantic classification and ambiguity provenance. Verified
forms: `25,000`, `25000`, `25 000`, `25k`, `25 K`, `25,000 spectators`,
`24,800.` in a sentence, `about 24800`, `100 km2`, `100 km²`, `100 sq km`,
`100 square kilometres`, `10,000 hectares`, `10000 ha`, `38.6102 sq mi`,
`100000000 m2`.

| Situation | Status |
| --- | --- |
| No number in the text | `NO_NUMBER` |
| `UNKNOWN` / `NONE` / `n/a` | `ABSTAINED` |
| Two target readings disagreeing beyond `tau` | `AMBIGUOUS`, both flagged |
| A stated unit this relation cannot convert | `UNSUPPORTED_UNIT` |
| A physical unit on a person count | `UNSUPPORTED_UNIT` |
| Negative or zero | `INVALID_VALUE` |
| Fractional person count | `INVALID_VALUE` + `non_integer_count` |
| Runtime raised | `RUNTIME_ERROR`, no number |

**Ambiguity is scoped deliberately.** A thousands separator (`25,000`) is
resolved by the core parser's documented, audited convention and is *flagged*
(`separator_reading_by_convention`) without downgrading the status — if it did,
almost every capacity figure ever written would be "ambiguous" and the status
would carry no information. `AMBIGUOUS` is reserved for the ambiguity that
matters: one answer offering several irreconcilable target values
("either 25,000 or 40,000"), where choosing one would invent a decision the
model did not make. A cross-unit answer stating one quantity twice is **not**
ambiguous — after canonicalisation those values agree, which is the consistency
signal §8.1 asks for.

**A defect found and fixed during development.** `parse_numbers` reports
`unit=None` both when no unit was given and when an unrecognised one was;
canonicalisation initially defaulted both to the contract's unit, so
`"100 furlongs"` silently became 100 km². `stated_unrecognised_unit` now tells
them apart by inspecting the words after the number, and an unrecognised unit
becomes `UNSUPPORTED_UNIT`. Caught by the test written for brief §25.

Raw text and raw expression are always preserved. Nothing is discarded, and no
parser failure becomes a zero.

---

## 13. Capacity normalisation

Canonical form is a positive integer count of persons. A non-integer count is
**rejected, not rounded** — rounding would invent precision the model never
gave, and the contract's `numeric_integer_only` is what makes it a count.
Attendance figures are never converted into capacity evidence: they are recorded
as `ATTENDANCE` observations and excluded from the target cluster while
remaining fully visible for Modules 16 and 17.

---

## 14. Area unit conversion

Constants from `normalization.numeric.AREA_UNITS_TO_KM2` — mathematical
definitions, not looked-up values:

| Unit | → km² | Definition |
| --- | --- | --- |
| `km2` | 1.0 | identity |
| `m2` | 1e-6 | SI |
| `ha` | 0.01 | 1 ha = 10 000 m² |
| `mi2` | 2.589988110336 | (1.609344 km)² exactly |
| `acre` | 0.0040468564224 | 1/640 mi² exactly |

A test derives `mi2` from 1.609344² and `acre` from `mi2/640` and asserts
agreement to 1e-12. Original value and unit are stored alongside the canonical
pair; the original representation is never destroyed.

---

## 15. Cross-unit consistency

`cross_unit_checks` compares every pair of target observations stated in
*different* raw units after canonicalisation, recording both canonical values,
their `relative_distance` and whether they agree within `tau`. Same-unit pairs
are skipped — they say nothing about unit consistency.

Diagnostic only. Agreement is evidence of stability, divergence of instability;
neither settles what the value is. Both directions are tested.

---

## 16-18. Clustering, tolerance and robust statistics

Clustering runs in canonical space over **target-quantity observations only** —
a hard-definition violation is a different quantity, and letting an attendance
figure join a capacity cluster is exactly the error §8.1's contrastive axis
exists to prevent. Two tests assert it directly.

The membership rule, representative and dispersion are the frozen core's
`cluster_values`, `statistics.median` and `_relative_mad` — the proposal's
`delta`, `x_hat` and `D_num`. M12 writes no second numeric stack, and a test
asserts it redefines none of them.

Verified properties: order invariance (reversing the input yields identical
clusters), unit invariance (the same quantity in km², hectares and sq mi lands
in one cluster with `total_support == 3`), median representative, relative-MAD
dispersion, zero dispersion for a singleton, and competing clusters for distant
values.

`tau_cluster,r` = the contract's `numeric_cluster_threshold` (0.025) unless
overridden in config. Out-of-range overrides are rejected.

---

## 19. Independence provenance

Every observation carries the family or M11 group that produced it plus its
sample index. `independent_support` counts **distinct groups**, `total_support`
counts members. Three resamples of one family give `total_support == 3` and
`independent_support == 1`; two distinct families give 2 and 2. Both are tested.
`member_indices` traces every member back to its observation.

---

## 20. Why M12 does not implement M16

M12 parses, normalises, classifies, clusters and computes stability. M16 fuses
atomic evidence across mechanisms and decides consensus.

The proposal's `ACCEPT(x) <= I(C*) >= k_r AND D_num < tau_disp,r AND
NOT HARDINVALID(x)` (§8.3) is therefore **not implemented**: it fuses numeric
consensus with verifier evidence that does not exist yet. M12 computes `I(C*)`,
`D_num` and the hard-definition violations and stops. A code scan fails on
`accepted`, `ACCEPT`, `REJECTED`, `candidate_score`, `consensus`,
`fuse_evidence`, and the serialised analytical output is asserted free of
`accepted`, `verdict`, `valid`, `score`. `dominant_cluster` is named for what it
is — the largest cluster, not an accepted value.

---

## 21. Why M12 does not implement M17

§8.4 assigns contrastive numeric verification to M17 explicitly. M12 makes no
verifier call, uses no verifier runtime, and defines no VALID/INVALID/UNKNOWN
semantics. A scan fails on `VerificationLabel`, `score_labels`, `LABEL_TOKENS`,
`VerifierTemplate`, `verifier_runtime`, `build_verifier_prompt`, `A = VALID` and
`adversarial`; `NumericParseStatus`'s values are asserted disjoint from
`{VALID, INVALID, UNKNOWN}`. Module 4's entire prompt surface is pinned by
sha256 `3acd7109…e6d874`.

(`UNKNOWN` does appear in M12's own *system prompt* as an abstention sentinel,
shared with Module 11. That is the model being allowed to decline, not a
verifier label — the distinction is why the scan targets the analytical output
rather than the prompts.)

---

## 22. Why M12 does not implement M19–M21

M12 exposes `dispersion`, `independent_support`, `competing_clusters` and
`estimated_calls` — statistics those modules will want — and decides none of
them. It never chooses to run another probe, stop, or allocate a call: the plan
is fixed before execution and every probe in it runs. A scan fails on
`should_stop`, `next_action`, `allocate_budget`, `schedule_budget`,
`residual_coverage` and `expected_value`.

---

## 23. Why no external factual lookup exists

An AST import scan rejects `requests`, `httpx`, `urllib`, `socket`, `http`,
`aiohttp`, `sqlite3`, `faiss`, `chromadb`, `pinecone`, `torch`, `transformers`.
A code scan rejects `wikipedia`, `wikidata`, `http://`, `https://`, `stadium_db`,
`venue_table`, `geonames`, `api_key`.

There is no venue table, no geographic table, no entity list anywhere. Semantic
cues are phrases the model itself writes next to a number. Unit conversion
constants are mathematical definitions.

---

## 24-25. Runtime integration and call accounting

M12 receives the **frozen enumerator** `LMRuntime` and calls
`runtime.generate(...)` — the same abstraction the production path uses. No new
loader; a clean-subprocess probe loads no `torch`, `transformers` or
`mistral_common`.

* **Every call attributable.** One probe is one call, tagged with its
  `operation_id`, family, `prompt_sha256` and model identity.
* **Counted exactly once, measured not assumed.** Cost is a delta off the
  runtime's own counters. A counter-silent runtime yields `calls == 0`; a
  four-probe area run yields `calls == 4 == runtime.calls`.
* **No double counting.** M11's and M12's totals *sum* to `pipeline.shadow_calls`
  and never exceed the runtime's own count — asserted directly.
* **No double subtraction.** `run_staged` subtracts `shadow_calls` from the
  production progress figure once; M12 adds to the same counter M11 established
  rather than introducing a second subtraction.
* **Outside Module 7's budget.** A pipeline with M12 and one without produce
  identical `graph.budget_snapshot`, while `shadow_calls` differs by exactly
  M12's five probes.
* **Reported honestly.** Phase A prints
  `[M12] numeric specialist: … (N queries, C shadow calls, T generated tokens)`.

---

## 26-27. Shadow isolation and disabled-path invariance

The real staged CLI, run twice with configs differing only in
`specialists.numeric.enabled` (M9/M10/M11 on in both), over both numeric
relations:

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
query_profiles.jsonl     IDENTICAL      <- M9 unchanged
prompt_programs.jsonl    IDENTICAL      <- M10 unchanged
parametric_memory.jsonl  IDENTICAL      <- M11 unchanged
```

`numeric_specialist.jsonl` is present with M12 on and absent with it off, and is
absent entirely for a non-numeric relation even with M12 enabled.

M12 references no `EvidenceGraph`, `build_graph`, `add_candidate` or `Evidence(`;
a graph is unchanged after an analysis, and the serialised graph after a piped
run contains no `canonical_unit`, `semantic_kind`, `dispersion` or `specialist`.

Audit 0018's M11 defaults are asserted intact: three families,
`samples_per_operation == 1`.

---

## 28. Persistence

`numeric_specialist.jsonl`, one record per **NUMERIC query analysed**, written in
Phase A in manifest order. Carries the plan (specialist/compiler/profile/
retrieval versions, query identity, canonical unit, tolerance, probes),
every observation with full provenance, the clusters with their statistics, the
cross-unit checks, errors and cost.

A test asserts manifest ordering and the presence of every provenance key, and
that no `gold`, `ObjectEntities`, `accepted` or `prediction` appears.

`query_profiles.jsonl`, `prompt_programs.jsonl`, `parametric_memory.jsonl`,
`stage_a_enumerated.jsonl`, `stage_b_verified.jsonl`, `diagnostics.json`,
`trace.jsonl` and `predictions.jsonl` are untouched — §26 proves it byte for
byte.

---

## 29. Error handling

Ten distinguished situations, none silent: upstream identity mismatch
(`NumericSpecialistError` listing every problem), unsupported relation/programme,
M12-without-M9/M10/M11 (at config *and* pipeline), malformed config (unknown key,
mode, family, duplicate family, out-of-range tolerance, family not declared for
the relation), runtime failure, empty output, non-numeric output, ambiguous
expression, unsupported unit, semantic near miss, invalid value.

A runtime failure produces an explicit `RUNTIME_ERROR` observation with
`canonical_value is None` — **no fallback zero, no fabricated number**. One
failing probe does not kill the others.

---

## 30. Test results

```
python -m pytest -q
    1382 passed, 3 skipped        (1246 before; +136)
```

`tests/test_numeric_specialist.py`, 136 tests, covering all 34 required areas:
proposal family set and formulae; routing for all six relations; five
parameterised identity mismatches; capacity and area near-miss taxonomies;
clause-scoped classification; eight capacity and eight area parse forms;
ambiguity scoping in both directions; abstention, non-numeric, empty, negative,
fractional, unsupported-unit and assumed-unit cases; per-unit conversion and
equivalence to 1e-9; cross-unit agreement, disagreement and same-unit skip;
order and unit invariance; near-miss separation for both relations; median and
relative-MAD; competing clusters; tolerance source and override; independence
accounting for resamples and distinct families; M11 provenance and
unverifiability; probe provenance; prompt authority; no relation scattering; no
external retrieval; no verifier, acceptance, consensus or control semantics; no
other specialist files; M2/M4 unchanged; graph untouched; exact call accounting
including a counter-silent runtime and a double-counting check; runtime failure
and partial failure; JSON round-trip for every public type; persistence ordering
and provenance; shadow invariance over both numeric relations; ten configuration
failures; zero new parameters; conversion constants derived mathematically.

Three failures surfaced during development. **One was a real defect in the
module** — an unrecognised unit was silently assumed to be the canonical one
(§12) — and two were my own test bugs: an arithmetic premise that four values
would form one cluster at `tau = 0.025` when they do not, and a scan for
`"INVALID"` that matched M12's own `INVALID_VALUE` parse status.

---

## 31. pyflakes

```
python -m pyflakes src/ tests/ scripts/
    clean
```

---

## 32. Model budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    Qwen/Qwen3.5-4B                                [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506  [enumerator] 24.011B (verified)
    total: 28.67B
    RESULT: PASS
```

Unchanged. M12 reuses the frozen enumerator and adds zero parameters.

---

## 33. Benchmark integrity

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no VAL or TEST gold was read.

---

## 34. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M12 value.** The
probe families come from the proposal. `tau_cluster,r` is the contract's
existing declaration, unchanged. Decode is greedy with a 64-token limit sized to
a one-number answer. The semantic cues are phrasings drawn from the contracts'
own hard-negative rules. Unit constants are mathematical. Every numeric string
in the tests is a parser fixture; no real entity's real value appears anywhere.

---

## 35. Challenge compliance

Allowed and used: deterministic numeric parsing, mathematical unit conversion,
frozen-model inference-only probes, clustering, robust statistics, rule-based
semantic classification.

Absent and structurally prevented: web, RAG, external factual corpus, Wikipedia,
Wikidata, KB lookup, factual lookup tables about specific entities, external
venue or geographic databases, fine-tuning, LoRA, continued pretraining, learned
numeric verifier, trained clustering model, learned specialist router.

---

## 36. Non-goals

M13–M21 remain unimplemented, and no placeholder files were created — the
`specialists` package contains exactly four files, asserted by a test:

M13 Large-Open-Set Specialist · M14 Null/Temporal Specialist · M15 Small-Set
Closure Specialist · M16 Atomic Consensus Engine · M17 Specialist Verifier Suite
· M18 Bidirectional/Counterfactual/Dispute Verifier · M19 Coverage Gap &
Missingness Estimator · M20 Relation Budget Scheduler · M21 Expected-Value
Micro-Planner.

---

## 37. Verdict

**PASS.**

M12 is implemented as the proposal specifies: five probe families applied where
the contract permits, canonicalisation into the contract's own unit, a near-miss
taxonomy derived from Module 0, and clustering with the proposal's own `delta`,
`x_hat` and `D_num` — reusing the audited numeric stack rather than writing a
second one. It applies to the two NUMERIC relations and is structurally unable to
reach the other four. It consumes M9, M10 and M11 and rebuilds none of them. Its
calls are real, attributable, counted exactly once, summed without double
counting and kept outside Module 7's budget. It verifies nothing, accepts
nothing, fuses nothing and controls nothing. Enabling it leaves predictions,
candidate graphs and every upstream artefact byte-identical. It is disabled by
default in every shipped config.

Next architecture step: **M13 Large-Open-Set Specialist** — not implemented here.
