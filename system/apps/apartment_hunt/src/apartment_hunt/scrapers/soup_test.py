from bs4 import BeautifulSoup, Tag

from apartment_hunt.scrapers.soup import attr_classes, attr_text


def _tag(html: str) -> Tag:
    tag = BeautifulSoup(html, "html.parser").find()
    assert isinstance(tag, Tag)
    return tag


def test_attr_text_reads_a_single_valued_attribute() -> None:
    assert attr_text(_tag('<a href="/rental/12">x</a>'), "href") == "/rental/12"


def test_attr_text_is_none_when_the_attribute_is_absent() -> None:
    assert attr_text(_tag("<img>"), "src") is None


def test_attr_text_joins_a_multi_valued_attribute() -> None:
    assert attr_text(_tag('<div class="card location-mission"></div>'), "class") == "card location-mission"


def test_attr_classes_lists_each_class() -> None:
    assert attr_classes(_tag('<li class="cat dog"></li>')) == ["cat", "dog"]


def test_attr_classes_is_empty_without_a_class() -> None:
    assert attr_classes(_tag("<li></li>")) == []
