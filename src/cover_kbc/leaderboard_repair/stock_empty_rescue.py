"""Profile F1 Mistral stock empty-row rescue."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import CandidateSignal, RowRepairRecord


STOCK_EMPTY_RESCUE_FEATURE = "MistralStockEmptyRescue"
STOCK_EMPTY_RESCUE_MODE = "EMPTY_ONLY_STRONG_CONSENSUS"
VALID_EXCHANGE_SET = "VALID_EXCHANGE_SET"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"
STRONG_CONSENSUS = "STOCK_EMPTY_REPAIR_STRONG_CONSENSUS"
REJECTED = "STOCK_EMPTY_REPAIR_REJECTED"

VIEW_IDS = (
    "stock_empty_rescue_s1_direct_listing",
    "stock_empty_rescue_s2_primary_listing",
    "stock_empty_rescue_s3_common_shares",
    "stock_empty_rescue_s4_anti_prior",
)

STOCK_SYSTEM_PROMPT = (
    "You extract closed-book knowledge base facts.\n"
    "Return only the requested strict output format.\n"
    "Do not use prose.\n"
    "Do not return ticker symbols, countries, indexes, regulators, market "
    "segments, or company names.\n"
    "If uncertain, return UNKNOWN."
)

EXCHANGE_HINTS = frozenset({
    "exchange",
    "nasdaq",
    "nyse",
    "euronext",
    "xetra",
    "otc",
    "bourse",
    "borsa",
    "bolsa",
    "tsx",
    "asx",
    "hkex",
    "jse",
    "lse",
    "nse",
    "bse",
    "sgx",
    "krx",
    "tse",
    "twse",
    "set",
    "idx",
    "b3",
    "six",
    "deutsche boerse",
    "frankfurt",
    "tokyo stock",
    "london stock",
    "new york stock",
    "hong kong",
    "toronto stock",
    "australian securities",
    "shanghai stock",
    "shenzhen stock",
    "korea exchange",
    "kosdaq",
    "kospi",
    "bmv",
    "bme",
    "bursa",
    "bovespa",
    "milan",
    "italiana",
})

ALIAS_KEYS = {
    "new york stock exchange": "new york stock exchange",
    "nyse": "new york stock exchange",
    "nasdaq": "nasdaq",
    "nasdaq stock market": "nasdaq",
    "london stock exchange": "london stock exchange",
    "lse": "london stock exchange",
    "tokyo stock exchange": "tokyo stock exchange",
    "hong kong stock exchange": "hong kong stock exchange",
    "hkex": "hong kong stock exchange",
    "toronto stock exchange": "toronto stock exchange",
    "tsx": "toronto stock exchange",
    "australian securities exchange": "australian securities exchange",
    "asx": "australian securities exchange",
    "frankfurt stock exchange": "frankfurt stock exchange",
    "xetra": "xetra",
    "six swiss exchange": "six swiss exchange",
    "euronext paris": "euronext paris",
    "b3": "b3",
}

_BAD_ENTITY_RE = re.compile(
    r"\b(ticker|symbol|isin|cusip|index|regulator|country|market segment)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedStockExchangeSet:
    """Strict parse result for one stock-exchange recall view."""

    status: str
    values: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == VALID_EXCHANGE_SET

    def to_json(self) -> dict[str, object]:
        return {"status": self.status, "values": list(self.values)}


@dataclass(frozen=True)
class StockExchangeCluster:
    """One normalized exchange value with cross-view support."""

    key: str
    representative: str
    support: int
    views: tuple[str, ...]
    surfaces: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "representative": self.representative,
            "support": self.support,
            "views": list(self.views),
            "surfaces": list(self.surfaces),
        }


@dataclass(frozen=True)
class StockEmptyRescueDecision:
    """Final deterministic F1 stock empty rescue decision."""

    values: tuple[str, ...]
    reason: str
    clusters: tuple[StockExchangeCluster, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "reason": self.reason,
            "clusters": [cluster.to_json() for cluster in self.clusters],
        }


def stock_exchange_view_prompt(subject: str, view_id: str) -> str:
    """Render one candidate-blind stock empty-rescue view."""
    if view_id == "stock_empty_rescue_s1_direct_listing":
        return (
            f"Subject: {subject}\n\n"
            "Relation: companyTradesAtStockExchange\n\n"
            "Which stock exchange or exchanges list the publicly traded shares "
            "of this exact company?\n"
            "Return official stock exchange entity names only.\n"
            "If the company is private, delisted without a current listing "
            "memory, acquired, or uncertain, return UNKNOWN.\n\n"
            "Format exactly:\n"
            "UNKNOWN\n\n"
            "or one to three lines:\n"
            "EXCHANGE: <exchange name>"
        )
    if view_id == "stock_empty_rescue_s2_primary_listing":
        return (
            f"Subject: {subject}\n\n"
            "First silently disambiguate the exact company.\n"
            "Recall the primary public stock exchange listing venue for this "
            "exact company.\n"
            "Do not substitute a ticker symbol or a generic country market.\n"
            "Do not use a parent company or subsidiary listing unless it is the "
            "exact subject.\n\n"
            "Format exactly:\n"
            "UNKNOWN\n\n"
            "or one to three lines:\n"
            "EXCHANGE: <exchange name>"
        )
    if view_id == "stock_empty_rescue_s3_common_shares":
        return (
            f"Subject: {subject}\n\n"
            "Return the stock exchange entity where this exact company's "
            "ordinary/common shares are traded.\n"
            "Reject ADR-only guesses unless you specifically remember the "
            "exchange as a listing for the exact company.\n"
            "Reject indexes, tickers, securities identifiers, and over-the-counter "
            "guesses when uncertain.\n\n"
            "Format exactly:\n"
            "UNKNOWN\n\n"
            "or one to three lines:\n"
            "EXCHANGE: <exchange name>"
        )
    if view_id == "stock_empty_rescue_s4_anti_prior":
        return (
            f"Subject: {subject}\n\n"
            "Reason silently.\n"
            "Ask whether the first answer is a real exchange-listing fact for "
            "this exact company or just a plausible market prior.\n"
            "Only return an exchange when you have specific factual memory.\n"
            "If uncertain, return UNKNOWN.\n\n"
            "Format exactly:\n"
            "UNKNOWN\n\n"
            "or one to three lines:\n"
            "EXCHANGE: <exchange name>"
        )
    raise ValueError(f"unknown stock empty-rescue view_id {view_id!r}")


def canonical_exchange_key(name: str) -> str:
    key = strict_key(name)
    return ALIAS_KEYS.get(key, key)


def clean_exchange_name(name: str) -> str | None:
    """Apply structural exchange-output validation without subject lookup."""
    text = re.sub(r"\s+", " ", name.strip().strip("`'\".,"))
    if len(text) < 2 or len(text) > 90:
        return None
    if any(separator in text for separator in (";", "|", "\n", "\t")):
        return None
    lowered = text.lower()
    if _BAD_ENTITY_RE.search(lowered):
        return None
    if re.fullmatch(r"[A-Z0-9.:-]{1,12}", text) and lowered not in EXCHANGE_HINTS:
        return None
    if not any(hint in lowered for hint in EXCHANGE_HINTS):
        return None
    return text


def parse_stock_exchange_output(text: str) -> ParsedStockExchangeSet:
    """Parse strict stock empty-rescue output."""
    raw = (text or "").strip()
    if raw.upper() == UNKNOWN:
        return ParsedStockExchangeSet(UNKNOWN)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 3:
        return ParsedStockExchangeSet(INVALID)

    values: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line.startswith("EXCHANGE: "):
            return ParsedStockExchangeSet(INVALID)
        cleaned = clean_exchange_name(line[len("EXCHANGE: "):])
        if cleaned is None:
            return ParsedStockExchangeSet(INVALID)
        key = canonical_exchange_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(cleaned)
    if not values:
        return ParsedStockExchangeSet(INVALID)
    return ParsedStockExchangeSet(VALID_EXCHANGE_SET, tuple(values))


def cluster_stock_exchange_outputs(
    parsed_by_view: dict[str, ParsedStockExchangeSet],
) -> list[StockExchangeCluster]:
    """Cluster exchange outputs by normalized key and count one support per view."""
    support: dict[str, set[str]] = defaultdict(set)
    surfaces: dict[str, list[str]] = defaultdict(list)
    first_seen: dict[str, int] = {}

    for view_id, parsed in parsed_by_view.items():
        if not parsed.valid:
            continue
        seen_in_view: set[str] = set()
        for value in parsed.values:
            key = canonical_exchange_key(value)
            if not key or key in seen_in_view:
                continue
            seen_in_view.add(key)
            support[key].add(view_id)
            surfaces[key].append(value)
            first_seen.setdefault(key, len(first_seen))

    clusters: list[StockExchangeCluster] = []
    for key, views in support.items():
        counts = Counter(surfaces[key])
        representative = min(
            counts,
            key=lambda surface: (
                -counts[surface],
                _first_surface_rank(surface, surfaces[key]),
                len(surface),
                surface,
            ),
        )
        clusters.append(StockExchangeCluster(
            key=key,
            representative=representative,
            support=len(views),
            views=tuple(sorted(views, key=_view_rank)),
            surfaces=tuple(surfaces[key]),
        ))
    return sorted(
        clusters,
        key=lambda cluster: (
            -cluster.support,
            min(_view_rank(view_id) for view_id in cluster.views),
            first_seen[cluster.key],
            cluster.representative,
        ),
    )


def decide_stock_empty_rescue(
    parsed_by_view: dict[str, ParsedStockExchangeSet],
    *,
    min_support: int = 3,
) -> StockEmptyRescueDecision:
    """Accept exactly one exchange only under strong cross-view consensus."""
    clusters = tuple(cluster_stock_exchange_outputs(parsed_by_view))
    if clusters and clusters[0].support >= min_support:
        return StockEmptyRescueDecision(
            values=(clusters[0].representative,),
            reason=STRONG_CONSENSUS,
            clusters=clusters,
        )
    return StockEmptyRescueDecision(values=(), reason=REJECTED, clusters=clusters)


def repair_stock(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    """Run Profile F1 stock empty-row rescue, when configured."""
    current = list(prediction.object_entities)
    if not config.features.mistral_stock_empty_rescue:
        return current
    if config.stock_empty_rescue_mode != STOCK_EMPTY_RESCUE_MODE:
        raise ValueError(
            f"unsupported stock empty-rescue mode {config.stock_empty_rescue_mode!r}; "
            f"expected {STOCK_EMPTY_RESCUE_MODE!r}"
        )
    if current:
        record.add_decision(
            STOCK_EMPTY_RESCUE_FEATURE,
            "bypassed_non_empty_stock_row",
            existing=list(current),
        )
        return current

    record.add_decision(STOCK_EMPTY_RESCUE_FEATURE, "eligible_empty_stock_row")
    parsed_by_view: dict[str, ParsedStockExchangeSet] = {}
    for view_id in VIEW_IDS:
        text = caller.generate(
            role="verifier",
            layer="F1_STOCK_EMPTY_RESCUE",
            feature=STOCK_EMPTY_RESCUE_FEATURE,
            system_prompt=STOCK_SYSTEM_PROMPT,
            prompt=stock_exchange_view_prompt(prediction.subject, view_id),
            view_id=view_id,
            max_new_tokens=96,
        )
        if text is None:
            record.skipped.append(f"MistralStockEmptyRescue: budget exhausted before {view_id}")
            parsed = ParsedStockExchangeSet(INVALID)
        else:
            parsed = parse_stock_exchange_output(text)
        parsed_by_view[view_id] = parsed
        record.add_decision(
            STOCK_EMPTY_RESCUE_FEATURE,
            "view_output",
            view_id=view_id,
            raw=text or "",
            **parsed.to_json(),
        )

    decision = decide_stock_empty_rescue(
        parsed_by_view,
        min_support=config.stock_empty_rescue_min_support,
    )
    record.add_decision(
        STOCK_EMPTY_RESCUE_FEATURE,
        "clustered_exchange_outputs",
        clusters=[cluster.to_json() for cluster in decision.clusters],
    )
    record.add_decision(
        STOCK_EMPTY_RESCUE_FEATURE,
        "final_decision",
        reason=decision.reason,
        values=list(decision.values),
    )
    return list(decision.values)


def _view_rank(view_id: str) -> int:
    try:
        return VIEW_IDS.index(view_id)
    except ValueError:
        return len(VIEW_IDS)


def _first_surface_rank(surface: str, surfaces: Sequence[str]) -> int:
    try:
        return list(surfaces).index(surface)
    except ValueError:
        return len(surfaces)
