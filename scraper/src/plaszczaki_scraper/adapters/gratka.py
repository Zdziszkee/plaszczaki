"""Gratka adapter (search pages + detail pages).

Gratka and Morizon are served by the same Nuxt front end (Grupa
Morizon-Gratka). This module and :mod:`plaszczaki_scraper.adapters.morizon`
therefore mirror each other; keep them in sync when the upstream payload
changes.

Search and detail pages embed a Nuxt *devalue* payload in
``<script id="__NUXT_DATA__">``. That structured payload is preferred over HTML
selectors because it is the data the site renders itself. Detail payloads carry
``location.map.center`` coordinates and the full HTML description.

robots.txt only allows ``?page=2`` … ``?page=10``, and ``?page=1`` is
disallowed, so page 1 is requested without the parameter and :data:`MAX_PAGES`
caps pagination.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

BASE_URL = "https://gratka.pl"
IMAGE_URL = "https://img1.staticmorizon.com.pl/thumb/{photo_id}/3x2_m:fill_and_crop/{name}.jpg"

# robots.txt allows ?page=2..?page=10 only.
MAX_PAGES = 10
# The site serves 35 offers per search page.
PAGE_SIZE_FALLBACK = 35

# (URL slug, offer type, property type, URL suffix). Rooms are rent-only and
# live at /nieruchomosci/pokoje/{region} without the /wynajem suffix.
CATEGORIES: tuple[tuple[str, OfferType, PropertyType, str], ...] = (
    ("mieszkania", "sale", "flat", ""),
    ("mieszkania", "rent", "flat", "/wynajem"),
    ("domy", "sale", "house", ""),
    ("domy", "rent", "house", "/wynajem"),
    ("dzialki-grunty", "sale", "plot", ""),
    ("dzialki-grunty", "rent", "plot", "/wynajem"),
    ("lokale-uzytkowe", "sale", "commercial", ""),
    ("lokale-uzytkowe", "rent", "commercial", "/wynajem"),
    ("garaze", "sale", "garage", ""),
    ("garaze", "rent", "garage", "/wynajem"),
    ("pokoje", "rent", "room", ""),
)

_ROOT_TYPE_TO_PROPERTY: dict[str, PropertyType] = {
    "FLAT": "flat",
    "HOUSE": "house",
    "PLOT": "plot",
    "COMMERCIAL": "commercial",
    "GARAGE": "garage",
    "ROOM": "room",
}

_TRANSACTION_TO_OFFER: dict[str, OfferType] = {
    "SALE": "sale",
    "RENT": "rent",
    "LEASE": "lease",
    "AUCTION": "auction",
}

NUXT_DATA_RE = re.compile(
    r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# devalue's negative sentinels.
_UNDEFINED = -1
_HOLE = -2
_NAN = -3
_POSITIVE_INFINITY = -4
_NEGATIVE_INFINITY = -5
_NEGATIVE_ZERO = -6

_SPECIAL_VALUES: dict[int, Any] = {
    _UNDEFINED: None,
    _HOLE: None,
    _NAN: None,
    _POSITIVE_INFINITY: None,
    _NEGATIVE_INFINITY: None,
    _NEGATIVE_ZERO: 0,
}

# Nuxt wraps reactive values as [marker, index].
_MARKERS = frozenset(
    {
        "Date",
        "EmptyRef",
        "Map",
        "NuxtError",
        "Reactive",
        "Ref",
        "RegExp",
        "Set",
        "ShallowReactive",
        "ShallowRef",
        "URL",
        "BigInt",
    },
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _hydrate(flat: list[Any]) -> Any:
    """Expand Nuxt's flattened (devalue) ``__NUXT_DATA__`` array."""
    cache: dict[int, Any] = {}

    def resolve(index: int, depth: int = 0) -> Any:
        if depth > 250:
            raise ParseError("Gratka __NUXT_DATA__ is nested too deeply")
        if index in _SPECIAL_VALUES:
            return _SPECIAL_VALUES[index]
        if index in cache:
            return cache[index]
        if not 0 <= index < len(flat):
            raise ParseError(f"Gratka __NUXT_DATA__ has a dangling reference: {index}")

        raw = flat[index]
        if isinstance(raw, dict):
            value: dict[str, Any] = {}
            cache[index] = value
            for key, reference in raw.items():
                value[key] = (
                    resolve(reference, depth + 1) if isinstance(reference, int) else reference
                )
            return value
        if isinstance(raw, list):
            if len(raw) == 2 and isinstance(raw[0], str) and raw[0] in _MARKERS:
                inner = resolve(raw[1], depth + 1) if isinstance(raw[1], int) else raw[1]
                cache[index] = inner
                return inner
            items: list[Any] = []
            cache[index] = items
            for reference in raw:
                items.append(
                    resolve(reference, depth + 1) if isinstance(reference, int) else reference
                )
            return items

        cache[index] = raw
        return raw

    return resolve(0)


def _nuxt_payload(html: str) -> dict[str, Any]:
    match = NUXT_DATA_RE.search(html)
    if match is None:
        raise ParseError("Gratka page is missing __NUXT_DATA__")
    try:
        flat = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"Gratka __NUXT_DATA__ is not valid JSON: {exc}") from exc
    if not isinstance(flat, list):
        raise ParseError("Gratka __NUXT_DATA__ is not a flattened array")
    payload = _hydrate(flat)
    if not isinstance(payload, dict):
        raise ParseError("Gratka __NUXT_DATA__ root is not an object")
    return payload


def _payload_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    return [value for value in data.values() if isinstance(value, dict)]


def _search_result(payload: dict[str, Any]) -> dict[str, Any]:
    for entry in _payload_values(payload):
        inner = entry.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("searchResult"), dict):
            return inner["searchResult"]
    raise ParseError("Gratka payload is missing searchResult")


def _property_data(payload: dict[str, Any]) -> dict[str, Any]:
    for entry in _payload_values(payload):
        inner = entry.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("propertyData"), dict):
            return inner["propertyData"]
    raise ParseError("Gratka payload is missing propertyData")


def _node_list(value: Any) -> list[dict[str, Any]]:
    nodes = _dict(value).get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _search_nodes(search: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one offer per result group (groups wrap duplicates)."""
    groups = search.get("propertyGroups")
    if isinstance(groups, dict):
        primaries: list[dict[str, Any]] = []
        for node in _node_list(groups):
            primary = node.get("primary")
            if isinstance(primary, dict):
                primaries.append(primary)
        return primaries
    return _node_list(search.get("properties"))


def _promoted_nodes(search: dict[str, Any]) -> list[dict[str, Any]]:
    return _node_list(search.get("topPromoted"))


def _total_items(payload: dict[str, Any], search: dict[str, Any]) -> int | None:
    """Prefer the offer count from the decoded listing URL; fall back to the page."""
    for entry in _payload_values(payload):
        decode = _dict(_dict(entry.get("data")).get("decodeListingUrl"))
        total = safe_int(decode.get("totalCount"))
        if total is not None:
            return total
    container = search.get("propertyGroups") or search.get("properties")
    return safe_int(_dict(container).get("totalCount"))


def _amount(value: Any) -> int | None:
    """Parse the API's decimal money strings, e.g. ``"1450000.00"``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(value) if value > 0 else None
    if isinstance(value, dict):
        return _amount(value.get("amount"))
    text = str(value).strip()
    if not text:
        return None
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        return parse_price(text)
    return round(amount) if amount > 0 else None


def _label_money(value: Any) -> int | None:
    """Parse label values such as ``"720,00"`` (decimal comma, not thousands)."""
    text = _text(value)
    if text is None:
        return None
    cleaned = re.sub(r"(zł|pln)", "", text, flags=re.IGNORECASE).strip()
    match = re.fullmatch(r"(\d[\d .]*?)(?:,(\d{1,2}))?", cleaned)
    if match is None:
        return parse_price(cleaned)
    whole = re.sub(r"[^0-9]", "", match.group(1))
    if not whole:
        return None
    amount = float(whole)
    if match.group(2):
        amount += float(f"0.{match.group(2)}")
    return round(amount) if amount > 0 else None


def _date_label(value: Any) -> int | None:
    text = _text(value)
    if text is None:
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
        return int(parsed.timestamp() * 1000)
    return parse_datetime_ms(text)


def _labels(property_data: dict[str, Any]) -> dict[str, str]:
    """Merge the detail page's label tables (offer, building, development)."""
    labels: dict[str, str] = {}
    for section in (
        "detailedInformation",
        "buildingDetailedInformation",
        "developmentDetailedInformation",
    ):
        for entry in property_data.get(section) or []:
            if not isinstance(entry, dict):
                continue
            label = _text(entry.get("label"))
            value = _text(entry.get("value"))
            if label and value:
                labels.setdefault(label, value)
    return labels


def _description(value: Any) -> str | None:
    """Strip the HTML description, keeping paragraph boundaries readable."""
    text = _text(value)
    if text is None:
        return None
    spaced = re.sub(r"</(?:p|div|li|h[1-6])\s*>", " ", text, flags=re.IGNORECASE)
    spaced = re.sub(r"<br\s*/?>", " ", spaced, flags=re.IGNORECASE)
    return strip_html(spaced)


def _label(labels: dict[str, str], *names: str) -> str | None:
    """Look a label up by normalised text (diacritics/case insensitive)."""
    wanted = {normalize_text(name) for name in names}
    for label, value in labels.items():
        if normalize_text(label) in wanted:
            return value
    return None


def _floor(value: Any) -> tuple[int | None, str | None]:
    """Parse a floor label, mapping parter/suterena before the shared digit rule."""
    label = _text(value)
    if label is None:
        return None, None
    head = normalize_text(label).split(" ", 1)[0]
    if head == "parter":
        return 0, label
    if head in {"suterena", "piwnica"}:
        return -1, label
    return parse_floor(label)


def _market(value: Any) -> Market:
    normalized = normalize_text(str(value or ""))
    if "pierwotny" in normalized:
        return "primary"
    if "wtorny" in normalized:
        return "secondary"
    return "unknown"


def _absolute(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return f"{BASE_URL}/{url.lstrip('/')}"


def _seller(contact: Any, development: Any) -> tuple[str, str | None]:
    contact = _dict(contact)
    company = _dict(contact.get("company"))
    person = _dict(contact.get("person"))
    company_type = normalize_text(str(company.get("type") or ""))
    person_type = normalize_text(str(person.get("type") or ""))
    name = _text(company.get("name")) or _text(person.get("name"))

    if company_type == "developer":
        return "developer", name
    if development:
        return "developer", name
    if company_type == "agency" or person_type == "agent":
        return "agency", name
    if person_type == "owner":
        return "private", name
    if company or person:
        return "agency", name
    return "unknown", name


def _county_names(regions: RegionRegistry) -> frozenset[str]:
    return frozenset(
        normalize_text(county)
        for region in regions.config.regions.values()
        for county in region.counties
    )


class GratkaAdapter:
    """Parse Gratka's server-rendered Nuxt payloads."""

    id = "gratka"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions
        self._counties = _county_names(regions)

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            slug = self._region_slug(region)
            for path, offer_type, property_type, suffix in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{path}:{offer_type}",
                        url=f"{BASE_URL}/nieruchomosci/{path}/{slug}{suffix}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        page = max(1, min(page, MAX_PAGES))
        if page == 1:
            # robots.txt disallows ?page=1, so page 1 keeps the bare URL.
            return replace(query, page=1)
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["page"] = str(page)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _nuxt_payload(html)
        search = _search_result(payload)

        nodes = [*_promoted_nodes(search), *_search_nodes(search)]
        items: list[Listing] = []
        seen: set[str] = set()
        for node in nodes:
            listing = self._summary(node, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        if nodes and not items:
            raise ParseError("Gratka search page has no parseable listings")

        total_items = _total_items(payload, search)
        page_size = len(_search_nodes(search)) or PAGE_SIZE_FALLBACK
        total_pages = None
        if total_items is not None:
            total_pages = max(1, min(MAX_PAGES, math.ceil(total_items / page_size)))

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _nuxt_payload(html)
        data = _property_data(payload)
        labels = _labels(data)

        location = self._location(data.get("location"))
        lat = location["lat"]
        lng = location["lng"]
        voivodeship = location["voivodeship"] or self.regions.region_for_point(lat, lng)

        offer_type = _TRANSACTION_TO_OFFER.get(
            str(data.get("transaction") or "").upper(),
            summary.offer_type,
        )
        property_type = _ROOT_TYPE_TO_PROPERTY.get(
            str(_dict(data.get("types")).get("primaryTypeRoot") or "").upper(),
            summary.property_type,
        )

        price = _amount(data.get("price"))
        price_kind = "asking" if price is not None else "call"
        area = parse_area(data.get("area"))
        land_area = parse_area(_label(labels, "Pow. działki"))
        if land_area is None and property_type == "plot":
            land_area = area

        floor_number = safe_int(data.get("floor"))
        floor_label = _text(data.get("floorFormatted")) or _label(labels, "Piętro")
        if floor_number is None:
            floor_number, floor_label = _floor(floor_label)

        rooms = safe_int(data.get("numberOfRoomsCount")) or parse_rooms(
            _label(labels, "Liczba pokoi") or data.get("numberOfRooms"),
        )

        images = self._images(data.get("photos"))
        seller_type, seller_name = _seller(data.get("contact"), data.get("development"))

        extra = dict(summary.extra)
        offer_details = {
            str(item.get("label")): item.get("value")
            for item in data.get("offerDetailedInformation") or []
            if isinstance(item, dict)
        }
        gratka_extra = dict(extra.get(self.id) or {})
        gratka_extra.update(
            {
                "id": data.get("id"),
                "reference": data.get("reference"),
                "transaction": data.get("transaction"),
                "market_type": data.get("marketType"),
                "type": _dict(data.get("types")).get("primaryType"),
                "status": data.get("status"),
                "labels": labels,
                "highlighted": [
                    {
                        "type": _text(item.get("type")),
                        "label": _text(item.get("label")),
                        "value": _text(item.get("value")),
                    }
                    for item in data.get("highlightedParameters") or []
                    if isinstance(item, dict)
                ],
                "offer_details": offer_details,
            },
        )
        extra[self.id] = gratka_extra

        return replace(
            summary,
            external_id=_text(data.get("idOnFrontend")) or summary.external_id,
            url=_absolute(_text(data.get("url"))) or summary.url,
            title=_text(data.get("title")) or summary.title,
            description=_description(data.get("description")),
            offer_type=offer_type,
            property_type=property_type,
            market=_market(data.get("marketType")),
            price_kind=price_kind,
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            rent_pln=_label_money(_label(labels, "Dodatkowe koszty", "Czynsz")),
            deposit_pln=_label_money(_label(labels, "Depozyt za wynajem", "Kaucja")),
            price_per_m2_pln=_amount(data.get("priceM2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor_number,
            floor_label=floor_label,
            build_year=safe_int(_label(labels, "Rok budowy")),
            voivodeship=voivodeship,
            county=location["county"],
            commune=location["commune"],
            city=location["city"],
            district=location["district"],
            street=location["street"],
            address_text=location["address_text"],
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images,
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=(
                _date_label(_label(offer_details, "Data dodania")) or summary.published_at
            ),
            modified_at=_date_label(_label(offer_details, "Aktualizacja")),
            extra=extra,
        )

    def _summary(self, node: dict[str, Any], query: Query) -> Listing | None:
        external_id = _text(node.get("idOnFrontend"))
        url = _absolute(_text(node.get("url")))
        if external_id is None or url is None:
            return None

        location = self._location(node.get("location"))
        property_type = self._property_type(node, query)
        price = _amount(node.get("price"))
        area = parse_area(node.get("area"))
        floor, floor_label = _floor(node.get("floorFormatted"))
        seller_type, seller_name = _seller(node.get("contact"), node.get("development"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=_text(node.get("advertisementText")) or _text(node.get("title")),
            description=_description(node.get("description")),
            offer_type=query.offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_amount(node.get("priceM2")),
            area_m2=area,
            land_area_m2=area if property_type == "plot" else None,
            rooms=parse_rooms(node.get("numberOfRooms")),
            floor=floor,
            floor_label=floor_label,
            voivodeship=location["voivodeship"],
            county=location["county"],
            commune=location["commune"],
            city=location["city"],
            district=location["district"],
            street=location["street"],
            address_text=location["address_text"],
            images=self._images(node.get("photos")),
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=parse_datetime_ms(node.get("addedAt")),
            extra={
                self.id: {
                    "id": node.get("id"),
                    "highlight": node.get("highlightText"),
                    "is_promoted": bool(node.get("isTopPromoted")),
                },
            },
        )

    def _property_type(self, node: dict[str, Any], query: Query) -> PropertyType:
        root = _dict(node.get("types")).get("primaryTypeRoot")
        return _ROOT_TYPE_TO_PROPERTY.get(str(root or "").upper(), query.property_type)

    def _location(self, raw: Any) -> dict[str, Any]:
        location = _dict(raw)
        levels = [
            str(level).strip()
            for level in location.get("location") or []
            if isinstance(level, str) and str(level).strip()
        ]

        voivodeship = None
        remaining = levels
        if levels:
            voivodeship = self.regions.canonical_voivodeship(levels[0])
            if voivodeship is not None:
                remaining = levels[1:]

        county = _text(location.get("county"))
        if county is not None:
            remaining = [
                level for level in remaining if normalize_text(level) != normalize_text(county)
            ]
        else:
            for index, level in enumerate(remaining):
                if normalize_text(level) in self._counties:
                    county = level
                    remaining = remaining[:index] + remaining[index + 1 :]
                    break

        city = remaining[0] if remaining else None
        district = remaining[1] if len(remaining) > 1 else None
        if district and city and normalize_text(district) == normalize_text(city):
            district = None

        street = _text(location.get("street"))
        center = _dict(_dict(location.get("map")).get("center"))
        address_text = ", ".join(part for part in (street, city) if part) or None

        return {
            "voivodeship": voivodeship,
            "county": county,
            "commune": _text(location.get("commune")),
            "city": city,
            "district": district,
            "street": street,
            "address_text": address_text,
            "lat": safe_float(center.get("latitude")),
            "lng": safe_float(center.get("longitude")),
        }

    def _images(self, photos: Any) -> list[str]:
        if not isinstance(photos, list):
            return []
        images: list[str] = []
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            photo_id = _text(photo.get("id"))
            name = _text(photo.get("name"))
            if photo_id is None or name is None:
                continue
            images.append(IMAGE_URL.format(photo_id=photo_id, name=name))
        return images[:20]

    def _region_slug(self, region: str) -> str:
        name = self.regions.region(region).name if self.regions.has_region(region) else region
        return normalize_text(name).replace(" ", "-")
