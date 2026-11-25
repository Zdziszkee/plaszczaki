"""Allegro Lokalnie Nieruchomości adapter.

The portal has no server-side voivodeship filter, but exact city pages work:
``/oferty/nieruchomosci/<city-slug>``. Each query therefore targets one city
from the configured region, newest first. Search cards only expose the city;
the street-level address lives in the structured ``Lokalizacja:`` block that
agency-fed offers copy into the detail description. Offers without that block
keep the city and stay un-geocoded (there are no coordinates on the page).

City slugs are mostly generated from the city name, but a few configured
cities use a disambiguating suffix (``chrzanow-3cc``), which is why verified
overrides are kept here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
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
    safe_int,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://allegrolokalnie.pl"
SEARCH_PATH = "/oferty/nieruchomosci"
SORT_NEWEST = "startingTime-desc"

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CATEGORY_SLUG_RE = re.compile(r"/oferty/nieruchomosci/([a-z0-9-]+)")
_CATEGORY_ID_RE = re.compile(r"-\d+$")
_AREA_PART_RE = re.compile(r"\d[\d\s.,]*\s*m\s*(?:²|2)?", re.IGNORECASE)
_FIELD_LABELS = (
    r"Kraj|Wojew[oó]dztwo|Powiat|Gmina[^:\n]{0,30}|Miasto na prawach powiatu"
    r"|Miejscowo[sś][cć]|Dzielnica|Ulica"
)
_FIELD_RE = re.compile(
    rf"(?P<label>{_FIELD_LABELS})\s*:\s*(?P<value>[^\n]*?)"
    rf"(?=[ \t]*(?:{_FIELD_LABELS})\s*:|\r?\n|$)",
    re.IGNORECASE,
)

_CATEGORIES: dict[str, tuple[OfferType, PropertyType]] = {
    "mieszkania-na-sprzedaz": ("sale", "flat"),
    "mieszkania-do-wynajecia": ("rent", "flat"),
    "domy-na-sprzedaz": ("sale", "house"),
    "domy-do-wynajecia": ("rent", "house"),
    "dzialki-na-sprzedaz": ("sale", "plot"),
    "dzialki-do-wynajecia": ("rent", "plot"),
    "lokale-i-obiekty-uzytkowe-na-sprzedaz": ("sale", "commercial"),
    "lokale-i-obiekty-uzytkowe-do-wynajecia": ("rent", "commercial"),
    "magazyny-i-hale-na-sprzedaz": ("sale", "commercial"),
    "magazyny-i-hale-do-wynajecia": ("rent", "commercial"),
    "garaze-na-sprzedaz": ("sale", "garage"),
    "garaze-i-miejsca-postojowe-do-wynajecia": ("rent", "garage"),
    "pokoje-do-wynajecia": ("rent", "room"),
}

# Slugs verified on cached result pages; the platform appends a short suffix to
# disambiguate cities that share a name.
_CITY_SLUG_OVERRIDES: dict[str, str] = {
    "Brzesko": "brzesko-v22",
    "Chrzanów": "chrzanow-3cc",
    "Lędziny": "ledziny-4tq",
    "Miechów": "miechow-o5e",
    "Nowy Targ": "nowy-targ-kqw",
    "Oświęcim": "oswiecim-fw2",
}

_TITLE_TYPES: dict[str, PropertyType] = {
    "mieszkanie": "flat",
    "mieszkania": "flat",
    "dom": "house",
    "domy": "house",
    "domek": "house",
    "dzialka": "plot",
    "dzialki": "plot",
    "lokal": "commercial",
    "lokale": "commercial",
    "biuro": "commercial",
    "biura": "commercial",
    "magazyn": "commercial",
    "magazyny": "commercial",
    "hala": "commercial",
    "hale": "commercial",
    "garaz": "garage",
    "garaze": "garage",
    "pokoj": "room",
    "pokoje": "room",
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _text(node: Any) -> str | None:
    if node is None:
        return None
    return _clean(node.text_content())


def _first_attr(nodes: list[Any], name: str) -> str | None:
    for node in nodes:
        value = node.get(name)
        if value:
            return value.strip()
    return None


def _first_param(params: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = params.get(key)
        if value:
            return value
    return None


def _clean_field(value: str) -> str | None:
    """Strip upstream annotations such as ``(pow.)``, ``(gm.)`` and refs."""
    text = _clean(value)
    if text is None:
        return None
    text = re.sub(r"\((?:pow|gm|ref)\.[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(ref\..*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" ,;")
    return text or None


def _slugify(city: str) -> str:
    override = _CITY_SLUG_OVERRIDES.get(city)
    if override is not None:
        return override
    normalized = normalize_text(city)
    return normalized.replace(" ", "-")


def _per_m2(value: str | None) -> int | None:
    area = parse_area(value)
    return int(area) if area is not None else None


def _market(value: str | None) -> Market:
    normalized = normalize_text(value or "")
    if normalized in {"pierwotny", "rynek pierwotny", "primary"}:
        return "primary"
    if normalized in {"wtorny", "rynek wtorny", "secondary"}:
        return "secondary"
    return "unknown"


def _seller_type(value: str | None) -> str | None:
    normalized = normalize_text(value or "")
    if "osoba prywatna" in normalized:
        return "private"
    if "firma" in normalized:
        return "agency"
    return None


class AllegrolokalnieAdapter:
    """Parse Allegro Lokalnie's server-rendered search and detail pages."""

    id = "allegrolokalnie"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        seen: set[str] = set()
        for region in regions:
            for city in self.regions.region(region).cities:
                key = f"{region}:{city}"
                if key in seen:
                    continue
                seen.add(key)
                queries.append(
                    Query(
                        key=key,
                        url=f"{BASE_URL}{SEARCH_PATH}/{_slugify(city)}?sort={SORT_NEWEST}",
                        region=region,
                        offer_type="other",
                        property_type="other",
                    ),
                )
        return queries

    def newest(self, query: Query) -> Query:
        return query.with_param("sort", SORT_NEWEST)

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = lxml_html.fromstring(html)
        cards = tree.xpath('//article[contains(@class, "mlc-itembox__container")]')
        expected_city = query.key.rsplit(":", 1)[-1]

        items: list[Listing] = []
        for card in cards:
            item = self._summary(card, query)
            if item.city and normalize_text(item.city) != normalize_text(expected_city):
                # A slug collision or a promoted card from another city.
                continue
            items.append(item)

        total_pages = self._total_pages(tree, query.page)
        if cards and not items:
            # The whole page belongs to another locality; do not crawl 100
            # pages of cards that will all be discarded.
            total_pages = 1
        return SearchPage(
            items=items,
            current_page=self._current_page(tree, query.page),
            total_pages=total_pages,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = lxml_html.fromstring(html)
        description_node = tree.xpath('//div[@id="offer-description-content"]')
        location_node = tree.xpath('//*[contains(@class, "mlc-offer__location-address")]')
        if not description_node and not location_node:
            raise ParseError("Allegro Lokalnie detail page is missing its offer body")

        description_raw = description_node[0].text_content() if description_node else None
        description_text = _clean(description_raw)
        h1 = _text(tree.xpath("//h1")[0]) if tree.xpath("//h1") else None
        params = self._params(tree)

        offer_type, property_type, category_text = self._category(tree)
        offer_type = offer_type or summary.offer_type
        property_type = property_type or summary.property_type

        fields = self._address_fields(description_raw)
        card_city, card_voivodeship = self._location_card(location_node)
        h1_city, h1_district = self._h1_location(h1)

        city = fields.get("city") or card_city or h1_city
        district = fields.get("district") or h1_district
        if district and city and normalize_text(district) == normalize_text(city):
            district = None
        county = fields.get("county")
        commune = fields.get("commune")
        street = fields.get("street")

        voivodeship = self.regions.canonical_voivodeship(
            fields.get("voivodeship") or card_voivodeship,
        )
        voivodeship = voivodeship or summary.voivodeship

        area, land_area = self._area(params, property_type)
        price = parse_price(
            _text(tree.xpath('//*[contains(@class, "ml-offer-price__dollars")]')[0])
            if tree.xpath('//*[contains(@class, "ml-offer-price__dollars")]')
            else None,
        )
        price = price or summary.price_pln
        floor, floor_label = parse_floor(_first_param(params, "pietro"))

        market = _market(_first_param(params, "rynek")) or _market(
            self._description_value(description_raw, "Typ rynku"),
        )

        address_text = ", ".join(part for part in (street, city) if part) or city
        seller_type = self._detail_seller(tree) or summary.seller_type

        extra = dict(summary.extra)
        portal_extra = dict(extra.get("allegrolokalnie") or {})
        portal_extra.update({"category": category_text, "params": params})
        extra["allegrolokalnie"] = portal_extra

        return replace(
            summary,
            title=h1 or summary.title,
            description=description_text,
            offer_type=offer_type,
            property_type=property_type,
            market=market,
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_per_m2(_first_param(params, "cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(_first_param(params, "liczba pokoi", "pokoje")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(_first_param(params, "rok budowy")),
            voivodeship=voivodeship,
            county=county,
            commune=commune,
            city=city,
            district=district,
            street=street,
            address_text=address_text,
            lat=None,
            lng=None,
            geo_source=None,
            images=self._images(tree) or summary.images,
            seller_type=seller_type,
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        link = card.xpath('.//a[contains(@class, "mlc-card")]')
        if not link:
            raise ParseError("Allegro Lokalnie listing card is missing its link")
        href = _first_attr(link, "href")
        if not href:
            raise ParseError("Allegro Lokalnie listing card has no href")

        external_id = _first_attr(link, "data-card-analytics-click") or self._analytics_id(link)
        if external_id is None:
            external_id = href.split("/oferta/", 1)[-1].split("?", 1)[0]

        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        title = (
            _text(card.xpath('.//h3[@itemprop="itemOffered"]')[0])
            if card.xpath(
                './/h3[@itemprop="itemOffered"]',
            )
            else None
        )
        city = _clean(
            " ".join(card.xpath('.//address[@itemprop="address"]//text()')),
        )
        params = self._card_params(card)

        property_type = self._property_from_title(title) or self._property_from_params(params)
        offer_type = self._offer_from_title(title)
        area, land_area = self._area(params, property_type)

        price = parse_price(
            _text(card.xpath('.//*[contains(@class, "ml-offer-price__dollars")]')[0])
            if card.xpath('.//*[contains(@class, "ml-offer-price__dollars")]')
            else None,
        )
        floor, floor_label = parse_floor(_first_param(params, "pietro"))
        seller_type = _seller_type(
            " ".join(card.xpath('.//ul[contains(@class, "ml-badges")]//li//text()')),
        )

        return Listing(
            source_id=self.source_id,
            external_id=external_id,
            url=url,
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            market="unknown",
            price_kind="call" if price is None else "asking",
            price_unit="monthly" if offer_type == "rent" else "total",
            price_pln=price,
            price_per_m2_pln=_per_m2(_first_param(params, "cena za m2")),
            area_m2=area,
            land_area_m2=land_area,
            rooms=parse_rooms(_first_param(params, "liczba pokoi", "pokoje")),
            floor=floor,
            floor_label=floor_label,
            build_year=safe_int(_first_param(params, "rok budowy")),
            voivodeship=query.region,
            city=city,
            address_text=city,
            images=self._images(card),
            seller_type=seller_type,
            extra={"allegrolokalnie": {"query": query.key}},
        )

    @staticmethod
    def _analytics_id(link: Any) -> str | None:
        raw = link.get("data-experiment-analytics")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("lokalnie_offer_id")
        return str(value) if value else None

    @staticmethod
    def _total_pages(tree: Any, fallback: int) -> int:
        values = tree.xpath(
            '//nav[contains(@class, "ml-pagination")]//input[@type="number"]/@max',
        )
        pages = [safe_int(value) for value in values]
        return max((page for page in pages if page is not None), default=fallback) or fallback

    @staticmethod
    def _current_page(tree: Any, fallback: int) -> int:
        values = tree.xpath(
            '//nav[contains(@class, "ml-pagination")]//input[@type="number"]/@value',
        )
        current = safe_int(values[0]) if values else None
        return current or fallback

    @staticmethod
    def _card_params(card: Any) -> dict[str, str]:
        params: dict[str, str] = {}
        for item in card.xpath('.//ul[contains(@class, "mlc-itembox__params")]/li'):
            text = item.text_content()
            if ":" not in text:
                continue
            label, value = text.split(":", 1)
            key = normalize_text(label)
            cleaned = _clean(value)
            if key and cleaned and key not in params:
                params[key] = cleaned
        return params

    @staticmethod
    def _params(tree: Any) -> dict[str, str]:
        params: dict[str, str] = {}
        for row in tree.xpath('//table[contains(@class, "mlc-params")]//tr'):
            cells = row.xpath("./th|./td")
            if len(cells) < 2:
                continue
            key = normalize_text(cells[0].text_content() or "")
            value = _text(cells[1])
            if key and value and key not in params:
                params[key] = value
        return params

    @staticmethod
    def _property_from_title(title: str | None) -> PropertyType | None:
        if not title:
            return None
        normalized = normalize_text(title)
        first = normalized.split(" ", 1)[0] if normalized else ""
        return _TITLE_TYPES.get(first)

    @staticmethod
    def _property_from_params(params: dict[str, str]) -> PropertyType:
        if "typ dzialki" in params:
            return "plot"
        if "typ komercyjny" in params or "liczba pomieszczen" in params:
            return "commercial"
        if "liczba miejsc parkingowych" in params:
            return "garage"
        if "powierzchnia mieszkalna" in params or "rodzaj zabudowy" in params:
            return "house"
        return "other"

    @staticmethod
    def _offer_from_title(title: str | None) -> OfferType:
        normalized = normalize_text(title or "")
        if "wynaj" in normalized or "najem" in normalized:
            return "rent"
        if "dzierzaw" in normalized:
            return "lease"
        if "sprzeda" in normalized:
            return "sale"
        return "other"

    @staticmethod
    def _area(
        params: dict[str, str],
        property_type: PropertyType,
    ) -> tuple[float | None, float | None]:
        area = parse_area(
            _first_param(
                params, "powierzchnia", "powierzchnia mieszkalna", "powierzchnia uzytkowa"
            ),
        )
        land_area = parse_area(_first_param(params, "powierzchnia dzialki"))
        if property_type == "plot":
            return None, land_area or area
        return area, land_area

    @staticmethod
    def _category(tree: Any) -> tuple[OfferType | None, PropertyType | None, str | None]:
        leaf_text: str | None = None
        for anchor in tree.xpath('//nav[contains(@class, "ml-category-breadcrumbs")]//a'):
            href = anchor.get("href") or ""
            match = _CATEGORY_SLUG_RE.search(href)
            if match is None:
                continue
            slug = _CATEGORY_ID_RE.sub("", match.group(1))
            category = _CATEGORIES.get(slug)
            leaf_text = _text(anchor)
            if category is not None:
                return category[0], category[1], leaf_text

        # Fallback for pages where the breadcrumb has no stable slug.
        normalized = normalize_text(leaf_text or "")
        if "wynaj" in normalized or "najem" in normalized:
            offer_type: OfferType = "rent"
        elif "sprzeda" in normalized:
            offer_type = "sale"
        else:
            offer_type = "other"

        if "mieszkan" in normalized:
            property_type: PropertyType = "flat"
        elif "dom" in normalized:
            property_type = "house"
        elif "dzial" in normalized:
            property_type = "plot"
        elif "garaz" in normalized or "postoj" in normalized:
            property_type = "garage"
        elif "pokoj" in normalized:
            property_type = "room"
        elif "lokal" in normalized or "magazyn" in normalized or "hal" in normalized:
            property_type = "commercial"
        else:
            property_type = "other"
        return offer_type, property_type, leaf_text

    @staticmethod
    def _address_fields(description: str | None) -> dict[str, str]:
        if not description:
            return {}

        fields: dict[str, str] = {}
        for match in _FIELD_RE.finditer(description):
            key = _field_key(match.group("label"))
            value = _clean_field(match.group("value"))
            if key is not None and value is not None:
                fields.setdefault(key, value)
        return fields

    @staticmethod
    def _description_value(description: str | None, label: str) -> str | None:
        if not description:
            return None
        match = re.search(rf"{re.escape(label)}\s*:\s*([^\n]+)", description, re.IGNORECASE)
        return _clean_field(match.group(1)) if match else None

    @staticmethod
    def _location_card(nodes: list[Any]) -> tuple[str | None, str | None]:
        if not nodes:
            return None, None
        node = nodes[0]
        city = _text(node.xpath(".//h4")[0]) if node.xpath(".//h4") else None
        voivodeship = (
            _text(node.xpath('.//*[contains(@class, "ml-text-small")]')[0])
            if node.xpath(
                './/*[contains(@class, "ml-text-small")]',
            )
            else None
        )
        return (
            city.strip(" ,") if city else None,
            voivodeship.strip(" ,") if voivodeship else None,
        )

    @staticmethod
    def _h1_location(title: str | None) -> tuple[str | None, str | None]:
        if not title:
            return None, None
        parts = [_clean(part) for part in title.split(",")]
        parts = [part for part in parts if part and not _AREA_PART_RE.fullmatch(part)]
        if len(parts) < 2:
            return None, None
        city = parts[1]
        district = parts[2] if len(parts) >= 3 else None
        return city, district

    @staticmethod
    def _detail_seller(tree: Any) -> str | None:
        return _seller_type(
            " ".join(tree.xpath('//ul[contains(@class, "ml-badges")]//li//text()')),
        )

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        for node in tree.xpath('.//img[@itemprop="image"]'):
            src = node.get("src") or node.get("data-src")
            if src and src not in images:
                images.append(src)
        return images[:20]


def _field_key(label: str) -> str | None:
    normalized = normalize_text(label)
    if normalized.startswith("kraj"):
        return "country"
    if normalized.startswith("wojew"):
        return "voivodeship"
    if normalized.startswith("powiat"):
        return "county"
    if normalized.startswith("gmina"):
        return "commune"
    if normalized.startswith("miasto") or normalized.startswith("miejscowosc"):
        return "city"
    if normalized.startswith("dzielnica"):
        return "district"
    if normalized.startswith("ulica"):
        return "street"
    return None
