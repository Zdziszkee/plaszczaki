from __future__ import annotations

import json

from plaszczaki_scraper.adapters.elicytacje_mf import ElicytacjeMfAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry


def _adapter() -> ElicytacjeMfAdapter:
    return ElicytacjeMfAdapter(
        source_id="elicytacje-mf",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_use_a_json_post() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 1
    query = queries[0]
    assert query.method == "POST"
    assert query.offer_type == "auction"
    assert "announcement/external" in query.url
    assert query.body is not None
    assert "announcementTypeGroupCode" in query.body


def test_with_page_is_zero_based_for_the_api() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)

    assert "page=2" in paged.url
    assert paged.page == 3


def test_parse_search_keeps_in_scope_real_estate_only() -> None:
    query = _adapter().queries(("malopolskie", "slaskie"))[0]
    payload = {
        "content": [
            {
                "uuid": "flat-1",
                "title": "Lokal mieszkalny Kraków",
                "startingPrice": 917250.0,
                "announcementTypeCodes": ["LICELENIER"],
                "localization": "Kraków",
                "localizationVoivodeshipCode": "12",
                "publicationDateTime": "2026-11-25T13:44:00.46Z",
                "saleBeginDateTime": "2026-12-10T09:00:00Z",
            },
            {
                "uuid": "movable-1",
                "title": "Alkoholomierz",
                "startingPrice": 11.25,
                "announcementTypeCodes": ["LICELERUCH"],
                "localization": "Bełchatów",
                "localizationVoivodeshipCode": "10",
            },
            {
                "uuid": "other-region",
                "title": "Dom",
                "announcementTypeCodes": ["LICELENIER"],
                "localization": "Warszawa",
                "localizationVoivodeshipCode": "14",
            },
        ],
        "totalPages": 1,
        "totalElements": 3,
    }

    page = _adapter().parse_search(json.dumps(payload), query)

    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "flat-1"
    assert item.voivodeship == "malopolskie"
    assert item.price_pln == 917250
    assert item.price_kind == "starting"
    assert item.offer_type == "auction"
    assert item.property_type == "flat"
    assert item.url.endswith("/announcements/flat-1")


def test_parse_detail_reads_the_labeled_values() -> None:
    query = _adapter().queries(("slaskie",))[0]
    payload = {
        "content": [
            {
                "uuid": "flat-1",
                "title": "Lokal mieszkalny",
                "announcementTypeCodes": ["LICELENIER"],
                "localization": "Zabrze",
                "localizationVoivodeshipCode": "24",
            },
        ],
        "totalPages": 1,
        "totalElements": 1,
    }
    summary = _adapter().parse_search(json.dumps(payload), query).items[0]

    def labeled(label: str, value: str) -> str:
        return (
            '<div class="line-height-1">'
            f'<span class="p3-regular text--gray-n-600">{label}</span>'
            f'<span class="p1-medium text--gray-n-1000 value">{value}</span>'
            "</div>"
        )

    html = (
        "<html><body><h1>Lokal mieszkalny</h1>"
        + labeled("Województwo", "śląskie")
        + labeled("Miejscowość", "Zabrze")
        + labeled("Ulica", "Tatarkiewicza")
        + labeled("Numer domu", "13A")
        + labeled("Numer lokalu", "14")
        + labeled("Powierzchnia użytkowa (m²)", "30,99")
        + labeled("Liczba pokoi", "1")
        + labeled("Data rozpoczęcia", "10.12.2026, 09:00")
        + "</body></html>"
    )

    detail = _adapter().parse_detail(html, summary)

    assert detail.voivodeship == "slaskie"
    assert detail.city == "Zabrze"
    assert detail.street == "Tatarkiewicza 13A"
    assert detail.area_m2 == 30.99
    assert detail.rooms == 1
    assert detail.address_text == "Tatarkiewicza 13A/14, Zabrze"
    assert detail.auction_date is not None
