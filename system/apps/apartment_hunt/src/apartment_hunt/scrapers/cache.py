"""Per-scraper cache for parsed detail page data.

Scrapers that fetch a separate detail page for each listing (e.g. Craigslist,
Equity) use this cache to skip re-fetching pages they've already parsed.  The
cache is keyed by detail URL and stored as JSON under
``~/.cache/apartmenthunt/scrapers/<slug>.json``.

The caller is responsible for deciding what to store (a parsed dict, a
serialized Pydantic model, etc.) — this class just persists JSON-serializable
values.

Staleness is handled at the call site by always refetching the index/search
page: listings that no longer appear on the index are simply not looked up in
the cache, so removed listings naturally drop out of the output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from apartment_hunt.paths import cache_dir as _cache_dir

logger = logging.getLogger(__name__)


class DetailCache:
    """JSON-backed cache of parsed detail page data keyed by URL."""

    def __init__(self, scraper_slug: str, cache_dir: Path | None = None):
        base = cache_dir if cache_dir is not None else _cache_dir() / "scrapers"
        self._path = base / f"{scraper_slug}.json"
        self._data: dict[str, Any] = self._load()
        self.hits = 0
        self.misses = 0

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load cache at %s; starting fresh", self._path)
            return {}

    def get(self, url: str) -> Any | None:
        value = self._data.get(url)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, url: str, value: Any) -> None:
        self._data[url] = value

    def retain(self, urls: set[str]) -> None:
        """Drop cached entries whose URL is not in the given set.

        Call this after processing all live URLs from an index page to keep
        the cache bounded.
        """
        self._data = {k: v for k, v in self._data.items() if k in urls}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data))
        logger.info(
            "Cache %s: %d hits, %d misses, %d entries persisted",
            self._path.name,
            self.hits,
            self.misses,
            len(self._data),
        )
