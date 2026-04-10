"""Data models — pure dataclasses, no logic."""
from __future__ import annotations

import dataclasses
import time
from typing import Any


@dataclasses.dataclass
class Intent:
    type: str
    tool: str = ""
    action: str = ""
    args: dict[str, Any] = dataclasses.field(default_factory=dict)
    repo: str | None = None
    task: str = ""
    name: str = ""
    description: str = ""
    language: str = ""
    text: str = ""


@dataclasses.dataclass
class TaskState:
    task_id: str
    description: str
    repo: str | None = None
    status: str = "running"
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    asyncio_task: Any = None
    process: Any = None


@dataclasses.dataclass
class ConversationMessage:
    chat_id: int
    role: str
    content: str
    message_id: int | None = None
