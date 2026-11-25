"""Nieruchomości-online adapter (search pages + detail pages).

The portal renders schema.org JSON-LD on both search and detail pages, which is
the primary data source.  Detail pages additionally expose the full description
and precise coordinates in the same JSON-LD document.
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

LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
TOTAL_ITEMS_RE = re.compile(r"Zobacz\s+([\d\s\u00a0]+)\s+ogłosze", re.IGNORECASE)
PAGE_PARAM_RE = re.compile(r"[?&]p=(\d+)")
ZERO_RESULTS_MARKERS = (
    "nie znaleziono",
    "brak ogłoszeń",
    "brak wyników",
    "0 ogłoszeń",
)
BLOCKED_MARKERS = (
    "just a moment",
    "cf-chl",
    "enable javascript and cookies to continue",
)
EXCLUDED_TYPES = {
    "OnlineBusiness",
    "WebSite",
    "WebPage",
    "BreadcrumbList",
    "Organization",
    "SearchAction",
    "ListItem",
}

# Search categories exposed by the portal.  "budynki-uzytkowe" covers halls,
# warehouses and garages (the portal does not have a separate garage section).
CATEGORIES: tuple[tuple[str, OfferType, PropertyType], ...] = (
    ("mieszkania,sprzedaz", "sale", "flat"),
    ("mieszkania,wynajem", "rent", "flat"),
    ("domy,sprzedaz", "sale", "house"),
    ("domy,wynajem", "rent", "house"),
    ("dzialki,sprzedaz", "sale", "plot"),
    ("dzialki,wynajem", "lease", "plot"),
    ("lokale-uzytkowe,sprzedaz", "sale", "commercial"),
    ("lokale-uzytkowe,wynajem", "rent", "commercial"),
    ("budynki-uzytkowe,sprzedaz", "sale", "commercial"),
    ("budynki-uzytkowe,wynajem", "rent", "commercial"),
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
    raise ParseError(f"Nieruchomości-online page is missing JSON-LD{detail}")


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
    # The portal emits values such as "2026-07-20CEST11:24:56Z".
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})(?:[A-Za-z]{2,5})?T?(\d{2}:\d{2}:\d{2})?",
        text,
    )
    if match is None:
        return None
    iso = match.group(1)
    iso = f"{iso}T{match.group(2)}" if match.group(2) else f"{iso}T00:00:00"
    return parse_datetime_ms(iso)


def _external_id(url: str) -> str | None:
    path = urlsplit(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]
    if last.endswith(".html"):
        last = last[: -len(".html")]
    return last or None


def _market_from_url(url: str) -> Market:
    path = urlsplit(url).path.lower()
    if "nowe-" in path or "/inwestycja/" in path:
        return "primary"
    return "unknown"


def _property_from_url(url: str, fallback: PropertyType) -> PropertyType:
    path = urlsplit(url).path.lower()
    checks: tuple[tuple[tuple[str, ...], PropertyType], ...] = (
        (("mieszkanie", "apartment"), "flat"),
        (("garaz", "garaż"), "garage"),
        (("pokoj", "pokój"), "room"),
        (("lokal", "budynek", "biuro", "magazyn", "hala"), "commercial"),
        (("dzialka", "działka", "grunt"), "plot"),
        (("dom",), "house"),
    )
    for needles, property_type in checks:
        if any(needle in path for needle in needles):
            return property_type
    return fallback


def _is_property_node(node: dict[str, Any]) -> bool:
    if _types(node) and _types(node).issubset(EXCLUDED_TYPES):
        return False
    if not ({"address", "geo"} & set(node)):
        return False
    return bool({"offers", "floorSize", "additionalProperty"} & set(node))


def _property_type(node: dict[str, Any], fallback: PropertyType) -> PropertyType:
    for schema_type in _types(node):
        mapped = _SCHEMA_TYPE_TO_PROPERTY.get(schema_type.lower())
        if mapped is not None:
            return mapped

    name = normalize_text(str(node.get("name") or ""))
    for needle, property_type in (
        ("mieszkanie", "flat"),
        ("apartament", "flat"),
        ("dom", "house"),
        ("dzialka", "plot"),
        ("lokal", "commercial"),
        ("budynek", "commercial"),
        ("garaz", "garage"),
        ("pokoj", "room"),
    ):
        if needle in name:
            return property_type
    return fallback


def _additional_properties(node: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for item in _dicts(node.get("additionalProperty")):
        name = _text(item.get("name"))
        if name:
            props[name] = item.get("value")
    return props


def _additional(props: dict[str, Any], *names: str) -> Any:
    wanted = {normalize_text(name) for name in names}
    for name, value in props.items():
        if normalize_text(name) in wanted:
            return value
    return None


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


def _location_parts(html: str) -> list[str]:
    """Read the human-readable address line from the detail page."""

    try:
        tree = lxml_html.fromstring(html)
    except ValueError, TypeError:
        return []

    entries = tree.xpath('//ul[@id="locationUl"]//li[contains(@class, "adress")]')
    if not entries:
        return []

    text = " ".join(entries[0].text_content().split())
    if text.lower().startswith("adres:"):
        text = text[len("adres:") :].strip()
    return [part.strip() for part in text.split(",") if part.strip()]


def _full_description(html: str) -> str | None:
    try:
        tree = lxml_html.fromstring(html)
    except ValueError, TypeError:
        return None

    expanded = tree.xpath(
        '//div[@id="boxCustomDesc"]//div[contains(@class, "estate-desc-more")]',
    )
    nodes = expanded or tree.xpath(
        '//div[@id="boxCustomDesc"]//div[contains(@class, "estate-desc-less")]',
    )
    if not nodes:
        return None

    target = nodes[0]
    for removable in target.xpath('.//span[@id="readMore"] | .//button'):
        parent = removable.getparent()
        if parent is not None:
            parent.remove(removable)

    text = " ".join(target.text_content().split())
    return text or None


class NieruchomosciOnlineAdapter:
    """Parse Nieruchomości-online's schema.org JSON-LD."""

    id = "nieruchomosci-online"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for category, offer_type, property_type in CATEGORIES:
                key_part = category.replace(",", ":")
                queries.append(
                    Query(
                        key=f"{region}:{key_part}",
                        url=f"https://{region}.nieruchomosci-online.pl/{category}/",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """The portal's default list is already newest-first."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params.pop("page", None)
        params["p"] = str(page)
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment),
        )
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        nodes = _nodes(html, required=False)
        if nodes is None:
            if self._is_empty_page(html):
                return SearchPage(
                    items=[],
                    current_page=query.page,
                    total_pages=1,
                    total_items=0,
                )
            raise ParseError("Nieruchomości-online search page is missing JSON-LD")

        collection = next(
            (node for node in nodes if _has_type(node, "CollectionPage")),
            None,
        )

        offers: list[dict[str, Any]] = []
        total_items: int | None = None
        if collection is not None:
            main = _dict(collection.get("mainEntity"))
            total_items = _count(_text(collection.get("description")))
            for container in _dicts(main.get("offers")):
                nested = _dicts(container.get("offers"))
                if nested and _has_type(container, "AggregateOffer"):
                    offers.extend(nested)
                elif _has_type(container, "Offer"):
                    offers.append(container)

        if not offers and self._is_empty_page(html):
            return SearchPage(items=[], current_page=query.page, total_pages=1, total_items=0)

        if collection is None:
            raise ParseError("Nieruchomości-online search page is missing a CollectionPage")

        items = [self._summary(offer, query) for offer in offers]
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
            raise ParseError("Nieruchomości-online detail page is missing JSON-LD")
        candidates = [node for node in nodes if _is_property_node(node)]
        if not candidates:
            raise ParseError("Nieruchomości-online detail page is missing a property node")

        node = candidates[-1]
        address = _dict(node.get("address"))
        parts = _location_parts(html)

        voivodeship = self.regions.canonical_voivodeship(
            _text(address.get("addressRegion")),
        )
        city = _text(address.get("addressLocality"))
        street = _text(address.get("streetAddress"))

        if voivodeship is None and parts:
            voivodeship = self.regions.canonical_voivodeship(parts[-1])
        if city is None and len(parts) >= 2:
            city = parts[-2]
        if street is None and len(parts) >= 3:
            street = parts[0]

        district = self._district(parts, city=city, street=street, voivodeship=voivodeship)

        geo = _dict(node.get("geo"))
        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        props = _additional_properties(node)
        offer = _dicts(node.get("offers"))[0] if node.get("offers") else {}
        price = _money(_first(offer.get("price"), node.get("price")))
        unit_price = _money(
            _first(
                self._price_specification(offer).get("price"),
                self._price_specification(node).get("price"),
            ),
        )

        property_type = _property_type(node, summary.property_type)
        offer_type = self._offer_type(
            _text(node.get("url")) or summary.url,
            summary.offer_type,
        )

        raw_area = _first(
            _additional(props, "Floor size", "House area", "Store area"),
            _dict(node.get("floorSize")).get("value"),
        )
        land_raw = _first(
            _additional(props, "Land area"),
            _dict(node.get("floorSize")).get("value") if property_type == "plot" else None,
        )
        area = parse_area(raw_area)
        land_area = parse_area(land_raw)
        if property_type == "plot" and area is None:
            area = land_area
        if property_type == "plot" and land_area is None:
            land_area = area

        rooms = parse_rooms(
            _first(node.get("numberOfRooms"), _additional(props, "Number of rooms")),
        )
        floor, floor_label = parse_floor(
            _first(node.get("floorLevel"), _additional(props, "Floor level")),
        )
        build_year = safe_int(
            _first(node.get("yearBuilt"), _additional(props, "Year built")),
        )

        agent = _dict(node.get("agent"))
        seller_name = _text(agent.get("name"))
        seller_type: str | None = None
        if _has_type(agent, "Person"):
            seller_type = "private"
        elif agent:
            seller_type = "agency"

        address_text = ", ".join(part for part in (street, city) if part) or None
        description = _full_description(html) or strip_html(_text(node.get("description")))

        extra = dict(summary.extra)
        source_extra = dict(extra.get(self.id) or {})
        source_extra.update(
            {
                "additional_property": props,
                "amenity_feature": _dicts(node.get("amenityFeature")),
                "agent": agent,
            },
        )
        extra[self.id] = source_extra

        market = _market_from_url(_text(node.get("url")) or summary.url)
        if market == "unknown":
            market = summary.market

        return replace(
            summary,
            title=summary.title or _text(node.get("name")),
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market=market,
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
            voivodeship=voivodeship or summary.voivodeship,
            county=summary.county,
            commune=summary.commune,
            city=city or summary.city,
            district=district or summary.district,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(node.get("image")) or summary.images,
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=_date_ms(node.get("datePosted")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, offer: dict[str, Any], query: Query) -> Listing:
        url = _text(offer.get("url"))
        if url is None:
            raise ParseError("Nieruchomości-online offer is missing a URL")

        external_id = _external_id(url)
        if external_id is None:
            raise ParseError(f"Nieruchomości-online offer {url} has no identifier")

        item = _dict(offer.get("itemOffered"))
        address = _dict(item.get("address"))
        price = _money(offer.get("price"))
        unit_price = _money(self._price_specification(offer).get("price"))
        area = parse_area(_dict(item.get("floorSize")).get("value"))
        land_area = area if query.property_type == "plot" else None

        voivodeship = (
            self.regions.canonical_voivodeship(_text(address.get("addressRegion"))) or query.region
        )
        city = _text(address.get("addressLocality"))
        street = _text(address.get("streetAddress"))
        address_text = ", ".join(part for part in (street, city) if part) or None

        property_type = _property_from_url(url, query.property_type)

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=_text(offer.get("name")),
            offer_type=query.offer_type,
            property_type=property_type,
            market=_market_from_url(url),
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=unit_price,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(item.get("numberOfRooms")),
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=address_text,
            extra={self.id: {"query": query.key}},
        )

    @staticmethod
    def _price_specification(node: dict[str, Any]) -> dict[str, Any]:
        value = node.get("priceSpecification")
        if isinstance(value, list):
            return _dict(value[0]) if value else {}
        return _dict(value)

    @staticmethod
    def _total_pages(html: str) -> int | None:
        start = html.find('id="pagination-outer"')
        if start < 0:
            return None
        window = html[start : start + 20_000]
        pages = [int(value) for value in PAGE_PARAM_RE.findall(window)]
        return max(pages) if pages else None

    @staticmethod
    def _is_empty_page(html: str) -> bool:
        lowered = html.lower()
        if any(marker in lowered for marker in BLOCKED_MARKERS):
            return False
        return any(marker in lowered for marker in ZERO_RESULTS_MARKERS)

    @staticmethod
    def _offer_type(url: str, fallback: OfferType) -> OfferType:
        path = urlsplit(url).path.lower()
        if "wynajem" in path or "wynaj" in path:
            return "rent"
        if "sprzedaz" in path or "sprzeda" in path:
            return "sale"
        if "dzierzaw" in path:
            return "lease"
        return fallback

    @staticmethod
    def _district(
        parts: list[str],
        *,
        city: str | None,
        street: str | None,
        voivodeship: str | None,
    ) -> str | None:
        remaining = [
            part
            for part in parts
            if part not in {city, street}
            and (voivodeship is None or normalize_text(part) != normalize_text(voivodeship))
            and normalize_text(part) not in {"malopolskie", "slaskie"}
        ]
        if len(remaining) == 1:
            return remaining[0]
        return None
