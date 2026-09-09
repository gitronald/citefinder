"""JSONL-backed cache with in-memory dict.

Each cache entry is appended to a JSONL log on disk; on load, the log is
replayed into a dict so the latest value for each key wins. This gives an
audit trail of every lookup without needing a database.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


# --- Maintenance ------------------------------------------------------------
#
# Consolidating caches is a separate, explicit step: nothing here runs as a
# side effect of a lookup, so a misdirected or stale row is never laundered
# into the shared cache by a routine `doi` or `verify` run.

SOURCE_HOSTS: dict[str, str] = {
    "api.crossref.org": "crossref",
    "api.openalex.org": "openalex",
}
"""Request host -> the source that answered it. The only reliable router."""

# A row with no `ts` loses every contest rather than being dropped: it is a
# real record, it just carries no freshness signal.
_OLDEST = float("-inf")


def source_for_key(key: Any) -> str | None:
    """Which source a cache key was fetched from, by the host in the key.

    `None` when the host is neither API's (a key from another tool, or a
    malformed row). Never routes by file name: a cache file's name does not
    guarantee its contents, because a misdirected record has been seen in
    practice (see `CacheRow`).
    """
    if not isinstance(key, str):
        return None
    return SOURCE_HOSTS.get(urlsplit(key).netloc.lower())


def row_ts(row: CacheRow) -> float:
    """A row's fetch time, or `-inf` when it carries none or a non-number."""
    ts = row.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return _OLDEST
    return float(ts)


@dataclass
class MergeStats:
    """What one `merge_caches` pass read, decided, and found wrong."""

    files: int = 0
    """Files opened, whether or not they held a row for the merged source."""
    rows_read: int = 0
    """Rows the merge took in — after routing, so filtered rows are not here."""
    keys: int = 0
    replaced: int = 0
    """Contests where a newer row displaced the one held so far."""
    nulls_superseded: int = 0
    """Cached 404s replaced by a real record — what a merge is mainly for."""
    records_superseded: int = 0
    """Records replaced by a later 404. `keep_records` prevents these."""
    records_kept: int = 0
    """Newer 404s refused because `keep_records` was set."""
    missing_ts: int = 0
    misrouted: int = 0
    """Rows whose host disagrees with the source their file is named for."""
    unroutable: int = 0
    """Rows whose host is neither API's. Never reach the output."""


@dataclass
class SourceStats:
    """One source's slice of a cache inventory, from `summarize_caches`."""

    files: int = 0
    """Files that carried at least one row for this source."""
    rows: int = 0
    keys: int = 0
    lookups: int = 0
    """Distinct keys that are single-record lookups (`/works/...`)."""
    searches: int = 0
    """Distinct keys that are search pages (`/works?...`)."""
    misses: int = 0
    """Distinct keys whose newest row is a cached 404."""
    missing_ts: int = 0
    newest_ts: float | None = None
    _seen: set[str] = field(default_factory=set, repr=False)


@dataclass
class _Contest:
    """Every routed row seen for one key, and what they were.

    `candidates` holds one row per file (line order already applied); the
    newest-`ts` winner is picked from those. `newest_null` and
    `newest_record` span *all* rows for the key, including ones a later line
    in their own file shadowed, so the null-versus-record report reads the
    same however the files were globbed.
    """

    candidates: list[CacheRow] = field(default_factory=list)
    rows: int = 0
    newest_null: float | None = None
    newest_record: float | None = None

    def saw(self, row: CacheRow) -> None:
        self.rows += 1
        ts = row_ts(row)
        if row.get("value") is None:
            self.newest_null = max(ts, self.newest_null or _OLDEST)
        else:
            self.newest_record = max(ts, self.newest_record or _OLDEST)

    def resolve(self, keep_records: bool, stats: MergeStats) -> CacheRow:
        """The winning row, folding this key's outcome into `stats`."""
        stats.replaced += self.rows - 1
        winner = max(self.candidates, key=row_ts)
        if keep_records and winner.get("value") is None:
            records = [r for r in self.candidates if r.get("value") is not None]
            if records:
                winner = max(records, key=row_ts)
        if winner.get("value") is None:
            if self.newest_record is not None:
                stats.records_superseded += 1
        elif self.newest_null is not None:
            # A null the winner outlives was superseded; one that outlives the
            # winner only lost because `keep_records` refused it.
            if self.newest_null > row_ts(winner):
                stats.records_kept += 1
            else:
                stats.nulls_superseded += 1
        return winner


def merge_caches(
    paths: Iterable[str | Path],
    *,
    source: str | None = None,
    keep_records: bool = False,
) -> tuple[list[CacheRow], MergeStats]:
    """Merge JSONL caches into one winning row per key, newest `ts` first.

    Each file is first collapsed by line order (latest line wins, as
    `JsonlCache` replays it), then the per-file winners are merged by `ts`:
    two caches are independent logs, so the order they are read in says
    nothing about which record is current. The winner keeps its own `ts` —
    that is the fetch time, the only freshness signal a row carries — and the
    returned rows are ordered oldest first, so writing them back yields a
    file a further merge leaves unchanged.

    Rows are routed by the host in their key (`source_for_key`). `source`
    keeps only that source's rows and drops the rest, so a row cannot be
    laundered into a cache it does not belong in — and a record that landed
    in the wrong file reaches the right one, counted as `misrouted`. `source`
    of `None` — what compacting a single file in place wants — keeps every
    row and only reports what it could not route. Pass every cache file to
    each source's merge; which file a row sits in decides nothing.

    A cached 404 that is newer than a record replaces it, and the count is
    reported every run; `keep_records` makes a 404 never displace a record,
    for when a transient upstream failure is the likelier explanation.

    Missing files are skipped silently — a merge names where caches *may* be,
    and a source with no cache yet is not an error.
    """
    stats = MergeStats()
    contests: dict[str, _Contest] = {}
    known_sources = set(SOURCE_HOSTS.values())
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        stats.files += 1
        file_source = path.stem if path.stem in known_sources else None
        collapsed: dict[str, CacheRow] = {}
        for row in read_records(path):
            key = row.get("key")
            if not isinstance(key, str):
                stats.unroutable += 1
                continue
            key_source = source_for_key(key)
            if key_source is None:
                stats.unroutable += 1
                # Dropped only when merging *into* a source's cache, where a
                # foreign row does not belong. Compacting a file in place
                # keeps them: nothing is laundered anywhere, and rewriting
                # someone's cache must not silently lose readable rows.
                if source is not None:
                    continue
            elif source is not None and key_source != source:
                continue
            elif file_source is not None and key_source != file_source:
                # Counted only for rows this merge keeps, so the number says
                # how many records the run moves to the source that answered
                # them — not how many it walked past.
                stats.misrouted += 1
            stats.rows_read += 1
            if row_ts(row) == _OLDEST:
                stats.missing_ts += 1
            contests.setdefault(key, _Contest()).saw(row)
            # Line order is time order inside one append-only log, so a later
            # line wins outright — except over a record when `keep_records`
            # is set, which is the one rule that holds everywhere.
            held = collapsed.get(key)
            if (
                keep_records
                and row.get("value") is None
                and held is not None
                and held.get("value") is not None
            ):
                continue
            collapsed[key] = row
        for key, row in collapsed.items():
            contests[key].candidates.append(row)
    rows = sorted(
        (c.resolve(keep_records, stats) for c in contests.values()), key=row_ts
    )
    stats.keys = len(rows)
    return rows, stats


def summarize_caches(paths: Iterable[str | Path]) -> dict[str, SourceStats]:
    """Inventory the rows in `paths`, grouped by the source that answered them.

    Read-only counterpart to `merge_caches`, sharing its routing: keys are
    counted per host, not per file name, and unroutable rows land under
    `"unroutable"` rather than being ignored. Distinct-key counts use the
    same newest-`ts`-wins rule, so `misses` is the number of keys a lookup
    would find a cached 404 for today.

    Missing files are skipped; a caller that needs a missing *directory* to
    be an error (a dropped mount must not read as an empty cache) checks
    that before globbing.
    """
    summary: dict[str, SourceStats] = {}
    latest: dict[str, dict[str, CacheRow]] = {}
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        seen_here: set[str] = set()
        for row in read_records(path):
            name = source_for_key(row.get("key")) or "unroutable"
            stats = summary.setdefault(name, SourceStats())
            if name not in seen_here:
                seen_here.add(name)
                stats.files += 1
            stats.rows += 1
            ts = row_ts(row)
            if ts == _OLDEST:
                stats.missing_ts += 1
            elif stats.newest_ts is None or ts > stats.newest_ts:
                stats.newest_ts = ts
            key = row["key"]
            if not isinstance(key, str):
                continue
            current = latest.setdefault(name, {}).get(key)
            if current is None or ts >= row_ts(current):
                latest[name][key] = row
    for name, rows in latest.items():
        stats = summary[name]
        stats.keys = len(rows)
        for key, row in rows.items():
            if "/works?" in key:
                stats.searches += 1
            else:
                stats.lookups += 1
            if row.get("value") is None:
                stats.misses += 1
    return summary


def write_records(path: str | Path, rows: Iterable[CacheRow]) -> None:
    """Write `rows` as a JSONL cache at `path`, never in place.

    The lines go to `<path>.tmp` and are moved over `path` with
    `os.replace`, so a crash mid-write leaves the original intact and a
    concurrent appender loses nothing it had already written to the old
    file. On a synced or network filesystem holding the target open, the
    replace fails outright — the desired outcome, and the reason this is not
    a truncate-and-rewrite.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, target)
