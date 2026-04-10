"""Hybrid classifier — regex fast path + Gemma LLM fallback."""
from __future__ import annotations

import json
import logging
import re

import httpx

from tools.telegram import config
from tools.telegram.models import Intent

logger = logging.getLogger(__name__)

_Pattern = tuple[re.Pattern, callable]

def _build_patterns() -> list[_Pattern]:
    patterns: list[_Pattern] = []

    def _add(pattern: str, factory):
        patterns.append((re.compile(pattern, re.IGNORECASE), factory))

    # Direct commands
    _add(r"^/?status$", lambda m: Intent(type="status"))
    _add(r"^/?cancel$", lambda m: Intent(type="cancel"))

    # Monitor — specific metrics
    _MONITOR_ACTION_MAP = {
        "thermal": "thermals",
        "thermals": "thermals",
        "container": "container",
        "containers": "container",
        "disk": "disk",
        "gpu": "gpu",
        "service": "services",
        "services": "services",
        "tailscale": "tailscale",
    }
    _add(
        r"(?:check|show|get)\s+(thermals?|containers?|disk|gpu|services?|tailscale)",
        lambda m: Intent(type="tool", tool="monitor", action=_MONITOR_ACTION_MAP.get(m.group(1).lower(), m.group(1).lower())),
    )

    # Monitor — overview
    _add(
        r"(?:monitor|overview|fleet|how.*fleet)",
        lambda m: Intent(type="tool", tool="monitor", action="overview"),
    )

    # Logs
    _add(
        r"(?:tail|read|show)\s+logs?\s*(?:for|of|from)?\s*(?P<svc>\S+)?",
        lambda m: Intent(
            type="tool", tool="log", action="tail",
            args={"service": m.group("svc")} if m.group("svc") else {},
        ),
    )

    # Git
    _GIT_ACTION_MAP = {
        "status": "status",
        "log": "log",
        "diff": "diff",
        "ci": "ci",
        "issue": "issue",
        "issues": "issue",
        "scorecard": "scorecard",
        "push": "push",
    }
    _add(
        r"git\s+(?P<action>status|log|diff|ci|issues?|scorecard|push)\s*(?:in|for|of)?\s*(?P<repo>\S+)?",
        lambda m: Intent(
            type="tool", tool="git",
            action=_GIT_ACTION_MAP.get(m.group("action").lower(), m.group("action").lower()),
            args={"repo": m.group("repo")} if m.group("repo") else {},
        ),
    )

    # IoT
    _add(
        r"(?:list|show)\s+(?:devices?|sensors?|switches?)",
        lambda m: Intent(type="tool", tool="iot", action="list_devices"),
    )

    # SSH
    _add(
        r"(?:ssh|run)\s+(?:on\s+)?(?P<host>\S+)\s+(?P<cmd>.+)",
        lambda m: Intent(
            type="tool", tool="ssh", action="exec",
            args={"host": m.group("host"), "command": m.group("cmd")},
        ),
    )

    # Notify
    _add(
        r"(?:send|notify|alert)\s+(?P<msg>.+)",
        lambda m: Intent(type="tool", tool="notify", action="send", args={"message": m.group("msg")}),
    )

    return patterns


_PATTERNS = _build_patterns()

_CLASSIFIER_SYSTEM_PROMPT = """\
You are an intent classifier for a home-lab infrastructure bot.
Classify the user message into ONE of these types and return raw JSON only (no markdown).

Types:
1. "tool" — invoke an MCP tool. Fields: type, tool, action, args (object)
   Tools: monitor (overview|containers|thermals|disk|gpu|services|tailscale),
          git (status|log|diff|ci|scorecard|create_pr|merge|push|trigger_ci|issues),
          notify (send|send_alert|send_photo|send_document),
          ssh (exec), log (read|tail|list),
          iot (list_devices|read_sensor|toggle_switch)
2. "code" — modify an existing repo. Fields: type, repo, task
3. "create" — new project from scratch. Fields: type, name, language, description
4. "question" — open-ended query. Fields: type, text

If unsure, use type "question" with the original text.
Return ONLY valid JSON, no wrapping.\
"""


async def classify(text: str) -> Intent:
    for pattern, factory in _PATTERNS:
        match = pattern.search(text)
        if match:
            return factory(match)
    return await _classify_llm(text)


async def _classify_llm(text: str) -> Intent:
    try:
        async with httpx.AsyncClient(timeout=config.LM_CLASSIFY_TIMEOUT) as client:
            resp = await client.post(
                f"{config.LM_STUDIO_URL}/v1/chat/completions",
                json={
                    "model": config.LM_STUDIO_MODEL,
                    "messages": [
                        {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 256,
                },
            )
        raw = resp.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return _parse_llm_response(data, text)
    except Exception:
        logger.warning("LLM classification failed — falling back to question", exc_info=True)
        return Intent(type="question", text=text)


def _parse_llm_response(data: dict, original_text: str) -> Intent:
    intent_type = data.get("type", "question")
    if intent_type == "tool":
        return Intent(type="tool", tool=data.get("tool", ""), action=data.get("action", ""), args=data.get("args", {}))
    elif intent_type == "code":
        return Intent(type="code", repo=data.get("repo"), task=data.get("task", original_text))
    elif intent_type == "create":
        return Intent(type="create", name=data.get("name", ""), language=data.get("language", ""), description=data.get("description", ""))
    else:
        return Intent(type="question", text=data.get("text", original_text))
