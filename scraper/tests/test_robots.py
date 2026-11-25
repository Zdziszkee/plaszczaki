from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper import robots as robots_module
from plaszczaki_scraper.robots import RobotsCache


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_query_string_rules_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse(200, "User-agent: *\nDisallow: *page=*\n")

    monkeypatch.setattr(robots_module.httpx, "get", fake_get)
    cache = RobotsCache(user_agent="test-agent", cache_dir=tmp_path)

    assert cache.can_fetch("https://example.test/search?page=2") is False
    assert cache.can_fetch("https://example.test/search") is True
    assert len(calls) == 1


def test_403_denies_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        robots_module.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(403),
    )
    cache = RobotsCache(user_agent="test-agent", cache_dir=tmp_path)
    assert cache.can_fetch("https://example.test/anything") is False


def test_404_allows_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        robots_module.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(404),
    )
    cache = RobotsCache(user_agent="test-agent", cache_dir=tmp_path)
    assert cache.can_fetch("https://example.test/anything") is True


def test_cache_is_reused_across_instances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse(200, "User-agent: *\nDisallow: /private/\n")

    monkeypatch.setattr(robots_module.httpx, "get", fake_get)
    first = RobotsCache(user_agent="test-agent", cache_dir=tmp_path)
    assert first.can_fetch("https://example.test/private/x") is False

    second = RobotsCache(user_agent="test-agent", cache_dir=tmp_path)
    assert second.can_fetch("https://example.test/private/x") is False
    assert len(calls) == 1
