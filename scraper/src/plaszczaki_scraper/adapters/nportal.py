"""Nportal.pl adapter.

Nportal is a server-rendered Morizon-Gratka property portal. Search pages use
``/{kind}/{region}/`` for sale and ``/{kind}/wynajem/{region}/`` for rent;
detail pages expose coordinates in ``#property-map`` and the full description
in ``#description``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

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
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "http://nportal.pl"

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("dzialki", "sprzedaz", "sale", "plot"),
    ("komercyjne", "sprzedaz", "sale", "commercial"),
    ("garaze", "sprzedaz", "sale", "garage"),
    ("garaze", "wynajem", "rent", "garage"),
    ("pokoje", "wynajem", "rent", "room"),
)

_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_EXTERNAL_ID_RE = re.compile(r"-npo(\d+)/?$")
_DATA_ID_RE = re.compile(r"npo(\d+)$")
_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_AREA_RE = re.compile(r"([\d\s.,]+)\s*m\s*(?:²|2)", re.IGNORECASE)
_ROOMS_RE = re.compile(r"(\d+)\s*pok(?:oje|ój|oj)", re.IGNORECASE)
_FLOOR_RE = re.compile(r"pi[eę]tro\s*([0-9/-]+)", re.IGNORECASE)

_PROPERTY_LABELS: dict[str, tuple[str, ...]] = {
    "flat": ("powierzchnia mieszkalna", "powierzchnia uzytkowa", "powierzchnia"),
    "house": ("powierzchnia mieszkalna", "powierzchnia uzytkowa", "powierzchnia"),
    "plot": ("powierzchnia", "powierzchnia dzialki"),
    "commercial": ("powierzchnia", "powierzchnia uzytkowa"),
    "garage": ("powierzchnia",),
    "room": ("powierzchnia",),
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


_STREET_PREFIX_RE = re.compile(r"^(?:ul\.|al\.|os\.|pl\.|rondo)\s*", re.IGNORECASE)


def _street_name(value: str | None) -> str | None:
    """Strip a Polish street-type prefix from a location fragment."""
    if not value:
        return None
    return _STREET_PREFIX_RE.sub("", value.strip()) or None


def _text(node: Any) -> str | None:
    if node is None:
        return None
    return _clean(node.text_content())


def _first_text(nodes: list[Any]) -> str | None:
    return _text(nodes[0]) if nodes else None


def _first_attr(nodes: list[Any], name: str) -> str | None:
    for node in nodes:
        value = node.get(name)
        if value:
            return value.strip()
    return None


def _per_m2(value: str | None) -> int | None:
    """Parse a price-per-square-metre string without losing decimals."""
    amount = parse_area(value)
    return int(amount) if amount is not None else None


def _parse_pl_date(value: str | None) -> int | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        return None


class NportalAdapter:
    """Parse Nportal's server-rendered HTML."""

    id = "nportal"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for slug, transaction, offer_type, property_type in CATEGORIES:
                if transaction == "wynajem":
                    url = f"{BASE_URL}/{slug}/wynajem/{region}/"
                else:
                    url = f"{BASE_URL}/{slug}/{region}/"
                queries.append(
                    Query(
                        key=f"{region}:{transaction}:{slug}",
                        url=url,
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """Nportal mirrors Domy.pl's bracket sort parameters."""
        return query.with_param("ps[sort_order]", "date").with_param("ps[sort_asc]", "0")

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath('//div[contains(@class, "slist")]')
        items = [self._summary(card, query) for card in cards]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=self._total_pages(tree, query.page),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        if not tree.xpath('//div[@id="description"]'):
            raise ParseError("Nportal detail page is missing #description")

        params = self._params(tree)
        crumbs = [
            _clean(value)
            for value in tree.xpath(
                '//nav[contains(@class, "breadcrumbs")]//span[@itemprop="title"]/text()',
            )
            if _clean(value)
        ]
        province = crumbs[2] if len(crumbs) >= 3 else None
        city = crumbs[3] if len(crumbs) >= 4 else None

        title = _first_text(tree.xpath('//h1[@id="location"]'))
        street, district = self._split_title(title or "", city)

        voivodeship = self.regions.canonical_voivodeship(province) or summary.voivodeship
        lat = safe_float(_first_attr(tree.xpath('//div[@id="property-map"]'), "data-lat"))
        lng = safe_float(_first_attr(tree.xpath('//div[@id="property-map"]'), "data-lng"))

        area = self._area(params, summary.property_type)
        land_area = parse_area(params.get("powierzchnia dzialki"))
        if summary.property_type == "plot" and area is not None:
            land_area, area = area, None

        price = parse_price(
            _first_text(tree.xpath('//div[@id="price"]/span')) or params.get("cena"),
        )
        modified_at = parse_datetime_ms(self._last_modified(tree))
        floor, floor_label = parse_floor(params.get("pietro"))

        extra = dict(summary.extra)
        extra["nportal"] = {
            "offer_number": params.get("numer oferty"),
            "building_type": params.get("typ budynku"),
        }

        return replace(
            summary,
            title=title or summary.title,
            description=_first_text(tree.xpath('//div[@id="description"]')),
            market=self._market(params.get("rynek pierwotny")),
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if summary.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_per_m2(params.get("cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(params.get("liczba pokoi")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(params.get("rok budowy")),
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or city,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(tree),
            published_at=summary.published_at,
            modified_at=modified_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        link = (card.xpath('.//a[contains(@class, "slr_location")]') or [None])[0]
        href = link.get("href") if link is not None else None
        if not href:
            raise ParseError("Nportal listing card is missing its link")

        match = _EXTERNAL_ID_RE.search(href)
        data_id = str(card.get("data-id") or "")
        data_match = _DATA_ID_RE.search(data_id)
        external_id = (
            match.group(1)
            if match
            else (data_match.group(1) if data_match else href.rstrip("/").rsplit("-", 1)[-1])
        )

        location = _text(link)
        street, district, city = self._split_location(location, None, parts_are_city_first=True)

        features = " ".join(
            value
            for value in card.xpath('.//div[contains(@class, "slr_left")]/div//text()')
            if _clean(value)
        )
        area_match = _AREA_RE.search(features)
        rooms_match = _ROOMS_RE.search(features)
        floor_match = _FLOOR_RE.search(features)

        area = parse_area(area_match.group(1)) if area_match else None
        land_area = None
        if query.property_type == "plot":
            land_area, area = area, None
        floor, floor_label = parse_floor(floor_match.group(1) if floor_match else None)

        prices = card.xpath('.//div[contains(@class, "slr_prices")]')
        price = parse_price(_first_text(prices[0].xpath("./div[1]")) if prices else None)
        per_m2 = _per_m2(_first_text(prices[0].xpath("./span[1]")) if prices else None)

        image = _first_attr(
            card.xpath('.//div[contains(@class, "slr_photo")]//img'), "data-original"
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=_clean(link.get("title") if link is not None else None),
            description=_first_text(card.xpath('.//div[contains(@class, "slr_description")]')),
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(rooms_match.group(1) if rooms_match else None),
            floor=floor,
            floor_label=floor_label,
            voivodeship=query.region,
            city=city,
            district=district,
            street=street,
            address_text=location,
            images=[image] if image else [],
            extra={"nportal": {"transaction": query.offer_type}},
        )

    @staticmethod
    def _total_pages(tree: Any, fallback: int) -> int:
        pages = [int(value) for value in _PAGE_RE.findall(" ".join(tree.xpath("//a/@href")))]
        return max(pages, default=fallback)

    @staticmethod
    def _params(tree: Any) -> dict[str, str]:
        params: dict[str, str] = {}
        for row in tree.xpath('//table[@id="paramsBasic"]//tr'):
            cells = row.xpath("./th|./td")
            for index in range(0, len(cells) - 1, 2):
                key = normalize_text(cells[index].text_content() or "")
                value = _clean(cells[index + 1].text_content())
                if key and value:
                    params.setdefault(key, value)
        return params

    @staticmethod
    def _last_modified(tree: Any) -> str | None:
        value = _first_text(tree.xpath('//div[@id="lastModified"]'))
        if not value:
            return None
        return value.split(":", 1)[-1].strip()

    @staticmethod
    def _split_title(title: str, city: str | None) -> tuple[str | None, str | None]:
        """Return (street, district) from the comma-separated H1 location."""
        tail = title.split(":", 1)[-1]
        parts = [part.strip() for part in tail.split(",") if part.strip()]
        if not parts:
            return None, None
        street = _street_name(parts[-1])
        if city and street and normalize_text(street) == normalize_text(city):
            street = None
        district = parts[2] if len(parts) >= 4 else (parts[1] if len(parts) >= 3 else None)
        return street, district

    @staticmethod
    def _split_location(
        value: str | None,
        city_hint: str | None,
        *,
        parts_are_city_first: bool = False,
    ) -> tuple[str | None, str | None, str | None]:
        """Return (street, district, city) from a comma-separated location."""
        if not value:
            return None, None, city_hint
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            return None, None, city_hint

        if parts_are_city_first:
            city = parts[0]
            street = _street_name(parts[-1]) if len(parts) > 1 else None
            district = parts[1] if len(parts) > 2 else None
            return street, district, city

        city = city_hint or (parts[0] if parts else None)
        street = _street_name(parts[-1]) if len(parts) > 1 else None
        if street and city and normalize_text(street) == normalize_text(city):
            street = None
        district = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else None)
        return street, district, city

    @staticmethod
    def _area(params: dict[str, str], property_type: PropertyType) -> float | None:
        for label in _PROPERTY_LABELS.get(property_type, ("powierzchnia",)):
            area = parse_area(params.get(label))
            if area is not None:
                return area
        return None

    @staticmethod
    def _market(value: str | None) -> Market:
        normalized = normalize_text(value or "")
        if normalized in {"tak", "yes", "true"}:
            return "primary"
        if normalized in {"nie", "no", "false"}:
            return "secondary"
        return "unknown"

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        for node in tree.xpath('//section[@id="imageBoxShow"]//img'):
            src = node.get("data-original") or node.get("src")
            if src and "nportal.pl/t.gif" not in src and src not in images:
                images.append(src)
        return images[:20]
