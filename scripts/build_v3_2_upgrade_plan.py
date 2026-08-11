#!/usr/bin/env python3
"""Emit the V3.2 upgrade plan artifacts derived from the weakness mining.

Audit 0079. Separated from `analyze_v3_2_weaknesses.py` because that script
measures and this one *concludes*: every row below is a judgement about what to
build, traceable to a measurement the analysis produced.

CPU only. Reads the mining outputs, writes plan tables. No model, no gold.

Usage::

    python scripts/build_v3_2_upgrade_plan.py \
      --mining-dir outputs/v3_2_weakness_mining
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v3-2-upgrade-plan-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# Deterministic rule candidates, with their measured TRAIN outcome
# --------------------------------------------------------------------------

CHEAP_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule": "abstain when every accepted candidate is singly supported (stock)",
        "relation": "companyTradesAtStockExchange",
        "class": "A_finalization",
        "measured_macro_f1_delta": -0.00927,
        "rows_changed": 36, "rows_improved": 9, "rows_harmed": 21,
        "verdict": "REJECT",
        "reason": (
            "support does not separate empty-gold from non-empty-gold rows: 92 of "
            "116 accepted candidates on non-empty-gold stock rows are also singly "
            "supported, so the rule deletes correct answers at twice the rate it "
            "removes wrong ones"
        ),
    },
    {
        "rule": "abstain when every accepted candidate is singly supported (city)",
        "relation": "personHasCityOfDeath",
        "class": "A_finalization",
        "measured_macro_f1_delta": +0.00629,
        "rows_changed": 22, "rows_improved": 9, "rows_harmed": 6,
        "verdict": "REJECT",
        "reason": (
            "positive on TRAIN but for the wrong reason: the support distributions "
            "for empty and non-empty gold overlap almost completely (9 vs 16 "
            "accepted candidates, all at support 1), so the gain comes from the "
            "42% empty-gold base rate rather than from evidence. Promoting it "
            "would encode a TRAIN prior about how often the relation is empty"
        ),
    },
    {
        "rule": "numeric cluster tiebreak by max acquisition support",
        "relation": "hasArea",
        "class": "A_finalization",
        "measured_macro_f1_delta": 0.0,
        "rows_changed": 0, "rows_improved": 0, "rows_harmed": 0,
        "verdict": "REJECT",
        "reason": (
            "no-op: 219 of 240 hasArea numeric candidates carry support 1, so "
            "competing clusters tie on every available signal and the ordering "
            "falls through to the same tiebreak"
        ),
    },
    {
        "rule": "numeric cluster tiebreak by max candidate score",
        "relation": "hasArea",
        "class": "A_finalization",
        "measured_macro_f1_delta": 0.0,
        "rows_changed": 0, "rows_improved": 0, "rows_harmed": 0,
        "verdict": "REJECT",
        "reason": "identical output to the current policy; scores tie with support",
    },
    {
        "rule": "numeric cluster tiebreak by largest representative",
        "relation": "hasArea",
        "class": "A_finalization",
        "measured_macro_f1_delta": -0.01048,
        "rows_changed": 53, "rows_improved": 5, "rows_harmed": 10,
        "verdict": "REJECT",
        "reason": "arbitrary in the other direction; harms twice as many rows as it helps",
    },
    {
        "rule": "structured-output / enumeration-label repair",
        "relation": "awardWonBy",
        "class": "A_finalization",
        "measured_macro_f1_delta": +0.00053,
        "rows_changed": 9, "rows_improved": 8, "rows_harmed": 0,
        "verdict": "ALREADY_SHIPPED",
        "reason": "audit 0076; 0 structured-output leaks remain in SAFE_CORE output",
    },
    {
        "rule": "final candidate retention (numeric emittable-cluster pool)",
        "relation": "hasArea",
        "class": "A_finalization",
        "measured_macro_f1_delta": +0.01468,
        "rows_changed": 55, "rows_improved": 7, "rows_harmed": 0,
        "verdict": "ALREADY_SHIPPED",
        "reason": "audit 0076",
    },
)


MODULES: tuple[dict[str, Any], ...] = (
    {
        "module": "Discriminative Verification Budget (DVB)",
        "covers": "hasArea, hasCapacity, companyTradesAtStockExchange, awardWonBy",
        "failure_modes_addressed": (
            "GOOD_CANDIDATE_NOT_SELECTED;WRONG_NUMERIC_ATTRIBUTE;"
            "BAD_CANDIDATE_SURVIVED;TOO_MANY_FALSE_POSITIVES"),
        "evidence": (
            "35 of 2937 TRAIN candidates were ever verified (1.2%); stock, borders "
            "and award received 0 verification calls across 477 rows. Every "
            "relation's contract call cap is fully consumed by Phase A "
            "acquisition (award 12.00/12, borders 4.00/4, area 4.00/4, city "
            "3.97/4, capacity 3.90/4, stock 4.58/5), so the configured "
            "max_verifications_per_query=6 is nominal"),
        "mechanism": (
            "reserve 1-2 calls of the contract budget for contrastive "
            "verification of the top competing candidates instead of a marginal "
            "additional recall view"),
        "class": "B_calibration_shifting",
        "expected_value": "HIGH",
        "why": (
            "this is the precondition for every selection improvement: 219/240 "
            "numeric candidates carry support 1, so selection currently has "
            "nothing to rank on. No finalization rule can fix that"),
        "merge_note": "subsumes the separate 'Numeric Attribute Resolver' ranking role",
    },
    {
        "module": "Numeric Attribute Resolver (NAR)",
        "covers": "hasArea, hasCapacity",
        "failure_modes_addressed": (
            "WRONG_NUMERIC_ATTRIBUTE;NUMERIC_SCALE_CONFUSION;UNIT_CONFUSION"),
        "evidence": (
            "hasCapacity: 74/92 error rows are WRONG_NUMERIC_ATTRIBUTE; hasArea: "
            "51/69. The wrong number is usually a real figure for the subject, "
            "just the wrong attribute or configuration"),
        "mechanism": (
            "parse and canonicalise numerals, carry the semantic qualifier "
            "alongside the value, group equivalent values, and expose competing "
            "attribute variants to the verifier as a contrast pair"),
        "class": "B_calibration_shifting",
        "expected_value": "HIGH",
        "why": "capacity is the weakest relation (0.080) and is 100% recall-bound",
        "merge_note": (
            "MERGE the proposed 'Capacity Variant Resolver' into this module: the "
            "capacity qualifiers (seated/standing/concert/post-renovation) are one "
            "instance of the general attribute-variant problem, and hasArea has "
            "the same shape (total vs land vs metropolitan)"),
    },
    {
        "module": "Set Completeness Controller (SCC)",
        "covers": "awardWonBy, companyTradesAtStockExchange, countryLandBordersCountry",
        "failure_modes_addressed": (
            "EXPANDED_TOO_MUCH;STOPPED_TOO_EARLY;MISSING_SET_MEMBERS;"
            "BAD_CANDIDATE_SURVIVED"),
        "evidence": (
            "stock: 40/70 error rows over-enumerate and 16 under-enumerate "
            "simultaneously across the relation; award: 9/10 rows are partial with "
            "both FP and FN; borders: 8 rows under-enumerate"),
        "mechanism": (
            "separate RECALL_MORE / VERIFY_EXISTING / SUPPRESS_WEAK / STOP and "
            "drive continuation on marginal supported-candidate gain"),
        "class": "B_calibration_shifting",
        "expected_value": "MEDIUM",
        "why": "needs DVB first - suppression without verification is the rejected rule above",
        "merge_note": "absorbs the proposed 'Listing Disambiguation Layer' and 'Award Set Completeness Controller'",
    },
    {
        "module": "Attribute-Contrast Resolver (city)",
        "covers": "personHasCityOfDeath",
        "failure_modes_addressed": "NO_RECALL;TOO_MANY_FALSE_POSITIVES",
        "evidence": "51/61 city error rows are NO_RECALL; 29 candidates total across 100 rows",
        "mechanism": "already wired as the audit-0077 Class-B verifier boundary",
        "class": "B_calibration_shifting",
        "expected_value": "MEDIUM",
        "why": "city is recall-starved, not selection-starved; the contrast prompt is already live and untested",
        "merge_note": "no new module needed - this is the existing city diagnostic",
    },
    {
        "module": "Generic Output Contract Guard",
        "covers": "all",
        "failure_modes_addressed": "STRUCTURED_OUTPUT_LEAK;PARSER_FAILURE;WRONG_ENTITY_TYPE",
        "evidence": "0 remaining structured-output leaks and 0 malformed numerals in SAFE_CORE",
        "mechanism": "already shipped as audit 0076 enumeration repair + stock structural validation",
        "class": "A_already_shipped",
        "expected_value": "NONE_REMAINING",
        "why": "the failure class it targets is already empty; adding a new layer would be presentation, not engineering",
        "merge_note": "DO NOT BUILD - already covered",
    },
)


PROMPTS: tuple[dict[str, Any], ...] = (
    {
        "relation": "hasCapacity", "owner": "enumerator",
        "feature": "capacity_definition_prompt",
        "status": "WIRED_AUDIT_0077_UNTESTED",
        "failure_mechanism": "74/92 error rows emit a real but wrong-attribute number",
        "change": "none proposed; the existing instruction already names every variant",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
    },
    {
        "relation": "personHasCityOfDeath", "owner": "verifier",
        "feature": "city_of_death_contrast_prompt",
        "status": "WIRED_AUDIT_0077_UNTESTED",
        "failure_mechanism": "51 NO_RECALL rows; only 29 candidates across 100 rows",
        "change": (
            "consider adding an enumerator-side binding: the current binding is "
            "verifier-only, and a relation with 29 candidates over 100 rows is "
            "starved of recall, not of judgement"),
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
    },
    {
        "relation": "companyTradesAtStockExchange", "owner": "enumerator",
        "feature": "stock_listing_entity_prompt",
        "status": "WIRED_AUDIT_0077_UNTESTED",
        "failure_mechanism": "54/70 error rows carry false positives; 40 over-enumerate",
        "change": "none proposed; measure the wired instruction first",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
    },
    {
        "relation": "awardWonBy", "owner": "enumerator",
        "feature": "award_expansion_and_fp_cap",
        "status": "WIRED_AUDIT_0077_UNTESTED",
        "failure_mechanism": "9/10 rows partial; both FP and FN large",
        "change": "none proposed; measure first",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
    },
)


RANKING: tuple[dict[str, Any], ...] = (
    {
        "rank": 1, "improvement": "Discriminative Verification Budget",
        "affected_rows": 311, "relations": "all",
        "expected_value": "HIGH", "implementation_cost": "MEDIUM",
        "gpu_cost": "one TRAIN diagnostic per relation",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "LOW",
        "note": "precondition for every selection improvement; 1.2% of candidates are verified today",
    },
    {
        "rank": 2, "improvement": "capacity definition-aware recall (already wired)",
        "affected_rows": 92, "relations": "hasCapacity",
        "expected_value": "HIGH", "implementation_cost": "NONE",
        "gpu_cost": "1 diagnostic, 100 rows",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "MEDIUM",
        "note": "weakest relation at 0.080 and 100% recall-bound",
    },
    {
        "rank": 3, "improvement": "Numeric Attribute Resolver with contrastive variants",
        "affected_rows": 161, "relations": "hasArea, hasCapacity",
        "expected_value": "HIGH", "implementation_cost": "MEDIUM",
        "gpu_cost": "shares the capacity and area diagnostics",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "LOW",
        "note": "125 of 161 numeric error rows are WRONG_NUMERIC_ATTRIBUTE",
    },
    {
        "rank": 4, "improvement": "city recall binding (enumerator side)",
        "affected_rows": 61, "relations": "personHasCityOfDeath",
        "expected_value": "MEDIUM", "implementation_cost": "LOW",
        "gpu_cost": "1 diagnostic, 100 rows",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "MEDIUM",
        "note": "29 candidates across 100 rows is recall starvation",
    },
    {
        "rank": 5, "improvement": "Set Completeness Controller",
        "affected_rows": 89, "relations": "stock, award, borders",
        "expected_value": "MEDIUM", "implementation_cost": "HIGH",
        "gpu_cost": "2 diagnostics",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "MEDIUM",
        "note": "blocked on DVB: suppression without verification was measured and rejected",
    },
    {
        "rank": 6, "improvement": "stock listing-entity recall (already wired)",
        "affected_rows": 70, "relations": "companyTradesAtStockExchange",
        "expected_value": "MEDIUM", "implementation_cost": "NONE",
        "gpu_cost": "1 diagnostic, 100 rows",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "MEDIUM", "note": "measure the wired instruction",
    },
    {
        "rank": 7, "improvement": "award expansion + FP cap (already wired)",
        "affected_rows": 10, "relations": "awardWonBy",
        "expected_value": "LOW", "implementation_cost": "NONE",
        "gpu_cost": "1 diagnostic, 10 rows",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "HIGH",
        "note": "10 rows; a single row swings relation macro-F1 by 0.1",
    },
    {
        "rank": 8, "improvement": "scientific-notation acquisition parser (already wired)",
        "affected_rows": 0, "relations": "hasArea",
        "expected_value": "LOW", "implementation_cost": "NONE",
        "gpu_cost": "1 diagnostic, 100 rows",
        "calibration_impact": "CALIBRATION_REVIEW_REQUIRED",
        "generalization_risk": "NONE",
        "note": "correctness fix; fires on 0 TRAIN rows but the gap is real",
    },
    {
        "rank": 9, "improvement": "borders finalization retention",
        "affected_rows": 5, "relations": "countryLandBordersCountry",
        "expected_value": "LOW", "implementation_cost": "LOW",
        "gpu_cost": "none",
        "calibration_impact": "SAFE_WITH_EXISTING_CALIBRATION",
        "generalization_risk": "HIGH",
        "note": "borders is frozen at 0.964; 5 recoverable rows are not worth the regression risk",
    },
    {
        "rank": 10, "improvement": "deterministic abstention policies",
        "affected_rows": 58, "relations": "stock, city",
        "expected_value": "NEGATIVE", "implementation_cost": "LOW",
        "gpu_cost": "none",
        "calibration_impact": "SAFE_WITH_EXISTING_CALIBRATION",
        "generalization_risk": "HIGH",
        "note": "measured and REJECTED - see cheap_rule_candidates.csv",
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mining-dir", required=True, type=Path)
    args = parser.parse_args()
    out = args.mining_dir
    out.mkdir(parents=True, exist_ok=True)

    write_csv(out / "cheap_rule_candidates.csv", CHEAP_RULES)
    write_csv(out / "module_candidates.csv", MODULES)
    write_csv(out / "prompt_candidates.csv", PROMPTS)
    write_csv(out / "improvement_ranking.csv", RANKING)

    summary_path = out / "current_system_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    safe_core = summary.get("variants", {}).get("SAFE_CORE", {})
    metrics = safe_core.get("metrics", {}).get("*** All Relations ***", {})

    architecture = f"""# Recommended V3.2 Architecture

Derived from the audit-0079 forensics, not from a desire for more layers.

## What the evidence says

SAFE_CORE macro-F1 `{metrics.get('macro-f1', 'n/a')}`, 311 remaining error rows.

* **229 of 311 remaining errors (74%) are `NO_RECALL`** - the gold-like value was
  never surfaced as a candidate at all.
* **Only 14 rows (4.5%) are selection-recoverable** - a gold-like candidate is
  present and not emitted.
* **35 of 2937 candidates were ever verified (1.2%)**. Stock, borders and award
  received **zero** verification calls across 477 rows.
* **Every relation's contract call cap is fully consumed by Phase A acquisition**
  (award 12.00/12, borders 4.00/4, area 4.00/4, city 3.97/4, capacity 3.90/4,
  stock 4.58/5), so `max_verifications_per_query: 6` is nominal.
* **219 of 240 hasArea numeric candidates carry independent support 1**, so every
  principled cluster tiebreak produces byte-identical output.

Those five facts are one finding: **the budget buys recall and never buys
discrimination, so selection has nothing to rank on.** That is why three
separate deterministic selection rules measured at exactly zero, and why the
two abstention rules had to be rejected.

## The architecture that follows

```
Relation Profile
      |
Parametric Multi-View Recall          <- unchanged
      |
Numeric / Set Specialist              <- unchanged
      |
Hypothesis Graph                      <- unchanged
      |
Discriminative Verification Budget    <- NEW: reserve 1-2 calls for contrast
      |                                  verification instead of a marginal view
Numeric Attribute Resolver            <- NEW: qualifier-aware variant contrast
      |                                  (absorbs the capacity variant resolver)
M21 Controller                        <- unchanged formula, recalibrated
      |
Relation Contract Finalizer           <- unchanged (audit 0076 already shipped it)
```

Two new modules, not six. Three of the originally proposed layers are **not**
built:

* **Capacity Variant Resolver** - merged into the Numeric Attribute Resolver.
  Capacity qualifiers are one instance of the attribute-variant problem and
  hasArea has the same shape (total vs land vs metropolitan).
* **Listing Disambiguation Layer** and **Award Set Completeness Controller** -
  merged into one Set Completeness Controller, and deferred: both need
  verification evidence that does not exist yet.
* **Generic Output Contract Guard** - already shipped in audit 0076. The failure
  class it targets is empty in SAFE_CORE output: 0 structured-output leaks, 0
  malformed numerals, 0 subject-as-object emissions.

## What is deliberately not changed

`countryLandBordersCountry` stays frozen at `0.964`. Five of its rows are
finalization-recoverable and they are not worth the regression risk.

## Ordering constraint

The Set Completeness Controller is **blocked on** the Discriminative Verification
Budget. Suppressing weakly supported set members without verification is exactly
the rule this audit measured and rejected: on stock it deleted correct answers at
twice the rate it removed wrong ones.
"""
    (out / "recommended_v3_2_architecture.md").write_text(architecture, encoding="utf-8")

    matrix = """# GPU Experiment Matrix

TRAIN only. No TEST. Each runs one relation and one experimental feature, so a
result is attributable.

| # | Config | Relation | Rows | Feature | Status |
|---:|---|---|---:|---|---|
| 1 | `configs/experiments/v3_1_diag_capacity.yaml` | hasCapacity | 100 | `capacity_definition_prompt` | wired, never run |
| 2 | `configs/experiments/v3_1_diag_city.yaml` | personHasCityOfDeath | 100 | `city_of_death_contrast_prompt` | wired, never run |
| 3 | `configs/experiments/v3_1_diag_stock.yaml` | companyTradesAtStockExchange | 100 | `stock_listing_entity_prompt` | wired, never run |
| 4 | `configs/experiments/v3_1_diag_award.yaml` | awardWonBy | 10 | `award_expansion_and_fp_cap` | wired, never run |
| 5 | `configs/experiments/v3_1_diag_area_parser.yaml` | hasArea | 100 | `scientific_notation_acquisition` | wired, never run |

Priority order is the audit-0077 `promotion.RECOMMENDED_ORDER`, which audit 0079
does not change: capacity first, because it is the weakest relation (`0.080`) and
100% recall-bound.

## Run one

```bash
python scripts/run_cover.py --config configs/experiments/v3_1_diag_capacity.yaml --no-eval

python scripts/evaluate_v3_1_diagnostic.py \\
  --relation hasCapacity \\
  --baseline-run outputs/v3_train_collect_v2_coverage/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z \\
  --diagnostic-run outputs/<new run dir> \\
  --gold benchmark/data/train.jsonl \\
  --output-dir outputs/v3_1_class_b_diagnostics/capacity
```

Promotion gates are in `src/cover_kbc/v3_1/promotion.py` and are unchanged.

## Not yet buildable

The **Discriminative Verification Budget** has no diagnostic config because it is
a budget-allocation change rather than a prompt change, and its design decision -
how many of a relation's calls to reserve for verification - should be made after
the five diagnostics above show how much recall is actually recoverable by prompt
work alone. Building it first risks spending the budget on verifying candidates
that better prompts would have made unnecessary.
"""
    (out / "gpu_experiment_matrix.md").write_text(matrix, encoding="utf-8")

    plan = {
        "schema_version": SCHEMA_VERSION,
        "cheap_rules_evaluated": len(CHEAP_RULES),
        "cheap_rules_promoted": sum(1 for r in CHEAP_RULES if r["verdict"] == "PROMOTE"),
        "cheap_rules_rejected": sum(1 for r in CHEAP_RULES if r["verdict"] == "REJECT"),
        "already_shipped": sum(1 for r in CHEAP_RULES if r["verdict"] == "ALREADY_SHIPPED"),
        "modules_proposed": sum(1 for m in MODULES if m["class"].startswith("B")),
        "modules_rejected_as_already_covered": sum(
            1 for m in MODULES if m["merge_note"].startswith("DO NOT BUILD")),
        "verdicts": dict(Counter(r["verdict"] for r in CHEAP_RULES)),
    }
    (out / "upgrade_plan_summary.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = sorted(p for p in out.iterdir()
                       if p.is_file() and p.name != "SHA256SUMS.txt")
    (out / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(p)}  {p.name}\n" for p in checksums), encoding="utf-8")

    print(f"cheap rules evaluated : {plan['cheap_rules_evaluated']} "
          f"({plan['verdicts']})")
    print(f"modules proposed      : {plan['modules_proposed']}")
    print(f"written               : {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
