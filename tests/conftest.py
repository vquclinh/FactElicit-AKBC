"""Shared fixtures.

Test data is synthetic throughout.  Nothing here encodes a real fact from the
official dataset: the tests check plumbing, not world knowledge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cover_kbc.contracts.registry import get_contract
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.types import Query


@pytest.fixture
def borders_contract():
    return get_contract("countryLandBordersCountry")


@pytest.fixture
def area_contract():
    return get_contract("hasArea")


@pytest.fixture
def capacity_contract():
    return get_contract("hasCapacity")


@pytest.fixture
def death_contract():
    return get_contract("personHasCityOfDeath")


@pytest.fixture
def stock_contract():
    return get_contract("companyTradesAtStockExchange")


@pytest.fixture
def award_contract():
    return get_contract("awardWonBy")


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def synthetic_gold_rows() -> list[dict]:
    """Gold covering every cardinality regime the evaluator must handle."""
    return [
        # zero-object
        {"SubjectEntity": "Testland", "Relation": "countryLandBordersCountry", "ObjectEntities": []},
        # one-object
        {
            "SubjectEntity": "Testperson",
            "Relation": "personHasCityOfDeath",
            "ObjectEntities": [["Testville", "Test Ville"]],
        },
        # multi-object with aliases
        {
            "SubjectEntity": "Testcorp",
            "Relation": "companyTradesAtStockExchange",
            "ObjectEntities": [
                ["Alpha Stock Exchange", "ASE", "The Alpha Stock Exchange"],
                ["Beta Stock Exchange", "BSE"],
            ],
        },
        # numeric
        {"SubjectEntity": "Testisland", "Relation": "hasArea", "ObjectEntities": [["5000"]]},
    ]


@pytest.fixture
def scripted_runtime_factory():
    """Build a :class:`ScriptedRuntime` from ``{(view_id, subject, relation): [...]}``."""

    def _make(script, **kwargs):
        return ScriptedRuntime(script, **kwargs)

    return _make


@pytest.fixture
def borders_query():
    return Query(subject="Testland", relation="countryLandBordersCountry", row_index=0)
