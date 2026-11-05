from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from apartment_hunt.config import Config
from apartment_hunt.dedup import deduplicate, normalize_address
from apartment_hunt.pipeline import DATA_DIR, apply_filters_scored, load_jsonl
from apartment_hunt.scoring import score_listings
from apartment_hunt.travel import TravelTimeCalculator

logger = logging.getLogger(__name__)


class Store:
    """In-memory data store backed by DuckDB.

    Loads JSONL listing data and a user-state JSON file, joins them via
    normalized address, and supports filtering, scoring, and annotation
    updates.  Scoring is done in Python; DuckDB manages the user-state
    table and the join.
    """

    def __init__(
        self,
        config: Config,
        data_dir: Path | None = None,
        travel_time_calculator: TravelTimeCalculator | None = None,
    ) -> None:
        self.data_dir = data_dir or DATA_DIR
        self.state_path = self.data_dir / "user_state.json"
        self.config = config
        self.travel_time_calculator = travel_time_calculator

        self.db = duckdb.connect()
        self._init_tables()
        self._load_user_state()
        self._load_and_score()

    # ── bootstrap ───────────────────────────────────────────────

    def _init_tables(self) -> None:
        self.db.execute("""
            CREATE TABLE user_state (
                normalized_address TEXT PRIMARY KEY,
                hidden BOOLEAN DEFAULT FALSE,
                contacted BOOLEAN DEFAULT FALSE,
                toured BOOLEAN DEFAULT FALSE,
                rating INTEGER,
                notes TEXT,
                first_seen TEXT
            )
        """)

    def _load_user_state(self) -> None:
        if not self.state_path.exists():
            return
        with open(self.state_path) as f:
            data = json.load(f)
        for norm_addr, state in data.items():
            self.db.execute(
                """
                INSERT INTO user_state
                    (normalized_address, hidden, contacted, toured, rating, notes, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (normalized_address) DO UPDATE SET
                    hidden = excluded.hidden,
                    contacted = excluded.contacted,
                    toured = excluded.toured,
                    rating = excluded.rating,
                    notes = excluded.notes,
                    first_seen = excluded.first_seen
                """,
                [
                    norm_addr,
                    state.get("hidden", False),
                    state.get("contacted", False),
                    state.get("toured", False),
                    state.get("rating"),
                    state.get("notes"),
                    state.get("first_seen"),
                ],
            )

    def _load_and_score(self) -> None:
        """Load JSONL, deduplicate, score all listings, register first_seen."""
        self._all_listings = deduplicate(load_jsonl(self.data_dir))
        self._all_scored = score_listings(
            self._all_listings, self.config, self.travel_time_calculator
        )
        self._register_first_seen()
        self._last_scrape_time: str | None = None

    def _register_first_seen(self) -> None:
        """Ensure every listing has a first_seen timestamp in user_state."""
        now = datetime.now(timezone.utc).isoformat()
        for listing in self._all_listings:
            norm = normalize_address(listing.address)
            row = self.db.execute(
                "SELECT first_seen FROM user_state WHERE normalized_address = ?",
                [norm],
            ).fetchone()
            if row is None:
                self.db.execute(
                    """
                    INSERT INTO user_state (normalized_address, first_seen)
                    VALUES (?, ?)
                    """,
                    [norm, now],
                )
        self._persist_state()

    # ── public API ──────────────────────────────────────────────

    def get_listings(self) -> list[dict]:
        """Return filtered+scored listings merged with user annotations.

        Each element is a dict with keys: listing, score, score_breakdown,
        waypoint_travel, and annotation fields (hidden, contacted, toured,
        rating, notes, first_seen, is_new).
        """
        filtered = apply_filters_scored(self._all_scored, self.config.filters)

        result: list[dict] = []
        for sl in filtered:
            norm = normalize_address(sl.listing.address)
            row = self.db.execute(
                """
                SELECT hidden, contacted, toured, rating, notes, first_seen
                FROM user_state WHERE normalized_address = ?
                """,
                [norm],
            ).fetchone()

            ann = {}
            if row:
                ann = {
                    "hidden": row[0] or False,
                    "contacted": row[1] or False,
                    "toured": row[2] or False,
                    "rating": row[3],
                    "notes": row[4] or "",
                    "first_seen": row[5],
                }
            else:
                ann = {
                    "hidden": False,
                    "contacted": False,
                    "toured": False,
                    "rating": None,
                    "notes": "",
                    "first_seen": None,
                }

            is_new = (
                self._last_scrape_time is not None
                and ann.get("first_seen") == self._last_scrape_time
            )

            entry = sl.model_dump(mode="json")
            entry["annotation"] = ann
            entry["is_new"] = is_new
            entry["normalized_address"] = norm
            result.append(entry)

        return result

    def update_annotation(
        self, normalized_address: str, field: str, value: object
    ) -> None:
        """Update a single annotation field and persist."""
        allowed = {"hidden", "contacted", "toured", "rating", "notes"}
        if field not in allowed:
            raise ValueError(f"Invalid annotation field: {field}")

        row = self.db.execute(
            "SELECT 1 FROM user_state WHERE normalized_address = ?",
            [normalized_address],
        ).fetchone()

        if row:
            self.db.execute(
                f"UPDATE user_state SET {field} = ? WHERE normalized_address = ?",
                [value, normalized_address],
            )
        else:
            self.db.execute(
                f"INSERT INTO user_state (normalized_address, {field}) VALUES (?, ?)",
                [normalized_address, value],
            )

        self._persist_state()

    def reload_after_scrape(self) -> int:
        """Reload JSONL data after a scrape, re-score, detect new listings.

        Returns the count of newly-discovered listings.
        """
        scrape_time = datetime.now(timezone.utc).isoformat()
        old_addresses = {normalize_address(ls.address) for ls in self._all_listings}

        self._all_listings = deduplicate(load_jsonl(self.data_dir))
        self._all_scored = score_listings(
            self._all_listings, self.config, self.travel_time_calculator
        )

        new_count = 0
        for listing in self._all_listings:
            norm = normalize_address(listing.address)
            if norm not in old_addresses:
                new_count += 1
                row = self.db.execute(
                    "SELECT first_seen FROM user_state WHERE normalized_address = ?",
                    [norm],
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO user_state (normalized_address, first_seen) VALUES (?, ?)",
                        [norm, scrape_time],
                    )
                else:
                    self.db.execute(
                        "UPDATE user_state SET first_seen = ? WHERE normalized_address = ?",
                        [scrape_time, norm],
                    )

        self._last_scrape_time = scrape_time
        self._persist_state()
        return new_count

    def get_new_listings_count(self) -> int:
        if self._last_scrape_time is None:
            return 0
        row = self.db.execute(
            "SELECT COUNT(*) FROM user_state WHERE first_seen = ?",
            [self._last_scrape_time],
        ).fetchone()
        return row[0] if row else 0

    def rescore(self, config: Config) -> None:
        """Re-score all listings with a new config (after Apply)."""
        self.config = config
        self._all_scored = score_listings(
            self._all_listings, self.config, self.travel_time_calculator
        )

    def dismiss_new(self) -> None:
        """Clear the new-listing indicators."""
        self._last_scrape_time = None

    # ── persistence ─────────────────────────────────────────────

    def _persist_state(self) -> None:
        """Write the full user_state table back to JSON."""
        rows = self.db.execute(
            """
            SELECT normalized_address, hidden, contacted, toured,
                   rating, notes, first_seen
            FROM user_state
            """
        ).fetchall()
        data: dict[str, dict] = {}
        for row in rows:
            entry: dict = {}
            if row[1]:
                entry["hidden"] = True
            if row[2]:
                entry["contacted"] = True
            if row[3]:
                entry["toured"] = True
            if row[4] is not None:
                entry["rating"] = row[4]
            if row[5]:
                entry["notes"] = row[5]
            if row[6]:
                entry["first_seen"] = row[6]
            if entry:
                data[row[0]] = entry
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(data, f, indent=2)
