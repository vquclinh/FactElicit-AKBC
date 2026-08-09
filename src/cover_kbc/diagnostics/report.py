"""Per-relation failure report: the question V3A exists to answer.

    hasCapacity:
      gold acquired     61%
      normalized        59%
      verifier reached  54%
      verifier accepted 31%
      final             17%

versus

    hasCapacity:
      gold acquired     19%
      final             15%

The first shape says the fact is in the model and the pipeline throws it away;
the second says the fact never arrives. They call for opposite V3B work - one
for better verification and control, the other for better elicitation - and
without this report the choice between them is a guess.

Every number is aggregated from :class:`~cover_kbc.diagnostics.gold_attribution.QueryAttribution`
objects, which were themselves scored by the pinned official evaluator. No
neural call is made and no threshold is fitted; this module only counts.

Two rate conventions, kept apart on purpose:

* **micro** rates are object-level - gold objects present, over gold objects
  that exist. This is the authoritative view, and the one §4 of the brief
  requires for set-valued relations.
* **macro** rates are query-level - the mean of per-query fractions. Reported
  alongside because a relation with one 40-object row and forty 1-object rows
  has two legitimately different recall stories.

Empty-gold queries appear in **neither**. They have no recall denominator, and
:class:`EmptyGoldSummary` accounts for them separately.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.diagnostics.gold_attribution import QueryAttribution
from cover_kbc.diagnostics.stages import (
    FailureCategory,
    FalsePositiveCategory,
    PipelineStage,
    STAGE_ORDER,
    STAGE_SOURCES,
)

#: Bumped when a column's meaning changes, so an old report is not silently
#: compared against a new one.
REPORT_VERSION = "v3a-failure-report-v1"


@dataclass(frozen=True)
class EmptyGoldSummary:
    """How the system behaves where the right answer is "nothing".

    §5 asks for these four separately, and they matter most for
    ``personHasCityOfDeath``, ``companyTradesAtStockExchange`` and
    ``countryLandBordersCountry`` - the three relations where an empty gold set
    is a real answer rather than a data gap.
    """

    query_count: int = 0
    acquisition_empty: int = 0
    proposed_a_candidate: int = 0
    final_correctly_empty: int = 0

    def _rate(self, count: int) -> float:
        return (count / self.query_count) if self.query_count else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "query_count": self.query_count,
            "acquisition_empty": self.acquisition_empty,
            "acquisition_empty_rate": self._rate(self.acquisition_empty),
            "proposed_a_candidate": self.proposed_a_candidate,
            "proposed_a_candidate_rate": self._rate(self.proposed_a_candidate),
            "final_correctly_empty": self.final_correctly_empty,
            "final_correctly_empty_rate": self._rate(self.final_correctly_empty),
        }


@dataclass(frozen=True)
class RelationFailureReport:
    """Everything V3A knows about why one relation fails."""

    relation: str
    query_count: int
    observable_query_count: int
    total_gold_objects: int
    non_empty_gold_query_count: int
    empty_gold: EmptyGoldSummary

    #: Gold objects present at each stage, and the micro/macro rates.
    stage_gold_present: Mapping[str, int]
    stage_gold_micro: Mapping[str, float]
    stage_gold_macro: Mapping[str, float]

    oracle_candidate_recall_micro: float
    oracle_candidate_recall_macro: float

    false_positive_categories: Mapping[str, int]
    false_positives_at_stage: Mapping[str, int]
    false_candidates_introduced: int
    false_candidates_accepted: int
    false_candidates_emitted: int

    empty_final_prediction_count: int
    average_candidate_count: float
    average_verifier_candidate_count: float

    neural_call_count: int
    generated_tokens: int
    verification_calls: int

    failure_histogram: Mapping[str, int]
    dominant_failure: str
    failure_state_histogram: Mapping[str, int]

    def to_json(self) -> dict[str, Any]:
        return {
            "Relation": self.relation,
            "query_count": self.query_count,
            "observable_query_count": self.observable_query_count,
            "total_gold_objects": self.total_gold_objects,
            "non_empty_gold_query_count": self.non_empty_gold_query_count,
            "empty_gold": self.empty_gold.to_json(),
            "stage_gold_present": dict(self.stage_gold_present),
            "stage_gold_micro": dict(self.stage_gold_micro),
            "stage_gold_macro": dict(self.stage_gold_macro),
            "oracle_candidate_recall_micro": self.oracle_candidate_recall_micro,
            "oracle_candidate_recall_macro": self.oracle_candidate_recall_macro,
            "false_positive_categories": dict(self.false_positive_categories),
            "false_positives_at_stage": dict(self.false_positives_at_stage),
            "false_candidates_introduced": self.false_candidates_introduced,
            "false_candidates_accepted": self.false_candidates_accepted,
            "false_candidates_emitted": self.false_candidates_emitted,
            "empty_final_prediction_count": self.empty_final_prediction_count,
            "average_candidate_count": self.average_candidate_count,
            "average_verifier_candidate_count":
                self.average_verifier_candidate_count,
            "neural_call_count": self.neural_call_count,
            "generated_tokens": self.generated_tokens,
            "verification_calls": self.verification_calls,
            "failure_histogram": dict(self.failure_histogram),
            "dominant_failure": self.dominant_failure,
            "failure_state_histogram": dict(self.failure_state_histogram),
        }


@dataclass(frozen=True)
class FailureAttributionReport:
    """The whole analysis: one entry per relation, plus provenance."""

    report_version: str
    split: str
    telemetry_path: str
    gold_path: str
    evaluator_sha256: str
    tolerance: float
    query_count: int
    relations: tuple[RelationFailureReport, ...] = field(default=())

    def to_json(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "split": self.split,
            "telemetry_path": self.telemetry_path,
            "gold_path": self.gold_path,
            "evaluator_sha256": self.evaluator_sha256,
            "tolerance": self.tolerance,
            "query_count": self.query_count,
            "stage_sources": {k.value: v for k, v in STAGE_SOURCES.items()},
            "relations": [r.to_json() for r in self.relations],
        }

    # -- human-readable views ------------------------------------------------

    def to_markdown(self) -> str:
        return _markdown(self)

    def to_csv(self) -> str:
        return _csv(self)


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def build_report(
    attributions: Iterable[QueryAttribution], *, split: str,
    telemetry_path: str = "", gold_path: str = "",
    evaluator_sha256: str = "", tolerance: float = 0.0,
) -> FailureAttributionReport:
    """Aggregate per-query attributions into one per-relation report."""
    by_relation: dict[str, list[QueryAttribution]] = {}
    total = 0
    for attribution in attributions:
        by_relation.setdefault(attribution.relation, []).append(attribution)
        total += 1
    return FailureAttributionReport(
        report_version=REPORT_VERSION, split=split,
        telemetry_path=telemetry_path, gold_path=gold_path,
        evaluator_sha256=evaluator_sha256, tolerance=tolerance,
        query_count=total,
        relations=tuple(
            _relation_report(relation, by_relation[relation])
            for relation in sorted(by_relation)),
    )


def _relation_report(
    relation: str, rows: "Sequence[QueryAttribution]",
) -> RelationFailureReport:
    non_empty = [r for r in rows if not r.is_empty_gold and r.observable]
    observable = [r for r in rows if r.observable]
    total_gold = sum(r.gold_count for r in non_empty)

    present = {
        stage.value: sum(r.stage(stage).gold_present_count for r in non_empty)
        for stage in STAGE_ORDER
    }
    micro = {
        stage: (count / total_gold) if total_gold else 0.0
        for stage, count in present.items()
    }
    macro = {
        stage.value: _mean([r.stage(stage).gold_fraction for r in non_empty])
        for stage in STAGE_ORDER
    }

    failures: dict[str, int] = {c.value: 0 for c in FailureCategory}
    for row in rows:
        for category in row.failures.values():
            failures[category.value] += 1
    # The dominant failure is the largest *loss* bucket. Success is excluded
    # because "most gold objects were emitted correctly" is not a failure mode,
    # and a relation at 0.95 F1 would otherwise report its own success as the
    # thing to fix.
    losses = {name: count for name, count in failures.items()
              if name not in (FailureCategory.SUCCESSFULLY_EMITTED.value,
                              FailureCategory.NOT_OBSERVABLE.value)}
    dominant = max(losses, key=lambda k: (losses[k], k)) if any(
        losses.values()) else ""

    fp_categories: dict[str, int] = {c.value: 0 for c in FalsePositiveCategory}
    for row in rows:
        for name, count in row.false_positives.items():
            fp_categories[name] = fp_categories.get(name, 0) + count
    fp_at_stage = {
        stage.value: sum(
            r.false_positives_at_stage.get(stage.value, 0) for r in rows)
        for stage in STAGE_ORDER
    }

    empty_rows = [r for r in rows if r.is_empty_gold and r.empty_gold is not None]
    empty = EmptyGoldSummary(
        query_count=len(empty_rows),
        acquisition_empty=sum(
            1 for r in empty_rows if r.empty_gold.acquisition_empty),
        proposed_a_candidate=sum(
            1 for r in empty_rows if r.empty_gold.proposed_any_candidate),
        final_correctly_empty=sum(
            1 for r in empty_rows if r.empty_gold.final_empty),
    )

    states: dict[str, int] = {}
    for row in rows:
        if row.failure_state:
            states[row.failure_state] = states.get(row.failure_state, 0) + 1

    return RelationFailureReport(
        relation=relation,
        query_count=len(rows),
        observable_query_count=len(observable),
        total_gold_objects=total_gold,
        non_empty_gold_query_count=len(non_empty),
        empty_gold=empty,
        stage_gold_present=present,
        stage_gold_micro=micro,
        stage_gold_macro=macro,
        oracle_candidate_recall_micro=(
            sum(r.oracle_present_count for r in non_empty) / total_gold
            if total_gold else 0.0),
        oracle_candidate_recall_macro=_mean(
            [r.oracle_fraction for r in non_empty]),
        false_positive_categories=dict(sorted(fp_categories.items())),
        false_positives_at_stage=fp_at_stage,
        # "Introduced" is entry into the evidence graph, not into the raw
        # fragment list: a fragment normalisation rejected cost nothing and was
        # never a proposal. Those are counted under ACQUISITION_FP instead.
        false_candidates_introduced=fp_at_stage[PipelineStage.NORMALIZED.value],
        false_candidates_accepted=fp_at_stage[
            PipelineStage.CONTROL_SURVIVED.value],
        false_candidates_emitted=fp_at_stage[PipelineStage.FINAL_EMITTED.value],
        empty_final_prediction_count=sum(1 for r in rows if r.final_empty),
        average_candidate_count=_mean([r.candidate_count for r in observable]),
        average_verifier_candidate_count=_mean(
            [r.verifier_candidate_count for r in observable]),
        neural_call_count=sum(r.calls_used for r in rows),
        generated_tokens=sum(r.generated_tokens_used for r in rows),
        verification_calls=sum(r.verification_calls for r in rows),
        failure_histogram=dict(sorted(failures.items())),
        dominant_failure=dominant,
        failure_state_histogram=dict(sorted(states.items())),
    )


def _mean(values: "Sequence[float]") -> float:
    """Arithmetic mean, or ``0.0`` for an empty sequence.

    The empty case is a real one - a relation may have no non-empty-gold row -
    and it must not be a ``ZeroDivisionError`` in a report that is supposed to
    explain failures.
    """
    return (sum(values) / len(values)) if values else 0.0


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

#: Stage labels as the report prints them, in pipeline order.
_STAGE_LABELS: tuple[tuple[PipelineStage, str], ...] = (
    (PipelineStage.ACQUIRED, "gold acquired"),
    (PipelineStage.NORMALIZED, "normalized"),
    (PipelineStage.VERIFIER_REACHED, "verifier reached"),
    (PipelineStage.VERIFIER_ACCEPTED, "verifier accepted"),
    (PipelineStage.CONTROL_SURVIVED, "control survived"),
    (PipelineStage.FINAL_EMITTED, "final"),
)


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def _markdown(report: FailureAttributionReport) -> str:
    out: list[str] = [
        "# V3A failure attribution", "",
        f"- split: `{report.split}`",
        f"- queries: {report.query_count}",
        f"- telemetry: `{report.telemetry_path}`",
        f"- gold: `{report.gold_path}`",
        f"- evaluator sha256: `{report.evaluator_sha256}`",
        f"- numeric tolerance: {report.tolerance}",
        f"- report version: `{report.report_version}`", "",
        "Micro rates are object-level over gold objects that exist; macro rates",
        "are the mean of per-query fractions. Empty-gold queries are in neither",
        "- see the empty-gold table.", "",
        "## Gold survival by stage (micro)", "",
    ]

    header = "| Relation | " + " | ".join(
        label for _, label in _STAGE_LABELS) + " |"
    out += [header, "|" + "---|" * (len(_STAGE_LABELS) + 1)]
    for relation in report.relations:
        cells = " | ".join(
            _pct(relation.stage_gold_micro[stage.value])
            for stage, _ in _STAGE_LABELS)
        out.append(f"| {relation.relation} | {cells} |")

    out += ["", "## Oracle candidate recall", "",
            "Gold objects that appeared anywhere in the candidate pool before",
            "finalization. The ceiling every downstream stage works under: no",
            "amount of better verification recovers what was never proposed.",
            "",
            "| Relation | micro | macro | gold objects | non-empty queries |",
            "|---|---|---|---|---|"]
    for relation in report.relations:
        out.append(
            f"| {relation.relation} "
            f"| {_pct(relation.oracle_candidate_recall_micro)} "
            f"| {_pct(relation.oracle_candidate_recall_macro)} "
            f"| {relation.total_gold_objects} "
            f"| {relation.non_empty_gold_query_count} |")

    out += ["", "## Dominant failure", "",
            "| Relation | dominant loss | histogram |", "|---|---|---|"]
    for relation in report.relations:
        histogram = ", ".join(
            f"{name}={count}"
            for name, count in relation.failure_histogram.items() if count)
        out.append(
            f"| {relation.relation} | {relation.dominant_failure or '-'} "
            f"| {histogram or '-'} |")

    out += ["", "## False positives", "",
            "| Relation | introduced | accepted | emitted | by category |",
            "|---|---|---|---|---|"]
    for relation in report.relations:
        categories = ", ".join(
            f"{name}={count}"
            for name, count in relation.false_positive_categories.items()
            if count)
        out.append(
            f"| {relation.relation} | {relation.false_candidates_introduced} "
            f"| {relation.false_candidates_accepted} "
            f"| {relation.false_candidates_emitted} | {categories or '-'} |")

    out += ["", "## Empty-gold behaviour", "",
            "Queries whose correct answer is nothing. These have no recall",
            "denominator and are excluded from every rate above.", "",
            "| Relation | queries | acquisition empty | proposed a candidate "
            "| final correctly empty |", "|---|---|---|---|---|"]
    for relation in report.relations:
        empty = relation.empty_gold
        out.append(
            f"| {relation.relation} | {empty.query_count} "
            f"| {empty.acquisition_empty} | {empty.proposed_a_candidate} "
            f"| {empty.final_correctly_empty} |")

    out += ["", "## Cost", "",
            "| Relation | queries | calls | generated tokens | verifier calls "
            "| avg candidates | avg verified |", "|---|---|---|---|---|---|---|"]
    for relation in report.relations:
        out.append(
            f"| {relation.relation} | {relation.query_count} "
            f"| {relation.neural_call_count} | {relation.generated_tokens} "
            f"| {relation.verification_calls} "
            f"| {relation.average_candidate_count:.2f} "
            f"| {relation.average_verifier_candidate_count:.2f} |")

    out += ["", "## Per-relation detail", ""]
    for relation in report.relations:
        out += [f"### {relation.relation}", ""]
        for stage, label in _STAGE_LABELS:
            out.append(
                f"    {label:<18} "
                f"{_pct(relation.stage_gold_micro[stage.value])}  "
                f"({relation.stage_gold_present[stage.value]}"
                f"/{relation.total_gold_objects})")
        states = ", ".join(
            f"{name}={count}"
            for name, count in relation.failure_state_histogram.items())
        out += ["", f"    search states: {states or '-'}",
                f"    empty final predictions: "
                f"{relation.empty_final_prediction_count}"
                f"/{relation.query_count}", ""]
    return "\n".join(out) + "\n"


#: One row per relation. Flat on purpose - this is what goes into a spreadsheet.
CSV_COLUMNS: tuple[str, ...] = (
    "Relation", "query_count", "observable_query_count",
    "total_gold_objects",
    "non_empty_gold_query_count", "empty_gold_query_count",
    "oracle_candidate_recall_micro", "oracle_candidate_recall_macro",
    *(f"gold_{stage.value.lower()}_micro" for stage in STAGE_ORDER),
    *(f"gold_{stage.value.lower()}_macro" for stage in STAGE_ORDER),
    "false_candidates_introduced", "false_candidates_accepted",
    "false_candidates_emitted", "empty_final_prediction_count",
    "average_candidate_count", "average_verifier_candidate_count",
    "neural_call_count", "generated_tokens", "verification_calls",
    "dominant_failure", "failure_histogram",
    "false_positive_categories", "failure_state_histogram",
)


def _csv(report: FailureAttributionReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for relation in report.relations:
        writer.writerow([
            relation.relation, relation.query_count,
            relation.observable_query_count,
            relation.total_gold_objects, relation.non_empty_gold_query_count,
            relation.empty_gold.query_count,
            f"{relation.oracle_candidate_recall_micro:.6f}",
            f"{relation.oracle_candidate_recall_macro:.6f}",
            *(f"{relation.stage_gold_micro[stage.value]:.6f}"
              for stage in STAGE_ORDER),
            *(f"{relation.stage_gold_macro[stage.value]:.6f}"
              for stage in STAGE_ORDER),
            relation.false_candidates_introduced,
            relation.false_candidates_accepted,
            relation.false_candidates_emitted,
            relation.empty_final_prediction_count,
            f"{relation.average_candidate_count:.4f}",
            f"{relation.average_verifier_candidate_count:.4f}",
            relation.neural_call_count, relation.generated_tokens,
            relation.verification_calls, relation.dominant_failure,
            json.dumps(relation.failure_histogram, sort_keys=True),
            json.dumps(relation.false_positive_categories, sort_keys=True),
            json.dumps(relation.failure_state_histogram, sort_keys=True),
        ])
    return buffer.getvalue()


def write_report(
    report: FailureAttributionReport, directory: "str | Any",
) -> dict[str, Any]:
    """Write the JSON, Markdown and CSV views side by side."""
    from pathlib import Path

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "failure_attribution.json",
        "markdown": out / "failure_attribution.md",
        "csv": out / "failure_attribution.csv",
    }
    paths["json"].write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    paths["markdown"].write_text(report.to_markdown(), encoding="utf-8")
    paths["csv"].write_text(report.to_csv(), encoding="utf-8")
    return paths


__all__ = [
    "CSV_COLUMNS",
    "EmptyGoldSummary",
    "FailureAttributionReport",
    "REPORT_VERSION",
    "RelationFailureReport",
    "build_report",
    "write_report",
]
