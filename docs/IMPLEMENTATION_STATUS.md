# Implementation status — COVER-KBC v2

Current frozen baseline: **Integrated Profile E1 — Mistral City empty rescue +
Direct Area**, hidden TEST overall F1 `0.5752`.
Integrated E1 is Profile D plus `AwardMetadataNormalizer`,
`MistralCityEmptyRescue`, and `MistralDirectArea`.

Previous frozen baseline: **Profile D — Mistral-only verifier role swap**,
hidden TEST overall F1 `0.4952`. Scope still follows
`COVER_KBC_V2_ARCHITECTURE_SPEC.pdf`, with later V3/M20/M21 and
leaderboard-probe audits recorded under [`docs/audits/`](audits/).

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

## 2. Active Development Architecture

| role | model | published params |
|---|---|---|
| enumerator | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | 24,011,361,280 |
| verifier | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | counted once |
| **total unique neural parameters** | | **24,011,361,280** (24.01B ≤ 32B) |

Current frozen baseline config:
[`configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml`](../configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml).
Previous frozen baseline config:
[`configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml`](../configs/experiments/cover_kbc_v3_3_profile_d_mistral_only_role_swap_test.yaml).
Profile D starts from historical A+Award and changes only the verifier model
identity from Qwen3.5-4B to the exact same Mistral24 checkpoint used by the
enumerator. Integrated E1 adds City empty-row rescue and Direct Area. C2,
numeric resolver, broad border repair, stock repair, award witness recall and
other aggressive repair paths are retired and not part of the active runtime.

```
Subject + Relation
   -> Relation Compiler -> Typed Program Router
   -> Mistral 24B elicitation (direct | structural | facets | contrastive | missingness)
   -> Candidate normalizer -> Candidate-Facet Evidence Graph
   -> Mistral 24B blind verifier (VALID/INVALID/UNKNOWN, same base contract)
      + Mistral verifier-role alternate recall where the base contract already uses it
   -> Evidence / uncertainty state -> RCSE residual coverage
   -> Active controller  --CONTINUE-> loop   --STOP-> Final selector
   -> AwardMetadataNormalizer
   -> E1 City empty-row rescue only for final-empty personHasCityOfDeath rows
   -> Direct Area for every hasArea row
   -> ObjectEntities
```

The older Mistral+Qwen production/readiness calibration remains historical
calibration provenance. Integrated E1 is hidden-TEST scored, but no new TRAIN
calibration was invented for the verifier role swap, City rescue, or Direct
Area.

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

python -m pytest -q
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 30
```

Neural runs use [`notebooks/COVER_KBC_Colab.ipynb`](../notebooks/COVER_KBC_Colab.ipynb),
which drives the same three phases:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_5_profile_e1_mistral_city_direct_area_baseline_test.yaml \
  --no-eval
```

Historical City-only E1 remains available at
`configs/experiments/cover_kbc_v3_4_profile_e1_mistral_city_rescue_test.yaml`.
Integrated E1 is hidden-TEST scored and frozen, but not newly TRAIN-calibrated.

## 7. Status of results

Integrated Profile E1 is the current frozen leaderboard baseline:

| relation | precision | recall | F1 |
|---|---:|---:|---:|
| awardWonBy | 0.3255 | 0.3707 | 0.3105 |
| companyTradesAtStockExchange | 0.9092 | 0.7863 | 0.7285 |
| countryLandBordersCountry | 0.9712 | 0.9295 | 0.9291 |
| hasArea | 0.6700 | 0.6600 | 0.6600 |
| hasCapacity | 0.3776 | 0.1224 | 0.1224 |
| personHasCityOfDeath | 0.9600 | 0.5900 | 0.5700 |
| **All Relations** | **0.7563** | **0.5929** | **0.5752** |

Promotion provenance:

- previous baseline Profile D source commit:
  `170c48756660a34d611b3a563ac26cd4564434ef`
- previous baseline Profile D prediction SHA256:
  `7a01382de3e95530ecdfabd7cee049712ce7e326f7d4d299e28c5b5ba981320c`
- local integrated E1 prediction artifact:
  `outputs/submission-best/predictions.jsonl`
- local integrated E1 prediction SHA256:
  `67bd1bc8af01de177520d93f9b5b9fc30839d56f36ceeeb6263813662e52d8a6`
- rows: 475
- unique model portfolio:
  `mistralai/Mistral-Small-3.2-24B-Instruct-2506`
- Qwen runtime calls: 0
- Direct Area applies to all 100 `hasArea` rows with one Mistral call per row.

Profile D remains preserved at overall F1 `0.4952`. Historical A+Award remains
preserved at overall F1 `0.4910`; C2 remains a retired negative hidden TEST
probe at overall F1 `0.4012`.

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

1. **Calibration caveat for integrated E1.** The verifier role swap and the
   post-pipeline City/Area calls are hidden-TEST scored but not newly
   TRAIN-calibrated. The explicit leaderboard-probe readiness path remains the
   honest status.
2. **Profile E2 is reserved for future work.** No Profile E2 runtime or config
   is implemented in this promotion.
3. **The official `baseline.py` does not exist upstream.** The README references
   it, but commit `30d8cfa` contains no such file. "Baseline reproduction" can
   only be a *reconstruction* from the published config, prompt templates,
   `abstract_model.py` interface and results table.
4. **Thresholds are frozen from existing provenance, not newly re-derived for
   D.**
5. **Upstream artifacts left as-is** (intentional): stale
   `seriesHasNumberOfEpisodes` prompt row, `SubjectEntityID` docstring.

Deferred: DoLa intermediate-layer decoding (experimental plugin; the
`hidden_states` seam exists), and learned policies (never — rules forbid).
