"""Logique métier : distance géodésique, filtrage et classement des stations.

Ce module ne dépend pas du réseau : il transforme une liste de
:class:`~src.fetcher.RawStation` en une liste triée de
:class:`StationResult` exploitable par l'interface web ou le notifier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Optional

from src.fetcher import RawStation

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088

# Correspondance entre les codes carburant utilisés dans l'app et les
# libellés effectivement utilisés par l'API gouvernementale (repli,
# format "liste imbriquée" d'anciennes versions du dataset).
FUEL_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "E10": ("E10",),
    "SP98": ("SP98",),
    "SP95": ("SP95",),
    "Gazole": ("Gazole", "GAZOLE"),
    "E85": ("E85",),
    "GPLc": ("GPLc", "GPL", "GPLC"),
}

# Le dataset "flux-instantane-v2" expose en réalité les prix sous forme
# de colonnes plates par carburant (ex: "gazole_prix", "gazole_maj"),
# et non sous forme de liste imbriquée. C'est le format prioritaire.
FUEL_FIELD_PREFIX: dict[str, str] = {
    "E10": "e10",
    "SP95": "sp95",
    "SP98": "sp98",
    "Gazole": "gazole",
    "E85": "e85",
    "GPLc": "gplc",
}

SUPPORTED_FUEL_TYPES: tuple[str, ...] = tuple(FUEL_TYPE_ALIASES.keys())


class NoStationFoundError(Exception):
    """Levée quand aucune station ne correspond aux critères de recherche."""


@dataclass
class StationResult:
    """Résultat consolidé pour une station, prêt à être notifié/affiché.

    Attributes:
        station_id: Identifiant de la station.
        nom: Nom d'affichage de la station (enseigne + ville si dispo).
        adresse: Adresse complète.
        ville: Ville.
        code_postal: Code postal.
        latitude: Latitude GPS.
        longitude: Longitude GPS.
        distance_km: Distance depuis le point de recherche, en km.
        fuel_type: Type de carburant demandé.
        prix: Prix du carburant en euros.
        derniere_maj: Horodatage de dernière mise à jour du prix (ISO 8601
            si disponible).
        horaires: Informations d'ouverture (texte libre, peut être ``None``).
    """

    station_id: str
    nom: str
    adresse: str
    ville: str
    code_postal: str
    latitude: float
    longitude: float
    distance_km: float
    fuel_type: str
    prix: float
    derniere_maj: Optional[str]
    horaires: Optional[str]

    def to_dict(self) -> dict[str, object]:
        """Sérialise le résultat en dictionnaire simple (JSON-friendly).

        Returns:
            Un dictionnaire contenant tous les champs de la station.
        """
        return {
            "station_id": self.station_id,
            "nom": self.nom,
            "adresse": self.adresse,
            "ville": self.ville,
            "code_postal": self.code_postal,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_km": round(self.distance_km, 2),
            "fuel_type": self.fuel_type,
            "prix": self.prix,
            "derniere_maj": self.derniere_maj,
            "horaires": self.horaires,
        }


def haversine_distance_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Calcule la distance orthodromique (haversine) entre deux points GPS.

    Args:
        lat1: Latitude du premier point (degrés décimaux).
        lon1: Longitude du premier point (degrés décimaux).
        lat2: Latitude du second point (degrés décimaux).
        lon2: Longitude du second point (degrés décimaux).

    Returns:
        La distance entre les deux points, en kilomètres.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)

    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    c = 2 * asin(sqrt(a))
    return EARTH_RADIUS_KM * c


def _extract_price_from_flat_columns(
    station: RawStation, fuel_type: str
) -> Optional[tuple[float, Optional[str]]]:
    """Cherche le prix d'un carburant via les colonnes plates ``{fuel}_prix``/``{fuel}_maj``.

    C'est le format effectivement utilisé par le dataset
    ``prix-des-carburants-en-france-flux-instantane-v2`` (une colonne par
    carburant plutôt qu'une liste imbriquée).

    Args:
        station: Station brute issue de l'API.
        fuel_type: Code carburant recherché (ex: ``"Gazole"``).

    Returns:
        Un tuple ``(prix, date_maj)`` si trouvé et renseigné, sinon ``None``.
    """
    prefix = FUEL_FIELD_PREFIX.get(fuel_type)
    if prefix is None:
        return None

    valeur = station.raw.get(f"{prefix}_prix")
    if valeur is None:
        return None

    try:
        prix = float(valeur)
    except (TypeError, ValueError):
        logger.debug("Prix invalide pour %s à %s: %r", fuel_type, station.station_id, valeur)
        return None

    maj = station.raw.get(f"{prefix}_maj")
    return prix, maj


def _extract_price_from_nested_list(
    station: RawStation, fuel_type: str
) -> Optional[tuple[float, Optional[str]]]:
    """Cherche le prix d'un carburant dans un éventuel format "liste imbriquée" (legacy/repli).

    Gère l'absence de stock (carburant listé mais sans valeur, ou
    totalement absent), et les différentes clés possibles utilisées par
    d'anciennes versions de l'API (``nom``/``valeur``/``maj`` ou
    ``id``/``value``/``update``).

    Args:
        station: Station brute issue de l'API.
        fuel_type: Code carburant recherché (ex: ``"Gazole"``).

    Returns:
        Un tuple ``(prix, date_maj)`` si le carburant est disponible et
        son prix renseigné, sinon ``None``.
    """
    aliases = FUEL_TYPE_ALIASES.get(fuel_type, (fuel_type,))

    for entry in station.prix:
        nom = str(entry.get("nom") or entry.get("id") or "").upper()
        if nom not in [a.upper() for a in aliases]:
            continue

        valeur = entry.get("valeur", entry.get("value"))
        if valeur is None:
            logger.debug(
                "Carburant %s en rupture de stock à la station %s",
                fuel_type,
                station.station_id,
            )
            return None

        try:
            prix = float(valeur)
        except (TypeError, ValueError):
            logger.debug("Prix invalide pour %s à %s: %r", fuel_type, station.station_id, valeur)
            return None

        # Normalisation: certaines valeurs legacy sont exprimées en
        # centimes (ex: 165.9 au lieu de 1.659) selon la source.
        if prix > 10:
            prix = prix / 1000.0

        maj = entry.get("maj") or entry.get("update") or entry.get("date")
        return prix, maj

    return None


def _extract_price_for_fuel(station: RawStation, fuel_type: str) -> Optional[tuple[float, Optional[str]]]:
    """Cherche le prix d'un carburant donné dans les données brutes d'une station.

    Essaie d'abord le format "colonnes plates" (format réel de l'API
    actuelle), puis se replie sur le format "liste imbriquée" pour rester
    compatible avec d'éventuelles évolutions du schéma.

    Args:
        station: Station brute issue de l'API.
        fuel_type: Code carburant recherché (ex: ``"Gazole"``).

    Returns:
        Un tuple ``(prix, date_maj)`` si le carburant est disponible et
        son prix renseigné, sinon ``None``.
    """
    result = _extract_price_from_flat_columns(station, fuel_type)
    if result is not None:
        return result
    return _extract_price_from_nested_list(station, fuel_type)


def find_cheapest_station(
    stations: list[RawStation],
    center_lat: float,
    center_lon: float,
    fuel_type: str,
    radius_km: float,
) -> StationResult:
    """Filtre par carburant/rayon et retourne la station la moins chère.

    Args:
        stations: Liste des stations brutes (déjà pré-filtrées par rayon
            côté API, mais revérifiées ici par précision).
        center_lat: Latitude du point de recherche.
        center_lon: Longitude du point de recherche.
        fuel_type: Type de carburant (voir :data:`SUPPORTED_FUEL_TYPES`).
        radius_km: Rayon de recherche en kilomètres.

    Returns:
        Le meilleur résultat (prix le plus bas) sous forme de
        :class:`StationResult`.

    Raises:
        ValueError: Si ``fuel_type`` n'est pas supporté.
        NoStationFoundError: Si aucune station avec ce carburant en stock
            n'est trouvée dans le rayon donné.
    """
    if fuel_type not in SUPPORTED_FUEL_TYPES:
        raise ValueError(
            f"Type de carburant '{fuel_type}' non supporté. "
            f"Valeurs possibles: {', '.join(SUPPORTED_FUEL_TYPES)}"
        )

    candidates = list(rank_stations(stations, center_lat, center_lon, fuel_type, radius_km))

    if not candidates:
        raise NoStationFoundError(
            f"Aucune station proposant du {fuel_type} disponible dans un rayon de "
            f"{radius_km} km autour de ({center_lat}, {center_lon})."
        )

    return candidates[0]


def rank_stations(
    stations: list[RawStation],
    center_lat: float,
    center_lon: float,
    fuel_type: str,
    radius_km: float,
) -> list[StationResult]:
    """Filtre et trie toutes les stations correspondant aux critères.

    Args:
        stations: Liste des stations brutes.
        center_lat: Latitude du point de recherche.
        center_lon: Longitude du point de recherche.
        fuel_type: Type de carburant recherché.
        radius_km: Rayon de recherche en kilomètres.

    Returns:
        Liste de :class:`StationResult` triée par prix croissant (la
        moins chère en premier). Peut être vide.
    """
    results: list[StationResult] = []

    for station in stations:
        distance = haversine_distance_km(
            center_lat, center_lon, station.latitude, station.longitude
        )
        if distance > radius_km:
            continue

        price_info = _extract_price_for_fuel(station, fuel_type)
        if price_info is None:
            continue

        prix, maj = price_info
        enseigne = station.raw.get("marque") or station.raw.get("enseigne") or "Station"
        nom = f"{enseigne} - {station.ville}" if station.ville else str(enseigne)

        results.append(
            StationResult(
                station_id=station.station_id,
                nom=nom,
                adresse=station.adresse,
                ville=station.ville,
                code_postal=station.code_postal,
                latitude=station.latitude,
                longitude=station.longitude,
                distance_km=distance,
                fuel_type=fuel_type,
                prix=round(prix, 3),
                derniere_maj=maj,
                horaires=station.horaires,
            )
        )

    results.sort(key=lambda r: (r.prix, r.distance_km))
    return results


def parse_update_date(raw_date: Optional[str]) -> Optional[datetime]:
    """Tente de parser une date de mise à jour renvoyée par l'API.

    Args:
        raw_date: Chaîne de date brute (formats variables selon l'API).

    Returns:
        Un objet :class:`datetime.datetime`, ou ``None`` si le parsing
        échoue ou si ``raw_date`` est vide.
    """
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw_date, fmt)
        except ValueError:
            continue
    logger.debug("Impossible de parser la date: %s", raw_date)
    return None
