from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from scrapling.fetchers import StealthyFetcher

from plaszczaki_scraper.http import HttpClient


@dataclass
class _Response:
    status: int = 200
    headers: dict[str, str] | None = None
    body: bytes = b"<html>ok</html>"


class _Robots:
    def can_fetch(self, url: str) -> bool:
        return True

    def crawl_delay(self, url: str) -> float | None:
        return None


def test_stealth_fetch_uses_the_browser(monkeypatch: Any) -> None:
    calls: dict[str, Any] = {}

    def fake_fetch(url: str, **kwargs: Any) -> _Response:
        calls["url"] = url
        calls.update(kwargs)
        return _Response()

    monkeypatch.setattr(StealthyFetcher, "fetch", staticmethod(fake_fetch))
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        fetch_mode="stealth",
        browser_path="/usr/bin/chromium",
    )

    response = client.get("https://example.test/page")

    assert response.status == 200
    assert calls["url"] == "https://example.test/page"
    assert calls["headless"] is True
    assert calls["executable_path"] == "/usr/bin/chromium"
    assert calls["solve_cloudflare"] is False
    assert calls["timeout"] == 30_000


def test_http_mode_does_not_start_a_browser(monkeypatch: Any) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("stealth fetcher must not be used in http mode")

    def fake_get(url: str, **_: Any) -> _Response:
        return _Response()

    import plaszczaki_scraper.http as http_module

    monkeypatch.setattr(
        http_module, "Fetcher", type("_Fetcher", (), {"get": staticmethod(fake_get)})
    )
    monkeypatch.setattr(StealthyFetcher, "fetch", staticmethod(fail))
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        fetch_mode="http",
        browser_path="/usr/bin/chromium",
    )

    response = client.get("https://example.test/page")

    assert response.status == 200
    assert client.fetch_mode == "http"
