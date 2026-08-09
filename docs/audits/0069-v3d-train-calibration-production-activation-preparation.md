# Audit 0069 — V3D Train Calibration and Production Activation Preparation

## Scope

V3D_BASE_SHA:

`336ea5ec07ec06bf18c193de74517078d3f9b29e`

This audit covers the source portion of V3D: making V3 action execution real
for TRAIN collection, moving the V3 hypothesis graph into the pre-M8 control
loop for activated V3 paths, extending readiness gates for separate V3
calibration artifacts, and preserving the V2 calibrated baseline.

No commit or push was made.

No full real-weight TRAIN, VAL, or TEST run was performed in this environment.
No V3 M20/M21 calibration artifacts were created or faked.

## Files Changed

- `configs/experiments/cover_kbc_v3_train_collection.yaml`
- `scripts/run_train_calibration_collection.py`
- `src/cover_kbc/control/historical_bins.py`
- `src/cover_kbc/controller_calibration/readiness.py`
- `src/cover_kbc/controller_calibration/sufficiency.py`
- `src/cover_kbc/evidence/graph.py`
- `src/cover_kbc/pipeline.py`
- `src/cover_kbc/v3_core/__init__.py`
- `src/cover_kbc/v3_core/config.py`
- `src/cover_kbc/v3_core/execution.py`
- `src/cover_kbc/v3_core/hypothesis.py`
- `src/cover_kbc/v3_core/relation_programs.py`
- `tests/test_v3_core.py`
- `tests/test_v3d_activation.py`
- `docs/audits/0069-v3d-train-calibration-production-activation-preparation.md`

## Model Contract

The active backbone remains:

- Enumerator: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- Verifier: `Qwen/Qwen3.5-4B`
  revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- Total published parameters:
  `28,671,226,368 / 32,000,000,000`

No third model, fine-tuning path, external factual data, web/RAG/KB lookup, or
Portfolio Direct path was introduced.

## Executable V3 Actions

The new executable seam is `src/cover_kbc/v3_core/execution.py`. It turns V3
relation/failure-state catalogue entries into real owner actions over the
existing graph and runtime APIs.

Action ownership and effects:

| Action family | Relation legality | Owner | Model role | Execution/evidence effect |
|---|---|---|---|---|
| `MULTI_VIEW_RECALL` | relation-conditioned, not borders expansion | M18/M12/M13 by relation | Mistral enumerator | one `GenerationRecord`, existing numeric/entity parsers, Module-3 support edges |
| `INDEPENDENT_RECALL` | non-stock recall where relation permits | M18/M12/M14 | Mistral enumerator | semantically distinct prompt family, support without counting repeats as independent mechanisms |
| `DEFINITION_RECALL` | `hasArea`, `hasCapacity` | M12 | Mistral enumerator | existing numeric parser/unit normalization, definition facet, capacity qualifier preservation |
| `ALTERNATIVE_RECALL` | numeric and award relations | M12/M13 | Mistral enumerator | competing hypotheses or additional set candidates |
| `ATTRIBUTE_DECOMPOSITION` | `personHasCityOfDeath` | M14 | Mistral enumerator | slot parse; only `DEATH_CITY` becomes positive evidence; non-target slots add semantic contradictions to existing candidates |
| `SET_EXPANSION` | `awardWonBy` | M13 | Mistral enumerator | bounded promote-suppress-iterate prompt using compact seen-set text |
| `LISTING_ELIMINATION` | `companyTradesAtStockExchange` | M17 with M15 semantics | Qwen verifier | rejection-first score-label prompt; invalid direct-listing claim rejects the candidate |
| `UNARY_VERIFY` | relation-conditioned | M17 | Qwen verifier | score-label verification, no free-form confidence parsing |
| `SEMANTIC_VERIFY` | relation-conditioned | M17 | Qwen verifier | relation/slot/qualifier-aware score-label verification |
| `CONTRAST_VERIFY` | materially competing hypotheses | M17 | Qwen verifier | deterministic strongest pair only; no O(n^2) verifier sweep |

Every neural action is planned through a `BudgetActionDescriptor`; execution cost
is measured by physical runtime counters before/after the action. The V3 loop
now refuses an action before execution if either planned calls or planned
generated tokens exceed the remaining hard per-query budget.

## Pre-M8 Control Loop

The V3 graph now participates before Module 8 in activated V3 paths:

1. ordinary acquisition/verification builds the current `EvidenceGraph`;
2. Module 16 consensus is built;
3. a `QueryHypothesisGraph` is derived before M8 finalization;
4. V3 failure state determines legal action families;
5. an action is selected by fixed TRAIN collection policy, or by M21 in future
   calibrated V3 production;
6. the owner executes and mutates evidence through existing graph APIs;
7. M16/Layer-4/M19 refresh;
8. bounded iteration repeats;
9. M8 finalizes only after STOP/no legal/no selected/budget exhaustion.

V2 configs do not activate this loop. V3 shadow remains post-hoc observability.

## M21 Integration

V3 actions now project into `PlannerActionCandidate` through
`V3ActionCandidate.to_planner_action()`. Historical bin loading accepts V2
`ActionFamily` and V3 `V3ActionFamily` strings without merging the enums, so the
old calibrated V2 action vocabulary remains unchanged.

Failure state constrains the legal action region; M21 still ranks only legal,
affordable actions with its existing utility equation. No new controller module
was added.

## Relation Behavior

- `hasArea`: executable multi-view/definition/alternative recall uses the
  authoritative numeric parser and unit normalization, then builds competing
  numeric hypotheses and contrast verification requests.
- `hasCapacity`: capacity qualifiers from V3 facets and surfaces survive into
  M16/M17, preserving current maximum vs historical/configuration alternatives.
- `awardWonBy`: set expansion suppresses seen recipients with bounded prompt
  text; duplicate rediscovery records no new set-member novelty.
- `companyTradesAtStockExchange`: precision-biased; default broad expansion is
  blocked when candidates already exist; listing elimination is typed and
  rejection-first.
- `personHasCityOfDeath`: attribute decomposition promotes only death city;
  birth/residence/country/burial/hospital slots are contrastive evidence and
  can reject existing confused candidates.
- `countryLandBordersCountry`: frozen conservative path; no default V3
  expansion is introduced.

## Telemetry and Collection

`scripts/run_train_calibration_collection.py` now uses the V3 readiness gate for
V3 configs and selects from the V3 catalogue. It writes normal predictions and
training action telemetry plus:

- gold-free `inference_telemetry.jsonl`;
- `v3_pre_m8_hypothesis_graphs.jsonl`;
- `v3_final_hypothesis_graphs.jsonl`;
- `v3_action_effects.jsonl`.

Gold remains offline only. No gold field or gold object is used by online V3
action selection.

The V3 action effect schema is `v3-action-effect-v1`.

## Readiness

V2 calibrated baseline: `READY`

V3 TRAIN collection: `SOURCE READY`

V3 calibration: `PENDING REAL-WEIGHT TRAIN COLLECTION`

V3 validation: `NOT_READY` until separate V3 M20/M21 artifacts exist with
matching V3 provenance and V3 action-family coverage.

V3 TEST: `NOT_READY`

The production readiness gate refuses `pipeline.v3_core.enabled: true` unless:

- `pipeline.v3_core.mode: production`;
- `pipeline.v3_core.production_calibration_ready: true`;
- M20/M21 production artifacts are separate V3 paths;
- artifact provenance includes V3 schema/action-effect identifiers;
- V3 historical bins cover the V3 action families.

The historical V2 calibration artifacts cannot be loaded as V3 calibration.

## Real-Weight Command

Full TRAIN collection remains pending for the intended A100/runtime
environment. Copy-paste command:

```bash
cd /content/FactElicit-AKBC
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --output-dir outputs/v3_train_collection
```

Do not pass `--limit` for the authoritative V3D collection.

## Validation Evidence

Focused V3/V3D tests after final source edits:

```bash
python -m pytest tests/test_v3_core.py tests/test_v3d_activation.py -q -p no:randomly
```

Result: `31 passed in 0.80s`.

Broader control/calibration target set:

```bash
python -m pytest tests/test_v3_core.py tests/test_v3d_activation.py \
  tests/test_controller_calibration_readiness.py \
  tests/test_controller_calibration_collection.py \
  tests/test_calibration_sufficiency.py \
  tests/test_micro_planner.py \
  tests/test_production_activation.py -q -p no:randomly
```

Result: `245 passed, 1 skipped in 2.74s`.

Full deterministic suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Result: `3549 passed, 4 skipped in 51.29s`.

Full randomized/default suite:

```bash
python -m pytest tests/ -q
```

Result: `3549 passed, 4 skipped in 50.66s`.

Static checks after final source edits:

- `python -m pyflakes src/ tests/ scripts/`: passed
- `git diff --check`: passed

## Immutable Artifacts

Direct SHA256 values:

- `benchmark/evaluate.py`:
  `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`
- TRAIN: 477 rows,
  `ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e`
- VAL: 475 rows,
  `ba86b53ac38eb4b23b80391b291e5987ff4bbfe79827596fc09751b1bb0ce2be`
- TEST: 475 rows,
  `67c31c8388c585634df55500612f522ad42da6735d4c89eb59a9ef5a39f043f1`
- TEST ordered identity:
  `69d7d7cbafed0a612a51c13ad42dafc448705af5d5522cd24ef6334e9ad78640`
- TEST rows with `ObjectEntities`: `0`

Historical V2 calibration artifact hashes:

- M20 relation budget:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- M21 historical bins:
  `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- M21 planner calibration:
  `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

`configs/calibration/v3/` does not exist in this source-only environment, so no
fake V3 calibration artifacts were produced.

## Source Review

Reviewed for:

- duplicated numeric normalization ownership: none introduced;
- prompt-family evidence double counting: V3 preserves prompt family and
  independence group provenance;
- majority-vote finalizer: none introduced;
- stock broad expansion: blocked by catalogue when stock candidates exist;
- borders expansion: frozen by relation profile/action catalogue;
- hypothesis-state inconsistencies: tests cover terminal and conflict states;
- V3 leaking into V2 baseline: disabled by default and regression-tested;
- unaccounted model calls: execution deltas come from runtime counters;
- gold leakage: no online gold API or gold field added;
- V2 calibration reused as V3: readiness blocks it;
- hidden official TEST path: V3 TEST remains `NOT_READY`;
- O(n^2) contrast verification: deterministic strongest pair only;
- unbounded award prompts: bounded seen-set rendering;
- uncontrolled backtracking loops: `ActionHistory` and hard round caps.

## Verdict

PASS — V3D SOURCE READY FOR REAL-WEIGHT TRAIN COLLECTION
