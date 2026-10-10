"""Classification of SSH/transport exceptions.

Remote commands go through Fabric/Paramiko, which signal a failed connection
with a variety of exception types.  Callers that turn a connection failure
into a *status* (``UNREACHABLE``, a failed ``MountResult``) must catch only
these — a bare ``except Exception`` would also swallow programming errors
and misreport them as an unreachable host.

Authentication and host-key failures are split out: the host answered, but
refused us.  That is a configuration problem the operator has to fix, not
the transient "drive/host not around right now" condition that ``UNREACHABLE``
models, so preflight reports it as a separate, non-inactive error.
"""

from __future__ import annotations

import invoke.exceptions  # type: ignore[import-untyped]
import paramiko

SSH_AUTH_ERRORS: tuple[type[BaseException], ...] = (
    paramiko.AuthenticationException,
    paramiko.BadHostKeyException,
)
"""The host was reached but refused the connection (bad key, unknown or
changed host key).  Subclasses of ``paramiko.SSHException`` — catch these
first."""

SSH_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,  # socket timeouts, DNS (gaierror), refused, NoValidConnectionsError
    EOFError,  # server closed the connection during the handshake
    paramiko.SSHException,  # protocol errors, includes SSH_AUTH_ERRORS
    invoke.exceptions.Failure,
    invoke.exceptions.ThreadException,
)
"""Everything that means "the command could not be run on the host"."""


def is_auth_error(error: BaseException) -> bool:
    """Whether *error* is an authentication / host-key refusal."""
    return isinstance(error, SSH_AUTH_ERRORS)


def describe_error(error: BaseException) -> str:
    """First non-blank line of the exception message, or its type name."""
    return next(
        (line.strip() for line in str(error).splitlines() if line.strip()),
        type(error).__name__,
    )
