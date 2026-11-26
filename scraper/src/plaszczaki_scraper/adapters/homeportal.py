"""HomePortal.pl adapter (JSON-LD backed search and detail pages)."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from lxml import html as lxml_html

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
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://homeportal.pl"

# (URL slug, transaction, offer type, property type)
CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("lokale", "sprzedaz", "sale", "commercial"),
    ("lokale", "wynajem", "rent", "commercial"),
    ("dzialki", "sprzedaz", "sale", "plot"),
    ("hale-magazyny", "sprzedaz", "sale", "commercial"),
    ("hale-magazyny", "wynajem", "rent", "commercial"),
    ("obiekty", "sprzedaz", "sale", "commercial"),
    ("obiekty", "wynajem", "rent", "commercial"),
)

_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_OFFER_HREF_RE = re.compile(r"/[^/]+-[A-Z]{2}-\d+/?$")
_OFFER_ID_RE = re.compile(r"([A-Za-z0-9]+-[A-Z]{2}-\d+)")
_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_AREA_RE = re.compile(r"Powierzchnia\s+([\d\s.,]+)\s*m", re.IGNORECASE)
_ROOMS_RE = re.compile(r"Ilość pokoi\s+(\d+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"Rok Budowy\s+(\d{4})", re.IGNORECASE)
_PER_PAGE = 10


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _text(node: Any) -> str | None:
    if node is None:
        return None
    return _clean(node.text_content())


def _first_text(nodes: list[Any]) -> str | None:
    return _text(nodes[0]) if nodes else None


def _json_ld(tree: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            continue
        if isinstance(payload, list):
            blocks.extend(entry for entry in payload if isinstance(entry, dict))
        elif isinstance(payload, dict):
            blocks.append(payload)
    return blocks


def _find_typed(tree: Any, type_name: str) -> dict[str, Any]:
    for payload in _json_ld(tree):
        types = payload.get("@type")
        type_list = types if isinstance(types, list) else [types]
        if any(isinstance(item, str) and type_name in item for item in type_list):
            return payload
    return {}


def _item_list(tree: Any) -> dict[str, Any]:
    payload = _find_typed(tree, "ItemList")
    return payload if isinstance(payload.get("itemListElement"), list) else {}


def _parse_pl_date(value: str | None) -> int | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        return None


class HomeportalAdapter:
    """Parse HomePortal's JSON-LD data plus its server-rendered cards."""

    id = "homeportal"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for slug, transaction, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{transaction}:{slug}",
                        url=f"{BASE_URL}/{transaction}/{slug}/{region}/",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath('//article[contains(@class, "property-card")]')

        item_list = _item_list(tree)
        names: dict[str, str] = {}
        for entry in item_list.get("itemListElement", []):
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            name = entry.get("name")
            if isinstance(url, str) and isinstance(name, str):
                names[url.rstrip("/")] = name

        items = [self._summary(card, query, names) for card in cards]
        total_items = safe_int(item_list.get("numberOfItems"))
        total_pages = self._total_pages(tree, total_items, query.page)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        listing = _find_typed(tree, "RealEstateListing")
        entity = listing.get("mainEntity")
        if not isinstance(entity, dict):
            entity = {}
        if not entity and not tree.xpath('//div[@id="offerDescription"]'):
            raise ParseError("HomePortal detail page has no structured listing data")

        geo = entity.get("geo")
        lat = safe_float(geo.get("latitude")) if isinstance(geo, dict) else None
        lng = safe_float(geo.get("longitude")) if isinstance(geo, dict) else None

        address = entity.get("address")
        if not isinstance(address, dict):
            address = {}
        province = _clean(address.get("addressRegion"))
        city = _clean(address.get("addressLocality"))

        breadcrumbs = self._breadcrumbs(tree)
        district = None
        if len(breadcrumbs) >= 2:
            district = breadcrumbs[-1]
        if city is None and len(breadcrumbs) >= 2:
            city = breadcrumbs[-2]
        if province is None and len(breadcrumbs) >= 3:
            province = breadcrumbs[-3]

        properties = self._additional_properties(entity)
        market = self._market(properties.get("rynek"))

        area = parse_area(
            (entity.get("floorSize") or {}).get("value")
            if isinstance(entity.get("floorSize"), dict)
            else None,
        )
        if area is None:
            area = parse_area(properties.get("powierzchnia"))
        land_area = parse_area(properties.get("powierzchnia dzialki"))
        if summary.property_type == "plot" and area is not None and land_area is None:
            land_area, area = area, None

        price = parse_price((listing.get("offers") or {}).get("price"))
        per_m2 = None
        if price is not None and area:
            per_m2 = round(price / area)

        floor, floor_label = parse_floor(entity.get("floorLevel"))
        images = self._images(listing)

        seller = (listing.get("offers") or {}).get("seller")
        seller_name = _clean(seller.get("name")) if isinstance(seller, dict) else None
        seller_type = None
        if isinstance(seller, dict):
            seller_types = seller.get("@type")
            seller_type = (
                "agency"
                if "RealEstateAgent" in str(seller_types) or "Organization" in str(seller_types)
                else "private"
            )

        voivodeship = self.regions.scoped_voivodeship(province, summary.voivodeship)
        description = _first_text(tree.xpath('//div[@id="offerDescription"]'))

        extra = dict(summary.extra)
        extra["homeportal"] = {
            "identifier": listing.get("identifier") or summary.external_id,
            "market": properties.get("rynek"),
            "building_type": properties.get("rodzaj"),
            "deposit": properties.get("kaucja"),
        }

        return replace(
            summary,
            title=_clean(listing.get("name")) or summary.title,
            description=description,
            market=market,
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if summary.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(entity.get("numberOfRooms")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(entity.get("yearBuilt")),
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=None,
            address_text=", ".join(part for part in (district, city) if part) or city,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=parse_datetime_ms(listing.get("datePosted")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query, names: dict[str, str]) -> Listing:
        href = ""
        for candidate in card.xpath(".//a/@href"):
            if isinstance(candidate, str) and _OFFER_HREF_RE.search(candidate):
                href = candidate
                break
        if not href:
            raise ParseError("HomePortal listing card is missing its offer link")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        text = card.text_content()
        id_match = _OFFER_ID_RE.search(text)
        external_id = id_match.group(1) if id_match else url.rstrip("/").rsplit("/", 1)[-1]

        price = parse_price(_first_text(card.xpath(".//h3")))
        area_match = _AREA_RE.search(text)
        area = parse_area(area_match.group(1) if area_match else None)
        land_area = None
        if query.property_type == "plot":
            land_area, area = area, None
        rooms_match = _ROOMS_RE.search(text)
        year_match = _YEAR_RE.search(text)

        location = _first_text(
            card.xpath('.//p[contains(@class, "text-gray-600")]'),
        )
        city = district = None
        if location:
            parts = [part.strip() for part in location.split(",") if part.strip()]
            if len(parts) >= 3:
                city = parts[1]
                district = parts[2]
            elif len(parts) == 2:
                city = parts[1]

        image = _first_attr_src(card)
        published_at = _parse_pl_date(
            _first_text(card.xpath('.//span[contains(@class, "leading-tight")]')),
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=names.get(url.rstrip("/")),
            description=None,
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(rooms_match.group(1) if rooms_match else None),
            build_year=safe_int(year_match.group(1) if year_match else None),
            voivodeship=query.region,
            city=city,
            district=district,
            address_text=location,
            images=[image] if image else [],
            published_at=published_at,
            extra={"homeportal": {"transaction": query.offer_type}},
        )

    @staticmethod
    def _total_pages(tree: Any, total_items: int | None, fallback: int) -> int:
        if total_items:
            return max(1, math.ceil(total_items / _PER_PAGE))
        pages = [int(value) for value in _PAGE_RE.findall(" ".join(tree.xpath("//a/@href")))]
        return max(pages, default=fallback)

    @staticmethod
    def _breadcrumbs(tree: Any) -> list[str]:
        payload = _find_typed(tree, "BreadcrumbList")
        names: list[str] = []
        for entry in payload.get("itemListElement", []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                name = _clean(entry["name"])
                if name:
                    names.append(name)
        return names

    @staticmethod
    def _additional_properties(entity: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        raw = entity.get("additionalProperty")
        if not isinstance(raw, list):
            return values
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _clean(str(item.get("name") or ""))
            value = _clean(str(item.get("value") or ""))
            if name and value:
                values.setdefault(normalize_text(name), value)
        return values

    @staticmethod
    def _market(value: str | None) -> Market:
        normalized = normalize_text(value or "")
        if normalized in {"pierwotny", "rynek pierwotny", "primary"}:
            return "primary"
        if normalized in {"wtorny", "rynek wtorny", "secondary"}:
            return "secondary"
        return "unknown"

    @staticmethod
    def _images(listing: dict[str, Any]) -> list[str]:
        raw = listing.get("image")
        if isinstance(raw, str):
            return [raw]
        if not isinstance(raw, list):
            return []
        images: list[str] = []
        for item in raw:
            if isinstance(item, str):
                images.append(item)
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                images.append(item["url"])
        return images[:20]


def _first_attr_src(card: Any) -> str | None:
    for node in card.xpath(".//img"):
        src = node.get("src")
        if src:
            return src.strip()
    return None
