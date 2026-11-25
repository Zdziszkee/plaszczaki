"""Magazyny.online adapter (magazyny.online).

The site is a warehouse-park directory.  Region list pages are server-rendered
cards and every detail page exposes JSON-LD with the park coordinates, address,
floor size and a full description section.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import (
    parse_area,
    parse_datetime_ms,
    safe_float,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://magazyny.online"
SEARCH_PATH = "/magazyny/lista/{region}"

_ID_RE = re.compile(r"-(\d+)$")


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


def _find_node(nodes: Sequence[dict[str, Any]], *names: str) -> dict[str, Any] | None:
    for node in nodes:
        types = _node_types(node)
        if any(name in types for name in names):
            return node
    return None


def _facts(node: Any) -> dict[str, str]:
    facts: dict[str, str] = {}
    for dt in node.xpath(".//dt"):
        dd = dt.getnext()
        label = _text(dt.text_content())
        if label and dd is not None:
            value = _text(dd.text_content())
            if value:
                facts[label] = value
    return facts


class MagazynyOnlineAdapter:
    """Parse Magazyny.online region lists and warehouse-park detail pages."""

    id = "magazyny-online"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        return [
            Query(
                key=f"{region}:rent:commercial",
                url=BASE_URL + SEARCH_PATH.format(region=region),
                region=region,
                offer_type="rent",
                property_type="commercial",
            )
            for region in regions
        ]

    def with_page(self, query: Query, page: int) -> Query:
        # The region list has no pagination; keep the URL stable.
        return replace(query, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath(
            '//article[contains(concat(" ", normalize-space(@class), " "), " offer-card ")]',
        )
        items = [summary for card in cards if (summary := self._summary(card, query))]
        return SearchPage(
            items=items,
            current_page=1,
            total_pages=1,
            total_items=len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        nodes = _json_ld_nodes(tree)

        place = _find_node(nodes, "Place", "Accommodation")
        listing = _find_node(nodes, "RealEstateListing")
        breadcrumb = _find_node(nodes, "BreadcrumbList")
        organization = _find_node(nodes, "Organization")

        if place is None and listing is None:
            raise ParseError("Magazyny.online detail page is missing JSON-LD place/listing")

        geo = place.get("geo") if place else {}
        if not isinstance(geo, dict):
            geo = {}
        address = place.get("address") if place else {}
        if not isinstance(address, dict):
            address = {}
        floor_size = place.get("floorSize") if place else {}
        if not isinstance(floor_size, dict):
            floor_size = {}

        lat = safe_float(geo.get("latitude"))
        lng = safe_float(geo.get("longitude"))
        if lat is None or lng is None:
            raise ParseError("Magazyny.online detail page is missing coordinates")
        province = _text(address.get("addressRegion"))
        city = _text(address.get("addressLocality"))
        street = _text(address.get("streetAddress"))
        area = parse_area(floor_size.get("value")) or summary.area_m2

        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)
        if voivodeship is None:
            voivodeship = summary.voivodeship

        description = self._description(tree)
        if description is None and listing:
            description = strip_html(_text(listing.get("description")))

        facts = _facts(tree)
        available = self._fact_area(facts, "Do 1 miesiąca", "Do 3 miesięcy", "Powyżej 3 miesięcy")
        minimum_module = self._fact_area(facts, "Minimalny moduł")
        total_existing = self._fact_area(facts, "Istniejąca pow.")

        images = self._images(place, listing, tree)
        seller_name = _text(organization.get("name")) if organization else None

        detail_url = _text(listing.get("url")) if listing else None
        detail_id = None
        if detail_url:
            match = _ID_RE.search(detail_url.rstrip("/"))
            detail_id = match.group(1) if match else None

        extra = dict(summary.extra)
        extra["magazyny_online"] = {
            "available_area_m2": available,
            "minimum_module_m2": minimum_module,
            "existing_area_m2": total_existing,
            "facts": facts,
            "amenities": self._amenities(place),
        }

        return replace(
            summary,
            external_id=detail_id or summary.external_id,
            title=_text(place.get("name") if place else None)
            or _text(listing.get("name") if listing else None)
            or summary.title,
            description=description,
            offer_type="rent",
            property_type="commercial",
            price_kind="call",
            price_unit="monthly",
            price_pln=None,
            area_m2=area,
            voivodeship=voivodeship,
            county=self._county(breadcrumb),
            city=city or summary.city,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images or summary.images,
            seller_type="agency",
            seller_name=seller_name or summary.seller_name,
            published_at=parse_datetime_ms(listing.get("datePosted")) if listing else None,
            modified_at=parse_datetime_ms(listing.get("dateModified")) if listing else None,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing | None:
        link = _text(card.xpath('.//a[contains(@href, "/centrum-logistyczne/")]/@href'))
        if link is None:
            return None

        external_id = _text(card.get("data-offer"))
        if external_id is None:
            match = _ID_RE.search(link.rstrip("/"))
            if match is None:
                return None
            external_id = match.group(1)

        title = _text(card.xpath('.//*[contains(@class, "offer-title")]'))
        city = _text(card.xpath('.//*[contains(@class, "offer-city")]'))
        facts = _facts(card)
        area = self._fact_area(facts, "Pow. całkowita") or self._fact_area(
            facts,
            "Pow. dostępna",
        )
        lat = safe_float(
            _text(card.xpath('.//*[@itemprop="geo"]//meta[@itemprop="latitude"]/@content')),
        )
        lng = safe_float(
            _text(card.xpath('.//*[@itemprop="geo"]//meta[@itemprop="longitude"]/@content')),
        )
        image = _text(card.xpath(".//img/@src"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=BASE_URL + link if link.startswith("/") else link,
            title=title,
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind="call",
            price_unit="monthly",
            area_m2=area,
            voivodeship=query.region,
            city=city,
            address_text=city,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=[image] if image else [],
            seller_type="agency",
            extra={"magazyny_online": {"facts": facts}},
        )

    @staticmethod
    def _fact_area(facts: dict[str, str], *labels: str) -> float | None:
        for label in labels:
            if label in facts:
                return parse_area(facts[label])
        return None

    @staticmethod
    def _description(tree: Any) -> str | None:
        best: str | None = None
        for node in tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), " center-description-copy ")]',
        ):
            text = " ".join(node.text_content().split())
            if text and (best is None or len(text) > len(best)):
                best = text
        return strip_html(best)

    @staticmethod
    def _county(breadcrumb: dict[str, Any] | None) -> str | None:
        if not breadcrumb:
            return None
        elements = breadcrumb.get("itemListElement")
        if not isinstance(elements, list) or len(elements) < 4:
            return None
        names = [_text(item.get("name")) for item in elements if isinstance(item, dict)]
        names = [name for name in names if name]
        # ... region, county, city, listing
        return names[-3] if len(names) >= 3 else None

    @staticmethod
    def _amenities(place: dict[str, Any] | None) -> list[str]:
        if not place:
            return []
        values = place.get("amenityFeature")
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for item in values:
            name = _text(item.get("name")) if isinstance(item, dict) else _text(item)
            if name:
                result.append(name)
        return result

    @staticmethod
    def _images(
        place: dict[str, Any] | None,
        listing: dict[str, Any] | None,
        tree: Any,
    ) -> list[str]:
        images: list[str] = []
        for node in (place, listing):
            if not node:
                continue
            value = node.get("image")
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

        for node in tree.xpath("//img[@src]"):
            url = _text(node.get("src"))
            if (
                url
                and not url.startswith("data:")
                and "cdn.magazyny.online/Photos" in url
                and url not in images
            ):
                images.append(url)
        return images[:20]
