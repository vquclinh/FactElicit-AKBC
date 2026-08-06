# Audit 0027 — Layer 4: Verification Evidence Integration and Conformance

Status: **PASS** (amended in place — see §20A)
Date: 2026-08-06
Amended: 2026-08-06, correcting the key-condition mapping to be
cardinality-aware.
Milestone: **layer-boundary integration**, not a numbered module.
Mode: **shadow**, **disabled by default**, **zero neural calls**.

---

## 1. Objective and scope

Produce one coherent, deterministic Layer-4 evidence view from three already
audited states — Module 16's pre-verification consensus, Module 17's calibrated
specialist verifications, Module 18's structural checks — so Module 19 has a
single surface to consume.

In scope: the two adapters, the canonical Layer-4 representation, the
independence and cost ledgers, the cross-model reconciliation, the pending-check
reconciliation, configuration, the artefact, and the Phase-C seam.

Out of scope and not implemented: M19, M20, M21, DoLa. No new module.

**This integration decides nothing and measures nothing new.** It spends zero
calls, mutates nothing upst



ream, and produces no score, no acceptance and no
stopping rule.

---

## 2. Proposal sections read

| Section | What it fixed here |
| --- | --- |
| **§12, §12.1** | `q_g(o) = max` and `phi(o) = (F, L, X, C, U, I, D, cost, risk)` — the arithmetic every Layer-4 group must still obey. |
| **§12.2** | Semantic disagreement stays M16's `D`, distinct from M17's two channels. |
| **§13, Table 5** | What M17's calibrated reading *is*, and therefore what it may and may not become. |
| **§13.1** | Template and label-order variations are **bias diagnostics**, which is why four readings are not four witnesses. |
| **§14** | The four mechanisms and the "increase X" instruction reconciled in §25. |
| **§14.1** | DoLa remains deferred (§42). |
| **§15, §15.1** | Read **only** to fix the downstream boundary: M19 needs novelty, singleton ratio, facet gap, disagreement and unresolved mass — so Layer 4 must expose inspectable availability and disagreement, and compute none of `R_t`. |
| **Appendix C** | Module I/O; Layer 4 sits between "candidate consensus states" and "residual/gap state". |

Prior audits read: **0006** (identity), **0008** (F/L/X/C/U), **0012** (M0–M8),
**0022** (M15 §17A), **0023** (M16), **0024** (M14 NULL), **0025** (M17),
**0026** (M18).

### Interpretations recorded rather than resolved silently

**1. `L` is not fused.** Module 16's `L` is Module 4's calibrated blind
verifier reading, via Module 5's `logit_term`. Module 17 produces a *different*
calibrated reading, under a different contract and a different prompt surface.
The architecture contains **no audited rule** for combining them — no weight, no
precedence, no averaging — and §10 of the brief forbids inventing one. So
`base_l` / `base_l_available` and `specialist_verifier` are reported side by
side, and a scalar collapse is deferred to whoever defines the rule. Averaging
margins or adding logits would have produced a number nobody can audit.

**2. `C` keeps its audited normalisation.** Module 17's INVALID is exposed as
`specialist_verifier.contradicts` and, for Module 18, as
`structural_contradicting_groups`. Neither is folded into `base_c`, because the
architecture defines no denominator that would make a specialist contradiction
commensurable with Module 5's. Same reasoning as **1**, and the same as M16's
own decision in Audit 0023 §2.

**3. `I` grows only from hidden-candidate recall.** Module 16 counts *recall*
groups in `I`. A Module 18 reverse or counterfactual check shows the candidate,
so its agreement is anchored — exactly the property Audit 0008 used to exclude
shown-candidate verifier agreement. Only the candidate-free group is genuine
recall, so only it raises `layer4_i`. `base_i` is always kept beside it.

**4. Cross-model credit needs provenance Module 16 does not persist.** The rule
requires knowing which families already produced a candidate;
`CandidateConsensusState` carries keys, not families. The map therefore comes
from Module 3 (`prior_family_map`), read-only, and when it cannot be determined
the credit is withheld with `UNRESOLVED_PROVENANCE`. A false negative costs a
missed signal; an unsupported credit corrupts an audited channel.

---

## 3. Architecture position

```
    M16 atomic consensus  (immutable)
          |
          +----------------------+
          |                      |
          v                      v
        M17                     M18
  calibrated blind        new structural
  verification            evidence checks
          \                    /
           v                  v
            Layer-4 evidence state      <- this milestone, 0 calls
                     |
                     v
                 future M19

    M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8      (unchanged)
```

## 4. Files changed

New: `evidence/layer4_types.py` (747), `evidence/layer4.py` (709),
`tests/test_layer4_integration.py` (1469), this audit.
Modified: `pipeline.py` (optional integrator, Phase-C seam, explicit
`integrate_layer4`), `run_staged.py`, `run_cover.py`, 3 configs.

**No module M0–M18 was changed.** `benchmark/` untouched.

## 5. Why this is integration, not a new module

It performs no inference, defines no new mechanism, asks no new question and
introduces no new evidence. It *projects* three recorded states into one and
keeps a ledger. A test scans for `M18_5`, `VerificationReasoner`,
`FinalVerifier`, `JudgeAgent` and `Reasoner` and fails on any. The proposal's
module numbering is untouched: M19 is still next.

## 6. Module 16 immutability

`atomic_consensus.jsonl` is written by Module 16 and never rewritten. The
integrator deep-copies nothing into it: a test compares
`consensus.to_json()`, `verification.to_json()` and `record.to_json()` before
and after and asserts all three are byte-identical, and the staged run confirms
`atomic_consensus.jsonl` is byte-identical with Layer 4 on or off. The base
values live in the overlay as `base_f`, `base_l`, `base_x`, `base_c`, `base_u`,
`base_i`, `base_d` — copied, never recomputed — which is what keeps the
M16 / M16+M17 / M16+M17+M18 ablations honest.

## 7–8. The two adapters

**M17 → `SpecialistVerifierEvidence`.** Keeps the mean calibrated distribution,
the argmax, the mean margin, the mean entropy, both §13.1 disagreements, the
reading count, the control count and the physical-call count. It is *not*
reduced to an argmax and *not* majority-voted.

**M18 → `StructuralCheckEvidence`.** Maps each mechanism's outcome to Module
16's signed vocabulary, plus the execution status, the origin, the family, the
`candidate_shown` flag and the cross-model verdict.

Neither adapter rewrites an upstream format, and neither introduces a second
candidate-identity system: entity keys are Module 3's strict keys, numeric
targets are Module 12's cluster indices, propositions are Module 14's.

## 9. The canonical representation

`Layer4EvidenceState` per query: candidate overlays, proposition overlays,
numeric overlays, Module 14's null state carried through, pending-check
statuses, a cost ledger, upstream versions and errors. Every type round-trips
`to_json`/`from_json`.

## 10–12. Factual evidence versus calibration controls, and independence

A cold Module 17 result for one target is **4 readings + 4 controls = 8
physical calls**, and:

* all 8 are in the cost ledger, once each;
* the 4 controls contribute **zero** support and **zero** contradiction — they
  measure prompt-label bias, not the world;
* the whole request is **one** mechanism, `m17:SPECIALIST_VERIFIER`, so
  `layer4_i == base_i` after verification. Two phrasings and two label orders do
  not become two, or four, independent witnesses.

A warm cache is 4 readings + 0 controls = 4 calls, and a test drives both in
sequence and asserts the ledger equals the runtime's own counter (12 = 8 + 4).

## 13–16. M17 VALID / INVALID / UNKNOWN / availability

| Reading | Effect |
| --- | --- |
| VALID | calibrated verifier evidence; **no** `F`, **no** `X`, `I` unchanged |
| INVALID | the same, plus `contradicts=True` and a signed structural contradiction; `base_c` untouched (§2.2) |
| UNKNOWN | `available=True` with an UNKNOWN-dominated distribution; **not** a contradiction, **not** failed recall, **not** substantive NULL |
| failed | `UNAVAILABLE`, `distribution=None`, `argmax_label=None` |
| never requested | `NOT_REQUESTED` |

Three availability states, all distinguishable — Module 19 must be able to tell
"not measured" from "measured and uncertain".

## 17. `L` — why no scalar fusion

See §2.1. `base_l` and `specialist_verifier` are separate fields; no field is
named `combined_l`, `fused_l`, `merged_l` or `l_total`, and the code contains no
`base_l +`, `log_odds` or `average_margin`.

## 18. `C` — why no invented denominator

See §2.2. Specialist contradiction is exposed structurally; `base_c` is copied
unchanged.

## 19. Disagreement channels

Three, kept apart and separately serialised: M16's `base.D`, M17's
`template_disagreement`, M17's `label_order_disagreement`. No field combines
them; a scan rejects `combined_disagreement`, `total_uncertainty`,
`disagreement_sum` and `mean_disagreement`. Module 5's `U` is not touched.

## 20–23. The M18 mappings

| Mechanism | Outcome | Mapped to |
| --- | --- | --- |
| reverse | SUPPORTED / CONTRADICTED / UNRESOLVED | SUPPORT / CONTRADICT / UNRESOLVED |
| key condition | TARGET_RECOVERED / DIFFERENT_VALUE_RECOVERED / UNRESOLVED | SUPPORT / **cardinality-aware, see §20A** / UNRESOLVED |
| counterfactual | TARGET_RELATION / NEAR_MISS_RELATION / NEITHER / UNRESOLVED | SUPPORT / CONTRADICT / UNRESOLVED / UNRESOLVED |
| candidate-free | TARGET_RECALLED / TARGET_ABSENT / NOTHING_RECALLED | SUPPORT / **UNRESOLVED** / UNRESOLVED |

Conservative by construction: an absence, an unresolved answer, a malformed
answer and a failed call are **never** contradictions. A failed call carries
`FAILED` status and no outcome.

## 20A. Correction — the key-condition mapping is cardinality-aware

### The defect

As first written, this audit and the adapter mapped, for **every** relation:

```
KEY_CONDITION  DIFFERENT_VALUE_RECOVERED  ->  CONTRADICT
```

That is invalid for a set-valued relation. The key-condition probe masks the
target and asks the model to reconstruct an object from the subject and the
contract. For `awardWonBy`, a target of `Recipient Alpha` and a reconstruction
of `Recipient Beta` are **both** able to satisfy the relation at once, so
`Beta != Alpha` is no evidence whatever that `Alpha` is false. The same holds
for `countryLandBordersCountry` and `companyTradesAtStockExchange`.

The old path manufactured a contradiction out of a relation's own plurality.

### Why it matters beyond wording

The mapping is what a downstream consumer reads. Under the old rule an award
candidate that a masked reconstruction simply did not happen to name first
acquired a structural contradiction group — and Module 19, and later Module 8,
would have consumed that as evidence against a candidate nothing had actually
disputed. What the reconstruction really signals for a set-valued relation is
incomplete recall or semantic competition, and this pass deliberately does not
decide which: that is Module 19's reading to make.

### The exclusivity test

Read from Module 0, never from a relation name: `admits_one_object(relation)`
returns `contract.selection.max_objects == 1`. A test asserts the function
agrees with the contract for every relation and that **no relation name appears
in the Layer-4 code at all**.

| Program type | Relations | `max_objects` | Admits one? |
| --- | --- | --- | --- |
| `LARGE_OPEN_SET` | `awardWonBy` | 0 | no |
| `SMALL_SET` | `countryLandBordersCountry`, `companyTradesAtStockExchange` | 0 | no |
| `NULL_SINGLE` | `personHasCityOfDeath` | 1 | **yes** |
| `NUMERIC` | `hasCapacity`, `hasArea` | 1 | **yes** |

### The corrected mapping

| Outcome | Admits one object | Set-valued |
| --- | --- | --- |
| `TARGET_RECOVERED` | SUPPORT | SUPPORT |
| `DIFFERENT_VALUE_RECOVERED` | **CONTRADICT** (competing) | **ALTERNATE_RECOVERED** |
| `UNRESOLVED` | UNRESOLVED | UNRESOLVED |

`ALTERNATE_RECOVERED` is a fourth `StructuralOutcome`, added because the reading
is genuinely distinct: it parsed cleanly (`RESOLVED`), it is not support for
this target, and it is not evidence against it. `StructuralOutcome.contradicts`
is true only for `CONTRADICT`, so an alternate can never reach
`structural_contradicting_groups` by any path.

### Per-cardinality behaviour, all tested

**`LARGE_OPEN_SET` (award)** and **`SMALL_SET` (border, stock)** — a different
recovered object yields `ALTERNATE_RECOVERED`: no structural contradicting
group, `base_c` unchanged, the specialist-verifier state untouched, `q_g = 0`
(not support either), and no `CONTRADICT` anywhere in the serialised row. An
exact recovery still yields SUPPORT with `q_g = 1`.

**`NULL_SINGLE` (death)** — the contract admits at most one city, so a distinct
locality is genuine competing evidence: `CONTRADICT`, and
`structural_contradicting_groups == ("M18_KEY_CONDITION",)`. It is competition,
never a rejection: `base_c` is still untouched and the payload carries no
`rejected`, `accepted`, `final` or `prune`.

**`NUMERIC` (capacity, area)** — one target quantity per the contract, so a
different canonical value competes and maps to `CONTRADICT`. Module 12's
representative and unit are carried unchanged; a scan re-asserts no
`cluster_values`, `0.05`, `tolerance` or `recluster`.

### The alternate is preserved, not acted on

`StructuralCheckEvidence.recovered_value` carries what the reconstruction
actually returned, whether or not it matched. It is provenance: the alternate
object is **not** inserted into Module 3, not inserted into Module 16, not
turned into a candidate in the Layer-4 view, and not counted as novelty — a
scan rejects `novelty` and `new_object`, because that reading belongs to
Module 19.

### Channels preserved

For a set-valued alternate recovery, every other channel is asserted equal to
the no-check baseline: `F`, `base_l` / `l_available`, `X`, `base_c`, `base_u` /
`u_available`, `base_d`, and `I`. The cross-model verdict stays
`NOT_INDEPENDENT_RECALL` — masking the target does not make a reconstruction an
independent recall — so §25's X rules are untouched. Four repeats of the
mechanism stay one group with `q_g` taken as a max.

### Module 18 unchanged

The fix is entirely in the Layer-4 adapter. `DIFFERENT_VALUE_RECOVERED` was
already the right neutral name, and Audit 0026 already said a different
recovered value is evidence rather than an automatic rejection — it was this
projection that over-read it. No file under `src/cover_kbc/verification/` was
touched, and M18's 132 tests pass unchanged.

---

## 24. New candidates

A candidate-free probe may name something Module 16 never held. It appears in
the Layer-4 view marked `discovered_by_structural_check`, keyed by Module 3's
strict key, unverified, and inserted nowhere: the graph and the consensus are
asserted byte-identical afterwards, and the payload contains no `accepted`,
`final` or `trusted`.

## 25–27. The cross-model rule

§14 says a natural recall increases `X`; Audit 0008 defines `X` as cross-model
independent recall. Both hold only under all six conditions, and each failure is
named:

| Condition | Failure state |
| --- | --- |
| the mechanism must hide the candidate | `SHOWN_CANDIDATE` |
| it must be an independent recall | `NOT_INDEPENDENT_RECALL` |
| the candidate must have been named | `TARGET_NOT_RECALLED` |
| it must have been held before | `FIRST_DISCOVERY` |
| the answering family must be new | `SAME_FAMILY` |
| prior families must be knowable | `UNRESOLVED_PROVENANCE` |
| all of the above | **`CREDITED`** |

Frozen by test:

* reverse and counterfactual, any family → **no X** (candidate shown);
* key condition, target masked → **no X** (not independent recall);
* candidate-free, same family → **no X**;
* candidate-free, distinct family, candidate hidden and named → **X = 1.0**,
  once, and repeating the probe keeps one group and one credit;
* a candidate discovered by that probe → **no X**: one family is not
  corroboration;
* unknown prior families (`None`, `{}` or an empty tuple) → **no X**.

`base_x` is never overwritten; `layer4_x = max(base_x, credit)`.

## 28–30. `F`, `q_g` and independence groups

**`F` never moves.** There is no `layer4_f` field and no `base_f +` anywhere.
Verifier evidence, reverse, counterfactual, key-condition and candidate-free all
leave it exactly as Module 16 computed it.

**`q_g` is a max.** `StructuralGroupSupport` raises on any `q_g` outside
`{0, 1}`. Ten reverse checks are ten origins, one group and — being anchored —
no change to `I`. Three counterfactual classes are one group. Four M17 readings
are one mechanism.

## 31–32. Origin and physical-call ledgers

Module 17 readings deduplicate on (model, template, prompt); Module 18 records
on their own deterministic origin id. Projecting one record twice charges once.
One Module 18 output naming five candidates is one call, one origin and five
evidence events. Conflicting immutable metadata on one origin raises
`Layer4ProvenanceError` rather than being silently merged.

The ledger is asserted equal to the runtimes' own counters, and
`integration_calls` is always 0.

## 33–35. Numeric, null and string identity

Numeric evidence attaches to Module 12's cluster with its own representative,
unit and dispersion; competing clusters stay visible; a scan rejects
`cluster_values`, `median`, `relative_distance`, `tolerance`, `0.05` and
`recluster`. A query proposition stays query-level and never enters the
candidate set. `"The Alpha Exchange"` and `"Alpha Exchange"` stay two
candidates; `alias_hint`, `levenshtein`, `difflib`, `fuzz`, `embedding`,
`cosine` and `similarity` are all absent.

## 36. Pending-check reconciliation

A Module 15 request appears with `ELIGIBLE_NOT_SCHEDULED` until a matching
Module 18 record exists, then `RESOLVED` (or `FAILED`) with the origin ids that
ran. **Execution status, never truth status** — the payload contains no
`factual_resolved`, `candidate_resolved` or `risk_resolved`.

## 37. Availability states

Five execution states and three verifier availability states, all distinct, so
Module 19 can tell an unmeasured target from an uncertain one.

## 38–41. No score, no accepted set, no M19, no M20/M21

No field on any type contains `accepted`, `rejected`, `final`, `prediction`,
`prune`, `rank`, `should_stop`, `decision` or `confidence`. Scans reject
`final_confidence`, `combined_probability`, `verification_score`,
`weighted_sum`, `fitted`; `accepted_set`, `A_t`, `final_set`, `CLOSED`,
`closure`; `residual`, `missingness`, `novelty`, `singleton_ratio`,
`facet_gap`, `unresolved_mass`, `saturation`, `coverage_map`;
`allocate_budget`, `schedule`, `next_action`, `expected_value`.

## 42. §14.1 DoLa — still deferred

Unchanged from Audit 0026 §42: optional, experimental, conditional on a runtime
audit and a validation ablation that cannot run during architecture
construction. No adapter, no hidden-state access, no config. A scan rejects
`dola` and `hidden_state`.

## 43. Zero-neural proof

No model module is imported; `score_labels`, `generate(`, `LMRuntime`,
`GenerationRequest`, `LabelScoreRequest` and `runtime` appear nowhere in the
Layer-4 code. Both runtimes' counters are asserted unmoved across an
integration, and across the pipeline seam.

## 44. Shadow invariance

Layer 4 on vs off, four relations × 18 artefacts — including
`atomic_consensus.jsonl`, `specialist_verification.jsonl`,
`bidirectional_verification.jsonl` and both call ledgers — **all
byte-identical**. Only `layer4_evidence.jsonl` appears. Module 3's graph and
Module 5's per-candidate state are asserted unchanged across `decide_graph`.

## 45. Persistence

`layer4_evidence.jsonl`, one row per query in manifest order: integration and
upstream versions, candidate overlays with base and Layer-4 values, discovered
candidates, cross-model diagnostics, structural groups and contradictions, three
disagreement channels, null state, numeric targets, pending-check statuses, the
cost ledger and errors. A row reloads to an equal state. No prior artefact
schema changed.

## 46. Error handling

Query-identity mismatch across M16/M17/M18, a target Module 16 does not hold, a
numeric cluster it does not hold, a conflicting origin, an unsupported version
and an unknown config key all raise. Nothing is silently repaired.

## 47–51. Prior-audit regressions

**0008** — the F/L/X/C/U matrix is re-asserted: a shown-candidate verifier edge
moves neither `F` nor `X`.
**0023** — M16's state is byte-identical after integration; `q_g` is still a
max; `F`'s denominator is untouched.
**0024** — `UNKNOWN` still asserts no relation-level absence, `NONE`/`UNKNOWN`
never become candidates through the recall path, and 100 failed-recall
operations still give zero substantive null support.
**0025** — M17's readings, controls, distributions and both disagreement
channels survive the projection intact.
**0026** — M18's four outcomes, its shown-candidate exclusions and its
one-call-one-origin rule all hold through the projection.

## 52. Tests

```
python -m pytest -q
2243 passed, 3 skipped in 18.53s
```

Layer 4's suite: **96 tests** — 82 for the brief's 105 numbered requirements,
plus 14 for the §20A correction. Repository 2147 → 2243.

One prior test was rescoped: `test_key_condition_outcomes_map_conservatively`
asserted the universal `DIFFERENT_VALUE_RECOVERED -> CONTRADICT` mapping, i.e.
it encoded the defect. It now asserts the set-valued reading, and the full
cardinality matrix is covered by the new tests beside it.

Two of my own errors were caught while writing them: a scan token `l_` that
matched inside `model_id`, and a `CrossModelCredit` count assertion written
before the `FIRST_DISCOVERY` state was added. One design gap was also found and
fixed: a newly discovered candidate was initially reported as
`TARGET_NOT_RECALLED`, which was misleading — it *was* recalled, it simply had
no prior family to corroborate against, which is now its own named state.

A third defect — the universal key-condition mapping — was found in review after
this audit was first written, and is corrected in §20A.

## 53. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

## 54. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  total: 28.67B    RESULT: PASS
```

No model, checkpoint or parameter is involved: the integration is arithmetic
over recorded state.

## 55. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact.

## 56. No TRAIN / VAL / TEST use

No split read, no metric computed, no threshold introduced — the configuration
has exactly three keys and none of them is a number. All fixtures are synthetic
and fictional; no real model was loaded.

## 57. Challenge compliance

Closed book (no web, KB, API, resolver); no training, no fitted coefficient, no
embedding; frozen model profile untouched; deterministic and order-invariant.

## 58. Verdict

**PASS.**

Layer 4 now has one coherent evidence surface. Module 16's consensus is
immutable beneath it; Module 17's calibrated readings arrive whole, with their
controls counted as cost and never as evidence, and four readings counted as one
mechanism; Module 18's four outcomes arrive conservatively mapped, with absence
and failure never becoming contradiction.

Where the proposal's "increase X" met Audit 0008's cross-model definition, the
audited semantics were preserved and every condition of the credit rule was
made explicit and named — including the two cases where the honest answer is to
withhold credit rather than guess.

The key-condition mapping is cardinality-aware (§20A): a reconstruction that
names another qualifying object of a set-valued relation is preserved as an
alternate, not manufactured into a contradiction of a candidate nothing
disputed.

`F` is unchanged, `q_g` is still a max, and the three disagreement channels are
still three. Nothing here decides anything: Module 19 receives evidence, not a
verdict.

Not committed. Not pushed.
