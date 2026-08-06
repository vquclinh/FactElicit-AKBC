# Audit 0021 — Module 14: Null/Temporal Specialist Conformance

Status: **PASS** (amended in place — see §15A)
Date: 2026-08-06
Amended: 2026-08-06, correcting the NULL-evidence semantics of §14–15.
Milestone: third Layer-2 specialist (M14 of M9–M21).
Mode: **shadow**, and **disabled by default** — M14 spends real neural calls.

---

## 1. Objective and scope

Implement **M14 Null/Temporal Specialist** for `personHasCityOfDeath`: run
Stage-A existence probes, gate on them, run Stage-B locality acquisition only
when the gate permits, keep §10.3's three NULL-evidence classes apart, and offer
the §10.2 cross-family recall branch.

In scope: the typed contract, the Stage-A/B registries, status and locality
parsing, the local gate, NULL-evidence bookkeeping, the cross-family branch,
configuration, the observability artefact, and the M11→M14 seam.

Out of scope and not implemented: M15–M21. No placeholder files.

**M14 decides nothing about the world.** No accepted city, no final empty
answer, no verdict, no score. §20–23 prove it.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§10** header | "A zero-or-one relation must separate two questions: 'does an object exist?' and 'which object is it?'" — the reason the module exists. Also: merely asking a model to revise its answer is often insufficient. |
| **§10.1** "Two-stage gate" | Stage A predicts `{living, deceased, unknown}` from **independent prompts**; "No city is inferred until the gate has sufficient evidence"; Stage B is **exactly** "direct locality, biography-locality, birth-vs-residence contrast, and candidate-free recall", run "If deceased/non-empty is plausible". |
| **§10.2** "Temporal freshness branch" | "M14 **may** trigger cross-family fresh recall: a smaller/fresher model can generate candidates on one branch, after which the other model blind-verifies them. **No factual web lookup is used**." |
| **§10.3** "NULL evidence" | "'no candidate was generated' is not automatically equivalent to 'gold is empty'"; `E_null = {living support, no-known-locality support, failed-recall only}`; "Failed recall receives very little weight." |
| **§11** (M15) | M15 may reuse the freshness branch — the reason it is kept behind a clean interface and the reason no stock semantics appear here. |
| **§7.1–7.2** (M11), **§6** (M10), **§5** (M9) | Upstream state M14 consumes and never rebuilds. |
| **§8–§9** (M12/M13) | The sibling boundary. |
| **§12–§14**, Appendix C | What M16/M17/M18 own; the module I/O table. |

Also read: Audits 0016–0020 and the current source.

### Interpretations recorded rather than resolved silently

**1. Stage-A prompt families.** §10.1 fixes the *label set* and says "independent
prompts" (plural), but names no prompt families. Three structurally distinct
framings are implemented — direct life status, death-event existence,
life-dates-then-conclude — which is the minimum "independent" can mean: the same
question asked three different ways rather than three samples of one. §10's own
note that asking a model to revise an answer is often insufficient is why the
third reaches the status through recalled dates instead of restating the
question. Each carries a written rationale.

**2. The local gate rule.** §10.1 requires "sufficient evidence" and gives no
threshold. The minimal rule "independent evidence" implies is used: count
**distinct independence groups**, not observations, with a configurable minimum
defaulting to 1 — the smallest number that can mean "independent evidence" at
all, and not a fitted value. Contradiction yields `UNRESOLVED`, because evidence
pointing both ways is not sufficient evidence and the safe consequence of not
knowing is to spend no Stage-B calls. Resolving contradictions across mechanisms
is Module 16's, so `conflict_policy` accepts only `unresolved` in this
milestone.

**3. "Freshness" is implemented as a *role*, not a claim.** §10.2 says "a
smaller/fresher model". The repository establishes that Qwen3.5-4B is *smaller*
(4.66 B vs 24.01 B); it establishes nothing about either checkpoint's knowledge
cutoff. The branch is therefore named **cross-family recall**
(`RecallFamily.CROSS_FAMILY`), and a test asserts no `knowledge_cutoff`,
`is_fresher`, `trained_until` or `more_recent_model` appears anywhere. §10.2's
second half — "after which the other model blind-verifies them" — is Module 17's
and Module 18's and is not implemented.

**4. The locality taxonomy covers four of five contract rules lexically.** The
remaining two are recorded in `NON_LOCALITY_CONTRACT_RULES` with reasons: "the
person is still living" is Stage A's and the NULL-evidence state's, and "a guess
supplied because the model was asked to name a city" is not detectable from
words — a guess and a recollection are written identically, and enforcing that
rule is Module 17's verification. The consistency check fails if any contract
rule is neither a locality kind nor recorded here, so nothing goes quietly
unrepresented.

No material conflict between this brief and the proposal was found.

---

## 3. Architecture position

```
M0 / M1
    v
M9  QueryRiskProfile
    v
M10 PromptProgram
    v
M11 ParametricMemoryRecords
    v
M14 Null/Temporal Specialist      <- this milestone   (sibling of M12, M13)
    |-- Stage A: existence / death status
    |-- local gate
    |-- Stage B: locality, only when the gate permits
    |-- cross-family recall branch
    \-- NULL evidence state
    v
[future M16 Consensus -> M17/M18 Verification -> M19-M21 control]

M2 -> M3 -> ... -> M8             (unchanged production path)
```

One seam, in `CoverPipeline.enumerate_query`, alongside M12 and M13 inside the
existing M9→M10→M11 branch. `null_temporal_specialist=None` is the default and
is the pre-M14 path. **M12, M13 and M14 are siblings**: none imports another,
and any may be enabled alone — asserted by AST import checks in all three
suites.

---

## 4. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/specialists/null_temporal_types.py` | **New.** `DeathStatusObservation`, `LocalityObservation`, `GateReading`, `NullEvidenceState`, `LocalityOccurrence`, plan/result, 8 enums. |
| `src/cover_kbc/specialists/null_temporal_registry.py` | **New.** Stage-A/B templates, status and locality cues, `NON_LOCALITY_CONTRACT_RULES`, `check_null_temporal_registry_consistency`. |
| `src/cover_kbc/specialists/null_temporal_specialist.py` | **New.** `NullTemporalSpecialist`, parsing, gate, aggregation, cross-family branch, config. |
| `src/cover_kbc/specialists/__init__.py` | M14 exports added; M12's and M13's unchanged. |
| `src/cover_kbc/specialists/numeric_specialist.py` | **One line**: the `specialists` config guard admits the `null_temporal` sibling key. |
| `src/cover_kbc/pipeline.py` | `null_temporal_specialist=None` arg, `null_temporal_results`, `_run_null_temporal_specialist`, 3 lines at the seam. |
| `scripts/run_staged.py`, `scripts/run_cover.py` | Build M14; write `null_temporal_specialist.jsonl`. |
| 3 configs | `specialists.null_temporal` block, `enabled: false`. |
| `tests/test_null_temporal_specialist.py` | **New.** 135 tests (105 + 30 from §15A). |
| `tests/test_numeric_specialist.py` | Three tests rescoped (§29). |
| `tests/test_large_set_specialist.py` | One shared helper corrected (§29). |

`benchmark/` untouched.

---

## 5-6. Public types and the two-stage architecture

```python
class DeathStatus(str, Enum)          # LIVING | DECEASED | UNKNOWN
class StatusProbeFamily(str, Enum)    # direct_life_status | death_event_existence |
                                      # life_dates_recollection
class LocalityProbeFamily(str, Enum)  # §10.1's exact four
class LocalityMentionKind(str, Enum)  # TARGET_CITY | BIRTHPLACE | RESIDENCE |
                                      # COUNTRY_OR_REGION | BURIAL_PLACE
class NullEvidenceKind(str, Enum)     # §10.3's exact three
class GateState(str, Enum)            # DECEASED_PLAUSIBLE | NULL_PLAUSIBLE | UNRESOLVED
class RecallFamily(str, Enum)         # PRIMARY_FAMILY | CROSS_FAMILY
class NullTemporalParseStatus(str, Enum)
```

`DeathStatusObservation` / `LocalityObservation` — full provenance
(`operation_id`, family, independence group, sample index, prompt hash, model
id, recall family), raw text, parse status, and `verified` fixed to `False` by
`__post_init__`.

`GateReading` — the state, the groups reporting each status, whether the
evidence conflicts, and the **rule that produced it**, recorded so it can be
reviewed rather than inferred.

`NullEvidenceState` — the three classes with their counts and groups, plus
`substantive_groups` (which excludes failed recall by construction) and
`failed_recall_only`. **There is no `is_empty` boolean.**

`NullTemporalSpecialistPlan` — Stage-A probes plus *conditionally executed*
Stage-B and cross-family probes; `stage_a_calls` is what always runs and
`estimated_calls` is the upper bound.

`NullTemporalSpecialistResult` — everything above plus `stage_b_executed` and
`cross_family_executed`, so a reader can tell "did not run" from "ran and found
nothing".

---

## 7-8. Stage-A families and status taxonomy

| Family | Framing | Why it is structurally distinct |
| --- | --- | --- |
| `direct_life_status` | "Is this person living or deceased?" | The direct form of §10.1's existence question. |
| `death_event_existence` | "Is there a recorded death for this person?" | Asks about the *event*, not the person's state. |
| `life_dates_recollection` | Recall birth/death years, then conclude | Reaches the status through different intermediate material — §10 notes that restating the question is often insufficient. |

Labels are `{LIVING, DECEASED, UNKNOWN}`, parsed by preferring a bare one-word
answer, then declared cues in order (deceased before living, so "died in 1990
and is no longer living" reads DECEASED). Text naming no recognisable status is
`UNPARSED_STATUS` and **never defaulted** to LIVING or DECEASED.

Independence is by family: three resamples of one framing give
`total_observations = 3` and `deceased_support = 1`.

---

## 9. The local gate, and why it is not consensus

```
deceased and living present        -> UNRESOLVED     (conflict is not sufficiency)
|deceased groups| >= minimum       -> DECEASED_PLAUSIBLE
|living groups|   >= minimum       -> NULL_PLAUSIBLE
otherwise                          -> UNRESOLVED
```

Only `DECEASED_PLAUSIBLE` permits Stage B. The states are deliberately **not**
named ACCEPTED/REJECTED/TRUE/FALSE — a test asserts none of those strings is a
`GateState` value.

This is a **module-internal execution-eligibility rule** required by §10.1's "No
city is inferred until the gate has sufficient evidence". It decides whether M14
spends Stage-B calls and nothing else. Module 16 fuses evidence across
mechanisms, Module 17 verifies, Module 8 emits. `NULL_PLAUSIBLE` in particular
does **not** mean the answer is empty: it means locality acquisition is not
warranted, and `GateReading` carries no emptiness field at all.

---

## 10. Stage-B families

§10.1's exact four, in order: `direct_locality`, `biography_locality`,
`birth_residence_contrast`, `candidate_free_recall`. The registry consistency
check fails if the declared list is not exactly this, and a test asserts it.

Every probe is rendered from Module 10 — definition, subject directives,
negative constraints — and no relation definition is restated. A test asserts no
relation name or contract definition appears in M14's executable code.

**M14 is not M2.** Module 2 has its own `death_status_gate`, scored by the
*verifier* as a calibrated A/B/C gate. M14's Stage A is generation-based
acquisition across three framings, in shadow, and never calls the verifier. A
test asserts M14 references no `ViewSpec`, `views_for`, `get_view`,
`ElicitationEngine` or `death_status_gate`, and that M2's gate view is unchanged.

---

## 11-12. Locality taxonomy and at-most-one

| Kind | Contract rule | Example cue |
| --- | --- | --- |
| `TARGET_CITY` | (default in a probe that asked for the place of death) | died in, place of death |
| `BIRTHPLACE` | "the city of birth…" | born, birthplace, native of |
| `RESIDENCE` | "…of residence, or of principal activity" | lived, resided, based in |
| `COUNTRY_OR_REGION` | "a country, state, province or region instead of a locality" | the country of, the province of |
| `BURIAL_PLACE` | "the place of burial when it differs from the place of death" | buried, interred, cemetery |

Classification is **clause-scoped and lexical**: "born in City Alpha, died in
City Beta" yields `BIRTHPLACE` and `TARGET_CITY`; "lived in City Alpha; died in
City Beta" yields `RESIDENCE` and `TARGET_CITY`. It reads what the model wrote
and knows nothing about whether any name denotes a real city.

**At most one, chosen by nobody.** All target-like localities are retained as
competing `LocalityOccurrence` entries with their support; `has_competing_
candidates` reports the conflict. There is no top-1, no majority winner and no
final city — a test asserts `accepted_city`, `final_city`, `top1` and `winner`
appear nowhere in the serialised result. A consequence worth stating: "died at
Example Hospital in City Beta" produces **two** competing candidates, because
deciding which is a city needs world knowledge M14 does not have.

**A defect found and fixed during development.** The direct and candidate-free
probes ask for "the locality name alone", and the extractor could read only
labelled lines and prepositional phrases — so a bare answer produced no
observation at all. `looks_like_bare_name` now reads a single-line, unpunctuated
output whose tokens are capitalised or place-name connectors, flagged as a
structural assumption. "I am not sure" is correctly rejected by the same rule.

---

## 13. Module 11 consumption

M14 mines life statuses, localities and explicit no-known-locality statements
from all three M11 families, carrying `operation_id`, `independence_group`,
`sample_index`, `prompt_sha256` and `model_id` through unchanged. Mining costs
**zero calls**, the M11 records stay `verified = False`, and the derived
observations are `verified = False` by construction. A retrieval result for a
different query is rejected.

Mined statuses contribute their own independence groups to the gate — a genuine
additional structural source, which is why an end-to-end scripted run shows six
deceased groups (three from M11, three from Stage A).

---

## 14-15. NULL evidence, and failed recall

`E_null` is stored as §10.3 separates it:

| Class | What produces it | Substantive? |
| --- | --- | --- |
| `LIVING_SUPPORT` | Stage A read LIVING | yes |
| `NO_KNOWN_LOCALITY_SUPPORT` | the model asserted, **about the record**, that no death locality exists or is known — or produced the empty sentinel **under a grammar that defines it** | yes |
| `FAILED_RECALL_ONLY` | UNKNOWN, "I don't know", a refusal, empty output, malformed output, no separable locality, or a runtime failure | **no** |

`substantive_groups` unions only the first two. `failed_recall_only` is true
exactly when recall failed and nothing positive was said. **A failed probe can
never become evidence that the gold set is empty** — a test makes every Stage-B
probe raise and asserts `failed_recall_only` with `has_substantive_null_evidence`
false and no locality observations.

The distinction between "said nothing is known" and "could not answer" is
carried by `states_no_known_locality`, `is_epistemic_abstention` and
`asserts_relation_level_absence` — see §15A, which corrects an earlier defect in
exactly this boundary.

Per the proposal's "failed recall receives very little weight", M14 encodes the
*class*, not a fitted weight: assigning a number would be inventing a
calibration the proposal does not define, and Module 16 is where weighting
belongs.

---

## 15A. Correction — epistemic abstention is not explicit NULL evidence

This audit was amended in place after review. Audit policy: an audit is amended
where the correction is to the *same* milestone's semantics and the original
claim would otherwise stand as wrong on the page (Audit 0010's precedent); a new
audit is created for a new milestone or a new reviewed commit.

### The defect

As first written, `_run_locality` recorded `NO_KNOWN_LOCALITY_SUPPORT` when the
output was **either** an explicit no-known-locality statement **or** any
abstention:

```python
stated = (
    [(probe.operation_id, probe.independence_group)]
    if states_no_known_locality(text) or _is_abstention(text)   # <- defect
    else []
)
```

`_is_abstention` matched `UNKNOWN`, `NONE`, `n/a`, `-`, `no recollection`,
`I do not know`. So a Stage-B probe answering **UNKNOWN** — which M14's own
system prompt *instructs* it to answer when it does not know — produced
substantive null evidence. `build_null_evidence` compounded it by not counting
`ABSTAINED` as failed recall at all, so an all-UNKNOWN run reported
`no_known_locality_support = 4`, `failed_recall_operations = 0`,
`has_substantive_null_evidence = True` and `failed_recall_only = False`.

That is precisely the conflation §10.3 exists to forbid: *"'no candidate was
generated' is not automatically equivalent to 'gold is empty'"*. Four
independent probes each saying "I don't know" is four independent ignorances,
not four independent pieces of evidence that the person has no recorded death
locality. The original §14–15 wording — "the model *said* no locality is known,
**or abstained**" — stated the defect on the page.

### Why abstention is not an explicit NULL claim

The line is **third person versus first person**, and it is a difference in what
the sentence is about:

* *"The city of death is not known."* — a claim about the record. If true, the
  gold set is empty. Evidence.
* *"I do not know."* / *"UNKNOWN"* — a claim about the speaker. It is compatible
  with the gold set being empty **and** with it holding a city the model failed
  to recall. It discriminates nothing.

Aggregating the second kind is worse than useless: it grows with the number of
probes, so a model that knows nothing about a person would accumulate
"evidence" that the person has no death locality in exact proportion to how hard
it was asked.

### NONE versus UNKNOWN

`UNKNOWN != NONE`, and `NONE` is substantive **only under a grammar that
defines it**. The repository's grammars, checked rather than assumed:

| Producer | What its grammar says | NONE substantive? |
| --- | --- | --- |
| M10 `output_contract` | *"Output exactly one name, and nothing else. If there are none, output exactly: NONE"* | **yes** |
| M11 `query_rewrite` probe | carries M10's `output_contract` verbatim | **yes** |
| M11 `pseudo_memory` probe | offers `NO RECOLLECTION`; never mentions NONE | no |
| M11 `self_ask` probe | offers `UNKNOWN` per sub-question; never mentions NONE | no |
| M14 Stage-B system prompt | *"If you do not know of one, answer exactly: UNKNOWN"*; never mentions NONE | no |

M10's NONE is anchored to Module 0's own definition of the empty answer — *"If
the person is still alive, or no locality of death is known, the answer is
empty"* — which makes it a relation-level assertion. A bare NONE from a probe
whose prompt never defined it is unanchored and means nothing in particular, so
it is treated as failed recall.

`asserts_relation_level_absence(text, *, sentinel_is_defined)` carries this:
M14's own probes pass `False`; M11 mining passes
`record.kind is RecallOperationKind.QUERY_REWRITE`. A test pins the linkage by
asserting which renderer actually embeds `program.output_contract`, so the
policy cannot drift if M11's templates change.

### Exact old and new behaviour

| Stage-B output | Old class | New class |
| --- | --- | --- |
| `UNKNOWN` | NO_KNOWN_LOCALITY_SUPPORT | **FAILED_RECALL_ONLY** |
| `I don't know` / `Not sure` / `I cannot determine` | NO_KNOWN_LOCALITY_SUPPORT | **FAILED_RECALL_ONLY** |
| `NONE` from an M14 probe | NO_KNOWN_LOCALITY_SUPPORT | **FAILED_RECALL_ONLY** |
| `NONE` from M11 `query_rewrite` | NO_KNOWN_LOCALITY_SUPPORT | NO_KNOWN_LOCALITY_SUPPORT *(unchanged, now for a stated reason)* |
| empty / malformed / no separable locality | FAILED_RECALL_ONLY | FAILED_RECALL_ONLY |
| runtime error | FAILED_RECALL_ONLY | FAILED_RECALL_ONLY |
| `"No known city of death."` | NO_KNOWN_LOCALITY_SUPPORT | NO_KNOWN_LOCALITY_SUPPORT |
| Stage A `LIVING` | LIVING_SUPPORT | LIVING_SUPPORT *(untouched)* |

An all-UNKNOWN run now reports `no_known_locality_support = 0`,
`failed_recall_operations = 4`, `has_substantive_null_evidence = False`,
`failed_recall_only = True`.

### Supporting changes

* `NO_KNOWN_LOCALITY_CUES` was tightened from loose fragments (`"no known"`,
  `"not known"`, `"not recorded"`) to nineteen phrases that each **name the
  death locality**. `"no known"` alone matched `"no known relatives"`; `"not
  known"` matched almost any hedge.
* `_EPISTEMIC_ABSTENTIONS` and `_EXPLICIT_EMPTY_SENTINELS` are now separate
  sets. `_ABSTENTIONS` remains their union, used only to decide that *no
  locality name follows* — a different question from which evidence class the
  response supplies.
* `build_null_evidence` now counts `ABSTAINED` as failed recall, excluding
  operations that made an explicit relation-level claim.
* `check_null_temporal_registry_consistency` rejects any no-known-locality cue
  containing a first-person marker (`i do not`, `i don't`, `not sure`,
  `unable to`, …) or failing to name the death locality — so the third-person
  line is enforced by the registry, not just observed by the code.

**No fourth class was added and no score was invented.** §10.3's three classes
are unchanged; only the boundary between two of them moved.

### Scope

Nothing else changed. Stage-A families, `DeathStatus`, the local gate rule,
Stage-B's four families, the locality taxonomy, competing-locality preservation,
M11 provenance, the cross-family branch, call accounting, shadow isolation and
disabled-by-default behaviour are all as originally audited. M12, M13, M2–M8 and
M15–M21 are untouched. The correction is bookkeeping: it moves no calls, and a
test asserts Stage-B accounting is unchanged.

### Tests

Thirty tests were added or corrected, including the one that had asserted the
defect (`test_an_abstention_counts_as_stated_not_failed`, removed):

* ten parameterised epistemic abstentions → never explicit null, under either
  grammar;
* five explicit relation-level statements → substantive;
* NONE substantive only where the grammar defines it, both directions;
* the grammar linkage pinned against M11's actual renderers and M14's system
  prompt;
* all-UNKNOWN Stage B → `failed_recall_only`, not substantive;
* abstention / refusal / empty / malformed → all failed recall (parameterised);
* bare NONE from an M14 probe → not substantive;
* NONE mined from `query_rewrite` → substantive, while the same NONE from
  `pseudo_memory` is not;
* mixed case — one explicit statement plus three abstentions — keeps both
  classes separate, with the stated operation excluded from the failed set;
* twelve independent abstentions → still not substantive;
* `LIVING_SUPPORT` unchanged;
* Stage-B call accounting unchanged;
* no declared cue contains an epistemic marker, and the registry check rejects
  one that does.

---

## 16-18. Cross-family branch

Planned only when **all three** static conditions hold:

1. `cross_family_recall` is enabled in configuration;
2. a genuinely distinct second model family is configured — the pipeline
   compares the **configured** enumerator and verifier ids, mirroring
   `cross_model_recall_available`'s audited rule, so a Phase-A run where one
   runtime object serves both roles correctly reports no second family;
3. Module 9 graded the relation temporally sensitive — read from Module 10's
   compiled `TEMPORAL` directive, an upstream static signal M14 neither computes
   nor adapts.

`plan.cross_family_rationale` records which condition failed, so a run always
explains itself. The branch is gated by the Stage-A gate exactly as Stage B is.

Its records carry `RecallFamily.CROSS_FAMILY` and their own `model_id`, so
Module 17 can later distinguish primary-family from cross-family recall. They
remain `verified = False`: §10.2's blind-verification half is not implemented.

**Not external retrieval.** It is an additional inference branch through an
already-audited frozen checkpoint. An AST import scan rejects `requests`,
`httpx`, `urllib`, `socket`, `http`, `aiohttp`, `sqlite3`, `faiss`, `chromadb`,
`pinecone`, `torch`, `transformers`, `spacy`, `nltk`; a code scan rejects
`wikipedia`, `wikidata`, `http://`, `https://`, `obituary`, `death_registry`,
`gazetteer`, `biography_corpus`, `api_key`. Zero new parameters — verified in a
clean subprocess.

---

## 19. The M15 reuse boundary

§11 notes M15 may reuse this branch. It is therefore a small typed unit —
`RecallFamily`, a planned probe, and an availability flag passed in — rather
than something entangled with death semantics. **No stock logic exists**: a test
scans M14's executable code for `stock`, `exchange`, `listing`, `ticker` and
`company` and fails on any. M14 still executes only for `personHasCityOfDeath`.

---

## 20-23. Why M14 does not implement M16, M17, M18 or M19–M21

**M16.** No `accepted`, `ACCEPT`, `REJECTED`, `consensus`, `fuse_evidence`,
`candidate_score`, `final_empty`, `final_verdict` or `accepted_city`. M14
computes local descriptive counts — status groups, locality occurrences, null
counts — and fuses nothing.

**M17.** No verifier call, no `score_labels`, `VerificationLabel`,
`LABEL_TOKENS`, `VerifierTemplate` or `build_verifier_prompt`. Stage-A labels
are acquisition observations, not verifier labels; M4's `UNKNOWN` is untouched
and its prompt surface is pinned by sha256 `3acd7109…e6d874`.

**M18.** No `key_condition`, `counterfactual`, `reverse_check`, `dispute` or
`reconstruct_event`. M14 prepares provenance those checks will use and performs
none of them.

**M19–M21.** No `should_stop`, `next_action`, `allocate_budget`,
`schedule_budget`, `residual_coverage`, `expected_value` or
`missingness_estimate`. The Stage-A→Stage-B transition is §10.1's intrinsic
two-stage gate, not a generic planner: it reads only Stage-A observations, its
rule is fixed and recorded, and it never consults yield or budget.

---

## 24-26. Runtime, call accounting, conditional Stage B

M14 receives the frozen enumerator and, for the cross-family branch, the
already-configured verifier-family runtime **as a generator**. No new loader.

* **Every call attributable** — module, operation id, stage, family,
  independence group, prompt hash, model id, recall family, tokens, parse
  status, error.
* **Counted exactly once, measured not assumed.** A gated-off run costs exactly
  3 calls (`= runtime.calls`); a `DECEASED_PLAUSIBLE` run costs 7. A
  counter-silent runtime yields 0.
* **Conditional cost is genuinely absent.** When the gate is `UNRESOLVED` or
  `NULL_PLAUSIBLE`, `runtime.calls == 3`: the Stage-B calls were never made, not
  made and discarded. `stage_b_executed` records which happened.
* **No double counting.** M11's and M14's totals *sum* to
  `pipeline.shadow_calls` and never exceed the runtime's own count.
* **No double subtraction.** M14 adds to the shadow counter M11 established;
  `run_staged` still subtracts once.
* **Outside Module 7's budget** — `graph.budget_snapshot` is identical with and
  without M14.
* **Reported honestly** — Phase A prints
  `[M14] null/temporal specialist: … (N queries, K reached stage B, C shadow calls, T generated tokens)`.

---

## 27-29. Shadow isolation, disabled path, M12/M13 preservation

The real staged CLI, run twice with configs differing only in
`specialists.null_temporal.enabled` (M9–M13 on in both):

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
query_profiles.jsonl     IDENTICAL
prompt_programs.jsonl    IDENTICAL
parametric_memory.jsonl  IDENTICAL
```

`null_temporal_specialist.jsonl` is present with M14 on, absent with it off, and
absent for any non-`NULL_SINGLE` relation. `numeric_specialist.jsonl` and
`large_open_set_specialist.jsonl` are byte-identical with M14 on and off.

M14 references no `EvidenceGraph`, `build_graph`, `add_candidate` or
`Evidence(`; a graph is unchanged after an analysis; the serialised graph after
a piped run contains no M14-unique token.

**Sibling preservation.** M12's and M13's modules are byte-unchanged except one
line in `build_numeric_specialist`'s key guard, which now admits
`null_temporal`. Three M12 tests were rescoped, all because M14 legitimately
exists: the specialist file list (7 → 10 files), the example *unknown* config
key (now `small_set_closure`), and the sibling-dependency scan — which was a
textual match and is now an **AST import check**, because
`build_numeric_specialist` legitimately *names* the sibling config keys in order
to reject genuinely unknown ones.

**A latent flaw in the shared test helper was found and fixed in all three
suites.** `_code_without_prose` stripped docstrings by comparing the source
*literal* against `ast.get_docstring`'s *value*. Any docstring containing an
escape — M14's ASCII-art `\\--` — never matched, so it stayed in the "executable
code" scanned by every boundary guard. The helper now compares
`ast.literal_eval(token.string)`. M12's and M13's scans were unaffected in
outcome (their docstrings carry no escapes) but were silently weaker than
intended; both are now correct.

---

## 30. Persistence

`null_temporal_specialist.jsonl`, one record per **NULL_SINGLE query analysed**,
written in Phase A in manifest order. Carries the plan (four versions, query
identity, both stages' probes, cross-family probes and rationale), Stage-A
observations, the gate reading with its rule, Stage-B locality observations,
occurrences, the NULL-evidence state, `stage_b_executed`,
`cross_family_executed`, errors and cost.

A test asserts manifest ordering, the presence of every key, and that no `gold`,
`ObjectEntities`, `accepted_city`, `final_empty` or `prediction` appears.

`query_profiles.jsonl`, `prompt_programs.jsonl`, `parametric_memory.jsonl`,
`numeric_specialist.jsonl`, `large_open_set_specialist.jsonl`, both stages,
`diagnostics.json`, `trace.jsonl` and `predictions.jsonl` are untouched — §27
proves it byte for byte.

---

## 31. Error handling

Thirteen distinguished situations, none silent: upstream identity mismatch,
unsupported relation/programme, M14-without-M9/M10/M11 (config *and* pipeline),
unknown config key, unsupported mode, `min_independent_groups < 1`, unsupported
`conflict_policy`, Stage-A runtime failure, malformed status label, unresolved
gate, empty locality output, locality abstention, no separable locality,
competing candidates, cross-family runtime failure, and a configured
cross-family branch with no distinct family available (planned away with a
recorded rationale rather than silently resampling the same checkpoint).

Nothing is ever fabricated: a Stage-A failure yields `UNKNOWN` with
`RUNTIME_ERROR`, never LIVING or DECEASED; a Stage-B failure yields an empty
surface, never a city; and neither becomes evidence of emptiness.

---

## 32. Test results

```
python -m pytest -q
    1626 passed, 3 skipped        (1491 before; +135)
```

Re-run after the §15A correction. Shadow invariance was re-verified with M14 on
versus off over `personHasCityOfDeath` with M9-M13 all enabled: `predictions`,
`diagnostics`, `trace`, both stages, `query_profiles`, `prompt_programs` and
`parametric_memory` all byte-identical.

`tests/test_null_temporal_specialist.py`, 105 tests, covering all 42 required
areas: §10.1/§10.2/§10.3 conformance; routing across all six relations; sibling
independence for all three specialists; five parameterised identity mismatches;
Stage-A label parsing including the un-defaulted malformed case; Stage-A
independence; every gate state and transition; Stage B genuinely not executing
when gated off; prompt authority; locality extraction and clause-scoped
classification across six shapes; the three-way contrast line; the venue/city
pair kept rather than guessed between; normalisation without resolution;
competing candidates retained; M11 mining with provenance; the three NULL
classes; the failed-recall invariant under runtime failure; explicit
no-known-locality; the cross-family branch's three conditions, provenance,
gating and non-verification; no external retrieval; no consensus, verifier, M18
or control semantics; no stock logic; M4's hash pin; graph isolation; exact and
conditional call accounting; JSON round-trip for every public type; persistence
ordering; shadow invariance; M12/M13 preservation; nine configuration failures;
zero new parameters.

Three failures surfaced during initial development. **One was a real defect in
the module** — a bare locality answer, which is exactly what two Stage-B probes
ask for, produced no observation (§12). The other two came from **one latent
flaw in the shared test helper**, which left a module docstring in the
"executable code" scanned by the boundary guards (§29); fixing it corrected the
guards in all three specialist suites.

**A fourth, more serious defect was found in review and corrected in §15A**: an
epistemic abstention was recorded as substantive NULL evidence, which is exactly
the conflation §10.3 forbids. The test that asserted that behaviour was itself
wrong and has been removed.

---

## 33-35. pyflakes, model budget, benchmark integrity

```
python -m pyflakes src/ tests/ scripts/          clean

python scripts/audit_model_budget.py …
    Qwen/Qwen3.5-4B                                [verifier]    4.660B (verified)
    mistralai/Mistral-Small-3.2-24B-Instruct-2506  [enumerator] 24.011B (verified)
    total: 28.67B    RESULT: PASS

git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no VAL or TEST gold was read.

---

## 36. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M14 value.** Stage-B
families come from §10.1 verbatim. Stage-A framings are the minimal
structurally-distinct set "independent prompts" implies, each with a written
rationale. `min_independent_groups` defaults to 1 — the smallest number that can
mean "independent evidence". `conflict_policy` has one supported value. Status
and locality cues are ordinary English phrasings and the contract's own
hard-negative rules. Decode is greedy. Every person and place in the tests is
fictional.

---

## 37. Challenge compliance

Allowed and used: frozen-model inference, independent life-status prompts,
deterministic locality parsing, model-generated parametric recall, reuse of the
already-budgeted second model family as a generator, non-neural evidence
bookkeeping.

Absent and structurally prevented: web, RAG, Wikipedia, Wikidata, online
obituary or death registry, external biography corpus, factual cache, third
model, fine-tuning, LoRA, learned gate, learned temporal classifier,
task-trained verifier.

---

## 38. Non-goals

M15–M21 remain unimplemented, and no placeholder files were created — the
`specialists` package contains exactly ten files, asserted by a test:

M15 Small-Set Closure Specialist · M16 Atomic Consensus Engine · M17 Specialist
Verifier Suite · M18 Bidirectional/Counterfactual Checks · M19 Coverage
Gap/Missingness · M20 Relation Budget Scheduler · M21 Expected-Value
Micro-Planner.

---

## 39. Verdict

**PASS.**

M14 implements §10's separation of "does an object exist?" from "which object is
it?": three independent Stage-A framings, a local gate that spends no Stage-B
calls without sufficient independent evidence, §10.1's exact four Stage-B
families, §10.3's three NULL-evidence classes kept apart — with epistemic
abstention firmly in the weak class after the §15A correction, and failed recall
never promoted, and §10.2's branch implemented as cross-family recall with no
freshness claim and no external lookup. It applies to `personHasCityOfDeath` and
is structurally unable to reach the other five. It is a sibling of M12 and M13,
not a dependant. Its calls are real, attributable, counted exactly once, and
genuinely absent when the gate withholds them. It accepts no city, declares no
answer empty, verifies nothing and controls nothing. Enabling it leaves
predictions, candidate graphs and every upstream artefact — including both
siblings' — byte-identical. It is disabled by default in every shipped config.

Next architecture step: **M15 Small-Set Closure Specialist** — not implemented
here.
