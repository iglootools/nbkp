"""The complete result of a preflight run, across all 4 layers."""

from __future__ import annotations

from dataclasses import dataclass

from .endpoint import DestinationEndpointStatus, SourceEndpointStatus
from .ssh import SshEndpointStatus
from .sync import SyncStatus
from .volume import VolumeStatus


@dataclass(frozen=True)
class PreflightResult:
    """Complete result of the 4-phase preflight check cascade."""

    ssh_endpoint_statuses: dict[str, SshEndpointStatus]
    volume_statuses: dict[str, VolumeStatus]
    source_endpoint_statuses: dict[str, SourceEndpointStatus]
    destination_endpoint_statuses: dict[str, DestinationEndpointStatus]
    sync_statuses: dict[str, SyncStatus]
