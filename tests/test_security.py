import pytest

from src.security import (
    validate_coordinates,
    validate_notification_url,
    validate_radius,
    validate_smtp_url,
)


@pytest.mark.parametrize(
    "latitude,longitude",
    [(float("nan"), 2.0), (91.0, 2.0), (48.0, 181.0), (48.0, float("inf"))],
)
def test_validate_coordinates_rejects_invalid_values(latitude: float, longitude: float) -> None:
    with pytest.raises(ValueError):
        validate_coordinates(latitude, longitude)


@pytest.mark.parametrize("radius", [0, -1, 50.1, float("nan"), float("inf")])
def test_validate_radius_rejects_unbounded_values(radius: float) -> None:
    with pytest.raises(ValueError):
        validate_radius(radius)


def test_validate_coordinates_accepts_wgs84_bounds() -> None:
    validate_coordinates(90.0, -180.0)


def test_validate_radius_accepts_maximum() -> None:
    validate_radius(50.0)


def test_validate_smtp_url_rejects_local_targets() -> None:
    with pytest.raises(ValueError):
        validate_smtp_url("mailtos://user:password@127.0.0.1?to=user@example.com")


@pytest.mark.parametrize("url", ["http://example.com/hook", "file:///etc/passwd", "discord://"])
def test_validate_notification_url_allows_only_complete_supported_channels(url: str) -> None:
    with pytest.raises(ValueError):
        validate_notification_url(url)


def test_validate_notification_url_accepts_apprise_channels() -> None:
    validate_notification_url("tgram://123:token/456")
    validate_notification_url("discord://123/token")