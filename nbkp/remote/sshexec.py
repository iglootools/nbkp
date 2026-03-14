"""Subprocess-based SSH remote command execution."""

from __future__ import annotations

import shlex
import subprocess

from ..config import SshEndpoint
from .ssh import build_ssh_base_args


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via SSH."""
    cmd_string = " ".join(shlex.quote(arg) for arg in command)
    return subprocess.run(
        [*build_ssh_base_args(server, proxy_chain), cmd_string],
        capture_output=True,
        text=True,
        input=input,
    )
