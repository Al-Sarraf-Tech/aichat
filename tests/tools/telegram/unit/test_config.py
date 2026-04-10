"""Tests for telegram.config — env loading, defaults, validation."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestConfig:
    def test_loads_all_env_vars(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok-123",
            "TELEGRAM_CHAT_ID": "999",
            "IMAGE_GEN_BASE_URL": "http://lm:1234",
            "TEAM_SSH_HOST": "myhost",
            "TEAM_SSH_PORT": "2222",
            "TEAM_SSH_USER": "admin",
            "TEAM_SSH_KEY": "/keys/id",
            "POSTGRES_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.BOT_TOKEN == "tok-123"
            assert cfg.CHAT_ID == "999"
            assert cfg.LM_STUDIO_URL == "http://lm:1234"
            assert cfg.SSH_HOST == "myhost"
            assert cfg.SSH_PORT == 2222
            assert cfg.SSH_USER == "admin"
            assert cfg.SSH_KEY == "/keys/id"
            assert "secret" in cfg.DB_DSN

    def test_defaults_without_optional_vars(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "1",
            "POSTGRES_PASSWORD": "pw",
        }
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.SSH_HOST == "host.docker.internal"
            assert cfg.SSH_PORT == 1337
            assert cfg.SSH_USER == "jalsarraf"
            assert cfg.LM_STUDIO_URL == "http://192.168.50.2:1234"

    def test_is_configured_true(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.is_configured() is True

    def test_is_configured_false_missing_token(self):
        env = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            assert cfg.is_configured() is False

    def test_redacted_summary(self):
        env = {"TELEGRAM_BOT_TOKEN": "secret-token-value", "TELEGRAM_CHAT_ID": "1", "POSTGRES_PASSWORD": "pw"}
        with patch.dict(os.environ, env, clear=False):
            from tools.telegram import config as cfg
            import importlib
            importlib.reload(cfg)
            summary = cfg.summary()
            assert "secret-token-value" not in summary
            assert "***" in summary
            assert "1" in summary
