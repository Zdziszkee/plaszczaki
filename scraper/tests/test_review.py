from __future__ import annotations

from dataclasses import dataclass

from plaszczaki_scraper.review import compare_fingerprints, fingerprint


@dataclass
class FakeResponse:
    status: int = 200
    headers: dict[str, str] | None = None
    body: bytes = b""

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


def test_fingerprint_counts_listing_links_and_location_signals() -> None:
    body = (
        b"<html><head><title>Oferty</title>"
        b'<script type="application/ld+json">{"latitude": 50.1}</script></head>'
        b'<body><a href="/oferta/1">A</a>'
        b'<a href="/nieruchomosc/2">B</a>'
        b"<p>ul. Konecka 42, 97-330</p></body></html>"
    )

    result = fingerprint(FakeResponse(body=body))

    assert result["status"] == 200
    assert result["title"] == "Oferty"
    assert result["listing_links"] == 2
    assert result["json_ld"] is True
    assert result["coordinates"] is True
    assert result["street"] is True
    assert result["postal"] is True
    assert result["challenge"] is None


def test_compare_reports_first_observation() -> None:
    assert compare_fingerprints(None, {"status": 200}) == ["first observation"]


def test_compare_reports_cleared_challenge_and_new_location() -> None:
    previous = {
        "status": 200,
        "challenge": "Cloudflare",
        "listing_links": 0,
        "coordinates": False,
    }
    current = {
        "status": 200,
        "challenge": None,
        "listing_links": 12,
        "coordinates": True,
    }

    changes = compare_fingerprints(previous, current)

    assert "challenge cleared (Cloudflare)" in changes
    assert "listing links appeared (12)" in changes
    assert "coordinates appeared" in changes


def test_compare_reports_status_change_and_failure() -> None:
    assert compare_fingerprints({"status": 403}, {"status": 200}) == ["status 403 -> 200"]
    assert compare_fingerprints({"status": 200}, None) == ["fetch failed"]


def test_compare_ignores_unchanged_fingerprints() -> None:
    current = {"status": 200, "challenge": None, "listing_links": 3, "coordinates": True}

    assert compare_fingerprints(current, dict(current)) == []
