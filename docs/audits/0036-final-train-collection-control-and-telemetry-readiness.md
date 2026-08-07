# Audit 0036 — Final TRAIN Collection Control and Telemetry Readiness

**Verdict: BLOCKED — NOT READY FOR TRAIN**

Two of Audit 0035's four blockers are genuinely closed. One is closed in
structure but not in substance, and one is untouched.

---

## 1. Scope

Working tree on HEAD `872bc8aa14aae9bfca9db9709f46c6146add436c`. Verified from
executable source and live runs, not from the milestone brief. `pytest`: 2919
passed, 3 skipped. `pyflakes`: clean. `git diff -- benchmark/`: empty.

## 2. Proposal sections reviewed

§2.3, §9.3, §15, §16 + Table 6, §17. Unchanged from Audit 0035; §17's
"estimates come from relation-specific historical bins on TRAIN" is the
requirement that drives the per-action telemetry work below.

## 3. M0–M21 status

M0–M19 implemented and production-wired (Audit 0035, re-verified).
**M20 implemented, not gating.** **M21 implemented, shadow-only.**

## 4–5. Execution traces

Production and collection both run:
`enumerate_query` → M9/M10 → M2 + M11 + M12–M15 → `decide_graph` →
`_run_consensus` → M16 → catalogue → `_execute_selected_verifications` →
**`execute_action`** (per action) → `_integrate_layer4` →
`ProductionEvidenceBridge.apply` → `_estimate_coverage_gap` (M19) → next round
→ `finalize` (M8).

The two modes diverge only at selection: production uses the injected selector,
collection uses `TrainCollectionPolicy`. Everything after selection is shared.

## 6–7. M20 precharge — **NOT SATISFIED**

`pipeline.py::_precharge` exists and is called by `execute_action` before any
runtime is touched, and a refusal provably costs zero physical calls
(`test_a_refused_action_makes_no_runtime_call`).

**But it never consults Module 20.** `_precharge` probes for a `plan_for`
method; `RelationBudgetScheduler` exposes only `schedule`. The `getattr` returns
`None` and the function always returns admitted. The control flow is correct;


the integration is absent. Calling this "M20 wired" would be false.

## 8–9. M21 → M7 — **NOT SATISFIED**

`MicroPlanner.plan` is invoked only at the shadow diagnostic site
(`_plan_micro_action`). No production path routes its decision into
`execute_action`. `controller.py` still has no planner hook.

## 10–11. Collection does not use M21 — CONFIRMED

`test_collection_never_invokes_the_planner` spies on `micro_planner.plan` during
a collection run and asserts zero calls, so the bootstrap circularity is
structurally avoided. `test_collection_and_production_share_the_executor`
asserts both modes resolve to the identical `CoverPipeline.execute_action`
function object.

## 12. M19 refresh between rounds — CONFIRMED

`execute_action` recomputes Layer 4, applies the bridge, then calls
`_estimate_coverage_gap`, in that order, per action.

## 13. Per-action telemetry lifecycle — FIXED

Pre-snapshot → precharge → execute one action → integrate → refresh →
post-snapshot → emit. One record per action, carrying that action's own cost.
Observed live: M17 round 1 = 8 physical calls (matching the audited cold plan),
M18 round 2 = 1 call, unselected actions = 0.

## 14–15. ΔR / ΔH source

Read from Module 19's own `CoverageGapComponents` (`residual` plus the five
weighted components), never recomputed. A second residual implementation would
be a second coverage estimator.

**Gap:** `H` (entropy) is not currently populated by the runner, so `ΔĤ` cannot
yet be derived from this telemetry.

## 16. Per-action cost source — FIXED

`physical_delta(pre, post)` over `physical_snapshot()`, which reads
`runtime.calls` / `verifier_runtime.calls` directly. Measured, not estimated,
and not template cost. `test_whole_row_cost_is_never_assigned_to_one_action`
pins the Audit-0035 defect closed.

## 17. Role partition — FIXED

Read from the runtimes because Modules 14 and 15 use both models inside one
operation, so no call site can attribute their calls. `physical_delta` raises if
the partition fails to sum to the physical total, and raises if a counter moves
backwards. Verified on a live run: 30 = 30 + 0 (the offline fixture is a
single-role profile, reported honestly via `single_role_profile`).

## 18. Exactly-once — CONFIRMED

The runner consumes pipeline deltas rather than re-counting. Duplicate edge ids
are refused by the graph. `test_global_calls_equal_the_sum_of_action_deltas_plus_acquisition`
bounds action totals by the measured global total.

## 19. Legal-but-unselected — CONFIRMED

Logged with `executed=False`, `post_state=None`, zero cost. No fabricated ΔR,
ΔH or gain. Opportunity data stays distinguishable from observed outcomes.

## 20. Round indices — FIXED

Real and increasing per executed action; the all-at-round-1 behaviour is gone.
No fabricated round total is emitted.

## 21. TRAIN gold isolation — CONFIRMED

Queries carry subject/relation/row_index only. A live run's telemetry contains
no `ObjectEntities` field (checked programmatically, not by eye).

## 22. Checkpoint / resume — PARTIAL

Row-level boundary: a row is completed only after its actions, telemetry and
predictions are flushed. **Not addressed:** a fatal failure mid-row replays that
row from the start on resume, and the already-written telemetry records for its
completed actions are not removed — so a resumed run can contain duplicate
action records for the interrupted row. §16 of the brief explicitly forbids
this.

## 23. OOM / fatal durability — CONFIRMED (observed)

Exercised for real during this milestone: flushed state, named the failing
row/relation/subject and directory, reported `quality_profile: unchanged`,
exited non-zero.

## 24–26. CLI, progress, outputs — CONFIRMED

`--config`, `--output-dir`, `--limit`, `--resume`. `[TRAIN n/N]` and
`[TRAIN n/N][round=k]` observed. Outputs: `predictions.jsonl`,
`train_telemetry.jsonl`, `accounting.json`, `action_coverage.json`,
`manifest.json`, `checkpoint.json`.

## 27–30. Readiness / benchmark / pytest / pyflakes

`readiness.py` untouched; `FULL_VALIDATION_READY` still fail-closed.
`benchmark/` untouched. 2919 passed, 3 skipped. pyflakes clean.

## 31. Remaining blockers before the 477-row collection

1. **M20 does not gate execution.** `_precharge` must call the real
   `RelationBudgetScheduler.schedule` contract.
2. **M21 → M7 is unwired.**
3. **Entropy is not captured**, so `ΔĤ` is underivable from this telemetry.
4. **Resume can duplicate action records** for an interrupted row.

## 32. Remaining blockers before FULL VALIDATION

All of the above, plus offline M20/M21 derivation from real telemetry, freezing
those artifacts, and pointing the full config at them.

## Epistemic status

- **Real-weight smoked:** nothing here. Audit 0034 remains the only real-model
  evidence and predates every seam in this milestone.
- **Pipeline-tested (scripted, offline):** action seam, per-action cost, role
  partition, round indices, shared executor, collection-avoids-M21.
- **Scripted collection-tested:** the CLI, 3 rows, offline null runtime.
- **Not run at scale:** the 477-row collection. TRAIN has not run.

## Verdict

**BLOCKED — NOT READY FOR TRAIN.**

Blockers 3 and 4 are small. Blockers 1 and 2 are the same two seams Audit 0035
named, and they remain the reason a 477-row session would produce telemetry that
cannot calibrate the modules it exists to calibrate.
