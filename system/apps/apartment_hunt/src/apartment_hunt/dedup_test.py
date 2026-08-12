from apartment_hunt.dedup import deduplicate, normalize_address
from apartment_hunt.models import Listing


class TestNormalizeAddress:
    def test_lowercases_and_strips(self):
        assert normalize_address("  123 Main ST  ") == "123 main street"

    def test_expands_abbreviations(self):
        assert normalize_address("456 Oak Ave") == "456 oak avenue"
        assert normalize_address("789 Pine Blvd") == "789 pine boulevard"

    def test_strips_punctuation(self):
        assert normalize_address("123 Main St., Apt. 4") == "123 main street apartment 4"

    def test_collapses_whitespace(self):
        assert normalize_address("123   Main    St") == "123 main street"

    def test_mixed_case_abbreviations(self):
        assert normalize_address("100 ELM DR") == "100 elm drive"


class TestDeduplicate:
    def test_no_duplicates_preserves_all(self):
        listings = [
            Listing(source="a", address="123 Main St", lat=37.79, lng=-122.40),
            Listing(source="a", address="456 Oak Ave", lat=37.78, lng=-122.41),
        ]
        result = deduplicate(listings)
        assert len(result) == 2

    def test_merges_duplicates_by_normalized_address(self):
        a = Listing(source="a", address="123 Main St", lat=37.79, lng=-122.40, rent=3000)
        b = Listing(source="b", address="123 Main Street", lat=37.79, lng=-122.40, bedrooms=2)
        result = deduplicate([a, b])
        assert len(result) == 1
        merged = result[0]
        assert merged.rent == 3000
        assert merged.bedrooms == 2
        assert set(merged.sources) == {"a", "b"}

    def test_prefers_source_with_more_fields(self):
        # a has more fields, so its rent value should win
        a = Listing(source="a", address="100 Elm Dr", lat=37.79, lng=-122.40, rent=2500, bedrooms=1, sqft=600)
        b = Listing(source="b", address="100 Elm Drive", lat=37.79, lng=-122.40, rent=2700)
        result = deduplicate([a, b])
        assert len(result) == 1
        assert result[0].rent == 2500

    def test_fills_gaps_from_secondary(self):
        a = Listing(source="a", address="200 Pine Rd", lat=37.79, lng=-122.40, rent=3000)
        b = Listing(source="b", address="200 Pine Road", lat=37.79, lng=-122.40, neighborhood="Mission")
        result = deduplicate([a, b])
        merged = result[0]
        assert merged.rent == 3000
        assert merged.neighborhood == "Mission"

    def test_three_way_merge(self):
        a = Listing(source="a", address="300 Oak St", lat=37.79, lng=-122.40, rent=2800)
        b = Listing(source="b", address="300 Oak Street", lat=37.79, lng=-122.40, bedrooms=2)
        c = Listing(source="c", address="300 Oak St.", lat=37.79, lng=-122.40, sqft=800)
        result = deduplicate([a, b, c])
        assert len(result) == 1
        merged = result[0]
        assert merged.rent == 2800
        assert merged.bedrooms == 2
        assert merged.sqft == 800
        assert len(merged.sources) == 3
