# Audit 0024 — Corrective: M14 M11-Mining Abstention / NULL Semantics

Status: **PASS**
Date: 2026-08-06
Scope: one upstream defect in **M14**, discovered by Audit 0023 §52.
Not a milestone. No new module. M17 not started.

**History is preserved, not rewritten.** Audit 0021 passed after its §15A
correction, and that correction was right. It fixed M14's *own probe* path and
its NULL-evidence classification. It did not reach one further path — Module 11
mining — because nothing at the time exercised it against a sentinel. Audit 0023
did, from the outside, and found it. Audit 0021 is left exactly as it stands;
this audit records what escaped it.

---

## 1. Discovery source

Audit 0023 §8 and §52. While building M16's adapters, the abstention guard
`_candidate_key_for` was written to apply Module 3's own `is_abstain` before
keying a specialist observation. A smoke run then showed why it was needed: a
`personHasCityOfDeath` consensus contained a candidate keyed **`none`** with
`I = 3`. Tracing it upstream showed M14's mining path producing

```
normalized_surface = 'NONE'   mention_kind = TARGET_CITY
parse_status       = OK       usable = True
```

for a Module 11 record whose entire raw output was `NONE` — while M14's own
Stage-B path parsed the identical text as `ABSTAINED` with an empty surface.
M12, M13 and M15 were checked at the same time and were correct on both paths.

---

## 2. Old inconsistent behaviour

| Path | Input `"NONE"` | Result |
| --- | --- | --- |
| M14 Stage-B probe | `_locality_records` → `_is_abstention` | `ABSTAINED`, empty surface, **no candidate** ✅ |
| M14 Module-11 mining | inline `extract_localities` loop | `OK`, surface `"NONE"`, **target city** ❌ |

The NULL-evidence half was already correct on both paths: the mining path
already asked `asserts_relation_level_absence(text, sentinel_is_defined=…)`
with grammar awareness. Only **candidate suppression** was missing.

---

## 3. Why M16 remained safe

M16 keys specialist observations through `_candidate_key_for`, which applies
`is_abstain` — Module 3's own predicate, the one `add_entity_mentions` uses to
refuse `"NONE"`/`"UNKNOWN"`/`""` before keying. A phantom `none` candidate could
therefore never enter a consensus state, and Audit 0023's
`test_an_abstention_never_becomes_a_candidate` asserted exactly that.

M16 was safe. The **M14 artefact was not**: `null_temporal_specialist.jsonl`
carried the phantom surface, its occurrence table counted it, and anything
reading that artefact without M16's guard would have inherited the defect.

---

## 4. Root cause

`_locality_records` is M14's correct parser. It handles, in order:
`RUNTIME_ERROR` → `EMPTY` → `_is_abstention` → `NO_LOCALITY` → extraction. It
was even written with provenance overrides (`source`, `operation_id`,
`independence_group`, `sample_index`, `prompt_sha256`) — i.e. designed to be
reused by the mining path.

`_mine` did not call it. It reimplemented the tail of it:

```python
for surface, context, kind in extract_localities(text, spec):   # <- no guard
    normalized, flags = normalise_locality(surface)
    ...
```

Two extraction sites, one of which knew about abstentions. §15A's correction
landed on the parser; this call site had already forked away from it.

---

## 5. The fix — one parser, not two

`_mine` now calls `_locality_records` with Module 11's provenance substituted
for the probe's. Supporting change: `_locality_records` takes provenance as
explicit keyword arguments instead of a `probe` object plus overrides (a mined
record has no probe), and `_probe_provenance` supplies them for the two
probe-driven call sites.

**Semantic duplication went down, not up.** There is no new `if text == "NONE"`
anywhere; the shared predicate `_is_abstention` — which covers
`_EXPLICIT_EMPTY_SENTINELS | _EPISTEMIC_ABSTENTIONS` — now governs both paths
because both paths are one path. A test AST-parses the module and asserts
`extract_localities` is called **exactly once**, inside `_locality_records`, and
nowhere else, so a second site cannot quietly reappear.

Net: **+47 / −34** lines in one file.

---

## 6. Producer-grammar distinction (unchanged, now enforced on a clean surface)

Audit 0021 established which producer defines `NONE` as an empty *answer*, and
that logic was already correct in `_mine`; it is now reached with the candidate
surface suppressed.

| Producer | Grammar | `NONE` → | Candidate? |
| --- | --- | --- | --- |
| M11 `query_rewrite` | carries **M10's output contract** verbatim: *"If there are none, output exactly: NONE"* | **substantive** `NO_KNOWN_LOCALITY_SUPPORT`, group `QUERY_REWRITE` | never |
| M11 `pseudo_memory` | free-form sketch; defines no empty sentinel | `ABSTAINED` → **failed recall** | never |
| M11 `self_ask` | decomposition; defines no empty sentinel | `ABSTAINED` → **failed recall** | never |
| M14 Stage-B | offers `UNKNOWN` for "if you do not know of one"; never defines `NONE` | `ABSTAINED` → **failed recall** | never |

Grammar is read from **producer identity** (`record.kind is
RecallOperationKind.QUERY_REWRITE`), never inferred from the text.

---

## 7. `NONE` versus `UNKNOWN`

Audit 0021 §15A's invariant, restated and now true on every path:

* **`UNKNOWN`, "I don't know", "not sure", "cannot determine"** — epistemic
  abstention. Failed recall on **every** producer, under **every** grammar,
  forever. Never substantive, never a candidate.
* **`NONE`** — an empty-answer sentinel *only where a grammar defines it*.
  Substantive from `query_rewrite`; failed recall from everything else. Never a
  candidate anywhere.
* **"No known city of death."** — a third-person claim about the record.
  Substantive from any producer (`states_no_known_locality`). Never a candidate.
* **"I don't know the city of death."** — a first-person claim about the model.
  Failed recall. Never substantive.

An operation that made an explicit relation-level claim is excluded from the
failed-recall list by `build_null_evidence`, so one record is never billed to
both classes.

---

## 8-9. Candidate-suppression invariant

For **every** M14 input path, after this correction:

```
strict_key("NONE")     never appears as a locality candidate key
"UNKNOWN"              never appears as a locality candidate
"I don't know"         never appears as a locality candidate
""                     never appears as a locality candidate
```

Asserted three ways: over `result.locality_observations`, over
`result.occurrences`, and over the **persisted** `null_temporal_specialist.jsonl`
from a staged run. A live four-query staged run now yields:

```
persisted locality surfaces : ['']
persisted occurrences       : []
NONE present                : False
M16 candidate keys          : []
```

Real localities are unaffected: `"Person Alpha died in City Alpha."` still mines
`City Alpha` as a usable target.

---

## 10. NULL-evidence classification

| Fixture | `no_known_locality` | failed recall | substantive? |
| --- | --- | --- | --- |
| all three producers `NONE` | 1 (`QUERY_REWRITE`) | 2 | yes — from the one grammar that defines it |
| `query_rewrite` `NONE`, rest `UNKNOWN` | 1 (`QUERY_REWRITE`) | 2 | yes |
| `pseudo_memory` `NONE`, rest `UNKNOWN` | 0 | 3 | **no** |
| all three `UNKNOWN` | 0 | 3 | **no** |
| `pseudo_memory` `NONE` + `self_ask` `NONE` | 0 | ≥2 | **no** — repetition does not scale |

Independent ignorance is still ignorance: repeated `UNKNOWN` and repeated
unanchored `NONE` both stay out of the substantive class no matter how many
groups produce them.

---

## 11. Provenance and origin preservation

A mined observation carries the Module 11 record's `operation_id`,
`independence_group`, `prompt_sha256`, `sample_index`, `model_id` and
`family` **unchanged** — the fix substitutes provenance into the shared parser
rather than minting any. A test asserts field-by-field equality and then asserts
that M16's `derive_origin_event_id` maps the record and the observation to the
**same origin id**.

M16's origin formula is untouched. One Module 11 record remains one physical
origin, whatever semantic annotation M14 adds to it.

---

## 12. M16 integration regression

Audit 0023's guard **stays** — defence in depth, per the brief. The M16 test
that previously asserted "M14 emits a NONE surface, prove M16 drops it" has been
split, because its precondition encoded the defect:

* `test_a_corrected_m14_result_yields_no_phantom_abstention_candidate` — the
  upstream result is now clean at source (every mined observation has an empty
  surface and `usable=False`), and M16 produces no candidate.
* `test_the_abstention_guard_still_protects_against_malformed_upstream` — a
  `LocalityObservation` with `normalized_surface="NONE"`, `parse_status=OK`,
  `usable=True` is constructed **by hand**, bypassing M14's parser, exactly as a
  future upstream regression would arrive. M16 still refuses to key it.

M16's consensus semantics are otherwise unchanged: all 106 of its tests pass,
and every other fixture produces equal results.

---

## 13. M15 / shared-cross-family regression

`cross_family.py` was not touched. M15's 157 tests pass unchanged, including the
eleven cross-family and §15A assertions, and Audit 0022 §17A's two-level trigger
is unaffected — M14's three rationale strings are still asserted byte-for-byte.

---

## 14. Files changed

| File | Change |
| --- | --- |
| `src/cover_kbc/specialists/null_temporal_specialist.py` | `_mine` calls `_locality_records`; that parser takes explicit provenance; `_probe_provenance` helper. **+47 / −34.** |
| `tests/test_null_temporal_specialist.py` | +30 tests (new Audit-0024 section). |
| `tests/test_atomic_consensus.py` | One test split into two (§12). |
| `docs/audits/0024-…md` | this file |

Nothing else changed. M12, M13, M15, M16, M2–M8, the model profile, all
configuration and `benchmark/` are untouched.

---

## 15. Tests

```
python -m pytest -q
1919 passed, 3 skipped in 14.73s
```

M14: **165** (was 135; +30). M16: **106** (was 105; one split into two).
M15: 157, unchanged. Total 1888 → 1919.

Covering the brief's 22 numbered requirements: Stage-B `NONE` and `UNKNOWN`
unchanged; mined `NONE` from each of the three producers; grammar-aware NULL
classification; mined `UNKNOWN` / "I don't know" / empty; mixed provenance;
repetition non-scaling for both sentinels; the literal-string invariant over
result *and* artefact; a real locality still parsing; explicit third-person
statement substantive; first-person hedge not; the single-parser AST check;
provenance/origin preservation; M16 integration both ways; M15 regression;
shadow invariance.

One fixture flaw of my own was caught and fixed while writing these: the
scripted runtime's default answer *is* the sentinel under test, so an unscripted
Module 11 probe silently became part of the fixture. `_mined` now scripts all
three probes explicitly and answers `UNKNOWN` where a case says nothing.

## 16. pyflakes

```
python -m pyflakes src/ tests/ scripts/
(clean)
```

## 17. Model-budget audit

```
python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
  total: 28.67B    RESULT: PASS
```

No model, checkpoint or parameter changed.

## 18. Benchmark integrity

```
git status --porcelain benchmark/     (empty)
git diff -- benchmark/                (empty)
git diff --cached -- benchmark/       (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact.

## 19. No TRAIN / VAL / TEST use

No split was read, no metric computed, no threshold introduced or tuned. Every
fixture is scripted and fictional. Production shadow invariance re-verified:
M14 on versus off leaves `predictions.jsonl`, `diagnostics.json`, `trace.jsonl`,
both stage files, both call ledgers, `metrics.json`, `query_profiles.jsonl`,
`prompt_programs.jsonl` and `parametric_memory.jsonl` **byte-identical**.

---

## 20. Verdict

**PASS.**

The defect was one call site that had forked away from its own parser before
§15A's correction was written, and it is fixed by deleting the fork rather than
by adding a second rule. `NONE`, `UNKNOWN` and every other abstention can no
longer become a locality candidate on any M14 path, and the persisted M14
artefact is clean at source rather than clean only after M16 filters it.

The grammar-aware NULL classification Audit 0021 established is unchanged and is
now reached on a surface that no longer contradicts it: `query_rewrite`'s `NONE`
is substantive because M10's contract defines it; every other `NONE` and every
`UNKNOWN` is failed recall; and neither scales into substantive evidence by
repetition.

M16's guard remains, and is now genuinely defence in depth rather than a
load-bearing patch over an upstream bug.

Not committed. Not pushed.
