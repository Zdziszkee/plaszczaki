"""Sprzedajemy.pl adapter (search pages + detail pages).

Sprzedajemy renders offers server-side. Search pages expose a schema.org
``ItemList`` plus article cards with title, price, area and city, while detail
pages expose a schema.org ``Product`` (offer, description, images), a
``data-coordinates`` attribute and an attribute table (area, rooms, floor,
market). Listings are paginated with ``?offset=`` steps of 30 and scoped by a
region path segment, e.g. ``/malopolskie/nieruchomosci/mieszkania/sprzedaz``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html as lxml_html
from lxml.html import HtmlElement

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PriceKind, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_floor,
    parse_price,
    parse_rooms,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://sprzedajemy.pl"
PAGE_SIZE = 30
WARSAW = ZoneInfo("Europe/Warsaw")

CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("grunty-i-dzialki", "sprzedaz", "sale", "plot"),
    ("grunty-i-dzialki", "wynajem", "rent", "plot"),
    ("lokale-uzytkowe", "sprzedaz", "sale", "commercial"),
    ("lokale-uzytkowe", "wynajem", "rent", "commercial"),
    ("hale-i-magazyny", "sprzedaz", "sale", "commercial"),
    ("hale-i-magazyny", "wynajem", "rent", "commercial"),
    ("garaze-i-miejsca-postojowe", "sprzedaz", "sale", "garage"),
    ("garaze-i-miejsca-postojowe", "wynajem", "rent", "garage"),
)

_OFFER_BY_NAME: dict[str, OfferType] = {
    "sprzedaz": "sale",
    "wynajem": "rent",
    "najem": "rent",
    "licytacja": "auction",
    "dzierzawa": "lease",
}

_PROPERTY_BY_NAME: dict[str, PropertyType] = {
    "mieszkania": "flat",
    "mieszkanie": "flat",
    "domy": "house",
    "dom": "house",
    "grunty i dzialki": "plot",
    "grunt": "plot",
    "dzialka": "plot",
    "lokale uzytkowe": "commercial",
    "lokal uzytkowy": "commercial",
    "hale i magazyny": "commercial",
    "hala": "commercial",
    "magazyn": "commercial",
    "garaze i miejsca postojowe": "garage",
    "garaz": "garage",
    "pokoje": "room",
    "pokoj": "room",
}

_MONTHS: dict[str, int] = {
    "sty": 1,
    "lut": 2,
    "mar": 3,
    "kwi": 4,
    "maj": 5,
    "cze": 6,
    "lip": 7,
    "sie": 8,
    "wrz": 9,
    "paz": 10,
    "lis": 11,
    "gru": 12,
}

_POLISH_DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3})\.?\s+(\d{1,2}):(\d{2})(?:\s+(\d{4}))?",
)
_OFFER_ID_RE = re.compile(r"-nr(\d+)(?:[/?#]|$)")
_COORDINATE_RE = re.compile(
    r"""['"]?lat['"]?\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*['"]?lng['"]?\s*:\s*(-?\d+(?:\.\d+)?)""",
)

_XPATH_ARTICLE = ".//article[contains(concat(' ', normalize-space(@class), ' '), ' element ')]"
_XPATH_CITY = ".//*[contains(concat(' ', normalize-space(@class), ' '), ' city ')]"
_XPATH_OFFERS_SECTION = "//section[contains(concat(' ', normalize-space(@class), ' '), ' offers ')]"
_XPATH_ATTRIBUTES = (
    "//ul[contains(concat(' ', normalize-space(@class), ' '), ' attribute-list ')]"
    "/li[contains(concat(' ', normalize-space(@class), ' '), ' item ')]"
)


class SprzedajemyAdapter:
    """Parse Sprzedajemy's server-rendered listing pages."""

    id = "sprzedajemy"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for category, offer, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{category}:{offer}",
                        url=f"{BASE_URL}/{region}/nieruchomosci/{category}/{offer}",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return replace(query, url=_page_url(query.url, page), page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _document(html)
        section = _first(tree, _XPATH_OFFERS_SECTION)
        if section is None:
            raise ParseError("Sprzedajemy search page is missing the offers section")

        items: list[Listing] = []
        for article in section.xpath(_XPATH_ARTICLE):
            summary = _summary(article, query, self.source_id)
            if summary is not None:
                items.append(summary)

        total_items = _total_items(tree)
        return SearchPage(
            items=items,
            current_page=_current_page(tree) or query.page,
            total_pages=_total_pages(total_items, tree),
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _document(html)
        product = _product_block(tree)
        attributes = _attributes(tree)

        title = _text(_first(tree, "//h1[contains(@class, 'offerTitle')]")) or _text(
            product.get("name"),
        )
        description = strip_html(_text(product.get("description"))) or strip_html(
            _text(_first(tree, "//*[contains(@class, 'offerDescription')]")),
        )

        city_node = _first(
            tree,
            "//ul[contains(@class, 'offerAdditionalInfo')]//a[contains(@class, 'locationName')]",
        )
        city = _place(_text(city_node))
        street = _text_value(city_node.get("data-details")) if city_node is not None else None
        province = _text(
            _first(
                tree,
                "//ul[contains(@class, 'offerAdditionalInfo')]"
                "//span[contains(@class, 'province')]//a",
            ),
        )
        lat, lng, zoom = _coordinates(tree)

        price_text = _text(_first(tree, "//div[contains(@class, 'offer-price-box')]"))
        price = _parse_amount(_text(_dict(product.get("offers")).get("price")))
        if price is None:
            price = parse_price(price_text)

        offer_type, property_type = _classification(product, attributes, summary)
        market = _market(_attribute(attributes, "rynek"))

        area_text = _attribute(attributes, "powierzchnia")
        land_text = _attribute(attributes, "powierzchnia dzialki")
        area = parse_area(area_text)
        land_area = parse_area(land_text)
        if property_type == "plot" and land_area is None and area is not None:
            # For plots the single "Powierzchnia" row describes the land.
            land_area, area = area, None

        floor, floor_label = parse_floor(_attribute(attributes, "pietro"))
        seller_type = _seller_type(
            _attribute(attributes, "oferta od"),
            _text(_first(tree, ".//*[contains(@class, 'seller-type-info__label')]")),
        )

        seller = _dict(product.get("owns"))
        seller_name = _text(seller.get("name"))
        seller_address = _text_value(seller.get("address"))

        voivodeship = self.regions.canonical_voivodeship(province) or summary.voivodeship
        address_text = seller_address or _join([street, city]) or _join([city, province])

        published = _published_at(tree) or summary.published_at

        extra = dict(summary.extra)
        sprzedajemy_extra = dict(extra.get("sprzedajemy") or {})
        sprzedajemy_extra.update(
            {
                "category": _text(product.get("category")),
                "attributes": attributes,
                "seller_address": seller_address,
                "seller_phone": _text_value(seller.get("telephone")),
                "coordinates_zoom": zoom,
                "offer_number": _offer_number(tree) or summary.external_id,
            },
        )
        extra["sprzedajemy"] = sprzedajemy_extra

        url = _canonical(tree) or _text(product.get("url")) or summary.url
        external_id = _offer_id(url) or _offer_number(tree) or summary.external_id

        return replace(
            summary,
            external_id=external_id,
            url=url,
            title=title or summary.title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market=market,
            price_kind=_price_kind(price, price_text or ""),
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=parse_price(_attribute(attributes, "cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(_attribute(attributes, "liczba pokoi")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(_attribute(attributes, "rok budowy")),
            voivodeship=voivodeship,
            city=city or summary.city,
            street=street or summary.street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=_images(product, tree) or summary.images,
            seller_type=seller_type or summary.seller_type,
            seller_name=seller_name or summary.seller_name,
            published_at=published,
            extra=extra,
        )


def _document(html: str) -> HtmlElement:
    if not html or not html.strip():
        raise ParseError("Sprzedajemy page is empty")
    try:
        return lxml_html.fromstring(html)
    except Exception as exc:  # lxml raises ParserError, which ty cannot import.
        raise ParseError(f"Sprzedajemy page is not valid HTML: {exc}") from exc


def _first(tree: HtmlElement, xpath: str) -> HtmlElement | None:
    nodes = tree.xpath(xpath)
    return nodes[0] if nodes else None


def _attribute_node(tree: HtmlElement, xpath: str, name: str) -> str | None:
    node = _first(tree, xpath)
    return node.get(name) if node is not None else None


def _canonical(tree: HtmlElement) -> str | None:
    return _text_value(_attribute_node(tree, "//link[@rel='canonical']", "href"))


def _text(node: Any) -> str | None:
    if node is None:
        return None
    content = node.text_content() if hasattr(node, "text_content") else str(node)
    text = " ".join(content.split())
    return text or None


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _place(value: str | None) -> str | None:
    """Clean a place label ("Niepołomice," -> "Niepołomice")."""
    if value is None:
        return None
    text = value.strip().strip(",").strip()
    if not text or normalize_text(text) == "cala polska":
        return None
    return text


_DECIMAL_AMOUNT_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _parse_amount(value: Any) -> int | None:
    """Parse a schema.org price, which may be a decimal string such as "2650.00"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    # parse_price() drops decimal separators, so keep JSON-LD decimals intact.
    if _DECIMAL_AMOUNT_RE.fullmatch(text):
        amount = int(float(text))
        return amount if amount > 0 else None
    return parse_price(text)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _join(parts: Sequence[str | None]) -> str | None:
    text = ", ".join(part for part in parts if part)
    return text or None


def _page_url(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if page <= 1:
        query.pop("offset", None)
    else:
        query["offset"] = str((page - 1) * PAGE_SIZE)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _ld_blocks(tree: HtmlElement) -> list[Any]:
    payloads: list[Any] = []
    for raw in tree.xpath("//script[@type='application/ld+json']/text()"):
        try:
            payloads.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return payloads


def _product_block(tree: HtmlElement) -> dict[str, Any]:
    for payload in _ld_blocks(tree):
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and "offers" in candidate and "name" in candidate:
                return candidate
    return {}


def _offer_id(href: str) -> str | None:
    match = _OFFER_ID_RE.search(href)
    return match.group(1) if match else None


def _total_items(tree: HtmlElement) -> int | None:
    for raw in tree.xpath("//li[contains(@class, 'tabAll')]//em/text()"):
        match = re.search(r"\d[\d\s\u00a0]*", raw)
        if match:
            return safe_int(match.group(0).replace(" ", "").replace("\u00a0", ""))
    return None


def _total_pages(total_items: int | None, tree: HtmlElement) -> int | None:
    if total_items is not None:
        return max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)

    pages = [
        safe_int(node.text_content())
        for node in tree.xpath("//ul[contains(@class, 'cntPaginator')]//li/a")
    ]
    known = [page for page in pages if page]
    return max(known) if known else None


def _current_page(tree: HtmlElement) -> int | None:
    counter = _text(_first(tree, "//div[contains(@class, 'cntItemCounter')]"))
    if not counter:
        return None
    match = re.search(r"(\d+)\s*-\s*(\d+)", counter)
    if match is None:
        return None
    start = safe_int(match.group(1))
    if not start:
        return None
    return (start - 1) // PAGE_SIZE + 1


def _summary(article: HtmlElement, query: Query, source_id: str) -> Listing | None:
    link = _first(article, ".//h2[contains(@class, 'title')]//a[@href]")
    if link is None:
        return None
    href = link.get("href")
    external_id = _offer_id(href) if href else None
    if not href or external_id is None:
        return None

    price_text = _text(_first(article, ".//*[contains(@class, 'price')]"))
    price = parse_price(price_text)

    area_text = _attribute_from_nodes(article, "Pow.")
    area = parse_area(area_text)

    seller_label = _text(_first(article, ".//*[contains(@class, 'seller-type-info__label')]"))
    image = _text_value(_attribute_node(article, ".//img[@src]", "src"))

    time_node = _first(article, ".//time[@datetime]")
    published = parse_datetime_ms(time_node.get("datetime")) if time_node is not None else None

    listing = Listing(
        source_id=source_id,
        external_id=external_id,
        url=urljoin(BASE_URL, href),
        title=_text(link),
        offer_type=query.offer_type,
        property_type=query.property_type,
        price_kind=_price_kind(price, price_text or ""),
        price_unit="monthly" if query.offer_type == "rent" else "total",
        price_pln=price,
        voivodeship=query.region,
        city=_place(_text(_first(article, _XPATH_CITY))),
        images=[image] if image else [],
        seller_type=_seller_type(None, seller_label),
        published_at=published,
        extra={"sprzedajemy": {"offer_number": external_id, "seller_label": seller_label}},
    )
    if query.property_type == "plot":
        listing.land_area_m2 = area
    else:
        listing.area_m2 = area
    return listing


def _attribute_from_nodes(tree: HtmlElement, label: str) -> str | None:
    for node in tree.xpath(".//*[contains(@class, 'attribute')]"):
        text = _text(node)
        if text is None or not text.startswith(label):
            continue
        _, _, value = text.partition(":")
        return value.strip() or None
    return None


def _attributes(tree: HtmlElement) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for item in tree.xpath(_XPATH_ATTRIBUTES):
        label = _text(_first(item, ".//span"))
        value = _text(_first(item, ".//strong"))
        if label is None or value is None:
            continue
        attributes[normalize_text(label)] = value
    return attributes


def _attribute(attributes: dict[str, str], key: str) -> str | None:
    return attributes.get(normalize_text(key))


def _coordinates(tree: HtmlElement) -> tuple[float | None, float | None, int | None]:
    raw = None
    for node in tree.xpath("//*[@data-coordinates]"):
        raw = node.get("data-coordinates")
        if raw:
            break
    if not raw:
        return None, None, None

    match = _COORDINATE_RE.search(raw)
    if match is None:
        return None, None, None
    zoom_match = re.search(r"""['"]?zoom['"]?\s*:\s*(-?\d+)""", raw)
    return (
        float(match.group(1)),
        float(match.group(2)),
        safe_int(zoom_match.group(1)) if zoom_match else None,
    )


def _classification(
    product: dict[str, Any],
    attributes: dict[str, str],
    summary: Listing,
) -> tuple[OfferType, PropertyType]:
    parts = [part.strip() for part in str(product.get("category") or "").split(",") if part.strip()]
    offer_type = summary.offer_type
    property_type = summary.property_type

    for part in parts:
        normalized = normalize_text(part)
        if normalized in _OFFER_BY_NAME:
            offer_type = _OFFER_BY_NAME[normalized]
        elif normalized in _PROPERTY_BY_NAME:
            property_type = _PROPERTY_BY_NAME[normalized]

    if "typ" in attributes and property_type == summary.property_type:
        normalized = normalize_text(attributes["typ"])
        if normalized in _PROPERTY_BY_NAME:
            property_type = _PROPERTY_BY_NAME[normalized]

    return offer_type, property_type


def _market(value: str | None) -> Market:
    normalized = normalize_text(value or "")
    if normalized in {"pierwotny", "rynek pierwotny"}:
        return "primary"
    if normalized in {"wtorny", "rynek wtorny"}:
        return "secondary"
    return "unknown"


def _seller_type(from_offer: str | None, label: str | None) -> str | None:
    for value in (from_offer, label):
        normalized = normalize_text(value or "")
        if not normalized:
            continue
        if "prywat" in normalized:
            return "private"
        if "firm" in normalized or "biur" in normalized or "dewelop" in normalized:
            return "agency"
    return None


def _price_kind(price: int | None, text: str) -> PriceKind:
    if price is not None:
        return "asking"
    if "zapytaj" in normalize_text(text) or "na zapytanie" in normalize_text(text):
        return "call"
    return "unknown"


def _images(product: dict[str, Any], tree: HtmlElement) -> list[str]:
    raw = product.get("image")
    values = raw if isinstance(raw, list) else [raw]
    images = [str(value).strip() for value in values if isinstance(value, str) and value.strip()]
    if not images:
        images = [
            src.strip()
            for src in tree.xpath("//div[@id='offerGallery']//img/@src")
            if src and src.strip()
        ]
    return images[:20]


def _offer_number(tree: HtmlElement) -> str | None:
    node = _first(tree, "//li[contains(., 'Nr ogłoszenia')]")
    if node is None:
        return None
    match = re.search(r"(\d{4,})", node.text_content())
    return match.group(1) if match else None


def _published_at(tree: HtmlElement) -> int | None:
    node = _first(tree, "//li[.//i[contains(@class, 'icon-clock')]]")
    return _parse_polish_datetime(_text(node)) if node is not None else None


def _parse_polish_datetime(value: str | None) -> int | None:
    if not value:
        return None

    now = datetime.now(WARSAW)
    normalized = normalize_text(value)

    if normalized.startswith("dzisiaj") or normalized.startswith("wczoraj"):
        match = re.search(r"(\d{1,2}):(\d{2})", value)
        if match is None:
            return None
        days = 0 if normalized.startswith("dzisiaj") else 1
        moment = now.replace(
            hour=int(match.group(1)),
            minute=int(match.group(2)),
            second=0,
            microsecond=0,
        )
        return int((moment.timestamp() - days * 86_400) * 1000)

    match = _POLISH_DATE_RE.search(value)
    if match is None:
        return None

    day, month_name, hour, minute, year = match.groups()
    month = _MONTHS.get(normalize_text(month_name)[:3])
    if month is None:
        return None

    year_number = int(year) if year else now.year
    try:
        moment = datetime(
            year_number,
            month,
            int(day),
            int(hour),
            int(minute),
            tzinfo=WARSAW,
        )
    except ValueError:
        return None

    if year is None and moment > now.replace(microsecond=0) + timedelta(days=1):
        moment = moment.replace(year=year_number - 1)
    return int(moment.timestamp() * 1000)
