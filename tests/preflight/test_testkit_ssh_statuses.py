"""
The demo's SSH Endpoints table lists endpoints in order of first appearance.

``collect_ssh_endpoint_statuses`` builds that table's rows for the ``check``
demo data, keeping the first status seen for each endpoint.
"""

from nbkp.preflight import SshEndpointDiagnostics, SshEndpointStatus, VolumeStatus
from nbkp.preflight.testkit.check import check_config
from nbkp.preflight.testkit.common import (
    collect_ssh_endpoint_statuses,
    standalone_ssh_status,
)


def _volume_status(volume: str, ssh: SshEndpointStatus) -> VolumeStatus:
    return VolumeStatus(
        slug=volume,
        config=check_config().volumes[volume],
        ssh_endpoint_status=ssh,
        diagnostics=None,
        errors=[],
    )


def test_endpoints_are_listed_in_order_of_first_appearance() -> None:
    vol_statuses = {
        "laptop": _volume_status("laptop", standalone_ssh_status("localhost")),
        "nas-backup": _volume_status("nas-backup", standalone_ssh_status("nas")),
    }

    statuses = collect_ssh_endpoint_statuses(vol_statuses, {})

    assert list(statuses) == ["localhost", "nas"]


def test_the_first_status_seen_for_an_endpoint_wins() -> None:
    first = standalone_ssh_status("localhost")
    later = SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
    )
    vol_statuses = {
        "laptop": _volume_status("laptop", first),
        "laptop-system": _volume_status("laptop-system", later),
    }

    statuses = collect_ssh_endpoint_statuses(vol_statuses, {})

    assert statuses == {"localhost": first}
