"""Domiporta adapter (search pages + detail pages).

Domiporta publishes schema.org JSON-LD on category pages (an ``ItemList`` of
listings) and on detail pages (a ``RealEstateListing`` with coordinates).  The
full description is server-rendered in the detail page markup.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

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

LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
TOTAL_ITEMS_RE = re.compile(r"Zobacz\s+([\d\s\u00a0]+)\s+ofert", re.IGNORECASE)
PAGE_PARAM_RE = re.compile(r"PageNumber=(\d+)", re.IGNORECASE)
ZERO_RESULTS_MARKERS = (
    "brak wyników spełniających",
    "brak wynikow spelniajacych",
    "nie znaleziono",
    "brak ogłoszeń",
)
BLOCKED_MARKERS = (
    "just a moment",
    "cf-chl",
    "enable javascript and cookies to continue",
)
NON_PROPERTY_TYPES = {
    "WebPage",
    "WebSite",
    "BreadcrumbList",
    "ItemList",
    "Organization",
    "SearchAction",
    "Place",
}

# Regular category searches.  ``dzialka/wydzierzawie`` is the lease variant of
# the plot section; the rest mirror the portal's sale/rent navigation.
CATEGORIES: tuple[tuple[str, OfferType, PropertyType], ...] = (
    ("mieszkanie/sprzedam", "sale", "flat"),
    ("mieszkanie/wynajme", "rent", "flat"),
    ("dom/sprzedam", "sale", "house"),
    ("dom/wynajme", "rent", "house"),
    ("dzialka/sprzedam", "sale", "plot"),
    ("dzialka/wydzierzawie", "lease", "plot"),
    ("lokal-uzytkowy/sprzedam", "sale", "commercial"),
    ("lokal-uzytkowy/wynajme", "rent", "commercial"),
    ("magazyn/sprzedam", "sale", "commercial"),
    ("magazyn/wynajme", "rent", "commercial"),
    ("garaz/sprzedam", "sale", "garage"),
    ("garaz/wynajme", "rent", "garage"),
    ("pokoj/wynajme", "rent", "room"),
)

PREMIUM_CATEGORIES: tuple[tuple[str, OfferType], ...] = (
    ("sprzedam", "sale"),
    ("wynajme", "rent"),
)

_SCHEMA_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "detachedhouse": "house",
    "singlefamilyresidence": "house",
    "landplot": "plot",
    "plot": "plot",
    "store": "commercial",
    "officebuilding": "commercial",
    "commercialproperty": "commercial",
    "garage": "garage",
    "parking": "garage",
    "room": "room",
}


def _ld_blocks(html: str, *, required: bool) -> list[dict[str, Any]] | None:
    """Return valid JSON-LD objects, or ``None`` when the page has none."""

    blocks: list[dict[str, Any]] = []
    invalid: list[str] = []
    for match in LD_JSON_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            try:
                payload = json.loads(unescape(raw))
            except json.JSONDecodeError as exc:
                invalid.append(str(exc))
                continue
        if isinstance(payload, dict):
            blocks.append(payload)
        elif isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))

    if blocks:
        return blocks
    if not required:
        return None
    detail = f" ({invalid[0]})" if invalid else ""
    raise ParseError(f"Domiporta page is missing JSON-LD{detail}")


def _nodes(html: str, *, required: bool = True) -> list[dict[str, Any]] | None:
    blocks = _ld_blocks(html, required=required)
    if blocks is None:
        return None

    nodes: list[dict[str, Any]] = []
    for block in blocks:
        graph = block.get("@graph")
        if isinstance(graph, list):
            nodes.extend(item for item in graph if isinstance(item, dict))
        else:
            nodes.append(block)
    return nodes


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _has_type(node: dict[str, Any], *names: str) -> bool:
    return bool(_types(node) & set(names))


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


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _money(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        amount = round(value)
        return amount if amount > 0 else None

    text = str(value).strip().replace("\u00a0", " ").replace(" ", "")
    if not text:
        return None
    try:
        amount = round(float(text.replace(",", ".")))
    except ValueError:
        amount = parse_price(value)
    if amount is None or amount <= 0:
        return None
    return amount


def _count(text: str | None) -> int | None:
    if not text:
        return None
    match = TOTAL_ITEMS_RE.search(text)
    if match is None:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    return int(digits) if digits else None


def _date_ms(value: Any) -> int | None:
    if value is None:
        return None
    parsed = parse_datetime_ms(value)
    if parsed is not None:
        return parsed

    text = str(value).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match is None:
        return None
    return parse_datetime_ms(f"{match.group(1)}T00:00:00")


def _external_id(url: str) -> str | None:
    last = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return last or None


def _market_from_url(url: str) -> Market:
    path = urlsplit(url).path.lower()
    if "/nowe/" in path or "inwestycja" in path:
        return "primary"
    return "unknown"


def _property_from_url(url: str, fallback: PropertyType) -> PropertyType:
    path = urlsplit(url).path.lower()
    checks: tuple[tuple[tuple[str, ...], PropertyType], ...] = (
        (("mieszkanie", "apartment", "apartament"), "flat"),
        (("garaz", "garaż"), "garage"),
        (("pokoj", "pokój"), "room"),
        (("lokal", "magazyn", "biuro", "hala", "uslug", "usług"), "commercial"),
        (("dzialka", "działka", "grunt"), "plot"),
        (("dom",), "house"),
    )
    for needles, property_type in checks:
        if any(needle in path for needle in needles):
            return property_type
    return fallback


def _property_type(node: dict[str, Any], fallback: PropertyType) -> PropertyType:
    for schema_type in _types(node):
        mapped = _SCHEMA_TYPE_TO_PROPERTY.get(schema_type.lower())
        if mapped is not None:
            return mapped
    return fallback


def _additional_properties(node: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for item in _dicts(node.get("additionalProperty")):
        name = _text(item.get("name"))
        if name:
            props[name] = item.get("value")
    return props


def _images(value: Any, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    urls: list[str] = []
    for item in candidates:
        url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
        if url:
            urls.append(url)
    return urls[:limit]


def _full_description(html: str) -> str | None:
    try:
        tree = lxml_html.fromstring(html)
    except ValueError, TypeError:
        return None

    panels = tree.xpath(
        '//div[contains(@class, "js-col-description")]'
        '//div[contains(@class, "description__panel")]',
    )
    if not panels:
        panels = tree.xpath('//div[contains(@class, "description__panel")]')
    if not panels:
        return None

    text = " ".join(panels[0].text_content().split())
    return text or None


def _seller(node: dict[str, Any], offer: dict[str, Any]) -> tuple[str | None, str | None]:
    seller = _dict(_first(node.get("seller"), offer.get("seller")))
    name = _text(seller.get("name"))
    if _has_type(seller, "Person"):
        return name, "private"
    if seller:
        return name, "agency"
    return name, None


def _description_from_node(node: dict[str, Any]) -> str | None:
    return strip_html(_text(node.get("description")))


class DomiportaAdapter:
    """Parse Domiporta's schema.org JSON-LD."""

    id = "domiporta"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for category, offer_type, property_type in CATEGORIES:
                key_part = category.replace("/", ":")
                queries.append(
                    Query(
                        key=f"{region}:{key_part}",
                        url=f"https://www.domiporta.pl/{category}/{region}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )

            for action, offer_type in PREMIUM_CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:premium:{action}",
                        url=(
                            "https://www.domiporta.pl/nieruchomosci-premium/"
                            f"nieruchomosci/{action}?Localization={region}"
                        ),
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params.pop("page", None)
        params["PageNumber"] = str(page)
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment),
        )
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        nodes = _nodes(html, required=False)
        item_list = None
        if nodes is not None:
            item_list = next((node for node in nodes if _has_type(node, "ItemList")), None)

        if item_list is None:
            if self._is_empty_page(html):
                return SearchPage(
                    items=[],
                    current_page=query.page,
                    total_pages=1,
                    total_items=0,
                )
            if nodes is None:
                raise ParseError("Domiporta search page is missing JSON-LD")
            raise ParseError("Domiporta search page is missing an ItemList")

        raw_items = [
            _dict(element.get("item")) for element in _dicts(item_list.get("itemListElement"))
        ]
        items = [self._summary(item, query) for item in raw_items]

        assert nodes is not None  # an ItemList requires a parsed JSON-LD document
        total_items = self._total_items(nodes)
        total_pages = self._total_pages(html) or (1 if items else 0)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items if total_items is not None else len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        nodes = _nodes(html)
        if nodes is None:
            raise ParseError("Domiporta detail page is missing JSON-LD")

        candidates = [
            node
            for node in nodes
            if _dict(node.get("itemOffered")) and not _types(node).issubset(NON_PROPERTY_TYPES)
        ]
        if not candidates:
            raise ParseError("Domiporta detail page is missing a listing node")

        node = next(
            (candidate for candidate in candidates if _has_type(candidate, "RealEstateListing")),
            candidates[0],
        )
        offer = _dict(node.get("offers"))
        offered = _dict(node.get("itemOffered"))
        address = _dict(_first(node.get("address"), offered.get("address")))
        geo = _dict(_first(offered.get("geo"), node.get("geo")))

        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))

        url = _text(node.get("url")) or summary.url
        price = _money(offer.get("price"))
        unit_price = (
            _money(self._price_specification(offer).get("price")) or summary.price_per_m2_pln
        )
        property_type = _property_from_url(url, summary.property_type)
        offer_type = self._offer_type(url, summary.offer_type)

        detail_area = parse_area(_dict(offered.get("floorSize")).get("value"))
        area = summary.area_m2 if summary.area_m2 is not None else detail_area
        land_area = summary.land_area_m2 if summary.land_area_m2 is not None else None
        if property_type == "plot":
            land_area = land_area or detail_area or area
            area = area or land_area

        floor, floor_label = parse_floor(offered.get("floorLevel"))
        rooms = parse_rooms(offered.get("numberOfRooms")) or summary.rooms
        build_year = safe_int(offered.get("yearBuilt"))

        voivodeship = (
            self.regions.canonical_voivodeship(
                _text(address.get("addressRegion")),
            )
            or summary.voivodeship
        )
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        city = _text(address.get("addressLocality")) or summary.city
        street = _text(address.get("streetAddress")) or summary.street
        address_text = ", ".join(part for part in (street, city) if part) or None

        seller_name, seller_type = _seller(node, offer)
        props = _additional_properties(offered)
        description = _full_description(html) or _description_from_node(node)

        extra = dict(summary.extra)
        source_extra = dict(extra.get(self.id) or {})
        source_extra.update(
            {
                "additional_property": props,
                "amenity_feature": _dicts(offered.get("amenityFeature")),
                "premium": "nieruchomosci-premium" in url,
            },
        )
        extra[self.id] = source_extra

        return replace(
            summary,
            url=url,
            title=summary.title or _text(node.get("name")),
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market=(summary.market if summary.market != "unknown" else _market_from_url(url)),
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if offer_type == "rent" else summary.price_unit,
            price_pln=price,
            price_per_m2_pln=unit_price,
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=build_year,
            voivodeship=voivodeship,
            county=summary.county,
            commune=summary.commune,
            city=city,
            district=summary.district,
            street=street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(node.get("image")) or summary.images,
            seller_type=seller_type or summary.seller_type,
            seller_name=seller_name or summary.seller_name,
            published_at=_date_ms(node.get("datePosted")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, item: dict[str, Any], query: Query) -> Listing:
        url = _text(item.get("url"))
        if url is None:
            raise ParseError("Domiporta listing is missing a URL")

        external_id = _external_id(url)
        if external_id is None:
            raise ParseError(f"Domiporta listing {url} has no identifier")

        offer = _dict(item.get("offers"))
        offered = _dict(_first(offer.get("itemOffered"), item.get("itemOffered")))
        address = _dict(_first(offered.get("address"), item.get("address")))

        price = _money(offer.get("price"))
        unit_price = _money(self._price_specification(offer).get("price"))
        area = parse_area(_dict(offered.get("floorSize")).get("value"))
        property_type = (
            query.property_type
            if query.property_type != "other"
            else _property_from_url(url, "other")
        )
        land_area = area if property_type == "plot" else None

        voivodeship = (
            self.regions.canonical_voivodeship(_text(address.get("addressRegion"))) or query.region
        )
        city = _text(address.get("addressLocality"))
        street = _text(address.get("streetAddress"))
        address_text = ", ".join(part for part in (street, city) if part) or None
        seller_name, seller_type = _seller(item, offer)

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=_text(item.get("name")),
            offer_type=query.offer_type,
            property_type=property_type,
            market=_market_from_url(url),
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=unit_price,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(offered.get("numberOfRooms")),
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=address_text,
            images=_images(item.get("image")),
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=_date_ms(item.get("datePosted")),
            extra={
                self.id: {
                    "query": query.key,
                    "premium": "nieruchomosci-premium" in query.url,
                },
            },
        )

    @staticmethod
    def _price_specification(offer: dict[str, Any]) -> dict[str, Any]:
        value = offer.get("priceSpecification")
        if isinstance(value, list):
            return _dict(value[0]) if value else {}
        return _dict(value)

    @staticmethod
    def _total_pages(html: str) -> int | None:
        start = html.find('class="listing__pagination"')
        if start < 0:
            return None
        window = html[start : start + 20_000]
        pages = [int(value) for value in PAGE_PARAM_RE.findall(window)]
        return max(pages) if pages else None

    @staticmethod
    def _total_items(nodes: Sequence[dict[str, Any]]) -> int | None:
        for node in nodes:
            if _has_type(node, "WebPage"):
                count = _count(_text(node.get("description")))
                if count is not None:
                    return count
        return None

    @staticmethod
    def _is_empty_page(html: str) -> bool:
        lowered = html.lower()
        if any(marker in lowered for marker in BLOCKED_MARKERS):
            return False
        return any(marker in lowered for marker in ZERO_RESULTS_MARKERS)

    @staticmethod
    def _offer_type(url: str, fallback: OfferType) -> OfferType:
        path = urlsplit(url).path.lower()
        if "wynajm" in path:
            return "rent"
        if "wydzierzaw" in path:
            return "lease"
        if "sprzeda" in path:
            return "sale"
        return fallback
