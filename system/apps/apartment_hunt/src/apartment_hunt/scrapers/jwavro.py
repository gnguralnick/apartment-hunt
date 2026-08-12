"""Scraper for J. Wavro Associates (jwavro.com) rental listings.

The site was rebuilt around a ``letsrent.com`` back end: the search page
(``rental_list.php``) renders a grid of ``a.rental-card-link`` anchors, each
wrapping a ``.rental-card`` with price, neighborhood, a title/description, and
bed/bath specs. J. Wavro deliberately hides exact street addresses, so the card
carries only a neighborhood; the per-listing detail page
(``rental_details.php?id=...``) embeds a Google Maps link with the real
coordinates and a fuller description with cross-streets. We parse the cards,
then fetch each detail page (cached) for coordinates, falling back to geocoding
the neighborhood when a detail page has no map embed.
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup, Tag

from apartment_hunt.geocode import GeocodingError, geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.cache import DetailCache
from apartment_hunt.scrapers.registry import register

logger = logging.getLogger(__name__)

_URL = "https://jwavro.com/rental_list.php?hood=San+Francisco"
_DETAIL_BASE = "https://jwavro.com/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_REQUEST_DELAY = 0.5
_TIMEOUT = 30

_PRICE_RE = re.compile(r"\$\s*([\d,]+)")
_BEDS_RE = re.compile(r"(\d+)\s*(?:BR|BD|Bed)", re.IGNORECASE)
_BATHS_RE = re.compile(r"([\d.]+)\s*BA", re.IGNORECASE)
# The detail page embeds a maps link like ".../maps?q=37.7662,-122.4459".
_COORDS_RE = re.compile(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)")


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_beds(specs: list[str]) -> int | None:
    for spec in specs:
        if "studio" in spec.lower():
            return 0
        match = _BEDS_RE.search(spec)
        if match:
            return int(match.group(1))
    return None


def _parse_baths(specs: list[str]) -> float | None:
    for spec in specs:
        match = _BATHS_RE.search(spec)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _parse_detail_page(html: str) -> dict:
    """Extract coordinates and description from a detail page."""
    result: dict = {}
    coords_match = _COORDS_RE.search(html)
    if coords_match:
        try:
            result["lat"] = float(coords_match.group(1))
            result["lng"] = float(coords_match.group(2))
        except ValueError:
            pass

    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string or '"Apartment"' not in script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") == "Apartment":
            desc = (data.get("description") or "").strip()
            if desc:
                result["description"] = desc
            break
    return result


def _parse_card(card: Tag, href: str | None) -> dict | None:
    """Parse a single search-page card into an intermediate dict."""
    desc_el = card.select_one(".rental-card-description")
    img_el = card.select_one(".rental-card-image img")
    title = (desc_el.get_text(strip=True) if desc_el else "") or (
        img_el.get("alt", "").strip() if img_el else ""
    )

    hood_el = card.select_one(".rental-card-location span")
    neighborhood = hood_el.get_text(strip=True) if hood_el else None

    # Address (label) is the title; the site hides the street address. Fall back
    # to the neighborhood so there is always a non-empty, human-meaningful label.
    address = title or (f"{neighborhood} rental" if neighborhood else "")
    if not address:
        return None

    price_el = card.select_one(".rental-card-price-badge")
    rent = _parse_price(price_el.get_text() if price_el else None)

    specs = [sp.get_text(strip=True) for sp in card.select(".rental-card-spec span")]
    amenities = [a.get_text(strip=True) for a in card.select(".rental-card-amenity")]
    pets_allowed = True if any("pet" in a.lower() for a in amenities) else None

    photos: list[str] = []
    if img_el:
        src = img_el.get("src", "")
        if src and not src.startswith("data:"):
            photos.append(src)

    source_id = None
    if href:
        id_match = re.search(r"id=([\w-]+)", href)
        if id_match:
            source_id = id_match.group(1)

    return {
        "address": address,
        "neighborhood": neighborhood,
        "rent": rent,
        "bedrooms": _parse_beds(specs),
        "bathrooms": _parse_baths(specs),
        "pets_allowed": pets_allowed,
        "photos": photos,
        "amenities": amenities,
        "detail_url": _DETAIL_BASE + href.lstrip("/") if href else None,
        "source_id": source_id,
    }


@register
class JWavroScraper:
    slug = "jwavro"

    def __init__(self, cache: DetailCache | None = None):
        self._cache = cache if cache is not None else DetailCache(self.slug)

    def fetch(self) -> list[Listing]:
        try:
            resp = httpx.get(
                _URL,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.error("Failed to fetch J. Wavro listings", exc_info=True)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        anchors = soup.select("a.rental-card-link")
        if not anchors:
            logger.warning("No listing cards found on J. Wavro page")
            return []

        listings: list[Listing] = []
        live_urls: set[str] = set()
        fetched_any = False

        with httpx.Client(timeout=_TIMEOUT) as client:
            for anchor in anchors:
                card = anchor.select_one(".rental-card")
                if card is None:
                    continue
                href = anchor.get("href")
                parsed = _parse_card(card, href)
                if parsed is None:
                    continue

                detail_url = parsed.get("detail_url")
                detail: dict = {}
                if detail_url:
                    live_urls.add(detail_url)
                    cached = self._cache.get(detail_url)
                    if cached is not None:
                        detail = cached
                    else:
                        if fetched_any:
                            time.sleep(_REQUEST_DELAY)
                        fetched_any = True
                        try:
                            dresp = client.get(
                                detail_url,
                                headers={"User-Agent": _USER_AGENT},
                                follow_redirects=True,
                            )
                            dresp.raise_for_status()
                            detail = _parse_detail_page(dresp.text)
                            self._cache.put(detail_url, detail)
                        except httpx.HTTPError:
                            logger.warning(
                                "Failed to fetch J. Wavro detail: %s", detail_url
                            )

                lat = detail.get("lat")
                lng = detail.get("lng")
                if lat is None or lng is None:
                    # Fall back to a neighborhood-center geocode.
                    hood = parsed.get("neighborhood")
                    if hood:
                        try:
                            lat, lng = geocode(hood, "San Francisco", "CA")
                        except GeocodingError:
                            logger.warning("Could not geocode neighborhood %r", hood)

                listings.append(
                    Listing(
                        source="jwavro",
                        address=parsed["address"],
                        lat=lat,
                        lng=lng,
                        rent=parsed["rent"],
                        bedrooms=parsed["bedrooms"],
                        bathrooms=parsed["bathrooms"],
                        neighborhood=parsed["neighborhood"],
                        city="San Francisco",
                        state="CA",
                        pets_allowed=parsed["pets_allowed"],
                        amenities=parsed["amenities"],
                        description=detail.get("description"),
                        photos=parsed["photos"],
                        detail_url=detail_url,
                        listing_url=_URL,
                        source_id=parsed["source_id"],
                    )
                )

        self._cache.retain(live_urls)
        self._cache.save()
        logger.info("Scraped %d listings from jwavro", len(listings))
        return listings
