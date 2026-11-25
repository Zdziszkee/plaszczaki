"""Gdom adapter (search pages + detail pages).

Gdom runs HivePress on WordPress and server-renders everything: listing cards
(``article.hp-listing--view-block``) on category pages and a single listing block
(``div.hp-listing--view-page``) on detail pages.  Coordinates are embedded as
``data-markers`` JSON on the detail page and as a Google Maps link on every
search card, so no JavaScript execution is needed.

Gdom is a nationwide portal without a server-side region filter, therefore
queries crawl whole categories and the runner resolves each listing's
voivodeship from the rendered address (``województwo <name>``) or coordinates.

``https://gdom.pl/robots.txt`` allows crawling but advertises
``Crawl-delay: 1200`` (20 minutes per request).  The shared
:class:`~plaszczaki_scraper.http.HttpClient` honours that delay, which makes a
full run impractically slow; the adapter is kept for completeness.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_floor,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://gdom.pl"

CATEGORIES: tuple[tuple[str, OfferType, PropertyType], ...] = (
    ("mieszkania-na-sprzedaz", "sale", "flat"),
    ("mieszkania-na-wynajem", "rent", "flat"),
    ("domy-na-sprzedaz", "sale", "house"),
    ("domy-na-wynajem", "rent", "house"),
    ("dzialki-na-sprzedaz", "sale", "plot"),
    ("lokale-uzytkowe-na-sprzedaz", "sale", "commercial"),
    ("lokale-uzytkowe-na-wynajem", "rent", "commercial"),
    ("hale-na-sprzedaz", "sale", "commercial"),
    ("hale-na-wynajem", "rent", "commercial"),
    ("magazyny-na-sprzedaz", "sale", "commercial"),
    ("magazyny-na-wynajem", "rent", "commercial"),
    ("garaze-na-sprzedaz", "sale", "garage"),
    ("garaze-na-wynajem", "rent", "garage"),
    ("pokoje-na-wynajem", "rent", "room"),
)

_CATEGORY_TYPES: dict[str, PropertyType] = {
    "mieszkania": "flat",
    "domy": "house",
    "dzialki": "plot",
    "lokale uzytkowe": "commercial",
    "hale": "commercial",
    "magazyny": "commercial",
    "garaze": "garage",
    "pokoje": "room",
}

_TRANSACTION_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "wynajem": "rent",
    "najem": "rent",
}

_SELLER_TYPES: dict[str, str] = {
    "wlasciciela": "private",
    "dewelopera": "developer",
    "posrednika": "agency",
    "posrednika 0 prowizji": "agency",
}

_POSTAL_RE = re.compile(r"^\d{2}-\d{3}\s+(.+)$")
_PROVINCE_RE = re.compile(r"^wojew[oó]dztwo\s+(.+)$", re.IGNORECASE)
_COUNTY_RE = re.compile(r"^powiat\s+(.+)$", re.IGNORECASE)
_COORDS_RE = re.compile(r"query=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_HOUSE_NUMBER_RE = re.compile(r"\d")


def _squash(value: str | None) -> str:
    return " ".join(value.split()) if value else ""


def _tree(html: str) -> lxml_html.HtmlElement:
    if not html.strip():
        raise ParseError("Gdom page is empty")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Gdom page is not valid HTML: {exc}") from exc


def _attribute(node: lxml_html.HtmlElement) -> tuple[str | None, str]:
    """Return the semantic suffix of a ``hp-listing__attribute`` element."""
    for value in (node.get("class") or "").split():
        if value.startswith("hp-listing__attribute--"):
            return value.removeprefix("hp-listing__attribute--"), _squash(node.text_content())
    return None, _squash(node.text_content())


def _after_label(text: str, label: str) -> str:
    normalized = normalize_text(text)
    prefix = normalize_text(label)
    if normalized.startswith(prefix):
        return text[len(label) :].lstrip(": ").strip()
    return text


class GdomAdapter:
    """Parse Gdom category and offer pages."""

    id = "gdom"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # Gdom serves nationwide category pages only, so each category is crawled
        # once regardless of how many regions were requested. With the 20-minute
        # robots delay only the first query runs per pass, so rotate the order
        # daily to cover every category over time.
        region = next((item for item in regions if self.regions.has_region(item)), None)
        if region is None:
            return []
        offset = date.today().toordinal() % len(CATEGORIES)
        categories = (*CATEGORIES[offset:], *CATEGORIES[:offset])
        return [
            Query(
                key=f"{region}:{category}",
                url=f"{BASE_URL}/kategoria-ogloszen/{category}/",
                region=region,
                offer_type=offer_type,
                property_type=property_type,
            )
            for category, offer_type, property_type in categories
        ]

    def newest(self, query: Query) -> Query:
        """The portal's default category list is already newest-first."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=1)
        parts = urlsplit(query.url)
        path = parts.path.rstrip("/")
        return replace(query, url=f"{BASE_URL}{path}/page/{page}/", page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        cards = tree.xpath('//article[contains(@class, "hp-listing--view-block")]')
        if not cards and not tree.xpath('//div[contains(@class, "hp-grid")]'):
            raise ParseError("Gdom category page has no listing grid")

        items = [self._summary(card, query) for card in cards]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=self._total_pages(tree),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        scope = tree.xpath('//div[contains(@class, "hp-listing--view-page")]')
        if not scope:
            raise ParseError("Gdom offer page is missing the listing block")
        scope = scope[0]

        attributes = self._attributes(scope)
        location = self._first_text(scope.xpath('.//div[contains(@class, "hp-listing__location")]'))
        address = self._address(location)

        lat, lng = self._marker(scope)
        offer_type = (
            _TRANSACTION_TYPES.get(
                normalize_text(
                    _after_label(attributes.get("typ-transakcji-2", ""), "Typ transakcji")
                ),
            )
            or _TRANSACTION_TYPES.get(
                normalize_text(
                    _after_label(attributes.get("typ-transakcji", ""), "Typ transakcji")
                ),
            )
            or summary.offer_type
        )
        property_type = self._property_type(scope) or summary.property_type

        price = parse_price(_after_label(attributes.get("cena", ""), "Cena"))
        if price is None:
            price = summary.price_pln

        area = parse_area(attributes.get("powierzchnia"))
        land_area = parse_area(attributes.get("dzialka"))
        if property_type == "plot":
            land_area, area = land_area or area, None

        seller_type = self._seller(attributes.get("dodane-przez"))
        floor, floor_label = parse_floor(_after_label(attributes.get("pietro", ""), "Piętro"))

        extra = dict(summary.extra)
        extra["gdom"] = {
            "attributes": attributes,
            "category": self._first_text(
                scope.xpath('.//div[contains(@class, "hp-listing__category")]//a'),
            ),
        }

        return replace(
            summary,
            title=self._first_text(scope.xpath(".//h1")) or summary.title,
            description=self._description(scope) or summary.description,
            offer_type=offer_type,
            property_type=property_type,
            market="primary" if seller_type == "developer" else summary.market,
            price_kind="asking" if price is not None else summary.price_kind,
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(attributes.get("liczba-pokoi")) or summary.rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(attributes.get("rok-budowy")),
            voivodeship=address["voivodeship"] or summary.voivodeship,
            county=address["county"],
            city=address["city"] or summary.city,
            street=address["street"] or summary.street,
            address_text=location or summary.address_text,
            lat=lat if lat is not None else summary.lat,
            lng=lng if lng is not None else summary.lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=self._images(scope) or summary.images,
            seller_type=seller_type,
            seller_name=None,
            published_at=parse_datetime_ms(
                self._first_text(scope.xpath(".//time/@datetime")),
            )
            or summary.published_at,
            modified_at=parse_datetime_ms(
                self._first_text(tree.xpath('.//meta[@property="article:modified_time"]/@content')),
            ),
            extra=extra,
        )

    # -- search -----------------------------------------------------------

    def _summary(self, card: lxml_html.HtmlElement, query: Query) -> Listing:
        href = self._first_text(card.xpath('.//h4[contains(@class, "hp-listing__title")]//a/@href'))
        if not href:
            raise ParseError("Gdom listing card is missing its link")
        if href.startswith("/"):
            href = f"{BASE_URL}{href}"
        external_id = urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1]
        if not external_id:
            raise ParseError(f"Gdom listing card {href} has no slug")

        location = self._first_text(card.xpath('.//div[contains(@class, "hp-listing__location")]'))
        address = self._address(location)
        lat, lng = self._coords_from_link(card)

        price = None
        for node in card.xpath('.//div[contains(@class, "hp-listing__attribute--cena")]'):
            price = parse_price(_after_label(_squash(node.text_content()), "Cena"))
            break

        image = self._first_text(
            card.xpath(".//img[@data-src]/@data-src") or card.xpath(".//img/@src"),
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=self._first_text(card.xpath('.//h4[contains(@class, "hp-listing__title")]//a')),
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="asking" if price is not None else "unknown",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            voivodeship=address["voivodeship"],
            county=address["county"],
            city=address["city"],
            street=address["street"],
            address_text=location,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=[image] if image else [],
            published_at=parse_datetime_ms(self._first_text(card.xpath(".//time/@datetime"))),
            extra={"gdom": {"category": query.key.rsplit(":", 1)[-1]}},
        )

    @staticmethod
    def _total_pages(tree: lxml_html.HtmlElement) -> int | None:
        pages = [
            safe_int(match.group(1))
            for href in tree.xpath('//div[contains(@class, "hp-pagination")]//a/@href')
            if (match := re.search(r"/page/(\d+)/", href)) is not None
        ]
        numbers = [page for page in pages if page is not None and page > 0]
        return max(numbers) if numbers else None

    # -- detail -----------------------------------------------------------

    @staticmethod
    def _attributes(scope: lxml_html.HtmlElement) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for node in scope.xpath('.//div[contains(@class, "hp-listing__attribute--")]'):
            suffix, text = _attribute(node)
            if suffix and text and suffix not in attributes:
                attributes[suffix] = text
        return attributes

    def _description(self, scope: lxml_html.HtmlElement) -> str | None:
        text = self._first_text(scope.xpath('.//div[contains(@class, "hp-listing__description")]'))
        return strip_html(text)

    @staticmethod
    def _marker(scope: lxml_html.HtmlElement) -> tuple[float | None, float | None]:
        raw = scope.xpath(".//*[@data-markers]/@data-markers")
        if not raw:
            return None, None
        try:
            markers = json.loads(raw[0])
        except json.JSONDecodeError, TypeError:
            return None, None
        if not isinstance(markers, list) or not markers:
            return None, None
        marker = markers[0] if isinstance(markers[0], dict) else {}
        return safe_float(marker.get("latitude")), safe_float(marker.get("longitude"))

    @staticmethod
    def _coords_from_link(card: lxml_html.HtmlElement) -> tuple[float | None, float | None]:
        links = card.xpath('.//a[contains(@href, "google.com/maps")]/@href')
        match = _COORDS_RE.search(links[0]) if links else None
        if match is None:
            return None, None
        return safe_float(match.group(1)), safe_float(match.group(2))

    def _address(self, value: str | None) -> dict[str, str | None]:
        result: dict[str, str | None] = {
            "voivodeship": None,
            "county": None,
            "city": None,
            "street": None,
        }
        if not value:
            return result
        for part in (item.strip() for item in value.split(",")):
            if not part:
                continue
            normalized = normalize_text(part)
            postal = _POSTAL_RE.match(part)
            province = _PROVINCE_RE.match(part)
            county = _COUNTY_RE.match(part)
            if postal is not None:
                result["city"] = postal.group(1).strip()
            elif province is not None:
                result["voivodeship"] = self.regions.canonical_voivodeship(province.group(1))
            elif county is not None:
                result["county"] = county.group(1).strip()
            elif normalized == "polska":
                continue
            elif self.regions.canonical_voivodeship(part) is not None:
                result["voivodeship"] = self.regions.canonical_voivodeship(part)
            elif result["city"] is None and not _HOUSE_NUMBER_RE.search(part):
                result["city"] = part
            elif result["street"] is None:
                result["street"] = part
        return result

    def _property_type(self, scope: lxml_html.HtmlElement) -> PropertyType | None:
        category = self._first_text(
            scope.xpath('.//div[contains(@class, "hp-listing__category")]//a'),
        )
        text = normalize_text(category or "")
        for name, property_type in _CATEGORY_TYPES.items():
            if name in text:
                return property_type
        return None

    @staticmethod
    def _seller(value: str | None) -> str | None:
        label = normalize_text(_after_label(value or "", "Dodane przez"))
        for key, seller_type in _SELLER_TYPES.items():
            if label.startswith(normalize_text(key)):
                return seller_type
        return None

    @staticmethod
    def _images(scope: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for url in scope.xpath(".//*[@data-zoom]/@data-zoom"):
            if isinstance(url, str) and url and url not in images:
                images.append(url)
        return images[:20]

    @staticmethod
    def _first_text(nodes: list[Any]) -> str | None:
        for node in nodes:
            text = _squash(node.text_content() if hasattr(node, "text_content") else str(node))
            if text:
                return text
        return None
