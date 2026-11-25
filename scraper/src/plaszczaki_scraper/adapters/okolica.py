"""Okolica adapter (search pages + detail pages).

Okolica's search pages are classical server-rendered HTML (``section.property``
cards).  There is no voivodeship-level search path: the site only exposes
``/{property}/{transaction}/{city}/`` paths, so :meth:`OkolicaAdapter.queries`
builds one query per city listed in ``config/regions.json`` for each region in
scope.  Listings outside the configured voivodeships are resolved by the runner
from the city or the detail coordinates.

The detail page carries the full description, the parameter list (price, area,
rooms, floor, build year, market) and the exact coordinates on the map container
(``div#map[data-lat][data-lng]``).  ``robots.txt`` disallows ``/pokoj/`` so the
rooms category is intentionally not crawled.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PropertyType
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

BASE_URL = "https://www.okolica.pl"

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkanie", "sprzedam", "sale", "flat"),
    ("mieszkanie", "wynajme", "rent", "flat"),
    ("dom", "sprzedam", "sale", "house"),
    ("dom", "wynajme", "rent", "house"),
    ("dzialka", "sprzedam", "sale", "plot"),
    ("komercyjne", "sprzedam", "sale", "commercial"),
    ("komercyjne", "wynajme", "rent", "commercial"),
)

_CATEGORY_TYPES: dict[str, PropertyType] = {
    "mieszkania": "flat",
    "domy": "house",
    "dzialki": "plot",
    "komercyjne": "commercial",
    "nieruchomosci komercyjne": "commercial",
    "pokoje": "room",
}

_TRANSACTION_TYPES: dict[str, OfferType] = {
    "na sprzedaz": "sale",
    "sprzedaz": "sale",
    "na wynajem": "rent",
    "do wynajecia": "rent",
    "wynajem": "rent",
}

_MARKETS: dict[str, Market] = {
    "pierwotny": "primary",
    "rynek pierwotny": "primary",
    "wtorny": "secondary",
    "rynek wtorny": "secondary",
}

_AREA_RE = re.compile(r"^(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*m2?$", re.IGNORECASE)


def _squash(value: str | None) -> str:
    return " ".join(value.split()) if value else ""


def _tree(html: str) -> lxml_html.HtmlElement:
    if not html.strip():
        raise ParseError("Okolica page is empty")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Okolica page is not valid HTML: {exc}") from exc


def _offer_type_from_id(external_id: str) -> OfferType | None:
    match = re.search(r"-([SW])-", external_id)
    if match is None:
        return None
    return "sale" if match.group(1) == "S" else "rent"


def _split_address(value: str | None) -> tuple[str | None, str | None, str | None]:
    parts = [part.strip() for part in (value or "").split(",") if part.strip()]
    if not parts:
        return None, None, None
    city = parts[0]
    district = None
    street = None
    for part in parts[1:]:
        if part.lower().startswith(("ul.", "al.", "os.", "pl.")):
            street = part
        elif district is None:
            district = part
        elif street is None:
            street = part
    return city, district, street


class OkolicaAdapter:
    """Parse Okolica search and offer pages."""

    id = "okolica"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            if not self.regions.has_region(region):
                continue
            cities = dict.fromkeys(self.regions.region(region).cities)
            for city in cities:
                slug = normalize_text(city).replace(" ", "-")
                if not slug:
                    continue
                for category, transaction, offer_type, property_type in CATEGORIES:
                    queries.append(
                        Query(
                            key=f"{region}:{slug}:{category}:{transaction}",
                            url=f"{BASE_URL}/{category}/{transaction}/{slug}/",
                            region=region,
                            offer_type=offer_type,
                            property_type=property_type,
                        ),
                    )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=1)
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        cards = tree.xpath(
            '//section[contains(concat(" ", normalize-space(@class), " "), " property ")][@data-id]',
        )
        if not cards and not tree.xpath('//div[@id="search-results"]'):
            raise ParseError("Okolica search page has no results container")

        items = [self._summary(card, query) for card in cards]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=self._total_pages(tree),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        page = tree.xpath('//div[@id="offer-page"]')
        if not page:
            raise ParseError("Okolica offer page is missing #offer-page")
        page = page[0]

        title = self._first_text(page.xpath("./h1"))
        address = self._first_text(page.xpath("./h2"))
        header_price = self._first_text(page.xpath("./h3"))

        params, prices = self._params(tree)
        map_node = tree.xpath('//div[@id="map"]')
        lat = safe_float(map_node[0].get("data-lat")) if map_node else None
        lng = safe_float(map_node[0].get("data-lng")) if map_node else None

        crumbs = [
            _squash(span.text_content())
            for span in tree.xpath('//ul[contains(@class, "breadcrumb")]//span[@itemprop="name"]')
        ]
        category = crumbs[1] if len(crumbs) > 1 else None
        transaction = crumbs[2] if len(crumbs) > 2 else None
        city = crumbs[3] if len(crumbs) > 3 else None
        district = crumbs[4] if len(crumbs) >= 6 else None

        address_city, address_district, address_street = _split_address(address)
        city = address_city or city
        district = address_district or district
        street = address_street or summary.street

        offer_type = (
            _TRANSACTION_TYPES.get(normalize_text(transaction or ""))
            or _offer_type_from_id(summary.external_id)
            or summary.offer_type
        )
        property_type = _CATEGORY_TYPES.get(normalize_text(category or ""), summary.property_type)

        description = self._first_text(
            tree.xpath('//div[contains(@class, "desc-params")]//div[contains(@class, "desc")]')
        )
        if not description:
            description = summary.description

        price = parse_price(header_price) or (prices[0] if prices else None)
        price_per_m2 = prices[1] if len(prices) > 1 else summary.price_per_m2_pln

        area = parse_area(params.get("powierzchnia"))
        if area is None:
            area = summary.area_m2
        if property_type == "plot":
            land_area, area = area, None
        else:
            land_area = None

        floor, floor_label = parse_floor((params.get("pietro") or "").split("(")[0])
        if floor_label:
            floor_label = floor_label.strip() or None

        market = None
        for key in ("rynek", "rynek nieruchomosci"):
            if key in params:
                market = _MARKETS.get(normalize_text(params[key]))
                break

        extra = dict(summary.extra)
        extra["okolica"] = {
            "parameters": params,
            "identifier": self._identifier(tree) or summary.external_id,
        }

        return replace(
            summary,
            title=title or summary.title,
            description=strip_html(description),
            offer_type=offer_type,
            property_type=property_type,
            market=market or summary.market,
            price_kind="asking" if price is not None else summary.price_kind,
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price if price is not None else summary.price_pln,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(params.get("liczba pokoi")) or summary.rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(params.get("rok")),
            voivodeship=summary.voivodeship or self.regions.match_city(city),
            city=city or summary.city,
            district=district,
            street=street,
            address_text=address
            or ", ".join(part for part in (city, district, street) if part)
            or None,
            lat=lat if lat is not None else summary.lat,
            lng=lng if lng is not None else summary.lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=self._images(tree) or summary.images,
            published_at=parse_datetime_ms(self._date_posted(tree)) or summary.published_at,
            extra=extra,
        )

    # -- search -----------------------------------------------------------

    def _summary(self, card: lxml_html.HtmlElement, query: Query) -> Listing:
        external_id = (card.get("data-id") or "").strip()
        href = self._first_text(card.xpath('.//h2[contains(@class, "property-title")]//a/@href'))
        if not external_id or not href:
            raise ParseError("Okolica offer card is missing its id or link")

        address = self._first_text(card.xpath('.//a[contains(@class, "property-address")]'))
        city, district, street = _split_address(address or "")

        price, price_per_m2, area, rooms, floor = None, None, None, None, None
        for item in card.xpath('.//ul[contains(@class, "property-data")]/li'):
            text = _squash(item.text_content())
            normalized = normalize_text(text)
            if normalized.startswith("pokoje"):
                rooms = parse_rooms(text)
            elif normalized.startswith("pietro"):
                floor, _ = parse_floor(text.partition(":")[2].strip())
            elif "zł/m" in text:
                price_per_m2 = parse_price(text)
            elif normalized.endswith("zl"):
                if price is None:
                    price = parse_price(text)
            elif _AREA_RE.match(text):
                area = parse_area(text)

        description = self._first_text(
            card.xpath('.//*[contains(@class, "description-short-content")]'),
        )
        image = self._first_text(
            card.xpath('.//ul[contains(@class, "property-images")]//img/@src'),
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urljoin(BASE_URL, href),
            title=self._first_text(card.xpath('.//h2[contains(@class, "property-title")]//a')),
            description=strip_html(description),
            offer_type=_offer_type_from_id(external_id) or query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="asking" if price is not None else "unknown",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=rooms,
            floor=floor,
            voivodeship=query.region,
            city=city,
            district=district,
            street=street,
            address_text=address,
            images=[image] if image else [],
            extra={"okolica": {"identifier": external_id}},
        )

    @staticmethod
    def _total_pages(tree: lxml_html.HtmlElement) -> int | None:
        numbers = [
            safe_int(value)
            for value in tree.xpath('//div[contains(@class, "pagination")]//a/@data-page')
        ]
        pages = [number for number in numbers if number is not None and number > 0]
        return max(pages) if pages else None

    # -- detail -----------------------------------------------------------

    @staticmethod
    def _params(tree: lxml_html.HtmlElement) -> tuple[dict[str, str], list[int]]:
        values: dict[str, str] = {}
        prices: list[int] = []
        for item in tree.xpath('//div[contains(@class, "params-right")]//li'):
            text = _squash(item.text_content())
            if not text:
                continue
            label, separator, value = text.partition(":")
            if not separator:
                continue
            key = normalize_text(label)
            values.setdefault(key, value.strip())
            if key.startswith("cena") and not prices:
                parsed: list[int | None] = [
                    parse_price(price.text_content())
                    for price in item.xpath('.//*[contains(@class, "price")]')
                ]
                prices = [price for price in parsed if price is not None]
        return values, prices

    @staticmethod
    def _identifier(tree: lxml_html.HtmlElement) -> str | None:
        for node in tree.xpath('//script[@type="application/ld+json"]/text()'):
            if "RealEstateListing" not in node:
                continue
            match = re.search(r'"identifier"\s*:\s*"([^"]+)"', node)
            if match is not None:
                return match.group(1)
        return None

    @staticmethod
    def _date_posted(tree: lxml_html.HtmlElement) -> str | None:
        for node in tree.xpath('//script[@type="application/ld+json"]/text()'):
            if "RealEstateListing" not in node:
                continue
            match = re.search(r'"datePosted"\s*:\s*"([^"]+)"', node)
            if match is not None:
                return match.group(1)
        return None

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for url in tree.xpath('//div[contains(@class, "jcarousel")]//img/@src'):
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
