# Audit 0034 — Post-Architecture Real-Model Runtime Smoke

Status: **PASS / COMPLETE**
Date: 2026-08-07
Milestone: **real-weight runtime verification**. Not calibration, not
validation, not a leaderboard experiment, not production activation.

---

## 1. What this audit is, and is not

Audit 0033 proved the architecture conformant under `ScriptedRuntime`. This
audit proves the **same architecture executes against the two real frozen
models** and their native tokenizer and runtime paths.

It answers runtime questions only: do the weights load, do the tokenizer and
chat-template paths work, does label scoring produce usable logits, does Module
17's live call plan cost what Module 20 says it costs, are physical calls
accounted, does the shadow stack leave production output untouched.

**It answers no factual question.** No accuracy, precision, recall, F1 or
leaderboard number appears here, and none was computed. A semantically wrong
answer is still a runtime PASS provided every contract executed correctly.

Audit 0033's architecture conclusions are **preserved unchanged**; nothing in
this milestone altered production source.

---

## 2. Evidence source

Two kinds of statement appear below, and they have different sources:

* **Runtime measurements and observed runtime values** — every call count,
  token count, logit, probability, identifier, hash, flag and role sequence —
  are quoted from the final `real_model_architecture_smoke_summary.json`
  produced by `scripts/real_model_smoke.py`. No GPU, VRAM or environment fact
  is asserted that the artifact does not contain.
* **Architecture and background statements** — the frozen 28.67B profile, the
  ownership boundary that leaves Module 18 unscheduled, the Module 17 call-plan
  rule, the tokenizer and blindness invariants — are **inherited from the prior
  audits named at each point** (0010, 0014, 0026, 0027, 0030–0033). This audit
  restates them as context and re-checks them against the measurements; it does
  not re-derive them.

Where the two meet, the audit says which is which: for example §6 quotes the
observed 8/4 from the artifact and attributes the 8/4 *prediction* to Audit 0033
§16A.

The artifact itself is a run output and is not committed to the repository. Its
`repo_sha` pins the exact repository state that produced the smoke.

---

## 3. Exact real-run identity

| Field | Value |
| --- | --- |
| repo SHA | `7742b6e1dfd8a06596fe947794018ef37992d928` |
| config | `configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml` |
| config SHA256 prefix | `83da2ca1ca199bab` |
| memory mode | `staged` |

**Enumerator** — `mistralai/Mistral-Small-3.2-24B-Instruct-2506`,
revision `95a6d26c4bfb886c58daf9d3f7332c857cb27b43`,
tokenizer `mistral_common`, quantization `nf4`.

**Verifier** — `Qwen/Qwen3.5-4B`,
revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`,
tokenizer `huggingface`, quantization `nf4`.

Exactly the frozen profile Audit 0033 recorded: 28.67B of the 32B cap, two
models, no third model, no checkpoint or quantization change.

---

## 4. Primitive enumerator evidence — Mistral generate

One real `LMRuntime.generate` through the production runtime:

| Field | Value |
| --- | --- |
| `ok` | `true` |
| `prompt_tokens` | 21 |
| `generated_tokens` | 2 |
| `finish_reason` | `stop` |
| `decoded_non_empty` | `true` |

The 24B checkpoint loads and generates through its **native `mistral_common`**
path — Audit 0014's tokenizer correction confirmed on real weights, with no
silent HuggingFace-tokenizer substitution.

---

## 5. Primitive verifier evidence — Qwen score_labels

One real `LMRuntime.score_labels`, with the canonical `dict(LABEL_TOKENS)`
mapping:

| Label | Continuation | Token ids | Logit | Probability |
| --- | --- | --- | --- | --- |
| VALID | `A` | `[32]` | 12.0 | 0.5250474461061729 |
| INVALID | `B` | `[33]` | 8.1875 | 0.011599808995177145 |
| UNKNOWN | `C` | `[34]` | 11.875 | 0.4633527448986499 |

`label_single_token = true`, `scoring_strategy = next_token_logits`,
`logits_finite = true`, `probabilities_normalise = true`,
`probability_sum = 1.0`, `generated_tokens = 0`, `prompt_tokens = 80`.

`generated_tokens = 0` on a scoring call is Audit 0010's rule holding on real
weights: label scoring **is** a neural call and generates nothing.

**Recorded, not generalised.** That this checkpoint's tokenizer encodes A/B/C as
single tokens is an observation about this revision, not an architectural
assumption. The runtime inspects the real tokenizer every time, and the
**sequence-likelihood fallback remains part of the design** for any checkpoint
or label set where it does not hold.

---

## 6. Module 17 real call-plan evidence

| Field | Value |
| --- | --- |
| templates | `m17_statement_v1`, `m17_question_v1` |
| label orders | `ABC`, `BAC` |
| `use_calibration` | `true` |
| factual readings | 4 |
| controls | 4 |
| expected cold calls | **8** |
| observed cold calls | **8** |
| expected warm calls | **4** |
| observed warm calls | **4** |

This is the load-bearing confirmation of **Audit 0033 §16A**. That corrective
pass found the Layer-6 adapter pricing the live Module 17 action at one call and
replaced the number with `m17_call_plan(live config)`; it predicted cold 8 and
warm 4 from two phrasings × two label orders plus one contextual control each.
Real weights returned exactly that, and the warm reading confirms the contextual
control cache genuinely amortises — a cache hit performs no inference.

Expected and observed agree on both readings, so the runtime and the planner
are consistent.

---

## 7. Composed core / shadow evidence

Two sequential staged passes over the four hand-declared smoke queries:

| Field | Value |
| --- | --- |
| `production_core_calls` | 10 |
| `upgraded_shadow_calls` | 51 |
| `shadow_only_calls` | 41 |
| `production_output_unchanged` | **true** |
| `m7_budget_unchanged` | **true** |
| `production_stop_reasons_unchanged` | **true** |
| `m9_refined` | `true` |
| `m21_executed` | `false` |

Specialist families exercised on real weights: **M12_NUMERIC**,
**M13_LARGE_OPEN_SET**, **M14_NULL_TEMPORAL**, **M15_SMALL_SET** — all four.

The 41 extra calls are **shadow neural spend** by M11, M12–M15 and M17,
attributed as such and not charged to Module 7's budget. This is the strongest
statement the architecture makes, now confirmed with real weights: enabling the
entire upgraded stack changes no production prediction, no Module 7 accounting
and no stop reason.

`m9_refined = true` confirms Audit 0033 §10A on real weights — the persisted
Module 9 profile is the graph-aware refinement, carrying `q_novel` measured from
early returns and Table 3's secondary route hints.

---

## 8. Natural Module 18 execution

```
m18_natural_mechanisms_executed = []
```

**This is correct behaviour, not a defect.** The pipeline's Phase-C seam
*catalogues* Module 18's eligible checks and executes none:
`_catalogue_bidirectional_checks` spends nothing, and execution lives behind an
explicitly-called `execute_bidirectional_checks`. Choosing which eligible check
is worth a call is Module 20/21's job, and both are uncalibrated and disabled
(§10). With no scheduler, nothing schedules — which is exactly the ownership
boundary Audits 0030–0032 established.

Weakening an eligibility guard, or fabricating graph evidence to make a
production branch fire, would have destroyed that boundary. Neither was done.

---

## 9. Isolated Module 18 real-weight contract evidence

**This is isolated contract-smoke evidence, explicitly NOT a natural production
firing.** It ran only because natural coverage was empty, and only after the
production/shadow comparison had already returned its verdict.

| Field | Value |
| --- | --- |
| mode | `ISOLATED_CONTRACT_SMOKE` |
| relation | `countryLandBordersCountry` |
| eligible catalogue | `CANDIDATE_FREE_RECALL`, `COUNTERFACTUAL` ×6, `KEY_CONDITION`, `REVERSE` |
| **mechanism executed** | **`CANDIDATE_FREE_RECALL`** |
| model role | `enumerator` |
| template | `m18_candidate_free_v1` |
| check version | `m18-v1` |
| operation id | `m18_candidate_free_recall:m18_candidate_free_v1#0` |
| `physical_calls` | 1 |
| `record_calls` | 1 |
| `origin_event_id` | `3f56fd2854bfa612` |
| `prompt_sha256` | `210c08b72396c8862000382eaee559c19d81bd21aea9df4d591190b5045ee734` |
| model | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` |
| revision | `95a6d26c4bfb886c58daf9d3f7332c857cb27b43` |
| `independence_group` | `M18_CANDIDATE_FREE_RECALL` |
| `parse_status` | `OK` |
| `candidate_shown` | `false` |
| `independent_recall` | `true` |
| `cross_model_eligible` | `false` |
| `generated_tokens` | 2 |
| `recalled_candidates` | 1 |
| `error` | `null` |
| `entered_production_graph` | **`false`** |
| `entered_shadow_graph` | **`false`** |

```
m18_isolated_contract_mechanisms_executed = [CANDIDATE_FREE_RECALL]
m18_real_weight_coverage = true
```

The mechanism ran through the production seam — `catalogue` → `build_request` →
`execute` on the real `BidirectionalVerifier` — over a hand-declared synthetic
consensus, with one real physical call. Coverage is derived from the executed
request's own `check_kind`, never a hard-coded name.

Three audited invariants are visible in the record and hold on real weights:
`candidate_shown = false` and `independent_recall = true` are candidate-free
blindness (Audit 0026); `cross_model_eligible = false` is correct because the
enumerator served the call, so no distinct second family was involved (Audit
0027's provenance rule).

**This evidence changed no `ObjectEntities` and no production accounting.** It
entered neither graph, and it ran after the invariance comparison, so the
production verdict in §7 cannot depend on it.

---

## 10. Staged residency

Observed role sequence:

```
enumerator -> verifier -> enumerator -> verifier -> enumerator -> verifier -> enumerator
```

`shared_profile = false` — the two roles name distinct checkpoints, so each was
loaded and released on its own.

What the artifact proves: staged execution completed end to end; the exact
frozen roles were repeatedly loaded and released over **seven staged role
activations across six role transitions**; and no quality downgrade was
introduced to achieve it — no model, revision, NF4,
prompt, decode-config, context or call-count concession.

**No peak-VRAM figure is claimed.** The artifact does not record one, and none
is invented here.

---

## 11. Module 20 / Module 21 safety state

| Field | Value |
| --- | --- |
| `module_20_activated` | `false` |
| `ledger_reserved` | `false` |
| `m20_refuses_without_calibration` | `true` |
| `m21_refuses_without_packages` | `true` |
| `m21_executed` | `false` |

Both remain **uncalibrated, disabled and fail-closed on real weights**. Enabling
either without its package still raises before anything runs, and no calibration
package — real or synthetic — was created for this milestone. Module 21 selected
nothing and executed nothing.

The team is intentionally proceeding to validation inference before TRAIN-set
calibration. That is a deliberate sequencing choice, and this audit neither
performs nor requires that calibration.

---

## 12. Closed-book and data safety

| Field | Value |
| --- | --- |
| `benchmark_data_read` | `false` |
| `factual_scoring_performed` | `false` |
| `errors` | `[]` |

No benchmark gold, no TRAIN, no VALIDATION and no TEST data was read by this
smoke. The manifest subjects are hand-declared in the harness for runtime
compatibility only, and nothing was scored against a reference.

---

## 13. Verdict

**POST-ARCHITECTURE REAL-MODEL RUNTIME SMOKE: PASS / COMPLETE.**

| Item | Result |
| --- | --- |
| Architecture | **unchanged from Audit 0033** |
| Frozen model profile | **confirmed runnable on real weights** |
| Staged execution | **PASS** |
| Module 17 real call-plan | **PASS** (expected 8/4, observed 8/4) |
| M12–M15 real composed coverage | **PASS** (all four families) |
| Module 18 real-weight contract coverage | **PASS**, via explicitly isolated contract smoke |
| Production / shadow invariance | **PASS** |
| Module 20 / Module 21 | intentionally disabled and unfitted; **fail-closed** |

```
M0-M21 architecture implementation:              COMPLETE
Cross-layer conformance (Audit 0033):            PASS
Real-weight post-architecture runtime smoke:     PASS / COMPLETE

Upgraded production activation:                  NOT YET
M20 production budget calibration:               NOT YET
M21 TRAIN historical bins / coefficients / tau:  NOT YET
Full VALIDATION:                                 NOT YET
```

Remaining next action: **FULL VALIDATION inference**.

---

## 14. Explicit non-goals

Not done here and not claimed: any calibration, any TRAIN/VALIDATION/TEST run,
any leaderboard submission, any accuracy or F1 number, any upgraded production
activation, any change to models, revisions, quantization, prompts, budgets,
thresholds or runtime semantics, and any DoLa work.
