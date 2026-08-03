# Implementation status — COVER-KBC v2

Current through **Milestone 2** (architecture build-out). Scope follows
`COVER_KBC_V2_ARCHITECTURE_SPEC.pdf`, with the official task definition and
evaluator treated as the source of truth wherever the two could be read
differently.

Audits: [`docs/audits/`](audits/) — `0001` (foundation), `0002` (architecture).

---

## 1. Execution policy

Heavyweight neural execution runs on **Google Colab**, not on the development
machine. The split is deliberate:

```
LOCAL                              COLAB
architecture, contracts, prompts   clone repo, install deps
evidence graph, verifier logic     download models
controller, RCSE, selection        neural inference
configs, unit tests, audits        validation, ablations, official evaluator
```

The local machine (RTX 4060 Laptop, 8 GB VRAM, 14 GB RAM) is an implementation
environment. Everything below is testable without loading a heavyweight model,
using `ScriptedRuntime` and synthetic logits.

## 2. Frozen target architecture

| role | model | published params |
|---|---|---|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 24,011,361,280 |
| verifier | `Qwen/Qwen3.5-4B` | 4,659,865,088 |
| **total** | | **28,671,226,368** (28.67B ≤ 32B) |

Roles are architecture, not interchangeable backends. Config:
[`configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml`](../configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml).

```
Subject + Relation
   -> Relation Compiler -> Typed Program Router
   -> Mistral 24B elicitation (direct | structural | facets | contrastive | missingness)
   -> Candidate normalizer -> Candidate-Facet Evidence Graph
   -> Qwen 4.66B blind verifier (VALID/INVALID/UNKNOWN, calibrated label logits)
      + Qwen independent alternate recall (cross-model evidence)
   -> Evidence / uncertainty state -> RCSE residual coverage
   -> Active controller  --CONTINUE-> loop   --STOP-> Final selector
   -> ObjectEntities
```

## 3. Module status

| Spec module | Where | Status |
|---|---|---|
| 0 Relation Compiler | `contracts/` | complete, 6 contracts |
| 1 Typed Program Router | `contracts/router.py` | complete |
| 2 Diverse Elicitation | `elicitation/` | complete, 22 views, 4 families |
| 3 Normalization + Evidence Graph | `normalization/`, `evidence/` | complete |
| 4 Logit-Calibrated Blind Verifier | `verification.py` | complete |
| 5 Evidence / Uncertainty State | `scoring.py` | complete, `S(o)` with components |
| 6 RCSE | `coverage.py` | complete |
| 7 Active Controller + stopping | `controller.py` | complete |
| 8 Final Selector | `selection.py` | complete, relation-specific |
| 21 Runtime + logit access | `models/` | complete, **unexercised on real weights** |
| — Staged execution | `staging.py` | complete |
| — DoLa | — | deferred (experimental plugin; seam exists via `hidden_states`) |

## 4. Relation programmes

| Relation | Programme | Selection behaviour |
|---|---|---|
| `countryLandBordersCountry` | `SMALL_SET` | direct + compass structural; maritime contrast; precision-aware |
| `companyTradesAtStockExchange` | `SMALL_SET` | calibrated listing gate; parent/subsidiary contrast; precision-first |
| `personHasCityOfDeath` | `NULL_SINGLE` | calibrated death gate; locality-granularity check; ≤ 1 object |
| `hasArea` | `NUMERIC` | total-area semantics; robust **dominant-cluster median** |
| `hasCapacity` | `NUMERIC` | **highest published** capacity among sufficiently-supported *valid* clusters |
| `awardWonBy` | `LARGE_OPEN_SET` | 5 fixed facets (enumeration/temporal/recipient-type/category/missingness); recall-first |

## 5. Load-bearing design decisions

**Candidate identity is two-level.** `strict_key` is the evaluator's own
normalisation — collapsing on it is provably lossless. `alias_hint_key` adds
leading-article folding only. Parenthetical qualifiers are **never** folded:
`Springfield (Illinois)` and `Springfield (Missouri)` are different entities,
and merging them would silently destroy one. Article folding can at worst pick a
different surface form of the same entity; parenthetical folding can lose an
entity outright.

**Independence ≠ repetition ≠ facets.** Three concepts kept apart:
`view_id` (which prompt), `facet_id` (which slice of one mechanism), and
`independence_group` (which evidence family). Five award decades are five facets
but **one** independent support. Asserted in `tests/test_evidence.py`.

**Cross-model evidence distinguishes recall from agreement.** The verifier
model *independently recalling* a name (`CROSS_MODEL_RECALL`,
`INDEPENDENT_RECALL`) is separate evidence and earns full `X(o)`. Merely
agreeing with a name it was shown (`BLIND_VERIFIER`, `SHOWN_CANDIDATE`) is
anchored and cheap, so it earns `shown_candidate_weight` (0.25) — its strength
is already carried by `L(o)`.

**Comma parsing is separator-ranked.** JSON array > semicolon/newline/pipe >
comma. Comma splitting only happens when no stronger separator was present, and
never on a digit-group comma — so `"35,000"` cannot become `"35"`/`"000"`, and
`"Washington, D.C."` survives. Numeric relations raise `TypeError` if routed to
entity parsing at all.

**Existence gates are calibrated, not single-shot.** A gate closes only when NO
is the argmax *and* clears both a logit-margin and a probability threshold. An
uncertain or high-entropy read falls through to discovery: forcing empty on a
weak signal converts uncertainty into guaranteed zero recall.

**Empty is explained, never conflated.** `confident_negative_gate`,
`unresolved_abstention`, `no_candidate_generated`, `candidate_rejected`,
`pipeline_error` are distinct — the first is a correct answer, the second a
coverage failure, and they call for opposite fixes.

**Numeric output is a bare numeral.** The evaluator's `try_parse_number` is
`float(v.replace(",", "").strip())`, so `"5556 km²"` can never be a true
positive while still costing precision.

**RCSE estimates search value, not cardinality.** `q_res ∈ [0,1]` answers "is
another action likely to add useful verified information?" — not "how many true
objects remain". Model views are not independent captures, and some `awardWonBy`
gold sets are partial, so a real cardinality estimate would be the wrong target.

## 6. How to run

```bash
pip install -e '.[dev]'            # add '.[hf]' for neural backends

python -m pytest -q                                       # 251 tests
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 30
```

Neural runs use [`notebooks/COVER_KBC_Colab.ipynb`](../notebooks/COVER_KBC_Colab.ipynb),
which drives the same three phases:

```bash
python scripts/run_staged.py enumerate --config C --split val   # Mistral only
python scripts/run_staged.py verify    --config C --run-dir D   # Qwen only
python scripts/run_staged.py decide    --config C --run-dir D   # no model
```

Phase C is non-neural, so thresholds can be re-tuned against one expensive set
of generations without re-running any model.

## 7. Status of results

**No neural evaluation result exists.** No metric in this repository was
produced by a heavyweight model. The only executed runs are non-neural plumbing
checks (abstain baseline; scripted staged smoke test), explicitly labelled as
such and never reportable as system performance.

Official upstream baseline, for later comparison — read from the upstream README
at the pinned commit, **not** reproduced by us:

| relation | macro-p | macro-r | macro-f1 |
|---|---|---|---|
| awardWonBy | 0.247 | 0.078 | 0.101 |
| companyTradesAtStockExchange | 0.369 | 0.725 | 0.354 |
| countryLandBordersCountry | 0.697 | 0.911 | 0.665 |
| hasArea | 0.290 | 0.290 | 0.290 |
| hasCapacity | 0.180 | 0.180 | 0.180 |
| personHasCityOfDeath | 0.210 | 0.600 | 0.210 |
| **All Relations** | **0.324** | **0.507** | **0.313** |

## 8. Open issues

1. **No neural validation.** Every neural path is unexercised against real
   weights. First Colab run is the next step.
2. **The official `baseline.py` does not exist upstream.** The README references
   it, but commit `30d8cfa` contains no such file. "Baseline reproduction" can
   only be a *reconstruction* from the published config, prompt templates,
   `abstract_model.py` interface and results table.
3. **Mistral/Qwen loading is untested on real weights.** The multi-auto-class
   loader and the multi-token label fallback are implemented and unit-tested
   with fakes, but no real checkpoint has been loaded.
4. **Thresholds are hand-set, not calibrated.** They must be tuned on `train`
   and frozen before `val` is scored.
5. **Upstream artifacts left as-is** (intentional): stale
   `seriesHasNumberOfEpisodes` prompt row, `SubjectEntityID` docstring.

Deferred: DoLa intermediate-layer decoding (experimental plugin; the
`hidden_states` seam exists), and learned policies (never — rules forbid).
