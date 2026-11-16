"""Standalone SSH endpoint observation.

Probes SSH endpoints that are not volume-referenced (bastions, alternate
endpoints, orphan endpoints) for SSH reachability only.  No tools
(rsync, btrfs, etc.) run on them, so only SSH connectivity is checked.

Separate from ``volume_checks.py`` because these endpoints have no
volume involvement.
"""

from ..config import RemoteVolume, SshEndpoint
from ..remote.dispatch import VolumeCommandRunner, run_on_volume
from ..remote.endpoints import ResolvedEndpoint
from ..remote.errors import ssh_connection_errors
from .status.ssh import SshEndpointDiagnostics
from .volume_checks import unreachable_diagnostics


def observe_standalone_endpoint(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint],
    *,
    run: VolumeCommandRunner = run_on_volume,
) -> SshEndpointDiagnostics:
    """Returns ``SshEndpointDiagnostics(ssh_reachable=True)`` on success
    (with ``host_tools=None``), which ``_ssh_endpoint_errors()`` interprets
    as no errors (active, no tool requirements).

    Returns ``SshEndpointDiagnostics(ssh_reachable=False)`` with the cause
    on a connection failure; other exceptions (bugs) propagate.

    The probe is addressed to a stand-in volume named after *server* and
    resolved to it, so it goes through the same *run* as every other probe:
    :func:`remote.dispatch.run_on_volume`, which runs it on *server* through
    *proxy_chain*, unless a test injects a fake.
    """
    host = RemoteVolume(slug=server.slug, ssh_endpoint=server.slug, path="/")
    try:
        run(
            ["true"],
            host,
            {host.slug: ResolvedEndpoint(server=server, proxy_chain=proxy_chain)},
        )
        return SshEndpointDiagnostics(ssh_reachable=True)
    except ssh_connection_errors() as e:
        return unreachable_diagnostics(e)
