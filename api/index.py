"""Vercel serverless entry point for the fuel-price search API."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.analyzer import SUPPORTED_FUEL_TYPES, rank_stations
from src.fetcher import FuelAPIError, fetch_stations
from src.security import validate_coordinates, validate_radius

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    """Handle Vercel requests without starting the Streamlit application."""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        expected_api_key = os.getenv("VERCEL_API_KEY")
        if expected_api_key and self.headers.get("X-API-Key") != expected_api_key:
            self._respond({"error": "Unauthorized"}, 401)
            return

        query = parse_qs(urlparse(self.path).query)
        if urlparse(self.path).path.rstrip("/") not in {"", "/api", "/api/search"}:
            self._respond({"error": "Not found"}, 404)
            return

        if urlparse(self.path).path.rstrip("/") in {"", "/api"} and not query:
            self._respond(
                {
                    "service": "fuel-price-alert",
                    "status": "ok",
                    "endpoint": "/api/search",
                }
            )
            return

        try:
            latitude = float(self._required(query, "latitude"))
            longitude = float(self._required(query, "longitude"))
            radius_km = float(query.get("radius_km", ["5"])[0])
            fuel_type = query.get("fuel_type", ["Gazole"])[0]
            validate_coordinates(latitude, longitude)
            validate_radius(radius_km)
            if fuel_type not in SUPPORTED_FUEL_TYPES:
                raise ValueError(f"fuel_type doit être l'un de: {', '.join(SUPPORTED_FUEL_TYPES)}")

            stations = fetch_stations(latitude, longitude, radius_km)
            results = rank_stations(stations, latitude, longitude, fuel_type, radius_km)
            self._respond({"results": [result.to_dict() for result in results]})
        except ValueError as exc:
            self._respond({"error": str(exc)}, 400)
        except FuelAPIError as exc:
            logger.error("Erreur API Vercel: %s", exc)
            self._respond({"error": "La source de données est temporairement indisponible."}, 502)
        except Exception:
            logger.exception("Erreur inattendue du handler Vercel")
            self._respond({"error": "Erreur interne du serveur."}, 500)

    @staticmethod
    def _required(query: dict[str, list[str]], name: str) -> str:
        value = query.get(name, [""])[0].strip()
        if not value:
            raise ValueError(f"Paramètre obligatoire manquant: {name}")
        return value

    def _respond(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
