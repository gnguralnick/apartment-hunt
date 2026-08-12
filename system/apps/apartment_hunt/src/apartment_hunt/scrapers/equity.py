"""Scraper for Equity Residential SF properties.

Equity Residential is a publicly traded REIT with ~8 communities in San
Francisco proper.  Data is extracted in two levels:

1. **Homepage** — contains ``ea5.siteSearch``, a JS search index with URLs
   and metadata for all communities nationally.  We filter for SF entries.
2. **Property pages** — each community page embeds JS objects (``ea5.hero``,
   ``ea5.contactProperty``, ``ea5.mediaGallery``) with pricing, address,
   photos and contact info.

If the homepage parse fails, we fall back to a hardcoded list of known SF
property URLs.
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from apartment_hunt.geocode import geocode
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.cache import DetailCache
from apartment_hunt.scrapers.registry import register

log = logging.getLogger(__name__)

BASE_URL = "https://www.equityapartments.com"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Regex to extract ea5.siteSearch from inline <script> on the homepage.
_SITE_SEARCH_RE = re.compile(r"ea5\.siteSearch\s*=\s*(\[.*?\]);\s*$", re.DOTALL | re.MULTILINE)

# Regexes for extracting ea5.* JSON objects from property pages.
# These assignments use the pattern:  ea5.hero = { ... };
# The value may span many lines, so we match from the opening brace/bracket
# to the next ea5.* assignment or </script>.
_EA5_HERO_RE = re.compile(
    r"ea5\.hero\s*=\s*(\{.*?\});\s*(?=ea5\.|</script>)", re.DOTALL
)
_EA5_CONTACT_RE = re.compile(
    r"ea5\.contactProperty\s*=\s*(\{.*?\});\s*(?=ea5\.|</script>)", re.DOTALL
)
_EA5_MEDIA_RE = re.compile(
    r"ea5\.mediaGallery\s*=\s*(\{.*?\});\s*(?=ea5\.|</script>)", re.DOTALL
)

# Hardcoded fallback: known SF Equity Residential property URLs.
# Used when the metro-page parse of ea5.siteSearch fails.
_FALLBACK_SF_PROPERTIES: list[dict[str, str]] = [
    {
        "name": "The Terraces",
        "url": "/san-francisco-bay/lower-nob-hill/the-terraces-apartments",
    },
    {
        "name": "77 Bluxome",
        "url": "/san-francisco-bay/soma/77-bluxome-apartments",
    },
    {
        "name": "SoMa Square",
        "url": "/san-francisco-bay/soma/soma-square-apartments",
    },
    {
        "name": "Azure",
        "url": "/san-francisco-bay/mission-bay/azure-apartments",
    },
    {
        "name": "Potrero 1010",
        "url": "/san-francisco/potrero-hill/potrero-1010-apartments",
    },
    {
        "name": "340 Fremont",
        "url": "/san-francisco/rincon-hill/340-fremont-apartments",
    },
    {
        "name": "One Henry Adams",
        "url": "/san-francisco/design-district/one-henry-adams-apartments",
    },
    {
        "name": "855 Brannan",
        "url": "/san-francisco/soma/855-brannan-apartments",
    },
]


# ---------------------------------------------------------------------------
# Level 1: discover SF community URLs from the homepage search index
# ---------------------------------------------------------------------------


def _fetch_sf_property_urls(client: httpx.Client) -> list[dict[str, str]]:
    """Fetch the homepage and parse ea5.siteSearch for SF community entries.

    Returns a list of dicts with keys ``name`` and ``url`` (path only).
    Falls back to the hardcoded list on any failure.
    """
    try:
        resp = client.get(
            BASE_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        log.warning("Failed to fetch Equity homepage, using fallback URLs", exc_info=True)
        return list(_FALLBACK_SF_PROPERTIES)

    match = _SITE_SEARCH_RE.search(resp.text)
    if not match:
        log.warning("ea5.siteSearch not found on homepage, using fallback URLs")
        return list(_FALLBACK_SF_PROPERTIES)

    try:
        site_search = json.loads(match.group(1))
    except json.JSONDecodeError:
        log.warning("Failed to parse ea5.siteSearch JSON, using fallback URLs", exc_info=True)
        return list(_FALLBACK_SF_PROPERTIES)

    # Filter for San Francisco city properties.
    # In the siteSearch data, SF properties have Metadata "San Francisco, CA"
    # and URLs starting with /san-francisco-bay/ or /san-francisco/.
    sf_properties: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for entry in site_search:
        results = entry.get("Results", [])
        for result in results:
            metadata = result.get("Metadata", "")
            url = result.get("Url", "")
            entry_id = result.get("Id", "")

            if entry_id in seen_ids:
                continue

            if metadata.strip() == "San Francisco, CA" and url:
                seen_ids.add(entry_id)
                name = result.get("Name", "").replace(" Apartments", "").strip()
                sf_properties.append({"name": name, "url": url})

    if not sf_properties:
        log.warning("No SF properties found in ea5.siteSearch, using fallback URLs")
        return list(_FALLBACK_SF_PROPERTIES)

    log.info("Discovered %d SF Equity properties from homepage", len(sf_properties))
    return sf_properties


# ---------------------------------------------------------------------------
# Level 2: scrape individual property pages
# ---------------------------------------------------------------------------


def _extract_js_object(html: str, pattern: re.Pattern[str], label: str) -> dict | None:
    """Extract and parse a JSON object from an ea5.* JS assignment in HTML."""
    match = pattern.search(html)
    if not match:
        log.debug("Pattern %s not found in page", label)
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        log.debug("Failed to parse %s JSON", label, exc_info=True)
        return None


def _extract_address_from_html(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract street address, city, state, zip from microdata in the HTML.

    Equity property pages use itemprop attributes: streetAddress,
    addressLocality, addressRegion, postalCode.
    """
    soup = BeautifulSoup(html, "lxml")
    street_el = soup.select_one('[itemprop="streetAddress"]')
    city_el = soup.select_one('[itemprop="addressLocality"]')
    state_el = soup.select_one('[itemprop="addressRegion"]')
    zip_el = soup.select_one('[itemprop="postalCode"]')

    street = street_el.get_text(strip=True) if street_el else None
    city = city_el.get_text(strip=True) if city_el else None
    state = state_el.get_text(strip=True) if state_el else None
    zip_code = zip_el.get_text(strip=True) if zip_el else None
    return street, city, state, zip_code


def _parse_availability(
    availability: list[dict] | None,
) -> tuple[int | None, float | None]:
    """Extract minimum bedrooms and minimum rent from hero Availability data.

    Each entry has BedroomCount, MinPrice (nullable), Count.
    We pick the cheapest available bedroom count.
    """
    if not availability:
        return None, None

    best_rent: float | None = None
    best_beds: int | None = None

    for entry in availability:
        count = entry.get("Count", 0)
        min_price = entry.get("MinPrice")
        if count > 0 and min_price is not None:
            if best_rent is None or min_price < best_rent:
                best_rent = float(min_price)
                best_beds = entry.get("BedroomCount")

    return best_beds, best_rent


def _neighborhood_from_url(url_path: str) -> str | None:
    """Extract a human-readable neighborhood from a URL path.

    E.g. ``/san-francisco-bay/soma/77-bluxome-apartments`` -> ``SoMa``.
    """
    parts = url_path.strip("/").split("/")
    if len(parts) >= 2:
        raw = parts[-2]  # e.g. "soma", "lower-nob-hill", "mission-bay"
        # Title-case, handling hyphens
        return raw.replace("-", " ").title()
    return None


def _scrape_property_page(
    client: httpx.Client,
    name: str,
    url_path: str,
) -> Listing | None:
    """Fetch and parse a single Equity Residential property page."""
    full_url = BASE_URL + url_path

    try:
        resp = client.get(
            full_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        log.warning("Failed to fetch property page %s", full_url, exc_info=True)
        return None

    html = resp.text

    # If we got redirected to the homepage, the property URL is no longer valid.
    if resp.url.path in ("/", "") and url_path not in ("/", ""):
        log.warning("Property page %s redirected to homepage, skipping", full_url)
        return None

    # Extract address from microdata
    street, city, state, zip_code = _extract_address_from_html(html)

    # Extract ea5 JS objects
    hero = _extract_js_object(html, _EA5_HERO_RE, "ea5.hero")
    contact = _extract_js_object(html, _EA5_CONTACT_RE, "ea5.contactProperty")
    media = _extract_js_object(html, _EA5_MEDIA_RE, "ea5.mediaGallery")

    # -- Pricing / bedroom data from hero.Availability --
    bedrooms: int | None = None
    rent: float | None = None
    if hero:
        availability = hero.get("Availability")
        bedrooms, rent = _parse_availability(availability)

    # -- Phone from hero.MainPhone --
    phone: str | None = None
    if hero:
        phone = hero.get("MainPhone") or None

    # -- Contact info fallback from contactProperty --
    property_name = name
    if contact:
        property_name = contact.get("PropertyName") or name
        if not city:
            city = contact.get("PropertyCity") or None
        if not state:
            state = contact.get("PropertyState") or None

    # -- Photos from mediaGallery.Photos --
    photos: list[str] = []
    if media:
        for photo in media.get("Photos", [])[:10]:
            img_url = photo.get("ImageUrl")
            if img_url:
                photos.append(img_url)

    # If we didn't get photos from mediaGallery, try hero.Gallery
    if not photos and hero:
        for photo in hero.get("Gallery", [])[:10]:
            img_url = photo.get("ImageUrl")
            if img_url:
                photos.append(img_url)

    # -- Neighborhood --
    neighborhood = _neighborhood_from_url(url_path)

    # Build address: prefer the microdata street address, fall back to property name
    address = street or property_name
    if not address:
        log.warning("No address found for property %s, skipping", full_url)
        return None

    lat, lng = geocode(address, city or "San Francisco", state or "CA")

    return Listing(
        source="equity",
        address=address,
        city=city or "San Francisco",
        state=state or "CA",
        zip=zip_code,
        lat=lat,
        lng=lng,
        rent=rent,
        bedrooms=bedrooms,
        neighborhood=neighborhood,
        photos=photos,
        contact_phone=phone,
        listing_url=full_url,
    )


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------


@register
class EquityScraper:
    slug = "equity"

    def __init__(self, cache: DetailCache | None = None):
        # Cache stores the serialized Listing (model_dump) keyed by the
        # property URL path.  Properties that disappear from the homepage
        # siteSearch never get looked up, so removed properties naturally
        # drop out of the output on the next run.
        self._cache = cache if cache is not None else DetailCache(self.slug)

    def fetch(self) -> list[Listing]:
        listings: list[Listing] = []

        with httpx.Client(timeout=30) as client:
            sf_properties = _fetch_sf_property_urls(client)

            fetched_any = False  # Track whether we need a sleep before next fetch
            live_urls: set[str] = set()
            for prop in sf_properties:
                url_path = prop["url"]
                live_urls.add(url_path)

                cached = self._cache.get(url_path)
                if cached is not None:
                    log.info(
                        "Cached Equity property: %s (%s)", prop["name"], url_path
                    )
                    try:
                        listings.append(Listing(**cached))
                        continue
                    except Exception:
                        log.warning(
                            "Failed to deserialize cached listing for %s; refetching",
                            url_path,
                            exc_info=True,
                        )

                if fetched_any:
                    time.sleep(1)
                fetched_any = True

                log.info("Scraping Equity property: %s (%s)", prop["name"], url_path)
                listing = _scrape_property_page(client, prop["name"], url_path)
                if listing is not None:
                    listings.append(listing)
                    self._cache.put(url_path, listing.model_dump(mode="json"))

        self._cache.retain(live_urls)
        self._cache.save()

        log.info("Scraped %d listings from Equity Residential", len(listings))
        return listings
