"""Scraper for MMGProp (Meridian Management Group) vacancy listings."""

from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag

from apartment_hunt.geocode import geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

logger = logging.getLogger(__name__)

VACANCIES_URL = "https://mmgprop.com/vacancies/"


def _parse_rent(text: str) -> float | None:
    """Extract rent from a string like '$2,150' -> 2150.0."""
    match = re.search(r"\$\s*([\d,]+)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_location(text: str) -> dict[str, str | None]:
    """Parse a WPL location string like '1229 Pacific Avenue, Santa Rosa, California, United States 95404'.

    Returns a dict with keys: address, city, state, zip.
    """
    result: dict[str, str | None] = {"address": None, "city": None, "state": None, "zip": None}

    # Split on commas to get the parts
    parts = [p.strip() for p in text.split(",")]
    if not parts:
        return result

    # First part is the street address
    result["address"] = parts[0] or None

    # Second part is the city (if present)
    if len(parts) >= 2:
        result["city"] = parts[1]

    # Third part is the state (if present)
    if len(parts) >= 3:
        result["state"] = parts[2]

    # Fourth part is typically "United States XXXXX" — extract zip from it
    if len(parts) >= 4:
        zip_match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", parts[3])
        if zip_match:
            result["zip"] = zip_match.group(1)

    return result


def _parse_listing_card(card: Tag) -> Listing | None:
    """Parse a single .wpl_prp_cont element into a Listing, or None on failure."""
    try:
        # --- Address ---
        # Primary: use the full location string from .wpl_prp_listing_location
        # Fallback: use the short title from .wpl_prp_title
        location_el = card.select_one(".wpl_prp_listing_location")
        title_el = card.select_one(".wpl_prp_title")

        address: str | None = None
        city: str | None = None
        state: str | None = None
        zip_code: str | None = None

        if location_el:
            location_text = location_el.get_text(strip=True)
            parsed = _parse_location(location_text)
            address = parsed["address"]
            city = parsed["city"]
            state = parsed["state"]
            zip_code = parsed["zip"]

        # If location parsing didn't produce an address, fall back to the title
        if not address and title_el:
            address = title_el.get_text(strip=True)

        if not address:
            logger.warning("Listing card missing address, skipping")
            return None

        # --- Rent ---
        rent: float | None = None
        price_el = card.select_one(".price_box span")
        if price_el:
            rent = _parse_rent(price_el.get_text(strip=True))

        # --- Bedrooms ---
        bedrooms: int | None = None
        bed_el = card.select_one(".bedroom .value")
        if bed_el:
            try:
                bedrooms = int(bed_el.get_text(strip=True))
            except ValueError:
                logger.warning("Could not parse bedroom count: %r", bed_el.get_text(strip=True))

        # --- Bathrooms ---
        bathrooms: float | None = None
        bath_el = card.select_one(".bathroom .value")
        if bath_el:
            try:
                bathrooms = float(bath_el.get_text(strip=True))
            except ValueError:
                logger.warning("Could not parse bathroom count: %r", bath_el.get_text(strip=True))

        # --- Description ---
        description: str | None = None
        desc_el = card.select_one(".wpl_prp_desc")
        if desc_el:
            raw = desc_el.get_text(strip=True)
            if raw:
                description = raw

        # --- Detail URL ---
        listing_url: str | None = None
        detail_link = card.select_one("a.view_detail")
        if detail_link and detail_link.get("href"):
            listing_url = detail_link["href"]

        # --- Photos ---
        photos: list[str] = []
        for img in card.select(".wpl_gallery_image"):
            src = img.get("src")
            if src:
                photos.append(src)

        lat, lng = geocode(address, city, state)

        return Listing(
            source="mmgprop",
            address=address,
            city=city,
            state=state,
            zip=zip_code,
            lat=lat,
            lng=lng,
            rent=rent,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            description=description,
            listing_url=listing_url,
            photos=photos,
        )
    except Exception:
        logger.warning("Failed to parse listing card", exc_info=True)
        return None


@register
class MMGPropScraper:
    slug = "mmgprop"

    def fetch(self) -> list[Listing]:
        listings: list[Listing] = []

        logger.info("Fetching MMGProp vacancies from %s", VACANCIES_URL)
        try:
            response = httpx.get(VACANCIES_URL, timeout=30, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("HTTP error fetching MMGProp vacancies", exc_info=True)
            return []

        soup = BeautifulSoup(response.text, "lxml")
        cards = soup.select(".wpl_prp_cont")

        if not cards:
            logger.warning("No .wpl_prp_cont containers found on page")
            return []

        for card in cards:
            listing = _parse_listing_card(card)
            if listing:
                listings.append(listing)

        logger.info("Scraped %d listings from MMGProp", len(listings))
        return listings
