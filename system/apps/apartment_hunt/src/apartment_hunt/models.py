from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Listing(BaseModel):
    """Universal listing schema. All fields optional except source and address."""

    # Required identifiers
    source: str
    address: str

    # Optional unit/property details
    unit: str | None = None
    rent: float | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: float | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    lat: float | None = None
    lng: float | None = None

    # Availability
    available_date: date | None = None
    move_in_date: date | None = None
    lease_term_months: int | None = None

    # Pet policy
    pets_allowed: bool | None = None
    cats_allowed: bool | None = None
    dogs_allowed: bool | None = None
    pet_deposit: float | None = None
    pet_rent: float | None = None

    # Amenities
    parking: str | None = None
    parking_cost: float | None = None
    laundry: str | None = None  # "in-unit", "shared", "none"
    dishwasher: bool | None = None
    furnished: bool | None = None
    amenities: list[str] = []

    # Description and media
    description: str | None = None
    photos: list[str] = []
    listing_url: str | None = None
    detail_url: str | None = None
    virtual_tour_url: str | None = None

    # Contact
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None

    # Financials and lease
    rent_controlled: bool | None = None
    security_deposit: float | None = None
    promotions: str | None = None
    utilities_included: list[str] = []

    # Floor/plan
    floor: str | None = None
    floor_plan_name: str | None = None

    # Source tracking
    source_id: str | None = None
    sources: list[str] = []

    def populated_field_count(self) -> int:
        """Count of fields that have a non-None, non-default value."""
        count = 0
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            count += 1
        return count


class WaypointTravel(BaseModel):
    """Travel time info for a single waypoint, recording the best mode used."""

    mode: str  # "walk" or "transit"
    minutes: float


class ScoredListing(BaseModel):
    """A listing with its computed score and per-dimension breakdown."""

    listing: Listing
    score: float
    score_breakdown: dict[str, float]
    # Per-waypoint travel times for each declared mode (walk, transit, etc.).
    # The order of the list follows the mode order declared in config.
    waypoint_travel: dict[str, list[WaypointTravel]] = {}
