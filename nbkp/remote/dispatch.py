import shutil
import subprocess
from collections.abc import Callable
from typing import Protocol

from ..config import LocalVolume, RemoteVolume, Volume
from .endpoints import ResolvedEndpoints
lazy from .fabricssh import run_remote_command


def run_on_volume(
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            return run_remote_command(ep.server, cmd, ep.proxy_chain, input=input)
        case LocalVolume():
            # input=None leaves stdin inherited, as when it is omitted.
            return subprocess.run(
                cmd, capture_output=True, text=True, input=input, check=False
            )


class VolumeCommandRunner(Protocol):
    """Runs *cmd* on the host holding *volume*: :func:`run_on_volume` in
    production, a fake in tests, so callers can be exercised without
    patching the transport.
    """

    def __call__(
        self,
        cmd: list[str],
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


type CommandLocator = Callable[[str], str | None]
"""Finds a command on the orchestrator's ``PATH``: :func:`shutil.which` in
production, a fake in tests."""


def check_command_available(
    volume: Volume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
    *,
    run: VolumeCommandRunner = run_on_volume,
    which: CommandLocator = shutil.which,
) -> bool:
    """Whether *command* is on the ``PATH`` of the host holding *volume*.

    A remote host is asked over SSH (``which``); the orchestrator looks it up
    in-process with *which*, so a local host needs no ``which`` binary (some
    minimal distributions ship none).
    """
    match volume:
        case LocalVolume():
            return which(command) is not None
        case RemoteVolume():
            return run(["which", command], volume, resolved_endpoints).returncode == 0
