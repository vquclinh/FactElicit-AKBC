# Audit 0084 - Profile-A Blind TEST Error-Signature Mining

Status: REVIEW ONLY - NO IMPLEMENTATION PERFORMED
Date: 2026-08-12
Source HEAD: `e9d2f42505dd2ee98f3e7ab7ad846340d607169a`

## 1. Scope / compliance

OBSERVED: This audit is a CPU-only forensic review of the current blind TEST
prediction artifact:

`outputs/submission-best/predictions.jsonl`

No TRAIN run, VAL run, fresh TEST inference, neural model call, web/RAG lookup,
external corpus, TEST gold, subject-answer table, fine-tuning, third model,
commit, or push was used.

The task created only this audit document:

`docs/audits/0084-profile-a-blind-test-error-signature-mining.md`

Evidence labels:

- OBSERVED: directly visible in repository source, configs, local artifacts, or
  blind prediction shape.
- CODEX-SUSPICION: semantic red-team suspicion from pretrained knowledge. This
  is not TEST gold and is used only to derive generic signatures.
- INFERRED: architecture/output-pattern reasoning without hidden labels.
- ALREADY-KNOWN: established by existing audits/artifacts.
- NEW-DISCOVERY: a current-profile finding not already captured, or materially
  different from Audit 0083 after Profile-A evidence.
- PROPOSED: design only; not implemented here.

CPU-only commands used included:

```bash
git rev-parse HEAD
git status --short
sha256sum outputs/submission-best/predictions.jsonl
wc -l outputs/submission-best/predictions.jsonl
find outputs -maxdepth 3 -type f
python - <<'PY'  # JSONL shape, sidecar joins, regex census; no repo writes
PY
git diff --check
```

## 2. Profile-A artifact provenance

OBSERVED:

| item | value |
|---|---:|
| artifact | `outputs/submission-best/predictions.jsonl` |
| prediction SHA256 | `227f65ffc3547e25c44f977218e858af25486e17bfa0825ad8202d268b82a3df` |
| rows | 475 |
| row order | canonical TEST order by local loader |
| canonical TEST rows | 475 |
| TEST object entities | all empty in canonical blind TEST |
| leaderboard-best profile | Profile A, per user-provided hidden TEST result |
| Profile A overall F1 | 0.4906 |
| `awardWonBy` F1 | 0.2929 |
| `companyTradesAtStockExchange` F1 | 0.7103 |
| `countryLandBordersCountry` F1 | 0.9264 |
| `hasArea` F1 | 0.3600 |
| `hasCapacity` F1 | 0.1224 |
| `personHasCityOfDeath` F1 | 0.4900 |

OBSERVED: `outputs/submission-best/` contains only `predictions.jsonl`.
There are no exact matching sidecars in that directory.

Closest compatible sidecar run:

`outputs/v3_test_16f60fb1_20260810T160048Z/run`

Sidecar provenance:

| item | value |
|---|---:|
| sidecar prediction SHA256 | `8a97a5e0696f0f4c77ace3af88725bb90f568ee0587bc07fe0f6e7b75c649f08` |
| source commit in sidecar provenance | `16f60fb1fa7c390ed0f0d0d741f9aa6f996d4da5` |
| same 475 subject/relation order as submission-best | true |
| exact row equality | 0 / 475 |
| object-list equality | 353 / 475 |

Relation-level object-list equality between `submission-best` and the sidecar
run:

| relation | equal rows |
|---|---:|
| `awardWonBy` | 0 / 10 |
| `companyTradesAtStockExchange` | 45 / 100 |
| `countryLandBordersCountry` | 66 / 67 |
| `hasArea` | 44 / 100 |
| `hasCapacity` | 98 / 98 |
| `personHasCityOfDeath` | 100 / 100 |

INFERRED: Sidecars are exact enough for capacity and city trace analysis, nearly
exact for borders, and useful but not exact for stock/area/award. They are not
treated as exact provenance for current Profile-A predictions unless the output
row matches.

## 3. Full 475-row forensic methodology

OBSERVED:

- Parsed all 475 prediction rows.
- Grouped every row by relation.
- Computed empty count, cardinality distribution, object count, duplicate
  outputs, repeated values, numeric roundness, and regex output-shape anomalies.
- Joined by `(SubjectEntity, Relation, row_index)` against compatible sidecars:
  `query_profiles.jsonl`, `prompt_programs.jsonl`, `parametric_memory.jsonl`,
  `numeric_specialist.jsonl`, `large_open_set_specialist.jsonl`,
  `null_temporal_specialist.jsonl`, `small_set_specialist.jsonl`,
  `atomic_consensus.jsonl`, `specialist_verification.jsonl`,
  `bidirectional_verification.jsonl`, `layer4_evidence.jsonl`,
  `coverage_gap.jsonl`, `relation_budget.jsonl`, `micro_planner.jsonl`,
  `v3_hypothesis_graph.jsonl`, `trace.jsonl`, and `calls.jsonl`.
- Inspected source implementation in `scripts/run_cover.py`,
  `src/cover_kbc/pipeline.py`, `src/cover_kbc/selection.py`,
  `src/cover_kbc/contracts/`, `src/cover_kbc/v3_core/`,
  `src/cover_kbc/v3_1/`, `src/cover_kbc/leaderboard_repair/`,
  `configs/experiments/`, `tests/`, `docs/audits/0082*`, and
  `docs/audits/0083*`.

Relation census for current `submission-best`:

| relation | rows | empty | objects | mean card | median | max | cardinality distribution |
|---|---:|---:|---:|---:|---:|---:|---|
| `awardWonBy` | 10 | 0 | 560 | 56.00 | 36 | 112 | `{7:1,26:2,32:1,36:2,85:1,98:1,102:1,112:1}` |
| `companyTradesAtStockExchange` | 100 | 46 | 101 | 1.01 | 1 | 28 | `{0:46,1:40,2:10,3:2,7:1,28:1}` |
| `countryLandBordersCountry` | 67 | 11 | 258 | 3.85 | 4 | 13 | `{0:11,1:9,2:5,3:4,4:11,5:8,6:7,7:5,8:3,9:1,10:2,13:1}` |
| `hasArea` | 100 | 15 | 85 | 0.85 | 1 | 1 | `{0:15,1:85}` |
| `hasCapacity` | 98 | 25 | 73 | 0.74 | 1 | 1 | `{0:25,1:73}` |
| `personHasCityOfDeath` | 100 | 98 | 2 | 0.02 | 0 | 1 | `{0:98,1:2}` |

NEW-DISCOVERY: Current Profile A is not the same shape as the older TEST file
used by Audit 0083. In particular `hasArea` is now 85% non-empty rather than
mostly empty. The area opportunity has shifted from broad empty rescue to
numeric attribute/cluster confidence.

## 4. Relation-by-relation error taxonomy

### `awardWonBy`

OBSERVED:

- All 10 rows are non-empty.
- Output is still structurally noisy: 560 objects total, max 112 in one row.
- Current artifact has no colon-prefix or NONE/UNKNOWN objects, so the most
  obvious metadata leak documented in Audit 0083 has already been reduced.
- Remaining structural anomalies:
  - parenthetical repeat/honorary metadata: 50 objects;
  - award role/team/committee terms: 47 objects, concentrated in `Aga Khan
    Award for Architecture`;
  - packed multi-recipient strings: 8 objects.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| role leakage | committee, jury, panel, review/team/chair/member strings | M13/LARGE_OPEN_SET over-acceptance | zero-call role-negative auditor plus witness verifier |
| repeated-recipient variants | `Name (second time)` style strings | final output repair did not fully canonicalize repeat metadata | deterministic normalizer |
| packed list object | comma/conjunction list inside one object | parser did not split a time/category bucket into recipients | deterministic packed-list splitter with provenance |
| open-set long tail | 85-112 objects on several awards | recall-first finalization and no witness pressure | chunked recipient witness |
| wrong role versus valid organization risk | organizations/projects may be valid, committees usually not | type-only deletion would be unsafe | role-specific, not organization-specific guard |

### `companyTradesAtStockExchange`

OBSERVED:

- 46/100 rows are empty.
- 40 rows are singleton; only 14 rows have cardinality >1.
- One row has 28 outputs and one has 7 outputs.
- Current deterministic `stock_wrong_type_reason` would flag 15 ticker-like or
  acronym-like objects and 6 city/bare-location-like objects, but hidden TEST
  showed the B stock repair harmed recall and overall score.
- Wide rows include regional exchange-list explosions such as a 28-object row.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| overwide exchange-list explosion | many exchanges from region/market family | broad recall/listing prompt over-expanded | high-cardinality stock auditor, not blanket guard |
| short venue ambiguity | `NASDAQ`, `NYSE`, `B3`, `Oslo Bors`, `Euronext Amsterdam` | ticker/exchange short-form ambiguity | type-only Qwen check if output would be changed |
| alias duplicate | `New York Stock Exchange` plus `NYSE` | alias canonicalization not applied in Profile A | conservative alias dedupe only when same-venue evidence is clear |
| empty despite listing evidence | empty final with M15/hgraph candidate signals | finalizer/gate/scope over-pruned or row failed in older run | graveyard verifier |
| parent/subsidiary/former listing | sidecar pending checks include parent/subsidiary/historical risks | relation identity/temporal confusion | listing-scope verifier only on routed rows |

PROPOSED constraint: Do not reintroduce Profile B's broad Stock repair stack.
Any future stock module must preserve Profile-A behavior unless a row has a
specific high-risk signature and the module can justify the mutation.

### `countryLandBordersCountry`

OBSERVED:

- 11 empty rows and 9 singleton rows.
- 9 reciprocity conflicts among rows where both endpoints are TEST subjects.
- Alias/rename duplicates appear in output, e.g. `Eswatini`/`Swaziland` and
  `North Macedonia`/`Republic of Macedonia`.
- Compatible sidecar has 17 support>=2 non-output border hypotheses across 9
  rows.
- Sidecar M15 contains many pending reverse/territory/maritime checks.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| singleton under-completeness | final set size <=1 while sidecar has multiple target-neighbour observations | M8 threshold/finalization loss | graveyard pair verifier |
| alias duplication | historical/current name pair | no final alias map for country aliases | deterministic alias dedupe plus exact-pair tests |
| reciprocity conflict | A predicts B but B does not predict A when B is a TEST subject | no cross-row constraint in Profile A | verified reciprocity, never blind union |
| maritime/bridge false positive risk | sidecar mentions bridge/maritime/non-integral dependency | broad recall hallucination | pairwise terrestrial-boundary verifier |
| island/no-border uncertainty | empty island rows plus conflicting `Q`/`A` parser artifacts | M15 parse noise | parser-noise filter before evidence reuse |

### `hasArea`

OBSERVED:

- 85/100 rows are non-empty, only 15 empty.
- 38/85 non-empty values have decimals.
- No repeated final area value appears.
- Empty rows are mostly other/qualified geographies: 4 island/small-geography
  rows, 7 other-geography rows, 4 qualified-name rows.
- Compatible M12 sidecar shows high numeric spread: 72 non-empty rows have
  multiple numeric observations; 69 have observed value ratios >=10.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| unit/scale conflict | sidecar values separated by 10x/100x/1000x | square miles/hectares/km2 parse ambiguity | numeric unit-hypothesis resolver |
| wrong entity area | qualified names, islands, lakes, countries | exact-entity identity drift | entity-type profiler and exact-entity verifier |
| lake/basin/catchment confusion | lake subjects with competing clusters | attribute ambiguity | lake surface-area resolver |
| singleton accepted despite conflict | final one value while M12 has competing observations | M8 cluster accepted without enough attribute labels | zero-call cluster confidence before recall |
| residual high despite non-empty | M19 residual >=0.75 on many non-empty rows | unresolved facets ignored late | failure-signature routing |

### `hasCapacity`

OBSERVED:

- 25/98 rows are empty.
- 73/73 non-empty rows are singleton integers.
- Repeated generic anchors: `10000` x8, `60000` x7, `30000` x7, `20000` x3,
  `45000` x3.
- 59/73 numeric outputs are multiples of 100; 51/73 are multiples of 1000;
  10/73 are powers of ten.
- Compatible M12 sidecar is exact for final capacity rows and shows:
  - 25 empty rows, 2 with numeric observations;
  - 27 non-empty rows with multiple numeric observations;
  - 3 non-empty rows with ratio >=10.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| generic round anchor | repeated 10000/30000/60000 across unrelated venues | model default/weak support | trigger only, not automatic reject |
| historical/current conflict | hgraph flags `HISTORICAL_CAPACITY` on competing candidates | temporal attribute ambiguity | capacity temporal resolver |
| zero/digit scale error | candidate ratios 10x or subject digit leakage | parser/attribute confusion | numeric ratio/digit auditor |
| venue identity confusion | subject has location qualifier or generic venue name | same-name venue substitution | exact venue+location verifier |
| empty with existing cluster | empty row has M12 cluster within sidecar | finalization/no-candidate mismatch | zero-call rescue only with support>=2 cluster |

### `personHasCityOfDeath`

OBSERVED:

- 98/100 rows are empty.
- Only `Bruce Chatwin -> Nicosia` and `Maeve Binchy -> Dublin` are non-empty.
- Compatible M14 sidecar is exact for current city output.
- M14 gate states: 83 `DECEASED_PLAUSIBLE`, 17 `UNRESOLVED`.
- 81 empty rows have `DECEASED_PLAUSIBLE`.
- Common M14 status patterns include `DECEASED` plus several `UNKNOWN`
  observations rather than two-model alive consensus.
- Some M14 locality observations are clearly not city-like by string shape:
  organization, country/adjectival, or birthplace/residence labels.

Failure families:

| family | signature | likely source | generic fix class |
|---|---|---|---|
| null over-abstention | empty final with `DECEASED_PLAUSIBLE` and target-city evidence | M8 NULL_SINGLE threshold/drop-on-unknown | M14 evidence reuse + Qwen contrast |
| locality type leakage | `Bell Labs`, country/adjective strings, residence/birthplace labels | M14 parser too permissive | locality semantic type filter |
| living-person protection | status evidence may be conflicted or living | false rescue risk | require no high-confidence ALIVE contradiction |
| exact-person identity risk | initials, non-ASCII names, qualifiers | same-name/biography collision | exact identity lock before recall |
| hospital/locality conversion | death record may name hospital rather than city | extraction ambiguity | verifier asks city-level death locality only |

## 5. CODEX-SUSPICION examples

These are not hidden TEST labels. They are red-team examples used only to
derive generic repair signatures.

| relation | subject | prediction shape | CODEX-SUSPICION | generic signature |
|---|---|---|---|---|
| stock | `Santander Group` | 28 exchanges across Latin America/Europe | likely regional exchange-list over-generation rather than exact listing set | high-cardinality stock set with many same-region venue names |
| stock | `Canadian National Railway` | `Toronto Stock Exchange`, `New York Stock Exchange`, `NYSE` | likely alias duplicate between full name and acronym | full-name/acronym same-venue pair |
| stock | `Groupon` | `NASDAQ` | bare acronym can be a legitimate exchange venue, not necessarily ticker | acronym shape alone must not delete |
| borders | `United Kingdom` | empty | sidecar says `Ireland`; possibly a recall/finalization miss | empty border row with target-neighbour observation |
| borders | `Sweden` | singleton `Norway` | sidecar mentions additional candidates including a bridge-only case | singleton plus directional/bridge contradiction |
| city | `George A. Romero` | empty | sidecar has repeated `Toronto` target-city evidence | empty city with strong M14 locality evidence |
| city | `Tony Tan Keng Yam` | empty | likely living-person protection may be correct despite M14 deceased-plausible noise | M14 status conflict requires alive/deceased resolver |
| area | `Samosir Island` | one decimal value with sidecar cluster conflict | possible exact island vs administrative/other area confusion | island subject plus competing area clusters |
| capacity | `Stade 1er Novembre 1954 in Algiers` | `115000` | subject contains year; capacity output may be record/scale/identity-confused | subject-digit and extreme capacity trigger |
| award | `Aga Khan Award for Architecture` | many committee/team strings | likely role leakage rather than recipient entities | award output contains committee/team/chair/member role terms |
| award | `Mark Twain Prize for American Humor` | many `(second time)` variants and `Mark Twain` | likely eponym/repeat metadata leakage | parenthetical repeat/eponym strings |

## 6. Common cross-relation error signatures

NEW-DISCOVERY signatures:

1. `high_residual_nonempty`: final output is non-empty, but M19 residual remains
   high. This appears in area, capacity, stock, city and awards. The final row
   can look complete while sidecars say important facets are weak.
2. `graveyard_support2`: support>=2 non-output hypotheses exist in the V3 graph.
   Counts from compatible sidecars: awards 109 candidates / 7 rows, stock 27 /
   22 rows, borders 17 / 9 rows, area 42 / 38 rows, capacity 25 / 19 rows, city
   9 / 9 rows.
3. `null_gate_disagrees_with_final_empty`: M14 says `DECEASED_PLAUSIBLE` while
   final city output is empty.
4. `numeric_ratio_spread`: existing numeric observations differ by 10x or more.
   This is frequent for area and present in capacity.
5. `role_or_metadata_object`: award output object has role, repeat, eponym, or
   packed-list shape.
6. `stock_high_cardinality_or_alias_pair`: stock output is either too wide or
   includes acronym/full-name duplicates.
7. `relation_specific_alias_pair`: border output contains renamed country pair
   or full/acronym venue pair.
8. `parser_label_noise`: compatible M15 sidecars contain `Q`/`A` as candidate
   observations hundreds of times; any evidence reuse must filter these first.
9. `M21_denied_by_token_cap`: many high-risk city/numeric actions were legal
   but denied by token cap, leaving an opportunity for a narrower late prompt.
10. `sidecar_not_exact`: `submission-best` lacks exact sidecars. The next run
    should persist L10 evidence inputs beside the prediction to avoid ambiguous
    attribution.

## 7. Backward trace findings

OBSERVED from actual code:

`scripts/run_cover.py` builds `CoverPipeline`, runs all queries, optionally
applies `LeaderboardRepairStack`, then writes `predictions.jsonl`, `trace.jsonl`
and sidecars. `LeaderboardRepairStack.apply()` currently sees predictions,
queries, and V3 hypothesis graphs. It does not receive raw M12/M13/M14/M15,
M16, Layer4, M19, M20, or M21 records.

Call flow near the end:

```text
M20/M21 production planning
-> V3 action loop
-> M8 relation-specific finalization
-> L7 relation repair, if enabled
-> L9 per-row guard
-> L8 consistency
-> L9 final guard
-> predictions.jsonl
```

Backward-trace conclusions by family:

| family | earliest observable point | loss point | classification |
|---|---|---|---|
| city empty with M14 locality | M14 `null_temporal_specialist.jsonl` status/locality evidence | not consumed by M8/L7 Profile A | evidence-reuse/finalization failure |
| area/capacity cluster conflict | M12 numeric observations and M16/V3 numeric hypotheses | M8 emits one cluster or abstains without later attribute resolver | attribute/finalization failure |
| award role leakage | M13 large-open-set observations and final output values | large-open-set finalizer emits role-like strings | output-semantic failure |
| stock empty or singleton with candidates | M15 and V3 non-output hypotheses | SMALL_SET finalizer/verification/gate suppresses candidates | recall+verification/finalization failure |
| border singleton with candidates | M15/V3 border hypotheses and M19 residual | final output loses supported neighbors | completeness/finalization failure |
| M21 token denial | `micro_planner.jsonl` denied actions | planned action never executes | routing/budget failure |

## 8. Evidence already available but unused

OBSERVED sidecar availability from closest compatible run:

| sidecar | rows | useful signal | exactness for current Profile A | current L7-L9 use | proposed use |
|---|---:|---|---|---|---|
| `query_profiles.jsonl` | 475 | identity, numeric, nullability, missingness risk | same query order, not output-dependent | not directly | Failure Signature Vector |
| `numeric_specialist.jsonl` | 198 | numeric observations, clusters, parse flags | exact for capacity, partial for area | not directly | numeric evidence reuse |
| `null_temporal_specialist.jsonl` | 100 | life status, locality, null evidence | exact for city outputs | not directly | city null-gate resolver |
| `small_set_specialist.jsonl` | 167 | stock/border candidates, near misses, pending checks | near-exact borders, partial stock | not directly | stock/border graveyard, after parser filter |
| `large_open_set_specialist.jsonl` | 10 | award facets, occurrences, near misses | not exact current award output | not directly | witness prioritization |
| `atomic_consensus.jsonl` | 436 | candidate states, numeric clusters, null state | compatible, not exact all rows | summarized through V3 graph only | richer graveyard scoring |
| `layer4_evidence.jsonl` | 436 | integrated candidates/propositions/numeric targets | compatible, not exact all rows | not directly | zero-call evidence scoring |
| `coverage_gap.jsonl` | 436 | residual, weak facets, numeric stability | compatible | not directly | routing trigger |
| `micro_planner.jsonl` | 306 | selected/denied actions, utility, stop reason | compatible | not directly | choose narrow late call |
| `v3_hypothesis_graph.jsonl` | 436 | support, status, contradictions | current stack sees only hgraph when available | partially | graveyard recovery |
| `trace.jsonl` | 475 | empty reasons, candidates, stop reasons | exact for city/capacity; partial others | prediction carries some of it in memory | router features |

NEW-DISCOVERY: Sidecar parser noise must be sanitized before reuse. In the M15
small-set sidecar, stock candidate observations include `Q` 298 times and `A`
176 times; border observations include `Q` 134 times and `A` 132 times. These
are prompt-format artifacts, not useful candidate entities.

## 9. Newly discovered signatures beyond Audit 0083

| priority | NEW-DISCOVERY | why it matters |
|---:|---|---|
| 1 | Profile-A `hasArea` is now mostly non-empty; empty rescue is no longer the main area signature | next work should target numeric confidence/attribute, not broad area recall |
| 2 | Capacity and city current outputs are byte-identical to the compatible sidecar run | M12/M14 sidecars are directly useful for these relations |
| 3 | 81 empty city rows have M14 `DECEASED_PLAUSIBLE` | the null gate is not simply preserving living people |
| 4 | M14 locality parser marks non-city strings as `TARGET_CITY` | city rescue needs locality type validation before adding |
| 5 | M15 has large `Q`/`A` parser noise | graveyard recovery needs a sidecar evidence sanitizer |
| 6 | Award colon/NONE leakage is gone in current Profile A, but role/repeat/packed strings remain | AwardMetadataNormalizer helped; next cleanup should be narrower |
| 7 | Stock B regression means high-cardinality stock rows and alias duplicates should be handled separately from broad wrong-type deletion | preserve Profile-A stock semantics |
| 8 | M21 token-cap denials dominate city/numeric late opportunities | a short post-pipeline verifier may recover what M21 could not afford |
| 9 | M19 residual stays high on many non-empty rows | non-empty is not a reliable completeness signal |
| 10 | `submission-best` has no exact sidecars | future profiles should persist repair/evidence inputs for attribution |

## 10. Failure Signature Vector proposal

PROPOSED:

```python
FailureSignature(
    subject: str,
    relation: str,
    row_index: int,
    sidecar_exactness: Literal["exact", "same_order_only", "missing"],
    final_empty: bool,
    final_cardinality: int,
    output_shape_flags: set[str],
    relation_shape_bucket: str,
    m19_residual: float | None,
    weak_facets: tuple[str, ...],
    candidate_count: int,
    supported_nonoutput_count: int,
    accepted_nonoutput_count: int,
    contradiction_kinds: Counter[str],
    verifier_label_counts: Counter[str],
    unknown_ratio: float | None,
    model_disagreement: bool,
    numeric_cluster_count: int,
    numeric_ratio_spread: float | None,
    numeric_unit_or_attribute_flags: set[str],
    null_gate_state: str,
    death_status_counts: Counter[str],
    target_locality_count: int,
    locality_type_anomaly_count: int,
    stock_acronym_or_short_venue_count: int,
    stock_high_cardinality: bool,
    award_role_noise_count: int,
    award_packed_object_count: int,
    award_repeat_metadata_count: int,
    border_reciprocity_conflict_count: int,
    parser_noise_count: int,
    m21_stop_reason: str,
    m21_denial_reasons: Counter[str],
)
```

Ranked oracle-like features without gold:

| feature | computed from | stage | zero-cost | target failure |
|---|---|---|---:|---|
| `final_empty && null_gate_state=DECEASED_PLAUSIBLE` | M14 + final | L10 | yes | city under-recall |
| `supported_nonoutput_count>=1` | hgraph/M16/trace | L10 | yes | graveyard recovery |
| `m19_residual>=0.75` | M19 | L10 | yes | incompleteness |
| `numeric_ratio_spread>=10` | M12 | L10 | yes | unit/scale/attribute error |
| `award_role_noise_count>0` | final/M13 | L10 | yes | award false positives |
| `award_packed_object_count>0` | final | L10 | yes | packed recipient parse |
| `stock_high_cardinality` | final | L10 | yes | stock over-generation |
| `stock_acronym_or_short_venue_count>0` | final | L10 | yes | risky stock deletion/preservation |
| `border_reciprocity_conflict_count>0` | batch final | L10/L11 | yes + verifier | border one-way error |
| `m21_denial_reason=DENIED_BY_TOKEN_CAP` | M21 | L10 | yes | narrow late-call routing |

## 11. Proposed routing architecture

PROPOSED architecture, downstream of current Profile-A/M8 or current L7-L9:

```text
M8 finalization
-> existing L7/L8/L9 if enabled by profile
-> L10 Failure Signature Router
   - computes relation-specific FailureSignature
   - consumes evidence bundle, not TEST labels
-> L11 Evidence Reuse Resolvers
   - zero-call deterministic repair from M12/M14/M15/M16/Layer4/M19/M21
-> L12 Targeted Second Opinion
   - only Mistral24/Qwen4
   - row selected by generic signature
   - relation-specific caps
-> L13 Conservative Final Guard
   - preserve official row order/schema
   - preserve Profile-A Stock unless targeted stock route explicitly proves change
-> predictions.jsonl + l10_l13_repair.jsonl + l10_l13_accounting.json
```

Branch points:

- For Profile A, L10 branches after M8 because L7-L9 are disabled.
- For A_PLUS_AWARD/C2/future profiles, L10 branches after current L9 so it can
  see all existing repair decisions and avoid undoing relation-specific guards.
- `run_cover.py` should pass an in-memory `EvidenceBundle` into the post-stack
  rather than forcing L10 to read sidecar files after the fact.

## 12. Exact proposed L10/L11/L12 modules

### `L10FailureSignatureRouter`

- RELATIONS: all.
- TRIGGER: every row, zero-call.
- INPUTS: final prediction, relation contract, hgraph, M12/M13/M14/M15, M16,
  Layer4, M19, M20/M21, trace candidates where in memory.
- DETECTION FEATURES: the Failure Signature Vector.
- OUTPUT ACTION: route to zero-call resolver, targeted verifier, recall branch,
  or keep.
- MAIN FP RISK: over-routing into high-call branches.
- MAIN FN RISK: too strict a signature misses weak rows.
- FEATURE FLAG: `leaderboard_repair.features.l10_failure_signature_router`.
- TEST WITHOUT GOLD: synthetic sidecar fixtures proving deterministic routing.

### `L10SidecarEvidenceSanitizer`

- RELATIONS: all sidecar-consuming branches.
- TRIGGER: evidence bundle contains candidate-like strings.
- INPUTS: specialist observations, consensus candidates, hgraph hypotheses.
- ZERO-CALL.
- ACTION: drop control labels, `Q`/`A`, empty strings, prompt scaffolding,
  subject-copy artifacts, and non-object strings before scoring.
- MAIN FP RISK: deleting a legitimate one-letter entity. Low for these six
  relations if tied to sidecar provenance, not final output.
- MAIN FN RISK: parser noise survives under a longer phrase.

### `L11CityNullEvidenceResolver`

- RELATIONS: `personHasCityOfDeath`.
- TRIGGER: final empty and M14 gate not confidently living.
- INPUTS: M14 status observations, gate state, locality observations, hgraph
  death-city hypotheses.
- ZERO-CALL first; Qwen if conflict.
- CALL BUDGET: 0-1 Qwen per triggered row, 2 only if exact-identity plus
  city-contrast are both required.
- QUESTION PURPOSE: exact person, deceased/living, city-level death locality,
  exclude birth/residence/burial/hospital unless city is explicit.
- ACTION: keep empty, add singleton city, or preserve unknown.
- FP RISK: adding residence/birthplace or wrong-identity city.
- FN RISK: preserving empty for obscure deceased persons.

### `L11NumericEvidenceReuseResolver`

- RELATIONS: `hasArea`, `hasCapacity`.
- TRIGGER: empty, high residual, numeric ratio spread >=10, repeated round
  anchor, subject digit leakage, or support>=2 non-output cluster.
- INPUTS: M12 observations/clusters, M16 numeric clusters, Layer4 numeric
  targets, final numeric value.
- ZERO-CALL when one cluster has >=2 independent support and no attribute
  contradiction; Qwen otherwise.
- CALL BUDGET: 0-1 Qwen per row.
- QUESTION PURPOSE: choose relation-compatible value and classify attribute:
  area total/surface/basin/wrong-entity; capacity current/historical/seated/
  standing/concert/attendance-record/wrong-venue.
- ACTION: keep, replace, add from empty, or abstain.
- FP RISK: replacing a correct final with a popular wrong cluster.
- FN RISK: no action when all clusters are weak.

### `L11AwardStructuralAuditor`

- RELATIONS: `awardWonBy`.
- TRIGGER: role terms, repeat parentheticals, eponym/subject copies, packed
  multi-recipient strings.
- INPUTS: final award outputs, M13 occurrences/facets.
- ZERO-CALL for normalization/splitting; optional Qwen witness for borderline
  role strings.
- CALL BUDGET: 0 deterministic; 1-3 Qwen chunks for G1/G2.
- ACTION: drop role-only strings, normalize repeats, split packed recipient
  objects, preserve organizations/projects unless role evidence says no.
- FP RISK: dropping a valid committee/project recipient.
- FN RISK: leaving field-famous non-recipients.

### `L11StockProfileAPreservingAuditor`

- RELATIONS: `companyTradesAtStockExchange`.
- TRIGGER: high cardinality >=5, alias/full-name duplicate, empty/singleton
  with support>=2 graveyard venue, or short/acronym ambiguity.
- INPUTS: final output, sanitized M15 observations, hgraph, current Stock repair
  decisions if any.
- ZERO-CALL for alias diagnostics; Qwen type/listing verifier only if mutation
  would occur.
- CALL BUDGET: 0-1 for G1, 2 for G2.
- QUESTION PURPOSE: first classify candidate as exchange venue, then only if
  venue survives ask whether exact company itself listed there.
- ACTION: keep Profile-A output by default; drop only high-card obvious
  non-listing list explosion; add only verified supported graveyard venue.
- FP RISK: reintroducing B-style over-deletion or adding false cross-listing.
- FN RISK: preserving Profile-A stock false positives.

### `L11BorderSafeCompletenessResolver`

- RELATIONS: `countryLandBordersCountry`.
- TRIGGER: reciprocity conflict, singleton/empty with support>=2 non-output
  border hypotheses, alias pairs, or high residual.
- INPUTS: final batch, sanitized M15, hgraph, M19.
- ZERO-CALL alias/reciprocity detection; Qwen pair verifier on mutations.
- CALL BUDGET: 0-1 per conflict/row, cap 2.
- QUESTION PURPOSE: actual terrestrial land boundary only; exclude maritime,
  bridge-only, nearby island, non-integral dependency.
- ACTION: add verified reciprocal edge, remove invalid direction, dedupe alias,
  or preserve unknown.
- FP RISK: hurting already strong border precision.
- FN RISK: not enough recall expansion.

### `L12UncertaintyTriggeredSecondOpinion`

- RELATIONS: all, with relation-specific prompts.
- TRIGGER: L10/L11 still marks row high risk after evidence reuse.
- MODEL: Qwen4 for verification/resolution; Mistral24 only for recall expansion
  when no candidate exists.
- CALL BUDGET: relation caps, e.g. Stock 1, Borders 1, Area 1, Capacity 1,
  City 2, Award 3-5 chunks.
- ACTION: bounded verifier/recall call.
- FP RISK: model overconfidence.
- FN RISK: router too conservative.

### `L13ConservativeFinalGuard`

- RELATIONS: all.
- TRIGGER: always after L10-L12.
- ZERO-CALL.
- ACTION: enforce official schema/order, singleton numeric/city, no subject-copy
  border, relation-specific Stock bypass, accounting sidecar, and no row loss.
- FP RISK: over-pruning if guard too broad.
- FN RISK: preserving suspicious values intentionally for Profile-A safety.

## 13. Zero-call fixes

PROPOSED zero-call modules ranked:

| priority | fix | relations | expected direction | risk |
|---:|---|---|---|---|
| 1 | `AwardRepeatAndRoleCleaner` | award | precision | medium if valid committee/project recipient exists |
| 2 | `AwardPackedRecipientSplitter` | award | precision+recall | low/medium; split only obvious lists |
| 3 | `EvidenceSanitizer` for `Q`/`A`/control sidecar noise | all evidence-reuse branches | precision | low |
| 4 | `FailureSignatureVector` and router sidecar | all | routing quality | low |
| 5 | `NumericClusterConfidenceScorer` | area/capacity | precision | medium if it replaces values; low if diagnostic-only |
| 6 | `CityM14StatusLocalityReader` diagnostic mode | city | recall routing | low if no output mutation |
| 7 | `BorderAliasDetector` | borders | precision | medium for disputed/alternate country names |
| 8 | `StockAliasDiagnostic` | stock | precision | medium; do not mutate in G0 |

## 14. Targeted model-call fixes

PROPOSED model-call modules:

| priority | call | model | relations | trigger | max calls | expected direction |
|---:|---|---|---|---|---:|---|
| 1 | death-city exact-person verifier | Qwen4 | city | empty + M14 deceased/locality evidence | 1-2 | recall with precision guard |
| 2 | numeric attribute resolver | Qwen4 | area/capacity | cluster conflict / anchor / digit leakage | 1 | precision |
| 3 | award witness chunks | Qwen4 | award | role/noisy/high-impact candidates | 3-5 | precision |
| 4 | border pair verifier | Qwen4 | borders | graveyard/reciprocity mutation | 1 | precision+recall |
| 5 | stock venue/listing verifier | Qwen4 | stock | high-card or graveyard candidate, not broad repair | 1-2 | precision+recall |
| 6 | missing-set recall | Mistral24 | award/border/city | no candidate evidence and high residual | 1 | recall |

No proposed module uses a third model.

## 15. Relation-specific branches

PROPOSED:

- Stock: Profile A is the base. Do not inherit B's Stock repair. Route only
  high-cardinality, alias-duplicate, or graveyard-support rows; use type-only
  then listing-scope checks.
- Borders: already strong. Use alias/reciprocity/graveyard with pairwise
  verification only; no automatic union.
- City: strongest separate branch. M14 evidence reuse first, exact-identity and
  locality-type verification second, recall only when evidence is insufficient.
- Area: focus on cluster/attribute/unit confidence, not broad empty rescue.
- Capacity: exact venue/location and current/historical/seated/record labels;
  repeated roundness is a trigger, not rejection.
- Award: deterministic structural cleanup first; witness chunks only after
  packed list splitting and role-negative filtering.

## 16. Ranked improvement opportunities

| priority | module | relations helped | trigger | input evidence | call type | expected benefit | main risk | complexity | profile |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | `CityNullEvidenceResolver` | city | empty + M14 deceased/locality | M14/hgraph | Qwen targeted | VERY HIGH | wrong locality/person | medium | G1/G2 |
| 2 | `AwardStructuralAuditor` | award | role/repeat/packed strings | final/M13 | zero + optional Qwen | HIGH | dropping valid project/org | low | G0/G1 |
| 3 | `NumericEvidenceReuseResolver` | area/capacity | cluster spread/anchor/empty | M12/M16/L4 | zero + Qwen | HIGH | wrong cluster replacement | medium | G1 |
| 4 | `EvidenceSanitizer` | all | sidecar reuse | sidecars | zero | HIGH | rare one-letter entity | low | G0 |
| 5 | `BorderSafeCompletenessResolver` | borders | reciprocity/support2 graveyard | final/M15/hgraph | Qwen pair | MEDIUM | border FP | medium | G1 |
| 6 | `StockProfileAPreservingAuditor` | stock | high-card/alias/graveyard only | final/M15/hgraph | Qwen targeted | MEDIUM | reintroduce B regression | medium | G1/G2 |
| 7 | `M21DeniedActionRerouter` | city/numeric | token-cap denial + high residual | M21/M19 | targeted call | MEDIUM | extra calls without gain | medium | G2 |
| 8 | `CompletenessAuditor` | award/border/stock | high residual set row | M19/current set | Mistral/Qwen | MEDIUM | recall FPs | high | G2 |
| 9 | `EntityIdentityLock` | city/capacity/stock/area | qualifier/generic name | subject/candidate | Qwen | MEDIUM | over-abstention | medium | G1/G2 |
| 10 | `TemporalStateResolver` | stock/capacity/city | current/historical conflict | hgraph/M14/M15 | Qwen | MEDIUM | temporal overconfidence | medium | G2 |

## 17. Proposed G0/G1/G2 leaderboard profiles

Do not implement these until the user requests it.

### PROFILE G0 - zero-call structural cleanup

Base: Profile A or A_PLUS_AWARD, depending on whether the user has submitted
A_PLUS_AWARD.

Modules:

- `L10FailureSignatureRouter`
- `L10SidecarEvidenceSanitizer`
- `AwardRepeatAndRoleCleaner`
- `AwardPackedRecipientSplitter`
- diagnostic-only `NumericClusterConfidenceScorer`
- diagnostic-only `CityM14StatusLocalityReader`
- no Stock semantic mutation

Maximum additional calls: 0.

Expected direction: award precision; better routing telemetry for later.

Risk: low to medium. Main risk is over-dropping valid award organizations if
role rules are too broad.

Implementation complexity: low.

### PROFILE G1 - evidence reuse plus targeted verification

Base: Profile A plus G0.

Modules:

- `CityNullEvidenceResolver`
- `NumericEvidenceReuseResolver`
- `BorderSafeCompletenessResolver`
- `StockProfileAPreservingAuditor` only for high-card/alias/graveyard rows
- chunked `AwardRecipientWitness` after structural cleanup

Maximum additional calls:

| relation | cap |
|---|---:|
| Stock | 1 |
| Borders | 1 |
| Area | 1 |
| Capacity | 1 |
| City | 2 |
| Award | 3 |

Expected direction: both precision and recall, strongest for city, award and
numeric relations.

Risk: medium. Main risk is adding city false positives or reintroducing stock
regression.

Implementation complexity: medium.

### PROFILE G2 - aggressive routed second opinion

Base: best measured result among A, A_PLUS_AWARD, C2, G1.

Modules:

- all G1 modules;
- `M21DeniedActionRerouter`;
- `CompletenessAuditor` for awards/borders/stock;
- relation-specific recall expansion only after L10/L11 says evidence is
  insufficient;
- `TemporalStateResolver`;
- exact-identity verifier for high-risk city/capacity/stock/area rows.

Maximum additional calls:

| relation | cap |
|---|---:|
| Stock | 2 |
| Borders | 2 |
| Area | 2 |
| Capacity | 2 |
| City | 4 |
| Award | 6 |

Expected direction: recall-oriented with precision guards.

Risk: high. Submit only after G0/G1 or leaderboard feedback justifies extra
recall pressure.

Implementation complexity: medium/high.

Recommended submit order after Profile A:

1. A_PLUS_AWARD if not already submitted.
2. G0 if deterministic award cleanup is acceptable.
3. G1 as the first serious score probe.
4. G2 only if recall remains the obvious deficit.

## 18. Risks / likely regressions

- Stock regression: broad stock deletion already harmed hidden TEST in Profile
  B. Future Stock mutation must be opt-in, routed, and audited.
- Border regression: border F1 is already high; unverified completeness repair
  can hurt more than help.
- City false positives: M14 has parser/type noise, so adding city names without
  exact-person and locality-type checks can damage precision.
- Numeric replacement: a high-support cluster can still be the wrong attribute
  or wrong unit.
- Award over-cleaning: organizations/projects can be valid award recipients;
  role-negative rules must target role strings, not entity type.
- Provenance mismatch: current `submission-best` lacks exact sidecars, so this
  audit's sidecar-backed findings must be revalidated in a run that persists
  the L10 evidence bundle.

## 19. Recommended next implementation

PROPOSED first implementation:

Implement G0/G1 infrastructure in one isolated post-pipeline stack, but enable
only the safest G0 profile first:

1. Add `FailureSignatureVector` and `EvidenceBundle`.
2. Wire `run_cover.py` to pass in-memory M12/M13/M14/M15/M16/Layer4/M19/M21
   evidence into the post-stack.
3. Add `EvidenceSanitizer`.
4. Add `AwardStructuralAuditor` zero-call mode.
5. Add diagnostic-only vector sidecar for all rows.
6. Keep Stock output byte-identical unless a later profile explicitly enables
   `StockProfileAPreservingAuditor`.

Reasoning:

- It addresses the currently observed award structural weakness without neural
  calls.
- It produces the missing exact sidecar needed for reliable future forensic
  attribution.
- It avoids the known Stock regression path.
- It creates the common router needed by the higher-value city/numeric G1
  modules.

## 20. Exact files that would need modification

PROPOSED implementation touch list:

- `src/cover_kbc/leaderboard_repair/config.py`
  - add L10/L11/L12/L13 feature flags and caps.
- `src/cover_kbc/leaderboard_repair/stack.py`
  - accept `EvidenceBundle`, call router/resolvers after existing L9.
- `src/cover_kbc/leaderboard_repair/types.py`
  - add `FailureSignature`, evidence provenance and L10 record types.
- `src/cover_kbc/leaderboard_repair/evidence_bundle.py`
  - new in-memory bundle over M12/M13/M14/M15/M16/Layer4/M19/M21/trace.
- `src/cover_kbc/leaderboard_repair/failure_signature.py`
  - new router/vector computation.
- `src/cover_kbc/leaderboard_repair/semantic_auditor.py`
  - new output-shape and evidence sanitizer logic.
- `src/cover_kbc/leaderboard_repair/award_auditor.py`
  - new structural award cleanup.
- `src/cover_kbc/leaderboard_repair/city_resolver.py`
  - new M14 evidence reuse and targeted city verifier.
- `src/cover_kbc/leaderboard_repair/numeric_resolver.py`
  - new M12/M16/Layer4 numeric resolver.
- `src/cover_kbc/leaderboard_repair/stock_auditor.py`
  - new Profile-A-preserving stock route, if enabled later.
- `src/cover_kbc/leaderboard_repair/border_auditor.py`
  - new conservative border route, if enabled later.
- `scripts/run_cover.py`
  - pass in-memory sidecar results into repair stack; write L10-L13 sidecars.
- `configs/experiments/`
  - add future G0/G1/G2 profiles only after implementation request.
- `tests/test_leaderboard_repair.py`
  - add unit and integration tests.
- `tests/test_package_submission.py` and/or `tests/test_run_cover*.py`
  - assert official schema/order and sidecar separation.

Tests that should be added:

- L10 disabled is byte-identical to Profile A.
- No web/RAG/TEST-gold/subject-answer-map imports or literals.
- Evidence sanitizer drops `Q`/`A` sidecar noise but not final legitimate
  acronym outputs.
- Award structural cleaner is idempotent and preserves valid organization-like
  recipients unless role evidence exists.
- Packed award lists split only under strict structural conditions.
- City resolver preserves empty for confident living evidence.
- City resolver never adds country/organization/residence/birthplace strings as
  city outputs.
- Numeric resolver never hard-codes subject values and only uses clusters from
  supplied evidence.
- Stock output remains byte-identical under G0 and under C2 Stock-bypass flags.
- Stock auditor cannot run broad B-style deletion unless explicitly enabled.
- Border reciprocity does not blindly union.
- Repair exceptions cannot reduce prediction rows below 475.
- L10-L13 accounting is separate from M20/M21 and existing repair accounting.

Final recommendation:

Build the common L10 evidence/router layer first, then submit a low-risk G0.
Move to G1 only after exact L10 sidecars show which city/numeric rows have
decisive already-paid evidence.

## 21. Mandatory 475-Row Semantic Review

OBSERVED follow-up scope:

- Artifact reviewed row by row:
  `outputs/submission-best/predictions.jsonl`
- Profile-A prediction SHA256:
  `227f65ffc3547e25c44f977218e858af25486e17bfa0825ad8202d268b82a3df`
- Temporary row ledger:
  `/tmp/profile_a_475_row_semantic_review.jsonl`
- Ledger status:
  temporary analysis artifact only; not added to the repository.
- Sidecar provenance:
  `outputs/submission-best` still contains only `predictions.jsonl`. The
  closest compatible Profile-A sidecar run remains
  `outputs/v3_test_16f60fb1_20260810T160048Z/run`.

OBSERVED methodology:

Every prediction row was read in canonical file order from row `0` through row
`474`. For each row, the pass inspected the subject, relation, final object
list, cardinality, empty state, object type, relation compatibility, identity
ambiguity, temporal ambiguity, completeness risk, over-generation risk,
under-generation risk, alias risk, malformed or packed-output risk and
sidecar-backed evidence where available. For multi-object rows, every emitted
object string received an object-level review entry in the temporary ledger.
For empty rows, the ledger records the most likely generic null mode rather
than treating all empties as the same failure.

Hard accounting:

| Check | Result |
| --- | ---: |
| Ledger rows | 475 |
| Unique `row_index` values | 475 |
| Minimum `row_index` | 0 |
| Maximum `row_index` | 474 |
| Missing row indices | `[]` |
| Duplicate row indices | `[]` |
| `reviewed=true` | 475 |
| `semantic_status` populated | 475 |
| `generalizable_signature` populated | 475 |
| Emitted `ObjectEntities` inspected | 1,079 |
| Object-level review records | 1,079 |

Relation accounting:

| Relation | Rows |
| --- | ---: |
| `awardWonBy` | 10 |
| `companyTradesAtStockExchange` | 100 |
| `countryLandBordersCountry` | 67 |
| `hasArea` | 100 |
| `hasCapacity` | 98 |
| `personHasCityOfDeath` | 100 |

Semantic status counts:

| Status | Rows |
| --- | ---: |
| `NO_OBVIOUS_SUSPICION` | 155 |
| `UNCERTAIN` | 45 |
| `SUSPICIOUS` | 191 |
| `HIGHLY_SUSPICIOUS` | 84 |

Per-relation status counts:

| Relation | No Obvious | Uncertain | Suspicious | Highly Suspicious |
| --- | ---: | ---: | ---: | ---: |
| `awardWonBy` | 0 | 0 | 4 | 6 |
| `companyTradesAtStockExchange` | 78 | 12 | 7 | 3 |
| `countryLandBordersCountry` | 50 | 0 | 9 | 8 |
| `hasArea` | 8 | 0 | 68 | 24 |
| `hasCapacity` | 18 | 0 | 46 | 34 |
| `personHasCityOfDeath` | 1 | 33 | 57 | 9 |

Sidecar exactness:

| Sidecar class | Rows |
| --- | ---: |
| Compatible Profile-A sidecars exact for row output | 353 |
| Compatible same-query-order sidecars, output differs | 122 |

Exact sidecar alignment by relation:

| Relation | Exact output match | Same order, output differs |
| --- | ---: | ---: |
| `awardWonBy` | 0 | 10 |
| `companyTradesAtStockExchange` | 45 | 55 |
| `countryLandBordersCountry` | 66 | 1 |
| `hasArea` | 44 | 56 |
| `hasCapacity` | 98 | 0 |
| `personHasCityOfDeath` | 100 | 0 |

Because the sidecars are not exact for all relations, the ledger never treats an
older sidecar as hidden TEST evidence. It marks sidecar exactness per row and
uses non-exact sidecars only as compatible architecture/evidence-shape context.

### 21.1 Row-By-Row Failure-Family Counts

OBSERVED row-level flags, after tightening stock and border to avoid broad
Profile-B-style mutation:

| Failure signature | Rows | Example rows |
| --- | ---: | --- |
| `NUMERIC_SCALE_SPREAD` | 73 | 0, 1, 2, 3, 4, 5, 6, 7 |
| `AREA_ENTITY_TYPE_DEFINITION_RISK` | 44 | 1, 2, 4, 6, 8, 9, 11, 12 |
| `CITY_DECEASED_PLAUSIBLE_EMPTY_NO_LOCALITY` | 33 | 375, 376, 379, 382, 385, 392 |
| `NUMERIC_COMPETING_CLUSTER` | 29 | 2, 5, 11, 17, 20, 32 |
| `CAPACITY_GENERIC_ANCHOR_WEAK_SUPPORT` | 26 | 103, 104, 106, 107, 109, 113 |
| `CITY_POSSIBLY_LEGITIMATE_LIVING_NULL` | 25 | 380, 387, 388, 393, 398, 399 |
| `CAPACITY_FINAL_EMPTY` | 25 | 108, 114, 117, 118, 119, 132 |
| `CITY_EMPTY_SINGLE_LOCALITY_SUPPRESSED` | 24 | 378, 381, 383, 384, 389, 390 |
| `NUMERIC_FINAL_CONFLICTS_WITH_STRONGER_CLUSTER` | 24 | 2, 4, 25, 26, 27, 28 |
| `CAPACITY_EMPTY_NO_EVIDENCE` | 23 | 108, 114, 117, 118, 119, 132 |
| `CAPACITY_HISTORICAL_OR_ATTRIBUTE_AMBIGUITY` | 18 | 100, 105, 110, 111, 112, 129 |
| `AREA_FINAL_EMPTY` | 15 | 14, 18, 22, 23, 38, 43 |
| `STOCK_SHORT_ACRONYM_TYPE_AMBIGUITY` | 12 | 218, 229, 236, 250, 265, 266 |
| `AWARD_ALIAS_OR_REPEAT_VARIANTS` | 10 | 198-207 |
| `AWARD_BROAD_FIELD_OVERGENERATION` | 9 | 198-206 |
| `CITY_EMPTY_SUPPORTED_LOCALITY_GRAVEYARD` | 8 | 408, 411, 419, 423, 440, 450 |
| `BORDER_COMPLETENESS_RISK` | 8 | 308, 312, 320, 325, 332, 334 |
| `CAPACITY_STRONG_NONOUTPUT_CLUSTER` | 8 | 107, 121, 122, 126, 158, 173 |
| `STOCK_PARENT_SUBSIDIARY_OR_DIRECT_LISTING_RISK` | 7 | 219, 222, 225, 240, 256, 289 |
| `BORDER_TERRITORY_OR_DEPENDENCY_OBJECT` | 5 | 324, 328, 340, 347, 371 |
| `AWARD_EPONYM_OR_AWARD_TITLE` | 4 | 198, 200, 205, 206 |
| `AWARD_TRUNCATED_FRAGMENT` | 4 | 198, 202, 203, 205 |
| `BORDER_SINGLETON_OR_LOW_CARDINALITY_COMPLETENESS_RISK` | 4 | 308, 312, 332, 370 |
| `AWARD_PACKED_LIST` | 3 | 199, 200, 205 |
| `BORDER_ALIAS_DUPLICATE` | 2 | 310, 311 |
| `BORDER_MARITIME_OR_CAUSEWAY_RISK` | 2 | 322, 328 |
| `CAPACITY_EMPTY_WITH_SUPPORTED_CLUSTER` | 2 | 133, 174 |
| `STOCK_EXTREME_OVERCROSSLISTING` | 2 | 242, 254 |

Per-relation suspicious-row counts, counting only `SUSPICIOUS` and
`HIGHLY_SUSPICIOUS`:

| Relation | Suspicious Rows |
| --- | ---: |
| `awardWonBy` | 10 |
| `companyTradesAtStockExchange` | 10 |
| `countryLandBordersCountry` | 17 |
| `hasArea` | 92 |
| `hasCapacity` | 80 |
| `personHasCityOfDeath` | 66 |

### 21.2 Object-Level Multi-Object Findings

OBSERVED object-level flags:

| Object-level flag | Objects |
| --- | ---: |
| `NUMERIC_SCALE_SPREAD` | 72 |
| `CAPACITY_EXACT_VENUE_IDENTITY_REQUIRED` | 59 |
| `AWARD_EPONYM_OR_AWARD_TITLE` | 56 |
| `AWARD_REPEAT_OR_METADATA_MARKER` | 51 |
| `AWARD_ROLE_LEAKAGE` | 47 |
| `AREA_ENTITY_TYPE_DEFINITION_RISK` | 44 |
| `CAPACITY_ROUND_ANCHOR` | 39 |
| `AREA_DECIMAL_SINGLETON` | 38 |
| `NUMERIC_FINAL_CONFLICTS_WITH_STRONGER_CLUSTER` | 32 |
| `NUMERIC_COMPETING_CLUSTER` | 28 |
| `AWARD_ORGANIZATION_PROJECT_OR_INSTITUTION` | 24 |
| `STOCK_SHORT_ACRONYM_REQUIRES_TYPE_CHECK` | 17 |
| `AWARD_TRUNCATED_FRAGMENT` | 13 |
| `AWARD_PACKED_MULTIPLE_RECIPIENTS` | 5 |
| `BORDER_TERRITORY_OR_DEPENDENCY_OBJECT` | 5 |
| `BORDER_ALIAS_DUPLICATE` | 4 |
| `BORDER_HISTORICAL_OR_ALIAS_NAME` | 3 |
| `STOCK_ALIAS_DUPLICATE` | 2 |
| `BORDER_MARITIME_OR_CAUSEWAY_RISK` | 2 |
| `AWARD_METADATA_PREFIX` | 1 |
| `STOCK_MARKET_SEGMENT_OR_INDEX_OBJECT` | 1 |

NEW-DISCOVERY from multi-object review:

- `awardWonBy` is not just high-cardinality. Row 205 contains a dense block of
  process-role strings for the exact award, including committee, team, jury,
  panel and technical review roles. This is a stronger structural target than a
  generic cardinality cap.
- Row 206 contains many repeat markers such as second/third/fourth-time style
  variants. These are alias/metadata variants, not independent recipient
  objects.
- Rows 199 and 200 contain packed recipient strings that should be split or
  dropped by a strict packed-list detector before recipient witness calls.
- Rows 198, 200, 205 and 206 contain eponym/title leakage. A recipient witness
  layer should ask whether the candidate received the exact award, not whether
  the candidate is associated with the award name or field.
- Stock rows with multiple outputs are mostly plausible Profile-A listings.
  The safe stock exceptions are narrow: row 247 alias duplication, rows 242 and
  254 extreme overcrosslisting, and row 254 market-segment leakage.
- Border multi-object rows mostly look stable. The safe late-layer work is
  limited to alias dedupe, dependency/territory classification and maritime or
  causeway exclusion.

### 21.3 Empty-Row Findings

OBSERVED empty rows:

| Relation | Empty Rows | Review result |
| --- | ---: | --- |
| `companyTradesAtStockExchange` | 46 | All 46 were left as no-obvious-suspicion after tightening; Profile-A stock should not be broadly rescued. |
| `countryLandBordersCountry` | 11 | 10 looked like safe island/no-land-neighbour nulls; only United Kingdom was flagged as a relation-semantics ambiguity. |
| `hasArea` | 15 | All 15 remain suspicious; two have M12 numeric evidence despite final empty. |
| `hasCapacity` | 25 | 23 have no usable sidecar evidence; two have supported numeric clusters and are high-value evidence-reuse targets. |
| `personHasCityOfDeath` | 98 | Split into five materially different null modes, not one aggregate failure. |

City empty split:

| City empty subtype | Rows |
| --- | ---: |
| Supported target-locality graveyard | 8 |
| Single/weak target-locality suppressed | 24 |
| Deceased-plausible but no locality | 33 |
| Possibly legitimate living/null conflict | 25 |
| Unresolved life status | 8 |

NEW-DISCOVERY from empty-row review:

- The previous "98/100 city empties" statement was directionally useful but
  too coarse. Only eight rows expose strong already-paid locality evidence in
  the compatible M14/hypothesis sidecars. Another 24 have weaker one-view
  locality evidence. The rest need life-status preservation or recall, not
  blind fill-in.
- Several city rows have `DECEASED_PLAUSIBLE` status but no locality. Those are
  recall failures, not finalizer failures.
- Some city rows have living/null conflict under CODEX-SUSPICION. A city rescue
  layer must first preserve `[]` under confident living evidence before adding
  recall.
- Capacity empties are not uniform. Most are true no-evidence failures, but
  rows 133 and 174 have supported numeric clusters in the compatible sidecars
  and should be handled by evidence reuse before another recall call.
- Border empty rows are mostly examples where no repair is safer. This pushes
  border work lower in the leaderboard priority unless a conflict is explicit.

### 21.4 CODEX-SUSPICION Examples

These examples are subject-specific red-team observations used only to derive
generic detectors. They are not hidden TEST labels and do not assert corrected
answers.

- CODEX-SUSPICION: row 205, `Aga Khan Award for Architecture`, emits many
  committee/jury/team strings. Generic signature:
  `AWARD_ROLE_LEAKAGE + AWARD_BROAD_FIELD_OVERGENERATION`. Route:
  zero-call structural role auditor, then recipient witness only for survivors.
- CODEX-SUSPICION: row 206, `Mark Twain Prize for American Humor`, emits many
  repeat-marker variants. Generic signature:
  `AWARD_REPEAT_MARKER + AWARD_ALIAS_OR_REPEAT_VARIANTS`. Route:
  deterministic metadata stripping and idempotent alias dedupe.
- CODEX-SUSPICION: row 242, `Santander Group`, emits 28 venues. Generic
  signature: `STOCK_EXTREME_OVERCROSSLISTING`. Route: targeted completeness or
  direct-company listing verifier only for extreme sets; no blanket stock
  guard.
- CODEX-SUSPICION: row 247, `Canadian National Railway`, emits both `NYSE` and
  `New York Stock Exchange`. Generic signature: `STOCK_ALIAS_DUPLICATE`. Route:
  exact exchange alias dedupe.
- CODEX-SUSPICION: row 310, `Mozambique`, emits both `Eswatini` and
  `Swaziland`. Generic signature: `BORDER_ALIAS_DUPLICATE`. Route: country
  alias canonicalization.
- CODEX-SUSPICION: row 322, `Bahrain`, emits `Saudi Arabia`. Generic
  signature: `BORDER_MARITIME_OR_CAUSEWAY_RISK`. Route: terrestrial-only
  verifier for bridge/causeway cases.
- CODEX-SUSPICION: row 408, `George A. Romero`, final output is empty while
  compatible M14/hypothesis evidence contains a supported target locality.
  Generic signature: `CITY_EMPTY_SUPPORTED_LOCALITY_GRAVEYARD`. Route:
  singleton city contrast verifier.
- CODEX-SUSPICION: row 407, `Bruce Chatwin`, final output has one locality
  while compatible evidence contains multiple competing localities, including a
  stronger non-output cluster. Generic signature:
  `CITY_FINAL_CHOSE_SINGLE_SUPPORT_OVER_STRONGER_COMPETING_LOCALITY`. Route:
  contradiction resolver before final singleton selection.
- CODEX-SUSPICION: row 107, `Baenao in Belem`, final capacity is a generic
  round anchor while M12 has stronger non-output clusters. Generic signature:
  `CAPACITY_GENERIC_ANCHOR_WEAK_SUPPORT + CAPACITY_STRONG_NONOUTPUT_CLUSTER`.
  Route: exact-venue numeric resolver.
- CODEX-SUSPICION: row 27, `Hispaniola`, final area conflicts with a stronger
  M12 cluster. Generic signature:
  `AREA_ENTITY_TYPE_DEFINITION_RISK + NUMERIC_FINAL_CONFLICTS_WITH_STRONGER_CLUSTER`.
  Route: area entity-type profiler plus cluster resolver.

### 21.5 What Changed From The Earlier Audit-0084 Conclusions

Survived:

- Profile A remains the Stock base. Broad Stock deletion/rescue is still
  unsafe after hidden leaderboard evidence.
- Award structural cleanup remains valuable. The row pass strengthened this
  conclusion by showing exact role, repeat-marker, packed-list and eponym
  patterns inside the emitted object strings.
- City null-gate behavior is a real recall risk.
- Numeric relations need late evidence reuse and attribute/identity resolution.
- Border repair should stay conservative because the relation is already
  strong and many apparent nulls are safer left untouched.

Too coarse:

- "98 city empties" was too coarse. The row pass splits those empties into
  supported graveyard, weak locality, deceased-with-no-locality,
  possible-living/null and unresolved-life-status cases.
- "Award high cardinality" was too coarse. The object pass reveals multiple
  distinct structural subtypes that need different actions.
- "Numeric disagreement" was too coarse. Area and Capacity need separate
  branches: area is dominated by entity-type/definition and unit/scale issues;
  capacity is dominated by exact-venue identity, historical/current attribute
  ambiguity and weak generic anchors.
- "Border completeness" was too coarse. Several border rows are examples where
  no repair is safer.

Wrong or misleading if left unqualified:

- A broad Stock semantic guard would likely repeat the Profile-B regression.
  Only exact alias dedupe, extreme-set routing and narrow exchange-type
  verification are currently safe enough to consider.
- A global round-number rule for capacity is unsafe. Roundness is only useful
  when combined with weak support, competing clusters or exact-venue identity
  risk.
- A generic border union/completeness pass is unsafe. Pairwise verification is
  required for dependency, maritime, causeway or reciprocity cases.

Ranking changes:

- CityNullEvidenceResolver should not be a blanket priority-1 recall module.
  It remains the strongest targeted model-call module for City rows with
  supported or weak locality evidence, but it must begin with a living/null
  preservation gate.
- AwardStructuralAuditor remains the top Award module and the safest
  zero-call output cleaner, but the row pass promotes split numeric evidence
  reuse ahead of broad award witness calls for the next non-zero-call profile.
- Numeric modules must branch separately for `hasArea` and `hasCapacity`.
  Sharing parsing and cluster utilities is fine; sharing final decision policy
  is not.
- Stock has only narrow safe signatures: exact alias duplication, extreme set
  size and type ambiguity for short acronyms or market-segment strings.
- Border has several rows where no repair is safer, especially island/null
  cases and plausible high-cardinality country sets.

### 21.6 Revised Module Ranking

PROPOSED revised ranking after the mandatory row pass:

| Rank | Module | Relations | Basis | Call cost | Risk |
| ---: | --- | --- | --- | --- | --- |
| 1 | `FailureSignatureVector` + exact evidence bundle | all | Required to avoid relying on compatible-but-not-exact sidecars | zero-call | low |
| 2 | `CapacityExactVenueClusterResolver` | `hasCapacity` | 80 suspicious rows; weak round anchors and stronger non-output clusters | zero/evidence reuse, optional verifier | medium |
| 3 | `AreaEntityTypeClusterResolver` | `hasArea` | 92 suspicious rows; entity-type and stronger-cluster conflicts | zero/evidence reuse, optional verifier | medium |
| 4 | `CityNullEvidenceResolver` with living gate | `personHasCityOfDeath` | 8 supported locality graveyards, 24 weak localities, 25 possible living/nulls | targeted Qwen verifier/recall | medium |
| 5 | `AwardStructuralAuditorV2` | `awardWonBy` | all 10 award rows structurally suspicious; 1,079 total objects include 560 award objects | zero-call | low/medium |
| 6 | `AwardRecipientWitnessBatcher` | `awardWonBy` | needed after structural cleanup for organization/project/person survivors | bounded Qwen verifier | medium |
| 7 | `BorderConservativeConflictAuditor` | `countryLandBordersCountry` | only 17 suspicious rows; many no-repair rows | zero-call plus rare verifier | low |
| 8 | `StockProfileAPreservingAuditor` | `companyTradesAtStockExchange` | only 10 suspicious rows after tightening | zero-call plus rare verifier | medium |

Recommended first implementation after this review:

Implement the exact `FailureSignatureVector`/`EvidenceBundle` first, with
diagnostic sidecars. The first score-affecting branch should be split numeric
evidence reuse for Capacity and Area, plus the already-supported Award metadata
cleanup. City should be enabled only through the living-gated supported-locality
route, not as blanket empty rescue.

### 21.7 Revised L10/L11/L12 Routing Architecture

PROPOSED:

```text
Profile-A final output
  -> L10 FailureSignatureVector
       zero-call row features:
       final_empty, cardinality, object type flags, numeric cluster spread,
       stronger_nonoutput_cluster, supported_graveyard_candidate,
       life_status_conflict, exact_identity_risk, alias_duplication,
       sidecar_exactness
  -> L11 EvidenceReuseResolver
       Area: entity type + M12 cluster voting
       Capacity: exact venue + M12 cluster/attribute voting
       City: supported-locality graveyard recovery only after living gate
       Award: structural cleanup and packed-list handling
       Stock: exact alias dedupe only when enabled
       Border: alias/dependency/maritime checks only when enabled
  -> L12 Targeted Second Opinion
       Qwen-only bounded verifier/contrast calls for rows selected by L10/L11
       Mistral24 remains available for independent recall only in higher-risk
       profiles
  -> L13 Conservative Relation Guard
       relation-specific final schema/order/cardinality guard
       never allows repair exceptions to reduce row count
```

Failure Signature Vector fields to implement:

- `sidecar_exactness`
- `final_empty`
- `final_cardinality`
- `object_type_flags`
- `candidate_mass`
- `strong_nonoutput_count`
- `supported_graveyard_count`
- `numeric_cluster_count`
- `numeric_cluster_spread`
- `numeric_final_vs_best_cluster_delta`
- `life_status_gate`
- `life_status_conflict`
- `target_locality_support`
- `exact_identity_risk`
- `temporal_attribute_risk`
- `alias_duplicate_risk`
- `packed_output_risk`
- `role_leakage_risk`
- `remaining_or_denied_budget`

### 21.8 Proposed G0/G1/G2 Adjustment

PROPOSED next profiles after A/A_PLUS_AWARD/C2 feedback:

- `G0_ZERO_CALL_AUDITOR`:
  Profile A plus Award structural cleanup, exact alias dedupe only where
  relation-safe, numeric evidence diagnostics and no broad Stock mutation.
- `G1_EVIDENCE_REUSE_NUMERIC_CITY`:
  G0 plus Area/Capacity cluster resolvers and City supported-locality resolver
  behind a living/null preservation gate.
- `G2_TARGETED_SECOND_OPINION`:
  G1 plus bounded Qwen verification for numeric attribute conflicts, city weak
  locality cases, award recipient witnesses, extreme Stock sets and border
  dependency/maritime cases.

Do not inherit Profile-B Stock repair into any G profile. Stock remains
Profile-A by default.
