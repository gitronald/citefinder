"""Shared HTTP-with-cache plumbing for Crossref and OpenAlex clients.

Both clients fetch JSON over HTTPS, treat 404 as a cached `None`, and key the
cache by URL with any polite-pool `mailto` stripped (so changing the email
on a request doesn't invalidate prior cache entries).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from citefinder.cache import JsonlCache

DEFAULT_TIMEOUT = 30.0


def _default_user_agent() -> str:
    try:
        ver = version("citefinder")
    except PackageNotFoundError:
        ver = "0.0.0"
    return f"citefinder/{ver} (https://github.com/gitronald/citefinder)"


def _strip_mailto(url: str) -> str:
    """Return `url` with any `mailto` query param removed."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [(k, v) for k, v in parse_qsl(parts.query) if k != "mailto"]
    return str(urlunsplit(parts._replace(query=urlencode(pairs))))


class CachedJsonClient:
    """HTTP client that caches JSON GETs by URL, with 404 cached as `None`.

    `mailto`, if set, is appended as a `?mailto=...` query param on outgoing
    requests (Crossref and OpenAlex both honor this for their polite pools)
    and stripped from the cache key so the same record cached under one
    email is reused after a rotation.
    """

    def __init__(
        self,
        cache: JsonlCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if cache is None and cache_path is not None:
            cache = JsonlCache(cache_path)
        self.cache = cache
        self.mailto = mailto
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or _default_user_agent()

    def _cache_key(self, url: str) -> str:
        return _strip_mailto(url)

    def _request_url(self, url: str) -> str:
        if not self.mailto:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode({'mailto': self.mailto})}"

    def _get(self, url: str) -> Any | None:
        cache_key = self._cache_key(url)
        if self.cache is not None and cache_key in self.cache:
            return self.cache.get(cache_key)
        response = self.session.get(self._request_url(url), timeout=self.timeout)
        if response.status_code == 404:
            value: Any | None = None
        else:
            response.raise_for_status()
            value = response.json()
        if self.cache is not None:
            self.cache.put(cache_key, value)
        return value
