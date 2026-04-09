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
FLEET_HOSTS: list[str] = ["amarillo", "dominus"]

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

    # Chips/features to skip (noise: board sensors with bogus values)
    _SKIP_FEATURES = {"AUXTIN", "PECI", "PCH", "Calibration"}

    for chip_name, chip_data in data.items():
        if not isinstance(chip_data, dict):
            continue
        for feature_name, feature_data in chip_data.items():
            if not isinstance(feature_data, dict):
                continue
            # Skip noisy board sensor features
            if any(skip in feature_name for skip in _SKIP_FEATURES):
                continue
            for subkey, value in feature_data.items():
                if subkey.startswith("temp") and subkey.endswith("_input") and isinstance(value, (int, float)):
                    if value < -40 or value > 150:
                        continue
                    label = f"{chip_name}/{feature_name}"
                    results.append((label, float(value)))

    return results


def _summarize_temps(temps: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Reduce full temp list to key readings: CPU package, GPU, NVMe composites.

    Skips individual cores, individual NVMe sensors, NIC temps, board sensors.
    """
    summary: list[tuple[str, float]] = []
    for label, val in temps:
        lower = label.lower()
        # CPU package temp (not individual cores)
        if "package" in lower:
            summary.append(("CPU", val))
        # GPU temp
        elif "i915" in lower or "amdgpu" in lower:
            summary.append(("GPU", val))
        # NVMe composite only (not Sensor 1/2/8)
        elif "nvme" in lower and "composite" in lower:
            # Use last path component for drive identity
            drive = label.split("/")[0].replace("nvme-pci-", "NVMe ")
            summary.append((drive, val))
    # If nothing matched (e.g., different sensor layout), return max temp
    if not summary and temps:
        max_temp = max(temps, key=lambda t: t[1])
        summary.append(("max", max_temp[1]))
    return summary


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
            # Skip virtual/pseudo filesystems
            _SKIP_MOUNTS = ("/dev", "/dev/shm", "/run", "/sys", "/proc",
                            "/run/credentials", "/run/user")
            if any(mount_field == s or mount_field.startswith(s + "/") for s in _SKIP_MOUNTS):
                continue
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
        # Filter out funnel-ingress-node and only show real devices
        real_peers = [p for p in peers.values() if p.get("HostName", "").lower() != "funnel-ingress-node"]
        online = sum(1 for p in real_peers if p.get("Online"))
        lines.append(f"  {online}/{len(real_peers)} peers online")
        for peer_data in real_peers:
            p_name = peer_data.get("HostName", "?")
            p_online = "online" if peer_data.get("Online") else "offline"
            lines.append(f"    {p_name}: {p_online}")
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
    # Short timeout — dominus (Windows) can't run Linux commands, will timeout gracefully
    vitals_results: dict[str, SSHResult] = await ssh.run_multi(FLEET_HOSTS, vitals_cmd, timeout=10)

    lines: list[str] = ["=== Fleet Overview ==="]

    # Windows hosts (WSL2 SSH → powershell.exe) get separate vitals
    _WINDOWS_HOSTS = {"dominus"}
    _WIN_VITALS_CMD = (
        "echo WIN_CPU=$(powershell.exe -NoProfile -Command "
        "'Get-CimInstance Win32_Processor | Select -Expand LoadPercentage') && "
        "echo WIN_CORES=$(nproc) && "
        "powershell.exe -NoProfile -Command "
        "'systeminfo | Select-String Memory'"
    )
    for wh in _WINDOWS_HOSTS:
        if wh in FLEET_HOSTS:
            try:
                wr = await ssh.run(wh, _WIN_VITALS_CMD, timeout=15)
                if wr.returncode == 0 and wr.stdout.strip():
                    vitals_results[wh] = wr
            except Exception:
                pass

    for host in FLEET_HOSTS:
        lines.append(f"\n--- {host} ---")
        if host not in vitals_results:
            lines.append("  [unreachable]")
            continue
        r = vitals_results[host]
        if r.returncode != 0 or not r.stdout.strip():
            lines.append("  [unreachable]")
            continue

        # Windows host — parse WIN_CPU, WIN_CORES, and Memory lines
        if host in _WINDOWS_HOSTS and "WIN_CPU" in r.stdout:
            parts = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("WIN_CPU="):
                    parts.append(f"CPU {line.split('=', 1)[1]}%")
                elif line.startswith("WIN_CORES="):
                    parts.append(f"Cores {line.split('=', 1)[1]}")
                elif "Total Physical Memory" in line:
                    mem_total = line.split(":")[-1].strip()
                    parts.append(f"RAM total {mem_total}")
                elif "Available Physical Memory" in line:
                    mem_free = line.split(":")[-1].strip()
                    parts.append(f"RAM free {mem_free}")
            lines.append(f"  {' | '.join(parts)}")
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

        # Thermals (summarized: CPU package, GPU, NVMe composites)
        temps = _parse_temps(sensors_part.strip())
        key_temps = _summarize_temps(temps)
        if key_temps:
            temp_parts = []
            for label, val in key_temps:
                flag = " WARNING" if val >= THERMAL_WARN_C else ""
                temp_parts.append(f"{label} {val:.0f}C{flag}")
            lines.append(f"  Temps: {' | '.join(temp_parts)}")
        else:
            lines.append("  Temps: (no data)")

        # Memory
        used_b, total_b = _parse_mem(free_part)
        if total_b > 0:
            pct = used_b * 100 // total_b
            lines.append(f"  Mem: {used_b // (1024**3)}GB / {total_b // (1024**3)}GB ({pct}%)")
        else:
            lines.append("  Mem: (no data)")

        # Disk (only mounts with >5% usage, compact format)
        mounts = _parse_df(df_part.strip())
        significant = [(m, p) for m, p in mounts if p > 5]
        if significant:
            disk_parts = []
            for mount, pct in significant:
                flag = " WARNING" if pct >= DISK_WARN_PCT else ""
                disk_parts.append(f"{mount} {pct}%{flag}")
            lines.append(f"  Disk: {' | '.join(disk_parts)}")
        else:
            lines.append("  Disk: (no data)")

        # nproc
        nproc = nproc_part.strip()
        if nproc.isdigit():
            lines.append(f"  CPUs: {nproc}")

    # Containers on amarillo — summary + flagged only
    containers_r: SSHResult = await ssh.run("amarillo", "docker ps --format json")
    lines.append("\n--- Containers (amarillo) ---")
    if containers_r.returncode == 0 and containers_r.stdout.strip():
        total = 0
        healthy = 0
        flagged: list[str] = []
        for raw_line in containers_r.stdout.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
                name = obj.get("Names", obj.get("Name", "?"))
                status = obj.get("Status", "?")
                total += 1
                status_lower = status.lower()
                if "healthy" in status_lower and "unhealthy" not in status_lower:
                    healthy += 1
                if "unhealthy" in status_lower or "restarting" in status_lower or "exited" in status_lower:
                    flagged.append(f"  {name}: {status}")
            except json.JSONDecodeError:
                pass
        lines.append(f"  {total} running, {healthy} healthy")
        if flagged:
            lines.append("  Issues:")
            lines.extend(flagged)
    else:
        lines.append("  (none or unreachable)")

    # Tailscale (real devices only, skip funnel-ingress-node)
    ts_r: SSHResult = await ssh.run("amarillo", "tailscale status --json")
    lines.append("\n--- Tailscale ---")
    if ts_r.returncode == 0:
        try:
            ts_data = json.loads(ts_r.stdout)
            peers_raw: dict[str, Any] = ts_data.get("Peer", ts_data.get("Peers", {}))
            real_peers = [p for p in peers_raw.values() if p.get("HostName", "").lower() != "funnel-ingress-node"]
            online = sum(1 for p in real_peers if p.get("Online"))
            lines.append(f"  {online}/{len(real_peers)} devices online")
            for p in real_peers:
                name = p.get("HostName", "?")
                status = "online" if p.get("Online") else "offline"
                lines.append(f"  {name}: {status}")
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
