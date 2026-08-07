# Audit 0040 — Full TRAIN Collection GO/NO-GO

**Verdict: PASS — READY FOR FULL TRAIN CALIBRATION COLLECTION**

Audit 0039's single remaining blocker is closed. No implementation blocker
remains before the 477-row TRAIN collection.

---

## 1. Scope

Working tree on HEAD `872bc8aa14aae9bfca9db9709f46c6146add436c`. Verified from
executable source, tests and live runs. `pytest`: 2966 passed, 3 skipped.
`pyflakes`: clean. `git diff -- benchmark/`: empty.

## 2. Proposal requirements relevant to collection

§2.3 (deterministic calibration permitted; no training), §9.3 (budget
decomposition, hard verification reservation), §15 (`R_t`), §16 + Table 6
("concrete values are calibrated on TRAIN"), §17 (`U_t(a)`, strict
`> τ_continue`, 1–2 step lookahead, "estimates come from relation-specific
historical bins on TRAIN"), Appendix C (planner receives the full state).

## 3. M0–M21 — implemented and production-wired

All twenty-two modules. Re-verified against source this milestone; no module is
shadow-only.

## 4–6. M20 / M21 / M21 → M7 — CONFIRMED, no regression

M20: `RelationBudgetScheduler.schedule` → `BudgetLedger.reserve`, one ledger per
query, precharge before any runtime touch, refusal costs zero physical calls
(9 tests). M21: `_plan_next_action` → `MicroPlanner.plan` → selected action →
`CoverPipeline.execute_action`, with STOP, strict `>`, equality-is-STOP and
lookahead ≤ 2 all asserted (12 tests).

## 7. Collection avoids M21 — CONFIRMED

`test_collection_still_never_uses_the_planner` spies `_plan_next_action` and
asserts zero planner-driven selections under
`TRAIN_CALIBRATION_COLLECTION_ONLY`. The bootstrap circularity — using an
uncalibrated planner to gather the bins that would calibrate it — remains
structurally impossible.

## 8–10. H / ΔH / ΔR / cost / role accounting — CONFIRMED, unchanged

H is Module 5's `mean_inclusion_uncertainty`; `ΔH = H_before - H_after`,
positive for a reduction, matching `historical_bins` and `+γ·delta_h`. Per-action
cost from `physical_delta` over runtime counters; the role partition raises if
it fails to sum. 12 entropy tests, 15 action-seam tests.

## 11–13. Row transaction and writer modes — CONFIRMED

A row's telemetry and coverage buffer in memory and commit only on row success.
`TelemetryWriter` takes a **required** `resume` argument: fresh runs open `"w"`,
resumed runs open `"a"` and pre-load committed identities so the duplicate guard
spans the whole file.

## 14–21. Forced mid-row failure + resume — **NOW PROVEN**

`tests/test_collection_failure_resume.py` (14 tests) drives the runner's real
`main()` twice against one output directory. Nothing about persistence is
mocked — the failure is injected into action execution, and every artefact is
read back off disk.

Scenario: row 0 commits; row 1 executes one real action, whose graph effects and
physical calls genuinely occur, and *then* a fatal exception is raised before
row commit. Observed failure report names `failing_row: 1`, `subject: Mangareva`.

**Immediately after failure:** row 0 telemetry and prediction durable exactly
once; checkpoint `completed_rows == [0]`; row 1 contributes **zero** durable
telemetry, no prediction, no accounting and no coverage. The row transaction
aborts whole.

**After resume:** exit 0; every pre-resume telemetry identity still present
(the Audit-0038 truncation bug is provably gone) and new records appended;
no duplicate `(row_index, round_index, operation_id)`; exactly one prediction
per row; `rows_completed == 2`; role partition still sums; coverage equals
committed executed telemetry; checkpoint `[0, 1]` agrees with every artefact.

**One real durability bug this test exposed and fixed:** the resumed process
started a fresh `CoverageLedger`, so committed coverage from before the restart
was silently dropped and the final table under-reported support — exactly the
support the offline derivation gate checks. `CoverageLedger.from_json` now
restores it on resume. That bug was invisible to every prior audit and is the
concrete justification for the test having been mandatory.

## 22. Identity / corruption guards — CONFIRMED

`RunIdentity` still pins all ten fields and refuses the whole checkpoint on any
drift (8 parameterised cases); `resume_from` refuses a missing or malformed
checkpoint; the writer refuses duplicate identities across the committed file.
Validation precedes any append.

## 23. TRAIN gold isolation — CONFIRMED

Queries carry subject/relation/row_index only. Telemetry from live runs contains
no `ObjectEntities` field, checked programmatically.

## 24–26. CLI, progress, durability — CONFIRMED

`--config`, `--output-dir`, `--limit`, `--resume`; 477-row default intact when
`--limit` is absent. `[TRAIN n/N]` and `[TRAIN n/N][round=k]` observed, with a
resume line reporting committed rows. OOM/fatal path flushes committed rows,
names the failing row, reports `quality_profile: unchanged`, exits non-zero. No
GPU is named or required anywhere.

## 27–29. Benchmark / pytest / pyflakes

`benchmark/` untouched. 2966 passed, 3 skipped. pyflakes clean across `src/`,
`tests/`, `scripts/`.

## 30. Blockers before the full 477-row TRAIN collection

**NONE.**

## 31. Blockers before FULL VALIDATION

Offline M20/M21 derivation from the real telemetry, freezing those artifacts,
and pointing the full config at them. `FULL_VALIDATION_READY` remains
fail-closed and is **not** claimed.

## 32. Epistemic status

- **Real-weight smoked:** nothing in Audits 0035–0040. Audit 0034 remains the
  last real-weight evidence and predates every seam built since.
- **Pipeline-tested (scripted, offline):** M20 gating, M21 → M7, STOP and the
  strict threshold, action seam, per-action cost, role partition, real rounds,
  H/ΔH, shared executor, collection-avoids-M21.
- **Scripted collection-tested through the real runner:** the CLI end to end,
  forced mid-row failure, resume, and exactly-once persistence.
- **Not run at scale:** the 477-row collection. **TRAIN has not run.**

## 33. Verdict

**PASS — READY FOR FULL TRAIN CALIBRATION COLLECTION.**

The next action is the owner's real 477-row TRAIN run. Its output is the input
to offline M20/M21 derivation, which is a separate milestone.
