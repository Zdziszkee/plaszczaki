"""Otoprzetargi adapter (search pages + notice detail pages).

Otoprzetargi publishes municipal, powiat and syndyk property notices.  The
search page exposes one card per notice; the detail page contains the full
notice body, the advertised price/area and a Google Maps centre point
(``defaultLat``/``defaultLng``) for the advertised location.

Listings without both coordinates and a description are treated as unparseable:
the source is only useful when the detail page carries those two fields.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, OfferType, PriceKind, PropertyType
from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_floor,
    parse_rooms,
)
from plaszczaki_scraper.regions import RegionRegistry

BASE_URL = "https://www.otoprzetargi.pl"
SEARCH_PATH = "/nieruchomosci"
PAGE_SIZE = 15

# The site uses this centre when a notice has not been geocoded.
FALLBACK_LAT = 52.13340
FALLBACK_LNG = 21.27435

WARSAW = ZoneInfo("Europe/Warsaw")

_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "wrzesnia": 9,
    "września": 9,
    "pazdziernika": 10,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}

_PROPERTY_TYPES: tuple[tuple[str, PropertyType], ...] = (
    ("mieszkanie", "flat"),
    ("dom", "house"),
    ("dworek", "house"),
    ("palac", "house"),
    ("pałac", "house"),
    ("dzialka", "plot"),
    ("działka", "plot"),
    ("grunt", "plot"),
    ("lokal uzytkowy", "commercial"),
    ("lokal użytkowy", "commercial"),
    ("biuro", "commercial"),
    ("obiekt biurowy", "commercial"),
    ("magazyn", "commercial"),
    ("hala", "commercial"),
    ("hotel", "commercial"),
    ("pensjonat", "commercial"),
    ("garaż", "garage"),
    ("garaz", "garage"),
)

_TRANSACTION_OFFER_TYPES: tuple[tuple[str, OfferType], ...] = (
    ("sprzedaz", "sale"),
    ("sprzedaż", "sale"),
    ("kupno", "sale"),
    ("dzierzawa", "lease"),
    ("dzierżawa", "lease"),
    ("wynajem", "rent"),
    ("najem", "rent"),
)

_AUCTION_MARKERS = ("przetarg", "licytacj", "aukcj")

_DATE_RE = re.compile(r"Data:\s*(\d{2})\.(\d{2})\.(\d{4})")
_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_TOTAL_RE = re.compile(
    r"of\s*(?:<[^>]+>\s*)*([\d\s]+?)\s*(?:</?[^>]+>\s*)*results",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"Cena(?:\s+wywoławcza)?(?:\s+za[^:]{0,60})?:\s*"
    r"([0-9][0-9\s\u00a0.,]*?)\s*zł",
    re.IGNORECASE,
)
_PRICE_FALLBACK_RE = re.compile(
    r"Cena\s+wywoławcza[\s\S]{0,180}?([0-9][0-9\s\u00a0.,]*?)\s*zł",
    re.IGNORECASE,
)
_AREA_RE = re.compile(
    r"Powierzchnia(?:\s+nieruchomości)?\s*[:\-\u2013]?\s*"
    r"([0-9][0-9\s\u00a0.,]*)\s*(m\s*2|m²|m\.?kw\.?|ha)",
    re.IGNORECASE,
)
_AREA_FALLBACK_RE = re.compile(
    r"(?:o\s+pow\.?|powierzchni)\s*([0-9][0-9\s\u00a0.,]*)\s*"
    r"(m\s*2|m²|m\.?kw\.?|ha)",
    re.IGNORECASE,
)
_LAT_RE = re.compile(r"defaultLat\s*=\s*(-?\d+(?:\.\d+)?)")
_LNG_RE = re.compile(r"defaultLng\s*=\s*(-?\d+(?:\.\d+)?)")
_CASE_RE = re.compile(r"\b(?:Km\s*\d+/\d+|[A-Z]{2,5}\.\d{2,}[A-Z0-9./-]*)\b")
_ROUND_RE = re.compile(
    r"\b(pierwsz|drug|trzec|czwart|piac|szost)\w*\s+"
    r"(?:ustn\w*\s+)?(?:przetarg|licytacj|aukcj)",
)
_TEXT_DATE_RE = re.compile(
    r"(?:w dniu|dnia)\s+(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"(?:w dniu|dnia)\s+(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})")
_TIME_RE = re.compile(r"(?:o\s+godzinie|godz\.?)\s+(\d{1,2})(?:[:.](\d{2}))?")

_ROUNDS = {
    "pierwsz": 1,
    "drug": 2,
    "trzec": 3,
    "czwart": 4,
    "piat": 5,
    "szost": 6,
}


def _text(node: Any) -> str:
    return " ".join(node.text_content().split()) if node is not None else ""


def _first_text(nodes: list[Any]) -> str:
    for node in nodes:
        value = _text(node)
        if value:
            return value
    return ""


def _money(value: str) -> int | None:
    """Parse a Polish amount that may contain grosze and dot separators."""
    raw = value.replace("\u00a0", " ").strip()
    number = re.search(r"\d[\d\s.,]*", raw)
    if number is None:
        return None
    raw = number.group(0).strip()
    if not raw:
        return None
    if "," in raw:
        normalized = raw.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        normalized = raw.replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized):
            normalized = normalized.replace(".", "")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    rounded = round(amount)
    return rounded if rounded > 0 else None


def _date_ms(day: str, month: str, year: str) -> int | None:
    try:
        moment = datetime(int(year), int(month), int(day), tzinfo=WARSAW)
    except ValueError:
        return None
    return int(moment.astimezone(UTC).timestamp() * 1000)


def _polish_date_ms(value: str) -> int | None:
    match = _TEXT_DATE_RE.search(value)
    if match:
        month = _MONTHS[match.group(2).lower()]
        return _date_ms(match.group(1), str(month), match.group(3))
    match = _NUMERIC_DATE_RE.search(value)
    if match:
        return _date_ms(match.group(1), match.group(2), match.group(3))
    return None


def _auction_date_ms(value: str) -> int | None:
    window = re.search(
        r"(?:przetarg(?: ustny)?\s+odbędzie\s+się|termin(?:\s+i\s+miejsce)?\s+przetargu)"
        r"[\s\S]{0,220}",
        value,
        re.IGNORECASE,
    )
    if window is None:
        return None

    snippet = window.group(0)
    date_ms = _polish_date_ms(snippet)
    if date_ms is None:
        return None

    time_match = _TIME_RE.search(snippet)
    if time_match is None:
        return date_ms

    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return date_ms
    moment = datetime.fromtimestamp(date_ms / 1000, tz=UTC).astimezone(WARSAW)
    accurate = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(accurate.astimezone(UTC).timestamp() * 1000)


def _offer_type(title: str, transaction: str | None) -> OfferType:
    normalized = normalize_text(f"{title} {transaction or ''}")
    if any(marker in normalized for marker in _AUCTION_MARKERS):
        return "auction"
    for key, offer_type in _TRANSACTION_OFFER_TYPES:
        if normalize_text(key) in normalized:
            return offer_type
    return "other"


def _is_lease(transaction: str | None, offer_type: OfferType) -> bool:
    normalized = normalize_text(transaction or "")
    return offer_type in {"lease", "rent"} or any(
        marker in normalized for marker in ("dzierzaw", "wynaj", "najem")
    )


def _is_rent(transaction: str | None, offer_type: OfferType) -> bool:
    normalized = normalize_text(transaction or "")
    return offer_type == "rent" or any(marker in normalized for marker in ("wynaj", "najem"))


def _property_type(subject: str | None, title: str) -> PropertyType:
    for source in (subject or "", title):
        normalized = normalize_text(source)
        words = set(normalized.split())
        for key, property_type in _PROPERTY_TYPES:
            needle = normalize_text(key)
            if (" " in needle and needle in normalized) or needle in words:
                return property_type
    return "other"


def _split_location(value: str) -> tuple[str | None, str | None]:
    if "," not in value:
        return value.strip() or None, None
    city, province = value.rsplit(",", 1)
    return city.strip() or None, province.strip() or None


def _field(text: str, label: str, following: str) -> str | None:
    pattern = re.compile(
        rf"{label}\s*:\s*(.+?)(?=\s+(?:{following})\b|$)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    value = " ".join(match.group(1).split()).strip(" ,;")
    return value or None


class OtoprzetargiAdapter:
    """Parse public property notices from Otoprzetargi."""

    id = "otoprzetargi"

    def __init__(self, *, source_id: str, regions: RegionRegistry) -> None:
        self.source_id = source_id
        self.regions = regions

    def queries(self, regions: Sequence[str]) -> list[Query]:
        queries: list[Query] = []
        for region in regions:
            config = self.regions.region(region)
            queries.append(
                Query(
                    key=f"{region}:nieruchomosci",
                    url=f"{BASE_URL}{SEARCH_PATH}?voivodeship={config.name}",
                    region=region,
                    offer_type="auction",
                    property_type="other",
                ),
            )
        return queries

    def newest(self, query: Query) -> Query:
        """The portal's default list is already newest-first."""
        return query

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = self._tree(html)
        cards = tree.xpath(
            '//div[contains(@class, "col-sm-6")]/a[contains(@href, "/nieruchomosci/")]',
        )
        items = [self._summary(card, query) for card in cards]
        if not items:
            raise ParseError("Otoprzetargi search page has no listing cards")

        total_items = self._total_items(html)
        total_pages = self._total_pages(html)
        if total_pages is None and total_items is not None:
            total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)

        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=total_pages,
            total_items=total_items,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        tree = self._tree(html)
        for node in tree.xpath("//script|//style"):
            node.drop_tree()

        title = _first_text(tree.xpath("//h1")) or self._meta(tree, "og:title") or summary.title
        if not title:
            raise ParseError("Otoprzetargi detail page has no title")

        coordinates = self._coordinates(html)
        if coordinates is None:
            raise ParseError("Otoprzetargi detail page has no location coordinates")
        lat, lng = coordinates

        description = self._description(tree)
        if description is None:
            raise ParseError("Otoprzetargi detail page has no description")

        full_text = " ".join(tree.text_content().split())
        cutoff = full_text.find(description[:80])
        content_text = full_text[: cutoff + len(description)] if cutoff >= 0 else full_text

        transaction = _field(
            content_text,
            "Typ transakcji",
            "Przedmiot ogłoszenia|Miejscowość ogłoszenia|Województwo|Drukuj",
        )
        subject = _field(
            content_text,
            "Przedmiot ogłoszenia",
            "Miejscowość ogłoszenia|Województwo|Drukuj",
        )
        city = _field(
            content_text,
            "Miejscowość ogłoszenia",
            "Województwo|Drukuj",
        )
        province = _field(content_text, "Województwo", "Drukuj|Wydanie")

        offer_type = _offer_type(f"{title} {description}", transaction)
        property_type = _property_type(subject, f"{title} {description}")

        price = self._price(content_text)
        price_kind: PriceKind = "starting" if offer_type == "auction" else "asking"

        area_m2, land_area_m2 = self._areas(content_text, property_type)
        rooms = self._rooms(content_text)
        floor, floor_label = self._floor(content_text)

        voivodeship = self.regions.canonical_voivodeship(province)
        city = city or summary.city
        if voivodeship is None:
            voivodeship = summary.voivodeship

        published_at = summary.published_at

        price_pln: int | None = price
        rent_pln: int | None = None
        price_unit = "total"
        lease = _is_lease(transaction, offer_type)
        if lease:
            price_pln = None
            rent_pln = price
            price_unit = "monthly" if _is_rent(transaction, offer_type) else "unknown"

        address_text = ", ".join(part for part in (city, province) if part) or summary.address_text

        images = self._images(tree)
        extra = dict(summary.extra)
        extra["otoprzetargi"] = {
            "transaction": transaction,
            "subject": subject,
            "coordinates": {"lat": lat, "lng": lng},
        }

        return replace(
            summary,
            url=summary.url,
            title=title,
            description=description,
            offer_type=offer_type,
            property_type=property_type,
            market="secondary",
            price_kind=price_kind,
            price_unit=price_unit,
            price_pln=price_pln,
            rent_pln=rent_pln,
            area_m2=area_m2,
            land_area_m2=land_area_m2,
            rooms=rooms,
            floor=floor,
            floor_label=floor_label,
            voivodeship=voivodeship,
            city=city,
            address_text=address_text,
            lat=lat,
            lng=lng,
            geo_source="source",
            images=images or summary.images,
            seller_type=self._seller_type(content_text),
            seller_name=self._seller_name(title),
            published_at=published_at,
            auction_date=_auction_date_ms(description),
            auction_round=self._round(title, description),
            case_number=self._case_number(title, description),
            extra=extra,
        )

    def _summary(self, card: Any, query: Query) -> Listing:
        href = (card.get("href") or "").strip()
        if not href:
            raise ParseError("Otoprzetargi card has no detail link")
        url = urljoin(BASE_URL, href)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if not slug:
            raise ParseError(f"Otoprzetargi card has no slug: {url}")

        title = _first_text(card.xpath(".//h2")) or None
        paragraphs = [_text(node) for node in card.xpath(".//p")]
        location = next(
            (
                value
                for value in paragraphs
                if value and not value.lower().startswith(("data:", "cena:", "powierzchnia:"))
            ),
            "",
        )
        city, province = _split_location(location)

        badges = [_text(node) for node in card.xpath('.//div[contains(@class, "badge")]')]
        badges = [value for value in badges if value]
        transaction = badges[0] if badges else None
        subject = badges[1] if len(badges) > 1 else None

        published_at = None
        for value in paragraphs:
            match = _DATE_RE.search(value)
            if match:
                published_at = _date_ms(match.group(1), match.group(2), match.group(3))
                break

        price = None
        area = None
        for value in paragraphs:
            if price is None and "cena" in value.lower():
                price = _money(value)
            if area is None and "powierzchnia" in value.lower():
                area = parse_area(value)

        offer_type = _offer_type(title or "", transaction)
        property_type = _property_type(subject, title or "")

        images = self._card_images(card)
        extra: dict[str, Any] = {
            "otoprzetargi": {
                "transaction": transaction,
                "subject": subject,
            },
        }

        return Listing(
            source_id=self.source_id,
            external_id=slug,
            url=url,
            title=title,
            offer_type=offer_type,
            property_type=property_type,
            market="secondary",
            price_kind="starting" if offer_type == "auction" else "asking",
            price_unit="total",
            price_pln=price,
            area_m2=area if property_type != "plot" else None,
            land_area_m2=area if property_type == "plot" else None,
            voivodeship=self.regions.canonical_voivodeship(province) or query.region,
            city=city,
            address_text=", ".join(part for part in (city, province) if part) or None,
            images=images,
            published_at=published_at,
            extra=extra,
        )

    def _description(self, tree: Any) -> str | None:
        blocks = tree.xpath('//div[contains(@class, "text-block")]')
        for block in blocks:
            heading = block.xpath('.//h3[contains(normalize-space(.), "Szczegóły")]')
            if not heading:
                continue
            for node in heading:
                node.drop_tree()
            description = _text(block)
            if description:
                return description
        return None

    def _coordinates(self, html: str) -> tuple[float, float] | None:
        lat_match = _LAT_RE.search(html)
        lng_match = _LNG_RE.search(html)
        if lat_match is None or lng_match is None:
            return None
        lat = float(lat_match.group(1))
        lng = float(lng_match.group(1))
        if abs(lat - FALLBACK_LAT) < 0.00001 and abs(lng - FALLBACK_LNG) < 0.00001:
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        return lat, lng

    def _price(self, text: str) -> int | None:
        match = _PRICE_RE.search(text) or _PRICE_FALLBACK_RE.search(text)
        if match is None:
            return None
        return _money(match.group(1))

    def _areas(self, text: str, property_type: PropertyType) -> tuple[float | None, float | None]:
        match = _AREA_RE.search(text) or _AREA_FALLBACK_RE.search(text)
        if match is None:
            return None, None
        value = parse_area(f"{match.group(1)} {match.group(2)}")
        if value is None:
            return None, None
        unit = match.group(2).lower()
        if unit == "ha" or property_type == "plot":
            return None, value
        return value, None

    @staticmethod
    def _rooms(text: str) -> int | None:
        match = re.search(r"(?:liczba\s+)?pokoi?\s*[:\-]?\s*(\d+)", text, re.IGNORECASE)
        if match is None:
            return None
        return parse_rooms(match.group(1))

    @staticmethod
    def _floor(text: str) -> tuple[int | None, str | None]:
        match = re.search(r"piętro\s*[:\-]?\s*([^\s,;]+)", text, re.IGNORECASE)
        if match is None:
            return None, None
        return parse_floor(match.group(1))

    @staticmethod
    def _round(title: str, description: str) -> int | None:
        normalized = normalize_text(f"{title} {description[:400]}")
        match = _ROUND_RE.search(normalized)
        if match is None:
            return None
        return _ROUNDS.get(match.group(1))

    @staticmethod
    def _case_number(title: str, description: str) -> str | None:
        for source in (description[:800], title):
            for match in _CASE_RE.finditer(source):
                value = match.group(0).strip()
                if value.startswith(("Dz.", "Art.", "Nr.")):
                    continue
                return value
        return None

    @staticmethod
    def _seller_type(text: str) -> str:
        normalized = normalize_text(text)
        if any(
            marker in normalized
            for marker in ("gmina", "urząd", "urzad", "starostwo", "powiat", "skarb państwa")
        ):
            return "government"
        if "syndyk" in normalized:
            return "agency"
        return "unknown"

    @staticmethod
    def _seller_name(title: str) -> str | None:
        match = re.match(
            r"((?:Wójt|Burmistrz|Prezydent|Starosta|Zarząd|Syndyk|Gmina|Miasto|Powiat)"
            r"[^,.]{0,80}?)\s+(?:ogłasza|oglasza|podaje|informuje|sprzeda|zbywa|przeznacza)",
            title,
            re.IGNORECASE,
        )
        if match is None:
            return None
        value = re.split(r"\s+działając\b", match.group(1), maxsplit=1)[0]
        return value.strip(" ,;") or None

    @staticmethod
    def _images(tree: Any) -> list[str]:
        images: list[str] = []
        for src in tree.xpath(
            '//div[contains(@class, "gallery")]//img/@src | //meta[@property="og:image"]/@content',
        ):
            url = urljoin(BASE_URL, src.strip())
            if url and url not in images:
                images.append(url)
        return images[:20]

    @staticmethod
    def _card_images(card: Any) -> list[str]:
        images: list[str] = []
        for style in card.xpath('.//div[contains(@class, "card-img-top")]/@style'):
            match = re.search(r"url\(['\"]?([^'\")]+)", style)
            if match:
                url = urljoin(BASE_URL, match.group(1))
                if url not in images:
                    images.append(url)
        return images[:20]

    @staticmethod
    def _meta(tree: Any, property_name: str) -> str | None:
        values = tree.xpath(f'//meta[@property="{property_name}"]/@content')
        return values[0].strip() if values and values[0].strip() else None

    @staticmethod
    def _total_items(html: str) -> int | None:
        match = _TOTAL_RE.search(html)
        if match is None:
            return None
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None

    @staticmethod
    def _total_pages(html: str) -> int | None:
        pages = [int(value) for value in _PAGE_RE.findall(html)]
        return max(pages) if pages else None

    @staticmethod
    def _tree(html: str) -> Any:
        try:
            return lxml_html.fromstring(html)
        except (TypeError, ValueError) as exc:
            raise ParseError(f"Otoprzetargi page is not valid HTML: {exc}") from exc
