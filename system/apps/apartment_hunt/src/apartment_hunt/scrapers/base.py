from __future__ import annotations

from typing import Protocol

from apartment_hunt.models import Listing


class Scraper(Protocol):
    """Interface that all landlord scrapers must implement."""

    slug: str

    def fetch(self) -> list[Listing]: ...
