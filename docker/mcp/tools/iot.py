"""
IoT device control MCP tool — Roku ECP + Shield SSH.

Actions:
  list_devices — show registry + online/offline status
  power        — on (WOL if MAC set) / off (Roku: PowerOff keypress, Shield: KEYCODE_SLEEP)
  keypress     — Roku via POST /keypress/{key}, Shield via input keyevent KEYCODE_{key}
  launch       — Roku via POST /launch/{app_id} (resolve name from GET /query/apps),
                 Shield via am start
  query        — Roku via GET /query/active-app + GET /query/device-info, Shield via dumpsys
  apps         — Roku only — GET /query/apps, parse XML
  command      — Shield only — raw SSH command

Protocol routing: Roku = httpx (ECP), Shield = SSH via SSHExecutor

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import re
import socket
from typing import Any

import httpx

from tools import register  # type: ignore[import]
from tools._ssh import SSHExecutor, SSHResult  # type: ignore[import]

# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------

DEVICES: dict[str, dict[str, Any]] = {
    "roku": {
        "name": "TCL Roku TV",
        "type": "roku",
        "host": "192.168.50.13",
        "port": 8060,
        "protocol": "http",
        "mac": None,
    },
    "shield": {
        "name": "NVIDIA Shield TV",
        "type": "shield",
        "host": "192.168.50.99",
        "port": 8022,
        "protocol": "ssh",
        "mac": None,
    },
}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "iot",
    "description": (
        "Control IoT devices in the home lab.\n"
        "Actions:\n"
        "  list_devices — list devices with online/offline status\n"
        "  power        — power on (WOL) or off a device\n"
        "  keypress     — send a key to a device (Roku ECP or Shield ADB key event)\n"
        "  launch       — launch an app by name or ID\n"
        "  query        — query device state (active app, device info)\n"
        "  apps         — list installed apps (Roku only)\n"
        "  command      — run a raw command on the Shield via SSH"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_devices", "power", "keypress", "launch", "query", "apps", "command"],
                "description": "Action to perform.",
            },
            "device": {
                "type": "string",
                "description": "Device name from the registry (e.g. 'roku', 'shield').",
            },
            "key": {
                "type": "string",
                "description": "Key name for keypress or power direction ('on'/'off') for power.",
            },
            "app": {
                "type": "string",
                "description": "App name or numeric ID for launch action.",
            },
            "command": {
                "type": "string",
                "description": "Raw shell command for Shield command action.",
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


def _roku_base_url(device: dict[str, Any]) -> str:
    return f"http://{device['host']}:{device['port']}"


def _parse_roku_xml_apps(xml_text: str) -> list[tuple[str, str]]:
    """Parse Roku /query/apps XML into a list of (id, name) tuples."""
    return re.findall(r'<app id="(\d+)"[^>]*>([^<]+)</app>', xml_text)


def _send_wol(mac: str) -> None:
    """Send a Wake-on-LAN magic packet to the given MAC address."""
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, ("<broadcast>", 9))


# ---------------------------------------------------------------------------
# Action: list_devices
# ---------------------------------------------------------------------------


async def _list_devices(args: dict[str, Any]) -> list[dict[str, Any]]:
    lines = ["Registered IoT devices:"]
    for key, dev in DEVICES.items():
        host = dev["host"]
        port = dev["port"]
        protocol = dev["protocol"]
        mac_info = f", mac={dev['mac']}" if dev["mac"] else ""
        lines.append(
            f"  {key}: {dev['name']} [{protocol}://{host}:{port}{mac_info}]"
        )
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# Action: keypress
# ---------------------------------------------------------------------------


async def _keypress_roku(device: dict[str, Any], key: str) -> list[dict[str, Any]]:
    url = f"{_roku_base_url(device)}/keypress/{key}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url)
    if resp.status_code == 200:
        return _text(f"roku keypress '{key}': ok")
    return _text(f"roku keypress '{key}': failed (status {resp.status_code})")


async def _keypress_shield(
    device: dict[str, Any], key: str, ssh: SSHExecutor
) -> list[dict[str, Any]]:
    keycode = f"KEYCODE_{key.upper()}"
    cmd = f"input keyevent {keycode}"
    result: SSHResult = await ssh.run(device["host"], cmd, port=device["port"])
    if result.returncode == 0:
        return _text(f"shield keypress '{keycode}': ok")
    return _text(f"shield keypress '{keycode}': failed (exit {result.returncode}): {result.stderr.strip()}")


async def _action_keypress(
    args: dict[str, Any], device_key: str, device: dict[str, Any], ssh: SSHExecutor
) -> list[dict[str, Any]]:
    key = args.get("key", "").strip()
    if not key:
        return _text("iot keypress: 'key' is required")

    if device["type"] == "roku":
        return await _keypress_roku(device, key)
    if device["type"] == "shield":
        return await _keypress_shield(device, key, ssh)
    return _text(f"iot keypress: unsupported device type '{device['type']}'")


# ---------------------------------------------------------------------------
# Action: power
# ---------------------------------------------------------------------------


async def _action_power(
    args: dict[str, Any], device_key: str, device: dict[str, Any], ssh: SSHExecutor
) -> list[dict[str, Any]]:
    direction = args.get("key", "off").strip().lower()

    if direction == "on":
        mac = device.get("mac")
        if mac:
            try:
                _send_wol(mac)
                return _text(f"iot power on: WOL packet sent to {device['name']}")
            except Exception as exc:
                return _text(f"iot power on: WOL failed — {exc}")
        return _text(f"iot power on: no MAC set for {device_key!r}; cannot send WOL")

    # Power off
    if device["type"] == "roku":
        return await _keypress_roku(device, "PowerOff")
    if device["type"] == "shield":
        return await _keypress_shield(device, "SLEEP", ssh)

    return _text(f"iot power off: unsupported device type '{device['type']}'")


# ---------------------------------------------------------------------------
# Action: launch
# ---------------------------------------------------------------------------


async def _action_launch(
    args: dict[str, Any], device_key: str, device: dict[str, Any], ssh: SSHExecutor
) -> list[dict[str, Any]]:
    app = args.get("app", "").strip()
    if not app:
        return _text("iot launch: 'app' is required")

    if device["type"] == "roku":
        # Resolve name -> id if not already numeric
        app_id = app
        if not app.isdigit():
            base = _roku_base_url(device)
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}/query/apps")
            apps = _parse_roku_xml_apps(resp.text)
            matched = [(aid, aname) for aid, aname in apps if aname.lower() == app.lower()]
            if not matched:
                available = ", ".join(f"{n} ({i})" for i, n in apps)
                return _text(f"iot launch: app '{app}' not found. Available: {available}")
            app_id = matched[0][0]

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{_roku_base_url(device)}/launch/{app_id}")
        if resp.status_code == 200:
            return _text(f"roku launch '{app}' (id={app_id}): ok")
        return _text(f"roku launch '{app}': failed (status {resp.status_code})")

    if device["type"] == "shield":
        cmd = f"am start {app}"
        result: SSHResult = await ssh.run(device["host"], cmd, port=device["port"])
        if result.returncode == 0:
            return _text(f"shield launch '{app}': ok")
        return _text(f"shield launch '{app}': failed — {result.stderr.strip()}")

    return _text(f"iot launch: unsupported device type '{device['type']}'")


# ---------------------------------------------------------------------------
# Action: query
# ---------------------------------------------------------------------------


async def _action_query(
    args: dict[str, Any], device_key: str, device: dict[str, Any], ssh: SSHExecutor
) -> list[dict[str, Any]]:
    if device["type"] == "roku":
        base = _roku_base_url(device)
        async with httpx.AsyncClient() as client:
            active_resp = await client.get(f"{base}/query/active-app")
            info_resp = await client.get(f"{base}/query/device-info")
        lines = [
            f"roku query — {device['name']}:",
            f"  active-app: {active_resp.text.strip()}",
            f"  device-info: {info_resp.text.strip()}",
        ]
        return _text("\n".join(lines))

    if device["type"] == "shield":
        cmd = "dumpsys activity activities | head -20"
        result: SSHResult = await ssh.run(device["host"], cmd, port=device["port"])
        return _text(f"shield query:\n{result.stdout.strip()}")

    return _text(f"iot query: unsupported device type '{device['type']}'")


# ---------------------------------------------------------------------------
# Action: apps
# ---------------------------------------------------------------------------


async def _action_apps(
    args: dict[str, Any], device_key: str, device: dict[str, Any]
) -> list[dict[str, Any]]:
    if device["type"] != "roku":
        return _text(f"iot apps: only supported on Roku devices (got '{device_key}')")

    base = _roku_base_url(device)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base}/query/apps")

    apps = _parse_roku_xml_apps(resp.text)
    if not apps:
        return _text("roku apps: no apps found (or failed to parse XML)")

    lines = [f"Installed apps on {device['name']} ({len(apps)} total):"]
    for app_id, app_name in apps:
        lines.append(f"  [{app_id}] {app_name}")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# Action: command (Shield only)
# ---------------------------------------------------------------------------


async def _action_command(
    args: dict[str, Any], device_key: str, device: dict[str, Any], ssh: SSHExecutor
) -> list[dict[str, Any]]:
    if device["type"] != "shield":
        return _text(f"iot command: only supported on Shield devices (got '{device_key}')")

    cmd = args.get("command", "").strip()
    if not cmd:
        return _text("iot command: 'command' is required")

    result: SSHResult = await ssh.run(device["host"], cmd, port=device["port"])
    lines = [
        f"shield command: {cmd!r}",
        f"exit: {result.returncode}",
    ]
    if result.stdout:
        lines.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        lines.append(f"stderr:\n{result.stderr.rstrip()}")
    lines.append(f"elapsed: {result.elapsed:.2f}s")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def handle(
    args: dict[str, Any],
    ssh: SSHExecutor | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the appropriate IoT action handler.

    Args:
        args: MCP tool input arguments.
        ssh:  SSHExecutor instance; if None a default one is created.
              Pass a mock in tests.

    Returns:
        list of MCP content blocks (always at least one text block).
    """
    if ssh is None:
        ssh = SSHExecutor(
            allowed_hosts={"192.168.50.99", "192.168.50.13"}
        )

    action = args.get("action")
    if not action:
        return _text("iot: 'action' is required")

    if action == "list_devices":
        return await _list_devices(args)

    # All other actions require a device
    device_key = args.get("device", "").strip().lower()
    if not device_key:
        return _text(f"iot {action}: 'device' is required")

    device = DEVICES.get(device_key)
    if device is None:
        known = ", ".join(sorted(DEVICES.keys()))
        return _text(f"iot {action}: unknown device '{device_key}' (known: {known})")

    if action == "keypress":
        return await _action_keypress(args, device_key, device, ssh)
    if action == "power":
        return await _action_power(args, device_key, device, ssh)
    if action == "launch":
        return await _action_launch(args, device_key, device, ssh)
    if action == "query":
        return await _action_query(args, device_key, device, ssh)
    if action == "apps":
        return await _action_apps(args, device_key, device)
    if action == "command":
        return await _action_command(args, device_key, device, ssh)

    return _text(f"iot: unknown action '{action}'")


# ---------------------------------------------------------------------------
# Register with the tool registry
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
