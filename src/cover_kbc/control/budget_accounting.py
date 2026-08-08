"""Precharge, protected reserves, settlement, and replay of recorded calls.

Three proposal sentences are load-bearing here, and each becomes a mechanism
rather than a comment.

*"Budget accounting must be cache-aware."* A cache hit performs no inference,
so it costs zero calls. The corollary is the dangerous one: an **unknown** cache
state must be reserved as a miss, because the mistake is only discovered after
the call has already been made.

*"Precharge before every neural call."* An action is authorised only if its
whole known call plan can be held first. Reserving the minimum and hoping is
how a hard ceiling gets crossed by a multi-call action that was already halfway
through.

*"No action may exceed the hard cap."* Reservation is atomic: either the
complete conservative cost fits, or the action is denied before anything runs.
There is no partial hold and no negative ledger.

On top of those, §9.3's protected reserve is enforced as a real constraint. A
verification floor that discovery may spend is not a floor, and the failure the
proposal names - hundreds of award candidates, almost none verified - is
exactly what happens when it is only a label.

**Cross-borrowing is undefined in the proposal, so the conservative reading is
taken and recorded here: a protected reserve is non-borrowable by unrelated
classes.** Verification's floor is reachable only by verification actions; a
special-purpose reserve is reachable only by actions tagged with that purpose.
Everything else spends from general capacity.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence

from cover_kbc.control.budget_types import (
    BudgetActionDescriptor,
    BudgetDenial,
    BudgetDenialReason,
    BudgetLedgerState,
    BudgetReservation,
    BudgetSchedulerError,
    BudgetSettlement,
    BudgetSpendClass,
    CacheDisposition,
    CallKind,
    PhysicalCallRecord,
    RelationBudgetPlan,
    ReplayReconciliation,
    ReservationStatus,
    SpecialReservePurpose,
    SubCall,
    reservation_id,
)


# --------------------------------------------------------------------------
# Cost plans for the architecture's real actions
# --------------------------------------------------------------------------


def score_label_call(label: str = "", *,
                     cache: CacheDisposition = CacheDisposition.NOT_CACHEABLE
                     ) -> SubCall:
    """One label-scoring call: one neural call, zero generated tokens.

    Audit 0010's rule. Treating it as free because nothing is generated is how
    a verifier-heavy query silently doubles its call count.
    """
    return SubCall(kind=CallKind.SCORE_LABELS, cache=cache, label=label)


def generation_call(max_generated_tokens: int, label: str = "", *,
                    cache: CacheDisposition = CacheDisposition.NOT_CACHEABLE
                    ) -> SubCall:
    """One generation call, reserved at its declared decode upper bound."""
    return SubCall(
        kind=CallKind.GENERATE, cache=cache,
        max_generated_tokens=max_generated_tokens, label=label,
    )


def specialist_verification_plan(
    *, readings: int, control_calls_needed: int, controls_total: int
) -> tuple[SubCall, ...]:
    """Module 17's call plan, cache-aware.

    One verification request is several real label readings plus contextual
    calibration controls that may already be cached. ``control_calls_needed``
    comes from the calibrator's own audited accounting, which returns zero for
    a template whose control is already measured - so a warm request reserves
    strictly fewer calls than a cold one, and no control is charged twice.
    """
    if readings < 0 or control_calls_needed < 0 or controls_total < 0:
        raise BudgetSchedulerError("a Module 17 call plan may not be negative")
    if control_calls_needed > controls_total:
        raise BudgetSchedulerError(
            f"{control_calls_needed} control calls needed exceeds the "
            f"{controls_total} controls this request has"
        )
    plan = [score_label_call(f"reading#{i}") for i in range(readings)]
    # The controls that are already measured cost nothing and are recorded as
    # hits rather than dropped, so the artefact shows what caching saved.
    for index in range(controls_total):
        cached = index >= control_calls_needed
        plan.append(score_label_call(
            f"control#{index}",
            cache=CacheDisposition.CACHE_HIT if cached
            else CacheDisposition.CACHE_MISS,
        ))
    return tuple(plan)


def structural_check_plan(max_generated_tokens: int) -> tuple[SubCall, ...]:
    """Module 18: one executed check is exactly one generation call.

    A candidate-free recall that names five candidates is still one physical
    call. Cost follows the call, never the evidence it produced.
    """
    return (generation_call(max_generated_tokens, "structural_check"),)


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class BudgetLedger:
    """Holds, releases and reconciles compute for one query.

    Deterministic throughout: reservation ids come from the query and action
    identity, never from a clock or an RNG, so a replayed run reconciles
    against the artefact it produced.
    """

    def __init__(self, plan: RelationBudgetPlan, *, prior_calls: int = 0,
                 prior_tokens: int = 0) -> None:
        """Open a ledger against one query's calibrated envelope.

        Args:
            plan: the numeric plan. Its ``hard_calls`` is the **calibrated
                whole-query envelope**, kept whole.
            prior_calls: physical calls this query already spent before Layer 4
                began - the acquisition phase. §16's envelope is whole-query, so
                that spend belongs inside it and the ledger opens with it
                already committed. Measured from the runtimes' own counters by
                the caller, never counted here, so there is one physical-call
                counter in the system and not two.
            prior_tokens: the same for generated tokens.

        Charged exactly once: the caller builds one ledger per query and caches
        it, so ``prior_*`` is read before any Layer-4 reservation exists. Every
        later call is charged through ``reserve``/``settle`` instead.
        """
        if not plan.is_numeric:
            raise BudgetSchedulerError(
                "a ledger needs a numeric calibration; the qualitative plan "
                "alone carries no capacity to reserve against"
            )
        if prior_calls < 0 or prior_tokens < 0:
            raise BudgetSchedulerError(
                f"prior spend cannot be negative ({prior_calls}, {prior_tokens})"
            )
        self.plan = plan
        self.calibration = plan.calibration
        self._sequence = 0
        self._reservations: dict[str, BudgetReservation] = {}
        self._settlements: list[BudgetSettlement] = []
        self._denials: list[BudgetDenial] = []
        self._reserved_calls = 0
        self._reserved_tokens = 0
        self._settled_calls = 0
        self._settled_tokens = 0
        #: Physical spend the query incurred before Layer 4 began. Held apart
        #: from reservations and settlements so it can never be released,
        #: settled or double-charged - it is already spent.
        self._prior_calls = int(prior_calls)
        self._prior_tokens = int(prior_tokens)
        self._class_committed: Counter[BudgetSpendClass] = Counter()
        self._purpose_committed: Counter[SpecialReservePurpose] = Counter()
        self._replayed_calls = 0

    # -- capacity ---------------------------------------------------------

    @property
    def prior_calls(self) -> int:
        """Physical calls spent by this query before Layer 4 began."""
        return self._prior_calls

    @property
    def committed_calls(self) -> int:
        """Everything charged against the whole-query envelope so far."""
        return self._prior_calls + self._reserved_calls + self._settled_calls

    @property
    def committed_tokens(self) -> int:
        return self._prior_tokens + self._reserved_tokens + self._settled_tokens

    def _protected_pools(self) -> list[tuple[str, int, int]]:
        """(name, size, used) for every protected pool."""
        pools = [(
            "verification",
            self.calibration.verification_reserve,
            self._class_committed[BudgetSpendClass.VERIFICATION],
        )]
        pools.extend(
            (f"special:{purpose.value}", size, self._purpose_committed[purpose])
            for purpose, size in self.calibration.special_reserves
        )
        return pools

    def _own_pool_available(self, descriptor: BudgetActionDescriptor) -> int:
        """Protected capacity this specific action may reach."""
        if descriptor.special_purpose is not None:
            size = self.calibration.reserve_for(descriptor.special_purpose)
            used = self._purpose_committed[descriptor.special_purpose]
            return max(0, size - used)
        if descriptor.spend_class is BudgetSpendClass.VERIFICATION:
            used = self._class_committed[BudgetSpendClass.VERIFICATION]
            return max(0, self.calibration.verification_reserve - used)
        return 0

    def _foreign_protected(self, descriptor: BudgetActionDescriptor) -> int:
        """Protected capacity this action may **not** touch.

        Held back from general availability, which is what makes a reserve a
        reserve rather than a label.
        """
        own = ""
        if descriptor.special_purpose is not None:
            own = f"special:{descriptor.special_purpose.value}"
        elif descriptor.spend_class is BudgetSpendClass.VERIFICATION:
            own = "verification"
        return sum(
            max(0, size - used)
            for name, size, used in self._protected_pools() if name != own
        )

    def available_calls(self, descriptor: BudgetActionDescriptor) -> int:
        """How many calls this action could actually fund."""
        hard_left = max(0, self.plan.hard_calls - self.committed_calls)
        general = max(0, hard_left - self._foreign_protected(descriptor))
        return min(hard_left, general + self._own_pool_available(descriptor))

    # -- reservation ------------------------------------------------------

    def _class_cap(self, spend_class: BudgetSpendClass) -> int:
        return (
            self.calibration.discovery_cap
            if spend_class is BudgetSpendClass.DISCOVERY
            else self.calibration.verification_cap
        )

    def _deny(self, descriptor: BudgetActionDescriptor,
              reason: BudgetDenialReason, requested: int, available: int,
              detail: str) -> BudgetDenial:
        denial = BudgetDenial(
            action_id=descriptor.action_id, reason=reason,
            requested_calls=requested, available_calls=available, detail=detail,
        )
        self._denials.append(denial)
        return denial

    def reserve(
        self, descriptor: BudgetActionDescriptor
    ) -> BudgetReservation | BudgetDenial:
        """Precharge one action's complete call plan, atomically.

        Returns a reservation or a **resource** denial. A denial says the
        action cannot be funded; it never says the query should stop, and it
        never says the action was not worth doing.
        """
        if descriptor.query_key != (
            self.plan.subject, self.plan.relation, self.plan.row_index
        ):
            raise BudgetSchedulerError(
                f"action {descriptor.action_id!r} belongs to "
                f"{descriptor.query_key}, not this ledger's "
                f"{(self.plan.subject, self.plan.relation, self.plan.row_index)}"
            )
        if (descriptor.special_purpose is not None
                and not self.plan.policy.declares(descriptor.special_purpose)):
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_UNDECLARED_PURPOSE, 0, 0,
                f"Table 6 does not declare {descriptor.special_purpose.value} "
                f"for {self.plan.relation}",
            )
        if not descriptor.cost_is_bounded:
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_UNKNOWN_COST, 0, 0,
                "the action declares no safe cost upper bound, so it cannot be "
                "reserved without risking the hard cap",
            )

        cost = descriptor.cost()
        calls, tokens = cost.neural_calls, cost.generated_tokens

        if self.committed_calls + calls > self.plan.hard_calls:
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_HARD_CAP, calls,
                max(0, self.plan.hard_calls - self.committed_calls),
                f"the complete action would take this query to "
                f"{self.committed_calls + calls} of {self.plan.hard_calls} calls",
            )
        if self.committed_tokens + tokens > self.plan.hard_generated_tokens:
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_TOKEN_CAP, calls,
                max(0, self.plan.hard_generated_tokens - self.committed_tokens),
                f"the action would reserve {tokens} generated tokens beyond the "
                f"ceiling {self.plan.hard_generated_tokens}",
            )

        cap = self._class_cap(descriptor.spend_class)
        if self._class_committed[descriptor.spend_class] + calls > cap:
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_CLASS_CAP, calls,
                max(0, cap - self._class_committed[descriptor.spend_class]),
                f"{descriptor.spend_class.value} is capped at {cap} calls",
            )

        available = self.available_calls(descriptor)
        if calls > available:
            return self._deny(
                descriptor, BudgetDenialReason.DENIED_BY_PROTECTED_RESERVE, calls,
                available,
                "the remaining capacity is protected for another class or "
                "purpose and is not borrowable",
            )

        self._sequence += 1
        identity = reservation_id(descriptor, self._sequence)
        if identity in self._reservations:
            raise BudgetSchedulerError(f"duplicate reservation id {identity!r}")

        envelope = (
            f"special:{descriptor.special_purpose.value}"
            if descriptor.special_purpose is not None
            else descriptor.spend_class.value.lower()
        )
        reservation = BudgetReservation(
            reservation_id=identity, descriptor=descriptor, reserved_calls=calls,
            reserved_generated_tokens=tokens, envelope_name=envelope,
            sequence=self._sequence,
        )
        self._reservations[identity] = reservation
        self._reserved_calls += calls
        self._reserved_tokens += tokens
        self._class_committed[descriptor.spend_class] += calls
        if descriptor.special_purpose is not None:
            self._purpose_committed[descriptor.special_purpose] += calls
        return reservation

    # -- settlement -------------------------------------------------------

    def _held(self, identity: str) -> BudgetReservation:
        reservation = self._reservations.get(identity)
        if reservation is None:
            raise BudgetSchedulerError(f"unknown reservation {identity!r}")
        if reservation.status is not ReservationStatus.OUTSTANDING:
            raise BudgetSchedulerError(
                f"reservation {identity!r} is already "
                f"{reservation.status.value.lower()}"
            )
        return reservation

    def settle(self, identity: str, *, actual_calls: int,
               actual_generated_tokens: int = 0) -> BudgetSettlement:
        """Reconcile a held reservation against actual runtime accounting.

        Actual runtime counters are authoritative (Audit 0010), but they may
        never exceed what was held: that would mean a call happened outside the
        precharge, which is the failure the precharge exists to prevent.
        """
        reservation = self._held(identity)
        if actual_calls < 0 or actual_generated_tokens < 0:
            raise BudgetSchedulerError("actual spend may not be negative")
        if actual_calls > reservation.reserved_calls:
            raise BudgetSchedulerError(
                f"reservation {identity!r} held {reservation.reserved_calls} "
                f"calls but {actual_calls} were spent; a neural call was made "
                "outside the precharge"
            )
        if actual_generated_tokens > reservation.reserved_generated_tokens:
            raise BudgetSchedulerError(
                f"reservation {identity!r} held "
                f"{reservation.reserved_generated_tokens} generated tokens but "
                f"{actual_generated_tokens} were spent"
            )

        released_calls = reservation.reserved_calls - actual_calls
        released_tokens = (
            reservation.reserved_generated_tokens - actual_generated_tokens)

        self._reserved_calls -= reservation.reserved_calls
        self._reserved_tokens -= reservation.reserved_generated_tokens
        self._settled_calls += actual_calls
        self._settled_tokens += actual_generated_tokens
        descriptor = reservation.descriptor
        self._class_committed[descriptor.spend_class] -= released_calls
        if descriptor.special_purpose is not None:
            self._purpose_committed[descriptor.special_purpose] -= released_calls

        self._reservations[identity] = BudgetReservation(
            reservation_id=reservation.reservation_id, descriptor=descriptor,
            reserved_calls=reservation.reserved_calls,
            reserved_generated_tokens=reservation.reserved_generated_tokens,
            envelope_name=reservation.envelope_name,
            status=ReservationStatus.SETTLED, sequence=reservation.sequence,
        )
        settlement = BudgetSettlement(
            reservation_id=identity, actual_calls=actual_calls,
            actual_generated_tokens=actual_generated_tokens,
            released_calls=released_calls, released_generated_tokens=released_tokens,
        )
        self._settlements.append(settlement)
        return settlement

    def cancel(self, identity: str) -> None:
        """Release a hold whose action never ran."""
        reservation = self._held(identity)
        self._reserved_calls -= reservation.reserved_calls
        self._reserved_tokens -= reservation.reserved_generated_tokens
        descriptor = reservation.descriptor
        self._class_committed[descriptor.spend_class] -= reservation.reserved_calls
        if descriptor.special_purpose is not None:
            self._purpose_committed[descriptor.special_purpose] -= (
                reservation.reserved_calls)
        self._reservations[identity] = BudgetReservation(
            reservation_id=reservation.reservation_id, descriptor=descriptor,
            reserved_calls=reservation.reserved_calls,
            reserved_generated_tokens=reservation.reserved_generated_tokens,
            envelope_name=reservation.envelope_name,
            status=ReservationStatus.CANCELLED, sequence=reservation.sequence,
        )

    # -- observation ------------------------------------------------------

    def note_replay(self, reconciliation: ReplayReconciliation) -> None:
        """Record replayed historical spend. Charges nothing."""
        self._replayed_calls = reconciliation.physical_calls

    @property
    def reservations(self) -> tuple[BudgetReservation, ...]:
        return tuple(
            self._reservations[key]
            for key in sorted(self._reservations,
                              key=lambda k: self._reservations[k].sequence)
        )

    @property
    def settlements(self) -> tuple[BudgetSettlement, ...]:
        return tuple(self._settlements)

    @property
    def denials(self) -> tuple[BudgetDenial, ...]:
        return tuple(self._denials)

    def state(self) -> BudgetLedgerState:
        return BudgetLedgerState(
            hard_calls=self.plan.hard_calls,
            hard_generated_tokens=self.plan.hard_generated_tokens,
            reserved_calls=self._reserved_calls,
            reserved_generated_tokens=self._reserved_tokens,
            settled_calls=self._settled_calls,
            settled_generated_tokens=self._settled_tokens,
            outstanding=sum(
                1 for r in self._reservations.values()
                if r.status is ReservationStatus.OUTSTANDING
            ),
            by_class=tuple(sorted(
                (c.value, n) for c, n in self._class_committed.items() if n
            )),
            by_purpose=tuple(sorted(
                (p.value, n) for p, n in self._purpose_committed.items() if n
            )),
            replayed_calls=self._replayed_calls,
        )


# --------------------------------------------------------------------------
# Shadow replay
# --------------------------------------------------------------------------


def replay_physical_calls(
    records: Iterable[PhysicalCallRecord],
) -> ReplayReconciliation:
    """Reconcile already-executed calls under Module 20's taxonomy.

    Spends nothing, charges nothing, mutates nothing. The mode is ``REPLAYED``
    rather than ``PRECHARGED`` because these calls were never precharged, and
    recording them as though they had been would invent a history the run does
    not have.

    Deduplication is by physical-call identity, so the representations a call
    accumulates downstream - an M11 record mined into a specialist observation,
    an M17 reading projected into Layer 4, an M18 generation naming five
    candidates - collapse to the one call that actually happened.
    """
    seen: dict[str, PhysicalCallRecord] = {}
    duplicates = 0
    for record in records:
        existing = seen.get(record.call_id)
        if existing is None:
            seen[record.call_id] = record
            continue
        if existing.identity_payload() != record.identity_payload():
            raise BudgetSchedulerError(
                f"two records claim physical call {record.call_id!r} with "
                "conflicting metadata; one physical call has one identity"
            )
        duplicates += 1

    unique = [seen[key] for key in sorted(seen)]
    by_module: Counter[str] = Counter()
    by_class: Counter[str] = Counter()
    by_purpose: Counter[str] = Counter()
    calls = tokens = hits = 0
    for record in unique:
        charged = record.charged_calls
        calls += charged
        tokens += record.generated_tokens if charged else 0
        hits += 1 if record.cache is CacheDisposition.CACHE_HIT else 0
        by_module[record.source_module] += charged
        by_class[record.spend_class.value] += charged
        if record.special_purpose is not None:
            by_purpose[record.special_purpose.value] += charged

    return ReplayReconciliation(
        physical_calls=calls, generated_tokens=tokens, cache_hits=hits,
        duplicates_collapsed=duplicates,
        by_module=tuple(sorted(by_module.items())),
        by_spend_class=tuple(sorted(by_class.items())),
        by_special_purpose=tuple(sorted(by_purpose.items())),
    )


def classify_generation_record(
    record: object, *, source_module: str, spend_class: BudgetSpendClass,
    action_kind: str = "", special_purpose: SpecialReservePurpose | None = None,
) -> PhysicalCallRecord:
    """Adapt a core :class:`GenerationRecord` into a physical-call record.

    Classification is by the *module and action identity the caller supplies*,
    never by parsing the prompt: a budget that depends on wording is a budget
    that changes when a prompt is reworded.
    """
    return PhysicalCallRecord(
        call_id=record.record_id, subject=record.query.subject,
        relation=record.query.relation,
        row_index=getattr(record.query, "row_index", 0),
        source_module=source_module,
        action_kind=action_kind or getattr(record.view_family, "value", ""),
        spend_class=spend_class, kind=CallKind.GENERATE,
        model_role=getattr(record.model_role, "value", str(record.model_role)),
        model_family=record.model_family, special_purpose=special_purpose,
        generated_tokens=record.generated_tokens or 0,
    )


def total_calls(records: Sequence[PhysicalCallRecord]) -> int:
    """Physical calls only. Cache hits are free."""
    return sum(record.charged_calls for record in records)


__all__ = [
    "BudgetLedger",
    "classify_generation_record",
    "generation_call",
    "replay_physical_calls",
    "score_label_call",
    "specialist_verification_plan",
    "structural_check_plan",
    "total_calls",
]
