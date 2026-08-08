# Audit 0050: Targeted P1 Remediation Re-Verification

Date: 2026-08-08

Verdict:

PASS — ALL THREE P1 REMEDIATIONS INDEPENDENTLY VERIFIED; SAFE TO COMMIT

Scope:

- Read `./COVER_KBC_Technical_Proposal_New.pdf` first and treated it as the architecture contract.
- Targeted re-verification only of the three Audit 0048 P1 blockers after remediation.
- Did not re-audit unrelated Modules 0-19.
- Did not trust Audit 0049 conclusions; traced executable source and tests directly.
- Did not modify production source, tests, config, benchmark, or existing audits.

## P1-1: Dirty-Tree / Derivation Provenance

Status: FIXED.

Evidence from executable source:

- The CLI resolves derivation provenance before opening calibration inputs or creating output directories: `scripts/derive_train_calibration.py:348-353` precedes file checks at `:355-359` and output writes at `:470-481`.
- Git is executed explicitly against a repository root via `git -C <repo_root>` in `src/cover_kbc/controller_calibration/derivation.py:912-919`, and the production default root is `cover_kbc.paths.REPO_ROOT` at `:959-962`, not caller cwd.
- HEAD must resolve to a 40-character commit SHA at `:964-969`.
- Source dirt is detected from `git status --porcelain` over source prefixes at `:971-981`; staged, unstaged, deleted, renamed, and untracked source paths are rejected at `:983-990`.
- The derivation implementation files are explicitly required to exist in HEAD at `:897-901` and checked with `git ls-tree` at `:992-1000`.
- I found no production escape hatch in CLI arguments or in `resolve_derivation_source()`; the only new operational knob is `--minimum-denominator`.
- `collection_repo_sha` is still sourced from the collection manifest identity, while `derivation_repo_sha` is the clean HEAD returned by the guard: `scripts/derive_train_calibration.py:439-442`.

Independent probes:

- Current uncommitted checkout correctly refused:

```text
REFUSED the derivation source at /mnt/vquclinh/PROJECT-CMAKE/FACTELICIT-AKBC/FactElicit-AKBC is not a clean checkout of 264c980361a513078903526440c72adc6e10edaf: ?? scripts/derive_train_calibration.py; ?? src/cover_kbc/controller_calibration/derivation.py; ?? src/cover_kbc/controller_calibration/gold_join.py; ?? tests/test_calibration_p1_remediation.py; ?? tests/test_derive_train_calibration_cli.py; ?? tests/test_train_calibration_derivation.py. A production calibration must be derived from a committed, exact source state, so that its derivation_repo_sha names the code that actually produced it
```

- Temporary committed Git repository accepted a clean exact commit and refused an untracked source file:

```text
clean True aa10dcf91e04ee9e49013437a2d08648d351f8e8
untracked_refused True
```

- Actual CLI refused at the dirty-source guard before checking deliberately missing inputs, exiting 2 with the same dirty-source message.

Conclusion: a clean exact committed checkout is now enforced rather than merely recommended. The current tree must not run the real derivation until the derivation/remediation files are committed.

## P1-2: Official One-To-One Gold Attribution

Status: FIXED.

Evidence from executable source:

- `GoldIndex.attribute()` loads the pinned official evaluator and relation types, deduplicates by the evaluator's normalization, preserves raw numeric surfaces for numeric parsing, and calls the evaluator's own string/numeric TP functions: `src/cover_kbc/controller_calibration/gold_join.py:122-160`.
- Attribution cardinality is fail-closed against the pinned evaluator count at `:163-169`.
- String attribution mirrors evaluator alias-set matching at `:193-217`; numeric attribution mirrors evaluator greedy one-to-one numeric matching and ±5% tolerance at `:236-253`.
- `GoldAttribution.count_correct()` and `count_incorrect()` count distinct normalized forms, preventing duplicate surfaces from adding gain: `:280-294`.
- `score_actions()` performs one attribution over the precedence-ordered union `supported`, then `named`, then `contradicted`: `:475-485`, with a distinct-correct invariant guard at `:487-498`.
- Production M21 consumes supported verified gain and supported false-positive rate only through `ActionGoldEffect.verified_gain` and `false_positive_rate` at `:330-344`, then aggregates those quantities at `src/cover_kbc/controller_calibration/derivation.py:629-634`.

Independent attribution probes:

```text
A_award_aliases official 1 matched 1 correct 1 incorrect 1
B_area_tolerance official 1 matched 1 correct 1 incorrect 1
duplicate_surface official 1 matched 1 correct 1 incorrect 0
two_distinct_gold official 2 matched 2 correct 2 incorrect 0
unmatched_extra official 1 matched 1 correct 1 incorrect 1
relation_isolation official 0 matched 0 correct 0 incorrect 1
numeric_raw_vs_normalized official 1 matched 1 correct 1 incorrect 1
overlap_same_key supported 1 named 1 contradicted 1 distinct 1 gain 1.0 fp 0.0
overlap_alias_precedence supported 1 named 0 contradicted 0 distinct 1 gain 1.0 fp 0.0
```

The required real cases pass:

- Nobel Prize in Physiology or Medicine / awardWonBy with `Max Theiler` and `Maks Teyler`: official row TP = 1, derived attributed correct total = 1.
- Wellington Island / hasArea with two numeric predictions inside one gold value's ±5% band: official row TP = 1, derived attributed correct total = 1.

Category-overlap assessment:

- Supported-first precedence can change which category receives credit when one gold entity appears in multiple categories. For two aliases of the same entity, supported consumes the match and named/contradicted do not receive it.
- For the exact same raw key present in multiple categories, the current counters are per-category views over one assignment, so `supported_correct`, `named_correct`, and `contradicted_correct` can each read 1 while `distinct_correct` remains 1.
- This is a non-blocking P3 diagnostic ambiguity, not a remaining P1: the production artifact does not ship candidate identities or action-level category counters, and M21 production utility is driven by supported verified gain and supported FP, not by summing all category counters. The invariant that one gold entity cannot produce more than one production verified gain or distinct correctness total holds.

No gold/entity identity leakage:

- Production artifact builders call `assert_no_leakage()` before returning each payload: `src/cover_kbc/controller_calibration/derivation.py:1126`, `:1133`, `:1140`.
- Forbidden artifact keys include `ObjectEntities`, `SubjectEntity`, aliases/gold, prompts, raw output, and candidate identity lists at `:1061-1065`.

## P1-3: Beta / Gamma Denominator Safety

Status: FIXED.

Evidence from executable source:

- `minimum_denominator` defaults to 1.0 and is serialized in settings/provenance: `src/cover_kbc/controller_calibration/derivation.py:176`, `:192-200`, `:1052`.
- Non-finite, zero, or negative settings are refused at `:186-190`.
- Coefficients derive from observed executed records: gain total, positive residual reduction total, positive H reduction total, and physical calls at `:821-826`.
- The ratio helper implements the required policy:
  - denominator exactly zero returns 0.0 at `:832-833`;
  - sub-floor denominator with zero numerator returns 0.0 at `:834-836`;
  - sub-floor denominator with positive numerator raises `DerivationError` at `:837-844`;
  - otherwise derives normally at `:845`.
- There is no epsilon patch and no silent clamp in `derive_planner_calibration()`; coefficients are rounded only for deterministic serialization at `:855-865`.
- C-02 is truthful: `derive_m21()` records actual `delta_entropy` from telemetry at `:612-614`; coefficient derivation sums actual positive `delta_entropy` at `:824-825`; gamma is only zero when that denominator is zero.

Independent denominator probe:

```text
zero_H beta 2.0 gamma 0.0 diag 1.0 True False
supported_H beta 2.0 gamma 2.0 diag 1.0 True True
small_zero_gain beta 0.0 gamma 0.0 diag 1.0 False False
small_positive_gain_refused True
bad_min_refused 0.0
bad_min_refused -1.0
bad_min_refused nan
bad_min_refused inf
```

Dimensional assessment:

- The denominators are accumulated observed reductions in the same scalar quantities that telemetry records as pre/post state movement. H is the canonical control uncertainty owned by the runtime path, and the telemetry contract exposes `pre_state.entropy - post_state.entropy`.
- A floor of 1.0 is dimensionally coherent as one full unit of accumulated observable movement. Under valid low-movement telemetry it fails closed rather than corrupting coefficients; it does not optimize against TRAIN score and cannot silently manufacture a coefficient.

## Targeted Regression Checks

- No neural model/runtime call or neural training path was found in the derivation source; the full regression tests include a fatal monkeypatch against model runtime construction.
- No VAL/TEST path is introduced by the fixes. CLI rejects non-TRAIN-shaped paths, checks config split, and binds manifest identity.
- Benchmark evaluator is untouched; `git diff -- benchmark/` is empty and `benchmark/evaluate.py` hashes to the pinned SHA.
- Serialization remains deterministic: production writes use `json.dumps(..., sort_keys=True)` at `scripts/derive_train_calibration.py:478-481`.
- M20/M21 loader compatibility remains intact:
  - M20 loader: `src/cover_kbc/control/relation_budget.py:380-405`.
  - M21 history loader and exact/fallback lookup: `src/cover_kbc/control/historical_bins.py:272-335`.
  - M21 planner calibration loader: `src/cover_kbc/control/planner_types.py:344-354`.
- Strict `U > tau_continue` remains in the canonical planner at `src/cover_kbc/control/micro_planner.py:441-445`.
- Preserved `--expect-*` hash binding remains explicit at `scripts/derive_train_calibration.py:340-345` and enforced at `:364-373`; manifest-bound TRAIN/config/telemetry/prediction checks remain at `:147-189`.

## Validation

Commands and exact results:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<current dirty guard probe>'
REFUSED ... not a clean checkout of 264c980361a513078903526440c72adc6e10edaf ... ?? scripts/derive_train_calibration.py ... ?? tests/test_train_calibration_derivation.py ...
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<temporary committed Git repo probe>'
clean True aa10dcf91e04ee9e49013437a2d08648d351f8e8
untracked_refused True
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<gold attribution probe>'
A_award_aliases official 1 matched 1 correct 1 incorrect 1
B_area_tolerance official 1 matched 1 correct 1 incorrect 1
duplicate_surface official 1 matched 1 correct 1 incorrect 0
two_distinct_gold official 2 matched 2 correct 2 incorrect 0
unmatched_extra official 1 matched 1 correct 1 incorrect 1
relation_isolation official 0 matched 0 correct 0 incorrect 1
numeric_raw_vs_normalized official 1 matched 1 correct 1 incorrect 1
overlap_same_key supported 1 named 1 contradicted 1 distinct 1 gain 1.0 fp 0.0
overlap_alias_precedence supported 1 named 0 contradicted 0 distinct 1 gain 1.0 fp 0.0
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c '<denominator probe>'
zero_H beta 2.0 gamma 0.0 diag 1.0 True False
supported_H beta 2.0 gamma 2.0 diag 1.0 True True
small_zero_gain beta 0.0 gamma 0.0 diag 1.0 False False
small_positive_gain_refused True
bad_min_refused 0.0
bad_min_refused -1.0
bad_min_refused nan
bad_min_refused inf
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/derive_train_calibration.py --config /tmp/no-config.yaml --train-gold /tmp/no-train.jsonl --predictions /tmp/no-pred.jsonl --telemetry /tmp/no-telemetry.jsonl --manifest /tmp/no-manifest.json --output-dir /tmp/cover-kbc-should-not-write
REFUSED (dirty derivation source): ... not a clean checkout of 264c980361a513078903526440c72adc6e10edaf ...
Exit code: 2
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_calibration_p1_remediation.py
35 passed in 1.99s
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_train_calibration_derivation.py tests/test_derive_train_calibration_cli.py tests/test_calibration_sufficiency.py tests/test_controller_calibration_telemetry.py tests/test_controller_calibration_readiness.py tests/test_controller_calibration_collection.py tests/test_relation_budget.py tests/test_micro_planner.py tests/test_m20_precharge_gate.py tests/test_m21_production_bridge.py tests/test_layer6_integration.py
436 passed in 3.89s
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
3186 passed, 3 skipped in 37.80s
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pyflakes scripts/derive_train_calibration.py src/cover_kbc/controller_calibration/derivation.py src/cover_kbc/controller_calibration/gold_join.py tests/test_calibration_p1_remediation.py tests/test_derive_train_calibration_cli.py tests/test_train_calibration_derivation.py
<no output; exit 0>
```

```text
sha256sum benchmark/evaluate.py
2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22  benchmark/evaluate.py
```

```text
git diff -- benchmark/
<no output>
```

```text
git diff --
<no output>
```

```text
git status --short
?? docs/audits/0047-offline-m20-m21-calibration-derivation.md
?? docs/audits/0048-independent-adversarial-train-calibration-derivation-review.md
?? docs/audits/0049-p1-remediation-of-train-calibration-derivation.md
?? scripts/derive_train_calibration.py
?? src/cover_kbc/controller_calibration/derivation.py
?? src/cover_kbc/controller_calibration/gold_join.py
?? tests/test_calibration_p1_remediation.py
?? tests/test_derive_train_calibration_cli.py
?? tests/test_train_calibration_derivation.py
```

## Explicit Answers

A. P1-1 independently fixed? YES

B. P1-2 independently fixed? YES

C. P1-3 independently fixed? YES

D. Any new P0/P1 introduced by these fixes? NO

E. Safe to commit all derivation/remediation files? YES

F. After commit, must real derivation be run from that exact clean commit? YES

G. After commit, will the guard enforce that condition? YES

H. Safe to run the one real 134 MB derivation AFTER commit? YES

