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
    SyncStatus,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.sync.output import build_run_preview_sections
from nbkp.sync.snapshots.common import create_snapshot_timestamp


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
    return {
        "src": VolumeStatus(
            slug="src",
            config=config.volumes["src"],
            diagnostics=VolumeDiagnostics(),
            errors=src_errors or [],
        ),
        "dst": VolumeStatus(
            slug="dst",
            config=config.volumes["dst"],
            diagnostics=VolumeDiagnostics(),
            errors=dst_errors or [],
        ),
    }


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
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_s["src"],
                destination_status=vol_s["dst"],
                errors=[],
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
        sync_s = {
            "hl-sync": SyncStatus(
                slug="hl-sync",
                config=sync,
                source_status=vol_s["src"],
                destination_status=vol_s["dst"],
                errors=[],
            )
        }
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
            "hl-sync": SyncStatus(
                slug="hl-sync",
                config=sync,
                source_status=vol_s["src"],
                destination_status=vol_s["dst"],
                errors=[],
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
        sync_s = {
            "plain-sync": SyncStatus(
                slug="plain-sync",
                config=sync,
                source_status=vol_s["src"],
                destination_status=vol_s["dst"],
                errors=[],
            )
        }
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/" in output
        assert "staging" not in output
        assert "snapshots" not in output
