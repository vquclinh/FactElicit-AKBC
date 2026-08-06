# Audit 0025 — Module 17: Specialist Verifier Suite Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: first Layer-4 module (M17 of M9–M21).
Mode: **shadow**, **disabled by default**. M17 spends real verifier calls.

---

## 1. Objective and scope

Implement **M17 Specialist Verifier Suite**: keep COVER's audited blind
verification machinery and give every relation family its own §13 verifier
contract, scored by the existing frozen Qwen verifier role through Module 4's
calibrated kernel.

In scope: the five Table 5 contracts in one registry, relation-specialised
prompt rendering, §13.1's label-order bias controls with matched controls, the
typed request/result surface, the deterministic verifiable-target catalogue,
configuration, the observability artefact, and the Phase-C seam.

Out of scope and not implemented: M18–M21, and DoLa. No placeholder files.

**M17 decides nothing.** A/B/C is verifier evidence. No accept, no reject, no
prune, no final set, no score, no rank. §15 and §29 prove it.

---

## 2. Proposal requirements

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§13** | "A single generic A/B/C prompt is too coarse for all relations. M17 preserves CoVe's blind-verification invariant, but **each relation gets its own verifier contract**." |
| **§13 Table 5** | Five specialists, each with a verifier question and a typical hard negative — reproduced verbatim in §8 below. |
| **§13** (closing) | "Generator quality and verifier quality need not coincide… model-role bake-offs must measure enumeration and verification separately." Noted; **not this milestone** (§35). |
| **§13.1** | "In addition to contextual calibration, test **template-order/label bias and label-order swaps in calibration controls**… COVER avoids free-form judge scores and uses fixed labels, but still **logs template disagreement**." |
| **§14, §14.1** | M18's reverse/key-condition/counterfactual/candidate-free mechanisms, and DoLa as M18's *optional* adapter with an explicit warning not to apply it where it changes calibration semantics. Neither implemented (§36, §37). |
| **§10.2–10.4** | Module 4's blind invariant, contextual calibration and prompt-disagreement mathematics — reused, not re-derived. |
| **§10.3** | `E_null`'s three classes, which fix why a query-level proposition is not a candidate (§27). |
| **§12** | M16's consensus state, read-only here. |
| **Appendix C** | "M17 Specialist Verifier \| candidate + specialist contract \| **label distributions + verdict** \| Neural: **Yes**." |

Prior audits read: **0007** (M4), **0008** (F/L/X/C/U), **0012** (M0–M8),
**0021**+**0024** (M14 null semantics), **0022** (M15 §17A), **0023** (M16).

### Interpretations recorded rather than resolved silently

**1. "Verdict" in Appendix C means the verifier's label, not a system decision.**
Appendix C lists M17's output as "label distributions + verdict", and §15 of the
brief forbids a system-level ACCEPT/REJECT. The reading implemented:
`argmax_label` is the verifier's own most-likely label — literally its output —
and no field converts it into an accept, a rejection or a prune. There is no
`system_decision` anywhere.

**2. Two phrasings per family, not many.** §13.1 requires template disagreement
to be *logged*, which needs at least two semantically equivalent phrasings.
Module 4 made the same call for the same reason (verification is the expensive
half of the budget), so M17 matches it rather than inventing a larger bank.

**3. Two label orders by default, three declared.** `LabelOrder` declares ABC,
BAC and CAB; the shipped default measures ABC and BAC. One swap is the minimum
that makes positional bias measurable, and each order costs its own control
call. CAB is available by configuration for a deeper probe.

**4. Bias is measured, never corrected beyond contextual calibration.** §13.1
asks for these to be *logged*. A fitted bias correction would be a trained
component, which the challenge rules forbid.

**5. The prompt carries the contract's hard-negative *classes*, never a claim
about this candidate.** Module 4's adversarial template says "this candidate is
suspected of being a near miss"; that sentence tells the verifier what the
acquisition layer suspects, so M17 does not reuse that template and passes
`near_misses=""`. The relation's own exclusions still reach the verifier,
because `verifier_definition()` already embeds them.

---

## 3. Architecture position

```
    M16 consensus state (READ-ONLY)
            |
            v
    deterministic verifiable-target catalogue        <- zero calls
            |
            v      the CALLER chooses which targets to request
    SpecialistVerificationRequest
            |
            v
    Module 4 kernel: score_labels -> control -> read_labels
            |            (frozen Qwen verifier role, T = 1)
            v
    SpecialistVerificationResult  ->  specialist_verification.jsonl

    M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8   (unchanged production path)
```

M17 runs at the Phase-C seam, after M16. The pipeline builds **only the
catalogue**; actual readings require an explicit caller (§29).

---

## 4. Files changed

The verification module became a package. **Module 4's code was moved, not
edited**: `verification.py` → `verification/blind.py`, byte-for-byte apart from
one self-import path, and its prompt surface still hashes to
`3acd7109…e6d874`. `verification/__init__.py` re-exports every Module 4 name, so
all 22 existing import sites mean exactly what they meant before.

New:

| File | Lines | Contents |
| --- | --- | --- |
| `src/cover_kbc/verification/__init__.py` | 117 | Module 4 re-exports; lazy M17 exports. |
| `src/cover_kbc/verification/specialist_types.py` | 560 | Families, target kinds, label orders, request/result, bias diagnostics. |
| `src/cover_kbc/verification/specialist_contracts.py` | 335 | Table 5, one declarative registry, consistency check. |
| `src/cover_kbc/verification/specialist_prompts.py` | 247 | Rendering, label-order block, the blindness boundary. |
| `src/cover_kbc/verification/specialist_verifier.py` | 639 | Catalogue, request building, execution, bias diagnostics. |
| `tests/test_specialist_verifier.py` | 1480 | 96 tests. |
| `docs/audits/0025-…md` | this file | — |

Modified: `pipeline.py` (optional verifier, catalogue seam, explicit
`verify_specialist_targets`), `run_staged.py`, `run_cover.py`, 3 configs,
`tests/test_verifier_conformance.py` (§40).

`benchmark/` untouched.

---

## 5. Public M17 types

Enums: `SpecialistVerifierFamily` (5), `VerificationTargetKind` (3),
`QueryPropositionKind` (3), `LabelOrder` (3), `TargetIneligible` (3). Records:
`VerificationTarget`, `SpecialistVerificationRequest`,
`SpecialistTemplateResult`, `VerifierBiasDiagnostics`,
`SpecialistVerificationResult`, `QuerySpecialistVerificationResult`,
`SpecialistVerifierContract`. All frozen, all round-tripped for all six
relations.

No field is named `accepted`, `rejected`, `final`, `decision`, `score`, `rank`,
`prune` or `verdict` — asserted against `__dataclass_fields__`.

---

## 6. Module 4 reuse

M17 calls; it does not copy.

| Primitive | Owner | How M17 uses it |
| --- | --- | --- |
| `LABEL_TOKENS` (A/B/C) | M4 | imported; the meanings are never restated |
| `runtime.score_labels` | M4/runtime | the only scoring path |
| `ContextualCalibrator` | M4 | control measurement **and** cache, unmodified |
| `read_labels` | M4 | control subtraction, softmax, margin, entropy |
| `normalized_disagreement` | M4 | both §13.1 disagreement readings |
| `VerifierTemplate` | M4 | a specialist template **is** one, which is what lets the calibrator render its own matched control with no new code |

A test AST-parses the imports and asserts all four are taken from
`verification.blind`; another scans for `def softmax`, `math.exp`, `def _kl`,
`def entropy`, `jensen_shannon`, `logsumexp`, `- control.get` and `def apply(`
and fails on any.

**The smallest possible change to M4: none.** Making specialist templates
ordinary `VerifierTemplate` values meant the calibrator needed no new parameter,
and `_control_cache_key` already keys on `(model, revision, label signature,
relation, template_id, decode identity)` — so encoding the family, the phrasing
and the label order into the template id gives every cache separation §26
requires for free.

---

## 7. A/B/C semantics

Identical to Module 4's, from the same constant:

* **A = VALID** — the target satisfies the exact specialist contract;
* **B = INVALID** — it contradicts or fails it;
* **C = UNKNOWN** — the verifier cannot establish either reliably.

UNKNOWN is **epistemic verifier uncertainty**. It is not "probably false", not
"not mentioned", not "low support", and it is never treated as INVALID. The
system prompt tells the model so in as many words: *"Choose UNKNOWN when you
cannot establish either answer reliably — it is not a weaker way of saying
INVALID."* An M17 UNKNOWN is also **not** Module 14's `FAILED_RECALL_ONLY`:
those are different evidence types produced by different modules, and a test
asserts no null-evidence vocabulary appears in an M17 payload.

---

## 8. The registry

One declarative table. A test scans the prompt, verifier and types modules for
every relation name and fails on any hit; the registry is the only module that
may name one.

| Family | Relations | Table 5 question | Boundary (hard-negative *classes*) |
| --- | --- | --- | --- |
| `NUMERIC` | `hasCapacity`, `hasArea` | "Does the value satisfy the exact quantity the definition asks for, for this subject?" | a different quantity, configuration, period or scope than the definition's |
| `AWARD_MEMBERSHIP` | `awardWonBy` | "Is the candidate a recipient of this exact award?" | only considered for it; a work rather than the recipient; a different award however similar; an award that does not stand |
| `NULL_TEMPORAL` | `personHasCityOfDeath` | "Do the existence, death-status and locality conditions in the definition hold?" | a place associated for another reason; wrong kind or granularity; a status making the relation inapplicable |
| `STOCK` | `companyTradesAtStockExchange` | "Is the subject **company itself** listed on the candidate exchange?" | a related company's listing; an index or non-exchange venue; a listing that has ended; not publicly listed |
| `BORDER` | `countryLandBordersCountry` | "Is there **physical land contact**?" | a sea boundary only; proximity without contact; contact only through an excluded territory |

`check_specialist_registry_consistency()` runs at import and cross-checks
Modules 0 and 1: every official relation routed, exactly five families, a
NUMBER relation only in the numeric family (and never with an entity target
kind), and query propositions only where Module 1 routes to `NULL_SINGLE`.

## 9–13. Per-family conformance

Each family's §13 question and boundary are asserted, and for each the
relation's **own** `hard_negative_rules` are asserted present in the rendered
prompt — so the exclusions the verifier sees are Module 0's, not a second
restatement. Border verification never asks a reverse question: §11.1's reverse
check is Module 18's, and a scan of the code and the prompts enforces it.

---

## 14. Verification target kinds

Three, deliberately not one:

| Kind | Identity | Shown to the verifier |
| --- | --- | --- |
| `ENTITY_CANDIDATE` | Module 3/16 **strict key** — never `alias_hint` | the display surface |
| `NUMERIC_CLUSTER` | cluster index | Module 12's representative and canonical unit, e.g. `25000 persons` |
| `QUERY_PROPOSITION` | proposition name | a plain statement about the subject |

A query-level proposition is **not** encoded as a fake candidate: a test asserts
no `__EMPTY__`, `NONE`, `LIVING` or `DECEASED` key appears in the entity target
set, which is what preserves Modules 14/16's candidate-versus-null separation.

---

## 15–16. The blind-input boundary

A specialist prompt carries the subject, `contract.verifier_definition()`, the
target, the §13 question and boundary, and the fixed labels. Nothing else.

Tested across every relation × phrasing × label order for absence of: `F=`,
`L=`, `X=`, `C=`, `U=`, `I=`, `D=`, `d_semantic`, `independent_support`, support
count, supporting groups, independence group, consensus, "strong consensus",
"contested", pseudo-memory, parametric memory, facet, cluster, dispersion,
closure, listing gate, near miss, "near-miss detected", risk flag, pending
check, "the generator", rationale, chain of thought, "step by step", "explain
your", reasoning.

**The poison test.** A rationale — *"The generator is 99% sure Candidate Alpha
is correct."* — is stored on the consensus provenance exactly where a real run
would put it, together with a `NEAR_MISS_MENTION` risk flag and a `HIGH` risk
grade. Every prompt M17 then renders is asserted free of the sentence, of
`99%`, of "generator", of `HIGH` and of `NEAR_MISS`.

`SpecialistVerificationRequest` has exactly six fields — target, family,
contract version, template ids, label orders, verification version — and
`VerificationTarget` has no field whose name contains support, score, consensus,
risk, evidence, rationale, facet or group. The boundary is structural, not
editorial.

---

## 17. Prompt rendering

Structure and rendering are separate: the contract is a dataclass, the renderer
consumes it, and no downstream code parses English to rediscover semantics. The
prompts are short, ask for no explanation and no chain of thought, and end in
the fixed label block — the surface being scored is the label, not prose.

---

## 18–19. Contextual calibration and control-cache identity

Module 4's calibrator, unmodified, at its audited `T = 1`. Nothing is fitted:
the calibrated logits are asserted to equal `raw − control` exactly, and a scan
rejects `fit(`, `Platt`, `isotonic`, `LogisticRegression` and `temperature=`.
The control carries the template and the contract but no subject and no
candidate — asserted — so what it measures is template bias.

Because a specialist template *is* a `VerifierTemplate` with a fully-qualified
id `m17:<family>:<phrasing>:<order>`, `_control_cache_key` separates:

| Boundary | Separated by | Test |
| --- | --- | --- |
| label order | template id suffix | three orders → three distinct keys |
| relation / family | `relation` + template id | award / stock / death → three keys |
| generic M4 vs specialist M17 | `m17:` prefix | keys differ; specialist id starts `m17:` |
| model, revision, label set, decode | already in the key | Module 4's audited design |

Calibration is honest about cost: the first candidate for a relation pays 4
readings + 4 controls = 8 calls; the second pays 4, with every reading flagged
`control_cache_hit=True`. A cache hit performs no inference and is never charged.

---

## 20–23. Label orders, bias diagnostics and the three disagreements

`LabelOrder` changes **presentation order only** — A is VALID, B is INVALID and
C is UNKNOWN in every variant, asserted for all three. Each variant carries its
own matched control, and a test reads the four control prompts back and asserts
their label blocks are `A,B,C` and `B,A,C`: no order's bias is ever subtracted
from another's.

Three *different* instabilities, kept apart:

| Reading | Varies | Holds fixed | Owner |
| --- | --- | --- | --- |
| `template_disagreement` | phrasing | label order | M17 |
| `label_order_disagreement` | label order | phrasing | M17 |
| `d_semantic` | — | — | **M16** |

Both M17 readings reuse Module 4's `normalized_disagreement`; a test recomputes
the label-order figure from the per-reading distributions and asserts exact
equality. `max_valid_shift` records the largest swing in P(VALID) across orders.
`VerifierBiasDiagnostics` has no `d_semantic` field and
`CandidateConsensusState` has no `template_disagreement` field — the three
cannot be collapsed by accident.

With an injected order bias: `label_order_disagreement > 0`,
`template_disagreement == 0.0`, `max_valid_shift > 0`. The diagnostic
distinguishes what moved.

---

## 24–25. Aggregation and availability

Per-template, per-order calibrated distributions are all retained. The summary
is a plain deterministic **mean** — Module 4's own aggregation — never a
majority vote over argmax labels and never a fitted mixture (`Counter(`,
`most_common`, `majority`, `vote` are scanned for and absent).

`available` is a first-class field. `available=False` means *no usable reading
was obtained*, which is a different state from a confident UNKNOWN, and a test
contrasts the two directly. When nothing was usable, `mean_distribution` is
`None` rather than a uniform stand-in.

---

## 26–27. Numeric and null boundaries

**Numeric.** The verifier sees Module 12's representative and unit and nothing
about the cluster: `cluster`, `dispersion`, `support`, `median`, `dominant` and
`independent` are all asserted absent from the prompt. M17 neither reclusters
nor rounds — a scan rejects `cluster_values`, `median`, `relative_distance` and
`tolerance`.

**Null.** Query propositions read as plain statements — "The subject is
living.", "There is no locality of the kind this relation asks for that is known
for the subject." — with no `Candidate:` line. A label on one of these is
evidence about a proposition; the payload is asserted free of `final_empty`,
`accepted_empty`, `no_known_locality_support` and `ObjectEntities`. The
proposition template and the candidate templates refuse to be swapped.

---

## 28–29. Hard-contract skip and the scheduling boundary

A target Module 3/16 already marked as a hard contract violation is `eligible =
False` with `HARD_CONTRACT_VIOLATION`, and verifying it spends **zero calls**
and produces no reading. It is a *skip*, not a rejection: the payload contains
no `rejected`, `pruned` or `accepted`. A candidate with no printable value is
`NO_PRINTABLE_VALUE`.

Eligibility and scheduling are different questions and different fields.
`verifiable_targets` reads no support count, no `F`, no `I`, no `D` and no risk
flag — a test gives it a strongly-supported and a barely-supported candidate and
asserts **both** are eligible, then scans the verifier module for `.f_support`,
`.i_independent_support`, `.d_semantic`, `.risk_flags`, `.l_logit`, `.u_prompt`,
`should_verify`, `select_targets`, `budget` and `expected_value`.

---

## 30–31. Call accounting and the shadow seam

Every call is explicit and attributed: model id and revision, prompt sha256,
template id, phrasing id, label order, cache hit, calls, tokens, latency, error.
`result.calls` is asserted equal to the runtime's own counter — 8 on a cold
cache, 4 on a warm one.

The pipeline seam builds the **catalogue only**. A test asserts the verifier
runtime's counter does not move across `decide_graph`, that
`record.results == ()` and `record.calls == 0`. There is no automatic
verify-all fan-out for `awardWonBy` or anything else: that would be an implicit
budget policy shipped four modules before Module 20.

An explicit caller uses `pipeline.verify_specialist_targets(...)`; its spend
joins the shadow counters Module 11 established and never Module 7's per-query
budget, asserted against `prediction.calls_used`.

---

## 32–33. Persistence and error handling

`specialist_verification.jsonl`, one row per query, in manifest order,
containing the version, query identity, family, contract version, the whole
catalogue (including targets skipped without a call), and any results. No gold,
no `ObjectEntities`, no accepted/rejected, no prediction, no `should_stop`.

Failures are distinguished and never repaired: identity mismatch, unsupported
relation/family/target kind, ineligible target, unknown template, unknown label
order, malformed config, runtime failure, incomplete logits, control failure.
A failed call yields **no distribution and no raw logits**; incomplete logits
are refused rather than softmaxed; and a partial failure keeps the surviving
readings, recomputes the mean over them, and honestly reports
`label_orders_measured == 1` with `max_valid_shift = None`.

---

## 34–38. Why the boundaries hold

**No free-form judge score** (§13.1): the surface scored is a fixed label set,
and no field holds a numeric self-rating or a rationale string.

**No model-role bake-off** (§13 closing): the proposal asks for enumeration and
verification to be measured *separately in a future bake-off*. The model profile
is frozen at Mistral-Small-3.2-24B + Qwen3.5-4B = 28.67B; M17 introduces no
model and no parameter, and a subprocess test asserts `torch`/`transformers`
are never imported.

**No M18**: `reverse_check`, `counterfactual`, `key_condition`, `reconstruct`,
`candidate_free`, `adversarial_pair` all absent.

**No DoLa**: `dola`, `early_exit`, `layer_contrast`, `premature_layer` absent.
§14.1 places it under M18 and warns against applying it where it changes
calibration semantics — which is exactly the A/B/C path M17 uses.

**No M19–M21**: `residual`, `missingness`, `saturation`, `allocate_budget`,
`schedule`, `next_action`, `expected_value`, `should_stop`, `STOP` absent.

---

## 39. M16 read-only proof

A consensus result is deep-compared before and after a verification, and the
code is scanned for `candidate.status`, `score_breakdown`, `add_evidence`,
`add_verification`, `graph.` and `EvidenceGraph`. A separate test builds the
same consensus twice around constructing M17 and asserts equality.

## 40–42. Prior-audit regressions

**Audit 0007.** Module 4's prompt surface still hashes to
`3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874`, and
`verify_candidate` still works unchanged.

Four Audit-0007 tests used `inspect.getsource(cover_kbc.verification)`. After
the package split that returns the `__init__`, which would have made three of
them — all *absence* assertions — pass **vacuously**; one failed loudly and
exposed it. All four now inspect `cover_kbc.verification.blind`, which is where
the code they audit lives. This is the only test change the split required, and
it restores rather than relaxes the guarantees.

**Audit 0008.** The F/L/X/C/U matrix is re-asserted inside M17's suite: a
shown-candidate verifier edge moves neither `F` nor `X`, and `BLIND_VERIFIER` is
not an acquisition group.

**Audit 0022 §17A** and **Audit 0024** are both re-asserted here: M14's
cross-family rationale is unchanged, and `UNKNOWN` remains an epistemic
abstention that asserts no relation-level absence.

## 43. Tests

```
python -m pytest -q
2015 passed, 3 skipped in 16.05s
```

M17's suite: **96 tests**, covering the brief's 75 numbered requirements.
Verification-layer totals: M4 conformance 76, M17 96. Repository 1919 → 2015.

Two of my own errors were caught while writing them: a scan token `l_logit` that
matched inside `control_logits`, and a config comment containing the word
"DoLa", which tripped Audit 0012's scan forbidding the word anywhere in the
target config. The comment was reworded rather than the audited scan weakened.

## 44. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

## 45. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  Qwen/Qwen3.5-4B [verifier]   4.660B      Mistral-Small-3.2-24B  24.011B
  total: 28.67B    RESULT: PASS
```

## 46. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact.

## 47. No TRAIN / VAL / TEST use

No split was read and no metric computed. M17 has no threshold and no weight —
a test asserts no config field name contains `threshold`, `weight`, `alpha`,
`beta`, `temperature`, `min_` or `max_`. All fixtures are scripted and
fictional; no real model was loaded.

## 48. Challenge compliance

* **Closed book.** The verifier is told it has no access to search or documents.
  No `requests`, `httpx`, `urllib`, `socket`, `sklearn`, `torch`, `transformers`
  in any M17 module, and no `wikipedia`, `wikidata`, `http://`, `https://` or
  `api_key` in the code.
* **No training.** No fine-tuning, LoRA, learned calibrator or fitted bias
  correction. Calibration is arithmetic on inference-time outputs at `T = 1`.
* **Frozen model profile.** The existing verifier role, 28.67B total, no third
  model, no checkpoint change.
* **Reproducible.** Prompts are deterministic; the catalogue is deterministic;
  results round-trip.

## 49. Non-goals — M18–M21 absent

| Module | Absent because |
| --- | --- |
| M18 Bidirectional/Counterfactual | §36 — no reverse, key-condition, counterfactual or candidate-free mechanism |
| DoLa adapter | §37 — §14.1 places it under M18 and warns about calibration |
| M19 Coverage/Missingness | §38 |
| M20/M21 Budget / Micro-planner | §38 — and §29's eligibility/scheduling split exists to keep it that way |

## 50. Verdict

**PASS.**

M17 implements §13's five specialist contracts as one declarative registry and
§13.1's bias controls as matched-control label-order swaps, on top of Module 4's
kernel rather than beside it — the calibrator, the softmax, the margin, the
entropy and the divergence are all called, and a specialist template *is* a
Module 4 template, which is what makes every control cache boundary hold with no
change to Module 4 at all.

The blind-verification invariant is enforced structurally: the request type has
no field for acquisition evidence, and the poison test proves a generator
rationale sitting in upstream provenance never reaches the prompt.

Nothing here is a decision. A/B/C is evidence; M17 verifies what the caller
asks for, catalogues what it could verify, and spends nothing on its own.

Not committed. Not pushed.
