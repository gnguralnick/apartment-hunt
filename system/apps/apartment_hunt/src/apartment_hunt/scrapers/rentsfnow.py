"""Scraper for RentSFNow (Veritas Investments) listings."""

from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from apartment_hunt.geocode import geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.registry import register

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rentsfnow.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"


def _parse_beds_baths_price(text: str) -> tuple[int | None, float | None, float | None]:
    """Parse a string like '1 Bed \\ 1 Bath \\ $2,395' into (beds, baths, rent)."""
    beds: int | None = None
    baths: float | None = None
    rent: float | None = None

    parts = re.split(r"[\\|/]", text)
    for part in parts:
        part = part.strip()
        bed_match = re.search(r"(\d+)\s*bed", part, re.IGNORECASE)
        if bed_match:
            beds = int(bed_match.group(1))
            continue
        bath_match = re.search(r"([\d.]+)\s*bath", part, re.IGNORECASE)
        if bath_match:
            baths = float(bath_match.group(1))
            continue
        price_match = re.search(r"\$\s*([\d,]+)", part)
        if price_match:
            rent = float(price_match.group(1).replace(",", ""))
    return beds, baths, rent


def _parse_image_url(style: str) -> str | None:
    """Extract URL from a CSS background-image property value."""
    match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
    return match.group(1) if match else None


def _parse_listing_card(card: BeautifulSoup) -> Listing | None:
    """Parse a single listing card element into a Listing, or None on failure."""
    try:
        # Address from h2
        h2 = card.find("h2")
        if not h2:
            logger.warning("Listing card missing h2 (address), skipping")
            return None
        address = h2.get_text(strip=True)

        # Neighborhood from h3
        neighborhood: str | None = None
        h3 = card.find("h3")
        if h3:
            neighborhood = h3.get_text(strip=True) or None

        # Listing URL
        listing_url: str | None = None
        link = card.select_one('a[href*="/rental/"]')
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("/"):
                listing_url = BASE_URL + href
            else:
                listing_url = href

        # Beds / Baths / Price
        beds: int | None = None
        baths: float | None = None
        rent: float | None = None
        info_el = card.select_one(".apartment-info")
        if info_el:
            beds, baths, rent = _parse_beds_baths_price(info_el.get_text())

        # Pets
        cats_allowed: bool | None = None
        dogs_allowed: bool | None = None
        feature_items = card.select(".apartment-features li")
        for li in feature_items:
            classes = li.get("class", [])
            if "cat" in classes:
                cats_allowed = True
            if "dog" in classes:
                dogs_allowed = True

        pets_allowed: bool | None = None
        if cats_allowed is True or dogs_allowed is True:
            pets_allowed = True
        elif cats_allowed is not None or dogs_allowed is not None:
            # We found feature items but neither cat nor dog
            pets_allowed = False

        # Photo
        photos: list[str] = []
        image_el = card.select_one(".imageCover")
        if image_el:
            style = image_el.get("style", "")
            img_url = _parse_image_url(style)
            if img_url:
                photos.append(img_url)

        lat, lng = geocode(address, "San Francisco", "CA")

        return Listing(
            source="rentsfnow",
            address=address,
            neighborhood=neighborhood,
            lat=lat,
            lng=lng,
            rent=rent,
            bedrooms=beds,
            bathrooms=baths,
            city="San Francisco",
            state="CA",
            pets_allowed=pets_allowed,
            cats_allowed=cats_allowed,
            dogs_allowed=dogs_allowed,
            photos=photos,
            listing_url=listing_url,
        )
    except Exception:
        logger.warning("Failed to parse listing card", exc_info=True)
        return None


def _is_endscroll(html: str) -> bool:
    """Check if the response HTML contains the endscroll sentinel."""
    soup = BeautifulSoup(html, "lxml")
    end_input = soup.select_one('input#endscroll, input[name="endscroll"]')
    if end_input and end_input.get("value") == "1":
        return True
    return False


@register
class RentSFNowScraper:
    slug = "rentsfnow"

    def fetch(self) -> list[Listing]:
        listings: list[Listing] = []
        page = 1

        with httpx.Client(timeout=30) as client:
            while True:
                logger.info("Fetching RentSFNow page %d", page)
                try:
                    response = client.post(
                        AJAX_URL,
                        data={
                            "action": "wpas_ajax_load",
                            "type": "search",
                            "page": str(page),
                            "city": "san-francisco",
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    logger.warning("HTTP error fetching page %d", page, exc_info=True)
                    break

                html = response.text
                if not html or not html.strip():
                    logger.info("Empty response on page %d, stopping pagination", page)
                    break

                if _is_endscroll(html):
                    logger.info("Endscroll reached on page %d, stopping pagination", page)
                    break

                soup = BeautifulSoup(html, "lxml")
                cards = soup.select('[id^="post-"]')

                if not cards:
                    logger.info("No listing cards on page %d, stopping pagination", page)
                    break

                for card in cards:
                    listing = _parse_listing_card(card)
                    if listing:
                        listings.append(listing)

                page += 1

        logger.info("Scraped %d listings from RentSFNow", len(listings))
        return listings
