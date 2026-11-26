"""Sprzedam Biznes adapter (sprzedambiznes.pl).

Region and transaction filters are plain query parameters (``w`` and ``rt``).
Search cards are server-rendered.  Detail pages expose a Product JSON-LD block
whose ``offers.areaServed.geo`` carries the source coordinates, and the full
description sits in the article next to the "Opis oferty" heading.
"""

from __future__ import annotations

import json
import math
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
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://sprzedambiznes.pl"
SEARCH_URL = f"{BASE_URL}/nieruchomosci-komercyjne"

CATEGORIES: tuple[tuple[str, OfferType], ...] = (
    ("sprzedaz", "sale"),
    ("wynajem", "rent"),
)

_DETAIL_HREF_RE = re.compile(r"/nieruchomosci-komercyjne/[a-z0-9-]+-(\d+)$")
_TOTAL_RE = re.compile(r'"Total"\s*:\s*(\d+)')
_PAGE_SIZE_RE = re.compile(r"var\s+pageSize\s*=\s*(\d+)\s*;")
_PRICE_RE = re.compile(r"Cena(?: sprzedaży)?\s*:\s*([\d\s\u00a0.,]+)\s*zł", re.IGNORECASE)
_AREA_RE = re.compile(r"Powierzchnia\s*:\s*([\d\s\u00a0.,]+)\s*m", re.IGNORECASE)
_ROOMS_RE = re.compile(r"Pomieszczenia\s*:\s*(\d+)", re.IGNORECASE)
_FLOORS_RE = re.compile(r"Kondygnacje\s*:\s*(\d+)", re.IGNORECASE)
_OFFER_ID_RE = re.compile(r"Numer oferty\s*:\s*(\d+)", re.IGNORECASE)


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


def _find_product(nodes: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for node in nodes:
        value = node.get("@type")
        types = {value} if isinstance(value, str) else set(value or [])
        if "Product" in types:
            return node
    return None


def _offer(product: dict[str, Any] | None) -> dict[str, Any]:
    if not product:
        return {}
    value = product.get("offers")
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _area_served(offer: dict[str, Any]) -> dict[str, Any]:
    value = offer.get("areaServed")
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


class SprzedambiznesAdapter:
    """Parse Sprzedam Biznes commercial search and detail pages."""

    id = "sprzedambiznes"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for transaction, offer_type in CATEGORIES:
                url = f"{SEARCH_URL}?{urlencode({'w': region, 'rt': transaction})}"
                queries.append(
                    Query(
                        key=f"{region}:{offer_type}:commercial",
                        url=url,
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
        total_match = _TOTAL_RE.search(html)
        total_items = safe_int(total_match.group(1)) if total_match else None

        cards = tree.xpath('//a[contains(@href, "/nieruchomosci-komercyjne/")][article]')
        items: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            summary = self._summary(card, query)
            if summary is None or summary.external_id in seen:
                continue
            seen.add(summary.external_id)
            items.append(summary)

        page_size_match = _PAGE_SIZE_RE.search(html)
        page_size = safe_int(page_size_match.group(1)) if page_size_match else None
        total_pages = 1
        if total_items is not None and page_size:
            total_pages = max(1, math.ceil(total_items / page_size))

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        product = _find_product(_json_ld_nodes(tree))
        if product is None:
            raise ParseError("Sprzedam Biznes detail page is missing Product JSON-LD")

        offer = _offer(product)
        served = _area_served(offer)
        geo_value = served.get("geo")
        if isinstance(geo_value, dict):
            geo = geo_value
        else:
            fallback = offer.get("geo")
            geo = fallback if isinstance(fallback, dict) else {}

        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if lat is None or lng is None:
            raise ParseError("Sprzedam Biznes detail page is missing coordinates")
        address = _text(geo.get("address")) or _text(served.get("address"))

        body = " ".join(tree.text_content().split())
        price = parse_price(offer.get("price"))
        if price is None:
            match = _PRICE_RE.search(body)
            if match:
                price = parse_price(match.group(1))

        area = self._area(body)
        rooms = self._int_match(_ROOMS_RE, body)
        floors = self._int_match(_FLOORS_RE, body)

        street, city, province = self._split_address(address)
        voivodeship = self.regions.scoped_voivodeship(
            province,
            self.regions.region_for_point(lat, lng) or summary.voivodeship,
        )

        description = self._description(tree)
        if description is None:
            description = strip_html(_text(product.get("description")))

        offer_id_match = _OFFER_ID_RE.search(body)
        external_id = offer_id_match.group(1) if offer_id_match else summary.external_id

        title = _text(tree.xpath("//h1/text()")) or _text(product.get("name")) or summary.title
        offer_type = summary.offer_type

        extra = dict(summary.extra)
        extra["sprzedambiznes"] = {
            "category": _text(offer.get("category")),
            "business_function": _text(offer.get("businessFunction")),
            "floors": floors,
        }

        price_per_m2 = None
        if price is not None and area:
            price_per_m2 = round(price / area)

        return replace(
            summary,
            external_id=external_id,
            title=title,
            description=description,
            offer_type=offer_type,
            property_type="commercial",
            market="unknown",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area or summary.area_m2,
            rooms=rooms or summary.rooms,
            voivodeship=voivodeship,
            city=city or summary.city,
            street=street or summary.street,
            address_text=address or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(product, tree) or summary.images,
            seller_type=summary.seller_type,
            seller_name=summary.seller_name,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing | None:
        href = _text(card.get("href"))
        if href is None:
            return None
        match = _DETAIL_HREF_RE.search(href)
        if match is None:
            return None
        external_id = match.group(1)

        article = card.xpath(".//article")[0]
        title = _text(article.xpath(".//h2"))
        description = strip_html(_text(article.xpath(".//p[1]")))
        price = parse_price(
            _text(article.xpath('.//p[contains(@class, "h4")]')),
        )
        location = _text(article.xpath('.//span[contains(@class, "text-capitalize")]'))
        city, region, raw_region, street = self._location(location)
        if raw_region is not None and region is None:
            return None
        if region is not None and region != query.region:
            return None

        category = _text(article.xpath(".//p/span"))
        transaction = _text(article.xpath('.//p[contains(@class, "g-front-size-16")]'))
        image = _text(article.xpath(".//img/@src"))
        if image and "#:" in image:
            image = None

        offer_type = query.offer_type
        if transaction:
            lowered = transaction.lower()
            if lowered.startswith("wynaj"):
                offer_type = "rent"
            elif lowered.startswith("sprzeda"):
                offer_type = "sale"

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=BASE_URL + href if href.startswith("/") else href,
            title=title,
            description=description,
            offer_type=offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            voivodeship=region or query.region,
            city=city,
            street=street,
            address_text=location,
            images=[image] if image else [],
            seller_type="agency",
            extra={"sprzedambiznes": {"category": category}},
        )

    def _location(
        self,
        location: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        if not location:
            return None, None, None, None
        parts = [part.strip() for part in location.split(",") if part.strip()]
        if not parts:
            return None, None, None, None
        raw_region = parts[-1] if len(parts) > 1 else None
        region = self.regions.canonical_voivodeship(raw_region) if raw_region is not None else None
        if len(parts) >= 3:
            city = self._clean_city(parts[-2])
            street = ", ".join(parts[:-2])
        else:
            city = self._clean_city(parts[0])
            street = None
        return city, region, raw_region, street

    @staticmethod
    def _clean_city(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+\d{2}-\d{3}$", "", value).strip()
        return cleaned or None

    def _split_address(
        self,
        address: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        if not address:
            return None, None, None
        parts = [part.strip() for part in address.split(",") if part.strip()]
        if not parts:
            return None, None, None
        province = parts[-1]
        city = self._clean_city(parts[-2]) if len(parts) >= 2 else None
        street = ", ".join(parts[:-2]) if len(parts) > 2 else None
        return street, city, province

    @staticmethod
    def _description(tree: Any) -> str | None:
        headings = tree.xpath('//h3[contains(normalize-space(.), "Opis oferty")]')
        for heading in headings:
            for sibling in heading.itersiblings():
                if not isinstance(sibling.tag, str):
                    continue
                text = " ".join(sibling.text_content().split())
                if text:
                    return strip_html(text)
        return None

    @staticmethod
    def _area(body: str) -> float | None:
        match = _AREA_RE.search(body)
        return parse_area(match.group(1)) if match else None

    @staticmethod
    def _int_match(pattern: re.Pattern[str], body: str) -> int | None:
        match = pattern.search(body)
        return safe_int(match.group(1)) if match else None

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

        for node in tree.xpath(
            '//img[contains(@src, "RenderImage") or contains(@src, "RenderThumbnail")]'
        ):
            url = _text(node.get("src"))
            if url and not url.startswith("data:") and url not in images:
                images.append(url)
        return images[:20]
