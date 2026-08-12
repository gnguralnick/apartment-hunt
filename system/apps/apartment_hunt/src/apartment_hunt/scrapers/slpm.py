"""Scraper for SLPM Property Management open houses.

SLPM uses a WordPress + Elementor + Jet Engine site. The open houses page
at https://slpm.com/open-houses/ renders listing cards with a custom
``property-card`` structure inside ``property-grid`` containers.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import httpx
from bs4 import BeautifulSoup, Tag

from apartment_hunt.geocode import geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

log = logging.getLogger(__name__)

_URL = "https://slpm.com/open-houses/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_DOLLAR_RE = re.compile(r"\$([\d,]+)")
_BEDS_RE = re.compile(r"(\d+)\s*bed", re.IGNORECASE)
_BATHS_RE = re.compile(r"([\d.]+)\s*bath", re.IGNORECASE)
_AVAIL_DATE_RE = re.compile(r"Available:\s*(.+)", re.IGNORECASE)
_MATTERPORT_RE = re.compile(r"https?://my\.matterport\.com/show/\?m=\w+")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_rent(text: str | None) -> float | None:
    """Extract numeric rent from a string like "$2,700/mo" -> 2700.0."""
    if not text:
        return None
    match = _DOLLAR_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_beds(text: str | None) -> int | None:
    """Extract bedroom count from a string like "2 bed"."""
    if not text:
        return None
    match = _BEDS_RE.search(text)
    return int(match.group(1)) if match else None


def _parse_baths(text: str | None) -> float | None:
    """Extract bathroom count from a string like "1 bath" or "1.5 bath"."""
    if not text:
        return None
    match = _BATHS_RE.search(text)
    return float(match.group(1)) if match else None


def _parse_available_date(text: str | None) -> date | None:
    """Parse an availability string like "NOW", "4/15", or "4/15/2026".

    Returns a ``date`` for date strings, ``date.today()`` for "NOW",
    or ``None`` if unparseable.
    """
    if not text:
        return None
    match = _AVAIL_DATE_RE.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    if raw.upper() == "NOW":
        return date.today()

    # Try M/D/YYYY first, then M/D (assume current year).
    parts = raw.split("/")
    try:
        month = int(parts[0])
        day = int(parts[1])
        year = int(parts[2]) if len(parts) >= 3 else date.today().year
        return date(year, month, day)
    except (ValueError, IndexError):
        log.warning("Could not parse available date: %r", raw)
        return None


def _parse_listing_card(card: Tag) -> Listing | None:
    """Parse a single ``.property-card`` (or its wrapping link) into a Listing.

    Returns ``None`` if we can't extract a usable address.
    """
    # Address
    addr_el = card.select_one(".property-address")
    if not addr_el:
        log.debug("Skipping card with no .property-address")
        return None
    address = addr_el.get_text(strip=True)
    # Strip leading material-icons text (e.g. "location_on")
    address = re.sub(r"^location_on\s*", "", address).strip()
    if not address:
        log.debug("Skipping card with empty address text")
        return None

    # Rent
    rent_el = card.select_one(".property-rent")
    rent = _parse_rent(rent_el.get_text(strip=True) if rent_el else None)

    # Beds / baths
    detail_els = card.select(".property-detail span")
    beds: int | None = None
    baths: float | None = None
    for el in detail_els:
        txt = el.get_text(strip=True)
        if beds is None:
            beds = _parse_beds(txt)
        if baths is None:
            baths = _parse_baths(txt)

    # City badge
    city_el = card.select_one(".city-badge")
    city_raw = city_el.get_text(strip=True) if city_el else None
    # Badge text may be e.g. "Oakland Apt" — take just the city name.
    city = city_raw.split()[0] if city_raw else None
    # Normalise common short names
    city_map = {"SF": "San Francisco"}
    if city:
        city = city_map.get(city, city)

    # Available date — look in the property-details area
    avail_date: date | None = None
    details_area = card.select_one(".property-details")
    if details_area:
        details_text = details_area.get_text()
        avail_date = _parse_available_date(details_text)

    # Photo
    photos: list[str] = []
    img_el = card.select_one(".property-image")
    if img_el:
        src = img_el.get("src") or img_el.get("data-src")
        if src:
            photos.append(str(src))

    # Virtual tour (Matterport)
    virtual_tour_url: str | None = None
    matterport_el = card.select_one(".matterport-button")
    if matterport_el:
        href = matterport_el.get("href")
        if href:
            virtual_tour_url = str(href)

    # Listing URL (the wrapping <a>)
    listing_url: str | None = None
    link_el = card.select_one("a.property-card-link") or card.find_parent(
        "a", class_="property-card-link"
    )
    if link_el:
        href = link_el.get("href")
        if href:
            listing_url = str(href)

    lat, lng = geocode(address, city, "CA")

    return Listing(
        source="slpm",
        address=address,
        lat=lat,
        lng=lng,
        rent=rent,
        bedrooms=beds,
        bathrooms=baths,
        available_date=avail_date,
        city=city,
        state="CA",
        photos=photos,
        virtual_tour_url=virtual_tour_url,
        listing_url=listing_url,
    )


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------


@register
class SlpmScraper:
    slug = "slpm"

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
            log.error("Failed to fetch SLPM open houses: %s", exc)
            return []

        return _parse_html(resp.text)


def _parse_html(html: str) -> list[Listing]:
    """Parse the full open-houses page HTML and return Listings."""
    soup = BeautifulSoup(html, "lxml")

    # Primary selector: each card is a .property-card inside the grid.
    cards = soup.select(".property-card")

    if not cards:
        log.warning("No .property-card elements found on SLPM page")
        return []

    listings: list[Listing] = []
    seen_addresses: set[str] = set()

    for card in cards:
        try:
            listing = _parse_listing_card(card)
        except Exception:
            log.warning("Failed to parse SLPM listing card", exc_info=True)
            continue

        if listing is None:
            continue

        # Deduplicate within the page (same listing may appear in multiple
        # day-sections with different open-house time slots).
        key = listing.address.lower()
        if key in seen_addresses:
            continue
        seen_addresses.add(key)

        listings.append(listing)

    log.info("Scraped %d listings from SLPM", len(listings))
    return listings
