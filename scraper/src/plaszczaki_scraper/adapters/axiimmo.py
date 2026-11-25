"""AXI IMMO adapter (commercial land, warehouses and offices).

AXI IMMO is a WordPress site.  Offer details are exposed through schema.org
JSON-LD, and every detail page also initialises a Google Maps marker with the
precise coordinates.  Search pages render all cards server-side (100 per page),
so no admin-ajax or REST endpoint is needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import (
    Listing,
    OfferType,
    PriceKind,
    PriceUnit,
    PropertyType,
)
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    safe_float,
    safe_int,
    strip_html,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://www.axiimmo.com"

# category URL segment -> canonical property type
CATEGORIES: tuple[tuple[str, PropertyType], ...] = (
    ("grunty-inwestycyjne", "plot"),
    ("magazyny", "commercial"),
    ("biura", "commercial"),
)

# Category pages that are already scoped to the region in question.
REGION_PATHS: dict[str, dict[str, str]] = {
    "malopolskie": {
        "grunty-inwestycyjne": "malopolskie",
        "magazyny": "region-krakow",
        "biura": "malopolskie",
    },
    "slaskie": {
        "grunty-inwestycyjne": "slaskie",
        "magazyny": "region-gorny-slask",
        "biura": "slaskie",
    },
}

_COUNTY_PREFIXES = ("powiat ", "pow. ")
_OFFER_ID_RE = re.compile(r"-((?:d|mp|mm|bb|bk)\d+)/?$", re.IGNORECASE)
_MARKER_RE = re.compile(r"new google\.maps\.LatLng\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")


def _text(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(str(node.text_content()).split())


def _attr(node: Any, name: str) -> str | None:
    if node is None:
        return None
    value = node.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(nodes: list[Any]) -> Any:
    return nodes[0] if nodes else None


def _decimal(value: Any) -> float | None:
    """Parse a decimal amount without inserting or dropping digits."""
    if value is None:
        return None
    match = re.search(r"\d[\d\s\u00a0]*(?:[.,]\d+)?", str(value))
    if match is None:
        return None
    number = match.group(0).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    return safe_float(number)


def _county(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    for prefix in _COUNTY_PREFIXES:
        if normalize_text(text).startswith(normalize_text(prefix)):
            text = text[len(prefix) :]
            break
    return text.strip(" ,") or None


def _html_text(value: str | None) -> str | None:
    if not value:
        return None
    spaced = re.sub(
        r"<(?:br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return strip_html(spaced)


def _slug(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def _external_id(url: str) -> str:
    match = _OFFER_ID_RE.search(url)
    return match.group(1).lower() if match else _slug(url)


def _ld_nodes(doc: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        raw = script.text or script.text_content()
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        graph = payload.get("@graph") if isinstance(payload, dict) else None
        if isinstance(graph, list):
            nodes.extend(node for node in graph if isinstance(node, dict))
        elif isinstance(payload, dict):
            nodes.append(payload)
    return nodes


def _listing_node(doc: Any) -> dict[str, Any] | None:
    for node in _ld_nodes(doc):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(isinstance(item, str) and "RealEstateListing" in item for item in types):
            return node
    return None


def _breadcrumbs(doc: Any) -> list[str]:
    for node in _ld_nodes(doc):
        if node.get("@type") == "BreadcrumbList":
            names: list[str] = []
            for item in node.get("itemListElement") or []:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]).strip())
            return names
    return []


def _properties(node: dict[str, Any]) -> dict[str, list[tuple[Any, str | None]]]:
    props: dict[str, list[tuple[Any, str | None]]] = {}
    for item in node.get("additionalProperty") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        props.setdefault(str(item["name"]), []).append(
            (item.get("value"), item.get("unitText")),
        )
    return props


def _prop_value(
    props: dict[str, list[tuple[Any, str | None]]],
    name: str,
) -> Any:
    values = props.get(name)
    return values[0][0] if values else None


def _prop_m2(
    props: dict[str, list[tuple[Any, str | None]]],
    name: str,
) -> float | None:
    """Read a property expressed in m², falling back to a ``ha`` value."""
    values = props.get(name) or []
    for value, unit in values:
        if unit and normalize_text(unit) == "m2":
            parsed = parse_area(value)
            if parsed is not None:
                return parsed
    for value, unit in values:
        if unit and normalize_text(unit) == "ha":
            parsed = parse_area(value)
            if parsed is not None:
                return parsed
    return None


def _offer_type_from_title(value: str | None, fallback: OfferType) -> OfferType:
    text = normalize_text(value or "")
    if not text:
        return fallback
    if "wynajem" in text or "wynajecia" in text or "najem" in text:
        return "rent"
    if "dzierzawa" in text:
        return "lease"
    if "sprzedaz" in text or "kupno" in text:
        return "sale"
    return fallback


def _parse_search_location(value: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Split ``voivodeship, county|district`` (or ``city, district``)."""
    parts = [" ".join(part.split()) for part in value.split(",") if part.strip()]
    if not parts:
        return None, None, None, None

    voivodeship: str | None = None
    city: str | None = None
    county: str | None = None
    district: str | None = None

    if parts[0][0].islower():
        # AXI prints voivodeships in lowercase, cities capitalised.
        voivodeship = parts[0]

    if voivodeship is not None:
        for part in parts[1:]:
            if normalize_text(part).startswith(("powiat ", "pow ")):
                county = _county(part)
            elif district is None:
                district = part
        return voivodeship, county, None, district

    city = parts[0] or None
    for part in parts[1:]:
        if normalize_text(part).startswith(("powiat ", "pow ")):
            county = _county(part)
        elif district is None:
            district = part
    return None, county, city, district


class AxiImmoAdapter:
    """Parse AXI IMMO's JSON-LD backed offer pages."""

    id = "axiimmo"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            paths = REGION_PATHS.get(region)
            if paths is None:
                continue
            for category, property_type in CATEGORIES:
                slug = paths[category]
                queries.append(
                    Query(
                        key=f"{region}:{category}",
                        url=f"{BASE_URL}/{category}/{slug}/",
                        region=region,
                        offer_type="rent" if category in {"magazyny", "biura"} else "sale",
                        property_type=property_type,
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        """AXI IMMO's WordPress date-descending order."""
        return query.with_param("orderby", "date").with_param("order", "desc")

    def with_page(self, query: Query, page: int) -> Query:
        base = re.sub(r"/page/\d+/?$", "/", query.url.split("#", 1)[0])
        if page <= 1:
            return replace(query, url=base, page=1)
        return replace(query, url=f"{base.rstrip('/')}/page/{page}/", page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        doc = lxml_html.fromstring(html)
        intro = _first(doc.xpath('//*[contains(@class, "lch-layout__pagination-intro")]'))
        cards = doc.xpath('//article[contains(@class, "offer-block")]')
        if intro is None and not cards:
            raise ParseError("AXI IMMO search page has no offer list")

        items: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            item = self._summary(card, query)
            if item is None or item.url in seen:
                continue
            seen.add(item.url)
            items.append(item)

        total_items = self._total_items(intro)
        total_pages = self._total_pages(doc)
        if total_pages is None:
            total_pages = 1 if total_items is None else max(1, (total_items + 99) // 100)

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        doc = lxml_html.fromstring(html)
        node = _listing_node(doc)
        if node is None:
            raise ParseError("AXI IMMO detail page has no RealEstateListing JSON-LD")

        props = _properties(node)
        raw_address = node.get("address")
        address: dict[str, Any] = raw_address if isinstance(raw_address, dict) else {}
        breadcrumbs = _breadcrumbs(doc)
        url = str(node.get("url") or summary.url)
        property_type = self._property_type(url, summary.property_type)

        voivodeship, county, city, district = self._detail_location(
            address,
            breadcrumbs,
        )

        offer_type, price_pln, price_per_m2, price_unit, price_kind, currency = self._price(
            node,
            node.get("name") or summary.title,
            summary.offer_type,
        )

        if property_type == "plot":
            area_m2 = None
            land_area = _prop_m2(props, "Plot area")
        else:
            area_m2 = (
                _prop_m2(props, "Available area")
                or _prop_m2(props, "Total building area")
                or _prop_m2(props, "Total available space (building)")
            )
            land_area = _prop_m2(props, "Plot area")

        lat, lng = self._coordinates(doc, node)

        images: list[str] = []
        for image in node.get("image") or []:
            value = image.get("url") if isinstance(image, dict) else image
            if isinstance(value, str) and value.strip():
                absolute = urljoin(BASE_URL, value.strip())
                if absolute not in images:
                    images.append(absolute)

        seller = None
        provider = node.get("provider")
        if isinstance(provider, dict) and provider.get("name"):
            seller = str(provider["name"]).strip()

        extra = dict(summary.extra)
        axios_extra = dict(extra.get("axiimmo") or {})
        axios_extra.update(
            {
                "offer_id": _prop_value(props, "Offer ID"),
                "currency": currency,
                "offer": (node.get("offers") or [None])[0],
                "properties": {
                    key: [value for value, _unit in values] for key, values in props.items()
                },
                "breadcrumbs": breadcrumbs,
            },
        )
        extra["axiimmo"] = axios_extra

        return replace(
            summary,
            external_id=str(_prop_value(props, "Offer ID") or summary.external_id),
            url=url,
            title=str(node.get("name") or summary.title or "") or None,
            description=_html_text(node.get("description")),
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind=price_kind,
            price_unit=price_unit,
            price_pln=price_pln,
            price_per_m2_pln=round(price_per_m2) if price_per_m2 else None,
            area_m2=area_m2,
            land_area_m2=land_area,
            voivodeship=voivodeship,
            county=county,
            city=city,
            district=district,
            address_text=(
                city
                or (str(address.get("addressLocality") or "").strip() or None)
                or (str(address.get("addressRegion") or "").strip() or None)
            ),
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=images[:20],
            seller_type="agency",
            seller_name=seller or "AXI IMMO",
            published_at=parse_datetime_ms(node.get("datePosted")) or summary.published_at,
            modified_at=parse_datetime_ms(node.get("dateModified")),
            extra=extra,
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _summary(self, card: Any, query: Query) -> Listing | None:
        link = _first(card.xpath('.//*[contains(@class, "offer-block__title")]//a'))
        href = _attr(link, "href")
        if href is None:
            return None
        url = urljoin(BASE_URL, href)

        category = _text(_first(card.xpath('.//*[contains(@class, "offer-block__meta-category")]')))
        property_type = self._property_type(url, query.property_type)
        title = _text(link) or None
        excerpt = _text(_first(card.xpath('.//*[contains(@class, "offer-block__excerpt")]')))
        location = _text(_first(card.xpath('.//*[contains(@class, "offer-block__date")]')))
        voivodeship, county, city, district = _parse_search_location(location)
        if voivodeship is not None:
            voivodeship = self.regions.canonical_voivodeship(voivodeship)

        area = parse_area(
            _text(_first(card.xpath('.//*[contains(@class, "offer-block__meta-rest")]'))),
        )
        offer_type = _offer_type_from_title(title, query.offer_type)

        return Listing(
            source_id=self.source_id,
            external_id=_external_id(url),
            url=url,
            title=title,
            description=excerpt or None,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="unknown",
            price_unit="monthly" if offer_type in {"rent", "lease"} else "total",
            area_m2=None if property_type == "plot" else area,
            land_area_m2=area if property_type == "plot" else None,
            voivodeship=voivodeship,
            county=county,
            city=city,
            district=district,
            address_text=location or None,
            extra={
                "axiimmo": {
                    "category": category or None,
                    "location": location or None,
                },
            },
        )

    @staticmethod
    def _property_type(url: str, fallback: PropertyType) -> PropertyType:
        path = urlsplit(url).path
        if "/grunty-inwestycyjne/" in path:
            return "plot"
        if "/magazyny/" in path or "/biura/" in path:
            return "commercial"
        return fallback

    def _detail_location(
        self,
        address: dict[str, Any],
        breadcrumbs: list[str],
    ) -> tuple[str | None, str | None, str | None, str | None]:
        region = str(address.get("addressRegion") or "").strip() or None
        locality = str(address.get("addressLocality") or "").strip() or None
        region_is_voivodeship = self.regions.canonical_voivodeship(region) is not None

        voivodeship = self.regions.canonical_voivodeship(region)
        if voivodeship is None:
            for name in breadcrumbs:
                voivodeship = self.regions.canonical_voivodeship(name)
                if voivodeship is not None:
                    break

        county = next(
            (
                stripped
                for name in breadcrumbs
                if normalize_text(name).startswith(("powiat ", "pow "))
                and (stripped := _county(name)) is not None
            ),
            None,
        )

        city: str | None = None
        district: str | None = None
        if voivodeship is not None:
            if locality:
                parts = locality.split(" ", 1)
                city = parts[0]
                district = parts[1].strip() if len(parts) > 1 else None
            elif region is not None and not region_is_voivodeship:
                city = region
        else:
            city = region or locality
            if region is not None and locality is not None and locality != region:
                district = locality

        if voivodeship is None and city is not None:
            voivodeship = self.regions.match_city(city)
        return voivodeship, county, city, district

    def _price(
        self,
        node: dict[str, Any],
        title: Any,
        fallback: OfferType,
    ) -> tuple[OfferType, int | None, float | None, PriceUnit, PriceKind, str | None]:
        """Resolve offer type and the parts of a price the model can store."""
        offers = node.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else None
        if not isinstance(offer, dict):
            offer = None

        props = _properties(node)
        title_type = _offer_type_from_title(str(title or ""), fallback)

        business = str(offer.get("businessFunction") or "") if offer else ""
        description = str(offer.get("description") or "") if offer else ""
        if "Lease" in business or "lease" in business:
            offer_type: OfferType = "rent"
        elif "Sell" in business:
            offer_type = "sale"
        elif normalize_text(_prop_value(props, "For rent")) == "yes":
            offer_type = "rent"
        elif normalize_text(_prop_value(props, "For sale")) == "yes":
            offer_type = "sale"
        else:
            offer_type = title_type

        currency = str(offer.get("priceCurrency") or "").upper() if offer else None
        raw_price = offer.get("price") if offer else None
        per_unit = "/m²" in description or "/m2" in description or "m2" in description.lower()
        price_pln: int | None = None
        price_per_m2: float | None = None

        if currency == "PLN" and raw_price is not None:
            amount = _decimal(raw_price)
            if amount is not None:
                if per_unit:
                    price_per_m2 = amount
                else:
                    price_pln = round(amount)

        price_kind: PriceKind = "asking" if offer is not None else "call"
        price_unit: PriceUnit = "monthly" if offer_type in {"rent", "lease"} else "total"
        return offer_type, price_pln, price_per_m2, price_unit, price_kind, currency

    @staticmethod
    def _coordinates(
        doc: Any,
        node: dict[str, Any],
    ) -> tuple[float | None, float | None]:
        for match in _MARKER_RE.finditer(doc.text_content()):
            lat = safe_float(match.group(1))
            lng = safe_float(match.group(2))
            if lat is not None and lng is not None and 48.5 <= lat <= 55.5 and 13.5 <= lng <= 24.5:
                return round(lat, 6), round(lng, 6)

        geo = node.get("geo")
        if isinstance(geo, dict):
            lat = safe_float(geo.get("latitude"))
            lng = safe_float(geo.get("longitude"))
            if lat is not None and lng is not None and 48.5 <= lat <= 55.5 and 13.5 <= lng <= 24.5:
                return round(lat, 6), round(lng, 6)
        return None, None

    @staticmethod
    def _total_items(intro: Any) -> int | None:
        if intro is None:
            return None
        total = _first(intro.xpath('.//*[contains(@class, "all")]'))
        return safe_int(_text(total)) if total is not None else None

    @staticmethod
    def _total_pages(doc: Any) -> int | None:
        button = _first(doc.xpath('//*[@id="search_load_more"]'))
        value = _attr(button, "data-max-pages")
        if value is None:
            return None
        pages = safe_int(value)
        return pages if pages is not None and pages > 0 else None
