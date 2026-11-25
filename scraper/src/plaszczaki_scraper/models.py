"""Canonical listing model shared by adapters and the database layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OfferType = Literal["sale", "rent", "auction", "lease", "other"]
PropertyType = Literal["flat", "house", "plot", "commercial", "garage", "room", "other"]
Market = Literal["primary", "secondary", "unknown"]
PriceKind = Literal["asking", "starting", "call", "unknown"]
PriceUnit = Literal["total", "monthly", "nightly", "unknown"]
ExtractionMethod = Literal["adapter", "llm", "hybrid"]


@dataclass(slots=True)
class Listing:
    """One canonical real-estate listing."""

    source_id: str
    external_id: str
    url: str
    title: str | None = None
    description: str | None = None

    offer_type: OfferType = "other"
    property_type: PropertyType = "other"
    market: Market = "unknown"
    price_kind: PriceKind = "asking"
    price_unit: PriceUnit = "total"

    price_pln: int | None = None
    rent_pln: int | None = None
    deposit_pln: int | None = None
    price_per_m2_pln: int | None = None

    area_m2: float | None = None
    land_area_m2: float | None = None
    rooms: int | None = None
    floor: int | None = None
    floor_label: str | None = None
    build_year: int | None = None

    voivodeship: str | None = None
    county: str | None = None
    commune: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    address_text: str | None = None
    lat: float | None = None
    lng: float | None = None
    geo_source: str | None = None

    images: list[str] = field(default_factory=list)
    seller_type: str | None = None
    seller_name: str | None = None

    published_at: int | None = None
    modified_at: int | None = None

    auction_date: int | None = None
    auction_round: int | None = None
    case_number: str | None = None

    extraction_method: ExtractionMethod = "adapter"
    extraction_model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (without DB bookkeeping)."""
        return {
            "source_id": self.source_id,
            "external_id": self.external_id,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "offer_type": self.offer_type,
            "property_type": self.property_type,
            "market": self.market,
            "price_kind": self.price_kind,
            "price_unit": self.price_unit,
            "price_pln": self.price_pln,
            "rent_pln": self.rent_pln,
            "deposit_pln": self.deposit_pln,
            "price_per_m2_pln": self.price_per_m2_pln,
            "area_m2": self.area_m2,
            "land_area_m2": self.land_area_m2,
            "rooms": self.rooms,
            "floor": self.floor,
            "floor_label": self.floor_label,
            "build_year": self.build_year,
            "voivodeship": self.voivodeship,
            "county": self.county,
            "commune": self.commune,
            "city": self.city,
            "district": self.district,
            "street": self.street,
            "address_text": self.address_text,
            "lat": self.lat,
            "lng": self.lng,
            "geo_source": self.geo_source,
            "images": self.images,
            "seller_type": self.seller_type,
            "seller_name": self.seller_name,
            "published_at": self.published_at,
            "modified_at": self.modified_at,
            "auction_date": self.auction_date,
            "auction_round": self.auction_round,
            "case_number": self.case_number,
            "extraction_method": self.extraction_method,
            "extraction_model": self.extraction_model,
            "extra": self.extra,
        }
