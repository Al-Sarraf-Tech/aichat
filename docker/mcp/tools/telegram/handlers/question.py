"""Q&A handler — conversational answers via Gemma with history context."""
from __future__ import annotations

import logging

import httpx

from tools.telegram import api, config, db
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a helpful infrastructure assistant for a home lab running Fedora 43.
You have access to monitoring, git, SSH, IoT, and log tools.
Answer questions concisely.
If the user seems to want an action, suggest the right phrasing.\
"""


async def handle_question(
    intent: Intent,
    chat_id: int,
    reply_to: int | None = None,
) -> None:
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    history = await db.get_history(chat_id, limit=10)
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": intent.text})

    try:
        async with httpx.AsyncClient(timeout=config.LM_QA_TIMEOUT) as client:
            resp = await client.post(
                f"{config.LM_STUDIO_URL}/v1/chat/completions",
                json={
                    "model": config.LM_STUDIO_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 512,
                },
            )
        answer = resp.json()["choices"][0]["message"]["content"]
        await api.send_message(answer, reply_to=reply_to)
        await db.save_message(chat_id, "assistant", answer)
    except Exception as exc:
        logger.error("Question handler failed: %s", exc)
        await api.send_message(f"Error: {exc}", reply_to=reply_to)
