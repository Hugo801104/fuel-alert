from src.fetcher import _extract_coordinates, _parse_prix_field, _record_to_station


def test_extract_coordinates_supports_geojson_point() -> None:
    latitude, longitude = _extract_coordinates(
        {"geom": {"type": "Point", "coordinates": [2.3522, 48.8566]}}
    )

    assert latitude == 48.8566
    assert longitude == 2.3522


def test_parse_prix_field_normalizes_supported_shapes() -> None:
    entry = {"nom": "Gazole", "valeur": "1.659"}

    assert _parse_prix_field(entry) == [entry]
    assert _parse_prix_field([entry, "invalid"]) == [entry]
    assert _parse_prix_field(None) == []


def test_record_to_station_ignores_records_without_coordinates() -> None:
    assert _record_to_station({"id": "without-location"}) is None


def test_extract_coordinates_ignores_malformed_coordinates() -> None:
    assert _record_to_station(
        {"id": "bad-location", "geom": {"coordinates": ["not-a-number", 48.0]}}
    ) is None
