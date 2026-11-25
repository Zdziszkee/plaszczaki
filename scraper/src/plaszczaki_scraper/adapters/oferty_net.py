"""Oferty.net adapter (server-rendered search tables + detail pages).

Search results are plain tables under region/transaction paths such as
``/mieszkania-sprzedaz,malopolskie?page=2``. Detail pages expose the exact map
point as ``data-lat``/``data-lng`` on ``#property-map`` and the full
description in ``#description``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

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
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://www.oferty.net"

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("dzialki", "sprzedaz", "sale", "plot"),
    ("dzialki", "wynajem", "rent", "plot"),
    ("komercyjne", "sprzedaz", "sale", "commercial"),
    ("komercyjne", "wynajem", "rent", "commercial"),
    ("garaze", "sprzedaz", "sale", "garage"),
    ("garaze", "wynajem", "rent", "garage"),
    ("pokoje", "wynajem", "rent", "room"),
)

_CATEGORY_SEGMENTS: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "mieszkania": "flat",
    "dom": "house",
    "domy": "house",
    "dzialka": "plot",
    "dzialki": "plot",
    "lokal": "commercial",
    "lokale": "commercial",
    "komercyjne": "commercial",
    "garaz": "garage",
    "garaze": "garage",
    "pokoj": "room",
    "pokoje": "room",
}

_PROPERTY_HINTS: tuple[tuple[str, PropertyType], ...] = (
    ("mieszkanie", "flat"),
    ("dom", "house"),
    ("dzialka", "plot"),
    ("lokal", "commercial"),
    ("garaz", "garage"),
    ("pokoj", "room"),
)

_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_PRICE_RE = re.compile(r"Cena:\s*([0-9\s\u00a0.,]+)\s*PLN")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_GALLERY_TOKEN_RE = re.compile(r"/thumbnail/([^/]+)/")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _unit_price(value: str | None) -> int | None:
    """Parse a ``12 909,09`` price-per-m² cell without dropping decimals."""
    if not value:
        return None
    match = re.search(r"\d[\d\s\u00a0]*(?:[.,]\d+)?", value)
    if match is None:
        return None
    amount = safe_float(match.group(0).replace(" ", "").replace("\u00a0", ""))
    return round(amount) if amount else None


def _params(tree: Any) -> dict[str, str]:
    params: dict[str, str] = {}
    for label in tree.xpath('//div[@class="param"]//dt'):
        siblings = label.xpath("following-sibling::dd[1]")
        key = " ".join(label.text_content().split()).rstrip(":").strip()
        if key and siblings:
            params[key] = _text(siblings[0].text_content()) or ""
    return params


def _offer_type(value: str | None) -> OfferType | None:
    if not value:
        return None
    normalized = normalize_text(value)
    if "sprzeda" in normalized or "sprzedaz" in normalized:
        return "sale"
    if "wynaj" in normalized or "najem" in normalized or "wynajec" in normalized:
        return "rent"
    if "dzierzaw" in normalized:
        return "lease"
    return None


def _offer_type_from_url(url: str) -> OfferType | None:
    path = urlsplit(url).path.lower()
    if "-na-sprzedaz" in path or "-sprzedaz" in path:
        return "sale"
    if "-na-wynajem" in path or "-wynajem" in path or "-do-wynajecia" in path:
        return "rent"
    return None


def _property_type_from_url(url: str) -> PropertyType | None:
    path = urlsplit(url).path.strip("/")
    first = path.split("/", 1)[0].split("-", 1)[0]
    return _CATEGORY_SEGMENTS.get(first)


def _property_type_from_hint(value: str | None) -> PropertyType | None:
    if not value:
        return None
    normalized = normalize_text(value)
    for hint, property_type in _PROPERTY_HINTS:
        if hint in normalized:
            return property_type
    return None


def _market(params: dict[str, str]) -> Market:
    value = normalize_text(params.get("Rynek pierwotny", ""))
    if value.startswith("tak"):
        return "primary"
    if value.startswith("nie"):
        return "secondary"
    return "unknown"


class OfertyNetAdapter:
    """Parse Oferty.net search tables and detail pages."""

    id = "oferty-net"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for category, transaction, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{category}:{transaction}",
                        url=f"{BASE_URL}/{category}-{transaction},{region}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        rows = tree.xpath(
            '//tr[contains(concat(" ", normalize-space(@class), " "), " property ")]',
        )

        items: list[Listing] = []
        for row in rows:
            summary = self._summary(row, query)
            if summary is not None:
                items.append(summary)

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=self._total_pages(tree),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        map_nodes = tree.xpath('//*[@id="property-map"]')
        if not map_nodes:
            raise ParseError("Oferty.net detail page is missing the location map")
        lat = safe_float(map_nodes[0].get("data-lat"))
        lng = safe_float(map_nodes[0].get("data-lng"))
        if lat is None or lng is None:
            raise ParseError("Oferty.net detail page is missing coordinates")

        params = _params(tree)
        header = _text(
            " ".join(
                part
                for node in tree.xpath('//div[@class="header"]/span')
                for part in [node.text_content()]
            ),
        )
        heading = _text(" ".join(tree.xpath("//h1//text()")))

        offer_type = (
            _offer_type(header)
            or _offer_type(_text(" ".join(tree.xpath("//title/text()"))))
            or summary.offer_type
        )
        property_type = self._detail_property_type(tree, summary)

        price_match = _PRICE_RE.search(_text(" ".join(tree.xpath("//h3//text()"))) or "")
        price = parse_price(price_match.group(1)) if price_match else None
        price_per_m2 = _unit_price(params.get("Cena za m²"))
        area = parse_area(params.get("Powierzchnia użytkowa"))
        if area is None:
            area = parse_area(params.get("Powierzchnia mieszkalna"))
        land_area = parse_area(params.get("Powierzchnia działki"))
        if property_type == "plot" and land_area is not None:
            area = None

        province = params.get("Województwo") or header
        voivodeship = self.regions.canonical_voivodeship(province) or summary.voivodeship

        location = self._location(tree, params)
        description_node = tree.xpath('//div[@id="description"]')
        description = (
            strip_html(description_node[0].text_content())
            if description_node
            else summary.description
        )

        published_at = self._published_at(tree)
        seller = self._seller(tree)
        floor, floor_label = parse_floor(params.get("Piętro"))

        return replace(
            summary,
            title=_text(" ".join(tree.xpath("//title/text()"))) or summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market=_market(params),
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price if price is not None else summary.price_pln,
            price_per_m2_pln=price_per_m2 or summary.price_per_m2_pln,
            area_m2=area if area is not None else summary.area_m2,
            land_area_m2=land_area if land_area is not None else summary.land_area_m2,
            rooms=parse_rooms(params.get("Liczba pokoi")) or summary.rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=self._int_param(params.get("Rok budowy")) or summary.build_year,
            voivodeship=voivodeship,
            county=location.get("county") or summary.county,
            city=location.get("city") or summary.city,
            district=location.get("district") or summary.district,
            street=location.get("street") or summary.street,
            address_text=heading or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source",
            images=self._images(tree) or summary.images,
            seller_type=seller.get("type") or summary.seller_type,
            seller_name=seller.get("name") or summary.seller_name,
            published_at=published_at or summary.published_at,
            extra={
                "oferty_net": {
                    "params": params,
                    "reference": self._reference(tree),
                },
            },
        )

    def _summary(self, row: Any, query: Query) -> Listing | None:
        anchors = row.xpath('.//td[contains(@class, "cell_location")]//a')
        if not anchors:
            return None
        anchor = anchors[0]
        href = anchor.get("href")
        if not href:
            return None

        offer_type = _offer_type_from_url(href) or _offer_type(anchor.get("title"))
        if offer_type is not None and offer_type != query.offer_type:
            return None
        offer_type = offer_type or query.offer_type
        property_type = _property_type_from_url(href) or query.property_type
        if property_type != query.property_type:
            return None

        external_id = urlsplit(href).path.rstrip("/").rsplit(",", 1)[-1]
        label = _text(anchor.text_content())
        city, district = self._label_location(anchor)
        region = self.regions.match_city(city) or query.region
        if region != query.region:
            return None

        price = parse_price(self._cell(row, "cell_price"))
        area = parse_area(self._cell(row, "cell_area"))
        land_area = None
        if property_type == "plot":
            land_area = area
            area = None

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=anchor.get("title") or label,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_unit_price(self._cell(row, "cell_price_m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(self._cell(row, "cell_rooms")),
            voivodeship=region,
            city=city,
            district=district,
            address_text=", ".join(part for part in (city, district) if part) or None,
            published_at=self._date(self._cell(row, "cell_added_at")),
            extra={"oferty_net": {"label": label}},
        )

    def _total_pages(self, tree: Any) -> int | None:
        pages: list[int] = []
        for anchor in tree.xpath(
            '//li[contains(@class, "navigate")]//a[contains(@href, "page=")]',
        ):
            match = _PAGE_RE.search(anchor.get("href") or "")
            if match:
                pages.append(int(match.group(1)))
        return max(pages) if pages else None

    def _detail_property_type(self, tree: Any, summary: Listing) -> PropertyType:
        categories = tree.xpath(
            '//*[contains(text(), "Tutaj jesteś")]/..//a[contains(@href, "/")]',
        )
        for anchor in categories[1:2]:
            href = anchor.get("href") or ""
            first = urlsplit(href).path.strip("/").split(",", 1)[0]
            property_type = _CATEGORY_SEGMENTS.get(first)
            if property_type:
                return property_type
        return summary.property_type

    def _location(self, tree: Any, params: dict[str, str]) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        crumbs = [
            _text(anchor.text_content())
            for anchor in tree.xpath('//*[contains(text(), "Tutaj jesteś")]/..//a')
        ]
        parts = [part for part in crumbs[2:] if part]
        if not parts:
            return result

        province = self.regions.canonical_voivodeship(parts[0])
        if province is not None:
            parts = parts[1:]
        else:
            province = self.regions.canonical_voivodeship(params.get("Województwo"))
            if province is None:
                province = self.regions.canonical_voivodeship(params.get("Lokalizacja"))

        if parts and self.regions.match_county(parts[0]) is not None:
            result["county"] = parts[0]
            parts = parts[1:]

        if parts:
            result["city"] = parts[0]
        if len(parts) > 1:
            result["district"] = parts[1]

        heading = _text(" ".join(tree.xpath("//h1//text()")))
        if heading:
            heading_parts = [_text(part) for part in heading.split(",")]
            heading_parts = [part for part in heading_parts if part]
            if heading_parts:
                street = heading_parts[-1]
                if street and street not in (parts or []):
                    result["street"] = street
        return result

    def _label_location(self, anchor: Any) -> tuple[str | None, str | None]:
        text = anchor.get("title") or anchor.text_content()
        if "\n" in text:
            tail = text.partition("\n")[2]
        else:
            match = re.search(
                r"(?:na sprzeda[żz]|do wynaj[eę]cia|na wynajem)\s*(.*)$",
                text,
                re.IGNORECASE,
            )
            tail = match.group(1) if match else text
        parts = [_text(part) for part in tail.split(",")]
        parts = [part for part in parts if part]
        if not parts:
            return None, None
        city = parts[0]
        district = ", ".join(parts[1:]) or None
        return city, district

    @staticmethod
    def _cell(row: Any, name: str) -> str | None:
        cells = row.xpath(f'.//td[contains(@class, "{name}")]')
        return _text(cells[0].text_content()) if cells else None

    @staticmethod
    def _date(value: str | None) -> int | None:
        if not value:
            return None
        match = _DATE_RE.search(value)
        return parse_datetime_ms(match.group(1)) if match else None

    @staticmethod
    def _published_at(tree: Any) -> int | None:
        nodes = tree.xpath('//*[contains(text(), "Data dodania")]')
        if not nodes:
            return None
        match = re.search(
            r"Data dodania:\s*(\d{2})-(\d{2})-(\d{4})",
            " ".join(nodes[0].getparent().text_content().split()),
        )
        if match is None:
            return None
        day, month, year = (int(part) for part in match.groups())
        try:
            parsed = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            return None
        return int(parsed.timestamp() * 1000)

    @staticmethod
    def _int_param(value: str | None) -> int | None:
        if not value:
            return None
        digits = re.search(r"\d{4}", value)
        return int(digits.group(0)) if digits else None

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for node in tree.xpath('//div[@id="imageBoxShow"]//img'):
            src = node.get("data-original") or node.get("src")
            if not src or "/t.gif" in src:
                continue
            token = _GALLERY_TOKEN_RE.search(src)
            key = token.group(1) if token else src
            if key in seen:
                continue
            seen.add(key)
            images.append(src)
        return images[:20]

    @staticmethod
    def _seller(tree: Any) -> dict[str, str | None]:
        for anchor in tree.xpath('//a[starts-with(@href, "/agencja/")]'):
            name = _text(anchor.text_content())
            if name:
                return {"type": "agency", "name": name}
        for node in tree.xpath('//*[contains(text(), "Oferent")]'):
            label = _text(node.text_content())
            if label and "prywat" in normalize_text(label):
                return {"type": "private", "name": None}
            if label and "agencj" in normalize_text(label):
                return {"type": "agency", "name": None}
        return {"type": None, "name": None}

    @staticmethod
    def _reference(tree: Any) -> str | None:
        nodes = tree.xpath('//*[contains(text(), "Numer oferty")]')
        if not nodes:
            return None
        return _text(nodes[0].getparent().text_content().replace("Numer oferty:", ""))
