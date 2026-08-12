"""Shared deterministic utilities for active leaderboard repair."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from cover_kbc.normalization.strings import clean_surface, strict_key
from cover_kbc.types import Candidate, Prediction
from cover_kbc.v3_core.hypothesis import QueryHypothesisGraph

from cover_kbc.leaderboard_repair.types import CandidateSignal


STOCK = "companyTradesAtStockExchange"
BORDERS = "countryLandBordersCountry"
CITY = "personHasCityOfDeath"
AREA = "hasArea"
CAPACITY = "hasCapacity"
AWARD = "awardWonBy"


def copy_prediction(prediction: Prediction, values: Sequence[str]) -> Prediction:
    return replace(prediction, object_entities=list(values))


def candidate_signals(
    prediction: Prediction, graph: QueryHypothesisGraph | None = None
) -> list[CandidateSignal]:
    """Collect pre-final candidates without reading labels or external data."""
    out: list[CandidateSignal] = []
    seen: set[tuple[str, str]] = set()

    def add(value: str, source: str, support: int = 0,
            verifier_label: str = "", verifier_margin: float | None = None,
            status: str = "") -> None:
        text = clean_surface(value)
        key = strict_key(text)
        if not key or (source, key) in seen:
            return
        seen.add((source, key))
        out.append(CandidateSignal(
            value=text,
            source=source,
            support=int(support or 0),
            verifier_label=verifier_label or "",
            verifier_margin=verifier_margin,
            status=status or "",
        ))

    for value in prediction.object_entities:
        add(value, "current_prediction", support=2, status="OUTPUT")
    for candidate in prediction.candidates:
        if isinstance(candidate, Candidate):
            add(
                candidate.output_value,
                "prediction_candidate",
                support=candidate.independent_support,
                status=candidate.status.value,
            )
    if graph is not None:
        for hypothesis in graph.hypotheses:
            add(
                hypothesis.display,
                "v3_hypothesis",
                support=hypothesis.independent_support_count,
                verifier_label=hypothesis.verifier_label or "",
                verifier_margin=hypothesis.verifier_margin,
                status=hypothesis.status.value,
            )
    return out


def normalize_award_metadata(value: str) -> str | None:
    """Drop temporal/category wrappers while keeping valid recipient types."""
    text = clean_surface(value)
    if not text:
        return None
    lowered_key = strict_key(text)
    if lowered_key in {"groups none", "individuals none", "none"}:
        return None
    text = re.sub(r"^\s*(?:\d{4}(?:\s*[-/]\s*\d{2,4})?|"
                  r"\d{3,4}s|early|middle|mid|recent|late)\s*:\s*",
                  "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:individuals?|persons?|people|groups?|"
                  r"organisations?|organizations?|projects?|recipients?|"
                  r"winners?)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\((?:second|third|fourth|fifth|another|repeat)"
                  r"(?:\s+time)?\)\s*$", "", text, flags=re.IGNORECASE)
    text = clean_surface(text)
    if not text or strict_key(text) in {"none", "no recipient", "no recipients"}:
        return None
    return text
