"""nieruchomosciowi.pl adapter.

The portal documents a public, key-less read-only API
(https://nieruchomosciowi.pl/dla-programistow): ``/api/v1/listings`` for search
and ``/api/v1/listings/{slug}`` for details, and robots.txt explicitly allows
``/api/v1/``. Responses carry coordinates, address, description, parameters and
amenities, so no page rendering is needed.

The inventory is nationwide and small (54 active listings when this adapter was
written), so one paginated feed is crawled and the region scope is resolved per
listing. The API allows 60 requests per minute and per IP; keep ``rps`` at or
below 1.0. Expired listings answer HTTP 410.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_floor,
    parse_rooms,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://nieruchomosciowi.pl"
SEARCH_URL = f"{BASE_URL}/api/v1/listings?per_page=50"
PER_PAGE = 50

_OFFER_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "wynajem": "rent",
    "dzierzawa": "lease",
    "licytacja": "auction",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "dom": "house",
    "dzialka": "plot",
    "lokal": "commercial",
    "garaz": "garage",
    "pokoj": "room",
}

_MARKETS: dict[str, Market] = {
    "pierwotny": "primary",
    "wtorny": "secondary",
}

_SELLER_TYPES: dict[str, str] = {
    "owner": "private",
    "private": "private",
    "agency": "agency",
    "developer": "developer",
}


def _load_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text.startswith("{"):
        # Scrapling wraps JSON responses as ``<html><body>{...}</body></html>``.
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ParseError("nieruchomosciowi response does not contain a JSON object")
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"nieruchomosciowi response is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("nieruchomosciowi response is not an object")
    return payload


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
        return value
    return None


def _offer_type(value: Any, fallback: OfferType) -> OfferType:
    return _OFFER_TYPES.get(normalize_text(str(value or "")), fallback)


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    return _PROPERTY_TYPES.get(normalize_text(str(value or "")), fallback)


def _market(value: Any) -> Market:
    return _MARKETS.get(normalize_text(str(value or "")), "unknown")


def _seller_type(value: Any) -> str | None:
    return _SELLER_TYPES.get(normalize_text(str(value or "")))


def _price(payload: dict[str, Any]) -> int | None:
    currency = _text(payload.get("currency"))
    if currency is not None and currency != "PLN":
        return None
    amount = safe_float(payload.get("price"))
    return round(amount) if amount is not None and amount > 0 else None


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


class NieruchomosciowiAdapter:
    """Read the documented public listings API."""

    id = "nieruchomosciowi"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # One nationwide feed; the region scope is resolved per listing.
        requested = [region for region in regions if self.regions.has_region(region)]
        if not requested:
            return []
        return [
            Query(
                key="all",
                url=SEARCH_URL,
                region=requested[0],
                offer_type="other",
                property_type="other",
            ),
        ]

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def detail_url(self, summary: Listing) -> str:
        """Fetch the JSON detail endpoint, not the public HTML page."""
        return f"{BASE_URL}/api/v1/listings/{summary.external_id}"

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _load_json(html)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ParseError("nieruchomosciowi search response is missing items")

        return SearchPage(
            items=[self._summary(item, query) for item in raw_items if isinstance(item, dict)],
            current_page=safe_int(payload.get("page")) or query.page,
            total_pages=safe_int(payload.get("total_pages")),
            total_items=safe_int(payload.get("total")),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _load_json(html)
        slug = _text(payload.get("slug"))
        if slug is None:
            raise ParseError("nieruchomosciowi detail response is missing a slug")

        details = _dict(payload.get("details"))
        seller = _dict(payload.get("seller"))
        location = _dict(payload.get("location"))

        lat = safe_float(location.get("lat"))
        lng = safe_float(location.get("lng"))
        city = _text(location.get("city")) or summary.city
        district = _text(location.get("district")) or summary.district
        address_text = _text(location.get("address")) or summary.address_text

        voivodeship = summary.voivodeship
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)
        if voivodeship is None:
            voivodeship = self.regions.match_city(city)

        offer_type = _offer_type(payload.get("transaction"), summary.offer_type)
        property_type = _property_type(payload.get("property_type"), summary.property_type)

        area = parse_area(payload.get("area_m2")) or summary.area_m2
        land_area = parse_area(_first(details.get("plot_area_m2"), summary.land_area_m2))
        if property_type == "plot":
            land_area = _first(land_area, area)
            area = None
        elif property_type == "house":
            land_area = land_area or summary.land_area_m2

        floor, floor_label = parse_floor(_first(details.get("floor"), summary.floor))

        extra = dict(summary.extra)
        site_extra = dict(extra.get("nieruchomosciowi") or {})
        site_extra.update(
            {
                "address_exact": location.get("exact"),
                "details": details,
                "features": payload.get("features") or [],
                "previous_price": payload.get("previous_price"),
                "currency": payload.get("currency"),
            },
        )
        extra["nieruchomosciowi"] = site_extra

        return replace(
            summary,
            url=_text(payload.get("url")) or f"{BASE_URL}/oferta/{slug}",
            title=_text(payload.get("title")) or summary.title,
            description=_text(payload.get("description")),
            offer_type=offer_type,
            property_type=property_type,
            market=_market(details.get("market")),
            price_kind="asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=_price(payload),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(_first(payload.get("rooms"), summary.rooms)),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(details.get("year_built")),
            voivodeship=voivodeship,
            county=summary.county,
            commune=summary.commune,
            city=city,
            district=district,
            street=summary.street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=_images(payload.get("images")) or summary.images,
            seller_type=_seller_type(seller.get("kind")) or summary.seller_type,
            seller_name=_text(seller.get("name")) or summary.seller_name,
            published_at=parse_datetime_ms(payload.get("published_at")) or summary.published_at,
            modified_at=parse_datetime_ms(payload.get("updated_at")),
            extra=extra,
        )

    def _summary(self, item: dict[str, Any], query: Query) -> Listing:
        slug = _text(item.get("slug"))
        if slug is None:
            raise ParseError("nieruchomosciowi listing has no slug")

        location = _dict(item.get("location"))
        lat = safe_float(location.get("lat"))
        lng = safe_float(location.get("lng"))
        city = _text(location.get("city"))
        district = _text(location.get("district"))
        address_text = _text(location.get("address"))

        transaction = _text(item.get("transaction"))
        offer_type = _offer_type(transaction, query.offer_type)
        property_type = _property_type(item.get("property_type"), query.property_type)

        area = parse_area(item.get("area_m2"))
        land_area = None
        if property_type == "plot":
            land_area = area
            area = None

        return Listing(
            source_id=self.source_id,
            external_id=slug,
            url=_text(item.get("url")) or f"{BASE_URL}/oferta/{slug}",
            title=_text(item.get("title")),
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=_price(item),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(item.get("rooms")),
            voivodeship=self.regions.region_for_point(lat, lng) or self.regions.match_city(city),
            city=city,
            district=district,
            address_text=address_text
            or ", ".join(part for part in (district, city) if part)
            or city,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(item.get("images") or item.get("image")),
            seller_type=_seller_type(_dict(item.get("seller")).get("kind")),
            published_at=parse_datetime_ms(item.get("published_at")),
            modified_at=parse_datetime_ms(item.get("updated_at")),
            extra={
                "nieruchomosciowi": {
                    "transaction": transaction,
                    "address_exact": location.get("exact"),
                    "previous_price": item.get("previous_price"),
                    "featured": item.get("featured"),
                },
            },
        )
