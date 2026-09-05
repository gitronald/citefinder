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

log = logging.getLogger("citefinder")


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
        raw = ""
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    key, value = entry["key"], entry["value"]
                except (ValueError, KeyError, TypeError):
                    # A write interrupted mid-append (crash, disk full) leaves
                    # a partial line. Losing that one record is the documented
                    # failure mode; losing the whole cache to it is not.
                    log.warning(
                        "%s:%d: skipping unreadable cache line", self.path, lineno
                    )
                    continue
                self._store[key] = value
        # A last line with no newline is that interrupted write; the next
        # `put` has to start on a fresh line or both records are lost.
        self._needs_newline = bool(raw) and not raw.endswith("\n")

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "value": value, "ts": time.time()}
        with self.path.open("a", encoding="utf-8") as f:
            if self._needs_newline:
                f.write("\n")
                self._needs_newline = False
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self._store)
