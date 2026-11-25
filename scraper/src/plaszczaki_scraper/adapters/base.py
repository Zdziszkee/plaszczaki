"""Adapter protocol and shared search-result types."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from plaszczaki_scraper.models import Listing, OfferType, PropertyType


class ParseError(RuntimeError):
    """Raised when a page does not match the adapter's expected structure."""


@dataclass(frozen=True, slots=True)
class Query:
    """One search URL to crawl."""

    key: str
    url: str
    region: str
    offer_type: OfferType
    property_type: PropertyType
    page: int = 1

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
