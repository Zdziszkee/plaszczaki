from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from plaszczaki_scraper.pdf import (
    PdfError,
    PdfInvalid,
    PdfTooLarge,
    extract_pdf,
    extract_zip,
    fetch_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"
WYKAZ = FIXTURES / "wykaz-commercial.pdf"


def _pdf_bytes() -> bytes:
    return WYKAZ.read_bytes()


class _Response:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls = 0

    def get(self, url: str, **_: object) -> _Response:
        self.calls += 1
        return self.response


def _zip(payload: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_extract_pdf_text_and_tables() -> None:
    document = extract_pdf(_pdf_bytes())

    assert document.page_count == 1
    assert document.has_text_layer is True
    assert document.needs_ocr is False
    assert document.chars_per_page > 500
    assert "Dunikowskiego" in document.text
    assert "37,29" in document.text
    assert document.sha256 == hashlib.sha256(_pdf_bytes()).hexdigest()

    header = document.tables[0][0][0]
    assert header is not None
    assert "Adres" in header


def test_scanned_pdf_needs_ocr() -> None:
    document = extract_pdf((FIXTURES / "scanned.pdf").read_bytes())

    assert document.page_count == 1
    assert document.text == ""
    assert document.has_text_layer is False
    assert document.needs_ocr is True


def test_extract_pdf_rejects_invalid_payloads() -> None:
    with pytest.raises(PdfInvalid, match="empty"):
        extract_pdf(b"")
    with pytest.raises(PdfInvalid, match="magic"):
        extract_pdf(b"<html>not a pdf</html>")
    with pytest.raises(PdfTooLarge):
        extract_pdf(_pdf_bytes(), max_bytes=10)


def test_fetch_pdf_caches_by_sha(tmp_path: Path) -> None:
    data = _pdf_bytes()
    client = _Client(_Response(data))

    first = fetch_pdf(client, "https://example.test/wykaz.pdf", cache_dir=tmp_path)
    second = fetch_pdf(client, "https://example.test/wykaz.pdf", cache_dir=tmp_path)

    assert first == second == data
    assert client.calls == 1
    sha = hashlib.sha256(data).hexdigest()
    assert (tmp_path / "pdf" / f"{sha}.pdf").read_bytes() == data


def test_fetch_pdf_refetches_after_the_ttl(tmp_path: Path) -> None:
    data = _pdf_bytes()
    client = _Client(_Response(data))
    url = "https://example.test/wykaz.pdf"

    fetch_pdf(client, url, cache_dir=tmp_path)
    index_path = tmp_path / "pdf" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index.values():
        entry["fetched_at"] = 0
    index_path.write_text(json.dumps(index), encoding="utf-8")

    fetch_pdf(client, url, cache_dir=tmp_path)

    assert client.calls == 2


def test_fetch_pdf_rejects_non_pdf_and_oversized(tmp_path: Path) -> None:
    with pytest.raises(PdfInvalid, match="magic"):
        fetch_pdf(
            _Client(_Response(b"<html>blocked</html>")),
            "https://example.test/a.pdf",
            cache_dir=tmp_path,
        )

    with pytest.raises(PdfTooLarge, match="declares"):
        fetch_pdf(
            _Client(_Response(b"%PDF-1.7", headers={"content-length": "9000000"})),
            "https://example.test/b.pdf",
            max_bytes=1000,
            cache_dir=tmp_path,
        )

    with pytest.raises(PdfTooLarge, match="limit"):
        fetch_pdf(
            _Client(_Response(b"%PDF-" + b"0" * 2000)),
            "https://example.test/c.pdf",
            max_bytes=1000,
            cache_dir=tmp_path,
        )


def test_fetch_pdf_reports_http_errors(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match="HTTP 404"):
        fetch_pdf(
            _Client(_Response(b"", status=404)),
            "https://example.test/missing.pdf",
            cache_dir=tmp_path,
        )


def test_extract_zip_skips_traversal_members() -> None:
    data = _zip(
        [
            ("wykaz.pdf", _pdf_bytes()),
            ("../evil.pdf", b"%PDF-1.7 evil"),
            ("/absolute.pdf", b"%PDF-1.7 absolute"),
            ("nested/../again.pdf", b"%PDF-1.7 again"),
        ],
    )

    members = extract_zip(data)

    assert [member.name for member in members] == ["wykaz.pdf"]
    assert members[0].is_pdf is True


def test_extract_zip_enforces_limits() -> None:
    data = _zip([("a.pdf", _pdf_bytes()), ("b.pdf", _pdf_bytes())])

    with pytest.raises(PdfTooLarge, match="files"):
        extract_zip(data, max_files=1)

    with pytest.raises(PdfInvalid, match="ZIP"):
        extract_zip(b"not a zip")

    with pytest.raises(PdfInvalid, match="empty"):
        extract_zip(b"")
