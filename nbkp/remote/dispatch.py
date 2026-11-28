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


def build_tool_lookup_args(command: str) -> list[str]:
    """
    Return the argument list asking a remote host's shell whether *command* exists.

    ``command -v`` is a POSIX shell builtin, so it answers on hosts that ship no
    ``which`` binary (Arch's ``base``, many container and minimal images), where
    ``which`` would exit 127 and every tool would read as missing. *command* is
    passed as ``$1``, never spliced into the script.
    """
    return ["sh", "-c", 'command -v "$1" >/dev/null 2>&1', "sh", command]


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

    A remote host is asked over SSH (:func:`build_tool_lookup_args`); the
    orchestrator looks it up in-process with *which*. Neither needs a ``which``
    binary, which some minimal distributions do not ship.
    """
    match volume:
        case LocalVolume():
            return which(command) is not None
        case RemoteVolume():
            lookup = build_tool_lookup_args(command)
            return run(lookup, volume, resolved_endpoints).returncode == 0
