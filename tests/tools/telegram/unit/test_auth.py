"""Tests for telegram.auth — authorization gate."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "12345", "POSTGRES_PASSWORD": "pw"}


class TestAuth:
    def test_authorized_correct_id(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 12345}}
            assert is_authorized(msg) is True

    def test_unauthorized_wrong_id(self):
        with patch.dict(os.environ, ENV, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 99999}}
            assert is_authorized(msg) is False

    def test_unauthorized_missing_chat_id_config(self):
        env = {**ENV, "TELEGRAM_CHAT_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config
            import importlib
            importlib.reload(config)
            from tools.telegram.auth import is_authorized
            msg = {"chat": {"id": 12345}}
            assert is_authorized(msg) is False
