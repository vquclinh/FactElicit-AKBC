# 0049 — P1 Remediation of the Offline TRAIN Calibration Derivation

**Verdict: PASS — ALL THREE P1 BLOCKERS FIXED AND VERIFIED.
SAFE TO INDEPENDENTLY RE-REVIEW. NOT YET SAFE TO RUN THE REAL DERIVATION.**

---

## 0. Scope

`COVER_KBC_Technical_Proposal_New.pdf` was re-read before any change; §16
(Table 6), §17 (the utility and `τ_continue`) and §9.3 remain the contract.
Audit 0048 was read in full and treated as the specification for this
milestone.

Exactly three defects were addressed — P1-1, P1-2, P1-3. No production
activation, no VAL, no TEST, no real 134 MB derivation, no model change, no
benchmark change, no architecture refactor.

All three findings were independently reproduced before being fixed, and one of
my own first attempts at P1-2 was **wrong and caught by the verification** (§2.3).

---

## 1. HEAD, working tree, benchmark

| item | value |
|---|---|
| HEAD | `264c980361a513078903526440c72adc6e10edaf` |
| Tracked modifications | **none** — every change is in files that were already untracked from milestone 0047, plus one new test file and this audit |
| `git diff` (tracked) | empty |
| `git diff -- benchmark/` | empty |
| `sha256sum benchmark/evaluate.py` | `2d592ae177c7b230922bb959da7a8ee1c4c662bf72a99d4dbd0cf62170ff9e22` — the pinned value |
| Tests | **3 186 passed, 3 skipped** (was 3 148) |
| `pyflakes src/ tests/ scripts/` | clean, exit 0 |

```
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

---

## 2. P1-1 — fail closed on a dirty derivation source

### 2.1 Reproduced

Before the change, `derive_train_calibration.py` resolved provenance with
`git rev-parse HEAD` alone. On this checkout that returns
`264c980…` — the *collection* commit — while the derivation implementation was
untracked. The artifact would have claimed to come from a commit that does not
contain the code that produced it. Confirmed exactly as Audit 0048 reported.

### 2.2 Fixed

`resolve_derivation_source()` in `derivation.py`, called by the CLI **before any
input is opened and before any artifact is written**. It refuses, in order:

| condition | behaviour |
|---|---|
| `git` unavailable, or HEAD unresolvable | refuse |
| HEAD not a 40-char commit id (`unknown`, a ref name, empty) | refuse |
| any **staged** modification under a source path | refuse |
| any **unstaged** modification under a source path | refuse |
| any **untracked** file under a source path | refuse |
| a derivation implementation file the commit does not contain | refuse |
| clean exact checkout, detached or not | **accept**, return the SHA |

"Source path" is `src/`, `scripts/`, `tests/`, `configs/`, `benchmark/`. A stray
file under `outputs/` says nothing about which code ran and does not block;
ignored files never appear. Git is always run with `-C REPO_ROOT`, never against
the caller's cwd.

**There is no `--allow-dirty`.** `test_there_is_no_allow_dirty_escape_hatch`
scans both the resolver and the CLI for `allow_dirty`, `force`, `skip_clean`,
`ignore_dirty`, `--allow-dirty`, `--force`, `--skip-clean-check`.

The two provenance fields stay distinct and are asserted so:
`collection_repo_sha` comes from the collection manifest (and is separately
refused if absent or `unknown`); `derivation_repo_sha` is the clean commit this
process executes from.

### 2.3 Observed

The guard fires on this very tree, which is the state it exists for:

```
REFUSED: the derivation source at /…/FactElicit-AKBC is not a clean checkout of
264c980361a513078903526440c72adc6e10edaf:
  ?? scripts/derive_train_calibration.py;
  ?? src/cover_kbc/controller_calibration/derivation.py;
  ?? src/cover_kbc/controller_calibration/gold_join.py; …
```

### 2.4 Tests — 11, against **real temporary Git repositories**

`clean exact checkout accepted` · `untracked source refused` · `modified tracked
source refused` · `staged modification refused` · `deleted source refused` ·
`untracked non-source does not block` · `detached HEAD on an exact commit
accepted` · `commit without the implementation refused` · `non-repository
refused` · `no escape hatch` · `the two SHAs stay distinct`.

Plus two at CLI level: `test_the_cli_refuses_a_dirty_derivation_source` (and
asserts **no artifact directory is created**), and
`test_the_guard_fires_before_any_input_is_read`, which passes a missing input
alongside a dirty tree and asserts the dirty refusal wins — proving ordering.

`git` is not mocked anywhere: the guard's only job is to read repository state,
so a fake `git` would test the fake.

---

## 3. P1-2 — official one-to-one gold attribution

### 3.1 Reproduced

Both cases, against real TRAIN and the real pinned evaluator:

```
Max Theiler + Maks Teyler   official row TP = 1, independent labels = True, True
Wellington Island 5556 ±5%  official row TP = 1, independent labels = True, True
```

### 3.2 Fixed

`GoldIndex.label` (per-candidate) is **gone**, replaced by
`GoldIndex.attribute(subject, relation, candidates)`, which performs **one**
one-to-one assignment over the whole candidate set:

- **Strings** — Kuhn's augmenting-path maximum bipartite matching over the
  evaluator's own normalised alias sets: the same algorithm
  `string_true_positives` runs, kept locally only because the evaluator returns
  a count and attribution needs the assignment.
- **Numerics** — the evaluator's greedy first-fit with `matched_gts`, reproduced
  exactly rather than improved on; a maximum matching here would score
  differently from the leaderboard.
- **Duplicates** collapse by `normalize_string` before matching, exactly as
  `evaluate_per_sr_pair` collapses a row's predictions.
- **Counting is over normalised forms**, never raw keys, so two spellings of one
  prediction cannot both earn gain.

Every attribution is **cross-checked against the evaluator's own count** for the
same input, and a mismatch raises. That is what makes the adaptation trustworthy
rather than an approximation, and it is itself tested
(`test_an_attribution_that_drifts_from_the_evaluator_is_refused`).

`score_actions` now runs **one attribution per action across all categories**
(supported ∪ named ∪ contradicted), in that precedence, then reads per-category
views off the single assignment. Precedence matters and is deliberate: an
augmenting-path matching never unmatches what it has already matched, so
supported-first means a candidate the action genuinely *asserted* is credited
rather than having its gold entity consumed by the same entity appearing in a
weaker category. Cardinality is unaffected by the order.

A new `distinct_correct` field records the gold entities the action accounts for
across all categories, and `score_actions` raises if it ever exceeds either the
matched-gold count or the row's gold size.

**On the "fail closed rather than double count" clause:** a candidate key
legitimately appears in two categories — the execution seam records support and
contradiction independently, and one action can do both to one candidate
(observed on real `awardWonBy` rows). That is not an undefined precedence, so
failing closed would break every real run. It is handled by attributing once and
exposing per-category *views*: `verified_gain` reads only `supported_correct`,
so the gold entity is counted once for gain.
`test_a_key_in_two_categories_is_attributed_once_not_twice` pins this.

### 3.3 A wrong first attempt, caught

My first implementation normalised candidates *before* numeric parsing. Because
`normalize_string("5556.0")` → `"5556 0"`, which parses as no number,
Wellington Island went from over-counting (2) to under-counting (0) — a
different wrong answer. The evaluator's numeric path reads **raw** surfaces;
only the dedup key is normalised. Caught by the cross-check against the official
count, and corrected. The verification is what found it, not review.

### 3.4 Observed — the two required cases

```
CASE A  Max Theiler / Maks Teyler (awardWonBy)
  official row-level TP (both together) : 1
  attribution matched_gold              : 1
  attribution count_correct(both)       : 1
  per-candidate is_correct              : [True, False]
  AGREE: True

CASE B  Wellington Island / hasArea, two values in one ±5% band
  gold value 5556.0 (1 gold entity); predictions ['5556.0', '5722.68']
  official row-level TP (both together) : 1
  attribution count_correct(both)       : 1
  AGREE: True
```

End to end through `score_actions`:

```
supported = ("Max Theiler", "Maks Teyler")
  before:  supported_correct = 2, supported_incorrect = 0
  after :  supported_correct = 1, supported_incorrect = 1,
           verified_gain = 1.0, distinct_correct = 1, expected_fp = 0.5
```

### 3.5 Tests — 14

Two aliases → one TP · two numerics in one band → one TP · duplicate surface →
no extra TP · two distinct gold entities → two TP · unmatched alias → FP ·
one gold entity cannot be claimed across two categories · supported precedence
survives a contradiction of the same entity · a key in two categories is
attributed once · relation isolation intact · drifted attribution refused ·
effects carry no gold strings.

Plus a **sweep over every real TRAIN row that has gold** —
`test_attribution_never_exceeds_the_official_row_score` — asserting for all 477
rows that `count_correct == official TP` and `matched_gold <= gold size`.

### 3.6 Leakage re-checked

Full-scale artifacts, all **20 864** TRAIN gold surfaces and **468** subjects,
exact-equality and whole-word matching over every string leaf and key:

```
exact leaks 0, word leaks 0
```

---

## 4. P1-3 — β / γ denominator safety

### 4.1 Reproduced

`beta = gain_total / delta_r_total` was computed whenever the denominator was
merely `> 0`. A denominator of `1e-6` with any gain yields an unbounded
production coefficient.

### 4.2 Fixed

A new `DerivationSettings.minimum_denominator`, default **1.0**, recorded in the
settings and therefore in provenance. Three cases, kept apart:

| denominator | numerator | behaviour |
|---|---|---|
| exactly `0.0` | any | coefficient `0.0`, term inert — truthful, and what C-02 requires of γ |
| `0 < d < floor` | `> 0` | **`DerivationError`**, naming the totals and the ratio it refused |
| `0 < d < floor` | `0` | `0.0` — a stable, real observation that no gain occurred |
| `d >= floor` | any | derived normally |

**Justification of 1.0, from the observable rather than any score.** Both ΔR and
ΔH are clamped to `[-1, 1]` per action, so one unit is exactly one action moving
the quantity across its entire range. Below one unit in total, the collection
observed less than a single full movement and a rate *per unit* is
extrapolation. It sits six orders of magnitude above the rounding floor
(`FLOAT_PRECISION = 6`) and, at real scale, 67× below the observed ΔR total —
so it bites on pathology and not on data. Nothing about TRAIN F1 informs it, and
raising it can only cause more refusals.

**No epsilon, no clamp.** `test_no_epsilon_is_added_to_a_denominator` parses the
function, strips its docstring, and asserts the code body contains no `1e-`,
`epsilon`, `max(1e` or `+ eps`. `delta` and `eta` are left alone and the reason
is recorded: their denominators are counts of calls and actions, whole units
with a natural floor of one, which cannot be small-but-positive the way an
accumulated float can.

### 4.3 Observed

At full 477-row scale the guard is satisfied comfortably, and γ remains
truthfully inert:

```
verified_gain_total        29.0
delta_r_reduction_total    66.666667      beta_denominator_supported   True
delta_h_reduction_total    0.0            gamma_denominator_supported  False
minimum_denominator        1.0
gamma_is_inert_because_delta_h_never_moved  True
alpha 1.0  beta 0.435  gamma 0.0  delta 0.006392  eta 0.01233  kappa 1.0
tau_continue 0.0   lookahead_depth 2
```

At CLI level the guard also fires on a genuinely under-supported slice: the
six-row fixture moves the residual by ~0.67 in total, and
`test_the_default_denominator_floor_refuses_a_tiny_slice` asserts the default
floor refuses it. The pipeline tests therefore pass an explicit slice-appropriate
floor, which still refuses the rounding-noise case.

### 4.4 Tests — 10

zero denominator stays inert · tiny positive with gain refused · tiny positive
without gain stable · threshold boundary inclusive (1.0 accepted, 0.9 refused) ·
normal denominator finite and deterministic · genuine H movement still permits
γ > 0 · structural-zero H still yields γ = 0 · floor recorded in settings ·
nonsensical floor refused (0, negative, NaN, inf) · no epsilon in the code body.

---

## 5. C-02 remains valid

γ is `0.0` because H did not move — 0 of 2 352 executed actions — not because
the code forces it. `test_genuine_h_movement_still_permits_a_non_zero_gamma`
replays the same fixture with H genuinely moving and asserts γ > 0 and
`gamma_is_inert_because_delta_h_never_moved is False`. Nothing was manufactured.

---

## 6. Regressions — none found

Re-verified after the changes:

| property | status |
|---|---|
| no model runtime reachable from the derivation | intact (grep + `test_the_cli_never_builds_a_model_runtime`) |
| no neural training | intact |
| no VAL/TEST read | intact |
| `benchmark/evaluate.py` untouched | intact, hash matches the pin |
| M20 Table-6 qualitative ownership | untouched |
| hard verification reserve semantics | untouched (`awardWonBy` reserve 10, others 0) |
| M20 / M21 canonical loader compatibility | intact |
| deterministic serialisation | **byte-identical** across two full-scale runs |
| no timestamps / random ids in artifacts | intact |
| no TRAIN factual identity leakage | 0 leaks over 20 864 surfaces |
| sparse-bin fallback semantics | untouched |
| strict `U > tau_continue` | untouched, τ = 0.0 |
| `--expect-*-sha256` binding | intact |
| TRAIN hash / row / sufficiency checks | intact |

Full-scale M20 budgets and M21 bins are numerically unchanged. The gain total is
also unchanged at 29.0 on the scripted full-scale fixture — expected, and worth
stating plainly: scripted runtimes emit fictional constants that rarely produce
two aliases of one gold entity inside one action, so the over-count has almost
no purchase there. **The defect is demonstrated directly on real TRAIN rows
(§3.4), which is the decisive evidence; the real frozen-model telemetry is where
it would have mattered at scale.**

---

## 7. Validation commands and results

| check | result |
|---|---|
| `pytest tests/test_calibration_p1_remediation.py` | **35 passed** |
| `pytest` (the three calibration files) | **105 passed** |
| `pytest -q` (full suite) | **3 186 passed, 3 skipped** |
| `pyflakes src/ tests/ scripts/` | clean, exit 0 |
| `git diff -- benchmark/` | empty |
| `git status --short -- benchmark/` | empty |
| `git diff` (tracked files) | empty |
| full-scale re-derivation × 2 | byte-identical artifacts |
| gold-leakage sweep, 477 rows | 0 exact, 0 word |

---

## 8. Explicit answers

1. **P1-1 dirty-tree provenance** — **fixed.**
2. **P1-2 official one-to-one gold attribution** — **fixed.**
3. **P1-3 coefficient denominator safety** — **fixed.**
4. **Regressions** — none.
5. **Test results** — 3 186 passed, 3 skipped; 35 new P1 tests.
6. **Benchmark diff** — empty; evaluator hash matches the pin.
7. **Git status** — 9 untracked files, no tracked modifications.
8. **Safe to independently re-review?** — **Yes.**
9. **Safe to commit?** — Yes from this milestone's side, once an independent
   reviewer confirms the three fixes. Nothing was committed here.
10. **Safe to run the real derivation?** — **NO.** It must wait for independent
    verification *and* the commit, and it must then be run from that clean exact
    commit — which the new guard now enforces rather than merely recommends.

Production is not active. No real calibration has been derived.

---

## 9. Remaining blockers before the real derivation

1. Independent review of this remediation.
2. Commit the implementation. Until then the guard will — correctly — refuse to
   run, because the derivation source is untracked.
3. Run the real derivation from that clean exact commit, with the three
   `--expect-*-sha256` flags bound to the preserved artifact hashes, from the
   repository root.

Unchanged from Audit 0047 §15: production activation additionally needs F-11,
F-22, F-24, a validation config reaching `FULL_VALIDATION_READY`, and the C-02
disclosure in the paper.

---

*No commit, no push. `benchmark/` untouched. No model weights loaded; no TRAIN,
VAL or TEST inference run; the real 134 MB derivation was not executed.*
