"""OLX adapter (server-rendered search and detail state).

OLX renders its listing data into ``window.__PRERENDERED_STATE__`` inside the
``#olx-init-config`` script. Search pages expose the result list there and detail
pages expose the full ad, including the ``map`` object with coordinates and the
description. Cards for ads served by other portals (for example Otodom) are
skipped so the adapter only follows OLX detail pages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PriceKind, PropertyType
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

BASE_URL = "https://www.olx.pl"
STATE_MARKER = "window.__PRERENDERED_STATE__"
_JSON_DECODER = json.JSONDecoder()
_ID_RE = re.compile(r"-ID([A-Za-z0-9]+)\.html")
_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# (category slug, offer slug or None for a single-offer category, offer, property)
CATEGORIES: tuple[tuple[str, str | None, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("dzialki", "sprzedaz", "sale", "plot"),
    ("dzialki", "wynajem", "rent", "plot"),
    ("biura-lokale", "sprzedaz", "sale", "commercial"),
    ("biura-lokale", "wynajem", "rent", "commercial"),
    ("hale-magazyny", "sprzedaz", "sale", "commercial"),
    ("hale-magazyny", "wynajem", "rent", "commercial"),
    ("garaze-parkingi", "sprzedaz", "sale", "garage"),
    ("garaze-parkingi", "wynajem", "rent", "garage"),
    # Rooms are a rent-only category without a transaction segment.
    ("stancje-pokoje", None, "rent", "room"),
)


@dataclass(frozen=True, slots=True)
class _Geo:
    voivodeship: str | None
    city: str | None
    district: str | None
    address_text: str | None
    lat: float | None
    lng: float | None


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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return False


def _host(url: str) -> str:
    return (urlsplit(url).netloc or "").lower().removeprefix("www.")


def _prerendered_state(html: str) -> dict[str, Any]:
    """Decode the JSON string assigned to ``window.__PRERENDERED_STATE__``."""
    index = html.find(STATE_MARKER)
    if index < 0:
        raise ParseError("OLX page is missing window.__PRERENDERED_STATE__")

    rest = html[index + len(STATE_MARKER) :].lstrip()
    if not rest.startswith("="):
        raise ParseError("OLX __PRERENDERED_STATE__ is not an assignment")
    rest = rest[1:].lstrip()

    try:
        literal, _ = _JSON_DECODER.raw_decode(rest)
    except json.JSONDecodeError as exc:
        raise ParseError(f"OLX __PRERENDERED_STATE__ is not valid JSON: {exc}") from exc

    if isinstance(literal, str):
        try:
            payload = json.loads(literal)
        except json.JSONDecodeError as exc:
            raise ParseError(f"OLX __PRERENDERED_STATE__ payload is invalid: {exc}") from exc
    else:
        payload = literal

    if not isinstance(payload, dict):
        raise ParseError("OLX __PRERENDERED_STATE__ is not an object")
    return payload


def _search_listing(payload: dict[str, Any]) -> dict[str, Any]:
    listing = payload.get("listing")
    if not isinstance(listing, dict):
        raise ParseError("OLX search state is missing listing")

    inner = listing.get("listing")
    if not isinstance(inner, dict):
        inner = listing
    if not isinstance(inner.get("ads"), list):
        raise ParseError("OLX search state is missing ads")
    return inner


def _detail_ad(payload: dict[str, Any]) -> dict[str, Any]:
    container = payload.get("ad")
    if not isinstance(container, dict):
        raise ParseError("OLX detail state is missing ad")

    ad = container.get("ad")
    if not isinstance(ad, dict):
        ad = container
    if ad.get("id") is None:
        raise ParseError("OLX detail ad is missing id")
    return ad


def _params(ad: dict[str, Any]) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for item in ad.get("params") or []:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("key"))
        if key:
            params[key] = item
    return params


def _param_value(item: dict[str, Any], *, normalized: bool) -> Any:
    value = item.get("value")
    if isinstance(value, dict):
        if normalized:
            return _first(value.get("key"), value.get("value"), item.get("normalizedValue"))
        return _first(value.get("label"), value.get("value"), item.get("normalizedValue"))
    if normalized:
        return _first(item.get("normalizedValue"), value)
    return value


def _param(
    params: dict[str, dict[str, Any]],
    *keys: str,
    normalized: bool = False,
) -> Any:
    for key in keys:
        item = params.get(key)
        if isinstance(item, dict):
            value = _param_value(item, normalized=normalized)
            if value is not None:
                return value
    return None


def _param_snapshot(params: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for key, item in params.items():
        snapshot[key] = {
            "value": _param_value(item, normalized=False),
            "normalized": _param_value(item, normalized=True),
        }
    return snapshot


def _description(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return strip_html(_BREAK_RE.sub(" ", text))


def _area_from_card(card: Any) -> float | None:
    for node in card.cssselect("span, p, div"):
        text = " ".join(node.text_content().split())
        match = re.match(r"(\d[\d\s\u00a0.,]*)\s*m²", text)
        if match:
            return parse_area(match.group(1))
    return None


def _images(ad: dict[str, Any]) -> list[str]:
    photos = ad.get("photos")
    if not isinstance(photos, list):
        return []

    images: list[str] = []
    for photo in photos:
        url = _first(photo.get("link"), photo.get("url")) if isinstance(photo, dict) else photo
        text = _text(url)
        if text:
            images.append(text)
    return images[:20]


def _is_olx_ad(ad: dict[str, Any]) -> bool:
    if _text(ad.get("externalUrl")):
        return False
    url = _text(ad.get("url"))
    return url is not None and _host(url) == "olx.pl"


def _external_id(ad: dict[str, Any]) -> str:
    identifier = _text(ad.get("id"))
    if identifier:
        return identifier
    url = _text(ad.get("url")) or ""
    match = _ID_RE.search(url)
    return match.group(1) if match else ""


def _market(params: dict[str, dict[str, Any]]) -> Market:
    raw = _first(_param(params, "market"), _param(params, "market", normalized=True))
    normalized = normalize_text(str(raw)) if raw is not None else ""
    if normalized in {"pierwotny", "rynek pierwotny", "primary"}:
        return "primary"
    if normalized in {"wtorny", "rynek wtorny", "secondary"}:
        return "secondary"
    return "unknown"


def _price(ad: dict[str, Any], params: dict[str, dict[str, Any]]) -> int | None:
    price = _dict(ad.get("price"))
    regular = _dict(price.get("regularPrice"))
    amount = parse_price(_first(regular.get("value"), price.get("displayValue")))
    if amount is None:
        amount = parse_price(_param(params, "price", normalized=True))
    if amount is None:
        amount = parse_price(_param(params, "price"))
    return amount


def _price_kind(ad: dict[str, Any], amount: int | None) -> PriceKind:
    if amount is not None:
        return "asking"

    price = _dict(ad.get("price"))
    display = normalize_text(
        str(_first(price.get("displayValue"), price.get("regularPrice")) or "")
    )
    if _truthy(price.get("budget")) or "zapytaj" in display or "zamien" in display:
        return "call"
    return "unknown"


def _area(ad: dict[str, Any], params: dict[str, dict[str, Any]]) -> float | None:
    return parse_area(_param(params, "m", normalized=True)) or parse_area(_param(params, "m"))


def _land_area(
    property_type: PropertyType,
    area: float | None,
    params: dict[str, dict[str, Any]],
) -> float | None:
    if property_type == "plot":
        return area
    if property_type == "house":
        return parse_area(_param(params, "area", normalized=True)) or parse_area(
            _param(params, "area"),
        )
    return None


def _rooms(params: dict[str, dict[str, Any]], property_type: PropertyType) -> int | None:
    if property_type == "room":
        return None
    value = _first(_param(params, "rooms"), _param(params, "rooms", normalized=True))
    return parse_rooms(value)


def _floor(params: dict[str, dict[str, Any]]) -> tuple[int | None, str | None]:
    label = _first(
        _param(params, "floor_select"),
        _param(params, "floor"),
        _param(params, "floor_no"),
    )
    if label is None:
        normalized = _first(
            _param(params, "floor_select", normalized=True),
            _param(params, "floor", normalized=True),
            _param(params, "floor_no", normalized=True),
        )
        if normalized is not None:
            label = str(normalized).removeprefix("floor_")
    return parse_floor(label)


def _price_per_m2(params: dict[str, dict[str, Any]]) -> int | None:
    # ``parse_price`` strips decimal separators, so parse the decimal value as
    # an area and round it instead.
    value = parse_area(
        _first(
            _param(params, "price_per_m", normalized=True),
            _param(params, "price_per_m"),
        ),
    )
    return round(value) if value is not None else None


def _build_year(params: dict[str, dict[str, Any]]) -> int | None:
    return safe_int(
        _first(
            _param(params, "build_year", normalized=True),
            _param(params, "build_year"),
            _param(params, "year", normalized=True),
            _param(params, "construction_year", normalized=True),
        ),
    )


def _seller(ad: dict[str, Any]) -> tuple[str, str | None]:
    user = _dict(ad.get("user"))
    company = _text(user.get("company_name"))
    business = _truthy(ad.get("isBusiness")) or bool(company)
    seller_type = "agency" if business else "private"
    seller_name = _text(user.get("name")) or company
    return seller_type, seller_name


class OlxAdapter:
    """Parse OLX's server-rendered listing state."""

    id = "olx"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for category, offer, offer_type, property_type in CATEGORIES:
                if offer is None:
                    url = f"{BASE_URL}/nieruchomosci/{category}/{region}/"
                else:
                    url = f"{BASE_URL}/nieruchomosci/{category}/{offer}/{region}/"
                queries.append(
                    Query(
                        key=f"{region}:{offer or 'all'}:{category}",
                        url=url,
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        # OLX canonicalises ?page=1 to the bare search URL with a 301.
        if page <= 1:
            return query
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        if STATE_MARKER not in html:
            return self._parse_search_dom(html, query)

        listing = _search_listing(_prerendered_state(html))
        items: list[Listing] = []
        for ad in listing.get("ads") or []:
            if not isinstance(ad, dict) or not _is_olx_ad(ad):
                continue
            items.append(
                self._listing_from_ad(
                    ad,
                    offer_type=query.offer_type,
                    property_type=query.property_type,
                ),
            )

        page_number = safe_int(listing.get("pageNumber"))
        current_page = page_number + 1 if page_number is not None else query.page
        return SearchPage(
            items=items,
            current_page=current_page,
            total_pages=safe_int(listing.get("totalPages")),
            total_items=safe_int(listing.get("totalElements")),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        if STATE_MARKER not in html:
            # JSON-LD on detail pages has no coordinates, so a partial listing
            # would violate the source contract.
            raise ParseError(
                "OLX detail page is missing __PRERENDERED_STATE__ (coordinates unavailable)",
            )

        ad = _detail_ad(_prerendered_state(html))
        detail = self._listing_from_ad(
            ad,
            offer_type=summary.offer_type,
            property_type=summary.property_type,
        )

        detail.url = detail.url or summary.url
        detail.external_id = detail.external_id or summary.external_id
        detail.title = detail.title or summary.title
        detail.description = detail.description or summary.description
        detail.images = detail.images or summary.images
        detail.voivodeship = detail.voivodeship or summary.voivodeship
        detail.city = detail.city or summary.city
        detail.district = detail.district or summary.district
        return detail

    def _listing_from_ad(
        self,
        ad: dict[str, Any],
        *,
        offer_type: OfferType,
        property_type: PropertyType,
    ) -> Listing:
        params = _params(ad)
        price = _price(ad, params)
        area = _area(ad, params)
        floor, floor_label = _floor(params)
        geo = self._geo(ad)
        seller_type, seller_name = _seller(ad)

        return Listing(
            source_id=self.source_id,
            external_id=_external_id(ad),
            url=_text(ad.get("url")) or "",
            title=_text(ad.get("title")),
            description=_description(ad.get("description")),
            offer_type=offer_type,
            property_type=property_type,
            market=_market(params),
            price_kind=_price_kind(ad, price),
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_price_per_m2(params),
            area_m2=area,
            land_area_m2=_land_area(property_type, area, params),
            rooms=_rooms(params, property_type),
            floor=floor,
            floor_label=floor_label,
            build_year=_build_year(params),
            voivodeship=geo.voivodeship,
            city=geo.city,
            district=geo.district,
            address_text=geo.address_text,
            lat=geo.lat,
            lng=geo.lng,
            geo_source="source" if geo.lat is not None and geo.lng is not None else None,
            images=_images(ad),
            seller_type=seller_type,
            seller_name=seller_name,
            published_at=parse_datetime_ms(ad.get("createdTime")),
            modified_at=parse_datetime_ms(ad.get("lastRefreshTime")),
            extra={
                "olx": {
                    "params": _param_snapshot(params),
                    "category_id": _dict(ad.get("category")).get("id"),
                    "status": _text(ad.get("status")),
                    "is_business": _truthy(ad.get("isBusiness")),
                    "external_url": _text(ad.get("externalUrl")),
                },
            },
        )

    def _geo(self, ad: dict[str, Any]) -> _Geo:
        location = _dict(ad.get("location"))
        place = _dict(ad.get("map"))
        lat = safe_float(place.get("lat"))
        lng = safe_float(_first(place.get("lon"), place.get("lng"), place.get("longitude")))

        voivodeship = self.regions.canonical_voivodeship(_text(location.get("regionName")))
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        city = _text(location.get("cityName"))
        district = _text(location.get("districtName"))
        address_text = _first(
            ", ".join(part for part in (city, district) if part) or None,
            _text(location.get("pathName")),
        )

        return _Geo(
            voivodeship=voivodeship,
            city=city,
            district=district,
            address_text=address_text,
            lat=lat,
            lng=lng,
        )

    def _parse_search_dom(self, html: str, query: Query) -> SearchPage:
        """Fallback for pages without the embedded state (DOM cards only)."""
        try:
            document = lxml_html.fromstring(html)
        except (TypeError, ValueError) as exc:
            raise ParseError("OLX search HTML cannot be parsed") from exc

        cards = document.cssselect('[data-cy="l-card"]')
        if not cards:
            raise ParseError("OLX search page is missing listings")

        items: list[Listing] = []
        for card in cards:
            links = card.cssselect('a[data-testid="card-title-link"]') or card.cssselect("a[href]")
            if not links:
                continue
            url = urljoin(BASE_URL, links[0].get("href") or "")
            if _host(url) != "olx.pl":
                continue

            title_nodes = card.cssselect("h4")
            title = " ".join(title_nodes[0].text_content().split()) if title_nodes else None
            price_nodes = card.cssselect('[data-testid="ad-price"]')
            price = parse_price(price_nodes[0].text_content()) if price_nodes else None
            location_nodes = card.cssselect('[data-testid="location-date"]')
            location_text = (
                " ".join(location_nodes[0].text_content().split()) if location_nodes else ""
            )
            city = location_text.split(" - ", 1)[0].strip() or None
            identifier = _text(card.get("id"))
            if identifier is None:
                id_match = _ID_RE.search(url)
                identifier = id_match.group(1) if id_match else ""

            items.append(
                Listing(
                    source_id=self.source_id,
                    external_id=identifier,
                    url=url,
                    title=title,
                    offer_type=query.offer_type,
                    property_type=query.property_type,
                    price_kind="asking" if price is not None else "unknown",
                    price_unit="monthly" if query.offer_type == "rent" else "total",
                    price_pln=price,
                    area_m2=_area_from_card(card),
                    voivodeship=self.regions.canonical_voivodeship(query.region),
                    city=city,
                ),
            )

        return SearchPage(items=items, current_page=query.page)
