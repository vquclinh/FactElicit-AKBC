# Audit 0006 — Module 3 (Atomic Normalization + Candidate–Facet Evidence Graph): Conformance Review

**Scope:** Module 3 only. Modules 4+ were inspected *solely* to determine whether
Module 3 hands them a correct graph contract. **No claim is made that Modules 4+
have been reviewed.**

**Date:** 2026-08-03

---

## 1. Objective and scope

Answer, against `COVER_KBC_V2_ARCHITECTURE_SPEC.pdf` §9:

> Does the current evidence graph faithfully represent atomic candidates and
> provenance-preserving evidence, or does it accidentally collapse semantic
> identity, aliases, repeated generations, facets or evidence mechanisms?

**Verdict: it collapsed semantic identity.** The graph keyed candidates on the
*alias hint* rather than the strict key, so a heuristic fold became irreversible
identity — and the fold was unsafe. Four further defects were found. No
wholesale refactor was needed: the node/edge separation, independence
accounting and facet handling were already correct.

---

## 2. Proposal requirements

Spec §9.1 — "All textual generations are parsed into relation-typed atomic
candidates. For string relations, surface normalization is for internal
deduplication only; the final output should retain one preferred human-readable
surface form. For numeric relations, candidates are parsed into numeric values
plus units."

Spec §9.2 — signed edges `e → o ∈ {SUPPORT, CONTRADICT, UNKNOWN}`; "Each edge
contains independence_group, model ID, view ID, run ID, raw verifier
distribution if available, and cost." Figure 3: "The graph records independent
evidence types rather than treating every generation as an equal vote."

Spec §9.3 — conservative hard rejection for type/format impossibilities;
"must not encode external factual lookup."

Spec §7.4 — "No downstream module should consume a candidate without
provenance."

---

## 3. Pre-work repository state

```
branch : main
HEAD   : 179f606  refactor: align COVER-KBC elicitation with architecture
tree   : clean
tests  : 383 passing
benchmark/ : clean
```

Modules 0, 1 and 2 decisions were preserved throughout.

---

## 4. Existing Module-3 design

- `normalization/strings.py` — `strict_key` (evaluator-identical) and
  `alias_hint_key` (strict + article folding); surface cleaning; abstain and
  refusal detection.
- `normalization/numeric.py` — number parsing, unit conversion, clustering,
  output formatting.
- `elicitation/parsing.py` — raw generation → atomic mentions.
- `evidence/graph.py` — `EvidenceGraph`: candidates dict, records dict, signed
  edge insertion, hard contract rules.
- `types.py` — `Candidate`, `Evidence`, `EvidenceGroup`, `GenerationRecord`.
- `staging.py` — graph ⇄ JSON for staged Colab execution.

The core shape was already right: candidate nodes and evidence edges are
separate, `EvidenceGroup` buckets edges by mechanism, and `raw_support_count`
is kept apart from `independent_support`.

---

## 5. Candidate-node schema

| Field | Meaning | Status |
|---|---|---|
| `key` | identity — **now the strict evaluator key** | fixed (§19.1) |
| `strict_key` | evaluator-identical normalisation | live |
| `alias_hint` | soft grouping hint, **never identity** | added (§19.1) |
| `display_value` | preferred human-readable surface | live |
| `surface_forms` | every observed surface | live |
| `relation`, `output_type` | relation typing | live |
| `numeric_value`, `unit` | normalised scalar + target unit | live |
| `raw_text`, `source_unit` | numeral as written + its unit | added (§19.2) |
| `facet_ids` | subspaces that mentioned it | live |
| `groups` | edges bucketed by independence group | live |
| `record_ids` | provenance back-references | live |
| `status`, `rejection_reason` | resolution + why | live |
| `verifications` | verifier read-outs | live |

---

## 6. String-identity / normalization analysis

Two levels, now correctly separated:

| Level | Rule | Used for |
|---|---|---|
| `strict_key` | official `normalize_string` | **candidate identity** (hard merge) |
| `alias_hint_key` | strict + English article folding | soft grouping, output dedup |

Hard merge happens only where the official scorer itself would treat two forms
as one prediction, so it can never cost a true positive. Case, punctuation and
diacritic variants merge losslessly; `Alpha-Land` / `alpha land` / `ALPHA LAND`
become one node with three preserved surfaces.

**Parenthetical qualifiers are never folded** (Module-0 decision preserved):
`Springfield (Illinois)`, `Springfield (Missouri)` and `Springfield` remain
three nodes.

No entity resolver, no fuzzy matching, no external KB. The graph does not
pretend to know the official alias relation — that stays with the evaluator.

---

## 7. Surface preservation analysis

Every observed surface is retained on the candidate, across runs and views.
Normalisation never rewrites the emitted string: `Côte d'Ivoire` is stored and
emitted with its diacritics and apostrophe intact while its key is folded.

The graph does **not** claim `Czech Republic` and `Czechia` are aliases — it has
no evidence for that, so they stay separate nodes and `alias_groups()` reports
no grouping for them. Module 8 still owns final output selection; Module 3 only
maintains deterministic surface metadata.

---

## 8. Numeric-candidate analysis

Before this review a numeric candidate stored only the *converted* value: an
observation of "2145 square miles" was indistinguishable from one of
"5556 km²" once normalised. Now all four parts required by the brief are kept:

| Part | Field | Example |
|---|---|---|
| raw text | `raw_text` | `"2145"` |
| source unit | `source_unit` | `"mi2"` |
| normalised value | `numeric_value` | `5555.52` |
| target unit | `unit` | `"km2"` |

Carried by a new `NumericObservation` from the parser through the engine and
pipeline into the graph. `add_numeric_mentions` still accepts bare floats, so
existing callers are unaffected.

`"35,000"` reaches the graph as the single candidate `35000`, not `35` and
`000`. Conversion is deterministic and covers only the units the contracts
already declare (km², m², hectares, square miles, acres) — no speculative units
were added. Cluster *selection* remains Module 8's.

---

## 9. Evidence-edge schema

| Field | Status |
|---|---|
| `edge_id` | **added** — deterministic, duplicate insertion fails loudly |
| `candidate_key`, `record_id` | live — every edge names its event |
| `edge_type` | live — SUPPORT / CONTRADICT / UNKNOWN |
| `independence_group` | live |
| `mode` | live — INDEPENDENT_RECALL vs SHOWN_CANDIDATE |
| `view_id`, `run_id` | live |
| `model_id`, `model_family` | live |
| `valid_prob` / `invalid_prob` / `unknown_prob` | live — verifier distribution |
| `token_cost` | live |

`facet_id`, `stage`, `source_record_id`, `source_candidate_key` and
`model_role` live on the `GenerationRecord` the edge points at, so they are one
dereference away rather than duplicated. Raw prompts and outputs are **not**
copied onto edges — `record_id` is a lossless trace.

**There is no anonymous evidence:** a test asserts every edge carries a
candidate key, record id, view id, model id and edge id, and that the record id
resolves inside the graph.

---

## 10. Signed-edge semantics

| Edge | Produced by | Verified |
|---|---|---|
| SUPPORT | any acquisition mention; verifier VALID | ✅ |
| CONTRADICT | **only** an explicit verifier INVALID verdict | ✅ |
| UNKNOWN | verifier UNKNOWN verdict | ✅ |

**Absence is never negative evidence.** A grep confirms `EdgeType.CONTRADICT`
is produced in exactly one place — `VerificationResult.edge_type` — and a test
asserts that a candidate named by one view and not by the next accumulates zero
contradictions. No contradiction is ever manufactured from a non-mention.

---

## 11. Provenance matrix

| Requirement (spec §7.4 / §9.2) | Where | Round-trips |
|---|---|---|
| event id | `Evidence.edge_id` | ✅ |
| candidate id | `Evidence.candidate_key` | ✅ |
| edge type | `Evidence.edge_type` | ✅ |
| independence group | `Evidence.independence_group` | ✅ |
| evidence mode | `Evidence.mode` | ✅ |
| model id / family | `Evidence.model_id` / `model_family` | ✅ |
| model role | `GenerationRecord.model_role` | ✅ |
| view id | `Evidence.view_id` | ✅ |
| facet id | `GenerationRecord.facet_id` | ✅ |
| run id | `Evidence.run_id` | ✅ |
| record id | `Evidence.record_id` | ✅ |
| source/parent record | `GenerationRecord.source_record_id` | **fixed** (§19.4) |
| source candidate | `GenerationRecord.source_candidate_key` | **fixed** (§19.4) |
| stage | `GenerationRecord.stage` | **fixed** (§19.4) |
| verifier distribution | `Evidence.*_prob`, `VerificationResult.raw/calibrated_logits` | ✅ |
| cost | `Evidence.token_cost`, record token counts | ✅ |

---

## 12. view / facet / run / independence separation

Four distinct axes, all preserved and independently tested:

- three runs of one view → 1 node, 3 edges, `raw_support_count = 3`,
  `independent_support = 1`;
- three award facets → 3 `facet_ids`, 1 `STRUCTURAL_DECOMPOSITION` group,
  `num_facets = 3`, `independent_support = 1`;
- three different views → 3 groups, `independent_support = 3`.

The accepted Module-2 decision that facets sub-partition one mechanism is
unchanged.

---

## 13. Module-2 special-event handling

**Gate.** A gate record registers for provenance and creates no candidate and
no edge. `EXISTENCE_GATE` never appears in `candidate_producing_groups()`, so it
cannot manufacture fake coverage.

**Relation-focused description.** Stage-1 prose creates no candidate
(`parsed_values == []`, no edge); only stage-2 extraction produces mentions.
Both stages share one `view_id` and one `RELATION_FOCUSED_DESCRIPTION` group, so
two calls count as **one** independent support. The extraction edge's record
carries `source_record_id` pointing at the description, and a test walks
edge → extraction record → description record.

**Reverse / alternate.** Attaches to the existing candidate under
`REVERSE_ALTERNATE` — not `DIRECT_RECALL`, not `BLIND_VERIFIER`. If a reverse
generation names a genuinely new object it flows through the ordinary atomic
path rather than a special case.

**Cross-model recall.** `CROSS_MODEL_RECALL` + `INDEPENDENT_RECALL` stays
distinguishable from `BLIND_VERIFIER` + `SHOWN_CANDIDATE` on the same candidate;
a test reads both modes off one node.

---

## 14. Hard-contract-rule inventory

| Rule | Class | Verdict |
|---|---|---|
| non-numeric candidate in a numeric relation | TYPE | allowed |
| numeric value ≤ 0 | FORMAT | allowed |
| entity candidate containing no letters | FORMAT | allowed |
| abstention token (`NONE`, `UNKNOWN`, empty) | FORMAT | allowed — **added** (§19.3) |

No FACTUAL rules exist. An AST test asserts `apply_hard_contract_rules`
contains no literal collection of names to test membership against, and no
proper nouns. Every rejection carries an explicit `rejection_reason` — nothing
is silently dropped — and rejection preserves the candidate's evidence.

---

## 15. Duplicate / alias handling

| Level | Behaviour |
|---|---|
| A. raw repeated mention (same view, later run) | one node, one edge per run, `raw_support_count` increments |
| A′. repeated mention *inside* one generation | one node, **one** edge — listing a name twice is not corroboration |
| B. strict-normalised duplicate | one node, every surface preserved |
| C. alias-like but uncertain | **separate nodes**, grouped softly by `alias_groups()` |

Deduplication never deletes evidence: a merged candidate keeps every edge, and
a rejected candidate keeps its edges too.

---

## 16. Graph mutation invariants

| Invariant | Enforced |
|---|---|
| every edge references an existing candidate | ✅ tested |
| every candidate has ≥1 provenance source resolving in `records` | ✅ tested |
| candidate relation/output type matches the graph's | ✅ tested |
| entity candidate cannot survive a numeric graph | ✅ hard rule + test |
| duplicate edge ids fail loudly | ✅ `_attach` raises |
| verifier evidence cannot masquerade as independent recall | ✅ mode is set at insertion |
| gate/context records cannot masquerade as candidate support | ✅ tested |
| a verifier label can never become a candidate | ✅ `add_verification` returns `None` for an unknown key |

The structure remains a plain deterministic in-memory object; no graph database
was introduced.

---

## 17. Serialization / staged round-trip

Stage file version bumped **2 → 3** (the schema gained fields).

Round-trip is now semantically lossless: candidates (including `alias_hint`,
`raw_text`, `source_unit`, `strict_key`, surfaces, facets, status, rejection
reason), records (including `stage`, `source_record_id`,
`source_candidate_key`), all edges with stable ids, and full verification
fields (`raw_logits`, `calibrated_logits`, `calibrated`, `prompt_disagreement`,
`model_family`).

A reloaded graph re-registers its edge ids, so duplicate insertion still fails
after a staged reload — tested.

---

## 18. Deterministic-ID analysis

| Id | Construction | Stable across processes |
|---|---|---|
| candidate key | strict normalisation of the surface | ✅ pure string transform |
| `record_id` | SHA-256 of `subject|relation|view|run|stage` | ✅ |
| `edge_id` | SHA-256 of `candidate|record|type|group|run|view` | ✅ |

No Python `hash()` and no UUIDs anywhere on the identity path, so nothing
depends on `PYTHONHASHSEED`. Candidate identity survives reload unchanged —
tested by comparing key lists before and after.

---

## 19. Mismatches found

### 19.1 The alias hint was the hard identity key — **severe**

`EvidenceGraph._candidate_key` used `contract.key()`, i.e. `alias_hint_key`.
A heuristic fold therefore became irreversible identity. Worse, the fold itself
was unsafe: `LEADING_ARTICLES` contained romance and Germanic articles that are
routinely part of a proper name.

Measured collisions:

| Merged | Reality |
|---|---|
| `Le Havre` → `havre` | collides with **Havre, Montana** |
| `Los Angeles` → `angeles` | collides with **Angeles, Philippines** |
| `La Paz` → `paz`, `El Paso` → `paso`, `Las Vegas` → `vegas` | same class |

For `personHasCityOfDeath` this is a direct wrong-answer path.

### 19.2 Numeric candidates lost their source provenance

Only the converted value survived; the numeral as written and its unit were
discarded, so a unit conversion could not be audited after the fact.

### 19.3 Abstention tokens could become candidates

`add_entity_mentions(["NONE", "", "UNKNOWN"])` created candidates `none` and
`unknown`. The parser filters these, but the graph relied on that rather than
guarding itself — so any direct insertion (reverse output, a future module, a
test fixture) could inject a fake object.

### 19.4 Staged persistence dropped three provenance fields

`stage`, `source_record_id` and `source_candidate_key` were written but never
read back. In staged Colab execution — Phase A writes, Phase C reads — the
**description → extraction chain was severed**, exactly the link Module 2's
trust boundary depends on.

### 19.5 Evidence edges had no identity

No `edge_id`, so duplicate insertion was undetectable and edges could not be
referenced individually.

### 19.6 Not defects (verified correct)

Node/edge separation; repeated-run accounting; facet vs independence; gates
creating no candidates; verifier unable to create candidates; type mismatch
hard-rejected with a reason; absence never becoming CONTRADICT; no scoring
logic in Module 3 (tested by source inspection).

---

## 20. Fixes made

| # | Fix | Follows |
|---|---|---|
| 1 | Graph keys on `strict_key`; `alias_hint` recorded as soft metadata; `alias_groups()` exposes the grouping | brief §4/§5, spec §9.1 |
| 2 | `LEADING_ARTICLES` restricted to English `the`/`a`/`an` | removes the Havre/Angeles class of collision |
| 3 | `NumericObservation` carries raw text + source unit from parser → engine → pipeline → graph | brief §7 |
| 4 | `is_abstain` guard in `add_entity_mentions` | brief §22 |
| 5 | `Evidence.edge_id`, deterministic; `_attach` refuses duplicates | brief §9/§16 |
| 6 | `staging` round-trips `stage`, `source_record_id`, `source_candidate_key`, `edge_id`, `alias_hint`, `raw_text`, `source_unit`; version → 3 | brief §17 |
| 7 | `executed_independence_groups()` / `candidate_producing_groups()` | brief §19 — what Module 5 will need |

Output precision is unaffected by fix 1: the writer still dedupes on the alias
hint, so two article variants still yield exactly one submitted surface form.

**Deliberately not done:** no entity resolver, no fuzzy matching, no external
KB, no change to `F(o)`/`q(o)`/`S(o)` or any threshold, no Module 4/5/7 logic.

---

## 21. Files created / modified

**Created (2)**
- `tests/test_graph.py` (59 tests)
- `docs/audits/0006-module-3-evidence-graph-conformance.md`

**Modified (7)**
- `src/cover_kbc/evidence/graph.py` — strict identity, abstain guard, numeric
  provenance, edge ids, alias/Module-5 accessors
- `src/cover_kbc/types.py` — `alias_hint`, `raw_text`, `source_unit`,
  `Evidence.edge_id` + `derive_edge_id()`
- `src/cover_kbc/normalization/strings.py` — English-only article list
- `src/cover_kbc/elicitation/parsing.py` — `NumericObservation`,
  `parse_numeric_observations`
- `src/cover_kbc/elicitation/engine.py` — observations on `ViewOutcome`
- `src/cover_kbc/pipeline.py` — observations passed to the graph
- `src/cover_kbc/staging.py` — full provenance round-trip, version 3
- `tests/test_evidence.py` — identity expectations updated

**`benchmark/` — untouched.**

---

## 22. Commands executed

```bash
git status / branch --show-current / log --oneline -10 / diff --stat
pdftotext -layout COVER_KBC_V2_ARCHITECTURE_SPEC.pdf     # §4, §7, §9, §26, §31
grep -n "_candidate_key|contract.key|strict_key" src/cover_kbc/evidence/graph.py
python -c "...alias_hint_key vs strict_key on Le Havre / Los Angeles..."   # collision probe
python -c "...add_entity_mentions(['NONE','','UNKNOWN'])..."               # abstain probe
python -c "...graph_from_json(graph_to_json(g))..."                        # round-trip probe
grep -rn "EdgeType.CONTRADICT" src/                                        # absence-vs-contradiction
python -m pytest -q
python -m pyflakes src/ tests/ scripts/
python scripts/run_staged.py all --config configs/experiments/smoke_staged_scripted.yaml --limit 6
git status --porcelain benchmark/ ; git diff -- benchmark/ ; git diff --cached -- benchmark/
```

---

## 23. Test results

**443 passed**, 0 failed (383 before this review; +59 new Module-3 tests, +1
split from an updated identity test). `pyflakes` clean. The staged scripted
smoke run completes all three phases. No test loads a heavyweight model.

One existing test failed during the fix and was a genuine behaviour change, not
a test bug: `test_alias_like_surface_forms_collapse_to_one_candidate` asserted
that article variants merge into one node. It is replaced by
`test_article_variants_stay_separate_nodes_but_group_softly` (strict identity,
soft grouping, one emitted surface) and
`test_exact_duplicate_surfaces_do_merge`.

Brief §24 coverage — all 24 required checks have a named test in
`tests/test_graph.py`, including: multiple objects → multiple atomic nodes;
gate/context producing no node; description→extraction provenance link; repeated
runs staying one independence group; facets surviving while sharing a group;
parentheticals not merging; alias hint never becoming identity; `"35,000"`
parsing; deterministic unit conversion; entity/numeric type mismatch failing
closed; all three edge types round-tripping; absence not becoming CONTRADICT;
hard rules carrying reasons and no lookup; cross-model vs verifier modes;
reverse attaching to the intended candidate; verifier labels never becoming
candidates; refusals creating no fake candidate; every edge traceable; stable
ids across serialization; lossless staged round-trip; dedup preserving evidence;
and the graph exposing executed vs candidate-producing groups for Module 5.

---

## 24. Benchmark integrity

```
git status --porcelain benchmark/   -> (empty)
git diff -- benchmark/              -> (empty)
git diff --cached -- benchmark/     -> (empty)
```

---

## 25. Challenge-compliance impact

None adverse, one improvement. Module 3 is deterministic non-neural bookkeeping
and adds no inference-time parameters. The frozen pairing is unchanged
(Mistral-Small-24B + Qwen3.5-4B = 28,671,226,368 < 32B).

The identity fix **reduces** rules risk: the graph no longer asserts an alias
relation it cannot justify, and it contains no entity resolver, no fuzzy
matcher and no external KB. No model was downloaded, loaded or run.

---

## 26. Unresolved Module-3-only issues

**26.1 Article folding remains a heuristic, now soft.** English `the`/`a`/`an`
folding is applied to the *hint* only, and the writer still uses the hint to
avoid submitting two surface forms. If two genuinely different entities differed
only by a leading English article, the writer would drop one. No such case is
known in these six relations, and the graph itself keeps both nodes.

**26.2 Numeric candidates keep the first observation's source provenance.**
When the same normalised value arrives from two units, `raw_text` /
`source_unit` record the first. All observations remain individually traceable
through their edges and records, but the candidate summarises one.

**26.3 Numeric identity is the formatted value, not a tolerance band.**
`5000` and `5001` are separate nodes; the 5% tolerance is applied by Module 8's
clustering. Correct separation of concerns, but it means near-duplicate scalars
do not accumulate support on one node.

**26.4 `alias_groups()` has no consumer inside Module 3.** It is exposed for
Module 8 (output dedup) and Module 5, and is currently used only by tests and
the graph's JSON summary. Recorded so it is not mistaken for dead metadata:
its consumer is by design outside this module.

---

## 27. Future-review notes (NOT fixed here)

**27.1 Module 5 — `m(o)` uses declared, not executed, mechanisms.** Carried
unchanged from audit 0005 §22.5. Module 3 now exposes
`executed_independence_groups()` and `candidate_producing_groups()`, so Module 5
has what it needs to compute a per-run denominator when that review happens.

**27.2 Module 4 — `auto_accept_independent_support` for stock/death.** Carried
unchanged from audit 0005 §22.1 (partly relieved by the Module-2 correction).

**27.3 Module 7 — no candidate-conditioned action** for reverse framing
(audit 0005 §22.4); **RUN_VIEW mandatory bonus** keyed on action type
(audit 0004 §17.1); **RESAMPLE never enumerated** (audit 0004 §17.2).

**27.4 Module 8 — surface selection interacts with soft alias groups.** With
article variants now separate nodes, the selector may see two nodes for one
entity; the writer collapses them at output. Whether the *selector* should
consult `alias_groups()` before ranking is a Module 8 question.

---

## 28. Modules 4+ remain unreviewed

Modules 4 (Blind Verifier), 5 (Evidence/Uncertainty State), 6 (RCSE),
7 (Active Controller) and 8 (Final Selector) have **not** been reviewed. They
were inspected only far enough to confirm Module 3 hands them a correct graph.

---

## 29. Recommended next review

**Module 4 — Logit-Calibrated Blind Verifier**, pending external authorisation.
