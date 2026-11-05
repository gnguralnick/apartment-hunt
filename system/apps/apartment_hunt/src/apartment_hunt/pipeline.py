from __future__ import annotations

import json
import logging
from pathlib import Path

from apartment_hunt.config import Config, Filters, RangeFilter
from apartment_hunt.dedup import deduplicate
from apartment_hunt.models import Listing, ScoredListing
from apartment_hunt.paths import data_dir as _data_dir
from apartment_hunt.scoring import score_listings
from apartment_hunt.travel import TravelTimeCalculator

logger = logging.getLogger(__name__)

DATA_DIR = _data_dir()

# Controls whether listings missing a filtered field are excluded.
# Set to True to let listings with missing data pass hard filters.
MISSING_DATA_PASSES_FILTER = False


def load_jsonl(data_dir: Path | None = None) -> list[Listing]:
    """Load all JSONL files from the data directory into Listing objects."""
    data_dir = data_dir or DATA_DIR
    listings: list[Listing] = []
    for jsonl_file in sorted(data_dir.glob("*.jsonl")):
        logger.info("Loading %s", jsonl_file.name)
        with open(jsonl_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    listings.append(Listing(**data))
                except Exception:
                    logger.warning(
                        "Skipping invalid record in %s line %d", jsonl_file.name, line_num
                    )
    logger.info("Loaded %d listings from %d files", len(listings), len(list(data_dir.glob("*.jsonl"))))
    return listings


def _check_range(value: float | int | None, range_filter: RangeFilter) -> bool:
    """Check if a value passes a range filter. Respects MISSING_DATA_PASSES_FILTER."""
    if range_filter.min is None and range_filter.max is None:
        return True
    if value is None:
        return MISSING_DATA_PASSES_FILTER
    if range_filter.min is not None and value < range_filter.min:
        return False
    if range_filter.max is not None and value > range_filter.max:
        return False
    return True


def _check_bool(value: bool | None, required: bool | None) -> bool:
    """Check if a boolean value matches a required filter."""
    if required is None:
        return True
    if value is None:
        return MISSING_DATA_PASSES_FILTER
    return value == required


def _check_string(value: str | None, required: str | None) -> bool:
    """Check if a string value matches (case-insensitive)."""
    if required is None:
        return True
    if value is None:
        return MISSING_DATA_PASSES_FILTER
    return value.lower() == required.lower()


def _listing_passes_filters(listing: Listing, filters: Filters) -> bool:
    """Check whether a single listing passes all hard filters."""
    if not _check_range(listing.rent, filters.rent):
        return False
    if not _check_range(listing.bedrooms, filters.bedrooms):
        return False
    if not _check_range(listing.bathrooms, filters.bathrooms):
        return False
    if not _check_range(listing.sqft, filters.sqft):
        return False
    if not _check_string(listing.city, filters.city):
        return False
    if not _check_bool(listing.pets_allowed, filters.pets_allowed):
        return False
    return True


def apply_filters(listings: list[Listing], filters: Filters) -> list[Listing]:
    """Apply hard filters, removing listings that don't meet constraints."""
    return [ls for ls in listings if _listing_passes_filters(ls, filters)]


def apply_filters_scored(
    scored: list[ScoredListing], filters: Filters
) -> list[ScoredListing]:
    """Apply hard filters to already-scored listings."""
    return [sl for sl in scored if _listing_passes_filters(sl.listing, filters)]


def run_pipeline(
    config: Config,
    data_dir: Path | None = None,
    travel_time_calculator: TravelTimeCalculator | None = None,
) -> list[ScoredListing]:
    """Full pipeline: load → deduplicate → filter → score → sort."""
    listings = load_jsonl(data_dir)
    listings = deduplicate(listings)
    listings = apply_filters(listings, config.filters)
    scored = score_listings(listings, config, travel_time_calculator)
    return scored
