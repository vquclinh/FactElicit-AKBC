# Audit 0001 — Milestone 1: Reproducible Benchmark Foundation + COVER Core Interfaces

**Status:** accepted with corrections (see §11)
**Commit audited:** `3037c1f` — *feat: establish COVER-KBC benchmark foundation*
**Audit written:** retrospectively, at the start of Milestone 2.

> **Provenance note.** This audit was not produced during Milestone 1; no
> `docs/audits/` directory existed at commit `3037c1f`. It was reconstructed at
> the start of Milestone 2 strictly from the committed repository state, the
> Milestone 1 implementation record in `docs/IMPLEMENTATION_STATUS.md`, and
> re-execution of the Milestone 1 test suite and smoke run. Nothing here is
> claimed that was not re-verified against the tree at `3037c1f`.

---

## 1. Objective and scope

Build a reproducible foundation for the AKBC Shared Task 2026 system, per
`COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` §25.1:

- read-only data/evaluation wrappers around the official benchmark;
- first-class relation contracts for all six relations;
- core typed interfaces with mandatory provenance;
- a model-agnostic runtime abstraction plus the 32B budget audit;
- a fixed initial elicitation layer with four named view families;
- candidate normalization and an independence-aware evidence graph skeleton;
- run manifests and call-level tracing.

Explicitly **out of scope** for Milestone 1: logit calibration, prompt
disagreement, DoLa, cross-model verification, RCSE, active scheduling, adaptive
stopping, learned policies.

---

## 2. Pre-work git state

```
branch : main
HEAD   : 1af8910  add proposal
tree   : clean
```

Prior history: `b607ae1` (official benchmark snapshot), `9f856e0` (initial commit).

---

## 3. Architecture sections implemented

| Spec section | Module | Status |
|---|---|---|
| §5 Relation Compiler | `contracts/base.py`, `contracts/registry.py` | implemented |
| §6 Typed Program Router | `contracts/router.py` | implemented |
| §7 Diverse Elicitation Engine | `elicitation/` | implemented (fixed, 4 families) |
| §9 Atomic Normalization + Evidence Graph | `normalization/`, `evidence/graph.py` | implemented |
| §10 Blind Verifier | `verification.py` | **interface only**; calibration raises |
| §11 Evidence/Uncertainty State | `types.py` (`EvidenceGroup`, `coverage`) | partial — accounting only |
| §12 RCSE | — | deferred (Milestone 3) |
| §13 Active Controller | — | deferred (Milestone 3) |
| §14 Final Selector | `selection.py` | implemented (v1 rule) |
| §21 Runtime + logit access | `models/base.py`, `models/huggingface.py` | implemented, unexercised |
| §23 Software interfaces | `types.py` | implemented |
| §24 Repository layout | — | adopted |

---

## 4. Files created / modified

49 files, 6,623 LOC across `src/`, `tests/`, `scripts/`.

- `src/cover_kbc/`: `types.py`, `paths.py`, `pipeline.py`, `selection.py`,
  `verification.py`, and packages `contracts/`, `data/`, `evaluation/`,
  `models/`, `elicitation/`, `evidence/`, `normalization/`, `runtime/`.
- `scripts/`: `run_cover.py`, `evaluate_local.py`, `audit_model_budget.py`,
  `_bootstrap.py`.
- `configs/`: `experiments/smoke_abstain.yaml`, `models/offline-null.yaml`,
  `models/qwen3.5-9b-baseline.yaml`.
- `tests/`: `conftest.py`, `test_data.py`, `test_contracts.py`,
  `test_normalization.py`, `test_evidence.py`, `test_evaluation.py`,
  `test_pipeline.py`.
- `docs/IMPLEMENTATION_STATUS.md`, `README.md`, `pyproject.toml`.

`benchmark/` — **no files created, modified or deleted.**

---

## 5. Model parameter sources and budget counts

**None verified in Milestone 1.** `configs/models/qwen3.5-9b-baseline.yaml`
carried `published_total_parameters: 9_000_000_000`, transcribed from the
checkpoint *name* in `benchmark/configs/baseline-qwen-3.5-9b.yaml`. Spec §2.2
explicitly rejects inferring a count from a marketing suffix. The profile was
labelled UNVERIFIED in-file and in `IMPLEMENTATION_STATUS.md` §5.2.

This is corrected in Milestone 2 (audit 0002 §4).

---

## 6. Runtime / hardware

No neural inference was performed. `torch` / `transformers` were not installed.
All Milestone 1 execution was pure-Python on CPU.

---

## 7. Commands executed

```bash
python3 -m pip install --user pandas          # required by benchmark/evaluate.py
python3 -m pytest -q
python3 scripts/run_cover.py --config configs/experiments/smoke_abstain.yaml
python3 benchmark/evaluate.py -p outputs/<run>/predictions.jsonl -g benchmark/data/val.jsonl
python3 scripts/audit_model_budget.py configs/models/offline-null.yaml configs/models/qwen3.5-9b-baseline.yaml
```

---

## 8. Tests and results

**153 passed**, 0 failed.

Coverage by requirement:

| Requirement | Test file |
|---|---|
| dataset loading, byte-for-byte round-trip, order | `test_data.py` |
| relation routing | `test_contracts.py` |
| numeric normalization | `test_normalization.py` |
| string normalization | `test_normalization.py` |
| prediction serialization | `test_data.py` |
| evidence grouping / independence | `test_evidence.py` |
| duplicate handling | `test_data.py`, `test_evidence.py` |
| empty-set handling | `test_evidence.py`, `test_evaluation.py` |
| official evaluator invocation | `test_evaluation.py` |
| zero/one/multi-object, numeric, alias-dup, malformed | across all |

---

## 9. Evaluation results

**No neural evaluation was executed.** The only run was the non-neural abstain
baseline (`NullRuntime`, val, 478 queries):

| relation | macro-p | macro-r | macro-f1 | #empty |
|---|---|---|---|---|
| awardWonBy | 1.000 | 0.000 | 0.000 | 10 |
| companyTradesAtStockExchange | 1.000 | 0.360 | 0.360 | 100 |
| countryLandBordersCountry | 1.000 | 0.265 | 0.265 | 68 |
| hasArea | 1.000 | 0.000 | 0.000 | 100 |
| hasCapacity | 1.000 | 0.000 | 0.000 | 100 |
| personHasCityOfDeath | 1.000 | 0.390 | 0.390 | 100 |
| **All Relations** | **1.000** | **0.195** | **0.195** | 478 |

The in-process harness and `python benchmark/evaluate.py` agreed exactly.

> This is a plumbing floor, **not a system result**. The backend abstains on
> every call and holds no factual knowledge. Precision is 1.0 only because the
> evaluator defines an empty prediction as precise; recall equals the fraction
> of val rows with genuinely empty gold.

---

## 10. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff HEAD --stat -- benchmark/  -> (empty)
```

Verified clean at the time of commit and re-verified at the start of
Milestone 2.

Evaluator SHA-256: `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`

Data SHA-256:
- `train.jsonl` `cb344aa3f153b30f4179f3c912ccfca19ae4e71288993292a093585d068a2c74`
- `val.jsonl` `90e4f2475e7e69caf9316ffd3b2e0bc4fe2cd428a99027f2abf08c9f88c18d02`
- `test.jsonl` `849f565d6fcf53f60b74e53503d1ac119933e823f191030b34befe0df044fc1f`

---

## 11. Challenge compliance

| Rule | Status | Evidence |
|---|---|---|
| Closed book, no web/RAG/KB at inference | PASS | no network/retrieval imports in `src/`, `scripts/` |
| No fine-tuning / LoRA / continued pretraining | PASS | no optimizer, `Trainer`, `peft`, or `.train()` call |
| ≤ 32B published parameters | INCOMPLETE | audit implemented; count unverified |
| MoE by total params | PASS by construction | audit consumes published totals only |
| Quantization does not reduce count | PASS | `quantization` recorded, never consulted |
| Train data not a factual lookup | PASS | `gold_lookup` defined, never called on inference path |
| `benchmark/` immutable | PASS | see §10 |

---

## 12. Unresolved issues carried into Milestone 2

1. **Evaluator provenance unverified** — pinned commit not checked against
   upstream (no network access attempted). *Resolved in audit 0002 §3.*
2. **Unverified Qwen parameter count.** *Resolved in audit 0002 §4.*
3. **No neural result; `HuggingFaceRuntime` unexercised.**
4. **Upstream artifacts left as-is** — stale `seriesHasNumberOfEpisodes` prompt
   row and `SubjectEntityID` docstring. Intentional; snapshot is read-only.
5. **`hasCapacity` "highest published capacity" not enforceable** — selector
   picks the dominant cluster, not the highest valid one.
6. **Over-aggressive deduplication** — the canonical key globally strips
   parenthetical qualifiers, which can merge genuinely distinct entities.
   *Identified by external review; resolved in audit 0002 §5.*
7. **Comma-list parsing not audited against real model output** — risk that
   `"35,000"` splits into `"35"`/`"000"`. *Resolved in audit 0002 §6.*

---

## 13. Deferred to Milestone 3 (unchanged)

RCSE residual coverage; active facet scheduler; adaptive stopping; DoLa
intermediate-layer decoding; full cross-model controller; learned policies.

---

## 14. Recommended next milestone

Milestone 2 — Neural Baseline Activation + Logit-Calibrated COVER-Core:
resolve model-budget metadata from primary sources, verify upstream provenance,
harden deduplication and parsing, activate a real neural backend, and implement
the calibrated blind verifier, tiering, candidate score and relation-specific
selection.
