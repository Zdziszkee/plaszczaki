"""PGE property portal adapter.

PGE exposes a public geo JSON feed at ``datahub.gkpge.pl``. Each item already
has coordinates, an address object, price, area, purpose and a detail URL, so
there is no detail hydration. The feed is filtered to one voivodeship at a
time; only małopolskie (12) and śląskie (24) are requested.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import normalize_text, parse_datetime_ms
from plaszczaki_scraper.regions import RegionRegistry

PORTAL_URL = "https://nieruchomosci.gkpge.pl/strona-glowna"
API_URL = "https://datahub.gkpge.pl/api/geo/property"
REGION_CODES = {"malopolskie": "12", "slaskie": "24"}

SITE_LABELS = {
    "gkpge": "PGE",
    "pgegiek": "PGE Górnictwo i Energetyka Konwencjonalna",
    "pgedystrybucja": "PGE Dystrybucja",
    "pgeenergiaciepla": "PGE Energia Ciepła",
    "pgeeo": "PGE Energia Odnawialna",
}


class PgeAdapter:
    """Read the PGE geo feed for małopolskie and śląskie."""

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.id = source_id
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            code = REGION_CODES.get(region)
            if code is None or not self.regions.has_region(region):
                continue
            queries.append(
                Query(
                    key=f"{region}:pge",
                    url=(
                        f"{API_URL}?voivodeshipCode={code}&limit=1000"
                        f"&order%5B%5D=createdAt&sort=DESC"
                    ),
                    region=region,
                    offer_type="other",
                    property_type="other",
                ),
            )
        return queries

    def newest(self, query: Query) -> Query:
        return query

    def with_page(self, query: Query, page: int) -> Query:
        return replace(query, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        try:
            payload = json.loads(html)
        except json.JSONDecodeError as exc:
            raise ParseError(f"PGE feed is not JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ParseError("PGE feed is not an array")

        items: list[Listing] = []
        seen: set[str] = set()
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            listing = self._summary(raw, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=1,
            total_items=len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        return summary

    def _summary(self, raw: dict[str, Any], query: Query) -> Listing | None:
        address = raw.get("address")
        if not isinstance(address, dict):
            address = {}
        code = _text(address.get("voivodeship_code"))
        voivodeship = self.regions.canonical_voivodeship(_text(address.get("voivodeship")))
        if code in REGION_CODES.values():
            voivodeship = next(
                (region for region, value in REGION_CODES.items() if value == code),
                voivodeship,
            )
        if voivodeship is None:
            voivodeship = query.region
        if voivodeship not in query.region.split(","):
            return None

        external_id = _text(raw.get("external_id")) or _text(raw.get("id"))
        url = _text(raw.get("url"))
        if external_id is None or url is None:
            return None
        if url.startswith("//"):
            url = f"https:{url}"

        title = _text(raw.get("name"))
        purpose = normalize_text(_text(raw.get("purpose")) or "")
        offer_type = _offer_type(purpose, title or "")
        property_type = _property_type(_text(raw.get("type")), title or "")
        price = _price(raw.get("price")) or _price(raw.get("promo_price"))
        area = _number(raw.get("building_area"))
        land = _number(raw.get("plot_area"))
        if property_type == "plot" and land is None:
            land = area
            area = None
        street = _text(address.get("street"))
        street_no = _text(address.get("street_no"))
        if street and street_no:
            street = f"{street} {street_no}"
        city = _text(address.get("city"))
        site = _text(raw.get("siteaccess")) or "gkpge"
        media = raw.get("media")
        images = []
        if isinstance(media, list):
            for image in media:
                text = _text(image)
                if text:
                    images.append(f"https:{text}" if text.startswith("//") else text)

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            description=title,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if purpose == "rent" else "total",
            price_pln=price,
            area_m2=area,
            land_area_m2=land,
            voivodeship=voivodeship,
            county=_text(address.get("county")),
            city=city,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or city,
            lat=_number(raw.get("lat")),
            lng=_number(raw.get("lng")),
            geo_source="source"
            if raw.get("lat") is not None and raw.get("lng") is not None
            else None,
            images=images,
            seller_type="company",
            seller_name=SITE_LABELS.get(site, "PGE"),
            published_at=parse_datetime_ms(raw.get("created_at")),
            auction_date=parse_datetime_ms(raw.get("start_at")),
            extra={
                "pge": {
                    "siteaccess": site,
                    "purpose": raw.get("purpose"),
                    "type": raw.get("type"),
                    "parcel_number": raw.get("parcel_number"),
                    "previous_price": raw.get("previous_price"),
                    "is_negotiable": raw.get("is_negotiable"),
                },
            },
        )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _price(value: Any) -> int | None:
    number = _number(value)
    return round(number) if number is not None else None


def _offer_type(purpose: str, title: str) -> OfferType:
    text = normalize_text(title)
    if "przetarg" in text or "licytac" in text:
        return "auction"
    if purpose == "rent":
        return "rent"
    if purpose in {"lease", "dzierzawa"} or "dzierzaw" in text:
        return "lease"
    return "sale"


def _property_type(raw_type: str | None, title: str) -> PropertyType:
    value = normalize_text(f"{raw_type or ''} {title}")
    if "apartment" in value or "mieszk" in value or "lokal mieszkalny" in value:
        return "flat"
    if "house" in value or "dom" in value:
        return "house"
    if "garage" in value or "garaz" in value:
        return "garage"
    if "plot" in value or "land" in value or "dzialk" in value or "grunt" in value:
        return "plot"
    if "building" in value or "budynek" in value or "magazyn" in value or "hala" in value:
        return "commercial"
    return "other"
