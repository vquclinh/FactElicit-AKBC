import argparse
import json
import unicodedata
from pathlib import Path
from typing import List, Dict, Union

import pandas as pd

RELATION_TYPE = {
    "awardWonBy": "string",
    "hasCapacity": "numeric",
    "hasArea": "numeric",
    "countryLandBordersCountry": "string",
    "personHasCityOfDeath": "string",
    "companyTradesAtStockExchange": "string"
}


def read_jsonl_file(file_path: Union[str, Path]) -> List[Dict]:
    with open(file_path, "r") as f:
        rows = [json.loads(line) for line in f]
    return rows


# Apostrophe-like marks: straight/curly apostrophes, okina and similar
# modifier letters, backtick/acute used as apostrophes.
APOSTROPHE_LIKE = set("'’‘ʻʼʹ`´")
# ASCII symbol characters (Unicode category S, e.g. in "Canal+") that the
# punctuation pass below would miss; treated as separators like before.
ASCII_SYMBOLS = set("+$<=>|~^")


def normalize_string(s: str) -> str:
    """Casefold, strip diacritics, drop apostrophe-like marks, replace
    punctuation (any Unicode category P, plus ASCII symbols) with a space,
    collapse whitespace. Dropping apostrophes (rather than spacing them) makes
    Kaua'i / Kauaʻi / Kauai and O'Brien / O’Brien normalize identically.
    Casefolding happens after NFKD so compatibility expansions (e.g. ™ -> TM)
    are folded too, and ß folds to ss."""
    s = "".join(c for c in s.strip() if c not in APOSTROPHE_LIKE)
    s = unicodedata.normalize("NFKD", s).casefold()
    out = []
    for c in s:
        if c in APOSTROPHE_LIKE or unicodedata.combining(c):
            continue  # also catches apostrophes produced by NFKD (e.g. ʼn)
        out.append(" " if c in ASCII_SYMBOLS or unicodedata.category(c).startswith("P") else c)
    return " ".join("".join(out).split())


def true_positives(preds: List[str], gts: List[List[str]], rel_type: str, tolerance: float = 0.05) -> int:
    if rel_type == "numeric":
        return numeric_true_positives(preds, gts, tolerance)
    else:
        return string_true_positives(preds, gts)


def try_parse_number(value: str) -> float | None:
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def numeric_true_positives(preds: List[str], gts: List[List[str]], tolerance: float = 0.05) -> int:
    tp = 0
    gt_nums = []
    for gt_aliases in gts:
        gt_num = try_parse_number(gt_aliases[0]) if gt_aliases else None
        gt_nums.append(gt_num)

    matched_gts = set()
    for pred in preds:
        pred_num = try_parse_number(pred)
        if pred_num is None:
            continue
        for i, gt_num in enumerate(gt_nums):
            if i not in matched_gts and gt_num is not None and gt_num != 0:
                if abs(pred_num - gt_num) / gt_num <= tolerance:
                    tp += 1
                    matched_gts.add(i)
                    break
    return tp


def string_true_positives(preds: List[str], gts: List[List[str]]) -> int:
    """Count true positives via maximum-cardinality bipartite matching between
    unique predictions and gold entities. A prediction matches a gold entity if
    the prediction's normalized form is in the gold entity's normalized alias
    set. Each prediction matches at most one gold and vice versa. Bipartite
    matching is used (rather than first-fit greedy) so the score is independent
    of prediction iteration order."""
    gt_alias_sets = [
        {normalize_string(alias) for alias in aliases}
        for aliases in gts
    ]
    unique_preds = list({normalize_string(p) for p in preds if isinstance(p, str)})

    # Adjacency: for each prediction index, the set of gold indices it can match.
    adj: List[set] = [
        {j for j, gs in enumerate(gt_alias_sets) if np in gs}
        for np in unique_preds
    ]

    # Maximum bipartite matching via augmenting paths (Kuhn's algorithm).
    match_gold_to_pred: dict = {}

    def try_augment(p: int, visited: set) -> bool:
        for g in adj[p]:
            if g in visited:
                continue
            visited.add(g)
            if g not in match_gold_to_pred or try_augment(match_gold_to_pred[g], visited):
                match_gold_to_pred[g] = p
                return True
        return False

    for p in range(len(unique_preds)):
        try_augment(p, set())

    return len(match_gold_to_pred)


def precision(preds: List[str], gts: List[List[str]], rel_type: str, tolerance: float = 0.05) -> float:
    try:
        if len(preds) == 0:
            return 1
        return min(true_positives(preds, gts, rel_type, tolerance) / len(preds), 1.0)
    except TypeError:
        return 0.0


def recall(preds: List[str], gts: List[List[str]], rel_type: str, tolerance: float = 0.05) -> float:
    try:
        if len(gts) == 0:
            return 1.0
        return min(true_positives(preds, gts, rel_type, tolerance) / len(gts), 1.0)
    except TypeError:
        return 0.0


def f1_score(p: float, r: float) -> float:
    try:
        return (2 * p * r) / (p + r)
    except ZeroDivisionError:
        return 0.0


def rows_to_dict(rows: List[Dict]) -> Dict:
    return {
        (r["SubjectEntity"], r["Relation"]): r["ObjectEntities"]
        for r in rows
    }


def evaluate_per_sr_pair(pred_rows, gt_rows, rel_types: Dict[str, str], tolerance: float = 0.05) -> List[
    Dict[str, float]]:
    pred_dict = rows_to_dict(pred_rows)
    gt_dict = rows_to_dict(gt_rows)

    results = []

    for subj, rel in gt_dict:
        gts = gt_dict[(subj, rel)]
        preds = pred_dict.get((subj, rel), [])

        # Ground truth: List[List[str]] (alias lists)
        # If still in old format (List[str]), wrap each entry
        if gts and isinstance(gts[0], str):
            gts = [[g] for g in gts]

        # Predictions are expected as List[str]. Tolerate one level of nesting
        # by taking the first element of any inner list; drop entries that are
        # neither strings nor non-empty lists. Deduplicate by normalized form
        # so that submitting the same answer twice does not inflate denominators.
        flat: List[str] = []
        seen: set = set()
        for p in preds:
            if isinstance(p, list):
                if not p:
                    continue
                p = p[0]
            if not isinstance(p, str):
                continue
            key = normalize_string(p)
            if key in seen:
                continue
            seen.add(key)
            flat.append(p)
        preds = flat

        rel_type = rel_types.get(rel, "string")
        tp = true_positives(preds, gts, rel_type=rel_type, tolerance=tolerance)

        p = tp / len(preds) if preds else 1.0
        r = tp / len(gts) if gts else 1.0
        f1 = f1_score(p, r)

        results.append({
            "SubjectEntity": subj,
            "Relation": rel,
            "p": p,
            "r": r,
            "f1": f1,
            "tp": tp,
            "total_pred": len(preds),
            "total_gt": len(gts),
        })

    return sorted(results, key=lambda x: (x["Relation"], x["SubjectEntity"]))


def macro_average_per_relation(scores_per_sr: List[Dict[str, float]]) -> dict:
    scores = {}
    for r in scores_per_sr:
        if r["Relation"] not in scores:
            scores[r["Relation"]] = []
        scores[r["Relation"]].append({
            "p": r["p"],
            "r": r["r"],
            "f1": r["f1"],
        })

    macro_averages = {}
    for rel in scores:
        macro_averages[rel] = {
            "macro-p": sum([x["p"] for x in scores[rel]]) / len(scores[rel]),
            "macro-r": sum([x["r"] for x in scores[rel]]) / len(scores[rel]),
            "macro-f1": sum([x["f1"] for x in scores[rel]]) / len(scores[rel]),
        }

    all_rel_macro_p = sum([x["p"] for x in scores_per_sr]) / len(scores_per_sr)
    all_rel_macro_r = sum([x["r"] for x in scores_per_sr]) / len(scores_per_sr)
    all_rel_macro_f1 = sum([x["f1"] for x in scores_per_sr]) / len(scores_per_sr)

    macro_averages["*** All Relations ***"] = {
        "macro-p": all_rel_macro_p,
        "macro-r": all_rel_macro_r,
        "macro-f1": all_rel_macro_f1,
    }

    return macro_averages


def micro_average_per_relation(scores_per_sr: List[Dict[str, float]]) -> dict:
    scores = {}
    for r in scores_per_sr:
        if r["Relation"] not in scores:
            scores[r["Relation"]] = {
                "tp": 0,
                "total_pred": 0,
                "total_gt": 0,
            }
        scores[r["Relation"]]["tp"] += r["tp"]
        scores[r["Relation"]]["total_pred"] += r["total_pred"]
        scores[r["Relation"]]["total_gt"] += r["total_gt"]

    micro_averages = {}
    for rel in scores:
        micro_p = scores[rel]["tp"] / scores[rel]["total_pred"] if scores[rel]["total_pred"] > 0 else 1.0
        micro_r = scores[rel]["tp"] / scores[rel]["total_gt"] if scores[rel]["total_gt"] > 0 else 1.0

        micro_averages[rel] = {
            "micro-p": micro_p,
            "micro-r": micro_r,
            "micro-f1": f1_score(micro_p, micro_r),
        }

    total_tp = sum([x["tp"] for x in scores.values()])
    total_pred = sum([x["total_pred"] for x in scores.values()])
    total_gt = sum([x["total_gt"] for x in scores.values()])

    all_rel_micro_p = total_tp / total_pred if total_pred > 0 else 1.0
    all_rel_micro_r = total_tp / total_gt if total_gt > 0 else 1.0

    micro_averages["*** All Relations ***"] = {
        "micro-p": all_rel_micro_p,
        "micro-r": all_rel_micro_r,
        "micro-f1": f1_score(all_rel_micro_p, all_rel_micro_r),
    }

    return micro_averages


def prediction_statistics(scores_per_sr: List[Dict[str, float]]) -> dict:
    stats = {}
    for r in scores_per_sr:
        if r["Relation"] not in stats:
            stats[r["Relation"]] = {
                "num_sr_pairs": 0,
                "total_pred": 0,
                "empty_pred": 0,
            }
        stats[r["Relation"]]["num_sr_pairs"] += 1
        stats[r["Relation"]]["total_pred"] += r["total_pred"]
        if r["total_pred"] == 0:
            stats[r["Relation"]]["empty_pred"] += 1

    final_stats = {}
    for rel in stats:
        final_stats[rel] = {
            "avg. #preds": stats[rel]["total_pred"] / stats[rel]["num_sr_pairs"],
            "#empty preds": stats[rel]["empty_pred"],
        }

    total_sr_pairs = len(scores_per_sr)
    total_preds = sum([x["total_pred"] for x in stats.values()])
    total_empty_preds = sum([x["empty_pred"] for x in stats.values()])

    final_stats["*** All Relations ***"] = {
        "avg. #preds": total_preds / total_sr_pairs,
        "#empty preds": total_empty_preds,
    }

    return final_stats


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Precision, Recall and F1-score of predictions")

    parser.add_argument(
        "-p", "--predictions",
        type=str,
        required=True,
        help="Path to the predictions file (required)"
    )
    parser.add_argument(
        "-g", "--ground_truth",
        type=str,
        required=True,
        help="Path to the ground truth file (required)"
    )

    args = parser.parse_args()

    pred_rows = read_jsonl_file(args.predictions)
    gt_rows = read_jsonl_file(args.ground_truth)

    scores_per_sr_pair = evaluate_per_sr_pair(pred_rows, gt_rows, RELATION_TYPE, tolerance=0.05)

    macro_per_relation = macro_average_per_relation(scores_per_sr_pair)
    macro_df = pd.DataFrame(macro_per_relation).transpose().round(3)

    micro_per_relation = micro_average_per_relation(scores_per_sr_pair)
    micro_df = pd.DataFrame(micro_per_relation).transpose().round(3)

    stats = prediction_statistics(scores_per_sr_pair)
    stats_df = pd.DataFrame(stats).transpose().round(3)
    stats_df["#empty preds"] = stats_df["#empty preds"].astype(int)

    results = pd.concat([macro_df, micro_df, stats_df], axis=1)
    print(results)


if __name__ == "__main__":
    main()
