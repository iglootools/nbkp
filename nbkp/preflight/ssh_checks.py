"""Standalone SSH endpoint observation.

Probes SSH endpoints that are not volume-referenced (bastions, alternate
endpoints, orphan endpoints) for SSH reachability only.  No tools
(rsync, btrfs, etc.) run on them, so only SSH connectivity is checked.

Separate from ``volume_checks.py`` because these endpoints have no
volume involvement — there is no ``Volume`` to dispatch on.
"""

from __future__ import annotations

from ..config import SshEndpoint
from ..remote import run_remote_command
from .status import SshEndpointDiagnostics


def observe_standalone_endpoint(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint],
) -> SshEndpointDiagnostics:
    """Observe an SSH endpoint: test SSH reachability only.

    Used for endpoints not tied to a volume (bastions, alternate
    endpoints, orphan endpoints).  Runs ``true`` to verify the SSH
    connection works.  No host tools or mount tools are probed.

    Returns ``SshEndpointDiagnostics(ssh_reachable=True)`` on success
    (with ``host_tools=None``), which ``_ssh_endpoint_errors()`` interprets
    as no errors (active, no tool requirements).

    Returns ``SshEndpointDiagnostics(ssh_reachable=False)`` on SSH failure.
    """
    try:
        run_remote_command(server, ["true"], proxy_chain)
        return SshEndpointDiagnostics(ssh_reachable=True)
    except Exception:
        return SshEndpointDiagnostics(ssh_reachable=False)
