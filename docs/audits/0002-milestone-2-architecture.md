# Audit 0002 — Milestone 2: Logit-Calibrated COVER-Core + Full Architecture Build-out

**Pre-work HEAD:** `3037c1f` — *feat: establish COVER-KBC benchmark foundation*
**Branch:** `main` (clean at start)
**Date:** 2026-08-03

---

## 1. Objective and scope

Milestone 2 began as *"Neural Baseline Activation + Logit-Calibrated
COVER-Core"*. **The execution policy changed mid-milestone** (see §2), which
removed local neural activation from scope and, in exchange, removed the
Milestone-3 deferral of RCSE, the active controller, adaptive stopping and
cross-model evidence.

Final scope:

- resolve model-budget metadata from primary sources;
- verify upstream benchmark provenance;
- harden deduplication and relation-aware parsing;
- implement the full COVER-KBC v2 architecture through the control layer;
- freeze the target model pairing and build a Colab-first execution path;
- **no heavyweight neural execution, and no fabricated metrics.**

DoLa remains deferred as an experimental plugin.

---

## 2. Execution-policy change (mandatory record)

Heavyweight neural execution was **moved from this machine to Google Colab**
part-way through the milestone.

What happened locally before the change:

| item | detail |
|---|---|
| model downloaded | `Qwen/Qwen3.5-9B`, ~19 GB, into `/mnt/.../FACTELICIT-AKBC/hf-cache` |
| load attempt 1 | 4-bit NF4, `device_map="auto"` → **failed**: modules dispatched to CPU/disk; the model does not fit 8 GB VRAM |
| root cause | 248,320-token vocab ⇒ `embed_tokens` (1.017 B) + `lm_head` (1.017 B) stay bf16 ≈ 4 GB, leaving too little for the quantised body |
| load attempt 2 | text-only + CPU-offload allowance — **not executed**; the policy change arrived first |
| local inference | **none performed** |
| metrics produced | **none. No neural metric exists in this repository.** |
| cache deleted | **yes** — `hf-cache` removed, 19 GB reclaimed |
| global caches | untouched (`~/.cache/huggingface` did not exist and was not created) |
| dev environment | retained: `conda-cover/` with torch 2.6.0+cu124, transformers 5.14.1 |

Hardware that made local execution impractical: RTX 4060 Laptop (7.62 GiB
usable VRAM), 14 GiB RAM (~6.7 GiB free), 24 GB free disk.

Useful metadata *was* obtained before deletion and is retained: verified
parameter breakdowns, tokenizer/label facts, and the architecture class of the
checkpoint. Those are recorded in §4 and in `configs/models/`.

---

## 3. Upstream benchmark provenance — RESOLVED

Milestone 1 left this unverified. Checked from a temporary clone **outside** the
repository:

```
pinned  : 30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57
upstream: refs/heads/main -> 30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57
```

- The pinned commit **exists upstream and is exactly current `main`.** The
  snapshot is not behind.
- Commit subject: *"Update baseline results"*. Immediately preceding:
  *"Avoid test-set examples in README"*, *"Normalize apostrophes and Unicode
  punctuation in evaluation"* — confirming the July 2026 evaluator update.
- **All 11 snapshot files match upstream byte-for-byte** (`evaluate.py`,
  `requirements.txt`, `LICENSE`, `configs/`, all three `data/*.jsonl`,
  `models/*.py`, both prompt templates).
- Upstream-only files not copied into our snapshot: `README.md`,
  `assets/codalab.png`. Intentional.
- `benchmark/` was **not modified**.

**New finding — the official `baseline.py` does not exist upstream.** The
upstream README documents `python baseline.py -c configs/...`, but commit
`30d8cfa` contains no such file. Only the config, prompt templates,
`abstract_model.py` interface and the published results table exist. Any future
"baseline reproduction" is therefore a **reconstruction**, and a difference from
0.313 must be interpreted with that in mind.

Official validation baseline, transcribed from the upstream README at the pinned
commit (**not** run by us):

| relation | macro-p | macro-r | macro-f1 | avg #preds | #empty |
|---|---|---|---|---|---|
| awardWonBy | 0.247 | 0.078 | 0.101 | 24.000 | 0 |
| companyTradesAtStockExchange | 0.369 | 0.725 | 0.354 | 1.170 | 0 |
| countryLandBordersCountry | 0.697 | 0.911 | 0.665 | 2.706 | 0 |
| hasArea | 0.290 | 0.290 | 0.290 | 1.000 | 0 |
| hasCapacity | 0.180 | 0.180 | 0.180 | 1.000 | 0 |
| personHasCityOfDeath | 0.210 | 0.600 | 0.210 | 1.000 | 0 |
| **All Relations** | **0.324** | **0.507** | **0.313** | 1.759 | 0 |

Matches the figures supplied in the task brief. Not hard-coded anywhere in
evaluation logic.

---

## 4. Model parameter sources and conservative budget counts

Counts were read from **primary sources**, never inferred from a model name.
Method for Qwen3.5-9B: HTTP range-read each safetensors shard header and sum
`numel(shape)` per tensor, bucketed by top-level module. Others: the Hugging
Face model API's `safetensors.total`.

### Qwen/Qwen3.5-9B (reference only — not used)

Revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`:

| component | parameters | share |
|---|---|---|
| text | 7,936,684,544 | 82.22% |
| lm_head | 1,017,118,720 | 10.54% |
| vision | 456,010,480 | 4.72% |
| mtp (multi-token prediction) | 243,290,624 | 2.52% |
| **full checkpoint** | **9,653,104,368** | |
| language only (text + lm_head) | 8,953,803,264 | ← the "9B" the card headlines |

The header sum equals the API's `safetensors.total` exactly. So the Milestone-1
value of 9,000,000,000 was wrong, and the "≈10B" in the brief is also imprecise:
the verified figure is **9.653B**. Architecture is
`Qwen3_5ForConditionalGeneration`, `pipeline_tag: image-text-to-text` — genuinely
multimodal, which is why `AutoModelForCausalLM` alone is unsafe.

Conservative rule applied: since we do not demonstrate that the vision tower and
MTP head are excluded from inference, the **full checkpoint** is charged.

### Bake-off metadata (verified, none downloaded except the 9B above)

| model | arch | full checkpoint | licence |
|---|---|---|---|
| `Qwen/Qwen3.5-9B` | Qwen3_5ForConditionalGeneration | 9,653,104,368 | apache-2.0 |
| `Qwen/Qwen3.5-4B` | Qwen3_5ForConditionalGeneration | 4,659,865,088 | apache-2.0 |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | Mistral3ForConditionalGeneration | 24,011,361,280 | apache-2.0 |
| `google/gemma-3-27b-it` | Gemma3ForConditionalGeneration | 27,432,406,640 | gemma |

Pairing arithmetic against the 32B limit:

| pairing | total | legal |
|---|---|---|
| Qwen 9B alone | 9,653,104,368 | yes |
| Qwen 9B + Qwen 4B | 14,312,969,456 | yes |
| **Mistral 24B + Qwen 4B** | **28,671,226,368** | **yes — FROZEN TARGET** |
| Mistral 24B + Qwen 9B | 33,664,465,648 | **no** (+1.66B over) |
| Gemma 27B + Qwen 4B | 32,092,271,728 | **no** (+92M over) |

The last row is why the audit must fail closed rather than round: it exceeds by
0.3%.

### Budget-audit hardening

`ModelSpec` now distinguishes `published_language_parameters`,
`published_checkpoint_parameters`, `budget_count_parameters`,
`parameter_source`, `parameter_source_verified`, `loaded_neural_components` and
`loaded_parameter_count`. The audit **fails closed** when: the count is missing;
the count is non-positive; provenance is not marked verified; or a budget count
sits below the full checkpoint without demonstrated evidence.

Verified output for the frozen target:

```
Parameter budget: 32B total published parameters
  - Qwen/Qwen3.5-4B [verifier] 4.660B (verified)
  - mistralai/Mistral-Small-3.2-24B-Instruct-2506 [enumerator] 24.011B (verified)
  total: 28.67B
  RESULT: PASS
```

---

## 5. Architecture implemented

| Spec module | File | Notes |
|---|---|---|
| 0/1 Compiler + Router | `contracts/` | 6 contracts, cross-checked against the evaluator's `RELATION_TYPE` |
| 2 Elicitation | `elicitation/` | 22 views; award expanded to 5 fixed facets |
| 3 Normalization + Graph | `normalization/`, `evidence/` | two-level identity; facet provenance; model-family/mode stamps |
| 4 Blind Verifier | `verification.py` | A/B/C logits, tokenisation assertion + sequence fallback, contextual calibration, 3 templates, JS disagreement, calibrated gates |
| 5 Evidence state | `scoring.py` | `S(o)` with all five components stored separately; 5-tier verification |
| 6 RCSE | `coverage.py` | search-value estimation, relation-typed, fully traceable |
| 7 Active Controller | `controller.py` | 7-action space, `A_t(a)` score, per-programme stopping, full decision logs |
| 8 Final Selector | `selection.py` | relation-specific; capacity takes the highest *valid* cluster |
| — Staged execution | `staging.py` | lossless graph persistence between phases |

**Cross-model evidence (requirement 16).** `IndependenceGroup.CROSS_MODEL_RECALL`
(second model recalls independently) is separated from `BLIND_VERIFIER` (second
model agrees with a candidate it was shown), and every edge carries
`model_family` and `EvidenceMode`. `X(o)` credits independent recall at 1.0 and
shown-candidate agreement at 0.25. Demonstrated end to end: in the synthetic
two-family run, `Alpha` (recalled by both families) scored `X = 1.00` while
`Beta` (only agreed with) scored `X = 0.25`.

**Staged execution (requirement 6).** Phase A enumerate → persist → Phase B
cross-model recall + verify → persist → Phase C decide (no model). A test
asserts the staged and interleaved paths produce the **same** prediction. The
counted budget stays 28.67B regardless of the split.

---

## 6. Files created / modified

**Created (13)**
`src/cover_kbc/{scoring,coverage,controller,staging}.py`;
`scripts/run_staged.py`;
`configs/experiments/{cover_kbc_v2_mistral24_qwen4,smoke_staged_scripted}.yaml`;
`configs/models/{qwen3.5-9b,bakeoff-candidates}.yaml`;
`notebooks/COVER_KBC_Colab.ipynb`;
`tests/test_{verification,controller,staging}.py`;
`docs/audits/0001-milestone-1-foundation.md`.

**Modified (18)**
`src/cover_kbc/`: `types.py`, `verification.py`, `selection.py`, `pipeline.py`,
`normalization/strings.py`, `normalization/__init__.py`, `elicitation/{views,library,parsing,engine}.py`,
`evidence/graph.py`, `contracts/{base,registry}.py`, `data/writer.py`,
`models/{base,budget,huggingface,offline,registry}.py`;
`tests/test_{evidence,normalization,pipeline}.py`;
`docs/IMPLEMENTATION_STATUS.md`.

**Deleted (1)** `configs/models/qwen3.5-9b-baseline.yaml` — superseded by the
verified profile.

**`benchmark/` — zero files created, modified or deleted.**

---

## 7. Corrections to Milestone 1

| # | Issue | Fix |
|---|---|---|
| 1 | Unverified 9B parameter count | Replaced with 9,653,104,368 from safetensors headers; audit now fails closed on unverified provenance |
| 2 | Evaluator provenance unverified | Verified: pinned commit **is** upstream `main`; all 11 files byte-identical |
| 3 | Parenthetical stripping merged distinct entities | Removed entirely. Identity split into `strict_key` / `alias_hint_key`; only articles fold |
| 4 | Comma parser could split `"35,000"` | Separator precedence + digit-group-comma guard; numeric relations raise if routed to entity parsing |
| 5 | Gate was a single-shot text short circuit | Calibrated gate with margin + probability thresholds; uncertain reads continue to discovery |
| 6 | `hasCapacity` picked the dominant cluster | Now picks the **highest sufficiently-supported valid** cluster; verifier-INVALID clusters excluded; unsupported high outliers rejected |
| 7 | Empty predictions were undifferentiated | Five distinct `EmptyReason` values, logged per query |

---

## 8. Commands executed

```bash
git status / branch --show-current / log --oneline -10 / diff
curl https://huggingface.co/api/models/{4 models}        # metadata only
curl .../Qwen3.5-9B/raw/main/config.json, model.safetensors.index.json
python  # HTTP range-read safetensors headers -> exact parameter breakdown
git clone --filter=blob:none https://github.com/lm-kbc/dataset2026.git   # temp, outside repo
git ls-remote / cat-file -t / log   # verify pinned commit
sha256sum   # 11-file snapshot comparison vs upstream@pin
conda create -p conda-cover python=3.12 ; pip install torch(cu124) transformers accelerate bitsandbytes
python  # AutoModelForCausalLM 4-bit load attempt -> FAILED (VRAM)
rm -rf hf-cache        # 19 GB reclaimed
python -m pytest -q
python -m pyflakes src/ tests/ scripts/
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 30
```

---

## 9. Tests

**251 passed**, 0 failed. `pyflakes` clean across `src/`, `tests/`, `scripts/`.
No test loads a heavyweight model; all use `ScriptedRuntime`, `NullRuntime`,
fake tokenizers and synthetic logits.

| file | tests | covers |
|---|---|---|
| `test_data.py` | 25 | loading, byte-for-byte round-trip, schema, serialization, dedup |
| `test_contracts.py` | 20 | routing, contract/library consistency, no factual content |
| `test_normalization.py` | 47 | `strict_key`/`alias_hint_key`, parentheticals, `"35,000"`, units, clustering, refusals, fences |
| `test_evidence.py` | 21 | independence, facets vs independence, capacity highest-valid-cluster, empty reasons |
| `test_evaluation.py` | 12 | official evaluator agreement, alias matching, tolerance, CLI parity |
| `test_pipeline.py` | 43 | runtime, budget audit (incl. 28.67B pairing), determinism, malformed output |
| `test_verification.py` | 36 | label tokenisation, calibration math, entropy/margin, JS disagreement, tiering, gates, `S(o)` components |
| `test_controller.py` | 30 | RCSE signals, residual, action scoring, decision auditability, per-programme stopping |
| `test_staging.py` | 17 | graph round-trip, stage files, three phases, staged == interleaved |

Requirement-17 coverage: parameter provenance ✅; conservative accounting ✅;
single vs multi-token labels ✅; calibration math ✅; entropy/margin ✅;
disagreement ✅; tier transitions ✅; confident vs uncertain gate ✅;
parenthetical distinctness ✅; article aliasing ✅; `"35,000"` ✅; capacity
highest-valid cluster ✅; repeated-view independence ✅; `facet_id` vs
`independence_group` ✅; score components ✅; serialization ✅; evaluator
agreement ✅.

---

## 10. Evaluation results

**No neural evaluation was executed. No metric here came from a heavyweight
model.** The two executed runs are non-neural plumbing checks:

**A. Abstain baseline** (`NullRuntime`, val, 478 queries) — macro-P 1.000,
macro-R 0.195, macro-F1 0.195. In-process harness and `benchmark/evaluate.py`
agreed exactly.

**B. Staged scripted smoke** (30 val queries, 3 phases) — completed all phases,
wrote predictions/trace/manifest/diagnostics, and scored through the official
evaluator. Controller executed `RUN_VIEW ×60`, `STOP ×30`; stopping reason
`"numeric: dominant cluster stable, dispersion low"`. Zero candidates because
the scripted backend has no script entries — expected.

Both are floors and plumbing checks. **Neither is a system result.**

---

## 11. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff HEAD --stat -- benchmark/  -> (empty)
```

SHA-256 unchanged from Milestone 1:

| file | sha256 |
|---|---|
| `evaluate.py` | `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22` |
| `data/train.jsonl` | `cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74` |
| `data/val.jsonl` | `90e4f2475e7e69caf9316ffd3b2e0bc4fe2cd428a99027f2abf08c9f88c18d02` |
| `data/test.jsonl` | `849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f` |

Stale upstream artifacts (`seriesHasNumberOfEpisodes` prompt row,
`SubjectEntityID` docstring) deliberately **not** cleaned up.

---

## 12. Challenge compliance

| Rule | Status | Evidence |
|---|---|---|
| Closed book — no web/RAG/KB at inference | PASS | no network/retrieval imports in `src/`; HF metadata calls are development-time only |
| No fine-tuning / LoRA / continued pretraining | PASS | no optimiser, `Trainer`, `peft`, or `.train()`; calibration is arithmetic on inference outputs |
| ≤ 32B published parameters | PASS | 28.67B verified, audit fails closed |
| MoE counted by total params | PASS by construction | audit consumes published totals only |
| Quantisation does not reduce the count | PASS | recorded on the spec, never consulted by the audit |
| Multi-step / agentic inference allowed | USED | deterministic active controller |
| Non-neural filtering/aggregation allowed | USED | normalisation, clustering, `S(o)`, RCSE, controller — all rule-based |
| No learned policy | PASS | no RL, no trained classifier on graph features |
| Train data not a factual lookup | PASS | `gold_lookup` defined, never called on the inference path |
| `benchmark/` immutable | PASS | §11 |

---

## 13. Unresolved issues

1. **No neural validation.** Every neural path is unexercised against real
   weights. This is the single largest open risk.
2. **Mistral/Qwen loading untested.** The multi-auto-class loader and the
   multi-token label fallback are unit-tested with fakes only. Qwen3.5-9B's
   labels A/B/C were confirmed single-token (ids 32/33/34) on the real
   tokenizer before deletion; Mistral's have **not** been checked.
3. **The official `baseline.py` does not exist upstream** (§3) — baseline
   comparison can only ever be a reconstruction.
4. **Thresholds are hand-set.** `accept_score`, `min_valid_prob`,
   `residual_stop`, `capacity_support_ratio` etc. must be calibrated on `train`
   and frozen before `val` is scored. Tuning on val and reporting that val
   number is not a measurement.
5. **Cross-model recall costs one extra generation per query.** Its value is
   unmeasured until a real run.
6. **`shown_candidate_weight = 0.25` is a judgement call**, not an estimate.

---

## 14. Intentionally deferred

- **DoLa** — experimental plugin. The `hidden_states` seam exists on the HF
  runtime; no decoding logic, and nothing depends on it.
- **Learned policies** — permanently excluded by the rules.
- **Full model bake-off** — no longer a prerequisite; the architecture is frozen
  on Mistral 24B + Qwen 4B. Metadata for all four candidates is retained.
- **Qwen3.5-9B execution** — reference metadata only.

---

## 15. Git review

Working tree at audit time: 18 modified, 13 created, 1 deleted, all outside
`benchmark/`. `outputs/` remains gitignored. No history rewritten, no unrelated
metadata added.

---

## 16. Recommended next milestone

**Milestone 3 — First neural validation on Colab.**

1. Push; open `notebooks/COVER_KBC_Colab.ipynb` on a GPU runtime.
2. Verify benchmark integrity and the 28.67B audit in-notebook.
3. Confirm Mistral label tokenisation (single vs multi-token A/B/C) before
   trusting any verifier output.
4. Smoke run `--limit 20` through all three phases.
5. Calibrate thresholds on `train`; **freeze**; then score `val`.
6. Run the ablation ladder: direct single-view → fixed multi-view → +blind
   verification → +contextual calibration → +cross-model → full COVER-Core with
   the active controller.
7. Reconstruct the official baseline for comparison, documenting that upstream
   ships no `baseline.py`.

Only after real validation should DoLa or further architectural work be
considered.
