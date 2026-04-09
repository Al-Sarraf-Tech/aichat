"""
SSHExecutor - async SSH command runner for MCP tools.

Features:
  - Allowlist enforcement (bare names + Tailscale FQDNs)
  - Host alias expansion (amarillo to host.docker.internal)
  - Per-host circuit breaker (max_failures=3, recovery_window=30s)
  - Fan-out via asyncio.gather (run_multi)
  - Error sanitization (strips IPs, key paths, home dirs)

SSH flags used:
  -i /app/.ssh/team_key
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
  -o BatchMode=yes

The container reaches the host via host.docker.internal. Bare hostnames
(except aliases) get the .tail9bdca.ts.net suffix appended.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TAILSCALE_DOMAIN = "tail9bdca.ts.net"

_DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "amarillo",
        "dominus",
        "sentinel",
        "superemus",
        "host.docker.internal",
    }
)

# alias to resolved target (resolved target must itself be in allowed_hosts)
_HOST_ALIASES: dict[str, str] = {
    "amarillo": "host.docker.internal",
}

_SSH_FLAGS: list[str] = [
    "-i", "/app/.ssh/team_key",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
]

# Patterns used by _sanitize_ssh_error
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Private/internal IPv4 addresses
    (re.compile(r"\b(10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"), "<ip-redacted>"),
    # host.docker.internal
    (re.compile(r"host\.docker\.internal"), "<internal-host>"),
    # /app/.ssh/... key paths
    (re.compile(r"/app/\.ssh/[^\s:]+"), "<key-path>"),
    # /home/<user>/... paths
    (re.compile(r"/home/[^/\s]+(?:/[^\s]*)?"), "<home-path>"),
    # /root/... paths
    (re.compile(r"/root(?:/[^\s]*)?"), "<home-path>"),
]


# ---------------------------------------------------------------------------
# SSHResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class SSHResult:
    """Result of an SSH command execution."""

    stdout: str
    stderr: str
    returncode: int
    host: str
    elapsed: float


# ---------------------------------------------------------------------------
# Circuit breaker state (per host)
# ---------------------------------------------------------------------------

@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None  # timestamp when circuit opened


# ---------------------------------------------------------------------------
# SSHExecutor
# ---------------------------------------------------------------------------

class SSHExecutor:
    """Execute commands on remote hosts via SSH with circuit breaker protection."""

    def __init__(
        self,
        allowed_hosts: Optional[set[str]] = None,
        max_failures: int = 3,
        recovery_window: float = 30.0,
    ) -> None:
        """
        Args:
            allowed_hosts: Override the default allowlist. If None, uses the
                           built-in default of 5 hosts.
            max_failures:  Number of consecutive failures before opening the
                           circuit (default 3).
            recovery_window: Seconds after opening before auto-recovery
                             (default 30).
        """
        if allowed_hosts is not None:
            self._allowed: frozenset[str] = frozenset(h.lower() for h in allowed_hosts)
            # Build per-executor alias map restricted to provided allowed set
            self._aliases: dict[str, str] = {
                k: v for k, v in _HOST_ALIASES.items()
                if k in self._allowed or v in self._allowed
            }
        else:
            self._allowed = _DEFAULT_ALLOWED_HOSTS
            self._aliases = _HOST_ALIASES.copy()

        self._max_failures = max_failures
        self._recovery_window = recovery_window
        self._circuits: dict[str, _CircuitState] = {}

    # ------------------------------------------------------------------
    # Allowlist
    # ------------------------------------------------------------------

    def is_host_allowed(self, host: str) -> bool:
        """Return True if *host* is in the allowlist (after normalization).

        Accepts:
          - bare names in the allowlist (case-insensitive)
          - aliases (e.g. 'amarillo')
          - Tailscale FQDNs: <name>.<_TAILSCALE_DOMAIN>
            where <name> is in the allowlist

        Rejects anything else, including subdomain-confusion attempts like
        'amarillo.ts.net.evil.com'.
        """
        normalized = host.lower().strip()

        # Direct match
        if normalized in self._allowed:
            return True

        # Alias match
        if normalized in self._aliases:
            return True

        # Tailscale FQDN: must end with exactly '.<_TAILSCALE_DOMAIN>'
        ts_suffix = f".{_TAILSCALE_DOMAIN}"
        if normalized.endswith(ts_suffix):
            bare = normalized[: -len(ts_suffix)]
            if "." not in bare and bare in self._allowed:
                return True
            if "." not in bare and bare in self._aliases:
                return True

        return False

    # ------------------------------------------------------------------
    # Host resolution
    # ------------------------------------------------------------------

    def _resolve_host(self, host: str) -> str:
        """Resolve *host* to the actual target address.

        Resolution rules (applied in order):
          1. If host is an alias key -> return alias value
          2. If host already contains a dot -> return unchanged (already FQDN)
          3. Otherwise append Tailscale suffix
        """
        lower = host.lower().strip()

        if lower in self._aliases:
            return self._aliases[lower]

        if "." in host:
            return host

        return f"{lower}.{_TAILSCALE_DOMAIN}"

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _circuit_state(self, host: str) -> _CircuitState:
        """Return (creating if necessary) the circuit state for *host*."""
        lower = host.lower()
        if lower not in self._circuits:
            self._circuits[lower] = _CircuitState()
        return self._circuits[lower]

    def _is_circuit_open(self, host: str) -> bool:
        """Return True if the circuit for *host* is currently open."""
        state = self._circuit_state(host)
        if state.opened_at is None:
            return False
        # Auto-recover after recovery_window seconds
        if time.monotonic() - state.opened_at >= self._recovery_window:
            # Reset circuit
            state.failures = 0
            state.opened_at = None
            return False
        return True

    def _record_failure(self, host: str) -> None:
        """Record a failure for *host*, opening the circuit if threshold reached."""
        state = self._circuit_state(host)
        state.failures += 1
        if state.failures >= self._max_failures and state.opened_at is None:
            state.opened_at = time.monotonic()

    def _record_success(self, host: str) -> None:
        """Record a success for *host*, resetting the circuit."""
        state = self._circuit_state(host)
        state.failures = 0
        state.opened_at = None

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, host: str, command: str, timeout: float = 30.0) -> SSHResult:
        """Run *command* on *host* via SSH.

        Raises:
            ValueError: if the host is not in the allowlist.
            RuntimeError: if the circuit breaker is open for this host.

        Args:
            host: Target host name (must be in the allowlist).
            command: Shell command to execute on the remote host.
            timeout: Seconds to wait for the command to complete (default 30.0).
                     On timeout the subprocess is killed and a circuit breaker
                     failure is recorded.

        Returns:
            SSHResult with stdout, stderr, returncode, host, and elapsed time.
        """
        if not self.is_host_allowed(host):
            raise ValueError(f"Host {host!r} is not allowed")

        if self._is_circuit_open(host):
            raise RuntimeError(
                f"Circuit open for host {host!r} - too many recent failures"
            )

        target = self._resolve_host(host)
        cmd_args = ["ssh", *_SSH_FLAGS, target, command]

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                elapsed = time.monotonic() - start
                self._record_failure(host)
                return SSHResult(
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    returncode=-1,
                    host=host,
                    elapsed=elapsed,
                )
            elapsed = time.monotonic() - start

            returncode = proc.returncode if proc.returncode is not None else -1

            if returncode == 0:
                self._record_success(host)
            else:
                self._record_failure(host)

            return SSHResult(
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                returncode=returncode,
                host=host,
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            self._record_failure(host)
            sanitized = self._sanitize_ssh_error(str(exc))
            return SSHResult(
                stdout="",
                stderr=sanitized,
                returncode=-1,
                host=host,
                elapsed=elapsed,
            )

    # ------------------------------------------------------------------
    # run_multi()
    # ------------------------------------------------------------------

    async def run_multi(
        self, hosts: list[str], command: str, timeout: float = 30.0
    ) -> dict[str, SSHResult]:
        """Run *command* on multiple *hosts* concurrently via asyncio.gather.

        Individual host failures are captured and returned as SSHResult entries
        with returncode=-1; the method itself never raises.

        Args:
            hosts: List of host names to run the command on concurrently.
            command: Shell command to execute on each remote host.
            timeout: Per-host timeout in seconds passed through to run()
                     (default 30.0).

        Returns:
            Dict mapping host name to SSHResult.
        """

        async def _safe_run(host: str) -> tuple[str, SSHResult]:
            try:
                result = await self.run(host, command, timeout=timeout)
            except Exception as exc:
                result = SSHResult(
                    stdout="",
                    stderr=self._sanitize_ssh_error(str(exc)),
                    returncode=-1,
                    host=host,
                    elapsed=0.0,
                )
            return host, result

        pairs = await asyncio.gather(*(_safe_run(h) for h in hosts))
        return dict(pairs)

    # ------------------------------------------------------------------
    # Error sanitization
    # ------------------------------------------------------------------

    def _sanitize_ssh_error(self, message: str) -> str:
        """Strip sensitive details from SSH error messages.

        Removes:
          - Internal IP addresses (10.x, 172.16-31.x, 192.168.x)
          - host.docker.internal references
          - SSH key file paths (/app/.ssh/...)
          - Home directory paths (/home/<user>/..., /root/...)
        """
        result = message
        for pattern, replacement in _REDACT_PATTERNS:
            result = pattern.sub(replacement, result)
        return result
