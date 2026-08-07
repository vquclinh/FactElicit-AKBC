# C-16 Committed Telemetry Integrity Remediation Audit

Date: 2026-08-07
Reviewer: Codex
Verdict: **PASS — SAFE FOR FINAL INDEPENDENT C-16 VERIFICATION**

Before changing source, this remediation reread `./COVER_KBC_Technical_Proposal_New.pdf`
and `docs/audits/0045-independent-codex-final-pre-smoke-review.md` in full. The
proposal remained the architecture contract. The implementation target was limited
to C-16: fail closed when telemetry belonging to a checkpoint-committed prefix is
missing, modified, malformed, duplicated, or torn.

## 1. HEAD / Working Tree

- HEAD: `2512fe000c49c57aaf2ed4fd6b7d1f921f2ea2ba`
- The current uncommitted remediation was inspected and extended.
- C-16 source changes:
  - `src/cover_kbc/controller_calibration/checkpoint.py`
  - `src/cover_kbc/controller_calibration/recovery.py`
  - `scripts/run_train_calibration_collection.py`
  - `tests/test_collection_failure_resume.py`
- No commits or pushes were made.
- `benchmark/` remains unchanged.

## 2. Exact C-16 Reproduction

Both Audit 0045 failures were reproduced before source changes through the real
`main()` resume path with the committed TRAIN config and scripted runtimes.

Case A, missing telemetry for committed row 1:

```text
first exit: 0
resume exit: 0
checkpoint completed_rows: [0, 1, 2]
prediction rows: 3
telemetry rows after resume: [0, 2]
sufficiency_ok: true
gate_blockers: []
```

Case B, torn final committed telemetry line:

```text
first exit: 0
resume exit: 0
checkpoint completed_rows: [0, 1, 2]
resume reconciliation: dropped 1 torn line
telemetry records after resume: 56
sufficiency_ok: true
gate_blockers: []
```

This confirmed the C-16 root cause: recovery treated tail JSONL parseability and
row membership as the boundary, not the checkpoint-acknowledged committed telemetry
prefix.

## 3. Checkpoint Committed-Prefix Contract

Checkpoint format now records the exact telemetry prefix acknowledged by the same
atomic checkpoint state that records `completed_rows` and committed counters:

```text
telemetry_committed_bytes
telemetry_committed_records
telemetry_committed_sha256
```

The typed in-memory contract is `TelemetryCommitBoundary`. The checkpoint's
`completed_rows` set remains the row-commit authority; the new metadata describes
only the durable telemetry prefix for that checkpoint.

## 4. Byte-Boundary Semantics

On resume, if `train_telemetry.jsonl` is shorter than
`telemetry_committed_bytes`, recovery refuses. Bytes beyond
`telemetry_committed_bytes` are uncommitted crash-tail material and may be
atomically truncated only after the committed prefix validates.

## 5. Record-Count Semantics

The committed prefix is parsed as telemetry JSONL and must contain exactly
`telemetry_committed_records` valid records. Record count is a prefix property,
not a completed-row count. A committed row is not required to own a telemetry
record.

## 6. SHA-256 Semantics

Recovery computes SHA-256 over `file[0:B]`, where `B` is
`telemetry_committed_bytes`. A mismatch with `telemetry_committed_sha256` is
committed corruption and is refused before any artifact mutation.

## 7. Resume Validation Order

Artifact handling order is:

1. load checkpoint
2. validate `RunIdentity`
3. validate committed telemetry byte/hash/count/schema/identity prefix
4. truncate only uncommitted telemetry suffix, if present
5. reconcile prediction tail
6. open append writers
7. resume

No artifact is altered before `RunIdentity` is accepted.

## 8. Committed Corruption Refusal

Post-fix reproductions of Audit 0045's bad cases now fail closed:

```text
missing committed row telemetry:
  refused: telemetry file is 57804 byte(s), checkpoint committed 86658

torn final committed line:
  refused: telemetry file is 85709 byte(s), checkpoint committed 86658
```

The new tests also refuse one deleted committed record, one modified committed
byte with valid JSON, and duplicate committed telemetry identity.

## 9. Uncommitted-Tail Rollback

Recovery still accepts and removes material beyond the committed prefix:

```text
complete uncommitted record appended: resume exit 0, suffix removed
torn uncommitted partial line appended: resume exit 0, suffix removed
```

The committed prefix remains byte-identical after rollback.

## 10. Zero-Action Row Semantics

Covered at the checkpoint/recovery boundary. A checkpoint may advance
`completed_rows` while the telemetry prefix remains empty or unchanged. Recovery
accepted a completed row with:

```text
telemetry_committed_bytes: 0
telemetry_committed_records: 0
telemetry_committed_sha256: sha256("")
```

and a matching prediction. The fix does not equate completed row with at least
one telemetry record.

## 11. Checkpoint Version

`CHECKPOINT_VERSION` is now:

```text
collection-checkpoint-v3
```

Old `collection-checkpoint-v2` checkpoints are refused. A v3 checkpoint missing
the committed-prefix fields is also refused rather than guessed from the current
telemetry file. `train-telemetry-v3` was not bumped because the telemetry record
schema did not change.

## 12. Final Gate Consistency

The final success gate reuses `validate_committed_telemetry_prefix(...,
allow_uncommitted_tail=False)`. A run reaching the final gate with telemetry bytes
beyond the checkpoint prefix returned non-zero and wrote a gate blocker:

```text
telemetry artifact does not match checkpoint commit boundary
```

It did not report `status=complete`, `sufficiency_ok=true`, and empty blockers.

## 13. Cases A-I

Real collection/resume persistence tests added in
`tests/test_collection_failure_resume.py`:

```text
A delete every telemetry record for one committed row: REFUSE
B delete one committed telemetry record: REFUSE
C modify one byte/value in committed telemetry while valid JSON: REFUSE
D truncate committed telemetry file: REFUSE
E tear final line inside committed prefix: REFUSE
F append complete valid-looking uncommitted record after prefix: rollback + PASS
G append torn uncommitted line after prefix: rollback + PASS
H clean resume with exact committed telemetry: no mutation + PASS
I resume after all rows completed: no new records + PASS
```

Focused persistence result:

```text
python -m pytest tests/test_collection_failure_resume.py -q
44 passed
```

## 14. C-04 Crash-Window Regression

Process-level `os._exit(137)` probes after the fix:

```text
telemetry window:
  checkpoint before resume: [0]
  uncommitted telemetry bytes truncated: 1809
  final rows: [0, 1, 2], predictions: 3, duplicate identities: 0
  gate_blockers: [], sufficiency_ok: true

coverage window:
  checkpoint before resume: [0]
  uncommitted telemetry bytes truncated: 28854
  final rows: [0, 1, 2], predictions: 3, duplicate identities: 0
  gate_blockers: [], sufficiency_ok: true

prediction window:
  checkpoint before resume: [0]
  uncommitted telemetry bytes truncated: 28854
  prediction lines dropped: 1
  final rows: [0, 1, 2], predictions: 3, duplicate identities: 0
  gate_blockers: [], sufficiency_ok: true

torn checkpoint partial:
  last valid checkpoint loaded
  partial removed by the next atomic save
  final gate PASS
```

The stronger corruption guard did not break legitimate in-flight recovery.

## 15. C-03 Retry Regression

Targeted retry-to-success test still passes. A recoverable failed attempt remains
history, is marked resolved after retry, `unresolved_failed_rows` clears, failed
attempt calls remain separate, and the final gate passes. The committed telemetry
prefix advances only when the successful retry commits.

## 16. C-05 Sufficiency Regression

Sufficiency adversaries still pass:

```text
python -m pytest tests/test_collection_failure_resume.py::test_a_retried_row_stops_being_an_unresolved_failure tests/test_calibration_sufficiency.py -q
21 passed
```

The validator still rejects absent candidate-effect measurement and invalid
redundancy states while accepting legitimate all-zero measured effects,
redundancy `0.0`, genuine `NOT_APPLICABLE`, and all-zero measured M19 state.

## 17. C-01 / C-14 Regression

No regression found.

Targeted Layer-6, production bridge, and action seam tests passed:

```text
python -m pytest tests/test_layer6_integration.py tests/test_pipeline_production_seam.py tests/test_action_execution_seam.py -q
150 passed
```

The committed-config scripted run retained distinct M18 hn0/hn1 action ids and
operation ids, and duplicate identity count stayed zero. `EvidenceGraph`
attach-before-verification-record ordering was not changed by this remediation.

## 18. Previous P0/P1 Regression

No regression found for:

```text
F-02 M19 state
F-03 per-action pre/post
F-04 multi-round
F-05 multi-resume
F-06 prompt tokens
F-09 readiness
F-10 family coverage
F-12 deterministic identity
F-13 canonical program_type
N-01
N-02
```

Additional targeted results:

```text
readiness/control/telemetry focused tests: 140 passed
two fresh deterministic runs: hashes_equal=true, operation_ids_equal=true
```

## 19. Committed-Config Scripted Run

The actual config
`configs/experiments/cover_kbc_v2_train_collection.yaml` was run with scripted
runtimes over a six-relation fixture and artifacts were read from disk.

```text
exit: 0
checkpoint_version: collection-checkpoint-v3
telemetry schema versions: ["train-telemetry-v3"]
telemetry committed bytes: 336570
telemetry file bytes: 336570
telemetry committed records: 221
prefix validation suffix bytes: 0
relations covered: 6
families observed: CANDIDATE_FREE_RECALL, COUNTERFACTUAL_VERIFY, REVERSE_CHECK, SPECIALIST_VERIFY
executed actions: 30
duplicate identities: 0
successor transitions: 24
delta_r_nonzero: 10
residual distinct count: 8
candidate_effect_measured on executed actions: 30
prompt tokens: 27235
physical calls: 151
role partition ok: true
unresolved_failed_rows: []
sufficiency_ok: true
gate_blockers: []
```

## 20. TRAIN Gold Isolation

No TRAIN `ObjectEntities` were read. Recovery uses checkpoint metadata, telemetry
bytes, telemetry identities, row index, subject, and relation. Targeted search
found only:

```text
runner comment: ObjectEntities joined offline
runner prediction writer: writes predicted ObjectEntities
pipeline docstring: final ObjectEntities
data loader comment: blind test split ships empty ObjectEntities
```

## 21. Pytest

```text
python -m pytest -q
3081 passed, 3 skipped in 27.76s
```

## 22. Pyflakes

```text
python -m pyflakes src/ tests/ scripts/
exit 0, no output
```

## 23. Benchmark Immutability

```text
git diff -- benchmark/
exit 0, no output
```

## 24. Remaining P0

None found in the C-16 remediation scope.

## 25. Remaining P1

None found. C-16 is fixed for implementation handoff.

## 26. Remaining P2/P3

C-02 remains unchanged as an open-by-design paper disclosure item, not a TRAIN
blocker. No unrelated P2/P3 cleanup was performed.

## 27. Exact Blockers Before Real-Weight Smoke

This implementation audit leaves no known C-16 implementation blocker. Before
real-weight smoke, the next required step is final independent C-16 verification
of this working tree.

## 28. Exact Blockers Before 477-Row TRAIN

Run and pass the real-weight smoke after independent C-16 verification. Do not
start the 477-row TRAIN until that smoke passes.

## 29. Blockers Before FULL VALIDATION

Do not claim `FULL_VALIDATION_READY`. Remaining full-validation prerequisites from
prior audits still apply, including M20/M21 calibration artifacts and the C-02
paper disclosure if the zero-DeltaH interpretation remains.

## 30. Final Verdict

**PASS — SAFE FOR FINAL INDEPENDENT C-16 VERIFICATION**

C-16's committed telemetry corruption gap is closed in implementation: committed
telemetry loss, mutation, malformed committed prefix, duplicate committed identity,
and committed torn-line cases now fail closed, while legitimate uncommitted crash
tails still roll back automatically.
