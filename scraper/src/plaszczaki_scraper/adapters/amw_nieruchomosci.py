"""AMW (Agencja Mienia Wojskowego) property tender adapter.

The public list is national and paginated through path segments
(``page,0,limit,50``). The voivodeship filter is a POST form, which the
GET-only HTTP client cannot use, so every card carries its own ``Woj.:``
field and the runner restricts the results to the configured regions.
"""

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
from plaszczaki_scraper.normalize import normalize_text, parse_area, parse_price
from plaszczaki_scraper.regions import RegionRegistry

SITE_ROOT = "https://amw.com.pl"
BASE_PATH = "/pl/nieruchomosci/przetargi-nieruchomosci"
SEARCH_PATH = f"{BASE_PATH}/wyniki-wyszukiwania/page,0,limit,50"
LIMIT = 50
WARSAW = ZoneInfo("Europe/Warsaw")

_PAGE_RE = re.compile(r"page,(\d+)")
_DATE_RE = re.compile(
    r"(\d{2})\.(\d{2})\.(\d{4})(?:r\.)?"
    r"(?:\s*(?:godz\.?)?\s*(\d{1,2}):(\d{2}))?",
)
_LOCATION_RE = re.compile(
    r"Woj\.?:\s*([^,]+),\s*Powiat:\s*([^,]+),\s*Gmina:\s*([^,]+)",
)
_TRAILING_ID_RE = re.compile(r"-(\d+)$")
_DECIMALS_RE = re.compile(r",\d{2}(?!\d)")

_OFFER_TYPES: dict[str, OfferType] = {
    "sprzedaz": "auction",
    "licytacja": "auction",
    "najem": "lease",
    "dzierzawa": "lease",
}

_PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkaniowe": "flat",
    "mieszkalne": "flat",
    "budynki mieszkalne": "house",
    "domy": "house",
    "grunty": "plot",
    "gruntowe": "plot",
    "nieruchomosci gruntowe": "plot",
    "garaze miejsca postojowe": "garage",
    "garaze": "garage",
    "magazynowe": "commercial",
    "lokale uzytkowe": "commercial",
    "uslugowo handlowe": "commercial",
    "handlowe": "commercial",
    "biurowe": "commercial",
    "produkcyjne": "commercial",
}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _price(value: str | None) -> int | None:
    """Parse whole-złoty prices that carry a ",00" decimal suffix."""
    if not value:
        return None
    return parse_price(_DECIMALS_RE.sub("", value))


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"AMW page is not valid HTML: {exc}") from exc


def _lines(node: lxml_html.HtmlElement) -> list[str]:
    """Split a node's text on ``<br>`` so the card heading keeps its lines."""
    raw = lxml_html.tostring(node, encoding="unicode")
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", raw))
    return [line.strip() for line in text.splitlines() if line.strip()]


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


def _canonical_offer_type(value: str | None, fallback: OfferType) -> OfferType:
    if not value:
        return fallback
    return _OFFER_TYPES.get(normalize_text(value), fallback)


def _canonical_property_type(categories: Sequence[str]) -> PropertyType:
    for category in categories:
        normalized = normalize_text(category)
        if normalized in _PROPERTY_TYPES:
            return _PROPERTY_TYPES[normalized]
        for key, property_type in _PROPERTY_TYPES.items():
            if normalized.startswith(key):
                return property_type
    return "other"


def _street_name(street_line: str | None) -> str | None:
    """Drop the ``ul.``/``al.`` prefix so the geocoder gets a clean street."""
    if not street_line:
        return None
    cleaned = re.sub(r"^(?:ul\.|al\.|pl\.|os\.)\s*", "", street_line, flags=re.I)
    return _text(cleaned)


class AmwAdapter:
    """Parse AMW's national tender list and detail pages."""

    id = "amw-nieruchomosci"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        # The site filter is a POST form and the client is GET-only. One
        # national query is parsed and the runner keeps the in-scope regions.
        return [
            Query(
                key="national:przetargi",
                url=f"{SITE_ROOT}{SEARCH_PATH}",
                region=",".join(regions) or "national",
                offer_type="auction",
                property_type="other",
            ),
        ]

    def with_page(self, query: Query, page: int) -> Query:
        parts = urlsplit(query.url)
        path = _PAGE_RE.sub(f"page,{max(page - 1, 0)}", parts.path, count=1)
        url = urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
        return replace(query, url=url, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        container = tree.xpath('//div[contains(@class,"real-estate-list-container")]')
        if not container:
            raise ParseError("AMW search page layout not recognised")

        items: list[Listing] = []
        seen: set[str] = set()
        for node in container[0].xpath('.//div[contains(@class,"element")]'):
            listing = self._summary(node, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        next_pages = {
            int(match.group(1)) + 1
            for href in tree.xpath('//button[contains(@class,"btn-show-more-items")]/@data-url')
            if (match := _PAGE_RE.search(href))
        }
        total_pages = max(next_pages) if next_pages else max(query.page, 1)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=len(items) if total_pages == query.page else None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        title_block = tree.xpath('//div[contains(@class,"slider")]//div[contains(@class,"title")]')

        city = None
        street_line = None
        if title_block:
            main = title_block[0].xpath('./p[contains(@class,"main")]')
            sub = title_block[0].xpath('./p[contains(@class,"sub")]')
            city = _text(main[0].text_content()) if main else None
            street_line = _text(sub[0].text_content()) if sub else None

        location = self._location(tree)
        city = city or location.get("city")
        street_line = street_line or location.get("street")
        voivodeship = self._voivodeship(location.get("voivodeship")) or summary.voivodeship

        area, land_area = self._areas(tree)
        position = self._position(tree)
        price = _price(position.get("price")) or summary.price_pln

        heading = self._heading(tree)
        offer_type = _canonical_offer_type(heading, summary.offer_type)
        property_type = self._property_type(tree) or summary.property_type

        images = self._images(tree)
        description = self._description(tree)
        auction_date = _date_ms(self._auction_text(tree)) or summary.auction_date

        address_text = (
            ", ".join(part for part in (street_line, city) if part) or summary.address_text
        )
        extra = dict(summary.extra)
        amw_extra = dict(extra.get("amw") or {})
        amw_extra.update(
            {
                "heading": heading,
                "location": location.get("raw"),
                "positions": position.get("raw"),
            },
        )
        extra["amw"] = amw_extra

        return replace(
            summary,
            title=summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="starting" if price is not None else "call",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=area or summary.area_m2,
            land_area_m2=land_area or summary.land_area_m2,
            voivodeship=voivodeship,
            county=location.get("county") or summary.county,
            commune=location.get("commune") or summary.commune,
            city=city or summary.city,
            street=_street_name(street_line) or summary.street,
            address_text=address_text,
            lat=None,
            lng=None,
            geo_source=None,
            images=images or summary.images,
            seller_type="government",
            seller_name="Agencja Mienia Wojskowego",
            auction_date=auction_date,
            extra=extra,
        )

    def _summary(self, node: lxml_html.HtmlElement, query: Query) -> Listing | None:
        hrefs = node.xpath(".//h2/a/@href")
        if not hrefs:
            return None
        href = _text(hrefs[0])
        if not href:
            return None

        slug = href.rstrip("/").rsplit("/", 1)[-1]
        id_match = _TRAILING_ID_RE.search(slug)
        external_id = id_match.group(1) if id_match else slug

        headings = node.xpath(".//h2")
        lines = _lines(headings[0]) if headings else []
        city = lines[0].rstrip(",").strip() if lines else None
        street_line = lines[1].rstrip(",").strip() if len(lines) > 1 else None
        title = _text(", ".join(line.rstrip(",").strip() for line in lines)) or city

        details = self._card_details(node)
        raw_area = details.get("powierzchnia")
        area = land_area = None
        if raw_area:
            if re.search(r"\bha\b", raw_area.lower()):
                land_area = parse_area(raw_area)
            else:
                area = parse_area(raw_area)

        price = _price(details.get("cena wywolawcza"))
        offer_type = _canonical_offer_type(details.get("typ"), query.offer_type)
        categories = [
            _text(category.text_content()) or ""
            for category in node.xpath('.//p[contains(@class,"col-category__item")]')
        ]
        property_type = _canonical_property_type(categories)

        location = self._location(node)
        street = _street_name(street_line)
        address_text = ", ".join(part for part in (street_line, city) if part) or None

        images: list[str] = []
        for src in node.xpath('.//div[contains(@class,"col-img")]//img/@src'):
            url = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if url not in images:
                images.append(url)

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=f"{SITE_ROOT}{href}",
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            price_kind="starting" if price is not None else "call",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            price_pln=price,
            area_m2=area,
            land_area_m2=land_area,
            voivodeship=self._voivodeship(location.get("voivodeship")),
            county=location.get("county"),
            commune=location.get("commune"),
            city=city,
            street=street,
            address_text=address_text,
            lat=None,
            lng=None,
            images=images[:20],
            seller_type="government",
            seller_name="Agencja Mienia Wojskowego",
            auction_date=_date_ms(details.get("data przetargu")),
            extra={
                "amw": {
                    "slug": slug,
                    "categories": [category for category in categories if category],
                },
            },
        )

    @staticmethod
    def _card_details(node: lxml_html.HtmlElement) -> dict[str, str]:
        details: dict[str, str] = {}
        for paragraph in node.xpath('.//div[contains(@class,"col-informations")]/p'):
            labels = paragraph.xpath('.//span[contains(@class,"bold")]/text()')
            label = _text(" ".join(labels))
            value = _text(paragraph.text_content())
            if label and value:
                if value.startswith(label):
                    value = value[len(label) :].strip()
                details[normalize_text(label)] = value
                continue
            strong = _text(" ".join(paragraph.xpath(".//strong/text()")))
            if strong:
                details["typ"] = strong
        return details

    def _location(self, node: lxml_html.HtmlElement) -> dict[str, str | None]:
        texts = [_text(paragraph.text_content()) for paragraph in node.xpath(".//p")]
        raw = next((text for text in texts if text and text.startswith("Woj.:")), None)
        if raw is None:
            return {}

        match = _LOCATION_RE.search(raw)
        if match is not None:
            voivodeship, county, commune = (part.strip() for part in match.groups())
        else:
            voivodeship = raw.split(":", 1)[1].strip().rstrip(",") if ":" in raw else raw
            county = self._labelled(texts, "Powiat:")
            commune = self._labelled(texts, "Gmina:")
        street = None
        for text in texts:
            if not text or text.startswith(("Woj.:", "gruntu:", "użytkowa:")):
                continue
            if re.match(r"^(?:ul\.|al\.|pl\.|os\.)", text, flags=re.I):
                street = text
                break

        return {
            "raw": raw,
            "voivodeship": voivodeship,
            "county": county,
            "commune": commune,
            "street": street,
        }

    @staticmethod
    def _labelled(texts: Sequence[str | None], label: str) -> str | None:
        for text in texts:
            if text and text.startswith(label):
                value = text.split(":", 1)[1].strip().rstrip(",")
                return value or None
        return None

    def _voivodeship(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.regions.canonical_voivodeship(value) or value

    @staticmethod
    def _areas(tree: lxml_html.HtmlElement) -> tuple[float | None, float | None]:
        area = land_area = None
        for paragraph in tree.xpath(
            '//div[contains(@class,"right-solid")]//p[contains(@class,"text")]'
        ):
            text = _text(paragraph.text_content())
            if not text:
                continue
            lowered = text.lower()
            if lowered.startswith("użytkowa:") or lowered.startswith("uzytkowa:"):
                area = parse_area(text.split(":", 1)[1])
            elif lowered.startswith("gruntu:"):
                land_area = parse_area(text.split(":", 1)[1])
        return area, land_area

    @staticmethod
    def _position(tree: lxml_html.HtmlElement) -> dict[str, str | None]:
        positions = tree.xpath('//ol[contains(@class,"positions")]/li')
        if not positions:
            return {}
        raw = _text(" ".join(positions[0].text_content().split()))
        price = None
        if raw:
            match = re.search(r"Cena wywoławcza\s+([\d\s.,]+)\s*PLN", raw)
            if match:
                price = f"{match.group(1)} PLN"
        return {"raw": raw, "price": price}

    @staticmethod
    def _heading(tree: lxml_html.HtmlElement) -> str | None:
        for heading in tree.xpath('//div[contains(@class,"header")]/h2'):
            text = _text(heading.text_content())
            if text and normalize_text(text).startswith("ogloszenie o przetargu"):
                return text
        return None

    @staticmethod
    def _property_type(tree: lxml_html.HtmlElement) -> PropertyType | None:
        categories = [
            _text(node.text_content()) or ""
            for node in tree.xpath(
                '//h2[normalize-space()="Kategoria"]'
                '/following-sibling::p[contains(@class,"text-with-icon")]',
            )
        ]
        parsed = _canonical_property_type(categories)
        return parsed if parsed != "other" else None

    @staticmethod
    def _auction_text(tree: lxml_html.HtmlElement) -> str | None:
        for paragraph in tree.xpath("//p"):
            text = _text(paragraph.text_content())
            if text and "Przetarg w dniu" in text:
                return text
        return None

    @staticmethod
    def _description(tree: lxml_html.HtmlElement) -> str | None:
        content = tree.xpath(
            '//h2[normalize-space()="Opis nieruchomości"]/following-sibling::div[1]',
        )
        if not content:
            return None
        paragraphs = [
            text
            for paragraph in content[0].xpath(".//p")
            if (text := _text(paragraph.text_content()))
        ]
        return "\n\n".join(paragraphs) or None

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for src in tree.xpath('//div[contains(@class,"swiper-container-estate")]//img/@src'):
            src = src.strip()
            if not src or src.endswith("no_photo.gif"):
                continue
            url = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if url not in images:
                images.append(url)
        return images[:20]
