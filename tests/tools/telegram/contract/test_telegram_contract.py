"""Contract tests — validate Telegram API response shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "telegram_responses.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURES.read_text())


class TestTelegramContract:
    def test_send_message_has_required_fields(self, fixtures):
        data = fixtures["sendMessage_success"]
        assert data["ok"] is True
        result = data["result"]
        assert "message_id" in result
        assert "chat" in result
        assert "id" in result["chat"]

    def test_get_updates_has_required_fields(self, fixtures):
        data = fixtures["getUpdates_success"]
        assert data["ok"] is True
        updates = data["result"]
        assert len(updates) > 0
        update = updates[0]
        assert "update_id" in update
        assert "message" in update
        msg = update["message"]
        assert "message_id" in msg
        assert "chat" in msg
        assert "text" in msg

    def test_rate_limit_has_retry_after(self, fixtures):
        data = fixtures["rate_limit_429"]
        assert data["ok"] is False
        assert "parameters" in data
        assert "retry_after" in data["parameters"]
        assert isinstance(data["parameters"]["retry_after"], int)
