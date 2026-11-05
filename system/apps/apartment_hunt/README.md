# apartment-hunt

Aggregates San Francisco rental listings from many landlord websites into one
scored, ranked, map-based view. Ported from the standalone `apartmenthunt` tool.

Open it as the **Apartment Hunt** tab, or hit it directly at
`/service/apartment-hunt/`.

## What it does

1. **Scrapes** 18 landlord sources (AppFolio-based property managers, AvalonBay,
   Brookfield, Craigslist, and others) into per-source JSONL files.
2. **Deduplicates** listings by normalized address, merging partial records.
3. **Filters** on hard constraints (rent, bedrooms, bathrooms, sqft, city, pets).
4. **Scores** each listing against soft preferences (dishwasher, in-unit laundry,
   sqft, rent, ...) and travel time to your waypoints (walk preferred, transit
   discounted), with median imputation for missing data.
5. **Shows** a Leaflet map with rank-colored pins alongside a sortable card list.
   Tune filters/preferences/waypoints live, re-scrape on demand, and annotate
   listings (hide / contacted / toured / star rating / notes).

## Usage

- **Scrape All** pulls fresh listings from every source (Craigslist crawls detail
  pages and is the slowest, ~30-60s). New listings since the last scrape are
  flagged.
- **Config** edits the filters, preferences, and waypoints. **Apply changes**
  persists them and re-scores.

## Configuration & state

All state lives under `DATA_DIR` (`data/.apps/apartment-hunt/`, override with
`APARTMENT_HUNT_DATA_DIR`):

- `<slug>.jsonl` — raw scraped listings per source (the preserved raw records).
- `user_state.json` — your per-listing annotations.
- `preferences.yaml` — filters / preferences / waypoints, seeded on first run
  from `src/apartment_hunt/assets/preferences.yaml`.
- `cache/` — scraper detail-page and travel-time caches.

Optional: set `GOOGLE_MAPS_API_KEY` to compute real travel times via the Google
Routes API. Without it, the app falls back to straight-line (haversine) estimates.

## Adding a scraper

Create a module in `src/apartment_hunt/scrapers/`, implement the `Scraper`
protocol (`slug: str` and `fetch() -> list[Listing]`), decorate the class with
`@register`, and add an import for it to `scrapers/catalog.py`.

## Notes on sources

- The `equity` (Equity Residential) scraper is disabled — the site is behind
  Cloudflare Turnstile. The module is retained in case that changes.
- Individual scrapers can return zero listings when a site genuinely has no
  matching inventory or changes its markup; a failing scraper never blocks the
  others (each is isolated and its error is surfaced in the scrape progress).
