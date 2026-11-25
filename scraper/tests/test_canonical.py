from __future__ import annotations

from typing import Any

from plaszczaki_scraper.canonical import canonical_key
from plaszczaki_scraper.models import Listing


def _listing(**overrides: Any) -> Listing:
    values: dict[str, Any] = {
        "source_id": "source-a",
        "external_id": "42",
        "url": "https://example.test/42",
    }
    values.update(overrides)
    return Listing(**values)


def test_auction_notices_share_a_key_across_sources() -> None:
    first = _listing(
        case_number="Km 123/24",
        auction_date=1_800_000_000_000,
        street="ul. Górniczego Dorobku",
        offer_type="auction",
    )
    second = _listing(
        source_id="source-b",
        external_id="99",
        case_number="KM 123 / 24",
        auction_date=1_800_000_000_000,
        street="Górniczego Dorobku",
        offer_type="auction",
    )

    assert canonical_key(first) == canonical_key(second)
    assert canonical_key(first).startswith("auction:km12324:")


def test_coordinates_take_precedence_over_the_street() -> None:
    listing = _listing(
        lat=50.06143,
        lng=19.93658,
        area_m2=52.4,
        rooms=2,
        street="ul. Rynek Główny 1",
        offer_type="sale",
        property_type="flat",
    )

    assert canonical_key(listing) == "geo:50.0614:19.9366:52:2:sale:flat"


def test_zero_coordinates_are_treated_as_missing() -> None:
    listing = _listing(
        lat=0.0,
        lng=0.0,
        street="ul. Rynek Główny 1",
        city="Kraków",
        voivodeship="małopolskie",
        offer_type="sale",
        property_type="flat",
    )

    assert canonical_key(listing).startswith("addr:")


def test_street_key_normalises_prefix_and_diacritics() -> None:
    first = _listing(
        street="ul. Górniczego Dorobku",
        city="Katowice",
        voivodeship="śląskie",
        offer_type="sale",
        property_type="plot",
    )
    second = _listing(
        source_id="source-b",
        street="Gorniczego Dorobku",
        city="KATOWICE",
        voivodeship="slaskie",
        offer_type="sale",
        property_type="plot",
    )

    assert canonical_key(first) == canonical_key(second)


def test_listings_without_location_stay_source_scoped() -> None:
    listing = _listing(source_id="portal", external_id="abc")

    assert canonical_key(listing) == "src:portal:abc"


def test_address_text_is_used_when_street_is_missing() -> None:
    listing = _listing(
        address_text="ul. Konecka 42, 97-330 Sulejów",
        city="Sulejów",
        voivodeship="łódzkie",
        offer_type="auction",
        property_type="flat",
    )

    assert canonical_key(listing).startswith("addr:")
    assert "konecka 42" in canonical_key(listing)
