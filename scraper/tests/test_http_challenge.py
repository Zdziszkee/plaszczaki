from __future__ import annotations

from dataclasses import dataclass, field

from plaszczaki_scraper.http import challenge_vendor


@dataclass
class FakeResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def test_cloudflare_managed_challenge_is_detected() -> None:
    response = FakeResponse(headers={"cf-mitigated": "challenge"}, body=b"<html>")

    assert challenge_vendor(response) == "Cloudflare"


def test_cloudflare_body_marker_is_detected() -> None:
    response = FakeResponse(body=b"<script src='https://challenges.cloudflare.com/x.js'>")

    assert challenge_vendor(response) == "Cloudflare"


def test_aws_waf_challenge_is_detected() -> None:
    response = FakeResponse(headers={"x-amzn-waf-action": "challenge"})

    assert challenge_vendor(response) == "AWS WAF"


def test_datadome_header_is_detected() -> None:
    response = FakeResponse(headers={"x-datadome-cid": "abc"})

    assert challenge_vendor(response) == "DataDome"


def test_imperva_shell_is_detected_from_body() -> None:
    response = FakeResponse(body=b"<iframe src='/_Incapsula_Resource?CWUDNSAI=1'>")

    assert challenge_vendor(response) == "Imperva"


def test_f5_rejection_is_detected() -> None:
    response = FakeResponse(body=b"Request Rejected. Your support ID is: 123")

    assert challenge_vendor(response) == "F5 ASM"


def test_normal_page_is_not_a_challenge() -> None:
    response = FakeResponse(
        body=b"<html><body>Mieszkanie na sprzedaz w Krakowie</body></html>",
    )

    assert challenge_vendor(response) is None
