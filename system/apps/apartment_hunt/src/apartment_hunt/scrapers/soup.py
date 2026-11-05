"""Typed readers for BeautifulSoup tag attributes.

``Tag.get`` returns ``str | AttributeValueList | None``: bs4 parses the
multi-valued attributes (``class``, ``rel``, ...) into a list and every other
attribute into a plain string. These narrow the value to what the caller reads.
"""

from __future__ import annotations

from bs4 import Tag


def attr_text(tag: Tag, name: str) -> str | None:
    """A single-valued attribute (``href``, ``src``, ``alt``, ...), or None when absent."""
    value = tag.get(name)
    if isinstance(value, list):
        return " ".join(value)
    return value


def attr_classes(tag: Tag) -> list[str]:
    """The tag's CSS classes, empty when it has none."""
    value = tag.get("class")
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)
