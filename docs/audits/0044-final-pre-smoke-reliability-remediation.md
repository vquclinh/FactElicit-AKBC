# 0044 — Final Pre-Smoke Reliability Remediation Audit

**Verdict: PASS — READY FOR INDEPENDENT PRE-SMOKE REVIEW**

---

## 0. Scope and method

This milestone exists to close four P2 findings Audit 0043 raised — **C-01,
C-03, C-04, C-05** — and nothing else. `COVER_KBC_Technical_Proposal_New.pdf`
and `docs/audits/0043-*` were re-read in full before any change.

Every finding was **reproduced from current source first**, and every fix is
verified below from executable code plus behavioural probes and tests, not from
a claim that the change was made. Where a fix exposed something Audit 0043 had
not seen, that is recorded with the measurement rather than quietly absorbed.

Not done, deliberately: no M0–M21 module was redesigned; no offline M20/M21
calibration; **C-02 was not "fixed"** (§27); the collection action space is
unchanged; unrelated P3s were left alone. No real weights, no TRAIN, no VAL, no
TEST. Nothing committed or pushed.

---

## 1. Current HEAD and working tree

| item | value |
|---|---|
| HEAD | `2512fe000c49c57aaf2ed4fd6b7d1f921f2ea2ba` (`main`) |
| Working tree | modified, uncommitted |
| `git diff --stat` | 26 files, +3 012 / −492 |
| New this milestone | `src/cover_kbc/controller_calibration/recovery.py`; edits to `bidirectional_types.py`, `evidence/graph.py`, `checkpoint.py`, `progress.py`, `telemetry.py`, `sufficiency.py`, `action_catalog.py`, `pipeline.py`, the runner, and 5 test files |
| Tests | **3 069 passed, 3 skipped** |
| Static | `python -m pyflakes src/ tests/ scripts/` clean |
| Benchmark | `git diff -- benchmark/` empty |

`docs/IMPLEMENTATION_STATUS.md` shows two trailing blank lines added; that edit
is not from this milestone and was left untouched.

---

## 2. C-01 reproduction

Re-derived against the source at the start of this milestone.
`BidirectionalCheckRequest.operation_id` was
`m18_<kind>:<template>#<sample>` — carrying neither target nor near-miss class.
A live six-relation run showed the collision directly:

```
row 200 awardWonBy
  M18:COUNTERFACTUAL:albert einstein:hn0 -> m18_counterfactual:m18_counterfactual_v1#0
  M18:COUNTERFACTUAL:albert einstein:hn1 -> m18_counterfactual:m18_counterfactual_v1#0
  operation_id collisions: {'m18_counterfactual:m18_counterfactual_v1#0': 2}
```

and `CoverPipeline._m18_reading`, which matched on that id and returned the
first hit, was shown to mis-attribute outright:

```
hn1's own true reading : SUPPORT / parse UNPARSEABLE
what the seam recorded : {'structural_outcome': 'CONTRADICT', 'errors': ()}
```

**Reproduced.**

---

## 3. C-01 fix

The identity is fixed **at the owning Module 18 layer**, and propagated. No
fuzzy matching, no positional matching, no second identity scheme.

- `EligibleCheck.check_id` (new) is Module 18's canonical logical check
  identity: `<mechanism>[:<target>][:<near-miss class>]`.
- `BidirectionalCheckRequest.operation_id` is now built **on** it:
  `m18:<check_id>:<template>#<sample>`. Template and sample stay in, because
  two renderings of one check really are two operations with two prompts and
  two costs — but the check is now named.
- `action_catalog.m18_actions` no longer rebuilds the id from the same parts;
  it namespaces the owner's: `action_id = f"M18:{check.check_id}"`. Two
  constructions of one identity is how they drift, and that drift was C-01.
- `_m18_reading` matches on `operation_id` and **raises
  `AccountingInvariantError` if two records share it**. Silently taking the
  first match is what made C-01 invisible; an ambiguous identity is now a
  contradiction, not a tie to be broken.

Byte-compatibility: the emitted `action_id` strings are unchanged
(`M18:COUNTERFACTUAL:albert einstein:hn0`), so F-12 and N-02 are preserved
exactly.

---

## 4. Exact M18 canonical check identity

One identity, five places:

| stage | value |
|---|---|
| owner | `EligibleCheck.check_id` = `COUNTERFACTUAL:albert einstein:hn0` |
| Layer-6 catalogue | `action_id` = `M18:COUNTERFACTUAL:albert einstein:hn0` |
| request | `operation_id` = `m18:COUNTERFACTUAL:albert einstein:hn0:m18_counterfactual_v1#0` |
| execution result | `origin_event_id` derived from `(model_id, operation_id, prompt_sha256, sample_index)` |
| telemetry | `operation_id` = `<row>:<round>:M18:COUNTERFACTUAL:albert einstein:hn0` |

Pinned by `test_the_near_miss_class_is_part_of_module_18s_own_check_identity`,
`test_the_request_identity_carries_the_canonical_check_identity`,
`test_the_layer6_action_id_is_module_18s_identity_namespaced`.

---

## 5. Two same-kind / different-near-miss outcome attribution

Deterministic scripted case: the runtime answers `EXCLUDED` for `hn0` and
unparseable garbage for `hn1`, keyed on the request's own `view_id`. Through
the real pipeline on two relations:

```
row 200 awardWonBy
  M18:COUNTERFACTUAL:albert einstein:hn0  outcome='NEAR_MISS_RELATION'  errors=()
  M18:COUNTERFACTUAL:albert einstein:hn1  outcome=''                    errors=('parse:MALFORMED',)
row 377 personHasCityOfDeath
  M18:COUNTERFACTUAL:paris:hn0            outcome='NEAR_MISS_RELATION'  errors=()
  M18:COUNTERFACTUAL:paris:hn1            outcome=''                    errors=('parse:MALFORMED',)

    PASS  action_id A != B
    PASS  request identity A != B
    PASS  hn0 outcome == NEAR_MISS_RELATION      PASS  hn0 has no error
    PASS  hn1 outcome empty (malformed)          PASS  hn1 carries parse:MALFORMED
    PASS  no cross-contamination
    result identities unique: op=True origin=True
```

Each record receives its own `structural_outcome`, its own `errors`, and its own
provenance (`prompt_sha256` differs, hence distinct `origin_event_id`).

---

## 6. Result-merging regression

Unchanged and re-verified on three multi-round queries: round-1 results survive
rounds 2 and 3 (3 M17 entries and 3 M18 records merged into one result object
per query, not overwritten), M17 target ids unique in every merged set, and
**operation-id collisions are now zero in every merged set** — the property
C-01's mis-attribution depended on is gone at the source rather than worked
around at the reader.

## 7. Bridge / evidence regression

`ProductionEvidenceBridge` is untouched. Its four rules hold: shadow mutates
nothing, an absent candidate is never minted, only RESOLVED checks produce
edges, and duplicate edge ids are refused. No candidate carries a duplicate
evidence tuple in any probed row. The `M18_*` independence groups (N-01) still
sit in their own groups; `m(o)` is unchanged at 3/3/3/4/5/6 across the six
relations, so `q(o) = g(o)/m(o)` cannot move.

**One evidence-layer defect was found and fixed — see §21.**

---

## 8. C-03 reproduction

Real runner, row 100 fails row-locally, then a resume in which it succeeds:

```
attempt 1: exit 1  rows_completed=2  failed_rows=[100]
attempt 2: exit 1  rows_completed=3  failed_rows=[100]
           telemetry rows [0,100,200]  predictions 3  sufficiency PASS
           gate: ['1 row(s) failed and were omitted from telemetry']
```

The row was complete, in telemetry, in predictions and calibration-sufficient,
and the gate still blocked on it — with a message that was by then false. Exit 0
was unreachable for the rest of the run. **Reproduced.**

---

## 9. Unresolved failure vs historical attempt

Two questions, two fields, everywhere:

| concept | where | meaning |
|---|---|---|
| `unresolved_failed_rows` | checkpoint, `RunCounters`, manifest, gate | rows that failed and have **not** since completed — the only thing that blocks |
| `failure_history` | checkpoint, manifest | every failed attempt, with `row_index`, relation, subject, error, calls burned, and a `resolved` flag; never pruned |
| `failed_attempts` | `RunCounters`, manifest | cumulative count of attempts that failed |
| `failed_attempt_calls` | `RunCounters`, manifest | cumulative wasted spend, **outside** the committed totals |

`CHECKPOINT_VERSION` moved to `collection-checkpoint-v2` because the payload
shape changed; `RunCounters.rows_failed`/`failed_row_calls` were renamed to
`unresolved_failed_rows`/`failed_attempt_calls` so a stale reader cannot
misread them.

## 10. Successful retry clears the active failure

On commit, the row is discarded from the unresolved set, its history entries are
marked `resolved: True`, and `rows_completed` is recomputed as `len(completed)`
— a function of the commit boundary rather than an independent tally.

## 11. Failed-attempt call diagnostics

Wasted spend never enters the committed accounting. Measured across the retry:

```
attempt 1: committed calls 55   failed_attempt_calls 10
attempt 2: committed calls 76   failed_attempt_calls 10   (unchanged)
```

76 = 55 + 21 for row 100's successful attempt, counted once. The 10 burned calls
remain visible and remain separate.

## 12. Final gate after a successful retry

```
attempt 2: exit 0
  status                : complete
  rows_completed        : 3
  unresolved_failed_rows: []
  failed_attempts       : 1
  failure_history       : [(100, resolved=True, 10 calls)]
  telemetry rows        : [0, 100, 200] (77 lines, 0 duplicates)
  predictions           : 3
  coverage executed     : 13
  sufficiency ok        : True
  gate                  : []
  run directories       : 1
```

Pinned by `test_a_retried_row_stops_being_an_unresolved_failure`, which asserts
both directions: the gate passes **and** the history is still there.

---

## 13. C-04 reproduction

`os._exit(137)` after a telemetry flush for a row the checkpoint had not
accepted:

```
after crash : checkpoint completed=[0], telemetry rows [0, 100]
on resume   : COLLECTION ABORTED
              TelemetryError: duplicate telemetry identity 100:1:...
              exit 1
```

Every subsequent resume aborted at the same point. **Reproduced.**

---

## 14. Resume reconciliation authority

`src/cover_kbc/controller_calibration/recovery.py` (new).
`checkpoint.completed_rows` is the durable commit boundary and the sole
authority. Order in the runner is now: **validate identity → reconcile →
open for append**, so nothing is repaired against a checkpoint describing a
different run. Three rules: the checkpoint is the only authority; committed rows
are never touched; an inconsistency it cannot explain is `ResumeRefused`, not
repaired.

## 15. Telemetry rollback

Records whose `row_index` is absent from `completed_rows` are dropped and the
file rewritten atomically (`.partial` + `replace`, so recovery is itself
crash-safe). A final line torn by the kill is dropped; a broken line anywhere
else is corruption and is refused rather than guessed at.

## 16. Prediction rollback

Predictions carry no `row_index` — the file is the official JSONL contract and
must stay that way. Exactly one row can be in flight, so any excess line is
that row's: the file is truncated to `len(committed_rows)` and the survivors are
then checked as a **multiset** of `(SubjectEntity, Relation)` against the
committed rows. Multiset, not positional, because completion order is not row
order after a retry. A mismatch is `ResumeRefused`.

## 17. Accounting reconciliation

`RunCounters` are restored from the checkpoint, which is written atomically with
`completed_rows`, so committed accounting is checkpoint-consistent by
construction. The ordering defect that made this untrue was found by the
crash-window test and fixed: `rows_completed` was incremented *before* the row
committed, so an interrupted commit left the checkpoint claiming a row it had
not accepted (observed as `4/3 rows completed`). It is now
`len(completed)`, and the only fallible step in the commit block
(`physical_delta`) runs before any counter moves.

## 18. Coverage reconciliation

Executed counts are rebuilt from the reconciled telemetry, which is the
canonical record of what ran, rather than subtracted by hand.
`legal_opportunities`/`surfaced` are left as the persisted diagnostics they are
— they feed only the printed table and the `>0` status test, and any inflation
there is fail-closed (it can only turn a family into `LEGAL_BUT_UNEXECUTED`).

## 19. Crash-window tests

Four hard-kill windows, each created with `os._exit(137)` inside the row commit
and then resumed through the real `main()`:

| window | interrupted state | resume result |
|---|---|---|
| A — telemetry flushed only | checkpoint `[0]`, telemetry rows `[0,100]`, 1 prediction | **exit 0**, 1 record rolled back, replayed |
| B — telemetry + coverage | same on disk | **exit 0**, 19 records rolled back, replayed |
| C — + prediction written | checkpoint `[0]`, 2 predictions | **exit 0**, 19 records + 1 prediction rolled back |
| D — + torn `checkpoint.json.partial` | last valid atomic checkpoint is authority | **exit 0**, same rollback |

Every one ends at `rows_completed 3`, `unresolved 0`, telemetry rows `[0,1,2]`,
77 lines, **0 duplicate identities**, 3 predictions, one run directory,
`sufficiency ok: True`, `gate: []` — byte-equivalent to an uninterrupted run.
**No manual file deletion was required in any case.** Pinned in-suite by
`test_a_kill_inside_the_row_commit_recovers_automatically[telemetry|coverage|prediction]`.

## 20. Multi-resume regression

Audit 0043's guarantees are intact. fresh → fatal → resume → fatal → resume →
fatal → resume → complete → resume-when-complete:

```
one run directory; one stable run_id in all 221 records; 0 duplicate identities;
6 predictions for 6 rows; accounting 151 calls / 27 235 prompt tokens
  — exactly equal to the uninterrupted run; coverage cumulative and identical;
resume-when-complete does nothing.
```

`test_reconciliation_leaves_a_clean_resume_untouched` asserts that a clean
resume rolls back nothing, so recovery can only ever remove uncommitted
material.

---

## 21. C-05 reproduction — and what it uncovered

Reproduced exactly: with a complete, valid telemetry fixture, wiping every
candidate-effect list returned **PASS**, and setting `redundancy=None` on every
record returned **PASS** — while still printing "redundancy is recorded whenever
the action had a candidate surface". The `redundancy` check only fired when
`candidates_named` was non-empty, and that list is empty on 30/30 real records,
so the check was vacuous.

**A further defect surfaced while fixing it.** `EvidenceGraph.add_verification`
appended the verdict to `candidate.verifications` **before** attaching the edge.
`_attach` refuses a duplicate edge id by raising, so on every Layer-4
re-integration the edge was correctly refused and the reading was kept anyway:

```
before:  r1 'albert einstein' -> ('INVALID',)
         r2 'albert einstein' -> ('INVALID','INVALID')      <- nothing applied
         r3 'albert einstein' -> ('INVALID','INVALID','INVALID')
         r4 (bridge wrote nothing at all) -> 4 copies
```

The per-action candidate diff therefore saw a "new" INVALID on every subsequent
action and reported candidates as contradicted by actions that had not touched
them. Bounded multi-round collection (F-04) re-integrates after every action,
which turned a latent ordering bug into a measurable one — 16 of 30 executed
actions carried an over-reported contradiction.

Fixed by attaching first and recording second, so a duplicate is refused whole.
`_attach` raises before it mutates, so the reordering is total. Predictions
cannot have been affected — every prediction-affecting reader takes
`verifications[-1]`, a membership test or a `max`, and the duplicates were
identical copies; the only length-sensitive reader (`controller.py:630`) runs in
a phase the collection reaches before any Layer-4 action. After the fix:

```
after:   r1 'albert einstein' -> ('INVALID',)
         r2 'marie curie'     -> ('INVALID',)      (einstein unchanged)
         r3 both -> raw support 7->8, labels unchanged
         r4, r5 -> no change at all
```

and `candidates_contradicted` falls from 16/30 to a truthful 7/30.

## 22. Explicit candidate-effect measurement semantics

`ActionOutcome.candidate_effect_measured: bool` (default **False**, so an absent
measurement is never the silent case) states whether the seam performed the
diff. `execute_action` sets it True for an executed action; an unexecuted action
publishes no effect at all, and the default then carries the truth for an
unexplored branch. Observed: `True` on 30/30 executed, `False` on 191/191
unexecuted.

Four empty candidate lists are now a real observation when the flag is True, and
a hole when it is False. The validator requires the flag on every executed
action and **never** requires a non-empty list.

## 23. Explicit redundancy measurement / N-A semantics

`RedundancyStatus` — a typed enum, no sentinel values:

| member | meaning | `redundancy` |
|---|---|---|
| `MEASURED` | the action had a candidate surface and it was measured, **including 0.0** | a float |
| `NOT_APPLICABLE` | the action touched and named nothing; redundancy is not a question about it | `None` |
| `UNMEASURED` | nothing measured it — never valid for an executed action | `None` |

`ActionOutcome.__post_init__` enforces the shape (only `MEASURED` carries a
value; `from_json` refuses an unknown member). Whether a *given record* was
allowed to be unmeasured is a sufficiency question, deliberately left to the
validator — that is what makes the validator testable against a record that lost
its instrumentation.

`ActionOutcome.candidates_touched` was added so the `NOT_APPLICABLE` claim is
checkable from the record: the surface is `candidates_touched + candidates_named`
— the bridge's own account of what the action wrote or named — and deliberately
**not** the graph diff, which answers a different question. Basing the check on
the graph diff was tried and rejected: it flagged truthful records where a
counterfactual changed a candidate's state without the bridge writing for it.

Observed on the six-relation run: `MEASURED` 11, `NOT_APPLICABLE` 19,
`UNMEASURED` 0; `candidates_touched` non-empty on exactly the 11 `MEASURED`.

## 24. Adversarial sufficiency cases

All six required cases, plus shape invariants, plus the whole prior suite:

| case | expected | observed |
|---|---|---|
| A — erase candidate-effect measurement presence | FAIL | **FAIL** |
| B — measurement present, all candidate lists empty | PASS | **PASS** |
| C — erase redundancy measurement (→ `UNMEASURED`) | FAIL | **FAIL** |
| D — measured redundancy `0.0` | PASS | **PASS** |
| E — explicit `NOT_APPLICABLE`, no candidate surface | PASS | **PASS** |
| F — false `NOT_APPLICABLE` with a candidate surface | FAIL | **FAIL** |
| `MEASURED` with no value | refused | **schema reject** |
| `NOT_APPLICABLE` carrying a value | refused | **schema reject** |
| unknown `RedundancyStatus` from JSON | refused | **schema reject** |
| `MEASURED` with no surface to have measured against | FAIL | **FAIL** |

Prior adversaries all still refuse: empty telemetry, only-unexecuted records,
mixed schema versions, repr `program_type`, non-canonical family, missing
`action_id`, address-shaped `operation_id`, unmeasured state, no available §15
component, M17 verdict *and* errors stripped, M18 reading *and* errors stripped,
missing `spend_class`, claimed `reserved_class`, zero prompt tokens on a charged
action, no successor chain, broken role partition. An all-zero but genuinely
*measured* state is still accepted.

## 25. Telemetry schema version

Bumped intentionally: `train-telemetry-v2` → **`train-telemetry-v3`**, because a
v2 reader would misread the new fields in the dangerous direction. Propagated
through `RunIdentity.telemetry_schema_version` (so every pre-existing checkpoint
is refused), the manifest, the writer, the reader, the sufficiency validator and
the tests. `CHECKPOINT_VERSION` moved to `collection-checkpoint-v2` for the same
reason. No real TRAIN artifact exists, so no backwards compatibility is owed —
and the semantics did not change silently under the old version.

---

## 26. M19 / ΔR regression

Unchanged. Six-relation run: 8 distinct residual values (0.0 … 0.8333), ΔR ≠ 0
on 10/30 executed actions, `measured=False` on 0 executed, three distinct
`available_components` patterns including one where `disagreement` reads 0.0
*and* is absent. `post(a) == pre(b)` on **24/24** transitions.

## 27. H / ΔH status — C-02 deliberately not touched

`H`, `coverage_q`, acquisition-group semantics and the `M18_*` independence
groups are **unchanged**. ΔH remains 0 on 30/30 executed actions, which Audit
0043 established is structural: `H` is a function of acquisition-group coverage,
and no M17/M18 action can change an acquisition group or mint a candidate.
Manufacturing a non-zero ΔH would be altering the architecture to satisfy a
metric. The measured zeros are kept.

**C-02 remains open as a proposal/paper interpretation item before FULL
VALIDATION and paper finalisation:** §17's `+γ·ΔĤ` term will be calibrated to
identically zero over the collected action space, and the paper must say so
rather than present a six-term utility calibrated on TRAIN.

## 28. Prompt-token regression

Unchanged end to end. Whole-run figures are bit-identical to Audit 0043's:
telemetry Layer-4 17 533 tokens over 30 charged actions; `accounting.json`
27 235 prompt tokens, 220 generated, 151 physical (80 enumerate + 71 verify),
role partition sums on every record.

## 29. Deterministic identity regression

Two separate `python` processes: full telemetry **byte-identical** once `run_id`
is removed (`sha256 d79c4de35abcc757`), 221 identical `operation_id`s, no
address-shaped id anywhere. `M18:COUNTERFACTUAL:<target>:hn0` / `:hn1` remain
distinct and stable.

## 30. Committed TRAIN config and readiness

`configs/experiments/cover_kbc_v2_train_collection.yaml` is **unmodified by this
milestone**. It remains the only committed profile that passes readiness; the
frozen VAL target and every mutation (each required module disabled, each
forbidden module enabled, split forced to `val`, a broken model profile) is
still refused. The gate still fires before any runtime is built.

## 31. Family-coverage regression

Unchanged and re-verified. Six-relation run observes all four required
families. With Module 18's `REVERSE` mechanism unwired and **no `--limit`**, the
full-run gate still fails closed:

```
BLOCKER action family REVERSE_CHECK was required but no catalogue ever offered it
        - a wiring failure, not a dataset fact
coverage: REVERSE_CHECK = NEVER_SURFACED   integrity_ok = False   exit 1
```

## 32. TRAIN-gold isolation

Intact. `Query` still carries exactly `(subject, relation, row_index)`. The new
fields are all model/evidence/bridge products: `candidates_touched` comes from
`BridgeReport.candidates_touched`, `candidate_effect_measured` from whether the
seam ran its own diff, `check_id` from Module 18's own declaration. The only
`ObjectEntities` occurrence on the collection path remains the prediction
writer. Nothing in `recovery.py` reads the dataset except `(subject, relation)`
for committed rows, which the runner already holds.

## 33. Closed-book / no-training / model-profile regression

Grep over the entire `+` side of the working diff for `requests`, `urllib`,
`httpx`, `aiohttp`, `socket`, `wikipedia`, `wikidata`, `bm25`, `faiss`,
`chroma`, `pinecone`, `elasticsearch`, `serpapi`, `duckduckgo`, `web_search`,
`optimizer`, `.backward(`, `torch.optim`, `requires_grad`, `lora`, `peft`,
`fine-tun`, `gradient`, `.train()`: the only hits are a local variable named
`requests` in a new test. Model ids, revisions, tokenizers, prompts, scoring,
decoding, quantization, generated-token limits and loading semantics are
untouched — `configs/` has no diff in this milestone, and `models/` carries only
the previous milestone's prompt-token instrumentation. Profile remains
Mistral-Small-3.2-24B `95a6d26c…` + Qwen3.5-4B `851bf6e8…`, **28 671 226 368 ≤
32B**. The real-weight smoke will test the same target profile.

---

## 34. Scripted committed-config run

Real `main()`, committed config, scripted runtimes, one TRAIN row per relation
(0, 100, 200, 210, 310, 377), artifacts read back from disk:

```
exit code            : 0
schema               : train-telemetry-v3 throughout
telemetry            : 221 records (30 executed, 191 legal-but-unexecuted)
relations            : all six
program types        : LARGE_OPEN_SET, NULL_SINGLE, NUMERIC, SMALL_SET
families             : all four OBSERVED
actions/query        : 4, 4, 5, 5, 6, 6      transitions: 24, post(a)==pre(b) on all
residual             : 8 distinct values, ΔR≠0 on 10/30
candidate effect     : measured 30/30 executed, 0/191 unexecuted
redundancy           : MEASURED 11, NOT_APPLICABLE 19, UNMEASURED 0
M18 near-miss ids    : M18:COUNTERFACTUAL:<target>:hn0 / :hn1, distinct
prompt tokens        : 17 533 (telemetry) / 27 235 (accounting)
role partition       : sums on every record
predictions          : 6 for 6 committed rows
unresolved failures  : 0        failed attempts: 0
manifest             : status=complete, gate_blockers=[]
sufficiency          : PASS (15 satisfied checks, 0 blockers)
```

Plus, from disk in the same battery: the C-01 divergent-outcome case (§5), the
C-03 retry case (§12), all four C-04 crash windows (§19), multi-resume (§20),
two-process determinism (§29), the coverage gate (§31), and the M20 derivability
reconciliation (`sum of per-query hard_calls = 151 = accounting.json`).

## 35. pytest

`python -m pytest -q` → **3 069 passed, 3 skipped** (was 3 053; +16 net from the
new C-01/C-03/C-04/C-05 regression tests).

Five test files were updated, each for a reason:
`test_layer6_integration.py` now builds a **real** `EligibleCheck` instead of a
duck-typed stub — a stub with the right attribute names but no `check_id` was
testing a parallel object, not the contract; `test_calibration_sufficiency.py`
and `test_controller_calibration_telemetry.py` carry the v3 fields;
`test_collection_failure_resume.py` uses the split failure fields; and
`test_the_production_m18_types_are_unmodified` — a `git status --porcelain`
assertion that fails on any legitimate edit and says nothing about behaviour —
was converted to the behavioural contract it was protecting, exactly as Audit
0042 §12 did for two others.

## 36. pyflakes

`python -m pyflakes src/ tests/ scripts/` — clean, exit 0.

## 37. Benchmark immutability

`git diff -- benchmark/` — empty.

---

## 38. Remaining P0 / P1

**None.**

## 39. Remaining P2 / P3

| ID | Sev | status |
|---|---|---|
| C-01 | P2 | **FIXED** (§3–§5) |
| C-02 | P2 | **OPEN BY DESIGN** — structural, not an instrumentation defect; a paper/proposal item before FULL VALIDATION (§27) |
| C-03 | P2 | **FIXED** (§9–§12) |
| C-04 | P2 | **FIXED** (§14–§19) |
| C-05 | P2 | **FIXED** (§22–§24) |
| C-06 | P3 | redundancy is still `{None, 1.0}` in practice; the raw `candidates_touched`/`candidates_named` identities are now recorded, so the definition can be changed offline without a rerun |
| C-07 | P3 | `cache_hits` still always 0 and `parse_ok` still always True; neither is in `M20_REQUIREMENTS` or `M21_REQUIREMENTS` |
| C-08…C-13 | P3 | unchanged; out of scope by instruction |
| F-14, F-15, F-17, F-18, F-23 | P3 | unchanged, still open, still harmless under the target profile |
| F-11, F-22, F-24 | — | deferred; FULL VALIDATION blockers only |
| **C-14 (new)** | **P2 → FIXED** | duplicate verification readings on re-integration, found while fixing C-05 (§21). Telemetry-only; predictions provably unaffected. Fixed in `EvidenceGraph.add_verification` |
| **C-15 (new)** | **P2 → FIXED** | `rows_completed` incremented before the row committed, so an interrupted commit left the checkpoint over-claiming (observed `4/3`). Found by the C-04 crash-window test; now `len(completed)` (§17) |

## 40. Exact blockers before the real-weight smoke

**None.**

## 41. Exact blockers before the 477-row TRAIN collection

**None** beyond the smoke itself. `models/base.py` and `models/huggingface.py`
remain on the audited weight-loading path from the previous milestone, so the
operational precondition Audit 0042 §12 and Audit 0043 §35 both stated still
applies: run the real-weight smoke once on the target profile first.

## 42. Exact blockers before FULL VALIDATION

1. Real TRAIN-derived M20 (`RelationBudgetCalibration`) and M21
   (`HistoricalBinPackage` + `PlannerCalibration`) artifacts — this collection
   produces the observations; the derivation is a later milestone.
2. **F-24** — an entrypoint constructing the pipeline with
   `IntegrationMode.PRODUCTION`.
3. **F-11** — module configs able to express `production`; `MicroPlannerConfig`
   and `RelationBudgetConfig` still raise on any non-`shadow` mode.
4. **F-22** — `layer6_integrator` supplied, or M21 always returns
   `STOP/NO_LEGAL_ACTION`.
5. A validation config (`split: val`, M11–M21 enabled, both artifacts declared)
   reaching `FULL_VALIDATION_READY`.
6. **C-02** disclosed: the γ·ΔĤ term will be identically zero in every bin.

`FULL_VALIDATION_READY` is **not** claimed. No real-weight smoke has been run.
No TRAIN, VAL or TEST run has been performed.

---

## 43. Verdict

> ## PASS — READY FOR INDEPENDENT PRE-SMOKE REVIEW

C-01 is fixed at the owning layer with one canonical Module 18 check identity
running from catalogue to telemetry, proved by two same-mechanism checks with
different near-miss classes recording their own divergent outcomes and errors.
C-03 separates unresolved failure from failure history, so a retried row
completes the run at exit 0 while its earlier attempt and its wasted calls stay
visible and stay out of the committed accounting. C-04 recovers automatically
from all four commit-crash windows with no manual file editing, byte-equivalent
to an uninterrupted run, with the checkpoint as the sole authority. C-05
replaces inference with typed measurement-presence semantics, and the six
required adversarial cases now behave as specified — erased instrumentation
fails, genuine zeros pass.

Two further defects surfaced while fixing those four and were fixed with them:
duplicate verification readings accumulating on re-integration, which had been
over-reporting contradictions in 16 of 30 executed actions, and a
`rows_completed` counter that moved before its row committed.

Zero unresolved P0. Zero unresolved P1. 3 069 tests pass, pyflakes is clean, the
benchmark is untouched, and the committed TRAIN config, readiness gate, M19
state, per-action transitions, prompt-token accounting, deterministic identity,
family coverage, gold isolation and the closed-book/no-training/parameter-budget
boundaries are all unchanged and re-verified.

C-02 was deliberately not altered: ΔH is genuinely zero over this action space,
and the measured zeros are kept.

---

*No commit, no push. No real weights, no TRAIN, no VAL, no TEST were run.*
