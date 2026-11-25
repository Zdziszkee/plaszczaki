"""Tauron Nieruchomości adapter (regional offer list + detail pages)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    safe_float,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://nieruchomosci.tauron.pl/lista"
SITE_ROOT = "https://nieruchomosci.tauron.pl"

# (site filter label, canonical offer type, query key suffix)
AD_TYPES: tuple[tuple[str, OfferType, str], ...] = (
    ("sprzedaż", "sale", "sprzedaz"),
    ("wynajem/dzierżawa", "lease", "wynajem"),
)

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "gruntowa niezabudowana": "plot",
    "gruntowa zabudowana": "plot",
    "lokal mieszkalny": "flat",
    "lokal uzytkowy": "commercial",
    "garaz": "garage",
    "magazyn": "commercial",
    "budynek": "commercial",
}

_OFFER_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "wynajem dzierzawa": "lease",
    "wynajem": "lease",
    "najem": "lease",
    "dzierzawa": "lease",
}

_POSTAL_CITY_RE = re.compile(r"^\s*(\d{2}-\d{3})\s+(.+)$")
_PROPERTY_ID_RE = re.compile(r"propertyId=([^&]+)")
_DMS_RE = re.compile(r"(\d+)\D+(\d+)\D+(\d+(?:[.,]\d+)?)")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?")
_DECIMALS_RE = re.compile(r",\d{2}(?!\d)")

WARSAW = ZoneInfo("Europe/Warsaw")


def _price(value: str | None) -> int | None:
    """Parse whole-złoty prices that carry a ",00" decimal suffix."""
    if not value:
        return None
    return parse_price(_DECIMALS_RE.sub("", value))


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _canonical_property_type(value: str | None) -> PropertyType:
    if not value:
        return "other"
    return _PROPERTY_TYPES.get(normalize_text(value), "other")


def _canonical_offer_type(value: str | None, fallback: OfferType) -> OfferType:
    if not value:
        return fallback
    return _OFFER_TYPES.get(normalize_text(value), fallback)


def _date_ms(value: str | None) -> int | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if match is None:
        return None
    day, month, year, hour, minute = match.groups()
    parsed = datetime(
        int(year),
        int(month),
        int(day),
        int(hour or 0),
        int(minute or 0),
        tzinfo=WARSAW,
    )
    return int(parsed.timestamp() * 1000)


def _drop_page_param(url: str) -> str:
    parts = urlsplit(url)
    query = [item for item in parts.query.split("&") if item and item.split("=", 1)[0] != "page"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), parts.fragment))


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Tauron page is not valid HTML: {exc}") from exc


class TauronAdapter:
    """Parse TAURON's regional sale/lease listing and detail pages."""

    id = "tauron"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            province = (
                self.regions.region(region).name if self.regions.has_region(region) else region
            )
            for label, offer_type, key in AD_TYPES:
                url = f"{BASE_URL}?ad_type={quote(label, safe='')}&province={quote(province, safe='')}"
                queries.append(
                    Query(
                        key=f"{region}:{key}",
                        url=url,
                        region=region,
                        offer_type=offer_type,
                        property_type="other",
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """Tauron defaults to newest-first but the sort is also explicit."""
        return query.with_param("sort", "publicationdate").with_param("asc", "true")

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, page=page, url=_drop_page_param(query.url))
        # The site paginates from zero while Query is one-based.
        paged = query.with_page(page - 1)
        return replace(paged, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        if not tree.xpath('//*[contains(@class,"property__wrapper")]'):
            raise ParseError("Tauron search page layout not recognised")

        items: list[Listing] = []
        for node in tree.xpath('//div[@class="property__list--item"]'):
            listing = self._summary(node, query)
            if listing is not None:
                items.append(listing)

        pager_pages = {
            int(match.group(1))
            for href in tree.xpath("//a/@href")
            if (match := re.search(r"[?&]page=(\d+)", href))
        }
        total_pages = max(pager_pages) + 1 if pager_pages else 1
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=len(items) if total_pages == 1 else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        details = self._detail_pairs(tree)

        title = _text(tree.xpath("//h1")[0].text_content()) if tree.xpath("//h1") else None
        description = self._description(tree)

        offer_type = _canonical_offer_type(
            details.get("typ ogloszenia"),
            summary.offer_type,
        )
        property_type = _canonical_property_type(
            details.get("rodzaj nieruchomosci"),
        )
        if property_type == "other":
            property_type = summary.property_type

        price = _price(details.get("cena"))
        price_kind = "asking"
        if price is None:
            price_kind = "call"
        elif "wywolawcza" in normalize_text(details.get("cena") or ""):
            price_kind = "starting"

        street = details.get("ulica")
        number = details.get("numer")
        postal = details.get("kod pocztowy")
        city = details.get("miejscowosc")
        street_line = " ".join(part for part in (street, number) if part) or None
        address_text = (
            ", ".join(
                part
                for part in (street_line, " ".join(part for part in (postal, city) if part) or None)
                if part
            )
            or None
        )

        lat, lng = self._coordinates(tree, details)
        auction_date = _date_ms(details.get("data skladania ofert"))
        if auction_date is None:
            body = _text(tree.text_content()) or ""
            match = re.search(
                r"Termin złożenia oferty\s+(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)", body
            )
            auction_date = _date_ms(match.group(1)) if match else None

        extra = dict(summary.extra)
        tauron_extra = dict(extra.get("tauron") or {})
        tauron_extra.update(
            {
                "tryb_postepowania": details.get("tryb postepowania"),
                "przeznaczenie": details.get(
                    "przeznaczenie nieruchomosci w planach zagospodarowania przestrzennego"
                ),
                "kod_pocztowy": postal,
                "numer": number,
                "data_skladania_ofert": details.get("data skladania ofert"),
            },
        )
        extra["tauron"] = tauron_extra

        return replace(
            summary,
            url=summary.url,
            title=title or summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind=price_kind,
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=parse_area(details.get("powierzchnia budynku")),
            land_area_m2=parse_area(details.get("powierzchnia gruntu")),
            voivodeship=self.regions.canonical_voivodeship(details.get("wojewodztwo"))
            or summary.voivodeship,
            city=city or summary.city,
            street=street or summary.street,
            address_text=address_text or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(tree),
            seller_type="utility",
            seller_name="TAURON",
            auction_date=auction_date,
            extra=extra,
        )

    def _summary(self, node: lxml_html.HtmlElement, query: Query) -> Listing | None:
        hrefs = node.xpath('.//a[contains(@href,"szczegoly")]/@href')
        if not hrefs:
            return None
        match = _PROPERTY_ID_RE.search(hrefs[0])
        if match is None:
            return None

        external_id = match.group(1).replace("%7B", "").replace("%7D", "").strip("{}")
        url = f"{SITE_ROOT}{hrefs[0]}".split("&prevPage=", 1)[0]

        title = _text(" ".join(node.xpath('.//*[@class="property__list--item--name"]/text()')))
        address = _text(" ".join(node.xpath('.//*[@class="property__list--item--adress"]/text()')))
        postal = city = None
        if address:
            parsed = _POSTAL_CITY_RE.match(address)
            if parsed:
                postal, city = parsed.group(1), parsed.group(2)
            else:
                city = address

        details = self._item_details(node)
        offer_type = _canonical_offer_type(details.get("typ ogloszenia"), query.offer_type)
        property_type = _canonical_property_type(details.get("rodzaj nieruchomosci"))

        price_node = node.xpath('.//*[@class="property__list--item--price"]')
        price_text = _text(price_node[0].text_content()) if price_node else None
        price = _price(price_text)
        price_kind = "call" if price is None else "asking"

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            price_kind=price_kind,
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=parse_area(details.get("powierzchnia budynku")),
            land_area_m2=parse_area(details.get("powierzchnia gruntu")),
            voivodeship=query.region,
            city=city,
            address_text=address,
            seller_type="utility",
            seller_name="TAURON",
            auction_date=_date_ms(details.get("data skladania ofert")),
            extra={
                "tauron": {
                    "kod_pocztowy": postal,
                    "data_skladania_ofert": details.get("data skladania ofert"),
                },
            },
        )

    @staticmethod
    def _item_details(node: lxml_html.HtmlElement) -> dict[str, str]:
        details: dict[str, str] = {}
        for paragraph in node.xpath('.//p[@class="property__list--item--detail"]'):
            labels = paragraph.xpath('.//*[contains(@class,"detail--label")]/text()')
            values = paragraph.xpath('.//*[contains(@class,"detail--text")]/text()')
            label = normalize_text(" ".join(labels).rstrip(":"))
            value = _text(" ".join(values))
            if label and value:
                details[label] = value
        return details

    @staticmethod
    def _detail_pairs(tree: lxml_html.HtmlElement) -> dict[str, str]:
        details: dict[str, str] = {}
        for row in tree.xpath('//*[contains(@class,"property__tabledetail")]'):
            labels = row.xpath('.//*[contains(@class,"tabledetail--label")]/text()')
            values = row.xpath('.//*[contains(@class,"tabledetail--info")]/text()')
            label = normalize_text(" ".join(labels))
            value = _text(" ".join(values))
            if label and value and label not in details:
                details[label] = value
        return details

    @staticmethod
    def _description(tree: lxml_html.HtmlElement) -> str | None:
        headings = tree.xpath('//h2[normalize-space()="Opis nieruchomości"]')
        if not headings:
            return None
        paragraphs = headings[0].getparent().xpath(".//p")
        text = "\n\n".join(value for p in paragraphs if (value := _text(p.text_content())))
        return text or None

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for src in tree.xpath(
            '//div[contains(@class,"gallery__slide--big")]//img/@src'
            ' | //div[contains(@class,"gallery__slide--big")]//img/@data-src'
        ):
            if "/-/media/realestate/uploads/" not in src:
                continue
            url = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if url not in images:
                images.append(url)
        return images[:20]

    def _coordinates(
        self, tree: lxml_html.HtmlElement, details: dict[str, str]
    ) -> tuple[float | None, float | None]:
        nodes = tree.xpath("//*[@data-latitude and @data-longitude]")
        if nodes:
            lat = safe_float(nodes[0].get("data-latitude"))
            lng = safe_float(nodes[0].get("data-longitude"))
            if lat is not None and lng is not None and (lat != 0.0 or lng != 0.0):
                return lat, lng

        lat = self._dms(details.get("szerokosc geograficzna"), positive="N")
        lng = self._dms(details.get("dlugosc geograficzna"), positive="E")
        return lat, lng

    @staticmethod
    def _dms(value: str | None, *, positive: str) -> float | None:
        if not value:
            return None
        match = _DMS_RE.search(value)
        if match is None:
            return None
        degrees, minutes, seconds = (float(part.replace(",", ".")) for part in match.groups())
        result = degrees + minutes / 60 + seconds / 3600
        upper = value.strip().upper()
        if positive == "N" and upper.endswith("S"):
            result = -result
        if positive == "E" and upper.endswith("W"):
            result = -result
        return round(result, 6)
