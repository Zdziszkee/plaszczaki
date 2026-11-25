"""GetHome adapter (search pages + detail pages).

GetHome renders its whole page state into ``window.__INITIAL_STATE__`` on both
search and detail pages, so no JavaScript execution is needed.  The search
payload already carries coordinates, the listing description, price and
location; the detail payload adds the land size, the full description and the
authoritative location path.

``https://gethome.pl/robots.txt`` disallows ``*/?page=`` but explicitly allows
``?page=2`` through ``?page=6``, so pagination is capped at six pages here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

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

BASE_URL = "https://gethome.pl"

# robots.txt: "Disallow: */?page=" with "Allow: */?page=2$" .. "?page=6$".
MAX_PAGE = 6

STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
SLUG_ID_RE = re.compile(r"-(\d+)$")

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "na-sprzedaz", "sale", "flat"),
    ("mieszkania", "do-wynajecia", "rent", "flat"),
    ("domy", "na-sprzedaz", "sale", "house"),
    ("domy", "do-wynajecia", "rent", "house"),
    ("dzialki", "", "sale", "plot"),
)

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "lot": "plot",
    "plot": "plot",
    "commercial": "commercial",
    "premises": "commercial",
    "garage": "garage",
    "room": "room",
}

_DEAL_TYPES: dict[str, OfferType] = {
    "sell": "sale",
    "sale": "sale",
    "rent": "rent",
    "najem": "rent",
    "lease": "lease",
}

_MARKETS: dict[str, Market] = {
    "aftermarket": "secondary",
    "secondary": "secondary",
    "primary_market": "primary",
    "primary": "primary",
}

_LOCATION_LEVELS: dict[str, str] = {
    "wojewodztwo": "province",
    "miasto": "city",
    "miasto na prawach powiatu": "city",
    "gmina": "commune",
    "dzielnica": "district",
}


def _state(html: str) -> dict[str, Any]:
    match = STATE_RE.search(html)
    if match is None:
        raise ParseError("GetHome page is missing window.__INITIAL_STATE__")
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError as exc:
        raise ParseError(f"GetHome state is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("GetHome state is not an object")
    return payload


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _levels(location: Any) -> dict[str, str]:
    levels: dict[str, str] = {}
    for entry in _dict(location).get("path") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        key = _LOCATION_LEVELS.get(normalize_text(kind)) if isinstance(kind, str) else None
        name = _text(entry.get("name"))
        if key and name and key not in levels:
            levels[key] = name
    return levels


def _price_parts(value: Any) -> tuple[int | None, int | None, int | None]:
    """Return (total, per_sqm, per_month) from GetHome price payloads."""
    if isinstance(value, dict):
        return (
            parse_price(value.get("total")),
            parse_price(value.get("per_sqm")),
            parse_price(value.get("per_month")),
        )
    return parse_price(value), None, None


def _images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    images: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = (
            item.get("o_img_500")
            or item.get("o_img_800")
            or item.get("o_img_1500")
            or item.get("link")
        )
        if isinstance(url, str) and url:
            images.append(url)
    return images[:20]


def _agent_name(agent: dict[str, Any]) -> str | None:
    parts = [_text(agent.get("name")), _text(agent.get("last_name"))]
    joined = " ".join(part for part in parts if part)
    return joined or None


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    text = _text(value)
    return _PROPERTY_TYPES.get(normalize_text(text), fallback) if text else fallback


def _offer_type(value: Any, fallback: OfferType) -> OfferType:
    text = _text(value)
    return _DEAL_TYPES.get(normalize_text(text), fallback) if text else fallback


def _market(value: Any) -> Market | None:
    text = _text(value)
    return _MARKETS.get(normalize_text(text)) if text else None


def _seller(item: dict[str, Any]) -> tuple[str, str | None]:
    agency = _dict(item.get("agency"))
    if item.get("is_private"):
        return "private", _agent_name(_dict(item.get("agent")))
    if agency:
        seller_type = "developer" if agency.get("type") == "developer" else "agency"
        return seller_type, _text(agency.get("name"))
    return "unknown", _agent_name(_dict(item.get("agent")))


class GetHomeAdapter:
    """Parse GetHome's server-rendered state payloads."""

    id = "gethome"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            if not self.regions.has_region(region):
                continue
            for category, deal, offer_type, property_type in CATEGORIES:
                path = f"{category}/{deal}/" if deal else f"{category}/"
                queries.append(
                    Query(
                        key=f"{region}:{category}:{deal or 'sprzedaz'}",
                        url=f"{BASE_URL}/{path}{region}/",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=1)
        return query.with_page(min(page, MAX_PAGE))

    def parse_search(self, html: str, query: Query) -> SearchPage:
        state = _state(html)
        container = _dict(_dict(state.get("offerList")).get("offers"))
        raw_items = container.get("offers")
        if not isinstance(raw_items, list):
            raise ParseError("GetHome search payload is missing offers")
        meta = _dict(container.get("meta"))
        total_pages = safe_int(container.get("pageCount"))
        return SearchPage(
            items=[self._summary(item, query) for item in raw_items if isinstance(item, dict)],
            current_page=safe_int(container.get("page")) or query.page,
            total_pages=min(total_pages, MAX_PAGE) if total_pages else None,
            total_items=safe_int(meta.get("total_offers")),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        state = _state(html)
        offer = _dict(_dict(state.get("offer")).get("offer"))
        if not offer:
            raise ParseError("GetHome detail payload is missing the offer")

        property_ = _dict(offer.get("property"))
        levels = _levels(property_.get("location"))
        address = _dict(property_.get("address_details"))

        coordinates = _dict(offer.get("coordinates"))
        lat = safe_float(coordinates.get("lat"))
        lng = safe_float(coordinates.get("lng"))

        price, price_per_m2, per_month = _price_parts(offer.get("price"))
        offer_type = _offer_type(offer.get("deal_type"), summary.offer_type)
        property_type = _property_type(property_.get("type"), summary.property_type)

        seller_type, seller_name = _seller(offer)

        size = parse_area(property_.get("size"))
        lot_size = parse_area(property_.get("lot_size"))
        if property_type == "plot":
            size, lot_size = None, lot_size or size

        floor, floor_label = parse_floor(property_.get("floor"))
        voivodeship = None
        if levels.get("province"):
            voivodeship = self.regions.canonical_voivodeship(levels["province"])
        if voivodeship is None:
            voivodeship = summary.voivodeship

        extra = dict(summary.extra)
        extra["gethome"] = {
            "offer_number": _text(offer.get("offer_number")),
            "offer_type": offer.get("offer_type"),
            "investment_id": offer.get("investment_id"),
            "coordinates_accurate": offer.get("coordinates_accurate"),
        }

        return replace(
            summary,
            title=_text(offer.get("title")) or _text(offer.get("name")) or summary.title,
            description=strip_html(_text(offer.get("description"))) or summary.description,
            offer_type=offer_type,
            property_type=property_type,
            market=_market(offer.get("market_type")) or summary.market,
            price_kind="asking" if price is not None else summary.price_kind,
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price if price is not None else summary.price_pln,
            rent_pln=per_month,
            price_per_m2_pln=price_per_m2 if price_per_m2 is not None else summary.price_per_m2_pln,
            area_m2=size if size is not None else summary.area_m2,
            land_area_m2=lot_size if lot_size is not None else summary.land_area_m2,
            rooms=parse_rooms(property_.get("rooms")) or summary.rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(property_.get("building_year")),
            voivodeship=voivodeship,
            commune=_text(levels.get("commune")),
            city=_text(address.get("city")) or _text(levels.get("city")) or summary.city,
            district=_text(address.get("district")) or _text(levels.get("district")),
            street=_text(address.get("street")),
            address_text=_text(property_.get("address")) or summary.address_text,
            lat=lat if lat is not None else summary.lat,
            lng=lng if lng is not None else summary.lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            seller_type=seller_type,
            seller_name=seller_name,
            modified_at=parse_datetime_ms(offer.get("updated_at")),
            extra=extra,
        )

    def _summary(self, item: dict[str, Any], query: Query) -> Listing:
        slug = _text(item.get("slug"))
        if slug is None:
            raise ParseError(f"GetHome offer {item.get('id')} has no slug")
        match = SLUG_ID_RE.search(slug)
        external_id = match.group(1) if match else _text(item.get("id"))
        if external_id is None:
            raise ParseError(f"GetHome offer {slug} has no identifier")

        property_ = _dict(item.get("property"))
        levels = _levels(property_.get("location"))
        address = _dict(property_.get("address_details"))
        coordinates = _dict(item.get("coordinates"))
        lat = safe_float(coordinates.get("lat"))
        lng = safe_float(coordinates.get("lon"))

        price, price_per_m2, _ = _price_parts(item.get("price"))
        show_price = item.get("show_price")
        if show_price is False:
            price, price_per_m2 = None, None
        price_kind = (
            "asking" if price is not None else ("call" if show_price is False else "unknown")
        )

        offer_type = _offer_type(item.get("deal_type"), query.offer_type)
        property_type = _property_type(property_.get("type"), query.property_type)

        size = parse_area(property_.get("size"))
        lot_size = parse_area(property_.get("lot_size"))
        if property_type == "plot":
            size, lot_size = None, lot_size or size

        voivodeship = None
        if levels.get("province"):
            voivodeship = self.regions.canonical_voivodeship(levels["province"])
        if voivodeship is None:
            voivodeship = query.region

        seller_type, seller_name = _seller(item)

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=f"{BASE_URL}/oferta/{slug}/",
            title=_text(item.get("name")) or _text(property_.get("name")),
            description=strip_html(_text(item.get("description"))),
            offer_type=offer_type,
            property_type=property_type,
            market=_market(item.get("market_type")) or "unknown",
            price_kind=price_kind,
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=size,
            land_area_m2=lot_size,
            rooms=parse_rooms(property_.get("room_number") or property_.get("rooms")),
            build_year=safe_int(property_.get("building_year")),
            voivodeship=voivodeship,
            commune=_text(levels.get("commune")),
            city=_text(address.get("city")) or _text(levels.get("city")),
            district=_text(address.get("district")) or _text(levels.get("district")),
            street=_text(address.get("street")),
            address_text=_text(property_.get("address")),
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(item.get("pictures")),
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=parse_datetime_ms(item.get("created_at")),
            modified_at=parse_datetime_ms(item.get("updated_at")),
            extra={
                "gethome": {
                    "offer_type": item.get("offer_type"),
                    "market_type": item.get("market_type"),
                    "investment_id": item.get("investment_id"),
                    "is_private": item.get("is_private"),
                },
            },
        )
