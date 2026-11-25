"""SRK (Spółka Restrukturyzacji Kopalń S.A.) property adapter.

SRK is a state-owned company selling former mining land, buildings and flats,
almost exclusively in the Śląskie voivodeship.  The public offer area has two
server-rendered shapes:

* tender lists (``przetargi-*``) are tables with a title, a location and an
  auction date/time, paginated with ``?strona=N``;
* the sale catalogue (``oferty-nieruchomosci``) is a card grid that also
  carries the price and the plot/usable area.

Neither the lists nor the detail pages publish coordinates, so ``lat``/``lng``
stay empty and the downstream geocoder resolves the address.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PriceKind, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

SITE_ROOT = "https://srk.com.pl"
OFFER_PATH = "/oferta"
WARSAW = ZoneInfo("Europe/Warsaw")
# SRK publishes Śląskie assets only; the adapter never emits another region.
SRK_REGION = "slaskie"
SELLER_NAME = "Spółka Restrukturyzacji Kopalń S.A."

# (slug, offer_type, property_type fallback for rows without a clear title)
CATEGORIES: tuple[tuple[str, OfferType, PropertyType], ...] = (
    ("przetargi-nieruchomosci", "sale", "other"),
    ("przetargi-mieszkania", "sale", "flat"),
    ("przetargi-najem-dzierzawa", "lease", "other"),
    ("oferty-nieruchomosci", "sale", "other"),
)

_PAGE_RE = re.compile(r"[?&]strona=(\d+)")
_DATE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
)
_TOTAL_RE = re.compile(r"liczba\s+(?:przetargów|ofert)\s*:\s*(\d+)", re.IGNORECASE)
_AREA_RE = re.compile(
    r"o\s+powierzchni\s+(?:u[żz]ytkowej\s+)?([\d][\d\s\u00a0.,]*)\s*(ha|m\s*2|m²)",
    re.IGNORECASE,
)
_PRICE_DECIMALS_RE = re.compile(r"\.\d{2}(?!\d)")
_PARCEL_RE = re.compile(r"\s+dzia[łl]k\w*.*$", re.IGNORECASE)
_LOCATION_RE = re.compile(
    r"^(?P<city>.+?)[,\s]+(?:w\s+rejonie\s+|rejon(?:ie)?\s+)?(?:przy\s+)?"
    r"(?:ul\.|al\.|pl\.|os\.)\s*(?P<street>.+)$",
    re.IGNORECASE,
)
_STREET_NUMBER_RE = re.compile(
    r"^(?P<name>.+?)\s+(?:nr\.?\s*)?(?P<number>\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?)$",
)
_TITLE_STREET_RE = re.compile(
    r"(?:przy\s+|rejon(?:ie)?\s+)?(?:ul\.|al\.|pl\.|os\.)\s*(?P<street>[^,;]+)",
    re.IGNORECASE,
)

# Ordered markers; the first normalised marker found in the title wins.
_PROPERTY_TYPE_MARKERS: tuple[tuple[str, PropertyType], ...] = (
    ("garaz", "garage"),
    ("lokal mieszkalny", "flat"),
    ("lokalu mieszkalnego", "flat"),
    ("mieszkanie", "flat"),
    ("mieszkania", "flat"),
    ("lokal uzytkowy", "commercial"),
    ("lokalu uzytkowego", "commercial"),
    ("lokal uslugowy", "commercial"),
    ("budynek przemyslowy", "commercial"),
    ("budynki przemyslowe", "commercial"),
    ("hala", "commercial"),
    ("magazyn", "commercial"),
    ("biurow", "commercial"),
    ("dom", "house"),
    ("budynek mieszkalny", "house"),
    ("dzialk", "plot"),
    ("grunt", "plot"),
    ("teren", "plot"),
    ("szyb", "plot"),
)

_OFFER_TYPE_MARKERS: tuple[tuple[str, OfferType], ...] = (
    ("najem", "lease"),
    ("dzierzaw", "lease"),
    ("wynajem", "rent"),
    ("sprzedaz", "sale"),
    ("przetarg", "sale"),
    ("licytacj", "sale"),
)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _first_text(nodes: Sequence[Any]) -> str | None:
    for node in nodes:
        raw = node.text_content() if isinstance(node, lxml_html.HtmlElement) else node
        text = _text(raw)
        if text:
            return text
    return None


def _tree(html: str) -> lxml_html.HtmlElement:
    if not html or not html.strip():
        raise ParseError("empty page")
    try:
        return lxml_html.fromstring(html)
    except Exception as exc:  # lxml raises etree.ParserError, which ships no stubs
        raise ParseError(f"SRK page is not valid HTML: {exc}") from exc


def _date_ms(date_text: str | None, time_text: str | None = None) -> int | None:
    """Parse the site's ISO date (plus optional time) as Europe/Warsaw."""
    if not date_text:
        return None
    combined = date_text.strip()
    if time_text and ":" in time_text and ":" not in combined:
        combined = f"{combined} {time_text.strip()}"

    match = _DATE_RE.search(combined)
    if match is None:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=WARSAW,
        )
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _price_from_text(value: str | None) -> int | None:
    """Strip the ".00" grosze suffix before handing the value to parse_price."""
    if not value:
        return None
    return parse_price(_PRICE_DECIMALS_RE.sub("", value))


def _property_type(title: str | None, fallback: PropertyType) -> PropertyType:
    normalized = normalize_text(title or "")
    if not normalized:
        return fallback
    for marker, property_type in _PROPERTY_TYPE_MARKERS:
        if marker in normalized:
            return property_type
    if "nieruchomosc" in normalized or re.search(r"\bha\b", normalized):
        return "plot"
    return fallback


def _offer_type(title: str | None, fallback: OfferType) -> OfferType:
    normalized = normalize_text(title or "")
    for marker, offer_type in _OFFER_TYPE_MARKERS:
        if marker in normalized:
            return offer_type
    return fallback


def _area_fields(
    text: str | None,
    property_type: PropertyType,
) -> tuple[float | None, float | None]:
    """Return ``(area_m2, land_area_m2)`` from an "o powierzchni ..." phrase."""
    if not text:
        return None, None
    match = _AREA_RE.search(text)
    if match is None:
        return None, None
    value = parse_area(f"{match.group(1)} {match.group(2)}")
    if value is None:
        return None, None
    if normalize_text(match.group(2)).startswith("ha") or property_type == "plot":
        return None, value
    return value, None


def _assign_area(
    value: float | None,
    property_type: PropertyType,
) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    return (None, value) if property_type == "plot" else (value, None)


def _clean_city(value: str | None) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = re.sub(r"^(?:miasto|gmina)\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.") or None


def _clean_street(value: str | None) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = _PARCEL_RE.sub("", text)
    text = re.split(r"\s+o\s+powierzchni", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return text.strip(" ,.") or None


def _street_name(value: str | None) -> str | None:
    """Drop the building number so the geocoder receives a clean street."""
    text = _clean_street(value)
    if not text:
        return None
    number_match = _STREET_NUMBER_RE.match(text)
    if number_match is not None:
        return number_match.group("name").strip(" ,.") or None
    return text


def _split_location(
    value: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Split "Katowice ul. Górniczego Dorobku" into city, street and address."""
    text = _text(value)
    if not text:
        return None, None, None

    match = _LOCATION_RE.match(text)
    if match is not None:
        city = match.group("city")
        street_full = match.group("street")
    elif "," in text:
        city, street_full = (part.strip() for part in text.split(",", 1))
    else:
        city, street_full = text, None

    city = _clean_city(city)
    street_full = _clean_street(street_full)
    if not street_full:
        return city, None, city

    address = ", ".join(part for part in (street_full, city) if part)
    return city, _street_name(street_full), address or None


def _street_from_title(title: str | None) -> str | None:
    match = _TITLE_STREET_RE.search(title or "")
    if match is None:
        return None
    return _clean_street(match.group("street"))


def _title_city(value: str | None) -> str | None:
    """Accept a title-derived city only when it looks like a place name."""
    city = _clean_city(value)
    if not city:
        return None
    normalized = f" {normalize_text(city)} "
    if len(city) > 30 or " w " in normalized:
        return None
    return city


class SrkAdapter:
    """Parse SRK's Śląskie tender tables, sale cards and detail pages."""

    id = "srk"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # SRK only publishes Śląskie assets, so other requested regions are
        # dropped instead of producing empty crawls.
        wanted = any(self.regions.canonical_voivodeship(region) == SRK_REGION for region in regions)
        if not wanted:
            return []
        return [
            Query(
                key=f"{SRK_REGION}:{slug}",
                url=f"{SITE_ROOT}{OFFER_PATH}/{slug}",
                region=SRK_REGION,
                offer_type=offer_type,
                property_type=property_type,
            )
            for slug, offer_type, property_type in CATEGORIES
        ]

    def with_page(self, query: Query, page: int) -> Query:
        # The site paginates with the 1-based ``?strona=N`` query parameter and
        # omits it on the first page.
        page = max(page, 1)
        parts = urlsplit(query.url)
        params = [
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if name != "strona"
        ]
        if page > 1:
            params.append(("strona", str(page)))
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment),
        )
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        items: list[Listing] = []
        seen: set[str] = set()

        rows = tree.xpath(
            '//table[contains(@class, "przetargi__lista")]//tr[contains(@class, "przetarg__item")]',
        )
        for row in rows:
            listing = self._table_item(row, query)
            if listing is not None and listing.external_id not in seen:
                seen.add(listing.external_id)
                items.append(listing)

        cards = tree.xpath(
            '//div[@id="oferty--lista"]//div[contains(@class, "oferta__item")]',
        )
        for card in cards:
            listing = self._card_item(card, query)
            if listing is not None and listing.external_id not in seen:
                seen.add(listing.external_id)
                items.append(listing)

        if not items and not rows and not cards and not _empty_page(tree):
            raise ParseError("SRK page has no tender list")

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=_total_pages(tree, query.page),
            total_items=_total_items(tree),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        # Detail pages are an enrichment only: a broken or empty page must not
        # drop an otherwise valid summary.
        try:
            tree = lxml_html.fromstring(html)
        except Exception:  # lxml raises etree.ParserError, which ships no stubs
            return summary
        if not tree.xpath('//div[@id="tresc"]') and not tree.xpath("//h1"):
            return summary

        title = (
            _first_text(
                tree.xpath('//*[@id="przetarg_tytul" or @id="oferta_tytul"]'),
            )
            or summary.title
        )

        fields = _detail_fields(tree)
        place = fields.get("miejsce przetargu")
        location = fields.get("lokalizacja przedmiotu przetargu") or _pin_location(tree)
        description = _description(tree)
        price = _detail_price(tree) or summary.price_pln

        offer_type = _offer_type(title, summary.offer_type)
        property_type = _property_type(title, summary.property_type)
        area_m2, land_area_m2 = _area_fields(
            " ".join(part for part in (title, description) if part),
            property_type,
        )
        area_value = _detail_area(tree)
        detail_area, detail_land = _assign_area(area_value, property_type)

        city, street, address_text = _split_location(location)
        city = city or summary.city
        street = street or summary.street
        address_text = address_text or summary.address_text
        files = _files(tree)
        images = _images(tree)

        extra = dict(summary.extra)
        srk_extra = dict(extra.get("srk") or {})
        if place:
            srk_extra["place"] = place
        if fields.get("data dodania"):
            srk_extra["added_on"] = fields["data dodania"]
        if files:
            srk_extra["files"] = files
        extra["srk"] = srk_extra

        return replace(
            summary,
            title=title or summary.title,
            description=description or summary.description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind=_price_kind(summary, price),
            price_pln=price,
            area_m2=area_m2 or detail_area or summary.area_m2,
            land_area_m2=land_area_m2 or detail_land or summary.land_area_m2,
            voivodeship=SRK_REGION,
            city=city,
            street=street,
            address_text=address_text,
            lat=None,
            lng=None,
            geo_source=None,
            images=images or summary.images,
            auction_date=(_date_ms(fields.get("data przetargu")) or summary.auction_date),
            published_at=_date_ms(fields.get("data dodania")) or summary.published_at,
            extra=extra,
        )

    def _table_item(self, row: lxml_html.HtmlElement, query: Query) -> Listing | None:
        cells = row.xpath("./td")
        if not cells:
            return None
        title = _first_text(cells[0].xpath(".//strong")) or _text(cells[0])
        if not title:
            return None

        location = _first_text([cells[1]]) if len(cells) > 1 else None
        href = None
        time_label = None
        auction_date = None
        if len(cells) > 2:
            date_cell = cells[2]
            hrefs = date_cell.xpath(".//a/@href")
            href = _text(hrefs[0]) if hrefs else None
            time_label = _first_text(
                date_cell.xpath('.//span[contains(@class, "przetarg__godzina")]'),
            )
            auction_date = _date_ms(
                _first_text(date_cell.xpath('.//span[contains(@class, "przetarg__data")]')),
                time_label,
            )

        property_type = _property_type(title, query.property_type)
        area_m2, land_area_m2 = _area_fields(title, property_type)
        url = urljoin(SITE_ROOT, href) if href else f"{SITE_ROOT}{OFFER_PATH}"

        return self._listing(
            query,
            external_id=_external_id(href, query, title, auction_date),
            url=url,
            title=title,
            location=location,
            offer_type=_offer_type(title, query.offer_type),
            property_type=property_type,
            auction_date=auction_date,
            area_m2=area_m2,
            land_area_m2=land_area_m2,
            price=None,
            price_kind="call",
            extra={
                "category": _slug(query),
                "location": location,
                "auction_time": time_label,
            },
        )

    def _card_item(self, card: lxml_html.HtmlElement, query: Query) -> Listing | None:
        hrefs = card.xpath('.//h3[contains(@class, "oferta__tytul")]//a/@href')
        if not hrefs:
            hrefs = card.xpath('.//a[contains(@href, "/oferta/")]/@href')
        href = _text(hrefs[0]) if hrefs else None
        title = _first_text(
            card.xpath('.//h3[contains(@class, "oferta__tytul")]'),
        )
        if not title or not href:
            return None

        price = _price_from_text(
            _first_text(card.xpath('.//dl[contains(@class, "oferta__cena")]//dd[1]')),
        )
        area_value = parse_area(
            _first_text(card.xpath('.//dl[contains(@class, "oferta__metrarz")]//dd[1]')),
        )

        kind = _first_text(card.xpath('.//dd[contains(@class, "oferta__rodzaj")]'))
        offer_type = _offer_type(kind, query.offer_type) if kind else query.offer_type
        property_type = _property_type(title, query.property_type)
        area_m2, land_area_m2 = _assign_area(area_value, property_type)

        # Card titles mix a place name with prose, so prefer the card's own
        # gmina/powiat and only fall back to a short title-derived city.
        parsed_city, parsed_street, _ = _split_location(title)
        city = _card_city(card) or _title_city(parsed_city)
        street_full = _street_from_title(title) or parsed_street
        street = _street_name(street_full)
        address_text = ", ".join(part for part in (street_full, city) if part) or None
        images: list[str] = []
        for src in card.xpath('.//div[contains(@class, "oferta__foto")]//img/@src'):
            source = _text(src)
            if not source:
                continue
            image = urljoin(SITE_ROOT, source)
            if image not in images:
                images.append(image)

        return self._listing(
            query,
            external_id=_external_id(href, query, title, None),
            url=urljoin(SITE_ROOT, href),
            title=title,
            location=None,
            city=city,
            street=street,
            address_text=address_text,
            offer_type=offer_type,
            property_type=property_type,
            area_m2=area_m2,
            land_area_m2=land_area_m2,
            price=price,
            price_kind="starting" if normalize_text(kind or "") == "przetarg" else "asking",
            images=images[:20],
            extra={
                "category": _slug(query),
                "location": address_text,
                "offer_kind": kind,
            },
        )

    def _listing(
        self,
        query: Query,
        *,
        external_id: str,
        url: str,
        title: str,
        offer_type: OfferType,
        property_type: PropertyType,
        location: str | None = None,
        city: str | None = None,
        street: str | None = None,
        address_text: str | None = None,
        auction_date: int | None = None,
        area_m2: float | None = None,
        land_area_m2: float | None = None,
        price: int | None = None,
        price_kind: PriceKind = "call",
        images: Sequence[str] | None = None,
        extra: dict[str, object] | None = None,
    ) -> Listing:
        parsed_city, parsed_street, parsed_address = _split_location(location)
        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            market="secondary",
            price_kind=price_kind,
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=area_m2,
            land_area_m2=land_area_m2,
            voivodeship=SRK_REGION,
            city=self._canonical_city(city or parsed_city),
            street=street or parsed_street,
            address_text=address_text or parsed_address,
            lat=None,
            lng=None,
            geo_source=None,
            images=list(images or ()),
            seller_type="government",
            seller_name=SELLER_NAME,
            auction_date=auction_date,
            extra={"srk": dict(extra or {})},
        )

    def _canonical_city(self, value: str | None) -> str | None:
        """Spell the city the way the region config does, when it knows it."""
        city = _clean_city(value)
        if not city:
            return None
        wanted = normalize_text(city)
        for region in self.regions.config.regions.values():
            for known in region.cities:
                if normalize_text(known) == wanted:
                    return known
        return city


def _slug(query: Query) -> str:
    return query.key.split(":", 1)[-1]


def _external_id(
    href: str | None,
    query: Query,
    title: str,
    auction_date: int | None,
) -> str:
    slug = _slug(query)
    match = re.search(r"/(\d+)/?$", href or "")
    if match is not None:
        return f"{slug}/{match.group(1)}"
    digest = hashlib.sha256(f"{title}|{auction_date}".encode()).hexdigest()[:16]
    return f"{slug}/{digest}"


def _empty_page(tree: lxml_html.HtmlElement) -> bool:
    text = normalize_text(_text(tree.text_content()) or "")
    return "brak przetarg" in text or "brak ofert" in text


def _total_items(tree: lxml_html.HtmlElement) -> int | None:
    for node in tree.xpath(
        '//span[contains(@class, "przetargi--liczba") or contains(@class, "oferty--liczba")]',
    ):
        match = _TOTAL_RE.search(_text(node.text_content()) or "")
        if match is not None:
            return int(match.group(1))
    return None


def _total_pages(tree: lxml_html.HtmlElement, current_page: int) -> int:
    pages = [
        int(match.group(1))
        for href in tree.xpath('//div[contains(@class, "pasek-stron")]//a/@href')
        if (match := _PAGE_RE.search(href or ""))
    ]
    return max(pages) if pages else max(current_page, 1)


def _detail_fields(tree: lxml_html.HtmlElement) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in tree.xpath('//section[@id="szczegoly"]//li'):
        label = _first_text(item.xpath("./span[1]"))
        value = _first_text(item.xpath("./strong[1]"))
        if label and value:
            fields[normalize_text(label)] = value
    return fields


def _pin_location(tree: lxml_html.HtmlElement) -> str | None:
    return _first_text(tree.xpath('//span[contains(@class, "oferta__lokalizacja--pin")]'))


def _description(tree: lxml_html.HtmlElement) -> str | None:
    section = tree.xpath('//section[@id="opis"]')
    if not section:
        return None
    paragraphs = [
        text
        for paragraph in section[0].xpath(".//p")
        if (text := strip_html(lxml_html.tostring(paragraph, encoding="unicode")))
    ]
    return "\n\n".join(paragraphs) or None


def _detail_price(tree: lxml_html.HtmlElement) -> int | None:
    return _price_from_text(
        _first_text(tree.xpath('//div[contains(@class, "oferta__cena--top")]')),
    )


def _detail_area(tree: lxml_html.HtmlElement) -> float | None:
    return parse_area(
        _first_text(tree.xpath('.//dl[contains(@class, "oferta__metrarz")]//dd[1]')),
    )


def _files(tree: lxml_html.HtmlElement) -> list[str]:
    files: list[str] = []
    for href in tree.xpath('//section[@id="pliki"]//a/@href'):
        value = _text(href)
        if not value:
            continue
        url = urljoin(SITE_ROOT, value)
        if url not in files:
            files.append(url)
    return files[:20]


def _images(tree: lxml_html.HtmlElement) -> list[str]:
    images: list[str] = []
    for href in tree.xpath(
        '//div[@id="big"]//a[contains(@class, "foto-zoom")]/@href'
        ' | //div[@id="big"]//a[contains(@href, "/assets/pics/")]/@href',
    ):
        value = _text(href)
        if not value:
            continue
        url = urljoin(SITE_ROOT, value)
        if url not in images:
            images.append(url)
    return images[:20]


def _card_city(card: lxml_html.HtmlElement) -> str | None:
    """Return the card's gmina/powiat when the title carries no street."""
    values: dict[str, str] = {}
    for item in card.xpath('.//dl[contains(@class, "oferta__lokalizacja")]/dt'):
        label = _text(item.text_content())
        value = _first_text(item.xpath("following-sibling::dd[1]"))
        if label and value:
            values[normalize_text(label)] = value

    city = values.get("gmina") or values.get("powiat")
    if not city:
        return None
    return city.title()


def _price_kind(summary: Listing, price: int | None) -> PriceKind:
    if price is None:
        return "call"
    if (summary.extra.get("srk") or {}).get("category", "").startswith("przetargi"):
        return "starting"
    return "asking"
