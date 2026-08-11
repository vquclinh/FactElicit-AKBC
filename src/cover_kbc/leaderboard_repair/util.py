"""Shared deterministic utilities for V3.2 repair."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Sequence

from cover_kbc.normalization.numeric import cluster_values, format_numeric
from cover_kbc.normalization.strings import alias_hint_key, clean_surface, strict_key
from cover_kbc.types import Candidate, EmptyReason, Prediction
from cover_kbc.v3_core.hypothesis import QueryHypothesisGraph

from cover_kbc.leaderboard_repair.types import CandidateSignal


STOCK = "companyTradesAtStockExchange"
BORDERS = "countryLandBordersCountry"
CITY = "personHasCityOfDeath"
AREA = "hasArea"
CAPACITY = "hasCapacity"
AWARD = "awardWonBy"

_PUNCT_WORD = re.compile(r"[^a-z0-9]+")
_TICKER = re.compile(r"^[A-Z.]{1,6}$")
_INDEX_MARKERS = (
    " index", "indices", "average", "composite", " s&p ", " ftse ", " dax",
    " nikkei", " hang seng", " dow jones",
)
_SEGMENT_MARKERS = (
    "segment", "board", "market tier", "capital market", "global market",
    "main market", "growth market", "venture market",
)
_NON_EXCHANGE_ORGS = (
    "broker", "brokerage", "clearing", "depository", "depositary",
    "settlement", "registrar", "transfer agent", "custodian",
)
_STOCK_VENUE_WORDS = (
    "exchange", "stock market", "securities market", "bourse", "börse",
    "bolsa", "borsa", "boursa", "marketplace",
)
_SAFE_EXCHANGE_ACRONYM_PARTS = ("SE", "EX")


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


def signal_values(signals: Iterable[CandidateSignal]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for signal in signals:
        key = strict_key(signal.value)
        if key and key not in seen:
            seen.add(key)
            out.append(signal.value)
    return out


def dedupe_aliases(values: Iterable[str], *, model_groups: dict[str, str] | None = None) -> list[str]:
    """Generic alias dedupe using evaluator key, article fold and acronyms."""
    surfaces = [clean_surface(v) for v in values if clean_surface(str(v))]
    if not surfaces:
        return []
    parent: dict[str, str] = {strict_key(v): strict_key(v) for v in surfaces if strict_key(v)}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

    by_alias: dict[str, str] = {}
    for value in surfaces:
        key = strict_key(value)
        hint = alias_hint_key(value)
        if hint in by_alias:
            union(key, by_alias[hint])
        else:
            by_alias[hint] = key

    acronyms = {_acronym(value): strict_key(value) for value in surfaces if _acronym(value)}
    for value in surfaces:
        key = strict_key(value)
        if key.upper() in acronyms:
            union(key, acronyms[key.upper()])

    if model_groups:
        group_owner: dict[str, str] = {}
        for value, group in model_groups.items():
            key = strict_key(value)
            if key not in parent:
                continue
            if group in group_owner:
                union(key, group_owner[group])
            else:
                group_owner[group] = key

    chosen: dict[str, str] = {}
    for value in surfaces:
        key = strict_key(value)
        if not key:
            continue
        root = find(key)
        current = chosen.get(root)
        if current is None or _surface_rank(value) < _surface_rank(current):
            chosen[root] = value
    emitted: set[str] = set()
    out: list[str] = []
    for value in surfaces:
        key = strict_key(value)
        if not key:
            continue
        representative = chosen[find(key)]
        rep_key = strict_key(representative)
        if rep_key in emitted:
            continue
        emitted.add(rep_key)
        out.append(representative)
    return out


def _surface_rank(value: str) -> tuple[int, int, str]:
    """Prefer fuller names over bare acronyms, then shorter clean surfaces."""
    words = value.split()
    acronym_penalty = 1 if _TICKER.match(value) and len(words) == 1 else 0
    return (acronym_penalty, len(value), value.lower())


def _acronym(value: str) -> str:
    words = [
        _PUNCT_WORD.sub("", word).upper()
        for word in clean_surface(value).split()
        if _PUNCT_WORD.sub("", word)
    ]
    ignored = {"THE", "AND", "OF", "DE", "DA", "DEL", "LA", "EL"}
    letters = [word[0] for word in words if word not in ignored]
    return "".join(letters) if len(letters) >= 2 else ""


def stock_wrong_type_reason(value: str, subject: str) -> str | None:
    """Reject only generic non-exchange answer types; no company fact table."""
    text = clean_surface(value)
    if not text:
        return "empty"
    lowered = f" {text.lower()} "
    subject_key = strict_key(subject)
    key = strict_key(text)
    if key and subject_key and (key == subject_key or key in subject_key.split()):
        return "company_or_subject_name"
    if _TICKER.match(text):
        if any(part in text for part in _SAFE_EXCHANGE_ACRONYM_PARTS):
            return None
        if text.upper() in {"NYSE", "NASDAQ", "LSE", "TSX", "ASX", "HKEX"}:
            return None
        return "ticker_only"
    if any(marker in lowered for marker in _INDEX_MARKERS):
        return "index_name"
    if any(marker in lowered for marker in _SEGMENT_MARKERS):
        return "market_segment"
    if any(marker in lowered for marker in _NON_EXCHANGE_ORGS):
        return "broker_clearing_or_depositary"
    # City-only outputs are usually one or two words with no venue marker. Do
    # not reject if the value carries any exchange/venue word.
    if len(text.split()) <= 2 and not any(marker in lowered for marker in _STOCK_VENUE_WORDS):
        return "city_or_bare_location"
    return None


def plausible_stock_exchange(value: str, subject: str) -> bool:
    return stock_wrong_type_reason(value, subject) is None


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


def numeric_cluster_output(
    values: Sequence[float], *, tolerance: float, integer_only: bool
) -> str | None:
    clusters = cluster_values(values, threshold=tolerance)
    if not clusters or clusters[0].size < 2:
        return None
    return format_numeric(clusters[0].representative, integer_only=integer_only)


def low_confidence_numeric(prediction: Prediction) -> bool:
    if not prediction.object_entities:
        return True
    return any(c.independent_support <= 1 for c in prediction.candidates)


def confident_no_border(prediction: Prediction) -> bool:
    return (
        not prediction.object_entities
        and prediction.empty_reason is EmptyReason.CONFIDENT_NEGATIVE_GATE
    )
