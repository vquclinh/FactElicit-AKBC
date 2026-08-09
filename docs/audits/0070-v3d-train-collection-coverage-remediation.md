# Audit 0070 - V3D Train Collection Coverage Remediation

## Scope

V3_COVERAGE_BASE_SHA:

`c9e36c4d431de3bea25b9247dffe021728ff1d26`

No commit or push was made.

No heavyweight real model was run in this environment. All execution tests used
scripted runtimes.

This audit remediates the real-weight V3 TRAIN collection coverage failure from
the full 477-row run at source commit
`c9e36c4d431de3bea25b9247dffe021728ff1d26`.

The failed real command was:

```bash
python -u scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --output-dir /content/v3_train_collection_full
```

That run processed `477 / 477` TRAIN rows with `unresolved failed: 0` and
`failed attempts: 0`, but five production-legal V3 families were never
executed:

- `INDEPENDENT_RECALL`: legal 77, executed 0
- `LISTING_ELIMINATION`: legal 6, executed 0
- `SEMANTIC_VERIFY`: legal 6, executed 0
- `SET_EXPANSION`: legal 10, executed 0
- `UNARY_VERIFY`: legal 10, executed 0

The final `TRAIN CALIBRATION COLLECTION INCOMPLETE` gate was correct. The bug
was upstream TRAIN exploration starvation, not neural runtime, OOM, query
execution, evaluator, or gold joining.

## Files Changed

- `configs/experiments/cover_kbc_v3_train_collection.yaml`
- `scripts/run_train_calibration_collection.py`
- `src/cover_kbc/controller_calibration/collection_policy.py`
- `src/cover_kbc/controller_calibration/readiness.py`
- `src/cover_kbc/controller_calibration/recovery.py`
- `src/cover_kbc/controller_calibration/supplemental_coverage.py`
- `src/cover_kbc/pipeline.py`
- `src/cover_kbc/v3_core/relation_programs.py`
- `tests/test_controller_calibration_collection.py`
- `tests/test_v3d_activation.py`

## Root Cause

The old policy, `collect-v1`, was only a per-query family rotation. It counted
every catalogue member as legal, returned a deterministic ordered slice, and the
pipeline consumed only the head action for that round. It had no run-wide target
deficit, no distinction between "observed once" and "sufficiently observed",
and no way to prefer a family that had repeatedly been legal but still had zero
successful observations.

The V3 loop is bounded to three rounds per query. Many V3 actions are
state-changing: a recall action can move `SINGLE_LOW_SUPPORT` to
`MULTIPLE_CONFLICTING` or `STABLE_VERIFIED`; listing elimination can reject the
primary stock candidate; set expansion can change the open-set state. When a
preceding action changed the state, the later family disappeared from the next
catalogue before it became the selected head.

There was a second source-level hole: the pipeline checked V3 physical
affordability after the selector returned one action. A legal but unaffordable
head caused the loop to stop instead of letting collection choose another legal
and affordable under-covered action. The ledger had already counted the full
catalogue as legal, so the final report could show legal opportunities with
zero executions.

Family-specific trace:

| Family | Relations that can offer it | Failure states that can offer it | Where legal catalogue appears | What preceded/starved it | Executable? | Predicate fix |
|---|---|---|---|---|---|---|
| `INDEPENDENT_RECALL` | `hasArea`, `hasCapacity`, `personHasCityOfDeath` | `SINGLE_LOW_SUPPORT` | `legal_action_families` -> `build_v3_action_catalog` generation action | `ALTERNATIVE_RECALL`, `DEFINITION_RECALL`, or `ATTRIBUTE_DECOMPOSITION` could run first and change the state within the three-round bound | yes, M12/M14/M18 generation seam | no broad executable mismatch found |
| `LISTING_ELIMINATION` | `companyTradesAtStockExchange` | `HIGH_FP_RISK` | stock high-FP hgraph -> M17 verifier action | competed with `SEMANTIC_VERIFY`; post-selection affordability/break or state change could consume the rare opportunity | yes, M17 rejection-first verifier seam | no broad executable mismatch found |
| `SEMANTIC_VERIFY` | `companyTradesAtStockExchange`, `personHasCityOfDeath` | `HIGH_FP_RISK`, `SEMANTIC_AMBIGUITY` | M17 verifier action when a primary hypothesis exists | lost to listing elimination on stock rows and to earlier state-changing actions elsewhere | yes when a primary exists | removed `SEMANTIC_VERIFY` from `NULL_UNRESOLVED`, where no primary exists |
| `SET_EXPANSION` | `awardWonBy` | `SET_GROWING` | M13 generation action with bounded seen-set suppression | could lose the scarce award opportunity to post-selection budget break or be followed by state changes before companion actions ran | yes, M13 generation seam | no broad executable mismatch found |
| `UNARY_VERIFY` | `awardWonBy` | `SET_GROWING` for actual open-set award states | M17 unary verifier action over primary candidate | `SET_EXPANSION` sorted before it in equal-coverage states; without run-wide deficit, later award rows repeated the same choice | yes, M17 score-label seam | no broad executable mismatch found |

`ActionHistory` was not the root cause for the five missed families. It only
marks a `(failure_state, family)` redundant after a zero-material-novelty
execution. These families were legal and unexecuted; history had no successful
execution to mark redundant.

The three-round query bound contributed because only one action is executed per
round and state is recomputed after every action. The remediation keeps the
existing bound and schedules better within it. No production hard cap was
raised.

## New Policy

The TRAIN collection policy is now:

`collect-v2-coverage`

Production inference policy is unchanged. V2 prediction is unchanged. V3
production M21 semantics are unchanged.

At each V3 TRAIN collection decision:

1. V3 derives the current failure state and owner-declared action catalogue.
2. `ActionHistory` and owner catalogue construction remove impossible or
   redundant actions.
3. The V3 TRAIN loop computes the currently executable and affordable subset
   under the physical per-query budget.
4. The selector receives both the full legal catalogue and the selectable
   subset.
5. The coverage ledger counts legal opportunities from the full catalogue.
6. Selection may return only currently selectable actions.
7. Positive-deficit families are preferred by lowest coverage ratio, then
   successful observations, per-query count, deficit, and family name.
8. Once selectable families have no positive deficit, the old deterministic
   per-query ordering is used for remaining opportunities.

This is deterministic and uses no randomness, gold, VAL, or TEST.

## Coverage Targets

V3 TRAIN collection now has an explicit configured target:

```yaml
train_collection:
  policy: collect-v2-coverage
  coverage_target_per_family: 10
```

For each family:

`target = min(configured_target, legal_opportunities_seen)`

Status meanings:

- `OBSERVED_SUFFICIENT`: TRAIN supplied at least the configured target and
  successful observations met it.
- `OBSERVED_LIMITED_BY_AVAILABLE_OPPORTUNITIES`: TRAIN supplied fewer than the
  configured target and every legal opportunity was successfully observed.
- `LEGAL_BUT_UNDERCOVERED`: TRAIN supplied legal opportunities but successful
  observations did not meet the target.
- `NEVER_LEGAL`: no legal TRAIN opportunity was observed.

`LEGAL_BUT_UNDERCOVERED` remains a hard final-gate blocker. A family legal many
times and executed zero times is still blocked, now with target context rather
than only a zero-execution check.

Legacy non-V3 collection keeps a target of 1 so V2 collection behavior is not
tightened by this V3 remediation.

## Coverage Artifacts

The runner still writes `action_coverage.json`. V3 runs now also write:

- `v3_action_coverage.json`
- `v3_action_coverage.csv`

The persisted table includes:

`action_family`, `legal_opportunities`, `executed`, `successful`, `failed`,
`target`, `coverage_ratio`, `status`

It also persists relation by action-family coverage. These artifacts contain no
gold.

## Checkpoint And Resume

`RunIdentity.collection_policy_version` now records `collect-v2-coverage`, so a
V3 resume cannot splice old `collect-v1` observations into the new policy.

On resume, V3 loads `v3_action_coverage.json`; legacy collection loads
`action_coverage.json`. If a checkpoint has committed rows and the committed
coverage artifact is missing, resume is refused. Reconciliation still treats the
checkpoint as the transaction boundary and rebuilds executed coverage counts
from committed telemetry after truncating any uncommitted tail.

Relation-by-family executed counts are rebuilt from telemetry as well. Legal
opportunity counts remain in the committed coverage artifact; corruption fails
closed through JSON/schema loading rather than guessed recovery.

All artifacts remain under the selected `--output-dir` run root:

- checkpoint
- predictions
- telemetry
- accounting
- inference telemetry
- V3 pre-M8 graphs
- V3 final graphs
- V3 action effects
- V3 coverage reports
- manifest

No Google Drive path is hardcoded in source.

## Supplemental Coverage

`src/cover_kbc/controller_calibration/supplemental_coverage.py` adds source-ready
support for future supplemental collection:

- plans supplemental TRAIN rows from under-covered families using only
  `Query(row_index, subject, relation)` and relation-to-family capability;
- never reads `ObjectEntities`;
- does not mutate a base collection;
- merges base and supplement ledgers offline after validating matching targets;
- validates that merged V3 action-effect identities are not duplicated.

This supports a future base collection plus deterministic supplement plus
offline merge workflow. Calibration remains blocked until the merged corpus
sufficiency passes.

## Relation Behavior

- `hasArea`: numeric actions still use the existing numeric parser and
  selection. Scripted telemetry proves coverage can select
  `INDEPENDENT_RECALL` when legal. No majority-vote finalizer was introduced.
- `hasCapacity`: definition and qualifier semantics are preserved; scripted
  telemetry executes independent recall after generic numeric actions have
  observations.
- `awardWonBy`: `SET_EXPANSION` and `UNARY_VERIFY` both execute under the
  three-round bound when under-covered. Seen-set suppression remains bounded.
- `companyTradesAtStockExchange`: `LISTING_ELIMINATION` and `SEMANTIC_VERIFY`
  both execute in scripted stock high-FP states. Broad expansion remains blocked
  when stock candidates already exist. Rejection-first semantics are unchanged.
- `personHasCityOfDeath`: `ATTRIBUTE_DECOMPOSITION` remains executable and
  promotes only death city while using non-target slots as contradictions.
- `countryLandBordersCountry`: frozen/conservative behavior remains unchanged;
  no expansion actions are invented.

## Precheck

Added:

```bash
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --output-dir /tmp/v3_train_precheck \
  --precheck-only
```

Result:

`status: PASS`, `model calls: 0`

The precheck verifies:

- all ten required V3 families have executable-owner registrations;
- the coverage scheduler recognizes every family;
- target configuration is valid;
- output root is writable;
- V3 production remains `NOT_READY`;
- V3 TEST remains blocked.

## Production And V2 Non-Regression

V3 production selection still routes through `_plan_next_action` / M21 when
production readiness is true. The new selectable-catalogue path is used only by
the injected TRAIN selector and the V3 TRAIN loop.

V2 production prediction path is unchanged. Legacy collection keeps a one-hit
coverage target so existing V2 collection persistence/resume tests continue to
pass.

No new model, model revision change, evaluator change, benchmark data change,
M20/M21 artifact mutation, or historical V2 calibration mutation was made.

## Tests

Targeted tests:

```bash
python -m pytest tests/test_controller_calibration_collection.py \
  tests/test_v3_core.py tests/test_v3d_activation.py -q -p no:randomly
```

Result: `79 passed in 0.87s`.

Persistence/readiness target:

```bash
python -m pytest tests/test_collection_failure_resume.py \
  tests/test_calibration_sufficiency.py \
  tests/test_controller_calibration_readiness.py -q -p no:randomly
```

Result: `94 passed in 2.34s`.

Full deterministic suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `3559 passed, 4 skipped in 50.58s`.

Full default suite:

```bash
python -m pytest tests/ -q
```

Result: `3559 passed, 4 skipped in 48.96s`.

Static checks:

```bash
python -m pyflakes src/ tests/ scripts/
git diff --check
```

Result: both passed.

Final targeted sanity after the pyflakes-only import removal:

```bash
python -m pytest tests/test_controller_calibration_collection.py \
  tests/test_v3d_activation.py -q -p no:randomly
```

Result: `62 passed in 0.67s`.

## Immutable Artifacts

- `benchmark/evaluate.py` SHA256:
  `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN rows: `477`
- TRAIN SHA256:
  `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- VAL rows: `475`
- VAL SHA256:
  `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- TEST rows: `475`
- TEST SHA256:
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`

Frozen model pair remains:

- Enumerator:
  `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- Verifier:
  `Qwen/Qwen3.5-4B`
  revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- Total:
  `28,671,226,368 / 32,000,000,000`

Historical V2 calibration hashes remain byte-identical:

- `configs/calibration/m20_relation_budget.json`:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- `configs/calibration/m21_historical_bins.json`:
  `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- `configs/calibration/m21_planner_calibration.json`:
  `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

## Next Real-Weight Command

Recommended Colab command with a persistent Drive output root:

```bash
cd /content/FactElicit-AKBC
python -u scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --output-dir /content/drive/MyDrive/Submissions-AKBC/v3_train_collection_collect_v2_coverage
```

Do not pass `--limit` for the authoritative collection.

## Verdict

PASS — V3 TRAIN COLLECTION COVERAGE REMEDIATION READY FOR REAL-WEIGHT RUN
