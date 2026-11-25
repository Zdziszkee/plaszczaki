"""LuksusoweMieszkania.pl adapter (JSON-LD search pages + detail pages).

The search pages embed a schema.org ``Product`` with an ``AggregateOffer`` whose
per-listing offers carry the price range, area, rooms, address and geo
coordinates. Detail pages add the full description and the developer brand.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PriceKind, PropertyType
from plaszczaki_scraper.normalize import (
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

BASE_URL = "https://luksusowemieszkania.pl"
PAGE_LINK_RE = re.compile(r"[?&]page=(\d+)")
DESCRIPTION_CLASS = "_opisWithZgloszenie__text"
STREET_PREFIXES = ("ul. ", "al. ", "pl. ", "os. ")

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("sprzedaz", "apartamenty", "sale", "flat"),
    ("sprzedaz", "domy", "sale", "house"),
    ("wynajem", "apartamenty", "rent", "flat"),
    ("wynajem", "domy", "rent", "house"),
)

_ITEM_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "apartment": "flat",
    "accommodation": "flat",
    "house": "house",
    "singlefamilyresidence": "house",
    "landparcel": "plot",
    "plot": "plot",
    "commercial": "commercial",
    "office": "commercial",
    "garage": "garage",
    "room": "room",
}


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
        if isinstance(value, dict | list) and not value:
            continue
        return value
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_scripts(html: str) -> list[dict[str, Any]]:
    try:
        tree = lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"LuksusoweMieszkania page is not valid HTML: {exc}") from exc

    payloads: list[dict[str, Any]] = []
    for node in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(node.text or "")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    if not payloads:
        raise ParseError("LuksusoweMieszkania page is missing JSON-LD")
    return payloads


def _product(html: str) -> dict[str, Any]:
    for payload in _json_scripts(html):
        if payload.get("@type") == "Product":
            return payload
        for node in payload.get("@graph") or []:
            if isinstance(node, dict) and node.get("@type") == "Product":
                return node
    raise ParseError("LuksusoweMieszkania page is missing the Product JSON-LD")


def _offers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    offers = _dict(payload.get("offers"))
    raw = offers.get("offers")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [offers] if offers else []


def _property_type_from_item(item: dict[str, Any]) -> PropertyType | None:
    value = _text(item.get("@type"))
    if value is None:
        return None
    return _ITEM_TYPE_TO_PROPERTY.get(value.lower())


def _locality(value: Any) -> tuple[str | None, str | None]:
    text = _text(value)
    if text is None:
        return None, None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None, None
    city = parts[0]
    district = ", ".join(parts[1:]) or None
    return city, district


def _street(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    lowered = text.lower()
    for prefix in STREET_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip() or None
    return text


def _market_from_url(url: str) -> Market:
    if "/inwestycja/" in url:
        return "primary"
    if "/oferta/" in url:
        return "secondary"
    return "unknown"


def _price_kind(raw: Any) -> PriceKind:
    text = str(raw or "")
    is_range = "\u2013" in text or "-" in text or text.lower().startswith("od ")
    return "starting" if is_range else "asking"


def _money(value: Any) -> int | None:
    """Parse a price, including JSON-LD decimals such as ``575122.19``."""
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace("\u00a0", "")
        if text.replace(".", "", 1).isdigit():
            return parse_price(float(text))
    return parse_price(value)


def _range_value(value: Any) -> Any:
    data = _dict(value)
    return _first(data.get("value"), data.get("minValue"))


def _additional_properties(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(value, list):
        return result
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        raw = _text(entry.get("value"))
        if name and raw:
            result[name] = raw
    return result


def _external_id(url: str) -> str:
    path = url.rstrip("/").rsplit("/", 1)[-1]
    _, _, suffix = path.rpartition(",")
    return suffix or path


def _max_page(html: str) -> int | None:
    pages = [int(value) for value in PAGE_LINK_RE.findall(html)]
    return max(pages) if pages else None


class LuksusoweMieszkaniaAdapter:
    """Parse LuksusoweMieszkania.pl server-rendered schema.org payloads."""

    id = "luksusowemieszkania"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for transaction, kind, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{transaction}:{kind}",
                        url=f"{BASE_URL}/{transaction}/{kind}/{region}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _product(html)
        items = [self._summary(offer, query) for offer in _offers(payload)]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=_max_page(html),
            total_items=None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _product(html)
        offers = _offers(payload)
        unit = offers[0] if offers else {}
        item = _dict(unit.get("itemOffered")) or _dict(payload.get("itemOffered"))

        aggregate = _dict(payload.get("offers"))
        url = _text(payload.get("url")) or summary.url

        address = _dict(item.get("address"))
        geo = _dict(item.get("geo"))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if lat is None or lng is None:
            lat, lng = summary.lat, summary.lng

        city, district = _locality(address.get("addressLocality"))
        province = _text(address.get("addressRegion")) or summary.voivodeship
        price = _money(_first(aggregate.get("lowPrice"), unit.get("price"), summary.price_pln))
        if price is None:
            price = summary.price_pln

        properties = _additional_properties(payload.get("additionalProperty"))
        build_year = safe_int(
            _first(
                properties.get("Rok budowy"),
                properties.get("Rok Budowy"),
                summary.build_year,
            ),
        )
        published_at = parse_datetime_ms(
            _first(
                properties.get("Data dodania"),
                _additional_properties(unit.get("additionalProperty")).get("Data dodania"),
            ),
        )

        area = parse_area(_range_value(item.get("floorSize"))) or summary.area_m2
        rooms = parse_rooms(_range_value(item.get("numberOfRooms"))) or summary.rooms
        floor, floor_label = parse_floor(item.get("floorLevel"))
        if floor is None and floor_label is None:
            floor, floor_label = summary.floor, summary.floor_label

        images = self._images(payload.get("image")) or summary.images
        brand = _dict(payload.get("brand"))
        market = _market_from_url(url)
        seller_type = (
            "developer" if market == "primary" else "agency" if brand else summary.seller_type
        )

        extra = dict(summary.extra)
        source_extra = dict(extra.get("luksusowemieszkania") or {})
        source_extra.update(
            {
                "low_price": aggregate.get("lowPrice"),
                "high_price": aggregate.get("highPrice"),
                "offer_count": aggregate.get("offerCount"),
                "unit_offers": len(offers),
                "properties": properties,
            },
        )
        extra["luksusowemieszkania"] = source_extra

        return replace(
            summary,
            url=url,
            external_id=_external_id(url),
            title=_text(payload.get("name")) or summary.title,
            description=self._description(html) or strip_html(_text(payload.get("description"))),
            price_kind="starting" if aggregate.get("lowPrice") else summary.price_kind,
            price_pln=price,
            area_m2=area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=build_year,
            market=market,
            voivodeship=self.regions.canonical_voivodeship(province) or summary.voivodeship,
            city=city or summary.city,
            district=district or summary.district,
            street=_street(address.get("streetAddress")) or summary.street,
            address_text=self._address_text(address) or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=images,
            seller_type=seller_type,
            seller_name=_text(brand.get("name")) or summary.seller_name,
            published_at=published_at or summary.published_at,
            extra=extra,
        )

    def _summary(self, offer: dict[str, Any], query: Query) -> Listing:
        url = _text(offer.get("url")) or ""
        if not url:
            raise ParseError("LuksusoweMieszkania search offer has no url")

        item = _dict(offer.get("itemOffered"))
        address = _dict(item.get("address"))
        geo = _dict(item.get("geo"))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))

        city, district = _locality(address.get("addressLocality"))
        province = _text(address.get("addressRegion"))
        property_type = _property_type_from_item(item) or query.property_type
        price_raw = offer.get("price")
        floor, floor_label = parse_floor(item.get("floorLevel"))

        extra = {
            "luksusowemieszkania": {
                "offer_count": offer.get("offerCount"),
                "properties": _additional_properties(item.get("additionalProperty")),
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=_external_id(url),
            url=url,
            title=_text(offer.get("name")),
            description=strip_html(_text(offer.get("description"))),
            offer_type=query.offer_type,
            property_type=property_type,
            market=_market_from_url(url),
            price_kind=_price_kind(price_raw),
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=_money(price_raw),
            area_m2=parse_area(_range_value(item.get("floorSize"))),
            land_area_m2=parse_area(_first(item.get("landSize"), item.get("lotSize"))),
            rooms=parse_rooms(_range_value(item.get("numberOfRooms"))),
            floor=floor,
            floor_label=floor_label,
            voivodeship=self.regions.canonical_voivodeship(province),
            city=city,
            district=district,
            street=_street(address.get("streetAddress")),
            address_text=self._address_text(address),
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(offer.get("image")),
            seller_type="developer" if _market_from_url(url) == "primary" else "agency",
            extra=extra,
        )

    @staticmethod
    def _address_text(address: dict[str, Any]) -> str | None:
        street = _street(address.get("streetAddress"))
        locality = _text(address.get("addressLocality"))
        parts = [part for part in (street, locality) if part]
        return ", ".join(parts) or None

    @staticmethod
    def _images(value: Any) -> list[str]:
        if value is None:
            return []
        candidates = value if isinstance(value, list) else [value]
        images: list[str] = []
        for item in candidates:
            url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
            if url and url not in images:
                images.append(url)
        return images[:20]

    @staticmethod
    def _description(html: str) -> str | None:
        try:
            tree = lxml_html.fromstring(html)
        except ValueError, TypeError:
            return None
        nodes = tree.xpath(f'//div[contains(@class, "{DESCRIPTION_CLASS}")]')
        for node in nodes:
            text = " ".join(" ".join(node.itertext()).split())
            if text:
                return text
        return None
