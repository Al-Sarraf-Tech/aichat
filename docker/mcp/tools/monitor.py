"""
Monitor MCP tool — fleet-wide infrastructure monitoring.

Actions:
  overview    — combined vitals: thermals, memory, disk, containers, tailscale
  containers  — list running Docker containers on a host
  thermals    — CPU/board temperatures across the fleet
  disk        — filesystem usage across the fleet
  gpu         — Intel GPU utilisation from amarillo
  services    — health-check curl on local services (LM Studio, ComfyUI, etc.)
  tailscale   — Tailscale peer status from amarillo

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from tools import register  # type: ignore[import]
from tools._ssh import SSHExecutor, SSHResult  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THERMAL_WARN_C: float = 85.0
DISK_WARN_PCT: int = 85
FLEET_HOSTS: list[str] = ["amarillo", "dominus", "sentinel", "superemus"]

# Services to health-check (name, URL)
_SERVICES: list[tuple[str, str]] = [
    ("LM Studio",    "http://192.168.50.2:1234/health"),
    ("ComfyUI",      "http://192.168.50.2:8388/system_stats"),
    ("Qdrant",       "http://localhost:6333/healthz"),
    ("aichat-mcp",   "http://localhost:8096/health"),
    ("aichat-data",  "http://localhost:8091/health"),
]

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "monitor",
    "description": (
        "Fleet-wide infrastructure monitoring.\n"
        "Actions:\n"
        "  overview   — thermals + memory + disk + containers + tailscale in one call\n"
        "  containers — list running Docker containers on a host\n"
        "  thermals   — CPU/board temperatures across the fleet\n"
        "  disk       — filesystem usage across the fleet\n"
        "  gpu        — Intel GPU utilisation on amarillo\n"
        "  services   — health check for LM Studio, ComfyUI, Qdrant, aichat-mcp, aichat-data\n"
        "  tailscale  — Tailscale peer status from amarillo"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["overview", "containers", "thermals", "disk", "gpu", "services", "tailscale"],
            },
            "host": {
                "type": "string",
                "description": "Target host (containers action, default amarillo).",
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


def _parse_temps(sensors_json: str) -> list[tuple[str, float]]:
    """Extract (label, value_C) pairs from ``sensors -j`` JSON output.

    Only fields whose keys end with ``_input`` and whose values are numeric
    are included (these are the actual temperature readings).

    Returns an empty list if the JSON is invalid or the structure is
    unrecognised.
    """
    try:
        data = json.loads(sensors_json)
    except (json.JSONDecodeError, TypeError):
        return []

    results: list[tuple[str, float]] = []
    if not isinstance(data, dict):
        return results

    for chip_name, chip_data in data.items():
        if not isinstance(chip_data, dict):
            continue
        for feature_name, feature_data in chip_data.items():
            if not isinstance(feature_data, dict):
                continue
            for subkey, value in feature_data.items():
                # Only temp*_input fields are actual temperatures.
                # Skip fan*_input (RPM), in*_input (voltage), energy*_input, etc.
                if subkey.startswith("temp") and subkey.endswith("_input") and isinstance(value, (int, float)):
                    label = f"{chip_name}/{feature_name}"
                    results.append((label, float(value)))

    return results


def _parse_mem(free_output: str) -> tuple[int, int]:
    """Extract (used_bytes, total_bytes) from ``free -b`` output.

    Looks for the ``Mem:`` line and reads the first two numeric columns
    (total, used).  Returns (0, 0) on parse failure.
    """
    for line in free_output.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    total = int(parts[1])
                    used = int(parts[2])
                    return used, total
                except ValueError:
                    pass
    return 0, 0


def _parse_df(df_output: str) -> list[tuple[str, int]]:
    """Extract (mountpoint, pct_used) pairs from ``df`` output.

    Expects the ``--output=source,size,used,avail,pcent,target`` format.
    Skips the header line and any line without a numeric percentage.

    Returns list of (mount, pct) tuples.
    """
    results: list[tuple[str, int]] = []
    lines = df_output.splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        # percentage is the second-to-last field (pcent), mount is last
        # Format: source size used avail pcent target
        # We look for a field matching N%
        pct_field = None
        mount_field = None
        for i, part in enumerate(parts):
            if re.match(r"^\d+%$", part):
                pct_field = part
                # mount is the next column if present
                if i + 1 < len(parts):
                    mount_field = parts[i + 1]
                break
        if pct_field is not None and mount_field is not None:
            try:
                pct = int(pct_field.rstrip("%"))
                results.append((mount_field, pct))
            except ValueError:
                pass
    return results


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _thermals(ssh: SSHExecutor) -> str:
    """Return a formatted thermals report for FLEET_HOSTS."""
    results: dict[str, SSHResult] = await ssh.run_multi(
        FLEET_HOSTS,
        "sensors -j 2>/dev/null || echo '{}'",
    )
    lines: list[str] = ["=== Thermals ==="]
    for host in FLEET_HOSTS:
        if host not in results:
            lines.append(f"  {host}: [unreachable]")
            continue
        r = results[host]
        if r.returncode != 0 or not r.stdout.strip():
            lines.append(f"  {host}: [unreachable]")
            continue
        temps = _parse_temps(r.stdout)
        if not temps:
            lines.append(f"  {host}: (no sensor data)")
            continue
        host_lines: list[str] = []
        for label, val in temps:
            flag = " *** WARNING: ABOVE THERMAL LIMIT ***" if val >= THERMAL_WARN_C else ""
            host_lines.append(f"    {label}: {val:.1f}°C{flag}")
        lines.append(f"  {host}:")
        lines.extend(host_lines)
    return "\n".join(lines)


async def _containers(args: dict[str, Any], ssh: SSHExecutor) -> str:
    """Return a formatted container list for the target host."""
    host = args.get("host", "amarillo")
    r: SSHResult = await ssh.run(host, "docker ps --format json")
    if r.returncode != 0:
        return f"=== Containers ({host}) ===\n  (error: {r.stderr.strip() or 'non-zero exit'})"

    lines: list[str] = [f"=== Containers ({host}) ==="]
    if not r.stdout.strip():
        lines.append("  (none running)")
        return "\n".join(lines)

    for raw_line in r.stdout.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
            name = obj.get("Names", obj.get("Name", "?"))
            status = obj.get("Status", "?")
            lines.append(f"  {name}  —  {status}")
        except json.JSONDecodeError:
            lines.append(f"  {raw_line}")

    return "\n".join(lines)


async def _disk(ssh: SSHExecutor) -> str:
    """Return a formatted disk-usage report for FLEET_HOSTS."""
    results: dict[str, SSHResult] = await ssh.run_multi(
        FLEET_HOSTS,
        "df -B1 --output=source,size,used,avail,pcent,target",
    )
    lines: list[str] = ["=== Disk ==="]
    for host in FLEET_HOSTS:
        if host not in results:
            lines.append(f"  {host}: [unreachable]")
            continue
        r = results[host]
        if r.returncode != 0 or not r.stdout.strip():
            lines.append(f"  {host}: [unreachable]")
            continue
        mounts = _parse_df(r.stdout)
        if not mounts:
            lines.append(f"  {host}: (no df data)")
            continue
        lines.append(f"  {host}:")
        for mount, pct in mounts:
            flag = f" *** WARNING: {pct}% used ***" if pct >= DISK_WARN_PCT else ""
            lines.append(f"    {mount}: {pct}%{flag}")
    return "\n".join(lines)


async def _gpu(ssh: SSHExecutor) -> str:
    """Return Intel GPU utilisation data from amarillo."""
    r: SSHResult = await ssh.run(
        "amarillo",
        "intel_gpu_top -J -s 1000 -o - | head -100 || echo '{}'",
        timeout=15.0,
    )
    lines: list[str] = ["=== GPU (amarillo) ==="]
    if r.returncode != 0 and not r.stdout.strip():
        lines.append("  (error or tool not available)")
        return "\n".join(lines)

    raw = r.stdout.strip()
    if not raw or raw == "{}":
        lines.append("  (no data)")
        return "\n".join(lines)

    # Try JSON parse; fall back to raw text
    try:
        data = json.loads(raw)
        # Surface engine busy percentages if available
        engines = data.get("engines", {})
        if engines:
            for eng_name, eng_data in engines.items():
                if isinstance(eng_data, dict):
                    busy = eng_data.get("busy", None)
                    if busy is not None:
                        lines.append(f"  {eng_name}: {busy:.1f}% busy")
        else:
            lines.append(f"  {raw}")
    except (json.JSONDecodeError, AttributeError):
        # Could be multiple JSON objects (streaming) — just show raw
        lines.append(raw[:500])

    return "\n".join(lines)


async def _services() -> str:
    """Health-check all configured services via HTTP GET."""
    lines: list[str] = ["=== Services ==="]
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in _SERVICES:
            try:
                resp = await client.get(url)
                status = f"HTTP {resp.status_code}"
                ok = resp.status_code < 400
                indicator = "OK" if ok else "FAIL"
            except Exception as exc:
                status = str(exc)
                indicator = "FAIL"
            lines.append(f"  {indicator}  {name} ({url}): {status}")
    return "\n".join(lines)


async def _tailscale(ssh: SSHExecutor) -> str:
    """Return Tailscale Self + Peer info from amarillo."""
    r: SSHResult = await ssh.run("amarillo", "tailscale status --json")
    lines: list[str] = ["=== Tailscale ==="]
    if r.returncode != 0:
        lines.append(f"  (error: {r.stderr.strip() or 'non-zero exit'})")
        return "\n".join(lines)

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        lines.append("  (could not parse tailscale JSON)")
        return "\n".join(lines)

    self_info = data.get("Self", {})
    if self_info:
        name = self_info.get("HostName", "?")
        ips = ", ".join(self_info.get("TailscaleIPs", []))
        online = "online" if self_info.get("Online") else "offline"
        lines.append(f"  self: {name} [{ips}] {online}")

    # The field may be "Peer" (singular, dict of dicts) or "Peers"
    peers: dict[str, Any] = data.get("Peer", data.get("Peers", {}))
    if peers:
        lines.append("  peers:")
        for peer_data in peers.values():
            p_name = peer_data.get("HostName", "?")
            p_ips = ", ".join(peer_data.get("TailscaleIPs", []))
            p_online = "online" if peer_data.get("Online") else "offline"
            p_os = peer_data.get("OS", "")
            os_str = f" ({p_os})" if p_os else ""
            lines.append(f"    {p_name}{os_str} [{p_ips}] {p_online}")
    else:
        lines.append("  peers: (none)")

    return "\n".join(lines)


async def _overview(ssh: SSHExecutor) -> str:
    """Return a combined overview: vitals per host + containers + tailscale."""
    vitals_cmd = (
        "sensors -j 2>/dev/null || echo '{}';"
        " echo '---FREE---';"
        " free -b;"
        " echo '---DF---';"
        " df -B1 --output=source,size,used,avail,pcent,target;"
        " echo '---NPROC---';"
        " nproc"
    )
    vitals_results: dict[str, SSHResult] = await ssh.run_multi(FLEET_HOSTS, vitals_cmd)

    lines: list[str] = ["=== Fleet Overview ==="]

    for host in FLEET_HOSTS:
        lines.append(f"\n--- {host} ---")
        if host not in vitals_results:
            lines.append("  [unreachable]")
            continue
        r = vitals_results[host]
        if r.returncode != 0 or not r.stdout.strip():
            lines.append("  [unreachable]")
            continue

        stdout = r.stdout

        # Split on section markers
        sensors_part = ""
        free_part = ""
        df_part = ""
        nproc_part = ""

        if "---FREE---" in stdout:
            sensors_part, rest = stdout.split("---FREE---", 1)
        else:
            sensors_part = stdout
            rest = ""

        if "---DF---" in rest:
            free_part, rest2 = rest.split("---DF---", 1)
        else:
            free_part = rest
            rest2 = ""

        if "---NPROC---" in rest2:
            df_part, nproc_part = rest2.split("---NPROC---", 1)
        else:
            df_part = rest2

        # Thermals
        temps = _parse_temps(sensors_part.strip())
        if temps:
            lines.append("  Temps:")
            for label, val in temps:
                flag = " WARNING" if val >= THERMAL_WARN_C else ""
                lines.append(f"    {label}: {val:.1f}°C{flag}")
        else:
            lines.append("  Temps: (no data)")

        # Memory
        used_b, total_b = _parse_mem(free_part)
        if total_b > 0:
            pct = used_b * 100 // total_b
            lines.append(f"  Mem: {used_b // (1024**3)}GB / {total_b // (1024**3)}GB ({pct}%)")
        else:
            lines.append("  Mem: (no data)")

        # Disk
        mounts = _parse_df(df_part.strip())
        if mounts:
            lines.append("  Disk:")
            for mount, pct in mounts:
                flag = f" WARNING ({pct}% used)" if pct >= DISK_WARN_PCT else ""
                lines.append(f"    {mount}: {pct}%{flag}")
        else:
            lines.append("  Disk: (no data)")

        # nproc
        nproc = nproc_part.strip()
        if nproc.isdigit():
            lines.append(f"  CPUs: {nproc}")

    # Containers on amarillo
    containers_r: SSHResult = await ssh.run("amarillo", "docker ps --format json")
    lines.append("\n--- Containers (amarillo) ---")
    if containers_r.returncode == 0 and containers_r.stdout.strip():
        for raw_line in containers_r.stdout.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
                name = obj.get("Names", obj.get("Name", "?"))
                status = obj.get("Status", "?")
                lines.append(f"  {name}  —  {status}")
            except json.JSONDecodeError:
                lines.append(f"  {raw_line}")
    else:
        lines.append("  (none or unreachable)")

    # Tailscale
    ts_r: SSHResult = await ssh.run("amarillo", "tailscale status --json")
    lines.append("\n--- Tailscale ---")
    if ts_r.returncode == 0:
        try:
            ts_data = json.loads(ts_r.stdout)
            self_info = ts_data.get("Self", {})
            if self_info:
                name = self_info.get("HostName", "?")
                ips = ", ".join(self_info.get("TailscaleIPs", []))
                online = "online" if self_info.get("Online") else "offline"
                lines.append(f"  self: {name} [{ips}] {online}")
            peers: dict[str, Any] = ts_data.get("Peer", ts_data.get("Peers", {}))
            for peer_data in peers.values():
                p_name = peer_data.get("HostName", "?")
                p_ips = ", ".join(peer_data.get("TailscaleIPs", []))
                p_online = "online" if peer_data.get("Online") else "offline"
                lines.append(f"  peer: {p_name} [{p_ips}] {p_online}")
        except (json.JSONDecodeError, AttributeError):
            lines.append("  (could not parse tailscale JSON)")
    else:
        lines.append("  (error or unreachable)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def handle(
    args: dict[str, Any],
    ssh: SSHExecutor | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the appropriate monitor action handler.

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
        return _text("monitor: 'action' is required")

    if action == "overview":
        return _text(await _overview(ssh))
    if action == "containers":
        return _text(await _containers(args, ssh))
    if action == "thermals":
        return _text(await _thermals(ssh))
    if action == "disk":
        return _text(await _disk(ssh))
    if action == "gpu":
        return _text(await _gpu(ssh))
    if action == "services":
        return _text(await _services())
    if action == "tailscale":
        return _text(await _tailscale(ssh))

    return _text(f"monitor: unknown action '{action}'")


# ---------------------------------------------------------------------------
# Register with the tool registry
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
