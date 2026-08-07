# Independent Codex Final Pre-Smoke Review

Date: 2026-08-07
Reviewer: Codex
Verdict: **BLOCKED — FIXES REQUIRED BEFORE REAL-WEIGHT SMOKE**

This review read `./COVER_KBC_Technical_Proposal_New.pdf`, `docs/audits/0043-*`, and
`docs/audits/0044-*` in full before inspecting the implementation. Audit 0044 was
independently verified rather than trusted. The current uncommitted working tree was
inspected directly; this review did not review `HEAD` alone. Codex modified no
production, configuration, or test source during this review. The only repository
modification made by this review is this audit file.

## 1. HEAD / Working Tree

- `git rev-parse HEAD`: `2512fe000c49c57aaf2ed4fd6b7d1f921f2ea2ba`
- `git status --short` before this audit file:

```text
 M docs/IMPLEMENTATION_STATUS.md
 M scripts/run_train_calibration_collection.py
 M src/cover_kbc/control/action_catalog.py
 M src/cover_kbc/controller_calibration/__init__.py
 M src/cover_kbc/controller_calibration/checkpoint.py
 M src/cover_kbc/controller_calibration/collection_policy.py
 M src/cover_kbc/controller_calibration/progress.py
 M src/cover_kbc/controller_calibration/readiness.py
 M src/cover_kbc/controller_calibration/telemetry.py
 M src/cover_kbc/evidence/graph.py
 M src/cover_kbc/models/base.py
 M src/cover_kbc/models/huggingface.py
 M src/cover_kbc/models/offline.py
 M src/cover_kbc/pipeline.py
 M src/cover_kbc/types.py
 M src/cover_kbc/verification/bidirectional_types.py
 M tests/test_action_execution_seam.py
 M tests/test_collection_failure_resume.py
 M tests/test_control_entropy.py
 M tests/test_controller_calibration_collection.py
 M tests/test_controller_calibration_readiness.py
 M tests/test_controller_calibration_telemetry.py
 M tests/test_layer6_integration.py
 M tests/test_micro_planner.py
 M tests/test_pipeline_production_seam.py
 M tests/test_real_model_smoke_harness.py
?? configs/experiments/cover_kbc_v2_train_collection.yaml
?? docs/audits/0041-independent-codex-full-repository-pre-train-review.md
?? docs/audits/0042-post-codex-train-collection-remediation.md
?? docs/audits/0043-independent-codex-post-remediation-train-go-no-go.md
?? docs/audits/0044-final-pre-smoke-reliability-remediation.md
?? src/cover_kbc/controller_calibration/recovery.py
?? src/cover_kbc/controller_calibration/sufficiency.py
?? tests/test_calibration_sufficiency.py
```

- `git diff --stat`: 26 tracked files changed, 3012 insertions, 492 deletions.
- The full tracked `git diff` was reviewed. Untracked remediation files were also
  read directly.

The remediation since Audit 0043 changed M18 logical identity, telemetry schema and
sufficiency semantics, collection retry/accounting/checkpoint behavior, automatic
recovery, duplicate evidence insertion order, committed TRAIN config, and tests for
those behaviors.

## 2. Remediation Diff Reviewed

The diff was not treated as trusted. I traced the modified source paths and then ran
direct probes through the runner and pipeline:

- `src/cover_kbc/verification/bidirectional_types.py`
- `src/cover_kbc/control/action_catalog.py`
- `src/cover_kbc/pipeline.py`
- `src/cover_kbc/evidence/graph.py`
- `src/cover_kbc/controller_calibration/{telemetry,checkpoint,progress,readiness,recovery,sufficiency}.py`
- `scripts/run_train_calibration_collection.py`
- `configs/experiments/cover_kbc_v2_train_collection.yaml`

## 3. C-01 Verdict

**Closed for M18 outcome attribution.**

M18 action identity now includes the canonical check identity. Outcome lookup uses
`operation_id`, not broad mechanism/target matching. I found no remaining
cross-attribution in the targeted probes.

## 4. M18 Canonical Identity Trace

Source trace:

- `EligibleCheck.check_id` is derived from `check_kind`, `target.target_id`, and
  `counterfactual_class`.
- Layer-6 `action_id` is `M18:{check.check_id}`.
- `BidirectionalCheckRequest.operation_id` is
  `m18:{check_id}:{template_id}#{sample_index}`.
- M18 execution records carry `origin_event_id == request.operation_id`.
- `CoverPipeline._m18_reading` indexes results by exact request `operation_id` and
  raises on multiple records for one operation.
- Telemetry uses the Layer-6 `action_id` and the per-action runner operation id.

Direct identity example for the same base candidate with different near-miss classes:

```text
check_id A: COUNTERFACTUAL:albert einstein:hn0
check_id B: COUNTERFACTUAL:albert einstein:hn1
action_id A: M18:COUNTERFACTUAL:albert einstein:hn0
action_id B: M18:COUNTERFACTUAL:albert einstein:hn1
request operation A: m18:COUNTERFACTUAL:albert einstein:hn0:m18_counterfactual_v1#0
request operation B: m18:COUNTERFACTUAL:albert einstein:hn1:m18_counterfactual_v1#0
```

All four identities differ.

## 5. Divergent Near-Miss Attribution Probe

The committed-config scripted six-relation run deliberately produced divergent M18
results for the same base candidate:

```text
row 200 action_id=M18:COUNTERFACTUAL:albert einstein:hn0
operation_id=200:4:M18:COUNTERFACTUAL:albert einstein:hn0
structural_outcome=NEAR_MISS_RELATION
errors=[]

row 200 action_id=M18:COUNTERFACTUAL:albert einstein:hn1
operation_id=200:5:M18:COUNTERFACTUAL:albert einstein:hn1
structural_outcome=
errors=["parse:MALFORMED"]
```

Each telemetry record received its own result. I saw the same hn0/hn1 separation on
other rows in the run.

## 6. C-14 Duplicate-Verification Verdict

**Closed.**

`EvidenceGraph.add_verification` now attaches the evidence edge before appending the
verification reading. A duplicate edge rejection therefore cannot leave behind a
duplicate verification label.

Repeated Layer-4 re-integration of an unchanged result kept the graph signature
stable. Candidate verification counts remained one per touched candidate, and edge
ids did not duplicate.

For an action that did not touch another candidate, the untouched candidate did not
appear in `candidates_supported` or `candidates_contradicted` after re-integration.

## 7. Result Merging / Bridge Regression

Multi-round M18 merging retained earlier round results and deduplicated identical
operations:

```text
merged M18 operation ids:
  m18:CANDIDATE_FREE_RECALL:m18_candidate_free_v1#0
  m18:COUNTERFACTUAL:albert einstein:hn0:m18_counterfactual_v1#0
  m18:COUNTERFACTUAL:albert einstein:hn1:m18_counterfactual_v1#0

origin ids unique: true
operation ids unique: true
duplicate same-operation multiplied: false
```

Bridge idempotency remained intact: applying the same Layer-4 result twice did not
change the graph after the first integration and did not add duplicate logical
evidence.

## 8. C-03 Verdict

**Closed.**

The runner now distinguishes unresolved failures from historical failed attempts and
separates wasted failed-attempt calls from committed accounting.

## 9. Unresolved Failures vs History

Retry lifecycle probe:

```text
attempt 1 exit: 1
attempt 1 rows_completed: 2
attempt 1 unresolved_failed_rows: [1]
attempt 1 failed_attempts: 1
attempt 1 failed_attempt_calls: 9

attempt 2 exit: 0
final rows_completed: 3
final unresolved_failed_rows: []
final failed_attempts: 1
final failed_attempt_calls: 9
failure_history row 1 resolved: true
```

The failed attempt remained visible after successful retry, but no longer blocked the
final gate.

## 10. Retry-To-Success Final Gate

The retry-to-success run completed with:

```text
status: complete
gate_blockers: []
sufficiency_ok: true
telemetry rows: [0, 1, 2]
duplicate telemetry identities: 0
predictions: 3
coverage executed rows/actions: once per committed action
```

Committed accounting counted only successful committed work. The failed attempt calls
remained in the separate failed-attempt accounting fields.

## 11. C-15 Completion-Order Verdict

**Closed.**

The runner computes row cost before moving durable completion state, then adds the
row to `completed_rows`, then sets `rows_completed = len(completed)`, and only then
persists checkpoint/accounting/coverage. I found no independent mutable completion
counter that can advance ahead of the checkpoint commit boundary.

Hard-kill probes during row commit showed the checkpoint still contained only prior
committed rows before resume; it never claimed the killed row as completed.

## 12. C-04 Reconciliation Design

`controller_calibration/recovery.py` was reviewed from scratch.

Expected resume order is implemented:

1. validate `RunIdentity`
2. load last valid checkpoint
3. reconcile persisted artifacts to checkpoint
4. open append writers
5. resume collection

The checkpoint `completed_rows` set is used as the commit boundary for reconciliation.
However, the fail-closed boundary is incomplete; see sections 14, 30, 31, and 33.

## 13. Four Crash-Window Probes

I ran hard-kill probes through the real collection runner with scripted runtimes:

```text
A. telemetry written, kill before prediction/checkpoint
   resume exit=0, final status=complete, gate_blockers=[]
   uncommitted telemetry dropped=1, predictions=3, duplicate telemetry ids=0

B. telemetry records written, kill before prediction/checkpoint
   resume exit=0, final status=complete, gate_blockers=[]
   uncommitted telemetry dropped=19, predictions=3, duplicate telemetry ids=0

C. telemetry + prediction written, kill before checkpoint commit
   resume exit=0, final status=complete, gate_blockers=[]
   uncommitted telemetry dropped=19, prediction lines dropped=1

D. torn checkpoint partial beside last valid checkpoint
   resume exit=0, final status=complete, gate_blockers=[]
   partial checkpoint discarded by the next atomic save
```

All four common in-flight row crash windows replayed the incomplete row without manual
file editing, kept prior completed rows, preserved one run directory and one run id,
and ended with correct predictions/accounting/coverage for the scripted subset.

Note: the runner writes `action_coverage.json` during `persist()`, so there is no
separate durable "coverage written but prediction/checkpoint missing" file boundary
in the current implementation. The realistic B/C windows were therefore exercised at
the telemetry-record and prediction-record boundaries.

## 14. Corruption Fail-Closed Probes

Automatic reconciliation does **not** fully fail closed on real corruption.

Probe matrix:

```text
committed prediction mismatch: REFUSED
malformed non-tail JSONL: REFUSED
prediction count less than committed rows: REFUSED
uncommitted tail material: accepted and rolled back
checkpoint identity mismatch: REFUSED
committed-row telemetry missing: NOT REFUSED
committed-row telemetry final torn line: NOT REFUSED
```

Blocking reproduction:

1. Run the committed config with scripted runtimes for three rows.
2. Confirm checkpoint completed rows `[0, 1, 2]` and predictions for three rows.
3. Delete every telemetry record for committed row `1`.
4. Resume automatically with no manual repair.

Observed result:

```text
resume exit: 0
status: complete
gate_blockers: []
sufficiency_ok: true
checkpoint completed_rows: [0, 1, 2]
prediction rows: 3
telemetry rows after resume: [0, 2]
```

This violates the required fail-closed contract. Corruption affecting a row that the
checkpoint says is committed must not be silently accepted.

## 15. Multi-Resume Regression

Stable resume behavior was confirmed in retry and hard-kill probes:

- resumed runs reused one run directory
- resumed runs reused the same `run_id`
- prior completed rows were left untouched
- incomplete rows replayed exactly once
- final telemetry identities did not duplicate

This area is blocked only by the committed-row telemetry corruption gap described
above.

## 16. C-05 Candidate-Effect Semantics

**Closed.**

For executed actions, `candidate_effect_measured == true` is set by the action
execution seam only after the graph/effect diff is run. Executed records in the
scripted run had candidate-effect measurement present. Unexecuted legal actions did
not claim measurement.

Valid all-zero measured candidate effects pass sufficiency. Removing the measurement
marker from an executed action fails sufficiency.

## 17. C-05 Redundancy Semantics

**Closed.**

The telemetry model preserves the three required states:

- `MEASURED`: numeric redundancy exists, including valid `0.0`
- `NOT_APPLICABLE`: no candidate surface exists
- `UNMEASURED`: instrumentation absent

The sufficiency validator rejects `UNMEASURED` when redundancy is applicable for an
executed action. It also rejects false `NOT_APPLICABLE` when an action touched or
named a candidate. Legitimate `NOT_APPLICABLE` and numeric `0.0` redundancy passed.

## 18. Sufficiency Adversaries

Starting from valid telemetry, the validator failed or schema-rejected all required
invalid mutations:

```text
candidate-effect measurement marker removed: FAIL
applicable redundancy changed to UNMEASURED: FAIL
false NOT_APPLICABLE with candidate surface: FAIL
missing state measurement: SCHEMA_REJECT
noncanonical program_type: FAIL
noncanonical family: FAIL
missing action_id: SCHEMA_REJECT
unstable/address-like operation_id: FAIL
missing M17 result-or-error: FAIL
missing M18 result-or-error: FAIL
missing spend_class: FAIL
claimed reserved_class: FAIL
charged action with missing prompt tokens: FAIL
broken role partition: SCHEMA_REJECT
no successor transitions where expected: FAIL
```

Legitimate zero-signal records passed:

```text
measured all-zero candidate effects: PASS
measured redundancy 0.0: PASS
genuine NOT_APPLICABLE redundancy: PASS
measured all-zero M19 state: PASS
```

The validator tests instrumentation presence, not signal magnitude.

## 19. Telemetry V3 Consistency

`train-telemetry-v3` is propagated through writer, reader, `RunIdentity`,
checkpoint identity, manifest, sufficiency validation, and tests.

Compatibility probes:

```text
v2 checkpoint resume: REFUSED
v2 telemetry read: REFUSED
```

Old v2 checkpoint/telemetry did not silently resume as v3.

## 20. Prior P0/P1 Regression Matrix

Targeted regression verdicts:

```text
F-02 real M19 residual/components: PASS
F-03 genuine pre/post action state: PASS
F-04 bounded multi-round successor transitions: PASS
F-05 stable multi-resume run_id/directory: PASS
F-06 prompt-token accounting: PASS
F-09 readiness before runtime: PASS
F-10 required family never surfaced -> full-run FAIL: PASS
F-12 deterministic identity across processes: PASS
F-13 canonical program_type lookup: PASS
```

Key observations:

- scripted run had nondegenerate M19 residual state and true pre/post transitions
- action chains stayed bounded by `max_control_rounds_per_catalogue=3`
- prompt tokens were recorded and charged
- readiness rejected invalid split before runtime construction
- suppressing a required family in full-run mode produced a gate blocker
- deterministic two-process telemetry identity hashes matched after removing run id
- program types were canonical

## 21. N-01 / N-02

**No regression found.**

M18 structural checks remain independent structural groups and do not inflate
acquisition support, `F`, `X`, `I`, or `m(o)`. Direct contract checks showed relation
catalogue group counts unchanged and no M18 groups in acquisition group support.

Different near-miss classes remain distinct logical actions. The C-01 identity change
does not undo N-02.

## 22. C-02 Status

**OPEN BY DESIGN / PAPER DISCLOSURE ITEM. Not a TRAIN blocker.**

`H` is still measured. `DeltaH == 0` across the current M17/M18 collected action
space remains structural, not missing instrumentation. This review does not require a
non-zero `DeltaH`.

## 23. Committed TRAIN Config

The actual committed config reviewed was:

`configs/experiments/cover_kbc_v2_train_collection.yaml`

Observed:

```text
split: train
M11-M19 enabled: true
M20/M21/Layer6 disabled: true
TrainCollectionPolicy owns selection: true
max_control_rounds_per_catalogue: 3
fake calibration: absent
readiness_state: CALIBRATION_COLLECTION_READY
may_run_collection: true
```

Frozen model profile matches the proposal:

```text
mistralai/Mistral-Small-3.2-24B-Instruct-2506
revision 95a6d26c4bfb886c58daf9d3f7332c857cb27b43

Qwen/Qwen3.5-4B
revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a

published total parameters: 28,671,226,368
budget <= 32B: true
```

## 24. Scripted Six-Relation Run

I drove the real collection `main()` with the committed config and scripted runtimes,
selecting one TRAIN row per relation without inspecting factual ObjectEntities.

Result:

```text
exit: 0
status: complete
rows completed: 6
gate_blockers: []
sufficiency_ok: true
telemetry schema versions: ["train-telemetry-v3"]
telemetry records: 221
executed actions: 30
relations covered: 6
predictions: 6
unresolved_failed_rows: []
duplicate record identities: 0
successor transitions: 24
M19 residual distinct count: 8
candidate_effect_measured on executed actions: 30
candidate_effect_measured on unexecuted actions: 0
redundancy statuses: MEASURED=12, NOT_APPLICABLE=18
layer4 prompt tokens: 17533
accounting prompt tokens: 27235
accounting physical calls: 151
role partition: ok
```

The scripted run showed truthful candidate effects, canonical program types,
deterministic action identities, prompt-token accounting, and correct M18 structural
attribution.

## 25. TRAIN Gold Isolation

No regression found.

The collection path builds queries from `row_index`, `subject`, and `relation`.
Recovery reconciliation uses the same committed query identity fields. It does not
read TRAIN `ObjectEntities`.

`ObjectEntities` references in the targeted runtime path are limited to comments or
writing predicted output objects:

```text
scripts/run_train_calibration_collection.py: ObjectEntities joined offline comment
scripts/run_train_calibration_collection.py: writes prediction ObjectEntities
src/cover_kbc/pipeline.py: pipeline output docstring
src/cover_kbc/data/loader.py: blind test split comment
```

Candidate-effect measurement comes from graph/evidence/bridge state, not gold labels.

## 26. Closed-Book / No-Training / Model Budget

No regression found.

The remediation did not add external factual RAG, web, KB lookup, optimizer,
backpropagation, LoRA, fine-tuning, or a new neural model. A targeted diff scan found
only the internal label `M11 parametric retrieval`, not an external retrieval path.

The frozen two-model profile remains exactly the proposal profile and stays under the
published 32B budget.

## 27. Pytest

```text
python -m pytest -q
3069 passed, 3 skipped in 26.33s
```

## 28. Pyflakes

```text
python -m pyflakes src/ tests/ scripts/
exit 0, no output
```

## 29. Benchmark

```text
git diff -- benchmark/
exit 0, no output
```

No benchmark artifacts were modified.

## 30. Remaining P0

No unresolved P0 was found in the targeted review.

## 31. Remaining P1

**C-16 P1: committed-row telemetry corruption is not fail-closed.**

If telemetry for a checkpoint-committed row is missing or the final committed-row
telemetry line is torn, automatic recovery can accept the run, keep the row committed,
and report `gate_blockers=[]` / `sufficiency_ok=true`. That threatens collection
durability and telemetry sufficiency and could let a 477-row run complete with missing
committed calibration data.

## 32. Remaining P2/P3

- C-02 remains an open-by-design paper disclosure item, not a TRAIN blocker.
- Prior full-validation items outside the pre-smoke collection gate remain outside
  this targeted review unless they intersect with the C-16 recovery blocker.

No additional P2/P3 finding was raised as a smoke blocker.

## 33. Exact Blockers Before Real-Weight Smoke

Fix C-16 before running the real-weight smoke:

- recovery must refuse missing telemetry for any checkpoint-committed row
- recovery must refuse torn/malformed telemetry affecting a checkpoint-committed row
- final gate or reconciliation must ensure committed-row telemetry coverage is
  consistent with the checkpoint, not merely that predictions and checkpoint agree
- only uncommitted in-flight row material may be removed automatically

## 34. Exact Blockers Before 477-Row TRAIN

Same as pre-smoke:

- C-16 must be fixed
- the pre-train real-weight smoke must then be rerun and pass

No other collection-source blocker was found in this targeted review.

## 35. Blockers Before FULL VALIDATION

Before full validation:

- C-16 must be fixed if still present
- remaining non-smoke M20/M21 calibration artifacts and validation readiness blockers
  from the prior audits must be completed
- C-02 must be disclosed as a design/paper limitation if the action space remains
  structurally zero-DeltaH

This review does not claim `FULL_VALIDATION_READY`.

## 36. Final Verdict

**BLOCKED — FIXES REQUIRED BEFORE REAL-WEIGHT SMOKE**

Audit 0044's main remediations for C-01, C-03, C-05, C-14, and C-15 were
independently verified. The common in-flight C-04 crash windows were also verified.
The final smoke remains blocked because recovery does not fail closed when telemetry
corruption affects a checkpoint-committed row.
