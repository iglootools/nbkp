"""Tests for nbkp.sync.output (run preview formatting)."""

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console, RenderableType

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
    SyncErrorKind,
)
from nbkp.snapshots.common import create_snapshot_timestamp
from nbkp.sync.output import (
    build_human_results_sections,
    build_run_preview_sections,
)
from nbkp.sync.results import (
    SyncFailureKind,
    SyncResult,
    SyncWarning,
    SyncWarningKind,
)
from tests.fixtures.preflight import btrfs_config, local_vol_statuses, sync_status


def _render_sections(sections: list[RenderableType]) -> str:
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False, width=300)
    for s in sections:
        console.print(s)
    return buf.getvalue()


class TestRunPreviewRsyncCommandDisplay:
    def test_btrfs_shows_staging_suffix(self) -> None:
        config = btrfs_config()
        vol_s = local_vol_statuses(config)
        sync_s = {"btrfs-sync": sync_status(config, vol_s, "btrfs-sync")}
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
        vol_s = local_vol_statuses(config)
        sync_s = {"hl-sync": sync_status(config, vol_s, "hl-sync")}
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
        vol_s = local_vol_statuses(config)
        sync_s = {
            "hl-sync": sync_status(
                config,
                vol_s,
                "hl-sync",
                destination_latest_snapshot=create_snapshot_timestamp(
                    datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC),
                    dst,
                    "linux",
                ),
            )
        }
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        assert "--link-dest" in output
        expected_ts = create_snapshot_timestamp(
            datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC), dst, "linux"
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
        vol_s = local_vol_statuses(config)
        sync_s = {"plain-sync": sync_status(config, vol_s, "plain-sync")}
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/" in output
        assert "staging" not in output
        assert "snapshots" not in output


class TestRunResultsMarkupSafety:
    """rsync's own output uses square brackets; Rich must not eat them."""

    # Verbatim shape of a real rsync failure: the trailing "[sender=3.4.1]"
    # is the piece a markup-formatted table cell silently dropped.
    RSYNC_ERROR = (
        'rsync: [sender] change_dir "/mnt/missing" failed: No such file (2)\n'
        "rsync error: some files could not be transferred (code 23) "
        "at main.c(1338) [sender=3.4.1]"
    )

    def _render_with_markup(self, results: list[SyncResult]) -> str:
        """Render results through a console with markup on, as the CLI does."""
        buf = StringIO()
        console = Console(file=buf, width=300)
        for section in build_human_results_sections(
            results, dry_run=False, config=Config(), resolved_endpoints={}
        ):
            console.print(section)
        return buf.getvalue()

    def test_rsync_output_brackets_survive(self) -> None:
        result = SyncResult.failed(
            "s",
            False,
            SyncFailureKind.RSYNC,
            "rsync exited with code 23",
            rsync_exit_code=23,
            output=self.RSYNC_ERROR,
        )
        output = self._render_with_markup([result])
        assert "[sender]" in output
        assert "[sender=3.4.1]" in output

    def test_detail_brackets_survive(self) -> None:
        result = SyncResult.failed(
            "s",
            False,
            SyncFailureKind.SNAPSHOT,
            "Snapshot failed: mkdir '/mnt/x[1]' failed",
        )
        assert "/mnt/x[1]" in self._render_with_markup([result])


class TestRunResultsTitle:
    def _title_line(self, dry_run: bool) -> str:
        text = _render_sections(
            build_human_results_sections(
                [], dry_run=dry_run, config=Config(), resolved_endpoints={}
            )
        )
        return text.splitlines()[0].strip()

    def test_a_dry_run_is_labeled_once_at_the_start_of_the_title(self) -> None:
        assert self._title_line(dry_run=True) == "[dry-run] Sync results:"

    def test_a_real_run_is_not_labeled(self) -> None:
        assert self._title_line(dry_run=False) == "Sync results:"


class TestRunResultsFailedOutput:
    def test_shows_the_last_lines_where_rsyncs_error_is(self) -> None:
        progress = [f"file-{i}.txt" for i in range(20)]
        error = "rsync error: some files could not be transferred (code 23)"
        result = SyncResult.failed(
            "s",
            False,
            SyncFailureKind.RSYNC,
            "rsync exited with code 23",
            rsync_exit_code=23,
            output="\n".join([*progress, error]) + "\n",
        )

        text = _render_sections(
            build_human_results_sections(
                [result], dry_run=False, config=Config(), resolved_endpoints={}
            )
        )

        assert "(code 23)" in text
        assert "file-0.txt" not in text
        assert "file-19.txt" in text


class TestRunResultsSkipped:
    def test_words_the_preflight_errors(self) -> None:
        result = SyncResult.skipped(
            "s",
            False,
            SyncFailureKind.INACTIVE,
            (SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,),
        )

        text = _render_sections(
            build_human_results_sections(
                [result], dry_run=False, config=Config(), resolved_endpoints={}
            )
        )

        assert "Sync not active: source endpoint inactive" in text


class TestRunResultsWarnings:
    def test_warnings_are_listed(self) -> None:
        result = SyncResult.failed(
            "s",
            False,
            SyncFailureKind.RSYNC,
            "rsync exited with code 23",
            warnings=(
                SyncWarning(
                    kind=SyncWarningKind.SNAPSHOT_DIR_CLEANUP,
                    message="Failed to remove [dir]",
                ),
            ),
        )
        buf = StringIO()
        console = Console(file=buf, width=300)
        for section in build_human_results_sections(
            [result], dry_run=False, config=Config(), resolved_endpoints={}
        ):
            console.print(section)
        assert "Warning: Failed to remove [dir]" in buf.getvalue()
