"""Populated scraper registry.

Importing this module imports every scraper module for its side effect of
registering itself via ``@register``. Callers that need the full registry
(``SCRAPERS``, ``get_scraper``, ``get_all_scrapers``) import from here rather
than from ``registry`` directly, since ``registry`` on its own is empty until
the scraper modules have been imported. Registration lives here (a normal
module) rather than in ``__init__.py``, which is kept blank by repo convention.
"""

from __future__ import annotations

# Import each scraper module so it self-registers via @register.
import apartment_hunt.scrapers.appfolio  # noqa: F401,E402
import apartment_hunt.scrapers.avalonbay  # noqa: F401,E402
import apartment_hunt.scrapers.brookfield  # noqa: F401,E402

# apartment_hunt.scrapers.equity is disabled: the site is now behind Cloudflare
# Turnstile (an interactive challenge with a Private Access Token), which httpx
# and headless browsers can't clear. The module and its tests are retained in
# case the protection is ever relaxed; re-enable by uncommenting the import.
# import apartment_hunt.scrapers.equity  # noqa: F401,E402
import apartment_hunt.scrapers.craigslist  # noqa: F401,E402
import apartment_hunt.scrapers.jwavro  # noqa: F401,E402
import apartment_hunt.scrapers.mmgprop  # noqa: F401,E402
import apartment_hunt.scrapers.rentalsinsf  # noqa: F401,E402
import apartment_hunt.scrapers.rentsfnow  # noqa: F401,E402
import apartment_hunt.scrapers.slpm  # noqa: F401,E402

# structureproperties migrated to AppFolio; its scraper now lives in appfolio.py.
from apartment_hunt.scrapers.registry import (  # noqa: F401  (re-exported)
    SCRAPERS,
    get_all_scrapers,
    get_scraper,
)
