"""Postgres persistence — conversation history and task audit trail via asyncpg."""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from tools.telegram import config

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

_SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    message_id  INT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tg_messages_chat
    ON telegram_messages(chat_id, created_at DESC);
"""

_SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS telegram_tasks (
    task_id     TEXT PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    intent_type TEXT NOT NULL,
    repo        TEXT,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    files       TEXT[],
    commit_sha  TEXT,
    exit_code   INT,
    summary     TEXT,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tg_tasks_chat
    ON telegram_tasks(chat_id, started_at DESC);
"""


async def init(dsn: str | None = None) -> None:
    global _pool
    dsn = dsn or config.DB_DSN
    try:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        await _pool.execute(_SCHEMA_MESSAGES)
        await _pool.execute(_SCHEMA_TASKS)
        logger.info("Telegram DB initialized")
    except Exception:
        logger.exception("Failed to initialize Telegram DB — continuing without persistence")
        _pool = None


async def close() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def save_message(
    chat_id: int, role: str, content: str, message_id: int | None = None,
) -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            "INSERT INTO telegram_messages (chat_id, role, content, message_id) VALUES ($1, $2, $3, $4)",
            chat_id, role, content, message_id,
        )
    except Exception:
        logger.warning("Failed to save message — DB may be down")


async def get_history(chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    try:
        rows = await _pool.fetch(
            "SELECT role, content, created_at FROM telegram_messages "
            "WHERE chat_id = $1 ORDER BY created_at DESC LIMIT $2",
            chat_id, limit,
        )
        return [dict(r) for r in reversed(rows)]
    except Exception:
        logger.warning("Failed to fetch history — DB may be down")
        return []


async def save_task(
    task_id: str, chat_id: int, intent_type: str,
    description: str, repo: str | None = None,
) -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            "INSERT INTO telegram_tasks (task_id, chat_id, intent_type, repo, description) "
            "VALUES ($1, $2, $3, $4, $5)",
            task_id, chat_id, intent_type, repo, description,
        )
    except Exception:
        logger.warning("Failed to save task %s", task_id)


async def update_task(
    task_id: str, status: str,
    files: list[str] | None = None,
    commit_sha: str | None = None,
    exit_code: int | None = None,
    summary: str | None = None,
) -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            "UPDATE telegram_tasks SET status=$1, files=$2, commit_sha=$3, "
            "exit_code=$4, summary=$5, finished_at=now() WHERE task_id=$6",
            status, files, commit_sha, exit_code, summary, task_id,
        )
    except Exception:
        logger.warning("Failed to update task %s", task_id)


async def recover_stale_tasks() -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            "UPDATE telegram_tasks SET status='failed', "
            "summary='Container restarted', finished_at=now() "
            "WHERE status = 'running'"
        )
    except Exception:
        logger.warning("Failed to recover stale tasks")
