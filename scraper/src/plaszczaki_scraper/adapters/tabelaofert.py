"""Tabelaofert.pl adapter (new-home investment catalogue).

Tabelaofert runs on the Next.js App Router.  The search page ships a schema.org
``AggregateOffer`` island inside the React Server Component flight payload; it
contains one entry per investment with coordinates, address, room/area ranges
and the price string.  Detail pages expose a schema.org ``Product`` with an
``AggregateOffer`` whose nested unit offers carry the exact "od" price.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import (
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://tabelaofert.pl"

REGION_SLUGS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}

_FLIGHT_PUSH = "self.__next_f.push("
_PRICE_PER_M2_RE = re.compile(
    r"(\d[\d\s\u00a0]{2,})\s*[\u2013-]\s*\d[\d\s\u00a0]{2,}\s*zł\s*/\s*m²",
)


def _flight_payload(html: str) -> str:
    """Concatenate every string chunk pushed by Next.js into the RSC stream."""
    chunks: list[str] = []
    position = 0
    while True:
        start = html.find(_FLIGHT_PUSH, position)
        if start < 0:
            break
        index = start + len(_FLIGHT_PUSH)
        depth = 0
        in_string = False
        escaped = False
        end = index
        while end < len(html):
            char = html[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char in "[{(":
                    depth += 1
                elif char in "]})":
                    depth -= 1
                    if depth == 0:
                        break
            end += 1
        try:
            payload = json.loads(html[index : end + 1])
        except json.JSONDecodeError as exc:
            raise ParseError(f"Tabelaofert flight payload is not valid JSON: {exc}") from exc
        for chunk in payload:
            if isinstance(chunk, str):
                chunks.append(chunk)
        position = end + 1
    if not chunks:
        raise ParseError("Tabelaofert page is missing the Next.js flight payload")
    return "".join(chunks)


def _json_objects_after(buffer: str, marker: str) -> list[dict[str, Any]]:
    """Extract every JSON object whose body contains ``marker``."""
    objects: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(marker), buffer):
        start = buffer.rfind("{", 0, match.start())
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(buffer)):
            char = buffer[index]
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
                        payload = json.loads(buffer[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(payload, dict):
                        objects.append(payload)
                    break
    return objects


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(node: dict[str, Any], key: str) -> float | None:
    parsed = safe_float(node.get(key))
    return parsed if parsed is not None and parsed > 0 else None


def _money(value: Any) -> int | None:
    """Parse a schema.org price that may be a plain decimal string."""
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return int(float(text))
    return parse_price(text)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _offer_entries(buffer: str) -> list[dict[str, Any]]:
    """Return the investment list stored in an ``AggregateOffer`` island."""
    for candidate in _json_objects_after(buffer, '"@type":"AggregateOffer"'):
        entries = candidate.get("offers")
        if not isinstance(entries, list) or not entries:
            continue
        first = entries[0]
        if isinstance(first, dict) and "/inwestycja/" in str(first.get("url", "")):
            return [
                entry
                for entry in entries
                if isinstance(entry, dict) and "/inwestycja/" in str(entry.get("url", ""))
            ]
    return []


def _product_ld(html: str) -> dict[str, Any]:
    tree = lxml_html.fromstring(html)
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(script.text or "")
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    raise ParseError("Tabelaofert detail page is missing the Product JSON-LD")


def _description(html: str, fallback: str | None) -> str | None:
    tree = lxml_html.fromstring(html)
    nodes = tree.xpath("//*[contains(@class, 'opisWithZgloszenie__text')]")
    if nodes:
        text = " ".join(nodes[0].text_content().split())
        if text:
            return text

    paragraphs = [
        " ".join(node.text_content().split())
        for node in tree.xpath("//p")
        if len(" ".join(node.text_content().split())) > 200
    ]
    if paragraphs:
        return "\n\n".join(dict.fromkeys(paragraphs))[:8000]
    return fallback


def _price_per_m2(html: str) -> int | None:
    """Read the published per-square-metre price range's lower bound."""
    match = _PRICE_PER_M2_RE.search(html)
    if match is None:
        return None
    return parse_price(match.group(1))


class TabelaofertAdapter:
    """Parse tabelaofert.pl investment listings."""

    id = "tabelaofert"

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
                    url=f"{BASE_URL}/nowe-mieszkania/{slug}",
                    region=region,
                    offer_type="sale",
                    property_type="flat",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        buffer = _flight_payload(html)
        entries = _offer_entries(buffer)
        if not entries:
            raise ParseError("Tabelaofert search payload is missing the investment list")

        items = [self._summary(entry, query) for entry in entries]
        pages = {int(match) for match in re.findall(r"\?page=(\d+)", html)}
        total_pages = max(pages) if pages else None

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        product = _product_ld(html)
        aggregate = _dict(product.get("offers"))
        units = [unit for unit in _list(aggregate.get("offers")) if isinstance(unit, dict)]

        price = _money(aggregate.get("lowPrice"))
        area = summary.area_m2
        rooms = summary.rooms
        unit_areas = [
            _value(_dict(_dict(unit.get("itemOffered")).get("floorSize")), "value")
            for unit in units
        ]
        unit_areas = [value for value in unit_areas if value is not None]
        if area is None and unit_areas:
            area = min(unit_areas)
        if rooms is None:
            unit_rooms = [
                parse_rooms(_dict(_dict(unit.get("itemOffered")).get("numberOfRooms")).get("value"))
                for unit in units
            ]
            rooms = next((value for value in unit_rooms if value), None)

        lat: float | None = None
        lng: float | None = None
        address: dict[str, Any] = {}
        for unit in units:
            offered = _dict(unit.get("itemOffered"))
            geo = _dict(offered.get("geo"))
            if lat is None:
                lat = safe_float(geo.get("latitude"))
            if lng is None:
                lng = safe_float(geo.get("longitude"))
            if not address:
                address = _dict(offered.get("address"))
            if lat is not None and lng is not None and address:
                break

        locality = _text(address.get("addressLocality")) or ""
        city, district = self._split_locality(locality)
        region = self.regions.canonical_voivodeship(_text(address.get("addressRegion")))
        street = _text(address.get("streetAddress"))
        address_text = ", ".join(part for part in (street, city) if part) or None

        price_per_m2 = _price_per_m2(html) or summary.price_per_m2_pln

        brand = _dict(product.get("brand"))
        title = _text(product.get("name"))
        tree = lxml_html.fromstring(html)
        headings = tree.xpath("//h1//text()")
        if headings:
            heading = " ".join(" ".join(headings).split())
            if heading:
                title = heading

        extra = dict(summary.extra)
        extra["tabelaofert"] = {
            "offer_count": safe_int(aggregate.get("offerCount")),
            "high_price": _money(aggregate.get("highPrice")),
            "available_units": len(units),
        }

        images = list(summary.images)
        product_image = _text(product.get("image"))
        if product_image:
            absolute = urljoin(BASE_URL, product_image)
            if absolute not in images:
                images.insert(0, absolute)

        return replace(
            summary,
            title=title or summary.title,
            description=_description(html, _text(product.get("description")))
            or summary.description,
            offer_type="sale",
            property_type=summary.property_type,
            market="primary",
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price or summary.price_pln,
            price_per_m2_pln=price_per_m2 or summary.price_per_m2_pln,
            area_m2=area,
            rooms=rooms,
            voivodeship=region or summary.voivodeship,
            county=summary.county,
            city=city or summary.city,
            district=district or summary.district,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type="developer",
            seller_name=_text(brand.get("name")) or summary.seller_name,
            extra=extra,
        )

    def _summary(self, entry: dict[str, Any], query: Query) -> Listing:
        url = _text(entry.get("url"))
        if not url:
            raise ParseError("Tabelaofert investment entry is missing its URL")
        external_id = url.rstrip("/").rsplit(",i", 1)[-1]
        offered = _dict(entry.get("itemOffered"))
        floor_size = _dict(offered.get("floorSize"))
        room_range = _dict(offered.get("numberOfRooms"))
        address = _dict(offered.get("address"))
        geo = _dict(offered.get("geo"))

        locality = _text(address.get("addressLocality")) or ""
        city, district = self._split_locality(locality)
        region = self.regions.canonical_voivodeship(_text(address.get("addressRegion")))
        street = _text(address.get("streetAddress"))

        price = _money(entry.get("price"))
        area = _value(floor_size, "minValue")
        rooms = parse_rooms(room_range.get("minValue"))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))

        image = _text(entry.get("image"))
        images = [urljoin(BASE_URL, image)] if image else []

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urljoin(BASE_URL, url),
            title=_text(entry.get("name")),
            description=strip_html(_text(entry.get("description"))),
            offer_type="sale",
            property_type="flat",
            market="primary",
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price,
            price_per_m2_pln=None,
            area_m2=area,
            rooms=rooms,
            voivodeship=region,
            city=city,
            district=district,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type="developer",
            seller_name=None,
            extra={
                "tabelaofert": {
                    "area_max": _value(floor_size, "maxValue"),
                    "rooms_max": parse_rooms(room_range.get("maxValue")),
                    "floor_level": _text(offered.get("floorLevel")),
                    "region_query": query.region,
                },
            },
        )

    @staticmethod
    def _split_locality(locality: str) -> tuple[str | None, str | None]:
        parts = [part.strip() for part in locality.split(",") if part.strip()]
        if not parts:
            return None, None
        if len(parts) == 1:
            return parts[0], None
        return parts[0], parts[1]
