"""Geocoding utility using OpenStreetMap Nominatim."""

from __future__ import annotations

import logging
import re
import threading
import time

import httpx

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "apartmenthunt/0.1.0 (rental listing aggregator)"

# Rate limiting: Nominatim requires max 1 request per second.
_rate_lock = threading.Lock()
_last_request_time: float = 0.0


# Patterns that represent unit/apartment identifiers in addresses.
# These confuse geocoders and should be stripped before querying.
_UNIT_RE = re.compile(
    r"""
    \s*                           # optional leading whitespace
    (?:
        [#]\s*\S+                 # hash-style: #456, # 4B
      | (?:apt|unit|suite|ste|fl)  # keyword-style: Apt 4, Unit 2
        \.?\s*\S+
    )
    \s*$                          # anchored to end of string
    """,
    re.IGNORECASE | re.VERBOSE,
)


class GeocodingError(Exception):
    """Raised when geocoding fails for an address."""


def _strip_unit(address: str) -> str:
    """Remove unit/apartment identifiers from an address for geocoding."""
    return _UNIT_RE.sub("", address).strip()


def geocode(
    address: str, city: str | None = None, state: str | None = None
) -> tuple[float, float]:
    """Geocode an address to (lat, lng) using OpenStreetMap Nominatim.

    Strips unit/apartment numbers before querying since geocoders
    can't resolve sub-building addresses.

    Respects the Nominatim usage policy (max 1 request per second).

    Raises GeocodingError if the address cannot be geocoded.
    """
    global _last_request_time

    clean_address = _strip_unit(address)
    parts = [clean_address]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    query = ", ".join(parts)

    # Enforce rate limit
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_request_time = time.monotonic()

    log.debug("Geocoding: %s", query)
    try:
        resp = httpx.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise GeocodingError(f"Nominatim request failed for {query!r}: {exc}") from exc

    results = resp.json()
    if not results:
        raise GeocodingError(f"No geocoding results for {query!r}")

    try:
        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])
    except (KeyError, ValueError, IndexError) as exc:
        raise GeocodingError(
            f"Invalid geocoding response for {query!r}: {exc}"
        ) from exc

    log.debug("Geocoded %s -> (%f, %f)", query, lat, lng)
    return lat, lng
