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

**Milestone 1 complete** — reproducible benchmark foundation and core typed
interfaces. Milestones 2 (COVER-Core) and 3 (active control) are pending; the
advanced inference modules exist as declared interfaces that raise rather than
approximate. See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
for what is implemented, the key design decisions, and open issues.

## Quickstart

```bash
pip install -e '.[dev]'          # add '.[hf]' for the neural backend

python -m pytest -q

# End-to-end plumbing run (non-neural abstain baseline; not a system result)
python scripts/run_cover.py --config configs/experiments/smoke_abstain.yaml

# Score any prediction file with the official evaluator
python scripts/evaluate_local.py -p outputs/<run>/predictions.jsonl -s val --cli

# Check a model profile against the 32B budget (downloads nothing)
python scripts/audit_model_budget.py configs/models/qwen3.5-9b-baseline.yaml
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
docs/               implementation status
scripts/            run_cover.py, evaluate_local.py, audit_model_budget.py
src/cover_kbc/
  contracts/        relation contracts + typed program router   (Module 0/1)
  elicitation/      view library, prompt rendering, parsing     (Module 2)
  evidence/         candidate–facet evidence graph              (Module 3)
  verification.py   blind three-way verifier (interface)        (Module 4)
  selection.py      evaluator-aware final selector              (Module 8)
  data/             read-only dataset access, official-format output
  evaluation/       wrappers around the official evaluator
  models/           model-agnostic runtime + 32B budget audit
  runtime/          run manifests and call tracing
  pipeline.py       fixed-budget orchestrator
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
