"""
SSH MCP tool — execute commands on remote hosts.

Actions:
  exec        — run a shell command on a remote host
  upload      — copy a local file (under /workspace/) to a remote host via SCP
  download    — copy a remote file to a local path (under /workspace/) via SCP
  test        — probe host reachability with a lightweight echo
  list_hosts  — query Tailscale peer status from amarillo and format the allowlist

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from tools import register  # type: ignore[import]
from tools._ssh import SSHExecutor, SSHResult, sanitize_ssh_error  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WORKSPACE_PREFIX = "/workspace/"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "ssh",
    "description": (
        "Execute commands on remote hosts or manage files via SSH/SCP.\n"
        "Actions:\n"
        "  exec        — run a shell command on a remote host\n"
        "  upload      — copy a local /workspace/ file to a remote host\n"
        "  download    — copy a remote file to a local /workspace/ path\n"
        "  test        — check host reachability (echo probe)\n"
        "  list_hosts  — list allowed hosts and their Tailscale status"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["exec", "upload", "download", "test", "list_hosts"],
                "description": "Action to perform.",
            },
            "host": {
                "type": "string",
                "description": "Target host name (must be in the allowlist).",
            },
            "command": {
                "type": "string",
                "description": "Shell command to execute (required for exec).",
            },
            "local_path": {
                "type": "string",
                "description": (
                    "Local file path for upload (source) or download (destination). "
                    "Must start with /workspace/."
                ),
            },
            "remote_path": {
                "type": "string",
                "description": "Remote file path for upload (destination) or download (source).",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default: 30).",
            },
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


def _sanitize(message: str) -> str:
    """Sanitize an SSH error message via the module-level helper."""
    return sanitize_ssh_error(message)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _exec(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    host = args.get("host")
    command = args.get("command")

    if not host:
        return _text("ssh exec: 'host' is required")
    if not command:
        return _text("ssh exec: 'command' is required")

    timeout = float(args.get("timeout", 30.0))
    try:
        result: SSHResult = await ssh.run(host, command, timeout=timeout)
    except (ValueError, RuntimeError) as exc:
        return _text(f"ssh exec error: {_sanitize(str(exc))}")

    lines: list[str] = [f"host: {result.host}", f"exit: {result.returncode}"]
    if result.stdout:
        lines.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        lines.append(f"stderr:\n{result.stderr.rstrip()}")
    lines.append(f"elapsed: {result.elapsed:.2f}s")
    return _text("\n".join(lines))


async def _test(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    host = args.get("host")
    if not host:
        return _text("ssh test: 'host' is required")

    timeout = float(args.get("timeout", 5.0))
    try:
        result: SSHResult = await ssh.run(host, "echo ok", timeout=timeout)
    except (ValueError, RuntimeError) as exc:
        return _text(f"{host}: unreachable — {_sanitize(str(exc))}")

    if result.returncode == 0:
        return _text(f"{host}: reachable (elapsed {result.elapsed:.2f}s)")
    sanitized_err = _sanitize(result.stderr.strip()) if result.stderr.strip() else "(no details)"
    return _text(f"{host}: unreachable — exit {result.returncode}, {sanitized_err}")


async def _list_hosts(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    timeout = float(args.get("timeout", 15.0))
    try:
        result: SSHResult = await ssh.run("amarillo", "tailscale status --json", timeout=timeout)
    except (ValueError, RuntimeError) as exc:
        return _text(f"ssh list_hosts error: {_sanitize(str(exc))}")

    if result.returncode != 0:
        sanitized = _sanitize(result.stderr.strip())
        return _text(f"ssh list_hosts: tailscale query failed — {sanitized}")

    try:
        data: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _text("ssh list_hosts: could not parse tailscale JSON output")

    lines: list[str] = ["Tailscale hosts:"]

    self_info = data.get("Self", {})
    if self_info:
        name = self_info.get("HostName", "?")
        ips = ", ".join(self_info.get("TailscaleIPs", []))
        online = "online" if self_info.get("Online") else "offline"
        lines.append(f"  self: {name} [{ips}] {online}")

    peers: dict[str, Any] = data.get("Peers", {})
    if peers:
        lines.append("  peers:")
        for peer_data in peers.values():
            p_name = peer_data.get("HostName", "?")
            p_ips = ", ".join(peer_data.get("TailscaleIPs", []))
            p_online = "online" if peer_data.get("Online") else "offline"
            lines.append(f"    {p_name} [{p_ips}] {p_online}")
    else:
        lines.append("  peers: (none)")

    return _text("\n".join(lines))


async def _upload(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    host = args.get("host")
    local_path: str = args.get("local_path", "")
    remote_path: str = args.get("remote_path", "")

    if not host:
        return _text("ssh upload: 'host' is required")
    if not local_path:
        return _text("ssh upload: 'local_path' is required")
    if not remote_path:
        return _text("ssh upload: 'remote_path' is required")
    if not local_path.startswith(_WORKSPACE_PREFIX):
        return _text(
            f"ssh upload: 'local_path' must start with {_WORKSPACE_PREFIX!r} — "
            f"got {local_path!r}"
        )

    if not ssh.is_host_allowed(host):
        return _text(f"ssh upload: host {host!r} is not allowed")

    timeout = float(args.get("timeout", 60.0))
    resolved_host = ssh._resolve_host(host)
    user = ssh.user
    port = ssh.port

    try:
        proc = await asyncio.create_subprocess_exec(
            "scp",
            "-i", "/app/.ssh/team_key",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-P", str(port),
            local_path,
            f"{user}@{resolved_host}:{remote_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return _text(f"upload timed out after {timeout}s")

        returncode = proc.returncode if proc.returncode is not None else -1
    except Exception as exc:
        return _text(f"ssh upload error: {_sanitize(str(exc))}")

    if returncode == 0:
        return _text(f"upload: {local_path} -> {host}:{remote_path} completed")
    stderr_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
    sanitized = _sanitize(stderr_msg)
    return _text(f"upload failed (exit {returncode}): {sanitized}")


async def _download(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    host = args.get("host")
    remote_path: str = args.get("remote_path", "")
    local_path: str = args.get("local_path", "")

    if not host:
        return _text("ssh download: 'host' is required")
    if not remote_path:
        return _text("ssh download: 'remote_path' is required")
    if not local_path:
        return _text("ssh download: 'local_path' is required")
    if not local_path.startswith(_WORKSPACE_PREFIX):
        return _text(
            f"ssh download: 'local_path' must start with {_WORKSPACE_PREFIX!r} — "
            f"got {local_path!r}"
        )

    if not ssh.is_host_allowed(host):
        return _text(f"ssh download: host {host!r} is not allowed")

    timeout = float(args.get("timeout", 60.0))
    resolved_host = ssh._resolve_host(host)
    user = ssh.user
    port = ssh.port

    try:
        proc = await asyncio.create_subprocess_exec(
            "scp",
            "-i", "/app/.ssh/team_key",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-P", str(port),
            f"{user}@{resolved_host}:{remote_path}",
            local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return _text(f"download timed out after {timeout}s")

        returncode = proc.returncode if proc.returncode is not None else -1
    except Exception as exc:
        return _text(f"ssh download error: {_sanitize(str(exc))}")

    if returncode == 0:
        return _text(f"download: {host}:{remote_path} -> {local_path} completed")
    stderr_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
    sanitized = _sanitize(stderr_msg)
    return _text(f"download failed (exit {returncode}): {sanitized}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def handle(
    args: dict[str, Any],
    ssh: SSHExecutor | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the appropriate SSH action handler.

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
        return _text("ssh: 'action' is required")

    if action == "exec":
        return await _exec(args, ssh)
    if action == "test":
        return await _test(args, ssh)
    if action == "list_hosts":
        return await _list_hosts(args, ssh)
    if action == "upload":
        return await _upload(args, ssh)
    if action == "download":
        return await _download(args, ssh)

    return _text(f"ssh: unknown action '{action}'")


# ---------------------------------------------------------------------------
# Register with the tool registry
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
