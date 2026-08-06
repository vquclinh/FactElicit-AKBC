# Audit 0023 — Module 16: Atomic Consensus Engine Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: first Layer-3 module, the first evidence-fusion stage (M16 of M9–M21).
Mode: **shadow**, **disabled by default**, **zero neural calls**.

---

## 1. Objective and scope

Implement **M16 Atomic Consensus Engine**: take provenance-rich atomic evidence
from core M3/M4/M5 and from the *applicable* Layer-2 specialist, and build
deterministic `CandidateConsensusState` objects carrying §12.1's support vector
`phi(o) = (F, L, X, C, U, I, D, cost, risk)`.

In scope: the canonical evidence-event representation, the canonical origin
identity, five adapters, `q_g = max`, the support vector, null and numeric
consensus, pending-check carry-forward, configuration, the observability
artefact, and the Phase-C seam.

Out of scope and not implemented: M17–M21. No placeholder files.

**M16 decides nothing.** No accepted set, no rejected set, no prediction, no
`should_stop`, no score, no rank. §37–§39 prove it.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§12** header | "Self-Consistency selects an answer mode; ASC merges atomic facts. COVER needs a relation-aware variant: **an atomic candidate graph rather than list-level voting**." |
| **§12.1** Independent consensus | "Each evidence event `e` has an `independence_group`. For group `g`: `q_g(o) = max_{e in g} support(e, o)`, **without summing all repeats**. The candidate support vector is `phi(o) = (F, L, X, C, U, I, D, cost, risk)`." |
| **§12.2** Semantic disagreement | "**No additional embedding model is required by default.** Entity/string relations use normalized candidate sets; numeric relations use clusters. An optional semantic-equivalence judge is enabled **only if** the model budget permits it and alias over-merging remains controlled." |
| **§13** (M17) | The specialist verifier contracts M16 must not pre-empt. |
| **§14** (M18) | Reverse, key-condition, counterfactual and candidate-free recall — M16 requests none and runs none. |
| **§15–§17** (M19–M21) | Residual/missingness, budget, next action — none of which M16 computes. |
| **§8.2, §9.2, §10.3, §11.3** | The specialist states M16 projects: numeric clusters, award facets, `E_null`, closure signals. |
| **§20.1–20.6** | The six end-to-end flows, which fix where consensus sits in each. |
| **Appendix C** | "M16 Consensus \| **evidence events** \| **candidate consensus states** \| Neural: **No**." |

Prior audits read, because M16 must not reintroduce their defects: **0006**
(M3 graph, the alias-collapse fix), **0008** (M5 F/L/X/C/U and its two
double-count removals), **0012** (full M0–M8 freeze), **0016**–**0022**
(M9–M15, including 0021 §15A and 0022 §17A).

### Interpretations recorded rather than resolved silently

**1. `F` keeps its audited definition; specialist evidence does not enter it.**
`F = q(o) = g(o)/m(o)` over `contract.eligible_independence_groups`, computed by
Module 5's own `support_term`. Specialist probe families are not in that
denominator, and the architecture cannot say how many of them *could* have
expressed a given candidate without world knowledge — an open-set award facet
cannot be asked "were you capable of naming this recipient?". Extending the
numerator without the denominator would push `q` above 1 or require an invented
`m(o)`. Specialist structural support is reported through `I` and
`group_supports`, which need no denominator. This is exactly the fallback the
brief's §14 sanctions, and it is tested.

**2. `C` keeps the audited normalisation; specialist contradictions are exposed
structurally.** `C` is Module 5's `contradiction_term` over its own index set.
Specialist contradictions appear as signed `CONTRADICT` events and in
`contradicting_groups` (plane-qualified), rather than being folded into a scale
whose denominator the architecture does not define. Both halves are visible;
neither is fabricated. Same reasoning as **1**.

**3. `D` is binary, plus typed details.** §12.2 names semantic disagreement and
defines no continuous formula. `d_semantic` is `1.0` when any structural
conflict was recorded and `0.0` otherwise, beside
`disagreement_details: tuple[SemanticDisagreement, ...]` carrying kind, detail
and provenance. Normalising a count by an invented denominator would put a
fabricated scale into `phi`.

**4. No status vocabulary.** The brief permits `STRONGLY_SUPPORTED` /
`CONTESTED` / … but forbids thresholds the proposal does not define — and every
such label *is* a threshold. M16 therefore emits the raw structured state, plus
`hard_contract_violation`, which comes from Module 3 rather than from a cut-off.

**5. `I` counts recall groups, including cross-model.** `F` and `X` are score
channels; `I` is the cardinality of structural recall groups, which §12.1 lists
as a *separate* component of `phi`. A verifier shown the candidate is excluded
by role, so anchored agreement can never buy independent support.

**6. Specialist-own probe cost is charged per origin; its token counts are
recorded as unknown.** Each specialist probe execution is one physical call and
is charged once, exactly as one Module 3 generation record is. Specialists
report tokens only in aggregate, so `tokens_recorded=False` and
`cost.origins_missing_tokens` say so rather than writing an unknown down as `0`.

---

## 3. Prior-audit constraints carried forward

| Audit | Invariant | How M16 preserves it |
| --- | --- | --- |
| **0006** | The alias hint is not identity; two surface forms are not merged on a lexical fold | Candidate identity is `contract.strict_key`; `alias_hint` and `alias_groups` appear nowhere in M16 (§17) |
| **0008** | `F` acquisition-only; `X` independent cross-model only; `L` the verifier's own term; INVALID → `L` + signed `C`; repeats are one mechanism | Reproduced as a regression matrix in §35 |
| **0012** | Facets are provenance, never independent support | §15, and a test with five facets of one group |
| **0021 §15A** | `UNKNOWN` / failed recall is **not** substantive null evidence | §32, re-asserted inside M16's own suite |
| **0022 §17A** | M15's closure snapshots are *observed*, never accepted | §33 |

---

## 4. Architecture position

```
    M2 -> M3 -> M4 -> M5            (core evidence, READ-ONLY)
      \
       \  M9 -> M10 -> M11 -> M12 | M13 | M14 | M15   (exactly one applies)
        \                     |
         `-------------------->+
                               v
                          M16 consensus          <- this milestone
                               |
                               v
              [future M17 / M18 -> M19-M21 -> M8]

    M6 -> M7 -> M8                  (unchanged production path)
```

M16 runs at the **Phase-C seam**, immediately before `finalize`, so that
Module 4's verifier evidence is visible as `L` and so that M16 and Module 8 see
the same graph. It cannot change what `finalize` returns.

---

## 5. Files changed

New:

| File | Lines | Contents |
| --- | --- | --- |
| `src/cover_kbc/evidence/consensus_types.py` | 914 | Events, roles, planes, group supports, `phi`, null/numeric/cost/risk states, pending checks. |
| `src/cover_kbc/evidence/consensus_adapters.py` | 550 | Five narrow projections: core graph, M11, M12, M13, M14, M15. |
| `src/cover_kbc/evidence/consensus.py` | 880 | Origin ledger, `q_g`, `I`, `D`, risk, the engine, the builder. |
| `tests/test_atomic_consensus.py` | 1776 | 105 tests. |
| `docs/audits/0023-…md` | this file | — |

Modified:

| File | Change |
| --- | --- |
| `src/cover_kbc/evidence/__init__.py` | Lazy (PEP 562) M16 exports, so importing the graph does not pull in four specialist result types. |
| `src/cover_kbc/pipeline.py` | Optional `consensus_engine`, `consensus_results`, identity-matched `_specialist_result_for` / `_retrieval_result_for` / `_profile_for`, `_run_consensus`, one seam line in `decide_graph`. |
| `scripts/run_staged.py` | Build M16 for Phase C; `load_shadow_results` reloads Phase-A artefacts; `write_atomic_consensus`. |
| `scripts/run_cover.py` | Build M16; write the artefact. |
| 3 × `configs/experiments/*.yaml` | `consensus` block, `enabled: false`. |

No specialist was rewritten. **`benchmark/` untouched** (§48).

---

## 6. Public M16 types

Enums: `EvidencePlane` (3), `EvidenceRole` (9), `DisagreementKind` (7),
`RiskFlag` (7). Records: `ConsensusEvidenceEvent`, `GroupSupport`,
`SemanticDisagreement`, `ConsensusCost`, `CandidateConsensusState`,
`NullConsensusState`, `NumericClusterConsensus`, `PendingDownstreamCheck`,
`QueryConsensusResult`. All frozen; all round-tripped by test for all six
relations.

No field is named `accepted`, `rejected`, `valid`, `invalid`, `final`, `score`,
`rank`, `status` or `tier` — asserted against `__dataclass_fields__`.

---

## 7. Canonical evidence-event schema

One event is one *description* of one physical output's bearing on one
candidate:

```
relation / subject / row_index        candidate_key / display
source_module / source_record_id      origin_event_id      (canonical, §13)
plane / independence_group / role     sign / support
model_id / model_family / mode        facet_id / sample_index / prompt_sha256
calls / generated_tokens / prompt_tokens / latency_ms / tokens_recorded
verified / annotations / hard_violation
```

`sign` reuses M3's `EdgeType` (SUPPORT / CONTRADICT / UNKNOWN) rather than
introducing a parallel vocabulary. `support` is **categorical** — `0` or `1` —
and the constructor rejects anything else, rejects a SUPPORT event carrying `0`,
rejects a non-SUPPORT event carrying `1`, rejects a recall role produced with the
candidate *shown*, and rejects `verified=True` on anything but the blind
verifier. Those five refusals are what make the accounting rules structural
rather than conventional.

`group_key` is **plane-qualified** (`core:DIRECT_RECALL` vs
`specialist:DIRECT_RECALL`): group names collide across subsystems, and merging
them would silently fuse two mechanisms.

---

## 8–12. The adapters

| # | Adapter | What it projects | Notes |
| --- | --- | --- | --- |
| 8 | `core_graph_events` | Every M3 edge and every M4 verdict; plus records that produced no candidate | Read-only. Cost is taken from the `GenerationRecord` once, so a record with five candidate edges is one call. |
| — | `parametric_events` | Every M11 record, as a **query-level** origin | **No candidate is extracted.** Parsing M11's text is the specialist's job; doing it twice is the double count §14 exists to prevent. |
| 9 | `numeric_events` (M12) | Observations keyed by `format_numeric`; hard-definition violations as signed contradictions | A quantity the contract excludes is the model saying the number is not the asked-for quantity. |
| 10 | `large_set_events` (M13) | Award mentions keyed by `strict_key`; near misses as signed contradictions | Nominee/work/similar-award are exactly the contract's exclusions. |
| 11 | `null_temporal_events` (M14) | Locality mentions (candidate-level); Stage-A status readings (**query-level**) | A life-status reading can never become candidate acquisition support. |
| 12 | `small_set_events` (M15) | Border/exchange mentions; listing-gate readings (query-level) | Cross-family observations carry `SPECIALIST_CROSS_FAMILY`. |

**Abstention guard.** `_candidate_key_for` applies Module 3's own `is_abstain`
before keying, so consensus can never mint a candidate the production graph
deliberately declined to create. This is not theoretical: M14's *mining* path
currently marks a `"NONE"` Module 11 record as a **usable target locality**
(`parse_status=OK`, `usable=True`), while M14's own probe path correctly parses
it as `ABSTAINED`. M12, M13 and M15 all handle it correctly. The guard means the
inconsistency cannot become a phantom consensus candidate; **the underlying M14
behaviour is reported, not silently altered inside this milestone** — see §52.

---

## 13. Canonical origin identity

```python
origin_event_id = sha256("origin|v1|" + model_id + "|" + operation_id
                         + "|" + prompt_sha256 + "|" + sample_index)[:16]
```

Deterministic, never random — a test scans for `uuid`, `random`, `id(` and
`time()`. Crucially it is **module-agnostic**: those four fields are exactly what
M12/M13/M14/M15 already copy verbatim when they mine a Module 11 record, so the
record and every observation derived from it land on the same origin **without
any module having to declare the derivation**.

`check_origin_consistency` refuses events that claim one origin but disagree on
`model_id`, `prompt_sha256`, `sample_index` or `model_family`, raising
`ConsensusProvenanceError`. Contradictory provenance is never repaired: if one
physical output is described two ways and the descriptions conflict, the
provenance is wrong, and preferring one would hide the corruption.

---

## 14. Module-11-derived evidence deduplication

The dangerous seam, handled in three places:

1. **Support** — the derived observation shares the M11 origin *and* the M11
   independence group (mined observations keep the `parametric` plane), so
   `q_g = max` collapses them to one group contribution. Tested for all four
   specialists: for a candidate mined from *n* M11 records, the number of
   parametric-plane group supports is exactly *n*, and the origins they carry
   number exactly *n*.
2. **Cost** — the M11 record declares its call; the derived reading declares
   `calls=0`. The ledger takes the max per origin, so the arithmetic cannot
   depend on which description is seen first.
3. **Attribution** — a candidate seen *only* through a derived reading still
   carries the true cost of the output behind it, because cost is looked up in
   the query-wide origin ledger rather than summed over the candidate's own
   events. (This was a defect in the first cut: candidate cost under-reported
   whenever the cost sat on the query-level twin of an origin.)

Turning the M11 registration off changes neither support nor unique origins —
also tested.

---

## 15. Physical event vs independence group vs facet vs sample

| Concept | Where | Counted by |
| --- | --- | --- |
| origin event | `origin_event_id` | `cost.unique_origin_events` |
| independence group | `group_key` (plane-qualified) | `I` |
| facet | `GroupSupport.facets` | **nothing** — provenance only |
| sample | `sample_index` | `total_events`, never `I` |

Tested: ten samples of one group → one contribution and `total_events == 10`;
five facets of one group → one contribution and five recorded facets; three
distinct groups → `I == 3`; one group name in two planes → two groups.

---

## 16. `q_g = max`

`group_supports` buckets events by `group_key` and takes
`max(e.support for e in bucket)`. There is no `sum` over events anywhere in the
support path, and `GroupSupport` raises on any `q_g` outside `{0, 1}`. A group
containing only UNKNOWN/CONTRADICT events has `q_g = 0`.

---

## 17. String identity policy

Identity is Module 3's `contract.strict_key`. `alias_hint`, `alias_groups`,
`same_record_alias_hints` appear nowhere in M16. `"The Alpha Exchange"` and
`"Alpha Exchange"` share an `alias_hint` and are kept as **two** candidates,
exactly as Module 3 keeps them — Audit 0006's decision, re-asserted here.

No fuzzy matching, no edit distance, no embeddings, no cosine similarity, no
external alias data, no LLM equivalence judge. §12.2's optional judge is
**not implemented and not configurable**. A text scan enforces all of it.

---

## 18. Numeric-cluster policy

Clusters are **projected from Module 12, never recomputed**: representative,
dispersion, values, `total_support`, `independent_support` and
`independence_groups` are copied and asserted equal to M12's own. A test scans
M16 for `cluster_values`, `relative_distance`, `median`, `_relative_mad` and
`tolerance=` and fails on any.

A core M3 numeric candidate joins a cluster only when its canonical value **is
one of that cluster's own values** — exact equality in canonical space, using
M12's `format_numeric`. Anything looser would be a second membership rule, and a
second membership rule is a second definition of "the same number". Unmatched
core values are reported in `unassigned_numeric_keys` rather than folded in.

The evaluator's 5 % tolerance is **not** applied as an acceptance rule
(scanned for), and no cluster is selected as a winner.

---

## 19. NULL / query-state policy

`NullConsensusState` carries §10.3's three classes separately and computes
`substantive_groups` as `living ∪ no_known_locality` only. Failed recall is
excluded **by construction**, so no amount of repetition can promote it. There
is no `final_empty`, `accepted_empty`, `gold_empty` or `is_empty` field — scanned
for in the serialised payload. Only `personHasCityOfDeath` produces one.

---

## 20–26. Exact `phi` semantics

| Term | Definition in M16 | Source | Availability flag |
| --- | --- | --- | --- |
| **F** | `support_term(candidate, contract)` — `q(o) = g(o)/m(o)` over core acquisition groups only | Module 5, unchanged | — (structurally 0 for a specialist-only candidate) |
| **L** | `logit_term(candidate)` — clipped calibrated verifier log-odds | Module 4 via Module 5 | `l_available` |
| **X** | `1.0` iff any supporting group has role `CROSS_MODEL_RECALL` or `SPECIALIST_CROSS_FAMILY` | M3 cross-model recall; M14/M15 §10.2 branch | — |
| **C** | `contradiction_term(candidate, contract)`, plus plane-qualified `contradicting_groups` from every plane | Module 5 + specialist near-miss taxonomies | — |
| **U** | `disagreement_term(candidate)` — Module 4's `U_prompt` JSD **only** | Module 4 | `u_available` |
| **I** | distinct recall groups with `q_g = 1` | all planes | — |
| **D** | `1.0` iff any `SemanticDisagreement`, plus typed details | specialist taxonomies | — |
| **cost** | per-unique-origin calls/tokens/latency | origin ledger | `latency_available`, `origins_missing_tokens` |
| **risk** | typed `RiskFlag` tuple + query-level M9 grades | M9, M3, specialists | — |

`H_inc`, `H_ver`, `U_prompt` and `D_semantic` are four separate fields and are
never blended — Audit 0008 §16's requirement, extended by one dimension.

**X may not also be F**: `EvidenceRole.pays_f` is true only for
`CORE_ACQUISITION`. **A shown candidate may not be recall**: the constructor
raises. **A gate is neither**: `EXISTENCE_GATE.is_recall` and `.pays_f` are both
false, and M15's listing gate / M14's Stage-A readings are query-level events
with no candidate key at all.

---

## 27. Cost deduplication

Cost is computed over an **origin ledger**, not over events: each origin
contributes its calls, tokens and latency once, and a candidate's cost is the
ledger summed over *its* origins. Consequences, all tested:

* one record producing three candidate edges → **1** call, 30 generated tokens;
* one M11 output mined by a specialist → **1** call;
* a specialist's own probe → **1** call (nothing else declares it);
* the total for a query = core records + M11 records + specialist probes.

## 28. Risk representation

`RiskFlag` is a tuple of typed descriptors — hard violation, near-miss mention,
single-group support, candidate explosion, pending check, ambiguous parse,
unverified — with **no weighting and no scalar**. The query-level `query_risk`
carries Module 9's grades verbatim (`open_set_risk: HIGH`, …). Nothing is
trained, fitted or combined.

## 29. Availability / unmeasured semantics

Three distinct absences are represented as absences:

* `l_available=False` — never verified, which is not a neutral verdict;
* `u_available=False` — no template distribution measured, which is not
  "templates agreed";
* `latency_available=False` / `origins_missing_tokens>0` — nothing timed or
  counted it, which is not zero cost.

A test contrasts a candidate with a measured `U_prompt` of exactly `0.0`
(`u_available=True`) against one that was never verified (`0.0`,
`u_available=False`).

---

## 30–33. Per-relation consensus

**30. Award (M13).** Atomic union across true independent origins, `I` from
group supports, near-miss mentions as both signed contradiction *and* risk flag,
nothing pruned or ranked. `L` for an unverified candidate is **unavailable**,
not negative. No Tier A–D routing: `VerificationTier`, `assign_tier`,
`shortlist` and `verification_targets` are scanned for and absent, because the
routing needs the specialist verifier that does not yet exist.

**31. Numeric (M12).** Cluster-level consensus with M12's own representative and
dispersion, competing clusters recorded as `NUMERIC_COMPETING_CLUSTERS`
disagreement on both the cluster and its members, cross-unit divergence
recorded, unassigned core values reported. No winner selected, no tolerance
applied.

**32. Null/temporal (M14).** Candidate-level locality consensus **and**
query-level null consensus, kept apart: a strongly supported city does not erase
recorded null evidence, and both are visible in one row. All-`UNKNOWN` Stage B
gives `substantive_null_groups == 0` and `failed_recall_only == True`. Competing
cities remain visible and each carries a `COMPETING_SINGLE_VALUE` disagreement.
No top-1, no final empty.

**33. Small set (M15).** Observations fused by strict identity and provenance;
pending checks carried forward with their source module and never executed;
closure snapshots and listing-gate state preserved as **observed**. The payload
contains no `accepted_set`, `final_set`, `A_t`, `closure_accepted`,
`should_stop` or `CLOSED`.

---

## 34. Hard contract violations

Taken from Module 3: `hard_contract_violation` and `rejection_reason` are copied
from the candidate, and `RiskFlag.HARD_CONTRACT_VIOLATION` is raised. M16
re-derives no rule of its own — `apply_hard_contract_rules` is not called and
not referenced — and adds no world knowledge.

---

## 35. Audit-0008 accounting regression matrix

Reproduced as executable tests, each asserting the *other* channels did not
move:

| Evidence | F | L | X | C | I |
| --- | --- | --- | --- | --- | --- |
| ordinary acquisition SUPPORT | **↑** | 0, unavailable | 0 | 0 | +1 |
| independent `CROSS_MODEL_RECALL` | unchanged | 0 | **1.0** | 0 | +1 |
| blind verifier **VALID** (shown) | unchanged | **↑** | 0 | 0 | unchanged |
| blind verifier **INVALID** (shown) | unchanged | **↓ <0** | 0 | **>0** + signed group | unchanged |
| same direct view ×3 | unchanged | — | — | — | unchanged (`total_support_events == 3`) |
| existence gate | 0 | — | — | — | not a group |
| specialist acquisition | **unchanged** (§2.1) | — | — | — | +1 |
| specialist cross-family | unchanged | — | **1.0** | — | +1 |
| shown-candidate agreement as X | **impossible** — constructor raises | | | | |

---

## 36. Why no embedding or equivalence model exists

§12.2: "No additional embedding model is required by default… An optional
semantic-equivalence judge is enabled **only if** the model budget permits it and
alias over-merging remains controlled." Neither precondition is met: the model
profile is frozen at 28.67 B with two models and no third, and no mechanism to
control over-merging exists. M16 v1 therefore uses strict normalised string
identity, M12's numeric clusters, typed specialist annotations, explicit conflict
flags and provenance — nothing else. Where two strings *might* be aliases and the
strict rules cannot prove it, they stay separate: false non-merging is
recoverable, unsupported over-merging is not (Audit 0006).

## 37. Why M16 does not implement M17

No verifier is called or constructed: `VerifierTemplate`, `build_verifier_prompt`,
`score_labels`, `verifier_runtime`, `LABEL_TOKENS`, `ContextualCalibrator` and
`calibrate(` are all absent. M4's prompt surface is sha256-pinned at
`3acd7109…e6d874` and unchanged. A specialist observation cannot claim
verification: the event constructor raises.

## 38. Why M16 does not implement M18

Pending checks are carried forward as `PendingDownstreamCheck` descriptors with
their source module and reason, and executed by nothing —
`reverse_prompt`, `counterfactual`, `execute_check` and `run_check` are scanned
for and absent. M16 spends zero calls, so it could not execute one.

## 39. Why M16 does not implement M19–M21

No `residual`, `missingness_estimate`, `saturation`, `allocate_budget`,
`schedule_budget`, `next_action`, `expected_value`, `should_stop` or `STOP`. M16
reports what evidence exists; deciding what to do about it is Layer 5's.

---

## 40. Zero-neural-call proof

* No model module is imported — an AST test bans `torch`, `transformers`,
  `requests`, `httpx`, `urllib`, `socket`, `faiss`, `chromadb`,
  `sentence_transformers`, `numpy`.
* No runtime object is accepted by any public entry point; `LMRuntime`,
  `generate(`, `GenerationRequest` and `score_labels` are absent from the code.
* A runtime's counter is `0` before and after a consensus build.
* A subprocess constructs the engine and asserts `torch`, `transformers` and
  `mistral_common` were never imported.
* The staged CLI reports `0 neural calls` on the M16 line.

## 41. Read-only production-graph proof

`graph.to_json()` is deep-compared before and after; candidate statuses, scores
and the edge-id set are compared; and the code is scanned for `add_evidence`,
`add_entity_mentions`, `add_verification`, `graph.reject`, `close_gate` and
`register_record`. All four specialist results are also deep-compared before and
after, for all six relations.

## 42. Shadow isolation

`mode` accepts only `shadow`. M16 runs in `decide_graph` immediately **before**
`finalize`, receives the graph and returns a separate object, and appends to
`pipeline.consensus_results`. Nothing downstream reads it.

Staged CLI, M16 the only variable, four relations × 15 artefacts:

```
predictions.jsonl  diagnostics.json  trace.jsonl  stage_a_enumerated.jsonl
stage_b_verified.jsonl  calls_enumerate.jsonl  calls_verify.jsonl
query_profiles.jsonl  prompt_programs.jsonl  parametric_memory.jsonl
numeric_specialist.jsonl  large_open_set_specialist.jsonl
null_temporal_specialist.jsonl  small_set_specialist.jsonl  metrics.json
```

**All byte-identical**, including both production call ledgers. Only
`atomic_consensus.jsonl` appears, and only when M16 is on.

## 43. Persistence schema

`atomic_consensus.jsonl`, one row per query, in `query_manifest.json` order.
Keys: `consensus_version`, `Relation`, `SubjectEntity`, `row_index`,
`applicable_specialist`, `upstream_versions` (M9/M10/M11/specialist),
`candidates`, `null_state`, `numeric_clusters`, `unassigned_numeric_keys`,
`pending_checks`, `query_events`, `cost`, `query_risk`, `errors`.

No gold, no `ObjectEntities`, no `prediction`, `final_set`, `accepted_set` or
`should_stop` — asserted per row. No prior artefact's schema was changed.

## 44. Staged round trip

Phase C builds no specialist and loads no model, so `load_shadow_results`
reloads Phase A's artefacts — profiles, parametric memory and the four
specialist results — and M16 fuses those. Two tests cover it: every public type
round-trips `to_json`/`from_json`, and consensus over a specialist result
reloaded from JSON is **equal** to consensus over the in-memory result.

(`ParametricRetrievalPlan.specialist_hint` is not persisted by the Module 11
artefact and is reconstructed as `""` rather than guessed; M16 never reads it.)

---

## 45. Test results

```
python -m pytest -q
1888 passed, 3 skipped in 14.77s
```

M16's suite: **105 tests** (`tests/test_atomic_consensus.py`), covering the
brief's 55 numbered requirements. No prior test needed rescoping — M16 adds a
seam rather than changing one.

Three defects were found by these tests and fixed before this audit:

1. **Candidate cost under-reported** when an origin's cost was declared on its
   query-level twin (fixed with the query-wide origin ledger, §27).
2. **Specialist-own probe origins vanished** — a barren observation produced no
   event at all, so both the origin and its call disappeared from the totals
   (fixed: every observation yields an event, query-level when it named no
   candidate).
3. **Specialist token counts were being written as zero** rather than as
   unknown (fixed with `tokens_recorded` and `origins_missing_tokens`, §29).

## 46. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

## 47. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  total: 28.67B    RESULT: PASS
```

M16 introduces no model, no checkpoint and no parameter.

## 48. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. All runs used
`ScriptedRuntime` fixtures and wrote only into a scratch directory.

## 49. No TRAIN / VAL / TEST tuning

M16 has no weight, no threshold and no coefficient. `ConsensusConfig` carries
`enabled`, `mode`, `consensus_version` and two artefact-shape switches — a test
asserts no field name contains `alpha`, `beta`, `gamma`, `delta`, `eta`,
`weight`, `threshold` or `tolerance`. Nothing was fitted, and no split was read.

## 50. Challenge compliance

* **Closed book.** M16 reads recorded evidence only; no web, search, Wikipedia,
  Wikidata, KB, vector store, entity linker or external API, enforced by AST and
  text scans.
* **No training.** Non-neural, unweighted, deterministic. No learned router,
  classifier, calibrator or scorer.
* **Frozen model profile.** Unchanged; M16 adds no inference-time component.
* **Reproducible.** Pure projection: same graph, specialist result and config →
  equal result, and reordering the evidence changes nothing.

## 51. Non-goals — M17–M21 absent

| Module | Absent because |
| --- | --- |
| M17 Specialist Verifier | §37 — no verifier call; M4 surface sha256-pinned |
| M18 Bidirectional/Counterfactual | §38 — checks carried, never executed |
| M19 Coverage/Missingness | §39 — no residual, no saturation |
| M20/M21 Budget / Micro-planner | §39 — no budget, no next action |

No placeholder files, no stub classes.

---

## 52. Finding reported, not silently fixed — M14's mining path

M14's Module 11 **mining** path marks a record whose text is `"NONE"` as a
usable **target locality** (`normalized_surface="NONE"`, `parse_status=OK`,
`usable=True`), while M14's **own probe** path parses the identical text as
`ABSTAINED` with an empty surface. M12, M13 and M15 all treat it as an
abstention on both paths.

This is an inconsistency inside M14, and it points the wrong way relative to
Audit 0021 §15A: an abstention becoming a *candidate* is the opposite of the
invariant that correction established. Its blast radius today is limited to
`null_temporal_specialist.jsonl`.

M16 is correct regardless — the §8 abstention guard means such a record can
never become a consensus candidate, and a test asserts it. The M14 behaviour
itself is **left unchanged in this milestone**: fixing it would alter an audited
Layer-2 artefact under an M16 brief that explicitly forbids redesigning
M12–M15. It is recorded here for a follow-up decision.

---

## 53. Verdict

**PASS.**

M16 implements §12.1 literally — `q_g` as a max over plane-qualified
independence groups, `phi(o) = (F, L, X, C, U, I, D, cost, risk)` with Audit
0008's channels intact across four new evidence producers — and §12.2 without an
embedding model. One physical model output is counted once and charged once
however many modules describe it, which is what the canonical origin identity
buys. The production graph is read and never written; every production artefact
is byte-identical with M16 on or off; and the module spends zero neural calls.

Nothing here is a decision. No accepted set, no final set, no stopping rule.

Not committed. Not pushed.
