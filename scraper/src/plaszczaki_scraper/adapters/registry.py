"""Adapter registry.

Adapters are imported lazily so a missing optional module does not break the
whole CLI. The mapping is intentionally small; adding a source means adding one
entry here and one entry in ``config/adapters.json``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol

from plaszczaki_scraper.adapters.base import Adapter
from plaszczaki_scraper.config import AdapterConfig
from plaszczaki_scraper.regions import RegionRegistry

_ADAPTERS: dict[str, tuple[str, str]] = {
    "adresowo": ("plaszczaki_scraper.adapters.adresowo", "AdresowoAdapter"),
    "allegrolokalnie": (
        "plaszczaki_scraper.adapters.allegrolokalnie",
        "AllegrolokalnieAdapter",
    ),
    "amw-nieruchomosci": (
        "plaszczaki_scraper.adapters.amw_nieruchomosci",
        "AmwAdapter",
    ),
    "alertyprzetargowe": (
        "plaszczaki_scraper.adapters.alertyprzetargowe",
        "AlertyPrzetargoweAdapter",
    ),
    "axiimmo": ("plaszczaki_scraper.adapters.axiimmo", "AxiImmoAdapter"),
    "domiporta": ("plaszczaki_scraper.adapters.domiporta", "DomiportaAdapter"),
    "domy": ("plaszczaki_scraper.adapters.domy", "DomyAdapter"),
    "flats": ("plaszczaki_scraper.adapters.flats", "FlatsAdapter"),
    "gdom": ("plaszczaki_scraper.adapters.gdom", "GdomAdapter"),
    "gethome": ("plaszczaki_scraper.adapters.gethome", "GetHomeAdapter"),
    "gratka": ("plaszczaki_scraper.adapters.gratka", "GratkaAdapter"),
    "homeportal": ("plaszczaki_scraper.adapters.homeportal", "HomeportalAdapter"),
    "kupbezposrednio": (
        "plaszczaki_scraper.adapters.kupbezposrednio",
        "KupbezposrednioAdapter",
    ),
    "komornik": ("plaszczaki_scraper.adapters.komornik", "KomornikAdapter"),
    "luksusowemieszkania": (
        "plaszczaki_scraper.adapters.luksusowemieszkania",
        "LuksusoweMieszkaniaAdapter",
    ),
    "magazyny-online": (
        "plaszczaki_scraper.adapters.magazyny_online",
        "MagazynyOnlineAdapter",
    ),
    "mapalicytacji": (
        "plaszczaki_scraper.adapters.mapalicytacji",
        "MapalicytacjiAdapter",
    ),
    "morizon": ("plaszczaki_scraper.adapters.morizon", "MorizonAdapter"),
    "nieruchomosci-online": (
        "plaszczaki_scraper.adapters.nieruchomosci_online",
        "NieruchomosciOnlineAdapter",
    ),
    "nieruchomosciowi": (
        "plaszczaki_scraper.adapters.nieruchomosciowi",
        "NieruchomosciowiAdapter",
    ),
    "nportal": ("plaszczaki_scraper.adapters.nportal", "NportalAdapter"),
    "noweinwestycje": (
        "plaszczaki_scraper.adapters.noweinwestycje",
        "NoweinwestycjeAdapter",
    ),
    "ogloszeniasyndyka": (
        "plaszczaki_scraper.adapters.ogloszeniasyndyka",
        "OgloszeniaSyndykaAdapter",
    ),
    "okolica": ("plaszczaki_scraper.adapters.okolica", "OkolicaAdapter"),
    "oferty-net": ("plaszczaki_scraper.adapters.oferty_net", "OfertyNetAdapter"),
    "otoprzetargi": (
        "plaszczaki_scraper.adapters.otoprzetargi",
        "OtoprzetargiAdapter",
    ),
    "olx": ("plaszczaki_scraper.adapters.olx", "OlxAdapter"),
    "otodom": ("plaszczaki_scraper.adapters.otodom", "OtodomAdapter"),
    "partnersinternational": (
        "plaszczaki_scraper.adapters.partnersinternational",
        "PartnersInternationalAdapter",
    ),
    "pkobp-nieruchomosci": (
        "plaszczaki_scraper.adapters.pkobp_nieruchomosci",
        "PkobpNieruchomosciAdapter",
    ),
    "pkp": ("plaszczaki_scraper.adapters.pkp", "PkpAdapter"),
    "poczta-polska": (
        "plaszczaki_scraper.adapters.poczta_polska",
        "PocztaPolskaAdapter",
    ),
    "rentola": ("plaszczaki_scraper.adapters.rentola", "RentolaAdapter"),
    "rentumo": ("plaszczaki_scraper.adapters.rentumo", "RentumoAdapter"),
    "rynekpierwotny": (
        "plaszczaki_scraper.adapters.rynekpierwotny",
        "RynekpierwotnyAdapter",
    ),
    "sprzedajemy": ("plaszczaki_scraper.adapters.sprzedajemy", "SprzedajemyAdapter"),
    "srk": ("plaszczaki_scraper.adapters.srk", "SrkAdapter"),
    "sprzedambiznes": (
        "plaszczaki_scraper.adapters.sprzedambiznes",
        "SprzedambiznesAdapter",
    ),
    "syndykaukcje": (
        "plaszczaki_scraper.adapters.syndykaukcje",
        "SyndykAukcjeAdapter",
    ),
    "szybko": ("plaszczaki_scraper.adapters.szybko", "SzybkoAdapter"),
    "szybkokomercyjne": (
        "plaszczaki_scraper.adapters.szybkokomercyjne",
        "SzybkokomercyjneAdapter",
    ),
    "tauron": ("plaszczaki_scraper.adapters.tauron", "TauronAdapter"),
    "terenyprzemyslowe": (
        "plaszczaki_scraper.adapters.terenyprzemyslowe",
        "TerenyPrzemysloweAdapter",
    ),
    "tabelaofert": ("plaszczaki_scraper.adapters.tabelaofert", "TabelaofertAdapter"),
    "txo": ("plaszczaki_scraper.adapters.txo", "TxoAdapter"),
    "vilea": ("plaszczaki_scraper.adapters.vilea", "VileaAdapter"),
    "wybierzdewelopera": (
        "plaszczaki_scraper.adapters.wybierzdewelopera",
        "WybierzdeweloperaAdapter",
    ),
}


class AdapterFactory(Protocol):
    def __call__(
        self,
        *,
        source_id: str,
        regions: RegionRegistry,
    ) -> Adapter: ...


def _load_factory(adapter: str) -> AdapterFactory:
    target = _ADAPTERS.get(adapter)
    if target is None:
        raise ValueError(f"unknown adapter '{adapter}'")
    module_name, class_name = target
    module = import_module(module_name)
    factory = getattr(module, class_name)
    return factory


def build_adapter(
    config: AdapterConfig,
    *,
    regions: RegionRegistry,
) -> Adapter:
    """Instantiate the adapter named in the configuration."""
    try:
        factory = _load_factory(config.adapter)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"adapter '{config.adapter}' for source '{config.id}' is not available: {exc}",
        ) from exc
    return factory(source_id=config.id, regions=regions)
