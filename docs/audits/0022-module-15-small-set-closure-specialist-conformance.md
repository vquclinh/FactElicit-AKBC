# Audit 0022 — Module 15: Small-Set Closure Specialist Conformance

Status: **PASS** (amended in place — see §17A)
Date: 2026-08-06
Amended: 2026-08-06, correcting the cross-family trigger to the two-level
condition of §20.5 step 2, and correcting the test count.
Milestone: fourth and final Layer-2 specialist (M15 of M9–M21).
Mode: **shadow**, and **disabled by default** — M15 spends real neural calls.

---

## 1. Objective and scope

Implement **M15 Small-Set Closure Specialist** for the two `SMALL_SET`
relations: `countryLandBordersCountry` (§11.1) and
`companyTradesAtStockExchange` (§11.2), plus §11.3's shared closure inputs.

In scope: the typed contract, the two relation registries, candidate and
listing-status parsing, the public-listing gate, occurrence/independence
bookkeeping, the missingness probe, closure snapshots and signals, pending
checks for Module 18, reuse of Module 14's cross-family primitive,
configuration, the observability artefact, and the M11→M15 seam.

Out of scope and not implemented: M16–M21. No placeholder files.

**M15 closes nothing and verifies nothing.** §11.3's rule is stated *"Given
accepted set `A_t`"* and no accepted set exists until M16/M17 do; §11.1's
reverse checks and §11.2's company-itself checks are *requested*, never
executed. §24–§30 prove both.

---

## 2. Proposal sections read

`COVER_KBC_Technical_Proposal_New.pdf`, read before any code was written:

| Section | What it fixed |
| --- | --- |
| **§11** header | "Borders and stock exchanges usually have small cardinality, so the objective is **high-precision closure**." |
| **§11.1** Borders | "Borders already reach 0.9531; the default policy must therefore be **minimal-change**. Use direct + geographic decomposition + reverse checks for singleton/territory ambiguity. **Do not increase compute if the set is already stable.**" |
| **§11.2** Stock exchange | "Stock requires a **public-listing gate**, **company-itself checks**, **primary/secondary/dual listing handling**, and **temporal status**. An **abnormally long candidate list** triggers parent/subsidiary/index confusion filters. **M14's freshness branch may be invoked as a subroutine.**" |
| **§11.3** Closure test | "Given accepted set `A_t`, the missingness probe returns new candidates `N_t`. Stop when `|N_t| = 0`, `J(A_t, A_{t−1}) > τ_J`, and **no high-risk singleton remains**." |
| **§5.1** Table 3 | Routing: `countryLandBordersCountry` → M15, secondary "M18 reverse check for singleton/territory risk"; `companyTradesAtStockExchange` → "M15 Small-Set + temporal subroutine", secondary "M14 freshness; M18 parent/subsidiary contrast". |
| **§10.2** (M14) | The cross-family recall branch M15 is permitted to invoke — and its "**No factual web lookup is used**" constraint. |
| **§7.1–7.2** (M11), **§6** (M10), **§5** (M9) | Upstream state M15 consumes and never rebuilds. |
| **§8–§10** (M12/M13/M14) | The sibling boundary. |
| **§12–§14**, Appendix C | What M16/M17/M18 own; the module I/O table ("M15 Small-Set Clo- … query + set state → closure/reverse actions"). |

Also read: Audits 0016–0021 and the current source.

### Interpretations recorded rather than resolved silently

**1. The border "direct" probe is declared but disabled by default.** §11.1
names "direct + geographic decomposition" *and*, in the same paragraph, orders
minimal change and "do not increase compute if the set is already stable". M11
`query_rewrite` already asks this relation directly, closed-book, on every
query, and M2's production views ask it again. A third direct ask would be the
same structural question through the same checkpoint — a resample, not new
evidence — and it would raise per-query cost for the relation the proposal
singles out as already strong. So `border_direct` is declared in the registry
with `enabled=False` and a recorded rationale; the geographic decomposition
(genuinely a different framing) and the missingness probe run. One config line
(`enable_facets: [border_direct]`) turns it on. Tested both ways.

**2. §11.3 is measured, not fired.** The rule is conditioned on an *accepted*
set. M15 has observations, not acceptances. It therefore computes and records
every input the rule needs — `|N_t|`, `J` over its own consecutive observed
snapshots, the singleton list, the high-risk singleton list — and forms no
verdict. There is no `should_stop`, no `CLOSED`, no `A_t`. §24 expands.

**3. "Do not increase compute if the set is already stable" is satisfied
structurally, not adaptively.** A runtime stability check that spends calls only
when a set looks unstable is adaptive compute allocation, which is M19–M21's.
M15 instead fixes the border plan at two calls — the minimum that can produce a
missingness reading at all — so compute cannot grow with instability. The
stability *measurement* is produced for the future controller.

**4. `τ_J` is not set.** §11.3 leaves the threshold symbolic. Choosing a value
would be tuning, and comparing against it would be deciding. `J` is recorded as
a float; no threshold constant exists in M15.

**5. The stock near-miss taxonomy follows the contract, not the brief's example
list.** The implementation brief mentions a "different security/ADR context"
kind. `companyTradesAtStockExchange`'s contract draws no such distinction, and
inventing a taxonomy class the contract cannot adjudicate would produce
observations nothing downstream can read. The five implemented near-miss kinds
are exactly the contract's five hard-negative rules (parent listing, subsidiary
listing, index/non-exchange, historical/delisted, private/not-listed).

**6. "Abnormally long" is structural.** §11.2 gives no number. The threshold is
derived from the relation contract's own
`verification.auto_accept_independent_support` (3 for stock) times four — a list
four times longer than the evidence the contract demands per accepted item is
anomalous by the contract's own scale. No dataset was consulted; the config can
override it. §15 expands.

---

## 3. Architecture position

```
    M0 contracts / M1 router
        v
    M9 profiler -> M10 compiler -> M11 parametric retrieval
        v
    M15 Small-Set Closure          <- this milestone
        |-- borders: minimal-change acquisition        (§11.1)
        |-- stock:   public-listing gate -> facets     (§11.2)
        |-- missingness probe (both)                   (§11.3)
        |-- cross-family recall (stock only)           (§10.2 reused)
        |-- closure signals                            (§11.3 inputs)
        \-- pending checks                             (for M18)
        v
    [future M16 consensus -> M17/M18 verification -> M19-M21 control]

    M2 -> M3 -> ... -> M8          (unchanged production path)
```

M15 is a **sibling** of M12/M13/M14, not a successor: it reads M9/M10/M11 and
writes one observability artefact. It is on no production path. With Layer 2
complete, all six relations now have a specialist:

| Relation | Program type | Specialist |
| --- | --- | --- |
| `hasCapacity`, `hasArea` | `NUMERIC` | M12 |
| `awardWonBy` | `LARGE_OPEN_SET` | M13 |
| `personHasCityOfDeath` | `NULL_SINGLE` | M14 |
| `countryLandBordersCountry`, `companyTradesAtStockExchange` | `SMALL_SET` | **M15** |

---

## 4. Files changed

New:

| File | Lines | Contents |
| --- | --- | --- |
| `src/cover_kbc/specialists/cross_family.py` | 95 | The relation-agnostic cross-family recall primitive **extracted from M14** (§17). |
| `src/cover_kbc/specialists/small_set_types.py` | 965 | Enums, observations, occurrences, gate reading, closure signals, pending checks, plan, result. |
| `src/cover_kbc/specialists/small_set_registry.py` | 595 | Per-relation probe templates, mention cues, listing/temporal cues, consistency check. |
| `src/cover_kbc/specialists/small_set_specialist.py` | 1410 | Planning, execution, parsing, aggregation, gate, closure signals, pending checks. |
| `tests/test_small_set_specialist.py` | 1883 | 157 tests. |
| `docs/audits/0022-…md` | this file | — |

Modified:

| File | Change |
| --- | --- |
| `src/cover_kbc/specialists/null_temporal_types.py` | `RecallFamily` now imported from `cross_family` (same name, same serialised values). |
| `src/cover_kbc/specialists/null_temporal_specialist.py` | Two ad-hoc fields replaced by one call to the shared `decide_cross_family`. **Byte-identical outputs** (§17, §35). |
| `src/cover_kbc/specialists/numeric_specialist.py` | One line: the `specialists` config-key guard now admits all four Layer-2 keys. |
| `src/cover_kbc/specialists/__init__.py` | M15 exports; M15's `normalise_surface` exported as `normalise_small_set_surface` to avoid colliding with M14's. |
| `src/cover_kbc/pipeline.py` | Optional `small_set_specialist`, `small_set_results`, `_run_small_set_specialist`, three seam lines; M14's inline model-id comparison routed through `distinct_families`. |
| `scripts/run_staged.py`, `scripts/run_cover.py` | Build M15; write `small_set_specialist.jsonl`. |
| 3 × `configs/experiments/*.yaml` | `specialists.small_set_closure` block, `enabled: false`. |
| `tests/test_numeric_specialist.py` | Specialist file inventory 13 → 14 files; sibling scan includes `small_set`; the "unknown config key" example changed to a key M15 does not own. |

`benchmark/` untouched (§41).

---

## 5. Public M15 types

Enums: `SmallSetRelationKind` (BORDERS, STOCK), `SmallSetProbeFamily` (10),
`BorderMentionKind` (7), `StockMentionKind` (6), `ListingType`
(PRIMARY/SECONDARY/DUAL/UNKNOWN), `ListingTemporalStatus`, `ListingGateState`,
`ListingExistenceStatus`, `SmallSetParseStatus`, `SmallSetObservationSource`,
`PendingCheckKind` (4), `PendingCheckReason` (7), `CrossFamilyTrigger` (6, see
§17A).

Records: `SmallSetCandidateObservation`, `ListingStatusObservation`,
`SmallSetCandidateOccurrence`, `ClosureSnapshot`, `ClosureSignals`,
`ListingGateReading`, `PendingCheck`, `SmallSetProbe`, `SmallSetSpecialistPlan`,
`SmallSetSpecialistResult`. All frozen, all `to_json`/`from_json` round-tripped
by test.

Every model-derived record carries `verified: bool = False` and **raises** on
`verified=True`:

```
ValueError: Module 15 never verifies; verified=True is Module 17/18's to set
```

Nothing in M15 is named for a decision: there is no `accepted`, `rejected`,
`score`, `final`, `winner` or `should_stop` field anywhere in the schema, and a
test asserts their absence from the serialised payload.

---

## 6. Relation routing

`applies_to` requires `program.program_type is ProgramType.SMALL_SET`.
`small_set_spec` fails closed with `UnsupportedSmallSetRelation`. A test derives
the expected set from `CONTRACTS` (`program_type is SMALL_SET`) rather than
hard-coding it, and the other four relations are proved to raise. M15 never
branches on a relation name: a test scans the executable code (docstrings and
comments stripped via AST + tokenize) for all six relation names and every
contract definition string, and fails on any hit. Relation-specific knowledge
lives only in the registry, as data.

Identity is checked before any call: relation, subject, `row_index`,
`compiler_version` and `profile_version` must agree across the query, the M10
program, the contract and any M11 result. Each mismatch raises
`SmallSetSpecialistError`; five parametrised tests cover them.

---

## 7. Border minimal-change design

§11.1's constraint is the design, not a footnote. The border plan is:

| Probe | Family | Enabled | Why |
| --- | --- | --- | --- |
| `border_direct` | `BORDER_DIRECT` | **no** | M11 `query_rewrite` and M2's views already ask this exact question through the same checkpoint. Re-asking is a resample. |
| `border_geographic` | `BORDER_GEOGRAPHIC_DECOMPOSITION` | yes | §11.1's "geographic decomposition" — a compass-partition framing, structurally distinct from a flat ask. |
| `border_missingness` | `SMALL_SET_MISSINGNESS` | yes | §11.3's `N_t`. |

Two calls per border query, unconditional and fixed. No prompt bank, no
stochastic repetition, no cross-family branch, no second pass. `estimated_calls
== unconditional_calls == 2`, asserted. Enabling `border_direct` through
`enable_facets` yields three, also asserted — the lever exists and its cost is
visible.

Borders deliberately do **not** get the freshness subroutine: §11.2 grants it to
stock, and the plan's `cross_family_rationale` for borders says so in words
(`"…§11.2 grants the freshness subroutine to stock…"`).

---

## 8. Border specialist probe set

Both probes are rendered from M10's compiled program — task definition, subject,
and every negative constraint are copied into the prompt verbatim, asserted by
test for both relations. M15 writes no relation prose of its own beyond the
probe instruction and a closed-book system frame:

> You answer from your own internal knowledge only. You have no access to
> search, documents, databases or external tools. Answer with names only, one
> per line, and add no commentary. If there are none, answer exactly: NONE

The geographic probe asks for neighbours by compass direction; the parser
recognises `North: X` lines and treats the direction label as a label, not a
candidate. The missingness probe is shown what has already been found
(`Neighbours already named: …`) — asserted by a prompt-capturing test.

Planning is pure and deterministic: `plan(...) == plan(...)`.

---

## 9. Border semantic/ambiguity taxonomy

`BorderMentionKind` has one target kind plus **six** near-miss kinds, and a test
asserts the six are exactly the six hard-negative rules on the contract — the
taxonomy cannot drift from M0:

`MARITIME_ONLY`, `NEARBY_NOT_ADJACENT`, `NON_INTEGRAL_DEPENDENCY`,
`DISPUTED_CLAIM_ONLY`, `SUBNATIONAL_REGION`, `SUBJECT_ITSELF`.

Classification is **lexical only**: it reads the clause the model wrote around
the candidate and matches declared cue phrases. It notices the word "maritime";
it knows nothing about any country. A test scans for `borders_with`,
`adjacency_table`, `gazetteer`, `geonames`, `country_list`, `iso3166` and fails
on any, and asserts that normalisation never rewrites a territory name into a
sovereign state.

An unlabelled candidate returned by a probe that asked for the relation is a
**target** mention — the default is the target kind, and near-misses require a
cue. Nothing is ever dropped for being a near-miss: it is recorded, kinded, and
surfaced to the closure signals and to M18.

---

## 10. Border pending reverse-check logic

§11.1 asks for "reverse checks for singleton/territory ambiguity". M15 emits
**requests**:

| Trigger | `PendingCheckKind` | `PendingCheckReason` |
| --- | --- | --- |
| Candidate seen in exactly one independence group | `REVERSE_ADJACENCY` | `SINGLETON_CANDIDATE` |
| Candidate mentioned as a dependency/territory | `REVERSE_ADJACENCY` | `TERRITORY_AMBIGUITY` |

Each `PendingCheck` carries the candidate, the reason, and the `operation_ids`
and `independence_groups` that produced it, so M18 can attribute and cost the
check it will run. Requests are deterministic, deduplicated on
(kind, reason, candidate), and **executed by nothing**: after analysis,
`result.calls == plan.estimated_calls == runtime.calls`, and the serialised
payload contains no `verdict`, `accepted`, `rejected`, `VALID` or `INVALID`.

---

## 11. Stock two-stage / public-listing design

§11.2 says stock "requires a public-listing gate". The plan is therefore
two-stage and the second stage is **conditional**:

```
stage 1  stock_listing_gate        "is the company publicly listed?"
         stock_listing_existence   (independent second framing)
              |
              +-- NOT_PUBLICLY_LISTED_PLAUSIBLE  -> stop, 2 calls
              +-- UNRESOLVED                     -> stop, 2 calls
              |                                     (+1 rescue call if §17A's
              |                                      static eligibility holds)
stage 2  stock_primary / stock_secondary_dual / stock_temporal /
         stock_company_itself / stock_missingness           -> 7 calls total
         (+1 cross_family call if eligible AND this query's temporal
          picture is unresolved or conflicting - §17A)
```

A parametrised test drives all three gate states and asserts the exact call
counts (2 / 2 / 7) against the runtime's own counter, so "the facets did not
run" means no call was made, not that a result was discarded. Six further tests
pin the conditional branch's counts (§17A).

Note the asymmetry with M14: an unresolved gate **stops** the listing facets.
Spending listing calls on a company the model cannot place as public would be
acquiring candidates the gate cannot support. It does not stop §11.2's freshness
subroutine, which exists for exactly this uncertainty — see §17A.

---

## 12. Stock local gate

`parse_listing_status` prefers a bare one-word answer (`LISTED`, `NOT_LISTED`,
`UNKNOWN`), then declared cue phrases in an order that makes an explicit
negation win — "not publicly traded" is never read as "traded". Text naming no
recognisable status is `UNPARSED_STATUS`, never defaulted to a status.

`read_listing_gate` folds the observations:

* every group agreeing `LISTED` → `PUBLICLY_LISTED_PLAUSIBLE`;
* every group agreeing `NOT_LISTED` → `NOT_PUBLICLY_LISTED_PLAUSIBLE`;
* disagreement → `conflicted=True`, state `UNRESOLVED`;
* nothing, or only abstentions → `UNRESOLVED`.

Support counts **independence groups, not calls**: two resamples of one framing
give `total_observations=2, listed_support=1`.

The state names are deliberately epistemic — `…_PLAUSIBLE`, `UNRESOLVED` — and a
test asserts the enum contains no `ACCEPTED`, `REJECTED`, `TRUE` or `FALSE`, and
that the serialised reading contains no `final_empty`, `accepted`, `is_empty` or
`prediction`. `NOT_PUBLICLY_LISTED_PLAUSIBLE` is **not** a prediction that the
gold set is empty; it is a local reading that stops acquisition. Resolving it is
M16/M17's.

`conflict_policy` accepts only `unresolved`; anything else raises. Resolving a
contradiction is consensus, and consensus is M16.

---

## 13. Stock listing-type facet set

The four acquisition facets are §11.2's list, one probe each:

| Facet | §11.2 phrase |
| --- | --- |
| `stock_primary` | "primary/secondary/dual listing handling" (primary) |
| `stock_secondary_dual` | "primary/secondary/dual listing handling" (secondary, dual) |
| `stock_temporal` | "temporal status" |
| `stock_company_itself` | "company-itself checks" (acquisition half; the verification half is M18's) |

Facets are **search partitions, not assertions** — the same discipline M13
established. The secondary/dual instruction says outright that the question
presumes nothing ("…does not imply one exists"), asserted by test, and no probe
purpose contains "this company is listed on", "definitely" or "always".

`ListingType` is read from what the model wrote (`primary listing`, `secondary
listing`, `dual-listed`) and is `UNKNOWN` otherwise. M15 never infers a listing
type from a name.

---

## 14. Stock semantic/near-miss taxonomy

`StockMentionKind` has one target kind plus **five** near-miss kinds, asserted
equal to the contract's five hard-negative rules:

`PARENT_COMPANY_LISTING`, `SUBSIDIARY_LISTING`, `INDEX_OR_NON_EXCHANGE`,
`HISTORICAL_OR_DELISTED`, `PRIVATE_OR_NOT_LISTED`.

Cue matching is word-bounded (§16). As with borders, near-miss candidates are
kept and kinded, never silently dropped, and each raises a pending check (§25).

---

## 15. Candidate-explosion handling

§11.2: "An abnormally long candidate list triggers parent/subsidiary/index
confusion filters."

`_explosion_threshold` returns `config.candidate_explosion_threshold` when set,
otherwise `contract.verification.auto_accept_independent_support × 4` — 12 for
stock. The scale comes from M0's own contract, not from any split; a test pins
both the derivation and the override.

When the unique-candidate count exceeds it, `candidate_explosion=True` and every
candidate gets a `COMPANY_ITSELF` / `CANDIDATE_EXPLOSION` pending check — the
"confusion filter" as a *request*, since the filter §11.2 describes is a
contrast check and contrast checks are M18's. **Nothing is pruned**: a test
feeds 20 exchanges and asserts `unique_candidates == 20` afterwards. A normal
two-exchange answer does not trip the flag.

---

## 16. Temporal and listing-status handling

Temporal status is lexical and local: `current`/`currently`/`is listed` →
`CURRENT`; `former`/`formerly`/`delisted`/`no longer listed`/`previously` →
`FORMER_OR_DELISTED`; otherwise `UNCLEAR`. M15 infers nothing from dates it did
not generate, and a test scans for `datetime`, `date.today`, `knowledge_cutoff`,
`current_year`, `market_data` and `quote_api`.

Two defects were found by these tests and fixed:

**(a) The temporal probe's own prescribed answer was unparseable.** The probe
asks for `'<exchange>: current'` / `'<exchange>: former'`; the cue table
contained only `currently` and `formerly`, so every well-formed reply parsed as
`UNCLEAR`. The bare words are now in the table, with a comment recording why.
This is the same shape as M14's bare-name defect in Audit 0021 §32.

**(b) Substring cue matching misread longer words.** "concurrent" ends in
"current". All four cue classifiers now share `_matches_cue`, which compiles
each phrase with word-boundary guards applied only at ends that are word
characters — so a phrase written with a deliberate trailing space ("until ")
still matches what follows it. Regression tests cover "concurrently listed" and
"transformer".

Consistent with M14's corrected §15A semantics: an abstention is an abstention.
`UNKNOWN` from the gate is `ABSTAINED` / `UNRESOLVED`, never evidence that the
company is private.

---

## 17. M14 cross-family reuse

§11.2: "M14's freshness branch may be invoked as a subroutine." The brief
required reuse of the audited mechanism rather than a second implementation, and
the smallest possible extraction.

`cross_family.py` (95 lines) contains exactly what is relation-agnostic:

* `RecallFamily` — `PRIMARY_FAMILY` / `CROSS_FAMILY` (moved, not copied; the
  name and serialised values are M14's);
* `CrossFamilyDecision` — eligibility plus a rationale string;
* `decide_cross_family(...)` — the three-way rule: disabled → not eligible; no
  genuinely distinct second family configured → not eligible (a branch through
  the same checkpoint is a resample); local condition unmet → not eligible;
* `distinct_families(enumerator_model_id, verifier_model_id)` — the identity
  comparison, previously written inline in the pipeline.

M14 now calls it, supplying its own strings. All three reachable rationales are
asserted **byte-for-byte** against Audit 0021's values (§35). No M14 behaviour,
schema, artefact or test outcome changed.

M15's stock branch requires those three **static** conditions: config on, a
distinct family available, and M9 grading the relation temporally sensitive.
They are necessary and, as §17A records, **not sufficient** — execution also
requires this query's listing status to be uncertain. Cross-family observations
carry `recall_family=CROSS_FAMILY` and the other runtime's `model_id`, are
`verified=False`, and are distinguishable in the artefact — so consensus can
later refuse to count them as an independent family if the deployment does not
warrant it. **No factual web lookup is used** (§10.2), and nothing in M15 claims
a knowledge cutoff or a freshness fact: "freshness family" is a configured
architectural role.

---

## 17A. Correction — static eligibility is not a trigger

### The defect

As first written, M15 executed the cross-family branch whenever three
**static** conditions held: `cross_family_recall` enabled, a genuinely distinct
second family configured, and M9/M10 grading the relation temporally sensitive.
Every one of those is a property of the *architecture and the relation*. None is
a property of the *query*.

Since `companyTradesAtStockExchange` is graded temporally sensitive for every
row, the branch would have fired on every stock query whenever it was switched
on — including queries whose listing status M15 had just read cleanly.

### Why relation-level temporal risk was insufficient

§20.5, the end-to-end stock flow, is explicit about the order and the condition:

> 1. M15 public-listing gate + listing-type facets.
> 2. **M14 temporal/freshness subroutine if listing status uncertain.**
> 3. M18 parent/subsidiary counterfactual and company-itself verification.

"If listing status uncertain" is a per-query state, and it is evaluated *after*
step 1 has produced the gate and the listing facets. The original implementation
conflated it with §10.2's relation-level statement that "for death, stock, and
recent awards, model freshness is a distinct risk dimension" — which is a reason
the branch **exists** for stock, not a reason to run it on a given row.

Firing on every stock query would also have been a real cost: a call per query
whose only justification was the relation's identity.

### Static eligibility versus local uncertainty

The two questions are now answered separately and both must be yes:

| Level | Question | Where |
| --- | --- | --- |
| **A. Static eligibility** | "May this architecture use cross-family recall at all?" | `decide_cross_family` in the shared primitive — unchanged, still shared with M14. Surfaces as `plan.cross_family_eligible` + `plan.cross_family_rationale`. |
| **B. Local uncertainty** | "Does *this query* need the temporal rescue branch?" | `evaluate_cross_family_trigger`, an M15-local pure function over state M15 already recorded. Surfaces as `result.cross_family_trigger`. |

`CrossFamilyTrigger` has six values, so §9's four reader-visible states are
distinguishable rather than collapsed into one boolean:

| State | `cross_family_eligible` | `cross_family_trigger` | `cross_family_executed` |
| --- | --- | --- | --- |
| architecture unavailable | `false` | `NOT_ELIGIBLE` | `false` |
| available, query locally clear | `true` | `LOCALLY_CLEAR` | `false` |
| available, uncertainty fired | `true` | `UNRESOLVED_LISTING_GATE` / `TEMPORAL_STATUS_UNCLEAR` / `TEMPORAL_STATUS_CONFLICT` | `true` |
| fired, returned nothing or failed | `true` | as above | `true`, with `errors` and a `RUNTIME_ERROR` / `ABSTAINED` observation |
| available, never evaluated (no runtime) | `true` | `NOT_EVALUATED` | `false` |

The plan additionally carries `cross_family_condition`, a sentence stating the
runtime condition, so a rendered cross-family probe is not mistaken for a
planned call.

### Unresolved-gate rescue semantics

An `UNRESOLVED` gate now means "the ordinary listing facets stay withheld, **and**
if the architecture permits it, spend exactly one cross-family recall". This is
the case the proposal gives stock the freshness subroutine for: the local family
could not place the company, and a second family is the only remaining
closed-book source of evidence.

What the rescue explicitly does **not** do — asserted by test:

* it does not move the gate: the state stays `UNRESOLVED`, `listed_support`
  stays 0, and the gate is computed before the branch runs and never recomputed;
* it does not re-open the listing facets: `acquisition_executed` stays `false`
  and the missingness probe stays unrun;
* it accepts, rejects, prunes and closes nothing. Its output is one
  `verified=False` observation in the `cross_family_recall` independence group,
  for M16/M17/M18 to consume.

### Stage-2 temporal-uncertainty semantics

When the gate permits Stage 2 and the fixed facets have run, the branch fires on
the smallest deterministic reading of "temporal status uncertain" the existing
types support, evaluated per candidate surface over primary-family observations:

* **conflict** — one surface carries both a `CURRENT` and a
  `FORMER_OR_DELISTED` reading, or is written both as a `TARGET_EXCHANGE` and as
  a `HISTORICAL_OR_DELISTED` mention;
* **unclear** — Stage 2 named candidates but resolved **no** temporal status at
  all (every reading is `UNCLEAR`).

Deliberately *not* uncertainty, so the trigger cannot broaden: a resolved
`NOT_PUBLICLY_LISTED_PLAUSIBLE` gate; a Stage 2 that consistently reports former
listings (a reading, not a gap); and Stage 2 naming nothing at all (there is no
temporal claim to be uncertain about, and absence is the missingness probe's
question). Near-miss mentions *are* read here — "Exchange Alpha" from one facet
and "Exchange Alpha (delisted)" from another is precisely the disagreement being
looked for, and the second of those is a near-miss.

No score is formed, nothing is fitted, and no split was consulted.

### One-shot guarantee

At most **one** cross-family call, ever, per query:

* the registry declares exactly one cross-family template, and the consistency
  check now rejects a relation declaring more ("the freshness subroutine is
  one-shot; … would make it a budget to spend, which is Module 20/21's");
* execution slices `plan.cross_family_probes[:1]`;
* the trigger is evaluated **once**, before the branch, and never re-evaluated
  after it — the branch's own output cannot cause another branch;
* a `UNKNOWN`, empty, malformed or exception-raising response schedules nothing:
  tests drive all four and assert the cross runtime's counter stays at 1.

There is no loop, no retry, no expected-value calculation, no dynamic budget and
no stopping policy. A test scans the executable code for `retry`, `attempt`,
`budget`, `expected_value`, `utility`, `while `, `reschedule` and `escalate`.

### Exact call counts

Measured against each runtime's own counter (primary + cross-family runtimes
counted separately, then summed and compared with `result.calls`):

| Case | Gate | Static | Local | Calls |
| --- | --- | --- | --- | --- |
| 1 | `NOT_PUBLICLY_LISTED_PLAUSIBLE` | eligible | `LOCALLY_CLEAR` | 2 + 0 |
| 2 | `UNRESOLVED` | ineligible | `NOT_ELIGIBLE` | 2 + 0 |
| 3 | `UNRESOLVED` | eligible | `UNRESOLVED_LISTING_GATE` | 2 + **1**, zero listing facets |
| 4 | `PUBLICLY_LISTED_PLAUSIBLE` | eligible | `LOCALLY_CLEAR` | 7 + 0 |
| 5 | `PUBLICLY_LISTED_PLAUSIBLE` | eligible | `TEMPORAL_STATUS_UNCLEAR` | 7 + **1** |
| 6 | `PUBLICLY_LISTED_PLAUSIBLE` | eligible | `TEMPORAL_STATUS_CONFLICT` | 7 + **1** |

Borders remain at 2 and have no branch at all. The cross-family call is shadow
spend, added to `pipeline.shadow_calls` on the same path as every other M15
call, counted exactly once, and never in M7's budget.

### M14 regression

Unchanged by this pass: `cross_family.py` was not modified, M14's three
rationales are still asserted byte-for-byte against Audit 0021, and §15A's
corrected NULL semantics are re-asserted (`UNKNOWN` → `FAILED_RECALL_ONLY`).
M14's own 135 tests pass unchanged. The two-level condition is **M15-local**:
`CrossFamilyTrigger` and `evaluate_cross_family_trigger` live in M15's modules
and M14 neither imports nor sees them.

### Test count

157 M15 tests (139 + 18 for this correction); 1783 in the repository.

---

## 18. Proof no death semantics leaked into stock

M14's specialist is not imported by M15 — only `cross_family`. A test parses all
three M15 modules with AST and fails on any import naming `numeric`,
`large_set` or `null_temporal`. A second test scans M15's executable code for
`death`, `deceased`, `living`, `locality`, `burial`, `birthplace`, `stage_a`,
`stage_b` and fails on any. The reciprocal test from Audit 0021 (M14 contains no
`stock`, `exchange`, `listing`, `ticker`, `company`) still passes.

---

## 19. Module 11 memory consumption

When an M11 result is supplied, M15 mines its already-generated records for
candidates and **spends nothing**: `result.calls == 0`. Mined observations keep
their M11 provenance — `PSEUDO_MEMORY_SKETCH`, `SELF_ASK_DECOMPOSITION`,
`QUERY_REWRITE` as their independence group, source
`PARAMETRIC_MEMORY`, `verified=False`, and the M11 records themselves are
unmodified. Identity is checked first: an M11 result for a different query
raises.

M15 never rebuilds upstream: a test asserts `QueryProfiler`,
`PromptProgramCompiler` and `ParametricRetriever` appear nowhere in its
executable code. M15 is built only when M9, M10 **and** M11 are enabled;
otherwise construction raises naming the missing stage, and the pipeline refuses
a specialist without a retriever.

M11 is not free: its three probes are real calls, accounted in
`pipeline.shadow_calls` (§31).

---

## 20. Small-set candidate extraction

One probe output becomes zero or more **atomic** observations. Splitting is
newline-, semicolon- and bullet-based; a comma is **not** a separator, because
"Exchange Alpha, Main Board" is one name. Normalisation strips list structure —
bullets, numbering, quotes, a trailing parenthetical or dash clause — and
nothing else: no alias resolution, no translation, no merging a territory into a
state. Stripped clauses are retained as `mention_context` and drive the
taxonomy; every departure from the raw surface is recorded in
`ambiguity_flags`.

A probe that returned nothing still yields a record, with `EMPTY`,
`ABSTAINED`, `NO_CANDIDATES`, `UNPARSED_STATUS` or `RUNTIME_ERROR` — §11.3 must
be able to tell "the missingness probe found nothing" from "the missingness
probe never ran". Empty and abstained outputs never fabricate a candidate.

Within one response, deduplication is on **(surface, mention kind)**, not
surface alone. This was a fix: a model writing `Country Beta` and then
`Country Beta (a maritime boundary only)` previously kept only the bare first
mention and silently discarded the qualification — hiding exactly the near-miss
that §11.1's reverse check exists for. Both readings are now kept (flagged
`repeated_in_response`), so the surface appears in `conflicting_surfaces`; a
plain repeat still collapses to one record. Neither form adds an independence
group, so support cannot inflate. Regression tests cover both.

---

## 21. Independence semantics

`build_occurrences` counts, and only counts. `total_support` is mentions;
`independent_support` is **distinct independence groups**. Resampling one facet
twice gives `total_support=2, independent_support=1`; two structurally different
families give `2`. Facets of one relation are separate groups because they are
separate framings; samples within a facet are not. Mined M11 records carry their
own groups. No score, weight or ranking is computed anywhere — `φ(o)` is M16's.

`is_singleton` means "seen in exactly one independence group", which is what
§11.3's "high-risk singleton" needs and is a *description*, not a rejection.

---

## 22. Missingness probe

One probe per relation, run last, shown the surfaces already observed and asked
for anything missing. Its yield is §11.3's `N_t`:
`closure.new_surfaces`, `new_surface_count`, and `missingness_empty`.
`missingness_probed` records whether it ran at all, so `|N_t| = 0` is never
confused with "not asked". Candidates it repeats appear as
`duplicate_surfaces`, not as new ones.

---

## 23. Closure snapshots, Jaccard and signals

`ClosureSignals` carries:

| Field | §11.3 term |
| --- | --- |
| `before` (`ClosureSnapshot`, stage `observed_before_missingness`) | `A_{t−1}` analogue — **observed**, not accepted |
| `after` (stage `observed_after_missingness`) | `A_t` analogue — **observed**, not accepted |
| `new_surfaces`, `new_surface_count` | `N_t` |
| `jaccard` | `J(·,·)` |
| `singletons`, `high_risk_singletons` | "no high-risk singleton remains" |
| `conflicting_surfaces` | surfaces carrying both a target and a near-miss mention |
| `missingness_probed`, `missingness_empty` | whether the probe ran; whether it was empty |

`jaccard` is `|A∩B|/|A∪B|` with `J(∅,∅)=1.0`, order-invariant, case-folded on
the same key the occurrence table uses; six parametrised cases plus an
order-invariance test pin it.

The snapshot stage names are the guard rail: they say `observed_*`, and a test
asserts the string `accepted` appears nowhere in the serialised signals.

---

## 24. Why §11.3's closure is not executed before an accepted `A_t` exists

The rule is stated *"Given accepted set `A_t`"*. Acceptance is produced by M16
(consensus) and M17/M18 (verification), neither of which exists. Evaluating the
rule over M15's *observed* set would silently redefine `A_t` as "whatever the
enumerator mentioned", which is precisely the conflation the proposal's
independence discipline exists to prevent: an unverified mention is not an
accepted fact, and a stable set of unverified mentions is not a closed set.

Additionally, `τ_J` is unset (§2, interpretation 4), and "no high-risk singleton
remains" is a *residual* condition over an accepted set — a singleton in an
observed set is a candidate that has not yet been checked, not a defect.

So M15 computes every input and forms no verdict. There is no `should_stop`, no
`CLOSED`, no `FINAL_SET_COMPLETE`, no `accepted_set` — asserted against the
serialised payload and against the executable code.

---

## 25. Pending-check / action contract

`PendingCheck` is a typed **request**: kind, reason, candidate, the operation ids
and independence groups that motivated it, and the relation/subject/row it
belongs to.

| Kind | Raised by |
| --- | --- |
| `REVERSE_ADJACENCY` | border singleton; border target/near-miss conflict; border territory ambiguity |
| `PARENT_SUBSIDIARY` | parent-listing or subsidiary-listing mention |
| `INDEX_CONFUSION` | index/non-exchange mention |
| `COMPANY_ITSELF` | historical/delisted mention; private/not-listed mention; candidate explosion |

Reasons (7): `SINGLETON_CANDIDATE`, `TERRITORY_AMBIGUITY`,
`CONFLICTING_SOURCES`, `PARENT_SUBSIDIARY_RISK`, `INDEX_RISK`,
`HISTORICAL_LISTING_RISK`, `CANDIDATE_EXPLOSION`.

A test asserts **every** near-miss kind reaches some check: a taxonomy class
that routes nowhere would record a risk and tell nobody. This caught one dead
end — a `PRIVATE_OR_NOT_LISTED` mention, which contradicts the gate that let the
probe run, now raises `COMPANY_ITSELF` / `CONFLICTING_SOURCES`.

Contract: requests are deterministic, deduplicated, carry provenance, and are
executed by nobody in this milestone. No reverse prompt is rendered and no
counterfactual is constructed — a test scans for `reverse_prompt`,
`counterfactual_prompt`, `run_reverse`, `execute_check`, `key_condition` and
`reconstruct`.

---

## 26. Why M15 does not implement M16

No `accepted`, `ACCEPT`, `REJECTED`, `consensus`, `fuse_evidence`,
`candidate_score`, `final_set`, `final_verdict` or `winner` in the executable
code. M15 counts occurrences and independence groups; it forms no support
vector, no mode, no ranking, and resolves no contradiction — a conflicted gate
stays `UNRESOLVED` by policy, and `conflict_policy` refuses any other value.

## 27. Why M15 does not implement M17

No verifier is called and none is constructed: no `VerificationLabel`,
`score_labels`, `LABEL_TOKENS`, `VerifierTemplate`, `verifier_runtime` or
`build_verifier_prompt`. Every observation is `verified=False` by construction
and setting it raises. M4's entire prompt surface is pinned by sha256
`3acd7109fd22cf37b9b0c1c8a3ab63e4a4a1b65875eab02888e3fbc491e6d874` and the test
still passes.

## 28. Why M15 does not implement M18

§11.1's reverse checks and §11.2's parent/subsidiary and index contrasts are
emitted as `PendingCheck` descriptors and executed by nothing (§25). M15
prepares the provenance those checks will need and performs none of them.

## 29. Why M15 does not implement M19

No `should_stop`, `next_action`, `residual_coverage`, `expected_value`,
`missingness_estimate` or `saturation_score`. The missingness probe is §11.3's
measurement, not a coverage estimator: it runs unconditionally, once, and its
output is a list of surfaces.

## 30. Why M15 does not implement M20/M21

No `allocate_budget`, `schedule_budget` or adaptive re-planning. The border plan
is fixed at two calls; the stock plan is two, plus five when §11.2's gate
permits — a gate the proposal specifies, evaluated from gate observations alone,
with a fixed recorded rule that never consults yield, budget or cost. No
threshold in M15 was fitted to any split.

§17A's conditional cross-family branch is likewise intrinsic, not control: it is
a single proposal-defined subroutine (§20.5 step 2), fired by a fixed rule over
observations M15 already holds, capped structurally at one call, and never
re-evaluated after it runs. It computes no expected value, holds no budget, and
cannot loop.

---

## 31. Runtime and call accounting

Calls are **measured, not assumed**: `result.calls` is incremented per issued
`GenerationRequest`, and a test with a runtime that returns text without
counting asserts `calls == 0` — so the number can never drift above the truth.
`generated_tokens` and `prompt_tokens` come from the runtime's own result.

With no runtime, M15 spends nothing and produces no observations. Mining M11
costs nothing. The pipeline adds M15's calls to the shared `shadow_calls`
counter, which is subtracted once from the production progress display; a test
asserts `pipeline.shadow_calls == sum(M11 calls) + sum(M15 calls)` and that this
is ≤ the runtime's own counter, i.e. each physical call is counted exactly once.

For a border query with M11 and M15 both on: 3 (M11) + 2 (M15) = 5 shadow calls,
and the production budget snapshot is **identical** to the run without M15.

---

## 32. Shadow isolation

`mode` accepts only `shadow`; `production` raises. M15 receives the query, the
M10 program, the contract and (optionally) the M11 result, and returns a result
object the pipeline stores in a list. It never touches the evidence graph — a
test scans for `EvidenceGraph`, `build_graph`, `add_candidate` and `Evidence(`,
and separately asserts a live graph's candidate, record and edge counts are
unchanged across an analysis. A second test asserts no M15 vocabulary
(`pending_check`, `closure`, `m15_`, `listing_type`,
`publicly_listed_plausible`) appears anywhere in the graph's public state.

M2–M8 are untouched: M2's system prompt, entity format and view registry are
asserted intact, and M15 references none of the elicitation machinery.

---

## 33. Disabled-path invariance

Staged CLI runs, `smoke_staged_scripted`, four queries per relation, M9/M10/M11
and M12/M13/M14 all on, M15 the only variable:

```
[countryLandBordersCountry]        [companyTradesAtStockExchange]
  IDENTICAL  predictions.jsonl       IDENTICAL  predictions.jsonl
  IDENTICAL  diagnostics.json        IDENTICAL  diagnostics.json
  IDENTICAL  trace.jsonl             IDENTICAL  trace.jsonl
  IDENTICAL  stage_a_enumerated      IDENTICAL  stage_a_enumerated
  IDENTICAL  stage_b_verified        IDENTICAL  stage_b_verified
  IDENTICAL  query_profiles.jsonl    IDENTICAL  query_profiles.jsonl
  IDENTICAL  prompt_programs.jsonl   IDENTICAL  prompt_programs.jsonl
  IDENTICAL  parametric_memory       IDENTICAL  parametric_memory
  IDENTICAL  query_manifest.json     IDENTICAL  query_manifest.json
  IDENTICAL  metrics.json            IDENTICAL  metrics.json
  IDENTICAL  calls_enumerate.jsonl   IDENTICAL  calls_enumerate.jsonl
  IDENTICAL  calls_verify.jsonl      IDENTICAL  calls_verify.jsonl
```

Byte-for-byte, including the production call ledgers. `small_set_specialist.jsonl`
exists only when M15 is on.

`manifest_enumerate.json` differs, and only in: the echoed
`specialists.small_set_closure.enabled` flag itself, the derived `config_hash`,
`run_id`, and the two timestamps. No production content differs. The equivalent
comparison is also automated in the test suite for both relations, and repeated
for the three sibling artefacts (§34).

---

## 34. M12 / M13 / M14 preservation

The same on/off comparison over `numeric_specialist.jsonl`,
`large_open_set_specialist.jsonl` and `null_temporal_specialist.jsonl` for
`hasCapacity`, `awardWonBy` and `personHasCityOfDeath`: byte-identical, and no
`small_set_specialist.jsonl` is produced for relations M15 does not handle.

All four specialists are **independently enableable**: a test enables each of the
four config keys in turn and asserts the other three builders return `None`.

M14's own suite (135 tests), M13's (108) and M12's (137) all pass unchanged.

---

## 35. M14 §15A regression proof

Audit 0021's corrected invariant is re-asserted here, in M15's suite, so the
extraction cannot regress it:

* `is_epistemic_abstention("UNKNOWN")` is true;
* `asserts_relation_level_absence("UNKNOWN", sentinel_is_defined=…)` is false for
  both values of the sentinel flag;
* a full M14 run with Stage A = `DECEASED` ×3 and Stage B = `UNKNOWN` ×4 yields
  `no_known_locality_support == 0`, `failed_recall_only == True`,
  `has_substantive_null_evidence == False`.

**UNKNOWN remains FAILED_RECALL_ONLY.** Independent ignorance is still not
independent evidence of emptiness.

The cross-family rationales are asserted byte-for-byte against Audit 0021's
three strings (disabled / no distinct family / eligible).

---

## 36. Persistence schema

`small_set_specialist.jsonl`, one row per query, written in
`query_manifest.json` order — asserted by comparing `(SubjectEntity, Relation)`
pairs positionally. Keys: `plan`, `listing_observations`, `gate`,
`candidate_observations`, `occurrences`, `closure`, `pending_checks`,
`near_miss_mentions`, `unique_candidates`, `candidate_explosion`,
`acquisition_executed`, `cross_family_trigger`, `cross_family_triggered`,
`cross_family_executed`, `calls`, `generated_tokens`, `prompt_tokens`,
`errors`. The plan additionally carries `cross_family_eligible`,
`cross_family_rationale` and `cross_family_condition`, so eligibility and firing
are separable on disk (§17A).

Every plan row carries `specialist_version` (`m15-v1`), `compiler_version` and
`profile_version`. No gold, no `ObjectEntities`, no `accepted`, `rejected`,
`should_stop`, `prediction` or `final_verdict` — asserted per row. Every public
type round-trips `to_json`/`from_json` for both relations.

---

## 37. Error handling

A runtime that raises produces one `RUNTIME_ERROR` observation per failed probe
with an empty surface, an entry in `errors`, and **no candidate**: a total
failure yields two errors, zero occurrences, and empty `new_surfaces` — never a
closure signal manufactured from silence. One failing probe does not kill the
others: the geographic probe's candidate survives a failing missingness probe.
Configuration failures are loud — unsupported mode, unknown config key,
`min_independent_groups < 1`, unsupported `conflict_policy`, a string where a
facet list belongs, and a negative explosion threshold each raise with a message
naming the key.

`check_small_set_registry_consistency()` runs at import and on demand; a test
deletes the stock gate from a copied registry and asserts it raises.

---

## 38. Test results

```
python -m pytest -q
1783 passed, 3 skipped in 12.88s
```

M15's suite: **157 tests** (`tests/test_small_set_specialist.py`) — 139 for the
53 numbered requirements of the brief, plus 18 for the §17A correction.
Layer-2 totals: M12 137, M13 108, M14 135, **M15 157**.

(An earlier revision of this section stated 139 in one place and 137 in
another. Both are superseded by the count above, taken from collection.)

Three prior tests in `tests/test_numeric_specialist.py` were rescoped, with
reasons: the specialist-package file inventory (13 → 14 files, adding
`cross_family.py`), the sibling-independence scan (now includes `small_set`),
and the "unknown config key" example (the previous example key is now owned by
M15). No test was weakened.

Two module defects were found by these tests and fixed before this audit: the
unparseable temporal answer and the substring cue match (§16), plus the
duplicate-mention masking (§20). All three have regression tests.

---

## 39. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

---

## 40. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  Qwen/Qwen3.5-4B [verifier]                              4.660B (verified)
  mistralai/Mistral-Small-3.2-24B-Instruct-2506 [enum]   24.011B (verified)
  total: 28.67B
  RESULT: PASS
```

M15 introduces no model, no checkpoint and no parameter. A subprocess test
constructs the specialist and asserts `torch`, `transformers` and
`mistral_common` are never imported.

---

## 41. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`ScriptedRuntime` fixtures and wrote only into a scratch directory.

---

## 42. No TRAIN / VAL / TEST tuning

No threshold, cue phrase, facet or plan in M15 was chosen by looking at data.
The explosion threshold derives from the relation contract's own auto-accept
support; `min_independent_groups` defaults to 1 (the minimum "independent" can
mean); `τ_J` is deliberately unset. No VAL or TEST split was read, no metric was
computed, no leaderboard submission was made. Smoke runs used four scripted
fixture rows purely to prove artefact invariance.

---

## 43. Challenge compliance

* **Closed book.** No web, search, Wikipedia, Wikidata, KB, vector store,
  entity linker, geographic or company-registry database, or external API. An
  AST test bans `requests`, `httpx`, `urllib`, `socket`, `http`, `aiohttp`,
  `sqlite3`, `faiss`, `chromadb`, `pinecone`; a text test bans `wikipedia`,
  `wikidata`, `http://`, `https://`, `api_key`, `exchange_api`,
  `company_registry`, `entity_linker`. Both probe system prompts state the
  constraint to the model.
* **No training.** No fine-tuning, LoRA, continued pretraining, learned router,
  classifier, calibrator, verifier or scorer. Every M15 decision is a declared
  rule over declared cues.
* **Frozen model profile.** Enumerator `mistralai/Mistral-Small-3.2-24B-Instruct-2506`,
  verifier `Qwen/Qwen3.5-4B`, 28.67B published total, unchanged. No third model.
  The cross-family branch is off by default and, when on, refuses to run unless a
  genuinely distinct family is configured **and** this query's listing status is
  uncertain (§17A).
* **Reproducible.** Temperature 0.0 for every M15 probe; planning is pure;
  extraction, aggregation, closure signals and pending checks are deterministic,
  asserted by repeat-and-compare tests.

---

## 44. Non-goals — M16–M21 absent

| Module | Absent because |
| --- | --- |
| M16 Atomic Consensus | §26 — no fusion, no scoring, no acceptance |
| M17 Verifier | §27 — no verifier call; M4 prompt surface sha256-pinned |
| M18 Reverse/Counterfactual | §28 — checks are requested, never executed |
| M19 Missingness/Stopping | §29 — no `should_stop`, no coverage estimate |
| M20/M21 Control/Calibration | §30 — no budget allocation, no adaptive re-planning |

No placeholder files, no stub classes, no "future" modules were created.

---

## 45. Verdict

**PASS.**

M15 implements §11.1's minimal-change border path (two fixed calls; the direct
probe declared and disabled with a recorded reason), §11.2's gated stock path
(public-listing gate → four listing facets → missingness, with the freshness
subroutine reusing M14's audited primitive and firing only on §20.5 step 2's
"if listing status uncertain" — see §17A), and §11.3's closure **inputs** —
`N_t`, `J`, singletons, high-risk singletons — while declaring no closure,
because no accepted set exists to close. Reverse, parent/subsidiary, index and
company-itself checks are typed requests for M18 with full provenance and zero
execution.

Layer 2 is complete: M12, M13, M14, M15. All four are independently enableable,
all four are shadow-only and disabled by default, and enabling any of them
changes no production artefact by a single byte.

Not committed. Not pushed.
