"""Interface web Streamlit - Fuel Price Alert.

Permet de :
    - sélectionner un point GPS sur une carte interactive ou saisir une adresse ;
    - choisir le type de carburant, le rayon de recherche et le canal de notification ;
    - lancer une recherche immédiate et visualiser les résultats sur la carte ;
    - exporter la configuration générée sous forme de fichier ``.env`` /
      liste de GitHub Secrets.

Lancement local:
    streamlit run app.py
"""

from __future__ import annotations

import logging
import os
import time
from html import escape
from typing import Optional

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src.analyzer import (
    SUPPORTED_FUEL_TYPES,
    NoStationFoundError,
    StationResult,
    rank_stations,
)
from src.fetcher import FuelAPIError, fetch_stations
from src.geocoding import GeocodingError, geocode_address
from src.notifier import NotificationError, build_message, send_notification
from src.security import validate_coordinates
from src.subscriptions import (
    DEFAULT_DURATION_DAYS,
    MAX_DURATION_DAYS,
    SubscriptionError,
    create_subscription,
    discord_notification_url,
    get_supabase_client,
    SUPPORTED_CHANNELS,
    telegram_notification_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fuel_alert.app")

st.set_page_config(
    page_title="Fuel Price Alert",
    page_icon="⛽",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_LAT = 48.8566  # Paris
DEFAULT_LON = 2.3522
TELEGRAM_BOT_USERNAME = "fuel_price_alert_bot"
SUBSCRIPTION_LIMIT = 3
SUBSCRIPTION_WINDOW_SECONDS = 24 * 60 * 60
TEST_NOTIFICATION_LIMIT = 5
TEST_NOTIFICATION_WINDOW_SECONDS = 60 * 60

def _init_session_state() -> None:
    """Initialise les valeurs par défaut du ``st.session_state``."""
    defaults = {
        "map_lat": DEFAULT_LAT,
        "map_lon": DEFAULT_LON,
        "results": None,
        "last_error": None,
        "subscription_attempts": [],
        "notification_test_attempts": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _allow_action(key: str, limit: int, window_seconds: int) -> bool:
    """Applique un quota local par session pour limiter les abus évidents."""
    now = time.monotonic()
    attempts = [stamp for stamp in st.session_state[key] if now - stamp < window_seconds]
    if len(attempts) >= limit:
        st.session_state[key] = attempts
        return False
    attempts.append(now)
    st.session_state[key] = attempts
    return True


def _geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Convertit une adresse texte en coordonnées GPS via Nominatim (OSM).

    Args:
        address: Adresse ou nom de lieu à géocoder.

    Returns:
        Un tuple ``(latitude, longitude)``, ou ``None`` si le géocodage échoue.
    """
    try:
        return geocode_address(address)
    except (GeocodingError, ValueError) as exc:
        logger.warning("Échec de la recherche d'adresse: %s", exc)
        return None


def _render_sidebar() -> dict[str, object]:
    """Affiche les contrôles de la barre latérale et retourne la configuration choisie.

    Returns:
        Un dictionnaire contenant tous les paramètres saisis par l'utilisateur.
    """
    st.sidebar.header("🔧 Paramètres de recherche")

    address = st.sidebar.text_input(
        "📍 Adresse (optionnel)",
        placeholder="Ex: 10 rue de Rivoli, Paris",
        help="Saisissez une adresse pour centrer la carte automatiquement, "
        "ou cliquez directement sur la carte.",
    )
    if st.sidebar.button("🔎 Localiser cette adresse"):
        if not address.strip():
            st.sidebar.warning("Saisissez une adresse avant de lancer la recherche.")
            coords = None
        else:
            coords = _geocode_address(address)
        if coords:
            st.session_state["map_lat"], st.session_state["map_lon"] = coords
            st.sidebar.success(f"Adresse localisée: {coords[0]:.5f}, {coords[1]:.5f}")
        elif address.strip():
            st.sidebar.error("Adresse introuvable, essayez une formulation différente.")

    fuel_type = st.sidebar.selectbox(
        "⛽ Type de carburant", options=SUPPORTED_FUEL_TYPES, index=SUPPORTED_FUEL_TYPES.index("Gazole")
    )
    radius_km = st.sidebar.slider("📏 Rayon de recherche (km)", min_value=1, max_value=50, value=5)

    return {
        "fuel_type": fuel_type,
        "radius_km": radius_km,
    }


def _get_setting(name: str) -> str:
    """Lit un secret Streamlit Cloud, puis retombe sur une variable d'environnement."""
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return str(value or os.getenv(name, "")).strip()


def _render_subscription_form(
    latitude: float,
    longitude: float,
    fuel_type: str,
    radius_km: float,
) -> dict[str, str]:
    """Affiche le formulaire d'inscription Telegram ou Discord."""
    st.divider()
    st.subheader("🔔 Recevoir une alerte quotidienne")
    st.caption(
        "L'abonnement est gratuit et s'arrête automatiquement après la durée choisie. "
        f"Telegram : utilisez @{TELEGRAM_BOT_USERNAME}."
    )

    supabase_url = _get_setting("SUPABASE_URL")
    service_role_key = _get_setting("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        st.info(
            "Les inscriptions sont momentanément indisponibles : configurez "
            "SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY dans les secrets Streamlit."
        )
        return {"channel": "Telegram", "value": ""}

    channel = st.selectbox("Canal de notification", options=list(SUPPORTED_CHANNELS))
    with st.form("daily_subscription_form"):
        if channel == "Telegram":
            st.info(
                f"1. Ouvrez @{TELEGRAM_BOT_USERNAME}. "
                "2. Appuyez sur Démarrer ou envoyez /start. "
                "3. Récupérez votre identifiant numérique avec @userinfobot."
            )
            channel_value = st.text_input(
                "Identifiant de discussion Telegram (Chat ID)",
                placeholder="Ex: 123456789 ou -1001234567890",
                help=f"Cet identifiant doit être associé à @{TELEGRAM_BOT_USERNAME}.",
            )
        else:
            st.info(
                "1. Ouvrez le serveur et le salon Discord destinataires. "
                "2. Modifier le salon > Intégrations > Webhooks. "
                "3. Créez un webhook, copiez son URL complète et collez-la ici."
            )
            channel_value = st.text_input(
                "URL du webhook Discord (salon destinataire)",
                placeholder="https://discord.com/api/webhooks/...",
                type="password",
            )
        duration_days = st.number_input(
            "Durée de l'abonnement (jours)",
            min_value=1,
            max_value=MAX_DURATION_DAYS,
            value=DEFAULT_DURATION_DAYS,
            step=1,
        )
        consent = st.checkbox("J'accepte de recevoir une notification quotidienne sur ce canal.")
        submitted = st.form_submit_button("📲 M'inscrire aux alertes", type="primary")

    if not submitted:
        return {"channel": channel, "value": channel_value}
    if not consent:
        st.error("Veuillez confirmer votre inscription aux notifications.")
        return {"channel": channel, "value": channel_value}
    if not _allow_action(
        "subscription_attempts", SUBSCRIPTION_LIMIT, SUBSCRIPTION_WINDOW_SECONDS
    ):
        st.error("Limite d'inscriptions atteinte pour cette session. Réessayez plus tard.")
        return {"channel": channel, "value": channel_value}

    try:
        client = get_supabase_client(supabase_url, service_role_key)
        create_args = {
            "client": client,
            "latitude": latitude,
            "longitude": longitude,
            "radius_km": radius_km,
            "fuel_type": fuel_type,
            "duration_days": int(duration_days),
            "channel": channel,
        }
        if channel == "Telegram":
            create_args["telegram_chat_id"] = channel_value
        else:
            create_args["discord_webhook_url"] = channel_value
        create_subscription(**create_args)
    except (SubscriptionError, ValueError) as exc:
        st.error(f"Inscription impossible : {exc}")
        return {"channel": channel, "value": channel_value}

    st.success(
        f"Inscription confirmée pour {int(duration_days)} jours. "
        "Vous recevrez le prochain message lors du prochain passage quotidien."
    )
    return {"channel": channel, "value": channel_value}


def _render_map(center_lat: float, center_lon: float, results: Optional[list[StationResult]]) -> dict:
    """Affiche la carte Folium interactive avec le point central et les résultats.

    Args:
        center_lat: Latitude du centre de recherche.
        center_lon: Longitude du centre de recherche.
        results: Résultats de recherche à afficher en marqueurs (optionnel).

    Returns:
        Les données d'interaction renvoyées par ``st_folium`` (dont un
        éventuel clic utilisateur sur la carte).
    """
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=12)
    folium.Marker(
        [center_lat, center_lon],
        tooltip="Point de recherche",
        icon=folium.Icon(color="blue", icon="home"),
    ).add_to(fmap)

    if results:
        for i, station in enumerate(results[:20]):
            color = "green" if i == 0 else "orange"
            folium.Marker(
                [station.latitude, station.longitude],
                tooltip=f"{escape(station.nom)} - {station.prix:.3f} €/L",
                popup=folium.Popup(
                    f"<b>{escape(station.nom)}</b><br>{escape(station.adresse)}, "
                    f"{escape(station.ville)}<br>Prix: {station.prix:.3f} €/L"
                    f"<br>Distance: {station.distance_km:.2f} km",
                    max_width=250,
                ),
                icon=folium.Icon(color=color, icon="tint"),
            ).add_to(fmap)

    return st_folium(fmap, height=480, use_container_width=True)


def _render_results_table(results: list[StationResult]) -> None:
    """Affiche le tableau des résultats triés par prix.

    Args:
        results: Liste des stations correspondant aux critères, triée par prix.
    """
    df = pd.DataFrame([r.to_dict() for r in results])
    df = df.rename(
        columns={
            "nom": "Station",
            "adresse": "Adresse",
            "ville": "Ville",
            "distance_km": "Distance (km)",
            "prix": "Prix (€/L)",
            "derniere_maj": "Dernière MAJ",
        }
    )
    st.dataframe(
        df[["Station", "Adresse", "Ville", "Distance (km)", "Prix (€/L)", "Dernière MAJ"]],
        use_container_width=True,
        hide_index=True,
    )


def _render_env_export(
    lat: float,
    lon: float,
    fuel_type: str,
    radius_km: int,
    notification_url: str | None,
) -> None:
    """Affiche la section d'export de configuration (.env / GitHub Secrets).

    Args:
        lat: Latitude sélectionnée.
        lon: Longitude sélectionnée.
        fuel_type: Type de carburant sélectionné.
        radius_km: Rayon de recherche sélectionné.
        notification_url: URL Apprise construite à partir du canal choisi.
    """
    st.subheader("📤 Export de la configuration")
    st.caption(
        "Ce bloc contient uniquement les paramètres non sensibles. Configurez "
        "NOTIFICATION_URLS séparément dans les **GitHub Secrets** ou le gestionnaire de secrets."
    )

    env_content = (
        f"LATITUDE={lat:.6f}\n"
        f"LONGITUDE={lon:.6f}\n"
        f"RADIUS_KM={radius_km}\n"
        f"FUEL_TYPE={fuel_type}\n"
        "# NOTIFICATION_URLS is intentionally omitted; configure it in a secret manager.\n"
        f"DRY_RUN=false\n"
        f"LOG_LEVEL=INFO\n"
    )

    st.code(env_content, language="bash")
    st.download_button(
        "⬇️ Télécharger le fichier .env",
        data=env_content,
        file_name=".env",
        mime="text/plain",
    )


def main() -> None:
    """Point d'entrée de l'application Streamlit."""
    _init_session_state()

    st.title("⛽ Fuel Price Alert")
    st.markdown(
        "Trouvez la station-service la moins chère autour de vous et recevez une "
        "**alerte quotidienne automatique** — données officielles "
        "[data.economie.gouv.fr](https://data.economie.gouv.fr/)."
    )

    params = _render_sidebar()

    col_map, col_side = st.columns([2, 1])

    with col_map:
        st.subheader("🗺️ Choisissez un point sur la carte")
        map_state = _render_map(
            st.session_state["map_lat"], st.session_state["map_lon"], st.session_state["results"]
        )
        if map_state and map_state.get("last_clicked"):
            try:
                clicked_lat = float(map_state["last_clicked"]["lat"])
                clicked_lon = float(map_state["last_clicked"]["lng"])
                validate_coordinates(clicked_lat, clicked_lon)
            except (KeyError, TypeError, ValueError):
                st.warning("Le point sélectionné sur la carte est invalide.")
            else:
                st.session_state["map_lat"] = clicked_lat
                st.session_state["map_lon"] = clicked_lon

    with col_side:
        st.subheader("📌 Point sélectionné")
        st.metric("Latitude", f"{st.session_state['map_lat']:.5f}")
        st.metric("Longitude", f"{st.session_state['map_lon']:.5f}")
        st.info(f"Rayon: **{params['radius_km']} km** — Carburant: **{params['fuel_type']}**")

        run_clicked = st.button("🚀 Lancer la recherche maintenant", type="primary", use_container_width=True)

    subscription_config = _render_subscription_form(
        latitude=float(st.session_state["map_lat"]),
        longitude=float(st.session_state["map_lon"]),
        fuel_type=str(params["fuel_type"]),
        radius_km=float(params["radius_km"]),
    )

    if run_clicked:
        with st.spinner("Récupération des données en temps réel..."):
            try:
                stations = fetch_stations(
                    latitude=st.session_state["map_lat"],
                    longitude=st.session_state["map_lon"],
                    radius_km=params["radius_km"],
                )
                results = rank_stations(
                    stations,
                    center_lat=st.session_state["map_lat"],
                    center_lon=st.session_state["map_lon"],
                    fuel_type=params["fuel_type"],
                    radius_km=params["radius_km"],
                )
                st.session_state["results"] = results
                st.session_state["last_error"] = None
                if not results:
                    st.session_state["last_error"] = (
                        f"Aucune station proposant du {params['fuel_type']} trouvée "
                        f"dans un rayon de {params['radius_km']} km."
                    )
            except FuelAPIError as exc:
                st.session_state["results"] = None
                st.session_state["last_error"] = f"Erreur API: {exc}"

    if st.session_state["last_error"]:
        st.warning(st.session_state["last_error"])

    results = st.session_state["results"]
    if results:
        st.success(
            f"✅ Top {len(results)} station(s) trouvée(s). La moins chère : "
            f"**{results[0].nom}** à **{results[0].prix:.3f} €/L** "
            f"({results[0].distance_km:.2f} km)."
        )
        _render_results_table(results)

        with st.expander("✉️ Aperçu du message de notification"):
            title, body = build_message(results)
            st.markdown(f"**{title}**")
            st.markdown(body)

        notification_url = None
        try:
            if subscription_config["channel"] == "Telegram":
                notification_url = telegram_notification_url(
                    _get_setting("TELEGRAM_BOT_TOKEN"), subscription_config["value"]
                )
            else:
                notification_url = discord_notification_url(subscription_config["value"])
        except (SubscriptionError, ValueError) as exc:
            st.info(f"Test de notification indisponible : {exc}")

        if notification_url and st.button("🧪 Tester l'envoi de la notification maintenant"):
            if not _allow_action(
                "notification_test_attempts",
                TEST_NOTIFICATION_LIMIT,
                TEST_NOTIFICATION_WINDOW_SECONDS,
            ):
                st.error("Trop de tests d'envoi. Réessayez plus tard.")
                return
            try:
                send_notification(results, [notification_url])
                st.success("Notification envoyée avec succès !")
            except NotificationError as exc:
                st.error(f"Échec de l'envoi: {exc}")

        st.divider()
        _render_env_export(
            lat=st.session_state["map_lat"],
            lon=st.session_state["map_lon"],
            fuel_type=params["fuel_type"],
            radius_km=params["radius_km"],
            notification_url=notification_url,
        )
    else:
        st.caption("👆 Cliquez sur *Lancer la recherche maintenant* pour voir les résultats.")


if __name__ == "__main__":
    main()
