# Audit 0068 — V3 Relation-Conditioned Hypothesis Search Core

V3_CORE_BASE_SHA:
f9003c66e016e649b630c6b933adcfbb2f01f2c2

## Scope

This milestone implements the V3 core source layer: relation-conditioned
hypothesis/search mechanics on the frozen two-model backbone. It does not add a
model, train, fine-tune, use web/RAG/KB lookup, regenerate calibration, run
official TEST inference, or claim calibrated V3 production readiness.

V2 calibrated baseline remains the executable production baseline. V3 is
explicitly opt-in through `pipeline.v3_core.enabled`.

## Files Changed

Modified tracked files:

- `scripts/run_cover.py`
- `src/cover_kbc/controller_calibration/readiness.py`
- `src/cover_kbc/evidence/consensus.py`
- `src/cover_kbc/evidence/consensus_types.py`
- `src/cover_kbc/pipeline.py`
- `tests/test_v3a_failure_attribution.py`

Added files:

- `configs/experiments/cover_kbc_v3_shadow_scripted.yaml`
- `configs/experiments/cover_kbc_v3_train_collection.yaml`
- `src/cover_kbc/v3_core/__init__.py`
- `src/cover_kbc/v3_core/config.py`
- `src/cover_kbc/v3_core/hypothesis.py`
- `src/cover_kbc/v3_core/prompt_families.py`
- `src/cover_kbc/v3_core/relation_programs.py`
- `src/cover_kbc/verification/v3_modes.py`
- `tests/test_v3_core.py`
- `docs/audits/0068-v3-relation-conditioned-hypothesis-search-core.md`

## Model Contract

Unchanged active pair:

- enumerator: `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
  revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`
- verifier: `Qwen/Qwen3.5-4B`
  revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- total published parameters: `28,671,226,368 / 32,000,000,000`
- legal: true

No third model, no new model strategy flag, no retired portfolio/direct path.

## M1 RelationProfile Activation

V3 consumes the six V3A `RelationProfile` records only inside the opt-in V3
core path:

- `hasArea`: numeric single, multi-view, numeric contrast
- `hasCapacity`: numeric single, definition-aware multi-view, definition contrast
- `companyTradesAtStockExchange`: entity set, elimination, rejection first
- `personHasCityOfDeath`: zero-or-one entity, attribute contrast, semantic verify
- `awardWonBy`: open entity set, promote-suppress-iterate, unary verify
- `countryLandBordersCountry`: structural small set, frozen conservative

Unknown relations continue to fail closed. Baseline V2 scoring and selection do
not consume relation profiles; `tests/test_v3a_failure_attribution.py` now
asserts that profile consumption remains outside V2 scoring/selection.

## M18 Multi-View Recall

`src/cover_kbc/v3_core/prompt_families.py` introduces the V3 prompt-family
vocabulary:

- `DIRECT`
- `SEMANTIC_PARAPHRASE`
- `DEFINITION`
- `CONTRAST`
- `ALTERNATIVE`
- `DECOMPOSITION`

Existing `GenerationRecord`/M16 provenance fields are reused: `view_id`,
existing family/group, `independence_group`, `model_role`, `model_id`,
`facet_id`, and record IDs. Same-family repeated samples do not become new
independent supports. Raw count is kept separately from independent count.

V3 does not run all views for all relations. `relation_programs.py` filters
legal actions by the relation profile and failure state, so stock does not get
award expansion, area does not get listing elimination, and borders remain
conservative.

## M16 Hypothesis/Provenance Graph

`src/cover_kbc/v3_core/hypothesis.py` builds a deterministic
`QueryHypothesisGraph` after ordinary prediction finalization. It consumes:

- M16 `QueryConsensusResult`
- relation profile
- optional M17 specialist verification result
- final M8 `Prediction`

It serializes:

- hypothesis IDs
- normalized/display values
- semantic type
- prompt families
- raw and independent support
- origin/event IDs
- contradiction IDs/details
- verifier label/margin when available
- alternative strength
- ambiguity flags
- numeric cluster ID
- capacity semantic qualifier
- stock listing disambiguation
- final hypothesis state
- legal V3 action families for the graph's failure state

M16 candidate state now carries source annotations such as
`mention_kind=TARGET_EXCHANGE` and `temporal_status=FORMER_OR_DELISTED` forward
from the event ledger. These are provenance labels, not decisions, and allow the
V3 graph to preserve stock/death semantics without reparsing prose.

## State Machine

Hypothesis states:

- `PROPOSED`
- `SUPPORTED`
- `SEMANTICALLY_ALIGNED`
- `CHALLENGED`
- `VERIFIED`
- `OUTPUT`
- `CONTRADICTED`
- `DROPPED`

Transitions are deterministic and recorded. A terminal `DROPPED` hypothesis
cannot silently resurrect to `VERIFIED`; the test suite asserts this. Frequency
alone is never a finalizer: three direct repeats remain one independent support.

## Contradictions and Alternatives

V3 graph contradiction types:

- `CANDIDATE_CONFLICT`
- `SEMANTIC_CONFLICT`
- `TEMPORAL_CONFLICT`
- `UNIT_QUANTITY_CONFLICT`
- `ATTRIBUTE_CONFUSION`
- `LISTING_SCOPE_CONFLICT`

M16 semantic disagreements are projected into contradiction edges. For
single-valued relations, the graph records deterministic top-vs-alternative
conflict edges instead of scheduling O(n^2) verifier comparisons.

## Relation-Specific Behavior

`hasArea`:

- numeric hypotheses use existing authoritative numeric normalization and
  clustering;
- relative-distance clustering remains rule/config driven, not TEST tuned;
- nearby values cluster, distant alternatives remain separate;
- majority count alone does not decide output.

`hasCapacity`:

- capacity qualifiers are preserved, including `CURRENT_MAXIMUM`,
  `HISTORICAL`, `SEATED_CONFIGURATION`, `CONCERT_CONFIGURATION`,
  `SPORTS_CONFIGURATION`, and `UNKNOWN`;
- historical/current distinctions remain traceable and are not collapsed into
  one factual slot.

`awardWonBy`:

- `award_promote_suppress_round()` implements seen-set suppression and novelty;
- suppression rendering is deterministic and bounded;
- duplicate rediscovery yields zero set novelty and can stop expansion.

`companyTradesAtStockExchange`:

- `ListingDisambiguation` is typed:
  `DIRECT_LISTING`, `PARENT_COMPANY_ONLY`, `SUBSIDIARY_ONLY`,
  `ADR_OR_DEPOSITARY`, `HISTORICAL_ONLY`, `OTC_OR_MARKET_CONFUSION`,
  `RELATED_BUT_NOT_DIRECT`, `UNKNOWN`;
- stock action bias is elimination;
- broad expansion is not a default action except no-candidate recall.

`personHasCityOfDeath`:

- death slots are typed as death city, birth location, residence, death country,
  burial location, or other;
- only `DEATH_CITY` is a direct target; other locality slots are contrast
  evidence.

`countryLandBordersCountry`:

- frozen conservative profile remains active;
- V3 permits provenance/hypothesis serialization but no default set expansion.

## M17 Verification Modes

`src/cover_kbc/verification/v3_modes.py` adds typed V3 verifier requests:

- `UNARY`
- `SEMANTIC`
- `CONTRAST`

Contrast labels are `H1`, `H2`, `UNKNOWN`. Unary/semantic labels remain
`VALID`, `INVALID`, `UNKNOWN`. The request is blind with respect to generator
confidence. Stock rejection-first verification is represented as a semantic
request with `rejection_first=true`.

Qwen/Qwen3.5-4B remains the verifier owner. No free-form confidence parsing is
introduced.

## M21 Failure-Aware Search Interface

`src/cover_kbc/v3_core/relation_programs.py` implements the failure-state to
legal-action-region mapping:

- `NO_CANDIDATE` -> `MULTI_VIEW_RECALL`
- `SINGLE_LOW_SUPPORT` -> `INDEPENDENT_RECALL`
- `MULTIPLE_CONFLICTING` -> `CONTRAST_VERIFY`
- `HIGH_FP_RISK` -> `LISTING_ELIMINATION`, `SEMANTIC_VERIFY`
- `SET_GROWING` -> `SET_EXPANSION`
- `SEMANTIC_AMBIGUITY` -> `SEMANTIC_VERIFY`, `ATTRIBUTE_DECOMPOSITION`
- `NULL_UNRESOLVED` -> targeted recall/semantic/decomposition
- `STABLE_VERIFIED` -> `STOP`

This is not a learned utility and does not replace the existing M21 formula.
V2 M21 calibration is not reused as V3 calibration. V3 graph artifacts expose
the legal families for future V3D collection/calibration.

Bounded backtracking mechanics are represented by `ActionHistory`; a repeated
state/action pair with no material novelty becomes inadmissible. `NoveltyChange`
separates new hypotheses, prompt families, contradictions, qualifiers, verified
candidates, numeric clusters, and set members.

## Accounting Guarantees

The V3 graph builder is non-neural and runs after ordinary prediction exists.
With `pipeline.v3_core.enabled` false, the pre-existing pipeline path is
structurally unchanged. With it true, the graph is appended after M8 output and
does not mutate `EvidenceGraph`, `Prediction`, scoring, selection, M20/M21
budgets, or call/token counters.

Evidence count and evidence independence are separate:

- `raw_support_count` records repeated observations;
- `independent_support_count` is reduced over prompt family, independence group,
  and model role.

## V3A Telemetry Integration

V3A telemetry remains versioned and unchanged. V3 adds a separate
`v3_hypothesis_graph.jsonl` artifact through `scripts/run_cover.py` when V3 is
enabled. This avoids changing V3A report JSON/Markdown/CSV semantics.

## Configs and Readiness

Added configs:

- `configs/experiments/cover_kbc_v3_shadow_scripted.yaml`
- `configs/experiments/cover_kbc_v3_train_collection.yaml`

The future real-weight V3D collection command is:

```bash
python scripts/run_train_calibration_collection.py \
  --config configs/experiments/cover_kbc_v3_train_collection.yaml \
  --output-dir outputs/v3_train_collection
```

Do not use `--limit` for the real run.

`evaluate_v3_core_readiness()` reports:

- V2 calibrated baseline: `READY`
- V3 core source: `IMPLEMENTED`
- V3 TRAIN collection: `READY`
- V3 production calibration: `NOT_READY`
- V3 official TEST: `NOT_READY`

The V3 gate verifies TRAIN split, frozen model IDs/revisions, exact parameter
budget, V3 enablement, no V3 production calibration claim, required action
families, and impossible relation/action exclusions.

## Validation

Targeted tests:

- `python -m pytest tests/test_v3_core.py -q -p no:randomly`
  - `17 passed`
- `python -m pytest tests/test_v3a_failure_attribution.py tests/test_controller_calibration_readiness.py tests/test_layer6_integration.py tests/test_specialist_verifier.py tests/test_bidirectional_verification.py -q -p no:randomly`
  - `373 passed`
- After adding M16 annotation carry-forward:
  `python -m pytest tests/test_v3_core.py tests/test_atomic_consensus.py tests/test_layer4_integration.py tests/test_layer6_integration.py -q -p no:randomly`
  - `304 passed`

CLI smoke:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_shadow_scripted.yaml \
  --limit 1 \
  --output-dir /tmp/cover-kbc-v3-smoke \
  --no-eval
```

Result: wrote `v3_hypothesis_graph.jsonl` with schema `v3-core-v1` and
`v3_production_calibration: NOT_READY`.

Full deterministic suite:

```bash
python -m pytest tests/ -q -p no:randomly
```

Final result after all source changes: `3535 passed, 4 skipped in 50.81s`.

Full randomized/default suite:

```bash
python -m pytest tests/ -q
```

Final result after all source changes: `3535 passed, 4 skipped in 50.34s`.

Static checks:

- `python -m pyflakes src/ tests/ scripts/` passed
- `git diff --check` passed

## Immutable Artifacts

Direct hashes from current files:

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

Calibration artifact hashes:

- M20 relation budget:
  `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68`
- M21 historical bins:
  `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071`
- M21 planner calibration:
  `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05`

No benchmark data, evaluator, calibration artifact, model ID, or model revision
was rewritten.

## Real-Weight Runs

No real-weight full TRAIN, VAL, or TEST run was performed in this source
milestone. No official TEST inference or TEST evaluation was run.

V3 production calibration is pending V3D TRAIN collection/derivation.

## Readiness States

- V2 calibrated baseline: `READY`
- V3 core source: `IMPLEMENTED`
- V3 TRAIN collection: `READY`
- V3 production calibration: `NOT_READY`
- V3 official TEST: `NOT_READY`

Final verdict:

PASS — V3 RELATION-CONDITIONED HYPOTHESIS SEARCH CORE READY FOR TRAIN CALIBRATION
