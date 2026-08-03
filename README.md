# FactElicit-AKBC — COVER-KBC v2

System for the **AKBC Shared Task 2026** (@ EMNLP 2026), *Predicting complete
knowledge base entries from language models*.

Given a `SubjectEntity` + `Relation`, predict the complete object set
`[o1, …, ok]`, where `k` may be 0, 1 or many, across six relations:
`countryLandBordersCountry`, `personHasCityOfDeath`, `hasCapacity`,
`awardWonBy`, `companyTradesAtStockExchange`, `hasArea`.

**COVER-KBC v2** treats this as *relation-typed active set elicitation*: each
relation compiles to a typed inference program that discovers candidates through
structurally diverse views, tracks independent evidence per atomic candidate,
verifies uncertain candidates with calibrated logits, and allocates further
test-time compute only while the expected coverage gain justifies it.

```
Contract → Typed Program → Elicit → Graph → Verify → RCSE → Act/Stop → Final Set
```

The full design is in [`COVER_KBC_V2_ARCHITECTURE_SPEC.pdf`](COVER_KBC_V2_ARCHITECTURE_SPEC.pdf).

## Status

**Milestone 2 complete** — the full architecture is implemented through the
control layer: relation contracts, typed programs, diverse elicitation with
relation-specific facets, the candidate-facet evidence graph, a logit-calibrated
blind verifier with contextual calibration and prompt-distribution disagreement,
candidate scoring, cross-model evidence, RCSE, the active controller, adaptive
stopping, and relation-specific final selection.

**No neural result exists yet.** Heavyweight inference runs on Google Colab, not
on the development machine; every metric currently in the repository comes from
a non-neural plumbing check and is labelled as such. See
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) and
[`docs/audits/`](docs/audits/).

## Target architecture

| role | model | published params |
|---|---|---|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | 4,659,865,088 |
| **total** | | **28,671,226,368** (28.67B ≤ 32B) |

Execution is staged, so a GPU need not hold both models at once:
`enumerate` (Mistral) → persist → `verify` (Qwen) → persist → `decide` (no
model). The counted budget is unchanged by the split.

## Quickstart

```bash
pip install -e '.[dev]'          # add '.[hf]' for the neural backends

python -m pytest -q              # 251 tests, no model required

# Check the 32B budget (downloads nothing, fails closed)
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml

# Non-neural plumbing runs (NOT system results)
python scripts/run_cover.py  --config configs/experiments/smoke_abstain.yaml
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 30

# Score any prediction file with the official evaluator
python scripts/evaluate_local.py -p outputs/<run>/predictions.jsonl -s val --cli
```

**Neural runs happen on Colab** via
[`notebooks/COVER_KBC_Colab.ipynb`](notebooks/COVER_KBC_Colab.ipynb), which
drives the same three phases:

```bash
python scripts/run_staged.py enumerate --config C --split val   # enumerator only
python scripts/run_staged.py verify    --config C --run-dir D   # verifier only
python scripts/run_staged.py decide    --config C --run-dir D   # no model at all
```

Each run writes `outputs/<run_id>/` containing `predictions.jsonl`,
`metrics.json`, `trace.jsonl` (per-query candidates and evidence),
`calls.jsonl` (one record per model call) and `manifest.json` (config hash,
model identity and parameter count, seed, dataset and evaluator checksums, git
revision, token/call totals).

## Layout

```
benchmark/          official snapshot — READ ONLY, never modified in place
configs/
  experiments/      run configurations
  models/           model profiles with published parameter counts
docs/               implementation status + audits/
notebooks/          Colab execution entrypoint
scripts/            run_staged.py, run_cover.py, evaluate_local.py, audit_model_budget.py
src/cover_kbc/
  contracts/        relation contracts + typed program router   (Module 0/1)
  elicitation/      view library, prompt rendering, parsing     (Module 2)
  evidence/         candidate-facet evidence graph              (Module 3)
  verification.py   blind verifier, calibration, disagreement   (Module 4)
  scoring.py        S(o) components + verification tiering      (Module 5)
  coverage.py       residual coverage & saturation (RCSE)       (Module 6)
  controller.py     active controller + adaptive stopping       (Module 7)
  selection.py      relation-specific final selector            (Module 8)
  staging.py        enumerate/verify/decide phase persistence
  data/             read-only dataset access, official-format output
  evaluation/       wrappers around the official evaluator
  models/           model-agnostic runtime + 32B budget audit
  runtime/          run manifests and call tracing
  pipeline.py       orchestrator (staged or interleaved)
tests/
outputs/            generated artifacts (gitignored)
```

## Competition constraints

Closed book. No web search, RAG, external factual corpora or KB lookup on the
prediction path. No fine-tuning, LoRA, continued pretraining or instruction
tuning. Total inference-time neural parameters ≤ 32B, counted from *published*
totals — quantization does not reduce the count, and MoE models count by total
rather than active parameters. Multi-step inference and non-neural filtering,
normalization, deduplication, aggregation and scheduling are allowed.

`benchmark/` is a pinned upstream snapshot ([lm-kbc/dataset2026](https://github.com/lm-kbc/dataset2026),
Apache-2.0). It is treated as an immutable dependency: our code wraps the
official evaluator and data rather than editing them. All project code lives
outside it.

Train data may be used for task understanding, few-shot demonstrations, prompt
design, non-neural threshold calibration and error analysis — never for weight
updates, and never as a factual lookup table on the inference path.
