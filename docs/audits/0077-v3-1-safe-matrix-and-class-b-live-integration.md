# Audit 0077 - V3.1 Safe Submission Matrix And Class-B Live Integration

## Scope

Two independent deliverables:

1. a **SAFE submission matrix** - two independently runnable, TEST-ready V3.1
   variants that separate the monotone finalization fixes from the one that
   trades rows;
2. **Class-B live integration** - the four relation instructions audit 0076
   declared but never wired, bound to the prompts the models are actually sent,
   plus the targeted TRAIN diagnostics needed to decide whether any of them
   deserves a full recollection.

Base SHA:

`107581c25cc3fff5cd1ea49c3ec78bc7d9eaa32b`

Frozen calibrated V3 TEST submission source, unchanged and not rewritten:

`16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5`

No TEST gold, web data, RAG, external KB or external factual corpus was used. No
model was fine-tuned. No subject-specific answer mapping was added. No V2 or
Audit-0073 V3 calibration artifact was modified. No immutable artifact under
`outputs/` was mutated; the replay reads persisted state and writes only to new
directories.

## 1. SAFE Submission Matrix

### Why the matrix exists

Audit 0076 shipped one safe config bundling five features. Four of them are
monotone on TRAIN - `0` harmed rows. The fifth, `stock_support_dominance`, is a
measured trade. Bundling them spends a TEST submission on a question that could
have been answered by spending two.

### Configs

| Config | Features | TRAIN macro-F1 | Readiness |
|---|---|---:|---|
| `cover_kbc_v3_test.yaml` (frozen V3) | none | 0.40333 | `READY` |
| `cover_kbc_v3_1_safe_core_test.yaml` | 4 monotone | **0.41853** | `READY` |
| `cover_kbc_v3_1_safe_full_test.yaml` | 5 (core + dominance) | **0.42000** | `READY` |
| `cover_kbc_v3_1_safe_test.yaml` (0076, kept) | 5, identical to SAFE_FULL | 0.42000 | `READY` |
| `cover_kbc_v3_1_aggressive_test.yaml` | Class-B | n/a | **`NOT_READY`** |

SAFE_CORE enables `final_candidate_retention`, `enumeration_label_repair`,
`numeric_output_canonicalization`, `stock_structural_validation`.
SAFE_FULL adds `stock_support_dominance`.

**Backward compatibility.** `cover_kbc_v3_1_safe_test.yaml` is untouched, so
audit 0076 and its tests still refer to a config that exists and means what it
meant. `test_safe_full_and_the_backward_compatible_config_cannot_drift` asserts
its `selection.v3_1` block stays identical to SAFE_FULL's, so the duplicate
cannot silently diverge.

### Exact config differences

SAFE_CORE vs SAFE_FULL differ in exactly two top-level keys, `experiment` (the
run name) and `pipeline`; within `pipeline`, in exactly one line:

```yaml
        stock_support_dominance: false   # SAFE_CORE
        stock_support_dominance: true    # SAFE_FULL
```

Asserted by `test_safe_core_and_full_differ_only_by_stock_dominance`. Both reuse
the Audit-0073 `relation_budget_scheduler`, `micro_planner`,
`calibration_provenance` and `test_dataset` blocks verbatim
(`test_safe_variants_reuse_the_audit_0073_calibration`), declare no
`train_collection`, run `v3_core.mode: production`, and enable no Class-B
feature.

## 2. TRAIN Replay Of The Matrix

`scripts/replay_v3_1_finalization.py` over the persisted 477-row TRAIN run.
Fidelity gate first: with every V3.1 feature off the replay reproduces the
committed `predictions.jsonl` on **477/477 rows**, and the script exits non-zero
if it does not.

| Variant | macro-F1 | Δ | micro-F1 | Δ | improved | harmed | unchanged | border rows changed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V3_BASELINE | 0.40333 | +0.00000 | 0.51799 | +0.00000 | 0 | 0 | 477 | **0** |
| SAFE_CORE | **0.41853** | +0.01520 | 0.53065 | +0.01265 | 15 | **0** | 462 | **0** |
| SAFE_FULL | **0.42000** | +0.01667 | 0.53079 | +0.01279 | 27 | 7 | 443 | **0** |

These reproduce the audit-0076 figures exactly.

### Per-relation

| Relation | V3_BASELINE | SAFE_CORE | SAFE_FULL |
|---|---:|---:|---:|
| awardWonBy | 0.38850 | 0.41375 | 0.41375 |
| companyTradesAtStockExchange | 0.52890 | 0.52890 | **0.53590** |
| countryLandBordersCountry | 0.96437 | **0.96437** | **0.96437** |
| hasArea | 0.24000 | 0.31000 | 0.31000 |
| hasCapacity | 0.08000 | 0.08000 | 0.08000 |
| personHasCityOfDeath | 0.39000 | 0.39000 | 0.39000 |

### Border non-regression

`countryLandBordersCountry` predictions are **byte-identical in every variant**:
`0` rows changed, macro-F1 `0.964369` unchanged. Verdict
`IDENTICAL_PREDICTIONS`. The replay exits non-zero on any border regression.

### Exact stock-dominance difference

`stock_support_dominance` changes **22** rows between SAFE_CORE and SAFE_FULL:
**12 improved, 7 harmed, 3 neutral**. Full ledger in
`outputs/v3_1_score_recovery/stock_dominance_delta.csv`. The seven harmed rows:

| Row | Subject | SAFE_CORE → SAFE_FULL | F1 |
|---:|---|---|---|
| 213 | West Japan Railway Company | [Tokyo, Fukuoka] → [Tokyo] | 0.800 → 0.500 |
| 214 | Chubu Electric Power | [Tokyo, Nagoya] → [Tokyo] | 1.000 → 0.667 |
| 246 | Enbridge | [Toronto, NYSE] → [Toronto] | 1.000 → 0.667 |
| 276 | Nautilus, Inc. | [NYSE American, NYSE] → [NYSE American] | 0.667 → **0.000** |
| 293 | Magna International | [Toronto, NYSE] → [Toronto] | 1.000 → 0.667 |
| 301 | Tokmanni | [Nasdaq Nordic, Helsinki] → [Nasdaq Nordic] | 0.667 → **0.000** |
| 302 | GD Power Development | [Shenzhen, Shanghai] → [Shenzhen] | 0.667 → **0.000** |

Net `+0.00147` overall macro-F1, `+0.00700` on stock. The upside is real and so
is the variance: three rows go to zero because the dropped listing was the gold
one. That is the whole reason for the matrix.

## 3. Class-B Integration Gap - Proven From Source

Audit 0076 stated the four instructions were not wired. Verified, and the
mechanism is precise:

* `src/cover_kbc/v3_1/prompts.py` defines four `RelationInstruction` values and
  exports `instruction_for` and `prompt_inventory`;
* before this milestone, **no module under `src/cover_kbc/` outside
  `v3_1/prompts.py` referenced any of them**. `V31AggressiveConfig` was read by
  `readiness` (to refuse TEST) and by nothing else;
* consequently `V31AggressiveConfig(capacity_definition_prompt=True)` changed
  readiness state and changed no prompt byte.

### The live call graph, traced

**Enumerator / factual recall.** Both acquisition paths converge on one call:

```
library.py static ViewSpec              v3_core.execution._recall_template
(contract.mandatory_views)              -> execution._view_spec (dynamic ViewSpec)
                     \                        /
                      ElicitationEngine.run_view       [src/cover_kbc/elicitation/engine.py]
                        view.render(...)          -> GenerationRequest.prompt
                        engine.system_prompt_for(view) -> GenerationRequest.system_prompt
                                    |
                              LMRuntime.generate
```

`run_view` is the **single choke point for every enumerator prompt**, including
the V3 actions: `SET_EXPANSION` (awards), the stock listing recall templates and
the capacity recall templates all build a dynamic `ViewSpec` in
`execution._view_spec` and execute it through `run_view`.

**Verifier - two distinct surfaces, not interchangeable:**

```
M17 blind specialist:  specialist_contracts.<FAMILY>_CONTRACT.boundary
                       -> specialist_prompts.specialist_template
                       -> render_specialist_prompt

V3 actions:            v3_core.execution._verification_request
                       -> verification.v3_modes.render_v3_verification_prompt
```

M17 runs `mode: shadow` in the production configs, but its verdicts are bridged
into the graph through `graph.add_verification` and its results reach
`build_hypothesis_graph` as `verification_evidence`, so it **does** influence
M21 state. Binding only one surface would leave half the city verification calls
un-instructed.

## 4. Class-B Live Wiring

### Bindings

| Relation | Feature | Owner | Call site | Effect |
|---|---|---|---|---|
| hasCapacity | `capacity_definition_prompt` | enumerator | `ElicitationEngine.run_view` → `GenerationRequest.system_prompt` | system prompt 211 → 1933 chars |
| companyTradesAtStockExchange | `stock_listing_entity_prompt` | enumerator | same | 211 → 1122 chars |
| awardWonBy | `award_expansion_and_fp_cap` | enumerator | same (incl. `SET_EXPANSION`) | 211 → 1060 chars |
| personHasCityOfDeath | `city_of_death_contrast_prompt` | verifier | `render_v3_verification_prompt` **and** `specialist_contract_with_boundary` | boundary replaced |
| countryLandBordersCountry | — | — | **no binding** | byte-identical |
| hasArea | — | — | no *prompt* binding | byte-identical |

### Why the system prompt rather than the template

The instruction is appended to the **system** prompt, never substituted for it,
and the view template is untouched. That keeps view identity, independence
groups, facet accounting and the format block - which is what the parser depends
on - exactly as they are, while still reaching every acquisition call for the
relation. `system_prompt_for` is public so a test can assert on what the model
receives rather than on an intermediate.

### Why the verifier binding respects the blindness invariant

`specialist_prompts` permits a prompt to carry the subject, Module 0's
definition, the target, the labels, and "§13's own question frame and
hard-negative *class* boundary, which are contract text and not observations
about this candidate". The city instruction is exactly a hard-negative class
boundary: it names confusable attribute classes and says nothing about the
candidate in front of the verifier. `specialist_contract_with_boundary` may
replace `boundary` and **nothing else** - asserted by
`test_m17_override_replaces_only_the_boundary`.

### One defect found and fixed during wiring

The first implementation emitted the M17 register (`Answer B if ...`) into the
V3 frames, whose labels are `VALID` / `INVALID` / `UNKNOWN`. That instructs the
model to produce a token the frame never offers. The boundary is now a template
with a `{reject}` placeholder filled per call site: `B` for M17, `INVALID` for
V3. `test_boundary_uses_the_label_vocabulary_of_the_frame_it_is_rendered_into`
pins both.

### A second regression found and fixed

Adding `relation_boundary` to `V3VerificationRequest.request_id` changed every
V3 verification edge id even when the boundary was empty - two existing tests
caught it. The field is now appended to the hash **only when non-empty**, so a
run with Class-B off reuses the exact V3 edge identities
(`test_v3_request_identity_is_unchanged_when_no_boundary_is_supplied`) while two
requests differing only by boundary still differ
(`test_v3_request_identity_changes_when_a_boundary_is_supplied`).

## 5. Prompt Hash / Version Provenance

`LIVE_PROMPT_VERSION = "v3.1-live-prompts-v1"`.

| Relation | Owner | Feature | Old live version | New live version | Body SHA256 |
|---|---|---|---|---|---|
| hasCapacity | enumerator | `capacity_definition_prompt` | none (unbound) | `v3.1-live-prompts-v1` | `27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36` |
| companyTradesAtStockExchange | enumerator | `stock_listing_entity_prompt` | none | `v3.1-live-prompts-v1` | `39af1c5be480c85350535f75ddd2466b497d1b124bb4e3b97c63b21bda1c39f8` |
| awardWonBy | enumerator | `award_expansion_and_fp_cap` | none | `v3.1-live-prompts-v1` | `0e9d2a920396ba2c485729f0fda25de601dbe25e712c9986310db49f2e9056dc` |
| personHasCityOfDeath | verifier (M17) | `city_of_death_contrast_prompt` | `m17-contract-v1` | `m17-contract-v1+v3.1-boundary` | `4090605c27371ea9a30248f9d794729d…` |
| personHasCityOfDeath | verifier (V3) | `city_of_death_contrast_prompt` | none | `v3.1-live-prompts-v1` | `8ee68476603ad95caa7e16f67201bf00…` |

"Old live version: none" is the honest entry - these are *additive standing
instructions* on relations that previously had none, so there is no prior body
to hash. The two city hashes differ because the reject token differs by frame;
both are recorded by `live_prompt_inventory()`.

Unchanged versions: `COMPILER_VERSION = "m10-v1"` (no `prompt_registry` edit),
`SPECIALIST_CONTRACT_VERSION = "m17-contract-v1"` for every relation without an
override.

### Making "the prompt changed" checkable

`GenerationRecord.system_prompt_hash` was added. Without it, an instruction
appended to the system prompt would leave **no trace on the record**, because
`prompt_hash` covers only the user turn. Now:

- `test_system_prompt_hash_differs_when_the_instruction_is_live` asserts the
  hash moves when a feature is on, and that `prompt_hash` does not;
- `test_border_system_prompt_hash_is_unchanged` asserts borders never move.

## 6. Relation Isolation

`test_no_cross_relation_prompt_leakage` runs over all six relations and asserts
enumerator and verifier changes occur on exactly the bound relations and nowhere
else. Additionally:

- `test_border_enumerator_prompt_is_byte_identical` - system prompt **and** user
  prompt identical with all Class-B features on;
- `test_border_verifier_prompt_is_byte_identical`;
- `test_no_border_binding_exists_in_source` - `countryLandBordersCountry` is in
  `FROZEN_RELATIONS` and absent from both binding tables, so adding one later
  fails a test rather than changing border prompts;
- `test_area_prompts_are_untouched_by_class_b` - hasArea has a Class-B *parser*
  feature but no prompt binding.

## 7. Aggressive Readiness Gate - Not Weakened

`cover_kbc_v3_1_aggressive_test.yaml` remains:

```
V3 TEST PRODUCTION READINESS: NOT_READY
selection.v3_1: CALIBRATION_REVIEW_REQUIRED - calibration-shifting feature(s)
['capacity_definition_prompt', 'city_of_death_contrast_prompt',
 'stock_listing_entity_prompt', 'award_expansion_and_fp_cap'] are enabled...
```

The gate got *stronger*: `scientific_notation_acquisition` was added to
`V31AggressiveConfig` with its own `InterventionRecord`, so it is refused by the
same rule. `V31SafeConfig` has no such field and
`test_safe_config_cannot_enable_the_acquisition_parser_change` asserts spelling
it inside a safe block raises.

## 8. Targeted TRAIN Diagnostics

Five configs, each enabling **exactly one** Class-B feature on **one** relation.

| Config | Relation | Feature | Rows |
|---|---|---|---:|
| `v3_1_diag_capacity.yaml` | hasCapacity | `capacity_definition_prompt` | 100 |
| `v3_1_diag_city.yaml` | personHasCityOfDeath | `city_of_death_contrast_prompt` | 100 |
| `v3_1_diag_stock.yaml` | companyTradesAtStockExchange | `stock_listing_entity_prompt` | 100 |
| `v3_1_diag_award.yaml` | awardWonBy | `award_expansion_and_fp_cap` | 10 |
| `v3_1_diag_area_parser.yaml` | hasArea | `scientific_notation_acquisition` | 100 |

Every one: `split: train`, **no `test_dataset` block at all**, `diagnostic: true`,
`NOT_READY`, `CALIBRATION_REVIEW_REQUIRED`. Row counts verified against
`benchmark/data/train.jsonl`.

### Relation filter

`scripts/run_cover.py` gained `--relation` (repeatable) and
`experiment.relation_filter`. It is a membership test on the query's own
relation name, applied before `--limit`, order-preserving, fails closed on an
unknown relation, and **refuses blind splits outright** - a partial submission is
not a submission. `test_relation_filter_reads_no_gold` walks the resolver's AST
and asserts it names no gold state, so a filtered run cannot be a disguised
lookup.

## 9. Capacity Numeric-Candidate Instrumentation

`scripts/evaluate_v3_1_diagnostic.py` emits `candidate_instrumentation.csv` with,
per candidate: raw text, parsed numeric value, unit, qualifier (facet ids),
source views, acquisition groups, support count, verifier label and probability,
hypothesis status, score, emitted flag, `gold_like`, and ratio bucket.

**Instrumentation, not heuristics.** No numeric target range is hard-coded.
`gold_like` is whatever the *official evaluator* would count for the relation's
type - 5% tolerance for numbers, its own normalised alias match for entities -
and it is a label applied offline, never a production predicate.

The four questions of §12 have distinct signatures in this table:

| Hypothesis | Signature |
|---|---|
| A. recalls the canonical value more often | `gold_like_candidate_rows` rises |
| B. merely creates more alternatives | `mean_candidates_per_row` rises, A does not |
| C. moves values toward correct scale | 10x/100x ratio buckets shrink |
| D. increases FP ambiguity | candidates rise, emitted precision falls |

Verified against the authoritative run (self-comparison, zero delta):

| Relation | macro-F1 | empty | candidates/row | gold-like rows | gold-like not emitted |
|---|---:|---:|---:|---:|---:|
| hasCapacity | 0.08000 | 13 | 2.01 | 8 | 0 |
| personHasCityOfDeath | 0.39000 | 78 | 1.32 | 7 | 1 |
| companyTradesAtStockExchange | 0.52890 | 39 | 14.89 | 42 | 0 |
| awardWonBy | 0.38850 | 0 | 62.30 | 9 | 0 |
| hasArea | 0.24000 | 65 | 2.67 | 39 | 15 |

The evaluator may read TRAIN gold and lives in `scripts/`;
`test_the_diagnostic_evaluator_is_not_importable_from_production` asserts no
module under `src/cover_kbc/` references it.

## 10. Acquisition Parser Class-B Fix

`src/cover_kbc/v3_1/acquisition_parser.py`. The rewrite expands a
scientific-notation numeral to plain decimal **before** the shared parser runs,
so unit reading, magnitude words, area conversion and person-count type rules all
stay exactly as they are and simply receive a numeral they can read.

| Input | Flag OFF (production today) | Flag ON |
|---|---|---|
| `7.5e4 m2` | 7.5, unit guessed km2 | 0.075 km2 (= 75000 m2) |
| `7.5E4 m²` | 7.5, unit guessed km2 | 0.075 km2 |
| `1.2e3 km2` | 1.2 km2 | 1200 km2 |
| `7.5e-2 km2` | 7.5 km2 | 0.075 km2 |

Ordinary numerals (`75000 m2`, `1,234 km2`, `2145 sq mi`, `500`, `12.5 hectares`,
`1.234.567 km2`) are **identical** under both paths, and
`expand_scientific_notation` returns them unchanged - unchanged by construction,
since the rewrite only matches exponent forms.

It is a correctness fix *and* Class B: correcting it changes candidate values,
hence clustering, hence acceptance, hence the action-effect distribution.
`CALIBRATION_REVIEW_REQUIRED`.

## 11. Full Recollection Plan - Prepared, Not Run

`configs/experiments/cover_kbc_v3_1_train_collection.yaml`.

- `split: train`, `prepared_not_run: true`, no `test_dataset`;
- **every aggressive flag is `false`** - which feature to promote is the
  diagnostics' answer, not this file's;
- new collection policy `collect-v3-1-class-b` and new output namespace
  `outputs/v3_1_train_collect_class_b`, with `overwrite_protected_namespaces`
  naming the authoritative corpora;
- writes to `configs/calibration/v3_1/`; **no output path points into
  `configs/calibration/v3/`**, so it cannot overwrite what the frozen submission
  depends on. The Audit-0073 M20 appears only under a separate read-only
  `v3_1_calibration_inputs` block.

### M20 is not re-derived blindly

`m20_policy: undecided`. Audit 0073 inherited a frozen M20 because the
action-effect observations were not a complete relation-spend sample; changing
prompts does not by itself make that sample complete, and does not by itself
justify re-estimating a budget scheduler. The decision belongs to the new
collection's coverage. `test_m20_is_not_assumed_to_be_re_derived` pins it.

Order, no step skippable: promote → collect → coverage check → re-derive M21 →
decide M20 on evidence → re-validate readiness → only then may a TEST profile
claim `FULL_TEST_READY`.

## 12. Promotion Gates

`src/cover_kbc/v3_1/promotion.py`, written **before** any diagnostic runs. No
gate hard-codes an expected gain; each states the shape of a justifying result
plus what would make an apparent pass spurious.

| Feature | Baseline | Promote if | Reject the pass if |
|---|---:|---|---|
| `capacity_definition_prompt` | 0.080 | macro-F1 rises materially, **or** gold-like candidate rows rise without precision collapsing | candidates/row rises while gold-like rows do not; or 10x/100x buckets grow |
| `city_of_death_contrast_prompt` | 0.390 | exact-city recall rises, or FP falls materially with recall held | empty rows rise sharply (precision bought by abstaining) |
| `stock_listing_entity_prompt` | 0.529 | FP down by more than FN up, macro-F1 ≥ 0.529 | mean predictions collapse toward 1 (multi-listing is real) |
| `award_expansion_and_fp_cap` | 0.389 | TP up with FP flat or down | any 10-row result treated as weak; require consistency across rows |
| `scientific_notation_acquisition` | 0.240 | it fires and moves values toward gold scale | any ordinary numeral changes |

The capacity gate's second clause is the load-bearing one: 92/100 capacity rows
never recall the answer, so surfacing the canonical figure as a *candidate* is
the hard part even if selection has not caught up.

## 13. Immutable Calibration Hashes

Verified unchanged, and pinned by `test_calibration_artifacts_are_byte_identical`:

| Artifact | SHA256 |
|---|---|
| `configs/calibration/v3/m20_relation_budget.json` | `74414b0f496bd0d194bf4537cbb2745918e23cab68fc8c5d0059f075d4f40e29` |
| `configs/calibration/v3/m21_historical_bins.json` | `ca907ea82b6e42819cafe27244944e7d4140814490a5ed1de897bdec7a61d675` |
| `configs/calibration/v3/m21_planner_calibration.json` | `1423df17137c2c175bd2fe0f22a287acf858c59f612664dcfaaa41dbe561c1e6` |
| `configs/calibration/m20_relation_budget.json` | `8110fccb4c3e85a942f5fc89a50f680bea72e8b6d1e83b1fa2c47d670ec15c68` |
| `configs/calibration/m21_historical_bins.json` | `d6d19493b0b82299e5c73bd0f37e2b3758c4a80894b6b1bdf9fa57139fbcd071` |
| `configs/calibration/m21_planner_calibration.json` | `36315cd72a2c31bcbc61bb1ada9f2e74d8980baa575f6221d92bf8b144f9ce05` |
| `configs/calibration/v3/calibration_provenance.json` | `f9619c5bcfae1d553f5dbca05aa3e33a3694a448ee22aa14890975e6560b317d` |

`git status configs/calibration/` reports nothing modified. Each config's own
`calibration_provenance` block is additionally byte-identical across V3 and both
SAFE variants (`test_safe_variants_reuse_the_audit_0073_calibration`).

## 14. Tests

New file `tests/test_v3_1_live_integration.py`: **97 tests** covering the SAFE
matrix feature sets and readiness; aggressive `NOT_READY`; Class-B prompts absent
when disabled; capacity/stock/award instructions present in the **real rendered
enumerator request**; award instruction reaching a `SET_EXPANSION` view; city
boundary present in the **real V3 verification prompt** and the **real M17
contract**; label-register correctness per frame; no cross-relation leakage;
border prompts byte-identical; V3 verification-request identity preserved when
Class-B is off; diagnostic configs unable to read TEST and filtering TRAIN to
exactly 100/100/100/10/100 rows; the parser fix behind its flag with ordinary
parsing unaffected; SAFE configs unable to enable the parser change; recollection
config inert and namespace-isolated; promotion gates complete; calibration hashes
byte-identical; no production import of the gold-reading evaluator.

```bash
python -m pytest tests/ -q -p no:randomly    # 3827 passed, 4 skipped
python -m pytest tests/ -q                   # 3827 passed, 4 skipped
python -m pyflakes src/ tests/ scripts/      # clean
git diff --check                             # clean
```

Audit 0076's baseline was `3724 passed, 4 skipped`; the difference is exactly the
97 new tests in this file plus 6 parametrized instances added to the audit-0076
file (three new `v3_1` modules times its two AST-scanning tests). Two pre-existing tests
failed during development and both were fixed as real regressions, not adjusted:
the V3 verification edge-identity change described in §4.

## 15. Honest Limitations

1. **No Class-B feature has ever been executed against a model.** The wiring is
   proven; the *value* is entirely unmeasured. Every Class-B number in this audit
   is a baseline, never a result.
2. The SAFE matrix numbers are TRAIN replay over persisted state, not TEST. **No
   TEST improvement is claimed and none can be until a submission is scored.**
3. `hasCapacity` (0.080) and `personHasCityOfDeath` (0.390) are still unchanged
   by anything runnable today; both are recall-bound and both fixes are Class B.
4. `stock_support_dominance` remains the least robust safe feature - hence
   SAFE_CORE.
5. M17 runs `mode: shadow`; its verdicts influence the hypothesis graph but the
   city boundary's production weight is carried mostly by the V3 verification
   path.

## Verdicts

**PASS — V3.1 SAFE SUBMISSION MATRIX READY**

Two independently runnable TEST variants, both `FULL_TEST_READY` under the
unchanged Audit-0073 calibration, differing by exactly one line. Both improve
both official metrics on TRAIN replay; SAFE_CORE harms zero rows; borders are
byte-identical in both. The fidelity gate reproduces the frozen baseline exactly
before anything changes.

**PASS — CLASS-B TARGETED DIAGNOSTICS READY**

The four audit-0076 instructions plus the parser fix are bound to the real
prompt paths, relation-isolated, feature-flagged, version-and-hash tracked, and
provably absent when disabled. Five single-feature TRAIN diagnostics, a CPU
evaluator with candidate-level instrumentation, and promotion gates written
before the data exist. The aggressive config remains `NOT_READY` and the
recollection profile promotes nothing.

**Neither verdict claims any Class-B feature works.** That question is what the
diagnostics are for.

## Recommended GPU Execution Order

**1. Submit SAFE_CORE first** (monotone on TRAIN, zero harmed rows):

```bash
python scripts/check_v3_test_readiness.py \
  --config configs/experiments/cover_kbc_v3_1_safe_core_test.yaml
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_1_safe_core_test.yaml --no-eval
```

**2. Submit SAFE_FULL** as the second variant, to price `stock_support_dominance`
on held-out data:

```bash
python scripts/run_cover.py \
  --config configs/experiments/cover_kbc_v3_1_safe_full_test.yaml --no-eval
```

**3. Capacity diagnostic** - the largest single opportunity in the benchmark
(0.080, 92/100 rows never recalled), 100 rows:

```bash
python scripts/run_cover.py \
  --config configs/experiments/v3_1_diag_capacity.yaml --no-eval

python scripts/evaluate_v3_1_diagnostic.py \
  --relation hasCapacity \
  --baseline-run outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z \
  --diagnostic-run outputs/<new run dir> \
  --gold benchmark/data/train.jsonl \
  --output-dir outputs/v3_1_class_b_diagnostics/capacity
```

**4. Remaining diagnostics** in `promotion.RECOMMENDED_ORDER`: city, stock, award
(10 rows, cheapest, weakest evidence), area parser.

**5. Only if a gate passes**: promote the feature in
`cover_kbc_v3_1_train_collection.yaml`, run the collection, check coverage,
re-derive M21, decide M20 on evidence, re-validate readiness.
