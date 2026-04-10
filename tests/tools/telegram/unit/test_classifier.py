"""Tests for telegram.classifier — regex patterns and LLM fallback."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}


def _lm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


@pytest.mark.asyncio
class TestRegexPatterns:
    async def test_status_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("status")
        assert intent.type == "status"

    async def test_status_slash_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("/status")
        assert intent.type == "status"

    async def test_cancel_command(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("cancel")
        assert intent.type == "cancel"

    async def test_check_thermals(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("check thermals")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "thermals"

    async def test_show_containers(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("show containers")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "container"

    async def test_fleet_overview(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("how's the fleet")
        assert intent.type == "tool"
        assert intent.tool == "monitor"
        assert intent.action == "overview"

    async def test_tail_logs(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("tail logs for mcp")
        assert intent.type == "tool"
        assert intent.tool == "log"
        assert intent.action == "tail"
        assert intent.args.get("service") == "mcp"

    async def test_git_status(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("git status in aichat")
        assert intent.type == "tool"
        assert intent.tool == "git"
        assert intent.action == "status"
        assert intent.args.get("repo") == "aichat"

    async def test_list_devices(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("list devices")
        assert intent.type == "tool"
        assert intent.tool == "iot"
        assert intent.action == "list_devices"

    async def test_case_insensitive(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            intent = await classify("CHECK THERMALS")
        assert intent.type == "tool"
        assert intent.tool == "monitor"


@pytest.mark.asyncio
class TestLLMFallback:
    async def test_ambiguous_routes_to_llm(self):
        lm_resp = _lm_response(json.dumps({"type": "tool", "tool": "monitor", "action": "overview"}))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("something seems off with the servers")

        assert intent.type == "tool"
        client.post.assert_awaited_once()

    async def test_llm_returns_code_intent(self):
        lm_resp = _lm_response(json.dumps({"type": "code", "repo": "aichat", "task": "fix the bug"}))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("fix the authentication bug in aichat")

        assert intent.type == "code"
        assert intent.repo == "aichat"

    async def test_malformed_json_falls_back_to_question(self):
        lm_resp = _lm_response("not valid json {{{")
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("blah blah blah")

        assert intent.type == "question"

    async def test_network_error_falls_back_to_question(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("connection refused"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("something weird")

        assert intent.type == "question"

    async def test_create_intent(self):
        lm_resp = _lm_response(json.dumps({
            "type": "create", "name": "myapp", "language": "python",
            "description": "a web app"
        }))
        client = AsyncMock()
        client.post = AsyncMock(return_value=lm_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.classifier import classify
            with patch("tools.telegram.classifier.httpx.AsyncClient", return_value=client):
                intent = await classify("create a new python web app called myapp")

        assert intent.type == "create"
        assert intent.name == "myapp"
        assert intent.language == "python"
