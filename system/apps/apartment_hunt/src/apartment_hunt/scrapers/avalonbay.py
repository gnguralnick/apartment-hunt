"""Scraper for AvalonBay Communities SF listings.

Fetches the search page and extracts Fusion.globalContent JSON containing
all community and unit data from a single GET request.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import httpx

from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

log = logging.getLogger(__name__)

_URL = "https://www.avaloncommunities.com/california/san-francisco-apartments"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_FUSION_RE = re.compile(r"Fusion\.globalContent\s*=\s*(\{.*?\});", re.DOTALL)


def _get(obj: dict, *keys: str, default: object = None) -> object:
    """Safely traverse nested dicts, returning *default* on any miss."""
    current: object = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)  # type: ignore[union-attr]
        if current is None:
            return default
    return current


def _parse_date(raw: str | None) -> date | None:
    """Parse an ISO-8601 datetime string into a date, or None."""
    if not raw:
        return None
    try:
        # Handles "2026-05-10T04:00:00+00:00" — just take the date portion.
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        log.warning("Could not parse date: %r", raw)
        return None


def _extract_rent(unit: dict) -> tuple[float | None, int | None]:
    """Return (rent, lease_term_months) from a unit's pricing data.

    Prefers unfurnished pricing.  Falls back to furnished if the unit has
    no unfurnished price.
    """
    for key in ("startingAtPricesUnfurnished", "startingAtPricesFurnished"):
        pricing = unit.get(key)
        if not isinstance(pricing, dict):
            continue
        prices = pricing.get("prices")
        if not isinstance(prices, dict):
            continue
        # Prefer netEffectivePrice, then price, then totalPrice.
        rent = prices.get("netEffectivePrice") or prices.get("price") or prices.get("totalPrice")
        if rent is not None:
            try:
                rent = float(rent)
            except (TypeError, ValueError):
                continue
            lease_term = pricing.get("leaseTerm")
            if lease_term is not None:
                try:
                    lease_term = int(lease_term)
                except (TypeError, ValueError):
                    lease_term = None
            return rent, lease_term
    return None, None


def _extract_move_in_date(unit: dict) -> date | None:
    """Extract the move-in date from pricing data."""
    for key in ("startingAtPricesUnfurnished", "startingAtPricesFurnished"):
        pricing = unit.get(key)
        if isinstance(pricing, dict):
            d = _parse_date(pricing.get("moveInDate"))
            if d is not None:
                return d
    return None


def _build_community_index(data: dict) -> dict[str, dict]:
    """Build a communityId -> community dict from the top-level JSON."""
    communities: list = _get(data, "communityResults", "communities", "items", default=[])  # type: ignore[assignment]
    if not isinstance(communities, list):
        log.warning("communityResults.communities.items is not a list")
        return {}
    index: dict[str, dict] = {}
    for comm in communities:
        if not isinstance(comm, dict):
            continue
        cid = comm.get("communityId")
        if cid:
            index[str(cid)] = comm
    return index


def _unit_to_listing(unit: dict, community: dict | None) -> Listing | None:
    """Convert a single unit dict (plus its community) into a Listing."""
    # --- address from community, with unit-level fallback ---
    addr = community.get("address", {}) if community else {}
    if not isinstance(addr, dict):
        addr = {}
    street = addr.get("addressLine1", "")

    # If no community address, try the unit's own address.
    if not street:
        unit_addr = unit.get("address", {})
        if isinstance(unit_addr, dict):
            street = unit_addr.get("addressLine1", "")

    if not street:
        log.warning("Unit %s has no street address, skipping", unit.get("unitId"))
        return None

    city = addr.get("city")
    state = addr.get("state")
    zip_code = addr.get("zip")

    # Coordinates from community.
    coords = (community or {}).get("coordinates", {})
    if not isinstance(coords, dict):
        coords = {}
    lat = coords.get("latitude")
    lng = coords.get("longitude")

    # --- unit fields ---
    unit_name = unit.get("unitName")
    unit_id = unit.get("unitId")
    floor_number = unit.get("floorNumber")

    floor_plan = unit.get("floorPlan")
    floor_plan_name = floor_plan.get("name") if isinstance(floor_plan, dict) else None

    bedrooms: int | None = None
    raw_beds = unit.get("bedroomNumber")
    if raw_beds is not None:
        try:
            bedrooms = int(raw_beds)
        except (TypeError, ValueError):
            pass

    bathrooms: float | None = None
    raw_baths = unit.get("bathroomNumber")
    if raw_baths is not None:
        try:
            bathrooms = float(raw_baths)
        except (TypeError, ValueError):
            pass

    sqft: float | None = None
    raw_sqft = unit.get("squareFeet")
    if raw_sqft is not None:
        try:
            sqft = float(raw_sqft)
        except (TypeError, ValueError):
            pass

    furnished: bool | None = None
    furnish_status = unit.get("furnishStatus")
    if furnish_status is not None:
        # "OnDemand" means optionally furnished; "Unfurnished"/"Furnished" are explicit.
        if isinstance(furnish_status, str):
            lower = furnish_status.lower()
            if lower == "furnished":
                furnished = True
            elif lower == "unfurnished":
                furnished = False
            # "OnDemand" → leave as None (optionally available).

    # Virtual tour.
    virtual_tour = unit.get("virtualTour")
    virtual_tour_url: str | None = None
    if isinstance(virtual_tour, dict):
        virtual_tour_url = virtual_tour.get("space") or None

    # Dates.
    available_date = _parse_date(unit.get("availableDateUnfurnished")) or _parse_date(
        unit.get("availableDateFurnished")
    )
    move_in_date = _extract_move_in_date(unit)

    # Pricing.
    rent, lease_term = _extract_rent(unit)

    # Promotions — combine community and unit promotions.
    promo_parts: list[str] = []
    for source in (unit, community or {}):
        promos = source.get("promotions")
        if isinstance(promos, list):
            for p in promos:
                if isinstance(p, str) and p:
                    promo_parts.append(p)
                elif isinstance(p, dict):
                    text = p.get("text") or p.get("description") or p.get("title") or ""
                    if text:
                        promo_parts.append(str(text))
    promotions = "; ".join(promo_parts) if promo_parts else None

    # Community-level detail URL.
    community_url_path = (community or {}).get("url")
    detail_url: str | None = None
    if community_url_path and isinstance(community_url_path, str):
        detail_url = f"https://www.avaloncommunities.com{community_url_path}"

    return Listing(
        source="avalonbay",
        source_id=str(unit_id) if unit_id else None,
        address=street,
        unit=str(unit_name) if unit_name else None,
        city=city,
        state=state,
        zip=zip_code,
        lat=lat,
        lng=lng,
        rent=rent,
        lease_term_months=lease_term,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        sqft=sqft,
        floor=str(floor_number) if floor_number else None,
        floor_plan_name=floor_plan_name,
        furnished=furnished,
        virtual_tour_url=virtual_tour_url,
        available_date=available_date,
        move_in_date=move_in_date,
        promotions=promotions,
        listing_url=_URL,
        detail_url=detail_url,
    )


def _fetch_avalonbay() -> list[Listing]:
    """Fetch and parse AvalonBay SF listings."""
    try:
        resp = httpx.get(
            _URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("Failed to fetch AvalonBay page: %s", exc)
        return []

    html = resp.text
    match = _FUSION_RE.search(html)
    if not match:
        log.warning("Fusion.globalContent not found in AvalonBay page")
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse Fusion.globalContent JSON: %s", exc)
        return []

    if not isinstance(data, dict):
        log.warning("Fusion.globalContent is not a dict")
        return []

    community_index = _build_community_index(data)

    units: list = _get(data, "unitResults", "items", default=[])  # type: ignore[assignment]
    if not isinstance(units, list):
        log.warning("unitResults.items is not a list")
        return []

    listings: list[Listing] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        community_id = unit.get("communityId") or unit.get("propertyId")
        community = community_index.get(str(community_id)) if community_id else None
        try:
            listing = _unit_to_listing(unit, community)
        except Exception:
            log.warning(
                "Failed to parse AvalonBay unit %s", unit.get("unitId"), exc_info=True
            )
            continue
        if listing is not None:
            listings.append(listing)

    log.info("Scraped %d listings from avalonbay", len(listings))
    return listings


@register
class AvalonBayScraper:
    slug = "avalonbay"

    def fetch(self) -> list[Listing]:
        return _fetch_avalonbay()
