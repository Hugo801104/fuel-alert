from datetime import datetime, timezone

from src.analyzer import StationResult
from src.notifier import build_message


def _result(station_id: str, price: float) -> StationResult:
    return StationResult(
        station_id=station_id,
        nom=f"Station {station_id}",
        adresse="1 rue de Test",
        ville="Paris",
        code_postal="75000",
        latitude=48.8566,
        longitude=2.3522,
        distance_km=1.0,
        fuel_type="Gazole",
        prix=price,
        derniere_maj=datetime.now(timezone.utc).isoformat(),
        horaires=None,
    )


def test_build_message_lists_all_three_results() -> None:
    results = [_result("1", 1.50), _result("2", 1.55), _result("3", 1.60)]

    title, body = build_message(results)

    assert title == "⛽ Top 3 prix Gazole"
    assert "**1. Station 1**" in body
    assert "**2. Station 2**" in body
    assert "**3. Station 3**" in body
    assert body.count("Voir sur la carte") == 3


def test_build_message_keeps_single_result_title() -> None:
    title, _ = build_message(_result("1", 1.50))

    assert title == "⛽ Gazole à 1.500 €/L - Station 1"
