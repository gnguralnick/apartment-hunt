"""Apartment Hunt -- aggregate, filter, score, and map SF rental listings.

Scrapes many landlord sites into per-source JSONL files under ``DATA_DIR``,
deduplicates by address, applies hard filters, scores each listing against
user preferences and travel-time-to-waypoints, and serves an interactive
Leaflet map + ranked card list. Users can tune filters/preferences/waypoints
live, re-scrape on demand, and annotate listings (hide / contacted / toured /
rating / notes), all persisted under ``DATA_DIR``.

Conventions (see the build-app skill):

- Persistent state lives under ``DATA_DIR`` (per-scraper JSONL, ``user_state.json``,
  ``preferences.yaml``, and scraper/travel caches). ``DATA_DIR`` defaults to
  ``data/.apps/apartment-hunt/`` but honors ``APARTMENT_HUNT_DATA_DIR``. All state
  paths resolve through ``apartment_hunt.paths``, which reads that env var, so a
  throwaway edit instance can run against a copy of the data.
- Bundled assets (the frontend and the seed preferences file) live next to this
  module under ``assets/``.
- The app serves at ``/``; the system_interface proxy handles the
  ``/service/apartment-hunt/`` prefix and rewrites the page's own fetches.
- Optional: set ``GOOGLE_MAPS_API_KEY`` to score travel times via the Google
  Routes API; without it the app falls back to straight-line estimates.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Flask, Response, request
from werkzeug.serving import run_simple

from apartment_hunt.config import (
    TIER_POINTS,
    Config,
    Filters,
    Preference,
    RangeFilter,
    Waypoint,
    WaypointMode,
    load_config,
    save_config,
)
from apartment_hunt.models import Listing
from apartment_hunt.paths import config_path, data_dir
from apartment_hunt.scrapers import catalog
from apartment_hunt.store import Store
from apartment_hunt.travel import GoogleRoutesTravelTimeCalculator, TravelTimeCalculator

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
PORT = int(os.environ.get("APARTMENT_HUNT_PORT", "8088"))

app = Flask("apartment_hunt", static_folder=None)

# ── global state (populated by init_app) ────────────────────
_store: Store | None = None
_scrape_progress: list[dict] = []
_scrape_lock = threading.Lock()
_scrape_running = False


def _get_store() -> Store:
    assert _store is not None, "Store not initialised -- call init_app() first"
    return _store


def _make_travel_calculator() -> TravelTimeCalculator | None:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if api_key:
        return GoogleRoutesTravelTimeCalculator(api_key)
    logger.warning(
        "GOOGLE_MAPS_API_KEY not set -- falling back to haversine distance estimates"
    )
    return None


def _seed_config() -> None:
    """Ensure an editable preferences.yaml exists under the data dir.

    Seeds it from the bundled default on first run so the user has something to
    edit and the scrapers that read the config (e.g. Craigslist) have a file.
    """
    dest = config_path()
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ASSETS / "preferences.yaml", dest)


def init_app() -> None:
    """Initialise the store. Called once before the server accepts requests."""
    global _store
    _seed_config()
    config = load_config()
    calculator = _make_travel_calculator()
    # Pass data_dir explicitly (resolved now) rather than relying on the
    # import-time default, so APARTMENT_HUNT_DATA_DIR is honored even when set
    # after this module was imported (tests, throwaway edit instances).
    _store = Store(config, data_dir=data_dir(), travel_time_calculator=calculator)


# ── frontend ────────────────────────────────────────────────

@app.route("/")
def index() -> Response:
    return Response((ASSETS / "index.html").read_text(), mimetype="text/html")


_STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}


@app.route("/static/<path:filename>")
def static_file(filename: str) -> Response:
    # Serve only from the bundled assets dir; reject any traversal.
    target = (ASSETS / filename).resolve()
    if not str(target).startswith(str(ASSETS.resolve())) or not target.is_file():
        return Response("Not found", status=404)
    mimetype = _STATIC_TYPES.get(target.suffix, "application/octet-stream")
    return Response(target.read_text(), mimetype=mimetype)


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


# ── listings + annotations ──────────────────────────────────

@app.route("/api/listings")
def get_listings() -> Response:
    store = _get_store()
    return _json(
        {"listings": store.get_listings(), "new_count": store.get_new_listings_count()}
    )


@app.route("/api/listings/state", methods=["PATCH"])
def update_annotation() -> Response:
    body = request.get_json(force=True)
    _get_store().update_annotation(body["normalized_address"], body["field"], body["value"])
    return _json({"ok": True})


@app.route("/api/dismiss-new", methods=["POST"])
def dismiss_new() -> Response:
    _get_store().dismiss_new()
    return _json({"ok": True})


# ── config ──────────────────────────────────────────────────

@app.route("/api/config")
def get_config() -> Response:
    return _json(_config_to_dict(_get_store().config))


@app.route("/api/config", methods=["PUT"])
def put_config() -> Response:
    """Persist a new config to preferences.yaml (does not re-score yet)."""
    config = _dict_to_config(request.get_json(force=True))
    save_config(config)
    _get_store().config = config
    return _json({"ok": True})


@app.route("/api/config/apply", methods=["POST"])
def apply_config() -> Response:
    """Re-score all listings with the persisted config."""
    _get_store().rescore(load_config())
    return _json({"ok": True})


# ── scraping ────────────────────────────────────────────────

@app.route("/api/scrape", methods=["POST"])
def start_scrape() -> Response:
    global _scrape_running
    body = request.get_json(silent=True) or {}
    landlords = body.get("landlords")
    with _scrape_lock:
        if _scrape_running:
            return _json({"error": "Scrape already in progress"}, status=409)
        _scrape_running = True
        _scrape_progress.clear()
    threading.Thread(target=_run_scrape, args=(landlords,), daemon=True).start()
    return _json({"ok": True})


@app.route("/api/scrape/progress")
def scrape_progress() -> Response:
    def stream() -> Iterator[str]:
        last_index = 0
        while True:
            with _scrape_lock:
                new_events = _scrape_progress[last_index:]
                last_index = len(_scrape_progress)
            for event in new_events:
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    return
            time.sleep(0.3)

    return Response(stream(), mimetype="text/event-stream")


def _fetch_scraper(scraper: object, out_dir: Path) -> int:
    """Run one scraper and write its listings to ``<slug>.jsonl``. Returns count."""
    listings: list[Listing] = scraper.fetch()  # type: ignore[attr-defined]
    out_path = out_dir / f"{scraper.slug}.jsonl"  # type: ignore[attr-defined]
    with open(out_path, "w") as f:
        for listing in listings:
            f.write(listing.model_dump_json() + "\n")
    return len(listings)


def _run_scrape(landlords: list[str] | None) -> None:
    """Run scrapers in parallel in a background thread, pushing progress events."""
    global _scrape_running
    try:
        if landlords:
            instances = [catalog.get_scraper(s) for s in landlords]
        else:
            instances = catalog.get_all_scrapers()
        slugs = [s.slug for s in instances]

        out_dir = data_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        with _scrape_lock:
            _scrape_progress.append({"type": "start", "landlords": slugs})

        with ThreadPoolExecutor(max_workers=max(1, len(instances))) as executor:
            futures = {}
            start_times: dict[str, float] = {}
            for scraper in instances:
                start_times[scraper.slug] = time.monotonic()
                futures[executor.submit(_fetch_scraper, scraper, out_dir)] = scraper.slug

            for future in as_completed(futures):
                slug = futures[future]
                elapsed = round(time.monotonic() - start_times[slug], 1)
                try:
                    count = future.result()
                    event = {
                        "type": "scraper_done",
                        "slug": slug,
                        "success": True,
                        "count": count,
                        "elapsed": elapsed,
                    }
                except Exception as exc:
                    logger.warning("Scraper %s failed", slug, exc_info=True)
                    event = {
                        "type": "scraper_done",
                        "slug": slug,
                        "success": False,
                        "error": str(exc),
                        "elapsed": elapsed,
                    }
                with _scrape_lock:
                    _scrape_progress.append(event)

        new_count = _get_store().reload_after_scrape()
        with _scrape_lock:
            _scrape_progress.append({"type": "done", "new_count": new_count})
    except Exception as exc:
        logger.exception("Scrape failed")
        with _scrape_lock:
            _scrape_progress.append({"type": "done", "error": str(exc), "new_count": 0})
    finally:
        with _scrape_lock:
            _scrape_running = False


# ── helpers ─────────────────────────────────────────────────

def _json(content: dict, status: int = 200) -> Response:
    return Response(json.dumps(content), status=status, mimetype="application/json")


def _config_to_dict(cfg: Config) -> dict:
    return {
        "filters": {
            "rent": {"min": cfg.filters.rent.min, "max": cfg.filters.rent.max},
            "bedrooms": {"min": cfg.filters.bedrooms.min, "max": cfg.filters.bedrooms.max},
            "bathrooms": {"min": cfg.filters.bathrooms.min, "max": cfg.filters.bathrooms.max},
            "sqft": {"min": cfg.filters.sqft.min, "max": cfg.filters.sqft.max},
            "city": cfg.filters.city,
            "pets_allowed": cfg.filters.pets_allowed,
        },
        "preferences": [{"name": p.name, "tier": p.tier} for p in cfg.preferences],
        "waypoints": [
            {
                "name": wp.name,
                "lat": wp.lat,
                "lng": wp.lng,
                "tier": wp.tier,
                "modes": {m: {"max_minutes": mc.max_minutes} for m, mc in wp.modes.items()},
            }
            for wp in cfg.waypoints
        ],
    }


def _dict_to_config(body: dict) -> Config:
    f = body["filters"]

    def _range(name: str) -> RangeFilter:
        raw = f.get(name) or {}
        return RangeFilter(min=raw.get("min"), max=raw.get("max"))

    filters = Filters(
        rent=_range("rent"),
        bedrooms=_range("bedrooms"),
        bathrooms=_range("bathrooms"),
        sqft=_range("sqft"),
        city=f.get("city"),
        pets_allowed=f.get("pets_allowed"),
    )
    preferences = [
        Preference(name=p["name"], tier=p["tier"], points=TIER_POINTS[p["tier"]])
        for p in body.get("preferences", [])
    ]
    waypoints = [
        Waypoint(
            name=wp["name"],
            lat=wp["lat"],
            lng=wp["lng"],
            tier=wp["tier"],
            points=TIER_POINTS[wp["tier"]],
            modes={
                m: WaypointMode(max_minutes=mc["max_minutes"])
                for m, mc in wp.get("modes", {}).items()
            },
        )
        for wp in body.get("waypoints", [])
    ]
    return Config(filters=filters, preferences=preferences, waypoints=waypoints)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    init_app()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
