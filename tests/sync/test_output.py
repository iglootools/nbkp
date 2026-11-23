"""Tests for nbkp.sync.output (run preview formatting)."""

import subprocess
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console, RenderableType

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
)
from nbkp.preflight import (
    SyncErrorKind,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.snapshots import SnapshotOp
from nbkp.snapshots.common import create_snapshot_timestamp
from nbkp.sync.output import (
    build_human_results_sections,
    build_run_preview_sections,
    sync_result_json,
)
from nbkp.sync.results import (
    SyncFailureKind,
    SyncResult,
    SyncWarning,
    SyncWarningKind,
)
from tests.fixtures.config import two_volume_config
from tests.fixtures.preflight import btrfs_config, local_vol_statuses, sync_status
from tests.sync.runner_helpers import make_local_config, make_snapshot_config


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
        _, config = two_volume_config(
            dst_hard_link=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        )
        vol_s = local_vol_statuses(config)
        sync_s = {"s1": sync_status(config, vol_s, "s1")}
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        # No previous snapshot: --link-dest is omitted
        assert "--link-dest" not in output

    def test_hard_link_creates_the_snapshot_directory_without_p(self) -> None:
        _, config = two_volume_config(
            dst_hard_link=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        )
        vol_s = local_vol_statuses(config)
        sync_s = {"s1": sync_status(config, vol_s, "s1")}
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        # As create_snapshot_dir runs it: an existing snapshot fails the run.
        assert "mkdir /mnt/dst/snapshots/<timestamp>" in output
        assert "mkdir -p" not in output

    def test_hard_link_shows_link_dest_when_previous_exists(self) -> None:
        _, config = two_volume_config(
            dst_hard_link=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        )
        vol_s = local_vol_statuses(config)
        sync_s = {
            "s1": sync_status(
                config,
                vol_s,
                "s1",
                destination_latest_snapshot=create_snapshot_timestamp(
                    datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC),
                    config.volumes["dst"],
                    "linux",
                ),
            ),
        }
        sections = build_run_preview_sections(sync_s, config, resolved_endpoints={})
        output = _render_sections(sections)
        assert "/mnt/dst/snapshots/<timestamp>/" in output
        assert "--link-dest" in output
        expected_ts = create_snapshot_timestamp(
            datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC),
            config.volumes["dst"],
            "linux",
        )
        assert f"../{expected_ts.name}" in output

    def test_plain_sync_shows_bare_destination(self) -> None:
        _, config = two_volume_config()
        vol_s = local_vol_statuses(config)
        sync_s = {"s1": sync_status(config, vol_s, "s1")}
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
            results,
            dry_run=False,
            config=Config(),
            resolved_endpoints={},
        ):
            console.print(section)
        return buf.getvalue()

    def test_rsync_output_brackets_survive(self) -> None:
        result = SyncResult.failed(
            "s",
            dry_run=False,
            failure=SyncFailureKind.RSYNC,
            detail="rsync exited with code 23",
            rsync_exit_code=23,
            output=self.RSYNC_ERROR,
        )
        output = self._render_with_markup([result])
        assert "[sender]" in output
        assert "[sender=3.4.1]" in output

    def test_detail_brackets_survive(self) -> None:
        result = SyncResult.failed(
            "s",
            dry_run=False,
            failure=SyncFailureKind.SNAPSHOT,
            detail="Snapshot failed: mkdir '/mnt/x[1]' failed",
        )
        assert "/mnt/x[1]" in self._render_with_markup([result])


class TestRunResultsTitle:
    def _title_line(self, *, dry_run: bool) -> str:
        text = _render_sections(
            build_human_results_sections(
                [],
                dry_run=dry_run,
                config=Config(),
                resolved_endpoints={},
            ),
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
            dry_run=False,
            failure=SyncFailureKind.RSYNC,
            detail="rsync exited with code 23",
            rsync_exit_code=23,
            output="\n".join([*progress, error]) + "\n",
        )

        text = _render_sections(
            build_human_results_sections(
                [result],
                dry_run=False,
                config=Config(),
                resolved_endpoints={},
            ),
        )

        assert "(code 23)" in text
        assert "file-0.txt" not in text
        assert "file-19.txt" in text


class TestRunResultsSkipped:
    def test_words_the_preflight_errors(self) -> None:
        result = SyncResult.skipped(
            "s",
            dry_run=False,
            failure=SyncFailureKind.INACTIVE,
            preflight_errors=(SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,),
        )

        text = _render_sections(
            build_human_results_sections(
                [result],
                dry_run=False,
                config=Config(),
                resolved_endpoints={},
            ),
        )

        assert "Sync not active: source endpoint inactive" in text


class TestRunResultsWarnings:
    def test_warnings_are_listed(self) -> None:
        result = SyncResult.failed(
            "s",
            dry_run=False,
            failure=SyncFailureKind.RSYNC,
            detail="rsync exited with code 23",
            warnings=(
                SyncWarning(
                    kind=SyncWarningKind.SNAPSHOT_DIR_CLEANUP,
                    op=SnapshotOp.DELETE,
                    path="/dst/snapshots/[dir]",
                    stderr="Permission denied",
                ),
            ),
        )
        buf = StringIO()
        console = Console(file=buf, width=300)
        for section in build_human_results_sections(
            [result],
            dry_run=False,
            config=Config(),
            resolved_endpoints={},
        ):
            console.print(section)
        assert (
            "Warning: Failed to remove snapshot dir after rsync failure:"
            " delete failed for /dst/snapshots/[dir]: Permission denied"
        ) in buf.getvalue()

    def test_json_warning_has_kind_facts_and_message(self) -> None:
        """Scripts read the kind and its facts; ``message`` stays for display."""
        result = SyncResult.succeeded(
            "s",
            dry_run=False,
            proc=subprocess.CompletedProcess(["rsync"], 24, "", ""),
            warnings=(SyncWarning(kind=SyncWarningKind.VANISHED_FILES, exit_code=24),),
        )
        assert sync_result_json(result)["warnings"] == [
            {
                "kind": "vanished-files",
                "exit_code": 24,
                "message": (
                    "Some files vanished from the source during the transfer"
                    " (rsync exit code 24); everything else was copied"
                ),
            },
        ]


class TestRunResultsSnapshot:
    SNAPSHOT = "/dst/snapshots/2026-03-06T14:30:00.000Z"

    def _render(self, config: Config, result: SyncResult) -> str:
        return _render_sections(
            build_human_results_sections(
                [result],
                dry_run=False,
                config=config,
                resolved_endpoints=resolve_all_endpoints(config),
            ),
        )

    def _succeeded(
        self,
        *,
        snapshot_path: str | None = None,
        pruned_paths: tuple[str, ...] | None = None,
    ) -> SyncResult:
        return SyncResult.succeeded(
            "s1",
            dry_run=False,
            proc=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            ),
            snapshot_path=snapshot_path,
            pruned_paths=pruned_paths,
        )

    def test_snapshot_path_shown_bare_when_destination_is_local(self) -> None:
        text = self._render(
            make_local_config(),
            self._succeeded(snapshot_path=self.SNAPSHOT),
        )
        assert f"Snapshot: {self.SNAPSHOT}" in text
        assert "nas.local" not in text

    def test_snapshot_path_prefixed_with_host_when_destination_is_remote(
        self,
    ) -> None:
        text = self._render(
            make_snapshot_config(),
            self._succeeded(snapshot_path=self.SNAPSHOT),
        )
        assert f"Snapshot: nas.local:{self.SNAPSHOT}" in text

    def test_pruned_count_shown_when_destination_is_local(self) -> None:
        result = self._succeeded(
            snapshot_path=self.SNAPSHOT,
            pruned_paths=("/dst/snapshots/a", "/dst/snapshots/b"),
        )
        assert "Pruned: 2 snapshot(s)" in self._render(make_local_config(), result)

    def test_pruned_count_shown_when_destination_is_remote(self) -> None:
        result = self._succeeded(
            snapshot_path=self.SNAPSHOT,
            pruned_paths=("/dst/snapshots/a",),
        )
        assert "Pruned: 1 snapshot(s)" in self._render(make_snapshot_config(), result)

    def test_snapshot_and_pruned_lines_absent_when_result_has_neither(self) -> None:
        text = self._render(make_snapshot_config(), self._succeeded())
        assert "Snapshot:" not in text
        assert "Pruned:" not in text
