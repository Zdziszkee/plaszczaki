"""KOWR property sale adapter.

The KOWR offer search is a server-rendered HTML list with per-voivodeship
filters. ``location=88`` is małopolskie and ``location=94`` is śląskie; the
site uses one-based ``?page=N`` pagination and ten cards per page. Detail pages
expose the full location, price, area, auction date and offer number.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://www.nieruchomoscikowr.gov.pl"
SEARCH_PATH = "/nieruchomosci/oferty"
REGION_IDS = {"malopolskie": "88", "slaskie": "94"}

CARD_XPATH = '//div[contains(@class, "immovables-list-item")]'
TABLE_XPATH = './/dl[contains(@class, "table-immovables-list")]'


class KowrAdapter:
    """Parse KOWR's per-voivodeship offer list and detail pages."""

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.id = source_id
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            location = REGION_IDS.get(region)
            if location is None or not self.regions.has_region(region):
                continue
            queries.append(
                Query(
                    key=f"{region}:kowr",
                    url=f"{BASE_URL}{SEARCH_PATH}?{urlencode({'location': location, 'page': 1})}",
                    region=region,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def newest(self, query: Query) -> Query:
        """The list is already ordered by publication; keep the URL stable."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        params["page"] = str(max(1, page))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath(CARD_XPATH)
        if not cards and not tree.xpath('//*[contains(@class, "offers-list")]'):
            raise ParseError("KOWR search page has no offer list")

        items: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            hrefs = card.xpath('.//a[contains(@href, "/nieruchomosci/oferty/")]/@href')
            if not hrefs:
                continue
            url = urljoin(BASE_URL, hrefs[0])
            external_id = url.rstrip("/").split("/")[-1]
            if external_id in seen:
                continue
            seen.add(external_id)
            listing = self._summary(card, url, external_id, query)
            if listing is not None:
                items.append(listing)

        pages = [int(value) for value in re.findall(r"[?&]page=(\d+)", html)]
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=max(pages) if pages else 1,
            total_items=len(items) if not pages else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        text = " ".join(tree.text_content().split())

        price_raw = _between(text, "Cena wywoławcza:", ["Powierzchnia:"])
        price = _price(price_raw)
        area_text = _between(text, "Powierzchnia:", ["Typ nieruchomości:"])
        type_text = _between(text, "Typ nieruchomości:", ["Data przetargu:", "Atrybuty oferty"])
        distribution = _between(text, "Typ rozdysponowania:", ["Miejscowy Plan", "Numer działki:"])
        auction_text = _between(text, "Data przetargu:", ["Data publikacji:"])
        published_text = _between(text, "Data publikacji:", ["Data zakończenia publikacji:"])
        case_number = _between(text, "Numer oferty:", ["Oddział terenowy KOWR:"])
        branch = _between(text, "Oddział terenowy KOWR:", ["Typ rozdysponowania:"])
        parcel = _between(text, "Numer działki:", ["Lokalizacja", "Opis"])
        location = _between(
            text,
            "Lokalizacja (w zakresie gruntów Zasobu Własności Rolnej Skarbu Państwa):",
            ["Opis", "Dostępne załączniki"],
        )
        description = _between(text, "Opis", ["Dostępne załączniki", "Krajowy Ośrodek"])
        title = summary.title or ""

        voivodeship = summary.voivodeship
        county = summary.county
        city = summary.city
        street = summary.street
        if location:
            parts = [part.strip() for part in location.split(",") if part.strip()]
            if parts:
                voivodeship = self.regions.canonical_voivodeship(parts[0]) or voivodeship
            if len(parts) > 1:
                county = parts[1]
            if len(parts) > 2:
                city = parts[3] if len(parts) > 3 else parts[2]
            if len(parts) > 3:
                city = parts[3]

        title_location = _title_location(title)
        county = county or title_location.get("county")
        city = city or title_location.get("city")
        street = street or title_location.get("street")

        property_type = _property_type(f"{type_text or ''} {title}")
        offer_type = _offer_type(distribution, auction_text)
        area_m2, land_area_m2 = _area(area_text, property_type)
        address_text = ", ".join(part for part in (street, city) if part) or city

        return replace(
            summary,
            title=summary.title or title,
            description=description or summary.description,
            property_type=property_type,
            offer_type=offer_type,
            price_kind="starting" if price is not None else summary.price_kind,
            price_pln=price if price is not None else summary.price_pln,
            area_m2=area_m2 if area_m2 is not None else summary.area_m2,
            land_area_m2=land_area_m2 if land_area_m2 is not None else summary.land_area_m2,
            voivodeship=voivodeship,
            county=county,
            city=city,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            published_at=parse_datetime_ms(published_text) or summary.published_at,
            auction_date=parse_datetime_ms(auction_text) or summary.auction_date,
            case_number=case_number or summary.case_number,
            seller_name=branch or summary.seller_name,
            extra={
                **summary.extra,
                "kowr": {
                    **summary.extra.get("kowr", {}),
                    "distribution": distribution,
                    "parcel": parcel,
                    "branch": branch,
                    "price_raw": price_raw,
                },
            },
        )

    def _summary(self, card: Any, url: str, external_id: str, query: Query) -> Listing | None:
        title = _card_title(card)
        values = _card_values(card)
        price_raw = values.get("cena wywolawcza")
        price = _price(price_raw)
        area_text = values.get("powierzchnia")
        type_text = values.get("typ nieruchomosci")
        distribution = values.get("typ rozdysponowania")
        auction_text = values.get("data przetargu")
        property_type = _property_type(f"{type_text or ''} {title or ''}")
        area_m2, land_area_m2 = _area(area_text, property_type)
        location = _title_location(title or "")
        voivodeship = (
            query.region if self.regions.has_region(query.region) else location.get("voivodeship")
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            description=title,
            offer_type=_offer_type(distribution, auction_text),
            property_type=property_type,
            market="unknown",
            price_kind="starting" if price is not None else "call",
            price_unit="total",
            price_pln=price,
            area_m2=area_m2,
            land_area_m2=land_area_m2,
            voivodeship=voivodeship,
            county=location.get("county"),
            city=location.get("city"),
            street=location.get("street"),
            address_text=location.get("street") or location.get("city") or title,
            seller_type="government",
            seller_name="KOWR",
            published_at=None,
            auction_date=parse_datetime_ms(auction_text),
            extra={
                "kowr": {
                    "type": type_text,
                    "distribution": distribution,
                    "auction": auction_text,
                    "price_raw": price_raw,
                },
            },
        )


def _card_title(card: Any) -> str | None:
    hidden = " ".join(card.xpath('.//span[contains(@class, "wcag-hide")]/text()')).strip()
    if hidden:
        return hidden[2:].strip() if hidden.lower().startswith("o ") else hidden
    texts = [
        " ".join(link.text_content().split())
        for link in card.xpath('.//a[contains(@href, "/nieruchomosci/oferty/")]')
    ]
    for text in texts:
        if text and "więcej" not in text.lower():
            return text
    return None


def _card_values(card: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for table in card.xpath(TABLE_XPATH):
        labels = [" ".join(node.xpath(".//text()")).strip() for node in table.xpath("./dt")]
        entries = [" ".join(node.xpath(".//text()")).strip() for node in table.xpath("./dd")]
        for label, value in zip(labels, entries, strict=False):
            key = normalize_text(label.rstrip(":"))
            if key and value:
                values.setdefault(key, value)
    return values


def _price(value: str | None) -> int | None:
    if not value:
        return None
    lowered = value.lower()
    if "dt" in lowered:
        # Some land offers quote "1,50 dt" (price per decitonne), not a total.
        return None
    # KOWR writes "362 000,00 zł"; parse_price would fold the grosze into the
    # integer and multiply by 100.
    return parse_price(value.split(",")[0])


def _between(text: str, start: str, ends: list[str]) -> str | None:
    index = text.find(start)
    if index < 0:
        return None
    index += len(start)
    end = len(text)
    for marker in ends:
        found = text.find(marker, index)
        if found >= 0:
            end = min(end, found)
    value = text[index:end].strip(" :")
    return value or None


def _title_location(title: str) -> dict[str, str | None]:
    def grab(pattern: str) -> str | None:
        match = re.search(pattern, title, re.IGNORECASE)
        return match.group(1).strip() if match else None

    voivodeship = grab(r"województwo\s+([^,]+)")
    county = grab(r"powiat\s+([^,]+)")
    commune = grab(r"gmina\s+([^,]+)")
    obreb = grab(r"obręb\s+([^,]+)")
    street = grab(r"przy ul\.\s*([^,;]+)")
    return {
        "voivodeship": voivodeship,
        "county": county,
        "city": obreb or commune,
        "street": street,
    }


def _property_type(value: str) -> PropertyType:
    text = normalize_text(value)
    if "mieszk" in text or "lokal mieszkalny" in text:
        return "flat"
    if "dom" in text or "budynek mieszkalny" in text:
        return "house"
    if "garaz" in text:
        return "garage"
    if any(word in text for word in ("lokal", "budynek", "magazyn", "hala", "biur")):
        return "commercial"
    return "plot"


def _offer_type(distribution: str | None, auction: str | None) -> OfferType:
    text = normalize_text(distribution or "")
    if "dzierz" in text or "najem" in text:
        return "lease"
    if "sprzeda" in text:
        return "auction" if auction else "sale"
    return "auction" if auction else "sale"


def _area(value: str | None, property_type: PropertyType) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    match = re.search(r"([\d\s.,]+)\s*(ha|m2|m²|m 2)", value, re.IGNORECASE)
    if not match:
        return None, None
    amount = parse_area(match.group(1))
    if amount is None:
        return None, None
    if match.group(2).lower() == "ha":
        square_metres = round(amount * 10_000, 2)
        return (square_metres, None) if property_type != "plot" else (None, square_metres)
    return (amount, None) if property_type != "plot" else (None, amount)
