"""Rynekpierwotny.pl adapter (new-home investment catalogue).

Search pages are server-rendered and embed the full Redux state in
``window.__INITIAL_STATE__``.  The investment listing lives under
``offerList.list`` and already carries coordinates, the description, the
vendor and the price/area/room ranges.  Detail pages additionally expose the
cheapest unit (``offerDetail.lowestPriceProperty``), which makes the "od"
(starting) price exact.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

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

BASE_URL = "https://rynekpierwotny.pl"
_STATE_MARKER = "window.__INITIAL_STATE__"

# Voivodeship id -> slug used in /s/nowe-mieszkania-<slug>/.
REGION_SLUGS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}

_INVESTMENT_TYPES: dict[int, PropertyType] = {
    1: "flat",
    2: "house",
}


def _extract_state(html: str) -> dict[str, Any]:
    """Return the object assigned to ``window.__INITIAL_STATE__``."""
    marker = html.find(_STATE_MARKER)
    if marker < 0:
        raise ParseError("Rynekpierwotny page is missing __INITIAL_STATE__")

    start = html.find("=", marker) + 1
    while start < len(html) and html[start] in " \t\r\n":
        start += 1
    if start >= len(html) or html[start] != "{":
        raise ParseError("Rynekpierwotny __INITIAL_STATE__ is not an object")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(html[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise ParseError(
                        f"Rynekpierwotny __INITIAL_STATE__ is not valid JSON: {exc}",
                    ) from exc
                if not isinstance(payload, dict):
                    raise ParseError("Rynekpierwotny __INITIAL_STATE__ is not an object")
                return payload

    raise ParseError("Rynekpierwotny __INITIAL_STATE__ is truncated")


def _first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class RynekpierwotnyAdapter:
    """Parse rynekpierwotny.pl investment listings."""

    id = "rynekpierwotny"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            slug = REGION_SLUGS.get(region)
            if slug is None:
                continue
            queries.append(
                Query(
                    key=f"{region}:nowe-mieszkania",
                    url=f"{BASE_URL}/s/nowe-mieszkania-{slug}/",
                    region=region,
                    offer_type="sale",
                    property_type="flat",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        state = _extract_state(html)
        offer_list = _dict(_dict(state.get("offerList")).get("list"))
        raw_items = offer_list.get("offers") or []
        if not isinstance(raw_items, list):
            raise ParseError("Rynekpierwotny offerList.offers is not a list")

        items = [
            self._summary(item, query)
            for item in raw_items
            if isinstance(item, dict) and item.get("id")
        ]

        total_items = safe_int(offer_list.get("count"))
        page_size = safe_int(offer_list.get("page_size")) or len(items) or None
        total_pages = None
        if total_items is not None and page_size:
            total_pages = math.ceil(total_items / page_size)
        # The paginator is authoritative when the payload is incomplete.
        linked_pages = [int(match) for match in re.findall(r"\?page=(\d+)", html)]
        if linked_pages:
            total_pages = max(total_pages or 0, max(linked_pages))

        return SearchPage(
            items=items,
            current_page=safe_int(offer_list.get("page")) or query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        state = _extract_state(html)
        detail = _dict(state.get("offerDetail"))
        offer = _dict(detail.get("offer"))
        if not offer:
            raise ParseError("Rynekpierwotny detail payload is missing the offer")

        lowest = _dict(detail.get("lowestPriceProperty"))
        stats = _dict(offer.get("stats"))
        region = _dict(offer.get("region"))
        location = self._location(region)

        coordinates = _dict(offer.get("geo_point")).get("coordinates")
        lat: float | None = None
        lng: float | None = None
        if isinstance(coordinates, list) and len(coordinates) == 2:
            lng = safe_float(coordinates[0])
            lat = safe_float(coordinates[1])

        price = parse_price(_first(lowest.get("price"), stats.get("ranges_price_min")))
        area = parse_area(_first(lowest.get("area"), stats.get("ranges_area_min")))
        rooms = parse_rooms(_first(lowest.get("rooms"), stats.get("ranges_rooms_min")))
        price_per_m2 = parse_price(
            _first(lowest.get("price_m2"), stats.get("ranges_price_m2_min")),
        )
        if price_per_m2 is None and price and area:
            price_per_m2 = round(price / area)

        property_type = _INVESTMENT_TYPES.get(safe_int(offer.get("type")) or 0, "flat")
        vendor = _dict(offer.get("vendor"))
        address = _text(offer.get("address"))
        street = _text(offer.get("street_name"))
        address_text = ", ".join(part for part in (street, location["city"]) if part)

        images = list(summary.images)
        main_image = _text(offer.get("main_image_url"))
        if main_image and main_image not in images:
            images.insert(0, main_image)

        extra = dict(summary.extra)
        extra["rynekpierwotny"] = {
            "investment_id": safe_int(offer.get("id")),
            "address": address,
            "construction_date_range": offer.get("construction_date_range"),
            "properties_for_sale_count": safe_int(offer.get("properties_for_sale_count")),
            "vendor_id": safe_int(vendor.get("id")),
            "region_id": safe_int(region.get("id")),
        }

        return replace(
            summary,
            url=_text(offer.get("url")) or summary.url,
            title=_text(offer.get("name")) or summary.title,
            description=strip_html(_text(offer.get("description"))) or summary.description,
            offer_type="sale",
            property_type=property_type,
            market="primary",
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area or summary.area_m2,
            rooms=rooms or summary.rooms,
            floor=safe_int(lowest.get("floor")),
            floor_label=_text(lowest.get("floor")),
            voivodeship=location["voivodeship"] or summary.voivodeship,
            county=location["county"] or summary.county,
            city=location["city"] or summary.city,
            district=location["district"] or summary.district,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            seller_type="developer",
            seller_name=_text(vendor.get("name")) or summary.seller_name,
            published_at=parse_datetime_ms(offer.get("create_date")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, offer: dict[str, Any], query: Query) -> Listing:
        offer_id = safe_int(offer.get("id"))
        vendor = _dict(offer.get("vendor"))
        slug = _text(offer.get("slug"))
        vendor_slug = _text(vendor.get("slug"))
        url = query.url
        if offer_id is not None and slug and vendor_slug:
            url = f"{BASE_URL}/oferty/{vendor_slug}/{slug}-{offer_id}/"
        else:
            raise ParseError(f"Rynekpierwotny offer {offer.get('id')} has no slug")

        stats = _dict(offer.get("stats"))
        region = _dict(offer.get("region"))
        location = self._location(region)

        coordinates = _dict(offer.get("geo_point")).get("coordinates")
        lat: float | None = None
        lng: float | None = None
        if isinstance(coordinates, list) and len(coordinates) == 2:
            lng = safe_float(coordinates[0])
            lat = safe_float(coordinates[1])

        price = parse_price(stats.get("ranges_price_min"))
        price_per_m2 = parse_price(stats.get("ranges_price_m2_min"))
        area = parse_area(stats.get("ranges_area_min"))
        rooms = parse_rooms(stats.get("ranges_rooms_min"))
        if price_per_m2 is None and price and area:
            price_per_m2 = round(price / area)

        images: list[str] = []
        gallery = offer.get("gallery")
        if isinstance(gallery, list):
            for image in gallery:
                image_url = _text(_dict(image).get("image_url"))
                if image_url and image_url not in images:
                    images.append(image_url)
        main_image = _text(offer.get("main_image_url"))
        if main_image and main_image not in images:
            images.insert(0, main_image)

        street = _text(offer.get("street_name"))
        address_text = ", ".join(part for part in (street, location["city"]) if part)

        property_type = _INVESTMENT_TYPES.get(safe_int(offer.get("type")) or 0, "flat")

        return Listing(
            source_id=self.source_id,
            external_id=str(offer_id),
            url=url,
            title=_text(offer.get("name")),
            description=strip_html(_text(offer.get("description"))),
            offer_type="sale",
            property_type=property_type,
            market="primary",
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=rooms,
            voivodeship=location["voivodeship"],
            county=location["county"],
            city=location["city"],
            district=location["district"],
            street=street,
            address_text=address_text or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            seller_type="developer",
            seller_name=_text(vendor.get("name")),
            extra={
                "rynekpierwotny": {
                    "investment_id": offer_id,
                    "vendor_id": safe_int(vendor.get("id")),
                    "region_id": safe_int(region.get("id")),
                    "properties_count_for_sale": safe_int(
                        stats.get("properties_count_for_sale"),
                    ),
                },
            },
        )

    def _location(self, region: dict[str, Any]) -> dict[str, str | None]:
        stats = _dict(region.get("stats"))
        direct_voivodeship = _text(region.get("voivodeship"))
        direct_city = _first(_text(region.get("city")), _text(region.get("town")))
        direct_district = _text(region.get("district"))

        type_voivodeship = _dict(stats.get("region_type_voivodeship"))
        type_city = _first(
            _dict(stats.get("region_type_city")).get("name"),
            _dict(stats.get("region_type_town")).get("name"),
        )
        type_district = _dict(stats.get("region_type_district")).get("name")

        voivodeship = self.regions.canonical_voivodeship(
            _text(_first(direct_voivodeship, type_voivodeship.get("name"))),
        )
        city = _text(_first(direct_city, type_city))
        district = _text(_first(direct_district, type_district))

        full_name = _text(region.get("full_name"))
        if voivodeship is None and full_name:
            voivodeship = self.regions.canonical_voivodeship(full_name.split(",", 1)[0])
        if city is None and full_name:
            parts = [part.strip() for part in full_name.split(",") if part.strip()]
            if len(parts) >= 2:
                city = parts[1]

        county = self._county(region)

        return {
            "voivodeship": voivodeship,
            "county": county,
            "city": city,
            "district": district if district and district != city else None,
        }

    @staticmethod
    def _county(region: dict[str, Any]) -> str | None:
        stats = _dict(region.get("stats"))
        town = _text(region.get("town")) or _text(
            _dict(stats.get("region_type_town")).get("name"),
        )
        short_name = _text(region.get("short_name"))
        if not town or not short_name or "," not in short_name:
            return None
        first = short_name.split(",", 1)[0].strip()
        if not first or normalize_text(first) == normalize_text(town):
            return None
        return first
