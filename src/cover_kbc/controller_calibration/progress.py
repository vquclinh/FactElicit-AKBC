"""Live progress and cumulative accounting for a long collection run.

A 477-row frozen-model run is hours of silence unless something says otherwise,
and silence is indistinguishable from a hang. So the counters are a first-class
object rather than print statements scattered through the harness: they are
testable, they are checkpointed, and they are the same numbers the manifest
reports at the end.

The ETA deliberately stays ``None`` until a few rows have finished. An estimate
extrapolated from one row is not an estimate, and a confident wrong number is
worse than an honest blank.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: Rows to complete before an ETA is meaningful enough to print.
MIN_ROWS_FOR_ETA = 5


@dataclass
class RunCounters:
    """Cumulative physical accounting for one collection run."""

    total_rows: int
    rows_completed: int = 0
    rows_failed: int = 0
    physical_model_calls: int = 0
    enumerator_calls: int = 0
    verifier_calls: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def charge(self, *, role: str, calls: int = 0, prompt_tokens: int = 0,
               generated_tokens: int = 0) -> None:
        """Record physical work exactly once, against exactly one role.

        The per-role counters are a partition of ``physical_model_calls``, not
        an independent tally: a call charged to both roles, or to neither,
        would make every derived cost estimate wrong.
        """
        if calls < 0 or prompt_tokens < 0 or generated_tokens < 0:
            raise ValueError("physical accounting cannot be negative")
        self.physical_model_calls += calls
        if role == "enumerate":
            self.enumerator_calls += calls
        elif role == "verify":
            self.verifier_calls += calls
        elif calls:
            raise ValueError(
                f"cannot charge {calls} call(s) to unknown model role {role!r}; "
                "every physical call belongs to exactly one role"
            )
        self.prompt_tokens += prompt_tokens
        self.generated_tokens += generated_tokens

    @property
    def rows_attempted(self) -> int:
        return self.rows_completed + self.rows_failed

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def seconds_per_row(self) -> float | None:
        if self.rows_completed <= 0:
            return None
        return self.elapsed_seconds / self.rows_completed

    @property
    def eta_seconds(self) -> float | None:
        rate = self.seconds_per_row
        if rate is None or self.rows_completed < MIN_ROWS_FOR_ETA:
            return None
        return rate * max(0, self.total_rows - self.rows_attempted)

    @property
    def percent(self) -> float:
        if self.total_rows <= 0:
            return 0.0
        return 100.0 * self.rows_completed / self.total_rows

    def to_json(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "rows_completed": self.rows_completed,
            "rows_failed": self.rows_failed,
            "physical_model_calls": self.physical_model_calls,
            "enumerator_calls": self.enumerator_calls,
            "verifier_calls": self.verifier_calls,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "seconds_per_row": (round(self.seconds_per_row, 3)
                                if self.seconds_per_row is not None else None),
            "eta_seconds": (round(self.eta_seconds, 3)
                            if self.eta_seconds is not None else None),
        }

    @classmethod
    def restore(cls, payload: dict[str, Any], *, total_rows: int) -> "RunCounters":
        """Rebuild counters from a checkpoint, restarting the clock.

        Elapsed time is not restored: the wall clock of an interrupted session
        says nothing about this one, and carrying it forward would poison the
        rate and the ETA.
        """
        counters = cls(total_rows=total_rows)
        for name in ("rows_completed", "rows_failed", "physical_model_calls",
                     "enumerator_calls", "verifier_calls", "prompt_tokens",
                     "generated_tokens"):
            setattr(counters, name, int(payload.get(name, 0)))
        return counters


def format_duration(seconds: float | None) -> str:
    """``h:mm:ss``, or ``--`` when there is not yet an honest answer."""
    if seconds is None:
        return "--"
    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def summary_block(counters: RunCounters, *, label: str = "TRAIN progress") -> str:
    """The periodic cumulative summary."""
    return "\n".join([
        f"{label}:{'':<8}{counters.rows_completed} / {counters.total_rows} "
        f"({counters.percent:.1f}%)",
        f"  failed:            {counters.rows_failed}",
        f"  physical calls:    {counters.physical_model_calls}",
        f"  enumerator calls:  {counters.enumerator_calls}",
        f"  verifier calls:    {counters.verifier_calls}",
        f"  prompt tokens:     {counters.prompt_tokens}",
        f"  generated tokens:  {counters.generated_tokens}",
        f"  elapsed:           {format_duration(counters.elapsed_seconds)}",
        f"  avg/query:         "
        f"{counters.seconds_per_row:.1f}s" if counters.seconds_per_row
        else "  avg/query:         --",
        f"  ETA:               {format_duration(counters.eta_seconds)}",
    ])


def query_line(index: int, total: int, *, relation: str, subject: str) -> str:
    """``[TRAIN 21/477] relation=... subject="..."`` - current/total is mandatory."""
    return f'[TRAIN {index}/{total}] relation={relation} subject="{subject}"'


def round_line(index: int, total: int, *, round_index: int, detail: str) -> str:
    """Within-query progress.

    ``round=n`` and never ``n/N``: the number of rounds an adaptive controller
    will take is unknown while it is taking them, and inventing a denominator
    would be a fabricated progress bar.
    """
    return f"[TRAIN {index}/{total}][round={round_index}] {detail}"


__all__ = [
    "MIN_ROWS_FOR_ETA",
    "RunCounters",
    "format_duration",
    "query_line",
    "round_line",
    "summary_block",
]
