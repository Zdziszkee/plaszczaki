from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.morizon import MorizonAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

EMPTY_RESULTS_PAGE = (
    '<html><body><script type="application/json" id="__NUXT_DATA__">'
    '[{"data":1},{"k":2},{"data":3},{"searchResult":4},{"properties":5},'
    '{"nodes":6,"totalCount":7},[],0]'
    "</script></body></html>"
)


FIXTURES = Path(__file__).parent / "fixtures" / "morizon"


def _adapter() -> MorizonAdapter:
    return MorizonAdapter(
        source_id="morizon",
        regions=RegionRegistry(load_regions_config()),
    )


def _search_page(name: str = "search.html"):
    query = _adapter().queries(("malopolskie",))[0]
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return _adapter().parse_search(html, query)


def test_queries_cover_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 22
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert len({query.key for query in queries}) == 22

    by_url = {query.url: query for query in queries}
    assert "https://www.morizon.pl/mieszkania/malopolskie/" in by_url
    assert "https://www.morizon.pl/do-wynajecia/mieszkania/malopolskie/" in by_url
    assert "https://www.morizon.pl/domy/slaskie/" in by_url
    assert "https://www.morizon.pl/do-wynajecia/domy/slaskie/" in by_url
    assert "https://www.morizon.pl/dzialki/malopolskie/" in by_url
    assert "https://www.morizon.pl/komercyjne/malopolskie/" in by_url
    assert "https://www.morizon.pl/garaze/slaskie/" in by_url
    assert "https://www.morizon.pl/do-wynajecia/pokoje/slaskie/" in by_url

    flat_sale = by_url["https://www.morizon.pl/mieszkania/malopolskie/"]
    assert (flat_sale.offer_type, flat_sale.property_type) == ("sale", "flat")
    flat_rent = by_url["https://www.morizon.pl/do-wynajecia/mieszkania/malopolskie/"]
    assert (flat_rent.offer_type, flat_rent.property_type) == ("rent", "flat")
    commercial = by_url["https://www.morizon.pl/komercyjne/malopolskie/"]
    assert (commercial.offer_type, commercial.property_type) == ("sale", "commercial")

    # Morizon lists rooms for rent only; /pokoje/ returns no sale offers.
    assert not any(
        query.property_type == "room" and query.offer_type == "sale" for query in queries
    )


def test_with_page_keeps_page_one_bare_and_respects_robots_cap() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    first = _adapter().with_page(query, 1)
    assert first.url == "https://www.morizon.pl/mieszkania/malopolskie/"
    assert first.page == 1

    second = _adapter().with_page(query, 2)
    assert second.url == "https://www.morizon.pl/mieszkania/malopolskie/?page=2"
    assert second.page == 2

    capped = _adapter().with_page(query, 99)
    assert capped.url.endswith("?page=10")
    assert capped.page == 10


def test_parse_search_grouped_page() -> None:
    page = _search_page("search.html")

    assert page.current_page == 1
    assert page.total_items == 16610
    assert page.total_pages == 10
    assert [item.external_id for item in page.items] == [
        "mzn2048409230",  # promoted offer shown above the results
        "mzn2048328589",
        "mzn2048434471",
        "mzn2048445876",
    ]

    promoted = page.items[0]
    assert promoted.price_pln == 770000
    assert promoted.city == "Kraków"
    assert promoted.district == "Dębniki"
    assert promoted.street == "Czerwone Maki"
    assert promoted.floor == 0
    assert promoted.floor_label == "parter/5"

    flat = page.items[1]
    assert flat.url == (
        "https://www.morizon.pl/oferta/sprzedaz-mieszkanie-wielicki-niepolomice-63m2-mzn2048328589"
    )
    assert flat.voivodeship == "malopolskie"
    assert flat.county == "wielicki"
    assert flat.city == "Niepołomice"
    assert flat.street is None
    assert flat.price_pln == 750000
    assert flat.price_per_m2_pln == 11905
    assert flat.area_m2 == 63.0
    assert flat.rooms == 3
    assert flat.price_unit == "total"
    assert flat.offer_type == "sale"
    assert flat.property_type == "flat"
    assert flat.seller_type == "agency"
    assert flat.seller_name == "ap7 group"
    assert flat.images[0].startswith("https://img1.staticmorizon.com.pl/thumb/")
    assert flat.images[0].endswith("/mieszkanie-na-sprzedaz-63-m.jpg")

    developer = page.items[3]
    assert developer.seller_type == "developer"
    assert developer.city == "Kraków"
    assert developer.district == "Nowa Huta"


def test_parse_search_flat_page_shape() -> None:
    page = _search_page("search-flat.html")

    assert page.total_items == 16610
    assert page.total_pages == 10
    assert [item.external_id for item in page.items] == [
        "mzn2047907883",
        "mzn2048445865",
        "mzn2048445863",
    ]
    assert page.items[1].price_pln is not None
    assert page.items[1].price_unit == "total"


def test_parse_search_handles_empty_results() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    page = _adapter().parse_search(EMPTY_RESULTS_PAGE, query)

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_parse_search_rejects_pages_without_payload() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>no payload</body></html>", query)


def test_parse_detail_fixture() -> None:
    page = _search_page()
    summary = next(item for item in page.items if item.external_id == "mzn2048328589")
    detail_html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "mzn2048328589"
    assert detail.url == (
        "https://www.morizon.pl/oferta/sprzedaz-mieszkanie-wielicki-niepolomice-63m2-mzn2048328589"
    )
    assert detail.title == "Komfortowe mieszkanie z ogródkiem"

    assert detail.price_pln == 750000
    assert detail.price_kind == "asking"
    assert detail.price_unit == "total"
    assert detail.price_per_m2_pln == 11905
    assert detail.area_m2 == 63.0
    assert detail.rooms == 3
    assert detail.floor == 0
    assert detail.floor_label == "parter/2"
    assert detail.build_year == 2017
    assert detail.market == "secondary"

    assert detail.voivodeship == "malopolskie"
    assert detail.county == "wielicki"
    assert detail.commune == "Niepołomice"
    assert detail.city == "Niepołomice"
    assert detail.lat == 50.025353
    assert detail.lng == 20.207287
    assert detail.geo_source == "source"

    assert detail.seller_type == "agency"
    assert detail.seller_name == "ap7 group"
    assert len(detail.images) == 3

    description = detail.description or ""
    assert description.startswith("Na sprzedaż nowoczesne, w pełni umeblowane mieszkanie")
    assert "63 m² położone na parterze" in description

    assert detail.published_at == 1793664000000
    assert detail.modified_at == 1794182400000
    assert detail.extra["morizon"]["reference"] == "AP7-MS-14628"
    assert detail.extra["morizon"]["labels"]["Rok budowy"] == "2017"


def test_parse_detail_rent_fixture() -> None:
    summary = Listing(
        source_id="morizon",
        external_id="mzn2048445416",
        url="https://www.morizon.pl/oferta/wynajem-mieszkanie-krakow-pradnik-bialy-pradnicka-24m2-mzn2048445416",
    )
    detail_html = (FIXTURES / "detail-rent.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "mzn2048445416"
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_unit == "monthly"
    assert detail.price_pln == 1600
    assert detail.rent_pln == 372
    assert detail.deposit_pln == 2000
    assert detail.area_m2 == 24.0
    assert detail.rooms == 1
    assert detail.floor == 7
    assert detail.city == "Kraków"
    assert detail.lat == 50.093935
    assert detail.lng == 19.9288559
    assert detail.geo_source == "source"
    assert detail.description


def test_newest_inserts_the_najnowsze_path_segment() -> None:
    adapter = _adapter()
    queries = adapter.queries(("malopolskie",))
    sale = next(query for query in queries if "do-wynajecia" not in query.url)
    rent = next(query for query in queries if "do-wynajecia" in query.url)

    assert adapter.newest(sale).url == ("https://www.morizon.pl/mieszkania/najnowsze/malopolskie/")
    assert adapter.newest(rent).url == (
        "https://www.morizon.pl/do-wynajecia/mieszkania/najnowsze/malopolskie/"
    )
