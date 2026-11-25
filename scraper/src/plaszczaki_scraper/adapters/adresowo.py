"""Adresowo adapter (search pages + detail pages).

Adresowo server-renders both the search cards (``div[data-offer-card]``) and the
detail page.  The detail page embeds a schema.org ``Place`` block with
``GeoCoordinates``, the full address and the listing description, which is the
authoritative source for coordinates.  Facts that schema.org does not carry
(price, floor, build year, rent, deposit) are rendered as a labelled parameter
list of ``<li>`` rows with a primary label and an optional secondary line.

``https://adresowo.pl/robots.txt`` allows offer pages (``/o/<slug>``); only
``/o/id/`` and ``/o/ajax/`` are disallowed.  Pagination is path based
(``/domy/fma_l2``), so :meth:`AdresowoAdapter.with_page` rewrites the path
instead of appending ``?page=``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    parse_rooms,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://adresowo.pl"

# Adresowo uses short voivodeship slugs in search paths.
REGION_SLUGS: dict[str, str] = {
    "malopolskie": "fma",
    "slaskie": "fsl",
}

CATEGORIES: tuple[tuple[str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sale", "flat"),
    ("mieszkania-wynajem", "rent", "flat"),
    ("domy", "sale", "house"),
    ("domy-wynajem", "rent", "house"),
    ("dzialki", "sale", "plot"),
    ("nieruchomosci-komercyjne", "sale", "commercial"),
    ("nieruchomosci-komercyjne-wynajem", "rent", "commercial"),
    ("pokoje-wynajem", "rent", "room"),
)

_PRICE_RE = re.compile(r"^(\d[\d\s\u00a0]*)\s*zł(?:\s*/\s*miesiąc)?$")
_PRICE_PER_M2_RE = re.compile(r"^(\d[\d\s\u00a0]*)\s*zł\s*/\s*m²$")
_AREA_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*m²$")
_ROOMS_RE = re.compile(r"^(\d+)\s*pok")
_FLOOR_RE = re.compile(r"^(parter|suterena|(\d+)\s*pi[eę]tro)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:rok budowy|wybudowany(?:o)?\s*w?)\s*(\d{4})", re.IGNORECASE)
_PLOT_RE = re.compile(
    r"^działka[^,]*?(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(m²|a|ar|ary|arów|ha)\b", re.IGNORECASE
)
_AREA_IN_TEXT_RE = re.compile(r"o powierzchni\s*(\d+(?:[.,]\d+)?)\s*m²")
_PAGE_SUFFIX_RE = re.compile(r"_l\d+$")

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "dom": "house",
    "dzialka": "plot",
    "lokal": "commercial",
    "pokoj": "room",
    "garaz": "garage",
}

_OFFER_TYPES: dict[str, OfferType] = {
    "na sprzedaz": "sale",
    "sprzedaz": "sale",
    "do wynajecia": "rent",
    "wynajem": "rent",
    "na wynajem": "rent",
}


def _squash(value: str | None) -> str:
    return " ".join(value.split()) if value else ""


def _tree(html: str) -> lxml_html.HtmlElement:
    if not html.strip():
        raise ParseError("Adresowo page is empty")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Adresowo page is not valid HTML: {exc}") from exc


def _json_ld(tree: lxml_html.HtmlElement) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for raw in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            nodes.append(payload)
    return nodes


def _ld_nodes(tree: lxml_html.HtmlElement, node_type: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for payload in _json_ld(tree):
        graph = payload.get("@graph")
        candidates = graph if isinstance(graph, list) else [payload]
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") == node_type:
                found.append(node)
    return found


def _place(tree: lxml_html.HtmlElement) -> dict[str, Any]:
    for node in _ld_nodes(tree, "Place"):
        if isinstance(node.get("geo"), dict):
            return node
    return {}


def _offer(tree: lxml_html.HtmlElement) -> dict[str, Any]:
    nodes = _ld_nodes(tree, "Offer")
    return nodes[0] if nodes else {}


def _pairs(tree: lxml_html.HtmlElement) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for block in tree.xpath('//li[contains(@class, "flex")]'):
        spans = block.xpath('.//div[contains(@class, "min-w-0")]/span')
        if not spans:
            continue
        primary = _squash(spans[0].text_content())
        secondary = _squash(spans[1].text_content()) if len(spans) > 1 else ""
        if primary:
            pairs.append((primary, secondary))
    return pairs


def _field_for(text: str) -> str | None:
    for pattern, field in (
        (_PRICE_PER_M2_RE, "price_per_m2"),
        (_PRICE_RE, "price"),
        (_AREA_RE, "area"),
        (_ROOMS_RE, "rooms"),
    ):
        if pattern.match(text):
            return field
    return None


class AdresowoAdapter:
    """Parse Adresowo search and offer pages."""

    id = "adresowo"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            slug = REGION_SLUGS.get(region)
            if slug is None or not self.regions.has_region(region):
                continue
            for category, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{category}",
                        url=f"{BASE_URL}/{category}/{slug}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=1)
        parts = urlsplit(query.url)
        path = _PAGE_SUFFIX_RE.sub("", parts.path.rstrip("/"))
        url = urlunsplit((parts.scheme, parts.netloc, f"{path}_l{page}", "", ""))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        cards = tree.xpath("//div[@data-offer-card]")
        if not cards and not tree.xpath('//div[@id="offer-list-results"]'):
            raise ParseError("Adresowo search page has no offer list")

        items = [self._summary(card, query) for card in cards]
        has_next = bool(
            tree.xpath('//a[@data-track="pagination-next"]') or tree.xpath('//link[@rel="next"]'),
        )
        # Adresowo only renders numbered pages never - just prev/next links - so a
        # page with a "next" link promises exactly one more page.
        total_pages = query.page + 1 if has_next else query.page
        return SearchPage(items=items, current_page=query.page, total_pages=total_pages)

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        place = _place(tree)
        pairs = _pairs(tree)
        if not place and not pairs:
            raise ParseError("Adresowo offer page is missing both geo data and parameters")

        ld_offer = _offer(tree)
        values = self._values(tree, pairs)

        raw_geo = place.get("geo")
        geo = raw_geo if isinstance(raw_geo, dict) else {}
        lat = self._float(geo.get("latitude"))
        lng = self._float(geo.get("longitude"))
        address = self._address(place.get("address"), pairs)

        description = _squash(place.get("description")) or summary.description
        offer_type = self._offer_type(pairs) or summary.offer_type
        property_type = self._property_type(pairs) or summary.property_type

        price = values.get("price")
        if price is None:
            ld_price = parse_price(ld_offer.get("price"))
            price = ld_price if ld_price is not None else summary.price_pln
        price_per_m2 = values.get("price_per_m2") or summary.price_per_m2_pln

        area = values.get("area")
        if area is None:
            area = summary.area_m2
        rooms = values.get("rooms")
        if rooms is None:
            rooms = summary.rooms

        if property_type == "plot":
            land_area, area = area, None
        else:
            land_area = values.get("land_area")

        price_unit = "monthly" if offer_type == "rent" else "total"

        extra = dict(summary.extra)
        adresowo_extra = {
            "parameters": [{"label": primary, "value": secondary} for primary, secondary in pairs],
            "negotiable": values.get("negotiable", False),
            "ownership": values.get("ownership"),
            "condition": values.get("condition"),
        }
        extra["adresowo"] = adresowo_extra

        return replace(
            summary,
            title=self._title(tree) or summary.title,
            description=strip_html(description),
            offer_type=offer_type,
            property_type=property_type,
            market=self._market(pairs) or summary.market,
            price_kind="asking" if price is not None else summary.price_kind,
            price_unit=price_unit,
            price_pln=price,
            rent_pln=values.get("rent") or summary.rent_pln,
            deposit_pln=values.get("deposit"),
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=values.get("floor"),
            floor_label=values.get("floor_label"),
            build_year=values.get("build_year"),
            voivodeship=address["voivodeship"] or summary.voivodeship,
            county=address["county"],
            commune=address["commune"],
            city=address["city"] or summary.city,
            district=address["district"],
            street=address["street"] or summary.street,
            address_text=address["address_text"] or summary.address_text,
            lat=lat if lat is not None else summary.lat,
            lng=lng if lng is not None else summary.lng,
            geo_source="source" if lat is not None and lng is not None else summary.geo_source,
            seller_type=values.get("seller_type") or summary.seller_type,
            images=self._images(tree) or summary.images,
            extra=extra,
        )

    # -- search -----------------------------------------------------------

    def _summary(self, card: lxml_html.HtmlElement, query: Query) -> Listing:
        external_id = (card.get("data-id") or "").strip()
        href = self._first(card.xpath(".//h2//a/@href")) or self._first(
            card.xpath('.//a[starts-with(@href, "/o/")]/@href'),
        )
        if not external_id or not href:
            raise ParseError("Adresowo offer card is missing its id or link")

        price, area, rooms = None, None, None
        for paragraph in card.xpath('.//p[not(contains(@class, "line-clamp-4"))]'):
            text = _squash(paragraph.text_content())
            field = _field_for(text)
            if field == "price" and price is None:
                price = parse_price(text)
            elif field == "area" and area is None:
                area = parse_area(text)
            elif field == "rooms" and rooms is None:
                rooms = parse_rooms(text)

        location = [
            _squash(span.text_content())
            for span in card.xpath('.//h2//span[contains(@class, "line-clamp-1")]')
        ]
        city = location[0] if location else None
        street = location[1] if len(location) > 1 else None

        kind = _squash(
            " ".join(card.xpath('.//span[contains(@class, "pt-4")]/span[1]//text()')),
        )
        property_type = self._kind_property(kind) or query.property_type
        seller_text = _squash(
            " ".join(card.xpath('.//span[contains(@class, "pt-4")]/span[2]//text()')),
        )

        description = self._first(
            [
                _squash(p.text_content())
                for p in card.xpath('.//p[contains(@class, "line-clamp-4")]')
            ],
        )
        image = self._first(card.xpath(".//picture//img/@src"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=urljoin(BASE_URL, href),
            title=f"{kind}, {city}" if kind and city else kind or city,
            description=strip_html(description),
            offer_type=query.offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking" if price is not None else "unknown",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            area_m2=area,
            rooms=rooms,
            voivodeship=query.region,
            city=city,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or None,
            images=[image] if image else [],
            seller_type="private" if "bez pośrednik" in seller_text else None,
            extra={"adresowo": {"kind": kind}},
        )

    # -- detail -----------------------------------------------------------

    def _values(self, tree: lxml_html.HtmlElement, pairs: list[tuple[str, str]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for span in tree.xpath('//span[contains(@class, "block")]'):
            text = _squash(span.text_content())
            field = _field_for(text)
            if field is None or field in values:
                continue
            if field == "price":
                values["price"] = parse_price(text)
            elif field == "price_per_m2":
                values["price_per_m2"] = parse_price(text)
            elif field == "area":
                values["area"] = parse_area(text)
            elif field == "rooms":
                values["rooms"] = parse_rooms(text)

        for primary, secondary in pairs:
            normalized = normalize_text(primary)
            combined = f"{primary} {secondary}"
            if normalized.startswith(("na sprzedaz", "do wynajecia")):
                area = _AREA_IN_TEXT_RE.search(primary)
                if area is not None and "area" not in values:
                    values["area"] = parse_area(area.group(1))
            if (
                "rok budowy" in normalize_text(combined) or "wybudowany" in normalize_text(combined)
            ) and "build_year" not in values:
                year = _YEAR_RE.search(combined)
                if year is not None:
                    values["build_year"] = safe_int(year.group(1))
            if "dzialka" in normalized and "land_area" not in values:
                plot = _PLOT_RE.search(primary)
                if plot is not None:
                    values["land_area"] = parse_area(plot.group(0))
            floor = _FLOOR_RE.match(primary)
            if floor is not None and "floor" not in values:
                label = floor.group(1)
                values["floor_label"] = label
                values["floor"] = (
                    0 if normalize_text(label) == "parter" else safe_int(floor.group(2))
                )
            if normalized.startswith("oplaty") and "rent" not in values:
                values["rent"] = parse_price(secondary or primary)
            if normalized.startswith("kaucja") and "deposit" not in values:
                values["deposit"] = parse_price(secondary or primary)
            if "cena do negocjacji" in normalized:
                values["negotiable"] = True
            if "bezposrednio od" in normalized:
                values["seller_type"] = "private"
            elif "przez agenta" in normalized or "biuro nieruchomosci" in normalized:
                values["seller_type"] = "agency"
            if normalized.startswith("odrebna wlasnosc") or "ksiegi wieczystej" in normalized:
                values["ownership"] = primary
            if "stanie" in normalized and "condition" not in values:
                values["condition"] = secondary or primary
        return values

    def _address(
        self,
        raw: Any,
        pairs: list[tuple[str, str]],
    ) -> dict[str, str | None]:
        address_text = _squash(raw.get("streetAddress") if isinstance(raw, dict) else None)
        parts = [part.strip() for part in address_text.split(",") if part.strip()]
        result: dict[str, str | None] = {
            "address_text": address_text or None,
            "voivodeship": None,
            "county": None,
            "commune": None,
            "city": None,
            "district": None,
            "street": None,
        }
        for part in parts:
            normalized = normalize_text(part)
            if normalized.startswith("gmina "):
                result["commune"] = part[len("gmina ") :].strip()
                continue
            if normalized.startswith("powiat "):
                result["county"] = part[len("powiat ") :].strip()
                continue
            if part.lower().startswith(("ul.", "al.", "os.", "pl.")):
                result["street"] = part
                continue
            province = self.regions.canonical_voivodeship(part)
            if province is not None:
                result["voivodeship"] = province
                continue
            if result["city"] is None:
                city, district = self._split_locality(part)
                result["city"], result["district"] = city, district

        for primary, secondary in pairs:
            if not result["street"] and secondary.lower().startswith(("ul.", "al.", "os.")):
                result["street"] = secondary
            if result["city"] is None and primary and "," in primary:
                city, district = self._split_locality(primary)
                result["city"], result["district"] = city, district

        if result["voivodeship"] is None:
            result["voivodeship"] = self.regions.match_city(
                result["city"]
            ) or self.regions.match_county(
                result["county"],
            )
        return result

    def _split_locality(self, locality: str) -> tuple[str | None, str | None]:
        text = locality.strip()
        if not text:
            return None, None
        head, _, tail = text.partition(" ")
        if tail and self.regions.match_city(head) is not None:
            return head, tail
        return text, None

    @staticmethod
    def _title(tree: lxml_html.HtmlElement) -> str | None:
        meta = tree.xpath('//meta[@property="og:title"]/@content')
        title = _squash(meta[0]) if meta else ""
        return title or None

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for url in tree.xpath('//div[contains(@class, "swiper")]//img/@src'):
            if not isinstance(url, str) or "/oi/" not in url or url in images:
                continue
            images.append(url)
        return images[:20]

    @staticmethod
    def _offer_type(pairs: list[tuple[str, str]]) -> OfferType | None:
        for primary, _ in pairs:
            normalized = normalize_text(primary)
            for prefix, offer_type in _OFFER_TYPES.items():
                if normalized.startswith(prefix):
                    return offer_type
        return None

    @staticmethod
    def _property_type(pairs: list[tuple[str, str]]) -> PropertyType | None:
        for primary, _ in pairs:
            for word, property_type in _PROPERTY_TYPES.items():
                if word in normalize_text(primary).split():
                    return property_type
        return None

    @staticmethod
    def _kind_property(kind: str) -> PropertyType | None:
        words = normalize_text(kind).split()
        for word, property_type in _PROPERTY_TYPES.items():
            if word in words:
                return property_type
        return None

    @staticmethod
    def _market(pairs: list[tuple[str, str]]) -> Market | None:
        text = normalize_text(" ".join(f"{primary} {secondary}" for primary, secondary in pairs))
        if "rynek pierwotny" in text or "deweloper" in text:
            return "primary"
        if "rynek wtorny" in text:
            return "secondary"
        return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except TypeError, ValueError:
            return None

    @staticmethod
    def _first(values: list[Any]) -> Any:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
