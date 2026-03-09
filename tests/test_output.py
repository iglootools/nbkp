"""Tests for nbkp.output (troubleshoot and check formatting)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from nbkp.preflight import (
    SyncReason,
    SyncStatus,
    VolumeReason,
    VolumeStatus,
)
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.output import build_check_sections, print_human_troubleshoot


def _make_console() -> tuple[Console, StringIO]:
    """Return a Console that writes to a StringIO buffer."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False)
    return console, buf


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
    src_reasons: list[VolumeReason] | None = None,
    dst_reasons: list[VolumeReason] | None = None,
) -> dict[str, VolumeStatus]:
    return {
        "src": VolumeStatus(
            slug="src",
            config=config.volumes["src"],
            reasons=src_reasons or [],
        ),
        "dst": VolumeStatus(
            slug="dst",
            config=config.volumes["dst"],
            reasons=dst_reasons or [],
        ),
    }


class TestTroubleshootBtrfsStagingNotFound:
    def test_destination_staging_not_found_text(self) -> None:
        """Output mentions staging/ for DESTINATION_TMP_NOT_FOUND."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                reasons=[SyncReason.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "staging" in output
        assert "btrfs subvolume create" in output
        assert "/mnt/dst/staging" in output

    def test_destination_staging_not_found_no_latest(self) -> None:
        """Fix instructions reference staging/, not latest/."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                reasons=[SyncReason.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        # The fix instructions must reference staging/, not latest/
        assert "btrfs subvolume create /mnt/dst/staging" in output

    def test_destination_staging_not_found_reason_label(self) -> None:
        """SyncReason label for TMP_NOT_FOUND appears."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                reasons=[SyncReason.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert SyncReason.DESTINATION_TMP_NOT_FOUND.value in output

    def test_no_issues_message(self) -> None:
        """When all statuses are active, prints no-issues message."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                reasons=[],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "No issues found" in output

    def test_destination_staging_not_found_with_subdir(self) -> None:
        """Troubleshoot output uses the correct subdir path for staging/."""
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        ep_src = SyncEndpoint(slug="ep-src", volume="src")
        ep_dst = SyncEndpoint(
            slug="ep-dst",
            volume="dst",
            subdir="backup",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        )
        sync = SyncConfig(
            slug="btrfs-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
            syncs={"btrfs-sync": sync},
        )
        vol_statuses = {
            "src": VolumeStatus(slug="src", config=src, reasons=[]),
            "dst": VolumeStatus(slug="dst", config=dst, reasons=[]),
        }
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=sync,
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                reasons=[SyncReason.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "/mnt/dst/backup/staging" in output


def _render_sections(sections: list) -> str:
    """Render Rich sections to a plain string."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False, width=300)
    for s in sections:
        console.print(s)
    return buf.getvalue()


class TestCheckRsyncCommandDisplay:
    def test_btrfs_shows_staging_suffix(self) -> None:
        config = _btrfs_config()
        vol_s = _make_vol_statuses(config)
        sync_s = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_s["src"],
                destination_status=vol_s["dst"],
                reasons=[],
            )
        }
        sections = build_check_sections(
            vol_s, sync_s, config, resolved_endpoints={}
        )
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
            hard_link_snapshots=HardLinkSnapshotConfig(
                enabled=True, max_snapshots=10
            ),
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
                reasons=[],
            )
        }
        sections = build_check_sections(
            vol_s, sync_s, config, resolved_endpoints={}
        )
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
            hard_link_snapshots=HardLinkSnapshotConfig(
                enabled=True, max_snapshots=10
            ),
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
                reasons=[],
                destination_latest_target="2026-03-06T14:30:00.000Z",
            )
        }
        sections = build_check_sections(
            vol_s, sync_s, config, resolved_endpoints={}
        )
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        assert "--link-dest" in output
        assert "../2026-03-06T14:30:00.000Z" in output

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
                reasons=[],
            )
        }
        sections = build_check_sections(
            vol_s, sync_s, config, resolved_endpoints={}
        )
        output = _render_sections(sections)
        assert "/mnt/dst/" in output
        assert "staging" not in output
        assert "snapshots" not in output
