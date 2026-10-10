"""Local/remote command dispatch based on volume type."""

from __future__ import annotations

import shutil
import subprocess

from ..config import LocalVolume, RemoteVolume, Volume
from .endpoints import ResolvedEndpoints
from .fabricssh import run_remote_command


def run_on_volume(
    cmd: list[str],
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints,
    *,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on the volume's host (local or remote)."""
    match volume:
        case RemoteVolume():
            ep = resolved_endpoints[volume.slug]
            return run_remote_command(ep.server, cmd, ep.proxy_chain, input=input)
        case LocalVolume():
            # input=None leaves stdin inherited, as when it is omitted.
            return subprocess.run(
                cmd, capture_output=True, text=True, input=input, check=False
            )


def check_command_available(
    volume: Volume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
) -> bool:
    """Whether *command* is on the ``PATH`` of the volume's host."""
    match volume:
        case LocalVolume():
            return shutil.which(command) is not None
        case RemoteVolume():
            return (
                run_on_volume(["which", command], volume, resolved_endpoints).returncode
                == 0
            )
