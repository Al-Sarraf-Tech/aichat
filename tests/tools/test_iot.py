"""
Unit tests for tools/iot.py — IoT device control MCP tool.

Test groups:
  - list_devices action (1 test): returns registry
  - roku keypress (2 tests): success, missing key
  - roku launch (1 test): success with app name resolution
  - roku query (1 test): returns active app
  - roku apps (1 test): list installed apps
  - shield keypress (1 test): success via SSH
  - shield command (1 test): raw command output
  - power off roku (1 test): sends PowerOff keypress
  - unknown device (1 test): rejected
  - missing device (1 test): error when required
  - unknown action (1 test): error message

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_iot.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools._ssh import SSHResult  # type: ignore[import]


# ===========================================================================
# list_devices — 1 test
# ===========================================================================


class TestListDevices:
    """handle() with action='list_devices'."""

    @pytest.mark.asyncio
    async def test_list_devices_returns_registry(self, mock_ssh):
        """list_devices must return a text block mentioning both roku and shield."""
        from tools.iot import handle  # type: ignore[import]

        result = await handle({"action": "list_devices"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "roku" in text.lower()
        assert "shield" in text.lower()


# ===========================================================================
# roku keypress — 2 tests
# ===========================================================================


class TestRokuKeypress:
    """handle() with action='keypress', device='roku'."""

    @pytest.mark.asyncio
    async def test_roku_keypress_success(self, mock_ssh):
        """keypress on roku must POST to /keypress/{key} and return success."""
        from tools.iot import handle  # type: ignore[import]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "keypress", "device": "roku", "key": "Home"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "ok" in result[0]["text"].lower() or "home" in result[0]["text"].lower()
        client.post.assert_called_once()
        call_url = client.post.call_args[0][0]
        assert "/keypress/Home" in call_url

    @pytest.mark.asyncio
    async def test_roku_keypress_missing_key(self, mock_ssh):
        """keypress without 'key' must return an error without calling the network."""
        from tools.iot import handle  # type: ignore[import]

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "keypress", "device": "roku"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "key" in result[0]["text"].lower()
        client.post.assert_not_called()


# ===========================================================================
# roku launch — 1 test
# ===========================================================================


class TestRokuLaunch:
    """handle() with action='launch', device='roku'."""

    @pytest.mark.asyncio
    async def test_roku_launch_resolves_app_name(self, mock_ssh):
        """launch on roku must resolve app name from /query/apps and POST /launch/{id}."""
        from tools.iot import handle  # type: ignore[import]

        apps_xml = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            '<apps>'
            '<app id="12" subtype="ndka" type="appl" version="4.1.218">Netflix</app>'
            '<app id="13" subtype="ndka" type="appl" version="4.2.0">Amazon Video</app>'
            '</apps>'
        )

        mock_apps_resp = MagicMock()
        mock_apps_resp.status_code = 200
        mock_apps_resp.text = apps_xml

        mock_launch_resp = MagicMock()
        mock_launch_resp.status_code = 200
        mock_launch_resp.text = ""

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            # First call is GET /query/apps, second is POST /launch/{id}
            client.get = AsyncMock(return_value=mock_apps_resp)
            client.post = AsyncMock(return_value=mock_launch_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "launch", "device": "roku", "app": "Netflix"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"].lower()
        assert "netflix" in text or "launch" in text
        # Verify the correct app_id (12) was used in the POST
        call_url = client.post.call_args[0][0]
        assert "/launch/12" in call_url


# ===========================================================================
# roku query — 1 test
# ===========================================================================


class TestRokuQuery:
    """handle() with action='query', device='roku'."""

    @pytest.mark.asyncio
    async def test_roku_query_returns_active_app(self, mock_ssh):
        """query on roku must call /query/active-app and return text with app info."""
        from tools.iot import handle  # type: ignore[import]

        active_xml = (
            '<active-app>'
            '<app id="12" type="appl" version="4.1.218">Netflix</app>'
            '</active-app>'
        )
        device_info_xml = (
            '<device-info>'
            '<model-name>TCL 50S525</model-name>'
            '<software-version>10.0</software-version>'
            '</device-info>'
        )

        mock_active_resp = MagicMock()
        mock_active_resp.status_code = 200
        mock_active_resp.text = active_xml

        mock_info_resp = MagicMock()
        mock_info_resp.status_code = 200
        mock_info_resp.text = device_info_xml

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[mock_active_resp, mock_info_resp])
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "query", "device": "roku"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert len(result[0]["text"]) > 0


# ===========================================================================
# roku apps — 1 test
# ===========================================================================


class TestRokuApps:
    """handle() with action='apps', device='roku'."""

    @pytest.mark.asyncio
    async def test_roku_apps_lists_installed(self, mock_ssh):
        """apps on roku must parse /query/apps XML and return app names."""
        from tools.iot import handle  # type: ignore[import]

        apps_xml = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            '<apps>'
            '<app id="12" subtype="ndka" type="appl" version="4.1.218">Netflix</app>'
            '<app id="2285" subtype="ndka" type="appl" version="2.0">Hulu</app>'
            '</apps>'
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = apps_xml

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "apps", "device": "roku"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"]
        assert "Netflix" in text
        assert "Hulu" in text


# ===========================================================================
# shield keypress — 1 test
# ===========================================================================


class TestShieldKeypress:
    """handle() with action='keypress', device='shield'."""

    @pytest.mark.asyncio
    async def test_shield_keypress_success(self, mock_ssh):
        """keypress on shield must send an SSH input keyevent command."""
        from tools.iot import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="", stderr="", returncode=0, host="192.168.50.99", elapsed=0.2
        )
        mock_ssh.is_host_allowed = MagicMock(return_value=True)

        result = await handle({"action": "keypress", "device": "shield", "key": "HOME"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        mock_ssh.run.assert_called_once()
        call_kwargs = mock_ssh.run.call_args
        # command should contain KEYCODE_HOME
        cmd_arg = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("command", "")
        assert "KEYCODE_HOME" in cmd_arg


# ===========================================================================
# shield command — 1 test
# ===========================================================================


class TestShieldCommand:
    """handle() with action='command', device='shield'."""

    @pytest.mark.asyncio
    async def test_shield_command_returns_output(self, mock_ssh):
        """command on shield must run via SSH and return stdout."""
        from tools.iot import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="dumpsys output here\n",
            stderr="",
            returncode=0,
            host="192.168.50.99",
            elapsed=0.3,
        )
        mock_ssh.is_host_allowed = MagicMock(return_value=True)

        result = await handle(
            {"action": "command", "device": "shield", "command": "dumpsys media_session"},
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "dumpsys output here" in result[0]["text"]
        mock_ssh.run.assert_called_once()


# ===========================================================================
# power off roku — 1 test
# ===========================================================================


class TestPowerAction:
    """handle() with action='power'."""

    @pytest.mark.asyncio
    async def test_power_off_roku_sends_poweroff(self, mock_ssh):
        """power off on roku must POST /keypress/PowerOff via ECP."""
        from tools.iot import handle  # type: ignore[import]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = ""

        with patch("tools.iot.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle({"action": "power", "device": "roku", "key": "off"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        call_url = client.post.call_args[0][0]
        assert "/keypress/PowerOff" in call_url


# ===========================================================================
# unknown device — 1 test
# ===========================================================================


class TestUnknownDevice:
    """handle() with an unrecognised device name."""

    @pytest.mark.asyncio
    async def test_unknown_device_rejected(self, mock_ssh):
        """An action with an unknown device name must return an error."""
        from tools.iot import handle  # type: ignore[import]

        result = await handle({"action": "keypress", "device": "toaster", "key": "Home"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        text = result[0]["text"].lower()
        assert "toaster" in text or "unknown" in text or "device" in text


# ===========================================================================
# missing device — 1 test
# ===========================================================================


class TestMissingDevice:
    """handle() with an action that requires a device but none given."""

    @pytest.mark.asyncio
    async def test_missing_device_returns_error(self, mock_ssh):
        """keypress without 'device' must return an error."""
        from tools.iot import handle  # type: ignore[import]

        result = await handle({"action": "keypress", "key": "Home"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "device" in result[0]["text"].lower()


# ===========================================================================
# unknown action — 1 test
# ===========================================================================


class TestUnknownAction:
    """handle() with an unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_ssh):
        """An unrecognised action must return a descriptive error message."""
        from tools.iot import handle  # type: ignore[import]

        result = await handle({"action": "fly_to_moon", "device": "roku"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "fly_to_moon" in result[0]["text"] or "unknown" in result[0]["text"].lower()
