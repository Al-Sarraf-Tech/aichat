"""Shared fixtures for telegram tests — fake servers, DB helpers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

TELEGRAM_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "123",
    "IMAGE_GEN_BASE_URL": "http://localhost:9999",
    "POSTGRES_PASSWORD": "testpw",
    "TEAM_SSH_HOST": "localhost",
    "TEAM_SSH_PORT": "22",
    "TEAM_SSH_USER": "test",
    "TEAM_SSH_KEY": "/dev/null",
}


@pytest.fixture
def telegram_env():
    with patch.dict(os.environ, TELEGRAM_ENV, clear=False):
        from tools.telegram import config
        import importlib
        importlib.reload(config)
        yield TELEGRAM_ENV
