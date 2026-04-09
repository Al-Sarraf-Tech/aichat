"""
Unit tests for tools/monitor.py — fleet monitoring MCP tool.

Test groups:
  - overview (1 test)
  - thermals: normal (1), warning above 85C (1)
  - containers: lists running (1)
  - disk: shows usage (1), warns above 85% (1)
  - gpu (1)
  - tailscale (1)
  - services (1)
  - unreachable host (1)
  - unknown action (1)

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_monitor.py -v
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tools._ssh import SSHResult  # type: ignore[import]


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _sensors_json():
    return json.dumps({"coretemp-isa-0000": {"Package id 0": {"temp1_input": 52.0}}})


def _sensors_json_hot():
    return json.dumps({"coretemp-isa-0000": {"Package id 0": {"temp1_input": 91.0}}})


def _free_output():
    return (
        "              total        used        free\n"
        "Mem:    33740025856 13186588672 20553437184\n"
    )


def _df_output():
    return (
        "Filesystem     1B-blocks       Used  Available Use% Mounted on\n"
        "/dev/sda1  500000000000 210000000000 290000000000  42% /\n"
    )


def _df_output_warn():
    return (
        "Filesystem     1B-blocks       Used  Available Use% Mounted on\n"
        "/dev/sda1  500000000000 440000000000  60000000000  88% /\n"
    )


def _docker_ps_json():
    return '{"Names":"aichat-aichat-mcp-1","Status":"Up 2 hours (healthy)"}\n'


def _tailscale_json():
    return json.dumps({
        "Self": {
            "HostName": "amarillo",
            "Online": True,
            "TailscaleIPs": ["100.64.0.1"],
        },
        "Peer": {
            "abc": {
                "HostName": "dominus",
                "Online": True,
                "TailscaleIPs": ["100.64.0.2"],
                "OS": "windows",
            },
        },
    })


def _vitals_output():
    """Compound vitals output as produced by the overview command."""
    sensors = _sensors_json()
    free_out = _free_output()
    df_out = _df_output()
    return f"{sensors}\n---FREE---\n{free_out}---DF---\n{df_out}---NPROC---\n8\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ssh(mock_ssh):  # noqa: F811 — shadow shared fixture to add monitor defaults
    return mock_ssh


# ===========================================================================
# overview — 1 test
# ===========================================================================


class TestOverviewAction:
    """handle() with action='overview'."""

    @pytest.mark.asyncio
    async def test_overview_returns_fleet_data(self, mock_ssh):
        """overview must return combined vitals, containers, and tailscale data."""
        from tools.monitor import handle  # type: ignore[import]

        vitals_result = SSHResult(
            stdout=_vitals_output(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        containers_result = SSHResult(
            stdout=_docker_ps_json(), stderr="", returncode=0, host="amarillo", elapsed=0.05
        )
        tailscale_result = SSHResult(
            stdout=_tailscale_json(), stderr="", returncode=0, host="amarillo", elapsed=0.05
        )

        mock_ssh.run_multi.return_value = {"amarillo": vitals_result}
        mock_ssh.run.side_effect = [containers_result, tailscale_result]

        result = await handle({"action": "overview"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        # Should contain fleet overview data
        assert len(text) > 0
        # Should mention thermals or memory or containers
        assert any(kw in text.lower() for kw in ("temp", "mem", "container", "amarillo"))


# ===========================================================================
# thermals — 2 tests
# ===========================================================================


class TestThermalsAction:
    """handle() with action='thermals'."""

    @pytest.mark.asyncio
    async def test_thermals_normal(self, mock_ssh):
        """thermals must report temperatures across fleet hosts."""
        from tools.monitor import handle  # type: ignore[import]

        result_ok = SSHResult(
            stdout=_sensors_json(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        mock_ssh.run_multi.return_value = {
            "amarillo": result_ok,
            "dominus": SSHResult(stdout=_sensors_json(), stderr="", returncode=0, host="dominus", elapsed=0.1),
            "sentinel": SSHResult(stdout=_sensors_json(), stderr="", returncode=0, host="sentinel", elapsed=0.1),
            "superemus": SSHResult(stdout=_sensors_json(), stderr="", returncode=0, host="superemus", elapsed=0.1),
        }

        result = await handle({"action": "thermals"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "52" in text  # temperature value present
        # no warnings expected
        assert "WARNING" not in text

    @pytest.mark.asyncio
    async def test_thermals_warning_above_85c(self, mock_ssh):
        """thermals must flag WARNING when a temperature exceeds 85C."""
        from tools.monitor import handle  # type: ignore[import]

        hot_result = SSHResult(
            stdout=_sensors_json_hot(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        mock_ssh.run_multi.return_value = {"amarillo": hot_result}

        result = await handle({"action": "thermals"}, mock_ssh)

        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "WARNING" in text
        assert "91" in text


# ===========================================================================
# containers — 1 test
# ===========================================================================


class TestContainersAction:
    """handle() with action='containers'."""

    @pytest.mark.asyncio
    async def test_containers_lists_running(self, mock_ssh):
        """containers must list running container names and statuses."""
        from tools.monitor import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout=_docker_ps_json(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )

        result = await handle({"action": "containers"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "aichat-aichat-mcp-1" in text
        assert "Up" in text


# ===========================================================================
# disk — 2 tests
# ===========================================================================


class TestDiskAction:
    """handle() with action='disk'."""

    @pytest.mark.asyncio
    async def test_disk_shows_usage(self, mock_ssh):
        """disk must report filesystem usage percentages."""
        from tools.monitor import handle  # type: ignore[import]

        result_ok = SSHResult(
            stdout=_df_output(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        mock_ssh.run_multi.return_value = {
            "amarillo": result_ok,
        }

        result = await handle({"action": "disk"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "42" in text
        assert "/" in text

    @pytest.mark.asyncio
    async def test_disk_warns_above_85pct(self, mock_ssh):
        """disk must flag WARNING when usage exceeds 85%."""
        from tools.monitor import handle  # type: ignore[import]

        warn_result = SSHResult(
            stdout=_df_output_warn(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        mock_ssh.run_multi.return_value = {"amarillo": warn_result}

        result = await handle({"action": "disk"}, mock_ssh)

        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "WARNING" in text
        assert "88" in text


# ===========================================================================
# gpu — 1 test
# ===========================================================================


class TestGpuAction:
    """handle() with action='gpu'."""

    @pytest.mark.asyncio
    async def test_gpu_returns_data(self, mock_ssh):
        """gpu must return intel_gpu_top data from amarillo."""
        from tools.monitor import handle  # type: ignore[import]

        gpu_json = json.dumps({
            "period": {"duration": 1000.0},
            "engines": {"Render/3D/0": {"busy": 12.5}},
        })
        mock_ssh.run.return_value = SSHResult(
            stdout=gpu_json, stderr="", returncode=0, host="amarillo", elapsed=0.2
        )

        result = await handle({"action": "gpu"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert len(text) > 0


# ===========================================================================
# tailscale — 1 test
# ===========================================================================


class TestTailscaleAction:
    """handle() with action='tailscale'."""

    @pytest.mark.asyncio
    async def test_tailscale_shows_peers(self, mock_ssh):
        """tailscale must display Self and Peer information."""
        from tools.monitor import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout=_tailscale_json(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )

        result = await handle({"action": "tailscale"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "amarillo" in text
        assert "dominus" in text


# ===========================================================================
# services — 1 test
# ===========================================================================


class TestServicesAction:
    """handle() with action='services'."""

    @pytest.mark.asyncio
    async def test_services_checks_endpoints(self, mock_ssh):
        """services must check all health endpoints and report status."""
        from tools.monitor import handle  # type: ignore[import]

        # Mock the HTTP check so we don't need real network
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await handle({"action": "services"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        # Should mention at least one of the known services
        assert any(svc in text.lower() for svc in ("lm studio", "comfyui", "qdrant", "aichat", "1234", "8388", "6333"))


# ===========================================================================
# unreachable host — 1 test
# ===========================================================================


class TestUnreachableHost:
    """Unreachable hosts are handled gracefully."""

    @pytest.mark.asyncio
    async def test_unreachable_host_handled(self, mock_ssh):
        """thermals action must include [unreachable] marker for non-responding hosts."""
        from tools.monitor import handle  # type: ignore[import]

        unreachable = SSHResult(
            stdout="", stderr="Connection timed out", returncode=-1, host="sentinel", elapsed=10.0
        )
        ok = SSHResult(
            stdout=_sensors_json(), stderr="", returncode=0, host="amarillo", elapsed=0.1
        )
        mock_ssh.run_multi.return_value = {
            "amarillo": ok,
            "sentinel": unreachable,
        }

        result = await handle({"action": "thermals"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "[unreachable]" in text


# ===========================================================================
# unknown action — 1 test
# ===========================================================================


class TestUnknownAction:
    """handle() with an unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_ssh):
        """An unknown action must return a descriptive error."""
        from tools.monitor import handle  # type: ignore[import]

        result = await handle({"action": "fly_a_kite"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "fly_a_kite" in result[0]["text"]
