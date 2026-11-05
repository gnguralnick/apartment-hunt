from __future__ import annotations

import re
from collections import defaultdict

from apartment_hunt.models import Listing

ABBREVIATIONS = {
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "rd": "road",
    "ct": "court",
    "pl": "place",
    "cir": "circle",
    "pkwy": "parkway",
    "hwy": "highway",
    "apt": "apartment",
    "ste": "suite",
    "fl": "floor",
}


def normalize_address(address: str) -> str:
    """Normalize an address for deduplication comparison.

    Lowercase, strip punctuation, expand common abbreviations.
    """
    addr = address.lower().strip()
    # Remove punctuation except spaces
    addr = re.sub(r"[^\w\s]", "", addr)
    # Collapse whitespace
    addr = re.sub(r"\s+", " ", addr)
    # Expand abbreviations (whole-word only)
    words = addr.split()
    words = [ABBREVIATIONS.get(w, w) for w in words]
    return " ".join(words)


def _merge_two(a: Listing, b: Listing) -> Listing:
    """Merge two listings. For conflicting non-None values, prefer the listing
    with more populated fields overall."""
    prefer_a = a.populated_field_count() >= b.populated_field_count()
    primary, secondary = (a, b) if prefer_a else (b, a)

    merged_data: dict = {}
    for field_name in Listing.model_fields:
        p_val = getattr(primary, field_name)
        s_val = getattr(secondary, field_name)

        if field_name == "sources":
            # Merge source lists
            all_sources = list(dict.fromkeys(
                primary.sources + secondary.sources
                + [primary.source, secondary.source]
            ))
            merged_data["sources"] = all_sources
            continue

        if isinstance(p_val, list):
            # For list fields, take the longer list
            merged_data[field_name] = p_val if len(p_val) >= len(s_val) else s_val
        elif p_val is not None:
            merged_data[field_name] = p_val
        else:
            merged_data[field_name] = s_val

    return Listing(**merged_data)


def deduplicate(listings: list[Listing]) -> list[Listing]:
    """Group listings by normalized address and merge duplicates."""
    groups: defaultdict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        key = normalize_address(listing.address)
        groups[key].append(listing)

    results = []
    for group in groups.values():
        merged = group[0]
        if not merged.sources:
            merged = merged.model_copy(update={"sources": [merged.source]})
        for other in group[1:]:
            merged = _merge_two(merged, other)
        results.append(merged)
    return results
