"""Join TRAIN gold to collected action outcomes — **offline, after inference**.

Two of §17's six estimates, ``Ĝ_verified`` and ``F̂P``, are about correctness,
and correctness is not something the frozen system observed. The collection
recorded *which candidate identities* each action touched; whether those
identities were right is decided here, once, against
``benchmark/data/train.jsonl``.

The boundary this file sits on is the one the whole milestone depends on:

* **Gold never reaches inference.** It is read here, after the run, by a
  derivation step that calls no model. The collection telemetry it is joined to
  was produced without it.
* **Gold never reaches the production artifact.** What leaves this module is a
  per-action *count* of correct and incorrect identities. No object string, no
  alias, no subject-specific answer is carried forward - see
  :mod:`cover_kbc.controller_calibration.derivation`, which consumes counts and
  serialises statistics.

Correctness is judged by the **pinned official evaluator itself**, never by a
local re-implementation. ``benchmark/evaluate.py`` owns normalisation, alias
handling and the 5 % numeric tolerance; asking it about a one-element
prediction list is asking exactly the question it answers for a full row, and
guarantees that "this action produced a true positive" means the same thing
here as it will on the leaderboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cover_kbc.evaluation.official import (
    evaluator_checksum,
    load_official_evaluator,
    relation_types,
)

#: The official numeric tolerance, taken from the harness rather than restated.
from cover_kbc.evaluation.harness import DEFAULT_TOLERANCE


class GoldJoinError(ValueError):
    """TRAIN gold could not be joined to the collected telemetry."""


@dataclass(frozen=True)
class GoldRow:
    """One TRAIN row's answer set, in the evaluator's own alias-list shape."""

    subject: str
    relation: str
    #: ``List[List[str]]`` - one alias list per gold object, as the evaluator
    #: reads it. Kept verbatim so no normalisation of ours can drift from its.
    aliases: tuple[tuple[str, ...], ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.relation)

    @property
    def size(self) -> int:
        return len(self.aliases)


@dataclass(frozen=True)
class GoldIndex:
    """Every TRAIN row, keyed by ``(SubjectEntity, Relation)``.

    Provenance travels with it: the evaluator checksum and the tolerance are
    part of what makes a derived false-positive rate reproducible, and a later
    audit must be able to see which scorer produced the labels.
    """

    rows: Mapping[tuple[str, str], GoldRow]
    evaluator_sha256: str
    tolerance: float
    relation_types: Mapping[str, str]

    def __len__(self) -> int:
        return len(self.rows)

    def relations(self) -> tuple[str, ...]:
        return tuple(sorted({relation for _, relation in self.rows}))

    def attribute(
        self, subject: str, relation: str, candidates: Sequence[str],
    ) -> "GoldAttribution":
        """Which of these candidates are correct, **one gold entity at most once**.

        The pinned evaluator scores a row, not a candidate: strings go through
        maximum-cardinality bipartite matching between unique normalised
        predictions and gold entities, numerics through greedy one-to-one
        matching inside the tolerance band. Asking it about one candidate at a
        time and summing the answers is a different question, and it gives a
        different number: ``Max Theiler`` and ``Maks Teyler`` are two aliases of
        one gold entity, so the row scores TP=1 while two independent labels
        say ``True, True`` (Audit 0048).

        So attribution is done once, over the whole candidate set, using the
        evaluator's own machinery - and the resulting cardinality is checked
        against the evaluator's own count for the same input, so an adaptation
        that drifted from it fails rather than silently disagreeing.

        Candidates are consumed in the order given. That order is the caller's
        priority: an earlier candidate keeps its gold entity, because an
        augmenting-path matching never unmatches a vertex it has already
        matched. Cardinality is unaffected either way.

        Raises:
            GoldJoinError: if the row is unknown, or if the attribution and the
                official scorer disagree about how many true positives there
                are.
        """
        row = self.rows.get((subject, relation))
        if row is None:
            raise GoldJoinError(
                f"no TRAIN gold row for {subject!r}/{relation!r}; the telemetry "
                "and the gold file describe different runs"
            )
        evaluator = load_official_evaluator()
        gts = [list(aliases) for aliases in row.aliases]

        # Duplicate surfaces collapse before matching, exactly as
        # ``evaluate_per_sr_pair`` collapses them before scoring a row: two
        # spellings of one prediction are one prediction. It keeps the *first
        # raw surface* of each normalised group and scores that, which matters
        # for numerics - ``normalize_string`` turns "5556.0" into "5556 0",
        # which parses as no number at all.
        norms: list[str] = []
        surfaces: list[str] = []
        by_norm: dict[str, list[str]] = {}
        for candidate in candidates:
            if not candidate:
                continue
            key = evaluator.normalize_string(candidate)
            if key not in by_norm:
                by_norm[key] = []
                norms.append(key)
                surfaces.append(candidate)
            by_norm[key].append(candidate)

        if not gts or not surfaces:
            return GoldAttribution(
                matched_norms=frozenset(),
                norm_by_candidate={surface: norm
                                   for norm, group in by_norm.items()
                                   for surface in group},
                gold_size=len(gts))

        rel_type = self.relation_types.get(relation, "string")
        if rel_type == "numeric":
            indices = _numeric_attribution(
                surfaces, gts, evaluator, self.tolerance)
            official = evaluator.numeric_true_positives(
                surfaces, gts, self.tolerance)
        else:
            indices = _string_attribution(surfaces, gts, evaluator)
            official = evaluator.string_true_positives(surfaces, gts)
        matched_norms = [norms[index] for index in indices]

        if len(matched_norms) != official:
            raise GoldJoinError(
                f"{subject!r}/{relation!r}: attribution matched "
                f"{len(matched_norms)} candidate(s) but the official scorer "
                f"counts {official} true positive(s); the adaptation has "
                "drifted from the pinned evaluator and must not be used"
            )
        return GoldAttribution(
            matched_norms=frozenset(matched_norms),
            norm_by_candidate={surface: norm
                               for norm, surfaces in by_norm.items()
                               for surface in surfaces},
            gold_size=len(gts))


def _string_attribution(
    surfaces: Sequence[str], gts: Sequence[Sequence[str]], evaluator,
) -> list[int]:
    """Kuhn's augmenting-path matching, over the evaluator's own alias sets.

    The same algorithm ``string_true_positives`` runs, kept here only because
    the evaluator returns a *count* and attribution needs the assignment. The
    caller checks the cardinality against the evaluator's own answer, so this
    cannot quietly become an approximation of it.

    Predictions are processed in the order given, and an augmenting path never
    unmatches an already-matched prediction, so an earlier candidate keeps its
    gold entity whenever some maximum matching lets it. That is how the caller
    expresses precedence without changing the score.
    """
    alias_sets = [
        {evaluator.normalize_string(alias) for alias in aliases}
        for aliases in gts
    ]
    # Normalised here, exactly as ``string_true_positives`` does internally.
    normalised = [evaluator.normalize_string(surface) for surface in surfaces]
    adjacency = [
        {index for index, aliases in enumerate(alias_sets) if norm in aliases}
        for norm in normalised
    ]
    gold_to_pred: dict[int, int] = {}

    def augment(pred: int, visited: set[int]) -> bool:
        for gold in sorted(adjacency[pred]):
            if gold in visited:
                continue
            visited.add(gold)
            if gold not in gold_to_pred or augment(gold_to_pred[gold], visited):
                gold_to_pred[gold] = pred
                return True
        return False

    for index in range(len(normalised)):
        augment(index, set())
    return sorted(gold_to_pred.values())


def _numeric_attribution(
    surfaces: Sequence[str], gts: Sequence[Sequence[str]], evaluator,
    tolerance: float,
) -> list[int]:
    """The evaluator's greedy one-to-one numeric matching, made attributable.

    ``numeric_true_positives`` walks predictions in order and takes the first
    unmatched gold value inside the tolerance band. That order-sensitive greedy
    *is* the official algorithm, so it is reproduced exactly rather than
    improved on - a maximum matching here would score differently from the
    leaderboard. It reads the **raw** surface, not a normalised one, because
    normalisation strips the decimal point.

    Returns:
        Indices into ``surfaces`` that were assigned a gold value.
    """
    gold_numbers = [
        evaluator.try_parse_number(aliases[0]) if aliases else None
        for aliases in gts
    ]
    matched_gold: set[int] = set()
    matched: list[int] = []
    for position, surface in enumerate(surfaces):
        value = evaluator.try_parse_number(surface)
        if value is None:
            continue
        for index, gold in enumerate(gold_numbers):
            if index in matched_gold or gold is None or gold == 0:
                continue
            if abs(value - gold) / gold <= tolerance:
                matched_gold.add(index)
                matched.append(position)
                break
    return matched


@dataclass(frozen=True)
class GoldAttribution:
    """One deterministic one-to-one assignment of candidates to gold entities.

    Counting is over *normalised* forms, never raw candidate keys, so two
    spellings of one prediction cannot both earn gain - the same reason the
    evaluator deduplicates a row's predictions before scoring it.
    """

    #: Normalised prediction forms that were assigned a gold entity. Its size
    #: is the official row-level true-positive count for this candidate set.
    matched_norms: frozenset[str]
    #: Raw candidate key -> its normalised form.
    norm_by_candidate: Mapping[str, str]
    gold_size: int

    @property
    def matched_gold(self) -> int:
        return len(self.matched_norms)

    def is_correct(self, candidate: str) -> bool:
        norm = self.norm_by_candidate.get(candidate)
        return norm is not None and norm in self.matched_norms

    def count_correct(self, candidates: Sequence[str]) -> int:
        """Distinct gold entities these candidates account for."""
        return len({
            norm for candidate in candidates
            if (norm := self.norm_by_candidate.get(candidate)) is not None
            and norm in self.matched_norms
        })

    def count_incorrect(self, candidates: Sequence[str]) -> int:
        """Distinct predictions among these that matched no gold entity."""
        return len({
            norm for candidate in candidates
            if (norm := self.norm_by_candidate.get(candidate)) is not None
            and norm not in self.matched_norms
        })


@dataclass(frozen=True)
class ActionGoldEffect:
    """What one executed action did, scored — as **counts, never strings**.

    This is the whole of what crosses from the gold side to the derivation
    side. ``expected_verified_gain`` is built from :attr:`supported_correct`
    and ``expected_fp`` from :attr:`supported_incorrect`; neither needs to know
    *which* object it was, and the production artifact therefore cannot carry
    one.
    """

    operation_id: str
    relation: str
    #: Candidates this action moved toward acceptance that gold agrees with.
    supported_correct: int = 0
    #: ...and that gold does not. §17's ``F̂P`` is built from these.
    supported_incorrect: int = 0
    #: Candidates this action contradicted that gold agrees with (a mistake).
    contradicted_correct: int = 0
    #: ...and that gold also rejects (a correct rejection).
    contradicted_incorrect: int = 0
    #: Candidates a §14 probe named that the graph did not hold, and gold does.
    #: Recall the action genuinely produced, even though nothing was minted.
    named_correct: int = 0
    named_incorrect: int = 0
    #: Gold objects for the row, so a per-relation rate has a denominator.
    gold_size: int = 0
    #: Distinct gold entities this action accounted for, across **all**
    #: categories. Bounded by the official row-level true-positive count for
    #: the action's candidate set, and the reason per-category counts cannot be
    #: summed into more gain than the row can supply.
    distinct_correct: int = 0

    @property
    def verified_gain(self) -> float:
        """``Ĝ_verified``: correct objects this action moved toward acceptance."""
        return float(self.supported_correct)

    @property
    def false_positive_rate(self) -> float:
        """``F̂P`` in [0, 1]: of what this action supported, how much was wrong.

        Zero when the action supported nothing - an action that asserted
        nothing introduced no false positive, which is different from an action
        that asserted only wrong things.
        """
        total = self.supported_correct + self.supported_incorrect
        return (self.supported_incorrect / total) if total else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id, "Relation": self.relation,
            "supported_correct": self.supported_correct,
            "supported_incorrect": self.supported_incorrect,
            "contradicted_correct": self.contradicted_correct,
            "contradicted_incorrect": self.contradicted_incorrect,
            "named_correct": self.named_correct,
            "named_incorrect": self.named_incorrect,
            "gold_size": self.gold_size,
            "distinct_correct": self.distinct_correct,
            "verified_gain": self.verified_gain,
            "false_positive_rate": self.false_positive_rate,
        }


def load_gold(path: str | Path, *, expected_rows: int | None = None) -> GoldIndex:
    """Read TRAIN through the evaluator's own reader.

    Args:
        path: ``benchmark/data/train.jsonl``.
        expected_rows: refuse a file of a different size. The row count is part
            of the dataset's identity, and a derivation joined against a
            different TRAIN is not the derivation it claims to be.

    Raises:
        GoldJoinError: on a malformed file, a duplicate ``(subject, relation)``
            identity, or a row count that does not match.
    """
    evaluator = load_official_evaluator()
    source = Path(path)
    if not source.is_file():
        raise GoldJoinError(f"no TRAIN gold file at {source}")
    try:
        raw = evaluator.read_jsonl_file(str(source))
    except Exception as error:                                  # noqa: BLE001
        raise GoldJoinError(f"{source}: unreadable JSONL ({error})") from None

    if expected_rows is not None and len(raw) != expected_rows:
        raise GoldJoinError(
            f"{source} holds {len(raw)} rows, expected {expected_rows}; this is "
            "not the split the collection was run against"
        )

    rows: dict[tuple[str, str], GoldRow] = {}
    for number, entry in enumerate(raw, 1):
        try:
            subject = str(entry["SubjectEntity"])
            relation = str(entry["Relation"])
            objects = entry["ObjectEntities"]
        except (KeyError, TypeError) as error:
            raise GoldJoinError(
                f"{source}:{number}: not an official TRAIN row ({error})"
            ) from None
        aliases: list[tuple[str, ...]] = []
        for item in objects or ():
            # The official format is a list of alias lists; one older shape
            # wraps bare strings. The evaluator tolerates both, so this does.
            if isinstance(item, str):
                aliases.append((item,))
            elif isinstance(item, (list, tuple)):
                aliases.append(tuple(str(a) for a in item))
            else:
                raise GoldJoinError(
                    f"{source}:{number}: ObjectEntities entry {item!r} is "
                    "neither a string nor an alias list"
                )
        key = (subject, relation)
        if key in rows:
            raise GoldJoinError(
                f"{source}:{number}: duplicate query identity {key}; a gold set "
                "that names one query twice cannot label an action unambiguously"
            )
        rows[key] = GoldRow(subject=subject, relation=relation,
                            aliases=tuple(aliases))

    return GoldIndex(
        rows=rows, evaluator_sha256=evaluator_checksum(),
        tolerance=DEFAULT_TOLERANCE, relation_types=relation_types(),
    )


def score_actions(
    records: Sequence[Any], gold: GoldIndex,
) -> dict[str, ActionGoldEffect]:
    """Label every executed action's candidate effect, keyed by operation id.

    **One attribution per action, over every category at once.** Supported,
    contradicted and named candidates are matched against the row's gold
    entities in a single one-to-one assignment, so the action as a whole cannot
    claim one gold entity twice - not through two aliases, not through two
    numerics inside one tolerance band, and not by appearing in two effect
    categories (Audit 0048 P1-2).

    Categories are consumed in a fixed precedence - supported, then named, then
    contradicted - because an augmenting-path matching keeps what it has
    already matched. Supported first means a correct candidate the action
    *asserted* is credited as such rather than having its gold entity consumed
    by the same entity appearing in a weaker category. The precedence changes
    which candidate is credited, never how many are.

    A key legitimately appears in more than one category: the execution seam
    records support and contradiction independently, and one action can do
    both to one candidate. That is why the counts below are per-category views
    of one assignment rather than three separate scorings.

    Only executed actions are scored: a legal-but-unselected branch produced no
    candidate effect, and giving it a gold label would invent an observation.

    Raises:
        GoldJoinError: if a record names a query the gold file does not hold,
            or if an attribution disagrees with the official scorer.
    """
    out: dict[str, ActionGoldEffect] = {}

    for record in records:
        if not record.executed:
            continue
        row = gold.rows.get((record.subject, record.relation))
        if row is None:
            raise GoldJoinError(
                f"telemetry names {record.subject!r}/{record.relation!r}, which "
                "the TRAIN gold file does not contain"
            )
        outcome = record.outcome
        supported = tuple(outcome.candidates_supported)
        contradicted = tuple(outcome.candidates_contradicted)
        named = tuple(outcome.candidates_named)

        # One ordered candidate set: precedence first, then the key itself so
        # the assignment is reproducible across processes.
        ordered: list[str] = []
        seen: set[str] = set()
        for group in (sorted(supported), sorted(named), sorted(contradicted)):
            for candidate in group:
                if candidate not in seen:
                    seen.add(candidate)
                    ordered.append(candidate)

        attribution = gold.attribute(record.subject, record.relation, ordered)

        # The invariant Audit 0048 requires: the distinct gold entities this
        # action accounts for can never exceed what the official scorer would
        # award its candidate set, nor the number of gold entities there are.
        distinct_correct = attribution.count_correct(ordered)
        if distinct_correct > attribution.matched_gold or (
                distinct_correct > row.size):
            raise GoldJoinError(
                f"{record.operation_id}: attributed {distinct_correct} correct "
                f"candidate(s) against {attribution.matched_gold} matched gold "
                f"entit(ies) of {row.size}; one gold entity would be claimed "
                "more than once"
            )

        out[record.operation_id] = ActionGoldEffect(
            operation_id=record.operation_id, relation=record.relation,
            supported_correct=attribution.count_correct(supported),
            supported_incorrect=attribution.count_incorrect(supported),
            contradicted_correct=attribution.count_correct(contradicted),
            contradicted_incorrect=attribution.count_incorrect(contradicted),
            named_correct=attribution.count_correct(named),
            named_incorrect=attribution.count_incorrect(named),
            gold_size=row.size,
            distinct_correct=distinct_correct,
        )
    return out


__all__ = [
    "ActionGoldEffect",
    "GoldAttribution",
    "GoldIndex",
    "GoldJoinError",
    "GoldRow",
    "load_gold",
    "score_actions",
]
