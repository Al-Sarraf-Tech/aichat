"""Tests for telegram.models — dataclass construction and defaults."""
from __future__ import annotations

from tools.telegram.models import ConversationMessage, Intent, TaskState


class TestIntent:
    def test_defaults(self):
        i = Intent(type="question")
        assert i.type == "question"
        assert i.tool == ""
        assert i.repo is None
        assert i.args == {}

    def test_tool_intent(self):
        i = Intent(type="tool", tool="monitor", action="thermals", args={"host": "amarillo"})
        assert i.tool == "monitor"
        assert i.action == "thermals"
        assert i.args == {"host": "amarillo"}


class TestTaskState:
    def test_defaults(self):
        ts = TaskState(task_id="abc12345", description="fix bug")
        assert ts.status == "running"
        assert ts.repo is None
        assert ts.asyncio_task is None
        assert ts.process is None
        assert ts.started_at > 0


class TestConversationMessage:
    def test_construction(self):
        msg = ConversationMessage(chat_id=123, role="user", content="hello")
        assert msg.chat_id == 123
        assert msg.message_id is None
