from datetime import datetime, timedelta, timezone

from src.analyzer import (
    DEFAULT_RESULT_LIMIT,
    NoStationFoundError,
    find_cheapest_station,
    parse_update_date,
    rank_stations,
)
from src.fetcher import RawStation


def _station(
    station_id: str,
    price: float,
    updated_at: datetime | None,
    *,
    latitude: float = 48.8566,
    longitude: float = 2.3522,
) -> RawStation:
    raw = {"gazole_prix": price}
    if updated_at is not None:
        raw["gazole_maj"] = updated_at.isoformat()
    return RawStation(
        station_id=station_id,
        adresse="1 rue de Test",
        ville="Paris",
        code_postal="75000",
        latitude=latitude,
        longitude=longitude,
        raw=raw,
    )


def test_parse_update_date_accepts_iso_fractional_seconds() -> None:
    parsed = parse_update_date("2026-09-20T12:30:45.123456+00:00")

    assert parsed == datetime(2026, 9, 20, 12, 30, 45, 123456, tzinfo=timezone.utc)


def test_rank_stations_filters_stale_and_missing_dates_and_returns_top_three() -> None:
    now = datetime.now(timezone.utc)
    stations = [
        _station("best", 1.50, now - timedelta(hours=1)),
        _station("second", 1.55, now - timedelta(days=1)),
        _station("third", 1.60, now - timedelta(days=2)),
        _station("fourth", 1.65, now - timedelta(hours=2)),
        _station("stale", 1.10, now - timedelta(days=3)),
        _station("undated", 1.20, None),
    ]

    results = rank_stations(stations, 48.8566, 2.3522, "Gazole", 5)

    assert len(results) == DEFAULT_RESULT_LIMIT
    assert [result.station_id for result in results] == ["best", "second", "third"]


def test_find_cheapest_station_raises_when_all_prices_are_stale() -> None:
    station = _station("stale", 1.50, datetime.now(timezone.utc) - timedelta(days=4))

    try:
        find_cheapest_station([station], 48.8566, 2.3522, "Gazole", 5)
    except NoStationFoundError:
        pass
    else:
        raise AssertionError("Une station avec un prix ancien ne doit pas être retenue")
