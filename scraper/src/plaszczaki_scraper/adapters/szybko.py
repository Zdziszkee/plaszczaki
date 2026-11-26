"""Szybko.pl adapter (server-rendered search and detail pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    parse_area,
    parse_datetime_ms,
    parse_floor,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://szybko.pl"

# (URL slug, transaction, offer type, property type)
CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("lokal-mieszkalny+mieszkanie", "sprzedaz", "sale", "flat"),
    ("lokal-mieszkalny+mieszkanie", "wynajem", "rent", "flat"),
    ("lokal-mieszkalny+apartament", "sprzedaz", "sale", "flat"),
    ("lokal-mieszkalny+apartament", "wynajem", "rent", "flat"),
    ("dom", "sprzedaz", "sale", "house"),
    ("dom", "wynajem", "rent", "house"),
    ("dzialka", "sprzedaz", "sale", "plot"),
    ("dzialka", "wynajem", "rent", "plot"),
    ("nieruchomosci-komercyjne", "sprzedaz", "sale", "commercial"),
    ("nieruchomosci-komercyjne", "wynajem", "rent", "commercial"),
    ("nieruchomosci-komercyjne+garaz", "sprzedaz", "sale", "garage"),
    ("nieruchomosci-komercyjne+garaz", "wynajem", "rent", "garage"),
    ("pokoj", "wynajem", "rent", "room"),
)

_PAGE_RE = re.compile(r"[?&]strona=(\d+)")
_OSM_RE = re.compile(r"marker=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_STREETVIEW_RE = re.compile(r"[?&]location=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_AREA_RE = re.compile(r"([\d\s.,]+)\s*m\s*(?:²|2)", re.IGNORECASE)
_ROOMS_RE = re.compile(r"(\d+)\s*(?:pokoje?|pok\.)", re.IGNORECASE)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _text(node: Any) -> str | None:
    if node is None:
        return None
    return _clean(node.text_content())


def _first_text(nodes: list[Any]) -> str | None:
    return _text(nodes[0]) if nodes else None


def _first_attr(nodes: list[Any], name: str) -> str | None:
    for node in nodes:
        value = node.get(name)
        if value:
            return value.strip()
    return None


def _per_m2(value: str | None) -> int | None:
    """Parse a price-per-square-metre string without losing decimals."""
    amount = parse_area(value)
    return int(amount) if amount is not None else None


def _json_ld(tree: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            continue
        if isinstance(payload, list):
            blocks.extend(entry for entry in payload if isinstance(entry, dict))
        elif isinstance(payload, dict):
            blocks.append(payload)
    return blocks


def _find_offer(tree: Any) -> dict[str, Any]:
    for payload in _json_ld(tree):
        graph = payload.get("@graph")
        entries = graph if isinstance(graph, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            types = entry.get("@type")
            type_list = types if isinstance(types, list) else [types]
            if "offers" in entry and any(
                isinstance(item, str) and "Product" in item for item in type_list
            ):
                return entry
    return {}


def _find_agent(tree: Any) -> dict[str, Any]:
    for payload in _json_ld(tree):
        graph = payload.get("@graph")
        entries = graph if isinstance(graph, list) else [payload]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("@type") == "RealEstateAgent":
                return entry
    return {}


def _coordinates(tree: Any) -> tuple[float | None, float | None]:
    for href in tree.xpath("//a/@href"):
        if not isinstance(href, str):
            continue
        match = _OSM_RE.search(href) or _STREETVIEW_RE.search(href)
        if match is not None:
            return safe_float(match.group(1)), safe_float(match.group(2))
    return None, None


def _price_list(tree: Any) -> dict[str, str]:
    prices: dict[str, str] = {}
    for item in tree.xpath('//ul[contains(@class, "property-features")]/li'):
        text = _clean(item.text_content())
        if not text or ":" not in text:
            continue
        label, value = text.split(":", 1)
        prices[label.strip().lower()] = value.strip()
    return prices


def _feature_list(tree: Any) -> dict[str, str]:
    features: dict[str, str] = {}
    for item in tree.xpath('//ul[contains(@class, "property-main-features")]/li'):
        span = item.xpath("./span")
        if not span:
            continue
        label = _clean(item.text_content())
        if label is None:
            continue
        label = label.split()[0].lower()
        features[label] = _clean(span[0].text_content()) or ""
    return features


class SzybkoAdapter:
    """Parse Szybko.pl's server-rendered HTML and JSON-LD."""

    id = "szybko"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for slug, transaction, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{transaction}:{slug}",
                        url=f"{BASE_URL}/l/na-{transaction}/{slug}/{region}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
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
        cards = tree.xpath('//div[contains(@class, "gt-listing-item-asset")]')
        items = [self._summary(card, query) for card in cards]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=self._total_pages(tree, query.page),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        if not tree.xpath('//div[contains(@class, "property-description")]'):
            raise ParseError("Szybko detail page is missing the property description")

        offer = _find_offer(tree)
        agent = _find_agent(tree)
        features = _feature_list(tree)
        details = _price_list(tree)

        title = _first_text(tree.xpath('//div[contains(@class, "property-title")]//h2'))
        description = _clean(
            " ".join(
                value
                for value in (
                    _text(node)
                    for node in tree.xpath(
                        '//div[contains(@class, "property-description")]'
                        '//div[contains(@class, "show-more")]//p'
                    )
                )
                if value
            ),
        )

        location_node = tree.xpath('//h3[@id="location"]/following-sibling::h4[1]')
        province = city = street = district = None
        if location_node:
            parts = [
                part.strip() for part in (_text(location_node[0]) or "").split(",") if part.strip()
            ]
            if parts:
                province = parts[0]
                if len(parts) > 1:
                    city = parts[1]
                street_index = next(
                    (
                        index
                        for index, part in enumerate(parts)
                        if re.match(r"^(ul\.|al\.|os\.)", part, flags=re.IGNORECASE)
                    ),
                    None,
                )
                if street_index is not None:
                    street = re.sub(
                        r"^(ul\.|al\.|os\.)\s*",
                        "",
                        parts[street_index],
                        flags=re.IGNORECASE,
                    )
                    if street_index > 2:
                        district = parts[2]
                elif len(parts) > 2 and not re.fullmatch(r"\d{2}-\d{3}", parts[2]):
                    street = parts[2]

        voivodeship = self.regions.scoped_voivodeship(province, summary.voivodeship)
        lat, lng = _coordinates(tree)
        if lat is None or lng is None:
            lat = safe_float(_first_attr(tree.xpath("//*[@data-lat]"), "data-lat"))
            lng = safe_float(_first_attr(tree.xpath("//*[@data-lon]"), "data-lon"))

        area_text = features.get("powierzchnia")
        rooms_text = features.get("pokoje")
        area_match = _AREA_RE.search(area_text or "")
        rooms_match = _ROOMS_RE.search(rooms_text or "")

        area = parse_area(area_match.group(1) if area_match else area_text)
        land_area = None
        if summary.property_type == "plot":
            land_area, area = area, None

        price = parse_price(_first_text(tree.xpath('//div[contains(@class, "property-price")]')))
        if price is None:
            price = parse_price(str(offer.get("offers", {}).get("price")))
        per_m2 = _per_m2(_first_text(tree.xpath('//div[contains(@class, "sub-price")]')))

        floor_value = details.get("piętro") or details.get("pietro")
        if floor_value and "parter" in floor_value.lower():
            floor, floor_label = 0, floor_value
        else:
            floor, floor_label = parse_floor(floor_value)
        build_year = safe_int(
            details.get("rok budowy")
            or details.get("rok zbudowania")
            or next(
                (
                    value
                    for label, value in details.items()
                    if "rok" in label and re.fullmatch(r"\d{4}", value)
                ),
                None,
            ),
        )
        rooms = parse_rooms(
            details.get("liczba pokoi") or (rooms_match.group(1) if rooms_match else rooms_text),
        )

        images = [
            src
            for src in tree.xpath('//img[contains(@class, "details-img")]/@data-lazy')
            if isinstance(src, str)
        ]
        if not images:
            image = offer.get("image")
            if isinstance(image, dict) and isinstance(image.get("url"), str):
                images = [image["url"]]

        seller_name = _clean(agent.get("name") if isinstance(agent.get("name"), str) else None)

        extra = dict(summary.extra)
        extra["szybko"] = {
            "offer_id": str(offer.get("mpn") or offer.get("sku") or summary.external_id),
            "agency_url": agent.get("url"),
        }

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            offer_type=summary.offer_type,
            property_type=summary.property_type,
            market="unknown",
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if summary.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            build_year=build_year,
            voivodeship=voivodeship,
            city=city,
            district=district or summary.district,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            seller_type="agency" if agent else None,
            seller_name=seller_name,
            published_at=parse_datetime_ms(offer.get("datePosted")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        link = (
            card.xpath('.//a[contains(@class, "listing-title-heading")]')
            or card.xpath('.//a[contains(@class, "listing-img-container")]')
            or [None]
        )[0]
        href = link.get("href") if link is not None else None
        if not href:
            raise ParseError("Szybko listing card is missing its link")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        external_id = str(card.get("data-assetid") or "").strip()
        if not external_id:
            external_id = url.rstrip("/").rsplit("-", 1)[-1]

        price_text = _first_text(card.xpath('.//span[contains(@class, "listing-price")]'))
        per_m2 = _per_m2(
            _first_text(
                card.xpath('.//span[contains(@class, "listing-price")]/i'),
            ),
        )
        area_text = _first_text(card.xpath('.//li[contains(@class, "asset-feature area")]'))
        area_match = _AREA_RE.search(area_text or "")
        area = parse_area(area_match.group(1) if area_match else area_text)
        land_area = None
        if query.property_type == "plot":
            land_area, area = area, None
        rooms_text = _first_text(card.xpath('.//li[contains(@class, "asset-feature rooms")]'))

        address_node = card.xpath('.//a[contains(@class, "list-elem-address")]')[0:1]
        address = _first_text(address_node)
        city = street = None
        if address:
            parts = [part.strip() for part in address.split(",") if part.strip()]
            if parts:
                city = parts[0]
            if len(parts) > 1:
                street = re.sub(r"^(ul\.|al\.|os\.)\s*", "", parts[-1], flags=re.IGNORECASE)

        lat = safe_float(_first_attr(address_node, "data-lat"))
        lng = safe_float(_first_attr(address_node, "data-lon"))

        image = _first_attr(card.xpath(".//img"), "src")

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=_text(link),
            description=_first_text(card.xpath('.//span[@itemprop="description"]')),
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="call" if price_text is None else "asking",
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=parse_price(price_text),
            price_per_m2_pln=per_m2,
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(rooms_text),
            voivodeship=query.region,
            city=city,
            street=street,
            address_text=address,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=[image] if image else [],
            seller_name=_first_text(
                card.xpath('.//span[contains(@class, "asset-listing-owner")]'),
            ),
            extra={"szybko": {"transaction": query.offer_type}},
        )

    @staticmethod
    def _total_pages(tree: Any, fallback: int) -> int:
        pages = [int(value) for value in _PAGE_RE.findall(" ".join(tree.xpath("//a/@href")))]
        for node in tree.xpath('//li[contains(@class, "blank")]'):
            value = _text(node)
            if value and value.isdigit():
                pages.append(int(value))
        return max(pages, default=fallback)
