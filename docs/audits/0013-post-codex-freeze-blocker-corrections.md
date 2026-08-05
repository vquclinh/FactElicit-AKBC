# Audit 0013 — Post-Codex Freeze-Blocker Corrections

Status: **PASS — ARCHITECTURE FREEZE READY**
Date: 2026-08-05
Reviewed commit: `814812ff46713aa145dc8b95fcd6ef38118929df`
Independent verdict on that commit: **FAIL — FREEZE BLOCKED**

---

## 1. Objective

Correct the integration defects an independent Codex review confirmed against
commit `814812f`, and nothing else. Audit 0012 is preserved unchanged as the
historical freeze-candidate audit for that commit; this audit records what the
review found, that each finding was reproduced first, and what changed.

No architecture was redesigned. No confirmed subsystem was refactored for style.

---

## 2. Reviewed commit and 3. verdict

`814812ff46713aa145dc8b95fcd6ef38118929df` — "refactor: freeze COVER-KBC
end-to-end architecture". HEAD matched exactly at the start of this work, with a
clean tree.

The review returned **FAIL — FREEZE BLOCKED** with two BLOCKERs, three MAJORs,
one MODERATE and three MINORs. All nine are addressed here.

---

## 4. Pre-fix repository state

```
$ git rev-parse HEAD   -> 814812ff46713aa145dc8b95fcd6ef38118929df
$ git status --short   -> (clean)
```

948 tests passing, 3 skipped. Every finding below was **reproduced against this
exact state before any code was changed.**

---

## 5. Codex findings reproduced

| Finding | Severity | Pre-fix reproduction |
|---|---|---|
| Staged Phase-B verifier role | **BLOCKER** | Qwen passed as both `runtime` and `verifier_runtime` → `has_second_model=False`, `_gate_deferred()=True`, cross-model unreachable |
| Hard call overrun | **BLOCKER** | 1 call left; calibrated VERIFY made 2 real calls; finished at **`calls_used = 5/4`** |
| Fixed-path stale budget | MAJOR | 5 real runtime calls, `prediction.calls_used = 4` |
| `gate_result` lost in staging | MAJOR | `graph_from_json` never assigned `gate_result`; round-trip → `None` |
| `run_cover.py` → NullRuntime | MAJOR | frozen target profile has no top-level `backend` → `build_runtime` returned `NullRuntime(offline/null)` |
| `phase_decide` completeness | MODERATE | `expected_queries` built *from the predictions themselves* |
| pyflakes | MINOR | four unused `_bootstrap` imports |
| stale numeric comment | MINOR | `contracts/base.py` still described single-linkage |
| stale calibration prose | MINOR | `calibration.py` said "six degrees of freedom" |

---

## 6. Root-cause summary

Five of the six substantive defects share one root cause: **a logical property
was inferred from an incidental runtime fact.**

- verifier capability inferred from *object/id inequality* rather than the
  configured role;
- action cost inferred from *action type* rather than the real cache state;
- budget inferred from a *fixed 1-per-action* rather than measured invocations;
- model profile inferred from a *flat shape* rather than the canonical resolver;
- output completeness inferred from *the output itself* rather than the input.

The corrections all move the same way: measure or declare the thing, rather than
guess it from a coincidence.

---

## 7. Model-role availability correction (BLOCKER 1)

`has_second_model` conflated three questions. They are now separate:

| property | question | basis |
|---|---|---|
| `verifier_available` | can verifier-role scoring execute now? | the bound runtime supports logits **and** genuinely fills the role — a distinct object, or its id/role matches the configured verifier |
| `cross_model_recall_available` | is Qwen recall genuinely heterogeneous? | configured **enumerator model id** ≠ configured **verifier model id**, and the verifier is available |
| object identity | must I read two counters? | `verifier_runtime is not runtime` — the only place identity is the right question |

`PipelineConfig` gained `enumerator_model_id` / `verifier_model_id`, set by both
runners from the canonical resolver. Capability is now a property of the
*architecture declaration*, not of which objects are resident.

`has_second_model` remains as a documented deprecated alias so older call sites
keep working.

Post-fix, with Qwen loaded as both (exactly Phase B):

```
verifier_available           : True
gate deferred                : False
cross_model_recall_available : True
```

A bare enumerator standing in for an absent verifier is still **not** the
capability — the two tests that caught that during this work are kept.

---

## 8. Deferred gate correction

`_gate_deferred()` now asks `not verifier_available`. Once Phase B has Qwen the
gate executes, and there is no residency-based substitution: `_gate_runtime()`
raises `GateRoleUnavailable` rather than silently using Mistral.

Staged death smoke, both branches, gate scored by **Qwen**:

| scenario | decision | `p_no` | `gate_negative` |
|---|---|---|---|
| confident negative | `NO` | 0.98 | True |
| uncertain | `UNKNOWN` | 0.33 | False |

UNKNOWN stays UNKNOWN. A target staged query no longer finishes with
`gate_deferred and gate_result is None`.

---

## 9. Cross-model reachability correction

Reachability is judged against the configured enumerator, so Qwen recall in
Phase B is correctly heterogeneous relative to Mistral even though one runtime
object exists at that moment. The target config was **not** changed, and
cross-model recall remains optional and never required for stopping.

Evidence semantics are untouched: independent recall stays
`CROSS_MODEL_RECALL` + `INDEPENDENT_RECALL` → `X`; shown-candidate verification
stays `BLIND_VERIFIER` + `SHOWN_CANDIDATE` → `L`. Nothing in this fix touches
the accounting.

Visible effect — the production CLI now performs genuine multi-swap execution
where before Phase B was a dead end:

```
[PHASE A] enumerate → [PHASE B] verify → [RESUME 1] enumerator
→ [RESUME 2] verifier → [RESUME 3] enumerator → [RESUME 4] verifier → [PHASE C]

RUN_VIEW ×3 → REVERSE_CHECK → ADVERSARIAL_VERIFY → RUN_VIEW
→ CROSS_MODEL_CHECK → REVERSE_CHECK → RUN_FACET → ADVERSARIAL_VERIFY
```

---

## 10. Staged / interleaved semantics

Physical residency may differ; logical capability may not. The six-relation
equivalence suite still passes, now with capability derived from configuration
rather than object count.

---

## 11. Hard neural-call budget correction (BLOCKER 2)

`_minimum_neural_cost` was a floor that **excluded calibration controls**, so a
1-call remainder authorised a 2-call verification.

Replaced by `_planned_neural_cost`: exact, given the *current* cache state.

| action | planned cost |
|---|---|
| ordinary view | 1 (or `runs`) |
| description-first view | **2** |
| VERIFY, cold control | **2** = score + control |
| VERIFY, warm control | **1** |
| ADVERSARIAL_VERIFY, cold | **2 × templates** |
| STOP | 0 |

Post-fix at 1 call remaining: planned cost 2 → **refused**; after the control is
cached, planned cost 1 → **allowed**. No overrun in either case.

None of the forbidden repairs was used: `max_calls` was not raised, no counter is
clipped, no call is made and then reported as exhausted, controls are not
excluded, and nothing charges logical actions.

---

## 12. Cache-aware calibration budgeting

`ContextualCalibrator.control_calls_needed(runtime, contract, templates)` reports
how many *new* control measurements a set of templates would cost, using the
same cache identity Module 4 already defines (model, revision, label signature,
relation, template, decode identity). Cached → 0. Module 4's calibration
mathematics is unchanged.

---

## 13. Runtime-level safety net

`Budget.reserve(calls)` charges *before* a call and raises `BudgetExceeded` if it
would cross the ceiling, so the hard rule does not depend on a prediction staying
exact. It changes no model output and no verification mathematics.

```
3/4 used, reserve(2) -> BudgetExceeded: would take this query to 5 of 4
3/4 used, reserve(1) -> allowed, calls_used = 4
4/4 used, reserve(1) -> BudgetExceeded
```

`Budget.charge` also rejects negative charges.

---

## 14. Fixed-ablation budget correction (MAJOR 1)

Two under-charges existed, both from assuming a cost rather than measuring it:

1. `verify_graph`'s fixed path (`_verify_pending`) charged nothing at all;
2. the fixed enumeration loop charged `calls=1` per view, so a description-first
   view's second generation was free.

Both now measure `_total_runtime_calls()` across the resident runtimes and fold
the difference into `graph.budget_snapshot`, which is the single authoritative
figure `decide_graph` reads. No mode has separate budget semantics.

---

## 15. Exact runtime-call reconciliation — all four modes

| mode | actual runtime calls | charged | exact |
|---|---|---|---|
| fixed interleaved | 5 | 5 | ✓ |
| active interleaved | 4 | 4 | ✓ |
| active staged | 4 | 4 | ✓ |
| fixed staged | 5 | 5 | ✓ |

Equality, not `charged <= actual`. Also asserted per relation across the staged
role-swap loop, together with `calls_used <= max_calls`.

---

## 16. `gate_result` persistence (MAJOR 2)

`GateResult.from_json` added; `graph_from_json` restores it. Round-trip is
lossless across every field — question, all three probabilities, raw/calibrated/
bias logits, `calibrated`, margin, entropy, decision, `model_id`. Stage schema
bumped **5 → 6**, so an older payload fails clearly.

A confident negative survives a role swap with `gate_negative` intact; an
UNKNOWN comes back scored, not unscored.

---

## 17. `run_cover.py` and the runtime registry (MAJOR 3)

`build_runtime` now **fails closed**: a profile with no `backend` key raises,
naming the canonical resolver. `backend: null` still yields `NullRuntime`, so
explicit stub requests and scripted smoke configs are unaffected.

`model_blocks` moved into `cover_kbc.models.registry` as the single canonical
profile resolver, accepting both the flat and the nested (`enumerator`/
`verifier`) shapes. Both runners use it, and both now also declare the logical
role ids onto `PipelineConfig`.

`run_cover.py` additionally hand-rolled its `PipelineConfig` from four keys,
ignoring the rest — a second divergence found while fixing the first. It now
uses `PipelineConfig.from_mapping`, forces `INTERLEAVED` (the staged seam is the
other runner's job), and passes the verifier runtime.

---

## 18. Canonical production CLI

**`scripts/run_staged.py all --config <target>`** is the canonical production
path. `run_cover.py` is retained as the *interleaved* entry point for the same
configs, using the same resolver, the same `PipelineConfig` construction and the
same runtimes. There is no path where one runner uses real models and the other
silently uses a stub.

---

## 19. Query-manifest completeness correction (MODERATE)

Phase A now writes `query_manifest.json` recording the split, relation filter,
limit and the exact ordered query identities selected. `phase_decide` validates
predictions against **that manifest** via `_expected_queries`, failing on a
missing, unexpected or duplicated row. Only when no manifest exists (an older
run directory) does it fall back to the predictions, and it says so rather than
silently self-validating.

Tested: exact set passes; missing, extra and duplicate rows each raise.

---

## 20. Minor cleanups

- `scripts/_bootstrap.py` exposes `ensure_src_on_path()`, a called function
  rather than an import side effect. `python -m pyflakes src/ tests/ scripts/`
  now **exits 0** with nothing suppressed.
- `contracts/base.py` no longer describes single-linkage; it documents the
  diameter bound audit 0012 §30 froze. **Cluster geometry itself is unchanged** —
  Codex confirmed it.
- `calibration.py` says **five** decisions, matching the inventory. The
  five-decision design is not reopened.

---

## 21. Files modified

| File | Change |
|---|---|
| `src/cover_kbc/pipeline.py` | capability split, exact cost planning, measured charging in every path |
| `src/cover_kbc/types.py` | `Budget.reserve`, `BudgetExceeded` |
| `src/cover_kbc/verification.py` | `control_calls_needed`, `GateResult.from_json` |
| `src/cover_kbc/staging.py` | restore `gate_result`, schema 5 → 6 |
| `src/cover_kbc/models/registry.py` | canonical `model_blocks`, fail-closed `build_runtime` |
| `src/cover_kbc/contracts/base.py` | corrected clustering docs |
| `src/cover_kbc/calibration.py` | corrected decision count |
| `scripts/run_staged.py` | shared resolver, role ids, query manifest, completeness check |
| `scripts/run_cover.py` | canonical resolver and config path, verifier runtime |
| `scripts/_bootstrap.py` | explicit `ensure_src_on_path()` |
| `tests/test_post_codex_regressions.py` | **created** — 25 tests |
| `tests/test_system_e2e_conformance.py` | adversarial cost assertion follows exact planning |

`benchmark/` untouched. `docs/audits/0012-*` preserved unchanged.

---

## 22. Commands executed

```
git rev-parse HEAD ; git status --short
python3 -m pytest -q
python3 -m pytest tests/test_post_codex_regressions.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 3
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_roleswap.yaml --relation awardWonBy --limit 3
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

No model download, no GPU inference, no Colab, no threshold tuning.

---

## 23. Exact test results

**973 passed, 3 skipped, 0 failed, 0 xfailed** (up from 948).

| Suite | Tests |
|---|---|
| `test_post_codex_regressions.py` | **25** |
| `test_system_e2e_conformance.py` | 125 (2 skipped) |
| `test_rcse_conformance.py` | 86 |
| `test_controller_conformance.py` | 79 |
| `test_verifier_conformance.py` | 76 |
| `test_evidence_state_conformance.py` | 72 |
| `test_final_selector_conformance.py` | 63 (1 skipped) |
| `test_graph.py` | 59 |
| remaining suites | 388 |

Skips unchanged and still individually explained (audit 0012 §50).

---

## 24-26. Six-relation, staged/interleaved and fixed-mode results

All six relations still execute end to end under their typed programmes;
staged and interleaved still agree semantically for all six; the fixed ablation
remains fixed and now charges its verifier and description-first calls. The
ablation ladder is intact.

---

## 27. Parameter budget

```
Qwen/Qwen3.5-4B                                   4,659,865,088
mistralai/Mistral-Small-3.2-24B-Instruct-2506    24,011,361,280
total 28.67B   RESULT: PASS
```

---

## 28. Benchmark integrity

All three git checks empty. Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57`
preserved; no organizer file touched.

---

## 29. No-retrieval / no-training impact

None of these corrections adds a network call, a factual source or any training.
The registry change makes an accidental model-less run **fail** rather than
silently proceed, which strengthens compliance rather than relaxing it.

---

## 30. Unresolved findings

None of the reported findings remains open. No BLOCKER or MAJOR is outstanding.

Two small items were found *while* fixing and are recorded rather than hidden:

1. `run_cover.py` had a second divergence — a hand-rolled `PipelineConfig`
   ignoring most of the config block. Fixed in the same change (§17).
2. `cross_model_recall_available` initially ignored verifier availability, so a
   bare enumerator briefly reported the capability. Caught by the regression
   test written for it and fixed before completion.

---

## 31. Measurement-only items

Unchanged from audit 0012, and none is a code defect:

- real Qwen3.5-4B tokenisation of `A`/`B`/`C`;
- real contextual-calibration bias magnitudes;
- real Mistral candidate quality;
- real action yields and costs, which will replace the `COST`-category priors;
- calibration of the five `CALIBRATABLE` decisions on train/internal split.

**No real Mistral-24B / Qwen3.5-4B result exists. No val performance claim has
been made.** Every number here is from scripted fixtures.

---

## 32. Final verdict

**PASS — ARCHITECTURE FREEZE READY.**

Both blockers are closed at the root rather than patched: verifier capability is
a declared property of the architecture instead of an accident of object
identity, and the hard call ceiling is enforced by exact cache-aware planning
plus a reservation guard at the invocation boundary. All three majors are fixed
— every execution mode now charges one authoritative budget, `gate_result`
survives serialisation losslessly, and no entry point can silently run without a
model. Output completeness is checked against the queries the run actually
selected. pyflakes exits 0.

The verdict is not "tests are green": each finding was reproduced first, and
each fix is pinned by a regression test that fails against the reviewed commit.

This remains an **architecture** freeze. The measured configuration cannot
freeze until the §31 measurements exist.
