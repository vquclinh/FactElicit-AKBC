"""Deterministic supplemental coverage support for V3 TRAIN collection.

The full TRAIN run is the base corpus. If it finishes with a small target
deficit, a supplemental run may revisit only TRAIN rows whose real base
artifacts show the missing family reached the V3 catalogue. Planning uses
subject, relation, row index and persisted graph/telemetry only. It never reads
TRAIN gold and never mutates the base collection.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cover_kbc.controller_calibration.collection_policy import CoverageLedger
from cover_kbc.controller_calibration.sufficiency import evaluate_sufficiency
from cover_kbc.controller_calibration.telemetry import read_telemetry
from cover_kbc.types import Query
from cover_kbc.v3_core.relation_programs import relation_train_collection_actions


EXPECTED_SUPPLEMENTAL_FAMILIES = (
    "LISTING_ELIMINATION",
    "SEMANTIC_VERIFY",
    "SET_EXPANSION",
    "UNARY_VERIFY",
)

REQUIRED_RUN_FILES = (
    "predictions.jsonl",
    "train_telemetry.jsonl",
    "inference_telemetry.jsonl",
    "v3_pre_m8_hypothesis_graphs.jsonl",
    "v3_final_hypothesis_graphs.jsonl",
    "v3_action_effects.jsonl",
    "v3_action_coverage.json",
    "v3_action_coverage.csv",
    "manifest.json",
)


class SupplementalCoverageError(ValueError):
    """A supplemental coverage plan or merge would be unsafe."""


@dataclass(frozen=True)
class SupplementalFamilyPlan:
    action_family: str
    deficit: int
    candidate_relations: tuple[str, ...]
    row_indices: tuple[int, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "action_family": self.action_family,
            "deficit": self.deficit,
            "candidate_relations": list(self.candidate_relations),
            "row_indices": list(self.row_indices),
        }


@dataclass(frozen=True)
class SupplementalCoveragePlan:
    schema_version: str = "v3-supplemental-coverage-plan-v2"
    split: str = "train"
    families: tuple[SupplementalFamilyPlan, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def row_indices(self) -> tuple[int, ...]:
        rows = {
            row
            for family in self.families
            for row in family.row_indices
        }
        return tuple(sorted(rows))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "split": self.split,
            "families": [family.to_json() for family in self.families],
            "row_indices": list(self.row_indices),
            "notes": list(self.notes),
        }

    def families_for_row(self, row_index: int) -> tuple[str, ...]:
        return tuple(sorted(
            family.action_family
            for family in self.families
            if row_index in family.row_indices
        ))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SupplementalCoverageError(f"{path}: unreadable JSON: {error}") from error


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise SupplementalCoverageError(
                        f"{path}:{number}: malformed JSONL: {error}"
                    ) from None
    except OSError as error:
        raise SupplementalCoverageError(f"{path}: unreadable: {error}") from error


def resolve_collection_run_dir(collection_dir: str | Path) -> Path:
    """Return the run directory containing the immutable collection artifacts."""
    root = Path(collection_dir)
    if all((root / name).is_file() for name in REQUIRED_RUN_FILES):
        return root
    matches: list[Path] = []
    if root.exists():
        for manifest in root.rglob("manifest.json"):
            candidate = manifest.parent
            if all((candidate / name).is_file() for name in REQUIRED_RUN_FILES):
                matches.append(candidate)
    if not matches:
        raise SupplementalCoverageError(
            f"{root}: no V3 collection run directory with required artifacts found"
        )
    return sorted(matches, key=lambda path: str(path))[0]


def supplemental_base_identity(collection_dir: str | Path) -> str:
    """Stable identity hash for a base collection used by supplement/resume."""
    run_dir = resolve_collection_run_dir(collection_dir)
    pieces = []
    for name in ("manifest.json", "v3_action_coverage.json", "v3_action_effects.jsonl"):
        path = run_dir / name
        pieces.append(hashlib.sha256(path.read_bytes()).hexdigest())
    raw = "|".join(pieces).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def supplemental_run_identity(collection_dir: str | Path) -> str:
    """Stable identity hash for one supplement artifact directory."""
    run_dir = resolve_collection_run_dir(collection_dir)
    pieces = []
    for name in ("manifest.json", "v3_action_coverage.json", "train_telemetry.jsonl"):
        path = run_dir / name
        pieces.append(hashlib.sha256(path.read_bytes()).hexdigest())
    raw = "|".join(pieces).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_collection_coverage(collection_dir: str | Path) -> CoverageLedger:
    run_dir = resolve_collection_run_dir(collection_dir)
    payload = _load_json(run_dir / "v3_action_coverage.json")
    return CoverageLedger.from_json(payload)


def load_chained_coverage(
    base_dir: str | Path,
    supplement_dirs: Sequence[str | Path] = (),
) -> CoverageLedger:
    """Coverage ledger for BASE plus already-committed supplements."""
    coverage = load_collection_coverage(base_dir)
    for supplement_dir in supplement_dirs:
        coverage = merge_coverage_ledgers(
            coverage, load_collection_coverage(supplement_dir))
    return coverage


def family_relations() -> dict[str, tuple[str, ...]]:
    """Relations whose V3 TRAIN catalogue may offer each family."""
    by_family: dict[str, list[str]] = {}
    from cover_kbc.contracts.registry import CONTRACTS

    for relation in sorted(CONTRACTS):
        for action in relation_train_collection_actions(relation):
            by_family.setdefault(action.value, []).append(relation)
    return {
        family: tuple(sorted(relations))
        for family, relations in sorted(by_family.items())
    }


def _active_primary(hgraph: Mapping[str, Any]) -> Mapping[str, Any] | None:
    active = [
        dict(item) for item in hgraph.get("hypotheses", ())
        if item.get("status") != "DROPPED"
    ]
    if not active:
        return None
    return sorted(
        active,
        key=lambda item: (
            -int(item.get("independent_support_count") or 0),
            -int(item.get("raw_support_count") or 0),
            str(item.get("hypothesis_id", "")),
        ),
    )[0]


def observed_catalogue_rows(collection_dir: str | Path) -> dict[str, tuple[int, ...]]:
    """Rows whose real base graph could build each deficit family catalogue.

    The persisted graph exposes the relation/state legality region; this helper
    applies the owner preconditions needed for the four current supplemental
    families so the planner prefers rows backed by real base evidence.
    """
    run_dir = resolve_collection_run_dir(collection_dir)
    by_family: dict[str, set[int]] = {
        family: set() for family in EXPECTED_SUPPLEMENTAL_FAMILIES
    }
    for hgraph in _jsonl(run_dir / "v3_pre_m8_hypothesis_graphs.jsonl"):
        relation = str(hgraph.get("Relation", ""))
        state = str(hgraph.get("failure_state", ""))
        row_index = int(hgraph.get("row_index", -1))
        primary = _active_primary(hgraph)
        if relation == "awardWonBy" and state == "SET_GROWING":
            by_family["SET_EXPANSION"].add(row_index)
            if primary is not None:
                by_family["UNARY_VERIFY"].add(row_index)
        if (
            relation == "companyTradesAtStockExchange"
            and state == "HIGH_FP_RISK"
            and primary is not None
        ):
            by_family["LISTING_ELIMINATION"].add(row_index)
            by_family["SEMANTIC_VERIFY"].add(row_index)
    return {
        family: tuple(sorted(rows))
        for family, rows in sorted(by_family.items())
    }


def successful_observation_rows_by_family(
    collection_dirs: Sequence[str | Path],
) -> dict[str, Counter[int]]:
    """Committed successful observations keyed by family and TRAIN row."""
    by_family: dict[str, Counter[int]] = defaultdict(Counter)
    for collection_dir in collection_dirs:
        run_dir = resolve_collection_run_dir(collection_dir)
        for record in read_telemetry(run_dir / "train_telemetry.jsonl"):
            if not record.executed or record.outcome.errors:
                continue
            by_family[record.action_family][record.row_index] += 1
    return by_family


def unresolved_failed_rows_by_relation(
    collection_dirs: Sequence[str | Path],
) -> dict[str, set[int]]:
    """Rows that a prior supplement attempted but never committed."""
    by_relation: dict[str, set[int]] = defaultdict(set)
    for collection_dir in collection_dirs:
        run_dir = resolve_collection_run_dir(collection_dir)
        manifest = _load_json(run_dir / "manifest.json")
        unresolved = {int(row) for row in manifest.get("unresolved_failed_rows", ())}
        for entry in manifest.get("failure_history", ()):
            row = int(entry.get("row_index", -1))
            if row not in unresolved or bool(entry.get("resolved", False)):
                continue
            relation = str(entry.get("relation", ""))
            if relation:
                by_relation[relation].add(row)
    return by_relation


def _failed_priority_rows_by_family(
    prior_supplement_dirs: Sequence[str | Path],
) -> dict[str, set[int]]:
    rows = unresolved_failed_rows_by_relation(prior_supplement_dirs)
    return {
        "SET_EXPANSION": set(rows.get("awardWonBy", set())),
        "UNARY_VERIFY": set(rows.get("awardWonBy", set())),
        "LISTING_ELIMINATION": set(
            rows.get("companyTradesAtStockExchange", set())),
        "SEMANTIC_VERIFY": set(
            rows.get("companyTradesAtStockExchange", set())),
    }


def plan_supplemental_coverage(
    coverage: CoverageLedger,
    queries: Sequence[Query],
    *,
    max_rows_per_family: int | None = None,
) -> SupplementalCoveragePlan:
    """Fallback planner using only TRAIN row identity and relation."""
    relations_by_family = family_relations()
    by_relation: dict[str, list[int]] = {}
    for query in queries:
        by_relation.setdefault(query.relation, []).append(query.row_index)

    family_plans: list[SupplementalFamilyPlan] = []
    notes: list[str] = []
    for family in coverage.unobserved_families:
        entry = coverage.families[family]
        deficit = entry.coverage_deficit
        if deficit <= 0:
            continue
        relations = relations_by_family.get(family, ())
        candidates = sorted({
            row
            for relation in relations
            for row in by_relation.get(relation, ())
        })
        limit = deficit if max_rows_per_family is None else min(
            deficit, max_rows_per_family)
        selected = tuple(candidates[:limit])
        if not selected:
            notes.append(
                f"{family}: no TRAIN row has a relation that can offer it")
        family_plans.append(SupplementalFamilyPlan(
            action_family=family,
            deficit=deficit,
            candidate_relations=relations,
            row_indices=selected,
        ))
    return SupplementalCoveragePlan(
        families=tuple(sorted(family_plans, key=lambda item: item.action_family)),
        notes=tuple(notes),
    )


def plan_supplemental_coverage_from_base(
    collection_dir: str | Path,
    queries: Sequence[Query],
    *,
    coverage: CoverageLedger | None = None,
    max_rows_per_family: int | None = None,
    prior_supplement_dirs: Sequence[str | Path] = (),
) -> SupplementalCoveragePlan:
    """Plan from under-covered base families, preferring real base evidence."""
    base_coverage = coverage or load_chained_coverage(
        collection_dir, prior_supplement_dirs)
    unexpected = sorted(
        set(base_coverage.unobserved_families) - set(EXPECTED_SUPPLEMENTAL_FAMILIES)
    )
    if unexpected:
        raise SupplementalCoverageError(
            f"unexpected under-covered family/families in base: {unexpected}"
        )
    evidence_rows = observed_catalogue_rows(collection_dir)
    prior_successes = successful_observation_rows_by_family(
        tuple(prior_supplement_dirs))
    failed_priority_rows = _failed_priority_rows_by_family(
        tuple(prior_supplement_dirs))
    relations_by_family = family_relations()
    by_index = {query.row_index: query for query in queries}
    family_plans: list[SupplementalFamilyPlan] = []
    notes: list[str] = []
    for family in base_coverage.unobserved_families:
        entry = base_coverage.families[family]
        deficit = entry.coverage_deficit
        if deficit <= 0:
            continue
        candidates = [row for row in evidence_rows.get(family, ()) if row in by_index]
        already_successful = set(prior_successes.get(family, Counter()))
        candidates = [row for row in candidates if row not in already_successful]
        if not candidates:
            notes.append(
                f"{family}: no persisted base graph reached an executable catalogue"
            )
            fallback = plan_supplemental_coverage(
                base_coverage, queries, max_rows_per_family=max_rows_per_family)
            for item in fallback.families:
                if item.action_family == family:
                    candidates = list(item.row_indices)
                    break
        candidates = sorted(
            candidates,
            key=lambda row: (
                0 if row in failed_priority_rows.get(family, set()) else 1,
                row,
            ),
        )
        limit = deficit if max_rows_per_family is None else min(
            deficit, max_rows_per_family)
        family_plans.append(SupplementalFamilyPlan(
            action_family=family,
            deficit=deficit,
            candidate_relations=relations_by_family.get(family, ()),
            row_indices=tuple(candidates[:limit]),
        ))
    return SupplementalCoveragePlan(
        families=tuple(sorted(family_plans, key=lambda item: item.action_family)),
        notes=tuple(notes),
    )


def write_supplemental_plan(path: str | Path, plan: SupplementalCoveragePlan) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan.to_json(), indent=2), encoding="utf-8")
    return target


def merge_coverage_ledgers(
    base: CoverageLedger, supplement: CoverageLedger,
) -> CoverageLedger:
    """Merge two ledgers without double-counting base legal opportunities.

    If the supplement ledger was initialized from base coverage, it is already
    base-inclusive and can be returned after validation. Otherwise only
    execution/selectability/blocker diagnostics are additive; legal opportunity
    counts remain the base TRAIN opportunity counts.
    """
    if base.configured_target != supplement.configured_target:
        raise SupplementalCoverageError(
            "coverage ledgers use different configured targets: "
            f"{base.configured_target} vs {supplement.configured_target}"
        )
    base_json = base.to_json()
    supp_json = supplement.to_json()
    base_families = {
        entry["action_family"]: entry for entry in base_json.get("families", ())
    }
    supp_families = {
        entry["action_family"]: entry for entry in supp_json.get("families", ())
    }
    if all(
        supp_families.get(family, {}).get("legal_opportunities")
        == base_entry.get("legal_opportunities")
        for family, base_entry in base_families.items()
    ):
        return CoverageLedger.from_json(supp_json)

    merged = CoverageLedger.from_json(base_json)
    for family, entry in supplement.families.items():
        slot = merged._slot(family)
        slot.selectable_opportunities += entry.selectable_opportunities
        slot.executed += entry.executed
        slot.succeeded += entry.succeeded
        slot.failed += entry.failed
        slot.blocked_unaffordable += entry.blocked_unaffordable
        slot.blocked_execution_precondition += (
            entry.blocked_execution_precondition
        )
        slot.blocked_history += entry.blocked_history
        slot.blocked_round_limit += entry.blocked_round_limit
        slot.blocked_missing_primary += entry.blocked_missing_primary
        slot.blocked_other += entry.blocked_other
        slot.required = slot.required or entry.required
        slot.surfaced = slot.surfaced or entry.surfaced
    for relation, by_family in supplement.relation_families.items():
        for family, entry in by_family.items():
            slot = merged._relation_slot(relation, family)
            slot.selectable_opportunities += entry.selectable_opportunities
            slot.executed += entry.executed
            slot.succeeded += entry.succeeded
            slot.failed += entry.failed
            slot.blocked_unaffordable += entry.blocked_unaffordable
            slot.blocked_execution_precondition += (
                entry.blocked_execution_precondition
            )
            slot.blocked_history += entry.blocked_history
            slot.blocked_round_limit += entry.blocked_round_limit
            slot.blocked_missing_primary += entry.blocked_missing_primary
            slot.blocked_other += entry.blocked_other
            slot.required = slot.required or entry.required
            slot.surfaced = slot.surfaced or entry.surfaced
    return merged


def _effect_identity(effect: Mapping[str, Any]) -> str:
    explicit = str(effect.get("action_effect_id", ""))
    if explicit:
        return explicit
    action = dict(effect.get("action") or {})
    action_id = str(action.get("action_id", ""))
    row_index = str(action.get("row_index", ""))
    if not action_id:
        raise SupplementalCoverageError("action effect has no action identity")
    return f"base:{row_index}:{action_id}"


def validate_no_duplicate_action_effect_ids(
    base_effects: Iterable[Mapping[str, Any]],
    supplement_effects: Iterable[Mapping[str, Any]],
) -> None:
    """Fail before an offline merge would duplicate action-effect identities."""
    seen: set[str] = set()
    for source, effects in (("base", base_effects), ("supplement", supplement_effects)):
        for effect in effects:
            identity = _effect_identity(effect)
            if identity in seen:
                raise SupplementalCoverageError(
                    f"duplicate action-effect identity {identity} in {source}"
                )
            seen.add(identity)


def committed_action_effects(
    run_dir: str | Path,
    effects: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split action effects into committed and uncommitted row-transaction tails.

    Supplement row failures can leave an owner effect in memory and eventually
    on disk even though the row never committed telemetry. Calibration merges
    must preserve the raw file untouched but count only effects backed by an
    executed committed telemetry record.
    """
    resolved = resolve_collection_run_dir(run_dir)
    remaining: Counter[tuple[int, str]] = Counter()
    for record in read_telemetry(resolved / "train_telemetry.jsonl"):
        if not record.executed:
            continue
        remaining[(record.row_index, record.action_id)] += 1
    kept: list[Mapping[str, Any]] = []
    ignored: list[Mapping[str, Any]] = []
    source_effects = list(effects) if effects is not None else list(
        _jsonl(resolved / "v3_action_effects.jsonl"))
    for effect in source_effects:
        action = dict(effect.get("action") or {})
        key = (
            int(action.get("row_index", -1)),
            str(action.get("action_id", "")),
        )
        if remaining[key] > 0:
            kept.append(effect)
            remaining[key] -= 1
        else:
            ignored.append(effect)
    missing = {
        f"{row}:{action_id}": count
        for (row, action_id), count in sorted(remaining.items())
        if count > 0
    }
    if missing:
        raise SupplementalCoverageError(
            f"{resolved}: committed telemetry is missing action effects {missing}"
        )
    return kept, ignored


def _coverage_csv_rows(coverage: CoverageLedger) -> list[dict[str, Any]]:
    return coverage.csv_rows()


def write_coverage_csv(path: Path, coverage: CoverageLedger) -> None:
    fieldnames = (
        "relation",
        "action_family",
        "legal_opportunities",
        "selectable_opportunities",
        "executed",
        "successful",
        "failed",
        "blocked_unaffordable",
        "blocked_execution_precondition",
        "blocked_history",
        "blocked_round_limit",
        "blocked_missing_primary",
        "blocked_other",
        "target",
        "coverage_ratio",
        "status",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in _coverage_csv_rows(coverage):
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge_collections(
    *,
    base_dir: str | Path,
    supplement_dir: str | Path | None = None,
    supplement_dirs: Sequence[str | Path] = (),
    output_dir: str | Path,
) -> dict[str, Any]:
    """Merge BASE plus one or more supplements into a derived corpus."""
    base_run = resolve_collection_run_dir(base_dir)
    supplement_inputs = tuple(
        item for item in ((supplement_dir,) if supplement_dir is not None else ())
        if item is not None
    ) + tuple(supplement_dirs)
    if not supplement_inputs:
        raise SupplementalCoverageError("at least one supplement is required")
    supplement_runs = tuple(resolve_collection_run_dir(path) for path in supplement_inputs)
    base_manifest = _load_json(base_run / "manifest.json")
    base_identity = base_manifest.get("identity", {})
    base_expected = supplemental_base_identity(base_run)
    supplement_manifests = []
    for supplement_run in supplement_runs:
        supplement_manifest = _load_json(supplement_run / "manifest.json")
        supplement_manifests.append(supplement_manifest)
        supplement_identity = supplement_manifest.get("identity", {})
        if base_identity.get("train_sha256") != supplement_identity.get("train_sha256"):
            raise SupplementalCoverageError("base and supplement TRAIN SHA mismatch")
        supplement_declared = (
            supplement_manifest.get("supplemental", {})
            .get("base_collection_identity", "")
        )
        if supplement_declared and supplement_declared != base_expected:
            raise SupplementalCoverageError(
                "supplement declares a different base collection identity"
            )

    base_effects = list(_jsonl(base_run / "v3_action_effects.jsonl"))
    supplement_effects: list[Mapping[str, Any]] = []
    ignored_effects: dict[str, int] = {}
    for supplement_run in supplement_runs:
        committed, ignored = committed_action_effects(supplement_run)
        supplement_effects.extend(committed)
        ignored_effects[str(supplement_run)] = len(ignored)
    validate_no_duplicate_action_effect_ids(base_effects, supplement_effects)

    base_coverage = load_collection_coverage(base_run)
    merged_coverage = base_coverage
    for supplement_run in supplement_runs:
        merged_coverage = merge_coverage_ledgers(
            merged_coverage, load_collection_coverage(supplement_run))

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    merged_effects = [
        {**effect, "source_tag": effect.get("source_tag", "base")}
        for effect in base_effects
    ] + [
        {**effect, "source_tag": effect.get("source_tag", "supplement")}
        for effect in supplement_effects
    ]
    action_effects_path = out / "action_effects.jsonl"
    write_jsonl(action_effects_path, merged_effects)

    base_telemetry = list(read_telemetry(base_run / "train_telemetry.jsonl"))
    supplement_telemetry = [
        record
        for supplement_run in supplement_runs
        for record in read_telemetry(supplement_run / "train_telemetry.jsonl")
    ]
    sufficiency = evaluate_sufficiency(
        [*base_telemetry, *supplement_telemetry],
        expect_transitions=True,
    )
    telemetry_path = out / "train_telemetry.jsonl"
    write_jsonl(telemetry_path, [
        record.to_json() for record in (*base_telemetry, *supplement_telemetry)
    ])

    coverage_path = out / "coverage.json"
    coverage_path.write_text(
        json.dumps(merged_coverage.to_json(), indent=2), encoding="utf-8")
    write_coverage_csv(out / "coverage.csv", merged_coverage)

    corpus_payload = action_effects_path.read_bytes() + telemetry_path.read_bytes()
    corpus_sha = hashlib.sha256(corpus_payload).hexdigest()
    manifest = {
        "schema_version": "merged-v3-calibration-corpus-v1",
        "base_run": str(base_run),
        "supplement_run": str(supplement_runs[0]),
        "supplement_runs": [str(path) for path in supplement_runs],
        "base_identity": base_expected,
        "supplement_identities": [
            supplemental_run_identity(path) for path in supplement_runs
        ],
        "ignored_uncommitted_action_effects": ignored_effects,
        "train_sha256": base_identity.get("train_sha256", ""),
        "base_repo_sha": base_identity.get("repo_sha", ""),
        "supplement_repo_sha": (
            supplement_manifests[-1].get("identity", {}).get("repo_sha", "")
        ),
        "supplement_repo_shas": [
            manifest.get("identity", {}).get("repo_sha", "")
            for manifest in supplement_manifests
        ],
        "coverage": merged_coverage.to_json(),
        "sufficiency": sufficiency.to_json(),
        "merged_corpus_sha256": corpus_sha,
        "calibration_derivation_blocked": not (
            merged_coverage.integrity_ok() and sufficiency.ok
        ),
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sums = {
        "action_effects.jsonl": sha256_file(action_effects_path),
        "train_telemetry.jsonl": sha256_file(telemetry_path),
        "coverage.json": sha256_file(coverage_path),
        "coverage.csv": sha256_file(out / "coverage.csv"),
        "manifest.json": sha256_file(manifest_path),
    }
    (out / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "EXPECTED_SUPPLEMENTAL_FAMILIES",
    "REQUIRED_RUN_FILES",
    "SupplementalCoverageError",
    "SupplementalCoveragePlan",
    "SupplementalFamilyPlan",
    "family_relations",
    "load_collection_coverage",
    "merge_collections",
    "merge_coverage_ledgers",
    "observed_catalogue_rows",
    "plan_supplemental_coverage",
    "plan_supplemental_coverage_from_base",
    "resolve_collection_run_dir",
    "committed_action_effects",
    "load_chained_coverage",
    "successful_observation_rows_by_family",
    "supplemental_base_identity",
    "supplemental_run_identity",
    "unresolved_failed_rows_by_relation",
    "validate_no_duplicate_action_effect_ids",
    "write_supplemental_plan",
]
