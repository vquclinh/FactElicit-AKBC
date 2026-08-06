# Audit 0020 — Module 13: Large-Open-Set Specialist Conformance

Status: **PASS**
Date: 2026-08-06
Milestone: second Layer-2 specialist (M13 of M9–M21).
Mode: **shadow**, and **disabled by default** — M13 spends real neural calls.

---

## 1. Objective and scope

Implement **M13 Large-Open-Set Specialist** for `awardWonBy`: run a direct seed
query, partition the recall space into the proposal's generic non-factual
facets, mine Module 11's recall, and collect **atomic** candidate mentions with
full provenance and descriptive per-facet yield.

In scope: the typed contract, the facet and near-miss registries, atomic


extraction, occurrence and facet aggregation, configuration, the observability
artefact, and the M11→M13 seam.

Out of scope and not implemented: M14–M21. No placeholder files.

**M13 decides nothing.** No acceptance, no score, no pruning, no verifier, no
control. §19–21 below prove it.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§9** header | "Award recovery is a set-reconstruction problem, not one-shot list generation"; ASC — merging **atomic subparts** across samples beats selecting one generation. |
| **§9.1** "Facet decomposition" | "M13 runs a direct seed query and then creates generic, non-factual facets": temporal slices/eras; recipient type (**where contract allows**); official category/discipline (**when the award defines categories**); geography (**only when semantically appropriate**); missingness facet. |
| **§9.2** "Atomic support score" | `S_award(o) = w_I·I(o) + w_V·p̃_V(o) + w_X·X(o) − w_C·C(o) − w_R·R_near(o)`, and "**Same-view stochastic repeats increase only total support, not I**". |
| **§9.3** "Compute reservation" | `B_r = B_seed + B_facet + B_verify + B_reverse + B_reserve`. |
| **§9.4** "Tiered set pruning" | Tiers A–D routing candidates to verifiers or pruning them. |
| **§7.1–7.2** (M11) | The three probe families M13 mines, and the evidence-hygiene rule that pseudo-context is an acquisition artifact. |
| **§6.2/6.4** (M10), **§5** (M9) | The prompt program M13 renders from and the identity it carries. |
| **§8** (M12) | The sibling boundary. |
| **§10–§14** (M14–M15) | Specialist responsibilities M13 must not take. |
| **Appendix C, Table 12** | M13 I/O: "set QuerySpec + graph" → "facet plan + shortlist", Neural: **Mixed**. |

Also read: Audits 0016–0019 and the current source.

### What of §9 is M13's, and what is not — reported, not resolved silently

**§9.1 is implemented in full** (§7 below).

**§9.2 is implemented only in the part M13 can compute.** The score needs
`p̃_V(o)`, a *calibrated verifier probability* (Module 17), and `X(o)`,
*cross-model support* (Module 16's fusion). Neither exists. M13 therefore
computes `I(o)` — `independent_support`, with the "same-view repeats increase
only total support" rule enforced exactly — and the `R_near` inputs as
near-miss flags, and forms **no weighted sum**. Assembling `S_award` is Module
16's once its inputs exist. A test asserts `S_award`, `candidate_score`, `w_I`
and `cross_model_support` appear nowhere.

**§9.3 is Module 20's.** Reserving `B_verify` so discovery cannot spend it is
relation-aware budget scheduling. M13 uses a fixed plan whose cost is knowable
in advance (`plan.estimated_calls`) precisely so M20 can schedule it later.

**§9.4 is Modules 16's and 17's.** Tiers A–D route candidates to a blind or
adversarial verifier and prune the rest — verification and acceptance, both
explicitly outside M13. A test asserts `tier_a`, `prune` and `spot_check` are
absent.

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
M13 Large-Open-Set Specialist     <- this milestone   (sibling of M12)
    v
[future M16 Consensus -> M17 Verification -> M19 Missingness -> M20/M21]

M2 -> M3 -> ... -> M8             (unchanged production path)
```

One seam, in `CoverPipeline.enumerate_query`, alongside M12 inside the existing
M9→M10→M11 branch. `large_set_specialist=None` is the default and is the
pre-M13 path.

**M12 and M13 are siblings, not a chain.** They cover disjoint relations,
neither imports the other, and either may be enabled alone — asserted by tests
in both suites, including an AST import check.

---

## 4. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/specialists/large_set_types.py` | **New.** `AwardCandidateObservation`, `CandidateOccurrence`, `FacetSearchState`, `LargeSetFacet`, `LargeSetProbe`, plan/result, 4 enums. |
| `src/cover_kbc/specialists/large_set_registry.py` | **New.** `LARGE_SET_RELATIONS`, facet templates, near-miss cues, `check_large_set_registry_consistency`. |
| `src/cover_kbc/specialists/large_set_specialist.py` | **New.** `LargeSetSpecialist`, extraction, classification, aggregation, config. |
| `src/cover_kbc/specialists/__init__.py` | M13 exports added; M12's unchanged. |
| `src/cover_kbc/specialists/numeric_specialist.py` | **One line**: the `specialists` config guard admits the `large_open_set` sibling key. Nothing else in M12 changed. |
| `src/cover_kbc/pipeline.py` | `large_set_specialist=None` arg, `large_set_results`, `_run_large_set_specialist`, 3 lines at the seam. |
| `scripts/run_staged.py`, `scripts/run_cover.py` | Build M13; write `large_open_set_specialist.jsonl`. |
| 3 configs | `specialists.large_open_set` block, `enabled: false`. |
| `tests/test_large_set_specialist.py` | **New.** 108 tests. |
| `tests/test_numeric_specialist.py` | Two tests rescoped (§27). |

`benchmark/` untouched.

---

## 5-6. Public types and result contract

```python
class LargeSetFacetKind(str, Enum)     # seed | temporal | recipient_type |
                                       # category | geography | missingness
class AwardMentionKind(str, Enum)      # TARGET_RECIPIENT | NOMINEE | WINNING_WORK |
                                       # ADJACENT_AWARD | DIFFERENT_CATEGORY | RESCINDED
class LargeSetParseStatus(str, Enum)   # OK | EMPTY | ABSTAINED | NO_CANDIDATES | RUNTIME_ERROR
class MentionSource(str, Enum)         # PARAMETRIC_MEMORY | SPECIALIST_PROBE
```

`AwardCandidateObservation` — surface + `normalized_surface`, source,
`operation_id`, `facet_id`, `facet_kind`, `independence_group`, `sample_index`,
`prompt_sha256`, `model_id`, `raw_text`, `mention_context`, `mention_kind`,
`parse_status`, `ambiguity_flags`, `error`, and `verified` fixed to `False` by
`__post_init__`.

`CandidateOccurrence` — surfaces, `total_support`, `independent_support`
(`I(o)`), `independence_groups`, `facet_ids`, `operation_ids`,
`near_miss_kinds`. **No score, no rank, no acceptance.**

`FacetSearchState` — `probed`, `operations`, `empty_operations`, `mentions`,
`target_mentions`, `unique_surfaces`, `new_surfaces`, `near_miss_mentions`,
derived `duplicate_surfaces` and `novelty_ratio`.

`LargeSetSpecialistPlan` / `LargeSetSpecialistResult` — the four upstream
versions, query identity, facets, probes, observations, occurrences, facet
states, errors, cost; derived `total_mentions`, `unique_candidates`,
`near_miss_mentions`, `duplicate_ratio`.

---

## 7. Facet taxonomy

Exactly §9.1's five dimensions, plus the seed query it mandates first:

| Dimension | Enabled | Slices |
| --- | :---: | --- |
| *(seed query)* | always | `seed` |
| `temporal` | ✓ | `temporal_early`, `temporal_middle`, `temporal_recent` |
| `recipient_type` | ✓ | `recipient_person`, `recipient_group`, `recipient_organisation`, `recipient_project` |
| `category` | ✓ | `category_dimension` |
| `geography` | **—** | (none) |
| `missingness` | ✓ | `missingness_uncovered` |

Ten probes for `awardWonBy`, knowable before any call. Config may restrict the
dimensions; the seed always runs, and asking to disable it raises.

**Geography is declared and disabled.** §9.1 admits it "only when semantically
appropriate". The contract defines a recipient as a person, group, organisation
or project and gives the relation no geographic dimension, so partitioning by
region would impose structure the contract does not have. Declared so the
taxonomy matches the proposal, disabled with a written rationale, and a
consistency check fails if any §9.1 dimension is neither declared nor disabled.

---

## 8. Specialist probe families

One probe per facet slice, plus the seed. M13 adds **no probe family of its
own** beyond §9.1's partitions: Module 11's pseudo-memory, self-ask and
query-rewrite families are consumed as upstream memory, not re-declared.

**M13 is not M2.** Module 2 already has award views —
`award_facet_temporal`, `award_facet_recipient_type`, `award_facet_category`,
`award_missing` — that ask **one** prompt to sweep every decade or recipient
type internally. M13 issues one probe **per slice**, so per-facet yield and
coverage are observable, which is what §9.1's missingness facet and Modules
19–21 need. A test asserts M13 references no `ViewSpec`, `views_for`,
`get_view`, `ElicitationEngine` or M2 view id, and that M2's views are
unchanged.

---

## 9. Prompt construction from Module 10

Every probe is a generic frame plus the facet's instruction. The relation's
meaning comes from Module 10 — `task_semantics`, `subject_directives`,
`negative_constraints` — and is never restated. A test asserts every probe
prompt contains M10's definition and every negative constraint, and that no
relation name or contract definition appears in M13's executable code.

---

## 10. Consumption of Module 11 memory

M13 mines atomic mentions from all three M11 families, carrying through
`operation_id`, `independence_group`, `sample_index`, `prompt_sha256` and
`model_id` unchanged. Mining costs **zero calls**.

The M11 records stay `verified = False` — asserted after mining — and the
derived observations are `verified = False` by construction. Nothing is passed
to Module 4, which M13 cannot reach. A retrieval result belonging to a different
query is rejected.

---

## 11. Atomic candidate extraction

A generated list becomes **many** observations — §9's whole premise. Splitting
is line-first, then on `;`, `•` and ` | `. Normalisation removes list structure
only: bullets, numbering, lettering, surrounding straight or curly quotes, a
trailing year, a trailing parenthetical or dash clause. Every removal is
recorded as a flag and the raw surface is preserved.

**A comma is deliberately not a separator.** "Institute Gamma, Delta Branch" and
"Recipient Beta, Jr." are indistinguishable without knowing the names, so
splitting on commas would invent entities.

**Normalisation never resolves.** No translation, transliteration, initial
expansion, alias resolution or merging — all need world knowledge M13 does not
have. A test asserts `R. Alpha`, `Recipient Alpha`, `Institut Gamma` and
`Institute Gamma` all survive unchanged. No external NER, no entity linker, no
award database.

Prose too long to be a name is **flagged**, not discarded: `raw_text` is kept
and a later module decides. Output with no separable candidate becomes an
explicit `NO_CANDIDATES` observation rather than vanishing.

---

## 12. Award near-miss taxonomy

Five kinds, one per contract `hard_negative_rule`, checked for exact
correspondence:

| Kind | Contract rule | Example cue |
| --- | --- | --- |
| `NOMINEE` | "a nominee, finalist or shortlisted entity that did not win" | nominee, shortlisted, runner-up |
| `RESCINDED` | "a recipient whose award was later rescinded or withdrawn" | rescinded, revoked, stripped of |
| `ADJACENT_AWARD` | "a recipient of a similarly named predecessor or successor award" | predecessor award, similarly named |
| `DIFFERENT_CATEGORY` | "a recipient of a different category or a different award from the same organisation" | different category, another category |
| `WINNING_WORK` | "the winning work (book, film, album, paper) instead of the entity" | winning work, for the novel |

`TARGET_RECIPIENT` is the default for an unlabelled name in a probe that asked
for recipients, which is why it is not a declared cue.

Classification is **lexical and clause-scoped**: one output naming a winner and
a nominee yields two observations with distinct kinds. It records *what the
model said*, never a verdict — a `NOMINEE` mention is still `verified = False`.

---

## 13. Facets are search partitions, not facts

Enforced, not merely intended. A test scans every probe's instruction for
"this award has", "the award was founded", "the award is given in",
"definitely" and "always", and fails on any.

The `category` dimension carries its condition **in the prompt** — "If this
award is given in several categories … If it has no categories, answer NONE" —
because whether a given award defines categories is a fact M13 cannot know and
deterministic code must not decide.

---

## 14. Temporal decomposition

Three slices, stated **relative to the award** and carrying **no calendar
dates**: "the award's earliest years", "its middle period", "its most recent
years". A test asserts no four-digit year appears in any temporal instruction.

Naming a date range would assert the award spanned it — a fact. A relative era
partitions the recall space without claiming anything, which is the only form of
temporal partition available to a closed-book system. Empty output from any era
is legitimate and recorded as an empty facet.

No boundary was selected from data; there are no boundaries to select.

---

## 15. Recipient-type and category decomposition

Four recipient-type slices — person, group, organisation, project — matching the
contract's own clause: "people, groups, organisations and projects are all valid
recipient types". §9.1's "where contract allows" is therefore satisfied for all
four, and a test cross-checks the slice ids against the contract's positive
rules.

Asking about a type is not a claim that the award has recipients of it; an empty
answer for any type is legitimate.

---

## 16-17. Occurrence provenance and independence

Every observation records its operation, facet id, facet kind and independence
group. `CandidateOccurrence` aggregates by a case-and-whitespace-normalised key
— nothing cleverer, because a wrong merge destroys an entity irrecoverably.

`independent_support` is §9.2's `I(o)`: **distinct independence groups**.
Slices of one dimension share a group, and so do resamples — three temporal
slices naming the same recipient give `total_support == 3`,
`independent_support == 1`. Distinct dimensions are distinct sources: seed +
temporal + category gives 3 and 3. Both are tested.

Occurrence order is deterministic (support, then alphabetical) and is
presentation, not ranking — a test asserts no `score` or `rank` attribute
exists. M13 groups are **not** mapped into Module 3's core independence groups;
that is Module 16's integration.

---

## 18. Descriptive yield metrics

Per facet: `operations`, `empty_operations`, `mentions`, `target_mentions`,
`unique_surfaces`, `new_surfaces`, `duplicate_surfaces`, `novelty_ratio`,
`near_miss_mentions`. Per result: `total_mentions`, `unique_candidates`,
`near_miss_mentions`, `duplicate_ratio`.

"New" means first seen in this facet, in plan order — deterministic and
order-stable. These are the precision-relevant signals §9.4 and Modules 19–21
will want.

**M13 computes them and never reads them back.** A test runs the same query
against a barren runtime and a rich one and asserts identical call counts and
identical probe lists: a facet returning nothing cannot cause another probe.

---

## 19-21. Why M13 does not implement M16, M17 or M19–M21

**M16.** No `accepted`, `ACCEPT`, `REJECTED`, `consensus`, `fuse_evidence`,
`confidence_threshold` or `vote_threshold` anywhere; the serialised result is
asserted free of `accepted`, `rejected`, `verdict`, `final_score`. A candidate
seen in five facets is five sightings, not a fact.

**M17.** No `VerificationLabel`, `score_labels`, `LABEL_TOKENS`,
`VerifierTemplate`, `verifier_runtime`, `build_verifier_prompt`, `A = VALID` or
`adversarial`. No verifier call. Module 4's entire prompt surface is pinned by
sha256 `3acd7109…e6d874`. Near-miss classification is acquisition metadata, not
a factual verdict.

**M19–M21.** No `should_stop`, `next_action`, `allocate_budget`,
`schedule_budget`, `residual_coverage`, `expected_value` or
`missingness_estimate`. The missingness *facet* runs — always, in a fixed plan,
shown whatever has been found so far, including "(none yet)". *Whether* an
underrepresented region justifies more search is Module 19's and Module 21's.

---

## 22. Why no external factual lookup exists

An AST import scan rejects `requests`, `httpx`, `urllib`, `socket`, `http`,
`aiohttp`, `sqlite3`, `faiss`, `chromadb`, `pinecone`, `torch`, `transformers`,
`spacy`, `nltk`. A code scan rejects `wikipedia`, `wikidata`, `http://`,
`https://`, `award_db`, `entity_linker`, `ner_model`, `api_key`.

No award list, no recipient table, no external corpus, no learned extractor.
Near-miss cues are words the model itself writes next to a name.

---

## 23-24. Runtime integration and call accounting

M13 receives the **frozen enumerator** `LMRuntime` and calls
`runtime.generate(...)`. No new loader; a clean-subprocess probe loads no
`torch`, `transformers` or `mistral_common`.

* **Every call attributable** — `operation_id`, facet id and kind, independence
  group, `prompt_sha256`, model identity, tokens, parse status, error.
* **Counted exactly once, measured not assumed.** A ten-probe run gives
  `calls == 10 == runtime.calls`; a counter-silent runtime gives `0`.
* **No double counting.** M11's and M13's totals *sum* to
  `pipeline.shadow_calls` and never exceed the runtime's own count.
* **No double subtraction.** M13 adds to the same shadow counter M11
  established; `run_staged` still subtracts once.
* **Outside Module 7's budget.** Pipelines with and without M13 produce
  identical `graph.budget_snapshot`, while `shadow_calls` differs by exactly
  M13's ten probes.
* **Reported honestly** — Phase A prints
  `[M13] large-open-set specialist: … (N queries, C shadow calls, T generated tokens)`.

---

## 25-27. Shadow isolation, disabled path, M12 preservation

The real staged CLI, run twice with configs differing only in
`specialists.large_open_set.enabled` (M9/M10/M11/M12 on in both):

```
predictions.jsonl        IDENTICAL
diagnostics.json         IDENTICAL
trace.jsonl              IDENTICAL
stage_a_enumerated.jsonl IDENTICAL
stage_b_verified.jsonl   IDENTICAL
query_profiles.jsonl     IDENTICAL      <- M9 unchanged
prompt_programs.jsonl    IDENTICAL      <- M10 unchanged
parametric_memory.jsonl  IDENTICAL      <- M11 unchanged
numeric_specialist.jsonl IDENTICAL      <- M12 unchanged
```

`large_open_set_specialist.jsonl` is present with M13 on, absent with it off,
and absent entirely for any non-large-set relation even with M13 enabled.

M13 references no `EvidenceGraph`, `build_graph`, `add_candidate` or
`Evidence(`; a graph is unchanged after an analysis, and the serialised graph
after a piped run contains no M13-unique token.

**M12 preservation.** M12's own modules are byte-unchanged except one line in
`build_numeric_specialist`'s `specialists` key guard, which now admits the
sibling key. Two M12 tests were rescoped, both because M13 legitimately exists:
`test_no_other_specialist_logic_exists` asserted the package held exactly four
files (now seven — rescoped to "these seven and no M14–M21 file", plus a new
test that M12 imports nothing from M13), and `test_unsupported_mode_and_unknown_keys_are_rejected`
used `large_open_set` as its example unknown key (now `null_temporal`). M12's
numeric semantics, artefact schema and disabled-by-default path are untouched
and asserted.

---

## 28. Persistence

`large_open_set_specialist.jsonl`, one record per **LARGE_OPEN_SET query
analysed**, written in Phase A in manifest order. Carries the plan (four
versions, query identity, facets with rationales, probes), every observation
with full provenance, occurrences, facet states, errors and cost.

A test asserts manifest ordering, the presence of every provenance key, and that
no `gold`, `ObjectEntities`, `accepted`, `prediction` or `final_score` appears.

`query_profiles.jsonl`, `prompt_programs.jsonl`, `parametric_memory.jsonl`,
`numeric_specialist.jsonl`, both stages, `diagnostics.json`, `trace.jsonl` and
`predictions.jsonl` are untouched — §25 proves it byte for byte.

---

## 29. Error handling

Ten distinguished situations, none silent: upstream identity mismatch
(`LargeSetSpecialistError` listing every problem), unsupported
relation/programme, M13-without-M9/M10/M11 (at config *and* pipeline), malformed
config (unknown key, mode, facet kind, duplicate, non-list, seed-as-facet,
disabled facet requested), runtime failure, empty output, abstention, malformed
prose, candidate-less output.

A runtime failure produces an explicit `RUNTIME_ERROR` observation with an empty
surface — **no fabricated candidate, no empty factual set**. One failing probe
does not kill the others.

---

## 30. Test results

```
python -m pytest -q
    1491 passed, 3 skipped        (1383 before; +108)
```

`tests/test_large_set_specialist.py`, 108 tests, covering all 36 required areas:
proposal facet set and the §9.2/§9.3/§9.4 exclusions; routing for all six
relations; sibling independence in both directions; five parameterised identity
mismatches; facet taxonomy including the disabled geography dimension; probe
plan and determinism; temporal date-freedom; facets-as-partitions; prompt
authority; no M2 view references; M11 provenance and unverifiability; atomic
splitting across seven list shapes; comma safety; flagged stripping; long-prose
flagging; no name resolution; the five-kind near-miss taxonomy; winner-plus-
nominee in one output; occurrence provenance; independence for slices,
resamples and distinct dimensions; deterministic non-ranking order; per-facet
yield; that yield never changes the plan; the always-running missingness probe
with and without prior candidates; no consensus, verifier, or control
semantics; the M4 hash pin; no external retrieval; graph untouched; exact call
accounting including a counter-silent runtime and a double-count check; runtime
and partial failure; empty and abstained output; JSON round-trip for every
public type; persistence ordering and provenance; shadow invariance; M12
preservation; ten configuration failures; zero new parameters.

Two failures surfaced during development, **both my own test bugs**: a scan for
`"numeric"` in M13's source matched a docstring that *compares* M13 with the
numeric specialist (rescoped to an AST import check), and a leak scan for
`facet_id` / `independent_support` matched Module 2's `GenerationRecord` field
and Module 0's `SelectionPolicy` — both legitimately on the production graph
(rescoped to M13-unique tokens). No defect was found in the module itself.

---

## 31-33. pyflakes, model budget, benchmark integrity

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

## 34. No performance-based tuning

**No TRAIN, VAL or TEST performance was used to select any M13 value.** The
facet dimensions come from §9.1. The temporal slices carry no boundaries to
tune. The recipient-type slices come from the contract's own clause. The
near-miss cues come from the contract's hard-negative rules. Decode is greedy
with a 384-token limit sized to a list answer. Every award, recipient and work
in the tests is fictional.

---

## 35. Challenge compliance

Allowed and used: frozen-model inference, deterministic prompt and facet
construction, candidate parsing, non-neural aggregation and descriptive
statistics, provenance tracking.

Absent and structurally prevented: external retrieval, factual award datasets,
web, Wikipedia, Wikidata, fine-tuning, LoRA, learned entity extraction, learned
specialist router, learned candidate scorer, task-trained classifier.

---

## 36. Non-goals

M14–M21 remain unimplemented, and no placeholder files were created — the
`specialists` package contains exactly seven files, asserted by a test:

M14 Null/Temporal Specialist · M15 Small-Set Closure Specialist · M16 Atomic
Consensus Engine · M17 Specialist Verifier Suite · M18
Bidirectional/Counterfactual/Dispute Verifier · M19 Coverage Gap & Missingness
Estimator · M20 Relation Budget Scheduler · M21 Expected-Value Micro-Planner.

---

## 37. Verdict

**PASS.**

M13 is implemented as §9.1 specifies: a direct seed query followed by generic
non-factual facets, with the two conditional dimensions handled honestly — the
category condition carried in the prompt for the model to resolve, geography
declared and disabled with a written rationale. Temporal slices carry no dates,
so no facet asserts anything about the award. A generated list becomes many
atomic observations with full provenance; near misses stay distinguishable and
unverified; `I(o)` is computed with §9.2's repeat rule enforced. §9.2's score,
§9.3's reservation and §9.4's pruning are deferred to the modules that own their
inputs. M13 applies to `awardWonBy` and is structurally unable to reach the
other five. It is a sibling of M12, not a dependant. Its calls are real,
attributable, counted exactly once and outside Module 7's budget. Enabling it
leaves predictions, candidate graphs and every upstream artefact — including
M12's — byte-identical. It is disabled by default in every shipped config.

Next architecture step: **M14 Null/Temporal Specialist** — not implemented here.
