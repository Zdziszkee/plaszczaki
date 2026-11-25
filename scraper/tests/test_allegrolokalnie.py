from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.allegrolokalnie import AllegrolokalnieAdapter
from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "allegrolokalnie"


def _adapter() -> AllegrolokalnieAdapter:
    return AllegrolokalnieAdapter(
        source_id="allegrolokalnie",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(city: str) -> Query:
    for query in _adapter().queries(("malopolskie", "slaskie")):
        if query.key.endswith(f":{city}"):
            return query
    raise AssertionError(f"no query for {city}")


def _search_page(city: str):
    html = (FIXTURES / "search-krakow.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query(city))


def _detail(name: str, city: str, index: int = 0):
    page = _search_page(city)
    detail_html = (FIXTURES / name).read_text(encoding="utf-8")
    return _adapter().parse_detail(detail_html, page.items[index])


def test_queries_cover_every_configured_city() -> None:
    adapter = _adapter()
    queries = adapter.queries(("malopolskie", "slaskie"))

    expected = {
        f"{region}:{city}"
        for region in ("malopolskie", "slaskie")
        for city in adapter.regions.region(region).cities
    }
    assert len(queries) == len(expected)
    assert {query.key for query in queries} == expected
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}


def test_queries_use_exact_city_paths_and_newest_sort() -> None:
    queries = {query.key: query for query in _adapter().queries(("malopolskie", "slaskie"))}

    assert queries["malopolskie:Kraków"].url == (
        "https://allegrolokalnie.pl/oferty/nieruchomosci/krakow?sort=startingTime-desc"
    )
    assert queries["malopolskie:Nowy Sącz"].url.endswith("/nowy-sacz?sort=startingTime-desc")
    # The platform disambiguates some city names with a suffix.
    assert queries["malopolskie:Oświęcim"].url.endswith("/oswiecim-fw2?sort=startingTime-desc")
    assert queries["malopolskie:Chrzanów"].url.endswith("/chrzanow-3cc?sort=startingTime-desc")


def test_with_page_keeps_sort_parameter() -> None:
    query = _query("Kraków")
    paged = _adapter().with_page(query, 3)

    assert paged.page == 3
    assert "page=3" in paged.url
    assert "sort=startingTime-desc" in paged.url


def test_parse_search_fixture() -> None:
    page = _search_page("Kraków")

    assert page.current_page == 1
    assert page.total_pages == 100
    assert len(page.items) == 2

    flat, house = page.items
    assert flat.external_id == "ab0af54e-f9dd-4bf2-bcb1-6c953e1101b3"
    assert flat.url.startswith("https://allegrolokalnie.pl/oferta/")
    assert flat.title == "Mieszkanie, Kraków, Bieńczyce, 30 m²"
    assert flat.price_pln == 495_000
    assert flat.price_per_m2_pln == 16_282
    assert flat.area_m2 == 30.4
    assert flat.build_year == 1955
    assert flat.voivodeship == "malopolskie"
    assert flat.city == "Kraków"
    assert flat.address_text == "Kraków"
    assert flat.property_type == "flat"
    assert flat.seller_type == "agency"
    assert flat.images == [
        "https://a.allegroimg.com/s180x180/118fa9/3e048caf410bb8a3ee39e85ad229/"
        "Mieszkanie-Krakow-Bienczyce-30-m2",
    ]

    assert house.external_id == "2ead35cb-29ed-4665-a74a-6996e5d8fcab"
    assert house.title == "Dom, Kraków, Podgórze, 112 m²"
    assert house.price_pln == 1_030_000
    assert house.land_area_m2 == 200
    assert house.property_type == "house"
    assert house.city == "Kraków"


def test_parse_search_only_keeps_the_requested_city() -> None:
    page = _search_page("Niepołomice")

    assert len(page.items) == 1
    plot = page.items[0]
    assert plot.city == "Niepołomice"
    assert plot.property_type == "plot"
    assert plot.price_pln == 21_654
    assert plot.land_area_m2 == 7218
    assert plot.area_m2 is None


def test_parse_search_discards_a_page_for_another_city() -> None:
    # A slug collision must not leak another locality's cards into the region.
    page = _search_page("Tarnów")

    assert page.items == []
    assert page.total_pages == 1


def test_parse_detail_reads_the_structured_location_block() -> None:
    detail = _detail("detail-flat-agency.html", "Kraków")

    assert detail.external_id == "ab0af54e-f9dd-4bf2-bcb1-6c953e1101b3"
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.price_pln == 495_000
    assert detail.price_unit == "total"
    assert detail.price_per_m2_pln == 16_282
    assert detail.area_m2 == 30.4
    assert detail.land_area_m2 is None
    assert detail.rooms == 1
    assert detail.floor == 3
    assert detail.build_year == 1955
    assert detail.voivodeship == "malopolskie"
    assert detail.county is None
    assert detail.commune is None
    assert detail.city == "Kraków"
    assert detail.district == "Bieńczyce"
    assert detail.street == "os. Ogrodowe"
    assert detail.address_text == "os. Ogrodowe, Kraków"
    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None
    assert detail.seller_type == "agency"


def test_parse_detail_house_with_county_free_address() -> None:
    detail = _detail("detail-house-agency.html", "Kraków", index=1)

    assert detail.offer_type == "sale"
    assert detail.property_type == "house"
    assert detail.market == "primary"
    assert detail.price_pln == 1_030_000
    assert detail.price_per_m2_pln == 9_162
    assert detail.area_m2 == 112
    assert detail.land_area_m2 == 200
    assert detail.rooms == 5
    assert detail.build_year == 2025
    assert detail.voivodeship == "malopolskie"
    assert detail.county is None
    assert detail.commune is None
    assert detail.city == "Kraków"
    assert detail.district == "Podgórze"
    assert detail.street == "Zofii Nałkowskiej"
    assert detail.address_text == "Zofii Nałkowskiej, Kraków"


def test_parse_detail_plot_rent_uses_monthly_price_and_land_area() -> None:
    detail = _detail("detail-plot-rent-agency.html", "Niepołomice")

    assert detail.offer_type == "rent"
    assert detail.property_type == "plot"
    assert detail.price_pln == 21_654
    assert detail.price_unit == "monthly"
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 7218
    assert detail.voivodeship == "malopolskie"
    assert detail.county == "Wielicki"
    assert detail.commune == "Niepołomice"
    assert detail.city == "Niepołomice"
    assert detail.district is None
    assert detail.street == "Grabska"
    assert detail.address_text == "Grabska, Niepołomice"
    assert detail.lat is None
    assert detail.lng is None


def test_parse_detail_without_structured_block_falls_back_to_city() -> None:
    detail = _detail("detail-no-structured-address.html", "Kraków")

    assert detail.voivodeship == "malopolskie"
    assert detail.county is None
    assert detail.commune is None
    assert detail.city == "Kraków"
    assert detail.district == "Bieńczyce"
    assert detail.street is None
    assert detail.address_text == "Kraków"
    assert detail.lat is None
    assert detail.lng is None
