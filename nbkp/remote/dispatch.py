"""Local/remote command dispatch based on volume type."""

from __future__ import annotations

import subprocess

from ..config import LocalVolume, RemoteVolume, ResolvedEndpoints, Volume
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
            if input is not None:
                return run_remote_command(ep.server, cmd, ep.proxy_chain, input=input)
            else:
                return run_remote_command(ep.server, cmd, ep.proxy_chain)
        case LocalVolume():
            if input is not None:
                return subprocess.run(cmd, capture_output=True, text=True, input=input)
            else:
                return subprocess.run(cmd, capture_output=True, text=True)
