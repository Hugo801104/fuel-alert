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
from html import escape
from typing import Optional

import folium
import pandas as pd
import streamlit as st
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium

from src.analyzer import (
    SUPPORTED_FUEL_TYPES,
    NoStationFoundError,
    StationResult,
    rank_stations,
)
from src.fetcher import FuelAPIError, fetch_stations
from src.notifier import NotificationError, build_message, send_notification

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

NOTIFICATION_TEMPLATES: dict[str, str] = {
    "Telegram": "tgram://{bot_token}/{chat_id}",
    "Discord": "discord://{webhook_id}/{webhook_token}",
    "Slack": "slack://{token_a}/{token_b}/{token_c}",
    "E-mail (SMTP)": "mailtos://{user}:{password}@{smtp_host}?to={destination_email}",
}


def _init_session_state() -> None:
    """Initialise les valeurs par défaut du ``st.session_state``."""
    defaults = {
        "map_lat": DEFAULT_LAT,
        "map_lon": DEFAULT_LON,
        "results": None,
        "last_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Convertit une adresse texte en coordonnées GPS via Nominatim (OSM).

    Args:
        address: Adresse ou nom de lieu à géocoder.

    Returns:
        Un tuple ``(latitude, longitude)``, ou ``None`` si le géocodage échoue.
    """
    try:
        geolocator = Nominatim(user_agent="fuel-price-alert-app")
        location = geolocator.geocode(address, timeout=10)
        if location:
            return location.latitude, location.longitude
    except Exception:  # noqa: BLE001 - le géocodage peut échouer de multiples façons
        logger.exception("Échec du géocodage pour l'adresse: %s", address)
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
    if st.sidebar.button("🔎 Localiser cette adresse") and address:
        coords = _geocode_address(address)
        if coords:
            st.session_state["map_lat"], st.session_state["map_lon"] = coords
            st.sidebar.success(f"Adresse localisée: {coords[0]:.5f}, {coords[1]:.5f}")
        else:
            st.sidebar.error("Adresse introuvable, essayez une formulation différente.")

    fuel_type = st.sidebar.selectbox(
        "⛽ Type de carburant", options=SUPPORTED_FUEL_TYPES, index=SUPPORTED_FUEL_TYPES.index("Gazole")
    )
    radius_km = st.sidebar.slider("📏 Rayon de recherche (km)", min_value=1, max_value=50, value=5)

    st.sidebar.header("📢 Canal de notification")
    channel = st.sidebar.selectbox("Type de canal", options=list(NOTIFICATION_TEMPLATES.keys()))

    channel_values: dict[str, str] = {}
    if channel == "Telegram":
        channel_values["bot_token"] = st.sidebar.text_input("Bot Token", type="password")
        channel_values["chat_id"] = st.sidebar.text_input("Chat ID")
    elif channel == "Discord":
        channel_values["webhook_id"] = st.sidebar.text_input("Webhook ID")
        channel_values["webhook_token"] = st.sidebar.text_input("Webhook Token", type="password")
    elif channel == "Slack":
        channel_values["token_a"] = st.sidebar.text_input("Token A", type="password")
        channel_values["token_b"] = st.sidebar.text_input("Token B", type="password")
        channel_values["token_c"] = st.sidebar.text_input("Token C", type="password")
    elif channel == "E-mail (SMTP)":
        channel_values["user"] = st.sidebar.text_input("Utilisateur SMTP")
        channel_values["password"] = st.sidebar.text_input("Mot de passe SMTP", type="password")
        channel_values["smtp_host"] = st.sidebar.text_input("Hôte SMTP", placeholder="smtp.gmail.com")
        channel_values["destination_email"] = st.sidebar.text_input("E-mail destinataire")

    return {
        "fuel_type": fuel_type,
        "radius_km": radius_km,
        "channel": channel,
        "channel_values": channel_values,
    }


def _build_notification_url(channel: str, values: dict[str, str]) -> str:
    """Construit l'URL Apprise à partir du canal choisi et des valeurs saisies.

    Args:
        channel: Nom du canal (clé de :data:`NOTIFICATION_TEMPLATES`).
        values: Valeurs saisies par l'utilisateur pour ce canal.

    Returns:
        L'URL Apprise formatée. Peut contenir des placeholders vides si
        l'utilisateur n'a pas encore rempli tous les champs.
    """
    template = NOTIFICATION_TEMPLATES[channel]
    safe_values = {k: (v or f"<{k}>") for k, v in values.items()}
    return template.format(**safe_values)


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
    notification_url: str,
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
            st.session_state["map_lat"] = map_state["last_clicked"]["lat"]
            st.session_state["map_lon"] = map_state["last_clicked"]["lng"]

    with col_side:
        st.subheader("📌 Point sélectionné")
        st.metric("Latitude", f"{st.session_state['map_lat']:.5f}")
        st.metric("Longitude", f"{st.session_state['map_lon']:.5f}")
        st.info(f"Rayon: **{params['radius_km']} km** — Carburant: **{params['fuel_type']}**")

        run_clicked = st.button("🚀 Lancer la recherche maintenant", type="primary", use_container_width=True)

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

        notification_url = _build_notification_url(params["channel"], params["channel_values"])
        if st.button("🧪 Tester l'envoi de la notification maintenant"):
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
