"""Normalisation helpers for Polish listing data."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from lxml import html as lxml_html

_NUMBER_RE = re.compile(r"\d[\d\s\u00a0.,]*")
_ROOM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "jeden": 1,
    "dwa": 2,
    "trzy": 3,
    "cztery": 4,
    "piec": 5,
    "szesc": 6,
    "siedem": 7,
    "osiem": 8,
    "dziewiec": 9,
    "dziesiec": 10,
}
_FLOOR_WORDS = {
    "parter": 0,
    "ground floor": 0,
    "ground_floor": 0,
    "ground": 0,
    "suterena": -1,
    "basement": -1,
    "piwnica": -1,
}

_TRANSLITERATION = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "A",
        "Ć": "C",
        "Ę": "E",
        "Ł": "L",
        "Ń": "N",
        "Ó": "O",
        "Ś": "S",
        "Ź": "Z",
        "Ż": "Z",
    }
)


def normalize_text(value: str) -> str:
    """Lowercase, strip Polish diacritics and punctuation for matching."""
    translated = value.translate(_TRANSLITERATION)
    decomposed = unicodedata.normalize("NFKD", translated)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def strip_html(value: str | None) -> str | None:
    """Return plain text from a fragment of HTML."""
    if not value:
        return None
    if "<" not in value and "&" not in value:
        return " ".join(value.split())

    try:
        tree = lxml_html.fromstring(value)
        text = tree.text_content()
    except ValueError, TypeError:
        text = re.sub(r"<[^>]+>", " ", value)

    cleaned = " ".join(text.split())
    return cleaned or None


def _clean_number(raw: str) -> tuple[str, str]:
    """Split a Polish number into digits and its multiplier."""
    lowered = raw.lower().replace("\u00a0", " ")
    multiplier = ""
    for token, factor in (("milion", "mln"), ("mln", "mln"), ("tys", "tys")):
        if token in lowered:
            multiplier = factor
            break

    number = _NUMBER_RE.search(lowered)
    if number is None:
        return "", multiplier
    return number.group(0).strip(), multiplier


def parse_price(value: Any) -> int | None:
    """Parse a Polish price string into whole PLN."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None

    raw, multiplier = _clean_number(str(value))
    if not raw:
        return None

    if multiplier == "mln":
        normalized = raw.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            amount = float(normalized) * 1_000_000
        except ValueError:
            return None
    else:
        normalized = re.sub(r"[^0-9]", "", raw)
        if not normalized:
            return None
        amount = int(normalized) * (1000 if multiplier == "tys" else 1)

    result = round(amount)
    return result if result > 0 else None


def parse_area(value: Any) -> float | None:
    """Parse an area string into square metres."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None

    lowered = str(value).lower().replace("\u00a0", " ")
    raw, _ = _clean_number(lowered)
    if not raw:
        return None

    normalized = raw.replace(" ", "").replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None

    if "ha" in lowered:
        amount *= 10_000
    elif re.search(r"\bar\b", lowered):
        amount *= 100

    return round(amount, 2) if amount > 0 else None


def parse_rooms(value: Any) -> int | None:
    """Parse a room count, including Otodom's English words."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    if isinstance(value, list):
        for item in value:
            parsed = parse_rooms(item)
            if parsed is not None:
                return parsed
        return None

    lowered = str(value).strip().lower()
    if not lowered:
        return None

    digits = re.search(r"\d+", lowered)
    if digits:
        rooms = int(digits.group(0))
        return rooms if rooms > 0 else None

    normalized = normalize_text(lowered)
    for word, rooms in _ROOM_WORDS.items():
        if word in normalized.split():
            return rooms
    return None


def parse_floor(value: Any) -> tuple[int | None, str | None]:
    """Parse a floor description into a number and a display label."""
    if value is None:
        return None, None
    if isinstance(value, int):
        return value, str(value)
    if isinstance(value, list):
        for item in value:
            floor, label = parse_floor(item)
            if floor is not None or label:
                return floor, label
        return None, None

    label = str(value).strip()
    if not label:
        return None, None

    normalized = normalize_text(label)
    if normalized in _FLOOR_WORDS:
        floor = _FLOOR_WORDS[normalized]
        return floor, label

    digits = re.search(r"-?\d+", normalized)
    if digits:
        return int(digits.group(0)), label
    return None, label


def parse_datetime_ms(value: Any) -> int | None:
    """Parse an ISO timestamp or date into Unix milliseconds."""
    if value is None:
        return None
    if isinstance(value, int | float):
        # Heuristic: seconds vs milliseconds.
        number = int(value)
        return number * 1000 if number < 10_000_000_000 else number

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def safe_int(value: Any) -> int | None:
    """Convert a scalar to an int when possible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = re.sub(r"[^0-9-]", "", str(value))
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def safe_float(value: Any) -> float | None:
    """Convert a scalar to a float when possible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return None
