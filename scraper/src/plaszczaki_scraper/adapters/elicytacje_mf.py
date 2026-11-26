"""eLicytacje MF (KAS) adapter.

The portal is an Angular app behind Imperva. After a browser wait the challenge
clears, and the app talks to a JSON API:

* ``POST /auction/api/v1/announcement/external`` lists announcements
* ``GET  /auction/announcements/{uuid}`` is the rendered detail page

The search request is POSTed through the browser context, so the Imperva cookies
are reused. Results are national; the runner keeps małopolskie/śląskie.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    parse_rooms,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://elicytacje.mf.gov.pl"
APP_URL = f"{BASE_URL}/auction/"
API_URL = f"{BASE_URL}/auction/api/v1/announcement/external"
PAGE_SIZE = 100

# Real-estate auction groups; movables (RUCH) and valuation notices (OSZ) are
# out of scope for this adapter.
AUCTION_GROUPS = (
    "LICELENIER",
    "LICNELNIER",
    "LICNELNIERPO",
    "LICELEPRWM",
    "LICNELPRWM",
)
VOIVODESHIPS = {"12": "malopolskie", "24": "slaskie"}
FILTERS = {"announcementTypeGroupCode": list(AUCTION_GROUPS)}


class ElicytacjeMfAdapter:
    """Search the eLicytacje KAS JSON API and parse rendered detail pages."""

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.id = source_id
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        if not any(self.regions.has_region(region) for region in regions):
            return []
        return [
            Query(
                key="national:aukcje-nieruchomosci",
                url=f"{API_URL}?{urlencode({'page': 0, 'size': PAGE_SIZE, 'sort': 'creationTimestamp,desc'})}",
                region=",".join(regions) or "national",
                offer_type="auction",
                property_type="other",
                method="POST",
                body=json.dumps({"filters": FILTERS}, ensure_ascii=False),
            ),
        ]

    def newest(self, query: Query) -> Query:
        """The API is already sorted by creation time, newest first."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["page"] = str(max(0, page - 1))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        try:
            payload = json.loads(html)
        except json.JSONDecodeError as exc:
            raise ParseError(f"eLicytacje search response is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ParseError("eLicytacje search response is not an object")

        raw_items = payload.get("content")
        if not isinstance(raw_items, list):
            raise ParseError("eLicytacje search response has no content array")

        items: list[Listing] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            listing = self._summary(raw, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        total_pages = payload.get("totalPages")
        total_items = payload.get("totalElements")
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages if isinstance(total_pages, int) else None,
            total_items=total_items if isinstance(total_items, int) else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        values = _labeled_values(tree)
        title = _first_text(tree.xpath("//h1")) or summary.title
        city = _get(values, "Miejscowość") or summary.city
        voivodeship = (
            self.regions.canonical_voivodeship(_get(values, "Województwo")) or summary.voivodeship
        )

        street_parts = [_clean(_get(values, "Ulica")), _clean(_get(values, "Numer domu"))]
        street = " ".join(part for part in street_parts if part) or summary.street
        flat = _clean(_get(values, "Numer lokalu"))
        address_text = ", ".join(
            part
            for part in (
                f"{street}/{flat}" if street and flat else street,
                city,
            )
            if part
        )
        area = parse_area(_get(values, "Powierzchnia użytkowa (m²)"))
        rooms = parse_rooms(_get(values, "Liczba pokoi"))
        price = parse_price(_get(values, "Cena wywoławcza"))
        case_number = _get(values, "Nr ogłoszenia") or summary.case_number
        description = _description(tree) or summary.description
        property_type = _property_type(f"{title or ''} {_get(values, 'Nazwa katalogowa') or ''}")

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            property_type=property_type,
            price_pln=price if price is not None else summary.price_pln,
            price_kind="starting"
            if (price is not None or summary.price_pln is not None)
            else "call",
            area_m2=area if area is not None else summary.area_m2,
            rooms=rooms if rooms is not None else summary.rooms,
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=address_text or summary.address_text,
            case_number=case_number,
            seller_name=_find(values, "Urząd Skarbowy") or summary.seller_name,
            auction_date=_detail_date(_get(values, "Data rozpoczęcia")) or summary.auction_date,
        )

    def _summary(self, raw: dict[str, Any], query: Query) -> Listing | None:
        uuid = _text(raw.get("uuid"))
        if uuid is None:
            return None
        codes = [str(code) for code in raw.get("announcementTypeCodes") or []]
        if not _is_real_estate(codes):
            return None
        if any(code.startswith("OSZ") for code in codes):
            return None

        city = _text(raw.get("localization"))
        code = _text(raw.get("localizationVoivodeshipCode"))
        voivodeship = VOIVODESHIPS.get(code or "")
        if voivodeship is None:
            voivodeship = self.regions.match_city(city)
        if voivodeship is None or voivodeship not in query.region.split(","):
            return None

        title = _text(raw.get("title"))
        price = _number(raw.get("startingPrice"))
        picture = _text(raw.get("pictureId"))
        return Listing(
            source_id=self.source_id,
            external_id=uuid,
            url=f"{APP_URL}announcements/{uuid}",
            title=title,
            offer_type="auction",
            property_type=_property_type(title or ""),
            market="unknown",
            price_kind="starting" if price is not None else "call",
            price_unit="total",
            price_pln=price,
            voivodeship=voivodeship,
            city=city,
            seller_type="government",
            seller_name="KAS",
            published_at=parse_datetime_ms(raw.get("publicationDateTime")),
            auction_date=parse_datetime_ms(raw.get("saleBeginDateTime")),
            images=[f"{BASE_URL}/auction/api/v1/items/pictures/{picture}/thumbnail"]
            if picture
            else [],
            extra={
                "elicytacje_mf": {
                    "electronicStateCode": raw.get("electronicStateCode"),
                    "announcementTypeCodes": codes,
                    "saleBeginDateTime": raw.get("saleBeginDateTime"),
                    "saleEndDateTime": raw.get("saleEndDateTime"),
                    "localizationVoivodeshipCode": code,
                },
            },
        )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _first_text(nodes: list[Any]) -> str | None:
    for node in nodes:
        text = " ".join(node.text_content().split())
        if text:
            return text
    return None


def _number(value: Any) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(value)
    return None


def _is_real_estate(codes: list[str]) -> bool:
    return any("NIER" in code or "PRWM" in code for code in codes)


def _property_type(value: str) -> PropertyType:
    text = normalize_text(value)
    if any(word in text for word in ("mieszkan", "lokal mieszkalny", "apartament")):
        return "flat"
    if any(word in text for word in ("dom", "willa", "budynek mieszkalny")):
        return "house"
    if any(word in text for word in ("działk", "grunt", "ziem")):
        return "plot"
    if any(word in text for word in ("garaż", "garaz")):
        return "garage"
    if any(
        word in text for word in ("lokal użytkowy", "lokal uzytkowy", "magazyn", "hala", "biur")
    ):
        return "commercial"
    return "other"


def _get(values: dict[str, str], label: str) -> str | None:
    return values.get(normalize_text(label))


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    tokens = [token for token in value.split() if token != "--"]
    text = " ".join(tokens).strip()
    if not text or set(text) <= {"-"}:
        return None
    return text


def _find(values: dict[str, str], prefix: str) -> str | None:
    key = normalize_text(prefix)
    for label, value in values.items():
        if label.startswith(key):
            return value
    return None


def _labeled_values(tree: lxml_html.HtmlElement) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in tree.xpath('//div[contains(@class, "line-height-1")]'):
        label = normalize_text(
            " ".join(node.xpath('.//span[contains(@class, "gray-n-600")]/text()'))
        )
        value = " ".join(node.xpath('.//span[contains(@class, "value")]/text()')).strip()
        if label and value:
            values.setdefault(label.rstrip(":"), value)
    return values


def _description(tree: lxml_html.HtmlElement) -> str | None:
    parts = tree.xpath("//jsp-parameters//text() | //jsp-additional-info//text()")
    text = normalize_text(" ".join(parts))
    return text[:6000] or None


def _detail_date(value: str | None) -> int | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y, %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
        return int(parsed.timestamp() * 1000)
    return parse_datetime_ms(value)
