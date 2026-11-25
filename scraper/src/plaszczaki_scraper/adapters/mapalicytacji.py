"""Mapa Licytacji adapter (bailiff and tax-auction map).

The portal aggregates komornik and urząd skarbowy auctions over Poland and
publishes regional listings plus a detail page per auction.  Listing cards
already carry the price, category, address and the auction term, while the
detail page embeds schema.org ``RealEstateListing`` JSON-LD with a postal
address and exact coordinates (``about.geo``) — the main reason this source is
worth scraping.

The site deliberately omits building numbers, debtor names, case numbers and
the court register number, so those fields stay ``None`` unless the page ever
starts exposing them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    safe_float,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://mapalicytacji.pl"
LISTING_URL = f"{BASE_URL}/wojewodztwo/{{slug}}"
PAGE_PARAM = "strona"
PAGE_SIZE = 60

WARSAW = ZoneInfo("Europe/Warsaw")

# Registry region id -> site path slug.  Only these voivodeships are crawled.
SITE_REGIONS: dict[str, str] = {
    "malopolskie": "malopolskie",
    "slaskie": "slaskie",
}

DETAIL_RE = re.compile(r"/(?:licytacja|licytacja-skarbowa)/([^/?#]+)")
POSTAL_RE = re.compile(r"(\d{2}-\d{3})\s*(.*)$")
CITY_SUFFIX_RE = re.compile(r"\s+gmina?\s+.*$|\s+gm\..*$", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
LISTING_COUNT_RE = re.compile(
    r"Lista licytacji\s*\(\s*(?:(\d+)\s*[\u2013-]\s*(\d+)\s*z\s*)?(\d+)\s*\)",
)
PAGER_RE = re.compile(r"[?&]strona=(\d+)")
AREA_RE = re.compile(r"(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*m")
CASE_RE = re.compile(
    r"sygn(?:atura|\.)?\s*(?:akt\.?\s*)?([A-Za-z0-9][A-Za-z0-9/\-]{1,30})",
    re.IGNORECASE,
)

CATEGORY_TYPES: dict[str, PropertyType] = {
    "mieszkania": "flat",
    "domy": "house",
    "dzialki i grunty": "plot",
    "lokale uzytkowe": "commercial",
    "magazyny i hale": "commercial",
    "garaze": "garage",
    "inne": "other",
}

PROPERTY_KEYWORDS: dict[PropertyType, tuple[str, ...]] = {
    "flat": (
        "mieszkanie",
        "mieszkania",
        "lokal mieszkalny",
        "lokalu mieszkalnego",
        "kawalerka",
        "apartament",
    ),
    "house": (
        "dom",
        "domu",
        "budynek mieszkalny",
        "budynkiem mieszkalnym",
        "zabudowana",
        "zabudowany",
    ),
    "plot": (
        "dzialka",
        "dzialki",
        "grunt",
        "grunty",
        "nieruchomosc gruntowa",
        "laki",
        "las",
    ),
    "commercial": (
        "lokal uzytkowy",
        "lokalu uzytkowego",
        "uslugowy",
        "handlowy",
        "biuro",
        "magazyn",
        "hala",
    ),
    "garage": ("garaz", "garazu"),
}

AUCTION_ROUNDS = {
    "pierwsza": 1,
    "druga": 2,
    "trzecia": 3,
    "czwarta": 4,
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "text_content"):
        value = value.text_content()
    text = " ".join(str(value).replace("\u00a0", " ").split())
    return text or None


def _first_text(nodes: Sequence[Any]) -> str | None:
    for node in nodes:
        text = _text(node)
        if text:
            return text
    return None


def _value_text(cell: Any) -> str | None:
    """Text of a detail-table value cell without the "inne licytacje" link."""
    text = _text(cell)
    if not text:
        return None
    return _text(text.split("·", 1)[0])


def _parse_html(html: str) -> lxml_html.HtmlElement:
    if not html or not html.strip():
        raise ParseError("empty page")
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ParseError(f"Mapa Licytacji page is not valid HTML: {exc}") from exc


def _json_ld_nodes(document: Any) -> list[dict[str, Any]]:
    """Collect every JSON-LD object, including lists, @graph and nested entities."""
    nodes: list[dict[str, Any]] = []
    for script in document.xpath('//script[contains(@type, "ld+json")]'):
        raw = script.text or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            continue

        stack: list[Any] = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                nodes.append(value)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return nodes


def _node_types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _listing_node(nodes: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the RealEstateListing (or equivalent) object carrying the address."""
    decorated = [
        node
        for node in nodes
        if _node_types(node) & {"RealEstateListing", "Product", "Offer"}
        and isinstance(node.get("about") or node.get("mainEntity") or node.get("address"), dict)
    ]
    if decorated:
        return next(
            (node for node in decorated if "RealEstateListing" in _node_types(node)),
            decorated[0],
        )
    return next(
        (
            node
            for node in nodes
            if isinstance(node.get("address"), dict)
            or (isinstance(node.get("about"), dict) and "address" in node["about"])
        ),
        None,
    )


def _location_parts(node: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Find postal address and geo coordinates, following about/mainEntity nesting."""
    address: dict[str, Any] = {}
    geo: dict[str, Any] = {}
    stack: list[Any] = [node] if node else []
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            candidate = value.get("address")
            if not address and isinstance(candidate, dict):
                address = candidate
            candidate = value.get("geo")
            if not geo and isinstance(candidate, dict):
                geo = candidate
            for key in ("about", "mainEntity", "itemOffered", "location"):
                nested = value.get(key)
                if isinstance(nested, dict | list):
                    stack.append(nested)
        elif isinstance(value, list):
            stack.extend(value)
    return address, geo


def _offer(node: dict[str, Any] | None) -> dict[str, Any]:
    if not node:
        return {}
    value = node.get("offers")
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, dict)), None)
    return value if isinstance(value, dict) else {}


def _seller_name(node: dict[str, Any] | None) -> str | None:
    seller = _offer(node).get("seller")
    if not isinstance(seller, dict):
        return None
    return _text(seller.get("name"))


def _sale_event(nodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return next((node for node in nodes if "SaleEvent" in _node_types(node)), {})


def _datetime_ms(value: str | None) -> int | None:
    """Parse "26.11.2026 09:00" as Europe/Warsaw wall-clock time."""
    if not value:
        return None
    match = DATE_RE.search(value)
    if match is None:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            tzinfo=WARSAW,
        )
    except ValueError:
        return None
    return int(moment.timestamp() * 1000)


def _split_address(value: str | None) -> tuple[str | None, str | None, str | None]:
    """Split "Sienkiewicza, 32-500 Chrzanów" into street, postal code and city."""
    text = _text(value)
    if not text:
        return None, None, None

    parts = [part.strip() for part in text.split(",") if part.strip()]
    for index, part in enumerate(parts):
        match = POSTAL_RE.search(part)
        if match is None:
            continue
        postal = match.group(1)
        city = _text(match.group(2))
        if city:
            city = CITY_SUFFIX_RE.sub("", city) or city
        street = parts[0] if index > 0 else None
        return street, postal, city

    # No postal code: the last comma-separated segment is the locality.
    return None, None, parts[-1] if parts else None


def _category_type(category: str | None) -> PropertyType | None:
    if not category:
        return None
    return CATEGORY_TYPES.get(normalize_text(category))


def _infer_property_type(
    title: str | None,
    category: str | None,
    fallback: PropertyType,
) -> PropertyType:
    from_category = _category_type(category)
    if from_category is not None and from_category != "other":
        return from_category

    haystack = normalize_text(title or "")
    if haystack:
        scores = {
            property_type: sum(haystack.count(normalize_text(keyword)) for keyword in keywords)
            for property_type, keywords in PROPERTY_KEYWORDS.items()
        }
        best_type, best_score = max(scores.items(), key=lambda item: item[1])
        if best_score > 0:
            return best_type
    return from_category or fallback


def _point(lat: float | None, lng: float | None) -> tuple[float | None, float | None]:
    if lat is None or lng is None or lat == 0 or lng == 0:
        return None, None
    return lat, lng


def _badge_texts(anchor: Any) -> dict[str, str]:
    """Map badge class suffixes ("cat", "e", "s", "druga", ...) to their label."""
    badges: dict[str, str] = {}
    for badge in anchor.xpath('.//span[contains(@class, "badge")]'):
        classes = (badge.get("class") or "").split()
        label = _text(badge)
        for name in classes:
            if name != "badge" and name not in badges:
                badges[name] = label or ""
    return badges


def _auction_round(badges: dict[str, str]) -> int | None:
    for key, label in badges.items():
        if key in AUCTION_ROUNDS:
            return AUCTION_ROUNDS[key]
        normalized = normalize_text(label)
        for word, round_number in AUCTION_ROUNDS.items():
            if normalized.startswith(word):
                return round_number
    return None


def _drop_page_param(url: str) -> str:
    parts = urlsplit(url)
    query = [
        item for item in parts.query.split("&") if item and item.split("=", 1)[0] != PAGE_PARAM
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), parts.fragment))


def _area_from_text(value: str | None) -> float | None:
    if not value:
        return None
    match = AREA_RE.search(value)
    if match is None:
        return None
    return parse_area(match.group(1))


def _mode(*values: str | None) -> str | None:
    """Canonicalise the auction mode label ("e-licytacja" / "stacjonarna")."""
    text = normalize_text(" ".join(value for value in values if value))
    if "e licytacja" in text:
        return "e-licytacja"
    if "stacjonarna" in text:
        return "stacjonarna"
    return None


def _detail_pairs(document: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in document.xpath('//div[contains(@class, "detail")]//table//tr'):
        cells = row.xpath("./td")
        if len(cells) < 2:
            continue
        label = normalize_text(_text(cells[0]) or "")
        value = _value_text(cells[1])
        if label and value and label not in pairs:
            pairs[label] = value
    return pairs


class MapalicytacjiAdapter:
    """Parse Mapa Licytacji regional listings and JSON-LD detail pages."""

    id = "mapalicytacji"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            region_id = self.regions.canonical_voivodeship(region)
            if region_id is None or not self.regions.has_region(region_id):
                continue
            slug = SITE_REGIONS.get(region_id)
            if slug is None:
                continue
            queries.append(
                Query(
                    key=f"{region_id}:all",
                    url=LISTING_URL.format(slug=slug),
                    region=region_id,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        if page <= 1:
            return replace(query.with_url(_drop_page_param(query.url)), page=1)
        return replace(query.with_param(PAGE_PARAM, str(page)), page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        document = _parse_html(html)
        cards = document.xpath(
            '//article[contains(concat(" ", normalize-space(@class), " "), " card ")]',
        )
        heading = document.xpath('//h2[contains(normalize-space(.), "Lista licytacji")]')
        if not cards and not heading:
            raise ParseError("Mapa Licytacji listing page not recognised")

        items: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            listing = self._summary(card, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        total_items = self._total_items(heading)
        pager_pages = {
            int(match.group(1))
            for href in document.xpath("//nav[contains(@class, 'pager')]//a/@href")
            if (match := PAGER_RE.search(href))
        }
        if total_items is not None:
            pager_pages.add(max(1, -(-total_items // PAGE_SIZE)))
        total_pages = max(pager_pages) if pager_pages else 1

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items if total_items is not None else len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        document = _parse_html(html)
        nodes = _json_ld_nodes(document)
        node = _listing_node(nodes)
        pairs = _detail_pairs(document)
        headings = document.xpath("//h1")
        if node is None and not pairs and not headings:
            raise ParseError("Mapa Licytacji detail page not recognised")

        address, geo = _location_parts(node)
        street_address = _text(address.get("streetAddress"))
        postal = _text(address.get("postalCode"))
        locality = _text(address.get("addressLocality"))
        region_name = _text(address.get("addressRegion"))
        trade_street, trade_postal, trade_city = _split_address(pairs.get("adres"))
        street = street_address or trade_street or summary.street
        city = locality or trade_city or summary.city
        postal = postal or trade_postal

        lat, lng = _point(
            safe_float(geo.get("latitude")),
            safe_float(geo.get("longitude")),
        )

        address_text = pairs.get("adres")
        if not address_text:
            line = " ".join(part for part in (postal, city) if part) or None
            address_text = (
                ", ".join(part for part in (street_address, line) if part) or summary.address_text
            )

        category = pairs.get("kategoria")
        hero = document.xpath('//div[contains(@class, "hero")]')
        badges = _badge_texts(hero[0]) if hero else {}
        category = category or badges.get("cat")
        title = summary.title or _text((node or {}).get("name"))

        event = _sale_event(nodes)
        auction_date = (
            _datetime_ms(pairs.get("zakonczenie licytacji"))
            or parse_datetime_ms(event.get("endDate"))
            or parse_datetime_ms(_offer(node).get("validThrough"))
        )

        price_text = pairs.get("cena wywolania")
        price = parse_price(price_text) or parse_price(_offer(node).get("price"))
        area = _area_from_text(pairs.get("powierzchnia"))

        description = _text((node or {}).get("description")) or summary.description
        voivodeship = self._voivodeship(region_name, city, lat, lng) or summary.voivodeship
        seller = _seller_name(node)

        extra = dict(summary.extra)
        extra["mapalicytacji"] = {
            **dict(extra.get("mapalicytacji") or {}),
            "category": category,
            "mode": _mode(pairs.get("tryb licytacji"), badges.get("e"), badges.get("s")),
            "postal_code": postal,
            "starting_price_text": price_text,
            "estimated_price_pln": parse_price(pairs.get("suma oszacowania")),
            "source_url": (node or {}).get("url"),
        }

        return replace(
            summary,
            title=title,
            description=description,
            offer_type="auction",
            property_type=_infer_property_type(title, category, summary.property_type),
            market="secondary",
            price_kind="starting" if price is not None else "call",
            price_unit="total",
            price_pln=price if price is not None else summary.price_pln,
            area_m2=area or summary.area_m2,
            voivodeship=voivodeship,
            city=city,
            street=street,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            seller_type="government",
            seller_name=seller or summary.seller_name,
            published_at=parse_datetime_ms((node or {}).get("datePosted")) or summary.published_at,
            auction_date=auction_date or summary.auction_date,
            auction_round=_auction_round(badges) or summary.auction_round,
            case_number=_case_number(pairs, description) or summary.case_number,
            extraction_method="adapter",
            extra=extra,
        )

    def _voivodeship(
        self,
        region_name: str | None,
        city: str | None,
        lat: float | None,
        lng: float | None,
    ) -> str | None:
        if not region_name:
            return None
        canonical = self.regions.canonical_voivodeship(region_name)
        if canonical is not None:
            return canonical
        by_city = self.regions.match_city(city) or self.regions.region_for_point(lat, lng)
        if by_city is not None:
            return by_city
        # Keep an out-of-scope voivodeship as reported; the runner filters it out.
        return normalize_text(region_name) or None

    def _summary(self, card: Any, query: Query) -> Listing | None:
        href = next(
            (value for value in card.xpath(".//a/@href") if DETAIL_RE.search(value or "")),
            None,
        )
        if not href:
            return None
        match = DETAIL_RE.search(href)
        if match is None:
            return None
        url = urljoin(f"{BASE_URL}/", href)

        title = _first_text(card.xpath(".//h3"))
        badges = _badge_texts(card)
        category = badges.get("cat")
        address = _first_text(card.xpath('.//div[contains(@class, "addr")]')[0:1])
        street, postal, city = _split_address(address)

        price = parse_price(_first_text(card.xpath('.//span[contains(@class, "price")]')))
        term = _first_text(card.xpath('.//span[contains(@class, "date")]'))
        area_text = next(
            (
                value
                for value in (
                    _text(node) for node in card.xpath('.//div[contains(@class, "addr")]')[1:]
                )
                if value and "m²" in value
            ),
            None,
        )

        return Listing(
            source_id=self.source_id,
            external_id=match.group(1),
            url=url,
            title=title,
            offer_type="auction",
            property_type=_infer_property_type(title, category, "other"),
            market="secondary",
            price_kind="starting" if price is not None else "call",
            price_unit="total",
            price_pln=price,
            area_m2=_area_from_text(area_text),
            voivodeship=query.region,
            city=city,
            street=street,
            address_text=address,
            seller_type="government",
            auction_date=_datetime_ms(term),
            auction_round=_auction_round(badges),
            extraction_method="adapter",
            extra={
                "mapalicytacji": {
                    "category": category,
                    "mode": _mode(badges.get("e"), badges.get("s")),
                    "postal_code": postal,
                    "auction_term_text": term,
                    "share": badges.get("udzial"),
                },
            },
        )

    @staticmethod
    def _total_items(heading: list[Any]) -> int | None:
        if not heading:
            return None
        match = LISTING_COUNT_RE.search(_text(heading[0]) or "")
        if match is None:
            return None
        return int(match.group(3))


def _case_number(pairs: dict[str, str], description: str | None) -> str | None:
    for label, value in pairs.items():
        if "sygnatura" in label or "sygn akt" in label or "nr sprawy" in label:
            return value
    if not description:
        return None
    match = CASE_RE.search(description)
    if match is None:
        return None
    return match.group(1).rstrip(".,;:")
