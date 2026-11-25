from __future__ import annotations

import pytest

from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_floor,
    parse_price,
    parse_rooms,
    strip_html,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 200 000 zł", 1_200_000),
        ("1.200.000 zł", 1_200_000),
        ("1,2 mln zł", 1_200_000),
        ("120 tys. zł", 120_000),
        ("12 300 zł/m²", 12_300),
        (854727, 854727),
        ("Zapytaj o cenę", None),
        ("do negocjacji", None),
        (None, None),
    ],
)
def test_parse_price(raw: object, expected: int | None) -> None:
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("69,49 m²", 69.49),
        ("69.49 m2", 69.49),
        ("0,5 ha", 5000.0),
        ("10 ar", 1000.0),
        (69.49, 69.49),
        (None, None),
    ],
)
def test_parse_area(raw: object, expected: float | None) -> None:
    assert parse_area(raw) == expected


def test_parse_rooms() -> None:
    assert parse_rooms("FOUR") == 4
    assert parse_rooms("5+") == 5
    assert parse_rooms(["4"]) == 4
    assert parse_rooms("3 pokoje") == 3
    assert parse_rooms(None) is None


def test_parse_floor() -> None:
    assert parse_floor("ground_floor") == (0, "ground_floor")
    assert parse_floor("parter") == (0, "parter")
    assert parse_floor("3") == (3, "3")
    assert parse_floor("poddasze") == (None, "poddasze")


def test_normalize_text_and_html() -> None:
    assert normalize_text("Małopolskie") == "malopolskie"
    assert normalize_text("Śląskie") == "slaskie"
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html("plain   text") == "plain text"


def test_parse_datetime_ms() -> None:
    assert parse_datetime_ms("2025-07-02T06:17:47Z") == 1751437067000
    assert parse_datetime_ms("not a date") is None
