from __future__ import annotations

from statistics import median

from apartment_hunt.config import Config, Preference, Waypoint
from apartment_hunt.models import Listing, ScoredListing, WaypointTravel
from apartment_hunt.travel import HaversineTravelTimeCalculator, TravelTimeCalculator

# Walking is preferred: transit scores are discounted by this factor.
TRANSIT_DISCOUNT = 0.5

# --- Preference scoring functions ---

BOOLEAN_PREFERENCE_FIELDS: dict[str, str] = {
    "dishwasher": "dishwasher",
    "in_unit_laundry": "laundry",
    "rent_controlled": "rent_controlled",
    "furnished": "furnished",
    "parking": "parking",
}

CONTINUOUS_PREFERENCE_FIELDS: set[str] = {"sqft", "rent"}

# Maps config mode names to Google Maps API mode parameter values.
MODE_API_NAMES: dict[str, str] = {
    "walk": "walking",
    "transit": "transit",
}


def _score_boolean(listing: Listing, pref: Preference) -> float | None:
    """Score a boolean preference. Returns None if the listing lacks the field."""
    field_name = BOOLEAN_PREFERENCE_FIELDS.get(pref.name)
    if field_name is None:
        return None

    value = getattr(listing, field_name, None)
    if value is None:
        return None

    # Special case: in_unit_laundry checks the laundry string field
    if pref.name == "in_unit_laundry":
        return float(pref.points) if value == "in-unit" else 0.0

    # Special case: parking — any non-"none" string value counts as having parking
    if pref.name == "parking":
        return float(pref.points) if value not in ("none", "") else 0.0

    # Standard boolean
    return float(pref.points) if value else 0.0


def _compute_continuous_scores(
    listings: list[Listing],
    pref: Preference,
) -> dict[int, float | None]:
    """Compute linearly-scaled scores for a continuous field across all listings.

    Returns a dict mapping listing index -> score (None if listing lacks the field).
    """
    field_name = pref.name
    values: list[tuple[int, float]] = []
    for i, listing in enumerate(listings):
        val = getattr(listing, field_name, None)
        if val is not None:
            values.append((i, float(val)))

    if not values:
        return {i: None for i in range(len(listings))}

    raw_values = [v for _, v in values]
    lo, hi = min(raw_values), max(raw_values)

    scores: dict[int, float | None] = {}
    for i in range(len(listings)):
        val = getattr(listings[i], field_name, None)
        if val is None:
            scores[i] = None
        elif hi == lo:
            # All values are the same — give full points
            scores[i] = float(pref.points)
        else:
            # For rent, lower is better (invert scale)
            if field_name == "rent":
                normalized = (hi - float(val)) / (hi - lo)
            else:
                normalized = (float(val) - lo) / (hi - lo)
            scores[i] = normalized * pref.points
    return scores


def _compute_waypoint_scores(
    listings: list[Listing],
    waypoint: Waypoint,
    calculator: TravelTimeCalculator,
) -> tuple[dict[int, float | None], dict[int, list[WaypointTravel]]]:
    """Compute travel-time-based scores for a waypoint across all listings.

    Walking is preferred over transit: transit scores are discounted by TRANSIT_DISCOUNT.
    The best score across all declared modes is used for scoring, but travel
    times for every declared mode are recorded so the UI can display them.

    Returns:
        (scores, travel_details) where:
        - scores maps listing index -> score (None if listing lacks lat/lng)
        - travel_details maps listing index -> list of WaypointTravel, one per
          declared mode with a known travel time (in config declaration order).
    """
    # Identify listings with valid coordinates
    valid: list[tuple[int, tuple[float, float]]] = []
    for i, listing in enumerate(listings):
        if listing.lat is not None and listing.lng is not None:
            valid.append((i, (listing.lat, listing.lng)))

    destination = (waypoint.lat, waypoint.lng)

    # For each declared mode, compute travel times and scores
    mode_scores: dict[str, dict[int, float]] = {}
    mode_times: dict[str, dict[int, float]] = {}

    if valid:
        origins = [coord for _, coord in valid]
        valid_indices = [idx for idx, _ in valid]

        for mode_name, mode_config in waypoint.modes.items():
            api_mode = MODE_API_NAMES.get(mode_name, mode_name)
            times = calculator.get_travel_times(origins, destination, api_mode)

            max_points = waypoint.points
            if mode_name != "walk":
                max_points *= TRANSIT_DISCOUNT

            scores: dict[int, float] = {}
            minutes_map: dict[int, float] = {}
            for j, idx in enumerate(valid_indices):
                t = times[j]
                if t is None or t >= mode_config.max_minutes:
                    scores[idx] = 0.0
                else:
                    scores[idx] = (1.0 - t / mode_config.max_minutes) * max_points
                if t is not None:
                    minutes_map[idx] = t

            mode_scores[mode_name] = scores
            mode_times[mode_name] = minutes_map

    # Take best score across modes for each listing; record all modes for UI.
    final_scores: dict[int, float | None] = {}
    final_details: dict[int, list[WaypointTravel]] = {}

    for i in range(len(listings)):
        if listings[i].lat is None or listings[i].lng is None:
            final_scores[i] = None
            final_details[i] = []
            continue

        best_score = 0.0
        for mode_name in waypoint.modes:
            score = mode_scores.get(mode_name, {}).get(i, 0.0)
            if score > best_score:
                best_score = score
        final_scores[i] = best_score

        # Record travel details for every declared mode that has a known time,
        # preserving the order declared in config.
        details_list: list[WaypointTravel] = []
        for mode_name in waypoint.modes:
            t = mode_times.get(mode_name, {}).get(i)
            if t is not None:
                details_list.append(WaypointTravel(mode=mode_name, minutes=t))
        final_details[i] = details_list

    return final_scores, final_details


def _impute_medians(
    per_dimension: dict[str, dict[int, float | None]],
    n_listings: int,
) -> dict[str, dict[int, float]]:
    """Replace None values with the median of non-None values for that dimension."""
    result: dict[str, dict[int, float]] = {}
    for dim_name, scores in per_dimension.items():
        known = [v for v in scores.values() if v is not None]
        med = median(known) if known else 0.0
        result[dim_name] = {i: (v if v is not None else med) for i, v in scores.items()}
    return result


def score_listings(
    listings: list[Listing],
    config: Config,
    travel_time_calculator: TravelTimeCalculator | None = None,
) -> list[ScoredListing]:
    """Score and sort listings based on preferences and waypoints.

    Returns listings sorted by score descending.
    """
    if not listings:
        return []

    if travel_time_calculator is None:
        travel_time_calculator = HaversineTravelTimeCalculator()

    # Collect raw per-dimension scores (with None for missing data)
    per_dimension: dict[str, dict[int, float | None]] = {}

    for pref in config.preferences:
        if pref.name in CONTINUOUS_PREFERENCE_FIELDS:
            per_dimension[pref.name] = _compute_continuous_scores(listings, pref)
        elif pref.name in BOOLEAN_PREFERENCE_FIELDS:
            per_dimension[pref.name] = {
                i: _score_boolean(listing, pref) for i, listing in enumerate(listings)
            }

    # Waypoint scoring with travel times
    all_waypoint_details: dict[str, dict[int, list[WaypointTravel]]] = {}
    for waypoint in config.waypoints:
        dim_name = f"waypoint:{waypoint.name}"
        scores, details = _compute_waypoint_scores(
            listings, waypoint, travel_time_calculator
        )
        per_dimension[dim_name] = scores
        all_waypoint_details[waypoint.name] = details

    # Impute medians for missing data
    imputed = _impute_medians(per_dimension, len(listings))

    # Aggregate scores
    scored = []
    for i, listing in enumerate(listings):
        breakdown = {dim: imputed[dim][i] for dim in imputed}
        total = sum(breakdown.values())

        waypoint_travel: dict[str, list[WaypointTravel]] = {}
        for wp_name, details in all_waypoint_details.items():
            details_list = details.get(i, [])
            if details_list:
                waypoint_travel[wp_name] = details_list

        scored.append(
            ScoredListing(
                listing=listing,
                score=total,
                score_breakdown=breakdown,
                waypoint_travel=waypoint_travel,
            )
        )

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
