# Audit 0038 — Final Full-TRAIN Collection Readiness

**Verdict: BLOCKED — NOT READY FOR TRAIN**

One of Audit 0037's three blockers is closed. One is partially addressed and
introduced a worse defect in the process. One is untouched.

---

## 1. Scope

Working tree on HEAD `872bc8aa14aae9bfca9db9709f46c6146add436c`. Verified from
executable source and live runs. `pytest`: 2940 passed, 3 skipped. `pyflakes`:
clean. `git diff -- benchmark/`: empty.

## 2. Proposal sections reviewed

§2.3, §9.3, §15, §16 + Table 6, §17. §17's `+γ·ΔĤ` term drives the sign-
convention finding in §15 below.

## 3. M0–M21 status

M0–M20 implemented and production-wired. **M21 implemented, still shadow-only.**

## 4–5. Traces

Production: `enumerate_query` → M9/M10 → M2 + M11 + M12–M15 → `decide_graph` →
`_run_consensus` → M16 → catalogue → per action: **`_precharge` (M20)** →
`execute_action` → `_integrate_layer4` → `ProductionEvidenceBridge.apply` →
`_estimate_coverage_gap` (M19) → **`control_entropy` (H)** → next round →
`finalize` (M8).

Collection: identical after selection; selection comes from
`TrainCollectionPolicy`.

## 6. M20 — CONFIRMED, no regression

`RelationBudgetScheduler.schedule` → `BudgetLedger.reserve`, one ledger per
query, precharge before any runtime touch, refusal costs zero physical calls.
Nine tests in `test_m20_precharge_gate.py` still pass.

## 7–8. M21 → M7 — **NOT SATISFIED**

`MicroPlanner.plan` remains reachable only through `_plan_micro_action`, the
shadow diagnostic site. No production path routes a planner decision into
`execute_action`. Utility, strict `> τ_continue`, and the lookahead bound are
untouched and unreached. **Not attempted this milestone.**

## 9–10. Collection separation — CONFIRMED

`test_collection_never_invokes_the_planner` (zero planner calls) and
`test_collection_and_production_share_the_executor` (identical function object).

## 11. M19 refresh — CONFIRMED

Per action, before the entropy snapshot.

## 12–13. Authoritative H — **FIXED**

Source: Module 5's `coverage.py::mean_inclusion_uncertainty`, the mean of
`scoring.py::inclusion_uncertainty` — `H_inc(q) = -q log q - (1-q) log(1-q)` —
over active candidates, **already normalised to `[0,1]` by its owner**. The
aggregation existed; none was invented.

`CoverPipeline.control_entropy` delegates to it. `test_pipeline_H_is_module_5s_quantity_not_a_new_one`
asserts numeric equality with the owner's function, and
`test_the_runner_contains_no_entropy_mathematics` asserts the collection runner
contains no `math.log` and delegates instead.

## 14. H lifecycle — FIXED

`entropy_before` captured before precharge; `entropy_after` captured after
integration, bridge and the M19 refresh, so it describes the state the next
round will see. Unexecuted actions carry `entropy_before` and an explicit
`entropy_after=None` with no `delta_entropy` key — no fabricated outcome.

## 15–16. ΔH sign convention — VERIFIED UNCHANGED

`historical_bins.py` documents `expected_delta_h` as *"Reduction in uncertainty
… M21 never recomputes an entropy of its own"*, and `micro_planner.py` adds
`+γ·delta_h`. A reduction must therefore be positive, which is exactly the
existing `telemetry.py` convention `H_before - H_after`. **No sign was flipped.**
`test_pipeline_and_telemetry_agree_on_the_sign` pins pipeline and telemetry to
one convention.

Case A (real change moves H), Case B (no change ⇒ ΔH exactly 0) and Case C
(action 2's `H_before` equals action 1's refreshed `H_after`) all pass.

## 17–19. ΔR, per-action cost, role partition — CONFIRMED, no regression

ΔR from M19's own components. Cost from `physical_delta` over runtime counters.
Partition raises if it fails to sum or a counter moves backwards.

## 20–22. Exactly-once, legal-unselected, rounds — CONFIRMED

## 23. Row transaction — **PARTIAL, AND A NEW DEFECT**

Implemented: the runner now buffers a row's telemetry and coverage outcomes in
memory and commits them only after the whole row succeeds, so a mid-row failure
leaves no partial action records.

**But `TelemetryWriter` opens its file with mode `"w"`.** On resume the runner
reopens the same path, which **truncates every previously committed row**. That
is a worse failure than the duplication it was meant to prevent: silent loss of
hours of completed work rather than detectable duplicates. The buffering is
sound; the writer's open mode is not.

## 24–29. Forced-failure resume test — **ABSENT**

§14 of the governing brief mandates a scripted mid-row failure + resume test
asserting exactly-once telemetry, predictions, accounting and coverage. **It was
not written.** Without it, resume safety is unproven regardless of the buffering
design, and the truncation defect above went unnoticed until source inspection.

## 30–34. Gold isolation, CLI, progress, durability, artifacts — CONFIRMED

Gold absent from telemetry. Flags `--config`, `--output-dir`, `--limit`,
`--resume`; 477-row default intact. `[TRAIN n/N]` and `[TRAIN n/N][round=k]`
observed. OOM path reports `quality_profile: unchanged`, exits non-zero.

## 35–36. Readiness

Collection: not ready. Full validation: fail-closed; real M20/M21 artifacts
still do not exist.

## 37–39. Benchmark / pytest / pyflakes

Untouched. 2940 passed, 3 skipped. Clean.

## 40. Remaining blockers before the 477-row collection

1. **M21 → M7 unwired** (untouched this milestone).
2. **`TelemetryWriter` truncates on resume** — must append when continuing a run.
3. **No forced mid-row failure + resume test** — mandated and absent.

## 41. Remaining blockers before FULL VALIDATION

All of the above, plus offline M20/M21 derivation from real telemetry, freezing
those artifacts, and pointing the full config at them.

## 42. Epistemic status

- **Real-weight smoked:** nothing. Audit 0034 remains the last real-weight
  evidence and predates every seam in Audits 0035–0038.
- **Pipeline-tested (scripted, offline):** M20 gating, action seam, per-action
  cost, role partition, rounds, shared executor, collection-avoids-M21,
  H lifecycle and ΔH sign.
- **Scripted collection-tested:** the CLI, 3 rows, offline null runtime, with
  row-transaction commit.
- **Not tested at all:** resume after a mid-row failure.
- **Not run at scale:** the 477-row collection. TRAIN has not run.

## 43. Verdict

**BLOCKED — NOT READY FOR TRAIN.**

H and ΔH are now fully recoverable with the correct, verified sign convention,
and no second entropy estimator was created. But Module 21 still does not reach
Module 7, and the resume path can now destroy completed telemetry rather than
duplicate it — a regression in severity that the missing mandated test would
have caught.
