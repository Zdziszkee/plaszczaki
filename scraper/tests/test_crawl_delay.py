from __future__ import annotations

from datetime import date as real_date
from pathlib import Path
from typing import Any, cast

import pytest

from plaszczaki_scraper.adapters import gdom as gdom_module
from plaszczaki_scraper.adapters.gdom import CATEGORIES, GdomAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.http import CrawlDeferred, HostDelayStore, HttpClient
from plaszczaki_scraper.regions import RegionRegistry


class _Robots:
    def can_fetch(self, url: str) -> bool:
        return True

    def crawl_delay(self, url: str) -> float | None:
        return 600.0


class _Response:
    status = 200
    headers = None
    body = b"<html>ok</html>"


def test_host_delay_store_remembers_the_last_request(tmp_path: Path) -> None:
    store = HostDelayStore(tmp_path / "delays.json")

    assert store.remaining("gdom.pl", 600) == 0
    store.mark("gdom.pl")

    remaining = store.remaining("gdom.pl", 600)
    assert 590 < remaining <= 600
    assert store.remaining("other.pl", 600) == 0


def test_defer_mode_raises_instead_of_sleeping(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_: Any) -> _Response:
        calls.append(url)
        return _Response()

    import plaszczaki_scraper.http as http_module

    monkeypatch.setattr(
        http_module,
        "Fetcher",
        type("_Fetcher", (), {"get": staticmethod(fake_get)}),
    )
    client = HttpClient(
        rps=100,
        robots=cast(Any, _Robots()),
        crawl_delay_mode="defer",
        delay_store=HostDelayStore(tmp_path / "delays.json"),
    )

    assert client.get("https://gdom.pl/kategoria-ogloszen/domy/").status == 200
    with pytest.raises(CrawlDeferred):
        client.get("https://gdom.pl/kategoria-ogloszen/mieszkania/")
    assert calls == ["https://gdom.pl/kategoria-ogloszen/domy/"]


def test_gdom_rotates_one_category_per_day(monkeypatch: Any) -> None:
    adapter = GdomAdapter(
        source_id="gdom",
        regions=RegionRegistry(load_regions_config()),
    )

    class _FakeDate:
        value = real_date(2026, 11, 25)

        @classmethod
        def today(cls) -> real_date:
            return cls.value

    monkeypatch.setattr(gdom_module, "date", _FakeDate)

    first_day = adapter.queries(("malopolskie",))
    _FakeDate.value = real_date(2026, 11, 26)
    second_day = adapter.queries(("malopolskie",))

    assert len(first_day) == len(CATEGORIES)
    assert first_day[0].key != second_day[0].key
    assert {query.key for query in first_day} == {query.key for query in second_day}
