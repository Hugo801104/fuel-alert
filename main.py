#!/usr/bin/env python3
"""Point d'entrée CLI pour l'exécution automatique (GitHub Actions / cron).

Lit la configuration depuis les variables d'environnement (ou un
fichier ``.env`` en local via ``python-dotenv``), interroge l'API des
prix des carburants, détermine les trois meilleurs prix mis à jour depuis
moins de trois jours dans le rayon configuré, puis envoie une notification.

Variables d'environnement attendues:
    LATITUDE (float): Latitude du point de recherche. Obligatoire.
    LONGITUDE (float): Longitude du point de recherche. Obligatoire.
    RADIUS_KM (float): Rayon de recherche en kilomètres. Défaut: 5.
    FUEL_TYPE (str): Type de carburant (E10, SP98, Gazole, E85, GPLc).
        Défaut: "Gazole".
    NOTIFICATION_URLS (str): URLs Apprise séparées par des virgules.
    DRY_RUN (str): "true"/"1" pour désactiver l'envoi réel. Défaut: "false".
    LOG_LEVEL (str): Niveau de log (DEBUG, INFO, WARNING...). Défaut: "INFO".

Exit codes:
    0: Succès (station trouvée et notification envoyée, ou dry-run).
    1: Erreur de configuration (variables manquantes/invalides).
    2: Erreur API (source de données injoignable).
    3: Aucune station trouvée dans le rayon donné.
    4: Échec de l'envoi de la notification.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from src.analyzer import (
    SUPPORTED_FUEL_TYPES,
    NoStationFoundError,
    rank_stations,
)
from src.fetcher import FuelAPIError, fetch_stations
from src.notifier import NotificationError, send_notification
from src.security import validate_coordinates, validate_radius

logger = logging.getLogger("fuel_alert")


def _configure_logging(level_name: str) -> None:
    """Configure le logging standard pour toute l'application.

    Args:
        level_name: Nom du niveau de log (ex: ``"INFO"``, ``"DEBUG"``).
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _read_config() -> dict[str, object]:
    """Lit et valide la configuration depuis les variables d'environnement.

    Returns:
        Un dictionnaire de configuration validé.

    Raises:
        ValueError: Si une variable obligatoire est manquante ou invalide.
    """
    load_dotenv()  # No-op silencieux si aucun fichier .env n'existe (ex: en CI).

    lat_raw = os.getenv("LATITUDE")
    lon_raw = os.getenv("LONGITUDE")
    if not lat_raw or not lon_raw:
        raise ValueError(
            "Les variables d'environnement LATITUDE et LONGITUDE sont obligatoires."
        )

    try:
        latitude = float(lat_raw)
        longitude = float(lon_raw)
        validate_coordinates(latitude, longitude)
    except ValueError as exc:
        raise ValueError("LATITUDE/LONGITUDE doivent être des nombres décimaux.") from exc

    try:
        radius_km = float(os.getenv("RADIUS_KM", "5"))
        validate_radius(radius_km)
    except ValueError as exc:
        raise ValueError("RADIUS_KM doit être un nombre compris entre 0 et 50 km.") from exc
    fuel_type = os.getenv("FUEL_TYPE", "Gazole")
    if fuel_type not in SUPPORTED_FUEL_TYPES:
        raise ValueError(
            f"FUEL_TYPE='{fuel_type}' invalide. Valeurs possibles: "
            f"{', '.join(SUPPORTED_FUEL_TYPES)}"
        )

    notification_urls = [
        url for url in os.getenv("NOTIFICATION_URLS", "").split(",") if url.strip()
    ]
    dry_run = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}

    return {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "fuel_type": fuel_type,
        "notification_urls": notification_urls,
        "dry_run": dry_run,
    }


def run(config: dict[str, object]) -> int:
    """Exécute le workflow complet: fetch -> analyze -> notify.

    Args:
        config: Dictionnaire de configuration (voir :func:`_read_config`).

    Returns:
        Un code de sortie entier (voir docstring du module).
    """
    try:
        stations = fetch_stations(
            latitude=config["latitude"],  # type: ignore[arg-type]
            longitude=config["longitude"],  # type: ignore[arg-type]
            radius_km=config["radius_km"],  # type: ignore[arg-type]
        )
    except FuelAPIError as exc:
        logger.error("Erreur API: %s", exc)
        return 2

    try:
        results = rank_stations(
            stations,
            center_lat=config["latitude"],  # type: ignore[arg-type]
            center_lon=config["longitude"],  # type: ignore[arg-type]
            fuel_type=config["fuel_type"],  # type: ignore[arg-type]
            radius_km=config["radius_km"],  # type: ignore[arg-type]
        )
    except NoStationFoundError as exc:
        logger.warning("Aucune station trouvée: %s", exc)
        return 3

    if not results:
        logger.warning("Aucune station avec un prix mis à jour depuis moins de 3 jours.")
        return 3

    logger.info(
        "%d meilleur(s) prix trouvé(s), de %.3f €/L à %.3f €/L",
        len(results),
        results[0].prix,
        results[-1].prix,
    )

    try:
        notification_sent = send_notification(
            results,
            notification_urls=config["notification_urls"],  # type: ignore[arg-type]
            dry_run=config["dry_run"],  # type: ignore[arg-type]
        )
    except NotificationError as exc:
        logger.error("Échec de la notification: %s", exc)
        return 4

    if not notification_sent:
        logger.error("La notification n'a été envoyée sur aucun canal.")
        return 4

    return 0


def main() -> int:
    """Fonction principale invoquée en ligne de commande.

    Returns:
        Le code de sortie du processus.
    """
    _configure_logging(os.getenv("LOG_LEVEL", "INFO"))

    try:
        config = _read_config()
    except ValueError as exc:
        logger.error("Erreur de configuration: %s", exc)
        return 1

    logger.info(
        "Recherche de %s dans un rayon de %.1f km autour de (%.5f, %.5f)",
        config["fuel_type"],
        config["radius_km"],
        config["latitude"],
        config["longitude"],
    )

    return run(config)


if __name__ == "__main__":
    sys.exit(main())
