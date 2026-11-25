"""Otodom adapter (search pages + detail pages)."""

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

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("sprzedaz", "mieszkanie", "sale", "flat"),
    ("wynajem", "mieszkanie", "rent", "flat"),
    ("sprzedaz", "dom", "sale", "house"),
    ("wynajem", "dom", "rent", "house"),
    ("sprzedaz", "dzialka", "sale", "plot"),
    ("sprzedaz", "lokal", "sale", "commercial"),
    ("wynajem", "lokal", "rent", "commercial"),
    ("sprzedaz", "garaz", "sale", "garage"),
    ("wynajem", "garaz", "rent", "garage"),
    ("wynajem", "pokoj", "rent", "room"),
)

_ESTATE_TO_PROPERTY: dict[str, PropertyType] = {
    "FLAT": "flat",
    "HOUSE": "house",
    "PLOT": "plot",
    "COMMERCIAL": "commercial",
    "GARAGE": "garage",
    "ROOM": "room",
    "INVESTMENT": "flat",
}

_PROPER_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "dom": "house",
    "dzialka": "plot",
    "lokal": "commercial",
    "biuro": "commercial",
    "hala": "commercial",
    "garaz": "garage",
    "pokoj": "room",
}

_OFFER_TYPE_MAP: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "sprzedaż": "sale",
    "wynajem": "rent",
    "najem": "rent",
    "licytacja": "auction",
    "aukcja": "auction",
    "dzierzawa": "lease",
    "dzierżawa": "lease",
}


def _next_data(html: str) -> dict[str, Any]:
    match = NEXT_DATA_RE.search(html)
    if match is None:
        raise ParseError("Otodom page is missing __NEXT_DATA__")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"Otodom __NEXT_DATA__ is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("Otodom __NEXT_DATA__ is not an object")
    return payload


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


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _canonical_offer_type(value: Any, fallback: OfferType) -> OfferType:
    if not value:
        return fallback
    normalized = normalize_text(str(value))
    for key, offer_type in _OFFER_TYPE_MAP.items():
        if normalized == normalize_text(key):
            return offer_type
    return fallback


def _canonical_property_type(value: Any, fallback: PropertyType) -> PropertyType:
    if not value:
        return fallback
    text = str(value)
    if text.upper() in _ESTATE_TO_PROPERTY:
        return _ESTATE_TO_PROPERTY[text.upper()]
    normalized = normalize_text(text)
    for key, property_type in _PROPER_TYPE_TO_PROPERTY.items():
        if normalized == normalize_text(key):
            return property_type
    return fallback


def _canonical_market(value: Any) -> Market:
    if not value:
        return "unknown"
    normalized = normalize_text(str(value))
    if normalized in {"primary", "pierwotny", "rynek pierwotny"}:
        return "primary"
    if normalized in {"secondary", "wtorny", "rynek wtorny"}:
        return "secondary"
    return "unknown"


def _county_from_subregion(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if normalized.startswith("powiat "):
        normalized = normalized[len("powiat ") :]
    return normalized.replace("-", " ") or None


class OtodomAdapter:
    """Parse Otodom's server-rendered JSON payloads."""

    id = "otodom"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for offer, kind, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{offer}:{kind}",
                        url=(f"https://www.otodom.pl/pl/wyniki/{offer}/{kind}/{region}"),
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """Otodom's server-side newest sort."""
        return query.with_param("by", "LATEST").with_param("direction", "DESC")

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _next_data(html)
        try:
            search_ads = payload["props"]["pageProps"]["data"]["searchAds"]
        except (KeyError, TypeError) as exc:
            raise ParseError("Otodom search payload is missing searchAds") from exc

        if not isinstance(search_ads, dict):
            raise ParseError("Otodom searchAds is not an object")

        raw_items = search_ads.get("items") or []
        items = [self._summary(item, query) for item in raw_items if isinstance(item, dict)]

        pagination = search_ads.get("pagination") or {}
        return SearchPage(
            items=items,
            current_page=safe_int(pagination.get("currentPage")) or query.page,
            total_pages=safe_int(pagination.get("totalPages")),
            total_items=safe_int(pagination.get("totalItems")),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _next_data(html)
        try:
            page_props = payload["props"]["pageProps"]
        except (KeyError, TypeError) as exc:
            raise ParseError("Otodom detail payload is missing pageProps") from exc

        ad = page_props.get("ad")
        if not isinstance(ad, dict):
            raise ParseError("Otodom detail payload is missing ad")

        target = _dict(ad.get("target"))
        characteristics = {
            str(item["key"]): item
            for item in (ad.get("characteristics") or [])
            if isinstance(item, dict) and "key" in item
        }

        def char(key: str) -> Any:
            item = characteristics.get(key)
            return item.get("value") if isinstance(item, dict) else None

        location = _dict(ad.get("location"))
        levels = self._location_levels(location)
        province = _first(levels.get("voivodeship"), target.get("Province"))
        county = _first(
            levels.get("county"),
            _county_from_subregion(target.get("Subregion")),
        )
        commune = levels.get("commune")
        city = _first(levels.get("city_or_village"), target.get("City"))

        address = _dict(location.get("address"))
        street_data = _dict(address.get("street"))
        street = _text(street_data.get("name"))

        coordinates = _dict(location.get("coordinates"))
        lat = safe_float(coordinates.get("latitude"))
        lng = safe_float(coordinates.get("longitude"))

        voivodeship = self.regions.canonical_voivodeship(_text(province))
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        images = self._images(ad.get("images"))
        advertiser_type = _text(ad.get("advertiserType"))
        agency = _dict(ad.get("agency"))
        owner = _dict(ad.get("owner"))
        seller_type = self._seller_type(advertiser_type, agency, owner)
        seller_name = _first(agency.get("name"), owner.get("name"))

        price = parse_price(_first(target.get("Price"), char("price")))
        price_kind = (
            "call" if _truthy(ad.get("hidePrice")) or _truthy(target.get("hidePrice")) else "asking"
        )
        if price is None and price_kind != "call":
            price_kind = "call" if "zapytaj" in normalize_text(str(ad.get("price"))) else "asking"

        area = parse_area(_first(target.get("Area"), char("m"), ad.get("areaInSquareMeters")))
        land_area = parse_area(
            _first(
                target.get("Plot_area"),
                target.get("Terrain_area"),
                target.get("Land_area"),
                char("plot_area"),
                char("terrain_area"),
                char("land_area"),
                char("dzialka"),
            ),
        )
        rooms = parse_rooms(_first(target.get("Rooms_num"), char("rooms_num")))
        floor, floor_label = parse_floor(_first(target.get("Floor_no"), char("floor_no")))
        build_year = safe_int(_first(target.get("Build_year"), char("build_year")))

        offer_type = _canonical_offer_type(
            _first(target.get("OfferType"), target.get("Transaction")),
            summary.offer_type,
        )
        property_type = _canonical_property_type(
            _first(target.get("ProperType"), ad.get("estate")),
            summary.property_type,
        )
        market = _canonical_market(_first(ad.get("market"), target.get("MarketType")))

        address_text = ", ".join(part for part in (street, city) if part) or None

        extra = dict(summary.extra)
        otodom_extra = dict(extra.get("otodom") or {})
        otodom_extra.update(
            {
                "advert_type": ad.get("advertType"),
                "source": ad.get("source"),
                "characteristics": {
                    key: {
                        "value": item.get("value"),
                        "currency": item.get("currency"),
                    }
                    for key, item in characteristics.items()
                },
                "target": {
                    key: value
                    for key, value in target.items()
                    if key not in {"Photo", "Title", "PriceRange", "AreaRange"}
                },
            },
        )
        extra["otodom"] = otodom_extra

        return replace(
            summary,
            url=_text(ad.get("url")) or summary.url,
            title=_text(ad.get("title")) or summary.title,
            description=strip_html(_text(ad.get("description"))),
            offer_type=offer_type,
            property_type=property_type,
            market=market,
            price_kind=price_kind,
            price_unit="monthly" if offer_type == "rent" else summary.price_unit,
            price_pln=price,
            price_per_m2_pln=parse_price(_first(target.get("Price_per_m"), char("price_per_m"))),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=build_year,
            voivodeship=voivodeship,
            county=_text(county),
            commune=_text(commune),
            city=_text(city),
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type=seller_type,
            seller_name=_text(seller_name),
            published_at=parse_datetime_ms(ad.get("createdAt")) or summary.published_at,
            modified_at=parse_datetime_ms(ad.get("modifiedAt")),
            extra=extra,
        )

    def _summary(self, item: dict[str, Any], query: Query) -> Listing:
        slug = _text(item.get("slug"))
        href = _text(item.get("href"))
        if slug is None and href:
            slug = href.rstrip("/").rsplit("/", 1)[-1]
        if slug is None:
            raise ParseError(f"Otodom item {item.get('id')} has no slug")

        external_id = _text(item.get("id")) or slug
        url = f"https://www.otodom.pl/pl/oferta/{slug}"

        location = _dict(item.get("location"))
        levels = self._location_levels(location)
        province = levels.get("voivodeship")
        voivodeship = self.regions.canonical_voivodeship(_text(province))

        total_price = _dict(item.get("totalPrice"))
        rent_price = _dict(item.get("rentPrice"))
        hide_price = _truthy(item.get("hidePrice"))
        price = None if hide_price else parse_price(total_price.get("value"))
        rent = None if hide_price else parse_price(rent_price.get("value"))

        city = _text(levels.get("city_or_village"))
        county = _text(levels.get("county"))
        street_data = _dict(_dict(location.get("address")).get("street"))
        street = _text(street_data.get("name"))
        address_text = ", ".join(part for part in (street, city) if part) or None

        agency = _dict(item.get("agency"))
        seller_type = "private" if item.get("isPrivateOwner") else None
        if seller_type is None and agency:
            seller_type = "developer" if agency.get("type") == "developer" else "agency"

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=_text(item.get("title")),
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="call" if hide_price else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            rent_pln=rent,
            price_per_m2_pln=parse_price(
                _first(item.get("pricePerSquareMeter"), item.get("priceFromPerSquareMeter")),
            ),
            area_m2=parse_area(item.get("areaInSquareMeters")),
            land_area_m2=parse_area(item.get("terrainAreaInSquareMeters")),
            rooms=parse_rooms(item.get("roomsNumber")),
            voivodeship=voivodeship,
            county=county,
            commune=_text(levels.get("commune")),
            city=city,
            street=street,
            address_text=address_text,
            images=self._images(item.get("images")),
            seller_type=seller_type,
            seller_name=_text(agency.get("name")),
            published_at=parse_datetime_ms(
                _first(item.get("dateCreated"), item.get("createdAtFirst")),
            ),
            extra={
                "otodom": {
                    "estate": item.get("estate"),
                    "transaction": item.get("transaction"),
                    "development_title": item.get("developmentTitle"),
                    "source": item.get("source"),
                },
            },
        )

    @staticmethod
    def _location_levels(location: dict[str, Any]) -> dict[str, str]:
        reverse = _dict(location.get("reverseGeocoding"))
        levels: dict[str, str] = {}
        for entry in reverse.get("locations") or []:
            if not isinstance(entry, dict):
                continue
            level = _text(entry.get("locationLevel"))
            name = _text(entry.get("name"))
            if level and name and level not in levels:
                levels[level] = name
        return levels

    @staticmethod
    def _images(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        images: list[str] = []
        for item in value:
            url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
            if url:
                images.append(url)
        return images[:20]

    @staticmethod
    def _seller_type(
        advertiser_type: str | None,
        agency: dict[str, Any],
        owner: dict[str, Any],
    ) -> str:
        owner_type = _text(owner.get("type"))
        agency_type = _text(agency.get("type"))
        if advertiser_type == "private" or owner_type == "private":
            return "private"
        if owner_type == "developer" or agency_type == "developer":
            return "developer"
        if agency or advertiser_type == "business":
            return "agency"
        return "unknown"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return False
