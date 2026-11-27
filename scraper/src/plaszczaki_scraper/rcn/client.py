"""Conditional, atomic downloads for the official RCN GeoParquet exports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    changed: bool
    etag: str | None
    last_modified: str | None
    sha256: str | None
    bytes_downloaded: int = 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dataset(
    url: str,
    destination: Path,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    force: bool = False,
    timeout: float = 900,
) -> DownloadResult:
    """Download a dataset, returning ``changed=False`` for an HTTP 304."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    headers = {
        "User-Agent": "plaszczaki-scraper/0.1 (+https://github.com/zdziszkee/plaszczaki)",
        "Accept": "application/octet-stream,application/parquet,*/*;q=0.1",
    }
    if not force:
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

    with (
        httpx.Client(follow_redirects=True, timeout=timeout) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code == 304 and destination.is_file():
            return DownloadResult(
                path=destination,
                changed=False,
                etag=response.headers.get("etag") or etag,
                last_modified=response.headers.get("last-modified") or last_modified,
                sha256=_sha256(destination),
            )
        response.raise_for_status()
        downloaded = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    handle.write(chunk)
                    downloaded += len(chunk)
            if downloaded <= 0:
                raise ValueError(f"empty RCN download: {url}")
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    return DownloadResult(
        path=destination,
        changed=True,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        sha256=_sha256(destination),
        bytes_downloaded=downloaded,
    )
