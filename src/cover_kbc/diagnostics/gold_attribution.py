"""Where each gold object was lost - joined to TRAIN labels, **after the run**.

This is the other half of the V3A gold boundary. Everything here reads gold;
nothing here can reach inference. The separation is structural, not a
convention:

* it consumes a persisted :class:`~cover_kbc.diagnostics.inference_telemetry.QueryInferenceRecord`,
  which was produced without labels and contains none;
* it has no model, no runtime and no way to obtain one - the module imports no
  backend and calls nothing that could;
* it **refuses any split but TRAIN**. A record from ``val`` or ``test`` raises
  :class:`GoldLeakageError` rather than being attributed, and a gold file whose
  rows carry no objects is refused as well, so pointing this at the blind split
  fails on both counts.

Correctness is decided by the pinned official evaluator through
:class:`~cover_kbc.controller_calibration.gold_join.GoldIndex`, never by a local
re-implementation: alias handling, maximum bipartite matching and the 5 %
numeric tolerance are all its. "This gold object survived to the verifier"
therefore means the same thing here as "true positive" does on the leaderboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.controller_calibration.gold_join import (
    GoldIndex,
    GoldJoinError,
    load_gold,
)
from cover_kbc.diagnostics.inference_telemetry import QueryInferenceRecord
from cover_kbc.diagnostics.stages import (
    FP_AT_STAGE,
    LOSS_AFTER_STAGE,
    FailureCategory,
    FalsePositiveCategory,
    PipelineStage,
    STAGE_ORDER,
)
from cover_kbc.integration_mode import CALIBRATION_SPLIT
from cover_kbc.paths import SPLIT_FILES

#: Stages that make up "the candidate pool before finalization" (§5). Module 8
#: emits a *derived* representative for numeric relations - a cluster median
#: that need never have been generated - so the oracle pool is the union of
#: everything upstream of that, not the ``ACQUIRED`` stage alone.
PRE_FINAL_STAGES: tuple[PipelineStage, ...] = tuple(
    stage for stage in STAGE_ORDER if stage is not PipelineStage.FINAL_EMITTED
)


class GoldLeakageError(RuntimeError):
    """Gold attribution was asked for on something that is not labelled TRAIN.

    Not a ``ValueError``: this is the boundary condition the whole milestone is
    built around, and it must be impossible to mistake for an ordinary bad
    argument that a caller might reasonably swallow.
    """


@dataclass(frozen=True)
class StageSurvival:
    """How much of one query's gold was still present at one stage."""

    stage: PipelineStage
    gold_count: int
    gold_present_count: int

    @property
    def gold_fraction(self) -> float:
        """Share of this row's gold present here. ``0.0`` when gold is empty.

        An empty-gold row contributes no recall denominator and must never be
        folded into one; :class:`EmptyGoldOutcome` accounts for it instead.
        """
        return (self.gold_present_count / self.gold_count
                if self.gold_count else 0.0)

    @property
    def all_present(self) -> bool:
        return bool(self.gold_count) and self.gold_present_count == self.gold_count

    @property
    def any_present(self) -> bool:
        return self.gold_present_count > 0

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value, "gold_count": self.gold_count,
            "gold_present_count": self.gold_present_count,
            "gold_fraction": self.gold_fraction,
            "all_present": self.all_present, "any_present": self.any_present,
        }


@dataclass(frozen=True)
class EmptyGoldOutcome:
    """What happened on a query whose gold set is empty.

    Reported entirely separately from recall, because an empty-gold row has no
    recall: the evaluator scores it 1.0 for recall by definition, and dividing
    by its zero gold objects is the statistic §5 exists to forbid. What matters
    instead is whether the system correctly declined to answer.
    """

    acquisition_empty: bool
    proposed_any_candidate: bool
    final_empty: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "acquisition_empty": self.acquisition_empty,
            "proposed_any_candidate": self.proposed_any_candidate,
            "final_empty": self.final_empty,
        }


@dataclass(frozen=True)
class QueryAttribution:
    """One query's stage survival, per-object failures and false positives."""

    subject: str
    relation: str
    row_index: int
    gold_count: int
    #: Present at every stage, keyed by stage name.
    survival: Mapping[str, StageSurvival]
    #: Gold indices present at each stage. Indices, never gold strings.
    present_gold: Mapping[str, frozenset[int]]
    #: Gold index -> where it was irreversibly lost.
    failures: Mapping[int, FailureCategory]
    #: False-positive category -> how many distinct wrong predictions earned it.
    false_positives: Mapping[str, int]
    #: Wrong predictions *present at* each stage - cumulative, unlike the
    #: category histogram above, which partitions by furthest stage reached.
    false_positives_at_stage: Mapping[str, int]
    #: Gold objects reachable anywhere in the pre-finalization candidate pool.
    oracle_present_count: int
    empty_gold: "EmptyGoldOutcome | None"
    observable: bool
    calls_used: int
    generated_tokens_used: int
    verification_calls: int
    candidate_count: int
    verifier_candidate_count: int
    final_empty: bool
    failure_state: str

    @property
    def is_empty_gold(self) -> bool:
        return self.gold_count == 0

    @property
    def oracle_fraction(self) -> float:
        """Per-query oracle candidate recall. ``0.0`` for an empty-gold row."""
        return (self.oracle_present_count / self.gold_count
                if self.gold_count else 0.0)

    def stage(self, stage: PipelineStage) -> StageSurvival:
        return self.survival[stage.value]

    def to_json(self) -> dict[str, Any]:
        return {
            "SubjectEntity": self.subject, "Relation": self.relation,
            "row_index": self.row_index, "gold_count": self.gold_count,
            "survival": {k: v.to_json() for k, v in self.survival.items()},
            "present_gold": {k: sorted(v) for k, v in self.present_gold.items()},
            "failures": {str(k): v.value for k, v in sorted(self.failures.items())},
            "false_positives": dict(sorted(self.false_positives.items())),
            "false_positives_at_stage": dict(
                sorted(self.false_positives_at_stage.items())),
            "oracle_present_count": self.oracle_present_count,
            "oracle_fraction": self.oracle_fraction,
            "empty_gold": (self.empty_gold.to_json()
                           if self.empty_gold else None),
            "observable": self.observable, "calls_used": self.calls_used,
            "generated_tokens_used": self.generated_tokens_used,
            "verification_calls": self.verification_calls,
            "candidate_count": self.candidate_count,
            "verifier_candidate_count": self.verifier_candidate_count,
            "final_empty": self.final_empty,
            "failure_state": self.failure_state,
        }


class TrainGoldAttribution:
    """Joins TRAIN gold to inference telemetry. **TRAIN only, offline only.**

    Construct it once per analysis and call :meth:`attribute` per record. It
    holds no model, opens no split but the one it was given, and refuses any
    record that did not come from TRAIN.
    """

    def __init__(self, gold: GoldIndex, *, split: str = CALIBRATION_SPLIT) -> None:
        if split != CALIBRATION_SPLIT:
            raise GoldLeakageError(
                f"gold attribution is defined for {CALIBRATION_SPLIT!r} only; "
                f"refusing to build one for {split!r}. VAL is a held-out "
                "measurement and TEST is blind: neither has labels this layer "
                "may consume.")
        if not len(gold):
            raise GoldLeakageError("the gold index is empty")
        if all(row.size == 0 for row in gold.rows.values()):
            # Exactly the shape of the blind split. Whatever file this is, it
            # carries no labels, so attributing against it would report every
            # gold object as absent and every prediction as a false positive.
            raise GoldLeakageError(
                "no row in this gold index carries objects; that is a blind "
                "split, and a blind split cannot label anything")
        self.gold = gold
        self.split = split

    @classmethod
    def from_split(
        cls, *, path: "str | Path | None" = None, expected_rows: int | None = None,
    ) -> "TrainGoldAttribution":
        """Load ``benchmark/data/train.jsonl`` through the official reader.

        Args:
            path: override, for tests. Production leaves it unset.
            expected_rows: refuse a TRAIN of a different size. The row count is
                part of the split's identity, and an attribution joined against
                a different TRAIN is not the one it claims to be.
        """
        source = Path(path) if path is not None else SPLIT_FILES[CALIBRATION_SPLIT]
        return cls(load_gold(source, expected_rows=expected_rows))

    # -- the one entry point -------------------------------------------------

    def attribute(self, record: QueryInferenceRecord) -> QueryAttribution:
        """Attribute one query. Refuses anything not recorded on TRAIN.

        Raises:
            GoldLeakageError: if the record declares a split other than TRAIN.
                A record with no declared split is refused too - "unlabelled"
                is not "safe".
            GoldJoinError: if the record names a row TRAIN does not hold.
        """
        if record.split != CALIBRATION_SPLIT:
            raise GoldLeakageError(
                f"{record.subject!r}/{record.relation!r} was recorded on split "
                f"{record.split!r}; gold attribution accepts "
                f"{CALIBRATION_SPLIT!r} only. Running it on a production split "
                "would be exactly the leak this layer is separated to prevent.")

        row = self.gold.rows.get((record.subject, record.relation))
        if row is None:
            raise GoldJoinError(
                f"telemetry names {record.subject!r}/{record.relation!r}, which "
                "the TRAIN gold file does not contain")
        gold_count = row.size

        if not record.observable:
            return self._unobservable(record, gold_count)

        present: dict[str, frozenset[int]] = {}
        survival: dict[str, StageSurvival] = {}
        for stage in STAGE_ORDER:
            values = record.values_at(stage)
            assignment = self.gold.gold_assignment(
                record.subject, record.relation, values)
            present[stage.value] = assignment.matched_gold
            survival[stage.value] = StageSurvival(
                stage=stage, gold_count=gold_count,
                gold_present_count=assignment.matched)

        oracle = self.gold.gold_assignment(
            record.subject, record.relation,
            _union_values(record, PRE_FINAL_STAGES))

        failures = _attribute_failures(gold_count, present)
        categories, at_stage = self._attribute_false_positives(record)

        emitted = record.values_at(PipelineStage.FINAL_EMITTED)
        return QueryAttribution(
            subject=record.subject, relation=record.relation,
            row_index=record.row_index, gold_count=gold_count,
            survival=survival, present_gold=present, failures=failures,
            false_positives=categories, false_positives_at_stage=at_stage,
            oracle_present_count=oracle.matched,
            empty_gold=(_empty_gold_outcome(record) if gold_count == 0 else None),
            observable=True, calls_used=record.calls_used,
            generated_tokens_used=record.generated_tokens_used,
            verification_calls=record.verification_calls,
            candidate_count=len(
                [c for c in record.candidates if not c.hard_rejected]),
            verifier_candidate_count=len(
                [c for c in record.candidates if c.reached_verifier]),
            final_empty=not emitted,
            failure_state=record.failure_state,
        )

    def attribute_all(
        self, records: Iterable[QueryInferenceRecord],
    ) -> list[QueryAttribution]:
        return [self.attribute(record) for record in records]

    # -- false positives -----------------------------------------------------

    def _attribute_false_positives(
        self, record: QueryInferenceRecord,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Which wrong predictions reached how far.

        Two views of the same evidence, because they answer different
        questions:

        * the **category histogram** partitions each wrong prediction by the
          furthest stage it reached - the last place that could have removed it
          and did not;
        * the **per-stage counts** are cumulative - how many wrong predictions
          were sitting at each stage - which is what a precision-side rate needs.

        Correctness is judged per stage, against that stage's own candidate set,
        because that is the set the evaluator would have scored there.
        """
        categories: dict[str, int] = {c.value: 0 for c in FalsePositiveCategory}
        at_stage: dict[str, int] = {}
        furthest: dict[str, PipelineStage] = {}
        wrong_at: dict[str, set[str]] = {}

        for stage in STAGE_ORDER:
            values = record.values_at(stage)
            attribution = self.gold.attribute(
                record.subject, record.relation, values)
            norms = {attribution.norm_by_candidate.get(v, v) for v in values}
            wrong = {n for n in norms if n not in attribution.matched_norms}
            at_stage[stage.value] = len(wrong)
            wrong_at[stage.value] = wrong
            for norm in norms:
                furthest[norm] = stage

        for norm, stage in furthest.items():
            if norm in wrong_at[stage.value]:
                categories[FP_AT_STAGE[stage].value] += 1
        return categories, at_stage

    # -- the unobservable case -----------------------------------------------

    @staticmethod
    def _unobservable(
        record: QueryInferenceRecord, gold_count: int,
    ) -> QueryAttribution:
        """A query with no finished graph. Nothing is inferred from the absence.

        Every gold object is ``NOT_OBSERVABLE`` rather than ``NEVER_ACQUIRED``:
        a row that crashed before producing a state did not demonstrate a recall
        failure, and recording one would put a fabricated observation into the
        very report that is supposed to say where the real ones are.
        """
        survival = {
            stage.value: StageSurvival(stage, gold_count, 0)
            for stage in STAGE_ORDER
        }
        return QueryAttribution(
            subject=record.subject, relation=record.relation,
            row_index=record.row_index, gold_count=gold_count,
            survival=survival,
            present_gold={stage.value: frozenset() for stage in STAGE_ORDER},
            failures={index: FailureCategory.NOT_OBSERVABLE
                      for index in range(gold_count)},
            false_positives={c.value: 0 for c in FalsePositiveCategory},
            false_positives_at_stage={s.value: 0 for s in STAGE_ORDER},
            oracle_present_count=0, empty_gold=None, observable=False,
            calls_used=record.calls_used,
            generated_tokens_used=record.generated_tokens_used,
            verification_calls=record.verification_calls,
            candidate_count=0, verifier_candidate_count=0,
            final_empty=not record.emitted, failure_state="",
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _union_values(
    record: QueryInferenceRecord, stages: "Sequence[PipelineStage]",
) -> tuple[str, ...]:
    """Every value seen at any of these stages, in stage then first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for stage in stages:
        for value in record.values_at(stage):
            if value not in seen:
                seen.add(value)
                out.append(value)
    return tuple(out)


def _attribute_failures(
    gold_count: int, present: Mapping[str, frozenset[int]],
) -> dict[int, FailureCategory]:
    """Assign each gold object the loss that follows the last stage it survived.

    Deliberately keyed off the **last** stage present rather than the first
    stage missing. Presence is not monotone across the pipeline - Module 8
    emits a numeric cluster median that need never have been generated, so a
    gold value can be absent at ``ACQUIRED`` and present at ``FINAL_EMITTED`` -
    and "earliest *irreversible* loss" is precisely the transition after which
    it never reappears.
    """
    out: dict[int, FailureCategory] = {}
    for index in range(gold_count):
        last: PipelineStage | None = None
        for stage in STAGE_ORDER:
            if index in present.get(stage.value, frozenset()):
                last = stage
        if last is PipelineStage.FINAL_EMITTED:
            out[index] = FailureCategory.SUCCESSFULLY_EMITTED
        elif last is None:
            out[index] = FailureCategory.NEVER_ACQUIRED
        else:
            out[index] = LOSS_AFTER_STAGE[last]
    return out


def _empty_gold_outcome(record: QueryInferenceRecord) -> EmptyGoldOutcome:
    """The three things worth knowing about a query whose gold set is empty."""
    acquired = record.values_at(PipelineStage.ACQUIRED)
    normalized = record.values_at(PipelineStage.NORMALIZED)
    return EmptyGoldOutcome(
        acquisition_empty=not acquired,
        # "A false candidate was proposed" means one entered the graph. A raw
        # fragment the normaliser then rejected cost nothing and was never a
        # proposal the system stood behind.
        proposed_any_candidate=bool(normalized),
        final_empty=not record.values_at(PipelineStage.FINAL_EMITTED),
    )


__all__ = [
    "EmptyGoldOutcome",
    "GoldLeakageError",
    "PRE_FINAL_STAGES",
    "QueryAttribution",
    "StageSurvival",
    "TrainGoldAttribution",
]
