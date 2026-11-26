"""Canonical offer attributes from explicit source payloads.

Deterministic extraction only handles fields whose location is known for a
source. Unknown or ambiguous attributes are intentionally left missing for the
LLM fallback; there is no generic label/alias registry to keep growing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from plaszczaki_scraper.normalize import (
    normalize_text,
    parse_area,
    parse_datetime_ms,
    parse_price,
    safe_int,
)

if TYPE_CHECKING:
    from plaszczaki_scraper.models import Listing

ATTRIBUTE_COLUMNS = (
    "condition",
    "building_type",
    "building_material",
    "heating",
    "windows_type",
    "ownership",
    "energy_certificate",
    "ceiling_height_m",
    "building_floors",
    "elevator",
    "parking",
    "parking_spaces",
    "balcony",
    "terrace",
    "garden",
    "furnished",
    "pets_allowed",
    "air_conditioning",
    "security",
    "internet",
    "fenced",
    "administrative_rent_pln",
    "estimate_pln",
    "available_from",
)

BOOLEAN_ATTRIBUTE_COLUMNS = frozenset(
    {
        "elevator",
        "balcony",
        "terrace",
        "garden",
        "furnished",
        "pets_allowed",
        "air_conditioning",
        "internet",
        "fenced",
    }
)

_CONDITION_MAP = {
    "to completion": "to_finish",
    "do wykonczenia": "to_finish",
    "ready to use": "move_in_ready",
    "do zamieszczenia": "move_in_ready",
    "do wprowadzenia": "move_in_ready",
    "to renovation": "to_renovate",
    "do remontu": "to_renovate",
    "unfinished close": "shell_closed",
    "surowy zamkniety": "shell_closed",
    "unfinished open": "shell_open",
    "surowy otwarty": "shell_open",
    "do odswiezenia": "to_refresh",
    "deweloperski": "developer",
    "bardzo dobry": "very_good",
    "dobry": "good",
    "wysoki standard": "high_standard",
    "bardzo wysoki standard": "very_high_standard",
    "wykonczone pod klucz": "move_in_ready",
    "move in ready": "move_in_ready",
}

_BUILDING_TYPE_MAP = {
    "block": "block",
    "blok": "block",
    "niski blok": "block",
    "blok mieszkalny": "block",
    "apartment": "apartment_building",
    "apartamentowiec": "apartment_building",
    "dom wielorodzinny": "apartment_building",
    "tenement": "tenement",
    "tenement house": "tenement",
    "kamienica": "tenement",
    "detached": "detached_house",
    "dom wolnostojacy": "detached_house",
    "wolnostojacy": "detached_house",
    "semi detached": "semi_detached_house",
    "blizniak": "semi_detached_house",
    "ribbon": "row_house",
    "szeregowiec": "row_house",
    "office building": "office_building",
    "warehouse": "warehouse",
    "shopping centre": "shopping_centre",
    "inny": "other",
}

_MATERIAL_MAP = {
    "brick": "brick",
    "cegla": "brick",
    "concrete": "concrete",
    "beton": "concrete",
    "breezeblock": "aerated_concrete",
    "pustak": "aerated_concrete",
    "concrete plate": "prefabricated_concrete",
    "wielka plyta": "prefabricated_concrete",
    "reinforced concrete": "reinforced_concrete",
    "silikat": "silicate",
    "cellular concrete": "cellular_concrete",
    "wood": "wood",
    "drewno": "wood",
    "other": "other",
    "inny": "other",
    "zroznicowany": "mixed",
}

_HEATING_MAP = {
    "urban": "district",
    "miejskie": "district",
    "co miejskie": "district",
    "co z sieci miejskiej": "district",
    "siec miejska": "district",
    "z sieci miejskiej": "district",
    "gas": "gas",
    "gazowe": "gas",
    "co gazowe": "gas",
    "electrical": "electric",
    "elektryczne": "electric",
    "boiler room": "own_boiler_room",
    "wlasna kotlownia": "own_boiler_room",
    "co wlasne": "own_boiler_room",
    "co z wlasnej kotlowni": "own_boiler_room",
    "heat pump": "heat_pump",
    "pompa ciepla": "heat_pump",
    "tiled stove": "solid_fuel_stove",
    "piec": "solid_fuel_stove",
    "weglowe": "coal",
    "podlogowe": "other",
    "other": "other",
}

_OWNERSHIP_MAP = {
    "full ownership": "full_ownership",
    "pelna wlasnosc": "full_ownership",
    "wlasnosc": "full_ownership",
    "odrebna wlasnosc z ksiega wieczysta": "full_ownership",
    "limited ownership": "limited_ownership",
    "share": "share",
    "udzialowa": "share",
    "usufruct": "usufruct",
    "spoldzielcze": "cooperative",
}

_SECURITY_MAP = {
    "entryphone": "intercom",
    "domofon": "intercom",
    "wideofon": "intercom",
    "monitoring": "monitoring",
    "kamery": "monitoring",
    "anti burglary door": "security_doors",
    "drzwi antywlamaniowe": "security_doors",
    "closed area": "gated_area",
    "teren ogrodzony": "gated_area",
    "alarm": "alarm",
    "roller shutters": "security_shutters",
    "rolety antywlamaniowe": "security_shutters",
    "kraty w oknach": "window_grilles",
    "ochrona": "security_guard",
    "24 7": "24_7",
    "unspecified": "unspecified",
}

_MISSING_TEXT = {"", "none", "null", "unknown", "brak danych"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _raw_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("normalized", "value"):
            if key in value and value[key] not in (None, "", [], {}):
                return value[key]
        return None
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _first(*values: Any) -> Any:
    return next((value for value in values if _present(value)), None)


def _get(values: dict[str, Any], key: str) -> Any:
    """Read one explicitly known source key, tolerating case changes only."""
    if key in values:
        return _raw_value(values[key])
    folded = key.casefold()
    for candidate, value in values.items():
        if isinstance(candidate, str) and candidate.casefold() == folded:
            return _raw_value(value)
    return None


def _labelled(parameters: Any, label: str) -> Any:
    if not isinstance(parameters, list):
        return None
    for item in parameters:
        if isinstance(item, dict) and item.get("label") == label:
            return _raw_value(item.get("value"))
    return None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [part for item in value if (part := _text(item))]
        return ", ".join(parts) or None
    text = str(value).strip()
    return text if text and normalize_text(text) not in _MISSING_TEXT else None


def _canonical(value: Any, mapping: dict[str, str]) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    return mapping.get(normalized, normalized.replace(" ", "_")) or None


def _condition(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if normalized.startswith("okna "):
        return None
    for needle, canonical in (
        ("do remontu", "to_renovate"),
        ("do wykonczenia", "to_finish"),
        ("do zamieszczenia", "move_in_ready"),
        ("do wprowadzenia", "move_in_ready"),
        ("surowy zamkniety", "shell_closed"),
        ("surowy otwarty", "shell_open"),
        ("do odswiezenia", "to_refresh"),
    ):
        if needle in normalized:
            return canonical
    return _canonical(text, _CONDITION_MAP)


def _heating(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if normalized in {"n", "y", "nie", "brak", "nie ma"}:
        return None
    if normalized.startswith("c o "):
        normalized = f"co {normalized[4:]}"
    return _canonical(normalized, _HEATING_MAP)


def _windows(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if "pcv" in normalized or "plastik" in normalized:
        return "plastic"
    if "drewn" in normalized:
        return "wood"
    if "alumin" in normalized:
        return "aluminium"
    if "czesciowo" in normalized:
        return "partially_new"
    if "nowe" in normalized or "wymienion" in normalized or "wymiana" in normalized:
        return "new"
    return _canonical(text, {})


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, list):
        parsed = [_boolean(item) for item in value]
        return (
            any(item is True for item in parsed)
            if any(item is not None for item in parsed)
            else None
        )
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if normalized in {"yes", "y", "true", "tak", "jest", "1", "available", "in stock"}:
        return True
    if normalized in {"no", "n", "false", "nie", "nie ma", "brak", "0", "none"}:
        return False
    if normalized.startswith("tak ") or normalized.startswith("yes "):
        return True
    if normalized.startswith("nie ") or normalized.startswith("no "):
        return False
    return None


def _internet(value: Any) -> bool | None:
    if isinstance(value, list):
        return any("internet" in normalize_text(str(item)) for item in value)
    if isinstance(value, bool):
        return value
    parsed = _boolean(value)
    if parsed is not None:
        return parsed
    normalized = normalize_text(str(value or ""))
    return True if "internet" in normalized else None


def _furnished(value: Any) -> bool | None:
    if isinstance(value, list):
        return any("furniture" in normalize_text(str(item)) for item in value) or None
    return _boolean(value)


def _money(value: Any) -> int | None:
    amount = parse_price(value)
    return amount if amount is not None and amount > 0 else None


def _positive_float(value: Any) -> float | None:
    amount = parse_area(value)
    return amount if amount is not None and amount > 0 else None


def _market(value: Any) -> Literal["primary", "secondary"] | None:
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    if normalized in {"primary", "pierwotny", "pierwotnego", "rynek pierwotny"}:
        return "primary"
    if normalized in {"secondary", "wtorny", "wtornego", "rynek wtorny"}:
        return "secondary"
    if normalized in {"tak", "yes", "true", "1"}:
        return "primary"
    if normalized in {"nie", "no", "false", "0"}:
        return "secondary"
    return None


def _parking(value: Any) -> str | None:
    boolean = _boolean(value)
    text = _text(value)
    if text is None:
        if boolean is True:
            return "unspecified"
        if boolean is False:
            return "none"
        return None
    normalized = normalize_text(text)
    if normalized in {"identyfikator", "unknown"}:
        return None
    tokens: set[str] = set()
    for needle, token in (
        ("gara", "garage"),
        ("podziem", "underground"),
        ("naziem", "surface"),
        ("na ulicy", "street"),
        ("przynalezne na ulicy", "street"),
        ("strzezon", "guarded"),
        ("parking", "parking"),
    ):
        if needle in normalized:
            tokens.add(token)
    if normalized in {"nie", "nie ma", "brak", "false", "no", "0"} or normalized.startswith("nie "):
        tokens.add("none")
    if boolean is True and not tokens:
        tokens.add("unspecified")
    if boolean is False and not tokens:
        tokens.add("none")
    return "+".join(sorted(tokens)) or text.strip().lower()


def _security(value: Any) -> str | None:
    if isinstance(value, bool):
        return "unspecified" if value else None
    text = _text(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    tokens = {
        token for needle, token in _SECURITY_MAP.items() if normalize_text(needle) in normalized
    }
    return "+".join(sorted(tokens)) or None


def _fenced(value: Any) -> bool | None:
    if isinstance(value, list):
        return True if value else None
    return _boolean(value)


def _otodom(extra: dict[str, Any]) -> dict[str, Any]:
    payload = _dict(extra.get("otodom"))
    characteristics = _dict(payload.get("characteristics"))
    target = _dict(payload.get("target"))
    return {
        "condition": _first(
            _get(characteristics, "construction_status"), _get(target, "Construction_status")
        ),
        "building_type": _first(
            _get(characteristics, "building_type"), _get(target, "Building_type")
        ),
        "building_material": _first(
            _get(characteristics, "building_material"), _get(target, "Building_material")
        ),
        "heating": _first(_get(characteristics, "heating"), _get(target, "Heating")),
        "windows_type": _first(_get(characteristics, "windows_type"), _get(target, "Windows_type")),
        "ownership": _first(
            _get(characteristics, "building_ownership"), _get(target, "Building_ownership")
        ),
        "energy_certificate": _first(
            _get(characteristics, "energy_certificate"), _get(target, "Energy_certificate")
        ),
        "ceiling_height_m": _first(
            _get(characteristics, "ceiling_height_from"), _get(characteristics, "ceiling_height_to")
        ),
        "building_floors": _first(
            _get(characteristics, "building_floors_num"), _get(characteristics, "floors_num")
        ),
        "fenced": _first(
            _get(target, "Fence_types"), _get(characteristics, "fence"), _get(target, "Fence")
        ),
        "furnished": _get(target, "Equipment_types"),
        "internet": _get(target, "Media_types"),
        "security": _first(_get(target, "Security_types"), _get(target, "Security")),
        "administrative_rent_pln": _first(_get(characteristics, "rent"), _get(target, "Rent")),
        "deposit_pln": _first(_get(characteristics, "deposit"), _get(target, "Deposit")),
        "available_from": _first(_get(characteristics, "free_from"), _get(target, "Free_from")),
        "market": _first(_get(characteristics, "market"), _get(target, "MarketType")),
    }


def _olx(extra: dict[str, Any]) -> dict[str, Any]:
    params = _dict(_dict(extra.get("olx")).get("params"))
    return {
        "furnished": _get(params, "furniture"),
        "elevator": _get(params, "winda"),
        "pets_allowed": _get(params, "pets"),
        "administrative_rent_pln": _get(params, "rent"),
        "parking": _get(params, "parking"),
        "building_type": _get(params, "builttype"),
        "market": _get(params, "market"),
    }


def _txo(extra: dict[str, Any]) -> dict[str, Any]:
    payload = _dict(extra.get("txo"))
    characteristics = _dict(payload.get("characteristics"))
    amenities = _dict(payload.get("amenities"))
    fenced = None
    for key, value in characteristics.items():
        if key.casefold().startswith("ogrodzenie"):
            fenced = (
                False
                if "brak" in key.casefold() or key.casefold().endswith(" nie")
                else _boolean(value)
            )
            if fenced is None and _present(value):
                fenced = True
            break
    return {
        "condition": _first(
            _get(characteristics, "Stan nieruchomości"), _get(characteristics, "Stan budynku")
        ),
        "building_type": _first(
            _get(characteristics, "Typ budynku"), _get(characteristics, "Rodzaj domu")
        ),
        "building_material": _get(characteristics, "Materiał budowlany"),
        "heating": _get(characteristics, "Ogrzewanie"),
        "windows_type": _get(characteristics, "Stolarka okienna"),
        "ownership": _get(characteristics, "Forma własności"),
        "elevator": _get(amenities, "Winda"),
        "parking": _first(_get(characteristics, "Parking"), _get(amenities, "Parking")),
        "parking_spaces": _get(characteristics, "Liczba miejsc parkingowych"),
        "balcony": _first(_get(characteristics, "Balkon"), _get(amenities, "Balkon")),
        "terrace": _first(_get(characteristics, "Taras"), _get(amenities, "Taras")),
        "garden": _first(_get(characteristics, "Ogród"), _get(amenities, "Ogród")),
        "air_conditioning": _get(amenities, "Klimatyzacja"),
        "security": _first(
            _get(characteristics, "Security system"), _get(characteristics, "Security features")
        ),
        "internet": _get(characteristics, "Media"),
        "fenced": fenced,
        "administrative_rent_pln": _first(
            _get(characteristics, "Czynsz (dodatkowo)"),
            _get(characteristics, "Czynsz administracyjny"),
        ),
        "market": _get(characteristics, "Rynek pierwotny"),
    }


def _nieruchomosci_online(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("nieruchomosci-online")).get("additional_property"))
    return {
        "building_type": _get(values, "Building type"),
        "parking": _get(values, "Parking"),
        "terrace": _get(values, "Terrace"),
        "security": _first(_get(values, "Security system"), _get(values, "Security features")),
        "administrative_rent_pln": _get(values, "Rent per month"),
        "fenced": _get(values, "Fenced"),
    }


def _adresowo(extra: dict[str, Any]) -> dict[str, Any]:
    payload = _dict(extra.get("adresowo"))
    parameters = payload.get("parameters")
    return {
        "condition": _first(
            _labelled(parameters, "Stan nieruchomości"),
            _labelled(parameters, "Stan budynku"),
            _get(payload, "condition"),
        ),
        "heating": _labelled(parameters, "Ogrzewanie"),
        "windows_type": _labelled(parameters, "Stolarka okienna"),
        "ownership": _first(
            _labelled(parameters, "Forma własności"),
            _labelled(parameters, "Typ własności"),
            _get(payload, "ownership"),
        ),
        "fenced": _labelled(parameters, "Ogrodzenie działki"),
        "market": _labelled(parameters, "Oferta z rynku"),
    }


def _domiporta(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("domiporta")).get("additional_property"))
    return {
        "building_type": _get(values, "Typ budynku"),
        "building_material": _get(values, "Materiał"),
        "ownership": _get(values, "Forma własności"),
        "internet": _get(values, "Media"),
        "fenced": _get(values, "Ogrodzenie działki"),
    }


def _oferty_net(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("oferty_net")).get("params"))
    return {
        "condition": _first(_get(values, "Stan nieruchomości"), _get(values, "Stan budynku")),
        "building_type": _get(values, "Typ budynku"),
        "building_material": _get(values, "Materiał budowlany"),
        "heating": _get(values, "Ogrzewanie"),
        "ownership": _get(values, "Forma własności"),
        "elevator": _get(values, "Winda"),
        "parking": _first(_get(values, "Miejsca parkingowe"), _get(values, "Parking")),
        "parking_spaces": _get(values, "Liczba miejsc parkingowych"),
        "balcony": _get(values, "Balkon"),
        "terrace": _get(values, "Taras"),
        "garden": _get(values, "Ogród"),
        "furnished": _get(values, "Meble"),
        "air_conditioning": _get(values, "Klimatyzacja"),
        "internet": _get(values, "Internet"),
        "fenced": _get(values, "Ogrodzenie"),
        "market": _get(values, "Rynek pierwotny"),
    }


def _allegrolokalnie(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("allegrolokalnie")).get("params"))
    additional = normalize_text(str(_get(values, "informacje dodatkowe") or ""))
    return {
        "building_type": _first(_get(values, "typ budynku"), _get(values, "rodzaj zabudowy")),
        "building_material": _get(values, "material budowlany"),
        "heating": _get(values, "ogrzewanie"),
        "ownership": _get(values, "forma wlasnosci"),
        "elevator": True if "winda" in additional else _get(values, "winda"),
        "parking": _get(values, "miejsce parkingowe"),
        "internet": _get(values, "media"),
        "fenced": _get(values, "ogrodzenie"),
        "condition": _get(values, "stan budynku"),
        "market": _get(values, "rynek"),
    }


def _gratka_morizon(extra: dict[str, Any], source_id: str) -> dict[str, Any]:
    values = _dict(_dict(extra.get(source_id)).get("labels"))
    return {
        "condition": _first(_get(values, "Stan nieruchomości"), _get(values, "Stan budynku")),
        "building_type": _get(values, "Typ budynku"),
        "building_material": _get(values, "Materiał budowlany"),
        "heating": _get(values, "Ogrzewanie"),
        "windows_type": _get(values, "Stolarka okienna"),
        "ownership": _get(values, "Forma własności"),
        "balcony": _get(values, "Balkon"),
        "terrace": _get(values, "Taras"),
        "garden": _get(values, "Ogród"),
        "market": _first(_get(values, "Rynek"), _get(_dict(extra.get(source_id)), "market_type")),
    }


def _luksusowemieszkania(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("luksusowemieszkania")).get("properties"))
    return {
        "condition": _get(values, "Standard wykończenia"),
        "building_type": _get(values, "Typ budynku"),
        "building_material": _get(values, "Materiał"),
        "heating": _get(values, "Ogrzewanie"),
        "ownership": _get(values, "Typ własności"),
        "elevator": _get(values, "Winda"),
        "internet": _get(values, "Media"),
    }


def _nieruchomosciowi(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("nieruchomosciowi")).get("details"))
    return {
        "condition": _get(values, "condition"),
        "heating": _get(values, "heating"),
        "ownership": _get(values, "ownership_form"),
        "furnished": _get(values, "furnished"),
        "administrative_rent_pln": _get(values, "rent_admin"),
        "deposit_pln": _get(values, "deposit"),
        "market": _get(values, "market"),
    }


def _axiimmo(extra: dict[str, Any]) -> dict[str, Any]:
    values = _dict(_dict(extra.get("axiimmo")).get("properties"))
    security = _get(values, "24/7 security")
    return {
        "building_type": _get(values, "Building type"),
        "parking": _get(values, "Parking ratio"),
        "parking_spaces": _first(
            _get(values, "Ground-level parking spaces"), _get(values, "Underground parking spaces")
        ),
        "security": "24_7" if _boolean(security) is True else security,
    }


def _direct(extra: dict[str, Any], source_id: str) -> dict[str, Any]:
    payload = extra.get(source_id)
    values = payload if isinstance(payload, dict) else {}
    return {field: _get(values, field) for field in (*ATTRIBUTE_COLUMNS, "market", "deposit_pln")}


def _structured_values(listing: Listing) -> dict[str, Any]:
    extra = listing.extra
    source_id = listing.source_id
    if source_id == "otodom":
        return _otodom(extra)
    if source_id == "olx":
        return _olx(extra)
    if source_id == "txo":
        return _txo(extra)
    if source_id == "nieruchomosci-online":
        return _nieruchomosci_online(extra)
    if source_id == "adresowo":
        return _adresowo(extra)
    if source_id == "domiporta":
        return _domiporta(extra)
    if source_id == "oferty-net":
        return _oferty_net(extra)
    if source_id == "allegrolokalnie":
        return _allegrolokalnie(extra)
    if source_id in {"gratka", "morizon"}:
        return _gratka_morizon(extra, source_id)
    if source_id == "luksusowemieszkania":
        return _luksusowemieszkania(extra)
    if source_id == "nieruchomosciowi":
        return _nieruchomosciowi(extra)
    if source_id == "axiimmo":
        return _axiimmo(extra)
    if source_id == "vilea":
        amenities = _dict(_dict(extra.get("vilea")).get("amenities"))
        return {"parking": _get(amenities, "Garaż/ parking")}
    if source_id == "magazyny-online":
        return {
            "administrative_rent_pln": _get(
                _dict(_dict(extra.get("magazyny_online")).get("facts")), "Czynsz"
            )
        }
    if source_id == "rentola":
        return {"available_from": _get(_dict(extra.get("rentola")), "valid_from")}
    if source_id == "komornik":
        return {"estimate_pln": _get(_dict(extra.get("komornik")), "estimate")}
    if source_id == "mapalicytacji":
        return {"estimate_pln": _get(_dict(extra.get("mapalicytacji")), "estimated_price_pln")}
    if source_id == "poczta-polska":
        return _direct(extra, "poczta")
    if source_id in {"homeportal", "kupbezposrednio", "nportal"}:
        return _direct(extra, source_id)
    return {}


def _district(extra: dict[str, Any]) -> str | None:
    for payload in extra.values():
        if not isinstance(payload, dict):
            continue
        direct = _text(payload.get("district"))
        if direct is not None:
            return direct
        levels = _dict(payload.get("location_levels"))
        for key in ("district", "neighbourhood", "neighborhood"):
            value = _text(levels.get(key))
            if value is not None:
                return value
    return None


def normalize_attributes(listing: Listing, *, overwrite_attributes: bool = False) -> None:
    """Fill canonical columns from explicitly known structured source fields."""
    values = _structured_values(listing)

    def assign(field: str, value: Any) -> None:
        if value is not None and (overwrite_attributes or getattr(listing, field) is None):
            setattr(listing, field, value)

    assign("condition", _condition(values.get("condition")))
    assign("building_type", _canonical(values.get("building_type"), _BUILDING_TYPE_MAP))
    assign("building_material", _canonical(values.get("building_material"), _MATERIAL_MAP))
    assign("heating", _heating(values.get("heating")))
    assign("windows_type", _windows(values.get("windows_type")))
    assign("ownership", _canonical(values.get("ownership"), _OWNERSHIP_MAP))

    certificate = values.get("energy_certificate")
    if certificate is not None:
        normalized = normalize_text(str(certificate))
        certificate = {"aplus": "a+", "bminus": "b-"}.get(normalized, normalized)
        assign("energy_certificate", certificate or None)

    height = _positive_float(values.get("ceiling_height_m"))
    if height is not None:
        assign("ceiling_height_m", round(height / 100 if height > 20 else height, 2))
    floors = safe_int(values.get("building_floors"))
    assign("building_floors", floors if floors is not None and floors > 0 else None)

    assign("elevator", _boolean(values.get("elevator")))
    assign("parking", _parking(values.get("parking")))
    spaces = safe_int(values.get("parking_spaces"))
    assign("parking_spaces", spaces if spaces is not None and spaces >= 0 else None)
    assign("balcony", _boolean(values.get("balcony")))
    assign("terrace", _boolean(values.get("terrace")))
    assign("garden", _boolean(values.get("garden")))
    assign("furnished", _furnished(values.get("furnished")))
    assign("pets_allowed", _boolean(values.get("pets_allowed")))
    assign("air_conditioning", _boolean(values.get("air_conditioning")))
    assign("security", _security(values.get("security")))
    assign("internet", _internet(values.get("internet")))
    assign("fenced", _fenced(values.get("fenced")))

    assign("administrative_rent_pln", _money(values.get("administrative_rent_pln")))
    assign("deposit_pln", _money(values.get("deposit_pln")))
    assign("estimate_pln", _money(values.get("estimate_pln")))
    assign("available_from", parse_datetime_ms(values.get("available_from")))

    if listing.market == "unknown":
        market = _market(values.get("market"))
        if market is not None:
            listing.market = market

    assign("district", _district(listing.extra))

    if listing.offer_type == "rent" and listing.rent_pln is None and listing.price_pln is not None:
        listing.rent_pln = listing.price_pln

    amount = listing.price_pln
    if amount is None and listing.offer_type == "rent":
        amount = listing.rent_pln
    area = listing.area_m2
    if area is None and listing.property_type == "plot":
        area = listing.land_area_m2
    if listing.price_per_m2_pln is None and amount is not None and area is not None and area > 0:
        listing.price_per_m2_pln = round(amount / area)
