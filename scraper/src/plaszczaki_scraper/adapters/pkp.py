"""PKP S.A. adapter (sale offers embedded as GeoJSON + detail pages)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from html import unescape

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_price,
    safe_float,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://www.pkp.pl/pl/sprzedaz"
SITE_ROOT = "https://www.pkp.pl"

# Region ids used by the site's province_id filter.
PROVINCE_IDS: dict[str, int] = {"malopolskie": 6, "slaskie": 12}

_CATEGORY_TYPES: dict[str, PropertyType] = {
    "grunty": "plot",
    "lokale mieszkalne": "flat",
    "lokale uzytkowe": "commercial",
    "lokale uzytkowe poza dworcem": "commercial",
    "lokale uzytkowe na dworcu": "commercial",
    "budynki": "commercial",
}

_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_POSTAL_RE = re.compile(r"^\s*(\d{2}-\d{3})\s+([^,]+)")
_ADDRESS_RE = re.compile(r"Adres:\s*</b>\s*(.*?)(?:<i>|</div>|$)", re.S | re.I)
_CATEGORY_RE = re.compile(r"Kategoria:\s*</b>\s*([^<]+)", re.I)
_TENDER_TYPE_RE = re.compile(r"Typ:\s*</b>\s*([^<]+)", re.I)
_SHOW_ID_RE = re.compile(r"show=(\d+)")
_DECIMALS_RE = re.compile(r",\d{2}(?!\d)")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _html_text(value: str | None) -> str | None:
    if not value:
        return None
    text = _BR_RE.sub(", ", value)
    text = unescape(_TAG_RE.sub(" ", text))
    return _text(text)


def _canonical_property_type(value: str | None) -> PropertyType:
    if not value:
        return "other"
    normalized = normalize_text(value)
    if normalized in _CATEGORY_TYPES:
        return _CATEGORY_TYPES[normalized]
    for key, property_type in _CATEGORY_TYPES.items():
        if normalized.startswith(key):
            return property_type
    return "other"


def _clean_address(value: str | None) -> str | None:
    if not value:
        return None
    parts = [part.strip(" ,\u00a0") for part in value.split(",")]
    return ", ".join(part for part in parts if part) or None


def _price(value: str | None) -> int | None:
    """Parse whole-złoty prices that carry a ",00" decimal suffix."""
    if not value:
        return None
    return parse_price(_DECIMALS_RE.sub("", value))


def _tree(html: str) -> lxml_html.HtmlElement:
    try:
        return lxml_html.fromstring(html)
    except (ValueError, TypeError) as exc:
        raise ParseError(f"PKP page is not valid HTML: {exc}") from exc


def _geojson(html: str) -> dict[str, object]:
    marker = html.find("var geoJsonData")
    candidates: list[int] = []
    if marker >= 0:
        candidates.append(html.find("{", marker))
    start = html.find("FeatureCollection")
    while start >= 0:
        candidates.append(html.rfind("{", 0, start))
        start = html.find("FeatureCollection", start + 1)

    decoder = json.JSONDecoder()
    for index in candidates:
        if index < 0:
            continue
        try:
            payload, _ = decoder.raw_decode(html[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
            return payload
    raise ParseError("PKP page is missing the offers GeoJSON payload")


def _coordinates(feature: dict[str, object]) -> tuple[float | None, float | None]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None, None
    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None, None
    lng = safe_float(coords[0])
    lat = safe_float(coords[1])
    return lat, lng


class PkpAdapter:
    """Parse PKP's single sale page with its embedded offer GeoJSON."""

    id = "pkp"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            province_id = PROVINCE_IDS.get(region)
            url = (
                f"{BASE_URL}?menu=2&province_id={province_id}"
                if province_id
                else f"{BASE_URL}?menu=2"
            )
            queries.append(
                Query(
                    key=f"{region}:sprzedaz",
                    url=url,
                    region=region,
                    offer_type="sale",
                    property_type="other",
                ),
            )
        return queries

    def with_page(self, query: Query, page: int) -> Query:
        # All offers are shipped in one response; there is no pagination.
        return replace(query, page=1)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        payload = _geojson(html)
        features = payload.get("features")
        if not isinstance(features, list):
            raise ParseError("PKP GeoJSON payload has no features")

        items: list[Listing] = []
        seen: set[str] = set()
        for feature in features:
            if not isinstance(feature, dict):
                continue
            listing = self._summary(feature, query)
            if listing is None or listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            items.append(listing)

        return SearchPage(
            items=items,
            current_page=1,
            total_pages=1,
            total_items=len(features),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = _tree(html)
        details = self._details(tree)
        feature = self._feature(html, summary.external_id)
        lat, lng = _coordinates(feature) if feature is not None else (None, None)
        if lat is None or lng is None:
            lat, lng = summary.lat, summary.lng

        city = _text(" ".join(tree.xpath('//div[@class="naglowek-miasto"]/text()'))) or summary.city
        title = (
            _text(" ".join(tree.xpath('//div[@class="naglowek-nazwa"]/text()'))) or summary.title
        )
        number = _text(" ".join(tree.xpath('//div[@class="naglowek-numer"]/text()')))
        if number:
            digits = re.search(r"\d+", number)
            number = digits.group(0) if digits else number

        address = details.get("adres")
        street = None
        parsed_city = None
        if address:
            match = _POSTAL_RE.match(address)
            if match:
                parsed_city = match.group(2).strip()
            if "," in address:
                street = _clean_address(address.split(",", 1)[1])
        city = parsed_city or city

        description = self._description(tree)
        property_type = _canonical_property_type(
            details.get("typ nieruchomosci") or summary.property_type,
        )
        if property_type == "other":
            property_type = summary.property_type

        price = _price(
            details.get("cena orientacyjna")
            or details.get("cena wywolawcza")
            or details.get("cena"),
        )
        extra = dict(summary.extra)
        pkp_extra = dict(extra.get("pkp") or {})
        pkp_extra.update(
            {
                "numer_oferty": number,
                "typ_postepowania": details.get("typ postepowania"),
                "status": details.get("status"),
                "przeznaczenie": details.get("przeznaczenie"),
                "powiat": details.get("powiat"),
                "gmina": details.get("gmina"),
            },
        )
        extra["pkp"] = pkp_extra

        return replace(
            summary,
            title=title or summary.title,
            description=description,
            property_type=property_type,
            price_kind="starting" if price is not None else "call",
            price_pln=price,
            area_m2=parse_area(
                details.get("powierzchnia budynku")
                or details.get("powierzchnia uzytkowa")
                or details.get("powierzchnia lokalu"),
            ),
            land_area_m2=parse_area(details.get("powierzchnia gruntu")),
            voivodeship=self.regions.canonical_voivodeship(details.get("wojewodztwo"))
            or summary.voivodeship,
            county=details.get("powiat") or summary.county,
            commune=details.get("gmina") or summary.commune,
            city=city,
            street=street,
            address_text=address or summary.address_text,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            images=self._images(tree),
            seller_type="government",
            seller_name="PKP S.A.",
            extra=extra,
        )

    def _summary(self, feature: dict[str, object], query: Query) -> Listing | None:
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            return None
        raw_address = properties.get("address")
        if not isinstance(raw_address, str):
            return None

        address_html = _ADDRESS_RE.search(raw_address)
        address = _clean_address(_html_text(address_html.group(1) if address_html else raw_address))
        if not address:
            return None

        postal = city = None
        match = _POSTAL_RE.match(address)
        if match:
            postal, city = match.group(1), match.group(2).strip()
        else:
            city = _text(address.split(",", 1)[0])

        lat, lng = _coordinates(feature)
        if query.region not in self._candidate_regions(city, lat, lng):
            return None

        category_match = _CATEGORY_RE.search(raw_address)
        category = _text(category_match.group(1)) if category_match else None
        type_match = _TENDER_TYPE_RE.search(raw_address)
        tender_type = _text(type_match.group(1)) if type_match else None
        show_match = _SHOW_ID_RE.search(raw_address)
        external_id = str(feature.get("id") or (show_match.group(1) if show_match else "")).strip()
        if not external_id:
            return None

        street = _clean_address(address.split(",", 1)[1]) if "," in address else None
        property_type = _canonical_property_type(category)
        title = None
        if category and city:
            title = f"{category.capitalize()} - {city}"

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=f"{BASE_URL}?menu=2&show={external_id}",
            title=title,
            offer_type="sale",
            property_type=property_type,
            price_kind="unknown",
            voivodeship=query.region,
            city=city,
            street=street,
            address_text=address,
            lat=lat,
            lng=lng,
            geo_source="source" if lat is not None and lng is not None else None,
            seller_type="government",
            seller_name="PKP S.A.",
            extra={
                "pkp": {
                    "kategoria": category,
                    "typ": tender_type,
                    "kod_pocztowy": postal,
                },
            },
        )

    def _candidate_regions(
        self,
        city: str | None,
        lat: float | None,
        lng: float | None,
    ) -> set[str]:
        matched = self.regions.match_city(city)
        if matched is not None:
            return {matched}
        if lat is None or lng is None:
            return set()
        candidates: set[str] = set()
        for region_id, region in self.regions.config.regions.items():
            min_lon, min_lat, max_lon, max_lat = region.bbox
            if min_lon <= lng <= max_lon and min_lat <= lat <= max_lat:
                candidates.add(region_id)
        return candidates

    @staticmethod
    def _details(tree: lxml_html.HtmlElement) -> dict[str, str]:
        details: dict[str, str] = {}
        for row in tree.xpath('//section[contains(@class,"oferta-szczegoly")]//tr'):
            cells = row.xpath("./td")
            if len(cells) < 2:
                continue
            label = normalize_text(cells[0].text_content())
            value = _text(cells[1].text_content())
            if label and value and label not in details:
                details[label] = value
        return details

    @staticmethod
    def _feature(html: str, external_id: str) -> dict[str, object] | None:
        payload = _geojson(html)
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        for feature in features:
            if isinstance(feature, dict) and str(feature.get("id")) == external_id:
                return feature
        return None

    @staticmethod
    def _description(tree: lxml_html.HtmlElement) -> str | None:
        sections = tree.xpath('//section[contains(@class,"oferta-opis")]')
        if not sections:
            return None
        paragraphs = [
            text
            for p in sections[0].xpath(".//p")
            if (text := _html_text(lxml_html.tostring(p, encoding="unicode")))
        ]
        if not paragraphs:
            text = _html_text("".join(lxml_html.tostring(sections[0], encoding="unicode")))
            return text.removeprefix("Opis oferty ") if text else None
        return "\n\n".join(paragraphs)

    @staticmethod
    def _images(tree: lxml_html.HtmlElement) -> list[str]:
        images: list[str] = []
        for src in tree.xpath('//section[contains(@class,"oferta-galeria")]//img/@src'):
            url = f"{SITE_ROOT}{src}" if src.startswith("/") else src
            if url not in images:
                images.append(url)
        return images[:20]
