from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from apartment_hunt.paths import config_path as DEFAULT_CONFIG_PATH

TIER_POINTS = {"strong": 10, "moderate": 5, "low": 2}


@dataclass
class RangeFilter:
    min: float | None = None
    max: float | None = None


@dataclass
class Filters:
    rent: RangeFilter = field(default_factory=RangeFilter)
    bedrooms: RangeFilter = field(default_factory=RangeFilter)
    bathrooms: RangeFilter = field(default_factory=RangeFilter)
    sqft: RangeFilter = field(default_factory=RangeFilter)
    city: str | None = None
    pets_allowed: bool | None = None


@dataclass
class Preference:
    name: str
    tier: str
    points: int


@dataclass
class WaypointMode:
    max_minutes: int


@dataclass
class Waypoint:
    name: str
    lat: float
    lng: float
    tier: str
    points: int
    modes: dict[str, WaypointMode]


@dataclass
class Config:
    filters: Filters
    preferences: list[Preference]
    waypoints: list[Waypoint]


def _parse_range_filter(raw: dict | None) -> RangeFilter:
    if raw is None:
        return RangeFilter()
    return RangeFilter(min=raw.get("min"), max=raw.get("max"))


def _parse_filters(raw: dict | None) -> Filters:
    if raw is None:
        return Filters()
    return Filters(
        rent=_parse_range_filter(raw.get("rent")),
        bedrooms=_parse_range_filter(raw.get("bedrooms")),
        bathrooms=_parse_range_filter(raw.get("bathrooms")),
        sqft=_parse_range_filter(raw.get("sqft")),
        city=raw.get("city"),
        pets_allowed=raw.get("pets_allowed"),
    )


def _parse_preferences(raw: dict | None) -> list[Preference]:
    if raw is None:
        return []
    prefs = []
    for name, spec in raw.items():
        tier = spec.get("tier", "moderate")
        prefs.append(Preference(name=name, tier=tier, points=TIER_POINTS[tier]))
    return prefs


def _parse_waypoints(raw: list | None) -> list[Waypoint]:
    if raw is None:
        return []
    waypoints = []
    for wp in raw:
        tier = wp.get("tier", "moderate")
        raw_modes = wp.get("modes", {})
        modes: dict[str, WaypointMode] = {}
        for mode_name, mode_spec in raw_modes.items():
            modes[mode_name] = WaypointMode(max_minutes=mode_spec["max_minutes"])
        if not modes:
            # Default: walk-only with 30-minute threshold
            modes["walk"] = WaypointMode(max_minutes=30)
        waypoints.append(
            Waypoint(
                name=wp["name"],
                lat=wp["lat"],
                lng=wp["lng"],
                tier=tier,
                points=TIER_POINTS[tier],
                modes=modes,
            )
        )
    return waypoints


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return Config(
        filters=_parse_filters(raw.get("filters")),
        preferences=_parse_preferences(raw.get("preferences")),
        waypoints=_parse_waypoints(raw.get("waypoints")),
    )


def _range_filter_to_dict(rf: RangeFilter) -> dict | None:
    d: dict = {}
    if rf.min is not None:
        d["min"] = rf.min
    if rf.max is not None:
        d["max"] = rf.max
    return d or None


def save_config(config: Config, path: Path | None = None) -> None:
    """Write a Config back to preferences.yaml."""
    path = path or DEFAULT_CONFIG_PATH()
    raw: dict = {}

    # Filters
    filters_dict: dict = {}
    for name in ("rent", "bedrooms", "bathrooms", "sqft"):
        rd = _range_filter_to_dict(getattr(config.filters, name))
        if rd is not None:
            filters_dict[name] = rd
    if config.filters.city is not None:
        filters_dict["city"] = config.filters.city
    if config.filters.pets_allowed is not None:
        filters_dict["pets_allowed"] = config.filters.pets_allowed
    if filters_dict:
        raw["filters"] = filters_dict

    # Preferences
    if config.preferences:
        raw["preferences"] = {p.name: {"tier": p.tier} for p in config.preferences}

    # Waypoints
    if config.waypoints:
        wps = []
        for wp in config.waypoints:
            wp_dict: dict = {
                "name": wp.name,
                "lat": wp.lat,
                "lng": wp.lng,
                "tier": wp.tier,
                "modes": {
                    mode_name: {"max_minutes": mode.max_minutes}
                    for mode_name, mode in wp.modes.items()
                },
            }
            wps.append(wp_dict)
        raw["waypoints"] = wps

    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
