"""Rentumo adapter (server-rendered search cards + JSON-LD detail pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://rentumo.pl"

# Major city landing pages per voivodeship. Rentumo has no voivodeship-level
# search URL; these are the city pages listed in its location sitemap.
CITY_SLUGS: dict[str, tuple[str, ...]] = {
    "malopolskie": (
        "krakow-malopolskie",
        "tarnow-malopolskie",
        "nowy-sacz-malopolskie",
        "zakopane-malopolskie",
    ),
    "slaskie": (
        "katowice-slaskie",
        "gliwice-slaskie",
        "czestochowa-slaskie",
        "bielsko-biala-slaskie",
    ),
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "apartment": "flat",
    "kawalerka": "flat",
    "dom": "house",
    "house": "house",
    "pokoj": "room",
    "room": "room",
    "lokal": "commercial",
    "biuro": "commercial",
}

_NEXT_LINK_RE = re.compile(r"<link[^>]+rel=['\"]next['\"]", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"Miesi[ęe]czny czynsz[^\d]{0,20}([\d\s\u00a0.,]+)\s*z[łl]",
    re.IGNORECASE,
)
_ALT_PRICE_RE = re.compile(
    r"Czynsz najmu[^\d]{0,20}([\d\s\u00a0.,]+)\s*z[łl]",
    re.IGNORECASE,
)
_DEPOSIT_RE = re.compile(r"Kaucja[^\d]{0,40}([\d\s\u00a0.,]+)\s*z[łl]", re.IGNORECASE)
_ADMIN_RENT_RE = re.compile(
    r"czynsz administracyjny[^\d]{0,40}([\d\s\u00a0.,]+)\s*z[łl]",
    re.IGNORECASE,
)
_BUILD_YEAR_RE = re.compile(r"Rok budowy[^\d]{0,10}(\d{4})", re.IGNORECASE)


class RentumoAdapter:
    """Parse Rentumo's server-rendered cards and JSON-LD detail payloads."""

    id = "rentumo"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for slug in CITY_SLUGS.get(region, ()):
                queries.append(
                    Query(
                        key=f"{region}:wynajem:mieszkanie:{slug}",
                        url=f"{BASE_URL}/wynajmij-mieszkanie/{slug}",
                        region=region,
                        offer_type="rent",
                        property_type="flat",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _document(html)
        cards = tree.cssselect("[data-testid='listing-card']")
        if not cards:
            if tree.cssselect("[data-testid='listings-results']"):
                return SearchPage(items=[], current_page=query.page, total_pages=query.page)
            normalized = normalize_text(html)
            if any(
                marker in normalized
                for marker in ("nie znalezlismy", "brak ogloszen", "brak ofert")
            ):
                return SearchPage(items=[], current_page=query.page, total_pages=query.page)
            raise ParseError("Rentumo search page has no listing results container")

        items = [self._summary(card, query) for card in cards]
        has_next = bool(_NEXT_LINK_RE.search(html))
        total_pages = query.page + 1 if has_next else query.page
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _document(html)
        ad = _listing_json_ld(html)
        if ad is None:
            raise ParseError("Rentumo detail page is missing JSON-LD listing data")

        address = _dict(ad.get("address"))
        floor_size = _dict(ad.get("floorSize"))
        description = strip_html(_text(ad.get("description")))
        page_text = " ".join(tree.text_content().split())

        city = _text(address.get("addressLocality")) or summary.city
        street = _text(address.get("streetAddress")) or summary.street
        district = _district_from_breadcrumbs(tree) or summary.district
        voivodeship = self.regions.canonical_voivodeship(
            _text(address.get("addressRegion")),
        )
        if voivodeship is None:
            match = self.regions.resolve(
                city=city,
                county=summary.county,
                lat=safe_float(_map_value(tree, "latitude")),
                lng=safe_float(_map_value(tree, "longitude")),
            )
            voivodeship = match.voivodeship if match else summary.voivodeship

        lat = safe_float(_map_value(tree, "latitude"))
        lng = safe_float(_map_value(tree, "longitude"))
        if lat is None or lng is None:
            lat = summary.lat
            lng = summary.lng

        price = (
            _search_price(_PRICE_RE, page_text)
            or _search_price(_ALT_PRICE_RE, page_text)
            or summary.price_pln
        )
        area = parse_area(floor_size.get("value")) or summary.area_m2
        rooms = parse_rooms(ad.get("numberOfRooms")) or summary.rooms
        property_type = _property_type(ad.get("@type"), summary.property_type)

        extra = dict(summary.extra)
        extra["rentumo"] = {
            "listing_id": summary.external_id,
            "breadcrumbs": _breadcrumbs(tree),
        }

        return replace(
            summary,
            url=_text(ad.get("url")) or summary.url,
            title=_text(ad.get("name")) or _heading(tree) or summary.title,
            description=description or summary.description,
            property_type=property_type,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly",
            price_pln=price,
            rent_pln=_search_price(_ADMIN_RENT_RE, page_text),
            deposit_pln=_search_price(_DEPOSIT_RE, page_text),
            area_m2=area,
            rooms=rooms,
            build_year=safe_int(_search_group(_BUILD_YEAR_RE, page_text)),
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_merge_images(_images(ad.get("image")), summary.images),
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        href = _card_href(card)
        if href is None:
            raise ParseError("Rentumo listing card has no detail link")

        external_id = _text(card.get("data-listing-id"))
        if external_id is None:
            external_id = href.rstrip("/").rsplit("-", 1)[-1]

        paragraphs = [_clean_text(item) for item in card.cssselect("p")]
        paragraphs = [item for item in paragraphs if item]
        city = paragraphs[0] if paragraphs else None
        snippet = paragraphs[1] if len(paragraphs) > 1 else None

        facts = [_clean_text(item) for item in card.cssselect("ul li")]
        facts = [item for item in facts if item]
        rooms = parse_rooms(next((fact for fact in facts if "pok" in normalize_text(fact)), None))
        area = parse_area(next((fact for fact in facts if "m²" in fact or "m2" in fact), None))
        property_fact = next(
            (fact for fact in facts if normalize_text(fact) in _PROPERTY_TYPES),
            None,
        )
        property_type = _PROPERTY_TYPES.get(
            normalize_text(property_fact or ""),
            query.property_type,
        )

        price_text = _clean_text(card.cssselect("strong")[0]) if card.cssselect("strong") else None
        price = parse_price(price_text)
        price_kind = "call" if price is None else "asking"

        match = self.regions.resolve(city=city)
        voivodeship = match.voivodeship if match else None

        title = _card_title(card)
        extra = {
            "rentumo": {
                "snippet": snippet,
                "facts": facts,
                "search_key": query.key,
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urljoin(BASE_URL, href),
            title=title,
            description=snippet,
            offer_type="rent",
            property_type=property_type,
            market="secondary",
            price_kind=price_kind,
            price_unit="monthly",
            price_pln=price,
            area_m2=area,
            rooms=rooms,
            voivodeship=voivodeship,
            city=city,
            images=_card_images(card),
            extra=extra,
        )


def _document(html: str) -> Any:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Rentumo page is not valid HTML: {exc}") from exc


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_text(element: Any) -> str:
    return " ".join(element.text_content().split())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_ld(html: str) -> list[dict[str, Any]]:
    tree = _document(html)
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


def _listing_json_ld(html: str) -> dict[str, Any] | None:
    for item in _json_ld(html):
        if item.get("@type") in {
            "Apartment",
            "House",
            "RealEstateListing",
            "Product",
        } and (item.get("description") or item.get("name")):
            return item
    return None


def _card_href(card: Any) -> str | None:
    for selector in ("a[data-testid='listing-card-photo']", "a[href^='/ogloszenia/']"):
        links = card.cssselect(selector)
        if links:
            href = _text(links[0].get("href"))
            if href:
                return href
    return None


def _card_title(card: Any) -> str | None:
    for image in card.cssselect("img[alt]"):
        alt = _text(image.get("alt"))
        if not alt:
            continue
        return alt.split(" \u2013 zdjęcie")[0].strip() or None
    return None


def _card_images(card: Any) -> list[str]:
    images: list[str] = []
    for image in card.cssselect("img"):
        url = _text(image.get("data-src")) or _text(image.get("src"))
        if not url or not url.startswith("https://"):
            continue
        if "get_map" in url or "img.rentumo.com" not in url:
            continue
        if url not in images:
            images.append(url)
        if len(images) >= 20:
            break
    return images


def _images(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = []

    images: list[str] = []
    for item in raw:
        url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
        if url and url.startswith("https://") and url not in images:
            images.append(url)
        if len(images) >= 20:
            break
    return images


def _merge_images(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for url in (*primary, *secondary):
        if url not in merged:
            merged.append(url)
        if len(merged) >= 20:
            break
    return merged


def _breadcrumbs(tree: Any) -> list[str]:
    items: list[str] = []
    for element in tree.cssselect("nav[aria-label='Breadcrumb'] li"):
        text = _clean_text(element)
        if text:
            items.append(text)
    return items


def _district_from_breadcrumbs(tree: Any) -> str | None:
    for link in tree.cssselect("nav[aria-label='Breadcrumb'] a"):
        href = _text(link.get("href")) or ""
        marker = "/wynajmij-mieszkanie/"
        if marker not in href:
            continue
        path = href.split(marker, 1)[1].strip("/")
        if not path or "/" in path:
            continue
        text = _clean_text(link)
        if text and normalize_text(text) not in {"mieszkania", "wynajem"}:
            return text
    return None


def _heading(tree: Any) -> str | None:
    headings = tree.cssselect("h1")
    if not headings:
        return None
    return _clean_text(headings[0]) or None


def _map_value(tree: Any, name: str) -> str | None:
    elements = tree.cssselect(f"[data-listing-map-{name}-value]")
    if not elements:
        return None
    return _text(elements[0].get(f"data-listing-map-{name}-value"))


def _search_price(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    return parse_price(match.group(1))


def _search_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    if not value:
        return fallback
    return _PROPERTY_TYPES.get(normalize_text(str(value)), fallback)
