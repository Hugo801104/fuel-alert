"""Service de geocodage des adresses francaises."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Callable

from geopy.exc import GeocoderQuotaExceeded, GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

from src.security import validate_coordinates

logger = logging.getLogger(__name__)

MAX_ADDRESS_LENGTH = 200
GEOCODING_TIMEOUT_SECONDS = 10
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class GeocodingError(Exception):
    """Erreur exploitable lors d'une requete de geocodage."""


def normalize_address(address: str) -> str:
    """Nettoie et borne une adresse avant de l'envoyer au fournisseur."""
    normalized = " ".join(str(address).split())
    if not normalized:
        raise ValueError("L'adresse ne peut pas être vide.")
    if len(normalized) > MAX_ADDRESS_LENGTH:
        raise ValueError(f"L'adresse ne doit pas dépasser {MAX_ADDRESS_LENGTH} caractères.")
    if _CONTROL_CHARACTERS.search(normalized):
        raise ValueError("L'adresse contient des caractères de contrôle interdits.")
    return normalized


def _create_geocoder() -> Nominatim:
    return Nominatim(user_agent="fuel-price-alert/1.0 (geocoding contact)")


@lru_cache(maxsize=1)
def _get_geocoder() -> Nominatim:
    """Réutilise le client Nominatim pendant la durée du processus."""
    return _create_geocoder()


def geocode_address(
    address: str,
    *,
    geocoder: Any | None = None,
    timeout: int = GEOCODING_TIMEOUT_SECONDS,
) -> tuple[float, float] | None:
    """Retourne les coordonnées françaises d'une adresse, sans appel réseau en cas d'entrée invalide."""
    query = normalize_address(address)
    try:
        location = (geocoder or _get_geocoder()).geocode(
            query,
            exactly_one=True,
            addressdetails=False,
            country_codes="fr",
            timeout=timeout,
        )
    except (GeocoderTimedOut, GeocoderQuotaExceeded) as exc:
        raise GeocodingError("Le service de recherche d'adresse est temporairement indisponible.") from exc
    except GeocoderServiceError as exc:
        raise GeocodingError("Le service de recherche d'adresse a refusé la requête.") from exc

    if location is None:
        return None
    try:
        latitude = float(location.latitude)
        longitude = float(location.longitude)
        validate_coordinates(latitude, longitude)
    except (AttributeError, TypeError, ValueError) as exc:
        raise GeocodingError("Le service a renvoyé des coordonnées invalides.") from exc
    return latitude, longitude
