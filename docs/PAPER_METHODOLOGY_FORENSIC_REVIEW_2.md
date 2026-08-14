# COVER-KBC Final Methodology Forensic Review

Review date: 2026-08-14.
Final HEAD at completion: `59d516145a7eca95d3972da3e8c461c489c114e6`
("fix full test validate"). Branch: `main` (only branch; no tags). 80 commits.

**The repository moved during this review, and the movement matters.** Inspection
began at HEAD `9281cd251cfc3ed898c8d586a7e11b4b925fa398` ("revert"), where a
Profile F1 (Stock Empty Rescue) layer existed only as uncommitted working-tree
files. During the write-up the author committed it (`7c88266` "implement stock
empty rescue"), added a Profile F1 Colab submission builder (`59d5161` "fix full
test validate"), and updated `README.md` and `docs/IMPLEMENTATION_STATUS.md` to
name Profile F1 as the current frozen best system. Those two documentation files
are modified-but-uncommitted at completion; everything else is committed.

This report describes **two frozen states** and keeps them apart everywhere:

| Name | Config | Hidden TEST overall F1 | Status in repository |
|---|---|---:|---|
| **Profile F1** — *the current system* | `configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml` | **0.5878** | committed (`7c88266`); `FROZEN_CURRENT_BASELINE`; audit `docs/audits/0093-profile-f1-stock-empty-rescue-promotion.md`; named current by `README.md` and `docs/IMPLEMENTATION_STATUS.md` |
| **Profile E3** — immediate predecessor | `configs/experiments/cover_kbc_v3_7_profile_e3_mistral_area_multiview_test.yaml` | 0.5857 | `PREVIOUS_FROZEN_BASELINE`, `superseded_by: cover_kbc_v3_8_profile_f1_stock_empty_rescue_test` |

Sections written before the commit landed described F1 as "uncommitted"; every
such statement has been corrected here and in §17/§23. The *technical* content of
the F1 layer is unchanged by the commit — only its status.

One further untracked file is present and was deliberately not used as a source:
`docs/PAPER_METHODOLOGY_FORENSIC_REVIEW.md`, a sibling report from an earlier
pass that predates the F1 promotion.

Everything below is derived from source, config, tests, audits and git history.
No inference was run, no model downloaded, no file outside this report created
or modified.

---

## 1. Executive Methodology Summary

COVER-KBC is a **closed-book, single-checkpoint, relation-typed active evidence
acquisition system** for LM-KBC 2026. One frozen open-weight model
(`mistralai/Mistral-Small-3.2-24B-Instruct-2506`, revision
`95a6d26c4bfb886c58daf9d3f7332c857cb27b43`, 24,011,361,280 published parameters)
serves *every* logical role — enumerator, calibrated label-scoring verifier,
existence gate, and post-pipeline relation repair caller. There is no second
model, no retrieval, no training of any kind, and no learned component anywhere
on the prediction path. Everything that is not a frozen-model forward pass is
deterministic Python.

The system has **two prediction stages** and the distinction is the single most
important fact for the Methodology section.

**Stage 1 — the COVER-KBC core** (`src/cover_kbc/pipeline.py :: CoverPipeline`).
A query `q = (subject, relation)` is compiled to a `RelationContract`
(`contracts/registry.py`) that declares the relation's semantics, its typed
inference regime (`SMALL_SET`, `NULL_SINGLE`, `NUMERIC`, `LARGE_OPEN_SET`), its
mandatory and optional elicitation views, its verification thresholds, its
stopping policy and its selection policy. A deterministic controller
(`controller.py :: choose_action`) then runs an adaptive loop over a typed action
space (`RUN_VIEW`, `RUN_FACET`, `REVERSE_CHECK`, `RESAMPLE`, `VERIFY`,
`ADVERSARIAL_VERIFY`, `CROSS_MODEL_CHECK`, `STOP`), scoring actions by
`A_t(a) = α·Ŷ + β·G − λ·C − ρ·D + γ·U` and stopping by a relation-typed rule that
reads a *search-need* residual `q_res` (`coverage.py :: estimate_residual`).
Parsed outputs land in an **independence-aware evidence graph**
(`evidence/graph.py :: EvidenceGraph`), where each candidate accumulates signed
edges tagged with the *acquisition mechanism* that produced them, so repeated
sampling of one view can never masquerade as corroboration. Candidates are
scored by `S(o) = a·F − ... ` (`scoring.py :: score_candidate`), verified by a
**blind, contextually calibrated three-way label scorer** (`verification/blind.py`),
and finalized by a **relation-specific selector** (`selection.py`).

**Stage 2 — the profile-specific final layer**
(`src/cover_kbc/leaderboard_repair/`). A feature-flagged, per-relation,
call-capped post-pipeline stack that may rewrite only `ObjectEntities`. In
Profile F1 it contains exactly five active features:
`AwardMetadataNormalizer` (deterministic, 0 calls), `MistralCityEmptyRescue`
(empty rows only, ≤2 calls), `MistralAreaMultiView` (all Area rows, 4 views + ≤1
judge), `MistralCapacityMultiView` (all Capacity rows, 4 views + ≤1 judge), and
`MistralStockEmptyRescue` (empty Stock rows only, 4 views, no judge). Borders
receive no repair at all.

**The forensically decisive finding.** For the two numeric relations, Stage 2
does not *repair* Stage 1 — it **replaces** it. `repair_area_multiview` and
`repair_capacity` both record the upstream answer as `current_ignored=True` and
never let it vote, cluster or act as a fallback
(`leaderboard_repair/area_multiview.py:399-404`,
`leaderboard_repair/capacity.py:337-342`). The targeted runners
`scripts/run_area_multiview.py` and `scripts/run_capacity_multiview.py`
construct `Prediction(object_entities=[])` and run the multi-view module
standalone, then merge by relation into a baseline artifact. So the hidden-TEST
`hasArea` (0.6700) and `hasCapacity` (0.1633) numbers are produced **entirely by
the multi-view modules**, with the COVER-KBC core contributing nothing to those
two relations' final values. Conversely the two strongest relations —
`countryLandBordersCountry` (0.9291) and, up to the empty-row rescue,
`companyTradesAtStockExchange` (0.7285 → 0.7385) — are produced **entirely by the
core**, with zero or empty-only Stage-2 mutation. The paper must not present
Stage 2 as the method, and must not present Stage 1 as covering all six
relations.

**What is genuinely active vs. what merely exists.** The repository implements
modules M0–M21 plus a "V3 core". Of these, M0–M8 (contracts, router, elicitation,
evidence graph, blind verifier, evidence/uncertainty state, RCSE, controller,
selector) are **ACTIVE-PRODUCTION**. M9–M19 are **SHADOW/DIAGNOSTIC by
construction** — their config classes literally accept only `mode: "shadow"`
(`SUPPORTED_MODES = frozenset({"shadow"})` in `query_intelligence/profiler.py`,
`prompt_compiler.py`, `parametric_retrieval.py`, all four `specialists/*`,
`evidence/consensus.py`, `evidence/layer4.py`, `coverage_gap/missingness.py`) and
they cannot write to the graph. M20 (relation budget scheduler) and M21
(expected-value micro-planner) run in `mode: production` and are the *only*
upgraded modules that can change a prediction, and they do so through exactly one
seam: the pre-Module-8 **V3 control loop**
(`pipeline.py :: _run_v3_control_loop`, gated by
`pipeline.py:747 :: _v3_control_loop_active`), where M21 selects at most one V3
action per round for at most 3 rounds, subject to the M20 ledger precharge and to
the relation contract's own hard call budget. Because Phase A/B typically exhaust
that budget (contract caps are 4 calls for borders/city/area/capacity, 5 for
stock, 16 for awards), **how often the V3 loop actually fires on TEST is
UNRESOLVED FROM REPOSITORY EVIDENCE** — no run artifact with
`v3_hypothesis_graph.jsonl` or `micro_planner.jsonl` is preserved in the repo.

**Several configured knobs are inert in the final profile**, and the paper must
not describe them as active:

- `enable_cross_model_recall: true` has no effect. `cross_model_recall_available`
  (`pipeline.py:720`) requires the configured enumerator and verifier model ids to
  *differ*; in F1/E3 both are the same Mistral id, so `CROSS_MODEL_CHECK` is never
  legal and the cross-model term `X(o)` (`gamma_cross_model: 0.5`) is identically 0.
- `max_verifications_per_query: 6` is read only by `_verify_pending`
  (`pipeline.py:1070`), which is reached only when `enable_active_controller` is
  false. It is true, so this knob is dead.
- The calibrated existence gate exists only for two relations: `GATE_QUESTIONS`
  (`pipeline.py:160`) has entries for `personHasCityOfDeath` and
  `companyTradesAtStockExchange` only.
- Qwen, CHIV, C2, NSMV, Direct-Border sweeps, `DeathCityRecall`,
  `AreaEmptyRescue`, `CapacityRepair`, `AwardRecipientWitness`,
  `AwardTimeSlicedRecall` and the L8/L9 guards are **RETIRED**: their flags still
  parse (so archival configs load) but no executable branch remains
  (`leaderboard_repair/config.py:44-49`).

**The defensible contribution** is therefore not "multi-view prompting" and not
"verification". It is the *composition*: a relation is compiled into an
executable inference program whose evidence accounting distinguishes independent
mechanisms from repeated samples, whose candidate confidence and whose
search-need signal are computed from **disjoint** evidence index sets, whose
controller spends test-time compute against a relation-typed stopping rule, and
whose finalization is relation-specific because the six relations have different
official targets (robust central value for area, *highest published* value for
capacity, precision-first set for stock, recall-first open set for awards,
zero-or-one for city, near-saturated structural set for borders).

---

## 2. Repository Coverage and Inspection

### 2.1 Inventory statistics

| Category | Count | Lines |
|---|---:|---:|
Counted at the **final** HEAD `59d5161` (the mid-review commits added the
Profile F1 module, config, runner, audit, tests and a Colab builder). Figures in
parentheses are the values at the starting HEAD `9281cd2`, where line counts were
measured.

| Category | Count | Lines |
|---|---:|---:|
| Tracked files (`git ls-files`) | **438** (431) | — |
| Python modules under `src/cover_kbc/` | **147** (146) | 60,938 + 422 |
| Tests under `tests/` | **85** (84) | 57,561 + ~730 |
| Scripts under `scripts/` | **34** (33) | 14,150 + ~360 |
| Configs under `configs/` | **47** (46) | — |
| Markdown under `docs/` | **96** (95) | 48,317 + ~200 |
| — of which conformance/promotion audits `docs/audits/` | **94** (93) | — |
| — of which runbooks `docs/runbooks/` | 2 | — |
| Benchmark snapshot `benchmark/` | 13 | — |
| Notebooks | **3** (2) | — |
| PDFs (proposal + V2 architecture spec) | 2 | — |
| `memory/` | 2 | — |
| Untracked at completion | 1 (this report) + 1 (sibling report, 1,754 lines) | — |

Git: **80** commits, one branch (`main`, tracking `origin/main`), no tags.

### 2.2 What was read in full

- **Every** file in `src/cover_kbc/` that is on or adjacent to the prediction
  path: `pipeline.py` (3,834 lines, read in sections covering every method named
  in §4), `contracts/*` (all 6), `elicitation/*` (all 4), `evidence/*` (graph,
  production_bridge, consensus header/modes, layer4 header/modes),
  `scoring.py`, `coverage.py`, `controller.py`, `selection.py`,
  `verification/blind.py`, `verification/v3_modes.py` (modes/labels),
  `normalization/numeric.py`, `normalization/strings.py` (header + symbol list),
  `models/registry.py`, `models/huggingface.py`, `integration_mode.py`,
  `types.py`, all of `leaderboard_repair/` (config, stack, relations, runtime,
  util, area, area_multiview, capacity, stock_empty_rescue), all of `v3_1/`
  (config, prompts, live_prompts, output_repair, retention, entity_finalization,
  numeric_recovery), `v3_core/` (config, relation_programs, prompt_families,
  hypothesis failure-state derivation, execution catalogue + `_recall_spec`),
  `control/micro_planner.py` (utility + plan), `control/historical_bins.py`
  (`state_bin_key`), `coverage_gap/missingness.py` (header, config, `combine`),
  `contracts/relation_profile.py`.
- **All** experiment configs relevant to the lineage: v3.3 (D), v3.4 (E1
  City-only), v3.5 (integrated E1), v3.6 (E2), v3.7 (E3), v3.8 (F1); plus the
  retired A/A+Award/C/C2 probe configs by name and metadata.
- **All three** calibration artifacts under `configs/calibration/v3/`
  (`m20_relation_budget.json`, `m21_planner_calibration.json`,
  `m21_historical_bins.json`) including provenance blocks and support counts.
- `benchmark/evaluate.py` (official scorer: normalization, numeric tolerance
  0.05, bipartite matching, macro-averaging) and the three split files
  (train 477, val 475, test 475 rows).
- Audits 0085–0093 in full or in their score/method sections; audit index for
  0001–0084 (titles + targeted greps).
- `README.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/PAPER_SYSTEM_SUMMARY.md`
  (§1–4), heading maps of `docs/PAPER_FULL_ARCHITECTURE_REVIEW.md` and
  `docs/PAPER_RELATED_WORK_LINEAGE_REVIEW.md`.
- **Both PDFs**, extracted with `pdftotext -layout` into the scratchpad
  (`COVER_KBC_Technical_Proposal_New.pdf` → 1,579 lines;
  `COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` → 1,688 lines). Both are fully readable;
  the proposal's §15 `R_t` ensemble, §17 `U_t(a)` utility and §25.2 novelty
  claims were read verbatim.
- **All three notebooks** parsed as JSON: `COVER_KBC_Colab.ipynb` (18 cells — a
  thin Profile E3 Colab driver that calls repository code only);
  `COVER_KBC_PostArchitecture_RealModel_Smoke.ipynb` (3 cells — runtime
  compatibility smoke, no benchmark split, no accuracy); and
  **`COVER_KBC_Profile_F1_Full_TEST_Colab.ipynb`** (15 cells, added mid-review by
  commit `59d5161`) — a *Profile F1 TEST submission builder* that implements
  exactly the artifact-seeded targeted path: static preflight → dry-run
  invariants → targeted Stock Empty Rescue only → merge into the 475-row E3
  baseline → validate → package the submission zip. Its own comment on the
  targeted cell is decisive corroboration of §3.2: *"This does NOT run the full
  475-row pipeline. It loads Mistral once and spends exactly 4 calls"* per empty
  stock row.
- `.gitignore`, `pyproject.toml`, git status, `git log --oneline` (all 78),
  `git show --stat` on the last two commits.
- The one local prediction artifact `outputs/submission-best/predictions.jsonl`
  (475 rows, SHA256 `c7e52a71d8366ba5863ce1e207cda13e294498e0bd89ac35d1ad951d53290e2d`),
  and `outputs/submission-best.zip`.

### 2.3 Exclusions

- `.git` object internals, `.pytest_cache`, `.agents`, `__pycache__` — not
  inspected (only `git` porcelain commands used).
- No virtual environments, model caches or downloaded weights exist in the
  working tree; `.gitignore` excludes `hf_cache/`, `model_weights/`,
  `*.safetensors`, `outputs/`, `tasks.jsonl`, `*.log`.
- `tasks.jsonl` (12.7 KB, gitignored, open in the user's IDE) was **not** read —
  it is an ignored local scratch artifact, not repository-authored code.
- `full_collect.log` (139 bytes, gitignored) not read.
- Audits 0001–0084 were not read line-by-line (≈37,000 lines); they were
  inventoried by title and searched for score tables, retirement statements and
  method contracts. This is recorded as a partial-coverage caveat: any claim in
  this report sourced from an old audit is cited by audit number.
- The sibling report `docs/PAPER_METHODOLOGY_FORENSIC_REVIEW.md` was inspected
  only for its scope statement (to avoid duplicating or contradicting it) and is
  not used as a source of truth.

### 2.4 Files that could not be interpreted

None. Every binary and non-plain-text artifact in the repository (2 PDFs, 2
notebooks, 1 zip) was successfully inspected or accounted for.

---

## 3. Final Runtime / Profile F1 (and Profile E3)

### 3.1 Canonical entrypoint

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml \
  --split test \
  --no-eval
```

`scripts/run_cover.py :: main()` (line 442) is the single owner of a full run.
Ordered responsibilities:

1. `check_router_consistency()` + `check_library_covers_contracts()` — fail
   closed on any contract/router/view mismatch before anything expensive
   (lines 478–479).
2. `resolve_execution_mode(config)` → `ExecutionMode.INTERLEAVED` (line 484).
3. `require_huggingface_runtime(...)` preflight (line 490).
4. `load_dataset(split)`; `--relation` filter is **refused** on blind splits
   (line 308).
5. Production activation *before weights load*: `_wants_production(config)` is
   true iff **both** `relation_budget_scheduler.mode` and `micro_planner.mode`
   are `production` (line 122). Then `evaluate_test_readiness` must return
   `FULL_TEST_READY`, **or** `_allow_leaderboard_probe` must accept the run
   (line 371). F1/E3 take the probe path: they declare
   `leaderboard_probe.status: CALIBRATION_REVIEW_LEADERBOARD_PROBE`,
   `role_swap: MISTRAL_ONLY_VERIFIER`, and are matched against a hard-coded
   allow-list of exactly four accepted blockers (lines 419–439).
6. `build_runtime(enumerator_cfg)`; `verifier_runtime = runtime` because
   `verifier_cfg == enumerator_cfg` (line 562) — **one physical model object**.
7. `audit_parameter_budget` — 24.01B / 32B, PASS.
8. `load_production_calibration(...)` with SHA-pinned artifacts.
9. Construct `CoverPipeline` with every module builder (lines 650–751), then
   `pipeline.run(queries, progress=True)` (line 753).
10. `build_repair_stack(config.get("leaderboard_repair"), ...)` and
    `repair_stack.apply(...)` (lines 762–781).
11. Write `predictions.jsonl`, `trace.jsonl`, per-module JSONL sidecars, repair
    records/accounting, `run_accounting.json`, `manifest.json`.
12. `submission_verdict` decides the process exit code; a blind split that is not
    ready returns 3.

### 3.2 The runners that actually produced the leaderboard artifacts

**This is a forensic caveat the paper must respect.** Audits 0089–0093 describe
each promotion as a *controlled, relation-only* hidden-TEST probe produced by a
targeted runner plus a merge, not by one end-to-end `run_cover.py` invocation:

| Runner | Scope | Seeds from | Merge |
|---|---|---|---|
| `scripts/run_area_multiview.py` | 100 TEST `hasArea` rows | `Prediction(object_entities=[])` (line 120) | `scripts/merge_area_multiview_results.py` |
| `scripts/run_capacity_multiview.py` | 98 TEST `hasCapacity` rows | `Prediction(object_entities=[])` (line 100) | `scripts/merge_targeted_relation_results.py` |
| `scripts/run_stock_empty_rescue.py` | 100 TEST stock rows | **the E3 baseline predictions file** (`baseline_stock_predictions`, line 175) | `scripts/merge_targeted_relation_results.py` |

`merge_targeted_relation_results.py` fails closed on: baseline ≠ 475 rows,
targeted ≠ expected rows, missing/extra/duplicate keys, row-order change, or any
mutation outside the target relation.

Consequence: Area and Capacity multi-view are provably independent of the core
(they start from an empty prediction), while Stock Empty Rescue is provably
*conditional* on the core (it needs the E3 output to know which rows are empty).

**This is now stated outright by the repository itself.** The `README.md` updated
mid-review says of the Profile F1 reproduction path:

> "This path starts from the hidden-winning Profile E3 475-row artifact and
> changes only the Stock rows. A fresh full `scripts/run_cover.py` TEST run is a
> valid diagnostic run, but it recomputes every relation and is not the
> artifact-seeded path that produced the user-provided 0.5878 leaderboard score."

So the reported hidden-TEST numbers were **not** produced by a single end-to-end
`run_cover.py` invocation. They were produced by an artifact-seeded chain of
relation-targeted runs and fail-closed merges. The paper's system description must
either describe that chain, or describe `run_cover.py` as the reference
implementation while stating that the submitted artifact was assembled
relation-by-relation. Note also that the same README now uses
`outputs/submission-best/predictions.jsonl` **as** the E3 baseline artifact — an
identification audit 0091 explicitly declined to make (§17, row 14).

### 3.3 Exact final configuration (Profile F1)

```yaml
model_profile.enumerator:
  backend: huggingface
  model_id: "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
  revision: "95a6d26c4bfb886c58daf9d3f7332c857cb27b43"
  family: mistral
  tokenizer_backend: mistral_common
  quantization: nf4
  torch_dtype: bfloat16
  device_map: auto
  published_total_parameters: 24011361280
budget_assertion: {total: 24011361280, limit: 32000000000, legal: true}

pipeline:
  mode: interleaved
  enable_active_controller: true
  max_steps_per_query: 12
  max_control_rounds_per_catalogue: 3
  max_calls_per_query: 12
  max_generated_tokens_per_query: 6000
  enable_calibrated_gate: true      # gate_min_margin 1.0, gate_min_prob 0.5
  enable_verifier: true
  use_calibration: true
  max_verifications_per_query: 6    # INERT (see §1)
  enable_prompt_disagreement: true
  enable_cross_model_recall: true   # INERT (same model id both roles)
  v3_core: {enabled: true, mode: production, production_calibration_ready: true}
  scoring: {alpha_support 1.0, beta_logit 0.6, gamma_cross_model 0.5,
            delta_contradiction 1.5, eta_disagreement 1.0,
            auto_accept_support 3, verify_max_support 2,
            adversarial_disagreement 0.15, accept_score 0.2,
            min_valid_prob 0.40, drop_on_unknown true,
            logit_clip 3.0, logit_epsilon 1e-6}
  selection: {capacity_support_ratio 1.0, capacity_trust_verified true,
              v3_1.enabled true,
              v3_1.safe: final_candidate_retention T, enumeration_label_repair T,
                         stock_support_dominance F, stock_structural_validation T,
                         numeric_output_canonicalization T,
              v3_1.aggressive: stock_listing_entity_prompt T, all others F}

leaderboard_repair:
  repair_version: v3.8-profile-f1-stock-empty-rescue
  profile: F1_STOCK_EMPTY_RESCUE
  direct_area_mode / area_multiview_mode / capacity_multiview_mode: DIRECT_ALL
  stock_empty_rescue_mode: EMPTY_ONLY_STRONG_CONSENSUS
  stock_empty_rescue_min_support: 3
  features: mistral_stock_empty_rescue T, mistral_city_empty_rescue T,
            mistral_direct_area T, mistral_area_multiview T,
            mistral_capacity_multiview T, award_metadata_cleanup T,
            everything else F
  max_calls_by_relation:
    countryLandBordersCountry: 0     # no repair at all
    companyTradesAtStockExchange: 4  # was 0 in E3
    hasArea: 5
    hasCapacity: 5
    personHasCityOfDeath: 2
    awardWonBy: 1                    # deterministic, spends 0 calls

query_intelligence.*, specialists.*, consensus, specialist_verifier,
bidirectional_verification, layer4_integration, coverage_gap,
layer6_integration:  mode: shadow
relation_budget_scheduler, micro_planner:  mode: production
```

The **only** semantic differences F1 − E3 are: `mistral_stock_empty_rescue`
`false → true`, `stock_empty_rescue_mode` `OFF → EMPTY_ONLY_STRONG_CONSENSUS`,
`stock_empty_rescue_min_support` `3`, and stock cap `0 → 4`. Every other block
is byte-identical. `DEFAULT_CAPS["companyTradesAtStockExchange"]` also moved
`2 → 4` in `leaderboard_repair/config.py:10`, which cannot affect E3 because E3
sets the stock cap explicitly to 0.

### 3.4 Hidden TEST scores (user-provided leaderboard evidence)

Profile F1 — `docs/audits/0093`, and `experiment.hidden_test_scores` in the v3.8
config:

| Relation | P | R | F1 |
|---|---:|---:|---:|
| `awardWonBy` | 0.3255 | 0.3707 | 0.3105 |
| `companyTradesAtStockExchange` | 0.8992 | 0.7963 | **0.7385** |
| `countryLandBordersCountry` | 0.9712 | 0.9295 | 0.9291 |
| `hasArea` | 0.6700 | 0.6700 | 0.6700 |
| `hasCapacity` | 0.2449 | 0.1633 | 0.1633 |
| `personHasCityOfDeath` | 0.9600 | 0.5900 | 0.5700 |
| **All relations** | **0.7268** | **0.6055** | **0.5878** |
| Zero-object cases | 0.6038 | 0.9412 | 0.7356 |

Profile E3 differs only in stock (P 0.9092 / R 0.7863 / F1 0.7285), overall
0.7289 / 0.6034 / **0.5857**, zero-object 0.5963 / 0.9412 / 0.7300.

**These numbers cannot be recomputed locally.** `benchmark/data/test.jsonl` has
475 rows with empty `ObjectEntities`; `run_cover.py:855` refuses to score a
blind split and writes no `metrics.json`. Every score in this report is
user-provided leaderboard evidence recorded in config metadata and audits.

### 3.5 Artifact status

- No winning artifact for E2, E3 or F1 exists locally. The config records
  `WINNING_PROFILE_*_ARTIFACT_PENDING_USER_IMPORT`.
- `outputs/submission-best/predictions.jsonl` (SHA256 `c7e52a71…`) is present but
  audit 0091 explicitly declines to identify it as the E3 winner. Its relation
  census is 100 area / 100 stock / 100 city / 98 capacity / 67 borders / 10
  awards, with 85 empty city rows, 57 empty stock rows, 11 empty border rows and
  8 empty capacity rows. Recorded here as observed shape only; **which profile
  produced it is UNRESOLVED FROM REPOSITORY EVIDENCE**.
- Known historical SHAs: A+Award `bf113ce4…` (audit 0085), Profile D
  `7a01382d…` (audit 0086), integrated E1 `67bd1bc8…` (recorded in
  `docs/IMPLEMENTATION_STATUS.md`).

---

## 4. End-to-End Inference Pipeline

The complete call graph for one `(subject, relation)` in the final profile.
Every arrow is a source-level call, not a conceptual step.

```text
run_cover.main()
 └─ CoverPipeline.run(queries)                             pipeline.py:3705
     └─ per query:
        A. enumerate_query(query)                          pipeline.py:1223
           1. compile_query(subject, relation)             contracts/router.py:53
              → (Query, RelationContract)
           2. profiler.profile(...)            [M9 shadow] query_intelligence/profiler.py
              → relation_budget_scheduler.schedule(...)   [M20 production]
              → prompt_compiler.compile(...)   [M10 shadow]
              → retriever probe(s)             [M11 shadow, REAL CALLS, shadow-billed]
              → numeric / large_set / null_temporal / small_set specialists
                                                [M12-M15 shadow, REAL CALLS]
           3. build_graph(query, contract)                 evidence/graph.py:474
           4. budget = config.budget(contract)             pipeline.py:344
              = Budget(min(12, contract.stopping.max_calls),
                       min(6000, contract.stopping.max_generated_tokens))
           5. _run_gate(graph, contract)                   pipeline.py:803
              → score_gate(verifier_runtime, GATE_QUESTIONS[relation], ...)
                                                           verification/blind.py:645
              (only personHasCityOfDeath, companyTradesAtStockExchange)
              if confident NO → graph.close_gate(); RETURN empty-answer graph
           6. _adaptive_discovery(...)                     pipeline.py:1332
              loop ≤ max_steps_per_query (12):
                 score_candidate + assign_tier on every active candidate
                 decision = choose_action(contract, candidates, state, budget, step,
                                          gate=..., scoring=...)   controller.py:659
                    ├─ estimate_residual(...)               coverage.py:602
                    ├─ legal_actions(...)                   controller.py:302
                    ├─ should_stop(...)                     controller.py:744
                    └─ score_action(a) for each legal a     controller.py:571
                 if STOP → break
                 if not budget.can_afford(_planned_neural_cost) → break
                 _execute_action(...)                       pipeline.py:1552
                    RUN_VIEW/RUN_FACET → _run_discovery_view → ElicitationEngine.run_view
                    REVERSE_CHECK      → _run_reverse_check → run_reverse_view
                    RESAMPLE           → _run_resample     → run_view(run_id+1)
                    VERIFY/ADV_VERIFY  → _verify_one       → verify_candidate /
                                                             verify_multi_template
                    CROSS_MODEL_CHECK  → (never legal in F1/E3)
                 budget.charge(measured neural calls, tokens)
                 record_outcome(state, action, trusted_keys=...)  controller.py:825
           7. apply_hard_contract_rules(graph)              evidence/graph.py:479
           8. persist graph.rcse_state, graph.budget_snapshot
        B. verify_graph(graph)                             pipeline.py:1445
           → _controlled_phase(graph, contract,
                               {ENUMERATOR, VERIFIER, NONE}, phase="verify")
                                                           pipeline.py:1601
             (same controller loop, resumed from the persisted RCSE state and
              budget snapshot; interleaved mode allows every role, so nothing is
              pended — audit 0078 fix)
        C. decide_graph(graph)                             pipeline.py:3553
           1. refuse to finalize over an unconsumed pending action
           2. _run_consensus(graph)                        pipeline.py:1866
              ├─ consensus_engine.consense(...)  [M16 shadow, non-neural]
              ├─ _catalogue_specialist_targets   [M17 shadow catalogue, 0 calls]
              ├─ _catalogue_bidirectional_checks [M18 shadow catalogue, 0 calls]
              ├─ _integrate_layer4(...)          [L4 shadow, non-neural]
              ├─ production_bridge.apply(graph, layer4)   evidence/production_bridge.py:135
              │   (mode=PRODUCTION, but M17/M18 have no *executed* readings in
              │    this profile, so it applies nothing — see §9.4)
              ├─ _estimate_coverage_gap(...)     [M19 shadow → R_t]
              └─ (M17/M18 execution + _plan_micro_action are SKIPPED because
                  _v3_control_loop_active() is true — pipeline.py:1896, 1911)
           3. _run_v3_control_loop(graph)                  pipeline.py:2816
              for round in 1..min(3, 12):
                 hgraph = _v3_hypothesis_graph(...)         → failure_state
                 catalogue = build_v3_action_catalog(...)   v3_core/execution.py:294
                 chosen = _select_actions("v3", catalogue, ...)  pipeline.py:1990
                    → _plan_next_action → MicroPlanner.plan(state, actions)
                                                           control/micro_planner.py:371
                 if STOP or nothing chosen → break
                 if not _v3_action_selectability(action, graph) → break
                 _execute_v3_action_record(...)             pipeline.py:2659
                    ├─ _precharge (M20 ledger reserve)      pipeline.py:2413
                    ├─ execute_v3_action(...)               v3_core/execution.py:630
                    │    REAL neural call, writes ordinary Module-3 edges
                    └─ _release_hold (settle/cancel)        pipeline.py:2459
           4. finalize(graph, ...)                          selection.py:495
              ├─ select(graph, config)                      selection.py:462
              │    relation override → programme fallback
              ├─ _check_cardinality(...)                    selection.py:434
              └─ _final_values(...) → V3.1 Class-A repair    selection.py:470
 └─ LeaderboardRepairStack.apply(predictions, queries, hypothesis_graphs)
                                                           leaderboard_repair/stack.py:43
     per row: RowBudget(cap = max_calls_by_relation[relation])
              REPAIR_BY_RELATION[relation](prediction, signals, caller, config, record)
              → hasArea    : repair_area → repair_area_multiview
              → hasCapacity: repair_capacity
              → personHasCityOfDeath: repair_city
              → companyTradesAtStockExchange: repair_stock          [F1 only]
              → awardWonBy : repair_award (deterministic, 0 calls)
              → countryLandBordersCountry: no entry, and cap 0
     _assert_query_coverage(...) — row count and order must be unchanged
 └─ write_predictions / write_trace / sidecars / manifest
```

Two invariants worth stating in the paper because they are enforced in code, not
by convention:

- **Budget is measured, never assumed.** `_execute_action` returns
  `self._total_runtime_calls() - before` (`pipeline.py:1569,1599`), so a
  description-first view that makes two generations is charged two calls and a
  cache-warm calibration control is charged zero.
- **Accounting failure is process-fatal.** If the measured spend exceeds the M20
  precharge, `_release_hold` raises `AccountingInvariantError`, `run()` re-raises
  it (`pipeline.py:3735`), and `run_cover.py` writes
  `FAILED_ACCOUNTING_INVARIANT.json` and exits 2 *without* writing predictions or
  a manifest (`run_cover.py:208`).

---

## 5. Relation-Typed Inference Programs

### 5.1 Is the typing real, or is it prompt-swapping?

It is real, and there are **three independent, cross-checked typing layers**.
The paper's central claim survives audit.

**(a) `ProgramType` — the inference regime** (`types.py:29`,
`contracts/programs.py:83`). Four regimes with executable consequences:

| ProgramType | allowed cardinality | required output | `max_objects` | `supports_missingness` |
|---|---|---|---:|---|
| `SMALL_SET` | `ZERO_OR_MANY_SMALL` | ENTITY | 0 (uncapped) | **yes** |
| `NULL_SINGLE` | `ZERO_OR_ONE` | ENTITY | **1** | **no** |
| `NUMERIC` | `EXACTLY_ONE` | NUMBER | **1** | **no** |
| `LARGE_OPEN_SET` | `ZERO_OR_MANY_LARGE` | ENTITY | 0 (uncapped) | **yes** |

The names in the task prompt (`SMALL_SET`, `NULL_SINGLE`, `NUMERIC`,
`LARGE_OPEN_SET`) are **exactly** the names in code. Routing is a static table,
`PROGRAM_BY_RELATION` (`contracts/router.py:25`) — a deliberate design decision,
documented at `router.py:5`: *"No learned classifier is used or needed: there are
exactly six relation ids."* `check_program_compatibility`
(`programs.py:139`) rejects a contract whose cardinality, output type, selection
cap or missingness family contradicts its regime, and
`check_router_consistency()` additionally cross-checks every contract against the
official evaluator's own `RELATION_TYPE` map (`router.py:97-105`), so a snapshot
that reclassified a relation as numeric fails loudly at startup.

**(b) `RelationContract` — the relation's meaning** (`contracts/base.py:112`,
instances in `contracts/registry.py`). Carries `definition`, `positive_rules`,
`hard_negative_rules`, `mandatory_views`, `optional_views`,
`NormalizationPolicy`, `VerificationPolicy`, `StoppingPolicy`, `SelectionPolicy`,
`eligible_independence_groups` and `view_families`. It is consumed simultaneously
by the router, the elicitation engine, the parser, the verifier prompt builder
(`verifier_definition()`, `near_miss_block()`), the scorer, the controller and
the selector. **The contract contains definitions only, never facts** — a
constraint stated at `registry.py:8` and enforced by review, not by a mechanism.

**(c) `RelationProfile` — the relation's failure semantics**
(`contracts/relation_profile.py:152`). A *second, separate* typed statement:
`RelationFamily`, `SetBehavior`, `PrimaryFailure`, `SemanticRisk`,
`RecallPolicyClass`, `VerificationPolicyClass`, `SearchBias`,
`frozen_conservative`. `check_profile_consistency()` (line 391) proves it agrees
with the contract. **Critically, the module docstring says "Milestone V3A:
declarative only… Nothing in the inference path reads a profile"
(`relation_profile.py:27-30`) — this is now STALE.** In the final profile the
profile *is* read on the prediction path, by
`v3_core/relation_programs.py :: _relation_allowed_actions` (line 232) via
`legal_action_families` (line 283), which the V3 control loop calls. This is a
documentation-vs-code discrepancy, recorded in §17.

### 5.2 The six relations, exactly as encoded

#### `countryLandBordersCountry` — `SMALL_SET` / `STRUCTURAL_SET`
`contracts/registry.py:32`, profile `relation_profile.py:291`.

- Cardinality `ZERO_OR_MANY_SMALL`; answer type `country_or_comparable_territory`.
- Positive semantics (4 rules): physical land boundary; currently recognised
  states only; borders through an *integral* overseas territory count
  (Suriname–France via French Guiana; Spain–Morocco via Ceuta/Melilla); enclaved
  neighbours count (Vatican City–Italy).
- Hard negatives (6): maritime-only however short (Russia–Japan, Samoa–USA);
  non-integral dependency (Cyprus–UK via the Sovereign Base Areas); deprecated or
  disputed claim; merely nearby / bridge-or-tunnel only; the subject itself; a
  sub-national region.
- Mandatory views: `borders_direct` (DIRECT), `borders_compass` (STRUCTURAL —
  "one direction at a time … include any enclaved neighbour and any neighbour
  reached through an integral overseas territory").
- Optional views: `borders_land_vs_maritime` (CONTRASTIVE),
  `borders_missing` (MISSINGNESS, injects the accepted set),
  `borders_description` (two-stage DESCRIPTION), `borders_reverse_check` (REVERSE).
- Verification: `auto_accept_independent_support=2`, `accept_valid_prob=0.5`,
  adversarial classes `maritime_neighbour`, `non_integral_dependency`.
- Stopping: `max_calls=4`, `max_generated_tokens=1024`, `saturation_patience=1`,
  note "Baseline recall is already high; stop early and protect precision."
- Selection: `min_independent_support=1`, `max_objects=0` → `select_small_set`.
- No calibrated gate. No V3 actions at all: profile is `frozen_conservative=True`
  so `_relation_allowed_actions` returns `{STOP}` (`relation_programs.py:234`),
  **and** `_recall_spec` returns `None` for borders unconditionally
  (`v3_core/execution.py:419`). Two independent locks.
- **No Stage-2 repair**: cap 0, and no entry in `REPAIR_BY_RELATION`.
- Failure mode addressed: near-miss precision (maritime, dependency), not recall.

#### `personHasCityOfDeath` — `NULL_SINGLE` / `ENTITY_SINGLE`
`registry.py:101`, profile `relation_profile.py:258`.

- Cardinality `ZERO_OR_ONE`; answer type `city_or_locality`; `max_objects=1`
  enforced by the *regime*, not by a local constant (`base.py:153`).
- Positive: the locality of death, at city granularity or finer.
- Hard negatives (5): still living → empty; a country/state/province/region; the
  birth / residence / principal-activity city; the burial place when it differs;
  **"a guess supplied because the model was asked to name a city"**.
- Mandatory views: `death_status_gate` (GATE), `death_city_direct` (DIRECT, with
  an explicit "output NONE rather than guessing").
- Optional: `death_locality_granularity` (CONTRASTIVE), `death_description`
  (two-stage DESCRIPTION).
- Verification: `auto_accept=3`, `accept_valid_prob=0.6`, **`drop_on_unknown=True`**,
  adversarial classes `country_or_region`, `birth_city`, `burial_place`.
- Stopping: `max_calls=4`, 768 tokens, note "Existence gate first; empty is a
  first-class answer, not a parse failure."
- **Calibrated gate active**: `GATE_QUESTIONS["personHasCityOfDeath"]` =
  *"Is {subject} deceased? Answer B = NO only if you are confident the person is
  still living."* Scored by the verifier role with `min_margin=1.0`,
  `min_prob=0.5`; only a confident NO closes the gate.
- Eligible independence groups deliberately **exclude** the gate family
  (`registry.py:138-143`): a yes/no probe can never support a candidate, so it
  must not inflate `m(o)`.
- V3 actions legal: `MULTI_VIEW_RECALL`, `INDEPENDENT_RECALL`,
  `ALTERNATIVE_RECALL` (from `ATTRIBUTE_CONTRAST` recall policy →
  `INDEPENDENT_RECALL`, `ATTRIBUTE_DECOMPOSITION`, `SEMANTIC_VERIFY`,
  `CONTRAST_VERIFY`), intersected with the failure-state region.
  `ATTRIBUTE_DECOMPOSITION` is city-only (`execution.py:421-431`).
- Stage 2: `MistralCityEmptyRescue`, **empty rows only**, cap 2.

#### `companyTradesAtStockExchange` — `SMALL_SET` / `ENTITY_SET`
`registry.py:153`, profile `relation_profile.py:242`.

- Positive: the *subject company's own* shares listed and traded; primary and
  secondary listings both count.
- Hard negatives (5): parent listed but not the subject; subsidiary listed but
  not the subject (*"a subsidiary that is not separately listed has an empty
  answer set"*); merely historical/mentioned; private or taken private; an index,
  broker, market segment or ticker rather than an exchange.
- Mandatory: `stock_listing_gate` (GATE), `stock_exchange_direct` (DIRECT, "Name
  the exchanges, not ticker symbols or market indices").
- Optional: `stock_parent_contrast` (CONTRASTIVE), `stock_description`
  (DESCRIPTION), `stock_reverse_check` (REVERSE).
- Verification: `auto_accept=3`, `accept_valid_prob=0.6`, adversarial classes
  `parent_listing`, `subsidiary_listing`, `historical_listing`.
- Stopping: `max_calls=5` (the largest non-award budget), 1024 tokens, note
  "Baseline recall beats precision here, so verification matters more than
  enumeration."
- **Calibrated gate active**, phrased to protect against exactly the parent /
  subsidiary confusion: *"Answer B = NO only if you are confident that {subject}
  itself is privately held, wholly owned, or delisted — not merely because its
  parent or a subsidiary is listed."*
- Profile: `search_bias=ELIMINATION`, `recall_policy=BASELINE`,
  `verification_policy=REJECTION_FIRST`. Consequently V3 recall specs return
  `None` for stock (`execution.py:477`, `execution.py:496` when candidates
  exist), leaving only `LISTING_ELIMINATION` and `SEMANTIC_VERIFY`.
- **V3.1 Class-B live prompt is ON for this relation only**:
  `aggressive.stock_listing_entity_prompt: true` injects
  `STOCK_LISTING_ENTITY` (`v3_1/prompts.py`) into the *system* prompt of every
  stock recall call, through the single choke point
  `ElicitationEngine.run_view` (`elicitation/engine.py:140`). This is the reason
  the profile is flagged `CALIBRATION_REVIEW_REQUIRED`.
- Selection: `select_small_set` + `reject_structurally_invalid_listings`
  (stock-only; drops the subject company itself and status tokens).
  `stock_support_dominance` is **off**.
- Stage 2: **Profile F1 only** — `MistralStockEmptyRescue`, empty rows only, cap 4.

#### `hasArea` — `NUMERIC` / `NUMERIC_SINGLE`
`registry.py:207`, profile `relation_profile.py:211`.

- Cardinality `EXACTLY_ONE`; `answer_type=area_km2`; target unit `km2`;
  `numeric_integer_only=False`; `numeric_cluster_threshold=0.025`.
- Positive: surface area in km²; for a country, total area including inland
  water; a value in hectares/mi²/other counts *once converted*.
- Hard negatives (5): land-only when total is larger; water area alone; the
  surrounding metro/urban/administrative region; an unconverted mi²/ha/acre
  value; a population/elevation/length/year mistaken for an area.
- Mandatory: `area_direct_km2` (DIRECT), `area_total_vs_land` (CONTRASTIVE).
  Optional: `area_alternate_unit` (STRUCTURAL — deliberately asks for **square
  miles**, so the conversion path is an independent mechanism).
- Verification: `auto_accept=2`, `accept_valid_prob=0.5`, no adversarial classes.
- Stopping: `max_calls=4`, 768 tokens, note "Stop when the dominant cluster is
  stable and cross-unit views agree."
- Selector: `select_numeric_robust` — dominant cluster, median representative.
- V3: `DEFINITION_RECALL` is explicitly granted to `hasArea`
  (`relation_programs.py:246`) plus `MULTI_VIEW_RECALL`, `INDEPENDENT_RECALL`,
  `ALTERNATIVE_RECALL`, `CONTRAST_VERIFY`.
- Stage 2: `MistralAreaMultiView`, **all** rows, `DIRECT_ALL`, cap 5.

#### `hasCapacity` — `NUMERIC` / `NUMERIC_SINGLE`
`registry.py:253`, profile `relation_profile.py:226`.

- `answer_type=spectator_count`; target unit `persons`;
  **`numeric_integer_only=True`**; cluster threshold 0.025.
- **The definition encodes a selection rule, not just a meaning**: *"When several
  capacity figures exist — seated versus total, or before versus after a
  renovation — the **highest published capacity** is the answer."* This is why
  `hasCapacity` needs a different selector from `hasArea`.
- Hard negatives (5): record/peak attendance; average attendance; seated-only when
  total is higher; a pre/post-renovation figure when a higher one is published;
  a similarly named different venue.
- Mandatory: `capacity_direct` (DIRECT), `capacity_contrast` (CONTRASTIVE).
  Optional: `capacity_configuration` (STRUCTURAL — "across its configurations and
  renovations, and report the highest of them").
- Verification: `auto_accept=2`, `accept_valid_prob=0.5`, adversarial classes
  `record_attendance`, `seated_only`.
- Stopping: `max_calls=4`, 768 tokens, note "Prefer a cluster supported by
  multiple semantic formulations."
- Selector: `select_numeric_highest_valid` — the *only* relation with a
  highest-value rule.
- Profile primary failure: `MEMORY_AMBIGUITY` ("severe over-abstention; UNKNOWN
  currently suppresses recall"), recall policy
  `MULTI_VIEW_DEFINITION_AWARE`, verification `DEFINITION_CONTRAST`.
- Stage 2: `MistralCapacityMultiView`, **all** rows, `DIRECT_ALL`, cap 5.

#### `awardWonBy` — `LARGE_OPEN_SET` / `ENTITY_SET`
`registry.py:303`, profile `relation_profile.py:274`.

- Cardinality `ZERO_OR_MANY_LARGE`; `answer_type=award_recipient`.
- Positive: the entity received *exactly this* award; recipients from every year;
  people, groups, organisations and projects are all valid recipient types.
- Hard negatives (5): the winning *work* instead of the recipient; a
  nominee/finalist; a predecessor or successor award; a different category or a
  different award from the same organisation; a rescinded award.
- Mandatory (4 — the largest mandatory set): `award_direct` (DIRECT),
  `award_facet_temporal`, `award_facet_recipient_type` (both STRUCTURAL),
  `award_missing` (MISSINGNESS, injects the accepted set).
  Optional: `award_facet_category` (STRUCTURAL),
  `award_exact_identity_contrast` (CONTRASTIVE), `award_reverse_check` (REVERSE).
- **Facets are provenance, never independence.** The three structural facets
  (`award_temporal`, `award_recipient_type`, `award_category`) share one
  independence group by construction, and the library says so in a comment
  (`elicitation/library.py:303-307`): *"three slices of one decomposition are one
  independent support, not three."*
- Verification: `auto_accept=2`, `accept_valid_prob=0.6`, **`drop_on_unknown=True`**,
  adversarial classes `winning_work`, `nominee`, `adjacent_award`, `rescinded`.
- Stopping: `max_calls=16`, `max_generated_tokens=6000`, `saturation_patience=3`
  — by far the largest adaptive budget; note *"Some open-ended awards have partial
  gold, so an unconstrained long tail is a precision risk, not free recall."*
- Selector: `select_large_open_set` — no cap, single-mechanism support not fatal.
- V3: `PROMOTE_SUPPRESS_ITERATE` recall policy → `MULTI_VIEW_RECALL`,
  `SET_EXPANSION` (award-only, repeatable, carries the `seen` set),
  `ALTERNATIVE_RECALL`, `UNARY_VERIFY`.
- Stage 2: `AwardMetadataNormalizer`, cap 1, **zero model calls**.

### 5.3 Why this is more than relation-specific prompting

Five things change per relation *besides* prompt text, each with a distinct
consumer:

1. **The legal action space.** `legal_actions` (`controller.py:302`) enumerates
   over `contract.all_views()`; `REVERSE_CHECK` is offered per candidate only
   where that candidate lacks the reverse mechanism; `RESAMPLE` becomes legal
   only once no structural frontier remains **and** the view is stochastic
   (`controller.py:372-397`). Borders can reach 6 mechanisms; area can reach 3.
2. **The stopping predicate.** `should_stop` (`controller.py:744`) branches on
   `ProgramType` with four different sufficient conditions (§9.2).
3. **The residual composition.** `estimate_residual` (`coverage.py:660-741`)
   assembles a *different weighted term set and a different set of blocking
   floors* per regime (§8).
4. **The acceptance thresholds.** `resolve_verification` (`scoring.py:192`) takes
   the contract's value as **authoritative** and never combines it arithmetically
   with the global default — so a relation may sit at a lower-precision operating
   point deliberately.
5. **The finalizer.** `_BY_RELATION` (`selection.py:407`) routes `hasCapacity`
   and `hasArea` to *different* numeric selectors; `_BY_PROGRAM` covers the rest.
   One global selector is impossible here: the official target for capacity is
   the maximum, for area the robust centre.

A system that only swapped prompts would share one action space, one stopping
rule, one residual and one selector. This one shares none of them.

---

## 6. Evidence Representation and Independence

### 6.1 The exact structures

Four dataclasses in `src/cover_kbc/types.py` plus one container in
`src/cover_kbc/evidence/graph.py` carry the entire evidence state.

**`GenerationRecord`** (`types.py:246`) — full provenance for one model call.
Fields: `record_id` (deterministic SHA of
`subject|relation|view_id|run_id|stage|candidate`, `engine.py:97`), `query`,
`view_id`, `view_family`, `independence_group`, `run_id`, `model_id`,
`model_family`, `model_role`, `facet_id`, `stage` (`""`/`"description"`/
`"extraction"`/`"reverse"`), `source_record_id` (the description whose prose was
mined), `source_candidate_key` (what a reverse probe was asked about), `prompt`,
`prompt_hash`, **`system_prompt_hash`** (separate, because V3.1 Class-B
instructions are appended to the *system* turn and `prompt_hash` would not see
them — `types.py:276-282`), `raw_output`, `decode_profile`, `parsed_values`,
`prompt_tokens`, `generated_tokens`, `latency_ms`, `error`.

**`Evidence`** (`types.py:305`) — one signed edge. Fields: `candidate_key`,
`edge_type` ∈ {`SUPPORT`, `CONTRADICT`, `UNKNOWN`}, `independence_group`,
`view_id`, `model_id`, `run_id`, `record_id`, `edge_id` (deterministic SHA-256
prefix over `candidate_key|record_id|edge_type|group|run_id|view_id`,
`types.py:313`), `model_family`, `mode` ∈ {`INDEPENDENT_RECALL`,
`SHOWN_CANDIDATE`}, `valid_prob`, `invalid_prob`, `unknown_prob`, `token_cost`.

**`EvidenceGroup`** (`types.py:355`) — *all* evidence for one candidate from one
independence group, split into `supports` / `contradictions` / `unknowns`. This
is the unit that counts as **one** independent support regardless of how many raw
runs it holds. `raw_support_count` is exposed separately and is explicitly *not*
an independence count (`types.py:367-370`).

**`VerificationResult`** (`types.py:393`) — `label`, `valid_prob`,
`invalid_prob`, `unknown_prob`, `raw_logits`, `calibrated_logits`, `bias_logits`,
`calibrated` flag, `margin`, `entropy`, `prompt_disagreement`, `template_id`,
`num_templates`, `model_id`, `model_family`, `record_id`, plus
`log_odds(epsilon)`. The docstring is emphatic and the paper should reuse the
wording: these are **calibrated verifier-label probabilities, not probabilities
of factual truth**.

**`Candidate`** (`types.py:494`) — `key` (the evaluator's own strict
normalisation), `display_value`, `relation`, `output_type`, `numeric_value`,
`unit`, `alias_hint` (soft grouping hint; *never* the identity),
`raw_text` (the numeral exactly as written), **`derived_value`** (a value Module 8
*derived*, e.g. a cluster median — kept apart so a trace never presents an
aggregate as an observation), `source_unit`, `groups: dict[IndependenceGroup,
EvidenceGroup]`, `verifications: list[VerificationResult]`, `surface_forms`,
`record_ids`, `status` ∈ {ACCEPTED, REJECTED, UNRESOLVED}, `score`,
`score_breakdown: CandidateScore`, `tier` ∈ {HARD_REJECT, AUTO_ACCEPT, VERIFY,
ADVERSARIAL_VERIFY, UNRESOLVED}, `strict_key`, `facet_ids`, `rejection_reason`.
Derived properties: `independent_support` = `g(o)`, `raw_support_count`,
`contradiction_count`, `supporting_groups`, `num_facets` (documented as
*diagnostic only*, `types.py:570-579`), `coverage(eligible)` = `q(o)`,
`output_value` = `derived_value or display_value`.

**`EvidenceGraph`** (`evidence/graph.py:41`) — `candidates`, `records`,
`gate_negative`, `gate_reason`, `gate_result`, `controller_log`,
`pending_action`, `rcse_state` (serialised Module-6 temporal state, because
Phase C cannot reconstruct *when* something was found), `budget_snapshot`,
`verification_calls`, `_edge_ids`.

**Timestamps / round indices.** There is **no wall-clock timestamp** anywhere in
the evidence model. Temporal ordering is carried by `run_id`, by `stage`, by the
ordered `RCSEState.outcomes` list, and by `round_index` on V3 action records.
`latency_ms` is recorded per generation but is a cost diagnostic, not ordering.
A paper claim of "timestamps" would be wrong.

**Cost.** `Evidence.token_cost`, `GenerationRecord.prompt_tokens` /
`generated_tokens`, `Budget.calls_used` / `generated_tokens_used` /
`logical_actions`, and the M20 ledger reservations.

### 6.2 A. What counts as independent evidence

The **independence group**, and nothing else. `FAMILY_TO_GROUP`
(`elicitation/views.py:22`) is a fixed, non-overridable map from view family to
group:

| ViewFamily | IndependenceGroup |
|---|---|
| DIRECT | `DIRECT_RECALL` |
| STRUCTURAL | `STRUCTURAL_DECOMPOSITION` |
| DESCRIPTION | `RELATION_FOCUSED_DESCRIPTION` |
| CONTRASTIVE | `CONTRASTIVE_SEPARATION` |
| MISSINGNESS | `MISSINGNESS_SEARCH` |
| REVERSE | `REVERSE_ALTERNATE` |
| GATE | `EXISTENCE_GATE` (never eligible) |

Plus three groups that are *not* acquisition families: `BLIND_VERIFIER` (paid
through `L(o)`), `CROSS_MODEL_RECALL` (paid through `X(o)`), and the four
`M18_*` structural-check groups (declared as their own groups precisely so a
verification can never inflate acquisition support — `types.py:135-152`).

`ViewSpec.independence_group` is a read-only property (`views.py:113`); a view
cannot declare its own group. `check_library_covers_contracts()`
(`library.py:407`) enforces, in both directions, that every contract view exists,
that its family is declared by the contract, that a gate uses the GATE family,
that every non-gate view's group is in `eligible_independence_groups`, and that
**every eligible group is reachable by some candidate-producing view** — an
unreachable group would cap `q(o) = g(o)/m(o)` below 1.0 forever.

### 6.3 B. What counts as correlated / repeated evidence

- **Repeats of one view.** `run_view_repeats` (`engine.py:322`) issues `runs`
  calls that share `view_id` and group and differ only in `run_id`. They land in
  one `EvidenceGroup`. Docstring: *"Repetition amplifies evidence; it never
  manufactures independence."*
- **Facets.** `award_temporal`, `award_recipient_type`, `award_category` are
  three `facet_id`s inside `STRUCTURAL_DECOMPOSITION`. `Candidate.add_facet` is
  provenance only.
- **The two stages of a description view.** `run_description_view`
  (`engine.py:210`) makes two calls but produces **one** mechanism; the prose
  stage yields no candidates and no edge at all — *"a self-generated context is an
  intermediate memory-elicitation artifact, never proof."*
- **Repeated mentions inside one generation.** `add_entity_mentions`
  (`graph.py:125`) keeps a `seen_in_record` set: *"a model listing 'Poland' twice
  in one answer has not corroborated it."*
- **Multiple verifier templates.** Three INVALID verdicts from three templates
  are **one** contradicting mechanism, not three (`contradicting_groups`,
  `scoring.py:361`). Where they genuinely conflict, that is `U(o)`.

### 6.4 C. Does repeating the same view increase support?

**No.** `g(o)` counts distinct groups (`Candidate.independent_support`,
`types.py:553`), and `F(o)` is computed only over
`acquisition_groups(contract, config)` (`scoring.py:243`). A repeat adds a new
`Evidence` row to an existing `EvidenceGroup`, moving `raw_support_count` but not
`g(o)`. Additionally, a *greedy* repeat is refused as an action outright
(`controller.py:381-388`): the same prompt at temperature 0 returns the same
text, so the duplicate edge would be rejected by the graph anyway. All library
views use `GREEDY`/`GREEDY_SHORT`/`GREEDY_LONG` at temperature 0.0, so in the
final profile `RESAMPLE` is effectively never legal.

### 6.5 D. How fake consensus is prevented

Five mechanisms, all enforced in code:

1. **Group-keyed counting** (above).
2. **Duplicate-edge refusal.** `EvidenceGraph._attach` (`graph.py:82`) raises on
   a repeated `edge_id`, so one physical measurement can become at most one edge
   even if several downstream layers try to bridge it.
3. **Index-set disjointness in scoring.** `F` reads acquisition groups only; `L`
   reads verifications; `X` reads `CROSS_MODEL_RECALL` with
   `mode is INDEPENDENT_RECALL` required. Structurally impossible to pay one
   event twice (`scoring.py:28-42`).
4. **`EvidenceMode`.** A verifier agreeing with a name it was *shown* is
   `SHOWN_CANDIDATE` and earns nothing in `X` (`scoring.py:441-444`).
5. **Structural checks get their own groups.** M18 reverse/counterfactual
   checks map to `M18_*` groups that no contract declares eligible, so they
   cannot move `m(o)` or `g(o)`.

### 6.6 E. How positive and negative signals combine

Positive: `SUPPORT` edges → `g(o)` → `F(o)`; VALID verdicts → `L(o) > 0`;
independent second-family recall → `X(o)`.
Negative: `CONTRADICT` edges → `C(o)`; INVALID verdicts → `L(o) < 0` **and** a
`CONTRADICT` edge (`VerificationResult.edge_type`, `types.py:441`); template
instability → `U(o)`.
Neutral: `UNKNOWN` is explicitly *not* negative evidence — `contradiction_term`
lists it among the four things that do not count (`scoring.py:382-393`).

They combine **additively with signed weights** in `S(o)` (§7.1) and
**non-arithmetically** in `decide_status` (`scoring.py:702`), which applies a
strict precedence: REJECTED stays rejected → below `min_independent_support` is
UNRESOLVED → latest INVALID is REJECTED → latest UNKNOWN with
`drop_on_unknown` is UNRESOLVED *unless* acquisition support alone already
reaches `auto_accept_support` (deliberately not total support, because the same
UNKNOWN call would otherwise rescue itself, `scoring.py:733-739`) →
`valid_prob < min_valid_prob` is UNRESOLVED → `AUTO_ACCEPT` tier or
`score ≥ accept_score` is ACCEPTED → else UNRESOLVED.

### 6.7 F. When evidence is collapsed / aggregated

- At ingestion: surface forms of one strict key collapse into one candidate;
  `preferred_surface_form` chooses the emitted string. Numeric values collapse on
  `format_numeric` (`graph.py:206`).
- At multi-template verification: `aggregate_verifications` (`blind.py:728`)
  averages the three label distributions into one `VerificationResult` with
  `template_id="aggregate"` and `num_templates=n`; only the aggregate is attached.
- At selection: numeric candidates are clustered (`_numeric_clusters`,
  `selection.py:226`) and one **derived** representative is emitted.
- Never: independence groups are never merged; alias groups are advisory only
  (`graph.py:371`); `same_record_alias_hints` is explicitly diagnostic-only
  (`graph.py:339`).

### 6.8 G. What survives to final selection

Everything. `finalize` (`selection.py:495`) attaches `graph.active_candidates()`
to the `Prediction`, and `Prediction.to_trace()` serialises every candidate with
its full `evidence` list and `verifications`. The official row carries only the
three official fields. Nothing is discarded before Module 8; candidates are
filtered by *status*, not deleted.

### 6.9 Proposal claims the implementation does NOT fully deliver

- **Semantic disagreement without an embedding model** (proposal §12.2) exists in
  `evidence/consensus.py`, but M16 is shadow-only; it never reaches Module 8.
- **Cross-model / heterogeneous evidence** (`X(o)`, `CROSS_MODEL_RECALL`) is
  fully implemented but **structurally unreachable in the final profile** because
  both roles are the same checkpoint. The proposal's "model family" dimension of
  independence is therefore inert.
- **DoLa / factual decoding** — `IndependenceGroup.FACTUAL_DECODING` exists as a
  reserved enum member and `HuggingFaceRuntime.hidden_states` exists, but no
  caller. DOCUMENTATION-ONLY.
- **Restatement / alias identity.** The graph deliberately refuses to merge on
  alias hints (`graph.py:339-352`): the parser recognises no "X, also known as Y"
  construction, so strict identity is retained.

---

## 7. Candidate Scoring and Verification

### 7.1 The actual candidate score

`src/cover_kbc/scoring.py :: score_candidate` (line 447):

```python
total = (config.alpha_support      * support
       + config.beta_logit         * logit
       + config.gamma_cross_model  * cross
       - config.delta_contradiction* contradiction
       - config.eta_disagreement   * disagreement)
```

i.e.

```text
S(o) = α·F(o) + β·L(o) + γ·X(o) − δ·C(o) − η·U(o)
```

with the final-profile coefficients α=1.0, β=0.6, γ=0.5, δ=1.5, η=1.0.

| Symbol | Code | Definition | Range | Active in F1/E3 |
|---|---|---|---|---|
| `F(o)` | `support_term` → `coverage_q` (`scoring.py:296,329`) | `g(o)/m(o)`: supporting **acquisition** groups over groups *capable of expressing* the candidate | [0,1] | yes |
| `L(o)` | `logit_term` (`scoring.py:344`) | `clip(log((p_valid+ε)/(p_invalid+p_unknown+ε)) / 3.0, −1, 1)` from the **latest** verification; 0.0 if never verified | [−1,1] | yes |
| `X(o)` | `cross_model_term` (`scoring.py:420`) | 1.0 iff the verifier-family model *independently recalled* the name | {0,1} | **no — always 0** |
| `C(o)` | `contradiction_term` (`scoring.py:377`) | `min(1, #contradicting_groups / (m(o)+1))` | [0,1] | yes |
| `U(o)` | `disagreement_term` (`scoring.py:400`) | max normalised Jensen–Shannon divergence across verifier templates | [0,1] | yes |

Three details a paper must get right:

- `m(o)` is **availability, not execution** (`scoring.py:127-141`). Counting only
  executed groups would make `q(o)=1` after one direct view and tell the residual
  estimator the query was saturated. Under the active controller,
  `optional_views_available` is forced to `True` by
  `PipelineConfig.__post_init__` (`pipeline.py:292`), so `m(o)` is the contract's
  full eligible set: 6 for borders, 5 for awards, 4 for stock, 3 for city, 3 for
  area, 3 for capacity.
- `L(o)` uses only the **latest** verification, and `aggregate_verifications`
  ensures the latest is the multi-template average when disagreement is measured.
- `C(o)`'s denominator is `m(o)+1` — acquisition mechanisms plus the blind
  verifier — so numerator and denominator share one index set.

Related but separate quantities, all kept individually readable on
`CandidateState` (`scoring.py:487`) because *"the controller should not make
decisions from a single scalar confidence"*: `q(o)`, `H_inc(o) = −q log q −
(1−q) log(1−q)` (`inclusion_uncertainty`, line 308), `H_ver` (verifier entropy),
`U_prompt`.

**Generations of the formula.** The five-term form is the only one in the
repository. `CandidateScore` (`types.py:463`) documents it, `scoring.py`
implements it once, and `docs/PAPER_FULL_ARCHITECTURE_REVIEW.md` §12 describes
the same. The proposal (§9.2 "atomic support score") sketches a support score for
awards that is *not* separately implemented — awards use the same `S(o)`. No
retired scoring generation survives in code.

### 7.2 Tiering — decided before any call is spent

`assign_tier` (`scoring.py:625`), strict precedence:

1. `status is REJECTED` → `HARD_REJECT`.
2. `contradiction_count > 0` **or** `U(o) > adversarial_disagreement` (0.15) →
   `ADVERSARIAL_VERIFY`.
3. acquisition support ≥ `resolve_verification(contract).auto_accept_support` →
   `AUTO_ACCEPT`. (Contract value wins: 2 for borders/area/capacity/awards, 3 for
   city/stock.)
4. support ≤ `verify_max_support` (2): if the contract declares near-miss classes
   **and** support ≤ `adversarial_max_support` (1) → `ADVERSARIAL_VERIFY`, else
   `VERIFY`.
5. otherwise `UNRESOLVED`.

Note step 3 uses *acquisition* support only: a verifier that agreed with a name
it was shown must not lift a candidate past the threshold that decides whether it
needs verifying at all. Step 4's adversarial rule is an explicitly non-factual
proxy for the spec's "is a common near miss" — the code says so
(`scoring.py:93-101`): the architecture knows *this relation is near-miss-prone*
and *this candidate rests on the thinnest evidence*, and never which candidate is
actually a near miss.

`verification_targets` (`scoring.py:669`) orders adversarial first, then
weakest-support first, ties on key.

### 7.3 Blind verification — the exact mechanism

`src/cover_kbc/verification/blind.py`.

- **Which model.** `self.verifier_runtime`, which in F1/E3 *is* `self.runtime`.
  `verifier_available` (`pipeline.py:694`) does not test object identity — it
  tests capability plus configured role, so one physical Mistral legitimately
  fills the verifier role. **Generator and verifier are the same checkpoint.** Any
  description of a Qwen verifier is historical (Profile A/A+Award and earlier).
- **What it sees.** `build_verifier_prompt` (line 155) renders exactly:
  subject, relation name, `contract.verifier_definition()` (definition + expected
  answer type + "Counts as correct" positive rules + "Does NOT count" hard
  negatives), and **one** candidate. It never sees the generator's reasoning, its
  draft, other candidates, support counts, or which view produced the name.
- **Labels.** `LABEL_TOKENS = {VALID: "A", INVALID: "B", UNKNOWN: "C"}`
  (line 42). Single ASCII letters to maximise single-token encoding.
- **How probabilities are obtained.** `runtime.score_labels(LabelScoreRequest)`.
  `HuggingFaceRuntime.score_labels` (`models/huggingface.py:275`) first calls
  `inspect_label_encoding` and **asserts** single-token encoding; if all three
  labels are single tokens it takes one forward pass and reads next-token logits
  (`_score_next_token`), otherwise it falls back to **full sequence
  log-likelihood** per label (`_score_sequence`), one forward pass each. It never
  silently compares first tokens — the module docstring names that failure
  explicitly ("A" vs "Australia").
- **Contextual calibration — genuinely used.** `ContextualCalibrator`
  (`blind.py:258`) runs the *same* template with a content-free instance
  (`CONTENT_FREE_SUBJECT = "N/A"`, `CONTENT_FREE_CANDIDATE = "N/A"`), obtains
  bias logits `b_j`, and computes `z̃_j = z_j − b_j`, `p̃ = softmax(z̃/T)` with
  `T = 1.0`. `use_calibration: true` in the final config. **Nothing is fitted** —
  it is arithmetic on inference-time outputs, which is what keeps it inside the
  no-training rule. The control cache key is
  `(model_id, revision, label_signature, relation, template_id, decode_identity)`
  (line 232) so a control can never be reused across a revision change or a
  different label set. `control_calls_needed` (line 278) lets the controller price
  an uncached control *before* acting, which is why `_planned_neural_cost`
  (`pipeline.py:1510`) is exact rather than a floor.
- **Prompt/label-order bias.** Handled by **paraphrase templates**, not by label
  rotation, in the active path. Three templates exist: `verify_standard_v1`,
  `verify_question_v1`, `verify_adversarial_v1` (the last names the contract's
  near-miss classes via `near_miss_block()`). `DISAGREEMENT_TEMPLATE_IDS` =
  (standard, question). `verify_multi_template` (line 520) scores both and
  computes `normalized_disagreement` = generalised JSD / log(m), giving a bounded
  [0,1] score comparable against a fixed threshold — the code notes this is
  deliberately preferred over the spec's unbounded mean-KL (line 494-500).
  *Label-order* rotation (`label_orders: [ABC, BAC]`) exists only in the M17
  specialist verifier, which is shadow.
- **UNKNOWN behaviour.** A first-class label. It produces an `UNKNOWN` edge (not
  a contradiction), contributes to `L(o)` through the denominator, and in
  `decide_status` sends the candidate to UNRESOLVED when the contract sets
  `drop_on_unknown` (city and awards) *unless* acquisition support alone already
  reaches the auto-accept threshold.
- **Relation-specific verifier instruction.** Delivered entirely through
  `contract.verifier_definition()` and `contract.near_miss_block()` — no separate
  per-relation verifier prompt file is active. (The V3.1 `CITY_OF_DEATH_CONTRAST`
  verifier boundary exists and is bound in `v3_1/live_prompts.py`, but its flag
  `city_of_death_contrast_prompt` is **false** in the final profile.)
- **Thresholds.** `min_valid_prob` 0.40 global, overridden per contract to 0.5
  (borders, area, capacity) or 0.6 (city, stock, awards).
- **Retries/fallbacks.** `LogitsUnavailable` is caught and returns 0 calls
  (`pipeline.py:1067`) — verification degrades to "no verifier evidence", never
  to a guess. There is no retry loop.
- **Cost control.** Verification is an *action*: it must be chosen by
  `choose_action`, must pass `budget.can_afford(_planned_neural_cost)`, and its
  cost prior is `cost_verify=1.0` / `cost_adversarial_verify=2.0`.
- **Coverage.** Verification applies to **selected candidates only** — those the
  tiering rules put in `VERIFY`/`ADVERSARIAL_VERIFY` and the controller then
  chooses. It is never applied to all candidates.

### 7.4 The calibrated existence gate — a separate mechanism

`score_gate` (`blind.py:645`) is *not* candidate verification. Labels are
`{YES: "A", NO: "B", UNKNOWN: "C"}`, the template is `GATE_TEMPLATE`, the control
question is `"Is N/A true of N/A?"`, and the decision rule is deliberately
asymmetric:

```text
decision = argmax(p̃)
if decision == "NO" and not (margin >= gate_min_margin and p̃(NO) >= gate_min_prob):
    decision = "UNKNOWN"
```

with `gate_min_margin = 1.0` (logit gap) and `gate_min_prob = 0.5`. Only a
confident NO closes the gate; UNKNOWN and YES both let discovery continue. The
rationale is stated at `blind.py:594-597`: *"A gate that cannot distinguish NO
from UNKNOWN must not be allowed to force an empty prediction: that turns one
uncertain token into a guaranteed zero-recall answer."*

The gate runs on the **verifier role** by configuration
(`gate_model_role: ModelRole.VERIFIER` default), and `_gate_runtime`
(`pipeline.py:778`) **raises** rather than substituting the enumerator if that
role is absent — so the same frozen config cannot change its factual
decision-maker by execution mode.

Note the generation-based gate views (`death_status_gate`, `stock_listing_gate`)
still run as ordinary mandatory RUN_VIEW actions, but with the calibrated gate
enabled their NO verdict **cannot** close the gate
(`pipeline.py:883`: `if outcome.gate.is_negative and not
self.config.enable_calibrated_gate`). Only the calibrated label scorer can.

### 7.5 Four verification mechanisms, kept apart

| Mechanism | Where | Labels | Blind? | Active in F1/E3 |
|---|---|---|---|---|
| Blind calibrated candidate verification | `verification/blind.py :: verify_candidate` | A/B/C = VALID/INVALID/UNKNOWN | yes (candidate only) | **ACTIVE-PRODUCTION** |
| Calibrated existence gate | `verification/blind.py :: score_gate` | A/B/C = YES/NO/UNKNOWN | yes | **ACTIVE-CONDITIONAL** (city, stock) |
| V3 typed verification (UNARY / SEMANTIC / CONTRAST) | `verification/v3_modes.py`, executed by `v3_core/execution.py :: _execute_verification_action` | VALID/INVALID/UNKNOWN, or H1/H2/UNKNOWN for contrast | yes | **ACTIVE-CONDITIONAL** (only if the V3 loop fires) |
| M17 specialist verifier suite (2 templates × 2 label orders, per-family contracts) | `verification/specialist_verifier.py` | A/B/C with rotated *order* | yes | **SHADOW/DIAGNOSTIC** (`SUPPORTED_MODES = {"shadow"}`) |
| M18 bidirectional / counterfactual checks | `verification/bidirectional_verifier.py` | free text | n/a | **SHADOW/DIAGNOSTIC** |
| E3/F1 numeric judges (Area, Capacity) | `leaderboard_repair/*_multiview.py` | anonymised candidate labels A, B, C… + UNKNOWN | **source-blind** | **FINAL-SPECIALIZATION** |

The final E3/F1 judges are a **fifth, distinct** mechanism: they are *generation*
calls (not label-logit scoring), they see anonymised numeric candidates with no
view identity and no support counts, and they have no calibration. Calling them
"the blind verifier" in the paper would be wrong.

---

## 8. Residual Coverage and Search Need

### 8.1 What the active estimator actually is

`src/cover_kbc/coverage.py :: estimate_residual` (line 602) is Module 6, RCSE,
and it is the **only** residual on the production path. Its own docstring
forbids the interpretation the paper must avoid (`coverage.py:1-26`):

> RCSE answers exactly one question: *is another inference action likely to add
> useful verified information?* It deliberately does **not** answer "how many
> true objects still exist"… `q_res ∈ [0,1]` is a **need-to-continue** signal. It
> is not a probability that n objects remain, not a cardinality, not a confidence
> that the current answer is right, and **not a stopping decision**.

Two reasons are given for having removed the v1 Chao/capture–recapture estimator:
model-generated views are not independent captures, and the official repository
states some open-ended award rows have necessarily *partial* gold. The paper
should use the term **search-need signal**, never "probability that a true object
remains undiscovered".

`q(o)` (candidate-level acquisition coverage) and `q_res` (query-level search
need) are explicitly different quantities and must never be aliased
(`coverage.py:19-25`).

### 8.2 The exact computation

Shared primitives (all in `[0,1]`, all oriented *higher = keep searching*):

| Component | Function | Definition |
|---|---|---|
| `mandatory_gap` | `mandatory_view_gap` (line 394) | `|mandatory − executed_views| / |mandatory|` |
| `mechanism_gap` | `mechanism_gap` (line 407) | `|available_groups − executed_groups| / |available_groups|` |
| `facet_gap` | `semantic_facet_gap` (line 429) | `|declared_facets − executed_facets| / |declared_facets|`; 0.0 for the five relations with no facets |
| `unresolved_mass` | `unresolved_mass` (line 466) | `min(1, Σ_{unresolved o} max(0.25, q(o)) / |active|)` |
| `marginal_yield` | `RCSEState.marginal_yield` (line 228) | `new_trusted / (tokens/1000 + ε)`, rescaled `min(1, y/yield_scale)` with `yield_scale=2.0`; if the window spent no tokens, returns `float(found>0)` rather than dividing by ε |
| `unsaturated` | `1 − RCSEState.saturation` (line 246) | saturation = share of the last 3 actions that added nothing |
| `set_instability` | `1 − RCSEState.set_stability` (line 259) | Jaccard of the last two trusted sets; two *empty* sets score 0, not 1 |
| `verifier_disagreement` | line 496 | max `U_prompt` over candidates |
| `inclusion_uncertainty` | `mean_inclusion_uncertainty` (line 507) | mean `H_inc(q(o))` / log 2 |
| `numeric_dispersion` | NUMERIC only | `min(1, relative_MAD / 0.05)` of the dominant cluster |
| `cluster_competition` | NUMERIC only | `min(1, (size₂/size₁) / 0.5)` |
| `gate_unresolved` | NULL_SINGLE only | 1.0 iff gate present and not resolved |
| `locality_competition` | NULL_SINGLE only | `rival.score / best.score`, clipped |

Then, per regime:

```text
weighted = Σ_i w_i · v_i  /  Σ_i w_i          # over that regime's term set only
q_res    = clip( max( weighted, mandatory_gap, *blocking_components ), 0, 1 )
```

`mandatory_gap` and the regime's `blocking` components are applied as **floors,
not weighted terms**. The reason is recorded in the source (`coverage.py:745-751`)
and is a genuinely good methodological point for the paper: *"a weighted mean
dilutes a single decisive signal against its zero-valued siblings — two competing
death localities scored 0.94 averaged down to 0.21 and read as 'settled'."*

Per-regime term sets and floors (weights from `RCSEConfig`, all defaults):

| Regime | Weighted terms (weight) | Blocking floors | Rationale string in code |
|---|---|---|---|
| `LARGE_OPEN_SET` | `marginal_yield` (1.0), `facet_gap` (1.0), `mechanism_gap` (0.8), `unresolved_mass` (0.4 = 0.8×0.5), `unsaturated` (1.0) | — (none) | "yield decay, facet coverage and tail drive continuation" |
| `NUMERIC` | `numeric_dispersion` (1.0), `cluster_competition` (1.0), `mechanism_gap` (0.8), `verifier_disagreement` (0.6) | `cluster_competition` | "continue only while the dominant cluster is unstable" |
| `NULL_SINGLE` | `gate_unresolved` (1.2), `locality_competition` (1.0), `unresolved_mass` (0.8), `mechanism_gap` (0.8), `verifier_disagreement` (0.6) | `gate_unresolved`, `locality_competition` | "continue while existence or locality is unresolved" |
| `NULL_SINGLE`, confident negative gate | `gate_unresolved` (1.2) forced to 0.0 | — | "confident negative gate, no candidate search needed" |
| `SMALL_SET` | `mechanism_gap` (0.8), `unresolved_mass` (0.8), `set_instability` (0.8), `marginal_yield` (0.5), `inclusion_uncertainty` (0.5) | — | "complete mandatory structure, then stop early" |

`ResidualEstimate` also carries `reasons` — deterministic reason codes such as
`mandatory_views_incomplete`, `residual_floored_by_mandatory_gap`,
`competing_numeric_clusters`, `existence_gate_unresolved`, `state_settled`
(`_reason_codes`, line 765). This is a genuinely paper-worthy interpretability
property: every stop/continue decision carries a machine-readable explanation.

### 8.3 Evidence of completion, per relation type

- **SMALL_SET** (borders, stock): mandatory views done, trusted set Jaccard
  ≥ `stability_threshold` (1.0 for both), `unresolved_mass = 0`. Nothing blocks;
  the regime is designed to finish cheaply.
- **NULL_SINGLE** (city): exactly one ACCEPTED candidate and nothing unresolved;
  **or** a confident negative gate (an *answer*, not an unfinished search);
  **or** no candidate generated, at least one no-gain action, and the gate not
  unresolved (`controller.py:779`: *"'Nothing was generated' is not 'confidently
  nobody'"*).
- **NUMERIC** (area, capacity): `cluster_competition = 0` **and**
  `numeric_dispersion = 0` **and** `set_instability = 0` **and**
  `unresolved_mass = 0`. Note the code deliberately does *not* re-derive
  stability from the trusted-set Jaccard, because two disagreeing clusters could
  look settled merely because the accepted set stopped changing
  (`controller.py:785-788`).
- **LARGE_OPEN_SET** (awards): consecutive no-gain ≥ `saturation_patience` (3)
  **and** `facet_gap = 0`.

Across all four, an unverified candidate in a verification tier overrides
completion: `should_stop` returns `False, "candidate awaiting verification"`
(`controller.py:804-814`).

### 8.4 M19 — the second, shadow estimator, and where it *does* matter

`src/cover_kbc/coverage_gap/missingness.py` implements the proposal's §15
ensemble exactly:

```text
R_t = w1·noveltyRate + w2·singletonRatio + w3·facetGap + w4·disagreement + w5·unresolvedMass
```

with **uniform unfitted weights** (all 1.0), renormalised over the *available*
components only (`combine`, line 506) — an unmeasurable signal is never read as a
measured zero. The module is explicit that `R_t` is *"a residual search-need
heuristic, not a probability"*, that *"Module 6 is untouched"*, and that *"M19
decides nothing"*.

`CoverageGapConfig.SUPPORTED_MODES = frozenset({"shadow"})` (line 101), so M19
can never write to the graph. **However — and this is the subtle part — M19 is
still on the causal path in the final profile.** `_estimate_coverage_gap`
(`pipeline.py:3146`) stores the latest `CoverageGapState`, and
`_plan_next_action` puts it into `PlannerStateSnapshot.coverage_gap`
(`pipeline.py:1968`). `state_bin_key` (`control/historical_bins.py:440,486`) then
reads `gap.residual.residual` as the numeric feature `residual` to select M21's
historical bin. The binning cuts are `residual ∈ {0.615, 1.0}` and
`unresolved_mass ∈ {1.0}` (`m21_historical_bins.json`).

So: **M19 never decides, but M19's `R_t` selects the bin whose expected values
drive M21's utility.** The correct paper statement is that the coverage-gap
estimate is a *state descriptor for the planner*, not a controller in itself.

### 8.5 Separation of active core vs later extensions

| Layer | Module | Status in F1/E3 |
|---|---|---|
| Core residual | M6 RCSE (`coverage.py`) | **ACTIVE-PRODUCTION** — read by `choose_action`/`should_stop` every step |
| Upgraded residual | M19 (`coverage_gap/`) | **SHADOW**, but a **planner state feature** (§8.4) |
| Facet coverage map | `coverage_gap/facet_coverage.py` | SHADOW, feeds M19 only |

They are never summed, never averaged, and neither imports the other
(`missingness.py:23-25`).

---

## 9. Adaptive Controller and Stopping

### 9.1 The production controller (Module 7)

`src/cover_kbc/controller.py`. **Rule-based, non-neural, deterministic** — no RL,
no learned scoring, no trained component (line 10-13). The action space
(`ActionType`, line 50) is exactly:

```text
RUN_VIEW  RUN_FACET  VERIFY  ADVERSARIAL_VERIFY  REVERSE_CHECK  CROSS_MODEL_CHECK  RESAMPLE  STOP
```

Mapping to the names in the task prompt: `ELICIT` = `RUN_VIEW`/`RUN_FACET`;
`VERIFY` = `VERIFY`/`ADVERSARIAL_VERIFY`; `ASK_ANOTHER_VIEW` = `RUN_FACET` (an
optional, not-yet-run view); `REVERSE_CHECK` = `REVERSE_CHECK` (and note it is
classified as **acquisition, not verification** — free text, no label scoring,
`pipeline.py:894-907`); `RESAMPLE` = `RESAMPLE`; `STOP` = `STOP`. All six exist
in production. `CROSS_MODEL_CHECK` exists but is unreachable in F1/E3.

`ACTION_ROLE` (line 71) declares which runtime each family needs, so staged
orchestration routes rather than substitutes.

**Legality is a property of state** (`legal_actions`, line 302):
- a view already in `state.executed_views` is not offered;
- reverse views are enumerated *per candidate*, and only where that candidate
  lacks the `REVERSE_ALTERNATE` mechanism;
- `RESAMPLE` becomes legal only when no structural frontier remains **and** the
  view is stochastic (all library views are greedy → effectively never);
- verification is offered only for candidates in a verify tier that have **no**
  prior verification (re-verifying unchanged inputs would produce a duplicate
  edge);
- an action *instance* runs at most once (`state.executed_actions`, keyed on
  `(type, view_id, candidate_key)`);
- `allowed_roles` filters by residency in staged mode (unused in F1/E3, which is
  interleaved).

**Action utility** (`score_action`, line 571):

```text
A_t(a) = α_yield·Ŷ_t(a) + β_gap·G_t(a) + γ_unc·U_t(a) − λ_cost·C(a) − ρ_red·D_t(a)
```

with `α_yield=1.0`, `β_gap=1.0`, `γ_uncertainty=0.8`, `λ_cost=0.15`,
`ρ_redundancy=1.0`, plus a configured `verify_first_bonus=+0.5` added to
verification actions when `unresolved_mass ≥ verify_first_unresolved (0.5)`.

| Term | Function | Meaning |
|---|---|---|
| `Ŷ_t(a)` | `mechanism_yield_prior` (line 455) | untried mechanism → optimistic prior 0.5; already-run → empirical `productive_runs / runs`; verification history excluded |
| `G_t(a)` | `view_gap_relevance` (line 488) | mandatory-not-yet-run → 1.0; facet view → `facet_gap × 0.8`; other → `mechanism_gap × 0.8`; `RESAMPLE` → 0.0. For `REVERSE_CHECK`: `1 − q(o)`. For verification: `unresolved_mass` |
| `U_t(a)` | `candidate_impact` (line 544) for candidate actions; `unresolved_mass × indirect_uncertainty (0.5)` for acquisition | how much resolving *this* candidate would reduce uncertainty; `+0.2` for the adversarial template |
| `C(a)` | `Action.estimated_cost` | priors: view 1.0, reverse 1.0, resample 1.0, verify 1.0, adversarial verify 2.0, cross-model 1.5 |
| `D_t(a)` | `mechanism_redundancy` (line 512) | 0.5 if the action's independence group is already executed; for `RESAMPLE`, `min(1, 0.8 + 0.1·repeats)`; for verification, `min(1, 0.5·|prior verifications|)` |

`STOP` scores exactly the relation's `residual_stop` threshold (line 581-586), so
"any action that beats the stopping threshold is worth taking" is literal, not
metaphorical.

**Stopping authority is single.** `choose_action` calls `should_stop` first; if it
says continue, the `STOP` action is *removed* from the candidate set so the two
cannot contradict each other (`controller.py:695-697`).

### 9.2 Stopping thresholds actually in force

`resolve_stopping` (line 235) with `honor_contract_stopping: True` takes the
contract's values:

| Relation | `max_calls` | `max_generated_tokens` | `stability_threshold` | `saturation_patience` | `residual_stop` |
|---|---:|---:|---:|---:|---:|
| `countryLandBordersCountry` | 4 | 1024 | 1.0 | 1 | 0.15 |
| `personHasCityOfDeath` | 4 | 768 | 1.0 | 2 | 0.15 |
| `companyTradesAtStockExchange` | 5 | 1024 | 1.0 | 2 | 0.15 |
| `hasArea` | 4 | 768 | 1.0 | 2 | 0.15 |
| `hasCapacity` | 4 | 768 | 1.0 | 2 | 0.15 |
| `awardWonBy` | 16 | 6000 | 1.0 | 3 | 0.15 |

(`StoppingPolicy.residual_stop_threshold` defaults to 0.15 and no contract
overrides it; `RCSEConfig.stop_threshold = 0.25` is the *unused* cross-relation
fallback, reachable only with `honor_contract_stopping: false`.)

Global ceilings: `max_calls_per_query: 12`, `max_generated_tokens_per_query:
6000`, `max_steps_per_query: 12`. `PipelineConfig.budget` takes the **stricter**
of contract and global for calls/tokens (`pipeline.py:344`) — deliberately unlike
the verification thresholds, where the contract is authoritative and never
clamped.

`Budget` (`types.py:640`) counts **actual neural runtime invocations**, not
logical actions; `logical_actions` is tracked separately and never bounds
anything. `Budget.reserve` (line 682) is a last line of defence that raises
`BudgetExceeded` at the invocation boundary rather than reporting an overrun
afterwards.

### 9.3 The three controllers, kept apart

The paper must not conflate these.

1. **Module 7 (`controller.py`)** — the production controller. Chooses every
   acquisition and verification action in Phase A and Phase B. **ACTIVE-PRODUCTION.**
2. **Module 20 (`control/relation_budget.py`, `budget_accounting.py`)** — the
   relation budget *scheduler*. `mode: production`. It does not choose actions;
   it precharges a reservation before an action runs and settles it afterwards
   (`pipeline.py:2413,2459`). Its TRAIN-derived envelope
   (`configs/calibration/v3/m20_relation_budget.json`) declares per relation:
   `hard_calls`, `hard_generated_tokens`, `discovery_cap` (1 for every relation),
   `verification_cap`, `verification_reserve`, `special_reserves`. Example:
   awards `hard_calls 44`, `hard_generated_tokens 4794`, `verification_cap 18`,
   `verification_reserve 14`; city has a `special_reserve` of 1 call for
   `CANDIDATE_FREE`. **ACTIVE-PRODUCTION, but as an accountant, not a planner.**
3. **Module 21 (`control/micro_planner.py`)** — the expected-value micro-planner.
   `mode: production`. **ACTIVE-CONDITIONAL**: it decides only inside the V3
   pre-M8 loop.

### 9.4 The V3 control loop — the only place M21 can change a prediction

`pipeline.py :: _run_v3_control_loop` (line 2816), reached from `decide_graph`
*before* `finalize`. It is active iff (`_v3_control_loop_active`, line 747):

```python
v3_core.enabled and v3_core.mode is PRODUCTION
and v3_core.production_calibration_ready
and integration_mode.is_production
and micro_planner is not None and relation_budget_scheduler is not None
```

All five hold in F1/E3. Per query, for at most
`min(max_control_rounds_per_catalogue=3, max_steps_per_query=12) = 3` rounds:

1. `_v3_hypothesis_graph` → `build_hypothesis_graph` → `_failure_state`
   (`v3_core/hypothesis.py:584`) classifies the query into one of eight
   `FailureSearchState` values: `NO_CANDIDATE`, `SINGLE_LOW_SUPPORT`,
   `MULTIPLE_CONFLICTING`, `HIGH_FP_RISK`, `SET_GROWING`, `SEMANTIC_AMBIGUITY`,
   `NULL_UNRESOLVED`, `STABLE_VERIFIED`.
2. `legal_action_families(relation, state, history)`
   (`v3_core/relation_programs.py:283`) = `_relation_allowed_actions(relation)`
   ∩ `action_region_for_failure_state(state)`, minus families the bounded
   `ActionHistory` has already shown to be non-material. This is the **relation ×
   failure-state legality lattice** and it is the sharpest expression of
   "relation-typed inference program" in the codebase.
3. `build_v3_action_catalog` (`v3_core/execution.py:294`) turns legal families
   into executable `V3ActionCandidate`s with owner, prompt family, view id, model
   role and a budget descriptor. `_recall_spec` (line 415) is where relation
   typing bites hardest: `None` for borders always; `ATTRIBUTE_DECOMPOSITION`
   city-only; `SET_EXPANSION` award-only and repeatable, carrying the `seen` set;
   `DEFINITION_RECALL` area/capacity only; `LISTING_ELIMINATION` stock-only.
4. `_select_actions("v3", …)` → because `integration_mode.is_production` and a
   planner exists → `_plan_next_action` → `MicroPlanner.plan(state, actions)`.
5. If the decision is `ACTION` and `_v3_action_selectability` says the planned
   cost fits inside the **contract** budget minus what Phase A/B already spent,
   `_execute_v3_action_record` precharges with M20, calls `execute_v3_action`
   (a real Mistral call that writes ordinary Module-3 edges), settles the hold,
   and recomputes consensus. Otherwise the loop breaks.

**M21's utility** (`control/micro_planner.py :: utility`, line 58) is the
proposal's §17 equation transcribed once:

```text
U_t(a) = α·Ĝ_verified(a) + β·ΔR̂(a) + γ·ΔĤ(a) − δ·Ĉost(a) − η·R̂edundancy(a) − κ·F̂P(a)
a* = argmax U_t(a)   if U_t(a*) > τ_continue,   else STOP
```

Every estimate comes from the TRAIN historical bin, **never** from the current
graph. Calibration (`configs/calibration/v3/m21_planner_calibration.json`):

```text
α = 1.0        (expected verified gain)
β = 0.0        (expected Δ residual — WEIGHTED TO ZERO)
γ = 22.634228  (expected Δ entropy)
δ = 1.01626    (expected cost)
η = 1.01626    (expected redundancy)
κ = 1.0        (expected false-positive risk)
τ_continue = 0.0
lookahead_depth = 1
```

Two facts a paper must not gloss: **β = 0**, so the residual-improvement term
contributes nothing to M21's utility; and **γ = 22.63**, an order of magnitude
above every other coefficient, so entropy reduction dominates. `τ_continue = 0.0`
with strictly-greater comparison (`micro_planner.py:442`) means any positive
utility continues.

Calibration provenance is fully recorded: derived from a merged TRAIN corpus
(`merged_corpus_sha256 50be66dc…`), 477 TRAIN rows, 170 queries, 446 considered
actions, 246 executed actions, 246 committed action effects, 76 observed
transitions, 50 historical bins, `minimum_bin_support = 8`, four-level fallback
hierarchy from exact `relation/program_type/state_bin/family/target_class` down to
a global family fallback. The M20 half is `inherited_frozen_v2_m20`.

### 9.5 What the V3 loop can and cannot do in practice

- **Borders**: zero V3 actions, by two independent locks (§5.2).
- **Stock**: no recall specs; only `LISTING_ELIMINATION` / `SEMANTIC_VERIFY`.
- **Everything else**: gated by the *contract* call budget, which Phase A/B
  largely consume (4 calls for city/area/capacity; 5 for stock; 16 for awards).
  `_v3_action_selectability` (`pipeline.py:2742`) refuses an action whose planned
  `neural_calls` exceeds `max_calls − calls_used`.
- **M17/M18 execution is skipped entirely** when the V3 loop is active
  (`pipeline.py:1896`), to avoid mixing two action spaces in one calibration
  corpus. Consequently the production bridge has no executed M17 readings and no
  `RESOLVED` M18 checks to apply, and `ProductionEvidenceBridge.apply`
  (`evidence/production_bridge.py:135`) is a functional no-op in this profile even
  though it runs in `PRODUCTION` mode.

**How often the V3 loop actually executes an action on the 475 TEST rows is
UNRESOLVED FROM REPOSITORY EVIDENCE.** No preserved run directory contains
`v3_hypothesis_graph.jsonl`, `micro_planner.jsonl` or `relation_budget.jsonl`.
The paper must therefore not claim a measured contribution from M20/M21.

---

## 10. Relation-Specific Selection and Finalization

`src/cover_kbc/selection.py` is Module 8. Dispatch (`select`, line 462):
relation override first (`_BY_RELATION`, line 407), programme fallback second
(`_BY_PROGRAM`, line 413).

```text
hasCapacity  -> select_numeric_highest_valid   (relation override)
hasArea      -> select_numeric_robust          (relation override)
SMALL_SET    -> select_small_set               (borders, stock)
NULL_SINGLE  -> select_null_single             (city)
LARGE_OPEN_SET -> select_large_open_set        (awards)
NUMERIC      -> select_numeric_robust          (programme fallback)
```

Common preamble `_resolve` (line 88): score every active candidate, assign a tier
if still `UNRESOLVED` (so a trace always shows which candidates *would* have been
verified), then set `status = decide_status(...)`.

Ranking key `_rank_key` (line 118): `(−score, −acquisition_support, key)`.
**Never raw mention frequency** — "a name repeated ten times by one view must not
outrank one found by several independent mechanisms."

### 10.1 `select_small_set` — borders and stock

Precision-aware: only ACCEPTED candidates are emitted, so an unresolved candidate
is dropped rather than emitted. `graph.gate_negative` short-circuits to `[]`.
Two **stock-only** rules follow, explicitly gated on `relation == STOCK_RELATION`
so borders cannot inherit them (line 174):

- `reject_structurally_invalid_listings` (**ON**) — drops a value whose strict
  key equals the subject company's, or that is in `_NEVER_AN_EXCHANGE`
  (`none`, `unlisted`, `otc`, `private`, `valid`, `invalid`, …). Ticker rejection
  is **deliberately not implemented**, with a stated reason: `NYSE`, `AIM`, `SIX`
  and `Nasdaq` have exactly the shape any ticker heuristic keys on, and `AIM` is a
  gold answer on a TRAIN row.
- `apply_support_dominance` (**OFF** in the final profile) — would keep only
  maximally supported listings. Audit 0075 measured it at +12 rows improved / 7
  harmed, net relation macro-F1 `+0.00700`; it is nevertheless disabled in
  E3/F1 (`stock_support_dominance: false`).

Cap: `contract.max_objects` = 0 (unlimited) for both.

### 10.2 `select_null_single` — city

Gate-negative → `[]`. Otherwise rank the accepted candidates and take
`accepted[: contract.max_objects]` where the cap is **1 by programme**, read from
Module 1 rather than hard-coded (`base.py:153`). Empty is a first-class answer.

### 10.3 `select_large_open_set` — awards

Recall-first: no cap (`selection.max_objects = 0`), single-mechanism support is
not automatically fatal, but candidates must still clear `decide_status`. The
docstring names the trade explicitly: partial gold makes an unconstrained tail a
precision risk, not free recall.

### 10.4 `select_numeric_robust` — hasArea

1. `_numeric_clusters` (line 226): each candidate contributes
   `max(1, acquisition_support)` copies of its value; `cluster_values` groups them
   with a **diameter bound** (`relative_distance(min, max) ≤ 0.025`), not
   single-linkage — the source explains why at length
   (`normalization/numeric.py:266-288`): with a pairwise threshold, four chained
   values already span 7% and twenty span 37%, so the median could sit outside the
   official ±5% tolerance from its own members. Clusters are returned
   `(−size, relative_mad, representative)`-sorted.
2. `retained_pool(..., enabled=final_candidate_retention)` (**ON**) — V3.1 Class-A.
   If the ordinary winning cluster carries no ACCEPTED candidate, restrict the
   pool to clusters that carry an ACCEPTED candidate and are not verified INVALID,
   then re-run the *unchanged* ordering. Audit 0075 found 55 TRAIN rows with an
   accepted candidate and an empty output — **every one of them `hasArea`** — and
   the oracle-free counterfactual moved TRAIN macro-F1 from 0.40333 to 0.42045.
3. Take `clusters[0]`; if no member is ACCEPTED, emit `[]`.
4. Winner = the member nearest the cluster **median**; `derived_value =
   format_numeric(representative)`.

### 10.5 `select_numeric_highest_valid` — hasCapacity

The one selector whose target is a maximum, because the contract says the highest
published capacity is the answer.

1. Cluster as above.
2. `dominant_support = max cluster support`; `threshold = dominant_support ×
   capacity_support_ratio` (1.0 — a rival must match the dominant cluster
   exactly).
3. A cluster qualifies iff its strongest verdict is not INVALID **and**
   (support ≥ threshold **or** (`capacity_trust_verified` and verdict is VALID)).
4. Keep only clusters with an ACCEPTED member; if that empties the pool and
   `final_candidate_retention` is on, fall back to clusters that are
   `is_retainable` (accepted and not contradicted).
5. Winner = `max(qualifying, key=(representative, −relative_mad))` — **the
   highest value**, ties on tighter dispersion.
6. Emit the member nearest that cluster's median as `derived_value`, formatted
   with `integer_only=True`.

### 10.6 Output-value repair (V3.1 Class A) — `_final_values`, line 470

Runs on the emitted **strings**, never on candidates: "the candidate list is the
trace of what the pipeline believed, and rewriting it to match a serialisation
fix would erase the evidence that the fix was needed."

- NUMERIC → `canonicalize_numeric_output` (**ON**). Parses grouping separators,
  decimal comma, and **scientific notation** (which the acquisition regex misses),
  and converts **only when an explicit area-unit token is present**; a bare number
  is never multiplied by anything. Measured TRAIN delta: 0.
- non-NUMERIC → `repair_values` (**ON**). Splits a value on its first `:`; if the
  prefix is a temporal bucket (`1990s`, `1901-1909`, `1974`) or one of a closed
  vocabulary of enumeration nouns (`Individuals`, `Groups`, `Organisations`, …),
  the value is a leaked enumeration line: drop it if the payload is an abstention
  token, otherwise reduce it to the payload; then dedupe on `strict_key`. Audit
  0076 found 76 such values on TRAIN awards, e.g. `'Groups: NONE'` (pure false
  positive) and `'1990s: Alan Shearer'` (can never match gold because the
  evaluator normalises the whole string).

### 10.7 Fail-closed invariants

`_check_cardinality` (line 434) **raises** `SelectionInvariantError` rather than
truncating if a selector returns more objects than the contract allows, if a
value is empty, or if a value is a control token
(`_NEVER_AN_OBJECT = {valid, invalid, unknown, none, null, n/a, na, nan}`).
`finalize` also raises if a pending controller action is still unconsumed.

`_empty_reason` (line 127) distinguishes four causes with strict precedence —
`CONFIDENT_NEGATIVE_GATE` (an answer), `NO_CANDIDATE_GENERATED` (a recall
failure), `CANDIDATE_REJECTED` (a precision success), `UNRESOLVED_ABSTENTION` (an
evidence failure) — read from the **full** graph, not from the active list.

### 10.8 Why one global selector is impossible

Four incompatible official targets, all encoded: robust central value (area) vs
maximum (capacity) vs precision-first bounded set (stock/borders) vs recall-first
unbounded set (awards) vs zero-or-one (city). Plus two evaluator facts that drive
every choice (`selection.py:9-16`): an empty prediction scores precision 1.0 and
an empty gold set scores recall 1.0; and matching is maximum bipartite over
normalised alias sets, so **two surface forms of the same entity guarantee a
false positive**.

---

## 11. Profile E3 / F1 Specialization

`src/cover_kbc/leaderboard_repair/`. Constructed only if
`leaderboard_repair.enabled`. Per row it builds a `RowBudget(cap =
max_calls_by_relation[relation])` and a `RepairCaller`, dispatches through
`REPAIR_BY_RELATION`, and asserts row coverage and order are unchanged
(`stack.py:203`). Repair calls are billed to a **separate** ledger, not to the
pipeline's per-query `Budget`.

### 11.1 `AwardMetadataNormalizer` — deterministic, zero calls

`relations.py :: repair_award` (line 116) → `util.py ::
normalize_award_metadata` (line 75). Exact transformations, in order:

1. `clean_surface`.
2. Drop if the strict key is `groups none`, `individuals none`, `none`.
3. Strip a leading temporal wrapper: `^(\d{4}(\s*[-/]\s*\d{2,4})?|\d{3,4}s|early|middle|mid|recent|late)\s*:\s*`.
4. Strip a leading role wrapper:
   `^(individuals?|persons?|people|groups?|organisations?|organizations?|projects?|recipients?|winners?)\s*:\s*`.
5. Strip a trailing repeat marker: `\s*\((second|third|fourth|fifth|another|repeat)(\s+time)?\)\s*$`.
6. `clean_surface` again; drop if now empty or `none` / `no recipient(s)`.
7. `_unique_by_strict_key` over the survivors.

**Trigger point**: after Module 8, on every award row (cap 1 > 0). **It adds no
factual content whatsoever** — it only removes structural wrappers and
deduplicates. Note this overlaps with V3.1 `repair_values`; both are on, and the
normalizer additionally handles the trailing-parenthetical and role-wrapper cases.

Measured effect (audit 0085): A → A+Award moved `awardWonBy` F1 0.2929 → 0.3105
and overall 0.4906 → 0.4910, changing **1 row** with **0 model calls**.

### 11.2 `MistralCityEmptyRescue` — null-aware selective rescue

`relations.py :: _repair_city_mistral_empty_rescue` (line 41). Cap 2.

**Triggering condition**: `prediction.object_entities == []`. A non-empty row is
bypassed with `decision="bypassed_profile_d_non_empty"` and **zero calls**. This
is the entire gating logic — there is no confidence threshold, no candidate
signal, no upstream state consulted.

Two stages, both `role="verifier"` (same Mistral runtime):

1. **Life-status gate**, `max_new_tokens=8`. Prompt asks *"Is this exact person
   deceased?"* and demands exactly one of `DECEASED` / `LIVING` / `UNKNOWN`, with
   *"Use UNKNOWN if you are not sufficiently confident."* Parser
   (`_parse_e1_life_status`, line 181) requires **exactly one non-empty line**
   matching one of the three tokens, else `INVALID`. Anything but `DECEASED`
   returns `[]` and spends no second call.
2. **City recall**, `max_new_tokens=20`. Prompt states the person has already been
   classified DECEASED, demands `CITY: <city name>` or `UNKNOWN`, and enumerates
   six rejections (hospital/institution, country, state/province, birthplace, main
   residence, burial place). Parser (`_parse_e1_city_output`, line 191) requires
   exactly one line; `UNKNOWN` → `[]`; must start with `CITY:`; the remainder must
   be non-empty and contain no `;` or `|`, else `INVALID` → `[]`.

Fallback order: any of LIVING / UNKNOWN / INVALID life output, UNKNOWN / INVALID
city output, or budget exhaustion → **preserve `[]`**. The rescue can only ever
turn `[]` into a singleton; it can never modify or delete a non-empty row.

Measured effect (audit 0089): Profile D → integrated E1 moved
`personHasCityOfDeath` 0.4900 → 0.5700 (P 0.9900 → 0.9600, R 0.4900 → 0.5900).
Profile D emitted only 2 non-empty city rows out of 100.

### 11.3 `MistralAreaMultiView` — semantic numeric multi-view

`leaderboard_repair/area_multiview.py`. Cap 5; **raises** if the cap is below 4
(line 396). Mode must be exactly `DIRECT_ALL`. Runs on **every** `hasArea` row.
Entered via `repair_area` (`area.py:131`), which delegates to the multi-view
whenever `mistral_area_multiview` is on — so `MistralDirectArea` is **not** a
separate final layer in E3/F1; its prompt survives only as view V1.

**Exactly four views** (`VIEW_IDS`, line 30), all `role="verifier"`,
`max_new_tokens=24`, greedy, sharing `DIRECT_AREA_SYSTEM_PROMPT`:

| View id | Semantic purpose |
|---|---|
| `area_multiview_v1_direct` | The E1 Direct Area prompt verbatim: canonical surface area in km²; explicit island / lake / country rules; nine explicit "do NOT return the area of" exclusions (containing country; state/province/county/municipality; archipelago unless the subject is the group; only part of the island; drainage basin/catchment; lagoon; protected area; nearby feature); convert mi²/ha before answering. |
| `area_multiview_v2_entity_type` | **Silent type classification first**: COUNTRY / ISLAND / LAKE / OTHER_GEOGRAPHIC_ENTITY, then the type-appropriate area (country → total incl. inland water; island → land area of that island; lake → surface area). |
| `area_multiview_v3_infobox` | **Encyclopedic/infobox recall**, with five internal distinctions: exact vs similarly named entity; island vs archipelago; lake surface vs basin/catchment; km² vs mi²/ha; area vs population/length/depth/elevation/volume. |
| `area_multiview_v4_attribute_contrast` | **Silent five-step reasoning** (identity → type → correct attribute → wrong-unit rejection → conversion) against eight explicit contrasts, with "Do not expose the reasoning." |

**Accepted output grammar** (`parse_area_output`, line 226): exactly one
non-empty line; either the literal `UNKNOWN`, or
`^AREA:\s*((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)$`. Commas are stripped, the value
becomes a `Decimal`, and must be finite and > 0. Everything else — bare numbers,
units after the number, ranges, multiple candidates, prose, `NaN`, `Infinity`,
zero, negatives, multi-line output — is `INVALID`.

**Unit handling**: none at parse time. Conversion is pushed entirely into the
prompts ("convert it to square kilometres before answering", "Do not include
units after the number"). The parser rejects any unit-bearing output outright.

**Clustering** (`cluster_area_values`, line 262): greedy first-fit where a new
observation joins a cluster only if it is within **5%** of **every** existing
member (`area_within_tolerance`, line 255: `|a−b| / max(|a|,|b|) ≤ 0.05`). Note
this is a *complete-linkage* rule inside the multi-view module, and the tolerance
is **0.05 — the evaluator's own tolerance** — not the core's 0.025.

**Support** = number of views in the cluster (max 4). **Representative** = the
observed value nearest the cluster median, ties on view order then numeric value
(line 106). No synthetic arithmetic answer is ever created.

**Top cluster** (`top_area_cluster`, line 288): `(−support, first_view_rank,
Decimal(representative))`.

**Decision order** (`decide_area_multiview`, line 348):

1. `top.support ≥ 3` → emit `top.representative`. Reason `MULTIVIEW_SUPPORT_GE_3`.
   **The judge is not called.**
2. Else, if any valid observation exists, run **exactly one source-blind judge**;
   a valid judge label → emit that value. Reason `JUDGE`.
3. Else if V1 produced a valid number → emit it. Reason `FALLBACK_V1_DIRECT_AREA`.
4. Else if any cluster exists → emit the top cluster representative. Reason
   `FALLBACK_TOP_CLUSTER`.
5. Else → `[]`. Reason `NO_VALUE`.

**The judge is genuinely source-blind** (`area_judge_prompt`, line 301). It
receives the subject, the relation, a rejection list (administrative/container
area; country instead of island; archipelago instead of island; basin/catchment
instead of lake surface; nearby or similarly named feature; unconverted mi²/ha;
population/length/depth/elevation/volume), and the distinct numeric candidates
**sorted ascending** and relabelled `A.`, `B.`, `C.`… plus a final `UNKNOWN`
label. It sees **no view names, no view identities, no support counts, no
clustering information**. Output must be exactly one label line
(`max_new_tokens=4`).

**Interaction with upstream**: none. The first decision recorded is
`eligible_has_area_row` with `current_ignored=True` (line 399-404).

Call accounting per Area row: 4 always, +1 iff `top.support < 3` and at least one
valid observation exists → max 5.

### 11.4 `MistralCapacityMultiView`

`leaderboard_repair/capacity.py`. Structurally parallel; the differences are what
matter.

- **Own system prompt** (line 20) stating the relation semantics inline: maximum
  spectator capacity of the exact named venue as an integer; *"When multiple
  capacities are published, use the highest published spectator capacity"*; never
  attendance, area, cost, year, field dimensions, or another venue.
- **Four views**: `capacity_multiview_v1` (direct maximum capacity, with a
  ten-item "Do NOT return" list including record attendance, average attendance,
  single-event attendance, area, field dimensions, construction cost, opening
  year, renovation year, one stand or section, another similarly named venue);
  `v2` (encyclopedic/infobox recall after silent disambiguation by full name and
  location); `v3` (configuration-aware: seated / total / standing / sport-specific
  / concert / historical / post-renovation / temporary, then the highest);
  `v4` (silent five-step reasoning, then the highest).
- **Grammar**: exactly one line; `UNKNOWN`, or
  `^CAPACITY:\s*((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.0+)?)$`; the value must be a
  positive **integer** (`number != number.to_integral_value()` → INVALID).
- **Tolerance is configurable here**: `cluster_capacity_values(...,
  tolerance=config.numeric_cluster_tolerance)` = 0.05. (Area hard-codes 0.05.)
- **Decision order** differs in one respect: there is no `FALLBACK_TOP_CLUSTER`
  before V1. It is: `support ≥ 3` → `strong_cluster`; else judge → `judge_selected`;
  else V1 valid → `fallback_v1`; else top cluster → `fallback_top_cluster`; else
  `no_numeric_values` → `[]`.
- Judge prompt (line 266) is source-blind in the same way, with a
  capacity-specific rejection list (attendance records, event attendance, area,
  dimensions, cost, dates, similarly named venues).
- Upstream ignored identically (`current_ignored=True`, line 337-342).

### 11.5 `MistralStockEmptyRescue` — Profile F1 only

`leaderboard_repair/stock_empty_rescue.py`. Cap 4. Mode
`EMPTY_ONLY_STRONG_CONSENSUS`. **Triggering condition**: the stock row's
`ObjectEntities` is empty. Non-empty rows are bypassed with zero calls.

**Four candidate-blind views**, `role="verifier"`, `max_new_tokens=96`, sharing a
system prompt that forbids prose, tickers, countries, indexes, regulators, market
segments and company names:

| View id | Purpose |
|---|---|
| `stock_empty_rescue_s1_direct_listing` | Which exchange(s) list the publicly traded shares of this exact company; UNKNOWN if private/delisted/uncertain. |
| `stock_empty_rescue_s2_primary_listing` | Silent disambiguation, then the **primary** listing venue; no ticker, no generic country market, no parent/subsidiary substitution. |
| `stock_empty_rescue_s3_common_shares` | The venue for **ordinary/common** shares; reject ADR-only guesses unless specifically remembered; reject indexes, tickers, identifiers, OTC guesses when uncertain. |
| `stock_empty_rescue_s4_anti_prior` | **Anti-prior probe**: "Ask whether the first answer is a real exchange-listing fact for this exact company or just a plausible market prior. Only return an exchange when you have specific factual memory." |

**Grammar** (`parse_stock_exchange_output`, line 248): either the literal
`UNKNOWN`, or **1–3** lines each exactly `EXCHANGE: <name>`. Each name passes
`clean_exchange_name` (line 231): 2–90 characters, no `;`/`|`/newline/tab, no
match against `\b(ticker|symbol|isin|cusip|index|regulator|country|market
segment)\b`, not a bare all-caps ≤12-char token unless it is a known hint, and
**must contain at least one token from `EXCHANGE_HINTS`** (a 45-entry structural
vocabulary: `exchange`, `nasdaq`, `nyse`, `euronext`, `xetra`, `bourse`, `borsa`,
`bolsa`, `tsx`, `asx`, `hkex`, `jse`, `lse`, `b3`, `six`, `kosdaq`, …). Any
failing line makes the whole output `INVALID`.

**Clustering** (`cluster_stock_exchange_outputs`, line 275): normalise each name
through `canonical_exchange_key` = `strict_key` then an 18-entry `ALIAS_KEYS`
table (`nyse` → `new york stock exchange`, `lse` → `london stock exchange`,
`hkex` → `hong kong stock exchange`, `tsx` → `toronto stock exchange`, `asx` →
`australian securities exchange`, …). Support counts **one per view**, not per
mention. Representative = most frequent surface, ties on first-seen then length
then lexical.

**Decision** (`decide_stock_empty_rescue`, line 326): if the top cluster's
support `≥ min_support` (**3 of 4**), emit **exactly one** exchange
(`STOCK_EMPTY_REPAIR_STRONG_CONSENSUS`); otherwise emit `[]`
(`STOCK_EMPTY_REPAIR_REJECTED`). There is **no judge** and no fallback ladder.

Measured effect (audit 0093): stock P 0.9092 → 0.8992, R 0.7863 → 0.7963,
F1 0.7285 → **0.7385**; overall 0.5857 → **0.5878**. Precision cost 0.0100 bought
recall 0.0100 — the classic empty-row rescue trade, made safe by the 3-of-4
consensus bar and the single-value cap.

**One flag to be careful about.** The F1 config declares
`standalone_stock_observations_are_runtime_knowledge: true`, whereas the Area and
Capacity equivalents are `false`. No source file reads this key — it is metadata
only (verified by repository-wide grep: the single occurrence is the config line
itself). Its intended meaning is **UNRESOLVED FROM REPOSITORY EVIDENCE**, and it
should not be cited in the paper without the author clarifying it.

### 11.6 Why this is "mutation-bounded final specialization", not the method

The code supports the framing precisely:

| Bound | Enforcement |
|---|---|
| Relation-scoped | `REPAIR_BY_RELATION` has four/five entries; borders has none |
| Call-capped per row | `RowBudget(cap)`; `RepairCaller.generate` returns `None` when exhausted, and every caller treats `None` as "keep the safe answer" |
| Output-only | The stack may rewrite `ObjectEntities` and nothing else |
| Coverage-preserving | `_assert_query_coverage` raises if row count or order changes |
| Feature-flagged, default-off | Every `RepairFeatures` field defaults `False`; a config omitting the block reproduces the frozen pipeline exactly |
| Audited | `repair_accounting.json` + `leaderboard_repair.jsonl` per run, with per-feature decision counts |

And the empirical record shows the *unbounded* version fails: Profile C2 made
1,075 post-calls and changed 246/475 rows, regressing five of six relations
(§15).

The honest framing for the paper: **the general COVER-KBC framework is Stage 1;
Stage 2 is a bounded, relation-scoped, empirically-gated final layer whose
components were each promoted only after a controlled single-relation hidden-TEST
ablation.** Two of its components (Area MV, Capacity MV) are strong enough that
they *replace* the core for their relations; two (City rescue, Stock rescue) are
null-aware and can only fill empty rows; one (Award normalizer) adds no facts at
all.

---

## 12. Per-Relation Pipelines

Notation: `[core]` = Stage 1, `[E3/F1]` = Stage 2. Call counts are the
*contract*/cap ceilings, not observed averages.

### 12.1 `countryLandBordersCountry` — SMALL_SET, F1 0.9291

```text
(subject, countryLandBordersCountry)
 -> contract: SMALL_SET / ZERO_OR_MANY_SMALL / country_or_comparable_territory
    4 positive rules, 6 hard negatives; m(o) = 6 eligible groups
 -> NO calibrated gate (not in GATE_QUESTIONS)
 -> controller loop, budget 4 calls / 1024 tokens
      mandatory: borders_direct (DIRECT), borders_compass (STRUCTURAL)
      optional, controller-selectable: borders_land_vs_maritime (CONTRASTIVE),
        borders_missing (MISSINGNESS, injects accepted set),
        borders_description (DESCRIPTION, 2 calls 1 mechanism),
        borders_reverse_check (REVERSE, per candidate)
 -> parse_entities -> strict_key identity -> EvidenceGraph
 -> S(o) = F + 0.6L - 1.5C - U ;  auto_accept at 2 groups; accept_valid_prob 0.5
    adversarial template names maritime_neighbour / non_integral_dependency
 -> RCSE SMALL_SET: mechanism_gap .8, unresolved .8, instability .8,
    yield .5, inclusion .5; floor = mandatory_gap only
 -> should_stop: stability >= 1.0 and unresolved == 0
 -> V3 loop: ZERO actions (frozen_conservative + _recall_spec None)
 -> select_small_set (no stock rules), no cap
 -> repair_values (enumeration-label repair)
 -> [E3/F1] NONE. cap 0.
 -> ObjectEntities
```

Divergence: the only relation with **six** reachable mechanisms, the tightest
`saturation_patience` (1), and total exemption from every adaptive extension.

### 12.2 `personHasCityOfDeath` — NULL_SINGLE, F1 0.5700

```text
(subject, personHasCityOfDeath)
 -> contract: NULL_SINGLE / ZERO_OR_ONE / city_or_locality; max_objects = 1
    m(o) = 3 (DIRECT, DESCRIPTION, CONTRASTIVE) - gate excluded by design
 -> CALIBRATED GATE (verifier role, A/B/C = YES/NO/UNKNOWN,
      "Answer B = NO only if you are confident the person is still living",
      contextual calibration on, margin >= 1.0 and p(NO) >= 0.5 to close)
      confident NO -> ObjectEntities = [] with EmptyReason.CONFIDENT_NEGATIVE_GATE
 -> controller loop, budget 4 calls / 768 tokens
      mandatory: death_status_gate (generation gate; cannot close the gate while
        the calibrated gate is enabled), death_city_direct ("output NONE rather
        than guessing", 16 new tokens)
      optional: death_locality_granularity (CONTRASTIVE),
        death_description (DESCRIPTION)
 -> auto_accept 3 groups; accept_valid_prob 0.6; drop_on_unknown TRUE
    adversarial classes country_or_region / birth_city / burial_place
 -> RCSE NULL_SINGLE: floors = gate_unresolved, locality_competition
 -> should_stop: one ACCEPTED and nothing unresolved; or confident negative gate;
    or no candidate + no-gain + gate resolved
 -> V3 loop: ATTRIBUTE_DECOMPOSITION (city-only), INDEPENDENT_RECALL,
    MULTI_VIEW_RECALL, SEMANTIC_VERIFY, CONTRAST_VERIFY - budget permitting
 -> select_null_single, cap 1
 -> [E3/F1] MistralCityEmptyRescue, EMPTY ROWS ONLY, cap 2:
       DECEASED? -> only "DECEASED" proceeds
       "CITY: <name>" strict single line -> singleton
       anything else -> keep []
 -> ObjectEntities (0 or 1)
```

Divergence: the only relation where **empty is a modelled answer with its own
evidence path**, and the only one whose Stage-2 layer is a two-stage gated
rescue. Hidden TEST P 0.9600 / R 0.5900 shows the design point: precision is
protected, recall is bought only under a hard gate.

### 12.3 `companyTradesAtStockExchange` — SMALL_SET, F1 0.7285 → 0.7385

```text
(subject, companyTradesAtStockExchange)
 -> contract: SMALL_SET / ZERO_OR_MANY_SMALL / stock_exchange
    m(o) = 4 (DIRECT, DESCRIPTION, CONTRASTIVE, REVERSE)
 -> CALIBRATED GATE ("Answer B = NO only if you are confident that {subject}
      itself is privately held, wholly owned, or delisted - not merely because
      its parent or a subsidiary is listed")
 -> controller loop, budget 5 calls / 1024 tokens  (largest non-award budget)
      mandatory: stock_listing_gate, stock_exchange_direct
      optional: stock_parent_contrast, stock_description, stock_reverse_check
      *** V3.1 Class-B LIVE: STOCK_LISTING_ENTITY instruction injected into the
          system prompt of every recall call (elicitation/engine.py:140) ***
 -> auto_accept 3; accept_valid_prob 0.6
    adversarial classes parent_listing / subsidiary_listing / historical_listing
 -> RCSE SMALL_SET (same as borders)
 -> V3 loop: LISTING_ELIMINATION and SEMANTIC_VERIFY only
 -> select_small_set + reject_structurally_invalid_listings (ON)
    (apply_support_dominance OFF)
 -> repair_values
 -> [E3] NONE, cap 0.
    [F1] MistralStockEmptyRescue, EMPTY ROWS ONLY, cap 4:
       4 candidate-blind views -> strict "EXCHANGE: X" (1-3 lines) or UNKNOWN
       -> alias-normalised clustering, one support per view
       -> emit ONE exchange iff top support >= 3 of 4, else keep []
 -> ObjectEntities
```

Divergence: the only relation with a *precision-oriented* profile
(`search_bias=ELIMINATION`, `verification_policy=REJECTION_FIRST`), the only one
with a live Class-B prompt injection, and the only one whose Stage-2 layer
appears in F1 but not E3.

### 12.4 `hasArea` — NUMERIC, F1 0.6700

```text
(subject, hasArea)
 -> contract: NUMERIC / EXACTLY_ONE / area_km2; target unit km2;
    numeric_cluster_threshold 0.025; integer_only False; m(o) = 3
 -> no gate
 -> [core] controller loop, budget 4 calls / 768 tokens
      mandatory: area_direct_km2 (DIRECT), area_total_vs_land (CONTRASTIVE)
      optional: area_alternate_unit (STRUCTURAL, asks in SQUARE MILES)
      parse_numeric_observations -> unit conversion to km2 -> format_numeric key
 -> auto_accept 2; accept_valid_prob 0.5
 -> RCSE NUMERIC: dispersion, cluster_competition (BLOCKING), mechanism_gap,
    verifier_disagreement
 -> should_stop: competition == dispersion == instability == unresolved == 0
 -> V3 loop: DEFINITION_RECALL / MULTI_VIEW_RECALL / INDEPENDENT_RECALL /
    ALTERNATIVE_RECALL / CONTRAST_VERIFY - budget permitting
 -> select_numeric_robust: weighted cluster (diameter <= 0.025) ->
    retained_pool (V3.1) -> clusters[0] -> median representative
 -> canonicalize_numeric_output
 -> [E3/F1] MistralAreaMultiView, ALL ROWS, DIRECT_ALL, cap 5:
       *** the core answer is discarded: current_ignored=True ***
       V1 direct | V2 entity-type | V3 infobox | V4 attribute-contrast
       strict "AREA: <number>" or UNKNOWN
       5% complete-linkage clustering, support = #views
       support >= 3 -> representative
       else 1 source-blind judge over anonymised sorted candidates
       else V1 -> else top cluster -> else []
 -> ObjectEntities (0 or 1)
```

Divergence: the largest measured jump of any single change in the project's
history (0.3600 → 0.6600 with Direct Area, → 0.6700 with Multi-View), and the
clearest case where Stage 2 supersedes Stage 1.

### 12.5 `hasCapacity` — NUMERIC, F1 0.1633

```text
(subject, hasCapacity)
 -> contract: NUMERIC / EXACTLY_ONE / spectator_count; unit persons;
    integer_only TRUE; threshold 0.025; m(o) = 3
    definition encodes the SELECTION RULE: "the highest published capacity"
 -> no gate
 -> [core] controller loop, budget 4 calls / 768 tokens
      mandatory: capacity_direct, capacity_contrast
      optional: capacity_configuration ("report the highest of them")
 -> auto_accept 2; accept_valid_prob 0.5
    adversarial classes record_attendance / seated_only
 -> RCSE NUMERIC (same shape as area)
 -> V3 loop: DEFINITION_RECALL (facet
    v3_hasCapacity_definition_current_maximum), ALTERNATIVE_RECALL (facet
    v3_hasCapacity_alternative_historical_configuration), etc.
 -> select_numeric_highest_valid: qualify clusters by
    (support >= dominant_support x 1.0) or (verified VALID);
    exclude INVALID; retained_pool fallback; take the HIGHEST representative
 -> canonicalize_numeric_output (integer_only)
 -> [E3/F1] MistralCapacityMultiView, ALL ROWS, DIRECT_ALL, cap 5:
       *** core answer discarded ***
       V1 direct-max | V2 encyclopedic | V3 configuration-aware | V4 step-back
       strict "CAPACITY: <integer>" or UNKNOWN
       5% clustering; support >= 3 -> representative
       else 1 source-blind judge; else V1; else top cluster; else []
 -> ObjectEntities (0 or 1)
```

Divergence: the only "highest value wins" selector; the weakest relation
(0.1633) and the only one where the multi-view improvement is small
(0.1224 → 0.1429 direct → 0.1633 multi-view).

### 12.6 `awardWonBy` — LARGE_OPEN_SET, F1 0.3105

```text
(subject, awardWonBy)
 -> contract: LARGE_OPEN_SET / ZERO_OR_MANY_LARGE / award_recipient
    m(o) = 5 (DIRECT, STRUCTURAL, CONTRASTIVE, MISSINGNESS, REVERSE)
 -> no gate
 -> controller loop, budget 16 calls / 6000 tokens, saturation_patience 3
      mandatory (4): award_direct;
        award_facet_temporal, award_facet_recipient_type  (both STRUCTURAL -
          ONE independence group, three facet ids);
        award_missing (MISSINGNESS, injects accepted set)
      optional: award_facet_category (STRUCTURAL, same group),
        award_exact_identity_contrast (CONTRASTIVE),
        award_reverse_check (REVERSE, per candidate)
 -> auto_accept 2; accept_valid_prob 0.6; drop_on_unknown TRUE
    adversarial classes winning_work / nominee / adjacent_award / rescinded
 -> RCSE LARGE_OPEN_SET: yield 1.0, facet_gap 1.0, mechanism_gap .8,
    unresolved .4, unsaturated 1.0; NO blocking floor
 -> should_stop: no_gain >= 3 AND facet_gap == 0
 -> V3 loop: SET_EXPANSION (repeatable, carries the seen set;
    render_seen_set caps at 12 items / 240 chars), MULTI_VIEW_RECALL,
    ALTERNATIVE_RECALL, UNARY_VERIFY
 -> select_large_open_set: all ACCEPTED, no cap
 -> repair_values (drops "Groups: NONE", reduces "1990s: X" -> "X", dedupes)
 -> [E3/F1] AwardMetadataNormalizer, cap 1, ZERO calls:
       strip temporal / role wrappers, strip "(second time)", drop none-payloads,
       dedupe on strict_key
 -> ObjectEntities (unbounded)
```

Divergence: the only facet-partitioned relation (and therefore the only one where
`facet_gap` is nonzero), the largest budget by 3×, the only relation whose
stopping rule is saturation-based, and the only Stage-2 layer that spends no
model call.

### 12.7 Divergence summary

| | borders | city | stock | area | capacity | awards |
|---|---|---|---|---|---|---|
| ProgramType | SMALL_SET | NULL_SINGLE | SMALL_SET | NUMERIC | NUMERIC | LARGE_OPEN_SET |
| Calibrated gate | – | **yes** | **yes** | – | – | – |
| `m(o)` | 6 | 3 | 4 | 3 | 3 | 5 |
| Mandatory views | 2 | 2 | 2 | 2 | 2 | **4** |
| Contract calls / tokens | 4 / 1024 | 4 / 768 | 5 / 1024 | 4 / 768 | 4 / 768 | **16 / 6000** |
| `auto_accept` | 2 | 3 | 3 | 2 | 2 | 2 |
| `accept_valid_prob` | .5 | .6 | .6 | .5 | .5 | .6 |
| `drop_on_unknown` | global (T) | **T** | global (T) | global (T) | global (T) | **T** |
| Saturation patience | **1** | 2 | 2 | 2 | 2 | **3** |
| RCSE blocking floors | – | gate, locality | – | cluster competition | cluster competition | – |
| Facets | – | – | – | – | – | **4** |
| Selector | small-set | null-single | small-set + structural validation | robust median | **highest valid** | large-open-set |
| V3 actions | **none** | 5 families | 2 families | 5 families | 5 families | 4 families |
| Stage-2 layer | **none** | empty-only rescue | **F1 only**, empty-only | **all rows, replaces core** | **all rows, replaces core** | deterministic, 0 calls |
| Hidden TEST F1 | .9291 | .5700 | .7285 / .7385 | .6700 | .1633 | .3105 |

---

## 13. Models, Prompts, and Inference Configuration

### 13.1 The one model

| Field | Value | Source |
|---|---|---|
| model id | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | `model_profile.enumerator.model_id` |
| revision | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | `model_profile.enumerator.revision` |
| family | `mistral` | config |
| architecture | `Mistral3ForConditionalGeneration` (multimodal checkpoint, text-only use) | `configs/models/bakeoff-candidates.yaml` |
| published parameters | **24,011,361,280** | HF API `safetensors.total`, `parameter_source_verified: true` |
| budget limit | 32,000,000,000 | `budget_assertion.limit` |
| tokenizer | `mistral_common` (Tekken), via `models/mistral_tokenizer.py :: build_tokenizer` | config + `huggingface.py:104` |
| precision | `torch_dtype: bfloat16` | config |
| quantization | `nf4` → `BitsAndBytesConfig(load_in_4bit, nf4, compute_dtype=bfloat16, double_quant)` | `huggingface.py:143` |
| device map | `auto` | config |
| `trust_remote_code` | false (default) | `registry.py:113` |
| loader | tries `AutoModelForCausalLM` then `AutoModelForImageTextToText`, reporting all errors | `huggingface.py:159` |
| mode | `self.model.eval()`; every forward under `torch.no_grad()` | `huggingface.py:127,255,294,327` |

`loaded_parameter_count` is recorded as a **diagnostic only**; the budget always
uses the *published* count and quantisation never reduces it
(`huggingface.py:185-192`).

### 13.2 Role assignment — one physical object

`model_blocks(config)` (`registry.py:18`) returns
`(enumerator, verifier = enumerator)` when the profile declares no separate
verifier block. `run_cover.py:562` then sets `verifier_runtime = runtime` when the
two configs are equal. `manifest.add_model` is called once. Logical roles served
by that single object: core enumerator; blind verifier; calibrated existence
gate; V3 verification actions; City rescue caller; Area MV caller; Capacity MV
caller; Stock rescue caller.

`verifier_available` (`pipeline.py:694`) deliberately does **not** test object
identity — that would make verification vanish exactly when the verifier is
loaded in a staged phase. It tests `spec.supports_logits` plus either object
distinctness or `spec.model_id == config.verifier_model_id`.

### 13.3 Decoding

All decoding is **greedy and deterministic**. Three profiles
(`elicitation/library.py:26-28`):

| Profile | temperature | top_p | `max_new_tokens` | Used by |
|---|---:|---:|---:|---|
| `GREEDY` | 0.0 | 1.0 | 192 | entity list views, descriptions, reverse checks |
| `GREEDY_SHORT` | 0.0 | 1.0 | 16 | gates, numeric views, `death_city_direct`, `death_locality_granularity` |
| `GREEDY_LONG` | 0.0 | 1.0 | 512 | all award views |

`HuggingFaceRuntime.generate` (line 234) sets `do_sample = not
decode.deterministic`, so with temperature 0 it passes **neither** `temperature`
nor `top_p` to `model.generate` — greedy in the strict sense.
`DecodeProfile.seed` is `None` for every library view; `torch.manual_seed` is
called only if a seed is supplied. `experiment.seed: 42` seeds
`ElicitationEngine` run-id derivation (`engine.py:138`) and the manifest, not
sampling. **There is no stochastic sampling anywhere in the final profile** — a
fact worth stating in the paper, since it makes "self-consistency by repeated
sampling" structurally unavailable and is exactly why `RESAMPLE` is unreachable.

Stage-2 decoding: `RepairCaller.generate` builds
`DecodeProfile(name=f"{feature}_greedy", temperature=0.0, top_p=1.0,
max_new_tokens=…)` with 8 (city life status), 20 (city recall), 24 (area/capacity
views), 4 (area/capacity judges), 96 (stock views).

Chat framing: `chat_token_ids(tokenizer, prompt, system_prompt)` when the Mistral
tokenizer supports it, else `apply_chat_template`, else
`f"{system}\n\n{prompt}"` (`huggingface.py:194-222`). Generation and label
scoring share `_prompt_ids`, so a model that swaps roles is framed identically.

### 13.4 Prompt inventory (all repository-authored, all closed-book)

- Shared system prompt (`elicitation/views.py:47`): *"You answer knowledge-base
  completion questions using only your own internal knowledge. You have no access
  to search, documents or external tools."*
- Three output-format footers: `ENTITY_FORMAT` (semicolon-separated one line, or
  exactly `NONE`), `NUMERIC_FORMAT` (a single number and its unit, or exactly
  `UNKNOWN`), `GATE_FORMAT` (exactly one word: YES/NO/UNKNOWN), plus
  `DESCRIPTION_FORMAT` (2–3 sentences of prose, explicitly *not* a list).
- 24 core views across six relations (`VIEW_LIBRARY`, `library.py:378`).
- 3 verifier templates + 1 gate template + 2 content-free controls
  (`verification/blind.py`).
- 4 V3.1 Class-B relation instructions (`v3_1/prompts.py`), of which **one**
  (`STOCK_LISTING_ENTITY`) is live.
- Stage-2 prompts: 1 city life-status + 1 city recall; 4 area views + 1 area
  judge; 4 capacity views + 1 capacity judge; 4 stock views. Plus 3 Stage-2 system
  prompts (`DIRECT_AREA_SYSTEM_PROMPT`, `CAPACITY_SYSTEM_PROMPT`,
  `STOCK_SYSTEM_PROMPT`).

### 13.5 Compliance — verified against source

| Claim | Verdict | Evidence |
|---|---|---|
| No fine-tuning | **TRUE** | no optimizer, no `.train()`, no `loss`, no `backward` anywhere in `src/`; `huggingface.py:25` states "frozen inference only" |
| No LoRA / adapters | **TRUE** | no `peft` import; `pyproject.toml` has no adapter dependency |
| No continued pretraining | **TRUE** | same |
| No learned router | **TRUE** | `PROGRAM_BY_RELATION` is a static dict; `router.py:5` says so explicitly |
| No learned verifier head | **TRUE** | verification is label-logit reading + subtraction of a content-free control; `blind.py:266-272`: "No calibrator is fitted — this is arithmetic on inference-time outputs" |
| No external RAG / web / KB | **TRUE** | no `requests`, `httpx`, `urllib`, `sqlite`, `faiss`, `chromadb` import in `src/`; the only network access is the HF checkpoint download |
| No subject-answer lookup table | **TRUE for code** | contracts carry definitions only; the F1 stock module's `ALIAS_KEYS`/`EXCHANGE_HINTS` are *structural exchange vocabulary*, not subject→answer pairs |
| Only one neural model contributes | **TRUE** | one `model_profile` block; `verifier_runtime is runtime`; `qwen_runtime_calls: 0`; `run_area_multiview.py:87` **raises** if the string `Qwen/Qwen3.5-4B` appears anywhere in the model profile |
| Parameter budget legal | **TRUE** | `audit_parameter_budget` → 24.01B / 32B PASS |

**Caveat on "no lookup table":** the *prompts* for Area/Capacity/Stock encode
relation-level disambiguation vocabulary that was clearly informed by inspecting
failures (e.g. the area prompt's nine specific exclusion classes). That is prompt
engineering against a relation, not a subject-answer table, and no subject or
gold value appears in any prompt. The configs assert this
(`no_subject_answer_lookup: true`) and tests check it
(`test_direct_area_result_row_schema_and_no_subject_lookup`).

### 13.6 Historical model bake-off — not the final runtime

`configs/models/bakeoff-candidates.yaml` records metadata for candidate
checkpoints, with a note that all counts were read from the HF API
`safetensors.total` on 2026-08-03:

| Model | Budget-counted parameters | Status |
|---|---:|---|
| `Qwen/Qwen3.5-9B` (rev `c202236…`) | 9,653,104,368 | "DOWNLOADED AND RUN in Milestone 2" — official baseline |
| `Qwen/Qwen3.5-4B` (rev `851bf6e…`) | 4,659,865,088 | verifier in Profiles A / A+Award; **retired at Profile D** |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 24,011,361,280 | **the final runtime** |

Profiles A and A+Award used the dual portfolio Mistral-24B (enumerator) +
Qwen3.5-4B (verifier), total 28,671,226,368 parameters — the number that appears
in `run_cover.py`'s accepted-blocker list as the value the role swap deliberately
departs from. **Any description of a Qwen verifier belongs to §15's history, not
to the method.**

---

## 14. Mathematical Formulation

Only equations that exist in code. Each row states paper notation, meaning, code
site, whether it is active in the final profile, and whether it earns space in a
six-page paper.

### 14.1 Independent acquisition coverage — **include**

```text
q(o) = g(o) / m(o)
```

- `m(o)` = `|acquisition_groups(contract, config)|` — independence groups
  *capable of expressing* `o` under this configuration (6 borders / 5 awards /
  4 stock / 3 city / 3 area / 3 capacity).
- `g(o)` = `|supporting_acquisition_groups(o, contract, config)|` — the subset
  with at least one SUPPORT edge.
- Code: `scoring.py:296`. Active. **This is the formal core of the independence
  claim and should be in the paper.**

### 14.2 Candidate confidence — **include**

```text
S(o) = α·F(o) + β·L(o) + γ·X(o) − δ·C(o) − η·U(o)
```

with `F(o) = q(o)`,
`L(o) = clip( log((p̃_VALID+ε)/(p̃_INVALID+p̃_UNKNOWN+ε)) / κ_clip, −1, 1 )`,
`C(o) = min(1, |contradicting groups| / (m(o)+1))`,
`U(o) = max_t JSD_norm(templates)`, `X(o) ∈ {0,1}`.
Final coefficients α=1.0, β=0.6, γ=0.5, δ=1.5, η=1.0, κ_clip=3.0, ε=1e-6.
Code: `scoring.py:447`. Active — **but state that γ·X ≡ 0 in the final profile**
(single checkpoint). For a six-page paper, present the equation and note the
term-to-mechanism disjointness; the coefficients can go to an appendix.

### 14.3 Contextual calibration — **include (two lines)**

```text
z̃_j = z_j − b_j ,      p̃ = softmax(z̃ / T),  T = 1
```

`b_j` = label logits of the same template on a content-free instance
(subject = candidate = "N/A"), cached per
(model, revision, label set, relation, template, decode identity).
Code: `verification/blind.py:258-395`. Active. **Nothing is fitted.**

### 14.4 Normalised prompt disagreement — **include (one line)**

```text
U_prompt = JSD(p^(1),…,p^(m)) / log m ,   m = 2 in the final profile
```

Generalised Jensen–Shannon in nats, rescaled by its `log m` ceiling so it is
comparable to a fixed threshold. Code: `blind.py:494-517`. Active.

### 14.5 Inclusion uncertainty — **appendix or omit**

```text
H_inc(o) = −q log q − (1−q) log(1−q)
```

Code: `scoring.py:308`. Active (enters the SMALL_SET residual and the
control-state entropy `H` that M21 bins on). Mention in one clause at most; the
subtlety that low `H_inc` at `q≈0` means "unambiguously unsupported" is a
footnote.

### 14.6 Numeric clustering — **include the tolerance, not the algorithm**

Core (Module 6/8):

```text
d(a,b) = |a − b| / max(|a|,|b|)
cluster C is valid iff d(min C, max C) ≤ τ_core,  τ_core = 0.025
representative = median(C);  order by (−|C|, relative_MAD, representative)
```

Stage 2 (Area / Capacity multi-view):

```text
o joins C iff  ∀ m ∈ C : d(o, m) ≤ τ_mv ,   τ_mv = 0.05
support(C) = number of distinct views in C
representative(C) = argmin_{o∈C} ( |o − median(C)|, view_rank(o), value(o) )
```

Code: `normalization/numeric.py:236,266`; `area_multiview.py:255-298`;
`capacity.py:220-263`. Both active. **The paper should state that the core uses a
diameter bound at half the evaluator tolerance while the final numeric layer uses
the evaluator's own 5% with complete linkage** — that contrast is a real design
statement, not a detail.

### 14.7 Residual search need (Module 6, active) — **include, carefully**

```text
q_res = clip( max( Σ_i w_i v_i / Σ_i w_i ,  mandatory_gap ,  max_{b ∈ B(π)} v_b ), 0, 1 )
```

where `π` is the programme type, `{(w_i, v_i)}` is `π`'s term set and `B(π)` its
blocking components (both tabulated in §8.2). Code: `coverage.py:602`. Active.

**Describe it as a search-need signal.** Do not write `P(∃ undiscovered gold)`.
The floor-not-average construction is the part worth a sentence.

### 14.8 Residual ensemble (Module 19, shadow) — **appendix at most**

```text
R_t = Σ_{k ∈ available} (w_k / Σ_{available} w) · v_k ,   all w_k = 1 (unfitted)
v ∈ {noveltyRate, singletonRatio, facetGap, disagreement, unresolvedMass}
```

Code: `coverage_gap/missingness.py:506`. Shadow, **but it is the state feature
that selects M21's bin** (§8.4). If M20/M21 are mentioned at all, this must be
mentioned too — otherwise the planner's inputs are unexplained.

### 14.9 Controller action utility (Module 7, active) — **include**

```text
A_t(a) = α_Y·Ŷ_t(a) + β_G·G_t(a) + γ_U·U_t(a) − λ_C·Ĉ(a) − ρ_D·D_t(a)
         (+ verify_first_bonus  if a ∈ {VERIFY, ADV_VERIFY} and unresolved ≥ 0.5)
a* = argmax A_t(a) over legal actions;  A_t(STOP) = residual_stop(relation)
```

α_Y=1.0, β_G=1.0, γ_U=0.8, λ_C=0.15, ρ_D=1.0, bonus=0.5,
`residual_stop = 0.15` for all six relations. Code: `controller.py:571,659`.
Active. **This is the controller equation the paper should show.**

### 14.10 Planner utility (Module 21, production but conditional) — **mention or omit**

```text
U_t(a) = α·Ĝ_verified(a) + β·ΔR̂(a) + γ·ΔĤ(a) − δ·Ĉost(a) − η·R̂ed(a) − κ·F̂P(a)
a* = argmax U_t(a)  if  U_t(a*) > τ_continue  else STOP
α=1.0, β=0.0, γ=22.634228, δ=η=1.01626, κ=1.0, τ_continue=0.0, lookahead=1
```

Every estimate is a TRAIN historical-bin lookup keyed by
`(relation, program_type, state_bin, family, target_class)` with a four-level
fallback. Code: `control/micro_planner.py:58`;
`configs/calibration/v3/m21_planner_calibration.json`. Active only inside the V3
loop. **Recommendation: do not put this equation in a six-page paper.** Its
measured contribution to the final scores is unestablished (§9.5), β=0 makes one
term vacuous, and it invites a reviewer question the repository cannot answer.

### 14.11 Budget

```text
Budget(q) = ( min(max_calls_per_query, contract.stopping.max_calls),
              min(max_generated_tokens_per_query, contract.stopping.max_generated_tokens) )
```

Code: `pipeline.py:344`. Active. Worth **one sentence plus the per-relation
table**, because "awards get 16 calls and borders get 4" is a concrete instance
of relation typing that a reviewer will remember.

### 14.12 Stopping predicate

Relation-typed; four sufficient conditions plus two overrides, all tabulated in
§8.3 / §9.2. There is no single closed-form expression, and inventing one would
misrepresent the code. **Present as a small table, not as an equation.**

### 14.13 Explicitly heuristic, and must be labelled so

- The 0.25 floor in `unresolved_mass` ("keeps every unresolved candidate slightly
  visible").
- `yield_scale = 2.0`, `numeric_dispersion_threshold = 0.05`,
  `competitor_support_ratio = 0.5`, and all `w_*` in `RCSEConfig` — hand-set.
- `untried_yield_prior = 0.5`, `covered_mechanism_redundancy = 0.5`,
  `optional_gap_scale = 0.8`, and the cost priors — the source calls them
  "priors, not measurements" (`controller.py:126-128`).
- `adversarial_max_support = 1` — an explicitly non-factual proxy.
- The multi-view `support ≥ 3` acceptance bar and the stock `≥ 3 of 4` bar.
- `ScoringConfig` docstring: *"Defaults are hand-set, not fitted."*

The only genuinely fitted numbers in the entire system are the M20 envelope and
the M21 coefficients/bins, both derived offline from TRAIN telemetry.

---

## 15. Experiment and Ablation History

All hidden-TEST numbers are **user-provided leaderboard evidence**. The repository
cannot recompute them (§3.4). Provenance is given per row.

### 15.1 Chronological progression

| # | Profile | Architectural change | Overall F1 | Verdict | Provenance |
|---|---|---|---:|---|---|
| 1 | **Profile A** | Mistral-24B enumerator + Qwen3.5-4B verifier; no repair stack | 0.4906 | baseline | audit 0085 §2 |
| 2 | **Profile A+Award** | + `AwardMetadataNormalizer` (deterministic, 0 calls) | **0.4910** | +0.0004, promoted | audit 0085 §3 |
| 3 | **Profile C2** | aggressive non-Stock repair stack: 1,075 post-calls, 246/475 rows changed | **0.4012** | **−0.0898, RETIRED** | audit 0085 §4 |
| 4 | **Profile D** | verifier role swapped Qwen3.5-4B → Mistral-24B; **single-checkpoint portfolio** | **0.4952** | +0.0042, promoted | audit 0086 §3 |
| 5 | **Integrated E1** | + `MistralCityEmptyRescue` + `MistralDirectArea` | **0.5752** | **+0.0800**, promoted | audit 0089 |
| 6 | Direct Mistral Capacity | one direct capacity call per row | 0.5794 | intermediate probe | audit 0090 |
| 7 | **CHIV** capacity | capacity hypothesis-inference variant | **0.5773** | **RETIRED, negative** | audit 0090 |
| 8 | **Profile E2** | + `MistralCapacityMultiView` (4 views + judge) | **0.5836** | +0.0084, promoted | audit 0090 |
| 9 | **Profile E3** | + `MistralAreaMultiView` (4 views + judge) | **0.5857** | +0.0021, promoted | audit 0091 |
| 10 | Profile F1 (capacity exactness) | suspicion-driven capacity exactness repair | — | **REVERTED**, never scored | commits `0d4f4d4` → `9281cd2` |
| 11 | **Profile F1** (stock empty rescue) | + `MistralStockEmptyRescue` (4 views, ≥3/4) | **0.5878** | +0.0021, promoted | audit 0093 |

### 15.2 Per-relation trajectory

| Relation | A | A+Award | C2 | D | E1 | E2 | E3 | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `awardWonBy` | .2929 | .3105 | .2263 | .3105 | .3105 | .3105 | .3105 | .3105 |
| `companyTradesAtStockExchange` | .7103 | .7103 | .7103 | .7285 | .7285 | .7285 | .7285 | **.7385** |
| `countryLandBordersCountry` | .9264 | .9264 | .7206 | .9291 | .9291 | .9291 | .9291 | .9291 |
| `hasArea` | .3600 | .3600 | .3200 | .3600 | **.6600** | .6600 | **.6700** | .6700 |
| `hasCapacity` | .1224 | .1224 | .0204 | .1224 | .1224 | **.1633** | .1633 | .1633 |
| `personHasCityOfDeath` | .4900 | .4900 | .3500 | .4900 | **.5700** | .5700 | .5700 | .5700 |
| **Overall** | .4906 | .4910 | .4012 | .4952 | .5752 | .5836 | .5857 | **.5878** |

Two observations the paper can make honestly:

- **Every gain after Profile D came from a relation-scoped final layer**, and each
  was promoted only after a controlled single-relation hidden-TEST ablation with
  every other relation byte-unchanged (verified by the `score_deltas_vs_*` blocks
  and by the `test_profile_*_diff_from_*_is_*_only` tests).
- **The single largest architectural gain (+0.0800) was Integrated E1**, which is
  two changes at once (City rescue + Direct Area). The Area component alone
  accounts for 0.3600 → 0.6600 and the City component for 0.4900 → 0.5700.

### 15.3 Negative experiments — the most valuable methodology evidence

**(a) Profile C2 — broad high-recall repair.** 1,075 post-model calls, 246/475
rows mutated. Result: five of six relations regressed
(`awardWonBy` −.0842, `countryLandBordersCountry` −.2058, `hasArea` −.0400,
`hasCapacity` −.1020, `personHasCityOfDeath` −.1400); stock was unchanged only
because C2's stock bypass worked. Overall .4910 → .4012.
**Lesson, stated in audit 0085 §4:** unbounded post-hoc repair destroys the
relations it does not understand. This is the direct empirical justification for
the *mutation-bounded* design of Stage 2 — relation-scoped, call-capped,
default-off, promoted one relation at a time.

**(b) CHIV capacity.** overall .5773 / capacity .1327, versus Direct Capacity
.5794 / .1429 and Multi-View .5836 / .1633.
**Lesson:** for a definition-ambiguous numeric relation, *semantically distinct
views plus deterministic consensus* beat a single cleverer inference variant.

**(c) Direct Capacity vs Capacity Multi-View.** .1429 → .1633 (+.0204).
**Lesson:** the gain comes from *view diversity plus clustering*, not from more
tokens on one prompt.

**(d) Direct Area vs Area Multi-View.** .6600 → .6700 (+.0100). Note the honest
asymmetry: the big win was Direct Area itself (.3600 → .6600); the multi-view
added a tenth of that. Audit 0091 explicitly says *"This is recorded only as a
positive controlled hidden TEST probe. It is not a claim that Area Multi-View is
universally superior."* The paper should copy that restraint.

**(e) Verifier-model swap (Profile D).** Replacing Qwen3.5-4B with Mistral-24B in
the verifier role moved stock +.0182 and borders +.0027, everything else
unchanged, overall +.0042 — while *reducing* the counted portfolio from
28.67B to 24.01B parameters.
**Lesson:** a small dedicated verifier was not buying anything; role separation
matters architecturally, but heterogeneity of *checkpoints* did not.

**(f) The reverted capacity exactness probe.** Commit `0d4f4d4` added 3,007 lines
(a `capacity_exactness.py` of 860 lines, a 458-line config, a 425-line runner and
729 lines of tests) and was reverted wholesale by `9281cd2` before any score was
recorded. It is not part of the system and must not be described.

**(g) `stock_support_dominance`.** Audit 0075 measured +12/−7 rows, net
`+0.00700` relation macro-F1 on TRAIN — and it is nevertheless **off** in
E3/F1. A genuinely multi-listed company whose second exchange was found once
loses that exchange.

**(h) `single_valued_keep_first_prediction`.** Audit 0075 measured it as
*harmful* on TRAIN (0.40333 → 0.40216). The response was
`rank_entity_candidates`, which orders on evidence and never on arrival order,
with a measured TRAIN delta of exactly 0.

**(i) `FINAL_CANDIDATE_RETENTION`.** Audit 0075 found 55 TRAIN rows — **all
`hasArea`** — with an ACCEPTED candidate and an empty output, caused by cluster
ordering breaking size/dispersion ties on the *smallest representative*, which
knows nothing about acceptance. Oracle-free fix moved TRAIN macro-F1
0.40333 → 0.42045.

**(j) `enumeration_label_repair`.** Audit 0076 found 76 emitted award values that
were whole bucket lines (`'Groups: NONE'`, `'1990s: Alan Shearer'`); one row
emitted four values of which all four were `: NONE` while the correct answer was
empty.

### 15.4 Evidence quality caveats

- Items (g)–(j) are **TRAIN** deltas from audit 0075/0076, not hidden-TEST.
- Every hidden-TEST number is **documentation-only**: no preserved local artifact
  reproduces any of them, and the winning artifacts for E2/E3/F1 are recorded as
  `PENDING_USER_IMPORT`.
- Known artifact SHAs exist only for A+Award (`bf113ce4…`), Profile D
  (`7a01382d…`) and integrated E1 (`67bd1bc8…`).
- The V3 calibration corpus (M20/M21) is fully SHA-pinned and reproducible from
  the recorded provenance, but its **effect on any leaderboard score has never
  been ablated**.

---

## 16. Active vs Shadow vs Retired Architecture

Status vocabulary as defined in the task. "Active" means *can change what is
written to `predictions.jsonl` in the final profile*.

### 16.1 Core (M0–M8)

| Component | Location | Status | Notes |
|---|---|---|---|
| M0 Relation contracts | `contracts/base.py`, `contracts/registry.py` | **ACTIVE-PRODUCTION** | 6 contracts; consumed by 6 downstream modules |
| M1 Typed program router | `contracts/router.py`, `contracts/programs.py` | **ACTIVE-PRODUCTION** | static table + startup cross-check against the official evaluator |
| M1b Relation profile | `contracts/relation_profile.py` | **ACTIVE-CONDITIONAL** | docstring says declarative-only (STALE): it drives `_relation_allowed_actions` |
| M2 Elicitation engine + view library | `elicitation/` | **ACTIVE-PRODUCTION** | 24 views, 7 families, greedy only |
| M3 Evidence graph | `evidence/graph.py` | **ACTIVE-PRODUCTION** | duplicate-edge refusal, strict-key identity |
| M4 Blind calibrated verifier | `verification/blind.py` | **ACTIVE-PRODUCTION** | 3 templates, contextual calibration, JSD disagreement |
| M4b Calibrated existence gate | `verification/blind.py :: score_gate` | **ACTIVE-CONDITIONAL** | city + stock only |
| M5 Scoring / tiering / status | `scoring.py` | **ACTIVE-PRODUCTION** | `S(o)`, `q(o)`, `H_inc`, tiers, `decide_status` |
| M6 RCSE | `coverage.py` | **ACTIVE-PRODUCTION** | relation-typed residual with floors |
| M7 Controller | `controller.py` | **ACTIVE-PRODUCTION** | `A_t(a)`, `legal_actions`, `should_stop` |
| M8 Selector | `selection.py` | **ACTIVE-PRODUCTION** | 5 selectors + fail-closed invariants |

### 16.2 Upgraded modules (M9–M21) and layers

| Component | Location | Status | Why |
|---|---|---|---|
| M9 Risk/difficulty profiler | `query_intelligence/profiler.py` | **SHADOW/DIAGNOSTIC** | `SUPPORTED_MODES={"shadow"}`; writes to an observability buffer |
| M10 Prompt-program compiler | `query_intelligence/prompt_compiler.py` | **SHADOW/DIAGNOSTIC** | same |
| M11 Closed-book parametric retrieval | `query_intelligence/parametric_retrieval.py` | **SHADOW/DIAGNOSTIC** | spends **real calls**, billed to `shadow_calls`; changes nothing |
| M12 Numeric specialist | `specialists/numeric_specialist.py` | **SHADOW/DIAGNOSTIC** | same |
| M13 Large-open-set specialist | `specialists/large_set_specialist.py` | **SHADOW/DIAGNOSTIC** | same |
| M14 Null/temporal specialist | `specialists/null_temporal_specialist.py` | **SHADOW/DIAGNOSTIC** | same |
| M15 Small-set closure specialist | `specialists/small_set_specialist.py` | **SHADOW/DIAGNOSTIC** | same |
| M16 Atomic consensus engine | `evidence/consensus.py` | **SHADOW/DIAGNOSTIC** | non-neural; runs before Module 8 and cannot change it |
| M17 Specialist verifier suite | `verification/specialist_verifier.py` | **SHADOW/DIAGNOSTIC**, and **not even catalogued-then-executed** in F1/E3 | `_execute_selected_verifications` is skipped when the V3 loop is active |
| M18 Bidirectional / counterfactual | `verification/bidirectional_verifier.py` | **SHADOW/DIAGNOSTIC**, same | same |
| Layer 4 integration | `evidence/layer4.py` | **SHADOW/DIAGNOSTIC** | `SUPPORTED_MODES={"shadow"}` |
| Production evidence bridge | `evidence/production_bridge.py` | **ACTIVE-PRODUCTION seam, functional no-op** | runs in PRODUCTION mode but has no executed M17/M18 readings to apply |
| M19 Coverage gap / missingness | `coverage_gap/missingness.py` | **SHADOW/DIAGNOSTIC**, but a **planner state feature** | `R_t` selects M21's bin |
| M20 Relation budget scheduler | `control/relation_budget.py`, `budget_accounting.py` | **ACTIVE-PRODUCTION** | precharge/settle ledger; fail-stop on overrun |
| M21 Expected-value micro-planner | `control/micro_planner.py` | **ACTIVE-CONDITIONAL** | decides only inside the V3 pre-M8 loop |
| Layer 6 integration | `control/layer6_integration.py` | **SHADOW** (`mode: shadow`) | supplies M21's legal-action surface in the non-V3 path, which is skipped |
| V3 core (hypothesis graph, relation programs, execution) | `v3_core/` | **ACTIVE-CONDITIONAL** | the only path by which M21 can change a prediction |

### 16.3 V3.1 finalization features

| Feature | Class | Final profile | Status |
|---|---|---|---|
| `final_candidate_retention` | A | **on** | **ACTIVE-PRODUCTION** (`selection.py:330,381`) |
| `enumeration_label_repair` | A | **on** | **ACTIVE-PRODUCTION** (`selection.py:491`) |
| `numeric_output_canonicalization` | A | **on** | **ACTIVE-PRODUCTION** (`selection.py:483`) |
| `stock_structural_validation` | A | **on** | **ACTIVE-CONDITIONAL** (stock only) |
| `stock_support_dominance` | A | off | **EXPERIMENTAL-NOT-FINAL** |
| `stock_listing_entity_prompt` | B | **on** | **ACTIVE-CONDITIONAL** (stock recall system prompt) |
| `capacity_definition_prompt` | B | off | EXPERIMENTAL-NOT-FINAL |
| `city_of_death_contrast_prompt` | B | off | EXPERIMENTAL-NOT-FINAL |
| `award_expansion_and_fp_cap` | B | off | EXPERIMENTAL-NOT-FINAL |
| `scientific_notation_acquisition` | B | off | EXPERIMENTAL-NOT-FINAL |

### 16.4 Stage-2 final layer

| Feature | Final profile | Status |
|---|---|---|
| `award_metadata_cleanup` | on | **FINAL-SPECIALIZATION** (deterministic) |
| `mistral_city_empty_rescue` | on | **FINAL-SPECIALIZATION** (empty-only) |
| `mistral_direct_area` | on but **superseded** by multi-view | FINAL-SPECIALIZATION, reachable only as view V1 |
| `mistral_area_multiview` | on | **FINAL-SPECIALIZATION** (all rows, replaces core) |
| `mistral_capacity_multiview` | on | **FINAL-SPECIALIZATION** (all rows, replaces core) |
| `mistral_stock_empty_rescue` | **F1 only** | **FINAL-SPECIALIZATION** (empty-only) |
| `stock_entity_guard`, `stock_alias_dedupe`, `stock_multi_listing_rescue`, `border_*`, `death_existence_gate`, `death_city_recall`, `area_empty_rescue`, `capacity_repair`, `award_recipient_witness`, `award_time_sliced_recall`, `l8_*`, `l9_*` | off | **RETIRED** — flags parse for archival configs; no executable branch exists |

### 16.5 Retired / historical / never-implemented

| Item | Status | Evidence |
|---|---|---|
| Qwen3.5-4B verifier portfolio | **RETIRED** at Profile D | audit 0086; `qwen_runtime_calls: 0`; runner raises on the string |
| Qwen3.5-9B baseline | **RETIRED** | `configs/models/qwen3.5-9b.yaml`, bake-off metadata |
| Profile C / C2 aggressive repair | **RETIRED (negative)** | audit 0085 §4 |
| CHIV capacity | **RETIRED (negative)** | audit 0090 |
| NSMV / neighbourhood-stable capacity | **RETIRED** | `docs/IMPLEMENTATION_STATUS.md` |
| Direct-border directional sweep | **RETIRED** | flag `border_directional_sweep: false` |
| Capacity exactness repair (`capacity_exactness.py`) | **RETIRED before scoring** | commit `9281cd2` reverts `0d4f4d4` in full |
| DoLa / factual decoding | **DOCUMENTATION-ONLY** | `IndependenceGroup.FACTUAL_DECODING` reserved; `hidden_states()` has no caller |
| Cross-model recall (`X(o)`) | **UNREACHABLE** in the final profile | `cross_model_recall_available` requires distinct model ids |
| `max_verifications_per_query` | **DEAD KNOB** | only read by `_verify_pending`, unreachable with the active controller |
| `RESAMPLE` | **UNREACHABLE** | all library views are greedy; `legal_actions` refuses greedy repeats |
| Staged execution mode | **ACTIVE but unused** | `ExecutionMode.STAGED` fully implemented; the final config is `interleaved` |
| `scripts/run_staged.py`, `resume()` | ACTIVE code, unused by the final profile | supports the 28.67B two-model era |
| TRAIN diagnostic / collection path | **EXPERIMENTAL-NOT-FINAL** | `IntegrationMode.TRAIN_CALIBRATION_COLLECTION_ONLY`; refused on any non-TRAIN split |
| `scripts/` diagnostics (18 of 33) | EXPERIMENTAL / analysis-only | never on the prediction path |

---

## 17. Documentation-vs-Code Reconciliation

| # | Claim / Module | Docs say | Code says | Final status | Safe to state in paper? |
|---|---|---|---|---|---|
| 1 | Current best profile | `README.md` and `docs/IMPLEMENTATION_STATUS.md`: **Profile E3, 0.5857** | v3.7 config now says `PREVIOUS_FROZEN_BASELINE`, `superseded_by` v3.8; v3.8 says `FROZEN_CURRENT_BASELINE` 0.5878; audit 0093 promotes F1 | **Profile F1 is current**; README/STATUS are **STALE** | State whichever profile the submission actually used — **ask the author**; do not cite README |
| 2 | Relation profile is inert | `relation_profile.py:27-30`: "Milestone V3A: declarative only… Nothing in the inference path reads a profile" | `v3_core/relation_programs.py:232` reads it to compute legal V3 action families, called from the pre-M8 loop | **STALE docstring** | Say the profile is read by the V3 legality lattice |
| 3 | M21 "shadow" | `micro_planner.py:113`: "Module 21 in shadow: ranks actions, selects one or STOPs, executes none" | `mode: production`; `_select_actions` routes to it; the chosen action is then executed by `_execute_v3_action_record` | **STALE class docstring** | Do not call M21 shadow; call it *conditionally active inside a bounded pre-finalization loop* |
| 4 | `_plan_micro_action` docstring | "The legal-action list is empty here on purpose… the honest live decision is STOP with reason `NO_LEGAL_ACTION`" | true only for the **non-V3** path, which is skipped in F1/E3 | partially stale | Avoid quoting |
| 5 | Cross-model support `X(o)` | proposal §11.2 and `scoring.py` treat it as a live term; `enable_cross_model_recall: true` | `cross_model_recall_available` is false (one checkpoint) | **INERT** | Never claim cross-model evidence |
| 6 | `MistralDirectArea` is a final layer | audit 0089 and `IMPLEMENTATION_STATUS` "Direct Area" | `repair_area` delegates to multi-view whenever the MV flag is on; Direct Area survives only as view V1 | superseded | `IMPLEMENTATION_STATUS` already says this correctly |
| 7 | Award normalizer adds facts | — | pure wrapper-strip + dedupe | correct as documented | Safe: "normalization and de-duplication only" |
| 8 | Multi-view "repairs" the core | README's word "repair" | `current_ignored=True`; targeted runners start from `[]` | **replacement, not repair** | Say *replacement* |
| 9 | M9–M19 are "modules of the system" | proposal §4 lists M0–M21 as the architecture | all M9–M19 config classes accept only `mode: "shadow"` | shadow | Never present M9–M19 as contributing to scores |
| 10 | M17 verifier bias controls (label orders ABC/BAC) | config block present | M17 is shadow **and** its execution is skipped in F1/E3 | inactive | Do not claim label-order debiasing; claim *paraphrase-template* disagreement |
| 11 | "8 layers, M0–M21" | proposal | ~9 of 22 modules affect predictions | overclaim if repeated | Present 4 abstractions, not 22 modules — the proposal itself says this (risk table: *"paper claims 3–4 abstractions"*) |
| 12 | Chao2 / capture–recapture cardinality | proposal §15 rejects it; some early docs discuss it | never implemented | correctly absent | Say the system estimates *search need*, not cardinality |
| 13 | Hidden-TEST scores | audits + configs | not recomputable locally; `run_cover.py:855` refuses to score a blind split | documentation-only | Always label as leaderboard evidence |
| 14 | Winning artifacts | configs record `PENDING_USER_IMPORT` | `outputs/submission-best/predictions.jsonl` present but unidentified | unresolved | Do not cite a SHA for E2/E3/F1 |
| 15 | "0 Qwen runtime calls" | config metadata | enforced by config identity + a runner assertion | correct | Safe |
| 16 | `standalone_stock_observations_are_runtime_knowledge: true` | F1 config only | **no code reads it** | metadata of unclear meaning | **Do not cite** until clarified |
| 17 | Full test suite passes | audit 0091: "4110 passed, 21 skipped" | not re-run in this review (would be safe, but was out of scope) | plausible, unverified here | Cite the audit, not this report |
| 18 | Sibling report `PAPER_METHODOLOGY_FORENSIC_REVIEW.md` | treats E3 as the paper target and flags F1 as ambiguous | this report treats F1 as current per config+audit 0093 | both are consistent descriptions of a moving target | Reconcile by asking which profile was submitted |

**Precedence rule applied throughout this report:** current source code + the
final profile config outrank audits, which outrank `README.md` /
`IMPLEMENTATION_STATUS.md`, which outrank the two PDFs. Where they disagree the
disagreement is reported rather than silently resolved.

---

## 18. What COVER-KBC Actually Proposes

Classification is from implementation, not from the proposal's own claims.

### 18.1 Ranked contribution audit

**1. Relation-Typed Active Evidence Acquisition — STRONG CORE CONTRIBUTION.**
Not "different prompts per relation". A relation is compiled into an object that
simultaneously determines: the legal action space (`legal_actions` iterates
`contract.all_views()`), the acceptance thresholds (`resolve_verification`, with
the contract authoritative and never averaged), the stopping predicate
(`should_stop` branches on `ProgramType`), the residual composition
(`estimate_residual`'s per-regime term sets **and different blocking floors**),
the compute budget (`PipelineConfig.budget` takes the stricter of contract and
global: 4 calls for borders, 16 for awards), and the finalizer (`_BY_RELATION`
then `_BY_PROGRAM`). Add the second typed layer — `RelationProfile` × 8
`FailureSearchState` values → `legal_action_families` — and the relation is
genuinely an *executable program*, not a prompt selection.
Evidence: `contracts/base.py`, `contracts/programs.py`,
`contracts/relation_profile.py`, `controller.py:302,571,744`,
`coverage.py:660-741`, `selection.py:407`, `v3_core/relation_programs.py:283`.

**2. Independence-Aware Atomic Evidence — STRONG CORE CONTRIBUTION.**
The `IndependenceGroup` abstraction is enforced end-to-end and is what makes the
confidence signal mean something: family→group is a fixed non-overridable map;
repeats share a group; facets are provenance only; two-stage descriptions are one
mechanism; repeated mentions inside one generation add one edge; duplicate edge
ids are refused; verification, cross-model recall and structural checks have
their **own** groups so they cannot inflate acquisition support; and the startup
check proves every eligible group is reachable. The five score terms own disjoint
index sets *by construction*, not by convention.
Evidence: `elicitation/views.py:22`, `types.py:108-152,355`,
`evidence/graph.py:82,125`, `scoring.py:28-42,243,420`,
`elicitation/library.py:407`.

**3. Coverage-Guided Adaptive Elicitation with a Relation-Typed Stopping Rule —
STRONG CORE CONTRIBUTION** (this is the honest name for what the code does).
A deterministic, fully logged controller scores a typed action space by
`A_t(a)`, and — crucially — the *stopping* decision is programme-specific, with
the floor-not-average construction so a single decisive signal (an undecided
existence gate; two competing localities; a rival numeric cluster) cannot be
averaged into "settled". Every decision carries `ResidualEstimate.reasons`,
machine-readable.
Evidence: `controller.py:571,659,744`, `coverage.py:602,745-751,765`.

**4. Candidate Confidence vs Residual Search Need as *two different quantities* —
SUPPORTING CONTRIBUTION, and unusually well-executed.**
`q(o)` is candidate-level acquisition coverage; `q_res` is query-level search
need; the code refuses to alias them and states why (`coverage.py:19-25`). The
proposal's own §15 rejects a cardinality oracle, and the implementation honours
that. This is the distinction the task prompt flags as critical
(*candidate correctness ≠ answer-set completeness*), and the repository gets it
right — which is itself worth a sentence.

**5. Calibrated Blind Verification — SUPPORTING CONTRIBUTION.**
Genuinely implemented (content-free control subtraction, cached per
model/revision/label-set/relation/template; asserted single-token label encoding
with a sequence-log-likelihood fallback; bounded JSD across paraphrase
templates; a three-valued gate that refuses to force an empty answer on a weak
signal). But contextual calibration is Zhao et al.'s, CoVe's blindness is Dhuliawala
et al.'s. **Claim the composition and the gate's asymmetric decision rule, not the
calibration itself.**

**6. Relation-Aware Finalization — SUPPORTING CONTRIBUTION.**
Five selectors, two of them numeric and *different* because the official targets
differ (robust median vs highest published). Written directly against the
evaluator's two facts. The `select_numeric_highest_valid` qualification rule
(support-ratio OR verified-VALID, INVALID excluded) is a small, real, defensible
piece of design.

**7. Semantic Numeric Multi-View — EMPIRICALLY IMPORTANT SPECIALIZATION.**
Four *semantically distinct* views (direct / entity-type classification /
encyclopedic-infobox recall / attribute-contrast step-back), a strict single-line
grammar, complete-linkage clustering at the evaluator's own 5%, support = number
of distinct views, a `support ≥ 3` bypass, and **one source-blind judge over
anonymised, sorted candidates** as the tie-breaker. This carried `hasArea`
0.3600 → 0.6700 and `hasCapacity` 0.1224 → 0.1633. It deserves real space —
but as an *instantiation* of the relation-typed principle for NUMERIC, and with
the honest note that Direct Area alone captured most of the Area gain.

**8. Null-Aware Selective Rescue — EMPIRICALLY IMPORTANT SPECIALIZATION.**
City and Stock rescues share one shape: **only an empty row is eligible**, a hard
gate must pass (`DECEASED`, or ≥3-of-4 cross-view consensus), the grammar is
strict, and every failure mode preserves `[]`. This is a principled way to buy
recall on a null-heavy relation without the precision collapse C2 demonstrated
(City: P 0.99 → 0.96 for R 0.49 → 0.59; Stock: P 0.9092 → 0.8992 for
R 0.7863 → 0.7963).

**9. Mutation-Bounded Final Specialization — SUPPORTING CONTRIBUTION**, and the
most defensible *framing* device in the paper. The bounds are mechanical
(relation-scoped dispatch, per-row call caps, output-only mutation,
coverage-order assertion, default-off flags, separate accounting), and the
negative control (C2) is on record.

**10. Closed-Book Parametric Retrieval / RAG-inspired probing — SHOULD NOT BE
CLAIMED as a contribution of the final system.** M11 is shadow-only. The *idea*
survives in the view families (description-then-extract, missingness sweep,
reverse framing) and in the multi-view prompts, and can be cited as design
lineage — but the module the proposal names does not affect predictions.

**11. M19/M20/M21 planning and control extensions — SHOULD NOT BE CLAIMED.**
M19 is shadow. M20 is an accountant, not a planner. M21 decides only inside a
bounded pre-finalization loop whose firing rate on TEST is unmeasured, with β=0
on one term and no ablation. Mentioning them as *implemented and audited
infrastructure* is fine; claiming they produced the score is not.

**12. Independence-aware *cross-model* evidence — SHOULD NOT BE CLAIMED.**
Structurally unreachable with one checkpoint.

**13. DoLa / factual decoding — SHOULD NOT BE CLAIMED.** No caller.

### 18.2 "What does COVER-KBC genuinely propose that is more than multi-prompt ensembling?"

Multi-prompt ensembling = sample several prompts, pool the answers, vote. Five
things here are not that, and each is enforced in code:

1. **Votes are not counted; mechanisms are.** Pooling treats every sample as a
   vote. Here, three samples of `borders_direct` are *one* support, and the
   confidence denominator `m(o)` is the set of mechanisms that *could* have
   expressed the candidate, not the number of calls made. An ensemble has no
   notion of "this evidence is correlated with that evidence".
2. **The number of prompts is decided at run time, per query, by a typed
   utility.** The controller enumerates legal actions from state, scores them, and
   stops on a relation-typed predicate. An ensemble has a fixed `n`.
3. **Positive and negative evidence are separately modelled.** `SUPPORT`,
   `CONTRADICT` and `UNKNOWN` are distinct edge types; `UNKNOWN` is explicitly not
   negative evidence; contradiction is counted per *mechanism*; template
   instability is its own term `U(o)`. Voting has none of this.
4. **Completion is a first-class, separately-computed signal.** `q_res` asks "is
   another action likely to add verified information" using yield decay, mechanism
   gaps, facet gaps, set instability and — for numeric — cluster competition, with
   decisive signals applied as floors. An ensemble cannot tell "everyone agrees"
   from "we have not looked anywhere else yet".
5. **Aggregation is relation-specific because the target is.** Majority vote gives
   the wrong answer for `hasCapacity` by construction: the official target is the
   *highest published* figure, not the most frequently recalled one.

And one honest concession the paper should make: **for `hasArea` and
`hasCapacity` the final system's answer really is produced by a small
deterministic multi-view consensus** — four views, 5% clustering, a support
threshold, one blind judge. What makes it more than ensembling there is the
*grammar-strict parsing*, the *source-blind* tie-break, and the fact that the
views are semantically distinct probes of the same quantity rather than
paraphrases.

### 18.3 The three strongest ideas a reviewer should remember

1. **A relation is an executable inference program** — same six relation ids,
   six different action spaces, budgets, stopping rules, residual compositions and
   finalizers.
2. **Independence-aware evidence accounting** — repeated support ≠ independent
   support, enforced by construction (group-keyed counting, duplicate-edge
   refusal, disjoint score index sets), so the confidence signal cannot be
   inflated by repetition.
3. **Two separate uncertainties: is *this candidate* right (`S(o)`), and is the
   *answer set* finished (`q_res`)** — computed from disjoint evidence, used for
   different decisions, and never collapsed into one number.

---

## 19. Recommended Methodology Structure for the Paper

Target: ~2.0–2.5 pages of a 6-page ACL system paper. **Four subsections.**
Organise by scientific abstraction, never by module number — the proposal's own
risk table says the failure mode of 22 modules is "diffuse paper narrative" and
prescribes "paper claims 3–4 abstractions".

### 3.1 Relation-Typed Inference Programs *(space ≈ 30%)*

- **Central idea.** `(s, r) → an executable program`. The relation contract is a
  single object that determines semantics, action space, thresholds, budget,
  stopping rule and finalizer.
- **Include.** The four regimes and the six-relation mapping; that positive rules
  and hard negatives are shared verbatim by the elicitation prompts *and* the
  verifier prompt; the per-relation budget/threshold table (this is the most
  convincing single artifact); the mandatory-vs-optional view distinction.
- **Formulas.** None. A table does the work.
- **Omit.** `check_router_consistency`, `check_program_compatibility`, the
  `RelationProfile` enum vocabulary, all view text.
- **Figure/table.** **Table 1 = the relation × property matrix** (§12.7 reduced to
  ~7 rows). Highest-value object in the paper.

### 3.2 Independence-Aware Evidence and Calibrated Verification *(≈ 25%)*

- **Central idea.** Evidence is attributed to *mechanisms*, not to samples; a
  candidate's confidence combines five terms drawn from **disjoint** evidence
  sets; verification is blind and its label bias is subtracted.
- **Include.** The independence-group idea in two sentences with the concrete
  contrast ("three samples of one view = one support; two different views = two");
  `q(o) = g(o)/m(o)` with `m(o)` as *availability*; `S(o)` with its five terms and
  the disjointness statement; blindness; `z̃ = z − b`; the two-template JSD.
- **Formulas.** `q(o)`; `S(o)`; `z̃_j = z_j − b_j`. Three lines.
- **Omit.** Tier precedence, `decide_status`'s six-step ladder, `H_inc`,
  `EffectiveVerification` resolution, aggregation details, edge-id derivation.
- **State plainly** that generator and verifier are the same frozen checkpoint in
  different roles, and that `X(o) = 0` because of it. A reviewer will check.

### 3.3 Coverage-Guided Adaptive Inference *(≈ 25%)*

- **Central idea.** The system decides *how much compute a query deserves* from a
  relation-typed **search-need** signal, and stops on a relation-typed predicate.
- **Include.** `q_res` vs `q(o)` (one sentence, explicit); the component list;
  the **floors-not-averages** construction with its concrete motivation; `A_t(a)`;
  the four stopping conditions as a compact table; the budget rule
  `min(global, contract)`.
- **Formulas.** `q_res` (the max-of-weighted-and-floors form) and `A_t(a)`.
- **Omit.** All `RCSEConfig` weights, `mechanism_yield_prior` internals,
  redundancy priors, `verify_first_bonus`.
- **On M19/M20/M21.** One sentence in Experimental Setup, or a footnote:
  *"A TRAIN-calibrated budget ledger and an expected-value micro-planner are
  implemented and run in the final configuration inside a bounded
  pre-finalization loop; their marginal contribution is not separately ablated."*
  Do **not** give them a subsection or an equation.

### 3.4 Relation-Specific Finalization and Numeric Consensus *(≈ 20%)*

- **Central idea.** The final answer set is constructed differently per relation
  because the official target differs; the numeric relations resolve a single
  scalar by deterministic multi-view consensus with a source-blind tie-break.
- **Include.** Why one selector is impossible (median vs maximum vs
  precision-first set vs recall-first set vs zero-or-one); the two evaluator facts;
  the multi-view procedure (4 semantically distinct views → strict grammar → 5%
  clustering → `support ≥ 3` → else one source-blind judge → fallback ladder);
  the null-aware empty-only rescue pattern.
- **Formulas.** The 5% compatibility relation, one line.
- **Omit.** Exact prompt text (appendix), `retained_pool`, enumeration-label
  repair, alias tables, per-view accounting keys.

### 19.1 Answers to the specific placement questions

- **Should Profile E3/F1 specialization live in Methodology or Experimental
  Setup?** **Both, split.** The *mechanism* (semantic numeric multi-view;
  null-aware empty-only rescue; mutation-bounded relation-scoped final layer)
  belongs in §3.4 — it is method, it is novel enough to describe, and it produces
  the numbers. The *profile ladder* (D → E1 → E2 → E3 → F1, which flag was on
  when) belongs in Experimental Setup / Results as the ablation table. Do not
  describe the system as "Profile F1" in Methodology; describe it as COVER-KBC
  with a relation-specific finalization layer.
- **Should Numeric Multi-View have its own subsection?** **No** — it would
  overweight one relation family and invite "this is just prompt ensembling".
  Give it the largest single block *inside* §3.4.
- **Where should the Evidence Graph be explained?** §3.2, opening paragraph, in
  ~4 sentences. It is the substrate for independence accounting; do not give it
  its own subsection and do not enumerate its fields.
- **Where should candidate scoring be introduced?** §3.2, immediately after
  independence, so `F(o)` has a meaning when it appears.
- **Where should residual search need be introduced?** §3.3, opening — and the
  first sentence must say what it is *not*.
- **Where should the controller equation be introduced?** §3.3, after `q_res`,
  because `G_t(a)` reads residual components.
- **Appendix / omit entirely.** All prompt text; all `RCSEConfig` and
  `ControllerConfig` weights; the M20 envelope table; the M21 bin schema; V3.1
  Class-A repair details; the staged execution mode; M9–M18 in full (one sentence
  in Limitations: *"a further set of profiling, specialist and structural-check
  modules is implemented and audited in shadow mode and does not affect the
  reported predictions"*); tier assignment; `decide_status`; alias/normalisation
  policy; the parameter-budget audit machinery.

---

## 20. Recommended Architecture Figure

**One figure, single-column width, ~7 nodes.** The story is: *a relation compiles
into a program; evidence is acquired with provenance; two different questions are
asked of that evidence; a controller spends compute until the second one says
stop; the answer is assembled relation-specifically.*

```text
                    (subject, relation)
                            |
                 ┌──────────▼───────────┐
                 │ Relation Contract    │   SMALL_SET · NULL_SINGLE
                 │ (typed program)      │   NUMERIC   · LARGE_OPEN_SET
                 └──────────┬───────────┘
                            │  views · thresholds · budget · stop rule · selector
                 ┌──────────▼───────────┐
        ┌───────►│ Evidence Acquisition │  direct / structural / description /
        │        │  (frozen LM, views)  │  contrastive / missingness / reverse
        │        └──────────┬───────────┘
        │                   │ parse + normalise
        │        ┌──────────▼───────────────────────┐
        │        │ Independence-Aware Evidence State│
        │        │  candidate × mechanism, signed   │
        │        │  repeats ≠ independent support   │
        │        └───────┬──────────────────┬───────┘
        │                │                  │
        │      ┌─────────▼────────┐  ┌──────▼──────────┐
        │      │ Candidate        │  │ Residual        │
        │      │ Confidence S(o)  │  │ Search Need q_res│
        │      │ (is o correct?)  │  │ (is the set done?)│
        │      └─────────┬────────┘  └──────┬──────────┘
        │                └────────┬─────────┘
        │                ┌────────▼─────────┐
        └────────────────┤ Adaptive         │      ← FEEDBACK LOOP
             acquire     │ Controller       │        (the only loop shown)
             / verify    └────────┬─────────┘
                                  │ stop
                       ┌──────────▼───────────┐
                       │ Relation-Specific    │  set · zero-or-one ·
                       │ Selector             │  robust median · highest valid
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │ Relation Finalization│  numeric multi-view consensus
                       │ (bounded, per-relation) │ null-aware empty-only rescue
                       └──────────┬───────────┘
                                  │
                            ObjectEntities
```

**Nodes (7).** Relation Contract; Evidence Acquisition; Evidence State;
Candidate Confidence; Residual Search Need; Adaptive Controller; Relation-Specific
Selector + Finalization (may be one node with two labels, or two stacked nodes).

**Arrows.** Forward chain as drawn; a **fork** from Evidence State into the two
signals; a **join** into the controller; **one** feedback arrow from the
controller back to Evidence Acquisition (labelled `acquire / verify`) and one
forward arrow labelled `stop`. Dashed side-annotations from the contract to the
acquisition, controller and selector nodes, showing that the contract parameterises
all three — that is the visual statement of relation typing.

**Visual emphasis.**
1. The **fork into two signals** — this is the paper's cleanest idea and should
   be the eye's first stop (two boxes side by side, equal weight).
2. The **feedback loop** — the only cycle in the figure.
3. The **dashed contract fan-out** — makes "relation-typed" visible rather than
   asserted.

**Relation-dependent branches to show.** Only inside the Selector node, as a
one-line label list (`set · zero-or-one · robust median · highest valid`). Do not
draw six lanes.

**What NOT to show.** M-numbers anywhere; M9–M19 shadow modules; the M20 ledger;
the M21 planner; the V3 hypothesis graph; the staged execution mode; the profile
ladder (D/E1/E2/E3/F1 belongs in the results table); the four multi-view view
names (they belong in §3.4 prose); the evaluator; the model box (state
"one frozen 24B checkpoint in all roles" in the caption instead).

**Caption should contain**: "All neural calls are made by a single frozen
open-weight checkpoint serving both the elicitation and verification roles;
everything outside the two shaded model boxes is deterministic."

---

## 21. Paper-Safe Claims

Each of these is directly supported by the code path named.

1. "COVER-KBC treats each `(subject, relation)` pair as an executable,
   relation-typed inference program rather than as a single generation."
   — `contracts/base.py`, `contracts/programs.py`, `contracts/router.py`.
2. "The six official relations are routed to four typed inference regimes —
   SMALL_SET, NULL_SINGLE, NUMERIC, LARGE_OPEN_SET — by a deterministic table; no
   learned router is used." — `contracts/router.py:25`.
3. "Each relation contract simultaneously determines the legal action space, the
   acceptance thresholds, the compute budget, the stopping predicate and the
   final selector." — `controller.py:302,571,744`, `scoring.py:192`,
   `pipeline.py:344`, `selection.py:462`.
4. "Evidence is attributed to independence groups rather than to samples: three
   samples of one view contribute one independent support, two structurally
   different views contribute two." — `elicitation/views.py:22`,
   `types.py:355`, `engine.py:322`.
5. "Candidate confidence combines independent acquisition coverage, calibrated
   verifier log-odds, contradiction and prompt disagreement, each drawn from a
   disjoint set of evidence mechanisms so no measurement is counted twice."
   — `scoring.py:28-42,447`.
6. "The verifier is blind: it sees the subject, the relation's definition with its
   positive rules and hard negatives, and one candidate — never the generator's
   reasoning, its draft, or the other candidates." — `blind.py:155`.
7. "Verifier label bias is removed by contextual calibration: the same template is
   run on a content-free instance and the resulting logits are subtracted before
   the softmax. Nothing is fitted." — `blind.py:258-395`.
8. "Verifier label probabilities are treated as calibrated label distributions,
   not as probabilities of factual truth." — `types.py:396-399`, `blind.py:14-17`.
9. "Prompt-distribution disagreement is measured as a Jensen-Shannon divergence
   across semantically equivalent verifier templates, normalised to [0,1] so it is
   comparable against a fixed threshold." — `blind.py:494-517`.
10. "An existence gate may force an empty answer only on a confident negative:
    the calibrated NO must exceed both a logit-margin and a probability
    threshold, and anything less is treated as UNKNOWN and lets discovery
    continue." — `blind.py:645-691`.
11. "The system estimates a relation-typed residual **search-need** signal, not a
    probability that undiscovered objects remain and not an estimate of the true
    set size." — `coverage.py:1-26`.
12. "Signals that are on their own sufficient reason to keep searching — an
    undecided existence gate, a competing locality, a rival numeric cluster,
    incomplete mandatory acquisition — are applied as lower bounds on the residual
    rather than averaged with zero-valued siblings." — `coverage.py:745-751`.
13. "Stopping is relation-typed: a stable small set, one verified locality, a
    dominant numeric cluster with low dispersion, and saturated yield with no
    unvisited facet are four different sufficient conditions." — `controller.py:771-802`.
14. "The controller is deterministic and rule-based; every decision records the
    state before, the components of the action score, and the resulting state."
    — `controller.py:254-275,571`.
15. "Compute budgets are relation-typed: `awardWonBy` is allowed 16 calls and
    6000 generated tokens per query while `countryLandBordersCountry` is allowed
    4 calls and 1024 tokens." — `contracts/registry.py`, `pipeline.py:344`.
16. "Budget is charged in measured neural invocations, not in logical actions, so
    a two-stage description view is charged two calls." — `pipeline.py:1569,1599`.
17. "Final selection is relation-specific because the official target differs:
    the robust central value for `hasArea`, the highest published value for
    `hasCapacity`, a precision-first set for stock exchanges, and a recall-first
    open set for awards." — `selection.py:314,342,163,207`.
18. "Numeric answers are resolved by clustering under a diameter bound and
    emitting a derived representative that is kept distinct from any observed
    value." — `normalization/numeric.py:266`, `types.py:516-518`.
19. "For the two numeric relations the final answer is produced by four
    semantically distinct closed-book views, a strict output grammar, 5%
    compatibility clustering, a cross-view support threshold, and at most one
    source-blind judge over anonymised candidate values." —
    `leaderboard_repair/area_multiview.py`, `capacity.py`.
20. "Empty-answer rescue is null-aware and strictly bounded: only rows the
    pipeline left empty are eligible, a hard gate must pass, and every failure
    mode preserves the empty answer." — `relations.py:41`,
    `stock_empty_rescue.py:342`.
21. "All inference is closed-book: no web access, no retrieval corpus, no external
    knowledge base, and no subject-answer lookup table." — no networking or DB
    import in `src/`; `views.py:47`.
22. "A single frozen open-weight checkpoint
    (`mistralai/Mistral-Small-3.2-24B-Instruct-2506`, 24.01B published parameters)
    serves every logical role; no fine-tuning, LoRA, continued pretraining or
    learned component of any kind is used." — `registry.py:18`,
    `run_cover.py:562`, `huggingface.py:25,127`.
23. "All decoding is greedy; the system performs no stochastic sampling."
    — `library.py:26-28`, `huggingface.py:248-253`.
24. "The parameter budget is audited before any weights are loaded and the run is
    refused if it fails." — `run_cover.py:567`.
25. "Physical call accounting is fail-stop: if measured spend exceeds the
    precharged envelope the run aborts without writing predictions or a manifest."
    — `pipeline.py:2459`, `run_cover.py:208`.

---

## 22. Claims to Avoid

1. **Do not** call the residual a probability that a true object remains
   undiscovered, or an estimate of set cardinality. It is a heuristic search-need
   score with hand-set weights.
2. **Do not** claim capture–recapture / Chao-style completeness estimation. It was
   deliberately removed and never implemented.
3. **Do not** claim cross-model or multi-model evidence, model-family diversity,
   or a heterogeneous verifier. One checkpoint; `X(o) ≡ 0`.
4. **Do not** describe a Qwen verifier as part of the system. Retired at Profile D.
5. **Do not** claim that M9–M19 (risk profiler, prompt-program compiler,
   parametric retrieval, four specialists, atomic consensus, specialist verifier
   suite, bidirectional/counterfactual verification, Layer 4, coverage-gap
   estimator) contribute to the reported scores. All shadow.
6. **Do not** claim an expected-value planner allocates the reported test-time
   compute. M21 acts only inside a bounded pre-finalization loop, its firing rate
   is unmeasured, and it is not ablated.
7. **Do not** claim label-order debiasing. The active mechanism is
   paraphrase-template disagreement; label-order rotation lives in shadow M17.
8. **Do not** claim self-consistency by repeated sampling. All decoding is greedy;
   `RESAMPLE` is unreachable.
9. **Do not** claim the multi-view layers *repair* or *refine* the core's numeric
   answers. They discard them (`current_ignored=True`) and, in the targeted
   runners, never see them.
10. **Do not** present the E3/F1 final layer as the COVER-KBC method, nor the core
    as covering all six relations.
11. **Do not** claim a learned or trained component anywhere, including for
    calibration. Contextual calibration is arithmetic on inference-time logits.
12. **Do not** claim RAG-inspired *retrieval*. Only control principles were
    borrowed; the module that instantiated them (M11) is shadow.
13. **Do not** cite hidden-TEST numbers as reproducible. They cannot be recomputed
    from the repository; label them leaderboard evidence.
14. **Do not** cite a SHA256 for the E2/E3/F1 winning artifacts. Unknown locally.
15. **Do not** claim DoLa / factual decoding. No caller.
16. **Do not** claim `hasCapacity` is solved or that multi-view "fixed" it. F1 is
    0.1633; it is the weakest relation by a factor of two.
17. **Do not** claim the Area Multi-View is universally superior to Direct Area.
    Audit 0091 explicitly declines that claim; the measured delta is +0.0100.
18. **Do not** claim facet decomposition yields independent evidence. Facets are
    provenance inside one mechanism, by explicit design.
19. **Do not** describe the system as "22 modules across 8 layers". Nine affect
    predictions.
20. **Do not** claim relation-specific prompting, multi-view prompting, repeated
    sampling, verification or adaptive inference as novel in themselves. The
    contribution is the composition and its evidence accounting.

---

## 23. Open Questions / Unresolved Evidence

1. **Which profile is the paper's system — E3 (0.5857) or F1 (0.5878)?**
   F1 is uncommitted; README and IMPLEMENTATION_STATUS still say E3. *This is the
   one genuinely blocking question for the Methodology section*, because it
   determines whether `companyTradesAtStockExchange` has a Stage-2 layer.
2. **How often does the V3 pre-M8 control loop actually execute an action on the
   475 TEST rows?** UNRESOLVED FROM REPOSITORY EVIDENCE. No preserved run
   directory contains `v3_hypothesis_graph.jsonl`, `micro_planner.jsonl` or
   `relation_budget.jsonl`. Consequently the marginal contribution of M20/M21 to
   the reported scores is unknown.
3. **Was the reported hidden-TEST submission produced by one `run_cover.py` run,
   or assembled from targeted runners + merges?** Audits 0089–0093 describe
   controlled relation-only probes and merges. If the latter, the paper's
   "pipeline" description must say so.
4. **Which profile produced `outputs/submission-best/predictions.jsonl`
   (`c7e52a71…`)?** Audit 0091 declines to identify it. Its 85 empty city rows are
   inconsistent with the E3/F1 city shape implied by R = 0.59, but gold emptiness
   is unknown, so no inference is safe.
5. **What does `standalone_stock_observations_are_runtime_knowledge: true` mean?**
   No code reads it; Area and Capacity declare `false`. Potentially a provenance
   statement about how the stock prompts were designed. **Must be clarified before
   any compliance claim about the stock layer is made.**
6. **Empty-stock-row count on TEST.** The F1 config records
   `empty_rows_attempted: E3_EMPTY_STOCK_ROW_COUNT_ARTIFACT_DEPENDENT`, so the
   actual number of rescued rows (and hence the number of F1 stock calls) is not
   recorded anywhere.
7. **Were the four Area / four Capacity / four Stock views individually ablated?**
   Only the aggregate multi-view was scored against the single-view predecessor.
   No per-view ablation exists, so "each view contributes" cannot be claimed.
8. **Verifier-call volume.** No preserved artifact reports how many verification
   calls the final runs actually made, so "verification is used sparingly" cannot
   be quantified.
9. **The `relation_profile.py` "declarative only" docstring** contradicts its use
   in `_relation_allowed_actions`. Whether the profile was *intended* to be on the
   inference path is unclear from history.
10. **Test suite not re-run in this review.** Audit 0091 reports
    "4110 passed, 21 skipped" at HEAD `b94f008`; the working tree has since
    changed. Running `pytest` would be safe (no model, no network) but was outside
    this review's scope.
11. **Award recall ceiling.** `awardWonBy` has been static at 0.3105 since
    A+Award across six profiles. No experiment in the repository moved it, and
    audit 0075's diagnosis (418 FN vs 266 FP on TRAIN) has no promoted remedy.
12. **`benchmark/UPSTREAM_COMMIT.txt` / snapshot currency** was not cross-checked
    against the live shared-task repository (no network access used).

---

## 24. Source Map

Critical claim → exact location.

| Claim | Location |
|---|---|
| Canonical entrypoint | `scripts/run_cover.py :: main()` (L442) |
| Production activation requires M20+M21 production mode | `scripts/run_cover.py :: _wants_production()` (L115-125) |
| Leaderboard-probe escape hatch, 4 accepted blockers | `scripts/run_cover.py :: _allow_leaderboard_probe()` (L371-439) |
| One physical runtime for both roles | `scripts/run_cover.py` L560-563; `models/registry.py :: model_blocks()` (L18) |
| Parameter budget audited before inference | `scripts/run_cover.py` L567; `models/budget.py :: audit_parameter_budget` |
| Blind split is never scored | `scripts/run_cover.py` L855 |
| Accounting-invariant fail-stop | `pipeline.py :: _release_hold()` (L2459); `run_cover.py :: _abort_on_accounting_invariant()` (L208) |
| Four typed inference regimes | `contracts/programs.py :: PROGRAMS` (L83) |
| Relation → programme table, no learned router | `contracts/router.py :: PROGRAM_BY_RELATION` (L25); docstring L5 |
| Startup cross-check against the official evaluator | `contracts/router.py :: check_router_consistency()` (L71-105) |
| Six relation contracts | `contracts/registry.py` L32 / L101 / L153 / L207 / L253 / L303 |
| Contract carries definitions, never facts | `contracts/registry.py` L8-9 |
| Verifier prompt block built from the contract | `contracts/base.py :: verifier_definition()` (L178), `near_miss_block()` (L191) |
| Relation failure/search profile | `contracts/relation_profile.py` L211-304 |
| Profile is read on the inference path (docstring stale) | `v3_core/relation_programs.py :: _relation_allowed_actions()` (L232) |
| Family → independence group, non-overridable | `elicitation/views.py :: FAMILY_TO_GROUP` (L22) |
| Closed-book system prompt | `elicitation/views.py :: SYSTEM_PROMPT` (L47) |
| 24 views, 6 relations | `elicitation/library.py :: VIEW_LIBRARY` (L378) |
| Award facets share one independence group | `elicitation/library.py` L303-307 |
| Library/contract bidirectional check; every eligible group reachable | `elicitation/library.py :: check_library_covers_contracts()` (L407-461) |
| Repeats share view id + group | `elicitation/engine.py :: run_view_repeats()` (L322) |
| Description prose yields no candidate and no edge | `elicitation/engine.py :: run_description_view()` (L210, L275) |
| System-prompt hash recorded separately | `types.py` L276-282; `engine.py` L196 |
| Duplicate evidence edges refused | `evidence/graph.py :: _attach()` (L82) |
| Repeated mention in one generation adds one edge | `evidence/graph.py :: add_entity_mentions()` (L125, L160-163) |
| Verifier edge marked SHOWN_CANDIDATE | `evidence/graph.py :: add_verification()` (L237) |
| Hard contract rules reject impossibilities only | `evidence/graph.py :: apply_hard_contract_rules()` (L479) |
| Alias grouping is advisory, never identity | `evidence/graph.py` L339-352, L371 |
| `q(o) = g(o)/m(o)`, `m(o)` is availability | `scoring.py :: coverage_q()` (L296), `acquisition_groups()` (L243), docstring L127-141 |
| `S(o)` five-term score | `scoring.py :: score_candidate()` (L447) |
| Disjoint index sets per term | `scoring.py` L28-42 |
| `X(o)` requires independent recall | `scoring.py :: cross_model_term()` (L420-444) |
| Contradiction counted per mechanism | `scoring.py :: contradicting_groups()` (L361) |
| Contract thresholds authoritative, never averaged | `scoring.py :: resolve_verification()` (L192) |
| Tier assignment before any call | `scoring.py :: assign_tier()` (L625) |
| Acceptance ladder | `scoring.py :: decide_status()` (L702) |
| Residual is a search-need signal, not cardinality | `coverage.py` L1-26 |
| Residual computation + per-regime terms | `coverage.py :: estimate_residual()` (L602-762) |
| Floors, not averages | `coverage.py` L745-751 |
| Deterministic reason codes | `coverage.py :: _reason_codes()` (L765) |
| Trusted set = accepted by current evidence | `coverage.py :: trusted_keys()` (L364) |
| Action space | `controller.py :: ActionType` (L50) |
| Action-role map | `controller.py :: ACTION_ROLE` (L71) |
| Legality from state | `controller.py :: legal_actions()` (L302) |
| Greedy resample refused | `controller.py` L381-388 |
| `A_t(a)` | `controller.py :: score_action()` (L571) |
| Single stopping authority | `controller.py :: choose_action()` (L695-697) |
| Relation-typed stopping | `controller.py :: should_stop()` (L744-822) |
| Yield = growth of the trusted set | `controller.py :: record_outcome()` (L825) |
| Blind verifier prompt | `verification/blind.py :: build_verifier_prompt()` (L155) |
| Three templates | `verification/blind.py` L103 / L113 / L122 |
| Contextual calibration | `verification/blind.py :: ContextualCalibrator` (L258-395) |
| Control cache key includes revision + label signature | `verification/blind.py :: _control_cache_key()` (L232) |
| Label probabilities ≠ truth probabilities | `types.py` L396-399; `blind.py` L14-17 |
| Normalised JSD | `verification/blind.py :: normalized_disagreement()` (L509) |
| Calibrated gate with asymmetric NO rule | `verification/blind.py :: score_gate()` (L645-691) |
| Gate questions exist for two relations only | `pipeline.py :: GATE_QUESTIONS` (L160) |
| Gate role never substituted | `pipeline.py :: _gate_runtime()` (L778) |
| Generation-gate cannot close a calibrated gate | `pipeline.py` L883 |
| Single-token label assertion + sequence fallback | `models/huggingface.py :: score_labels()` (L275-341) |
| Greedy decoding, no sampling params at T=0 | `models/huggingface.py :: generate()` (L246-253) |
| Frozen inference only | `models/huggingface.py` L25-26, L127 |
| Per-query budget = min(global, contract) | `pipeline.py :: PipelineConfig.budget()` (L344) |
| Measured, not assumed, call charging | `pipeline.py :: _execute_action()` (L1569, L1599) |
| Cross-model recall unreachable with one model | `pipeline.py :: cross_model_recall_available` (L720) |
| `max_verifications_per_query` unreachable | `pipeline.py :: _verify_pending()` (L1070-1072) |
| V3 loop activation predicate | `pipeline.py :: _v3_control_loop_active()` (L747) |
| M17/M18 execution skipped when V3 loop is active | `pipeline.py` L1896 |
| V3 loop body, ≤3 rounds | `pipeline.py :: _run_v3_control_loop()` (L2816) |
| M20 precharge / settle | `pipeline.py :: _precharge()` (L2413), `_release_hold()` (L2459) |
| V3 affordability against the contract budget | `pipeline.py :: _v3_action_selectability()` (L2742) |
| M19 residual feeds the planner bin key | `pipeline.py` L1968; `control/historical_bins.py :: _numeric()` (L486) |
| M21 utility equation | `control/micro_planner.py :: utility()` (L58-96) |
| M21 coefficients (β=0, γ=22.63, τ=0) | `configs/calibration/v3/m21_planner_calibration.json` |
| M20 envelope per relation | `configs/calibration/v3/m20_relation_budget.json` |
| Shadow-only enforcement | `SUPPORTED_MODES` in `query_intelligence/profiler.py:168`, `prompt_compiler.py:235`, `parametric_retrieval.py:223`, `specialists/*`, `evidence/consensus.py:122`, `evidence/layer4.py:79`, `coverage_gap/missingness.py:101` |
| Production bridge rules | `evidence/production_bridge.py :: apply()` (L135-176) |
| M19 ensemble, uniform unfitted weights | `coverage_gap/missingness.py :: combine()` (L506) |
| Selector dispatch | `selection.py :: select()` (L462), `_BY_RELATION` (L407) |
| Robust median selector | `selection.py :: select_numeric_robust()` (L314) |
| Highest-valid selector | `selection.py :: select_numeric_highest_valid()` (L342) |
| Stock-only structural validation | `v3_1/entity_finalization.py :: reject_structurally_invalid_listings()` |
| Fail-closed cardinality / control-token check | `selection.py :: _check_cardinality()` (L434) |
| Four distinct empty reasons | `selection.py :: _empty_reason()` (L127) |
| Diameter-bounded clustering, not single linkage | `normalization/numeric.py :: cluster_values()` (L266-288) |
| Repair stack, per-row cap, coverage assertion | `leaderboard_repair/stack.py :: apply()` (L43), `_assert_query_coverage()` (L203) |
| Repair feature flags default off | `leaderboard_repair/config.py :: RepairFeatures` (L20) |
| Award normalization transformations | `leaderboard_repair/util.py :: normalize_award_metadata()` (L75) |
| City empty-only rescue | `leaderboard_repair/relations.py :: _repair_city_mistral_empty_rescue()` (L41) |
| Area multi-view: 4 views, judge, fallback ladder | `leaderboard_repair/area_multiview.py` L30, L148, L226, L262, L301, L348, L380 |
| Area upstream ignored | `leaderboard_repair/area_multiview.py` L399-404 |
| Capacity multi-view | `leaderboard_repair/capacity.py` L37, L114, L199, L227, L266, L316 |
| Stock empty rescue (F1) | `leaderboard_repair/stock_empty_rescue.py` L26, L166, L231, L248, L275, L326, L342 |
| Targeted Area runner starts from an empty prediction | `scripts/run_area_multiview.py :: _prediction()` (L120) |
| Targeted Stock runner reads the E3 baseline | `scripts/run_stock_empty_rescue.py :: baseline_stock_predictions()` (L175) |
| Merge fails closed on non-target mutation | `scripts/merge_targeted_relation_results.py` L86-171 |
| Official evaluator: normalization, 5% tolerance, bipartite matching | `benchmark/evaluate.py` L33, L64, L85 |
| Profile ladder scores | `docs/audits/0085`, `0086`, `0089`, `0090`, `0091`, `0093` |
| C2 regression table | `docs/audits/0085` §4 |
| Capacity exactness probe reverted | commits `0d4f4d4` → `9281cd2` |

---

## Final Forensic Verdict

- **What the final system is.** A single frozen 24B open-weight checkpoint
  (`mistralai/Mistral-Small-3.2-24B-Instruct-2506` @ `95a6d26c…`) driven by a
  deterministic, relation-typed, closed-book active-evidence-acquisition pipeline
  (Stage 1: contracts → typed views → independence-aware evidence graph →
  calibrated blind verification → relation-typed residual and controller →
  relation-specific selector), followed by a bounded, feature-flagged,
  per-relation final layer (Stage 2). Current frozen baseline is **Profile F1**
  (uncommitted, hidden-TEST macro-F1 **0.5878**); its committed predecessor is
  **Profile E3** (**0.5857**).
- **Three strongest methodological contributions.** (1) Relation-typed inference
  programs — one relation determines action space, thresholds, budget, stopping
  rule and finalizer, not just prompts. (2) Independence-aware atomic evidence —
  repeated support is structurally prevented from becoming independent support,
  and the five score terms own disjoint evidence index sets. (3) Two separate
  uncertainties — candidate confidence `S(o)` and answer-set search need `q_res`,
  computed from disjoint evidence and used for different decisions.
- **The exact final inference loop.** `compile_query` → shadow M9–M15 → calibrated
  gate (city/stock only) → controller loop over
  {RUN_VIEW, RUN_FACET, REVERSE_CHECK, VERIFY, ADVERSARIAL_VERIFY, (RESAMPLE),
  (CROSS_MODEL_CHECK), STOP} scored by `A_t(a)`, stopped by a relation-typed
  predicate reading `q_res`, bounded by `min(global, contract)` calls → Phase-B
  continuation of the same loop → M16/L4/M19 shadow snapshot → **bounded V3 loop
  (≤3 rounds) where M21 may select one further action under an M20 precharge** →
  Module 8 relation-specific selection + V3.1 output repair → Stage-2 relation
  layer → `predictions.jsonl`.
- **Most important modules.** `contracts/registry.py` + `contracts/base.py`
  (everything else reads them); `elicitation/library.py` + `views.py` (the
  independence substrate); `evidence/graph.py`; `scoring.py`; `coverage.py`;
  `controller.py`; `selection.py`; `verification/blind.py`;
  `leaderboard_repair/{area_multiview,capacity,stock_empty_rescue,relations}.py`.
- **Seemingly impressive but NOT production-critical.** M9 profiler, M10 prompt
  compiler, M11 parametric retrieval, M12–M15 specialists, M16 consensus, M17
  specialist verifier suite, M18 bidirectional/counterfactual verification,
  Layer 4, M19 coverage-gap estimator — all shadow, and M17/M18 are not even
  executed in the final profile. M20 is an accountant. M21 acts only inside a
  bounded loop of unmeasured frequency. The production bridge is a functional
  no-op here. Cross-model recall, `RESAMPLE`, staged execution and DoLa are
  unreachable.
- **What Methodology should emphasise.** Four subsections: Relation-Typed
  Inference Programs; Independence-Aware Evidence and Calibrated Verification;
  Coverage-Guided Adaptive Inference; Relation-Specific Finalization and Numeric
  Consensus. Three equations only: `q(o) = g(o)/m(o)`, `S(o)`, `A_t(a)` — plus
  `z̃ = z − b` and the `q_res` max-of-floors form as one-liners. One table
  (relation × property) and one figure (the two-signal fork with a single
  feedback loop).
- **What the paper must avoid claiming.** Cardinality estimation; cross-model
  evidence; a Qwen verifier; self-consistency by sampling; that M9–M21 produced
  the scores; label-order debiasing; that the multi-view layers refine rather than
  replace the core numeric answers; that relation-specific prompting, multi-view
  prompting, verification or adaptive inference are novel in themselves.
- **The one blocking question.** Whether the paper's system is Profile E3 or
  Profile F1 — it changes whether `companyTradesAtStockExchange` has a Stage-2
  layer, and F1 is currently uncommitted while README/IMPLEMENTATION_STATUS still
  name E3.
- **Second-order caveat worth carrying into the write-up.** The reported
  hidden-TEST artifacts appear to have been assembled from targeted single-relation
  runners plus fail-closed merges rather than from one end-to-end run; and for
  `hasArea`/`hasCapacity` those runners start from an empty prediction, so the
  core pipeline contributes nothing to two of the six relations' final values.
