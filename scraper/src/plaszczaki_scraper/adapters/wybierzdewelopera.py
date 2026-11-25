"""Wybierzdewelopera.pl adapter (new-home investment catalogue).

The site is a WordPress installation, but its REST API is disabled.  Search
pages are server-rendered and list investments as ``article.offer-card``
elements with name, location, delivery date, area and room ranges.  Detail
pages expose a schema.org ``Product`` with the full description and a
per-square-metre price, plus Leaflet map coordinates in ``data-lat`` /
``data-lng`` attributes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import (
    parse_area,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://wybierzdewelopera.pl"

REGION_SLUGS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _product(html: str) -> dict[str, Any]:
    tree = lxml_html.fromstring(html)
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(script.text or "")
        except json.JSONDecodeError:
            continue
        graph = payload.get("@graph") if isinstance(payload, dict) else None
        items = graph if isinstance(graph, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    raise ParseError("Wybierzdewelopera detail page is missing the Product JSON-LD")


class WybierzdeweloperaAdapter:
    """Parse wybierzdewelopera.pl investment listings."""

    id = "wybierzdewelopera"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            slug = REGION_SLUGS.get(region)
            if slug is None:
                continue
            queries.append(
                Query(
                    key=f"{region}:inwestycje",
                    url=f"{BASE_URL}/inwestycje/{slug}/",
                    region=region,
                    offer_type="sale",
                    property_type="flat",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=1)
        return replace(query, url=f"{query.url.rstrip('/')}/page/{page}/", page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath('//article[contains(@class, "offer-card")]')
        if not cards:
            raise ParseError("Wybierzdewelopera search page is missing investment cards")

        items = [item for card in cards if (item := self._summary(card, query)) is not None]

        pages = {int(match) for match in re.findall(r"/page/(\d+)/", html)}
        total_pages = max(pages) if pages else None
        total_items = None
        match = re.search(r"<b>(\d+)</b>\s*inwestycji", html)
        if match:
            total_items = int(match.group(1))

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        product = _product(html)
        offers = _dict(product.get("offers"))
        offered = _dict(offers.get("itemOffered"))
        specification = _dict(offers.get("priceSpecification"))

        price_per_m2 = None
        if str(specification.get("unitText", "")).upper() == "PER_SQUARE_METER":
            price_per_m2 = parse_price(specification.get("price"))
        price_per_m2 = price_per_m2 or summary.price_per_m2_pln

        floor_size = _dict(offered.get("floorSize"))
        area = parse_area(floor_size.get("minValue")) or summary.area_m2
        rooms = parse_rooms(_dict(offered.get("numberOfRooms")).get("minValue"))
        rooms = rooms or summary.rooms

        address = _dict(offered.get("address"))
        locality = _text(address.get("addressLocality"))
        city, district = self._split_location(locality)
        voivodeship = self.regions.canonical_voivodeship(_text(address.get("addressRegion")))
        street = _text(address.get("streetAddress"))

        lat, lng = self._coordinates(html)

        brand = _dict(product.get("brand"))
        seller_name = _text(brand.get("name")) or summary.seller_name
        if seller_name is None:
            tree = lxml_html.fromstring(html)
            alts = tree.xpath('//a[starts-with(@href, "/deweloper/")]//img/@alt')
            if alts:
                seller_name = _text(alts[0])

        title = summary.title
        tree = lxml_html.fromstring(html)
        headings = tree.xpath("//h1//text()")
        if headings:
            heading = " ".join(" ".join(headings).split())
            if heading:
                title = heading

        address_text = ", ".join(part for part in (street, city) if part) or None
        extra = dict(summary.extra)
        extra["wybierzdewelopera"] = {
            "offer_count": safe_int(offers.get("offerCount")),
            "area_max": parse_area(floor_size.get("maxValue")),
            "rooms_max": parse_rooms(_dict(offered.get("numberOfRooms")).get("maxValue")),
        }

        return replace(
            summary,
            title=title,
            description=_text(product.get("description")) or summary.description,
            offer_type="sale",
            property_type=summary.property_type,
            market="primary",
            price_kind=summary.price_kind,
            price_unit="total",
            price_pln=summary.price_pln,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=rooms,
            voivodeship=voivodeship or summary.voivodeship,
            county=summary.county,
            city=city or summary.city,
            district=district or summary.district,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=summary.images,
            seller_type="developer",
            seller_name=seller_name,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing | None:
        links = card.xpath(".//h3//a/@href")
        if not links:
            return None
        url = urljoin(BASE_URL, links[0])
        external_id = url.rstrip("/").rsplit("/", 1)[-1]
        name = _text(" ".join(card.xpath(".//h3//a//text()")))

        location = _text(
            " ".join(card.xpath('.//*[contains(@class, "offer-card__location")]//span//text()'))
        )
        city, district = self._split_location(location)

        specs: dict[str, str] = {}
        for term, value in zip(card.xpath(".//dt"), card.xpath(".//dd"), strict=False):
            key = _text(term.text_content())
            if key:
                specs[key] = _text(value.text_content()) or ""

        area = parse_area(specs.get("Powierzchnia"))
        rooms = parse_rooms(specs.get("Liczba pokoi"))
        build_year = None
        delivery = specs.get("Zakończenie budowy")
        if delivery:
            match = re.search(r"(\d{4})", delivery)
            build_year = int(match.group(1)) if match else None

        images: list[str] = []
        for source in card.xpath(".//img/@src"):
            image = _text(source)
            if image:
                images.append(urljoin(BASE_URL, image))
                break

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=name,
            offer_type="sale",
            property_type="flat",
            market="primary",
            price_kind="unknown",
            price_unit="total",
            price_pln=None,
            price_per_m2_pln=None,
            area_m2=area,
            rooms=rooms,
            build_year=build_year,
            voivodeship=query.region,
            city=city,
            district=district,
            address_text=location,
            images=images,
            seller_type="developer",
            seller_name=None,
            extra={"wybierzdewelopera": {"region_query": query.region}},
        )

    def _coordinates(self, html: str) -> tuple[float | None, float | None]:
        tree = lxml_html.fromstring(html)
        for node in tree.xpath("//*[@data-lat]"):
            lat = safe_float(node.get("data-lat"))
            lng = safe_float(node.get("data-lng"))
            if lat is not None and lng is not None:
                return lat, lng
        return None, None

    def _split_location(self, value: str | None) -> tuple[str | None, str | None]:
        """Split ``"Kraków Prądnik Biały, ul. Starego Dębu"`` into city/district."""
        if not value:
            return None, None
        head = value.split(",", 1)[0].strip()
        if not head:
            return None, None
        words = head.split()
        for length in range(len(words), 0, -1):
            candidate = " ".join(words[:length])
            if self.regions.match_city(candidate) is not None:
                district = " ".join(words[length:]) or None
                return candidate, district
        return head, None
