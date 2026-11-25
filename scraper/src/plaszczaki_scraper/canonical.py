"""Canonical listing identity used for cross-source deduplication.

Rows are never deleted or merged at write time: every source keeps its own row
and provenance. Instead each row gets a ``canonical_key`` and the web app shows
one representative per key.

Key precedence:

1. ``auction:<case>:<date>:<street>`` for bailiff/auction notices, which are
   syndicated verbatim between several sources.
2. ``geo:<lat>:<lng>:<area>:<rooms>:<offer>:<property>`` when coordinates are
   known (4 decimal places is roughly 11 m).
3. ``addr:<voivodeship>:<city>:<street>:<area>:<rooms>:<offer>:<property>``
   when only a street address is known.
4. ``src:<source_id>:<external_id>`` for everything else, which never matches
   another source.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import normalize_text

_STREET_PREFIXES = (
    "ul ",
    "ulica ",
    "al ",
    "aleja ",
    "os ",
    "osiedle ",
    "pl ",
    "plac ",
)


def _street(value: str | None) -> str:
    if not value:
        return ""
    normalized = normalize_text(value)
    for prefix in _STREET_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _area(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    return str(round(value))


def _date(value: int | None) -> str:
    """Date part of an epoch-millisecond timestamp in Europe/Warsaw."""
    if value is None:
        return "-"
    moment = datetime.fromtimestamp(value / 1000, tz=UTC)
    return moment.date().isoformat()


def _coordinates(lat: float | None, lng: float | None) -> tuple[str, str] | None:
    if lat is None or lng is None:
        return None
    # Several portals expose missing coordinates as 0,0.
    if lat == 0 or lng == 0 or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return None
    return f"{lat:.4f}", f"{lng:.4f}"


def canonical_key(listing: Listing) -> str:
    """Return the deduplication key for a listing."""
    street = _street(listing.street) or _street(listing.address_text)
    area = _area(listing.area_m2)
    rooms = "-" if listing.rooms is None else str(listing.rooms)

    if listing.case_number:
        case = normalize_text(listing.case_number).replace(" ", "")
        return f"auction:{case}:{_date(listing.auction_date)}:{street}"

    coordinates = _coordinates(listing.lat, listing.lng)
    if coordinates is not None:
        lat, lng = coordinates
        return f"geo:{lat}:{lng}:{area}:{rooms}:{listing.offer_type}:{listing.property_type}"

    if street:
        voivodeship = normalize_text(listing.voivodeship or "")
        city = normalize_text(listing.city or "")
        return (
            f"addr:{voivodeship}:{city}:{street}:{area}:{rooms}:"
            f"{listing.offer_type}:{listing.property_type}"
        )

    return f"src:{listing.source_id}:{listing.external_id}"
