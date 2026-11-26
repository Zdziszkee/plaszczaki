"""Adapter protocol and shared search-result types."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from plaszczaki_scraper.models import Listing, OfferType, PropertyType


def rotated(values: Sequence[str]) -> list[str]:
    """Rotate city lists hourly so a query budget cannot starve later cities."""
    items = list(values)
    if not items:
        return items
    offset = int(time.time() // 3600) % len(items)
    return items[offset:] + items[:offset]


class ParseError(RuntimeError):
    """Raised when a page does not match the adapter's expected structure."""


@dataclass(frozen=True, slots=True)
class Query:
    """One search URL (or API request) to crawl."""

    key: str
    url: str
    region: str
    offer_type: OfferType
    property_type: PropertyType
    page: int = 1
    method: str = "GET"
    body: str | None = None

    def with_page(self, page: int) -> Query:
        parts = urlsplit(self.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["page"] = str(page)
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment),
        )
        return replace(self, url=url, page=page)

    def with_url(self, url: str) -> Query:
        return replace(self, url=url)

    def with_param(self, name: str, value: str) -> Query:
        parts = urlsplit(self.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[name] = value
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment),
        )
        return replace(self, url=url)


@dataclass(slots=True)
class SearchPage:
    """Parsed search page."""

    items: list[Listing]
    current_page: int
    total_pages: int | None = None
    total_items: int | None = None


class Adapter(Protocol):
    """Contract every source adapter implements."""

    id: str

    def queries(self, regions: Sequence[str]) -> list[Query]: ...

    def with_page(self, query: Query, page: int) -> Query: ...

    def parse_search(self, html: str, query: Query) -> SearchPage: ...

    def parse_detail(self, html: str, summary: Listing) -> Listing: ...
