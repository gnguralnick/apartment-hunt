---
title: "Apartment Hunt"
description: "Aggregate, filter, score, and map SF rental listings from many landlord sites"
thumbnail: "template.svg"
version: v1
format: v2
---

# Apartment Hunt

This file is the manifest for the **Apartment Hunt** template (slug:
`apartment-hunt`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Aggregate, filter, score, and map SF rental listings from many landlord sites

Apartment Hunt turns the scattered, one-site-at-a-time grind of finding a rental
into a single ranked view. It scrapes rental listings from ~18 San Francisco
landlord and property-manager websites, merges duplicates that appear on more
than one site, throws out anything that fails your hard limits (rent, bedrooms,
bathrooms, square footage, city, pets), and scores what remains against your
soft preferences (dishwasher, in-unit laundry, more space, lower rent) and how
long it takes to get from each place to the locations you care about
("waypoints" -- work, a park, a friend's). What you open is a web page with a
map of San Francisco on one side, a numbered pin on every listing colored by
rank, and a sortable list of listing cards on the other. You can retune the
filters, preferences, and waypoints live and re-rank instantly, kick off a
fresh scrape on demand, and mark listings up as you go (hide, contacted, toured,
a star rating, free-text notes) -- all of which persists between sessions.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/apartment_hunt`

- `system/apps/apartment_hunt` -- the whole app: a Flask package
  (`src/apartment_hunt/`) with the web server (`runner.py`), the scraper
  package (`scrapers/`, one module per landlord source plus a shared AppFolio
  reader and a `catalog.py` that registers them), the ranking core
  (`pipeline.py`, `dedup.py`, `scoring.py`, `travel.py`, `store.py`,
  `config.py`, `models.py`), the browser frontend and the seed
  `preferences.yaml` under `src/apartment_hunt/assets/`, plus its own tests and
  README.

At runtime a supervisord program named `apartment-hunt` (in
`system/supervisord.conf`) runs `uv run apartment-hunt`, which starts the Flask
app on port 8088 and registers it with the workspace UI via
`system/scripts/forward_port.py`, so it shows up as a tab served at
`/service/apartment-hunt/`. The root `pyproject.toml` lists `apartment-hunt` as
a workspace member and dependency so `uv run apartment-hunt` resolves. All
persistent state -- the per-source scraped listings (JSONL), your annotations,
your editable `preferences.yaml`, and the scraper/travel caches -- lives under
`data/.apps/apartment-hunt/` (override with `APARTMENT_HUNT_DATA_DIR`), which is
gitignored. Each scraper self-registers via an `@register` decorator, so adding
a source is a new module plus one import line in `scrapers/catalog.py`.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

**Activation:** none. Every scraper fetches public, unauthenticated web pages,
so there are no permissions to grant and no secrets to set -- the app runs as
published. It also makes no LLM/Claude calls, so there is nothing to wire up
there either.

**Adaptation** (worked through interactively; none block startup):

- **It is San Francisco-specific.** Every scraper targets a particular SF
  landlord or the Craigslist SF region, and the seed `preferences.yaml` uses SF
  filters and waypoints. An adopter hunting in another city must repoint the
  scrapers at their own market's sources and rewrite the preferences; the
  scraper framework (the `@register` pattern in `scrapers/catalog.py`) is
  reusable, but the individual site parsers are not portable as-is.
- **Optional Google travel times.** If the `GOOGLE_MAPS_API_KEY` environment
  variable is set, waypoint scoring uses real walking/transit times from the
  Google Routes API; if it is unset (the default), the app falls back to
  straight-line (haversine) estimates. Purely an enhancement -- nothing breaks
  without it.
- **The sample waypoints are placeholders.** The shipped preferences include
  two generic SF waypoints ("Downtown SF", "Golden Gate Park") purely so the
  map and scoring demo works out of the box. Replace them with the places you
  actually care about.
- **One source is disabled.** The Equity Residential (`equity`) scraper is
  present but not registered, because that site sits behind a Cloudflare
  interactive challenge that a plain HTTP fetch cannot clear. Individual
  scrapers can also return zero listings when a site changes its markup -- that
  is normal for an aggregator and does not affect the others.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. Every dependency
(Flask, httpx, BeautifulSoup, lxml, pydantic, PyYAML, DuckDB) is a Python wheel
installed from the app's own `pyproject.toml` by `uv`; the app shells out to no
external binaries and needs no system packages, npm, or other global tools.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-08-12) -- Initial publish of the Apartment Hunt SF rental aggregator: ~18 landlord scrapers, dedup, filtering, preference + travel-time scoring, and the interactive map dashboard.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
