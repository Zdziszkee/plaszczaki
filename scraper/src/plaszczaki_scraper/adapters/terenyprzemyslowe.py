"""Tereny Przemysłowe adapter (search pages + detail pages).

The portal renders its offer list server-side and exposes a GeoJSON polygon on
every detail page, so coordinates come straight from the source.  Only the
public HTML is used; the JSON API under ``/api/`` is disallowed by robots.txt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode, urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://terenyprzemyslowe.pl/"

# Portal UI names for the two voivodeships in scope.
REGION_NAMES: dict[str, str] = {
    "malopolskie": "małopolskie",
    "slaskie": "śląskie",
}

# Search form values for "Rodzaj nieruchomości" and their canonical type.
CATEGORIES: tuple[tuple[str, str, PropertyType], ...] = (
    ("5", "grunt", "plot"),
    ("6", "hala / magazyn", "commercial"),
    ("8", "BTS", "commercial"),
    ("9", "biuro", "commercial"),
)

_OFFER_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "sprzedaż": "sale",
    "wynajem": "rent",
    "najem": "rent",
    "dzierzawa": "lease",
    "dzierżawa": "lease",
    "licytacja": "auction",
    "przetarg": "auction",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "grunt": "plot",
    "dzialka": "plot",
    "działka": "plot",
    "hala magazyn": "commercial",
    "hala / magazyn": "commercial",
    "hala produkcyjna": "commercial",
    "magazyn": "commercial",
    "biuro": "commercial",
    "bts": "commercial",
    "lokal": "commercial",
}

_SLUG_RE = re.compile(r"/+([^/]+)/*$")


def _text(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(str(node.text_content()).split())


def _attr(node: Any, name: str) -> str | None:
    if node is None:
        return None
    value = node.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(nodes: list[Any]) -> Any:
    return nodes[0] if nodes else None


def _decimal(value: Any) -> float | None:
    """Parse a decimal such as ``60,16`` without mangling thousands."""
    if value is None:
        return None
    match = re.search(r"\d[\d\s\u00a0]*(?:[.,]\d+)?", str(value))
    if match is None:
        return None
    number = match.group(0).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    return safe_float(number)


def _canonical_offer_type(value: Any, fallback: OfferType) -> OfferType:
    text = normalize_text(str(value)) if value else ""
    for key, offer_type in _OFFER_TYPES.items():
        if text == normalize_text(key) or normalize_text(key) in text:
            return offer_type
    return fallback


def _canonical_property_type(value: Any, fallback: PropertyType) -> PropertyType:
    text = normalize_text(str(value)) if value else ""
    if not text:
        return fallback
    if "grunt" in text or "dzialka" in text:
        return "plot"
    for key, property_type in _PROPERTY_TYPES.items():
        if normalize_text(key) == text:
            return property_type
    return fallback


def _county(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    lowered = normalize_text(text)
    for prefix in ("powiat ", "pow. "):
        if lowered.startswith(normalize_text(prefix)):
            text = text[len(prefix) :]
            break
    return text.strip(" ,") or None


def _join_text(node: Any) -> str | None:
    """Return readable text for an element, keeping sibling blocks apart."""
    if node is None:
        return None
    parts = [part.strip() for part in node.itertext() if part and part.strip()]
    return strip_html(" ".join(parts))


def _parse_geojson(value: str | None) -> tuple[float | None, float | None]:
    """Return the centroid of a GeoJSON point/polygon from ``data-geojson``."""
    if not value:
        return None, None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return None, None
    geometry = features[0].get("geometry") if isinstance(features[0], dict) else None
    if not isinstance(geometry, dict):
        return None, None

    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    points: list[tuple[float, float]] = []

    def collect(node: Any) -> None:
        if (
            isinstance(node, list)
            and len(node) == 2
            and all(isinstance(item, int | float) for item in node)
        ):
            points.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, list):
            for item in node:
                collect(item)

    if kind in {"Polygon", "MultiPolygon", "LineString", "Point"}:
        collect(coordinates)
    if not points:
        return None, None

    lng = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    # Guard against swapped axes and stray placeholders.
    if not (48.5 <= lat <= 55.5 and 13.5 <= lng <= 24.5):
        return None, None
    return round(lat, 6), round(lng, 6)


def _parse_date(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value.strip())
    if match is None:
        return parse_datetime_ms(value)
    day, month, year = match.groups()
    return parse_datetime_ms(f"{year}-{month}-{day}")


def _slug(url: str) -> str:
    path = url.split("?", 1)[0].rstrip("/")
    match = _SLUG_RE.search(path)
    return match.group(1) if match else path.rsplit("/", 1)[-1]


class TerenyPrzemysloweAdapter:
    """Parse the server-rendered Tereny Przemysłowe portal."""

    id = "terenyprzemyslowe"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            name = REGION_NAMES.get(region)
            if name is None:
                continue
            for rodzaj, _label, property_type in CATEGORIES:
                url = f"{BASE_URL}?{urlencode({'wojewodztwo': name, 'rodzaj': rodzaj})}"
                queries.append(
                    Query(
                        key=f"{region}:{rodzaj}",
                        url=url,
                        region=region,
                        offer_type="sale",
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        doc = lxml_html.fromstring(html)
        title = _text(_first(doc.xpath('//h2[contains(@class, "tp-results__title")]')))
        if not title and not doc.xpath('//article[contains(@class, "tp-rcard")]'):
            raise ParseError("Tereny Przemysłowe search page has no results section")

        items = [
            self._summary(card, query)
            for card in doc.xpath('//article[contains(@class, "tp-rcard")]')
        ]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=1,
            total_items=self._total_items(title),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        doc = lxml_html.fromstring(html)
        if not doc.xpath('//*[contains(@class, "od-hero")]'):
            raise ParseError("Tereny Przemysłowe detail page has no offer hero")

        params = self._params(doc)
        title = _text(_first(doc.xpath('//h1[contains(@class, "od-hero__title")]')))
        hero_location = _text(
            _first(doc.xpath('//*[contains(@class, "od-hero__location")]')),
        )
        city, county_from_hero = self._parse_location(hero_location)

        voivodeship = self.regions.canonical_voivodeship(params.get("WOJEWÓDZTWO"))
        if voivodeship is None:
            voivodeship = self.regions.canonical_voivodeship(hero_location.rsplit(",", 1)[-1])

        property_type = _canonical_property_type(
            params.get("RODZAJ"),
            summary.property_type,
        )
        offer_type = _canonical_offer_type(
            params.get("TYP TRANSAKCJI"),
            summary.offer_type,
        )

        area = parse_area(params.get("POWIERZCHNIA"))
        land_area = area if property_type == "plot" else None
        if property_type != "plot":
            land_area = parse_area(params.get("POWIERZCHNIA DZIAŁKI"))

        total_price = parse_price(
            _first_text(
                params,
                "CENA NETTO ZA CAŁOŚĆ",
                "CENA ZA CAŁOŚĆ",
                "CENA BRUTTO ZA CAŁOŚĆ",
            ),
        )
        price_per_m2 = _decimal(
            _first_text(
                params,
                "CENA NETTO ZA M²",
                "CENA ZA M²",
                "CENA BRUTTO ZA M²",
            ),
        )
        if total_price is None:
            total_price = self._price_from_card(doc)
        if price_per_m2 is None:
            price_per_m2 = self._price_per_m2_from_card(doc)

        price_kind = "asking" if (total_price or price_per_m2) else "call"

        lat, lng = _parse_geojson(
            _attr(_first(doc.xpath("//*[@data-geojson]")), "data-geojson"),
        )

        images: list[str] = []
        for node in doc.xpath('//*[contains(@class, "od-hero__thumb")]'):
            src = _attr(node, "data-src") or _attr(node, "src")
            if src:
                absolute = urljoin(BASE_URL, src)
                if absolute not in images:
                    images.append(absolute)

        description = None
        for node in doc.xpath('//*[contains(@class, "od-description")]'):
            description = _join_text(node)
            if description:
                break

        extra = dict(summary.extra)
        portal_extra = dict(extra.get("terenyprzemyslowe") or {})
        portal_extra.update(
            {
                "params": params,
                "zone": self._zone(doc),
                "hero_location": hero_location or None,
            },
        )
        extra["terenyprzemyslowe"] = portal_extra

        return replace(
            summary,
            url=_attr(_first(doc.xpath('//link[@rel="canonical"]')), "href") or summary.url,
            title=title or summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind=price_kind,
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=total_price,
            price_per_m2_pln=round(price_per_m2) if price_per_m2 else None,
            area_m2=None if property_type == "plot" else area,
            land_area_m2=land_area,
            voivodeship=voivodeship,
            county=params.get("POWIAT") or county_from_hero,
            commune=params.get("GMINA"),
            city=city,
            address_text=hero_location or None,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20] or summary.images,
            published_at=_parse_date(params.get("DATA DODANIA")) or summary.published_at,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _summary(self, card: Any, query: Query) -> Listing:
        link = _first(card.xpath('.//a[contains(@class, "tp-rcard__link")]'))
        href = _attr(link, "href")
        if href is None:
            raise ParseError("Tereny Przemysłowe card has no offer link")
        url = urljoin(BASE_URL, href)
        external_id = _attr(card, "data-id") or _slug(url)

        title = _text(link) or None
        price_text = _text(_first(card.xpath('.//*[contains(@class, "tp-rcard__price")]')))
        ppm_text = _text(_first(card.xpath('.//*[contains(@class, "tp-rcard__ppm")]')))
        badge = _text(_first(card.xpath('.//*[contains(@class, "tp-rcard__badge")]')))
        tags = [
            " ".join(node.text_content().split())
            for node in card.xpath('.//*[contains(@class, "tp-rcard__tags")]//span')
        ]
        property_type = _canonical_property_type(
            tags[1] if len(tags) > 1 else None,
            query.property_type,
        )
        offer_type = _canonical_offer_type(badge, query.offer_type)

        total_price: int | None = None
        price_per_m2: float | None = None
        if "/m²" in price_text or "/m2" in price_text:
            price_per_m2 = _decimal(price_text)
        else:
            total_price = parse_price(price_text)
        if total_price is None and ppm_text:
            total_price = parse_price(ppm_text)
        if price_per_m2 is None and "/m²" in ppm_text:
            price_per_m2 = _decimal(ppm_text)

        area = parse_area(tags[0]) if tags else None
        location = _text(_first(card.xpath('.//*[contains(@class, "tp-rcard__loc")]')))
        city, county = self._parse_location(location)
        voivodeship = self.regions.canonical_voivodeship(
            location.rsplit(",", 1)[-1] if location else None,
        )

        image = _attr(_first(card.xpath(".//img")), "src")
        images = [urljoin(BASE_URL, image)] if image else []

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            description=None,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking" if (total_price or price_per_m2) else "call",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=total_price,
            price_per_m2_pln=round(price_per_m2) if price_per_m2 else None,
            area_m2=None if property_type == "plot" else area,
            land_area_m2=area if property_type == "plot" else None,
            voivodeship=voivodeship,
            county=county,
            city=city,
            address_text=location or None,
            images=images,
            extra={
                "terenyprzemyslowe": {
                    "rodzaj": tags[1] if len(tags) > 1 else None,
                    "badge": badge or None,
                    "card_id": _attr(card, "data-id"),
                },
            },
        )

    def _parse_location(self, value: str) -> tuple[str | None, str | None]:
        if not value:
            return None, None
        parts = [" ".join(part.split()) for part in value.split(",") if part.strip()]
        if not parts:
            return None, None
        city = parts[0]
        county = next(
            (
                stripped
                for part in parts[1:]
                if normalize_text(part).startswith(("powiat ", "pow "))
                and (stripped := _county(part)) is not None
            ),
            None,
        )
        return city or None, county

    @staticmethod
    def _total_items(title: str) -> int | None:
        if not title:
            return None
        match = re.search(r"(\d[\d\s\u00a0]*)", title)
        return safe_int(match.group(1)) if match else None

    @staticmethod
    def _params(doc: Any) -> dict[str, str]:
        params: dict[str, str] = {}
        for card in doc.xpath('//*[contains(@class, "od-param-card")]'):
            label = _text(_first(card.xpath('.//*[contains(@class, "od-param-card__label")]')))
            value = _text(_first(card.xpath('.//*[contains(@class, "od-param-card__value")]')))
            if label:
                params[label.upper()] = value
        return params

    @staticmethod
    def _price_from_card(doc: Any) -> int | None:
        for node in doc.xpath('//*[contains(@class, "od-hero__price-per-m2")]'):
            text = _text(node)
            if "za całość" in text.lower():
                price = parse_price(text)
                if price is not None:
                    return price
        for row in doc.xpath('//*[contains(@class, "od-hero__price-row")]'):
            text = _text(row)
            if "całość" in text.lower() or "całkowita" in text.lower():
                price = parse_price(text)
                if price is not None:
                    return price
        return None

    @staticmethod
    def _price_per_m2_from_card(doc: Any) -> float | None:
        value = _text(_first(doc.xpath('//*[contains(@class, "od-hero__price-value")]')))
        if "m²" in value or "m2" in value:
            return _decimal(value)
        for row in doc.xpath('//*[contains(@class, "od-hero__price-row")]'):
            text = _text(row)
            if ("m²" in text or "m2" in text) and "cena" in text.lower():
                return _decimal(text)
        return None

    @staticmethod
    def _zone(doc: Any) -> str | None:
        for item in doc.xpath('//*[contains(@class, "od-location-bar__item")]'):
            label = _text(_first(item.xpath(".//span")))
            if normalize_text(label).startswith("strefa"):
                return _text(_first(item.xpath(".//strong"))) or None
        return None


def _first_text(params: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = params.get(key)
        if value:
            return value
    return None
