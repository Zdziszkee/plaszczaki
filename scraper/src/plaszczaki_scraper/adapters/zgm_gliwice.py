"""ZGM Gliwice municipal flat-auction adapter (PDF wykazy).

The Zakład Gospodarki Miejskiej w Gliwicach publishes flats earmarked for
sale as one PDF *wykaz* per property.  The HTML page only links the documents,
so the adapter:

1. reads the listing page and collects the ``wykaz`` PDF links;
2. downloads each PDF through :mod:`plaszczaki_scraper.pdf` (cached by hash);
3. extracts the "Nieruchomość lokalowa" row from the document table.

Scanned PDFs (no text layer) are logged and skipped rather than guessed.  All
prices are the municipal starting prices of an auction-style sale, and the
documents never carry coordinates, so the geocoder resolves the address.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from lxml import html as lxml_html

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.models import Listing, PropertyType
from plaszczaki_scraper.normalize import normalize_text, parse_area, parse_price
from plaszczaki_scraper.pdf import PdfDocument, PdfError, extract_pdf, fetch_pdf
from plaszczaki_scraper.regions import RegionRegistry

logger = logging.getLogger(__name__)

SITE_ROOT = "https://zgm-gliwice.pl"
SEARCH_URL = f"{SITE_ROOT}/wykaz-lokali-przeznaczonych-do-sprzedazy-w-przetargu/"
REGION = "slaskie"
CITY = "Gliwice"
SELLER_NAME = "Zakład Gospodarki Miejskiej w Gliwicach"
RPS = 0.5
MAX_PDFS = 40
WARSAW = ZoneInfo("Europe/Warsaw")

_CASE_RE = re.compile(r"Wykaz\s+nr\s+(ZGM\s*/\s*DS\s*/\s*\d+\s*/\s*\d{4})", re.IGNORECASE)
_WYKAZ_LINE_RE = re.compile(r"^Wykaz\s+nr\b.*$", re.IGNORECASE)
_ANNOUNCEMENT_RE = re.compile(
    r"z\s+dnia\s+(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})\s*r\.",
    re.IGNORECASE,
)
_STREET_NUMBER_RE = re.compile(r"^(?P<name>.+?)\s+(?P<number>\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?)$")
_LOCAL_NUMBER_RE = re.compile(r"(?:lokal\s+nr|identyfikator\s+lokalu)", re.IGNORECASE)
_AREA_RE = re.compile(r"lokalu[:\s]+(\d[\d\s]*(?:[.,]\d+)?)", re.IGNORECASE)
_PRICE_DECIMALS_RE = re.compile(r",\d{2}(?!\d)")
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


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _tree(html: str) -> lxml_html.HtmlElement:
    if not html or not html.strip():
        raise ParseError("empty ZGM page")
    try:
        return lxml_html.fromstring(html)
    except Exception as exc:  # lxml raises etree.ParserError, which ships no stubs
        raise ParseError(f"ZGM page is not valid HTML: {exc}") from exc


def _date_ms(day: int, month: int, year: int) -> int | None:
    try:
        parsed = datetime(year, month, day, tzinfo=WARSAW)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _cell_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _pdf_links(tree: lxml_html.HtmlElement) -> list[tuple[str, str | None]]:
    """Return the ``wykaz`` PDF links in document order."""
    links: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for anchor in tree.xpath("//a[@href]"):
        href = anchor.get("href")
        if not href or ".pdf" not in href.lower():
            continue
        url = urljoin(SITE_ROOT, href)
        label = _text(anchor.text_content())
        if "wykaz" not in normalize_text(label or "") and "wykaz" not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append((url, label))
    return links


def _case_number(text: str) -> str | None:
    match = _CASE_RE.search(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _published_at(text: str) -> int | None:
    match = _ANNOUNCEMENT_RE.search(text)
    if match is None:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    return _date_ms(int(day), month, int(year))


def _wykaz_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if _WYKAZ_LINE_RE.match(stripped):
            return stripped
    return None


def _row_index(rows: Sequence[Sequence[str | None]], marker: str) -> int | None:
    for index, row in enumerate(rows):
        first = normalize_text(row[0] if row and row[0] else "")
        if first.startswith(marker):
            return index
    return None


def _address_and_local(row: Sequence[str | None]) -> tuple[str | None, str | None]:
    """Split the address cell into a street line and the flat number."""
    lines = _cell_lines(row[0] if row else None)
    if not lines:
        return None, None

    start: int | None = None
    for index, line in enumerate(lines):
        if normalize_text(line).startswith("nieruchomosc"):
            start = index + 1
            break
    if start is None:
        return None, None

    # The marker is often split over two lines: "Nieruchomość" / "lokalowa:".
    while start < len(lines) and normalize_text(lines[start]) in {"lokalowa", "lokal"}:
        start += 1

    address_parts: list[str] = []
    local: str | None = None
    for index in range(start, len(lines)):
        line = lines[index]
        normalized = normalize_text(line)
        if _LOCAL_NUMBER_RE.search(normalized):
            inline = re.split(r"[:]", line, maxsplit=1)
            if len(inline) == 2 and inline[1].strip():
                local = inline[1].strip()
            elif index + 1 < len(lines):
                candidate = lines[index + 1]
                if not _LOCAL_NUMBER_RE.search(normalize_text(candidate)):
                    local = candidate
            break
        if normalized.startswith("jednostka") or normalized.startswith("obreb"):
            break
        address_parts.append(line)

    address = _text(" ".join(address_parts))
    return address, local


def _street(address: str | None) -> str | None:
    if not address:
        return None
    match = _STREET_NUMBER_RE.match(address)
    if match is not None:
        return match.group("name").strip(" ,.") or None
    return address


def _area(row: Sequence[str | None]) -> float | None:
    if len(row) < 2 or not row[1]:
        return None
    raw = str(row[1]).replace("\n", " ")
    match = _AREA_RE.search(raw)
    if match is not None:
        return parse_area(match.group(1))
    for candidate in re.findall(r"\d+[.,]\d+", raw):
        value = parse_area(candidate)
        if value is not None:
            return value
    return None


def _price(row: Sequence[str | None]) -> tuple[int | None, int | None]:
    """Return ``(price, share_price)`` from the last filled price cell.

    A few wykazy sell a fractional share; their header packs both the full
    flat price and the share price into one cell separated by ``/``.
    """
    values = [cell for cell in row if _text(cell)]
    if not values:
        return None, None
    raw = _text(values[-1]) or ""
    parts = [part.strip() for part in raw.split("/")]
    price = _whole_zloty(parts[0]) if parts else None
    share = _whole_zloty(parts[1]) if len(parts) > 1 else None
    return price, share


def _whole_zloty(value: str) -> int | None:
    # Municipal wykazy print grosze (",00"); parse_price would read the
    # decimal part as more digits and inflate the price 100x.
    return parse_price(_PRICE_DECIMALS_RE.sub("", value))


def _property_type(row: Sequence[str | None]) -> PropertyType:
    # Only the "Opis nieruchomości" column names the premises themselves; the
    # land-use column mentions "usługowe" even for residential buildings.
    description = normalize_text(row[2] if len(row) > 2 and row[2] else "")
    if any(marker in description for marker in ("uzytkow", "uslugow", "handlow", "biurow")):
        return "commercial"
    return "flat"


def _price_row(rows: Sequence[Sequence[str | None]], local_index: int) -> Sequence[str | None]:
    """Fall back to the land row when the flat row leaves the price empty."""
    if _price(rows[local_index])[0] is not None:
        return rows[local_index]
    land_index = _row_index(rows, "nieruchomosc gruntowa")
    return rows[land_index] if land_index is not None else rows[local_index]


class ZgmGliwiceAdapter:
    """Read ZGM Gliwice's per-flat PDF wykazy into listings."""

    id = "zgm-gliwice"

    def __init__(
        self,
        *,
        source_id: str,
        regions: RegionRegistry,
        client: Any | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.source_id = source_id
        self.regions = regions
        self._client = client
        self.cache_dir = cache_dir
        # Populated by parse_search so operators can see what was skipped.
        self.stats: dict[str, int] = {
            "pdfs": 0,
            "skipped_scanned": 0,
            "failed": 0,
            "listings": 0,
        }

    def queries(self, regions: Sequence[str]) -> list[Query]:
        wanted = any(self.regions.canonical_voivodeship(region) == REGION for region in regions)
        if not wanted:
            return []
        return [
            Query(
                key=f"{REGION}:wykaz:{property_type}",
                url=SEARCH_URL,
                region=REGION,
                offer_type="auction",
                property_type=property_type,
            )
            for property_type in ("flat", "commercial")
        ]

    def with_page(self, query: Query, page: int) -> Query:
        # The listing page is a single, unpaginated index of PDF files.
        return replace(query, page=page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        tree = _tree(html)
        links = _pdf_links(tree)
        if not links:
            raise ParseError("ZGM page links no wykaz PDFs")

        self.stats = {"pdfs": 0, "skipped_scanned": 0, "failed": 0, "listings": 0}
        items: list[Listing] = []
        seen: set[str] = set()

        for url, label in links[:MAX_PDFS]:
            try:
                data = fetch_pdf(self._http(), url, cache_dir=self.cache_dir)
                document = extract_pdf(data)
            except PdfError as exc:
                self.stats["failed"] += 1
                logger.warning("zgm-gliwice: cannot read %s: %s", url, exc)
                continue

            self.stats["pdfs"] += 1
            if document.needs_ocr:
                self.stats["skipped_scanned"] += 1
                logger.warning(
                    "zgm-gliwice: %s has no text layer (%s chars/page); skipping scanned PDF",
                    url,
                    round(document.chars_per_page, 1),
                )
                continue

            for listing in self._listings(document, url=url, label=label, query=query):
                if listing.external_id in seen:
                    continue
                seen.add(listing.external_id)
                items.append(listing)

        self.stats["listings"] = len(items)
        return SearchPage(
            items=items,
            current_page=query.page,
            total_pages=1,
            total_items=len(items),
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        # Everything comes from the PDF parsed during discovery; the runner's
        # detail GET only confirms the file is still reachable.
        return summary

    def _http(self) -> Any:
        if self._client is None:
            from plaszczaki_scraper.http import HttpClient, user_agent

            self._client = HttpClient(rps=RPS, ua=user_agent())
        return self._client

    def _listings(
        self,
        document: PdfDocument,
        *,
        url: str,
        label: str | None,
        query: Query,
    ) -> list[Listing]:
        text = document.text
        case_number = _case_number(text)
        title = _wykaz_title(text) or label
        published_at = _published_at(text)
        listings: list[Listing] = []

        for page in document.pages:
            for table_index, rows in enumerate(page.tables):
                local_index = _row_index(rows, "nieruchomosc lokalowa")
                if local_index is None:
                    continue
                row = rows[local_index]
                address, local_number = _address_and_local(row)
                if not address:
                    logger.debug("zgm-gliwice: lokal row without address in %s", url)
                    continue

                price, share_price = _price(_price_row(rows, local_index))
                property_type = _property_type(row)
                if property_type != query.property_type:
                    continue

                external_id = self._external_id(case_number, local_number, document, row)
                description = _text(row[2]) if len(row) > 2 else None
                zg_extra: dict[str, Any] = {
                    "case_number": case_number,
                    "local_number": local_number,
                    "page_url": query.url,
                }
                if share_price is not None:
                    zg_extra["share_price_pln"] = share_price

                listings.append(
                    Listing(
                        source_id=self.source_id,
                        external_id=external_id,
                        url=url,
                        title=title,
                        description=description,
                        offer_type="auction",
                        property_type=property_type,
                        market="secondary",
                        price_kind="starting",
                        price_unit="total",
                        price_pln=price,
                        area_m2=_area(row),
                        voivodeship=REGION,
                        city=CITY,
                        street=_street(address),
                        address_text=f"{address}, {CITY}",
                        lat=None,
                        lng=None,
                        geo_source=None,
                        seller_type="government",
                        seller_name=SELLER_NAME,
                        published_at=published_at,
                        case_number=case_number,
                        extra={
                            "pdf": {
                                "url": url,
                                "page": page.number,
                                "table_index": table_index,
                                "row": list(row),
                            },
                            "zgm": zg_extra,
                        },
                    ),
                )

        if not listings and not any(
            _row_index(rows, "nieruchomosc lokalowa")
            for page in document.pages
            for rows in page.tables
        ):
            logger.warning("zgm-gliwice: no lokal table in %s", url)
        return listings

    @staticmethod
    def _external_id(
        case_number: str | None,
        local_number: str | None,
        document: PdfDocument,
        row: Sequence[str | None],
    ) -> str:
        base = case_number or document.sha256[:16]
        if local_number:
            return f"{base}#{local_number}"
        digest = normalize_text(" ".join(_text(cell) or "" for cell in row))[:40]
        return f"{base}#{digest or 'lokal'}"
