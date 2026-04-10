"""Poll loop — long-poll for messages, classify, dispatch."""
from __future__ import annotations

import asyncio
import logging

from tools.telegram import api, classifier, config, db, dispatcher
from tools.telegram.auth import is_authorized
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

_pending_code: dict[int, Intent] = {}
_background_tasks: set[asyncio.Task] = set()


async def _handle_message(message: dict) -> None:
    if not is_authorized(message):
        return

    text = message.get("text", "")
    if not text:
        return

    chat_id = message["chat"]["id"]
    msg_id = message.get("message_id")

    await db.save_message(chat_id, "user", text, msg_id)

    if chat_id in _pending_code:
        intent = _pending_code.pop(chat_id)
        intent.repo = text.strip()
        await dispatcher.dispatch(intent, chat_id=chat_id, reply_to=msg_id)
        return

    intent = await classifier.classify(text)

    if intent.type == "code" and not intent.repo:
        _pending_code[chat_id] = intent
        await api.send_message("Which repo?", reply_to=msg_id)
        return

    if intent.type not in ("status", "cancel"):
        await api.send_message("Got it — classifying...", reply_to=msg_id)

    await dispatcher.dispatch(intent, chat_id=chat_id, reply_to=msg_id)


async def poll_loop() -> None:
    if not config.is_configured():
        logger.warning("Telegram not configured (missing token or chat_id) — not starting")
        return

    await db.init()
    await db.recover_stale_tasks()

    logger.info("Telegram poll_loop starting (%s)", config.summary())

    offset = 0
    try:
        while True:
            try:
                updates = await api.get_updates(offset)
                for update in updates:
                    offset = max(offset, update["update_id"] + 1)
                    msg = update.get("message")
                    if msg:
                        task = asyncio.create_task(_handle_message(msg))
                        _background_tasks.add(task)
                        task.add_done_callback(_background_tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poll_loop error — retrying in 5s")
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("poll_loop cancelled — exiting")
    finally:
        await db.close()
