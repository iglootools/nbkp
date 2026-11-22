"""The combined result of every preflight phase."""

from dataclasses import dataclass

from nbkp.preflight.status.endpoint import (
    DestinationEndpointStatus,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import SshEndpointStatus
from nbkp.preflight.status.sync import SyncStatus
from nbkp.preflight.status.volume import VolumeStatus


@dataclass(frozen=True)
class PreflightResult:
    """Complete result of the 4-phase preflight check cascade."""

    ssh_endpoint_statuses: dict[str, SshEndpointStatus]
    volume_statuses: dict[str, VolumeStatus]
    source_endpoint_statuses: dict[str, SourceEndpointStatus]
    destination_endpoint_statuses: dict[str, DestinationEndpointStatus]
    sync_statuses: dict[str, SyncStatus]
