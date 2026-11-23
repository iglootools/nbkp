"""
Subprocess-based SSH remote command execution (OpenSSH CLI).

Test-only counterpart of ``fabricssh.run_remote_command``: integration tests
run the same commands through both transports to cross-check them.
"""

import subprocess

from nbkp.config import SshEndpoint
from nbkp.remote.ssh import build_ssh_base_args, shell_join


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run *command* on *server* through the ``ssh`` CLI and return its result.

    Each element of *command* is one argument, quoted for the remote shell as
    production quotes it (``shell_join``: a leading ``~`` stays expandable).
    *stdin* is fed to the command; a non-zero exit is returned, not raised.
    """
    cmd_string = shell_join(command)
    return subprocess.run(
        [*build_ssh_base_args(server, proxy_chain), cmd_string],
        capture_output=True,
        text=True,
        input=stdin,
        check=False,
    )
