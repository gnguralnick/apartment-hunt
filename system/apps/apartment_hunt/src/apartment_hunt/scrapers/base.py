from __future__ import annotations

from typing import ClassVar, Protocol

from apartment_hunt.models import Listing


class Scraper(Protocol):
    """Interface that all landlord scrapers must implement."""

    slug: ClassVar[str]

    def fetch(self) -> list[Listing]: ...
