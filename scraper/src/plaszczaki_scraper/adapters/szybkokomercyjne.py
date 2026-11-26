"""Szybko Komercyjne adapter (szybkokomercyjne.pl).

Search pages are server-rendered microdata cards.  Detail pages expose the
offer JSON-LD, a full description block, a spec list and an OpenStreetMap
iframe whose marker carries the source coordinates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType
from plaszczaki_scraper.normalize import (
    parse_area,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://szybkokomercyjne.pl"
SEARCH_PATH = "/oferty/{offer}/nieruchomosci-komercyjne/{region}"

# (path segment, canonical offer type)
CATEGORIES: tuple[tuple[str, OfferType], ...] = (
    ("sprzedaz", "sale"),
    ("wynajem", "rent"),
)

_OSM_MARKER_RE = re.compile(r"marker=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_REGION_LINE_RE = re.compile(r"\(([^,()]+),\s*gm\.\s*")
_PRICE_PER_M2_RE = re.compile(r"([\d\s\u00a0.,]+)\s*zł\s*/\s*m", re.IGNORECASE)
_AREA_RE = re.compile(r"Powierzchnia\s*:?\s*([\d\s\u00a0.,]+)\s*m", re.IGNORECASE)
_ROOMS_RE = re.compile(r"Pokoje\s*:?\s*(\d+)", re.IGNORECASE)
_BUILD_YEAR_RE = re.compile(r"Rok budowy\s*:?\s*(\d{4})", re.IGNORECASE)
_TOTAL_RE = re.compile(r"ofert:\s*(\d+)", re.IGNORECASE)
_NO_RESULTS_RE = re.compile(
    r"Znalezionych ofert:\s*0\b|nie mamy ofert w podanej lokalizacji",
    re.IGNORECASE,
)
_PAGE_RE = re.compile(r"strona=(\d+)", re.IGNORECASE)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _text(item)
            if parsed:
                return parsed
        return None
    if hasattr(value, "text_content"):
        value = value.text_content()
    text = " ".join(str(value).split())
    return text or None


def _json_ld_nodes(tree: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for script in tree.xpath('//script[contains(@type, "ld+json")]'):
        raw = script.text or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            graph = payload.get("@graph")
            if isinstance(graph, list):
                nodes.extend(item for item in graph if isinstance(item, dict))
            else:
                nodes.append(payload)
        elif isinstance(payload, list):
            nodes.extend(item for item in payload if isinstance(item, dict))
    return nodes


def _node_types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _find_product(nodes: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for node in nodes:
        if any("Product" in type_name for type_name in _node_types(node)):
            return node
    return None


def _offer(node: dict[str, Any] | None) -> dict[str, Any]:
    if not node:
        return {}
    value = node.get("offers")
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


class SzybkokomercyjneAdapter:
    """Parse Szybko Komercyjne search and detail pages."""

    id = "szybkokomercyjne"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for offer, offer_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{offer_type}:commercial",
                        url=BASE_URL + SEARCH_PATH.format(offer=offer, region=region),
                        region=region,
                        offer_type=offer_type,
                        property_type="commercial",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["strona"] = str(page)
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment),
        )
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        body = " ".join(tree.text_content().split())

        # The portal keeps a nationwide "similar offers" block when the
        # requested region has no exact matches; it must not be crawled.
        if _NO_RESULTS_RE.search(body):
            return SearchPage(
                items=[],
                current_page=query.page,
                total_pages=1,
                total_items=0,
            )

        cards = tree.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " property_item ")]',
        )
        items: list[Listing] = []
        for card in cards:
            summary = self._summary(card, query)
            if summary is not None:
                items.append(summary)

        total_match = _TOTAL_RE.search(body)
        total_items = safe_int(total_match.group(1)) if total_match else None
        total_pages = self._total_pages(tree, total_items, len(items))

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        nodes = _json_ld_nodes(tree)
        product = _find_product(nodes)
        if product is None:
            raise ParseError("Szybko Komercyjne detail page is missing Product JSON-LD")

        offers = _offer(product)
        body = " ".join(tree.text_content().split())

        description = self._description(tree) or strip_html(_text(product.get("description")))
        area, rooms, build_year = self._specs(tree)

        price = parse_price(offers.get("price"))
        if price is None:
            price = parse_price(self._first_text(tree, './/h3[contains(@class, "property-price")]'))
        price_per_m2 = self._price_per_m2(body)

        lat, lng = self._coordinates(tree)
        if lat is None or lng is None:
            raise ParseError("Szybko Komercyjne detail page is missing map coordinates")
        city, district, province, commune = self._location(tree)

        voivodeship = self.regions.scoped_voivodeship(
            province,
            self.regions.region_for_point(lat, lng) or summary.voivodeship,
        )

        offer_type = self._offer_type(body) or summary.offer_type
        images = self._images(product, tree)

        address_text = ", ".join(part for part in (city, district) if part) or None

        extra = dict(summary.extra)
        extra["szybkokomercyjne"] = {
            "sku": _text(product.get("sku")),
            "availability": _text(offers.get("availability")),
            "valid_until": _text(offers.get("priceValidUntil")),
            "commune": commune,
        }

        return replace(
            summary,
            external_id=_text(product.get("sku")) or summary.external_id,
            title=_text(product.get("name")) or summary.title,
            description=description,
            offer_type=offer_type,
            property_type=summary.property_type,
            price_kind="asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area or summary.area_m2,
            rooms=rooms or summary.rooms,
            build_year=build_year,
            voivodeship=voivodeship,
            city=city or summary.city,
            district=district,
            street=summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images or summary.images,
            seller_type=summary.seller_type,
            seller_name=summary.seller_name,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing | None:
        href = _text(card.xpath('.//a[contains(@href, "/oferta-")]/@href'))
        if href is None:
            return None

        offer_id = _text(card.xpath('.//*[@itemprop="mpn"]/text()'))
        if offer_id is None:
            match = re.search(r"/oferta-(\d+)/", href)
            if match is None:
                return None
            offer_id = match.group(1)

        title = _text(
            card.xpath('.//span[@itemprop="name"]/text()')
            or card.xpath(".//h3//a/text()")
            or card.xpath(".//h3/text()"),
        )
        description = strip_html(_text(card.xpath('.//span[@itemprop="description"]/text()')))

        price_node = card.xpath('.//*[@itemprop="price"]')
        price = None
        if price_node:
            price = parse_price(price_node[0].get("content") or price_node[0].text_content())
        if price is None:
            price = parse_price(
                _text(card.xpath('.//p[contains(@class, "proerty_price")]')),
            )

        location = _text(card.xpath('.//p[contains(@class, "property_add")]'))
        city, region, raw_region = self._card_location(location)
        if raw_region is not None and region is None:
            return None
        if region is not None and region != query.region:
            return None

        tag = _text(card.xpath('.//*[contains(@class, "tag_t")]'))
        if tag:
            normalized_tag = tag.lower()
            card_offer_type = (
                "sale"
                if "sprzeda" in normalized_tag
                else "rent"
                if "wynaj" in normalized_tag
                else None
            )
            if card_offer_type is not None and card_offer_type != query.offer_type:
                return None

        area = parse_area(_text(card.xpath('.//*[contains(@class, "inc-area")]')))
        rooms = parse_rooms(_text(card.xpath('.//*[contains(@class, "inc-bed")]')))
        image = _text(
            card.xpath('.//link[@itemprop="image"]/@href')
            or card.xpath(".//img/@data-src")
            or card.xpath(".//img/@src"),
        )

        price_per_m2 = None
        price_text = _text(card.xpath('.//p[contains(@class, "proerty_price")]'))
        if price_text:
            match = _PRICE_PER_M2_RE.search(price_text)
            if match:
                price_per_m2 = parse_price(match.group(1))

        return Listing(
            source_id=self.source_id,
            external_id=offer_id,
            url=BASE_URL + href if href.startswith("/") else href,
            title=title,
            description=description,
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=rooms,
            voivodeship=region or query.region,
            city=city,
            address_text=location,
            lat=None,
            lng=None,
            images=[image] if image else [],
            seller_type="agency",
            extra={"szybkokomercyjne": {"offer_id": offer_id}},
        )

    def _card_location(
        self,
        location: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        if not location:
            return None, None, None
        city = location.split("(", 1)[0].strip() or None
        match = _REGION_LINE_RE.search(location)
        raw_region = match.group(1).strip() if match else None
        region = self.regions.canonical_voivodeship(raw_region) if raw_region else None
        return city, region, raw_region

    @staticmethod
    def _total_pages(tree: Any, total_items: int | None, item_count: int) -> int:
        pages: list[int] = []
        for node in tree.xpath(
            '//ul[contains(@class, "pagination")]//*[contains(@class, "page-link")]',
        ):
            raw = str(node.get("href") or node.text_content())
            match = _PAGE_RE.search(raw) or re.fullmatch(r"\s*(\d+)\s*", raw)
            page = safe_int(match.group(1)) if match else None
            if page is not None:
                pages.append(page)
        if pages:
            return max(pages)
        if total_items is not None and item_count:
            return max(1, (total_items + item_count - 1) // item_count)
        return 1

    @staticmethod
    def _description(tree: Any) -> str | None:
        best: str | None = None
        for block in tree.xpath('//div[contains(@class, "block-wrap")]'):
            text = " ".join(block.text_content().split())
            marker = "Opis szczegółowy"
            if marker not in text:
                continue
            description = text.split(marker, 1)[1].strip(" :")
            if description and (best is None or len(description) > len(best)):
                best = description
        return strip_html(best)

    @staticmethod
    def _specs(tree: Any) -> tuple[float | None, int | None, int | None]:
        spec_nodes = tree.xpath('//ul[contains(@class, "property-main-features")]')
        spec_text = " ".join(spec_nodes[0].text_content().split()) if spec_nodes else ""
        body = " ".join(tree.text_content().split())
        source = f"{spec_text} {body}"

        area_match = _AREA_RE.search(source)
        rooms_match = _ROOMS_RE.search(spec_text) or _ROOMS_RE.search(source)
        year_match = _BUILD_YEAR_RE.search(source)

        area = parse_area(area_match.group(1)) if area_match else None
        rooms = safe_int(rooms_match.group(1)) if rooms_match else None
        build_year = safe_int(year_match.group(1)) if year_match else None
        return area, rooms, build_year

    @staticmethod
    def _price_per_m2(body: str) -> int | None:
        match = _PRICE_PER_M2_RE.search(body)
        if match is None:
            return None
        return parse_price(match.group(1))

    @staticmethod
    def _coordinates(tree: Any) -> tuple[float | None, float | None]:
        for node in tree.xpath("//iframe[@src]"):
            match = _OSM_MARKER_RE.search(node.get("src") or "")
            if match:
                return safe_float(match.group(1)), safe_float(match.group(2))
        return None, None

    @staticmethod
    def _location(tree: Any) -> tuple[str | None, str | None, str | None, str | None]:
        anchors = tree.xpath('//a[@href="#asset_map"]')
        city: str | None = None
        district: str | None = None
        province: str | None = None
        commune: str | None = None
        if anchors:
            anchor = anchors[0]
            label = _text(anchor.xpath("./b")) or ""
            parts = [part.strip() for part in label.split(",", 1)]
            city = parts[0] or None
            district = parts[1] if len(parts) > 1 and parts[1] else None
            match = re.search(r"\(([^,()]+)\s*-\s*gmina\s*([^)]+)\)", " ".join(anchor.itertext()))
            if match:
                province = match.group(1).strip()
                commune = match.group(2).strip()
        return city, district, province, commune

    @staticmethod
    def _offer_type(body: str) -> OfferType | None:
        match = re.search(r"\bHome\s+(Sprzedaż|Wynajem)\b", body, re.IGNORECASE)
        if match is None:
            return None
        return "sale" if match.group(1).lower().startswith("sprzeda") else "rent"

    @staticmethod
    def _images(product: dict[str, Any], tree: Any) -> list[str]:
        images: list[str] = []
        value = product.get("image")
        if isinstance(value, dict):
            url = _text(value.get("url"))
            if url:
                images.append(url)
        elif isinstance(value, list):
            for item in value:
                url = _text(item.get("url")) if isinstance(item, dict) else _text(item)
                if url:
                    images.append(url)
        else:
            url = _text(value)
            if url:
                images.append(url)

        for node in tree.xpath("//img[@src or @data-src]"):
            url = _text(node.get("data-src") or node.get("src"))
            if (
                url
                and not url.startswith("data:")
                and ("mediaproxy.szybko.pl" in url or "/photo/asset/" in url)
                and url not in images
            ):
                images.append(url)
        return images[:20]

    @staticmethod
    def _first_text(tree: Any, query: str) -> str | None:
        nodes = tree.xpath(query)
        return _text(nodes[0]) if nodes else None
