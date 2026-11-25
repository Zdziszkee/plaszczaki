"""Syndyk Aukcje adapter (Listivo/WordPress classifieds + detail pages).

The site is a free-to-read classifieds board for syndyk and komornik sales.
Search cards are server rendered, while the exact coordinates only appear on
the detail page inside the OpenStreetMap widget.
"""

from __future__ import annotations

import html as html_module
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

BASE_URL = "https://syndykaukcje.pl"
LISTING_URL = f"{BASE_URL}/lista-ogloszen/nieruchomosci/"
PAGE_SIZE = 36

REGION_SLUGS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}

SLUG_RE = re.compile(r"/ogloszenia/([^/]+)/")
MODEL_RE = re.compile(r'model-id="(\d+)"')
POSITION_RE = re.compile(
    r""":position=(?:"|')\{"lat":(-?[\d.]+),"lng":(-?[\d.]+)\}(?:"|')""",
)
CASE_RE = re.compile(
    r"sygn[a-z]*\.?\s*akt\.?\s*([A-Za-z0-9][A-Za-z0-9/\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9/\-]*){0,3})",
)
VIEWS_RE = re.compile(r"(\d+)\s*Wyświetleń", re.IGNORECASE)

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

# Keyword weights used to infer a property type from free text.  The portal
# only offers a single "Nieruchomości" category, so the title/description is
# the most reliable signal available before the detail page is inspected.
PROPERTY_KEYWORDS: dict[PropertyType, tuple[str, ...]] = {
    "flat": (
        "mieszkanie",
        "mieszkania",
        "mieszkaniu",
        "lokal mieszkalny",
        "lokalu mieszkalnego",
        "apartament",
        "kawalerka",
    ),
    "house": (
        "dom",
        "domu",
        "dom jednorodzinny",
        "domu jednorodzinnego",
        "dom wolnostojacy",
        "budynek mieszkalny jednorodzinny",
        "szeregowka",
        "szeregowca",
    ),
    "plot": (
        "dzialka",
        "dzialki",
        "dzialke",
        "grunt",
        "gruntowa",
        "nieruchomosc gruntowa",
        "nieruchomosci gruntowe",
    ),
    "commercial": (
        "lokal uzytkowy",
        "lokalu uzytkowego",
        "lokale uzytkowe",
        "biuro",
        "biurowy",
        "magazyn",
        "hala",
        "sklep",
        "uslugowy",
        "nieruchomosc komercyjna",
    ),
    "garage": ("garaz", "garazu", "garaże", "garaze"),
    "room": ("pokoj", "pokoju"),
}


class SyndykAukcjeAdapter:
    """Parse Listivo search cards and the coordinate-bearing detail pages."""

    id = "syndykaukcje"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            slug = REGION_SLUGS.get(region)
            if slug is None:
                continue
            queries.append(
                Query(
                    key=f"{region}:nieruchomosci",
                    url=f"{LISTING_URL}?wojewodztwo={quote(slug)}",
                    region=region,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def newest(self, query: Query) -> Query:
        """Listivo's Polish newest-sort parameter."""
        return query.with_param("wertuj-wedlug", "najnowsze")

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return query
        return replace(query.with_param("paginacja", str(page)), page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        document = _parse_html(html)
        items: list[Listing] = []
        seen: set[str] = set()

        anchors = document.xpath(
            '//a[contains(@class, "listivo-listing-card-row-v2")'
            ' and starts-with(@href, "https://syndykaukcje.pl/ogloszenia/")]',
        )
        for anchor in anchors:
            href = (anchor.get("href") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            try:
                items.append(self._summary(anchor, href, query))
            except ParseError:
                continue

        if not items and not anchors:
            raise ParseError("Syndyk Aukcje page has no result cards")

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=query.page if len(items) < PAGE_SIZE else query.page + 1,
            total_items=None,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        document = _parse_html(html)

        title = _first_text(document.xpath('//h1[contains(@class, "listivo-listing-name")]'))
        price = parse_price(
            _first_text(document.xpath('//div[contains(@class, "listivo-listing-price")]')),
        )
        address = _address(document)
        city, county = _split_address(address)

        lat, lng = _coordinates(html)
        lat, lng = _valid_point(lat, lng)

        description = _description(document)
        property_type = _infer_property_type(title, description, summary.property_type)
        voivodeship = self._voivodeship(address) or summary.voivodeship
        if voivodeship is None:
            voivodeship = self.regions.region_for_point(lat, lng)

        images = _gallery_images(html)
        extra = dict(summary.extra)
        extra["syndykaukcje"] = {
            "category": _first_text(
                document.xpath('//div[contains(@class, "listivo-listing-attribute-v3")]'),
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
            county=county or summary.county,
            address_text=address or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images or summary.images,
            case_number=_case_number(description),
            auction_date=_auction_date(description),
            extra=extra,
        )

    def _voivodeship(self, address: str | None) -> str | None:
        if not address:
            return None
        for part in address.split(","):
            region = self.regions.canonical_voivodeship(part.strip())
            if region is not None:
                return region
        return None

    def _summary(self, anchor: Any, href: str, query: Query) -> Listing:
        slug_match = SLUG_RE.search(href)
        if slug_match is None:
            raise ParseError(f"Syndyk Aukcje card has no listing slug: {href}")
        slug = slug_match.group(1)
        model_match = MODEL_RE.search(lxml_html.tostring(anchor, encoding="unicode"))
        external_id = model_match.group(1) if model_match else slug

        title = _first_text(
            anchor.xpath('.//h3[contains(@class, "listivo-listing-card-row-v2__name")]'),
        )
        address = _first_text(
            anchor.xpath(
                './/div[contains(@class, "listivo-listing-card-address-selector")]',
            ),
        )
        city, county = _split_address(address)
        description = strip_html(
            _first_text(
                anchor.xpath(
                    './/div[contains(@class, "listivo-listing-card-description-selector")]',
                ),
            ),
        )
        price = parse_price(
            _first_text(
                anchor.xpath('.//div[contains(@class, "listivo-listing-card-value-selector")]'),
            ),
        )
        seller_name = _first_text(
            anchor.xpath('.//div[contains(@class, "listivo-listing-card-row-v2__user")]//span'),
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=title,
            offer_type="auction",
            property_type=_infer_property_type(title, description, query.property_type),
            market="secondary",
            price_kind="starting",
            price_unit="total",
            price_pln=price,
            voivodeship=query.region,
            city=city,
            county=county,
            address_text=address,
            images=_card_images(anchor),
            seller_name=seller_name,
            extra={
                "syndykaukcje": {
                    "slug": slug,
                    "model_id": model_match.group(1) if model_match else None,
                },
            },
        )


def _parse_html(html: str) -> lxml_html.HtmlElement:
    if not html or not html.strip():
        raise ParseError("empty page")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ParseError(f"Syndyk Aukcje page is not valid HTML: {exc}") from exc


def _clean_text(node: Any) -> str | None:
    if node is None:
        return None
    text = node.text_content() if hasattr(node, "text_content") else str(node)
    cleaned = " ".join(text.split())
    return cleaned or None


def _first_text(nodes: list[Any]) -> str | None:
    for node in nodes:
        text = " ".join(node.split()) if isinstance(node, str) else _clean_text(node)
        if text:
            return text
    return None


def _address(document: Any) -> str | None:
    for node in document.xpath('//div[contains(@class, "listivo-listing-address")]//text()'):
        text = " ".join(node.split()).strip()
        if text and text.casefold() not in {"zobacz mapę", "zobacz mape"}:
            return text
    return None


def _split_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = re.sub(r"^\d{2}-\d{3}\s*", "", value.strip())
    parts = [part.strip() for part in text.split(",") if part.strip()]
    city = parts[0] if parts else None
    if city and normalize_text(city) in {"polska", "poland"}:
        city = None
    county = None
    for part in parts[1:]:
        normalized = normalize_text(part)
        if normalized in {"polska", "poland"}:
            continue
        if normalized.startswith("powiat ") and county is None:
            county = normalized.removeprefix("powiat ").strip() or None
    return city, county or None


def _coordinates(html: str) -> tuple[float | None, float | None]:
    match = POSITION_RE.search(html_module.unescape(html))
    if match is None:
        return None, None
    return safe_float(match.group(1)), safe_float(match.group(2))


def _valid_point(lat: float | None, lng: float | None) -> tuple[float | None, float | None]:
    if lat is None or lng is None or lat == 0 or lng == 0:
        return None, None
    return lat, lng


def _description(document: Any) -> str | None:
    for section in document.xpath('//div[contains(@class, "listivo-listing-section")]'):
        label = _first_text(
            section.xpath('./h3[contains(@class, "listivo-listing-section__label")]'),
        )
        if label and normalize_text(label) == "opis":
            node = section.xpath('.//div[contains(@class, "listivo-listing-section__text")]')
            if node:
                return strip_html(lxml_html.tostring(node[0], encoding="unicode"))
    return None


def _infer_property_type(
    title: str | None,
    description: str | None,
    fallback: PropertyType,
) -> PropertyType:
    haystack = normalize_text(f"{title or ''} {description or ''}")
    if not haystack:
        return fallback
    scores = {
        property_type: sum(haystack.count(keyword) for keyword in keywords)
        for property_type, keywords in PROPERTY_KEYWORDS.items()
    }
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return fallback
    return best_type


def _card_images(anchor: Any) -> list[str]:
    images: list[str] = []
    for srcset in anchor.xpath(".//img/@data-srcset"):
        url = _best_from_srcset(srcset)
        if url and url not in images:
            images.append(url)
    for src in anchor.xpath(".//img/@data-src"):
        if _is_listing_image(src) and src not in images:
            images.append(src)
    return images[:20]


def _best_from_srcset(srcset: str) -> str | None:
    candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
    return candidates[-1] if candidates else None


def _gallery_images(html: str) -> list[str]:
    images: list[str] = []
    for attribute in ("data-src", "src"):
        pattern = rf'{attribute}="(https://syndykaukcje\.pl/wp-content/uploads/[^"]+)"'
        for url in re.findall(pattern, html):
            if _is_listing_image(url) and url not in images:
                images.append(url)
    return images[:20]


def _is_listing_image(url: str) -> bool:
    lowered = url.casefold()
    if lowered.endswith(".svg"):
        return False
    return not any(token in lowered for token in ("logo", "banner", "ikona", "icon"))


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


def _auction_date(description: str | None) -> int | None:
    if not description:
        return None
    match = re.search(
        r"(?:licytacj|aukcj|przetarg|otwarcie)[\s\S]{0,300}?(?:w\s+dniu|dnia)\s+"
        r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})",
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
    match = VIEWS_RE.search(text)
    return int(match.group(1)) if match else None
