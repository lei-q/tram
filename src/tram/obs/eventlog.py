"""Append-only JSONL event log - the black box every decision lands in."""

from __future__ import annotations

import datetime as dt
import json
import threading
from collections.abc import Iterator
from pathlib import Path

from tram.models.events import EventKind, TramEvent


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_seq = self._scan_last_seq()

    def _scan_last_seq(self) -> int:
        last = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    last = max(last, int(json.loads(line)["seq"]))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    continue  # malformed line: never crash the black box
        return last

    def append(
        self,
        kind: EventKind | str,
        source: str,
        data: dict | None = None,
        refs: dict[str, str] | None = None,
    ) -> TramEvent:
        if isinstance(kind, str):
            kind = EventKind(kind)
        with self._lock:
            event = TramEvent.model_validate(
                {
                    "seq": self._last_seq + 1,
                    "ts": dt.datetime.now(dt.UTC),
                    "kind": kind,
                    "source": source,
                    "data": data or {},
                    "refs": refs or {},
                }
            )
            self._last_seq = event.seq
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event.model_dump_json() + "\n")
        return event

    def read(self, kind: EventKind | str | None = None) -> Iterator[TramEvent]:
        if isinstance(kind, str) and kind:
            kind = EventKind(kind)
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = TramEvent.model_validate_json(line)
            except ValueError:
                continue
            if kind is None or event.kind == kind:
                yield event

    def last_seq(self) -> int:
        return self._last_seq

    def count(self) -> int:
        return sum(1 for _ in self.read())

    def contains_seq(self, seq: int) -> bool:
        return any(e.seq == seq for e in self.read())
