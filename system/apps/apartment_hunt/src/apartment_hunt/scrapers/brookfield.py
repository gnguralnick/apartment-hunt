"""Scraper for Brookfield Properties SF listings.

Brookfield's location page embeds a ``let properties = [...]`` JavaScript
array containing every property nationwide (sourced from RentCafe).  We
extract that array, parse it as JSON, and filter to San Francisco.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

log = logging.getLogger(__name__)

_URL = "https://rent.brookfieldproperties.com/location/san-francisco/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_PROPERTIES_RE = re.compile(r"let\s+properties\s*=\s*(\[.*?\])\s*;", re.DOTALL)


def _parse_lat_lng(raw: str | None) -> tuple[float | None, float | None]:
    """Parse a 'lat,lng' string into a (lat, lng) tuple."""
    if not raw:
        return None, None
    parts = raw.split(",", 1)
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except (ValueError, TypeError):
        return None, None


def _safe_int(value: str | int | None) -> int | None:
    """Convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value: str | float | int | None) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_site_url(raw: str | None) -> str | None:
    """Ensure the site URL has a scheme."""
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _parse_amenities(raw: str | None) -> list[str]:
    """Split comma-separated amenities string into a list."""
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def _property_to_listing(prop: dict) -> Listing | None:
    """Convert a single property dict from the JS array into a Listing.

    Returns None if the property lacks the minimum required data (address).
    """
    address = prop.get("address", "").strip()
    if not address:
        log.warning("Brookfield property missing address, skipping: %s", prop.get("propertyName"))
        return None

    lat, lng = _parse_lat_lng(prop.get("lat_long"))

    min_rent = _safe_float(prop.get("minrent"))
    min_beds = _safe_int(prop.get("minbed"))
    min_sqft = _safe_float(prop.get("minAreaSQFT"))
    max_sqft = _safe_float(prop.get("maxAreaSQFT"))

    image_url = prop.get("imageURL", "")
    photos: list[str] = []
    if image_url and "NoImage" not in image_url:
        photos = [image_url]

    has_specials = prop.get("hasSpecials")
    promotions: str | None = None
    if has_specials and str(has_specials) not in ("0", "false", "False", ""):
        promotions = "Special offer available"

    property_id = prop.get("propertyId")

    return Listing(
        source="brookfield",
        source_id=str(property_id) if property_id is not None else None,
        address=address,
        city=prop.get("city") or None,
        state=prop.get("state") or None,
        zip=prop.get("zipCode") or None,
        lat=lat,
        lng=lng,
        rent=min_rent,
        bedrooms=min_beds,
        bathrooms=_safe_float(prop.get("minbath")),
        sqft=max_sqft if max_sqft is not None else min_sqft,
        amenities=_parse_amenities(prop.get("amenities")),
        photos=photos,
        listing_url=_normalize_site_url(prop.get("siteUrl")),
        contact_phone=prop.get("phone") or None,
        promotions=promotions,
    )


def extract_properties_json(html: str) -> list[dict] | None:
    """Extract and parse the properties JS array from the page HTML.

    Returns the parsed list of dicts, or None if extraction/parsing fails.
    """
    match = _PROPERTIES_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def filter_sf_properties(properties: list[dict]) -> list[dict]:
    """Return only properties whose city matches San Francisco (case-insensitive)."""
    return [
        p for p in properties
        if (p.get("city") or "").strip().lower() == "san francisco"
    ]


@register
class BrookfieldScraper:
    slug = "brookfield"

    def fetch(self) -> list[Listing]:
        try:
            resp = httpx.get(
                _URL,
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("Failed to fetch Brookfield page: %s", exc)
            return []

        all_properties = extract_properties_json(resp.text)
        if all_properties is None:
            log.warning("Could not extract properties JSON from Brookfield page")
            return []

        sf_properties = filter_sf_properties(all_properties)
        log.info(
            "Brookfield: found %d SF properties out of %d total",
            len(sf_properties),
            len(all_properties),
        )

        listings: list[Listing] = []
        for prop in sf_properties:
            try:
                listing = _property_to_listing(prop)
            except Exception:
                log.warning(
                    "Failed to parse Brookfield property: %s",
                    prop.get("propertyName"),
                    exc_info=True,
                )
                continue
            if listing is not None:
                listings.append(listing)

        log.info("Scraped %d listings from Brookfield", len(listings))
        return listings
