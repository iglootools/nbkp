"""
The exception for an SSH host key nbkp was not told to trust.

Kept apart from :mod:`.errors` because it subclasses a paramiko exception:
``errors`` defers paramiko until a remote command runs, and imports this
module lazily.
"""

import paramiko


class UnknownHostKeyError(paramiko.SSHException):
    """
    The host's key is unknown, and trusting it on first use is forbidden.

    The key is in none of the known_hosts files consulted, and
    ``strict-host-key-checking`` forbids trusting it on first use.
    """

    def __init__(self, hostname: str) -> None:
        """Build the error from the host whose key is unknown."""
        super().__init__(f"host key for {hostname} not found in known_hosts")
        self.hostname = hostname
