from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import plaszczaki_scraper.http as http_module
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


SESSIONS: list[_FakeSession] = []


class _FakeSession:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.urls: list[str] = []
        SESSIONS.append(self)

    def start(self) -> None:
        self.started = True

    def fetch(self, url: str) -> _Response:
        self.urls.append(url)
        return _Response()

    def close(self) -> None:
        self.closed = True


def test_stealth_fetch_reuses_one_browser_session(monkeypatch: Any) -> None:
    SESSIONS.clear()
    monkeypatch.setattr(http_module, "StealthySession", _FakeSession)
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        fetch_mode="stealth",
        browser_path="/usr/bin/chromium",
        browser_solve_cloudflare=True,
    )

    first = client.get("https://example.test/one")
    second = client.get("https://example.test/two")

    assert first.status == second.status == 200
    assert len(SESSIONS) == 1
    session = SESSIONS[0]
    assert session.started is True
    assert session.kwargs["headless"] is True
    assert session.kwargs["executable_path"] == "/usr/bin/chromium"
    assert session.kwargs["solve_cloudflare"] is True
    assert session.kwargs["timeout"] == 30_000
    assert session.urls == ["https://example.test/one", "https://example.test/two"]

    client.close()
    assert session.closed is True
    assert client._stealth is None


def test_stealth_options_are_forwarded(monkeypatch: Any) -> None:
    SESSIONS.clear()
    monkeypatch.setattr(http_module, "StealthySession", _FakeSession)
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        fetch_mode="stealth",
        browser_headless=False,
        browser_solve_cloudflare=True,
    )

    client.get("https://example.test/page")

    session = SESSIONS[0]
    assert session.kwargs["headless"] is False
    assert session.kwargs["solve_cloudflare"] is True
    client.close()


def test_http_mode_does_not_start_a_browser(monkeypatch: Any) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("stealth session must not be used in http mode")

    def fake_get(url: str, **_: Any) -> _Response:
        return _Response()

    monkeypatch.setattr(http_module, "StealthySession", fail)
    monkeypatch.setattr(
        http_module,
        "Fetcher",
        type("_Fetcher", (), {"get": staticmethod(fake_get)}),
    )
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        fetch_mode="http",
        browser_path="/usr/bin/chromium",
    )

    response = client.get("https://example.test/page")

    assert response.status == 200
    assert client.fetch_mode == "http"
    client.close()
