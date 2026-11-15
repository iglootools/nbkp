"""Tests for nbkp.remote.dispatch: routing a command to the volume's host.

The transport is the subject here, so these are the tests that patch it;
callers of ``run_on_volume`` take a fake ``VolumeCommandRunner`` instead.
"""

import subprocess
from unittest.mock import MagicMock, patch

from nbkp.config import LocalVolume, RemoteVolume, SshEndpoint
from nbkp.remote.dispatch import run_on_volume
from nbkp.remote.endpoints import ResolvedEndpoint

_NAS = SshEndpoint(slug="nas", host="nas.local")
_BASTION = SshEndpoint(slug="bastion", host="bastion.example.com")
_REMOTE = RemoteVolume(slug="backup", ssh_endpoint="nas", path="/backup")
_RESOLVED = {"backup": ResolvedEndpoint(server=_NAS, proxy_chain=[_BASTION])}


class TestRunOnVolume:
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_remote_runs_over_the_resolved_endpoint(
        self, mock_remote: MagicMock
    ) -> None:
        mock_remote.return_value = subprocess.CompletedProcess([], 0, "out", "")

        result = run_on_volume(["ls", "/backup"], _REMOTE, _RESOLVED, input="pw")

        assert result.stdout == "out"
        mock_remote.assert_called_once_with(
            _NAS, ["ls", "/backup"], [_BASTION], input="pw"
        )

    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_local_runs_a_subprocess(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "out", "")
        vol = LocalVolume(slug="data", path="/mnt/data")

        result = run_on_volume(["ls", "/mnt/data"], vol, {})

        assert result.stdout == "out"
        mock_run.assert_called_once_with(
            ["ls", "/mnt/data"],
            capture_output=True,
            text=True,
            input=None,
            check=False,
        )
