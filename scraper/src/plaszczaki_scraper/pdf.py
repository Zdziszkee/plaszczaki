"""PDF and ZIP extraction for PDF-only sources.

Some official sources (municipal *wykazy*, company tender portals) publish
their inventory only as PDF or ZIP downloads.  This module keeps the document
handling in one place:

* :func:`fetch_pdf` downloads through the shared ``HttpClient`` so robots.txt,
  pacing and retries still apply, then caches the bytes by SHA-256;
* :func:`extract_pdf` turns a document into per-page text and tables and
  reports whether it carries a text layer at all;
* :func:`extract_zip` lists the members of an archive without ever writing
  them to disk.

Scanned documents are *not* OCRed here.  ``PdfDocument.needs_ocr`` lets an
adapter skip them instead of inventing rows.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import stat
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from plaszczaki_scraper.robots import default_cache_dir

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"
DEFAULT_MAX_BYTES = 25_000_000
DEFAULT_ZIP_MAX_BYTES = 50_000_000
# A page with fewer than this many characters is treated as image-only.
MIN_CHARS_PER_PAGE = 50
# URL -> hash entries are trusted for a day so a daily poll still notices a
# revised document served from the same address.
CACHE_TTL_SECONDS = 24 * 60 * 60
_INDEX_NAME = "index.json"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PdfError(RuntimeError):
    """Raised when a PDF or ZIP cannot be fetched or decoded."""


class PdfInvalid(PdfError):
    """Raised when the payload is not a valid document of the expected type."""


class PdfTooLarge(PdfError):
    """Raised when a document exceeds the configured size or count limits."""


@dataclass(slots=True)
class PdfPage:
    """One decoded PDF page."""

    number: int
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text.strip())


@dataclass(slots=True)
class PdfDocument:
    """A decoded PDF with its text layer, tables and document metadata."""

    pages: list[PdfPage]
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    size_bytes: int = 0
    sha256: str = ""

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    @property
    def tables(self) -> list[list[list[str | None]]]:
        return [table for page in self.pages for table in page.tables]

    @property
    def chars(self) -> int:
        return sum(page.chars for page in self.pages)

    @property
    def chars_per_page(self) -> float:
        return self.chars / self.page_count if self.page_count else 0.0

    @property
    def has_text_layer(self) -> bool:
        return self.chars_per_page >= MIN_CHARS_PER_PAGE

    @property
    def needs_ocr(self) -> bool:
        """True when the document looks scanned (no usable text layer)."""
        return not self.has_text_layer


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """One file extracted from a ZIP archive."""

    name: str
    data: bytes

    @property
    def is_pdf(self) -> bool:
        return _looks_like_pdf(self.data)


def fetch_pdf(
    client: Any,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    cache_dir: Path | None = None,
) -> bytes:
    """Fetch a PDF with the shared HTTP client and cache it by SHA-256.

    ``client`` is an ``HttpClient`` (or anything exposing a compatible
    ``get(url)``).  Cached bytes are keyed by content hash under
    ``<cache>/pdf/<sha>.pdf``; a small ``index.json`` maps URLs to hashes so a
    repeated run does not download the same document again.
    """
    cache = (cache_dir or default_cache_dir()) / "pdf"
    data = _cached(cache, url)
    if data is not None:
        return data

    response = client.get(url)
    status = int(getattr(response, "status", 0) or 0)
    if status and not 200 <= status < 300:
        raise PdfError(f"HTTP {status} for {url}")

    declared = _content_length(response)
    if declared is not None and declared > max_bytes:
        raise PdfTooLarge(f"{url} declares {declared} bytes (limit {max_bytes})")

    data = _response_body(response)
    if len(data) > max_bytes:
        raise PdfTooLarge(f"{url} is {len(data)} bytes (limit {max_bytes})")
    if not _looks_like_pdf(data):
        raise PdfInvalid(f"{url} is not a PDF (missing %PDF magic)")

    sha = hashlib.sha256(data).hexdigest()
    _store(cache, url, sha, data)
    return data


def extract_pdf(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> PdfDocument:
    """Decode a PDF into per-page text and tables using pdfplumber."""
    if not data:
        raise PdfInvalid("empty PDF payload")
    if len(data) > max_bytes:
        raise PdfTooLarge(f"PDF is {len(data)} bytes (limit {max_bytes})")
    if not _looks_like_pdf(data):
        raise PdfInvalid("not a PDF (missing %PDF magic)")

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # pdfminer raises several document-specific errors
        raise PdfInvalid(f"cannot open PDF: {exc}") from exc

    pages: list[PdfPage] = []
    try:
        with pdf:
            metadata = dict(pdf.metadata or {})
            for number, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    tables = page.extract_tables() or []
                except Exception as exc:
                    raise PdfInvalid(f"cannot decode PDF page {number}: {exc}") from exc
                pages.append(PdfPage(number=number, text=text, tables=tables))
    except PdfError:
        raise
    except Exception as exc:
        raise PdfInvalid(f"cannot decode PDF: {exc}") from exc

    if not pages:
        raise PdfInvalid("PDF has no pages")

    return PdfDocument(
        pages=pages,
        title=_metadata_title(metadata),
        metadata=metadata,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def extract_zip(
    data: bytes,
    *,
    max_files: int = 20,
    max_bytes: int = DEFAULT_ZIP_MAX_BYTES,
) -> list[ArchiveMember]:
    """Return the regular files of a ZIP archive, in archive order.

    Unsafe member names (absolute paths, ``..`` segments, Windows drive
    letters, symlinks) are skipped with a warning.  Nothing is written to
    disk, so traversal is contained by construction.
    """
    if not data:
        raise PdfInvalid("empty ZIP payload")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PdfInvalid(f"not a ZIP archive: {exc}") from exc

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > max_files:
            raise PdfTooLarge(f"ZIP has {len(infos)} files (limit {max_files})")
        if sum(info.file_size for info in infos) > max_bytes:
            raise PdfTooLarge(f"ZIP expands beyond {max_bytes} bytes")

        members: list[ArchiveMember] = []
        total = 0
        for info in infos:
            if _is_unsafe_member(info):
                logger.warning("skipping unsafe ZIP member %r", info.filename)
                continue
            remaining = max_bytes - total
            try:
                with archive.open(info) as handle:
                    payload = handle.read(remaining + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise PdfInvalid(f"cannot read ZIP member {info.filename!r}: {exc}") from exc
            if len(payload) > remaining:
                raise PdfTooLarge(f"ZIP member {info.filename!r} exceeds {max_bytes} bytes")
            total += len(payload)
            members.append(ArchiveMember(name=info.filename, data=payload))

    return members


def _looks_like_pdf(data: bytes) -> bool:
    # Readers tolerate leading junk; the header must appear near the start.
    return PDF_MAGIC in data[:1024]


def _metadata_title(metadata: dict[str, Any]) -> str | None:
    for key, value in metadata.items():
        if str(key).strip().lower() == "title" and isinstance(value, str):
            title = " ".join(value.split())
            return title or None
    return None


def _content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get("content-length") or headers.get("Content-Length")
    except AttributeError:  # pragma: no cover - exotic header containers
        return None
    try:
        return int(value) if value is not None else None
    except TypeError, ValueError:
        return None


def _response_body(response: Any) -> bytes:
    body = getattr(response, "body", None)
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, bytes):
        return body
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    raise PdfInvalid("response has no binary body")


def _is_unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    if not name or name.startswith("/") or _DRIVE_RE.match(name):
        return True
    if any(part == ".." for part in name.split("/")):
        return True
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _index_path(cache: Path) -> Path:
    return cache / _INDEX_NAME


def _read_index(cache: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads((cache / _INDEX_NAME).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}

    index: dict[str, dict[str, Any]] = {}
    for url, value in payload.items():
        if isinstance(value, str) and value:
            # Legacy plain-hash entries carry no timestamp: treat as expired.
            index[str(url)] = {"sha": value, "fetched_at": 0.0}
        elif isinstance(value, dict) and isinstance(value.get("sha"), str):
            fetched_at = value.get("fetched_at")
            index[str(url)] = {
                "sha": value["sha"],
                "fetched_at": float(fetched_at) if isinstance(fetched_at, int | float) else 0.0,
            }
    return index


def _cached(cache: Path, url: str) -> bytes | None:
    entry = _read_index(cache).get(url)
    if not entry:
        return None
    if time.time() - float(entry["fetched_at"]) > CACHE_TTL_SECONDS:
        return None
    try:
        data = (cache / f"{entry['sha']}.pdf").read_bytes()
    except OSError:
        return None
    return data if _looks_like_pdf(data) else None


def _store(cache: Path, url: str, sha: str, data: bytes) -> None:
    index_path = _index_path(cache)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{sha}.pdf"
        if not target.exists():
            temporary = cache / f".{sha}.tmp"
            temporary.write_bytes(data)
            os.replace(temporary, target)
        index = _read_index(cache)
        index[url] = {"sha": sha, "fetched_at": time.time()}
        temporary_index = index_path.with_suffix(".tmp")
        temporary_index.write_text(
            json.dumps(index, ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_index, index_path)
    except OSError as exc:  # a read-only cache must not fail a scrape
        logger.warning("cannot cache PDF %s: %s", url, exc)
