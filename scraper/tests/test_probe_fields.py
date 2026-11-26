from __future__ import annotations

from plaszczaki_scraper.adapters.base import Query, SearchPage
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.probe import _available_field_keys, _inspect_fields


class _Adapter:
    def parse_search(self, html: str, query: Query) -> SearchPage:
        return SearchPage(
            items=[
                Listing(
                    source_id="example",
                    external_id="1",
                    url="https://example.test/1",
                    offer_type="sale",
                    property_type="flat",
                    price_pln=500_000,
                )
            ],
            current_page=1,
        )


def test_probe_reports_raw_keys_and_parsed_canonical_fields() -> None:
    html = """
    <script type="application/json">
      {"Price": 500000, "Heating": "urban", "params": [{"name": "Liczba pokoi", "value": "2"}]}
    </script>
    <div>Ogrzewanie</div>
    """
    query = Query(
        key="malopolskie:sale:flat",
        url="https://example.test/search",
        region="malopolskie",
        offer_type="sale",
        property_type="flat",
    )

    report = _inspect_fields(html, _Adapter(), query)

    assert report["listings_found"] == 1
    assert report["parsed_fields"]["price_pln"] == 1
    assert {"Price", "Heating", "params", "label:Liczba pokoi", "text:Ogrzewanie"}.issubset(
        report["available_field_keys"]
    )
    assert "unparsed_fields" not in report


def test_raw_key_inventory_does_not_map_polish_prose_to_canonical_fields() -> None:
    keys = _available_field_keys("<p>Mieszkanie z balkonem i ogrodem</p>")

    assert keys == {"text:Mieszkanie z balkonem i ogrodem"}
