"""JSONL-backed cache with in-memory dict.

Each cache entry is appended to a JSONL log on disk; on load, the log is
replayed into a dict so the latest value for each key wins. This gives an
audit trail of every lookup without needing a database.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class JsonlCache:
    """Append-only JSONL log replayed into an in-memory dict.

    The log is the source of truth on disk. The dict is rebuilt from it on
    construction. Writes append to the log and update the dict in one shot.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._store: dict[str, Any] = {}
        if self.path.exists():
            self._replay()

    def _replay(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                self._store[entry["key"]] = entry["value"]

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "value": value, "ts": time.time()}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self._store)
