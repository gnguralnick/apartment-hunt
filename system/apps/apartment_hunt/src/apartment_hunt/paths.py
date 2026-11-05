from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Root directory for this app's persistent state.

    Defaults to ``data/.apps/apartment-hunt/`` (cwd-relative, resolved from the
    repo root where the supervised service runs) but honors the
    ``APARTMENT_HUNT_DATA_DIR`` env var so an editing agent can point a throwaway
    instance at a copy of the data. Holds the per-scraper JSONL files, the user
    annotation state, the preferences file, and scraper/travel caches.
    """
    return Path(os.environ.get("APARTMENT_HUNT_DATA_DIR", "data/.apps/apartment-hunt"))


def config_path() -> Path:
    """Path to the user's editable preferences file under the data dir."""
    return data_dir() / "preferences.yaml"


def cache_dir() -> Path:
    """Directory for scraper detail-page and travel-time caches."""
    return data_dir() / "cache"
