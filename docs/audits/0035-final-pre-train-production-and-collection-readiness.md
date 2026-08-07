# Audit 0035 — Final Pre-TRAIN Production and Collection Readiness

**Verdict: BLOCKED — NOT READY FOR TRAIN**

Two of the three declared blockers are closed. The third — Module 20's budget
interface and the Module 21 → Module 7 control interface — is not implemented,
and §25 of the governing brief is explicit that a missing production seam makes
the verdict BLOCKED regardless of what else passes.

---

## 1. Scope and repo SHA

Working tree at HEAD `872bc8aa14aae9bfca9db9709f46c6146add436c`, plus uncommitted
changes audited here. Every claim below was checked against executable source,
not against prior reports. `pytest`: 2904 passed, 3 skipped. `pyflakes`: clean.

## 2. Proposal contract reviewed

`COVER_KBC_Technical_Proposal_New.pdf`. §2.3 (no training; deterministic
calibration permitted), §9.3 (budget decomposition, hard verification
reservation), §15 (`R_t` ensemble), §16 + Table 6 ("concrete values are
calibrated on TRAIN"), §17 (`U_t(a)`, strict `> τ_continue`, 1–2 step
lookahead, "estimates come from relation-specific historical bins on TRAIN").

## 3. Implementation status M0–M21

| Module | Implemented | Production-wired | Evidence |
|---|---|---|---|
| M0–M8 core | yes | yes | unchanged |
| M9/M10 | yes | yes | prompts |
| M11 | yes | yes (via consensus → Layer 4 → bridge) | accounting fixed this milestone |
| M12–M15 | yes | yes (same path) | accounting fixed this milestone |
| M16 | yes | yes — no longer decision-inert | bridge call site |
| M17 | yes | **yes — real caller added** | `_execute_selected_verifications` |
| M18 | yes | **yes — real caller added** | same, via `build_request` |
| M19 | yes | yes — recomputed post-bridge | ordering test |
| M20 | yes | **NO — not wired to ex



ecution** | blocker |
| M21 | yes | **NO — no M7 bridge** | blocker |

## 4. Real production execution trace

`enumerate_query` → M9/M10 → M2 + M11 + routed M12–M15 → `decide_graph` →
`_run_consensus` → M16 → catalogue M17/M18 → `_execute_selected_verifications`
(**real calls**) → `_integrate_layer4` → `ProductionEvidenceBridge.apply` →
`_estimate_coverage_gap` (M19) → `finalize` (M8).

**M20 and M21 do not appear in this trace.** That is the gap.

## 5–6. M17 / M18 real callers — CONFIRMED

`pipeline.py::_execute_selected_verifications` calls `verify_specialist_targets`
and `execute_bidirectional_checks`. Verified by pipeline-level tests that never
touch the executors directly (`test_m17_executes_through_the_pipeline_not_the_test`,
`test_m18_executes_through_the_pipeline_not_the_test`). M18 routes
`EligibleCheck → build_request → execute`, preserving the owner's profile check.

## 7. M19 → Layer 6 — PARTIAL

M19 is recomputed over bridged evidence, and source ordering is pinned by test.
But "reaches Layer 6" is only meaningful once Layer 6 acts on it, and M21 is not
wired. The residual reaches the telemetry record; it does not yet reach a
controller decision.

## 8. M20 constrains the real action path — **NOT SATISFIED**

`RelationBudgetConfig` remains correctly fail-closed without a
`calibration_file`, and `load_calibrations` still refuses `SYNTHETIC_TEST` in
production. But **nothing consults M20 before a neural action executes.**
`_execute_selected_verifications` calls the executors with no precharge. §2 of
the brief requires the execution path to consult M20 first; it does not.

## 9. M21 selection reaches M7 — **NOT SATISFIED**

`controller.py` still has no planner hook. Action choice comes from the injected
`action_selector`, not from `MicroPlanner`. The utility mathematics, strict
`> τ_continue` and lookahead bound are untouched but unreached.

## 10. M8 sole final owner — CONFIRMED

`finalize` → `select` → `graph.active_candidates()`. No second selector exists.

## 11. Mode behaviour — CONFIRMED

`IntegrationMode` normalized once in `CoverPipeline.__init__`. Shadow performs
zero production writes (`applied is False`, zero edges). Production and
collection use the identical seam; `test_collection_mode_uses_the_same_pipeline_seam`
asserts identical ObjectEntities. Invalid modes fail closed.

## 12. M11–M15 accounting — FIXED

All five call sites (`retrieval_results`, and the four specialist results) now
route through `_charge_calls`. Previously they billed `shadow_calls` even in
production.

## 13. Exactly-once accounting — CONFIRMED, with one gap

A physical call appears in exactly one ledger.
`test_acquisition_alone_is_billed_by_mode` proves identical inference with only
the ledger differing. `graph._attach` refuses duplicate edge ids, so a record
consumed by several layers bills once; re-applying an integrated state is a
no-op.

**Gap:** the collection runner charges all calls to `role="enumerate"`, so
`enumerator_calls` / `verifier_calls` is not a truthful partition. §12 of the
brief requires both separately.

## 14. Collection policy — CONFIRMED

`TrainCollectionPolicy` round-robins across families before repeating one, and
selects only catalogue entries. `_select_actions` raises `UnsupportedAction` if
a selector returns anything not in the catalogue.

## 15. TRAIN gold isolation — CONFIRMED

The runner builds queries from `dataset.queries()`, which carries only
subject/relation/row_index. Telemetry contains no `ObjectEntities` field
(verified: `grep -c` returns 0 on a real run). Gold is never read into
inference.

## 16. Telemetry completeness — PARTIAL

`train-telemetry-v1` has the fields M20/M21 derivation needs, and the writer
refuses duplicate identities. But the runner currently emits **one record per
catalogue action at `round_index=1` only**, with a crude cost attribution
(whole-row delta assigned to the first executed action). Adequate for family
coverage; **not adequate for per-action `Ĉost`, `ΔR̂` or `ΔĤ` estimation.**

## 17. Checkpoint / resume — CONFIRMED (unit-tested only)

Atomic write; `RunIdentity` pins all ten fields; any drift refuses the whole
checkpoint. Not yet exercised across a real interrupted run.

## 18. OOM / fatal durability — CONFIRMED (observed)

The failure path was exercised for real during this milestone's smoke: it
flushed state, named the failing row/relation/subject and run directory,
reported `quality_profile: unchanged`, and exited non-zero. No downgrade is
attempted anywhere.

## 19. Live progress — CONFIRMED

`[TRAIN n/N] relation=… subject="…"` and `[TRAIN n/N][round=k] …` observed in
the smoke. `round=k` carries no fabricated denominator. Cumulative summary every
20 rows; ETA suppressed below 5 completed rows.

## 20. CLI — CONFIRMED

`scripts/run_train_calibration_collection.py`, flags `--config`, `--output-dir`,
`--limit`, `--resume`. Split guard refuses VAL and TEST via `require_split`.
Row count guard requires exactly 477 when `--limit` is absent.

## 21. Output artifacts — CONFIRMED

Observed on disk: `predictions.jsonl`, `train_telemetry.jsonl`,
`accounting.json`, `action_coverage.json`, `manifest.json`, plus
`checkpoint.json` at the output root.

## 22. Readiness — CONFIRMED

`readiness.py` unchanged and not weakened. Uncalibrated profiles return
`CALIBRATION_COLLECTION_READY`; `FULL_VALIDATION_READY` still requires real
`TRAIN_CALIBRATED` artifacts.

## 23–25. Compliance

No training of any kind; no optimizer, gradient or weight modification anywhere.
Model profile, revisions, quantization, prompts, views and reading counts
unchanged. Closed-book preserved. `git diff -- benchmark/` empty.

## 26–27. pytest / pyflakes

2904 passed, 3 skipped. pyflakes clean across `src/`, `tests/`, `scripts/`.

## 28. Remaining blockers before TRAIN

1. **M20 precharge before neural execution** (§2) — absent.
2. **M21 → M7 control bridge** (§3) — absent.
3. **Per-action telemetry attribution** — current records cannot support
   per-action cost/ΔR/ΔH estimation, which is the point of collecting.
4. **Enumerator/verifier call split** in the runner's counters.

## 29. Remaining blockers before FULL VALIDATION

All of the above, plus offline M20/M21 derivation from real telemetry, freezing
those artifacts, and pointing the full config at them.

## 30. Epistemic status

- **Real-weight smoked:** nothing in this milestone. Audit 0034 remains the only
  real-model evidence, and it predates every seam here.
- **Pipeline-tested (scripted, offline):** mode adoption, bridge call site,
  M17/M18 callers, M19 ordering, accounting partition, A≠B.
- **Unit-tested only:** telemetry, checkpoint/resume, collection policy,
  readiness, packager.
- **Observed once, offline, 3 rows:** the collection CLI end-to-end.
- **Not run at scale:** the 477-row TRAIN collection.
- TRAIN has **not** run. The profile is **not** `FULL_VALIDATION_READY`.

## Verdict

**BLOCKED — NOT READY FOR TRAIN.**

M20 does not gate execution and M21 does not drive M7. Running the 477-row
collection now would spend a full GPU session producing telemetry that cannot
calibrate the two modules it exists to calibrate.
