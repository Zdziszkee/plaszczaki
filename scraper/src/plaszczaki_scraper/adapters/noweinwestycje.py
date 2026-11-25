"""Noweinwestycje.pl adapter (new-home investment catalogue).

The portal is a server-rendered Homsters application.  Every search card is a
``div.js-project-item`` carrying coordinates and a hidden
``GmgEventData_ProjectId_<id>`` JSON blob with the price/area/room ranges.
Detail pages ship a ``GmgDevelopmentEventData`` blob (market, vendor, area,
price per m², location tree) plus a map block with the exact coordinates and a
dedicated description section.
"""

from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    parse_area,
    parse_datetime_ms,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://noweinwestycje.pl"

REGION_SLUGS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "apartment": "flat",
    "flat": "flat",
    "house": "house",
    "plot": "plot",
}


def _number(value: Any) -> float | None:
    """Parse Homsters numbers, which use a comma decimal separator."""
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    return safe_float(text)


def _low(value: Any) -> float | None:
    """Parse ``"25.23-72.70"`` into its lower bound."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    first = text.split("-", 1)[0].strip()
    return _number(first)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_blob(html: str, name: str) -> dict[str, Any] | None:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', html)
    if match is None:
        return None
    try:
        payload = json.loads(html_module.unescape(match.group(1)))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class NoweinwestycjeAdapter:
    """Parse noweinwestycje.pl investment listings."""

    id = "noweinwestycje"

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
                    key=f"{region}:rynek-pierwotny",
                    url=f"{BASE_URL}/rynek-pierwotny/{slug}",
                    region=region,
                    offer_type="sale",
                    property_type="flat",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        # robots.txt disallows query strings, so pagination is path based.
        if page <= 1:
            return replace(query, url=query.url, page=1)
        return replace(query, url=f"{query.url.rstrip('/')}/page{page}", page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath('//div[contains(@class, "js-project-item")]')
        if not cards:
            raise ParseError("Noweinwestycje search page is missing investment cards")

        items: list[Listing] = []
        for card in cards:
            item = self._summary(card, query)
            if item is not None:
                items.append(item)

        pages = {int(match) for match in re.findall(r"/page(\d+)", html)}
        total_pages = max(pages) if pages else None

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        blob = _json_blob(html, "GmgDevelopmentEventData")
        if blob is None:
            raise ParseError("Noweinwestycje detail page is missing the development payload")

        tree = lxml_html.fromstring(html)
        lat, lng = self._coordinates(tree)
        description = self._description(tree)
        title = self._heading(tree) or summary.title

        price = self._price(tree)
        area = parse_area(blob.get("area_from")) or summary.area_m2
        price_per_m2 = parse_price(blob.get("price_m2_from")) or summary.price_per_m2_pln

        province = _text(blob.get("province"))
        city = _text(blob.get("city"))
        district = self._district(_text(blob.get("location_tree")), city)
        voivodeship = self.regions.canonical_voivodeship(province)

        company = _dict(blob.get("company"))
        market = blob.get("market_type")
        property_type = _PROPERTY_TYPES.get(
            str(blob.get("propertyType") or "apartment").lower(),
            "flat",
        )
        completion = _text(blob.get("completion_date_to"))
        build_year = safe_int(completion[:4]) if completion else None

        address_text = ", ".join(part for part in (city, district) if part) or None

        extra = dict(summary.extra)
        extra["noweinwestycje"] = {
            "development_id": safe_int(blob.get("id")),
            "area_to": parse_area(blob.get("area_to")),
            "price_m2_to": parse_price(blob.get("price_m2_to")),
            "completion_date": completion,
            "owner": _text(blob.get("owner")),
            "is_commercial": blob.get("is_commercial"),
            "transaction": _text(blob.get("transaction")),
        }

        return replace(
            summary,
            title=title,
            description=description or summary.description,
            offer_type="sale",
            property_type=property_type,
            market="primary" if market == "primary" else summary.market,
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price or summary.price_pln,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=summary.rooms,
            build_year=build_year,
            voivodeship=voivodeship or summary.voivodeship,
            county=summary.county,
            city=city or summary.city,
            district=district or summary.district,
            street=summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=summary.images,
            seller_type="developer",
            seller_name=_text(company.get("name")) or summary.seller_name,
            published_at=parse_datetime_ms(blob.get("added_at")) or summary.published_at,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing | None:
        external_id = _text(card.get("data-id"))
        url = _text(card.get("data-href"))
        if not external_id or not url:
            return None

        serialized = lxml_html.tostring(card, encoding="unicode")
        blob = _json_blob(serialized, f"GmgEventData_ProjectId_{external_id}") or {}

        price = parse_price(_low(blob.get("price")))
        area = parse_area(_low(blob.get("residenceArea")))
        rooms = parse_rooms(_low(blob.get("numberOfRooms")))
        price_per_m2 = parse_price(_low(blob.get("priceM2")))

        lat = _number(card.get("data-latitude"))
        lng = _number(card.get("data-longitude"))

        province = _text(blob.get("province"))
        city = _text(blob.get("city"))
        district = _text(blob.get("district"))
        street = _text(blob.get("street"))
        voivodeship = self.regions.canonical_voivodeship(province)

        images: list[str] = []
        for image in card.xpath(".//img[@data-lazy-load-url]/@data-lazy-load-url"):
            image_url = _text(image)
            if image_url and image_url not in images:
                images.append(image_url)
        if not images:
            holder = card.xpath(".//*[@data-image]/@data-image")
            if holder:
                images.append(holder[0])

        address_text = ", ".join(part for part in (street, city) if part) or None

        title = None
        names = card.xpath(".//*[@data-name]/@data-name")
        if names:
            title = _text(names[0])
        if title is None:
            headings = card.xpath(".//h2//text()")
            if headings:
                title = " ".join(" ".join(headings).split()) or None

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            offer_type="sale",
            property_type="flat",
            market="primary",
            price_kind="starting" if price is not None else "unknown",
            price_unit="total",
            price_pln=price,
            price_per_m2_pln=price_per_m2,
            area_m2=area,
            rooms=rooms,
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            seller_type="developer",
            seller_name=_text(blob.get("ownership")),
            extra={
                "noweinwestycje": {
                    "region_query": query.region,
                    "transaction_type": _text(blob.get("transactionType")),
                },
            },
        )

    @staticmethod
    def _coordinates(tree: Any) -> tuple[float | None, float | None]:
        nodes = tree.xpath('//div[contains(@class, "js-get-map")]')
        for node in nodes:
            lat = _number(node.get("data-latitude"))
            lng = _number(node.get("data-longitude"))
            if lat is not None and lng is not None:
                return lat, lng
        return None, None

    @staticmethod
    def _description(tree: Any) -> str | None:
        nodes = tree.xpath('//*[contains(@class, "b-complex-about-project__description")]')
        if nodes:
            text = strip_html(nodes[0].text_content())
            if text:
                return text
        return None

    @staticmethod
    def _heading(tree: Any) -> str | None:
        nodes = tree.xpath('//*[@data-test="SchemaName"]//text()')
        if nodes:
            text = " ".join(" ".join(nodes).split())
            return text or None
        return None

    @staticmethod
    def _price(tree: Any) -> int | None:
        nodes = tree.xpath('//*[@data-test="SchemaOffer"]')
        if not nodes:
            return None
        text = " ".join(nodes[0].text_content().split())
        match = re.search(r"od\s+([\d\s\u00a0]+)\s*zł", text)
        if match is None:
            return None
        return parse_price(match.group(1))

    @staticmethod
    def _district(location_tree: str | None, city: str | None) -> str | None:
        if not location_tree:
            return None
        parts = [part.strip() for part in location_tree.splitlines() if part.strip()]
        if city is not None:
            for part in parts:
                if part != city and part not in {"małopolskie", "śląskie"}:
                    return part
        return None
