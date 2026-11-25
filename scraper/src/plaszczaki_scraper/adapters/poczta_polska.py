"""Poczta Polska property adapter (sale and lease offer lists)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from html import unescape
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

SITE_ROOT = "https://nieruchomosci.poczta-polska.pl"
BASE_URL = f"{SITE_ROOT}/oferty"
WARSAW = ZoneInfo("Europe/Warsaw")

# (path suffix, canonical offer type, query key suffix)
CATEGORIES: tuple[tuple[str, OfferType, str], ...] = (
    ("sprzedaz", "sale", "sprzedaz"),
    ("wynajem", "rent", "wynajem"),
)

_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_DECIMALS_RE = re.compile(r",\d{2}(?!\d)")
_AUCTION_DATE_RE = re.compile(
    r"Aukcja\s+odbędzie\s+się.{0,300}?w\s+dniu\s+"
    r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_AUCTION_TIME_RE = re.compile(
    r"aukcja\s+rozpocznie\s+się\s+o\s+godzinie\s+(\d{1,2})[.:](\d{2})",
    re.IGNORECASE,
)

_MONTHS: dict[str, int] = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "wrzesnia": 9,
    "pazdziernika": 10,
    "listopada": 11,
    "grudnia": 12,
}

_OFFER_TYPES: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "wynajem": "rent",
    "najem": "lease",
    "dzierzawa": "lease",
    "licytacja": "auction",
    "aukcja": "auction",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "mieszkalny": "flat",
    "dom": "house",
    "budynek mieszkalny": "house",
    "grunt": "plot",
    "dzialka": "plot",
    "parking": "plot",
    "plac": "plot",
    "garaz": "garage",
    "garaż": "garage",
    "budynek biurowy": "commercial",
    "biuro": "commercial",
    "biura": "commercial",
    "lokal uslugowy": "commercial",
    "lokale uslugowe": "commercial",
    "uslugi": "commercial",
    "uslugowo": "commercial",
    "handel": "commercial",
    "pom biurowe": "commercial",
    "pomieszczenia biurowo uslugowe": "commercial",
    "magazyn": "commercial",
    "wielofunkcyjne": "commercial",
}

_COUNTY_SUFFIX_RE = re.compile(r"(?:ski|cki|dzki|wski|owski|icki|ycki)$")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _price(value: str | None) -> int | None:
    """Parse whole-złoty prices that carry a ",dd" grosze suffix."""
    if not value:
        return None
    return parse_price(_DECIMALS_RE.sub("", value))


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Poczta Polska page is not valid HTML: {exc}") from exc


def _node_text(node: lxml_html.HtmlElement) -> str | None:
    """Plain text for a node, keeping ``<br>`` line breaks."""
    raw = lxml_html.tostring(node, encoding="unicode")
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _looks_like_county(value: str | None) -> bool:
    if not value:
        return False
    return bool(_COUNTY_SUFFIX_RE.search(normalize_text(value)))


def _offer_type(value: str | None, fallback: OfferType) -> OfferType:
    if not value:
        return fallback
    normalized = normalize_text(value)
    first = normalized.split(" ", 1)[0]
    if first in _OFFER_TYPES:
        return _OFFER_TYPES[first]
    return fallback


def _property_type(value: str | None) -> PropertyType:
    normalized = normalize_text(value or "")
    if not normalized:
        return "other"
    for key, property_type in _PROPERTY_TYPES.items():
        if normalize_text(key) in normalized:
            return property_type
    return "other"


def _auction_date(value: str | None) -> int | None:
    if not value:
        return None
    match = _AUCTION_DATE_RE.search(value)
    if match is None:
        return None

    day, month_name, year = match.groups()
    month = _MONTHS.get(normalize_text(month_name))
    if month is None:
        return None

    hour = minute = 0
    time_match = _AUCTION_TIME_RE.search(value)
    if time_match is not None:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))

    parsed = datetime(int(year), month, int(day), hour, minute, tzinfo=WARSAW)
    return int(parsed.timestamp() * 1000)


def _drop_page_param(url: str) -> str:
    parts = urlsplit(url)
    query = [item for item in parts.query.split("&") if item and item.split("=", 1)[0] != "page"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), parts.fragment))


class PocztaPolskaAdapter:
    """Parse Poczta Polska's sale/lease lists and detail pages."""

    id = "poczta-polska"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # The list is national; the runner filters cards by ``Woj.:`` derived
        # from the card location. One query per transaction type is enough.
        return [
            Query(
                key=f"national:{key}",
                url=f"{BASE_URL}/{path}",
                region=",".join(regions) or "national",
                offer_type=offer_type,
                property_type="other",
            )
            for path, offer_type, key in CATEGORIES
        ]

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query, url=_drop_page_param(query.url), page=page)
        paged = query.with_page(page)
        return replace(paged, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        container = tree.xpath('//section[contains(@class,"latest-offers")]')
        if not container:
            raise ParseError("Poczta Polska search page layout not recognised")

        items: list[Listing] = []
        seen: set[str] = set()
        for node in container[0].xpath('.//div[contains(@class,"property-item")]'):
            listing = self._summary(node, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        pages = {
            int(match.group(1))
            for href in tree.xpath('//ul[contains(@class,"pagination")]//a/@href')
            if (match := _PAGE_RE.search(href))
        }
        total_pages = max(pages) if pages else 1
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=len(items) if total_pages == 1 else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        details = self._details(tree)
        title = self._title(tree) or summary.title
        description = self._description(tree)

        offer_type = _offer_type(title, "other")
        if offer_type == "other":
            offer_type = _offer_type(details.get("typ transakcji"), summary.offer_type)

        property_type = _property_type(
            f"{title or ''} {details.get('typ lokalu') or ''} {details.get('typ budynku') or ''}",
        )
        if property_type == "other":
            property_type = summary.property_type

        price = _price(details.get("cena")) or summary.price_pln
        area = parse_area(details.get("powierzchnia")) or summary.area_m2
        land_area = parse_area(details.get("powierzchnia dzialki")) or summary.land_area_m2
        rooms = safe_int(details.get("liczba pokoi")) or summary.rooms

        auction_date = _auction_date(description) or summary.auction_date
        images = self._images(tree) or summary.images
        extra = dict(summary.extra)
        poczta_extra = dict(extra.get("poczta") or {})
        poczta_extra.update(
            {
                "numer_oferty": details.get("numer oferty"),
                "transaction": details.get("typ transakcji"),
                "local_type": details.get("typ lokalu"),
                "building_type": details.get("typ budynku"),
                "price_per_m2": details.get("cena za m2"),
            },
        )
        extra["poczta"] = poczta_extra

        return replace(
            summary,
            title=title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="starting" if offer_type == "auction" else "asking",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            price_per_m2_pln=_price(details.get("cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            lat=None,
            lng=None,
            geo_source=None,
            images=images,
            seller_type="government",
            seller_name="Poczta Polska S.A.",
            auction_date=auction_date,
            extra=extra,
        )

    def _summary(self, node: lxml_html.HtmlElement, query: Query) -> Listing | None:
        hrefs = node.xpath('.//h2[contains(@class,"title")]/a/@href')
        if not hrefs:
            hrefs = node.xpath(".//a/@href")
        if not hrefs:
            return None
        url = _text(hrefs[0])
        if not url:
            return None

        parts = [part for part in urlsplit(url).path.split("/") if part]
        external_id = parts[1] if len(parts) > 1 and parts[0] == "oferta" else parts[-1]

        title = _text(" ".join(node.xpath('.//h2[contains(@class,"title")]//a/text()')))
        alt = _text(" ".join(node.xpath(".//*[@data-alt]/@data-alt")))
        h6 = _text(" ".join(node.xpath(".//h6/text()")))
        location = self._location(alt, h6)

        price_node = node.xpath('.//p[contains(@class,"price")]')
        price_text = _text(price_node[0].text_content()) if price_node else None

        area = rooms = None
        for paragraph in node.xpath('.//div[contains(@class,"info")]//p'):
            label = normalize_text(paragraph.text_content())
            number = _text(
                " ".join(paragraph.xpath('.//span[contains(@class,"number")]/text()')),
            )
            if number and "powierzchnia" in label:
                area = parse_area(number)
            elif number and "liczba pokoi" in label:
                rooms = safe_int(number)

        offer_type = _offer_type(title, query.offer_type)
        property_type = _property_type(title)
        images: list[str] = []
        for src in node.xpath(".//img[@data-src]/@data-src"):
            image = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if image not in images:
                images.append(image)

        street = location.get("street")
        city = location.get("city")
        address_text = ", ".join(part for part in (street, city) if part) or city

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="starting" if offer_type == "auction" else "asking",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=_price(price_text),
            area_m2=area,
            rooms=rooms,
            voivodeship=location.get("voivodeship"),
            county=location.get("county"),
            commune=location.get("commune"),
            city=city,
            street=street,
            address_text=address_text,
            lat=None,
            lng=None,
            images=images[:20],
            seller_type="government",
            seller_name="Poczta Polska S.A.",
            extra={"poczta": {"data_alt": alt, "h6": h6}},
        )

    def _location(self, alt: str | None, h6: str | None) -> dict[str, str | None]:
        alt_parts = _split(alt.split("|", 1)[0]) if alt else []
        h6_parts = _split(h6)

        raw_voivodeship = alt_parts[0] if alt_parts else None
        if raw_voivodeship is None and len(h6_parts) >= 3:
            raw_voivodeship = h6_parts[2]
        voivodeship = self.regions.canonical_voivodeship(raw_voivodeship) or raw_voivodeship

        street = city = admin = None
        if len(h6_parts) >= 3:
            street, city, admin = h6_parts[0], h6_parts[1], h6_parts[2]
        elif len(h6_parts) == 2:
            second = h6_parts[1]
            if self.regions.match_city(second) is not None and not _looks_like_county(second):
                street, city = h6_parts[0], second
            else:
                city, admin = h6_parts[0], second
        elif len(h6_parts) == 1:
            city = h6_parts[0]

        county = commune = None
        if admin and self.regions.canonical_voivodeship(admin) is None:
            if _looks_like_county(admin):
                county = admin
            else:
                commune = admin

        if county is None and len(alt_parts) > 1 and _looks_like_county(alt_parts[1]):
            county = alt_parts[1]

        if commune is None and city:
            city_key = normalize_text(city)
            for index, part in enumerate(alt_parts):
                if normalize_text(part) != city_key:
                    continue
                if index > 1:
                    candidate = alt_parts[index - 1]
                    if (
                        not _looks_like_county(candidate)
                        and self.regions.canonical_voivodeship(candidate) is None
                    ):
                        commune = candidate
                break

        return {
            "voivodeship": voivodeship,
            "county": county,
            "commune": commune,
            "city": city,
            "street": street,
        }

    @staticmethod
    def _title(tree: lxml_html.HtmlElement) -> str | None:
        nodes = tree.xpath('//div[contains(@class,"property-detail_overview")]/h5')
        return _text(nodes[0].text_content()) if nodes else None

    @staticmethod
    def _details(tree: lxml_html.HtmlElement) -> dict[str, str]:
        details: dict[str, str] = {}
        rows = tree.xpath(
            '//div[contains(@class,"property-detail")]/div[contains(@class,"area")]',
        )
        for row in rows:
            strong = row.xpath(".//strong")
            value = _text(strong[0].text_content()) if strong else None
            full = _text(row.text_content())
            if not full or not value:
                continue
            label = full.removesuffix(value).strip()
            if label:
                details.setdefault(normalize_text(label), value)
        return details

    @staticmethod
    def _description(tree: lxml_html.HtmlElement) -> str | None:
        paragraphs = [
            text
            for paragraph in tree.xpath('//div[@id="overview"]//p')
            if (text := _node_text(paragraph))
        ]
        return "\n\n".join(paragraphs) or None

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for src in tree.xpath('//*[@data-src and contains(@data-src,"/photos/")]/@data-src'):
            image = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if image not in images:
                images.append(image)
        return images[:20]
