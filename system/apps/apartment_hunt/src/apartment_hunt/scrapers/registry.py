from __future__ import annotations

from apartment_hunt.scrapers.base import Scraper

# Maps landlord slug -> scraper class.
# Each scraper module should register itself by adding to this dict.
SCRAPERS: dict[str, type[Scraper]] = {}


def register(scraper_cls: type[Scraper]) -> type[Scraper]:
    """Decorator to register a scraper class in the global registry."""
    SCRAPERS[scraper_cls.slug] = scraper_cls
    return scraper_cls


def get_scraper(slug: str) -> Scraper:
    """Instantiate and return a scraper by its slug."""
    if slug not in SCRAPERS:
        raise KeyError(f"Unknown scraper: {slug!r}. Available: {list(SCRAPERS.keys())}")
    return SCRAPERS[slug]()


def get_all_scrapers() -> list[Scraper]:
    """Instantiate and return all registered scrapers."""
    return [cls() for cls in SCRAPERS.values()]
