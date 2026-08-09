"""Compare two model strategies on TRAIN, by relation and by action.

Overall F1 cannot answer the question this milestone exists to ask. "Is Gemma a
better factual enumerator than Mistral" and "is Nemotron a better structural
reasoner than Mistral" are different claims, and a single number hides both -
a portfolio can find more true positives *and* introduce more false positives
and land on the same F1.

So the comparison is decomposed the way the architecture is:

* **candidate generation** - what recall did each strategy's enumerator reach,
  at what precision, and how many true positives are *novel* relative to the
  baseline rather than the same ones found again;
* **verification and structural work** - of the candidates that existed, which
  true positives were preserved, which were recovered, which false positives
  were prevented, and which were introduced.

Both halves carry their own cost: physical calls, prompt tokens, generated
tokens, latency. A strategy that wins on recall and costs three times as much
is a different trade, and §17's utility is priced in exactly these terms.

**TRAIN only.** Every entry point here refuses a split that is not train, and
the module never imports the evaluator. There is no VAL comparison and no TEST
access, so nothing here can be fitted to a label the leaderboard holds.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.models.strategy import ModelStrategy


class BakeoffError(RuntimeError):
    """A comparison that would not mean what it appears to mean."""


#: The only split a bake-off may read. Stated as a constant so the refusal is
#: one check in one place rather than a convention.
ALLOWED_SPLIT = "train"


def require_train(split: str) -> None:
    """Refuse any split but TRAIN.

    Raises:
        BakeoffError: for anything else. A model choice justified by VAL is a
            model choice fitted to the leaderboard, and TEST has no labels here
            at all.
    """
    if str(split) != ALLOWED_SPLIT:
        raise BakeoffError(
            f"a model bake-off may only read {ALLOWED_SPLIT!r}, not "
            f"{split!r}; comparing strategies on val or test would fit the "
            "model choice to the labels the submission is scored against")


def _normalise(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


# --------------------------------------------------------------------------
# candidate generation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateOutcome:
    """What one strategy's acquisition produced for one query."""

    relation: str
    subject: str
    row_index: int
    #: Every candidate surfaced, in the order acquisition produced them.
    candidates: tuple[str, ...]
    physical_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class CandidateReport:
    """Recall, precision, novelty and cost for one relation."""

    relation: str
    queries: int = 0
    gold: int = 0
    true_positives: int = 0
    false_positives: int = 0
    #: True positives this strategy found that the reference did not. The
    #: number that justifies a heterogeneous portfolio at all.
    novel_true_positives: int = 0
    #: True positives the reference found and this strategy lost.
    lost_true_positives: int = 0
    #: Candidates offered more than once for the same query.
    redundant_candidates: int = 0
    physical_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.gold if self.gold else 0.0

    @property
    def precision(self) -> float:
        surfaced = self.true_positives + self.false_positives
        return self.true_positives / surfaced if surfaced else 0.0

    @property
    def calls_per_query(self) -> float:
        return self.physical_calls / self.queries if self.queries else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "relation": self.relation, "queries": self.queries,
            "gold": self.gold, "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "novel_true_positives": self.novel_true_positives,
            "lost_true_positives": self.lost_true_positives,
            "redundant_candidates": self.redundant_candidates,
            "recall": round(self.recall, 6),
            "precision": round(self.precision, 6),
            "physical_calls": self.physical_calls,
            "calls_per_query": round(self.calls_per_query, 4),
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "latency_ms": round(self.latency_ms, 2),
        }


def compare_candidates(
    outcomes: Sequence[CandidateOutcome],
    gold: Mapping[tuple[str, str, int], Sequence[str]],
    *, reference: Sequence[CandidateOutcome] | None = None,
    split: str = ALLOWED_SPLIT,
) -> dict[str, CandidateReport]:
    """Per-relation candidate analysis, optionally against a reference run.

    Args:
        outcomes: what the strategy under test surfaced.
        gold: TRAIN objects per query. TRAIN gold is a *measurement* input
            here, exactly as it is for the M20/M21 derivation - it never
            reaches inference.
        reference: the baseline's outcomes, for novelty. Omit for a standalone
            profile.
        split: refused unless ``train``.

    Returns:
        One report per relation, keyed by relation.
    """
    require_train(split)
    reference_by_query = {
        (o.relation, o.subject, o.row_index): {_normalise(c) for c in o.candidates}
        for o in (reference or ())
    }

    reports: dict[str, CandidateReport] = {}
    for outcome in outcomes:
        report = reports.setdefault(
            outcome.relation, CandidateReport(relation=outcome.relation))
        key = (outcome.relation, outcome.subject, outcome.row_index)
        truth = {_normalise(o) for o in gold.get(key, ())}

        counts = Counter(_normalise(c) for c in outcome.candidates)
        unique = set(counts)
        report.queries += 1
        report.gold += len(truth)
        report.true_positives += len(unique & truth)
        report.false_positives += len(unique - truth)
        report.redundant_candidates += sum(n - 1 for n in counts.values() if n > 1)
        report.physical_calls += outcome.physical_calls
        report.prompt_tokens += outcome.prompt_tokens
        report.generated_tokens += outcome.generated_tokens
        report.latency_ms += outcome.latency_ms

        if key in reference_by_query:
            baseline_found = reference_by_query[key] & truth
            mine = unique & truth
            report.novel_true_positives += len(mine - baseline_found)
            report.lost_true_positives += len(baseline_found - mine)
    return reports


# --------------------------------------------------------------------------
# verification and structural work
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationOutcome:
    """One executed Layer-4 action, as the owner reported it."""

    relation: str
    action_family: str
    #: ``VALID`` / ``INVALID`` / ``UNKNOWN`` for M17; §14's own outcome for M18.
    verdict: str
    #: Whether the candidate this action judged is in TRAIN gold.
    candidate_is_true: bool
    #: Whether the candidate survived into the final prediction.
    accepted: bool
    #: Whether the *reference* strategy accepted the same candidate.
    reference_accepted: bool | None = None
    disagreement: float = 0.0
    physical_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float = 0.0
    #: The successor state bin this action moved the query into, if recorded.
    successor_state: str = ""


@dataclass
class VerificationReport:
    """What verification and structural work changed, per relation+family."""

    relation: str
    action_family: str
    actions: int = 0
    #: True and accepted; the reference also accepted it.
    true_positives_preserved: int = 0
    #: True and accepted; the reference did not accept it.
    true_positives_recovered: int = 0
    #: False and rejected; the reference accepted it.
    false_positives_prevented: int = 0
    #: False and accepted; the reference rejected it.
    false_positives_introduced: int = 0
    unknown_verdicts: int = 0
    disagreement_total: float = 0.0
    physical_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    latency_ms: float = 0.0
    successor_states: Counter = field(default_factory=Counter)

    @property
    def unknown_rate(self) -> float:
        return self.unknown_verdicts / self.actions if self.actions else 0.0

    @property
    def mean_disagreement(self) -> float:
        return self.disagreement_total / self.actions if self.actions else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "relation": self.relation, "action_family": self.action_family,
            "actions": self.actions,
            "true_positives_preserved": self.true_positives_preserved,
            "true_positives_recovered": self.true_positives_recovered,
            "false_positives_prevented": self.false_positives_prevented,
            "false_positives_introduced": self.false_positives_introduced,
            "unknown_verdicts": self.unknown_verdicts,
            "unknown_rate": round(self.unknown_rate, 6),
            "mean_disagreement": round(self.mean_disagreement, 6),
            "physical_calls": self.physical_calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "successor_states": dict(sorted(self.successor_states.items())),
        }


def compare_verification(
    outcomes: Sequence[VerificationOutcome], *, split: str = ALLOWED_SPLIT,
) -> dict[tuple[str, str], VerificationReport]:
    """Per relation and action family, what Layer 4 actually changed.

    Preserved / recovered / prevented / introduced are counted against the
    reference decision carried on each outcome, so "this strategy is better at
    verification" becomes four separate numbers instead of one.
    """
    require_train(split)
    reports: dict[tuple[str, str], VerificationReport] = {}
    for outcome in outcomes:
        key = (outcome.relation, outcome.action_family)
        report = reports.setdefault(key, VerificationReport(*key))
        report.actions += 1
        report.physical_calls += outcome.physical_calls
        report.prompt_tokens += outcome.prompt_tokens
        report.generated_tokens += outcome.generated_tokens
        report.latency_ms += outcome.latency_ms
        report.disagreement_total += outcome.disagreement
        if str(outcome.verdict).strip().upper() == "UNKNOWN":
            report.unknown_verdicts += 1
        if outcome.successor_state:
            report.successor_states[outcome.successor_state] += 1

        was = outcome.reference_accepted
        if was is None:
            continue
        if outcome.candidate_is_true and outcome.accepted:
            if was:
                report.true_positives_preserved += 1
            else:
                report.true_positives_recovered += 1
        elif not outcome.candidate_is_true:
            if was and not outcome.accepted:
                report.false_positives_prevented += 1
            elif not was and outcome.accepted:
                report.false_positives_introduced += 1
    return reports


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def candidate_table(reports: Mapping[str, CandidateReport]) -> str:
    """A fixed-width table of the candidate analysis, for the paper."""
    header = (f"{'relation':30} {'recall':>7} {'prec':>7} {'TP':>5} {'FP':>6} "
              f"{'novel':>6} {'lost':>5} {'redun':>6} {'calls':>7} {'gen tok':>8}")
    lines = [header, "-" * len(header)]
    for relation in sorted(reports):
        r = reports[relation]
        lines.append(
            f"{relation:30} {r.recall:7.4f} {r.precision:7.4f} "
            f"{r.true_positives:5} {r.false_positives:6} "
            f"{r.novel_true_positives:6} {r.lost_true_positives:5} "
            f"{r.redundant_candidates:6} {r.physical_calls:7} "
            f"{r.generated_tokens:8}")
    return "\n".join(lines)


def verification_table(
    reports: Mapping[tuple[str, str], VerificationReport],
) -> str:
    header = (f"{'relation':30} {'family':22} {'n':>4} {'TPkeep':>7} "
              f"{'TPrec':>6} {'FPprev':>7} {'FPnew':>6} {'UNK':>6} {'calls':>6}")
    lines = [header, "-" * len(header)]
    for key in sorted(reports):
        r = reports[key]
        lines.append(
            f"{r.relation:30} {r.action_family:22} {r.actions:4} "
            f"{r.true_positives_preserved:7} {r.true_positives_recovered:6} "
            f"{r.false_positives_prevented:7} "
            f"{r.false_positives_introduced:6} {r.unknown_rate:6.3f} "
            f"{r.physical_calls:6}")
    return "\n".join(lines)


@dataclass(frozen=True)
class BakeoffResult:
    """One strategy's TRAIN profile, ready to serialise into an ablation row."""

    strategy: ModelStrategy
    candidates: Mapping[str, CandidateReport]
    verification: Mapping[tuple[str, str], VerificationReport]

    def to_json(self) -> dict[str, Any]:
        return {
            "model_strategy": self.strategy.value,
            "split": ALLOWED_SPLIT,
            "candidates": [self.candidates[r].to_json()
                           for r in sorted(self.candidates)],
            "verification": [self.verification[k].to_json()
                             for k in sorted(self.verification)],
        }

    def tables(self) -> str:
        return (f"model_strategy = {self.strategy.value}   (split: "
                f"{ALLOWED_SPLIT})\n\ncandidate generation\n"
                f"{candidate_table(self.candidates)}\n\n"
                f"verification and structural work\n"
                f"{verification_table(self.verification)}")


def profile_strategy(
    strategy: ModelStrategy,
    outcomes: Sequence[CandidateOutcome],
    gold: Mapping[tuple[str, str, int], Sequence[str]],
    verifications: Sequence[VerificationOutcome],
    *, reference: Sequence[CandidateOutcome] | None = None,
    split: str = ALLOWED_SPLIT,
) -> BakeoffResult:
    """Both halves of one strategy's TRAIN profile."""
    require_train(split)
    return BakeoffResult(
        strategy=strategy,
        candidates=compare_candidates(
            outcomes, gold, reference=reference, split=split),
        verification=compare_verification(verifications, split=split),
    )


def ablation_rows(results: Iterable[BakeoffResult]) -> list[dict[str, Any]]:
    """One row per strategy per relation, for the paper's ablation table."""
    rows: list[dict[str, Any]] = []
    for result in results:
        for relation in sorted(result.candidates):
            report = result.candidates[relation]
            rows.append({"model_strategy": result.strategy.value,
                         **report.to_json()})
    return rows


__all__ = [
    "ALLOWED_SPLIT",
    "BakeoffError",
    "BakeoffResult",
    "CandidateOutcome",
    "CandidateReport",
    "VerificationOutcome",
    "VerificationReport",
    "ablation_rows",
    "candidate_table",
    "compare_candidates",
    "compare_verification",
    "profile_strategy",
    "require_train",
    "verification_table",
]
