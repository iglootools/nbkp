"""
Tests for nbkp.remote.dispatch: routing a command to the volume's host.

A local command runs as a real child process; a remote one goes to a
recording ``run_remote``. Callers of ``run_on_volume`` take a fake
``VolumeCommandRunner`` instead.
"""

import subprocess
import sys
from dataclasses import dataclass

from nbkp.config import LocalVolume, RemoteVolume, SshEndpoint
from nbkp.remote.dispatch import (
    build_tool_lookup_args,
    check_command_available,
    run_on_volume,
)
from nbkp.remote.endpoints import ResolvedEndpoint, ResolvedEndpoints
from tests.fakes import FakeCommandRunner, completed

_NAS = SshEndpoint(slug="nas", host="nas.local")
_BASTION = SshEndpoint(slug="bastion", host="bastion.example.com")
_REMOTE = RemoteVolume(slug="backup", ssh_endpoint="nas", path="/backup")
_RESOLVED = {"backup": ResolvedEndpoint(server=_NAS, proxy_chain=[_BASTION])}


@dataclass(frozen=True)
class _RemoteCall:
    server: SshEndpoint
    command: list[str]
    proxy_chain: list[SshEndpoint] | None
    stdin: str | None


class _RecordingRemote:
    def __init__(self) -> None:
        self.calls: list[_RemoteCall] = []

    def __call__(
        self,
        server: SshEndpoint,
        command: list[str],
        proxy_chain: list[SshEndpoint] | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(_RemoteCall(server, command, proxy_chain, stdin))
        return completed(stdout="out")


class TestRunOnVolume:
    def test_remote_runs_over_the_resolved_endpoint(self) -> None:
        remote = _RecordingRemote()

        result = run_on_volume(
            ["ls", "/backup"],
            _REMOTE,
            _RESOLVED,
            stdin="pw",
            run_remote=remote,
        )

        assert result.stdout == "out"
        assert remote.calls == [_RemoteCall(_NAS, ["ls", "/backup"], [_BASTION], "pw")]

    def test_local_runs_a_subprocess(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        echo_upper = "import sys; s = sys.stdin.read(); print(s.upper()); sys.exit(3)"

        result = run_on_volume([sys.executable, "-c", echo_upper], vol, {}, stdin="pw")

        assert result.returncode == 3
        assert result.stdout == "PW\n"
        assert result.stderr == ""

    def test_local_captures_stderr_apart(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        to_stderr = "import sys; sys.stderr.write('oops')"

        result = run_on_volume([sys.executable, "-c", to_stderr], vol, {})

        assert result.stdout == ""
        assert result.stderr == "oops"


def _remote_volume() -> tuple[RemoteVolume, ResolvedEndpoints]:
    server = SshEndpoint(slug="nas-server", host="nas.local")
    vol = RemoteVolume(slug="nas", ssh_endpoint="nas-server", path="/backup")
    return vol, {"nas": ResolvedEndpoint(server=server)}


class TestCheckCommandAvailableLocal:
    """The orchestrator looks tools up in-process: no ``which`` subprocess."""

    def test_command_found(self) -> None:
        looked_up: list[str] = []
        run = FakeCommandRunner()
        vol = LocalVolume(slug="data", path="/mnt/data")

        def which(tool: str) -> str | None:
            looked_up.append(tool)
            return f"/usr/bin/{tool}"

        assert check_command_available(vol, "rsync", {}, run=run, which=which)
        assert looked_up == ["rsync"]
        assert run.commands == []

    def test_command_not_found(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert (
            check_command_available(vol, "rsync", {}, which=lambda _tool: None) is False
        )


class TestCheckCommandAvailableRemote:
    def test_command_found(self) -> None:
        run = FakeCommandRunner().on("sh", "-c", returns=completed(0))
        vol, resolved = _remote_volume()
        assert check_command_available(vol, "rsync", resolved, run=run) is True
        assert run.commands == [build_tool_lookup_args("rsync")]
        assert run.calls[0].volume == vol

    def test_command_not_found(self) -> None:
        run = FakeCommandRunner().on("sh", "-c", returns=completed(1))
        vol, resolved = _remote_volume()
        assert check_command_available(vol, "btrfs", resolved, run=run) is False
        assert run.commands == [build_tool_lookup_args("btrfs")]
