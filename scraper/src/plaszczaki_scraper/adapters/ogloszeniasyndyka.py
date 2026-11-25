"""Ogłoszenia Syndyka adapter (regional classifieds search + detail pages).

The portal is a small, free-to-read classifieds site for syndyk/likwidator
sales.  Search results are server rendered and the detail pages embed the
exact coordinates of the advertised property, which makes the source usable
for the map-oriented pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    safe_float,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://ogloszeniasyndyka.pl"
SEARCH_URL = f"{BASE_URL}/listing"
CATEGORY_ID = "4"  # "Nieruchomości"

# The listing page filters by a Google-Places style region id ("placeid").
# Only the voivodeships in the configured scope need to be mapped.
REGION_PLACES: dict[str, tuple[str, str]] = {
    "malopolskie": ("Małopolskie", "PL.6"),
    "slaskie": ("Śląskie", "PL.9"),
}

AD_PATH_RE = re.compile(r"/ad/(\d+)/")
TOTAL_RE = re.compile(
    r"(?:Znaleźliśmy|Znaleziono)\s*([\d\s\u00a0]+)\s*(?:ogłosze|ofert)",
    re.IGNORECASE,
)
PAGE_RE = re.compile(r"[?&]page=(\d+)")
DATE_RE = re.compile(r"(\d{1,2}),?\s+([a-ząćęłńóśźż]{3,})\s+(\d{4})", re.IGNORECASE)
CASE_RE = re.compile(
    r"sygn[a-z]*\.?\s*akt\.?\s*([A-Za-z0-9][A-Za-z0-9/\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9/\-]*){0,3})",
)

MONTHS = {
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
    "paź": 10,
    "lis": 11,
    "gru": 12,
}

PROPERTY_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "mieszkania": "flat",
    "dom": "house",
    "domy": "house",
    "dzialka": "plot",
    "dzialki": "plot",
    "grunt": "plot",
    "grunty": "plot",
    "lokal": "commercial",
    "lokale": "commercial",
    "garaz": "garage",
    "garaż": "garage",
    "pokoj": "room",
    "pokój": "room",
}


class OgloszeniaSyndykaAdapter:
    """Parse the regional search pages and coordinate-bearing detail pages."""

    id = "ogloszeniasyndyka"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            place = REGION_PLACES.get(region)
            if place is None:
                continue
            name, place_id = place
            queries.append(
                Query(
                    key=f"{region}:nieruchomosci",
                    url=(
                        f"{SEARCH_URL}?cat={CATEGORY_ID}"
                        f"&location={quote(name)}"
                        f"&placetype=state&placeid={place_id}&sort=newest"
                    ),
                    region=region,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        document = _parse_html(html)
        items: list[Listing] = []
        seen: set[str] = set()

        for anchor in document.xpath(f'//a[starts-with(@href, "{BASE_URL}/ad/")]'):
            href = (anchor.get("href") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            match = AD_PATH_RE.search(href)
            if match is None:
                continue
            try:
                items.append(self._summary(anchor, match.group(1), href, query))
            except ParseError:
                continue

        total_items = _total_items(document)
        if not items and total_items is None:
            raise ParseError("Ogłoszenia Syndyka page has no result list")

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=_total_pages(document),
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        document = _parse_html(html)

        title = _first_text(document.xpath('(//div[contains(@class, "ofertasection")]//h2)[1]'))
        price = parse_price(
            _first_text(document.xpath('//span[contains(@class, "cena")]')),
        )

        description_node = _first_node(
            document.xpath(
                '//div[contains(@class, "ramkaopis")]//div[contains(@class, "user-html")]',
            ),
        )
        description = _description(description_node)

        lat = safe_float(
            _first_attr(document, '//div[@id="singleListingMap"]', "data-latitude"),
        )
        lng = safe_float(
            _first_attr(document, '//div[@id="singleListingMap"]', "data-longitude"),
        )
        lat, lng = _valid_point(lat, lng)

        address = _address(document)
        city, province = _split_address(address)
        voivodeship = self.regions.canonical_voivodeship(province)
        if voivodeship is None and province:
            # An out-of-scope voivodeship is kept verbatim so the runner's
            # resolver can reject it instead of inheriting the query region.
            voivodeship = normalize_text(province) or summary.voivodeship
        if voivodeship is None:
            voivodeship = summary.voivodeship
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        breadcrumb = [
            text
            for text in (
                _clean_text(node) for node in document.xpath('//nav[@id="breadcrumbs"]//a')
            )
            if text
        ]
        property_type = _property_type(breadcrumb[-1] if breadcrumb else None)
        if property_type == "other":
            property_type = summary.property_type

        images = _gallery_images(document)
        published_at = (
            _polish_date(_first_text(document.xpath('//div[contains(@class, "pasekszary")]//span')))
            or summary.published_at
        )

        extra = dict(summary.extra)
        extra["ogloszeniasyndyka"] = {
            "breadcrumb": breadcrumb,
            "badge": _first_text(
                document.xpath(
                    '//div[contains(@class, "ofertasection")]//div[contains(@class, "badge")]'
                ),
            ),
            "views": _views(document),
            "images": len(images),
        }

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            offer_type="auction",
            property_type=property_type,
            market="secondary",
            price_kind="starting",
            price_unit="total",
            price_pln=price if price is not None else summary.price_pln,
            area_m2=_area_from_description(description),
            land_area_m2=_land_area_from_description(description),
            voivodeship=voivodeship,
            city=city or summary.city,
            address_text=address or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images or summary.images,
            published_at=published_at,
            case_number=_case_number(description),
            auction_date=_auction_date(description),
            extra=extra,
        )

    def _summary(self, anchor: Any, external_id: str, href: str, query: Query) -> Listing:
        title = _first_attr_value(anchor, ".//img", "alt") or _first_text(
            anchor.xpath('.//h4[contains(@class, "card-title")]'),
        )
        city = _first_text(anchor.xpath('.//p[contains(@class, "miasto")]'))
        price = parse_price(_first_text(anchor.xpath('.//p[contains(@class, "cena")]')))
        published_at = _polish_date(
            _first_text(anchor.xpath('.//div[contains(@class, "listing-footer")]//span')),
        )
        image = _first_attr_value(anchor, ".//img", "data-original")
        if not image or image.endswith("/default.png"):
            image = _first_attr_value(anchor, ".//img", "src")
        images = [image] if image and not image.endswith("/default.png") else []

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=title,
            offer_type="auction",
            property_type=query.property_type,
            market="secondary",
            price_kind="starting",
            price_unit="total",
            price_pln=price,
            voivodeship=query.region,
            city=city,
            images=images,
            published_at=published_at,
            extra={"ogloszeniasyndyka": {"listing_id": external_id}},
        )


def _parse_html(html: str) -> lxml_html.HtmlElement:
    if not html or not html.strip():
        raise ParseError("empty page")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ParseError(f"Ogłoszenia Syndyka page is not valid HTML: {exc}") from exc


def _clean_text(node: Any) -> str | None:
    text = node.text_content() if hasattr(node, "text_content") else str(node)
    cleaned = " ".join(text.split())
    return cleaned or None


def _first_node(nodes: list[Any]) -> Any | None:
    return nodes[0] if nodes else None


def _first_text(nodes: list[Any]) -> str | None:
    for node in nodes:
        text = " ".join(node.split()) if isinstance(node, str) else _clean_text(node)
        if text:
            return text
    return None


def _first_attr(document: Any, xpath: str, name: str) -> str | None:
    nodes = document.xpath(xpath)
    if not nodes:
        return None
    value = nodes[0].get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first_attr_value(element: Any, xpath: str, name: str) -> str | None:
    nodes = element.xpath(xpath)
    for node in nodes:
        value = node.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _total_items(document: Any) -> int | None:
    text = _clean_text(document)
    if not text:
        return None
    match = TOTAL_RE.search(text)
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def _total_pages(document: Any) -> int:
    pages = [
        int(value)
        for href in document.xpath("//a/@href")
        if "/category/" in href or "/listing" in href
        for value in PAGE_RE.findall(href or "")
    ]
    return max(pages) if pages else 1


def _description(node: Any | None) -> str | None:
    if node is None:
        return None
    clone = lxml_html.fromstring(lxml_html.tostring(node, encoding="unicode"))
    for link in clone.xpath('.//a[contains(@class, "show-more-button")]'):
        link.drop_tree()
    return strip_html(lxml_html.tostring(clone, encoding="unicode"))


def _address(document: Any) -> str | None:
    for item in document.xpath('//ul[contains(@class, "info-map")]/li'):
        text = _clean_text(item)
        if not text:
            continue
        text = re.sub(r"^Adres\s*", "", text)
        return text.strip() or None
    return None


def _split_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    city = parts[0] if parts else None
    province = None
    for part in parts[1:]:
        if normalize_text(part) in {"polska", "poland"}:
            continue
        if province is None:
            province = part
    return city, province


def _property_type(label: str | None) -> PropertyType:
    if not label:
        return "other"
    return PROPERTY_TYPES.get(normalize_text(label), "other")


def _gallery_images(document: Any) -> list[str]:
    images: list[str] = []
    for href in document.xpath('//div[@id="gallery-box"]//a/@href'):
        url = href.strip()
        if url and url not in images and "/storage/products/" in url:
            images.append(url)
    return images[:20]


def _valid_point(lat: float | None, lng: float | None) -> tuple[float | None, float | None]:
    if lat is None or lng is None or lat == 0 or lng == 0:
        return None, None
    return lat, lng


def _case_number(description: str | None) -> str | None:
    if not description:
        return None
    match = CASE_RE.search(description)
    if match is None:
        return None
    value = match.group(1).strip()
    while re.search(r"[a-ząćęłńóśźż]{3,}$", value):
        value = re.sub(r"[a-ząćęłńóśźż]{3,}$", "", value).strip()
    return value.rstrip(".,;:") or None


def _area_from_description(description: str | None) -> float | None:
    if not description:
        return None
    match = re.search(
        r"powierzchni\w*\s+u[żz]ytkow\w*[^0-9]{0,40}([\d][\d\s\u00a0.,]*)\s*m",
        description,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return parse_area(match.group(1))


def _land_area_from_description(description: str | None) -> float | None:
    if not description:
        return None
    match = re.search(
        r"dzia[łl]\w*[^.]{0,80}?o\s+(?:[łl]ącznej\s+)?powierzchni\s*"
        r"([\d][\d\s\u00a0.,]*)\s*(m2|m²|ha|ar)",
        description,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return parse_area(f"{match.group(1)} {match.group(2)}")


def _polish_date(value: str | None) -> int | None:
    if not value:
        return None
    match = DATE_RE.search(value)
    if match is None:
        return None
    day = int(match.group(1))
    month_key = normalize_text(match.group(2))[:3]
    month = MONTHS.get(month_key)
    if month is None:
        return None
    try:
        parsed = datetime(int(match.group(3)), month, day, tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _auction_date(description: str | None) -> int | None:
    if not description:
        return None
    match = re.search(
        r"otwarcie[\s\S]{0,400}?(?:w\s+dniu|dnia)\s+(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})",
        description,
        re.IGNORECASE,
    )
    if match is None:
        return None
    month = MONTHS.get(normalize_text(match.group(2))[:3])
    if month is None:
        return None
    try:
        parsed = datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _views(document: Any) -> int | None:
    text = _clean_text(document)
    if not text:
        return None
    match = re.search(r"Odwiedziny:\s*(\d+)", text)
    return int(match.group(1)) if match else None
