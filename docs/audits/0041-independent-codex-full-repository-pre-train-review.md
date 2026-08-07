# 0041 — Independent Codex Full Repository Pre-TRAIN Review

**Verdict: BLOCKED — REPOSITORY REQUIRES FIXES BEFORE TRAIN**

---

## Statement of independence

This audit was produced by an independent reviewer (Codex) that did **not**
implement any part of this repository. Prior audits 0001–0040, `README.md`,
`docs/IMPLEMENTATION_STATUS.md`, docstrings, comments, test names and every
"complete"/"ready" claim in the tree were treated as **non-authoritative** and
were not used as evidence for any finding below. Every statement here was
re-derived from the current executable source, from the current configuration
files, from executing the test suite, and from read-only probes run entirely
inside a scratchpad directory outside the repository.

**No production code was modified.** The only repository change is the creation
of this file. `git status` is otherwise clean; `benchmark/` is byte-identical.

The authoritative contract for this review is
`COVER_KBC_Technical_Proposal_New.pdf` (26 pages, read in full).

---

## 1. Review identity

| item | value |
|---|---|
| HEAD | `2512fe000c49c57aaf2ed4f
d6b7d1f921f2ea2ba` |
| Branch | `main` |
| Working tree at start | clean (`git status --short` empty) |
| Proposal | `COVER_KBC_Technical_Proposal_New.pdf`, "COVER-KBC Super-System Technical Proposal", 26 pp. |
| Review date | 2026-08-07 |
| Scope | source + config + test + executable-reachability audit |
| Excluded by instruction | full TRAIN run, VALIDATION, TEST, real weight loading, any training, reading factual gold values |

Nothing in this review loaded a model checkpoint, ran the 477-row collection, or
read `ObjectEntities` values from any split. Row counts, file hashes and schema
shape were verified; factual content was not inspected.

---

## 2. Repository inventory

232 tracked files. Distribution:

| area | files |
|---|---|
| `src/cover_kbc/` | 102 |
| `tests/` | 52 |
| `docs/` | 41 |
| `benchmark/` | 13 |
| `scripts/` | 9 |
| `configs/` | 8 |
| `notebooks/` | 2 |
| root (`README.md`, `pyproject.toml`, `.gitignore`, 2 PDFs) | 5 |

Total first-party Python + config surface: **171 files, ~87k lines**. All 171
were enumerated. Roughly 45 were read in full or in substantial part
(every file on the collection execution path, every control/calibration module,
every config, the two collection tests, both notebooks); the remainder were
covered by targeted structural greps (imports, constructors, call sites, mode
comparisons, write sites) and by executing the suite.

Uncommitted files: none. Ignored-but-present: `outputs/`, `__pycache__/`,
`.pytest_cache/` — none tracked.

Benchmark: `train.jsonl` 477 rows (`sha256 cb344aa3f153b30f…`), `val.jsonl` 478,
`test.jsonl` 477. Single commit `b607ae1`, upstream pin
`30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57`. `git diff -- benchmark/` empty.

---

## 3. Proposal requirement summary

Requirements extracted independently from the proposal and used as the contract
for §4–§39:

- **§2.1/§2.2/§2.3** — closed book (no web, RAG, corpus, KB); ≤32B published
  inference parameters counted from published totals, quantization does not
  reduce; no training of any kind.
- **§4** — eight layers, M0–M21; M0–M8 core preserved.
- **§7.2 / Evidence hygiene** — pseudo-context is *never* truth evidence; only
  parsed candidates may reach the graph; CoT never crosses to the verifier.
- **§8** — numeric canonicalisation, relative-δ clustering, `UNKNOWN ≠
  contradiction`, ACCEPT under strong low-dispersion cluster.
- **§9.2/§9.3** — award atomic support; `B_r = B_seed + B_facet + B_verify +
  B_reverse + B_reserve` with `B_verify` a *hard* reservation.
- **§10.3** — `E_null` separates living support / no-known-locality /
  failed-recall-only; failed recall gets very little weight.
- **§11** — borders minimal-change; stock listing gate, primary/secondary,
  parent/subsidiary; closure test.
- **§12.1** — `q_g(o) = max_{e∈g} support(e,o)`, **never** a sum of repeats;
  `φ(o) = (F, L, X, C, U, I, D, cost, risk)`.
- **§13/§13.1** — relation-specific verifier contracts, fixed A/B/C labels,
  contextual calibration, template/label-order bias measured.
- **§14** — four mechanisms: REVERSE, KEY_CONDITION, COUNTERFACTUAL,
  CANDIDATE_FREE_RECALL; candidate-free recall increases `X`.
- **§15** — `R_t = w1·noveltyRate + w2·singletonRatio + w3·facetGap +
  w4·disagreement + w5·unresolvedMass`.
- **§16** — budget by relation, reserve by action class, cache-aware,
  **precharge before every neural call**, no action exceeds the hard cap;
  concrete values **calibrated on TRAIN**.
- **§17** — `U_t(a) = α·Ĝ + β·ΔR̂ + γ·ΔĤ − δ·Ĉost − η·R̂ed − κ·F̂P`;
  `a* = argmax U_t` if `U_t(a*) > τ_continue`, else STOP; estimates from
  **relation-specific historical bins on TRAIN**; 1–2 step lookahead; no learned
  policy.
- **§18** — exact model ID + revision + published parameter total per profile.
- **§21.2** — specialists never bypass the graph; all neural calls go through
  runtime accounting; benchmark immutable; official JSONL contract.
- **§22** — Algorithm 1: compile → route → mandatory views → loop{consensus →
  unresolved/coverage → stop? → legal actions → argmax utility → execute} →
  finalize.

---

## 4. M0–M21 matrix

"COLLECTION-REACHABLE" is judged against the **only way the collection can
actually be launched today**. Two columns are given because they differ: *(a)*
with the tracked configs as committed, *(b)* with the ad-hoc config that
`tests/test_collection_failure_resume.py` synthesises at runtime (split forced
to `train`, M11–M18 + Layer-4 + M19 forced on). There is no committed config
equivalent to (b) — see F-01.

| M | Source | Impl | Imported | Constructed | Called | Prod-reachable | Coll-reachable (a) | Coll-reachable (b) | Output effect | Tested | Real-weight smoked | Calib. available |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M0 | `contracts/base.py`, `contracts/registry.py` | Y | Y | Y | Y | Y | Y | Y | contract | Y | Y | n/a |
| M1 | `contracts/router.py`, `contracts/programs.py` | Y | Y | Y | Y | Y | Y | Y | routing | Y | Y | n/a |
| M2 | `elicitation/{engine,library,views,parsing}.py` | Y | Y | Y | Y | Y | Y | Y | candidates | Y | Y | n/a |
| M3 | `evidence/graph.py` | Y | Y | Y | Y | Y | Y | Y | graph | Y | Y | n/a |
| M4 | `verification/blind.py` | Y | Y | Y | Y | Y | Y | Y | L, U | Y | Y | n/a |
| M5 | `scoring.py` | Y | Y | Y | Y | Y | Y | Y | score/tier/H | Y | Y | n/a |
| M6 | `coverage.py` | Y | Y | Y | Y | Y | Y | Y | q_res | Y | Y | n/a |
| M7 | `controller.py` + `pipeline._adaptive_discovery/_controlled_phase` | Y | Y | Y | Y | Y | Y | Y | actions | Y | Y | n/a |
| M8 | `selection.py` | Y | Y | Y | Y | Y | Y | Y | **ObjectEntities** | Y | Y | n/a |
| M9 | `query_intelligence/profiler.py`,`priors.py` | Y | Y | Y | Y | shadow only | Y | Y | observability | Y | Y | n/a |
| M10 | `query_intelligence/prompt_compiler.py`,`prompt_registry.py` | Y | Y | Y | Y | shadow only | Y | Y | program blueprint | Y | Y | n/a |
| M11 | `query_intelligence/parametric_retrieval.py` | Y | Y | only if enabled | Y | shadow only | **N (disabled)** | Y | candidates via M16 | Y | Y | n/a |
| M12 | `specialists/numeric_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | via M16→bridge | Y | partial | n/a |
| M13 | `specialists/large_set_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | via M16→bridge | Y | partial | n/a |
| M14 | `specialists/null_temporal_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | via M16→bridge | Y | partial | n/a |
| M15 | `specialists/small_set_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | via M16→bridge | Y | partial | n/a |
| M16 | `evidence/consensus*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | φ(o) | Y | partial | n/a |
| M17 | `verification/specialist_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | verdict via bridge | Y | Y | n/a |
| M18 | `verification/bidirectional_*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | structural edges | Y | Y | n/a |
| L4 | `evidence/layer4.py`, `evidence/production_bridge.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | **sole mutation seam** | Y | partial | n/a |
| M19 | `coverage_gap/*.py` | Y | Y | only if enabled | Y | shadow only | **N** | Y | R_t (**never recorded** — F-02) | Y | partial | n/a |
| M20 | `control/relation_budget.py`,`budget_types.py`,`budget_accounting.py` | Y | Y | **N** | N | **N** | **N** | **N** (bypassed, `pipeline.py:1933`) | none | Y (unit) | N | **absent** |
| M21 | `control/micro_planner.py`,`planner_types.py`,`historical_bins.py` | Y | Y | **N** | N | **N** | **N** | **N** (correctly excluded) | none | Y (unit) | N | **absent** |
| L6 | `control/layer6_integration.py`,`control/action_catalog.py` | Y | partial | **N — no caller ever passes `layer6_integrator`** | N | N | N | N | none | Y (unit) | N | n/a |

Key structural conclusions:

- **No module with source code is missing.** All 22 modules plus both
  integration seams are implemented, and the implementations are of high
  quality (see §9–§21 below).
- **M20, M21 and Layer-6 integration are not constructed by any entrypoint.**
  This is *correct* for M20/M21 (they fail closed without TRAIN calibration —
  `relation_budget.py:462-478`, `micro_planner.py:531-552`), but Layer-6
  integration is unreachable for no stated reason: `layer6_integrator` is a
  `CoverPipeline` keyword argument that **no script ever supplies**.
- **`IntegrationMode.PRODUCTION` is referenced only in tests.** No production
  runner (`run_cover.py:149`, `run_staged.py:850`, `real_model_smoke.py:424`,
  `architecture_smoke.py:123`) passes `integration_mode`, so every one of them
  defaults to `SHADOW` and every one leaves `action_selector=None`. A calibrated
  production/validation path does not currently exist as an executable
  entrypoint.

---

## 5. Actual production execution graph

Reconstructed from call sites, not from diagrams.

```
run_cover.py:149 / run_staged.py:850
  └─ CoverPipeline(...)                       integration_mode defaults to SHADOW
     ├─ enumerate_query()                     pipeline.py:1049
     │   ├─ compile_query()                   contracts/router.py:48        M0→M1
     │   ├─ profiler.profile()                pipeline.py:1056              M9
     │   │   └─ _schedule_relation_budget()   pipeline.py:1613              M20 (record only)
     │   ├─ prompt_compiler.compile()         pipeline.py:1061              M10
     │   ├─ _run_shadow_retrieval()           pipeline.py:957               M11
     │   ├─ _run_{numeric,large_set,null_temporal,small_set}_specialist()
     │   │                                    pipeline.py:973-1047          M12–M15
     │   ├─ build_graph()                     evidence/graph.py             M3
     │   ├─ _run_gate()                       pipeline.py:629
     │   └─ _adaptive_discovery()             pipeline.py:1153              M7
     │       └─ choose_action → _execute_action → M2 views / M4 verify / cross-model
     ├─ verify_graph()                        pipeline.py:1237              M4 + M7 phase B
     └─ decide_graph()                        pipeline.py:2419
         ├─ _run_consensus()                  pipeline.py:1646              M16
         │   ├─ _catalogue_specialist_targets()   M17 catalogue (no calls)
         │   ├─ _catalogue_bidirectional_checks() M18 catalogue (no calls)
         │   ├─ _integrate_layer4()               Layer-4 projection
         │   ├─ production_bridge.apply()         SHADOW ⇒ **no-op**
         │   ├─ _estimate_coverage_gap()          M19
         │   ├─ _execute_selected_verifications() ⇒ **returns [] in SHADOW** (1969)
         │   └─ _plan_micro_action()              skipped (micro_planner None)
         └─ finalize()                        selection.py:459              M8 → ObjectEntities
```

**Dead ends and shadow-only paths in the production graph as wired today:**

- `production_bridge.apply()` is called on the real path but returns immediately
  in SHADOW (`production_bridge.py:143`). M11–M18 evidence therefore *cannot*
  reach M8 in any current production run. This is the audited invariant, not a
  bug — but it means "M11–M21 affects predictions" is false for every existing
  runner.
- `_select_actions` returns `()` in SHADOW (`pipeline.py:1781`), so M17/M18
  never execute in production.
- `_plan_micro_action` (`pipeline.py:2128`) is reachable only when
  `micro_planner is not None`, which no runner produces.
- `Layer6Integrator` / `collect_catalog` are wired inside `_plan_micro_action`
  only; with `layer6_integrator=None` the planner is handed an **empty legal
  action list** and returns `STOP/NO_LEGAL_ACTION`. Unreachable in practice.

---

## 6. Actual TRAIN collection execution graph

```
scripts/run_train_calibration_collection.py:main()
  ├─ require_split(TRAIN_CALIBRATION_COLLECTION_ONLY, cfg.experiment.split)   :249
  │     ⇒ raises for every committed config (all declare split: val)           F-01
  ├─ load_dataset("train")                     :251     477-row guard :253
  ├─ TrainCollectionPolicy()                   :262
  ├─ build_pipeline(config, selector)          :162
  │     integration_mode = TRAIN_CALIBRATION_COLLECTION_ONLY   :226
  │     action_selector  = policy.select                       :264
  │     pipeline.mode forced to INTERLEAVED                    :180
  └─ per row:
      ├─ pipeline.enumerate_query(query)       :338    (M0,M1,M9,M10,M11,M12-15,M3,M4,M7)
      ├─ pipeline.decide_graph(graph)          :343
      │   └─ _run_consensus                    pipeline.py:1646
      │       ├─ M16 → M17/M18 catalogues → Layer-4 → bridge.apply (**mutates**) → M19
      │       ├─ _execute_selected_verifications          pipeline.py:1952
      │       │   ├─ _select_actions → policy.select      (collection branch, 1790)
      │       │   └─ execute_action                        pipeline.py:1802
      │       │       ├─ _precharge → **returns True immediately** (1933) — M20 bypassed
      │       │       ├─ verify_specialist_targets / execute_bidirectional_checks
      │       │       ├─ _integrate_layer4 → bridge.apply → _estimate_coverage_gap
      │       │       └─ records pre/post physical snapshot + entropy_before/after
      │       └─ Layer-4 re-integrate → bridge → M19 refresh
      ├─ build ActionTelemetryRecord per action  :355-379
      ├─ writer.write / policy.record_outcome / predictions.write  :389-399
      └─ persist() → checkpoint + accounting.json + action_coverage.json  :320
```

Divergences from the production graph that matter:

1. **`verify_graph()` is never called** (compare `pipeline.run_query`, :2488).
   The collection runs `enumerate_query` → `decide_graph` only. Under the active
   controller M4 still runs inside Phase A, so this is not a total loss of
   verification — but the second controller phase (`_controlled_phase`, which a
   real validation run *does* execute) never happens, so the collection observes
   a systematically shorter core loop than production will.
2. **At most one M17 action and one M18 action per query.** The `while True`
   loop in `_execute_selected_verifications` ends with an unconditional `break`
   at `pipeline.py:2007`. `remaining`/`executed_ids`/`pending` are therefore
   dead machinery, and multi-round dynamics — precisely what §17's successor
   statistics are built from — are never observed.
3. **M20 is bypassed entirely** (`pipeline.py:1933`). Correct in that collection
   must not pretend to hold TRAIN-calibrated envelopes; but it also means no
   `reserved_class`, no refusal record and no per-class spend is ever observed.

---

## 7. M8 ownership review — **PASS**

Repository-wide search for anything that can create or modify a final
prediction payload:

- `selection.finalize()` — `selection.py:459` — the only site that builds
  `object_entities` from evidence.
- `pipeline.run()` error path — `pipeline.py:2585` — constructs
  `object_entities=[]` with `EmptyReason.PIPELINE_ERROR`. Not used by the
  collection runner.
- `types.py:729,736` — serialisation of an already-built `Prediction`.
- `data/writer.py:62` — `dedupe_object_entities` on an already-final row.
- `scripts/package_submission.py` — validation only, raises on malformation.

No specialist, verifier, bridge, controller, planner or runner writes
`ObjectEntities`. `ProductionEvidenceBridge` writes verification results and
signed evidence edges only (`production_bridge.py:218`, `:265`) and never
touches selection. **M8 remains the sole final-output owner.**

---

## 8. Mode review (IntegrationMode) — PASS with one terminology hazard

`src/cover_kbc/integration_mode.py` is a single, well-formed contract. The
`str`-enum is normalised exactly once at the pipeline boundary
(`pipeline.py:510`) and `parse_mode` fails closed on an unknown string
(`integration_mode.py:122-128`). All consumers use the typed properties, never
string comparison:

| site | check | effect |
|---|---|---|
| `pipeline.py:1781` | `may_mutate_production_state` | SHADOW selects no action |
| `pipeline.py:1783` | `is_production` | only PRODUCTION routes to M21 |
| `pipeline.py:1933` | `is_collection` | M20 precharge bypassed |
| `pipeline.py:1969` | `may_mutate_production_state` | SHADOW executes nothing |
| `pipeline.py:2094` | `charges_production_budget` | shadow vs production counters |
| `production_bridge.py:143` | `may_mutate_production_state` | SHADOW mutates nothing |

- **SHADOW** cannot mutate evidence/output (bridge returns before any write) and
  cannot contaminate accounting (`_charge_calls` splits `shadow_calls` from
  `production_calls`). ✔
- **PRODUCTION** would route selection through M21 and reach M20/M21 — but is
  unreachable from any entrypoint (see §4). ✔ semantically, ✘ operationally.
- **TRAIN_CALIBRATION_COLLECTION_ONLY** uses the identical post-selection path
  (`execute_action` is the single seam for both callers), is TRAIN-locked by
  `require_split` (`integration_mode.py:135-148`), and never reaches
  `_plan_next_action` (guarded by `is_production`). ✔

No forgotten shadow guard, no enum/string comparison bug, no permissive default
was found.

**Hazard (F-11):** every upgraded module *also* carries its own config key named
`mode:`, hard-pinned to `"shadow"` (`consensus.py:122`, `layer4.py:79`,
`missingness.py:101`, `parametric_retrieval.py:223`, `micro_planner.py:472`,
`relation_budget.py:341`, …). During collection every one of those reads
`mode: shadow` in YAML while the pipeline genuinely mutates production state.
Two different things are called "mode". Worse, no config can express
`mode: production` for any module, so a future calibrated validation run cannot
be configured without a code change.

---

## 9. M11 review — PASS

`query_intelligence/parametric_retrieval.py` implements §7.2's three probe
families (`pseudo_memory`, `self_ask`, `query_rewrite`), each with its own
declared independence group and decode profile.

**The critical invariant holds.** Pseudo-memory text never becomes truth
evidence:

- `_run_shadow_retrieval` (`pipeline.py:957`) appends a typed
  `ParametricRetrievalResult` and charges its calls; it never touches
  `graph.candidates` and never creates an `Evidence` edge.
- The only route from M11 to the graph is
  M11 → M16 (`consensus.consense(..., retrieval=…)`) → Layer-4 →
  `ProductionEvidenceBridge`, and the bridge inserts **nothing** for a candidate
  the graph does not already hold (`production_bridge.py:163-166`, Rule 2).
- `consensus.include_parametric_origins` registers M11 records as *query-level
  origins* so a specialist observation mined from one is recognisable as a
  description of an already-paid output rather than second independent support
  (`consensus.py:290` config comment; enforced through `origin_ledger`).
- Grep found no path injecting retrieval prose into a verifier prompt. The
  verifier prompt builders take contract + candidate string only
  (`verification/specialist_prompts.py`, `bidirectional_prompts.py`).

Model identity, provenance and independence groups are recorded per record.
Proposal §7.2's "the sketch does not itself create a support edge" is satisfied
structurally, not by convention.

---

## 10. M12 review — PASS

`normalization/numeric.py` + `specialists/numeric_*.py`.

- Deterministic canonicalisation to km² via an explicit factor table
  (`AREA_UNITS_TO_KM2`, `_UNIT_ALIASES`), with `UNSUPPORTED_UNIT` as an explicit
  invalid state rather than a silent pass-through.
- `cluster_values(values, threshold=0.05)` implements §8.2's relative δ; the
  5 % default matches the evaluator tolerance. Clustering is on floats with a
  *relative* predicate — no float equality is used for identity anywhere on this
  path.
- `NumericSemanticKind` separates TARGET from non-target quantities
  (`is_target` / `is_not_target`, `numeric_types.py:78-83`), so attendance is
  never clustered against capacity and land area never against total area.
  §8's "hard-definition violations" are represented.
- `UNKNOWN` is modelled as insufficient confidence, not contradiction: only
  `StructuralOutcome.CONTRADICT` contradicts (`layer4_types.py:112-114`) and
  only `VerificationLabel.INVALID` maps to a negative edge
  (`production_bridge.py:71-75`). §8.3 is honoured.
- M19's numeric novelty reads `state.numeric_targets`
  (`missingness.py:349,440,679`), which are cluster-level, so novelty identity is
  cluster identity and not raw-value identity, as §15 requires.
- `coverage.numeric_stability` explicitly reuses the *same* `cluster_values`
  primitive M8 uses, so diagnostics and finalisation cannot disagree about what
  a cluster is (`coverage.py:531-544`).

No duplicate candidate identity and no incompatible-quantity comparison was
found.

---

## 11. M13 review — PASS

`specialists/large_set_*.py`.

- Seed + generic non-factual facets per §9.1; facets carry provenance and are
  projected into the Layer-6 catalogue as `SPECIALIST_PROBE` actions with a
  declared `SpecialReservePurpose` (`action_catalog.py:485-520`).
- Shortlist/near-miss machinery is diagnostic; M13 **cannot** accept a
  candidate. It emits observations only, and the sole route to the graph is the
  bridge, which requires the candidate to already exist and requires a RESOLVED
  check. Verification targeting is `verifiable_targets(consensus)` — M17's own
  surface — so M13 cannot self-verify.
- `Bverify` reservation is modelled as a *protected floor* in
  `BudgetSpendClass.VERIFICATION` with `SpecialReservePurpose.MISSINGNESS` /
  `REVERSE` modifiers (`budget_types.py`), matching §9.3's "hard reservation
  that discovery cannot spend". The mechanism is correct; the **numbers do not
  exist** because M20 is uncalibrated.

M13 cannot bypass verification or final-selection ownership.

---

## 12. M14 review — PASS

`specialists/null_temporal_*.py` is the strictest module in the tree and it is
strict in the right direction.

- `DeathStatus` = {LIVING, DECEASED, UNKNOWN}; `is_definite` explicitly excludes
  UNKNOWN (`null_temporal_types.py:209-212`). **UNKNOWN ≠ NONE.**
- `NullEvidenceKind` separates `LIVING_SUPPORT`, no-known-locality and
  `FAILED_RECALL_ONLY`; `is_substantive` returns False for
  `FAILED_RECALL_ONLY` (`:119`) and `failed_recall_only` is a derived property
  that can never be promoted (`:432-434`). §10.3 is implemented as a type, not
  as a weight.
- Stage A gate (`GateState.DECEASED_PLAUSIBLE`) precedes Stage B locality
  acquisition; no city is inferred before the gate.
- Freshness / cross-family recall is guarded by `distinct_families(enumerator,
  verifier)` comparing *configured* model ids (`pipeline.py:1017-1023`), so a
  single-runtime phase correctly reports no second family.
- Repository-wide search for collapse of "empty list / parse failure / unknown /
  no recall" into NONE found none. `EmptyReason` keeps
  `CONFIDENT_NEGATIVE_GATE`, `NO_CANDIDATE_GENERATED`, `CANDIDATE_REJECTED` and
  `UNRESOLVED_ABSTENTION` distinct (`types.py:171-183`), and
  `selection._empty_reason` (`:110-138`) assigns them from graph state rather
  than defaulting.

---

## 13. M15 review — PASS

`specialists/small_set_*.py` (1410 + 965 lines) separates borders from stock.

- **Borders**: §11.1 minimal-change respected — the direct probe is *declared
  but off by default* (`enable_facets: []`), leaving geographic decomposition
  plus the §11.3 missingness probe. Reverse/singleton handling is delegated to
  M18 via a *request* (`small_set_specialist.py:733`, "Requests only"), never
  executed by M15 itself.
- **Stock**: public-listing gate, primary/secondary/dual facets, company-itself
  check, `candidate_explosion_threshold` for parent/subsidiary/index confusion.
  Freshness is invoked as M14's shared primitive, not reimplemented — no
  duplicate execution.
- M15 closes nothing: there is no `should_stop` and no verdict; §11.3's rule
  needs the accepted set that M16/M17 own.
- Ownership boundary with M18 is a request/execute split, so no cross-module
  ownership violation exists.

---

## 14. M16 review — PASS

`evidence/consensus.py`.

- **§12.1 verified exactly**: `group_supports` (`consensus.py:258-284`) buckets
  events by `group_key` and takes `q_g = max(e.support for e in members)`. Ten
  samples of one probe are ten origin events and one group contribution; five
  facets of one mechanism are five facets and one group. **No sum of repeats.**
- `independent_support` (`:287-293`) counts only groups whose role
  `is_recall` — a verifier shown the candidate is explicitly excluded, and a
  gate contributes nothing. This is exactly the double-counting §12.1 warns
  about.
- Feature semantics (`scoring.py`): `F = coverage_q = g(o)/m(o)` over
  *acquisition* groups only, with gate/verifier/cross-model excluded by
  construction (`scoring.py:250-275`); `L` = clipped calibrated verifier
  log-odds; `X` = cross-model independent recall, carried as its own term;
  `C` = contradiction; `U` = prompt/template disagreement. `I`/`D` come through
  M16's own group mechanism.
- `m(o)` shrinks when optional views are unreachable (`PipelineConfig.__post_init__`,
  `pipeline.py:225-240`), so `q(o)` is not depressed by a mechanism that never
  had a chance to run.
- Structural (M18) evidence enters as signed edges in its *own* independence
  group (`production_bridge.py:243-263`), never as acquisition support.

No double counting, no same-operation inflation, no structural-as-factual
leakage found.

---

## 15. M17 review — PASS

`verification/specialist_*.py`.

- Per-relation contracts matching Table 5 (`specialist_contracts.py`), fixed
  A/B/C labels in two semantically distinct phrasings (`m17_statement_v1`,
  `m17_question_v1`) and ABC/BAC label-order variants — **presentation order
  only**, letters keep their meanings, and each order is calibrated by its own
  content-free control (`specialist_types.py:227`) so no order's bias is
  subtracted from another's. This is §13.1 implemented correctly.
- **Controls never become factual evidence.** The bridge refuses a verifier
  overlay with `readings == 0` even when `control_calls > 0`
  (`production_bridge.py:194-197`): "controls alone are prompt-label bias
  measurements, however many calls they cost."
- Cold/warm behaviour: `calibrator.control_calls_needed` drives cache-aware
  charging (`specialist_verifier.py:485-490`); a cache hit costs zero physical
  calls and is recorded as `control_cache_hit`.
- Physical accounting is exact and measured: `runtime.score_labels` increments
  `self.calls` once per forward pass (`huggingface.py:289`), and the sequence
  fallback increments per label (`:319`) — the pipeline reads the runtime's own
  counters via `physical_snapshot()`/`physical_delta()` rather than assuming a
  per-action cost, so compound readings are billed exactly once each.
- The M17 production caller is `pipeline.execute_action` → `verify_specialist_targets`
  (`pipeline.py:1837`), and the budget projection used for the call plan is
  `m17_actions(...).budget_descriptor` (`pipeline.py:1906-1910`) — the same
  projection Layer 6 uses, so catalogue estimate and executed plan come from one
  source.

**Caveat:** `_planned_neural_cost` (`pipeline.py:1324-1338`) assumes one call
per template plus calibration controls. If A/B/C do not tokenise to single
tokens, `_score_sequence` charges one call *per label*, so the planned cost is a
3× underestimate for the core M7 hard cap. Not reachable for M17 (which reads
runtime counters), but it is a real hazard for M7's `budget.can_afford` guard on
a new tokenizer. Recorded as P3 (F-14) because the current profile's labels are
verified single-token by `inspect_labels`.

---

## 16. M18 review — PASS

`verification/bidirectional_*.py`.

- Exactly four mechanisms, no generic re-ask
  (`bidirectional_types.py:52-58`).
- `shows_candidate` is False **only** for `CANDIDATE_FREE_RECALL` (`:60-67`),
  which is what makes that mechanism — and only that one — a possible route to
  cross-model credit. `CrossModelCredit` (`layer4_types.py:117`) names every
  reason a record may *not* credit `X`, rather than silently dropping it.
- Path: `catalogue()` → `eligible_checks()` → `build_request()` →
  `execute_all()` → typed record → Layer-4 → bridge. The pipeline calls
  `build_request` then `execute_bidirectional_checks` (`pipeline.py:1839-1840`)
  — the caller cannot construct a request the owner did not declare.
- **Candidate-free recall cannot mint an unsupported candidate.** The bridge's
  Rule 2 reports a named-but-absent candidate under `discovered_not_inserted`
  and inserts nothing (`production_bridge.py:163-166`). Proposal §14 only says
  candidate-free recall "increases X"; it never authorises creating an object,
  so this is conformant.
- **Alternate-recovered vs contradiction** is correctly separated:
  `StructuralOutcome.ALTERNATE_RECOVERED` is deliberately absent from
  `_OUTCOME_EDGES` (`production_bridge.py:62-68`) — recovering a *different*
  qualifying object of a set-valued relation signs nothing. `contradicts` is
  True only for explicit `CONTRADICT` (`layer4_types.py:112-114`).
- Only `CheckExecutionStatus.RESOLVED` produces an edge; eligible-but-unscheduled
  and failed checks increment `skipped_unexecuted_checks`
  (`production_bridge.py:234-237`). Rule 3.
- Stock parent/subsidiary ownership sits with M15's request + M18's execution
  (§13 above); no duplication.

---

## 17. ProductionEvidenceBridge review — PASS (best-engineered component in the tree)

`evidence/production_bridge.py`, 279 lines, four explicit rules:

1. Shadow mutates nothing — the mode check precedes any write (`:143`).
2. A candidate the graph does not hold is never inserted (`:163`).
3. Only RESOLVED checks produce edges (`:234`).
4. Each physical measurement becomes at most one edge — duplicate edge ids are
   refused by the graph and swallowed as a no-op, which is the ordinary case
   because Layer 4 is re-integrated after every action (`:217-225`, `:266-269`).

Additional verified properties:

- Subject/relation identity is asserted before any mutation (`:151-156`), so a
  Layer-4 state cannot be applied to the wrong query.
- An unknown `IndependenceGroup` raises rather than being coerced (`:243-248`).
- `SUPPORTED_MODES` fails closed on an unlisted mode (`:128-132`).
- **No bypass path exists.** `graph.add_verification` is called from
  `pipeline._verify_one` (M4, the legitimate core path) and from the bridge.
  `graph._attach` is called from the bridge and from within `graph.py` itself.
  No specialist, verifier or planner reaches the graph directly.
- Legitimate evidence that is dropped: only the three documented classes
  (unavailable verifier, controls-only reading, unresolved/alternate outcome).
  All are correct per proposal.

Minor: the bridge calls `graph._attach`, a private method, from outside its
module. Works, but the coupling is undeclared (P3, F-15).

---

## 18. M19 review — PASS for the estimator, **FAIL for its recording**

`coverage_gap/missingness.py` implements §15 faithfully:

- Five named components (`ResidualComponentName` = novelty_rate,
  singleton_ratio, facet_gap, disagreement, unresolved_mass), each with its own
  availability and reason.
- Weighted combination at `missingness.py:508-548`. **Documented deviation:**
  weights are renormalised over *available* components
  (`(w[n]/mass)·value`), so with uniform weights `R_t` is the *mean* of
  available components rather than the proposal's literal `Σ wᵢ·componentᵢ`.
  This keeps `R_t ∈ [0,1]` and avoids reading an unavailable component as zero.
  §15 supplies no weight values, so this is a defensible instantiation, but it
  is not the literal formula and should be stated in the paper (P3, F-16).
- **No second implementation exists.** Grep for an alternative residual found
  only `coverage.py`'s Module-6 RCSE, which is a different, separately-owned
  quantity (`q_res`) and is neither read nor written by M19.
- Recomputation after each real action is genuine: `execute_action` calls
  `_integrate_layer4` → `bridge.apply` → `_estimate_coverage_gap` **in that
  order** (`pipeline.py:1846-1850`), and `_estimate_coverage_gap` *replaces* the
  existing per-query state rather than appending (`pipeline.py:2121-2126`), so
  the state is current and not a history.
- Numeric novelty uses cluster identity (§10 above).

**The failure is downstream:** M19's output is never recorded into telemetry —
see F-02. Layer 6 would receive a refreshed `R`, but Layer 6 is unreachable.

---

## 19. H / uncertainty review — PASS with one latent inconsistency

- Authoritative `H` is `pipeline.control_entropy` (`pipeline.py:2022-2036`) →
  `coverage.mean_inclusion_uncertainty` (`coverage.py:507-523`) →
  `inclusion_uncertainty(coverage_q(c, contract, config))`, the mean of
  `H_inc(o) = −q log q − (1−q) log(1−q)` over active candidates, normalised by
  `log 2` to `[0,1]`. This is genuinely Module 5's inclusion uncertainty, not an
  implementation convenience: it is the same `coverage_q` M5/M6 use, and
  `CandidateState` carries `q` alongside `H_inc` so "nothing found it" and
  "everything found it" are distinguishable.
- It is deliberately a *different* signal from verifier entropy and template
  disagreement, which are carried separately as `U` and consumed by M19's
  `disagreement` component. No semantic drift between them.
- **Sign is consistent.** `execute_action` records
  `delta_entropy = entropy_before − entropy_after` (`pipeline.py:1863`);
  `ActionTelemetryRecord.delta_entropy` computes
  `pre.entropy − post.entropy` (`telemetry.py:209-212`);
  `historical_bins.ESTIMATE_UNITS["expected_delta_h"]` declares "reduction in
  uncertainty"; and §17 has `+γ·ΔĤ`. All four agree: **a reduction is
  positive.** ✔
- M21 never recomputes an entropy of its own — `utility()` reads
  `bin_estimates.expected_delta_h` and nothing else (`micro_planner.py:71`).
- The collection runner computes no entropy itself; it reads
  `record["entropy_before"/"entropy_after"]` from the canonical seam. ✔

**Latent inconsistency (P3, F-17):** `control_entropy` calls
`mean_inclusion_uncertainty(candidates, contract)` without passing
`self.config.scoring`, so it silently uses `DEFAULT_SCORING`. The only
behavioural difference is `optional_views_available`, which
`PipelineConfig.__post_init__` derives from the run. Under the target config
(`enable_active_controller: true`) both are `True`, so H is currently correct —
but a mandatory-only fixed run would compute `H` against a different `m(o)` than
the rest of the pipeline.

---

## 20. M20 review — architecture PASS, wiring **NOT ACTIVE**

The design is right: `RelationBudgetScheduler.schedule` → `RelationBudgetResult`
→ `BudgetLedger` → `reserve(descriptor)` → runtime action, with `_precharge`
placed **before** any runtime touch and a refused action returning before
touching a runtime at all (`pipeline.py:1823-1834`, with the explicit comment
that refusal is asserted by returning early rather than cleaning up afterwards).
One ledger per query is cached by `(subject, relation, row_index)`
(`pipeline.py:1878-1892`) — a fresh ledger per action would make every reserve
succeed. `physical_delta` raises if the role partition does not sum to the
physical total (`pipeline.py:2081-2085`). Table 6's qualitative policy is
architecture; concrete counts are correctly absent.

However:

- **M20 does not gate neural execution today.** `_precharge` returns
  `True, ""` unconditionally in collection (`pipeline.py:1933-1934`), and in
  every other mode `relation_budget_scheduler` is `None` because no runner
  constructs it. `build_relation_budget_scheduler` raises without calibrations
  (`relation_budget.py:462-478`), which is the correct fail-closed behaviour.
- Collection therefore **correctly avoids pretending** it holds TRAIN-calibrated
  M20 values — the bounded `TrainCollectionPolicy` is the only ceiling and it is
  never serialised as a `RelationBudgetCalibration`. ✔
- Consequence for calibration: no `reserved_class`, no per-class spend, no
  denial record is ever observed, so `discovery_cap`, `verification_cap`,
  `verification_reserve` and the special-reserve sizes have **no observational
  basis** in the collection output (F-03).

`_schedule_relation_budget` is also called once per query inside
`enumerate_query` (`pipeline.py:1058`) purely to append a record, and
`_budget_ledger_for` schedules again in production. Non-neural and harmless, but
it is a duplicated plan (P3, F-18).

---

## 21. M21 review — PASS (the module), correctly excluded from collection

`control/micro_planner.py` is a faithful, disciplined transcription of §17:

- `utility()` (`:58-97`) transcribes the equation once, itemised, with the three
  penalty terms **subtracted**. No seventh term, no relation-specific
  adjustment, no reuse of M7's score.
- Three orderings kept strictly apart: legality (owner) → affordability (M20,
  *reused* via a deep-copied probe ledger, never recomputed) → value (M21).
- **`best_value > tau_continue` strictly; equality means STOP** (`:442-446`). ✔
- Lookahead is depth ≤ 2 (`:283-367`), undiscounted and additive because §17
  supplies no discount term, with affordability carried forward through a
  hypothetical ledger copy. No MCTS, no learned policy, no third step.
- **M21 executes nothing**: it touches no runtime, reserves nothing on the real
  ledger, and cannot mutate `ObjectEntities`. `_plan_next_action`
  (`pipeline.py:1697-1766`) maps the decision back to the owner's catalogue
  entry and `execute_action` does the work.
- Estimates come only from `history.lookup(...)`; a missing bin **raises**
  rather than defaulting to zero (`historical_bins.py:80-85`), which is the
  right call — defaulting would bias `argmax` toward the sparsest history.
- Deterministic tie-break on canonical action identity (`:427-435`); insertion
  order, object id and hash order never decide.
- **Action de-duplication is correct.** `_plan_next_action` collapses several
  catalogue entries that project to one `action_id`, keeping the first and
  preserving catalogue order (`pipeline.py:1729-1738`), and `_screen` raises on
  a duplicate `identity` (`micro_planner.py:205-209`). Distinct actions are not
  collapsed (identity is `(family, action_id, target, facet_id)`), and identical
  logical actions are not double-ranked.
- Collection never reaches M21 (`pipeline.py:1783` requires `is_production`) —
  using an uncalibrated planner to gather the bins it needs would be circular. ✔

---

## 22. M7 / action-execution review — PASS with a depth limitation

Per controller round the seam is coherent:

```
_select_actions → one action → execute_action
   before  = physical_snapshot()
   H_before= control_entropy(graph)
   precharge(kind, action, graph)          ← before any runtime touch
   [m17] verify_specialist_targets  |  [m18] build_request + execute_bidirectional_checks
   _integrate_layer4 → bridge.apply → _estimate_coverage_gap
   after   = physical_snapshot();  H_after = control_entropy(graph)
   cost    = physical_delta(before, after)
```

- **One logical action, N physical calls.** Compound M17 readings (two
  templates × two label orders, plus controls) are one planner action measured
  by differencing the runtimes' own counters — not multiple planner decisions,
  and not an assumed per-type cost.
- **No execute-twice.** `executed_ids` and the `chosen[0]` single-action rule
  prevent re-execution within a round; `graph.add_verification` and `_attach`
  refuse duplicate edge ids, so a retry cannot double-bill evidence.
- **No stale catalogue execution**: the catalogue is re-read from
  `source(consensus)` before each selection round.
- **Action identity is consistent** between `_plan_next_action` and
  `_action_descriptor` because both project through `m17_actions`/`m18_actions`.
- `physical_delta` raises on a backwards counter or a broken role partition, so
  a silent accounting corruption is impossible.

**Limitation (F-04):** the unconditional `break` at `pipeline.py:2007` caps the
loop at one M17 action and one M18 action per query, making the `while True`
loop and its `remaining`/`executed_ids` bookkeeping dead code. The comment
directly above says "Re-read the catalogue between actions: a stale one would
offer targets the previous action already resolved" — a statement the `break`
makes unreachable.

---

## 23. TrainCollectionPolicy review — appropriate, with a blind spot

`controller_calibration/collection_policy.py` is well-suited to bootstrapping:

- **Legal actions only** — `select` returns a subset of the catalogue it is
  handed; `_select_actions` independently re-checks that every returned entry is
  in the catalogue and raises `UnsupportedAction` otherwise
  (`pipeline.py:1793-1799`). The policy never constructs an action.
- **Deterministic** — round-robin across `sorted(by_family)`, within a family
  ordered by published identity then position, final result re-sorted by
  catalogue position (`:203-209`). Same row ⇒ same selection.
- **Bounded** — `DEFAULT_PER_FAMILY_LIMIT = 2`.
- **Relation-aware** implicitly, because the catalogue it is offered is already
  relation-specific.
- **No TRAIN gold, no validation feedback, no M21 call.** Verified by reading
  the whole module: it consults nothing but the catalogue.

**Sampling-bias assessment.** Family-first round-robin is the right shape: it
deliberately takes from every legal family before repeating any one, which is
exactly what prevents a cheap family from monopolising the bins. But two real
biases remain:

1. Because at most one M17 and one M18 action execute per query (F-04), the
   policy's `per_family_limit=2` is unreachable and every bin is populated from
   **round-1 states only**. Successor statistics — which §17's depth-2 lookahead
   requires — have zero support by construction.
2. `note_families()` (`:214-217`) **is never called by the runner.** A family
   that never appears in any catalogue simply never enters the ledger, so
   `integrity_ok()` returns True. This is not a distinction between "inherent
   TRAIN coverage absence" and "collection failed to execute a legal family" —
   it is a third, undetected case: **"the family was never even offered."**
   A read-only probe (below) confirms the failure mode: a 4-row collection that
   executed exactly one family (`CANDIDATE_FREE_RECALL`) and zero M17 actions
   printed `required action-family coverage: PASS` and exited 0.

---

## 24. Telemetry / calibration-sufficiency review — **FAIL (decisive)**

This is the go/no-go question of §35, and the answer is negative.

`train-telemetry-v1` (`controller_calibration/telemetry.py`) is a *good schema*.
`ControlStateFeatures` carries all five §15 components alongside `R_t`;
`ActionOutcome` carries `candidates_added/supported/contradicted`, `redundancy`,
`verifier_outcome`, `prompt_tokens`, `cache_hits`; the record carries
`target_class`, `action_id`, `reserved_class`, `model_role`. The schema is
sufficient in principle.

**The runner does not populate it.** Verified empirically by executing the real
`main()` against scripted offline runtimes with M11–M19 enabled. A verbatim
committed record:

```json
{"schema_version":"train-telemetry-v1","row_index":2,"Relation":"hasArea",
 "program_type":"ProgramType.NUMERIC","round_index":1,
 "operation_id":"m18:1:1:140144940782416","action_family":"CANDIDATE_FREE_RECALL",
 "target_class":"","action_id":"","model_role":"enumerate","reserved_class":"",
 "pre_state" :{"residual":0.0,"novelty_rate":0.0,"singleton_ratio":0.0,"facet_gap":0.0,
               "disagreement":0.0,"unresolved_mass":0.0,"entropy":0.0,
               "active_candidates":0,"calls_used":8,"calls_remaining":0},
 "post_state":{"residual":0.0,"novelty_rate":0.0,"singleton_ratio":0.0,"facet_gap":0.0,
               "disagreement":0.0,"unresolved_mass":0.0,"entropy":0.0,
               "active_candidates":0,"calls_used":8,"calls_remaining":0},
 "outcome":{"physical_calls":1,"prompt_tokens":0,"generated_tokens":0,"cache_hits":0,
            "candidates_added":[],"candidates_supported":[],"candidates_contradicted":[],
            "redundancy":0.0,"verifier_outcome":"","errors":[]}}
```

### Why the state block is structurally zero

`_state_features` (`scripts/run_train_calibration_collection.py:131-159`) reads:

```python
gap = getattr(state, "coverage_gap", None) or getattr(state, "gap", None)
if gap is None:
    break
```

`state` here is a `CoverageGapState`. Verified directly:

```
hasattr(CoverageGapState, "coverage_gap") -> False
hasattr(CoverageGapState, "gap")          -> False
hasattr(CoverageGapState, "residual")     -> True  (CoverageGapComponents)
```

The attribute is `state.residual`. The runner therefore **always** takes the
`break` and returns all-zero features. The name `coverage_gap` comes from
`PlannerStateSnapshot`, a different type.

There is a **second, independent** bug on the same path. Even with the attribute
fixed, the component loop does `str(getattr(component, "name", ""))` and compares
against keys `"novelty_rate"`, … . On Python 3.14.5:

```
str(ResidualComponentName.NOVELTY_RATE) -> 'ResidualComponentName.NOVELTY_RATE'
ResidualComponentName.NOVELTY_RATE.value -> 'novelty_rate'
```

so no component would match. `.value` is required. (`ResidualComponent` also has
no `signal` attribute, so the second fallback is inert.)

The same `str(enum)` class of error produces `program_type:
"ProgramType.NUMERIC"` (line 359) instead of `"NUMERIC"`. The pipeline already
owns `_program_type_value()` for exactly this reason (`pipeline.py:145-152`,
whose docstring says the repr form "makes Module 20 reject every schedule") — the
runner does not use it. Bins built from this telemetry would carry
`program_type="ProgramType.NUMERIC"` and would **never match**
`PlannerStateSnapshot.program_type == "NUMERIC"` at
`HistoricalBinPackage.lookup` time.

### Why pre_state and post_state are identical even where non-zero

Both are built by calling `_state_features(pipeline, graph)` **at the same
moment** (lines 368-373), after the whole row has finished; only `entropy` is
overridden from the per-action seam. `_state_features` reads
`pipeline.coverage_gap_results`, which `_estimate_coverage_gap` *replaces* in
place — so the value available at telemetry-build time is the final post-row
state for every record. `ΔR ≡ 0` by construction, independent of the two bugs
above.

### Derivability of the six §17 estimates and the successor distribution

| required by `historical_bins.REQUIRED_ESTIMATES` | source in telemetry | derivable? |
|---|---|---|
| `expected_verified_gain` | `candidates_added/supported` — never populated | **NO** |
| `expected_delta_r` | `pre.residual − post.residual` ≡ 0 (two bugs + same-moment read) | **NO** |
| `expected_delta_h` | real `entropy_before/after` from `execute_action` | **YES** |
| `expected_cost` | real `physical_calls` from `physical_delta` | **YES** |
| `expected_redundancy` | `redundancy` — never populated | **NO** |
| `expected_fp` | needs `candidates_added` + offline gold | **NO** |
| `successors[].successor_state_bin` + probability | needs a real post-action state bin | **NO** |
| bin key `state_bin_key` | needs residual/novelty/singleton/facet_gap/disagreement/unresolved_mass, M9 grades, `residual_availability`, `null_failed_recall_only`, `numeric_cluster_count`, `verification_reserve_unused` | **NO** — all zero or absent |
| bin key `program_type` | `"ProgramType.NUMERIC"` — wrong form | **NO** (unusable) |
| bin key `target_class` | `""` always | degraded (family-level only) |

**Two of six estimates are derivable. The state binning itself is not.** Every
action would collapse into one degenerate bin key, so no relation-specific
historical bin can be constructed at all.

For M20 the picture is the same: `reserved_class` is never set, `prompt_tokens`
is never measured anywhere in the codebase (no runtime carries a cumulative
prompt-token counter — `physical_snapshot()` reads only `calls` and
`generated_tokens`), and per-row totals are absent because `calls_used` is a
run-cumulative counter of upgraded-module calls only. `hard_calls`,
`hard_generated_tokens`, `discovery_cap`, `verification_cap`,
`verification_reserve` and the special-reserve sizes have no per-relation
observational basis.

**Conclusion: running the full 477-row TRAIN collection today would consume the
entire session and produce telemetry from which M21 cannot be calibrated at all
and M20 only partially. A second full TRAIN inference would be required. §35
mandates BLOCKED.**

---

## 25. TRAIN-gold isolation review — **PASS**

Traced from dataset read to every downstream call:

- `load_dataset("train")` → `Dataset.rows: list[DatasetRow]`.
- `dataset.queries()` (`data/loader.py:43-45`) constructs
  `Query(row.subject, row.relation, row.row_index)` — nothing else.
- `Query` is a frozen dataclass with exactly three fields
  (`types.py:191-205`). It carries no gold, no aliases, no row payload.
- The runner touches `query.subject`, `query.relation`, `query.row_index` and
  `dataset.sha256` / `dataset.path` (metadata only). No `DatasetRow` object ever
  crosses into the pipeline.
- `compile_query(subject, relation, row_index)` (`contracts/router.py:48`) is
  the single entry, and it returns a fresh `Query` plus a contract.
- No full-row dict propagation exists: grep for `to_official_row`,
  `preferred_surface_forms`, `object_entities` on the collection path returns
  nothing outside `evaluation/` and `scripts/evaluate_local.py`, neither of
  which the collection runner imports.

Gold cannot reach prompts, acquisition, pseudo-memory, M3, specialists, the
verifier, M18, M19, H, M20, the collection selector or telemetry features.
Offline joining remains separate, as designed.

---

## 26. Call / token-accounting review — PASS for calls, **FAIL for prompt tokens**

- `runtime.generate` → `self.calls += 1` (`huggingface.py:237`,
  `offline.py:70,135`). ✔
- `runtime.score_labels` → `self.calls += 1` for single-token labels
  (`huggingface.py:289`); the sequence fallback charges one call per label
  (`:319`) — correct, since each is a real forward pass. ✔
- Cache hit → zero physical call: `calibrator.control_calls_needed` returns 0
  for a warm control and `specialist_verifier` charges accordingly
  (`specialist_verifier.py:485-490`). ✔
- `physical_snapshot`/`physical_delta` (`pipeline.py:2038-2086`) read the
  runtimes' own counters, guard against a backwards counter, and **assert the
  role partition sums to the physical total**. Role attribution is measured, not
  inferred — which matters because M14/M15 use both models inside one operation.
- `_charge_calls` (`pipeline.py:2088-2097`) bills to exactly one of
  `production_calls` / `shadow_calls`, never both.
- `RunCounters.charge` refuses an unknown role and refuses negative values
  (`progress.py:38-59`).
- Resumed-run double counting: telemetry has a duplicate-identity guard that
  spans the file (`telemetry.py:274-297`); accounting is restored from the
  checkpoint and rows already in `completed` are skipped. ✔

**Failure:** `prompt_tokens` is never measured. `LMRuntime` has `calls` and
`generated_tokens` only (`models/base.py:236-237`); per-call
`LabelScoreResult.prompt_tokens` / `GenerationResult.prompt_tokens` exist but are
aggregated only into `graph.total_prompt_tokens()`, which the collection path
never reads. `counters.charge(...)` is called without `prompt_tokens`
(runner :407-408), so the live summary and `accounting.json` report
`prompt tokens: 0` for a run that will consume millions. Telemetry's
`outcome.prompt_tokens` is likewise always 0.

Also: `failed` (runner :313) is never appended to, so `rows_failed` is always 0
and `manifest.json` reports `"rows_failed": 0` even for a failed run.

---

## 27. Checkpoint / resume durability review — **FAIL on multi-resume**

Audit 0040's forced-failure test was **not** taken on trust; the real
implementation was read and then exercised with an additional scenario.

What is genuinely correct:

- `CollectionCheckpoint.save` writes to `*.partial` then `Path.replace()` —
  atomic on POSIX (`checkpoint.py:79-86`).
- `RunIdentity` covers train sha, repo sha, config sha, both model ids and
  revisions, policy version, telemetry schema version and `total_rows`; any
  drift refuses the resume whole (`checkpoint.py:105-126`). A `--limit N` resume
  against a full run is refused because `total_rows` differs. ✔
- `TelemetryWriter` requires `resume` explicitly rather than guessing from file
  existence, preloads committed identities so the duplicate guard spans the
  restart, and flushes per record (`telemetry.py:268-300`).
- Row transaction ordering is correct: telemetry → coverage → prediction+flush →
  counters → `completed.add` → `persist()`. A mid-row failure leaves nothing
  durable for that row.
- Malformed/truncated JSONL raises `TelemetryError` with file:line
  (`telemetry.py:312-325`); a corrupt checkpoint raises `ResumeRefused`
  (`checkpoint.py:115-118`); a bad checkpoint version is refused (`:91-95`).
- Resume with all rows complete: every row is skipped and the run exits 0.

**The hole — F-05, confirmed by read-only probe.** On resume the runner mints a
*new* `run_id` (:279) and never overwrites it, but resolves the output directory
from the checkpoint (:289) while `persist()` writes the **new** id back into the
checkpoint (:322):

```python
run_id  = new_run_id(...)                                   # always fresh
out_dir = args.output_dir / restored.counters.get("run_id", run_id)   # old dir
...
state = counters.to_json(); state["run_id"] = run_id        # writes the NEW id
```

Consequence, verified by running `main()` three times (fail → resume+fail →
resume) with distinct timestamps:

```
run directories under .../run
  cover_kbc..._20260807T043258Z: telemetry_lines=2 prediction_lines=2
  cover_kbc..._20260807T043300Z: telemetry_lines=2 prediction_lines=2
checkpoint completed_rows: [0, 1, 2, 3]
checkpoint counters.run_id: cover_kbc..._20260807T043302Z   ← no such directory
```

Rows 0–1 live in directory A, rows 2–3 in directory B, the checkpoint names a
third id that has no directory, and the final `manifest.json` — written into B —
reports `rows_completed: 4` while B holds only 2 rows of telemetry and 2
predictions. `action_coverage.json` is also not restored on the second resume
(the path is under the non-existent directory), so the coverage ledger silently
restarts.

Audit 0040's test does not catch this because it performs exactly **one**
resume, and its `_run_dir` helper asserts there is exactly one directory. A
477-row A100 run will very plausibly need more than one resume.

Also noted: telemetry records written after a resume carry the *new* `run_id`
while sitting in the same file as records carrying the old one, so `run_id` is
not a stable grouping key for offline derivation (P3, F-19).

---

## 28. CLI review — `scripts/run_train_calibration_collection.py`

Read line by line. Flags and defaults:

| flag | required | default | behaviour |
|---|---|---|---|
| `--config` | yes | — | experiment YAML |
| `--output-dir` | yes | — | root; run dir is `<root>/<run_id>`, checkpoint is `<root>/checkpoint.json` |
| `--limit` | no | `0` | development only; **bypasses the 477-row guard** (:253) and changes `RunIdentity.total_rows`, so a limited run cannot be resumed as a full one |
| `--resume` | no | `False` | continues a matching interrupted run |

Verified:

- **477 rows without `--limit`**: the guard at :253-257 raises `CollectionError`
  if `len(queries) != 477`, and TRAIN is confirmed at 477 rows. ✔
- **No VAL/TEST path can be selected.** `load_dataset(CALIBRATION_SPLIT)` is
  hard-coded to `"train"` (:251), and `require_split` refuses any other
  `experiment.split` (:249). There is no `--split` flag. ✔
- Exit codes: `0` success, `1` on failure or coverage-integrity FAIL, `2` on
  `CollectionError`/`ResumeRefused` (:484-488). ✔
- Incremental durability: `persist()` after every row, plus a `finally` block. ✔
- Progress logging present (see §29).
- Path safety: paths come from `--output-dir`; `mkdir(parents=True,
  exist_ok=True)`; no shell interpolation; no network.

**But the CLI cannot be run at all today** with any committed config (F-01), and
its `--resume` is unsafe past the first use (F-05).

Dead locals: `pre` (:339) and `post` (:344) are computed and never used; `post`
is immediately shadowed inside the loop (:353). Harmless, but they are the
remnants of the row-level pre/post design that the per-action design replaced,
and their presence disguises F-02.

---

## 29. Live-progress review — PASS except the token counters

- `query_line` emits `[TRAIN 21/477] relation=… subject="…"` (`progress.py:150-152`). ✔
- `round_line` emits `[TRAIN 21/477][round=3] …` and deliberately has **no
  fabricated denominator** (`:155-162`) — correct, since adaptive round count is
  unknown while it is being taken. ✔
- Counters come from real accounting: `rows_completed`, `physical_model_calls`,
  `enumerator_calls`, `verifier_calls`, `generated_tokens`, `elapsed`,
  `seconds_per_row`, `eta_seconds` all derive from `RunCounters` fed by
  `physical_delta` of the runtimes' own counters. ✔
- ETA stays `None` until `MIN_ROWS_FOR_ETA = 5` rows (`:76-80`). ✔
- Resume restores committed counters and **restarts the clock**
  (`:105-118`) — correct, since an interrupted session's wall clock says nothing
  about this one.

Untruthful fields:

- `prompt tokens: 0` always (F-06).
- `failed: 0` always (`failed` list never appended, F-08).
- Only one round line per row is printed (:340), fixed at `round=1`, before
  `decide_graph` runs — so the actual M17/M18 rounds inside `decide_graph`
  produce no round line at all. The `[round=k]` format is correct; it is simply
  never emitted for real rounds (P3, F-20).

---

## 30. Model / parameter-budget review — **PASS**

From `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`:

| role | model_id | revision | published params |
|---|---|---|---|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 4,659,865,088 |
| **total** | | | **28,671,226,368 ≤ 32,000,000,000** ✔ |

- Both ids and both revisions match the required values **exactly**.
- Counts are the full released checkpoint
  (`published_checkpoint_parameters == budget_count_parameters ==
  published_total_parameters`), with `parameter_source` URLs and
  `parameter_source_verified: true`.
- `audit_parameter_budget` (`models/budget.py:98-145`) fails on a missing count,
  a non-positive count, an unverified source, and on a budget count *below* the
  full checkpoint. **Quantization is explicitly not consulted** (`:136-137`);
  `quantization: nf4` is recorded for reproducibility only. ✔
- Tokenizers: `tokenizer_backend: mistral_common` for Mistral (the Tekken
  tokenizer, declared explicitly because `AutoTokenizer` converts it lossily) and
  `huggingface` for Qwen. Correct per §18's tokenizer-compatibility requirement.
- Shared-checkpoint double counting is prevented by `counted_specs` deduping on
  `model_id` (`budget.py:34-48`).
- **No hidden third neural model.** `build_pipeline` constructs exactly
  `runtime` and `verifier_runtime` (`runner :168-170`); the audit is run over
  exactly those specs and raises `CollectionError` if it fails (:174-177).
  Repository-wide grep for other model construction found only `NullRuntime`,
  `ScriptedRuntime` (non-neural fixtures) and the optional
  `hidden_states` DoLa seam, which no configuration enables.
- `configs/models/bakeoff-candidates.yaml` lists §18's Table 7 alternatives with
  PROVISIONAL markers; none is wired into any runner.

---

## 31. No-training review — **PASS**

Repository-wide executable search for `optimizer`, `.backward(`, `torch.optim`,
`requires_grad`, `LoRA`, `PEFT`, `adapter_config`, `.train()`, `fine-tun*`,
`gradient`, learned router / policy / verifier head:

- **One** hit, and it is prose: `control/planner_types.py:295` — a comment
  stating that no coefficient is a gradient and nothing updates at run time.
- Positively: `self.model.eval()` (`huggingface.py:127`) and `torch.no_grad()`
  on every forward path (`:255, :292, :322, :346`).
- M21's coefficients are read from a versioned JSON package and validated;
  `HistoricalBinPackage` is a frozen dataclass with **no mutation path**
  (`historical_bins.py:272-310`), so online updating during VAL or TEST is
  structurally impossible.
- M20's calibration is likewise a frozen artifact.
- No neural weight can change during collection or validation.

---

## 32. Closed-book review — **PASS**

Executable search across `src/` and `scripts/` for `requests`, `urllib`,
`httpx`, `aiohttp`, `socket`, `wikipedia`, `wikidata`, `bm25`, `faiss`,
`chromadb`, `pinecone`, `elasticsearch`, `serpapi`, `duckduckgo`, `web_search`:

- Zero factual-retrieval hits. Every "requests" match is a local variable or
  docstring meaning "check requests" (`pipeline.py:2334`,
  `bidirectional_verifier.py:650`, …).
- `subprocess` appears in exactly four places, none on the inference path:
  `git rev-parse` for the manifest (`runtime/manifest.py:36`, runner `:109`) and
  invoking the *local* official evaluator (`evaluation/harness.py:178`,
  `scripts/evaluate_local.py`).
- `from_pretrained` / `hf_hub_download` are weight and tokenizer **setup**, not
  inference-time retrieval (`huggingface.py:176`, `mistral_tokenizer.py:79-92`).
- `query_intelligence/prompt_registry.py:12` states the constraint in the module
  that would be the natural place to violate it, and the code matches.
- Runtime factual knowledge comes only from the two frozen checkpoints plus
  deterministic query-local processing (normalisation, unit conversion,
  clustering, dedup, scheduling) — all permitted by §2.3.

---

## 33. Config review — **BLOCKER**

Eight config files, all inspected.

| file | split | M11 | M12–15 | M16 | M17 | M18 | L4 | M19 | M20 | M21 | L6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `experiments/cover_kbc_v2_mistral24_qwen4.yaml` | **val** | off | off | off | off | off | off | off | off | off | off |
| `experiments/ablation_fixed_multiview.yaml` | val | — | — | — | — | — | — | — | — | — | — |
| `experiments/smoke_abstain.yaml` | val | — | — | — | — | — | — | — | — | — | — |
| `experiments/smoke_staged_roleswap.yaml` | val | off | off | off | off | off | off | off | off | off | off |
| `experiments/smoke_staged_scripted.yaml` | val | off | off | off | off | off | off | off | off | off | off |
| `models/{bakeoff-candidates,offline-null,qwen3.5-9b}.yaml` | n/a — model profiles only |

**There is no config that can run the TRAIN collection.**

1. Every experiment config declares `split: val`. `require_split` (runner :249)
   raises `IntegrationModeError` before a single row is read, and the process
   exits 2 with `REFUSED: train_calibration_collection_only may only run on
   'train', not 'val'`.
2. Even with the split corrected, `parametric_retrieval.enabled: false` makes
   `build_parametric_retriever` return `None`, which makes M12–M15 `None`;
   `consensus.enabled: false` makes M16 `None`, which makes M17, M18, Layer-4
   and M19 `None`. `_run_consensus` is never called, `action_records` stays
   empty, and **the telemetry file would contain zero records** while the run
   printed `TRAIN CALIBRATION COLLECTION COMPLETE`, `telemetry integrity: PASS`
   and `required action-family coverage: PASS`, and exited 0.

The only place the correct config shape exists is
`tests/test_collection_failure_resume.py:45-65`, which **synthesises it at
runtime in `tmp_path`** — forcing `split: train` and enabling
`consensus`, `layer4_integration`, `coverage_gap`, `specialist_verifier`,
`bidirectional_verification`, all four specialists and `parametric_retrieval`.
That fixture is the de-facto specification of the collection config and it is
not committed.

Additional config findings:

- `build_consensus_engine` is called from `build_pipeline` (runner :195) with
  neither `available_specialists` nor `relations`, so its specialist-availability
  guard is vacuous (`consensus.py:857-865` computes `needed` from an empty
  `relations`). A config that enables M16 without the owning specialist would
  build cleanly and then raise `ConsensusError` from `_specialist_result_for`
  (`pipeline.py:1573`) on the first row — and because the runner has **no
  per-query exception handling** (unlike `pipeline.run()`), that aborts the whole
  477-row run. (F-07)
- `evaluate_readiness` (`controller_calibration/readiness.py`) is a well-designed
  gate that computes exactly the right verdict — and is referenced only by tests.
  The collection runner never calls it. (F-09)
- `relation_budget_scheduler.calibration_file: null` and
  `micro_planner.{historical_bins,planner_calibration}: null` correctly keep both
  modules disabled; enabling either without artifacts raises. No fake
  `SYNTHETIC_TEST` artifact appears in any committed config. ✔
- No stale core config, no contradictory flags, no duplicated model definitions,
  no absolute developer paths.

**Which config will be used for the full TRAIN collection: none exists.**
**Which config will be used for full production validation after calibration:
none exists** — no config can express `mode: production` (F-11), no runner
passes `IntegrationMode.PRODUCTION`, and no runner supplies `layer6_integrator`.

---

## 34. Test-quality review

`2966 passed, 3 skipped in 35.32s`. Volume was **not** treated as evidence; the
important tests were read.

Genuinely strong:

- `tests/test_collection_failure_resume.py` drives the **real `main()`** with
  real `TelemetryWriter` open modes, a real checkpoint and real artifact
  read-back, injecting the failure into action execution rather than mocking the
  writer. Its own docstring explains that mocking the open mode is what would
  have hidden the Audit-0038 truncation bug. This is the right shape of test.
- `tests/test_action_execution_seam.py`, `test_m20_precharge_gate.py`,
  `test_m21_production_bridge.py`, `test_production_bridge.py` assert *behaviour*
  through the seam (refused action ⇒ zero runtime calls; shadow ⇒ byte-identical
  predictions), not flags.
- `test_control_entropy.py` pins the ΔH sign convention end to end.
- `test_micro_planner.py` asserts §17 term by term, including strict `>` and
  STOP-at-equality.

Weaknesses that let the P0/P1 findings through:

1. **No test asserts that a telemetry record carries a non-degenerate state.**
   No assertion anywhere requires `pre_state.residual != post_state.residual`,
   or `residual > 0` for a query M19 actually scored, or that
   `candidates_added` is non-empty for an action that added a candidate. The
   entire §24 failure is invisible to 2966 tests.
2. **`program_type` is never asserted against the canonical value.** The repr
   form `"ProgramType.NUMERIC"` passes every existing assertion.
3. **Resume is tested exactly once.** `_run_dir` asserts a single run directory,
   which structurally prevents the multi-resume split (F-05) from being
   observed.
4. **The coverage gate is tested only on families that were offered.**
   `test_family_absent_from_train_is_distinct_from_a_coverage_failure` uses
   `note_families()` — which production code never calls — so the "family never
   offered ⇒ silent PASS" case is tested in a configuration production does not
   use.
5. **No test drives the committed config through the collection runner.** The
   only collection test builds its own config, so "no runnable config exists" is
   untestable by construction.
6. `test_architecture_conformance.py` and parts of `test_system_e2e_conformance.py`
   assert on module structure and source ordering rather than behaviour; useful
   as regression pins, but they cannot detect an unwired module.

Missing high-value tests (recommended alongside the fixes):

- A collection smoke over the **committed** config that asserts a non-empty
  telemetry file and a non-degenerate `pre_state`/`post_state` pair.
- `program_type == contract.program_type.value` in every emitted record.
- Two sequential resumes landing in one directory.
- `integrity_ok()` returns False when a declared family was never offered.

---

## 35. Dead / stale / duplicate-code review

| symbol | file | status |
|---|---|---|
| `CoverPipeline.has_second_model` | `pipeline.py:537` | zero callers; self-documented as deprecated |
| `_minimum_neural_cost` | `pipeline.py:1343` | alias, zero callers |
| `CoverPipeline.integrate_layer4` (public) | `pipeline.py:2302` | zero callers |
| `layer6_integrator` parameter | `pipeline.py:345` | **no entrypoint ever supplies it**; makes `Layer6Integrator`, `collect_catalog` and the M11/M7/specialist catalogue projections unreachable in production |
| `TrainCollectionPolicy.note_families` | `collection_policy.py:214` | zero production callers ⇒ coverage gate blind spot (F-10) |
| `evaluate_readiness` | `readiness.py:102` | zero production callers (F-09) |
| `remaining` / `executed_ids` / `pending` loop | `pipeline.py:1981-2006` | unreachable past one iteration because of the `break` at `:2007` |
| `pre` / `post` row-level locals | runner `:339`, `:344` | dead; superseded by per-action records |
| `id(other) is not id(action)` | `pipeline.py:1997` | comparing two distinct `int` objects with `is`; a no-op saved only by the `and other is not action` clause |

No TODO/FIXME/HACK/XXX anywhere in `src/`, `scripts/` or `configs/`. No stale
core-only runner, no duplicated bridge, no duplicated planner. No legacy
alternative path can be selected by the real CLI — the CLI has no path
selection at all.

---

## 36. Error-handling review

Correct fail-closed behaviour:

- `parse_mode`, `require_split`, `MicroPlannerConfig`, `RelationBudgetConfig`,
  `build_relation_budget_scheduler`, `build_micro_planner`,
  `load_planner_calibration`, `HistoricalBinPackage.from_json` all raise rather
  than defaulting. `SYNTHETIC_TEST` artifacts are refused outside tests.
- `_check` in `historical_bins.py` refuses `None`, non-numeric, non-finite and
  out-of-range estimates — **a missing estimate is never zero**.
- `_finite` in `telemetry.py` rejects NaN/inf.
- `ActionTelemetryRecord.__post_init__` refuses executed-without-selected,
  selected-but-illegal, executed-without-post-state, and
  not-executed-but-charged.
- `physical_delta` raises on a backwards counter or a broken role partition.
- `CorruptPendingAction` / `PendingActionNotConsumed` refuse to finalise
  abandoned work.
- `LogitsUnavailable` is caught narrowly and returns 0 calls
  (`pipeline.py:652`, `:893`) rather than fabricating a verdict.
- Budget refusal returns before touching a runtime; planner STOP executes
  nothing.

Silent fallbacks that change semantics:

- `_state_features`'s `break` on a missing attribute (F-02) is the worst case in
  the repository: a schema-shape mismatch degrades silently to all-zero state
  and nothing downstream notices.
- `family_of(action) or record["kind"].upper()` (runner :363) falls back to
  `"M17"`/`"M18"` if an owner publishes no family attribute, which would merge
  distinct families into one bin without a warning.
- The whole-run `except BaseException` (runner :418) is correct for durability
  but means one malformed row aborts all 477 (F-07), and the failing row is not
  recorded in `failed`.

---

## 37. Determinism / reproducibility review — PASS

- `PipelineConfig.seed = 42`; `ElicitationEngine(runtime, seed=…)` and the
  verifier engine at `seed + 1`. `DecodeProfile.temperature = 0` ⇒ greedy.
- No `import random`, no `time.time()`-dependent decision anywhere in `src/`.
  The only clock uses are `time.monotonic` (elapsed), `perf_counter` (latency)
  and `datetime.now` in `runtime/manifest.py` (run ids and timestamps).
- Ordering is explicit everywhere it matters: `sorted(by_family)` and identity
  sort in the collection policy; `sorted(buckets)` in `group_supports`;
  `sorted(root.successors, key=…)` and the canonical-identity tie-break in the
  planner; `sorted(self.families)` in the coverage table. Only one
  `set(...)` membership test exists (`bidirectional_verifier.py:246`) and it
  iterates the enum, not the set.
- Identity capture is thorough: `RunIdentity` pins train sha256, repo sha,
  config sha256, both model ids and revisions, policy version, telemetry schema
  version and row count. `manifest.json` records all of it plus `train_path`.

**One material nondeterminism**: `operation_id` embeds `id(action)` (runner
:362), a CPython memory address. Two runs of the same row produce different
operation ids, and an address can be reused after garbage collection, so the
duplicate-identity guard is theoretically defeatable and offline joins on
`operation_id` are impossible. A deterministic identity already exists —
`action_catalog` publishes `M17:SPECIALIST_VERIFY:<target_id>` and
`M18:<kind>:<target_id>` (`action_catalog.py:314`, `:406`) — and the runner does
not use it. (F-12)

`new_run_id`'s one-second granularity also allows two runs started in the same
second to collide on a directory (observed during probing).

---

## 38. Proposal / paper consistency review

**Implemented exactly:** §12.1 `q_g = max`; §17's utility equation, strict
threshold and depth ≤ 2; §14's four mechanisms and the candidate-free/`X` rule;
§10.3's three null-evidence classes; §8.3's `UNKNOWN ≠ contradiction`; §8.2's
relative-δ clustering; §13.1's label-order controls with per-order calibration;
§9.3's protected verification reserve as a real constraint; §2.2's closed-book
boundary; §2.3's no-training boundary; §18's exact id/revision/parameter
discipline; §21.2's "specialists never bypass the graph".

**Implemented approximately:**

- §15 `R_t` — weights renormalised over available components rather than summed
  (F-16). Defensible; must be stated.
- §22 Algorithm 1 — the `while BUDGETLEFT` loop over legal actions is realised
  as *one* M17 action plus *one* M18 action per query (F-04). Not the unbounded
  adaptive loop the algorithm describes.
- §16's "precharge before every neural call" — implemented and correct, but
  bypassed in collection and unconstructed elsewhere, so it currently gates
  nothing.

**Absent from the implementation (correctly):** §14.1's DoLa adapter (the seam
exists via `hidden_states`; nothing enables it, and the proposal makes it
optional and conditional on a validation ablation). §19's Prompt Lab and offline
prompt search are not implemented; the proposal places them outside the
inference path.

**Behaviour not described by the proposal:** the three-valued `IntegrationMode`
and `TRAIN_CALIBRATION_COLLECTION_ONLY` mode; `ProductionEvidenceBridge` as a
named chokepoint; `train-telemetry-v1`; `TrainCollectionPolicy`. All four are
sound engineering that the proposal implies but does not name — they should be
described in the paper's system section.

**Terminology mismatch:** the per-module config key `mode:` (pinned to
`"shadow"`) collides with the pipeline's `IntegrationMode` (F-11). The proposal
uses neither term; the repository uses one word for two concepts.

---

## 39. Repository hygiene review — **PASS**

- `benchmark/` unchanged: `git diff -- benchmark/` empty, single commit
  `b607ae1`, upstream pin recorded in `UPSTREAM_COMMIT.txt`.
- `outputs/`, `predictions/`, `__pycache__/`, `.pytest_cache/`, model caches and
  `*.safetensors`/`*.bin`/`*.pt` are gitignored; none is tracked.
- No secrets, tokens, API keys or private data. Grep for
  `/home/`, `/Users/`, `/mnt/`, `api_key`, `secret` across `configs/`, `src/`,
  `scripts/` returned nothing.
- No absolute developer paths in any config.
- No unsafe shell or network behaviour: `subprocess.run` is always called with a
  list argument, never `shell=True`.
- `pyproject.toml` is minimal and correct; neural backends are optional extras.

---

## 40. Test / static-check results

| check | command | result |
|---|---|---|
| unit + integration | `python -m pytest -q` | **2966 passed, 3 skipped, 35.32s** |
| static | `python -m pyflakes src/ tests/ scripts/` | **clean, exit 0** |
| benchmark immutability | `git diff -- benchmark/` | **empty** |
| working tree | `git status --short` | **clean** (before this file) |
| row counts | `load_all_splits()` | train 477, val 478, test 477 |
| runtime probe A | real `main()`, offline runtimes, M11–M19 on, 4 rows | telemetry emitted; **all six state features 0.0; `program_type="ProgramType.NUMERIC"`; `candidates_added`/`redundancy`/`prompt_tokens` empty; `operation_id` contains `id()`** |
| runtime probe B | fail → resume → fail → resume, 4 rows | **artifacts split across two run directories**; manifest claims 4 rows in a directory holding 2 |
| runtime probe C | attribute check on `CoverageGapState` | `coverage_gap` False, `gap` False, `residual` True |
| runtime probe D | `str(ResidualComponentName.NOVELTY_RATE)` on py3.14.5 | `'ResidualComponentName.NOVELTY_RATE'` ≠ `'novelty_rate'` |

All probes ran in the scratchpad. No repository file was written by any probe.

---

## 41. Findings table

| ID | Sev | File / symbol | Finding | Impact | Proposal conflict | Minimal fix |
|---|---|---|---|---|---|---|
| F-01 | **P1** | `configs/experiments/*.yaml`; `run_train_calibration_collection.py:249` | No committed config declares `split: train`, and every config disables M11–M21 | The runner refuses to start; if the split alone were fixed it would run to "COMPLETE / PASS" with an empty telemetry file | §16/§17 require TRAIN observation of the real action space | Commit a `configs/experiments/cover_kbc_v2_train_collection.yaml` with `split: train` and `parametric_retrieval`, all four specialists, `consensus`, `specialist_verifier`, `bidirectional_verification`, `layer4_integration`, `coverage_gap` enabled; M20/M21/L6 left disabled |
| F-02 | **P0** | `run_train_calibration_collection.py:145,150-153` `_state_features` | Reads `state.coverage_gap`/`state.gap`, neither of which exists on `CoverageGapState` ⇒ immediate `break` ⇒ all six §15 features 0.0. Second bug: `str(component.name)` yields `'ResidualComponentName.X'`, never the key | M19's residual is never recorded; `ΔR ≡ 0`; `state_bin_key` collapses to one degenerate bin; **no M21 bin can be built** | §15, §17 (`ΔR̂`, "relation-specific historical bins") | Read `state.residual`; use `component.name.value`; drop the inert `signal` fallback |
| F-03 | **P0** | `run_train_calibration_collection.py:355-379` | `pre_state` and `post_state` are both computed *after the row finishes*; `candidates_added/supported/contradicted`, `redundancy`, `verifier_outcome`, `prompt_tokens`, `cache_hits`, `target_class`, `action_id`, `reserved_class` are never populated | 4 of 6 §17 estimates and the successor distribution are underivable ⇒ **a second full TRAIN inference would be required** | §17 (all six estimates), §16 (per-class caps/reserves) | Have `execute_action` return the per-action state snapshot and candidate delta, and pass them through; populate the outcome fields from the M17/M18 results already in hand |
| F-04 | P2 | `pipeline.py:2007` | Unconditional `break` caps execution at one M17 + one M18 action per query; the `while` loop and its bookkeeping are dead | Every bin is populated from round-1 states only; §17's successor statistics have zero support even after F-02/F-03 are fixed | §22 Algorithm 1's action loop; §17's 1–2 step lookahead | Replace the `break` with a bounded round limit driven by the selector returning `()` |
| F-05 | **P1** | `run_train_calibration_collection.py:279,289,322` | Resume resolves `out_dir` from the checkpoint but writes a *fresh* `run_id` back into it; the second resume writes to a directory that never existed | Artifacts split across directories; coverage ledger silently restarts; final manifest over-reports rows relative to the file it sits beside | durability of a multi-hour 477-row run | Reassign `run_id = restored.counters["run_id"]` on resume, so identity and directory stay bound |
| F-06 | P2 | `run_train_calibration_collection.py:407-408`; `models/base.py:236` | `prompt_tokens` is never measured; no runtime carries a cumulative prompt-token counter | Live progress, `accounting.json` and telemetry all report 0 prompt tokens; M20's token budget has no observational basis | §16 budget accounting; §23.3 metrics | Add `prompt_tokens` to `LMRuntime` (incremented from `LabelScoreResult`/`GenerationResult`), surface it in `physical_snapshot`/`physical_delta`, charge it |
| F-07 | P2 | `run_train_calibration_collection.py:195,330-441`; `consensus.py:857` | `build_consensus_engine` is called without `relations`/`available_specialists` (guard vacuous); the runner has no per-query exception handling | One malformed row aborts all 477 rows and requires a resume — which is itself unsafe (F-05) | — | Pass the run's relations and specialist availability; catch per-row exceptions, record the row in `failed`, and continue |
| F-08 | P3 | `run_train_calibration_collection.py:313,447` | `failed` is never appended to | `rows_failed` is always 0 in progress and manifest | §21 truthful accounting | Append the failing `row_index` in the exception handler |
| F-09 | P2 | `controller_calibration/readiness.py:102` | `evaluate_readiness` has zero production callers | The gate that would have caught F-01 exists and is never consulted | — | Call it at runner start; refuse unless `may_run_collection` |
| F-10 | P2 | `collection_policy.py:214`; runner | `note_families()` is never called, so a family never *offered* never enters the ledger | `integrity_ok()` returns PASS for a collection that executed one family and zero M17 actions (observed) | §17 "estimate the value of every action family" | Declare the full `ActionFamily` vocabulary via `note_families()` at run start |
| F-11 | P2 | every module config `mode:`; `IntegrationMode` | Two different concepts share the name "mode"; module `mode:` is hard-pinned to `"shadow"` and cannot express production | Collection reads `mode: shadow` everywhere while genuinely mutating; a calibrated validation run cannot be configured without a code change | §4 layer semantics | Rename the per-module key (e.g. `observability_mode`), or admit `production` where the pipeline's `IntegrationMode` governs |
| F-12 | P2 | `run_train_calibration_collection.py:362` | `operation_id` embeds `id(action)` (memory address) | Non-reproducible identities; offline joins on `operation_id` impossible; duplicate guard theoretically defeatable after GC | §34 determinism | Use the owner's published `action_id` from `m17_actions`/`m18_actions` |
| F-13 | **P1** | `run_train_calibration_collection.py:359` | `program_type=str(enum)` yields `"ProgramType.NUMERIC"` | Derived bins carry a `program_type` that can never match `PlannerStateSnapshot.program_type` at lookup ⇒ every lookup raises `PlannerError` | §17 bin lookup | Use `pipeline._program_type_value(graph.contract)` (already exists for this exact reason) |
| F-14 | P3 | `pipeline.py:1324-1338` `_planned_neural_cost` | Assumes one call per template; `_score_sequence` charges one per label | M7's hard-cap guard would under-plan on a tokenizer whose A/B/C are multi-token | §16 "no action may exceed the hard cap" | Multiply by `len(labels)` when `inspect_labels(...).single_token` is False |
| F-15 | P3 | `production_bridge.py:265` | Calls `graph._attach`, a private method, across module boundaries | Undeclared coupling | — | Promote a narrow public `attach_structural_evidence` on `EvidenceGraph` |
| F-16 | P3 | `coverage_gap/missingness.py:508-548` | `R_t` renormalises weights over available components instead of `Σ wᵢ·cᵢ` | With uniform weights `R_t` is the mean, not the sum | §15 literal formula | No code change needed; state the instantiation in the paper |
| F-17 | P3 | `pipeline.py:2035` | `mean_inclusion_uncertainty` called without `self.config.scoring` | `H` uses `DEFAULT_SCORING.optional_views_available`; identical under the target config, divergent for a mandatory-only run | §17 `ΔĤ` consistency | Pass `self.config.scoring` |
| F-18 | P3 | `pipeline.py:1058`, `:1884` | `RelationBudgetScheduler.schedule` runs twice per query in production | Duplicate entries in `relation_budget_results` | — | Reuse the enumerate-phase result in `_budget_ledger_for` |
| F-19 | P3 | `telemetry.py:290`; runner `:316` | Records written after a resume carry the new `run_id` alongside older records in one file | `run_id` is not a stable grouping key for offline derivation | §34 reproducibility | Follows from the F-05 fix |
| F-20 | P3 | `run_train_calibration_collection.py:340` | One `[round=1]` line per row, printed before `decide_graph`; real M17/M18 rounds emit none | Round progress is not actually reported | §26 live progress | Emit `round_line` per `pipeline.action_records` entry |
| F-21 | P3 | `pipeline.py:1997` | `id(other) is not id(action)` compares two distinct `int` objects with `is` | No-op clause; saved only by the following `and` | — | Delete the clause |
| F-22 | P3 | `pipeline.py:345`; all runners | `layer6_integrator` is never supplied by any entrypoint | `Layer6Integrator`/`collect_catalog` and the M7/M11/specialist catalogue projections are unreachable | §17 "full state + legal actions" | Wire it in the production runner when M20/M21 are calibrated |
| F-23 | P3 | `pipeline.py:537,1343,2302` | `has_second_model`, `_minimum_neural_cost`, `integrate_layer4` have zero callers | Maintenance noise | — | Remove |
| F-24 | P2 | `run_cover.py:149`, `run_staged.py:850`, `real_model_smoke.py:424`, `architecture_smoke.py:123` | No runner ever passes `IntegrationMode.PRODUCTION`; `IntegrationMode.PRODUCTION` appears only in tests | There is no executable calibrated-production path; the "collection matches production" claim cannot be exercised end to end | §4, §22 | Add the production wiring in the post-calibration milestone |

---

## 42. P0 findings

- **F-02** — M19's residual and all five §15 components are never recorded;
  the runner reads attributes that do not exist and, even if fixed, would match
  component names against the wrong string form.
- **F-03** — per-action pre/post state are the same post-row snapshot, and every
  gain/redundancy/FP/token/class field in `ActionOutcome` and
  `ActionTelemetryRecord` is left at its default.

Together these mean the collection output cannot support **four of the six §17
estimates**, cannot support the successor distribution, and cannot support the
state binning at all. This is precisely the condition §35 says must produce
BLOCKED: discovering it after the run would force another full TRAIN inference.

---

## 43. P1 findings

- **F-01** — no runnable collection config exists; the run either refuses or
  produces an empty telemetry file while reporting success.
- **F-05** — the second and subsequent `--resume` write into a directory that
  was never used, splitting artifacts and over-reporting completion.
- **F-13** — `program_type` is emitted in Python `repr` form, so any bin derived
  from this telemetry can never be looked up.

---

## 44. P2 findings

F-04 (one action per catalogue per query), F-06 (prompt tokens never measured),
F-07 (vacuous consensus guard + no per-row error containment), F-09 (readiness
gate unwired), F-10 (coverage gate blind to never-offered families), F-11
(overloaded `mode` vocabulary), F-12 (non-deterministic `operation_id`), F-24
(no production entrypoint).

None of these alone invalidates the method, but F-04, F-06, F-10 and F-12 each
degrade the calibration the collection exists to enable, and F-07 turns any
single bad row into a full-run abort that must then be resumed through F-05.

---

## 45. P3 findings

F-08, F-14 – F-23. Maintainability, documentation and latent-hazard items with
no current semantic effect on the collection. They do not threaten the run and
do not threaten later calibration:

- F-16 and F-17 are documented instantiation choices that are behaviourally
  identical under the target configuration.
- F-14 is unreachable with the current profile's verified single-token labels.
- F-15, F-18, F-21, F-22, F-23 are structural tidiness with no behavioural
  effect.
- F-19 and F-20 are reporting-quality issues that resolve with the F-05 fix and
  a one-line logging addition.

---

## 46. Exact blockers before the full 477-row TRAIN collection

1. **F-02** — fix `_state_features` to read `state.residual` and
   `component.name.value`, and prove with a test that a real record carries a
   non-zero residual and non-zero components.
2. **F-03** — record the genuine per-action pre-state and post-state, and
   populate `candidates_added`, `candidates_supported`,
   `candidates_contradicted`, `redundancy`, `verifier_outcome`, `target_class`,
   `action_id`, `reserved_class` and `prompt_tokens`.
3. **F-13** — emit the canonical `program_type` value via
   `_program_type_value`.
4. **F-01** — commit an unambiguous TRAIN collection config with `split: train`
   and M11–M19 enabled (M20/M21/L6 remaining disabled), and make the runner
   refuse to start unless `evaluate_readiness` returns
   `may_run_collection` (F-09).
5. **F-05** — bind `run_id` to the resumed run so that repeated resumes write to
   one directory, and add a two-resume test.

Strongly recommended in the same pass, because each one otherwise degrades the
resulting bins and would be discovered only after the run:
**F-04** (more than one action per query), **F-10** (declare the family
vocabulary so the coverage gate can fail), **F-12** (deterministic
`operation_id`), **F-06** (prompt-token accounting), **F-07** (per-row error
containment).

---

## 47. Exact blockers before FULL VALIDATION

These are **not** TRAIN blockers, but none of them can be skipped later:

1. Real TRAIN-derived M20 (`RelationBudgetCalibration`, `TRAIN_CALIBRATED`) and
   M21 (`HistoricalBinPackage` + `PlannerCalibration`) artifacts must exist.
   None exists today.
2. **F-24** — a production entrypoint that constructs the pipeline with
   `IntegrationMode.PRODUCTION`. No script does today.
3. **F-11** — module configs cannot express `production`;
   `MicroPlannerConfig.__init__` and `RelationBudgetConfig.__init__` raise for
   any `mode != "shadow"` (`micro_planner.py:472`, `relation_budget.py:341`).
4. **F-22** — `layer6_integrator` must be supplied, or M21 receives an empty
   legal-action list and always returns `STOP/NO_LEGAL_ACTION`.
5. `evaluate_readiness` must gate the validation runner and return
   `FULL_VALIDATION_READY`.
6. A validation config with `split: val`, M11–M21 enabled and both calibration
   artifacts declared — which is a different file from the TRAIN collection
   config.

---

## 48. Epistemic evidence levels

| claim | evidence |
|---|---|
| Telemetry state block is all-zero | **Executed** — real `main()` run, record read off disk; plus direct `hasattr` probe on `CoverageGapState` |
| `str(enum)` mismatch | **Executed** — verified on Python 3.14.5 for both `ResidualComponentName` and `ProgramType` |
| Multi-resume splits artifacts | **Executed** — three sequential `main()` invocations, directory listing captured |
| No runnable config | **Read + executed** — all 8 configs read; `require_split` traced; the only correct shape found is a test-local fixture |
| Coverage gate reports PASS on a near-empty collection | **Executed** — observed in probe A |
| M8 sole ownership | **Static, exhaustive** — repository-wide grep for every `ObjectEntities`/`object_entities` write site |
| Closed-book, no-training, ≤32B | **Static, exhaustive** — repository-wide greps plus config arithmetic |
| TRAIN gold isolation | **Static, exhaustive** — `Query` has three fields; traced every call from `load_dataset` |
| §12.1 `q_g = max`, §17 utility, §14 mechanisms, §10.3 null classes | **Read in full** — source compared line by line against the proposal |
| M12/M13/M15 internals | **Read in substantial part** — types, registries and key invariants read; full 1400-line specialist bodies sampled at the invariant sites |
| M20/M21 gating in a calibrated production run | **Not executed** — no such path exists to execute; conclusions are static |
| Real-weight behaviour | **Not executed** — prohibited by the review scope |

---

## 49. Final verdict

> ## BLOCKED — REPOSITORY REQUIRES FIXES BEFORE TRAIN

The architecture is genuinely strong. M0–M19 are implemented to a high standard
and are, on inspection, proposal-conformant in the places that are hardest to
get right: `q_g = max` rather than a sum, `UNKNOWN ≠ contradiction`, failed
recall structurally incapable of becoming null evidence, `ALTERNATE_RECOVERED`
signing nothing, candidate-free recall unable to mint a candidate, controls
unable to become factual evidence, a single auditable production write seam,
M8 as the sole output owner, exact model identity and a correct 28.67B budget,
verified closed-book and verified no-training. 2966 tests pass and pyflakes is
clean.

The blocker is not the architecture. It is the **thin layer that turns that
architecture into a calibration dataset**, and it fails in a way no existing
test can see:

- the collection cannot be launched at all with any committed configuration; and
- if it were launched, the telemetry it produced would record none of Module
  19's state, no per-action state transition, no candidate effect, no
  redundancy, no token cost and a `program_type` in a form that can never be
  looked up — leaving two of six §17 estimates derivable and the state binning
  impossible.

A 477-row A100 session run against today's HEAD would end with a file that looks
complete, a run that exits 0, a coverage table that prints PASS, and a
calibration milestone that discovers it must run the whole thing again.

Fix F-01, F-02, F-03, F-05 and F-13 — and preferably F-04, F-06, F-07, F-09,
F-10 and F-12 in the same pass — add the four missing tests named in §34, and
re-audit. The distance to GO is small and entirely in the runner.

`FULL_VALIDATION_READY` is **not** claimed and must not be: no real
TRAIN-derived M20 or M21 artifact exists.

---

*Independent review. No production code, configuration, test, notebook,
benchmark file or existing audit was modified. `git status` shows this file as
the only addition.*
