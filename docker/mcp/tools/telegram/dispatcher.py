"""Intent -> handler routing."""
from __future__ import annotations

import logging

from tools.telegram import api
from tools.telegram.handlers.cancel import handle_cancel
from tools.telegram.handlers.code import handle_code
from tools.telegram.handlers.create import handle_create
from tools.telegram.handlers.question import handle_question
from tools.telegram.handlers.status import handle_status
from tools.telegram.handlers.tool import handle_tool
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)


async def dispatch(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    match intent.type:
        case "status":
            await handle_status(reply_to=reply_to)
        case "cancel":
            await handle_cancel(reply_to=reply_to)
        case "tool":
            await handle_tool(intent, reply_to=reply_to)
        case "code":
            await handle_code(intent, chat_id=chat_id, reply_to=reply_to)
        case "create":
            await handle_create(intent, chat_id=chat_id, reply_to=reply_to)
        case "question":
            await handle_question(intent, chat_id=chat_id, reply_to=reply_to)
        case _:
            logger.warning("Unknown intent type: %s", intent.type)
            await api.send_message(f"Unknown intent: {intent.type}", reply_to=reply_to)
