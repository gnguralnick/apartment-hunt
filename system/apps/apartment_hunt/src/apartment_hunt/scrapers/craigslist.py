"""Scraper for Craigslist SF Bay Area apartment listings."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from apartment_hunt.config import Filters, load_config
from apartment_hunt.models import Listing
from apartment_hunt.scrapers.cache import DetailCache
from apartment_hunt.scrapers.registry import register

logger = logging.getLogger(__name__)

SEARCH_URL = "https://sfbay.craigslist.org/search/san-francisco-ca/apa"
BASE_PARAMS = {
    "lat": "37.7555",
    "lon": "-122.4402",
    "search_distance": "4.8",
}
_REQUEST_DELAY = 1.0
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
_TIMEOUT = 30


def _build_search_params(filters: Filters) -> dict[str, str]:
    """Map config filters to Craigslist search URL parameters."""
    params = dict(BASE_PARAMS)
    if filters.rent.min is not None:
        params["min_price"] = str(int(filters.rent.min))
    if filters.rent.max is not None:
        params["max_price"] = str(int(filters.rent.max))
    if filters.bedrooms.min is not None:
        params["min_bedrooms"] = str(int(filters.bedrooms.min))
    if filters.bedrooms.max is not None:
        params["max_bedrooms"] = str(int(filters.bedrooms.max))
    if filters.bathrooms.min is not None:
        params["min_bathrooms"] = str(int(filters.bathrooms.min))
    if filters.bathrooms.max is not None:
        params["max_bathrooms"] = str(int(filters.bathrooms.max))
    if filters.sqft.min is not None:
        params["minSqft"] = str(int(filters.sqft.min))
    if filters.sqft.max is not None:
        params["maxSqft"] = str(int(filters.sqft.max))
    if filters.pets_allowed is True:
        params["pets_cat"] = "1"
        params["pets_dog"] = "1"
    return params


def _parse_price(text: str) -> float | None:
    """Parse a price string like '$1,125' to float. Returns None for $0."""
    match = re.search(r"\$\s*([\d,]+)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if value > 0 else None


def _parse_beds_baths(text: str) -> tuple[int | None, float | None]:
    """Parse '2BR / 1.5Ba' into (bedrooms, bathrooms)."""
    beds: int | None = None
    baths: float | None = None
    bed_match = re.search(r"(\d+)\s*BR", text, re.IGNORECASE)
    if bed_match:
        beds = int(bed_match.group(1))
    bath_match = re.search(r"([\d.]+)\s*Ba", text, re.IGNORECASE)
    if bath_match:
        baths = float(bath_match.group(1))
    return beds, baths


def _parse_sqft(text: str) -> float | None:
    """Parse '1104ft2' into sqft float."""
    match = re.search(r"([\d,]+)\s*ft", text, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _parse_search_page(html: str) -> list[dict]:
    """Parse search page static HTML + JSON-LD into listing dicts.

    Each dict contains keys from the static results (detail_url, title, price,
    neighborhood) merged with the JSON-LD data (bedrooms, bathrooms, lat, lng,
    city, state, zip).  The two lists are aligned by index.
    """
    soup = BeautifulSoup(html, "lxml")

    # Parse static search results for URL, price, neighborhood
    static_items: list[dict] = []
    ol = soup.select_one("ol.cl-static-search-results")
    if ol:
        for li in ol.select("li.cl-static-search-result"):
            item: dict = {}
            link = li.select_one("a")
            if link:
                item["detail_url"] = link.get("href", "")
            item["title"] = li.get("title", "")
            price_el = li.select_one(".price")
            item["price"] = _parse_price(price_el.get_text()) if price_el else None
            loc_el = li.select_one(".location")
            item["neighborhood"] = (
                loc_el.get_text(strip=True) or None if loc_el else None
            )
            static_items.append(item)

    # Parse JSON-LD for bedrooms, bathrooms, lat, lng
    ld_items: list[dict] = []
    ld_script = soup.select_one("script#ld_searchpage_results")
    if ld_script and ld_script.string:
        try:
            data = json.loads(ld_script.string)
            ld_items = data.get("itemListElement", [])
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON-LD from search page")

    # Merge by index (static and LD lists are in the same order)
    results: list[dict] = []
    for i, static in enumerate(static_items):
        result = dict(static)
        if i < len(ld_items):
            ld_item = ld_items[i].get("item", {})
            beds = ld_item.get("numberOfBedrooms")
            if beds is not None:
                try:
                    result["bedrooms"] = int(beds)
                except (ValueError, TypeError):
                    pass
            baths = ld_item.get("numberOfBathroomsTotal")
            if baths is not None:
                try:
                    result["bathrooms"] = float(baths)
                except (ValueError, TypeError):
                    pass
            result["lat"] = ld_item.get("latitude")
            result["lng"] = ld_item.get("longitude")
            addr = ld_item.get("address", {})
            result["city"] = addr.get("addressLocality") or None
            result["state"] = addr.get("addressRegion") or None
            result["zip"] = addr.get("postalCode") or None
        results.append(result)

    return results


def _parse_detail_page(html: str) -> dict:
    """Parse a Craigslist detail page for enriched listing data.

    Returns a dict with available fields: address, city, state, zip, lat, lng,
    price, bedrooms, bathrooms, sqft, pets_allowed, laundry, parking,
    description, photos, source_id.
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict = {}

    # JSON-LD for address, coordinates, and pet policy
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") != "Apartment":
            continue
        addr = data.get("address", {})
        street = addr.get("streetAddress", "")
        if street:
            result["address"] = street
        result["city"] = addr.get("addressLocality") or None
        result["state"] = addr.get("addressRegion") or None
        result["zip"] = addr.get("postalCode") or None
        if data.get("latitude"):
            result["lat"] = float(data["latitude"])
        if data.get("longitude"):
            result["lng"] = float(data["longitude"])
        if "petsAllowed" in data:
            result["pets_allowed"] = data["petsAllowed"]

    # Price
    price_el = soup.select_one(".price")
    if price_el:
        result["price"] = _parse_price(price_el.get_text())

    # Beds / baths / sqft from attribute groups
    for group in soup.select(".attrgroup"):
        text = group.get_text()
        if "BR" in text or "Ba" in text:
            beds, baths = _parse_beds_baths(text)
            if beds is not None:
                result["bedrooms"] = beds
            if baths is not None:
                result["bathrooms"] = baths
        sqft = _parse_sqft(text)
        if sqft is not None:
            result["sqft"] = sqft

    # Laundry and parking from attribute links
    for group in soup.select(".attrgroup"):
        for attr_div in group.select(".attr"):
            link = attr_div.select_one("a")
            if not link:
                continue
            href = str(link.get("href", ""))
            if "laundry" in href:
                result["laundry"] = link.get_text(strip=True)
            elif "parking" in href:
                result["parking"] = link.get_text(strip=True)

    # Description
    body = soup.select_one("#postingbody")
    if body:
        for qr in body.select(".print-information"):
            qr.decompose()
        result["description"] = body.get_text(strip=True)

    # Photos from the imgList JavaScript variable
    for script in soup.find_all("script"):
        text = script.string or ""
        if "imgList" not in text:
            continue
        match = re.search(r"var\s+imgList\s*=\s*(\[.*?\]);", text, re.DOTALL)
        if match:
            try:
                imgs = json.loads(match.group(1))
                result["photos"] = [img["url"] for img in imgs if "url" in img]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # Post ID
    for pi in soup.select(".postinginfos .postinginfo"):
        text = pi.get_text(strip=True)
        id_match = re.search(r"post id:\s*(\d+)", text)
        if id_match:
            result["source_id"] = id_match.group(1)

    return result


def _coalesce(*values):
    """Return the first value that is not None."""
    for v in values:
        if v is not None:
            return v
    return None


@register
class CraigslistScraper:
    slug = "craigslist"

    def __init__(self, cache: DetailCache | None = None):
        # Cache of parsed detail-page dicts keyed by detail URL.  Listings that
        # disappear from the search page naturally drop out of the output on
        # the next run, so stale entries don't matter for correctness — we
        # only ever look up URLs we just saw in a fresh search response.
        self._cache = cache if cache is not None else DetailCache(self.slug)

    def fetch(self) -> list[Listing]:
        listings: list[Listing] = []

        try:
            config = load_config()
            filters = config.filters
        except Exception:
            logger.warning("Could not load config, using unfiltered search")
            filters = Filters()

        params = _build_search_params(filters)
        logger.info("Craigslist search params: %s", params)

        with httpx.Client(timeout=_TIMEOUT) as client:
            # Step 1: Fetch search page
            logger.info("Fetching Craigslist search page")
            try:
                resp = client.get(
                    SEARCH_URL,
                    params=params,
                    headers={"User-Agent": _USER_AGENT},
                    follow_redirects=True,
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.error("Failed to fetch search page", exc_info=True)
                return []

            search_results = _parse_search_page(resp.text)
            logger.info("Found %d listings on search page", len(search_results))

            # Step 2: Fetch each detail page for enriched data (cache-aware)
            fetched_any = False  # Track whether we need a sleep before next fetch
            live_urls: set[str] = set()
            for i, result in enumerate(search_results):
                detail_url = result.get("detail_url")
                if not detail_url:
                    continue
                live_urls.add(detail_url)

                cached = self._cache.get(detail_url)
                if cached is not None:
                    logger.debug(
                        "Cache hit %d/%d: %s",
                        i + 1,
                        len(search_results),
                        detail_url,
                    )
                    detail = cached
                else:
                    if fetched_any:
                        time.sleep(_REQUEST_DELAY)
                    fetched_any = True

                    logger.debug(
                        "Fetching detail %d/%d: %s",
                        i + 1,
                        len(search_results),
                        detail_url,
                    )
                    try:
                        detail_resp = client.get(
                            detail_url,
                            headers={"User-Agent": _USER_AGENT},
                            follow_redirects=True,
                        )
                        detail_resp.raise_for_status()
                    except httpx.HTTPError:
                        logger.warning(
                            "Failed to fetch detail: %s", detail_url, exc_info=True
                        )
                        continue

                    detail = _parse_detail_page(detail_resp.text)
                    self._cache.put(detail_url, detail)

                # Merge: detail data takes precedence over search data
                address = detail.get("address") or result.get("neighborhood") or result.get("title", "Unknown")
                lat = _coalesce(detail.get("lat"), result.get("lat"))
                lng = _coalesce(detail.get("lng"), result.get("lng"))

                if lat is None or lng is None:
                    logger.warning("No coordinates for: %s", detail_url)
                    continue

                listing = Listing(
                    source="craigslist",
                    address=address,
                    lat=lat,
                    lng=lng,
                    rent=_coalesce(detail.get("price"), result.get("price")),
                    bedrooms=_coalesce(detail.get("bedrooms"), result.get("bedrooms")),
                    bathrooms=_coalesce(
                        detail.get("bathrooms"), result.get("bathrooms")
                    ),
                    sqft=detail.get("sqft"),
                    neighborhood=result.get("neighborhood"),
                    city=_coalesce(detail.get("city"), result.get("city")),
                    state=_coalesce(detail.get("state"), result.get("state")),
                    zip=_coalesce(detail.get("zip"), result.get("zip")),
                    pets_allowed=detail.get("pets_allowed"),
                    laundry=detail.get("laundry"),
                    parking=detail.get("parking"),
                    description=detail.get("description"),
                    photos=detail.get("photos", []),
                    listing_url=detail_url,
                    source_id=detail.get("source_id"),
                )
                listings.append(listing)

        # Prune cache entries for listings that are no longer on the search
        # page, then persist.  Keeps the cache bounded to currently-live URLs.
        self._cache.retain(live_urls)
        self._cache.save()

        logger.info("Scraped %d listings from Craigslist", len(listings))
        return listings
