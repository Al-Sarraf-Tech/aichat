"""
Unit tests for SSHExecutor in tools/_ssh.py.

Test groups:
  - Host allowlist (6 tests)
  - Host resolution (3 tests)
  - Circuit breaker (3 tests)
  - run() command execution (3 tests)
  - run_multi() (2 tests)
  - Error sanitization (3 tests)

Run with:
  cd ~/git/aichat
  PYTHONPATH=docker/mcp:$PYTHONPATH python -m pytest tests/tools/test_ssh_executor.py -v
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from tools._ssh import SSHExecutor, SSHResult  # type: ignore[import]


# ===========================================================================
# Helpers / constants
# ===========================================================================

_DEFAULT_ALLOWED = {"amarillo", "dominus", "sentinel", "host.docker.internal", "192.168.50.2"}


# ===========================================================================
# Host allowlist — 6 tests
# ===========================================================================

class TestHostAllowlist:
    """SSHExecutor.is_host_allowed() / allowlist behaviour."""

    def test_default_hosts_are_allowed(self):
        """All five default hosts must be in the allowlist."""
        ex = SSHExecutor()
        for host in _DEFAULT_ALLOWED:
            assert ex.is_host_allowed(host), f"{host!r} should be allowed by default"

    def test_unknown_host_is_rejected(self):
        """A host not in the allowlist must be rejected."""
        ex = SSHExecutor()
        assert not ex.is_host_allowed("attacker.example.com")

    def test_tailscale_fqdn_is_allowed(self):
        """A bare name's Tailscale FQDN (.ts.net suffix) should be resolved and allowed."""
        ex = SSHExecutor()
        # amarillo resolves to host.docker.internal; both are allowed
        assert ex.is_host_allowed("amarillo.tail9bdca.ts.net")

    def test_non_ts_net_fqdn_is_rejected(self):
        """A hostname ending in .ts.net.<extra> must be rejected (subdomain confusion)."""
        ex = SSHExecutor()
        assert not ex.is_host_allowed("amarillo.ts.net.evil.com")

    def test_custom_allowlist_overrides_default(self):
        """Constructor allowlist parameter replaces the default set."""
        ex = SSHExecutor(allowed_hosts={"myhost"})
        assert ex.is_host_allowed("myhost")
        assert not ex.is_host_allowed("amarillo")

    def test_allowlist_is_case_insensitive_for_bare_names(self):
        """Host lookup should be case-insensitive for bare hostnames."""
        ex = SSHExecutor()
        assert ex.is_host_allowed("Amarillo")
        assert ex.is_host_allowed("DOMINUS")


# ===========================================================================
# Host resolution — 3 tests
# ===========================================================================

class TestHostResolution:
    """SSHExecutor._resolve_host() — alias expansion & suffix injection."""

    def test_amarillo_resolves_to_docker_internal(self):
        """'amarillo' must resolve to 'host.docker.internal'."""
        ex = SSHExecutor()
        assert ex._resolve_host("amarillo") == "host.docker.internal"

    def test_bare_non_alias_gets_tailscale_suffix(self):
        """A bare name that is NOT an alias gets .tail9bdca.ts.net appended."""
        ex = SSHExecutor()
        # Use a name that isn't in _HOST_ALIASES
        resolved = ex._resolve_host("sentinel")
        assert resolved == "sentinel.tail9bdca.ts.net"

    def test_dominus_resolves_to_lan_ip(self):
        """dominus alias resolves to LAN IP."""
        ex = SSHExecutor()
        resolved = ex._resolve_host("dominus")
        assert resolved == "192.168.50.2"

    def test_already_qualified_hostname_is_unchanged(self):
        """A hostname that already contains a dot is passed through unchanged."""
        ex = SSHExecutor()
        fqdn = "dominus.tail9bdca.ts.net"
        assert ex._resolve_host(fqdn) == fqdn


# ===========================================================================
# Circuit breaker — 3 tests
# ===========================================================================

class TestCircuitBreaker:
    """Per-host circuit breaker with max_failures=3 and recovery_window=30s."""

    def test_circuit_opens_after_max_failures(self):
        """After 3 consecutive failures the circuit must be open."""
        ex = SSHExecutor()
        for _ in range(3):
            ex._record_failure("dominus")
        assert ex._is_circuit_open("dominus"), "Circuit should be open after 3 failures"

    def test_circuit_resets_after_success(self):
        """A success call must reset the failure counter and close the circuit."""
        ex = SSHExecutor()
        for _ in range(2):
            ex._record_failure("dominus")
        ex._record_success("dominus")
        assert not ex._is_circuit_open("dominus"), "Circuit should be closed after success"

    def test_circuit_recovers_after_window(self):
        """After recovery_window seconds the circuit should close automatically."""
        ex = SSHExecutor(recovery_window=0.1)  # short window for test speed
        for _ in range(3):
            ex._record_failure("sentinel")
        assert ex._is_circuit_open("sentinel"), "Should be open right after 3 failures"
        time.sleep(0.15)  # wait past recovery window
        assert not ex._is_circuit_open("sentinel"), "Should auto-recover after window"


# ===========================================================================
# run() — 3 tests
# ===========================================================================

class TestRunCommand:
    """SSHExecutor.run() — subprocess invocation, result parsing, circuit integration."""

    @pytest.mark.asyncio
    async def test_run_returns_ssh_result_on_success(self):
        """run() must return an SSHResult with correct fields on success."""
        ex = SSHExecutor()
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await ex.run("amarillo", "echo hello")

        assert isinstance(result, SSHResult)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.host == "amarillo"
        assert result.elapsed >= 0

    @pytest.mark.asyncio
    async def test_run_raises_on_disallowed_host(self):
        """run() must raise ValueError for a host not in the allowlist."""
        ex = SSHExecutor()
        with pytest.raises(ValueError, match="not allowed"):
            await ex.run("evil.example.com", "id")

    @pytest.mark.asyncio
    async def test_run_raises_on_open_circuit(self):
        """run() must raise RuntimeError when the circuit is open."""
        ex = SSHExecutor()
        for _ in range(3):
            ex._record_failure("sentinel")

        with pytest.raises(RuntimeError, match="[Cc]ircuit.*open|[Oo]pen.*circuit"):
            await ex.run("sentinel", "uptime")


# ===========================================================================
# run_multi() — 2 tests
# ===========================================================================

class TestRunMulti:
    """SSHExecutor.run_multi() — fan-out with asyncio.gather."""

    @pytest.mark.asyncio
    async def test_run_multi_returns_results_for_all_hosts(self):
        """run_multi() must return a dict keyed by host with SSHResult values."""
        ex = SSHExecutor()
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"pong", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            results = await ex.run_multi(["amarillo", "dominus"], "echo pong")

        assert set(results.keys()) == {"amarillo", "dominus"}
        for host, res in results.items():
            assert isinstance(res, SSHResult)
            assert res.returncode == 0

    @pytest.mark.asyncio
    async def test_run_multi_captures_per_host_exceptions(self):
        """run_multi() must not raise; failed hosts get an SSHResult with returncode=-1."""
        ex = SSHExecutor()

        async def _raise(*args, **kwargs):
            raise OSError("connection refused")

        with patch("asyncio.create_subprocess_exec", side_effect=_raise):
            # amarillo is allowed; sentinel is allowed; both will fail
            results = await ex.run_multi(["amarillo", "sentinel"], "uptime")

        # Both entries should exist with failure returncode
        assert "amarillo" in results
        assert "sentinel" in results
        for res in results.values():
            assert isinstance(res, SSHResult)
            assert res.returncode != 0


# ===========================================================================
# Error sanitization — 3 tests
# ===========================================================================

class TestErrorSanitization:
    """SSHExecutor._sanitize_ssh_error() — strips sensitive details."""

    def test_strips_internal_ip(self):
        """Internal IP addresses must be redacted."""
        ex = SSHExecutor()
        raw = "ssh: connect to host 192.168.1.5 port 22: Connection refused"
        sanitized = ex._sanitize_ssh_error(raw)
        assert "192.168" not in sanitized

    def test_strips_home_path(self):
        """Home directory paths must be redacted."""
        ex = SSHExecutor()
        raw = "Warning: Identity file /home/jalsarraf/.ssh/id_rsa not accessible"
        sanitized = ex._sanitize_ssh_error(raw)
        assert "/home/jalsarraf" not in sanitized

    def test_strips_key_path_and_docker_internal(self):
        """SSH key path and host.docker.internal must be redacted."""
        ex = SSHExecutor()
        raw = (
            "ssh: Could not resolve hostname host.docker.internal: Name or service not known\n"
            "Identity file /app/.ssh/team_key not accessible: No such file or directory"
        )
        sanitized = ex._sanitize_ssh_error(raw)
        assert "host.docker.internal" not in sanitized
        assert "/app/.ssh/team_key" not in sanitized
