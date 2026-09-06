"""JSONL-backed cache with in-memory dict.

Each cache entry is appended to a JSONL log on disk; on load, the log is
replayed into a dict so the latest value for each key wins. This gives an
audit trail of every lookup without needing a database.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from citefinder.models import CacheRow

log = logging.getLogger("citefinder")


def read_records(path: Path) -> list[CacheRow]:
    """Every readable `{key, value, ts}` row of a JSONL cache, in file order.

    A line that does not decode, does not parse, or lacks `key`/`value` is
    skipped with a warning: a write interrupted mid-append (crash, disk full)
    leaves a partial line, and losing that one record is the documented
    failure mode; losing the whole cache to it is not. Lines are decoded one
    at a time, so a write torn inside a multi-byte character is one unreadable
    line, not an unreadable file. Duplicate keys are kept; `JsonlCache` lets
    the latest win on replay, other readers (`citefinder drift`) see each.
    """
    records: list[CacheRow] = []
    with path.open("rb") as f:
        for lineno, raw in enumerate(f, 1):
            try:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                entry = json.loads(line)
                entry["key"], entry["value"]
            except (ValueError, KeyError, TypeError):
                log.warning("%s:%d: skipping unreadable cache line", path, lineno)
                continue
            records.append(entry)
    return records


class JsonlCache:
    """Append-only JSONL log replayed into an in-memory dict.

    The log is the source of truth on disk. The dict is rebuilt from it on
    construction. Writes append to the log and update the dict in one shot.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._store: dict[str, Any] = {}
        self._needs_newline = False
        if self.path.exists():
            self._replay()

    def _replay(self) -> None:
        for record in read_records(self.path):
            self._store[record["key"]] = record["value"]
        # A last line with no newline is an interrupted write; the next `put`
        # has to start on a fresh line or both records are lost.
        with self.path.open("rb") as f:
            f.seek(0, 2)
            if f.tell() == 0:
                self._needs_newline = False
            else:
                f.seek(-1, 2)
                self._needs_newline = f.read(1) != b"\n"

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def put(self, key: str, value: Any) -> None:
        # Serialise first: a value json can't encode must not land in the
        # dict while nothing reaches disk, or memory and file disagree until
        # the next reload silently drops the key.
        record = {"key": key, "value": value, "ts": time.time()}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            if self._needs_newline:
                f.write("\n")
                self._needs_newline = False
            f.write(line)
        self._store[key] = value

    def __len__(self) -> int:
        return len(self._store)
