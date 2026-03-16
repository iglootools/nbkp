"""Tests for nbkp.sync.output (run preview formatting)."""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointStatus,
    SourceEndpointDiagnostics,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SyncStatus,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.sync.output import build_run_preview_sections
from nbkp.sync.snapshots.common import create_snapshot_timestamp


def _localhost_ssh_status() -> SshEndpointStatus:
    """Create an active localhost SSH endpoint status."""
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
        errors=[],
    )


def _btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    ep_src = SyncEndpoint(slug="ep-src", volume="src")
    ep_dst = SyncEndpoint(
        slug="ep-dst",
        volume="dst",
        btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
    )
    sync = SyncConfig(
        slug="btrfs-sync",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"btrfs-sync": sync},
    )


def _make_vol_statuses(
    config: Config,
    src_errors: list[VolumeError] | None = None,
    dst_errors: list[VolumeError] | None = None,
) -> dict[str, VolumeStatus]:
    ssh_status = _localhost_ssh_status()
    return {
        "src": VolumeStatus(
            slug="src",
            config=config.volumes["src"],
            ssh_endpoint_status=ssh_status,
            diagnostics=VolumeDiagnostics(),
            errors=src_errors or [],
        ),
        "dst": VolumeStatus(
            slug="dst",
            config=config.volumes["dst"],
            ssh_endpoint_status=ssh_status,
            diagnostics=VolumeDiagnostics(),
            errors=dst_errors or [],
        ),
    }


def _make_sync_status(
    sync_slug: str,
    sync_cfg: SyncConfig,
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
    *,
    destination_latest_snapshot=None,
) -> SyncStatus:
    """Build a SyncStatus with the new 4-layer model."""
    src_ep_slug = sync_cfg.source
    dst_ep_slug = sync_cfg.destination
    src_vol_slug = config.sync_endpoints[src_ep_slug].volume
    dst_vol_slug = config.sync_endpoints[dst_ep_slug].volume

    src_ep_status = SourceEndpointStatus(
        endpoint_slug=src_ep_slug,
        volume_status=vol_statuses[src_vol_slug],
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=src_ep_slug,
            sentinel_exists=True,
        ),
        errors=[],
    )
    dst_ep_status = DestinationEndpointStatus(
        endpoint_slug=dst_ep_slug,
        volume_status=vol_statuses[dst_vol_slug],
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug=dst_ep_slug,
            sentinel_exists=True,
            endpoint_writable=True,
        ),
        errors=[],
    )
    return SyncStatus(
        slug=sync_slug,
        config=sync_cfg,
        source_endpoint_status=src_ep_status,
        destination_endpoint_status=dst_ep_status,
        errors=[],
        destination_latest_snapshot=destination_latest_snapshot,
    )


def _render_sections(sections: list) -> str:
    """Render Rich sections to a plain string."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False, width=300)
    for s in sections:
        console.print(s)
    return buf.getvalue()


class TestRunPreviewRsyncCommandDisplay:
    def test_btrfs_shows_staging_suffix(self) -> None:
        config = _btrfs_config()
        vol_s = _make_vol_statuses(config)
        sync_s = {
            "btrfs-sync": _make_sync_status(
                "btrfs-sync", config.syncs["btrfs-sync"], config, vol_s
            )
        }
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/staging/" in output
        assert "--link-dest" not in output

    def test_hard_link_shows_snapshots_timestamp_suffix(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        ep_src = SyncEndpoint(slug="ep-src", volume="src")
        ep_dst = SyncEndpoint(
            slug="ep-dst",
            volume="dst",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        )
        sync = SyncConfig(
            slug="hl-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
            syncs={"hl-sync": sync},
        )
        vol_s = _make_vol_statuses(config)
        sync_s = {"hl-sync": _make_sync_status("hl-sync", sync, config, vol_s)}
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        # No previous snapshot: --link-dest is omitted
        assert "--link-dest" not in output

    def test_hard_link_shows_link_dest_when_previous_exists(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        ep_src = SyncEndpoint(slug="ep-src", volume="src")
        ep_dst = SyncEndpoint(
            slug="ep-dst",
            volume="dst",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        )
        sync = SyncConfig(
            slug="hl-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
            syncs={"hl-sync": sync},
        )
        vol_s = _make_vol_statuses(config)
        sync_s = {
            "hl-sync": _make_sync_status(
                "hl-sync",
                sync,
                config,
                vol_s,
                destination_latest_snapshot=create_snapshot_timestamp(
                    datetime(2026, 3, 6, 14, 30, 0, tzinfo=timezone.utc),
                    dst,
                ),
            )
        }
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        assert "--link-dest" in output
        expected_ts = create_snapshot_timestamp(
            datetime(2026, 3, 6, 14, 30, 0, tzinfo=timezone.utc), dst
        )
        assert f"../{expected_ts.name}" in output

    def test_plain_sync_shows_bare_destination(self) -> None:
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        ep_src = SyncEndpoint(slug="ep-src", volume="src")
        ep_dst = SyncEndpoint(slug="ep-dst", volume="dst")
        sync = SyncConfig(
            slug="plain-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
            syncs={"plain-sync": sync},
        )
        vol_s = _make_vol_statuses(config)
        sync_s = {"plain-sync": _make_sync_status("plain-sync", sync, config, vol_s)}
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/" in output
        assert "staging" not in output
        assert "snapshots" not in output
