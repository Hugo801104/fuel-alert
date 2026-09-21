"""Accès aux abonnements quotidiens stockés dans Supabase."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

CHAT_ID_PATTERN = re.compile(r"^-?\d{1,30}$")
DEFAULT_DURATION_DAYS = 10
MAX_DURATION_DAYS = 30


class SubscriptionError(Exception):
    """Levée lorsqu'un abonnement ne peut pas être créé ou traité."""


def _validate_supabase_url(url: str) -> str:
    """Valide l'URL du projet Supabase, sans suffixe d'API REST."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SubscriptionError(
            "SUPABASE_URL doit être l'URL HTTPS du projet, par exemple "
            "https://xxxx.supabase.co."
        )
    if parsed.path.rstrip("/") == "/rest/v1" or parsed.path not in {"", "/"}:
        raise SubscriptionError(
            "SUPABASE_URL doit contenir uniquement l'URL du projet "
            "(retirez /rest/v1 et tout autre chemin)."
        )
    if parsed.query or parsed.fragment:
        raise SubscriptionError("SUPABASE_URL ne doit pas contenir de paramètres ni de fragment.")
    return url.rstrip("/")


def validate_telegram_chat_id(chat_id: str) -> str:
    """Valide et normalise un identifiant de conversation Telegram."""
    normalized = str(chat_id).strip()
    if not CHAT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Le Chat ID Telegram doit être un nombre entier valide.")
    return normalized


def validate_duration_days(duration_days: int) -> int:
    """Valide la durée d'un abonnement en jours."""
    if not 1 <= duration_days <= MAX_DURATION_DAYS:
        raise ValueError(f"La durée doit être comprise entre 1 et {MAX_DURATION_DAYS} jours.")
    return duration_days


def get_supabase_client(url: str | None = None, key: str | None = None) -> Any:
    """Construit un client Supabase à partir des variables d'environnement."""
    supabase_url = (url or os.getenv("SUPABASE_URL", "")).strip()
    service_role_key = (key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not supabase_url or not service_role_key:
        raise SubscriptionError(
            "SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY doivent être configurées."
        )
    supabase_url = _validate_supabase_url(supabase_url)

    try:
        from supabase import create_client
    except ImportError as exc:
        raise SubscriptionError(
            "La dépendance supabase n'est pas installée. Exécutez: pip install supabase."
        ) from exc

    return create_client(supabase_url, service_role_key)


def create_subscription(
    client: Any,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    fuel_type: str,
    telegram_chat_id: str,
    duration_days: int = DEFAULT_DURATION_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Crée un abonnement actif dont le premier envoi est immédiat."""
    from src.analyzer import SUPPORTED_FUEL_TYPES
    from src.security import validate_coordinates, validate_radius

    validate_coordinates(latitude, longitude)
    validate_radius(radius_km)
    if fuel_type not in SUPPORTED_FUEL_TYPES:
        raise ValueError(f"Carburant non supporté: {fuel_type}")
    validate_duration_days(duration_days)
    normalized_chat_id = validate_telegram_chat_id(telegram_chat_id)

    started_at = now or datetime.now(timezone.utc)
    expires_at = started_at + timedelta(days=duration_days)
    payload = {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "fuel_type": fuel_type,
        "telegram_chat_id": normalized_chat_id,
        "duration_days": duration_days,
        "expires_at": expires_at.isoformat(),
        "next_run_at": started_at.isoformat(),
    }
    response = client.table("subscriptions").insert(payload).execute()
    if not response.data:
        raise SubscriptionError("Supabase n'a pas retourné l'abonnement créé.")
    return response.data[0]


def list_due_subscriptions(client: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Retourne les abonnements actifs arrivés à leur prochaine échéance."""
    current = now or datetime.now(timezone.utc)
    response = (
        client.table("subscriptions")
        .select("*")
        .eq("active", True)
        .lte("next_run_at", current.isoformat())
        .gt("expires_at", current.isoformat())
        .execute()
    )
    return list(response.data or [])


def notification_was_sent_today(client: Any, subscription_id: str, today: str) -> bool:
    """Évite un doublon si le workflow quotidien est relancé."""
    response = (
        client.table("notification_logs")
        .select("id")
        .eq("subscription_id", subscription_id)
        .eq("notification_date", today)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def record_notification(
    client: Any,
    *,
    subscription_id: str,
    sent_count: int,
    duration_days: int,
    sent_at: datetime | None = None,
) -> None:
    """Journalise un envoi et programme le suivant, ou termine l'abonnement."""
    current = sent_at or datetime.now(timezone.utc)
    next_count = sent_count + 1
    update = {
        "sent_count": next_count,
        "last_sent_at": current.isoformat(),
        "next_run_at": (current + timedelta(days=1)).isoformat(),
        "active": next_count < duration_days,
    }
    client.table("subscriptions").update(update).eq("id", subscription_id).execute()


def log_notification(client: Any, subscription_id: str, sent_at: datetime | None = None) -> None:
    """Ajoute une trace idempotente de l'envoi du jour."""
    current = sent_at or datetime.now(timezone.utc)
    client.table("notification_logs").insert(
        {
            "subscription_id": subscription_id,
            "notification_date": current.date().isoformat(),
            "sent_at": current.isoformat(),
        }
    ).execute()


def telegram_notification_url(bot_token: str, chat_id: str) -> str:
    """Construit l'URL Apprise Telegram sans exposer le chat ID ailleurs."""
    if not bot_token.strip():
        raise SubscriptionError("TELEGRAM_BOT_TOKEN doit être configuré.")
    return f"tgram://{bot_token.strip()}/{validate_telegram_chat_id(chat_id)}"