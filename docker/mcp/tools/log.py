"""
Log MCP tool — search and analyze logs on amarillo (/mnt/nvmeINT/logs/).

Actions:
  list    — list log files sorted by size (largest first)
  search  — grep for a pattern across logs (with path traversal prevention)
  tail    — tail the last N lines of a log file (max 500)
  count   — count pattern occurrences time-bucketed by hour or day
  errors  — aggregate error/exception counts per log file, sorted descending
  between — filter log lines within an ISO 8601 timestamp range

Security:
  - _validate_file_param(): rejects '..', absolute paths, allows only safe chars
  - _shell_quote(): single-quote escaping for safe SSH command building
  - Output capped at max_results (default 100, hard max 500)

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import re
from typing import Any

from tools import register  # type: ignore[import]
from tools._ssh import SSHExecutor  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = "/mnt/nvmeINT/logs"
_DEFAULT_MAX_RESULTS = 100
_HARD_MAX_RESULTS = 500
_MAX_TAIL_LINES = 500

# Only allow safe filename characters: word chars, dots, asterisks, hyphens, question marks
_SAFE_FILE_RE = re.compile(r'^[\w.*?\-]+$')

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "log",
    "description": (
        "Search and analyze logs on amarillo (/mnt/nvmeINT/logs/).\n"
        "Actions:\n"
        "  list    — list log files sorted by size (largest first)\n"
        "  search  — grep for a pattern across logs\n"
        "  tail    — tail the last N lines of a log file (max 500)\n"
        "  count   — count pattern occurrences bucketed by hour or day\n"
        "  errors  — aggregate ERROR/FATAL/Exception/panic/Traceback per file\n"
        "  between — filter log lines within an ISO 8601 timestamp range"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "search", "tail", "count", "errors", "between"],
            },
            "pattern": {"type": "string"},
            "file": {"type": "string"},
            "lines": {"type": "integer"},
            "window": {"type": "string", "enum": ["hour", "day"]},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["action"],
    },
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _text(content: str) -> list[dict[str, Any]]:
    """Wrap *content* in an MCP text content block."""
    return [{"type": "text", "text": content}]


def _shell_quote(s: str) -> str:
    """Single-quote *s* for safe inclusion in an SSH shell command.

    Replaces each single-quote inside *s* with '\\'' (end quote, escaped
    quote, re-open quote) per standard POSIX shell quoting rules.
    """
    return "'" + s.replace("'", "'\\''") + "'"


def _validate_file_param(file: str) -> str | None:
    """Validate *file* parameter against path traversal and injection.

    Returns None if valid, or an error message string if invalid.

    Rules:
      - Must not be empty
      - Must not start with '/' (absolute path)
      - Must not contain '..' (directory traversal)
      - Must match only safe chars: word chars, dots, asterisks, hyphens
    """
    if not file:
        return "log: 'file' parameter is empty"
    if file.startswith("/"):
        return f"log: invalid 'file' parameter {file!r} — absolute paths are not allowed"
    if ".." in file:
        return f"log: invalid 'file' parameter {file!r} — path traversal ('..') is not allowed"
    if not _SAFE_FILE_RE.match(file):
        return f"log: invalid 'file' parameter {file!r} — only [\\w.*?\\-] characters are allowed"
    return None


def _effective_max(args: dict[str, Any]) -> int:
    """Return the effective max_results, clamped to [1, _HARD_MAX_RESULTS]."""
    raw = args.get("max_results", _DEFAULT_MAX_RESULTS)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_MAX_RESULTS
    return max(1, min(n, _HARD_MAX_RESULTS))


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _list(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """List log files sorted by size (largest first)."""
    cmd = f"ls -lhS --time-style=long-iso {LOG_DIR}/"
    result = await ssh.run("amarillo", cmd)
    if result.returncode != 0 and result.stderr:
        return _text(f"log list error: {result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no files found)")


async def _search(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Grep for a pattern across log files."""
    pattern = args.get("pattern", "")
    if not pattern:
        return _text("log search: 'pattern' is required")

    file_param: str = args.get("file", "*.log")
    err = _validate_file_param(file_param)
    if err:
        return _text(err)

    max_results = _effective_max(args)
    quoted_pattern = _shell_quote(pattern)
    target = f"{LOG_DIR}/{file_param}"
    cmd = f"grep -rPn --max-count={max_results} {quoted_pattern} {target} 2>/dev/null | head -n {max_results}"
    result = await ssh.run("amarillo", cmd)
    output = result.stdout.strip()
    if not output:
        return _text(f"log search: no matches for pattern {pattern!r}")
    return _text(output)


async def _tail(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Tail the last N lines of a log file (clamped to 500)."""
    file_param: str = args.get("file", "")
    if not file_param:
        return _text("log tail: 'file' is required")

    err = _validate_file_param(file_param)
    if err:
        return _text(err)

    lines = args.get("lines", 50)
    try:
        lines = int(lines)
    except (TypeError, ValueError):
        lines = 50
    lines = max(1, min(lines, _MAX_TAIL_LINES))

    target = f"{LOG_DIR}/{file_param}"
    cmd = f"tail -n {lines} {target}"
    result = await ssh.run("amarillo", cmd)
    if result.returncode != 0 and result.stderr:
        return _text(f"log tail error: {result.stderr.strip()}")
    return _text(result.stdout.strip() or "(file is empty)")


async def _count(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Count pattern occurrences time-bucketed by hour or day."""
    pattern = args.get("pattern", "")
    if not pattern:
        return _text("log count: 'pattern' is required")

    file_param: str = args.get("file", "*.log")
    err = _validate_file_param(file_param)
    if err:
        return _text(err)

    window = args.get("window", "hour")
    if window not in ("hour", "day"):
        window = "hour"

    # awk field for bucketing: hour = first 13 chars (YYYY-MM-DD HH), day = first 10
    bucket_len = 13 if window == "hour" else 10

    quoted_pattern = _shell_quote(pattern)
    target = f"{LOG_DIR}/{file_param}"
    cmd = (
        f"grep -rP {quoted_pattern} {target} 2>/dev/null "
        f"| awk '{{print substr($0,1,{bucket_len})}}' "
        f"| sort | uniq -c | sort -rn"
    )
    result = await ssh.run("amarillo", cmd)
    output = result.stdout.strip()
    if not output:
        return _text(f"log count: no occurrences of pattern {pattern!r}")
    return _text(output)


async def _errors(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Aggregate ERROR/FATAL/Exception/panic/Traceback counts per log file."""
    file_param: str = args.get("file", "*.log")
    err = _validate_file_param(file_param)
    if err:
        return _text(err)

    target = f"{LOG_DIR}/{file_param}"
    cmd = (
        f"grep -rcP 'ERROR|FATAL|Exception|panic|Traceback' {target} 2>/dev/null "
        f"| grep -v ':0$' | sort -t: -k2 -rn"
    )
    result = await ssh.run("amarillo", cmd)
    output = result.stdout.strip()
    if not output:
        return _text("log errors: no error patterns found")
    return _text(output)


async def _between(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Filter log lines within an ISO 8601 start/end timestamp range."""
    start: str = args.get("start", "")
    end: str = args.get("end", "")

    if not start or not end:
        missing = []
        if not start:
            missing.append("'start'")
        if not end:
            missing.append("'end'")
        return _text(f"log between: {' and '.join(missing)} {'are' if len(missing) > 1 else 'is'} required")

    file_param: str = args.get("file", "*.log")
    err = _validate_file_param(file_param)
    if err:
        return _text(err)

    max_results = _effective_max(args)
    quoted_start = _shell_quote(start)
    quoted_end = _shell_quote(end)
    target = f"{LOG_DIR}/{file_param}"

    # awk-based range filter: print lines whose leading timestamp is within [start, end]
    # Assumes log lines begin with an ISO 8601 timestamp (YYYY-MM-DDThh:mm:ss or similar)
    cmd = (
        f"awk 'NF && substr($0,1,{len(start)}) >= {quoted_start} "
        f"&& substr($0,1,{len(end)}) <= {quoted_end}' {target} 2>/dev/null "
        f"| head -n {max_results}"
    )
    result = await ssh.run("amarillo", cmd)
    output = result.stdout.strip()
    if not output:
        return _text(f"log between: no lines found between {start!r} and {end!r}")
    return _text(output)


# ---------------------------------------------------------------------------
# Public handle()
# ---------------------------------------------------------------------------


async def handle(
    args: dict[str, Any],
    ssh: SSHExecutor | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the appropriate log action handler.

    Args:
        args: MCP tool input arguments.
        ssh:  SSHExecutor instance; if None a default one is created.
              Pass a mock in tests.

    Returns:
        list of MCP content blocks (always at least one text block).
    """
    if ssh is None:
        ssh = SSHExecutor()

    action = args.get("action")
    if action is None:
        return _text("log: 'action' is required")

    if action == "list":
        return await _list(args, ssh)
    if action == "search":
        return await _search(args, ssh)
    if action == "tail":
        return await _tail(args, ssh)
    if action == "count":
        return await _count(args, ssh)
    if action == "errors":
        return await _errors(args, ssh)
    if action == "between":
        return await _between(args, ssh)

    return _text(f"log: unknown action {action!r}. Valid: list, search, tail, count, errors, between")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
