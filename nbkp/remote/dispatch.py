"""
Running a command on the host that holds a volume, local or over SSH.

Callers take the runners as parameters (:class:`VolumeCommandRunner`,
:class:`RemoteCommandRunner`) so tests can pass fakes instead of patching the
transport.
"""

import shutil
import subprocess
from collections.abc import Callable
from typing import Protocol

from nbkp.config import LocalVolume, RemoteVolume, SshEndpoint, Volume
from nbkp.remote.endpoints import ResolvedEndpoints
lazy from nbkp.remote.fabricssh import run_remote_command


class RemoteCommandRunner(Protocol):
    """
    Runs *command* on *server* through *proxy_chain*.

    :func:`~nbkp.remote.fabricssh.run_remote_command` in production.
    """

    def __call__(
        self,
        server: SshEndpoint,
        command: list[str],
        proxy_chain: list[SshEndpoint] | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run *command* on *server* and return its result.

        *stdin* is fed to the command; a non-zero exit is returned, not raised.
        """
        ...


def run_on_volume(
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    stdin: str | None = None,
    run_remote: RemoteCommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run *cmd* on the host holding *volume*.

    *run_remote* runs a remote volume's command; ``None`` means
    :func:`~nbkp.remote.fabricssh.run_remote_command`, named only when called so that a
    local command never imports Fabric.
    """
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            return (run_remote or run_remote_command)(
                ep.server,
                cmd,
                ep.proxy_chain,
                stdin=stdin,
            )
        case LocalVolume():
            # stdin=None leaves stdin inherited, as when it is omitted.
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=stdin,
                check=False,
            )


class VolumeCommandRunner(Protocol):
    """
    Runs *cmd* on the host holding *volume*.

    :func:`run_on_volume` in production, a fake in tests, so callers can be exercised
    without patching the transport.
    """

    def __call__(
        self,
        cmd: list[str],
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run *cmd* on the host holding *volume* and return its result.

        *stdin* is fed to the command; a non-zero exit is returned, not raised.
        """
        ...


type WhichFn = Callable[[str], str | None]
"""Finds a command on the orchestrator's ``PATH``: :func:`shutil.which` in
production, a fake in tests."""


def check_command_available(
    volume: Volume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
    which: WhichFn = shutil.which,
) -> bool:
    """
    Whether *command* is on the ``PATH`` of the host holding *volume*.

    A remote host is asked over SSH (``which``); the orchestrator looks it up
    in-process with *which*, so a local host needs no ``which`` binary (some
    minimal distributions ship none).
    """
    match volume:
        case LocalVolume():
            return which(command) is not None
        case RemoteVolume():
            return run(["which", command], volume, resolved_endpoints).returncode == 0
