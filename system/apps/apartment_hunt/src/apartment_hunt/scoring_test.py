from apartment_hunt.config import Config, Filters, Preference, Waypoint, WaypointMode
from apartment_hunt.models import Listing
from apartment_hunt.scoring import TRANSIT_DISCOUNT, score_listings
from apartment_hunt.travel import HaversineTravelTimeCalculator


def _make_config(
    preferences: list[Preference] | None = None,
    waypoints: list[Waypoint] | None = None,
) -> Config:
    return Config(
        filters=Filters(),
        preferences=preferences or [],
        waypoints=waypoints or [],
    )


def _make_listing(**kwargs) -> Listing:
    defaults = {"source": "test", "address": "123 Test St", "lat": 37.79, "lng": -122.40}
    defaults.update(kwargs)
    return Listing(**defaults)


class _FakeTravelTimeCalculator:
    """Returns pre-configured travel times for testing."""

    def __init__(self, times: dict[str, list[float | None]]):
        """times: mapping from mode ("walking"/"transit") to list of minutes per origin."""
        self._times = times

    def get_travel_times(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
        mode: str,
    ) -> list[float | None]:
        return self._times.get(mode, [None] * len(origins))


class TestBooleanScoring:
    def test_dishwasher_present_gets_full_points(self):
        config = _make_config(preferences=[Preference(name="dishwasher", tier="strong", points=10)])
        listings = [_make_listing(dishwasher=True)]
        result = score_listings(listings, config)
        assert result[0].score_breakdown["dishwasher"] == 10.0

    def test_dishwasher_absent_gets_zero(self):
        config = _make_config(preferences=[Preference(name="dishwasher", tier="strong", points=10)])
        listings = [_make_listing(dishwasher=False)]
        result = score_listings(listings, config)
        assert result[0].score_breakdown["dishwasher"] == 0.0

    def test_in_unit_laundry_checks_laundry_field(self):
        config = _make_config(
            preferences=[Preference(name="in_unit_laundry", tier="strong", points=10)]
        )
        in_unit = _make_listing(laundry="in-unit")
        shared = _make_listing(laundry="shared", address="456 Test St")
        result = score_listings([in_unit, shared], config)
        scores = {r.listing.address: r.score_breakdown["in_unit_laundry"] for r in result}
        assert scores["123 Test St"] == 10.0
        assert scores["456 Test St"] == 0.0

    def test_missing_field_gets_median(self):
        config = _make_config(preferences=[Preference(name="dishwasher", tier="strong", points=10)])
        listings = [
            _make_listing(dishwasher=True),
            _make_listing(dishwasher=False, address="456 Test St"),
            _make_listing(dishwasher=None, address="789 Test St"),
        ]
        result = score_listings(listings, config)
        scores = {r.listing.address: r.score_breakdown["dishwasher"] for r in result}
        # Median of [10.0, 0.0] = 5.0
        assert scores["789 Test St"] == 5.0


class TestContinuousScoring:
    def test_sqft_linear_scaling(self):
        config = _make_config(preferences=[Preference(name="sqft", tier="moderate", points=5)])
        listings = [
            _make_listing(sqft=500),
            _make_listing(sqft=1000, address="456 Test St"),
            _make_listing(sqft=750, address="789 Test St"),
        ]
        result = score_listings(listings, config)
        scores = {r.listing.address: r.score_breakdown["sqft"] for r in result}
        assert scores["123 Test St"] == 0.0  # lowest
        assert scores["456 Test St"] == 5.0  # highest
        assert scores["789 Test St"] == 2.5  # midpoint

    def test_rent_inverted_scaling(self):
        """Lower rent should score higher."""
        config = _make_config(preferences=[Preference(name="rent", tier="moderate", points=5)])
        listings = [
            _make_listing(rent=2000),
            _make_listing(rent=4000, address="456 Test St"),
        ]
        result = score_listings(listings, config)
        scores = {r.listing.address: r.score_breakdown["rent"] for r in result}
        assert scores["123 Test St"] == 5.0  # lower rent = more points
        assert scores["456 Test St"] == 0.0  # higher rent = fewer points

    def test_all_same_value_gets_full_points(self):
        config = _make_config(preferences=[Preference(name="sqft", tier="moderate", points=5)])
        listings = [
            _make_listing(sqft=700),
            _make_listing(sqft=700, address="456 Test St"),
        ]
        result = score_listings(listings, config)
        assert all(r.score_breakdown["sqft"] == 5.0 for r in result)

    def test_missing_sqft_gets_median(self):
        config = _make_config(preferences=[Preference(name="sqft", tier="moderate", points=5)])
        listings = [
            _make_listing(sqft=500),
            _make_listing(sqft=1000, address="456 Test St"),
            _make_listing(sqft=None, address="789 Test St"),
        ]
        result = score_listings(listings, config)
        scores = {r.listing.address: r.score_breakdown["sqft"] for r in result}
        # Median of [0.0, 5.0] = 2.5
        assert scores["789 Test St"] == 2.5


class TestWaypointScoring:
    """Tests for travel-time-based waypoint scoring."""

    def test_walk_within_threshold_scores_full_tier(self):
        """A listing at 0 travel time should get full tier points."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"walk": WaypointMode(max_minutes=30)},
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.40)]
        calc = _FakeTravelTimeCalculator(times={"walking": [0.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        assert result[0].score_breakdown["waypoint:Work"] == 10.0

    def test_walk_over_threshold_scores_zero(self):
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"walk": WaypointMode(max_minutes=30)},
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.90, lng=-122.50)]
        calc = _FakeTravelTimeCalculator(times={"walking": [45.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        assert result[0].score_breakdown["waypoint:Work"] == 0.0

    def test_walk_linear_decay(self):
        """15min out of 30min max should give half the points."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"walk": WaypointMode(max_minutes=30)},
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        calc = _FakeTravelTimeCalculator(times={"walking": [15.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        assert result[0].score_breakdown["waypoint:Work"] == 5.0

    def test_transit_discounted(self):
        """Transit scores should be discounted by TRANSIT_DISCOUNT."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"transit": WaypointMode(max_minutes=45)},
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        calc = _FakeTravelTimeCalculator(times={"transit": [0.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        assert result[0].score_breakdown["waypoint:Work"] == 10.0 * TRANSIT_DISCOUNT

    def test_walk_preferred_over_transit(self):
        """When both walk and transit are available and within threshold,
        walk should score higher because transit is discounted."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={
                "walk": WaypointMode(max_minutes=30),
                "transit": WaypointMode(max_minutes=45),
            },
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        # Same travel time for both modes — walk should win due to discount on transit
        calc = _FakeTravelTimeCalculator(times={"walking": [15.0], "transit": [15.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        walk_score = (1.0 - 15.0 / 30) * 10  # 5.0
        transit_score = (1.0 - 15.0 / 45) * 10 * TRANSIT_DISCOUNT  # 3.33
        assert result[0].score_breakdown["waypoint:Work"] == walk_score
        assert walk_score > transit_score

    def test_transit_fallback_when_walk_over_threshold(self):
        """Transit is used when walking exceeds its threshold."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={
                "walk": WaypointMode(max_minutes=30),
                "transit": WaypointMode(max_minutes=45),
            },
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.80, lng=-122.42)]
        # Walk too far, transit within threshold
        calc = _FakeTravelTimeCalculator(times={"walking": [40.0], "transit": [20.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        expected = (1.0 - 20.0 / 45) * 10 * TRANSIT_DISCOUNT
        assert abs(result[0].score_breakdown["waypoint:Work"] - expected) < 0.01

    def test_waypoint_travel_details_recorded(self):
        """ScoredListing should contain travel time details for every declared mode."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"walk": WaypointMode(max_minutes=30)},
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        calc = _FakeTravelTimeCalculator(times={"walking": [12.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        assert "Work" in result[0].waypoint_travel
        details = result[0].waypoint_travel["Work"]
        assert len(details) == 1
        assert details[0].mode == "walk"
        assert details[0].minutes == 12.0

    def test_waypoint_travel_records_all_declared_modes(self):
        """Travel time should be recorded for every declared mode with a known time,
        not only the best-scoring one, so the UI can display both."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={
                "walk": WaypointMode(max_minutes=30),
                "transit": WaypointMode(max_minutes=45),
            },
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        calc = _FakeTravelTimeCalculator(times={"walking": [12.0], "transit": [20.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        details = result[0].waypoint_travel["Work"]
        by_mode = {d.mode: d.minutes for d in details}
        assert by_mode == {"walk": 12.0, "transit": 20.0}

    def test_waypoint_travel_declared_order_preserved(self):
        """Travel detail list should follow config declaration order."""
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={
                "transit": WaypointMode(max_minutes=45),
                "walk": WaypointMode(max_minutes=30),
            },
        )
        config = _make_config(waypoints=[wp])
        listings = [_make_listing(lat=37.79, lng=-122.41)]
        calc = _FakeTravelTimeCalculator(times={"walking": [12.0], "transit": [20.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        details = result[0].waypoint_travel["Work"]
        assert [d.mode for d in details] == ["transit", "walk"]

    def test_two_listings_different_travel_times(self):
        wp = Waypoint(
            name="Work", lat=37.79, lng=-122.40, tier="strong", points=10,
            modes={"walk": WaypointMode(max_minutes=30)},
        )
        config = _make_config(waypoints=[wp])
        listings = [
            _make_listing(lat=37.79, lng=-122.40),
            _make_listing(lat=37.80, lng=-122.42, address="456 Test St"),
        ]
        calc = _FakeTravelTimeCalculator(times={"walking": [0.0, 20.0]})
        result = score_listings(listings, config, travel_time_calculator=calc)
        scores = {r.listing.address: r.score_breakdown["waypoint:Work"] for r in result}
        assert scores["123 Test St"] == 10.0
        assert abs(scores["456 Test St"] - (1.0 - 20.0 / 30) * 10) < 0.01


class TestHaversineTravelTimeCalculator:
    def test_same_point_is_zero(self):
        calc = HaversineTravelTimeCalculator()
        times = calc.get_travel_times([(37.79, -122.40)], (37.79, -122.40), "walking")
        assert times[0] == 0.0

    def test_walking_slower_than_transit(self):
        calc = HaversineTravelTimeCalculator()
        walk = calc.get_travel_times([(37.79, -122.40)], (37.77, -122.42), "walking")
        transit = calc.get_travel_times([(37.79, -122.40)], (37.77, -122.42), "transit")
        assert walk[0] > transit[0]

    def test_returns_one_result_per_origin(self):
        calc = HaversineTravelTimeCalculator()
        origins = [(37.79, -122.40), (37.80, -122.41), (37.81, -122.42)]
        times = calc.get_travel_times(origins, (37.79, -122.40), "walking")
        assert len(times) == 3


class TestScoreSorting:
    def test_sorted_by_score_descending(self):
        config = _make_config(preferences=[Preference(name="dishwasher", tier="strong", points=10)])
        listings = [
            _make_listing(dishwasher=False),
            _make_listing(dishwasher=True, address="456 Test St"),
        ]
        result = score_listings(listings, config)
        assert result[0].listing.address == "456 Test St"
        assert result[0].score > result[1].score

    def test_empty_listings_returns_empty(self):
        config = _make_config()
        assert score_listings([], config) == []
