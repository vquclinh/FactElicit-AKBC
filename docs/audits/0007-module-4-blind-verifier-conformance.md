# Audit 0007 — Module 4: Logit-Calibrated Blind Verifier Conformance

Status: **complete — 3 defects found, 3 fixed**
Date: 2026-08-04
Scope: Module 4 only. Modules 5, 6, 7, 8 remain unreviewed.

---

## 1. Objective and scope

Determine whether the implemented Module 4 is the verifier the COVER-KBC v2
proposal specifies — a *blind*, *label-logit*, *contextually calibrated*,
*three-way* verifier — or a generated-text classifier wearing that vocabulary.

In scope: `src/cover_kbc/verification.py`, the label-scoring path in
`src/cover_kbc/models/huggingface.py` and `src/cover_kbc/models/offline.py`,
the tiering and policy-precedence functions in `src/cover_kbc/scoring.py` that
decide *what Module 4 is asked to do*, and the seam where verifier output
becomes graph evidence.

Out of scope, deliberately: the S(o) aggregation formula (Module 5), the active
controller and stopping rule (Module 7), and final selection (Module 8). Where
this review found defects in those modules it **records them and leaves the code
alone** (§34).

No model was downloaded or run. All neural execution remains reserved for Colab.

---

## 2. Proposal requirements

From the specification (section numbers as printed in the PDF):

| § | Requirement |
|---|---|
| 10.1 | Verification is *blind*: "The generator's explanation is deliberately hidden. This follows the independence principle in Chain-of-Verification." |
| 10.2 | Three fixed labels A/B/C = VALID/INVALID/UNKNOWN, scored as label logits `z_A, z_B, z_C`, with `p_j = exp(z_j) / Σ exp(z_·)`. |
| 10.3 | Contextual calibration: run the same template on a **content-free control** instance to obtain bias logits `b_j`; then `z̃_j = z_j − b_j`, `p̃ = softmax(z̃/T)`. Default `T = 1`. "Fitting a learned calibrator is deliberately avoided." |
| 10.3 | Signals: `P_V(o) = p̃_A`, `M(o) = z̃_A − max(z̃_B, z̃_C)`, `H_ver(o) = −Σ p̃_j log p̃_j`. |
| 10.4 | Prompt-distribution stability: `p̄(o) = (1/m) Σ p_i(o)`, `U_prompt(o) = (1/m) Σ D_KL(p_i ‖ p̄)`. "High disagreement makes a candidate unresolved even when one prompt greedily emits VALID." |
| 10.5 | Four tiers: auto-accept (strong independent support, no contradiction, low uncertainty); verifier (weak support, near threshold, or disagreement); adversarial verifier (candidate "is a common near miss specified by the contract"); auto-reject (hard type/format violation). |
| 9.2 | Every edge carries `independence_group`, model ID, view ID, run ID, and the raw verifier distribution. |
| Table 9 | No training, no learned probe, no retrieval on the prediction path. |

---

## 3. Pre-work repository state

Branch `main`, HEAD `d9d4c5d` ("refactor: align COVER-KBC evidence graph with
architecture"). Audits 0001–0006 accepted. Working tree carried no uncommitted
changes to `src/` at the start of this review.

Test suite before this review: 447 tests, all passing.

---

## 4. Existing Module-4 design

`src/cover_kbc/verification.py` provides:

- `LABEL_TOKENS = {"VALID": "A", "INVALID": "B", "UNKNOWN": "C"}` and
  `GATE_LABELS = {"YES": "A", "NO": "B", "UNKNOWN": "C"}`.
- Three `VerifierTemplate` instances: `verify_standard_v1`, `verify_question_v1`,
  `verify_adversarial_v1`.
- `ContextualCalibrator` — caches one content-free control measurement per
  scoring setup.
- `read_labels` — turns raw label logits plus an optional control into a
  `VerificationResult`.
- `verify_candidate` — one blind call; `verify_multi_template` — the same
  candidate under several templates; `aggregate_verifications` — merges them.
- `jensen_shannon_divergence` / `normalized_disagreement` — `U_prompt`.
- `score_gate` — the calibrated existence gate, sharing the same machinery.
- `inspect_label_encoding` / `LabelEncoding` — tokenisation introspection.

Tiering (`assign_tier`), policy precedence (`resolve_verification`) and the
accept/reject decision (`decide_status`) live in `src/cover_kbc/scoring.py`.

---

## 5. Verifier-input / blindness matrix

The verifier prompt is built solely by `build_verifier_prompt(query, contract,
candidate_display, template)`. What each argument can contribute:

| Input | Reaches the prompt? | Notes |
|---|---|---|
| `query.subject` | yes | required to state the claim |
| `query.relation` | yes | via the contract |
| `contract.definition` | yes | the relation's meaning |
| `contract.hard_negative_rules` | yes | type discipline |
| `contract.verification.adversarial_classes` | adversarial template only | named near-miss classes |
| candidate **display value** | yes | the claim under test |
| candidate `independent_support` | **no** | |
| candidate `raw_support_count` | **no** | |
| candidate `score` / `score_breakdown` | **no** | |
| candidate `groups` / evidence edges | **no** | |
| candidate `record_ids` | **no** | |
| generating view id / family / run id | **no** | |
| the generator's raw output text | **no** | |
| other candidates for the same query | **no** | |

`build_verifier_prompt` receives a *string*, not a `Candidate`, so the leak
surface is structurally closed rather than merely unused.

**Verdict: CORRECT.** Three tests enforce it:

- `test_no_template_leaks_generator_rationale` — parametrized over all three
  templates, asserts none of `independent_support`, `raw_support`, `score`,
  `evidence`, `generator`, `rationale`, `because`, `run_id`, `frequency`,
  `views agreed`, `confidence` appear.
- `test_rich_acquisition_provenance_never_reaches_the_prompt` — builds a real
  graph in which the candidate carries a `RELATION_FOCUSED_DESCRIPTION` record
  whose raw output contains `SECRET_RATIONALE …`, confirms the provenance
  exists, then confirms none of it (nor the record ids) reaches the prompt.
- `test_the_verifier_never_sees_other_candidates`.

---

## 6. Label / tokenisation scoring analysis

`verify_candidate` calls `runtime.score_labels(LabelScoreRequest(...))` and
never `runtime.generate(...)`. This is enforced statically, not by convention:
`test_verification_never_falls_back_to_generated_text` parses `verification.py` with `ast` and
asserts no `Call` node whose function attribute is `generate` exists anywhere in
the module.

`HuggingFaceRuntime.score_labels` does **not** assume single-token labels. It
calls `self.inspect_labels(...)`, which runs the *real* tokenizer, and branches:

```python
encoding = self.inspect_labels(dict(request.labels))
if encoding.single_token:
    return self._score_next_token(request, encoding)
return self._score_sequence(request, encoding)
```

**Verdict: CORRECT.** There is no silent first-token shortcut.

**Explicitly untested:** whether `"A"`, `"B"`, `"C"` in fact encode to single
tokens under the real `Qwen/Qwen3.5-4B` tokenizer. Tokenizer observations from
earlier Qwen3.5-**9B** development do **not** transfer, and this review did not
download 4B. The code does not need the answer — it measures it at runtime —
but the audit records that the *measurement* has not yet been taken on the
frozen checkpoint. Tests use fake tokenizers to exercise both branches and an
AST assertion to prove the real runtime branches on `encoding.single_token`.

---

## 7. Single-token vs multi-token fallback

`_score_next_token`: one forward pass, reads `logits[0, -1, :]` at each label's
token id. Comparable across labels because all are next-token logits at the same
position.

`_score_sequence`: for each label, concatenates the label's token ids to the
prompt, runs a forward pass, and sums `log P(token)` over the full continuation
(`log_probs[start_index + offset - 1, token_id]`). Cost is one forward pass per
label; correctness is preserved because full sequence log-likelihoods are
comparable across labels of differing length in the same way first tokens are
not.

The docstring names the failure it guards: *"Comparing only first tokens would
be wrong, and silently doing so is the specific failure this guards against."*

**Verdict: CORRECT — principled, not a stub.**

---

## 8. Contextual-calibration derivation and implementation

Implementation in `read_labels`:

```
z̃_j = z_j − b_j                     (b from the content-free control)
p̃   = softmax(z̃ / T),  T = 1        (default; no fitted temperature)
P_V  = p̃_A
M    = z̃_A − max(z̃_B, z̃_C)          (calibrated logits, not raw)
H    = −Σ p̃_j log p̃_j                (calibrated distribution)
```

Checked against §10.3 term by term. Tests:

- `test_calibrated_logits_are_raw_minus_bias` — asserts `z̃ == z − b` for
  every label, not merely that the numbers moved.
- `test_calibration_can_flip_the_decision` — constructs a case where raw logits
  say VALID and calibrated logits say otherwise, proving calibration is
  load-bearing rather than cosmetic.
- `test_the_default_temperature_is_exactly_one`.
- `test_probabilities_sum_to_one`.
- `test_no_learned_calibrator_exists` — AST walk over `verification.py` for any
  `fit`/`train`/`partial_fit` call, and a check that no module-level mutable
  state accumulates across calls.

**Verdict: CORRECT — exactly the proposal's arithmetic, no learned component.**

---

## 9. Content-free control design / cache analysis

The control renders the *same template* with `CONTENT_FREE_CANDIDATE` in place
of the candidate, measuring the template's label prior with no factual content.

### 9.1 DEFECT 2 (fixed) — the cache key was under-specified

The original key was `(model_id, relation, template_id, decode_identity)`. A
control measures *template bias for a specific scoring setup*, and two parts of
that setup were missing:

- **model revision** — two runs pinned to different revisions of the same
  `model_id` share a key, so a revision change silently subtracts the previous
  checkpoint's bias from every candidate;
- **label set / scoring convention** — the verifier (`VALID/INVALID/UNKNOWN`)
  and the gate (`YES/NO/UNKNOWN`) both call through `_control_cache_key`. They
  use different label sets, so a key that does not name the label set can serve
  one measurement to the other.

Fixed: the key is now
`(model_id, revision, label_signature, relation, template_id, decode_identity)`,
with `_label_signature` producing a stable `"INVALID=B,UNKNOWN=C,VALID=A"`
rendering. Both call sites pass `revision=runtime.spec.revision` and their own
label signature.

Test: `test_the_control_cache_cannot_leak_across_incompatible_setups` is parametrized over `template`,
`relation`, `model`, and `revision`. It asserts the call counter goes 1 → 1
(identical setup reuses the measurement) → 2 (any differing component forces a
fresh measurement). The revision case was manually confirmed to be non-vacuous:
the counter is 1, then 2 after `dataclasses.replace(spec, revision="r2")`, then
still 2 when the first revision is requested again.

### 9.2 Controls create no evidence

`verification.py` never imports `cover_kbc.evidence.graph`. The content-free
control therefore *structurally cannot* create a candidate or an edge — it is
not a discipline that could be broken by a future edit without also adding an
import. `test_the_control_creates_no_candidate_and_no_edge` asserts the graph
is unchanged across a control measurement, and
`test_a_verifier_label_can_never_become_a_candidate` asserts `"A"`, `"B"`,
`"C"`, `"VALID"` etc. never appear as candidate keys.

**Verdict: CORRECT after fix.**

---

## 10. Numerical-stability analysis

`softmax` in `models/base.py` subtracts the max before exponentiating. Probed:

| Input | Result |
|---|---|
| `z = {A: 1e4, B: −1e4, C: 0}` | finite, sums to 1, no overflow |
| `z = {A: −1e4, B: −1e4, C: −1e4}` | uniform 1/3, no underflow to all-zero |
| all-equal logits | uniform; `argmax` tie broken deterministically |
| control equal to logits (`z̃ = 0`) | uniform, `M = 0`, `H = log 3` |

### 10.1 DEFECT 3 (minor, fixed) — entropy could report `−0.0`

On a fully degenerate distribution the entropy sum produced `-0.0`, which
serialises as `-0.0` in the staged JSONL and reads as a negative entropy in
diagnostics. Clamped at both sites: `entropy=max(0.0, entropy(probabilities))`
in `read_labels` and `score_gate`.

**Verdict: CORRECT after fix.**

---

## 11. P_VALID / margin / entropy analysis

| Signal | Source | Conformance |
|---|---|---|
| `valid_prob` | `p̃_A` from the calibrated distribution | §10.3 ✓ |
| `invalid_prob`, `unknown_prob` | `p̃_B`, `p̃_C` — stored separately, never folded together | ✓ |
| `margin` | `z̃_A − max(z̃_B, z̃_C)`, from **calibrated** logits | §10.3 ✓ |
| `entropy` | `−Σ p̃_j log p̃_j`, from the **calibrated** distribution | §10.3 ✓ |
| `raw_logits`, `bias_logits` | both retained on the result | §9.2 ✓ |
| `calibrated` | boolean flag recording whether a control was applied | ✓ |

Tests assert margin and entropy are computed from calibrated rather than raw
values by constructing a case where the two disagree.

---

## 12. Multi-template inventory

| `template_id` | Role | Used for disagreement? |
|---|---|---|
| `verify_standard_v1` | declarative claim | yes |
| `verify_question_v1` | interrogative reframing | yes |
| `verify_adversarial_v1` | names the contract's near-miss classes | no — it is a *different question*, not a paraphrase |

`DISAGREEMENT_TEMPLATE_IDS = ("verify_standard_v1", "verify_question_v1")`.

Excluding the adversarial template from the disagreement set is correct: §10.4
measures instability *under paraphrase*. The adversarial template deliberately
changes the question by naming near misses, so a divergence between it and the
standard template is signal about the near-miss classes, not about prompt
instability. `test_multi_template_verification_records_the_disagreement`
pins it: the multi-template run returns exactly `len(DISAGREEMENT_TEMPLATE_IDS)`
results, so the adversarial template cannot silently join the paraphrase set.
`test_all_templates_ask_the_same_question_of_the_same_contract` separately
asserts all three templates are worded differently but carry identical
semantics — the same subject, candidate, contract definition and label key.

All three templates were verified blind (§5).

---

## 13. Prompt-disagreement / JSD analysis

`jensen_shannon_divergence(distributions)` computes the generalized JSD as the
mean KL to the mean distribution — exactly `U_prompt` in §10.4.

- `test_disagreement_equals_the_mean_kl_to_the_mean_distribution` recomputes
  `(1/m) Σ D_KL(p_i ‖ p̄)` independently from the same inputs and asserts
  equality, rather than merely checking the value is in `[0, 1]`.
- `test_identical_distributions_give_zero_disagreement`.
- `test_multi_template_verification_records_the_disagreement` — the score is
  written onto *every* component `VerificationResult`, so §10.4's "high
  disagreement makes a candidate unresolved" has an input to act on whichever
  result a later module reads.
- `test_high_disagreement_is_not_averaged_into_confident_valid` — a 0.95-VALID
  template merged with a 0.90-INVALID template yields `valid_prob == 0.5` and a
  strictly positive `prompt_disagreement`. Aggregation cannot launder a conflict
  into confidence.

**Verdict: CORRECT.**

---

## 14. Verifier provenance schema

Every verifier edge carries:

| Field | Value |
|---|---|
| `independence_group` | `BLIND_VERIFIER` — exactly one group, never merged into an elicitation family |
| `mode` | `SHOWN_CANDIDATE` |
| `edge_type` | `SUPPORT` / `CONTRADICT` / `UNKNOWN` per verdict |
| `model_id` | the verifier runtime's id |
| `view_id` | `blind_verifier` |
| `run_id` | present |
| `edge_id` | deterministic, from Module 3 |

`VerificationResult` additionally retains `raw_logits`, `bias_logits`,
`calibrated`, `margin`, `entropy`, `prompt_disagreement`, `template_id`,
`model_id`, `model_family` — satisfying §9.2's "raw verifier distribution if
applicable".

**Verdict: CORRECT.**

---

## 15. Standard vs adversarial verifier analysis — DEFECT 1 (severe, fixed)

### 15.1 The defect

`ADVERSARIAL_VERIFY` was **unreachable on a first pass**. The tier was assigned
only when:

- `candidate.contradiction_count > 0` — but a contradiction requires a prior
  `INVALID` verdict, i.e. a verification must already have happened; or
- `prompt_disagreement > adversarial_disagreement (0.15)` — but multi-template
  verification, the only producer of a disagreement score, ran *only for
  candidates already in the adversarial tier*.

Both conditions require the tier they are supposed to trigger. The adversarial
template, the contract's `adversarial_classes` on five of six relations, and the
whole `verify_multi_template` path were dead code on the first pass through any
query. §10.5 lists the adversarial tier as one of four; it was decorative.

### 15.2 The constraint on any fix

§10.5 says the adversarial tier is for a candidate that "is a common near miss
specified by the contract". Deciding that a *particular* candidate is a near
miss of a *particular* subject would require factual knowledge — forbidden on
the prediction path. A literal reading is therefore not implementable within the
challenge rules.

### 15.3 The fix

A non-factual proxy using only what the architecture already knows:

```python
if candidate.independent_support <= config.verify_max_support:
    if (
        config.adversarial_on_declared_near_misses
        and contract.verification.adversarial_classes
        and candidate.independent_support <= config.adversarial_max_support
    ):
        return VerificationTier.ADVERSARIAL_VERIFY
    return VerificationTier.VERIFY
```

Two inputs, neither factual:

1. **This relation is near-miss-prone** — Module 0's contract declares the
   classes. A property of the relation, not of the candidate.
2. **This candidate rests on the thinnest evidence** — Module 3's mechanism
   count. Support of 1 is exactly where a near miss slips through; better
   supported candidates get the ordinary verifier.

`adversarial_max_support = 1` and `adversarial_on_declared_near_misses = True`
are both configurable; setting the flag false restores the previous behaviour
while leaving the contract's classes intact.

Tests: `test_the_adversarial_tier_is_reachable_on_a_first_pass`,
`test_the_adversarial_condition_is_non_factual` (asserts the rule reads only
the contract and the support count),
`test_a_relation_without_near_misses_is_not_escalated` (`hasArea`, 0 classes),
`test_the_adversarial_template_carries_the_contract_near_misses`,
`test_the_escalation_rule_can_be_switched_off`.

**Verdict: DEFECT — fixed. The adversarial tier is now executable.**

---

## 16. Complete verification-tier matrix

| Tier | Trigger | Model calls | Reachable first pass |
|---|---|---|---|
| `HARD_REJECT` | contract type/format violation (Module 3 `apply_hard_contract_rules`) | **0** | yes |
| `AUTO_ACCEPT` | `independent_support ≥ contract auto-accept threshold`, no contradiction | **0** | yes |
| `ADVERSARIAL_VERIFY` | contract declares near-miss classes **and** `independent_support ≤ 1`; or contradiction; or disagreement > 0.15 | multi-template (3) + 1 control | yes (after fix) |
| `VERIFY` | `independent_support ≤ verify_max_support (2)` | 1 + 1 control | yes |
| `UNRESOLVED` | none of the above | 0 | yes |

`test_every_tier_is_reachable` constructs a candidate reaching each of the five
outcomes. No tier is decorative.

---

## 17. Auto-accept reachability by all six relations

The audit-0005 §22.1 concern — that some relations could never auto-accept
because their threshold exceeded the number of mechanisms able to produce a
candidate — is **resolved**. The Module-2 correction (adding the relation-focused
description and reverse/alternate families) raised `m(o)` for every relation.

| Relation | `m(o)` eligible groups | auto-accept threshold | Reachable |
|---|---|---|---|
| `awardWonBy` | 5 | 2 | yes, comfortably |
| `companyTradesAtStockExchange` | 4 | 3 | yes |
| `countryLandBordersCountry` | 6 | 2 | yes, comfortably |
| `hasArea` | 3 | 2 | yes |
| `hasCapacity` | 3 | 2 | yes |
| `personHasCityOfDeath` | 3 | 3 | yes — **requires unanimity** |

`personHasCityOfDeath` sits exactly at 3 of 3: every eligible mechanism must
support the candidate for it to skip verification. This is **deliberate, not
accidental** — the relation is `NULL_SINGLE`, where a wrong non-null answer is
worse than an abstention, so the cheapest path to acceptance is the strictest.
`test_death_requires_unanimity_by_design` pins the value so it cannot drift
silently, and `test_auto_accept_is_reachable_for_every_relation` covers all six.

---

## 18. Relation-policy precedence

Per the Module-0 policy correction (audit 0003 §3.1), `resolve_verification`
makes the **contract authoritative** and the global config a **fallback**:

| Relation | `min_valid_prob` | `drop_on_unknown` | auto-accept |
|---|---|---|---|
| `awardWonBy` | 0.60 | True | 2 |
| `companyTradesAtStockExchange` | 0.60 | True | 3 |
| `countryLandBordersCountry` | 0.50 | True | 2 |
| `hasArea` | 0.50 | True | 2 |
| `hasCapacity` | 0.50 | True | 2 |
| `personHasCityOfDeath` | 0.60 | True | 3 |

Values are **never blended**: no `max()`, no `min()`, no AND/OR. A relation may
choose a *lower* bar than the global default when recall matters more than
precision. Resource ceilings (calls, tokens) still use `min()` because a
relation must not be able to raise a hard budget.

`force_global_verification_policy` is a named, default-off override for
ablations. Tests: `test_the_contract_operating_point_is_authoritative`,
`test_thresholds_are_never_blended`,
`test_an_undeclared_value_falls_back_to_the_global_default`,
`test_the_global_override_is_named_and_off_by_default`.

**Verdict: CORRECT.**

---

## 19. UNKNOWN semantics analysis

UNKNOWN stays distinct from INVALID at all three layers:

1. **Result** — `read_labels` returns `VerificationLabel.UNKNOWN`, and
   `unknown_prob` is stored separately from `invalid_prob`.
2. **Edge** — maps to `EdgeType.UNKNOWN`, never `EdgeType.CONTRADICT`. It
   therefore does not increment `contradiction_count` and cannot trigger the
   contradiction escalation.
3. **Decision** — `decide_status` returns `UNRESOLVED`, never `REJECTED`. Under
   `drop_on_unknown` an UNKNOWN candidate is dropped from acceptance but can
   still be rescued by broad independent support.

`test_unknown_is_never_rewritten_as_invalid` asserts all three.

**Verdict: CORRECT.**

---

## 20. Hard-reject bypass analysis

A candidate violating a hard contract rule is rejected by Module 3's
`apply_hard_contract_rules` during Phase A, before any tier is assigned. The
verifier is never called on it.

Verified end to end rather than by inspection: `test_a_hard_reject_costs_no_model_call`
runs the pipeline on a `hasArea` query whose enumerator answers with an entity
("Alpha") instead of a number. The candidate is hard-rejected on type, and the
verifier runtime records **0 calls**. Confirmed non-vacuous by checking the
counter is genuinely zero rather than the verifier being absent.

**Verdict: CORRECT.**

---

## 21. Module-4 → EvidenceGraph boundary

`verification.py` does not import the graph. The pipeline (`_verify_one`) is the
only writer, and it maps verdicts to edges:

| Verdict | Edge type | Group | Mode |
|---|---|---|---|
| VALID | `SUPPORT` | `BLIND_VERIFIER` | `SHOWN_CANDIDATE` |
| INVALID | `CONTRADICT` | `BLIND_VERIFIER` | `SHOWN_CANDIDATE` |
| UNKNOWN | `UNKNOWN` | `BLIND_VERIFIER` | `SHOWN_CANDIDATE` |

All verifier evidence lands in **exactly one** independence group, so repeated
templates on one candidate cannot manufacture multiple independent supports.
`test_a_verdict_becomes_the_matching_signed_edge` is parametrized over all three
verdicts; `test_verifier_evidence_is_shown_candidate_not_independent_recall`
pins the mode.

**Verdict: CORRECT at the Module-4 boundary.** The *consumer* of this evidence
has a defect — see §34.1, deferred to Module 5.

---

## 22. Cross-model recall vs shown-candidate verification

The two are kept structurally distinct:

| | Cross-model recall | Blind verification |
|---|---|---|
| Group | `CROSS_MODEL_RECALL` | `BLIND_VERIFIER` |
| Mode | `INDEPENDENT_RECALL` | `SHOWN_CANDIDATE` |
| Prompt | the relation question, no candidate list | one specific candidate |
| Call | `generate` | `score_labels` |
| `X(o)` weight | 1.0 | `shown_candidate_weight` (0.25) |

The verifier-family model producing a name *it was never shown* is genuinely
separate evidence; agreeing with a name handed to it is anchored and cheap.
`test_cross_model_recall_and_verification_are_not_conflated` reads both modes
off a single candidate node.

**Verdict: CORRECT.**

---

## 23. Gate / calibration boundary

`score_gate` shares the calibration machinery but keeps its own label set
(`GATE_LABELS = {YES: A, NO: B, UNKNOWN: C}`) and its own control
(`CONTENT_FREE_GATE_QUESTION`) with its own cache identity (§9.1).

Only a **confident** negative closes the gate — both `margin ≥ gate_min_margin`
and `p_no ≥ gate_min_prob` must hold. An uncertain gate leaves the query open,
which is the conservative direction for a `NULL_SINGLE` relation.

Tests: `test_an_uncertain_gate_is_not_a_confident_negative`,
`test_a_confident_gate_negative_is_distinguishable`,
`test_the_gate_uses_its_own_label_set`.

**Verdict: CORRECT.** See §34.3 for a mode-dependent gate-model-identity note.

---

## 24. Staged execution analysis

Phase A (enumerate, Mistral) → persist → Phase B (cross-model recall + verify,
Qwen) → persist → Phase C (control/selection, no model).

`scripts/run_staged.py::build_pipeline` constructs **only** the runtime a phase
needs, so at most one heavyweight model is resident at a time. In `STAGED` mode
cross-model recall is deferred from Phase A to Phase B — correctly, since the
verifier model is not loaded during Phase A. `build_pipeline` prints an explicit
note rather than silently skipping it.

Verified by `test_the_verifier_model_is_untouched_during_phase_a`, which drives
all three phases through real `StageWriter` / `read_stage` round trips and
asserts:

- Phase A: verifier calls **== 0**;
- Phase B: verifier calls **> 0** (so it is deferred, not lost);
- Phase C: neither runtime's call counter moves;
- verifications survive the persist/reload cycle;
- after reload, `BLIND_VERIFIER` edges are all `SHOWN_CANDIDATE` and
  `CROSS_MODEL_RECALL` edges are all `INDEPENDENT_RECALL`.

A non-neural staged run also completes end to end:
`scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6`
exits cleanly through all three phases. That run exercises plumbing only and is
**not** a system result.

**Verdict: CORRECT.**

---

## 25. Mismatches found

| # | Severity | Description |
|---|---|---|
| 1 | **severe** | `ADVERSARIAL_VERIFY` unreachable on a first pass — both triggers required the tier they were meant to produce (§15) |
| 2 | moderate | Contextual-calibration cache key omitted model **revision** and the **label set / scoring convention** (§9.1) |
| 3 | minor | Calibrated entropy could report `−0.0` (§10.1) |

No mismatch was found in blindness, label-logit scoring, the calibration
arithmetic, `T = 1`, the disagreement formula, UNKNOWN semantics, hard-reject
bypass, verifier provenance, or the cross-model boundary.

---

## 26. Fixes made

1. **`src/cover_kbc/scoring.py`** — added `adversarial_max_support` (default 1)
   and `adversarial_on_declared_near_misses` (default True) to `ScoringConfig`;
   `assign_tier` now routes a thinly supported candidate of a near-miss-prone
   relation to `ADVERSARIAL_VERIFY`. The comment records why the literal §10.5
   reading is not implementable closed-book and why the proxy is non-factual.
2. **`src/cover_kbc/verification.py`** — `_control_cache_key` extended to
   `(model_id, revision, label_signature, relation, template_id,
   decode_identity)`; added `_label_signature`; both call sites updated.
3. **`src/cover_kbc/verification.py`** — entropy clamped to `≥ 0.0` in
   `read_labels` and `score_gate`.

No other production code was changed. No Module-5 or Module-7 algorithm was
altered.

---

## 27. Files created / modified

| File | Change |
|---|---|
| `src/cover_kbc/verification.py` | modified — cache identity, entropy clamp (+45/−7) |
| `src/cover_kbc/scoring.py` | modified — adversarial tier reachability (+24/−1) |
| `tests/test_verification.py` | modified — 3 tests updated for corrected routing, 3 added (+47/−3) |
| `tests/test_verifier_conformance.py` | **created** — 76 tests |
| `docs/audits/0007-module-4-blind-verifier-conformance.md` | **created** — this file |

`benchmark/` untouched.

---

## 28. Commands executed

```
python3 -m pytest -q
python3 -m pytest tests/test_verifier_conformance.py -q
python3 -m pyflakes src/ tests/ scripts/
python3 scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/
git diff -- benchmark/
git diff --cached -- benchmark/
git diff --stat
```

No model download, no `huggingface-cli`, no heavyweight inference.

---

## 29. Exact test results

**523 passed, 0 failed, 0 skipped** (up from 447 before this review).

| File | Tests |
|---|---|
| `tests/test_contracts.py` | 44 |
| `tests/test_controller.py` | 32 |
| `tests/test_data.py` | 26 |
| `tests/test_elicitation.py` | 63 |
| `tests/test_evaluation.py` | 13 |
| `tests/test_evidence.py` | 23 |
| `tests/test_graph.py` | 59 |
| `tests/test_normalization.py` | 59 |
| `tests/test_pipeline.py` | 31 |
| `tests/test_programs.py` | 40 |
| `tests/test_staging.py` | 17 |
| `tests/test_verification.py` | 40 |
| `tests/test_verifier_conformance.py` | **76** |

`pyflakes`: clean apart from four intentional `import _bootstrap` sys.path
shims in `scripts/`.

Three pre-existing tests in `tests/test_verification.py` changed expectations as
a direct consequence of the §15 fix, not because the tests were wrong:
`test_tier_verify_for_weak_support` moved to `hasArea` (no near-miss classes),
and `test_verification_targets_prioritise_adversarial_then_weakest` now sets
`adversarial_on_declared_near_misses=False` to keep testing ordering across
tiers, with a new companion test for ordering *within* the adversarial tier.

---

## 30. Benchmark integrity

```
$ git status --porcelain benchmark/     ->  (empty)
$ git diff -- benchmark/                ->  (empty)
$ git diff --cached -- benchmark/       ->  (empty)
```

The organizer snapshot is unmodified.

---

## 31. Challenge-compliance impact

| Constraint | Module 4 status |
|---|---|
| No model training | ✓ nothing is fitted |
| No fine-tuning / LoRA | ✓ |
| No learned verifier | ✓ label logits read from a frozen checkpoint |
| No learned calibrator | ✓ `z̃ = z − b` is deterministic arithmetic; `T = 1` fixed. Asserted by `test_no_learned_calibrator_exists` |
| No retrieval | ✓ `test_no_retrieval_reaches_the_verifier` AST-walks imports for `requests`, `urllib`, `httpx`, `aiohttp`, `socket`, `wikipedia`, `wikidata` |
| No external corpus | ✓ |
| No hidden test lookup | ✓ prompts are built from the query and the contract only |

Frozen parameter budget unchanged:

```
Mistral Small 3.2 24B    24,011,361,280
Qwen3.5-4B                4,659,865,088
------------------------------------------
total                    28,671,226,368  <  32,000,000,000
```

Module 4 adds no parameters and no new model role. Model strategy unchanged.

---

## 32. Thresholds still requiring later train calibration

The following are **design defaults, not results**. None has been fitted to
data, and none may be reported as a performance claim:

| Threshold | Default | Owner |
|---|---|---|
| `min_valid_prob` | 0.50 / 0.60 per contract | Module 0 contract |
| `auto_accept_independent_support` | 2 or 3 per contract | Module 0 contract |
| `verify_max_support` | 2 | `ScoringConfig` |
| `adversarial_max_support` | 1 | `ScoringConfig` (new) |
| `adversarial_disagreement` | 0.15 | `ScoringConfig` |
| `gate_min_margin` | 1.0 | `PipelineConfig` |
| `gate_min_prob` | 0.5 | `PipelineConfig` |
| `logit_clip` | Module 5 | `ScoringConfig` |

Calibration protocol when Colab results exist: fit on train / an internal split,
**freeze**, then evaluate val once. Do not tune on val and report that same val
number.

---

## 33. Unresolved Module-4-only issues

1. **Real-tokenizer measurement outstanding.** Whether `A`/`B`/`C` are single
   tokens under `Qwen/Qwen3.5-4B` has not been measured, because the checkpoint
   is intentionally reserved for Colab. The code handles both cases and selects
   at runtime; only the observation is missing. The first Colab run should log
   `inspect_label_encoding` output so the branch actually taken is recorded.
2. **No real-model calibration magnitude.** The size of the template bias `b_j`
   on the frozen checkpoint is unknown. Calibration is verified arithmetically
   and behaviourally on synthetic logits only.
3. **`adversarial_max_support = 1` is a judgement call.** It is the smallest
   value that makes the tier reachable. Whether 1 or 2 is the better operating
   point is an empirical question for train-split calibration.

Nothing in this list blocks Module 5. All three are measurements, not defects.

---

## 34. Future-review notes for Modules 5 / 7 / 8

Recorded, **not fixed** — these belong to modules that have not been authorized
for review.

### 34.1 Module 5 — shown-candidate agreement is counted as independent support

`Candidate.independent_support` (`g(o)`) counts every group with a supporting
edge, **including `BLIND_VERIFIER`**. `support_term` (`F(o)`) then divides that
by `contract.coverage_denominator()` (`m(o)`), which contains **only elicitation
groups** — never `BLIND_VERIFIER` or `CROSS_MODEL_RECALL`.

Two consequences, both reproduced:

- A single `SHOWN_CANDIDATE` agreement earns the same `F(o)` increment as an
  entire independent recall mechanism. For `companyTradesAtStockExchange`, one
  verifier VALID raised `F(o)` from 0.250 to 0.500. The verifier's contribution
  is then counted a third time — once in `L(o)`, once in `X(o)` at the
  deliberately discounted `shown_candidate_weight`, and once in `F(o)` at full
  weight.
- `g(o)` can exceed `m(o)`. With all mechanisms plus cross-model plus verifier
  support, `g(o) = 6` against `m(o) = 4` — a raw ratio of 1.5, silently clamped
  by `min(1.0, ...)`, so the inflation is invisible.

This contradicts the documented invariant in `EvidenceMode`: *"the two must
never be counted alike."* The discount is implemented in `X(o)` but not in the
more load-bearing `F(o)`.

Proposed fix, for Module 5 to accept or reject: `support_term` should use the
existing `Candidate.coverage(contract.eligible_independence_groups)` accessor,
which already implements §11.1's `q(o) = g(o)/m(o)` over a consistent index set
and cannot exceed 1. `Candidate.coverage` is already correct; only
`support_term` bypasses it.

Note that `assign_tier` is unaffected in the normal flow, because tiering runs
*before* verification. The exposure is in post-verification consumers:
`support_term`, `decide_status`'s `min_independent_support` floor, and the
`drop_on_unknown` rescue.

### 34.2 Module 8 — an unreachable `EmptyReason`

`_empty_reason` in `selection.py` tests `if not candidates` before
`if any(c.status is REJECTED ...)`, but `candidates` is
`graph.active_candidates()`, which already excludes rejected nodes. When every
candidate is rejected the first branch fires, so `CANDIDATE_REJECTED` is
**unreachable** and the row is reported as `NO_CANDIDATE_GENERATED`. This
conflates "the model produced nothing" with "the model produced only type
violations" — two cases the error analysis needs to tell apart. Observed in the
hard-reject probe of §20.

### 34.3 Module 7 — the calibrated gate's model identity is mode-dependent

`_run_gate` scores the existence gate with `self.verifier_runtime`. In
`INTERLEAVED` mode that is Qwen; in `STAGED` mode the verifier is not resident
during Phase A, so `verifier_runtime` falls back to the enumerator and the gate
is scored by **Mistral**. The gate closes a query and emits an empty row, so
which model makes that call is load-bearing and should be an explicit
configuration choice rather than a consequence of which runtime happens to be
loaded. Not a Module-4 defect — `score_gate` itself is correct — but it should
be settled before gate thresholds are calibrated.

---

## 35. Module 5+ remain unreviewed

Modules 5 (Evidence and Uncertainty State), 6 (Residual Coverage / RCSE),
7 (Active Controller and Adaptive Stopping) and 8 (Final Selection) have **not**
been reviewed against the proposal. Their code exists and their tests pass, but
no conformance judgement has been made about them. The findings in §34 are
observations made incidentally while reviewing Module 4; they are not a review
of those modules and are not exhaustive.

---

## 36. Recommended next review

**Module 5 — Evidence and Uncertainty State.**

It is the immediate consumer of everything Module 4 produces, and §34.1 is a
confirmed defect sitting in its code awaiting authorization to fix.

---

## Verdict

**Module 4 PASSES** against the proposal, after three defects were found and
fixed. The verifier is genuinely blind, genuinely logit-based, and calibrated
exactly as §10.3 specifies; all four tiers are now executable; the three-way
label semantics hold end to end; and no training, retrieval, or heavyweight
local inference was introduced.

Two measurements remain outstanding and are explicitly *not* faked: the real
`Qwen/Qwen3.5-4B` tokenisation of `A`/`B`/`C`, and the magnitude of the template
bias on the frozen checkpoint. Both are reserved for Colab.
