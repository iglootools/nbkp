"""
Subprocess-based SSH remote command execution (OpenSSH CLI).

Test-only counterpart of ``fabricssh.run_remote_command``: integration tests
run the same commands through both transports to cross-check them.
"""

import shlex
import subprocess

from nbkp.config import SshEndpoint
from nbkp.remote.ssh import build_ssh_base_args


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run *command* on *server* through the ``ssh`` CLI and return its result.

    Each element of *command* is one argument, quoted for the remote shell.
    *stdin* is fed to the command; a non-zero exit is returned, not raised.
    """
    cmd_string = shlex.join(command)
    return subprocess.run(
        [*build_ssh_base_args(server, proxy_chain), cmd_string],
        capture_output=True,
        text=True,
        input=stdin,
        check=False,
    )
