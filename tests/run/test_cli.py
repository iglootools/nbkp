"""Tests for `nbkp run`.

The JSON payload and the abort message are tested from plain pipeline
results; the command itself runs against a fake NAS (``tests.nas``) through
an injected runtime, with a fake rsync.
"""

import json
import subprocess
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from nbkp.cli import app
from nbkp.clidisplay import echo_json
from nbkp.config import Config
from nbkp.config.testkit import write_config
from nbkp.disks import UmountResult
from nbkp.policy import Strictness
from nbkp.preflight import SyncErrorKind, SyncStatus
from nbkp.run.cli.run_cmd import abort_message, run_json
from nbkp.run.pipeline import PipelineResult
from nbkp.sync import ProgressMode, SyncFailureKind, SyncResult
from tests.clidisplay import (
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_error_sync_statuses,
    sample_sentinel_only_sync_statuses,
    sample_sync_statuses_with_snapshots,
    sample_vol_statuses,
)
from tests.cliwiring.test_mount import (
    BUSY,
    LOCK_FAILED,
    encrypted_volume,
    mounted_drive_host,
)
from tests.fakes import FakeCommandRunner, FakeRsync, completed, fake_runtime
from tests.nas import healthy_nas, nas_config
from tests.preflight.fake_hosts import FAILS

_DONE = subprocess.CompletedProcess(args=[], returncode=0, stdout="done", stderr="")
_HINT = "nbkp preflight troubleshoot -c conf/nbkp.yaml -N private"


def _ok(slug: str, *, dry_run: bool = False) -> SyncResult:
    return SyncResult.succeeded(slug, dry_run, _DONE)


def _as_json(data: object, capsys: pytest.CaptureFixture[str]) -> Any:
    """*data* as ``-o json`` prints it."""
    echo_json(data)
    return json.loads(capsys.readouterr().out)


def _ran(
    config: Config,
    sync_statuses: dict[str, SyncStatus],
    results: list[SyncResult],
) -> PipelineResult:
    return PipelineResult(
        preflight=preflight(sample_all_active_vol_statuses(config), sync_statuses),
        results=results,
        has_preflight_errors=False,
        has_sync_failures=False,
    )


def _aborted(config: Config) -> PipelineResult:
    vol_s = sample_vol_statuses(config)
    return PipelineResult(
        preflight=preflight(vol_s, sample_error_sync_statuses(config, vol_s)),
        results=[],
        has_preflight_errors=True,
        has_sync_failures=True,
    )


class TestRunJson:
    def test_completed_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)
        pipeline = _ran(
            config,
            sample_all_active_sync_statuses(config, vol_s),
            [_ok("photos-to-nas")],
        )

        data = _as_json(run_json(pipeline, None, []), capsys)

        assert [v["slug"] for v in data["volumes"]] == ["local-data", "nas"]
        assert [s["slug"] for s in data["syncs"]] == ["photos-to-nas"]
        assert [(r["sync_slug"], r["outcome"]) for r in data["results"]] == [
            ("photos-to-nas", "success")
        ]
        assert data["umount"] == []
        assert "hint" not in data

    def test_aborted_run_carries_the_hint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _as_json(run_json(_aborted(sample_config()), _HINT, []), capsys)

        assert data["results"] == []
        assert data["hint"] == _HINT

    def test_snapshot_timestamps_serialize(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Regression: statuses carrying a snapshot broke JSON serialization.

        `run` is the command where this matters most, since scripting it is
        the reason to ask for JSON at all — and any destination that has ever
        completed a sync has a populated ``latest``.
        """
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)
        pipeline = _ran(
            config,
            sample_sync_statuses_with_snapshots(config, vol_s),
            [_ok("photos-to-nas")],
        )

        data = _as_json(run_json(pipeline, None, []), capsys)

        assert data["syncs"][0]["destination_latest_snapshot"]["timestamp"].startswith(
            "2026-03-06T14:30:00"
        )

    def test_umount_outcomes(self, capsys: pytest.CaptureFixture[str]) -> None:
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)
        pipeline = _ran(
            config,
            sample_all_active_sync_statuses(config, vol_s),
            [_ok("photos-to-nas")],
        )
        umounts = [UmountResult("plain", success=True), LOCK_FAILED]

        data = _as_json(run_json(pipeline, None, umounts), capsys)

        assert data["umount"] == [
            {"volume": "plain", "success": True},
            {
                "volume": "enc",
                "success": False,
                "failure_reason": "lock_failed",
                "detail": "lock failed (exit 1): busy",
                "failure": {"step": "lock", "exit_code": 1, "message": "busy"},
                "warning": "LUKS container still unlocked, manual lock needed",
            },
        ]


def _render(renderable: object) -> str:
    buf = StringIO()
    Console(file=buf, width=200, color_system=None).print(renderable)
    return buf.getvalue()


class TestAbortMessage:
    def test_lists_the_offending_syncs_and_suggests_troubleshoot(self) -> None:
        text = _render(
            abort_message(_aborted(sample_config()), Strictness.IGNORE_INACTIVE, _HINT)
        )

        assert "Aborting: preflight checks found errors in 1 sync:" in text
        assert "  photos-to-nas: destination endpoint inactive" in text
        assert f"Run {_HINT} for step-by-step fixes." in text

    def test_does_not_list_disabled_syncs(self) -> None:
        """Under ignore-none an inactive sync aborts the run, but a disabled
        one is never fatal and must not be reported as offending."""
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sentinel_only_sync_statuses(config, vol_s)
        sync_s["off"] = sync_s["photos-to-nas"].model_copy(
            update={"slug": "off", "errors": [SyncErrorKind.DISABLED]}
        )
        pipeline = PipelineResult(
            preflight=preflight(vol_s, sync_s),
            results=[],
            has_preflight_errors=True,
            has_sync_failures=True,
        )

        text = _render(abort_message(pipeline, Strictness.IGNORE_NONE, _HINT))

        assert "found errors in 1 sync:" in text
        assert "photos-to-nas:" in text
        assert "off:" not in text


@pytest.fixture
def nas_config_file(tmp_path: Path, chdir: Callable[[Path], None]) -> str:
    """``conf/nbkp.yaml`` (two syncs on the fake NAS), relative to the
    working directory so that suggested commands show it as given."""
    chdir(tmp_path)
    (tmp_path / "conf").mkdir()
    write_config(tmp_path / "conf", nas_config(syncs=("photos", "music")), "nbkp.yaml")
    return "conf/nbkp.yaml"


def _invoke(
    config_file: str,
    *args: str,
    run: FakeCommandRunner | None = None,
    rsync: FakeRsync | None = None,
) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["run", "-c", config_file, *args],
        obj=fake_runtime(run=run or healthy_nas(), rsync=rsync),
    )
    return result.exit_code, result.output


class TestRunCommand:
    def test_successful_run(self, nas_config_file: str) -> None:
        rsync = FakeRsync()

        exit_code, output = _invoke(nas_config_file, "-o", "json", rsync=rsync)

        assert exit_code == 0
        data = json.loads(output)
        assert [(r["sync_slug"], r["outcome"]) for r in data["results"]] == [
            ("photos", "success"),
            ("music", "success"),
        ]
        assert "hint" not in data
        assert rsync.synced == ["photos", "music"]

    def test_human_output_shows_the_checks_before_the_results(
        self, nas_config_file: str
    ) -> None:
        rsync = FakeRsync()

        exit_code, output = _invoke(nas_config_file, rsync=rsync)

        assert exit_code == 0
        assert output.index("Volumes:") < output.index("Sync Results")
        # Without --progress a spinner stands in for rsync's own output.
        assert rsync.last_kwargs["on_output"] is None

    def test_failed_rsync_fails_the_run(self, nas_config_file: str) -> None:
        rsync = FakeRsync([completed(23, stderr="partial transfer")])

        exit_code, output = _invoke(
            nas_config_file, "-s", "photos", "-o", "json", rsync=rsync
        )

        assert exit_code == 1
        [result] = json.loads(output)["results"]
        assert (result["outcome"], result["failure"]) == ("failed", "rsync")

    def test_dry_run_of_selected_syncs(self, nas_config_file: str) -> None:
        rsync = FakeRsync()
        run = healthy_nas()

        exit_code, output = _invoke(
            nas_config_file,
            "--dry-run",
            "--sync",
            "photos",
            "-o",
            "json",
            run=run,
            rsync=rsync,
        )

        assert exit_code == 0
        [result] = json.loads(output)["results"]
        assert (result["sync_slug"], result["dry_run"]) == ("photos", True)
        assert rsync.synced == ["photos"]
        assert rsync.last_kwargs["dry_run"] is True
        # Preflight checks only the selected sync's endpoints.
        assert [cmd for cmd in run.commands if any("music" in a for a in cmd)] == []

    def test_progress_streams_rsync_output(self, nas_config_file: str) -> None:
        rsync = FakeRsync()

        exit_code, _ = _invoke(nas_config_file, "--progress", "per-file", rsync=rsync)

        assert exit_code == 0
        assert rsync.last_kwargs["progress"] is ProgressMode.PER_FILE
        assert callable(rsync.last_kwargs["on_output"])


def _source_sentinel_missing() -> FakeCommandRunner:
    return healthy_nas(
        FakeCommandRunner().on("test", "-f", "/data/photos/.nbkp-src", returns=FAILS)
    )


class TestPreflightAbort:
    def test_infrastructure_error_aborts_before_any_sync(
        self, nas_config_file: str
    ) -> None:
        rsync = FakeRsync()
        rsync_missing = healthy_nas(
            FakeCommandRunner().on("which", "rsync", returns=FAILS)
        )

        exit_code, output = _invoke(nas_config_file, run=rsync_missing, rsync=rsync)

        assert exit_code == 1
        assert "Aborting: preflight checks found errors in 2 syncs:" in output
        assert rsync.calls == []

    def test_inactive_sync_is_skipped_by_default(self, nas_config_file: str) -> None:
        rsync = FakeRsync()

        exit_code, output = _invoke(
            nas_config_file, "-o", "json", run=_source_sentinel_missing(), rsync=rsync
        )

        assert exit_code == 0
        outcomes = {r["sync_slug"]: r["failure"] for r in json.loads(output)["results"]}
        assert outcomes == {"music": None, "photos": SyncFailureKind.INACTIVE.value}
        assert rsync.synced == ["music"]

    def test_inactive_sync_aborts_under_ignore_none(self, nas_config_file: str) -> None:
        rsync = FakeRsync()

        exit_code, output = _invoke(
            nas_config_file,
            "--strictness",
            "ignore-none",
            run=_source_sentinel_missing(),
            rsync=rsync,
        )

        assert exit_code == 1
        assert "found errors in 1 sync:" in output
        assert "photos: source endpoint inactive" in output
        assert "nbkp preflight troubleshoot -c conf/nbkp.yaml" in output
        assert rsync.calls == []

    def test_json_hint_carries_config_and_network_flags(
        self, nas_config_file: str
    ) -> None:
        exit_code, output = _invoke(
            nas_config_file,
            "-N",
            "private",
            "-S",
            "ignore-none",
            "-o",
            "json",
            run=_source_sentinel_missing(),
        )

        assert exit_code == 1
        data = json.loads(output)
        assert data["results"] == []
        # Location flags are covered by TestTroubleshootCommand: this config
        # defines no locations, so the CLI rejects them here.
        assert data["hint"] == _HINT


class TestUnknownSyncFilter:
    def test_unknown_sync_is_an_error(self, nas_config_file: str) -> None:
        run = healthy_nas()

        exit_code, output = _invoke(
            nas_config_file, "-o", "json", "--sync", "nope", run=run
        )

        assert exit_code == 2
        error = json.loads(output)["error"]
        assert (error["reason"], error["names"]) == ("unknown-sync", ["nope"])
        assert run.calls == []

    def test_unknown_sync_is_reported_on_stderr(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["run", "-c", str(config_path), "--sync", "nope"])

        assert result.exit_code == 2
        assert "unknown sync name(s): nope" in result.stderr
        assert "photos-to-nas" in result.stderr


class TestUmountFailure:
    def test_failed_lock_fails_a_successful_run(self, tmp_path: Path) -> None:
        config = nas_config()
        config = config.model_copy(
            update={"volumes": {**config.volumes, "enc": encrypted_volume()}}
        )
        config_path = write_config(tmp_path, config)

        result = runner.invoke(
            app,
            ["run", "-c", str(config_path), "-o", "json"],
            obj=fake_runtime(run=healthy_nas(mounted_drive_host(BUSY))),
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["results"][0]["outcome"] == "success"
        [umount] = data["umount"]
        assert umount["volume"] == "enc"
        assert umount["success"] is False
        assert umount["failure_reason"] == "lock_failed"
        assert umount["warning"] == "LUKS container still unlocked, manual lock needed"
