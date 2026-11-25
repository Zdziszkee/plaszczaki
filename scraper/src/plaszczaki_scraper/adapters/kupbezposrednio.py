"""KupBezpośrednio adapter (Next.js flight payloads + JSON-LD details).

The search page embeds the whole result set in the React Server Components
flight payload (``self.__next_f.push`` chunks). Each listing object already
carries coordinates, the description and the address, so the detail page only
needs to confirm them. Detail pages render the listing as JSON-LD
(``RealEstateListing``) plus the coordinates passed to the map component.
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

BASE_URL = "https://www.kupbezposrednio.pl"

_JSONLD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"')
_LISTINGS_RE = re.compile(
    r'\{\\"listings\\":\[(.*?)\],\\"total\\":(\d+),\\"perPage\\":(\d+)',
    re.DOTALL,
)
_COORDS_RE = re.compile(
    r'\\"lat\\":(-?\d+(?:\.\d+)?),\\"lng\\":(-?\d+(?:\.\d+)?)',
)
_HREF_RE = re.compile(r'href="(/listings/[^"]+)"')

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "plot": "plot",
    "land": "plot",
    "garage": "garage",
    "commercial": "commercial",
    "office": "commercial",
    "premises": "commercial",
    "room": "room",
}
_OFFER_TYPES: dict[str, OfferType] = {
    "sale": "sale",
    "rent": "rent",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | None:
    if isinstance(value, str) and value.startswith("$"):
        return None
    return safe_int(value)


def _jsonld(html: str) -> dict[str, Any]:
    for match in _JSONLD_RE.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "RealEstateListing":
                return item
    raise ParseError("KupBezpośrednio detail page is missing RealEstateListing JSON-LD")


def _listings(html: str) -> tuple[list[dict[str, Any]], int, int]:
    match = _LISTINGS_RE.search(html)
    if match is None:
        raise ParseError("KupBezpośrednio search page is missing the listings payload")
    try:
        entries = json.loads(f'"[{match.group(1)}]"')
        items = json.loads(entries)
    except json.JSONDecodeError as exc:
        raise ParseError(f"KupBezpośrednio listings payload is not valid JSON: {exc}") from exc
    if not isinstance(items, list):
        raise ParseError("KupBezpośrednio listings payload is not an array")
    return (
        [item for item in items if isinstance(item, dict)],
        safe_int(match.group(2)) or 0,
        safe_int(match.group(3)) or 0,
    )


class KupbezposrednioAdapter:
    """Parse KupBezpośrednio's server-rendered React payloads."""

    id = "kupbezposrednio"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for offer_type in ("sale", "rent"):
                queries.append(
                    Query(
                        key=f"{region}:{offer_type}",
                        url=f"{BASE_URL}/listings?listing_type={offer_type}",
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        raw_items, total, per_page = _listings(html)
        hrefs = self._hrefs(html)

        items: list[Listing] = []
        for raw in raw_items:
            if raw.get("is_demo"):
                continue
            listing = self._summary(raw, query, hrefs)
            if listing is None:
                continue
            items.append(listing)

        total_pages = None
        if total and per_page:
            total_pages = max(1, -(-total // per_page))

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total or None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _jsonld(html)
        coords = _COORDS_RE.search(html)
        lat = safe_float(coords.group(1)) if coords else None
        lng = safe_float(coords.group(2)) if coords else None
        if lat is None or lng is None:
            raise ParseError("KupBezpośrednio detail page is missing coordinates")

        offers = _as_dict(payload.get("offers"))
        floor_size = _as_dict(payload.get("floorSize"))
        address = _as_dict(payload.get("address"))

        price = parse_price(offers.get("price"))
        if price is None:
            price = parse_price(_as_dict(offers.get("priceSpecification")).get("price"))

        area = parse_area(floor_size.get("value"))
        land_area = summary.land_area_m2
        if summary.property_type == "plot":
            land_area = area or land_area
            area = None

        street = _text(address.get("streetAddress"))
        city = _text(address.get("addressLocality")) or summary.city
        province = _text(address.get("addressRegion"))
        voivodeship = self.regions.canonical_voivodeship(province) or summary.voivodeship

        images = summary.images
        if not images:
            og_image = _OG_IMAGE_RE.search(html)
            if og_image:
                images = [og_image.group(1)]

        description = strip_html(_text(payload.get("description"))) or summary.description
        address_text = ", ".join(part for part in (street, city) if part) or summary.address_text
        effective_price = price if price is not None else summary.price_pln

        return replace(
            summary,
            url=_text(payload.get("url")) or summary.url,
            title=_text(payload.get("name")) or summary.title,
            description=description,
            price_kind="asking" if effective_price is not None else "call",
            price_pln=effective_price,
            area_m2=area if area is not None else summary.area_m2,
            land_area_m2=land_area,
            rooms=parse_rooms(payload.get("numberOfRooms")) or summary.rooms,
            voivodeship=voivodeship,
            city=city,
            street=street or summary.street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source",
            images=images,
        )

    def _summary(
        self,
        raw: dict[str, Any],
        query: Query,
        hrefs: dict[str, str],
    ) -> Listing | None:
        external_id = _text(raw.get("id"))
        if external_id is None:
            return None

        lat = safe_float(raw.get("lat"))
        lng = safe_float(raw.get("lng"))
        province = _text(raw.get("address_province"))
        city = _text(raw.get("address_city"))
        region = self._region(province=province, city=city, lat=lat, lng=lng)
        if region is not None and region != query.region:
            return None
        if region is None and not self._in_scope(lat, lng):
            return None

        offer_type = _OFFER_TYPES.get(str(raw.get("listing_type")), query.offer_type)
        if offer_type != query.offer_type:
            return None
        property_type = _PROPERTY_TYPES.get(str(raw.get("property_type")), "other")
        market = self._market(raw.get("market"))

        price = parse_price(raw.get("price"))
        area = parse_area(raw.get("area"))
        land_area = None
        if property_type == "plot":
            land_area = area
            area = None

        street = _text(raw.get("address_street"))
        district = _text(raw.get("address_district"))
        address_text = ", ".join(part for part in (street, city) if part) or None

        owner = _as_dict(raw.get("owner"))
        images = [str(photo) for photo in (raw.get("photos") or []) if isinstance(photo, str)][:20]
        floor, floor_label = parse_floor(raw.get("floor"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=self._url(external_id, hrefs),
            title=_text(raw.get("title")),
            description=strip_html(_text(raw.get("description"))),
            offer_type=offer_type,
            property_type=property_type,
            market=market,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            rent_pln=parse_price(raw.get("rent_fee")),
            deposit_pln=parse_price(raw.get("deposit")),
            price_per_m2_pln=parse_price(raw.get("price_per_m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(raw.get("rooms_num")),
            floor=floor,
            floor_label=floor_label,
            build_year=_number(raw.get("building_year")),
            voivodeship=region,
            city=city,
            district=district,
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type="private",
            seller_name=_text(owner.get("name")),
            published_at=parse_datetime_ms(raw.get("created_at")),
            extra={
                "kupbezposrednio": {
                    "listing_type": raw.get("listing_type"),
                    "property_type": raw.get("property_type"),
                    "condition": raw.get("condition"),
                    "building_type": raw.get("building_type"),
                    "heating": raw.get("heating"),
                    "features": raw.get("features"),
                    "tier": raw.get("tier"),
                    "is_featured": raw.get("is_featured"),
                },
            },
        )

    def _region(
        self,
        *,
        province: str | None,
        city: str | None,
        lat: float | None,
        lng: float | None,
    ) -> str | None:
        region = self.regions.canonical_voivodeship(province)
        if region is None:
            region = self.regions.match_city(city)
        if region is None:
            region = self.regions.region_for_point(lat, lng)
        return region

    def _in_scope(self, lat: float | None, lng: float | None) -> bool:
        if lat is None or lng is None:
            return True
        return any(
            region.bbox[0] <= lng <= region.bbox[2] and region.bbox[1] <= lat <= region.bbox[3]
            for region in self.regions.config.regions.values()
        )

    @staticmethod
    def _market(value: Any) -> Market:
        text = str(value or "").strip().lower()
        if text in {"primary", "pierwotny"}:
            return "primary"
        if text in {"secondary", "wtorny", "wtórny"}:
            return "secondary"
        return "unknown"

    @staticmethod
    def _url(external_id: str, hrefs: dict[str, str]) -> str:
        suffix = external_id.replace("-", "")[:12]
        href = hrefs.get(suffix)
        if href:
            return f"{BASE_URL}{href}"
        return f"{BASE_URL}/listings/{external_id}"

    @staticmethod
    def _hrefs(html: str) -> dict[str, str]:
        index: dict[str, str] = {}
        for href in set(_HREF_RE.findall(html)):
            suffix = href.rsplit("-", 1)[-1]
            if len(suffix) == 12:
                index[suffix] = href
        return index
