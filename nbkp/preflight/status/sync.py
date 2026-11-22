"""Layer 4 — Sync: disabled, ``latest → /dev/null`` interpretation."""

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from nbkp.config import SyncConfig, SyncEndpoint
from nbkp.fsprotocol import DEVNULL_TARGET, Snapshot
from nbkp.preflight.status.endpoint import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    DestinationEndpointStatus,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import INACTIVE_SSH_ERRORS
from nbkp.preflight.status.volume import INACTIVE_VOLUME_ERRORS


class SyncErrorKind(enum.StrEnum):
    """
    Errors that require sync-graph context.

    The rest are at the SSH endpoint, volume or sync endpoint level.
    """

    DISABLED = "disabled"
    ENDPOINT_HOST_ERRORS = "endpoint-host-errors"
    """The host of either side (localhost included) has non-inactive SSH
    endpoint errors — rsync missing or too old, btrfs/udisks tools missing.
    Remote hosts already cascade through ``VolumeErrorKind.SSH_ENDPOINT_INACTIVE``;
    this is what makes *localhost* tool errors stop the sync too."""
    SRC_EP_LATEST_DEVNULL_NO_UPSTREAM = "src-ep-latest-devnull-no-upstream"
    DRY_RUN_SRC_EP_SNAPSHOT_PENDING = "dry-run-src-ep-snapshot-pending"

    # Cascade — lower layer inactive
    SOURCE_ENDPOINT_INACTIVE = "source-endpoint-inactive"
    DESTINATION_ENDPOINT_INACTIVE = "destination-endpoint-inactive"


INACTIVE_SYNC_ERRORS: frozenset[SyncErrorKind] = frozenset(
    {
        # Switched off in the config: never attempted, so never a problem.
        SyncErrorKind.DISABLED,
        SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING,
        SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
        SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
    },
)


class SyncStatus(BaseModel):
    """A sync's endpoint statuses and the sync-level errors they lead to."""

    model_config = ConfigDict(frozen=True)

    slug: str
    config: SyncConfig
    source_endpoint_status: SourceEndpointStatus
    destination_endpoint_status: DestinationEndpointStatus
    errors: list[SyncErrorKind]
    destination_latest_snapshot: Snapshot | None = None
    """Snapshot from the destination ``latest`` symlink.

    ``None`` when the symlink is absent, invalid, or points to
    ``/dev/null`` (no snapshot yet).
    """

    @computed_field
    @property
    def active(self) -> bool:
        """Return whether the sync has no errors."""
        return not self.errors

    def is_expected_inactive(self) -> bool:
        """
        Whether all errors across all 4 layers are expected-inactive.

        Used to distinguish syncs that are inactive due to expected
        conditions (missing sentinels, offline volumes) from those with
        real infrastructure errors.
        """
        src_ep = self.source_endpoint_status
        dst_ep = self.destination_endpoint_status
        return not self.active and all(
            [
                *(
                    set(ep.volume_status.ssh_endpoint_status.errors)
                    <= INACTIVE_SSH_ERRORS
                    for ep in (src_ep, dst_ep)
                ),
                *(
                    set(ep.volume_status.errors) <= INACTIVE_VOLUME_ERRORS
                    for ep in (src_ep, dst_ep)
                ),
                set(src_ep.errors) <= INACTIVE_SRC_ENDPOINT_ERRORS,
                set(dst_ep.errors) <= INACTIVE_DST_ENDPOINT_ERRORS,
                set(self.errors) <= INACTIVE_SYNC_ERRORS,
            ],
        )

    @staticmethod
    def from_diagnostics(
        sync: SyncConfig,
        src_endpoint: SyncEndpoint,
        src_ep_status: SourceEndpointStatus,
        dst_ep_status: DestinationEndpointStatus,
        selected_syncs: dict[str, SyncConfig],
        *,
        dry_run: bool,
    ) -> SyncStatus:
        """
        Build the status from the endpoints' statuses.

        A disabled sync gets only ``DISABLED``. *selected_syncs* are the syncs of this
        run: only one of them can replace a source ``latest`` that points to
        ``/dev/null``, and under *dry_run* not even that.
        """
        dst_diag = dst_ep_status.diagnostics
        return SyncStatus(
            slug=sync.slug,
            config=sync,
            source_endpoint_status=src_ep_status,
            destination_endpoint_status=dst_ep_status,
            errors=(
                [
                    *_host_errors(src_ep_status, dst_ep_status),
                    *_sync_errors(
                        sync,
                        src_endpoint,
                        src_ep_status,
                        selected_syncs,
                        dry_run=dry_run,
                    ),
                    *(
                        [SyncErrorKind.SOURCE_ENDPOINT_INACTIVE]
                        if not src_ep_status.active
                        else []
                    ),
                    *(
                        [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE]
                        if not dst_ep_status.active
                        else []
                    ),
                ]
                if sync.enabled
                else [SyncErrorKind.DISABLED]
            ),
            destination_latest_snapshot=(
                dst_diag.latest.snapshot if dst_diag and dst_diag.latest else None
            ),
        )


def _host_errors(
    src_ep_status: SourceEndpointStatus,
    dst_ep_status: DestinationEndpointStatus,
) -> list[SyncErrorKind]:
    """
    ``ENDPOINT_HOST_ERRORS`` when either side's host has fatal tool errors.

    Inactive host errors (unreachable, location-excluded) are left to the
    volume cascade, which already classifies them as expected-inactive.
    """
    return (
        [SyncErrorKind.ENDPOINT_HOST_ERRORS]
        if any(
            set(ep.volume_status.ssh_endpoint_status.errors) - INACTIVE_SSH_ERRORS
            for ep in (src_ep_status, dst_ep_status)
        )
        else []
    )


def _sync_errors(
    sync: SyncConfig,
    src_endpoint: SyncEndpoint,
    src_ep_status: SourceEndpointStatus,
    selected_syncs: dict[str, SyncConfig],
    *,
    dry_run: bool,
) -> list[SyncErrorKind]:
    if not src_ep_status.active:
        return []
    if src_endpoint.snapshot_mode == "none":
        return []
    diag = src_ep_status.diagnostics
    if diag is None or diag.latest is None:
        return []
    if diag.latest.raw_target != DEVNULL_TARGET:
        return []
    if not _has_upstream_sync(sync, selected_syncs):
        return [SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM]
    elif dry_run:
        return [SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING]
    else:
        return []


def _has_upstream_sync(
    sync: SyncConfig,
    selected_syncs: dict[str, SyncConfig],
) -> bool:
    # Only an upstream sync that is part of this run will replace the
    # /dev/null marker before *sync* reads it; one left out by --sync never
    # runs, and rsync would then fail on `latest/` with "Not a directory".
    return any(
        other.destination == sync.source and other.slug != sync.slug and other.enabled
        for other in selected_syncs.values()
    )
