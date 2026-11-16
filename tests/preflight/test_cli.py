"""Tests for `preflight check`.

Its JSON view is tested from testkit statuses; the command runs against
the fake NAS (``tests.nas``) through an injected runtime.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from nbkp.cli import app
from nbkp.clidisplay import echo_json
from nbkp.config.testkit import write_config
from nbkp.preflight.cli.check_cmd import check_json
from tests.clidisplay import (
    config_with_locations,
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_sync_statuses_with_snapshots,
)
from tests.fakes import FakeCommandRunner, fake_runtime
from tests.nas import healthy_nas, nas_config
from tests.preflight.fake_hosts import FAILS


def _as_json(data: object, capsys: pytest.CaptureFixture[str]) -> Any:
    echo_json(data)
    return json.loads(capsys.readouterr().out)


class TestCheckJson:
    def test_lists_every_layer(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``ssh_endpoints`` includes the endpoints the volumes reach, with
        their warnings; volumes and syncs follow."""
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)

        data = _as_json(
            check_json(
                preflight(vol_s, sample_all_active_sync_statuses(config, vol_s))
            ),
            capsys,
        )

        assert [s["slug"] for s in data["ssh_endpoints"]] == ["localhost", "nas-server"]
        assert "warnings" in data["ssh_endpoints"][0]
        assert [v["slug"] for v in data["volumes"]] == ["local-data", "nas"]
        assert [s["slug"] for s in data["syncs"]] == ["photos-to-nas"]

    def test_snapshot_timestamps_serialize(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A populated ``latest`` snapshot puts a datetime in the status tree.

        Regression: the statuses were dumped with a bare ``model_dump()``,
        which leaves ``Snapshot.timestamp`` as a ``datetime`` for
        ``json.dumps`` to reject.  The other JSON tests pass because their
        fixtures leave every snapshot field unset.
        """
        config = sample_config()
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_sync_statuses_with_snapshots(config, vol_s)

        data = _as_json(check_json(preflight(vol_s, sync_s)), capsys)

        sync = data["syncs"][0]
        assert sync["destination_latest_snapshot"]["timestamp"].startswith(
            "2026-03-06T14:30:00"
        )
        nested = sync["destination_endpoint_status"]["diagnostics"]["latest"]
        assert nested["snapshot"]["name"] == "2026-03-06T14:30:00.000Z"


def _check(
    config_path: Path, *args: str, run: FakeCommandRunner | None = None
) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["preflight", "check", "--config", str(config_path), *args],
        obj=fake_runtime(run=run or healthy_nas()),
    )
    return result.exit_code, result.output


def _destination_sentinel_missing() -> FakeCommandRunner:
    return healthy_nas(
        FakeCommandRunner().on("test", "-f", "/backup/photos/.nbkp-dst", returns=FAILS)
    )


class TestCheckCommand:
    def test_human_output_all_active(self, tmp_path: Path) -> None:
        exit_code, output = _check(write_config(tmp_path, nas_config()))

        assert exit_code == 0
        assert "Preflight Checks" in output
        assert "✓ check dst:photos-dst" in output

    def test_json_output(self, tmp_path: Path) -> None:
        exit_code, output = _check(write_config(tmp_path, nas_config()), "-o", "json")

        assert exit_code == 0
        data = json.loads(output)
        assert [(s["slug"], s["errors"]) for s in data["syncs"]] == [("photos", [])]

    def test_inactive_sync_exits_0_by_default(self, tmp_path: Path) -> None:
        exit_code, output = _check(
            write_config(tmp_path, nas_config()),
            "-o",
            "json",
            run=_destination_sentinel_missing(),
        )

        assert exit_code == 0
        [sync] = json.loads(output)["syncs"]
        assert sync["errors"] == ["destination endpoint inactive"]

    def test_inactive_sync_exits_1_when_strict(self, tmp_path: Path) -> None:
        exit_code, _ = _check(
            write_config(tmp_path, nas_config()),
            "--strictness",
            "ignore-none",
            run=_destination_sentinel_missing(),
        )

        assert exit_code == 1

    def test_infrastructure_error_exits_1(self, tmp_path: Path) -> None:
        rsync_missing = healthy_nas(
            FakeCommandRunner().on("which", "rsync", returns=FAILS)
        )

        exit_code, _ = _check(write_config(tmp_path, nas_config()), run=rsync_missing)

        assert exit_code == 1

    def test_hint_carries_config_and_endpoint_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run from the config's directory: the hint shows the path relative
        # to it, short enough not to wrap inside the panel.
        monkeypatch.chdir(tmp_path)
        write_config(tmp_path, nas_config())

        _, output = _check(
            Path("config.yaml"), "-N", "private", run=_destination_sentinel_missing()
        )

        flat = " ".join(
            line.strip(" │") for line in output.splitlines() if line.strip()
        )
        assert "nbkp preflight troubleshoot -c config.yaml -N private" in flat


class TestLocationValidation:
    @pytest.mark.parametrize("flag", ["--location", "--exclude-location"])
    def test_unknown_location_rejected(self, flag: str, tmp_path: Path) -> None:
        run = healthy_nas()

        exit_code, output = _check(
            write_config(tmp_path, config_with_locations()), flag, "office", run=run
        )

        assert exit_code == 2
        assert f"unknown location 'office' passed to {flag}" in output
        assert "home" in output
        assert "travel" in output
        assert run.calls == []

    def test_unknown_location_json_error(self, tmp_path: Path) -> None:
        run = healthy_nas()

        exit_code, output = _check(
            write_config(tmp_path, config_with_locations()),
            "--output",
            "json",
            "--location",
            "office",
            run=run,
        )

        assert exit_code == 2
        error = json.loads(output)["error"]
        assert error["reason"] == "unknown-location"
        assert "unknown location 'office'" in error["message"]
        assert "home" in error["message"]
        assert run.calls == []

    def test_known_location_accepted(self, tmp_path: Path) -> None:
        exit_code, _ = _check(
            write_config(tmp_path, config_with_locations()), "--location", "home"
        )

        assert exit_code == 0

    def test_location_on_config_without_locations(self, tmp_path: Path) -> None:
        exit_code, output = _check(
            write_config(tmp_path, nas_config()), "--location", "home"
        )

        assert exit_code == 2
        assert "no locations are defined" in output
