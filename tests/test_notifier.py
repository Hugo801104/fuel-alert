import pytest

from src.analyzer import StationResult
from src.notifier import NotificationError, build_message, send_notification


def _result() -> StationResult:
    return StationResult(
        station_id="1",
        nom="Station",
        adresse="1 rue Test",
        ville="Paris",
        code_postal="75001",
        latitude=48.8566,
        longitude=2.3522,
        distance_km=1.2,
        fuel_type="Gazole",
        prix=1.7,
        derniere_maj="2026-09-21T10:00:00+00:00",
        horaires=None,
    )


def test_send_notification_dry_run_does_not_require_a_channel() -> None:
    assert send_notification([_result()], [], dry_run=True) is True


def test_send_notification_rejects_unsupported_channel() -> None:
    with pytest.raises(NotificationError):
        send_notification([_result()], ["http://example.com/hook"])


def test_build_message_escapes_station_text() -> None:
    result = _result()
    result.nom = "Station **frauduleuse**"
    result.adresse = "[adresse](https://example.com)"

    _, body = build_message(result)

    assert "\\*\\*frauduleuse\\*\\*" in body
    assert "\\[adresse\\]" in body


from datetime import datetime, timezone


def _message_result(station_id: str, price: float) -> StationResult:
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
    results = [_message_result("1", 1.50), _message_result("2", 1.55), _message_result("3", 1.60)]

    title, body = build_message(results)

    assert title == "⛽ Top 3 prix Gazole"
    assert "**1. Station 1**" in body
    assert "**2. Station 2**" in body
    assert "**3. Station 3**" in body
    assert body.count("Carte : https://www.openstreetmap.org/") == 3
    assert ")\n" not in body


def test_build_message_keeps_single_result_title() -> None:
    title, _ = build_message(_message_result("1", 1.50))

    assert title == "⛽ Gazole à 1.500 €/L - Station 1"


def test_build_message_omits_hours_and_respects_discord_limit() -> None:
    result = _message_result("1", 1.50)
    result.horaires = {
        "@automate-24-24": "0",
        "jour": [
            {
                "@nom": "Lundi",
                "@ferme": "",
                "horaire": {"@ouverture": "06.00", "@fermeture": "22.00"},
            }
        ],
    }

    _, body = build_message([result, result, result])

    assert "Horaires" not in body
    assert '"@automate-24-24"' not in body
    assert len(body) <= 2000
