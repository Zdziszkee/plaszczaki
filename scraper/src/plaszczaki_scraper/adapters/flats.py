"""Flats.pl adapter (embedded Next.js listing data + JSON-LD fallback)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

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
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://flats.pl"
IMAGE_BASE = "https://d1yl0ebouq9os5.cloudfront.net"

# The city HTML page only embeds the first 16 cards (Kraków has 33 active
# offers), and /api/ is disallowed by robots.txt. The allowed listings sitemap
# carries the complete active inventory, so discovery reads it and the runner
# hydrates each detail page.
SITEMAP_URL = f"{BASE_URL}/sitemaps/sitemap/listings.xml"

_NEXT_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)',
    re.DOTALL,
)
_LISTING_START_RE = re.compile(r'\{"id":"[0-9a-fA-F-]{36}"')
_OBJECT_URL_RE = re.compile(r"https://flats\.pl/pl/ogloszenia/[^\"'\\\s]+")
_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "studio": "flat",
    "room": "room",
    "house": "house",
}
_OWNER_TYPES: dict[str, str] = {
    "landlord": "private",
    "private": "private",
    "agency": "agency",
    "developer": "developer",
}


class FlatsAdapter:
    """Parse Flats.pl's embedded RSC listing objects."""

    id = "flats"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions
        # Use every configured city in both voivodeships, not just the largest
        # few, so smaller towns are not dropped from the national sitemap.
        self._city_region: dict[str, str] = {}
        self._city_names: dict[str, str] = {}
        for region in regions.config.regions.values():
            for city in region.cities:
                slug = normalize_text(city).replace(" ", "-")
                if slug:
                    self._city_region[slug] = region.id
                    self._city_names[slug] = city

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # One national sitemap; parse_search keeps only the configured cities.
        return [
            Query(
                key="sitemap:wynajem",
                url=SITEMAP_URL,
                region=",".join(regions) or "national",
                offer_type="rent",
                property_type="flat",
            ),
        ]

    def with_page(self, query: Query, page: int) -> Query:
        # The sitemap is a single unpaginated document.
        return replace(query, page=1)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        sitemap_items = self._sitemap_listings(html, query)
        if sitemap_items is not None:
            return sitemap_items

        objects = _listing_objects(html)
        if not objects:
            item_list = _json_ld_item_list(html)
            if item_list is not None and not item_list.get("itemListElement"):
                return SearchPage(items=[], current_page=query.page, total_pages=query.page)
            raise ParseError("Flats search page is missing embedded listing data")

        url_map = _url_map(html)
        items: list[Listing] = []
        seen: set[str] = set()
        for data in objects:
            external_id = _text(data.get("publicId")) or _text(data.get("id"))
            if external_id is None or external_id in seen:
                continue
            url = url_map.get(external_id)
            if url is None:
                continue
            seen.add(external_id)
            items.append(self._summary(data, url, query))

        return SearchPage(items=items, current_page=query.page, total_pages=query.page)

    def _url_city(self, url: str) -> str | None:
        """Match a listing URL to the closest configured city slug."""
        path = urlsplit(url).path
        matches = [
            (path.rfind(f"-{city}-"), city) for city in self._city_region if f"-{city}-" in path
        ]
        if not matches:
            return None
        return max(matches)[1]

    def _sitemap_listings(self, html: str, query: Query) -> SearchPage | None:
        if "<urlset" not in html and "<loc>" not in html:
            return None

        wanted = set(query.region.split(","))
        items: list[Listing] = []
        seen: set[str] = set()
        for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", html):
            if "/pl/ogloszenia/" not in url or url in seen:
                continue
            city = self._url_city(url)
            if city is None or self._city_region.get(city) not in wanted:
                continue
            seen.add(url)
            items.append(
                Listing(
                    source_id=self.source_id,
                    external_id=url.rstrip("/").rsplit("-", 1)[-1],
                    url=url,
                    title=None,
                    offer_type="rent",
                    property_type="room" if "/pokoj-" in url else "flat",
                    price_kind="call",
                    price_unit="monthly",
                    price_pln=None,
                    voivodeship=self._city_region.get(city),
                    city=self._city_names.get(city),
                    lat=None,
                    lng=None,
                    images=[],
                    seller_type=None,
                    extra={"flats": {"sitemap": True}},
                ),
            )

        return SearchPage(
            items=items,
            current_page=1,
            total_pages=1,
            total_items=len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        data = _detail_object(html)
        if data is None:
            data = _json_ld_detail(html)
        if data is None:
            raise ParseError("Flats detail page is missing embedded listing data")

        city = _text(data.get("locationCity")) or summary.city
        street = _text(data.get("locationStreetName")) or summary.street
        district = _text(data.get("locationDistrict")) or summary.district
        lat = safe_float(data.get("latitude"))
        lng = safe_float(data.get("longitude"))
        if lat is None or lng is None:
            lat = summary.lat
            lng = summary.lng

        province = _clean_province(_text(data.get("addressRegion")) or _address_region(data))
        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None:
            if self.regions.is_known_voivodeship(province):
                voivodeship = self.regions.scoped_voivodeship(province)
            else:
                match = self.regions.resolve(
                    city=city,
                    county=summary.county,
                    lat=lat,
                    lng=lng,
                )
                voivodeship = match.voivodeship if match else summary.voivodeship

        price = parse_price(data.get("price")) or summary.price_pln
        description = strip_html(_text(data.get("description")))
        images = _images(data.get("pictures")) or _images(data.get("image"))

        extra = dict(summary.extra)
        extra["flats"] = {
            "available_from": data.get("availableFrom"),
            "charges": data.get("charges"),
            "bills_included": data.get("billsIncluded"),
            "ownership_verified": data.get("ownershipVerified"),
        }

        return replace(
            summary,
            url=_text(data.get("url")) or summary.url,
            title=_text(data.get("title")) or summary.title,
            description=description or summary.description,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly",
            price_pln=price,
            rent_pln=parse_price(data.get("charges")),
            deposit_pln=parse_price(data.get("securityDeposit")),
            area_m2=parse_area(data.get("area")) or summary.area_m2,
            rooms=parse_rooms(data.get("rooms")) or summary.rooms,
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=street,
            address_text=_text(data.get("address"))
            or ", ".join(part for part in (street, city) if part)
            or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type=_OWNER_TYPES.get(normalize_text(str(data.get("ownerType")))),
            published_at=parse_datetime_ms(data.get("createdAt")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, data: dict[str, Any], url: str, query: Query) -> Listing:
        city = _text(data.get("locationCity"))
        street = _text(data.get("locationStreetName"))
        lat = safe_float(data.get("latitude"))
        lng = safe_float(data.get("longitude"))
        price = parse_price(data.get("price"))

        match = self.regions.resolve(city=city, lat=lat, lng=lng)
        voivodeship = match.voivodeship if match else None

        return Listing(
            source_id=self.source_id,
            external_id=_text(data.get("publicId")) or _text(data.get("id")) or "",
            url=url,
            title=_text(data.get("title")),
            offer_type="rent",
            property_type=_property_type(data.get("type"), query.property_type),
            market="secondary",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly",
            price_pln=price,
            area_m2=parse_area(data.get("area")),
            rooms=parse_rooms(data.get("rooms")),
            voivodeship=voivodeship,
            city=city,
            district=_text(data.get("locationDistrict")),
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(data.get("pictures")),
            seller_type=_OWNER_TYPES.get(normalize_text(str(data.get("ownerType")))),
            published_at=parse_datetime_ms(data.get("createdAt")),
            extra={
                "flats": {
                    "search_key": query.key,
                    "has_living_room": data.get("hasLivingRoom"),
                    "pets_allowed": data.get("petsAllowed"),
                },
            },
        )


def _flight_text(html: str) -> str:
    chunks: list[str] = []
    for match in _NEXT_PUSH_RE.finditer(html):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(chunks)


def _balanced_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
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
                return text[start : index + 1]
    return None


def _listing_objects(html: str) -> list[dict[str, Any]]:
    text = _flight_text(html)
    objects: list[dict[str, Any]] = []
    for match in _LISTING_START_RE.finditer(text):
        raw = _balanced_object(text, match.start())
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "price" in payload and "latitude" in payload:
            objects.append(payload)
    return objects


def _detail_object(html: str) -> dict[str, Any] | None:
    for data in _listing_objects(html):
        if "description" in data:
            return data
    return None


def _json_ld(html: str) -> list[dict[str, Any]]:
    try:
        tree = lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Flats page is not valid HTML: {exc}") from exc

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


def _json_ld_item_list(html: str) -> dict[str, Any] | None:
    for item in _json_ld(html):
        if item.get("@type") == "ItemList":
            return item
    return None


def _json_ld_detail(html: str) -> dict[str, Any] | None:
    for item in _json_ld(html):
        if item.get("@type") != "RealEstateListing":
            continue
        about = _dict(item.get("about"))
        offers = _dict(item.get("offers"))
        address = _dict(about.get("address"))
        return {
            "title": _text(item.get("name")),
            "description": _text(item.get("description")),
            "url": _text(item.get("url")),
            "price": offers.get("price"),
            "area": _dict(about.get("floorSize")).get("value"),
            "rooms": about.get("numberOfRooms"),
            "locationCity": _text(address.get("addressLocality")),
            "locationStreetName": _text(address.get("streetAddress")),
            "locationDistrict": None,
            "addressRegion": _text(address.get("addressRegion")),
            "image": item.get("image"),
            "createdAt": item.get("datePosted"),
        }
    return None


def _url_map(html: str) -> dict[str, str]:
    urls = set(_OBJECT_URL_RE.findall(html))
    for match in re.finditer(r'href="(/pl/ogloszenia/[^"]+)"', html):
        urls.add(f"{BASE_URL}{match.group(1)}")

    mapping: dict[str, str] = {}
    for url in urls:
        clean = url.rstrip("/")
        public_id = clean.rsplit("-", 1)[-1]
        if public_id:
            mapping[public_id] = clean
    return mapping


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
        url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
        if not url:
            continue
        if url.startswith("/listings/"):
            url = f"{IMAGE_BASE}{url}"
        if url.startswith("https://") and url not in images:
            images.append(url)
        if len(images) >= 20:
            break
    return images


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _address_region(data: dict[str, Any]) -> str | None:
    address = data.get("address")
    if isinstance(address, dict):
        return _text(address.get("addressRegion"))
    return None


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
