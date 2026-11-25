"""Rentola adapter (JSON-LD search results and detail pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://rentola.pl"

# City landing pages per voivodeship. Rentola has no voivodeship search URL.
CITY_SLUGS: dict[str, tuple[str, ...]] = {
    "malopolskie": ("krakow", "tarnow", "nowy-sacz", "zakopane"),
    "slaskie": ("katowice", "gliwice", "czestochowa", "bielsko-biala"),
}

CATEGORIES: tuple[tuple[str, PropertyType], ...] = (
    ("mieszkanie", "flat"),
    ("dom", "house"),
)

_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "room": "room",
    "singlefamilyresidence": "house",
}

_PAGINATION_RE = re.compile(r'"pagination"\s*:\s*\{([^}]*)\}', re.IGNORECASE)
_RENT_RE = re.compile(
    r"Op[łl]aty administracyjne:?[^\d]{0,30}([\d\s\u00a0.,]+)\s*z[łl]",
    re.IGNORECASE,
)
_DEPOSIT_RE = re.compile(r"Kaucja[^\d]{0,40}([\d\s\u00a0.,]+)\s*z[łl]", re.IGNORECASE)


class RentolaAdapter:
    """Parse Rentola's schema.org search and detail payloads."""

    id = "rentola"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for city in CITY_SLUGS.get(region, ()):
                for category, property_type in CATEGORIES:
                    queries.append(
                        Query(
                            key=f"{region}:wynajem:{category}:{city}",
                            url=f"{BASE_URL}/wynajem/{category}/{city}",
                            region=region,
                            offer_type="rent",
                            property_type=property_type,
                        ),
                    )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _search_payload(html)
        if payload is None:
            raise ParseError("Rentola search page is missing SearchResultsPage JSON-LD")

        main_entity = _dict(payload.get("mainEntity"))
        entries = main_entity.get("itemListElement") or []
        items = [self._summary(entry, query) for entry in entries if isinstance(entry, dict)]

        current_page, total_pages, total_items = _pagination(html, query.page)
        return SearchPage(
            items=items,
            current_page=current_page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _detail_payload(html)
        if payload is None:
            raise ParseError("Rentola detail page is missing RealEstateListing JSON-LD")

        offers = _dict(payload.get("offers"))
        offered = _dict(offers.get("itemOffered"))
        address = _dict(offered.get("address"))
        geo = _dict(offered.get("geo"))
        floor_size = _dict(offered.get("floorSize"))
        description = strip_html(_text(payload.get("description")))
        page_text = " ".join(lxml_html.fromstring(html).text_content().split())

        city = _text(address.get("addressLocality")) or summary.city
        street = _street(_text(address.get("streetAddress"))) or summary.street
        province = _clean_province(_text(address.get("addressRegion")))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if lat is None or lng is None:
            lat = summary.lat
            lng = summary.lng

        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None:
            match = self.regions.resolve(
                city=city,
                county=summary.county,
                lat=lat,
                lng=lng,
            )
            voivodeship = match.voivodeship if match else summary.voivodeship

        price = parse_price(offers.get("price")) or summary.price_pln
        area = parse_area(floor_size.get("value")) or summary.area_m2
        rooms = (
            parse_rooms(
                _value(_first(offered.get("numberOfRooms"), offered.get("numberOfBedrooms"))),
            )
            or summary.rooms
        )

        extra = dict(summary.extra)
        extra["rentola"] = {
            "valid_from": offers.get("validFrom"),
            "availability": offers.get("availability"),
        }

        return replace(
            summary,
            url=_text(payload.get("url")) or summary.url,
            title=_text(payload.get("name")) or summary.title,
            description=description or summary.description,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly",
            price_pln=price,
            rent_pln=_search_price(_RENT_RE, page_text),
            deposit_pln=_search_price(_DEPOSIT_RE, page_text),
            area_m2=area,
            rooms=rooms,
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(payload.get("image")),
            published_at=parse_datetime_ms(
                _first(payload.get("datePosted"), offers.get("validFrom")),
            )
            or summary.published_at,
            extra=extra,
        )

    def _summary(self, entry: dict[str, Any], query: Query) -> Listing:
        item = entry.get("item")
        if not isinstance(item, dict):
            item = entry

        url = _text(item.get("url")) or _text(entry.get("url"))
        if url is None:
            raise ParseError("Rentola search item has no URL")

        offers = _dict(item.get("offers"))
        offered = _dict(offers.get("itemOffered"))
        address = _dict(offered.get("address"))
        geo = _dict(offered.get("geo"))
        floor_size = _dict(offered.get("floorSize"))

        external_id = url.rstrip("/").rsplit("/", 1)[-1]
        city = _text(address.get("addressLocality"))
        street = _street(_text(address.get("streetAddress")))
        province = _clean_province(_text(address.get("addressRegion")))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        price = parse_price(offers.get("price"))

        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None:
            match = self.regions.resolve(city=city, lat=lat, lng=lng)
            voivodeship = match.voivodeship if match else None

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urljoin(BASE_URL, url),
            title=_text(item.get("name")),
            offer_type="rent",
            property_type=_property_type(offered.get("@type"), query.property_type),
            market="secondary",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly",
            price_pln=price,
            area_m2=parse_area(floor_size.get("value")),
            rooms=parse_rooms(
                _value(_first(offered.get("numberOfRooms"), offered.get("numberOfBedrooms"))),
            ),
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(item.get("image")),
            published_at=parse_datetime_ms(offers.get("validFrom")),
            extra={
                "rentola": {
                    "availability": offers.get("availability"),
                    "search_key": query.key,
                },
            },
        )


def _json_ld(html: str) -> list[dict[str, Any]]:
    try:
        tree = lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Rentola page is not valid HTML: {exc}") from exc

    objects: list[dict[str, Any]] = []
    for script in tree.cssselect("script[type='application/ld+json']"):
        raw = script.text_content().strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        stack: list[Any] = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                objects.append(value)
                graph = value.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(value, list):
                stack.extend(value)
    return objects


def _search_payload(html: str) -> dict[str, Any] | None:
    for item in _json_ld(html):
        if item.get("@type") == "SearchResultsPage":
            return item
    return None


def _detail_payload(html: str) -> dict[str, Any] | None:
    for item in _json_ld(html):
        if item.get("@type") == "RealEstateListing":
            return item
    return None


def _pagination(html: str, fallback_page: int) -> tuple[int, int, int | None]:
    # Rentola embeds the paginator in escaped Next.js flight data; normalising
    # the escaped quotes makes the same JSON shape parseable.
    text = html.replace('\\"', '"')
    match = _PAGINATION_RE.search(text)
    if match is None:
        return fallback_page, fallback_page, None

    block = match.group(1)
    current = safe_int(_match_group(r'"currentPage"\s*:\s*(\d+)', block))
    total = safe_int(_match_group(r'"totalPages"\s*:\s*(\d+)', block))
    count = safe_int(_match_group(r'"totalCount"\s*:\s*(\d+)', block))
    return current or fallback_page, total or fallback_page, count


def _match_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return value


def _street(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _clean_province(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    for prefix in ("wojewodztwo ", "wojewodztwa "):
        if normalized.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    if not value:
        return fallback
    return _TYPE_TO_PROPERTY.get(normalize_text(str(value)), fallback)


def _images(value: Any) -> list[str]:
    raw: list[Any]
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = []

    images: list[str] = []
    for item in raw:
        url = _text(item)
        if url and url.startswith("https://") and url not in images:
            images.append(url)
        if len(images) >= 20:
            break
    return images


def _search_price(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    return parse_price(match.group(1))
