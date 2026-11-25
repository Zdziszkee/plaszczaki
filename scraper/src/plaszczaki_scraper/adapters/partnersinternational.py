"""Partners International adapter (WordPress listing search + detail pages).

Search results are server-rendered cards filtered with the site's own text query
(``?q=malopolskie``); detail pages add the full description and, when the agency
publishes a precise address, the Google Maps centre with coordinates.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PriceKind, PropertyType
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

BASE_URL = "https://partnersinternational.pl"
SEARCH_URL = f"{BASE_URL}/nieruchomosci"
PAGE_LINK_RE = re.compile(r"/nieruchomosci/strona/(\d+)")
TOTAL_RE = re.compile(r"(\d+)\s*ofert")
STREET_PREFIXES = ("ul. ", "al. ", "pl. ", "os. ")

TRANSACTIONS: tuple[tuple[str, OfferType], ...] = (
    ("sprzedaz", "sale"),
    ("wynajem", "rent"),
)

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartament": "flat",
    "mieszkanie": "flat",
    "dom": "house",
    "willa": "house",
    "rezydencja": "house",
    "dzialka": "plot",
    "grunt": "plot",
    "biuro": "commercial",
    "lokal": "commercial",
    "lokal komercyjny": "commercial",
    "komercja": "commercial",
    "magazyn": "commercial",
    "hala": "commercial",
    "garaz": "garage",
    "pokoj": "room",
}

# All Polish voivodeships, normalised, so an out-of-scope region is never
# mistaken for a city name.
_VOIVODESHIPS = frozenset(
    {
        "dolnoslaskie",
        "kujawsko pomorskie",
        "lubelskie",
        "lubuskie",
        "lodzkie",
        "malopolskie",
        "mazowieckie",
        "opolskie",
        "podkarpackie",
        "podlaskie",
        "pomorskie",
        "slaskie",
        "swietokrzyskie",
        "warminsko mazurskie",
        "wielkopolskie",
        "zachodniopomorskie",
    },
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Partners International page is not valid HTML: {exc}") from exc


def _join_text(nodes: list[Any]) -> str | None:
    text = " ".join(" ".join(node.text_content().split()) for node in nodes)
    return text.strip() or None


def _json_ld(html: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for node in _tree(html).xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(node.text or "")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.extend(item for item in payload.get("@graph") or [] if isinstance(item, dict))
            payloads.append(payload)
    return payloads


def _street(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    lowered = text.lower()
    for prefix in STREET_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip() or None
    return text


def _property_type(value: Any, fallback: PropertyType) -> PropertyType:
    text = normalize_text(str(value or ""))
    if not text:
        return fallback
    for key, property_type in _PROPERTY_TYPES.items():
        if text == normalize_text(key):
            return property_type
    for key, property_type in _PROPERTY_TYPES.items():
        if normalize_text(key) in text:
            return property_type
    return fallback


def _property_type_from_url(url: str, fallback: PropertyType) -> PropertyType:
    match = re.search(r"/nieruchomosc/([^/]+)/", url)
    if match is None:
        return fallback
    segment = match.group(1)
    mapping: dict[str, PropertyType] = {
        "apartament": "flat",
        "dom": "house",
        "dzialka": "plot",
        "komercja": "commercial",
        "garaz": "garage",
        "pokoj": "room",
    }
    return mapping.get(normalize_text(segment), fallback)


def _offer_type(value: Any, fallback: OfferType) -> OfferType:
    text = normalize_text(str(value or ""))
    if "wynajem" in text or "wynajmu" in text or "najem" in text:
        return "rent"
    if "sprzedaz" in text or "sprzedazy" in text:
        return "sale"
    return fallback


def _external_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _price_kind(value: str | None) -> PriceKind:
    if not value:
        return "call"
    lowered = normalize_text(value)
    return "call" if "zapytaj" in lowered or "na zapytanie" in lowered else "asking"


class PartnersInternationalAdapter:
    """Parse Partners International property cards and listing pages."""

    id = "partnersinternational"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for transaction, offer_type in TRANSACTIONS:
                queries.append(
                    Query(
                        key=f"{region}:{transaction}",
                        url=f"{SEARCH_URL}?q={region}&transaction={transaction}",
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return query
        base, _, tail = query.url.partition("?")
        paged = f"{base}/strona/{page}"
        return replace(query, url=f"{paged}?{tail}" if tail else paged, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        cards = tree.xpath('//div[contains(@class, "js-wishlistable-property")]')
        if not cards and not tree.xpath('//div[contains(@class, "list-items")]'):
            raise ParseError("Partners International page has no listing cards")

        items = [self._summary(card, query) for card in cards]
        pages = [int(value) for value in PAGE_LINK_RE.findall(html)]
        total_match = TOTAL_RE.search(html)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=max(pages) if pages else None,
            total_items=int(total_match.group(1)) if total_match else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        location = _join_text(tree.xpath('(//h1[contains(@class, "listing-subtitle")])[1]'))
        title = _join_text(tree.xpath('(//h2[contains(@class, "h5")])[1]'))

        price_text = _join_text(
            tree.xpath(
                '//div[contains(@class, "listing-price")]//p[contains(@class, "price-headline")]',
            ),
        )
        price_per_m2 = _join_text(
            tree.xpath(
                '//div[contains(@class, "listing-price")]//p[contains(@class, "price-subline")]',
            ),
        )

        metrics: dict[str, str] = {}
        offer_label: str | None = None
        property_label: str | None = None
        for item in tree.xpath('//ul[li/span[contains(@class, "list-label")]]/li'):
            headline = _join_text(
                item.xpath('.//span[contains(@class, "list-headline")]'),
            )
            value = _join_text(item.xpath(".//strong"))
            if not headline or not value:
                continue
            if normalize_text(headline) in _VOIVODESHIPS:
                continue
            if _offer_type(value, "other") in {"sale", "rent"}:
                offer_label = value
                property_label = headline
            else:
                metrics[normalize_text(headline)] = value

        description_node = tree.xpath(
            '//div[contains(@class, "generic-content")]'
            '[.//h1[contains(@class, "listing-subtitle")]]',
        )
        description = _description_text(description_node[0]) if description_node else None

        lat, lng = self._coordinates(tree)
        if lat is None or lng is None:
            lat, lng = summary.lat, summary.lng

        city, district, street, province = self._location(location)
        url = summary.url
        property_type = _property_type_from_url(
            url,
            _property_type(property_label, summary.property_type),
        )
        offer_type = _offer_type(offer_label, summary.offer_type)

        images = [
            _text(value) for value in tree.xpath('//div[contains(@class, "gallery-item")]//a/@href')
        ]
        images = [url for url in images if url] or summary.images

        published_at = summary.published_at
        modified_at = summary.modified_at
        for payload in _json_ld(html):
            if payload.get("@type") == "WebPage":
                published_at = published_at or parse_datetime_ms(payload.get("datePublished"))
                modified_at = parse_datetime_ms(payload.get("dateModified")) or modified_at

        extra = dict(summary.extra)
        source_extra = dict(extra.get("partnersinternational") or {})
        source_extra.update(
            {
                "metrics": metrics,
                "property_label": property_label,
                "offer_label": offer_label,
            },
        )
        extra["partnersinternational"] = source_extra

        return replace(
            summary,
            url=url,
            external_id=summary.external_id or _external_id(url),
            title=title or summary.title,
            description=description or summary.description,
            offer_type=offer_type,
            property_type=property_type,
            market=summary.market,
            price_kind=_price_kind(price_text),
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=parse_price(price_text) or summary.price_pln,
            price_per_m2_pln=parse_price(price_per_m2) or summary.price_per_m2_pln,
            area_m2=parse_area(metrics.get("powierzchnia")) or summary.area_m2,
            rooms=parse_rooms(_first(metrics.get("pokoje"), metrics.get("pomieszczenia")))
            or summary.rooms,
            floor=parse_floor(metrics.get("pietro"))[0] or summary.floor,
            floor_label=parse_floor(metrics.get("pietro"))[1] or summary.floor_label,
            build_year=safe_int(metrics.get("rok budowy")) or summary.build_year,
            voivodeship=province or summary.voivodeship,
            city=city or summary.city,
            district=district or summary.district,
            street=street or summary.street,
            address_text=_address_text(street, city, district) or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=images[:20],
            seller_type=summary.seller_type,
            published_at=published_at,
            modified_at=modified_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        hrefs = card.xpath(".//a/@href")
        url = next(
            (href for href in hrefs if "/nieruchomosc/" in href and not href.startswith("#")),
            None,
        )
        if url is None:
            raise ParseError("Partners International card has no listing URL")
        url = url.split("?", 1)[0]

        title = _join_text(card.xpath('.//h3[contains(@class, "card-headline")]'))
        location = _join_text(card.xpath('.//p[contains(@class, "card-subline")]'))
        price_text = _join_text(card.xpath('.//div[contains(@class, "card-price")]//p'))
        description = _join_text(card.xpath('.//div[contains(@class, "generic-content")]'))

        metrics: dict[str, str] = {}
        property_label: str | None = None
        for item in card.xpath('.//ul[contains(@class, "icons-list")]/li'):
            label = _text(item.get("title"))
            value = _join_text(item.xpath(".//strong"))
            if not label or not value:
                continue
            if normalize_text(label) == "typ":
                property_label = value
            else:
                metrics[normalize_text(label)] = value

        images: list[str] = []
        for value in card.xpath(".//img/@data-src | .//img/@src"):
            text = _text(value)
            if text and not text.startswith("data:") and text not in images:
                images.append(text)

        external_id = _text(card.get("data-property-id")) or _external_id(url)
        city, district, street, province = self._location(location)
        property_type = _property_type_from_url(
            url, _property_type(property_label, query.property_type)
        )

        extra = {
            "partnersinternational": {
                "metrics": metrics,
                "property_label": property_label,
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            description=description,
            offer_type=query.offer_type,
            property_type=property_type,
            market="unknown",
            price_kind=_price_kind(price_text),
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=parse_price(price_text),
            price_per_m2_pln=parse_price(metrics.get("cena za m2")),
            area_m2=parse_area(metrics.get("powierzchnia")),
            rooms=parse_rooms(_first(metrics.get("pokoje"), metrics.get("pomieszczenia"))),
            floor=parse_floor(metrics.get("pietro"))[0],
            floor_label=parse_floor(metrics.get("pietro"))[1],
            build_year=safe_int(metrics.get("rok budowy")),
            voivodeship=province,
            city=city,
            district=district,
            street=street,
            address_text=_address_text(street, city, district),
            images=images[:20],
            seller_type="agency",
            extra=extra,
        )

    def _location(self, value: str | None) -> tuple[str | None, str | None, str | None, str | None]:
        if not value:
            return None, None, None, None
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            return None, None, None, None

        province: str | None = None
        if normalize_text(parts[0]) in _VOIVODESHIPS:
            province = self.regions.canonical_voivodeship(parts[0])
            parts = parts[1:]

        city = parts[0] if parts else None
        district: str | None = None
        street: str | None = None
        if len(parts) == 2:
            street = _street(parts[1])
        elif len(parts) >= 3:
            district = parts[1]
            street = _street(parts[2])
        return city, district, street, province

    @staticmethod
    def _coordinates(tree: lxml_html.HtmlElement) -> tuple[float | None, float | None]:
        for raw in tree.xpath("//div[@data-google-map]/@data-google-map"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError, TypeError:
                continue
            if not isinstance(payload, dict):
                continue
            centre = payload.get("center")
            if isinstance(centre, dict):
                lat = safe_float(centre.get("lat"))
                lng = safe_float(centre.get("lng"))
                if lat is not None and lng is not None:
                    return lat, lng
            markers = payload.get("markers")
            if isinstance(markers, list):
                for marker in markers:
                    position = marker.get("position") if isinstance(marker, dict) else None
                    if isinstance(position, dict):
                        lat = safe_float(position.get("lat"))
                        lng = safe_float(position.get("lng"))
                        if lat is not None and lng is not None:
                            return lat, lng
        return None, None


def _description_text(node: Any) -> str | None:
    clone = copy.deepcopy(node)
    for heading in clone.xpath(".//h1 | .//h2"):
        parent = heading.getparent()
        if parent is not None:
            parent.remove(heading)
    text = " ".join(" ".join(clone.itertext()).split())
    return text or None


def _address_text(street: str | None, city: str | None, district: str | None) -> str | None:
    parts = [street, district, city]
    text = ", ".join(part for part in parts if part)
    return text or None
