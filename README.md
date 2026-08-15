# COVER-KBC

<div align="center">

### Relation-Typed Closed-Book Knowledge Base Construction for AKBC Shared Task 2026

Evidence-centric inference for object-set prediction with one frozen open-weight language model.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Task](https://img.shields.io/badge/AKBC%20Shared%20Task-2026-red)
![Parameters](https://img.shields.io/badge/Counted%20Params-24.01B%20%3C%2032B-brightgreen)
![Runtime](https://img.shields.io/badge/Runtime-Closed--Book-success)
![Training](https://img.shields.io/badge/Training-None%20(Pretrained%20Only)-informational)
![Score](https://img.shields.io/badge/Hidden%20TEST%20Macro--F1-0.5878-orange)

</div>

## What COVER-KBC is

COVER-KBC is a closed-book knowledge-base construction system. Given an official
row with a `SubjectEntity` and a `Relation`, it predicts the string-valued
`ObjectEntities` set for that query. The answer may be empty, singleton, or
multi-valued, and numeric relations are evaluated with the challenge's 5%
relative tolerance.

The current public repository is trimmed around the frozen **Profile F1 - Stock
Empty Rescue** system. It uses one physical checkpoint,
`mistralai/Mistral-Small-3.2-24B-Instruct-2506`, reused for every logical
generation and verification role. No external retrieval, no web access, no
additional model training, and no fine-tuning are used.

The method treats closed-book KBC as active evidence acquisition. A model answer
is not emitted directly as a final object set. Each call is parsed as evidence,
attached to a relation-specific inference state, verified when needed, and then
finalized by a relation-aware selector.

## Competition and runtime constraints

This repository targets the AKBC Shared Task 2026 closed-book object-set
prediction setting. The active system follows these constraints:

| Constraint | COVER-KBC Profile F1 |
|---|---|
| Neural parameter budget | 24,011,361,280 unique parameters, under the 32B cap |
| Model portfolio | one frozen Mistral-Small-3.2-24B checkpoint |
| External factual retrieval | none |
| Training / fine-tuning | none |
| Inference style | multi-step closed-book prompting, parsing, verification, and control |
| Main runtime used | Google Colab A100 |

Quantization is treated only as a memory/runtime choice. It does not reduce the
counted parameter total.

## Final architecture

A single public profile is kept. Historical variants and rejected probes were
removed from the public surface.

```text
SubjectEntity + Relation
  |
  |-- relation contract and typed program
  |-- closed-book Mistral evidence-acquisition views
  |-- strict parsing and normalization
  |-- independence-aware evidence state
  |-- blind candidate verification
  |-- residual search-need estimate
  |-- deterministic budget-aware controller
  |-- relation-specific final selector
  |
  `-- Profile F1 final relation layer
        |-- award metadata cleanup
        |-- city-of-death two-stage protocol
        |-- area multi-view numeric resolver
        |-- capacity multi-view numeric resolver
        |-- stock empty-row rescue
        `-- official JSONL prediction row
```

Profile F1 final layer:

| Relation | Final strategy |
|---|---|
| `countryLandBordersCountry` | core small-set border inference; no final repair |
| `companyTradesAtStockExchange` | core stock path plus empty-only stock-exchange rescue |
| `personHasCityOfDeath` | two-stage deceased gate followed by strict city recall |
| `hasArea` | four-view numeric resolver with 5% clustering |
| `hasCapacity` | four-view numeric resolver with 5% clustering |
| `awardWonBy` | large-set enumeration plus deterministic metadata cleanup and deduplication |

## Design invariants

| Invariant | Mechanism |
|---|---|
| One counted model | All logical roles share the same frozen Mistral checkpoint; retired Qwen profiles are not active. |
| Closed-book inference | The pipeline never calls web search, RAG, external factual corpora, or a knowledge-base lookup at inference time. |
| Evidence before output | Generated strings become parsed observations first; final objects come only from selectors over evidence state. |
| No fake consensus | Repeated samples from the same view do not count as independent corroboration. |
| Blind verification | The verifier sees the subject, relation definition, hard-negative rules, and one candidate, not the generator prompt or support count. |
| Numeric fail-closed behavior | Numeric answers must parse strictly, cluster within 5% tolerance, or pass a source-blind judge. |
| Bounded stock rescue | Stock rescue runs only on empty stock rows and emits an exchange only with sufficient independent support. |

## Output format

Predictions use the official JSONL row shape:

```json
{
  "SubjectEntity": "France",
  "Relation": "countryLandBordersCountry",
  "ObjectEntities": ["Belgium", "Germany", "Italy", "Luxembourg", "Monaco", "Spain", "Switzerland"]
}
```

`ObjectEntities` is always a list. Empty answers are represented by
`"ObjectEntities": []`. Numeric answers are emitted as strings accepted by the
official evaluator.

## Deployed model

| Model | Role | Revision | Counted parameters |
|---|---|---|---:|
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | generation, verification, relation-specific final calls | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` | 24,011,361,280 |
| | **TOTAL** | | **24,011,361,280 / 32,000,000,000** |

`scripts/audit_model_budget.py` verifies the declared profile and exits
non-zero if the active model portfolio exceeds the budget or names an
unexpected model.

## Results

Profile F1 hidden TEST scores:

| Relation | Macro Precision | Macro Recall | Macro F1 |
|---|---:|---:|---:|
| `awardWonBy` | 0.3255 | 0.3707 | 0.3105 |
| `companyTradesAtStockExchange` | 0.8992 | 0.7963 | 0.7385 |
| `countryLandBordersCountry` | 0.9712 | 0.9295 | 0.9291 |
| `hasArea` | 0.6700 | 0.6700 | 0.6700 |
| `hasCapacity` | 0.2449 | 0.1633 | 0.1633 |
| `personHasCityOfDeath` | 0.9600 | 0.5900 | 0.5700 |
| **All Relations** | **0.7268** | **0.6055** | **0.5878** |

Zero-object cases: precision `0.6038`, recall `0.9412`, F1 `0.7356`.

These hidden TEST numbers are leaderboard-reported. The hidden labels are not
included in this repository, so hidden TEST metrics cannot be recomputed
locally.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[hf]"    # neural runtime only
```

Python 3.10+ is required. CPU-only machines can run budget, packaging, and smoke
tests. Full neural inference needs a CUDA runtime capable of loading the
Mistral-Small-3.2-24B checkpoint; the reported runs used Colab A100.

## Required assets

| Asset | Tracked here | Notes |
|---|:---:|---|
| Official benchmark snapshot | yes | `benchmark/` |
| F1 profile config | yes | `configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml` |
| TRAIN-derived calibration artifacts | yes | `configs/calibration/v3/` |
| Mistral model weights | no | downloaded or mounted through Hugging Face tooling |
| Hidden TEST gold labels | no | unavailable by task design |
| Profile E3 baseline prediction artifact | no | required only for exact artifact-seeded F1 promotion |

## Usage

Budget and public smoke checks:

```bash
python scripts/audit_model_budget.py \
  configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml

python -m pytest -q
```

Full pipeline entrypoint:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml \
  --split test \
  --output-dir outputs/profile_f1_full_run \
  --no-eval
```

Leaderboard-tested F1 promotion path:

```bash
python scripts/run_stock_empty_rescue.py \
  --config configs/experiments/cover_kbc_v3_8_profile_f1_stock_empty_rescue_test.yaml \
  --baseline-predictions /path/to/profile_e3_best_predictions.jsonl \
  --split test \
  --output-dir outputs/f1_stock_empty_rescue

python scripts/merge_targeted_relation_results.py \
  --baseline-predictions /path/to/profile_e3_best_predictions.jsonl \
  --targeted-results outputs/f1_stock_empty_rescue/stock_empty_rescue_results.jsonl \
  --relation companyTradesAtStockExchange \
  --expected-targeted-rows 100 \
  --output outputs/profile_f1_stock_empty_rescue_475.jsonl

python scripts/package_submission.py \
  --predictions outputs/profile_f1_stock_empty_rescue_475.jsonl \
  --input benchmark/data/test.jsonl \
  --split test \
  --out outputs/profile_f1_stock_empty_rescue_submission.zip
```

Local validation on a labelled split:

```bash
python scripts/evaluate_local.py \
  -p outputs/<run>/predictions.jsonl \
  -s val \
  --cli
```

## Produced artifacts

| File | Contents |
|---|---|
| `predictions.jsonl` | official prediction rows |
| `trace.jsonl` | per-query inference trace |
| `calls.jsonl` | model-call accounting and raw call records |
| `manifest.json` | config, dataset, model, and run metadata |
| `repair_accounting.json` | Profile F1 final-layer mutation accounting, when enabled |
| `*.zip` | packaged submission archive |

Generated artifacts are ignored by git under `outputs/`, `predictions/`, and
`runs/`.

## Reproducibility

The public profile is identified by the committed YAML config, the model
revision, the benchmark snapshot, and the run manifest produced at inference
time. Generation is greedy throughout, so repeated runs with the same runtime
and artifacts avoid sampling variance.

There are two valid reproduction modes:

| Mode | What it does | Caveat |
|---|---|---|
| Full runner | recomputes every relation from the F1 config | does not locally score hidden TEST |
| Artifact-seeded F1 promotion | starts from the Profile E3 hidden TEST artifact and changes eligible empty stock rows only | requires the historical E3 prediction file |

The leaderboard-reported F1 score belongs to the artifact-seeded promotion path.

## Repository structure

```text
benchmark/                 official local benchmark snapshot
configs/experiments/       one public profile: Profile F1
configs/calibration/v3/    calibration artifacts used by the F1 config
docs/                      public implementation status
notebooks/                 Profile F1 Colab notebook
scripts/run_cover.py       full pipeline runner
scripts/run_stock_empty_rescue.py
scripts/merge_targeted_relation_results.py
scripts/package_submission.py
scripts/audit_model_budget.py
src/cover_kbc/             COVER-KBC package
tests/                     public smoke tests
```

Development audits are intentionally ignored by git. They may be kept locally
under `docs/audits/`, but they are not part of the public GitHub repository.

## Development

```bash
python -m pytest -q
git diff --check
```

The public smoke suite is zero-model: it checks the F1 config, model-budget
accounting, stock rescue invariants, and evaluator behavior without downloading
or loading neural weights.

## Evidence and limitations

- Hidden TEST scores are leaderboard-reported and cannot be recomputed from this
  repository because hidden labels are unavailable.
- Exact reproduction of the submitted F1 artifact requires the Profile E3
  baseline prediction file, which is not committed.
- `hasCapacity` and `awardWonBy` remain the weakest relations; the current
  system favors precision and strict parsing over broad speculative recall.
- The system is closed-book, so all factual evidence comes from the frozen
  model's parametric knowledge. It cannot recover facts that the checkpoint does
  not expose through the configured views.
- Historical experimental branches, broad diagnostics, and development audit
  notes were removed from the public surface to keep this repository focused on
  the final system.

## Security and compliance

- No credentials or API keys are required.
- Model weights, generated outputs, local caches, and local audits are ignored.
- Inference uses no web calls, external factual retrieval, RAG, or answer lookup
  tables.
- The active model revision is pinned, and parameter accounting is checked
  before runtime.

## Acknowledgements

COVER-KBC was built for the AKBC Shared Task 2026 closed-book KBC setting and
uses the local challenge benchmark snapshot under `benchmark/`. The system uses
the open-weight Mistral-Small-3.2-24B-Instruct checkpoint as its only neural
model.
