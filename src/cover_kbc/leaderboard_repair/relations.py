"""Relation-specific L7 repair modules."""

from __future__ import annotations

import re
from typing import Sequence

from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.normalization.numeric import format_numeric
from cover_kbc.normalization.strings import strict_key
from cover_kbc.types import Prediction

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.parsing import (
    choose_existing_surface,
    confidence,
    first_label,
    numeric_values_from_text,
    supported_items_from_text,
    unique_by_strict_key,
)
from cover_kbc.leaderboard_repair.runtime import RepairCaller
from cover_kbc.leaderboard_repair.types import CandidateSignal, RowRepairRecord
from cover_kbc.leaderboard_repair.util import (
    AREA,
    AWARD,
    BORDERS,
    CAPACITY,
    CITY,
    STOCK,
    confident_no_border,
    dedupe_aliases,
    low_confidence_numeric,
    normalize_award_metadata,
    numeric_cluster_output,
    plausible_stock_exchange,
    signal_values,
    stock_wrong_type_reason,
)


def repair_stock(
    prediction: Prediction,
    signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    values = list(prediction.object_entities)
    contract = CONTRACTS[STOCK]
    features = config.features

    if features.stock_entity_guard and values:
        kept, rejected = [], []
        for value in values:
            reason = stock_wrong_type_reason(value, prediction.subject)
            if reason:
                rejected.append({"value": value, "reason": reason})
            else:
                kept.append(value)
        if rejected:
            record.add_decision(
                "StockExchangeEntityGuard",
                "deterministic_type_reject",
                rejected=rejected,
            )
        values = kept

        if values and caller.row_budget.can_spend():
            prompt = _stock_type_prompt(prediction.subject, values)
            text = caller.generate(
                role="verifier",
                layer="L7",
                feature="StockExchangeEntityGuard",
                prompt=prompt,
                view_id="l7_stock_entity_guard",
                max_new_tokens=192,
            )
            keep = choose_existing_surface(
                values,
                supported_items_from_text(text or "", contract, ("KEEP", "VALID")),
            )
            drop_items = supported_items_from_text(text or "", contract, ("DROP", "INVALID"))
            if keep:
                removed = [v for v in values if strict_key(v) not in {strict_key(k) for k in keep}]
                values = keep
                record.add_decision(
                    "StockExchangeEntityGuard",
                    "model_type_filter",
                    kept=keep,
                    removed=removed or drop_items,
                )

    if features.stock_alias_dedupe and values:
        before = list(values)
        values = dedupe_aliases(values)
        if values != before:
            record.add_decision(
                "StockAliasDeduplicator",
                "deterministic_alias_collapse",
                before=before,
                after=values,
            )
        if len(values) > 1 and caller.row_budget.can_spend():
            prompt = _stock_alias_prompt(prediction.subject, values)
            text = caller.generate(
                role="verifier",
                layer="L7",
                feature="StockAliasDeduplicator",
                prompt=prompt,
                view_id="l7_stock_alias_dedupe",
                max_new_tokens=160,
            )
            groups = _parse_alias_groups(text or "", values)
            if groups:
                before = list(values)
                values = dedupe_aliases(values, model_groups=groups)
                if values != before:
                    record.add_decision(
                        "StockAliasDeduplicator",
                        "model_assisted_alias_collapse",
                        groups=groups,
                        before=before,
                        after=values,
                    )

    if features.stock_multi_listing_rescue and len(values) <= 1:
        support_by_key: dict[str, int] = {}
        for signal in signals:
            key = strict_key(signal.value)
            if not key:
                continue
            support_by_key[key] = max(
                support_by_key.get(key, 0),
                int(signal.support or 0),
                2 if signal.source == "current_prediction" else 0,
            )
        plausible = [
            signal.value for signal in signals
            if plausible_stock_exchange(signal.value, prediction.subject)
            and int(signal.support or 0) >= 1
        ]
        plausible = unique_by_strict_key([*values, *plausible])
        if len(plausible) >= 2:
            if caller.row_budget.can_spend():
                prompt = _stock_rescue_prompt(prediction.subject, plausible)
                text = caller.generate(
                    role="verifier",
                    layer="L7",
                    feature="StockMultiListingRescue",
                    prompt=prompt,
                    view_id="l7_stock_multi_listing_rescue",
                    max_new_tokens=256,
                )
                restored = choose_existing_surface(
                    plausible,
                    supported_items_from_text(text or "", contract, ("RESTORE", "VALID", "KEEP")),
                )
                conf = confidence(text or "", 0.65)
                if conf >= config.stock_rescue_min_confidence:
                    before = list(values)
                    value_keys = {strict_key(v) for v in values}
                    for candidate in restored:
                        key = strict_key(candidate)
                        if key not in value_keys and support_by_key.get(key, 0) >= 1:
                            values.append(candidate)
                            value_keys.add(key)
                    if values != before:
                        record.add_decision(
                            "StockMultiListingRescue",
                            "restored_supported_secondary_listing",
                            restored=[v for v in values if v not in before],
                            verifier_confidence=conf,
                            plausible_candidates=plausible,
                        )
                else:
                    record.add_decision(
                        "StockMultiListingRescue",
                        "rejected_low_confidence_rescue",
                        verifier_confidence=conf,
                        plausible_candidates=plausible,
                    )
            else:
                record.skipped.append("StockMultiListingRescue: budget exhausted")

    return values


def repair_borders(
    prediction: Prediction,
    signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    values = [v for v in prediction.object_entities if strict_key(v) != strict_key(prediction.subject)]
    features = config.features
    contract = CONTRACTS[BORDERS]

    if features.border_alias_dedupe and values:
        before = list(values)
        values = dedupe_aliases(values)
        if values != before:
            record.add_decision(
                "BorderAliasDeduplicator",
                "generic_alias_collapse",
                before=before,
                after=values,
            )

    suspicious = (
        not values
        or (len(values) <= 1 and not confident_no_border(prediction))
        or len(signal_values(signals)) > len(values)
    )
    if features.border_directional_sweep and suspicious:
        recalled: list[str] = []
        for view_id, directions in (
            ("l7_border_directional_sweep_north_west_east",
             "NORTH, NORTHWEST, NORTHEAST, WEST and EAST"),
            ("l7_border_directional_sweep_south_edge",
             "SOUTH, SOUTHWEST, SOUTHEAST, ENCLAVE, EXCLAVE and OVERSEAS-TERRITORY edge cases"),
        ):
            if not caller.row_budget.can_spend():
                record.skipped.append("BorderDirectionalSweep: budget exhausted")
                break
            prompt = _border_sweep_prompt(prediction.subject, directions)
            text = caller.generate(
                role="enumerator",
                layer="L7",
                feature="BorderDirectionalSweep",
                prompt=prompt,
                view_id=view_id,
                max_new_tokens=192,
            )
            recalled.extend(supported_items_from_text(text or "", contract, ("BORDERS", "CANDIDATES")))

        candidates = unique_by_strict_key([*values, *recalled])
        if recalled and caller.row_budget.can_spend():
            prompt = _border_verify_prompt(prediction.subject, candidates)
            text = caller.generate(
                role="verifier",
                layer="L7",
                feature="BorderDirectionalSweep",
                prompt=prompt,
                view_id="l7_border_directional_verify",
                max_new_tokens=192,
            )
            verified = choose_existing_surface(
                candidates,
                supported_items_from_text(text or "", contract, ("VALID", "BORDERS")),
            )
            before = list(values)
            values = verified or values
            if values != before:
                record.add_decision(
                    "BorderDirectionalSweep",
                    "added_verified_directional_neighbors",
                    recalled=recalled,
                    after=values,
                )
        elif recalled:
            record.skipped.append("BorderDirectionalSweep verifier: budget exhausted")

    return values


def repair_city(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    values = list(prediction.object_entities[:1])
    if values or not config.features.death_existence_gate:
        return values[:1]

    status_evidence = []
    for role, view_id in (
        ("enumerator", "l7_death_status_mistral"),
        ("verifier", "l7_death_status_qwen"),
    ):
        if not caller.row_budget.can_spend():
            record.skipped.append("DeathExistenceGate: budget exhausted")
            break
        text = caller.generate(
            role=role,
            layer="L7",
            feature="DeathExistenceGate",
            prompt=_death_status_prompt(prediction.subject),
            view_id=view_id,
            max_new_tokens=48,
        )
        label = first_label(text or "", ("ALIVE", "DECEASED", "UNKNOWN"))
        status_evidence.append({
            "model_role": role,
            "label": label,
            "confidence": confidence(text or "", 0.7 if label != "UNKNOWN" else 0.5),
            "raw": text or "",
        })
    record.add_decision(
        "DeathExistenceGate",
        "life_status_evidence",
        evidence=status_evidence,
    )

    alive = [e for e in status_evidence if e["label"] == "ALIVE"]
    deceased = [e for e in status_evidence if e["label"] == "DECEASED"]
    if len(alive) == 2 and all(float(e["confidence"]) >= 0.65 for e in alive):
        record.add_decision("DeathExistenceGate", "kept_empty_living_person")
        return []
    if not deceased or any(
        e["label"] == "ALIVE" and float(e["confidence"]) >= 0.75
        for e in status_evidence
    ):
        record.add_decision("DeathExistenceGate", "kept_empty_no_deceased_consensus")
        return []
    if not config.features.death_city_recall:
        return []

    contract = CONTRACTS[CITY]
    recalled: list[tuple[str, list[str]]] = []
    for role, view_id, prompt in (
        ("enumerator", "l7_death_city_direct", _death_city_direct_prompt(prediction.subject)),
        ("verifier", "l7_death_city_final_locality", _death_city_locality_prompt(prediction.subject)),
    ):
        if not caller.row_budget.can_spend():
            record.skipped.append("DeathCityRecall: budget exhausted")
            break
        text = caller.generate(
            role=role,
            layer="L7",
            feature="DeathCityRecall",
            prompt=prompt,
            view_id=view_id,
            max_new_tokens=96,
        )
        recalled.append((view_id, supported_items_from_text(text or "", contract, ("CITY", "ANSWER"))))

    agreed = _first_agreement([items for _, items in recalled])
    if agreed:
        record.add_decision(
            "DeathCityRecall",
            "two_view_city_agreement",
            recalled={view: items for view, items in recalled},
            city=agreed,
        )
        return [agreed]

    flat = unique_by_strict_key([item for _, items in recalled for item in items])
    if len(flat) > 1 and caller.row_budget.can_spend():
        text = caller.generate(
            role="verifier",
            layer="L7",
            feature="DeathCityRecall",
            prompt=_death_city_contrast_prompt(prediction.subject, flat),
            view_id="l7_death_city_contrast",
            max_new_tokens=128,
        )
        chosen = choose_existing_surface(
            flat,
            supported_items_from_text(text or "", contract, ("VALID", "CITY", "ANSWER")),
        )
        if chosen:
            record.add_decision(
                "DeathCityRecall",
                "contrast_verifier_selected_singleton",
                candidates=flat,
                city=chosen[0],
            )
            return [chosen[0]]
    elif len(flat) > 1:
        record.skipped.append("DeathCityRecall contrast: budget exhausted")
    return []


def repair_area(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    if not config.features.area_empty_rescue or not low_confidence_numeric(prediction):
        return prediction.object_entities[:1]
    entity_type = _area_entity_type(prediction.subject)
    values: list[float] = []
    contract = CONTRACTS[AREA]
    for view_id, prompt in (
        ("l7_area_recall_exact_entity", _area_recall_prompt(prediction.subject, entity_type, "exact entity")),
        ("l7_area_recall_semantic_contrast", _area_recall_prompt(prediction.subject, entity_type, "semantic contrast")),
    ):
        if not caller.row_budget.can_spend():
            record.skipped.append("AreaEmptyRescue: budget exhausted")
            break
        text = caller.generate(
            role="enumerator" if "exact" in view_id else "verifier",
            layer="L7",
            feature="AreaEmptyRescue",
            prompt=prompt,
            view_id=view_id,
            max_new_tokens=96,
        )
        values.extend(numeric_values_from_text(text or "", contract))
    output = numeric_cluster_output(
        values, tolerance=config.numeric_cluster_tolerance, integer_only=False)
    if output:
        record.add_decision(
            "AreaEmptyRescue",
            "numeric_consensus_cluster",
            entity_type=entity_type,
            values=values,
            output=output,
        )
        return [output]
    if values and caller.row_budget.can_spend():
        text = caller.generate(
            role="verifier",
            layer="L7",
            feature="NumericAttributeResolver",
            prompt=_numeric_resolver_prompt(prediction.subject, AREA, values),
            view_id="l7_area_numeric_attribute_resolver",
            max_new_tokens=128,
        )
        resolved = numeric_values_from_text(text or "", contract)
        if resolved:
            output = format_numeric(resolved[0], integer_only=False)
            record.add_decision(
                "NumericAttributeResolver",
                "selected_relation_compatible_area",
                entity_type=entity_type,
                values=values,
                output=output,
            )
            return [output]
    elif values:
        record.skipped.append("Area NumericAttributeResolver: budget exhausted")
    return prediction.object_entities[:1]


def repair_capacity(
    prediction: Prediction,
    signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    if not config.features.capacity_repair:
        return prediction.object_entities[:1]
    current = list(prediction.object_entities[:1])
    signal_values_text = " ".join(signal.value for signal in signals)
    trigger = (
        not current
        or low_confidence_numeric(prediction)
        or _round_number(current[0] if current else "")
        or len(set(re.findall(r"\d[\d,.]*", signal_values_text))) > 1
    )
    if not trigger:
        return current
    values: list[float] = []
    contract = CONTRACTS[CAPACITY]
    for view_id, prompt in (
        ("l7_capacity_exact_venue_direct", _capacity_recall_prompt(prediction.subject, "direct maximum capacity")),
        ("l7_capacity_exact_venue_variants", _capacity_recall_prompt(prediction.subject, "configuration variants")),
    ):
        if not caller.row_budget.can_spend():
            record.skipped.append("CapacityRepair: budget exhausted")
            break
        text = caller.generate(
            role="enumerator" if "direct" in view_id else "verifier",
            layer="L7",
            feature="CapacityRepair",
            prompt=prompt,
            view_id=view_id,
            max_new_tokens=112,
        )
        values.extend(numeric_values_from_text(text or "", contract))
    output = numeric_cluster_output(
        values, tolerance=config.numeric_cluster_tolerance, integer_only=True)
    if output:
        record.add_decision(
            "CapacityRepair",
            "numeric_consensus_cluster",
            values=values,
            output=output,
        )
        return [output]
    if values and caller.row_budget.can_spend():
        text = caller.generate(
            role="verifier",
            layer="L7",
            feature="NumericAttributeResolver",
            prompt=_numeric_resolver_prompt(prediction.subject, CAPACITY, values),
            view_id="l7_capacity_numeric_attribute_resolver",
            max_new_tokens=128,
        )
        resolved = numeric_values_from_text(text or "", contract)
        if resolved:
            output = format_numeric(resolved[0], integer_only=True)
            record.add_decision(
                "NumericAttributeResolver",
                "selected_relation_compatible_capacity",
                values=values,
                output=output,
            )
            return [output]
    elif values:
        record.skipped.append("Capacity NumericAttributeResolver: budget exhausted")
    return current


def repair_award(
    prediction: Prediction,
    _signals: Sequence[CandidateSignal],
    caller: RepairCaller,
    config: LeaderboardRepairConfig,
    record: RowRepairRecord,
) -> list[str]:
    values = list(prediction.object_entities)
    if config.features.award_metadata_cleanup:
        before = list(values)
        values = unique_by_strict_key(
            normalized for normalized in (normalize_award_metadata(v) for v in values)
            if normalized
        )
        if values != before:
            record.add_decision(
                "AwardMetadataNormalizer",
                "normalized_structural_metadata",
                before=before,
                after=values,
            )

    contract = CONTRACTS[AWARD]
    if config.features.award_time_sliced_recall:
        recalled: list[str] = []
        for period in ("early", "middle", "recent"):
            if not caller.row_budget.can_spend():
                record.skipped.append("AwardTimeSlicedRecall: budget exhausted")
                break
            text = caller.generate(
                role="enumerator",
                layer="L7",
                feature="AwardTimeSlicedRecall",
                prompt=_award_period_prompt(prediction.subject, period),
                view_id=f"l7_award_recall_{period}",
                max_new_tokens=384,
            )
            recalled.extend(supported_items_from_text(text or "", contract, ("RECIPIENTS", "WINNERS")))
        if recalled:
            before = list(values)
            values = unique_by_strict_key([*values, *recalled])
            record.add_decision(
                "AwardTimeSlicedRecall",
                "added_period_recalled_recipients",
                recalled=recalled,
                before=before,
                after=values,
            )

    if config.features.award_recipient_witness and values and caller.row_budget.can_spend():
        text = caller.generate(
            role="verifier",
            layer="L7",
            feature="AwardRecipientWitness",
            prompt=_award_witness_prompt(prediction.subject, values),
            view_id="l7_award_recipient_witness",
            max_new_tokens=512,
        )
        valid = choose_existing_surface(
            values,
            supported_items_from_text(text or "", contract, ("VALID", "RECIPIENTS", "YES")),
        )
        invalid = supported_items_from_text(text or "", contract, ("INVALID", "DROP", "NO"))
        if valid:
            before = list(values)
            # Unknowns are preserved; explicit invalids are removed.
            invalid_keys = {strict_key(v) for v in invalid}
            valid_keys = {strict_key(v) for v in valid}
            values = [
                value for value in values
                if strict_key(value) in valid_keys or strict_key(value) not in invalid_keys
            ]
            record.add_decision(
                "AwardRecipientWitness",
                "membership_witness_filter",
                valid=valid,
                invalid=invalid,
                before=before,
                after=values,
            )
    elif config.features.award_recipient_witness and values:
        record.skipped.append("AwardRecipientWitness: budget exhausted")
    return values


def _stock_type_prompt(subject: str, values: Sequence[str]) -> str:
    return (
        f"Exact company: {subject}\n"
        "Relation: stock exchanges where the exact company itself is publicly listed.\n"
        "Classify each candidate as KEEP only if it is a stock exchange venue. "
        "DROP city-only strings, ticker-only strings, index names, market segments, "
        "brokers, clearing/depositary entities, and company/parent/subsidiary names.\n"
        f"Candidates: {'; '.join(values)}\n"
        "Return lines: KEEP: ... and DROP: ... with short reasons."
    )


def _stock_alias_prompt(subject: str, values: Sequence[str]) -> str:
    return (
        f"Exact company: {subject}\n"
        "Group aliases that name the same stock exchange venue. Use generic "
        "acronym/full-name reasoning only; do not infer where the company is listed.\n"
        f"Venue candidates: {'; '.join(values)}\n"
        "Return lines like GROUP: canonical venue | alias one; alias two."
    )


def _stock_rescue_prompt(subject: str, candidates: Sequence[str]) -> str:
    return (
        f"Exact company: {subject}\n"
        "The strict final output has at most one venue, but the broad candidate "
        "graph contains possible exchange venues. Which of these are genuine "
        "stock exchanges where the EXACT company itself was publicly listed? "
        "Return ALL supported venues. Do not force cardinality one. Exclude a "
        "parent/subsidiary listing, historical-only mention, ticker, index, city, "
        "broker, segment, clearing or depositary entity.\n"
        f"Candidates: {'; '.join(candidates)}\n"
        "Return: RESTORE: venue1; venue2. Include confidence=0.0-1.0."
    )


def _border_sweep_prompt(subject: str, directions: str) -> str:
    return (
        f"Exact subject country or territory: {subject}\n"
        f"Find actual terrestrial land-boundary neighbours for {subject} by checking "
        f"these views: {directions}. Repeat the exact subject in your reasoning. "
        "Exclude maritime adjacency, nearby islands, bridge/causeway-only links, "
        "and political association without a terrestrial boundary.\n"
        "Return only candidate countries/territories as BORDERS: item; item."
    )


def _border_verify_prompt(subject: str, candidates: Sequence[str]) -> str:
    return (
        f"Exact subject: {subject}\n"
        "For each candidate, decide whether the pair shares an actual terrestrial "
        "land boundary. Exclude maritime adjacency, nearby island, bridge/causeway-only "
        "connection, and non-integral dependency edges.\n"
        f"Candidates paired with {subject}: {'; '.join(candidates)}\n"
        "Return VALID: ... INVALID: ... UNKNOWN: ..."
    )


def _death_status_prompt(subject: str) -> str:
    return (
        f"Exact person: {subject}\n"
        "Without using external lookup, classify life status from parametric memory: "
        "ALIVE, DECEASED, or UNKNOWN. Do not guess. Return one label and confidence=0.0-1.0."
    )


def _death_city_direct_prompt(subject: str) -> str:
    return (
        f"Exact person: {subject}\n"
        "Recall the city/town/locality where this exact person died. Exclude birth city, "
        "residence, nationality, burial place, career city, and hospital name unless "
        "you convert it to its city. Return CITY: name or NONE."
    )


def _death_city_locality_prompt(subject: str) -> str:
    return (
        f"Exact person: {subject}\n"
        "Recall the final locality or death-record location for this exact person. "
        "Return the city/town/locality only, not a country, region, hospital, burial "
        "place, residence, birth city, or career city. Return CITY: name or NONE."
    )


def _death_city_contrast_prompt(subject: str, candidates: Sequence[str]) -> str:
    return (
        f"Exact person: {subject}\n"
        "Candidate death localities disagree. Which single candidate is the actual "
        "city/town/locality of death? Exclude birth, residence, burial, nationality, "
        "career city, and hospitals unless converted to city.\n"
        f"Candidates: {'; '.join(candidates)}\n"
        "Return VALID: one city, or UNKNOWN."
    )


def _area_entity_type(subject: str) -> str:
    key = strict_key(subject)
    if "lake" in key:
        return "LAKE"
    if "island" in key or "(island)" in subject.lower():
        return "ISLAND"
    if len(key.split()) <= 2:
        return "COUNTRY"
    return "OTHER_GEO"


def _area_recall_prompt(subject: str, entity_type: str, view: str) -> str:
    if entity_type == "ISLAND":
        semantics = "area of the exact island, not municipality, province or archipelago"
    elif entity_type == "LAKE":
        semantics = "surface area of the exact lake, not basin, catchment, volume or shoreline"
    elif entity_type == "COUNTRY":
        semantics = "relation-compatible country total area semantics used in TRAIN"
    else:
        semantics = "surface area of the exact geographic entity only"
    return (
        f"Exact geographic entity: {subject}\n"
        f"Entity type: {entity_type}. View: {view}. Recall approximate area in square "
        f"kilometres; {semantics}. Return AREA: numeric km2 value only or NONE."
    )


def _capacity_recall_prompt(subject: str, view: str) -> str:
    return (
        f"Exact venue name and supplied location: {subject}\n"
        f"Recall {view}. Do not substitute another stadium/arena in the same city, "
        "a famous nearby venue, or a similarly named venue. Surface multiple "
        "remembered variants if relevant. Return CAPACITY: integer values only."
    )


def _numeric_resolver_prompt(subject: str, relation: str, values: Sequence[float]) -> str:
    if relation == CAPACITY:
        labels = (
            "CURRENT, HISTORICAL, SEATED, STANDING, CONCERT, ATTENDANCE_RECORD, OTHER. "
            "Prefer relation-compatible maximum spectator capacity."
        )
    else:
        labels = (
            "TOTAL_AREA, LAND_AREA, WATER_AREA, BASIN_OR_CATCHMENT, WRONG_ENTITY, OTHER. "
            "Prefer the relation-compatible exact entity area."
        )
    rendered = "; ".join(format_numeric(v, integer_only=(relation == CAPACITY)) for v in values)
    return (
        f"Exact subject: {subject}\nRelation: {relation}\n"
        f"Numeric candidates: {rendered}\n"
        f"Label candidates when possible: {labels}\n"
        "Return the best relation-compatible numeric value as VALUE: number, or UNKNOWN."
    )


def _award_period_prompt(subject: str, period: str) -> str:
    return (
        f"Exact award: {subject}\n"
        f"Recall {period} period recipients of this exact award. Include people, groups, "
        "organisations or projects if they actually received the award. Exclude nominees, "
        "jury/committee/selection-panel members, organizers, famous field figures, and "
        "similarly named awards. Return RECIPIENTS: item; item or NONE."
    )


def _award_witness_prompt(subject: str, values: Sequence[str]) -> str:
    return (
        f"Exact award: {subject}\n"
        "For each candidate X, did X actually receive this exact award? A concrete "
        "witness can be a year, edition, cycle or recipient event. Exclude famous "
        "in the field, nominee, jury member, committee member, selection-panel member, "
        "organizer, or similarly named award recipient.\n"
        f"Candidates: {'; '.join(values)}\n"
        "Return VALID: recipients with witness; INVALID: non-recipients; UNKNOWN: uncertain."
    )


def _parse_alias_groups(text: str, values: Sequence[str]) -> dict[str, str]:
    keys = {strict_key(v): v for v in values}
    groups: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.upper().startswith("GROUP:"):
            continue
        payload = line.split(":", 1)[1]
        if "|" in payload:
            group_name, aliases = payload.split("|", 1)
        else:
            group_name, aliases = payload, payload
        group_key = strict_key(group_name) or f"group-{len(groups)}"
        for alias in re.split(r"[;|]", aliases):
            alias_key = strict_key(alias)
            if alias_key in keys:
                groups[keys[alias_key]] = group_key
    return groups


def _first_agreement(groups: Sequence[Sequence[str]]) -> str | None:
    if len(groups) < 2:
        return None
    first = {strict_key(item): item for item in groups[0]}
    for later in groups[1:]:
        for item in later:
            key = strict_key(item)
            if key in first:
                return first[key]
    return None


def _round_number(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    return len(digits) >= 4 and digits.endswith(("000", "500"))


REPAIR_BY_RELATION = {
    STOCK: repair_stock,
    BORDERS: repair_borders,
    CITY: repair_city,
    AREA: repair_area,
    CAPACITY: repair_capacity,
    AWARD: repair_award,
}
