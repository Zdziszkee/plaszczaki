"""Domy.pl adapter (server-rendered search and detail pages)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, Market, OfferType, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_floor,
    parse_price,
    parse_rooms,
    safe_float,
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://domy.pl"

# (URL slug, transaction, offer type, property type)
CATEGORIES: tuple[tuple[str, str, OfferType, PropertyType], ...] = (
    ("mieszkania", "sprzedaz", "sale", "flat"),
    ("mieszkania", "wynajem", "rent", "flat"),
    ("domy", "sprzedaz", "sale", "house"),
    ("domy", "wynajem", "rent", "house"),
    ("dzialki", "sprzedaz", "sale", "plot"),
    ("lokale", "sprzedaz", "sale", "commercial"),
    ("lokale", "wynajem", "rent", "commercial"),
    ("garaze", "sprzedaz", "sale", "garage"),
    ("garaze", "wynajem", "rent", "garage"),
    ("pokoje", "wynajem", "rent", "room"),
)

_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_EXTERNAL_ID_RE = re.compile(r"/dol(\d+)/?$")
_OFFER_NUMBER_RE = re.compile(r"Numer ogłoszenia:\s*(\S+)")
_ROOMS_IN_URL_RE = re.compile(r"-(\d+)-pokoje-")
_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")

_PROPERTY_LABELS: dict[str, tuple[str, ...]] = {
    "flat": ("powierzchnia mieszkalna", "powierzchnia uzytkowa", "powierzchnia"),
    "house": ("powierzchnia mieszkalna", "powierzchnia uzytkowa", "powierzchnia"),
    "plot": ("powierzchnia", "powierzchnia dzialki"),
    "commercial": ("powierzchnia", "powierzchnia uzytkowa"),
    "garage": ("powierzchnia",),
    "room": ("powierzchnia",),
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


_STREET_PREFIX_RE = re.compile(r"^(?:ul\.|al\.|os\.|pl\.|rondo)\s*", re.IGNORECASE)


def _street_name(value: str | None) -> str | None:
    """Strip a Polish street-type prefix from a location fragment."""
    if not value:
        return None
    return _STREET_PREFIX_RE.sub("", value.strip()) or None


def _text(node: Any) -> str | None:
    if node is None:
        return None
    return _clean(node.text_content())


def _first_text(nodes: list[Any]) -> str | None:
    return _text(nodes[0]) if nodes else None


def _first_attr(nodes: list[Any], name: str) -> str | None:
    for node in nodes:
        value = node.get(name)
        if value:
            return value.strip()
    return None


def _per_m2(value: str | None) -> int | None:
    """Parse a price-per-square-metre string without losing decimals."""
    amount = parse_area(value)
    return int(amount) if amount is not None else None


def _parse_pl_date(value: str | None) -> int | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        return None


def _rooms_from_url(url: str) -> int | None:
    match = _ROOMS_IN_URL_RE.search(url)
    if match is not None:
        return int(match.group(1))
    if "kawalerka" in url:
        return 1
    return None


def _split_title_location(title: str, city: str | None) -> tuple[str | None, str | None]:
    """Return (street, district) from Domy's comma-separated headline."""
    tail = title.split(":", 1)[-1]
    parts = [part.strip() for part in tail.split(",") if part.strip()]
    if not parts:
        return None, None

    street = _street_name(parts[-1])
    if city and street and normalize_text(street) == normalize_text(city):
        street = None
    if len(parts) >= 4:
        district = parts[2]
    elif len(parts) == 3:
        district = parts[1]
    else:
        district = None
    return street, district


class DomyAdapter:
    """Parse Domy.pl's server-rendered HTML."""

    id = "domy"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            for slug, transaction, offer_type, property_type in CATEGORIES:
                queries.append(
                    Query(
                        key=f"{region}:{transaction}:{slug}",
                        url=f"{BASE_URL}/{slug}-{transaction}-{region}-pl",
                        region=region,
                        offer_type=offer_type,
                        property_type=property_type,
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """Domy.pl uses two bracket parameters for the date-descending sort."""
        return query.with_param("ps[sort_order]", "date").with_param("ps[sort_asc]", "0")

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        articles = tree.xpath(
            '//article[contains(concat(" ", normalize-space(@class), " "), " propertyBox ")]',
        )
        items = [self._summary(article, query) for article in articles]

        total_pages = self._total_pages(tree, query.page)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        if not tree.xpath('//article[@id="propertyDetails"]'):
            raise ParseError("Domy detail page is missing #propertyDetails")

        params = self._params(tree)
        title = _first_text(tree.xpath('//section[contains(@class, "summaryTop")]//h1'))
        province = _first_text(
            tree.xpath(
                '//div[contains(@class, "paramsItem")][b[contains(., "Województwo")]]//a',
            ),
        )
        location = _first_text(
            tree.xpath(
                '//div[contains(@class, "paramsItem")][b[contains(., "Lokalizacja")]]//a',
            ),
        )

        city = location or summary.city
        street, district = _split_title_location(title or "", city)
        voivodeship = self.regions.scoped_voivodeship(province, summary.voivodeship)

        lat = safe_float(_first_attr(tree.xpath('//div[@id="property-map"]'), "data-lat"))
        lng = safe_float(_first_attr(tree.xpath('//div[@id="property-map"]'), "data-lng"))

        description = _first_text(tree.xpath('//div[@id="description"]'))
        area = self._area(params, summary.property_type)
        land_area = parse_area(params.get("powierzchnia dzialki"))
        if summary.property_type == "plot" and area is not None:
            land_area, area = area, None

        price = parse_price(params.get("cena"))
        price_kind = "call" if price is None else "asking"
        market = self._market(params.get("rynek pierwotny"))
        floor, floor_label = parse_floor(params.get("pietro"))

        published_at = _parse_pl_date(
            _first_text(tree.xpath('//span[contains(., "Data dodania")]')),
        )
        modified_at = _parse_pl_date(
            _first_text(tree.xpath('//span[contains(., "Data aktualizacji")]')),
        )
        offer_number = _OFFER_NUMBER_RE.search(
            " ".join(tree.xpath('//div[contains(@class, "baseParam")]//text()')),
        )

        extra = dict(summary.extra)
        extra["domy"] = {
            "offer_number": offer_number.group(1) if offer_number else None,
            "property_type": summary.property_type,
        }

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            market=market,
            price_kind=price_kind,
            price_unit="monthly" if summary.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_per_m2(params.get("cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(params.get("liczba pokoi")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(params.get("rok budowy")),
            voivodeship=voivodeship,
            city=city,
            district=district,
            street=street,
            address_text=", ".join(part for part in (street, city) if part) or location,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(tree),
            published_at=published_at or summary.published_at,
            modified_at=modified_at,
            extra=extra,
        )

    def _summary(self, article: Any, query: Query) -> Listing:
        link = (
            article.xpath(
                './/a[contains(concat(" ", normalize-space(@class), " "), " property_link ")]'
            )
            or [None]
        )[0]
        href = link.get("href") if link is not None else None
        if not href:
            raise ParseError("Domy listing card is missing its link")

        id_match = _EXTERNAL_ID_RE.search(href)
        external_id = id_match.group(1) if id_match else href.rstrip("/").rsplit("/", 1)[-1]
        title = link.get("title") if link is not None else None

        location = _text(link)
        city = location.split(",", 1)[0].strip() if location else None
        street = None
        district = None
        if location:
            parts = [part.strip() for part in location.split(",") if part.strip()]
            if len(parts) >= 2:
                street = _street_name(parts[-1])
            if len(parts) >= 3:
                district = parts[1]

        price = parse_price(
            _first_text(
                article.xpath(
                    './/span[contains(concat(" ", normalize-space(@class), " "), " price ")]'
                )
            )
        )
        if price is None and article.xpath('.//span[contains(@class, "zapytaj")]'):
            price_kind = "call"
        else:
            price_kind = "asking"

        area = parse_area(
            _first_text(
                article.xpath(
                    './/span[contains(concat(" ", normalize-space(@class), " "), " area ")]'
                )
            )
        )
        land_area = None
        if query.property_type == "plot":
            land_area, area = area, None

        floor_text = _first_text(
            article.xpath('.//span[contains(concat(" ", normalize-space(@class), " "), " rooms ")]')
        )
        floor, floor_label = parse_floor(
            floor_text.replace("piętro:", "").strip() if floor_text else None,
        )
        rooms = _rooms_from_url(href)

        lat = safe_float(
            _first_attr(
                article.xpath(
                    './/div[contains(concat(" ", normalize-space(@class), " "), " GoogleMap ")]'
                ),
                "data-lat",
            )
        )
        lng = safe_float(
            _first_attr(
                article.xpath(
                    './/div[contains(concat(" ", normalize-space(@class), " "), " GoogleMap ")]'
                ),
                "data-lng",
            )
        )

        images = [
            src
            for src in article.xpath(".//img/@data-original")
            if isinstance(src, str) and "static.domy.pl/t.gif" not in src
        ]

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=href,
            title=_clean(title),
            description=_first_text(
                article.xpath(
                    './/span[contains(concat(" ", normalize-space(@class), " "), " descriptionShort ")]'
                ),
            ),
            offer_type=query.offer_type,
            property_type=query.property_type,
            market="unknown",
            price_kind=price_kind,
            price_unit="monthly" if query.offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_per_m2(
                _first_text(
                    article.xpath(
                        './/span[contains(concat(" ", normalize-space(@class), " "), " pricem2 ")]'
                    )
                ),
            ),
            area_m2=area,
            land_area_m2=land_area,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            voivodeship=query.region,
            city=city,
            district=district,
            street=street,
            address_text=location,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            published_at=_parse_pl_date(
                _first_text(
                    article.xpath(
                        './/span[contains(concat(" ", normalize-space(@class), " "), " addedAt ")]'
                    )
                ),
            ),
            extra={"domy": {"transaction": query.offer_type}},
        )

    @staticmethod
    def _total_pages(tree: Any, fallback: int) -> int:
        pages = [int(value) for value in _PAGE_RE.findall(" ".join(tree.xpath("//a/@href")))]
        return max(pages, default=fallback)

    @staticmethod
    def _params(tree: Any) -> dict[str, str]:
        params: dict[str, str] = {}
        for item in tree.xpath('//div[contains(@class, "paramsItem")]'):
            strong = item.xpath(".//strong")
            if not strong:
                continue
            label = (item.text_content() or "").split(":", 1)[0]
            key = normalize_text(label)
            value = _clean(strong[0].text_content())
            if key and value:
                params.setdefault(key, value)
        return params

    @staticmethod
    def _area(params: dict[str, str], property_type: PropertyType) -> float | None:
        for label in _PROPERTY_LABELS.get(property_type, ("powierzchnia",)):
            area = parse_area(params.get(label))
            if area is not None:
                return area
        return None

    @staticmethod
    def _market(value: str | None) -> Market:
        normalized = normalize_text(value or "")
        if normalized in {"tak", "yes", "true"}:
            return "primary"
        if normalized in {"nie", "no", "false"}:
            return "secondary"
        return "unknown"

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        for node in tree.xpath('//section[@id="imageBoxShow"]//img'):
            src = node.get("data-original") or node.get("src")
            if src and "static.domy.pl/t.gif" not in src and src not in images:
                images.append(src)
        return images[:20]
