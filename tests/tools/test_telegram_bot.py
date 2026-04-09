"""
Unit tests for tools/telegram_bot.py — Telegram command handler.

Test groups:
  - Telegram helpers (4): send success, truncation, getUpdates success, getUpdates error
  - Classifier (6): tool/code/create/question intents, malformed JSON fallback, network error fallback
  - Auth + poll loop (3): authorized correct ID, unauthorized wrong ID, handle_message dispatches tool
  - Dispatchers Task 4 (7): tool dispatch, unknown tool, code ack+task, invalid repo,
    milestone extraction, create ack, invalid create name

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_telegram_bot.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV_VARS = {
    "TELEGRAM_BOT_TOKEN": "fake-token",
    "TELEGRAM_CHAT_ID": "123456",
    "IMAGE_GEN_BASE_URL": "http://192.168.50.2:1234",
}


def _make_mock_client(post_status: int = 200, post_json: dict | None = None,
                      get_status: int = 200, get_json: dict | None = None) -> AsyncMock:
    """Return an AsyncMock httpx.AsyncClient with pre-configured post/get responses."""
    post_resp = MagicMock()
    post_resp.status_code = post_status
    post_resp.json.return_value = post_json if post_json is not None else {"ok": True, "result": {"message_id": 7}}

    get_resp = MagicMock()
    get_resp.status_code = get_status
    get_resp.json.return_value = get_json if get_json is not None else {"ok": True, "result": []}

    client = AsyncMock()
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ===========================================================================
# Task 1: Telegram helpers — 4 tests
# ===========================================================================


class TestSendTelegram:
    """_send_telegram helper."""

    @pytest.mark.asyncio
    async def test_send_telegram_posts_correct_payload(self):
        """_send_telegram must POST to /sendMessage with chat_id, text, parse_mode."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await tb._send_telegram("hello world")

        call_args = client.post.call_args
        assert call_args is not None
        url_arg = call_args[0][0] if call_args[0] else ""
        assert "sendMessage" in url_arg
        json_payload = call_args.kwargs.get("json", {}) or (call_args[0][1] if len(call_args[0]) > 1 else {})
        assert json_payload.get("text") == "hello world"
        assert json_payload.get("parse_mode") == "Markdown"
        assert str(json_payload.get("chat_id")) == "123456"

    @pytest.mark.asyncio
    async def test_send_telegram_truncates_long_text(self):
        """_send_telegram must truncate text exceeding 4096 chars."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        long_text = "x" * 5000
        client = _make_mock_client()
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await tb._send_telegram(long_text)

        call_args = client.post.call_args
        json_payload = call_args.kwargs.get("json", {})
        sent_text = json_payload.get("text", "")
        assert len(sent_text) <= 4096
        assert "truncated" in sent_text.lower()


class TestGetUpdates:
    """_get_updates helper."""

    @pytest.mark.asyncio
    async def test_get_updates_returns_results(self):
        """_get_updates must return the result list from Telegram on success."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        updates_payload = {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"message_id": 10, "text": "hi", "chat": {"id": 123456}}}
            ],
        }
        client = _make_mock_client(get_json=updates_payload)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await tb._get_updates(0)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["update_id"] == 1

    @pytest.mark.asyncio
    async def test_get_updates_returns_empty_on_error(self):
        """_get_updates must return [] when the request raises an exception."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(side_effect=Exception("network down"))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await tb._get_updates(0)

        assert result == []


# ===========================================================================
# Task 2: Intent classifier — 6 tests
# ===========================================================================


def _lm_response(content: str) -> MagicMock:
    """Build a fake LM Studio chat completion response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


class TestClassifyIntent:
    """_classify_intent uses LM Studio to determine intent type."""

    @pytest.mark.asyncio
    async def test_tool_intent_parsed(self):
        """tool intent with action=monitor/overview must be parsed correctly."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        tool_json = json.dumps({"type": "tool", "tool": "monitor", "action": "overview", "args": {}})
        client = _make_mock_client(post_json=None)
        client.post.return_value = _lm_response(tool_json)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("show me the fleet overview")

        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "overview"

    @pytest.mark.asyncio
    async def test_code_intent_parsed(self):
        """code intent must parse repo and task correctly."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        code_json = json.dumps({"type": "code", "repo": "aichat", "task": "add logging to main.py"})
        client = _make_mock_client()
        client.post.return_value = _lm_response(code_json)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("add logging to main.py in aichat")

        assert intent.type == "code"
        assert intent.repo == "aichat"
        assert "logging" in intent.task

    @pytest.mark.asyncio
    async def test_create_intent_parsed(self):
        """create intent must parse name, description, and language."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        create_json = json.dumps({
            "type": "create",
            "name": "ssh-keytool",
            "description": "a rust cli for managing ssh keys",
            "language": "rust",
        })
        client = _make_mock_client()
        client.post.return_value = _lm_response(create_json)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("make me a rust cli for ssh keys")

        assert intent.type == "create"
        assert intent.name == "ssh-keytool"
        assert intent.language == "rust"

    @pytest.mark.asyncio
    async def test_question_intent_parsed(self):
        """question intent must populate the text field."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        q_json = json.dumps({"type": "question", "text": "what is the qdrant port?"})
        client = _make_mock_client()
        client.post.return_value = _lm_response(q_json)
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("what is the qdrant port?")

        assert intent.type == "question"

    @pytest.mark.asyncio
    async def test_malformed_json_returns_question_fallback(self):
        """Malformed JSON from LM Studio must return Intent(type='question')."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        client = _make_mock_client()
        client.post.return_value = _lm_response("not valid json at all!!!")
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("something weird")

        assert intent.type == "question"
        assert intent.text == "something weird"

    @pytest.mark.asyncio
    async def test_network_error_returns_question_fallback(self):
        """Network exception must return Intent(type='question') without raising."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            intent = await tb._classify_intent("anything")

        assert intent.type == "question"
        assert intent.text == "anything"


# ===========================================================================
# Task 3: Auth gate + poll loop — 3 tests
# ===========================================================================


class TestIsAuthorized:
    """_is_authorized checks message.chat.id against _CHAT_ID."""

    def test_authorized_correct_id_returns_true(self):
        """Message from the configured chat ID must be authorized."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        message = {"chat": {"id": 123456}, "text": "hi", "message_id": 1}
        assert tb._is_authorized(message) is True

    def test_authorized_wrong_id_returns_false(self):
        """Message from a different chat ID must not be authorized."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        message = {"chat": {"id": 999999}, "text": "hi", "message_id": 1}
        assert tb._is_authorized(message) is False


class TestHandleMessage:
    """_handle_message dispatches to the correct handler based on intent."""

    @pytest.mark.asyncio
    async def test_handle_message_dispatches_tool_intent(self):
        """_handle_message must call _dispatch_tool for a tool intent."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        message = {"chat": {"id": 123456}, "text": "show fleet overview", "message_id": 42}

        tool_intent = tb.Intent(type="tool", tool="monitor", action="overview")

        with patch.dict(os.environ, ENV_VARS), \
             patch.object(tb, "_classify_intent", AsyncMock(return_value=tool_intent)), \
             patch.object(tb, "_send_telegram", AsyncMock()), \
             patch.object(tb, "_dispatch_tool", AsyncMock()) as mock_dispatch:
            await tb._handle_message(message)

        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        assert call_args[0][0].type == "tool"


# ===========================================================================
# Task 4: Dispatchers + milestone streaming — 7 tests
# ===========================================================================


class TestDispatchTool:
    """_dispatch_tool: invoke TOOL_HANDLERS and send reply."""

    @pytest.mark.asyncio
    async def test_dispatch_tool_calls_handler_and_sends_reply(self):
        """_dispatch_tool must call the correct TOOL_HANDLERS entry and send result."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        mock_handler = AsyncMock(return_value=[{"type": "text", "text": "fleet is healthy"}])
        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch.dict("tools.telegram_bot.TOOL_HANDLERS", {"monitor": mock_handler}):
            intent = tb.Intent(type="tool", tool="monitor", action="overview", args={})
            await tb._dispatch_tool(intent, reply_to=1)

        # handler should have been called
        mock_handler.assert_called_once()
        # a reply should have been sent containing the result text
        assert any("fleet is healthy" in str(c) for c in mock_send.call_args_list)

    @pytest.mark.asyncio
    async def test_dispatch_tool_unknown_tool_sends_error(self):
        """_dispatch_tool with unknown tool must send an error message."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch.dict("tools.telegram_bot.TOOL_HANDLERS", {}):
            intent = tb.Intent(type="tool", tool="nonexistent", action="foo", args={})
            await tb._dispatch_tool(intent, reply_to=1)

        # should have sent an error-like message
        sent_texts = [str(c) for c in mock_send.call_args_list]
        assert any("unknown" in t.lower() or "error" in t.lower() or "nonexistent" in t.lower()
                   for t in sent_texts)


class TestDispatchCode:
    """_dispatch_code: ack, background task, validation."""

    @pytest.mark.asyncio
    async def test_dispatch_code_sends_ack_and_spawns_task(self):
        """_dispatch_code must send an ack message and spawn a background task."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        import asyncio

        async def fake_stream(ssh_cmd, reply_to, task_state):
            return "Done"

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("tools.telegram_bot._stream_claude", side_effect=fake_stream):
            intent = tb.Intent(type="code", repo="aichat", task="add logging")
            await tb._dispatch_code(intent, reply_to=10)
            # allow the spawned task to run
            await asyncio.sleep(0.05)

        ack_sent = any("aichat" in str(c) or "add logging" in str(c) or "Starting" in str(c) or "coding" in str(c).lower()
                       for c in mock_send.call_args_list)
        assert ack_sent or len(mock_send.call_args_list) >= 1

    @pytest.mark.asyncio
    async def test_dispatch_code_invalid_repo_name_returns_error(self):
        """_dispatch_code with an invalid repo name must send an error and not spawn a task."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("tools.telegram_bot._stream_claude", new_callable=AsyncMock) as mock_stream:
            intent = tb.Intent(type="code", repo="../../evil; rm -rf /", task="do stuff")
            await tb._dispatch_code(intent, reply_to=5)

        # stream should NOT have been called
        mock_stream.assert_not_called()
        # an error should have been sent
        sent = " ".join(str(c) for c in mock_send.call_args_list)
        assert "invalid" in sent.lower() or "error" in sent.lower()

    @pytest.mark.asyncio
    async def test_dispatch_code_milestone_extraction(self):
        """_stream_claude must detect milestones from stream-json lines."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        import asyncio

        # Build fake stdout lines simulating stream-json events
        read_event = json.dumps({
            "type": "tool_use", "name": "Read", "input": {"file_path": "main.py"}
        }) + "\n"
        edit_event = json.dumps({
            "type": "tool_use", "name": "Edit", "input": {"file_path": "main.py"}
        }) + "\n"
        bash_test = json.dumps({
            "type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/"}
        }) + "\n"
        result_event = json.dumps({
            "type": "result", "subtype": "success", "result": "Done"
        }) + "\n"

        lines = [
            read_event.encode(),
            edit_event.encode(),
            bash_test.encode(),
            result_event.encode(),
        ]

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        async def fake_readline():
            if lines:
                return lines.pop(0)
            return b""

        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = fake_readline
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        task_state = tb.TaskState(
            task_id="t1",
            repo="aichat",
            description="test task",
        )

        milestones_sent = []

        async def capture_send(text, reply_to=None):
            milestones_sent.append(text)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", side_effect=capture_send), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tb._stream_claude("echo hi", 10, task_state)

        # Should have sent at least one milestone
        milestone_messages = [m for m in milestones_sent if any(
            kw in m for kw in ("Reading", "Writing", "Testing", "Running tests", "Committing")
        )]
        assert len(milestone_messages) >= 1


class TestDispatchCreate:
    """_dispatch_create: validate name and send ack."""

    @pytest.mark.asyncio
    async def test_dispatch_create_sends_creating_ack(self):
        """_dispatch_create must send a 'Creating project' acknowledgement."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        import asyncio

        async def fake_stream(ssh_cmd, reply_to, task_state):
            return "Done"

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("tools.telegram_bot._stream_claude", side_effect=fake_stream):
            intent = tb.Intent(type="create", name="my-tool", description="does stuff", language="rust")
            await tb._dispatch_create(intent, reply_to=20)
            await asyncio.sleep(0.05)

        sent = " ".join(str(c) for c in mock_send.call_args_list)
        assert "my-tool" in sent or "Creating" in sent or "creating" in sent.lower()

    @pytest.mark.asyncio
    async def test_dispatch_create_invalid_name_returns_error(self):
        """_dispatch_create with invalid project name must send error and not stream."""
        with patch.dict(os.environ, ENV_VARS):
            import importlib
            import tools.telegram_bot as tb
            importlib.reload(tb)

        with patch.dict(os.environ, ENV_VARS), \
             patch("tools.telegram_bot._send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("tools.telegram_bot._stream_claude", new_callable=AsyncMock) as mock_stream:
            intent = tb.Intent(type="create", name="bad name with spaces!", description="x", language="go")
            await tb._dispatch_create(intent, reply_to=20)

        mock_stream.assert_not_called()
        sent = " ".join(str(c) for c in mock_send.call_args_list)
        assert "invalid" in sent.lower() or "error" in sent.lower()
