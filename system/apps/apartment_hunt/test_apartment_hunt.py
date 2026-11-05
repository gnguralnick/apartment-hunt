"""Integration tests for the Apartment Hunt Flask server.

Drives the real routes against a temp data dir seeded with listing JSONL, so
the store/pipeline/scoring stack runs end to end (no network -- scraping is not
exercised here). The travel-time calculator falls back to haversine.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from apartment_hunt import runner
from flask.testing import FlaskClient


def _seed_listing(**kwargs) -> dict:
    base = {
        "source": "test",
        "address": "123 Test St",
        "city": "San Francisco",
        "rent": 3000.0,
        "bedrooms": 1,
        "sqft": 700.0,
        "lat": 37.79,
        "lng": -122.40,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("APARTMENT_HUNT_DATA_DIR", str(data_dir))

    # Seed two SF one-bedroom listings that pass the default filters, plus one
    # that should be filtered out (studio / 0 bedrooms).
    listings = [
        _seed_listing(address="123 Test St", rent=3000.0),
        _seed_listing(address="456 Oak Ave", rent=3500.0, lat=37.78, lng=-122.41),
        _seed_listing(address="999 Studio Ln", bedrooms=0, sqft=300.0),
    ]
    with open(data_dir / "test.jsonl", "w") as f:
        for ls in listings:
            f.write(json.dumps(ls) + "\n")

    runner.init_app()
    runner.app.config.update(TESTING=True)
    yield runner.app.test_client()


def test_index_serves_frontend(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Apartment Hunt" in resp.data


def test_static_asset_served(client: FlaskClient) -> None:
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["Content-Type"]


def test_static_asset_traversal_rejected(client: FlaskClient) -> None:
    resp = client.get("/static/../runner.py")
    assert resp.status_code == 404


def test_listings_are_filtered_and_scored(client: FlaskClient) -> None:
    resp = client.get("/api/listings")
    assert resp.status_code == 200
    data = resp.get_json()
    addresses = {item["listing"]["address"] for item in data["listings"]}
    # The two 1-bedroom SF listings pass; the 0-bedroom studio is filtered out.
    assert addresses == {"123 Test St", "456 Oak Ave"}
    for item in data["listings"]:
        assert "score" in item
        assert "score_breakdown" in item
        assert "normalized_address" in item


def test_config_roundtrip(client: FlaskClient) -> None:
    original = client.get("/api/config").get_json()
    assert original["filters"]["city"] == "San Francisco"

    original["filters"]["rent"]["max"] = 3200
    put = client.put("/api/config", json=original)
    assert put.status_code == 200
    assert client.post("/api/config/apply").status_code == 200

    # The tighter rent cap now excludes the $3500 listing.
    data = client.get("/api/listings").get_json()
    addresses = {item["listing"]["address"] for item in data["listings"]}
    assert addresses == {"123 Test St"}


def test_annotation_persists(client: FlaskClient) -> None:
    listings = client.get("/api/listings").get_json()["listings"]
    norm = listings[0]["normalized_address"]

    resp = client.patch(
        "/api/listings/state",
        json={"normalized_address": norm, "field": "contacted", "value": True},
    )
    assert resp.status_code == 200

    updated = client.get("/api/listings").get_json()["listings"]
    match = next(i for i in updated if i["normalized_address"] == norm)
    assert match["annotation"]["contacted"] is True


def test_dismiss_new(client: FlaskClient) -> None:
    assert client.post("/api/dismiss-new").status_code == 200
    assert client.get("/api/listings").get_json()["new_count"] == 0
