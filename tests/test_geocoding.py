import pytest

from src.geocoding import geocode_address, normalize_address


class _Location:
    latitude = 48.8566
    longitude = 2.3522


class _Geocoder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def geocode(self, query: str, **kwargs: object) -> _Location:
        self.calls.append((query, kwargs))
        return _Location()


def test_normalize_address_collapses_whitespace() -> None:
    assert normalize_address("  10 rue de Rivoli,\n Paris  ") == "10 rue de Rivoli, Paris"


def test_normalize_address_rejects_empty_and_oversized_values() -> None:
    with pytest.raises(ValueError):
        normalize_address("   ")
    with pytest.raises(ValueError):
        normalize_address("x" * 201)


def test_geocode_address_sends_bounded_french_query() -> None:
    geocoder = _Geocoder()

    result = geocode_address(" 10 rue de Rivoli, Paris ", geocoder=geocoder)

    assert result == (48.8566, 2.3522)
    assert geocoder.calls == [
        (
            "10 rue de Rivoli, Paris",
            {
                "exactly_one": True,
                "addressdetails": False,
                "countrycodes": "fr",
                "timeout": 10,
            },
        )
    ]


def test_geocode_address_returns_none_for_unknown_address() -> None:
    class EmptyGeocoder:
        def geocode(self, *_args: object, **_kwargs: object) -> None:
            return None

    assert geocode_address("Adresse inconnue", geocoder=EmptyGeocoder()) is None
