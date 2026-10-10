"""Layer 4 — Sync: disabled, ``latest → /dev/null`` interpretation."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ...config import (
    SyncConfig,
    SyncEndpoint,
)
from ...fsprotocol import (
    DEVNULL_TARGET,
    Snapshot,
)
from .endpoint import (
    INACTIVE_DST_ENDPOINT_ERRORS,
    INACTIVE_SRC_ENDPOINT_ERRORS,
    DestinationEndpointStatus,
    SourceEndpointStatus,
)
from .ssh import INACTIVE_SSH_ERRORS
from .volume import INACTIVE_VOLUME_ERRORS


class SyncError(str, enum.Enum):
    """Sync-level errors.

    Drastically reduced from the pre-refactor version.  Most errors
    that were previously ``SyncError`` variants now live at the SSH
    endpoint, volume, or sync endpoint level.  Only errors that
    require sync-graph context remain here.
    """

    DISABLED = "disabled"
    ENDPOINT_HOST_ERRORS = "a host this sync runs on has tool errors"
    """The host of either side (localhost included) has non-inactive SSH
    endpoint errors — rsync missing or too old, btrfs/udisks tools missing.
    Remote hosts already cascade through ``VolumeError.SSH_ENDPOINT_INACTIVE``;
    this is what makes *localhost* tool errors stop the sync too."""
    SRC_EP_LATEST_DEVNULL_NO_UPSTREAM = (
        "source latest \u2192 /dev/null with no upstream sync"
    )
    DRY_RUN_SRC_EP_SNAPSHOT_PENDING = (
        "source snapshot not yet available (dry-run; upstream has not run)"
    )

    # Cascade — lower layer inactive
    SOURCE_ENDPOINT_INACTIVE = "source endpoint inactive"
    DESTINATION_ENDPOINT_INACTIVE = "destination endpoint inactive"


INACTIVE_SYNC_ERRORS: frozenset[SyncError] = frozenset(
    {
        # Switched off in the config: never attempted, so never a problem.
        SyncError.DISABLED,
        SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING,
        SyncError.SOURCE_ENDPOINT_INACTIVE,
        SyncError.DESTINATION_ENDPOINT_INACTIVE,
    }
)


class SyncStatus(BaseModel):
    """Runtime status of a sync."""

    model_config = ConfigDict(frozen=True)

    slug: str
    config: SyncConfig
    source_endpoint_status: SourceEndpointStatus
    destination_endpoint_status: DestinationEndpointStatus
    errors: list[SyncError]
    destination_latest_snapshot: Snapshot | None = None
    """Snapshot from the destination ``latest`` symlink.

    ``None`` when the symlink is absent, invalid, or points to
    ``/dev/null`` (no snapshot yet).
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    def is_expected_inactive(self) -> bool:
        """Whether all errors across all 4 layers are expected-inactive.

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
            ]
        )

    @staticmethod
    def from_diagnostics(
        sync: SyncConfig,
        src_endpoint: SyncEndpoint,
        src_ep_status: SourceEndpointStatus,
        dst_ep_status: DestinationEndpointStatus,
        all_syncs: dict[str, SyncConfig],
        dry_run: bool,
    ) -> SyncStatus:
        """Create status by interpreting sync-level errors."""
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
                        sync, src_endpoint, src_ep_status, all_syncs, dry_run
                    ),
                    *(
                        [SyncError.SOURCE_ENDPOINT_INACTIVE]
                        if not src_ep_status.active
                        else []
                    ),
                    *(
                        [SyncError.DESTINATION_ENDPOINT_INACTIVE]
                        if not dst_ep_status.active
                        else []
                    ),
                ]
                if sync.enabled
                else [SyncError.DISABLED]
            ),
            destination_latest_snapshot=(
                dst_diag.latest.snapshot if dst_diag and dst_diag.latest else None
            ),
        )


# ── Layer 4 error interpretation ───────────────────────────


def _host_errors(
    src_ep_status: SourceEndpointStatus,
    dst_ep_status: DestinationEndpointStatus,
) -> list[SyncError]:
    """``ENDPOINT_HOST_ERRORS`` when either side's host has fatal tool errors.

    Inactive host errors (unreachable, location-excluded) are left to the
    volume cascade, which already classifies them as expected-inactive.
    """
    return (
        [SyncError.ENDPOINT_HOST_ERRORS]
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
    all_syncs: dict[str, SyncConfig],
    dry_run: bool,
) -> list[SyncError]:
    """Sync-level errors: ``latest → /dev/null`` interpretation.

    Only errors that require sync-graph context (upstream dependency)
    remain at this level.  All other errors cascade from lower layers.
    """
    if not src_ep_status.active:
        return []
    if src_endpoint.snapshot_mode == "none":
        return []
    diag = src_ep_status.diagnostics
    if diag is None or diag.latest is None:
        return []
    if diag.latest.raw_target != DEVNULL_TARGET:
        return []
    # latest → /dev/null: interpret based on sync graph
    if not _has_upstream_sync(sync, all_syncs):
        return [SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM]
    elif dry_run:
        return [SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING]
    else:
        return []


def _has_upstream_sync(
    sync: SyncConfig,
    all_syncs: dict[str, SyncConfig],
) -> bool:
    """Check if an enabled upstream sync writes to this sync's source.

    An upstream sync is one whose destination endpoint slug
    matches this sync's source endpoint slug.
    """
    return any(
        other.destination == sync.source and other.slug != sync.slug and other.enabled
        for other in all_syncs.values()
    )
