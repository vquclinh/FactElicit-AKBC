"""Parsing helpers for model-assisted repair outputs."""

from __future__ import annotations

import json
import re
from typing import Iterable, Mapping

from cover_kbc.elicitation.parsing import parse_entities, parse_numeric_observations
from cover_kbc.normalization.strings import clean_surface, is_abstain, strict_key

_CONFIDENCE = re.compile(r"\bconfidence\s*[:=]\s*([01](?:\.\d+)?)", re.IGNORECASE)
_LABEL = re.compile(r"\b(VALID|INVALID|UNKNOWN|ALIVE|DECEASED)\b", re.IGNORECASE)
_SECTION = re.compile(r"^\s*([A-Z_ -]+)\s*:\s*(.*)$")


def first_label(text: str, allowed: Iterable[str]) -> str:
    allowed_set = {label.upper() for label in allowed}
    match = _LABEL.search(text or "")
    if match and match.group(1).upper() in allowed_set:
        return match.group(1).upper()
    head = (text or "").strip().split(None, 1)[0].strip(":,.;").upper()
    return head if head in allowed_set else "UNKNOWN"


def confidence(text: str, default: float = 0.5) -> float:
    match = _CONFIDENCE.search(text or "")
    if not match:
        return default
    try:
        value = float(match.group(1))
    except ValueError:
        return default
    return max(0.0, min(1.0, value))


def section_items(text: str) -> dict[str, list[str]]:
    """Parse `SECTION: a; b` style output into cleaned item lists."""
    out: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        match = _SECTION.match(line)
        if not match:
            continue
        section = match.group(1).strip().upper().replace(" ", "_").replace("-", "_")
        payload = _CONFIDENCE.sub("", match.group(2)).strip()
        if not payload:
            out.setdefault(section, [])
            continue
        items = []
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            items = [clean_surface(str(item)) for item in parsed]
        else:
            items = [
                clean_surface(part)
                for part in re.split(r"[;|\n]", payload)
                if clean_surface(part)
            ]
        out.setdefault(section, []).extend(item for item in items if not is_abstain(item))
    return out


def supported_items_from_text(text: str, contract, sections: tuple[str, ...]) -> list[str]:
    parsed = section_items(text)
    values: list[str] = []
    for section in sections:
        for item in parsed.get(section, []):
            values.extend(parse_entities(item, contract))
    if values:
        return unique_by_strict_key(values)
    return unique_by_strict_key(parse_entities(text, contract))


def numeric_values_from_text(text: str, contract) -> list[float]:
    return [obs.value for obs in parse_numeric_observations(text or "", contract)]


def unique_by_strict_key(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = clean_surface(str(value))
        key = strict_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def choose_existing_surface(candidates: Iterable[str], wanted: Iterable[str]) -> list[str]:
    by_key: dict[str, str] = {}
    for candidate in candidates:
        key = strict_key(candidate)
        if key and key not in by_key:
            by_key[key] = candidate
    out = []
    for value in wanted:
        key = strict_key(value)
        if key in by_key and by_key[key] not in out:
            out.append(by_key[key])
    return out


def label_prob_confidence(probabilities: Mapping[str, float], label: str) -> float:
    if not probabilities:
        return 0.5
    return float(probabilities.get(label, 0.0))
