# Milestone 1 — Reproducible Benchmark Foundation + COVER Core Interfaces

Status as of this commit. Scope follows `COVER_KBC_V2_ARCHITECTURE_SPEC.pdf`
§25.1, with the official task definition and evaluator treated as the source of
truth wherever the two could be read differently.

---

## 1. What was implemented

### Data and evaluation layer (`src/cover_kbc/data/`, `src/cover_kbc/evaluation/`)

- Read-only loaders for `train` / `val` / `test`, preserving official row order.
  Round-tripping a split reproduces the upstream JSONL byte-for-byte (asserted
  in tests for all three splits).
- Schema validation for gold rows (`list[list[str]]` alias lists, with the
  evaluator's legacy flat `list[str]` form wrapped on read and flagged) and for
  prediction rows (exactly the three official fields, flat `list[str]`).
- Duplicate `(SubjectEntity, Relation)` detection — the evaluator keys on that
  pair, so a duplicate would silently discard a row.
- Prediction writer that enforces one row per input query, in input order, and
  refuses to write a file with missing, extra or reordered rows.
- The official evaluator is **loaded from the snapshot by file path and
  executed**, never reimplemented. `EvaluationReport` adds only bookkeeping the
  official scorer does not provide (missing/extra prediction rows, evaluator
  checksum). A subprocess path runs `benchmark/evaluate.py` exactly as a
  participant would; a test asserts the two agree numerically.

### Relation contracts (`src/cover_kbc/contracts/`)

All six relations are compiled into a `RelationContract` carrying: program
type, output type, cardinality, prose definition, positive rules, hard-negative
/ near-miss rules, mandatory and optional views, normalization policy,
verification policy, stopping policy (placeholder fields) and selection policy.

| Relation | Program | Output | Cardinality |
|---|---|---|---|
| `countryLandBordersCountry` | `SMALL_SET` | entity | zero-or-many (small) |
| `companyTradesAtStockExchange` | `SMALL_SET` | entity | zero-or-many (small) |
| `personHasCityOfDeath` | `NULL_SINGLE` | entity | zero-or-one |
| `hasArea` | `NUMERIC` | number (km²) | exactly one |
| `hasCapacity` | `NUMERIC` | number (persons) | exactly one |
| `awardWonBy` | `LARGE_OPEN_SET` | entity | zero-or-many (large) |

`check_router_consistency()` cross-checks every contract against the spec's
routing table **and** the official evaluator's own `RELATION_TYPE` map, so a
snapshot update that reclassified a relation fails loudly instead of silently
producing wrong output.

### Core typed interfaces (`src/cover_kbc/types.py`)

`Query`, `Candidate`, `GenerationRecord`, `Evidence`, `EvidenceGroup`,
`VerificationResult`, `RelationContract`, `ProgramState`, `Budget`,
`Prediction`. A `Candidate` cannot exist without provenance: it carries the
view, run, model, raw output, normalized value and token/latency metadata of
every generation that produced it.

### Model runtime (`src/cover_kbc/models/`)

`LMRuntime` protocol with two modes — `generate` (text + token accounting +
optional logprobs) and `score_labels` (next-token logits restricted to a fixed
label set). Backends: `NullRuntime` and `ScriptedRuntime` (non-neural,
dependency-free) and an optional lazily-imported `HuggingFaceRuntime`.
Hidden-state access is declared and raises `HiddenStatesUnavailable`, so the
Milestone 3 DoLa branch has a seam without the architecture depending on it.

The 32B budget audit runs from configuration alone, downloads nothing, counts a
shared checkpoint once, ignores quantization, and **fails on an unrecorded
parameter count** rather than guessing from a model name.

### Elicitation (`src/cover_kbc/elicitation/`)

Four view families — `direct`, `structural`, `contrastive`, `missingness` — with
relation-aware templates driven entirely by contracts. 17 views total; a
consistency check enforces a strict 1:1 correspondence between contract-declared
views and implemented templates, in both directions.

Parsing handles semicolon/newline/comma lists, numbered and bulleted lists, JSON
arrays, label prefixes, markdown code fences, refusals and abstentions. It never
raises on malformed input — it returns no candidates.

### Evidence graph (`src/cover_kbc/evidence/`)

Signed `SUPPORT` / `CONTRADICT` / `UNKNOWN` edges, each carrying its
independence group, view, run, model, record id and cost.

### Reproducibility (`src/cover_kbc/runtime/`)

Every run writes `manifest.json` (config + config hash, model identity and
parameter count, seed, dataset path and SHA-256, evaluator SHA-256, git
revision, call/token totals, budget audit, evaluation result),
`calls.jsonl` (one record per model call, with prompt hash and raw output),
`trace.jsonl` (per-query candidates and evidence), `predictions.jsonl` and
`metrics.json`.

---

## 2. Key architectural decisions

**The official evaluator is executed, never reimplemented.** Loaded by file path
from the unmodified snapshot and checksummed into every manifest. Refreshing the
snapshot automatically changes our numbers.

**Independence is a first-class concept, and repetition is not evidence.** Every
view declares an `IndependenceGroup`; the mapping from view family to group is
fixed and cannot be overridden per view. Three runs of `borders_direct` produce
`independent_support == 1` and `raw_support_count == 3`. Only structurally
different views raise independent support. This is asserted directly in
`tests/test_evidence.py`.

**Internal deduplication is deliberately stricter than the evaluator's.** The
canonical key is built *on top of* the evaluator's own `normalize_string`, then
additionally folds leading articles and parenthetical qualifiers. Rationale: the
evaluator collapses only exact normalized matches, and its bipartite matcher
lets one gold entity absorb only one prediction — so "The Alpha Exchange" and
"Alpha Exchange" would reach it as two predictions and the second is a
guaranteed false positive. Folding is **key-only**; emitted strings are always
original model surface forms, never rewritten.

**Numeric output is a bare numeral, by construction.** The evaluator's
`try_parse_number` is `float(value.replace(",", "").strip())`, so `"5556 km²"`
parses to `None` — it can never be a true positive while still counting against
precision. A test asserts every value `format_numeric` produces survives the
official parser. Selection uses median-of-dominant-cluster, not token
likelihood.

**Empty is a real answer.** The official baseline emits no empty predictions and
loses F1 for it; ~19% of val rows have empty gold. A negative existence gate
short-circuits discovery and returns `[]` with `stopped_reason="gate_negative"`.

**Unimplemented advanced logic raises rather than approximating.**
`verification.calibrate()` and `verification.prompt_disagreement()` raise
`NotImplementedError`. Uncalibrated verifier output is marked `calibrated=False`
so nothing downstream can mistake it for a probability of correctness.

**No relation-specific `if/elif` chains leak.** Relation behaviour lives in
contracts and the view library; the pipeline, graph and selector are
relation-agnostic (spec invariant 8).

---

## 3. How to run

```bash
pip install -e '.[dev]'          # add '.[hf]' for the neural backend

python -m pytest -q                                              # tests
python scripts/run_cover.py --config configs/experiments/smoke_abstain.yaml
python scripts/evaluate_local.py -p <predictions.jsonl> -s val --cli
python scripts/audit_model_budget.py configs/models/qwen3.5-9b-baseline.yaml
```

`--limit N` restricts a run to the first N queries; `--split` overrides the
config.

### Current results

`pytest`: **153 passed**.

Smoke run — abstain baseline (`NullRuntime`, val, 478 queries). Our in-process
harness and `python benchmark/evaluate.py` agree exactly:

| relation | macro-p | macro-r | macro-f1 | #empty |
|---|---|---|---|---|
| awardWonBy | 1.000 | 0.000 | 0.000 | 10 |
| companyTradesAtStockExchange | 1.000 | 0.360 | 0.360 | 100 |
| countryLandBordersCountry | 1.000 | 0.265 | 0.265 | 68 |
| hasArea | 1.000 | 0.000 | 0.000 | 100 |
| hasCapacity | 1.000 | 0.000 | 0.000 | 100 |
| personHasCityOfDeath | 1.000 | 0.390 | 0.390 | 100 |
| **All Relations** | **1.000** | **0.195** | **0.195** | 478 |

> This is a **plumbing check and a floor, not a system result.** The backend
> abstains on every call and contains no factual knowledge. Precision is 1.0
> because the evaluator defines an empty prediction as precise; recall is the
> fraction of val rows whose gold set is genuinely empty. It must never be
> reported as a COVER-KBC result.

---

## 4. What remains for COVER-Core (Milestone 2)

- Contextual calibration of verifier logits against a content-free control.
- Verification tiering (auto-accept / verifier / adversarial / auto-reject).
- Candidate score `S(o) = αF + βL + γX − δC − ηU`, replacing the current
  independent-support-and-contradiction rule.
- Multi-template prompt disagreement `U_prompt(o)`.
- Award facet expansion beyond the single `award_facet` view.
- Model bake-off across Qwen / Mistral Small / Gemma profiles, each gated on the
  budget audit passing first.

Milestone 3: RCSE residual coverage, active controller, adaptive stopping, DoLa
and cross-model branches behind feature flags.

---

## 5. Unresolved compliance and correctness issues

**1. Evaluator provenance is consistent with the July 2026 update but not
independently verified.** `benchmark/UPSTREAM_COMMIT.txt` pins
`30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57`. The snapshot's `evaluate.py`
contains the features the spec attributes to that update — alias-list gold,
maximum-cardinality bipartite matching, normalized prediction dedup, and the
apostrophe/symbol normalization pass. It was **not** verified against the
upstream repository, because doing so requires network access. Confirming the
commit hash resolves to the current upstream revision is an open item.
`evaluator_checksum()` (`2d592ae177c7b230…`) is recorded in every manifest so a
future change is detectable.

**2. `configs/models/qwen3.5-9b-baseline.yaml` carries an UNVERIFIED parameter
count.** The 9B figure is transcribed from the checkpoint name in
`benchmark/configs/baseline-qwen-3.5-9b.yaml`. Spec §2.2 explicitly says a
marketing suffix is not evidence. The profile is marked unverified in-file and
must be checked against the published model card, with the URL recorded in
`source:`, before it is used for any reported result. No model has been
downloaded or run.

**3. No neural result exists yet.** `torch`/`transformers` are not installed in
this environment, so `HuggingFaceRuntime` is untested against a real checkpoint.
Its `generate` / `score_labels` implementations are written but unexercised.

**4. Upstream artifacts left as-is (intentionally).**
`benchmark/prompt_templates/*.csv` still contain a `seriesHasNumberOfEpisodes`
row, which is not one of the six 2026 relations, and
`benchmark/models/abstract_model.py` documents a `SubjectEntityID` field the
2026 data does not carry. Both are upstream artifacts; the snapshot is not
modified. Our loaders ignore them.

**5. `hasCapacity` "highest published capacity" is not yet enforceable.** The
contract states it and the contrastive view asks for it, but with several
clusters present the selector currently picks the *dominant* cluster, not the
highest. Resolving that needs the verifier's near-miss handling
(record-attendance vs capacity) from Milestone 2.
