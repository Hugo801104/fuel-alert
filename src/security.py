"""Validation des entrées utilisées par les interfaces publiques."""

from __future__ import annotations

import math
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_RADIUS_KM = 50.0
ALLOWED_NOTIFICATION_SCHEMES = {"tgram", "telegram", "discord"}


def validate_coordinates(latitude: float, longitude: float) -> None:
    """Valide des coordonnées GPS finies et dans les bornes WGS84."""
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("Les coordonnées doivent être des nombres finis.")
    if not -90 <= latitude <= 90:
        raise ValueError("La latitude doit être comprise entre -90 et 90.")
    if not -180 <= longitude <= 180:
        raise ValueError("La longitude doit être comprise entre -180 et 180.")


def validate_radius(radius_km: float) -> None:
    """Valide un rayon positif et borné pour limiter les requêtes coûteuses."""
    if not math.isfinite(radius_km) or not 0 < radius_km <= MAX_RADIUS_KM:
        raise ValueError(f"Le rayon doit être compris entre 0 et {MAX_RADIUS_KM:g} km.")


def validate_smtp_url(url: str) -> None:
    """Empêche une URL SMTP de cibler une adresse locale ou privée."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"mail", "mails", "mailto", "mailtos"}:
        return
    host = parsed.hostname
    if not host:
        raise ValueError("L'hôte SMTP est obligatoire.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError("L'hôte SMTP est introuvable.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            raise ValueError("Les hôtes SMTP privés ou locaux sont interdits.")


def validate_notification_url(url: str) -> None:
    """Autorise uniquement Telegram et Discord dans la configuration sortante."""
    if any(ord(character) < 32 for character in url):
        raise ValueError("L'URL de notification contient un caractère de contrôle.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_NOTIFICATION_SCHEMES:
        raise ValueError("Seuls les canaux Telegram et Discord sont autorisés.")
    if not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError("L'URL de notification est incomplète.")
