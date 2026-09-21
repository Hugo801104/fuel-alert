"""Module d'interaction avec l'API Open Data des prix des carburants.

Ce module encapsule tous les appels HTTP vers l'API Explore v2.1 de
data.economie.gouv.fr, avec gestion de la pagination, des timeouts et
des erreurs réseau/HTTP.

Documentation API :
    https://data.economie.gouv.fr/api/explore/v2.1/console
    Dataset : prix-des-carburants-en-france-flux-instantane-v2
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from src.security import validate_coordinates, validate_radius

logger = logging.getLogger(__name__)

API_BASE_URL = (
    "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "prix-des-carburants-en-france-flux-instantane-v2/records"
)

DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_PAGE_SIZE = 100
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


class FuelAPIError(Exception):
    """Exception levée en cas d'échec de communication avec l'API."""


@dataclass
class RawStation:
    """Représente un enregistrement brut renvoyé par l'API.

    Attributes:
        station_id: Identifiant unique de la station.
        adresse: Adresse postale de la station.
        ville: Ville de la station.
        code_postal: Code postal.
        latitude: Latitude GPS (WGS84).
        longitude: Longitude GPS (WGS84).
        prix: Liste de dictionnaires bruts décrivant chaque carburant
            disponible (nom, valeur, date de mise à jour).
        horaires: Informations d'ouverture brutes (peut être ``None``).
        raw: Enregistrement complet non transformé, conservé pour
            debug ou extension future.
    """

    station_id: str
    adresse: str
    ville: str
    code_postal: str
    latitude: float
    longitude: float
    prix: list[dict[str, Any]] = field(default_factory=list)
    horaires: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_prix_field(value: Any) -> list[dict[str, Any]]:
    """Normalise le champ ``prix`` de l'API en liste de dictionnaires.

    Le champ peut être renvoyé par l'API sous forme de liste de
    dictionnaires, d'un dictionnaire unique, ou absent. Cette fonction
    homogénéise ces cas.

    Args:
        value: Valeur brute du champ ``prix`` telle que renvoyée par
            l'API (list, dict ou None).

    Returns:
        Une liste de dictionnaires, vide si aucune donnée exploitable.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    logger.debug("Champ 'prix' de type inattendu ignoré: %s", type(value))
    return []


def _extract_coordinates(record: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Extrait latitude/longitude d'un enregistrement API.

    Le champ géographique principal de ce dataset s'appelle ``geom``
    (GeoJSON Point ou dict ``{"lon": x, "lat": y}`` selon le format de
    réponse). D'autres clés sont tentées en repli pour robustesse face
    à d'éventuelles évolutions du schéma.

    Args:
        record: Enregistrement brut (dictionnaire ``result`` de l'API).

    Returns:
        Un tuple ``(latitude, longitude)``, ou ``(None, None)`` si les
        coordonnées sont introuvables.
    """
    for key in ("geom", "coordonnees", "geo_point_2d", "position"):
        geo = record.get(key)
        if isinstance(geo, dict):
            lat = geo.get("lat")
            lon = geo.get("lon")
            if lat is not None and lon is not None:
                try:
                    coordinates = float(lat), float(lon)
                    validate_coordinates(*coordinates)
                    return coordinates
                except (TypeError, ValueError):
                    continue
            coords = geo.get("coordinates")
            if isinstance(coords, (list, tuple)) and len(coords) == 2:
                try:
                    coordinates = float(coords[1]), float(coords[0])
                    validate_coordinates(*coordinates)
                    return coordinates
                except (TypeError, ValueError):
                    continue
        if isinstance(geo, (list, tuple)) and len(geo) == 2:
            try:
                coordinates = float(geo[0]), float(geo[1])
                validate_coordinates(*coordinates)
                return coordinates
            except (TypeError, ValueError):
                continue

    lat_raw = record.get("latitude")
    lon_raw = record.get("longitude")
    if lat_raw is not None and lon_raw is not None:
        try:
            coordinates = float(lat_raw), float(lon_raw)
            validate_coordinates(*coordinates)
            return coordinates
        except (TypeError, ValueError):
            pass
    return None, None


def _record_to_station(record: dict[str, Any]) -> Optional[RawStation]:
    """Convertit un enregistrement API brut en :class:`RawStation`.

    Args:
        record: Dictionnaire ``result`` renvoyé par l'API pour une
            station donnée.

    Returns:
        Une instance de :class:`RawStation`, ou ``None`` si les données
        essentielles (coordonnées) sont manquantes.
    """
    latitude, longitude = _extract_coordinates(record)
    if latitude is None or longitude is None:
        logger.debug("Enregistrement ignoré (coordonnées manquantes): %s", record.get("id"))
        return None

    return RawStation(
        station_id=str(record.get("id") or record.get("station_id") or "unknown"),
        adresse=str(record.get("adresse") or record.get("adresse_station") or ""),
        ville=str(record.get("ville") or record.get("com_arm_name") or ""),
        code_postal=str(record.get("cp") or record.get("code_postal") or ""),
        latitude=latitude,
        longitude=longitude,
        prix=_parse_prix_field(record.get("prix") or record.get("carburants_disponibles")),
        horaires=record.get("horaires") or record.get("horaires_jour"),
        raw=record,
    )


def fetch_stations(
    latitude: float,
    longitude: float,
    radius_km: float,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = 20,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    session: Optional[requests.Session] = None,
) -> list[RawStation]:
    """Récupère les stations-service dans un rayon donné autour d'un point GPS.

    Utilise une clause ODSQL ``where=within_distance(geom, ...)`` côté
    serveur pour ne récupérer que les enregistrements pertinents, avec
    pagination automatique jusqu'à épuisement des résultats ou atteinte
    de ``max_pages``.

    Args:
        latitude: Latitude du point de recherche (WGS84).
        longitude: Longitude du point de recherche (WGS84).
        radius_km: Rayon de recherche en kilomètres.
        page_size: Nombre d'enregistrements par page (max 100 côté API).
        max_pages: Nombre maximal de pages à parcourir (sécurité contre
            les boucles infinies).
        timeout: Timeout HTTP en secondes par requête.
        session: Session ``requests`` optionnelle (réutilisation de
            connexions, tests unitaires).

    Returns:
        La liste des stations trouvées (peut être vide).

    Raises:
        FuelAPIError: Si l'API est injoignable après plusieurs tentatives
            ou renvoie une réponse invalide.
    """
    validate_coordinates(latitude, longitude)
    validate_radius(radius_km)
    if not 1 <= page_size <= 100:
        raise ValueError("page_size doit être compris entre 1 et 100")
    if max_pages < 1:
        raise ValueError("max_pages doit être positif")
    if timeout <= 0:
        raise ValueError("timeout doit être positif")

    http = session or requests.Session()
    stations: list[RawStation] = []
    offset = 0

    # L'API Explore v2.1 n'accepte plus le paramètre historique
    # "geofilter.distance" (hérité de la v1) : il est silencieusement
    # ignoré et l'API renvoie alors l'intégralité du flux national sans
    # filtrage. Le filtrage spatial se fait désormais via une clause
    # ODSQL "where" utilisant la fonction within_distance() sur le champ
    # géographique "geom". Attention à l'ordre WKT: POINT(longitude latitude).
    where_clause = (
        f"within_distance(geom, GEOM'POINT({longitude} {latitude})', {radius_km}km)"
    )

    for page in range(max_pages):
        params = {
            "limit": min(page_size, 100),
            "offset": offset,
            "where": where_clause,
        }

        if page == 0:
            logger.debug("Requête API avec where=%s", where_clause)

        payload = _get_with_retries(http, API_BASE_URL, params, timeout)
        results = payload.get("results", [])
        logger.info("Page %d: %d enregistrement(s) reçu(s)", page + 1, len(results))

        if not results:
            break

        for record in results:
            station = _record_to_station(record)
            if station is not None:
                stations.append(station)

        if len(results) < params["limit"]:
            break
        offset += params["limit"]

    logger.info(
        "Total stations récupérées dans un rayon de %.1f km: %d", radius_km, len(stations)
    )
    return stations


def _get_with_retries(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    """Effectue une requête GET avec retries exponentiels.

    Args:
        session: Session HTTP à utiliser.
        url: URL cible.
        params: Paramètres de requête.
        timeout: Timeout en secondes.

    Returns:
        Le corps de la réponse désérialisé en JSON.

    Raises:
        FuelAPIError: Si toutes les tentatives échouent.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout as exc:
            last_error = exc
            logger.warning("Timeout API (tentative %d/%d)", attempt, MAX_RETRIES)
        except requests.exceptions.HTTPError as exc:
            last_error = exc
            status_code = getattr(exc.response, "status_code", None)
            logger.warning(
                "Erreur HTTP %s (tentative %d/%d)",
                status_code or "?",
                attempt,
                MAX_RETRIES,
            )
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                break
        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning("Erreur réseau (tentative %d/%d): %s", attempt, MAX_RETRIES, exc)
        except ValueError as exc:  # JSON invalide
            last_error = exc
            logger.warning("Réponse JSON invalide (tentative %d/%d)", attempt, MAX_RETRIES)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise FuelAPIError(
        f"Échec de récupération des données depuis l'API après {MAX_RETRIES} tentatives: "
        f"{last_error}"
    ) from last_error
