"""Vilea (Christie's International Real Estate Polska) adapter.

Search pages are WordPress archives filtered by ``localizations[]`` and rendered
as server-side cards; detail pages expose a schema.org ``RealEstateListing``
with the description, price, area and (when the address is public) coordinates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

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
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://vilea.pl"
PAGE_LINK_RE = re.compile(r"/page/(\d+)")

CATEGORIES: tuple[tuple[str, OfferType], ...] = (
    ("nieruchomosci-na-sprzedaz", "sale"),
    ("nieruchomosci-na-wynajem", "rent"),
)

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartament": "flat",
    "mieszkanie": "flat",
    "penthouse": "flat",
    "dom": "house",
    "willa": "house",
    "rezydencja": "house",
    "dzialka": "plot",
    "grunt": "plot",
    "lokal komercyjny": "commercial",
    "lokal": "commercial",
    "biuro": "commercial",
    "garaz": "garage",
}

_ITEM_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "apartment": "flat",
    "singlefamilyresidence": "house",
    "house": "house",
    "landparcel": "plot",
    "commercialproperty": "commercial",
    "office": "commercial",
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


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Vilea page is not valid HTML: {exc}") from exc


def _join_text(nodes: list[Any]) -> str | None:
    text = " ".join(" ".join(node.text_content().split()) for node in nodes)
    return text.strip() or None


def _listing_payload(html: str) -> dict[str, Any]:
    tree = _tree(html)
    for node in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(node.text or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        nodes = payload.get("@graph") if isinstance(payload.get("@graph"), list) else [payload]
        for entry in nodes:
            if isinstance(entry, dict) and entry.get("@type") == "RealEstateListing":
                return entry
    raise ParseError("Vilea page is missing the RealEstateListing JSON-LD")


def _external_id(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    text = normalize_text(str(value or ""))
    if not text:
        return fallback
    for key, property_type in _PROPERTY_TYPES.items():
        if normalize_text(key) in text:
            return property_type
    return fallback


def _offer_type(value: Any, fallback: OfferType) -> OfferType:
    text = normalize_text(str(value or ""))
    if "wynajem" in text or "wynajmu" in text or "najem" in text:
        return "rent"
    if "sprzedaz" in text or "sprzedazy" in text:
        return "sale"
    return fallback


def _max_page(html: str) -> int | None:
    pages = [int(value) for value in PAGE_LINK_RE.findall(html)]
    return max(pages) if pages else None


class VileaAdapter:
    """Parse Vilea archive cards and RealEstateListing detail pages."""

    id = "vilea"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            name = self.regions.region(region).name
            location = quote(f"pl|{name}", safe="")
            for archive, offer_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{archive}",
                        url=f"{BASE_URL}/{archive}?localizations%5B0%5D={location}",
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return query
        parts = urlsplit(query.url)
        path = f"{parts.path.rstrip('/')}/page/{page}"
        url = f"{parts.scheme}://{parts.netloc}{path}"
        if parts.query:
            url = f"{url}?{parts.query}"
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        cards = tree.xpath('//div[contains(@class, "section-offer-with-img__wrapper")]')
        if not cards:
            raise ParseError("Vilea page has no listing cards")
        items = [self._summary(card, query) for card in cards]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=_max_page(html),
            total_items=None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _listing_payload(html)
        offers = _dict(payload.get("offers"))
        item = _dict(offers.get("itemOffered")) or _dict(payload.get("itemOffered"))
        address = _dict(item.get("address"))
        geo = _dict(item.get("geo"))

        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if lat is None or lng is None:
            lat, lng = summary.lat, summary.lng

        property_type = _ITEM_TYPE_TO_PROPERTY.get(
            str(item.get("@type") or "").lower(),
            summary.property_type,
        )
        offer_type = _offer_type(payload.get("name"), summary.offer_type)
        url = _text(payload.get("url")) or summary.url
        price = parse_price(offers.get("price")) or summary.price_pln

        city, district = self._locality(address.get("addressLocality"))
        province = self.regions.canonical_voivodeship(_text(address.get("addressRegion")))
        floor, floor_label = parse_floor(item.get("floorLevel"))

        offered_by = _dict(offers.get("offeredBy"))
        seller_type = "agency" if offered_by else summary.seller_type

        extra = dict(summary.extra)
        source_extra = dict(extra.get("vilea") or {})
        source_extra.update(
            {
                "property_kind": item.get("@type"),
                "amenities": self._amenities(item.get("amenityFeature")),
                "seller": offered_by.get("@id"),
            },
        )
        extra["vilea"] = source_extra

        return replace(
            summary,
            url=url,
            external_id=_external_id(url) or summary.external_id,
            title=_text(payload.get("name")) or summary.title,
            description=strip_html(_text(payload.get("description"))) or summary.description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind=summary.price_kind if price else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            area_m2=parse_area(_dict(item.get("floorSize")).get("value")) or summary.area_m2,
            rooms=parse_rooms(item.get("numberOfRooms")) or summary.rooms,
            floor=floor if floor is not None else summary.floor,
            floor_label=floor_label or summary.floor_label,
            voivodeship=province or summary.voivodeship,
            city=city or summary.city,
            district=district or summary.district,
            street=_text(address.get("streetAddress")) or summary.street,
            address_text=self._address_text(address) or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=self._images(payload.get("image")) or summary.images,
            seller_type=seller_type,
            seller_name=_text(offered_by.get("name")) or summary.seller_name,
            published_at=parse_datetime_ms(payload.get("datePosted")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        hrefs = [
            urljoin(BASE_URL, href)
            for href in card.xpath('.//a[contains(@href, "/nieruchomosc/")]/@href')
        ]
        url = hrefs[0] if hrefs else None
        if url is None:
            raise ParseError("Vilea card has no listing URL")

        location = _join_text(card.xpath('.//h3[contains(@class, "_left-location")]'))
        label = _join_text(card.xpath('.//h4[contains(@class, "_left-type")]'))
        description = _join_text(
            card.xpath('.//p[contains(@class, "_left-description")]'),
        )

        price_raw = None
        prices = card.xpath(".//*[@data-offer-prices]/@data-offer-prices")
        if prices:
            try:
                payload = json.loads(prices[0])
            except json.JSONDecodeError, TypeError:
                payload = None
            if isinstance(payload, dict):
                price_raw = payload.get("pln")

        area: float | None = None
        land_area: float | None = None
        rooms: int | None = None
        build_year: int | None = None
        for value in card.xpath('.//div[contains(@class, "_left-icon-wrapper")]//p'):
            text = _text(" ".join(value.text_content().split()))
            if text is None:
                continue
            if "m²" in text or "m2" in text:
                parsed = parse_area(text)
                if parsed is None:
                    continue
                if area is None:
                    area = parsed
                elif land_area is None:
                    land_area = parsed
            elif "pok" in text:
                rooms = parse_rooms(text)
            elif 4 <= len(text) <= 4 and text.isdigit():
                build_year = int(text)

        city, district = self._locality(location)
        property_type = _property_type(label, query.property_type)
        offer_type = _offer_type(label, query.offer_type)
        extra = {
            "vilea": {
                "label": label,
                "offer_prices": prices[0] if prices else None,
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=_external_id(url),
            url=url,
            title=" \u2013 ".join(part for part in (label, location) if part) or None,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking" if price_raw else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=parse_price(price_raw),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            build_year=build_year,
            city=city,
            district=district,
            address_text=", ".join(part for part in (district, city) if part) or None,
            images=self._images(card.xpath(".//img/@src")),
            seller_type="agency",
            extra=extra,
        )

    @staticmethod
    def _locality(value: Any) -> tuple[str | None, str | None]:
        text = _text(value)
        if text is None:
            return None, None
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return None, None
        return parts[0], ", ".join(parts[1:]) or None

    @staticmethod
    def _address_text(address: dict[str, Any]) -> str | None:
        street = _text(address.get("streetAddress"))
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
    def _amenities(value: Any) -> dict[str, str]:
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
