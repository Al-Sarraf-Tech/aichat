"""Contract tests — validate Claude stream-json event shapes against fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_stream.jsonl"


@pytest.fixture
def events():
    lines = FIXTURES.read_text().strip().split("\n")
    return [json.loads(line) for line in lines]


class TestClaudeStreamContract:
    def test_tool_use_event_shape(self, events):
        tool_uses = [e for e in events if e.get("type") == "tool_use"]
        assert len(tool_uses) > 0
        for event in tool_uses:
            assert "tool" in event
            assert "tool_input" in event
            assert isinstance(event["tool_input"], dict)

    def test_tool_result_event_shape(self, events):
        results = [e for e in events if e.get("type") == "tool_result"]
        assert len(results) > 0
        for event in results:
            assert "content" in event
            assert isinstance(event["content"], list)
            for block in event["content"]:
                assert "type" in block
                assert "text" in block

    def test_result_event_shape(self, events):
        finals = [e for e in events if e.get("type") == "result"]
        assert len(finals) > 0
        for event in finals:
            assert "result" in event
