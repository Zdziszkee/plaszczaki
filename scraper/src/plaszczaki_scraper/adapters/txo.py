"""TXO adapter.

TXO (https://txo.pl) is a Polish meta-search that aggregates listings from a
dozen portals. Its search pages are server-rendered Inertia documents that embed
the whole result page as JSON (``<script data-page="app">``), including
coordinates, price, area and rooms. Detail pages carry schema.org
``RealEstateListing`` JSON-LD with the description, address, coordinates and
characteristics, so no browser rendering is required.

TXO exposes no voivodeship filter, so queries are scoped by city using the
cities listed for each region in ``config/regions.json``; the listing's own
``region`` field and coordinates are used to confirm the scope.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

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

BASE_URL = "https://txo.pl"

PAGE_DATA_RE = re.compile(
    r'<script[^>]*data-page="app"[^>]*>(.*?)</script>',
    re.DOTALL,
)
LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# (TXO transaction, TXO property type, canonical offer type, canonical property type)
CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("sale", "apartment", "sale", "flat"),
    ("rent", "apartment", "rent", "flat"),
    ("sale", "house", "sale", "house"),
    ("rent", "house", "rent", "house"),
    ("sale", "plot", "sale", "plot"),
    ("sale", "commercial", "sale", "commercial"),
    ("rent", "commercial", "rent", "commercial"),
)

_OFFER_TYPES: dict[str, OfferType] = {
    "sale": "sale",
    "rent": "rent",
    "auction": "auction",
    "lease": "lease",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "plot": "plot",
    "commercial": "commercial",
    "room": "room",
    "garage": "garage",
    "other": "other",
}

_PLOT_LAND_KEYS = ("Pow. działki", "Powierzchnia działki", "Pow. terenu")

_AMOUNT_RE = re.compile(r"\d[\d .,]*")


def _amount(value: Any) -> int | None:
    """Parse a JSON number or a Polish money string into whole PLN.

    ``normalize.parse_price`` drops decimal separators, which is right for
    listing prices but wrong for decimals such as TXO's ``"2390000.00"``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(value) if value > 0 else None

    text = str(value).replace("\u00a0", " ").strip()
    if not text:
        return None
    lowered = text.lower()
    if "mln" in lowered or "milion" in lowered or "tys" in lowered:
        return parse_price(text)

    match = _AMOUNT_RE.search(text)
    if match is None:
        return None
    number = match.group(0).strip().rstrip(".,").replace(" ", "")
    if "," in number:
        whole, _, fraction = number.rpartition(",")
        number = f"{whole}.{fraction}" if fraction.isdigit() and len(fraction) <= 2 else number
        number = number.replace(",", "")
    try:
        amount = round(float(number))
    except ValueError:
        return None
    return amount if amount > 0 else None


def _property_type_from_url(url: str | None, fallback: PropertyType) -> PropertyType:
    if not url:
        return fallback
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    prefix = slug.split("-", 1)[0]
    return _PROPERTY_TYPES.get(prefix, fallback)


def _page_data(html: str) -> dict[str, Any]:
    match = PAGE_DATA_RE.search(html)
    if match is None:
        raise ParseError("TXO page is missing the data-page payload")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"TXO data-page payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("TXO data-page payload is not an object")
    return payload


def _json_ld_nodes(html: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for match in LD_JSON_RE.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if not isinstance(record, dict):
                continue
            graph = record.get("@graph")
            if isinstance(graph, list):
                nodes.extend(item for item in graph if isinstance(item, dict))
            else:
                nodes.append(record)
    return nodes


def _listing_node(html: str) -> dict[str, Any]:
    for node in _json_ld_nodes(html):
        if node.get("@type") == "RealEstateListing":
            return node
    raise ParseError("TXO detail page is missing RealEstateListing JSON-LD")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
        if isinstance(value, list) and not value:
            continue
        return value
    return None


def _offer_type(value: Any, fallback: OfferType) -> OfferType:
    text = normalize_text(str(value or ""))
    return _OFFER_TYPES.get(text, fallback)


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    text = normalize_text(str(value or ""))
    return _PROPERTY_TYPES.get(text, fallback)


def _characteristics(node: dict[str, Any]) -> dict[str, str]:
    items: dict[str, str] = {}
    for entry in node.get("additionalProperty") or []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        value = entry.get("value")
        if name is None or value is None:
            continue
        items[name] = str(value)
    return items


def _market(characteristics: dict[str, str]) -> Market:
    primary = characteristics.get("Rynek pierwotny")
    if primary is not None:
        text = normalize_text(primary)
        if text in {"tak", "true", "1", "yes"}:
            return "primary"
        if text in {"nie", "false", "0", "no"}:
            return "secondary"
    market = characteristics.get("Rynek") or characteristics.get("Rynek nieruchomości")
    if market is not None:
        text = normalize_text(market)
        if "pierwotn" in text:
            return "primary"
        if "wtorn" in text:
            return "secondary"
    return "unknown"


def _seller_type(characteristics: dict[str, str]) -> str | None:
    value = characteristics.get("Typ ogłoszeniodawcy") or characteristics.get("Oferent")
    if value is None:
        return None
    text = normalize_text(value)
    if "wlasci" in text or "prywatn" in text:
        return "private"
    if "deweloper" in text or "developer" in text:
        return "developer"
    if "biuro" in text or "agencj" in text or "posredni" in text:
        return "agency"
    return None


def _images(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    images: list[str] = []
    for item in value:
        url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
        if url:
            images.append(urljoin(BASE_URL, url))
    return images[:20]


def _clean_description(node: dict[str, Any], title: str | None) -> str | None:
    description = strip_html(_text(node.get("description")))
    if description is None or title is None:
        return description
    cleaned = description.strip()
    if cleaned.lower().startswith(title.strip().lower()):
        cleaned = cleaned[len(title.strip()) :].lstrip(" -\u2013\u2014|:,")
    return cleaned or None


class TxoAdapter:
    """Parse TXO search payloads and detail JSON-LD."""

    id = "txo"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            if not self.regions.has_region(region):
                continue
            cities = list(dict.fromkeys(self.regions.region(region).cities))
            for city in cities:
                for transaction, kind, offer_type, property_type in CATEGORIES:
                    city_key = normalize_text(city).replace(" ", "-")
                    queries.append(
                        Query(
                            key=f"{region}:{city_key}:{transaction}:{kind}",
                            url=(
                                f"{BASE_URL}/ogloszenia?type={transaction}"
                                f"&property_type={kind}&city={quote(city)}"
                            ),
                            region=region,
                            offer_type=offer_type,
                            property_type=property_type,
                        ),
                    )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        props = _dict(_page_data(html).get("props"))
        listings = _dict(props.get("listings"))
        raw_items = listings.get("data")
        if not isinstance(raw_items, list):
            raise ParseError("TXO search payload is missing listings.data")

        items = [self._summary(item, query) for item in raw_items if isinstance(item, dict)]
        meta = _dict(listings.get("meta"))
        return SearchPage(
            items=items,
            current_page=safe_int(meta.get("current_page")) or query.page,
            total_pages=safe_int(meta.get("last_page")),
            total_items=safe_int(meta.get("total")),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        node = _listing_node(html)
        characteristics = _characteristics(node)
        offers = _dict(node.get("offers"))
        address = _dict(node.get("address"))
        geo = _dict(node.get("geo"))

        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        province = _text(address.get("addressRegion"))
        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)
        if voivodeship is None:
            voivodeship = summary.voivodeship

        title = _text(node.get("name")) or summary.title
        city = _text(address.get("addressLocality")) or summary.city
        street = _text(address.get("streetAddress")) or summary.street
        district = summary.district
        address_text = (
            summary.address_text or ", ".join(part for part in (street, city) if part) or None
        )

        offer_type = summary.offer_type
        property_type = _property_type_from_url(
            _text(node.get("url")) or summary.url,
            summary.property_type,
        )
        price = _amount(offers.get("price"))
        price_unit = "monthly" if offer_type == "rent" else "total"

        floor_size = _dict(node.get("floorSize"))
        area = parse_area(floor_size.get("value"))
        land_area = parse_area(_first(*(characteristics.get(key) for key in _PLOT_LAND_KEYS)))
        if property_type == "plot":
            land_area = _first(land_area, area)
            area = None
        else:
            land_area = _first(land_area, summary.land_area_m2)

        rooms = parse_rooms(_first(node.get("numberOfRooms"), characteristics.get("Liczba pokoi")))
        floor, floor_label = parse_floor(
            _first(node.get("floorLevel"), characteristics.get("Piętro")),
        )
        build_year = safe_int(_first(node.get("yearBuilt"), characteristics.get("Rok budowy")))

        extra = dict(summary.extra)
        txo_extra = dict(extra.get("txo") or {})
        txo_extra.update(
            {
                "characteristics": characteristics,
                "amenities": {
                    _text(item.get("name")): item.get("value")
                    for item in (node.get("amenityFeature") or [])
                    if isinstance(item, dict) and _text(item.get("name"))
                },
                "date_modified": node.get("dateModified"),
            },
        )
        extra["txo"] = txo_extra

        return replace(
            summary,
            url=_text(node.get("url")) or summary.url,
            title=title,
            description=_clean_description(node, title),
            offer_type=offer_type,
            property_type=property_type,
            market=_market(characteristics),
            price_kind="asking",
            price_unit=price_unit,
            price_pln=price,
            price_per_m2_pln=_amount(
                _first(characteristics.get("Cena za m²"), summary.price_per_m2_pln),
            ),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=build_year,
            voivodeship=voivodeship,
            county=summary.county,
            commune=summary.commune,
            city=city,
            district=district,
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=_images(node.get("image")) or summary.images,
            seller_type=_seller_type(characteristics) or summary.seller_type,
            seller_name=summary.seller_name,
            published_at=parse_datetime_ms(node.get("datePosted")) or summary.published_at,
            modified_at=parse_datetime_ms(node.get("dateModified")),
            extra=extra,
        )

    def _summary(self, item: dict[str, Any], query: Query) -> Listing:
        slug = _text(item.get("slug"))
        url = _text(item.get("url"))
        if slug is None and url:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
        if slug is None:
            raise ParseError(f"TXO item {item.get('id')} has no slug")

        external_id = _text(item.get("id")) or slug
        offer_type = _offer_type(item.get("type"), query.offer_type)
        property_type = _property_type(item.get("property_type"), query.property_type)

        lat = safe_float(item.get("latitude"))
        lng = safe_float(item.get("longitude"))
        voivodeship = self.regions.canonical_voivodeship(_text(item.get("region")))
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        city = _text(item.get("city"))
        district = _text(item.get("district"))
        address_text = ", ".join(part for part in (district, city) if part and part != city) or city

        area = parse_area(item.get("area"))
        land_area = None
        if property_type == "plot":
            land_area = area
            area = None

        floor, floor_label = parse_floor(item.get("floor"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url or f"{BASE_URL}/ogloszenia/{slug}",
            title=_text(item.get("title")),
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=_amount(item.get("price")),
            price_per_m2_pln=_amount(item.get("price_per_sqm")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(item.get("rooms")),
            floor=floor,
            floor_label=floor_label,
            voivodeship=voivodeship,
            city=city,
            district=district,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(item.get("images")),
            published_at=parse_datetime_ms(
                _first(item.get("source_created_at"), item.get("created_at")),
            ),
            extra={
                "txo": {
                    "id": item.get("id"),
                    "source_name": item.get("source_name"),
                    "sources_count": item.get("sources_count"),
                    "region": item.get("region"),
                    "coordinates_exact": item.get("coordinates_exact"),
                },
            },
        )
