from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.olx import OlxAdapter
from plaszczaki_scraper.adapters.otodom import OtodomAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import parse_datetime_ms
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _regions() -> RegionRegistry:
    return RegionRegistry(load_regions_config())


def test_otodom_detail_normalizes_attributes() -> None:
    adapter = OtodomAdapter(source_id="otodom", regions=_regions())
    search = (FIXTURES / "otodom" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "otodom" / "detail.html").read_text(encoding="utf-8")
    query = adapter.queries(("malopolskie",))[0]
    summary = adapter.parse_search(search, query).items[1]

    detail = adapter.parse_detail(detail_html, summary)

    assert detail.condition == "to_finish"
    assert detail.ownership == "full_ownership"
    assert detail.building_floors == 1
    assert detail.security == "gated_area"
    assert detail.available_from == parse_datetime_ms("2027-05-15")
    assert detail.market == "primary"


def test_olx_detail_normalizes_furnishing_and_market() -> None:
    adapter = OlxAdapter(source_id="olx", regions=_regions())
    search = (FIXTURES / "olx" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "olx" / "detail.html").read_text(encoding="utf-8")
    query = next(
        item
        for item in adapter.queries(("malopolskie",))
        if item.key == "malopolskie:sprzedaz:mieszkania"
    )
    summary = adapter.parse_search(search, query).items[0]

    detail = adapter.parse_detail(detail_html, summary)

    assert detail.furnished is True
    assert detail.market == "secondary"
    assert detail.balcony is None  # Description mining is a separate phase.


def test_auction_appraisal_and_zero_costs() -> None:
    auction = Listing(
        source_id="komornik",
        external_id="2",
        url="https://example.test/2",
        offer_type="auction",
        property_type="house",
        price_pln=300_000,
        area_m2=100,
        extra={"komornik": {"estimate": 400_000}},
    )
    rental = Listing(
        source_id="otodom",
        external_id="3",
        url="https://example.test/3",
        offer_type="rent",
        property_type="flat",
        rent_pln=2000,
        area_m2=40,
        extra={
            "otodom": {
                "characteristics": {
                    "rent": {"value": "0"},
                    "deposit": {"value": "0"},
                }
            }
        },
    )

    assert auction.estimate_pln == 400_000
    assert auction.price_per_m2_pln == 3000
    assert rental.administrative_rent_pln is None
    assert rental.deposit_pln is None
    assert rental.price_per_m2_pln == 50


def test_market_and_availability_from_small_adapters() -> None:
    price_only_rental = Listing(
        source_id="example",
        external_id="price-only-rental",
        url="https://example.test/price-only-rental",
        offer_type="rent",
        property_type="flat",
        price_pln=2400,
        area_m2=40,
    )
    listing = Listing(
        source_id="rentola",
        external_id="4",
        url="https://example.test/4",
        offer_type="rent",
        property_type="flat",
        extra={"rentola": {"valid_from": "2026-12-01T10:00:00Z"}},
    )
    secondary = Listing(
        source_id="homeportal",
        external_id="5",
        url="https://example.test/5",
        offer_type="sale",
        property_type="flat",
        extra={"homeportal": {"market": "wtórny", "deposit": None}},
    )

    assert price_only_rental.rent_pln == 2400
    assert price_only_rental.price_per_m2_pln == 60
    assert listing.available_from == parse_datetime_ms("2026-12-01T10:00:00Z")
    assert secondary.market == "secondary"


def test_long_source_labels_are_canonicalized() -> None:
    listing = Listing(
        source_id="adresowo",
        external_id="labels",
        url="https://example.test/labels",
        offer_type="sale",
        property_type="flat",
        extra={
            "adresowo": {
                "condition": "Budynek w stanie do zamieszczenia",
                "parameters": [
                    {"label": "Ogrzewanie", "value": "C.O. Z SIECI MIEJSKIEJ"},
                    {"label": "Ogrodzenie działki", "value": "brak"},
                    {"label": "Stolarka okienna", "value": "Nowe PCV"},
                    {"label": "Typ własności", "value": "własność"},
                ],
            }
        },
    )

    assert listing.condition == "move_in_ready"
    assert listing.heating == "district"
    assert listing.fenced is False
    assert listing.windows_type == "plastic"
    assert listing.ownership == "full_ownership"


def test_additional_information_and_commercial_security() -> None:
    rental = Listing(
        source_id="allegrolokalnie",
        external_id="extra",
        url="https://example.test/extra",
        extra={"allegrolokalnie": {"params": {"informacje dodatkowe": "winda"}}},
    )
    office = Listing(
        source_id="axiimmo",
        external_id="office",
        url="https://example.test/office",
        extra={"axiimmo": {"properties": {"24/7 security": ["Yes"]}}},
    )

    assert rental.elevator is True
    assert office.security == "24_7"


def test_media_type_list_sets_internet_boolean() -> None:
    connected = Listing(
        source_id="otodom",
        external_id="connected",
        url="https://example.test/connected",
        extra={"otodom": {"target": {"Media_types": ["internet", "phone"]}}},
    )
    offline = Listing(
        source_id="otodom",
        external_id="offline",
        url="https://example.test/offline",
        extra={"otodom": {"target": {"Media_types": ["water", "electricity"]}}},
    )

    assert connected.internet is True
    assert offline.internet is False


def test_fence_type_list_means_fenced_not_false() -> None:
    listing = Listing(
        source_id="otodom",
        external_id="fence",
        url="https://example.test/fence",
        extra={"otodom": {"target": {"Fence_types": ["metal", "brick"]}}},
    )

    assert listing.fenced is True


def test_non_condition_text_is_not_promoted_to_condition() -> None:
    listing = Listing(
        source_id="adresowo",
        external_id="6",
        url="https://example.test/6",
        offer_type="sale",
        property_type="flat",
        extra={"adresowo": {"condition": "okna plastikowe"}},
    )

    assert listing.condition is None


def test_otodom_district_is_preserved_for_future_backfills() -> None:
    adapter = OtodomAdapter(source_id="otodom", regions=_regions())
    search = (FIXTURES / "otodom" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "otodom" / "detail.html").read_text(encoding="utf-8")
    query = adapter.queries(("malopolskie",))[0]
    summary = adapter.parse_search(search, query).items[1]

    detail = adapter.parse_detail(detail_html, summary)

    assert detail.extra["otodom"]["location_levels"]["city_or_village"] == "Rząska"
    located = Listing(
        source_id="otodom",
        external_id="located",
        url="https://example.test/located",
        extra={"otodom": {"location_levels": {"district": "Stare Miasto"}}},
    )
    assert located.district == "Stare Miasto"
