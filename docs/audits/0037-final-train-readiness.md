# Audit 0037 — Final TRAIN Readiness

**Verdict: BLOCKED — NOT READY FOR TRAIN**

One of Audit 0036's four blockers is closed. Three remain.

---

## 1. Scope

Working tree on HEAD `872bc8aa14aae9bfca9db9709f46c6146add436c`. Verified from
executable source and live runs. `pytest`: 2928 passed, 3 skipped. `pyflakes`:
clean. `git diff -- benchmark/`: empty.

## 2. Proposal sections reviewed

§2.3, §9.3, §15, §16 + Table 6, §17. §17's `ΔĤ` term is what drives the
entropy work in §15–17 below; §9.3's hard verification reservation is what the
Module 20 ledger now enforces.

## 3. M0–M21 status

M0–M19 implemented and production-wired (Audits 0035/0036, re-verified).
**M20 implemented and now genuinely gating.** **M21 implemented, still
shadow-only.**

## 4–5. Traces

Production: `enumerate_query` → M9/M10 → M2 + M11 + M12–M15 → `decide_graph` →
`_run_consensus` → M16 → catalogue → `_execute_selected_verifications` →
per action: **`_precharge` (M20)** → `execute_action` → `_integrate_layer4` →
`ProductionEvidenceBridge.apply` → `_estimate_coverage_gap` (M19) → next round
→ `finalize` (M8).

Collection: identical, except selection comes from `TrainCollectionPolicy` and
`_precharge` returns admitted unconditionally (no calibration exists yet).

## 6–9. M20 — **FIXED**

Call site: `pipeline.py::_precharge`, invoked by `execute_action` before any

runtime is touched.

Method invoked: the real `RelationBudgetScheduler.schedule(subject, relation,
row_index, program_type, profile, budget)` → `RelationBudgetResult.plan` →
`BudgetLedger(plan).reserve(descriptor)`. The descriptor comes from the owner's
own `action_catalog` projections (`m17_actions` / `m18_actions`), so spend
class, reserve purpose and sub-call plan are the owner's declaration.

One ledger per query, cached (`test_one_ledger_per_query_so_caps_bind`) — a
fresh ledger per action would make every reserve succeed and the caps
meaningless.

Proof of ordering: `test_precharge_precedes_execution`.
Proof refusal costs nothing: `test_a_starved_budget_prevents_every_neural_call`
asserts on the physical counter, not on a return value.
Proof it is not a stub: `test_the_pipeline_calls_the_real_scheduler` spies the
canonical method.
`test_synthetic_calibration_is_refused_by_shipped_configuration` confirms
`load_calibrations` still refuses `SYNTHETIC_TEST` by default.

**Two real defects were found and fixed while wiring this:** the projections
return `(candidates, exclusions)` and were being indexed as a flat list; and
`str(ProgramType.SMALL_SET)` yields `"ProgramType.SMALL_SET"`, which does not
match the risk profile's `"SMALL_SET"` and made Module 20 reject every
schedule. Both were caught by the tests asserting on counters.

## 10–11. M21 → M7 — **NOT SATISFIED**

`MicroPlanner.plan` is still invoked only at the shadow diagnostic site
(`_plan_micro_action`). No production path routes its decision into
`execute_action`. Utility, strict `> τ_continue` and the lookahead bound are
untouched but unreached.

## 12–13. Collection separation — CONFIRMED

`test_collection_never_invokes_the_planner` spies `micro_planner.plan` during a
collection run and asserts zero calls, so the bootstrap circularity is avoided.
`test_collection_and_production_share_the_executor` asserts both modes resolve
to the identical `CoverPipeline.execute_action` function object — the two
diverge only at selection.

## 14. M19 refresh — CONFIRMED

`execute_action` recomputes Layer 4, applies the bridge, then calls
`_estimate_coverage_gap`, per action, in that order.

## 15–17. Authoritative H — **TRACED, NOT WIRED**

The canonical uncertainty quantity is `scoring.py`'s
`H_inc(o) = -q log q - (1-q) log(1-q)`, the binary entropy of coverage.
`historical_bins.py` corroborates that this is the intended single definition:
*"M21 never recomputes an entropy of its own"*, with `expected_delta_h` declared
in uncertainty points on `[-1, 1]`.

`ControlStateFeatures.entropy` exists and `ActionTelemetryRecord.delta_entropy`
computes `H_before - H_after`, but the runner never populates `entropy`, so it
is uniformly `0.0` and **ΔĤ remains underivable.** No second entropy estimator
was created; the remaining work is to expose the existing `scoring.py` quantity
at the snapshot boundary.

## 18–19. R and ΔR — CONFIRMED

Read from Module 19's own `CoverageGapComponents`. `ΔR = R_before - R_after`
per executed action, recoverable from the record.

## 20–21. Per-action cost and role partition — CONFIRMED (unchanged)

`physical_delta(pre, post)` over `physical_snapshot()`, reading
`runtime.calls` / `verifier_runtime.calls`. Raises if the partition fails to sum
or a counter moves backwards. Live: M17 round 1 = 8 calls, M18 round 2 = 1,
unselected = 0; accounting 30 = 30 + 0 on the single-role offline fixture.

## 22–24. Exactly-once, rounds, legal-but-unselected — CONFIRMED

Unchanged from Audit 0036 and still asserted.

## 25–26. Mid-row resume — **NOT SATISFIED**

Unchanged from Audit 0036. Telemetry flushes per action; the checkpoint marks
completion per row. A fatal failure mid-row leaves that row's already-written
action records on disk while the checkpoint still considers the row incomplete,
so a resume replays it and duplicates them. No row-transaction boundary was
implemented, and no forced-failure resume test exists.

## 27–31. Gold isolation, CLI, progress, durability, artifacts — CONFIRMED

Gold absent from telemetry (checked programmatically). Flags `--config`,
`--output-dir`, `--limit`, `--resume`. `[TRAIN n/N]` and `[TRAIN n/N][round=k]`
observed. OOM path exercised, reports `quality_profile: unchanged`, exits
non-zero. Artifacts: `predictions.jsonl`, `train_telemetry.jsonl`,
`accounting.json`, `action_coverage.json`, `manifest.json`, `checkpoint.json`.

## 32–33. Readiness

Collection: not ready (blockers below). Full validation: fail-closed, and real
M20/M21 artifacts still do not exist.

## 34–36. Benchmark / pytest / pyflakes

Untouched. 2928 passed, 3 skipped. Clean.

## 37. Remaining blockers before the 477-row collection

1. **M21 → M7 unwired.**
2. **H not populated**, so ΔĤ is underivable.
3. **Mid-row resume can duplicate telemetry.**

## 38. Remaining blockers before FULL VALIDATION

All of the above, plus offline M20/M21 derivation from real telemetry, freezing
those artifacts, and pointing the full config at them.

## 39. Epistemic status

- **Real-weight smoked:** nothing. Audit 0034 remains the last real-weight
  evidence and predates every seam in Audits 0035–0037.
- **Pipeline-tested (scripted, offline):** M20 gating, action seam, per-action
  cost, role partition, rounds, shared executor, collection-avoids-M21.
- **Scripted collection-tested:** the CLI, 3 rows, offline null runtime.
- **Not run at scale:** the 477-row collection. TRAIN has not run.

## 40. Verdict

**BLOCKED — NOT READY FOR TRAIN.**

Module 20 now genuinely gates neural execution, which was the largest of the
four. But Module 21 still does not reach Module 7, ΔĤ cannot be derived from the
telemetry, and a mid-row failure can corrupt it on resume — so a 477-row session
would still not produce a calibratable dataset.
