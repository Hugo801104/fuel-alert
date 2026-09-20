"""Module d'envoi de notifications multi-canal (Telegram, Discord, Slack, e-mail...).

Utilise la librairie `Apprise <https://github.com/caronc/apprise>`_ qui
unifie l'envoi vers des dizaines de services à partir d'une simple URL
de configuration (ex: ``tgram://token/chat_id``, ``discord://webhook_id/webhook_token``).
"""

from __future__ import annotations

import logging
from typing import Optional

import apprise

from src.analyzer import StationResult, parse_update_date

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Levée quand l'envoi de la notification échoue sur tous les canaux."""


def build_message(result: StationResult) -> tuple[str, str]:
    """Construit le titre et le corps (Markdown) du message de notification.

    Args:
        result: Résultat de la station la moins chère à notifier.

    Returns:
        Un tuple ``(titre, corps_markdown)``.
    """
    maj_dt = parse_update_date(result.derniere_maj)
    maj_str = maj_dt.strftime("%d/%m/%Y à %H:%M") if maj_dt else (result.derniere_maj or "N/A")

    title = f"⛽ {result.fuel_type} à {result.prix:.3f} €/L - {result.nom}"

    body_lines = [
        f"**⛽ Carburant :** {result.fuel_type}",
        f"**💶 Prix :** {result.prix:.3f} €/L",
        f"**🏪 Station :** {result.nom}",
        f"**📍 Adresse :** {result.adresse}, {result.code_postal} {result.ville}",
        f"**📏 Distance :** {result.distance_km:.2f} km",
        f"**🕒 Dernière mise à jour du prix :** {maj_str}",
    ]
    if result.horaires:
        body_lines.append(f"**🕑 Horaires :** {result.horaires}")

    body_lines.append(
        f"\n[Voir sur la carte](https://www.openstreetmap.org/?mlat={result.latitude}"
        f"&mlon={result.longitude}#map=17/{result.latitude}/{result.longitude})"
    )

    return title, "\n\n".join(body_lines)


def send_notification(
    result: StationResult,
    notification_urls: list[str],
    dry_run: bool = False,
) -> bool:
    """Envoie une notification sur un ou plusieurs canaux via Apprise.

    Args:
        result: Résultat de la station la moins chère.
        notification_urls: Liste d'URLs Apprise (une par canal). Formats
            courants :
                - Telegram: ``tgram://{bot_token}/{chat_id}``
                - Discord: ``discord://{webhook_id}/{webhook_token}``
                - Slack: ``slack://{token_a}/{token_b}/{token_c}``
                - E-mail SMTP: ``mailtos://user:pass@smtp.example.com?to=dest@example.com``
        dry_run: Si ``True``, construit le message mais n'envoie rien
            (utile pour les tests depuis l'interface web).

    Returns:
        ``True`` si l'envoi a réussi sur au moins un canal (ou si
        ``dry_run`` est actif), ``False`` sinon.

    Raises:
        NotificationError: Si aucune URL n'est fournie et que
            ``dry_run`` est désactivé.
    """
    title, body = build_message(result)

    if dry_run:
        logger.info("[DRY-RUN] Notification non envoyée. Aperçu:\n%s\n%s", title, body)
        return True

    valid_urls = [url.strip() for url in notification_urls if url and url.strip()]
    if not valid_urls:
        raise NotificationError(
            "Aucun canal de notification configuré (variable NOTIFICATION_URLS vide)."
        )

    notifier = apprise.Apprise()
    added = 0
    for url in valid_urls:
        if notifier.add(url):
            added += 1
        else:
            logger.warning("URL de notification invalide, ignorée: %s", _redact(url))

    if added == 0:
        raise NotificationError("Aucune URL de notification valide n'a pu être ajoutée.")

    success = notifier.notify(title=title, body=body, body_format=apprise.NotifyFormat.MARKDOWN)

    if success:
        logger.info("Notification envoyée avec succès sur %d canal(aux).", added)
    else:
        logger.error("Échec de l'envoi de la notification sur tous les canaux configurés.")

    return bool(success)


def _redact(url: str) -> str:
    """Masque les éventuels secrets contenus dans une URL pour le logging.

    Args:
        url: URL potentiellement sensible (contient un token/mot de passe).

    Returns:
        Une version tronquée de l'URL, sûre pour les logs.
    """
    scheme = url.split("://", 1)[0] if "://" in url else "unknown"
    return f"{scheme}://***redacted***"
