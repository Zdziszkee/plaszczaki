"""Komornik adapter (licytacje.komornik.pl bailiff auctions).

The portal is a Nuxt application. Search pages embed a *devalue* payload in
``<script id="__NUXT_DATA__">``: a flattened array whose objects and lists
reference their children by integer index. The search payload already contains
everything needed to build a listing (auction id, title, starting price, dates,
case data and a decomposed address), so detail pages are not required.

The detail route is WAF protected for automated clients, therefore
:meth:`KomornikAdapter.parse_detail` intentionally returns the summary
unchanged instead of depending on it.

Pagination is offset based (20 auctions per page), and the site exposes a
server-side province filter on ``/wyszukiwarka-licytacji``.
"""

from __future__ import annotations

import html as html_module
import json
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://licytacje.komornik.pl"
SEARCH_PATH = "/wyszukiwarka-licytacji"
# The site serves 20 auctions per offset page.
PAGE_SIZE = 20
MAX_PAGES = 200

WARSAW = ZoneInfo("Europe/Warsaw")

NUXT_DATA_RE = re.compile(
    r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
DETAIL_HREF_RE = re.compile(
    r"""href=["'](?:https?://licytacje\.komornik\.pl)?(/licytacje/(\d+)/[^"']*)["']""",
    re.IGNORECASE,
)
AREA_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:m\s*2|m²|m\^2|ha)\b",
    re.IGNORECASE,
)
CASE_RE = re.compile(
    r"sygn\.?\s*akt\.?\s*([A-Za-z]{1,4}[\s.]*\d+/\d+[A-Za-z0-9./-]*)",
    re.IGNORECASE,
)
ROUND_RE = re.compile(
    r"\b(pierwsz|drug|trzec|czwart|piat|szost)\w*\s+(?:ustn\w*\s+)?licytacj",
)
ROMAN_ROUND_RE = re.compile(r"\b(i{1,3}|iv|v)\s+licytacj\w*")

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

_SUBCATEGORY_TO_PROPERTY: dict[str, PropertyType] = {
    "HOUSES": "house",
    "APARTMENTS": "flat",
    "LAND": "plot",
    "GARAGES": "garage",
    "COMMERCIAL_PREMISESISES": "commercial",
    "WAREHOUSES_AND_HALLS": "commercial",
    "OTHER": "other",
    "SEA_VESSELS": "other",
}

_PROPERTY_KEYWORDS: tuple[tuple[PropertyType, tuple[str, ...]], ...] = (
    (
        "flat",
        (
            "mieszkanie",
            "mieszkania",
            "lokal mieszkalny",
            "lokalu mieszkalnego",
            "apartament",
            "kawalerka",
        ),
    ),
    (
        "house",
        (
            "dom",
            "domu",
            "dom jednorodzinny",
            "budynek mieszkalny",
            "zabudowana budynkiem mieszkalnym",
        ),
    ),
    ("plot", ("dzialka", "grunt", "nieruchomosc gruntowa", "nieruchomosci gruntowe")),
    (
        "commercial",
        (
            "lokal uzytkowy",
            "lokalu uzytkowego",
            "biuro",
            "magazyn",
            "hala",
            "sklep",
            "uslugowy",
        ),
    ),
    ("garage", ("garaz", "garazu", "miejsce postojowe", "miejsce parkingowe")),
    ("room", ("pokoj", "pokoju")),
)

_ROUND_KEYS = ("auctionRound", "auction_round", "round", "roundNumber", "licytacjaRound")
_CASE_KEYS = ("caseNumber", "case_number", "caseNo", "sygnatura", "signature")
_ROUNDS = {
    "pierwsz": 1,
    "drug": 2,
    "trzec": 3,
    "czwart": 4,
    "piat": 5,
    "szost": 6,
}
_ROMAN_ROUNDS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}


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
            raise ParseError("Komornik __NUXT_DATA__ is nested too deeply")
        if index in _SPECIAL_VALUES:
            return _SPECIAL_VALUES[index]
        if index in cache:
            return cache[index]
        if not 0 <= index < len(flat):
            raise ParseError(f"Komornik __NUXT_DATA__ has a dangling reference: {index}")

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
        raise ParseError("Komornik page is missing __NUXT_DATA__")
    try:
        flat = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"Komornik __NUXT_DATA__ is not valid JSON: {exc}") from exc
    if not isinstance(flat, list):
        raise ParseError("Komornik __NUXT_DATA__ is not a flattened array")
    payload = _hydrate(flat)
    if not isinstance(payload, dict):
        raise ParseError("Komornik __NUXT_DATA__ root is not an object")
    return payload


def _search_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Return the auction items and the reported total count."""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseError("Komornik payload is missing data")

    block = data.get("search-items")
    if not isinstance(block, dict):
        block = next(
            (
                value
                for value in data.values()
                if isinstance(value, dict) and isinstance(value.get("items"), list)
            ),
            None,
        )
    if not isinstance(block, dict):
        raise ParseError("Komornik payload is missing search-items")

    items = [item for item in block.get("items") or [] if isinstance(item, dict)]
    return items, safe_int(block.get("count"))


def _detail_urls(html: str) -> dict[str, str]:
    """Map auction ids to the detail links rendered on the search page."""
    urls: dict[str, str] = {}
    for match in DETAIL_HREF_RE.finditer(html):
        href = html_module.unescape(match.group(1))
        urls.setdefault(match.group(2), f"{BASE_URL}{href}")
    return urls


def _money(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        amount = round(value)
        return amount if amount > 0 else None
    return parse_price(value)


def _date_ms(value: Any) -> int | None:
    """Parse an ISO timestamp, treating naive values as Europe/Warsaw."""
    text = _text(value)
    if text is None:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return parse_datetime_ms(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return int(parsed.timestamp() * 1000)


def _area_m2(title: str | None) -> float | None:
    if title is None:
        return None
    match = AREA_RE.search(title)
    if match is None:
        return None
    return parse_area(match.group(0))


def _coordinates(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    location = _dict(raw.get("location"))
    lat = safe_float(location.get("lat"))
    lng = safe_float(location.get("lon", location.get("lng")))
    if lat is None or lng is None:
        return None, None
    # The portal ships 0,0 for auctions without a geocoded location.
    if lat == 0 and lng == 0:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None
    return lat, lng


def _auction_round(raw: dict[str, Any], title: str | None) -> int | None:
    for key in _ROUND_KEYS:
        value = safe_int(raw.get(key))
        if value is not None and value > 0:
            return value

    normalized = normalize_text(title or "")
    match = ROUND_RE.search(normalized)
    if match is not None:
        return _ROUNDS.get(match.group(1))
    match = ROMAN_ROUND_RE.search(normalized)
    if match is not None:
        return _ROMAN_ROUNDS.get(match.group(1))
    return None


def _case_number(raw: dict[str, Any], title: str | None) -> str | None:
    for key in _CASE_KEYS:
        value = _text(raw.get(key))
        if value is not None:
            return value
    match = CASE_RE.search(title or "")
    if match is not None:
        return " ".join(match.group(1).split())
    return None


def _address_text(
    street: str | None, building: str | None, flat: str | None, zip_code: str | None
) -> str | None:
    number = building or ""
    if building and flat:
        number = f"{building}/{flat}"
    elif flat:
        number = flat
    line = " ".join(part for part in (street, number) if part) or None
    return ", ".join(part for part in (line, zip_code) if part) or None


def _property_type(raw: dict[str, Any], title: str | None) -> PropertyType:
    subcategory = _text(raw.get("subCategory"))
    mapped = _SUBCATEGORY_TO_PROPERTY.get((subcategory or "").upper())
    if mapped is not None:
        return mapped

    normalized = normalize_text(title or "")
    for property_type, keywords in _PROPERTY_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return property_type
    return "other"


class KomornikAdapter:
    """Parse licytacje.komornik.pl's server-rendered Nuxt search payloads."""

    id = "komornik"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            name = self.regions.region(region).name if self.regions.has_region(region) else region
            params = urlencode({"mainCategory": "REAL_ESTATE", "province": name})
            queries.append(
                Query(
                    key=f"{region}:all",
                    url=f"{BASE_URL}{SEARCH_PATH}?{params}",
                    region=region,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        page = max(1, min(page, MAX_PAGES))
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["offset"] = str((page - 1) * PAGE_SIZE)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _nuxt_payload(html)
        raw_items, total_items = _search_payload(payload)
        urls = _detail_urls(html)

        items: list[Listing] = []
        seen: set[str] = set()
        for raw in raw_items:
            listing = self._summary(raw, query, urls)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        if raw_items and not items:
            raise ParseError("Komornik search page has no parseable listings")

        total_pages: int | None
        if total_items is not None:
            total_pages = max(1, min(MAX_PAGES, math.ceil(total_items / PAGE_SIZE)))
        else:
            total_pages = query.page if len(items) < PAGE_SIZE else query.page + 1

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        # Detail pages are WAF protected; the search payload is complete enough.
        return summary

    def _summary(
        self,
        raw: dict[str, Any],
        query: Query,
        urls: dict[str, str],
    ) -> Listing | None:
        external_id = _text(raw.get("id"))
        if external_id is None:
            return None

        address = _dict(raw.get("address"))
        street = " ".join((_text(address.get("street")) or "").split()) or None
        building = _text(address.get("buildingNo"))
        flat = _text(address.get("flatNo"))
        zip_code = _text(address.get("zipCode"))
        city = _text(address.get("city"))

        province = _text(raw.get("province")) or _text(address.get("province"))
        voivodeship = self.regions.canonical_voivodeship(province) or province

        title = _text(raw.get("title"))
        property_type = _property_type(raw, title)
        price = _money(raw.get("openingValue"))
        area = _area_m2(title)
        published_at = _date_ms(raw.get("dateCreated"))
        lat, lng = _coordinates(raw)

        extra: dict[str, Any] = {
            "postal_code": zip_code,
            self.id: {
                "id": raw.get("id"),
                "notice_id": safe_int(raw.get("noticeId")),
                "sub_category": _text(raw.get("subCategory")),
                "main_category": _text(raw.get("mainCategory")),
                "status": _text(raw.get("status")),
                "estimate": _money(raw.get("estimate")),
                "opening_value": price,
                "eauction": bool(raw.get("eauction")),
                "joinable": bool(raw.get("joinable")),
                "margin_due_at": _date_ms(raw.get("marginDueDate")),
                "end_auction_at": _date_ms(raw.get("endAuctionAt")),
                "date_created": published_at,
                "province": province,
                "city": city,
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urls.get(external_id, f"{BASE_URL}/licytacje/{external_id}"),
            title=title,
            description=_text(raw.get("explanation")),
            offer_type="auction",
            property_type=property_type,
            market="secondary",
            price_kind="starting",
            price_unit="total",
            price_pln=price,
            area_m2=area,
            land_area_m2=area if property_type == "plot" else None,
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=_address_text(street, building, flat, zip_code),
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            seller_type="bailiff",
            published_at=published_at,
            auction_date=_date_ms(raw.get("startAuctionAt")),
            auction_round=_auction_round(raw, title),
            case_number=_case_number(raw, title),
            extra=extra,
        )
