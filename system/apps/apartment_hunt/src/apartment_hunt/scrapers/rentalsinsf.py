"""Scraper for rentalsinsf.com — WordPress + Easy Property Listings (EPL) plugin."""

from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag

from apartment_hunt.geocode import geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

logger = logging.getLogger(__name__)

URL = "https://www.rentalsinsf.com/listings/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Maps the rental-type-* CSS class suffix to a bedroom count.
_BEDROOM_MAP: dict[str, int] = {
    "studio": 0,
    "one-bedroom": 1,
    "two-bedroom": 2,
    "three-bedroom": 3,
    "four-bedroom": 4,
}


def _parse_bedrooms(class_str: str) -> int | None:
    """Extract bedroom count from the rental-type-* CSS class."""
    match = re.search(r"rental-type-([\w-]+)", class_str)
    if not match:
        return None
    return _BEDROOM_MAP.get(match.group(1))


def _parse_price(price_text: str) -> float | None:
    """Extract numeric rent from a price string like '$2,200'."""
    digits = re.sub(r"[^\d.]", "", price_text)
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _parse_neighborhood(card: Tag) -> str | None:
    """Extract neighborhood from the .neighborhood element or the location-* CSS class."""
    el = card.select_one(".neighborhood p")
    if el:
        text = el.get_text(strip=True)
        if text:
            return text

    # Fall back to the location-* CSS class on the card div.
    class_str = " ".join(card.get("class", []))
    match = re.search(r"location-([\w-]+)", class_str)
    if match:
        return match.group(1).replace("-", " ").title()
    return None


def _parse_photo_url(card: Tag) -> str | None:
    """Extract the real image URL, preferring data-src (lazy-loaded) over src."""
    img = card.select_one("img")
    if not img:
        return None
    url = img.get("data-src") or img.get("src", "")
    if not url or url.startswith("data:"):
        return None
    return url


def _parse_card(card: Tag) -> Listing | None:
    """Parse a single EPL listing card into a Listing, or None on failure."""
    class_str = " ".join(card.get("class", []))

    # Address (required field)
    title_el = card.select_one("h3.entry-title a")
    if not title_el:
        logger.warning("Skipping card with no title element: id=%s", card.get("id"))
        return None
    address = title_el.get_text(strip=True)
    if not address:
        logger.warning("Skipping card with empty address: id=%s", card.get("id"))
        return None

    # Detail URL
    detail_url = title_el.get("href")

    # Price
    price_el = card.select_one(".page-price")
    rent = _parse_price(price_el.get_text(strip=True)) if price_el else None

    # Bedrooms
    bedrooms = _parse_bedrooms(class_str)

    # Neighborhood
    neighborhood = _parse_neighborhood(card)

    # Pet policy — presence of tax_feature-pet-friendly in the card's classes
    pet_friendly = "tax_feature-pet-friendly" in class_str
    pets_allowed: bool | None = True if pet_friendly else None
    cats_allowed: bool | None = True if pet_friendly else None
    dogs_allowed: bool | None = True if pet_friendly else None

    # Photo
    photo_url = _parse_photo_url(card)
    photos = [photo_url] if photo_url else []

    lat, lng = geocode(address, "San Francisco", "CA")

    return Listing(
        source="rentalsinsf",
        address=address,
        lat=lat,
        lng=lng,
        rent=rent,
        bedrooms=bedrooms,
        neighborhood=neighborhood,
        city="San Francisco",
        state="CA",
        pets_allowed=pets_allowed,
        cats_allowed=cats_allowed,
        dogs_allowed=dogs_allowed,
        photos=photos,
        listing_url=detail_url,
    )


@register
class RentalsInSFScraper:
    slug = "rentalsinsf"

    def fetch(self) -> list[Listing]:
        resp = httpx.get(URL, headers=_HEADERS, follow_redirects=True, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".epl-listing-post")
        logger.info("Found %d listing cards on %s", len(cards), URL)

        listings: list[Listing] = []
        for card in cards:
            try:
                listing = _parse_card(card)
            except Exception:
                logger.warning("Failed to parse listing card", exc_info=True)
                continue
            if listing is not None:
                listings.append(listing)

        logger.info("Parsed %d listings from %s", len(listings), URL)
        return listings
