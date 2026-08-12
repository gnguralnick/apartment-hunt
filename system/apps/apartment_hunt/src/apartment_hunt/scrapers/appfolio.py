"""Scrapers for all AppFolio-based landlord sites.

All AppFolio sites share the same listing page structure: a <script> tag
containing a ``markers:`` JavaScript array with JSON listing data.  The
shared helper ``_fetch_appfolio`` handles the GET + parse logic; each
individual scraper class is a thin wrapper that supplies the slug and URL.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_MARKERS_RE = re.compile(r"markers:\s*(\[.*?\])", re.DOTALL)
_DOLLAR_RE = re.compile(r"\$([\d,]+)")
_BEDS_RE = re.compile(r"(\d+)\s*bd", re.IGNORECASE)
_BATHS_RE = re.compile(r"([\d.]+)\s*ba", re.IGNORECASE)
_SQFT_RE = re.compile(r"([\d,]+)\s*sq\.?\s*ft", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_rent(rent_range: str | None) -> float | None:
    """Extract numeric rent from a string like "$3,295" or "$2,500 - $3,000".

    Returns the lower value if a range is present.
    """
    if not rent_range:
        return None
    matches = _DOLLAR_RE.findall(rent_range)
    if not matches:
        return None
    try:
        return float(matches[0].replace(",", ""))
    except ValueError:
        return None


def _parse_unit_specs(
    unit_specs: str | None,
) -> tuple[int | None, float | None, float | None]:
    """Parse bedrooms, bathrooms, sqft from e.g. "1 bd, 1 ba, 800 Sq. Ft."."""
    if not unit_specs:
        return None, None, None

    beds_match = _BEDS_RE.search(unit_specs)
    baths_match = _BATHS_RE.search(unit_specs)
    sqft_match = _SQFT_RE.search(unit_specs)

    beds = int(beds_match.group(1)) if beds_match else None
    baths = float(baths_match.group(1)) if baths_match else None
    sqft = float(sqft_match.group(1).replace(",", "")) if sqft_match else None
    return beds, baths, sqft


def _parse_address(
    address: str | None,
) -> tuple[str, str | None, str | None, str | None]:
    """Split "123 Main St, San Francisco, CA 94102" into components.

    Returns (street, city, state, zip).  Falls back to the raw string as
    the street address if the expected format isn't found.
    """
    if not address:
        return "", None, None, None

    parts = [p.strip() for p in address.split(",")]

    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        state_zip = parts[2].strip().split()
        state = state_zip[0] if len(state_zip) >= 1 else None
        zip_code = state_zip[1] if len(state_zip) >= 2 else None
        return street, city, state, zip_code

    # Fallback: not enough commas — return the whole thing as street.
    return address.strip(), None, None, None


# ---------------------------------------------------------------------------
# Shared fetch logic
# ---------------------------------------------------------------------------

def _fetch_appfolio(url: str, slug: str) -> list[Listing]:
    """GET an AppFolio listings page, extract markers JSON, return Listings."""
    try:
        resp = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("Failed to fetch %s (%s): %s", slug, url, exc)
        return []

    html = resp.text
    markers_match = _MARKERS_RE.search(html)
    if not markers_match:
        log.warning("No markers JSON found on %s (%s)", slug, url)
        return []

    try:
        markers = json.loads(markers_match.group(1))
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse markers JSON for %s: %s", slug, exc)
        return []

    base_url = url.rsplit("/listings", 1)[0]  # e.g. "https://X.appfolio.com"
    listings: list[Listing] = []

    for marker in markers:
        try:
            listing = _marker_to_listing(marker, slug=slug, base_url=base_url, listing_url=url)
        except Exception:
            log.warning("Failed to parse marker for %s: %s", slug, marker, exc_info=True)
            continue
        if listing is not None:
            listings.append(listing)

    log.info("Scraped %d listings from %s", len(listings), slug)
    return listings


def _marker_to_listing(
    marker: dict,
    *,
    slug: str,
    base_url: str,
    listing_url: str,
) -> Listing | None:
    """Convert a single AppFolio marker dict to a Listing (or None to skip)."""
    rent = _parse_rent(marker.get("rent_range"))
    beds, baths, sqft = _parse_unit_specs(marker.get("unit_specs"))

    # Filter out parking-only listings (no bedrooms and no rent).
    if beds is None and rent is None:
        return None

    raw_address = marker.get("address", "")
    street, city, state, zip_code = _parse_address(raw_address)

    detail_page = marker.get("detail_page_url")
    detail_url = f"{base_url}{detail_page}" if detail_page else None

    photo_url = marker.get("default_photo_url")
    photos = [photo_url] if photo_url else []

    listing_id = marker.get("listing_id")

    return Listing(
        source=slug,
        address=street,
        city=city,
        state=state,
        zip=zip_code,
        rent=rent,
        bedrooms=beds,
        bathrooms=baths,
        sqft=sqft,
        lat=marker.get("latitude"),
        lng=marker.get("longitude"),
        photos=photos,
        listing_url=listing_url,
        detail_url=detail_url,
        source_id=str(listing_id) if listing_id is not None else None,
    )


# ---------------------------------------------------------------------------
# Individual scraper classes
# ---------------------------------------------------------------------------

@register
class EminvestScraper:
    slug = "eminvest"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://eminvest.appfolio.com/listings/listings", self.slug)


@register
class AmsiresScraper:
    slug = "amsires"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://amsires.appfolio.com/listings/listings", self.slug)


@register
class WcpmScraper:
    slug = "wcpm"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://wcpm.appfolio.com/listings", self.slug)


@register
class ProgressiveSfScraper:
    slug = "progressivesf"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio(
            "https://progressivesf.appfolio.com/listings/listings", self.slug
        )


@register
class LuminorScraper:
    slug = "luminor"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://luminor.appfolio.com/listings", self.slug)


@register
class GgPropertiesScraper:
    slug = "gg-properties"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio(
            "https://ggproperties.appfolio.com/listings/listings", self.slug
        )


@register
class ApgScraper:
    slug = "apg"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://apg.appfolio.com/listings/listings", self.slug)


@register
class SutroScraper:
    slug = "sutro"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://sutro.appfolio.com/listings/listings", self.slug)


@register
class ChandlerScraper:
    slug = "chandler"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio(
            "https://chandlerproperties.appfolio.com/listings/listings", self.slug
        )


@register
class StructurePropertiesScraper:
    # Structure Properties migrated its listings to AppFolio (spsf.appfolio.com);
    # the old WordPress/JSON-LD page no longer carries them.
    slug = "structureproperties"

    def fetch(self) -> list[Listing]:
        return _fetch_appfolio("https://spsf.appfolio.com/listings/listings", self.slug)
