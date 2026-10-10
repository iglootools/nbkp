"""Subprocess-based SSH remote command execution (OpenSSH CLI).

Test-only counterpart of ``fabricssh.run_remote_command``: integration tests
run the same commands through both transports to cross-check them.
"""

from __future__ import annotations

import shlex
import subprocess

from ...config import SshEndpoint
from ..ssh import build_ssh_base_args


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via SSH."""
    cmd_string = shlex.join(command)
    return subprocess.run(
        [*build_ssh_base_args(server, proxy_chain), cmd_string],
        capture_output=True,
        text=True,
        input=input,
        check=False,
    )
