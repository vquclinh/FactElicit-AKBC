# Audit 0048: Independent adversarial review of offline M20/M21 TRAIN derivation

Verdict: FAIL — DO NOT COMMIT / DO NOT RUN REAL DERIVATION

Scope: read `COVER_KBC_Technical_Proposal_New.pdf`, the new derivation CLI and modules, the M20/M21 production contracts/loaders, telemetry/sufficiency contracts, the pinned evaluator wrapper and evaluator snapshot, relevant configs, and relevant tests. I did not rely on audit 0047 or implementation comments for the verdict.

## Blocking findings

### P1: dirty-tree provenance can silently lie about the derivation implementation

Current HEAD is `264c980361a513078903526440c72adc6e10edaf`, the supplied collection source SHA. The new derivation files are untracked. `scripts/derive_train_calibration.py` resolves provenance with `git rev-parse HEAD` only (`_repo_sha`, lines 99-114) and writes that value as `derivation_repo_sha` (lines 443-450). There is no `git status --porcelain` or equivalent clean-tree guard.

If the real derivation is run now, `derivation_repo_sha` will incorrectly name the collection-source commit, not the derivation implementation. If it is run later from a dirty checkout, it can record a clean HEAD while executing modified code. That is provenance corruption and can force a required real rerun.

Verdict on review point 1: the real derivation MUST NOT be run before the implementation is committed, and it MUST be run from a clean exact commit checkout. A fail-closed dirty-tree guard is required before the real run.

### P1: gold join does not preserve the official evaluator's one-to-one semantics

The pinned evaluator is one-to-one at row level: numeric scoring tracks `matched_gts` (benchmark/evaluate.py lines 64-82), and string scoring uses maximum bipartite matching between unique predictions and gold entities (lines 85-105).

The new gold join asks the evaluator about each candidate independently (`GoldIndex.label`, `src/cover_kbc/controller_calibration/gold_join.py` lines 87-114), then sums those independent booleans over `candidates_supported`, `candidates_contradicted`, and `candidates_named` (lines 273-283). This can overcount true positives and undercount false positives when multiple candidate keys correspond to one gold entity.

Real TRAIN reachability was confirmed:

- `Nobel Prize in Physiology or Medicine` / `awardWonBy`: aliases `Max Theiler` and `Maks Teyler` are one gold entity. The official row scorer gives TP=1 for both together, but `GoldIndex.label(...)` returns `True` for each. `score_actions()` on both supported keys returns `supported_correct=2`, `supported_incorrect=0`.
- `Wellington Island` / `hasArea`: gold `5556`; two predictions inside the same +/-5% tolerance band score TP=1 together, but both label true independently.

This contaminates `verified_gain`, `expected_fp`, M21 bins, and the coefficient totals before the production artifacts are written. The fix must attribute per-action candidate lists with the evaluator's row-level matching semantics, not independent single-candidate labels.

### P1: beta/gamma ratios can explode on tiny positive denominators

`derive_planner_calibration()` computes `beta = gain_total / delta_r_total` and `gamma = gain_total / delta_h_total` whenever the denominator is merely positive (`src/cover_kbc/controller_calibration/derivation.py` lines 780-789). There is no minimum denominator, cap, fallback, or refusal for tiny positive totals. The diagnostics record zero/nonzero, but do not prevent an artifact with an arbitrary large coefficient.

This violates the review requirement that ratios cannot explode on small denominators. Because the real 134 MB telemetry has not been derived yet, this must fail closed before producing the one real artifact.

## Non-blocking findings

- No scripted/full-scale calibration constants are embedded in production. The derivation uses observed quantiles, means, counts, and ratios; synthetic sources are refused by production loaders.
- M20 Table 6 ownership is mostly correct: qualitative policy is in `RELATION_BUDGET_POLICIES` (`src/cover_kbc/control/relation_budget.py` lines 49-100), and `derive_m20()` reads it while sizing observed quantities only (`derivation.py` lines 404-495).
- Award verification reserve is protected by the production ledger. Foreign protected pools are withheld from unrelated actions (`budget_accounting.py` lines 167-211).
- `deltaH` is measured from telemetry (`derivation.py` lines 590-592), and `gamma` becomes nonzero if `delta_h_total > 0` (lines 783 and 787). The C-02 path is not hardcoded to zero.
- Production artifacts have a structural leakage guard forbidding `SubjectEntity`, `ObjectEntities`, aliases, prompts, raw outputs, and candidate lists (`derivation.py` lines 880-907). I did not find a production artifact path that serializes TRAIN factual identity.
- Input binding is strong for preserved artifacts when the real `--expect-*` hashes are supplied: telemetry, predictions, and manifest hashes are checked directly (CLI lines 353-379), and the manifest binds TRAIN hash, row count, config hash, integration mode, completion, unresolved failures, and sufficiency (lines 164-195). This does not bind the derivation code state; the dirty-tree guard above is still required.
- The generated artifact shapes round-trip through canonical production loaders: `load_calibrations()` (`relation_budget.py` lines 380-405), `load_history()` (`historical_bins.py` lines 477-480), and `load_planner_calibration()` (`micro_planner.py` lines 518-528). Activation remains a later milestone.

## Explicit answers

A. Is it safe to commit the derivation implementation? No, not as-is. P1 findings require remediation first.

B. After committing, is it safe to run the real 134 MB TRAIN derivation? No, not with the current code.

C. Must the real derivation be run from a CLEAN exact commit checkout? Yes.

D. Is a dirty-tree fail-closed guard required before the real run? Yes.

E. Are any scripted calibration numbers capable of leaking into the real artifacts? I found no embedded scripted calibration constants or synthetic production path.

F. Is any TRAIN factual identity capable of leaking into production artifacts? I found no production artifact schema path for subjects, objects, aliases, prompts, raw output, or candidate identity. The blocking gold issue is incorrect count attribution, not serialized identity leakage.

G. Are M20/M21 artifacts actually consumable by their canonical loaders? Yes, the artifact shapes are consumable by the canonical loaders and readiness tests; production activation should remain off until the blockers are fixed.

## Validation

Commands and results:

- `sha256sum benchmark/evaluate.py` -> `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22`, matching the supplied pinned evaluator SHA.
- Gold one-to-one probe for `Max Theiler` / `Maks Teyler`: official row TP for both aliases together was `1`; independent `GoldIndex.label` returned `True` for both; `score_actions()` returned `2 0 2.0 0.0`.
- Numeric one-to-one probe for `Wellington Island` / `hasArea`: official row TP for two +/-5% values together was `1`; independent labels were `True True`.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider tests/test_train_calibration_derivation.py tests/test_derive_train_calibration_cli.py tests/test_calibration_sufficiency.py tests/test_controller_calibration_telemetry.py tests/test_controller_calibration_readiness.py tests/test_relation_budget.py tests/test_micro_planner.py tests/test_m20_precharge_gate.py tests/test_m21_production_bridge.py tests/test_layer6_integration.py` -> `395 passed in 3.38s`.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider` -> `3148 passed, 3 skipped in 26.38s`.
- `git rev-parse HEAD` -> `264c980361a513078903526440c72adc6e10edaf`.
- `git status --short` before this audit showed untracked `docs/audits/0047-offline-m20-m21-calibration-derivation.md`, the new CLI/source/test files, and no tracked modifications.
- `git diff -- benchmark/` -> empty.
- `git diff --stat` -> empty for tracked files at review time.

## Required remediation before real derivation

1. Add a fail-closed clean-tree guard to the derivation CLI before any artifact is written, and record the exact committed derivation SHA only from a clean checkout.
2. Replace independent per-candidate gold labels with per-action row-level evaluator attribution that respects alias maximum matching, numeric one-to-one tolerance matching, and duplicate candidate surfaces.
3. Add coefficient denominator guards/caps/refusals for `beta` and `gamma` so tiny positive denominators cannot produce unstable production policy.
4. Add regression tests for all three blockers, including real-alias and numeric-tolerance duplicate cases.
