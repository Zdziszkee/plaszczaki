from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from plaszczaki_scraper.rcn import client


def test_download_dataset_writes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"PAR1example",
            headers={"etag": '"new"', "last-modified": "today"},
            request=request,
        )
    )
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    destination = tmp_path / "nested" / "dataset.parquet"

    result = client.download_dataset("https://example.test/data", destination)

    assert result.changed is True
    assert result.etag == '"new"'
    assert result.bytes_downloaded == len(b"PAR1example")
    assert destination.read_bytes() == b"PAR1example"
    assert not destination.with_suffix(".parquet.part").exists()
    assert len(result.sha256 or "") == 64


def test_download_dataset_304_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(304, headers={"etag": '"same"'}, request=request)
    )
    monkeypatch.setattr(
        client.httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    destination = tmp_path / "dataset.parquet"
    destination.write_bytes(b"PAR1cached")

    result = client.download_dataset(
        "https://example.test/data",
        destination,
        etag='"same"',
        last_modified="yesterday",
    )

    assert result.changed is False
    assert result.etag == '"same"'
    assert result.last_modified == "yesterday"
    assert result.sha256 is not None
