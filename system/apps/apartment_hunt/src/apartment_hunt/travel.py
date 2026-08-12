from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Protocol

import httpx

from apartment_hunt.paths import cache_dir as _cache_dir

logger = logging.getLogger(__name__)


class TravelTimeCalculator(Protocol):
    """Protocol for computing travel times from multiple origins to a destination."""

    def get_travel_times(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
    ) -> list[float | None]:
        """Return travel time in minutes for each origin to the destination.

        Args:
            origins: List of (lat, lng) pairs.
            destination: (lat, lng) of the destination.
            mode: "walking" or "transit".

        Returns:
            List of travel times in minutes, one per origin.
            None for origins where the route is not possible.
        """
        ...


class HaversineTravelTimeCalculator:
    """Estimates travel time from straight-line distance using average speeds.

    Useful as a fallback when no routing API key is available, or for testing.
    """

    EARTH_RADIUS_MILES = 3958.8
    WALK_SPEED_MPH = 3.0
    TRANSIT_SPEED_MPH = 15.0
    DETOUR_FACTOR = 1.3

    def _haversine_miles(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
        )
        return 2 * self.EARTH_RADIUS_MILES * math.asin(math.sqrt(a))

    def get_travel_times(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
    ) -> list[float | None]:
        speed = self.WALK_SPEED_MPH if mode == "walking" else self.TRANSIT_SPEED_MPH
        dest_lat, dest_lng = destination
        times: list[float | None] = []
        for lat, lng in origins:
            dist = self._haversine_miles(lat, lng, dest_lat, dest_lng)
            time_hours = (dist * self.DETOUR_FACTOR) / speed
            times.append(time_hours * 60)
        return times


class GoogleRoutesTravelTimeCalculator:
    """Travel time calculator using the Google Routes API's computeRouteMatrix method.

    Replaces the legacy Distance Matrix API. Batches origins into matrix requests
    (one destination per call, matching the caller's usage) and caches results to
    minimize API spend across runs.
    """

    API_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    # Per-axis cap is 25 and the transit element limit is 100 (origins × destinations).
    # We always send one destination per call, so 25 origins is safely within both.
    BATCH_SIZE = 25
    FIELD_MASK = "originIndex,destinationIndex,duration,condition,status"
    # "walking"/"transit" are the interface vocabulary (carried over from the legacy
    # Distance Matrix implementation); Routes API uses "WALK"/"TRANSIT".
    _MODE_TRANSLATION = {"walking": "WALK", "transit": "TRANSIT"}

    def __init__(self, api_key: str, cache_path: Path | None = None):
        self._api_key = api_key
        # New filename — Routes API results are not comparable to legacy Distance Matrix
        # results (different routing engines), so don't reuse the old cache file.
        self._cache_path = cache_path or _cache_dir() / "travel_times_routes.json"
        self._cache: dict[str, float | None] = self._load_cache()

    def _load_cache(self) -> dict[str, float | None]:
        if self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache))

    @staticmethod
    def _cache_key(lat: float, lng: float, dest_lat: float, dest_lng: float, mode: str) -> str:
        return f"{lat:.5f},{lng:.5f}|{dest_lat:.5f},{dest_lng:.5f}|{mode}"

    def get_travel_times(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
    ) -> list[float | None]:
        travel_mode = self._MODE_TRANSLATION.get(mode)
        if travel_mode is None:
            raise ValueError(f"Unsupported travel mode: {mode!r}")

        dest_lat, dest_lng = destination
        results: list[float | None] = [None] * len(origins)

        # Resolve cache hits first
        uncached_indices: list[int] = []
        for i, (lat, lng) in enumerate(origins):
            key = self._cache_key(lat, lng, dest_lat, dest_lng, mode)
            if key in self._cache:
                results[i] = self._cache[key]
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            return results

        logger.info(
            "Querying Google Routes computeRouteMatrix: %d origins, mode=%s (%d cached)",
            len(uncached_indices),
            mode,
            len(origins) - len(uncached_indices),
        )

        for batch_start in range(0, len(uncached_indices), self.BATCH_SIZE):
            batch_indices = uncached_indices[batch_start : batch_start + self.BATCH_SIZE]
            batch_origins = [origins[i] for i in batch_indices]
            batch_results = self._call_matrix(batch_origins, destination, travel_mode)

            for local_idx, element in batch_results.items():
                idx = batch_indices[local_idx]
                results[idx] = element
                lat, lng = origins[idx]
                key = self._cache_key(lat, lng, dest_lat, dest_lng, mode)
                self._cache[key] = element

        self._save_cache()
        return results

    def _call_matrix(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        travel_mode: str,
    ) -> dict[int, float | None]:
        """Call computeRouteMatrix for one batch; return {origin_index -> minutes|None}."""
        dest_lat, dest_lng = destination
        body: dict[str, Any] = {
            "origins": [_waypoint(lat, lng) for lat, lng in origins],
            "destinations": [_waypoint(dest_lat, dest_lng)],
            "travelMode": travel_mode,
        }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": self.FIELD_MASK,
        }

        resp = httpx.post(self.API_URL, json=body, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.warning(
                "Routes API error: HTTP %s — %s", resp.status_code, resp.text[:300]
            )
            return {}

        data = resp.json()
        # Matrix endpoint returns a JSON array of RouteMatrixElement, possibly out of
        # order — use originIndex/destinationIndex to map back.
        results: dict[int, float | None] = {i: None for i in range(len(origins))}
        for element in data:
            origin_idx = element.get("originIndex")
            if origin_idx is None or origin_idx not in results:
                continue

            # A missing status object or status with no "code" field means OK
            # (google.rpc.Status uses code=0 for OK and omits it when marshalled).
            status = element.get("status") or {}
            if status.get("code", 0) != 0:
                results[origin_idx] = None
                continue

            if element.get("condition") != "ROUTE_EXISTS":
                results[origin_idx] = None
                continue

            duration_str = element.get("duration")
            results[origin_idx] = _parse_duration_seconds(duration_str)

        return results


def _waypoint(lat: float, lng: float) -> dict[str, Any]:
    return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}


def _parse_duration_seconds(duration: str | None) -> float | None:
    """Parse a Routes API duration string like '1234s' into minutes.

    Returns None if the value is missing or malformed.
    """
    if not duration or not duration.endswith("s"):
        return None
    try:
        return float(duration[:-1]) / 60.0
    except ValueError:
        return None
