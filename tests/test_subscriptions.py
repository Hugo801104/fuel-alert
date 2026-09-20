from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.subscriptions import (
    create_subscription,
    record_notification,
    telegram_notification_url,
    validate_telegram_chat_id,
)


class _FakeTable:
    def __init__(self) -> None:
        self.inserted: list[dict[str, object]] = []
        self.updated: dict[str, object] | None = None

    def insert(self, payload: dict[str, object]) -> "_FakeTable":
        self.inserted.append(payload)
        return self

    def update(self, payload: dict[str, object]) -> "_FakeTable":
        self.updated = payload
        return self

    def eq(self, *_args: object) -> "_FakeTable":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[{"id": "subscription-1"}])


class _FakeClient:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTable] = {}

    def table(self, name: str) -> _FakeTable:
        self.tables.setdefault(name, _FakeTable())
        return self.tables[name]


def test_validate_telegram_chat_id_accepts_private_and_group_ids() -> None:
    assert validate_telegram_chat_id(" -1001234567890 ") == "-1001234567890"


def test_validate_telegram_chat_id_rejects_non_numeric_values() -> None:
    with pytest.raises(ValueError):
        validate_telegram_chat_id("@fuel_alert")


def test_create_subscription_sets_expiration_and_first_run() -> None:
    client = _FakeClient()
    started_at = datetime(2026, 9, 20, tzinfo=timezone.utc)

    result = create_subscription(
        client,
        latitude=48.8566,
        longitude=2.3522,
        radius_km=5,
        fuel_type="Gazole",
        telegram_chat_id="123456789",
        duration_days=10,
        now=started_at,
    )

    payload = client.tables["subscriptions"].inserted[0]
    assert result["id"] == "subscription-1"
    assert payload["telegram_chat_id"] == "123456789"
    assert payload["next_run_at"] == started_at.isoformat()
    assert payload["expires_at"] == "2026-09-30T00:00:00+00:00"


def test_record_notification_deactivates_after_last_send() -> None:
    client = _FakeClient()
    sent_at = datetime(2026, 9, 20, tzinfo=timezone.utc)

    record_notification(
        client,
        subscription_id="subscription-1",
        sent_count=9,
        duration_days=10,
        sent_at=sent_at,
    )

    assert client.tables["subscriptions"].updated == {
        "sent_count": 10,
        "last_sent_at": sent_at.isoformat(),
        "next_run_at": "2026-09-21T00:00:00+00:00",
        "active": False,
    }


def test_telegram_notification_url_contains_bot_and_chat_id() -> None:
    assert telegram_notification_url("123:token", "456") == "tgram://123:token/456"