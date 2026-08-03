"""Append-only JSONL tracing of every model call.

Spec invariant 2: every model call is logged with prompt hash, model identity,
decoding config, token counts and provenance.  Invariant 3: every candidate in
the final output traces back to evidence-graph events.

Raw prompts and outputs are stored by default because they are what makes a
result auditable, but they can be suppressed for very large runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from cover_kbc.types import GenerationRecord


class RunTracer:
    """Writes one JSON object per model call to a JSONL file.

    Usable as a context manager; a no-op when constructed with ``path=None``.
    """

    def __init__(
        self,
        path: str | Path | None,
        *,
        store_prompts: bool = True,
        store_raw_output: bool = True,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.store_prompts = store_prompts
        self.store_raw_output = store_raw_output
        self._handle: TextIO | None = None
        self.count = 0

    def __enter__(self) -> RunTracer:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        if self.path is None or self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def log_record(self, record: GenerationRecord) -> None:
        """Trace one generation call."""
        payload = record.to_json()
        if not self.store_prompts:
            payload.pop("prompt", None)
        if not self.store_raw_output:
            payload.pop("raw_output", None)
        self.write({"kind": "generation", **payload})

    def write(self, payload: dict[str, Any]) -> None:
        """Trace an arbitrary event."""
        self.count += 1
        if self._handle is None:
            return
        self._handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
