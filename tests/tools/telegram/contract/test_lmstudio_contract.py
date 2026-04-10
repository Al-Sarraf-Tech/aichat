"""Contract tests — validate LM Studio response shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lmstudio_responses.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURES.read_text())


class TestLMStudioContract:
    def test_chat_completion_has_choices(self, fixtures):
        data = fixtures["chat_completion_success"]
        assert "choices" in data
        assert len(data["choices"]) > 0
        choice = data["choices"][0]
        assert "message" in choice
        assert "content" in choice["message"]
        assert "role" in choice["message"]

    def test_error_response_has_message(self, fixtures):
        data = fixtures["chat_completion_error"]
        assert "error" in data
        assert "message" in data["error"]
