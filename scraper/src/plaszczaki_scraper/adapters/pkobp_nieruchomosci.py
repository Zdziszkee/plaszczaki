"""PKO Bank Polski property portal adapter (JSON search API + detail pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from html import unescape
from urllib.parse import quote

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://nieruchomosci.pkobp.pl"
SEARCH_URL = f"{BASE_URL}/api/modules/property/search"
PAGE_SIZE = 100

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "grunty": "plot",
    "lokale mieszkalne": "flat",
    "mieszkania": "flat",
    "lokale uzytkowe": "commercial",
    "lokale uzytkowo biurowe": "commercial",
    "budynki uzytkowo biurowe": "commercial",
    "budynki": "commercial",
    "garaze": "garage",
}

_TRANSACTIONS: dict[str, OfferType] = {
    "sale": "sale",
    "rent": "rent",
}

_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END_RE = re.compile(r"</(?:p|div|li|tr|ul|ol|h[1-6])\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _absolute_url(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("http"):
        return value
    cleaned = value.lstrip("/")
    if "." in cleaned.split("/", 1)[0]:
        return f"https://{cleaned}"
    return f"{BASE_URL}/{cleaned}"


def _canonical_property_type(value: str | None) -> PropertyType:
    if not value:
        return "other"
    return _PROPERTY_TYPES.get(normalize_text(value), "other")


def _canonical_offer_type(value: str | None, fallback: OfferType) -> OfferType:
    if not value:
        return fallback
    return _TRANSACTIONS.get(str(value).strip().lower(), fallback)


def _rich_text(value: str | None) -> str | None:
    if not value:
        return None
    text = _BR_RE.sub("\n", value)
    text = _BLOCK_END_RE.sub("\n", text)
    text = unescape(_TAG_RE.sub(" ", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None


def _json_document(raw: str) -> object:
    """Decode either a bare JSON body or Scrapling's HTML-wrapped JSON."""
    text = raw.strip()
    if not text:
        raise ParseError("PKO BP response is empty")
    if not text.startswith(("{", "[")):
        match = _BODY_RE.search(text)
        text = (match.group(1) if match else text).strip()
        text = text.replace("&amp;", "&").replace("&#38;", "&")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"PKO BP response is not valid JSON: {exc}") from exc


def _next_data(html: str) -> dict[str, object]:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        raise ParseError("PKO BP page is missing __NEXT_DATA__")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"PKO BP __NEXT_DATA__ is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("PKO BP __NEXT_DATA__ is not an object")
    return payload


def _walk(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


class PkobpNieruchomosciAdapter:
    """Parse PKO BP's property search API and offer pages."""

    id = "pkobp-nieruchomosci"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for transaction, offer_type in (("sale", "sale"), ("rent", "rent")):
                url = (
                    f"{SEARCH_URL}?page_size={PAGE_SIZE}"
                    f"&voivodeship={quote(region, safe='')}&transaction_type={transaction}"
                )
                queries.append(
                    Query(
                        key=f"{region}:{transaction}",
                        url=url,
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _json_document(html)
        if not isinstance(payload, dict):
            raise ParseError("PKO BP search payload is not an object")

        results = payload.get("results")
        if not isinstance(results, list):
            raise ParseError("PKO BP search payload is missing results")

        items = [
            listing
            for result in results
            if isinstance(result, dict) and (listing := self._summary(result, query))
        ]
        count = safe_int(payload.get("count")) or len(items)
        total_pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=count,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        payload = _next_data(html)
        props = payload.get("props")
        page_props = props.get("pageProps") if isinstance(props, dict) else None
        data = page_props.get("data") if isinstance(page_props, dict) else None
        if not isinstance(data, dict):
            raise ParseError("PKO BP detail payload is missing page data")

        result: dict[str, object] | None = None
        for node in _walk(data):
            candidate = node.get("result")
            if node.get("type") == "property_details_snippet" and isinstance(candidate, dict):
                result = candidate
                break
        if result is None:
            raise ParseError("PKO BP detail page is missing the property snippet")

        description = self._description(result)
        offer_type = _canonical_offer_type(
            _text(result.get("transaction_type")),
            summary.offer_type,
        )
        property_type = _canonical_property_type(_text(result.get("property_type")))
        if property_type == "other":
            property_type = summary.property_type

        price = parse_price(result.get("price"))
        price_kind = "asking"
        if price is None:
            price_kind = "call"
        elif description and "cena wywolawcza" in normalize_text(description):
            price_kind = "starting"
        area = parse_area(result.get("area"))
        lat = safe_float(result.get("lat")) or summary.lat
        lng = safe_float(result.get("lng")) or summary.lng

        street = _text(result.get("street")) or summary.street
        number = _text(result.get("building_number"))
        apartment = _text(result.get("apartment_number"))
        city = _text(result.get("city")) or summary.city
        street_line = " ".join(part for part in (street, number) if part) or None
        if apartment:
            street_line = f"{street_line}/{apartment}" if street_line else apartment
        address_text = ", ".join(part for part in (city, street_line) if part) or None

        extra = dict(summary.extra)
        pkobp_extra = dict(extra.get("pkobp") or {})
        pkobp_extra.update(
            {
                "offer_deadline": _text(result.get("offer_deadline")),
                "previous_price": result.get("previous_price"),
                "leaseback": result.get("leaseback"),
                "leasable_area": result.get("leasable_area"),
                "is_archived": result.get("is_archived"),
            },
        )
        extra["pkobp"] = pkobp_extra

        return replace(
            summary,
            title=summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind=price_kind,
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=area if property_type != "plot" else summary.area_m2,
            land_area_m2=area if property_type == "plot" else summary.land_area_m2,
            voivodeship=self.regions.canonical_voivodeship(_text(result.get("voivodeship")))
            or summary.voivodeship,
            city=city,
            street=street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(result),
            seller_type="bank",
            seller_name="PKO Bank Polski",
            published_at=parse_datetime_ms(result.get("offer_date_created"))
            or summary.published_at,
            extra=extra,
        )

    def _summary(self, result: dict[str, object], query: Query) -> Listing | None:
        external_id = _text(result.get("uuid")) or _text(result.get("page_link"))
        page_link = _text(result.get("page_link"))
        if external_id is None or page_link is None:
            return None

        property_type = _canonical_property_type(_text(result.get("property_type")))
        offer_type = _canonical_offer_type(_text(result.get("transaction_type")), query.offer_type)

        street = _text(result.get("street"))
        number = _text(result.get("building_number"))
        apartment = _text(result.get("apartment_number"))
        city = _text(result.get("city"))
        street_line = " ".join(part for part in (street, number) if part) or None
        if apartment:
            street_line = f"{street_line}/{apartment}" if street_line else apartment
        address_text = ", ".join(part for part in (city, street_line) if part) or None
        title = ", ".join(
            part for part in (city, f"ul. {street_line}" if street_line else None) if part
        )

        price = parse_price(result.get("price"))
        area = parse_area(result.get("area"))
        lat = safe_float(result.get("lat"))
        lng = safe_float(result.get("lng"))

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=_absolute_url(page_link) or "",
            title=title or None,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="asking" if price is not None else "call",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=area if property_type != "plot" else None,
            land_area_m2=area if property_type == "plot" else None,
            voivodeship=self.regions.canonical_voivodeship(_text(result.get("voivodeship")))
            or query.region,
            city=city,
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(result),
            seller_type="bank",
            seller_name="PKO Bank Polski",
            published_at=parse_datetime_ms(result.get("offer_date_created")),
            extra={
                "pkobp": {
                    "offer_deadline": _text(result.get("offer_deadline")),
                    "previous_price": result.get("previous_price"),
                    "leaseback": result.get("leaseback"),
                    "leasable_area": result.get("leasable_area"),
                },
            },
        )

    @staticmethod
    def _description(result: dict[str, object]) -> str | None:
        accordions = result.get("accordions")
        if not isinstance(accordions, list):
            return None
        sections: list[str] = []
        for accordion in accordions:
            if not isinstance(accordion, dict):
                continue
            content = _rich_text(accordion.get("content"))
            title = _text(accordion.get("title"))
            if content is None or "rodo" in normalize_text(title or ""):
                continue
            sections.append(f"{title}\n{content}" if title else content)
        return "\n\n".join(sections) or None

    @staticmethod
    def _images(result: dict[str, object]) -> list[str]:
        images: list[str] = []
        photos = result.get("photos")
        if isinstance(photos, list):
            for photo in photos:
                if isinstance(photo, dict):
                    url = _absolute_url(_text(photo.get("url")))
                    if url and url not in images:
                        images.append(url)
        main = result.get("main_photo")
        if isinstance(main, dict):
            url = _absolute_url(_text(main.get("url")))
            if url and url not in images:
                images.insert(0, url)
        return images[:20]
