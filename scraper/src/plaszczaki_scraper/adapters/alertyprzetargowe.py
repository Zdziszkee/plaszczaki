"""Alerty Przetargowe (Otoprzetargi) adapter.

``alertyprzetargowe.pl`` serves the Otoprzetargi announcement register: property
sales, rentals, leases and takeovers published by municipalities, state
agencies and insolvency trustees. Search results are plain server-rendered
HTML; each detail page exposes the announcement body, gallery images, the
source-provided location and the coordinates used by its "Pokaż na mapie"
Google Maps widget.

The site only filters by a point plus a radius, so the adapter fans out over a
small grid of centres derived from each region's bounding box and lets the
per-card location (``miejscowość, województwo``) and the detail page decide
whether a listing is in scope.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import normalize_text, safe_float
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://alertyprzetargowe.pl"
SEARCH_PATH = "/nieruchomosci"
SEARCH_RADIUS_KM = 75
# Fractions of each region's bounding box used as search centres. A 2x2 grid
# with a 75 km radius covers both małopolskie and śląskie completely while
# keeping the number of search URLs small.
GRID_FRACTIONS = (0.25, 0.75)

_RESULT_COUNT_RE = re.compile(r"Znaleziono\s*<strong>\s*([\d\s]+?)\s*</strong>", re.IGNORECASE)
_PAGE_LINK_RE = re.compile(r"[?&]page=(\d+)")
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_LAT_RE = re.compile(r"var\s+defaultLat\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_LNG_RE = re.compile(r"var\s+defaultLng\s*=\s*(-?\d+(?:\.\d+)?)\s*;")
_EDITION_RE = re.compile(
    r"Wydanie nr\s*(?P<edition>[^\s]+)\s*z dnia\s*(?P<day>\d{1,2})\s+"
    r"(?P<month>[a-ząćęłńóśźż]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_AUCTION_DATE_RE = re.compile(
    r"(?:przetarg\w*|licytacj\w*|aukcj\w*)[^.]{0,160}?"
    r"(?:w dniu|dnia|odbędzie się)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})",
    re.IGNORECASE,
)

_POLISH_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "wrzesnia": 9,
    "pazdziernika": 10,
    "listopada": 11,
    "grudnia": 12,
}

_TRANSACTION_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "kupno": "other",
    "wynajem": "rent",
    "najem": "rent",
    "dzierzawa": "lease",
    "inne": "other",
}

_SUBJECT_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "lokal mieszkalny": "flat",
    "dom": "house",
    "dworek palac": "house",
    "dzialka": "plot",
    "garaz": "garage",
    "biuro obiekt biurowy": "commercial",
    "biuro": "commercial",
    "lokal uzytkowy": "commercial",
    "obiekt uzytkowy": "commercial",
    "magazyn": "commercial",
    "hotel pensjonat": "commercial",
}

_DETAIL_LABELS = {
    "Cena:": "price",
    "Powierzchnia:": "area",
    "Typ transakcji:": "transaction",
    "Przedmiot ogłoszenia:": "subject",
    "Miejscowość ogłoszenia:": "location",
}


def _text(node: Any) -> str | None:
    if node is None:
        return None
    text = " ".join(node.text_content().split())
    return text or None


def _first_text(nodes: Sequence[Any]) -> str | None:
    for node in nodes:
        text = _text(node)
        if text:
            return text
    return None


def _parse_amount(value: str | None) -> int | None:
    """Parse Polish amounts such as ``819.000,00`` or ``1 234,56``."""
    if not value:
        return None
    text = re.sub(r"[^\d.,]", "", value)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        if head and len(tail) in {1, 2}:
            text = f"{head.replace('.', '')}.{tail}"
        else:
            text = text.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text) or text.count(".") > 1:
        text = text.replace(".", "")
    try:
        amount = round(float(text))
    except ValueError:
        return None
    return amount if amount > 0 else None


def _parse_measure(value: str | None) -> float | None:
    """Parse an area in square metres, tolerating both decimal separators."""
    if not value:
        return None
    text = re.sub(r"(?i)\s*m(?:2|²)\s*$", "", value.strip())
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        amount = float(text)
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def _parse_date(value: str | None) -> int | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        parsed = datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _parse_polish_date(day: str, month: str, year: str) -> int | None:
    month_number = _POLISH_MONTHS.get(normalize_text(month))
    if month_number is None:
        return None
    try:
        parsed = datetime(int(year), month_number, int(day), tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


class AlertyPrzetargoweAdapter:
    """Parse the Otoprzetargi announcement register."""

    id = "alertyprzetargowe"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region_id in regions:
            region = self.regions.region(region_id)
            min_lon, min_lat, max_lon, max_lat = region.bbox
            centres = [
                (lat_fraction, lon_fraction)
                for lat_fraction in GRID_FRACTIONS
                for lon_fraction in GRID_FRACTIONS
            ]
            for index, (lat_fraction, lon_fraction) in enumerate(centres):
                lat = round(min_lat + (max_lat - min_lat) * lat_fraction, 4)
                lng = round(min_lon + (max_lon - min_lon) * lon_fraction, 4)
                queries.append(
                    Query(
                        key=f"{region_id}:grid:{index}",
                        url=(
                            f"{BASE_URL}{SEARCH_PATH}"
                            f"?latitude={lat}&longitude={lng}&radius={SEARCH_RADIUS_KM}"
                        ),
                        region=region_id,
                        offer_type="other",
                        property_type="other",
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """The portal's default list is already newest-first."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        anchors = [anchor for anchor in tree.iter("a") if self._is_listing_href(anchor.get("href"))]

        if not anchors and "Znaleziono" not in html:
            raise ParseError("expected the Otoprzetargi result list")

        items: list[Listing] = []
        seen: set[str] = set()
        for anchor in anchors:
            url = urljoin(BASE_URL, anchor.get("href") or "")
            if url in seen:
                continue
            seen.add(url)
            item = self._summary(anchor, url, query)
            if item is not None:
                items.append(item)

        match = _RESULT_COUNT_RE.search(html)
        total_items = int(re.sub(r"\s+", "", match.group(1))) if match else None
        pages = [int(value) for value in _PAGE_LINK_RE.findall(html)]
        if pages:
            total_pages = max(pages)
        elif total_items is not None and items:
            total_pages = max(1, math.ceil(total_items / len(items)))
        else:
            total_pages = 1

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        fields = self._detail_fields(tree)
        title = self._detail_title(tree)

        transaction = fields.get("transaction")
        subject = fields.get("subject")
        location = self._detail_location(fields.get("location"))

        description = self._detail_description(tree)
        normalized_description = normalize_text(description or "")
        price_kind = "starting" if "cena wywolawcza" in normalized_description else "asking"

        lat_match = _LAT_RE.search(html)
        lng_match = _LNG_RE.search(html)
        lat = safe_float(lat_match.group(1)) if lat_match else None
        lng = safe_float(lng_match.group(1)) if lng_match else None

        edition = (summary.extra.get("otoprzetargi") or {}).get("edition")
        published_at = summary.published_at
        edition_text = _first_text(
            tree.xpath(
                "//h1[contains(@class, 'text-shadow')]/following-sibling::p"
                "[contains(., 'Wydanie nr')]",
            ),
        )
        edition_match = _EDITION_RE.search(edition_text or "")
        if edition_match:
            edition = edition_match.group("edition")
            published_at = (
                _parse_polish_date(
                    edition_match.group("day"),
                    edition_match.group("month"),
                    edition_match.group("year"),
                )
                or published_at
            )

        auction_at = None
        auction_match = _AUCTION_DATE_RE.search(description or "")
        if auction_match:
            auction_at = _parse_date(".".join(auction_match.groups()))

        extra = dict(summary.extra)
        otoprzetargi_extra = dict(extra.get("otoprzetargi") or {})
        otoprzetargi_extra.update(
            {
                "edition": edition,
                "transaction": transaction,
                "subject": subject,
            },
        )
        extra["otoprzetargi"] = otoprzetargi_extra

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            offer_type=self._offer_type(transaction, summary.offer_type),
            property_type=self._property_type(subject, summary.property_type),
            price_kind=price_kind,
            price_pln=_parse_amount(fields.get("price")) or summary.price_pln,
            area_m2=_parse_measure(fields.get("area")) or summary.area_m2,
            city=location.get("city") or summary.city,
            voivodeship=self._detail_region(location.get("voivodeship"), summary.voivodeship),
            lat=lat if lat is not None else summary.lat,
            lng=lng if lng is not None else summary.lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            images=self._images(tree) or summary.images,
            seller_type=self._seller_type(normalized_description),
            published_at=published_at,
            auction_date=auction_at,
            extra=extra,
        )

    @staticmethod
    def _is_listing_href(href: str | None) -> bool:
        if not href:
            return False
        path = href.removeprefix(BASE_URL)
        return path.startswith(f"{SEARCH_PATH}/") and len(path) > len(SEARCH_PATH) + 1

    def _summary(self, anchor: Any, url: str, query: Query) -> Listing | None:
        cards = anchor.xpath(".//div[contains(@class, 'card-body')]")
        if not cards:
            return None
        card = cards[0]

        title = _first_text(card.xpath(".//h2"))
        if title is None:
            return None

        labels: dict[str, str] = {}
        location_text: str | None = None
        for paragraph in card.iter("p"):
            text = _text(paragraph)
            if not text:
                continue
            key, separator, value = text.partition(":")
            normalized_key = normalize_text(key)
            if separator and normalized_key in {"powierzchnia", "cena", "data"}:
                labels[normalized_key] = value.strip()
            else:
                location_text = text

        transaction: str | None = None
        subject: str | None = None
        for badge in card.xpath(".//div[contains(@class, 'badge')]"):
            text = _text(badge)
            if not text:
                continue
            normalized = normalize_text(text)
            if normalized in _TRANSACTION_TYPES:
                transaction = text
            elif normalized in _SUBJECT_TYPES:
                subject = text

        city: str | None = None
        voivodeship: str | None = None
        if location_text:
            head, _, province = location_text.rpartition(",")
            city = (head or location_text).strip() or None
            voivodeship = province.strip() or None

        return Listing(
            source_id=self.source_id,
            external_id=url.rstrip("/").rsplit("/", 1)[-1],
            url=url,
            title=title,
            offer_type=self._offer_type(transaction, query.offer_type),
            property_type=self._property_type(subject, query.property_type),
            price_kind="asking",
            price_unit="total",
            price_pln=_parse_amount(labels.get("cena")),
            area_m2=_parse_measure(labels.get("powierzchnia")),
            voivodeship=self.regions.canonical_voivodeship(voivodeship),
            city=city,
            published_at=_parse_date(labels.get("data")),
            images=self._card_images(anchor),
            extra={
                "otoprzetargi": {
                    "transaction": transaction,
                    "subject": subject,
                },
            },
        )

    @staticmethod
    def _card_images(card: Any) -> list[str]:
        images: list[str] = []
        for node in card.xpath(".//*[contains(@class, 'bg-cover')]"):
            match = re.search(
                r"url\(['\"]?(?P<url>https://[^'\")]+)",
                node.get("style") or "",
            )
            if match and match.group("url") not in images:
                images.append(match.group("url"))
        return images[:5]

    def _detail_region(self, province: str | None, fallback: str | None) -> str | None:
        """Prefer the detail page's voivodeship; never leak an out-of-scope one."""
        if province:
            return self.regions.canonical_voivodeship(province)
        return fallback

    @staticmethod
    def _detail_title(tree: Any) -> str | None:
        for xpath in (
            "//h1[contains(@class, 'text-shadow')]",
            "//h1[contains(@class, 'verified')]",
            "(//h1)[last()]",
        ):
            title = _first_text(tree.xpath(xpath))
            if title and normalize_text(title) != "otoprzetargi":
                return title
        return None

    @staticmethod
    def _detail_fields(tree: Any) -> dict[str, str]:
        fields: dict[str, str] = {}
        for strong in tree.iter("strong"):
            label = _text(strong)
            key = _DETAIL_LABELS.get(label or "")
            if key is None or key in fields:
                continue
            parent = strong.getparent()
            value = _text(parent)
            if value is None:
                continue
            fields[key] = value.removeprefix(label or "").strip()
        return fields

    @staticmethod
    def _detail_location(value: str | None) -> dict[str, str]:
        if not value:
            return {}
        province = ""
        match = re.search(r"Województwo:\s*(?P<province>[^|]+)$", value, re.IGNORECASE)
        if match:
            province = match.group("province").strip()
            value = value[: match.start()].strip()
        city = value.split(",", 1)[0].strip()
        return {"city": city, "voivodeship": province}

    @staticmethod
    def _detail_description(tree: Any) -> str | None:
        blocks = tree.xpath(
            "//div[contains(@class, 'text-block')][h3[normalize-space(.) = 'Szczegóły']]",
        )
        if not blocks:
            return None
        block = blocks[0]
        for heading in block.xpath(".//h3"):
            parent = heading.getparent()
            if parent is not None:
                parent.remove(heading)
        return _text(block)

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        for value in tree.xpath(
            "//a[contains(@href, '/storage/')]/@href | //img[contains(@src, '/storage/')]/@src",
        ):
            if value not in images:
                images.append(value)
        return images[:20]

    @staticmethod
    def _offer_type(value: str | None, fallback: OfferType) -> OfferType:
        if not value:
            return fallback
        return _TRANSACTION_TYPES.get(normalize_text(value), fallback)

    @staticmethod
    def _property_type(value: str | None, fallback: PropertyType) -> PropertyType:
        if not value:
            return fallback
        return _SUBJECT_TYPES.get(normalize_text(value), fallback)

    @staticmethod
    def _seller_type(description: str) -> str | None:
        if "syndyk" in description:
            return "insolvency"
        for marker in ("gmina", "miasto", "gminy", "miasta", "skarb panstwa", "sad rejonowy"):
            if marker in description:
                return "government"
        return None
