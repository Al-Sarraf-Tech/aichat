"""Centralized configuration — single source of truth for all env vars."""
from __future__ import annotations

import os

# Telegram
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# LM Studio
LM_STUDIO_URL: str = os.environ.get("IMAGE_GEN_BASE_URL", "http://192.168.50.2:1234")
LM_STUDIO_MODEL: str = os.environ.get("TELEGRAM_LM_MODEL", "gemma-4-e2b-it")

# SSH (for Claude Code dispatch)
SSH_HOST: str = os.environ.get("TEAM_SSH_HOST", "host.docker.internal")
SSH_PORT: int = int(os.environ.get("TEAM_SSH_PORT", "1337"))
SSH_USER: str = os.environ.get("TEAM_SSH_USER", "jalsarraf")
SSH_KEY: str = os.environ.get("TEAM_SSH_KEY", "/app/.ssh/team_key")

# Postgres
_pg_password: str = os.environ.get("POSTGRES_PASSWORD", "")
DB_DSN: str = os.environ.get(
    "TELEGRAM_DB_DSN",
    f"postgresql://aichat:{_pg_password}@aichat-db:5432/aichat",
)

# Limits
MAX_MSG_LEN: int = 4096
CLAUDE_TIMEOUT: int = 600
HEARTBEAT_INTERVAL: int = 90
LM_CLASSIFY_TIMEOUT: int = 15
LM_QA_TIMEOUT: int = 20


def is_configured() -> bool:
    return bool(BOT_TOKEN) and bool(CHAT_ID)


def summary() -> str:
    redacted = "***" if BOT_TOKEN else "(empty)"
    return (
        f"bot_token={redacted} chat_id={CHAT_ID} "
        f"lm_studio={LM_STUDIO_URL} model={LM_STUDIO_MODEL} "
        f"ssh={SSH_USER}@{SSH_HOST}:{SSH_PORT}"
    )
