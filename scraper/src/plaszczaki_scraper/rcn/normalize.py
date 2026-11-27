"""Normalize flattened GUGiK RCN export rows into stable database records."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache
from typing import Any

from pyproj import Transformer

OBJECT_KIND_BY_LAYER = {
    "dzialki": "plot",
    "budynki": "building",
    "lokale": "premises",
}

MARKET_BY_CODE = {
    "rynekpierwotny": "primary",
    "rynekwtorny": "secondary",
}

RESIDENTIAL_BUILDINGS = {
    "mieszkalny",
    "budynekmieszkalnyjednorodzinny",
    "budynekmieszkalnyjednorodzinnyzlozkasamodzielna",
    "budynekmieszkalnyjednorodzinnyzlozkawspolnotowa",
}


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if math.isfinite(result) else None


def _price(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return int(number.to_integral_value())


def _integer(value: Any) -> int | None:
    number = _number(value)
    return round(number) if number is not None else None


def _timestamp_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def _document_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _share_fraction(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            result = float(Fraction(int(numerator), int(denominator)))
        else:
            result = float(Decimal(text.replace(",", ".")))
    except ValueError, ZeroDivisionError, InvalidOperation:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _address(value: Any) -> tuple[str | None, str | None, str | None]:
    raw = _text(value)
    if not raw:
        return None, None, None
    city: str | None = None
    street: str | None = None
    number: str | None = None
    for part in raw.split(";"):
        key, separator, item = part.partition(":")
        if not separator:
            continue
        item = item.strip()
        if key.strip().upper() == "MSC":
            city = item or None
        elif key.strip().upper() == "UL":
            street = item or None
        elif key.strip().upper() == "NR_PORZ":
            number = item or None
    if street and number:
        street = f"{street} {number}"
    return raw, city, street


def _hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cell(value: float | None, size: float) -> float | None:
    return math.floor(value / size + 1e-9) * size if value is not None else None


@lru_cache(maxsize=8)
def _to_wgs84(source_crs: str) -> Transformer:
    return Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)


def _coordinates(row: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = _number(_pick(row, "lat", "LAT", "latitude"))
    lng = _number(_pick(row, "lng", "LNG", "longitude"))
    if lat is None or lng is None:
        source_x = _number(_pick(row, "source_x", "SOURCE_X"))
        source_y = _number(_pick(row, "source_y", "SOURCE_Y"))
        if source_x is not None and source_y is not None:
            source_crs = _text(_pick(row, "source_crs", "SOURCE_CRS")) or "EPSG:2180"
            try:
                lng, lat = _to_wgs84(source_crs).transform(source_x, source_y)
            except Exception:
                lat = lng = None
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lng is not None and not -180 <= lng <= 180:
        lng = None
    if lat == 0 or lng == 0:
        lat = lng = None
    # RCN covers Poland. Reject projections that land outside a generous national
    # bounds instead of allowing a CRS mistake to poison every spatial query.
    if lat is not None and lng is not None and not (49 <= lat <= 55 and 13 <= lng <= 25):
        lat = lng = None
    return lat, lng


def normalize_row(
    row: dict[str, Any],
    *,
    region_id: str,
    region_teryt: str,
    source_layer: str,
    source_url: str,
    grid_degrees: float,
    now_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return a transaction/object pair, or ``None`` for an unusable source row."""
    county_teryt = _text(_pick(row, "TERYT")) or region_teryt
    namespace = _text(_pick(row, "TRAN_PRZESTRZEN_NAZW"))
    local_id = _text(_pick(row, "TRAN_LOKALNY_ID_IIP"))
    designation = _text(_pick(row, "TRAN_OZNACZENIE_TRANS"))
    document_ref = _text(_pick(row, "DOK_OZNACZENIE"))
    if not namespace or not local_id:
        identity = "|".join(
            value or ""
            for value in (
                county_teryt,
                namespace,
                local_id,
                designation,
                document_ref,
                _text(_pick(row, "DOK_DATA")) or "",
            )
        )
        local_id = f"fallback:{_hash({'identity': identity})}"
    namespace = namespace or "unknown"
    transaction_key = f"{county_teryt}|{namespace}|{local_id}"

    transaction_type = _text(_pick(row, "TRAN_RODZAJ_TRANS"))
    market_code = _text(_pick(row, "TRAN_RODZAJ_RYNKU")) or ""
    market = MARKET_BY_CODE.get(market_code.replace("_", "").lower())
    source_service = _text(_pick(row, "SERWIS_RCN")) or source_url
    lat, lng = _coordinates(row)

    transaction = {
        "transaction_key": transaction_key,
        "county_teryt": county_teryt,
        "region_id": region_id,
        "iip_namespace": namespace,
        "local_id": local_id,
        "version_at": _timestamp_ms(_pick(row, "TRAN_WERSJA_ID")),
        "designation": designation,
        "transaction_type": transaction_type,
        "market": market,
        "seller_type": _text(_pick(row, "TRAN_SPRZEDAJACY")),
        "buyer_type": _text(_pick(row, "TRAN_KUPUJACY")),
        "gross_price_pln": _price(_pick(row, "TRAN_CENA_BRUTTO")),
        "vat_pln": _price(_pick(row, "TRAN_VAT")),
        "document_ref": document_ref,
        "document_date": _document_date(_pick(row, "DOK_DATA")),
        "document_creator": _text(_pick(row, "DOK_TWORCA")),
        "property_kind": _text(_pick(row, "NIER_RODZAJ")),
        "right_kind": _text(_pick(row, "NIER_PRAWO")),
        "share_raw": _text(_pick(row, "NIER_UDZIAL")),
        "share_fraction": _share_fraction(_pick(row, "NIER_UDZIAL")),
        "land_area_m2": _number(_pick(row, "NIER_POW_GRUNTU")),
        "property_price_pln": _price(_pick(row, "NIER_CENA_BRUTTO")),
        "property_vat_pln": _price(_pick(row, "NIER_VAT")),
        "description": _text(_pick(row, "NIER_OPIS")),
        "source_url": source_service,
    }
    transaction["record_hash"] = _hash(transaction)

    address_text, city, street = _address(
        _pick(row, "LOK_ADRES", "BUD_ADRES", "DZI_ADRES", "ADRES")
    )
    function_raw = _text(_pick(row, "LOK_FUNKCJA"))
    building_type_raw = _text(_pick(row, "BUD_RODZAJ"))
    object_kind = OBJECT_KIND_BY_LAYER[source_layer]
    if source_layer == "lokale":
        function_code = (function_raw or "").replace("_", "").lower()
        object_kind = "flat" if function_code == "mieszkalna" else "premises"

    object_id = _text(_pick(row, "DZI_ID_DZIALKI", "BUD_ID_BUDYNKU", "LOK_ID_LOKALU", "OBJECT_ID"))
    source_gid = _integer(_pick(row, "gid", "GID"))
    object_number = _text(_pick(row, "DZI_NR_DZIALKI", "BUD_NR_BUDYNKU", "LOK_NR_LOKALU"))
    if not object_id:
        object_id = "hash:" + _hash(
            {
                "transaction": transaction_key,
                "layer": source_layer,
                "address": address_text,
                "number": object_number,
                "function": function_raw,
                "building_type": building_type_raw,
                "usable_area": _number(_pick(row, "LOK_POW_UZYT", "BUD_POW_UZYT")),
                "plot_area": _number(_pick(row, "DZI_POW_EWID")),
                "rooms": _integer(_pick(row, "LOK_LICZBA_IZB")),
                "floor": _integer(_pick(row, "LOK_NR_KOND")),
                "lat": lat,
                "lng": lng,
            }
        )
    object_key = f"{transaction_key}|{source_layer}|{object_id}"

    obj = {
        "object_key": object_key,
        "region_id": region_id,
        "object_kind": object_kind,
        "object_id": object_id,
        "object_number": object_number,
        "address_text": address_text,
        "city": city,
        "street": street,
        "function_raw": function_raw,
        "rooms": _integer(_pick(row, "LOK_LICZBA_IZB")),
        "floor": _integer(_pick(row, "LOK_NR_KOND")),
        "usable_area_m2": _number(_pick(row, "LOK_POW_UZYT", "BUD_POW_UZYT")),
        "ancillary_area_m2": _number(_pick(row, "LOK_POW_PRZYN")),
        "plot_area_m2": _number(_pick(row, "DZI_POW_EWID")),
        "mpzp_purpose": _text(_pick(row, "DZI_PRZEZN_WMPZP")),
        "land_use": _text(_pick(row, "DZI_SPOSOB_UZYT")),
        "building_type_raw": building_type_raw,
        "object_price_pln": _price(
            _pick(row, "DZI_CENA_BRUTTO", "BUD_CENA_BRUTTO", "LOK_CENA_BRUTTO")
        ),
        "object_vat_pln": _price(_pick(row, "DZI_VAT", "BUD_VAT", "LOK_VAT")),
        "info": _text(_pick(row, "DZI_INFO", "BUD_INFO", "LOK_INFO")),
        "lat": lat,
        "lng": lng,
        "geo_cell_lat": _cell(lat, grid_degrees),
        "geo_cell_lng": _cell(lng, grid_degrees),
        "source_layer": source_layer,
        "source_gid": source_gid,
    }
    obj["record_hash"] = _hash(obj)
    return transaction, obj


def is_residential_building(value: str | None) -> bool:
    return (value or "").replace("_", "").lower() in RESIDENTIAL_BUILDINGS
