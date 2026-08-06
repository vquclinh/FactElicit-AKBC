# Audit 0026 — Module 18: Bidirectional and Counterfactual Verification

Status: **PASS**
Date: 2026-08-06
Milestone: second Layer-4 module (M18 of M9–M21).
Mode: **shadow**, **disabled by default**. Each executed check spends a real call.

---

## 1. Objective and scope

Implement **M18 Bidirectional and Counterfactual Verification**: §14's four
mechanisms for *creating genuinely new evidence*, each asking a structurally
different question, executed only when a caller explicitly asks.

In scope: the four mechanisms, the relation/check registry, prompt rendering,
bounded parsing, the eligible-check catalogue, Module 15 pending-check
consumption, cross-model provenance, configuration, the observability artefact,
and the Phase-C seam.

Out of scope and not implemented: M19–M21, and §14.1's optional DoLa adapter
(§42). No placeholder files.

**M18 decides nothing.** No accept, reject, rank, prune, stop, final set or
score. A mismatch is evidence; an absence is not a contradiction; a failed call
is not support.

---

## 2. Proposal §14 / §14.1 mapping

§14, quoted in full and mapped:

| §14 bullet | Implementation |
| --- | --- |
| *"M18 creates genuinely new evidence instead of issuing a generic 'think again' instruction."* | Four structurally distinct frames; a test scans for "think again", "review your", "reconsider", "are you sure", "double-check", "reflect on", "previous answer", "try again", "self-correct" and fails on any. |
| *"**Reverse check**: candidate o → subject s when the relation supports a meaningful reverse question."* | `REVERSE`, declared **only** for `countryLandBordersCountry` (§10). The candidate is placed in the subject position and the subject asked about. |
| *"**Key-condition reconstruction**: mask the subject/candidate condition and ask the model to recover it; use the resulting consistency signal."* | `KEY_CONDITION`. The target is **not shown**; the model reconstructs it and the recovered value is compared under strict identity. |
| *"**Counterfactual pair**: compare a true-looking candidate against a near-miss class **generated from the contract, not from external facts**."* | `COUNTERFACTUAL`. The class *is* one of `contract.hard_negative_rules`, rendered verbatim (§15–16). |
| *"**Candidate-free recall**: do not show the candidate; if it appears naturally in an independent probe, **increase X**."* | `CANDIDATE_FREE_RECALL`. The renderer takes no candidate at all. On "increase X" see §24 — this is the one place the proposal and Audit 0008 need reconciling, and the reconciliation is recorded rather than chosen silently. |

§14.1 read in full; see §42.

Prior audits read: **0006** (identity), **0008** (F/L/X/C/U), **0012** (M0–M8),
**0022** (M15 §17A), **0023** (M16), **0024** (M14 NULL), **0025** (M17).

### Interpretations recorded rather than resolved silently

**1. "Increase X" versus Audit 0008's X.** §14 says a candidate-free recall
that names the candidate naturally should increase `X`. Audit 0008 froze `X` as
*genuinely independent **cross-model** recall* (`CROSS_MODEL_RECALL` +
`INDEPENDENT_RECALL`), deliberately excluding same-family probes. A literal
reading of §14 would credit `X` for a same-family probe and contradict that.

Resolved as the brief directs — **preserve the audited channel semantics and
record the interpretation**. M18 credits no `X` at all. It records the three
facts that decide the question — `candidate_shown`, the model family, and
`cross_model_eligible` — and leaves the crediting to the Layer-4 integration.
`cross_model_eligible` is true only when the candidate was hidden *and* the
answering family differs from the family that produced the candidate. Nothing
else in M18 can ever qualify, and the record type **raises** if a shown-candidate
record is marked eligible.

**2. Counterfactual classes are contract rules, addressed positionally.** A
class id is `hn<index>` into `contract.hard_negative_rules`, and the prompt
quotes that rule verbatim. This means M18 writes no near-miss prose at all,
which is the strongest available form of "from the contract, not from external
facts". `EXPECTED_HARD_NEGATIVES` pins each relation's rule count so a
reordering in Module 0 fails loudly instead of silently shifting class ids.

**3. Reverse is declared for one relation.** §14 conditions it on "the relation
supports a meaningful reverse question". Physical land contact is symmetric, so
the same contract answers in both directions. Asking an exchange to list its
companies, an award to list its recipients, or a city to list who died there is
unbounded open-set *acquisition* wearing a verification label. Each refusal
carries a recorded rationale, and the registry check asserts the reversible set
is exactly `{countryLandBordersCountry}`.

**4. The candidate-free probe is one probe per query.** Being candidate-free, it
does not vary per candidate, so cataloguing one per candidate would suggest N
calls for one question. The single check carries the query's known candidate
keys for **post-hoc** comparison; the renderer's signature takes only
`(profile, contract, subject)`, so no key can reach a prompt.

---

## 3. Architecture position

```
    M16 consensus + M17 catalogue + M15 pending checks   (all READ-ONLY)
            |
            v
    eligible-check catalogue                       <- zero neural calls
            |
            v      the CALLER chooses which checks to execute
    BidirectionalCheckRequest -> frozen runtime -> BidirectionalCheckRecord
            |
            v
    bidirectional_verification.jsonl   ->  [Layer-4 integration, then M19-M21]

    M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8          (unchanged)
```

---

## 4. Files changed

New:

| File | Lines | Contents |
| --- | --- | --- |
| `verification/bidirectional_types.py` | 653 | Kinds, outcomes, targets, requests, records, origin identity. |
| `verification/bidirectional_contracts.py` | 383 | The per-relation check registry and its consistency check. |
| `verification/bidirectional_prompts.py` | 260 | Four renderers; the blindness boundary. |
| `verification/bidirectional_verifier.py` | 795 | Catalogue, parsing, execution, cross-model provenance. |
| `tests/test_bidirectional_verification.py` | 1491 | 132 tests. |
| `docs/audits/0026-…md` | this file | — |

Modified: `pipeline.py` (optional verifier, catalogue seam, explicit
`execute_bidirectional_checks`), `run_staged.py`, `run_cover.py`, 3 configs.
No M0–M17 module was changed. `benchmark/` untouched.

---

## 5–6. Public types and the request/result contract

Enums: `BidirectionalCheckKind` (4), `CheckTargetKind` (3), `CheckIneligible`
(6), `CheckParseStatus` (6), `ReverseOutcome` (3), `ReconstructionOutcome` (3),
`CounterfactualOutcome` (4), `RecallOutcome` (4). Records: `CheckTarget`,
`PendingCheckOrigin`, `EligibleCheck`, `BidirectionalCheckRequest`,
`RecalledCandidate`, `BidirectionalCheckRecord`, `QueryBidirectionalResult`.

A request carries identity and presentation: target, kind, template, model role,
sample index, decode identity, versions. A record adds model/revision/family,
prompt hash, origin id, raw output, parse status, the mechanism's outcome,
recalled candidates, the three cross-model provenance flags, calls, tokens,
latency and error.

No field is named `accepted`, `rejected`, `final`, `decision`, `score`, `rank`,
`prune`, `verdict` or `stop` — asserted against `__dataclass_fields__` — and the
serialised payload is scanned for the same plus `gold`, `ObjectEntities`,
`residual` and `budget`.

`RecalledCandidate` and `BidirectionalCheckRecord` both **raise** on
`verified=True`: M18 acquires and contrasts, it does not establish truth.

---

## 7. Eligibility versus scheduling

`eligible_checks` answers *can this be posed* — does the relation declare the
mechanism, is the target kind right, is there a printable value, does Module 3
already rule the candidate impossible. It reads **no** `F`, `I`, `D`, `L`,
verifier label or risk flag; a test gives it a strongly- and a barely-supported
candidate and asserts both get the identical check set, then scans the module
for `.f_support`, `.i_independent_support`, `.d_semantic`, `.risk_flags`,
`.l_logit`, `budget`, `expected_value`, `next_action` and `should_stop`.

Whether a check is *worth* a call is Module 20/21's, and the caller supplies the
requests.

## 8. The registry

One declarative table; a test scans the prompts, verifier and types modules for
every relation name and fails on any hit.

| Relation | Reverse | Key condition | Counterfactual | Candidate-free | Target kind |
| --- | --- | --- | --- | --- | --- |
| `countryLandBordersCountry` | **yes** | yes | yes (6 classes) | yes | entity |
| `companyTradesAtStockExchange` | no | yes | yes (5) | yes | entity |
| `awardWonBy` | no | yes | yes (5) | yes | entity |
| `personHasCityOfDeath` | no | yes | yes (5) | yes | entity |
| `hasCapacity`, `hasArea` | no | yes | yes (5 each) | yes | numeric cluster |

The consistency check cross-references Module 0 (output type, rule counts),
Module 1 and Module 17 (family agreement), and requires a recorded rationale for
every reverse decision in both directions.

## 9–10. Reverse check and its applicability

The border reverse prompt places the candidate in the subject position —
`Take "Country Beta" as the subject.` / `does "Country Alpha" satisfy this
relation for it?` — and uses its own bounded vocabulary
(`SUPPORTED`/`CONTRADICTED`/`UNRESOLVED`), so it is not a renamed Module 17
prompt and contains no `A = VALID`. Requesting a reverse check where the
registry refuses it raises, and the renderer raises independently.

§11.1's minimal-change rule survives: three border candidates yield three
*possible* reverse checks and executing one costs exactly one call.

## 11. Module 15 pending checks

M15's descriptors arrive through Module 16 and are attached to the checks they
motivated as `requested_by` — source module, kind, reason, detail. A
`REVERSE_ADJACENCY` descriptor motivates the reverse check; a non-reverse
descriptor does not. **None of it reaches a prompt**: a test executes the check
and asserts `SINGLETON`, `REVERSE_ADJACENCY`, `M15` and the detail text are
absent from prompt and system prompt alike. M15 itself is unchanged.

## 12–13. Key-condition reconstruction

The target is masked; the model is asked to produce the object from the subject
and the contract. No frame mentions a previous answer, and a test scans the
rendered prompts for "your answer", "previously", "again", "confirm", "verify
that" and "reconsider".

Comparison is strict: Module 3's `strict_key` for entities and Module 12's own
`canonicalise` + `format_numeric` for quantities — `25,000` and `25000 persons`
both recover the target, `61000` recovers a different value. A test scans for
`cluster_values`, `_relative_mad`, `dominant_cluster`, `relative_distance`,
`tolerance` and `0.05`: there is no second clustering rule and no tolerance.
`"The Recipient Alpha"` does **not** match `"Recipient Alpha"` — Audit 0006's
decision, upheld.

A different recovered value is `DIFFERENT_VALUE_RECOVERED`, and the payload
contains no rejection vocabulary.

## 14–16. Counterfactual pair

The prompt shows the candidate and exactly one excluded case, quoted verbatim
from `contract.hard_negative_rules`, then offers `TARGET` / `EXCLUDED` /
`NEITHER` / `UNKNOWN`.

All ten required classes render, each asserted by its own contract text:
award nominee and winning work; capacity versus record attendance; area total
versus land-only; death city versus birth/residence; stock parent, subsidiary
and index; border maritime-only and merely-nearby.

**M18 writes no near-miss prose.** A scan of its executable code for "nominee",
"attendance", "land area", "maritime", "subsidiary", "birthplace", "residence",
"delisted", "stock index", "shortlisted", "rescinded" and "privately held" finds
nothing — every one of those words reaches a prompt only from Module 0.

The prompt never states what upstream suspects: "suspect", "flagged",
"probably", "likely to be", "the system", "thinks", "detected" and "believes"
are all absent. The outcome enum is deliberately not A/B/C, and M18 imports
none of Module 4's kernel (`LABEL_TOKENS`, `score_labels`, `read_labels`,
`ContextualCalibrator`, `VerificationLabel` all absent), so calibrated verifier
evidence and adversarial evidence can never be confused.

## 17–20. Candidate-free recall

The strictest frame. `render_candidate_free`'s signature is
`(profile, contract, subject)` — there is no candidate parameter, so a leak
would have to be added deliberately. Tests assert no display value from any
relation appears in any candidate-free prompt.

**The poison test.** Five upstream signals — a 99 %-confidence generator
sentence, `M17 returned VALID`, `M16 independent_support = 5`, `M15 suspects a
subsidiary`, `risk = HIGH` — are placed in the consensus provenance and in a
Module 15 pending descriptor. All three candidate-bearing mechanisms are then
executed, and every prompt and system prompt is asserted free of all five plus
`99%`, `generator`, `independent_support`, `HIGH`, `PARENT_SUBSIDIARY`,
`suspects` and `VALID`.

New candidates are preserved with provenance, keyed by `strict_key`, marked
`verified=False`, and inserted **nowhere**: a test asserts the evidence graph
and the consensus result are byte-identical afterwards and that
`add_entity_mentions`, `add_evidence`, `EvidenceGraph` and
`CandidateConsensusState(` appear nowhere in M18.

Abstention safety reuses Module 3's own predicate. An empty recall is
`NOTHING_RECALLED`, an abstention is `NOTHING_RECALLED`/`ABSTAINED`, and a
malformed numeric answer is `NUMERIC_PARSE_FAILED` — none of them a
contradiction.

Two parser defects were found by these tests and fixed:

1. **A multi-line list beginning with `NONE` was discarded wholesale.**
   `is_abstain` normalises `"NONE\nUNKNOWN\nCity Beta"` to `"none unknown city
   beta"`, whose first word is `none` and whose length is ≤ 24, so the whole
   recall read as an abstention and `City Beta` was lost. Module 3 applies that
   predicate *per surface*; M18 now does too, and only the single-value numeric
   branch tests the whole answer.
2. **Punctuation-only output read as an abstention.** `"!!!"` normalises to the
   empty string, which `is_abstain` accepts. An answer that normalises to
   nothing is unreadable, not "no object" — it is now `MALFORMED`.

## 21. Audit 0024 regression

Re-asserted inside M18's suite over all four entity relations: `NONE` and
`UNKNOWN` never become a candidate on the recall path while a real locality in
the same answer still does; and `is_epistemic_abstention("UNKNOWN")` still
asserts no relation-level absence.

## 22–23. Independence groups, origins and cost

One stable group per mechanism: `M18_REVERSE`, `M18_KEY_CONDITION`,
`M18_COUNTERFACTUAL`, `M18_CANDIDATE_FREE_RECALL`. Three samples of one
mechanism are three origins, three calls and **one** group. Three different
counterfactual classes are likewise one group — a class id is provenance, not a
second structural source.

`derive_check_origin_id` mirrors Module 16's formula: deterministic, no UUID, no
clock. A new M18 call is a new origin and never reuses an upstream one. One
output naming four candidates is **one** origin and **one** call with four
observations.

## 24. X / cross-model eligibility

| Mechanism | candidate shown | independent recall | cross-model eligible |
| --- | --- | --- | --- |
| reverse | yes | no | **never** |
| counterfactual | yes | no | **never** |
| key condition | no (target masked) | no | never |
| candidate-free, same family | no | yes | **no** |
| candidate-free, distinct family | no | yes | **yes** |

Enforced structurally: `BidirectionalCheckRecord` raises if
`cross_model_eligible` is set on a record with `candidate_shown=True`.

## 25–26. Audit 0008 preserved

M16's `X` is not mutated: a consensus result is byte-identical after every
mechanism runs, `x_cross_model` stays 0.0, and `x_cross_model`,
`CROSS_MODEL_RECALL`, `f_support` and `i_independent_support` appear nowhere in
M18. Module 5's own matrix is re-asserted: an independent cross-model edge gives
`X = 1.0` and leaves `F` unchanged.

Shown-candidate checks are excluded from `X` for the same reason Audit 0008
excluded shown-candidate verifier agreement: anchoring makes agreement cheap,
and a differently-shaped prompt does not make an anchored answer independent.

## 27–29. Read-only proofs

Modules 3, 5, 16 and 17 are deep-compared before and after all four mechanisms
run; Module 17's target catalogue is recomputed and compared; its calibrator
records zero calls. Code scans reject `score_breakdown`, `candidate.status` and
`add_verification`.

## 30–34. Per-relation policies

**Border** — minimal change: M15 detects the risk, M18 executes only on request,
no enumeration, no cross-family default, no geographic lookup.
**Stock** — company-itself / parent / subsidiary / index classes from the
contract; M15's public-listing gate is neither rerun nor read; no market data.
**Null/temporal** — reconstruction, counterfactual and candidate-free with the
contract's birth/residence/country classes; Audit 0024's abstention handling
reused; no final empty.
**Award** — recipient versus nominee, work, adjacent award and rescinded; no
pruning, no Tier A–D.
**Numeric** — capacity-versus-attendance and total-versus-land classes; M12's
canonicalisation reused; no reclustering, no winner, no 5 % tolerance.

## 35–36. Call accounting and the shadow seam

Every executed check is exactly one call, recorded with kind, target, template,
group, `candidate_shown`, model and revision, family, prompt hash, decode
identity, calls, tokens, latency and error. The pipeline seam builds only the
catalogue: a test asserts the runtime's counter does not move across
`decide_graph`, that `records == ()` and `calls == 0`. An explicit caller uses
`execute_bidirectional_checks`; its spend joins the shadow counters and never
Module 7's budget, asserted against `prediction.calls_used`.

## 37. Persistence

`bidirectional_verification.jsonl`, one row per query in manifest order:
check version, query identity, the whole catalogue including ineligible checks,
executed records, origin ids, newly recalled candidates, calls and errors. No
gold, no accepted/rejected, no prediction, no `should_stop`, no residual, no
budget. No prior artefact schema changed.

## 38. Error handling

Query/contract mismatch, unsupported relation, undeclared mechanism, ineligible
reverse, wrong target kind, missing value, unknown counterfactual class,
runtime failure, malformed output, abstention, empty recall and numeric parse
failure are all distinct and explicit. A runtime failure is recorded with
`RUNTIME_ERROR`, no outcome and no retry — `retry`, `max_attempts`, `backoff`
and `while True` are scanned for and absent.

## 39–41. No self-correction, no M19, no M20/M21

Scanned and absent: the nine generic re-ask phrasings; `residual`,
`missingness`, `saturation`, `coverage_gap`; `allocate_budget`, `schedule`,
`next_action`, `expected_value`, `should_stop`, `STOP`.

## 42. §14.1 Optional DoLa adapter — intentionally deferred

§14.1 read in full. The proposal is explicit that DoLa is *"an **optional
experimental** adapter, **not a core component**"*, to be enabled "only on
compatible text-only generation paths **after parameter/runtime audit**", and
retained "**only if validation ablation shows a gain**". It adds: *"Do not apply
DoLa to the A/B/C verifier if it changes calibration semantics."*

Every precondition is currently unmet:

* it needs intermediate hidden-state access plus LM-head compatibility, which
  the runtime abstraction deliberately does not expose;
* retention requires a matched-compute validation ablation, and **no validation
  split may be touched during M9–M21 construction**;
* the architecture must not depend on an optional adapter.

Therefore **no DoLa runtime, no hidden-state access, no premature-layer
configuration and no config placeholder** exist. A test scans for `dola`,
`hidden_state`, `hidden_states`, `premature_layer`, `early_exit`,
`layer_contrast` and `output_hidden`, and the shipped configs are asserted free
of the word.

This is a **deferral to the post-architecture experimental phase**, not a
rejection. Nothing in M18 forecloses adding it later on a generation-only path.

## 43. No A/B/C calibration change

Module 4's prompt surface still hashes to
`3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874`. M18 imports
no part of the verifier kernel and scores no labels.

## 44. Tests

```
python -m pytest -q
2147 passed, 3 skipped in 17.50s
```

M18's suite: **132 tests**, covering the brief's 90 numbered requirements.
Repository 2015 → 2147. No prior test needed rescoping.

Beyond the two parser defects in §20, four of my own test premises were wrong
and were corrected rather than the code: a scan that hit the registry's own
rationale prose (now scanning the renderers and every produced prompt), a
"near-miss prose" token `index` colliding with `sample_index`, and an
ineligibility assertion that ignored the reverse-not-declared answer taking
precedence.

## 45. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

## 46. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  total: 28.67B    RESULT: PASS
```

M18 names no checkpoint: the runtime is supplied by the caller, and a
subprocess test asserts `torch`, `transformers` and `mistral_common` are never
imported. A code scan rejects `mistralai/` and `qwen/`.

## 47. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact.

## 48. No TRAIN / VAL / TEST use

No split read, no metric computed, no threshold introduced. Every fixture is
scripted and fictional. Shadow invariance verified over three relations × 17
artefacts.

## 49. Challenge compliance

Closed book (no web, KB, API, entity resolver; scans reject `wikipedia`,
`wikidata`, `http://`, `https://`, `api_key`); no training (`fine_tune`,
`lora`, `.fit(` absent); no third model; no fuzzy matching or embeddings
(`embedding`, `cosine`, `levenshtein`, `difflib`, `fuzz` absent); deterministic
decoding at temperature 0.

## 50. Explicit non-goals

M19, M20, M21, DoLa, generic self-correction, candidate insertion into M3/M16,
and any acceptance or pruning decision.

## 51. Verdict

**PASS.**

M18 implements §14's four mechanisms as four structurally different questions —
a reversed framing, a masked reconstruction, a contract-defined contrast and a
blind recall — and none of them is a reworded Module 17 question or a "think
again". The counterfactual class is Module 0's own rule text, so no code path
can invent a factual alternative. The candidate-free renderer cannot see a
candidate, and the poison test proves five upstream signals stay out of every
prompt.

Where §14's "increase X" met Audit 0008's cross-model definition of `X`, the
audited semantics were preserved and the provenance recorded, with the
reconciliation written down here rather than chosen quietly.

Nothing here decides anything. Eligibility is not scheduling; the catalogue
costs nothing; and no check runs unless a caller asks for it.

Not committed. Not pushed.
